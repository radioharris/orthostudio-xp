# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The zoom set, as AutoOrtho's max_zoom and max_zoom_near_airports: each texture's file at the size
of its zoom (1024 px two zooms below the mesh's), a zoom above the mesh's starting at 4096 px and
growing to 8192 px where X-Plane reads that (as XPME's "normal resolution, then high"), a new zoom
laying the pack out again without losing what was built, and what a start waits for in each start
mode."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from live_helpers import NAMES, FakeDownloader, Image, make_source, make_xplane
from orthostudio.live import dds, session
from orthostudio.live.cache import ChunkCache
from orthostudio.live.evict import texture_bytes
from orthostudio.live.geo import parse_texture_name
from orthostudio.live.region import (
    LAYOUT_GROWING,
    NEUTRAL_LEVEL,
    REGION_MARKER,
    Zooms,
    ensure_neutral,
    needs_relayout,
    neutral_file,
    region_layout,
    region_zooms,
    relayout,
    setup_region,
)
from orthostudio.live.streamer import RELOAD_COMMAND, START_MODES, Fix, Streamer
from orthostudio.live.texture import downgrade, write_sparse

AIRPORT = "93184_132608_BI18"  # a ZL18 texture inside NAMES[0]: the mesh's airports
# ZL16 textures in a row eastwards from NAMES[0], one every 6.9 km (45.4° N)
ROW = tuple(f"23296_{33152 + 16 * k}_BI16" for k in range(12))


def quiet(_: str) -> None:
    pass


def side_of(path: Path) -> int:
    """The width a DDS file's header gives."""
    return int.from_bytes(path.read_bytes()[16:20], "little")


def region(root: Path, zooms: Zooms | None = None, textures=(*NAMES, AIRPORT)) -> Path:
    pack = root / "pack"
    source = make_source(root, tile=(45, 6), textures=textures)
    setup_region(source, pack, log=quiet, zooms=zooms)
    return pack


def levels_from(top: int, file_top: int) -> list[bytes]:
    """Levels ``top`` down, each byte its level's number (+10, so that no level reads as 0)."""
    layout = dds.level_layout(file_top)
    return [bytes([10 + file_top + k]) * n for k, (_, n) in enumerate(layout)][top - file_top :]


def level_bytes(path: Path, level: int, file_top: int) -> bytes:
    offset, length = dds.level_layout(file_top)[level - file_top]
    return path.read_bytes()[offset : offset + length]


def record(pack: Path, textures: dict[str, int], grown=()) -> None:
    """What the streamer saves: the textures built, to which level, and those grown."""
    meta = {"textures": textures, "seen": {}, "grown": sorted(grown)}
    (pack / "_live" / "ortho.json").write_text(json.dumps(meta))


def recorded(pack: Path) -> dict:
    return json.loads((pack / "_live" / "ortho.json").read_text())


def grow(pack: Path, name: str) -> Path:
    """A texture as the streamer grows it: an 8192 px file, built from its 8192 px level."""
    path = pack / "textures" / name
    base = ensure_neutral(pack, -1)
    write_sparse(path, -1, levels_from(-1, -1), base=base, file_top=-1)
    return path


# --- the pack's layout --------------------------------------------------------------------------


def test_the_pack_laid_out_for_the_zoom(tmp_path: Path) -> None:
    """Away from airports ZL14: 1024 px files; around them ZL19: 4096 px ones, which grow."""
    pack = region(tmp_path, Zooms(max_zoom=14, max_zoom_airports=19))
    textures = pack / "textures"
    assert side_of(textures / f"{NAMES[0]}.dds") == 1024
    assert side_of(textures / f"{AIRPORT}.dds") == 4096
    assert (textures / f"{AIRPORT}.dds").stat().st_size == dds.file_size(4096)
    assert (pack / neutral_file(2)).exists()
    assert region_layout(pack) == (Zooms(14, 19), LAYOUT_GROWING)
    assert not needs_relayout(pack, Zooms(14, 19))
    assert needs_relayout(pack, Zooms(16, 19))


def test_the_mesh_zooms_are_the_pack_as_it_was(tmp_path: Path) -> None:
    """A pack set up before zooms could be set: 4096 px everywhere, the same as ZL16 and ZL18."""
    pack = region(tmp_path)
    assert side_of(pack / "textures" / f"{NAMES[0]}.dds") == 4096
    assert not needs_relayout(pack, Zooms(16, 18))
    assert relayout(pack, Zooms(16, 18), log=quiet) == 0
    assert region_zooms(pack) == Zooms(16, 18)


def test_a_new_zoom_keeps_what_is_built(tmp_path: Path) -> None:
    pack = region(tmp_path)
    built = pack / "textures" / f"{NAMES[0]}.dds"
    write_sparse(built, 0, levels_from(0, 0), base=pack / neutral_file(0))
    record(pack, {built.name: 0})
    lines: list[str] = []

    # down to ZL14: a 1024 px file, its levels from 2 down the very ones built, nothing downloaded
    assert relayout(pack, Zooms(14, 18), log=lines.append) == 2  # the two ZL16 textures
    assert side_of(built) == 1024
    for level in range(2, 5):
        assert level_bytes(built, level, 2) == levels_from(level, 0)[0]
    assert recorded(pack)["textures"] == {built.name: 2}
    other = pack / "textures" / f"{NAMES[1]}.dds"
    assert other.read_bytes() == (pack / neutral_file(2)).read_bytes()  # still neutral
    assert side_of(pack / "textures" / f"{AIRPORT}.dds") == 4096  # airports untouched
    assert any("ZL16 textures: 2 to 1024 px" in line for line in lines)

    # up to ZL17: 4096 px again, the start size, its levels kept, the sharper ones neutral
    assert relayout(pack, Zooms(17, 18), log=quiet) == 2
    assert side_of(built) == 4096
    assert level_bytes(built, 2, 0) == levels_from(2, 0)[0]
    neutral = pack / neutral_file(0)
    for level in (0, 1):
        assert level_bytes(built, level, 0) == level_bytes(neutral, level, 0)
    assert recorded(pack)["textures"] == {built.name: 2}
    assert recorded(pack)["grown"] == []
    assert not needs_relayout(pack, Zooms(17, 18))


def test_a_grown_texture_keeps_its_size_while_its_zoom_goes_that_far(tmp_path: Path) -> None:
    pack = region(tmp_path, Zooms(17, 18))
    path = grow(pack, f"{NAMES[0]}.dds")
    record(pack, {path.name: -1}, grown=[path.name])
    assert not needs_relayout(pack, Zooms(17, 18))
    assert not needs_relayout(pack, Zooms(17, 19))  # the airports' zoom: not this texture's

    # back to ZL16: 4096 px, its levels from 0 down the ones built at 8192 px
    assert relayout(pack, Zooms(16, 18), log=quiet) == 1
    assert side_of(path) == 4096
    for level in range(5):
        assert level_bytes(path, level, 0) == levels_from(level, -1)[0]
    assert recorded(pack)["textures"] == {path.name: 0}
    assert recorded(pack)["grown"] == []


def test_a_pack_laid_out_before_textures_grew(tmp_path: Path) -> None:
    """A pack of ZL17 made when every file was 8192 px from the start: laid out again once, at
    4096 px, but the textures built at 8192 px stay so."""
    pack = region(tmp_path, Zooms(16, 18))
    from orthostudio.live.clone import replace_with_clone

    sharp = grow(pack, f"{NAMES[0]}.dds")
    neutral8k = pack / "textures" / f"{NAMES[1]}.dds"
    replace_with_clone(str(pack / neutral_file(-1)), str(neutral8k))
    record(pack, {sharp.name: -1})  # no "grown": the old layout
    marker = pack / REGION_MARKER
    meta = json.loads(marker.read_text())
    meta["zooms"] = {"max_zoom": 17, "max_zoom_airports": 18}
    del meta["layout"]
    marker.write_text(json.dumps(meta))

    assert region_layout(pack) == (Zooms(17, 18), 1)
    assert needs_relayout(pack, Zooms(17, 18))
    assert relayout(pack, Zooms(17, 18), log=quiet) == 1
    assert side_of(sharp) == 8192
    assert level_bytes(sharp, -1, -1) == levels_from(-1, -1)[0]
    assert neutral8k.read_bytes() == (pack / neutral_file(0)).read_bytes()
    assert recorded(pack)["grown"] == [sharp.name]
    assert region_layout(pack) == (Zooms(17, 18), LAYOUT_GROWING)


def test_an_8192_texture_on_the_disk_and_made_coarser(tmp_path: Path) -> None:
    assert texture_bytes(-1) == 4096 + sum(n for _, n in dds.level_layout(-1))
    assert texture_bytes(-1) > 4 * texture_bytes(0) - 1_000_000
    pack = region(tmp_path, Zooms(17, 18))
    path = grow(pack, f"{NAMES[0]}.dds")
    neutral = pack / neutral_file(0)
    downgrade(path, 4, neutral, file_top=-1, to_top=0)  # back to its start size
    assert side_of(path) == 4096
    assert level_bytes(path, 4, 0) == levels_from(4, -1)[0]
    assert level_bytes(path, 0, 0) == level_bytes(neutral, 0, 0)
    downgrade(path, NEUTRAL_LEVEL, neutral, file_top=0)
    assert path.read_bytes() == neutral.read_bytes()


def test_no_new_zoom_under_a_running_xplane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X-Plane keeps a texture at the size it first read: the pack is laid out again only once it
    has quit."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    xp = make_xplane(tmp_path)
    pack = region(tmp_path, Zooms(16, 18))
    (xp / "Custom Scenery" / "z_osxp_live").symlink_to(pack)
    monkeypatch.setattr(session, "xplane_running", lambda: True)
    monkeypatch.setattr(session, "is_installed", lambda *a: True)
    options = session.Options(
        xplane=xp, pack=pack, source=tmp_path / "z_ao_test", max_zoom=14, no_plugin=True
    )
    with pytest.raises(session.RefusedError, match="zoom changed and X-Plane is running"):
        session.prepare(options, log=quiet)
    assert side_of(pack / "textures" / f"{NAMES[0]}.dds") == 4096  # untouched

    # ZL17 changes no file's size: textures grow as X-Plane flies, and it may run
    options.max_zoom = 17
    session.prepare(options, log=quiet)
    assert region_layout(pack) == (Zooms(17, 18), LAYOUT_GROWING)


# --- growing to 8192 px -------------------------------------------------------------------------


class Xp:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, name: str) -> None:
        self.commands.append(name)


def row_streamer(tmp_path: Path, mode: str, reach_km: float = 25.0, **kw) -> Streamer:
    pack = region(tmp_path, Zooms(17, 18), textures=ROW)
    cache = ChunkCache(tmp_path / "c.sqlite")
    return Streamer(
        pack,
        cache,
        FakeDownloader,
        finest_quality=0,
        max_zoom=17,
        max_zoom_airports=18,
        reach_km=reach_km,
        log=quiet,
        start_mode=mode,
        **kw,
    )


def at(k: int, **kw) -> Fix:
    lat, lon = parse_texture_name(f"{ROW[k]}.dds").center
    return Fix(lat, lon, **kw)


def sharp(jobs: dict) -> list[int]:
    """Which textures of the row are to be built at 8192 px."""
    return [k for k, name in enumerate(ROW) if jobs.get(f"{name}.dds", (0, 0))[1] == -1]


@pytest.mark.parametrize(
    ("mode", "waits_for"),
    [("full", [0, 1, 2, 3, 4]), ("around", [0, 1, 2]), ("normal", []), ("reload", [])],
)
def test_what_a_start_waits_for(tmp_path: Path, mode: str, waits_for: list[int]) -> None:
    """At a start: 8192 px as far as the camera may go (25 km more), wherever X-Plane reads 4096
    px while the flight loads (15 km with the margin), or none; X-Plane waits for them (urgency
    <= 0)."""
    s = row_streamer(tmp_path, mode)
    jobs = s.plan(at(0), x_plane_there=False, starting=True)
    assert sharp(jobs) == waits_for
    for k in waits_for:
        assert jobs[f"{ROW[k]}.dds"][0] <= 0
    assert all(jobs[f"{name}.dds"][1] == 0 for name in ROW[5:6])  # 4096 px, with the reach
    s.cache.close()


@pytest.mark.parametrize("mode", START_MODES)
def test_in_flight_textures_grow_before_xplane_reads_them(tmp_path: Path, mode: str) -> None:
    """Whatever the start mode: on the path ahead, while still farther than X-Plane reads 4096 px
    (12 km); not the textures X-Plane has already, nor those behind."""
    s = row_streamer(tmp_path, mode)
    east = s.plan(at(0, gs_ms=200.0, trk_deg=90.0), x_plane_there=False)
    assert sharp(east) == list(range(2, 10))  # the path: 60 km ahead, 7.5 km around its end
    assert all(east[f"{ROW[k]}.dds"][0] > 0 for k in range(2, 10))  # never what X-Plane waits for
    assert east[f"{ROW[1]}.dds"][1] == 0  # 6.9 km: X-Plane has read it at 4096 px
    assert east[f"{ROW[10]}.dds"][1] == 0
    west = s.plan(at(0, gs_ms=200.0, trk_deg=270.0), x_plane_there=False)
    assert sharp(west) == []  # behind
    s.cache.close()


def test_no_8192_px_from_high_up(tmp_path: Path) -> None:
    """X-Plane measures from the camera in three dimensions: at cruise, a texture right under the
    aircraft is 10 km away, and X-Plane never reads its 8192 px level there; low, the textures
    ahead grow as they do on the ground."""
    s = row_streamer(tmp_path, "around")
    east = {"gs_ms": 200.0, "trk_deg": 90.0}
    assert sharp(s.plan(at(0, agl_m=10_700.0, **east), x_plane_there=False)) == []  # FL350
    assert sharp(s.plan(at(0, agl_m=8_000.0, **east), x_plane_there=False)) == []
    assert sharp(s.plan(at(0, agl_m=900.0, **east), x_plane_there=False)) == list(range(2, 10))
    # an external view's camera counts at its own height: down low, while the aircraft is high
    low_camera = at(0, agl_m=10_700.0, **east)
    low_camera.cam_lat, low_camera.cam_lon = at(5).lat, at(5).lon
    low_camera.cam_agl_m = 300.0
    assert sharp(s.plan(low_camera, x_plane_there=False)) == []  # 4096 px read there already
    s.cache.close()


def built_as_planned(s: Streamer, jobs: dict) -> None:
    """Every job done, as the builder records it."""
    for name, (_, level) in jobs.items():
        s.built[name] = level
        if level < 0:
            s.grown.add(name)


def test_the_reload_offered_after_a_start(tmp_path: Path) -> None:
    """The start mode "reload": X-Plane starts at 4096 px; the 8192 px textures of "full" are built
    afterwards, not urgent, and once they all are, with nothing X-Plane may show left, the reload is
    offered; asked, X-Plane gets it once."""
    xp = Xp()
    s = row_streamer(tmp_path, "reload", xplane=xp)
    start = at(0)
    assert sharp(s.plan(start, x_plane_there=False, starting=True)) == []  # the start: 4096 px
    assert s.offer_state() is None
    s.offer_reload(start.lat, start.lon)  # X-Plane is there
    jobs = s.plan(start, x_plane_there=False)
    assert sharp(jobs) == [0, 1, 2, 3, 4]
    assert all(jobs[f"{ROW[k]}.dds"][0] > 0 for k in range(5))  # after what X-Plane shows
    assert s.offer_state() == {"state": "building", "left": 5, "id": int(s.offer.since)}
    assert not s.reload_now()  # not ready
    built_as_planned(s, jobs)
    s.plan(start, x_plane_there=False)
    assert s.offer_state()["state"] == "ready"
    assert s.reload_now()
    assert xp.commands == [RELOAD_COMMAND]
    assert s.offer_state()["state"] == "asked"
    assert not s.reload_now()  # once
    s.offer_reload(start.lat, start.lon)  # the reload itself, seen as a start: no new offer
    assert s.offer_state()["state"] == "asked"
    s.cache.close()


def test_no_reload_offered_once_the_aircraft_has_left(tmp_path: Path) -> None:
    s = row_streamer(tmp_path, "reload")
    start = at(0)
    s.offer_reload(start.lat, start.lon)
    s.plan(start, x_plane_there=False)
    assert s.offer_state()["state"] == "building"
    s.plan(at(2), x_plane_there=False)  # 13.8 km on: taken off
    assert s.offer_state() is None
    s.cache.close()


def test_no_reload_offered_in_the_other_modes(tmp_path: Path) -> None:
    s = row_streamer(tmp_path, "around")
    start = at(0)
    s.offer_reload(start.lat, start.lon)
    assert s.offer_state() is None
    s.cache.close()


def test_one_build_at_a_time_per_texture(tmp_path: Path) -> None:
    """Its file is the last build's: a sharper one waits for the one on its way."""
    s = row_streamer(tmp_path, "full")
    name = f"{ROW[0]}.dds"
    s.queue = {name: (0.0, 2)}
    assert [n for n, _, _ in s.take_batch()] == [name]
    s.queue = {name: (-1.0, -1)}
    assert s.take_batch() == []
    s.in_flight.clear()
    assert [(n, lv) for n, lv, _ in s.take_batch()] == [(name, -1)]
    s.cache.close()


def test_a_grown_texture_made_coarser_goes_back_to_4096_px(tmp_path: Path) -> None:
    """Beyond the disk budget, far away: as any texture, its levels kept from 256 px down, and its
    file at its start size again."""
    s = row_streamer(tmp_path, "full", disk_budget=1)
    name = f"{ROW[0]}.dds"
    path = grow(s.pack_dir, name)
    s.built[name] = -1
    s.grown.add(name)
    assert s.evict() == 1
    assert side_of(path) == 4096
    assert level_bytes(path, 4, 0) == levels_from(4, -1)[0]
    assert s.built[name] == 4 and name not in s.grown
    s.save()
    assert recorded(s.pack_dir)["grown"] == []
    s.cache.close()


@pytest.mark.skipif(Image is None, reason="needs numpy, Pillow and ispc_texcomp")
@pytest.mark.parametrize(
    ("max_zoom", "mode", "level", "side"),
    [(17, "full", -1, 8192), (17, "normal", 0, 4096), (14, "full", 2, 1024)],
)
def test_the_streamer_builds_to_the_file_size(
    tmp_path: Path, max_zoom: int, mode: str, level: int, side: int
) -> None:
    pack = region(tmp_path, Zooms(max_zoom, 18))
    cache = ChunkCache(tmp_path / "c.sqlite")
    s = Streamer(
        pack,
        cache,
        FakeDownloader,
        finest_quality=0,
        max_zoom=max_zoom,
        max_zoom_airports=18,
        workers=2,
        reach_km=0.0,
        log=quiet,
        start_mode=mode,
    )
    name = f"{NAMES[0]}.dds"
    lat, lon = parse_texture_name(name).center
    jobs = s.plan(Fix(lat, lon), x_plane_there=False, starting=True)
    assert jobs[name][1] == level  # the sharpest level X-Plane reads there, as the mode has it
    s.start()
    deadline = time.time() + 120
    while s.built.get(name) != level and time.time() < deadline:
        time.sleep(0.1)
    s.close()
    cache.close()
    assert s.built[name] == level
    path = pack / "textures" / name
    assert side_of(path) == side
    assert path.stat().st_size == dds.file_size(side)
    assert (name in recorded(pack)["grown"]) == (side == 8192)
