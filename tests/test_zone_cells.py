# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The squares a zone will take, shown on the map before the build (idea 7, 2026-10-11).

Every mesh cell a zone covers a part of takes its level, so the sharp area runs in straight lines
up to a cell beyond the outline, and a zone under the zones above it may take none; a user saw it
only in X-Plane. The page now draws the cells, which must be the build's own: they are checked
here against :func:`orthostudio.dsf.zones.texture_map`, what the DSF is written from.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from orthostudio.api.zones_api import zones_router
from orthostudio.dsf.params import DsfParams
from orthostudio.dsf.zones import texture_map
from orthostudio.model import TileRef
from orthostudio.zone_cells import zone_cells
from orthostudio.zones import parse_zones_document, zones_for_tile

anyio_backend = "asyncio"


def _ring(west: float, south: float, east: float, north: float) -> list[list[float]]:
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


ZONES = [
    {"id": "a", "name": "LSGG", "zl": 18, "polygon": _ring(6.09, 46.225, 6.13, 46.25)},
    {"id": "b", "name": "around", "zl": 17, "polygon": _ring(6.05, 46.20, 6.20, 46.30)},
    {"id": "c", "name": "inside a", "zl": 17, "polygon": _ring(6.10, 46.23, 6.11, 46.24)},
    {"id": "d", "name": "across", "zl": 17, "polygon": _ring(6.98, 46.50, 7.03, 46.53)},
]


def test_the_cells_are_the_builds_own() -> None:
    doc = parse_zones_document({"format": "osxp-zones-1", "zones": ZONES})
    got = {z["id"]: z for z in zone_cells(doc.zones, "BI", mesh_zl=19)}
    built: dict[int, int] = {17: 0, 18: 0}
    for tile in (TileRef(46, 6), TileRef(46, 7)):
        entries = zones_for_tile(doc.zones, tile, "BI", mesh_zl=19)
        params = DsfParams(default_zl=16, default_website="BI", mesh_zl=19, zone_list=entries)
        tmap = texture_map(tile, params)
        for zl in built:
            built[zl] += int((tmap.zl == zl).sum())
    assert got["a"]["count"] == built[18] == 20
    assert got["b"]["count"] + got["d"]["count"] == built[17]
    assert got["c"]["count"] == 0, "under the zone above it, it takes nothing"
    assert got["c"]["area"] == [] and got["d"]["count"] > 0
    # a's twenty cells are one rectangle: it covers what was drawn and runs less than a cell beyond
    (rings,) = got["a"]["area"]
    (outer,) = rings
    lons, lats = [p[0] for p in outer], [p[1] for p in outer]
    assert len(outer) == 5, "a rectangle of cells is four corners, not every cell's"
    assert min(lons) <= 6.09 < 6.13 <= max(lons) and min(lats) <= 46.225 < 46.25 <= max(lats)
    assert 6.09 - min(lons) < 0.02 and max(lons) - 6.13 < 0.02
    # b, around a, takes everything but a's cells: one polygon with a hole where a is
    (b_rings,) = got["b"]["area"]
    assert len(b_rings) == 2


@pytest.mark.anyio
async def test_the_page_asks_for_the_zones_it_holds(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(zones_router(lambda: tmp_path / "zones.json", mesh_zl_of=lambda: 19))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://osxp") as c:
        got = await c.post("/api/zones/cells", json={"zones": ZONES[:1], "provider": "BI"})
        bad = await c.post("/api/zones/cells", json={"zones": [{**ZONES[0], "zl": 21}]})
    assert got.status_code == 200
    body = got.json()
    assert body["mesh_zl"] == 19 and body["zones"][0]["id"] == "a"
    assert body["zones"][0]["count"] == 20
    assert not (tmp_path / "zones.json").exists(), "asking writes nothing"
    assert bad.status_code == 422 and bad.json()["error"]["code"].startswith("ZONE_")
