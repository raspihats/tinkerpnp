# Where TinkerPnP differs from OpenPnP

TinkerPnP aims to be compatible with OpenPnP's **files** (parts, packages, boards, jobs, machine configuration), not
to copy its **behaviour** in every detail. This page lists every place where TinkerPnP deliberately or (for now)
unavoidably behaves differently. Importers convert where they can; the "On import" column says how.

Keep this page current: every change that makes TinkerPnP behave differently from OpenPnP adds a row here.

Sources for the OpenPnP side: OpenPnP 2.7 as set up on the first machine (`../openpnp-setup.md` in the
pnp-machine folder), its `machine.xml` (`tools/openpnp-config/`), and what was observed on that machine. Points that
weren't checked against OpenPnP's source or a running OpenPnP are marked *not verified*.

Status: as of phase 1, step 1 (motion core on the simulated driver, 2026-11-23).

## Summary

| Area | OpenPnP | TinkerPnP | Why |
|---|---|---|---|
| Park location | Where the head's **default camera** goes | Where the **head** goes (head X/Y, no offset) | Parking at 0,0 pushed the head past its soft limits on the first machine |
| Safe Z on moves | Done by the caller (e.g. "move to location at safe Z"); a plain move doesn't lift | Every `move_to` with X/Y travel lifts first, unless `safe_z: none` | Safe by default; API clients can't forget it |
| Unsafe Z roaming | X/Y moves below safe Z allowed within `unsafe-Z-roaming-distance` | Not supported: always lift before X/Y travel | Simpler and safer; may be added |
| Dynamic safe Z | Per nozzle (`enable-dynamic-safe-z`): safe Z accounts for the part height | Not yet | Comes with parts/jobs (phase 5) |
| Soft-limit check | Per motion command | All targets of a whole move (lift, travel, lower) are checked **before** anything moves | A refused move never half-runs |
| Rotation limit | `limit-rotation`: angles are **normalized** into ±180° | `limit_rotation: [-180, 180]` **refuses** targets outside the range | Known difference, to fix: normalize like OpenPnP |
| Rotation wrap-around | `wrap-around-rotation` | Not yet | |
| Homing of rotation axes | Every axis is set to its `home-coordinate` after homing | Axes with `homing: false` (default for rotation) keep their position | grblHAL doesn't home A/B; their position survives `$H` (home all axes) |
| Homing a subset | Whole machine (`HOME_COMMAND` per driver) | `machine.home(axes)` can home some axes | Recovery after a stall on one axis |
| What happens after enable / homing | Checkboxes: `home-after-enabled`, `park-after-homed`, `safe-Z-park` | Step lists: `machine.on_enable`, `machine.on_home` | Configurable instead of fixed |
| Actuators on enable / home / disable | `enabled-actuation`, `homed-actuation`, `disabled-actuation` (ActuateOn/Off, LeaveAsIs, AssumeUnknown) | `on_enable` / `on_disable` values per actuator; on home: an `actuate` step in `on_home` | Same effect, fewer special cases |
| Stopping motion | Power button (disable the machine); job Stop stops the job | `machine.stop`: aborts the running move, cancels queued requests, machine stays enabled where the controller allows | API clients and a future e-stop button need an abort that isn't "disable" |
| Disabling | Motors released by the driver's `DISABLE_COMMAND` | Same, and the machine is marked **not homed** | Released motors lose position |
| Speed | Fraction of max; motion planner (`motion-control-type`, e.g. ToolpathFeedRate) computes feed, acceleration, jerk | Fraction of each axis's feed; simulated driver moves each axis at its own feed; G-code feed calculation comes with the gcode driver | Not decided yet; see below |
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
TinkerPnP), and both lift all nozzles of the head before X/Y travel. On the first machine, OpenPnP raised Z to 57
before the first X/Y jog, as TinkerPnP does.

The difference is where it happens. In OpenPnP, lifting is a step the calling code takes ("move to location at safe
Z"); a plain move of a head-mountable goes straight there (*not verified for every caller*). In TinkerPnP it's part
of `motion.move_to` itself, the only way to move. The order is:

1. every nozzle on the head into its safe zone (on raw axes, so a shared seesaw Z moves once)
2. X, Y and rotation
3. the object's own Z, if the target has one

A move without X/Y travel moves all axes together. `safe_z: none` turns lifting off for one move; jogs follow
`machine.jog.safe_z` (`always` or `never`).

Not supported (yet): OpenPnP's `unsafe-Z-roaming-distance` (small X/Y moves allowed below safe Z) and dynamic safe Z
(`enable-dynamic-safe-z`, which accounts for the height of the part on the nozzle).

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
`wrap-around-rotation` lets the axis take the shortest way. TinkerPnP currently treats `limit_rotation: [-180, 180]`
as a hard range: a target of 200° is refused with `soft_limit`. **This is a known difference to fix:** angles should
be normalized like in OpenPnP. Wrap-around isn't implemented.

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

TinkerPnP's `speed` is also a fraction (0–1) of the axis feed. The simulated driver moves each axis at its own feed
and doesn't simulate acceleration. How the gcode driver computes `F` for a multi-axis move, and whether it sends
acceleration (`acceleration_applies_in: driver`), is decided in phase 1, step 2. That will be documented here.

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
