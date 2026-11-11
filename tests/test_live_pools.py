# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's process forks nothing while X-Plane runs (orthostudio.live.pools): its encoders are forked
by a fork server (new Pythons on Windows, tests/test_live_systems.py), a pool that loses one starts
again, X-Plane is found in the system's list of processes and started by posix_spawn on a Mac."""

import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import pytest

from orthostudio.live import pools, scenery


@pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot fork")
def test_the_encoders_are_forked_by_a_fork_server():
    assert pools.context().get_start_method() == "forkserver"


def test_a_pool_that_loses_a_process_starts_again():
    lines = []
    make = lambda: ProcessPoolExecutor(max_workers=1, mp_context=pools.context())  # noqa: E731
    with pools.RenewingPool(make, lines.append) as pool:
        died = pool.submit(os._exit, 1)  # a process that dies, as one crashed at a fork
        with pytest.raises(BrokenProcessPool):
            died.result(timeout=60)
        assert pool.submit(pow, 2, 5).result(timeout=60) == 32
    assert pool.renewals == 1
    assert any("its pool started again" in line for line in lines)


class _Lost:
    """A pool that has lost its processes, as every new one would."""

    def submit(self, *args, **kwargs):
        raise BrokenProcessPool("lost")

    def shutdown(self, wait=True, cancel_futures=False):
        pass


def test_pools_lost_again_and_again_stop_the_builder():
    pool = pools.RenewingPool(_Lost, lambda line: None)
    with pytest.raises(BrokenProcessPool):
        pool.submit(pow, 2, 5)
    assert pool.renewals == pools.MAX_RENEWALS


def test_x_plane_is_found_in_the_list_of_processes(monkeypatch):
    monkeypatch.setattr(scenery, "_xplane_running", lambda: pytest.fail("ps was run"))
    monkeypatch.setattr(scenery, "_processes", lambda: {1: ("launchd", 0.0), 7: ("X-Plane", 9.5)})
    assert scenery.xplane_running()
    assert scenery.xplane_started_at() == 9.5
    two = {7: ("X-Plane", 9.5), 9: ("X-Plane", 30.0)}  # one quitting, the next one starting
    monkeypatch.setattr(scenery, "_processes", lambda: two)
    assert scenery.xplane_started_at() == 30.0
    monkeypatch.setattr(scenery, "_processes", lambda: {1: ("launchd", 0.0), 8: ("Finder", 1.0)})
    assert not scenery.xplane_running()
    assert scenery.xplane_started_at() is None


def test_without_that_list_osxp_answers(monkeypatch):
    monkeypatch.setattr(scenery, "_processes", lambda: None)
    monkeypatch.setattr(scenery, "_xplane_running", lambda: True)
    assert scenery.xplane_running()


def test_the_list_of_processes_holds_this_one():
    name, started = scenery._processes()[os.getpid()]
    assert name
    assert 0 < started <= time.time()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS's posix_spawn")
def test_a_command_is_started_without_a_fork(monkeypatch):
    monkeypatch.setattr(subprocess, "_fork_exec", lambda *a: pytest.fail("Live's process forked"))
    assert scenery._spawn(["/usr/bin/true"]).wait(timeout=30) == 0
