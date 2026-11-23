"""API methods: server.*, machine.*, motion.*, actuator.*, config.*. See docs/api.md."""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING, Any

from .. import API_VERSION, __version__
from ..core.config_edit import ConfigEditError
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
    machine = ctx.app_state.machine

    def clear() -> None:
        st = machine.state.status()
        if st["state"] == "alarm":
            machine.state.update(state="idle" if st["enabled"] else "disabled", alarm=None, message=None)
    await _run(ctx, clear)
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
async def motion_jog(ctx: "Connection", object: str, delta: dict, speed: float = 1.0) -> dict:
    """Move relative to the current location; applies machine.jog.safe_z."""
    planner = ctx.app_state.machine.planner
    obj = planner.resolve(object)
    return {"location": await _run(ctx, planner.jog, obj, delta, speed)}


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


def _apply_result(ctx: "Connection", result) -> dict:
    """Rebuild the machine from the new configuration and tell every client."""
    app = ctx.app_state
    if result.applied:
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
    if not dry_run and ctx.app_state.machine.state.status().get("enabled"):
        raise RpcError("busy", "disable the machine before changing its configuration")
    try:
        result = store.update(changes, revision, dry_run=dry_run, client=_client_name(ctx))
    except ConfigEditError as exc:
        raise _edit_error(exc) from None
    return _apply_result(ctx, result)


@rpc.method("config.history", read_only=True)
def config_history(ctx: "Connection", limit: int = 20) -> list:
    """Recent configuration changes, newest first."""
    return _store(ctx).history_list(limit)


@rpc.method("config.undo")
def config_undo(ctx: "Connection", revision: int) -> dict:
    """Return the configuration to how it was at `revision` (creates a new revision)."""
    store = _store(ctx)
    if ctx.app_state.machine.state.status().get("enabled"):
        raise RpcError("busy", "disable the machine before changing its configuration")
    try:
        result = store.undo(revision, client=_client_name(ctx))
    except ConfigEditError as exc:
        raise _edit_error(exc) from None
    return _apply_result(ctx, result)
