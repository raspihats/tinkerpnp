import math

import pytest
from fastapi.testclient import TestClient

from tinkerpnp.api.server import create_app
from tinkerpnp.core.jobs import board_to_machine
from tinkerpnp.core.vision import fit_board

from test_pnp import m  # noqa: F401

# the "real" board: 0.6 mm right, 0.4 mm down and 0.5 degrees turned against where the job thinks it is
NOMINAL = {"x": 150, "y": 100, "z": 2, "rotation": 0}
REAL = {"x": 150.6, "y": 99.6, "z": 2, "rotation": 0.5}
FIDS = [{"id": "FID1", "x": 2, "y": 2}, {"id": "FID2", "x": 45, "y": 25}, {"id": "FID3", "x": 45, "y": 2}]


def test_fit_board_exact_and_least_squares():
    board = [(f["x"], f["y"]) for f in FIDS]
    real = [(p["x"], p["y"]) for p in (board_to_machine(REAL, f) for f in FIDS)]
    fit = fit_board(board[:2], real[:2])
    assert fit["x"] == pytest.approx(150.6, abs=1e-6) and fit["rotation"] == pytest.approx(0.5, abs=1e-6)
    noisy = [(x + 0.01 * (i - 1), y) for i, (x, y) in enumerate(real)]
    fit = fit_board(board, noisy)
    assert fit["rotation"] == pytest.approx(0.5, abs=0.05) and fit["error_mm"] < 0.02 and fit["scale"] == pytest.approx(1, abs=1e-3)


@pytest.fixture
def api(m):  # noqa: F811
    cam = m.get("camera", "top")
    cam.settings.settle = 120
    cam.settings.sim_fiducials = [{**board_to_machine(REAL, f), "diameter": 1.0} for f in FIDS]
    with TestClient(create_app(m, data_path=":memory:")) as c:
        rpc = lambda method, **p: c.post(f"/api/{method.replace('.', '/')}", json=p)  # noqa: E731
        rpc("data.boards.create", item={"id": "fb", "placements": [
            *[{"id": f["id"], "type": "fiducial", "location": {"x": f["x"], "y": f["y"]}} for f in FIDS],
            {"id": "C1", "part": "C0603", "location": {"x": 10, "y": 5}}]})
        rpc("data.jobs.create", item={"id": "fj", "boards": [{"board": "fb", "location": NOMINAL, "check_fiducials": True}]})
        c.rpc = rpc
        yield c


def test_locate_one_fiducial(api):
    api.rpc("machine.enable")
    real = board_to_machine(REAL, FIDS[0])
    nominal = board_to_machine(NOMINAL, FIDS[0])
    r = api.rpc("vision.locate_fiducial", x=nominal["x"], y=nominal["y"], diameter=1.0)
    assert r.status_code == 200, r.json()
    got = r.json()["result"]
    assert math.hypot(got["x"] - real["x"], got["y"] - real["y"]) < 0.05       # within 0.05 mm (1-2 px)
    assert got["diameter"] == pytest.approx(1.0, abs=0.1)
    r = api.rpc("vision.locate_fiducial", x=300, y=250)                          # nothing there
    assert r.status_code == 422 and r.json()["error"]["data"]["name"] == "vision_failed"


def test_job_corrects_the_board_from_its_fiducials(api):
    api.rpc("machine.enable")
    fit = api.rpc("job.check_fiducials", job_id="fj", board_index=0).json()["result"]
    assert fit["x"] == pytest.approx(150.6, abs=0.05) and fit["y"] == pytest.approx(99.6, abs=0.05)
    assert fit["rotation"] == pytest.approx(0.5, abs=0.1) and fit["error_mm"] < 0.1
    # the job itself checks before the board's first placement and places C1 where it really is
    api.rpc("job.load", job_id="fj")
    api.rpc("job.start")
    import time
    for _ in range(200):
        st = api.get('/api/machine/objects/query?objects={"job":null}').json()["result"]["status"]["job"]
        if st["state"] in ("finished", "paused", "error"):
            break
        time.sleep(0.05)
    assert st["state"] == "finished", st
    want = board_to_machine(REAL, {"x": 10, "y": 5})
    last = api.app.state.tinker.machine.motion.status()["last_move"]["requested"]
    assert math.hypot(last["x"] - want["x"], last["y"] - want["y"]) < 0.06
