"""Load and validate a machine configuration file (format: docs/config.md).

All problems are collected and reported together, each with a path (e.g. "axes.z_n2.input"), instead of stopping at
the first one.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML

from . import cameras, feeders, gcode, objects  # noqa: F401  (registers the built-in types)
from .objects import Location, Settings
from .registry import Registry, registry as default_registry

FORMAT = "tinkerpnp-machine/1"

# section name -> object kind
SECTIONS = {
    "drivers": "driver",
    "axes": "axis",
    "heads": "head",
    "nozzle_tips": "nozzle_tip",
    "actuators": "actuator",
    "cameras": "camera",
    "feeders": "feeder",
}
DEFAULT_TYPES = {"head": "head", "nozzle": "nozzle", "nozzle_tip": "nozzle_tip"}
OTHER_KEYS = {"format", "name", "include", "machine", "plugins", "vision", "signalers"}


class Park(BaseModel):
    model_config = ConfigDict(extra="forbid")
    head: str
    location: Location


class Jog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    safe_z: Literal["always", "never"] = Field(
        "never", description="never (as OpenPnP): jogs move straight there, at the current Z. always: every X/Y jog "
                             "first raises all nozzles on the head to safe Z")
    distances: list[float] = Field(default_factory=lambda: [0.01, 0.1, 1, 10, 100])


class MachineSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on_enable: list[Any] = Field(default_factory=list)
    on_home: list[Any] = Field(default_factory=list)
    on_before_home: list[Any] = Field(default_factory=list, description="Steps before homing, run only when the "
                                                                        "machine is homed (position known), e.g. "
                                                                        "[safe_z]")
    park: Park | None = None
    jog: Jog = Field(default_factory=Jog)
    discard_location: Location | None = None
    unsafe_z_roaming_distance: float = Field(
        10.0, ge=0, description="mm. As OpenPnP's Unsafe Z Roaming: a tool with a virtual Z (a head camera) that is "
                                "jogged below its safe Z farther than this from where it went below goes back to "
                                "safe Z")
    macros: dict[str, list[Any]] = Field(default_factory=dict,
                                         description="Named step lists run on request (machine.run_macro), like "
                                                     "Klipper macros: the steps of on_enable plus move_to and gcode")


@dataclass
class ConfigProblem:
    path: str
    message: str

    def as_dict(self) -> dict:
        return {"path": self.path, "message": self.message}


@dataclass
class ObjectConfig:
    kind: str
    id: str
    type: str
    settings: Settings
    head: str | None = None            # for nozzles and head-mounted cameras
    path: str = ""


@dataclass
class MachineConfig:
    path: str | None
    name: str = ""
    machine: MachineSection = field(default_factory=MachineSection)
    objects: list[ObjectConfig] = field(default_factory=list)
    vision: dict = field(default_factory=dict)
    plugins: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)          # plain-dict copy of the merged file content
    included: dict[str, str] = field(default_factory=dict)   # "section.id" -> file, for objects from includes
    problems: list[ConfigProblem] = field(default_factory=list)
    invalid: set[tuple[str, str]] = field(default_factory=set)   # (kind, id) of objects that failed validation

    @property
    def ok(self) -> bool:
        return not self.problems

    def of_kind(self, kind: str) -> list[ObjectConfig]:
        return [o for o in self.objects if o.kind == kind]

    def find(self, kind: str, obj_id: str) -> ObjectConfig | None:
        return next((o for o in self.objects if o.kind == kind and o.id == obj_id), None)


class ConfigError(Exception):
    def __init__(self, problems: list[ConfigProblem]):
        self.problems = problems
        super().__init__("; ".join(f"{p.path}: {p.message}" for p in problems[:5])
                         + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""))


def _plain(value: Any) -> Any:
    """ruamel round-trip types -> plain dict/list/str/int/float, dropping x-* keys."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items() if not str(k).startswith("x-")}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _read_yaml(path: str) -> Any:
    yaml = YAML(typ="rt")
    with open(path, encoding="utf-8") as fh:
        return yaml.load(fh)


def _validation_problems(prefix: str, exc: ValidationError) -> list[ConfigProblem]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        out.append(ConfigProblem(f"{prefix}.{loc}" if loc else prefix, err["msg"]))
    return out


def load_config(path: str, reg: Registry | None = None, strict: bool = True) -> MachineConfig:
    try:
        raw = _read_yaml(path)
    except Exception as exc:
        cfg = MachineConfig(path=path)
        cfg.problems.append(ConfigProblem("", f"cannot read {path}: {exc}"))
        return _finish(cfg, strict)
    return build_config(_plain(raw) if raw is not None else {}, path, reg, strict)


def build_config(data: Any, path: str | None, reg: Registry | None = None, strict: bool = True) -> MachineConfig:
    """Validate configuration data (the plain content of the main file). Includes are resolved relative to `path`."""
    import copy

    reg = reg or default_registry
    cfg = MachineConfig(path=path)
    problems = cfg.problems
    data = copy.deepcopy(data)
    if not isinstance(data, dict):
        problems.append(ConfigProblem("", "top level must be a mapping"))
        return _finish(cfg, strict)

    # includes: merge sections from extra files
    base = os.path.dirname(os.path.abspath(path)) if path else os.getcwd()
    for pattern in data.get("include", []) or []:
        for inc in sorted(glob.glob(os.path.join(base, pattern))):
            try:
                inc_data = _plain(_read_yaml(inc)) or {}
            except Exception as exc:
                problems.append(ConfigProblem("include", f"cannot read {inc}: {exc}"))
                continue
            for section, content in inc_data.items():
                if section not in SECTIONS or not isinstance(content, dict):
                    problems.append(ConfigProblem(f"include({os.path.basename(inc)}).{section}",
                                                  "included files may only contain object sections"))
                    continue
                target = data.setdefault(section, {})
                for obj_id, obj in content.items():
                    if obj_id in target:
                        problems.append(ConfigProblem(f"{section}.{obj_id}", f"defined twice (also in {inc})"))
                    target[obj_id] = obj
                    cfg.included[f"{section}.{obj_id}"] = inc
    cfg.data = data

    if data.get("format") != FORMAT:
        problems.append(ConfigProblem("format", f"expected {FORMAT!r}, got {data.get('format')!r}"))
    for key in data:
        if key not in SECTIONS and key not in OTHER_KEYS:
            problems.append(ConfigProblem(key, "unknown top-level key"))
    cfg.name = str(data.get("name") or "")
    cfg.vision = data.get("vision") or {}

    # plugins named in the file must be installed
    cfg.plugins = list(data.get("plugins") or [])
    installed = reg.installed_plugin_names()
    missing_plugins = [p for p in cfg.plugins if p not in installed]
    for p in missing_plugins:
        problems.append(ConfigProblem("plugins", f"plugin {p!r} is not installed"))

    try:
        cfg.machine = MachineSection.model_validate(data.get("machine") or {})
    except ValidationError as exc:
        problems.extend(_validation_problems("machine", exc))

    def add_object(kind: str, obj_id: str, obj: Any, path_: str, head: str | None = None) -> None:
        if not isinstance(obj, dict):
            problems.append(ConfigProblem(path_, "must be a mapping"))
            return
        obj = dict(obj)
        type_name = obj.pop("type", None) or DEFAULT_TYPES.get(kind)
        if not type_name:
            problems.append(ConfigProblem(f"{path_}.type", "missing"))
            return
        info = reg.get(kind, type_name)
        if info is None:
            hint = f" (provided by a plugin? not installed: {', '.join(missing_plugins)})" if missing_plugins else ""
            known = ", ".join(t.type for t in reg.of_kind(kind))
            problems.append(ConfigProblem(f"{path_}.type", f"unknown {kind} type {type_name!r}{hint}; known: {known}"))
            return
        try:
            settings = info.model.model_validate(obj)
        except ValidationError as exc:
            problems.extend(_validation_problems(path_, exc))
            cfg.invalid.add((kind, obj_id))
            return
        if any(o.kind == kind and o.id == obj_id for o in cfg.objects):
            problems.append(ConfigProblem(path_, f"{kind} id {obj_id!r} is used twice"))
            return
        cfg.objects.append(ObjectConfig(kind, obj_id, type_name, settings, head, path_))

    for section, kind in SECTIONS.items():
        content = data.get(section) or {}
        if not isinstance(content, dict):
            problems.append(ConfigProblem(section, "must be a mapping of id -> object"))
            continue
        for obj_id, obj in content.items():
            add_object(kind, str(obj_id), obj, f"{section}.{obj_id}")

    # nozzles and cameras mounted on heads
    for head in cfg.of_kind("head"):
        for nz_id, nz in head.settings.nozzles.items():
            add_object("nozzle", str(nz_id), nz, f"heads.{head.id}.nozzles.{nz_id}", head=head.id)
        for cam_id, cam in head.settings.cameras.items():
            add_object("camera", str(cam_id), cam, f"heads.{head.id}.cameras.{cam_id}", head=head.id)

    _check_references(cfg)
    return _finish(cfg, strict)


def _check_references(cfg: MachineConfig) -> None:
    problems = cfg.problems

    def need(kind: str, ref: str | None, path_: str, optional: bool = False) -> None:
        if ref is None:
            if not optional:
                problems.append(ConfigProblem(path_, "missing reference"))
            return
        if cfg.find(kind, ref) is None and (kind, ref) not in cfg.invalid:   # invalid ones are reported already
            problems.append(ConfigProblem(path_, f"no {kind} with id {ref!r}"))

    for o in cfg.objects:
        s = o.settings
        if o.kind == "axis" and o.type == "controller":
            need("driver", s.driver, f"{o.path}.driver")
        if o.kind == "axis" and o.type == "mapped":
            need("axis", s.input, f"{o.path}.input")
            (i0, _), (i1, _) = s.map
            if i0 == i1:
                problems.append(ConfigProblem(f"{o.path}.map", "the two input values must differ"))
        if o.kind == "head":
            need("axis", s.axes.x, f"{o.path}.axes.x")
            need("axis", s.axes.y, f"{o.path}.axes.y")
            need("actuator", s.pump, f"{o.path}.pump", optional=True)
            for i, a in enumerate(s.actuators):
                need("actuator", a, f"{o.path}.actuators[{i}]")
        if o.kind == "nozzle":
            need("axis", s.axes.z, f"{o.path}.axes.z")
            need("axis", s.axes.rotation, f"{o.path}.axes.rotation")
            need("actuator", s.vacuum, f"{o.path}.vacuum", optional=True)
            need("nozzle_tip", s.tip, f"{o.path}.tip", optional=True)
            for i, t in enumerate(s.compatible_tips):
                need("nozzle_tip", t, f"{o.path}.compatible_tips[{i}]")
        if o.kind == "camera":
            if o.head is None and s.location is None:
                problems.append(ConfigProblem(f"{o.path}.location", "machine-mounted cameras need a location"))
            if s.axes is not None:
                need("axis", s.axes.z, f"{o.path}.axes.z")
                need("axis", s.axes.rotation, f"{o.path}.axes.rotation")
        if o.kind == "feeder" and o.type == "auto":
            need("actuator", s.feed_actuator, f"{o.path}.feed_actuator", optional=True)
            need("actuator", s.post_pick_actuator, f"{o.path}.post_pick_actuator", optional=True)
        if o.kind == "actuator" and o.type == "gcode":
            need("driver", s.driver, f"{o.path}.driver")
    park = cfg.machine.park
    if park is not None:
        need("head", park.head, "machine.park.head")

    # sequences: home, park, safe_z, {actuate: {id: value}}, {dwell: ms}; macros also {move_to: ...}, {gcode: ...}
    sequences = [(f"machine.{seq}", getattr(cfg.machine, seq)) for seq in ("on_enable", "on_before_home", "on_home")]
    sequences += [(f"machine.macros.{name}", steps) for name, steps in cfg.machine.macros.items()]
    for base, steps in sequences:
        seq = base.rsplit(".", 1)[-1]
        if not isinstance(steps, list):
            problems.append(ConfigProblem(base, "must be a list of steps"))
            continue
        for i, step in enumerate(steps):
            where = f"{base}[{i}]"
            if step in ("home", "park", "safe_z"):
                if step == "home" and seq in ("on_home", "on_before_home"):
                    problems.append(ConfigProblem(where, f"home inside {seq} would loop"))
                if step == "park" and park is None:
                    problems.append(ConfigProblem(where, "park needs machine.park"))
            elif isinstance(step, dict) and list(step) == ["actuate"] and isinstance(step["actuate"], dict):
                for act_id in step["actuate"]:
                    need("actuator", str(act_id), f"{where}.actuate.{act_id}")
            elif isinstance(step, dict) and list(step) == ["dwell"] and isinstance(step["dwell"], (int, float)):
                pass
            elif base.startswith("machine.macros.") and isinstance(step, dict) and list(step) == ["move_to"] \
                    and isinstance(step["move_to"], dict) and isinstance(step["move_to"].get("location"), dict):
                obj = str(step["move_to"].get("object", ""))
                kind, _, oid = obj.partition("/")
                need(kind, oid, f"{where}.move_to.object") if kind in ("nozzle", "camera", "axis") else \
                    problems.append(ConfigProblem(f"{where}.move_to.object", "a nozzle/<id>, camera/<id> or axis/<id>"))
            elif base.startswith("machine.macros.") and isinstance(step, dict) and list(step) == ["gcode"] \
                    and (isinstance(step["gcode"], str) or isinstance(step["gcode"], dict)):
                pass
            else:
                problems.append(ConfigProblem(where, f"unknown step {step!r}; use home, park, safe_z, "
                                                     "{actuate: {id: value}}, {dwell: ms}"
                                                     + (", {move_to: {object, location}}, {gcode: text}"
                                                        if base.startswith("machine.macros.") else "")))


def _finish(cfg: MachineConfig, strict: bool) -> MachineConfig:
    if strict and cfg.problems:
        raise ConfigError(cfg.problems)
    return cfg
