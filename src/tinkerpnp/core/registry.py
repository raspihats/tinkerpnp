"""Type registry: every configurable object type (built-in or plugin) registers here.

A type is identified by (kind, type), e.g. ("axis", "mapped") or ("feeder", "servo"). It has a pydantic settings
model (whose JSON Schema the API serves, so front ends can render forms for any type) and a runtime class.

Plugins are Python packages that declare an entry point in the group "tinkerpnp.plugins"; the entry point is a
callable taking the registry:

    [project.entry-points."tinkerpnp.plugins"]
    servo_hat = "tinkerpnp_servo_hat:register"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib import metadata
from typing import Callable

from pydantic import BaseModel

KINDS = ("driver", "axis", "head", "nozzle", "nozzle_tip", "actuator", "camera", "feeder")

ENTRY_POINT_GROUP = "tinkerpnp.plugins"


@dataclass
class TypeInfo:
    kind: str
    type: str
    model: type[BaseModel]
    runtime: type
    title: str = ""
    description: str = ""
    plugin: str | None = None          # distribution name for plugin types, None for built-ins

    def schema(self) -> dict:
        return self.model.model_json_schema()


@dataclass
class Registry:
    types: dict[tuple[str, str], TypeInfo] = field(default_factory=dict)
    plugins: list[dict] = field(default_factory=list)
    plugin_errors: list[dict] = field(default_factory=list)
    _current_plugin: str | None = None

    def register(self, kind: str, type_name: str, model: type[BaseModel], title: str = "",
                 description: str = "") -> Callable[[type], type]:
        """Class decorator: @registry.register("axis", "mapped", MappedAxisSettings, "Mapped axis")."""
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")

        def deco(cls: type) -> type:
            key = (kind, type_name)
            if key in self.types:
                raise ValueError(f"type {kind}/{type_name} registered twice")
            doc = (cls.__doc__ or "").strip()
            self.types[key] = TypeInfo(kind, type_name, model, cls, title or type_name,
                                       description or doc, self._current_plugin)
            return cls

        return deco

    def get(self, kind: str, type_name: str) -> TypeInfo | None:
        return self.types.get((kind, type_name))

    def of_kind(self, kind: str) -> list[TypeInfo]:
        return [t for (k, _), t in sorted(self.types.items()) if k == kind]

    def load_plugins(self) -> None:
        for ep in metadata.entry_points(group=ENTRY_POINT_GROUP):
            dist = ep.dist.name if ep.dist else ep.name
            self._current_plugin = dist
            try:
                ep.load()(self)
                version = ep.dist.version if ep.dist else ""
                self.plugins.append({"name": dist, "version": version, "entry_point": ep.name})
            except Exception as exc:  # a broken plugin must not take the server down
                self.plugin_errors.append({"name": dist, "error": f"{type(exc).__name__}: {exc}"})
            finally:
                self._current_plugin = None

    def installed_plugin_names(self) -> set[str]:
        return {p["name"] for p in self.plugins}


registry = Registry()
