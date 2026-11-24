"""Parts, packages, boards and jobs (docs/api.md, section 12), kept in SQLite: one validated JSON document per item.

Shapes follow OpenPnP's model with snake_case names, so OpenPnP's parts.xml, packages.xml, *.board.xml and *.job.xml
map onto them directly. Lengths are mm, angles degrees; values may carry units like the configuration ("0.5 mm").
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .errors import MachineError
from .units import Length


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., min_length=1, max_length=200)
    notes: str | None = None
    x_openpnp: dict[str, Any] | None = Field(None, description="Imported OpenPnP fields TinkerPnP doesn't use (kept)")


# ── packages ───────────────────────────────────────────────────────────────────────────────────────────────────────

class Pad(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    x: Length = 0.0
    y: Length = 0.0
    width: Length = 0.0
    height: Length = 0.0
    rotation: float = 0.0
    roundness: float = Field(0.0, ge=0, le=100, description="Corner rounding in percent (OpenPnP's roundness)")


class Footprint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body_width: Length = 0.0
    body_height: Length = 0.0
    pads: list[Pad] = Field(default_factory=list)


class Package(Item):
    description: str | None = None
    footprint: Footprint = Field(default_factory=Footprint)
    compatible_nozzle_tips: list[str] = Field(default_factory=list)
    vision: dict[str, Any] = Field(default_factory=dict, description="Bottom vision settings (step E2)")


# ── parts ──────────────────────────────────────────────────────────────────────────────────────────────────────────

class Part(Item):
    name: str | None = None
    package: str | None = Field(None, description="Package id")
    height: Length = Field(0.0, description="Part height (thickness): the nozzle places at board + height")
    speed: float = Field(1.0, gt=0, le=1, description="Speed factor for moves while the part is on the nozzle")
    pick_retry_count: int = Field(0, ge=0, le=10)


# ── boards ─────────────────────────────────────────────────────────────────────────────────────────────────────────

class XYR(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: Length = 0.0
    y: Length = 0.0
    z: Length = 0.0
    rotation: float = 0.0


class Placement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., min_length=1, description="Reference designator: R1, C2, U3, FID1")
    part: str | None = Field(None, description="Part id (fiducials may have none)")
    side: Literal["top", "bottom"] = "top"
    location: XYR = Field(default_factory=XYR, description="On the board, from its origin (usually lower left)")
    type: Literal["placement", "fiducial"] = "placement"
    enabled: bool = True
    comments: str | None = None


class Dimensions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: Length = 0.0
    y: Length = 0.0


class Board(Item):
    name: str | None = None
    dimensions: Dimensions = Field(default_factory=Dimensions)
    placements: list[Placement] = Field(default_factory=list)

    @field_validator("placements")
    @classmethod
    def _unique(cls, v: list[Placement]) -> list[Placement]:
        seen = set()
        for p in v:
            if p.id in seen:
                raise ValueError(f"placement id {p.id!r} is used twice")
            seen.add(p.id)
        return v


# ── jobs ───────────────────────────────────────────────────────────────────────────────────────────────────────────

class JobBoard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    board: str = Field(..., description="Board id")
    location: XYR = Field(default_factory=XYR, description="Where the board's origin is on the machine (z: its "
                                                            "top surface), and its rotation")
    side: Literal["top", "bottom"] = "top"
    enabled: bool = True
    check_fiducials: bool = False
    placed: list[str] = Field(default_factory=list, description="Placement ids already placed on this board")


class Job(Item):
    name: str | None = None
    boards: list[JobBoard] = Field(default_factory=list)


MODELS: dict[str, type[Item]] = {"parts": Part, "packages": Package, "boards": Board, "jobs": Job}


def _problems(exc: ValidationError) -> list[dict]:
    return [{"path": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]


def validate(collection: str, item: Any) -> dict:
    """Validate an item; returns it as plain JSON data (units converted)."""
    model = MODELS.get(collection)
    if model is None:
        raise MachineError("not_found", f"no collection {collection!r}", what="collection", id=collection)
    try:
        return model.model_validate(item).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise MachineError("validation_error", f"{collection}: {len(exc.errors())} problem(s)",
                           errors=_problems(exc)) from None


class DataStore:
    """Items by (collection, id) with a revision each. Thread-safe."""

    def __init__(self, path: str):
        self.path = path
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("""CREATE TABLE IF NOT EXISTS items (
                collection TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL, updated REAL NOT NULL,
                data TEXT NOT NULL, PRIMARY KEY (collection, id))""")
            self._db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, data TEXT NOT NULL)")
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _out(revision: int, updated: float, data: str) -> dict:
        item = json.loads(data)
        item["revision"] = revision
        item["updated"] = updated
        return item

    def get(self, collection: str, item_id: str) -> dict:
        validate_collection(collection)
        with self._lock:
            row = self._db.execute("SELECT revision, updated, data FROM items WHERE collection=? AND id=?",
                                   (collection, item_id)).fetchone()
        if row is None:
            raise MachineError("not_found", f"no {collection[:-1]} {item_id!r}", what=collection[:-1], id=item_id)
        return self._out(*row)

    def find(self, collection: str, item_id: str) -> dict | None:
        try:
            return self.get(collection, item_id)
        except MachineError:
            return None

    def list(self, collection: str, filter: str | None = None, sort: str = "id", limit: int = 500,
             offset: int = 0) -> dict:
        validate_collection(collection)
        if sort not in ("id", "updated", "-updated"):
            raise MachineError("invalid_params", "sort: id, updated or -updated")
        order = {"id": "id", "updated": "updated", "-updated": "updated DESC"}[sort]
        where, args = "collection=?", [collection]
        if filter:
            where += " AND (id LIKE ? OR data LIKE ?)"
            args += [f"%{filter}%", f"%{filter}%"]
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM items WHERE {where}", args).fetchone()[0]
            rows = self._db.execute(f"SELECT revision, updated, data FROM items WHERE {where} ORDER BY {order} "
                                    "LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return {"items": [self._out(*r) for r in rows], "total": total}

    def create(self, collection: str, item: Any, replace: bool = False) -> dict:
        data = validate(collection, _strip_meta(item))
        now = time.time()
        with self._lock:
            row = self._db.execute("SELECT revision FROM items WHERE collection=? AND id=?",
                                   (collection, data["id"])).fetchone()
            if row is not None and not replace:
                raise MachineError("conflict", f"{collection[:-1]} {data['id']!r} exists", revision=row[0])
            revision = (row[0] + 1) if row else 1
            self._db.execute("INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?, ?)",
                             (collection, data["id"], revision, now, json.dumps(data)))
            self._db.commit()
        return {**data, "revision": revision, "updated": now}

    def update(self, collection: str, item_id: str, changes: dict, revision: int | None = None) -> dict:
        if not isinstance(changes, dict) or not changes:
            raise MachineError("invalid_params", "changes must be a non-empty object")
        if "id" in changes and changes["id"] != item_id:
            raise MachineError("invalid_params", "the id can't be changed (create a copy instead)")
        current = self.get(collection, item_id)
        if revision is not None and revision != current["revision"]:
            raise MachineError("conflict", f"{collection[:-1]} {item_id!r} is at revision {current['revision']}",
                               revision=current["revision"])
        merged = {**_strip_meta(current), **_strip_meta(changes)}
        data = validate(collection, merged)
        now = time.time()
        with self._lock:
            cur = self._db.execute("UPDATE items SET revision=revision+1, updated=?, data=? WHERE collection=? "
                                   "AND id=? AND revision=?",
                                   (now, json.dumps(data), collection, item_id, current["revision"]))
            self._db.commit()
            if cur.rowcount != 1:
                raise MachineError("conflict", f"{collection[:-1]} {item_id!r} changed meanwhile")
        return {**data, "revision": current["revision"] + 1, "updated": now}

    def delete(self, collection: str, item_id: str, revision: int | None = None) -> None:
        current = self.get(collection, item_id)
        if revision is not None and revision != current["revision"]:
            raise MachineError("conflict", f"{collection[:-1]} {item_id!r} is at revision {current['revision']}",
                               revision=current["revision"])
        with self._lock:
            self._db.execute("DELETE FROM items WHERE collection=? AND id=?", (collection, item_id))
            self._db.commit()

    # small machine state that changes often and doesn't belong in the configuration file (feeder counters)
    def state_get(self, key: str) -> Any:
        with self._lock:
            row = self._db.execute("SELECT data FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def state_set(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value)))
            self._db.commit()

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT collection, COUNT(*) FROM items GROUP BY collection").fetchall()
        return {c: 0 for c in MODELS} | dict(rows)


def validate_collection(collection: str) -> None:
    if collection not in MODELS:
        raise MachineError("not_found", f"no collection {collection!r}", what="collection", id=collection)


def _strip_meta(item: Any) -> Any:
    if isinstance(item, dict):
        return {k: v for k, v in item.items() if k not in ("revision", "updated")}
    return item
