# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A tile filed elsewhere, built again there with another setting, its folder put in the Trash,
then built again: tests 4.5 and 4.8 of the atelier's campaign, on the owner's Mac and Shadow
(2026-10-06). The first build after the Trash failed with an internal error, and the roads of the
folder put in the Trash stayed drawn beside the tile's new ones.

The graph, the scheduler, the store, the pack, the install, the filing, the put back and the end of
the build are the real ones. The stages up to the DSF, the textures and the roads are stand-ins
that write small files of the shape the pack reads, as the tests of the second pass do.
"""

from __future__ import annotations

import dataclasses
import logging
import shutil
from pathlib import Path
from typing import Any

import pytest

import test_api_fakes as fakes
from orthostudio.graph import Store
from orthostudio.imagery.providers import load_registry
from orthostudio.install import Library
from orthostudio.install import packs as install_packs
from orthostudio.install.packs import SCENERY_PACKS_INI
from orthostudio.install.scenery_packs import SceneryPacks
from orthostudio.model import TileRef
from orthostudio.pipeline import build as buildmod
from orthostudio.pipeline import native
from orthostudio.pipeline.build import BuildEnv, BuildReport, BuildSpec, build_tiles
from orthostudio.pipeline.filing import file_tile
from orthostudio.pipeline.pack import (
    OVERLAY_PACK,
    library_pack,
    links_to,
    overlay_link,
    pack_dir_name,
    pack_is_intact,
    read_manifest,
)
from orthostudio.sched import NodeContext
from test_review2_robustesse_build import _empty_layers

xplane = fakes.xplane

T = TileRef(43, 5)
W = TileRef(43, 4)


def _stand_in(*_args: object) -> Any:
    """A run shaped like ``_vectors_run`` and the others: each node writes small files of its
    artefact's shape, the textures one DDS."""

    def run(ctx: NodeContext) -> Any:
        tile, role = ctx.node_id.split("/", 1)[0], ctx.node_id.rsplit("/", 1)[-1]

        def write(out: Path) -> None:
            if role == "textures":
                (out / "textures").mkdir()
                (out / "textures" / "a.dds").write_bytes(b"DDS " + tile.encode() * 16)
            elif out.is_dir():
                (out / "id").write_text(ctx.node_id, encoding="utf-8")
            else:
                out.write_bytes(b"XPLNEDSF roads of " + tile.encode())

        return ctx.produce(write)

    return run


def _dsf(ctx: Any) -> None:
    """The DSF rule's own body, standing in: the tile's DSF and one terrain file."""
    tile = ctx.params.tile
    (ctx.out / f"{tile}.dsf").write_bytes(b"XPLNEDSF" + tile.encode() * 20)
    (ctx.out / "terrain").mkdir()
    (ctx.out / "terrain" / "a.ter").write_text("A\n800\nTERRAIN\n", encoding="utf-8")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BuildEnv:
    data = tmp_path / "data"
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OSXP_DATA_DIR", str(data))
    triangle = tmp_path / "Triangle4XP"
    triangle.write_bytes(b"")
    monkeypatch.setenv("OSXP_TRIANGLE4XP", str(triangle))
    for name in ("_dem_run", "_vectors_run", "_mesh_run", "_masks_run", "_env_run"):
        monkeypatch.setattr(buildmod, name, _stand_in)
    monkeypatch.setattr(buildmod, "TILE_DSF", dataclasses.replace(buildmod.TILE_DSF, fn=_dsf))
    gs = tmp_path / "Global Scenery"
    for tile in (T, W):
        p = gs / tile.dsf_relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"XPLNEDSF-not-really-" + tile.name.encode())
    return BuildEnv(
        store=Store(data / "store", fsync=False), store_root=data / "store",
        chunks_root=data / "chunks", workdir=data / "work", global_scenery=gs, dsftool=None,
        registry=load_registry(), workers=1, library_path=tmp_path / "library.sqlite",
    )  # fmt: skip


def _build(env: BuildEnv, cs: Path, *tiles: TileRef, zl: int = 14) -> BuildReport:
    """The build the page starts for ``tiles``, asked to install into ``cs``."""
    data = env.store_root.parent
    specs = [
        BuildSpec(
            tile=tile,
            provider="BI",
            zl=zl,
            out_dir=data / "tiles",
            store_root=env.store_root,
            chunks_root=env.chunks_root,
            workdir=env.workdir,
            relief="xplane",
            install=True,
            custom_scenery=cs,
            overlay=True,
            xp12_rasters=False,
        )
        for tile in tiles
    ]
    with native.osm_job(native.OsmJob(fetch=_empty_layers)):
        return build_tiles(specs, cpu_in_threads=True, env=env, handle_sigint=False)


def _file(env: BuildEnv, cs: Path, tile: TileRef, dest: Path) -> Path:
    """*File elsewhere...* of the Library, into ``dest``."""
    pack = env.store_root.parent / "tiles" / pack_dir_name(tile)
    entry = library_pack(tile.name, path=str(pack), library_path=env.library_path)
    file_tile(entry, dest, custom_sceneries=[cs], library_path=env.library_path)
    return dest / pack_dir_name(tile)


def _trash(folder: Path, tmp_path: Path) -> None:
    """Put in the Trash from the Finder or the Explorer: moved away, on the same disk."""
    trash = tmp_path / ".Trash"
    trash.mkdir(exist_ok=True)
    shutil.move(folder, trash / folder.name)


def _lines(cs: Path) -> list[str]:
    """The names of the packs X-Plane's list holds."""
    return SceneryPacks.load(cs / SCENERY_PACKS_INI).names()


def _filed_changed_then_trashed(
    env: BuildEnv, cs: Path, tmp_path: Path, *others: TileRef
) -> tuple[Path, Path]:
    """``T`` (and ``others``) built and installed in the workshop, filed in ``Elsewhere``, ``T``
    built again there at ZL15 (put back, never installed from the workshop), then its folder put in
    the Trash. Returns the workshop's folder of ``T`` and ``Elsewhere``."""
    elsewhere = tmp_path / "Elsewhere"
    elsewhere.mkdir()
    assert all(t.ok and t.installed for t in _build(env, cs, T, *others).tiles)
    for tile in (T, *others):
        _file(env, cs, tile, elsewhere)
    here = env.store_root.parent / "tiles" / pack_dir_name(T)
    filed = elsewhere / pack_dir_name(T)
    assert not here.exists() and links_to(cs / pack_dir_name(T), filed)
    (changed,) = _build(env, cs, T, zl=15).tiles
    assert changed.ok and read_manifest(filed).zl == 15  # put back in its folder
    _trash(filed, tmp_path)
    return here, elsewhere


def test_a_filed_tile_put_in_the_trash_is_built_again_and_installed_at_once(
    env: BuildEnv, xplane: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Its pack is found in the cache and wrote nothing in the workshop, and its install, never
    run for that build, read the folder before the end of the build laid it: "Internal error:
    FileNotFoundError ... orthostudio.toml", the tile out of X-Plane, until a second build."""
    cs = xplane / "Custom Scenery"
    here, _elsewhere = _filed_changed_then_trashed(env, cs, tmp_path)
    with caplog.at_level(logging.ERROR, logger="orthostudio"):
        (tile,) = _build(env, cs, T, zl=15).tiles
    assert tile.ok, tile.error
    assert tile.installed and tile.repaired == ["pack"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    manifest = read_manifest(here)
    assert manifest.zl == 15 and pack_is_intact(here, manifest)
    assert links_to(cs / pack_dir_name(T), here)
    with Library(env.library_path) as lib:
        assert [r.path for r in lib.list(tile=T, kind="ortho")] == [here]
    # and the next build finds it whole, installed: nothing to repair
    (again,) = _build(env, cs, T, zl=15).tiles
    assert again.ok and again.repaired == []


def test_the_roads_of_its_folder_put_in_the_trash_are_not_drawn_beside_its_new_ones(
    env: BuildEnv, xplane: Path, tmp_path: Path
) -> None:
    """The new build of the tile has its roads in the workshop's overlays pack; those of the
    folder put in the Trash stayed in the overlays pack beside it, linked in X-Plane: roads
    flickering, trees and buildings twice. Taken out, that pack, left with no tile's roads, goes
    out of X-Plane too, its link and its line."""
    cs = xplane / "Custom Scenery"
    here, elsewhere = _filed_changed_then_trashed(env, cs, tmp_path)
    old_roads = elsewhere / OVERLAY_PACK / T.dsf_relpath
    old_pack = overlay_link(cs, elsewhere / OVERLAY_PACK)
    assert old_roads.is_file() and old_pack is not None and old_pack.name in _lines(cs)
    (tile,) = _build(env, cs, T, zl=15).tiles
    assert tile.ok, tile.error
    assert (here.parent / OVERLAY_PACK / T.dsf_relpath).is_file()  # its own, drawn
    assert overlay_link(cs, here.parent / OVERLAY_PACK) is not None
    assert not old_roads.exists()
    assert overlay_link(cs, elsewhere / OVERLAY_PACK) is None
    assert not (cs / old_pack.name).exists() and old_pack.name not in _lines(cs)
    with Library(env.library_path) as lib:
        rows = {(r.kind, r.path) for r in lib.list(tile=T)}
    assert rows == {("ortho", here), ("overlay", here.parent / OVERLAY_PACK)}


def test_the_overlays_pack_of_that_folder_stays_for_the_tiles_still_in_it(
    env: BuildEnv, xplane: Path, tmp_path: Path
) -> None:
    """Only the roads of the tile built again go: those of a tile still filed there are drawn."""
    cs = xplane / "Custom Scenery"
    _here, elsewhere = _filed_changed_then_trashed(env, cs, tmp_path, W)
    (tile,) = _build(env, cs, T, zl=15).tiles
    assert tile.ok, tile.error
    assert not (elsewhere / OVERLAY_PACK / T.dsf_relpath).exists()
    assert (elsewhere / OVERLAY_PACK / W.dsf_relpath).is_file()
    kept = overlay_link(cs, elsewhere / OVERLAY_PACK)
    assert kept is not None and kept.name in _lines(cs)


def test_while_x_plane_runs_the_roads_of_that_folder_wait_for_the_next_build(
    env: BuildEnv, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X-Plane may be reading them, and a tile is not deleted while it runs: they stay, and the
    folder's rows with them, for the next build after X-Plane is closed to take them out. The
    tile is not installed meanwhile either, as always while X-Plane runs."""
    cs = xplane / "Custom Scenery"
    here, elsewhere = _filed_changed_then_trashed(env, cs, tmp_path)
    old_roads = elsewhere / OVERLAY_PACK / T.dsf_relpath
    monkeypatch.setattr(install_packs, "xplane_running", lambda: True)
    (tile,) = _build(env, cs, T, zl=15).tiles
    assert not tile.ok and tile.error is not None and tile.error.error is not None
    assert tile.error.role == "install" and tile.error.error["code"] == "XP_RUNNING"
    assert old_roads.is_file()
    with Library(env.library_path) as lib:
        assert elsewhere / pack_dir_name(T) in {r.path for r in lib.list(tile=T, kind="ortho")}
    monkeypatch.setattr(install_packs, "xplane_running", lambda: False)
    (again,) = _build(env, cs, T, zl=15).tiles
    assert again.ok, again.error
    assert not old_roads.exists() and overlay_link(cs, elsewhere / OVERLAY_PACK) is None
    with Library(env.library_path) as lib:
        assert [r.path for r in lib.list(tile=T, kind="ortho")] == [here]
