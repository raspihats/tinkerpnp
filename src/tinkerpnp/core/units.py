"""Quantities with units in configuration values.

Internally TinkerPnP uses millimetres, degrees, seconds (speeds in mm/s or deg/s, accelerations in mm/s^2), and
milliseconds for short durations. Configuration values may be plain numbers (already in the internal unit) or
strings with a unit: "2 in", "8000 mm/min", "3000 deg/min", "0.5 s".
"""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import BeforeValidator

LENGTH_UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "um": 0.001, "in": 25.4, "mil": 0.0254}
ANGLE_UNITS = {"deg": 1.0, "rad": 57.29577951308232, "turn": 360.0, "rev": 360.0}
TIME_UNITS = {"s": 1.0, "ms": 0.001, "min": 60.0}

_QUANTITY = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-z/^0-9]*)\s*$")


class UnitError(ValueError):
    pass


def _split(value) -> tuple[float, str]:
    if isinstance(value, bool):
        raise UnitError(f"expected a number, got {value!r}")
    if isinstance(value, (int, float)):
        return float(value), ""
    if isinstance(value, str):
        m = _QUANTITY.match(value)
        if m:
            return float(m.group(1)), m.group(2)
    raise UnitError(f"expected a number or a number with a unit, got {value!r}")


def _factor(unit: str, table: dict[str, float], what: str) -> float:
    if unit not in table:
        raise UnitError(f"unknown {what} unit {unit!r} (known: {', '.join(table)})")
    return table[unit]


def parse_length(value) -> float:
    """-> millimetres"""
    number, unit = _split(value)
    return number if not unit else number * _factor(unit, LENGTH_UNITS, "length")


def parse_angle(value) -> float:
    """-> degrees"""
    number, unit = _split(value)
    return number if not unit else number * _factor(unit, ANGLE_UNITS, "angle")


def parse_length_or_angle(value) -> float:
    """For values whose meaning depends on the axis kind (linear: mm, rotation: deg)."""
    number, unit = _split(value)
    if not unit:
        return number
    if unit in LENGTH_UNITS:
        return number * LENGTH_UNITS[unit]
    return number * _factor(unit, ANGLE_UNITS, "length or angle")


def _rate(value, per_power: int) -> float:
    number, unit = _split(value)
    if not unit:
        return number
    if "/" not in unit:
        raise UnitError(f"expected a rate like 'mm/s' or 'mm/min', got unit {unit!r}")
    top, bottom = unit.split("/", 1)
    if top in LENGTH_UNITS:
        top_f = LENGTH_UNITS[top]
    else:
        top_f = _factor(top, ANGLE_UNITS, "length or angle")
    power = per_power
    if per_power == 2 and bottom.endswith("^2"):
        bottom = bottom[:-2]
    elif per_power == 2:
        raise UnitError(f"expected an acceleration like 'mm/s^2', got unit {unit!r}")
    bottom_f = _factor(bottom, TIME_UNITS, "time")
    return number * top_f / bottom_f ** power


def parse_speed(value) -> float:
    """-> mm/s or deg/s"""
    return _rate(value, 1)


def parse_acceleration(value) -> float:
    """-> mm/s^2 or deg/s^2"""
    return _rate(value, 2)


def parse_duration_ms(value) -> float:
    """-> milliseconds"""
    number, unit = _split(value)
    return number if not unit else number * _factor(unit, TIME_UNITS, "time") * 1000.0


Length = Annotated[float, BeforeValidator(parse_length)]
Angle = Annotated[float, BeforeValidator(parse_angle)]
LengthOrAngle = Annotated[float, BeforeValidator(parse_length_or_angle)]
Speed = Annotated[float, BeforeValidator(parse_speed)]
Acceleration = Annotated[float, BeforeValidator(parse_acceleration)]
DurationMs = Annotated[float, BeforeValidator(parse_duration_ms)]
