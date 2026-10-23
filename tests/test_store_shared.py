# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The store's files that are a tile's own too, one file under two names, counted once: in the
tile's size, not again in the store's (``clean.pack_disk_bytes``, ``PackFacts.shared``,
``shared_bytes`` of ``GET /api/sizes`` and ``GET /api/disk``). A user's file manager showed the
store folder about twice the size of the tiles, and the page's own figures counted those files
twice: 91 GB for a store adding 40 GB to 62 GB of tiles (2026-10-23). Temporary folders only."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import test_api_fakes as fakes
from orthostudio.api import app as appmod
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.clean import pack_disk_bytes
from orthostudio.graph import Store, artifact_key
from orthostudio.install import Library, default_library_path
from orthostudio.install.library import PackFacts
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import ArtefactEntry, PackManifest, write_pack
from test_api_fakes import FakeBuild, FakeIndex, client_for

anyio_backend = fakes.anyio_backend
home = fakes.home

T = TileRef(43, 5)
DDS = 5000
DSF = 108


def _store(root: Path) -> tuple[str, Path, Path]:
    """A store holding one image (``texture.dds``), the tile's textures folder linking it
    (``tile.textures``) and its DSF (``tile.dsf``), as a build leaves them: the textures folder's
    key and path, and the DSF's folder."""
    with Store(root, fsync=False) as store:
        key, recipe = artifact_key("texture.dds", 1, {"n": 1}, {})
        with store.begin("texture.dds", key, "file") as b:
            b.out.write_bytes(b"D" * DDS)
            b.commit(version=1, recipe=recipe, inputs=[])
        folder, recipe = artifact_key("tile.textures", 1, {"n": 2}, {})
        with store.begin("tile.textures", folder, "dir") as b:
            (b.out / "textures").mkdir()
            os.link(store.path(key), b.out / "textures" / "1_2_BI16.dds")
            b.commit(version=1, recipe=recipe, inputs=[])
        dsf, recipe = artifact_key("tile.dsf", 1, {"n": 3}, {})
        with store.begin("tile.dsf", dsf, "dir") as b:
            (b.out / "terrain").mkdir()
            (b.out / f"{T.name}.dsf").write_bytes(b"XPLNEDSF" + b"\0" * (DSF - 8))
            (b.out / "terrain" / "a.ter").write_text("A\n800\nTERRAIN\n")
            b.commit(version=1, recipe=recipe, inputs=[])
        return folder, store.path(folder), store.path(dsf)


def _pack(where: Path, store: tuple[str, Path, Path], *, link: bool = True) -> Path:
    """A pack of ``T`` laid from ``store``'s artefacts, linked as a build links it on the store's
    disk, or copied as on another."""
    key, textures, dsf = store
    files = write_pack(where, T, dsf_dir=dsf, textures_dir=textures, overlay_file=None, link=link)
    listed = {"dsf": T.dsf_relpath.as_posix(), "dsf_size": DSF, "textures": 1, "terrain": 1}
    artefacts = {"textures": ArtefactEntry(key, "d" * 64, "tile.textures@1")}
    manifest = PackManifest(T.name, "BI", 16, artefacts, listed)
    (files.pack_dir / "orthostudio.toml").write_text(manifest.to_toml())
    return files.pack_dir


def test_a_tile_s_images_kept_by_the_store_count_once(tmp_path: Path) -> None:
    """A tile laid from the store on its disk shares its images with it: they are the store's
    files too. A copy, on another disk or after the cache went, shares nothing; nor does a tile of
    a former data folder on the same disk, which shares with that folder's store, not this one,
    even where this store holds the same build under the same key."""
    root = tmp_path / "data" / "store"
    store = _store(root)
    linked = _pack(tmp_path / "data" / "tiles", store)
    total, shared = pack_disk_bytes(linked, root)
    assert shared == DDS + DSF and total > DDS + DSF  # its terrain files are its own
    copy = _pack(tmp_path / "elsewhere", store, link=False)
    assert pack_disk_bytes(copy, root) == (total, 0)
    other = tmp_path / "new data" / "store"
    _store(other)  # the same build again, under the same key, in another store
    assert pack_disk_bytes(linked, other) == (total, 0)
    assert pack_disk_bytes(linked, None) == (total, 0)


def test_a_library_of_an_older_version_gains_the_new_figures(tmp_path: Path) -> None:
    """What the Library remembers of a tile had no room for the files it shares with the store:
    a library written before keeps its rows, gains the columns, and its facts say nothing of
    them until measured again."""
    path = tmp_path / "library.sqlite"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE pack_facts (path TEXT PRIMARY KEY, stamp TEXT NOT NULL, bytes INTEGER NOT "
        "NULL, photo TEXT, built TEXT, measured_at REAL NOT NULL)"
    )
    db.execute("INSERT INTO pack_facts VALUES ('/t/zOrthoStudio_+43+005', 's', 7, NULL, NULL, 1.0)")
    db.commit()
    db.close()
    with Library(path) as lib:
        (old,) = lib.remembered_facts().values()
        assert (old.bytes, old.shared, old.shared_with) == (7, None, None)
        lib.remember_facts(
            Path("/t/zOrthoStudio_+43+005"), PackFacts("s2", 9, None, None, 2.0, 5, "/s")
        )
        (new,) = lib.remembered_facts().values()
    assert (new.bytes, new.shared, new.shared_with) == (9, 5, "/s")
    with Library(path) as lib:  # opened again: the columns are there already
        assert next(iter(lib.remembered_facts().values())).shared == 5


@pytest.fixture
def app(home: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=None)
    application = create_app(
        env_factory=None, jobs=mgr, airports=FakeIndex(), settings_path=home / "config.toml"
    )
    yield application
    mgr.close()


@pytest.mark.anyio
async def test_the_page_is_told_what_the_store_shares_with_the_tiles(
    app: Any, home: Path, tmp_path: Path
) -> None:
    """The status bar and Disk space count the store less the tiles' own files, measured with
    each tile's size; a tile moved off by hand no longer counts, the store's files its own again.
    Fails on the code before: no ``shared_bytes``."""
    pack = _pack(home / "tiles", _store(home / "store"))
    with Library(default_library_path()) as lib:
        lib.register(T, "BI", 16, pack, "osxp")
    async with client_for(app) as c:
        (row,) = [r for r in (await c.get("/api/library")).json() if r["kind"] == "ortho"]
        assert row["size_pending"] is False
        sizes = (await c.get("/api/sizes")).json()
        disk = (await c.get("/api/disk")).json()
        assert sizes["store_bytes"] >= DDS + DSF and sizes["shared_bytes"] == DDS + DSF
        assert disk["shared_bytes"] == DDS + DSF
        pack.rename(tmp_path / pack.name)
        appmod_sizes = (await c.get("/api/sizes")).json()
    assert appmod_sizes["shared_bytes"] == 0
    assert appmod._shared_bytes(home / "elsewhere") == 0  # measured against this store only
