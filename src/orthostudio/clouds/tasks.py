# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The engine's cloud tasks: a search, getting a cloud's images, trying an outline's sources,
Apply and Restore (``clouds.md`` R7). Each runs in a thread of its own and ends whether or not a
page is open; it writes its result at its end, so a page that went away loses nothing. One search
at a time for the whole app (a search holds gigabytes, B2), a few reads of a cloud's sources at
once, Apply and Restore at once (never behind another tile's reads); one of each kind for a
cloud, or for a tile. Each says its phase and how far it is; Stop ends it at its next step, the
list left as it was, except Apply and Restore, which write a tile's textures and end what they
began. A tile's folder is never the tasks' and another's at once: none starts while something
else holds it (a build, a filing), and what takes it ends those under way first (:meth:`end`).
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from orthostudio.clouds.state import WRITES, possible
from orthostudio.errors import OsxpError
from orthostudio.home import data_root_missing

__all__ = ["Task", "Tasks"]

log = logging.getLogger(__name__)

CHECKS_AT_ONCE = 3


@dataclass(eq=False)
class Task:
    """One task: of ``tile``, ``kind`` (the action that starts it: ``find``, ``check``,
    ``outline``, ``apply``, ``restore``, ``forget``), for ``cloud`` when it is one cloud's; where
    it is (``phase``, ``done`` of ``total``); whether Stop ends it (``stoppable``); what After
    showed of its cloud when it began (``was``: its row and the images it laid, shown and counted
    meanwhile, ``state.view``)."""

    id: str
    tile: str
    kind: str
    cloud: str | None = None
    stoppable: bool = True
    was: Mapping[str, Any] | None = None
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
            "was": self.was,
        }


class Tasks:
    """The tasks running and waiting. ``taken``: the tiles whose folder something else holds,
    each with the state that says it (``state.TILE_STATES``: built, queued, filed, deleted)."""

    def __init__(self, taken: Callable[[], Mapping[str, str]] = dict) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._failed: dict[str, Task] = {}  # each tile's last failure, until its next task
        self._search = threading.Semaphore(1)
        self._checks = threading.Semaphore(CHECKS_AT_ONCE)
        self._taken = taken
        self._following: dict[str, int] = {}
        """Per tile, the tasks ended whose follow-up runs (``then``): under way still
        (:meth:`busy`)."""

    def start(
        self,
        tile: str,
        kind: str,
        run: Callable[[Task], None],
        cloud: str | None = None,
        *,
        then: Callable[[], None] | None = None,
        was: Mapping[str, Any] | None = None,
    ) -> Task:
        """Run ``run(task)`` in a thread once its turn comes (one search at a time for the app,
        :data:`CHECKS_AT_ONCE` reads of sources, Apply and Restore at once); the task is
        forgotten at its end, then ``then()`` runs when it ended neither stopped nor failed (what
        follows it, seeing it ended). Refused while something else holds the tile's folder
        (``CLOUD_TILE_NOT_READY``), while what runs of the tile does not allow it
        (``state.possible``: no Apply beside a task, no task beside Apply), or a task of the same
        tile, kind and cloud is under way (``CLOUD_NOT_NOW``: a click the page drew before); the
        tile's last failure forgotten. Decided under the lock :meth:`end` lists the tasks under: a
        task is refused, or ended by what takes the folder."""
        with self._lock:
            taken = self._taken().get(tile)
            if taken is not None:
                raise OsxpError("CLOUD_TILE_NOT_READY", context={"tile": tile, "state": taken})
            mine = self._of(tile)
            under_way = {t.kind for t in mine}
            same = any(t.kind == kind and t.cloud == cloud for t in mine)
            if same or kind not in possible("ready", under_way):
                raise OsxpError("CLOUD_NOT_NOW", context={"tile": tile, "action": kind})
            task = Task(secrets.token_hex(6), tile, kind, cloud, kind not in WRITES, was)
            self._tasks[task.id] = task
            self._failed.pop(tile, None)
        gate = self._search if kind == "find" else None if kind in WRITES else self._checks

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
                    task.error = _said(exc)
            except Exception as exc:
                log.exception("%s of %s failed", task.kind, tile)
                task.error = _said(
                    OsxpError(
                        "SYS_INTERNAL_ERROR",
                        context={"type": type(exc).__name__, "detail": str(exc)},
                    )
                )
            finally:
                follow = then if task.error is None and not task.stop.is_set() else None
                with self._lock:
                    self._tasks.pop(task.id, None)
                    if task.error is not None:
                        self._failed[tile] = task
                    if follow is not None:  # under way for whoever asks until it has run
                        self._following[tile] = self._following.get(tile, 0) + 1
            if follow is not None:
                try:
                    _follow(follow, task)
                finally:
                    with self._lock:
                        self._following[tile] -= 1

        threading.Thread(target=body, name=f"osxp-clouds-{kind}-{tile}", daemon=True).start()
        return task

    def _of(self, tile: str | None) -> list[Task]:
        """:meth:`of`, the lock held."""
        return [t for t in self._tasks.values() if tile is None or t.tile == tile]

    def of(self, tile: str | None = None) -> list[Task]:
        with self._lock:
            return self._of(tile)

    def busy(self, tile: str | None = None) -> bool:
        """Whether anything of ``tile`` (of any tile: ``None``) is under way: a task, or what
        follows one (an outline's tick and images, between its end and their own task). The one
        rule, for the cloud mode's view, :meth:`end` and the engine kept up for its tasks."""
        tiles = None if tile is None else {tile}
        with self._lock:
            return bool(self._mine(tiles)) or self._after(tiles)

    def _mine(self, tiles: Collection[str] | None) -> list[Task]:
        """The tasks of ``tiles`` (of every tile: ``None``), the lock held."""
        return [t for t in self._tasks.values() if tiles is None or t.tile in tiles]

    def _after(self, tiles: Collection[str] | None) -> bool:
        """Whether what follows a task of ``tiles`` runs, the lock held."""
        return any(n for t, n in self._following.items() if tiles is None or t in tiles)

    def failed(self, tile: str) -> Task | None:
        """The tile's last task that failed, until its next one starts."""
        with self._lock:
            return self._failed.get(tile)

    def stop(self, task_id: str) -> bool:
        """Stop the task ``task_id`` at its next step, when Stop ends it; whether it does."""
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None or not task.stoppable:
            return False
        task.stop.set()
        return True

    def end(self, tiles: Collection[str] | None = None) -> None:
        """Every task of ``tiles`` (of every tile: ``None``) ended before the caller takes their
        folders: those Stop ends stopped, Apply and Restore waited for, which end what they began
        (as long as they take, a few seconds a texture). Taking the folder is said first
        (``taken``): a task asked meanwhile is refused (:meth:`start`)."""
        while True:
            with self._lock:
                mine, after = self._mine(tiles), self._after(tiles)
            if not mine and not after:
                return
            for t in mine:
                t.stop.set()
            time.sleep(0.05)


def _said(exc: OsxpError) -> dict[str, Any]:
    """What a task that failed says: the data folder's disk gone meanwhile, when it is, alone, as
    a build says it (``jobs._lost_data_folder``): a search cut by it said an internal error to
    report, in English (a campaign, 2026-11-07)."""
    lost = data_root_missing()
    if lost is not None:
        return OsxpError("CFG_DATA_DIR_MISSING", context={"path": str(lost)}).to_dict()
    return exc.to_dict()


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
