# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The engine's cloud tasks: a search, getting a cloud's images, trying an outline's sources,
Apply and Restore (``clouds.md`` R7). Each runs in a thread of its own and ends whether or not a
page is open; it writes its result at its end, so a page that went away loses nothing. One search
at a time for the whole app (a search holds gigabytes, B2), a few reads of a cloud's sources at
once, Apply and Restore at once (never behind another tile's reads). Each says its phase and how
far it is; Stop ends it at its next step, the list left as it was, except Apply and Restore, which
write a tile's textures and end what they began.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from orthostudio.errors import OsxpError

__all__ = ["Task", "Tasks"]

log = logging.getLogger(__name__)

CHECKS_AT_ONCE = 3


@dataclass(eq=False)
class Task:
    """One task: of ``tile``, ``kind`` (the action that starts it: ``find``, ``check``,
    ``outline``, ``apply``, ``restore``), for ``cloud`` when it is one cloud's; where it is
    (``phase``, ``done`` of ``total``); whether Stop ends it (``stoppable``)."""

    id: str
    tile: str
    kind: str
    cloud: str | None = None
    stoppable: bool = True
    phase: str = "waiting"
    done: int = 0
    total: int = 0
    stop: threading.Event = field(default_factory=threading.Event)
    error: dict[str, Any] | None = None
    """How it failed (``OsxpError.to_dict``), once it has."""

    def say(self, phase: str, done: int = 0, total: int = 0) -> None:
        if self.stoppable and self.stop.is_set():
            raise OsxpError("SYS_CANCELLED")
        self.phase, self.done, self.total = phase, done, total

    def view(self) -> dict[str, Any]:
        progress = self.done / self.total if self.total else None
        return {
            "id": self.id,
            "tile": self.tile,
            "kind": self.kind,
            "cloud": self.cloud,
            "phase": self.phase,
            "progress": progress,
            "stoppable": self.stoppable,
            "error": self.error,
        }


class Tasks:
    """The tasks running and waiting."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._failed: dict[str, Task] = {}  # each tile's last failure, until its next task
        self._search = threading.Semaphore(1)
        self._checks = threading.Semaphore(CHECKS_AT_ONCE)

    def start(
        self,
        tile: str,
        kind: str,
        run: Callable[[Task], None],
        cloud: str | None = None,
        *,
        stoppable: bool = True,
        then: Callable[[], None] | None = None,
    ) -> Task:
        """Run ``run(task)`` in a thread once its turn comes (one search at a time for the app,
        :data:`CHECKS_AT_ONCE` reads of sources, an unstoppable task at once); the task is
        forgotten at its end, then ``then()`` runs when it ended neither stopped nor failed (what
        follows it, seeing it ended). A task of the same tile, kind and cloud already under way
        is stopped, and the tile's last failure forgotten."""
        with self._lock:
            for t in self._tasks.values():  # one of each for a cloud, one search for a tile
                if t.tile == tile and t.kind == kind and t.cloud == cloud:
                    t.stop.set()
            task = Task(secrets.token_hex(6), tile, kind, cloud, stoppable)
            self._tasks[task.id] = task
            self._failed.pop(tile, None)
        gate = self._search if kind == "find" else self._checks if stoppable else None

        def body() -> None:
            try:
                if gate is None:
                    run(task)
                elif _turn(gate, task):
                    try:
                        if not task.stop.is_set():
                            run(task)
                    finally:
                        gate.release()
            except OsxpError as exc:
                if exc.code != "SYS_CANCELLED":
                    log.warning("%s %s of %s: %s", task.kind, task.cloud or "", tile, exc.message)
                    task.error = exc.to_dict()
            except Exception as exc:
                log.exception("%s of %s failed", task.kind, tile)
                task.error = OsxpError(
                    "SYS_INTERNAL_ERROR", context={"type": type(exc).__name__, "detail": str(exc)}
                ).to_dict()
            finally:
                with self._lock:
                    self._tasks.pop(task.id, None)
                    if task.error is not None:
                        self._failed[tile] = task
            if then is not None and task.error is None and not task.stop.is_set():
                _follow(then, task)

        threading.Thread(target=body, name=f"osxp-clouds-{kind}-{tile}", daemon=True).start()
        return task

    def of(self, tile: str | None = None) -> list[Task]:
        with self._lock:
            return [t for t in self._tasks.values() if tile is None or t.tile == tile]

    def failed(self, tile: str) -> Task | None:
        """The tile's last task that failed, until its next one starts."""
        with self._lock:
            return self._failed.get(tile)

    def stop(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is not None:
            task.stop.set()
        return task is not None

    def stop_all(self) -> int:
        with self._lock:
            tasks = list(self._tasks.values())
        for t in tasks:
            t.stop.set()
        return len(tasks)

    def settle(self, timeout: float, tile: str | None = None) -> bool:
        """Wait until no task Stop does not end runs (an Apply, a few seconds), of ``tile`` or of
        any, ``timeout`` at most; whether none does."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                busy = any(not t.stoppable and tile in (None, t.tile) for t in self._tasks.values())
            if not busy or time.monotonic() >= deadline:
                return not busy
            time.sleep(0.05)


def _turn(gate: threading.Semaphore, task: Task) -> bool:
    """Whether ``task``'s turn came before its stop."""
    while not gate.acquire(timeout=0.25):
        if task.stop.is_set():
            return False
    return True


def _follow(then: Callable[[], None], task: Task) -> None:
    """What follows ``task``, run once it ended: a list it finds forgotten ends it quietly."""
    try:
        then()
    except OsxpError as exc:
        if exc.code != "SYS_CANCELLED":
            log.warning("after %s of %s: %s", task.kind, task.tile, exc.message)
    except Exception:
        log.exception("after %s of %s failed", task.kind, task.tile)
