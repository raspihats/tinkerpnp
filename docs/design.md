# TinkerPnP design (draft 2)

Status: draft 2, 2026-11-23. Rewritten after the scope decision: **as configurable as OpenPnP or more, compatible
with OpenPnP's files, and API-first like Klipper + Moonraker**, so any number of front ends can exist. Stack decided
in draft 1: Python 3 + FastAPI backend, Svelte default UI, SQLite data, YAML machine configuration.

Items marked **Decision needed** are open. Everything else is a proposal to challenge.

## 1. Goals

1. **Fully configurable machine model**, at least as general as OpenPnP's: any number of drivers, axes (incl.
   transformed axes such as shared/seesaw Z, cam Z, linear transforms), heads, nozzles, nozzle tips and changers,
   cameras, actuators, feeders, signalers. No machine-specific code in the core.
2. **API-first.** A documented, versioned API is the product boundary, like Moonraker in the 3D-printing world. The
   bundled web UI is one client of that API; others (alternative UIs, tablet apps, scripts, MES integration) use the
   same API and get the same capabilities.
3. **OpenPnP compatible.** Import, and where possible export, OpenPnP's files: parts, packages (footprints), boards,
   panels, jobs, and the machine configuration, so users can move in both directions.
4. **Python 3 extensibility.** New feeder, actuator, camera or driver types are small Python classes (plugins), with
   full access to modern Python libraries (I2C servo HATs, OpenCV, numpy).
5. **Runs on a Raspberry Pi** next to the machine (Pi 4 / CM4 / Pi 5, 64-bit OS); also runs on a PC with a simulated
   machine for development and testing.
6. **Safe by default:** never moves unhomed, safe Z before X/Y travel, soft limits checked before anything is sent,
   a stop that always works.

### Non-goals (for now)

- Step generation in TinkerPnP. Steps come from an MCU: either a G-code controller (grblHAL, Marlin, Smoothieware,
  Duet, ...) or Klipper (klippy on the Pi + Klipper MCU firmware). TinkerPnP is the host, like OpenPnP.
- User accounts with roles (single shared password / API keys only).

## 2. The Klipper / Moonraker / Fluidd model

In 3D printing, three layers are separate projects with clean boundaries:

| 3D printing | Role | TinkerPnP equivalent |
|---|---|---|
| Klipper (klippy) | Machine runtime: config, kinematics, drivers | **tinker-core**: machine model, motion, drivers, vision, job engine |
| Moonraker | API server: JSON-RPC over WebSocket + HTTP, files, auth, updates, webcams | **tinker-api**: the public API |
| Fluidd / Mainsail | Interchangeable web UIs | **TinkerUI** (bundled, Svelte) + any third-party UI |

What to copy from Moonraker's API style, because front-end authors already know it:

- **JSON-RPC 2.0 over a WebSocket**, with an HTTP mirror of every method (`GET/POST /machine/...`).
- **Object model with query/subscribe:** the runtime exposes named objects (`axis x`, `nozzle N1`, `camera Bottom`,
  `feeder F1`, `job`, `machine`). A client calls `machine.objects.subscribe` with the fields it wants and then
  receives `notify_status_update` deltas. This is Moonraker's `printer.objects.subscribe` pattern.
- **Server-side file storage** (boards, jobs, `.pos` files, OpenPnP XML) with upload/download/list endpoints.
- **Webcam registry:** the API lists cameras with their stream URLs; UIs embed them.
- **Notifications** for state changes, alarms, job progress and log lines.
- **Auth:** API keys and an optional login, trusted-network mode for the bench.

What not to copy: **TinkerPnP is not Moonraker-compatible.** Fluidd and Mainsail are built around printer concepts
(extruders, heaters, G-code print files); running them against a PnP machine wouldn't make sense. TinkerPnP uses the
same *conventions* so the step to a PnP front end is small, not the same method names.

**Decision needed:** one process or two. Klipper/Moonraker split because klippy is a real-time host. Here grblHAL does
the real-time work, so **one process** (core + API layer in the same asyncio program, with the API as a strict module
boundary) is simpler. The API layer only calls the core's public Python interface, so it could be split into its own
process later.

## 3. Machine model

### 3.1 Objects and types

Everything on the machine is an **object** with a **type**, a unique **id**, a human **name** and type-specific
settings. Types come from a registry: built-in types plus plugins (Python entry points, `tinkerpnp.plugins`).

| Kind | Built-in types (first) | OpenPnP equivalent |
|---|---|---|
| Driver | `gcode` (OpenPnP-style command templates), `simulated` | GcodeDriver, GcodeAsyncDriver, NullDriver |
| Axis | `controller`, `virtual`, `mapped` (linear map), `cam` (CW/CCW), `linear_transform` | ReferenceControllerAxis, ReferenceVirtualAxis, ReferenceMappedAxis, ReferenceCam*Axis, ReferenceLinearTransformAxis |
| Head | `head` | ReferenceHead |
| Nozzle | `nozzle` | ReferenceNozzle |
| Nozzle tip | `nozzle_tip` (incl. changer locations) | ReferenceNozzleTip |
| Actuator | `gcode`, `python` (any callable), `http`, `script` | ReferenceActuator, HttpActuator, ScriptActuator |
| Camera | `v4l2` (OpenCV/V4L2 USB), `mjpeg_url`, `image` (static), `simulated` | OpenPnpCaptureCamera, Webcam, ImageCamera, Simulated*Camera |
| Feeder | `strip`, `tray`, `tube`, `push_pull` (actuator driven), `servo` (plugin), `auto` (G-code/actuator), `rotated_tray` | ReferenceStripFeeder, ReferenceTrayFeeder, ReferenceTubeFeeder, ReferencePushPullFeeder, ReferenceAutoFeeder, ReferenceRotatedTrayFeeder |
| Vision | `pipeline` (stage list, like CvPipeline) | CvPipeline |
| Signaler | `actuator`, `sound` | ActuatorSignaler, SoundSignaler |

Objects reference each other by id (a nozzle names its X/Y/Z/rotation axes, a camera its light actuator, a feeder its
actuator and vision pipeline). This is OpenPnP's model, kept on purpose: it's what makes unusual machines possible
(shared Z, cam Z, multiple drivers, axes on different controllers).

### 3.2 Configuration

Format details: [`config.md`](config.md); full example for the first machine:
[`examples/mc5s3dp-xhat.yaml`](examples/mc5s3dp-xhat.yaml).

- **YAML**, one file per machine plus optional includes (`include: feeders/*.yaml`), so large feeder banks stay tidy.
- Every type publishes a **JSON Schema** of its settings (from pydantic models). The API serves these schemas, so a
  front end can render a settings form for *any* object type, including plugin types it has never seen. This is
  where TinkerPnP can be more configurable than OpenPnP without UI code per type.
- Changes come in through the API: validate → apply to the running machine where safe (or flag "restart needed") →
  write back to YAML, keeping comments (ruamel.yaml). Every change goes into a change log, with undo.
- Units: millimetres and degrees internally; configs may write `inch` values with units, like OpenPnP's `Length`.

### 3.3 Motion and safety

- One function moves anything: `move_to(head_mountable, location, speed)`. It resolves offsets and axis transforms,
  checks homed state and soft limits, plans safe-Z travel (raise all nozzles sharing the head before X/Y moves), then
  hands axis targets to the drivers. Jogging goes through it too.
- Per-axis feed, acceleration and jerk; drivers send them if the controller supports it (`M204`/`M205` (set
  acceleration / jerk) on grblHAL with its OpenPnP plugin, Marlin, Duet) or leave them to the firmware.
- Machine states: disconnected, connected/alarm, homed/idle, busy, paused, error, disabled (sleep).
- Stop: feed hold (`!`), then soft reset (Ctrl-X) on grbl-type controllers; configurable per driver.

### 3.4 Motion backends (drivers)

TinkerPnP is a host. Real-time step generation always happens on an MCU; TinkerPnP supports the two ways the hobby
world does this, plus one for later. A machine can use several drivers at once (e.g. X/Y on one, Z/rotation on
another), as in OpenPnP.

| Backend | Who plans motion | Hardware | Use when |
|---|---|---|---|
| **`gcode`**: G-code controller | The controller firmware | grblHAL, Marlin, Smoothieware, Duet/RepRapFirmware, TinyG; any board that takes G-code over serial/USB/TCP | Existing OpenPnP machines; turnkey controller boards |
| **`klipper`**: Klipper as the motion system | klippy on the same Linux computer (Pi/CM4) | Any Klipper-supported MCU board (STM32, RP2040, ...), incl. CM4 carrier boards with an on-board STM32 and CAN toolhead boards | New builds; cheap boards; input shaping; many motors |
| `native` (later) | TinkerPnP itself, talking Klipper's MCU protocol | Klipper MCU firmware | Only if the `klipper` backend proves too limiting |

All backends implement the same interface: `connect`, `enable`/`disable`, `home(axes)`, `move(targets, feed,
accel)`, `wait_still`, `position`, `actuate(output, value)`, `read(input)`, `stop`, `raw(command)`, plus capability
flags (`coordinated_axes`, `per_move_acceleration`, `home_per_axis`, `outputs`, `inputs`). The motion layer
(3.3) only uses this interface; nothing above it knows which backend is in use.

#### `gcode` backend

- Uses **OpenPnP's command model and template syntax** (`CONNECT_COMMAND`, `MOVE_TO_COMMAND`, `{X:X%.4f}`,
  `POSITION_REPORT_REGEX` with named groups, ...), so an OpenPnP GcodeDriver configuration imports unchanged.
- Built-in **firmware profiles** (Marlin, Smoothieware, Duet, TinyG, grblHAL) fill in the commands and quirks, e.g.
  the grblHAL profile knows `?` (status report) has no `ok`, reset + newline = one `ok`, `ALARM:11` (homing required)
  after reset, `$SLP` (sleep) to release motors.
- Transports: serial, USB serial, TCP (e.g. Duet over Ethernet), simulated.

#### `klipper` backend

Klipper splits motion into **klippy** (a Python host on Linux: config, kinematics, planning, input shaping) and
**MCU firmware** that executes timed step schedules. On a CM4-class carrier board both run on the same PCB: klippy on
the CM4, MCU firmware on the board's STM32. TinkerPnP runs next to klippy on the same computer.

- **Connection:** klippy's API socket (Unix domain socket, JSON; `klippy -a /tmp/klippy_uds`), or **Moonraker**'s
  JSON-RPC if Moonraker is installed anyway. **Decision needed:** proposal: talk to the klippy API socket directly,
  so Moonraker isn't required; support Moonraker as an alternative transport.
- **Commands:** G-code through `gcode/script` (`G28`, `G1`, `M400` (wait for moves to finish), `SET_PIN`,
  `MANUAL_STEPPER`); state through `objects/subscribe` (`toolhead.position`, `toolhead.homed_axes`,
  `print_stats`/`idle_timeout` for busy/idle, pins, endstops). Errors arrive as `!!` responses and shutdown states.
- **Kinematics mapping:** X/Y/Z map onto Klipper's cartesian (or CoreXY) kinematics. Extra motors (nozzle rotations,
  a second Z, feeder steppers) are `manual_stepper`s registered as extra G-code axes (`MANUAL_STEPPER
  STEPPER=... GCODE_AXIS=A`, supported since May 2025), so they move in sync with X/Y/Z in one `G1`. To verify:
  homing of such axes (it wasn't supported with `G28` when the feature appeared; `MANUAL_STEPPER ... MOVE/SET_POSITION`
  with an endstop works).
- **TinkerPnP writes `printer.cfg`** (or the included part of it) from its own machine configuration: steppers,
  pins, endstops, kinematics, limits, output pins for pump/valves/lights. The user configures the machine once,
  in TinkerPnP; Klipper's config is generated, like the OpenPnP `machine.xml` generator used during this machine's
  bring-up (`../tools/openpnp-config/`).
- **Outputs and inputs:** pump/valves/lights as Klipper `output_pin` / `pwm` pins (`SET_PIN`), vacuum sensors as
  ADC inputs, feeders as `manual_stepper`, servo (`[servo]`) or anything Klipper can drive. Pi-side hardware (I2C
  servo HAT) stays a TinkerPnP plugin.
- **Holding motors:** Klipper keeps steppers enabled after moves until `M84` (motors off) or its idle timeout; the
  backend disables the idle timeout while the machine is enabled (the spring-loaded Z on the first machine needs that).
- What klippy brings for free: input shaping (less ringing on fast moves), multi-MCU (CAN toolheads), accurate
  step timing, a large tested ecosystem of boards, drivers and pin definitions.

#### `native` backend (later, only if needed)

Klippy's planner is built for printing. If PnP-specific motion (e.g. coordinated multi-nozzle moves, S-curve on
rotation axes) needs more than Klipper offers, TinkerPnP could drive Klipper MCU firmware directly with its own
planner. That is essentially rewriting klippy, so it stays out of scope unless the `klipper` backend hits a hard
limit.

### 3.5 Cameras and vision

- Capture per camera in its own thread; MJPEG over HTTP to clients (WebRTC later); a watchdog reopens dead streams
  (seen during bring-up: USB `-71` errors, a crashed USB controller).
- Calibration as data: units per pixel, lens distortion, camera-to-head offset, and later OpenPnP-style advanced
  calibration.
- Vision **pipelines** as an ordered list of stages, like OpenPnP's CvPipeline, so pipelines can be imported where the
  stages exist. Custom stages are Python functions (plugin), with numpy/OpenCV available.
- Uses: nozzle tip calibration, bottom vision (part alignment), fiducials, feeder hole/pocket detection.

### 3.6 Jobs

- Data model compatible with OpenPnP: **job → board/panel placements → part → package → footprint**; feeders hold
  parts.
- Imports: KiCad `.pos`, OpenPnP `board.xml` / `panel.xml` / `job.xml`, and later Altium/Eagle like OpenPnP's
  importers.
- Planner: group by nozzle tip and feeder, pick multiple parts per trip on multi-nozzle heads, pause/resume/step.

### 3.7 Storage

- **SQLite** for parts, packages, footprints, feeders' contents, boards, jobs, history (placements done, vision
  results). One file, safe concurrent access from API requests.
- **YAML** for the machine configuration (diff-able, kept in git by the user).
- **Files** in a managed directory for uploads (`.pos`, OpenPnP XML, images), exposed through the API's file manager.

## 4. OpenPnP compatibility

OpenPnP's XML files are the interchange format. Plan:

| OpenPnP file | Content | Import | Export |
|---|---|---|---|
| `parts.xml` | Part id, name, height, package, speed | Yes | Yes |
| `packages.xml` | Package id, footprint (pads, body), vision settings, compatible nozzle tips | Yes | Yes |
| `*.board.xml` | Board placements, fiducials, dimensions | Yes | Yes |
| `*.panel.xml` | Panelisation | Yes | Yes |
| `*.job.xml` | Boards/panels in a job, locations, enabled flags | Yes | Yes |
| `machine.xml` | Whole machine: drivers, axes, heads, nozzles, tips, cameras, actuators, feeders | Yes, best effort with a report of anything unsupported | Later, for the types TinkerPnP supports |
| `vision-settings.xml` | Bottom/fiducial vision settings and pipelines | Stages that exist; report the rest | Later |

Rules:

- Import never silently drops data: unknown classes and attributes are listed in an import report and kept in a raw
  form so an export can write them back.
- IDs are kept (OpenPnP's `id`s for parts, packages, feeders, nozzles), so files exported again stay recognisable in
  OpenPnP.
- Round-trip tests: OpenPnP 2.x sample files (OpenPnP ships some in `samples/`) and this machine's own configuration
  (`../tools/openpnp-config/`) import → export → compare.

**To verify against real files** (the Pi was offline when this was written): exact element/attribute names of
`parts.xml`, `packages.xml`, board/job/panel files and `vision-settings.xml` in OpenPnP 2.7. The machine.xml format is
known from the bring-up (`../openpnp-setup.md`, "How the XML field names were found").

## 5. API sketch

Superseded by the full specification in [`api.md`](api.md); the table below is the original overview.

Namespaces (JSON-RPC methods, mirrored over HTTP):

| Namespace | Examples |
|---|---|
| `server.*` | `server.info`, `server.config`, `server.restart`, `server.logs.subscribe` |
| `machine.*` | `machine.state`, `machine.enable`, `machine.disable`, `machine.home`, `machine.stop`, `machine.objects.list/query/subscribe` |
| `motion.*` | `motion.move_to`, `motion.jog`, `motion.park`, `motion.position` |
| `config.*` | `config.types` (with JSON Schemas), `config.objects`, `config.update`, `config.undo` |
| `actuator.*` | `actuator.set`, `actuator.read` |
| `feeder.*` | `feeder.feed`, `feeder.pick_location`, `feeder.setup` (guided) |
| `camera.*` | `camera.list` (with stream URLs), `camera.snapshot`, `camera.calibrate` |
| `vision.*` | `vision.run(pipeline, camera)`, `vision.pipelines` |
| `data.*` | parts, packages, boards, jobs CRUD |
| `job.*` | `job.load`, `job.start`, `job.pause`, `job.resume`, `job.step`, `job.status` |
| `files.*` | `files.list`, `files.upload`, `files.download`, `files.delete` |
| `import.*` / `export.*` | `import.openpnp`, `import.kicad_pos`, `export.openpnp` |
| `gcode.*` | `gcode.send` (console), `gcode.subscribe` (raw driver traffic) |

Notifications: `notify_status_update`, `notify_machine_state`, `notify_job_progress`, `notify_log`,
`notify_config_changed`.

The API version is part of `server.info`; breaking changes bump the major version. An OpenAPI document (from FastAPI)
covers the HTTP side; a hand-written spec covers JSON-RPC methods and notifications.

## 6. Lessons from the bring-up of the first machine

Requirements that came out of setting up OpenPnP on the MC5S3DP-XHAT machine (details in `../grblHAL-bringup.md` and
`../openpnp-setup.md`):

- grbl replies must be counted line by line; `?` (status report) is real-time and only answered with `ok` if followed
  by a newline; Ctrl-X + newline gives exactly one `ok`; after waking from sleep `ALARM:11` comes first.
- Z can work against a spring: drivers stay on from homing to disable; disable = `$SLP` (sleep), enable = soft reset.
- Safe Z before every X/Y move, including jogs; OpenPnP doesn't do this for jogs.
- Park after homing; park location in machine coordinates, not "where the camera goes".
- Configuration edits must persist immediately. OpenPnP only saves on a normal exit, which caused lost edits.
- Camera device IDs that include the USB port break when cables move; identify cameras by serial number or USB
  path with a fallback, and let the user re-pick from a list.
- Cameras drop out; detect dead streams and reopen without restarting anything.
- Acceleration is set in the firmware unless the driver sends it; the UI should show where a value actually applies.

## 7. Phases

0. **Skeleton:** object registry, YAML config with schemas, simulated driver and camera, API server with
   `server.*`, `machine.objects.*`, `config.*`, WebSocket notifications, minimal TinkerUI showing objects and state.
1. **Machine console:** `gcode` backend (first profile: grblHAL, the board at hand) and `klipper` backend (klippy API
   socket), enable/home/disable, jog with safe Z, actuators, live cameras, G-code console.
2. **Setup:** generic settings forms from schemas, capture-position helpers, nozzle offsets, camera calibration
   (units per pixel, location), persist and undo.
3. **OpenPnP import/export:** parts, packages, boards, jobs; then machine.xml import with report.
4. **Feeders:** strip, tray, push-pull; servo feeder plugin for the I2C servo HAT.
5. **Jobs:** KiCad and OpenPnP job import, board alignment (fiducials or manual), run/pause/step.
6. **Vision:** pipelines, nozzle tip calibration, bottom vision, fiducials.
7. **Second front end proof:** a minimal alternative UI or a CLI client using only the public API, to prove the
   boundary.

## 8. Open decisions

| # | Question | Proposal |
|---|---|---|
| 1 | Backend web framework | **Decided:** FastAPI + uvicorn |
| 2 | Data storage | **Decided:** SQLite for data, YAML for the machine configuration |
| 3 | Bundled front end | **Decided:** Svelte (TinkerUI) |
| 4 | Repository | **Decided:** `git@github.com:raspihats/tinkerpnp.git` (existing, initial commit 2019), branch `master`, cloned in `pnp-machine/TinkerPnP/` |
| 5 | One process or core + API as two processes | One process, strict module boundary |
| 6 | API protocol | JSON-RPC 2.0 over WebSocket + HTTP mirror, Moonraker-style object subscriptions |
| 7 | Plugin mechanism | Python entry points (`tinkerpnp.plugins`), pip-installable plugin packages |
| 8 | Config format details | YAML with includes, ruamel.yaml to keep comments; JSON Schema per type |
| 9 | Video to clients | MJPEG first, WebRTC later |
| 10 | Repo layout | Monorepo: `core/`, `api/`, `ui/`, `plugins/`, `docs/`; UI could move to its own repo later like Fluidd |
| 11 | License | **Open, the owner is deciding.** The repo currently has an MIT `LICENSE` (2019); switching to GPLv3 means replacing it before any OpenPnP-derived code. GPLv3: may copy/translate OpenPnP code (also GPLv3), derivatives and in-process plugins stay GPL, API front ends can use any license (as Fluidd/Mainsail on Moonraker; Klipper, Moonraker, Fluidd are GPLv3). MIT: anyone may use it, incl. closed commercial products, but no OpenPnP code may be copied or translated; everything written independently from formats and docs. **Until decided, write no code derived from OpenPnP's source**, so both options stay open |
| 13 | Klipper connection | klippy API socket directly (no Moonraker required); Moonraker as optional transport |
| 14 | Klipper config ownership | TinkerPnP generates `printer.cfg` (or an included part) from its machine config |
| 12 | Name of the API objects | Follow OpenPnP's names (nozzle, nozzle tip, head, feeder) so OpenPnP users feel at home |
