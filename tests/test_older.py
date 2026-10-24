# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The cache made ready for an older version, before going back to it (``orthostudio.older``).

Since the relief and the mesh are kept once, an earlier version finds neither ``Data<tile>.alt``
nor ``Data<tile>.mesh`` in their artefacts (nor a neighbour's mesh, which draws the shores along a
border), and refuses an index of format 2. The Library's *Prepare for an older version…* writes
back every second form Free space trimmed, each taken back only as it was built, gives the
artefacts built in one form their second form, removes what cannot be written, gives the index the
format 1 and stops the engine. The stores the releases left (``tests/data/stores``) give the second
forms their own writers made: each must come back byte for byte. Temporary folders only.
"""

from __future__ import annotations

import errno
import shutil
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import numpy as np
import pytest

import test_api_fakes as fakes
from orthostudio import older
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.graph import Store, artifact_key
from orthostudio.graph import store as store_module
from orthostudio.graph.errors import StoreError
from orthostudio.mesh.mesh_file import MeshData, write_mesh_npz, write_mesh_text
from orthostudio.model import TileRef
from orthostudio.older import OlderStoppedError, plan_older, prepare_older
from orthostudio.pipeline.build import BuildEnv
from test_api_fakes import FakeBuild, FakeIndex, client_for

anyio_backend = fakes.anyio_backend
home = fakes.home

STORES = sorted((Path(__file__).parent / "data" / "stores").iterdir())
T = TileRef(46, 6)
SECONDS = {"orthostudio.dem": ".alt", "orthostudio.mesh": ".mesh"}


@pytest.fixture(autouse=True)
def _reopen() -> Iterator[None]:
    """A store prepared here is refused for the rest of the process: the next test's are not."""
    yield
    store_module._CLOSED_FOR_OLDER.clear()


def _format(root: Path) -> str:
    db = sqlite3.connect(root / "index.sqlite")
    try:
        return str(db.execute("SELECT v FROM meta WHERE k = 'schema_version'").fetchone()[0])
    finally:
        db.close()


def _seconds(store: Store) -> dict[tuple[str, str], bytes]:
    """The second forms the store holds, by artefact and name, with their bytes."""
    out: dict[tuple[str, str], bytes] = {}
    for rule, suffix in SECONDS.items():
        for info in store.iter_artifacts(rule):
            for path in info.path.glob(f"Data*{suffix}"):
                out[(info.key, path.name)] = path.read_bytes()
    return out


def _put(store: Store, rule: str, n: int, files: dict[str, Any]) -> str:
    key, recipe = artifact_key(rule, 1, {"n": n, "tile": T.name}, {})
    with store.begin(rule, key, "dir") as b:
        for name, content in files.items():
            content(b.out / name) if callable(content) else (b.out / name).write_bytes(content)
        b.commit(version=1, recipe=recipe, inputs=[])
    return key


def _raster(n: int) -> np.ndarray:
    return np.random.default_rng(n).uniform(0, 3000, (33, 33)).astype(np.float32)


def _relief(store: Store, n: int, *, second: bool) -> str:
    """A relief artefact as a version before 2026-10-23 wrote it (``second``), or as this one."""
    raster = _raster(n)
    files: dict[str, Any] = {
        "dem.npy": lambda p: np.save(p, raster, allow_pickle=False),
        "meta.json": b'{"format": "osxp-dem-1"}',
    }
    if second:
        files[f"Data{T.name}.alt"] = raster.tobytes()
    return _put(store, "orthostudio.dem", n, files)


def _mesh_data(n: int) -> MeshData:
    rng = np.random.default_rng(n)
    k = 40
    vertices = np.column_stack([6 + rng.random(k), 46 + rng.random(k), rng.uniform(3, 9, k)])
    tris = np.array([[i, i + 1, i + 2] for i in range(k - 2)], np.int32)
    return MeshData(vertices, rng.uniform(-1, 1, (k, 2)).astype(np.float32), tris, np.zeros(k - 2))


def _mesh(store: Store, n: int, *, second: bool) -> str:
    mesh = _mesh_data(n)
    files: dict[str, Any] = {"mesh.npz": lambda p: write_mesh_npz(p, mesh)}
    if second:
        files[f"Data{T.name}.mesh"] = lambda p: write_mesh_text(p, mesh)
    return _put(store, "orthostudio.mesh", n, files)


@pytest.mark.parametrize("made_by", STORES, ids=[p.name for p in STORES])
def test_the_second_forms_come_back_as_their_own_version_wrote_them(
    made_by: Path, tmp_path: Path
) -> None:
    """A store a release left: opened by this version (format 2), its second forms trimmed by
    Free space, then prepared: each comes back byte for byte, every relief and mesh holds its
    second form again, the store is format 1 and as it was built, refused by this process;
    opened again later by this version, it is 2 and clean."""
    copy = tmp_path / made_by.name
    shutil.copytree(made_by, copy)
    root = copy / "store"
    with Store(root, fsync=False) as s:
        assert _format(root) == "2"
        seconds = _seconds(s)
        for key, name in seconds:
            s.trim(key, [name])
        before = {i.key: i.digest for i in s.iter_artifacts()}
        unproved = {i.key for i in s.iter_artifacts() if not s.trimmed_proven(i.key)}
    report = prepare_older(root)
    assert _format(root) == "1"
    with pytest.raises(StoreError):
        Store(root, fsync=False)  # this process opens it no more: it would become 2 again
    store_module._CLOSED_FOR_OLDER.clear()
    with Store(root, fsync=False) as s:
        assert _format(root) == "2"
        after = _seconds(s)
        assert {k: v for k, v in after.items() if k in seconds} == {
            k: v for k, v in seconds.items() if k[0] not in unproved
        }
        for rule, suffix in SECONDS.items():  # an older version finds every one, a neighbour's too
            for info in s.iter_artifacts(rule):
                assert list(info.path.glob(f"Data*{suffix}")), (made_by.name, rule)
                assert s.content_digest(info.key) == info.digest
        assert all(not s.trimmed(info.key) for info in s.iter_artifacts())
        restored = {key for key, _ in seconds if key not in unproved}
        assert all(s.info(k).digest == before[k] for k in restored)  # type: ignore[union-attr]
        assert report.restored == len(seconds) - sum(1 for k, _ in seconds if k in unproved)
        assert report.removed == len(unproved)
        assert s.fsck(verify=True).clean


def test_a_one_form_relief_and_mesh_get_their_second_form(tmp_path: Path) -> None:
    """Built by this version in its one form: given the form an older version reads, by the
    writers that made it, and taken as if built with it (their digest that of their files now),
    so that an older version reuses them and finds a neighbour's mesh. Those built with their
    second form, untouched, stay as they are."""
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        one_relief, one_mesh = _relief(s, 1, second=False), _mesh(s, 2, second=False)
        both_relief, both_mesh = _relief(s, 3, second=True), _mesh(s, 4, second=True)
        untouched = {k: s.info(k).digest for k in (both_relief, both_mesh)}  # type: ignore[union-attr]
        plan = plan_older(s)
        assert sorted(k for k, _n, _b in plan.complete) == sorted([one_relief, one_mesh])
        assert plan.restore == [] and plan.remove == [] and plan.write_bytes > 0
    report = prepare_older(root)
    assert (report.completed, report.restored, report.removed) == (2, 0, 0)
    store_module._CLOSED_FOR_OLDER.clear()
    text = tmp_path / "text.mesh"
    write_mesh_text(text, _mesh_data(2))
    with Store(root, fsync=False) as s:
        assert (s.path(one_relief) / f"Data{T.name}.alt").read_bytes() == _raster(1).tobytes()
        assert (s.path(one_mesh) / f"Data{T.name}.mesh").read_bytes() == text.read_bytes()
        for key in (one_relief, one_mesh):
            assert s.content_digest(key) == s.info(key).digest  # type: ignore[union-attr]
        assert {k: s.info(k).digest for k in untouched} == untouched  # type: ignore[union-attr]
        assert s.fsck(verify=True).clean
        assert plan_older(s).complete == []  # nothing left to do


def test_a_trimmed_relief_and_mesh_come_back_on_the_disk_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Written by the writers of the versions before from ``dem.npy`` and ``mesh.npz``: the same
    bytes, each forced to the disk (file and folder) before the index takes it back, the size
    counted again, the progress told."""
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        relief, mesh = _relief(s, 5, second=True), _mesh(s, 6, second=True)
        before = _seconds(s)
        sizes = {key: s.info(key).size for key in (relief, mesh)}  # type: ignore[union-attr]
        assert s.trim(relief, [f"Data{T.name}.alt"]) > 0
        assert s.trim(mesh, [f"Data{T.name}.mesh"]) > 0
        plan = plan_older(s)
        assert len(plan.restore) == 2 and plan.remove == [] and plan.complete == []
        assert plan.write_bytes == sum(len(b) for b in before.values())
    synced: list[Any] = []
    real = older.os.fsync
    monkeypatch.setattr(older.os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    dirs: list[Path] = []
    monkeypatch.setattr(older, "fsync_dir", lambda d: dirs.append(Path(d)))
    seen: list[tuple[int, int]] = []
    report = prepare_older(root, progress=lambda done, total: seen.append((done, total)))
    assert report.restored == 2 and report.removed == 0
    assert len(synced) == 2 and len(dirs) == 2
    assert seen and seen[-1] == (plan.write_bytes, plan.write_bytes)
    store_module._CLOSED_FOR_OLDER.clear()
    with Store(root, fsync=False) as s:
        assert _seconds(s) == before
        assert {key: s.info(key).size for key in (relief, mesh)} == sizes  # type: ignore[union-attr]
        assert s.fsck(verify=True).clean


def test_without_the_room_nothing_is_written_or_the_artefacts_go_instead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The disk of the cache must hold everything first, and a margin: refused before any write,
    the cache left at 2 and as it was. Asked to remove them instead, the relief and mesh an
    older version cannot read go, which it builds again, and the cache is format 1."""
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        relief = _relief(s, 7, second=True)
        s.trim(relief, [f"Data{T.name}.alt"])
        one = _mesh(s, 8, second=False)
        both = _mesh(s, 9, second=True)
    real = shutil.disk_usage

    def tight(path: Any) -> Any:
        u = real(path)
        return type(u)(u.total, u.used, older.ROOM_MARGIN)

    monkeypatch.setattr(older.shutil, "disk_usage", tight)
    with pytest.raises(OSError) as refused:
        prepare_older(root)
    assert refused.value.errno == errno.ENOSPC
    assert _format(root) == "2"
    with Store(root, fsync=False) as s:
        assert s.trimmed(relief) and not (s.path(relief) / f"Data{T.name}.alt").exists()
        assert not plan_older(s).room
    report = prepare_older(root, remove_instead=True)
    assert (report.removed, report.restored, report.completed) == (2, 0, 0)
    assert _format(root) == "1"
    store_module._CLOSED_FOR_OLDER.clear()
    with Store(root, fsync=False) as s:
        assert not s.has(relief) and not s.has(one) and s.has(both)


def test_what_cannot_be_written_back_goes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file written back that is not the one recorded when it went is not taken back; one
    trimmed before digests were kept cannot be proved; a form kept that cannot be read gives
    nothing to write from: each artefact goes, the older version building it again, and the
    others are prepared as usual."""
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        wrong, unproved = _relief(s, 8, second=True), _relief(s, 9, second=True)
        damaged, sound = _relief(s, 10, second=True), _mesh(s, 11, second=True)
        for key in (wrong, unproved, damaged):
            s.trim(key, [f"Data{T.name}.alt"])
        s.trim(sound, [f"Data{T.name}.mesh"])
        s._db.execute("UPDATE trimmed SET digest = NULL WHERE key = ?", (unproved,))
        assert unproved in plan_older(s).remove
        (s.path(damaged) / "dem.npy").write_bytes(b"not an array")
    real_write = older._write_second

    def write(info: Any, kept: Any, final: Path) -> None:
        if info.key == wrong:
            final.write_bytes(b"\0" * final.stat().st_size if final.exists() else b"\0" * 64)
            return
        real_write(info, kept, final)

    monkeypatch.setattr(older, "_write_second", write)
    report = prepare_older(root)
    assert (report.restored, report.removed) == (1, 3)
    store_module._CLOSED_FOR_OLDER.clear()
    with Store(root, fsync=False) as s:
        assert not any(s.has(k) for k in (wrong, unproved, damaged))
        assert s.has(sound) and s.fsck(verify=True).clean


def test_a_run_stopped_leaves_the_cache_sound_and_goes_on_from_there(tmp_path: Path) -> None:
    """Quitting stops the run between two files: the cache stays this version's (format 2) and
    sound, opened again by this process; a run started again takes what the first wrote and
    finishes. Files a run killed outright left half written are swept first."""
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        keys = [_relief(s, n, second=True) for n in (12, 13, 14)]
        for key in keys:
            s.trim(key, [f"Data{T.name}.alt"])
        (s.path(keys[0]) / f"Data{T.name}.alt{older.TMP_SUFFIX}").write_bytes(b"half")
        (s.path(keys[1]) / f"Data{T.name}.mesh{older.TMP_SUFFIX}.tmp").write_bytes(b"half")
    stop = threading.Event()
    with pytest.raises(OlderStoppedError):
        prepare_older(root, stop=stop, progress=lambda done, total: stop.set())
    assert _format(root) == "2"
    with Store(root, fsync=False) as s:  # not closed: still this version's
        assert sum(1 for k in keys if not s.trimmed(k)) == 1
        assert not list(s.path(keys[0]).glob(f"*{older.TMP_SUFFIX}*"))
        assert not list(s.path(keys[1]).glob(f"*{older.TMP_SUFFIX}*"))
        assert s.fsck(verify=True).clean
    report = prepare_older(root)
    assert report.restored == 2 and _format(root) == "1"


def test_a_second_form_written_but_not_yet_taken_is_taken(tmp_path: Path) -> None:
    """A run stopped between writing a one-form artefact's second form and the index taking it:
    the next run finds the file, writes nothing, and takes it."""
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        key = _relief(s, 15, second=False)
        (s.path(key) / f"Data{T.name}.alt").write_bytes(_raster(15).tobytes())
        plan = plan_older(s)
        assert plan.complete == [(key, f"Data{T.name}.alt", 0)] and plan.write_bytes == 0
    report = prepare_older(root)
    assert (report.completed, report.written_bytes, report.removed) == (1, 0, 0)
    store_module._CLOSED_FOR_OLDER.clear()
    with Store(root, fsync=False) as s:
        assert s.content_digest(key) == s.info(key).digest  # type: ignore[union-attr]


def test_a_format_that_cannot_be_written_leaves_the_cache_this_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The index's number written last: when it cannot be (a lock held long elsewhere, a disk
    error), the store stays format 2 and this process keeps opening it."""
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        _relief(s, 16, second=True)

    def locked(*a: Any, **k: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store_module.sqlite3, "connect", locked)
    with pytest.raises(sqlite3.OperationalError):
        store_module.close_for_older(root, 1)
    monkeypatch.undo()
    assert _format(root) == "2"
    with Store(root, fsync=False) as s:
        assert len(s) == 1


def _app(home: Path, stopped: list[float] | None) -> Any:
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=None)
    return create_app(
        env_factory=BuildEnv.create,
        jobs=mgr,
        airports=FakeIndex(),
        settings_path=home / "config.toml",
        shutdown=None if stopped is None else (lambda: stopped.append(time.monotonic())),
    )


async def _until(c: Any, *states: str) -> dict[str, Any]:
    for _ in range(400):
        run = (await c.get("/api/older")).json()
        if run["state"] in states:
            return run  # type: ignore[no-any-return]
        await anyio.sleep(0.02)
    raise AssertionError(run)


@pytest.mark.anyio
async def test_the_library_prepares_the_cache_then_stops(home: Path) -> None:
    """``GET /api/older`` measures, ``POST /api/older`` writes back, says how far it is, and
    stops the engine once the cache is format 1: nothing here opens it again meanwhile."""
    root = home / "store"
    with Store(root, fsync=False) as s:
        relief = _relief(s, 20, second=True)
        alt = (s.path(relief) / f"Data{T.name}.alt").read_bytes()
        s.trim(relief, [f"Data{T.name}.alt"])
        _mesh(s, 21, second=False)
    stopped: list[float] = []
    async with client_for(_app(home, stopped)) as c:
        plan = (await c.get("/api/older")).json()
        assert plan["state"] == "idle" and plan["can_quit"] is True and plan["room"] is True
        assert (plan["restore"], plan["complete"], plan["remove"]) == (1, 1, 0)
        assert plan["write_bytes"] > len(alt) and plan["need_bytes"] > plan["write_bytes"]
        assert (await c.get("/api/engine")).json()["preparing_older"] is False
        r = await c.post("/api/older", json={})
        assert r.status_code == 200 and r.json()["state"] in ("running", "done"), r.text
        run = await _until(c, "done", "failed")
        assert run["state"] == "done", run
        assert (run["restored"], run["completed"], run["removed"]) == (1, 1, 0)
        assert (await c.post("/api/older")).json()["state"] == "done"  # once
        for _ in range(100):
            if stopped:
                break
            await anyio.sleep(0.05)
    assert stopped and _format(root) == "1"
    with pytest.raises(StoreError):
        Store(root, fsync=False)


@pytest.mark.anyio
async def test_a_run_that_failed_is_asked_again(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that failed is said with its error, then measured and started again on the next
    click, where the button showed the same error until a restart (a review, 2026-10-24)."""
    with Store(home / "store", fsync=False) as s:
        relief = _relief(s, 22, second=True)
        s.trim(relief, [f"Data{T.name}.alt"])
    calls: list[int] = []
    real = older.prepare_older

    def first_fails(*a: Any, **k: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError(13, "held by another program", "x")
        return real(*a, **k)

    import orthostudio.api.app as app_module

    monkeypatch.setattr(app_module, "prepare_older", first_fails)
    stopped: list[float] = []
    async with client_for(_app(home, stopped)) as c:
        await c.post("/api/older")
        failed = await _until(c, "failed")
        assert failed["error"]["code"] == "SYS_WRITE_FAILED"
        again = (await c.get("/api/older")).json()  # measured again, with the last error
        assert again["state"] == "failed" and again["restore"] == 1 and again["room"] is True
        await c.post("/api/older")
        assert (await _until(c, "done"))["restored"] == 1
    assert len(calls) == 2


@pytest.mark.anyio
async def test_quitting_stops_the_preparation_and_the_rest_waits_with_its_own_words(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While the cache is prepared, a build, Free space or a delete are refused with that very
    reason, and the engine says it runs; quitting asks first, then stops it between two files,
    the cache staying this version's, before the engine stops."""
    with Store(home / "store", fsync=False) as s:
        keys = [_relief(s, n, second=True) for n in (23, 24)]
        for key in keys:
            s.trim(key, [f"Data{T.name}.alt"])
    gate = threading.Event()
    real = older._write_second

    def slow(*a: Any) -> None:
        gate.wait(10)
        real(*a)

    monkeypatch.setattr(older, "_write_second", slow)
    stopped: list[float] = []
    async with client_for(_app(home, stopped)) as c:
        assert (await c.post("/api/older")).json()["state"] == "running"
        assert (await c.get("/api/engine")).json()["preparing_older"] is True
        for method, path, body in (
            ("POST", "/api/clean", {}),
            ("POST", "/api/jobs", {"tiles": ["+46+006"]}),
        ):
            r = await c.request(method, path, json=body)
            assert r.status_code == 409 and "older version" in r.json()["error"]["message"], path
        refused = await c.post("/api/quit", json={"force": False})
        assert refused.status_code == 409 and "older version" in refused.json()["error"]["message"]
        assert (await c.post("/api/quit", json={"force": True})).json()["stopping"] is True
        gate.set()
        assert (await _until(c, "stopped"))["state"] == "stopped"
        for _ in range(100):
            if stopped:
                break
            await anyio.sleep(0.05)
    assert stopped and _format(home / "store") == "2"
    with Store(home / "store", fsync=False) as s:
        assert s.fsck(verify=True).clean


@pytest.mark.anyio
async def test_an_engine_the_page_cannot_stop_does_not_prepare(home: Path) -> None:
    """Once the cache is ready the engine must stop, or its next opening raises the format back:
    one started otherwise than by ``osxp serve`` refuses, and the cache stays as it is."""
    with Store(home / "store", fsync=False) as s:
        _relief(s, 25, second=False)
    async with client_for(_app(home, None)) as c:
        assert (await c.get("/api/older")).json()["can_quit"] is False
        r = await c.post("/api/older")
        assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_NOT_STOPPABLE"
    assert _format(home / "store") == "2"
