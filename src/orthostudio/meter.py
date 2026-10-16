# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Who counts the bytes a download receives, and those written beside the store: the build step
they are for.

A step binds its counters (``NodeContext.fetched`` and ``NodeContext.wrote``) with
:func:`counting`, and the code that receives or writes the bytes, deep down (the OpenStreetMap
library, Overpass, a relief file), says how many with :func:`received` or :func:`written`,
without every function in between carrying the counters. The binding is a context variable: it
holds in the thread that set it and in the coroutines ``asyncio.run`` starts there, not in another
thread, so a receiver whose bytes arrive elsewhere counts them back in the step's own thread.
Nothing bound, nothing is counted (a probe, a test).

Works shows per step what a build really downloaded and wrote (a user, 2026-10-16).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar

__all__ = ["counting", "received", "written"]

_Counter = Callable[[int], None]
_COUNTERS: ContextVar[tuple[_Counter | None, _Counter | None]] = ContextVar(
    "osxp_byte_counters", default=(None, None)
)


@contextlib.contextmanager
def counting(fetched: _Counter | None = None, wrote: _Counter | None = None) -> Iterator[None]:
    """Count what :func:`received` and :func:`written` hear in this context for the block."""
    token = _COUNTERS.set((fetched, wrote))
    try:
        yield
    finally:
        _COUNTERS.reset(token)


def received(nbytes: int) -> None:
    """Say that ``nbytes`` came from the network, to the step that counts in this context."""
    counter = _COUNTERS.get()[0]
    if counter is not None and nbytes > 0:
        counter(int(nbytes))


def written(nbytes: int) -> None:
    """Say that ``nbytes`` were written to the disk outside the store (a relief file)."""
    counter = _COUNTERS.get()[1]
    if counter is not None and nbytes > 0:
        counter(int(nbytes))
