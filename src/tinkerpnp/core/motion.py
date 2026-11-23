"""Motion planning (design.md, 3.3): one path for every move.

`move_to(object, location)` resolves the object's offsets and axis maps to raw axis targets, checks that the machine
is enabled and homed and that every target is inside its soft limits, raises all nozzles of the head to safe Z before
X/Y travel, and hands the raw targets to the drivers. Jogging and parking use it too.

Order of a move with X/Y travel and `safe_z: auto`:
  1. every nozzle on the head into its safe zone (raw axes, so a shared seesaw Z moves once)
  2. X, Y and rotation
  3. the object's own Z, if the location has one
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import MachineError
from .objects import AxisBase, CameraBase, ControllerAxis, HeadMountable, MappedAxis, Nozzle, VirtualAxis

if TYPE_CHECKING:
    from .machine import Machine

COORDS = ("x", "y", "z", "rotation")


def raw(axis: AxisBase, value: float) -> tuple[AxisBase, float]:
    """Follow mapped axes down to the axis that really moves."""
    while isinstance(axis, MappedAxis):
        value = axis.inverse(value)
        axis = axis.input
    return axis, value


class Motion:
    def __init__(self, machine: "Machine"):
        self.m = machine

    # ── objects and their axes ─────────────────────────────────────────────────────────────────────────────────
    def resolve(self, name: str) -> Any:
        obj = self.m.objects.get(name)
        if obj is None:
            raise MachineError("not_found", f"no object {name!r}", what="object", id=name)
        if isinstance(obj, AxisBase):
            return obj
        if isinstance(obj, HeadMountable) or (isinstance(obj, CameraBase) and obj.head is not None):
            return obj
        raise MachineError("invalid_params", f"{name} can't be moved (use a nozzle, a head camera or axis/<id>)")

    def axes_of(self, obj: Any) -> dict[str, AxisBase]:
        """coordinate -> axis for a movable object."""
        if isinstance(obj, AxisBase):
            return {"position": obj}
        out = {"x": obj.head.x, "y": obj.head.y}
        if isinstance(obj, Nozzle):
            out.update(z=obj.z, rotation=obj.rotation)
        elif obj.settings.axes is not None:
            out.update(z=self.m.get("axis", obj.settings.axes.z),
                       rotation=self.m.get("axis", obj.settings.axes.rotation))
        return out

    def location(self, obj: Any) -> dict:
        if isinstance(obj, AxisBase):
            return {"position": obj.position}
        loc = dict(obj.compute_location() or {})
        for coord, axis in self.axes_of(obj).items():
            if coord in ("z", "rotation"):
                loc[coord] = axis.position
        return loc

    def targets(self, obj: Any, location: dict) -> dict[AxisBase, float]:
        """Raw axis targets that put the object at `location` (missing coordinates keep their value)."""
        if not isinstance(location, dict) or not location:
            raise MachineError("invalid_params", "location must be an object with x, y, z and/or rotation")
        axes = self.axes_of(obj)
        unknown = set(location) - set(axes)
        if unknown:
            raise MachineError("invalid_params", f"{obj.name} has no {', '.join(sorted(unknown))} axis")
        offset = getattr(obj.settings, "offset", None) if not isinstance(obj, AxisBase) else None
        out: dict[AxisBase, float] = {}
        for coord, value in location.items():
            if value is None:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise MachineError("invalid_params", f"{coord} must be a number")
            value = float(value)
            if coord in ("x", "y") and offset is not None:
                value -= getattr(offset, coord) or 0.0
            axis, value = raw(axes[coord], value)
            if axis in out and abs(out[axis] - value) > axis.tolerance:
                raise MachineError("invalid_params", f"{obj.name}: z and another coordinate drive axis/{axis.id}"
                                                     " to different positions")
            out[axis] = value
        return out

    # ── checks ─────────────────────────────────────────────────────────────────────────────────────────────────
    def problems(self, targets: dict[AxisBase, float]) -> list[dict]:
        out = []
        for axis, value in targets.items():
            limits = axis.limits() if isinstance(axis, ControllerAxis) else None
            if limits and not (limits[0] - axis.tolerance <= value <= limits[1] + axis.tolerance):
                out.append({"name": "soft_limit", "axis": axis.id, "value": round(value, 4), "limit": list(limits),
                            "message": f"axis/{axis.id} to {value:.3f} is outside its limits {list(limits)}"})
        return out

    def require_ready(self) -> None:
        st = self.m.state.status()
        if st["state"] == "alarm":
            raise MachineError("alarm", f"machine is in alarm: {st.get('alarm') or st.get('message')}",
                               alarm=st.get("alarm"))
        if not st["enabled"]:
            raise MachineError("not_enabled", "the machine is disabled")
        if not st["homed"]:
            raise MachineError("not_homed", "home the machine first")

    def require_ready_for_homing(self) -> None:
        st = self.m.state.status()
        if not st["enabled"]:
            raise MachineError("not_enabled", "the machine is disabled")

    def check(self, targets: dict[AxisBase, float]) -> None:
        self.require_ready()
        problems = self.problems(targets)
        if problems:
            p = problems[0]
            raise MachineError("soft_limit", p["message"], axis=p["axis"], value=p["value"], limit=p["limit"])

    # ── moving ─────────────────────────────────────────────────────────────────────────────────────────────────
    def execute(self, targets: dict[AxisBase, float], speed: float = 1.0) -> None:
        """Move raw axes (already checked). Axes of one driver move together; virtual axes just take the value."""
        moving = {a: v for a, v in targets.items() if abs(a.position - v) > a.tolerance}
        if not moving:
            return
        virtual = {a.id: v for a, v in moving.items() if isinstance(a, VirtualAxis)}
        if virtual:
            self.m.set_axis_positions(virtual)
        by_driver: dict[Any, dict[ControllerAxis, float]] = {}
        for a, v in moving.items():
            if isinstance(a, ControllerAxis):
                by_driver.setdefault(a.driver, {})[a] = v
        for driver, axis_targets in by_driver.items():
            self.m.motion.update(moving=True)
            try:
                driver.move(axis_targets, speed)
            finally:
                self.m.motion.update(moving=False)

    def safe_z_targets(self, head: Any) -> dict[AxisBase, float]:
        """Raw targets that bring every nozzle on the head into its safe zone."""
        out: dict[AxisBase, float] = {}
        for nozzle in self.m.of_kind("nozzle"):
            if nozzle.head is not head:
                continue
            axis, _ = raw(nozzle.z, nozzle.z.position)
            if isinstance(axis, ControllerAxis):
                value = axis.safe_value()
                if value is not None:
                    out[axis] = value
        return out

    def move_to(self, obj: Any, location: dict, speed: float = 1.0, safe_z: str = "auto") -> dict:
        if not 0 < speed <= 1:
            raise MachineError("invalid_params", "speed is a fraction of the axis feed: 0 < speed <= 1")
        if safe_z not in ("auto", "none"):
            raise MachineError("invalid_params", "safe_z must be auto or none")
        targets = self.targets(obj, location)
        self.check(targets)

        head = getattr(obj, "head", None)
        xy = {a: v for a, v in targets.items() if head is not None and a in (head.x, head.y)}
        travels = any(abs(a.position - v) > a.tolerance for a, v in xy.items())
        if safe_z == "auto" and travels:
            lift = self.safe_z_targets(head)
            self.check(lift)
            self.execute(lift, speed)
            rest = {a: v for a, v in targets.items() if a not in xy}
            z_axes = {raw(n.z, 0.0)[0] for n in self.m.of_kind("nozzle") if n.head is head}
            first = {**xy, **{a: v for a, v in rest.items() if a not in z_axes}}
            self.execute(first, speed)
            self.execute({a: v for a, v in rest.items() if a in z_axes}, speed)
        else:
            self.execute(targets, speed)
        self.m.motion.update(last_move={"object": obj.name, "location": self.location(obj)})
        return self.location(obj)

    def jog(self, obj: Any, delta: dict, speed: float = 1.0) -> dict:
        if not isinstance(delta, dict) or not delta:
            raise MachineError("invalid_params", "delta must be an object with x, y, z and/or rotation")
        current = self.location(obj)
        location = {}
        for coord, d in delta.items():
            if coord not in current:
                raise MachineError("invalid_params", f"{obj.name} has no {coord} axis")
            if not isinstance(d, (int, float)) or isinstance(d, bool):
                raise MachineError("invalid_params", f"{coord} must be a number")
            location[coord] = current[coord] + d
        safe_z = "auto" if self.m.config.machine.jog.safe_z == "always" else "none"
        return self.move_to(obj, location, speed, safe_z)

    def move_to_safe_z(self, head: Any) -> None:
        lift = self.safe_z_targets(head)
        self.check(lift)
        self.execute(lift)

    def park(self, head: Any, axes: str = "all") -> dict:
        if axes not in ("xy", "z", "all"):
            raise MachineError("invalid_params", "axes must be xy, z or all")
        self.move_to_safe_z(head)
        if axes == "z":
            return head.compute_location()
        park = self.m.config.machine.park
        if park is None or park.head != head.id:
            raise MachineError("invalid_params", f"no park location for head {head.id} (machine.park)")
        # head X/Y in machine coordinates (not "where the camera goes" as in OpenPnP: that pushed the head past its
        # soft limits on the first machine)
        loc = park.location
        targets = {head.x: head.x.position if loc.x is None else loc.x,
                   head.y: head.y.position if loc.y is None else loc.y}
        self.check(targets)
        self.execute(targets)
        self.m.motion.update(last_move={"object": head.name, "location": head.compute_location()})
        return head.compute_location()
