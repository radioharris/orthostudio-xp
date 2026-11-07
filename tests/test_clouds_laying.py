# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The replaced clouds in a tile's folder (``clouds.md`` 2.1 to 2.3): laid in the textures the
build placed, only the blocks a cloud reaches made again at every level; put back byte for byte;
always laid from the build's own texture, never over a laid one; read from the folder alone; laid
again after a build where they still stand, whole or not at all; nothing of it ever fails a
build. +43+005 at ZL12, two textures side by side, as the build encodes them."""

from __future__ import annotations

import io
import logging
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from PIL import Image

import test_api_fakes as fakes
from orthostudio.clouds import cloudlist, laying, state
from orthostudio.clouds.layer import encode_part, pieces
from orthostudio.clouds.laying import lay_again, lay_folder, lay_texture, plan_folder, show
from orthostudio.clouds.sources import JUDGED
from orthostudio.clouds.tile import (
    TileClouds,
    entry_problem,
    lays_not,
    part_path,
    parts_dir,
    patches_of,
    read_head,
    read_tile,
    stands_not,
)
from orthostudio.errors import OsxpError
from orthostudio.graph.digest import digest_bytes
from orthostudio.home import default_tiles_root
from orthostudio.imagery.grid import TextureId, parse_texture_name, texture_name
from orthostudio.model import TileRef, pack_dir_name
from orthostudio.pipeline import build as build_mod
from orthostudio.pipeline.pack import ArtefactEntry, PackManifest, write_manifest
from orthostudio.textures import dds
from orthostudio.textures.bcdecode import decode_dds
from orthostudio.textures.encode import encode_dds

home = fakes.home
TILE = TileRef(43, 5)
WEST = TextureId(til_x=2096, til_y=1488, zl=12, provider="T")
EAST = TextureId(til_x=2112, til_y=1488, zl=12, provider="T")
NEUTRAL = [[1.0, 1.0, 1.0], [0.0, 0.0, 0.0]]
RED, BLUE = (230, 20, 20), (20, 20, 230)
WEST_ONLY = [43.45, 5.30, 43.45, 5.40, 43.52, 5.40, 43.52, 5.30, 43.45, 5.30]
ACROSS = [43.30, 5.60, 43.30, 5.65, 43.36, 5.65, 43.36, 5.60, 43.30, 5.60]
"""A cloud on the seam of the two textures (lon 5.625)."""


def _built(seed: int) -> bytes:
    """A texture as the build encodes it: a soft gradient, all its levels."""
    y, x = np.mgrid[0:4096, 0:4096]
    rgb = np.dstack([(x * 7 + seed) % 200 + 20, (y * 5 + seed) % 180 + 30, (x + y) % 150 + 40])
    return encode_dds(rgb.astype(np.uint8), "bc1", encoder="ispc")


@pytest.fixture(scope="module")
def textures() -> dict[str, bytes]:
    return {texture_name(WEST): _built(0), texture_name(EAST): _built(90)}


@pytest.fixture
def folder(home: Path, textures: dict[str, bytes]) -> Path:
    """The tile built: its textures and its manifest, in the folder the app builds it in."""
    pack = default_tiles_root() / pack_dir_name(TILE)
    (pack / "textures").mkdir(parents=True)
    for name, data in textures.items():
        (pack / "textures" / f"{name}.dds").write_bytes(data)
    (pack / TILE.dsf_relpath).parent.mkdir(parents=True)
    (pack / TILE.dsf_relpath).write_bytes(b"")  # whole: every file its manifest lists
    files = {"dsf": TILE.dsf_relpath.as_posix(), "dsf_size": 0, "textures": 2, "terrain": 0}
    manifest = PackManifest(TILE.name, "T", 12, {"textures": ArtefactEntry("k", "d", "r@1")}, files)
    write_manifest(pack, TILE, manifest)
    return pack


def _png(rgb: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), rgb).save(buf, format="PNG")
    return buf.getvalue()


def _cloud(cid: str, ring: list[float], held: TileClouds, colour: tuple[int, int, int]) -> dict:
    """A cloud's images got from a source showing ``colour`` there, as a check gets them."""
    c: dict[str, Any] = {"id": cid, "source": "EOX", "match": NEUTRAL, "rings": [ring]}
    parts: dict[str, list[Any]] = {}
    keep = parts_dir()
    keep.mkdir(parents=True, exist_ok=True)
    for name, patch in patches_of(c, TILE, held.textures, lambda t: t.zl).items():
        t = parse_texture_name(name)
        data = encode_part({k: _png(colour) for k in pieces(patch, t)})
        (keep / f"{digest_bytes(data)}.part").write_bytes(data)
        parts[name] = [t.zl, digest_bytes(data)]
    entry = {**c, "parts": parts, "plain": {n: held.plain(n) for n in parts}}
    assert entry_problem(entry) is None
    return entry


def _lay(folder: Path, entries: list[dict]) -> list:
    """The folder made to show ``entries``, one images each, as a build after lays them."""
    return lay_folder(TILE, folder, [[e] for e in entries], custom_scenery=None)


def _texture(folder: Path, t: TextureId) -> Path:
    return folder / "textures" / f"{texture_name(t)}.dds"


def _stamp(path: Path) -> Any:
    return read_head(path)[0]


def _blocks(data: bytes) -> list[np.ndarray]:
    header, levels = dds.split_levels(data)
    size = dds.BLOCK_BYTES[header.fmt]
    return [np.frombuffer(bytes(lv), np.uint8).reshape(-1, size) for lv in levels]


def test_only_the_blocks_a_cloud_reaches_are_made_again_at_every_level(
    folder: Path, textures: dict[str, bytes]
) -> None:
    held = read_tile(folder)
    assert held is not None
    cloud = _cloud("c1", WEST_ONLY, held, RED)
    assert list(cloud["parts"]) == [texture_name(WEST)]
    before = _texture(folder, WEST).read_bytes()
    _lay(folder, [cloud])
    after = _texture(folder, WEST).read_bytes()
    assert len(after) == len(before)
    assert after[:32] == before[:32] and after[76:128] == before[76:128]  # only the stamp
    old, new = _blocks(before), _blocks(after)
    changed = [float((o != n).any(axis=1).mean()) for o, n in zip(old, new, strict=True)]
    assert 0 < changed[0] < 0.01  # a few blocks of the first level, the rest the build's
    assert all(c > 0 for c in changed)  # every level, down to the last
    # inside the cloud, the source's colour; the other texture, untouched
    first = decode_dds(after)[0]
    x, y = (np.array(patches_of(cloud, TILE, [WEST], lambda t: 12)[texture_name(WEST)].rings[0])
            .reshape(-1, 2).mean(axis=0).astype(int))  # fmt: skip
    assert np.abs(first[y, x, :3].astype(int) - RED).max() < 12
    assert _texture(folder, EAST).read_bytes() == textures[texture_name(EAST)]
    assert _stamp(_texture(folder, EAST)) is None


def test_the_build_texture_is_kept_beside_and_restore_puts_it_back_byte_for_byte(
    folder: Path,
) -> None:
    held = read_tile(folder)
    assert held is not None
    before = _texture(folder, WEST).read_bytes()
    _lay(folder, [_cloud("c1", WEST_ONLY, held, RED)])
    kept = folder / "clouds" / f"{texture_name(WEST)}.dds"
    assert kept.read_bytes() == before
    stamp = _stamp(_texture(folder, WEST))
    assert stamp is not None and stamp.plain == digest_bytes(before)[:40]
    other = folder / "clouds" / ("d" * 64 + ".json")  # a file there not the clouds' own
    other.write_text("{}")
    _lay(folder, [])
    assert _texture(folder, WEST).read_bytes() == before
    assert _stamp(_texture(folder, WEST)) is None
    assert sorted(p.name for p in (folder / "clouds").iterdir()) == [other.name]  # ours gone


def test_a_texture_is_laid_from_the_build_one_never_over_a_laid_one(folder: Path) -> None:
    held = read_tile(folder)
    assert held is not None
    red, blue = _cloud("c1", WEST_ONLY, held, RED), _cloud("c2", WEST_ONLY, held, BLUE)
    _lay(folder, [red])
    _lay(folder, [blue])
    once = _texture(folder, WEST).read_bytes()
    plain = (folder / "clouds" / f"{texture_name(WEST)}.dds").read_bytes()
    assert once == lay_texture(plain, texture_name(WEST), TILE, [blue])
    # the same clouds again: the texture is left as it is
    at = os.stat(_texture(folder, WEST)).st_mtime_ns
    _lay(folder, [blue])
    assert os.stat(_texture(folder, WEST)).st_mtime_ns == at


def test_the_tile_shows_the_clouds_every_texture_they_reach_shows(folder: Path) -> None:
    held = read_tile(folder)
    assert held is not None
    west, across = _cloud("c1", WEST_ONLY, held, RED), _cloud("c2", ACROSS, held, BLUE)
    assert sorted(across["parts"]) == sorted([texture_name(WEST), texture_name(EAST)])
    _lay(folder, [west, across])
    assert [c["id"] for c in read_tile(folder).held] == ["c1", "c2"]  # read from the folder alone
    # a build put the east texture back as it made it: the cloud across is not whole there
    shutil.copyfile(folder / "clouds" / f"{texture_name(EAST)}.dds", _texture(folder, EAST))
    assert [c["id"] for c in read_tile(folder).held] == ["c1"]
    # what the textures name beside them, nothing else, says what the tile shows
    with cloudlist.update(TILE) as cl:
        cl.laid = [across]
    assert [c["id"] for c in read_tile(folder).held] == ["c1"]


def test_a_build_after_lays_again_the_clouds_that_still_stand_whole(
    folder: Path, textures: dict[str, bytes]
) -> None:
    held = read_tile(folder)
    assert held is not None
    west, across = _cloud("c1", WEST_ONLY, held, RED), _cloud("c2", ACROSS, held, BLUE)
    _lay(folder, [west, across])
    shown = {n: _texture(folder, t).read_bytes() for n, t in (("w", WEST), ("e", EAST))}
    with cloudlist.update(TILE) as cl:
        cl.laid = [west, across]
    # the same build again: its textures placed as it made them, the clouds laid again
    for name, data in textures.items():
        (folder / "textures" / f"{name}.dds").write_bytes(data)
    lay_again(TILE, folder, custom_scenery=None)
    assert _texture(folder, WEST).read_bytes() == shown["w"]
    assert _texture(folder, EAST).read_bytes() == shown["e"]
    # a build with another photo east: the cloud across stands no more, on either texture
    for name, data in textures.items():
        (folder / "textures" / f"{name}.dds").write_bytes(data)
    _texture(folder, EAST).write_bytes(_built(7))
    after = read_tile(folder)
    assert after is not None and stands_not(across, TILE, after) == "images"
    lay_again(TILE, folder, custom_scenery=None)
    assert [c["id"] for c in read_tile(folder).held] == ["c1"]
    assert _stamp(_texture(folder, EAST)) is None
    assert _texture(folder, WEST).read_bytes() != shown["w"]  # laid with c1 alone
    # the list keeps what Apply laid: the first photo back, the cloud across comes back with it
    # (a review, 2026-11-06: the build between had told the list, and the next one laid it no more)
    assert [c["id"] for c in cloudlist.load(TILE).laid] == ["c1", "c2"]
    for name, data in textures.items():
        (folder / "textures" / f"{name}.dds").write_bytes(data)
    lay_again(TILE, folder, custom_scenery=None)
    assert [c["id"] for c in read_tile(folder).held] == ["c1", "c2"]
    assert _texture(folder, WEST).read_bytes() == shown["w"]
    assert _texture(folder, EAST).read_bytes() == shown["e"]


def test_a_build_after_fetches_the_tool_that_lays(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # without OpenCV (a new version's, its folder cleaned), the build put the photos back unsaid
    # (a review, 2026-11-07): fetched again first, asked for once when the clouds were got
    asked: list[int] = []
    monkeypatch.setattr("orthostudio.clouds.laying.ensure_opencv", lambda: asked.append(1))
    held = read_tile(folder)
    assert held is not None
    west = _cloud("c1", WEST_ONLY, held, RED)
    _lay(folder, [west])
    with cloudlist.update(TILE) as cl:
        cl.laid = [west]
    lay_again(TILE, folder, custom_scenery=None)
    assert asked == [1]


def test_a_build_lays_nothing_again_without_clouds_applied_or_in_another_folder(
    folder: Path, tmp_path: Path, textures: dict[str, bytes]
) -> None:
    held = read_tile(folder)
    assert held is not None
    lay_again(TILE, folder, custom_scenery=None)  # no list: nothing
    assert _stamp(_texture(folder, WEST)) is None
    with cloudlist.update(TILE) as cl:
        cl.laid = [_cloud("c1", WEST_ONLY, held, RED)]
    other = tmp_path / "elsewhere" / pack_dir_name(TILE)
    shutil.copytree(folder, other)
    lay_again(TILE, other, custom_scenery=None)  # a command line's own folder: not the tile's
    assert _stamp(other / "textures" / f"{texture_name(WEST)}.dds") is None


def test_a_build_making_the_photos_another_way_is_said_to_put_clouds_back(folder: Path) -> None:
    # the Plan said nothing, and the replaced clouds came back as photographed after a build with
    # other colours (a campaign, 2026-11-07): ``CLOUD_PUT_BACK``
    from orthostudio.clouds.laying import puts_back

    assert not puts_back(TILE, "other", custom_scenery=None)  # no clouds laid: nothing to say
    held = read_tile(folder)
    assert held is not None
    with cloudlist.update(TILE) as cl:
        cl.laid = [_cloud("c1", WEST_ONLY, held, RED)]
    assert not puts_back(TILE, "k", custom_scenery=None)  # the same images: laid again
    assert puts_back(TILE, "other", custom_scenery=None)  # made another way
    assert puts_back(TILE, None, custom_scenery=None)  # not known before the build runs
    (folder / "orthostudio.toml").unlink()  # no tile of ours there: none shown
    assert not puts_back(TILE, "other", custom_scenery=None)


def test_a_build_texture_lost_from_beside_is_said_and_nothing_is_written(folder: Path) -> None:
    held = read_tile(folder)
    assert held is not None
    _lay(folder, [_cloud("c1", WEST_ONLY, held, RED)])
    laid = _texture(folder, WEST).read_bytes()
    (folder / "clouds" / f"{texture_name(WEST)}.dds").unlink()
    with pytest.raises(OsxpError) as got:
        _lay(folder, [])
    assert got.value.code == "CLOUD_TEXTURE_LOST"
    assert _texture(folder, WEST).read_bytes() == laid


def test_nothing_of_the_clouds_fails_a_build(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("a disk gone")

    monkeypatch.setattr("orthostudio.clouds.laying.lay_again", broken)
    spec = SimpleNamespace(tile=TILE, custom_scenery=None)
    with caplog.at_level(logging.WARNING, logger="orthostudio.build"):
        build_mod._clouds_again(spec, tmp_path)  # type: ignore[arg-type]
    assert "not laid again" in caplog.text


def _refusing(monkeypatch: pytest.MonkeyPatch, at: int) -> None:
    """The ``at``-th texture put in place refused, as Windows refuses one X-Plane holds; every
    other replace, the putting back included, done."""
    from orthostudio.clouds import laying

    calls = [0]
    real = laying.replace

    def replace(src: Path, dst: Path) -> None:
        calls[0] += Path(dst).parent.name == "textures"  # a texture X-Plane reads
        if calls[0] == at and Path(dst).parent.name == "textures":
            raise PermissionError(13, "The process cannot access the file", str(dst))
        real(src, dst)

    monkeypatch.setattr(laying, "replace", replace)


def test_apply_is_all_or_nothing_a_texture_xplane_holds_puts_the_others_back(
    folder: Path, textures: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    held = read_tile(folder)
    assert held is not None
    across = _cloud("c2", ACROSS, held, BLUE)  # two textures to lay
    _refusing(monkeypatch, at=2)
    with pytest.raises(OsxpError) as got:
        show(TILE, folder, [[across]], custom_scenery=None)
    assert got.value.code == "XP_PACK_CONFLICT" and "Quit X-Plane" in got.value.remedy
    for name, data in textures.items():  # the first one put back: nothing changed
        assert (folder / "textures" / f"{name}.dds").read_bytes() == data
    assert cloudlist.load(TILE).laid == []
    assert not list((folder / "textures").glob("*.osxp-clouds-*"))  # nothing left beside


def test_textures_made_at_once_one_failing_changes_nothing(
    folder: Path, textures: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    # made four at once (a build laid 19 again in 12 s, a user, 2026-11-07): one that fails while
    # the others are made lets those go, and nothing has changed
    held = read_tile(folder)
    assert held is not None
    across = _cloud("c2", ACROSS, held, BLUE)  # two textures to lay
    make = laying._make

    def failing(tile: Any, shown: Any, name: str, mine: Any) -> Path:
        if name == texture_name(EAST):
            raise OSError("the disk is full")
        return make(tile, shown, name, mine)

    monkeypatch.setattr(laying, "_make", failing)
    with pytest.raises(OSError):
        show(TILE, folder, [[across]], custom_scenery=None)
    for name, data in textures.items():
        assert (folder / "textures" / f"{name}.dds").read_bytes() == data
    assert cloudlist.load(TILE).laid == []
    assert not list((folder / "textures").glob("*.osxp-clouds-*"))  # nothing left beside


def test_a_texture_a_build_replaced_meanwhile_is_not_laid_over(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orthostudio.clouds import laying

    held = read_tile(folder)
    assert held is not None
    cloud = _cloud("c1", WEST_ONLY, held, RED)
    other = _built(7)
    real = laying.lay_texture

    def while_a_build_places_its_own(*args: Any) -> bytes:
        made = real(*args)
        _texture(folder, WEST).write_bytes(other)  # the build's write, meanwhile
        return made

    monkeypatch.setattr(laying, "lay_texture", while_a_build_places_its_own)
    with pytest.raises(OsxpError) as got:
        show(TILE, folder, [[cloud]], custom_scenery=None)
    assert got.value.code == "CLOUD_TILE_NOT_READY" and got.value.context["state"] == "in_build"
    assert _texture(folder, WEST).read_bytes() == other  # the build's, untouched
    assert cloudlist.load(TILE).laid == []


def test_a_cloud_whose_images_are_gone_is_left_out_and_the_others_laid(folder: Path) -> None:
    held = read_tile(folder)
    assert held is not None
    west, across = _cloud("c1", WEST_ONLY, held, RED), _cloud("c2", ACROSS, held, BLUE)
    part_path(across["parts"][texture_name(EAST)][1]).unlink()
    assert lays_not(across, TILE, held) == "parts" and stands_not(across, TILE, held) is None
    assert [c["id"] for c in _lay(folder, [west, across])] == ["c1"]
    assert _stamp(_texture(folder, EAST)) is None


def test_a_texture_that_cannot_be_read_is_never_taken_for_the_builds(folder: Path) -> None:
    texture = _texture(folder, WEST)
    texture.chmod(0)
    try:
        with pytest.raises(OsxpError) as got:
            read_tile(folder)
        assert got.value.code == "CLOUD_TEXTURE_UNREADABLE"
    finally:
        texture.chmod(0o644)


def test_files_a_laying_cut_short_left_go_once_their_program_is_gone(folder: Path) -> None:
    import subprocess
    import sys

    held = read_tile(folder)
    assert held is not None
    ended = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                           capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
    gone = folder / "textures" / f"{texture_name(WEST)}.dds.osxp-clouds-new-{ended}"
    mine = folder / "textures" / f"{texture_name(EAST)}.dds.osxp-clouds-new-{os.getpid()}"
    for left in (gone, mine):
        left.write_bytes(b"left")
    _lay(folder, [_cloud("c1", WEST_ONLY, held, RED)])
    assert not gone.exists() and mine.exists()  # one of a program under way stays


def test_a_sidecar_is_written_beside_as_the_textures_so_one_cut_short_goes(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # its atomic write's own temporary stayed in the tile's clouds/ after a kill (a campaign,
    # 2026-11-07): written beside as the textures, the sweep knows it (tile.leftovers)
    from orthostudio.clouds import laying

    written: list[str] = []
    real = laying._write

    def write(path: Path, data: bytes) -> None:
        written.append(path.name)
        real(path, data)

    monkeypatch.setattr(laying, "_write", write)
    held = read_tile(folder)
    assert held is not None
    _lay(folder, [_cloud("c1", WEST_ONLY, held, RED)])
    sides = [n for n in written if ".json" in n]
    assert sides and all(".osxp-clouds-side" in n for n in sides)
    assert not list(folder.glob("clouds/*.osxp-clouds-*"))  # put in place, nothing left


def test_a_build_that_changes_nothing_writes_no_list(folder: Path) -> None:
    held = read_tile(folder)
    assert held is not None
    show(TILE, folder, [[_cloud("c1", WEST_ONLY, held, RED)]], custom_scenery=None)
    path = cloudlist.list_path(TILE)
    at = path.stat().st_mtime_ns
    lay_again(TILE, folder, custom_scenery=None)  # every texture shows its clouds already
    assert path.stat().st_mtime_ns == at


def test_a_build_texture_linked_from_its_store_leaves_nothing_beside(folder: Path) -> None:
    # a build links its textures from its store: the one kept beside may be that same file
    held = read_tile(folder)
    assert held is not None
    cloud = _cloud("c1", WEST_ONLY, held, RED)
    _lay(folder, [cloud])
    laid = _texture(folder, WEST).read_bytes()
    kept = folder / "clouds" / f"{texture_name(WEST)}.dds"
    texture = _texture(folder, WEST)
    texture.unlink()
    os.link(kept, texture)  # the build's placing: the same file as the one kept
    with cloudlist.update(TILE) as cl:
        cl.laid = [cloud]
    lay_again(TILE, folder, custom_scenery=None)
    assert texture.read_bytes() == laid
    assert not list(folder.glob("*/*.osxp-clouds-*"))  # nothing left beside


def test_a_texture_whose_clouds_no_file_says_is_made_again(folder: Path) -> None:
    # a sidecar gone (another program's sweep, by hand): Apply writes it again, never loops
    held = read_tile(folder)
    assert held is not None
    cloud = _cloud("c1", WEST_ONLY, held, RED)
    _lay(folder, [cloud])
    (side,) = (folder / "clouds").glob("*.json")
    side.unlink()
    shown = read_tile(folder)
    assert shown is not None and shown.held == () and texture_name(WEST) not in shown.known
    assert plan_folder(TILE, shown, [[cloud]]).remade == {texture_name(WEST)}
    _lay(folder, [cloud])
    shown = read_tile(folder)
    assert shown is not None and [c["id"] for c in shown.held] == ["c1"]
    assert plan_folder(TILE, shown, [[cloud]]).work == []


def test_after_draws_as_built_only_a_texture_where_a_cloud_goes_or_changes(folder: Path) -> None:
    # each texture made again drawn as built, the whole tile was drawn again at each tick near
    # clouds laid, 0.8 s and the page waiting (a user, 2026-11-07)
    held = read_tile(folder)
    assert held is not None
    west = _cloud("c1", WEST_ONLY, held, RED)
    _lay(folder, [west])
    shown = read_tile(folder)
    assert shown is not None
    w, e = texture_name(WEST), texture_name(EAST)
    across = _cloud("c2", ACROSS, shown, BLUE)
    # a cloud gained, laid after or before the one shown: west keeps it, as X-Plane shows it
    for clouds in ([[west], [across]], [[across], [west]]):
        plan = plan_folder(TILE, shown, clouds)
        assert plan.remade == {w, e} and plan.bare == frozenset()
    # the one shown goes, or comes with other images: west drawn as built
    assert plan_folder(TILE, shown, [[across]]).bare == {w}
    assert plan_folder(TILE, shown, [[_cloud("c1", WEST_ONLY, shown, BLUE)], [across]]).bare == {w}
    # what it shows no file says: drawn as built
    (side,) = (folder / "clouds").glob("*.json")
    side.unlink()
    unknown = read_tile(folder)
    assert unknown is not None and w not in unknown.known
    assert plan_folder(TILE, unknown, [[west], [across]]).bare == {w}


def test_a_sidecar_that_is_not_whole_says_nothing_and_breaks_nothing(folder: Path) -> None:
    held = read_tile(folder)
    assert held is not None
    _lay(folder, [_cloud("c1", WEST_ONLY, held, RED)])
    (side,) = (folder / "clouds").glob("*.json")
    side.write_text("[1]")
    shown = read_tile(folder)
    assert shown is not None and shown.held == () and shown.known == frozenset()


def test_a_link_left_where_a_texture_is_made_is_never_written_through(folder: Path) -> None:
    # a file left beside by a laying cut short may be a link to the build's own: let go of first
    from orthostudio.clouds.tile import beside

    held = read_tile(folder)
    assert held is not None
    texture = _texture(folder, WEST)
    built = texture.read_bytes()
    os.link(texture, beside(texture, "new"))  # the build's own under the name Apply writes
    _lay(folder, [_cloud("c1", WEST_ONLY, held, RED)])
    assert (folder / "clouds" / f"{texture_name(WEST)}.dds").read_bytes() == built


def test_a_texture_left_torn_keeps_the_build_one_kept_beside(folder: Path) -> None:
    # a power cut after a rename: the texture no laid one, nor the build's; the kept one stays
    held = read_tile(folder)
    assert held is not None
    _lay(folder, [_cloud("c1", WEST_ONLY, held, RED)])
    kept = folder / "clouds" / f"{texture_name(WEST)}.dds"
    texture = _texture(folder, WEST)
    texture.write_bytes(b"\0" * texture.stat().st_size)
    _lay(folder, [])
    assert kept.is_file()


def test_what_lies_beside_once_laid_never_fails_the_laying(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows: a file let go of while another holds it; it goes at a later laying
    held = read_tile(folder)
    assert held is not None
    cloud = _cloud("c1", WEST_ONLY, held, RED)
    real = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if ".osxp-clouds-before-" in self.name and self.exists():
            raise PermissionError(13, "in use", str(self))
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    show(TILE, folder, [[cloud]], custom_scenery=None)
    assert [e["id"] for e in cloudlist.load(TILE).laid] == ["c1"]


def test_a_folder_not_whole_is_never_laid_in(folder: Path) -> None:
    held = read_tile(folder)
    assert held is not None
    cloud = _cloud("c1", WEST_ONLY, held, RED)
    _texture(folder, EAST).unlink()  # a file taken from it
    with pytest.raises(OsxpError) as got:
        _lay(folder, [cloud])
    assert got.value.context["state"] == "not_whole"
    assert _stamp(_texture(folder, WEST)) is None


def test_a_folder_whose_clouds_are_a_link_elsewhere_is_not_ours(
    folder: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (folder / "clouds").symlink_to(elsewhere, target_is_directory=True)
    assert read_tile(folder) is None
    with pytest.raises(OsxpError):
        _lay(folder, [])


def test_the_build_texture_is_found_one_way_the_store_after_the_one_kept(
    folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the map's picture and Apply read the same file: the kept one, else the store's
    held = read_tile(folder)
    assert held is not None
    _lay(folder, [_cloud("c1", WEST_ONLY, held, RED)])
    kept = folder / "clouds" / f"{texture_name(WEST)}.dds"
    store = tmp_path / "store-copy.dds"
    shutil.copyfile(kept, store)
    kept.unlink()
    monkeypatch.setattr(TileClouds, "stored_path", lambda self, name: store)
    shown = read_tile(folder)
    assert shown is not None
    assert shown.plain_path(texture_name(WEST)) == store
    _lay(folder, [])  # Restore from the store's
    assert _stamp(_texture(folder, WEST)) is None
    assert _texture(folder, WEST).read_bytes() == store.read_bytes()


def test_the_tile_shows_its_clouds_before_the_build_says_it_finished_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # a job lets go of a tile it finished: laid again before that, never at the job's end
    from orthostudio.sched.events import Done

    seen: list[str] = []

    def again(spec: Any, folder: Path, progress: Any) -> None:
        progress(1, 2)  # a texture of two laid: the step says so, its bar full
        seen.append("laid")

    monkeypatch.setattr(build_mod, "_clouds_again", again)
    spec = SimpleNamespace(tile=TILE, level="BI_12", out_dir=str(tmp_path))
    workshop = SimpleNamespace(spec=spec, target=SimpleNamespace(id="+43+005/BI_12/pack"))
    heard: list[Any] = []
    told = build_mod._clouds_before_done(
        lambda e: (heard.append(e), seen.append(e.node_id)), [workshop], {}
    )
    for node in ("+43+005/BI_12/textures", "+43+005/BI_12/pack"):
        told(Done(node, "k", False, 0.0, None))  # type: ignore[arg-type]
    pack = "+43+005/BI_12/pack"
    assert seen == ["+43+005/BI_12/textures", pack, "laid", pack]
    said = heard[1]  # what the pack's step said while its clouds were laid, before its Done
    assert said.node_id == pack and said.fraction == 1.0 and "1 of 2 textures" in said.message
    assert isinstance(heard[-1], Done)
    seen.clear()
    filed = build_mod._clouds_before_done(lambda e: seen.append(e.node_id), [workshop],
                                          {TILE.name: tmp_path})  # fmt: skip
    filed(Done("+43+005/BI_12/pack", "k", False, 0.0, None))  # type: ignore[arg-type]
    filed(Done(f"+43+005/BI_12/{build_mod.PUT_BACK_ROLE}", "k", False, 0.0, None))  # type: ignore[arg-type]
    back = f"+43+005/BI_12/{build_mod.PUT_BACK_ROLE}"
    assert seen == ["+43+005/BI_12/pack", back, "laid", back]


def test_a_cloud_the_tile_shows_and_its_list_forgot_is_kept_by_the_next_apply(
    folder: Path,
) -> None:
    # its list put aside (not a list), or written on another computer: what X-Plane shows stays
    held = read_tile(folder)
    assert held is not None
    west, across = _cloud("c1", WEST_ONLY, held, RED), _cloud("c2", ACROSS, held, BLUE)
    show(TILE, folder, [[west]], custom_scenery=None)
    cloudlist.list_path(TILE).write_text("not a list")
    shown = read_tile(folder)
    cl = cloudlist.knowing(TILE, shown)  # the list knows it again, ticked, of its source
    (c,) = cl.clouds
    assert c["id"] == "c1" and c["tick"] is True and c["chosen"] == "EOX"
    assert list(cloudlist.list_path(TILE).parent.glob("*.broken-*"))  # the file put aside
    with cloudlist.update(TILE) as cl:  # another cloud ticked, its images got
        n = cl.add("found", [ACROSS], tick=True, chosen="EOX")
        n["checks"] = {"EOX": {"answer": "clear", "fit": 70, "entry": across, "judged": JUDGED}}
        n["id"] = "c2"
    v = state.view(cloudlist.load(TILE), shown, TILE, "ready")
    assert [s.row for s in v.clouds] == ["held", "ready"] and v.counts["held"] == 1
    show(TILE, folder, v.asked, custom_scenery=None)
    shown = read_tile(folder)
    assert shown is not None and sorted(c["id"] for c in shown.held) == ["c1", "c2"]


def test_a_texture_unreadable_right_after_its_replace_is_still_put_back(
    folder: Path, textures: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows: an antivirus holds the texture just replaced, then the next one is refused
    from orthostudio.clouds import laying

    held = read_tile(folder)
    assert held is not None
    across = _cloud("c2", ACROSS, held, BLUE)
    real_replace, real_identity = laying.replace, laying.identity
    placed = [0]
    held_once = [False]

    def replace(src: Path, dst: Path) -> None:
        a_new_one = Path(dst).parent.name == "textures" and ".osxp-clouds-new-" in Path(src).name
        if a_new_one and placed[0] == 1:  # the second texture: refused
            raise PermissionError(13, "in use", str(dst))
        real_replace(src, dst)
        placed[0] += a_new_one

    def identity(path: Path) -> Any:
        if placed[0] == 1 and not held_once[0] and Path(path).parent.name == "textures":
            held_once[0] = True
            raise PermissionError(13, "held", str(path))
        return real_identity(path)

    monkeypatch.setattr(laying, "replace", replace)
    monkeypatch.setattr(laying, "identity", identity)
    with pytest.raises(OsxpError):
        show(TILE, folder, [[across]], custom_scenery=None)
    for name, data in textures.items():  # nothing changed
        assert (folder / "textures" / f"{name}.dds").read_bytes() == data


def test_apply_while_x_plane_shows_the_tile_says_so_in_its_own_words(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orthostudio.clouds import laying

    def running(*_a: Any) -> None:
        raise OsxpError("SYS_REBUILD_XP_RUNNING", context={"tile": TILE.name})

    monkeypatch.setattr(laying, "_refuse_rewrite_under_running_xplane", running)
    held = read_tile(folder)
    assert held is not None
    textures = {p.name: p.read_bytes() for p in (folder / "textures").glob("*.dds")}
    with pytest.raises(OsxpError) as got:
        show(TILE, folder, [[_cloud("c1", WEST_ONLY, held, RED)]], custom_scenery=None)
    assert got.value.code == "CLOUD_XPLANE_RUNNING" and "apply again" in got.value.remedy
    assert {p.name: p.read_bytes() for p in (folder / "textures").glob("*.dds")} == textures
    # nothing to write, nothing refused: Forget with no cloud laid was (a review, 2026-11-07)
    show(TILE, folder, [], custom_scenery=None)


def test_a_build_lays_again_a_cloud_its_list_knew_only_from_the_folder(folder: Path) -> None:
    # its list lost: known again from the folder, laid again by the build after
    held = read_tile(folder)
    assert held is not None
    west = _cloud("c1", WEST_ONLY, held, RED)
    show(TILE, folder, [[west]], custom_scenery=None)
    laid = _texture(folder, WEST).read_bytes()
    cloudlist.list_path(TILE).unlink()
    cl = cloudlist.knowing(TILE, read_tile(folder))
    assert [e["id"] for e in cl.laid] == ["c1"] and cl.clouds[0]["checks"]["EOX"]["entry"]
    kept = folder / "clouds" / f"{texture_name(WEST)}.dds"
    _texture(folder, WEST).unlink()
    shutil.copyfile(kept, _texture(folder, WEST))  # the build places its own
    lay_again(TILE, folder, custom_scenery=None)
    assert _texture(folder, WEST).read_bytes() == laid


def test_a_cached_pack_whose_folder_lost_files_is_laid_again_when_its_pack_is_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a tile not installed too: before its clouds are laid again, not after (they were erased)
    from orthostudio.sched.events import Done

    laid: list[str] = []
    monkeypatch.setattr(build_mod, "pack_is_intact", lambda *a, **k: False)
    monkeypatch.setattr(
        build_mod, "_assemble_again", lambda nodes, env, col, m: laid.append("pack")
    )
    pack = tmp_path / "pack"
    files = {"dsf": TILE.dsf_relpath.as_posix(), "dsf_size": 0, "textures": 0, "terrain": 0}
    write_manifest(pack, TILE, PackManifest(TILE.name, "T", 12, {}, files))
    spec = SimpleNamespace(tile=TILE, out_dir=str(tmp_path), level="T_12")
    nodes = SimpleNamespace(spec=spec, pack=SimpleNamespace(id="+43+005/T_12/pack"), install=None)
    collector = build_mod._Collector(lambda e: laid.append("told"))
    collector.watch([nodes], SimpleNamespace(), {})  # type: ignore[list-item, arg-type]
    ref = SimpleNamespace(path=pack / "orthostudio.toml")
    collector(Done("+43+005/T_12/pack", "k", True, 0.0, ref))  # type: ignore[arg-type]
    assert laid == ["pack", "told"]
