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
from orthostudio.clouds import cloudlist
from orthostudio.clouds.laying import lay_again, lay_folder, lay_texture, show
from orthostudio.clouds.tile import (
    TileClouds,
    entry_problem,
    part_path,
    parts_dir,
    patches_of,
    read_stamp,
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
from orthostudio.textures.clouds import encode_part, pieces
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
    return lay_folder(TILE, folder, entries)


def _texture(folder: Path, t: TextureId) -> Path:
    return folder / "textures" / f"{texture_name(t)}.dds"


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
    assert read_stamp(_texture(folder, EAST)) is None


def test_the_build_texture_is_kept_beside_and_restore_puts_it_back_byte_for_byte(
    folder: Path,
) -> None:
    held = read_tile(folder)
    assert held is not None
    before = _texture(folder, WEST).read_bytes()
    _lay(folder, [_cloud("c1", WEST_ONLY, held, RED)])
    kept = folder / "clouds" / f"{texture_name(WEST)}.dds"
    assert kept.read_bytes() == before
    stamp = read_stamp(_texture(folder, WEST))
    assert stamp is not None and stamp.plain == digest_bytes(before)[:40]
    _lay(folder, [])
    assert _texture(folder, WEST).read_bytes() == before
    assert read_stamp(_texture(folder, WEST)) is None
    assert not (folder / "clouds").exists()  # nothing left beside


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
    assert [c["id"] for c in read_tile(folder, [west, across]).held] == ["c1", "c2"]
    # a build put the east texture back as it made it: the cloud across is not whole there
    shutil.copyfile(folder / "clouds" / f"{texture_name(EAST)}.dds", _texture(folder, EAST))
    assert [c["id"] for c in read_tile(folder, [west, across]).held] == ["c1"]
    # clouds last applied that the textures do not name are not held either
    assert read_tile(folder, [across]).held == ()


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
    laid = cloudlist.load(TILE).laid  # the list told what the tile shows now
    assert [c["id"] for c in laid] == ["c1"]
    assert [c["id"] for c in read_tile(folder, laid).held] == ["c1"]
    assert read_stamp(_texture(folder, EAST)) is None
    assert _texture(folder, WEST).read_bytes() != shown["w"]  # laid with c1 alone


def test_a_build_lays_nothing_again_without_clouds_applied_or_in_another_folder(
    folder: Path, tmp_path: Path, textures: dict[str, bytes]
) -> None:
    held = read_tile(folder)
    assert held is not None
    lay_again(TILE, folder, custom_scenery=None)  # no list: nothing
    assert read_stamp(_texture(folder, WEST)) is None
    with cloudlist.update(TILE) as cl:
        cl.laid = [_cloud("c1", WEST_ONLY, held, RED)]
    other = tmp_path / "elsewhere" / pack_dir_name(TILE)
    shutil.copytree(folder, other)
    lay_again(TILE, other, custom_scenery=None)  # a command line's own folder: not the tile's
    assert read_stamp(other / "textures" / f"{texture_name(WEST)}.dds") is None


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
        calls[0] += 1
        if calls[0] == at:
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
        show(TILE, folder, [across])
    assert got.value.code == "XP_PACK_CONFLICT" and "Quit X-Plane" in got.value.remedy
    for name, data in textures.items():  # the first one put back: nothing changed
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
        show(TILE, folder, [cloud])
    assert got.value.code == "CLOUD_TILE_NOT_READY" and got.value.context["state"] == "in_build"
    assert _texture(folder, WEST).read_bytes() == other  # the build's, untouched
    assert cloudlist.load(TILE).laid == []


def test_a_cloud_whose_images_are_gone_is_left_out_and_the_others_laid(folder: Path) -> None:
    held = read_tile(folder)
    assert held is not None
    west, across = _cloud("c1", WEST_ONLY, held, RED), _cloud("c2", ACROSS, held, BLUE)
    part_path(across["parts"][texture_name(EAST)][1]).unlink()
    assert stands_not(across, TILE, held) == "parts"
    assert [c["id"] for c in _lay(folder, [west, across])] == ["c1"]
    assert read_stamp(_texture(folder, EAST)) is None


def test_a_texture_that_cannot_be_read_is_never_taken_for_the_builds(folder: Path) -> None:
    texture = _texture(folder, WEST)
    texture.chmod(0)
    try:
        with pytest.raises(OsxpError) as got:
            read_tile(folder)
        assert got.value.code == "CLOUD_TEXTURE_UNREADABLE"
    finally:
        texture.chmod(0o644)


def test_files_a_laying_cut_short_left_go_once_old(folder: Path) -> None:
    held = read_tile(folder)
    assert held is not None
    old = folder / "textures" / f"{texture_name(WEST)}.dds.osxp-clouds-new-1"
    young = folder / "textures" / f"{texture_name(EAST)}.dds.osxp-clouds-new-2"
    for left in (old, young):
        left.write_bytes(b"left")
    os.utime(old, (1, 1))
    _lay(folder, [_cloud("c1", WEST_ONLY, held, RED)])
    assert not old.exists() and young.exists()  # one may be another program's, under way


def test_a_build_that_changes_nothing_writes_no_list(folder: Path) -> None:
    held = read_tile(folder)
    assert held is not None
    show(TILE, folder, [_cloud("c1", WEST_ONLY, held, RED)])
    path = cloudlist.list_path(TILE)
    at = path.stat().st_mtime_ns
    lay_again(TILE, folder, custom_scenery=None)  # every texture shows its clouds already
    assert path.stat().st_mtime_ns == at
