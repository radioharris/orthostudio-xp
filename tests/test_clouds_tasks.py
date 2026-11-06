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
    tasks.start("+43+005", "search", lambda t: go.wait(10))
    waiting = tasks.start("+44+005", "search", lambda t: ran.append(t.id))
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

    tasks.start("+43+005", "search", fails)
    assert _ended(tasks, "+43+005")
    failed = tasks.failed("+43+005")
    assert failed is not None and failed.view()["error"]["code"] == "CLOUD_DETECTOR_MISSING"
    tasks.start("+43+005", "search", lambda t: None)
    assert tasks.failed("+43+005") is None


def test_a_stopped_task_is_no_failure() -> None:
    tasks = Tasks()

    def stopped(t: Task) -> None:
        raise OsxpError("SYS_CANCELLED")

    tasks.start("+43+005", "getting", stopped, cloud="a")
    assert _ended(tasks, "+43+005") and tasks.failed("+43+005") is None
