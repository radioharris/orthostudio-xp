# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the machine has (``orthostudio.machine``): the memory free now and what a process's
children hold, which the scheduler keeps to below its share of the memory."""

from __future__ import annotations

import subprocess
import sys
import time

from orthostudio.machine import (
    available_memory_mb,
    children_memory_mb,
    physical_memory_mb,
    process_memory_mb,
)

HOLD = """
import subprocess, sys
held = b"\\x01" * (200 * 2**20)  # written, so counted by every system
if sys.argv[1] == "parent":
    # a pipe of its own: Windows does not hand a child the pipes its parent was given; the child
    # reads it to its end, which comes when the parent ends
    child = subprocess.Popen([sys.executable, "-c", sys.argv[2], "x", ""], stdin=subprocess.PIPE)
    sys.stdout.write("ready\\n")
    sys.stdout.flush()
sys.stdin.read()
"""
# The interpreter itself: a virtual environment's python.exe on Windows is a launcher that runs it
# as its child, and the launcher holds next to nothing of the 200 MB (the ci on Windows, 2026-10-30)
PYTHON = getattr(sys, "_base_executable", sys.executable)


def test_a_child_older_than_its_parent_is_not_counted() -> None:
    """Windows keeps the number of a parent that ended, which a later process may take:
    explorer.exe's parent ends at logon, and the engine given its number counted X-Plane and every
    app started from the Start menu as the batch's own (review, 2026-10-29)."""
    from orthostudio.machine import _descendants

    engine = 100
    parents = {
        engine: [200, 300],  # 200 its tracing; 300 explorer.exe, from before the engine
        200: [210],  # the tracing's own child
        300: [310],  # X-Plane, started from explorer
    }
    started = {engine: 50, 200: 60, 210: 61, 300: 10, 310: 40}
    assert sorted(_descendants(engine, parents, started.get)) == [200, 210]
    assert sorted(_descendants(engine, parents)) == [200, 210, 300, 310]  # macOS, Linux


def test_the_memory_free_now_is_read_on_this_computer() -> None:
    free, total = available_memory_mb(), physical_memory_mb()
    assert free is not None and total is not None
    assert 0 < free <= total


def test_the_memory_of_the_children_and_theirs_is_counted() -> None:
    """A step's process and what it starts (the mesh's Triangle4XP) both count: 200 MB each."""
    before = children_memory_mb()
    assert before is not None
    proc = subprocess.Popen(
        [PYTHON, "-c", HOLD, "parent", HOLD],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline() == "ready\n"
        own = process_memory_mb(proc.pid)
        assert own is not None and own >= 190
        for _ in range(100):  # the grandchild writes its 200 MB after its parent said ready
            held = children_memory_mb()
            assert held is not None
            if held - before >= 380:
                break
            time.sleep(0.1)
        assert held - before >= 380, held - before
    finally:
        assert proc.stdin is not None
        proc.stdin.close()  # both read it to its end, then stop
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
