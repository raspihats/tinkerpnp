"""A small grblHAL simulator, for tests and for trying the gcode driver without hardware (port `sim://grblhal`).

It answers the way the MC5S3DP-XHAT's grblHAL did on the bench (see pnp-machine/grblHAL-bringup.md and
grbl-commands.md), not the whole of grblHAL:

- Real-time bytes act at once, without `ok`: `?` (status report), `!` (feed hold), `~` (resume), Ctrl-X (soft reset).
- Every line gets `ok` or `error:N`. An empty line gets `ok`, so Ctrl-X + newline gives exactly one `ok`.
- With homing required (`$22`), it starts and resets into alarm and says `ALARM:11` (homing required); `$X` (unlock)
  is then refused with `error:46`; `$H` (home all axes) or `$H<axis>` homes and, like the real board, answers
  `[MSG:]`, `ok` and 11 ms later a second `ok`.
- A soft reset during motion loses the position: `ALARM:3` (reset while in motion), homing required again.
- `$SLP` (sleep): motors and outputs off, everything except a reset is refused.
- `G0`/`G1` (move) answer `ok` at once and move in the background at the F feed rate (scaled by `time_scale`);
  `G4 P0` (dwell 0) answers when motion has finished, as on real grbl.
- `M3`/`M5` (spindle on/off), `M64`/`M65 P<n>` (aux output on/off) set outputs; a reset stops the spindle.

Machine positions start at 0 and are reported as `MPos` with three decimals.
"""
from __future__ import annotations

import queue
import re
import threading
import time
from typing import Callable

GREETING = "GrblHAL 1.1f ['$' or '$' for help]"
_WORD = re.compile(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")


class FakeGrblHAL:
    def __init__(self, emit: Callable[[str], None], axes: str = "XYZAB", homing_required: bool = True,
                 home_axes: str = "XYZ", home_time: float = 0.0, time_scale: float = 0.0):
        self.emit = emit                      # called with each output line (without line ending)
        self.axes = axes
        self.homing_required = homing_required
        self.home_axes = home_axes
        self.home_time = home_time
        self.time_scale = time_scale          # 0: moves are instant; 1: real time
        self.pos = {a: 0.0 for a in axes}
        self.outputs: dict[str, float] = {}   # "spindle", "aux0", ... -> value
        self.received: list[str] = []         # every line received, for tests
        self.homed = False
        self.state = "Alarm" if homing_required else "Idle"
        self._move: tuple[dict, dict, float, float] | None = None   # start, target, t0, duration
        self._hold_at: float | None = None
        self._lock = threading.RLock()
        self._buf = ""
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._reset_count = 0
        self._worker = threading.Thread(target=self._run, name="fake-grblhal", daemon=True)
        self._worker.start()

    # ── input ──────────────────────────────────────────────────────────────────────────────────────────────────
    def feed(self, data: bytes) -> None:
        for ch in data.decode("latin-1"):
            if ch == "?":
                self.emit(self.status())
            elif ch == "!":
                self._hold()
            elif ch == "~":
                self._resume()
            elif ch == "\x18":
                self._reset()
            elif ch in "\x84\x85\x87\x90\x91\x92\x93\x94\x95\x96\x97\x99\x9a\x9b\x9c\x9d\x9e\xa0\xa1":
                pass                           # other real-time commands: accepted, not simulated
            elif ch == "\n":
                self._lines.put(self._buf.strip())
                self._buf = ""
            elif ch != "\r":
                self._buf += ch

    def close(self) -> None:
        self._lines.put(None)

    # ── real-time ──────────────────────────────────────────────────────────────────────────────────────────────
    def _now_pos(self) -> dict:
        with self._lock:
            if self._move is None:
                return dict(self.pos)
            start, target, t0, duration = self._move
            now = self._hold_at if self._hold_at is not None else time.monotonic()
            frac = 1.0 if duration <= 0 else min(1.0, (now - t0) / duration)
            pos = {a: start[a] + (target[a] - start[a]) * frac for a in start}
            if frac >= 1.0 and self._hold_at is None:
                self.pos, self._move = pos, None
                if self.state == "Run":
                    self.state = "Idle"
            return pos

    def status(self) -> str:
        pos = self._now_pos()
        return f"<{self.state}|MPos:{','.join(f'{pos[a]:.3f}' for a in self.axes)}|FS:0,0>"

    def _hold(self) -> None:
        with self._lock:
            if self._move is not None and self._hold_at is None:
                self._hold_at = time.monotonic()
                self.state = "Hold:0"

    def _resume(self) -> None:
        with self._lock:
            if self._hold_at is not None and self._move is not None:
                start, target, t0, duration = self._move
                self._move = (start, target, t0 + time.monotonic() - self._hold_at, duration)
                self._hold_at = None
                self.state = "Run"

    def _reset(self) -> None:
        with self._lock:
            moving = self._move is not None and self._now_pos() != self._move[1]
            if self._move is not None:
                self.pos = self._now_pos()
                self._move, self._hold_at = None, None
            self._reset_count += 1
            self._buf = ""
            while not self._lines.empty():          # the input buffer is flushed
                try:
                    self._lines.get_nowait()
                except queue.Empty:
                    break
            if moving:
                self.homed = False
            self.outputs.pop("spindle", None)       # a reset stops the spindle (aux outputs are left alone here)
            self.emit(GREETING)
            if moving:
                self.state = "Alarm"
                self.emit("ALARM:3")
            elif self.homing_required and not self.homed:
                self.state = "Alarm"
                self.emit("ALARM:11")
            else:
                self.state = "Idle"

    # ── lines ──────────────────────────────────────────────────────────────────────────────────────────────────
    def _run(self) -> None:
        while True:
            line = self._lines.get()
            if line is None:
                return
            generation = self._reset_count
            self.received.append(line)
            reply = self._line(line, generation)
            if reply is not None and generation == self._reset_count:
                self.emit(reply)

    def _wait_motion(self, generation: int) -> bool:
        while self._move is not None:
            if generation != self._reset_count:
                return False
            self._now_pos()
            time.sleep(0.005)
        return generation == self._reset_count

    def _line(self, line: str, generation: int) -> str | None:
        up = line.upper()
        if up == "":
            return "ok"
        if self.state == "Sleep":
            return "error:79" if up != "$SLP" else "ok"
        if up == "$I":
            self.emit("[VER:1.1f.20251101:]")
            self.emit("[OPT:VNMSL,35,1024,5,0]")
            return "ok"
        if up == "$SLP":
            if not self._wait_motion(generation):
                return None
            self.state = "Sleep"
            self.outputs = {}
            self.emit("[MSG:Sleeping]")
            return "ok"
        if up == "$X":
            if self.homing_required and not self.homed:
                return "error:46"
            self.state = "Idle"
            return "ok"
        if up.startswith("$H"):
            axes = up[2:] or self.home_axes
            if any(a not in self.home_axes for a in axes):
                return "error:3"
            self.state = "Home"
            deadline = time.monotonic() + self.home_time
            while time.monotonic() < deadline:
                if generation != self._reset_count:
                    return None
                time.sleep(0.005)
            with self._lock:
                for a in axes:
                    self.pos[a] = 0.0
                self.homed = self.homed or axes == self.home_axes or set(self.home_axes) <= set(axes)
                self.state = "Idle"
            self.emit("[MSG:]")
            self.emit("ok")
            time.sleep(0.011)                        # the real board sends a second ok 11 ms later
            return "ok"
        if self.state == "Alarm":
            return "error:9"                         # G-code locked out during alarm
        if up.startswith("$"):
            return "ok"
        return self._gcode(up, generation)

    def _gcode(self, up: str, generation: int) -> str | None:
        words = _WORD.findall(up.split(";")[0])
        if not words:
            return "error:20"
        letters = {k: float(v) for k, v in words}
        code = up.split()[0] if up.split() else ""
        if words[0][0] == "G" and float(words[0][1]) in (0, 1):
            target = dict(self._now_pos())
            if not self._wait_motion(generation):    # one move at a time is enough here
                return None
            for a in self.axes:
                if a in letters:
                    target[a] = letters[a]
            feed = letters.get("F", 1000.0)
            dist = sum((target[a] - self.pos[a]) ** 2 for a in self.axes) ** 0.5
            duration = dist / (feed / 60.0) * self.time_scale if feed > 0 else 0.0
            with self._lock:
                self._move = (dict(self.pos), target, time.monotonic(), duration)
                self.state = "Run"
            return "ok"
        if code.startswith("G4"):
            return "ok" if self._wait_motion(generation) else None
        if code.startswith("G92") or code in ("G90", "G91", "G21", "G20", "G17", "G94"):
            return "ok"
        if words[0] == ("M", "3"):
            self.outputs["spindle"] = letters.get("S", 0.0)
            return "ok"
        if words[0] == ("M", "5"):
            self.outputs["spindle"] = 0.0
            return "ok"
        if words[0][0] == "M" and float(words[0][1]) in (64, 65):
            if "P" not in letters:
                return "error:36"
            self.outputs[f"aux{int(letters['P'])}"] = 1.0 if float(words[0][1]) == 64 else 0.0
            return "ok"
        return "error:20"
