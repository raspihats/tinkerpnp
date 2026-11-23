"""Built-in object types: settings models (validation + JSON Schema) and runtime classes (status for the API).

The simulated driver works without hardware. The `gcode` driver and actuator live in gcode.py, the camera types in
cameras.py.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .errors import MachineError, Stopped
from .registry import registry
from .units import Acceleration, DurationMs, Length, LengthOrAngle, Speed

if TYPE_CHECKING:
    from .machine import Machine


# ── Settings building blocks ───────────────────────────────────────────────────────────────────────────────────────

class Settings(BaseModel):
    """Base for all object settings. Unknown keys are errors (catches typos); `x-*` keys are stripped by the loader."""
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, description="Human-readable name")
    notes: str | None = Field(None, description="Free-text notes")


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: Length | None = None
    y: Length | None = None
    z: Length | None = None
    rotation: float | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class Scale(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float
    y: float


Range = tuple[LengthOrAngle, LengthOrAngle]


# ── Runtime base ───────────────────────────────────────────────────────────────────────────────────────────────────

class MachineObject:
    kind: str = ""

    def __init__(self, obj_id: str, type_name: str, settings: Settings, machine: "Machine"):
        self.id = obj_id
        self.type = type_name
        self.settings = settings
        self.machine = machine
        self._status: dict[str, Any] = {"type": type_name}

    @property
    def name(self) -> str:
        return f"{self.kind}/{self.id}"

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def setup(self) -> None:
        """Called once all objects exist (references resolved)."""

    def update(self, **fields: Any) -> None:
        changed = {k: v for k, v in fields.items() if self._status.get(k, object()) != v}
        if changed:
            self._status.update(changed)
            self.machine.publish(self.name, changed)


# ── Drivers ────────────────────────────────────────────────────────────────────────────────────────────────────────

class DriverBase(MachineObject):
    """The motion backend interface (design.md, 3.4). All methods run on the machine's motion thread, except
    `stop` and `cancel`, which are called from anywhere and must return quickly. Blocking methods return when the
    controller has finished, and raise Stopped when the machine's stop event is set while they wait."""
    kind = "driver"

    def connect(self) -> None: ...

    def enable(self) -> None: ...

    def disable(self) -> None: ...

    def home(self, axes: list["ControllerAxis"]) -> None:
        raise MachineError("driver_error", f"{self.name} can't home")

    def move(self, targets: dict["ControllerAxis", float], speed: float) -> None:
        """Move the axes to raw positions; speed is a fraction (0..1] of each axis's feed."""
        raise MachineError("driver_error", f"{self.name} can't move")

    def actuate(self, actuator: "ActuatorBase", value: Any) -> None:
        raise MachineError("driver_error", f"{self.name} can't switch actuators")

    def stop(self) -> str:
        """Abort motion now. Returns the machine state afterwards: "idle" or "alarm"."""
        return "idle"

    def cancel(self) -> None:
        """Abort the current jog (the machine stays usable)."""


class SimulatedDriverSettings(Settings):
    time_scale: float = Field(1.0, ge=0, description="Simulated moves take distance/feed × this; 0 = instant")
    home_time: DurationMs = Field(1000.0, description="How long simulated homing takes (before time_scale)")


@registry.register("driver", "simulated", SimulatedDriverSettings, "Simulated driver")
class SimulatedDriver(DriverBase):
    """A driver without hardware, for development and tests. Moves take realistic time and report positions
    while they run."""

    def setup(self) -> None:
        self._status.update(connected=True, firmware="simulated", port=None, last_error=None)

    def _wait(self, seconds: float) -> None:
        if self.machine.stop_event.wait(seconds):
            raise Stopped()

    def home(self, axes: list["ControllerAxis"]) -> None:
        self._wait(self.settings.home_time / 1000.0 * self.settings.time_scale)
        self.machine.set_axis_positions({a.id: a.settings.home for a in axes}, homed=True)

    def move(self, targets: dict["ControllerAxis", float], speed: float) -> None:
        start = {a: a.position for a in targets}
        duration = max((abs(t - start[a]) / ((a.settings.feed or 50.0) * speed) for a, t in targets.items()),
                       default=0.0) * self.settings.time_scale
        t0 = time.monotonic()
        while True:
            frac = 1.0 if duration <= 0 else min(1.0, (time.monotonic() - t0) / duration)
            self.machine.set_axis_positions({a.id: start[a] + (t - start[a]) * frac for a, t in targets.items()})
            if frac >= 1.0:
                return
            self._wait(0.05)

    def actuate(self, actuator: "ActuatorBase", value: Any) -> None:
        pass


# The gcode driver and actuator are in gcode.py.


# ── Axes ───────────────────────────────────────────────────────────────────────────────────────────────────────────

class ControllerAxisSettings(Settings):
    driver: str
    letter: str = Field(..., min_length=1, max_length=1)
    kind: Literal["linear", "rotation"] = "linear"
    home: LengthOrAngle = Field(0.0, description="Position after homing")
    homing: bool | None = Field(None, description="Has a home switch. Default: linear axes yes, rotation axes no "
                                                  "(they keep their position and count as homed after homing)")
    soft_limits: Range | None = None
    safe_zone: Range | None = None
    limit_rotation: Range | None = None
    feed: Speed | None = None
    acceleration: Acceleration | None = None
    acceleration_applies_in: Literal["firmware", "driver"] = "firmware"
    tolerance: float = 0.025


class VirtualAxisSettings(Settings):
    kind: Literal["linear", "rotation"] = "linear"
    home: LengthOrAngle = 0.0


class MappedAxisSettings(Settings):
    input: str = Field(..., description="Id of the input axis")
    map: tuple[tuple[float, float], tuple[float, float]] = Field(
        ..., description="Two points [[input0, output0], [input1, output1]] of a linear map")


class AxisBase(MachineObject):
    kind = "axis"
    axis_kind = "linear"
    homeable = False
    tolerance = 1e-6

    @property
    def position(self) -> float:
        return self._status["position"]

    @property
    def homed(self) -> bool:
        return bool(self._status.get("homed"))


@registry.register("axis", "controller", ControllerAxisSettings, "Controller axis")
class ControllerAxis(AxisBase):
    """An axis driven by a motion backend (G-code letter or Klipper axis)."""
    def setup(self) -> None:
        s = self.settings
        self.axis_kind = s.kind
        self.homeable = s.homing if s.homing is not None else s.kind == "linear"
        self.tolerance = s.tolerance
        self.driver: DriverBase = self.machine.get("driver", s.driver)
        self._status.update(kind=s.kind, position=s.home, homed=False, driver=s.driver, letter=s.letter,
                            homing=self.homeable,
                            soft_limits=list(s.soft_limits) if s.soft_limits else None,
                            safe_zone=list(s.safe_zone) if s.safe_zone else None)

    def limits(self) -> tuple[float, float] | None:
        s = self.settings
        return s.soft_limits or s.limit_rotation

    def safe_value(self) -> float | None:
        """The nearest position inside the safe zone (None: no safe zone or already inside)."""
        zone = self.settings.safe_zone
        if not zone:
            return None
        low, high = zone
        if low - self.tolerance <= self.position <= high + self.tolerance:
            return None
        return min(max(self.position, low), high)


@registry.register("axis", "virtual", VirtualAxisSettings, "Virtual axis")
class VirtualAxis(AxisBase):
    """An axis without hardware (e.g. Z of a fixed-focus camera on the head)."""

    def setup(self) -> None:
        self.axis_kind = self.settings.kind
        self._status.update(kind=self.settings.kind, position=self.settings.home, homed=True)


@registry.register("axis", "mapped", MappedAxisSettings, "Mapped axis")
class MappedAxis(AxisBase):
    """A linear map of another axis, e.g. the second nozzle on a shared seesaw Z (output = 114 - input)."""

    def setup(self) -> None:
        self.input: AxisBase = self.machine.get("axis", self.settings.input)
        self.axis_kind = self.input.axis_kind
        self._status.update(kind=self.axis_kind, input=self.settings.input, homed=self.input.status()["homed"],
                            position=self.forward(self.input.position))

    def forward(self, value: float) -> float:
        (i0, o0), (i1, o1) = self.settings.map
        return o0 + (value - i0) * (o1 - o0) / (i1 - i0)

    def inverse(self, value: float) -> float:
        (i0, o0), (i1, o1) = self.settings.map
        return i0 + (value - o0) * (i1 - i0) / (o1 - o0)

    def refresh(self) -> None:
        self.update(position=self.forward(self.input.position), homed=self.input.status()["homed"])


# ── Heads, nozzles, nozzle tips ────────────────────────────────────────────────────────────────────────────────────

class HeadAxes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: str
    y: str


class HeadSettings(Settings):
    axes: HeadAxes
    pump: str | None = Field(None, description="Actuator id switched on while any nozzle carries a part")
    pump_settle: DurationMs = 0.0
    nozzles: dict[str, dict] = Field(default_factory=dict)
    cameras: dict[str, dict] = Field(default_factory=dict)
    actuators: list[str] = Field(default_factory=list)


@registry.register("head", "head", HeadSettings, "Head")
class Head(MachineObject):
    kind = "head"

    def setup(self) -> None:
        s = self.settings
        self.x = self.machine.get("axis", s.axes.x)
        self.y = self.machine.get("axis", s.axes.y)
        self._status.update(location=self.compute_location(), nozzles=list(s.nozzles), cameras=list(s.cameras),
                            actuators=list(s.actuators), pump=s.pump)

    def compute_location(self) -> dict:
        return {"x": self.x.position, "y": self.y.position}

    def refresh(self) -> None:
        self.update(location=self.compute_location())


class NozzleAxes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    z: str
    rotation: str


class NozzleSettings(Settings):
    axes: NozzleAxes
    offset: Location = Field(default_factory=Location)
    vacuum: str | None = None
    tip: str | None = None
    compatible_tips: list[str] = Field(default_factory=list)
    pick_dwell: DurationMs = 0.0
    place_dwell: DurationMs = 0.0


class HeadMountable(MachineObject):
    head: Head

    def head_location(self, z: float | None, rotation: float | None) -> dict:
        off = self.settings.offset
        loc = {"x": self.head.x.position + (off.x or 0.0), "y": self.head.y.position + (off.y or 0.0)}
        if z is not None:
            loc["z"] = z
        if rotation is not None:
            loc["rotation"] = rotation
        return loc


@registry.register("nozzle", "nozzle", NozzleSettings, "Nozzle")
class Nozzle(HeadMountable):
    kind = "nozzle"

    def setup(self) -> None:
        s = self.settings
        self.z = self.machine.get("axis", s.axes.z)
        self.rotation = self.machine.get("axis", s.axes.rotation)
        self._status.update(location=self.compute_location(), tip=s.tip, part=None, vacuum=s.vacuum,
                            vacuum_value=None, in_safe_zone=self.in_safe_zone())

    def compute_location(self) -> dict:
        return self.head_location(self.z.position, self.rotation.position)

    def in_safe_zone(self) -> bool | None:
        raw = self.z.input if isinstance(self.z, MappedAxis) else self.z
        zone = raw.status().get("safe_zone")
        if not zone:
            return None
        return zone[0] - 1e-6 <= raw.position <= zone[1] + 1e-6

    def refresh(self) -> None:
        self.update(location=self.compute_location(), in_safe_zone=self.in_safe_zone())


class NozzleTipSettings(Settings):
    max_part_diameter: Length | None = None
    max_part_height: Length | None = None


@registry.register("nozzle_tip", "nozzle_tip", NozzleTipSettings, "Nozzle tip")
class NozzleTip(MachineObject):
    kind = "nozzle_tip"

    def setup(self) -> None:
        self._status.update(loaded_on=None, calibrated=False)


# ── Actuators ──────────────────────────────────────────────────────────────────────────────────────────────────────

ValueType = Literal["boolean", "double", "string"]


class SimulatedActuatorSettings(Settings):
    value: ValueType = "boolean"
    on_enable: Any = None
    on_disable: Any = None


_TRUE = {"on", "true", "yes", "1"}
_FALSE = {"off", "false", "no", "0"}


class ActuatorBase(MachineObject):
    kind = "actuator"

    def setup(self) -> None:
        self._status.update(value_type=self.settings.value, value=None,
                            readable=bool(getattr(self.settings, "read", None)))

    def coerce(self, value: Any) -> Any:
        """Check a value against value_type. Booleans also accept on/off (YAML 1.2 reads `off` as a string)."""
        vt = self.settings.value
        if vt == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)) and value in (0, 1):
                return bool(value)
            if isinstance(value, str) and value.lower() in _TRUE | _FALSE:
                return value.lower() in _TRUE
        elif vt == "double":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        elif isinstance(value, str):
            return value
        raise MachineError("invalid_params", f"{self.name} takes a {vt} value, not {value!r}")

    def set(self, value: Any) -> Any:
        value = self.coerce(value)
        self.write(value)
        self.update(value=value)
        return value

    def write(self, value: Any) -> None:
        """Send the value to the hardware."""

    def read(self) -> Any:
        raise MachineError("invalid_params", f"{self.name} can't be read")


@registry.register("actuator", "simulated", SimulatedActuatorSettings, "Simulated actuator")
class SimulatedActuator(ActuatorBase):
    pass


# ── Cameras ────────────────────────────────────────────────────────────────────────────────────────────────────────
# Settings shared by every camera type, and the capture machinery (frames, viewers, JPEG, overlays). The camera
# types (simulated, v4l2) are in cameras.py.

class CameraAxes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    z: str
    rotation: str


class Crop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: int = Field(0, ge=0)
    y: int = Field(0, ge=0)
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)


class ImageTransform(BaseModel):
    """Applied to every frame, in this order: crop, rotate, flip."""
    model_config = ConfigDict(extra="forbid")
    crop: Crop | None = Field(None, description="Pixels of the sensor image to keep")
    rotate: Literal[0, 90, 180, 270] = Field(0, description="Clockwise, degrees")
    flip_x: bool = Field(False, description="Mirror left-right")
    flip_y: bool = Field(False, description="Mirror top-bottom")


class CameraCommon(Settings):
    looking: Literal["up", "down"]
    location: Location | None = Field(None, description="Machine-mounted cameras: fixed location")
    offset: Location | None = Field(None, description="Head-mounted cameras: offset from the head")
    axes: CameraAxes | None = None
    units_per_pixel: Scale | None = Field(None, description="mm per pixel in X and Y (after the transform)")
    transform: ImageTransform = Field(default_factory=ImageTransform)
    idle_close: DurationMs = Field(10000.0, description="Close the device this long after the last viewer left; "
                                                        "0 = keep it open once opened")
    jpeg_quality: int = Field(80, ge=10, le=100, description="For streams and snapshots")
    stream_fps: float = Field(15.0, gt=0, le=120, description="Default frame rate of MJPEG streams")
    warmup: DurationMs = Field(0.0, description="Drop frames for this long after opening the device (auto "
                                                "exposure and white balance settle)")


OVERLAYS = ("crosshair", "scale", "grid", "info")


class CameraBase(MachineObject):
    """Frames come from `open_device` / `grab` / `close_device` (camera types implement them) on a capture thread,
    which runs while anyone is watching (`acquire` / `release`) and stops `idle_close` ms after the last viewer."""
    kind = "camera"
    head: Head | None = None
    KEEP_FRAMES = 10

    def setup(self) -> None:
        s = self.settings
        self._status.update(looking=s.looking, location=self.compute_location(), streaming=False,
                            units_per_pixel=s.units_per_pixel.model_dump() if s.units_per_pixel else None,
                            stream_url=f"/cameras/{self.id}/stream.mjpg",
                            snapshot_url=f"/cameras/{self.id}/snapshot.jpg", last_error=None, viewers=0,
                            fps=None, width=None, height=None)
        import threading
        self._cond = threading.Condition()
        self._frames: dict[int, Any] = {}          # seq -> frame (numpy array), the last KEEP_FRAMES
        self._seq = 0
        self._jpeg: dict[tuple, bytes] = {}         # (seq, overlays) -> JPEG
        self._users = 0
        self._last_use = time.monotonic()
        self._thread: threading.Thread | None = None
        self._running = False
        self._stop = threading.Event()

    def compute_location(self) -> dict | None:
        s = self.settings
        if self.head is not None:
            off = s.offset or Location()
            return {"x": self.head.x.position + (off.x or 0.0), "y": self.head.y.position + (off.y or 0.0)}
        return s.location.as_dict() if s.location else None

    def refresh(self) -> None:
        if self.head is not None:
            self.update(location=self.compute_location())

    # ── implemented by camera types ────────────────────────────────────────────────────────────────────────────
    def open_device(self) -> None: ...

    def grab(self) -> Any:
        """Block until the next frame (BGR numpy array); None if none came (the watchdog decides what then)."""
        return None

    def close_device(self) -> None: ...

    # ── viewers ────────────────────────────────────────────────────────────────────────────────────────────────
    def acquire(self) -> None:
        import threading
        with self._cond:
            self._users += 1
            self._last_use = time.monotonic()
            self.update(viewers=self._users)
            if not self._running:
                self._running = True
                self._stop.clear()
                self._thread = threading.Thread(target=self._capture_loop, name=f"camera-{self.id}", daemon=True)
                self._thread.start()

    def release(self) -> None:
        with self._cond:
            self._users = max(0, self._users - 1)
            self._last_use = time.monotonic()
            self.update(viewers=self._users)

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)

    def reopen(self) -> None:
        """Close and reopen the device (after unplugging, or to apply a new format)."""
        was_running = self._running
        self.close()
        if was_running:
            self.acquire()
            self.release()

    # ── capture ────────────────────────────────────────────────────────────────────────────────────────────────
    def _capture_loop(self) -> None:
        import logging
        log = logging.getLogger("tinkerpnp.camera")
        self.update(last_error=None)
        try:
            self.open_device()               # may set last_error itself (e.g. controls it couldn't set)
        except Exception as exc:
            log.error("%s: %s", self.name, exc)
            self.update(last_error=str(exc), streaming=False)
            with self._cond:
                self._running = False
                self._cond.notify_all()
            try:
                self.close_device()
            except Exception:
                pass
            return
        frames, t_fps = 0, time.monotonic()
        warm_until = time.monotonic() + self.settings.warmup / 1000.0
        try:
            while not self._stop.is_set():
                with self._cond:   # decide and mark stopped under the lock, so acquire() starts a new thread
                    if (self.settings.idle_close > 0 and self._users == 0
                            and time.monotonic() - self._last_use > self.settings.idle_close / 1000):
                        self._running = False
                        break
                try:
                    frame = self.grab()
                except Exception as exc:
                    log.error("%s: %s", self.name, exc)
                    self.update(last_error=str(exc))
                    frame = None
                if frame is None:
                    if self._stop.wait(0.05):
                        break
                    continue
                if time.monotonic() < warm_until:
                    continue                       # still settling after opening: nobody sees this frame
                frame = self.apply_transform(frame)
                with self._cond:
                    self._seq += 1
                    self._frames[self._seq] = frame
                    self._frames.pop(self._seq - self.KEEP_FRAMES, None)
                    for key in [k for k in self._jpeg if k[0] <= self._seq - self.KEEP_FRAMES]:
                        del self._jpeg[key]
                    self._cond.notify_all()
                frames += 1
                now = time.monotonic()
                if now - t_fps >= 1.0:
                    h, w = frame.shape[:2]
                    self.update(streaming=True, fps=round(frames / (now - t_fps), 1), width=w, height=h)
                    frames, t_fps = 0, now
                elif not self._status.get("streaming"):
                    h, w = frame.shape[:2]
                    self.update(streaming=True, width=w, height=h)
        finally:
            try:
                self.close_device()
            except Exception:
                log.exception("%s: close", self.name)
            import threading
            with self._cond:
                if self._thread is threading.current_thread():
                    self._running = False
                self._cond.notify_all()
            self.update(streaming=False, fps=None)

    def apply_transform(self, frame: Any) -> Any:
        import cv2
        t = self.settings.transform
        if t.crop is not None:
            c = t.crop
            frame = frame[c.y:c.y + c.height, c.x:c.x + c.width]
        if t.rotate:
            frame = cv2.rotate(frame, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                                       270: cv2.ROTATE_90_COUNTERCLOCKWISE}[t.rotate])
        if t.flip_x and t.flip_y:
            frame = cv2.flip(frame, -1)
        elif t.flip_x:
            frame = cv2.flip(frame, 1)
        elif t.flip_y:
            frame = cv2.flip(frame, 0)
        return frame

    def wait_frame(self, after: int, timeout: float) -> tuple[int, Any] | None:
        """The newest frame with seq > after, waiting up to timeout seconds. None if none came."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._seq <= after or self._seq not in self._frames:
                left = deadline - time.monotonic()
                if left <= 0 or (not self._running and self._status.get("last_error")):
                    return None
                self._cond.wait(min(left, 0.25))
            return self._seq, self._frames[self._seq]

    def frame(self, seq: int) -> Any:
        with self._cond:
            return self._frames.get(seq)

    def snapshot(self, timeout: float = 5.0) -> tuple[int, Any]:
        """A frame taken after this call started (opens the device if needed)."""
        from .errors import MachineError
        self.acquire()
        try:
            got = self.wait_frame(self._seq, timeout)
        finally:
            self.release()
        if got is None:
            raise MachineError("driver_error", f"{self.name}: no frame ({self._status.get('last_error') or 'timeout'})",
                               camera=self.id)
        return got

    # ── encoding and overlays ──────────────────────────────────────────────────────────────────────────────────
    def jpeg(self, seq: int, frame: Any, overlays: tuple[str, ...] = ()) -> bytes:
        import cv2
        key = (seq, overlays)
        cached = self._jpeg.get(key)
        if cached is not None:
            return cached
        if overlays:
            frame = self.draw_overlays(frame.copy(), overlays)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.settings.jpeg_quality])
        data = buf.tobytes()
        with self._cond:
            if seq > self._seq - self.KEEP_FRAMES:
                self._jpeg[key] = data
        return data

    def draw_overlays(self, img: Any, overlays: tuple[str, ...]) -> Any:
        import cv2
        h, w = img.shape[:2]
        cx, cy = w // 2, h // 2
        color = (0, 220, 255)
        upp = self.settings.units_per_pixel
        if "grid" in overlays and upp:
            step = _nice_step(upp.x * w / 8)
            for i in range(-50, 51):
                x = int(cx + i * step / upp.x)
                y = int(cy + i * step / upp.y)
                if 0 <= x < w:
                    cv2.line(img, (x, 0), (x, h), (90, 90, 90), 1)
                if 0 <= y < h:
                    cv2.line(img, (0, y), (w, y), (90, 90, 90), 1)
        if "crosshair" in overlays:
            cv2.line(img, (0, cy), (w, cy), color, 1)
            cv2.line(img, (cx, 0), (cx, h), color, 1)
            cv2.circle(img, (cx, cy), max(6, min(w, h) // 12), color, 1)
        if "scale" in overlays and upp:
            length_mm = _nice_step(upp.x * w / 5)
            px = int(length_mm / upp.x)
            x0, y0 = 12, h - 16
            cv2.line(img, (x0, y0), (x0 + px, y0), (255, 255, 255), 3)
            cv2.line(img, (x0, y0), (x0 + px, y0), (0, 0, 0), 1)
            cv2.putText(img, f"{length_mm:g} mm", (x0, y0 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1,
                        cv2.LINE_AA)
        if "info" in overlays:
            loc = self.compute_location() or {}
            text = f"{self.id}  X{loc.get('x', 0):.2f} Y{loc.get('y', 0):.2f}  {w}x{h}"
            cv2.putText(img, text, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(img, text, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        return img


def _nice_step(value: float) -> float:
    """1, 2 or 5 x 10^n just below value."""
    import math
    if value <= 0:
        return 1.0
    exp = math.floor(math.log10(value))
    for m in (5, 2, 1):
        if m * 10 ** exp <= value:
            return m * 10 ** exp
    return 10 ** exp


# ── Feeders ────────────────────────────────────────────────────────────────────────────────────────────────────────

class StaticFeederSettings(Settings):
    part: str | None = None
    pick_location: Location
    enabled: bool = True


@registry.register("feeder", "static", StaticFeederSettings, "Static feeder",
                   "Fixed pick location (loose part, single pocket); no feeding mechanism")
class StaticFeeder(MachineObject):
    kind = "feeder"

    def setup(self) -> None:
        s = self.settings
        self._status.update(enabled=s.enabled, part=s.part, pick_location=s.pick_location.as_dict(),
                            feeds_left=None, last_error=None)
