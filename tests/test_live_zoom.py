# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The zoom set, as AutoOrtho's max_zoom and max_zoom_near_airports: each texture's file at the size
of its zoom (1024 px two zooms below the mesh's, 8192 px one above), a new zoom laying the pack out
again without losing what was built, and the streamer building to the file's size."""

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
    NEUTRAL_LEVEL,
    Zooms,
    needs_relayout,
    neutral_file,
    region_zooms,
    relayout,
    setup_region,
)
from orthostudio.live.streamer import Fix, Streamer
from orthostudio.live.texture import downgrade, write_sparse

AIRPORT = "93184_132608_BI18"  # a ZL18 texture inside NAMES[0]: the mesh's airports


def quiet(_: str) -> None:
    pass


def side_of(path: Path) -> int:
    """The width a DDS file's header gives."""
    return int.from_bytes(path.read_bytes()[16:20], "little")


def region(root: Path, zooms: Zooms | None = None) -> Path:
    pack = root / "pack"
    source = make_source(root, tile=(45, 6), textures=(*NAMES, AIRPORT))
    setup_region(source, pack, log=quiet, zooms=zooms)
    return pack


def levels_from(top: int, file_top: int) -> list[bytes]:
    """Levels ``top`` down, each byte its level's number (+10, so that no level reads as 0)."""
    layout = dds.level_layout(file_top)
    return [bytes([10 + file_top + k]) * n for k, (_, n) in enumerate(layout)][top - file_top :]


def level_bytes(path: Path, level: int, file_top: int) -> bytes:
    offset, length = dds.level_layout(file_top)[level - file_top]
    return path.read_bytes()[offset : offset + length]


def test_the_pack_laid_out_for_the_zoom(tmp_path: Path) -> None:
    """Away from airports ZL14: 1024 px files; around them ZL19: 8192 px ones."""
    pack = region(tmp_path, Zooms(max_zoom=14, max_zoom_airports=19))
    textures = pack / "textures"
    assert side_of(textures / f"{NAMES[0]}.dds") == 1024
    assert side_of(textures / f"{AIRPORT}.dds") == 8192
    assert (textures / f"{AIRPORT}.dds").stat().st_size == dds.file_size(8192)
    assert (pack / neutral_file(2)).exists() and (pack / neutral_file(-1)).exists()
    assert region_zooms(pack) == Zooms(14, 19)
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
    (pack / "_live" / "ortho.json").write_text(json.dumps({"textures": {built.name: 0}}))
    lines: list[str] = []

    # down to ZL14: a 1024 px file, its levels from 2 down the very ones built, nothing downloaded
    assert relayout(pack, Zooms(14, 18), log=lines.append) == 2  # the two ZL16 textures
    assert side_of(built) == 1024
    for level in range(2, 5):
        assert level_bytes(built, level, 2) == levels_from(level, 0)[0]
    assert json.loads((pack / "_live" / "ortho.json").read_text())["textures"] == {built.name: 2}
    other = pack / "textures" / f"{NAMES[1]}.dds"
    assert other.read_bytes() == (pack / neutral_file(2)).read_bytes()  # still neutral
    assert side_of(pack / "textures" / f"{AIRPORT}.dds") == 4096  # airports untouched
    assert any("4096 px -> 1024 px" in line for line in lines)

    # up to ZL17: an 8192 px file, its levels kept, the sharper ones neutral, never black
    relayout(pack, Zooms(17, 18), log=quiet)
    assert side_of(built) == 8192
    assert level_bytes(built, 2, -1) == levels_from(2, 0)[0]
    neutral = pack / neutral_file(-1)
    for level in (-1, 0, 1):
        assert level_bytes(built, level, -1) == level_bytes(neutral, level, -1)
    assert json.loads((pack / "_live" / "ortho.json").read_text())["textures"] == {built.name: 2}
    assert not needs_relayout(pack, Zooms(17, 18))


def test_an_8192_texture_on_the_disk_and_made_coarser(tmp_path: Path) -> None:
    assert texture_bytes(-1) == 4096 + sum(n for _, n in dds.level_layout(-1))
    assert texture_bytes(-1) > 4 * texture_bytes(0) - 1_000_000
    pack = region(tmp_path, Zooms(17, 18))
    path = pack / "textures" / f"{NAMES[0]}.dds"
    write_sparse(path, -1, levels_from(-1, -1), base=pack / neutral_file(-1), file_top=-1)
    downgrade(path, 4, pack / neutral_file(-1), file_top=-1)
    assert side_of(path) == 8192
    assert level_bytes(path, 4, -1) == levels_from(4, -1)[0]
    neutral = pack / neutral_file(-1)
    assert level_bytes(path, 0, -1) == level_bytes(neutral, 0, -1)
    downgrade(path, NEUTRAL_LEVEL, neutral, file_top=-1)
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


@pytest.mark.skipif(Image is None, reason="needs numpy, Pillow and ispc_texcomp")
@pytest.mark.parametrize(("max_zoom", "level", "side"), [(17, -1, 8192), (14, 2, 1024)])
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
    jobs = s.plan(Fix(lat, lon), x_plane_there=False)
    assert jobs[name][1] == level  # the file's sharpest level: X-Plane reads it here
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
