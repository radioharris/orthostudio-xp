# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""How long a start took, and where the time went: what ``serve.log`` says of it.

The app took more than 45 s to open on a cloud PC after an install, and the log could not say
why (a Shadow PC, 2026-10-02): no line of it had a time between the window's and uvicorn's, which
carry none. A start now says its parts. The window says Python's own start and its own loading as
it starts the engine (``desktop.in_a_window``). The engine says Python's own start, the loading of
its code, its setting up and the opening of its port once it listens, then when its page first
said it was open (``api/serve.py``, ``api/app.py``). What the page waits for longer than
:data:`SLOW_S` is named with its time: an answer of the API, a part of the status, a check of the
doctor.

Python's own start runs from the creation of the process to :data:`orthostudio.STARTED_AT`. The
engine's is known everywhere, from the time its window hands it (:data:`LAUNCHED_ENV`). The
window's own is known on Windows alone, which says when a process was created
(``GetProcessTimes``).
"""

from __future__ import annotations

import os
import sys

from orthostudio import STARTED_AT

__all__ = [
    "LAUNCHED_ENV",
    "SLOW_S",
    "from_filetime",
    "launched_at",
    "parts",
    "process_started_at",
    "span",
]

LAUNCHED_ENV = "OSXP_LAUNCHED_AT"
"""When the window started its engine (``time.time()``), handed to the engine's process."""

SLOW_S = 1.0
"""What the page waits for longer than this is named in ``serve.log`` with its time."""

LONGEST_S = 24 * 3600.0
"""A span longer than this, or below zero, is a clock that moved, not a start: it is left out."""

FILETIME_EPOCH_S = 11_644_473_600
"""Seconds from Windows' epoch (1601-01-01) to the one of ``time.time()`` (1970-01-01)."""


def span(start: float | None, end: float | None) -> float | None:
    """``end - start``; ``None`` when either is unknown or the span cannot be a start's."""
    if start is None or end is None:
        return None
    seconds = end - start
    return seconds if 0.0 <= seconds <= LONGEST_S else None


def parts(*named: tuple[str, float | None]) -> str:
    """``Python 1.2 s, loading 24.3 s``: the parts whose time is known, in their order."""
    return ", ".join(f"{name} {seconds:.1f} s" for name, seconds in named if seconds is not None)


def from_filetime(ticks: int) -> float:
    """A Windows ``FILETIME`` (100 ns steps since 1601) on ``time.time()``'s clock."""
    return ticks / 10_000_000 - FILETIME_EPOCH_S


def process_started_at() -> float | None:
    """When the system created this process, on ``time.time()``'s clock; ``None`` on any system
    but Windows, or when Windows does not say."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32")  # its own, so that no one else's signatures change
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            *[ctypes.POINTER(wintypes.FILETIME)] * 4,
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(
            kernel32.GetCurrentProcess(),
            ctypes.byref(created),
            ctypes.byref(ended),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        return from_filetime((created.dwHighDateTime << 32) | created.dwLowDateTime)
    except (OSError, AttributeError, TypeError, ValueError):
        return None


def launched_at() -> float | None:
    """When the window started this engine (:data:`LAUNCHED_ENV`); ``None`` for an engine started
    otherwise. It is taken out of the environment, so that what the engine starts does not
    inherit it."""
    value = os.environ.pop(LAUNCHED_ENV, None)
    try:
        when = None if value is None else float(value)
    except ValueError:
        return None
    return when if span(when, STARTED_AT) is not None else None
