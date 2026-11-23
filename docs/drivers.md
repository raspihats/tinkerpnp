# Drivers

A driver is the link between TinkerPnP and a motion controller. Every axis and every G-code actuator names its driver.
A machine can have several drivers.

| Type | Talks to | Status |
|---|---|---|
| `simulated` | Nothing: moves take distance / feed, positions update live | Works |
| `gcode` | Any controller that takes G-code over a serial port or TCP: grblHAL, Marlin, Smoothieware, Duet, TinyG, ... | Works (grblHAL profile; tested against the built-in simulator, bench test pending) |
| `klipper` | Klipper's klippy API socket | Planned (phase 1, last) |

## The `gcode` driver

Configured like OpenPnP's GcodeDriver: the same **command names** and the same **template syntax**, so commands
copied from an OpenPnP `machine.xml` work unchanged. Everything is configurable. A `profile` only fills in defaults
for what the configuration doesn't set; any value written in the config wins.

```yaml
drivers:
  grbl:
    type: gcode
    port: /dev/serial0        # or tcp://192.168.1.50:23, or sim://grblhal
    baud: 115200
    profile: grblhal          # defaults for everything below
    commands:                 # only what differs from the profile
      DISABLE_COMMAND: "$SLP"
```

### Ports

| `port` | Link |
|---|---|
| `/dev/serial0`, `/dev/ttyUSB0`, `COM3` | Serial port (pyserial), at `baud` |
| `tcp://host:port` | TCP socket (e.g. a controller with Ethernet, or ser2net) |
| any pyserial URL (`rfc2217://...`, `socket://...`) | As pyserial understands it |
| `sim://grblhal?time_scale=1&home_time=2` | The built-in grblHAL simulator. Options: `time_scale` (0 = instant moves, 1 = real time), `home_time` (s), `axes` (default `XYZAB`), `home_axes` (default `XYZ`), `homing_required` (default 1) |

### Settings

Every setting except `port`, `baud`, `profile`, `commands` and `max_feed` can come from the profile; set it to
override.

| Setting | Meaning | `generic` | `grblhal` |
|---|---|---|---|
| `commands` | Command templates and regexes by OpenPnP name (table below) | see below | see below |
| `letter_variables` | Template variables are axis letters (`{X}`, `{A}`, `{B}`). Off: `X`, `Y`, `Z` and `Rotation` for rotation axes, like OpenPnP without letter variables | true | true |
| `backslash_escapes` | Decode `\u0018`, `\n`, ... inside **commands** (not regexes), for commands copied from OpenPnP. In YAML, double-quoted strings already do this | false | false |
| `line_ending` | Appended to every line | `"\n"` | `"\n"` |
| `connect_wait` | Wait after opening the port, for boards that reset when the port opens | 0 | 200 ms |
| `timeout` | How long a command may take to be confirmed | 5000 ms | 5000 ms |
| `home_timeout` | Timeout for homing | 60000 ms | 30000 ms |
| `move_timeout` | Timeout for a move; unset: 1.5 × the expected time + `timeout` | unset | unset |
| `max_feed` | Upper limit for the feed of any move | none | none |
| `motion_control` | How the feed of a move is chosen (below) | `toolpath` | `toolpath` |
| `realtime` | Commands sent as bytes, without line ending and without waiting for a confirmation | `[]` | `?`, `!`, `~`, `\x85` |
| `stop` | Sent in order, as bytes, on `machine.stop` | `[]` | `!` (feed hold), Ctrl-X (soft reset) |
| `cancel` | Sent on `motion.jog_cancel` | the stop sequence | the stop sequence |
| `after_stop` | Machine state after a stop: `idle`, or `alarm` (position lost, home again) | `idle` | `alarm` |
| `position_poll` | While a move runs, ask for the position this often, so clients see it move (needs a real-time `GET_POSITION_COMMAND`); 0 = never | 0 | 250 ms |
| `location_confirmation` | Read the position after every move instead of trusting the target | false | false |
| `remove_comments` | Strip `; ...` and `( ... )` before sending | false | false |
| `compress_gcode` | Remove spaces and trailing zeros (`G1X10.5F3000`) | false | false |
| `disconnect_on_disable` | Close the port on `machine.disable` | false | false |

### Commands

Missing commands are skipped, as in OpenPnP: a machine without `SET_GLOBAL_OFFSETS_COMMAND` simply doesn't send one.
Commands can have several lines (separated by `\n`); each line is sent and confirmed on its own.

| Name | When | `grblhal` default |
|---|---|---|
| `CONNECT_COMMAND` | Once after the port opens. The first reply line that isn't a confirmation is shown as `firmware` | `$I` (build info; allowed in alarm) |
| `ENABLE_COMMAND` | `machine.enable` | Ctrl-X (soft reset: wakes from sleep; reset + newline gives exactly one `ok`) |
| `DISABLE_COMMAND` | `machine.disable` | `$SLP` (sleep: motors, spindle and outputs off) |
| `HOME_COMMAND` | `machine.home` (all axes) | `$H` (home all axes) |
| `HOME_AXIS_COMMAND` * | `machine.home` with some axes: once per axis, `{Letter}`, `{Id}`. Without it, homing some axes homes all | `$H{Letter}` |
| `HOME_COMPLETE_REGEX` | Waited for after homing | – |
| `SET_GLOBAL_OFFSETS_COMMAND` | After homing, with each axis at its `home` value (e.g. `G92 {X:X%.4f} ...`) | – |
| `MOVE_TO_COMMAND` | A move: one variable per moving axis, `FeedRate` (mm/min), `Acceleration` (mm/s², the lowest of the moving axes × speed²) | `G1 {X:X%.4f} {Y:Y%.4f} {Z:Z%.4f} {A:A%.4f} {B:B%.4f} {C:C%.4f} F{FeedRate:%.0f}` |
| `MOVE_TO_COMPLETE_COMMAND` | After every move; its confirmation means motion has finished | `G4 P0` (dwell 0: answered when the planner is empty) |
| `MOVE_TO_COMPLETE_REGEX` | Waited for after `MOVE_TO_COMPLETE_COMMAND` | – |
| `GET_POSITION_COMMAND` | After enable and homing, for `location_confirmation` and `position_poll` | `?` (status report) |
| `POSITION_REPORT_REGEX` | Any received line matching it updates the axes: one named group per axis (Java `(?<X>...)` or Python `(?P<X>...)` syntax) | `MPos:` of the status report, 3 to 6 axes |
| `COMMAND_CONFIRM_REGEX` | A command was accepted | `^ok.*` |
| `COMMAND_ERROR_REGEX` | A command failed: the request fails with `driver_error` and the reply | `^(error:\d+\|ALARM:(?!11\b)\d+).*` (`ALARM:11`, homing required, is expected after a reset) |
| `ALARM_REGEX` * | A line saying the controller went into alarm (group `Alarm`), even unasked: the machine goes to `alarm` and must be homed again | `^ALARM:(?<Alarm>(?!11\b)\d+).*` |
| `CLEAR_ALARM_COMMAND` * | `machine.clear_alarm` | `$X` (unlock) |
| `ACTUATE_BOOLEAN_COMMAND` | Boolean actuators without `on`/`off` | – |
| `ACTUATE_DOUBLE_COMMAND` | Number actuators without `set` | – |
| `ACTUATE_STRING_COMMAND` | String actuators without `set` | – |
| `ACTUATOR_READ_COMMAND` | Readable actuators without `read` | – |
| `ACTUATOR_READ_REGEX` | Group `Value` | – |

\* TinkerPnP additions; OpenPnP has no such command.

The `generic` profile only has `COMMAND_CONFIRM_REGEX` (`^ok.*`), `COMMAND_ERROR_REGEX` (`^(error|!!).*`),
`MOVE_TO_COMMAND` (as above), `MOVE_TO_COMPLETE_COMMAND` (`M400`, wait for moves to finish on Marlin and
Smoothieware) and `HOME_COMMAND` (`G28`). Set the rest in `commands`.

### Templates

OpenPnP's syntax:

| Template | Result |
|---|---|
| `{X:X%.4f}` | `X12.3400`; the text is printf-style with the value |
| `{FeedRate:%.0f}` | `4800` |
| `{Value}` | the value as is (`3`, not `3.0`) |
| `{True:M64 P0}{False:M65 P0}` | `M64 P0` when switching on, `M65 P0` when switching off |
| Any `{Name...}` whose variable is missing | removed, so axes that don't move vanish from `MOVE_TO_COMMAND` |

Variables: axis letters (or `X`/`Y`/`Z`/`Rotation`), `FeedRate`, `Acceleration`, `Letter`, `Id`, `Name`, `Index`,
`Value`, `BooleanValue`, `DoubleValue`, `IntegerValue`, `StringValue`, `True`, `False`.

### Feed rate (`motion_control`)

Each axis has a `feed` (its top speed). For a move, the driver picks one `F`:

- `toolpath` (default): the fastest feed along the path at which no axis goes faster than its own feed:
  F = min over moving axes of feed_axis × path length / distance_axis. The path length counts every moving axis,
  rotation included, as grbl does. A diagonal X/Y move on the MC5S3DP-XHAT (X 8000, Y 6000 mm/min, equal distances)
  runs at 6000 × √2 ≈ 8485 mm/min.
- `slowest_axis`: the lowest feed of the moving axes.

Then × `speed` (0–1) of the request, and at most `max_feed`. Acceleration stays in the firmware unless
`MOVE_TO_COMMAND` uses `{Acceleration}`.

### Actuators (`type: gcode`)

```yaml
actuators:
  valve1: {type: gcode, driver: grbl, on: M64 P0, off: M65 P0, on_enable: off, on_disable: off}
  pump:   {type: gcode, driver: grbl, on: M3 S1000, off: M5}
  light:  {type: gcode, driver: grbl, value: double, set: "M106 S{IntegerValue}"}
  vac:    {type: gcode, driver: grbl, value: double, read: M105, read_regex: '.*T:(?<Value>-?\d+\.\d+).*'}
```

`on`/`off`/`set`/`read` are templates with the actuator variables. An actuator without them uses the driver's
`ACTUATE_*` and `ACTUATOR_READ_*` commands with its `index`, as OpenPnP's actuators do.

`on_enable` values are switched **after** the machine's `on_enable` sequence, i.e. after homing:
grbl-type controllers refuse G-code (`M5`, `M65` ...) with `error:9` while they wait to be homed.

### What happens when

| Request | Sent (grblhal profile) |
|---|---|
| `machine.enable` | port opens, `connect_wait`, `CONNECT_COMMAND`, `ENABLE_COMMAND`, `GET_POSITION_COMMAND`; then `machine.on_enable` (e.g. home, park), then actuators' `on_enable` values |
| `machine.home` | `HOME_COMMAND` (or `HOME_AXIS_COMMAND` per axis), `HOME_COMPLETE_REGEX`, `SET_GLOBAL_OFFSETS_COMMAND`, `GET_POSITION_COMMAND`; then `machine.on_home` |
| `motion.move_to` | `MOVE_TO_COMMAND`, `MOVE_TO_COMPLETE_COMMAND` (with `?` every `position_poll` meanwhile), `MOVE_TO_COMPLETE_REGEX`; once per step (safe Z, X/Y, Z) |
| `machine.stop` | the `stop` bytes at once, from any thread; the running request fails with `stopped`; machine goes to `after_stop` |
| `machine.disable` | actuators' `on_disable`, `DISABLE_COMMAND`; the machine is not homed any more |
| a line matching `ALARM_REGEX` | machine to `alarm`, the driver's axes not homed; home again (or `machine.clear_alarm`) |

Every line sent and received is logged (`tx` / `rx`, log level debug), kept (the last 1000 lines, `gcode.history`)
and streamed to console views (`gcode.subscribe`, `notify_gcode`). The web UI's **Console** page sends commands with
`gcode.send`; for grblHAL it has buttons for `$I`, `$$`, `$#`, `$G`, `?`, `$X` and the real-time Ctrl-X, `!`, `~`.

## The `simulated` driver

| Setting | Meaning | Default |
|---|---|---|
| `time_scale` | Moves take distance / feed × this; 0 = instant | 1 |
| `home_time` | How long homing takes (ms, before `time_scale`) | 1000 |
