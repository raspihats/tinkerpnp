"""Advanced camera calibration, as OpenPnP's (written from its behaviour, not its code): the machine moves a known
point through the camera's view along radial lines at two heights; a camera model is fitted to where it shows up:

- lens distortion (radial k1, k2; tangential only if asked, as OpenPnP),
- the camera's pose against the machine: tilt, rotation (incl. a mirror image), distance,
- so mm per pixel at any Z, and where the optical axis really is.

Frames are then corrected into a "virtual camera": a straight top view, +X right, +Y up, centred on the camera's
location, at `units_per_pixel` on the primary Z (the location's Z). Vision keeps using the plain
`location + (pixel - centre) * units_per_pixel` and is right across the whole image.

Up-looking machine cameras with the nozzle tip as the point (no calibration part needed). Down-looking cameras
(a calibration fiducial) come next.

The model is solved with OpenCV's calibrateCamera, the machine positions as the 3D reference points (one
non-planar view: the two heights), starting from a guess of the focal length (OpenPnP starts from 1000 px).
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any

from .errors import MachineError

log = logging.getLogger(__name__)


def _detect_tip(camera: Any, upp: tuple[float, float], bore: float) -> tuple[float, float] | None:
    """The nozzle tip's bore in the raw (crop-only) frame, sub-pixel; None if not found or ambiguous."""
    from .calibration import TIP_PIPELINE
    from .vision import run_pipeline
    frame = camera.capture()                   # light, settle, a fresh frame
    # all candidates (ambiguous = no point); the hole is dark against the bright tip, not against the median: most of
    # the view can be dark background
    stages = [({**s, "against": 99} if s.get("stage") == "threshold" else s) for s in TIP_PIPELINE
              if s.get("stage") != "nearest"]
    image = frame
    result = run_pipeline(image, stages, upp, {"diameter": bore})
    dets = [d for d in result["detections"] if _bore_in_tip(image, d, upp)]
    if len(dets) != 1:
        return None
    return float(dets[0]["x"]), float(dets[0]["y"])


def _bore_in_tip(image: Any, det: dict, upp: tuple[float, float]) -> bool:
    """A bore is a dark hole in the bright tip: the ring around it must be much brighter than the hole. Rejects
    dark specks in the background, which the tip pipeline also finds (it keeps the one nearest the centre)."""
    import cv2
    import numpy as np
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    ux = (upp[0] + upp[1]) / 2
    r = max(2.0, det["d_mm"] / 2 / ux)
    h, w = gray.shape[:2]
    x, y = det["x"], det["y"]
    x0, x1, y0, y1 = int(max(0, x - 3 * r)), int(min(w, x + 3 * r + 1)), int(max(0, y - 3 * r)), int(min(h, y + 3 * r + 1))
    if x1 - x0 < 3 or y1 - y0 < 3:
        return False
    yy, xx = np.mgrid[y0:y1, x0:x1]
    dd = np.hypot(xx - x, yy - y)
    patch = gray[y0:y1, x0:x1].astype(np.float32)
    inner = patch[dd <= 0.7 * r]
    ring = patch[(dd >= 1.5 * r) & (dd <= 2.5 * r)]
    if not inner.size or not ring.size:
        return False
    return float(ring.mean()) - float(inner.mean()) > 80


def calibrate_up_camera(machine: Any, camera: Any, nozzle: Any, bore: float = 0.6,
                        progress: Any = None) -> dict:
    """On the motion thread. The nozzle (empty, tip in focus at the camera's location) walks radial lines through the
    view at the primary Z (the location's Z) and the secondary Z (+ secondary_z_offset). Returns the model (not
    saved). Needs the camera's location and a rough units_per_pixel (Measure first)."""
    import numpy as np
    s = camera.settings
    cal = s.calibration
    if s.looking != "up" or camera.head is not None:
        raise MachineError("invalid_params", f"{camera.name}: advanced calibration works on machine-mounted, "
                                             "up-looking cameras so far")
    loc = s.location
    if loc is None or None in (loc.x, loc.y, loc.z):
        raise MachineError("invalid_params", f"{camera.name}: set its location first (X, Y and the Z where the tip "
                                             "is in focus)")
    if s.units_per_pixel is None:
        raise MachineError("invalid_params", f"{camera.name}: measure units_per_pixel first (a rough value is "
                                             "enough)")
    if nozzle.status().get("part"):
        raise MachineError("busy", f"{nozzle.name} holds a part")
    upp = (abs(s.units_per_pixel.x), abs(s.units_per_pixel.y))
    planner = machine.planner
    zs = [loc.z, loc.z + cal.secondary_z_offset]
    obj: list[list[float]] = []
    img: list[list[float]] = []
    size: tuple[int, int] | None = None
    camera.acquire()
    camera._raw_frames = True
    try:
        got = camera.wait_frame(camera._seq, 5.0)
        if got is None:
            raise MachineError("driver_error", f"{camera.name}: no frame")
        h, w = got[1].shape[:2]
        size = (w, h)
        # how far out to go, in mm, if the image were square to the machine; the walk stops at the edge anyway
        reach = cal.fill * max(w * upp[0], h * upp[1]) / 2 * 1.5
        step = cal.fill * min(w * upp[0], h * upp[1]) / 2 / 4
        planner.move_to(nozzle, {"x": loc.x, "y": loc.y, "z": zs[0]})         # safe Z, over, down into focus
        for zi, z in enumerate(zs):
            planner.move_to(nozzle, {"x": loc.x, "y": loc.y}, safe_z="none")
            planner.move_to(nozzle, {"z": z}, safe_z="none")
            centre = _detect_tip(camera, upp, bore)
            if centre is None:
                raise MachineError("vision_failed", f"{camera.name}: the nozzle tip isn't found at the camera's "
                                                    f"location (Z {z:g}); is it centred and in focus?")
            here = planner.location(nozzle)
            obj.append([here["x"] - loc.x, here["y"] - loc.y, here["z"] - zs[0]])
            img.append(list(centre))
            for i in range(cal.radial_lines):
                a = 2 * math.pi * i / cal.radial_lines + (math.pi / cal.radial_lines if zi else 0.0)
                k = 1
                while k * step <= reach:
                    target = {"x": loc.x + k * step * math.cos(a), "y": loc.y + k * step * math.sin(a)}
                    try:
                        planner.move_to(nozzle, target, safe_z="none")      # within the camera's view, at depth
                    except MachineError as exc:
                        if exc.name == "soft_limit":
                            break
                        raise
                    p = _detect_tip(camera, upp, bore)
                    if p is None:
                        break
                    if abs(p[0] - w / 2) > cal.fill * w / 2 or abs(p[1] - h / 2) > cal.fill * h / 2:
                        break
                    here = planner.location(nozzle)
                    obj.append([here["x"] - loc.x, here["y"] - loc.y, here["z"] - zs[0]])
                    img.append(list(p))
                    k += 1
                if progress:
                    progress((zi * cal.radial_lines + i + 1) / (2 * cal.radial_lines))
                planner.move_to(nozzle, {"x": loc.x, "y": loc.y}, safe_z="none")
        planner.move_to(nozzle, {"x": loc.x, "y": loc.y, "z": zs[0]}, safe_z="none")
    finally:
        camera._raw_frames = False
        camera.release()
    if len(obj) < 30:
        raise MachineError("vision_failed", f"{camera.name}: only {len(obj)} points found; is the tip visible and in "
                                            "focus over the camera?", result={"points": len(obj)})
    model = solve(np.array(obj, np.float64), np.array(img, np.float64), size, upp, cal.tangential)
    model.update({"camera": camera.id, "nozzle": nozzle.id, "location": {"x": loc.x, "y": loc.y, "z": zs[0]},
                  "secondary_z": zs[1], "time": time.time()})
    return model


def _detect_fiducial(camera: Any, upp: tuple[float, float], diameter: float) -> tuple[float, float] | None:
    """The calibration fiducial in the raw (crop-only) frame; None if not found or ambiguous."""
    from .vision import ROUND_FIDUCIAL, run_pipeline
    frame = camera.capture()
    stages = [s for s in ROUND_FIDUCIAL if s.get("stage") != "nearest"]
    dets = run_pipeline(frame, stages, upp, {"diameter": diameter})["detections"]
    if len(dets) != 1:
        return None
    return float(dets[0]["x"]), float(dets[0]["y"])


def calibrate_down_camera(machine: Any, camera: Any, progress: Any = None) -> dict:
    """On the motion thread. A head camera walks so that the primary calibration fiducial (and then the secondary
    one, at another height) appears along radial lines through its view, as OpenPnP's advanced calibration of a
    down-looking camera. The model is relative to the camera, so it holds wherever the head is."""
    import numpy as np
    s = camera.settings
    cal = s.calibration
    if s.looking != "down" or camera.head is None:
        raise MachineError("invalid_params", f"{camera.name}: needs a head-mounted, down-looking camera")
    if cal.fiducial is None or cal.secondary_fiducial is None:
        raise MachineError("invalid_params", f"{camera.name}: set calibration.fiducial and "
                                             "calibration.secondary_fiducial (two marks at different heights)")
    if abs(cal.secondary_fiducial.z - cal.fiducial.z) < 0.5:
        raise MachineError("invalid_params", "the secondary fiducial must be at least 0.5 mm higher or lower")
    if s.units_per_pixel is None:
        raise MachineError("invalid_params", f"{camera.name}: set a rough units_per_pixel first")
    upp = (abs(s.units_per_pixel.x), abs(s.units_per_pixel.y))
    planner = machine.planner
    zp = cal.fiducial.z
    obj: list[list[float]] = []
    img: list[list[float]] = []
    camera.acquire()
    camera._raw_frames = True
    try:
        got = camera.wait_frame(camera._seq, 5.0)
        if got is None:
            raise MachineError("driver_error", f"{camera.name}: no frame")
        h, w = got[1].shape[:2]
        reach = cal.fill * max(w * upp[0], h * upp[1]) / 2 * 1.5
        step = cal.fill * min(w * upp[0], h * upp[1]) / 2 / 4
        for zi, fid in enumerate((cal.fiducial, cal.secondary_fiducial)):
            planner.move_to(camera, {"x": fid.x, "y": fid.y}, safe_z="auto" if zi == 0 else "none")
            centre = _detect_fiducial(camera, upp, fid.diameter)
            if centre is None:
                raise MachineError("vision_failed", f"{camera.name}: no fiducial found at X {fid.x:g} Y {fid.y:g}")

            def add(p: tuple[float, float]) -> None:
                here = camera.compute_location()
                obj.append([fid.x - here["x"], fid.y - here["y"], fid.z - zp])
                img.append(list(p))
            add(centre)
            for i in range(cal.radial_lines):
                a = 2 * math.pi * i / cal.radial_lines + (math.pi / cal.radial_lines if zi else 0.0)
                k = 1
                while k * step <= reach:
                    # the camera moves the other way, so the mark shows k steps out along the line
                    try:
                        planner.move_to(camera, {"x": fid.x - k * step * math.cos(a),
                                                 "y": fid.y - k * step * math.sin(a)}, safe_z="none")
                    except MachineError as exc:
                        if exc.name == "soft_limit":
                            break
                        raise
                    p = _detect_fiducial(camera, upp, fid.diameter)
                    if p is None or abs(p[0] - w / 2) > cal.fill * w / 2 or abs(p[1] - h / 2) > cal.fill * h / 2:
                        break
                    add(p)
                    k += 1
                if progress:
                    progress((zi * cal.radial_lines + i + 1) / (2 * cal.radial_lines))
            planner.move_to(camera, {"x": fid.x, "y": fid.y}, safe_z="none")
    finally:
        camera._raw_frames = False
        camera.release()
    if len(obj) < 30:
        raise MachineError("vision_failed", f"{camera.name}: only {len(obj)} points found", result={"points": len(obj)})
    model = solve(np.array(obj, np.float64), np.array(img, np.float64), (w, h), upp, cal.tangential)
    model.update({"camera": camera.id, "location": {"z": zp}, "secondary_z": cal.secondary_fiducial.z,
                  "time": time.time()})
    return model


def calibrate_camera(machine: Any, camera: Any, nozzle: Any | None = None) -> dict:
    """Up-looking machine camera: with the nozzle tip; down-looking head camera: with the calibration fiducials."""
    if camera.settings.looking == "down":
        return calibrate_down_camera(machine, camera)
    if nozzle is None:
        raise MachineError("invalid_params", "an up-looking camera is calibrated with a nozzle: give the nozzle")
    return calibrate_up_camera(machine, camera, nozzle)


def solve(obj: Any, img: Any, size: tuple[int, int], upp_guess: tuple[float, float],
          tangential: bool = False) -> dict:
    """Fit the camera model to machine points (relative to the location, mm) and their pixels. Returns the model
    as plain data (JSON for the state store)."""
    import cv2
    import numpy as np
    w, h = size
    # k3 fixed: with points out to 90 % of the view it only overfits, and blows up in the corners beyond them
    flags = cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_ASPECT_RATIO | cv2.CALIB_FIX_K3
    if not tangential:
        flags |= cv2.CALIB_ZERO_TANGENT_DIST

    def fit(o: Any, i: Any) -> tuple:
        best = None
        for distance in (20.0, 40.0, 80.0, 160.0):         # focal length guesses (OpenPnP starts from 1000 px)
            f0 = distance / ((upp_guess[0] + upp_guess[1]) / 2)
            k0 = np.array([[f0, 0, w / 2], [0, f0, h / 2], [0, 0, 1]], np.float64)
            try:
                r = cv2.calibrateCamera([o.astype(np.float32)], [i.astype(np.float32).reshape(-1, 1, 2)], (w, h),
                                        k0, np.zeros(5), flags=flags)
            except cv2.error:
                continue
            if best is None or r[0] < best[0]:
                best = r
        if best is None:
            raise MachineError("vision_failed", "the camera model didn't converge")
        return best

    rms, k, dist, rvecs, tvecs = fit(obj, img)
    # outliers (a misdetection): drop points far off the model, fit again (as OpenPnP)
    proj, _ = cv2.projectPoints(obj, rvecs[0], tvecs[0], k, dist)
    err = np.linalg.norm(proj.reshape(-1, 2) - img, axis=1)
    keep = err <= max(3 * float(np.median(err)), 1.0)
    outliers = int((~keep).sum())
    if outliers and keep.sum() >= 30:
        rms, k, dist, rvecs, tvecs = fit(obj[keep], img[keep])
    r, _ = cv2.Rodrigues(rvecs[0])
    t = tvecs[0].reshape(3)
    centre = -r.T @ t                                     # the camera's optical centre, relative to the location
    axis = r.T @ np.array([0.0, 0.0, 1.0])                # its optical axis in machine coordinates
    if axis[2] < 0:
        axis = -axis
    tilt = math.degrees(math.acos(min(1.0, abs(axis[2]))))
    s_ = -centre[2] / axis[2]                              # where the axis meets the primary Z plane
    axis_at = centre + s_ * axis
    distance = abs(s_)
    f = float(k[0, 0])
    upp = distance / f
    # rotation of the image against the machine: where machine +X goes in the image
    x_img = r @ np.array([1.0, 0.0, 0.0])
    rotation = math.degrees(math.atan2(x_img[1], x_img[0]))
    # mirror image: in a straight top view (+X right, +Y up; image rows go down) X x Y points out of the image
    y_img = r @ np.array([0.0, 1.0, 0.0])
    mirrored = bool(x_img[0] * y_img[1] - x_img[1] * y_img[0] > 0)
    return {
        "image_size": [w, h],
        "camera_matrix": k.tolist(),
        "distortion": [float(v) for v in dist.reshape(-1)[:5]],
        "rvec": [float(v) for v in rvecs[0].reshape(3)],
        "tvec": [float(v) for v in t],
        "rms_px": round(float(rms), 4),
        "points": int(len(obj)),
        "outliers": outliers,
        "distance_mm": round(distance, 3),
        "camera_height": round(float(centre[2]), 4),        # signed: above (+) or below (-) the primary Z
        "tilt_deg": round(tilt, 3),
        "rotation_deg": round(rotation, 3),
        "mirrored": mirrored,
        "axis_offset": {"x": round(float(axis_at[0]), 4), "y": round(float(axis_at[1]), 4)},
        "units_per_pixel": round(upp, 7),
    }


def units_per_pixel_at(model: dict, z: float) -> float:
    """mm per pixel of the virtual camera for a point at machine Z (3D units per pixel, as OpenPnP's): it scales
    with the distance from the camera (below an up-looking camera's view, above a down-looking one's)."""
    primary = model["location"]["z"]
    h = model.get("camera_height") or -model["distance_mm"]
    return model["units_per_pixel"] * abs(h - (z - primary)) / abs(h)


def rectify_maps(model: dict, size: tuple[int, int]) -> tuple[Any, Any]:
    """cv2.remap tables: virtual pixel -> raw pixel. The virtual camera looks straight down the machine Z at the
    location, +X right, +Y up, at units_per_pixel on the primary Z plane."""
    import cv2
    import numpy as np
    w, h = size
    k = np.array(model["camera_matrix"], np.float64)
    dist = np.array(model["distortion"], np.float64)
    rvec = np.array(model["rvec"], np.float64)
    tvec = np.array(model["tvec"], np.float64)
    upp = model["units_per_pixel"]
    # calibrated on another image size (a different crop): scale the camera matrix
    cw, ch = model.get("image_size", [w, h])
    if (cw, ch) != (w, h):
        k = k.copy()
        k[0] *= w / cw
        k[1] *= h / ch
    us, vs = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
    pts = np.stack([(us - w / 2) * upp, -(vs - h / 2) * upp, np.zeros_like(us)], axis=-1).reshape(-1, 3)
    proj, _ = cv2.projectPoints(pts, rvec, tvec, k, dist)
    proj = proj.reshape(h, w, 2).astype(np.float32)
    return cv2.convertMaps(proj[..., 0], proj[..., 1], cv2.CV_16SC2)
