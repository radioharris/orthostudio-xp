"""The end of the imagery step, when an encoder will not come back.

A user watched Imagery sit at 99 % with Assembly pending and no activity; stopping the build and
starting it again showed the imagery already finished, and he had to find two Python processes in
his task manager and kill them by hand (2026-09-23). The work was done; the step could not end,
because closing the pool joins its processes and one of them never returned.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from typing import Any


def sleeps_for_ever(_x: int) -> None:
    """An encoder that never answers. At module level so ``spawn`` can import it."""
    import time as _time

    _time.sleep(600)


class _JustThePool:
    """What ``_close_pool`` reads of the run."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool


def test_the_step_ends_even_when_an_encoder_never_comes_back(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from orthostudio.pipeline import textures

    monkeypatch.setattr(textures, "POOL_CLOSE_S", 1.0)
    pool = ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn"))
    workers: list[Any] = []
    try:
        pool.submit(sleeps_for_ever, 1)
        deadline = time.monotonic() + 30.0
        while not pool._processes and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(1.0)  # the worker is inside its sleep
        workers = list(pool._processes.values())  # kept: shutdown forgets them
        assert workers

        t0 = time.perf_counter()
        asyncio.run(textures._Pipeline._close_pool(_JustThePool(pool)))
        took = time.perf_counter() - t0
    finally:
        for proc in workers:
            if proc.is_alive():
                proc.kill()

    assert took < 20.0, f"the step waited {took:.0f} s for an encoder that will not answer"
    assert not [p for p in workers if p.is_alive()], (
        "a stopped build must leave no Python of its own running"
    )


def test_the_rope_is_long_enough_for_a_whole_tile_handed_over_at_once() -> None:
    """A user's own numbers: 14 workers, 719 textures, 6.7 s each at the median. Rebuilt from a
    full cache, every texture is handed to the pool at once, so the last ones wait about 350 s
    before a worker even starts on them. A fixed 300 s would have marked them failed and refused
    a tile that was building perfectly well (2026-09-23)."""
    from orthostudio.pipeline.textures import ENCODE_TIMEOUT_S, _Pipeline

    class Run:
        encode_seconds = [7.29] * 50
        workers = 14
        encoding = 0

    run = Run()
    assert _Pipeline._encode_deadline(run) == ENCODE_TIMEOUT_S  # nothing queued: the floor

    run.encoding = 719
    rope = _Pipeline._encode_deadline(run)
    his_tile_takes = 719 * 7.29 / 14  # 374 s of queue, measured
    assert rope > 3 * his_tile_takes, "no healthy texture may ever reach the rope"

    slow = Run()
    slow.encode_seconds = [90.0] * 20  # a machine four times slower still
    slow.encoding = 200
    assert _Pipeline._encode_deadline(slow) > 200 * 90.0 / 14
