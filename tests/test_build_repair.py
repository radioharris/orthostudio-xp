# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A tile repaired by a build that found everything in the cache keeps its build's own manifest.

Found on the owner's tiles (2026-10-06): +44+006 built, then +44+005 built beside it; the masks of
+44+006 take their neighbours as inputs (``nb_w``), so their next build was another artefact of the
same bytes. A texture of +44+006 missing, its next build repaired the pack: assembling walked the
DSF's provenance, which led to the new masks, and wrote that manifest, while the Library kept the
build's. The tile could then never be filed (taken for another build), and each build assembled it
again. The store, the pack and the end of the build here are the real ones; only the scheduler
answers that every node is already built.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import test_api_fakes as fakes
from orthostudio.graph import InputRef, Store
from orthostudio.imagery.providers import load_registry
from orthostudio.install import Library
from orthostudio.install import packs as install_packs
from orthostudio.model import ArtifactRef
from orthostudio.pipeline import build as build_mod
from orthostudio.pipeline import native
from orthostudio.pipeline.build import BuildEnv, BuildSpec
from orthostudio.pipeline.filing import filing_plan
from orthostudio.pipeline.pack import install_receipt, library_pack, pack_is_intact, read_manifest
from orthostudio.sched import Done, Scheduler
from test_clean_filed import A, World, _put, _ref

xplane = fakes.xplane


def _built_again(
    w: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    keys: dict[str, str],
    manifest: str | None = None,
    *,
    installed: Path | None = None,
    custom_scenery: Path | None = None,
) -> Any:
    """``build_tiles`` as the app runs it for ``A``, every node answered as already built with
    the store's artefacts, the pack node with the manifest its pack step wrote (``manifest``, or
    the one of ``A``'s folder), and the install node, when the build installs into
    ``custom_scenery``, with the receipt its install step wrote (``installed``)."""
    receipt = tmp_path / "pack-artefact.toml"
    receipt.write_text(manifest or (w.packs[A] / "orthostudio.toml").read_text())
    dummy = tmp_path / "dummy"
    dummy.write_text("x")
    store = Store(w.store, fsync=False)
    refs = {"tile.pack": ArtifactRef("c" * 64, "d" * 64, receipt, "tile.pack", "file")}
    if installed is not None:
        refs["tile.install"] = ArtifactRef("e" * 64, "f" * 64, installed, "tile.install", "file")
    for rule, label, kind in (("tile.dsf", "dsf", "dir"), ("tile.textures", "textures", "dir"),
                              ("tile.overlay", "overlay", "file")):  # fmt: skip
        key = keys[label]
        refs[rule] = ArtifactRef(key, store.digest_of(key), store.path(key), rule, kind)

    async def every_node_built(self: Any, targets: Any, on_event: Any = None) -> dict[str, Any]:
        for node in list(self.nodes.values()):
            ref = refs.get(node.rule.name) or ArtifactRef(
                "a" * 64, "b" * 64, dummy, node.rule.name, "file"
            )
            on_event(Done(node.id, ref.key, True, 0.0, ref))
        return {}

    gs = tmp_path / "Global Scenery"
    (gs / A.dsf_relpath).parent.mkdir(parents=True, exist_ok=True)
    (gs / A.dsf_relpath).write_bytes(b"XPLNEDSF-not-really")
    env = BuildEnv(
        store=store, store_root=w.store, chunks_root=w.chunks, workdir=tmp_path / "work",
        global_scenery=gs, dsftool=None, registry=load_registry(), workers=1,
        library_path=w.library,
    )  # fmt: skip
    spec = BuildSpec(
        tile=A, provider="BI", zl=16, out_dir=w.tiles, store_root=w.store, chunks_root=w.chunks,
        workdir=tmp_path / "work", relief="xplane", install=installed is not None,
        custom_scenery=custom_scenery, overlay=True, xp12_rasters=False,
    )  # fmt: skip
    monkeypatch.setattr(Scheduler, "run", every_node_built)
    monkeypatch.setattr(build_mod, "snapshot_label_of", lambda p: "x")
    with native.osm_job(native.OsmJob(fetch=lambda tile, layers: {})):
        return build_mod.build_tiles([spec], cpu_in_threads=True, env=env, handle_sigint=False)


def test_a_repaired_tile_keeps_its_build_s_manifest_and_can_be_filed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OSXP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(install_packs, "xplane_running", lambda: False)
    w = World(tmp_path)
    keys = w.build(A, 1, osm=w.osm)
    pack = w.pack(A, keys, w.tiles)
    built = read_manifest(pack)
    # a neighbour built since: the masks are another artefact of the same bytes, and the DSF,
    # found again in the cache, now leads to it
    with Store(w.store, fsync=False) as s:
        twin = _put(s, "orthostudio.masks", 77, files={
            "index.json": b'{"masks": []}', "5760_8496.png": b"p" * 400})  # fmt: skip
        assert s.digest_of(twin) == s.digest_of(keys["masks"]) and twin != keys["masks"]
        s.touch(keys["dsf"], [_ref(s, "mesh", keys["mesh"]), InputRef(
            "masks", s.digest_of(twin), twin)])  # fmt: skip
    lost = sorted((pack / "textures").glob("*.dds"))[0]
    os.unlink(lost)  # a texture gone: the build repairs the tile
    report = _built_again(w, tmp_path, monkeypatch, keys)
    (tile,) = report.tiles
    assert tile.ok and "pack" in tile.repaired
    assert lost.is_file()
    assert read_manifest(pack) == built  # the build's own, not the one the store leads to now
    with Library(w.library) as lib:
        (row,) = [r for r in lib.list(tile=A, kind="ortho")]
    assert row.keys == built.keys
    assert pack_is_intact(pack, built)
    entry = library_pack(A.name, path=str(pack), library_path=w.library)
    assert filing_plan(entry, tmp_path / "Elsewhere")["how"] != "not_whole"
    # and the next build finds it whole: nothing to repair
    report = _built_again(w, tmp_path, monkeypatch, keys)
    assert report.tiles[0].repaired == []


def _row(w: World) -> Any:
    """The Library's one row of ``A``'s tile."""
    with Library(w.library) as lib:
        (row,) = lib.list(tile=A, kind="ortho")
    return row


def test_a_build_found_whole_in_the_cache_puts_its_own_keys_in_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, xplane: Path
) -> None:
    """Built with its airports sharp, then without, then sharp again (the owner's +44+009,
    2026-10-06): the third build is found whole in the cache, its install step too, and the
    tile's row kept the second build's keys. The Library then took the tile's own folder for
    another build, File elsewhere asked to build it again, and building it again changed
    nothing: the row was so since 0.1.0, and nothing read it before the atelier."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OSXP_DATA_DIR", str(tmp_path / "data"))
    cs = xplane / "Custom Scenery"
    w = World(tmp_path)
    sharp_keys = w.build(A, 1, osm=w.osm)
    pack = w.pack(A, sharp_keys, w.tiles)
    sharp = read_manifest(pack)
    sharp_text = (pack / "orthostudio.toml").read_text()
    installed = tmp_path / "install-artefact.json"
    receipt = install_receipt(pack, cs, tile=A, library_path=w.library)
    installed.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    roads = Path(receipt["overlay_target"]) / A.dsf_relpath
    sharp_roads = roads.read_bytes()
    # the second build, in the same folder, installed the same way
    w.pack(A, w.build(A, 2, osm=w.osm, remember=False), w.tiles)
    install_receipt(pack, cs, tile=A, library_path=w.library)
    assert _row(w).keys != sharp.keys and roads.read_bytes() != sharp_roads

    def third() -> Any:
        report = _built_again(
            w, tmp_path, monkeypatch, sharp_keys, sharp_text, installed=installed, custom_scenery=cs
        )
        (tile,) = report.tiles
        assert tile.ok and tile.installed
        return tile

    assert third().repaired == ["pack"]  # the folder held the second build: laid again
    assert read_manifest(pack) == sharp and pack_is_intact(pack, sharp)
    assert _row(w).keys == sharp.keys
    assert roads.read_bytes() == sharp_roads  # X-Plane reads the first build's roads again
    entry = library_pack(A.name, path=str(pack), library_path=w.library)
    assert filing_plan(entry, tmp_path / "Elsewhere")["how"] != "not_whole"
    # the next build of it finds the tile whole and its row right: nothing to do
    assert third().repaired == []
    assert _row(w).keys == sharp.keys
