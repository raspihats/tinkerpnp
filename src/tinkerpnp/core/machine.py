"""The machine runtime: builds objects from a validated configuration, publishes their status changes, and runs
machine control (enable, home, disable, stop, sequences) on one motion thread.

Threading: everything that talks to drivers runs on `worker` (one thread), one request after another, via `submit`.
`stop` and `cancel_jog` run on the caller's thread: they set `stop_event`, which every blocking driver call watches,
and make requests that were queued before the stop fail with `stopped`.
"""
from __future__ import annotations

import concurrent.futures
import logging
import threading
from typing import Any, Callable

from .config import MachineConfig
from .errors import MachineError, Stopped
from .motion import Motion
from .objects import (ActuatorBase, AxisBase, CameraBase, ControllerAxis, DriverBase, Head, MachineObject, MappedAxis,
                      Nozzle, Settings)
from .registry import Registry, registry as default_registry

log = logging.getLogger("tinkerpnp.machine")

Listener = Callable[[str, dict], None]

# build/setup order: an object's references are set up before it
BUILD_ORDER = ("driver", "axis", "head", "nozzle_tip", "actuator", "nozzle", "camera", "feeder")


class Singleton(MachineObject):
    """`machine`, `motion`, `job`: objects without an id."""

    def __init__(self, kind: str, machine: "Machine", **fields: Any):
        super().__init__("", kind, Settings(), machine)
        self.kind = kind
        self._status = dict(fields)

    @property
    def name(self) -> str:
        return self.kind


class Machine:
    def __init__(self, config: MachineConfig, reg: Registry | None = None):
        self.config = config
        self.registry = reg or default_registry
        self.objects: dict[str, MachineObject] = {}
        self._listeners: list[Listener] = []
        self._building = True

        first_nozzle = next((o.id for o in config.objects if o.kind == "nozzle"), None)
        self.state = Singleton("machine", self, state="disabled", enabled=False, homed=False, alarm=None,
                               message=None, selected_tool=f"nozzle/{first_nozzle}" if first_nozzle else None)
        self.motion = Singleton("motion", self, moving=False, last_move=None, safe_z=None, queue_length=0)
        self.job = Singleton("job", self, state="none", job_id=None, name=None,
                             progress={"done": 0, "total": 0}, current_placement=None, elapsed=0.0, errors=[])
        for s in (self.state, self.motion, self.job):
            self.objects[s.name] = s

        ordered = sorted(config.objects, key=lambda o: (BUILD_ORDER.index(o.kind),
                                                        o.kind == "axis" and o.type == "mapped"))
        for oc in ordered:
            info = self.registry.get(oc.kind, oc.type)
            obj = info.runtime(oc.id, oc.type, oc.settings, self)
            if oc.head is not None:
                obj.head = self.get("head", oc.head)
            self.objects[obj.name] = obj
            obj.setup()
        self.motion._status["safe_z"] = self._all_safe()
        self._building = False

        self.planner = Motion(self)
        self.worker = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="motion")
        self.stop_event = threading.Event()
        self._stop_generation = 0
        self._queued = 0
        self._lock = threading.Lock()

    def close(self) -> None:
        """Stop motion and the worker thread (the machine is being replaced or the server stops)."""
        self.stop("shutting down")
        self.worker.shutdown(wait=False, cancel_futures=True)

    # ── running requests on the motion thread ──────────────────────────────────────────────────────────────────
    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> concurrent.futures.Future:
        """Queue fn on the motion thread. If the machine is stopped before it runs, it fails with Stopped."""
        generation = self._stop_generation
        with self._lock:
            self._queued += 1
            self.motion.update(queue_length=self._queued)

        def run() -> Any:
            with self._lock:
                self._queued -= 1
                self.motion.update(queue_length=self._queued)
            if generation != self._stop_generation:
                raise Stopped("cancelled by machine.stop")
            self.stop_event.clear()
            return fn(*args, **kwargs)

        return self.worker.submit(run)

    def stop(self, reason: str | None = None) -> None:
        """Abort motion now and cancel queued requests. Safe to call from any thread."""
        self._stop_generation += 1
        self.stop_event.set()
        states = []
        for driver in self.of_kind("driver"):
            try:
                states.append(driver.stop())
            except Exception as exc:  # stopping must not fail half-way
                log.exception("stop failed on %s", driver.name)
                states.append("alarm")
                driver.update(last_error=str(exc))
        st = self.state.status()
        if st["enabled"]:
            if "alarm" in states:
                self._set_state("alarm", message=reason or "stopped", alarm="stopped")
            else:
                self._set_state("idle", message=reason or "stopped")
        self.motion.update(moving=False)

    def cancel_jog(self) -> None:
        self.stop_event.set()
        for driver in self.of_kind("driver"):
            driver.cancel()

    def _set_state(self, state: str, message: str | None = None, **fields: Any) -> None:
        self.state.update(state=state, message=message, **fields)

    def _update_homed(self) -> None:
        homeable = [a for a in self.of_kind("axis") if isinstance(a, ControllerAxis)]
        self.state.update(homed=bool(homeable) and all(a.homed for a in homeable))

    # ── machine control (run these on the motion thread, through submit) ───────────────────────────────────────
    def drivers(self) -> list[DriverBase]:
        return self.of_kind("driver")  # type: ignore[return-value]

    def enable(self) -> None:
        if self.state.status()["enabled"]:
            return
        self._set_state("enabling")
        try:
            for driver in self.drivers():
                driver.connect()
                driver.enable()
            self.state.update(enabled=True, alarm=None)
            self._set_state("idle")
            for act in self.of_kind("actuator"):
                if act.settings.on_enable is not None:
                    act.set(act.settings.on_enable)
            self.run_sequence(self.config.machine.on_enable, "machine.on_enable")
        except Exception as exc:
            message = exc.message if isinstance(exc, MachineError) else str(exc)
            try:
                self.disable()
            except Exception:
                log.exception("disable after a failed enable")
            self._set_state("disabled", message=f"enable failed: {message}")
            raise

    def disable(self) -> None:
        errors = []
        for act in self.of_kind("actuator"):
            if act.settings.on_disable is not None and self.state.status()["enabled"]:
                try:
                    act.set(act.settings.on_disable)
                except Exception as exc:
                    errors.append(f"{act.name}: {exc}")
        for driver in self.drivers():
            try:
                driver.disable()
            except Exception as exc:
                errors.append(f"{driver.name}: {exc}")
        # motors are released: positions can't be trusted any more
        for axis in self.of_kind("axis"):
            if isinstance(axis, ControllerAxis):
                axis.update(homed=False)
        self._refresh_dependents()
        self.state.update(enabled=False, homed=False, alarm=None)
        self._set_state("disabled", message="; ".join(errors) or None)

    def home(self, axes: list[str] | None = None) -> None:
        self.planner.require_ready_for_homing()
        controller = [a for a in self.of_kind("axis") if isinstance(a, ControllerAxis)]
        if axes is not None:
            if not isinstance(axes, list):
                raise MachineError("invalid_params", "axes must be a list of axis ids")
            known = {a.id: a for a in controller}
            missing = [a for a in axes if a not in known]
            if missing:
                raise MachineError("not_found", f"no controller axis {', '.join(missing)}", what="axis", id=missing[0])
            controller = [known[a] for a in axes]
        self._set_state("homing")
        try:
            by_driver: dict[DriverBase, list[ControllerAxis]] = {}
            for a in controller:
                if a.homeable:
                    by_driver.setdefault(a.driver, []).append(a)
            for driver, driver_axes in by_driver.items():
                driver.home(driver_axes)
            for a in controller:
                if not a.homeable:          # no switch: keeps its position, counts as homed
                    a.update(homed=True)
            self._refresh_dependents()
        finally:
            self._update_homed()
            if self.state.status()["state"] == "homing":
                self._set_state("idle")
        if self.state.status()["homed"]:
            self.run_sequence(self.config.machine.on_home, "machine.on_home")

    def actuate(self, actuator_id: str, value: Any) -> Any:
        act = self.objects.get(f"actuator/{actuator_id}")
        if not isinstance(act, ActuatorBase):
            raise MachineError("not_found", f"no actuator {actuator_id!r}", what="actuator", id=actuator_id)
        if not self.state.status()["enabled"]:
            raise MachineError("not_enabled", "the machine is disabled")
        return act.set(value)

    def run_sequence(self, steps: list, where: str) -> None:
        """Steps: home, park, safe_z, {actuate: {id: value, ...}}, {dwell: ms}."""
        heads = self.of_kind("head")
        for i, step in enumerate(steps):
            if step == "home":
                if where == "machine.on_home":
                    raise MachineError("invalid_params", f"{where}[{i}]: home inside on_home would loop")
                self.home()
            elif step in ("park", "safe_z"):
                park = self.config.machine.park
                for head in heads:
                    if step == "park" and park is not None and park.head == head.id:
                        self.planner.park(head, "all")
                    else:
                        self.planner.move_to_safe_z(head)
            elif isinstance(step, dict) and len(step) == 1 and "actuate" in step:
                for act_id, value in (step["actuate"] or {}).items():
                    self.actuate(act_id, value)
            elif isinstance(step, dict) and len(step) == 1 and "dwell" in step:
                if self.stop_event.wait(float(step["dwell"]) / 1000.0):
                    raise Stopped()
            else:
                raise MachineError("invalid_params", f"{where}[{i}]: unknown step {step!r}")

    # ── lookup ─────────────────────────────────────────────────────────────────────────────────────────────────
    def get(self, kind: str, obj_id: str) -> Any:
        return self.objects[f"{kind}/{obj_id}"]

    def names(self, kind: str | None = None) -> list[str]:
        if kind is None:
            return list(self.objects)
        return [n for n, o in self.objects.items() if o.kind == kind]

    def of_kind(self, kind: str) -> list[MachineObject]:
        return [o for o in self.objects.values() if o.kind == kind]

    # ── status ─────────────────────────────────────────────────────────────────────────────────────────────────
    def query(self, request: dict[str, list[str] | None]) -> dict[str, dict]:
        out = {}
        for name, fields in request.items():
            obj = self.objects.get(name)
            if obj is None:
                continue
            status = obj.status()
            out[name] = status if fields is None else {f: status[f] for f in fields if f in status}
        return out

    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Listener) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def publish(self, name: str, delta: dict) -> None:
        if self._building:
            return
        for fn in list(self._listeners):
            fn(name, delta)

    # ── simulation hooks (phase 0) ─────────────────────────────────────────────────────────────────────────────
    def set_axis_position(self, axis_id: str, position: float, homed: bool | None = None) -> None:
        """Set a raw axis position (simulated driver / tests) and refresh everything that depends on it."""
        self.set_axis_positions({axis_id: position}, homed)

    def set_axis_positions(self, positions: dict[str, float], homed: bool | None = None) -> None:
        """Drivers report raw axis positions here; dependent objects (mapped axes, nozzles, ...) follow."""
        for axis_id, position in positions.items():
            fields: dict[str, Any] = {"position": round(position, 6)}
            if homed is not None:
                fields["homed"] = homed
            self.get("axis", axis_id).update(**fields)
        self._refresh_dependents()

    def _refresh_dependents(self) -> None:
        for obj in self.objects.values():
            if isinstance(obj, MappedAxis):
                obj.refresh()
        for obj in self.objects.values():
            if isinstance(obj, (Head, Nozzle)) or (isinstance(obj, CameraBase) and obj.head is not None):
                obj.refresh()
        self.motion.update(safe_z=self._all_safe())

    def _all_safe(self) -> bool | None:
        values = [n.in_safe_zone() for n in self.of_kind("nozzle")]
        values = [v for v in values if v is not None]
        return all(values) if values else None

    def axes(self) -> list[AxisBase]:
        return [o for o in self.of_kind("axis")]
