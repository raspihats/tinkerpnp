import pytest

from tinkerpnp.core.units import (UnitError, parse_acceleration, parse_angle, parse_duration_ms, parse_length,
                                  parse_speed)


def test_plain_numbers_are_internal_units():
    assert parse_length(5) == 5.0
    assert parse_speed(12.5) == 12.5


def test_lengths():
    assert parse_length("2 in") == pytest.approx(50.8)
    assert parse_length("1.5mm") == 1.5
    assert parse_length("-3 cm") == -30.0


def test_speeds_and_rates():
    assert parse_speed("8000 mm/min") == pytest.approx(133.3333, rel=1e-4)
    assert parse_speed("3000 deg/min") == pytest.approx(50.0)
    assert parse_speed("2 in/s") == pytest.approx(50.8)
    assert parse_acceleration("600 mm/s^2") == 600.0


def test_angles_and_durations():
    assert parse_angle("1 turn") == 360.0
    assert parse_duration_ms("0.5 s") == 500.0


@pytest.mark.parametrize("bad", ["fast", "10 furlongs", "5 mm/fortnight", True, None])
def test_bad_values(bad):
    with pytest.raises(UnitError):
        parse_speed(bad) if isinstance(bad, str) and "/" in bad else parse_length(bad)
