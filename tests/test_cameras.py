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
from tinkerpnp.core.objects import ImageTransform, Location

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


def test_transforms_mean_what_they_mean_in_openpnp(m):
    """flipX flips about the X axis (top-bottom), flipY about the Y axis (left-right); rotation is counter-clockwise,
    any angle, and the image grows to its bounding box. Settings copied from OpenPnP must look the same."""
    cam = m.get("camera", "top")
    img = np.zeros((40, 60, 3), np.uint8)
    img[0:10, 0:10] = 255                                      # a white square in the top-left corner
    t = lambda **kw: (setattr(cam.settings, "transform", ImageTransform(**kw)), cam.apply_transform(img))[1]  # noqa: E731
    assert t(flip_x=True)[35, 5].mean() == 255                 # now bottom-left
    assert t(flip_y=True)[5, 55].mean() == 255                 # now top-right
    r = t(rotate=90)                                           # counter-clockwise: top-left goes to bottom-left
    assert r.shape[:2] == (60, 40) and r[55, 5].mean() == 255
    assert t(rotate=-90)[5, 35].mean() == 255                  # clockwise: to top-right
    r = t(rotate=30)
    assert r.shape[:2] == (round(40 * np.cos(np.pi / 6) + 60 * 0.5), round(40 * 0.5 + 60 * np.cos(np.pi / 6)))


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


def test_selectors_must_all_fit(fake_sys):
    assert resolve_device(DeviceSelector(usb_serial="SN0001"))[0].endswith("video2")
    assert resolve_device(DeviceSelector(usb_id="0c45:6340")) == (str(fake_sys / "video0"), "usb_id")
    assert resolve_device(DeviceSelector(name="USB 2.0 Camera"))[1] == "name"
    with pytest.raises(MachineError):                  # serial of one camera, id of another: no camera fits both
        resolve_device(DeviceSelector(usb_serial="SN0001", usb_id="0c45:6340"))
    with pytest.raises(MachineError) as e:
        resolve_device(DeviceSelector(usb_id="dead:beef"))
    assert e.value.message.startswith("No camera with usb_id dead:beef") and "0c45:6366" in e.value.message


def test_the_port_decides_between_identical_cameras(fake_sys):
    """Two Sonix cameras with the same serial (SN0001): each config entry gets the one in its port, whichever the
    kernel lists first (the bench, 2026-11-25: a second one in port 2 was opened instead of the one in port 3)."""
    usb, sysfs = fake_sys.parent / "usb", fake_sys.parent / "sys"
    port0 = usb / "1-1.0" / "1-1.0:1.0"                 # the new camera, in a port that the kernel lists first
    port0.mkdir(parents=True)
    for f, v in (("idVendor", "0c45"), ("idProduct", "6366"), ("serial", "SN0001"), ("product", "USB 2.0 Camera")):
        (usb / "1-1.0" / f).write_text(v + "\n")
    (sysfs / "video00").mkdir()
    for f, v in (("name", "USB 2.0 Camera"), ("index", "0")):
        (sysfs / "video00" / f).write_text(v + "\n")
    (sysfs / "video00" / "device").symlink_to(port0)
    (fake_sys / "video00").write_text("")
    by_path = fake_sys / "v4l" / "by-path"
    by_path.mkdir()
    (by_path / "usb-0:3:1.0-video-index0").symlink_to(fake_sys / "video2")
    (by_path / "usb-0:2:1.0-video-index0").symlink_to(fake_sys / "video00")
    old = DeviceSelector(usb_serial="SN0001", usb_id="0c45:6366", path=str(by_path / "usb-0:3:1.0-video-index0"))
    new = DeviceSelector(usb_serial="SN0001", usb_id="0c45:6366", path=str(by_path / "usb-0:2:1.0-video-index0"))
    assert resolve_device(old) == (str(fake_sys / "video2"), "path+usb_serial+usb_id")
    assert resolve_device(new) == (str(fake_sys / "video00"), "path+usb_serial+usb_id")
    # unplugged from its port: with two that fit, guessing could swap them, so it's an error
    (by_path / "usb-0:3:1.0-video-index0").unlink()
    with pytest.raises(MachineError) as e:
        resolve_device(old)
    assert "None of the 2 matching cameras" in e.value.message


def test_a_unique_camera_is_found_in_another_port(fake_sys):
    sel = DeviceSelector(usb_serial="SN0001", path=str(fake_sys / "v4l" / "by-path" / "gone-video-index0"))
    assert resolve_device(sel) == (str(fake_sys / "video2"), "usb_serial (not at its path)")


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
    assert (cam.settings.location.x, cam.settings.location.y, cam.settings.location.z) == (189.9, 49.5, 38.0)
    run(m, m.planner.move_to, n1, {"x": 100, "y": 100})                     # somewhere else, up at safe Z
    r = run(m, measure_units_per_pixel, m, cam, n1, 1.0, 0.15)              # goes to the camera, down into focus
    assert r["value"]["x"] == pytest.approx(0.02, rel=0.03) and r["value"]["y"] == pytest.approx(0.02, rel=0.03)
    d = r["details"]
    assert not d["mirrored_x"] and not d["mirrored_y"] and abs(d["rotation_deg"]) < 1
    end = n1.compute_location()                                             # ends at the camera's location
    assert (end["x"], end["y"], end["z"]) == (pytest.approx(189.9), pytest.approx(49.5), pytest.approx(38.0, abs=0.2))
    assert r["details"]["at"] == {"x": 189.9, "y": 49.5, "z": 38.0}
    cam.settings.location = None                                             # no location: nothing to measure at
    with pytest.raises(MachineError):
        run(m, measure_units_per_pixel, m, cam, n1, 1.0, 0.15)
    cam.settings.location = Location(x=189.9, y=49.5, z=38.0)
    cam.settings.transform = ImageTransform(flip_y=True)    # left-right (OpenPnP's flipY): reported as mirrored X
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


def test_light_on_for_pictures_and_user_moves(m):
    """As OpenPnP's camera light actuator: on before vision's picture (and when the user moves the camera); off after
    it if asked; other cameras' lights off while this one takes a picture if they ask for it (anti-glare)."""
    run(m, m.enable)
    top, bottom = m.get("camera", "top"), m.get("camera", "bottom")
    light, other = m.get("actuator", "valve2"), m.get("actuator", "valve1")
    top.settings.light, bottom.settings.light = "valve2", "valve1"
    bottom.settings.light_off_for_other_cameras = True
    run(m, m.actuate, "valve1", True)
    top.settings.settle = 10
    top.acquire()
    try:
        run(m, top.capture)
    finally:
        top.release()
    assert light.status()["value"] is True and other.status()["value"] is False
    top.settings.light_off_after_capture = True
    top.acquire()
    try:
        run(m, top.capture)
    finally:
        top.release()
    assert light.status()["value"] is False
    m.submit(m.planner.jog, top, {"x": 1}).result(10)
    m.submit(m.planner.user_action, top, True).result(10)
    assert light.status()["value"] is True


def test_settle_by_motion_waits_until_the_image_stops(m):
    """OpenPnP's Motion settle method: pictures until two in a row barely differ."""
    cam = m.get("camera", "top")
    cam.settings.settle_method = "motion"
    cam.settings.noise = 0.5
    cam.settings.fps = 30
    run(m, m.enable)
    cam.acquire()
    try:
        t0 = time.monotonic()
        frame = run(m, cam.capture)
        assert frame is not None and time.monotonic() - t0 < cam.settings.settle_timeout / 1000.0
        cam.settings.settle_threshold = 0.0001                     # never still: the timeout takes the picture
        t0 = time.monotonic()
        run(m, cam.capture)
        assert time.monotonic() - t0 >= cam.settings.settle_timeout / 1000.0 - 0.05
    finally:
        cam.release()


def test_openpnp_transforms_scale_offset_white_balance_deinterlace(m):
    from tinkerpnp.core.objects import Gains, ImageSize, PixelOffset
    cam = m.get("camera", "top")
    img = np.zeros((40, 60, 3), np.uint8)
    img[:, :] = (100, 100, 100)
    img[0:10, 0:10] = (200, 200, 200)
    img[1::2] = 0                                              # odd lines dark: an interlaced field
    cam.settings.transform = ImageTransform(white_balance=Gains(red=1.2, green=1.0, blue=0.5))
    out = cam.apply_transform(img)
    assert tuple(out[20, 30]) == (50, 100, 120)                  # BGR: blue halved, red up
    cam.settings.transform = ImageTransform(scale=ImageSize(width=30, height=20))
    assert cam.apply_transform(img).shape == (20, 30, 3)
    cam.settings.transform = ImageTransform(offset=PixelOffset(x=5, y=3))
    assert cam.apply_transform(img)[3 + 4, 5 + 4].mean() == 200   # the corner square moved right 5, down 3
    cam.settings.transform = ImageTransform(deinterlace=True)
    assert cam.apply_transform(img)[1:39].min() > 0              # the dark field is gone


def test_a_camera_missing_from_its_port_is_reported_plainly(fake_sys):
    """Path only (the usual for two look-alike cameras) and nothing there: say so, list the USB cameras."""
    by_path = fake_sys / "v4l" / "by-path"
    by_path.mkdir()
    (by_path / "platform-x-usb-0:3:1.0-video-index0").symlink_to(fake_sys / "video2")
    with pytest.raises(MachineError) as e:
        resolve_device(DeviceSelector(path=str(by_path / "platform-x-usb-0:2:1.0-video-index0")))
    msg = e.value.message
    assert msg.startswith("No camera at USB port 2.") and "video2 (USB port 3, 0c45:6366, SN0001)" in msg
    assert "cameras fit" not in msg


def test_a_missing_camera_is_retried_while_someone_watches(m, monkeypatch):
    """Unplugged camera: the error shows, and it streams again by itself once it's back (no reload needed)."""
    cam = m.get("camera", "top")
    cam.settings.fps = 30
    real = cam.open_device
    state = {"present": False}

    def flaky():
        if not state["present"]:
            raise MachineError("not_found", "No camera at USB port 2.")
        real()
    monkeypatch.setattr(cam, "open_device", flaky)
    monkeypatch.setattr(cam, "_reopen_delay", lambda: 0.1)
    cam.acquire()
    try:
        time.sleep(0.3)
        assert cam.status()["last_error"].startswith("No camera") and not cam.status()["streaming"]
        state["present"] = True                                   # plugged in again
        assert cam.wait_frame(cam._seq, 3) is not None
        assert cam.status()["last_error"] is None and cam.status()["streaming"]
    finally:
        cam.release()


def test_measure_units_per_pixel_with_a_head_camera(m):
    """The head camera moves over the (simulated) bed: mm/px from the machine's own steps, no fiducial needed; a
    mirrored camera is reported the same way as for an up-looking one."""
    from tinkerpnp.core.calibration import measure_units_per_pixel
    run(m, m.enable)
    cam = m.get("camera", "top")
    cam.settings.sim_fiducials = [{"x": 151.3, "y": 100.6, "diameter": 1.0}, {"x": 149.2, "y": 99.1, "diameter": 0.7}]
    run(m, m.planner.move_to, cam, {"x": 150.3, "y": 100.2})
    r = m.submit(measure_units_per_pixel, m, cam, None, 1.0, 0.15).result(timeout=30)
    assert r["value"]["x"] == pytest.approx(0.04, rel=0.03) and r["value"]["y"] == pytest.approx(0.04, rel=0.03)
    d = r["details"]
    assert not d["mirrored_x"] and not d["mirrored_y"] and abs(d["rotation_deg"]) < 1
    here = cam.compute_location()
    assert (here["x"], here["y"]) == (pytest.approx(150.3), pytest.approx(100.2))     # back where it started
    cam.settings.transform = ImageTransform(flip_x=True)                              # top-bottom flipped
    r = m.submit(measure_units_per_pixel, m, cam, None, 1.0, 0.15).result(timeout=30)
    assert r["details"]["mirrored_y"] and not r["details"]["mirrored_x"]
