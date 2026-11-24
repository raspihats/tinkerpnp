import os
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from tinkerpnp.api.server import create_app
from tinkerpnp.core import cameras
from tinkerpnp.core.cameras import DeviceSelector, V4l2CameraSettings, list_devices, resolve_device
from tinkerpnp.core.errors import MachineError
from tinkerpnp.core.objects import ImageTransform

from test_motion import simulated


@pytest.fixture
def m():
    machine = simulated()
    yield machine
    machine.close()


def run(m, fn, *args):
    return m.submit(fn, *args).result(timeout=10)


def test_down_camera_sees_the_bed_move(m):
    cam = m.get("camera", "top")
    _, a = cam.snapshot()
    assert a.shape == (480, 640, 3)
    run(m, m.enable)
    run(m, m.planner.move_to, m.get("nozzle", "N1"), {"x": 120, "y": 60})
    _, b = cam.snapshot()
    assert np.abs(a.astype(int) - b.astype(int)).mean() > 1          # a different part of the bed


def test_feeder_part_is_visible_under_the_camera(m):
    run(m, m.enable)
    cam = m.get("camera", "top")
    run(m, m.planner.move_to, cam, {"x": 237.9, "y": 119.5})          # F1's pick location
    _, img = cam.snapshot()
    centre = img[235:245, 315:325]
    assert centre.mean() < 60                                         # the dark part body


def test_up_camera_sees_the_nozzle(m):
    run(m, m.enable)
    cam = m.get("camera", "bottom")
    run(m, m.planner.move_to, m.get("nozzle", "N1"), {"x": 189.9, "y": 49.5, "z": 38.0})
    _, over = cam.snapshot()
    assert over[240, 320].mean() < 40 and over[240, 340].mean() > 180  # the dark bore in the middle, the bright tip
    run(m, m.planner.move_to, m.get("nozzle", "N1"), {"x": 50, "y": 49.5})
    _, away = cam.snapshot()
    assert away[240, 320].mean() < 30


def test_transform_and_overlays(m):
    cam = m.get("camera", "top")
    cam.settings.transform = ImageTransform(rotate=90, crop={"x": 0, "y": 0, "width": 400, "height": 300})
    seq, img = cam.snapshot()
    assert img.shape == (400, 300, 3)
    jpeg = cam.jpeg(seq, img, ("crosshair", "scale", "grid", "info"))
    decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (400, 300, 3)
    assert cam.jpeg(seq, img, ("crosshair", "scale", "grid", "info")) is jpeg     # cached


def test_device_closes_after_the_last_viewer(m):
    cam = m.get("camera", "top")
    cam.settings.idle_close = 100
    cam.acquire()
    assert cam.wait_frame(0, 3) is not None
    assert cam.status()["viewers"] == 1
    cam.release()
    deadline = time.time() + 3
    while cam._running and time.time() < deadline:
        time.sleep(0.05)
    assert not cam._running and cam.status()["streaming"] is False
    cam.acquire()                                                     # and opens again
    assert cam.wait_frame(cam._seq, 3) is not None
    cam.release()


# ── device matching ────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_sys(tmp_path, monkeypatch):
    """Two USB cameras as the kernel shows them in sysfs, plus /dev links."""
    sysfs, dev, usb = tmp_path / "sys", tmp_path / "dev", tmp_path / "usb"
    (dev / "v4l" / "by-id").mkdir(parents=True)
    sysfs.mkdir()

    def camera(node, index, port, vendor, product, serial, name):
        usbdev = usb / port
        iface = usbdev / f"{port}:1.0"
        iface.mkdir(parents=True, exist_ok=True)
        for f, v in (("idVendor", vendor), ("idProduct", product), ("serial", serial), ("product", name)):
            (usbdev / f).write_text(v + "\n")
        d = sysfs / node
        d.mkdir()
        (d / "name").write_text(name + "\n")
        (d / "index").write_text(f"{index}\n")
        (d / "device").symlink_to(iface)
        (dev / node).write_text("")
    camera("video0", 0, "1-1.2", "0c45", "6340", "20131107", "Trust Webcam")
    camera("video1", 1, "1-1.2", "0c45", "6340", "20131107", "Trust Webcam")
    camera("video2", 0, "1-1.3", "0c45", "6366", "SN0001", "USB 2.0 Camera")
    (dev / "v4l" / "by-id" / "usb-Sonix_USB_2.0_Camera_SN0001-video-index0").symlink_to(dev / "video2")
    monkeypatch.setattr(cameras, "SYSFS", str(sysfs))
    monkeypatch.setattr(cameras, "DEV", str(dev))
    return dev


def test_devices_are_listed_with_usb_details(fake_sys):
    devs = list_devices()
    assert [(d["path"].rsplit("/", 1)[1], d["capture"], d["usb_id"], d["usb_serial"]) for d in devs] == [
        ("video0", True, "0c45:6340", "20131107"), ("video1", False, "0c45:6340", "20131107"),
        ("video2", True, "0c45:6366", "SN0001")]
    assert devs[2]["links"][0].endswith("usb-Sonix_USB_2.0_Camera_SN0001-video-index0")


def test_selectors_in_order(fake_sys):
    assert resolve_device(DeviceSelector(usb_serial="SN0001"))[0].endswith("video2")
    assert resolve_device(DeviceSelector(usb_serial="gone", usb_id="0c45:6340")) == (str(fake_sys / "video0"),
                                                                                       "usb_id")
    assert resolve_device(DeviceSelector(name="USB 2.0 Camera"))[1] == "name"
    with pytest.raises(MachineError) as e:
        resolve_device(DeviceSelector(usb_id="dead:beef"))
    assert "present:" in e.value.message and "0c45:6366" in e.value.message


# ── v4l2 capture path, fed from a video file ──────────────────────────────────────────────────────────────────

def test_v4l2_camera_reads_frames(m, tmp_path):
    video = str(tmp_path / "clip.avi")
    out = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*"MJPG"), 10, (320, 240))
    for i in range(5):
        out.write(np.full((240, 320, 3), 40 * i, np.uint8))
    out.release()
    cam = m.get("camera", "bottom")
    from tinkerpnp.core.cameras import V4l2Camera
    v4l = V4l2Camera("file", "v4l2", V4l2CameraSettings(
        looking="up", location={"x": 0, "y": 0}, device={"path": video},
        format={"width": 320, "height": 240, "fps": 10}, controls={"brightness": 10}), m)
    v4l.setup()
    try:
        _, frame = v4l.snapshot()
        assert frame.shape == (240, 320, 3)
        st = v4l.status()
        assert st["device"] == video and st["matched_by"] == "path"
        assert "brightness" in st["last_error"]                         # a file has no controls
    finally:
        v4l.close()
    assert cam is not None


# ── HTTP and API ───────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def api():
    with TestClient(create_app(simulated())) as c:
        yield c
        c.app.state.tinker.machine.close()


def test_snapshot_stream_and_api(api):
    r = api.get("/cameras/top/snapshot.jpg?overlay=crosshair")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert api.get("/cameras/nope/snapshot.jpg").status_code == 404

    cams = api.get("/api/camera/list").json()["result"]
    assert [(c["id"], c["looking"], c["head"]) for c in cams] == [("bottom", "up", None), ("top", "down", "H1")]

    snap = api.get("/api/camera/snapshot?id=bottom").json()["result"]
    img = api.get(snap["url"])
    assert img.status_code == 200 and img.content[:2] == b"\xff\xd8"
    assert api.get("/cameras/bottom/frames/999999.jpg").status_code == 404

    r = api.get("/cameras/top/stream.mjpg?fps=20&frames=3&overlay=crosshair")
    assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert r.content.count(b"--tinkerpnpframe\r\nContent-Type: image/jpeg") == 3

    r = api.post("/api/camera/set_controls", json={"id": "top", "controls": {"gain": 1}})
    assert r.status_code == 400                                       # simulated cameras have no controls


def test_warmup_drops_the_first_frames(m):
    cam = m.get("camera", "top")
    cam.settings.warmup = 400
    t0 = time.monotonic()
    cam.snapshot()
    assert time.monotonic() - t0 >= 0.4


def test_measure_units_per_pixel_with_the_nozzle(m):
    """The simulated bottom camera draws at 0.02 mm/px: moving N1 over it must measure that."""
    from tinkerpnp.core.calibration import measure_units_per_pixel
    run(m, m.enable)
    cam, n1 = m.get("camera", "bottom"), m.get("nozzle", "N1")
    run(m, m.planner.move_to, n1, {"x": 189.9, "y": 49.5, "z": 38.0})
    r = run(m, measure_units_per_pixel, m, cam, n1, 1.0, 0.15)
    assert r["value"]["x"] == pytest.approx(0.02, rel=0.03) and r["value"]["y"] == pytest.approx(0.02, rel=0.03)
    d = r["details"]
    assert not d["mirrored_x"] and not d["mirrored_y"] and abs(d["rotation_deg"]) < 1
    assert n1.compute_location()["x"] == pytest.approx(189.9)              # back where it started
    cam.settings.transform = ImageTransform(flip_x=True)                     # a mirrored camera is reported
    r = run(m, measure_units_per_pixel, m, cam, n1, 1.0, 0.15)
    assert r["details"]["mirrored_x"] and not r["details"]["mirrored_y"]


def test_units_per_pixel_through_the_api(api):
    api.post("/api/machine/enable")
    api.post("/api/motion/move_to", json={"object": "nozzle/N1", "location": {"x": 189.9, "y": 49.5, "z": 38.0}})
    r = api.post("/api/config/capture", json={"field": "cameras.bottom.units_per_pixel", "from": "units_per_pixel",
                                               "args": {"camera": "bottom", "object": "nozzle/N1", "distance": 0.5}})
    assert r.status_code == 200, r.text
    v = r.json()["result"]
    assert v["value"]["x"] == pytest.approx(0.02, rel=0.05) and v["details"]["mirrored_x"] is False


def test_frames_are_decoded_only_on_demand(m):
    """An open camera that nobody watches keeps reading the device but doesn't decode (or draw) frames."""
    cam = m.get("camera", "top")
    cam.settings.idle_close = 60000                       # stays open after the viewer leaves
    cam.settings.stream_fps = 50
    cam.acquire()
    cam.wait_frame(0, 3)
    time.sleep(0.5)
    watched = cam._seq
    cam.release()
    time.sleep(0.6)
    seq = cam._seq
    time.sleep(0.6)
    assert cam._running and cam._seq == seq               # open, but no new frames without a viewer
    assert watched >= 3
    assert cam.snapshot()[0] > seq                        # a snapshot still gets a fresh one
