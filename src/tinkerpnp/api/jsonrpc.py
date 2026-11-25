"""Minimal JSON-RPC 2.0 dispatcher with TinkerPnP's error model (docs/api.md, section 4)."""
from __future__ import annotations

import inspect
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..core.errors import MachineError

log = logging.getLogger("tinkerpnp.api")

# name -> (code, http status)
ERRORS = {
    "parse_error": (-32700, 400),
    "invalid_request": (-32600, 400),
    "method_not_found": (-32601, 404),
    "invalid_params": (-32602, 400),
    "not_found": (-32001, 404),
    "validation_error": (-32002, 422),
    "conflict": (-32003, 409),
    "not_homed": (-32010, 409),
    "not_enabled": (-32011, 409),
    "soft_limit": (-32012, 409),
    "busy": (-32013, 409),
    "stopped": (-32014, 409),
    "driver_error": (-32015, 502),
    "driver_timeout": (-32016, 504),
    "alarm": (-32017, 409),
    "vision_failed": (-32018, 422),
    "feeder_error": (-32019, 409),
    "unauthorized": (-32020, 401),
    "below_resolution": (-32021, 422),
    "restart_required": (-32030, 409),
    "internal_error": (-32099, 500),
}


class RpcError(Exception):
    def __init__(self, name: str, message: str, **data: Any):
        super().__init__(message)
        self.name = name
        self.message = message
        self.data = {"name": name, **data}

    @property
    def code(self) -> int:
        return ERRORS[self.name][0]

    @property
    def http_status(self) -> int:
        return ERRORS[self.name][1]

    def as_error(self) -> dict:
        return {"code": self.code, "message": self.message, "data": self.data}


@dataclass
class Method:
    name: str
    fn: Callable[..., Awaitable[Any] | Any]
    read_only: bool = False
    immediate: bool = False
    websocket_only: bool = False
    description: str = ""


@dataclass
class Dispatcher:
    methods: dict[str, Method] = field(default_factory=dict)

    def method(self, name: str, read_only: bool = False, immediate: bool = False, websocket_only: bool = False):
        def deco(fn):
            self.methods[name] = Method(name, fn, read_only, immediate, websocket_only,
                                        (inspect.getdoc(fn) or "").split("\n")[0])
            return fn
        return deco

    async def call(self, name: str, params: Any, ctx: Any) -> Any:
        method = self.methods.get(name)
        if method is None:
            raise RpcError("method_not_found", f"unknown method {name!r}")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise RpcError("invalid_params", "params must be an object")
        sig = inspect.signature(method.fn)
        accepted = {p for p in sig.parameters if p != "ctx"}
        has_var_kw = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
        unknown = set(params) - accepted
        if unknown and not has_var_kw:
            raise RpcError("invalid_params", f"unknown parameter(s): {', '.join(sorted(unknown))}")
        missing = [p.name for p in sig.parameters.values()
                   if p.name != "ctx" and p.default is p.empty and p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL)
                   and p.name not in params]
        if missing:
            raise RpcError("invalid_params", f"missing parameter(s): {', '.join(missing)}")
        try:
            result = method.fn(ctx=ctx, **params)
            if inspect.isawaitable(result):
                result = await result
        except MachineError as exc:
            raise RpcError(exc.name, exc.message, **exc.data) from None
        return result

    async def handle(self, message: Any, ctx: Any) -> dict | list | None:
        """Handle one decoded JSON-RPC message (object or batch). Returns the response, or None for notifications."""
        if isinstance(message, list):
            if not message:
                return _error(None, RpcError("invalid_request", "empty batch"))
            responses = [r for r in [await self.handle(m, ctx) for m in message] if r is not None]
            return responses or None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            return _error(message.get("id") if isinstance(message, dict) else None,
                          RpcError("invalid_request", "not a JSON-RPC 2.0 request"))
        req_id = message.get("id")
        try:
            result = await self.call(message["method"], message.get("params"), ctx)
        except RpcError as err:
            return _error(req_id, err) if "id" in message else None
        except Exception as exc:  # bug: log with a trace id, return a generic error
            trace = uuid.uuid4().hex[:12]
            log.exception("internal error in %s (trace %s)", message.get("method"), trace)
            return _error(req_id, RpcError("internal_error", f"{type(exc).__name__}: {exc}", trace_id=trace))
        if "id" not in message:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, err: RpcError) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": err.as_error()}
