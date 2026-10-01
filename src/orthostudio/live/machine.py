# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The computer Live runs on, and the share of it Live takes: X-Plane first. macOS, Linux and
Windows alike.

Live encodes with every core but two while X-Plane waits on a start, and while it flies with a
third of them at most, four at the very most: a Mac with 14 cores flew without a stutter with four
(2026-09-28), a computer with 8 gets two. The memory bounds both: an encoder takes up to 0.36 GB
at a 4096 px texture (twelve of them 4.3 GB at a start, 2026-09-28), so one for each 3 GB, and in
flight one at once for each 8 GB (a computer of 16 GB: five encoders, two at once in flight).
An 8192 px texture takes 1.3 GB while it is encoded: one at once for each 12 GB. ZL17 and ZL19,
which make them, want 32 GB (:data:`SHARP_RAM_GB`): on the mountain test path X-Plane and Live
together took about 22 GB there, X-Plane alone up to 19 (a Mac, 2026-09-28, where the textures
share the memory; a PC's graphics card holds them apart, not measured). The window and the log
say so on a smaller computer; the choice stays the pilot's.

In flight, the system's own word on its memory (:func:`memory_pressure`) holds Live back: short
of it, one texture at once and no 8192 px one ahead of time; very short, only what X-Plane reads
now. On a simulated 16 GB computer Live's bursts (1.5 GB and 5 cores in 2 s) had macOS compress
and swap while X-Plane stalled, up to 370 ms (2026-09-28).

``OSXP_LIVE_MACHINE="8,16"`` makes Live take the share of a computer with 8 cores and 16 GB, to try
a smaller one's on a larger one: X-Plane keeps the whole computer, only Live's own share changes.

On a hot Mac in flight, X-Plane loads a column of scenery only once its 7 background workers have
done what they were doing, placing trees above all; and the Mac gives X-Plane and Live together
about 5 to 7 cores (2026-10-01). Live's encoders, niced, still took 0.4 to 1.2 core from those
workers for each core they used: with Live at work X-Plane's workers were all busy before its
loads (6 to 6.9 of 7 running), with Live idle at the same heat 3.1 to 4.5, and its tiles came
minutes late or in seconds. So in flight Live watches X-Plane's workers (:func:`xplane_pool`) and
stops all but what X-Plane shows while they are all busy (orthostudio.live.streamer). Encoders in
the system's background band, on the efficiency cores, did not help: they only took longer there.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import os
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

MAX_FLIGHT_ENCODES = 4
MAX_SHARP_BUILDS = 4
GB_PER_ENCODER = 3.0  # an encoder's 0.36 GB at a 4096 px texture, X-Plane keeping the rest
GB_PER_FLIGHT_ENCODE = 8.0
GB_PER_SHARP_BUILD = 12.0
SHARP_RAM_GB = 32  # the memory ZL17 and ZL19 want
UNKNOWN_RAM_GB = 16.0  # a computer whose memory cannot be read: counted as a small one
WINDOWS = os.name == "nt"
MACOS = sys.platform == "darwin"
MEMINFO = "/proc/meminfo"  # Linux
NORMAL, SHORT, VERY_SHORT = 0, 1, 2  # memory_pressure()


def machine() -> tuple[int, float]:
    """(cores, GB of memory) of this computer, or those ``OSXP_LIVE_MACHINE`` says."""
    said = os.environ.get("OSXP_LIVE_MACHINE", "")
    if said:
        with contextlib.suppress(ValueError):
            said_cores, said_ram = said.split(",")
            return max(1, int(said_cores)), max(1.0, float(said_ram))
    # the cores this process may use (Linux's affinity, from Python 3.13), else all of them
    cores: int = getattr(os, "process_cpu_count", os.cpu_count)() or 4
    return cores, memory_gb() or UNKNOWN_RAM_GB


def memory_gb() -> float | None:
    """The memory installed, GB, or None where the system does not say. OrthoStudio XP's own
    reading (``pipeline.build.physical_memory_mb``) is not imported: its module loads PROJ, which
    must not be open in Live's process before its fork server starts (``pools``)."""
    try:
        pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):  # Windows has no sysconf
        status = _windows_memory_status()
        return None if status is None else status.ullTotalPhys / 2**30
    return pages * size / 2**30 if pages > 0 and size > 0 else None


class _MemoryStatus(ctypes.Structure):
    """Windows's MEMORYSTATUSEX."""

    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _windows_memory_status() -> _MemoryStatus | None:
    """``GlobalMemoryStatusEx()``; None anywhere else."""
    if not WINDOWS:
        return None
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    with contextlib.suppress(OSError, AttributeError):
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return status
    return None


def memory_pressure() -> int:
    """How short of memory this computer is now, as its system says: NORMAL, SHORT or VERY_SHORT.
    macOS: its own pressure level (``kern.memorystatus_vm_pressure_level``: warning, critical);
    Linux: the memory available against the total (under 10 %, under 5 %); Windows: the memory
    load (90 %, 95 %). NORMAL where it cannot be read. No process started: Live forks nothing
    while X-Plane runs."""
    try:
        if MACOS:
            level = _darwin_pressure_level()
            return {2: SHORT, 4: VERY_SHORT}.get(level, NORMAL)
        if WINDOWS:
            status = _windows_memory_status()
            if status is None:
                return NORMAL
            load = status.dwMemoryLoad
            return VERY_SHORT if load >= 95 else SHORT if load >= 90 else NORMAL
        return _linux_pressure()
    except (OSError, ValueError, AttributeError):
        return NORMAL


def _darwin_pressure_level() -> int:
    """macOS's memory pressure level: 1 normal, 2 warning, 4 critical (0 unread)."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    value = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(value))
    name = b"kern.memorystatus_vm_pressure_level"
    if libc.sysctlbyname(name, ctypes.byref(value), ctypes.byref(size), None, ctypes.c_size_t(0)):
        return 0
    return value.value


def _linux_pressure() -> int:
    """From ``/proc/meminfo``: MemAvailable against MemTotal."""
    kb: dict[str, int] = {}
    with open(MEMINFO) as f:
        for line in f:
            name, _, rest = line.partition(":")
            if name in ("MemTotal", "MemAvailable"):
                kb[name] = int(rest.split()[0])
    if not kb.get("MemTotal") or "MemAvailable" not in kb:
        return NORMAL
    share = kb["MemAvailable"] / kb["MemTotal"]
    return VERY_SHORT if share < 0.05 else SHORT if share < 0.10 else NORMAL


@contextlib.contextmanager
def background_io() -> Iterator[None]:
    """This thread's disk work in the system's background mode while inside: long file work (the
    pack's one-time setup, 700,000 files) leaves every other program the disk. macOS: the thread's
    I/O policy throttled; Linux: the idle I/O class; Windows: the thread's background mode. Nothing
    where the system says no. On macOS the setup froze other programs twice (2026-09-28)."""
    undo = None
    with contextlib.suppress(OSError, AttributeError, TypeError, ValueError):
        undo = _enter_background_io()
    try:
        yield
    finally:
        if undo is not None:
            with contextlib.suppress(OSError, AttributeError, TypeError, ValueError):
                undo()


def _enter_background_io():
    """Put this thread's disk work in the background; return what undoes it, or None."""
    if MACOS:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        disk, thread, throttle = 0, 1, 3  # IOPOL_TYPE_DISK, IOPOL_SCOPE_THREAD, IOPOL_THROTTLE
        before = libc.getiopolicy_np(disk, thread)
        if before < 0 or libc.setiopolicy_np(disk, thread, throttle):
            return None
        return lambda: libc.setiopolicy_np(disk, thread, before)
    if WINDOWS:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        begin, end = 0x00010000, 0x00020000  # THREAD_MODE_BACKGROUND_BEGIN / _END
        me = kernel32.GetCurrentThread()
        if not kernel32.SetThreadPriority(me, begin):
            return None
        return lambda: kernel32.SetThreadPriority(me, end)
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        # ioprio_set(IOPRIO_WHO_PROCESS, this thread, IOPRIO_CLASS_IDLE); its number per machine
        number = {"x86_64": 251, "aarch64": 30, "arm64": 30}.get(os.uname().machine)
        if number is None:
            return None
        tid = threading.get_native_id()
        before = libc.syscall(number + 1, 1, tid)  # ioprio_get, next to ioprio_set
        if before < 0 or libc.syscall(number, 1, tid, 3 << 13):
            return None
        return lambda: libc.syscall(number, 1, tid, before)
    return None


# X-Plane 12's background workers, as it names its threads (Linux keeps 15 characters of a name)
POOL_NAME = "Pool worker (background"
LINUX_NAME = 15


class _ThreadInfo(ctypes.Structure):  # struct proc_threadinfo (<sys/proc_info.h>)
    _fields_ = [
        ("user_ns", ctypes.c_uint64),
        ("system_ns", ctypes.c_uint64),
        ("cpu_usage", ctypes.c_int32),
        ("policy", ctypes.c_int32),
        ("run_state", ctypes.c_int32),
        ("flags", ctypes.c_int32),
        ("sleep_time", ctypes.c_int32),
        ("curpri", ctypes.c_int32),
        ("priority", ctypes.c_int32),
        ("maxpriority", ctypes.c_int32),
        ("name", ctypes.c_char * 64),
    ]


def xplane_pool(pid: int) -> tuple[int, int] | None:
    """(running, all) of X-Plane's background workers now: those of its threads named POOL_NAME
    that the system runs or has ready to run; None where that cannot be read (the process gone).
    Read from outside, as Activity Monitor or the Task Manager do: nothing attached to X-Plane,
    no privilege needed. Where X-Plane names no thread so (all 0), Live goes without."""
    with contextlib.suppress(OSError, AttributeError, TypeError, ValueError):
        if MACOS:
            return _darwin_pool(pid)
        if sys.platform.startswith("linux"):
            return _linux_pool(pid)
        if WINDOWS:
            return _windows_pool(pid)
    return None


def _darwin_pool(pid: int) -> tuple[int, int] | None:
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    listed, info_of = 6, 5  # PROC_PIDLISTTHREADS, PROC_PIDTHREADINFO
    handles = (ctypes.c_uint64 * 4096)()
    size = libproc.proc_pidinfo(pid, listed, ctypes.c_uint64(0), handles, ctypes.sizeof(handles))
    if size <= 0:
        return None
    running = total = 0
    info = _ThreadInfo()
    for handle in handles[: size // 8]:
        got = libproc.proc_pidinfo(
            pid, info_of, ctypes.c_uint64(handle), ctypes.byref(info), ctypes.sizeof(info)
        )
        if got == ctypes.sizeof(info) and info.name.decode(errors="replace").startswith(POOL_NAME):
            total += 1
            running += info.run_state == 1  # TH_STATE_RUNNING: on a core or ready for one
    return running, total


def _linux_pool(pid: int, proc: Path = Path("/proc")) -> tuple[int, int] | None:
    tasks = proc / str(pid) / "task"
    if not tasks.is_dir():
        return None
    running = total = 0
    for task in tasks.iterdir():
        with contextlib.suppress(OSError):
            if (task / "comm").read_text().strip() != POOL_NAME[:LINUX_NAME]:
                continue
            stat = (task / "stat").read_text()
            total += 1
            running += stat[stat.rindex(")") + 2 :].startswith("R")  # the state after the name
    return running, total


# Windows: the system's list of processes and their threads (ntdll's NtQuerySystemInformation,
# SystemProcessInformation), 64-bit: each SYSTEM_PROCESS_INFORMATION of 0x100 bytes followed by its
# threads' SYSTEM_THREAD_INFORMATION of 0x50; a thread's name from GetThreadDescription (Windows
# 10 1607 and later). Not tried on a Windows with X-Plane (2026-10-01): where X-Plane names no
# thread "Pool worker (background ...)" there, the reading finds none and Live goes without.
_PROCESS_SIZE, _THREAD_SIZE = 0x100, 0x50
_RUNNABLE = {1, 2, 3, 7}  # KTHREAD_STATE: Ready, Running, Standby, DeferredReady
_names: dict[tuple[int, int], str] = {}  # (process, thread): its name, read once


def _windows_snapshot() -> ctypes.Array | None:
    """The system's list of processes and their threads, as it gives it; None if it does not."""
    ntdll = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
    ntdll.NtQuerySystemInformation.restype = ctypes.c_uint32
    size = 1 << 20
    for _ in range(8):
        buffer = ctypes.create_string_buffer(size)
        needed = ctypes.c_uint32(0)
        status = ntdll.NtQuerySystemInformation(5, buffer, size, ctypes.byref(needed))
        if status == 0:
            return buffer
        if status != 0xC0000004:  # STATUS_INFO_LENGTH_MISMATCH: a larger buffer
            return None
        size = max(2 * size, needed.value + (1 << 16))
    return None


def _windows_blocks(buffer: ctypes.Array) -> Iterator[tuple[int, int, str]]:
    """(address, process, image name) of each SYSTEM_PROCESS_INFORMATION in a
    SystemProcessInformation buffer (:func:`_windows_snapshot`)."""
    # Windows' ULONG is 32 bits, where this Python's c_ulong may be 64 (off Windows)
    base, end, offset = ctypes.addressof(buffer), ctypes.sizeof(buffer), 0
    while offset + _PROCESS_SIZE <= end:
        at = base + offset
        length = ctypes.c_ushort.from_address(at + 0x38).value
        text = ctypes.c_void_p.from_address(at + 0x40).value
        # UTF-16 as Windows writes it, whatever this Python's wchar_t (4 bytes off Windows)
        name = ctypes.string_at(text, length).decode("utf-16-le", "replace") if text else ""
        yield at, ctypes.c_void_p.from_address(at + 0x50).value or 0, name
        step = ctypes.c_uint32.from_address(at).value
        if not step:
            return
        offset += step


def windows_processes(buffer: ctypes.Array) -> Iterator[tuple[int, str, list[tuple[int, int]]]]:
    """(process, image name, [(thread, state)]) of each process in a SystemProcessInformation
    buffer (:func:`_windows_snapshot`)."""
    end = ctypes.addressof(buffer) + ctypes.sizeof(buffer)
    for at, pid, name in _windows_blocks(buffer):
        threads = []
        for k in range(ctypes.c_uint32.from_address(at + 4).value):
            t = at + _PROCESS_SIZE + k * _THREAD_SIZE
            if t + _THREAD_SIZE > end:
                break
            tid = ctypes.c_void_p.from_address(t + 0x30).value or 0
            threads.append((tid, ctypes.c_uint32.from_address(t + 0x44).value))
        yield pid, name, threads


_FILETIME_UNIX = 11_644_473_600  # seconds from 1601, Windows' clock's start, to 1970


def windows_starts(buffer: ctypes.Array) -> dict[int, tuple[str, float]]:
    """(image name, start as Unix time) of each process in a SystemProcessInformation buffer:
    its CreateTime, in 100 ns since 1601 (0 for the system's own first ones)."""
    out = {}
    for at, pid, name in _windows_blocks(buffer):
        created = ctypes.c_int64.from_address(at + 0x20).value
        out[pid] = (name, created / 1e7 - _FILETIME_UNIX if created > 0 else 0.0)
    return out


def windows_process_starts() -> dict[int, tuple[str, float]] | None:
    """Each process running on Windows, its name and start, from the system's own list; None
    where it does not give it."""
    buffer = _windows_snapshot()
    return None if buffer is None else windows_starts(buffer)


def _windows_thread_name(pid: int, tid: int) -> str:
    """A thread's name (GetThreadDescription), read once for each; "" where it has none."""
    if (pid, tid) in _names:
        return _names[(pid, tid)]
    kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
    describe = getattr(kernel32, "GetThreadDescription", None)
    name = ""
    if describe is not None:
        kernel32.OpenThread.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        describe.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        handle = kernel32.OpenThread(0x0800, False, tid)  # THREAD_QUERY_LIMITED_INFORMATION
        if handle:
            try:
                text = ctypes.c_void_p()
                if describe(handle, ctypes.byref(text)) >= 0 and text.value:
                    name = ctypes.wstring_at(text.value)
                    kernel32.LocalFree(text)
            finally:
                kernel32.CloseHandle(handle)
    _names[(pid, tid)] = name
    return name


def windows_pool_in(
    buffer: ctypes.Array, pid: int, name_of: Callable[[int, int], str]
) -> tuple[int, int] | None:
    """(running, all) of the threads named POOL_NAME of process ``pid`` in a
    SystemProcessInformation buffer, their names from ``name_of(process, thread)``; None where
    the process is not."""
    for p, _, threads in windows_processes(buffer):
        if p == pid:
            pool = [state for tid, state in threads if name_of(pid, tid).startswith(POOL_NAME)]
            return sum(state in _RUNNABLE for state in pool), len(pool)
    return None


def _windows_pool(pid: int) -> tuple[int, int] | None:
    buffer = _windows_snapshot()
    if buffer is None:
        return None
    if any(p != pid for p, _ in _names):  # another X-Plane: the names read are its old ones
        _names.clear()
    return windows_pool_in(buffer, pid, _windows_thread_name)


WINDOWS_POOL_MAX = 61  # the processes a pool may have on Windows (Python's ProcessPoolExecutor)


def encoders(cores: int, ram_gb: float | None = None) -> int:
    """Encoding processes: every core but two, all of them at work while X-Plane waits; one for
    each 3 GB of memory at most, two at the least; 61 at most on Windows."""
    n = max(1, cores - 2)
    if ram_gb is not None:
        n = min(n, max(2, int(ram_gb // GB_PER_ENCODER)))
    return min(n, WINDOWS_POOL_MAX) if WINDOWS else n


def flight_encodes(cores: int, ram_gb: float | None = None) -> int:
    """Textures encoded at once while X-Plane flies: a third of the cores left to Live, four at
    most, and one for each 8 GB of memory."""
    n = max(1, min(MAX_FLIGHT_ENCODES, (cores - 2) // 3))
    if ram_gb is not None:
        n = min(n, max(1, int(ram_gb // GB_PER_FLIGHT_ENCODE)))
    return n


def sharp_builds(ram_gb: float) -> int:
    """8192 px textures encoded at once: one for each 12 GB of memory."""
    return max(1, min(MAX_SHARP_BUILDS, int(ram_gb // GB_PER_SHARP_BUILD)))


def short_for_sharp(ram_gb: float) -> bool:
    """Whether this computer has less memory than ZL17 and ZL19 want."""
    return ram_gb < SHARP_RAM_GB


def sharp_zooms(max_zoom: int, max_zoom_airports: int) -> bool:
    """Whether the zooms make 8192 px textures: one above the mesh's, ZL16 away from airports and
    ZL18 around them (the window's ``ZOOMS``)."""
    return max_zoom > 16 or max_zoom_airports > 18


@dataclass(frozen=True)
class Share:
    """What Live takes of a computer: its encoding processes, the textures they encode at once
    while X-Plane flies, and the 8192 px ones at once."""

    cores: int
    ram_gb: float
    encoders: int
    flight_encodes: int
    sharp_builds: int

    def summary(self) -> str:
        """The share, as Live's log says it."""
        return (
            f"this computer: {self.cores} cores, {self.ram_gb:.0f} GB: {self.encoders} encoders, "
            f"{self.flight_encodes} textures at once in flight, "
            f"{self.sharp_builds} at 8192 px at once"
        )

    def notes(self, max_zoom: int, max_zoom_airports: int) -> list[str]:
        """What Live's log says of it as a stream starts: the share, and a warning when the zooms
        want more memory than this computer has."""
        out = [self.summary()]
        if sharp_zooms(max_zoom, max_zoom_airports) and short_for_sharp(self.ram_gb):
            out.append(
                f"this computer has {self.ram_gb:.0f} GB of memory, ZL17 and ZL19 want "
                f"{SHARP_RAM_GB}: X-Plane may stutter or run short of memory, ZL16 and ZL18 suit "
                f"it better"
            )
        return out


def share(workers: int | None = None) -> Share:
    """This computer's share for Live; ``workers`` encoding processes when they are set
    (``--workers``, 61 at most on Windows), else as its cores and memory allow
    (:func:`encoders`)."""
    cores, ram = machine()
    if workers and WINDOWS:
        workers = min(workers, WINDOWS_POOL_MAX)
    return Share(
        cores,
        ram,
        workers if workers else encoders(cores, ram),
        flight_encodes(cores, ram),
        sharp_builds(ram),
    )
