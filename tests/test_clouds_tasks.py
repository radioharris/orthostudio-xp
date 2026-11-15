# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The engine's cloud tasks (``clouds.md`` R7): as many searches at once as the memory holds,
each fetching from a source after another's; a task stopped while it waits for its turn ends at
once, and one that fails says how until the tile's next task; one of each kind for a cloud or a
tile; none while something else holds the tile's folder, and what
takes it waits for those under way to end, an Apply ending what it began."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from orthostudio.clouds.tasks import Task, Tasks
from orthostudio.errors import OsxpError


def _ended(tasks: Tasks, tile: str, within: float = 5.0) -> bool:
    end = time.monotonic() + within
    while time.monotonic() < end:
        if not tasks.of(tile):
            return True
        time.sleep(0.02)
    return False


def test_a_task_stopped_while_it_waits_for_its_turn_ends_at_once() -> None:
    tasks, go, ran = Tasks(), threading.Event(), []
    tasks.start("+43+005", "find", lambda t: go.wait(10))
    waiting = tasks.start("+44+005", "find", lambda t: ran.append(t.id))
    time.sleep(0.1)
    assert waiting.phase == "waiting"
    tasks.stop(waiting.id)
    assert _ended(tasks, "+44+005", within=2.0) and ran == []
    go.set()
    assert _ended(tasks, "+43+005")


def test_a_task_that_fails_says_how_until_the_tiles_next_one() -> None:
    tasks = Tasks()

    def fails(t: Task) -> None:
        raise OsxpError("CLOUD_DETECTOR_MISSING", context={"reason": "no internet"})

    tasks.start("+43+005", "find", fails)
    assert _ended(tasks, "+43+005")
    failed = tasks.failed("+43+005")
    assert failed is not None and failed.view()["error"]["code"] == "CLOUD_DETECTOR_MISSING"
    tasks.start("+43+005", "find", lambda t: None)
    assert tasks.failed("+43+005") is None


def test_a_task_cut_by_the_data_folders_disk_going_says_that_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # a search said an internal error to report while the disk was unplugged (a campaign,
    # 2026-11-07): the build says the data folder alone (jobs._lost_data_folder), and so here
    from orthostudio.clouds import tasks as tasks_module

    gone = tmp_path / "Logic Projects"
    monkeypatch.setattr(tasks_module, "data_root_missing", lambda: gone)
    tasks = Tasks()

    def cut(t: Task) -> None:
        raise PermissionError(13, "Permission denied", str(gone))

    tasks.start("+43+005", "find", cut)
    assert _ended(tasks, "+43+005")
    error = tasks.failed("+43+005").view()["error"]
    assert error["code"] == "CFG_DATA_DIR_MISSING" and error["context"]["path"] == str(gone)
    monkeypatch.setattr(tasks_module, "data_root_missing", lambda: None)
    # the disk back, no more said: it asked to plug in a disk that was there (a campaign,
    # 2026-11-08)
    assert tasks.failed("+43+005") is None
    tasks.start("+43+005", "find", cut)
    assert _ended(tasks, "+43+005")
    # the disk there, the folder refused: said as such (``errors.wrap``), as a picture's is, not
    # an internal error to report (a review, 2026-11-07)
    assert tasks.failed("+43+005").view()["error"]["code"] == "SYS_WRITE_FAILED"


def test_a_stopped_task_is_no_failure() -> None:
    tasks = Tasks()

    def stopped(t: Task) -> None:
        raise OsxpError("SYS_CANCELLED")

    tasks.start("+43+005", "check", stopped, cloud="a")
    assert _ended(tasks, "+43+005") and tasks.failed("+43+005") is None


def test_quit_waits_for_an_apply_and_ends_the_others() -> None:
    tasks = Tasks()
    ended: list[str] = []
    tasks.start("+43+005", "apply", lambda t: (time.sleep(0.3), ended.append("apply")))
    tasks.start("+44+005", "find", lambda t: [(t.say("x"), time.sleep(0.02)) for _ in range(500)])
    tasks.end()
    assert ended == ["apply"] and not tasks.of()  # Apply ended what it began, the search stopped


def test_a_tile_something_else_holds_starts_no_task_and_waits_for_those_under_way() -> None:
    taken: dict[str, str] = {}
    tasks, ended = Tasks(taken=lambda: taken), []
    tasks.start("+43+005", "apply", lambda t: (time.sleep(0.3), ended.append("apply")))
    taken["+43+005"] = "in_build"  # a build takes the tile's folder: said first
    with pytest.raises(OsxpError) as got:
        tasks.start("+43+005", "check", lambda t: None, cloud="a")
    assert got.value.code == "CLOUD_TILE_NOT_READY" and got.value.context["state"] == "in_build"
    tasks.end({"+43+005"})  # then it waits for what runs
    assert ended == ["apply"] and not tasks.of("+43+005")


def test_one_task_of_each_kind_for_a_cloud_and_stop_ends_only_what_it_may() -> None:
    tasks, go = Tasks(), threading.Event()
    first = tasks.start("+43+005", "check", lambda t: go.wait(10), cloud="a")
    with pytest.raises(OsxpError):  # a second is refused: the first is never stopped for it
        tasks.start("+43+005", "check", lambda t: None, cloud="a")
    tasks.start("+43+005", "check", lambda t: go.wait(10), cloud="b")  # another cloud's
    apply = tasks.start("+44+005", "apply", lambda t: go.wait(10))
    assert not tasks.stop(apply.id) and not apply.stop.is_set()  # Stop does not end an Apply
    assert tasks.stop(first.id)
    go.set()
    tasks.end()


def test_what_follows_a_task_runs_once_it_ended_and_never_after_a_stop() -> None:
    tasks, seen = Tasks(), []
    t = tasks.start("+43+005", "outline", lambda t: None, cloud="a",
                    then=lambda: seen.append([x.kind for x in tasks.of("+43+005")]))  # fmt: skip
    assert _ended(tasks, "+43+005")
    end = time.monotonic() + 2
    while not seen and time.monotonic() < end:
        time.sleep(0.02)
    assert seen == [[]] and t.error is None  # it saw the task ended
    go = threading.Event()
    stopped = tasks.start("+43+005", "outline", lambda t: (go.wait(10), t.say("x")),
                          cloud="b", then=lambda: seen.append(["after a stop"]))  # fmt: skip
    tasks.stop(stopped.id)
    go.set()
    assert _ended(tasks, "+43+005") and seen == [[]]


def test_an_apply_never_waits_behind_another_tiles_reads() -> None:
    tasks, go, ran = Tasks(), threading.Event(), []
    for n in range(4):
        tasks.start(f"+4{n}+005", "check", lambda t: go.wait(10), cloud="a")
    tasks.start("+50+005", "apply", lambda t: ran.append("apply"))
    assert _ended(tasks, "+50+005", within=2.0) and ran == ["apply"]
    go.set()


def test_a_tile_being_deleted_waits_for_its_own_tasks_only() -> None:
    tasks, go = Tasks(), threading.Event()
    tasks.start("+43+005", "apply", lambda t: time.sleep(0.2))
    tasks.start("+44+005", "apply", lambda t: go.wait(10))
    tasks.end({"+43+005"})
    assert not tasks.of("+43+005") and tasks.of("+44+005")
    go.set()
    tasks.end()


def test_no_apply_beside_a_task_nor_a_task_beside_apply_decided_where_they_start() -> None:
    tasks, go = Tasks(), threading.Event()
    tasks.start("+43+005", "check", lambda t: go.wait(10), cloud="a")
    with pytest.raises(OsxpError):
        tasks.start("+43+005", "apply", lambda t: None)
    go.set()
    tasks.end()
    go.clear()
    tasks.start("+43+005", "apply", lambda t: go.wait(10))
    with pytest.raises(OsxpError):
        tasks.start("+43+005", "find", lambda t: None)
    tasks.start("+44+005", "find", lambda t: None)  # another tile's: its own
    go.set()
    tasks.end()


def test_a_tile_stays_under_way_while_what_follows_its_task_runs() -> None:
    # an outline's sources tried, then its tick: a page reading the tile between saw nothing
    # under way, stopped reading it again, and missed the tick and its images
    tasks, go, seen = Tasks(), threading.Event(), []
    tasks.start("+43+005", "outline", lambda t: None, cloud="a",
                then=lambda: (seen.append(tasks.busy("+43+005")), go.wait(10)))  # fmt: skip
    end = time.monotonic() + 5
    while not seen and time.monotonic() < end:
        time.sleep(0.01)
    assert seen == [True] and not tasks.of("+43+005") and tasks.busy("+43+005")
    go.set()
    tasks.end({"+43+005"})  # what takes the tile waits for it too
    assert not tasks.busy("+43+005")


# -- several searches at once (a user's choice, 2026-11-15) ---------------------------------------


def test_searches_at_once_follow_the_computers_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    from orthostudio.clouds import tasks as tasks_module

    got = {}
    for gb in (None, 4, 8, 16, 24, 32, 48, 128):
        mb = None if gb is None else gb * 1024
        monkeypatch.setattr(tasks_module, "physical_memory_mb", lambda mb=mb: mb)
        got[gb] = tasks_module.searches_at_once()
    # four from 32 GB (each search holds up to 3.6 GB), one per 8 GB below, one when unknown
    assert got == {None: 1, 4: 1, 8: 1, 16: 2, 24: 3, 32: 4, 48: 4, 128: 4}


def test_searches_run_as_many_at_once_as_given_the_next_waiting() -> None:
    tasks, go = Tasks(searches=2), threading.Event()
    first = tasks.start("+43+005", "find", lambda t: (t.say("looking"), go.wait(10)))
    second = tasks.start("+44+005", "find", lambda t: (t.say("looking"), go.wait(10)))
    third = tasks.start("+45+005", "find", lambda t: t.say("looking"))
    end = time.monotonic() + 2
    while time.monotonic() < end and "waiting" in (first.phase, second.phase):
        time.sleep(0.02)
    assert (first.phase, second.phase, third.phase) == ("looking", "looking", "waiting")
    go.set()
    for tile in ("+43+005", "+44+005", "+45+005"):
        assert _ended(tasks, tile)


def test_searches_at_once_fetch_from_one_source_one_after_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # two searches asking EOX at once would ask it twice as fast as one: EOX refuses above about
    # 60 requests a second (2026-10-29); each search's fetch from a source waits for the other's
    import asyncio

    from orthostudio.clouds import sources
    from orthostudio.imagery.providers import load_registry

    spans: list[tuple[float, float]] = []

    class Slow:
        def __init__(self, **_k: object) -> None:
            pass

        async def __aenter__(self) -> Slow:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def fetch_many(self, requests: object, **_k: object) -> None:
            start = time.monotonic()
            await asyncio.sleep(0.3)
            spans.append((start, time.monotonic()))

    monkeypatch.setattr(sources, "Fetcher", Slow)
    eox = {"EOX": load_registry()["EOX"]}

    def both(alone: bool) -> list[tuple[float, float]]:
        spans.clear()
        runs = [
            threading.Thread(
                target=lambda k=k: sources.SourcePieces(
                    tmp_path / f"c{k}", eox, one_at_a_time=alone
                ).fetch({"EOX": [(k, k, 12)]})
            )
            for k in (1, 2)
        ]
        for r in runs:
            r.start()
        for r in runs:
            r.join(10)
        return sorted(spans)

    (_a0, a1), (b0, _b1) = both(True)
    assert b0 >= a1  # one after the other
    (_c0, c1), (d0, _d1) = both(False)
    assert d0 < c1  # a cloud's images (not a search) are not held back


def test_a_search_says_how_far_the_whole_of_it_is_never_going_back() -> None:
    # one share for the whole search (a user, 2026-11-15): each phase's count starts from 0 again
    from orthostudio.clouds.find import search_share

    tasks, seen = Tasks(), []

    def run(t: Task) -> None:
        for phase, done, total in (
            ("drawing", 0, 0),
            ("looking", 5, 10),
            ("looking", 10, 10),
            ("fetching", 0, 100),
            ("fetching", 100, 100),
            ("sources", 1, 2),
            ("fetching", 3, 10),  # the coarser levels' download, after the judging
            ("coarser", 50, 100),
        ):
            t.say(phase, done, total)
            seen.append(round(t.share or 0.0, 3))

    tasks.start("+43+005", "find", run, shares=search_share)
    assert _ended(tasks, "+43+005")
    assert seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 0.99
    assert seen[1] == round(search_share("looking", 5, 10), 3)
    assert search_share("laying", 1, 2) is None  # not a search's phase


def test_a_follow_keeps_its_tile_under_way_and_ends_with_its_tasks() -> None:
    # Find and apply is a follow of the tile's tasks (a review, 2026-11-15): one rule of "under
    # way" for the view, the Library, the engine kept up and what takes the tile (Quit, a build)
    tasks, taken = Tasks(taken=lambda: taken), {}
    ran, stopped = threading.Event(), []

    def work(stop: threading.Event) -> None:
        ran.set()
        stop.wait(10)
        stopped.append(stop.is_set())

    tasks.follow("+43+005", work)
    assert ran.wait(2) and tasks.busy("+43+005") and tasks.following() == ["+43+005"]
    assert tasks.settle("+43+005", threading.Event())  # its own tasks: none
    with pytest.raises(OsxpError) as twice:
        tasks.follow("+43+005", work)
    assert twice.value.code == "CLOUD_NOT_NOW"
    tasks.end({"+43+005"})  # what Quit, a build or a deletion does: stopped and waited for
    assert stopped == [True] and not tasks.busy("+43+005") and tasks.following() == []
    taken["+43+005"] = "in_build"
    with pytest.raises(OsxpError) as held:
        tasks.follow("+43+005", work)
    assert held.value.code == "CLOUD_TILE_NOT_READY"


def test_a_follow_stopped_waits_for_an_apply_without_spinning() -> None:
    # stopped, a follow waits for its tile's Apply, which ends what it began: it looked again at
    # once, a core at 100 % until the Apply ended (a review, 2026-11-15)
    tasks, stop, release = Tasks(), threading.Event(), threading.Event()
    tasks.start("+43+005", "apply", lambda _t: release.wait(5))
    stop.set()
    threading.Timer(1.0, release.set).start()
    used = time.process_time()
    assert not tasks.settle("+43+005", stop)
    assert not tasks.busy("+43+005") and time.process_time() - used < 0.3


def test_one_follow_at_a_time_gets_its_images_a_stopped_one_never() -> None:
    # four follows at their images together drew each tile's picture 102 times for 4 (a review,
    # 2026-11-15): one at a time; one stopped while it waits gives up at once
    tasks, first, second = Tasks(), threading.Event(), threading.Event()
    with tasks.images_turn(first) as mine:
        assert mine
        threading.Timer(0.3, second.set).start()
        with tasks.images_turn(second) as theirs:
            assert not theirs
    with tasks.images_turn(threading.Event()) as next_one:
        assert next_one


def test_a_stop_of_a_tasks_tile_stops_the_follow_that_takes_it() -> None:
    # Stop in the cloud mode stopped the search Find and apply began, then it applied the rest
    # (a review, 2026-11-15)
    tasks, started, ended = Tasks(), threading.Event(), threading.Event()

    def work(stop: threading.Event) -> None:
        task = tasks.start("+43+005", "find", lambda t: t.stop.wait(5))
        started.set()
        tasks.settle("+43+005", stop)
        if stop.is_set():
            ended.set()
        assert task.stop.is_set()

    tasks.follow("+43+005", work)
    assert started.wait(2)
    find = next(t for t in tasks.of("+43+005") if t.kind == "find")
    assert tasks.stop(find.id)
    assert ended.wait(2)


def test_a_follow_that_fails_says_how_until_the_tiles_next_task() -> None:
    tasks = Tasks()

    def fails(_stop: threading.Event) -> None:
        raise OsxpError("CLOUD_XPLANE_RUNNING", context={"tile": "+43+005"})

    tasks.follow("+43+005", fails)
    end = time.monotonic() + 2
    while tasks.busy("+43+005") and time.monotonic() < end:
        time.sleep(0.02)
    failed = tasks.failed("+43+005")
    assert failed is not None and failed.error["code"] == "CLOUD_XPLANE_RUNNING"


def test_a_searchs_turn_at_a_source_is_given_back_when_a_failure_beside_it_ends_its_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # another search holds B; this one's fetch from A fails, which cancels its wait for B: the turn
    # its waiting thread took later was never given back, every later search then waiting on B
    # until a restart (a review, 2026-11-15)
    from types import SimpleNamespace

    from orthostudio.clouds import sources

    class Failing:
        def __init__(self, **_k: object) -> None:
            pass

        async def __aenter__(self) -> Failing:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def fetch_many(self, requests: object, **_k: object) -> None:
            if next(iter(requests)).host_group == "A":  # type: ignore[call-overload]
                raise OSError("disk gone")

    monkeypatch.setattr(sources, "Fetcher", Failing)
    monkeypatch.setattr(sources, "tile_url", lambda p, *k: "http://x")
    pieces = sources.SourcePieces.__new__(sources.SourcePieces)
    pieces.one_at_a_time, pieces.failed = True, {}
    pieces.providers = {
        c: SimpleNamespace(
            code=c, max_in_flight=1, server_req_per_s=1, pushback_status=(), headers={}
        )
        for c in ("A", "B")
    }
    pieces.caches = {c: SimpleNamespace(lookup=lambda *k: None, put=lambda *a: None) for c in "AB"}
    with sources._TURNS_LOCK:
        held = sources._TURNS.setdefault("B", threading.Lock())
    held.acquire()
    threading.Timer(0.5, held.release).start()  # the other search ends
    with pytest.raises(OSError):
        pieces.fetch({"A": [(1, 2, 3)], "B": [(1, 2, 3)]}, cancel=threading.Event())
    time.sleep(1.0)
    assert not sources._TURNS["B"].locked()
