import os
import textwrap

import pytest

from tinkerpnp.core.config import ConfigError, load_config
from tinkerpnp.core.machine import Machine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIMULATED = os.path.join(ROOT, "config", "examples", "simulated.yaml")
REAL = os.path.join(ROOT, "docs", "examples", "mc5s3dp-xhat.yaml")


def write(tmp_path, text, name="machine.yaml"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(text))
    return str(p)


def test_simulated_example_loads():
    cfg = load_config(SIMULATED)
    assert cfg.ok
    assert {o.id for o in cfg.of_kind("nozzle")} == {"N1", "N2"}
    z = cfg.find("axis", "x")
    assert z.settings.feed == pytest.approx(8000 / 60)


def test_real_machine_example_only_misses_the_servo_plugin():
    cfg = load_config(REAL, strict=False)
    messages = [f"{p.path}: {p.message}" for p in cfg.problems]
    assert all("servo" in m or "tinkerpnp-servo-hat" in m for m in messages), messages
    assert len(messages) == 3


def test_problems_are_collected_with_paths(tmp_path):
    path = write(tmp_path, """
        format: tinkerpnp-machine/1
        drivers:
          d: {type: simulated, colour: red}
        axes:
          x: {type: controller, driver: nope, letter: X}
          z2: {type: mapped, input: missing, map: [[0, 1], [0, 2]]}
          q: {type: warp}
        bogus: 1
    """)
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    paths = {p.path for p in exc.value.problems}
    assert "drivers.d.colour" in paths            # unknown key
    assert "axes.x.driver" in paths               # dangling reference
    assert "axes.z2.input" in paths
    assert "axes.z2.map" in paths                 # degenerate map
    assert "axes.q.type" in paths                 # unknown type
    assert "bogus" in paths                       # unknown top-level key


def test_x_keys_are_ignored_and_includes_merge(tmp_path):
    (tmp_path / "feeders").mkdir()
    write(tmp_path, """
        feeders:
          F9: {type: static, pick_location: {x: 1, y: 2, z: 3}, x-note: kept for humans}
    """, name="feeders/bank.yaml")
    path = write(tmp_path, """
        format: tinkerpnp-machine/1
        include: [feeders/*.yaml]
        x-comment: anything
        drivers:
          d: {type: simulated}
    """)
    cfg = load_config(path)
    assert cfg.find("feeder", "F9") is not None


def test_shared_z_mapping_and_safe_zone():
    m = Machine(load_config(SIMULATED))
    q = m.query({"nozzle/N1": ["location", "in_safe_zone"], "nozzle/N2": ["location"], "motion": ["safe_z"]})
    assert q["nozzle/N1"]["location"]["z"] == 57
    assert q["nozzle/N2"]["location"]["z"] == 57
    assert q["motion"]["safe_z"] is True
    m.set_axis_position("z", 6.0)
    q = m.query({"nozzle/N1": ["location", "in_safe_zone"], "nozzle/N2": ["location"], "motion": ["safe_z"]})
    assert q["nozzle/N1"]["location"]["z"] == 6.0
    assert q["nozzle/N2"]["location"]["z"] == pytest.approx(108.0)   # 114 - 6
    assert q["nozzle/N1"]["in_safe_zone"] is False
    assert q["motion"]["safe_z"] is False


def test_head_offsets():
    m = Machine(load_config(SIMULATED))
    m.set_axis_position("x", 100.0)
    m.set_axis_position("y", 50.0)
    q = m.query({"nozzle/N2": ["location"], "camera/top": ["location"], "camera/bottom": ["location"]})
    assert q["nozzle/N2"]["location"]["x"] == pytest.approx(133.2)
    assert q["camera/top"]["location"] == {"x": pytest.approx(121.9), "y": pytest.approx(70.0)}
    assert q["camera/bottom"]["location"]["x"] == 189.9       # machine-mounted: fixed


def test_status_changes_are_published():
    m = Machine(load_config(SIMULATED))
    seen = []
    m.add_listener(lambda name, delta: seen.append((name, delta)))
    m.set_axis_position("x", 10.0)
    names = {n for n, _ in seen}
    assert {"axis/x", "head/H1", "nozzle/N1", "nozzle/N2", "camera/top"} <= names
    assert "camera/bottom" not in names
