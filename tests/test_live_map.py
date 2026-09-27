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
FAR = texture_at(44.0, 2.0, 16)  # 256 px, as every texture within 300 km of a flight
BUILT = {LYON[0]: 0, LYON[1]: -1, LYON[2]: 2, GENEVA: 0, FAR: 4}


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
    # by the zoom of its photos, the sharpest first: Geneva's ZL18 at 4096 px (ZL18 photos)
    # before Lyon's ZL16 grown to 8192 px (ZL17), then 4096 (ZL16), then 1024 px (ZL14)
    assert [row[4] for row in near["textures"]] == [18, 17, 16, 14]
    assert near["squares"] == [] and near["more"] is False
    south, west, north, east, _ = near["textures"][0]
    assert south < north and west < east
    # a wide view: every texture of ZL15 photos or sharper on its own still (a square of one
    # colour hid the sharp ones), the blurrier grouped by square
    wide = mapdata.built_in(pack, -10.0, 35.0, 20.0, 55.0)
    assert [row[4] for row in wide["textures"]] == [18, 17, 16]
    assert sorted(wide["squares"]) == [[43, 1, 1, 12], [45, 4, 1, 14]]  # squares of their middles
    assert mapdata.built_in(pack, 100.0, 10.0, 101.0, 11.0)["textures"] == []
    # where the user's own tile is, X-Plane shows it: Live's textures there are left out
    theirs = mapdata.built_in(pack, 4.5, 45.3, 6.5, 46.5, covered={(46, 6)})
    assert [row[4] for row in theirs["textures"]] == [17, 16, 14]  # Geneva's ZL18 left out
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
        # the tooltip's facts: the texture under a point, the airport's ZL18 before the ZL16
        at = (await client.get("/api/live/map/texture?lat=46.24&lon=6.11")).json()["textures"]
        assert [t["zoom"] for t in at] == [18]
        assert at[0]["px"] == 4096 and at[0]["photos_zoom"] == 18 and at[0]["bytes"] > 10**7
        assert (await client.get("/api/live/map/regions")).json() == {}  # none installed
        assert (await client.get("/api/live/map/own")).json() == []  # no X-Plane: no own tile
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


class FakeStreamer:
    """What the map reads of a running streamer: its textures built, versioned, and their facts."""

    def __init__(self) -> None:
        self.version, self.built, self.grown = 1, dict(BUILT), {LYON[1]}
        self.copied = 0

    def facts_now(self):
        return dict(self.built), set(self.grown), set(), {}

    def built_now(self, known: int = -1):
        if known == self.version:
            return self.version, None, None
        self.copied += 1
        return self.version, dict(self.built), set(self.grown)


def test_the_map_reads_the_running_streamer(home: Path) -> None:
    control = LiveControl(
        echo=lambda _: None, catalog=lambda root: {}, checks=lambda: {"xplane": None}
    )
    streamer = FakeStreamer()
    control._attach(streamer)  # as session.follow does once the streamer runs
    view = (4.5, 45.3, 6.5, 46.5)
    got = control.built_in(*view)
    assert got["version"] == 1 and len(got["textures"]) == 4  # nothing on the disk: its memory
    under = control.textures_at(45.74, 4.95)  # the texture grown to 8192 px, from its memory
    assert [(t["px"], t["photos_zoom"], t["grown"]) for t in under] == [(8192, 17, True)]
    assert control.built_in(*view, known=1) == {"version": 1, "same": True}  # nothing new
    assert streamer.copied == 1  # "same" copies nothing
    streamer.built[LYON[2]], streamer.version = 0, 2  # one more built sharper
    got = control.built_in(*view, known=1)
    assert got["version"] == 2 and [r[4] for r in got["textures"]] == [18, 17, 16, 16]
    control._attach(None)  # stopped: the record on the disk again
    assert "version" not in control.built_in(*view)


def test_the_streamer_counts_its_changes(tmp_path: Path) -> None:
    from live_helpers import NAMES, FakeDownloader, make_source
    from orthostudio.live.cache import ChunkCache
    from orthostudio.live.region import setup_region
    from orthostudio.live.streamer import Streamer

    pack = tmp_path / "pack"
    setup_region(make_source(tmp_path, textures=NAMES), pack, log=lambda _: None)
    s = Streamer(
        pack,
        ChunkCache(tmp_path / "c.sqlite"),
        FakeDownloader,
        finest_quality=1,
        log=lambda _: None,
    )
    try:
        version, built, grown = s.built_now()
        assert built == {} and grown == set()
        assert s.built_now(version) == (version, None, None)

        class Done:
            def result(self):
                return None

        s._finished(Done(), f"{NAMES[0]}.dds", 0)  # a texture built
        after, built, _ = s.built_now(version)
        assert after == version + 1 and built == {f"{NAMES[0]}.dds": 0}
    finally:
        s.cache.close()
