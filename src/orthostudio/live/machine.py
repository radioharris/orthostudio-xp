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
from collections.abc import Iterator
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
    that the system runs or has ready to run; None where that cannot be read (Windows for now, or
    the process gone). Read from outside, as Activity Monitor does: nothing attached to X-Plane,
    no privilege needed."""
    with contextlib.suppress(OSError, AttributeError, TypeError, ValueError):
        if MACOS:
            return _darwin_pool(pid)
        if sys.platform.startswith("linux"):
            return _linux_pool(pid)
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


def encoders(cores: int, ram_gb: float | None = None) -> int:
    """Encoding processes: every core but two, all of them at work while X-Plane waits; one for
    each 3 GB of memory at most, two at the least."""
    n = max(1, cores - 2)
    if ram_gb is not None:
        n = min(n, max(2, int(ram_gb // GB_PER_ENCODER)))
    return n


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
    (``--workers``), else as its cores and memory allow (:func:`encoders`)."""
    cores, ram = machine()
    return Share(
        cores,
        ram,
        workers if workers else encoders(cores, ram),
        flight_encodes(cores, ram),
        sharp_builds(ram),
    )
