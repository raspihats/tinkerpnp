"""Phase 0 API methods: server.*, machine.objects.*, config.* (read side). See docs/api.md."""
from __future__ import annotations

import socket
from typing import TYPE_CHECKING, Any

from .. import API_VERSION, __version__
from ..core.registry import KINDS
from .jsonrpc import Dispatcher, RpcError

if TYPE_CHECKING:
    from .server import Connection

rpc = Dispatcher()


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
