# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The Library's disk space: ``GET /api/disk`` measures, ``POST /api/clean`` frees.

Found when a user deleted every tile a minute after building them and got no space back (the
delete keeps what was used in the last ten minutes), then asked for a button that empties the
cache and the images from the page.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import test_api_fakes as fakes
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.graph import Store, artifact_key
from orthostudio.pipeline.build import BuildEnv
from test_api_fakes import FakeBuild, FakeIndex, client_for, make_spec

anyio_backend = fakes.anyio_backend
home = fakes.home


def _unused_artefact(root: Path, data: bytes) -> str:
    """A texture no pack on disk needs, as a build of a deleted tile leaves it."""
    with Store(root, fsync=False) as s:
        key, recipe = artifact_key("texture.dds", 1, {"n": len(data)}, {})
        with s.begin("texture.dds", key, "file") as b:
            b.out.write_bytes(data)
            b.commit(version=1, recipe=recipe, inputs=[])
    return key


def _world(home: Path) -> dict[str, Path]:
    _unused_artefact(home / "store", b"D" * 6000)
    chunks = home / "chunks" / "BI" / "16"
    chunks.mkdir(parents=True)
    (chunks / "1_2.chunks").write_bytes(b"j" * 4000)
    mapcache = home / "mapcache" / "BI" / "12" / "2140"
    mapcache.mkdir(parents=True)
    (mapcache / "1470.jpg").write_bytes(b"m" * 300)
    return {"chunks": chunks / "1_2.chunks", "mapcache": mapcache / "1470.jpg"}


def _app(home: Path, build: FakeBuild | None = None) -> tuple[object, JobManager]:
    mgr = JobManager(jobs_dir=home / "jobs", build=build or FakeBuild(), env_factory=None)
    app = create_app(env_factory=BuildEnv.create, jobs=mgr, airports=FakeIndex(),
                     settings_path=home / "config.toml")  # fmt: skip
    return app, mgr


@pytest.mark.anyio
async def test_disk_measures_and_free_space_frees_what_no_tile_needs(home: Path) -> None:
    files = _world(home)
    app, mgr = _app(home)
    try:
        async with client_for(app) as c:
            doc = (await c.get("/api/disk")).json()
            assert doc["unused_bytes"] == 6000 and doc["tiles"] == 0
            assert doc["images_bytes"] == 4000 and doc["mapcache_bytes"] == 300
            assert doc["building"] is False and doc["store_bytes"] >= 6000

            r = await c.post("/api/clean", json={"images": False})
            assert r.status_code == 200, r.text
            assert r.json()["freed_bytes"] == 6000 and r.json()["images_freed_bytes"] == 0
            assert files["chunks"].is_file() and files["mapcache"].is_file()  # images kept

            r = await c.post("/api/clean", json={"images": True})
            assert r.status_code == 200, r.text
            assert r.json()["images_freed_bytes"] == 4300
            assert not files["chunks"].exists() and not files["mapcache"].exists()
            doc = (await c.get("/api/disk")).json()
            assert doc["unused_bytes"] == doc["images_bytes"] == doc["mapcache_bytes"] == 0

            r = await c.post("/api/clean", json={"images": True, "bogus": 1})
            assert r.status_code == 422
    finally:
        mgr.close()


@pytest.mark.anyio
async def test_the_cache_of_the_tiles_filed_outside_is_measured_and_freed_on_request(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The atelier, step 5: the measure gives the cache of the tiles filed outside the atelier
    apart, the images' choice leaves it, and "Free space" deletes it when asked; the tile stays
    whole."""
    from test_clean_filed import F, World

    monkeypatch.setenv("OSXP_DATA_DIR", str(tmp_path / "data"))
    w = World(tmp_path)
    w.build(F, 2, osm=w.osm)
    w.pack(F, tmp_path / "Other disk" / "Alps", link=False)
    piece = w.piece(46.5, 7.5, 4000)
    app, mgr = _app(home)
    try:
        async with client_for(app) as c:
            doc = (await c.get("/api/disk")).json()
            assert doc["filed_tiles"] == 1 and doc["filed_images_bytes"] == 4000
            assert doc["filed_bytes"] > 0 and doc["images_bytes"] == doc["unused_bytes"] == 0
            r = await c.post("/api/clean", json={"images": True})
            assert r.status_code == 200 and r.json()["filed_freed_bytes"] == 0, r.text
            assert piece.is_file()
            r = await c.post("/api/clean", json={"filed": True})
            assert r.status_code == 200, r.text
            assert r.json()["filed_freed_bytes"] == doc["filed_bytes"] + 4000
            after = (await c.get("/api/disk")).json()
            assert after["filed_tiles"] == 1
            assert after["filed_bytes"] == after["filed_images_bytes"] == 0
    finally:
        mgr.close()
    assert not piece.exists() and w.whole(F)


@pytest.mark.anyio
async def test_free_space_waits_for_builds_here_and_in_a_terminal(home: Path) -> None:
    """Without a grace period, a running build could lose what it is about to use."""
    files = _world(home)
    app, mgr = _app(home, FakeBuild(delay_s=0.2))
    try:
        async with client_for(app) as c:
            job = mgr.start([make_spec("+46+006", home=home)])
            assert (await c.get("/api/disk")).json()["building"] is True
            r = await c.post("/api/clean", json={"images": True})
            assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_BUSY"
            mgr.cancel(job.id)
            assert job.wait(30)

            with Store(home / "store", fsync=False) as s:
                key = next(i.key for i in s.iter_artifacts())
                shard = s.path(key).parent
            building = shard / f"{key}.tmp-{os.getppid()}-abcd"  # a live process, not this one
            building.mkdir()
            assert (await c.get("/api/disk")).json()["building"] is True
            r = await c.post("/api/clean", json={"images": True})
            assert r.status_code == 409 and "terminal" in r.json()["error"]["message"]
            assert files["chunks"].is_file()
            building.rmdir()

            r = await c.post("/api/clean", json={"images": True})
            assert r.status_code == 200 and not files["chunks"].exists()
    finally:
        mgr.close()


@pytest.mark.anyio
async def test_pages_that_ask_while_the_disk_is_measured_share_the_measure(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Library asked for "Free space"'s figures every five seconds while sizes were pending,
    each ask a new measure that a hard disk takes minutes over, until they took every thread and
    nothing else answered (review of 0.1.19, 2026-09-29). An ask that comes while a measure runs
    shares it; one that comes after it ends measures again."""
    import asyncio
    import threading
    from types import SimpleNamespace

    from orthostudio.api import app as api_app

    started, release = threading.Event(), threading.Event()
    measured: list[bool] = []

    def slow_clean(*args: object, **kw: object) -> SimpleNamespace:
        measured.append(bool(kw.get("dry_run")))
        started.set()
        release.wait(10)
        return SimpleNamespace(
            freed_bytes=1,
            images_bytes=5,
            mapcache_bytes=2,
            relief_bytes=4,
            filed_tiles=0,
            filed_bytes=0,
            filed_images_bytes=0,
            packs=[],
        )

    monkeypatch.setattr(api_app, "clean", slow_clean)
    app, mgr = _app(home)
    try:
        async with client_for(app) as c:
            asks = [asyncio.ensure_future(c.get("/api/disk")) for _ in range(3)]
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.2)  # the other asks arrive while the measure runs
            release.set()
            answers = [(await ask).json() for ask in asks]
            assert measured == [True], "one measure for the three asks"
            assert all(a == answers[0] for a in answers) and answers[0]["images_bytes"] == 3
            assert (await c.get("/api/disk")).status_code == 200
            assert measured == [True, True], "an ask after it ends measures again"
    finally:
        release.set()
        mgr.close()
