"""Built-in object types: settings models (validation + JSON Schema) and runtime classes (status for the API).

The simulated driver works without hardware. The `gcode` driver and actuator live in gcode.py, the camera types in
cameras.py.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    tolerance: float | None = Field(None, gt=0, description="Positions closer than this count as reached (no move "
                                                           "is sent). Default: half the `resolution`, or 0.0001 "
                                                           "without one (OpenPnP's default resolution)")
    wrap_around: bool = Field(False, description="Rotation axes without limit_rotation: turn the shortest way to the "
                                                 "angle (OpenPnP's wrap around)")
    backlash: "Backlash | None" = Field(None, description="Backlash compensation (as OpenPnP's)")
    resolution: float | None = Field(None, gt=0, description="Smallest move the axis can make (one motor step, "
                                                             "e.g. 1/4.444 mm), in mm or degrees. Jogs smaller than "
                                                             "this are refused, offering the minimum step")

    @model_validator(mode="after")
    def _tolerance_below_resolution(self):
        if self.resolution is not None and self.tolerance is not None and self.tolerance >= self.resolution:
            raise ValueError(f"tolerance {self.tolerance} must be smaller than resolution {self.resolution}, "
                             "or moves of one step are never sent (half the resolution is enough)")
        return self


class VirtualAxisSettings(Settings):
    kind: Literal["linear", "rotation"] = "linear"
    home: LengthOrAngle = 0.0


class Backlash(BaseModel):
    """As OpenPnP's backlash compensation. one_sided: every move ends approaching from the same side (first to
    target + offset, then to the target); one_sided_optimized: only when it would otherwise arrive from the other
    side. The final approach runs at speed_factor."""
    model_config = ConfigDict(extra="forbid")
    method: Literal["none", "one_sided", "one_sided_optimized"] = "none"
    offset: float = Field(0.0, description="Signed: the side the axis comes from (mm or degrees), e.g. -0.1")
    speed_factor: float = Field(1.0, gt=0, le=1, description="Speed of the final approach")


ControllerAxisSettings.model_rebuild()          # its `backlash` refers to Backlash, defined after it


class CamAxisSettings(Settings):
    """As OpenPnP's cam axes: two nozzles pushed down by one cam on a rotary motor. z = radius · sin(angle + 90° -
    arms_angle/2) + wheel_radius + wheel_gap (counter-clockwise), the angle mirrored for the clockwise nozzle."""
    input: str = Field(..., description="Id of the rotary (controller) axis that turns the cam, in degrees")
    direction: Literal["counter_clockwise", "clockwise"] = "counter_clockwise"
    cam_radius: Length = Field(..., gt=0)
    wheel_radius: Length = 0.0
    wheel_gap: Length = 0.0
    arms_angle: float = Field(180.0, gt=0, le=180, description="Angle between the cam's two arms")


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
        # as OpenPnP, which suppresses a move only below the axis resolution (default 0.0001)
        self.tolerance = s.tolerance if s.tolerance is not None else (s.resolution / 2 if s.resolution else 0.0001)
        self.driver: DriverBase = self.machine.get("driver", s.driver)
        self._status.update(kind=s.kind, position=s.home, homed=False, driver=s.driver, letter=s.letter,
                            homing=self.homeable,
                            soft_limits=list(s.soft_limits) if s.soft_limits else None,
                            safe_zone=list(s.safe_zone) if s.safe_zone else None, resolution=s.resolution)

    def limits(self) -> tuple[float, float] | None:
        s = self.settings
        if s.kind == "rotation":            # as OpenPnP: a rotation axis is limited only with limit-rotation
            return s.limit_rotation
        return s.soft_limits

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


@registry.register("axis", "cam", CamAxisSettings, "Cam axis",
                   "Z of a nozzle pushed down by a cam on a rotary motor (OpenPnP's cam clockwise / counter-clockwise "
                   "axes)")
class CamAxis(MappedAxis):
    def setup(self) -> None:
        self.input = self.machine.get("axis", self.settings.input)
        self.axis_kind = "linear"
        self._status.update(kind="linear", input=self.settings.input, homed=self.input.status()["homed"],
                            position=self.forward(self.input.position))

    def _shift(self) -> float:
        return 90.0 - self.settings.arms_angle / 2.0

    def forward(self, value: float) -> float:
        import math
        s = self.settings
        a = (-value if s.direction == "clockwise" else value) + self._shift()
        a = max(-89.99, min(89.99, a))
        return s.cam_radius * math.sin(math.radians(a)) + s.wheel_radius + s.wheel_gap

    def inverse(self, value: float) -> float:
        import math
        s = self.settings
        q = (value - s.wheel_radius - s.wheel_gap) / s.cam_radius
        q = max(-math.sin(math.radians(89.99)), min(math.sin(math.radians(89.99)), q))
        a = math.degrees(math.asin(q)) - self._shift()
        if s.direction == "clockwise":
            a = -a
        limit = 180.0 - s.arms_angle / 2.0
        return max(-limit, min(limit, a))


# ── Heads, nozzles, nozzle tips ────────────────────────────────────────────────────────────────────────────────────

class HeadAxes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: str
    y: str


class HeadSettings(Settings):
    axes: HeadAxes
    pump: str | None = Field(None, description="Vacuum pump actuator id (see pump_control)")
    pump_control: Literal["none", "part_on", "task_duration", "keep_running"] = Field(
        "part_on", description="As OpenPnP's vacuum pump control. none: never switched; part_on: on while any "
                               "nozzle carries a part; task_duration: on from the first pick until the job ends; "
                               "keep_running: on from the first pick until the machine is disabled. In every mode "
                               "it is switched off when the machine is disabled")
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
    dynamic_safe_z: bool = Field(False, description="With a part on, safe Z is higher by the part's height (OpenPnP's "
                                                    "dynamic safe Z), within the safe zone")
    vacuum_sense: str | None = Field(None, description="Readable actuator with this nozzle's vacuum level (OpenPnP's "
                                                       "vacuum sense actuator): part-on / part-off checks with the "
                                                       "tip's ranges")
    blow_off: str | None = Field(None, description="Actuator that blows the part off (OpenPnP's blow-off): on after "
                                                   "the vacuum valve closes, for the place dwell, then off")


class HeadMountable(MachineObject):
    head: Head

    def head_location(self, z: float | None, rotation: float | None) -> dict:
        off = self.settings.offset
        loc = {"x": self.head.x.position + (off.x or 0.0), "y": self.head.y.position + (off.y or 0.0)}
        if z is not None:
            loc["z"] = z + (off.z or 0.0)        # as OpenPnP, the head offset applies to Z too
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
        self._status.update(location=self.compute_location(), tip=s.tip, part=None, part_rotation=None,
                            part_height=None, part_speed=None, vacuum=s.vacuum,
                            vacuum_value=None, in_safe_zone=self.in_safe_zone())

    def axis_location(self) -> dict:
        """Where the nozzle's rotation axis is (no tip runout)."""
        return self.head_location(self.z.position, self.rotation.position)

    def compute_location(self) -> dict:
        """Where the tip is: the axis plus the calibrated runout at the current rotation (as OpenPnP reports a
        compensated nozzle). Moves put the tip at the target, so a jog from here moves the tip by exactly the step;
        starting from the axis instead made every jog move by the step minus the runout (drift on the bench)."""
        loc = self.axis_location()
        dx, dy = self.runout_offset(self.rotation.position)
        loc["x"] += dx
        loc["y"] += dy
        return loc

    def tip_id(self) -> str | None:
        """The loaded nozzle tip: what a tip change loaded (state store, survives restarts), else the configured
        `tip` (the one loaded at start)."""
        store = getattr(self.machine, "state_store", None)
        loaded = store.state_get(f"nozzle_loaded_tip/{self.id}") if store is not None else None
        if loaded is not None:
            return loaded.get("tip")
        return self.settings.tip

    def set_tip(self, tip: str | None) -> None:
        """Record the loaded tip (after a change); None = no tip loaded."""
        self.machine.state_store.state_set(f"nozzle_loaded_tip/{self.id}", {"tip": tip})
        self.update(tip=tip)

    def calibration_key(self, tip: str | None = None) -> str | None:
        """As OpenPnP, a runout calibration belongs to the tip on this nozzle (the same tip on another nozzle runs
        out differently)."""
        tip = tip or self.tip_id()
        return f"tip_calibration/{self.id}/{tip}" if tip else None

    def runout(self) -> dict | None:
        """The loaded tip's calibration (nozzle tip runout) on this nozzle, if any: {runout, phase, ...}."""
        key = self.calibration_key()
        if key is None:
            return None
        cal = self.machine.state_store.state_get(key)
        if cal is None:                    # saved per tip before 2026-11-25: still good for the nozzle it was made on
            old = self.machine.state_store.state_get(f"tip_calibration/{self.tip_id()}")
            if old and old.get("nozzle") == self.id:
                cal = old
        return cal

    def runout_enabled(self) -> bool:
        tip = self.machine.objects.get(f"nozzle_tip/{self.tip_id()}") if self.tip_id() else None
        return bool(tip is not None and tip.settings.calibration.enabled)

    def runout_offset(self, rotation: float) -> tuple[float, float]:
        """Where the tip is relative to the rotation axis at this nozzle rotation (0, 0 if not calibrated)."""
        import math
        cal = self.runout()
        if not cal or not cal.get("enabled", True) or not self.runout_enabled():
            return 0.0, 0.0
        a = math.radians(rotation + cal["phase"])
        return cal["runout"] * math.cos(a), cal["runout"] * math.sin(a)

    def in_safe_zone(self) -> bool | None:
        raw = self.z.input if isinstance(self.z, MappedAxis) else self.z
        zone = raw.status().get("safe_zone")
        if not zone:
            return None
        return zone[0] - 1e-6 <= raw.position <= zone[1] + 1e-6

    def refresh(self) -> None:
        self.update(location=self.compute_location(), in_safe_zone=self.in_safe_zone())


class TipCalibrationSettings(BaseModel):
    """As OpenPnP's nozzle tip calibration: off by default; when on, measured runout is compensated."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(False, description="Compensate the measured runout (and allow automatic runs)")
    recalibrate: Literal["manual", "machine_home", "tip_change"] = Field(
        "manual", description="When it is measured again: by hand, after every homing (OpenPnP's MachineHome) or "
                              "after the tip is loaded (OpenPnP's NozzleTipChange)")
    allow_misdetections: int = Field(0, ge=0, description="Pictures that may miss the tip (OpenPnP's default 0)")
    offset_threshold: Length = Field(0.5, gt=0, description="A tip found farther than this from the camera centre "
                                                            "doesn't count (OpenPnP's offset threshold)")


class TipVacuumSettings(BaseModel):
    """As OpenPnP's nozzle tip vacuum sensing (Absolute method): the vacuum level read after a pick must be in
    part_on, after a place in part_off. A check without its range (or a nozzle without vacuum_sense) is skipped."""
    model_config = ConfigDict(extra="forbid")
    part_on: tuple[float, float] | None = Field(None, description="Vacuum level with a part on the nozzle (low, high)")
    part_off: tuple[float, float] | None = Field(None, description="Vacuum level with no part (low, high)")
    check_after_pick: bool = True
    check_after_place: bool = True


class ChangerActuation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actuator: str
    value: Any = True


class TipChanger(BaseModel):
    """As OpenPnP's nozzle tip changer: the nozzle's path into the holder. Load: start -> middle1 -> middle2 -> end;
    unload: the other way. Speeds per segment (fraction of the feed)."""
    model_config = ConfigDict(extra="forbid")
    start: Location
    middle1: Location
    middle2: Location
    end: Location
    start_to_middle1_speed: float = Field(1.0, gt=0, le=1)
    middle1_to_middle2_speed: float = Field(1.0, gt=0, le=1)
    middle2_to_end_speed: float = Field(1.0, gt=0, le=1)
    after_middle1: ChangerActuation | None = Field(None, description="Actuated on reaching middle 1 (OpenPnP's post "
                                                                     "step one), e.g. a holder latch")
    after_middle2: ChangerActuation | None = None
    after_end: ChangerActuation | None = None


class NozzleTipSettings(Settings):
    max_part_diameter: Length | None = None
    max_part_height: Length | None = None
    diameter: Length | None = Field(None, description="Outer diameter of the tip at its end (OpenPnP's "
                                                       "diameter_low); used by board protection")
    pick_dwell: DurationMs = Field(0.0, description="Added to the nozzle's pick dwell (as OpenPnP)")
    place_dwell: DurationMs = Field(0.0, description="Added to the nozzle's place dwell (as OpenPnP)")
    calibration: TipCalibrationSettings = Field(default_factory=TipCalibrationSettings)
    vacuum: TipVacuumSettings = Field(default_factory=TipVacuumSettings)
    changer: TipChanger | None = Field(None, description="Where the tip is changed automatically; without it a change "
                                                         "is done by hand (the job pauses and asks)")


@registry.register("nozzle_tip", "nozzle_tip", NozzleTipSettings, "Nozzle tip")
class NozzleTip(MachineObject):
    kind = "nozzle_tip"

    def setup(self) -> None:
        self._status.update(loaded_on=None, calibrated=False)


# ── Actuators ──────────────────────────────────────────────────────────────────────────────────────────────────────

ValueType = Literal["boolean", "double", "string"]


class SimulatedActuatorSettings(Settings):
    value: ValueType = "boolean"
    read_value: float | None = Field(None, description="What reading it returns (a simulated sensor); none: not "
                                                       "readable")
    on_value: float = Field(1.0, description="Number actuators: the value for on (OpenPnP's default on double)")
    off_value: float = Field(0.0, description="Number actuators: the value for off (OpenPnP's default off double)")
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
            # as OpenPnP: on/off on a number actuator (e.g. a PWM vacuum valve) is its on/off value
            on = value if isinstance(value, bool) else (value.lower() in _TRUE if isinstance(value, str)
                                                         and value.lower() in _TRUE | _FALSE else None)
            if on is not None:
                return float(getattr(self.settings, "on_value", 1.0) if on else getattr(self.settings, "off_value", 0.0))
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
    def read(self) -> Any:
        if self.settings.read_value is None:
            return super().read()
        return self.settings.read_value


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


class Gains(BaseModel):
    model_config = ConfigDict(extra="forbid")
    red: float = Field(1.0, gt=0, le=8)
    green: float = Field(1.0, gt=0, le=8)
    blue: float = Field(1.0, gt=0, le=8)


class ImageSize(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)


class PixelOffset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: int = 0
    y: int = 0


class ImageTransform(BaseModel):
    """Applied to every frame in OpenPnP's order: deinterlace, crop, white balance, (advanced calibration), scale,
    rotate, offset, flip."""
    model_config = ConfigDict(extra="forbid")
    deinterlace: bool = Field(False, description="Interlaced analog cameras: keep one field, lines doubled")
    crop: Crop | None = Field(None, description="Pixels of the sensor image to keep")
    white_balance: Gains | None = Field(None, description="Gain per colour channel (OpenPnP's manual white balance)")
    scale: ImageSize | None = Field(None, description="Resize the picture to this many pixels")
    offset: PixelOffset | None = Field(None, description="Shift the picture by this many pixels (after rotating)")
    # as OpenPnP's camera transforms (same meaning, so settings can be copied from it)
    rotate: float = Field(0.0, ge=-360, le=360, description="Degrees counter-clockwise, any angle (as OpenPnP). "
                                                             "Other than 90° steps the image grows to hold it all")
    flip_x: bool = Field(False, description="Flip about the X axis: top-bottom (OpenPnP's flipX, 'Flip Vertical')")
    flip_y: bool = Field(False, description="Flip about the Y axis: left-right (OpenPnP's flipY, "
                                            "'Flip Horizontal')")


class CalibrationFiducial(BaseModel):
    """A round mark at a known place (OpenPnP's calibration primary / secondary fiducial)."""
    model_config = ConfigDict(extra="forbid")
    x: float
    y: float
    z: float = Field(..., description="Its top surface")
    diameter: Length = 1.0


class AdvancedCalibrationSettings(BaseModel):
    """As OpenPnP's Advanced Camera Calibration: the machine moves a known point (the nozzle tip over an up-looking
    camera) along radial lines through the view at two heights; the lens distortion, the camera's tilt and its
    distance follow, and every frame is corrected into a 'virtual camera' image: undistorted, square to the machine,
    +X right and +Y up, at units_per_pixel on the primary Z (the camera location's Z). The measured model is kept in
    the state store (camera_calibration/<id>); this switches it on. While on, transform.rotate/flip don't apply (the
    model includes the orientation), crop still does (before)."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(False, description="Correct every frame with the measured model")
    radial_lines: int = Field(32, ge=4, le=128, description="Lines from the centre to the edge (OpenPnP: 32)")
    fill: float = Field(0.90, gt=0.2, le=1.0, description="How far towards the image edge to go (OpenPnP: 0.90)")
    secondary_z_offset: Length = Field(2.0, description="Up-looking cameras: the second height, mm above the "
                                                         "primary Z (the camera's location Z): gives the camera's "
                                                         "distance and mm/px at any Z")
    tangential: bool = Field(False, description="Model tangential distortion too (OpenPnP: off)")
    fiducial: CalibrationFiducial | None = Field(None, description="Down-looking cameras: the primary calibration "
                                                                   "fiducial, at the Z where boards are (its Z is "
                                                                   "the primary Z)")
    secondary_fiducial: CalibrationFiducial | None = Field(
        None, description="Down-looking cameras: a second fiducial at another height (e.g. on a block a few mm high): "
                          "gives the distance and mm/px at any Z")


class UnitsPerPixel3D(BaseModel):
    """As OpenPnP's 3D units per pixel: units_per_pixel holds at primary_z, `secondary` at secondary_z; between and
    beyond, linear in Z (the scale follows the distance from the camera)."""
    model_config = ConfigDict(extra="forbid")
    primary_z: float
    secondary_z: float
    secondary: Scale


class CameraCommon(Settings):
    looking: Literal["up", "down"]
    location: Location | None = Field(None, description="Machine-mounted cameras: fixed location")
    offset: Location | None = Field(None, description="Head-mounted cameras: offset from the head")
    axes: CameraAxes | None = None
    units_per_pixel: Scale | None = Field(None, description="mm per pixel in X and Y (after the transform)")
    units_per_pixel_3d: UnitsPerPixel3D | None = Field(None, description="mm per pixel at a second Z (OpenPnP's 3D "
                                                                         "units per pixel); not needed with "
                                                                         "advanced calibration on")
    transform: ImageTransform = Field(default_factory=ImageTransform)
    idle_close: DurationMs = Field(10000.0, description="Close the device this long after the last viewer left; "
                                                        "0 = keep it open once opened")
    jpeg_quality: int = Field(80, ge=10, le=100, description="For streams and snapshots")
    stream_fps: float = Field(15.0, gt=0, le=120, description="Default frame rate of MJPEG streams")
    warmup: DurationMs = Field(0.0, description="Drop frames for this long after opening the device (auto "
                                                "exposure and white balance settle)")
    settle: DurationMs = Field(250.0, description="settle_method fixed: vision waits this long after a move before "
                                                  "taking a picture (OpenPnP's default 250 ms)")
    settle_method: Literal["fixed", "motion"] = Field(
        "fixed", description="As OpenPnP's camera settling. fixed: wait `settle`; motion: take pictures until two in "
                             "a row differ by less than settle_threshold (the image stopped moving), at most "
                             "settle_timeout")
    settle_threshold: float = Field(2.0, gt=0, description="motion: mean difference between pictures, 0..255")
    settle_timeout: DurationMs = Field(1000.0, description="motion: take the picture anyway after this long")
    settle_debounce: int = Field(1, ge=0, le=10, description="motion: this many more still pictures in a row")
    roaming_radius: Length = Field(0.0, ge=0, description="Machine-mounted cameras: within this distance of the "
                                                           "camera's location, Shift+click moves the nozzle at its "
                                                           "depth (no safe Z), as OpenPnP's roaming radius")
    light: str | None = Field(None, description="Actuator id of the camera's light (OpenPnP's light actuator)")
    light_on_capture: bool = Field(True, description="Switch the light on before vision takes a picture (OpenPnP's "
                                                     "default)")
    light_off_after_capture: bool = Field(False, description="Switch it off again after the picture")
    light_on_user_action: bool = Field(True, description="Switch it on when the camera is moved by the user")
    light_off_for_other_cameras: bool = Field(False, description="Switch it off while another camera takes a picture "
                                                                 "(OpenPnP's anti-glare)")
    calibration: AdvancedCalibrationSettings = Field(default_factory=AdvancedCalibrationSettings)


OVERLAYS = ("crosshair", "scale", "grid", "info")

# grab(decode=False) returns this: the device delivered a frame, but nobody needed it decoded
SKIPPED = object()


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
        self._waiters = 0                           # threads in wait_frame (snapshots, streams)
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

    def grab(self, decode: "bool | Callable[[], bool]" = True) -> Any:
        """Block until the next frame. decode=True: return it (BGR numpy array); decode=False: return SKIPPED (keep
        the device going without the cost of decoding). A callable is asked once the frame is there (so a rate limit
        judges the frame's real arrival time). None if no frame came (the watchdog decides what then)."""
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
        last_decode = 0.0
        last_ok = time.monotonic()
        try:
            while not self._stop.is_set():
                # decode only when someone is waiting for a frame, and at most stream_fps times a second:
                # a USB camera nobody looks at then costs almost nothing (bench: 3 cameras took 2+ Pi cores)
                # Decided when the frame is there, not before waiting for it: deciding first skipped the frame that
                # arrived just in time, so only every 3rd-4th frame was decoded (7.8 instead of 15 fps on the bench).
                # A little slack, too: at 30 fps camera and 15 fps stream the frame 66.6 ms after the last must count.
                interval = 1.0 / self.settings.stream_fps

                def decode() -> bool:
                    t = time.monotonic()
                    return (t >= warm_until and (self._users > 0 or self._waiters > 0)
                            and t - last_decode >= interval - min(0.015, interval * 0.25))
                with self._cond:   # decide and mark stopped under the lock, so acquire() starts a new thread
                    if (self.settings.idle_close > 0 and self._users == 0
                            and time.monotonic() - self._last_use > self.settings.idle_close / 1000):
                        self._running = False
                        break
                try:
                    frame = self.grab(decode)
                except Exception as exc:
                    log.error("%s: %s", self.name, exc)
                    self.update(last_error=str(exc))
                    frame = None
                if frame is None:
                    # no frame: say so (a camera that dropped off USB must not look like it's streaming)
                    if self._status.get("streaming") and time.monotonic() - last_ok > 1.0:
                        self.update(streaming=False, fps=0.0)
                    if self._stop.wait(0.05):
                        break
                    continue
                last_ok = time.monotonic()
                now = time.monotonic()
                if now - t_fps >= 1.0:                     # fps = frames delivered to viewers
                    self.update(fps=round(frames / (now - t_fps), 1))
                    frames, t_fps = 0, now
                if frame is SKIPPED:
                    continue
                if now < warm_until:
                    continue                       # still settling after opening: nobody sees this frame
                last_decode = now
                frame = self.apply_transform(frame)
                with self._cond:
                    self._seq += 1
                    self._frames[self._seq] = frame
                    self._frames.pop(self._seq - self.KEEP_FRAMES, None)
                    for key in [k for k in self._jpeg if k[0] <= self._seq - self.KEEP_FRAMES]:
                        del self._jpeg[key]
                    self._cond.notify_all()
                frames += 1
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

    def capture(self, settle: float | None = None) -> Any:
        """On the motion thread: a picture for vision, as OpenPnP's settle-and-capture: the light on first (and other
        cameras' lights off, if they ask for it), the image settled (fixed time or until it stops moving), then a
        fresh frame. settle: ms, overriding a fixed settle time. The camera must be acquired."""
        m = self.machine
        s = self.settings
        if s.light and s.light_on_capture and m.objects.get(f"actuator/{s.light}") is not None:
            if m.get("actuator", s.light).status().get("value") is not True:
                m.actuate(s.light, True)
        for other in m.of_kind("camera"):
            os_ = other.settings
            if other is not self and os_.light and os_.light_off_for_other_cameras and os_.light != s.light:
                if m.get("actuator", os_.light).status().get("value") is not False:
                    m.actuate(os_.light, False)
        try:
            if s.settle_method == "motion" and settle is None:
                frame = self._settle_by_motion()
            else:
                time.sleep((s.settle if settle is None else settle) / 1000.0)
                got = self.wait_frame(self._seq, 5.0)
                if got is None:
                    raise MachineError("driver_error", f"{self.name}: no frame")
                frame = got[1]
        finally:
            if s.light and s.light_off_after_capture:
                m.actuate(s.light, False)
        return frame

    def _settle_by_motion(self) -> Any:
        """Pictures until the image stops changing (OpenPnP's Motion settle method, simplified: mean difference of
        small grey copies)."""
        import cv2
        import numpy as np
        s = self.settings
        deadline = time.monotonic() + s.settle_timeout / 1000.0
        prev, still, frame = None, 0, None
        while True:
            got = self.wait_frame(self._seq, 5.0)
            if got is None:
                raise MachineError("driver_error", f"{self.name}: no frame")
            frame = got[1]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
            small = cv2.resize(gray, (160, max(1, int(160 * gray.shape[0] / gray.shape[1]))),
                               interpolation=cv2.INTER_AREA).astype(np.float32)
            if prev is not None:
                diff = float(np.abs(small - prev).mean())
                still = still + 1 if diff < s.settle_threshold else 0
                if still > s.settle_debounce:
                    return frame
            prev = small
            if time.monotonic() > deadline:
                import logging
                logging.getLogger("tinkerpnp.camera").debug("%s: settle timeout", self.name)
                return frame

    def units_per_pixel_at(self, z: float | None = None) -> tuple[float, float] | None:
        """mm per pixel for something at machine Z (None: the primary / default Z). With advanced calibration on,
        from its model; else from units_per_pixel_3d if set; else units_per_pixel."""
        s = self.settings
        if s.units_per_pixel is None:
            return None
        base = (s.units_per_pixel.x, s.units_per_pixel.y)
        if z is None:
            return base
        model = self.calibration_model() if s.calibration.enabled else None
        if model:
            from .camera_calibration import units_per_pixel_at
            f = units_per_pixel_at(model, z) / model["units_per_pixel"]
            return base[0] * f, base[1] * f
        d3 = s.units_per_pixel_3d
        if d3 is not None and d3.secondary_z != d3.primary_z:
            t = (z - d3.primary_z) / (d3.secondary_z - d3.primary_z)
            return (base[0] + (d3.secondary.x - base[0]) * t, base[1] + (d3.secondary.y - base[1]) * t)
        return base

    def calibration_model(self) -> dict | None:
        """The measured advanced calibration (camera_calibration/<id> in the state store), if any."""
        store = getattr(self.machine, "state_store", None)
        return store.state_get(f"camera_calibration/{self.id}") if store is not None else None

    def calibration_active(self) -> bool:
        return bool(self.settings.calibration.enabled and self.calibration_model())

    def _calibration_maps(self, model: dict, size: tuple[int, int]) -> Any:
        """Remap tables raw -> virtual camera, built once per model and image size."""
        key = (model.get("time"), size)
        cached = self.__dict__.get("_cal_maps")
        if cached and cached[0] == key:
            return cached[1]
        from .camera_calibration import rectify_maps
        maps = rectify_maps(model, size)
        self.__dict__["_cal_maps"] = (key, maps)
        return maps

    def apply_transform(self, frame: Any) -> Any:
        import cv2
        import numpy as np
        t = self.settings.transform
        if t.deinterlace:                              # one field, lines doubled
            frame = cv2.resize(frame[::2], (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_LINEAR)
        if t.crop is not None:
            c = t.crop
            frame = frame[c.y:c.y + c.height, c.x:c.x + c.width]
        if t.white_balance is not None and frame.ndim == 3:
            g = t.white_balance
            frame = np.clip(frame.astype(np.float32) * np.array([g.blue, g.green, g.red], np.float32), 0,
                            255).astype(np.uint8)
        if getattr(self, "_raw_frames", False):        # advanced calibration collecting points: no geometry
            return frame
        model = self.calibration_model() if self.settings.calibration.enabled else None
        if model:                                      # the virtual camera: orientation included, no rotate/flip
            h, w = frame.shape[:2]
            m1, m2 = self._calibration_maps(model, (w, h))
            # outside the sensor: the edge repeated (black would throw off dark-threshold vision)
            return cv2.remap(frame, m1, m2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        if t.scale is not None:
            frame = cv2.resize(frame, (t.scale.width, t.scale.height), interpolation=cv2.INTER_AREA)
        angle = t.rotate % 360
        if angle:
            quarter = {90: cv2.ROTATE_90_COUNTERCLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_CLOCKWISE}
            if angle in quarter:
                frame = cv2.rotate(frame, quarter[angle])
            else:                               # as OpenPnP: counter-clockwise, the image grows to its bounding box
                h, w = frame.shape[:2]
                m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
                cos, sin = abs(m[0, 0]), abs(m[0, 1])
                nw, nh = int(round(h * sin + w * cos)), int(round(h * cos + w * sin))
                m[0, 2] += nw / 2 - w / 2
                m[1, 2] += nh / 2 - h / 2
                frame = cv2.warpAffine(frame, m, (nw, nh))
        if t.offset is not None and (t.offset.x or t.offset.y):
            h, w = frame.shape[:2]
            frame = cv2.warpAffine(frame, np.float32([[1, 0, t.offset.x], [0, 1, t.offset.y]]), (w, h),
                                   borderMode=cv2.BORDER_REPLICATE)
        # flip_x is about the X axis (top-bottom), flip_y about the Y axis (left-right): OpenPnP's meaning
        if t.flip_x and t.flip_y:
            frame = cv2.flip(frame, -1)
        elif t.flip_x:
            frame = cv2.flip(frame, 0)
        elif t.flip_y:
            frame = cv2.flip(frame, 1)
        return frame

    def wait_frame(self, after: int, timeout: float) -> tuple[int, Any] | None:
        """The newest frame with seq > after, waiting up to timeout seconds. None if none came."""
        deadline = time.monotonic() + timeout
        with self._cond:
            self._waiters += 1
            try:
                while self._seq <= after or self._seq not in self._frames:
                    left = deadline - time.monotonic()
                    if left <= 0 or (not self._running and self._status.get("last_error")):
                        return None
                    self._cond.wait(min(left, 0.25))
                return self._seq, self._frames[self._seq]
            finally:
                self._waiters -= 1

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
    def jpeg(self, seq: int, frame: Any, overlays: tuple[str, ...] = (), quality: int | None = None,
             scale: float | None = None) -> bytes:
        """The frame as JPEG. quality (10-100) and scale (0.1-1, downscale) override the camera's settings, e.g. for a
        low-bandwidth stream; overlays are drawn at full size first."""
        import cv2
        quality = int(min(max(quality or self.settings.jpeg_quality, 10), 100))
        scale = min(max(scale or 1.0, 0.1), 1.0)
        key = (seq, overlays, quality, scale)
        cached = self._jpeg.get(key)
        if cached is not None:
            return cached
        if overlays:
            frame = self.draw_overlays(frame.copy(), overlays)
        if scale < 1.0:
            h, w = frame.shape[:2]
            frame = cv2.resize(frame, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
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


# The feeder types are in feeders.py.
