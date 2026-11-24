import os
import pty
import threading
import time

import pytest
from ruamel.yaml import YAML

from tinkerpnp.core.config import ConfigError, _plain, build_config
from tinkerpnp.core.errors import MachineError, Stopped
from tinkerpnp.core.gcode import compress, java_regex, render, strip_comments
from tinkerpnp.core.machine import Machine
from tinkerpnp.sim.grblhal import FakeGrblHAL

from conftest import tmp_config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "config", "examples", "simulated-grblhal.yaml")


def config_data(port="sim://grblhal", **driver):
    with open(EXAMPLE) as fh:
        data = _plain(YAML(typ="rt").load(fh))
    data["drivers"]["grbl"].update(port=port, **driver)
    return data


def make(port="sim://grblhal", data=None, **driver) -> Machine:
    data = data or config_data(port, **driver)
    return Machine(build_config(data, tmp_config(EXAMPLE)))


@pytest.fixture
def m():
    machine = make()
    yield machine
    machine.close()


def run(m, fn, *args):
    return m.submit(fn, *args).result(timeout=10)


def fake(m) -> FakeGrblHAL:
    return m.get("driver", "grbl").transport.fake


def pos(m, axis):
    return m.get("axis", axis).position


# ── templates ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_templates_follow_openpnp_syntax():
    t = "G1 {X:X%.4f} {Y:Y%.4f} {Z:Z%.4f} F{FeedRate:%.0f}"
    assert render(t, {"X": 10, "Z": 2.5, "FeedRate": 4800.4}) == "G1 X10.0000 Z2.5000 F4800"
    assert render(t, {"Y": -1.0}) == "G1 Y-1.0000 F"             # missing variables vanish with their text
    assert render("{True:M64 P0}{False:M65 P0}", {"True": True}) == "M64 P0"
    assert render("{True:M64 P0}{False:M65 P0}", {"False": True}) == "M65 P0"
    assert render("M3 S{IntegerValue}", {"IntegerValue": 750}) == "M3 S750"
    assert render("M42 P{Index} S{DoubleValue:%.2f}", {"Index": 3, "DoubleValue": 0.5}) == "M42 P3 S0.50"
    assert render("$H{Letter}", {"Letter": "Z"}) == "$HZ"
    assert render("G4 P0\nM400", {}) == "G4 P0\nM400"


def test_compress_comments_and_java_regex():
    assert compress("G1 X10.5000 Y10.0000 F3000") == "G1X10.5Y10F3000"
    assert strip_comments("G1 X1 (move) ; done") == "G1 X1"
    m = java_regex(r"^<\w+\|MPos:(?<X>-?\d+\.\d+),(?<Y>-?\d+\.\d+)").match("<Idle|MPos:1.500,-2.000|FS:0,0>")
    assert m.groupdict() == {"X": "1.500", "Y": "-2.000"}


def test_settings_are_checked():
    data = config_data(commands={"MOVE_COMMAND": "G1"})
    with pytest.raises(ConfigError) as e:
        build_config(data, EXAMPLE)
    assert "unknown command 'MOVE_COMMAND'" in str(e.value)
    data = config_data(commands={"POSITION_REPORT_REGEX": "^<(?<X>"})
    with pytest.raises(ConfigError) as e:
        build_config(data, EXAMPLE)
    assert "invalid regex" in str(e.value)
    data = config_data(profile="marlin9000")
    with pytest.raises(ConfigError):
        build_config(data, EXAMPLE)


# ── grblHAL simulator ─────────────────────────────────────────────────────────────────────────────────────────────

def test_enable_talks_grblhal_and_homes_and_parks(m):
    run(m, m.enable)
    f = fake(m)
    sent = f.received
    assert sent[:3] == ["$I", "", "$H"]                             # build info, Ctrl-X + newline, home
    assert "G1 Z57.0000 F3000" in sent                              # park: safe Z first ...
    assert "G1 X1.0000 Y1.0000 F" in " ".join(sent) or any(s.startswith("G1 X1.0000 Y1.0000") for s in sent)
    assert sent.index("G1 Z57.0000 F3000") < next(i for i, s in enumerate(sent) if s.startswith("G1 X1.0000"))
    assert m.get("driver", "grbl").status()["firmware"] == "[VER:1.1f.20251101:]"
    st = m.state.status()
    assert st["homed"] and st["state"] == "idle"
    assert (pos(m, "x"), pos(m, "y"), pos(m, "z")) == (1, 1, 57)
    assert f.pos["Z"] == 57 and f.state == "Idle"
    assert f.outputs == {"spindle": 0.0, "aux0": 0.0, "aux1": 0.0}   # actuators on_enable: off


def test_moves_offsets_and_feed(m):
    run(m, m.enable)
    f = fake(m)
    f.received.clear()
    run(m, m.planner.move_to, m.get("nozzle", "N2"), {"x": 133.2, "y": 101, "z": 10})
    assert f.received == ["G1 X100.0000 Y101.0000 F8443",   # Y (6000) limits: 6000 * 140.7 / 100
                           "G4 P0", "G1 Z104.0000 F3000", "G4 P0"]
    assert f.pos == {"X": 100, "Y": 101, "Z": 104, "A": 0, "B": 0}
    drv = m.get("driver", "grbl")
    x, y = m.get("axis", "x"), m.get("axis", "y")
    assert drv.feed_rate({x: x.position + 300}, 1.0) == pytest.approx(8000)
    assert drv.feed_rate({x: x.position + 300, y: y.position + 300}, 1.0) == pytest.approx(6000 * 2 ** 0.5)
    assert drv.feed_rate({x: x.position + 300}, 0.5) == pytest.approx(4000)


def test_rotation_by_whole_turns(m):
    run(m, m.enable)
    f = fake(m)
    f.received.clear()
    run(m, m.planner.move_to, m.get("nozzle", "N1"), {"rotation": 270})
    assert f.received[0] == "G1 A-90.0000 F3000"


def test_actuators(m):
    run(m, m.enable)
    run(m, m.actuate, "pump", "on")
    run(m, m.actuate, "valve2", True)
    assert fake(m).outputs == {"spindle": 1000.0, "aux0": 0.0, "aux1": 1.0}
    run(m, m.disable)
    assert fake(m).received[-1] == "$SLP" and fake(m).state == "Sleep"


def test_disable_then_enable_wakes_and_rehomes(m):
    run(m, m.enable)
    run(m, m.disable)
    assert not m.state.status()["homed"]
    run(m, m.enable)                                   # Ctrl-X wakes it; ALARM:11 after the reset isn't an error
    assert m.state.status()["state"] == "idle" and m.state.status()["homed"]


def test_controller_errors_become_api_errors(m):
    run(m, m.enable)
    fake(m).state = "Alarm"                            # e.g. a limit switch hit behind our back
    with pytest.raises(MachineError) as e:
        run(m, m.planner.move_to, m.get("nozzle", "N1"), {"x": 50})
    assert e.value.name == "driver_error" and e.value.data["response"] == "error:9"


def test_timeouts():
    machine = make(commands={"MOVE_TO_COMPLETE_REGEX": "^never$"}, move_timeout=300)
    try:
        run(machine, machine.enable)
    except MachineError as e:
        assert e.name == "driver_timeout"
    else:
        pytest.fail("expected a timeout")
    finally:
        machine.close()


def test_unsolicited_alarm_puts_the_machine_in_alarm(m):
    run(m, m.enable)
    m.get("driver", "grbl")._received("ALARM:1")       # hard limit
    st = m.state.status()
    assert st["state"] == "alarm" and st["alarm"] == "1" and not st["homed"]
    with pytest.raises(MachineError) as e:
        run(m, m.planner.move_to, m.get("nozzle", "N1"), {"x": 50})
    assert e.value.name == "alarm"
    run(m, m.home)                                     # homing clears it
    assert m.state.status()["state"] == "idle"


def test_stop_during_a_move_and_position_reports():
    machine = make(port="sim://grblhal?time_scale=1")
    try:
        run(machine, machine.enable)
        xs = []
        machine.add_listener(lambda name, d: xs.append(d["position"]) if name == "axis/x" and "position" in d
                             else None)
        moving = machine.submit(machine.planner.move_to, machine.get("nozzle", "N1"), {"x": 300})  # ~2.2 s
        time.sleep(0.8)
        machine.stop("operator")
        with pytest.raises(Stopped):
            moving.result(5)
        assert any(1 < x < 300 for x in xs)                     # live positions from ? reports while moving
        assert machine.get("actuator", "pump").status()["value"] is None   # the reset may have switched outputs
        f = fake(machine)
        time.sleep(0.1)
        assert f.state == "Alarm"                                 # feed hold + reset during motion: ALARM:3
        st = machine.state.status()
        assert st["state"] == "alarm" and not st["homed"]
        run(machine, machine.home)
        assert machine.state.status()["state"] == "idle" and pos(machine, "x") == 1
    finally:
        machine.close()


def test_home_one_axis(m):
    run(m, m.enable)
    fake(m).received.clear()
    run(m, m.home, ["z"])
    assert fake(m).received[0] == "$HZ"


def test_everything_is_overridable():
    data = config_data(letter_variables=False, compress_gcode=True,
                       commands={"MOVE_TO_COMMAND": "G0 {X:X%.2f} {Y:Y%.2f} {Z:Z%.2f} {Rotation:A%.1f} F{FeedRate:%.0f}",
                                 "ENABLE_COMMAND": "\\u0018", "CONNECT_COMMAND": "$I\n$I"},
                       backslash_escapes=True)
    data["axes"].pop("b")
    data["heads"]["H1"]["nozzles"]["N2"]["axes"]["rotation"] = "a"
    machine = make(data=data)
    try:
        run(machine, machine.enable)
        f = fake(machine)
        assert f.received[:4] == ["$I", "$I", "", "$H"]          # two-line command; \u0018 decoded
        f.received.clear()
        run(machine, machine.planner.move_to, machine.get("nozzle", "N1"), {"rotation": 45})
        assert f.received[0] == "G0A45F3000"                     # Rotation variable, compressed
    finally:
        machine.close()


def test_gcode_log(m):
    lines = []
    m.add_gcode_listener(lambda drv, direction, line: lines.append((direction, line)))
    run(m, m.enable)
    assert ("tx", "$I") in lines and ("rx", "[VER:1.1f.20251101:]") in lines and ("rx", "ok") in lines


def test_over_a_real_serial_port():
    """The same through pyserial on a pseudo-terminal, as on /dev/serial0."""
    master, slave = pty.openpty()
    import tty
    tty.setraw(slave)
    out_lock = threading.Lock()

    def emit(line):
        with out_lock:
            os.write(master, (line + "\r\n").encode())
    grbl = FakeGrblHAL(emit)
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            try:
                data = os.read(master, 1024)
            except OSError:
                return
            grbl.feed(data)
    threading.Thread(target=pump, daemon=True).start()
    machine = make(port=os.ttyname(slave))
    try:
        run(machine, machine.enable)
        run(machine, machine.planner.move_to, machine.get("nozzle", "N1"), {"x": 42, "y": 24})
        assert grbl.pos["X"] == 42 and grbl.pos["Y"] == 24
        assert machine.get("driver", "grbl").status()["connected"]
    finally:
        machine.close()
        stop.set()
        grbl.close()
        os.close(master)


def test_second_ok_after_homing_is_dropped(m):
    """grblHAL answers $H with two oks; with `settle` the second is dropped before the next command."""
    drv = m.get("driver", "grbl")
    drv.connect()
    drv.command("HOME_COMMAND", timeout=5000)
    time.sleep(0.05)
    assert drv._rx.empty()                                   # nothing left to confirm the next command early
    drv.opt["settle"] = {}                                    # without settle the stray ok is left waiting
    drv.command("HOME_COMMAND", timeout=5000)
    time.sleep(0.05)
    assert drv._drain() == ["ok"]


def test_actuator_without_a_command_refuses(m):
    data = config_data()
    data["actuators"]["pump"].pop("on")
    machine = make(data=data)
    try:
        run(machine, machine.enable)
        with pytest.raises(MachineError) as e:
            run(machine, machine.actuate, "pump", True)
        assert "no on command" in e.value.message
        assert machine.get("actuator", "pump").status()["value"] is False      # still off, as it really is
    finally:
        machine.close()


def test_serial_port_is_opened_exclusively():
    """Two servers on one serial port split the replies between them (seen on the bench): the second must fail."""
    import tty
    master, slave = pty.openpty()
    tty.setraw(slave)
    from tinkerpnp.core.gcode import SerialTransport
    first = SerialTransport(os.ttyname(slave), 115200)
    try:
        with pytest.raises(Exception) as e:
            SerialTransport(os.ttyname(slave), 115200)
        assert "lock" in str(e.value).lower() or "busy" in str(e.value).lower() or "resource" in str(e.value).lower()
    finally:
        first.close()
        os.close(master)


def test_closing_an_idle_machine_sends_no_stop_sequence(m):
    """Stopping the server must not soft-reset an idle controller."""
    run(m, m.enable)
    lines = []
    m.add_gcode_listener(lambda drv, direction, line: lines.append(line))
    m.close()
    assert "\x18" not in lines and "!" not in lines


def test_macros(m):
    run(m, m.enable)
    f = fake(m)
    run(m, m.run_macro, "N2 to bottom camera")
    assert m.get("nozzle", "N2").compute_location()["x"] == pytest.approx(189.9)
    run(m, m.run_macro, "Pick test N1")
    assert f.outputs["spindle"] == 1000.0 and f.outputs["aux0"] == 1.0 and pos(m, "z") == 57
    f.received.clear()
    run(m, m.run_macro, "Status report")
    with pytest.raises(MachineError) as e:
        run(m, m.run_macro, "nope")
    assert e.value.name == "not_found"
    last = m.motion.status()["last_move"]
    assert last["requested"]["z"] == 30 and last["object"] == "nozzle/N1"


def test_macro_steps_are_validated():
    data = config_data()
    data["machine"]["macros"] = {"bad": [{"move_to": {"object": "nozzle/N9", "location": {"x": 1}}}, {"fly": 1}]}
    with pytest.raises(ConfigError) as e:
        build_config(data, EXAMPLE)
    paths = [p.path for p in e.value.problems]
    assert paths == ["machine.macros.bad[0].move_to.object", "machine.macros.bad[1]"]
