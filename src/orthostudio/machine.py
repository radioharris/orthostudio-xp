# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the machine has: its installed memory.

The scheduler's RAM budget reads it (``pipeline/build.py``), and so does the reader of one's own
relief files, which must know before it fills the memory whether a raster fits (``dem/raster.py``).
"""

from __future__ import annotations

import contextlib
import os

__all__ = ["physical_memory_mb"]


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


def _windows_memory_mb() -> int | None:
    """``GlobalMemoryStatusEx().ullTotalPhys``; ``None`` anywhere else."""
    if os.name != "nt":
        return None
    import ctypes

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
            return int(status.ullTotalPhys / 2**20)
    return None
