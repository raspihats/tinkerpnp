import json
import os
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from tinkerpnp.api.server import create_app
from tinkerpnp.core.config import load_config
from tinkerpnp.core.machine import Machine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIMULATED = os.path.join(ROOT, "config", "examples", "simulated.yaml")


@pytest.fixture
def cfg_path(tmp_path):
    p = tmp_path / "machine.yaml"
    shutil.copy(SIMULATED, p)
    return str(p)


@pytest.fixture
def client(cfg_path):
    app = create_app(Machine(load_config(cfg_path)))
    with TestClient(app) as c:
        c.app_state = app.state.tinker
        yield c


def update(client, changes, **kw):
    return client.post("/api/config/update", json={"changes": changes, **kw})


def test_set_applies_to_running_machine_and_keeps_comments(client, cfg_path):
    before = open(cfg_path).read()
    assert "# starts with both nozzles up" in before
    r = update(client, [{"op": "set", "path": "heads.H1.nozzles.N2.offset.x", "value": 40.0}], revision=1)
    assert r.status_code == 200, r.json()
    assert r.json()["result"] == {"revision": 2, "applied": True, "restart_required": [], "warnings": []}

    loc = client.get('/api/machine/objects/query?objects={"nozzle/N2":["location"]}').json()
    assert loc["result"]["status"]["nozzle/N2"]["location"]["x"] == 40.0       # rebuilt machine uses it

    after = open(cfg_path).read()
    assert "# starts with both nozzles up" in after                            # comments survive
    assert "x: 40.0" in after or "x: 40" in after
    assert load_config(cfg_path).find("nozzle", "N2").settings.offset.x == 40.0


def test_units_are_accepted_in_values(client, cfg_path):
    r = update(client, [{"op": "set", "path": "axes.x.feed", "value": "6000 mm/min"}])
    assert r.status_code == 200
    assert load_config(cfg_path).find("axis", "x").settings.feed == pytest.approx(100.0)


def test_dry_run_writes_nothing(client, cfg_path):
    before = open(cfg_path).read()
    r = update(client, [{"op": "set", "path": "axes.z.safe_zone", "value": [50, 50]}], dry_run=True)
    assert r.json()["result"]["applied"] is False
    assert r.json()["result"]["revision"] == 1
    assert open(cfg_path).read() == before


def test_whole_config_is_validated(client, cfg_path):
    before = open(cfg_path).read()
    r = update(client, [{"op": "delete", "path": "actuators.valve1"}])
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["data"]["name"] == "validation_error"
    paths = {e["path"] for e in err["data"]["errors"]}
    assert "heads.H1.nozzles.N1.vacuum" in paths and "heads.H1.actuators[1]" in paths
    assert open(cfg_path).read() == before

    r = update(client, [{"op": "set", "path": "axes.x.soft_limits", "value": "wide"}])
    assert r.status_code == 422


def test_conflict_and_bad_requests(client):
    r = update(client, [{"op": "set", "path": "name", "value": "x"}], revision=7)
    assert r.status_code == 409 and r.json()["error"]["data"]["revision"] == 1
    assert update(client, [{"op": "rename", "path": "name"}]).status_code == 400
    assert update(client, [{"op": "delete", "path": "axes.nope"}]).status_code == 404
    assert update(client, []).status_code == 400
    assert update(client, [{"op": "add", "path": "axes.x", "value": {}}]).status_code == 409   # exists


def test_add_new_object_and_list_item(client, cfg_path):
    r = update(client, [
        {"op": "add", "path": "feeders.F2", "value": {"type": "static", "part": "C0603",
                                                       "pick_location": {"x": 10, "y": 20, "z": 6}}},
        {"op": "add", "path": "machine.on_enable", "value": {"actuate": {"pump": False}}},
    ])
    assert r.status_code == 200, r.json()
    objs = client.get("/api/machine/objects/list?kind=feeder").json()["result"]["objects"]
    assert objs == ["feeder/F1", "feeder/F2"]
    assert load_config(cfg_path).machine.on_enable == ["home", {"actuate": {"pump": False}}]


def test_history_and_undo_restore_the_file(client, cfg_path):
    original = open(cfg_path).read()
    update(client, [{"op": "set", "path": "name", "value": "Edited"}])
    update(client, [{"op": "set", "path": "axes.z.safe_zone", "value": [50, 50]}])
    hist = client.get("/api/config/history").json()["result"]
    assert [h["revision"] for h in hist] == [3, 2]
    assert hist[0]["summary"] == "set axes.z.safe_zone"

    r = client.post("/api/config/undo", json={"revision": 1})
    assert r.json()["result"]["revision"] == 4
    assert open(cfg_path).read() == original                                   # byte for byte
    info = client.get("/api/server/info").json()["result"]
    assert info["machine_name"] == "Simulated MC5S3DP-XHAT"


def test_refused_while_enabled(client):
    client.app_state.machine.state.update(enabled=True)
    r = update(client, [{"op": "set", "path": "drivers.sim.time_scale", "value": 0}])
    assert r.status_code == 409 and r.json()["error"]["data"]["name"] == "busy"


def test_included_objects_are_protected(tmp_path):
    (tmp_path / "feeders").mkdir()
    (tmp_path / "feeders" / "bank.yaml").write_text(
        "feeders:\n  F9: {type: static, pick_location: {x: 1, y: 2, z: 3}}\n")
    main = tmp_path / "machine.yaml"
    main.write_text("format: tinkerpnp-machine/1\ninclude: [feeders/*.yaml]\ndrivers:\n  d: {type: simulated}\n")
    app = create_app(Machine(load_config(str(main))))
    with TestClient(app) as c:
        r = c.post("/api/config/update", json={"changes": [{"op": "set", "path": "feeders.F9.part", "value": "x"}]})
        assert r.status_code == 422 and "included file" in r.json()["error"]["message"]


def test_websocket_clients_are_notified(client):
    with client.websocket_connect("/websocket") as ws:
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "server.connection.identify",
                                 "params": {"client_name": "watcher"}}))
        json.loads(ws.receive_text())
        update(client, [{"op": "set", "path": "name", "value": "Renamed"}])
        deadline = time.time() + 2
        while time.time() < deadline:
            msg = json.loads(ws.receive_text())
            if msg.get("method") == "notify_config_changed":
                assert msg["params"]["revision"] == 2
                assert msg["params"]["paths"] == ["name"]
                assert msg["params"]["client"].startswith("http")
                break
        else:
            pytest.fail("no notify_config_changed")


def test_invalid_object_is_not_also_reported_as_missing(client):
    r = update(client, [{"op": "set", "path": "axes.x.soft_limits", "value": "wide"}], dry_run=True)
    paths = [e["path"] for e in r.json()["error"]["data"]["errors"]]
    assert paths == ["axes.x.soft_limits"]


def test_calibration_changes_while_enabled(client, cfg_path):
    """Calibration values go in live (no rebuild, homing kept); structural changes still need a disabled machine."""
    client.post("/api/machine/enable")
    machine = client.app_state.machine
    r = update(client, [{"op": "set", "path": "cameras.bottom.location", "value": {"x": 200, "y": 40, "z": 30}}])
    assert r.status_code == 200, r.json()
    assert client.app_state.machine is machine                     # same machine, still enabled and homed
    assert machine.state.status()["enabled"] and machine.state.status()["homed"]
    assert machine.get("camera", "bottom").status()["location"] == {"x": 200, "y": 40, "z": 30}
    r = update(client, [{"op": "set", "path": "heads.H1.nozzles.N2.offset.x", "value": 33.0}])
    assert r.status_code == 200
    assert machine.get("nozzle", "N2").status()["location"]["x"] == pytest.approx(1 + 33.0)
    r = update(client, [{"op": "set", "path": "axes.x.feed", "value": 100}])
    assert r.status_code == 409 and r.json()["error"]["data"]["paths"] == ["axes.x.feed"]
    r = client.post("/api/config/undo", json={"revision": 1})          # undoing calibration is live too
    assert r.status_code == 200
    assert machine.get("camera", "bottom").status()["location"]["x"] == 189.9


def test_capture_location(client):
    r = client.post("/api/config/capture", json={"field": "cameras.bottom.location", "from": "location",
                                                  "args": {"object": "nozzle/N1"}})
    assert r.status_code == 200, r.json()
    assert r.json()["result"]["value"] == {"x": 0.0, "y": 0.0, "z": 57.0}
    assert client.get("/api/config/capture_helpers").json()["result"][0]["name"] == "location"
    r = client.post("/api/config/capture", json={"field": "x", "from": "magic", "args": {"object": "nozzle/N1"}})
    assert r.status_code == 400
