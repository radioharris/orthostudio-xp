# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The stores earlier versions left in users' data folders, opened by this one.

Each folder of ``tests/data/stores`` is a small store made by the store code of one version, run
as it was released (``git archive`` of its tag, 2026-10-23): the releases since 0.1.22, and the
intermediate states of next a user's store went through that day (the list of images held by tiles
in its first form, which the next form refused to open, "no such column: listed_in"; the list of
trimmed files without digests; the file digests). Each holds a relief and a mesh in both forms, a
vectors stage, an image and its textures folder, pinned; the later ones an image let go of (its
tile's copy beside the store, listed in an ``away`` table this version neither reads nor writes),
a relief trimmed, a file's digest. Opened by this version, a store must keep every artefact
readable and as built, and take everything this version does to it. The fixtures are copied
before being opened: never changed in place.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from orthostudio.graph import Store

STORES = sorted((Path(__file__).parent / "data" / "stores").iterdir())
TILE = "+46+006"


def _columns(store: Store, table: str) -> set[str]:
    return {str(r[1]) for r in store._db.execute(f"PRAGMA table_info({table})")}


@pytest.mark.parametrize("made_by", STORES, ids=[p.name for p in STORES])
def test_a_store_an_earlier_version_left_opens_and_works(made_by: Path, tmp_path: Path) -> None:
    """Every table and column of this version is there after the opening; every artefact is
    there, readable and as it was built; the last build of a step is found; the second forms are
    trimmed, the artefacts still as built; a file's digest is kept, its file's number read again
    once from a row an earlier version wrote; opened again, nothing changed."""
    copy = tmp_path / made_by.name
    shutil.copytree(made_by, copy)
    root, tiles = copy / "store", copy / "tiles"
    with Store(root, fsync=False) as s:
        assert "digest" in _columns(s, "trimmed") and _columns(s, "sources")
        # its format number raised: the versions before this one turn it away at the opening
        assert s._db.execute("SELECT v FROM meta WHERE k = 'schema_version'").fetchone()[0] == "2"
        infos = list(s.iter_artifacts())
        assert len(infos) >= 5 and all(s.has(i.key) for i in infos)
        for info in infos:
            found = s.content_digest(info.key)
            if s.trimmed(info.key) and found is None:
                continue  # trimmed by the list's first form, without the files' digests
            assert found == info.digest, info.rule
        assert s.fsck(verify=True).clean
        assert s.last_made("orthostudio.dem", {"tile": TILE, "n": 1}, 1) is not None
        assert s.last_made("orthostudio.mesh", {"tile": TILE, "n": 1}, 1) is not None
        assert s.pins()  # what was pinned stays pinned

        seconds = {"orthostudio.dem": f"Data{TILE}.alt", "orthostudio.mesh": f"Data{TILE}.mesh"}
        for rule, second in seconds.items():
            for info in s.iter_artifacts(rule):
                if (info.path / second).is_file():
                    assert s.trim(info.key, [second]) > 0, rule
                    assert s.content_digest(info.key) == info.digest
        for info in s.iter_artifacts("orthostudio.vectors"):  # its .alt is not a second form
            assert (info.path / f"Data{TILE}.alt").is_file()
        assert s.fsck(verify=True).clean

        dsf = tiles / "+46+006.dsf"
        dsf.parent.mkdir(parents=True, exist_ok=True)
        dsf.write_bytes(b"XPLNEDSF" + b"\1" * 64)
        digest = s.file_digest(dsf)
        keys = {i.key: i.digest for i in s.iter_artifacts()}
    with Store(root, fsync=False) as s:  # opened again: the same store
        assert {i.key: i.digest for i in s.iter_artifacts()} == keys
        assert s.file_digest(dsf) == digest and s.fsck(verify=True).clean


def test_the_stores_of_each_release_are_here() -> None:
    """A release whose store is not among these is not checked: each release adds its own."""
    names = {p.name for p in STORES}
    assert {"v0.1.22", "v1.0.0", "v1.1.0", "v1.1.1-rc.3"} <= names
    for made_by in STORES:
        assert (made_by / "store" / "index.sqlite").is_file()
        assert not list(made_by.rglob("*-wal")) and not list(made_by.rglob("*-shm"))
