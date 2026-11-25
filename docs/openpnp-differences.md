# Where TinkerPnP differs from OpenPnP

TinkerPnP behaves like OpenPnP by default: many users know OpenPnP, and its files (parts, packages, boards, jobs,
machine configuration) should mean the same here. A difference is either a deliberate improvement (the "Why" column
says why it is worth it) or **to align**: not done yet. This page lists every one. Importers convert where they can.

Keep this page current: every change that makes TinkerPnP behave differently from OpenPnP adds a row here.

Sources for the OpenPnP side: OpenPnP's source (github.com/openpnp/openpnp, `develop`, checked 2026-11-25: motion,
homing, drivers, cameras, vision, nozzles, actuators, feeders, jobs, Machine Setup), OpenPnP 2.7 as set up on the
first machine (`../openpnp-setup.md` in the pnp-machine folder) and its `machine.xml` (`tools/openpnp-config/`).
TinkerPnP is written from OpenPnP's behaviour, not from its code (licence: `design.md`).

Status: 2026-11-25.

## Summary

| Area | OpenPnP | TinkerPnP | Why |
|---|---|---|---|
| Park location | Where the head's **default camera** goes | Where the **head** goes (head X/Y, no offset) | Parking at 0,0 pushed the head past its soft limits on the first machine |
| Safe Z on moves | Done by the caller (e.g. "move to location at safe Z"); a plain move doesn't lift | Every `move_to` with X/Y travel lifts first, unless `safe_z: none` | Safe by default; API clients can't forget it |
| Jogs | Straight to the target at the current Z (`JogControlsPanel.jogTool`: `moveTo(..., JogMotion)`) | Same (`machine.jog.safe_z: never`, the default) | – |
| Board protection | Jog safety, on at every start ("Safety" tab): a jog that puts a nozzle below safe Z within 1 mm + half the tip diameter (half the max part diameter with a part) of an enabled board is refused, unless above the board surface | Same: `motion.jog` `board_protection` (default on), the "Board protection" switch in the UI (on again after a reload), tip `diameter` | – |
| Unsafe Z roaming | `unsafe-Z-roaming-distance` (10 mm): a tool with a **virtual** Z jogged below safe Z farther than that from where it went below goes back to safe Z | Same: `machine.unsafe_z_roaming_distance` (10 mm), after `motion.jog` / `motion.move_to` | – |
| Fixed camera roaming radius | Inside a fixed camera's `roaming-radius` (default 0), click-to-move and bottom vision move the nozzle without safe Z; outside, at safe Z | Same for Shift+click (since 2026-11-25): camera `roaming_radius` | – |
| Dynamic safe Z | Per nozzle (`enable-dynamic-safe-z`): safe Z accounts for the part height | Same (since 2026-11-25): nozzle `dynamic_safe_z`, within the safe zone | – |
| Soft-limit check | Per motion command: with the default (null) motion planner a later refused step comes after earlier steps already moved | All targets of a whole move (lift, travel, lower) are checked **before** anything moves | A refused move never half-runs |
| Rotation limit | `limit-rotation`: the soft limits are the range (default ±180°); any range is first normalized by whole turns, then refused; soft limits on rotation axes only with limit-rotation | Same (since 2026-11-25); the range is `limit_rotation` | – |
| Rotation wrap-around | `wrap-around-rotation`: the shortest way | Same for axes without `limit_rotation` (since 2026-11-25): `wrap_around`; no G92 renormalizing of the controller's coordinate | grbl's rotation coordinate just grows |
| Homing of rotation axes | Every axis is set to its `home-coordinate` after homing (in OpenPnP's memory; no command is sent) | Axes with `homing: false` (default for rotation) keep their position | grblHAL doesn't home A/B; their position survives `$H` (home all axes) |
| After homing | No G92: `SET_GLOBAL_OFFSETS_COMMAND` only after visual homing. Virtual axes go to their home | Same (since 2026-11-25) | – |
| Homing fails on enable | The machine stays enabled and unhomed | Same (since 2026-11-25): jog off a stuck switch, home again | – |
| Re-homing | Unhomed first: a failed home leaves the machine unhomed | Same (since 2026-11-25) | – |
| Homing a subset | Whole machine (`HOME_COMMAND` per driver) | `machine.home(axes)` can home some axes | Recovery after a stall on one axis |
| What happens after enable / homing | Checkboxes: `home-after-enabled`, `park-after-homed`; `safe-Z-park` only affects the Z park button | Step lists: `machine.on_enable`, `machine.on_home` | Configurable instead of fixed |
| Before homing | Nothing: Home sends `HOME_COMMAND`; a lift has to come from the firmware's homing order or the command | `machine.on_before_home` steps, run only when the machine is homed (e.g. `[safe_z]`) | The first machine homes X, then Y, then Z, and its Z home is a nozzle fully down: a nozzle left down at camera focus would be dragged along |
| Actuators on enable / home / disable | `enabled-actuation` (default **AssumeUnknown**), `homed-actuation`, `disabled-actuation` | `on_enable` / `on_disable` values per actuator; without `on_enable` the value is unknown after enable, as AssumeUnknown (since 2026-11-25); on home: an `actuate` step in `on_home` | Fewer special cases |
| When `on_enable` actuator values are sent | Queued after the enable task, so after home-after-enabled | Same: after the `on_enable` sequence (after homing) | grbl refuses G-code (`M5`, `M65`) with `error:9` until homed |
| Stopping motion | Power button (disable the machine, Ctrl+E); job Stop stops the job | `machine.stop`: aborts the running move, cancels queued requests, machine stays enabled where the controller allows | An abort that isn't "disable" |
| Disabling | Acts at once, even mid-move (it overtakes everything); unhomes; drivers disabled in reverse order | Same (since 2026-11-25: `machine.disable` stops a running move first); drivers in config order | – |
| Feed rate | Speed factor; `motion-control-type`: ToolpathFeedRate (the **driver's** max feed × speed), EuclideanAxisLimits (per-axis limits on a straight path), ConstantAcceleration, SimpleSCurve, ... | `motion_control: toolpath` = per-axis feeds on the straight path (also accepted as `euclidean_axis_limits`, OpenPnP's name), `toolpath_feed_rate` (OpenPnP's ToolpathFeedRate: the driver's max feed) or `slowest_axis`; × speed; no minimum speed (OpenPnP 5 %); acceleration and jerk stay in the firmware unless a template uses `{Acceleration}` | Host-side acceleration profiles aren't needed for grbl-type firmware; default `toolpath` kept: OpenPnP's ToolpathFeedRate default would run Z at the overall max feed |
| Global speed | One speed slider (jog panel) for **every** move incl. jobs; each head move × the slowest part speed of any nozzle carrying a part; at least 5 % | Same (since 2026-11-25): `machine.set_speed`, the slider in the jog panel; requests may still pass their own `speed` (× the global one) | – |
| G-code driver commands | GcodeDriver command names and templates, incl. `DELAY_COMMAND`, deprecated ones (`PUMP_ON_COMMAND`, ...), MOVE_TO variables `{XL}`, `{XF}`, `{XIncreasing}`/`{XDecreasing}`, `{XJerk}`; HOME_COMMAND gets the home coordinates and lowest rates; `{IntegerValue}` truncated | Same (since 2026-11-25) except `{XJerk}` (no jerk: removed); deprecated commands load but aren't sent. Plus `HOME_AXIS_COMMAND`, `ALARM_REGEX`, `CLEAR_ALARM_COMMAND`; defaults from a `profile`. See [`drivers.md`](drivers.md) | – |
| `letter_variables: false` | Template variables by axis type (X, Y, Z, Rotation); the position report is always read by letter | Same (since 2026-11-25) | – |
| Error replies | Stored and raised on the **next** command | Raised on the command that caused it | Clearer errors |
| Several drivers in one move | All drivers get the move, then all are waited for | Same (since 2026-11-25): one thread per controller | – |
| Real-time commands | Every command waits for its confirmation | `realtime` list (grbl: `?`, `!`, `~`) sent as bytes without waiting; `stop` sequence always as bytes | grbl never answers `?` with `ok` |
| Position while moving | Read after moves if "location confirmation" is on | Also polled during moves (`position_poll`), so clients see the head move | Live UI |
| Camera device | `unique-id` + format; with several matches it warns and uses the last; or a device index | `device:` selectors (USB serial, vendor:product, name, path): all given must fit one camera; the USB port path decides between look-alikes; ambiguous = refused | Two cameras of one model (same serial) can't be swapped by accident |
| Camera image transforms | Deinterlace, crop (centred), white balance, lens calibration, scale, rotation (any angle, counter-clockwise), offset, flipX (**top-bottom**) / flipY (left-right) | Same, in the same order (since 2026-11-25); crop anywhere (`{x, y, width, height}`); white balance: manual gains; the old chessboard lens calibration is replaced by advanced calibration | Crop: more general |
| Advanced camera calibration | Machine moves a known point (nozzle tip / calibration fiducial) along radial lines at two Z; fits distortion, tilt, mounting rotation, mm/px at any Z; frames corrected into a virtual camera | Same (since 2026-11-25) for up-looking machine cameras (nozzle tip) and down-looking head cameras (primary + secondary calibration fiducial), fitted with OpenCV's calibrateCamera (k3 fixed); the virtual camera is centred on the configured location | – |
| Backlash compensation | OneSided, OneSidedOptimized, Directional, DirectionalSneakUp, speed factor | `backlash`: one_sided, one_sided_optimized with a speed factor for the final approach (since 2026-11-25) | **To align**: directional methods (not defined well enough from the behaviour alone) |
| Cam axes | ReferenceCamCounterClockwiseAxis / ClockwiseAxis (cam radius, wheel radius, gap, arms angle) | Axis type `cam` (direction, same parameters; since 2026-11-25) | – |
| Auto tool select | The tool the user moves becomes the selected tool (default on) | Same (since 2026-11-25): `machine.auto_tool_select` | – |
| Z park / C park | Buttons: all to safe Z; rotation to 0 (or the middle of a limited range) | Same (since 2026-11-25): `motion.park` axes `z` / `rotation`, buttons in the jog panel | – |
| Vacuum sensing | Part-on / part-off checks (Absolute or Difference method) with a vacuum sense actuator, levels on the tip | Absolute method (since 2026-11-25): nozzle `vacuum_sense`, tip `vacuum.part_on` / `part_off`; a failed part-on check drops the part and the pick is retried | **To align**: the Difference method |
| Unsafe Z roaming distance | Measured in X/Y | Same (since 2026-11-25) | – |
| Units per pixel | One value, or per Z (3D: primary/secondary Z) | Same (since 2026-11-25): `units_per_pixel_3d`, or from the advanced calibration; fiducials use the board's Z | – |
| Camera settling | FixedTime (250 ms default), Maximum, Mean, Euclidean, Square, Motion... | `fixed` (250 ms) or `motion` (pictures until two in a row differ less than a threshold; debounce, timeout) since 2026-11-25 | Motion covers the other image-difference methods |
| Camera lights | Light actuator per camera, on before capture (default), off after (optional), on for user actions, anti-glare | Same (since 2026-11-25): `light`, `light_on_capture`, `light_off_after_capture`, `light_on_user_action`, `light_off_for_other_cameras` | – |
| Camera capture | Opened at startup, captured continuously | Opened on the first viewer, closed `idle_close` after the last | Spare the Pi's CPU and USB |
| Viewing cameras | In the GUI | MJPEG streams any browser can show, overlays drawn on the server | API-first |
| Simulated cameras | OpenPnP's simulated camera shows a picture of a board | `simulated` draws the bed grid and feeder parts (down) or the nozzles (up), following the machine | Testing jogs and offsets without hardware |
| Actuator state after a stop or alarm | No stop as such; after re-enable unknown (default AssumeUnknown) | **Unknown** (shown amber) until set again: grbl's reset switches the outputs off | The UI must not show a pump as on when it is off |
| Stop key | Ctrl+E toggles enable (the power button) | **Esc** on every page while enabled, plus a STOP button in the header | Safety |
| Click to move | **Shift+click** (or drag-jog) in the camera view; on a fixed camera it moves the selected nozzle | Shift+click (since 2026-11-25), head cameras and fixed up-looking cameras (`camera.center_on`); no drag-jog | – |
| Jog keys | Ctrl+arrows (X/Y), Ctrl+' Ctrl+/ (Z), Ctrl+, Ctrl+. (C), Ctrl+- Ctrl+= (step); **Shift = two steps finer** | Arrows, PgUp/PgDn, `,` `.` without Ctrl; Shift = two steps finer (since 2026-11-25); separate rotation step | Bare keys: the page has no text editing to clash with |
| Jog smaller than an axis step | Rounded away silently | Error `below_resolution` with the minimum step to offer. Default `tolerance`: half the `resolution`, or 0.0001 without one (OpenPnP's default resolution), so 0.01 jogs work (since 2026-11-25) | The error tells the operator why nothing moved |
| Safe Z | Moves at safe Z lift when X, Y **or rotation** change; "safe Z" covers every head mountable (head camera's virtual Z too); a nozzle without a safe zone is an error | Same (since 2026-11-25) | – |
| Board protection with a part | Z lowered by the part height before the safe-zone test | Same (since 2026-11-25) | – |
| Capturing positions | "Capture" buttons write the current position into a setting | `config.capture` returns the real position (read back from the controller) for the client to confirm and save; the camera view has "Set location from N1" and "Move N1 here" for up-looking cameras | Same idea; the value is shown before it's saved |
| Measuring units per pixel | Issues & Solutions: the nozzle tip (up cameras) or a calibration fiducial (down cameras) is found by vision while the machine moves it; flip, rotation and the camera location are then set automatically | Measure (up cameras, nozzle tip) reports mm/px, mirror and rotation, **Apply orientation** sets flip and rotation; down cameras: advanced calibration | Down cameras without advanced calibration: by hand |
| Editing while enabled | Most settings can be changed any time | Only calibration values while enabled (applied live); structural changes need a disabled machine | The machine is rebuilt from the file for structural changes |
| Several clients | One GUI | Any number of API clients; requests run in order on one motion thread | API-first |
| Errors | Java exceptions shown in dialogs | Named API errors (`not_homed`, `soft_limit`, `stopped`, ...) with data | Front ends switch on names |
| Configuration file | `machine.xml` (Simple XML), edited through the GUI | `machine.yaml`, edited through the API (validated, comments kept) or by hand | See `docs/config.md` |

### Nozzles, feeders, jobs and setup (checked 2026-11-25)

| Area | OpenPnP | TinkerPnP | Why |
|---|---|---|---|
| Head offset Z | Nozzle/camera head offsets X, Y and Z apply; rotation doesn't | Same (since 2026-11-25) | – |
| Pump | Modes None / PartOn (default) / TaskDuration / KeepRunning; off with the valve at place; off on disable when no part is carried | Same modes (`head.pump_control`) and off on disable (since 2026-11-25); PartOn switches off after the lift to safe Z, not with the valve | Harmless: the part is already released |
| Double-type vacuum actuator | `actuate(true/false)` uses its on/off double values | Same: `on_value` / `off_value` (since 2026-11-25) | – |
| Nozzle tips at run time | Tip changer (4 waypoints, 3 speeds, 3 post-step actuators), manual change, compatible tips, "unloaded" state; the job loads the tips it needs | Same (since 2026-11-25): `nozzle_tips.<id>.changer` incl. `after_middle1/2`, `after_end` actuations; manual change pauses the job (`nozzle.load_tip manual`); the loaded tip is kept in the state store | – |
| Pick/place dwell | Nozzle dwell + tip dwell | Same (since 2026-11-25) | – |
| Tip runout calibration | Per tip **and nozzle**; off by default (when off, not compensated); reset when the up camera or a nozzle offset changes; re-run on tip change in a job (default), home or by hand; ambiguous or too far (0.5 mm) detections discarded; no misdetections allowed by default | Same (since 2026-11-25), except re-run on tip change (no tip changer yet): `recalibrate: manual` or `machine_home` | – |
| Feeder choice and errors | Highest priority, then closest; feed retries (3); a failing feeder is disabled and the next one for the part tried | Same (since 2026-11-25): `priority`, `feed_retry_count`; a failed feeder is out of service (state store, not the config file) until `feeder.reset` | The config file isn't rewritten by a job |
| Tray feeder order | Inner loop along the shorter tray side | Same (since 2026-11-25) | – |
| Strip feeder | Taught by sprocket holes (EIA-481), vision on by default | Taught by first/last pocket centre + pitch, no vision | Simpler to teach; the importer converts |
| Feeder counter | `feedCount` in machine.xml, advanced on feed | `index` in the state store, advanced after a successful pick | A failed pick doesn't use up a part |
| Placement side | Only placements on the board side facing up | Same (since 2026-11-25) | – |
| Job pre-flight | Refuses to start: missing part, package, compatible tip, feeder, duplicate ref | Same (since 2026-11-25) | – |
| Job order and nozzle | Planned: grouped by nozzle tip, shortest path, placement rank; nozzle by compatible tip; every nozzle in a cycle | Same (since 2026-11-25): `order` nozzle_tips / part / part_height / unsorted, `rank`, pick-all-then-place-all cycles | – |
| Job start and end | Start: discard all. End / stop: safe Z, discard all, park (pump off for TaskDuration) | Same (since 2026-11-25); `machine.stop` aborts without cleanup moves | – |
| Error handling | Alert (pause) or Defer (retry later, up to 5, then mark errored) | Same (since 2026-11-25): job `error_handling`, `max_placement_retries`, per placement `error_handling` | – |
| Step | One processor step (feed, pick, align, place) | One whole placement | Simpler; fine for a first run |
| Dry run | – | Moves over each pick and place at safe Z | Checks a job without parts |
| Discard | Full location incl. rotation (default 0,0,0,0), with blow-off and dwell; no-op without a part | Same (since 2026-11-25) except: drops in place if unset | Not driving to 0,0 when nothing is set |
| Fiducials | Mark nearest to nominal, ≤ 4 mm from it; 3 passes, 0.2 mm; 3+ fiducials: affine fit; scale/shear 5 %, origin 5 mm | Same (since 2026-11-25); 2 fiducials: rigid fit; pass tolerance 0.03 mm | Tighter passes: more accurate |
| Machine Setup | Tree, class chooser, per-object tabs; Apply keeps changes in memory (saved on exit); unapplied edits dropped on selection change; search does nothing | Same layout; Apply saves the file (history, undo), Validate, asks before dropping changes, working search, references checked | Improvements |

## Details

### Park location

OpenPnP's head **park location** is where the head's default camera goes. The machine position is the park location
minus the camera's head offset. On the first machine, park 0,0 with a camera offset of (21.9, 20.0) asked for X −21.9 /
Y −20, and OpenPnP refused with *"Cant move ... lower than soft limit"*. The OpenPnP setup there now uses park
(22.9, 21.0) to land the head at X 1, Y 1.

TinkerPnP's `machine.park.location` is the **head's** X/Y in machine coordinates. What you write is where the
axes go. Parking always lifts every nozzle on the head to safe Z first, as OpenPnP's park does (its `safe-Z-park`
only concerns the Z park button).

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
| `safe-Z-park` | Z park button only (TinkerPnP has none yet); `park` always lifts first, as in OpenPnP |
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

OpenPnP's GUI stops motion by disabling the machine (the power button, Ctrl+E), which acts at once even mid-move;
the job Stop button stops the job after the current step (Stop → Abort → Cleanup: safe Z, discard, park).

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

- `toolpath` (default; what OpenPnP calls EuclideanAxisLimits, not its ToolpathFeedRate): the fastest feed along the path at which no axis
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
- `tolerance` is **not** OpenPnP's `acceptable-tolerance` (that one is only used by backlash calibration). OpenPnP
  suppresses a move closer than the axis `resolution` (default 0.0001); TinkerPnP suppresses one closer than
  `tolerance`, which defaults to half the `resolution` (0.0001 without one), and uses it for the safe-zone and limit
  tests.

### Several clients, one machine

OpenPnP is one desktop program. TinkerPnP is a server: any number of front ends and scripts can be connected. All
requests that move or switch things run one after another on one motion thread. Every client sees the same state
through status subscriptions, including `machine.selected_tool`, which OpenPnP keeps per GUI. Configuration changes
are refused while the machine is enabled (`busy`), so the running machine never changes under a move.
