# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Worker processes leave when the process that started them is gone.

The engine's pools (the scheduler's CPU workers, the textures' encoders, the masks' cells, the
road squares of a tracing) are
``ProcessPoolExecutor`` workers, which wait for their next task on a queue. When the engine itself
ends normally it shuts them down; when it is killed or crashes, nothing tells them, and they wait
forever: the engine killed during a build left 11 of them behind, idle, holding about 7.3 GB of
memory and files on the data disk until the computer restarted (2026-10-15, on a Mac; the same on
Windows with the app's own Python).

``multiprocessing`` gives each child a sentinel of its parent, ready once the parent has ended,
whatever the way: a pipe whose other end only the parent holds on macOS and Linux, the parent's
process handle on Windows. A thread of the worker waits on it and ends the process.
"""

from __future__ import annotations

import multiprocessing
import os
import threading
from multiprocessing.connection import wait

__all__ = ["leave_with_parent"]

PARENT_GONE_EXIT = 3
"""The worker's exit code when it leaves because its parent is gone."""


def leave_with_parent() -> None:
    """In a worker process (a pool's initializer calls it): end this process as soon as the one
    that started it is gone. Nothing in the main process, which has no parent of ours."""
    parent = multiprocessing.parent_process()
    if parent is None:
        return
    sentinel = parent.sentinel

    def watch() -> None:
        wait([sentinel])
        # its work is moot: the files it was writing are left as a crash leaves them, and the
        # next build checks them (graph/store.py recover)
        os._exit(PARENT_GONE_EXIT)

    threading.Thread(target=watch, name="osxp-parent-watch", daemon=True).start()
