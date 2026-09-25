"""Jobs of the API (spec ``docs/specs/api.md`` section 5): the manager alone, then over HTTP.

A fake ``build_tiles`` plays synthetic scheduler events (success, failure with a code,
cancellation); no network, no Ortho4XP.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

import test_api_fakes as fakes
from orthostudio.api import (
    JobBusyError,
    JobManager,
    TileInBuildError,
    action_for,
    parse_node_id,
    stage_of,
)
from orthostudio.api.app import create_app
from orthostudio.api.jobs import error_json
from orthostudio.errors import OsxpError
from orthostudio.sources.osm import MIRRORS, shared_board
from test_api_fakes import FakeBuild, client_for, make_spec, sse_messages

anyio_backend = fakes.anyio_backend
home = fakes.home
xplane = fakes.xplane

STAGES = ("data", "terrain", "coast", "imagery", "assembly", "install")


def test_node_ids_map_to_the_six_stages() -> None:
    assert parse_node_id("+43+005/vectors") == ("+43+005", "vectors")
    assert parse_node_id("+43+005/BI14/dsf#2") == ("+43+005", "dsf")
    assert [stage_of(r) for r in ("vectors", "mesh", "masks", "textures", "install")] == [
        "data", "terrain", "coast", "imagery", "install"
    ]  # fmt: skip
    assert {stage_of(r) for r in ("xp12", "dsf", "overlay", "pack")} == {"assembly"}
    assert stage_of("repair") is None


def test_action_for_codes() -> None:
    assert action_for("TEX_MISSING") == "retry"
    assert action_for("IMG_TILE_MISSING") == "retry" and action_for("NET_TIMEOUT") == "retry"
    assert action_for("CFG_VALUE_INVALID") == "settings"
    assert action_for("XP_DIR_NOT_FOUND") == "settings"
    assert action_for("XP_GLOBAL_SCENERY_NOT_FOUND") == "settings"
    assert action_for("DSF_GLOBAL_SCENERY_MISSING") == "settings"
    assert action_for("XP_RUNNING") == "none"
    assert action_for("MESH_TRIANGULATION_FAILED") == "none"
    e = error_json(OsxpError("TEX_MISSING", context={"count": 1, "total": 2, "tile": "+43+005"}))
    assert e["action"] == "retry" and e["node_action"] == "stop" and e["code"] == "TEX_MISSING"


def _manager(home: Path, build: FakeBuild) -> JobManager:
    return JobManager(jobs_dir=home / "jobs", build=build, env_factory=lambda specs: None)


def test_manager_runs_a_job_to_done(home: Path) -> None:
    build = FakeBuild()
    mgr = _manager(home, build)
    job = mgr.start(
        [make_spec("+43+005", home=home)], install=False, request={"tiles": ["+43+005"]}
    )
    # start does not wait for the build, which the fake may finish before this line runs
    assert job.status in ("queued", "running", "done")
    assert job.wait(10.0)
    st = job.state()
    assert st["status"] == "done" and st["report"]["ok"] and st["last_seq"] > 10
    (tile,) = st["tiles"]
    assert tile["status"] == "done" and list(tile["stages"]) == list(STAGES)
    assert tile["stages"]["data"]["status"] == "done"
    assert tile["stages"]["assembly"]["status"] == "done"
    assert tile["stages"]["install"]["status"] == "skipped"  # no install requested
    assert len(tile["stages"]["assembly"]["nodes"]) == 4  # xp12, dsf, overlay, pack
    kinds = [e["event"] for e in job.events()]
    assert kinds[0] == "log" and kinds[-1] == "finished"
    assert {"started", "progress", "done", "stats", "log"} <= set(kinds)
    logs = [e for e in job.events() if e["event"] == "log" and e.get("stage") == "imagery"]
    assert logs and "req/s" in logs[0]["message"]
    # job-level stats (api.md 5.6): the last one says the whole job is done, nothing is left
    stats = [e for e in job.events() if e["event"] == "stats"][-1]["stats"]
    assert stats["progress"] == 1.0 and stats["eta_low_s"] == 0.0 and stats["eta_high_s"] == 0.0
    assert stats["phase"] == "build" and stats["done"] == 11 and stats["pending"] == 0
    assert st["stats"] == stats and st["eta"] is None
    decisions = {d["kind"] for d in st["decisions"]}
    assert {"nodes", "stage_time"} <= decisions
    nodes = next(d for d in st["decisions"] if d["kind"] == "nodes")
    # the orthostudio.osm node of phase 0 is an expected node of the page too (api/stages.py
    # ROLE_STAGE, blocker B4), and so are the elevation and coastline rows (api.md 5.2): 11.
    assert nodes["built"] == 11 and nodes["hit"] == 0
    # journal on disk, one JSON per line, dense sequence numbers
    lines = (home / "jobs" / f"{job.id}.jsonl").read_text().splitlines()
    assert [json.loads(line)["seq"] for line in lines] == list(range(1, len(lines) + 1))
    assert (home / "jobs" / f"{job.id}.json").is_file()


def test_a_started_job_gives_every_overpass_mirror_another_chance(home: Path) -> None:
    """2026-09-22: two Overpass machines were down, and the breaker then refused every later
    build in four seconds for up to an hour, quitting the app being the only way out. A build
    the user asks for says what the breaker cannot know: try them all again."""
    board = shared_board()
    board.register(MIRRORS, 600.0)
    board.open(MIRRORS[0].code, reason="HTTP 504")
    assert board.state(MIRRORS[0].code, time.monotonic()) == "open"

    mgr = _manager(home, FakeBuild())
    job = mgr.start([make_spec("+43+005", home=home)], install=False)
    assert job.wait(10.0)
    assert board.state(MIRRORS[0].code, time.monotonic()) == "closed"


def test_manager_failure_then_retry_hits(home: Path) -> None:
    build = FakeBuild(fail={"textures": "TEX_MISSING"})
    mgr = _manager(home, build)
    job = mgr.start([make_spec(home=home)], install=False)
    assert job.wait(10.0)
    st = job.state()
    assert st["status"] == "failed"
    (tile,) = st["tiles"]
    assert tile["status"] == "failed"
    assert tile["stages"]["imagery"]["status"] == "failed"
    assert tile["stages"]["assembly"]["status"] == "skipped"  # pack skipped after textures
    (err,) = st["errors"]
    assert err["code"] == "TEX_MISSING" and err["action"] == "retry" and err["stage"] == "imagery"
    assert err["remedy"]
    build.fail.clear()
    again = mgr.retry(job.id)
    assert again.id != job.id and again.request["retry_of"] == job.id
    assert again.wait(10.0)
    st2 = again.state()
    assert st2["status"] == "done"
    nodes = next(d for d in st2["decisions"] if d["kind"] == "nodes")
    # osm..dsf were committed the first time: osm, dem, vectors, coastline, mesh, masks, xp12,
    # dsf
    assert nodes["hit"] == 8 and nodes["built"] == 3
    (tile2,) = st2["tiles"]
    assert (
        tile2["stages"]["data"]["status"] == "hit"
        and tile2["stages"]["imagery"]["status"] == "done"
    )


def test_manager_busy_and_cancel(home: Path) -> None:
    build = FakeBuild(delay_s=0.05)
    mgr = _manager(home, build)
    job = mgr.start([make_spec(home=home), make_spec("+44+005", home=home)])
    with pytest.raises(JobBusyError):
        mgr.start([make_spec(home=home)])
    assert mgr.active() is job
    time.sleep(0.12)
    assert mgr.cancel(job.id)
    assert job.wait(10.0)
    assert job.status == "cancelled" and mgr.active() is None
    st = job.state()
    assert st["report"] is None and st["eta"] is None
    assert st["tiles"][1]["status"] == "cancelled"
    assert job.events()[-1]["event"] == "finished" and job.events()[-1]["status"] == "cancelled"
    assert not mgr.cancel(job.id)  # already finished
    # the queue: a queued job starts after the active one
    build2 = FakeBuild(delay_s=0.02)
    mgr2 = _manager(home, build2)
    first = mgr2.start([make_spec(home=home)])
    second = mgr2.start([make_spec("+44+005", home=home)], queue=True)
    assert second.status == "queued" and mgr2.active() is first
    assert first.wait(10.0) and second.wait(10.0)
    assert first.status == "done" and second.status == "done"


def test_a_tile_is_in_one_build_at_a_time(home: Path) -> None:
    """A user could choose on the map a tile being built and queue it: it would have been built
    again once the first build ended, and installed twice. The manager refuses a tile of the
    running job or of a queued one, naming both, and takes it again once that job has ended."""
    mgr = _manager(home, FakeBuild(delay_s=0.05))
    try:
        first = mgr.start([make_spec("+43+005", home=home), make_spec("+44+005", home=home)])
        with pytest.raises(TileInBuildError) as refused:
            mgr.start(
                [make_spec("+45+005", home=home), make_spec("+44+005", home=home)], queue=True
            )
        assert refused.value.tiles == ["+44+005"] and refused.value.job_ids == [first.id]
        second = mgr.start([make_spec("+45+005", home=home)], queue=True)
        assert second.status == "queued" and mgr.queue_position(second.id) == 1
        with pytest.raises(TileInBuildError) as refused:
            mgr.start([make_spec("+45+005", home=home)], queue=True)
        assert refused.value.job_ids == [second.id]
        assert mgr.building_tiles() == {
            "+43+005": first.id, "+44+005": first.id, "+45+005": second.id
        }  # fmt: skip
        assert mgr.queue_position(first.id) == 0
        # a tile the running job has finished is free again while the job goes on (a user was
        # refused +27+035, built and installed, while the other tiles of its build ran)
        deadline = time.monotonic() + 30
        while first.state()["tiles"][0]["status"] != "done" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert first.state()["tiles"][0]["status"] == "done" and not first.finished
        assert mgr.building_tiles() == {"+44+005": first.id, "+45+005": second.id}
        again = mgr.start([make_spec("+43+005", home=home)], queue=True)
        assert again.status == "queued" and mgr.queue_position(again.id) == 2
        assert first.wait(30) and second.wait(30) and again.wait(30)
        assert first.status == second.status == again.status == "done"
        assert mgr.building_tiles() == {} and mgr.queue_position(second.id) == 0
        after = mgr.start([make_spec("+44+005", home=home)])
        assert after.wait(30) and after.status == "done"
    finally:
        mgr.close()


def test_cancel_all_cancels_the_queue_before_the_running_job(home: Path) -> None:
    """Quit cancels every build: the running one last, since the next job of the queue would start
    the moment it ends."""
    mgr = _manager(home, FakeBuild(delay_s=0.05))
    try:
        running = mgr.start([make_spec("+43+005", home=home)])
        queued = [mgr.start([make_spec(t, home=home)], queue=True) for t in ("+44+005", "+45+005")]
        assert mgr.cancel_all() == [queued[0].id, queued[1].id, running.id]
        assert running.wait(30) and all(job.wait(30) for job in queued)
        assert [job.status for job in (running, *queued)] == ["cancelled"] * 3
        assert [job.started_at for job in queued] == [None, None]
        assert mgr.active() is None and mgr.cancel_all() == []
    finally:
        mgr.close()


def test_manager_reports_a_raising_build(home: Path) -> None:
    def boom(specs, *, on_event, env=None):  # type: ignore[no-untyped-def]
        raise OsxpError("XP_GLOBAL_SCENERY_NOT_FOUND", context={"path": "/nowhere"})

    mgr = JobManager(jobs_dir=home / "jobs", build=boom, env_factory=None)
    job = mgr.start([make_spec(home=home)])
    assert job.wait(10.0)
    st = job.state()
    assert st["status"] == "failed"
    assert st["errors"][-1]["code"] == "XP_GLOBAL_SCENERY_NOT_FOUND"


def test_manager_reloads_past_jobs(home: Path) -> None:
    build = FakeBuild()
    mgr = _manager(home, build)
    job = mgr.start([make_spec(home=home)])
    assert job.wait(10.0)
    mgr2 = _manager(home, FakeBuild())
    past = mgr2.get(job.id)
    assert past is not None and past.status == "done" and past.finished
    assert [j.id for j in mgr2.list()] == [job.id]
    assert past.events()[-1]["event"] == "finished"
    assert past.events(after=past.last_seq - 1)[0]["seq"] == past.last_seq
    assert past.state()["tiles"][0]["stages"]["data"]["status"] == "done"


def test_manager_clears_finished_jobs_and_their_files(home: Path) -> None:
    """The Works list can be emptied: the finished jobs go with their state and journal files, a
    running one stays, and a restart does not bring the cleared ones back."""
    build = FakeBuild()
    mgr = _manager(home, build)
    first = mgr.start([make_spec(home=home)])
    assert first.wait(10.0)
    files = [first.state_path(), first.journal_path]
    assert all(p.is_file() for p in files)
    build.delay_s = 0.2
    running = mgr.start([make_spec("+44+005", home=home)])
    assert mgr.forget_finished() == [first.id]
    assert not any(p.exists() for p in files) and mgr.get(first.id) is None
    assert [j.id for j in mgr.list()] == [running.id] and mgr.active() is running
    assert mgr.cancel(running.id) and running.wait(10.0)
    assert mgr.forget_finished() == [running.id]
    assert mgr.list() == [] and mgr.forget_finished() == []
    assert list((home / "jobs").iterdir()) == []
    assert _manager(home, FakeBuild()).list() == []


def test_manager_forgets_one_finished_job_only(home: Path) -> None:
    """A single finished build can leave the list, with its state and journal files. The one
    running and the one waiting behind it are refused, so nothing is taken from under a thread,
    and the list keeps everything else.

    What "finished" adds to those two is the rule's name, not a third case: a job the manager
    holds is always the active one, a queued one, or one that has finished, since a build killed
    mid-run writes no state file and is not read back. It stays as the meaning a reader and the
    409 rely on, and as the guard if that order ever changes.
    """
    build = FakeBuild()
    mgr = _manager(home, build)
    first = mgr.start([make_spec(home=home)])
    assert first.wait(10.0)
    second = mgr.start([make_spec("+45+006", home=home)])
    assert second.wait(10.0)
    build.delay_s = 0.5
    running = mgr.start([make_spec("+44+005", home=home)])
    queued = mgr.start([make_spec("+43+004", home=home)], queue=True)

    files = [first.state_path(), first.journal_path]
    assert all(f.is_file() for f in files)
    assert mgr.forget(first.id) is True
    assert not any(f.exists() for f in files) and mgr.get(first.id) is None
    assert {j.id for j in mgr.list()} == {second.id, running.id, queued.id}

    assert mgr.forget(running.id) is False, "the build under way stays"
    assert mgr.forget(queued.id) is False, "a build waiting its turn stays"
    assert mgr.forget("no-such-job") is False
    assert mgr.forget(first.id) is False, "already gone"
    assert {j.id for j in mgr.list()} == {second.id, running.id, queued.id}

    assert _manager(home, FakeBuild()).get(first.id) is None, "a restart does not bring it back"

    # the live builds end before the manager's own state is played with, and nothing outlives
    # the test: a build left running logged into the closed output of the next one
    assert mgr.cancel(queued.id) and mgr.cancel(running.id)
    assert running.wait(10.0) and queued.wait(10.0) and mgr.active() is None

    # The two guards beyond "finished" are for the moment the worker has marked a job finished but
    # the manager has not let go of it yet: `_finish` runs, then `save_state` writes, and only then
    # is the lock taken to clear the active slot and start the next one. Forgetting the job in that
    # window would delete the file being written and pull it from under that bookkeeping.
    mgr._active = second
    assert mgr.forget(second.id) is False, "finished, but still the active one"
    mgr._active = None
    mgr._queue.append(second)
    assert mgr.forget(second.id) is False, "finished, but still in the queue"
    mgr._queue.remove(second)
    assert mgr.forget(second.id) is True, "let go of, and now it can leave"
    mgr.close()


# -- over HTTP ----------------------------------------------------------------------------------


def _app(home: Path, build: FakeBuild):  # type: ignore[no-untyped-def]
    mgr = _manager(home, build)
    return create_app(env_factory=None, jobs=mgr, settings_path=home / "config.toml"), mgr


@pytest.mark.anyio
async def test_http_job_lifecycle_and_sse(home: Path, xplane: Path) -> None:
    app, mgr = _app(home, FakeBuild())
    body = {"tiles": ["+43+005"], "provider": "BI", "zoom_level": 14, "xplane_dir": str(xplane)}
    async with client_for(app) as c:
        r = await c.post("/api/jobs", json=body)
        assert r.status_code == 201, r.text
        job_id = r.json()["job_id"]
        # the whole stream, until 'finished'
        r = await c.get(f"/api/jobs/{job_id}/events")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        msgs = list(sse_messages(r.text))
        assert msgs[-1]["event"] == "finished"
        ids = [int(m["id"]) for m in msgs]
        assert ids == list(range(1, len(ids) + 1))
        data = json.loads(msgs[-1]["data"])
        assert data["status"] == "done" and data["report"]["ok"]
        # replay from the middle with Last-Event-ID
        mid = ids[len(ids) // 2]
        r = await c.get(f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": str(mid)})
        replay = list(sse_messages(r.text))
        assert int(replay[0]["id"]) == mid + 1 and replay[-1]["event"] == "finished"
        r = await c.get(f"/api/jobs/{job_id}/events", params={"after": ids[-1]})
        assert list(sse_messages(r.text)) == []
        # state and list
        r = await c.get(f"/api/jobs/{job_id}")
        st = r.json()
        assert st["status"] == "done" and st["tiles"][0]["stages"]["imagery"]["status"] == "done"
        assert st["request"]["tiles"] == ["+43+005"] and st["install"] is False
        r = await c.get("/api/jobs")
        assert [j["id"] for j in r.json()] == [job_id]
        r = await c.get("/api/jobs/nope")
        assert r.status_code == 404
        r = await c.post(f"/api/jobs/{job_id}/cancel")
        assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_BUSY"
        r = await c.get("/api/status")
        assert r.json()["active_job"] is None
    mgr.close()


@pytest.mark.anyio
async def test_http_409_while_running_cancel_and_retry(home: Path, xplane: Path) -> None:
    build = FakeBuild(fail={"textures": "TEX_MISSING"}, delay_s=0.03)
    app, mgr = _app(home, build)
    body = {"tiles": ["+43+005", "+44+005"], "zoom_level": 14, "xplane_dir": str(xplane)}
    async with client_for(app) as c:
        r = await c.post("/api/jobs", json=body)
        assert r.status_code == 201
        job_id = r.json()["job_id"]
        r = await c.post("/api/jobs", json=body)
        assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_BUSY"
        r = await c.post(f"/api/jobs/{job_id}/retry")
        assert r.status_code == 409
        r = await c.get("/api/status")
        assert r.json()["active_job"] == job_id
        # Wait for the first tile's data stage instead of sleeping: 0.15 s was not always enough
        # on the Windows runner, and the retry then found nothing to hit (CI, 2026-09-18).
        for _ in range(400):
            r = await c.get(f"/api/jobs/{job_id}")
            if r.json()["tiles"][0]["stages"]["data"]["status"] in ("done", "hit"):
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("the data stage of the first tile never committed")
        r = await c.post(f"/api/jobs/{job_id}/cancel")
        assert r.status_code == 200 and r.json()["cancel_requested"]
        assert mgr.get(job_id).wait(10.0)  # type: ignore[union-attr]
        r = await c.get(f"/api/jobs/{job_id}")
        assert r.json()["status"] == "cancelled"
        # retry after the failure is fixed: a new job, the first tile's nodes are hits
        build.fail.clear()
        build.delay_s = 0.0
        r = await c.post(f"/api/jobs/{job_id}/retry")
        assert r.status_code == 201 and r.json()["retry_of"] == job_id
        new_id = r.json()["job_id"]
        assert mgr.get(new_id).wait(10.0)  # type: ignore[union-attr]
        r = await c.get(f"/api/jobs/{new_id}")
        st = r.json()
        assert st["status"] == "done" and all(t["status"] == "done" for t in st["tiles"])
        assert st["tiles"][0]["stages"]["data"]["status"] == "hit"
    mgr.close()


@pytest.mark.anyio
async def test_http_builds_wait_in_a_queue(home: Path, xplane: Path) -> None:
    """The page queues a build while another runs (``queue``): 201 and its place in the queue, and
    it starts once the build before it ends; a retry queues the same way. Without ``queue`` the
    answer stays 409 ``SYS_BUSY``. A tile already in a build is 409 ``SYS_TILE_IN_BUILD``, whose
    context names the tiles and their builds."""
    build = FakeBuild(delay_s=0.02)
    app, mgr = _app(home, build)
    body = {"zoom_level": 14, "xplane_dir": str(xplane)}
    try:
        async with client_for(app) as c:
            r = await c.post("/api/jobs", json={**body, "tiles": ["+43+005"], "queue": True})
            assert r.status_code == 201 and r.json()["queue_position"] == 0  # nothing ran: it runs
            first = r.json()["job_id"]
            r = await c.post("/api/jobs", json={**body, "tiles": ["+44+005"]})
            assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_BUSY"
            r = await c.post("/api/jobs", json={**body, "tiles": ["+44+005"], "queue": True})
            assert r.status_code == 201, r.text
            assert r.json()["status"] == "queued" and r.json()["queue_position"] == 1
            second = r.json()["job_id"]
            tiles = ["+45+005", "+43+005", "+44+005"]
            r = await c.post("/api/jobs", json={**body, "tiles": tiles, "queue": True})
            assert r.status_code == 409, r.text
            err = r.json()["error"]
            assert err["code"] == "SYS_TILE_IN_BUILD" and err["remedy"]
            assert err["context"] == {
                "tiles": ["+43+005", "+44+005"],
                "jobs": sorted([first, second]),
            }
            assert "+43+005 +44+005 are in a build already" in err["message"]
            listed = {j["id"]: j["status"] for j in (await c.get("/api/jobs")).json()}
            assert listed[second] == "queued" and set(listed) == {first, second}
            assert "queue" not in (await c.get(f"/api/jobs/{second}")).json()["request"]
            assert mgr.get(first).wait(10.0) and mgr.get(second).wait(10.0)  # type: ignore[union-attr]
            jobs = {j["id"]: j for j in (await c.get("/api/jobs")).json()}
            assert jobs[first]["status"] == jobs[second]["status"] == "done"
            assert jobs[second]["started_at"] >= jobs[first]["finished_at"]

            build.delay_s = 0.05
            third = (await c.post("/api/jobs", json={**body, "tiles": ["+46+006"]})).json()[
                "job_id"
            ]
            r = await c.post(f"/api/jobs/{first}/retry", json={"queue": True})
            assert r.status_code == 201, r.text
            assert r.json()["retry_of"] == first and r.json()["queue_position"] == 1
            retried = r.json()["job_id"]
            r = await c.post(f"/api/jobs/{third}/retry", json={"queue": True})
            assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_TILE_IN_BUILD"
            r = await c.post(f"/api/jobs/{second}/retry")
            assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_BUSY"
            r = await c.post(f"/api/jobs/{second}/retry", json={"bogus": 1})
            assert r.status_code == 422
            assert mgr.get(third).wait(30) and mgr.get(retried).wait(30)  # type: ignore[union-attr]
            assert mgr.get(retried).status == "done"  # type: ignore[union-attr]
    finally:
        mgr.close()


@pytest.mark.anyio
async def test_http_failed_job_lists_error_with_action(home: Path, xplane: Path) -> None:
    app, mgr = _app(home, FakeBuild(fail={"mesh": "MESH_TRIANGULATION_FAILED"}))
    async with client_for(app) as c:
        r = await c.post("/api/jobs", json={"tiles": ["+43+005"], "zoom_level": 14})
        assert r.status_code == 201, r.text
        job_id = r.json()["job_id"]
        assert mgr.get(job_id).wait(10.0)  # type: ignore[union-attr]
        st = (await c.get(f"/api/jobs/{job_id}")).json()
        assert st["status"] == "failed"
        (err,) = st["errors"]
        assert err["code"] == "MESH_TRIANGULATION_FAILED" and err["action"] == "none"
        assert err["stage"] == "terrain" and err["severity"] == "blocking"
        stages = st["tiles"][0]["stages"]
        assert stages["data"]["status"] == "done" and stages["terrain"]["status"] == "failed"
        assert stages["coast"]["status"] == "skipped" and stages["imagery"]["status"] == "skipped"
        r = await c.get(f"/api/jobs/{job_id}/events")
        failed = [json.loads(m["data"]) for m in sse_messages(r.text) if m["event"] == "failed"]
        assert (
            failed[0]["error"]["code"] == "MESH_TRIANGULATION_FAILED" and not failed[0]["skipped"]
        )
        assert failed[1]["skipped"] and failed[1]["cause"] == "+43+005/mesh"
    mgr.close()


@pytest.mark.anyio
async def test_http_clear_empties_the_job_list(home: Path, xplane: Path) -> None:
    app, mgr = _app(home, FakeBuild())
    body = {"tiles": ["+43+005"], "zoom_level": 14, "xplane_dir": str(xplane)}
    async with client_for(app) as c:
        r = await c.post("/api/jobs", json=body)
        job_id = r.json()["job_id"]
        assert mgr.get(job_id).wait(10.0)  # type: ignore[union-attr]
        r = await c.post("/api/jobs/clear")
        assert r.status_code == 200 and r.json() == {"removed": [job_id]}
        assert (await c.get("/api/jobs")).json() == []
        assert (await c.get(f"/api/jobs/{job_id}")).status_code == 404
        r = await c.post("/api/jobs/clear")
        assert r.status_code == 200 and r.json() == {"removed": []}
    mgr.close()


@pytest.mark.anyio
async def test_http_removes_one_finished_job_and_refuses_a_live_one(
    home: Path, xplane: Path
) -> None:
    """``DELETE /api/jobs/{id}``: the finished build leaves the list alone, an unknown id is 404,
    and a build still under way is 409 with what to do about it."""
    build = FakeBuild()
    app, mgr = _app(home, build)
    body = {"tiles": ["+43+005"], "zoom_level": 14, "xplane_dir": str(xplane)}
    async with client_for(app) as c:
        done_id = (await c.post("/api/jobs", json=body)).json()["job_id"]
        assert mgr.get(done_id).wait(10.0)  # type: ignore[union-attr]
        build.delay_s = 0.5
        live_id = (await c.post("/api/jobs", json={**body, "tiles": ["+44+005"]})).json()["job_id"]

        r = await c.delete(f"/api/jobs/{live_id}")
        assert r.status_code == 409
        err = r.json()["error"]
        assert err["code"] == "SYS_BUSY" and "cancel it first" in err["remedy"]

        assert (await c.delete("/api/jobs/no-such-job")).status_code == 404

        r = await c.delete(f"/api/jobs/{done_id}")
        assert r.status_code == 200 and r.json() == {"job_id": done_id, "removed": True}
        assert [j["id"] for j in (await c.get("/api/jobs")).json()] == [live_id]
        assert (await c.get(f"/api/jobs/{done_id}")).status_code == 404
        assert (await c.delete(f"/api/jobs/{done_id}")).status_code == 404, "gone is gone"
    mgr.close()
