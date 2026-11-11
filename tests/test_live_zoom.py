# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The zoom set, as AutoOrtho's max_zoom and max_zoom_near_airports: each texture's file at the size
of its zoom from the start (1024 px two zooms below the mesh's, 8192 px one above), an 8192 px file
built from a far level holding its copy above it, a new zoom laying the pack out again without
losing what was built, where the 8192 px level gets its photos, and its copy when they are late."""

from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

import pytest

from live_helpers import (
    NAMES,
    FakeDownloader,
    Image,
    disk_clones,
    make_source,
    make_squares,
    make_xplane,
    square_of,
    unbuilt,
)
from orthostudio.live import dds, session
from orthostudio.live.cache import ChunkCache
from orthostudio.live.evict import texture_bytes
from orthostudio.live.filesystem import folder_link
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
    set_zooms,
    setup_region,
)
from orthostudio.live.streamer import Fix, Streamer
from orthostudio.live.texture import (
    copy_up,
    double_bc1,
    downgrade,
    needs_copy,
    piece_keys,
    write_sparse,
)

AIRPORT = "93184_132608_BI18"  # a ZL18 texture inside NAMES[0]: the mesh's airports
# ZL16 textures in a row eastwards from NAMES[0], one every 6.8 km (45.4° N)
ROW = tuple(f"23296_{33152 + 16 * k}_BI16" for k in range(12))


def quiet(_: str) -> None:
    pass


def live_names_for(pack: Path):
    from live_helpers import live_names

    return live_names(pack)


def side_of(path: Path) -> int:
    """The width a DDS file's header gives."""
    return int.from_bytes(path.read_bytes()[16:20], "little")


def region(root: Path, zooms: Zooms | None = None, textures=(*NAMES, AIRPORT)) -> Path:
    """A pack set up, the files of its squares made as round the aircraft."""
    pack = root / "pack"
    source = make_source(root, tile=(45, 6), textures=textures)
    setup_region(source, pack, log=quiet, zooms=zooms)
    make_squares(pack)
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


def test_an_8192_file_built_from_a_far_level_holds_its_copy(tmp_path: Path) -> None:
    """X-Plane counts a file's levels from its top: it reads the far levels of an 8192 px file (at
    72 km, at load) before their photos come, and finds there the copy of the level below. Nearer,
    the photos are there first: no copy, each would take four times the disk of its level."""
    assert [needs_copy(level, -1) for level in range(-1, 5)] == [False] * 4 + [True] * 2
    assert not needs_copy(4, 0)  # a 4096 px file: X-Plane reads each level where it is built
    pack = region(tmp_path, Zooms(17, 18))
    neutral = ensure_neutral(pack, -1)
    path = pack / "textures" / f"{NAMES[0]}.dds"
    write_sparse(path, 3, levels_from(3, -1), base=neutral, file_top=-1, copy_above=True)
    assert side_of(path) == 8192
    assert level_bytes(path, 2, -1) == double_bc1(levels_from(3, -1)[0], 512)
    for level in (-1, 0, 1):  # above the copy: the neutral colour, never read that early
        assert level_bytes(path, level, -1) == unbuilt(level_bytes(neutral, level, -1), tmp_path)
    for level in (3, 4):
        assert level_bytes(path, level, -1) == levels_from(level, -1)[0]

    near = pack / "textures" / f"{NAMES[1]}.dds"
    write_sparse(near, 0, levels_from(0, -1), base=neutral, file_top=-1)
    assert level_bytes(near, -1, -1) == unbuilt(level_bytes(neutral, -1, -1), tmp_path)
    copy_up(near, neutral, -1)  # its photos late: the 4096 px level copied up, nothing else
    assert level_bytes(near, -1, -1) == double_bc1(levels_from(0, -1)[0], 4096)
    for level in range(5):
        assert level_bytes(near, level, -1) == levels_from(level, -1)[0]


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

    # up to ZL17: 8192 px, its levels kept, the sharper ones neutral
    assert relayout(pack, Zooms(17, 18), log=quiet) == 2
    assert side_of(built) == 8192
    assert level_bytes(built, 2, -1) == levels_from(2, 0)[0]
    neutral = pack / neutral_file(-1)
    for level in (-1, 0, 1):
        assert level_bytes(built, level, -1) == unbuilt(level_bytes(neutral, level, -1), tmp_path)
    assert other.read_bytes() == neutral.read_bytes()
    assert recorded(pack)["textures"] == {built.name: 2}
    assert not needs_relayout(pack, Zooms(17, 18))


def test_a_new_zoom_shared_by_several_processes_lays_out_the_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The textures shared between processes (the start's encoders, the user's « c'est long » of
    2026-10-10): every file and every level as one process alone lays them out, small chunks so
    that several processes have some."""
    from orthostudio.live import region as region_module

    monkeypatch.setattr(region_module, "RELAYOUT_CHUNK", 1)
    files = {}
    for workers in (1, 3):
        pack = region(tmp_path / str(workers))
        built = pack / "textures" / f"{NAMES[0]}.dds"
        write_sparse(built, 0, levels_from(0, 0), base=pack / neutral_file(0))
        record(pack, {built.name: 0})
        lines: list[str] = []
        assert relayout(pack, Zooms(17, 19), log=lines.append, workers=workers) == 3
        assert recorded(pack)["textures"] == {built.name: 0}
        assert region_zooms(pack) == Zooms(17, 19)
        files[workers] = {
            p.name: p.read_bytes() for p in (pack / "textures").iterdir() if p.suffix == ".dds"
        }
        assert any("by 3 processes" in line for line in lines) == (workers == 3)
    assert files[1] == files[3]
    assert side_of(tmp_path / "3" / "pack" / "textures" / f"{NAMES[0]}.dds") == 8192


def _all_squares(names) -> list[tuple[int, int]]:
    from orthostudio.live.evict import tiles_of

    return sorted(
        {
            square
            for entries in names.by_tile.values()
            for entry in entries
            for square in tiles_of(parse_texture_name(entry[0]))
        }
    )


def test_a_new_zoom_touches_no_texture_at_the_start(tmp_path: Path) -> None:
    """A new zoom in an instant, whatever the pilot has flown (the user: « si j'ai 100000 textures
    ... il va appliquer aux 100000 », 2026-10-10): no texture written, the squares made before no
    longer readable by X-Plane (made again as it comes), the levels recorded those the files
    hold."""
    from orthostudio.live.region import remove_aside

    pack = region(tmp_path)
    built = pack / "textures" / f"{NAMES[0]}.dds"
    write_sparse(built, 0, levels_from(0, 0), base=pack / neutral_file(0))
    record(pack, {built.name: 0})
    make_squares(pack)
    before = {p.name: p.read_bytes() for p in (pack / "textures").iterdir()}
    assert any((pack / "terrain").iterdir())
    lines: list[str] = []
    set_zooms(pack, Zooms(17, 19), lines.append)
    assert {p.name: p.read_bytes() for p in (pack / "textures").iterdir()} == before
    assert not any((pack / "terrain").iterdir())  # X-Plane reads none of them at its old size
    assert recorded(pack)["textures"] == {built.name: 0}
    assert region_layout(pack) == (Zooms(17, 19), LAYOUT_FIXED)
    assert not needs_relayout(pack, Zooms(17, 19))
    assert any("as its square's files are made" in line for line in lines)
    remove_aside(pack)  # the thread's work, done here
    assert not list(pack.glob(".terrain-old-*"))
    # down again: an 8192 px level recorded stays while the file holds it, until its square
    record(pack, {built.name: -1})
    set_zooms(pack, Zooms(16, 18), quiet)
    assert recorded(pack)["textures"] == {built.name: -1}


def test_a_zoom_set_back_before_a_square_is_made_again_builds_nothing_again(
    tmp_path: Path,
) -> None:
    """17/19, 16/18, then 17/19 again before the aircraft came near a texture: its 8192 px level,
    still in its file, is neither forgotten nor built again (29 textures downloaded and built a
    second time at a start round Toulouse, 2026-10-10); set to 4096 px only once its square is
    made at 16/18, its level recorded then. The map shows what X-Plane will show meanwhile."""
    from orthostudio.live.mapdata import rows_of
    from orthostudio.live.region import file_tops

    pack = region(tmp_path, Zooms(17, 19))
    path = sharp_file(pack, f"{NAMES[0]}.dds")
    record(pack, {path.name: -1})

    def made(zooms: Zooms) -> dict[str, int]:
        set_zooms(pack, zooms, quiet)
        names = live_names_for(pack)
        refitted: dict[str, int] = {}
        names.level_of = {path.name: recorded(pack)["textures"][path.name]}.get
        names.refit = refitted.__setitem__
        names.building = lambda _: False
        for each in _all_squares(names):
            names.make(each)
        return refitted

    set_zooms(pack, Zooms(16, 18), quiet)  # a start at 16/18, gone before the aircraft came
    assert recorded(pack)["textures"] == {path.name: -1} and side_of(path) == 8192
    [(*_, photos, _)] = rows_of(recorded(pack)["textures"], file_tops(pack))
    assert photos == 16  # X-Plane will show its 4096 px level, no sharper
    assert made(Zooms(17, 19)) == {}  # back at 17/19: nothing to set, nothing to build
    assert side_of(path) == 8192 and level_bytes(path, -1, -1) == levels_from(-1, -1)[0]
    assert made(Zooms(16, 18)) == {path.name: 0}  # its square made at 16/18: 4096 px now
    assert side_of(path) == 4096


def test_a_square_made_after_a_new_zoom_has_its_textures_at_its_size_first(
    tmp_path: Path,
) -> None:
    """Each texture set to the new zoom's size as its square is made, its levels kept, nothing
    downloaded; its terrain files only then, so that X-Plane never reads one at its old size; a
    texture being built at its old size makes the square wait, a moment."""
    from orthostudio.live.names import SquareWaitError
    from orthostudio.live.texture import resize

    pack = region(tmp_path)
    built = pack / "textures" / f"{NAMES[0]}.dds"
    write_sparse(built, 0, levels_from(0, 0), base=pack / neutral_file(0))
    record(pack, {built.name: 0})
    make_squares(pack)
    set_zooms(pack, Zooms(17, 19), quiet)
    names = live_names_for(pack)
    levels = {built.name: 0}
    refitted: dict[str, int] = {}
    names.level_of = levels.get
    names.refit = refitted.__setitem__
    square = square_of(NAMES[0])
    # being built: the square waits, nothing of it readable yet
    names.building = lambda name: name == built.name
    with pytest.raises(SquareWaitError):
        names.make(square)
    assert not any((pack / "terrain").iterdir())
    assert side_of(built) == 4096  # the builder's, not touched
    # built: the square is made
    resize(built, 0, 0, -1, ensure_neutral(pack, -1))
    names.building = lambda name: False
    for each in _all_squares(names):
        names.make(each)
    assert side_of(built) == 8192
    assert level_bytes(built, 0, -1) == levels_from(0, 0)[0]
    neutral = pack / neutral_file(-1)
    for name in NAMES[1:]:
        assert (pack / "textures" / f"{name}.dds").read_bytes() == neutral.read_bytes()
    assert side_of(pack / "textures" / f"{AIRPORT}.dds") == 8192  # ZL19 round airports
    assert any((pack / "terrain").iterdir())
    # one not being built is set by the square's making, its level said to the builder
    write_sparse(built, 0, levels_from(0, 0), base=pack / neutral_file(0))  # back to 4096 px
    names.make(square)
    assert side_of(built) == 8192 and refitted == {built.name: 0}


def test_a_square_waits_for_a_texture_the_builder_took_not_for_one_queued(
    tmp_path: Path,
) -> None:
    """A texture only queued is set to the new zoom's size with its square at once; one the
    builder took makes its square wait until it is written. Waiting for the queued ones too held
    a start's square past GIVE_UP_S and X-Plane went on without it (three starts, 2026-10-10)."""
    s = row_streamer(tmp_path)
    assert s.names is not None
    name = f"{ROW[0]}.dds"
    s.queue[name] = (1.0, -1)
    assert not s.names.building(name)
    s.in_flight[name] = s.queue.pop(name)
    assert s.names.building(name)


def test_a_texture_is_set_to_a_new_zoom_even_before_its_neutral_texture_is_made(
    tmp_path: Path,
) -> None:
    """A built texture set to a size no texture had yet: its neutral texture made first. Its
    absence made the setting fail without a word, and the square readable with the texture at
    its old size (found by the test of several processes, 2026-10-10)."""
    pack = region(tmp_path)
    built = pack / "textures" / f"{NAMES[0]}.dds"
    write_sparse(built, 0, levels_from(0, 0), base=pack / neutral_file(0))
    record(pack, {built.name: 0})
    make_squares(pack)
    set_zooms(pack, Zooms(17, 19), quiet)
    from orthostudio.live.names import run_ops

    (pack / neutral_file(-1)).unlink(missing_ok=True)
    # the texture alone, as a process given a share of a square's textures may have it
    result = run_ops(str(pack), [("fit", -1, str(built), 0)])
    assert result["fitted"] == {built.name: 0} and not result["errors"]
    assert side_of(built) == 8192
    assert level_bytes(built, 0, -1) == levels_from(0, 0)[0]


def test_a_texture_not_yet_at_the_zoom_s_size_is_made_coarser_or_copied_up_as_it_is(
    tmp_path: Path,
) -> None:
    """Made coarser for the disk, or its 8192 px level copied up, a texture a new zoom has not
    reached yet is read at the size its header gives, and written at the zoom's."""
    pack = region(tmp_path)
    path = pack / "textures" / f"{NAMES[0]}.dds"
    write_sparse(path, 0, levels_from(0, 0), base=pack / neutral_file(0))  # 4096 px
    downgrade(path, 2, ensure_neutral(pack, -1), file_top=-1)  # the zoom's 8192 px file
    assert side_of(path) == 8192
    assert level_bytes(path, 2, -1) == levels_from(2, 0)[0]
    write_sparse(path, 0, levels_from(0, 0), base=pack / neutral_file(0))
    copy_up(path, ensure_neutral(pack, -1), -1)
    assert side_of(path) == 8192
    assert level_bytes(path, 0, -1) == levels_from(0, 0)[0]


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
    out once more, every file at 8192 px, its levels kept; the grown ones as they are."""
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
    assert level_bytes(built, -1, -1) == unbuilt(
        level_bytes(pack / neutral_file(-1), -1, -1), tmp_path
    )
    assert level_bytes(built, 0, -1) == levels_from(0, 0)[0]
    assert side_of(pack / "textures" / f"{AIRPORT}.dds") == 4096  # ZL18: the mesh's own
    assert "grown" not in recorded(pack)
    assert region_layout(pack) == (Zooms(17, 18), LAYOUT_FIXED)


def test_a_pack_of_8192_px_files_without_copies(tmp_path: Path) -> None:
    """The first layout: 8192 px files from the start, but none with the copy: each file built from
    a far level gets it, the others are left as they are."""
    pack = region(tmp_path, Zooms(17, 18))
    far, near = (pack / "textures" / f"{name}.dds" for name in NAMES)
    neutral = ensure_neutral(pack, -1)
    write_sparse(far, 3, levels_from(3, -1), base=neutral, file_top=-1)  # as the first layout
    write_sparse(near, 1, levels_from(1, -1), base=neutral, file_top=-1)
    record(pack, {far.name: 3, near.name: 1})
    as_layout(pack, Zooms(17, 18), None)
    before = near.read_bytes()

    assert region_layout(pack) == (Zooms(17, 18), 1)
    assert relayout(pack, Zooms(17, 18), log=quiet) == 1
    assert level_bytes(far, 2, -1) == double_bc1(levels_from(3, -1)[0], 512)
    assert near.read_bytes() == before
    assert recorded(pack)["textures"] == {far.name: 3, near.name: 1}


def test_an_8192_texture_on_the_disk_and_made_coarser(tmp_path: Path) -> None:
    """Its disk, the copy counted; made coarser, it keeps its size, the copy above what it keeps.
    Counted in the disk's steps (evict.ALLOC_BLOCK): equal give or take two of them."""
    from orthostudio.live.evict import ALLOC_BLOCK

    def about(a: int, b: int) -> bool:
        return abs(a - b) <= 2 * ALLOC_BLOCK

    assert about(texture_bytes(-1, -1), 4096 + sum(n for _, n in dds.level_layout(-1)))
    assert texture_bytes(-1, -1) > 4 * texture_bytes(0) - 1_000_000
    assert about(texture_bytes(0, -1), texture_bytes(0))  # no copy: its photos come first
    assert about(texture_bytes(3, -1), texture_bytes(3) + dds.level_layout(2)[0][1])
    assert about(texture_bytes(4, -1), texture_bytes(4) + dds.level_layout(3)[0][1])
    assert texture_bytes(4, 0) == texture_bytes(4)
    pack = region(tmp_path, Zooms(17, 18))
    path = sharp_file(pack, f"{NAMES[0]}.dds")
    neutral = pack / neutral_file(-1)
    downgrade(path, 4, neutral, file_top=-1)
    assert side_of(path) == 8192
    assert level_bytes(path, 4, -1) == levels_from(4, -1)[0]
    assert level_bytes(path, 3, -1) == double_bc1(levels_from(4, -1)[0], 256)
    assert level_bytes(path, 0, -1) == unbuilt(level_bytes(neutral, 0, -1), tmp_path)
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
    folder_link(pack, xp / "Custom Scenery" / "z_osxp_live")  # as Live links it here
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
    """At a start: the 8192 px within 13.6 km, where X-Plane reads them, no margin while it loads
    (2026-10-20), which X-Plane waits for (urgency <= 0); farther, each level of these 8192 px
    files where X-Plane reads it, a ring before a 4096 px file's, widened by the camera's reach
    even in the cockpit."""
    s = row_streamer(tmp_path)
    jobs = s.plan(at(0), x_plane_there=False, starting=True)
    assert sharp(jobs) == [0, 1, 2]  # 0, 6.8 and 13.57 km, within 13.6; 20.4 km beyond
    for k in (0, 1, 2):
        assert jobs[f"{ROW[k]}.dds"][0] <= 0
    # 4096 px out to 40.8 km, 2048 px from 47.6 km
    assert [jobs[f"{name}.dds"][1] for name in ROW[3:8]] == [0, 0, 0, 0, 1]
    s.cache.close()


def test_a_start_plans_x_plane_s_4096_px_ring_with_the_margin(tmp_path: Path) -> None:
    """X-Plane's 4096 px ring lies within a few hundred metres of 14 km, past the rings' 13.6: a
    texture 13.9 km from a start near LAX was read at 4096 px before Live built it, black at night,
    in every flight (2026-11-11). A start plans it with the flight's margin, and waits for it."""
    s = row_streamer(tmp_path)
    lat, lon = parse_texture_name(f"{ROW[0]}.dds").center
    west = lon - 0.4 / (111.195 * math.cos(math.radians(lat)))  # the third one 13.97 km away
    jobs = s.plan(Fix(lat, west), x_plane_there=False, starting=True)
    assert sharp(jobs) == [0, 1, 2]
    assert jobs[f"{ROW[2]}.dds"][0] <= 0  # X-Plane waits for it
    s.cache.close()


def test_in_flight_all_around_and_ahead(tmp_path: Path) -> None:
    """In flight: within 15 km of the path ahead, the aircraft and the camera, whatever the way it
    flies, nothing guessed of where it turns; urgent once X-Plane reads them (13.6 km). The path is
    two minutes long: in a turn, five minutes swept 38 km of it all around the aircraft."""
    s = row_streamer(tmp_path)
    east = s.plan(at(0, gs_ms=150.0, trk_deg=90.0), x_plane_there=False)
    assert sharp(east) == list(range(5))  # the path: 13.5 km ahead, 15.1 km around its end
    assert east[f"{ROW[1]}.dds"][0] <= 0  # 6.8 km: X-Plane reads it now
    assert east[f"{ROW[2]}.dds"][0] <= 0  # 13.57 km: within 13.6, read now too
    assert east[f"{ROW[3]}.dds"][0] > 0  # 20.4 km: not yet
    west = s.plan(at(0, gs_ms=150.0, trk_deg=270.0), x_plane_there=False)
    assert sharp(west) == [0, 1, 2]  # behind: those around the aircraft still
    s.cache.close()


def test_the_8192_px_in_three_dimensions(tmp_path: Path) -> None:
    """X-Plane measures from the aircraft in three dimensions: high up, fewer textures are within
    12 km, none above 15 km; a camera down low changes nothing. From 18,500 ft none gets its photos
    any more (test_no_8192_px_photos_at_cruise_height)."""
    s = row_streamer(tmp_path)
    north = {"gs_ms": 200.0, "trk_deg": 0.0}  # the row is east: only its distance counts
    assert sharp(s.plan(at(0, **north), x_plane_there=False)) == [0, 1, 2]
    assert sharp(s.plan(at(0, agl_m=5_000.0, **north), x_plane_there=False)) == [
        0,
        1,
        2,
    ]  # 16,400 ft
    assert sharp(s.plan(at(0, agl_m=10_700.0, **north), x_plane_there=False)) == []  # FL350
    assert sharp(s.plan(at(0, agl_m=16_000.0, **north), x_plane_there=False)) == []
    low_camera = at(0, agl_m=10_700.0, **north)  # X-Plane takes nothing more for a camera
    low_camera.cam_lat, low_camera.cam_lon = at(5).lat, at(5).lon
    low_camera.cam_agl_m = 300.0
    assert sharp(s.plan(low_camera, x_plane_there=False)) == []
    s.cache.close()


def test_no_8192_px_photos_at_cruise_height(tmp_path: Path) -> None:
    """Above 18,500 ft from the ground no 8192 px level gets its photos: the screen cannot show
    them from there (the user's rule of 2026-10-01). Those X-Plane is about to read get the copy of
    their 4096 px level, never the neutral colour; their photos again below 17,500 ft, and at a
    start, where X-Plane gives no height; not for a camera down low, X-Plane taking the levels for
    the aircraft (docs/specs/live/levels-test.md)."""
    s = row_streamer(tmp_path)
    lines: list[str] = []
    s.log = lines.append
    for k in (0, 1, 2):
        s.built[f"{ROW[k]}.dds"] = 0
    west = {"gs_ms": 150.0, "trk_deg": 270.0}  # away from the row: only its distance counts
    assert sharp(s.plan(at(0, agl_m=3_000.0, **west), x_plane_there=False)) == [0, 1, 2]
    assert not s.cruising and lines == []
    s.to_copy.clear()
    assert sharp(s.plan(at(0, agl_m=9_144.0, **west), x_plane_there=False)) == []  # FL300
    assert s.cruising and len(lines) == 1 and lines[0].startswith("  above 18,500 ft")
    assert s.to_copy == {f"{ROW[0]}.dds", f"{ROW[1]}.dds"}  # 9.1 and 11.4 km: X-Plane reads them
    assert sharp(s.plan(at(0, agl_m=5_500.0, **west), x_plane_there=False)) == []  # 18,000 ft
    assert s.cruising and len(lines) == 1
    assert {0, 1} <= set(sharp(s.plan(at(0, agl_m=5_000.0, **west), x_plane_there=False)))
    assert not s.cruising and lines[-1].startswith("  below 17,500 ft")
    s.plan(at(0, agl_m=9_144.0, **west), x_plane_there=False)
    low_camera = at(0, agl_m=9_144.0, **west)
    low_camera.cam_lat, low_camera.cam_lon = at(4).lat, at(4).lon
    low_camera.cam_agl_m = 300.0
    assert sharp(s.plan(low_camera, x_plane_there=False)) == []  # the aircraft's height counts
    assert s.cruising
    s.plan(at(0, agl_m=9_144.0), x_plane_there=False, starting=True)
    assert not s.cruising  # a start: planned as on the ground
    s.cache.close()


def test_a_descent_gets_its_8192_px_photos_ahead_of_time(tmp_path: Path) -> None:
    """Descending, the 8192 px photos come again three minutes before the aircraft is 13,000 ft
    from the ground, where they begin to show, higher the faster it descends (the user's wish,
    2026-10-01: after a switch at 17,500 ft, 17 were late, the last near 15,000 ft); level or
    descending slowly, at 17,500 ft as before."""
    s = row_streamer(tmp_path)
    lines: list[str] = []
    s.log = lines.append
    ft, fpm = 0.3048, 0.3048 / 60  # metres in a foot, metres a second in a foot a minute
    west = {"gs_ms": 150.0, "trk_deg": 270.0}

    def high(feet: float, rate: float = 0.0) -> bool:
        s.plan(at(0, agl_m=feet * ft, vs_ms=rate * fpm, **west), x_plane_there=False)
        return s.cruising

    assert high(30_000)
    assert high(22_000, -2_500)  # 216 s from 13,000 ft
    assert not high(20_000, -2_500)  # 168 s: the photos again
    assert lines[-1].startswith("  descending at 2500 ft a minute, 20000 ft from the ground")
    assert high(24_000)  # level again above 18,500 ft: no 8192 px photos up there
    assert high(25_500, -4_000)  # 188 s
    assert not high(24_500, -4_000)  # 173 s
    assert high(30_000)
    assert high(18_000, -1_000)  # 300 s: a slow descent, the rule as before
    assert not high(17_000, -1_000)
    assert lines[-1].startswith("  below 17,500 ft")
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
    assert s.plan(fix, x_plane_there=False)[f"{ROW[4]}.dds"][1] == 1  # 27 km: 2048 px
    s.set_screen(2704, 33.5)  # zoomed in: X-Plane reads the top level up to 25 km
    assert s.plan(fix, x_plane_there=False)[f"{ROW[3]}.dds"][1] == -1
    assert s.snapshot()["screen"] == {
        "width": 2704,
        "fov": 33.5,
        "scale": pytest.approx(2.117, abs=0.01),
        "km_4096": pytest.approx(28.8, abs=0.2),
    }
    s.save()  # remembered for the next start, before X-Plane says it again
    again = Streamer(s.pack_dir, s.cache, FakeDownloader, finest_quality=0, max_zoom=17, log=quiet)
    assert again.scale == pytest.approx(s.scale)
    s.cache.close()


def test_no_8192_px_below_maximum_texture_quality(tmp_path: Path) -> None:
    """At High, X-Plane reads no 8192 px level: none gets its photos."""
    s = row_streamer(tmp_path)
    s.reads_8192.clear()  # as finest_quality=1 makes it
    assert sharp(s.plan(at(0), x_plane_there=False, starting=True)) == []
    s.cache.close()


def test_late_8192_px_photos_get_the_copy_first(tmp_path: Path) -> None:
    """In flight, an 8192 px level still without its photos as X-Plane is about to read it (14 km)
    is marked for the copy of its 4096 px level; not at a start (X-Plane waits for the photos),
    nor where the copy is already, nor farther."""
    s = row_streamer(tmp_path)
    for k in (1, 2):
        s.built[f"{ROW[k]}.dds"] = 0
    s.plan(at(0), x_plane_there=False, starting=True)
    assert s.to_copy == set()
    s.plan(at(0, gs_ms=150.0, trk_deg=270.0), x_plane_there=False)
    assert s.to_copy == {f"{ROW[1]}.dds", f"{ROW[2]}.dds"}  # 6.8 and 13.57 km; 20.4 is not late
    s.to_copy.clear()
    s.copied_up |= {f"{ROW[1]}.dds", f"{ROW[2]}.dds"}
    s.plan(at(0, gs_ms=150.0, trk_deg=270.0), x_plane_there=False)
    assert s.to_copy == set()
    s.cache.close()


def block_names() -> tuple[list[str], str]:
    """An airport's block of ZL18 textures, 4 x 4 near 46.1° N 5.5° E (each 1.7 km across), and
    one more ZL18 texture 8.5 km east of its first, two columns away: another airfield's."""
    y0, x0 = 93136, 135040  # the first one's centre: 46.11° N, 5.46° E
    block = [f"{y0 + 16 * r}_{x0 + 16 * c}_BI18" for r in range(4) for c in range(4)]
    return block, f"{y0 + 16}_{x0 + 16 * 6}_BI18"


def test_a_start_waits_for_the_airport_s_whole_block(tmp_path: Path) -> None:
    """At a start, every ZL18 texture of the airport's block gets its 4096 px level before X-Plane
    goes on (urgency <= 0), those farther than X-Plane reads it from the start too: a camera
    taken over the airport finds them built. Another airfield's, not touching the block, does not;
    nor the block's far corner once X-Plane flies (2026-10-20, LSGG)."""
    block, other = block_names()
    pack = region(tmp_path, textures=(*block, other))
    cache = ChunkCache(tmp_path / "c.sqlite")
    s = Streamer(pack, cache, FakeDownloader, finest_quality=0, log=quiet)
    start = Fix(*parse_texture_name(f"{block[5]}.dds").center)  # the block's second row and column
    corner = f"{block[15]}.dds"  # 4.8 km from the start: X-Plane reads its 2048 px level there
    jobs = s.plan(start, x_plane_there=False, starting=True)
    for name in block:
        assert jobs[f"{name}.dds"][1] == 0 and jobs[f"{name}.dds"][0] <= 0, name
    assert jobs[f"{other}.dds"][1] > 0
    standing = s.plan(Fix(start.lat, start.lon), x_plane_there=False)
    assert standing[corner][1] == 1
    cache.close()


class NoInternet:
    """A downloader that never answers."""

    downloaded = bytes = failed = 0

    def __init__(self, on_piece) -> None:
        pass

    def submit(self, key, priority) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.mark.skipif(Image is None, reason="needs numpy, Pillow and ispc_texcomp")
def test_the_builder_copies_up_while_the_photos_do_not_come(tmp_path: Path) -> None:
    """No internet: the 8192 px level of a texture X-Plane is about to read gets the copy of its
    4096 px level, the ortho as at ZL16, never the neutral colour; its photos stay awaited."""
    pack = region(tmp_path, Zooms(17, 18), textures=ROW)
    name = f"{ROW[1]}.dds"
    path = pack / "textures" / name
    write_sparse(path, 0, levels_from(0, -1), base=ensure_neutral(pack, -1), file_top=-1)
    cache = ChunkCache(tmp_path / "c.sqlite")
    s = Streamer(pack, cache, NoInternet, finest_quality=0, max_zoom=17, workers=2, log=quiet)
    s.built[name] = 0
    s.plan(at(0, gs_ms=150.0, trk_deg=270.0), x_plane_there=False)
    s.start()
    deadline = time.time() + 60
    while name not in s.copied_up and time.time() < deadline:
        time.sleep(0.1)
    s.close()
    cache.close()
    assert name in s.copied_up and s.built[name] == 0
    assert level_bytes(path, -1, -1) == double_bc1(levels_from(0, -1)[0], 4096)
    assert level_bytes(path, 0, -1) == levels_from(0, -1)[0]


def test_a_sharper_build_writes_only_its_new_levels(tmp_path: Path) -> None:
    """Built to 4096 px, then to 8192: the 8192 px level written, those below kept as the file had
    them, built from their own photos, not written again; a build no sharper writes them all. A
    far level's copy above is written with it, over the one the file had. On a disk that clones
    nothing (NTFS, ext4) the file is written whole anyway: its coarser levels are the new
    build's (tests/test_live_filesystem.py)."""
    pack = region(tmp_path, Zooms(17, 18), textures=ROW)
    clones = disk_clones(tmp_path)
    neutral = ensure_neutral(pack, -1)
    path = pack / "textures" / f"{ROW[0]}.dds"
    write_sparse(path, 0, levels_from(0, -1), base=neutral, file_top=-1)
    new = [bytes([200 + k]) * len(b) for k, b in enumerate(levels_from(-1, -1))]
    write_sparse(path, -1, new, base=neutral, file_top=-1, keep_from=0)
    assert level_bytes(path, -1, -1) == new[0]
    assert level_bytes(path, 0, -1) == (levels_from(0, -1)[0] if clones else new[1])
    assert level_bytes(path, 4, -1) == (levels_from(4, -1)[0] if clones else new[5])
    write_sparse(path, -1, new, base=neutral, file_top=-1, keep_from=-1)  # no sharper: all
    assert level_bytes(path, 0, -1) == new[1]

    far = pack / "textures" / f"{ROW[1]}.dds"
    write_sparse(far, 4, levels_from(4, -1), base=neutral, file_top=-1, copy_above=True)
    new3 = [bytes([150 + k]) * len(b) for k, b in enumerate(levels_from(3, -1))]
    write_sparse(far, 3, new3, base=neutral, file_top=-1, copy_above=True, keep_from=4)
    assert level_bytes(far, 3, -1) == new3[0]
    assert level_bytes(far, 2, -1) == double_bc1(new3[0], dds.size_of(3))  # its copy
    assert level_bytes(far, 4, -1) == (levels_from(4, -1)[0] if clones else new3[1])  # kept
    assert level_bytes(far, 1, -1) == unbuilt(level_bytes(neutral, 1, -1), tmp_path)


def test_a_copy_up_writes_only_the_copy(tmp_path: Path) -> None:
    pack = region(tmp_path, Zooms(17, 18), textures=ROW)
    neutral = ensure_neutral(pack, -1)
    path = pack / "textures" / f"{ROW[0]}.dds"
    write_sparse(path, 0, levels_from(0, -1), base=neutral, file_top=-1)
    before = path.read_bytes()
    copy_up(path, neutral, -1)
    offset = dds.level_layout(-1)[1][0]
    assert path.read_bytes()[offset:] == before[offset:]
    assert level_bytes(path, -1, -1) == double_bc1(levels_from(0, -1)[0], 4096)


@pytest.mark.skipif(Image is None, reason="needs numpy, Pillow and ispc_texcomp")
@pytest.mark.parametrize(
    ("max_zoom", "level", "kept"), [(17, -1, False), (16, 0, False), (14, 2, True)]
)
def test_the_photos_of_the_sharp_levels_are_not_kept(
    tmp_path: Path, max_zoom: int, level: int, kept: bool
) -> None:
    """The photos of a texture built to 4096 px or sharper stay out of the imagery cache: the
    texture holds them. Those of coarser levels are kept, for the next flights."""
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
    # in flight, with the rings' margin: at a start, without it, a ZL18 texture near the airport
    # gets its 2048 px level from the same ZL17 photos, which that level keeps
    s.plan(Fix(lat, lon, gs_ms=150.0, trk_deg=90.0), x_plane_there=False)
    s.start()
    deadline = time.time() + 120
    while s.built.get(name) != level and time.time() < deadline:
        time.sleep(0.1)
    s.close()
    keys = piece_keys(parse_texture_name(name), level)
    held = cache.get_many(s.provider, keys)
    cache.close()
    assert s.built[name] == level
    assert len(held) == (len(keys) if kept else 0)


def test_live_pauses_while_x_plane_loads_scenery(tmp_path: Path) -> None:
    """In flight, X-Plane's scenery load flag up: Live pauses, and YIELD_AFTER_S after; for
    YIELD_MAX_S of a load at most; never at a start, where X-Plane waits for what Live builds."""
    from orthostudio.live import streamer as st

    s = row_streamer(tmp_path)
    s.x_plane_loads(True, now=100.0)
    assert s.yield_until == 0.0  # not flying: a start, X-Plane waits on its loading screen
    s.flying = True
    s.x_plane_loads(True, now=100.0)
    assert s.yield_until == 100.0 + st.YIELD_AFTER_S
    lines = []
    s.log = lines.append
    s.x_plane_loads(True, now=100.0 + st.YIELD_MAX_S + 1)  # a load this long: no more paused
    s.x_plane_loads(True, now=100.0 + st.YIELD_MAX_S + 2)
    assert s.yield_until == 100.0 + st.YIELD_AFTER_S
    s.x_plane_loads(False, now=100.0 + st.YIELD_MAX_S + 10)
    took = st.YIELD_MAX_S + 10
    assert lines == [  # the load's length, and what Live paused of it (Norway, 2026-09-30)
        f"  X-Plane still loads scenery {st.YIELD_MAX_S:.0f} s on: Live works again until its "
        "window moves",
        f"  X-Plane loaded scenery in {took:.0f} s: Live paused at most "
        f"{st.YIELD_MAX_S:.0f} s from each move of its window",
    ]
    s.x_plane_loads(True, now=500.0)  # a new load: paused again
    assert s.yield_until == 500.0 + st.YIELD_AFTER_S
    s.x_plane_loads(False, now=507.0)
    assert lines[-1] == "  X-Plane loaded scenery in 7 s: Live paused meanwhile"
    s.cache.close()


def test_a_test_flight_freezes_live_once_the_aircraft_flies(tmp_path: Path, monkeypatch) -> None:
    """fly.py --freeze-in-flight (2026-10-01): Live builds nothing once the aircraft flies, not
    even what X-Plane shows; a start's textures still built, X-Plane waiting for them."""
    from orthostudio.live import streamer as st

    monkeypatch.setattr(st, "FROZEN_IN_FLIGHT", True)
    s = row_streamer(tmp_path)
    assert not s.yielding() and s.may_encode(-1.0, 0)  # a start
    s.flying = True
    assert s.yielding() and not s.may_encode(-1.0, 0)
    s.flying = False  # X-Plane gone, or a new start
    assert not s.yielding()
    s.cache.close()


def test_live_stops_while_x_plane_s_workers_are_all_busy(tmp_path: Path) -> None:
    """X-Plane's background workers all but one running on average over POOL_WINDOW readings, in
    flight: Live stops all but what X-Plane shows, downloads included, until two and a half of
    them are idle on average again, POOL_HOLD_S at least after (a hot Mac, 2026-10-01: with Live
    at work they were all busy 61 % of a flight, its tiles minutes late)."""
    from orthostudio.live import streamer as st

    s = row_streamer(tmp_path)
    lines: list[str] = []
    s.log = lines.append
    for t in range(st.POOL_WINDOW + 2):
        s.x_plane_pool((7, 7), now=float(t))
    assert not s.x_plane_busy()  # not flying: a start, X-Plane waits for what Live builds
    s.flying = True
    t = 100.0
    for running in (7, 6, 5, 6, 6):  # 6 of 7 on average over five readings
        assert not s.x_plane_busy()
        s.x_plane_pool((running, 7), now=t)
        t += 1
    assert s.x_plane_busy()
    assert lines == ["  X-Plane's background workers all busy: Live stops but what it shows"]
    assert s.holds(5.0) and not s.holds(-1.0)  # its downloads: only what X-Plane shows
    assert s.may_encode(-1.0, 0) and not s.may_encode(5.0, 2)
    for _ in range(5):  # room again at once: X-Plane left POOL_HOLD_S at least to catch up
        s.x_plane_pool((3, 7), now=t)
        t += 1
    assert s.x_plane_busy()
    while s.x_plane_busy() and t < 130:
        s.x_plane_pool((3, 7), now=t)
        t += 1
    assert lines[-1] == (
        f"  X-Plane's background workers free again: Live works again after {st.POOL_HOLD_S:.0f} s"
    )
    for _ in range(st.POOL_WINDOW):
        s.x_plane_pool(None, now=t)  # not readable: never stopped
        t += 1
    assert not s.x_plane_busy() and not s.holds(5.0)
    s.cache.close()


def test_a_move_of_x_plane_s_window_pauses_live_again(tmp_path: Path) -> None:
    """X-Plane's flag up 519 s over three columns: Live back at work after 120 s, the second
    column's last tile came after 237 s (Norway, 2026-09-30). Each move of its window while the
    flag stays up pauses Live again, YIELD_MAX_S from there."""
    from orthostudio.live import streamer as st

    s = row_streamer(tmp_path)
    s.log = lambda _: None
    s.flying = True
    kt480 = 480 * 0.514444
    s.follow_window(Fix(60.415, 12.5, gs_ms=kt480, trk_deg=270.0), now=0.0)  # taken
    s.x_plane_loads(True, now=0.0)
    s.x_plane_loads(True, now=st.YIELD_MAX_S + 1)  # past the pause: Live works again
    assert s.yield_until == st.YIELD_AFTER_S  # paused from the flag's start only
    s.follow_window(Fix(60.415, 12.36, gs_ms=kt480, trk_deg=270.0), now=150.0)  # not yet 12.35
    assert s.yield_until < 150.0
    s.follow_window(Fix(60.415, 12.34, gs_ms=kt480, trk_deg=270.0), now=160.0)  # moved west
    assert s.yield_until == 160.0 + st.YIELD_AFTER_S
    s.x_plane_loads(True, now=161.0)  # the flag still up: paused, counted from the move
    assert s.yield_until == 161.0 + st.YIELD_AFTER_S
    s.x_plane_loads(True, now=160.0 + st.YIELD_MAX_S + 1)
    assert s.yield_until == 161.0 + st.YIELD_AFTER_S  # its pause over too
    s.x_plane_loads(False, now=300.0)
    s.follow_window(Fix(60.415, 11.34, gs_ms=kt480, trk_deg=270.0), now=400.0)  # no load up
    assert s.yield_until == 161.0 + st.YIELD_AFTER_S  # the flag will say when X-Plane loads
    s.cache.close()


def test_nothing_encoded_while_x_plane_loads_not_even_what_it_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live's work during X-Plane's loads delayed the third tile of a column by minutes over
    Norway; paused for the whole load, none came late (2026-09-30)."""
    from orthostudio.live import streamer as st

    s = row_streamer(tmp_path)
    s.flying = True
    s.log = lambda _: None
    monkeypatch.setattr(st.time, "monotonic", lambda: 1000.0)
    s.x_plane_loads(True, now=1000.0 - st.YIELD_MAX_S / 2)
    s.x_plane_loads(True, now=1000.0)  # past the 30 s Live once held back: still paused
    assert s.yielding() and not s.may_encode(-1.0, 0)
    s.x_plane_loads(False, now=1000.0)
    monkeypatch.setattr(st.time, "monotonic", lambda: 1000.0 + st.YIELD_AFTER_S + 1)
    assert not s.yielding() and s.may_encode(-1.0, 0)
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


def test_the_window_hears_of_a_source_refusing_this_computer(tmp_path: Path) -> None:
    """The sources whose server refuses this computer (Downloader.refusing) go to the window with
    the streamer's figures; a downloader that cannot tell, none."""
    s = row_streamer(tmp_path)

    class Denied:
        def refusing(self):
            return ["Esri World Imagery"]

    assert s.snapshot()["refused_by"] == []
    s.downloader = Denied()
    assert s.snapshot()["refused_by"] == ["Esri World Imagery"]
    s.cache.close()


def test_an_8192_px_texture_made_coarser_keeps_its_size(tmp_path: Path) -> None:
    """Beyond the disk budget, far away: as any texture, its levels kept from 256 px down, the
    copy above them, in its 8192 px file still: X-Plane may have read its size."""
    s = row_streamer(tmp_path, disk_budget=1, cache_budget=0)  # no imagery room to borrow
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


def test_speed_and_track_from_positions_when_xplane_says_none() -> None:
    """A drone's aircraft: X-Plane says 0 kt while it flies at 600 kt. The planner takes its speed
    and track from its last two positions, so that the path ahead is planned (2026-09-29)."""
    from orthostudio.live.streamer import motion_from_positions

    before = Fix(43.637, 2.40)
    fix = Fix(43.637, 2.40 + 0.6172 / 80.47, trk_deg=275.5)  # 617 m east in 2 s
    motion_from_positions(before, 100.0, fix, 102.0)
    assert fix.gs_ms == pytest.approx(308.6, rel=0.01)
    assert fix.trk_deg == pytest.approx(90.0, abs=0.5)
    flying = Fix(43.637, 2.4077, gs_ms=250.0, trk_deg=88.0)  # X-Plane says it: as it says
    motion_from_positions(before, 100.0, flying, 102.0)
    assert (flying.gs_ms, flying.trk_deg) == (250.0, 88.0)
    parked = Fix(43.637, 2.40001)  # a metre: taxiing, or nothing
    motion_from_positions(before, 100.0, parked, 102.0)
    assert parked.gs_ms == 0.0
    jumped = Fix(45.0, 5.0)  # hundreds of km in 2 s: a jump, not a flight
    motion_from_positions(before, 100.0, jumped, 102.0)
    assert jumped.gs_ms == 0.0
    motion_from_positions(None, 0.0, jumped, 102.0)  # the first position: nothing to go by
    assert jumped.gs_ms == 0.0


def test_a_start_knows_when_it_sets_the_textures_to_a_new_zoom(tmp_path: Path) -> None:
    """What the window says while Live starts (status relayout): due once the zoom differs from
    the pack's layout, no longer once laid out; never for a pack not set up yet."""
    pack = region(tmp_path, Zooms(16, 18))
    same = session.Options(pack=pack, max_zoom=16, max_zoom_airports=18)
    lower = session.Options(pack=pack, max_zoom=14, max_zoom_airports=16)
    assert not session.relayout_due(same)
    assert session.relayout_due(lower)
    relayout(pack, Zooms(14, 16), quiet)
    assert not session.relayout_due(lower) and session.relayout_due(same)
    assert not session.relayout_due(session.Options(pack=tmp_path / "none", max_zoom=14))


def test_a_start_s_wait_tells_the_textures_waiting_for_photos_from_those_being_built(
    tmp_path: Path,
) -> None:
    """A start's wait counts apart the textures waiting for their photos and those with all their
    photos, being built: the plugin's and the window's two lines. One count of both, at 0.0 MB/s
    once every photo was in, read as stuck (the user's Shadow, 2026-10-11)."""
    from orthostudio.live.gate import _wait_line

    line = _wait_line(801, "LFBO", 0.37, 100, 0.0, "fr", building=400)
    assert line.startswith("LIVE1 WAIT n=801 b=400 p=370 s=100 mbs=0.0 lang=fr night=0 at=")
    s = row_streamer(tmp_path)
    queued, taken, far = f"{ROW[0]}.dds", f"{ROW[1]}.dds", f"{ROW[5]}.dds"
    s.queue[queued] = (0.0, 0)  # urgent, its photos still to ask for
    s.in_flight[taken] = (0.0, 0)  # urgent, its photos coming
    assert s.urgent_building() == 0
    s.photos_in_names = frozenset({taken})  # all its photos in: being built
    assert s.urgent_building() == 1
    s.in_flight[far] = (40.0, 4)  # being built too, but X-Plane does not wait for it
    s.photos_in_names = frozenset({taken, far})
    assert s.urgent_building() == 1


def test_a_neutral_name_made_neutral_again_and_a_stopped_live_s_files_leave_nothing(
    tmp_path: Path,
) -> None:
    """A texture already a name of the neutral texture made one again (a reset of what was
    recorded as built) leaves no temporary file; those a Live stopped in the middle of a write left
    are removed at the next start, the textures kept."""
    from orthostudio.live.names import NeutralPool
    from orthostudio.live.region import TEMP_PREFIX, remove_temps

    pack = region(tmp_path)
    textures = pack / "textures"
    name = textures / f"{NAMES[0]}.dds"
    pool = NeutralPool(pack)
    pool.replace(0, name)
    pool.replace(0, name)  # already the neutral texture's name
    assert not [p for p in textures.iterdir() if p.name.startswith(TEMP_PREFIX)]
    left = textures / f"{TEMP_PREFIX}4980-{NAMES[1]}.dds"
    left.write_bytes(b"half written")
    before = {p.name for p in textures.iterdir()} - {left.name}
    assert remove_temps(pack) == 1
    assert {p.name for p in textures.iterdir()} == before
