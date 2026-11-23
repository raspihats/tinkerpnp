"""The machine runtime: builds objects from a validated configuration and publishes their status changes."""
from __future__ import annotations

from typing import Any, Callable

from .config import MachineConfig
from .objects import AxisBase, CameraBase, Head, MachineObject, MappedAxis, Nozzle, Settings
from .registry import Registry, registry as default_registry

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
        axis = self.get("axis", axis_id)
        fields: dict[str, Any] = {"position": position}
        if homed is not None:
            fields["homed"] = homed
        axis.update(**fields)
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
