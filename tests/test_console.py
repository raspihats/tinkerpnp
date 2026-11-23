import json
import os

import pytest
from fastapi.testclient import TestClient

from tinkerpnp.api.server import create_app
from tinkerpnp.core.config import load_config
from tinkerpnp.core.machine import Machine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRBL = os.path.join(ROOT, "config", "examples", "simulated-grblhal.yaml")
SIMULATED = os.path.join(ROOT, "config", "examples", "simulated.yaml")


def instant(path):
    cfg = load_config(path)
    for o in cfg.objects:
        if o.kind == "driver" and o.type == "gcode":
            o.settings.port = "sim://grblhal"          # instant moves and homing
    return Machine(cfg)


@pytest.fixture
def api():
    with TestClient(create_app(instant(GRBL))) as c:
        yield c
        c.app.state.tinker.machine.close()


def send(api, command, **kw):
    return api.post("/api/gcode/send", json={"command": command, **kw}).json()


def test_console_works_on_a_disabled_machine(api):
    r = send(api, "$I")["result"]
    assert r == {"responses": ["[VER:1.1f.20251101:]", "[OPT:VNMSL,35,1024,5,0]", "ok"], "ok": True}
    r = send(api, "G1 X10 F1000")["result"]           # grbl waits to be homed: G-code is locked
    assert r == {"responses": ["error:9"], "ok": False}


def test_multiple_lines_and_escapes(api):
    r = send(api, "$I\n$I")["result"]
    assert r["responses"].count("ok") == 2 and r["ok"]
    r = send(api, "\\x18")["result"]                  # Ctrl-X + newline: reset, then exactly one ok
    assert r["responses"][-1] == "ok"
    machine = api.app.state.tinker.machine
    assert machine.get("driver", "grbl").transport.fake.received[-1] == ""


def test_raw_moves_update_the_position(api):
    api.post("/api/machine/enable")
    r = send(api, "G1 X50 Y20 F3000\nG4 P0")["result"]
    assert r["ok"]
    loc = api.get("/api/motion/position?object=axis/x").json()["result"]["location"]
    assert loc == {"position": 50.0}


def test_history_and_notifications(api):
    send(api, "$I")
    hist = [(h["direction"], h["line"]) for h in api.get("/api/gcode/history?limit=10").json()["result"]]
    i = hist.index(("tx", "$I"))
    assert hist[i:i + 4] == [("tx", "$I"), ("rx", "[VER:1.1f.20251101:]"), ("rx", "[OPT:VNMSL,35,1024,5,0]"),
                             ("rx", "ok")]
    assert ("tx", "?") in hist[i + 4:]                 # then the position is read back
    with api.websocket_connect("/websocket") as ws:
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "gcode.subscribe", "params": {}}))
        assert json.loads(ws.receive_text())["result"] == {}
        send(api, "$I")
        seen = []
        for _ in range(50):
            if ("rx", "ok") in seen:
                break
            msg = json.loads(ws.receive_text())
            if msg.get("method") == "notify_gcode":
                assert msg["params"]["driver"] == "grbl" and "eventtime" in msg["params"]
                seen.append((msg["params"]["direction"], msg["params"]["line"]))
        assert ("tx", "$I") in seen and ("rx", "ok") in seen


def test_bad_requests(api):
    assert api.post("/api/gcode/send", json={"command": ""}).status_code == 400
    assert api.post("/api/gcode/send", json={"command": "$I", "driver": "nope"}).status_code == 404
    with TestClient(create_app(Machine(load_config(SIMULATED)))) as c:
        r = c.post("/api/gcode/send", json={"command": "$I"})
        assert r.status_code == 400 and "no G-code driver" in r.json()["error"]["message"]
