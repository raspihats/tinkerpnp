# TinkerPnP

An open, API-first pick-and-place host. It runs on a Raspberry Pi (or a PC) next to the machine and is used from any
browser. Started 2026-11-23.

Status: **design phase.** No code yet. See [`docs/design.md`](docs/design.md), the API specification
[`docs/api.md`](docs/api.md) and the configuration format [`docs/config.md`](docs/config.md).

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
