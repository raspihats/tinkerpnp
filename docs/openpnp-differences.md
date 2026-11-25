# Where TinkerPnP differs from OpenPnP

TinkerPnP aims to be compatible with OpenPnP's **files** (parts, packages, boards, jobs, machine configuration), not
to copy its **behaviour** in every detail. This page lists every place where TinkerPnP deliberately or (for now)
unavoidably behaves differently. Importers convert where they can; the "On import" column says how.

Keep this page current: every change that makes TinkerPnP behave differently from OpenPnP adds a row here.

Sources for the OpenPnP side: OpenPnP 2.7 as set up on the first machine (`../openpnp-setup.md` in the
pnp-machine folder), its `machine.xml` (`tools/openpnp-config/`), and what was observed on that machine. Points that
weren't checked against OpenPnP's source or a running OpenPnP are marked *not verified*.

Status: as of phase 1, step 5 (motion core, `gcode` driver, console, cameras, Machine page; 2026-11-23).

## Summary

| Area | OpenPnP | TinkerPnP | Why |
|---|---|---|---|
| Park location | Where the head's **default camera** goes | Where the **head** goes (head X/Y, no offset) | Parking at 0,0 pushed the head past its soft limits on the first machine |
| Safe Z on moves | Done by the caller (e.g. "move to location at safe Z"); a plain move doesn't lift | Every `move_to` with X/Y travel lifts first, unless `safe_z: none` | Safe by default; API clients can't forget it |
| Jogs | Straight to the target at the current Z (`JogControlsPanel.jogTool`: `moveTo(..., JogMotion)`) | Same (`machine.jog.safe_z: never`, the default) | – |
| Board protection | Jog safety, on at every start ("Safety" tab): a jog that puts a nozzle below safe Z within 1 mm + half the tip diameter (half the max part diameter with a part) of an enabled board is refused, unless above the board surface | Same: `motion.jog` `board_protection` (default on), the "Board protection" switch in the UI (on again after a reload), tip `diameter` | – |
| Unsafe Z roaming | `unsafe-Z-roaming-distance` (10 mm): a tool with a **virtual** Z jogged below safe Z farther than that from where it went below goes back to safe Z | Same: `machine.unsafe_z_roaming_distance` (10 mm), after `motion.jog` / `motion.move_to` | – |
| Fixed camera roaming radius | Click-to-move on a fixed (up-looking) camera keeps the nozzle's Z within the camera's `roaming-radius` (default 0) | Not yet: click-to-move exists only on head cameras | |
| Dynamic safe Z | Per nozzle (`enable-dynamic-safe-z`): safe Z accounts for the part height | Not yet | Comes with parts/jobs (phase 5) |
| Soft-limit check | Per motion command | All targets of a whole move (lift, travel, lower) are checked **before** anything moves | A refused move never half-runs |
| Rotation limit | `limit-rotation`: angles are normalized into ±180° | Same: `limit_rotation: [-180, 180]` takes angles into the range by whole turns (200° → −160°); a range narrower than one turn is a hard limit | – |
| Rotation wrap-around | `wrap-around-rotation` | Not yet | |
| Homing of rotation axes | Every axis is set to its `home-coordinate` after homing | Axes with `homing: false` (default for rotation) keep their position | grblHAL doesn't home A/B; their position survives `$H` (home all axes) |
| Homing a subset | Whole machine (`HOME_COMMAND` per driver) | `machine.home(axes)` can home some axes | Recovery after a stall on one axis |
| What happens after enable / homing | Checkboxes: `home-after-enabled`, `park-after-homed`, `safe-Z-park` | Step lists: `machine.on_enable`, `machine.on_home` | Configurable instead of fixed |
| Before homing | Nothing: Home sends `HOME_COMMAND`; a lift has to come from the firmware's homing order or the command | `machine.on_before_home` steps, run only when the machine is homed (e.g. `[safe_z]`) | The first machine homes X, then Y, then Z, and its Z home is a nozzle fully down: a nozzle left down at camera focus would be dragged along |
| Actuators on enable / home / disable | `enabled-actuation`, `homed-actuation`, `disabled-actuation` (ActuateOn/Off, LeaveAsIs, AssumeUnknown) | `on_enable` / `on_disable` values per actuator; on home: an `actuate` step in `on_home` | Same effect, fewer special cases |
| When `on_enable` actuator values are sent | On enable (*not verified exactly when*) | **After** the `on_enable` sequence (after homing) | grbl refuses G-code (`M5`, `M65`) with `error:9` until homed |
| Stopping motion | Power button (disable the machine); job Stop stops the job | `machine.stop`: aborts the running move, cancels queued requests, machine stays enabled where the controller allows | API clients and a future e-stop button need an abort that isn't "disable" |
| Disabling | Motors released by the driver's `DISABLE_COMMAND` | Same, and the machine is marked **not homed** | Released motors lose position |
| Feed rate | Speed factor; motion planner (`motion-control-type`: ToolpathFeedRate, ConstantAcceleration, SimpleSCurve, ...) computes feed, acceleration, jerk | `motion_control: toolpath` (fastest feed at which no axis exceeds its own feed, like ToolpathFeedRate) or `slowest_axis`; × speed; acceleration and jerk stay in the firmware unless a template uses `{Acceleration}` | Planning acceleration profiles on the host isn't needed for grbl-type firmware |
| G-code driver commands | GcodeDriver command names and templates | Same names and syntax, plus `HOME_AXIS_COMMAND`, `ALARM_REGEX`, `CLEAR_ALARM_COMMAND`; defaults from a `profile` | See [`drivers.md`](drivers.md) |
| Real-time commands | Every command waits for its confirmation | `realtime` list (grbl: `?`, `!`, `~`) sent as bytes without waiting; `stop` sequence always as bytes | grbl never answers `?` with `ok` |
| Position while moving | Read after moves if "location confirmation" is on | Also polled during moves (`position_poll`), so clients see the head move | Live UI |
| Camera device | `unique-id` (from the device, often includes the USB port) and a device index | `device:` selectors tried in order: USB serial, vendor:product, name, path | Survives moving the camera to another USB port |
| Camera image transforms | Rotation, flip, crop, deinterlace, lens calibration (undistort) | `transform`: crop, rotate (90° steps), flip; undistort later | – |
| Camera capture | Opened at startup, captured continuously | Opened on the first viewer, closed `idle_close` after the last | Spare the Pi's CPU and USB |
| Viewing cameras | In the GUI | MJPEG streams any browser can show, overlays drawn on the server | API-first |
| Simulated cameras | OpenPnP's simulated camera shows a picture of a board | `simulated` draws the bed grid and feeder parts (down) or the nozzles (up), following the machine | Testing jogs and offsets without hardware |
| Actuator state after a stop or alarm | Kept as last set (*not verified*) | **Unknown** (shown amber) until set again: grbl's reset switches the spindle output off | The UI must not show a pump as on when it is off |
| Stop key | – (*not verified*) | **Esc** on every page while enabled, plus a STOP button in the header | Safety |
| Click to move | Camera view: click moves the camera there | Same, for head-mounted down-looking cameras with `units_per_pixel` | – |
| Capturing positions | "Capture" buttons write the current position into a setting | `config.capture` returns the real position (read back from the controller) for the client to confirm and save; the camera view has "Set location from N1" and "Move N1 here" for up-looking cameras | Same idea; the value is shown before it's saved |
| Measuring units per pixel | Camera wizard: measure an object of known size in the image | The nozzle is the ruler: it moves a known distance over the camera and the image shift is measured; also reports rotation and mirroring. Up-looking cameras only so far | No calibration part needed; uses the machine's own accuracy |
| Editing while enabled | Most settings can be changed any time | Only calibration values while enabled (applied live); structural changes need a disabled machine | The machine is rebuilt from the file for structural changes |
| Several clients | One GUI | Any number of API clients; requests run in order on one motion thread | API-first |
| Errors | Java exceptions shown in dialogs | Named API errors (`not_homed`, `soft_limit`, `stopped`, ...) with data | Front ends switch on names |
| Configuration file | `machine.xml` (Simple XML), edited through the GUI | `machine.yaml`, edited through the API (validated, comments kept) or by hand | See `docs/config.md` |

## Details

### Park location

OpenPnP's head **park location** is where the head's default camera goes. The machine position is the park location
minus the camera's head offset. On the first machine, park 0,0 with a camera offset of (21.9, 20.0) asked for X −21.9 /
Y −20, and OpenPnP refused with *"Cant move ... lower than soft limit"*. The OpenPnP setup there now uses park
(22.9, 21.0) to land the head at X 1, Y 1.

TinkerPnP's `machine.park.location` is the **head's** X/Y in machine coordinates. What you write is where the
axes go. Parking always lifts every nozzle on the head to safe Z first (OpenPnP's `safe-Z-park="true"`).

**On import:** TinkerPnP location = OpenPnP park location − offset of the head's default camera.

### Safe Z

Both use the axis **safe zone** (`safe-zone-low` / `safe-zone-high` in OpenPnP, `safe_zone: [low, high]` in
TinkerPnP). An axis without one counts as safe everywhere; a virtual axis is safe at or above its home coordinate.

**Moves** the user or a job asks for (move to a location, park, click-to-move on a head camera) lift every nozzle of
the head into its safe zone before X/Y travel. In OpenPnP that's a step the caller takes
(`MovableUtils.moveToLocationAtSafeZ`); in TinkerPnP it's part of `motion.move_to`. The order is:

1. every nozzle on the head into its safe zone (on raw axes, so a shared seesaw Z moves once)
2. X, Y and rotation
3. the object's own Z, if the target has one

A move without X/Y travel moves all axes together. `safe_z: none` turns lifting off for one move.

**Jogs** don't lift, in either (checked in OpenPnP's source, 2026-11-25: `JogControlsPanel.jogTool` calls
`moveTo(target, JogMotion)`). Two things protect them instead, both as in OpenPnP:

- **Board protection** (`MachineControlsPanel.checkJogMotionSafety`): before the jog, every nozzle of the head is put
  where it would end up. One that is below its safe zone and within 1 mm + half its tip diameter (half the max part
  diameter if it holds a larger part; Z lowered by the part height) of an enabled board of the job, and not above
  the board's surface, refuses the jog. On at every start; the switch in the jog panel turns it off until the next
  start.
- **Unsafe Z roaming** (`machineTargetedUserAction`): after a jog or move, a tool below safe Z remembers where it
  went below. Jogging the same tool farther than `unsafe_z_roaming_distance` (10 mm) from there sends it back to
  safe Z, but only if its Z axis is **virtual** (a head camera's): a real Z axis is never moved by this.

Earlier notes here said OpenPnP lifted Z to 57 before the first X/Y jog on the first machine. That was most likely
"park after homed" (Z to safe height after homing), not the jog.

`machine.jog.safe_z: always` is a TinkerPnP option that makes jogs lift like moves; the default `never` is OpenPnP's
behaviour.

Not supported (yet): dynamic safe Z (`enable-dynamic-safe-z`, which accounts for the height of the part on the
nozzle) and the fixed camera `roaming-radius`.

### Soft limits

Both have per-axis soft limits (`soft-limit-low/high` with `-enabled` flags in OpenPnP; `soft_limits: [low, high]`,
or no key, in TinkerPnP). Limits are checked on **raw** controller axes in both, so for the seesaw nozzle N2 the
check is on Z, not on z_n2.

TinkerPnP checks every target of the whole move, including the safe-Z lift, before sending anything. If one target is
outside its limits, the API returns `soft_limit` with the axis, value and limit, and nothing has moved. OpenPnP
checks per motion command, so a multi-step move can run its first steps before a later one is refused (*not
verified*).

### Rotation

OpenPnP's axis option `limit-rotation` keeps angles within ±180° by normalizing them (200° becomes −160°), and
`wrap-around-rotation` lets the axis take the shortest way. TinkerPnP does the same normalization for
`limit_rotation` ranges of a full turn or more (`[-180, 180]`: by whole turns into the range). A narrower range,
e.g. `[-90, 90]` for a nozzle with a hose, is a hard limit and refuses targets outside it. Wrap-around isn't
implemented.

### Homing

OpenPnP sends `HOME_COMMAND` per driver, then sets **every** axis of the driver to its `home-coordinate`, rotation
axes included. Optional visual homing (a homing fiducial) can correct X/Y afterwards.

TinkerPnP:

- `controller` axes have `homing: true|false` (default: true for linear, false for rotation axes). Axes with a
  switch are set to their `home` position. Axes without one keep their position and count as homed once the
  machine homes. grblHAL behaves this way: `$H` (home all axes) homes only the axes in its homing mask, so on the
  MC5S3DP-XHAT A and B keep whatever position they had.
- `machine.home` takes an optional list of axes.
- Visual homing isn't implemented yet (phase 6, vision).

**On import:** `home-coordinate` → `home`. Whether an axis has a switch isn't in `machine.xml`; the importer sets
`homing: false` on rotation axes and leaves linear axes at the default (true).

### What happens on enable, homing and disable

Order on enable in TinkerPnP: driver connect and enable commands, then the `on_enable` sequence (e.g. home, park),
then the actuators' `on_enable` values. The actuators come last because grbl-type controllers refuse G-code
(`M5`, `M65`, ...) with `error:9` until they are homed.

| OpenPnP (`machine.xml`) | TinkerPnP (`machine.yaml`) |
|---|---|
| `<home-after-enabled>true` | `machine.on_enable: [home]` |
| `<park-after-homed>true` | `machine.on_home: [park]` |
| `safe-Z-park="true"` | Always: `park` lifts to safe Z first |
| actuator `enabled-actuation="ActuateOff"` / `"ActuateOn"` | actuator `on_enable: off` / `on` |
| actuator `disabled-actuation="ActuateOff"` / `"ActuateOn"` | actuator `on_disable: off` / `on` |
| actuator `homed-actuation="ActuateOff"` / `"ActuateOn"` | `machine.on_home: [..., {actuate: {<id>: off}}]` |
| `LeaveAsIs`, `AssumeUnknown` | no value (nothing is sent) |

Sequences can also contain `safe_z` and `{dwell: ms}`, in any order. The loader checks them; `home` inside `on_home`
is refused because it would loop.

TinkerPnP marks the machine **not homed** when it is disabled, because the driver releases the motors (on grblHAL
with `$SLP`, sleep) and positions can no longer be trusted. With `on_enable: [home]` the next enable homes again, as
OpenPnP does with *Home after enabled*.

### Actuators

Switching is the same idea (boolean, double or string values; G-code templates per value). Differences:

- `actuator.set` and `on_enable` / `on_disable` accept `on`/`off`, `true`/`false` or 1/0 for boolean actuators.
- OpenPnP's `coordinated-before-actuate` (wait for motion to stop before switching): in TinkerPnP every request
  runs on the motion thread after the previous one has finished, so actuators always switch at standstill. Switching
  *during* a move (OpenPnP's coordinated settings set to None) isn't possible.
- Interlock actuators (OpenPnP's `interlock-actuator`) aren't implemented.
- OpenPnP's head `vacuum-pump-control` (`PartOn`, ...) corresponds to the head's `pump`; pump control by
  pick/place comes with jobs (phase 5).

### Stopping

OpenPnP's GUI stops motion by disabling the machine (the power button); the job Stop button stops the job after the
current step (*not verified in detail*).

TinkerPnP has `machine.stop`, separate from disable:

- It is *immediate*: on a WebSocket it overtakes the client's queued requests, and it can be sent from any client.
- The running move ends with the error `stopped`, and so does every request that was queued before the stop.
- The driver runs its stop sequence (for grbl-type controllers: feed hold `!`, then soft reset Ctrl-X). The machine
  ends `idle` or `alarm` depending on the controller, and stays enabled.
- `motion.jog_cancel` ends only the current jog.

### Speed and acceleration

OpenPnP gives a move a speed factor; its motion planner (`motion-control-type`: ToolpathFeedRate, ConstantAcceleration,
SimpleSCurve, ...) turns per-axis feed, acceleration and jerk into G-code feed and, where supported, acceleration
commands.

TinkerPnP's `speed` is also a fraction (0–1). The gcode driver picks one `F` per move with `motion_control`:

- `toolpath` (default, the idea of OpenPnP's ToolpathFeedRate): the fastest feed along the path at which no axis
  exceeds its own feed. The path length counts every moving axis, rotation included, as grbl does.
- `slowest_axis`: the lowest feed of the moving axes.

Then × speed, capped at `max_feed`. Acceleration and jerk stay in the firmware (`acceleration_applies_in:
firmware`). A `MOVE_TO_COMMAND` that uses `{Acceleration}` gets the lowest acceleration of the moving axes × speed².
OpenPnP's ConstantAcceleration and S-curve modes, which plan motion on the host, aren't implemented. The simulated
driver moves each axis at its own feed and doesn't simulate acceleration.

### The G-code driver

The `gcode` driver uses OpenPnP's GcodeDriver command names and template syntax, so commands copied from a
`machine.xml` work unchanged (full reference: [`drivers.md`](drivers.md)). Differences:

- **Profiles.** `profile: grblhal` fills in every command and setting that the config leaves out. OpenPnP has an
  "Issues & Solutions" wizard that writes commands into the configuration instead.
- **Additions:** `HOME_AXIS_COMMAND` (home some axes), `ALARM_REGEX` (an unasked alarm puts the machine in `alarm`
  and un-homes it), `CLEAR_ALARM_COMMAND`.
- **`settle`** per command: wait and drop stray replies after a command (grblHAL sometimes answers `$H` with two
  `ok`s). OpenPnP pairs replies the same way, one confirmation per command (*not verified how it deals with this*).
- **Real-time commands** (`realtime`: `?`, `!`, `~` for grbl) are sent as bytes without line ending and without
  waiting for a confirmation; the `stop` sequence always is. grbl never answers `?` with `ok`.
- **`backslash_escapes`** decodes commands only, never regexes: decoding would turn `\b` in `(?!11\b)` into a
  backspace character.
- **Regexes** accept Java's `(?<name>...)` (as in OpenPnP) and Python's `(?P<name>...)`.
- **Position while moving:** with `position_poll`, the driver asks for the position during moves, so clients see
  the head move. OpenPnP reads positions after moves only with location confirmation.
- **After a stop** (`after_stop: alarm` in the grblhal profile), the driver's axes count as not homed: a soft reset
  during motion loses the position.
- Per-head-mountable commands (OpenPnP's `head-mountable-id` on commands) and the sub-driver structure aren't
  supported; use several drivers instead.

### Coordinates and axes (same as OpenPnP)

For reference, these work as in OpenPnP:

- Location of a head-mounted object = head position + its offset; moving a nozzle or head camera to X means the head
  goes to X − offset.
- Mapped axes use OpenPnP's two-point linear map (`map-input-0/1`, `map-output-0/1` → `map: [[in0, out0], [in1,
  out1]]`).
- Virtual axes (e.g. the Z of a fixed-focus camera) take any value without moving anything.
- Machine-mounted cameras can't be moved.
- `acceptable-tolerance` → `tolerance`: a target closer than this to the current position doesn't move the axis.

### Several clients, one machine

OpenPnP is one desktop program. TinkerPnP is a server: any number of front ends and scripts can be connected. All
requests that move or switch things run one after another on one motion thread. Every client sees the same state
through status subscriptions, including `machine.selected_tool`, which OpenPnP keeps per GUI. Configuration changes
are refused while the machine is enabled (`busy`), so the running machine never changes under a move.
