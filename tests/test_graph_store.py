# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Store: atomic writes, crash safety, index/disk healing, pins, refcounts, GC, why."""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from orthostudio.graph import (
    SCHEMA_VERSION,
    ArtifactInUseError,
    CommitError,
    IncompatibleIndexError,
    InputRef,
    MissingArtifactError,
    Store,
    artifact_key,
    digest_bytes,
    digest_path,
)
from orthostudio.graph.store import TMP_MARKER

RULE = "vectors"


def _key(n: int, rule: str = RULE, inputs: dict[str, str | None] | None = None) -> tuple[str, str]:
    return artifact_key(rule, 1, {"n": n}, inputs or {})


def _put(
    store: Store,
    n: int,
    data: bytes = b"payload",
    *,
    rule: str = RULE,
    inputs: list[InputRef] | None = None,
) -> str:
    key, recipe = _key(n, rule, {i.name: i.digest for i in (inputs or [])})
    with store.begin(rule, key, "file") as b:
        b.out.write_bytes(data)
        b.commit(version=1, recipe=recipe, inputs=inputs or [])
    return key


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "store", fsync=False) as s:
        yield s


def test_layout_and_file_artifact(store: Store) -> None:
    key = _put(store, 1, b"hello")
    p = store.path(key)
    assert p == store.root / RULE / key[:2] / key
    assert p.read_bytes() == b"hello"
    info = store.info(key)
    assert info is not None
    assert (info.size, info.kind, info.digest) == (5, "file", digest_bytes(b"hello"))
    assert store.has(key) and len(store) == 1 and store.total_size() == 5
    assert not list(store._iter_tmp())


def test_dir_artifact_digest_is_manifest_based(store: Store, tmp_path: Path) -> None:
    key, recipe = _key(7, "mesh")
    with store.begin("mesh", key, "dir") as b:
        (b.out / "b.bin").write_bytes(b"22")
        (b.out / "sub").mkdir()
        (b.out / "sub" / "a.bin").write_bytes(b"1")
        (b.scratch / "junk").write_bytes(b"x" * 100)
        info, adopted = b.commit(version=1, recipe=recipe, inputs=[])
    assert not adopted
    assert info.kind == "dir" and info.size == 3
    assert store.path(key).is_dir()
    assert digest_path(store.path(key)) == (info.digest, 3)
    # same content in another directory gives the same digest (order independent)
    other = tmp_path / "other"
    (other / "sub").mkdir(parents=True)
    (other / "sub" / "a.bin").write_bytes(b"1")
    (other / "b.bin").write_bytes(b"22")
    assert digest_path(other)[0] == info.digest
    # scratch never leaks into the artefact
    assert not (store.path(key) / "junk").exists()


def test_commit_requires_output_of_the_declared_kind(store: Store) -> None:
    key, recipe = _key(1)
    with pytest.raises(CommitError), store.begin(RULE, key, "file") as b:
        b.commit(version=1, recipe=recipe, inputs=[])
    assert not store.has(key)
    assert not list(store._iter_tmp())


def test_abort_or_exception_leaves_nothing(store: Store) -> None:
    key, _ = _key(2)
    with pytest.raises(RuntimeError), store.begin(RULE, key, "file") as b:
        b.out.write_bytes(b"half")
        raise RuntimeError("boom")
    assert not store.has(key)
    assert not list(store._iter_tmp())
    assert not store.artifact_path(RULE, key).exists()


def test_abandoned_tmp_is_ignored_then_swept(tmp_path: Path) -> None:
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        key, _ = _key(3)
        build = s.begin(RULE, key, "file")  # never committed: simulates a crash mid-write
        build.out.write_bytes(b"partial")
        tmp_dir = build.tmp_dir
        assert TMP_MARKER in tmp_dir.name
        assert not s.has(key)
        assert s.fsck().tmp_leftovers == (tmp_dir,)
        assert s.sweep_tmp(max_age_s=3600) == []  # too young to be swept
    old = time.time() - 7200
    os.utime(tmp_dir, (old, old))
    with Store(root, fsync=False) as s:  # our own (live) pid: kept however old
        assert tmp_dir.exists()
        assert s.sweep_tmp(max_age_s=0) == []
        assert s.sweep_tmp(max_age_s=0, hard_max_age_s=3600) == [tmp_dir]  # hard cap
    build2 = Store(root, fsync=False).begin(RULE, key, "file")
    dead = build2.tmp_dir.with_name(build2.tmp_dir.name.replace(f"-{os.getpid()}-", "-4194999-"))
    os.rename(build2.tmp_dir, dead)  # the crashed process is gone
    os.utime(dead, (old, old))
    with Store(root, fsync=False) as s:  # opening sweeps stale tmp dirs of dead owners
        assert not dead.exists()
        assert not s.has(key)
        assert s.fsck().clean


def test_row_without_files_heals_on_has(store: Store) -> None:
    key = _put(store, 4)
    store.path(key).unlink()
    assert store.fsck().rows_without_files == (key,)
    assert not store.has(key)
    assert store.info(key) is None
    with pytest.raises(MissingArtifactError):
        store.path(key)


def test_files_without_row_are_adopted_on_next_commit(store: Store) -> None:
    key = _put(store, 5, b"first")
    # simulate a crash between rename and index insert: the row vanishes, the file stays
    with store._tx():
        store._db.execute("DELETE FROM artifacts WHERE key = ?", (key,))
    assert not store.has(key)
    assert store.fsck().files_without_rows == (store.artifact_path(RULE, key),)
    _, recipe = _key(5)
    with store.begin(RULE, key, "file") as b:
        b.out.write_bytes(b"second")
        info, adopted = b.commit(version=1, recipe=recipe, inputs=[])
    assert adopted
    assert store.path(key).read_bytes() == b"first"
    assert info.digest == digest_bytes(b"first")
    assert store.fsck().clean


def test_fsck_repair_and_verify(store: Store) -> None:
    key = _put(store, 6, b"good")
    store.path(key).write_bytes(b"corrupted")
    rep = store.fsck(verify=True)
    assert rep.digest_mismatches == (key,)
    rep = store.fsck(repair=True, verify=True)
    assert rep.digest_mismatches == (key,)
    assert not store.has(key) and store.fsck(verify=True).clean


def test_reopen_persists_and_schema_is_checked(tmp_path: Path) -> None:
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        key = _put(s, 8, b"persist")
    with Store(root, fsync=False) as s:
        assert s.has(key) and s.path(key).read_bytes() == b"persist"
        assert s.info(key) is not None
    db = sqlite3.connect(root / "index.sqlite")
    db.execute("UPDATE meta SET v = ? WHERE k = 'schema_version'", (str(SCHEMA_VERSION + 1),))
    db.commit()
    db.close()
    with pytest.raises(IncompatibleIndexError):
        Store(root, fsync=False)


def test_wal_mode_is_on(store: Store) -> None:
    assert store._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_fsync_path_works(tmp_path: Path) -> None:
    with Store(tmp_path / "s", fsync=True) as s:
        key = _put(s, 9, b"synced")
        assert s.path(key).read_bytes() == b"synced"


def test_a_write_waits_for_an_index_held_longer_than_sqlites_own_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQLite gives up after its ``busy_timeout``, and a build on a hard disk held the index
    longer: 12 textures of 695 failed "database is locked", their tile with them (2026-09-28).
    A write asks again, and goes through once the index is free."""
    from orthostudio.graph import store as store_mod

    monkeypatch.setattr(store_mod, "INDEX_LOCKED_PATIENCE_S", 20.0)
    with Store(tmp_path / "store", fsync=False) as s:
        s._db.execute("PRAGMA busy_timeout = 100")  # SQLite's own wait: 30 s in a build
        other = sqlite3.connect(
            s.root / "index.sqlite", isolation_level=None, check_same_thread=False
        )
        other.execute("BEGIN IMMEDIATE")  # another process holding the index
        release = threading.Timer(1.5, lambda: other.execute("COMMIT"))
        release.start()
        started = time.monotonic()
        key = _put(s, 1)
        waited = time.monotonic() - started
        release.join()
        other.close()
        assert s.has(key) and waited >= 1.0, waited


def test_a_write_to_an_index_held_too_long_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The patience has an end: a stuck index is an error, not a build that never ends."""
    from orthostudio.graph import store as store_mod

    monkeypatch.setattr(store_mod, "INDEX_LOCKED_PATIENCE_S", 0.5)
    with Store(tmp_path / "store", fsync=False) as s:
        s._db.execute("PRAGMA busy_timeout = 100")
        other = sqlite3.connect(s.root / "index.sqlite", isolation_level=None)
        other.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            _put(s, 1)
        assert time.monotonic() - started < 5.0
        other.execute("ROLLBACK")
        other.close()


def test_a_texture_linked_from_the_store_is_not_forced_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tile's textures folder holds a hard link to each DDS of the store, whose data was forced
    when it was committed: forcing every link again protected nothing and made a hard disk wait
    once more per texture (2026-09-28). A copy, and any other file, is forced as before."""
    from orthostudio.graph import store as store_mod

    forced: list[str] = []
    monkeypatch.setattr(store_mod, "_fsync_file", lambda p: forced.append(p.name))
    stored = tmp_path / "stored.dds"
    stored.write_bytes(b"DDS |")
    tile = tmp_path / "tile"
    (tile / "textures").mkdir(parents=True)
    (tile / "terrain").mkdir()
    os.link(stored, tile / "textures" / "linked.dds")
    (tile / "textures" / "copied.dds").write_bytes(b"DDS |")
    (tile / "terrain" / "one.ter").write_text("A\n")
    store_mod._fsync_tree(tile)
    assert sorted(forced) == ["copied.dds", "one.ter"]


def _put_dds(store: Store, n: int, data: bytes) -> str:
    return _put(store, n, data, rule="texture.dds")


def test_a_texture_is_committed_unforced_and_forced_by_the_durable_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forcing each texture as it was written held a build on a hard disk (2026-09-28): the
    textures are forced as a group at the durable point, and the few artefacts of other rules as
    before. A second point forces nothing already forced."""
    from orthostudio.graph import store as store_mod

    monkeypatch.setattr(store_mod, "DURABLE_MARGIN_S", 0.0)
    forced: list[str] = []
    monkeypatch.setattr(
        store_mod,
        "_fsync_file",
        lambda p: forced.append(next(x for x in p.parts if x in (RULE, "texture.dds"))),
    )
    with Store(tmp_path / "store", fsync=True) as s:
        before = s.durable_until()
        assert before is not None
        _put_dds(s, 1, b"DDS one")
        _put(s, 2, b"vectors")
        assert forced == [RULE], "the texture waits for the durable point"
        time.sleep(0.01)
        assert s.make_durable() == 1 and forced == [RULE, "texture.dds"]
        after = s.durable_until()
        assert after is not None and after > before
        time.sleep(0.01)
        assert s.make_durable() == 0 and forced == [RULE, "texture.dds"]


def test_what_an_interrupted_build_left_torn_is_dropped_at_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A power cut before the durable point can leave a texture's bytes unwritten: the next
    build checks every texture indexed since the point against its digest, drops the torn one
    and keeps the whole one; then the point moves and nothing is checked twice."""
    from orthostudio.graph import store as store_mod

    monkeypatch.setattr(store_mod, "DURABLE_MARGIN_S", 0.0)
    with Store(tmp_path / "store", fsync=True) as s:
        whole = _put_dds(s, 1, b"DDS whole texture")
        torn = _put_dds(s, 2, b"DDS torn texture!")
        path = s.path(torn)
    path.write_bytes(bytes(path.stat().st_size))  # what the disk kept of it: zeros
    time.sleep(0.01)
    with Store(tmp_path / "store", fsync=True) as s:
        report = s.recover()
        assert report.checked == 2 and report.dropped == (torn,)
        assert s.has(whole) and not s.has(torn) and not path.exists()
        time.sleep(0.01)
        assert s.recover().checked == 0


def test_a_texture_found_on_disk_without_its_row_is_kept_only_when_it_is_the_same(
    tmp_path: Path,
) -> None:
    """Renamed into place, then a power cut before its row: the file may be torn. Building the
    same key again, the store keeps the one on disk when it is byte for byte what it has just
    built, and puts its own in place of any other."""
    with Store(tmp_path / "store", fsync=False) as s:
        key, recipe = _key(7, "texture.dds")
        final = s.artifact_path("texture.dds", key)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(bytes(9))  # torn: zeros where the texture was
        with s.begin("texture.dds", key, "file") as b:
            b.out.write_bytes(b"DDS bytes")
            info, adopted = b.commit(version=1, recipe=recipe, inputs=[])
        assert not adopted and final.read_bytes() == b"DDS bytes"
        assert info.digest == digest_bytes(b"DDS bytes")
        assert not [p for p in final.parent.iterdir() if p != final], "nothing left aside"
        key2, recipe2 = _key(8, "texture.dds")
        final2 = s.artifact_path("texture.dds", key2)
        final2.parent.mkdir(parents=True, exist_ok=True)
        final2.write_bytes(b"DDS same")  # whole, only its row lost
        with s.begin("texture.dds", key2, "file") as b:
            b.out.write_bytes(b"DDS same")
            _info, adopted2 = b.commit(version=1, recipe=recipe2, inputs=[])
        assert adopted2 and final2.read_bytes() == b"DDS same"


def test_a_store_without_a_durable_point_counts_what_it_holds_as_durable(tmp_path: Path) -> None:
    """Every version before this one forced each file as it was written: a store it left behind
    gets a durable point on opening, and nothing of it is checked."""
    from orthostudio.graph import store as store_mod

    with Store(tmp_path / "store", fsync=True) as s:
        _put_dds(s, 1, b"DDS old")
    (tmp_path / "store" / store_mod.DURABLE_FILE).unlink()
    time.sleep(0.01)
    with Store(tmp_path / "store", fsync=True) as s:
        assert s.durable_until() is not None and s.recover().checked == 0


def test_pins_refcount_and_delete(store: Store) -> None:
    parent = _put(store, 10, b"parent")
    d = store.digest_of(parent)
    child = _put(store, 11, b"child", rule="mesh", inputs=[InputRef("vectors", d, parent)])
    assert store.refcount(parent) == 1 and store.refcount(child) == 0
    assert store.dependents(parent) == [(child, "vectors")]
    store.pin("tile:+43+005", child)
    assert store.pins() == {"tile:+43+005": child}
    assert store.refcount(child) == 1
    with pytest.raises(ArtifactInUseError):
        store.delete(parent)
    with pytest.raises(MissingArtifactError):
        store.pin("x", "00" * 32)
    assert store.delete(parent, force=True)
    assert not store.has(parent)
    # the edge survives with its digest but no parent key
    prov = store.why(child)
    assert prov.inputs == (InputRef("vectors", d, None),)
    assert store.unpin("tile:+43+005") and not store.unpin("tile:+43+005")
    assert store.delete(child) and not store.delete(child)


def test_gc_collects_unreferenced_lru_first_and_cascades(store: Store) -> None:
    a = _put(store, 20, b"a" * 100)
    b = _put(store, 21, b"b" * 100, rule="mesh", inputs=[InputRef("v", store.digest_of(a), a)])
    c = _put(store, 22, b"c" * 100)  # unreferenced, unpinned
    store.pin("keep", b)
    rep = store.gc()
    assert rep.deleted == (c,) and rep.freed_bytes == 100
    assert store.has(a) and store.has(b)
    store.unpin("keep")
    rep = store.gc()
    assert set(rep.deleted) == {a, b} and len(store) == 0
    assert rep.size_after == 0


def test_gc_quota_stops_when_it_fits_and_respects_min_age(store: Store) -> None:
    keys = [_put(store, 30 + i, bytes([i]) * 100) for i in range(4)]
    store.touch(keys[0])  # most recently used -> last candidate
    rep = store.gc(quota_bytes=250)
    assert rep.size_after <= 250 and len(rep.deleted) == 2
    assert keys[0] not in rep.deleted
    rep = store.gc(quota_bytes=0, min_age_s=3600)
    assert rep.deleted == ()  # everything is too young
    rep = store.gc(quota_bytes=0)
    assert len(rep.deleted) == 2 and len(store) == 0


def test_why_and_explain(store: Store) -> None:
    src_digest = digest_bytes(b"osm")
    v = _put(store, 40, b"vec", inputs=[InputRef("osm", src_digest), InputRef("dem", None)])
    m = _put(store, 41, b"mesh", rule="mesh", inputs=[InputRef("vectors", store.digest_of(v), v)])
    store.pin("tile:test", m)
    prov = store.why(v)
    assert prov.info.rule == RULE and prov.params == {"n": 40}
    assert prov.inputs == (InputRef("dem", None), InputRef("osm", src_digest))
    assert prov.dependents == ((m, "vectors"),) and prov.refcount == 1
    text = store.explain(m, depth=1)
    assert "mesh@1" in text and "pins: tile:test" in text
    assert "vectors@1" in text and "<- source" in text and "<- absent" in text
    with pytest.raises(MissingArtifactError):
        store.why("00" * 32)


def test_touch_updates_lru_and_edges(store: Store) -> None:
    a = _put(store, 50)
    b = _put(store, 51, b"other")
    c = _put(store, 52, b"c", rule="mesh", inputs=[InputRef("v", store.digest_of(a), a)])
    before = store.info(c)
    assert before is not None
    time.sleep(0.01)
    store.touch(c, [InputRef("v", store.digest_of(b), b)])
    after = store.info(c)
    assert after is not None
    assert after.uses == before.uses + 1 and after.last_used_at > before.last_used_at
    assert store.refcount(a) == 0 and store.refcount(b) == 1
    with pytest.raises(MissingArtifactError):
        store.touch("00" * 32)


def test_iter_artifacts_and_keys_with_digest(store: Store) -> None:
    a = _put(store, 60, b"same")
    b = _put(store, 61, b"same")
    _put(store, 62, b"same", rule="mesh")
    assert store.keys_with_digest(RULE, digest_bytes(b"same")) == [a, b]
    assert [i.key for i in store.iter_artifacts(RULE)] == [a, b]
    assert len(list(store.iter_artifacts())) == 3


def test_symlinks_are_refused_in_artifacts(store: Store, tmp_path: Path) -> None:
    key, recipe = _key(70, "mesh")
    target = tmp_path / "target"
    target.write_bytes(b"t")
    with pytest.raises(OSError), store.begin("mesh", key, "dir") as b:
        (b.out / "link").symlink_to(target)
        b.commit(version=1, recipe=recipe, inputs=[])
    assert not store.has(key)
    shutil.rmtree(tmp_path / "unused", ignore_errors=True)


_RACER = """
import sys, time
from orthostudio.graph import Store, artifact_key
root, n = sys.argv[1], int(sys.argv[2])
key, recipe = artifact_key("race", 1, {"n": n}, {})
with Store(root, fsync=False) as s:
    with s.begin("race", key, "dir") as b:
        (b.out / "a.bin").write_bytes(b"same content" * 1000)
        time.sleep(0.05)  # widen the window where every racer holds a tmp dir
        info, adopted = b.commit(version=1, recipe=recipe, inputs=[])
print(info.digest, int(adopted))
"""


def test_concurrent_processes_building_the_same_key_agree(tmp_path: Path) -> None:
    import subprocess
    import sys

    root = tmp_path / "store"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _RACER, str(root), "1"], stdout=subprocess.PIPE, text=True
        )
        for _ in range(4)
    ]
    outputs = [p.communicate()[0].split() for p in procs]
    assert all(p.returncode == 0 for p in procs)
    digests = {o[0] for o in outputs}
    assert len(digests) == 1
    adopted = sum(int(o[1]) for o in outputs)
    assert adopted >= 1  # at least one racer found the artefact already on disk
    with Store(root, fsync=False) as s:
        key, _ = artifact_key("race", 1, {"n": 1}, {})
        assert s.has(key) and len(s) == 1
        info = s.info(key)
        assert info is not None and info.uses == 4 and info.digest in digests
        assert s.fsck(verify=True).clean


def test_a_deletion_stopped_half_way_leaves_no_half_artefact(store: Store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Removing an artefact's files takes many system calls and anything can stop them: a second
    Ctrl-C, the app quitting while Free space runs, one ``.dds`` held open by X-Plane or by an
    antivirus. The row went first, so what was left behind was a half-emptied artefact at the
    name a finished one has, and the next build of that tile adopted it: it threw away what it
    had just built correctly, indexed the remains, and reported success. A pack short of its
    textures is grey ground in X-Plane, and the key is a hit for ever (found in review,
    2026-09-23).
    """
    from orthostudio.graph import store as store_module

    files = ("manifest.json", "0.dds", "1.dds", "2.dds")
    key, recipe = _key(9, rule="tile.textures")

    def build() -> tuple[Any, bool]:
        with store.begin("tile.textures", key, "dir") as b:
            for name in files:
                (b.out / name).write_bytes(name.encode() * 100)
            return b.commit(version=1, recipe=recipe, inputs=[])

    whole, _ = build()
    final = store.artifact_path("tile.textures", key)

    def stops_half_way(path: Any, *args: Any, **kw: Any) -> None:
        for child in sorted(Path(path).iterdir())[:2]:
            child.unlink()
        raise PermissionError(13, "held by another program", str(path))

    monkeypatch.setattr(store_module.shutil, "rmtree", stops_half_way)
    with pytest.raises(PermissionError):
        store.delete(key, force=True)

    assert not final.exists(), "nothing half-emptied is left at the artefact's own name"
    monkeypatch.undo()

    again, adopted = build()
    assert not adopted, "the build keeps what it built rather than adopting remains"
    assert again.digest == whole.digest
    assert sorted(p.name for p in store.path(key).iterdir()) == sorted(files)
    assert store.sweep_tmp(hard_max_age_s=0.0), "and the remains are swept like any tmp directory"
    assert store.fsck().clean
