# TinkerPnP API (draft 1)

Status: draft 1, 2026-11-23. API version **0.1** (unstable until 1.0). Companion to [`design.md`](design.md)
(section 2: why API-first, section 5: the first sketch this replaces).

The API is the product boundary: the bundled UI (TinkerUI) uses nothing that isn't documented here, so any other
front end, tablet app, script or MES integration can do the same. The style follows Moonraker, so authors of
Fluidd/Mainsail-style front ends feel at home; method names are TinkerPnP's own.

## 1. Transport

| Channel | URL | Use |
|---|---|---|
| WebSocket | `ws://<host>:<port>/websocket` | JSON-RPC 2.0 requests, responses and notifications. The main channel |
| HTTP | `POST /api/<method path>` (e.g. `POST /api/machine/home`) | The same methods, one request each. `GET` is allowed for methods marked *read-only* |
| HTTP | `/cameras/<id>/stream.mjpg`, `/cameras/<id>/snapshot.jpg` | Video (section 9) |
| HTTP | `/files/...` | Upload/download (section 13) |
| HTTP | `/api/openapi.json`, `/api/docs` | Generated OpenAPI description of the HTTP side |

Default port **7300**. JSON everywhere, UTF-8.

**Method names** are dotted (`machine.objects.subscribe`). The HTTP path replaces dots with slashes
(`/api/machine/objects/subscribe`); subscriptions only make sense over the WebSocket.

### 1.1 JSON-RPC over WebSocket

```json
→ {"jsonrpc": "2.0", "id": 7, "method": "motion.move_to",
   "params": {"object": "nozzle/N1", "location": {"x": 120, "y": 80}}}
← {"jsonrpc": "2.0", "id": 7, "result": {"location": {"x": 120.0, "y": 80.0, "z": 57.0, "rotation": 0.0}}}
```

Notifications from the server have no `id`:

```json
← {"jsonrpc": "2.0", "method": "notify_status_update",
   "params": {"status": {"nozzle/N1": {"location": {"x": 120.0}}}, "eventtime": 1532.208}}
```

Batch requests (JSON arrays) are supported. Requests on one connection are handled in order of arrival unless a
method is documented as *immediate* (`machine.stop`, `motion.jog_cancel`, `job.pause`), which bypass the queue.

### 1.2 HTTP

```
POST /api/motion/move_to        {"object": "nozzle/N1", "location": {"x": 120, "y": 80}}
200 {"result": {"location": {...}}}
4xx/5xx {"error": {"code": -32010, "message": "Machine not homed", "data": {...}}}
```

## 2. Connection, authentication, versioning

### 2.1 Identify

Every WebSocket client should identify itself first (shown in the UI's client list and logs):

| Method | Params | Result |
|---|---|---|
| `server.connection.identify` | `client_name`, `version`, `type` (`web`, `mobile`, `script`, `service`), `url` (optional), `api_key` (optional) | `{"connection_id": 4}` |

### 2.2 Authentication

- **Trusted networks** (configured CIDR ranges, default: none) need no credentials.
- **API keys**: `X-Api-Key` header (HTTP) or `api_key` in `server.connection.identify` / `?api_key=` (WebSocket).
- **One-shot tokens** for URLs that can't carry headers (camera streams in `<img>` tags, file downloads):
  `access.oneshot_token` → `{"token": "..."}`, valid 5 s for one request, appended as `?token=`.
- Login with a password (`access.login` → API key for the session) for the browser UI, optional.

Unauthenticated requests get error `-32020` (HTTP 401). There are no roles in 0.x: any authenticated client can do
everything.

### 2.3 Versioning

`server.info` returns `api_version` (`"0.1"`). Within a major version, changes are additive only (new methods, new
optional params, new fields). Clients must ignore fields they don't know.

## 3. Common types

| Type | JSON | Notes |
|---|---|---|
| Length | number, mm | Inputs may also be strings with units: `"2 in"`, `"0.5 mm"` |
| Angle | number, degrees | |
| Speed | number, mm/s (deg/s) | Or `"8000 mm/min"` |
| Location | `{"x", "y", "z", "rotation"}`, any subset | Missing fields = keep current value (for moves) |
| Object name | `"<kind>/<id>"` | `nozzle/N1`, `axis/z`, `camera/bottom`, `feeder/F1`; singletons have no id: `machine`, `job`, `motion` |
| Time | `eventtime`: float seconds (server monotonic clock); timestamps: ISO 8601 UTC strings | |
| Id | string | User-visible ids from the config / database; stable across restarts and OpenPnP import/export |

**Coordinate frames.** Locations of head-mounted objects (nozzles, head cameras) are where *that object* is, after
offsets and axis transforms. `axis/<id>` positions are raw axis values. Machine-mounted cameras have fixed locations.
Every location in the API is in the **machine frame** unless a method says `frame: "board"` (job placements).

## 4. Errors

JSON-RPC errors: `{"code": int, "message": str, "data": {"name": str, ...}}`. `data.name` is the stable identifier
to switch on; `message` is human-readable and may change.

| Code | `data.name` | HTTP | Meaning |
|---|---|---|---|
| -32700 / -32600 / -32601 / -32602 | (JSON-RPC standard) | 400 / 400 / 404 / 400 | Parse error, invalid request, unknown method, invalid params |
| -32001 | `not_found` | 404 | Object, part, file, job, ... doesn't exist (`data.what`, `data.id`) |
| -32002 | `validation_error` | 422 | Config or data rejected (`data.errors`: list of `{path, message}`) |
| -32003 | `conflict` | 409 | Id already exists, stale revision (`data.revision`) |
| -32010 | `not_homed` | 409 | Motion before homing |
| -32011 | `not_enabled` | 409 | Machine is disabled |
| -32012 | `soft_limit` | 409 | Target outside soft limits (`data.axis`, `data.value`, `data.limit`) |
| -32013 | `busy` | 409 | Can't do this while a job runs / a move is in progress |
| -32014 | `stopped` | 409 | Request was aborted by `machine.stop` |
| -32015 | `driver_error` | 502 | Controller reported an error (`data.driver`, `data.response`, e.g. `"error:9"`) |
| -32016 | `driver_timeout` | 504 | Controller didn't answer in time |
| -32017 | `alarm` | 409 | Machine is in alarm (`data.alarm`) |
| -32018 | `vision_failed` | 422 | Vision didn't find the target (`data.result` with diagnostics) |
| -32019 | `feeder_error` | 409 | Feeder couldn't feed (`data.feeder`) |
| -32020 | `unauthorized` | 401 | Missing/invalid credentials |
| -32030 | `restart_required` | 409 | Change accepted but needs reconnect/restart to apply (informational on `config.update`) |
| -32099 | `internal_error` | 500 | Bug; `data.trace_id` points into the server log |

## 5. Server

| Method | Read-only | Params | Result |
|---|---|---|---|
| `server.info` | yes | – | `name`, `version`, `api_version`, `hostname`, `machine_name`, `plugins` (list of `{name, version}`), `state` (`starting`, `ready`, `error`), `warnings` |
| `server.config` | yes | – | Server settings (port, trusted networks, paths). Not the machine config |
| `server.restart` | | – | `{}`; the server restarts, clients reconnect |
| `server.connections` | yes | – | Connected clients (`connection_id`, `client_name`, `type`, `remote`) |
| `server.logs.query` | yes | `level`, `since`, `limit`, `source` | Log entries |
| `server.logs.subscribe` | | `level` | `{}`; then `notify_log` |

## 6. Object model and subscriptions

Every runtime object exposes a **status**: a JSON object of fields. Clients query or subscribe to the fields they
need; the server sends deltas.

| Method | Read-only | Params | Result |
|---|---|---|---|
| `machine.objects.list` | yes | `kind` (optional filter) | `{"objects": ["machine", "motion", "axis/x", ..., "feeder/F1"]}` |
| `machine.objects.query` | yes | `objects`: `{name: [fields] or null}` (`null` = all fields) | `{"eventtime": t, "status": {name: {field: value}}}` |
| `machine.objects.subscribe` | | same as query | Same as query (current values); afterwards `notify_status_update` with changed fields only. A new subscribe replaces the connection's previous one |

Example: subscribe to what a jog panel needs.

```json
→ {"jsonrpc":"2.0","id":3,"method":"machine.objects.subscribe","params":{"objects":{
     "machine":["state","homed","enabled"],
     "nozzle/N1":["location","part"],
     "actuator/pump":["value"]}}}
← {"jsonrpc":"2.0","id":3,"result":{"eventtime":812.4,"status":{
     "machine":{"state":"idle","homed":true,"enabled":true},
     "nozzle/N1":{"location":{"x":1.0,"y":1.0,"z":57.0,"rotation":0.0},"part":null},
     "actuator/pump":{"value":false}}}}
← {"jsonrpc":"2.0","method":"notify_status_update","params":{"eventtime":815.0,"status":{
     "nozzle/N1":{"location":{"x":11.0}}}}}
```

Deltas are merged per field; objects inside fields (like `location`) are merged one level deep. Updates are
rate-limited per connection (default 10 per second, `rate` param on subscribe), so fast moves don't flood clients.

### 6.1 Status fields per kind

| Object | Fields |
|---|---|
| `machine` | `state` (`disconnected`, `connecting`, `alarm`, `idle`, `busy`, `paused`, `error`, `disabled`), `enabled`, `homed`, `alarm` (text or null), `message`, `selected_tool` (object name the UI jogs) |
| `motion` | `moving`, `last_move` (target, duration), `safe_z` (bool: all nozzles in safe zone), `queue_length` |
| `driver/<id>` | `type`, `connected`, `firmware` (detected name/version), `port`, `last_error` |
| `axis/<id>` | `type`, `kind` (`linear`, `rotation`), `position`, `homed`, `soft_limits`, `safe_zone`, `driver`, `letter` |
| `head/<id>` | `location` (head reference point), `nozzles`, `cameras`, `actuators`, `pump` |
| `nozzle/<id>` | `location`, `tip` (nozzle tip id), `part` (part id held, or null), `vacuum` (actuator name), `vacuum_value` (if sensed), `in_safe_zone` |
| `nozzle_tip/<id>` | `loaded_on` (nozzle id or null), `calibrated` |
| `camera/<id>` | `looking` (`up`, `down`), `location`, `streaming`, `fps`, `width`, `height`, `units_per_pixel`, `last_error`, `stream_url`, `snapshot_url` |
| `actuator/<id>` | `type`, `value_type` (`boolean`, `double`, `string`), `value` (last set or read), `readable` |
| `feeder/<id>` | `type`, `enabled`, `part`, `pick_location`, `feeds_left` (if known), `last_error` |
| `job` | `state` (`none`, `loaded`, `running`, `paused`, `stopping`, `finished`, `error`), `job_id`, `name`, `progress` (`done`, `total`), `current_placement`, `elapsed`, `errors` |

Plugins add fields to their own kinds and may add new kinds (e.g. `servo_hat/<id>`); clients ignore what they
don't know.

## 7. Machine control

| Method | Params | Result | Notes |
|---|---|---|---|
| `machine.enable` | – | `{}` when ready | Runs the configured `on_enable` sequence (e.g. driver enable command, homing, park). Errors leave the machine disabled |
| `machine.disable` | – | `{}` | Actuators to their `on_disable` values, driver disable command (e.g. sleep), motors released |
| `machine.home` | `axes` (optional list; default all homeable) | `{}` when done | Then `on_home` (e.g. park) |
| `machine.stop` | `reason` (optional) | `{}` | **Immediate.** Aborts motion and the job, cancels queued requests (they fail with `stopped`), driver stop sequence (e.g. feed hold + soft reset). Machine ends `alarm` or `idle` depending on the driver |
| `machine.run_macro` | `name` | `{}` when done. Runs a macro from `machine.macros` |
| `machine.clear_alarm` | – | `{}` | Driver-specific unlock where the controller allows it |
| `machine.select_tool` | `object` | `{}` | Sets `machine.selected_tool` (shared by all clients) |

## 8. Motion

All motion goes through the core's safety checks: homed, enabled, soft limits, safe Z before X/Y travel (section 3.3
of the design). Methods return when the motion has **finished** (`wait: false` returns as soon as it's queued).

| Method | Params | Result |
|---|---|---|
| `motion.move_to` | `object` (head-mountable or `axis/<id>`), `location` (or `position` for axes), `speed` (fraction 0–1 of max, default 1), `safe_z` (`auto` default, `none` to move directly), `wait` | `{"location": {...}}` final location |
| `motion.jog` | `object`, `delta` (`{x,y,z,rotation}`), `speed`, `safe_z` (`auto`: raise all nozzles before X/Y travel, `none`: stay at this Z, e.g. to centre a nozzle over a camera; default from `machine.jog.safe_z`) | final location |
| `motion.jog_cancel` | – | `{}`. **Immediate** |
| `motion.park` | `head` (default: the only head), `axes` (`xy`, `z`, `all`) | final location |
| `motion.move_to_safe_z` | `head` | `{}` |
| `motion.position` | `object` | `{"location": {...}}` (read-only) |
| `motion.check` | `object`, `location` | `{"ok": bool, "problems": [...]}`: dry-run of the safety checks, no motion (read-only) |

How motion works (implemented in phase 1, step 1):

- Requests that move or switch things run on one motion thread, in order of arrival. On a WebSocket, a connection's
  requests are answered in order; *immediate* methods overtake: `machine.stop` sets a stop flag that every
  driver wait watches, so the running move ends with `stopped`, and so does every request queued before the stop.
- `move_to` with X/Y travel and `safe_z: auto`: first every nozzle on the head into its safe zone (a shared seesaw Z
  moves once), then X, Y and rotation, then the object's own Z. Without X/Y travel everything moves together.
- All targets are checked before anything moves: one target outside its soft limits (or `limit_rotation`) refuses
  the whole move with `soft_limit`.
- Locations are of the object itself: for `nozzle/N2` with offset X 33.2, `x: 100` puts the head at 66.8, and
  `z: 10` on the seesaw puts raw Z at 104.
- `motion.park` moves the **head** to `machine.park.location` (machine coordinates, no camera offset).
- Rotation axes with `limit_rotation` of a full turn or more take angles by whole turns: 200° on [-180, 180] → -160°.
- Controller errors fail the request with `driver_error` (`data.response`, e.g. `"error:9"`); no answer in time
  with `driver_timeout`. An alarm reported by the controller puts the machine in `alarm` until it is homed again.
- `motion.check` also returns the raw axis targets (`axes`), which is handy for checking offsets and axis maps.

## 9. Cameras

| Method | Read-only | Params | Result |
|---|---|---|---|
| `camera.list` | yes | – | `[{id, name, type, looking, head, location, stream_url, snapshot_url, width, height, units_per_pixel}]` |
| `camera.snapshot` | yes | `id`, `overlay` (optional, e.g. `crosshair,scale`) | `{"url": "/cameras/<id>/frames/<n>.jpg", "frame": n, "width", "height", "eventtime"}`: a frame taken after the call; the last 10 are kept. Settling (waiting after a move) comes with vision |
| `camera.controls` | yes | `id` | `v4l2` cameras: `[{name, type, min, max, step, default, value}]` from `v4l2-ctl --list-ctrls` (or the OpenCV values without it). The camera must be open (being watched) |
| `camera.set_controls` | | `id`, `controls` (`{name: value}`) | `{"problems": [...]}`. Applied now, **not saved**: put lasting values under the camera's `controls` in the configuration |
| `camera.reopen` | | `id` | `{}`: closes and reopens the device |
| `camera.devices` | yes | – | Video devices on the server: `[{path, name, index, capture, usb_id, usb_serial, manufacturer, product, links}]`, for the `device:` selector |

Camera status fields: `looking`, `location`, `streaming`, `viewers`, `fps` (measured), `width`, `height`,
`units_per_pixel`, `last_error`; `v4l2` adds `device`, `matched_by`, `requested` and `actual` (format).

Streams: `GET /cameras/<id>/stream.mjpg` (multipart MJPEG; `?fps=` to reduce, `?overlay=`, `?frames=N` to stop after
N frames), `GET /cameras/<id>/snapshot.jpg`, `GET /cameras/<id>/frames/<n>.jpg`. Overlays (`crosshair`, `scale`,
`grid`, `info`) are drawn server-side so every front end gets them. Details: [`cameras.md`](cameras.md). WebRTC is
planned; `camera.list` will then add a `webrtc` entry.

## 10. Actuators and feeders

| Method | Params | Result |
|---|---|---|
| `actuator.set` | `id`, `value` (bool / number / string per `value_type`) | `{"value": ...}` |
| `actuator.read` | `id` | `{"value": ...}` (for readable actuators, e.g. vacuum sensors) |
| `feeder.feed` | `id` | `{"pick_location": {...}}` |
| `feeder.pick_location` | `id` | `{"pick_location": {...}}` (read-only) |
| `feeder.move_to_pick` | `id`, `object` (nozzle or camera) | final location |
| `feeder.setup` | `id`, `step`, `input` | Guided setup step (e.g. capture reference hole); returns the next step. Each feeder type defines its steps |

Vacuum is normally switched by pick/place, not by clients; `actuator.set` is for manual control and testing.

## 11. Configuration

The machine configuration (YAML, see [`config.md`](config.md)) is read and changed through these methods; the server
writes the file. Every successful change bumps `revision`.

| Method | Read-only | Params | Result |
|---|---|---|---|
| `config.types` | yes | `kind` (optional) | `[{kind, type, title, description, schema (JSON Schema), plugin}]` for every object type, incl. plugin types |
| `config.get` | yes | `path` (optional, e.g. `axes.z`) | `{"revision": n, "config": {...}}` |
| `config.objects` | yes | `kind` | `[{name, type, settings}]` |
| `config.update` | | `revision` (expected), `changes`: list of `{op: set/delete/add, path, value}`, `dry_run` | `{"revision": n+1, "applied": bool, "restart_required": [paths], "warnings": [...]}` or `validation_error` / `conflict` |
| `config.history` | yes | `limit` | `[{revision, time, client, summary}]` |
| `config.undo` | | `revision` (to go back to) | like `config.update` |
| `config.capture` | | `field` (the config path the value is meant for, e.g. `cameras.bottom.location`), `from` (helper name), `args` | `{"field", "value", "from", "object"}`: computes a value from the machine as it is now; nothing is saved. The client shows it and saves it with `config.update`. Helpers so far: `location` (`args: {object}`): where a nozzle, head camera or axis really is, read back from the controller, as `{x, y, z}`. Used for a bottom camera's location: centre a nozzle over it, in focus, and capture |
| `config.capture_helpers` | yes | – | Available capture helpers with descriptions |

`revision` gives optimistic locking: two front ends editing at once get a `conflict` instead of overwriting each
other. Notification `notify_config_changed` tells every client which paths changed.

How `config.update` works (implemented in phase 0):

- `path` is dotted, list positions are numbers (`machine.on_enable.0`). `set` replaces or creates a value, `delete`
  removes it, `add` creates a new key (`conflict` if it exists), appends to a list, or inserts at a list position.
- Values may use units like the file does (`"6000 mm/min"`).
- All changes in one call are applied together and the **whole** configuration is validated, references included,
  before anything is written. `validation_error` lists every problem as `data.errors: [{path, message}]`; an object
  that fails its own validation is not reported again as a missing reference.
- The file is edited in YAML round-trip mode (comments, key order and formatting of unchanged parts are kept) and
  replaced atomically. The machine is then rebuilt from the new configuration.
- While the machine is enabled, only **calibration values** can change: camera `location` / `offset` /
  `units_per_pixel` / `transform`, nozzle `offset`, `machine.park`, `machine.jog`, `machine.discard_location`, feeder
  `pick_location` and `part`, and any `name` / `notes`. They are applied to the running machine without rebuilding
  it, so it stays enabled and homed (`notify_config_changed` then has `live: true`). Anything else is refused with
  `busy` (`data.paths` lists what needs a disabled machine); `dry_run` always works. `config.undo` follows the same
  rule for the changes it takes back.
- Objects that come from `include`d files can't be edited yet (`validation_error`).
- Revisions and history are kept in memory: they start at 1 when the server starts. `config.undo` restores the file
  byte for byte as it was at that revision and records a new revision.

## 12. Data: parts, packages, boards, jobs

Collections: `parts`, `packages`, `boards`, `panels`, `jobs`. Uniform methods:

| Method | Read-only | Params | Result |
|---|---|---|---|
| `data.<collection>.list` | yes | `filter`, `sort`, `limit`, `offset` | `{"items": [...], "total": n}` |
| `data.<collection>.get` | yes | `id` | item |
| `data.<collection>.create` | | `item` | item (with `revision`) |
| `data.<collection>.update` | | `id`, `revision`, `changes` (partial item) | item |
| `data.<collection>.delete` | | `id`, `revision` | `{}` |

Item shapes follow OpenPnP's model (field names in snake_case), so import/export is a direct mapping:

- **part:** `id`, `name`, `package`, `height`, `speed`, `pick_retry_count`, `x_openpnp` (unknown imported fields)
- **package:** `id`, `description`, `footprint` (`units`, `body_width`, `body_height`, `pads`: `[{name, x, y, width,
  height, rotation, roundness}]`), `compatible_nozzle_tips`, `vision` (bottom vision settings or pipeline id)
- **board:** `id`, `name`, `dimensions`, `placements`: `[{id, part, side, location, type (placement/fiducial),
  enabled, comments}]`
- **job:** `id`, `name`, `boards`: `[{board, location (machine frame), side, enabled, check_fiducials}]`, `panels`

Notification: `notify_data_changed` with collection, id, revision.

## 13. Files

| Method / endpoint | Params | Result |
|---|---|---|
| `files.list` | `root` (`imports`, `exports`, `images`, `logs`), `path` | entries with `name`, `size`, `modified` |
| `POST /files/upload` (multipart) | `root`, `path`, `file` | `{"item": {...}}` |
| `GET /files/<root>/<path>` | | the file |
| `files.delete` | `root`, `path` | `{}` |

## 14. Import and export

| Method | Params | Result |
|---|---|---|
| `import.formats` | – | Supported formats (`openpnp`, `kicad_pos`, ...) with what they contain (read-only) |
| `import.openpnp` | `files` (paths under `imports/`), `include` (`parts`, `packages`, `boards`, `panels`, `jobs`, `machine`, `vision`), `dry_run`, `on_conflict` (`skip`, `replace`, `rename`) | Import report: per item `created` / `updated` / `skipped`, plus `unsupported` (classes and attributes kept under `x_openpnp`) |
| `import.kicad_pos` | `file`, `board_id`, `side`, `units` | Board with placements, plus parts/packages it created |
| `export.openpnp` | `include`, `ids` (optional) | `{"files": [paths under exports/]}` |

Imports are jobs that can take a while: they return when done (or with `wait: false` immediately, reporting through
`notify_task_progress`).

## 15. Jobs

| Method | Params | Result |
|---|---|---|
| `job.load` | `job_id` | `{}`; `job.state` becomes `loaded` |
| `job.start` | `from_placement` (optional), `dry_run` (move without picking) | `{}` immediately; progress via `job` status and `notify_job_event` |
| `job.pause` | – | **Immediate.** Finishes the current motion step, then pauses |
| `job.resume` | – | `{}` |
| `job.step` | – | Runs one placement, then pauses |
| `job.stop` | – | `{}`; stops after the current step (use `machine.stop` to abort immediately) |
| `job.plan` | – | Planned order of placements per nozzle/feeder (read-only) |
| `job.set_enabled` | `board`, `placement`, `enabled` | `{}` |

`notify_job_event`: `{"type": "placement_done" | "pick_failed" | "vision_failed" | "feeder_empty" | "paused" |
"finished" | "error", "placement": ..., "message": ...}`.

## 16. Vision

| Method | Params | Result |
|---|---|---|
| `vision.pipelines` | – | `[{id, name, kind (bottom, fiducial, nozzle_tip, feeder), stages}]` (read-only) |
| `vision.run` | `pipeline`, `camera`, `context` (e.g. `{part, nozzle}`), `debug` | `{"found": bool, "result": {...}, "images": [urls of stage outputs if debug]}` |
| `vision.calibrate_nozzle_tip` | `nozzle`, `camera` | Calibration result (runout model) |
| `vision.locate_fiducials` | `job_id`, `board` | Board location in the machine frame, with residuals |

## 17. G-code console and raw driver access

| Method | Params | Result |
|---|---|---|
| `gcode.send` | `command` (one or more lines, `\n`-separated), `driver` (optional if there is only one G-code driver), `timeout` (ms, optional; default: the driver's `timeout`) | `{"responses": ["ok", ...], "ok": bool}`. A controller error ends the command early with `ok: false` and the error as the last response; it is not an API error. Bypasses the motion safety checks: the UI should warn. Refused while a job runs (`busy`) |
| `gcode.subscribe` | `driver` (optional; default all) | `{}`; then `notify_gcode` with every line sent and received, for console views (WebSocket only) |
| `gcode.unsubscribe` | – | `{}` |
| `gcode.history` | `driver` (optional), `limit` (1–1000, default 200) | The last lines sent and received, oldest first: `[{driver, direction, line, eventtime}]` (read-only) |

Notes:

- Works on a disabled machine: the port is opened on demand (e.g. to read grblHAL's `$$`, all settings, before
  enabling).
- Escapes like `\x18` (Ctrl-X) and `\u0085` are decoded, so control bytes can be typed. Commands in the driver's
  `realtime` list (grbl: `?`, `!`, `~`) are sent as bytes without waiting; their answers arrive as `notify_gcode`.
- After the command the driver reads the position back (if it has `GET_POSITION_COMMAND`), so raw moves don't
  leave TinkerPnP with a wrong position. Other state changes made behind its back (e.g. `$SLP`, sleep) aren't
  tracked.
- Requests go through the same motion thread as moves, so a console command waits until a running move has
  finished.

## 18. Notifications (summary)

| Notification | Params |
|---|---|
| `notify_status_update` | `status` (delta per subscribed object), `eventtime` |
| `notify_machine_state` | `state`, `previous`, `message` (also reflected in `machine` status; sent to all clients, subscribed or not) |
| `notify_alarm` | `driver`, `alarm`, `message` |
| `notify_job_event` | see section 15 |
| `notify_task_progress` | `task_id`, `kind` (import, calibration, ...), `progress` (0–1), `message` |
| `notify_config_changed` | `revision`, `paths`, `client` |
| `notify_data_changed` | `collection`, `id`, `revision`, `change` (`created`, `updated`, `deleted`) |
| `notify_log` | `level`, `source`, `message`, `time` |
| `notify_gcode` | `driver`, `direction` (`tx`, `rx`), `line`, `eventtime` |
| `notify_server_shutdown` | `reason`; the server is about to restart or stop |

## 19. Open questions

1. **Port 7300:** any convention to follow? (Moonraker uses 7125.)
2. **Move completion semantics:** `motion.*` returns at the end of motion; long sequences from scripts may prefer
   `wait: false` plus status polling. Is a `motion.queue` batch call worth adding?
3. **Multiple clients jogging at once:** currently both are executed in arrival order. A "control lock"
   (`machine.acquire_control`) could prevent surprises on shared machines.
4. **Config change granularity:** JSON-patch-like `changes` list (proposed) vs. replacing whole objects.
5. **Image transport for vision debug:** URLs to frames kept for N minutes (proposed) vs. inline base64.
6. **Units in responses:** always mm/deg (proposed); clients convert for display.
