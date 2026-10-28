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


def test_the_memory_free_now_is_read_on_this_computer() -> None:
    free, total = available_memory_mb(), physical_memory_mb()
    assert free is not None and total is not None
    assert 0 < free <= total


def test_the_memory_of_the_children_and_theirs_is_counted() -> None:
    """A step's process and what it starts (the mesh's Triangle4XP) both count: 200 MB each."""
    before = children_memory_mb()
    assert before is not None
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLD, "parent", HOLD],
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
