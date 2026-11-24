"""Importing boards from CAD placement files.

KiCad (`.pos`, from File → Fabrication Outputs → Component Placement): ASCII (`# Ref Val Package PosX PosY Rot Side`
columns, millimetres) or CSV (`Ref,Val,Package,PosX,PosY,Rot,Side`). Part ids are `<Package>-<Value>`, like
OpenPnP's KiCad importer; packages are named after KiCad's footprint name. Positions are relative to the origin used
for the export (use the drill/place file origin at the board's lower left corner).
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any

from .errors import MachineError


def parse_kicad_pos(text: str) -> list[dict]:
    rows: list[dict] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise MachineError("validation_error", "empty file", errors=[{"path": "", "message": "empty file"}])
    if lines[0].lstrip().startswith('"Ref"') or lines[0].lstrip().startswith("Ref,"):
        reader = csv.DictReader(io.StringIO("\n".join(lines)))
        for r in reader:
            rows.append({k.strip(): (v or "").strip() for k, v in r.items()})
    else:
        header = None
        for ln in lines:
            if ln.startswith("#"):
                cols = ln.lstrip("#").split()
                if cols and cols[0] == "Ref":
                    header = cols
                continue
            if header is None:
                header = ["Ref", "Val", "Package", "PosX", "PosY", "Rot", "Side"]
            parts = ln.split()
            if len(parts) < len(header):       # values with spaces: Ref, then the last 5 fixed columns
                continue
            if len(parts) > len(header):
                extra = len(parts) - len(header)
                parts = [parts[0], " ".join(parts[1:2 + extra])] + parts[2 + extra:]
            rows.append(dict(zip(header, parts)))
    out = []
    for i, r in enumerate(rows):
        try:
            out.append({"ref": r["Ref"], "value": r.get("Val", ""), "package": r.get("Package", ""),
                        "x": float(r["PosX"]), "y": float(r["PosY"]), "rotation": float(r.get("Rot", 0) or 0),
                        "side": "bottom" if r.get("Side", "top").lower().startswith("b") else "top"})
        except (KeyError, ValueError) as exc:
            raise MachineError("validation_error", f"line {i + 1}: {exc}",
                               errors=[{"path": f"line {i + 1}", "message": str(exc)}]) from None
    return out


def _clean_id(s: str) -> str:
    return re.sub(r"\s+", "_", s.strip()) or "unknown"


def import_kicad_pos(data: Any, text: str, board_id: str, name: str | None = None, replace: bool = False) -> dict:
    rows = parse_kicad_pos(text)
    created_parts, created_packages = [], []
    placements = []
    for r in rows:
        package = _clean_id(r["package"])
        part_id = _clean_id(f"{r['package']}-{r['value']}")
        if data.find("packages", package) is None:
            data.create("packages", {"id": package, "description": f"from KiCad ({r['package']})"})
            created_packages.append(package)
        if data.find("parts", part_id) is None:
            data.create("parts", {"id": part_id, "name": r["value"], "package": package})
            created_parts.append(part_id)
        placements.append({"id": r["ref"], "part": part_id, "side": r["side"],
                           "location": {"x": r["x"], "y": r["y"], "rotation": r["rotation"]},
                           "type": "fiducial" if re.match(r"^(FID|FD)\d*", r["ref"], re.I) else "placement"})
    xs = [p["location"]["x"] for p in placements] or [0]
    ys = [p["location"]["y"] for p in placements] or [0]
    board = {"id": board_id, "name": name or board_id, "placements": placements,
             "dimensions": {"x": round(max(xs) - min(0, min(xs)), 3), "y": round(max(ys) - min(0, min(ys)), 3)},
             "notes": "imported from a KiCad placement file"}
    saved = data.create("boards", board, replace=replace)
    return {"board": saved["id"], "revision": saved["revision"], "placements": len(placements),
            "fiducials": sum(1 for p in placements if p["type"] == "fiducial"),
            "bottom": sum(1 for p in placements if p["side"] == "bottom"),
            "created_parts": created_parts, "created_packages": created_packages}
