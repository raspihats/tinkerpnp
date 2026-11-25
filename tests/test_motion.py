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
    loc = run(m, m.planner.jog, nozzle(m, "N1"), {"x": 1})                  # default as OpenPnP: no lift
    assert loc["z"] == 38
    loc = run(m, m.planner.jog, nozzle(m, "N1"), {"x": 1}, 1.0, "auto")      # asked for: lift first
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


def test_disable_overtakes_and_stops_a_running_move():
    """As OpenPnP's power button: disabling mid-move stops the machine at once, it doesn't wait for the move."""
    machine = simulated(time_scale=1.0, on_enable=[], on_home=[])
    machine.get("driver", "sim").settings.home_time = 0
    with TestClient(create_app(machine)) as c, c.websocket_connect("/websocket") as ws:
        def call(i, method, **params):
            ws.send_text(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}))

        seen: dict = {}

        def response(i):                 # replies may come in any order
            while i not in seen:
                msg = json.loads(ws.receive_text())
                if "id" in msg:
                    seen[msg["id"]] = msg
            return seen[i]

        call(1, "machine.enable"); assert response(1)["result"] == {}
        call(2, "machine.home"); assert response(2)["result"] == {}
        call(3, "motion.move_to", object="nozzle/N1", location={"x": 400})    # ~3 s
        time.sleep(0.3)
        t0 = time.monotonic()
        call(4, "machine.disable")
        assert response(4)["result"] == {}
        assert time.monotonic() - t0 < 1.5                                   # not after the move
        assert response(3)["error"]["data"]["name"] == "stopped"
        st = machine.state.status()
        assert not st["enabled"] and not st["homed"] and pos(machine, "x") < 400
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


BOARD = {"id": "B1", "location": {"x": 100, "y": 100, "z": 10, "rotation": 0}, "dimensions": {"x": 50, "y": 40}}


def test_board_protection(m):
    """As OpenPnP: a jog that would put a nozzle below safe Z over a board (plus 1 mm) is refused; above the board's
    surface, outside its outline or in the safe zone it is fine."""
    run(m, m.enable)
    n1 = nozzle(m, "N1")
    run(m, m.planner.move_to, n1, {"x": 90, "y": 120, "z": 5})                  # below safe Z, left of the board
    with pytest.raises(MachineError) as e:
        m.submit(m.planner.jog, n1, {"x": 10}, 1.0, None, False, [BOARD]).result(timeout=10)    # onto it
    assert e.value.name == "board_protection" and e.value.data["board"] == "B1"
    assert pos(m, "x") == 90                                                    # nothing moved
    m.submit(m.planner.jog, n1, {"x": 8.5}, 1.0, None, False, [BOARD]).result(timeout=10)       # 1.5 mm off: ok
    m.submit(m.planner.jog, n1, {"z": 10}, 1.0, None, False, [BOARD]).result(timeout=10)        # Z 15 > board 10
    m.submit(m.planner.jog, n1, {"x": 10}, 1.0, None, False, [BOARD]).result(timeout=10)        # above: ok
    run(m, m.planner.jog, n1, {"z": -10})                                       # no boards given: no check
    assert pos(m, "z") == 5


def test_rpc_jog_uses_loaded_job_boards():
    from tinkerpnp.api.methods import _job_boards

    class Runner:
        job = {"boards": [{"board": "B1", "location": {"x": 1, "y": 2, "z": 3}},
                          {"board": "B2", "enabled": False}]}

    class Data:
        @staticmethod
        def find(collection, item_id):
            return {"id": item_id, "dimensions": {"x": 50, "y": 40}}

    class State:
        job, data = Runner(), Data()

    class Ctx:
        app_state = State()

    assert _job_boards(Ctx()) == [{"id": "B1", "location": {"x": 1, "y": 2, "z": 3}, "dimensions": {"x": 50, "y": 40}}]


def test_unsafe_z_roaming_resets_virtual_z(m):
    """As OpenPnP: a head camera (virtual Z) jogged below its safe Z (its home) farther than
    unsafe_z_roaming_distance (10 mm) from where it went below gets its Z back to safe Z."""
    run(m, m.enable)
    cam = m.get("camera", "top")
    m.state.update(selected_tool=cam.name)
    zc = m.planner.axes_of(cam)["z"]

    def jog(delta):
        run(m, m.planner.jog, cam, delta)
        m.planner.user_action(cam, jogging=True)

    jog({"z": -2})
    assert zc.position == -2
    jog({"x": 6})
    assert zc.position == -2                    # 6 mm from where it went below
    jog({"x": 6})
    assert zc.position == 0                     # 12 mm (incl. Z) > 10: back to safe Z
    n1 = nozzle(m, "N1")                        # a real Z axis is never reset
    m.state.update(selected_tool=n1.name)
    run(m, m.planner.jog, n1, {"z": -20})
    m.planner.user_action(n1, jogging=True)
    run(m, m.planner.jog, n1, {"x": 30})
    m.planner.user_action(n1, jogging=True)
    assert pos(m, "z") == 37


def test_jog_with_tip_runout_does_not_drift(m):
    """With a tip calibration, the location is the tip's: jogging up and down returns to the start, and a move puts
    the tip (reported location) exactly at the target. Before, every jog moved by the step minus the runout."""
    run(m, m.enable)
    n1 = nozzle(m, "N1")
    m.state_store.state_set(n1.calibration_key(), {"runout": 0.031, "phase": -92.4, "enabled": True})
    m.get("nozzle_tip", n1.settings.tip).settings.calibration.enabled = True
    run(m, m.planner.move_to, n1, {"x": 100, "y": 100, "rotation": 0})
    start = m.planner.location(n1)
    assert (start["x"], start["y"]) == (pytest.approx(100), pytest.approx(100))
    y_axis0 = pos(m, "y")
    assert y_axis0 == pytest.approx(100 + 0.031)          # the axis is off by the runout, the tip is on target
    for _ in range(5):
        run(m, m.planner.jog, n1, {"y": 0.1})
        run(m, m.planner.jog, n1, {"y": -0.1})
    end = m.planner.location(n1)
    assert end["y"] == pytest.approx(start["y"]) and end["x"] == pytest.approx(start["x"])
    run(m, m.planner.jog, n1, {"y": 0.1})
    assert pos(m, "y") == pytest.approx(y_axis0 + 0.1)


def test_before_home_lifts_when_homed(m):
    """machine.on_before_home ([safe_z] in the examples): homing a homed machine raises the nozzles first; an unhomed
    machine homes directly (its position is unknown)."""
    run(m, m.enable)
    order = []
    drv = m.get("axis", "x").driver
    real_home, real_safe = drv.home, m.planner.move_to_safe_z
    drv.home = lambda axes: (order.append(("home", pos(m, "z"))), real_home(axes))[1]
    m.planner.move_to_safe_z = lambda head: (order.append(("safe_z", pos(m, "z"))), real_safe(head))[1]
    run(m, m.planner.move_to, nozzle(m, "N1"), {"z": 38}, 1.0, "none")       # down, e.g. focused over the camera
    order.clear()
    run(m, m.home)
    assert order[0] == ("safe_z", 38) and order[1] == ("home", 57)            # raised to 57 before homing
    run(m, m.disable)
    n = len(order)
    run(m, m.enable)                                                          # on_enable homes from unknown
    assert order[n][0] == "home"                                             # no lift first (then park lifts)


def test_rotation_alone_lifts_first(m):
    """As OpenPnP's moveToLocationAtSafeZ: a move that only rotates still goes up first (a part isn't turned at
    depth); a jog (safe_z none) rotates in place."""
    run(m, m.enable)
    n1 = nozzle(m, "N1")
    run(m, m.planner.move_to, n1, {"x": 100, "y": 100, "z": 30})
    run(m, m.planner.move_to, n1, {"rotation": 45})
    assert pos(m, "a") == pytest.approx(45) and pos(m, "z") == pytest.approx(57)     # lifted to safe Z
    run(m, m.planner.move_to, n1, {"z": 30})
    run(m, m.planner.move_to, n1, {"rotation": 90}, 1.0, "none")
    assert pos(m, "a") == pytest.approx(90) and pos(m, "z") == pytest.approx(30)


def test_a_nozzle_without_a_safe_zone_refuses_to_travel(m):
    """OpenPnP throws "has no Z axis with Safe Zone mapped"; travelling at an unknown depth isn't safer."""
    run(m, m.enable)
    m.get("axis", "z").settings.safe_zone = None
    with pytest.raises(MachineError) as e:
        run(m, m.planner.move_to, nozzle(m, "N1"), {"x": 50})
    assert "safe_zone" in e.value.message
    run(m, m.planner.move_to, nozzle(m, "N1"), {"x": 50}, 1.0, "none")             # without safe Z it may


def test_safe_z_also_resets_a_head_cameras_virtual_z(m):
    """OpenPnP's safe Z covers every head mountable: a head camera's virtual Z goes back to its home."""
    run(m, m.enable)
    cam = m.get("camera", "top")
    run(m, m.planner.move_to, cam, {"z": -5}, 1.0, "none")
    assert pos(m, "z_top_camera") == pytest.approx(-5)
    run(m, m.planner.move_to, nozzle(m, "N1"), {"x": 120})
    assert pos(m, "z_top_camera") == pytest.approx(0)


def test_board_protection_counts_the_part_under_the_nozzle(m):
    """As OpenPnP: the safe-zone test uses the part's underside, so a nozzle at safe Z carrying a tall part over a
    board is still checked (made-up height: the board is at Z 10, safe Z is 57)."""
    run(m, m.enable)
    n1 = nozzle(m, "N1")
    run(m, m.planner.move_to, n1, {"x": 90, "y": 120})                         # at safe Z, left of the board
    m.submit(m.planner.jog, n1, {"x": 10}, 1.0, None, False, [BOARD]).result(timeout=10)        # no part: fine
    run(m, m.planner.jog, n1, {"x": -10})
    n1.update(part="TALL", part_height=48.0)                                     # underside at Z 9: below the board
    with pytest.raises(MachineError) as e:
        m.submit(m.planner.jog, n1, {"x": 10}, 1.0, None, False, [BOARD]).result(timeout=10)
    assert e.value.name == "board_protection" and "holding TALL" in e.value.message
    n1.update(part=None, part_height=None)


def test_failed_homing_on_enable_leaves_the_machine_enabled_and_unhomed():
    """As OpenPnP: enabling succeeds once the drivers are on; a failing home then leaves the machine enabled (to jog
    off a stuck switch) but not homed. A re-home unhomes first, so a failure doesn't keep the old 'homed'."""
    machine = simulated()
    try:
        drv = machine.get("driver", "sim")
        real_home = drv.home

        def broken(axes):
            raise MachineError("driver_error", "homing switch not found")
        drv.home = broken
        with pytest.raises(MachineError):
            run(machine, machine.enable)
        st = machine.state.status()
        assert st["enabled"] and not st["homed"]
        drv.home = real_home
        run(machine, machine.home)
        assert machine.state.status()["homed"]
        drv.home = broken
        with pytest.raises(MachineError):
            run(machine, machine.home)
        assert not machine.state.status()["homed"]
    finally:
        machine.close()


def test_homing_resets_virtual_axes(m):
    run(m, m.enable)
    run(m, m.planner.move_to, m.get("camera", "top"), {"z": -5}, 1.0, "none")
    run(m, m.home)
    assert pos(m, "z_top_camera") == 0


def test_global_speed_and_part_speed(m):
    """As OpenPnP: one global speed for every move, × the slowest part speed of a nozzle carrying a part on the
    moving head; never below 5 %."""
    run(m, m.enable)
    drv = m.get("driver", "sim")
    speeds = []
    real = drv.move
    drv.move = lambda targets, speed: (speeds.append(round(speed, 3)), real(targets, speed))[1]
    n1 = nozzle(m, "N1")
    run(m, m.planner.move_to, n1, {"x": 50})
    m.set_speed(0.5)
    run(m, m.planner.move_to, n1, {"x": 60})
    n1.update(part="SLOW", part_speed=0.4)
    run(m, m.planner.move_to, n1, {"x": 70})
    m.set_speed(0.01)
    run(m, m.planner.move_to, n1, {"x": 80})
    assert speeds == [1.0, 0.5, 0.2, 0.05]
    with pytest.raises(MachineError):
        m.set_speed(0)
    n1.update(part=None, part_speed=None)


def test_a_0_01_jog_moves_without_a_resolution(m):
    """OpenPnP only suppresses moves below the axis resolution (default 0.0001): the smallest jog step works."""
    run(m, m.enable)
    x0 = pos(m, "x")
    run(m, m.planner.jog, nozzle(m, "N1"), {"x": 0.01})
    assert pos(m, "x") == pytest.approx(x0 + 0.01)


def test_head_offset_z_applies(m):
    """As OpenPnP: a nozzle's head offset Z shifts its Z like X and Y (rotation offsets don't apply)."""
    run(m, m.enable)
    n2 = nozzle(m, "N2")
    n2.settings.offset.z = 0.5
    run(m, m.planner.move_to, n2, {"x": 100, "y": 100, "z": 20.5})
    assert pos(m, "z_n2") == pytest.approx(20, abs=0.01)
    assert m.planner.location(n2)["z"] == pytest.approx(20.5, abs=0.01)
    assert n2.compute_location()["z"] == pytest.approx(20.5, abs=0.01)


def test_narrow_rotation_limit_normalizes_by_whole_turns(m):
    """As OpenPnP: any limit range first takes the angle by whole turns ([-90, 90]: 270 -> -90); only an angle with no
    equivalent inside is refused. A rotation axis without limit_rotation has no limits (soft limits don't apply)."""
    from tinkerpnp.core.motion import normalize_rotation
    a = m.get("axis", "a")
    a.settings.limit_rotation = (-90.0, 90.0)
    assert normalize_rotation(a, 270) == pytest.approx(-90) and normalize_rotation(a, -300) == pytest.approx(60)
    assert normalize_rotation(a, 120) in (120, -240)                 # nothing fits: refused by the limit check
    run(m, m.enable)
    run(m, m.planner.move_to, nozzle(m, "N1"), {"rotation": 270})
    assert pos(m, "a") == pytest.approx(-90)
    with pytest.raises(MachineError):
        run(m, m.planner.move_to, nozzle(m, "N1"), {"rotation": 120})
    a.settings.limit_rotation = None
    a.settings.soft_limits = (0.0, 10.0)
    run(m, m.planner.move_to, nozzle(m, "N1"), {"rotation": 500})
    assert pos(m, "a") == pytest.approx(500)


def test_actuator_values_are_unknown_after_enable(m):
    """OpenPnP's default enabled-actuation AssumeUnknown: without on_enable, the value is unknown after enabling."""
    act = m.get("actuator", "pump")
    act.settings.on_enable = None
    act.settings.on_disable = None
    run(m, m.enable)
    run(m, m.actuate, "pump", True)
    run(m, m.disable)
    run(m, m.enable)
    assert act.status()["value"] is None


def test_click_on_a_fixed_camera_moves_the_nozzle(m):
    """OpenPnP: Shift+click in an up-looking camera's view moves the selected nozzle so the clicked point comes to
    the centre; within the roaming radius at its depth, outside at safe Z."""
    run(m, m.enable)
    cam, n1 = m.get("camera", "bottom"), nozzle(m, "N1")
    loc = cam.settings.location
    run(m, m.planner.move_to, n1, {"x": loc.x + 1, "y": loc.y, "z": loc.z})
    cam.settings.roaming_radius = 3.0
    run(m, m.planner.center_on, cam, {"x": loc.x + 1, "y": loc.y}, n1)         # the tip seen 1 mm right of centre
    got = m.planner.location(n1)
    assert got["x"] == pytest.approx(loc.x) and got["z"] == pytest.approx(loc.z)  # centred, still in focus
    cam.settings.roaming_radius = 0.0
    run(m, m.planner.center_on, cam, {"x": loc.x - 2, "y": loc.y}, n1)
    assert m.planner.location(n1)["x"] == pytest.approx(loc.x + 2) and pos(m, "z") == pytest.approx(57)   # safe Z


def test_two_controllers_move_together():
    """As OpenPnP: a move over two controllers starts on both, then waits for both (not one after the other)."""
    import tempfile
    with open(SIMULATED) as fh:
        data = _plain(YAML(typ="rt").load(fh))
    data["drivers"]["sim"]["time_scale"] = 1.0
    data["drivers"]["sim2"] = {"type": "simulated", "time_scale": 1.0, "home_time": 0}
    data["drivers"]["sim"]["home_time"] = 0
    data["axes"]["y"]["driver"] = "sim2"
    data["machine"].update(on_enable=[], on_home=[])
    path = os.path.join(tempfile.mkdtemp(prefix="tinkerpnp-test-"), "machine.yaml")
    with open(path, "w") as fh:
        YAML().dump(data, fh)
    machine = Machine(build_config(data, path))
    try:
        machine.submit(machine.enable).result(5)
        machine.submit(machine.home).result(5)
        t0 = time.monotonic()
        # X 100 mm at 8000 mm/min and Y 75 mm at 6000 mm/min: 0.75 s each; one after the other would be 1.5 s
        machine.submit(machine.planner.move_to, nozzle(machine, "N1"), {"x": 100, "y": 75}, 1.0, "none").result(10)
        assert time.monotonic() - t0 < 1.2
        assert (pos(machine, "x"), pos(machine, "y")) == (100, 75)
    finally:
        machine.close()


def test_wrap_around_turns_the_shortest_way(m):
    a = m.get("axis", "a")
    a.settings.limit_rotation = None
    a.settings.wrap_around = True
    run(m, m.enable)
    run(m, m.planner.move_to, nozzle(m, "N1"), {"rotation": 170}, 1.0, "none")
    run(m, m.planner.move_to, nozzle(m, "N1"), {"rotation": -170}, 1.0, "none")
    assert pos(m, "a") == pytest.approx(190)                         # +20 the short way, not -340


def test_backlash_one_sided_approach(m):
    """OpenPnP's one-sided positioning: past the target by the offset first, then to it (slower); optimized: only
    when the move would arrive from the other side."""
    from tinkerpnp.core.objects import Backlash
    run(m, m.enable)
    x = m.get("axis", "x")
    x.settings.backlash = Backlash(method="one_sided_optimized", offset=-0.5, speed_factor=0.5)
    drv = m.get("driver", "sim")
    seen = []
    real = drv.move
    drv.move = lambda t, speed: (seen.append((round(t.get(x, -1), 3), round(speed, 3))), real(t, speed))[1]
    n1 = nozzle(m, "N1")
    run(m, m.planner.move_to, n1, {"x": 50}, 1.0, "none")              # from 1 up to 50: already from below
    assert seen == [(50, 1.0)]
    seen.clear()
    run(m, m.planner.move_to, n1, {"x": 20}, 1.0, "none")              # down to 20: from above, so overshoot
    assert seen == [(19.5, 1.0), (20, 0.5)] and pos(m, "x") == 20


def test_dynamic_safe_z_lifts_by_the_part_height(m):
    """OpenPnP's dynamic safe Z: with a part on, the nozzle goes higher by the part's height, within the zone."""
    z = m.get("axis", "z")
    z.settings.safe_zone = (50.0, 60.0)
    run(m, m.enable)
    n1 = nozzle(m, "N1")
    n1.settings.dynamic_safe_z = True
    run(m, m.planner.move_to, n1, {"x": 100, "y": 100, "z": 20})
    n1.update(part="TALL", part_height=4.0)
    run(m, m.planner.move_to, n1, {"x": 150})
    assert pos(m, "z") == pytest.approx(54)                           # zone low end 50 + 4 mm part
    n1.update(part=None, part_height=None)


def test_cam_axis_follows_openpnp_formula(m):
    from tinkerpnp.core.objects import CamAxis, CamAxisSettings
    s = CamAxisSettings(input="a", cam_radius=10, wheel_radius=2, wheel_gap=0.5)
    ccw = CamAxis("cam1", "cam", s, m)
    ccw.setup()
    cw = CamAxis("cam2", "cam", s.model_copy(update={"direction": "clockwise"}), m)
    cw.setup()
    assert ccw.forward(0) == pytest.approx(2.5)                       # balanced (180° arms): sin 0
    assert ccw.forward(30) == pytest.approx(10 * 0.5 + 2.5)
    assert cw.forward(30) == pytest.approx(-10 * 0.5 + 2.5)           # the other nozzle goes the other way
    assert ccw.inverse(ccw.forward(25)) == pytest.approx(25) and cw.inverse(cw.forward(-40)) == pytest.approx(-40)


def test_c_park_and_auto_tool_select(m):
    run(m, m.enable)
    n2 = nozzle(m, "N2")
    run(m, m.planner.move_to, n2, {"rotation": 45}, 1.0, "none")
    run(m, m.planner.park, m.get("head", "H1"), "rotation")
    assert pos(m, "b") == pytest.approx(0)
    run(m, m.planner.user_action, n2, True)
    assert m.state.status()["selected_tool"] == "nozzle/N2"            # as OpenPnP's auto tool select
