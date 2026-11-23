"""FastAPI application: WebSocket JSON-RPC, HTTP mirror, status subscriptions (docs/api.md)."""
from __future__ import annotations

import asyncio
import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .. import __version__
from ..core.machine import Machine
from .jsonrpc import RpcError
from .methods import rpc


@dataclass
class AppState:
    machine: Machine
    connections: dict[int, "Connection"] = field(default_factory=dict)
    config_revision: int = 1
    started: float = field(default_factory=time.monotonic)
    _ids: Any = field(default_factory=lambda: itertools.count(1))

    def eventtime(self) -> float:
        return round(time.monotonic() - self.started, 3)


class Connection:
    """One WebSocket client (or a one-off HTTP request, without websocket)."""

    def __init__(self, app_state: AppState, websocket: WebSocket | None = None, remote: str = ""):
        self.app_state = app_state
        self.websocket = websocket
        self.id = next(app_state._ids) if websocket is not None else 0
        self.remote = remote
        self.client: dict | None = None
        self._subscription: dict[str, list[str] | None] = {}
        self._pending: dict[str, dict] = {}
        self._interval = 0.1
        self._flush_task: asyncio.Task | None = None
        self._outbox: asyncio.Queue | None = asyncio.Queue() if websocket is not None else None
        self._loop = asyncio.get_running_loop() if websocket is not None else None

    # subscriptions
    def subscribe(self, objects: dict[str, list[str] | None], rate: float) -> None:
        if self.websocket is None:
            raise RpcError("invalid_request", "subscriptions need the WebSocket")
        self._subscription = dict(objects)
        self._pending = {}
        self._interval = 1.0 / rate

    def on_status(self, name: str, delta: dict) -> None:
        """Called by the machine for every status change; may come from any thread."""
        if self._loop is None or name not in self._subscription:
            return
        self._loop.call_soon_threadsafe(self._collect, name, delta)

    def _collect(self, name: str, delta: dict) -> None:
        if name not in self._subscription:
            return
        fields = self._subscription[name]
        wanted = delta if fields is None else {k: v for k, v in delta.items() if k in fields}
        if not wanted:
            return
        self._pending.setdefault(name, {}).update(wanted)
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        await asyncio.sleep(self._interval)
        if self._pending:
            status, self._pending = self._pending, {}
            self.send({"jsonrpc": "2.0", "method": "notify_status_update",
                       "params": {"status": status, "eventtime": self.app_state.eventtime()}})

    def send(self, message: dict) -> None:
        if self._outbox is not None:
            self._outbox.put_nowait(message)

    async def writer(self) -> None:
        assert self.websocket is not None and self._outbox is not None
        while True:
            msg = await self._outbox.get()
            await self.websocket.send_text(json.dumps(msg))


def create_app(machine: Machine) -> FastAPI:
    app = FastAPI(title="TinkerPnP API", version=__version__,
                  description="HTTP mirror of the JSON-RPC API; see docs/api.md. Subscriptions need the WebSocket.",
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    state = AppState(machine=machine)
    app.state.tinker = state

    def broadcast(name: str, delta: dict) -> None:
        for conn in list(state.connections.values()):
            conn.on_status(name, delta)

    machine.add_listener(broadcast)

    @app.websocket("/websocket")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        conn = Connection(state, ws, remote=f"{ws.client.host}:{ws.client.port}" if ws.client else "")
        state.connections[conn.id] = conn
        writer = asyncio.create_task(conn.writer())
        try:
            while True:
                text = await ws.receive_text()
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    conn.send({"jsonrpc": "2.0", "id": None,
                               "error": RpcError("parse_error", "invalid JSON").as_error()})
                    continue
                response = await rpc.handle(message, conn)
                if response is not None:
                    conn.send(response)
        except WebSocketDisconnect:
            pass
        finally:
            writer.cancel()
            state.connections.pop(conn.id, None)

    async def http_call(method_path: str, request: Request, params: dict) -> JSONResponse:
        name = method_path.strip("/").replace("/", ".")
        method = rpc.methods.get(name)
        try:
            if method is None:
                raise RpcError("method_not_found", f"unknown method {name!r}")
            if method.websocket_only:
                raise RpcError("invalid_request", f"{name} is only available over the WebSocket")
            if request.method == "GET" and not method.read_only:
                raise RpcError("invalid_request", f"{name} changes state; use POST")
            conn = Connection(state, None, remote=request.client.host if request.client else "")
            result = await rpc.call(name, params, conn)
            return JSONResponse({"result": result})
        except RpcError as err:
            return JSONResponse({"error": err.as_error()}, status_code=err.http_status)

    @app.post("/api/{method_path:path}")
    async def http_post(method_path: str, request: Request) -> JSONResponse:
        body = await request.body()
        try:
            params = json.loads(body) if body else {}
        except json.JSONDecodeError:
            err = RpcError("parse_error", "invalid JSON body")
            return JSONResponse({"error": err.as_error()}, status_code=err.http_status)
        return await http_call(method_path, request, params)

    @app.get("/api/{method_path:path}")
    async def http_get(method_path: str, request: Request) -> JSONResponse:
        params: dict[str, Any] = {}
        for k, v in request.query_params.items():
            try:
                params[k] = json.loads(v)       # ?objects={"machine":null}
            except json.JSONDecodeError:
                params[k] = v
        return await http_call(method_path, request, params)

    return app
