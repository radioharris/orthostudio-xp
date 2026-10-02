# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What ``sampler.py`` reads on a Mac, read on Windows (a user's Shadow PC, 2026-10-01), from
outside as the Task Manager does, nothing attached to X-Plane, no privilege needed:

- every process from the system's own list (ntdll's NtQuerySystemInformation, the one Live reads
  X-Plane's workers from: ``orthostudio.live.machine``): its parent, processor time, memory in
  use (its working set) and its own (private), the bytes it read and wrote, its hard page faults
  (Windows' page-ins), and its threads: processor time, state, priority, and their names
  (GetThreadDescription);
- the memory: free (100 % less Windows' memory load), the pressure as Live reckons it on Windows
  (``machine.memory_pressure``: 90 % used, 95 %), what Windows holds compressed (the "Memory
  Compression" process's working set) and what the page files hold;
- the bytes received by the computer's own network adapters (GetIfTable2), and the bytes read and
  written on each fixed volume since it was mounted (IOCTL_DISK_PERFORMANCE: "C:", "D:"), which
  names the disks as ``whole_disk`` does.

No heat and no allowed cores: Windows tells neither (None, the report says nothing of them).
Thread states are written as a Mac's are (``sampler.THREAD_STATES``), so that the report reads both:
running for those on a processor or ready for one, waiting for those waiting."""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wt
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

PROCESS_SIZE, THREAD_SIZE = (
    0x100,
    0x50,
)  # SYSTEM_PROCESS_INFORMATION, SYSTEM_THREAD_INFORMATION (64-bit)
TICKS = 1e7  # Windows' times: 100 ns
RUNNING_STATES = {1, 2, 3, 7}  # Ready, Running, Standby, DeferredReady: a Mac's "running"
WAITING_STATE = 5
MAC_RUNNING, MAC_WAITING, MAC_HALTED = 1, 3, 5  # sampler.THREAD_STATES
IF_ROW_SIZE, IF_IN_OCTETS, IF_FLAGS, IF_TYPE = 1352, 1208, 1152, 1128  # MIB_IF_ROW2 (64-bit)
LOOPBACK = 24  # IF_TYPE_SOFTWARE_LOOPBACK
IOCTL_DISK_PERFORMANCE = 0x00070020
DRIVE_FIXED = 3


@dataclass
class Thread:
    tid: int
    cpu_s: float
    state: int  # Windows' own (KTHREAD_STATE)
    priority: int


@dataclass
class Process:
    pid: int
    parent: int
    name: str
    cpu_s: float
    working_set: int  # bytes in memory now
    private: int  # bytes its own (committed): the closest to a Mac's footprint
    read: int  # bytes read and written through its I/O, since it started
    written: int
    hard_faults: int  # pages it had to read back from the disk
    threads: list[Thread] = field(default_factory=list)


def _u32(at: int) -> int:
    return ctypes.c_uint32.from_address(at).value


def _u64(at: int) -> int:
    return ctypes.c_uint64.from_address(at).value


def parse(buffer: ctypes.Array) -> list[Process]:
    """Every process of a SystemProcessInformation buffer (``machine._windows_snapshot``)."""
    base, end, offset, out = ctypes.addressof(buffer), ctypes.sizeof(buffer), 0, []
    while offset + PROCESS_SIZE <= end:
        at = base + offset
        length = ctypes.c_ushort.from_address(at + 0x38).value
        text = ctypes.c_void_p.from_address(at + 0x40).value
        name = ctypes.string_at(text, length).decode("utf-16-le", "replace") if text else ""
        p = Process(
            pid=_u64(at + 0x50),
            parent=_u64(at + 0x58),
            name=name,
            cpu_s=(_u64(at + 0x28) + _u64(at + 0x30)) / TICKS,  # user and kernel
            working_set=_u64(at + 0x90),
            private=_u64(at + 0xC8),
            read=_u64(at + 0xE8),
            written=_u64(at + 0xF0),
            hard_faults=_u32(at + 0x10),
        )
        for k in range(_u32(at + 4)):
            t = at + PROCESS_SIZE + k * THREAD_SIZE
            if t + THREAD_SIZE > base + end:
                break
            p.threads.append(
                Thread(
                    tid=_u64(t + 0x30),
                    cpu_s=(_u64(t) + _u64(t + 8)) / TICKS,
                    state=_u32(t + 0x44),
                    priority=ctypes.c_int32.from_address(t + 0x38).value,
                )
            )
        out.append(p)
        step = _u32(at)
        if not step:
            break
        offset += step
    return out


def snapshot() -> list[Process]:
    from orthostudio.live import machine

    buffer = machine._windows_snapshot()
    return parse(buffer) if buffer is not None else []


def mac_state(state: int) -> int:
    """A Windows thread's state as a Mac's (sampler.THREAD_STATES)."""
    if state in RUNNING_STATES:
        return MAC_RUNNING
    return MAC_WAITING if state == WAITING_STATE else MAC_HALTED


def processes(rows: list[Process] | None = None) -> list[tuple[int, int, float, float, str]]:
    """(pid, parent, memory MB, CPU s, name) of every process, as ``sampler.processes``."""
    rows = snapshot() if rows is None else rows
    return [(p.pid, p.parent, p.working_set / 2**20, p.cpu_s, p.name) for p in rows if p.pid]


def threads(pid: int, rows: list[Process] | None = None) -> list[tuple[int, str, float, int, int]]:
    """(thread, name, processor s, state as a Mac's, priority) of a process's threads."""
    from orthostudio.live import machine

    for p in snapshot() if rows is None else rows:
        if p.pid == pid:
            return [
                (
                    t.tid,
                    machine._windows_thread_name(pid, t.tid),
                    t.cpu_s,
                    mac_state(t.state),
                    t.priority,
                )
                for t in p.threads
            ]
    return []


def usage(pid: int, rows: list[Process] | None = None) -> dict[str, int]:
    """As ``sampler.usage``: its own memory, the bytes it read and wrote, its hard page faults."""
    for p in snapshot() if rows is None else rows:
        if p.pid == pid:
            return {
                "footprint": p.private,
                "read": p.read,
                "written": p.written,
                "pageins": p.hard_faults,
            }
    return {}


def _page_files() -> int | None:
    """Bytes the page files hold now (SystemPageFileInformation, in pages)."""
    ntdll = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
    ntdll.NtQuerySystemInformation.restype = ctypes.c_uint32
    buffer = ctypes.create_string_buffer(4096)
    needed = ctypes.c_uint32(0)
    if ntdll.NtQuerySystemInformation(18, buffer, len(buffer), ctypes.byref(needed)) != 0:
        return None
    used, offset = 0, 0
    while offset + 32 <= needed.value:
        step, _total, in_use = struct.unpack_from("<III", buffer, offset)
        used += in_use
        if not step:
            break
        offset += step
    return used * 4096


def memory(rows: list[Process] | None = None) -> dict[str, int | None]:
    """As ``sampler.memory``: free %, the pressure (1 normal, 2 warning, 4 critical), MB compressed,
    MB in the page files."""
    from orthostudio.live import machine

    status = machine._windows_memory_status()
    load = status.dwMemoryLoad if status is not None else None
    compressed = [
        p.working_set
        for p in (snapshot() if rows is None else rows)
        if p.name == "Memory Compression"
    ]
    swapped = None
    with contextlib.suppress(OSError, AttributeError):
        swapped = _page_files()
    return {
        "free_pct": None if load is None else 100 - load,
        "pressure": None if load is None else 4 if load >= 95 else 2 if load >= 90 else 1,
        "compressed_mb": compressed[0] // 2**20 if compressed else None,
        "swapped_mb": None if swapped is None else swapped // 2**20,
    }


def net_in() -> int:
    """Bytes received by the computer's own network adapters since they came up (GetIfTable2):
    hardware interfaces only, not their filters, which count the same bytes again."""
    iphlpapi = ctypes.WinDLL("iphlpapi")  # type: ignore[attr-defined]
    table = ctypes.c_void_p()
    if iphlpapi.GetIfTable2(ctypes.byref(table)) != 0 or not table.value:
        return 0
    try:
        count = _u32(table.value)
        total = 0
        for i in range(count):
            row = table.value + 8 + i * IF_ROW_SIZE
            flags = ctypes.c_uint8.from_address(row + IF_FLAGS).value
            hardware, filtering = flags & 1, flags & 2
            if hardware and not filtering and _u32(row + IF_TYPE) != LOOPBACK:
                total += _u64(row + IF_IN_OCTETS)
        return total
    finally:
        iphlpapi.FreeMibTable(table)


def disk_bytes() -> dict[str, tuple[int, int]]:
    """(bytes written, bytes read) on each fixed volume ("C:") since it was mounted."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateFileW.restype = wt.HANDLE
    kernel32.CreateFileW.argtypes = [
        wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE,
    ]  # fmt: skip
    kernel32.DeviceIoControl.argtypes = [
        wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD,
        ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
    ]  # fmt: skip
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    out: dict[str, tuple[int, int]] = {}
    drives = kernel32.GetLogicalDrives()
    for i in range(26):
        letter = f"{chr(65 + i)}:"
        if not drives & (1 << i) or kernel32.GetDriveTypeW(letter + "\\") != DRIVE_FIXED:
            continue
        # no access asked, shared for reading and writing, as it exists
        handle = kernel32.CreateFileW(f"\\\\.\\{letter}", 0, 3, None, 3, 0, None)
        if handle in (None, wt.HANDLE(-1).value):
            continue
        try:
            perf = ctypes.create_string_buffer(88)  # DISK_PERFORMANCE
            got = wt.DWORD(0)
            if kernel32.DeviceIoControl(
                handle, IOCTL_DISK_PERFORMANCE, None, 0, perf, len(perf), ctypes.byref(got), None
            ):
                read, written = struct.unpack_from("<qq", perf, 0)
                out[letter] = (written, read)
        finally:
            kernel32.CloseHandle(handle)
    return out


def whole_disk(path: Path) -> str | None:
    """The volume a folder is on ("C:"), as ``disk_bytes`` names it."""
    drive = os.path.splitdrive(os.path.abspath(path))[0].upper()
    return drive if len(drive) == 2 and drive[1] == ":" else None
