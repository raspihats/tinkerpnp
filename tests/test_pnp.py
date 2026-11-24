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
    machine = Machine(build_config(data, SIMULATED))
    yield machine
    machine.close()


def run(m, fn, *args):
    return m.submit(fn, *args).result(timeout=10)


def test_feeder_geometry(m):
    s1, t1 = m.get("feeder", "S1"), m.get("feeder", "T1")
    assert s1.remaining() == 11 and s1.pick_location() == {"x": 100, "y": 200, "z": 5, "rotation": 90}
    s1.set_index(3)
    assert s1.pick_location()["x"] == pytest.approx(112)
    assert [t1.pick_location() for _ in [t1.set_index(4)]][0] == {"x": 310, "y": 112, "z": 4}
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
