"""Built-in object types: settings models (validation + JSON Schema) and runtime classes (status for the API).

Phase 0: simulated drivers and cameras work; the `gcode` driver and `v4l2` camera are registered with their full
settings (so real machine configs validate) but report "not implemented yet" at runtime.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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

class SimulatedDriverSettings(Settings):
    pass


@registry.register("driver", "simulated", SimulatedDriverSettings, "Simulated driver")
class SimulatedDriver(MachineObject):
    """A driver without hardware, for development and tests."""
    kind = "driver"

    def setup(self) -> None:
        self._status.update(connected=True, firmware="simulated", port=None, last_error=None)


class GcodeDriverSettings(Settings):
    port: str = Field(..., description="Serial device, e.g. /dev/serial0, or tcp://host:port")
    baud: int = 115200
    profile: str | None = Field(None, description="Built-in firmware profile: grblhal, marlin, smoothieware, duet, tinyg")
    letter_variables: bool = True
    timeout: DurationMs = 5000.0
    max_feed: Speed | None = None
    commands: dict[str, str] = Field(default_factory=dict, description="OpenPnP-style commands, overriding the profile")
    stop: list[str] = Field(default_factory=list, description="Sent in order on machine.stop")


@registry.register("driver", "gcode", GcodeDriverSettings, "G-code controller",
                   "G-code controller (grblHAL, Marlin, Smoothieware, Duet, TinyG) with OpenPnP-compatible commands")
class GcodeDriver(MachineObject):
    kind = "driver"

    def setup(self) -> None:
        self._status.update(connected=False, firmware=None, port=self.settings.port,
                            last_error="gcode driver not implemented yet (phase 1)")


# ── Axes ───────────────────────────────────────────────────────────────────────────────────────────────────────────

class ControllerAxisSettings(Settings):
    driver: str
    letter: str = Field(..., min_length=1, max_length=1)
    kind: Literal["linear", "rotation"] = "linear"
    home: LengthOrAngle = 0.0
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

    @property
    def position(self) -> float:
        return self._status["position"]


@registry.register("axis", "controller", ControllerAxisSettings, "Controller axis")
class ControllerAxis(AxisBase):
    """An axis driven by a motion backend (G-code letter or Klipper axis)."""
    homeable = True

    def setup(self) -> None:
        s = self.settings
        self.axis_kind = s.kind
        self._status.update(kind=s.kind, position=s.home, homed=False, driver=s.driver, letter=s.letter,
                            soft_limits=list(s.soft_limits) if s.soft_limits else None,
                            safe_zone=list(s.safe_zone) if s.safe_zone else None)


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


class GcodeActuatorSettings(Settings):
    driver: str
    value: ValueType = "boolean"
    on: str | None = Field(None, description="G-code for true/on")
    off: str | None = Field(None, description="G-code for false/off")
    set: str | None = Field(None, description="Template for double/string values, e.g. 'M3 S{value}'")
    read: str | None = None
    read_regex: str | None = None
    on_enable: Any = None
    on_disable: Any = None


class ActuatorBase(MachineObject):
    kind = "actuator"

    def setup(self) -> None:
        self._status.update(value_type=self.settings.value, value=None,
                            readable=bool(getattr(self.settings, "read", None)))


@registry.register("actuator", "simulated", SimulatedActuatorSettings, "Simulated actuator")
class SimulatedActuator(ActuatorBase):
    pass


@registry.register("actuator", "gcode", GcodeActuatorSettings, "G-code actuator")
class GcodeActuator(ActuatorBase):
    """Switched with G-code through a driver (e.g. M64 P0 / M65 P0)."""


# ── Cameras ────────────────────────────────────────────────────────────────────────────────────────────────────────

class CameraAxes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    z: str
    rotation: str


class CameraCommon(Settings):
    looking: Literal["up", "down"]
    location: Location | None = Field(None, description="Machine-mounted cameras: fixed location")
    offset: Location | None = Field(None, description="Head-mounted cameras: offset from the head")
    axes: CameraAxes | None = None
    units_per_pixel: Scale | None = None


class SimulatedCameraSettings(CameraCommon):
    width: int = 640
    height: int = 480


class DeviceSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")
    usb_serial: str | None = None
    usb_id: str | None = Field(None, description="vendor:product, e.g. 0c45:6340")
    path: str | None = None


class VideoFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int
    height: int
    fps: float = 30
    pixel_format: str = "MJPG"


class Watchdog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    no_frame_timeout: DurationMs = 3000.0
    reopen: bool = True


class V4l2CameraSettings(CameraCommon):
    device: DeviceSelector
    format: VideoFormat
    controls: dict[str, Any] = Field(default_factory=dict)
    watchdog: Watchdog = Field(default_factory=Watchdog)


class CameraBase(MachineObject):
    kind = "camera"
    head: Head | None = None

    def setup(self) -> None:
        s = self.settings
        self._status.update(looking=s.looking, location=self.compute_location(), streaming=False,
                            units_per_pixel=s.units_per_pixel.model_dump() if s.units_per_pixel else None,
                            stream_url=f"/cameras/{self.id}/stream.mjpg",
                            snapshot_url=f"/cameras/{self.id}/snapshot.jpg", last_error=None)

    def compute_location(self) -> dict | None:
        s = self.settings
        if self.head is not None:
            off = s.offset or Location()
            return {"x": self.head.x.position + (off.x or 0.0), "y": self.head.y.position + (off.y or 0.0)}
        return s.location.as_dict() if s.location else None

    def refresh(self) -> None:
        if self.head is not None:
            self.update(location=self.compute_location())


@registry.register("camera", "simulated", SimulatedCameraSettings, "Simulated camera")
class SimulatedCamera(CameraBase):
    def setup(self) -> None:
        super().setup()
        self._status.update(streaming=True, width=self.settings.width, height=self.settings.height, fps=10)


@registry.register("camera", "v4l2", V4l2CameraSettings, "USB / V4L2 camera")
class V4l2Camera(CameraBase):
    """USB camera through Video4Linux (OpenCV). Matched by USB serial, vendor:product or path."""

    def setup(self) -> None:
        super().setup()
        f = self.settings.format
        self._status.update(width=f.width, height=f.height, fps=f.fps,
                            last_error="v4l2 camera not implemented yet (phase 1)")


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
