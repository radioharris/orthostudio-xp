# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The store's copy of the images of the tiles on another disk or partition, deleted by "Free
space" only on request (``apart``), and the pack never written from a source gone.

A tile laid on the store's disk shares its images with the store, one file under two names. A tile
on another disk or partition holds its own copy, which X-Plane reads, and the store's is a second
one: a user's tiles moved to another partition left 358 GB of them twice, on the disk the move
was to free (2026-10-23). The store keeps nothing of where the tile's copies are: a build of such
a tile prepares its images again from the downloaded image pieces, which the images choice then
keeps. An earlier form let them go whatever the choices and took them back from the tile; a review
found a tile emptied of its images by a build that had found its textures folder in the store
before Free space deleted it, in another process. The pack now stops when a source is gone.

The world is the filed tiles' one (``test_clean_filed``): ``A`` in the workshop, ``F`` copied to
another folder, ``S`` moved on the workshop's own disk. Which disk a folder is on is what
``same_disk`` says, as for filing: told here which folders are on another disk. Temporary folders
only.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import orthostudio.clean as clean_module
import test_api_fakes as fakes
import test_clean_filed as filed
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.cli import app as cli_app
from orthostudio.errors import OsxpError
from orthostudio.graph import ResolvedInput, Store
from orthostudio.imagery.grid import texture_at
from orthostudio.pipeline.pack import assemble_pack, read_manifest
from test_api_fakes import FakeBuild, FakeIndex, client_for
from test_clean_filed import A, F, S, World

anyio_backend = fakes.anyio_backend
world = filed.world


def _apart(monkeypatch: pytest.MonkeyPatch, *tiles: Path) -> None:
    """The textures folders of the packs ``tiles`` are on another disk than the store."""
    away = {os.path.realpath(p) for p in tiles}
    monkeypatch.setattr(
        clean_module,
        "same_disk",
        lambda a, b: os.path.realpath(Path(b).parent) not in away,
    )


def _images(w: World, tile: object) -> set[str]:
    """The tile's images in the store, and its textures folder."""
    with Store(w.store, fsync=False) as s:
        return {
            k
            for k in w.keys[tile]  # type: ignore[index]
            if (i := s.info(k)) is not None and i.rule in ("texture.dds", "tile.textures")
        }


def _dds(pack: Path) -> dict[str, bytes]:
    return {f.name: f.read_bytes() for f in sorted((pack / "textures").glob("*.dds"))}


def test_the_copy_of_the_images_of_a_tile_on_another_disk_goes_only_on_request(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counted on a line of their own, F's images and its textures folder stay without the choice,
    and go with it: the figure is what the disk gets back. F is whole and untouched, its other
    data stays (its mesh, its DSF), and the tiles on the store's disk keep all theirs. A second
    Free space finds nothing more."""
    w = world
    _apart(monkeypatch, w.packs[F])
    images = _images(w, F)
    tile = _dds(w.packs[F])
    seen = w.run(dry_run=True)
    assert seen.apart_tiles == 1 and seen.apart_bytes > 0
    plain = w.run()
    assert not plain.apart_removed and images <= w.stored()
    before = w.disk()
    done = w.run(apart=True)
    assert done.apart_removed and done.apart_tiles == 1
    assert done.apart_bytes == seen.apart_bytes
    assert done.freed_bytes + done.apart_bytes == before - w.disk()
    assert not images & w.stored()
    assert w.keys[F] - images <= w.stored()  # its mesh, masks, DSF and overlay stay
    assert w.keys[A] <= w.stored() and w.keys[S] <= w.stored()
    assert w.whole(F) and _dds(w.packs[F]) == tile
    again = w.run(dry_run=True, apart=True)
    assert again.apart_tiles == 0 and again.apart_bytes == 0


def test_a_tile_on_the_store_s_disk_keeps_its_images(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a tile on another disk, the choice offers and frees nothing: A and S share their
    images with the store, one file under two names."""
    w = world
    _apart(monkeypatch)
    seen = w.run(dry_run=True, apart=True)
    assert seen.apart_tiles == 0 and seen.apart_bytes == 0
    stored = w.stored()
    w.run(apart=True)
    assert w.stored() == stored


def test_an_image_a_tile_on_the_store_s_disk_lists_stays(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A texture astride the border is listed by both tiles' folders: the neighbour on the store's
    disk needs it, so it stays, and F's other images go."""
    w = world
    _apart(monkeypatch, w.packs[F])
    with Store(w.store, fsync=False) as s:
        f_doc = json.loads((s.path(_folder(w, F)) / "manifest.json").read_text())
        shared = f_doc["textures"][0]["key"]
        a_list = s.path(_folder(w, A)) / "manifest.json"
        a_doc = json.loads(a_list.read_text())
        a_doc["textures"].append(f_doc["textures"][0])
        a_list.write_text(json.dumps(a_doc))
    w.run(apart=True)
    stored = w.stored()
    assert shared in stored
    assert not (_images(w, F) - {shared}) & stored


def _folder(w: World, tile: object) -> str:
    with Store(w.store, fsync=False) as s:
        return next(
            k
            for k in w.keys[tile]  # type: ignore[index]
            if (i := s.info(k)) is not None and i.rule == "tile.textures"
        )


def test_a_tile_whose_folder_cannot_be_read_is_none_of_them(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tile on a disk unplugged, or behind a letter another disk took: its folder cannot be read,
    it is not counted, and its cache stays through the keys the Library recorded."""
    w = world
    _apart(monkeypatch, w.packs[F])
    pack = w.packs[F]
    shutil.move(pack, pack.with_name("unplugged"))
    seen = w.run(dry_run=True, apart=True)
    assert seen.apart_tiles == 0 and seen.apart_bytes == 0
    w.run(apart=True)
    assert w.keys[F] <= w.stored()


def test_the_images_choice_keeps_the_pieces_of_those_tiles(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tile of the workshop whose tiles folder is a link to another partition (a user's, as
    found on 2026-10-23): its images go with the choice, and the downloaded image pieces of its
    square stay, the levels above too, so that a build of it prepares its images again without
    downloading anything. Before, they count with the images; after, nowhere."""
    w = world
    _apart(monkeypatch, w.packs[A])
    pieces = filed._pieces(w)
    t = texture_at(46.5, 6.5, 14, "BI")
    parent = w.chunks / "BI" / "_parents" / "14" / f"{t.til_y}_{t.til_x}.chunks"
    parent.parent.mkdir(parents=True)
    parent.write_bytes(b"p" * 500)
    a_piece = w.piece(46.5, 6.5, 700)  # only A's square
    seen = w.run(dry_run=True)
    assert seen.apart_tiles == 1
    assert seen.apart_images_bytes == 700 + 500 + 2000  # its own, the level above, astride A and F
    told = w.run(dry_run=True, apart=True)
    assert told.images_bytes == seen.images_bytes - seen.apart_images_bytes
    done = w.run(apart=True, images=True)
    assert done.apart_removed and done.images_removed
    assert a_piece.is_file() and parent.is_file() and pieces["AF"].is_file()
    assert pieces["F"].is_file() and pieces["S"].is_file()  # the filed tiles' choice's
    assert not pieces["none"].exists()
    again = w.run(dry_run=True, images=True)
    assert again.apart_images_bytes == 0  # its images gone before: its pieces count nowhere
    w.run(images=True)
    assert a_piece.is_file() and parent.is_file()


def _refs(w: World, tile: object) -> dict[str, ResolvedInput]:
    """What a build found in the store for the tile's pack step: its inputs' folders."""
    manifest = read_manifest(w.packs[tile])  # type: ignore[index]
    with Store(w.store, fsync=False) as s:
        out = {}
        for label in ("dsf", "textures", "overlay"):
            key = manifest.artefacts[label].key
            out[label] = ResolvedInput(label, s.digest_of(key), s.path(key), key)
        return out


def test_a_pack_whose_textures_folder_went_meanwhile_stops_as_it_is(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build found A's textures folder in the store, then Free space, in another process,
    deleted it: the pack step stops before touching anything, where it took the folder for one
    with no images and emptied the tile (a review, 2026-10-23). A is whole, its manifest too."""
    w = world
    _apart(monkeypatch, w.packs[A])
    refs = _refs(w, A)
    pack = w.packs[A]
    images, manifest = _dds(pack), (pack / "orthostudio.toml").read_bytes()
    w.run(apart=True)
    assert not refs["textures"].path.exists()  # type: ignore[union-attr]
    with Store(w.store, fsync=False) as s, pytest.raises(OsxpError) as err:
        assemble_pack(s, w.tiles, A, provider="BI", zl=16, link=True, **refs)
    assert err.value.code == "MESH_INPUT_MISSING"
    assert _dds(pack) == images and (pack / "orthostudio.toml").read_bytes() == manifest
    assert w.whole(A)


def test_a_pack_whose_textures_folder_lacks_an_image_stops_as_it_is(
    world: World, tmp_path: Path
) -> None:
    """The folder there, one of the images its list names missing (deleted by hand in the
    store): the pack stops too, where the image was swept from the tile as no longer wanted."""
    w = world
    refs = _refs(w, A)
    pack = w.packs[A]
    images = _dds(pack)
    folder = refs["textures"].path
    assert folder is not None
    copy = tmp_path / "folder"
    shutil.copytree(folder, copy)
    victim = sorted((copy / "textures").glob("*.dds"))[0]
    victim.unlink()
    was = refs["textures"]
    refs["textures"] = ResolvedInput("textures", was.digest, copy, was.key)
    with Store(w.store, fsync=False) as s, pytest.raises(OsxpError) as err:
        assemble_pack(s, w.tiles, A, provider="BI", zl=16, link=False, **refs)
    assert err.value.code == "TEX_MISSING"
    assert _dds(pack) == images


def test_a_filed_tile_whose_textures_folder_went_keeps_its_old_version(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F built again in the workshop, then put back in its folder: its textures folder gone
    meanwhile, the pack in the workshop stops, and nothing replaces F's old version, which the
    put back would have deleted with its images."""
    w = world
    _apart(monkeypatch, w.packs[F])
    home = w.packs[F]
    refs = _refs(w, F)
    images = _dds(home)
    w.run(apart=True)
    with Store(w.store, fsync=False) as s, pytest.raises(OsxpError):
        assemble_pack(s, w.tiles, F, provider="BI", zl=16, link=True, **refs)
    assert not (w.tiles / home.name).exists()  # nothing to put back
    assert _dds(home) == images and w.whole(F)


def test_osxp_clean_apart(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """``osxp clean --apart`` says what it frees, and without it what it keeps and how."""
    w = world
    _apart(monkeypatch, w.packs[F])
    monkeypatch.setattr(clean_module, "GRACE_S", 0.0)
    runner = CliRunner()
    store = ["--store", str(w.store), "--chunks", str(w.chunks)]
    kept = runner.invoke(cli_app, ["clean", "--dry-run", *store])
    assert kept.exit_code == 0, kept.output
    assert "tile(s) on another disk" in kept.output and "kept (--apart deletes it" in kept.output
    done = runner.invoke(cli_app, ["clean", "--apart", *store])
    assert done.exit_code == 0, done.output
    assert "images of the 1 tile(s) on another disk: freed" in done.output
    assert not _images(w, F) & w.stored()


@pytest.fixture
def app(world: World, tmp_path: Path) -> Any:
    mgr = JobManager(jobs_dir=tmp_path / "home" / "jobs", build=FakeBuild(), env_factory=None)
    application = create_app(
        env_factory=None,
        jobs=mgr,
        airports=FakeIndex(),
        settings_path=tmp_path / "home" / "config.toml",
    )
    yield application
    mgr.close()


@pytest.mark.anyio
async def test_the_page_offers_the_choice_and_free_space_frees_it_only_when_asked(
    app: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disk space counts them on a line of their own; Free space keeps them unless asked, and
    says what came back."""
    w = world
    _apart(monkeypatch, w.packs[F])
    images = _images(w, F)
    async with client_for(app) as c:
        disk = (await c.get("/api/disk")).json()
        assert disk["apart_tiles"] == 1 and disk["apart_bytes"] > 0
        plain = (await c.post("/api/clean", json={})).json()
        assert plain["apart_freed_bytes"] == 0 and images <= w.stored()
        done = (await c.post("/api/clean", json={"apart": True})).json()
        assert done["apart_freed_bytes"] == disk["apart_bytes"]
        assert (await c.get("/api/disk")).json()["apart_bytes"] == 0
    assert not images & w.stored()
