# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The zoom set, as AutoOrtho's max_zoom and max_zoom_near_airports: each texture's file at the size
of its zoom from the start (1024 px two zooms below the mesh's, 8192 px one above), an 8192 px file
holding the copy of its sharpest level built above it, a new zoom laying the pack out again without
losing what was built, and where the 8192 px level gets its photos."""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

import pytest

from live_helpers import NAMES, FakeDownloader, Image, make_source, make_xplane
from orthostudio.live import dds, session
from orthostudio.live.cache import ChunkCache
from orthostudio.live.evict import texture_bytes
from orthostudio.live.geo import parse_texture_name
from orthostudio.live.region import (
    LAYOUT_FIXED,
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
from orthostudio.live.streamer import Fix, Streamer
from orthostudio.live.texture import double_bc1, downgrade, write_sparse

AIRPORT = "93184_132608_BI18"  # a ZL18 texture inside NAMES[0]: the mesh's airports
# ZL16 textures in a row eastwards from NAMES[0], one every 6.8 km (45.4° N)
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


def record(pack: Path, textures: dict[str, int], **more) -> None:
    """What the streamer saves: the textures built, to which level (and, before, those grown)."""
    meta = {"textures": textures, "seen": {}, **more}
    (pack / "_live" / "ortho.json").write_text(json.dumps(meta))


def recorded(pack: Path) -> dict:
    return json.loads((pack / "_live" / "ortho.json").read_text())


def sharp_file(pack: Path, name: str) -> Path:
    """A texture built from its 8192 px level, in its 8192 px file."""
    path = pack / "textures" / name
    base = ensure_neutral(pack, -1)
    write_sparse(path, -1, levels_from(-1, -1), base=base, file_top=-1)
    return path


def as_layout(pack: Path, zooms: Zooms, layout: int | None) -> None:
    """The pack's marker as an older Live wrote it (``layout`` None: no layout at all)."""
    marker = pack / REGION_MARKER
    meta = json.loads(marker.read_text())
    meta["zooms"] = {"max_zoom": zooms.max_zoom, "max_zoom_airports": zooms.max_zoom_airports}
    if layout is None:
        meta.pop("layout", None)
    else:
        meta["layout"] = layout
    marker.write_text(json.dumps(meta))


# --- the copy of a level, twice as wide ---------------------------------------------------------


def pixel_indices(blob: bytes, side: int) -> list[list[tuple[int, int, int]]]:
    """Every pixel of a BC1 level as (its block's two colours, its index): what it shows."""
    n = side // 4
    out = [[(0, 0, 0)] * side for _ in range(side)]
    for by in range(n):
        for bx in range(n):
            at = 8 * (by * n + bx)
            c0 = int.from_bytes(blob[at : at + 2], "little")
            c1 = int.from_bytes(blob[at + 2 : at + 4], "little")
            bits = int.from_bytes(blob[at + 4 : at + 8], "little")
            for y in range(4):
                for x in range(4):
                    out[4 * by + y][4 * bx + x] = (c0, c1, (bits >> (2 * (4 * y + x))) & 3)
    return out


def test_the_copy_is_the_level_doubled() -> None:
    """Each pixel of the copy shows what the level's pixel under it shows: its block's colours
    and index, nothing decoded or encoded again."""
    rng = random.Random(7)
    side = 16
    blob = bytes(rng.randrange(256) for _ in range((side // 4) ** 2 * 8))
    copy = double_bc1(blob, side)
    assert len(copy) == 4 * len(blob)
    small, big = pixel_indices(blob, side), pixel_indices(copy, 2 * side)
    for y in range(2 * side):
        for x in range(2 * side):
            assert big[y][x] == small[y // 2][x // 2]


def test_an_8192_file_holds_the_copy_of_its_sharpest_level(tmp_path: Path) -> None:
    """X-Plane counts a file's levels from its top: it reads each level of an 8192 px file a ring
    before its photos are built, and finds there the copy of the level below. A 4096 px file has
    none: X-Plane reads each of its levels where its photos are built."""
    pack = region(tmp_path, Zooms(17, 18))
    path = pack / "textures" / f"{NAMES[0]}.dds"
    write_sparse(path, 2, levels_from(2, -1), base=ensure_neutral(pack, -1), file_top=-1)
    assert side_of(path) == 8192
    assert level_bytes(path, 1, -1) == double_bc1(levels_from(2, -1)[0], 1024)
    for level in (-1, 0):  # above the copy: the neutral colour, never read that early
        assert level_bytes(path, level, -1) == level_bytes(pack / neutral_file(-1), level, -1)
    for level in range(2, 5):
        assert level_bytes(path, level, -1) == levels_from(level, -1)[0]

    plain = pack / "textures" / f"{NAMES[1]}.dds"
    write_sparse(plain, 2, levels_from(2, 0), base=ensure_neutral(pack, 0), file_top=0)
    assert level_bytes(plain, 1, 0) == level_bytes(pack / neutral_file(0), 1, 0)


# --- the pack's layout --------------------------------------------------------------------------


def test_the_pack_laid_out_for_the_zoom(tmp_path: Path) -> None:
    """Away from airports ZL14: 1024 px files; around them ZL19: 8192 px ones, from the start."""
    pack = region(tmp_path, Zooms(max_zoom=14, max_zoom_airports=19))
    textures = pack / "textures"
    assert side_of(textures / f"{NAMES[0]}.dds") == 1024
    assert side_of(textures / f"{AIRPORT}.dds") == 8192
    assert (textures / f"{AIRPORT}.dds").stat().st_size == dds.file_size(8192)
    assert (pack / neutral_file(2)).exists() and (pack / neutral_file(-1)).exists()
    assert region_layout(pack) == (Zooms(14, 19), LAYOUT_FIXED)
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

    # up to ZL17: 8192 px, its levels kept, the copy of the sharpest above it, the rest neutral
    assert relayout(pack, Zooms(17, 18), log=quiet) == 2
    assert side_of(built) == 8192
    assert level_bytes(built, 2, -1) == levels_from(2, 0)[0]
    assert level_bytes(built, 1, -1) == double_bc1(levels_from(2, 0)[0], 1024)
    neutral = pack / neutral_file(-1)
    for level in (-1, 0):
        assert level_bytes(built, level, -1) == level_bytes(neutral, level, -1)
    assert other.read_bytes() == neutral.read_bytes()
    assert recorded(pack)["textures"] == {built.name: 2}
    assert not needs_relayout(pack, Zooms(17, 18))


def test_an_8192_texture_keeps_its_size_while_its_zoom_goes_that_far(tmp_path: Path) -> None:
    pack = region(tmp_path, Zooms(17, 18))
    path = sharp_file(pack, f"{NAMES[0]}.dds")
    record(pack, {path.name: -1})
    assert not needs_relayout(pack, Zooms(17, 18))
    assert needs_relayout(pack, Zooms(17, 19))  # the airports' textures: 8192 px too now

    # back to ZL16: 4096 px, its levels from 0 down the ones built at 8192 px
    assert relayout(pack, Zooms(16, 18), log=quiet) == 2
    assert side_of(path) == 4096
    for level in range(5):
        assert level_bytes(path, level, 0) == levels_from(level, -1)[0]
    assert recorded(pack)["textures"] == {path.name: 0}


def test_a_pack_whose_textures_grew(tmp_path: Path) -> None:
    """A pack of ZL17 from when textures started at 4096 px and grew to 8192 px in flight: laid
    out once more, every file at 8192 px, those built with the copy above their sharpest level;
    the grown ones as they are."""
    pack = region(tmp_path, Zooms(16, 18))
    grown = sharp_file(pack, f"{NAMES[0]}.dds")
    built = pack / "textures" / f"{NAMES[1]}.dds"
    write_sparse(built, 0, levels_from(0, 0), base=pack / neutral_file(0))
    record(pack, {grown.name: -1, built.name: 0}, grown=[grown.name])
    as_layout(pack, Zooms(17, 18), LAYOUT_GROWING)
    before = grown.read_bytes()

    assert needs_relayout(pack, Zooms(17, 18))
    assert relayout(pack, Zooms(17, 18), log=quiet) == 1
    assert grown.read_bytes() == before
    assert side_of(built) == 8192
    assert level_bytes(built, -1, -1) == double_bc1(levels_from(0, 0)[0], 4096)
    assert level_bytes(built, 0, -1) == levels_from(0, 0)[0]
    assert side_of(pack / "textures" / f"{AIRPORT}.dds") == 4096  # ZL18: the mesh's own
    assert "grown" not in recorded(pack)
    assert region_layout(pack) == (Zooms(17, 18), LAYOUT_FIXED)


def test_a_pack_of_8192_px_files_without_copies(tmp_path: Path) -> None:
    """The first layout: 8192 px files from the start, but none with the copy: each file built
    coarser than 8192 px gets it."""
    pack = region(tmp_path, Zooms(17, 18))
    path = pack / "textures" / f"{NAMES[0]}.dds"
    neutral = ensure_neutral(pack, -1)
    write_sparse(path, 1, levels_from(1, -1), base=neutral, file_top=-1)
    with path.open("r+b") as f:  # as the first layout wrote it: no copy
        offset, _ = dds.level_layout(-1)[1]
        f.seek(offset)
        f.write(level_bytes(neutral, 0, -1))
    record(pack, {path.name: 1})
    as_layout(pack, Zooms(17, 18), None)

    assert region_layout(pack) == (Zooms(17, 18), 1)
    assert relayout(pack, Zooms(17, 18), log=quiet) == 1
    assert level_bytes(path, 0, -1) == double_bc1(levels_from(1, -1)[0], 2048)
    assert recorded(pack)["textures"] == {path.name: 1}


def test_an_8192_texture_on_the_disk_and_made_coarser(tmp_path: Path) -> None:
    """Its disk, the copy counted; made coarser, it keeps its size, the copy above what it keeps."""
    assert texture_bytes(-1, -1) == 4096 + sum(n for _, n in dds.level_layout(-1))
    assert texture_bytes(-1, -1) > 4 * texture_bytes(0) - 1_000_000
    assert texture_bytes(0, -1) == texture_bytes(0) + dds.level_layout(-1)[0][1]
    assert texture_bytes(4, -1) == texture_bytes(4) + dds.level_layout(3)[0][1]
    assert texture_bytes(4, 0) == texture_bytes(4)
    pack = region(tmp_path, Zooms(17, 18))
    path = sharp_file(pack, f"{NAMES[0]}.dds")
    neutral = pack / neutral_file(-1)
    downgrade(path, 4, neutral, file_top=-1)
    assert side_of(path) == 8192
    assert level_bytes(path, 4, -1) == levels_from(4, -1)[0]
    assert level_bytes(path, 3, -1) == double_bc1(levels_from(4, -1)[0], 256)
    assert level_bytes(path, 0, -1) == level_bytes(neutral, 0, -1)
    downgrade(path, NEUTRAL_LEVEL, neutral, file_top=-1)
    assert path.read_bytes() == neutral.read_bytes()


def test_no_new_zoom_under_a_running_xplane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X-Plane keeps a texture at the size it first read: the pack is laid out again only once it
    has quit, a zoom below the mesh's or above alike."""
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
    for zoom in (14, 17):
        options.max_zoom = zoom
        with pytest.raises(session.RefusedError, match="zoom changed and X-Plane is running"):
            session.prepare(options, log=quiet)
        assert side_of(pack / "textures" / f"{NAMES[0]}.dds") == 4096  # untouched

    options.max_zoom = 16  # the zoom it has: it may run
    session.prepare(options, log=quiet)
    assert region_layout(pack) == (Zooms(16, 18), LAYOUT_FIXED)


# --- where the 8192 px level gets its photos ------------------------------------------------------


def row_streamer(tmp_path: Path, reach_km: float = 20.0, **kw) -> Streamer:
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
        **kw,
    )


def at(k: int, **kw) -> Fix:
    lat, lon = parse_texture_name(f"{ROW[k]}.dds").center
    return Fix(lat, lon, **kw)


def sharp(jobs: dict) -> list[int]:
    """Which textures of the row are to get their 8192 px photos."""
    return [k for k, name in enumerate(ROW) if jobs.get(f"{name}.dds", (0, 0))[1] == -1]


def test_what_a_start_waits_for(tmp_path: Path) -> None:
    """At a start: the 8192 px within 15 km (12 km, where X-Plane reads them, and a quarter), which
    X-Plane waits for (urgency <= 0); 4096 px farther, to the camera's reach."""
    s = row_streamer(tmp_path)
    jobs = s.plan(at(0), x_plane_there=False, starting=True)
    assert sharp(jobs) == [0, 1, 2]  # 0, 6.8 and 13.6 km
    for k in (0, 1, 2):
        assert jobs[f"{ROW[k]}.dds"][0] <= 0
    assert [jobs[f"{name}.dds"][1] for name in ROW[3:6]] == [0, 0, 0]  # 35 km with the reach
    assert jobs[f"{ROW[6]}.dds"][1] == 1
    s.cache.close()


def test_in_flight_all_around_and_ahead(tmp_path: Path) -> None:
    """In flight: within 15 km of the path ahead, the aircraft and the camera, whatever the way it
    flies, nothing guessed of where it turns; urgent once X-Plane reads them (12 km)."""
    s = row_streamer(tmp_path)
    east = s.plan(at(0, gs_ms=150.0, trk_deg=90.0), x_plane_there=False)
    assert sharp(east) == list(range(9))  # the path: 45 km ahead, 15 km around its end
    assert east[f"{ROW[1]}.dds"][0] <= 0  # 6.8 km: X-Plane reads it now
    assert east[f"{ROW[2]}.dds"][0] > 0  # 13.6 km: not yet
    west = s.plan(at(0, gs_ms=150.0, trk_deg=270.0), x_plane_there=False)
    assert sharp(west) == [0, 1, 2]  # behind: those around the aircraft still
    s.cache.close()


def test_the_8192_px_in_three_dimensions(tmp_path: Path) -> None:
    """X-Plane measures from the camera in three dimensions: high up, fewer textures are within
    12 km, none above 15 km; a camera down low counts at its own height."""
    s = row_streamer(tmp_path)
    north = {"gs_ms": 200.0, "trk_deg": 0.0}  # the row is east: only its distance counts
    assert sharp(s.plan(at(0, **north), x_plane_there=False)) == [0, 1, 2]
    assert sharp(s.plan(at(0, agl_m=10_700.0, **north), x_plane_there=False)) == [0, 1]  # FL350
    assert sharp(s.plan(at(0, agl_m=16_000.0, **north), x_plane_there=False)) == []
    low_camera = at(0, agl_m=10_700.0, **north)
    low_camera.cam_lat, low_camera.cam_lon = at(5).lat, at(5).lon
    low_camera.cam_agl_m = 300.0
    assert sharp(s.plan(low_camera, x_plane_there=False)) == [0, 1, 3, 4, 5, 6, 7]
    s.cache.close()


def test_the_rings_grow_with_xplanes_image() -> None:
    """Probe flight 2: X-Plane reads each level farther on a wider image and with a narrower field
    of view, as the ground a pixel covers shrinks; never nearer than measured, whatever the
    image."""
    from orthostudio.live.rings import MAX_SCALE, screen_scale

    assert screen_scale(2704, 65.0) == pytest.approx(1.0)  # where the rings were measured
    assert screen_scale(3840, 65.0) == pytest.approx(3840 / 2704)  # a 4K image: about 17 km
    assert screen_scale(2704, 33.5) == pytest.approx(2.117, abs=0.01)  # zoomed in: 20.5 km measured
    assert screen_scale(1579, 65.0) == 1.0  # a small window reads nearer: the rings as measured
    assert screen_scale(2704, 5.0) == MAX_SCALE
    assert screen_scale(None, None) == screen_scale(2704, 0.0) == 1.0  # not said


def test_a_wider_image_reads_sharper_farther(tmp_path: Path) -> None:
    s = row_streamer(tmp_path, reach_km=0.0)
    fix = at(0)
    assert s.plan(fix, x_plane_there=False)[f"{ROW[3]}.dds"][1] == 1  # 20.8 km: 2048 px
    s.set_screen(2704, 33.5)  # zoomed in: X-Plane reads the top level up to 25 km
    assert s.plan(fix, x_plane_there=False)[f"{ROW[3]}.dds"][1] == -1
    assert s.snapshot()["screen"] == {
        "width": 2704,
        "fov": 33.5,
        "scale": pytest.approx(2.117, abs=0.01),
        "km_4096": pytest.approx(25.4, abs=0.2),
    }
    s.save()  # remembered for the next start, before X-Plane says it again
    again = Streamer(s.pack_dir, s.cache, FakeDownloader, finest_quality=0, max_zoom=17, log=quiet)
    assert again.scale == pytest.approx(s.scale)
    s.cache.close()


def test_no_8192_px_below_maximum_texture_quality(tmp_path: Path) -> None:
    """At High, X-Plane reads no 8192 px level: none gets its photos, the copy stays there."""
    s = row_streamer(tmp_path)
    s.reads_8192.clear()  # as finest_quality=1 makes it
    assert sharp(s.plan(at(0), x_plane_there=False, starting=True)) == []
    s.cache.close()


def test_one_build_at_a_time_per_texture(tmp_path: Path) -> None:
    """Its file is the last build's: a sharper one waits for the one on its way."""
    s = row_streamer(tmp_path)
    name = f"{ROW[0]}.dds"
    s.queue = {name: (0.0, 2)}
    assert [n for n, _, _ in s.take_batch()] == [name]
    s.queue = {name: (-1.0, -1)}
    assert s.take_batch() == []
    s.in_flight.clear()
    assert [(n, lv) for n, lv, _ in s.take_batch()] == [(name, -1)]
    s.cache.close()


def test_an_8192_px_texture_made_coarser_keeps_its_size(tmp_path: Path) -> None:
    """Beyond the disk budget, far away: as any texture, its levels kept from 256 px down, the
    copy above them, in its 8192 px file still: X-Plane may have read its size."""
    s = row_streamer(tmp_path, disk_budget=1)
    name = f"{ROW[0]}.dds"
    path = sharp_file(s.pack_dir, name)
    s.built[name] = -1
    assert s.snapshot()["at_8192"] == 1
    assert s.snapshot()["disk_bytes"] == texture_bytes(-1, -1)
    assert s.evict() == 1
    assert side_of(path) == 8192
    assert level_bytes(path, 4, -1) == levels_from(4, -1)[0]
    assert s.built[name] == 4
    assert s.snapshot()["disk_bytes"] == texture_bytes(4, -1)
    s.save()
    assert "grown" not in recorded(s.pack_dir)
    s.cache.close()


@pytest.mark.skipif(Image is None, reason="needs numpy, Pillow and ispc_texcomp")
@pytest.mark.parametrize(
    ("max_zoom", "level", "side"), [(17, -1, 8192), (16, 0, 4096), (14, 2, 1024)]
)
def test_the_streamer_builds_to_the_file_size(
    tmp_path: Path, max_zoom: int, level: int, side: int
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
    )
    name = f"{NAMES[0]}.dds"
    lat, lon = parse_texture_name(name).center
    jobs = s.plan(Fix(lat, lon), x_plane_there=False, starting=True)
    assert jobs[name][1] == level  # the sharpest level X-Plane reads there
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
