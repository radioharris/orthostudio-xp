# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The engine's worker processes leave when the engine is gone.

Killed during a build, the engine left 11 workers behind, idle, holding about 7.3 GB of memory and
files on the data disk (2026-10-15). Here a real parent starts a pool the way the engine does
(``spawn``), is killed from outside, and its workers must be gone a few seconds later; the same
pool without the watch keeps them, which proves the test sees the difference.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from orthostudio.masks import build as masks_build
from orthostudio.parentwatch import leave_with_parent
from orthostudio.pipeline import textures
from orthostudio.sched import workers

PARENT = textwrap.dedent(
    """
    import multiprocessing as mp, os, sys, time
    from concurrent.futures import ProcessPoolExecutor
    from orthostudio.parentwatch import leave_with_parent

    def plain():
        pass

    def pid(_):
        time.sleep(0.05)
        return os.getpid()

    if __name__ == "__main__":
        init = leave_with_parent if sys.argv[1] == "watch" else plain
        pool = ProcessPoolExecutor(2, mp_context=mp.get_context("spawn"), initializer=init)
        list(pool.map(pid, range(8)))
        with open(sys.argv[2], "w") as f:  # every worker the pool started, busy or not
            f.write(" ".join(map(str, sorted(pool._processes))))
        time.sleep(120)
    """
)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _workers_left(tmp_path: Path, mode: str) -> list[int]:
    script = tmp_path / "parent.py"
    script.write_text(PARENT, encoding="utf-8")
    out = tmp_path / f"{mode}.pids"
    parent = subprocess.Popen([sys.executable, str(script), mode, str(out)], env=os.environ.copy())
    try:
        deadline = time.monotonic() + 60
        while not (out.is_file() and out.read_text().strip()):
            assert time.monotonic() < deadline, "the pool never answered"
            assert parent.poll() is None, "the parent ended on its own"
            time.sleep(0.1)
        pids = [int(p) for p in out.read_text().split()]
    finally:
        if sys.platform == "win32":
            parent.kill()
        else:
            os.kill(parent.pid, signal.SIGKILL)  # as a crash: nothing runs in the parent
        parent.wait()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.2)
    left = [p for p in pids if _alive(p)]
    for p in left:  # the control's survivors, or a failure's: never leave them behind
        with contextlib.suppress(OSError):
            os.kill(p, signal.SIGTERM)
    return left


def test_workers_leave_when_their_parent_is_killed(tmp_path: Path) -> None:
    assert _workers_left(tmp_path, "plain"), "without the watch the workers stay: the control"
    assert _workers_left(tmp_path, "watch") == []


def test_the_engine_s_three_pools_watch_their_parent() -> None:
    assert "leave_with_parent()" in inspect.getsource(workers.cpu_init)
    assert "leave_with_parent()" in inspect.getsource(textures._worker_init)
    assert "initializer=leave_with_parent" in inspect.getsource(masks_build)


def test_the_main_process_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine itself has no parent of ours: nothing to watch, nothing started."""
    started: list[object] = []
    monkeypatch.setattr("threading.Thread.start", lambda self: started.append(self))
    leave_with_parent()
    assert started == []
