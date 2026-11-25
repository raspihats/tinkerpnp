"""Camera types: `simulated` (draws what a camera would see) and `v4l2` (USB cameras through Video4Linux / OpenCV).

The capture machinery (thread, viewers, JPEG, overlays, transform) is in objects.CameraBase. See docs/cameras.md.
"""
from __future__ import annotations

import glob
import logging
import math
import os
import re
import shutil
import subprocess
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .errors import MachineError
from .objects import SKIPPED, CameraBase, CameraCommon, Nozzle
from .registry import registry
from .units import DurationMs

log = logging.getLogger("tinkerpnp.camera")

SYSFS = "/sys/class/video4linux"      # tests point these elsewhere
DEV = "/dev"


# ── simulated ──────────────────────────────────────────────────────────────────────────────────────────────────────

class SimulatedCameraSettings(CameraCommon):
    width: int = Field(640, ge=16)
    height: int = Field(480, ge=16)
    fps: float = Field(10.0, gt=0, le=60)
    scene: Literal["auto", "bed", "nozzles", "test"] = Field(
        "auto", description="bed: the machine bed under a down-looking camera (grid, feeders); nozzles: nozzles "
                            "above an up-looking camera; test: a test pattern; auto: by `looking`")
    focus_depth: float = Field(3.0, gt=0, description="Up-looking: mm of Z from `location.z` before nozzles blur")
    sim_runout: dict[str, dict] = Field(default_factory=dict,
                                        description="Up-looking: nozzle tip runout to draw, {nozzle id: {runout, "
                                                    "phase}} (to test tip calibration)")
    sim_fiducials: list[dict] = Field(default_factory=list,
                                      description="Down-looking: round copper marks drawn on the bed, "
                                                  "[{x, y, diameter}] in machine mm (to test fiducial vision)")
    noise: float = Field(2.0, ge=0, le=50, description="Sensor noise (standard deviation, 0..255 scale); 0 = none")


@registry.register("camera", "simulated", SimulatedCameraSettings, "Simulated camera",
                   "Draws what a camera at its location would see: the bed with feeders (looking down) or the "
                   "nozzles passing over it (looking up)")
class SimulatedCamera(CameraBase):

    def open_device(self) -> None:
        import cv2  # noqa: F401  (fail early with a clear error if OpenCV is missing)
        self._next = time.monotonic()

    def grab(self, decode: Any = True) -> Any:
        self._next += 1.0 / self.settings.fps
        delay = self._next - time.monotonic()
        if delay > 0:
            if self._stop.wait(delay):
                return None
        else:
            self._next = time.monotonic()
        if not (decode() if callable(decode) else decode):
            return SKIPPED                          # nobody looks: don't draw
        scene = self.settings.scene
        if scene == "auto":
            scene = "bed" if self.settings.looking == "down" else "nozzles"
        img = {"bed": self._bed, "nozzles": self._nozzles, "test": self._test}[scene]()
        if self.settings.noise:                     # sensor noise, like a real camera
            import numpy as np
            rng = self.__dict__.setdefault("_rng", np.random.default_rng(1))
            noise = rng.normal(0, self.settings.noise, img.shape)
            img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        return img

    def _px(self) -> tuple[float, float]:
        upp = self.settings.units_per_pixel
        return (upp.x, upp.y) if upp else (0.05, 0.05)

    def _to_img(self, x: float, y: float, cam: dict) -> tuple[int, int]:
        ux, uy = self._px()
        w, h = self.settings.width, self.settings.height
        return int(round(w / 2 + (x - cam.get("x", 0)) / ux)), int(round(h / 2 - (y - cam.get("y", 0)) / uy))

    def _bed(self) -> Any:
        import cv2
        import numpy as np
        w, h = self.settings.width, self.settings.height
        ux, uy = self._px()
        cam = self.compute_location() or {}
        img = np.full((h, w, 3), (28, 64, 34), np.uint8)          # green solder mask
        x0, x1 = cam.get("x", 0) - w / 2 * ux, cam.get("x", 0) + w / 2 * ux
        y0, y1 = cam.get("y", 0) - h / 2 * uy, cam.get("y", 0) + h / 2 * uy
        for step, color, width in ((1, (40, 84, 46), 1), (10, (70, 120, 76), 1), (50, (120, 170, 120), 2)):
            if step / ux < 4:
                continue
            for gx in range(int(math.floor(x0 / step)) * step, int(x1) + step, step):
                px, _ = self._to_img(gx, 0, cam)
                cv2.line(img, (px, 0), (px, h), color, width)
            for gy in range(int(math.floor(y0 / step)) * step, int(y1) + step, step):
                _, py = self._to_img(0, gy, cam)
                cv2.line(img, (0, py), (w, py), color, width)
        for gx in range(int(math.floor(x0 / 50)) * 50, int(x1) + 50, 50):
            for gy in range(int(math.floor(y0 / 50)) * 50, int(y1) + 50, 50):
                px, py = self._to_img(gx, gy, cam)
                cv2.putText(img, f"{gx},{gy}", (px + 3, py - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (160, 210, 160), 1,
                            cv2.LINE_AA)
        for fid in self.settings.sim_fiducials:                  # copper fiducials
            fx, fy = self._to_img(float(fid["x"]), float(fid["y"]), cam)
            r = max(1, int(round(float(fid.get("diameter", 1.0)) / 2 / ux)))
            cv2.circle(img, (fx, fy), r, (80, 170, 215), -1, cv2.LINE_AA)
        for feeder in self.machine.of_kind("feeder"):           # a 0805 part (2 x 1.25 mm) at every pick location
            loc = feeder.status().get("pick_location") or {}
            if "x" not in loc or "y" not in loc:
                continue
            cx, cy = self._to_img(loc["x"], loc["y"], cam)
            size = (2.0 / ux, 1.25 / uy)
            box = cv2.boxPoints(((cx, cy), size, -(loc.get("rotation") or 0)))
            cv2.fillPoly(img, [box.astype(np.int32)], (40, 40, 40))
            for side in (-1, 1):                                  # tinned terminals
                ex = cx + side * (0.8 / ux) * math.cos(math.radians(loc.get("rotation") or 0))
                ey = cy - side * (0.8 / uy) * math.sin(math.radians(loc.get("rotation") or 0))
                term = cv2.boxPoints(((ex, ey), (0.4 / ux, 1.25 / uy), -(loc.get("rotation") or 0)))
                cv2.fillPoly(img, [term.astype(np.int32)], (200, 200, 200))
        return img

    def _nozzle_sprite(self, nozzle_id: str, ux: float) -> Any:
        """What a nozzle looks like from below, drawn once per scale: a textured body (3 mm), the tip (1 mm) with a
        dark bore, like the real ones. The texture matters: flat disks give vision nothing to lock onto."""
        import cv2
        import numpy as np
        cache = self.__dict__.setdefault("_sprites", {})
        key = (nozzle_id, round(ux, 6))
        if key in cache:
            return cache[key]
        r_body = max(4, int(round(1.5 / ux)))
        size = 2 * r_body + 3
        c = size // 2
        rng = np.random.default_rng(abs(hash(nozzle_id)) % 2**32)
        sprite = np.zeros((size, size, 3), np.uint8)
        alpha = np.zeros((size, size), np.uint8)
        cv2.circle(alpha, (c, c), r_body, 255, -1)
        texture = rng.integers(70, 170, (size, size), dtype=np.uint8)
        texture = cv2.GaussianBlur(texture, (0, 0), max(1.0, 0.05 / ux))
        sprite[:] = texture[..., None]
        cv2.circle(sprite, (c, c), int(round(0.5 / ux)), (220, 230, 220), -1)      # tip
        cv2.circle(sprite, (c, c), int(round(0.5 / ux)), (90, 90, 90), max(1, int(round(0.05 / ux))))
        cv2.circle(sprite, (c, c), max(2, int(round(0.2 / ux))), (10, 10, 10), -1)  # bore
        sprite = cv2.bitwise_and(sprite, sprite, mask=alpha)
        cache[key] = (sprite, alpha)
        return sprite, alpha

    def _nozzles(self) -> Any:
        import cv2
        import numpy as np
        w, h = self.settings.width, self.settings.height
        ux, uy = self._px()
        cam = self.compute_location() or {}
        img = np.full((h, w, 3), 12, np.uint8)
        focus_z = cam.get("z")
        for nozzle in self.machine.of_kind("nozzle"):
            if not isinstance(nozzle, Nozzle):
                continue
            loc = dict(nozzle.axis_location())          # the physical tip: axis + the simulated runout
            run = self.settings.sim_runout.get(nozzle.id)
            if run:                                      # the tip turns around an axis it isn't on
                a = math.radians((loc.get("rotation") or 0) + run.get("phase", 0))
                loc["x"] += run["runout"] * math.cos(a)
                loc["y"] += run["runout"] * math.sin(a)
            cx, cy = self._to_img(loc["x"], loc["y"], cam)
            sprite, alpha = self._nozzle_sprite(nozzle.id, ux)
            size = sprite.shape[0]
            if not (-size < cx < w + size and -size < cy < h + size):
                continue
            angle = loc.get("rotation") or 0
            m = cv2.getRotationMatrix2D((size / 2, size / 2), angle, 1.0)
            m[0, 2] += cx - size / 2
            m[1, 2] += cy - size / 2
            layer = cv2.warpAffine(sprite, m, (w, h))
            mask = cv2.warpAffine(alpha, m, (w, h))
            mx, my = cx + int(1.2 / ux * math.cos(math.radians(angle))), cy - int(1.2 / uy * math.sin(math.radians(angle)))
            cv2.circle(layer, (mx, my), max(2, int(0.2 / ux)), (0, 0, 200), -1)      # mark showing rotation
            if focus_z is not None and loc.get("z") is not None:
                blur = abs(loc["z"] - focus_z) / self.settings.focus_depth
                k = int(min(51, blur * 4)) * 2 + 1
                if k > 1:
                    layer = cv2.GaussianBlur(layer, (k, k), 0)
                    mask = cv2.GaussianBlur(mask, (k, k), 0)
            a = (mask.astype(np.float32) / 255.0)[..., None]
            img = (img * (1 - a) + layer * a).astype(np.uint8)
        return img

    def _test(self) -> Any:
        import cv2
        import numpy as np
        w, h = self.settings.width, self.settings.height
        img = np.zeros((h, w, 3), np.uint8)
        bars = [(255, 255, 255), (0, 255, 255), (255, 255, 0), (0, 255, 0), (255, 0, 255), (0, 0, 255), (255, 0, 0)]
        for i, c in enumerate(bars):
            img[:, i * w // len(bars):(i + 1) * w // len(bars)] = c
        cv2.putText(img, time.strftime("%H:%M:%S"), (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
        return img


# ── v4l2 ───────────────────────────────────────────────────────────────────────────────────────────────────────────

class DeviceSelector(BaseModel):
    """Tried in this order; the first that matches a present device wins."""
    model_config = ConfigDict(extra="forbid")
    usb_serial: str | None = Field(None, description="USB serial number (survives replugging into another port)")
    usb_id: str | None = Field(None, description="vendor:product, e.g. 0c45:6340 (the first such camera)")
    name: str | None = Field(None, description="Device name as the driver reports it, e.g. 'USB 2.0 Camera'")
    path: str | None = Field(None, description="/dev/videoN or a stable /dev/v4l/by-path/... or by-id/... link")


class VideoFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int
    height: int
    fps: float = 30
    pixel_format: str = Field("MJPG", description="FourCC: MJPG (less USB bandwidth), YUYV, ...")


class Watchdog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    no_frame_timeout: DurationMs = Field(3000.0, description="No frame for this long counts as a failure")
    reopen: bool = Field(True, description="Close and reopen the device after a failure")
    reopen_delay: DurationMs = Field(1000.0, description="Wait before reopening")


class V4l2CameraSettings(CameraCommon):
    device: DeviceSelector
    format: VideoFormat
    controls: dict[str, Any] = Field(default_factory=dict,
                                     description="V4L2 controls set after opening, e.g. {focus_automatic_continuous: "
                                                 "false, focus_absolute: 250} (names as v4l2-ctl --list-ctrls)")
    buffer_size: int = Field(1, ge=1, le=10, description="Driver frame buffers; 1 = always the newest frame")
    warmup: DurationMs = Field(1500.0, description="Drop frames for this long after opening the device (USB "
                                                   "cameras start dark until auto exposure settles)")
    watchdog: Watchdog = Field(default_factory=Watchdog)


def _read(path: str) -> str | None:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def list_devices() -> list[dict]:
    """Video capture devices on this computer, with what selectors can match."""
    out = []
    links: dict[str, list[str]] = {}
    for kind in ("by-id", "by-path"):
        for link in sorted(glob.glob(os.path.join(DEV, "v4l", kind, "*"))):
            target = os.path.realpath(link)
            links.setdefault(os.path.basename(target), []).append(link)
    for sys_dir in sorted(glob.glob(os.path.join(SYSFS, "video*")), key=lambda p: int(re.sub(r"\D", "", p) or 0)):
        node = os.path.basename(sys_dir)
        info: dict[str, Any] = {"path": os.path.join(DEV, node), "name": _read(os.path.join(sys_dir, "name")),
                                "index": _read(os.path.join(sys_dir, "index")), "usb_id": None, "usb_serial": None,
                                "manufacturer": None, "product": None, "links": links.get(node, [])}
        usb = os.path.realpath(os.path.join(sys_dir, "device"))
        for _ in range(4):                       # interface -> device: the device dir has idVendor
            if os.path.exists(os.path.join(usb, "idVendor")):
                vendor, product = _read(os.path.join(usb, "idVendor")), _read(os.path.join(usb, "idProduct"))
                info.update(usb_id=f"{vendor}:{product}", usb_serial=_read(os.path.join(usb, "serial")),
                            manufacturer=_read(os.path.join(usb, "manufacturer")),
                            product=_read(os.path.join(usb, "product")))
                break
            usb = os.path.dirname(usb)
        info["capture"] = info["index"] in (None, "0")    # USB cameras: index 0 is the picture, 1 the metadata
        out.append(info)
    return out


def resolve_device(sel: DeviceSelector) -> tuple[str, str]:
    """(device path, which selector matched)."""
    devices = [d for d in list_devices() if d["capture"]]
    if sel.usb_serial:
        for d in devices:
            if d["usb_serial"] == sel.usb_serial and (not sel.usb_id or d["usb_id"] == sel.usb_id):
                return d["path"], "usb_serial"
    if sel.usb_id:
        for d in devices:
            if d["usb_id"] == sel.usb_id.lower():
                return d["path"], "usb_id"
    if sel.name:
        for d in devices:
            if d["name"] == sel.name:
                return d["path"], "name"
    if sel.path and os.path.exists(sel.path):
        return os.path.realpath(sel.path) if sel.path.startswith(DEV) else sel.path, "path"
    wanted = ", ".join(f"{k}={v}" for k, v in sel.model_dump().items() if v)
    found = "; ".join(f"{d['path']} {d['usb_id'] or ''} {d['usb_serial'] or ''} {d['name'] or ''}".strip()
                      for d in devices) or "none"
    raise MachineError("not_found", f"no camera matches {wanted} (present: {found})", what="camera_device")


# OpenCV properties for common V4L2 control names, used when v4l2-ctl isn't installed
_CV_PROPS = {
    "brightness": "CAP_PROP_BRIGHTNESS", "contrast": "CAP_PROP_CONTRAST", "saturation": "CAP_PROP_SATURATION",
    "hue": "CAP_PROP_HUE", "gain": "CAP_PROP_GAIN", "sharpness": "CAP_PROP_SHARPNESS", "gamma": "CAP_PROP_GAMMA",
    "backlight_compensation": "CAP_PROP_BACKLIGHT", "zoom_absolute": "CAP_PROP_ZOOM",
    "exposure_absolute": "CAP_PROP_EXPOSURE", "exposure_time_absolute": "CAP_PROP_EXPOSURE",
    "exposure_auto": "CAP_PROP_AUTO_EXPOSURE", "auto_exposure": "CAP_PROP_AUTO_EXPOSURE",
    "focus_absolute": "CAP_PROP_FOCUS", "focus_auto": "CAP_PROP_AUTOFOCUS",
    "focus_automatic_continuous": "CAP_PROP_AUTOFOCUS",
    "white_balance_temperature": "CAP_PROP_WB_TEMPERATURE",
    "white_balance_temperature_auto": "CAP_PROP_AUTO_WB", "white_balance_automatic": "CAP_PROP_AUTO_WB",
}
_CTRL_LINE = re.compile(r"^\s*(\w+)\s+0x[0-9a-f]+\s+\((\w+)\)\s*:\s*(.*)$")


def _control_value(value: Any) -> int | float:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and value.lower() in ("on", "true", "yes", "off", "false", "no"):
        return int(value.lower() in ("on", "true", "yes"))
    raise MachineError("invalid_params", f"control value {value!r} is not a number or on/off")


@registry.register("camera", "v4l2", V4l2CameraSettings, "USB / V4L2 camera",
                   "USB camera through Video4Linux (OpenCV), matched by USB serial, vendor:product, name or path")
class V4l2Camera(CameraBase):

    def setup(self) -> None:
        super().setup()
        f = self.settings.format
        self._status.update(device=None, matched_by=None, requested={"width": f.width, "height": f.height,
                                                                     "fps": f.fps, "pixel_format": f.pixel_format})
        self._cap = None

    def open_device(self) -> None:
        import cv2
        path, how = resolve_device(self.settings.device)
        f = self.settings.format
        backend = cv2.CAP_V4L2 if path.startswith(DEV) else cv2.CAP_ANY
        cap = cv2.VideoCapture(path, backend)
        if not cap.isOpened():
            raise MachineError("driver_error", f"{self.name}: can't open {path}")
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*f.pixel_format[:4].ljust(4)))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, f.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, f.height)
        cap.set(cv2.CAP_PROP_FPS, f.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, self.settings.buffer_size)
        if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, self.settings.watchdog.no_frame_timeout)
        self._cap, self._path = cap, path
        self._last_frame = time.monotonic()
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        actual = {"width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                  "fps": cap.get(cv2.CAP_PROP_FPS),
                  "pixel_format": "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)).strip("\x00")}
        self.update(device=path, matched_by=how, actual=actual)
        problems = self.apply_controls(self.settings.controls)
        if problems:
            self.update(last_error="; ".join(problems))

    def grab(self, decode: Any = True) -> Any:
        cap = self._cap
        if cap is None:
            return None
        ok = cap.grab()                               # take the frame from the device (cheap) ...
        frame = None
        if callable(decode):
            decode = ok and decode()                  # asked now that the frame is there
        if ok and decode:
            try:
                ok, frame = cap.retrieve()            # ... and decode it only when needed (expensive)
            except Exception:                         # an empty or broken buffer (the device is going away)
                ok, frame = False, None
            ok = ok and frame is not None
        now = time.monotonic()
        if ok:
            self._last_frame = now
            if str(self._status.get("last_error") or "").startswith("no frame"):
                self.update(last_error=None)          # recovered after the watchdog
            return frame if decode else SKIPPED
        if not self._path.startswith(DEV):          # a video file ended: start over (tests, demos)
            import cv2
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        wd = self.settings.watchdog
        if now - self._last_frame > wd.no_frame_timeout / 1000:
            self.update(last_error=f"no frame for {wd.no_frame_timeout:.0f} ms")
            if not wd.reopen:
                raise MachineError("driver_error", f"{self.name}: no frames")
            log.warning("%s: no frames, reopening %s", self.name, self._path)
            self.close_device()
            if self._stop.wait(wd.reopen_delay / 1000):
                return None
            self.open_device()
        return None

    def close_device(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    # ── controls ───────────────────────────────────────────────────────────────────────────────────────────────
    def apply_controls(self, controls: dict[str, Any]) -> list[str]:
        """Set V4L2 controls (v4l2-ctl if installed, else the OpenCV properties that exist). Returns problems."""
        problems = []
        path = self._status.get("device")
        v4l2ctl = shutil.which("v4l2-ctl")
        for name, value in controls.items():
            try:
                v = _control_value(value)
            except MachineError as exc:
                problems.append(f"{name}: {exc.message}")
                continue
            if v4l2ctl and path and path.startswith(DEV):
                r = subprocess.run([v4l2ctl, "-d", path, "-c", f"{name}={int(v)}"], capture_output=True, text=True,
                                   timeout=5)
                if r.returncode != 0:
                    problems.append(f"{name}: {(r.stderr or r.stdout).strip()}")
            else:
                import cv2
                prop = _CV_PROPS.get(name)
                if prop is None or self._cap is None or not self._cap.set(getattr(cv2, prop), float(v)):
                    problems.append(f"{name}: can't set (install v4l2-ctl for all controls)")
        return problems

    def list_controls(self) -> list[dict]:
        path = self._status.get("device")
        v4l2ctl = shutil.which("v4l2-ctl")
        if v4l2ctl and path and path.startswith(DEV):
            r = subprocess.run([v4l2ctl, "-d", path, "--list-ctrls"], capture_output=True, text=True, timeout=5)
            out = []
            for line in r.stdout.splitlines():
                m = _CTRL_LINE.match(line)
                if m:
                    fields = dict(kv.split("=", 1) for kv in m.group(3).split() if "=" in kv)
                    out.append({"name": m.group(1), "type": m.group(2),
                                **{k: _num(v) for k, v in fields.items()}})
            return out
        if self._cap is None:
            return []
        import cv2
        return [{"name": name, "value": self._cap.get(getattr(cv2, prop))} for name, prop in _CV_PROPS.items()]


def _num(v: str) -> Any:
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v
