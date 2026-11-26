"""Calibration helpers that measure something with the machine itself (used by config.capture).

units_per_pixel: with a nozzle in focus over an up-looking camera, move it a known distance in X and then in Y (at
the same height) and see how far the image moved. The area around the nozzle is found again by template matching
(OpenCV), so no calibration part is needed. Also reports whether the image is mirrored or rotated against the
machine axes.

Parallax (bench test 2026-11-23): the sharp nozzle tip moves ~42 px per mm in the image, the blurry nozzle body
behind it much less, because it is further from the lens. Matching a large area mixed the two and gave results that
depended on the distance (86 instead of 117 px for 1 mm on one camera). So the images are high-pass filtered (only
sharp, in-focus detail is matched), the template is small, and the nozzle is moved half the distance and the full
distance: the image must move twice as far the second time, or the measurement is refused.
"""
from __future__ import annotations

import math
import time
from typing import Any

from .errors import MachineError


def _frame_after_move(camera: Any, settle: float) -> Any:
    """A frame taken after the move has finished and the image settled (s; the camera's light on first)."""
    return camera.capture(settle * 1000.0)


def _gray(frame: Any) -> Any:
    import cv2
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame


def _sharp(gray: Any) -> Any:
    """High-pass: the image minus a strongly blurred copy. Out-of-focus parts (further away, moving less) vanish."""
    import cv2
    import numpy as np
    g = gray.astype(np.float32)
    return g - cv2.GaussianBlur(g, (0, 0), max(g.shape) / 60)


def _shift(reference: Any, moved: Any, template_size: float) -> tuple[float, float, float]:
    """Where the centre region of `reference` is in `moved`: (dx, dy) in pixels, and the match score (0..1)."""
    import cv2
    ref, mov = _sharp(_gray(reference)), _sharp(_gray(moved))
    h, w = ref.shape
    th, tw = int(h * template_size), int(w * template_size)
    y0, x0 = (h - th) // 2, (w - tw) // 2
    template = ref[y0:y0 + th, x0:x0 + tw]
    result = cv2.matchTemplate(mov, template, cv2.TM_CCOEFF_NORMED)
    # flat windows (no variation, e.g. black background) score 1.0 in OpenCV's normalized correlation: ignore them
    import numpy as np
    ones = np.ones_like(template)
    mean = cv2.matchTemplate(mov, ones, cv2.TM_CCORR) / ones.size
    mean_sq = cv2.matchTemplate(mov * mov, ones, cv2.TM_CCORR) / ones.size
    local_std = np.sqrt(np.maximum(mean_sq - mean * mean, 0))
    result[local_std < 0.05 * float(template.std()) + 1e-6] = -1.0
    _, score, _, (bx, by) = cv2.minMaxLoc(result)
    # sub-pixel: fit a parabola through the peak and its neighbours
    def refine(values: Any, i: int) -> float:
        if 0 < i < len(values) - 1:
            a, b, c = values[i - 1], values[i], values[i + 1]
            d = a - 2 * b + c
            if d != 0:
                return i + 0.5 * (a - c) / d
        return float(i)
    fx = refine(result[by, :], bx)
    fy = refine(result[:, bx], by)
    return float(fx - x0), float(fy - y0), float(score)      # plain floats: numpy types don't go into JSON


def orientation(shift_x: tuple[float, float], shift_y: tuple[float, float], transform: Any) -> dict:
    """How the picture is turned, and the transform that makes it right, from how far it moved (pixels, rows down)
    for +1 of machine X and of Y. In picture coordinates with y up, machine X shows as (xx, -xy) and Y as (yx, -yy):
    a mapping M. Upside down is a rotation by 180 degrees, not two mirrors: the turn is M's angle, and it is a mirror
    image only if M's determinant is negative (then one flip, left-right, is needed).

    The pipeline is P = F . R (rotate, then flip: CameraBase.apply_transform), so what is shown is P . S for the
    sensor's own S = P^-1 M. The new transform must give P' . S = scale . I, so P' = P . M^-1, orthogonalized and
    split into a rotation and at most one flip."""
    import numpy as np
    (xx, xy), (yx, yy) = shift_x, shift_y
    m = np.array([[xx, yx], [-xy, -yy]], float)
    det = float(np.linalg.det(m))
    mirrored = det < 0
    rotation = math.degrees(math.atan2(m[1, 0], m[0, 0]))       # where machine +X points in the picture
    if transform is not None:
        th = math.radians(float(transform.rotate or 0.0))
        r = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
        f = np.diag([-1.0 if transform.flip_y else 1.0, -1.0 if transform.flip_x else 1.0])
        p = f @ r
    else:
        p = np.eye(2)
    q = p @ np.linalg.inv(m)
    u, _, vt = np.linalg.svd(q)                                   # nearest rotation/reflection (scale removed)
    q = u @ vt
    flip_y = bool(np.linalg.det(q) < 0)
    if flip_y:
        q = np.diag([-1.0, 1.0]) @ q                              # Q = F . R with F a left-right flip
    rotate = math.degrees(math.atan2(q[1, 0], q[0, 0]))
    rotate = round(((rotate + 180.0) % 360.0) - 180.0, 2)
    if abs(rotate) < 0.005:
        rotate = 0.0
    current = (round((((float(transform.rotate or 0.0)) + 180.0) % 360.0) - 180.0, 2), bool(transform.flip_x),
               bool(transform.flip_y)) if transform is not None else (0.0, False, False)
    # the same picture can be had two ways (flip both = rotate 180): compare what they do, not the settings
    needed = abs(((rotation + 180.0) % 360.0) - 180.0) > 0.05 or mirrored
    return {"mirrored": mirrored, "rotation_deg": round(rotation, 2), "needs_correction": needed,
            "correction": {"rotate": rotate, "flip_x": False, "flip_y": flip_y}, "current": {
                "rotate": current[0], "flip_x": current[1], "flip_y": current[2]}}


def measure_units_per_pixel(machine: Any, camera: Any, nozzle: Any = None, distance: float = 1.0,
                            settle: float = 0.4, template_size: float = 0.15, min_score: float = 0.6,
                            linearity: float = 0.15, takeup: float = 0.3) -> dict:
    """Run on the motion thread. Moves by `distance` in X and in Y and measures how far the picture moved.

    Up-looking machine camera: as OpenPnP's camera calibration, the nozzle first goes to the camera's location (safe
    Z, then X/Y, then down to the focus Z there): measuring wherever the nozzle happens to be (e.g. up at safe Z,
    out of focus) matches whatever else moves with the head and gives a wrong but consistent-looking scale (bench,
    2026-11-25). It ends at the camera's location.

    Head camera (down-looking): the camera itself moves over whatever is under it (a board with some detail; no
    fiducial needed, unlike OpenPnP's Issues & Solutions), from where it is now, and comes back there. The board
    stands still, so its picture moves the other way: the sign is turned, so mirrored / rotated mean the same as for
    an up-looking camera (+X should show as right, +Y as up)."""
    if not 0.05 <= distance <= 10:
        raise MachineError("invalid_params", "distance must be 0.05..10 mm")
    planner = machine.planner
    if camera.head is not None:
        if camera.settings.looking != "down":
            raise MachineError("invalid_params", f"{camera.name}: head cameras are measured looking down")
        mover, sign, what = camera, -1.0, "the board"
        here = camera.compute_location()
        start = {"x": here["x"], "y": here["y"]}
    else:
        if camera.settings.looking != "up":
            raise MachineError("invalid_params", f"{camera.name}: machine-mounted cameras are measured looking up")
        if nozzle is None:
            raise MachineError("invalid_params", "an up-looking camera is measured with a nozzle: give the nozzle")
        loc = camera.settings.location
        if loc is None or None in (loc.x, loc.y, loc.z):
            raise MachineError("invalid_params", f"{camera.name}: set the camera's location (X, Y and the Z where "
                                                 "the nozzle tip is in focus) first: units per pixel are measured "
                                                 "there")
        mover, sign, what = nozzle, 1.0, "the nozzle"
        start = {"x": loc.x, "y": loc.y, "z": loc.z}
        planner.move_to(nozzle, start)           # safe Z first, then X/Y, then down into focus
    camera.acquire()
    try:
        shifts, points = {}, {}
        for axis in ("x", "y"):
            # backlash: arrive at the start moving in the direction of the measurement, so the slack is taken up
            # before the reference picture (seen on the bench: ~0.05 mm in X)
            planner.move_to(mover, {"x": start["x"], "y": start["y"], axis: start[axis] - takeup}, safe_z="none")
            planner.move_to(mover, {"x": start["x"], "y": start["y"]}, safe_z="none")
            reference = _frame_after_move(camera, settle)
            h, w = reference.shape[:2]
            pts = []
            for d in (distance / 2, distance):
                planner.move_to(mover, {axis: start[axis] + d}, safe_z="none")
                moved = _frame_after_move(camera, settle)
                dx, dy, score = _shift(reference, moved, template_size)
                dx, dy = sign * dx, sign * dy
                if score < min_score:
                    planner.move_to(mover, {"x": start["x"], "y": start["y"]}, safe_z="none")
                    raise MachineError("vision_failed", f"{camera.name}: couldn't find {what} again after moving "
                                                        f"{axis.upper()} {d:g} mm (match {score:.2f}); is it in focus, "
                                                        "with some detail in the middle of the picture?",
                                       result={"axis": axis, "score": round(score, 3)})
                pts.append((d, dx, dy, score))
            planner.move_to(mover, {"x": start["x"], "y": start["y"]}, safe_z="none")
            (d1, x1, y1, s1), (d2, x2, y2, s2) = pts
            l1, l2 = math.hypot(x1, y1), math.hypot(x2, y2)
            if l1 < 1 or abs(l2 / l1 - d2 / d1) > linearity * d2 / d1:
                raise MachineError("vision_failed", f"{camera.name}: the image moved {l1:.1f} px for {d1:g} mm but "
                                                    f"{l2:.1f} px for {d2:g} mm in {axis.upper()} (not proportional): "
                                                    f"something blurry may be matched; is {what} in focus?",
                                   result={"axis": axis, "pixels": [round(l1, 2), round(l2, 2)]})
            # least-squares line through the origin and both points
            k = d1 * d1 + d2 * d2
            shifts[axis] = ((d1 * x1 + d2 * x2) / k * distance, (d1 * y1 + d2 * y2) / k * distance, min(s1, s2))
            points[axis] = [[d1, round(x1, 2), round(y1, 2)], [d2, round(x2, 2), round(y2, 2)]]
        _frame_after_move(camera, 0.0)
    finally:
        camera.release()
    (xx, xy, sx), (yx, yy, sy) = shifts["x"], shifts["y"]
    len_x, len_y = math.hypot(xx, xy), math.hypot(yx, yy)
    if len_x < 3 or len_y < 3:
        raise MachineError("vision_failed", f"{camera.name}: the image moved only {len_x:.1f} / {len_y:.1f} px for "
                                            f"{distance} mm; use a larger distance")
    # with x right and y up in the image (y down in pixel rows), +X should move the image right, +Y up
    return {
        "value": {"x": round(distance / len_x, 6), "y": round(distance / len_y, 6)},
        "details": {
            "distance_mm": distance,
            "at": start,                      # where it was measured (and is now)
            "pixels_for_x": [round(xx, 2), round(xy, 2)],
            "pixels_for_y": [round(yx, 2), round(yy, 2)],
            "match": [round(sx, 3), round(sy, 3)],
            "points": points,                 # [mm, px x, px y] for half and full distance, per axis
            "image_size": [w, h],
            "field_of_view_mm": [round(w * distance / len_x, 2), round(h * distance / len_y, 2)],
            # the picture's orientation and the transform that puts it right (+X right, +Y up): see orientation()
            **orientation((xx, xy), (yx, yy), getattr(camera.settings, "transform", None)),
        },
    }


# ── nozzle tip runout ──────────────────────────────────────────────────────────────────────────────────────────────

TIP_PIPELINE = [
    {"stage": "gray"},
    {"stage": "blur", "mm": 0.03},
    {"stage": "threshold", "value": "dark", "invert": True},   # the bore: a hole much darker than the tip around it
    {"stage": "morph", "op": "open", "mm": 0.05},
    {"stage": "blobs", "min_round": 0.6, "min_factor": 0.1, "max_factor": 6.0},   # bores differ: 0.4 to 1 mm+
    {"stage": "nearest"},
]


def _image_to_offset(det: dict, size: list[int], upp: tuple[float, float]) -> tuple[float, float]:
    """Up-looking camera, convention after the transform: image right = +X, image up = +Y."""
    w, h = size
    return (det["x"] - w / 2) * upp[0], -(det["y"] - h / 2) * upp[1]


def fit_circle(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Least-squares circle (Kåsa): centre x, y and radius."""
    import numpy as np
    p = np.array(points, float)
    a = np.column_stack([2 * p[:, 0], 2 * p[:, 1], np.ones(len(p))])
    b = (p ** 2).sum(axis=1)
    (cx, cy, c), *_ = np.linalg.lstsq(a, b, rcond=None)
    return float(cx), float(cy), float(math.sqrt(max(c + cx * cx + cy * cy, 0.0)))


def calibrate_nozzle_tip(machine: Any, nozzle: Any, camera: Any, angles: int = 12, bore: float = 0.6,
                         settle: float | None = None) -> dict:
    """On the motion thread: the nozzle turns over an up-looking camera; the bore's track gives the runout.
    Needs the camera's location (incl. Z = focus) and units_per_pixel. Returns the calibration (not saved)."""
    from .vision import run_pipeline
    if camera.settings.looking != "up" or camera.head is not None or camera.settings.location is None:
        raise MachineError("invalid_params", f"{camera.name}: needs a machine-mounted, up-looking camera with a "
                                             "location")
    if camera.settings.units_per_pixel is None:
        raise MachineError("invalid_params", f"{camera.name}: measure units_per_pixel first")
    if not 6 <= angles <= 72:
        raise MachineError("invalid_params", "angles: 6..72")
    upp = (camera.settings.units_per_pixel.x, camera.settings.units_per_pixel.y)
    cam = camera.settings.location.as_dict()
    planner = machine.planner
    settle_s = (camera.settings.settle if settle is None else settle) / 1000.0
    # measure the plain axis: switch compensation off while calibrating
    state_key = nozzle.calibration_key()
    tip = machine.objects.get(f"nozzle_tip/{nozzle.tip_id()}") if nozzle.tip_id() else None
    tip_cal = tip.settings.calibration if tip is not None else None
    threshold = tip_cal.offset_threshold if tip_cal else 0.5
    allowed_misses = tip_cal.allow_misdetections if tip_cal else 0
    old = machine.state_store.state_get(state_key) if state_key else None
    if old:
        machine.state_store.state_set(state_key, {**old, "enabled": False})
    points, used = [], []
    camera.acquire()
    try:
        planner.move_to(nozzle, {"x": cam["x"], "y": cam["y"], "rotation": -180.0}, safe_z="auto")
        if "z" in cam:
            planner.move_to(nozzle, {"z": cam["z"]}, safe_z="none")
        for i in range(angles):
            angle = -180.0 + 360.0 * i / angles
            planner.move_to(nozzle, {"rotation": angle}, safe_z="none")
            frame = camera.capture(None if settle is None else settle_s * 1000.0)
            result = run_pipeline(frame, TIP_PIPELINE, upp, {"diameter": bore})
            found = [_image_to_offset(d, result["image_size"], upp) for d in result["detections"]]
            found = [f for f in found if math.hypot(*f) <= threshold]      # as OpenPnP: offset threshold
            if len(found) != 1:                                             # none, or ambiguous: discarded
                continue
            dx, dy = found[0]
            points.append((dx, dy))
            used.append(angle)
        planner.move_to_safe_z(nozzle.head)
    finally:
        camera.release()
        if old:
            machine.state_store.state_set(state_key, old)
    if len(points) < max(5, angles - allowed_misses):
        raise MachineError("vision_failed", f"found the tip in {len(points)} of {angles} pictures (allowed misses: "
                                            f"{allowed_misses}); is the nozzle in focus over the camera, with the "
                                            "bore visible, within the offset threshold?",
                           result={"found": len(points)})
    cx, cy, r = fit_circle(points)
    # phase: where the tip is (relative to the axis) at rotation 0, as an angle
    phases = [math.atan2(py - cy, px - cx) - math.radians(a) for (px, py), a in zip(points, used)]
    phase = math.degrees(math.atan2(sum(math.sin(p) for p in phases), sum(math.cos(p) for p in phases)))
    residual = max(abs(math.hypot(px - cx, py - cy) - r) for px, py in points)
    return {"tip": nozzle.tip_id(), "nozzle": nozzle.id, "camera": camera.id, "runout": round(r, 4),
            "phase": round(phase, 2), "axis_offset": {"x": round(cx, 4), "y": round(cy, 4)},
            "residual": round(residual, 4), "angles": len(used),
            "points": [[round(a, 1), round(px, 4), round(py, 4)] for (px, py), a in zip(points, used)],
            "time": time.time(), "enabled": True}


# ── when a tip calibration is no longer valid ──────────────────────────────────────────────────────────────────────

def stale_tip_calibrations(old: dict, new: dict) -> set[str]:
    """As OpenPnP, a runout calibration is reset when what it was measured with changes: the up-looking camera's
    view (location, transform, units per pixel) resets all of them, a nozzle's X/Y offset resets that nozzle's.
    `old` / `new`: plain configuration data. Returns nozzle ids ("*" = all)."""
    def up_cameras(cfg: dict) -> dict:
        return {k: {f: v.get(f) for f in ("location", "transform", "units_per_pixel", "device", "calibration")}
                for k, v in (cfg.get("cameras") or {}).items() if isinstance(v, dict) and v.get("looking") == "up"}

    def nozzle_offsets(cfg: dict) -> dict:
        out = {}
        for head in (cfg.get("heads") or {}).values():
            for nid, n in ((head or {}).get("nozzles") or {}).items():
                off = (n or {}).get("offset") or {}
                out[nid] = (off.get("x"), off.get("y"))
        return out

    if up_cameras(old) != up_cameras(new):
        return {"*"}
    a, b = nozzle_offsets(old), nozzle_offsets(new)
    return {nid for nid in a.keys() & b.keys() if a[nid] != b[nid]}


def reset_tip_calibrations(machine: Any, nozzle_ids: set[str]) -> list[str]:
    """Forget the runout calibrations of these nozzles ("*" = all). Returns the keys that were set."""
    reset = []
    tips = [t.id for t in machine.of_kind("nozzle_tip")]
    for n in machine.of_kind("nozzle"):
        if "*" not in nozzle_ids and n.id not in nozzle_ids:
            continue
        for tip in tips:
            for key in (n.calibration_key(tip), f"tip_calibration/{tip}"):
                cal = machine.state_store.state_get(key)
                if cal and (key != f"tip_calibration/{tip}" or cal.get("nozzle") == n.id):
                    machine.state_store.state_set(key, None)
                    reset.append(key)
        n.update(calibration=None)
    return reset
