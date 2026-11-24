import json

import pytest
from fastapi.testclient import TestClient

from tinkerpnp.api.server import create_app
from tinkerpnp.core.config import load_config
from tinkerpnp.core.data import DataStore
from tinkerpnp.core.errors import MachineError
from tinkerpnp.core.machine import Machine

from conftest import tmp_config
from test_api import SIMULATED

BOARD = {"id": "demo", "name": "Demo board", "dimensions": {"x": 50, "y": "1.2 in"},
         "placements": [{"id": "R1", "part": "R0805-1K", "location": {"x": 10, "y": 5, "rotation": 90}},
                        {"id": "FID1", "type": "fiducial", "location": {"x": 2, "y": 2}}]}


def test_store_roundtrip(tmp_path):
    db = DataStore(str(tmp_path / "d.sqlite"))
    db.create("packages", {"id": "0805", "footprint": {"body_width": 2.0, "body_height": 1.25,
                                                       "pads": [{"name": "1", "x": -0.95, "width": 1, "height": 1.3}]}})
    db.create("parts", {"id": "R0805-1K", "package": "0805", "height": "0.5 mm"})
    b = db.create("boards", BOARD)
    assert b["revision"] == 1 and b["dimensions"]["y"] == pytest.approx(30.48)       # units converted
    assert db.get("boards", "demo")["placements"][0]["location"]["rotation"] == 90
    u = db.update("boards", "demo", {"name": "Renamed"}, revision=1)
    assert u["revision"] == 2 and u["placements"][1]["type"] == "fiducial"
    with pytest.raises(MachineError) as e:
        db.update("boards", "demo", {"name": "x"}, revision=1)
    assert e.value.name == "conflict"
    with pytest.raises(MachineError) as e:
        db.create("parts", {"id": "R0805-1K"})
    assert e.value.name == "conflict"
    assert db.list("parts", filter="0805")["total"] == 1
    assert db.counts() == {"parts": 1, "packages": 1, "boards": 1, "jobs": 0}
    db.delete("parts", "R0805-1K")
    assert db.find("parts", "R0805-1K") is None
    db.close()
    assert DataStore(str(tmp_path / "d.sqlite")).get("boards", "demo")["name"] == "Renamed"    # persisted


def test_validation():
    db = DataStore(":memory:")
    with pytest.raises(MachineError) as e:
        db.create("boards", {"id": "b", "placements": [{"id": "R1"}, {"id": "R1"}]})
    assert e.value.name == "validation_error" and "used twice" in json.dumps(e.value.data)
    with pytest.raises(MachineError) as e:
        db.create("parts", {"id": "p", "speed": 3, "colour": "red"})
    paths = {p["path"] for p in e.value.data["errors"]}
    assert paths == {"speed", "colour"}
    with pytest.raises(MachineError):
        db.list("widgets")


def test_api_and_notifications(tmp_path):
    cfg = tmp_config(SIMULATED)
    with TestClient(create_app(Machine(load_config(cfg)))) as c:
        assert c.app.state.tinker.data.path.endswith("simulated.data.sqlite")
        with c.websocket_connect("/websocket") as ws:
            r = c.post("/api/data/boards/create", json={"item": BOARD})
            assert r.status_code == 200 and r.json()["result"]["revision"] == 1
            for _ in range(20):
                msg = json.loads(ws.receive_text())
                if msg.get("method") == "notify_data_changed":
                    assert msg["params"] == {"collection": "boards", "id": "demo", "revision": 1, "change": "created"}
                    break
        assert c.get("/api/data/boards/get?id=demo").json()["result"]["name"] == "Demo board"
        assert c.get("/api/data/boards/list").json()["result"]["total"] == 1
        r = c.post("/api/data/boards/update", json={"id": "demo", "changes": {"name": "x"}, "revision": 9})
        assert r.status_code == 409
        assert c.post("/api/data/boards/create", json={"item": {"id": ""}}).status_code == 422
        assert c.get("/api/data/counts").json()["result"]["boards"] == 1
        assert c.post("/api/data/boards/delete", json={"id": "demo"}).json() == {"result": {}}


KICAD_ASCII = """### Footprint positions - created on 2026-11-24 ###
### Printed by KiCad version 9.0
## Unit = mm, Angle = deg.
## Side : All
# Ref     Val       Package                PosX       PosY       Rot  Side
C1        100n      C_0603_1608Metric     10.0000     5.0000    90.0000  top
FID1      Fiducial  Fiducial_1mm           2.0000     2.0000     0.0000  top
R1        10k       R_0805_2012Metric     20.5000    12.2500   180.0000  bottom
U1        STM32F411 LQFP-64_10x10mm_P0.5mm 30.0000   20.0000     0.0000  top
## End
"""

KICAD_CSV = '''"Ref","Val","Package","PosX","PosY","Rot","Side"
"C1","100n","C_0603_1608Metric",10.000000,5.000000,90.000000,top
"R1","10k","R_0805_2012Metric",20.500000,12.250000,180.000000,bottom
'''


def test_kicad_import():
    from tinkerpnp.core.imports import import_kicad_pos, parse_kicad_pos
    rows = parse_kicad_pos(KICAD_ASCII)
    assert [r["ref"] for r in rows] == ["C1", "FID1", "R1", "U1"]
    assert rows[2] == {"ref": "R1", "value": "10k", "package": "R_0805_2012Metric", "x": 20.5, "y": 12.25,
                       "rotation": 180.0, "side": "bottom"}
    assert parse_kicad_pos(KICAD_CSV)[1]["side"] == "bottom"
    db = DataStore(":memory:")
    r = import_kicad_pos(db, KICAD_ASCII, "mc5")
    assert r["placements"] == 4 and r["fiducials"] == 1 and r["bottom"] == 1
    assert "C_0603_1608Metric-100n" in r["created_parts"] and "LQFP-64_10x10mm_P0.5mm" in r["created_packages"]
    board = db.get("boards", "mc5")
    assert board["placements"][1]["type"] == "fiducial"
    assert db.get("parts", "C_0603_1608Metric-100n")["package"] == "C_0603_1608Metric"
    with pytest.raises(MachineError):
        import_kicad_pos(db, KICAD_ASCII, "mc5")                     # exists
    assert import_kicad_pos(db, KICAD_CSV, "mc5", replace=True)["created_parts"] == []   # parts exist already
