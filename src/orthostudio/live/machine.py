# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The computer Live runs on, and the share of it Live takes: X-Plane first. macOS, Linux and
Windows alike.

Live encodes with every core but two while X-Plane waits on a start, and while it flies with a
third of them at most, four at the very most: a Mac with 14 cores flew without a stutter with four
(2026-09-28), a computer with 8 gets two. An 8192 px texture takes 1.3 GB while it is encoded, so
the memory sets how many are encoded at once: one for each 12 GB, a computer of 16 GB one. ZL17
and ZL19, which make them, want 32 GB (:data:`SHARP_RAM_GB`): on the mountain test path X-Plane
and Live together took about 22 GB there, X-Plane alone up to 19 (a Mac, 2026-09-28, where the
textures share the memory; a PC's graphics card holds them apart, not measured). The window and
the log say so on a smaller computer; the choice stays the pilot's.

``OSXP_LIVE_MACHINE="8,16"`` makes Live take the share of a computer with 8 cores and 16 GB, to try
a smaller one's on a larger one: X-Plane keeps the whole computer, only Live's own share changes.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
from dataclasses import dataclass

MAX_FLIGHT_ENCODES = 4
MAX_SHARP_BUILDS = 4
GB_PER_SHARP_BUILD = 12.0
SHARP_RAM_GB = 32  # the memory ZL17 and ZL19 want
UNKNOWN_RAM_GB = 16.0  # a computer whose memory cannot be read: counted as a small one
WINDOWS = os.name == "nt"


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
        return _windows_memory_gb()
    return pages * size / 2**30 if pages > 0 and size > 0 else None


def _windows_memory_gb() -> float | None:
    """``GlobalMemoryStatusEx().ullTotalPhys``, GB; None anywhere else."""
    if not WINDOWS:
        return None

    class _Status(ctypes.Structure):
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

    status = _Status()
    status.dwLength = ctypes.sizeof(_Status)
    with contextlib.suppress(OSError, AttributeError):
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return status.ullTotalPhys / 2**30
    return None


def encoders(cores: int) -> int:
    """Encoding processes: every core but two, all of them at work while X-Plane waits."""
    return max(1, cores - 2)


def flight_encodes(cores: int) -> int:
    """Textures encoded at once while X-Plane flies: a third of the cores left to Live."""
    return max(1, min(MAX_FLIGHT_ENCODES, (cores - 2) // 3))


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
    (``--workers``), else every core but two."""
    cores, ram = machine()
    return Share(
        cores,
        ram,
        workers if workers else encoders(cores),
        flight_encodes(cores),
        sharp_builds(ram),
    )
