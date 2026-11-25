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


class MemoryState:
    """Fallback for Machine.state_store (tests, no server): state kept in memory only."""

    def __init__(self) -> None:
        self._d: dict[str, Any] = {}

    def state_get(self, key: str) -> Any:
        return self._d.get(key)

    def state_set(self, key: str, value: Any) -> None:
        self._d[key] = value


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
        # where small state that changes often is kept (feeder counters): the server sets its data store here
        self.state_store: Any = MemoryState()
        self._gcode_listeners: list[Callable[[str, str, str], None]] = []
        self._event_listeners: list[Callable[[str, dict], None]] = []
        self._building = True

        first_nozzle = next((o.id for o in config.objects if o.kind == "nozzle"), None)
        self.state = Singleton("machine", self, state="disabled", enabled=False, homed=False, alarm=None,
                               message=None, selected_tool=f"nozzle/{first_nozzle}" if first_nozzle else None)
        self.motion = Singleton("motion", self, moving=False, last_move=None, safe_z=None, queue_length=0)
        self.job = Singleton("job", self, state="none", job_id=None, name=None,
                             progress={"done": 0, "total": 0}, current_placement=None, elapsed=0.0, message=None)
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
        """Close driver links, cameras and the worker thread (machine replaced or server stopping). Motion is stopped
        only if something is moving: the stop sequence (e.g. grbl's soft reset) shouldn't hit an idle machine."""
        if self.motion.status().get("moving") or self._queued:
            self.stop("shutting down")
        else:
            self._stop_generation += 1
            self.stop_event.set()            # end any wait
        self.worker.shutdown(wait=False, cancel_futures=True)
        for camera in self.of_kind("camera"):
            try:
                camera.close()
            except Exception:
                log.exception("closing %s", camera.name)
        for driver in self.drivers():
            disconnect = getattr(driver, "disconnect", None)
            if disconnect is not None:
                try:
                    disconnect()
                except Exception:
                    log.exception("closing %s", driver.name)

    # ── driver callbacks (any thread) ──────────────────────────────────────────────────────────────────────────
    def add_gcode_listener(self, fn: Callable[[str, str, str], None]) -> None:
        self._gcode_listeners.append(fn)

    def remove_gcode_listener(self, fn: Callable[[str, str, str], None]) -> None:
        if fn in self._gcode_listeners:
            self._gcode_listeners.remove(fn)

    def add_event_listener(self, fn: Callable[[str, dict], None]) -> None:
        self._event_listeners.append(fn)

    def emit(self, method: str, params: dict) -> None:
        """A notification for all clients (e.g. notify_job_event). Any thread."""
        for fn in list(self._event_listeners):
            fn(method, params)

    def log_gcode(self, driver_id: str, direction: str, line: str) -> None:
        """Every line a driver sends ("tx") or receives ("rx"), for console views."""
        log.debug("%s %s %r", driver_id, direction, line)
        for fn in list(self._gcode_listeners):
            fn(driver_id, direction, line)

    def driver_alarm(self, driver: Any, alarm: str, line: str) -> None:
        """A controller reported an alarm: its position (and its outputs) can't be trusted any more."""
        forget = getattr(driver, "forget_outputs", None)
        if forget is not None:
            forget()
        for axis in self.of_kind("axis"):
            if isinstance(axis, ControllerAxis) and axis.driver is driver:
                axis.update(homed=False)
        self._refresh_dependents()
        self._update_homed()
        if self.state.status()["enabled"]:
            self._set_state("alarm", message=f"{driver.id}: {line}", alarm=alarm)

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
        self._update_homed()
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
            self.run_sequence(self.config.machine.on_enable, "machine.on_enable")
            # after the sequence: controllers like grbl refuse G-code (M5, M65, ...) until they're homed
            for act in self.of_kind("actuator"):
                if act.settings.on_enable is not None:
                    act.set(act.settings.on_enable)
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
        # before homing, while the position is still known (e.g. [safe_z]: the homing cycle may move X/Y first,
        # with a nozzle down). Skipped when not homed: the position is unknown and grbl refuses moves anyway.
        if self.config.machine.on_before_home and self.state.status()["homed"]:
            self.run_sequence(self.config.machine.on_before_home, "machine.on_before_home")
        self._set_state("homing", alarm=None)
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

    def clear_alarm(self) -> None:
        for driver in self.drivers():
            clear = getattr(driver, "clear_alarm", None)
            if clear is not None and driver.status().get("connected"):
                clear()
        st = self.state.status()
        if st["state"] == "alarm":
            self._set_state("idle" if st["enabled"] else "disabled", alarm=None)

    def actuate(self, actuator_id: str, value: Any) -> Any:
        act = self.objects.get(f"actuator/{actuator_id}")
        if not isinstance(act, ActuatorBase):
            raise MachineError("not_found", f"no actuator {actuator_id!r}", what="actuator", id=actuator_id)
        if not self.state.status()["enabled"]:
            raise MachineError("not_enabled", "the machine is disabled")
        return act.set(value)

    def run_macro(self, name: str) -> None:
        macro = self.config.machine.macros.get(name)
        if macro is None:
            raise MachineError("not_found", f"no macro {name!r}", what="macro", id=name)
        self.run_sequence(macro, f"machine.macros.{name}")

    def run_sequence(self, steps: list, where: str) -> None:
        """Steps: home, park, safe_z, {actuate: {id: value, ...}}, {dwell: ms}, and in macros
        {move_to: {object, location, speed?, safe_z?}} and {gcode: text | {driver, command}}."""
        heads = self.of_kind("head")
        for i, step in enumerate(steps):
            if step == "home":
                if where in ("machine.on_home", "machine.on_before_home"):
                    raise MachineError("invalid_params", f"{where}[{i}]: home inside {where[8:]} would loop")
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
            elif isinstance(step, dict) and len(step) == 1 and "move_to" in step:
                m = step["move_to"]
                self.planner.move_to(self.planner.resolve(m["object"]), m["location"], m.get("speed", 1.0),
                                     m.get("safe_z", "auto"))
            elif isinstance(step, dict) and len(step) == 1 and "gcode" in step:
                g = step["gcode"]
                command, driver_id = (g, None) if isinstance(g, str) else (g.get("command", ""), g.get("driver"))
                drivers = [d for d in self.drivers() if hasattr(d, "send")]
                drv = next((d for d in drivers if d.id == driver_id), None) if driver_id else \
                    (drivers[0] if len(drivers) == 1 else None)
                if drv is None:
                    raise MachineError("invalid_params", f"{where}[{i}]: say which driver (gcode: {{driver, command}})")
                for line in str(command).split("\n"):
                    if line.strip():
                        drv.send(line.strip())
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

    def apply_live(self, config: MachineConfig) -> None:
        """Take calibration changes (config_edit.LIVE_PATHS) into the running machine without rebuilding it."""
        self.config = config
        for oc in config.objects:
            obj = self.objects.get(f"{oc.kind}/{oc.id}")
            if obj is not None and obj.type == oc.type:
                old = obj.settings
                obj.settings = oc.settings
                # camera device controls: set them now if the device is open (otherwise on the next open)
                new_controls = getattr(oc.settings, "controls", None)
                if (new_controls is not None and new_controls != getattr(old, "controls", None)
                        and hasattr(obj, "apply_controls") and obj.status().get("device") and obj._running):
                    problems = obj.apply_controls(new_controls)
                    obj.update(last_error="; ".join(problems) or None)
        for cam in self.of_kind("camera"):
            s = cam.settings
            cam.update(location=cam.compute_location(),
                       units_per_pixel=s.units_per_pixel.model_dump() if s.units_per_pixel else None)
        for feeder in self.of_kind("feeder"):
            feeder.refresh_status()
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
