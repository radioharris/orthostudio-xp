# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Bring a window another program opened in front of the browser, on Windows.

The File Explorer window of "Show in File Explorer" opened behind the browser (a user,
2026-09-15): the engine that starts it runs in the background, and Windows lets only the program
the user works in put a window in front. The window is found among the top-level windows (a new
File Explorer window, or the dialog of the process started) and brought in front with the input of
the foreground window attached, else with the Alt key: the two ways Windows leaves a background
program. Nothing here raises; on other systems nothing happens.
"""

from __future__ import annotations

import functools
import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

__all__ = [
    "DIALOG_CLASS",
    "EXPLORER_CLASS",
    "TopWindow",
    "explorer_windows",
    "force_foreground",
    "front_explorer_window",
    "front_window_of",
    "pick_explorer_window",
    "top_windows",
]

EXPLORER_CLASS = "CabinetWClass"
"""The window class of a File Explorer window."""

DIALOG_CLASS = "#32770"
"""The window class of a dialog box, the folder dialog among them."""

WAIT_S = 8.0
"""How long a window is looked for after its program was started."""

POLL_S = 0.1

VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x2
SW_RESTORE = 9


@dataclass(frozen=True, slots=True)
class TopWindow:
    hwnd: int
    cls: str
    title: str
    pid: int


@functools.cache
def _api() -> tuple[Any, Any, Any]:
    """``(user32, kernel32, the EnumWindows callback type)``, their argument types declared on
    private instances (``ctypes.windll``'s are shared with every other module)."""
    import ctypes
    from ctypes import wintypes

    user32: Any = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    enum_proc: Any = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)  # type: ignore[attr-defined]
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    return user32, kernel32, enum_proc


def top_windows() -> list[TopWindow]:
    """The visible top-level windows, front to back; empty on other systems or on failure."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    user32, _kernel32, enum_proc = _api()
    found: list[TopWindow] = []

    def each(hwnd: int | None, _lparam: int) -> bool:
        if hwnd and user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            title = ctypes.create_unicode_buffer(1024)
            pid = wintypes.DWORD(0)
            user32.GetClassNameW(hwnd, cls, 256)
            user32.GetWindowTextW(hwnd, title, 1024)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append(TopWindow(int(hwnd), cls.value, title.value, int(pid.value)))
        return True

    try:
        user32.EnumWindows(enum_proc(each), 0)
    except OSError:
        return []
    return found


def explorer_windows() -> set[int]:
    """The File Explorer windows open now (taken before a reveal)."""
    return {w.hwnd for w in top_windows() if w.cls == EXPLORER_CLASS}


def pick_explorer_window(
    windows: Iterable[TopWindow], before: set[int], folder: PurePath | None, *, waited: bool
) -> int | None:
    """The File Explorer window a reveal opened: a new one first. Once the wait is over, one
    already open whose title names the folder (Windows may show the folder in a window that was
    open already instead of opening another)."""
    explorers = [w for w in windows if w.cls == EXPLORER_CLASS]
    new = [w.hwnd for w in explorers if w.hwnd not in before]
    if new:
        return new[0]
    if not waited or folder is None:
        return None
    names = {folder.name.casefold(), str(folder).casefold()}
    named = [w.hwnd for w in explorers if w.title.casefold() in names]
    return named[0] if named else None


def force_foreground(hwnd: int) -> bool:
    """Put ``hwnd`` in front with the keyboard; whether it is in front afterwards."""
    if os.name != "nt":
        return False
    user32, kernel32, _enum_proc = _api()
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        front = user32.GetForegroundWindow()
        if front == hwnd:
            return True
        # 1. the input of the foreground window's thread attached to this one: its rights shared
        theirs = user32.GetWindowThreadProcessId(front, None) if front else 0
        mine = kernel32.GetCurrentThreadId()
        attached = bool(theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(mine, theirs, False)
        if user32.GetForegroundWindow() == hwnd:
            return True
        # 2. the Alt key: Windows lets the program that sent the last input set the front window
        user32.keybd_event(VK_MENU, 0, 0, None)
        try:
            user32.SetForegroundWindow(hwnd)
        finally:
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, None)
        return bool(user32.GetForegroundWindow() == hwnd)
    except OSError:
        return False


def _in_background(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="osxp-front", daemon=True).start()


def front_explorer_window(
    before: set[int], folder: PurePath | None, *, wait_s: float = WAIT_S
) -> None:
    """In the background: bring the File Explorer window a reveal opens in front of the browser.
    ``before`` is :func:`explorer_windows` taken before the reveal, ``folder`` the folder shown."""
    if os.name != "nt":
        return

    def run() -> None:
        deadline = time.monotonic() + wait_s
        while True:
            waited = time.monotonic() >= deadline
            hwnd = pick_explorer_window(top_windows(), before, folder, waited=waited)
            if hwnd is not None:
                force_foreground(hwnd)
                return
            if waited:
                return
            time.sleep(POLL_S)

    _in_background(run)


def front_window_of(pid: int, done: threading.Event, *, wait_s: float = WAIT_S) -> None:
    """In the background: bring the dialog of process ``pid`` in front of the browser once it
    shows, unless ``done`` is set first (answered, or its program ended)."""
    if os.name != "nt":
        return

    def run() -> None:
        deadline = time.monotonic() + wait_s
        while not done.is_set() and time.monotonic() < deadline:
            dialogs = [w.hwnd for w in top_windows() if w.pid == pid and w.cls == DIALOG_CLASS]
            if dialogs:
                force_foreground(dialogs[0])
                return
            time.sleep(POLL_S)

    _in_background(run)
