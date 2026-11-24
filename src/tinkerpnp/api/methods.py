"""API methods: server.*, machine.*, motion.*, actuator.*, config.*. See docs/api.md."""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING, Any

from .. import API_VERSION, __version__
from ..core.config_edit import ConfigEditError, is_live
from ..core.machine import Machine
from ..core.registry import KINDS
from .jsonrpc import Dispatcher, RpcError

if TYPE_CHECKING:
    from .server import Connection

rpc = Dispatcher()
log = logging.getLogger("tinkerpnp.api")


# ── server.* ───────────────────────────────────────────────────────────────────────────────────────────────────────

@rpc.method("server.info", read_only=True)
def server_info(ctx: "Connection") -> dict:
    """Server, API and machine information."""
    app = ctx.app_state
    reg = app.machine.registry
    return {
        "name": "TinkerPnP",
        "version": __version__,
        "api_version": API_VERSION,
        "hostname": socket.gethostname(),
        "machine_name": app.machine.config.name,
        "plugins": reg.plugins,
        "state": "ready",
        "warnings": [f"plugin {e['name']} failed to load: {e['error']}" for e in reg.plugin_errors]
        + [f"{p.path}: {p.message}" for p in app.machine.config.problems],
    }


@rpc.method("server.connection.identify", websocket_only=True)
def identify(ctx: "Connection", client_name: str, version: str = "", type: str = "web", url: str = "",
             api_key: str | None = None) -> dict:
    """Identify this WebSocket client."""
    if type not in ("web", "mobile", "script", "service"):
        raise RpcError("invalid_params", "type must be web, mobile, script or service")
    ctx.client = {"client_name": client_name, "version": version, "type": type, "url": url}
    return {"connection_id": ctx.id}


@rpc.method("server.connections", read_only=True)
def connections(ctx: "Connection") -> list:
    """Connected WebSocket clients."""
    return [{"connection_id": c.id, "remote": c.remote, **(c.client or {})} for c in ctx.app_state.connections.values()]


# ── machine.objects.* ──────────────────────────────────────────────────────────────────────────────────────────────

def _check_objects(ctx: "Connection", objects: Any) -> dict[str, list[str] | None]:
    if not isinstance(objects, dict):
        raise RpcError("invalid_params", "objects must be a mapping of object name -> list of fields or null")
    for name, fields in objects.items():
        if name not in ctx.app_state.machine.objects:
            raise RpcError("not_found", f"no object {name!r}", what="object", id=name)
        if fields is not None and (not isinstance(fields, list) or not all(isinstance(f, str) for f in fields)):
            raise RpcError("invalid_params", f"fields for {name!r} must be a list of strings or null")
    return objects


@rpc.method("machine.objects.list", read_only=True)
def objects_list(ctx: "Connection", kind: str | None = None) -> dict:
    """Names of all runtime objects, optionally of one kind."""
    if kind is not None and kind not in KINDS + ("machine", "motion", "job"):
        raise RpcError("invalid_params", f"unknown kind {kind!r}")
    return {"objects": ctx.app_state.machine.names(kind)}


@rpc.method("machine.objects.query", read_only=True)
def objects_query(ctx: "Connection", objects: dict) -> dict:
    """Current status fields of the given objects."""
    req = _check_objects(ctx, objects)
    return {"eventtime": ctx.app_state.eventtime(), "status": ctx.app_state.machine.query(req)}


@rpc.method("machine.objects.subscribe", websocket_only=True)
def objects_subscribe(ctx: "Connection", objects: dict, rate: float = 10.0) -> dict:
    """Current status, then notify_status_update deltas for the subscribed fields."""
    req = _check_objects(ctx, objects)
    if not 0.5 <= rate <= 60:
        raise RpcError("invalid_params", "rate must be between 0.5 and 60 updates per second")
    ctx.subscribe(req, rate)
    return {"eventtime": ctx.app_state.eventtime(), "status": ctx.app_state.machine.query(req)}


# ── machine.* / motion.* / actuator.* ──────────────────────────────────────────────────────────────────────────
# Everything that moves runs on the machine's motion thread, one request after another.

async def _run(ctx: "Connection", fn, *args, wait: bool = True, **kwargs):
    machine = ctx.app_state.machine
    future = asyncio.wrap_future(machine.submit(fn, *args, **kwargs))
    if wait:
        return await future

    def done(f: asyncio.Future) -> None:
        if not f.cancelled() and f.exception() is not None:
            log.warning("background request failed: %s", f.exception())
    future.add_done_callback(done)
    return None


def _head(ctx: "Connection", head: str | None):
    machine = ctx.app_state.machine
    heads = machine.of_kind("head")
    if head is None:
        if len(heads) != 1:
            raise RpcError("invalid_params", "this machine has several heads; give head")
        return heads[0]
    obj = machine.objects.get(f"head/{head}")
    if obj is None:
        raise RpcError("not_found", f"no head {head!r}", what="head", id=head)
    return obj


@rpc.method("machine.enable")
async def machine_enable(ctx: "Connection") -> dict:
    """Enable the drivers, set actuators to their on_enable values, run machine.on_enable (e.g. home)."""
    await _run(ctx, ctx.app_state.machine.enable)
    return {}


@rpc.method("machine.disable")
async def machine_disable(ctx: "Connection") -> dict:
    """Actuators to their on_disable values, drivers disabled (motors released); the machine must be homed again."""
    await _run(ctx, ctx.app_state.machine.disable)
    return {}


@rpc.method("machine.home")
async def machine_home(ctx: "Connection", axes: list | None = None) -> dict:
    """Home all (or the given) controller axes, then run machine.on_home (e.g. park)."""
    await _run(ctx, ctx.app_state.machine.home, axes)
    return {}


@rpc.method("machine.stop", immediate=True)
def machine_stop(ctx: "Connection", reason: str | None = None) -> dict:
    """Abort motion now; queued requests fail with `stopped`."""
    ctx.app_state.machine.stop(reason)
    return {}


@rpc.method("machine.clear_alarm")
async def machine_clear_alarm(ctx: "Connection") -> dict:
    """Leave the alarm state where the controller allows it."""
    await _run(ctx, ctx.app_state.machine.clear_alarm)
    return {}


@rpc.method("machine.run_macro")
async def machine_run_macro(ctx: "Connection", name: str) -> dict:
    """Run a macro from machine.macros (a named list of steps)."""
    await _run(ctx, ctx.app_state.machine.run_macro, name)
    return {}


@rpc.method("machine.select_tool")
def machine_select_tool(ctx: "Connection", object: str) -> dict:
    """Set machine.selected_tool (shared by all clients)."""
    machine = ctx.app_state.machine
    ctx.app_state.machine.planner.resolve(object)
    machine.state.update(selected_tool=object)
    return {}


@rpc.method("motion.move_to")
async def motion_move_to(ctx: "Connection", object: str, location: dict | None = None,
                         position: float | None = None, speed: float = 1.0, safe_z: str = "auto",
                         wait: bool = True) -> dict:
    """Move a nozzle, head camera or axis/<id> to a location (machine frame), with safe Z before X/Y travel."""
    planner = ctx.app_state.machine.planner
    obj = planner.resolve(object)
    if object.startswith("axis/"):
        if position is None:
            raise RpcError("invalid_params", "axes take position")
        location = {"position": position}
    elif location is None:
        raise RpcError("invalid_params", "missing location")
    result = await _run(ctx, planner.move_to, obj, location, speed, safe_z, wait=wait)
    return {"location": result} if wait else {}


@rpc.method("motion.jog")
async def motion_jog(ctx: "Connection", object: str, delta: dict, speed: float = 1.0,
                     safe_z: str | None = None) -> dict:
    """Move relative to the current location. safe_z: auto / none; default machine.jog.safe_z."""
    if safe_z not in (None, "auto", "none"):
        raise RpcError("invalid_params", "safe_z must be auto or none")
    planner = ctx.app_state.machine.planner
    obj = planner.resolve(object)
    return {"location": await _run(ctx, planner.jog, obj, delta, speed, safe_z)}


@rpc.method("motion.jog_cancel", immediate=True)
def motion_jog_cancel(ctx: "Connection") -> dict:
    """Abort the current jog."""
    ctx.app_state.machine.cancel_jog()
    return {}


@rpc.method("motion.park")
async def motion_park(ctx: "Connection", head: str | None = None, axes: str = "all") -> dict:
    """Safe Z, then the head's camera to machine.park.location (axes: xy, z or all)."""
    h = _head(ctx, head)
    return {"location": await _run(ctx, ctx.app_state.machine.planner.park, h, axes)}


@rpc.method("motion.move_to_safe_z")
async def motion_safe_z(ctx: "Connection", head: str | None = None) -> dict:
    """Bring every nozzle on the head into its safe zone."""
    h = _head(ctx, head)
    await _run(ctx, ctx.app_state.machine.planner.move_to_safe_z, h)
    return {}


@rpc.method("motion.position", read_only=True)
def motion_position(ctx: "Connection", object: str) -> dict:
    """Current location of a nozzle, head camera or axis."""
    planner = ctx.app_state.machine.planner
    return {"location": planner.location(planner.resolve(object))}


@rpc.method("motion.check", read_only=True)
def motion_check(ctx: "Connection", object: str, location: dict) -> dict:
    """Dry run of the safety checks for a move; nothing moves."""
    machine = ctx.app_state.machine
    planner = machine.planner
    targets = planner.targets(planner.resolve(object), location)
    problems = planner.problems(targets)
    st = machine.state.status()
    if not st["enabled"]:
        problems.insert(0, {"name": "not_enabled", "message": "the machine is disabled"})
    elif not st["homed"]:
        problems.insert(0, {"name": "not_homed", "message": "home the machine first"})
    return {"ok": not problems, "problems": problems,
            "axes": {a.id: round(v, 4) for a, v in targets.items()}}


@rpc.method("actuator.set")
async def actuator_set(ctx: "Connection", id: str, value: Any) -> dict:
    """Switch an actuator (bool / number / string per its value_type). For manual control and tests."""
    return {"value": await _run(ctx, ctx.app_state.machine.actuate, id, value)}


@rpc.method("actuator.read")
async def actuator_read(ctx: "Connection", id: str) -> dict:
    """Read a readable actuator (e.g. a vacuum sensor)."""
    machine = ctx.app_state.machine
    act = machine.objects.get(f"actuator/{id}")
    if act is None:
        raise RpcError("not_found", f"no actuator {id!r}", what="actuator", id=id)
    return {"value": await _run(ctx, act.read)}


# ── camera.* ───────────────────────────────────────────────────────────────────────────────────────────────────

def _camera(ctx: "Connection", id: str):
    cam = ctx.app_state.machine.objects.get(f"camera/{id}")
    if cam is None:
        raise RpcError("not_found", f"no camera {id!r}", what="camera", id=id)
    return cam


@rpc.method("camera.list", read_only=True)
def camera_list(ctx: "Connection") -> list:
    """Cameras with their stream and snapshot URLs."""
    out = []
    for cam in ctx.app_state.machine.of_kind("camera"):
        st = cam.status()
        out.append({"id": cam.id, "name": cam.settings.name or cam.id, "type": cam.type, "looking": st["looking"],
                    "head": cam.head.id if cam.head is not None else None, "location": st["location"],
                    "stream_url": st["stream_url"], "snapshot_url": st["snapshot_url"],
                    "width": st.get("width"), "height": st.get("height"),
                    "units_per_pixel": st.get("units_per_pixel")})
    return out


@rpc.method("camera.snapshot", read_only=True)
async def camera_snapshot(ctx: "Connection", id: str, overlay: str | None = None) -> dict:
    """Take a frame now; returns its URL (/cameras/<id>/frames/<n>.jpg, kept for the last 10 frames)."""
    cam = _camera(ctx, id)
    seq, frame = await asyncio.to_thread(cam.snapshot)
    h, w = frame.shape[:2]
    url = f"/cameras/{id}/frames/{seq}.jpg" + (f"?overlay={overlay}" if overlay else "")
    return {"url": url, "frame": seq, "width": w, "height": h, "eventtime": ctx.app_state.eventtime()}


@rpc.method("camera.devices", read_only=True)
def camera_devices(ctx: "Connection") -> list:
    """Video devices on the server, with USB serial, vendor:product, name and stable paths, to pick in setup."""
    from ..core.cameras import list_devices
    return list_devices()


@rpc.method("camera.controls", read_only=True)
async def camera_controls(ctx: "Connection", id: str) -> list:
    """Device controls (exposure, focus, white balance, ...) with ranges and values. The camera must be open."""
    cam = _camera(ctx, id)
    if not hasattr(cam, "list_controls"):
        raise RpcError("invalid_params", f"camera {id!r} ({cam.type}) has no device controls")
    return await asyncio.to_thread(cam.list_controls)


@rpc.method("camera.set_controls")
async def camera_set_controls(ctx: "Connection", id: str, controls: dict) -> dict:
    """Set device controls now (not saved; put them under the camera's `controls` in the config to keep them)."""
    cam = _camera(ctx, id)
    if not hasattr(cam, "apply_controls"):
        raise RpcError("invalid_params", f"camera {id!r} ({cam.type}) has no device controls")
    if not isinstance(controls, dict) or not controls:
        raise RpcError("invalid_params", "controls must be a non-empty object {name: value}")
    problems = await asyncio.to_thread(cam.apply_controls, controls)
    return {"problems": problems}


@rpc.method("camera.reopen")
async def camera_reopen(ctx: "Connection", id: str) -> dict:
    """Close and reopen the camera device."""
    cam = _camera(ctx, id)
    await asyncio.to_thread(cam.reopen)
    return {}


# ── gcode.* (console) ──────────────────────────────────────────────────────────────────────────────────────────

def _gcode_driver(ctx: "Connection", driver: str | None):
    machine = ctx.app_state.machine
    drivers = [d for d in machine.of_kind("driver") if hasattr(d, "send")]
    if driver is None:
        if len(drivers) != 1:
            raise RpcError("invalid_params", "give driver: " + (", ".join(d.id for d in drivers) or
                                                               "this machine has no G-code driver"))
        return drivers[0]
    obj = machine.objects.get(f"driver/{driver}")
    if obj is None:
        raise RpcError("not_found", f"no driver {driver!r}", what="driver", id=driver)
    if not hasattr(obj, "send"):
        raise RpcError("invalid_params", f"driver {driver!r} ({obj.type}) doesn't take G-code")
    return obj


@rpc.method("gcode.send")
async def gcode_send(ctx: "Connection", command: str, driver: str | None = None,
                     timeout: float | None = None) -> dict:
    """Send lines as typed (console). Bypasses the motion safety checks. Escapes like \\x18 (Ctrl-X) are decoded."""
    from ..core.errors import MachineError
    from ..core.gcode import _unescape
    drv = _gcode_driver(ctx, driver)
    if not isinstance(command, str) or not command.strip("\r\n"):
        raise RpcError("invalid_params", "command must be a non-empty string")
    if timeout is not None and not (isinstance(timeout, (int, float)) and 0 < timeout <= 600000):
        raise RpcError("invalid_params", "timeout is in ms, 1..600000")
    if ctx.app_state.machine.job.status().get("state") in ("running", "paused"):
        raise RpcError("busy", "a job is running")
    lines = [ln.rstrip("\r") for ln in _unescape(command).split("\n")]

    def work() -> dict:
        if drv.transport is None:
            drv.connect()                 # the console works on a disabled machine too (e.g. to read $$)
        responses: list[str] = []
        ok = True
        for line in lines:
            if not line.strip():
                continue
            try:
                responses += drv.send(line, timeout)
            except MachineError as exc:
                if exc.name != "driver_error" or "response" not in exc.data:
                    raise
                responses.append(exc.data["response"])
                ok = False
                break
        try:
            drv.read_position()           # raw moves bypass TinkerPnP: re-read where the axes are
        except MachineError:
            pass
        return {"responses": responses, "ok": ok}

    return await _run(ctx, work)


@rpc.method("gcode.subscribe", websocket_only=True)
def gcode_subscribe(ctx: "Connection", driver: str | None = None) -> dict:
    """notify_gcode for every line sent (tx) and received (rx), of one driver or all."""
    if driver is not None:
        _gcode_driver(ctx, driver)
    ctx.gcode_drivers = {driver} if driver else set()
    return {}


@rpc.method("gcode.unsubscribe", websocket_only=True)
def gcode_unsubscribe(ctx: "Connection") -> dict:
    """Stop notify_gcode."""
    ctx.gcode_drivers = None
    return {}


@rpc.method("gcode.history", read_only=True)
def gcode_history(ctx: "Connection", driver: str | None = None, limit: int = 200) -> list:
    """The last lines sent and received (up to 1000 are kept), oldest first."""
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise RpcError("invalid_params", "limit must be 1..1000")
    entries = [e for e in ctx.app_state.gcode_log if driver is None or e["driver"] == driver]
    return entries[-limit:]


# ── config.* (read side) ───────────────────────────────────────────────────────────────────────────────────────────

@rpc.method("config.types", read_only=True)
def config_types(ctx: "Connection", kind: str | None = None) -> list:
    """All configurable object types with their JSON Schemas, incl. plugin types."""
    reg = ctx.app_state.machine.registry
    out = []
    for (k, t), info in sorted(reg.types.items()):
        if kind is None or k == kind:
            out.append({"kind": k, "type": t, "title": info.title, "description": info.description,
                        "plugin": info.plugin, "schema": info.schema()})
    return out


@rpc.method("config.get", read_only=True)
def config_get(ctx: "Connection", path: str | None = None) -> dict:
    """The machine configuration (plain data), optionally a sub-tree by dotted path."""
    data: Any = ctx.app_state.machine.config.data
    if path:
        for part in path.split("."):
            if not isinstance(data, dict) or part not in data:
                raise RpcError("not_found", f"no config path {path!r}", what="config_path", id=path)
            data = data[part]
    return {"revision": ctx.app_state.config_revision, "config": data}


@rpc.method("config.objects", read_only=True)
def config_objects(ctx: "Connection", kind: str) -> list:
    """Configured objects of one kind with their validated settings."""
    if kind not in KINDS:
        raise RpcError("invalid_params", f"unknown kind {kind!r}")
    return [{"name": f"{o.kind}/{o.id}", "type": o.type, "head": o.head,
             "settings": o.settings.model_dump(mode="json")}
            for o in ctx.app_state.machine.config.of_kind(kind)]


# ── config.* (write side) ──────────────────────────────────────────────────────────────────────────────────────────

def _store(ctx: "Connection"):
    store = ctx.app_state.store
    if store is None:
        raise RpcError("invalid_request", "this server has no writable configuration file")
    return store


def _client_name(ctx: "Connection") -> str:
    if ctx.client:
        return ctx.client.get("client_name", "")
    return f"http {ctx.remote}" if ctx.remote else "http"


def _check_enabled(ctx: "Connection", paths: list[str]) -> bool:
    """True if the machine is enabled (changes then go in live). Refuses changes that need a disabled machine."""
    if not ctx.app_state.machine.state.status().get("enabled"):
        return False
    not_live = [p for p in paths if not is_live(p)]
    if not_live:
        raise RpcError("busy", "disable the machine before changing " + ", ".join(not_live[:5])
                       + " (only calibration values can change while it is enabled)", paths=not_live)
    return True


def _apply_result(ctx: "Connection", result, live: bool = False) -> dict:
    """Rebuild the machine from the new configuration (or apply calibration values live) and tell every client."""
    app = ctx.app_state
    if result.applied and live:
        app.machine.apply_live(result.config)
        app.notify_all("notify_config_changed", {"revision": result.revision, "paths": result.paths,
                                                 "client": _client_name(ctx), "live": True})
    elif result.applied:
        app.replace_machine(Machine(result.config, app.machine.registry))
        app.notify_all("notify_config_changed", {"revision": result.revision, "paths": result.paths,
                                                 "client": _client_name(ctx)})
    return {"revision": result.revision, "applied": result.applied,
            "restart_required": result.restart_required, "warnings": result.warnings}


def _edit_error(exc: ConfigEditError) -> RpcError:
    return RpcError(exc.name, exc.message, **exc.data)


@rpc.method("config.update")
def config_update(ctx: "Connection", changes: list, revision: int | None = None, dry_run: bool = False) -> dict:
    """Validate and apply changes ({op: set/delete/add, path, value}) to the machine configuration."""
    store = _store(ctx)
    paths = [c.get("path", "") if isinstance(c, dict) else "" for c in (changes if isinstance(changes, list) else [])]
    live = False if dry_run else _check_enabled(ctx, [str(p) for p in paths])
    try:
        result = store.update(changes, revision, dry_run=dry_run, client=_client_name(ctx))
    except ConfigEditError as exc:
        raise _edit_error(exc) from None
    return _apply_result(ctx, result, live)


@rpc.method("config.history", read_only=True)
def config_history(ctx: "Connection", limit: int = 20) -> list:
    """Recent configuration changes, newest first."""
    return _store(ctx).history_list(limit)


@rpc.method("config.undo")
def config_undo(ctx: "Connection", revision: int) -> dict:
    """Return the configuration to how it was at `revision` (creates a new revision)."""
    store = _store(ctx)
    undone = store.changes_since(revision) if isinstance(revision, int) else []
    live = _check_enabled(ctx, [c.get("path", "") for c in undone] or ["*"])
    try:
        result = store.undo(revision, client=_client_name(ctx))
    except ConfigEditError as exc:
        raise _edit_error(exc) from None
    return _apply_result(ctx, result, live)


@rpc.method("config.capture_helpers", read_only=True)
def config_capture_helpers(ctx: "Connection") -> list:
    """Helpers for config.capture."""
    return [{"name": "location", "args": {"object": "nozzle/<id>, camera/<id> or axis/<id>"},
             "description": "Where the object really is now (the position is read back from the controller): "
                            "e.g. a bottom camera's location = the nozzle centred over it, in focus"},
            {"name": "units_per_pixel", "args": {"camera": "camera id (up-looking, machine-mounted)",
                                                 "object": "nozzle/<id>, in focus over the camera",
                                                 "distance": "mm to move in X and Y (default 1)"},
             "description": "Moves the nozzle by `distance` in X and in Y at the same height and measures how far "
                            "the image moved (template matching). Returns mm per pixel, plus whether the image is "
                            "mirrored or rotated. The nozzle ends where it started"}]


@rpc.method("config.capture")
async def config_capture(ctx: "Connection", field: str, args: dict, **kw: Any) -> dict:
    """Compute a configuration value from the machine as it is now (see config.capture_helpers). Nothing is saved:
    the client shows the value and saves it with config.update."""
    helper = kw.pop("from", "location")
    if kw:
        raise RpcError("invalid_params", f"unknown parameter(s): {', '.join(kw)}")
    if helper not in ("location", "units_per_pixel"):
        raise RpcError("invalid_params", f"unknown helper {helper!r}; see config.capture_helpers")
    machine = ctx.app_state.machine
    obj = machine.planner.resolve(str((args or {}).get("object", "")))
    if helper == "units_per_pixel":
        from ..core.calibration import measure_units_per_pixel
        from ..core.objects import Nozzle
        if not isinstance(obj, Nozzle):
            raise RpcError("invalid_params", "object must be a nozzle")
        camera = machine.objects.get(f"camera/{(args or {}).get('camera')}")
        if camera is None:
            raise RpcError("not_found", f"no camera {args.get('camera')!r}", what="camera", id=args.get("camera"))
        machine.planner.require_ready()
        distance = float(args.get("distance", 1.0))
        r = await _run(ctx, measure_units_per_pixel, machine, camera, obj, distance)
        return {"field": field, "value": r["value"], "details": r["details"], "from": helper, "object": obj.name}

    def read() -> dict:
        for driver in machine.drivers():       # the true position, not the last target
            read_position = getattr(driver, "read_position", None)
            if read_position is not None and driver.status().get("connected"):
                read_position()
        return machine.planner.location(obj)

    loc = await _run(ctx, read)
    value = {k: round(v, 4) for k, v in loc.items() if k in ("x", "y", "z") and v is not None}
    return {"field": field, "value": value, "from": helper, "object": obj.name}
