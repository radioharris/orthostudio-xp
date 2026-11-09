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
from orthostudio.api import app as app_module
from orthostudio.api import clouds_api
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.clouds import cloudlist, state
from orthostudio.clouds.picture import Picture, map_level
from orthostudio.clouds.tile import read_tile
from orthostudio.errors import OsxpError
from orthostudio.home import default_tiles_root
from orthostudio.install.library import Library, default_library_path
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


async def _library_size(c: Any) -> int:
    """The tile's size in the Library, as one reading of it says."""
    rows = (await c.get("/api/library")).json()
    row = next(r for r in rows if r["tile"] == TILE.name and r["kind"] == "ortho")
    assert not row["size_pending"]
    return int(row["size_bytes"])


def _ring() -> list[list[float]]:
    return [[43.1, 5.1, 43.1, 5.2, 43.2, 5.2, 43.1, 5.1]]


async def _read(c: Any, tile: str = "+43+005") -> dict[str, Any]:
    """The mode's answer once nothing is under way, its pictures drawn: read again while it says
    so, as the page does."""
    for _ in range(400):
        a = (await c.get(f"/api/clouds/{tile}")).json()
        if not a.get("busy"):
            return a
        await anyio.sleep(0.01)
    raise AssertionError(f"still busy: {a}")


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
        a = await _read(c)
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
async def test_without_the_tool_the_mode_opens_undrawn_and_a_search_asks_for_it(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # an installed app has no OpenCV until the user says yes, and the tile is drawn with it: the
    # mode said "the cloud detector could not be made ready" and offered nothing (a campaign on
    # Windows, 2026-11-07)
    monkeypatch.setattr(clouds_api, "opencv_ready", lambda: False)
    drawn: list[Any] = []
    monkeypatch.setattr(clouds_api.CloudEngine, "picture_file", lambda *a, **k: drawn.append(a))
    async with client_for(app) as c:
        a = (await c.get("/api/clouds/+43+005")).json()
        r = await c.post("/api/clouds/+43+005/find")
    assert a["state"] == "no_tool" and a["picture"] is None and drawn == []
    assert a["line"]["key"] == "clouds.tile.no_tool" and "find" in a["allowed"]
    assert r.json()["error"]["code"] == "CLOUD_DETECTOR_ASK"


@pytest.mark.anyio
async def test_the_download_asked_counts_opencv_once(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # an installed app has no OpenCV: it was added twice, 151 MB said for 108 (a review, 2026-11-07)
    from orthostudio.clouds import model

    monkeypatch.setattr(model, "opencv_bytes", lambda: 50_000_000)
    monkeypatch.setattr(clouds_api, "opencv_bytes", lambda: 50_000_000, raising=False)
    monkeypatch.setattr(clouds_api, "opencv_ready", lambda: False)
    async with client_for(app) as c:
        err = (await c.post("/api/clouds/+43+005/find")).json()["error"]
    assert err["code"] == "CLOUD_DETECTOR_ASK"
    assert err["context"]["mb"] == round(model.detector_bytes() / 1e6)


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
        # on yes, its images asked (the body of ``check`` the engine gives), not the tick again
        assert err["context"]["then"] == {"id": cid}
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
    hooks = clouds_api.CloudHooks(
        custom_scenery=lambda: None, taken=lambda: taken, changed=lambda folder: None
    )
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
@pytest.mark.parametrize("tool", [True, False])
async def test_apply_and_restore_with_nothing_to_do_are_refused(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch, tool: bool
) -> None:
    # the tool not here yet, the tile allows all as ready: a refused click said "build the tile
    # first" (a review, 2026-11-07)
    monkeypatch.setattr(clouds_api, "opencv_ready", lambda: tool)
    async with client_for(app) as c:
        for body in ({"action": "apply"}, {"action": "restore"}):
            r = await c.post("/api/clouds/+43+005/apply", json=body)
            assert r.json()["error"]["code"] == "CLOUD_NOT_NOW", body  # not "build it first"


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
    assert refused.json()["error"]["code"] == "CLOUD_NOT_NOW"  # a click the page drew before
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
    # counted as it was when its images began to be got: no "waiting for its images" for the
    # seconds they take (a user, 2026-11-07)
    assert a["texts"]["count"] == [{"key": "clouds.pending_none", "params": {}}]


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
        a = await _read(c)
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
        assert r.json()["error"]["context"]["then"] == {}  # every cloud's images
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
    monkeypatch.setattr(state, "fine_level", lambda zl, textures: zl + 1)
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
    running = SimpleNamespace(tasks=SimpleNamespace(busy=lambda: True))
    assert _busy({"jobs": idle, "clouds": running})
    resting = SimpleNamespace(tasks=SimpleNamespace(busy=lambda: False))
    assert not _busy({"jobs": idle, "clouds": resting})


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
    assert asked == ["EOX", "Arc"]  # each once
    (row,) = a["clouds"]
    # Arc did not answer: what a piece said is not what it has, it stays the cloud's source, and
    # the line offers to ask it again (a review, 2026-11-06: the cloud passed through every
    # source on a cut network, and ended on none)
    assert row["source"] == "Arc" and row["line"]["key"] == "clouds.row.waiting.did_not_answer"
    assert "check" in row["actions"]
    assert {m["source"]: m["proposable"] for m in row["menu"]} == {"EOX": False, "Arc": True}


@pytest.mark.anyio
async def test_an_outline_whose_try_is_stopped_leaves_the_list_as_it_was(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a review, 2026-11-06: stopped, it stayed with no source, the found cloud it covered gone
    from orthostudio.errors import OsxpError

    def screen(*_a: Any, cancel: Any, **_k: Any) -> Any:
        cancel.set()  # Stop clicked meanwhile
        raise OsxpError("SYS_CANCELLED")

    monkeypatch.setattr(clouds_api, "detector_ready", lambda: True)
    monkeypatch.setattr(clouds_api, "opencv_ready", lambda: True)
    monkeypatch.setattr(clouds_api, "_tools", lambda task: None)
    monkeypatch.setattr(clouds_api, "screen", screen)
    with cloudlist.update(TILE) as cl:
        found = cl.add("found", _ring(), screening={"EOX": {"answer": "clear", "fit": 70}})["id"]
    polygon = [[5.1, 43.1], [5.2, 43.1], [5.2, 43.2], [5.1, 43.2]]
    async with client_for(app) as c:
        r = await c.post("/api/clouds/+43+005/outlines", json={"polygon": polygon})
        assert r.status_code == 200, r.text
        for _ in range(200):
            if not (await c.get("/api/clouds/+43+005")).json()["busy"]:
                break
            await anyio.sleep(0.05)
    assert [x["id"] for x in cloudlist.load(TILE).clouds] == [found]


@pytest.mark.anyio
async def test_stop_all_stops_every_task_of_the_tile(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # after Tick all, two hundred tasks were stopped one by one (a review, 2026-11-06)
    from orthostudio.errors import OsxpError

    def check(*_a: Any, cancel: Any = None, **_k: Any) -> Any:
        while not cancel.wait(0.02):
            pass
        raise OsxpError("SYS_CANCELLED")

    monkeypatch.setattr(clouds_api, "_tools", lambda task: None)
    monkeypatch.setattr(clouds_api, "check", check)
    screening = {"EOX": {"answer": "clear", "fit": 70}}
    with cloudlist.update(TILE) as cl:
        for _ in range(2):
            cl.add("found", _ring(), screening=screening)
    async with client_for(app) as c:
        r = await c.post("/api/clouds/+43+005/picks", json={"action": "tick", "download": True})
        assert r.status_code == 200, r.text
        a = (await c.get("/api/clouds/+43+005")).json()
        assert "stop" in a["allowed"] and all(x["task"] for x in a["clouds"])
        r = await c.post("/api/clouds/+43+005/stop")
        assert r.json()["stopped"] == 2
        for _ in range(200):
            a = (await c.get("/api/clouds/+43+005")).json()
            if not a["busy"]:
                break
            await anyio.sleep(0.05)
    assert "stop" not in a["allowed"] and not any(x["task"] for x in a["clouds"])


@pytest.mark.anyio
async def test_restore_unticks_what_was_ticked_when_it_was_asked(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a cloud ticked while Restore ran was unticked at its end without a word (a review)
    from orthostudio.clouds.tile import Stamp, stamped

    texture = ready / "textures" / f"{NAME}.dds"
    data = texture.read_bytes()
    texture.write_bytes(stamped(data[:128], Stamp("a" * 40, "b" * 40)) + data[128:])
    screening = {"EOX": {"answer": "clear", "fit": 70}}
    with cloudlist.update(TILE) as cl:
        before = cl.add("found", _ring(), screening=screening, tick=True)["id"]
        meanwhile = cl.add("found", _ring(), screening=screening)["id"]

    def show(*_a: Any, **_k: Any) -> list[Any]:
        with cloudlist.update(TILE) as cl:
            cloudlist.pick(cl, meanwhile, "tick")  # the user, while it runs
        return []

    monkeypatch.setattr(clouds_api, "show", show)
    async with client_for(app) as c:
        r = await c.post("/api/clouds/+43+005/apply", json={"action": "restore"})
        assert r.status_code == 200, r.text
        for _ in range(200):
            if not (await c.get("/api/clouds/+43+005")).json()["busy"]:
                break
            await anyio.sleep(0.05)
    ticks = {x["id"]: x["tick"] for x in cloudlist.load(TILE).clouds}
    assert ticks == {before: False, meanwhile: True}


@pytest.mark.anyio
async def test_a_fine_search_is_refused_where_the_tile_has_nothing_finer(
    app: Any, ready: Path
) -> None:
    async with client_for(app) as c:
        assert (await c.get("/api/clouds/+43+005")).json()["fine"] is False
        r = await c.post("/api/clouds/+43+005/find", json={"fine": True, "download": True})
    assert r.json()["error"]["code"] == "CLOUD_NOT_NOW"


@pytest.mark.anyio
async def test_a_tile_with_no_photo_says_so_and_allows_nothing(app: Any, ready: Path) -> None:
    # the sea alone, no texture: opening its cloud mode was an internal error (a review)
    import json

    (ready / "textures" / f"{NAME}.dds").unlink()
    manifest = ready / "orthostudio.toml"
    text = manifest.read_text(encoding="utf-8").replace("textures = 1", "textures = 0")
    manifest.write_text(text, encoding="utf-8")
    async with client_for(app) as c:
        r = await c.get("/api/clouds/+43+005")
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["state"] == "no_photo" and a["allowed"] == [], json.dumps(a)[:300]
    assert a["line"] == {"key": "clouds.tile.no_photo", "params": {}}


@pytest.mark.anyio
async def test_get_its_images_asks_again_after_the_detectors_question_too(
    app: Any, ready: Path
) -> None:
    # on yes, the page sends what the engine said: Get its images kept its ``again`` (a review)
    with cloudlist.update(TILE) as cl:
        cid = cl.add(
            "found", _ring(), screening={"EOX": {"answer": "clear", "fit": 70}}, tick=True
        )["id"]
    async with client_for(app) as c:
        r = await c.post("/api/clouds/+43+005/check", json={"id": cid, "again": True})
    assert r.json()["error"]["context"]["then"] == {"id": cid, "again": True}


@pytest.mark.anyio
async def test_a_picture_already_made_is_not_drawn_again(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # drawn again at each start, two pictures took a user's first reading 3.75 s (2026-11-07)
    import time

    from orthostudio.clouds.tile import picture_path

    drawn: list[Any] = []
    draw = clouds_api._draw
    monkeypatch.setattr(clouds_api, "_draw", lambda *a: (drawn.append(a), draw(*a))[1])
    async with client_for(app) as c:
        first = await _read(c)
    assert len(drawn) == 1  # After is Before while nothing is laid: one picture
    hooks = clouds_api.CloudHooks(
        custom_scenery=lambda: None, taken=dict, changed=lambda folder: None
    )
    router, _engine = clouds_api.clouds_router(hooks)  # the app started again
    get = next(r.endpoint for r in router.routes if r.path == "/api/clouds/{name}")
    made = picture_path(TILE, "x").parent / first["picture"]["name"]
    at = made.stat().st_mtime_ns
    again = get("+43+005")
    assert again["picture"] == first["picture"] and not again["busy"]  # the page waits for none
    # the one in memory the first preview is made on drawn meanwhile, aside: once, its file kept
    # (asked by the first preview, the first tick after a start waited 1.2 to 2.1 s, a user)
    for _ in range(500):
        if len(drawn) == 2:
            break
        time.sleep(0.01)
    get("+43+005")
    time.sleep(0.1)
    assert len(drawn) == 2 and made.stat().st_mtime_ns == at


@pytest.mark.anyio
async def test_the_answer_never_waits_for_a_picture(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # drawn in the answer, the page stood still 10 to 37 s on a Windows server at each new state
    # of a tile of 3 M triangles, a search's progress included (+52+004, 2026-11-07)
    import threading
    import time

    go = threading.Event()
    draw = clouds_api._draw
    monkeypatch.setattr(clouds_api, "_draw", lambda *a: (go.wait(10), draw(*a))[1])
    async with client_for(app) as c:
        t = time.monotonic()
        a = (await c.get("/api/clouds/+43+005")).json()
        assert time.monotonic() - t < 2 and a["busy"] is True  # the page reads again
        assert a["picture"] is None and a["after"] is None  # none drawn yet: nothing to show
        assert a["views"] == {"after": "clouds.drawing", "before": "clouds.drawing"}
        assert a["allowed"] == ["find", "outline"]  # the tile's actions wait for no picture
        go.set()
        b = await _read(c)
    assert b["picture"] is not None and b["views"]["before"] == "clouds.before"


@pytest.mark.anyio
async def test_the_last_picture_stands_in_while_its_next_is_drawn(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from orthostudio.clouds.tile import picture_path

    async with client_for(app) as c:
        first = await _read(c)
        go = threading.Event()
        draw = clouds_api._draw
        monkeypatch.setattr(clouds_api, "_draw", lambda *a: (go.wait(10), draw(*a))[1])
        made = picture_path(TILE, "x").parent / first["picture"]["name"]
        made.unlink()  # what the tile shows to draw again
        a = (await c.get("/api/clouds/+43+005")).json()
        assert a["busy"] is True and a["picture"] == first["picture"]  # the last one meanwhile
        assert a["views"]["before"] == "clouds.drawing"
        go.set()
        b = await _read(c)
    assert b["picture"] == first["picture"] and made.exists()


def test_a_view_stands_in_whole_and_only_for_its_build() -> None:
    # After's picture alone showed its clouds at the end of an Apply; one of the build before
    # showed the old tile after Build again (a user, 2026-11-07)
    engine = clouds_api.CloudEngine(
        clouds_api.CloudHooks(custom_scenery=lambda: None, taken=dict, changed=lambda folder: None)
    )
    whole = {"picture": {"name": "a.jpg"}, "previews": {"c1": {"name": "p.png"}}}
    assert engine.ground(TILE, "after", "b1", whole) == whole
    assert engine.ground(TILE, "after", "b1", None) == whole  # its previews with it
    assert engine.ground(TILE, "after", "b2", None) is None  # another build of the tile: none
    assert engine.ground(TILE, "after", None, None) == whole  # its folder not read now: the last
    assert engine.ground(TILE, "before", "b1", None) is None


def test_while_a_build_holds_the_tile_its_map_stays_as_it_was(ready: Path) -> None:
    # none while a build held the tile took its picture away until its end, a blink when the
    # mode was opened again (a user, 2026-11-07)
    import time

    taken: dict[str, str] = {}
    hooks = clouds_api.CloudHooks(
        custom_scenery=lambda: None, taken=lambda: taken, changed=lambda folder: None
    )
    router, _engine = clouds_api.clouds_router(hooks)
    get = next(r.endpoint for r in router.routes if r.path == "/api/clouds/{name}")
    for _ in range(200):
        first = get("+43+005")
        if not first["busy"]:
            break
        time.sleep(0.01)
    taken[TILE.name] = "in_build"
    held = get("+43+005")
    assert held["state"] == "in_build" and held["allowed"] == []
    assert held["picture"] == first["picture"] and held["after"] == first["after"]


@pytest.mark.anyio
async def test_after_build_again_the_old_tile_is_not_shown_while_its_picture_is_drawn(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    async with client_for(app) as c:
        first = await _read(c)
        go = threading.Event()
        draw = clouds_api._draw
        monkeypatch.setattr(clouds_api, "_draw", lambda *a: (go.wait(10), draw(*a))[1])
        files = {"dsf": TILE.dsf_relpath.as_posix(), "dsf_size": 108, "textures": 1, "terrain": 0}
        entries = {"textures": ArtefactEntry("k2", "d", "r@2")}  # built again: another build
        write_manifest(ready, TILE, PackManifest(TILE.name, "BI", 12, entries, files))
        a = (await c.get("/api/clouds/+43+005")).json()
        assert a["busy"] is True and a["picture"] is None and a["after"] is None
        assert a["views"] == {"after": "clouds.drawing", "before": "clouds.drawing"}
        go.set()
        b = await _read(c)
    assert b["picture"] is not None and b["picture"] != first["picture"]


def test_what_runs_of_a_tile_is_said_without_reading_or_drawing_it(
    ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the Library's bin waited on the mode's answer, which drew the picture of a mode never
    # opened (a campaign, 2026-11-07)
    import threading
    import time

    drawn: list[Any] = []
    monkeypatch.setattr(clouds_api, "_draw", lambda *a: drawn.append(a))
    monkeypatch.setattr(clouds_api, "read_tile", lambda folder: drawn.append(folder))
    hooks = clouds_api.CloudHooks(
        custom_scenery=lambda: None, taken=dict, changed=lambda folder: None
    )
    router, engine = clouds_api.clouds_router(hooks)
    tasks = next(r.endpoint for r in router.routes if r.path == "/api/clouds/{name}/tasks")
    assert tasks("+43+005") == {"busy": False, "failed": None}
    go = threading.Event()

    def broken(task: Any) -> None:
        go.wait(10)
        raise OsxpError("CLOUD_TEXTURE_LOST", context={"tile": TILE.name, "path": "x"})

    engine.tasks.start(TILE.name, "forget", broken)
    assert tasks("+43+005")["busy"] is True
    go.set()
    for _ in range(200):
        said = tasks("+43+005")
        if not said["busy"]:
            break
        time.sleep(0.01)
    assert said["failed"]["kind"] == "forget"
    assert said["failed"]["error"]["code"] == "CLOUD_TEXTURE_LOST"
    assert drawn == []  # nothing of the tile read or drawn


def test_no_picture_is_asked_while_what_the_tile_shows_is_written(
    ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # asked during a Restore, a picture of a state between two drew 27 s for nothing on a
    # Windows server (2026-11-07): the pictures wait for the end of Apply, Restore or Forget
    import threading
    import time

    drawn: list[Any] = []
    draw = clouds_api._draw
    monkeypatch.setattr(clouds_api, "_draw", lambda *a: (drawn.append(a), draw(*a))[1])
    hooks = clouds_api.CloudHooks(
        custom_scenery=lambda: None, taken=dict, changed=lambda folder: None
    )
    router, engine = clouds_api.clouds_router(hooks)
    get = next(r.endpoint for r in router.routes if r.path == "/api/clouds/{name}")
    go = threading.Event()
    engine.tasks.start(TILE.name, "restore", lambda task: go.wait(10))
    a = get("+43+005")
    assert a["busy"] and a["picture"] is None and a["views"] == {"after": None, "before": None}
    time.sleep(0.2)
    assert drawn == []  # nothing asked
    go.set()
    for _ in range(200):
        if not engine.tasks.busy(TILE.name):
            break
        time.sleep(0.01)
    b = get("+43+005")  # its end: asked now
    assert b["busy"] and b["views"]["before"] == "clouds.drawing"
    for _ in range(200):
        b = get("+43+005")
        if not b["busy"]:
            break
        time.sleep(0.01)
    assert b["picture"] is not None and len(drawn) == 1


@pytest.mark.anyio
async def test_a_picture_that_could_not_be_drawn_is_said_then_asked_again_later(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # asked again at once, it failed again and its error came back every second while the mode
    # was open; never again, a disk asleep for a moment kept the map away until the tile changed
    # (two reviews, 2026-11-07): said in the answer, asked again ``_AGAIN_S`` after
    drawn: list[Any] = []
    draw = clouds_api._draw
    fail = [True]

    def flaky(*a: Any) -> Any:
        drawn.append(a)
        if fail[0]:
            raise OsxpError("CLOUD_NOT_BUILT", context={"tile": TILE.name, "reason": "cut"})
        return draw(*a)

    monkeypatch.setattr(clouds_api, "_draw", flaky)
    async with client_for(app) as c:
        for _ in range(400):  # Before's file and the one in memory, in either order (After is
            # Before while no cloud is laid): both failed, neither said drawn
            a = (await c.get("/api/clouds/+43+005")).json()
            if a["failed"] and "clouds.drawing" not in a["views"].values() and len(drawn) == 2:
                break
            await anyio.sleep(0.01)
        assert a["failed"]["kind"] == "picture"
        assert a["failed"]["error"]["code"] == "CLOUD_NOT_BUILT"
        for _ in range(3):  # read again for its next try, not asked again meanwhile
            again = (await c.get("/api/clouds/+43+005")).json()
            assert again["busy"] is True and again["failed"] == a["failed"]
            await anyio.sleep(0.05)
        assert len(drawn) == 2
        fail[0] = False  # the disk back
        monkeypatch.setattr(clouds_api, "_AGAIN_S", 0.0)
        b = await _read(c)
    assert len(drawn) > 2 and b["picture"] is not None and b["failed"] is None


@pytest.mark.anyio
async def test_a_picture_cut_by_the_disk_going_is_asked_again_once_it_is_back(
    ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # its failure said 30 s after the disk was back, asking to plug in a disk that was there (two
    # reviews, 2026-11-08): what a task says the same way (``Tasks.failed``)
    from orthostudio.clouds import tasks as tasks_module

    gone: list[Path | None] = [ready.parent / "unplugged"]
    monkeypatch.setattr(tasks_module, "data_root_missing", lambda: gone[0])
    drawn = clouds_api._draw  # the ready tile's small picture

    def cut(*a: Any) -> Any:
        raise PermissionError(13, "Permission denied", str(gone[0]))

    monkeypatch.setattr(clouds_api, "_draw", cut)
    engine = clouds_api.CloudEngine(
        clouds_api.CloudHooks(custom_scenery=lambda: None, taken=dict, changed=lambda folder: None)
    )
    shown = read_tile(ready)
    assert shown is not None
    zl = map_level(TILE, shown.textures)
    engine.held(TILE, shown, zl)
    with pytest.raises(clouds_api._PictureError) as got:
        for _ in range(400):
            engine.held(TILE, shown, zl, ask=False)
            await anyio.sleep(0.01)
    assert got.value.said["code"] == "CFG_DATA_DIR_MISSING"
    gone[0] = None  # the disk back
    monkeypatch.setattr(clouds_api, "_draw", drawn)
    assert engine.held(TILE, shown, zl, ask=False) is None  # no more said
    engine.held(TILE, shown, zl)  # and drawn again at once, not 30 s after
    for _ in range(400):
        if engine.held(TILE, shown, zl, ask=False) is not None:
            break
        await anyio.sleep(0.01)
    assert engine.held(TILE, shown, zl, ask=False) is not None


@pytest.mark.anyio
async def test_a_picture_that_could_not_be_drawn_leaves_the_others_laid(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # caught for all three at once, the picture in memory that failed took Before and After away
    # with it, the map empty when no answer had laid them yet (a review, 2026-11-07)
    async with client_for(app) as c:
        first = await _read(c)  # their files made
    hooks = clouds_api.CloudHooks(
        custom_scenery=lambda: None, taken=dict, changed=lambda folder: None
    )
    router, engine = clouds_api.clouds_router(hooks)  # the app started again: none in memory
    get = next(r.endpoint for r in router.routes if r.path == "/api/clouds/{name}")

    def broken(*a: Any) -> Any:
        raise OsxpError("CLOUD_NOT_BUILT", context={"tile": TILE.name, "reason": "cut"})

    monkeypatch.setattr(clouds_api, "_draw", broken)
    shown = read_tile(ready)
    assert shown is not None
    zl = map_level(TILE, shown.textures)
    engine.held(TILE, shown, zl)  # its failure known before the first answer
    with pytest.raises(clouds_api._PictureError):
        for _ in range(400):
            engine.held(TILE, shown, zl, ask=False)
            await anyio.sleep(0.01)
    a = get("+43+005")
    assert a["failed"]["kind"] == "picture" and a["failed"]["error"]["code"] == "CLOUD_NOT_BUILT"
    assert a["picture"] == first["picture"] and a["after"] == first["after"]


def test_where_a_picture_lies_is_its_frame_drawn_or_not() -> None:
    from orthostudio.clouds.picture import box_bounds, frame, frame_bounds

    for zl in (11, 13, 15):
        x0, y0, w, h = frame(TILE, zl)
        blank = np.zeros((h, w), np.uint8)
        p = Picture(np.zeros((h, w, 3), np.uint8), blank, blank, blank, (), zl, x0, y0)
        assert frame_bounds(TILE, zl) == box_bounds(p, (0, 0, w, h))


@pytest.mark.anyio
async def test_forget_restores_the_tile_then_erases_its_clouds(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # no way was there to wipe a tile's clouds and start again: Forget is Restore, then its list,
    # pictures and parts erased (a user, 2026-11-07)
    from orthostudio.clouds.tile import Stamp, part_path, previews_dir, stamped

    texture = ready / "textures" / f"{NAME}.dds"
    data = texture.read_bytes()
    texture.write_bytes(stamped(data[:128], Stamp("a" * 40, "b" * 40)) + data[128:])
    part = part_path("p" * 64)
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(b"a part")
    with cloudlist.update(TILE) as cl:
        c = cl.add("found", _ring(), screening={"EOX": {"answer": "clear", "fit": 70}}, tick=True)
        entry = {
            "id": c["id"],
            "source": "EOX",
            "match": [[1, 1, 1], [0, 0, 0]],
            "rings": _ring(),
            "parts": {NAME: [12, "p" * 64]},
            "plain": {NAME: "a" * 40},
        }
        c["checks"] = {"EOX": {"answer": "clear", "fit": 70, "entry": entry}}
    assert cloudlist.load(TILE).clouds  # a whole list
    previews_dir(TILE).mkdir(parents=True, exist_ok=True)
    (previews_dir(TILE) / "a.png").write_bytes(b"png")
    shown: list[Any] = []

    def show(tile: Any, folder: Path, laid: Any, **k: Any) -> None:
        shown.append(laid)
        (folder / "textures" / "laid.dds").write_bytes(b"\0" * 100_000)  # the folder changed

    monkeypatch.setattr(clouds_api, "show", show)
    with Library(default_library_path()) as lib:
        lib.register(TILE, "BI", 12, ready, "osxp", {})
    async with client_for(app) as c:
        size = await _library_size(c)  # read and remembered
        for _ in range(200):  # its stamp checked after the answer: done before the folder changes
            if not app_module._PACK_SIZES_MEASURING:
                break
            await anyio.sleep(0.02)
        assert "forget" in (await c.get("/api/clouds/+43+005")).json()["allowed"]
        r = await c.post("/api/clouds/+43+005/apply", json={"action": "forget"})
        assert r.status_code == 200, r.text
        for _ in range(200):
            if not (await c.get("/api/clouds/+43+005")).json()["busy"]:
                break
            await anyio.sleep(0.05)
        # the Library's size of the folder, at its first reading after: it showed the one
        # before until it was read twice (a campaign, 2026-11-08)
        assert await _library_size(c) > size
    assert shown == [[]]  # Restore's own laying: nothing
    # the readings after drew the tile's picture again, as any opening does: the old preview went
    assert not cloudlist.list_path(TILE).exists() and not (previews_dir(TILE) / "a.png").exists()
    assert not part.exists()


@pytest.mark.anyio
async def test_a_restore_that_fails_midway_has_its_folder_measured_again(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # what it wrote before a failure, a disk full, left the Library's size the one before until it
    # was read twice (a review, 2026-11-08)
    from orthostudio.clouds.tile import Stamp, stamped

    texture = ready / "textures" / f"{NAME}.dds"  # a cloud laid there: Restore offered
    data = texture.read_bytes()
    texture.write_bytes(stamped(data[:128], Stamp("a" * 40, "b" * 40)) + data[128:])

    def show(tile: Any, folder: Path, laid: Any, **k: Any) -> None:
        (folder / "textures" / "laid.dds").write_bytes(b"\0" * 100_000)
        raise OsxpError("SYS_DISK_FULL", context={"path": str(folder)})

    monkeypatch.setattr(clouds_api, "show", show)
    with Library(default_library_path()) as lib:
        lib.register(TILE, "BI", 12, ready, "osxp", {})
    async with client_for(app) as c:
        size = await _library_size(c)
        for _ in range(200):  # its stamp checked after the answer: done before the folder changes
            if not app_module._PACK_SIZES_MEASURING:
                break
            await anyio.sleep(0.02)
        r = await c.post("/api/clouds/+43+005/apply", json={"action": "restore"})
        assert r.status_code == 200, r.text
        for _ in range(200):
            a = (await c.get("/api/clouds/+43+005")).json()
            if not a["busy"]:
                break
            await anyio.sleep(0.05)
        assert a["failed"]["error"]["code"] == "SYS_DISK_FULL"
        assert await _library_size(c) > size


def test_only_before_is_kept_in_memory(ready: Path) -> None:
    # After's pictures kept, five of them took 1.5 GB and pushed Before out, which every preview,
    # search and cloud's images is made on, drawn again then (a review, 2026-11-07)
    _router, engine = clouds_api.clouds_router(
        clouds_api.CloudHooks(custom_scenery=lambda: None, taken=dict, changed=lambda folder: None)
    )
    shown = read_tile(ready)
    assert shown is not None
    zl = map_level(TILE, shown.textures)
    engine.drawn(TILE, shown, zl, frozenset({NAME}))  # After: its file alone is read
    assert engine.held(TILE, shown, zl, ask=False) is None
    engine.drawn(TILE, shown, zl)  # Before
    assert engine.held(TILE, shown, zl, ask=False) is not None


def test_one_before_a_tile_and_two_tiles_are_kept_in_memory(ready: Path) -> None:
    # five kept, each 100 to 385 MB, took up to 1.9 GB until a tile was deleted (a review,
    # 2026-11-07): a tile's newest build's alone, and two tiles
    _router, engine = clouds_api.clouds_router(
        clouds_api.CloudHooks(custom_scenery=lambda: None, taken=dict, changed=lambda folder: None)
    )
    shown = read_tile(ready)
    assert shown is not None
    zl = map_level(TILE, shown.textures)
    old: Any = object()
    engine._pictures = {
        ("+01+001", "a"): old,
        ("+02+002", "b"): old,
        (TILE.name, "built before"): old,
    }
    engine.drawn(TILE, shown, zl)
    assert [k[0] for k in engine._pictures] == ["+02+002", TILE.name]
    assert (TILE.name, "built before") not in engine._pictures
    # the tile used last kept: a third one lets the other go, not the one opened first and opened
    # again (a review, 2026-11-07)
    engine._pictures = {(TILE.name, clouds_api._what(shown, zl, None)): old, ("+02+002", "b"): old}
    assert engine.held(TILE, shown, zl, ask=False) is old
    engine.drawn(TileRef.parse("+44+005"), shown, zl)
    assert [k[0] for k in engine._pictures] == [TILE.name, "+44+005"]


@pytest.mark.anyio
async def test_a_search_says_its_tile_is_drawn_not_that_it_waits_for_its_turn(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a search said "waiting for its turn" the 20 s its tile was drawn as the mode opened, nothing
    # else searching (a Windows server, a campaign, 2026-11-08)
    import threading

    go = threading.Event()
    real = clouds_api._draw

    def slow(*a: Any) -> Any:
        go.wait(10)
        return real(*a)

    monkeypatch.setattr(clouds_api, "_draw", slow)
    monkeypatch.setattr(clouds_api, "_tools", lambda task: None)
    async with client_for(app) as c:
        r = await c.post("/api/clouds/+43+005/find", json={"download": True})
        assert r.status_code == 200, r.text
        phases: list[str] = []
        for _ in range(200):
            a = (await c.get("/api/clouds/+43+005")).json()
            phases += [t["phase"] for t in a["tasks"]]
            if "drawing" in phases:
                break
            await anyio.sleep(0.02)
        await c.post("/api/clouds/+43+005/stop")
        go.set()
        for _ in range(200):
            if not (await c.get("/api/clouds/+43+005")).json()["busy"]:
                break
            await anyio.sleep(0.05)
    assert phases[-1] == "drawing"


def test_the_tools_download_hears_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stop went unheard by the detector's download: it went on, tried again four times, and held
    # Quit, a build, Delete and filing meanwhile (a review, 2026-11-07)
    from orthostudio.clouds.tasks import Task

    got: dict[str, Any] = {}
    monkeypatch.setattr(clouds_api, "ensure_detector", lambda **k: got.update(k))
    task = Task("t1", TILE.name, "find", None, True, None)
    clouds_api._tools(task)
    assert got["cancel"] is task.stop


@pytest.mark.anyio
async def test_apply_asks_for_the_tool_that_lays_and_fetches_it(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # without OpenCV (a new version's, its folder cleaned), Apply failed saying "check the
    # internet connection" (a review, 2026-11-07): asked for as a search asks, fetched in its task
    from orthostudio.clouds.sources import JUDGED
    from orthostudio.clouds.tile import file_digest, part_path

    held = read_tile(ready)
    assert held is not None
    loose = part_path("p" * 64)  # its own digest names a part: written, then named so
    loose.parent.mkdir(parents=True, exist_ok=True)
    loose.write_bytes(b"a part")
    digest = file_digest(loose)
    loose.rename(part_path(digest))
    with cloudlist.update(TILE) as cl:
        c = cl.add("found", _ring(), screening={"EOX": {"answer": "clear", "fit": 70}}, tick=True)
        entry = {
            "id": c["id"],
            "source": "EOX",
            "match": [[1, 1, 1], [0, 0, 0]],
            "rings": _ring(),
            "parts": {NAME: [12, digest]},
            "plain": {NAME: held.plain(NAME)},
        }
        c["checks"] = {"EOX": {"answer": "clear", "fit": 70, "entry": entry, "judged": JUDGED}}
    monkeypatch.setattr(clouds_api, "detector_ready", lambda: False)
    monkeypatch.setattr(clouds_api, "opencv_ready", lambda: False)
    fetched: list[str] = []
    laid: list[int] = []
    monkeypatch.setattr(clouds_api, "_tools", lambda task: fetched.append(task.kind))
    monkeypatch.setattr(
        clouds_api, "show", lambda tile, folder, asked, **k: laid.append(len(asked))
    )
    async with client_for(app) as c:
        r = await c.post("/api/clouds/+43+005/apply", json={"action": "apply"})
        assert r.json()["error"]["code"] == "CLOUD_DETECTOR_ASK", r.text
        assert not fetched and not laid
        r = await c.post("/api/clouds/+43+005/apply", json={"action": "apply", "download": True})
        assert r.status_code == 200, r.text
        for _ in range(200):
            if not (await c.get("/api/clouds/+43+005")).json()["busy"]:
                break
            await anyio.sleep(0.05)
    assert fetched == ["apply"] and laid == [1]


@pytest.mark.anyio
async def test_an_outline_stopped_before_its_turn_leaves(
    app: Any, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # three reads of sources under way, an outline waiting for its turn stopped stayed in the
    # list, "no source" and a bin (a review, 2026-11-07): what a task began is undone either way
    monkeypatch.setattr(clouds_api, "detector_ready", lambda: True)
    monkeypatch.setattr(clouds_api, "opencv_ready", lambda: True)
    monkeypatch.setattr(clouds_api, "_tools", lambda task: None)
    engine = app.state.orthostudio["clouds"]
    gate = engine.tasks._checks
    for _ in range(3):
        gate.acquire()
    try:
        async with client_for(app) as c:
            polygon = {"polygon": [[5.1, 43.1], [5.2, 43.1], [5.2, 43.2]], "download": True}
            cid = (await c.post("/api/clouds/+43+005/outlines", json=polygon)).json()["id"]
            assert cid in [x["id"] for x in cloudlist.load(TILE).clouds]  # shown while it waits
            task = next(t for t in engine.tasks.of(TILE.name) if t.cloud == cid)
            assert engine.tasks.stop(task.id)
            for _ in range(200):
                if not engine.tasks.busy(TILE.name):
                    break
                await anyio.sleep(0.02)
    finally:
        for _ in range(3):
            gate.release()
    assert cid not in [x["id"] for x in cloudlist.load(TILE).clouds]
