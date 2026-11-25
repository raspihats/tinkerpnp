import math

import pytest

from tinkerpnp.core.calibration import TIP_PIPELINE, _image_to_offset
from tinkerpnp.core.camera_calibration import calibrate_up_camera, units_per_pixel_at
from tinkerpnp.core.errors import MachineError
from tinkerpnp.core.objects import Scale
from tinkerpnp.core.vision import run_pipeline

from test_pnp import m, run  # noqa: F401

LENS = {"distance": 25.0, "k1": -1.0, "k2": 0.5, "tilt_x": 1.5, "tilt_y": -1.0}    # strong barrel (fish-eye like)


@pytest.fixture
def cam(m):  # noqa: F811
    c = m.get("camera", "bottom")
    c.settings.sim_lens = dict(LENS)
    c.settings.fps = 30
    c.settings.settle = 40
    c.settings.noise = 1.0
    c.settings.calibration.radial_lines = 8
    run(m, m.enable)
    return c


def tip_offset(cam):
    """Where the tip shows in the (corrected) image, as machine mm from the camera's location."""
    upp = (cam.settings.units_per_pixel.x, cam.settings.units_per_pixel.y)
    import time
    cam.acquire()
    try:
        time.sleep(0.1)
        seq, img = cam.wait_frame(cam._seq, 5.0)          # a frame from after the move
    finally:
        cam.release()
    from tinkerpnp.core.camera_calibration import _bore_in_tip
    stages = [({**s, "against": 99} if s.get("stage") == "threshold" else s) for s in TIP_PIPELINE
              if s.get("stage") != "nearest"]
    det = run_pipeline(img, stages, upp, {"diameter": 0.6})
    found = [d for d in det["detections"] if _bore_in_tip(img, d, upp)]
    assert len(found) == 1, f"tip found {len(found)} times"
    return _image_to_offset(found[0], det["image_size"], upp)


def test_up_camera_calibration_recovers_the_lens(m, cam):  # noqa: F811
    """As OpenPnP's advanced calibration: the nozzle tip walks radial lines at two heights; the fitted model has the
    simulated lens (distortion, tilt, distance, mirror image) and the corrected picture is a true top view."""
    model = m.submit(calibrate_up_camera, m, cam, m.get("nozzle", "N1")).result(timeout=180)
    assert model["rms_px"] < 0.6 and model["points"] > 60
    assert model["distortion"][0] == pytest.approx(LENS["k1"], abs=0.3)      # k1/k2 trade off: geometry below
    assert model["tilt_deg"] == pytest.approx(math.hypot(1.5, 1.0), abs=0.4)
    assert model["distance_mm"] == pytest.approx(25, abs=2)
    assert model["units_per_pixel"] == pytest.approx(0.02, rel=0.03)
    assert model["mirrored"] is True                               # an up-looking camera sees a mirror image
    assert units_per_pixel_at(model, 40.0) > model["units_per_pixel"]  # farther away: more mm per pixel

    # without the model (only the mirror fixed, as before): far off at the edge
    cam.settings.transform.flip_x = True
    n1 = m.get("nozzle", "N1")
    loc = cam.settings.location
    run(m, m.planner.move_to, n1, {"x": loc.x + 5, "y": loc.y + 3.5, "z": loc.z})
    ox, oy = tip_offset(cam)
    assert math.hypot(ox - 5, oy - 3.5) > 0.1
    # switched on: the corrected picture puts the tip where the machine says, also off-centre
    m.state_store.state_set("camera_calibration/bottom", model)
    cam.settings.calibration.enabled = True
    cam.settings.units_per_pixel = Scale(x=model["units_per_pixel"], y=model["units_per_pixel"])
    assert cam.calibration_active()
    for dx, dy in ((0, 0), (4, 3), (-5, 2.5), (3.5, -3.5), (5, 3.5)):
        run(m, m.planner.move_to, n1, {"x": loc.x + dx, "y": loc.y + dy, "z": loc.z})
        ox, oy = tip_offset(cam)
        assert math.hypot(ox - dx, oy - dy) < 0.03, (dx, dy, ox, oy)


def test_calibration_needs_a_location_and_a_rough_scale(m, cam):  # noqa: F811
    cam.settings.units_per_pixel = None
    with pytest.raises(MachineError) as e:
        run(m, calibrate_up_camera, m, cam, m.get("nozzle", "N1"))
    assert "units_per_pixel" in e.value.message


def test_down_camera_calibration_with_two_fiducials(m):  # noqa: F811
    """As OpenPnP: the head camera walks over a primary calibration fiducial (board height) and a secondary one
    (3 mm higher); the corrected picture then places marks right off-centre, at either height (3D mm/px)."""
    from tinkerpnp.core.camera_calibration import calibrate_down_camera
    from tinkerpnp.core.objects import CalibrationFiducial
    from tinkerpnp.core.vision import ROUND_FIDUCIAL, to_machine
    import time
    cam = m.get("camera", "top")
    cam.settings.sim_lens = {"distance": 30.0, "k1": -0.6, "k2": 0.2, "tilt_x": -1.0, "tilt_y": 1.2, "z": 0.0}
    # away from the simulator's bright 50 mm grid lines (they'd merge with a mark)
    cam.settings.sim_fiducials = [{"x": 165, "y": 165, "z": 0.0, "diameter": 1.0},
                                  {"x": 265, "y": 165, "z": 3.0, "diameter": 1.0}]
    cam.settings.fps, cam.settings.settle, cam.settings.noise = 30, 40, 1.0
    cal = cam.settings.calibration
    cal.radial_lines = 8
    cal.fiducial = CalibrationFiducial(x=165, y=165, z=0.0, diameter=1.0)
    cal.secondary_fiducial = CalibrationFiducial(x=265, y=165, z=3.0, diameter=1.0)
    run(m, m.enable)
    model = m.submit(calibrate_down_camera, m, cam).result(timeout=180)
    assert model["rms_px"] < 0.6 and model["points"] > 60
    assert model["distance_mm"] == pytest.approx(30, abs=2) and model["mirrored"] is False
    assert model["tilt_deg"] == pytest.approx(math.hypot(1.0, 1.2), abs=0.4)

    m.state_store.state_set("camera_calibration/top", model)
    cal.enabled = True
    cam.settings.units_per_pixel = Scale(x=model["units_per_pixel"], y=model["units_per_pixel"])
    for fx, fy, fz in ((165, 165, 0.0), (265, 165, 3.0)):
        for dx, dy in ((7, 5), (-8, 4), (0, 0)):
            run(m, m.planner.move_to, cam, {"x": fx - dx, "y": fy - dy})     # the mark shows (dx, dy) off-centre
            cam.acquire()
            try:
                time.sleep(0.1)
                _, img = cam.wait_frame(cam._seq, 5.0)
            finally:
                cam.release()
            upp = cam.units_per_pixel_at(fz)
            det = run_pipeline(img, ROUND_FIDUCIAL, upp, {"diameter": 1.0})
            # the nearest to the centre is the mark here (the other one is 100 mm away)
            got = to_machine(cam.compute_location(), det["detections"][0], det["image_size"], upp)
            assert math.hypot(got["x"] - fx, got["y"] - fy) < 0.03, (fx, fz, dx, dy, got)
