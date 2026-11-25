import json
import os
import time

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from tinkerpnp.api.server import create_app
from tinkerpnp.core.config import _plain, build_config
from tinkerpnp.core.errors import MachineError, Stopped
from tinkerpnp.core.machine import Machine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIMULATED = os.path.join(ROOT, "config", "examples", "simulated.yaml")


def simulated(time_scale: float = 0.0, **machine_section) -> Machine:
    """The simulated machine, with its config in a temporary file: servers built from it may write their config
    (config.update), and must never write the example in the repo."""
    import tempfile
    with open(SIMULATED) as fh:
        data = _plain(YAML(typ="rt").load(fh))
    data["drivers"]["sim"]["time_scale"] = time_scale
    data["machine"].update(machine_section)
    path = os.path.join(tempfile.mkdtemp(prefix="tinkerpnp-test-"), "machine.yaml")
    with open(path, "w") as fh:
        YAML().dump(data, fh)
    return Machine(build_config(data, path))


@pytest.fixture
def m():
    machine = simulated()
    yield machine
    machine.close()


def run(m, fn, *args):
    return m.submit(fn, *args).result(timeout=10)


def pos(m, axis):
    return m.get("axis", axis).position


def nozzle(m, nz):
    return m.get("nozzle", nz)


def test_nothing_moves_before_enable(m):
    with pytest.raises(MachineError) as e:
        run(m, m.planner.move_to, nozzle(m, "N1"), {"x": 10})
    assert e.value.name == "not_enabled"


def test_enable_homes_and_parks(m):
    run(m, m.enable)
    st = m.state.status()
    assert st["enabled"] and st["homed"] and st["state"] == "idle"
    assert (pos(m, "x"), pos(m, "y"), pos(m, "z")) == (1, 1, 57)       # park: head X/Y, Z safe
    assert m.get("axis", "a").homed                                     # rotation: no switch, counts as homed
    assert m.get("actuator", "pump").status()["value"] is False        # on_enable: false


def test_nozzle_offsets_and_seesaw_z(m):
    run(m, m.enable)
    loc = run(m, m.planner.move_to, nozzle(m, "N2"), {"x": 100, "y": 50, "z": 10})
    assert loc == {"x": 100, "y": 50, "z": 10, "rotation": 0}
    assert pos(m, "x") == pytest.approx(66.8)                          # 100 - N2 offset 33.2
    assert pos(m, "z") == pytest.approx(104)                           # z_n2 = 114 - Z
    assert nozzle(m, "N1").status()["location"]["z"] == pytest.approx(104)
    assert m.motion.status()["safe_z"] is False


def test_safe_z_before_xy_travel(m):
    run(m, m.enable)
    run(m, m.planner.move_to, nozzle(m, "N1"), {"z": 20})
    seen = []
    m.add_listener(lambda name, delta: seen.append((name, delta["position"])) if name in ("axis/x", "axis/z")
                   and "position" in delta else None)
    run(m, m.planner.move_to, nozzle(m, "N1"), {"x": 200})
    assert seen == [("axis/z", 57), ("axis/x", 200)]                   # up first, then across
    # with a Z target: up, across, then down
    seen.clear()
    run(m, m.planner.move_to, nozzle(m, "N1"), {"x": 210, "z": 30})
    assert seen == [("axis/x", 210), ("axis/z", 30)]                    # already safe: across, then down
    # no X/Y travel: no lift
    seen.clear()
    run(m, m.planner.move_to, nozzle(m, "N1"), {"z": 25})
    assert seen == [("axis/z", 25)]


def test_soft_limits_refuse_the_whole_move(m):
    run(m, m.enable)
    with pytest.raises(MachineError) as e:
        run(m, m.planner.move_to, nozzle(m, "N1"), {"x": 600, "y": 10})
    assert e.value.name == "soft_limit" and e.value.data["axis"] == "x"
    assert (pos(m, "x"), pos(m, "y")) == (1, 1)                         # Y didn't move either
    with pytest.raises(MachineError) as e:
        run(m, m.planner.move_to, nozzle(m, "N2"), {"z": -5})           # raw Z 119 > 116
    assert e.value.data["axis"] == "z"
    run(m, m.planner.move_to, nozzle(m, "N1"), {"rotation": 200})   # limit_rotation [-180, 180]: by whole turns
    assert pos(m, "a") == -160


def test_jog_and_head_camera(m):
    run(m, m.enable)
    run(m, m.planner.jog, nozzle(m, "N1"), {"x": 10, "y": 5})
    assert (pos(m, "x"), pos(m, "y")) == (11, 6)
    run(m, m.planner.jog, nozzle(m, "N1"), {"z": -5})
    assert pos(m, "z") == 52
    cam = m.get("camera", "top")
    loc = run(m, m.planner.move_to, cam, {"x": 100, "y": 100})
    assert pos(m, "x") == pytest.approx(78.1) and loc["x"] == pytest.approx(100)
    assert pos(m, "z") == 57                                            # head travel raised the nozzles


def test_jog_without_lift_keeps_z(m):
    run(m, m.enable)
    run(m, m.planner.move_to, nozzle(m, "N1"), {"x": 189.9, "y": 49.5, "z": 38})   # down over the camera
    loc = run(m, m.planner.jog, nozzle(m, "N1"), {"x": 0.1, "y": -0.1}, 1.0, "none")
    assert loc["z"] == 38 and loc["x"] == pytest.approx(190.0) and loc["y"] == pytest.approx(49.4)
    loc = run(m, m.planner.jog, nozzle(m, "N1"), {"x": 1})                         # default: lift first
    assert loc["z"] == 57


def test_disable_forgets_home(m):
    run(m, m.enable)
    run(m, m.actuate, "pump", "on")
    assert m.get("actuator", "pump").status()["value"] is True
    run(m, m.disable)
    st = m.state.status()
    assert not st["enabled"] and not st["homed"] and st["state"] == "disabled"
    assert m.get("actuator", "pump").status()["value"] is False        # on_disable
    assert not m.get("axis", "x").homed


def test_actuator_values_are_checked(m):
    with pytest.raises(MachineError) as e:
        run(m, m.actuate, "pump", True)
    assert e.value.name == "not_enabled"
    run(m, m.enable)
    with pytest.raises(MachineError) as e:
        run(m, m.actuate, "pump", 5)
    assert e.value.name == "invalid_params"
    with pytest.raises(MachineError) as e:
        run(m, m.actuate, "nope", True)
    assert e.value.name == "not_found"


def test_stop_aborts_the_move_and_the_queue():
    m = simulated(time_scale=1.0, on_enable=[], on_home=[])
    try:
        m.submit(m.enable).result(5)
        m.get("driver", "sim").settings.home_time = 0
        m.submit(m.home).result(5)
        moving = m.submit(m.planner.move_to, nozzle(m, "N1"), {"x": 400})    # ~3 s at 8000 mm/min
        queued = m.submit(m.planner.move_to, nozzle(m, "N1"), {"y": 100})
        time.sleep(0.3)
        m.stop("test")
        with pytest.raises(Stopped):
            moving.result(5)
        with pytest.raises(Stopped):
            queued.result(5)
        assert 0 < pos(m, "x") < 400 and pos(m, "y") == 0
        assert m.state.status()["state"] == "idle" and m.state.status()["message"] == "test"
        m.submit(m.planner.move_to, nozzle(m, "N1"), {"y": 1}).result(5)      # usable again
    finally:
        m.close()


def test_sequences_are_validated():
    from tinkerpnp.core.config import ConfigError
    with open(SIMULATED) as fh:
        data = _plain(YAML(typ="rt").load(fh))
    data["machine"]["on_home"] = ["home", {"actuate": {"nope": True}}, "dance"]
    with pytest.raises(ConfigError) as e:
        build_config(data, SIMULATED)
    paths = [p.path for p in e.value.problems]
    assert paths == ["machine.on_home[0]", "machine.on_home[1].actuate.nope", "machine.on_home[2]"]


# ── API ────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def api():
    machine = simulated()
    with TestClient(create_app(machine)) as c:
        yield c
    c.app.state.tinker.machine.close()


def test_api_enable_move_check(api):
    r = api.post("/api/motion/move_to", json={"object": "nozzle/N1", "location": {"x": 5}})
    assert r.status_code == 409 and r.json()["error"]["data"]["name"] == "not_enabled"
    assert api.post("/api/machine/enable").json() == {"result": {}}
    r = api.post("/api/motion/move_to", json={"object": "nozzle/N2", "location": {"x": 100, "z": 20}})
    assert r.json()["result"]["location"] == {"x": 100, "y": 1, "z": 20, "rotation": 0}
    r = api.get("/api/motion/position?object=axis/z").json()["result"]
    assert r == {"location": {"position": 94}}
    r = api.get('/api/motion/check?object=nozzle/N1&location={"x": 900}').json()["result"]
    assert r["ok"] is False and r["problems"][0]["axis"] == "x"
    r = api.post("/api/motion/move_to", json={"object": "camera/bottom", "location": {"x": 1}})
    assert r.status_code == 400
    r = api.post("/api/motion/move_to", json={"object": "axis/a", "position": 90})
    assert r.json()["result"]["location"] == {"position": 90}
    assert api.post("/api/actuator/set", json={"id": "valve1", "value": "on"}).json() == {"result": {"value": True}}
    assert api.post("/api/motion/park").json()["result"]["location"] == {"x": 1, "y": 1}


def test_config_change_refused_while_enabled(api):
    api.post("/api/machine/enable")
    r = api.post("/api/config/update", json={"changes": [{"op": "set", "path": "axes.x.feed", "value": 10}]})
    assert r.json()["error"]["data"]["name"] == "busy"


def test_websocket_stop_overtakes_a_running_move():
    machine = simulated(time_scale=1.0, on_enable=[], on_home=[])
    machine.get("driver", "sim").settings.home_time = 0
    with TestClient(create_app(machine)) as c, c.websocket_connect("/websocket") as ws:
        def call(i, method, **params):
            ws.send_text(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}))

        def response(i):
            while True:
                msg = json.loads(ws.receive_text())
                if msg.get("id") == i:
                    return msg
                if msg.get("method") == "notify_machine_state":
                    states.append(msg["params"]["state"])

        states: list = []
        call(1, "machine.enable"); assert response(1)["result"] == {}
        call(2, "machine.home"); assert response(2)["result"] == {}
        call(3, "motion.move_to", object="nozzle/N1", location={"x": 400})
        time.sleep(0.3)
        call(4, "machine.stop", reason="operator")
        assert response(4)["result"] == {}                              # answered while the move still runs
        assert response(3)["error"]["data"]["name"] == "stopped"
        assert "idle" in states and "homing" in states
    machine.close()


def test_jog_below_resolution():
    """Z moves in 0.225 mm steps: a 0.1 mm jog is refused with the minimum step, min_step=True makes one step."""
    import tempfile
    with open(SIMULATED) as fh:
        data = _plain(YAML(typ="rt").load(fh))
    data["drivers"]["sim"]["time_scale"] = 0.0
    data["axes"]["z"].update(resolution=0.225, tolerance=0.12)
    path = os.path.join(tempfile.mkdtemp(prefix="tinkerpnp-test-"), "machine.yaml")
    with open(path, "w") as fh:
        YAML().dump(data, fh)
    m = Machine(build_config(data, path))
    try:
        run(m, m.enable)
        z0 = pos(m, "z")
        with pytest.raises(MachineError) as e:
            run(m, m.planner.jog, nozzle(m, "N1"), {"z": -0.1})
        assert e.value.name == "below_resolution"
        assert e.value.data["min_step"] == pytest.approx(0.225) and e.value.data["coord"] == "z"
        assert pos(m, "z") == z0
        m.submit(m.planner.jog, nozzle(m, "N1"), {"z": -0.1}, 1.0, None, True).result(timeout=10)
        assert pos(m, "z") == pytest.approx(z0 - 0.225)
        # N2 sits on the mapped axis z_n2 = 114 - z: same step size, opposite direction
        m.submit(m.planner.jog, nozzle(m, "N2"), {"z": -0.05}, 1.0, None, True).result(timeout=10)
        assert pos(m, "z") == pytest.approx(z0)
        run(m, m.planner.jog, nozzle(m, "N1"), {"z": -1})                # larger steps are unaffected
        assert pos(m, "z") == pytest.approx(z0 - 1)
        run(m, m.planner.jog, nozzle(m, "N1"), {"x": 0.1})               # X has no resolution set
    finally:
        m.close()


def test_tolerance_must_be_below_resolution():
    with open(SIMULATED) as fh:
        data = _plain(YAML(typ="rt").load(fh))
    data["axes"]["z"].update(resolution=0.225, tolerance=0.25)
    with pytest.raises(Exception, match="tolerance"):
        build_config(data, "/nonexistent/machine.yaml")


def test_every_error_name_has_a_code():
    """Each MachineError/RpcError name raised in the code must be in jsonrpc.ERRORS (else HTTP answers 500)."""
    import re
    from tinkerpnp.api.jsonrpc import ERRORS
    src = os.path.join(ROOT, "src", "tinkerpnp")
    names = set()
    for d, _, files in os.walk(src):
        for f in files:
            if f.endswith(".py"):
                names |= set(re.findall(r'(?:MachineError|RpcError)\(\s*"([a-z_]+)"', open(os.path.join(d, f)).read()))
    assert names and not names - set(ERRORS), sorted(names - set(ERRORS))


def test_low_bandwidth_jpeg():
    """quality and scale make smaller JPEGs (low-bandwidth streams); the default stays full size."""
    import cv2
    import numpy as np
    m = simulated()
    try:
        cam = m.get("camera", "top")
        rng = np.random.default_rng(1)
        frame = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
        full = cam.jpeg(1, frame)
        small = cam.jpeg(1, frame, (), 60, 0.5)
        assert cv2.imdecode(np.frombuffer(full, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (480, 640)
        assert cv2.imdecode(np.frombuffer(small, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (240, 320)
        assert len(small) < len(full) / 3
        assert cam.jpeg(1, frame) == full                              # cached per (quality, scale)
    finally:
        m.close()


def test_jog_within_tolerance_is_never_silent(m):
    """Without a resolution, a jog inside the axis tolerance (it would not be sent) fails with below_resolution."""
    run(m, m.enable)
    tol = m.get("axis", "x").tolerance
    with pytest.raises(MachineError) as e:
        run(m, m.planner.jog, nozzle(m, "N1"), {"x": tol / 2})
    assert e.value.name == "below_resolution" and e.value.data["min_step"] == pytest.approx(2 * tol)
    x0 = pos(m, "x")
    m.submit(m.planner.jog, nozzle(m, "N1"), {"x": tol / 2}, 1.0, None, True).result(timeout=10)
    assert pos(m, "x") == pytest.approx(x0 + 2 * tol)
