"""Feeder types. A feeder tells where the next part is (pick_location), can feed it (feed: e.g. advance a tape with an
actuator) and is told when a part was taken (picked: counters advance). Counters are kept in the machine's state
store (the data database), not in the configuration file: they change with every pick.

Pick locations are where the nozzle tip touches the part: X/Y of the part's centre, Z of its top, and rotation = the
part's angle as it lies in the feeder (0 = as in its footprint).
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .errors import MachineError, Stopped
from .objects import Location, MachineObject, Settings
from .registry import registry
from .units import DurationMs, Length


class FeederCommon(Settings):
    part: str | None = Field(None, description="Part id (from the parts list)")
    enabled: bool = True


class FeederBase(MachineObject):
    kind = "feeder"

    def setup(self) -> None:
        self._status.update(enabled=self.settings.enabled, part=self.settings.part, last_error=None)
        self.refresh_status()

    # counters in the state store
    @property
    def _key(self) -> str:
        return f"feeder/{self.id}"

    def index(self) -> int:
        state = self.machine.state_store.state_get(self._key) or {}
        return int(state.get("index", 0))

    def set_index(self, index: int) -> None:
        if index < 0:
            raise MachineError("invalid_params", "index must be 0 or more")
        self.machine.state_store.state_set(self._key, {"index": index})
        self.refresh_status()

    def refresh_status(self) -> None:
        try:
            loc = self.pick_location()
        except MachineError:
            loc = None
        self.update(enabled=self.settings.enabled, part=self.settings.part, pick_location=loc, index=self.index(),
                    feeds_left=self.remaining())

    # the feeder interface
    def remaining(self) -> int | None:
        """Parts left; None = unknown/unlimited."""
        return None

    def pick_location(self) -> dict:
        raise NotImplementedError

    def feed(self) -> None:
        """Make the next part ready (on the motion thread). Raises feeder_error if there is none."""
        if not self.settings.enabled:
            raise MachineError("feeder_error", f"{self.name} is disabled", feeder=self.id)
        left = self.remaining()
        if left is not None and left <= 0:
            raise MachineError("feeder_error", f"{self.name} is empty", feeder=self.id)

    def picked(self) -> None:
        """A part was taken."""
        self.set_index(self.index() + 1)


# ── static ─────────────────────────────────────────────────────────────────────────────────────────────────────────

class StaticFeederSettings(FeederCommon):
    pick_location: Location


@registry.register("feeder", "static", StaticFeederSettings, "Static feeder",
                   "Fixed pick location (loose part, single pocket); no feeding mechanism")
class StaticFeeder(FeederBase):
    def pick_location(self) -> dict:
        return self.settings.pick_location.as_dict()


# ── strip (tape on the bed) ────────────────────────────────────────────────────────────────────────────────────────

class XY(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: Length
    y: Length


class StripFeederSettings(FeederCommon):
    first: Location = Field(..., description="First pocket: X/Y of its centre, Z of the part top, rotation of the "
                                             "part in the tape")
    last: XY = Field(..., description="Last pocket's centre: gives the direction (and, with pitch, the count)")
    pitch: Length = Field(4.0, gt=0, description="Pocket spacing: 2, 4, 8, 12 mm ...")
    count: int | None = Field(None, ge=1, description="Parts in the strip; default: from first, last and pitch")


@registry.register("feeder", "strip", StripFeederSettings, "Strip feeder",
                   "Cut tape lying on the bed: picks pocket after pocket from the first towards the last")
class StripFeeder(FeederBase):
    def _geometry(self) -> tuple[float, float, int]:
        s = self.settings
        dx, dy = s.last.x - (s.first.x or 0), s.last.y - (s.first.y or 0)
        length = math.hypot(dx, dy)
        if length < 1e-6:
            raise MachineError("feeder_error", f"{self.name}: first and last pocket are the same", feeder=self.id)
        count = s.count or int(round(length / s.pitch)) + 1
        return dx / length, dy / length, count

    def remaining(self) -> int | None:
        try:
            _, _, count = self._geometry()
        except MachineError:
            return 0
        return max(0, count - self.index())

    def pick_location(self) -> dict:
        ux, uy, count = self._geometry()
        i = min(self.index(), count - 1)
        f = self.settings.first
        loc = f.as_dict()
        loc["x"] = (f.x or 0) + ux * self.settings.pitch * i
        loc["y"] = (f.y or 0) + uy * self.settings.pitch * i
        return loc


# ── tray ───────────────────────────────────────────────────────────────────────────────────────────────────────────

class TrayFeederSettings(FeederCommon):
    first: Location = Field(..., description="First part (row 1, column 1)")
    columns: int = Field(1, ge=1)
    rows: int = Field(1, ge=1)
    column_step: XY = Field(..., description="From one column to the next")
    row_step: XY = Field(..., description="From one row to the next")


@registry.register("feeder", "tray", TrayFeederSettings, "Tray feeder",
                   "Parts in a grid (JEDEC tray, waffle pack): row by row, column by column")
class TrayFeeder(FeederBase):
    def remaining(self) -> int | None:
        return max(0, self.settings.columns * self.settings.rows - self.index())

    def pick_location(self) -> dict:
        s = self.settings
        i = min(self.index(), s.columns * s.rows - 1)
        row, col = divmod(i, s.columns)
        loc = s.first.as_dict()
        loc["x"] = (s.first.x or 0) + col * s.column_step.x + row * s.row_step.x
        loc["y"] = (s.first.y or 0) + col * s.column_step.y + row * s.row_step.y
        return loc


# ── auto (an actuator feeds) ───────────────────────────────────────────────────────────────────────────────────────

class AutoFeederSettings(FeederCommon):
    pick_location: Location
    feed_actuator: str | None = Field(None, description="Actuator that feeds the next part")
    feed_value: Any = Field(True, description="Value that feeds (e.g. on, or a servo angle)")
    feed_time: DurationMs = Field(0.0, description="Wait after feeding (the mechanism moves)")
    release_value: Any = Field(None, description="Value set after feed_time (e.g. off, or the servo's rest angle)")
    post_pick_actuator: str | None = None
    post_pick_value: Any = None
    count: int | None = Field(None, ge=1, description="Parts loaded; none = unknown")


@registry.register("feeder", "auto", AutoFeederSettings, "Auto feeder",
                   "Feeds with an actuator (motorized tape feeder, servo feeder, vibrating tube): fixed pick location")
class AutoFeeder(FeederBase):
    def remaining(self) -> int | None:
        return None if self.settings.count is None else max(0, self.settings.count - self.index())

    def pick_location(self) -> dict:
        return self.settings.pick_location.as_dict()

    def feed(self) -> None:
        super().feed()
        s = self.settings
        if s.feed_actuator:
            self.machine.actuate(s.feed_actuator, s.feed_value)
            if s.feed_time and self.machine.stop_event.wait(s.feed_time / 1000.0):
                raise Stopped()
            if s.release_value is not None:
                self.machine.actuate(s.feed_actuator, s.release_value)

    def picked(self) -> None:
        super().picked()
        s = self.settings
        if s.post_pick_actuator:
            self.machine.actuate(s.post_pick_actuator, s.post_pick_value)
