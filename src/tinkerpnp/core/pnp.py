"""Picking and placing (run on the motion thread).

pick:  feed, then the nozzle goes to the feeder's pick location (safe Z first) at the part's angle in the feeder,
       down to the part top, vacuum on (head pump on first), pick dwell, up to safe Z.
place: to the target at the wanted part angle, down to the board surface + part height, vacuum off, place dwell,
       up; the head pump goes off when no nozzle holds a part any more.

Rotation ("absolute part angle", as OpenPnP's default): the nozzle picks at the part's angle in the feeder, so the
part sits on the nozzle at angle 0 relative to it; placing at angle P is then a nozzle rotation of P.
"""
from __future__ import annotations

from typing import Any

from .errors import MachineError, Stopped
from .objects import Nozzle


def _dwell(machine: Any, ms: float) -> None:
    if ms and machine.stop_event.wait(ms / 1000.0):
        raise Stopped()


def _set_vacuum(machine: Any, nozzle: Nozzle, on: bool) -> None:
    if nozzle.settings.vacuum:
        machine.actuate(nozzle.settings.vacuum, on)


def _pump(machine: Any, nozzle: Nozzle, on: bool) -> None:
    head = nozzle.head
    pump = head.settings.pump
    if not pump:
        return
    holding = [n for n in machine.of_kind("nozzle") if n.head is head and n.status().get("part")]
    act = machine.get("actuator", pump)
    if on and act.status().get("value") is not True:
        machine.actuate(pump, True)
        _dwell(machine, head.settings.pump_settle)
    elif not on and not holding and act.status().get("value") is not False:
        machine.actuate(pump, False)


def pick(machine: Any, nozzle: Nozzle, feeder: Any, part: dict | None = None) -> dict:
    """Pick the next part from a feeder. Returns {"part", "feeder", "pick_location"}."""
    if nozzle.status().get("part"):
        raise MachineError("busy", f"{nozzle.name} already holds {nozzle.status()['part']}")
    part_id = feeder.settings.part
    feeder.feed()
    loc = feeder.pick_location()
    planner = machine.planner
    over = {k: loc[k] for k in ("x", "y") if k in loc}
    if "rotation" in loc:
        over["rotation"] = loc["rotation"]
    planner.move_to(nozzle, over, safe_z="auto")
    if "z" in loc:
        planner.move_to(nozzle, {"z": loc["z"]}, safe_z="none")
    _pump(machine, nozzle, True)
    _set_vacuum(machine, nozzle, True)
    _dwell(machine, nozzle.settings.pick_dwell)
    nozzle.update(part=part_id, part_rotation=0.0, part_height=(part or {}).get("height", 0.0))
    planner.move_to_safe_z(nozzle.head)
    feeder.picked()
    return {"part": part_id, "feeder": feeder.id, "pick_location": loc}


def place(machine: Any, nozzle: Nozzle, location: dict, part: dict | None = None) -> dict:
    """Place the part on the nozzle at location (x, y, z = board surface, rotation = part angle)."""
    st = nozzle.status()
    if not st.get("part"):
        raise MachineError("invalid_params", f"{nozzle.name} holds no part")
    height = (part or {}).get("height", st.get("part_height") or 0.0)
    speed = (part or {}).get("speed", 1.0)
    planner = machine.planner
    over = {k: location[k] for k in ("x", "y") if k in location}
    if "rotation" in location:
        over["rotation"] = location["rotation"] + (st.get("part_rotation") or 0.0)
    planner.move_to(nozzle, over, speed=speed, safe_z="auto")
    if "z" in location:
        planner.move_to(nozzle, {"z": location["z"] + height}, speed=speed, safe_z="none")
    _set_vacuum(machine, nozzle, False)
    _dwell(machine, nozzle.settings.place_dwell)
    placed = st.get("part")
    nozzle.update(part=None, part_rotation=None, part_height=None)
    planner.move_to_safe_z(nozzle.head)
    _pump(machine, nozzle, False)
    return {"part": placed, "location": location}


def discard(machine: Any, nozzle: Nozzle) -> dict:
    """Drop the part on the nozzle at machine.discard_location (or where it is, if none is set)."""
    st = nozzle.status()
    planner = machine.planner
    target = machine.config.machine.discard_location
    if target is not None:
        planner.move_to(nozzle, {k: v for k, v in target.as_dict().items() if k in ("x", "y")}, safe_z="auto")
        if target.z is not None:
            planner.move_to(nozzle, {"z": target.z}, safe_z="none")
    _set_vacuum(machine, nozzle, False)
    _dwell(machine, max(nozzle.settings.place_dwell, 100.0))
    nozzle.update(part=None, part_rotation=None, part_height=None)
    planner.move_to_safe_z(nozzle.head)
    _pump(machine, nozzle, False)
    return {"part": st.get("part")}
