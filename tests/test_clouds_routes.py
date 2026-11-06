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
from orthostudio.clouds.tile import read_tile
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
    monkeypatch.setattr(  # named as the engine names it, by what it is made of
        clouds_api,
        "_draw",
        lambda tile, folder, zl, remade: (clouds_api._what(read_tile(folder), zl, remade), picture),
    )
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
        assert r.json()["error"]["code"] == "CLOUD_INVALID"  # no action named
        r = await c.post(
            "/api/clouds/+43+005/picks", json={"action": "choose", "id": "a", "source": [1]}
        )
        assert r.json()["error"]["code"] == "CLOUD_INVALID"  # a source is a name
        two = {"polygon": [[5.1, 43.1], [5.2, 43.2]], "download": True}
        r = await c.post("/api/clouds/+43+005/outlines", json=two)
        assert r.json()["error"]["code"] == "CLOUD_INVALID"


@pytest.mark.anyio
async def test_what_a_ready_tile_allows_is_the_engines_to_say(app: Any, ready: Path) -> None:
    async with client_for(app) as c:
        a = (await c.get("/api/clouds/+43+005")).json()
    assert a["state"] == "ready" and a["busy"] is False
    # nothing to apply, restore, discard, tick or untick yet: those wait for a cloud or a change
    assert a["allowed"] == ["find", "outline"]
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
        r = await c.post("/api/clouds/+43+005/picks", json={"id": cid, "action": "tick"})
        err = r.json()["error"]
        assert err["code"] == "CLOUD_DETECTOR_ASK"  # its images: the detector
        assert err["context"]["then"] == "check"  # on yes, its images asked, not the tick again
        # the tick kept either way: on no, the cloud waits for its images, offered to get them
        assert cloudlist.load(TILE).get(cid)["tick"] is True
        row = (await c.get("/api/clouds/+43+005")).json()["clouds"][0]
        assert row["state"] == "waiting" and row["line"]["tone"] == "warn"
        assert "check" in row["actions"] and row["toggle"] == "untick"
        r = await c.post("/api/clouds/+43+005/picks", json={"id": cid, "action": "untick"})
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
    taken = {TILE.name: "in_build"}
    hooks = clouds_api.CloudHooks(custom_scenery=lambda: None, taken=lambda: taken)
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
    assert state.possible("ready", {"apply"}) == state.DRAFT  # Apply writes it


@pytest.mark.anyio
async def test_apply_and_restore_with_nothing_to_do_are_refused(app: Any, ready: Path) -> None:
    async with client_for(app) as c:
        for body in ({"action": "apply"}, {"action": "restore"}):
            r = await c.post("/api/clouds/+43+005/apply", json=body)
            assert r.json()["error"]["code"] == "CLOUD_TILE_NOT_READY", body


@pytest.mark.anyio
async def test_a_ready_tiles_rows_offer_what_its_list_allows(app: Any, ready: Path) -> None:
    with cloudlist.update(TILE) as cl:
        found = cl.add("found", _ring(), screening={"EOX": {"answer": "clear", "fit": 70}})
        drawn = cl.add("drawn", _ring())
    async with client_for(app) as c:
        rows = {r["id"]: r for r in (await c.get("/api/clouds/+43+005")).json()["clouds"]}
        refused = await c.post(
            "/api/clouds/+43+005/picks", json={"id": drawn["id"], "action": "tick"}
        )
        unknown = await c.post(
            "/api/clouds/+43+005/picks", json={"id": "gone0001", "action": "tick"}
        )
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
    assert rows == {str(own.path): state.library_cell()}


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
        r = await c.post("/api/clouds/+43+005/picks", json={"id": cid, "action": "tick"})
        assert r.status_code == 200, r.text
        a = (await c.get("/api/clouds/+43+005")).json()
        go.set()
    (row,) = a["clouds"]
    assert a["busy"] is True and a["tasks"] == []  # its task is in its row, the page reads again
    assert row["state"] == "check" and row["task"]["kind"] == "check" and row["actions"] == ["stop"]
    assert a["texts"]["count"][0]["key"] == "clouds.pending.waiting"  # counted while it runs
    assert "discard" in a["allowed"]


@pytest.mark.anyio
async def test_an_outline_of_no_numbers_or_off_the_tile_is_refused(app: Any, ready: Path) -> None:
    async with client_for(app) as c:
        for polygon in (
            [[5.1, 43.1], [5.2, "NaN"], [5.2, 43.2]],
            [[5.1, 10.1], [5.2, 10.1], [5.2, 10.2]],
        ):
            r = await c.post("/api/clouds/+43+005/outlines", json={"polygon": polygon})
            assert r.json()["error"]["code"] == "CLOUD_INVALID", polygon
        a = (await c.get("/api/clouds/+43+005")).json()
    assert a["clouds"] == []  # nothing kept that the tile's view could not show


@pytest.mark.anyio
async def test_after_is_what_x_plane_shows_before_anything_is_laid(app: Any, ready: Path) -> None:
    async with client_for(app) as c:
        a = (await c.get("/api/clouds/+43+005")).json()
    assert a["after"]["name"] == a["picture"]["name"]  # no cloud laid: the build's own


def test_what_a_picture_is_made_of_names_the_textures_drawn_as_x_plane_shows_them() -> None:
    from orthostudio.clouds.tile import Stamp, TileClouds

    stamps = {"a": Stamp("p" * 40, "c" * 40), "b": Stamp("q" * 40, "d" * 40)}
    shown = TileClouds(Path("/t"), "k", "BI", True, ("a", "b", "c"), stamps, {}, (), frozenset())
    assert state.as_shown(shown, None) == {}  # Before: every texture as built
    assert state.as_shown(shown, frozenset({"a"})) == {"b": "d" * 40}  # After: but those remade
    assert clouds_api._what(shown, 12, None) != clouds_api._what(shown, 12, frozenset())


def test_a_job_lets_the_cloud_tasks_of_its_tiles_end_before_it_builds(home: Path) -> None:
    from test_api_fakes import make_spec

    order: list[str] = []

    class Build(FakeBuild):
        def __call__(self, specs: Any, **kw: Any) -> Any:
            order.append("build")
            return super().__call__(specs, **kw)

    mgr = JobManager(jobs_dir=home / "jobs", build=Build(), env_factory=lambda specs: None)
    mgr.make_way = lambda tiles: order.append(f"make way {list(tiles)}")
    try:
        job = mgr.start([make_spec("+43+005", home=home)])
        assert job.wait(10.0)
    finally:
        mgr.close()
    assert order == ["make way ['+43+005']", "build"]


@pytest.mark.anyio
async def test_yes_to_the_detector_after_a_tick_gets_its_images_and_tick_all_ticks_every_cloud(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orthostudio.clouds.find import Check, Judgement

    asked: list[str] = []
    monkeypatch.setattr(clouds_api, "_tools", lambda task: None)

    def check(*args: Any, **_k: Any) -> Check:
        asked.append(args[2])  # the cloud
        return Check(Judgement("did_not_answer"))

    monkeypatch.setattr(clouds_api, "check", check)
    with cloudlist.update(TILE) as cl:
        a = cl.add("drawn", _ring(), chosen="EOX")["id"]
        b = cl.add("drawn", _ring(), chosen="EOX")["id"]
    async with client_for(app) as c:
        r = await c.post("/api/clouds/+43+005/picks", json={"action": "tick"})  # Tick all
        assert r.json()["error"]["context"]["then"] == "check"
        assert all(x["tick"] for x in cloudlist.load(TILE).clouds)  # kept, both
        r = await c.post("/api/clouds/+43+005/check", json={"download": True})  # yes, for all
        assert r.status_code == 200, r.text
        for _ in range(200):
            if not (await c.get("/api/clouds/+43+005")).json()["busy"]:
                break
            await anyio.sleep(0.05)
        assert sorted(asked) == sorted([a, b])
        r = await c.post("/api/clouds/+43+005/picks", json={"action": "untick"})  # Untick all
        assert r.status_code == 200, r.text
        v = (await c.get("/api/clouds/+43+005")).json()
    assert not any(x["tick"] for x in v["clouds"]) and "untick" not in v["allowed"]
    assert v["clouds"][0]["tip"] == "clouds.tip_tick"


@pytest.mark.anyio
async def test_the_quick_search_reads_one_level_the_fine_one_two(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read: list[Any] = []
    monkeypatch.setattr(clouds_api, "detector_ready", lambda: True)
    monkeypatch.setattr(clouds_api, "opencv_ready", lambda: True)
    monkeypatch.setattr(clouds_api, "_tools", lambda task: None)
    monkeypatch.setattr(clouds_api, "fine_level", lambda zl, textures: zl + 1)
    monkeypatch.setattr(clouds_api, "find", lambda p, fine, **k: (read.append(fine), ([], None))[1])
    monkeypatch.setattr(clouds_api, "screen", lambda *a, **k: [])
    async with client_for(app) as c:
        assert (await c.get("/api/clouds/+43+005")).json()["fine"] is True
        for fine in (False, True):
            r = await c.post("/api/clouds/+43+005/find", json={"fine": fine})
            assert r.status_code == 200, r.text
            for _ in range(200):
                if not (await c.get("/api/clouds/+43+005")).json()["busy"]:
                    break
                await anyio.sleep(0.05)
    assert read[0] is None and read[1] is not None  # the fine one alone reads a finer picture


def test_a_cloud_task_keeps_the_engine_up_as_a_build_does() -> None:
    from types import SimpleNamespace

    from orthostudio.api.serve import _busy

    idle = SimpleNamespace(active=lambda: None)
    running = SimpleNamespace(tasks=SimpleNamespace(of=lambda: ["a search"]))
    assert _busy({"jobs": idle, "clouds": running})
    assert not _busy({"jobs": idle, "clouds": SimpleNamespace(tasks=SimpleNamespace(of=list))})


@pytest.mark.anyio
async def test_a_source_answering_worse_turns_the_cloud_to_the_next_whose_images_come_in_turn(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tick all left clouds waiting: their best source's check answered no picture, the next
    # source took its place, and nobody asked its images (a user, 2026-11-06)
    from orthostudio.clouds.find import Check, Judgement

    asked: list[str] = []
    monkeypatch.setattr(clouds_api, "_tools", lambda task: None)

    def check(*args: Any, **_k: Any) -> Check:
        asked.append(args[4])
        return Check(Judgement("no_picture" if args[4] == "EOX" else "did_not_answer"))

    monkeypatch.setattr(clouds_api, "check", check)
    screening = {"EOX": {"answer": "clear", "fit": 70}, "Arc": {"answer": "clear", "fit": 50}}
    with cloudlist.update(TILE) as cl:
        cid = cl.add("found", _ring(), screening=screening)["id"]
    async with client_for(app) as c:
        r = await c.post(
            "/api/clouds/+43+005/picks", json={"id": cid, "action": "tick", "download": True}
        )
        assert r.status_code == 200, r.text
        for _ in range(200):
            a = (await c.get("/api/clouds/+43+005")).json()
            if not a["busy"]:
                break
            await anyio.sleep(0.05)
    assert asked == ["EOX", "Arc"]  # each once: Arc not answering, the user asks it again
    (row,) = a["clouds"]
    assert row["line"]["key"] == "clouds.row.waiting.no_source"  # none is clear there now
    assert {m["source"]: m["proposable"] for m in row["menu"]} == {"EOX": False, "Arc": True}
