# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A tile filed elsewhere, built again into the very same files, is not copied again.

Found on the owner's tiles (2026-10-06): +45+006, filed on the Mac's disk, the cache of the tiles
filed outside freed, then built again unchanged. Its neighbours' meshes, gone with that cache, are
inputs of its masks (``nb_*``): the masks came back under another key, the same bytes, so the same
DSF and textures. The manifest the pack step wrote named the new key, its folder's the old one, and
the whole tile was copied back, which X-Plane running refuses. The store, the packs, the filing
and the end of the build are the real ones; only the scheduler answers that every node is built.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from orthostudio.graph import InputRef, ResolvedInput, Store
from orthostudio.install import Library
from orthostudio.install import packs as install_packs
from orthostudio.pipeline import filing
from orthostudio.pipeline.filing import filing_plan
from orthostudio.pipeline.pack import (
    ArtefactEntry,
    assemble_pack,
    lays_the_same,
    library_pack,
    read_manifest,
)
from test_build_repair import _built_again
from test_clean_filed import A, World, _put, _ref


@pytest.fixture
def filed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[World, dict[str, str], Path]:
    """``A`` built, then filed on another disk (copied there)."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OSXP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(install_packs, "xplane_running", lambda: False)
    w = World(tmp_path)
    keys = w.build(A, 1, osm=w.osm)
    w.pack(A, keys, w.tiles)
    home = w.file(A, tmp_path / "Other disk" / "Alps", monkeypatch, away=True)
    return w, keys, home


def _masks_twin(w: World, keys: dict[str, str], tmp_path: Path) -> str:
    """The masks of ``A`` come back under another key, the same bytes, and its DSF leads to them;
    returns the manifest the pack step writes then, walking the DSF's provenance."""
    with Store(w.store, fsync=False) as s:
        twin = _put(s, "orthostudio.masks", 77, files={
            "index.json": b'{"masks": []}', "5760_8496.png": b"p" * 400})  # fmt: skip
        assert s.digest_of(twin) == s.digest_of(keys["masks"]) and twin != keys["masks"]
        s.touch(keys["dsf"], [_ref(s, "mesh", keys["mesh"]), InputRef(
            "masks", s.digest_of(twin), twin)])  # fmt: skip

        def given(name: str, label: str) -> ResolvedInput:
            return ResolvedInput(name, s.digest_of(keys[label]), s.path(keys[label]), keys[label])

        manifest, _files = assemble_pack(
            s, tmp_path / "scratch", A, provider="BI", zl=16, dsf=given("dsf", "dsf"),
            textures=given("textures", "textures"),
            overlay=given("overlay", "overlay"))  # fmt: skip
    assert manifest.keys["masks"] == twin
    return manifest.to_toml()


def test_built_again_into_the_same_files_it_is_not_copied_x_plane_running_or_not(
    filed: tuple[World, dict[str, str], Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, keys, home = filed
    built = _masks_twin(w, keys, tmp_path)
    dsf = home / A.dsf_relpath
    inode = os.stat(dsf).st_ino
    monkeypatch.setattr(filing, "same_disk", lambda a, b: False)  # a copy, were it put back
    monkeypatch.setattr(install_packs, "xplane_running", lambda: True)
    report = _built_again(w, tmp_path, monkeypatch, keys, built)
    (tile,) = report.tiles
    assert tile.ok and tile.repaired == []
    assert os.stat(dsf).st_ino == inode  # not copied
    assert (home / "orthostudio.toml").read_text() == built
    assert not (w.tiles / home.name).exists()  # nothing left in the workshop
    with Library(w.library) as lib:
        (row,) = lib.list(tile=A, kind="ortho")
    assert Path(row.path) == home and row.keys == read_manifest(home).keys
    entry = library_pack(A.name, path=str(home), library_path=w.library)
    assert filing_plan(entry, tmp_path / "Elsewhere")["how"] == "copy"  # whole, as it is


def test_another_decal_texture_or_a_folder_not_whole_are_other_files(
    filed: tuple[World, dict[str, str], Path],
) -> None:
    """What the put back is left to: a build of other files, or a folder that lacks some."""
    _w, _keys, home = filed
    own = read_manifest(home)
    assert lays_the_same(home, own)
    decal = own.files | {"decal": "lib/g10/decals/grass.dcl"}
    assert not lays_the_same(home, replace(own, files=decal))
    other = ArtefactEntry("e" * 64, "f" * 64, "tile.textures@1")
    assert not lays_the_same(home, replace(own, artefacts=own.artefacts | {"textures": other}))
    os.unlink(sorted((home / "textures").glob("*.dds"))[0])
    assert not lays_the_same(home, own)
