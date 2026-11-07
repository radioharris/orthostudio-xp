# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The engine's cloud tasks (``clouds.md`` R7): one search at a time, a task stopped while it
waits for its turn ends at once, and one that fails says how until the tile's next task; one of
each kind for a cloud or a tile; none while something else holds the tile's folder, and what
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
    tasks.start("+43+005", "find", cut)
    assert _ended(tasks, "+43+005")
    assert tasks.failed("+43+005").view()["error"]["code"] == "SYS_INTERNAL_ERROR"


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
