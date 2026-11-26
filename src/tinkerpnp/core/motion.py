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

import logging
import math
from typing import TYPE_CHECKING, Any

from .errors import MachineError
from .objects import AxisBase, CameraBase, ControllerAxis, HeadMountable, MappedAxis, Nozzle, VirtualAxis

if TYPE_CHECKING:
    from .machine import Machine

log = logging.getLogger(__name__)

COORDS = ("x", "y", "z", "rotation")


def raw(axis: AxisBase, value: float) -> tuple[AxisBase, float]:
    """Follow mapped axes down to the axis that really moves."""
    while isinstance(axis, MappedAxis):
        value = axis.inverse(value)
        axis = axis.input
    return axis, value


def normalize_rotation(axis: AxisBase, value: float) -> float:
    """As OpenPnP's "limit rotation": a rotation axis with limit_rotation (e.g. [-180, 180]) takes the angle by whole
    turns into that range: 200 -> -160; also into a narrower range when a whole-turn equivalent fits ([-90, 90]:
    270 -> -90). If none fits, the nearest is kept and the limit check refuses it."""
    if not isinstance(axis, ControllerAxis) or axis.axis_kind != "rotation" or not axis.settings.limit_rotation:
        return value
    low, high = axis.settings.limit_rotation
    k0 = math.floor((value - low) / 360.0)
    candidates = [value - 360.0 * k for k in (k0 - 1, k0, k0 + 1)]
    inside = [c for c in candidates if low - 1e-9 <= c <= high + 1e-9]
    if inside:
        return min(inside, key=lambda c: abs(c - value))
    return min(candidates, key=lambda c: max(low - c, c - high))


class Motion:
    def __init__(self, machine: "Machine"):
        self.m = machine
        self._last_user_action: tuple[str, dict] | None = None    # unsafe Z roaming: (object, where it went below)

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
        offset = getattr(obj.settings, "offset", None)
        for coord, axis in self.axes_of(obj).items():
            if coord in ("z", "rotation"):
                loc[coord] = axis.position
        if "z" in loc and offset is not None and offset.z:
            loc["z"] += offset.z
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
        # nozzle tip runout: put the tip (not the axis) where asked, at the rotation it will have
        run_dx = run_dy = 0.0
        if isinstance(obj, Nozzle) and ("x" in location or "y" in location):
            rot = location.get("rotation")
            run_dx, run_dy = obj.runout_offset(obj.rotation.position if rot is None else float(rot))
        out: dict[AxisBase, float] = {}
        for coord, value in location.items():
            if value is None:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise MachineError("invalid_params", f"{coord} must be a number")
            value = float(value)
            if coord in ("x", "y", "z") and offset is not None:     # as OpenPnP: X, Y and Z (not rotation)
                value -= getattr(offset, coord) or 0.0
            if coord == "x":
                value -= run_dx
            elif coord == "y":
                value -= run_dy
            axis, value = raw(axes[coord], value)
            value = normalize_rotation(axis, value)
            if (isinstance(axis, ControllerAxis) and axis.axis_kind == "rotation" and axis.settings.wrap_around
                    and not axis.settings.limit_rotation):        # as OpenPnP's wrap around: the shortest way
                value = axis.position + ((value - axis.position + 180.0) % 360.0 - 180.0)
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
    def execute(self, targets: dict[AxisBase, float], speed: float = 1.0, backlash: bool = True) -> None:
        """Move raw axes (already checked). Axes of one driver move together; virtual axes just take the value."""
        moving = {a: v for a, v in targets.items() if abs(a.position - v) > a.tolerance}
        if not moving:
            return
        if backlash:
            pre, factor = self._backlash(moving)
            if pre:                              # as OpenPnP: past the target first, then approach from one side
                self.execute({**moving, **pre}, speed, backlash=False)
                self.execute(moving, speed * factor, backlash=False)
                return
        virtual = {a.id: v for a, v in moving.items() if isinstance(a, VirtualAxis)}
        if virtual:
            self.m.set_axis_positions(virtual)
        by_driver: dict[Any, dict[ControllerAxis, float]] = {}
        for a, v in moving.items():
            if isinstance(a, ControllerAxis):
                by_driver.setdefault(a.driver, {})[a] = v
        speed = self.effective_speed(speed, moving)
        self.m.motion.update(moving=True)
        try:
            if len(by_driver) <= 1:
                for driver, axis_targets in by_driver.items():
                    driver.move(axis_targets, speed)
            else:
                # as OpenPnP: every controller gets its part of the move, then all are waited for (each has its own
                # link, so they run side by side)
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(by_driver)) as pool:
                    futures = [pool.submit(d.move, t, speed) for d, t in by_driver.items()]
                    errors = [f.exception() for f in futures]
                first = next((e for e in errors if e is not None), None)
                if first is not None:
                    raise first
        finally:
            self.m.motion.update(moving=False)

    def _backlash(self, moving: dict) -> tuple[dict, float]:
        """Overshoot targets for axes with backlash compensation (one_sided: always; one_sided_optimized: only when
        the move would arrive from the other side), and the speed factor of the final approach."""
        pre: dict = {}
        factor = 1.0
        for a, v in moving.items():
            b = getattr(a.settings, "backlash", None) if isinstance(a, ControllerAxis) else None
            if b is None or b.method == "none" or not b.offset:
                continue
            arriving_from = math.copysign(1.0, a.position - v)          # the side the move comes from
            if b.method == "one_sided_optimized" and arriving_from == math.copysign(1.0, b.offset):
                continue
            target = v + b.offset
            lim = a.limits()
            if lim:
                target = min(max(target, lim[0]), lim[1])
            pre[a] = target
            factor = min(factor, b.speed_factor)
        return pre, factor

    def effective_speed(self, speed: float, moving: dict) -> float:
        """As OpenPnP: the request's speed × the global speed × the slowest part speed of any nozzle carrying a part
        on a head that moves; never below 5 %."""
        slowest = 1.0
        for head in self.m.of_kind("head"):
            nozzles = [n for n in self.m.of_kind("nozzle") if n.head is head]
            axes = {head.x, head.y} | {raw(n.z, 0.0)[0] for n in nozzles} | {raw(n.rotation, 0.0)[0] for n in nozzles}
            if not axes & set(moving):
                continue
            for n in nozzles:
                st = n.status()
                if st.get("part") and st.get("part_speed"):
                    slowest = min(slowest, float(st["part_speed"]))
        factor = speed * float(self.m.motion.status().get("speed") or 1.0) * slowest
        return max(0.05, min(1.0, factor))

    def safe_z_targets(self, head: Any) -> dict[AxisBase, float]:
        """Raw targets that bring everything on the head to safe Z, as OpenPnP's head "move to safe Z": every nozzle
        into its safe zone, and head cameras' virtual Z back to its home. A nozzle whose Z has no safe zone is an
        error, as in OpenPnP (it would otherwise travel at whatever depth it is)."""
        out: dict[AxisBase, float] = {}
        raised: set = set()                     # axes set for a nozzle carrying a part (dynamic safe Z): they win
        for nozzle in self.m.of_kind("nozzle"):
            if nozzle.head is not head:
                continue
            axis, _ = raw(nozzle.z, nozzle.z.position)
            if isinstance(axis, ControllerAxis):
                if not axis.settings.safe_zone:
                    raise MachineError("invalid_params", f"{nozzle.name}: its Z (axis/{axis.id}) has no safe_zone, "
                                                         "so it can't be raised to safe Z before moving",
                                       what="safe_zone", id=axis.id)
                value = axis.safe_value()
                st = nozzle.status()
                if nozzle.settings.dynamic_safe_z and st.get("part") and st.get("part_height"):
                    # as OpenPnP's dynamic safe Z: the part's underside at least at the safe zone's low end (in the
                    # nozzle's coordinates), within the safe zone
                    low, high = axis.settings.safe_zone
                    n_low = min(self._coord(nozzle.z, {axis: low}), self._coord(nozzle.z, {axis: high}))
                    want_n = n_low + float(st["part_height"])
                    if self._coord(nozzle.z, {}) < want_n - axis.tolerance:
                        _, want = raw(nozzle.z, want_n)
                        value = min(max(want, low), high)
                        out[axis] = value
                        raised.add(axis)
                        continue
                if value is not None and axis not in raised:
                    out[axis] = value
        for camera in self.m.of_kind("camera"):
            if camera.head is not head or camera.settings.axes is None:
                continue
            z = self.m.get("axis", camera.settings.axes.z)
            if isinstance(z, VirtualAxis) and abs(z.position - z.settings.home) > z.tolerance:
                out[z] = z.settings.home
        return out

    def move_to(self, obj: Any, location: dict, speed: float = 1.0, safe_z: str = "auto") -> dict:
        if not 0 < speed <= 1:
            raise MachineError("invalid_params", "speed is a fraction of the axis feed: 0 < speed <= 1")
        if safe_z not in ("auto", "none"):
            raise MachineError("invalid_params", "safe_z must be auto or none")
        targets = self.targets(obj, location)
        self.check(targets)
        before = self.location(obj)

        head = getattr(obj, "head", None)
        xy = {a: v for a, v in targets.items() if head is not None and a in (head.x, head.y)}
        # as OpenPnP's moveToLocationAtSafeZ: X, Y or rotation changing means travel (rotating a part at depth can
        # smear it)
        rot = self.axes_of(obj).get("rotation") if head is not None else None
        rot_raw = raw(rot, 0.0)[0] if rot is not None else None
        turns = rot_raw is not None and rot_raw in targets and abs(rot_raw.position - targets[rot_raw]) > rot_raw.tolerance
        travels = turns or any(abs(a.position - v) > a.tolerance for a, v in xy.items())
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
        requested = {**{k: v for k, v in before.items()}, **{k: float(v) for k, v in location.items() if v is not None}}
        self.m.motion.update(last_move={"object": obj.name, "requested": requested, "location": self.location(obj)})
        return self.location(obj)

    def jog(self, obj: Any, delta: dict, speed: float = 1.0, safe_z: str | None = None,
            min_step: bool = False, boards: list[dict] | None = None) -> dict:
        """Move relative to the current location. safe_z: auto (lift before X/Y travel), none (stay at this Z, e.g.
        to centre a nozzle over a camera), or None = machine.jog.safe_z from the configuration.
        A non-zero step smaller than the axis `resolution` fails with `below_resolution` (data: coord, delta,
        min_step); with min_step=True such a step is made one resolution step instead.
        boards: board protection (OpenPnP's): [{id, location {x, y, z, rotation}, dimensions {x, y}}] of the job; a
        jog that would bring a nozzle below safe Z over one of them fails with `board_protection`."""
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
        for coord, d in delta.items():
            small = self._below_resolution(obj, coord, float(d))
            if small is None:
                continue
            axis, step = small
            if not min_step:
                raise MachineError("below_resolution",
                                   f"{coord} step {abs(d):g} is smaller than the minimum step of axis/{axis.id} "
                                   f"({step:.4g})", coord=coord, delta=d, min_step=round(step, 6), axis=axis.id)
            location[coord] = current[coord] + math.copysign(step, d)
        if safe_z is None:
            jog = self.m.config.machine.jog
            travel = math.hypot(float(delta.get("x", 0.0) or 0.0), float(delta.get("y", 0.0) or 0.0))
            lift = jog.safe_z == "always" or (jog.safe_z == "long" and travel > jog.lift_above)
            safe_z = "auto" if lift else "none"
        if boards:
            self.check_board_protection(obj, self.targets(obj, location), boards)
        return self.move_to(obj, location, speed, safe_z)

    # ── OpenPnP's jog safety (MachineControlsPanel.checkJogMotionSafety / machineTargetedUserAction) ─────────────
    def _coord(self, axis: AxisBase, raw_values: dict) -> float:
        """An axis' coordinate if the raw axes were at raw_values (others where they are)."""
        if isinstance(axis, MappedAxis):
            return axis.forward(self._coord(axis.input, raw_values))
        return raw_values.get(axis, axis.position)

    @staticmethod
    def _in_safe_zone(axis: AxisBase, raw_value: float) -> bool:
        """As OpenPnP: a controller axis without a safe zone counts as safe; a virtual axis is safe at or above its
        home coordinate."""
        if isinstance(axis, VirtualAxis):
            return raw_value >= float(axis.settings.home) - 1e-6
        zone = axis.settings.safe_zone if isinstance(axis, ControllerAxis) else None
        if not zone:
            return True
        tol = axis.tolerance
        return zone[0] - tol <= raw_value <= zone[1] + tol

    def check_board_protection(self, obj: Any, targets: dict[AxisBase, float], boards: list[dict]) -> None:
        """Refuse a jog that would put a nozzle of the head below its safe Z within its safe distance of a board:
        1 mm + half the tip diameter (half the max part diameter when it holds a part), Z lowered by the part's
        height; above the board's surface is fine."""
        head = getattr(obj, "head", None)
        if head is None:
            return
        for n in self.m.of_kind("nozzle"):
            if n.head is not head:
                continue
            # as OpenPnP: the part's underside counts, so a nozzle with a part just inside the safe zone is checked
            held = float(n.status().get("part_height") or 0.0) if n.status().get("part") else 0.0
            z_raw_axis, z_low_raw = raw(n.z, self._coord(n.z, targets) - held)
            if self._in_safe_zone(z_raw_axis, z_low_raw):
                continue
            off = n.settings.offset
            x = self._coord(head.x, targets) + (off.x or 0.0)
            y = self._coord(head.y, targets) + (off.y or 0.0)
            z = self._coord(n.z, targets) + (n.settings.offset.z or 0.0)
            st = n.status()
            tip = self.m.objects.get(f"nozzle_tip/{n.tip_id()}") if n.tip_id() else None
            safe = 1.0
            subject = f"nozzle {n.id}"
            if tip is not None:
                diameter = float(tip.settings.diameter or 0.0)
                max_part = float(tip.settings.max_part_diameter or 0.0)
                safe += (max_part if st.get("part") and max_part > diameter else diameter) * 0.5
                subject += f" with {tip.id}"
            if st.get("part"):
                z -= float(st.get("part_height") or 0.0)
                subject += f" holding {st['part']}"
            for b in boards:
                loc, dim = b.get("location") or {}, b.get("dimensions") or {}
                a = math.radians(-(loc.get("rotation") or 0.0))
                dx, dy = x - (loc.get("x") or 0.0), y - (loc.get("y") or 0.0)
                lx, ly = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
                lz = z - (loc.get("z") or 0.0)
                outside = (lx <= -safe or ly <= -safe or lx >= (dim.get("x") or 0.0) + safe
                           or ly >= (dim.get("y") or 0.0) + safe or lz > 0)
                if not outside:
                    raise MachineError("board_protection",
                                       f"{subject} would potentially crash into board {b.get('id')}. To jog there "
                                       "anyway, turn off board protection", nozzle=n.id, board=b.get("id"))

    def center_on(self, camera: Any, point: dict, nozzle: Any | None = None) -> dict:
        """As OpenPnP's Shift+click in a camera view: bring what is seen at `point` (machine X/Y, from the picture) to
        the centre. A head camera moves there. On a machine camera the nozzle moves by (location - point): within
        the camera's roaming radius at its depth, otherwise at safe Z."""
        if camera.head is not None:
            return self.move_to(camera, {"x": point["x"], "y": point["y"]})
        if nozzle is None:
            raise MachineError("invalid_params", "a machine camera moves a nozzle: give the nozzle")
        loc = camera.settings.location
        if loc is None or loc.x is None or loc.y is None:
            raise MachineError("invalid_params", f"{camera.name} has no location")
        here = self.location(nozzle)
        target = {"x": here["x"] + loc.x - point["x"], "y": here["y"] + loc.y - point["y"]}
        r = camera.settings.roaming_radius
        near = all(math.hypot(p["x"] - loc.x, p["y"] - loc.y) <= r + 1e-9 for p in (here, target))
        return self.move_to(nozzle, target, safe_z="none" if r > 0 and near else "auto")

    def user_action(self, obj: Any, jogging: bool) -> None:
        """After a move the user asked for (OpenPnP's machineTargetedUserAction): unsafe Z roaming. A tool with a
        virtual Z (a head camera) jogged below its safe Z farther than machine.unsafe_z_roaming_distance from
        where it went below goes back to safe Z (its virtual Z is reset)."""
        if getattr(obj, "head", None) is None or isinstance(obj, AxisBase):
            return
        if self.m.config.machine.auto_tool_select:      # as OpenPnP: the tool the user moved is the selected one
            self.m.state.update(selected_tool=obj.name)
        light = getattr(obj.settings, "light", None) if isinstance(obj, CameraBase) else None
        if light and obj.settings.light_on_user_action and self.m.get("actuator", light).status().get("value") is not True:
            self.m.actuate(light, True)          # as OpenPnP's user action light on
        z_axis = self.axes_of(obj).get("z")
        if z_axis is None:
            return
        base, _ = raw(z_axis, 0.0)
        here = self.location(obj)
        selected = self.m.state.status().get("selected_tool")
        if self._in_safe_zone(base, base.position):
            self._last_user_action = None
        elif self._last_user_action is None or selected != obj.name or not jogging:
            self._last_user_action = (obj.name, here)
        elif isinstance(base, VirtualAxis):
            last = self._last_user_action[1]
            # as OpenPnP (Location.getLinearDistanceTo): X/Y only
            dist = math.hypot(here.get("x", 0.0) - last.get("x", 0.0), here.get("y", 0.0) - last.get("y", 0.0))
            if dist > self.m.config.machine.unsafe_z_roaming_distance:
                log.debug("%s roamed %.2f mm below safe Z: back to safe Z", obj.name, dist)
                self.m.set_axis_positions({base.id: float(base.settings.home)})
                self._last_user_action = None

    def _below_resolution(self, obj: Any, coord: str, d: float) -> tuple[ControllerAxis, float] | None:
        """(raw axis, minimum step in the coordinate's units) when 0 < |d| < one step of that axis, else None."""
        if d == 0:
            return None
        axis = self.axes_of(obj).get(coord)
        if axis is None:
            return None
        base, v0 = raw(axis, 0.0)
        _, v1 = raw(axis, 1.0)
        if not isinstance(base, ControllerAxis):
            return None
        scale = abs(v1 - v0)                      # raw units per coordinate unit (mapped axes)
        if scale == 0:
            return None
        res = base.settings.resolution
        if res:
            step = res / scale
            return (base, step) if abs(d) < step - 1e-9 else None
        # no resolution configured: a move within the tolerance is never sent, so don't drop it silently either;
        # offer twice the tolerance, which is always sent
        tol = base.tolerance / scale
        return (base, 2 * tol) if abs(d) <= tol + 1e-12 else None

    def move_to_safe_z(self, head: Any) -> None:
        lift = self.safe_z_targets(head)
        self.check(lift)
        self.execute(lift)

    def park(self, head: Any, axes: str = "all") -> dict:
        if axes not in ("xy", "z", "rotation", "all"):
            raise MachineError("invalid_params", "axes must be xy, z, rotation or all")
        self.move_to_safe_z(head)
        if axes == "z":
            return head.compute_location()
        if axes == "rotation":                  # as OpenPnP's C park: 0, or the middle of a limited range
            for n in self.m.of_kind("nozzle"):
                if n.head is not head:
                    continue
                base, _ = raw(n.rotation, 0.0)
                lim = base.settings.limit_rotation if isinstance(base, ControllerAxis) else None
                target = 0.0 if not lim or lim[0] <= 0 <= lim[1] else (lim[0] + lim[1]) / 2
                self.move_to(n, {"rotation": target}, safe_z="none")
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
