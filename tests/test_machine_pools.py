# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""How many processes Build and Live start (``orthostudio.machine.cpu_workers``): every processor
but two, at least one, and no more than a pool may have here, Python's own limit on Windows
(61)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from orthostudio import machine
from orthostudio.model import TileRef


@pytest.mark.parametrize(
    ("limit", "count", "workers"),
    [
        (61, 64, 61),
        (61, 63, 61),
        (61, 62, 60),
        (61, 14, 12),
        (61, 2, 1),
        (61, 1, 1),
        (None, 64, 62),
        (None, 128, 126),
    ],
)
def test_every_processor_but_two_within_the_limit(
    monkeypatch: pytest.MonkeyPatch, limit: int | None, count: int, workers: int
) -> None:
    monkeypatch.setattr(machine, "POOL_MAX", limit)
    assert machine.cpu_workers(count) == workers
    monkeypatch.setattr(os, "process_cpu_count", lambda: count, raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: count)
    assert machine.processors() == count and machine.cpu_workers() == workers


def test_a_computer_that_does_not_say_counts_four(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "process_cpu_count", lambda: None, raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert machine.processors() == 4


def test_a_windows_computer_of_64_threads_builds_with_61(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every pool of a build, from its defaults and from a count set by hand (``--workers``,
    ``$OSXP_MASKS_WORKERS``), on a Windows computer of 64 threads; Live's encoders too."""
    from orthostudio.graph import Store
    from orthostudio.live import machine as live_machine
    from orthostudio.masks.build import env_workers, worker_count
    from orthostudio.pipeline.build import BuildEnv, BuildSpec
    from orthostudio.sched.scheduler import Scheduler
    from orthostudio.vectors.roads import square_workers

    monkeypatch.setattr(machine, "POOL_MAX", 61)
    monkeypatch.setattr(os, "process_cpu_count", lambda: 64, raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 64)

    def env(workers: int | None) -> int:
        spec = BuildSpec(
            tile=TileRef.parse("+43+005"),
            provider="BI",
            zl=16,
            store_root=tmp_path / "store",
            chunks_root=tmp_path / "chunks",
            workdir=tmp_path / "work",
            out_dir=tmp_path / "out",
            workers=workers,
        )
        return BuildEnv.create([spec]).workers

    assert (env(None), env(100), env(0)) == (61, 61, 0)  # 0: the images encoded in their process
    assert Scheduler(Store(tmp_path / "graph")).cpu_workers == 61
    monkeypatch.setenv("OSXP_MASKS_WORKERS", "100")  # what the budget counts is what starts
    assert env_workers() == worker_count(200) == 61
    assert square_workers() == 61 // 3  # the engine's 61 CPU tasks shared by 3 tracings
    monkeypatch.setenv("OSXP_LIVE_MACHINE", "64,256")
    assert live_machine.share().encoders == 61 and live_machine.share(100).encoders == 61
