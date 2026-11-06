# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The engine's cloud tasks (``clouds.md`` R7): one search at a time, a task stopped while it
waits for its turn ends at once, and one that fails says how until the tile's next task."""

from __future__ import annotations

import threading
import time

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


def test_a_stopped_task_is_no_failure() -> None:
    tasks = Tasks()

    def stopped(t: Task) -> None:
        raise OsxpError("SYS_CANCELLED")

    tasks.start("+43+005", "check", stopped, cloud="a")
    assert _ended(tasks, "+43+005") and tasks.failed("+43+005") is None


def test_quit_waits_for_an_apply_and_stops_the_others() -> None:
    tasks = Tasks()
    go = threading.Event()
    ended: list[str] = []
    tasks.start(
        "+43+005", "apply", lambda t: (time.sleep(0.3), ended.append("apply")), stoppable=False
    )
    tasks.start("+43+005", "find", lambda t: go.wait(10))
    tasks.stop_all()
    assert tasks.settle(5.0) and ended == ["apply"]  # Apply ended what it began
    go.set()


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
    tasks.start("+50+005", "apply", lambda t: ran.append("apply"), stoppable=False)
    assert _ended(tasks, "+50+005", within=2.0) and ran == ["apply"]
    go.set()


def test_a_tile_being_deleted_waits_for_its_own_apply_only() -> None:
    tasks, go = Tasks(), threading.Event()
    tasks.start("+43+005", "apply", lambda t: time.sleep(0.2), stoppable=False)
    tasks.start("+44+005", "apply", lambda t: go.wait(10), stoppable=False)
    assert tasks.settle(5.0, "+43+005") and not tasks.of("+43+005")
    assert tasks.of("+44+005")
    go.set()
    assert tasks.settle(5.0)
