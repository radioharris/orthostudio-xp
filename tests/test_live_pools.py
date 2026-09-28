# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's encoding processes: forked by a fork server, a pool started again when it loses one; and
X-Plane found running without a command started from Live's process."""

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import pytest

from orthostudio.live import pools, scenery


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
    monkeypatch.setattr(scenery, "_process_names", lambda: {"launchd", "X-Plane"})
    assert scenery.xplane_running()
    monkeypatch.setattr(scenery, "_process_names", lambda: {"launchd", "Finder"})
    assert not scenery.xplane_running()


def test_without_that_list_osxp_answers(monkeypatch):
    monkeypatch.setattr(scenery, "_process_names", lambda: None)
    monkeypatch.setattr(scenery, "_xplane_running", lambda: True)
    assert scenery.xplane_running()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS's list of processes")
def test_the_list_of_processes_holds_this_one():
    import ctypes

    name = ctypes.create_string_buffer(256)
    ctypes.CDLL("/usr/lib/libproc.dylib").proc_name(os.getpid(), name, 256)
    assert name.value.decode() in scenery._process_names()
