import pytest

from tinkerpnp.core.errors import MachineError
from tinkerpnp.core.pnp import discard, pick, place

from test_motion import simulated



@pytest.fixture
def m():
    from ruamel.yaml import YAML
    import os
    from tinkerpnp.core.config import _plain, build_config
    from tinkerpnp.core.machine import Machine
    from test_motion import SIMULATED
    with open(SIMULATED) as fh:
        data = _plain(YAML(typ="rt").load(fh))
    data["drivers"]["sim"]["time_scale"] = 0
    data["machine"]["discard_location"] = {"x": 100, "y": 300, "z": 40}
    data["feeders"].update({
        "S1": {"type": "strip", "part": "C0603", "first": {"x": 100, "y": 200, "z": 5, "rotation": 90},
               "last": {"x": 140, "y": 200}, "pitch": 4},
        "T1": {"type": "tray", "part": "QFN", "first": {"x": 300, "y": 100, "z": 4}, "columns": 3, "rows": 2,
               "column_step": {"x": 10, "y": 0}, "row_step": {"x": 0, "y": 12}},
        "A1": {"type": "auto", "part": "LED", "pick_location": {"x": 400, "y": 50, "z": 6},
               "feed_actuator": "valve2", "feed_value": True, "release_value": False, "count": 2},
    })
    import tempfile
    path = os.path.join(tempfile.mkdtemp(prefix="tinkerpnp-test-"), "machine.yaml")
    with open(path, "w") as fh:            # a copy: servers built on this machine may write their config
        YAML().dump(data, fh)
    machine = Machine(build_config(data, path))
    yield machine
    machine.close()


def run(m, fn, *args):
    return m.submit(fn, *args).result(timeout=10)


def test_feeder_geometry(m):
    s1, t1 = m.get("feeder", "S1"), m.get("feeder", "T1")
    assert s1.remaining() == 11 and s1.pick_location() == {"x": 100, "y": 200, "z": 5, "rotation": 90}
    s1.set_index(3)
    assert s1.pick_location()["x"] == pytest.approx(112)
    # 3 columns x 2 rows: as OpenPnP along the shorter side (the 2 rows) first: index 4 = column 3, row 1
    assert [t1.pick_location() for _ in [t1.set_index(4)]][0] == {"x": 320, "y": 100, "z": 4}
    assert [t1.pick_location() for _ in [t1.set_index(1)]][0] == {"x": 300, "y": 112, "z": 4}
    t1.set_index(4)
    assert t1.remaining() == 2 and t1.status()["feeds_left"] == 2


def test_pick_and_place_cycle(m):
    run(m, m.enable)
    n1 = m.get("nozzle", "N1")
    r = run(m, pick, m, n1, m.get("feeder", "S1"), {"height": 0.8})
    assert r["part"] == "C0603" and n1.status()["part"] == "C0603"
    assert m.get("actuator", "valve1").status()["value"] is True and m.get("actuator", "pump").status()["value"]
    assert m.get("axis", "a").position == 90                       # picked at the part's angle in the tape
    assert m.get("feeder", "S1").index() == 1 and n1.in_safe_zone()
    r = run(m, place, m, n1, {"x": 150, "y": 100, "z": 2, "rotation": 45}, {"height": 0.8, "speed": 0.5})
    assert n1.status()["part"] is None and m.get("actuator", "valve1").status()["value"] is False
    assert m.get("actuator", "pump").status()["value"] is False   # no part held any more
    last = m.motion.status()["last_move"]
    assert last["requested"]["z"] == pytest.approx(2.8)            # board surface + part height
    loc = n1.compute_location()
    assert (loc["x"], loc["y"], loc["rotation"]) == (150, 100, 45)


def test_auto_feeder_and_empty(m):
    run(m, m.enable)
    n2, a1 = m.get("nozzle", "N2"), m.get("feeder", "A1")
    run(m, pick, m, n2, a1)
    assert m.get("actuator", "valve2").status()["value"] is True   # N2's vacuum (also A1's feed actuator here)
    run(m, discard, m, n2)
    assert n2.status()["part"] is None
    run(m, pick, m, n2, a1)
    run(m, discard, m, n2)
    with pytest.raises(MachineError) as e:
        run(m, pick, m, n2, a1)
    assert e.value.name == "feeder_error" and "empty" in e.value.message
    a1.set_index(0)
    assert a1.remaining() == 2


def test_nozzle_holding_a_part_cant_pick(m):
    run(m, m.enable)
    n1 = m.get("nozzle", "N1")
    run(m, pick, m, n1, m.get("feeder", "F1"))
    with pytest.raises(MachineError) as e:
        run(m, pick, m, n1, m.get("feeder", "F1"))
    assert e.value.name == "busy"


def test_pump_control_modes(m):
    """As OpenPnP's vacuum pump control."""
    from tinkerpnp.core.pnp import pump_off
    run(m, m.enable)
    head, pump = m.get("head", "H1"), m.get("actuator", "pump")
    n1 = m.get("nozzle", "N1")
    s1 = m.get("feeder", "S1")
    head.settings.pump_control = "keep_running"
    run(m, pick, m, n1, s1, {"height": 0.8})
    run(m, place, m, n1, {"x": 150, "y": 100, "z": 2}, {"height": 0.8})
    assert pump.status()["value"] is True                     # keeps running after the place
    run(m, pump_off, m, head, True)
    assert pump.status()["value"] is True                     # not at a task end either
    head.settings.pump_control = "task_duration"
    run(m, pump_off, m, head, True)
    assert pump.status()["value"] is False                    # off at the task end
    head.settings.pump_control = "keep_running"
    run(m, m.actuate, "pump", True)
    run(m, m.disable)
    assert pump.status()["value"] is False                    # every mode: off on disable


def test_on_off_for_a_number_actuator(m):
    """OpenPnP: actuate(true/false) on a double actuator uses its default on/off doubles (a PWM vacuum valve)."""
    act = m.get("actuator", "valve1")
    act.settings.value = "double"
    act.settings.on_value, act.settings.off_value = 0.7, 0.1
    assert act.coerce(True) == 0.7 and act.coerce("off") == 0.1 and act.coerce(0.5) == 0.5


def test_failing_feeder_is_taken_out_and_the_next_one_used(m):
    """As OpenPnP: feeding is retried (3); then the feeder is taken out of service and the job uses the next
    feeder with the part (higher priority first). feeder.reset (set_index) puts it back."""
    from tinkerpnp.core.jobs import JobRunner
    a1 = m.get("feeder", "A1")
    calls = []

    def broken():
        calls.append(1)
        raise MachineError("driver_error", "feed actuator timed out")
    a1.feed = broken
    with pytest.raises(MachineError):
        a1.feed_with_retries()
    assert len(calls) == 4 and a1.failed() and not a1.usable()
    a1.set_index(0)
    assert a1.failed() is None and a1.usable()
    # priority: two feeders with the same part, the high one first
    runner = JobRunner(m, {"id": "j", "boards": []}, [])
    s1, t1 = m.get("feeder", "S1"), m.get("feeder", "T1")
    t1.settings.part = "C0603"
    assert runner.feeder_for("C0603") is s1
    t1.settings.priority = "high"
    assert runner.feeder_for("C0603") is t1
    t1.set_failed("broken")
    assert runner.feeder_for("C0603") is s1


def test_blow_off_during_the_place_dwell(m):
    """OpenPnP's blow-off: after the vacuum valve closes, the blow-off actuator is on for the place dwell."""
    run(m, m.enable)
    n1 = m.get("nozzle", "N1")
    n1.settings.blow_off = "valve2"
    n1.settings.place_dwell = 20
    seen = []
    real = m.actuate
    m.actuate = lambda act_id, value: (seen.append((act_id, value)), real(act_id, value))[1]
    run(m, pick, m, n1, m.get("feeder", "S1"), {"height": 0.8})
    seen.clear()
    run(m, place, m, n1, {"x": 150, "y": 100, "z": 2}, {"height": 0.8})
    assert seen[:3] == [("valve1", False), ("valve2", True), ("valve2", False)]


def test_vacuum_part_on_and_part_off_checks(m):
    """As OpenPnP's vacuum sensing: after a pick the level must be in the tip's part-on range (else the part is
    dropped and the pick fails, to be retried); after a place in the part-off range (else the part is still held)."""
    run(m, m.enable)
    n1, sense = m.get("nozzle", "N1"), m.get("actuator", "valve2")
    n1.settings.vacuum_sense = "valve2"
    vac = m.get("nozzle_tip", "NT1").settings.vacuum
    vac.part_on, vac.part_off = (40.0, 100.0), (0.0, 10.0)
    s1 = m.get("feeder", "S1")
    sense.settings.read_value = 5.0                      # no vacuum: nothing picked up
    with pytest.raises(MachineError) as e:
        run(m, pick, m, n1, s1, {"height": 0.8})
    assert e.value.name == "part_check" and n1.status()["part"] is None and s1.index() == 0
    sense.settings.read_value = 60.0                     # a part seals the nozzle
    run(m, pick, m, n1, s1, {"height": 0.8})
    assert n1.status()["part"] == "C0603" and s1.index() == 1
    with pytest.raises(MachineError) as e:               # still 60 after the place: the part came back up
        run(m, place, m, n1, {"x": 150, "y": 100, "z": 2}, {"height": 0.8})
    assert e.value.name == "part_check" and n1.status()["part"] == "C0603"
    sense.settings.read_value = 3.0
    run(m, discard, m, n1)
    assert n1.status()["part"] is None


def _changer(x):
    from tinkerpnp.core.objects import Location, TipChanger
    return TipChanger(start=Location(x=x, y=300, z=30), middle1=Location(x=x, y=310, z=20),
                      middle2=Location(x=x, y=310, z=10), end=Location(x=x, y=300, z=10), middle2_to_end_speed=0.3)


def test_tip_changer_load_and_unload(m):
    """As OpenPnP: unload goes end -> middle2 -> middle1 -> start, load start -> middle1 -> middle2 -> end; the tip
    must be compatible and free; without changer locations it's a manual change."""
    from tinkerpnp.core.pnp import load_tip, unload_tip
    from tinkerpnp.core.objects import NozzleTip
    run(m, m.enable)
    n1 = m.get("nozzle", "N1")
    # a third tip, NT3, for N1, with changer locations; NT1 gets its holder too
    nt3 = NozzleTip("NT3", "nozzle_tip", m.get("nozzle_tip", "NT1").settings.model_copy(), m)
    m.objects["nozzle_tip/NT3"] = nt3
    m.get("nozzle_tip", "NT1").settings.changer = _changer(400)
    nt3.settings.changer = _changer(420)
    n1.settings.compatible_tips = ["NT1", "NT3"]
    moves = []
    real = m.planner.move_to
    m.planner.move_to = lambda obj, loc, speed=1.0, safe_z="auto": (moves.append((dict(loc), speed)), real(obj, loc, speed, safe_z))[1]
    run(m, load_tip, m, n1, "NT3")
    assert n1.tip_id() == "NT3" and n1.status()["tip"] == "NT3"
    path = [(mv[0], mv[1]) for mv in moves]
    assert path == [
        ({"x": 400, "y": 300}, 1.0), ({"z": 10}, 1.0),                                  # NT1 out: over its end,
        ({"x": 400, "y": 310, "z": 10}, 0.3), ({"x": 400, "y": 310, "z": 20}, 1.0),     # middle2 (that leg's
                                                                                        # speed), middle1,
        ({"x": 400, "y": 300, "z": 30}, 1.0),                                           # start
        ({"x": 420, "y": 300}, 1.0), ({"z": 30}, 1.0),                                  # NT3 in: its start,
        ({"x": 420, "y": 310, "z": 20}, 1.0), ({"x": 420, "y": 310, "z": 10}, 1.0),     # middle1, middle2,
        ({"x": 420, "y": 300, "z": 10}, 0.3)]                                           # end, slowly
    with pytest.raises(MachineError):                                       # NT2 isn't compatible with N1
        run(m, load_tip, m, n1, "NT2")
    m.get("nozzle_tip", "NT3").settings.changer = None
    with pytest.raises(MachineError) as e:                                  # no holder: by hand
        run(m, unload_tip, m, n1)
    assert e.value.name == "manual_tip_change"
    run(m, unload_tip, m, n1, True)
    assert n1.tip_id() is None
