import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from tinkerpnp import API_VERSION
from tinkerpnp.api.server import create_app
from tinkerpnp.core.config import load_config
from tinkerpnp.core.machine import Machine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIMULATED = os.path.join(ROOT, "config", "examples", "simulated.yaml")


@pytest.fixture
def machine():
    return Machine(load_config(SIMULATED))


@pytest.fixture
def client(machine):
    with TestClient(create_app(machine)) as c:
        yield c


def rpc(ws, method, params=None, req_id=1):
    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.receive_text())
        if msg.get("id") == req_id:
            return msg


# ── HTTP mirror ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_http_server_info(client):
    r = client.get("/api/server/info")
    assert r.status_code == 200
    info = r.json()["result"]
    assert info["api_version"] == API_VERSION
    assert info["machine_name"] == "Simulated MC5S3DP-XHAT"


def test_http_query_and_errors(client):
    r = client.post("/api/machine/objects/query", json={"objects": {"nozzle/N2": ["location"]}})
    assert r.json()["result"]["status"]["nozzle/N2"]["location"]["x"] == pytest.approx(33.2)

    r = client.post("/api/machine/objects/query", json={"objects": {"nozzle/N9": None}})
    assert r.status_code == 404
    assert r.json()["error"]["data"]["name"] == "not_found"

    r = client.post("/api/no/such/method", json={})
    assert r.status_code == 404 and r.json()["error"]["code"] == -32601

    r = client.post("/api/machine/objects/query", json={"objects": {}, "bogus": 1})
    assert r.status_code == 400 and "bogus" in r.json()["error"]["message"]

    r = client.post("/api/machine/objects/subscribe", json={"objects": {}})
    assert r.status_code == 400          # websocket only


def test_http_get_with_json_params(client):
    r = client.get('/api/machine/objects/query?objects={"machine":["state"]}')
    assert r.json()["result"]["status"]["machine"]["state"] == "disabled"


def test_config_types_have_schemas(client):
    types = client.get("/api/config/types").json()["result"]
    by_key = {(t["kind"], t["type"]): t for t in types}
    assert ("axis", "mapped") in by_key
    schema = by_key[("axis", "mapped")]["schema"]
    assert "input" in schema["properties"] and "map" in schema["properties"]


def test_config_get_and_objects(client):
    r = client.get('/api/config/get?path=axes.z_n2')
    assert r.json()["result"]["config"]["input"] == "z"
    nozzles = client.get("/api/config/objects?kind=nozzle").json()["result"]
    assert {n["name"] for n in nozzles} == {"nozzle/N1", "nozzle/N2"}


# ── WebSocket ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_websocket_identify_list_and_batch(client):
    with client.websocket_connect("/websocket") as ws:
        r = rpc(ws, "server.connection.identify", {"client_name": "pytest", "type": "script"})
        assert r["result"]["connection_id"] >= 1
        r = rpc(ws, "machine.objects.list", {"kind": "nozzle"}, req_id=2)
        assert r["result"]["objects"] == ["nozzle/N1", "nozzle/N2"]
        ws.send_text(json.dumps([
            {"jsonrpc": "2.0", "id": 10, "method": "server.info"},
            {"jsonrpc": "2.0", "id": 11, "method": "nope"},
        ]))
        batch = json.loads(ws.receive_text())
        assert {m["id"] for m in batch} == {10, 11}
        assert next(m for m in batch if m["id"] == 11)["error"]["code"] == -32601


def test_websocket_subscription_sends_deltas(client, machine):
    with client.websocket_connect("/websocket") as ws:
        r = rpc(ws, "machine.objects.subscribe",
                {"objects": {"nozzle/N1": ["location"], "motion": ["safe_z"]}, "rate": 20})
        assert r["result"]["status"]["motion"]["safe_z"] is True

        machine.set_axis_position("z", 6.0)          # simulate a move (from another thread)
        machine.set_axis_position("x", 12.0)

        deadline = time.time() + 2
        merged: dict = {}
        while time.time() < deadline and not ("motion" in merged and "nozzle/N1" in merged
                                                and merged["nozzle/N1"]["location"]["x"] == 12.0):
            msg = json.loads(ws.receive_text())
            assert msg["method"] == "notify_status_update"
            for name, fields in msg["params"]["status"].items():
                merged.setdefault(name, {}).update(fields)
        assert merged["motion"]["safe_z"] is False
        assert merged["nozzle/N1"]["location"] == {"x": 12.0, "y": 0.0, "z": 6.0, "rotation": 0.0}
        assert "nozzle/N2" not in merged             # not subscribed


def test_websocket_invalid_json(client):
    with client.websocket_connect("/websocket") as ws:
        ws.send_text("{not json")
        msg = json.loads(ws.receive_text())
        assert msg["error"]["code"] == -32700
