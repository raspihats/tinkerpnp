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
    """A frame taken after the move has finished and the image settled."""
    time.sleep(settle)
    got = camera.wait_frame(camera._seq, 5.0)
    if got is None:
        raise MachineError("driver_error", f"{camera.name}: no frame")
    return got[1]


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


def measure_units_per_pixel(machine: Any, camera: Any, nozzle: Any, distance: float = 1.0, settle: float = 0.4,
                            template_size: float = 0.15, min_score: float = 0.6, linearity: float = 0.15,
                            takeup: float = 0.3) -> dict:
    """Run on the motion thread. The nozzle must be over the camera and in focus; it ends where it started."""
    if camera.settings.looking != "up" or camera.head is not None:
        raise MachineError("invalid_params", f"{camera.name}: only machine-mounted, up-looking cameras")
    if not 0.05 <= distance <= 10:
        raise MachineError("invalid_params", "distance must be 0.05..10 mm")
    planner = machine.planner
    start = planner.location(nozzle)
    camera.acquire()
    try:
        shifts, points = {}, {}
        for axis in ("x", "y"):
            # backlash: arrive at the start moving in the direction of the measurement, so the slack is taken up
            # before the reference picture (seen on the bench: ~0.05 mm in X)
            planner.move_to(nozzle, {"x": start["x"], "y": start["y"], axis: start[axis] - takeup}, safe_z="none")
            planner.move_to(nozzle, {"x": start["x"], "y": start["y"]}, safe_z="none")
            reference = _frame_after_move(camera, settle)
            h, w = reference.shape[:2]
            pts = []
            for d in (distance / 2, distance):
                planner.move_to(nozzle, {axis: start[axis] + d}, safe_z="none")
                moved = _frame_after_move(camera, settle)
                dx, dy, score = _shift(reference, moved, template_size)
                if score < min_score:
                    planner.move_to(nozzle, {"x": start["x"], "y": start["y"]}, safe_z="none")
                    raise MachineError("vision_failed", f"{camera.name}: couldn't find the nozzle again after moving "
                                                        f"{axis.upper()} {d:g} mm (match {score:.2f}); is it in focus "
                                                        "and in the middle of the image?",
                                       result={"axis": axis, "score": round(score, 3)})
                pts.append((d, dx, dy, score))
            planner.move_to(nozzle, {"x": start["x"], "y": start["y"]}, safe_z="none")
            (d1, x1, y1, s1), (d2, x2, y2, s2) = pts
            l1, l2 = math.hypot(x1, y1), math.hypot(x2, y2)
            if l1 < 1 or abs(l2 / l1 - d2 / d1) > linearity * d2 / d1:
                raise MachineError("vision_failed", f"{camera.name}: the image moved {l1:.1f} px for {d1:g} mm but "
                                                    f"{l2:.1f} px for {d2:g} mm in {axis.upper()} (not proportional): "
                                                    "something blurry may be matched; is the nozzle in focus?",
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
            "pixels_for_x": [round(xx, 2), round(xy, 2)],
            "pixels_for_y": [round(yx, 2), round(yy, 2)],
            "match": [round(sx, 3), round(sy, 3)],
            "points": points,                 # [mm, px x, px y] for half and full distance, per axis
            "image_size": [w, h],
            "field_of_view_mm": [round(w * distance / len_x, 2), round(h * distance / len_y, 2)],
            "rotation_deg": round(math.degrees(math.atan2(-xy, xx)), 2),
            "mirrored_x": bool(xx < 0),
            "mirrored_y": bool(yy > 0),
        },
    }
