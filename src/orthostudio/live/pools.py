# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's encoding processes: forked from a small process of their own, and a pool started again
when it loses one.

A process started from Live's own may die before it runs anything (2026-09-28, a comparison flight
from LFBO): OrthoStudio XP's engine, which Live's process loads, keeps PROJ's database open, PROJ
closes it in every new process, and macOS's log crashed it there. The pool of the first 8192 px
texture of the flight was broken for good, the builder stopped, and Live with it. So the encoders
are forked by a fork server (:func:`context`): a bare Python, started from Live's process once, as
early as it can (:func:`start_server`, before the engine is loaded), where forking is safe. And a
pool that loses a process all the same, to a crash or to macOS short of memory, starts again
(:class:`RenewingPool`): the textures it was encoding fail and are queued again, and Live goes on.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import time
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import forkserver
from multiprocessing.context import BaseContext
from typing import Any

RENEW_WINDOW_S = 60.0
MAX_RENEWALS = 5  # pools lost within RENEW_WINDOW_S beyond which something else is wrong


def context() -> BaseContext:
    """The fork server's, with nothing loaded in it: each process it forks imports what it runs."""
    ctx = multiprocessing.get_context("forkserver")
    ctx.set_forkserver_preload([])
    return ctx


def start_server() -> None:
    """Start the fork server now, while Live's process is small: the first pool would start it
    otherwise, from a process that may have loaded PROJ by then, and starts it again if it died."""
    context()
    with contextlib.suppress(OSError):
        forkserver.ensure_running()


class RenewingPool:
    """A process pool that starts again when it has lost a process: what it was running fails
    (``BrokenProcessPool``), what is submitted next goes to a new pool. More than MAX_RENEWALS
    pools lost within RENEW_WINDOW_S: the error, as a plain pool gives it."""

    def __init__(self, make: Callable[[], ProcessPoolExecutor], log: Callable[[str], None]) -> None:
        self._make = make
        self._log = log
        self._pool = make()
        self._lost: list[float] = []
        self.renewals = 0

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        while True:
            try:
                return self._pool.submit(fn, *args, **kwargs)
            except (BrokenProcessPool, OSError) as e:  # OSError: the fork server did not start
                self._renew(e)

    def _renew(self, cause: BaseException) -> None:
        now = time.monotonic()
        self._lost = [t for t in self._lost if now - t < RENEW_WINDOW_S] + [now]
        if len(self._lost) > MAX_RENEWALS:
            raise BrokenProcessPool(
                f"{len(self._lost)} encoding pools lost within {RENEW_WINDOW_S:.0f} s"
            ) from cause
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._pool = self._make()
        self.renewals += 1
        self._log(f"  an encoding process died ({cause}): its pool started again")

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)

    def __enter__(self) -> RenewingPool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
