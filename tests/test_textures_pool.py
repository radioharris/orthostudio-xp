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

import pytest

from orthostudio.pipeline import textures


def sleeps_for_ever(_x: int) -> None:
    """An encoder that never answers. At module level so ``spawn`` can import it."""
    import time as _time

    _time.sleep(600)


def deaf_to_the_signal(_x: int) -> None:
    """An encoder that will not take the polite signal: a worker blocked in a write to a drive
    that has stopped answering behaves like this."""
    import signal
    import time as _time

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    _time.sleep(600)


class _JustThePool:
    """What ``_close_pool`` reads of the run."""

    _take_workers_down = textures._Pipeline._take_workers_down

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.workers_seen: list[Any] = []


def test_the_step_ends_even_when_an_encoder_never_comes_back(monkeypatch) -> None:  # type: ignore[no-untyped-def]
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
        for proc in workers:  # terminate is a signal, not a wait
            proc.join(10.0)
    finally:
        for proc in workers:
            if proc.is_alive():
                proc.kill()

    assert took < 20.0, f"the step waited {took:.0f} s for an encoder that will not answer"
    assert not [p for p in workers if p.is_alive()], (
        "a stopped build must leave no Python of its own running"
    )


def test_the_workers_are_taken_down_even_when_the_pool_was_already_shut() -> None:
    """A stop cancels the step, and a cancel usually shuts the pool before this runs; the pool
    then answers ``None`` when asked for its processes and every child survives the build. The
    list is taken while the workers are there to take (found in review, 2026-09-23)."""
    pool = ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn"))
    workers: list[Any] = []
    try:
        pool.submit(sleeps_for_ever, 1)
        deadline = time.monotonic() + 30.0
        while not pool._processes and time.monotonic() < deadline:
            time.sleep(0.05)
        workers = list(pool._processes.values())
        assert workers

        run = _JustThePool(pool)
        run.workers_seen = workers  # as ``_hand_over`` keeps them, submit by submit
        pool._processes = None  # what a cancelled shutdown leaves behind
        run._take_workers_down()
        for proc in workers:
            proc.join(10.0)
    finally:
        for proc in workers:
            if proc.is_alive():
                proc.kill()

    assert not [p for p in workers if p.is_alive()]


def test_the_rope_measures_the_encoding_and_not_the_queue() -> None:
    """A user's own numbers: 14 workers, 719 textures, 6.7 s each at the median, 60.6 s at the
    worst. Rebuilt from a full cache every texture used to be handed to the pool at once, so the
    last ones waited about 350 s before a worker started on them and a fixed 300 s marked them
    failed. Now only ``workers + 2`` are handed over at a time, so the rope times one encoding
    (2026-09-23)."""
    from orthostudio.pipeline.textures import ENCODE_TIMEOUT_S, MAX_ENCODE_ROPE_S, _Pipeline

    class Run:
        def __init__(self) -> None:
            self.encode_seconds: list[float] = []
            self.workers = 14

    run = Run()
    assert _Pipeline._encode_deadline(run) == ENCODE_TIMEOUT_S  # nothing measured yet: the floor

    run.encode_seconds = [7.29] * 50  # his machine
    # his worst texture took 60.6 s, and behind a full pool a texture waits about one encoding
    assert _Pipeline._encode_deadline(run) > 5 * 60.6, "no healthy texture may reach the rope"

    slow = Run()
    slow.encode_seconds = [90.0] * 20  # a machine four times slower still
    assert _Pipeline._encode_deadline(slow) == 20.0 * 90.0

    stuck = Run()
    stuck.encode_seconds = [3600.0] * 5  # measurements a wedged run would feed it
    assert _Pipeline._encode_deadline(stuck) == MAX_ENCODE_ROPE_S, (
        "the rope has an end, whatever the measurements say"
    )


def test_no_more_textures_are_handed_over_than_the_pool_can_hold() -> None:
    """The bound is what makes the rope honest: a texture's clock starts when a worker is free
    for it, not when the tile was scheduled."""
    handed: list[int] = []
    inflight = 0

    class Run:
        pool = None
        encoding = 0
        t_first_submit = 0.0
        encode_slots = None

        async def _hand_over(self, st: Any, job: Any, dest: Any) -> None:
            nonlocal inflight
            inflight += 1
            handed.append(inflight)
            await asyncio.sleep(0.01)
            inflight -= 1

    async def go() -> None:
        run = Run()
        run.encode_slots = asyncio.Semaphore(4 + 2)
        await asyncio.gather(
            *(textures._Pipeline._encode_one(run, None, None, None) for _ in range(60))
        )

    asyncio.run(go())
    assert len(handed) == 60
    assert max(handed) <= 6, f"{max(handed)} textures were in the pool's hands at once"


def test_an_encoder_that_ignores_the_signal_is_killed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``terminate`` is a signal a worker may ignore. One that does held the thread inside
    ``pool.shutdown``, and the interpreter then waits 300 s for that thread at the end of the
    run: the progress line freezes with nothing happening and the user's Python is still in his
    task manager, which is the fault this is here to stop (found in review, 2026-09-23)."""
    monkeypatch.setattr(textures, "POOL_CLOSE_S", 1.0)
    pool = ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))
    workers: list[Any] = []
    try:
        pool.submit(deaf_to_the_signal, 1)
        deadline = time.monotonic() + 30.0
        while not pool._processes and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(1.5)  # it has installed its handler and is inside its sleep
        workers = list(pool._processes.values())
        assert workers

        t0 = time.perf_counter()
        asyncio.run(textures._Pipeline._close_pool(_JustThePool(pool)))
        took = time.perf_counter() - t0
        for proc in workers:
            proc.join(10.0)
    finally:
        for proc in workers:
            if proc.is_alive():
                proc.kill()

    assert took < 20.0, f"the step waited {took:.0f} s for an encoder that will not end"
    assert not [p for p in workers if p.is_alive()], (
        "an encoder deaf to the signal must still be gone when the build stops"
    )


class _OneTexture:
    """The little of a texture's state that ``_hand_over`` touches."""

    submitted_at = 0.0


class _ShutPool:
    """A pool the stop has already closed, as ``_cancel_now`` leaves it."""

    _processes: dict[str, Any] | None = None

    def submit(self, *_args: Any, **_kw: Any) -> Any:
        raise RuntimeError("cannot schedule new futures after shutdown")


class _Handing:
    """What ``_hand_over`` reads and writes of the run."""

    _hand_over = textures._Pipeline._hand_over

    def __init__(self, pool: Any, *, cancelled: bool) -> None:
        self.pool = pool
        self.cancelled = cancelled
        self.encoding = 0
        self.t_first_submit = 0.0
        self.workers_seen: list[Any] = []
        self.said: list[str] = []

    def _finish(self, _st: Any, status: str, *, error: Any = None) -> None:
        self.said.append(status)

    async def _after_worker(self, *_args: Any, **_kw: Any) -> None:
        raise AssertionError("a stopped build hands nothing to a worker")


def test_a_stop_while_a_texture_waits_for_a_worker_is_a_stop_and_not_a_fault() -> None:
    """The step looks at the stop before a texture queues for a worker, and the wait for one is
    exactly where a stop lands. Handed to a pool the stop had just shut, the texture came back as
    an internal error: the report the user is asked to send was filled with them, and each one
    told him to file a bug for having pressed Stop (found in review, 2026-09-23)."""
    run = _Handing(_ShutPool(), cancelled=True)
    asyncio.run(run._hand_over(_OneTexture(), None, None))
    assert run.said == ["cancelled"]
    assert run.encoding == 0, "a texture that never reached a worker is not counted as encoding"

    # and if the stop lands between the guard and the hand-over, the answer is the same
    late = _Handing(_ShutPool(), cancelled=False)
    late.cancelled = False

    class _Late(_ShutPool):
        def submit(self, *_args: Any, **_kw: Any) -> Any:
            late.cancelled = True  # the stop, arriving as the texture is handed over
            raise RuntimeError("cannot schedule new futures after shutdown")

    late.pool = _Late()
    asyncio.run(late._hand_over(_OneTexture(), None, None))
    assert late.said == ["cancelled"] and late.encoding == 0

    # a RuntimeError that is not a stop is still a fault, and is not swallowed
    broken = _Handing(_ShutPool(), cancelled=False)
    with pytest.raises(RuntimeError):
        asyncio.run(broken._hand_over(_OneTexture(), None, None))
    assert broken.said == [] and broken.encoding == 0
