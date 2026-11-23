# TinkerPnP

An open, API-first pick-and-place host. It runs on a Raspberry Pi (or a PC) next to the machine and is used from any
browser. Started 2026-11-23.

Status: **phase 0 (skeleton)**: configuration, object model, simulated machine, the read side of the API,
configuration editing (`config.update`, history, undo) and a first web UI work. Phase 1 (machine console) has
started: enable/home/disable/stop, moves and jogs with safe Z and soft limits, actuators, and the `gcode` driver
(OpenPnP-style commands, grblHAL profile, built-in grblHAL simulator: `config/examples/simulated-grblhal.yaml`),
a G-code console (API and web UI), and cameras (simulated and USB/V4L2, MJPEG streams with overlays:
[`docs/cameras.md`](docs/cameras.md)).
Drivers: [`docs/drivers.md`](docs/drivers.md).
See [`docs/design.md`](docs/design.md), the API specification
[`docs/api.md`](docs/api.md), the configuration format [`docs/config.md`](docs/config.md), and where TinkerPnP
behaves differently from OpenPnP: [`docs/openpnp-differences.md`](docs/openpnp-differences.md).

Aims:

- **As configurable as OpenPnP, or more:** a general machine model (drivers, axes incl. mapped/cam/transformed axes,
  heads, nozzles, nozzle tips, cameras, actuators, feeders, vision pipelines), with plugin types in Python.
- **Compatible with OpenPnP's files:** import and export parts, packages, boards, panels and jobs, and import a
  machine configuration.
- **API-first, like Klipper + Moonraker:** a documented JSON-RPC/WebSocket + HTTP API; the bundled UI (TinkerUI,
  Svelte) is one front end, and others can be built on the same API, the way Fluidd and Mainsail sit on Moonraker.
- **Modern Python 3:** Pi add-on boards (I2C servo drivers, I/O HATs), OpenCV and numpy usable directly.

First target machine: the MC5S3DP-XHAT pick-and-place machine in `../` (grblHAL, two nozzles on a shared seesaw Z,
USB cameras, servo feeders on an I2C servo HAT).

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                                                   # tests
.venv/bin/tinkerpnp --config config/examples/simulated.yaml --check # validate a machine config
.venv/bin/tinkerpnp --config config/examples/simulated.yaml        # API on http://127.0.0.1:7300
```

- `GET /api/server/info`, `GET /api/docs` (OpenAPI), WebSocket JSON-RPC on `/websocket`.
- The server also serves the bundled web UI from `ui/build/` (or `--ui DIR`) at `/`.

### Web UI (TinkerUI)

Svelte 5 + SvelteKit 3 (static single-page app), TypeScript, Tailwind CSS 4, shadcn-svelte components (bits-ui),
lucide icons. It's a client of the public API like any other front end.

```sh
cd ui
npm install
npm run dev      # http://localhost:5173, forwards /api, /websocket, /cameras to the server on :7300
npm run check    # type check
npm run build    # static files in ui/build, served by `tinkerpnp`
```

Pages so far: Machine (state, nozzles, axes, actuators, cameras, feeders, all live), Objects (every object's
live status), Configuration (all object types with their schema fields; the machine configuration).
- `config/examples/simulated.yaml`: the first machine without hardware.
- `docs/examples/mc5s3dp-xhat.yaml`: the real machine; validates except for the servo feeders, whose plugin
  (`tinkerpnp-servo-hat`) doesn't exist yet.

Layout: `src/tinkerpnp/core/` (units, type registry, config loader, object types, machine runtime),
`src/tinkerpnp/api/` (JSON-RPC dispatcher, methods, FastAPI server), `tests/`.
