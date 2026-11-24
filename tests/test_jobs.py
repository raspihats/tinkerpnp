import time

import pytest
from fastapi.testclient import TestClient

from tinkerpnp.api.server import create_app
from tinkerpnp.core.jobs import board_to_machine

from test_pnp import m  # noqa: F401  (fixture: simulated machine with strip, tray and auto feeders)


def test_board_to_machine():
    loc = board_to_machine({"x": 100, "y": 50, "z": 3, "rotation": 90}, {"x": 10, "y": 0, "rotation": 45})
    assert loc["x"] == pytest.approx(100) and loc["y"] == pytest.approx(60)
    assert loc["z"] == 3 and loc["rotation"] == 135


def wait_for(c, states, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = c.get('/api/machine/objects/query?objects={"job":null}').json()["result"]["status"]["job"]
        if st["state"] in states:
            return st
        time.sleep(0.05)
    raise AssertionError(f"job state {st['state']}, wanted {states}")


@pytest.fixture
def api(m):  # noqa: F811
    with TestClient(create_app(m, data_path=":memory:")) as c:
        post = lambda method, **p: c.post(f"/api/{method.replace('.', '/')}", json=p)  # noqa: E731
        post("data.parts.create", item={"id": "C0603", "height": 0.8, "pick_retry_count": 1})
        post("data.parts.create", item={"id": "QFN", "height": 0.9})
        post("data.boards.create", item={"id": "b1", "placements": [
            {"id": "C1", "part": "C0603", "location": {"x": 10, "y": 5, "rotation": 0}},
            {"id": "C2", "part": "C0603", "location": {"x": 20, "y": 5, "rotation": 90}},
            {"id": "U1", "part": "QFN", "location": {"x": 30, "y": 15}},
            {"id": "FID1", "type": "fiducial", "location": {"x": 1, "y": 1}},
            {"id": "R9", "part": "C0603", "location": {"x": 5, "y": 5}, "enabled": False}]})
        post("data.jobs.create", item={"id": "j1", "boards": [{"board": "b1", "location": {"x": 150, "y": 100, "z": 2}}]})
        c.post = c.post
        c.rpc = post
        yield c


def test_job_runs_and_records_placed(api):
    r = api.rpc("machine.enable")
    assert r.status_code == 200
    assert api.rpc("job.load", job_id="j1").json()["result"] == {"placements": 3}     # no fiducial, no disabled
    plan = api.get("/api/job/plan").json()["result"]
    assert [p["placement"] for p in plan] == ["C1", "C2", "U1"] and plan[0]["location"]["x"] == 160
    assert api.rpc("job.start").status_code == 200
    st = wait_for(api, ["finished", "paused", "error"])
    assert st["state"] == "finished", st
    assert st["progress"] == {"done": 3, "total": 3}
    job = api.get("/api/data/jobs/get?id=j1").json()["result"]
    assert job["boards"][0]["placed"] == ["C1", "C2", "U1"]
    m = api.app.state.tinker.machine
    assert m.get("feeder", "S1").index() == 2 and m.get("feeder", "T1").index() == 1
    # loading again: nothing left; reset makes all placements pending again
    assert api.rpc("job.load", job_id="j1").json()["result"] == {"placements": 0}
    api.rpc("job.reset", job_id="j1")
    assert api.rpc("job.load", job_id="j1").json()["result"] == {"placements": 3}


def test_step_pause_and_empty_feeder(api):
    api.rpc("machine.enable")
    m = api.app.state.tinker.machine
    m.get("feeder", "T1").set_index(6)                       # tray empty
    api.rpc("job.load", job_id="j1")
    api.rpc("job.step")
    st = wait_for(api, ["paused"])
    assert st["progress"]["done"] == 1                       # one placement, then paused
    api.rpc("job.resume")
    st = wait_for(api, ["paused"])                           # U1: no QFN left -> paused with the reason
    assert st["current_placement"] == "U1" and "QFN" in st["message"]
    m.get("feeder", "T1").set_index(0)                       # "loaded a new tray"
    api.rpc("job.resume")
    st = wait_for(api, ["finished"])
    assert st["progress"]["done"] == 3


def test_job_refuses_when_not_homed(api):
    api.rpc("job.load", job_id="j1")
    r = api.rpc("job.start")
    assert r.status_code == 409 and r.json()["error"]["data"]["name"] == "not_enabled"
