"""Editing the machine configuration file: validated changes, revisions, history and undo (docs/api.md, config.*).

Changes are applied to the YAML document with ruamel's round-trip mode, so comments, key order and formatting of
everything that wasn't changed stay as they were. Every change is validated against the whole configuration before
anything is written; the file is replaced atomically.
"""
from __future__ import annotations

import copy
import io
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

from ruamel.yaml import YAML

from .config import ConfigProblem, MachineConfig, _plain, build_config
from .registry import Registry

OPS = ("set", "delete", "add")
_INDEX = re.compile(r"^\d+$")

# Calibration values that can change while the machine is enabled: they're applied to the running machine without
# rebuilding it (Machine.apply_live), so homing and positions are kept. Everything else needs a disabled machine.
LIVE_PATHS = [re.compile(p) for p in (
    r"^cameras\.[^.]+\.(location|units_per_pixel|transform|controls|warmup|stream_fps|jpeg_quality|name|notes)(\..*)?$",
    r"^heads\.[^.]+\.cameras\.[^.]+\.(offset|units_per_pixel|transform|controls|warmup|stream_fps|jpeg_quality|name|notes)"
    r"(\..*)?$",
    r"^heads\.[^.]+\.nozzles\.[^.]+\.(offset|name|notes)(\..*)?$",
    r"^machine\.(park|jog|discard_location|macros)(\..*)?$",
    r"^feeders\.[^.]+\.(pick_location|name|notes|part)(\..*)?$",
    r"^(axes|actuators|drivers|nozzle_tips|heads)\.[^.]+\.(name|notes)$",
    r"^name$",
)]


def is_live(path: str) -> bool:
    return any(p.match(path) for p in LIVE_PATHS)


class ConfigEditError(Exception):
    """name is an API error name (validation_error, conflict, not_found, invalid_params)."""

    def __init__(self, name: str, message: str, **data: Any):
        super().__init__(message)
        self.name = name
        self.message = message
        self.data = data


@dataclass
class HistoryEntry:
    revision: int
    time: float
    client: str
    summary: str
    changes: list[dict]
    text_before: str            # file content before this revision, for undo


@dataclass
class EditResult:
    revision: int
    applied: bool
    config: MachineConfig
    paths: list[str]
    restart_required: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 120
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def split_path(path: str) -> list[str | int]:
    if not isinstance(path, str) or not path or path.startswith(".") or path.endswith(".") or ".." in path:
        raise ConfigEditError("invalid_params", f"invalid path {path!r}")
    return [int(p) if _INDEX.match(p) else p for p in path.split(".")]


def _walk(root: Any, parts: list, create: bool) -> Any:
    node = root
    for i, part in enumerate(parts):
        here = ".".join(str(p) for p in parts[: i + 1])
        if isinstance(node, dict):
            if part not in node:
                if not create:
                    raise ConfigEditError("not_found", f"no config path {here!r}", what="config_path", id=here)
                node[part] = {}
            node = node[part]
        elif isinstance(node, list):
            if not isinstance(part, int) or part >= len(node):
                raise ConfigEditError("not_found", f"no config path {here!r}", what="config_path", id=here)
            node = node[part]
        else:
            raise ConfigEditError("invalid_params", f"{here!r} is not a mapping or list")
    return node


def apply_change(root: Any, change: dict) -> str:
    """Apply one {op, path, value} change to a dict/list tree (plain or ruamel). Returns the path."""
    if not isinstance(change, dict):
        raise ConfigEditError("invalid_params", "each change must be an object {op, path, value}")
    op, path = change.get("op"), change.get("path")
    if op not in OPS:
        raise ConfigEditError("invalid_params", f"op must be one of {', '.join(OPS)}, got {op!r}")
    parts = split_path(path)
    parent = _walk(root, parts[:-1], create=(op != "delete"))
    last = parts[-1]
    if op in ("set", "add") and "value" not in change:
        raise ConfigEditError("invalid_params", f"{op} {path}: missing value")
    value = copy.deepcopy(change.get("value"))

    if isinstance(parent, dict):
        if op == "delete":
            if last not in parent:
                raise ConfigEditError("not_found", f"no config path {path!r}", what="config_path", id=path)
            del parent[last]
        elif op == "add":
            target = parent.get(last)
            if isinstance(target, list):
                target.append(value)
            elif last in parent:
                raise ConfigEditError("conflict", f"{path!r} already exists (use set to replace it)")
            else:
                parent[last] = value
        else:
            parent[last] = value
    elif isinstance(parent, list):
        if not isinstance(last, int):
            raise ConfigEditError("invalid_params", f"{path!r}: list positions are numbers")
        if op == "delete":
            if last >= len(parent):
                raise ConfigEditError("not_found", f"no config path {path!r}", what="config_path", id=path)
            del parent[last]
        elif op == "add":
            if last > len(parent):
                raise ConfigEditError("invalid_params", f"{path!r}: position past the end of the list")
            parent.insert(last, value)
        else:
            if last >= len(parent):
                raise ConfigEditError("not_found", f"no config path {path!r}", what="config_path", id=path)
            parent[last] = value
    else:
        raise ConfigEditError("invalid_params", f"{path!r}: parent is not a mapping or list")
    return path


class ConfigStore:
    def __init__(self, path: str, config: MachineConfig, registry: Registry | None = None):
        self.path = path
        self.registry = registry
        with open(path, encoding="utf-8") as fh:
            self.text = fh.read()
        self.config = config
        self.revision = 1
        self.history: list[HistoryEntry] = []

    # ── reading ────────────────────────────────────────────────────────────────────────────────────────────────
    def _doc(self, text: str | None = None) -> Any:
        return _yaml().load(text if text is not None else self.text) or {}

    def _dump(self, doc: Any) -> str:
        buf = io.StringIO()
        _yaml().dump(doc, buf)
        return buf.getvalue()

    # ── editing ────────────────────────────────────────────────────────────────────────────────────────────────
    def update(self, changes: list[dict], revision: int | None, dry_run: bool = False, client: str = "") -> EditResult:
        if revision is not None and revision != self.revision:
            raise ConfigEditError("conflict", f"configuration is at revision {self.revision}, not {revision}",
                                  revision=self.revision)
        if not isinstance(changes, list) or not changes:
            raise ConfigEditError("invalid_params", "changes must be a non-empty list")

        paths = []
        for ch in changes:
            p = ch.get("path") if isinstance(ch, dict) else None
            if isinstance(p, str):
                key = ".".join(p.split(".")[:2])
                if key in self.config.included:
                    raise ConfigEditError("validation_error",
                                          f"{key} is defined in an included file ({self.config.included[key]}); "
                                          "editing included files isn't supported yet",
                                          errors=[{"path": p, "message": "defined in an included file"}])

        # validate on plain data first
        doc = self._doc()
        plain = _plain(doc)
        for ch in changes:
            paths.append(apply_change(plain, ch))
        candidate = build_config(plain, self.path, self.registry, strict=False)
        if candidate.problems:
            raise ConfigEditError("validation_error", f"{len(candidate.problems)} problem(s) in the changed configuration",
                                  errors=[p.as_dict() for p in candidate.problems])
        if dry_run:
            return EditResult(self.revision, False, candidate, paths)

        # apply the same changes to the round-trip document, so comments and formatting survive
        for ch in changes:
            apply_change(doc, ch)
        new_text = self._dump(doc)
        summary = "; ".join(f"{c['op']} {c['path']}" for c in changes)
        return self._commit(new_text, candidate, changes, summary, client, paths)

    def undo(self, revision: int, client: str = "") -> EditResult:
        """Go back to the configuration as it was at `revision`."""
        if revision == self.revision:
            raise ConfigEditError("invalid_params", f"already at revision {revision}")
        entry = next((h for h in self.history if h.revision == revision + 1), None)
        if entry is None:
            raise ConfigEditError("not_found", f"no history for revision {revision}", what="revision", id=revision)
        text = entry.text_before
        candidate = build_config(_plain(self._doc(text)), self.path, self.registry, strict=False)
        if candidate.problems:
            raise ConfigEditError("validation_error", "the old revision no longer validates",
                                  errors=[p.as_dict() for p in candidate.problems])
        return self._commit(text, candidate, [], f"undo to revision {revision}", client, ["*"])

    def _commit(self, new_text: str, candidate: MachineConfig, changes: list[dict], summary: str, client: str,
                paths: list[str]) -> EditResult:
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(prefix=".tinkerpnp-", suffix=".yaml", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            if os.path.exists(self.path):
                os.chmod(tmp, os.stat(self.path).st_mode & 0o777)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        self.revision += 1
        self.history.append(HistoryEntry(self.revision, time.time(), client, summary, changes, self.text))
        self.text = new_text
        self.config = candidate
        return EditResult(self.revision, True, candidate, paths)

    def changes_since(self, revision: int) -> list[dict]:
        """Every change made after `revision` (what an undo to it would take back)."""
        return [c for h in self.history if h.revision > revision for c in h.changes]

    def history_list(self, limit: int = 20) -> list[dict]:
        out = [{"revision": h.revision, "time": h.time, "client": h.client, "summary": h.summary}
               for h in reversed(self.history)]
        return out[:limit]
