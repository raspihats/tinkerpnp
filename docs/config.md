# Machine configuration format (draft, 2026-11-23)

Rules behind the machine YAML. Full example: [`examples/mc5s3dp-xhat.yaml`](examples/mc5s3dp-xhat.yaml).

## Structure

```yaml
format: tinkerpnp-machine/1     # format version; the loader migrates older versions
name: ...
include: [feeders/*.yaml]       # optional extra files, merged by section
machine: {...}                  # behaviour: enable/home/park sequences, jog rules, discard location
drivers: {id: {...}}
axes: {id: {...}}
heads: {id: {nozzles: {id: {...}}, cameras: {id: {...}}, actuators: [ids]}}
nozzle_tips: {id: {...}}
actuators: {id: {...}}
cameras: {id: {...}}            # machine-mounted cameras (head cameras live under their head)
feeders: {id: {...}}
vision: {...}
signalers: {id: {...}}
plugins: [pip package names]
```

- **Each section is a mapping from id to object.** The id is the YAML key; it's stable and used for references,
  API object names (`nozzle N1`, `axis z`) and OpenPnP import/export. `name` is free text for people.
- **`type`** picks the implementation from the type registry (built-ins + plugins). Each type has its own settings,
  described by a JSON Schema the API serves (`config.types`).
- **References are ids:** `driver: grbl`, `axes: {z: z_n2}`, `vacuum: valve1`. The loader checks that every
  reference exists and has the right kind, and reports all problems at once.
- **Unknown keys are an error** (catches typos), except under `x-*` keys, which are kept untouched for user notes and
  for data preserved from imports (e.g. `x-openpnp:` with attributes TinkerPnP doesn't use yet).

## Values

- **Units:** a bare number uses the default unit of the field (mm, deg, mm/s, mm/s², ms). A string with a unit is
  converted: `"8000 mm/min"`, `"2 in"`, `"0.5 s"`.
- **Locations:** `{x, y, z, rotation}`, any subset. Machine coordinates unless the field says otherwise.
- **Ranges:** `[low, high]` (soft limits, safe zone, rotation limits).
- **Booleans and enums** are plain YAML (`true`, `off`, `always`).
- **Strings with special characters:** G-code templates and regexes go in quotes; `"\u0018"` (YAML double-quoted
  escape) is the Ctrl-X byte.

## Key concepts

- **Axes and transforms.** `controller` axes belong to a driver and have a G-code letter. `mapped` (linear map of
  one input axis), `cam` (CW/CCW cam), `linear_transform` (combination of several axes) and `virtual` (no hardware)
  are computed. A nozzle on a seesaw Z simply names a `mapped` axis as its Z.
- **Safe zone** on a Z axis: the range in which every nozzle using it is clear of the work. The motion layer moves
  into it before any X/Y travel, for jogs too (`machine.jog.safe_z`).
- **Offsets:** head-mounted objects have an `offset` from the head's reference point. Machine position of an
  object = head position + offset. Machine-mounted cameras have a fixed `location` instead.
- **Acceleration where it applies:** axes can say `acceleration_applies_in: firmware` (informational, the value
  lives in the controller) or `driver` (sent with each move, if the driver has a command for it). The UI shows which.
- **Device matching for cameras:** a list of selectors tried in order (USB serial, USB vendor:product id, stable
  `/dev/v4l/by-path` or `by-id` paths). USB port paths break when cables move, so they're the last resort.
- **Sequences:** `machine.on_enable` and `on_home` are lists of steps, so behaviour is configured, not coded:
  `home`, `park` (safe Z, then the head to `machine.park.location`), `safe_z`, `{actuate: {pump: off, ...}}`,
  `{dwell: 200}` (ms). The loader checks them; `home` inside `on_home` is refused (it would loop).
- **Homing:** `controller` axes with `homing: true` have a home switch and are set to their `home` position.
  The default is true for linear axes and false for rotation axes; those keep their position and count as homed
  once the machine homes. Disabling the machine releases the motors, so it must be homed again.
- **Simulated driver:** `time_scale` (1 = moves take distance / feed, 0 = instant) and `home_time` make the
  simulated machine behave like a real one for UI work, or instantly for tests.

## Editing and persistence

- The file is the source of truth. Edits through the API are validated, applied, and written back immediately,
  keeping comments and ordering (ruamel.yaml). Each edit is logged with undo.
- Values that can't change while running (e.g. a driver's port) are marked in the schema; the API answers with
  "applies after reconnect/restart".
- Secrets (API keys, passwords) never go in this file.

## Relation to OpenPnP's machine.xml

| OpenPnP | TinkerPnP |
|---|---|
| `<axis class="...ReferenceControllerAxis" id=... letter=...>` | `axes: {id: {type: controller, letter: ...}}` |
| `ReferenceMappedAxis` with `map-input-0/1`, `map-output-0/1` | `type: mapped`, `map: [[in0, out0], [in1, out1]]` |
| `<nozzle axis-Z-id=... head-offsets=...>` | `heads.H1.nozzles.N1: {axes: {z: ...}, offset: {...}}` |
| GcodeDriver `<command type=... head-mountable-id=...>` | Driver `commands:` (same names, same templates); actuator commands move to the actuator (`on:` / `off:`) |
| Park location = where the head's camera goes | `machine.park.location`: the **head's** X/Y in machine coordinates (no camera offset), Z goes to safe Z first. Converted on import; see [`openpnp-differences.md`](openpnp-differences.md) |
| Camera `unique-id` (includes USB port) | `device:` selector list |
| Settings saved on normal exit | Saved on every edit |

The importer (phase 3) maps these automatically and puts anything it can't map under `x-openpnp`.
