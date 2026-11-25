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
        rpc("data.parts.create", item={"id": "C0603", "height": 0.8})
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
    api.app.state.tinker.machine.config.machine.park = None     # the job parks at the end: keep C1's move the last
    assert api.rpc("job.start").status_code == 200
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


def test_nozzle_tip_runout_calibration_and_compensation(api):
    from tinkerpnp.core.calibration import TIP_PIPELINE, _image_to_offset
    from tinkerpnp.core.vision import run_pipeline
    m = api.app.state.tinker.machine
    bottom = m.get("camera", "bottom")
    bottom.settings.settle = 120
    bottom.settings.sim_runout = {"N1": {"runout": 0.15, "phase": 30}}
    api.rpc("machine.enable")
    r = api.rpc("nozzle.calibrate_tip", nozzle="N1", angles=12)
    assert r.status_code == 200, r.json()
    cal = r.json()["result"]
    assert cal["runout"] == pytest.approx(0.15, abs=0.02) and cal["phase"] == pytest.approx(30, abs=6)
    assert abs(cal["axis_offset"]["x"]) < 0.03 and cal["residual"] < 0.03 and cal["angles"] == 12
    assert api.get("/api/nozzle/tip_calibration?nozzle=N1").json()["result"]["runout"] == cal["runout"]
    assert m.get("nozzle", "N1").runout_offset(0) == (0.0, 0.0)        # as OpenPnP: off until enabled on the tip
    m.get("nozzle_tip", "NT1").settings.calibration.enabled = True
    # compensated: the tip itself now ends up over the camera centre at any angle
    for angle in (0, 90, -135):
        api.rpc("motion.move_to", object="nozzle/N1", location={"x": 189.9, "y": 49.5, "z": 38.0, "rotation": angle})
        seq, img = bottom.snapshot()
        det = run_pipeline(img, TIP_PIPELINE, (0.02, 0.02), {"diameter": 0.5})
        dx, dy = _image_to_offset(det["detections"][0], det["image_size"], (0.02, 0.02))
        assert math.hypot(dx, dy) < 0.04, (angle, dx, dy)
    api.rpc("nozzle.reset_tip_calibration", nozzle="N1")
    assert api.get("/api/nozzle/tip_calibration?nozzle=N1").json()["result"] is None


def test_tip_calibration_belongs_to_the_nozzle_and_resets_with_the_camera(api):
    """As OpenPnP: stored per tip and nozzle; reset when the up camera's view or the nozzle's offset changes."""
    from tinkerpnp.core.calibration import stale_tip_calibrations
    m = api.app.state.tinker.machine
    store = m.state_store
    n1, n2 = m.get("nozzle", "N1"), m.get("nozzle", "N2")
    store.state_set(n1.calibration_key("NT1"), {"runout": 0.1, "phase": 0, "nozzle": "N1"})
    n2.settings.tip = "NT1"                                   # the same tip on the other nozzle: not calibrated
    assert n1.runout()["runout"] == 0.1 and n2.runout() is None
    n2.settings.tip = "NT2"
    old = m.config.data
    cam_moved = {**old, "cameras": {**old["cameras"], "bottom": {**old["cameras"]["bottom"],
                                                                   "location": {"x": 190, "y": 49.5, "z": 38}}}}
    assert stale_tip_calibrations(old, cam_moved) == {"*"}
    assert stale_tip_calibrations(old, old) == set()
    r = api.rpc("config.update", changes=[{"op": "set", "path": "cameras.bottom.location.x", "value": 190}])
    assert r.status_code == 200, r.json()
    assert any("tip calibrations reset" in w for w in r.json()["result"]["warnings"])
    assert api.app.state.tinker.machine.get("nozzle", "N1").runout() is None


def test_tip_recalibration_after_homing(api):
    m = api.app.state.tinker.machine
    m.get("camera", "bottom").settings.settle = 120
    m.get("camera", "bottom").settings.sim_runout = {"N1": {"runout": 0.12, "phase": -40}}
    cal = m.get("nozzle_tip", "NT1").settings.calibration
    cal.enabled, cal.recalibrate = True, "machine_home"
    api.rpc("machine.enable")                                # enable homes: N1/NT1 is measured
    got = m.get("nozzle", "N1").runout()
    assert got is not None and got["runout"] == pytest.approx(0.12, abs=0.02)


def test_a_fiducial_3_5_mm_off_is_still_found(api):
    """As OpenPnP (4 mm search distance): a board put down by eye a few mm off is found; farther is refused."""
    api.rpc("machine.enable")
    real = board_to_machine(REAL, FIDS[0])
    r = api.rpc("vision.locate_fiducial", x=real["x"] + 3.5, y=real["y"], diameter=1.0)
    assert r.status_code == 200, r.json()
    assert math.hypot(r.json()["result"]["x"] - real["x"], r.json()["result"]["y"] - real["y"]) < 0.05


def test_three_fiducials_fit_scale_and_shear():
    """As OpenPnP with 3+ fiducials: an affine fit, so a PCB stretched by 0.3 % in X is placed right."""
    from tinkerpnp.core.jobs import board_to_machine as b2m
    board = [(f["x"], f["y"]) for f in FIDS] + [(2, 25)]
    stretch = lambda x, y: b2m(REAL, {"x": x * 1.003, "y": y})                     # noqa: E731
    real = [(p["x"], p["y"]) for p in (stretch(x, y) for x, y in board)]
    fit = fit_board(board, real)
    assert fit["scale_x"] == pytest.approx(1.003, abs=1e-5) and fit["scale_y"] == pytest.approx(1.0, abs=1e-5)
    assert abs(fit["shear"]) < 1e-6 and fit["error_mm"] < 1e-6
    got = b2m({**fit, "z": 2}, {"x": 40, "y": 10})
    want = stretch(40, 10)
    assert math.hypot(got["x"] - want["x"], got["y"] - want["y"]) < 1e-6
