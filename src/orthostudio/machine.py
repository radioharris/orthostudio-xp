# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the machine has: its installed memory, what of it is free now, and what a process's
children hold.

The scheduler's RAM budget reads them (``pipeline/build.py``), and the reader of one's own relief
files reads the installed memory, which must know before it fills the memory whether a raster fits
(``dem/raster.py``). Each answer is ``None`` when the platform does not give it: the scheduler
then keeps to its share of the installed memory.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

__all__ = ["available_memory_mb", "children_memory_mb", "physical_memory_mb", "process_memory_mb"]


def physical_memory_mb() -> int | None:
    """Installed RAM in MB, or ``None`` when the platform does not say.

    ``os.sysconf`` does not exist on Windows, where the scheduler then ran with **no** RAM
    budget at all and every ``ram_mb`` the native nodes declare became inert (review 4,
    portability finding); ``GlobalMemoryStatusEx`` is the answer there, and a build with no
    budget at all now says so.
    """
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return _windows_memory_mb()
    if pages <= 0 or page <= 0:
        return None
    return int(pages * page / 2**20)


class _Status(ctypes.Structure):
    """``MEMORYSTATUSEX``."""

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


def _windows_status() -> _Status | None:
    """``GlobalMemoryStatusEx``; ``None`` anywhere else."""
    if os.name != "nt":
        return None
    status = _Status()
    status.dwLength = ctypes.sizeof(_Status)
    with contextlib.suppress(OSError, AttributeError):
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return status
    return None


def _windows_memory_mb() -> int | None:
    """``GlobalMemoryStatusEx().ullTotalPhys``; ``None`` anywhere else."""
    status = _windows_status()
    return None if status is None else int(status.ullTotalPhys / 2**20)


def available_memory_mb() -> int | None:
    """MB the system can still give without writing memory to its disk, ``None`` when it does not
    say: what macOS judges its memory pressure by (``kern.memorystatus_level``, the free
    percentage ``memory_pressure`` prints, of the installed memory), ``ullAvailPhys`` on Windows,
    ``MemAvailable`` on Linux. The file cache the system drops when asked counts as free on all
    three."""
    if sys.platform == "darwin":
        level = ctypes.c_int()
        size = ctypes.c_size_t(ctypes.sizeof(level))
        with contextlib.suppress(OSError, AttributeError):
            libc = ctypes.CDLL(None)
            name = b"kern.memorystatus_level"
            if libc.sysctlbyname(name, ctypes.byref(level), ctypes.byref(size), None, 0) == 0:
                mem = physical_memory_mb()
                if mem is not None and 0 <= level.value <= 100:
                    return mem * level.value // 100
        return None
    if os.name == "nt":
        status = _windows_status()
        return None if status is None else int(status.ullAvailPhys / 2**20)
    with contextlib.suppress(OSError, ValueError):
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return None


class _RusageV2(ctypes.Structure):
    """macOS ``rusage_info_v2``, up to ``ri_phys_footprint`` and what follows it."""

    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64)
        for name in (
            "ri_user_time",
            "ri_system_time",
            "ri_pkg_idle_wkups",
            "ri_interrupt_wkups",
            "ri_pageins",
            "ri_wired_size",
            "ri_resident_size",
            "ri_phys_footprint",
            "ri_proc_start_abstime",
            "ri_proc_exit_abstime",
            "ri_child_user_time",
            "ri_child_system_time",
            "ri_child_pkg_idle_wkups",
            "ri_child_interrupt_wkups",
            "ri_child_pageins",
            "ri_child_elapsed_abstime",
            "ri_diskio_bytesread",
            "ri_diskio_byteswritten",
        )
    ]


class _MemoryCounters(ctypes.Structure):
    """Windows ``PROCESS_MEMORY_COUNTERS_EX``."""

    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _ProcessEntry(ctypes.Structure):
    """Windows ``PROCESSENTRY32W``."""

    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def process_memory_mb(pid: int) -> int | None:
    """MB process ``pid`` holds, as each system counts it for the memory a step reserves: its
    footprint on macOS (what Activity Monitor shows), its private bytes on Windows (what Task
    Manager shows), its resident size on Linux; ``None`` when it cannot be read (gone, not ours)."""
    if sys.platform == "darwin":
        info = _RusageV2()
        with contextlib.suppress(OSError, AttributeError):
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
            if libproc.proc_pid_rusage(pid, 2, ctypes.byref(info)) == 0:  # RUSAGE_INFO_V2
                return int(info.ri_phys_footprint / 2**20)
        return None
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1010, False, pid)  # QUERY_LIMITED_INFORMATION, VM_READ
        if not handle:
            return None
        try:
            counters = _MemoryCounters()
            counters.cb = ctypes.sizeof(_MemoryCounters)
            if kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return int(counters.PrivateUsage / 2**20)
            return None
        finally:
            kernel32.CloseHandle(handle)
    with contextlib.suppress(OSError, ValueError, IndexError):
        resident = int(Path(f"/proc/{pid}/statm").read_text().split()[1])
        return int(resident * os.sysconf("SC_PAGE_SIZE") / 2**20)
    return None


def _windows_started(pid: int) -> int | None:
    """When process ``pid`` started (``GetProcessTimes``), ``None`` when it cannot be read."""
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        times = [ctypes.c_ulonglong() for _ in range(4)]  # creation, exit, kernel, user
        if kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return times[0].value
        return None
    finally:
        kernel32.CloseHandle(handle)


def _descendants(
    pid: int,
    parents: Mapping[int, list[int]],
    started: Callable[[int], int | None] | None = None,
) -> list[int]:
    """The processes under ``pid`` in ``parents`` (a parent's children by its number), theirs
    too. With ``started``, a child that started before its parent is left out: Windows keeps the
    number of a parent that ended, which a later process may take, where macOS and Linux give its
    children to another. explorer.exe's parent ends at logon; the engine given its number counted
    X-Plane and every app started from the Start menu as the batch's own (review, 2026-10-29)."""
    found: list[int] = []
    todo = [pid]
    while todo:
        parent = todo.pop()
        kids = [p for p in parents.get(parent, []) if p != pid and p not in found]
        if started is not None:
            born = started(parent)
            kids = [p for p in kids if born is not None and (started(p) or -1) >= born]
        found += kids
        todo += kids
    return found


def _children(pid: int) -> list[int]:
    """The processes ``pid`` started, and theirs, down to the last."""
    if sys.platform == "darwin":
        found: list[int] = []
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        todo = [pid]
        while todo:
            buf = (ctypes.c_int * 1024)()
            n = libproc.proc_listchildpids(todo.pop(), buf, ctypes.sizeof(buf))
            kids = [p for p in buf[: max(0, n)] if p > 0]
            found += kids
            todo += kids
        return found
    parents: dict[int, list[int]] = {}
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snap or snap == ctypes.c_void_p(-1).value:
            raise OSError("no process snapshot")
        try:
            entry = _ProcessEntry()
            entry.dwSize = ctypes.sizeof(_ProcessEntry)
            more = kernel32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(entry))
            while more:
                parents.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
                more = kernel32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(snap))
        return _descendants(pid, parents, _windows_started)
    for stat in Path("/proc").glob("[0-9]*/stat"):
        with contextlib.suppress(OSError, ValueError, IndexError):
            # the name between parentheses may hold spaces: the parent comes after it
            fields = stat.read_text().rsplit(")", 1)[1].split()
            parents.setdefault(int(fields[1]), []).append(int(stat.parent.name))
    return _descendants(pid, parents)


def children_memory_mb(pid: int | None = None) -> int | None:
    """MB the processes ``pid`` (this one by default) started hold together, theirs included, as
    :func:`process_memory_mb` counts them; ``None`` when the system does not list them. A child
    that ends while it is read counts nothing."""
    try:
        kids = _children(os.getpid() if pid is None else pid)
    except (OSError, AttributeError):
        return None
    return sum(process_memory_mb(p) or 0 for p in kids)
