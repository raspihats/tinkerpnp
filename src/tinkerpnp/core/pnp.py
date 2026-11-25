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
    """As OpenPnP: a driver with DELAY_COMMAND dwells in the controller (G4); otherwise the host waits."""
    if not ms:
        return
    for driver in machine.drivers():
        delay = getattr(driver, "delay", None)
        if delay is not None and delay(ms):
            return
    if machine.stop_event.wait(ms / 1000.0):
        raise Stopped()


def _tip(machine: Any, nozzle: Nozzle) -> Any:
    """The loaded nozzle tip; as OpenPnP, picking or placing without one is an error."""
    tip = machine.objects.get(f"nozzle_tip/{nozzle.tip_id()}") if nozzle.tip_id() else None
    if tip is None:
        raise MachineError("invalid_params", f"{nozzle.name}: no nozzle tip loaded")
    return tip


def _pick_dwell(machine: Any, nozzle: Nozzle) -> float:
    return nozzle.settings.pick_dwell + _tip(machine, nozzle).settings.pick_dwell


def _place_dwell(machine: Any, nozzle: Nozzle) -> float:
    return nozzle.settings.place_dwell + _tip(machine, nozzle).settings.place_dwell


def _release(machine: Any, nozzle: Nozzle) -> None:
    """Vacuum off, then (as OpenPnP) blow-off on for the place dwell if the nozzle has one, else just the dwell."""
    _set_vacuum(machine, nozzle, False)
    blow = nozzle.settings.blow_off
    if blow:
        machine.actuate(blow, True)
        try:
            _dwell(machine, _place_dwell(machine, nozzle))
        finally:
            machine.actuate(blow, False)
    else:
        _dwell(machine, _place_dwell(machine, nozzle))


def _vacuum_check(machine: Any, nozzle: Nozzle, part_on: bool) -> None:
    """As OpenPnP's vacuum sensing (Absolute): the level read must be in the tip's part-on / part-off range."""
    sense = nozzle.settings.vacuum_sense
    if not sense:
        return
    v = _tip(machine, nozzle).settings.vacuum
    wanted = v.part_on if part_on else v.part_off
    if wanted is None or not (v.check_after_pick if part_on else v.check_after_place):
        return
    level = float(machine.get("actuator", sense).read())
    nozzle.update(vacuum_value=round(level, 4))
    low, high = wanted
    if not low <= level <= high:
        what = "no part on the nozzle after the pick" if part_on else "the part is still on the nozzle after the place"
        raise MachineError("part_check", f"{nozzle.name}: {what} (vacuum {level:g}, expected {low:g}..{high:g})",
                           nozzle=nozzle.id, value=level)


def _set_vacuum(machine: Any, nozzle: Nozzle, on: bool) -> None:
    if nozzle.settings.vacuum:
        machine.actuate(nozzle.settings.vacuum, on)


def _pump(machine: Any, nozzle: Nozzle, on: bool) -> None:
    """The head's vacuum pump, as OpenPnP's pump control: on before the valve (then pump_settle), off per mode."""
    head = nozzle.head
    pump, mode = head.settings.pump, head.settings.pump_control
    if not pump or mode == "none":
        return
    act = machine.get("actuator", pump)
    if on:
        if act.status().get("value") is not True:
            machine.actuate(pump, True)
            _dwell(machine, head.settings.pump_settle)
        return
    holding = [n for n in machine.of_kind("nozzle") if n.head is head and n.status().get("part")]
    if mode == "part_on" and not holding and act.status().get("value") is not False:
        machine.actuate(pump, False)


def pump_off(machine: Any, head: Any, task_end: bool = False) -> None:
    """End of a task (job end: task_duration) or machine disable (every mode): off if no nozzle carries a part."""
    pump, mode = head.settings.pump, head.settings.pump_control
    if not pump or mode == "none" or (task_end and mode != "task_duration"):
        return
    if any(n.head is head and n.status().get("part") for n in machine.of_kind("nozzle")):
        return
    if machine.get("actuator", pump).status().get("value") is not False:
        machine.actuate(pump, False)


def pick(machine: Any, nozzle: Nozzle, feeder: Any, part: dict | None = None) -> dict:
    """Pick the next part from a feeder. Returns {"part", "feeder", "pick_location"}."""
    if nozzle.status().get("part"):
        raise MachineError("busy", f"{nozzle.name} already holds {nozzle.status()['part']}")
    _tip(machine, nozzle)
    part_id = feeder.settings.part
    feeder.feed_with_retries()
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
    _dwell(machine, _pick_dwell(machine, nozzle))
    nozzle.update(part=part_id, part_rotation=0.0, part_height=(part or {}).get("height", 0.0),
                  part_speed=(part or {}).get("speed", 1.0))     # slows every head move while it's on (OpenPnP)
    planner.move_to_safe_z(nozzle.head)
    try:
        _vacuum_check(machine, nozzle, part_on=True)
    except MachineError:
        # as OpenPnP: a failed part-on check discards (here: vacuum off, nothing held); the pick is retried
        _set_vacuum(machine, nozzle, False)
        nozzle.update(part=None, part_rotation=None, part_height=None, part_speed=None)
        _pump(machine, nozzle, False)
        raise
    feeder.picked()
    return {"part": part_id, "feeder": feeder.id, "pick_location": loc}


def place(machine: Any, nozzle: Nozzle, location: dict, part: dict | None = None) -> dict:
    """Place the part on the nozzle at location (x, y, z = board surface, rotation = part angle)."""
    st = nozzle.status()
    if not st.get("part"):
        raise MachineError("invalid_params", f"{nozzle.name} holds no part")
    height = (part or {}).get("height", st.get("part_height") or 0.0)
    planner = machine.planner
    over = {k: location[k] for k in ("x", "y") if k in location}
    if "rotation" in location:
        over["rotation"] = location["rotation"] + (st.get("part_rotation") or 0.0)
    planner.move_to(nozzle, over, safe_z="auto")         # at the part's speed: see Planner.effective_speed
    if "z" in location:
        planner.move_to(nozzle, {"z": location["z"] + height}, safe_z="none")
    _release(machine, nozzle)
    placed = st.get("part")
    nozzle.update(part=None, part_rotation=None, part_height=None, part_speed=None)
    planner.move_to_safe_z(nozzle.head)
    try:
        _vacuum_check(machine, nozzle, part_on=False)
    except MachineError:
        # as OpenPnP's part-off check: the part came back up with the nozzle; it counts as still held (discard it)
        nozzle.update(part=placed, part_rotation=st.get("part_rotation"), part_height=st.get("part_height"),
                      part_speed=st.get("part_speed"))
        raise
    _pump(machine, nozzle, False)
    return {"part": placed, "location": location}


def discard(machine: Any, nozzle: Nozzle) -> dict:
    """As OpenPnP: drop the part on the nozzle at machine.discard_location (incl. its rotation), as a place (vacuum
    off, place dwell). Nothing happens without a part. Without a discard location it drops where it is."""
    st = nozzle.status()
    if not st.get("part"):
        return {"part": None}
    planner = machine.planner
    target = machine.config.machine.discard_location
    if target is not None:
        planner.move_to(nozzle, {k: v for k, v in target.as_dict().items() if k in ("x", "y", "rotation")},
                        safe_z="auto")
        if target.z is not None:
            planner.move_to(nozzle, {"z": target.z}, safe_z="none")
    _release(machine, nozzle)
    nozzle.update(part=None, part_rotation=None, part_height=None, part_speed=None)
    planner.move_to_safe_z(nozzle.head)
    _pump(machine, nozzle, False)
    return {"part": st.get("part")}


# ── nozzle tip changes (as OpenPnP's tool changer) ─────────────────────────────────────────────────────────────────

def _changer_path(machine: Any, nozzle: Nozzle, changer: Any, reverse: bool) -> None:
    points = [changer.start, changer.middle1, changer.middle2, changer.end]
    speeds = [changer.start_to_middle1_speed, changer.middle1_to_middle2_speed, changer.middle2_to_end_speed]
    # OpenPnP's post-step actuators: on reaching middle 1, middle 2, end
    after = [None, changer.after_middle1, changer.after_middle2, changer.after_end]
    if reverse:
        points, speeds, after = points[::-1], speeds[::-1], after[::-1]
    planner = machine.planner
    first = {k: v for k, v in points[0].as_dict().items() if k in ("x", "y", "rotation")}
    planner.move_to(nozzle, first, safe_z="auto")                     # safe Z, over the first point
    if points[0].z is not None:
        planner.move_to(nozzle, {"z": points[0].z}, safe_z="none")
    for p, speed, act in zip(points[1:], speeds, after[1:]):
        planner.move_to(nozzle, {k: v for k, v in p.as_dict().items() if v is not None}, speed=speed, safe_z="none")
        if act is not None:
            machine.actuate(act.actuator, act.value)
    planner.move_to_safe_z(nozzle.head)


def unload_tip(machine: Any, nozzle: Nozzle, manual: bool = False) -> dict:
    """Put the loaded tip back in its holder (end -> middle2 -> middle1 -> start). Without a changer: by hand
    (manual=True records that the operator did it)."""
    if nozzle.status().get("part"):
        raise MachineError("busy", f"{nozzle.name} holds a part")
    tip_id = nozzle.tip_id()
    if tip_id is None:
        return {"tip": None}
    tip = machine.objects.get(f"nozzle_tip/{tip_id}")
    changer = tip.settings.changer if tip is not None else None
    if changer is None and not manual:
        raise MachineError("manual_tip_change", f"take {tip_id} off {nozzle.name} by hand, then confirm",
                           nozzle=nozzle.id, unload=tip_id)
    if changer is not None and not manual:
        _changer_path(machine, nozzle, changer, reverse=True)
    nozzle.set_tip(None)
    return {"tip": None, "unloaded": tip_id}


def load_tip(machine: Any, nozzle: Nozzle, tip_id: str, manual: bool = False) -> dict:
    """As OpenPnP's loadNozzleTip: unload what's on the nozzle, then take tip_id from its holder (start -> middle1 ->
    middle2 -> end). The tip must be one of the nozzle's compatible_tips and not on another nozzle."""
    tip = machine.objects.get(f"nozzle_tip/{tip_id}")
    if tip is None:
        raise MachineError("not_found", f"no nozzle tip {tip_id!r}", what="nozzle_tip", id=tip_id)
    if nozzle.settings.compatible_tips and tip_id not in nozzle.settings.compatible_tips:
        raise MachineError("invalid_params", f"{tip_id} isn't compatible with {nozzle.name} (compatible_tips)")
    other = next((n for n in machine.of_kind("nozzle") if n is not nozzle and n.tip_id() == tip_id), None)
    if other is not None:
        raise MachineError("busy", f"{tip_id} is on {other.name}")
    if nozzle.tip_id() == tip_id:
        return {"tip": tip_id}
    if nozzle.tip_id() is not None:
        unload_tip(machine, nozzle, manual=manual)
    changer = tip.settings.changer
    if changer is None and not manual:
        raise MachineError("manual_tip_change", f"put {tip_id} on {nozzle.name} by hand, then confirm",
                           nozzle=nozzle.id, load=tip_id)
    if changer is not None and not manual:
        _changer_path(machine, nozzle, changer, reverse=False)
    nozzle.set_tip(tip_id)
    cal = tip.settings.calibration
    if cal.enabled and cal.recalibrate == "tip_change":           # OpenPnP's NozzleTipChange recalibration
        from .calibration import calibrate_nozzle_tip
        camera = next((c for c in machine.of_kind("camera") if c.head is None and c.settings.looking == "up"), None)
        if camera is not None:
            result = calibrate_nozzle_tip(machine, nozzle, camera)
            machine.state_store.state_set(nozzle.calibration_key(), result)
    return {"tip": tip_id}
