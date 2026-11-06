# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The cloud mode's routes through the app (``clouds.md`` 1.5, 6): what a tile and its clouds
allow is the engine's one view's (``clouds/state.py``), and its routes refuse by it; what runs the
detector is asked first; a bad tile name, cloud or outline is refused, never a bare error."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import numpy as np
import pytest

import test_api_fakes as fakes
from orthostudio.api import clouds_api
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.clouds import cloudlist, state
from orthostudio.clouds.picture import Picture
from orthostudio.home import default_tiles_root
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import ArtefactEntry, PackManifest, write_manifest
from test_api_fakes import FakeBuild, FakeIndex, client_for

home = fakes.home
TILE = TileRef(43, 5)
NAME = "1488_2096_BI12"


@pytest.fixture
def app(home: Path):  # type: ignore[no-untyped-def]
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=lambda specs: None)
    application = create_app(
        env_factory=None, jobs=mgr, airports=FakeIndex(), settings_path=home / "config.toml"
    )
    yield application
    mgr.close()


@pytest.fixture
def ready(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tile built, whatever version built it: its DSF, its texture, its manifest; its picture a
    small one."""
    folder = default_tiles_root() / "zOrthoStudio_+43+005"
    folder.mkdir(parents=True)
    dsf = folder / TILE.dsf_relpath
    dsf.parent.mkdir(parents=True)
    dsf.write_bytes(b"XPLNEDSF" + b"\0" * 100)
    (folder / "textures").mkdir()
    (folder / "textures" / f"{NAME}.dds").write_bytes(b"DDS " + b"\0" * 124)
    files = {"dsf": TILE.dsf_relpath.as_posix(), "dsf_size": 108, "textures": 1, "terrain": 0}
    write_manifest(
        folder,
        TILE,
        PackManifest(TILE.name, "BI", 12, {"textures": ArtefactEntry("k1", "d", "r@2")}, files),
    )
    blank = np.zeros((8, 8), np.uint8)
    picture = Picture(np.zeros((8, 8, 3), np.uint8), blank, blank, blank - 1, (), 12, 0, 0)
    monkeypatch.setattr(clouds_api, "_draw", lambda tile, held, zl: picture)
    monkeypatch.setattr(clouds_api, "detector_ready", lambda: False)
    return folder


def _ring() -> list[list[float]]:
    return [[43.1, 5.1, 43.1, 5.2, 43.2, 5.2, 43.1, 5.1]]


@pytest.mark.anyio
async def test_a_bad_name_cloud_or_outline_is_refused_in_words(app: Any, ready: Path) -> None:
    async with client_for(app) as c:
        r = await c.get("/api/clouds/what")
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_LATLON_INVALID"
        r = await c.post("/api/clouds/+43+005/picks", json={"tick": False})
        assert r.json()["error"]["code"] == "CLOUD_INVALID"
        two = {"polygon": [[5.1, 43.1], [5.2, 43.2]], "download": True}
        r = await c.post("/api/clouds/+43+005/outlines", json=two)
        assert r.json()["error"]["code"] == "CLOUD_INVALID"


@pytest.mark.anyio
async def test_what_a_ready_tile_allows_is_the_engines_to_say(app: Any, ready: Path) -> None:
    async with client_for(app) as c:
        a = (await c.get("/api/clouds/+43+005")).json()
    assert a["state"] == "ready" and a["busy"] is False
    # nothing to apply, restore or discard yet: those wait for a change
    assert a["allowed"] == ["check", "choose", "find", "outline", "remove", "tick", "untick"]
    assert a["line"] == {"key": "clouds.not_looked", "params": {}}  # no search made yet
    assert a["texts"]["count"] == [{"key": "clouds.pending_none", "params": {}}]
    assert a["views"] == {"after": "clouds.after", "before": "clouds.before"}
    assert a["picture"]["level"] == 12


@pytest.mark.anyio
async def test_a_tile_not_built_allows_nothing(app: Any, home: Path) -> None:
    async with client_for(app) as c:
        a = (await c.get("/api/clouds/+43+005")).json()
        r = await c.post("/api/clouds/+43+005/find", json={"download": True})
    assert a["state"] == "not_built" and a["allowed"] == [] and a["views"]["after"] is None
    assert r.json()["error"]["code"] == "CLOUD_TILE_NOT_READY"


@pytest.mark.anyio
async def test_what_runs_the_detector_is_asked_first_and_an_untick_is_not(
    app: Any, ready: Path
) -> None:
    with cloudlist.update(TILE) as cl:
        cid = cl.add("drawn", _ring(), chosen="EOX")["id"]
    async with client_for(app) as c:
        r = await c.post("/api/clouds/+43+005/find")
        err = r.json()["error"]
        assert err["code"] == "CLOUD_DETECTOR_ASK" and err["context"]["mb"] >= 1
        r = await c.post("/api/clouds/+43+005/picks", json={"id": cid, "tick": True})
        assert r.json()["error"]["code"] == "CLOUD_DETECTOR_ASK"  # its images: the detector
        # nothing kept before the user says yes: the page sends the same tick again then
        assert cloudlist.load(TILE).get(cid)["tick"] is False
        with cloudlist.update(TILE) as cl:
            cl.get(cid)["tick"] = True
        row = (await c.get("/api/clouds/+43+005")).json()["clouds"][0]
        assert row["state"] == "waiting" and row["line"]["tone"] == "warn"
        assert "check" in row["actions"] and row["toggle"] == "untick"
        r = await c.post("/api/clouds/+43+005/picks", json={"id": cid, "tick": False})
        assert r.status_code == 200, r.text  # an untick needs no detector
    assert cloudlist.load(TILE).get(cid)["tick"] is False


@pytest.mark.anyio
async def test_a_build_allows_nothing_and_its_folder_is_not_read(
    home: Path, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import FastAPI

    def read_tile(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the folder read while a build writes it")

    monkeypatch.setattr(clouds_api, "read_tile", read_tile)
    building = {TILE.name: "running"}
    hooks = clouds_api.CloudHooks(custom_scenery=lambda: None, building=lambda: building)
    router, _engine = clouds_api.clouds_router(hooks)
    api = FastAPI()
    api.include_router(router)
    async with client_for(api) as c:
        a = (await c.get("/api/clouds/+43+005")).json()
    assert a["state"] == "in_build" and a["allowed"] == [] and a["busy"] is True
    assert a["picture"] is None
    assert a["texts"]["count"] == [{"key": "clouds.pending_none", "params": {}}]


def test_what_a_build_and_the_tasks_let_be_done() -> None:
    assert state.possible("in_build") == state.possible("queued") == frozenset()
    assert "apply" not in state.possible("ready", {"check"})  # a task reads the folder
    assert state.possible("ready", {"apply"}) == state.LIST_EDITS  # Apply writes it


@pytest.mark.anyio
async def test_apply_and_restore_with_nothing_to_do_are_refused(app: Any, ready: Path) -> None:
    async with client_for(app) as c:
        for body in ({}, {"restore": True}):
            r = await c.post("/api/clouds/+43+005/apply", json=body)
            assert r.json()["error"]["code"] == "CLOUD_TILE_NOT_READY", body


@pytest.mark.anyio
async def test_a_ready_tiles_rows_offer_what_its_list_allows(app: Any, ready: Path) -> None:
    with cloudlist.update(TILE) as cl:
        found = cl.add("found", _ring(), screening={"EOX": {"answer": "clear", "fit": 70}})
        drawn = cl.add("drawn", _ring())
    async with client_for(app) as c:
        rows = {r["id"]: r for r in (await c.get("/api/clouds/+43+005")).json()["clouds"]}
        refused = await c.post("/api/clouds/+43+005/picks", json={"id": drawn["id"]})
        unknown = await c.post("/api/clouds/+43+005/picks", json={"id": "gone0001"})
    assert (
        rows[found["id"]]["actions"] == ["choose", "tick"] and rows[found["id"]]["toggle"] == "tick"
    )
    assert rows[drawn["id"]]["actions"] == ["choose", "remove"]  # no source yet: no tick
    assert rows[drawn["id"]]["toggle"] is None and rows[found["id"]]["source"] == "EOX"
    assert refused.json()["error"]["code"] == "CLOUD_INVALID"
    assert unknown.json()["error"]["code"] == "CLOUD_UNKNOWN"


@pytest.mark.anyio
async def test_an_outline_is_tried_then_ticked_then_its_images_asked(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # one path: the outline's sources tried, then its state ticks it and asks its images
    from orthostudio.clouds.find import Check, Judgement

    asked: list[str] = []

    def check(*args: Any, **_k: Any) -> Check:
        asked.append(args[4])  # the source
        return Check(Judgement("no_picture"))

    monkeypatch.setattr(clouds_api, "detector_ready", lambda: True)
    monkeypatch.setattr(clouds_api, "opencv_ready", lambda: True)
    monkeypatch.setattr(clouds_api, "_tools", lambda task: None)
    monkeypatch.setattr(clouds_api, "screen", lambda *a, **k: [{"EOX": Judgement("clear", 70)}])
    monkeypatch.setattr(clouds_api, "check", check)
    polygon = [[5.1, 43.1], [5.2, 43.1], [5.2, 43.2], [5.1, 43.2]]
    async with client_for(app) as c:
        r = await c.post("/api/clouds/+43+005/outlines", json={"polygon": polygon})
        assert r.status_code == 200, r.text
        cid = r.json()["id"]
        for _ in range(200):
            a = (await c.get("/api/clouds/+43+005")).json()
            if not a["busy"] and asked:
                break
            await anyio.sleep(0.05)
    (row,) = a["clouds"]
    assert row["id"] == cid and row["tick"] is True and asked == ["EOX"]
    # its one source has no picture this sharp: it may no longer be its source
    assert row["state"] == "waiting" and row["line"]["key"] == "clouds.row.waiting.no_source"
    assert row["menu"] == [{"source": "EOX", "answer": "no_picture", "fit": 0, "proposable": False}]


@pytest.mark.anyio
async def test_the_library_cell_is_offered_on_the_tiles_own_folder_only(
    app: Any, ready: Path, tmp_path: Path
) -> None:
    from orthostudio.install.library import Library, default_library_path

    elsewhere = tmp_path / "elsewhere" / "zOrthoStudio_+43+005"
    elsewhere.mkdir(parents=True)
    with Library(default_library_path()) as lib:
        own = lib.register(TILE, "BI", 12, ready, "osxp")
        lib.register(TILE, "BI", 12, elsewhere, "ortho4xp")
    async with client_for(app) as c:
        rows = (await c.get("/api/clouds")).json()["rows"]
    assert rows == {str(own.path): {"replaced": 0, "to_apply": 0}}


@pytest.mark.anyio
async def test_a_tile_whose_cloud_gets_its_images_is_busy(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from orthostudio.clouds.find import Check, Judgement

    go = threading.Event()

    def check(*_a: Any, **_k: Any) -> Check:
        go.wait(10)
        return Check(Judgement("did_not_answer"))

    monkeypatch.setattr(clouds_api, "detector_ready", lambda: True)
    monkeypatch.setattr(clouds_api, "opencv_ready", lambda: True)
    monkeypatch.setattr(clouds_api, "_tools", lambda task: None)
    monkeypatch.setattr(clouds_api, "check", check)
    with cloudlist.update(TILE) as cl:
        cid = cl.add("drawn", _ring(), chosen="EOX")["id"]
    async with client_for(app) as c:
        r = await c.post("/api/clouds/+43+005/picks", json={"id": cid, "tick": True})
        assert r.status_code == 200, r.text
        a = (await c.get("/api/clouds/+43+005")).json()
        go.set()
    (row,) = a["clouds"]
    assert a["busy"] is True and a["tasks"] == []  # its task is in its row, the page reads again
    assert row["state"] == "check" and row["task"]["kind"] == "check" and row["actions"] == ["stop"]
