# What OpenPnP has that TinkerPnP doesn't (yet)

The work list for "everything OpenPnP can do", in the order it gets a real board placed soonest. Each step ends with
tests, the simulator (`sim://grblhal`, simulated cameras) and, where it touches hardware, the bench.

Status marks: **done**, *partly* (what's missing is said), open (no mark).

Written from OpenPnP 2.x's documented features and file formats, and checked against its behaviour in the source on
2026-11-25 (no code taken from it: see the license note in `design.md`). Where TinkerPnP behaves differently, and
what is still to align: `openpnp-differences.md`.

## Already in TinkerPnP

Machine model and config (drivers, controller/mapped/virtual axes, heads, nozzles, nozzle tips as data, actuators,
cameras, static feeder), validated YAML config with live editing, history and undo; G-code driver with OpenPnP's
command model; enable/home/disable/stop, safe Z, soft limits, jog, park, macros; G-code console; cameras (V4L2,
simulated) with MJPEG streams, overlays, transforms, device matching and controls; capture of camera location and
measurement of units per pixel; web UI dashboard. Differences in behaviour: `openpnp-differences.md`.

## A. Data: parts, packages, boards, jobs

| OpenPnP | TinkerPnP |
|---|---|
| Parts (id, name, package, height, speed, pick retries) | **done** |
| Packages: footprint (pads, body size), compatible nozzle tips, bottom-vision settings | *partly*: data **done**; compatible tips aren't used yet (B4, D3), vision settings with E; pick vacuum / blow-off levels, tape specification missing |
| Boards: placements (ref, part, side, X/Y/rotation, type placement/fiducial, enabled), dimensions | **done** |
| Panels (arrays of boards, panel fiducials) | step A5 |
| Jobs: boards (and panels) with their location, side, enabled; placed status | **done** (panels: A5) |
| Parts/Packages/Boards/Jobs tabs | *partly*: Library page (JSON editor) and Jobs page; forms and a board view: A2 |
| Import: KiCad `.pos`, named CSV; Eagle, DipTrace, Altium | *partly*: KiCad `.pos` (ASCII and CSV) **done**; others later |
| OpenPnP files: `parts.xml`, `packages.xml`, `*.board.xml`, `*.panel.xml`, `*.job.xml` | step A4 (import and export) |

## B. Picking and placing

| OpenPnP | TinkerPnP |
|---|---|
| Pick: move to feeder, nozzle down, vacuum on, dwell, up | **done** |
| Place: move to placement, down to board + part height, vacuum off, blow-off, dwell, up | **done** (blow-off: B2) |
| Part height from part/package; dynamic safe Z with a part on the nozzle | *partly*: part height **done**; dynamic safe Z: B3 |
| Head vacuum pump control (pump on while any nozzle holds a part) | **done**: `pump_control` none / part_on / task_duration / keep_running, off on disable |
| Vacuum sensing: part-on / part-off checks with thresholds, retry | step B2 |
| Discard location (drop rejected parts) | *partly*: `nozzle.discard` with rotation, place dwell, no-op without a part, at job start and end **done**; blow-off missing (B2) |
| Rotation modes (absolute part angle, minimal rotation), limits per nozzle | step B3 |
| Nozzle tip changer (load/unload with 3–4 waypoints and speeds), automatic tip selection | step B4 |
| Nozzle tip cleaning | step B4 |

## C. Feeders

| OpenPnP | TinkerPnP |
|---|---|
| Static/single location | **done** (`static`) |
| Strip feeder (tape on the bed, pitch, reference holes; vision of sprocket holes) | **done** (`strip`: first/last pocket, pitch; vision: E3) |
| Tray feeder / rotated tray (rows × columns) | **done** (`tray`, any row/column direction) |
| Tube feeder (fixed pick, actuator/vibrator) | **done** (`auto` with an actuator, or `static`) |
| Auto feeder (actuator-driven: feed command, post-pick) | **done** (`auto`) |
| Servo feeders (the I2C servo HAT) | step C2 (plugin) |
| Push-pull feeder (actuator, OCR of feeder label) | step C3 |
| Drag feeder | step C3 |
| Loose part / heap feeder (vision finds parts) | step C4 (needs E) |
| Feeder banks / slots (feeders that move between slots) | step C3 |
| Feeder setup helpers (capture pick location, test feed, move camera to feeder) | *partly*: `feeder.feed`, `feeder.move_to_pick`, `feeder.reset` API; UI: C1b |
| Smart feeders (Photon) | later (plugin) |

## D. Jobs

| OpenPnP | TinkerPnP |
|---|---|
| Board location in the machine (manual: capture two placements or a corner) | *partly*: location + rotation per board, origin from the top camera, or measured from fiducials |
| Fiducial check (2 or 3 fiducials, board rotation and scale, per board) | *partly*: 2+ round fiducials, rigid fit (rotation + translation; scale checked, not applied), per board in the job or on request; OpenPnP's affine fit, 4 mm search distance and editable pipeline missing |
| Job processor: plan, feed, pick, (align), place, per placement, with several nozzles | *partly*: one nozzle at a time, placement side filter, pre-flight errors, nozzle by compatible tip, discard and park at start/end **done**; ordering and several nozzles missing (D3) |
| Start / pause / step / stop, skip placement, mark placed / not placed, resume after a stop | **done** (+ dry run) |
| Error handling: retry pick, skip, pause on error, feeder empty | *partly*: pause with the reason, resume retries, skip, feed retries, taking a failing feeder out and using the next one **done**; Defer mode, discard between pick retries missing |
| Bottom side placements (flipped board) | step D3 |
| Multi-nozzle planning (pick with several nozzles, then place), tip changes in the plan | step D3 |
| Panelized jobs, auto panelize, panel fiducials | step D3 |
| Job progress, placed counters, time estimates | *partly*: progress, placed, elapsed; estimates later |

## E. Vision

| OpenPnP | TinkerPnP |
|---|---|
| Pipeline engine (stages: image read, filters, threshold, contours, Hough circles, template match, ...) and editor | *partly*: engine with gray, blur, threshold, morph, circles, blobs, nearest (sizes in mm); more stages and the editor: E5 |
| Fiducial locator (round fiducials, by footprint), visual homing (homing fiducial) | *partly*: round fiducials (diameter from the fiducial's package pad), iterating onto the mark; visual homing later |
| Bottom vision: part rotation and offset, pre-rotate, multi-pass, footprint-based | step E2 |
| Nozzle tip calibration (runout compensation, model by angle) | **done**: the empty nozzle turns over an up-looking camera (12 angles), circle fit gives runout, phase and the axis position; saved per tip, moves compensated. Bench: N1/NT1 39 µm, fit within 4 µm. Since 2026-11-25 as OpenPnP: per tip and nozzle, off by default, reset when the camera or offsets change, re-run after homing (optional), ambiguous detections discarded. Missing: re-run on tip change (tip changer), use of the axis offset |
| Strip feeder hole vision | step E3 |
| Camera settling (fixed time or motion detection) | *partly*: fixed time (`settle`); motion detection later |
| Lens calibration (undistort), advanced camera calibration | step E4 |
| Camera light actuators (light on while imaging) | open: left out of E1 (OpenPnP: light actuator per camera, on before capture by default) |
| Vision compositing (large parts in several shots) | later |

## F. Machine and motion

| OpenPnP | TinkerPnP |
|---|---|
| GcodeAsyncDriver (no waiting for each move; confirmation flow control) | step F1 |
| Sub-drivers, several drivers per axis set | partly (several drivers, no sub-drivers) |
| Backlash compensation (one-sided, directional, sneak-up) | step F2 |
| Motion control: constant acceleration, S-curve, jerk, per-move acceleration | step F2 (simple + toolpath **done**) |
| Axis types: cam (CW/CCW), linear transform (skew/non-squareness) | step F3 |
| Kinematic / non-squareness calibration | step F3 |
| HTTP actuator, script actuator, actuator profiles, interlock actuators | step F4 |
| Signalers (sound, status light on errors and job end) | step F4 |
| Issues & Solutions (guided setup that finds problems and offers fixes) | step F5 |
| Nozzle offset calibration with the camera (both nozzles) | step F5 |

## G. Extensibility and UI

| OpenPnP | TinkerPnP |
|---|---|
| Scripts (JavaScript/Python) and script events (Job.Starting, Placement.Complete, ...) | step G1 (Python plugins and event hooks; macros **done**) |
| Placement/board view with parts drawn on the board | step G2 |
| Camera reticles (ruler, fiducial outline, package footprint overlay) | step G2 |
| Keyboard shortcuts for every action | partly (jog, stop) |
| Machine config import from `machine.xml` | step G3 |
| Config backup/restore | step G3 |

## Status (2026-11-24)

A1, B1, C1, D1 and D2 (with the first part of E1) are done: a KiCad board can be imported, put in a job and placed from strip, tray and auto
feeders on the simulator (`config/examples/simulated-grblhal.yaml` with `docs/examples/demo.pos`). Dry run on the
bench done (`../tinkerpnp-bench.md`), and E3 (nozzle tip runout) measured on the bench. Next: E2 (bottom vision),
B2 (vacuum sensing), A4 (OpenPnP files).

## Order

A1 → B1 → C1 → D1 gives a first complete job (manual board location, no vision) on the simulator and the bench.
Then A3 (KiCad import), E1 + D2 (fiducials), E2 (bottom vision), and the rest.
