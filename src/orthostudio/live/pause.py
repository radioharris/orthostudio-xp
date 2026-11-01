# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live pauses X-Plane itself at a new location chosen in flight, until the ortho there is built.

A new flight started from within a flight (Flight Configuration, Fly) loads no aircraft, so the
plugin hears nothing it could hold X-Plane at: X-Plane 12.4.3 calls no plugin at all between that
click and the scenery's reading (no message, no flight loop, no drawing: probes of 2026-11-01).
Message 115, which the plugin held at before, is XPLM_MSG_WEATHER_DELIVERED (SDK 4.4.0): 12.4.4
sends it as it delivers a flight's weather, before its scenery, but at every change of weather too
(three changes of weather in flight, three of it), and 12.4.3 not at all.

What X-Plane does do at that click is write ``Output/preferences/Freeflight.prf``, in place, line
by line, several times within a few milliseconds: three times with the flight running, then with
the new ``_last_start`` near its top, before it reads any scenery (on a Mac and on Windows,
2026-11-01). So :class:`Pauser` is woken at every write of that file while X-Plane flies (kqueue on
a Mac, inotify on Linux, its size read every millisecond on Windows), pauses X-Plane's process at
once (SIGSTOP; NtSuspendProcess on Windows), then reads ``_last_start``: the one already there (the
first writes, a change of weather, X-Plane quitting), X-Plane goes on at once, within a
millisecond; a new one, X-Plane stays paused until the streamer's loop says everything it will read
there is built (:meth:`Pauser.resume`). Pauses held X-Plane between « Starting new flight » and
« Init dat_p0 », with no scenery file open, on a Mac (12.4.4) and on Windows (12.4.3).

A flight X-Plane loads otherwise (the first one, from the main menu; a situation; its web API)
loads its aircraft, and the plugin holds those: the pause is for the one case the plugin cannot
take. While X-Plane is paused nothing of it runs, the plugin neither, so Live shows the wait in a
window of its own (``orthostudio.live.pause_window``); on Windows the plugin has asked Windows, as
X-Plane started, not to grey X-Plane's window as « Not Responding » and offer to close it.

A guard process (:data:`GUARD`) holds the reading end of a pipe whose writing end only Live holds:
whatever way Live ends, its end closes, and the guard lets X-Plane go at that instant.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import select
import signal
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from orthostudio.fsutil import NO_CONSOLE_WINDOW

Log = Callable[[str], None]

POLL_S = 0.001  # Windows: the file's size read this often (no event comes early enough)
FIND_S = 1.0  # X-Plane looked for this often while it does not run

# the guard: reads its standard input to its end (Live gone, or done), then lets X-Plane go
GUARD = """import sys
pid = int(sys.argv[1])
sys.stdin.buffer.read()
if sys.platform == "win32":
    import ctypes
    h = ctypes.WinDLL("kernel32").OpenProcess(0x0800, False, pid)
    if h:
        ctypes.WinDLL("ntdll").NtResumeProcess(ctypes.c_void_p(h))
else:
    import os, signal
    try:
        os.kill(pid, signal.SIGCONT)
    except OSError:
        pass
"""


def last_start(prefs: Path) -> str | None:
    """The ``_last_start`` line of Freeflight.prf as it is now, None where it is not written
    (yet): the file may be read in the middle of X-Plane's writing it, its top only."""
    try:
        with open(prefs, "rb") as f:
            head = f.read(8192).decode("utf-8", "replace")
    except OSError:
        return None
    return next(
        (line.strip() for line in head.splitlines() if line.startswith("_last_start ")), None
    )


def _start(line: str | None) -> dict | None:
    """The start of a ``_last_start`` line: ``{"airport_id": "LSGG", "runway": "22"}``."""
    import json

    try:
        start = next(iter(json.loads((line or "")[len("_last_start ") :]).values()))
    except (ValueError, StopIteration, AttributeError, TypeError):
        return None
    return start if isinstance(start, dict) else None


def start_key(line: str | None) -> str | None:
    """What the streamer's name for that start begins with (``orthostudio.live.gate``'s
    ``start_position``): its airport, or its coordinates."""
    start = _start(line)
    if start is None:
        return None
    if start.get("airport_id"):
        return str(start["airport_id"])
    with contextlib.suppress(KeyError, TypeError, ValueError):
        return f"{float(start['latitude']):.3f} {float(start['longitude']):.3f}"
    return None


def start_name(line: str | None) -> str:
    """The start's whole name as the streamer gives it (``LSGG runway 22``, ``LGAV parking B17``),
    from the line alone: shown at once, before the streamer has planned it."""
    from .gate import _spot

    start = _start(line)
    key = start_key(line) or ""
    return key + _spot(start) if start is not None and start.get("airport_id") else key


class Process:
    """X-Plane's process, paused and let go as a whole."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._handle = None
        if sys.platform == "win32":
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            k32.OpenProcess.restype = ctypes.c_void_p
            # PROCESS_SUSPEND_RESUME, SYNCHRONIZE (waited on to see it end)
            self._handle = k32.OpenProcess(0x0800 | 0x00100000, False, pid)
            if not self._handle:
                raise OSError(
                    f"X-Plane's process {pid} cannot be opened: {ctypes.get_last_error()}"
                )
            self._k32 = k32
            self._ntdll = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]

    def pause(self) -> None:
        if self._handle is not None:
            self._ntdll.NtSuspendProcess(ctypes.c_void_p(self._handle))
        else:
            os.kill(self.pid, signal.SIGSTOP)

    def go_on(self) -> None:
        with contextlib.suppress(OSError):
            if self._handle is not None:
                self._ntdll.NtResumeProcess(ctypes.c_void_p(self._handle))
            else:
                os.kill(self.pid, signal.SIGCONT)

    def ended(self) -> bool:
        if self._handle is not None:
            return self._k32.WaitForSingleObject(ctypes.c_void_p(self._handle), 0) == 0
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        return False

    def close(self) -> None:
        if self._handle is not None:
            self._k32.CloseHandle(ctypes.c_void_p(self._handle))
            self._handle = None


def start_guard(pid: int) -> subprocess.Popen:
    """The guard of X-Plane's process ``pid``: it lets X-Plane go the instant Live's end of its
    pipe closes, Live gone however it went (or :func:`stop_guard`)."""
    return subprocess.Popen(
        [sys.executable, "-I", "-c", GUARD, str(pid)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=sys.platform != "win32",  # not stopped along with Live's group
        creationflags=NO_CONSOLE_WINDOW,
    )


def stop_guard(guard: subprocess.Popen | None) -> None:
    if guard is None:
        return
    with contextlib.suppress(OSError):
        guard.stdin.close()  # type: ignore[union-attr]
    with contextlib.suppress(subprocess.TimeoutExpired):
        guard.wait(timeout=2)


class Writes:
    """Wakes at every write of a file: ``wait(timeout)`` is True when it was written since the last
    call (or replaced), False when the timeout went by. kqueue on a Mac, inotify on Linux; on
    Windows its size and time are read every :data:`POLL_S` through a handle that leaves X-Plane
    every right on the file (reading, writing, deleting)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None
        self._kq = None
        self._seen: tuple[int, int, int] | None = None
        self._inotify: int | None = None
        self._open()

    def _open(self) -> None:
        if sys.platform == "darwin":
            self._fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_EVTONLY", 0))
            self._kq = select.kqueue()
            flags = (
                select.KQ_NOTE_WRITE
                | select.KQ_NOTE_EXTEND
                | select.KQ_NOTE_ATTRIB
                | select.KQ_NOTE_DELETE
                | select.KQ_NOTE_RENAME
            )
            self._kq.control(
                [
                    select.kevent(
                        self._fd,
                        select.KQ_FILTER_VNODE,
                        select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                        flags,
                    )
                ],
                0,
                0,
            )
        elif sys.platform == "win32":
            self._fd = _shared_fd(self.path)
            self._seen = self._stamp()
        else:
            libc = ctypes.CDLL(None, use_errno=True)
            self._inotify = libc.inotify_init1(os.O_NONBLOCK)
            # IN_MODIFY, IN_ATTRIB, IN_CLOSE_WRITE, IN_MOVE_SELF, IN_DELETE_SELF
            if (
                libc.inotify_add_watch(
                    self._inotify, os.fsencode(self.path), 0x2 | 0x4 | 0x8 | 0x800 | 0x400
                )
                < 0
            ):
                raise OSError(ctypes.get_errno(), f"inotify cannot watch {self.path}")

    def _stamp(self) -> tuple[int, int, int]:
        st = os.fstat(self._fd)  # type: ignore[arg-type]
        return st.st_size, st.st_mtime_ns, st.st_ino

    def wait(self, timeout: float) -> bool:
        if sys.platform == "darwin":
            events = self._kq.control(None, 8, timeout)  # type: ignore[union-attr]
            if any(e.fflags & (select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME) for e in events):
                self._reopen()
            return bool(events)
        if sys.platform == "win32":
            end = time.monotonic() + timeout
            checked = 0.0
            while True:
                now = self._stamp()
                if now != self._seen:
                    self._seen = now
                    return True
                t = time.monotonic()
                if t - checked > 0.2:  # replaced by a new file rather than written over
                    checked = t
                    with contextlib.suppress(OSError):
                        if os.stat(self.path).st_ino != now[2]:
                            self._reopen()
                            return True
                if t >= end:
                    return False
                time.sleep(POLL_S)
        ready, _, _ = select.select([self._inotify], [], [], timeout)
        if not ready:
            return False
        data = b""
        with contextlib.suppress(BlockingIOError):
            data = os.read(self._inotify, 4096)  # type: ignore[arg-type]
        gone = False
        i = 0
        while i + 16 <= len(data):
            _, mask, _, size = struct.unpack_from("iIII", data, i)
            gone = gone or bool(mask & (0x800 | 0x400 | 0x8000))
            i += 16 + size
        if gone:
            self._reopen()
        return True

    def _reopen(self) -> None:
        self.close()
        for _ in range(200):  # X-Plane put a new file in its place: watch that one
            if self.path.exists():
                break
            time.sleep(0.005)
        with contextlib.suppress(OSError):
            self._open()

    def close(self) -> None:
        if self._kq is not None:
            self._kq.close()
            self._kq = None
        if self._fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._fd)
            self._fd = None
        if self._inotify is not None:
            with contextlib.suppress(OSError):
                os.close(self._inotify)
            self._inotify = None


def _shared_fd(path: Path) -> int:
    """Windows: ``path`` open for reading, sharing reading, writing and deleting."""
    import msvcrt

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    # GENERIC_READ; FILE_SHARE_READ | WRITE | DELETE; OPEN_EXISTING; FILE_ATTRIBUTE_NORMAL
    h = k32.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x80, None)
    if h in (None, ctypes.c_void_p(-1).value):
        raise OSError(f"{path} cannot be opened: {ctypes.get_last_error()}")
    return msvcrt.open_osfhandle(h, os.O_RDONLY)  # type: ignore[attr-defined]


class Pauser:
    """Pauses X-Plane at a new location chosen in flight (see the module's text).

    The streamer's loop says whether X-Plane flies (:attr:`flying`, its data fresh), reads
    :attr:`paused_for` (the ``_last_start`` X-Plane is paused for, None while it runs) and calls
    :meth:`resume` once everything X-Plane will read there is built. ``find_pid`` gives X-Plane's
    process, None while it does not run."""

    def __init__(self, prefs: Path, find_pid: Callable[[], int | None], log: Log = print) -> None:
        self.prefs = prefs
        self.find_pid = find_pid
        self.log = log
        self.flying = False
        self.paused_for: str | None = None
        self.paused_since = 0.0
        self.brief = 0  # pauses let go at once, since the last one kept
        self._lock = threading.Lock()
        self._process: Process | None = None
        self._guard: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="live-pause", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """X-Plane let go, the watch and the guard ended (Live stops)."""
        self._stop.set()
        self.resume()
        self._thread.join(timeout=3)
        self._detach()

    def resume(self) -> None:
        with self._lock:
            if self.paused_for is not None and self._process is not None:
                self._process.go_on()
            self.paused_for = None

    def _detach(self) -> None:
        stop_guard(self._guard)
        self._guard = None
        if self._process is not None:
            self._process.close()
            self._process = None

    def _run(self) -> None:
        while not self._stop.is_set():
            pid = self.find_pid()
            if pid is None or not self.prefs.exists():
                self._stop.wait(FIND_S)
                continue
            try:
                self._watch(pid)
            except OSError as e:
                self.log(f"  X-Plane cannot be paused at a new location chosen in flight: {e}")
                self._stop.wait(FIND_S * 10)
            finally:
                self.resume()
                self._detach()

    def _watch(self, pid: int) -> None:
        self._process = Process(pid)
        self._guard = start_guard(pid)
        writes = Writes(self.prefs)
        running = last_start(self.prefs)  # the start of the flight X-Plane has, or will load
        try:
            while not self._stop.is_set():
                if not writes.wait(1.0):
                    if self._process.ended():
                        return
                    continue
                if self.paused_for is not None:
                    continue
                if not self.flying:  # its main menu, a load: the plugin's; the start remembered
                    running = last_start(self.prefs) or running
                    continue
                with self._lock:
                    if self._stop.is_set():
                        return
                    self._process.pause()
                    start = last_start(self.prefs)
                    if start is None or start == running:
                        self._process.go_on()
                        self.brief += 1
                        continue
                    running = start
                    self.paused_for, self.paused_since = start, time.time()
                self.log(
                    f"X-Plane paused at the new location chosen in flight ({self.brief} brief "
                    f"pauses before): {start[len('_last_start ') :][:100]}"
                )
                self.brief = 0
        finally:
            writes.close()
