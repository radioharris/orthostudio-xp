# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The cache made ready for an older version, before going back to it (``orthostudio.older``).

Since the relief and the mesh are kept once, an earlier version finds neither ``Data<tile>.alt``
nor ``Data<tile>.mesh`` in their artefacts, and refuses an index of format 2. The Library's
*Prepare for an older version…* writes back every second form Free space trimmed, each taken back
only as it was built, removes what only this version reads, gives the index the format 1 and stops
the engine. The stores the releases left (``tests/data/stores``) give the second forms their own
writers made: each must come back byte for byte. Temporary folders only.
"""

from __future__ import annotations

import errno
import shutil
import sqlite3
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
from orthostudio.older import plan_older, prepare_older
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


def _relief(store: Store, n: int, *, second: bool) -> str:
    """A relief artefact as a version before 2026-10-23 wrote it (``second``), or as this one."""
    raster = np.random.default_rng(n).uniform(0, 3000, (33, 33)).astype(np.float32)
    files: dict[str, Any] = {
        "dem.npy": lambda p: np.save(p, raster, allow_pickle=False),
        "meta.json": b'{"format": "osxp-dem-1"}',
    }
    if second:
        files[f"Data{T.name}.alt"] = raster.tobytes()
    return _put(store, "orthostudio.dem", n, files)


def _mesh(store: Store, n: int, *, second: bool) -> str:
    rng = np.random.default_rng(n)
    k = 40
    vertices = np.column_stack([6 + rng.random(k), 46 + rng.random(k), rng.uniform(3, 9, k)])
    tris = np.array([[i, i + 1, i + 2] for i in range(k - 2)], np.int32)
    mesh = MeshData(vertices, rng.uniform(-1, 1, (k, 2)).astype(np.float32), tris, np.zeros(k - 2))
    files: dict[str, Any] = {"mesh.npz": lambda p: write_mesh_npz(p, mesh)}
    if second:
        files[f"Data{T.name}.mesh"] = lambda p: write_mesh_text(p, mesh)
    return _put(store, "orthostudio.mesh", n, files)


@pytest.mark.parametrize("made_by", STORES, ids=[p.name for p in STORES])
def test_the_second_forms_come_back_as_their_own_version_wrote_them(
    made_by: Path, tmp_path: Path
) -> None:
    """A store a release left: opened by this version (format 2), its second forms trimmed by
    Free space, then prepared: each comes back byte for byte, the store is format 1 and as it was
    built, refused by this process; opened again later by this version, it is 2 and clean."""
    copy = tmp_path / made_by.name
    shutil.copytree(made_by, copy)
    root = copy / "store"
    with Store(root, fsync=False) as s:
        assert _format(root) == "2"
        seconds = _seconds(s)
        for key, name in seconds:
            s.trim(key, [name])
        trimmed_before = {key for key, _ in seconds if s.trimmed(key)}
        digests = {i.key: i.digest for i in s.iter_artifacts()}
    report = prepare_older(root)
    assert _format(root) == "1"
    with pytest.raises(StoreError):
        Store(root, fsync=False)  # this process opens it no more: it would become 2 again
    store_module._CLOSED_FOR_OLDER.clear()
    with Store(root, fsync=False) as s:
        assert _format(root) == "2"
        assert _seconds(s) == {k: v for k, v in seconds.items() if k[0] in digests}
        assert report.restored == sum(1 for key, _ in seconds if key in trimmed_before)
        assert all(not s.trimmed(info.key) for info in s.iter_artifacts())
        assert {i.key: i.digest for i in s.iter_artifacts()} == {
            k: d for k, d in digests.items() if s.has(k)
        }
        for info in s.iter_artifacts():
            assert s.content_digest(info.key) == info.digest, info.rule
        assert s.fsck(verify=True).clean


def test_what_only_this_version_reads_goes_and_the_rest_stays(tmp_path: Path) -> None:
    """A relief and a mesh this version built in its one form are removed (the older version
    builds them again); those built with their second form, untouched, stay as they are."""
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        one_relief, one_mesh = _relief(s, 1, second=False), _mesh(s, 2, second=False)
        both_relief, both_mesh = _relief(s, 3, second=True), _mesh(s, 4, second=True)
        plan = plan_older(s)
        assert sorted(plan.remove) == sorted([one_relief, one_mesh])
        assert plan.restore == [] and plan.write_bytes == 0
    report = prepare_older(root)
    assert (report.restored, report.removed, report.written_bytes) == (0, 2, 0)
    store_module._CLOSED_FOR_OLDER.clear()
    with Store(root, fsync=False) as s:
        assert not s.has(one_relief) and not s.has(one_mesh)
        assert s.has(both_relief) and s.has(both_mesh) and s.fsck(verify=True).clean


def test_a_trimmed_relief_and_mesh_come_back_from_the_form_kept(tmp_path: Path) -> None:
    """Written by the writers of the versions before (``Dem.write_alt``, ``write_mesh_text``)
    from ``dem.npy`` and ``mesh.npz``: the same bytes, taken back, the size counted again."""
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        relief, mesh = _relief(s, 5, second=True), _mesh(s, 6, second=True)
        before = _seconds(s)
        sizes = {key: s.info(key).size for key in (relief, mesh)}  # type: ignore[union-attr]
        assert s.trim(relief, [f"Data{T.name}.alt"]) > 0
        assert s.trim(mesh, [f"Data{T.name}.mesh"]) > 0
        plan = plan_older(s)
        assert len(plan.restore) == 2 and plan.remove == []
        assert plan.write_bytes == sum(len(b) for b in before.values())
    seen: list[tuple[int, int]] = []
    report = prepare_older(root, progress=lambda done, total: seen.append((done, total)))
    assert report.restored == 2 and report.removed == 0
    assert seen and seen[-1] == (plan.write_bytes, plan.write_bytes)
    store_module._CLOSED_FOR_OLDER.clear()
    with Store(root, fsync=False) as s:
        assert _seconds(s) == before
        assert {key: s.info(key).size for key in (relief, mesh)} == sizes  # type: ignore[union-attr]
        assert s.fsck(verify=True).clean


def test_nothing_is_written_without_the_room_for_all_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The disk of the cache must hold everything first, and a margin: refused before any write,
    the cache left at 2 and as it was, and still opened by this process."""
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        relief = _relief(s, 7, second=True)
        s.trim(relief, [f"Data{T.name}.alt"])
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


def test_a_second_form_not_the_same_takes_its_artefact_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file written back that is not the one recorded when it went is not taken back: the
    artefact goes, and the older version builds it again. One trimmed before digests were kept
    goes the same way: nothing proves it."""
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        wrong, unproved = _relief(s, 8, second=True), _relief(s, 9, second=True)
        for key in (wrong, unproved):
            s.trim(key, [f"Data{T.name}.alt"])
        s._db.execute("UPDATE trimmed SET digest = NULL WHERE key = ?", (unproved,))
        assert unproved in plan_older(s).remove

    def other(store: Store, key: str, tmp: Path) -> None:
        tmp.write_bytes(b"\0" * (store.trimmed(key)[f"Data{T.name}.alt"]))

    monkeypatch.setattr(older, "_write_second", other)
    report = prepare_older(root)
    assert (report.restored, report.removed) == (0, 2)
    store_module._CLOSED_FOR_OLDER.clear()
    with Store(root, fsync=False) as s:
        assert not s.has(wrong) and not s.has(unproved) and s.fsck(verify=True).clean


def _app(home: Path, stopped: list[float] | None) -> Any:
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=None)
    return create_app(
        env_factory=BuildEnv.create,
        jobs=mgr,
        airports=FakeIndex(),
        settings_path=home / "config.toml",
        shutdown=None if stopped is None else (lambda: stopped.append(time.monotonic())),
    )


@pytest.mark.anyio
async def test_the_library_prepares_the_cache_then_stops(home: Path) -> None:
    """``GET /api/older`` measures, ``POST /api/older`` writes back, says how far it is, and
    stops the engine once the cache is format 1: nothing here opens it again meanwhile."""
    root = home / "store"
    with Store(root, fsync=False) as s:
        relief = _relief(s, 10, second=True)
        alt = (s.path(relief) / f"Data{T.name}.alt").read_bytes()
        s.trim(relief, [f"Data{T.name}.alt"])
        _mesh(s, 11, second=False)
    stopped: list[float] = []
    async with client_for(_app(home, stopped)) as c:
        plan = (await c.get("/api/older")).json()
        assert plan["state"] == "idle" and plan["can_quit"] is True and plan["room"] is True
        assert (plan["restore"], plan["remove"], plan["write_bytes"]) == (1, 1, len(alt))
        r = await c.post("/api/older")
        assert r.status_code == 200 and r.json()["state"] in ("running", "done"), r.text
        for _ in range(200):
            run = (await c.get("/api/older")).json()
            if run["state"] != "running":
                break
            await anyio.sleep(0.05)
        assert run["state"] == "done", run
        assert (run["restored"], run["removed"], run["written_bytes"]) == (1, 1, len(alt))
        assert (await c.post("/api/older")).json()["state"] == "done"  # once
        for _ in range(100):
            if stopped:
                break
            await anyio.sleep(0.05)
    assert stopped and _format(root) == "1"
    with pytest.raises(StoreError):
        Store(root, fsync=False)


@pytest.mark.anyio
async def test_an_engine_the_page_cannot_stop_does_not_prepare(home: Path) -> None:
    """Once the cache is ready the engine must stop, or its next opening raises the format back:
    one started otherwise than by ``osxp serve`` refuses, and the cache stays as it is."""
    with Store(home / "store", fsync=False) as s:
        _relief(s, 12, second=False)
    async with client_for(_app(home, None)) as c:
        assert (await c.get("/api/older")).json()["can_quit"] is False
        r = await c.post("/api/older")
        assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_NOT_STOPPABLE"
    assert _format(home / "store") == "2"
