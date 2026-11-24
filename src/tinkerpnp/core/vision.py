"""Vision: pipelines of image stages, and the fiducial locator built on them (docs/openpnp-gap.md, E1/D2).

A pipeline is a list of stages, each `{"stage": name, ...parameters}`, run on a camera image. Sizes are in mm and
converted with the camera's units_per_pixel, so one pipeline works at any scale. The result is a list of
detections in image pixels, which `to_machine` turns into machine coordinates for a head-mounted, down-looking
camera (image x right = +X, image y down = −Y).

Stages (in the spirit of OpenPnP's CvPipeline, written independently):
  gray                                  colour -> gray
  blur        {mm: 0.1}                 Gaussian blur
  threshold   {value: 128 | "otsu", invert: false}
  morph       {op: open|close|erode|dilate, mm: 0.1}
  circles     {min_mm, max_mm, sensitivity: 30}            Hough circles -> detections
  blobs       {min_mm2, max_mm2, min_round: 0.6}           contours of the binary image -> detections
  nearest     {}                                           keep the detection nearest to the image centre
"""
from __future__ import annotations

import math
import time
from typing import Any

from .errors import MachineError

ROUND_FIDUCIAL = [
    {"stage": "gray"},
    {"stage": "blur", "mm": 0.05},
    {"stage": "threshold", "value": "otsu"},
    {"stage": "morph", "op": "open", "mm": 0.1},
    {"stage": "blobs", "min_round": 0.7},
    {"stage": "nearest"},
]


def run_pipeline(image: Any, stages: list[dict], upp: tuple[float, float], params: dict | None = None) -> dict:
    """Run stages on an image. params fill in sizes (e.g. diameter for the fiducial pipeline)."""
    import cv2
    import numpy as np
    params = params or {}
    ux = (upp[0] + upp[1]) / 2
    img = image
    found: list[dict] = []
    for st in stages:
        name = st.get("stage")
        if name == "gray":
            if img.ndim == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        elif name == "blur":
            sigma = max(0.5, st.get("mm", 0.1) / ux)
            img = cv2.GaussianBlur(img, (0, 0), sigma)
        elif name == "threshold":
            v = st.get("value", "otsu")
            mode = cv2.THRESH_BINARY_INV if st.get("invert") else cv2.THRESH_BINARY
            if v == "otsu":
                _, img = cv2.threshold(img, 0, 255, mode | cv2.THRESH_OTSU)
            else:
                _, img = cv2.threshold(img, float(v), 255, mode)
        elif name == "morph":
            k = max(1, int(round(st.get("mm", 0.1) / ux)))
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
            op = {"open": cv2.MORPH_OPEN, "close": cv2.MORPH_CLOSE, "erode": cv2.MORPH_ERODE,
                  "dilate": cv2.MORPH_DILATE}[st.get("op", "open")]
            img = cv2.morphologyEx(img, op, kernel)
        elif name == "circles":
            d = params.get("diameter")
            min_mm = st.get("min_mm", d * 0.7 if d else 0.3)
            max_mm = st.get("max_mm", d * 1.3 if d else 5.0)
            gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            c = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=max(4, min_mm / ux),
                                 param1=100, param2=st.get("sensitivity", 30),
                                 minRadius=int(min_mm / ux / 2), maxRadius=int(math.ceil(max_mm / ux / 2)))
            found = [] if c is None else [{"x": float(x), "y": float(y), "d_mm": float(2 * r * ux)}
                                          for x, y, r in c[0]]
        elif name == "blobs":
            d = params.get("diameter")
            area = math.pi * (d / 2) ** 2 if d else None
            min_a = st.get("min_mm2", area * 0.5 if area else 0.05)
            max_a = st.get("max_mm2", area * 1.8 if area else 50.0)
            binary = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            found = []
            for cnt in contours:
                a_px = cv2.contourArea(cnt)
                a_mm = a_px * upp[0] * upp[1]
                if not min_a <= a_mm <= max_a:
                    continue
                perim = cv2.arcLength(cnt, True)
                roundness = 4 * math.pi * a_px / (perim * perim) if perim else 0
                if roundness < st.get("min_round", 0.6):
                    continue
                m = cv2.moments(cnt)
                found.append({"x": m["m10"] / m["m00"], "y": m["m01"] / m["m00"],
                              "d_mm": 2 * math.sqrt(a_mm / math.pi), "round": round(roundness, 3)})
        elif name == "nearest":
            h, w = image.shape[:2]
            found = sorted(found, key=lambda f: (f["x"] - w / 2) ** 2 + (f["y"] - h / 2) ** 2)[:1]
        else:
            raise MachineError("invalid_params", f"unknown vision stage {name!r}")
    h, w = image.shape[:2]
    return {"detections": found, "image_size": [w, h]}


def to_machine(camera_location: dict, det: dict, size: list[int], upp: tuple[float, float]) -> dict:
    w, h = size
    return {"x": camera_location["x"] + (det["x"] - w / 2) * upp[0],
            "y": camera_location["y"] - (det["y"] - h / 2) * upp[1]}


def _frame_after_settle(camera: Any) -> Any:
    time.sleep(camera.settings.settle / 1000.0)
    got = camera.wait_frame(camera._seq, 5.0)
    if got is None:
        raise MachineError("driver_error", f"{camera.name}: no frame")
    return got[1]


def locate_fiducial(machine: Any, camera: Any, near: dict, diameter: float = 1.0, tolerance: float = 0.03,
                    passes: int = 3, pipeline: list[dict] | None = None) -> dict:
    """On the motion thread: find the round fiducial nearest to `near` (machine X/Y) with a head camera."""
    upp_s = camera.settings.units_per_pixel
    if upp_s is None:
        raise MachineError("invalid_params", f"{camera.name}: set units_per_pixel first")
    if camera.head is None or camera.settings.looking != "down":
        raise MachineError("invalid_params", f"{camera.name}: needs a head-mounted, down-looking camera")
    upp = (upp_s.x, upp_s.y)
    target = {"x": near["x"], "y": near["y"]}
    camera.acquire()
    try:
        for attempt in range(passes):
            machine.planner.move_to(camera, target)
            image = _frame_after_settle(camera)
            result = run_pipeline(image, pipeline or ROUND_FIDUCIAL, upp, {"diameter": diameter})
            if not result["detections"]:
                raise MachineError("vision_failed", f"no fiducial found near X {near['x']:.2f} Y {near['y']:.2f}",
                                   result={"near": near, "pass": attempt + 1})
            det = result["detections"][0]
            found = to_machine(camera.compute_location(), det, result["image_size"], upp)
            off = math.hypot(found["x"] - target["x"], found["y"] - target["y"])
            target = found
            if off <= tolerance:
                break
        dist = math.hypot(target["x"] - near["x"], target["y"] - near["y"])
        if dist > max(3 * diameter, 2.0):
            raise MachineError("vision_failed", f"found a mark {dist:.2f} mm away from the expected place: "
                                                "probably not the fiducial", result={"near": near, "found": target})
        return {"x": round(target["x"], 4), "y": round(target["y"], 4), "diameter": round(det["d_mm"], 3),
                "passes": attempt + 1}
    finally:
        camera.release()


def fit_board(board_points: list[tuple[float, float]], machine_points: list[tuple[float, float]]) -> dict:
    """Rotation + translation (+ scale, reported) that maps board coordinates onto measured machine coordinates.
    2 points: exact; more: least squares (Umeyama/Procrustes without reflection)."""
    import numpy as np
    if len(board_points) < 2:
        raise MachineError("invalid_params", "need at least 2 fiducials")
    a = np.array(board_points, float)
    b = np.array(machine_points, float)
    ca, cb = a.mean(axis=0), b.mean(axis=0)
    a0, b0 = a - ca, b - cb
    h = a0.T @ b0
    angle = math.atan2(h[0, 1] - h[1, 0], h[0, 0] + h[1, 1])
    c, s = math.cos(angle), math.sin(angle)
    rot = np.array([[c, -s], [s, c]])
    scale = float(np.sum(b0 * (a0 @ rot.T)) / np.sum(a0 * a0)) if np.sum(a0 * a0) else 1.0
    t = cb - rot @ ca
    residuals = b - (a @ rot.T + t)
    return {"x": round(float(t[0]), 4), "y": round(float(t[1]), 4), "rotation": round(math.degrees(angle), 4),
            "scale": round(scale, 5), "error_mm": round(float(np.sqrt((residuals ** 2).sum(axis=1)).max()), 4)}
