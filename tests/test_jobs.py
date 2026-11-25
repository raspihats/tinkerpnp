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
            {"id": "R9", "part": "C0603", "location": {"x": 5, "y": 5}, "enabled": False},
            {"id": "C9", "part": "C0603", "side": "bottom", "location": {"x": 6, "y": 6}}]})
        post("data.jobs.create", item={"id": "j1", "boards": [{"board": "b1", "location": {"x": 150, "y": 100, "z": 2}}]})
        c.post = c.post
        c.rpc = post
        yield c


def test_job_runs_and_records_placed(api):
    r = api.rpc("machine.enable")
    assert r.status_code == 200
    assert api.rpc("job.load", job_id="j1").json()["result"] == {"placements": 3}     # no fiducial, no disabled, no bottom side
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
    assert st["progress"]["done"] == 2                       # one cycle (N1 and N2: C1, C2), then paused
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


def test_preflight_refuses_a_job_that_cant_run(api):
    """As OpenPnP: a missing feeder (or part, or compatible tip) stops the job before it starts."""
    api.rpc("machine.enable")
    api.rpc("data.parts.create", item={"id": "NOFEED"})
    api.rpc("data.boards.create", item={"id": "b2", "placements": [{"id": "R1", "part": "NOFEED"}]})
    api.rpc("data.jobs.create", item={"id": "j2", "boards": [{"board": "b2", "location": {"x": 150, "y": 100}}]})
    api.rpc("job.load", job_id="j2")
    r = api.rpc("job.start")
    assert r.status_code == 422
    assert "no enabled feeder for NOFEED" in r.json()["error"]["message"]


def test_nozzle_comes_from_the_packages_compatible_tips(api):
    """Not the tool selected in the UI: the first nozzle whose loaded tip the part's package allows."""
    api.rpc("data.packages.create", item={"id": "QFN16", "compatible_nozzle_tips": ["NT2"]})
    api.rpc("data.parts.update", id="QFN", changes={"package": "QFN16"})
    api.rpc("job.load", job_id="j1")
    runner = api.app.state.tinker.job
    assert runner.nozzle_for("QFN").id == "N2" and runner.nozzle_for("C0603").id == "N1"
    api.rpc("data.packages.update", id="QFN16", changes={"compatible_nozzle_tips": ["NT9"]})
    assert runner.nozzle_for("QFN") is None
    assert any("no nozzle has or can load a tip" in p for p in runner.preflight())


def test_job_starts_with_empty_nozzles_and_ends_parked(api):
    """As OpenPnP: discard what's on the nozzles first; at the end safe Z, discard, park."""
    api.rpc("machine.enable")
    m = api.app.state.tinker.machine
    n2 = m.get("nozzle", "N2")
    n2.update(part="LEFTOVER", part_height=0.5)
    api.rpc("job.load", job_id="j1")
    assert api.rpc("job.start").status_code == 200
    st = wait_for(api, ["finished", "paused", "error"])
    assert st["state"] == "finished", st
    assert n2.status()["part"] is None
    head = m.get("head", "H1")
    assert head.x.position == pytest.approx(1) and head.y.position == pytest.approx(1)     # machine.park


def test_defer_error_handling(api, monkeypatch):
    """As OpenPnP's Defer: a failing placement is put back after the others, retried up to max_placement_retries,
    then marked errored; the job goes on and finishes."""
    import tinkerpnp.core.jobs as jobs
    real = jobs.pick
    tries = []

    def flaky(machine, nozzle, feeder, part=None):
        if feeder.settings.part == "QFN":
            tries.append(1)
            raise jobs.MachineError("vision_failed", "no part seen")
        return real(machine, nozzle, feeder, part)
    monkeypatch.setattr(jobs, "pick", flaky)
    api.rpc("data.jobs.update", id="j1", changes={"error_handling": "defer", "max_placement_retries": 2})
    api.rpc("machine.enable")
    api.rpc("job.load", job_id="j1")
    assert api.rpc("job.start").status_code == 200
    st = wait_for(api, ["finished", "paused", "error"])
    assert st["state"] == "finished", st
    assert st["progress"] == {"done": 2, "total": 3, "errored": 1}
    assert len(tries) == 3                                      # first try + 2 retries
    plan = api.get("/api/job/plan").json()["result"]
    assert [p["placement"] for p in plan if p["errored"]] == ["U1"]


def test_cycles_use_every_nozzle_and_the_order_groups_by_tip(api):
    """As OpenPnP: a cycle picks on every nozzle that can take a part, then places them all; the default order
    groups placements by the nozzle tip they need, shortest path within a group, rank first."""
    api.rpc("data.packages.create", item={"id": "QFN16", "compatible_nozzle_tips": ["NT2"]})
    api.rpc("data.parts.update", id="QFN", changes={"package": "QFN16"})
    api.rpc("data.packages.create", item={"id": "C0603P", "compatible_nozzle_tips": ["NT1"]})
    api.rpc("data.parts.update", id="C0603", changes={"package": "C0603P"})
    api.rpc("job.load", job_id="j1")
    runner = api.app.state.tinker.job
    order = [s.placement for s in runner.steps]
    assert order.index("U1") in (0, 2) and abs(order.index("C1") - order.index("C2")) == 1     # grouped by tip
    cycle = runner.plan_cycle()
    assert sorted(n.id for _, n in cycle) == ["N1", "N2"]                  # one C0603 on N1, the QFN on N2
    assert {s.placement for s, n in cycle if n.id == "N2"} == {"U1"}
    # rank first
    board = api.get("/api/data/boards/get?id=b1").json()["result"]
    for p in board["placements"]:
        if p["id"] == "C2":
            p["rank"] = -1
    api.rpc("data.boards.update", id="b1", changes={"placements": board["placements"]})
    api.rpc("job.load", job_id="j1")
    assert api.app.state.tinker.job.steps[0].placement == "C2"
    api.rpc("machine.enable")
    assert api.rpc("job.start").status_code == 200
    st = wait_for(api, ["finished", "paused", "error"])
    assert st["state"] == "finished" and st["progress"]["done"] == 3, st


def test_job_changes_tips(api):
    """As OpenPnP: a part whose package needs another tip gets it loaded (tool changer); a tip without a holder is
    a manual change: the job pauses, the operator confirms (nozzle.load_tip manual), resume finishes."""
    from test_pnp import _changer
    from tinkerpnp.core.objects import NozzleTip
    m = api.app.state.tinker.machine
    nt3 = NozzleTip("NT3", "nozzle_tip", m.get("nozzle_tip", "NT1").settings.model_copy(deep=True), m)
    m.objects["nozzle_tip/NT3"] = nt3
    m.get("nozzle_tip", "NT1").settings.changer = _changer(400)
    nt3.settings.changer = _changer(420)
    m.get("nozzle", "N1").settings.compatible_tips = ["NT1", "NT3"]
    m.get("nozzle", "N2").settings.compatible_tips = ["NT2"]
    api.rpc("data.packages.create", item={"id": "QFN16", "compatible_nozzle_tips": ["NT3"]})
    api.rpc("data.parts.update", id="QFN", changes={"package": "QFN16"})
    api.rpc("machine.enable")
    api.rpc("job.load", job_id="j1")
    assert api.rpc("job.start").status_code == 200
    st = wait_for(api, ["finished", "paused", "error"])
    assert st["state"] == "finished", st
    assert m.get("nozzle", "N1").tip_id() == "NT3"
    # manual: NT1 has no holder now; the next job needs it back on N1
    m.get("nozzle_tip", "NT1").settings.changer = None
    api.rpc("data.packages.update", id="QFN16", changes={"compatible_nozzle_tips": ["NT1"]})
    api.rpc("job.reset", job_id="j1")
    api.rpc("job.load", job_id="j1")
    assert api.rpc("job.start").status_code == 200
    st = wait_for(api, ["paused", "finished", "error"])
    assert st["state"] == "paused" and "by hand" in st["message"], st
    assert api.rpc("nozzle.load_tip", nozzle="N1", tip="NT1", manual=True).status_code == 200
    api.rpc("job.resume")
    st = wait_for(api, ["finished", "error"])
    assert st["state"] == "finished", st
