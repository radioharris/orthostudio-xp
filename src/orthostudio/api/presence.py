# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The app started from its icon stops once no page of it has been open for a while.

Closing the page leaves the engine running, so that a build goes on; but nothing shows the engine
then (``pythonw`` gives it no window on Windows), and a user on Windows had to end it in the Task
Manager (2026-09-17). An open page says so every 30 s, and at once when it shows again
(``POST /api/presence``); ``osxp serve --quit-when-closed``, what the app runs, stops the engine
:data:`QUIT_AFTER_S` after the last word from a page, unless a build runs or waits.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

__all__ = ["QUIT_AFTER_S", "TICK_S", "Presence", "quit_when_closed"]

QUIT_AFTER_S = 300.0
"""How long the engine waits once no page has said it is open. A page in a background tab still
says so about once a minute: browsers slow its timers down, they do not stop them."""

TICK_S = 15.0
"""How often the wait is checked. A check far later than due means the computer slept, and the
wait starts again: the page could not speak meanwhile."""


class Presence:
    """When a page of OrthoStudio XP last said it was open (the engine's start counts as one)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._seen = clock()

    def seen(self) -> None:
        with self._lock:
            self._seen = self._clock()

    def idle_s(self) -> float:
        """Seconds since a page last said it was open."""
        with self._lock:
            return self._clock() - self._seen


def quit_when_closed(
    presence: Presence,
    busy: Callable[[], bool],
    stop: Callable[[], None],
    *,
    wait: Callable[[float], bool],
    clock: Callable[[], float] = time.monotonic,
    after_s: float = QUIT_AFTER_S,
    tick_s: float = TICK_S,
    say: Callable[[str], object] = print,
) -> bool:
    """Call ``stop`` once no page has said it is open for ``after_s`` and no build runs or waits
    (``busy``); ``True`` then.

    ``wait(tick_s)`` sleeps between checks and returns ``True`` when the engine stops anyway: the
    watch ends then, ``False``. A build under way, or a check more than three ticks late (the
    computer slept), starts the wait again.
    """
    last = clock()
    while not wait(tick_s):
        now = clock()
        if busy() or now - last > 3 * tick_s:
            presence.seen()
        last = now
        if presence.idle_s() >= after_s:
            say(
                f"No page of OrthoStudio XP has been open for {after_s / 60:g} min and no build "
                "runs: OrthoStudio XP stops."
            )
            stop()
            return True
    return False
