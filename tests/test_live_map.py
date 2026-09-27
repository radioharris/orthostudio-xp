# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The Live window's map: OrthoStudio XP's base map and airports served beside Live's page, the
textures built in a view (each one, or one-degree squares when the view is wide), the regions
installed, the SimBrief route on the button only; the page's scripts."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

import test_api_fakes as fakes
from orthostudio.live import mapdata
from orthostudio.live.control import LiveControl
from orthostudio.live.flightplan import FlightPlan
from orthostudio.live.web import UI_DIR, create_live_app
from orthostudio.net.fetch import FetchRequest, FetchResult
from test_api_fakes import client_for

anyio_backend = fakes.anyio_backend
NODE = shutil.which("node")


def texture_at(lat: float, lon: float, zoom: int) -> str:
    """The name of the texture holding (lat, lon) at ``zoom``: its first piece, 16 to a side."""
    n = 2**zoom
    x = int((lon + 180) / 360 * n) // 16 * 16
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n) // 16 * 16
    return f"{y}_{x}_BI{zoom}.dds"  # as the pack's record names it


LYON = [texture_at(45.76, 4.84, 16), texture_at(45.76, 4.97, 16), texture_at(45.90, 4.84, 16)]
GENEVA = texture_at(46.24, 6.11, 18)
BUILT = {LYON[0]: 0, LYON[1]: -1, LYON[2]: 2, GENEVA: 0}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OSXP_API_NO_AIRPORTS", "1")
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def write_pack(pack: Path) -> None:
    """The record of a pack where these were built: one grown to 8192 px (its level -1)."""
    (pack / "_live").mkdir(parents=True)
    grown = [n for n, level in BUILT.items() if level == -1]
    (pack / "_live" / "ortho.json").write_text(json.dumps({"textures": BUILT, "grown": grown}))


def test_the_textures_built_in_a_view(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_pack(pack)
    near = mapdata.built_in(pack, 4.5, 45.3, 6.5, 46.5)  # a small view: each texture
    levels = sorted(row[4] for row in near["textures"])
    assert levels == [-1, 0, 0, 2] and near["more"] is False
    south, west, north, east, _ = near["textures"][0]
    assert south < north and west < east
    wide = mapdata.built_in(pack, -10.0, 35.0, 20.0, 55.0)  # a wide view: squares
    assert "textures" not in wide
    assert sorted((lat, lon, count, best) for lat, lon, count, best in wide["squares"]) == [
        (45, 4, 3, -1),  # three textures near Lyon, the sharpest 8192 px
        (46, 6, 1, 0),  # Geneva's
    ]
    assert mapdata.built_in(pack, 100.0, 10.0, 101.0, 11.0) == {"textures": [], "more": False}
    assert mapdata.built_in(tmp_path / "nothing", 4.5, 45.3, 6.5, 46.5)["textures"] == []


def test_the_regions_installed_and_a_route() -> None:
    squares = mapdata.regions_installed({"eur"})
    assert list(squares) == ["eur"] and len(squares["eur"]) == 1981
    assert [45, 5] in squares["eur"]  # Lyon's square
    assert mapdata.regions_installed(set()) == {}
    long = [(45.0 + i / 1000, 5.0) for i in range(2001)]
    drawn = mapdata.route_points(long)
    assert len(drawn) == mapdata.ROUTE_POINTS
    assert drawn[0] == [45.0, 5.0] and drawn[-1] == [47.0, 5.0]  # its ends kept


@pytest.mark.anyio
async def test_the_map_s_api(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from orthostudio.live import flightplan
    from orthostudio.live.pack import default_pack_dir
    from orthostudio.live.settings import LiveSettings, save_settings

    write_pack(default_pack_dir())
    tiles: list[str] = []

    async def fetch(request: FetchRequest) -> FetchResult:  # never the network in a test
        tiles.append(request.url)
        return FetchResult(request.key, 404, b"", {}, 0.0, 1, False, None)  # no imagery there

    # no X-Plane here: the regions AutoOrtho installed on this machine are not the test's
    control = LiveControl(
        echo=lambda _: None, catalog=lambda root: {}, checks=lambda: {"xplane": None}
    )
    async with client_for(create_live_app(control, map_fetch=fetch)) as client:
        got = await client.get("/api/live/map/built?west=4.5&south=45.3&east=6.5&north=46.5")
        assert len(got.json()["textures"]) == 4
        assert (await client.get("/api/live/map/regions")).json() == {}  # none installed
        # the SimBrief route: only on the button, and only with a name in Live's settings
        assert (await client.post("/api/live/map/route")).status_code == 409
        save_settings(LiveSettings(simbrief_user="pilot"))
        asked = []

        def last_plan(user: str):
            asked.append(user)
            return FlightPlan("LFLL-LSGG", [(45.72, 5.08), (46.24, 6.11)]), ""

        monkeypatch.setattr(flightplan, "last_plan", last_plan)
        route = (await client.post("/api/live/map/route")).json()
        assert route == {"name": "LFLL-LSGG", "points": [[45.72, 5.08], [46.24, 6.11]]}
        assert asked == ["pilot"]
        # OrthoStudio XP's base map, served beside Live's page
        tile = await client.get("/api/map/BI/6/33/22")
        assert tile.status_code == 204 and tiles  # asked upstream, "no imagery" passed on
        # airports: from OrthoStudio XP's index only if it is there
        assert (
            await client.get("/api/airports/in?west=5&south=45&east=6&north=46")
        ).status_code == 404
        page = (await client.get("/")).text
        assert "static/vendor/leaflet/leaflet.js" in page and 'id="live-map"' in page


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_map_s_scripts_parse() -> None:
    for name in ("basemap.js", "livemap.js", "live.js"):
        subprocess.run([NODE or "node", "--check", str(UI_DIR / name)], check=True)
