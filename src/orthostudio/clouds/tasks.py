# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The engine's cloud tasks: a search, getting a cloud's images, trying an outline's sources
(``clouds.md`` R7). Each runs in a thread of its own and ends whether or not a page is open; it
writes its result into the tile's list at its end, so a page that went away loses nothing. One
search at a time for the whole app (a search holds gigabytes, B4), a few image gettings at once.
Each says its phase and how far it is; Stop ends it at its next step, the list left as it was.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from orthostudio.errors import OsxpError

__all__ = ["Task", "Tasks"]

log = logging.getLogger(__name__)

CHECKS_AT_ONCE = 3


@dataclass(eq=False)
class Task:
    """One task: of ``tile``, ``kind`` (``search``, ``getting``, ``trying``), for ``cloud`` when
    it is one cloud's; where it is (``phase``, ``done`` of ``total``)."""

    id: str
    tile: str
    kind: str
    cloud: str | None = None
    phase: str = "waiting"
    done: int = 0
    total: int = 0
    stop: threading.Event = field(default_factory=threading.Event)

    def say(self, phase: str, done: int = 0, total: int = 0) -> None:
        if self.stop.is_set():
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
        }


class Tasks:
    """The tasks running and waiting."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._search = threading.Semaphore(1)
        self._checks = threading.Semaphore(CHECKS_AT_ONCE)

    def start(
        self, tile: str, kind: str, run: Callable[[Task], None], cloud: str | None = None
    ) -> Task:
        """Run ``run(task)`` in a thread once its turn comes; the task is forgotten at its end."""
        with self._lock:
            for t in self._tasks.values():  # one of each for a cloud, one search for a tile
                if t.tile == tile and t.kind == kind and t.cloud == cloud:
                    t.stop.set()
            task = Task(secrets.token_hex(6), tile, kind, cloud)
            self._tasks[task.id] = task
        gate = self._search if kind == "search" else self._checks

        def body() -> None:
            try:
                with gate:
                    if not task.stop.is_set():
                        run(task)
            except OsxpError as exc:
                if exc.code != "SYS_CANCELLED":
                    log.warning("%s %s of %s: %s", task.kind, task.cloud or "", tile, exc.message)
            except Exception:
                log.exception("%s of %s failed", task.kind, tile)
            finally:
                with self._lock:
                    self._tasks.pop(task.id, None)

        threading.Thread(target=body, name=f"osxp-clouds-{kind}-{tile}", daemon=True).start()
        return task

    def of(self, tile: str | None = None) -> list[Task]:
        with self._lock:
            return [t for t in self._tasks.values() if tile is None or t.tile == tile]

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
