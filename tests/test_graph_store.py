"""Store: atomic writes, crash safety, index/disk healing, pins, refcounts, GC, why."""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path

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
