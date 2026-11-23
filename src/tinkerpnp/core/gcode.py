"""The `gcode` driver: any controller that takes G-code over a serial port or TCP (docs/drivers.md).

Configured like OpenPnP's GcodeDriver: the same command names (`MOVE_TO_COMMAND`, `COMMAND_CONFIRM_REGEX`, ...) and
the same template syntax (`{X:X%.4f}`, `{True:M64 P0}`), so commands from an OpenPnP `machine.xml` work unchanged.
Everything is configurable; a built-in `profile` only supplies defaults for what the config doesn't set.

Written from OpenPnP's documented file formats and observed behaviour, not from its source code.
"""
from __future__ import annotations

import codecs
import logging
import math
import queue
import re
import threading
import time
import urllib.parse
from typing import Any, Literal

from pydantic import Field, field_validator

from .errors import MachineError, Stopped
from .objects import ActuatorBase, ControllerAxis, DriverBase, Settings, ValueType
from .registry import registry
from .units import DurationMs, Speed

log = logging.getLogger("tinkerpnp.gcode")

# ── command names ──────────────────────────────────────────────────────────────────────────────────────────────────
# name -> (kind, description). kind: "command" (G-code template) or "regex".
COMMANDS: dict[str, tuple[str, str]] = {
    "CONNECT_COMMAND": ("command", "Sent once after the port opens"),
    "ENABLE_COMMAND": ("command", "Sent on machine.enable"),
    "DISABLE_COMMAND": ("command", "Sent on machine.disable (should release the motors)"),
    "HOME_COMMAND": ("command", "Home all axes"),
    "HOME_AXIS_COMMAND": ("command", "TinkerPnP: home one axis ({Letter}); without it, homing some axes homes all"),
    "HOME_COMPLETE_REGEX": ("regex", "Wait for this line after HOME_COMMAND"),
    "SET_GLOBAL_OFFSETS_COMMAND": ("command", "After homing: set the axes to their home positions (e.g. G92)"),
    "MOVE_TO_COMMAND": ("command", "A move; variables per axis, FeedRate, Acceleration"),
    "MOVE_TO_COMPLETE_COMMAND": ("command", "Answered when motion has finished (e.g. G4 P0 on grbl, M400 on Marlin)"),
    "MOVE_TO_COMPLETE_REGEX": ("regex", "Wait for this line after MOVE_TO_COMPLETE_COMMAND"),
    "GET_POSITION_COMMAND": ("command", "Ask for the position (answered by POSITION_REPORT_REGEX)"),
    "POSITION_REPORT_REGEX": ("regex", "Position report; one named group per axis (letter)"),
    "COMMAND_CONFIRM_REGEX": ("regex", "A command was accepted"),
    "COMMAND_ERROR_REGEX": ("regex", "A command failed"),
    "ALARM_REGEX": ("regex", "TinkerPnP: the controller went into alarm (group Alarm); position is lost"),
    "CLEAR_ALARM_COMMAND": ("command", "TinkerPnP: sent on machine.clear_alarm"),
    "ACTUATE_BOOLEAN_COMMAND": ("command", "For actuators without on/off: {True:...}{False:...}, {Index}, {Id}"),
    "ACTUATE_DOUBLE_COMMAND": ("command", "For actuators without set: {DoubleValue}, {IntegerValue}, {Index}"),
    "ACTUATE_STRING_COMMAND": ("command", "For actuators without set: {StringValue}"),
    "ACTUATOR_READ_COMMAND": ("command", "For actuators without read"),
    "ACTUATOR_READ_REGEX": ("regex", "For actuators without read_regex: group Value"),
}

PROFILE_FIELDS = ("letter_variables", "backslash_escapes", "line_ending", "connect_wait", "timeout", "home_timeout",
                  "move_timeout", "motion_control", "realtime", "stop", "cancel", "after_stop", "position_poll",
                  "location_confirmation", "remove_comments", "compress_gcode", "disconnect_on_disable")

_GENERIC = {
    "letter_variables": True, "backslash_escapes": False, "line_ending": "\n", "connect_wait": 0.0,
    "timeout": 5000.0, "home_timeout": 60000.0, "move_timeout": None, "motion_control": "toolpath",
    "realtime": [], "stop": [], "cancel": None, "after_stop": "idle", "position_poll": 0.0,
    "location_confirmation": False, "remove_comments": False, "compress_gcode": False,
    "disconnect_on_disable": False,
    "commands": {
        "COMMAND_CONFIRM_REGEX": "^ok.*",
        "COMMAND_ERROR_REGEX": "^(error|!!).*",
        "MOVE_TO_COMMAND": "G1 {X:X%.4f} {Y:Y%.4f} {Z:Z%.4f} {A:A%.4f} {B:B%.4f} {C:C%.4f} F{FeedRate:%.0f}",
        "MOVE_TO_COMPLETE_COMMAND": "M400",
        "HOME_COMMAND": "G28",
    },
}

_NUM = r"-?\d+(?:\.\d+)?"
PROFILES: dict[str, dict] = {
    "generic": _GENERIC,
    # grblHAL as on the MC5S3DP-XHAT (pnp-machine/grblHAL-bringup.md, grbl-commands.md)
    "grblhal": {
        **_GENERIC,
        "realtime": ["?", "!", "~", "\x85"],   # status report, feed hold, resume, jog cancel: no "ok"
        "stop": ["!", "\x18"],                  # feed hold, then soft reset
        "after_stop": "alarm",                  # a reset during motion loses the position (ALARM:3)
        "position_poll": 250.0,
        "connect_wait": 200.0,
        "home_timeout": 30000.0,
        "commands": {
            "CONNECT_COMMAND": "$I",            # build info: allowed in alarm, proves the link
            "ENABLE_COMMAND": "\x18",           # soft reset (wakes from sleep); reset + newline = exactly one ok
            "DISABLE_COMMAND": "$SLP",          # sleep: motors, spindle and outputs off
            "HOME_COMMAND": "$H",
            "HOME_AXIS_COMMAND": "$H{Letter}",
            "MOVE_TO_COMMAND": "G1 {X:X%.4f} {Y:Y%.4f} {Z:Z%.4f} {A:A%.4f} {B:B%.4f} {C:C%.4f} F{FeedRate:%.0f}",
            "MOVE_TO_COMPLETE_COMMAND": "G4 P0",   # dwell 0: answered when the planner is empty
            "GET_POSITION_COMMAND": "?",
            "POSITION_REPORT_REGEX": (rf"^<\w+(?::\d+)?\|MPos:(?<X>{_NUM}),(?<Y>{_NUM}),(?<Z>{_NUM})"
                                      rf"(?:,(?<A>{_NUM}))?(?:,(?<B>{_NUM}))?(?:,(?<C>{_NUM}))?.*"),
            "COMMAND_CONFIRM_REGEX": "^ok.*",
            "COMMAND_ERROR_REGEX": r"^(error:\d+|ALARM:(?!11\b)\d+).*",   # ALARM:11 (homing required) after reset
            "ALARM_REGEX": r"^ALARM:(?<Alarm>(?!11\b)\d+).*",
            "CLEAR_ALARM_COMMAND": "$X",
        },
    },
}


def java_regex(pattern: str) -> re.Pattern:
    """OpenPnP regexes use Java's named groups (?<name>...); Python wants (?P<name>...)."""
    return re.compile(re.sub(r"\(\?<([A-Za-z_]\w*)>", r"(?P<\1>", pattern))


# ── templates ──────────────────────────────────────────────────────────────────────────────────────────────────────
_VAR = re.compile(r"\{(\w+)(?::([^{}]*))?\}")


def render(template: str, variables: dict[str, Any]) -> str:
    """OpenPnP template syntax. `{Name}` -> value; `{Name:fmt}` -> fmt with the value (printf-style, e.g. "X%.4f");
    `{Name:text}` without % -> text. A variable that is missing or None removes the whole `{...}`."""
    def sub(m: re.Match) -> str:
        name, fmt = m.group(1), m.group(2)
        value = variables.get(name)
        if value is None or value is False and name in ("True", "False"):
            return ""
        if fmt is None:
            return _fmt_plain(value)
        if "%" not in fmt:
            return fmt
        try:
            return fmt % value
        except TypeError:
            return fmt % (int(round(value)),) if isinstance(value, float) else fmt % (str(value),)
    out = _VAR.sub(sub, template)
    return "\n".join(re.sub(r"[ \t]{2,}", " ", line).strip() for line in out.split("\n"))


def _fmt_plain(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def compress(line: str) -> str:
    """OpenPnP's "compress G-code": no spaces, no trailing zeros (X10.500 -> X10.5, X10.000 -> X10)."""
    line = line.replace(" ", "")
    line = re.sub(r"(\.\d*?)0+(?=[^\d]|$)", r"\1", line)
    return re.sub(r"\.(?=[^\d]|$)", "", line)


def strip_comments(line: str) -> str:
    return re.sub(r"\([^)]*\)", "", line.split(";")[0]).strip()


# ── transports ─────────────────────────────────────────────────────────────────────────────────────────────────────

class Transport:
    def write(self, data: bytes) -> None: ...

    def read(self) -> bytes:
        """Return what has arrived (b"" after a short wait if nothing); raise OSError if the link is gone."""
        return b""

    def close(self) -> None: ...


class SerialTransport(Transport):
    """Serial ports (/dev/ttyAMA0, /dev/serial0, COM3) and pyserial URLs; tcp://host:port is socket://host:port."""

    def __init__(self, port: str, baud: int):
        import serial  # pyserial
        url = "socket://" + port[len("tcp://"):] if port.startswith("tcp://") else port
        self.port = serial.serial_for_url(url, baudrate=baud, timeout=0.05, write_timeout=2.0)

    def write(self, data: bytes) -> None:
        self.port.write(data)
        self.port.flush()

    def read(self) -> bytes:
        return self.port.read(max(1, self.port.in_waiting))

    def close(self) -> None:
        self.port.close()


class SimTransport(Transport):
    """sim://grblhal?time_scale=1&home_time=2: the built-in grblHAL simulator (tinkerpnp.sim.grblhal)."""

    def __init__(self, url: str):
        from ..sim.grblhal import FakeGrblHAL
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc != "grblhal":
            raise MachineError("driver_error", f"unknown simulator {parsed.netloc!r} (known: grblhal)")
        opts = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        self._rx: queue.Queue[bytes] = queue.Queue()
        self.fake = FakeGrblHAL(lambda line: self._rx.put((line + "\r\n").encode("latin-1")),
                                axes=opts.get("axes", "XYZAB"), home_axes=opts.get("home_axes", "XYZ"),
                                homing_required=opts.get("homing_required", "1") not in ("0", "false"),
                                home_time=float(opts.get("home_time", 0)),
                                time_scale=float(opts.get("time_scale", 0)))

    def write(self, data: bytes) -> None:
        self.fake.feed(data)

    def read(self) -> bytes:
        try:
            return self._rx.get(timeout=0.05)
        except queue.Empty:
            return b""

    def close(self) -> None:
        self.fake.close()


def open_transport(port: str, baud: int) -> Transport:
    if port.startswith("sim://"):
        return SimTransport(port)
    return SerialTransport(port, baud)


# ── settings ───────────────────────────────────────────────────────────────────────────────────────────────────────

class GcodeDriverSettings(Settings):
    port: str = Field(..., description="Serial device (/dev/serial0), tcp://host:port, or sim://grblhal (simulator)")
    baud: int = 115200
    profile: Literal["generic", "grblhal"] = Field("generic", description="Defaults for everything below")
    commands: dict[str, str] = Field(default_factory=dict,
                                     description="OpenPnP command names and templates; override the profile's")
    letter_variables: bool | None = Field(None, description="Template variables named by axis letter (X, Y, Z, A, B)"
                                                            " instead of X, Y, Z, Rotation")
    backslash_escapes: bool | None = Field(None, description=r"Decode \u0018, \n, ... in commands "
                                                             "(for commands copied from OpenPnP)")
    line_ending: str | None = Field(None, description=r'Appended to every line: "\n" or "\r\n"')
    connect_wait: DurationMs | None = Field(None, description="Wait after opening the port (boards that reset)")
    timeout: DurationMs | None = Field(None, description="Wait this long for a command to be confirmed")
    home_timeout: DurationMs | None = Field(None, description="Timeout for homing")
    move_timeout: DurationMs | None = Field(None, description="Timeout for a move; default: expected time + timeout")
    max_feed: Speed | None = Field(None, description="Cap on the feed rate of any move")
    motion_control: Literal["toolpath", "slowest_axis"] | None = Field(
        None, description="toolpath: the fastest feed along the path at which no axis exceeds its own feed; "
                          "slowest_axis: the lowest feed of the moving axes")
    realtime: list[str] | None = Field(None, description="Commands sent as bytes without line ending and without "
                                                         "waiting for a confirmation (grbl: ?, !, ~)")
    stop: list[str] | None = Field(None, description="Sent in order on machine.stop, as bytes, without waiting")
    cancel: list[str] | None = Field(None, description="Sent on motion.jog_cancel; default: the stop sequence")
    after_stop: Literal["idle", "alarm"] | None = Field(None, description="State after stop; alarm = home again")
    position_poll: DurationMs | None = Field(None, description="Ask for the position this often while moving (0: "
                                                               "never); needs a real-time GET_POSITION_COMMAND")
    location_confirmation: bool | None = Field(None, description="Read the position after every move")
    remove_comments: bool | None = None
    compress_gcode: bool | None = Field(None, description="Remove spaces and trailing zeros")
    disconnect_on_disable: bool | None = Field(None, description="Close the port on machine.disable")

    @field_validator("commands")
    @classmethod
    def _check_commands(cls, v: dict[str, str]) -> dict[str, str]:
        for name, text in v.items():
            if name not in COMMANDS:
                raise ValueError(f"unknown command {name!r}; known: {', '.join(COMMANDS)}")
            if COMMANDS[name][0] == "regex":
                try:
                    java_regex(text)
                except re.error as exc:
                    raise ValueError(f"{name}: invalid regex: {exc}") from None
        return v


@registry.register("driver", "gcode", GcodeDriverSettings, "G-code controller",
                   "G-code controller over serial or TCP (grblHAL, Marlin, Smoothieware, Duet, TinyG, ...) with "
                   "OpenPnP-compatible commands and templates")
class GcodeDriver(DriverBase):

    # ── configuration ──────────────────────────────────────────────────────────────────────────────────────────
    def setup(self) -> None:
        s = self.settings
        profile = PROFILES[s.profile]
        self.opt = {f: getattr(s, f) if getattr(s, f) is not None else profile[f] for f in PROFILE_FIELDS}
        if self.opt["cancel"] is None:
            self.opt["cancel"] = self.opt["stop"]
        self.commands = {**profile["commands"], **s.commands}
        if self.opt["backslash_escapes"]:
            # commands only: regexes keep their backslashes (\d, \b)
            self.commands = {k: _unescape(v) if COMMANDS[k][0] == "command" else v
                             for k, v in self.commands.items()}
            for f in ("realtime", "stop", "cancel", "line_ending"):
                v = self.opt[f]
                self.opt[f] = [_unescape(x) for x in v] if isinstance(v, list) else _unescape(v)
        self.regex = {k: java_regex(v) for k, v in self.commands.items() if COMMANDS[k][0] == "regex" and v}
        self.transport: Transport | None = None
        self._reader: threading.Thread | None = None
        self._rx: queue.Queue[str] = queue.Queue()
        self._write_lock = threading.Lock()
        self._position_seen = threading.Event()
        self._last_position: dict[str, float] = {}
        self._closing = False
        self._status.update(connected=False, firmware=None, port=s.port, profile=s.profile, last_error=None,
                            alarm=None)

    def axes(self) -> list[ControllerAxis]:
        return [a for a in self.machine.of_kind("axis") if isinstance(a, ControllerAxis) and a.driver is self]

    def var_name(self, axis: ControllerAxis) -> str:
        if self.opt["letter_variables"] or axis.axis_kind != "rotation":
            return axis.settings.letter
        return "Rotation"

    # ── link ───────────────────────────────────────────────────────────────────────────────────────────────────
    def connect(self) -> None:
        if self.transport is not None:
            return
        try:
            self.transport = open_transport(self.settings.port, self.settings.baud)
        except MachineError:
            raise
        except Exception as exc:
            self.update(last_error=str(exc))
            raise MachineError("driver_error", f"{self.name}: can't open {self.settings.port}: {exc}",
                               driver=self.id) from None
        self._closing = False
        self._reader = threading.Thread(target=self._read_loop, name=f"gcode-{self.id}", daemon=True)
        self._reader.start()
        self.update(connected=True, last_error=None)
        if self.opt["connect_wait"]:
            self._sleep(self.opt["connect_wait"] / 1000.0)
        replies = self.command("CONNECT_COMMAND") or []
        firmware = next((r for r in replies if not self.regex["COMMAND_CONFIRM_REGEX"].match(r)), None)
        self.update(firmware=firmware)

    def disconnect(self) -> None:
        self._closing = True
        if self.transport is not None:
            try:
                self.transport.close()
            finally:
                self.transport = None
        self.update(connected=False)

    def _read_loop(self) -> None:
        buf = b""
        transport = self.transport
        while not self._closing and transport is not None:
            try:
                data = transport.read()
            except Exception as exc:
                if not self._closing:
                    log.error("%s: link lost: %s", self.name, exc)
                    self.update(connected=False, last_error=f"link lost: {exc}")
                    self.transport = None
                return
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").strip("\r\x00")
                if line:
                    self._received(line)

    def _received(self, line: str) -> None:
        self.machine.log_gcode(self.id, "rx", line)
        pos = self.regex.get("POSITION_REPORT_REGEX")
        if pos is not None:
            m = pos.match(line)
            if m:
                self._position_report(m)
        alarm = self.regex.get("ALARM_REGEX")
        if alarm is not None:
            m = alarm.match(line)
            if m:
                code = m.groupdict().get("Alarm") or line
                self.update(alarm=code)
                self.machine.driver_alarm(self, code, line)
        self._rx.put(line)

    def _position_report(self, m: re.Match) -> None:
        values = {k: float(v) for k, v in m.groupdict().items() if v is not None}
        self._last_position = values
        self._position_seen.set()
        positions = {a.id: values[self.var_name(a)] for a in self.axes() if self.var_name(a) in values}
        if positions:
            self.machine.set_axis_positions(positions)

    # ── sending ────────────────────────────────────────────────────────────────────────────────────────────────
    def _sleep(self, seconds: float) -> None:
        if self.machine.stop_event.wait(seconds):
            raise Stopped()

    def _write(self, text: str) -> None:
        if self.transport is None:
            raise MachineError("driver_error", f"{self.name} is not connected", driver=self.id)
        with self._write_lock:
            self.transport.write(text.encode("latin-1"))

    def write_raw(self, text: str) -> None:
        """Real-time bytes: no line ending, no confirmation. Safe from any thread."""
        self._write(text)
        self.machine.log_gcode(self.id, "tx", text)

    def send(self, line: str, timeout: float | None = None, poll: bool = False) -> list[str]:
        """Send one line and wait for COMMAND_CONFIRM_REGEX. Returns the lines received meanwhile.
        poll: ask for the position every position_poll ms while waiting (during moves)."""
        if self.opt["remove_comments"]:
            line = strip_comments(line)
        if self.opt["compress_gcode"]:
            line = compress(line)
        if line in self.opt["realtime"]:
            self.write_raw(line)
            return []
        while not self._rx.empty():          # drop what was left from earlier commands
            try:
                self._rx.get_nowait()
            except queue.Empty:
                break
        self._write(line + self.opt["line_ending"])
        self.machine.log_gcode(self.id, "tx", line)
        return self._wait_for(self.regex["COMMAND_CONFIRM_REGEX"], timeout, line, poll=poll)

    def _wait_for(self, pattern: re.Pattern, timeout: float | None, what: str,
                  poll: bool = False) -> list[str]:
        timeout = self.opt["timeout"] if timeout is None else timeout
        deadline = time.monotonic() + timeout / 1000.0
        error = self.regex.get("COMMAND_ERROR_REGEX")
        replies: list[str] = []
        interval = self.opt["position_poll"] / 1000.0 if poll else 0
        get_pos = self.commands.get("GET_POSITION_COMMAND")
        poll_ok = bool(interval and get_pos and get_pos in self.opt["realtime"])
        next_poll = time.monotonic() + interval
        while True:
            if self.machine.stop_event.is_set():
                raise Stopped()
            now = time.monotonic()
            if now > deadline:
                self.update(last_error=f"timeout waiting for {what!r}")
                raise MachineError("driver_timeout", f"{self.name}: no answer to {what!r} within {timeout:.0f} ms",
                                   driver=self.id, command=what)
            if poll_ok and now >= next_poll:
                self.write_raw(get_pos)
                next_poll = now + interval
            if self.transport is None:
                raise MachineError("driver_error", f"{self.name}: link lost", driver=self.id)
            try:
                line = self._rx.get(timeout=0.02)
            except queue.Empty:
                continue
            if pattern.match(line):
                replies.append(line)
                return replies
            if error is not None and error.match(line):
                self.update(last_error=line)
                raise MachineError("driver_error", f"{self.name}: {what!r} failed: {line}", driver=self.id,
                                   command=what, response=line)
            replies.append(line)

    def command(self, name: str, variables: dict[str, Any] | None = None, timeout: float | None = None,
                poll: bool = False) -> list[str] | None:
        """Render and send a configured command (each line on its own). None if the command isn't configured."""
        template = self.commands.get(name)
        if not template:
            return None
        text = render(template, variables or {})
        replies: list[str] = []
        for line in (ln for ln in text.split("\n") if ln):      # multi-line commands: one line at a time
            replies += self.send(line, timeout, poll=poll)
        return replies

    def read_position(self) -> dict[str, float] | None:
        """Ask for the position and wait for the report. Axis positions are updated by the report itself."""
        if not self.commands.get("GET_POSITION_COMMAND") or "POSITION_REPORT_REGEX" not in self.regex:
            return None
        self._position_seen.clear()
        self.command("GET_POSITION_COMMAND")
        deadline = time.monotonic() + self.opt["timeout"] / 1000.0
        while not self._position_seen.wait(0.02):
            if self.machine.stop_event.is_set():
                raise Stopped()
            if time.monotonic() > deadline:
                raise MachineError("driver_timeout", f"{self.name}: no position report", driver=self.id)
        return dict(self._last_position)

    # ── driver interface ───────────────────────────────────────────────────────────────────────────────────────
    def enable(self) -> None:
        self.update(alarm=None)
        self.command("ENABLE_COMMAND")
        self.read_position()
        self.update(last_error=None)          # an error from before the enable is history now

    def disable(self) -> None:
        if self.transport is None:
            return
        try:
            self.command("DISABLE_COMMAND")
        finally:
            if self.opt["disconnect_on_disable"]:
                self.disconnect()

    def home(self, axes: list[ControllerAxis]) -> None:
        mine = [a for a in self.axes() if a.homeable]
        timeout = self.opt["home_timeout"]
        if set(axes) != set(mine) and self.commands.get("HOME_AXIS_COMMAND"):
            for a in axes:
                self.command("HOME_AXIS_COMMAND", {"Letter": a.settings.letter, "Id": a.id}, timeout)
        else:
            axes = mine
            self.command("HOME_COMMAND", None, timeout)
        if "HOME_COMPLETE_REGEX" in self.regex:
            self._wait_for(self.regex["HOME_COMPLETE_REGEX"], timeout, "HOME_COMPLETE_REGEX")
        home_vars = {self.var_name(a): a.settings.home for a in axes}
        self.command("SET_GLOBAL_OFFSETS_COMMAND", home_vars)
        if self.read_position() is None:
            self.machine.set_axis_positions({a.id: a.settings.home for a in axes})
        for a in axes:
            a.update(homed=True)
        self.update(alarm=None)

    def feed_rate(self, targets: dict[ControllerAxis, float], speed: float) -> float | None:
        """Feed (mm/min) for a move: see motion_control. The firmware measures the path over all moving axes."""
        deltas = {a: abs(v - a.position) for a, v in targets.items()}
        deltas = {a: d for a, d in deltas.items() if d > 0}
        if not deltas:
            return None
        feeds = {a: a.settings.feed for a in deltas if a.settings.feed}
        if not feeds:
            feed = None
        elif self.opt["motion_control"] == "slowest_axis":
            feed = min(feeds.values())
        else:
            length = math.sqrt(sum(d * d for d in deltas.values()))
            feed = min(f * length / deltas[a] for a, f in feeds.items())
        if self.settings.max_feed:
            feed = min(feed, self.settings.max_feed) if feed else self.settings.max_feed
        return None if feed is None else feed * speed * 60.0

    def move(self, targets: dict[ControllerAxis, float], speed: float) -> None:
        feed = self.feed_rate(targets, speed)
        accels = [a.settings.acceleration for a in targets if a.settings.acceleration]
        variables: dict[str, Any] = {self.var_name(a): v for a, v in targets.items()}
        variables["FeedRate"] = feed
        variables["Acceleration"] = min(accels) * speed * speed if accels else None
        self.command("MOVE_TO_COMMAND", variables)
        timeout = self.opt["move_timeout"]
        if timeout is None:
            length = math.sqrt(sum((v - a.position) ** 2 for a, v in targets.items()))
            expected = length / (feed / 60.0) if feed else 0.0
            timeout = expected * 1500.0 + self.opt["timeout"]     # 1.5 x expected (acceleration) + timeout
        polling = self.opt["position_poll"] > 0
        self.command("MOVE_TO_COMPLETE_COMMAND", None, timeout, poll=polling)
        if "MOVE_TO_COMPLETE_REGEX" in self.regex:
            self._wait_for(self.regex["MOVE_TO_COMPLETE_REGEX"], timeout, "MOVE_TO_COMPLETE_REGEX")
        if not (self.opt["location_confirmation"] and self.read_position() is not None):
            self.machine.set_axis_positions({a.id: v for a, v in targets.items()})

    def actuate(self, actuator: ActuatorBase, value: Any) -> None:
        s = actuator.settings
        variables = {"Id": actuator.id, "Name": s.name or actuator.id, "Index": getattr(s, "index", None),
                     "Value": value, "True": True if value is True else None, "False": True if value is False else None,
                     "BooleanValue": value if isinstance(value, bool) else None,
                     "DoubleValue": float(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
                     else None,
                     "IntegerValue": int(round(value)) if isinstance(value, (int, float)) and not isinstance(value, bool)
                     else None,
                     "StringValue": value if isinstance(value, str) else None}
        if isinstance(value, bool):
            fallback = "ACTUATE_BOOLEAN_COMMAND"
            if s.on or s.off:
                template = s.on if value else s.off
                if template is None:
                    return                           # only one direction configured: the other sends nothing
            else:
                template = None
        else:
            template = s.set
            fallback = "ACTUATE_STRING_COMMAND" if isinstance(value, str) else "ACTUATE_DOUBLE_COMMAND"
        if template is not None:
            text = render(_unescape(template) if self.opt["backslash_escapes"] else template, variables)
            for line in text.split("\n"):
                self.send(line)
            return
        if self.command(fallback, variables) is None:
            raise MachineError("invalid_params", f"{actuator.name}: no command for value {value!r} "
                                                 f"(set on/off/set on the actuator or {fallback} on {self.name})")

    def read_actuator(self, actuator: ActuatorBase) -> Any:
        s = actuator.settings
        variables = {"Id": actuator.id, "Name": s.name or actuator.id, "Index": getattr(s, "index", None)}
        if s.read:
            replies = []
            for line in render(s.read, variables).split("\n"):
                replies += self.send(line)
        else:
            replies = self.command("ACTUATOR_READ_COMMAND", variables)
            if replies is None:
                raise MachineError("invalid_params", f"{actuator.name} can't be read (no read command)")
        regex = java_regex(s.read_regex) if s.read_regex else self.regex.get("ACTUATOR_READ_REGEX")
        if regex is None:
            raise MachineError("invalid_params", f"{actuator.name}: no read_regex")
        for line in replies:
            m = regex.match(line)
            if m:
                return _value(m)
        found = self._wait_for(regex, None, f"{actuator.name} read")
        return _value(regex.match(found[-1]))

    def stop(self) -> str:
        """Machine.stop: send the stop sequence as real-time bytes. Called from any thread."""
        if self.transport is None:
            return "idle"
        for item in self.opt["stop"]:
            try:
                self.write_raw(item)
            except Exception as exc:
                log.error("%s: stop: %s", self.name, exc)
        if self.opt["after_stop"] == "alarm":
            for a in self.axes():
                a.update(homed=False)
        return self.opt["after_stop"]

    def cancel(self) -> None:
        if self.transport is None:
            return
        for item in self.opt["cancel"]:
            self.write_raw(item)
        if self.opt["cancel"] == self.opt["stop"] and self.opt["after_stop"] == "alarm":
            for a in self.axes():
                a.update(homed=False)
            self.machine.driver_alarm(self, "cancelled", "jog cancelled with the stop sequence")

    def clear_alarm(self) -> None:
        self.command("CLEAR_ALARM_COMMAND")
        self.update(alarm=None)

    def raw(self, line: str) -> list[str]:
        """G-code console: send one line as typed."""
        return self.send(line)


def _unescape(text: str | None) -> str | None:
    if text is None or "\\" not in text:
        return text
    return codecs.decode(text, "unicode_escape")


def _value(m: re.Match) -> Any:
    v = m.groupdict().get("Value", m.group(1) if m.groups() else m.group(0))
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


# ── actuator ───────────────────────────────────────────────────────────────────────────────────────────────────────

class GcodeActuatorSettings(Settings):
    driver: str
    value: ValueType = "boolean"
    on: str | None = Field(None, description="G-code for true/on, e.g. M64 P0")
    off: str | None = Field(None, description="G-code for false/off, e.g. M65 P0")
    set: str | None = Field(None, description="Template for number/string values, e.g. 'M3 S{IntegerValue}'")
    read: str | None = Field(None, description="Command that reads the actuator (e.g. a vacuum sensor)")
    read_regex: str | None = Field(None, description="Regex for the reading; group Value")
    index: int | None = Field(None, description="{Index} in the driver's ACTUATE_* commands")
    on_enable: Any = None
    on_disable: Any = None

    @field_validator("read_regex")
    @classmethod
    def _check_regex(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                java_regex(v)
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from None
        return v


@registry.register("actuator", "gcode", GcodeActuatorSettings, "G-code actuator",
                   "Switched with G-code through a gcode driver (e.g. M64 P0 / M65 P0)")
class GcodeActuator(ActuatorBase):

    def setup(self) -> None:
        super().setup()
        self.driver = self.machine.get("driver", self.settings.driver)
        self._status["readable"] = bool(self.settings.read or (
            isinstance(self.driver, GcodeDriver) and self.driver.commands.get("ACTUATOR_READ_COMMAND")))

    def write(self, value: Any) -> None:
        self.driver.actuate(self, value)

    def read(self) -> Any:
        value = self.driver.read_actuator(self)
        self.update(reading=value)
        return value
