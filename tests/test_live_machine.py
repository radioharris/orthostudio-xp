# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's share of the computer it runs on (``orthostudio.live.machine``): X-Plane first, on macOS,
Linux and Windows alike."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import time
from pathlib import Path

import pytest

from orthostudio.live import control, machine


@pytest.mark.parametrize(
    ("cores", "ram", "encoders", "flight", "sharp"),
    [
        (14, 48, 12, 4, 4),  # the Mac the test flights flew on (2026-09-28): as before
        (14, 16, 5, 2, 1),  # the same cores, 16 GB: the memory bounds the encoders
        (8, 16, 5, 2, 1),  # a Mac of 8 cores and 16 GB
        (10, 24, 8, 2, 2),
        (16, 32, 10, 4, 2),  # a PC of 8 cores, 16 threads
        (4, 8, 2, 1, 1),
    ],
)
def test_the_share_follows_the_computer(
    monkeypatch: pytest.MonkeyPatch, cores: int, ram: int, encoders: int, flight: int, sharp: int
) -> None:
    monkeypatch.setenv("OSXP_LIVE_MACHINE", f"{cores},{ram}")
    got = machine.share()
    assert (got.cores, got.ram_gb) == (cores, ram)
    assert (got.encoders, got.flight_encodes, got.sharp_builds) == (encoders, flight, sharp)


def test_windows_pools_hold_61_processes_at_most(monkeypatch: pytest.MonkeyPatch) -> None:
    """Python's process pools take 61 processes at most on Windows: a computer of 128 threads and
    384 GB gets 61 encoders there, every core but two elsewhere."""
    monkeypatch.setattr(machine, "WINDOWS", False)
    assert machine.encoders(128, 384) == 126
    monkeypatch.setattr(machine, "WINDOWS", True)
    assert machine.encoders(128, 384) == 61
    assert machine.encoders(14, 48) == 12


def test_the_encoders_set_by_hand_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSXP_LIVE_MACHINE", "8,16")
    assert machine.share(3).encoders == 3


def test_a_machine_that_does_not_parse_is_this_computer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSXP_LIVE_MACHINE", "lots")
    cores, ram = machine.machine()
    assert cores >= 1
    assert ram > 1


def test_this_computer_s_memory_is_read() -> None:
    """On the machine the tests run on, whichever system it is."""
    got = machine.memory_gb()
    assert got is not None and got > 1


def test_windows_memory_from_global_memory_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows has no ``os.sysconf``: its memory comes from ``GlobalMemoryStatusEx``."""

    class Kernel32:
        def GlobalMemoryStatusEx(self, status: object) -> int:  # noqa: N802 (Windows' name)
            status._obj.ullTotalPhys = 32 * 2**30  # type: ignore[attr-defined]
            return 1

    monkeypatch.delattr(machine.os, "sysconf", raising=False)
    monkeypatch.setattr(machine, "WINDOWS", True)
    monkeypatch.setattr(
        ctypes, "windll", type("WinDLL", (), {"kernel32": Kernel32()})(), raising=False
    )
    assert machine.memory_gb() == 32.0


def test_a_computer_whose_memory_cannot_be_read_counts_as_small(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OSXP_LIVE_MACHINE", raising=False)
    monkeypatch.delattr(machine.os, "sysconf", raising=False)
    monkeypatch.setattr(machine, "WINDOWS", False)
    assert machine.memory_gb() is None
    assert machine.machine()[1] == machine.UNKNOWN_RAM_GB
    assert machine.share().sharp_builds == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_memory_for_real() -> None:
    status = machine._windows_memory_status()
    assert status is not None and status.ullTotalPhys > 0
    assert machine.memory_gb() == status.ullTotalPhys / 2**30


def test_the_log_says_the_share_and_warns_below_32_gb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSXP_LIVE_MACHINE", "8,16")
    small = machine.share()
    assert small.notes(16, 18) == [
        "this computer: 8 cores, 16 GB: 5 encoders, 2 textures at once in flight, "
        "1 at 8192 px at once"
    ]
    for zooms in ((17, 18), (16, 19), (17, 19)):
        said = small.notes(*zooms)
        assert len(said) == 2
        assert said[1].startswith("this computer has 16 GB of memory, ZL17 and ZL19 want 32")
    monkeypatch.setenv("OSXP_LIVE_MACHINE", "12,32")
    assert len(machine.share().notes(17, 19)) == 1  # 32 GB: enough


def test_the_window_is_told_of_the_computer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("OSXP_LIVE_MACHINE", "8,16")
    checks = control.gather_checks()
    assert checks["machine"] == {"cores": 8, "ram_gb": 16, "sharp_ram_gb": 32}


def test_no_osxp_engine_before_the_fork_server() -> None:
    """Reading the computer loads nothing of OrthoStudio XP's engine: PROJ must not be open
    before Live's fork server starts (``pools``), and the default number of encoders is read as
    the command line is parsed."""
    import subprocess

    code = (
        "import sys; from orthostudio.live import machine, session; session.default_workers(); "
        "machine.share(); print('pyproj' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=os.environ
    )
    assert out.stdout.strip() == "False"


@pytest.mark.parametrize(("level", "expected"), [(1, 0), (2, 1), (4, 2), (0, 0)])
def test_macos_memory_pressure(monkeypatch: pytest.MonkeyPatch, level: int, expected: int) -> None:
    """macOS's own level: normal, warning, critical; unread counts as normal."""
    monkeypatch.setattr(machine, "MACOS", True)
    monkeypatch.setattr(machine, "WINDOWS", False)
    monkeypatch.setattr(machine, "_darwin_pressure_level", lambda: level)
    assert machine.memory_pressure() == expected


@pytest.mark.parametrize(
    ("available_kb", "expected"), [(3_200_000, 0), (1_300_000, 1), (500_000, 2)]
)
def test_linux_memory_pressure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, available_kb: int, expected: int
) -> None:
    """Linux: the memory available against the total, from /proc/meminfo (16 GB here)."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        f"MemTotal:       16000000 kB\nMemFree:  100 kB\nMemAvailable:   {available_kb} kB\n"
    )
    monkeypatch.setattr(machine, "MACOS", False)
    monkeypatch.setattr(machine, "WINDOWS", False)
    monkeypatch.setattr(machine, "MEMINFO", str(meminfo))
    assert machine.memory_pressure() == expected


@pytest.mark.parametrize(("load", "expected"), [(85, 0), (92, 1), (97, 2)])
def test_windows_memory_pressure(monkeypatch: pytest.MonkeyPatch, load: int, expected: int) -> None:
    """Windows: the memory load GlobalMemoryStatusEx gives."""

    class Kernel32:
        def GlobalMemoryStatusEx(self, status: object) -> int:  # noqa: N802 (Windows' name)
            status._obj.dwMemoryLoad = load  # type: ignore[attr-defined]
            return 1

    monkeypatch.setattr(machine, "MACOS", False)
    monkeypatch.setattr(machine, "WINDOWS", True)
    monkeypatch.setattr(
        ctypes, "windll", type("WinDLL", (), {"kernel32": Kernel32()})(), raising=False
    )
    assert machine.memory_pressure() == expected


def test_memory_pressure_unreadable_is_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(machine, "MACOS", False)
    monkeypatch.setattr(machine, "WINDOWS", False)
    monkeypatch.setattr(machine, "MEMINFO", "/no/such/meminfo")
    assert machine.memory_pressure() == machine.NORMAL


def test_this_computer_s_memory_pressure_reads() -> None:
    """On the machine the tests run on, whichever system it is."""
    assert machine.memory_pressure() in (machine.NORMAL, machine.SHORT, machine.VERY_SHORT)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS's I/O policy")
def test_background_io_throttles_this_thread_and_gives_it_back() -> None:
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    before = libc.getiopolicy_np(0, 1)
    with machine.background_io():
        assert libc.getiopolicy_np(0, 1) == 3  # IOPOL_THROTTLE, this thread only
    assert libc.getiopolicy_np(0, 1) == before


def test_background_io_where_the_system_says_no(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing breaks: the work is done at its usual priority."""

    def refused() -> None:
        raise OSError("not allowed")

    monkeypatch.setattr(machine, "_enter_background_io", refused)
    with machine.background_io():
        done = True
    assert done


def _name_this_thread(name: str) -> None:
    """This thread named as X-Plane names its own: macOS's and Linux's pthread_setname_np (Linux
    keeps 15 characters), Windows' SetThreadDescription."""
    import ctypes
    import ctypes.util

    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
        kernel32.GetCurrentThread.restype = ctypes.c_void_p
        kernel32.SetThreadDescription.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.SetThreadDescription(kernel32.GetCurrentThread(), name)
        return
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    if sys.platform == "darwin":
        libc.pthread_setname_np(name.encode())
        return
    libc.pthread_self.restype = ctypes.c_ulong
    libc.pthread_setname_np.argtypes = [ctypes.c_ulong, ctypes.c_char_p]
    assert libc.pthread_setname_np(libc.pthread_self(), name.encode()[: machine.LINUX_NAME]) == 0


@pytest.mark.skipif(
    sys.platform not in ("darwin", "win32") and not sys.platform.startswith("linux"),
    reason="macOS's libproc, Linux's /proc, Windows' list of threads",
)
def test_x_plane_s_workers_read_from_outside() -> None:
    """Live reads X-Plane's background workers from outside, no privilege needed (2026-10-01):
    here a thread of the test's own, named as X-Plane names them, running or waiting."""
    import os
    import threading

    stop, named = threading.Event(), threading.Event()

    def worker(spin: bool) -> None:
        _name_this_thread("Pool worker (background - 1)")
        named.set()
        while not stop.is_set():
            if not spin:
                stop.wait(0.05)

    for spin, running in ((True, 1), (False, 0)):
        stop.clear()
        named.clear()
        t = threading.Thread(target=worker, args=(spin,))
        t.start()
        try:
            assert named.wait(5)
            assert machine.xplane_pool(os.getpid()) == (running, 1)
        finally:
            stop.set()
            t.join()
    for _ in range(100):  # the thread may still be leaving the system's list a moment after
        if machine.xplane_pool(os.getpid()) == (0, 0):
            break
        time.sleep(0.02)
    assert machine.xplane_pool(os.getpid()) == (0, 0)
    assert machine.xplane_pool(999_999_999) is None


def test_x_plane_s_workers_read_on_linux(tmp_path: Path) -> None:
    """Linux: /proc/<pid>/task/<thread>/comm keeps 15 characters of a thread's name, its stat
    says R for running after the name in brackets, which may hold brackets itself."""
    proc = tmp_path / "proc"
    for tid, name, state in (
        (1, "X-Plane-x86_64", "S"),
        (2, "Pool worker (ba", "R"),
        (3, "Pool worker (ba", "S"),
        (4, "Pool worker (re", "R"),  # a realtime worker: not counted
    ):
        task = proc / "42" / "task" / str(tid)
        task.mkdir(parents=True)
        (task / "comm").write_text(name + "\n")
        (task / "stat").write_text(f"{tid} ({name}) {state} 1 1 1 0 -1\n")
    assert machine._linux_pool(42, proc) == (1, 2)
    assert machine._linux_pool(43, proc) is None  # X-Plane gone


def system_processes(
    entries: list[tuple[int, str, list[tuple[int, int]]]], created: dict[int, int] | None = None
):
    """A buffer laid out as Windows' NtQuerySystemInformation(SystemProcessInformation) gives it
    on a 64-bit system: (process, image name, [(thread, state)]) each, and ``created``, a
    process's start in 100 ns since 1601."""
    import struct

    size = sum(0x100 + 0x50 * len(threads) for _, _, threads in entries)
    names = sum(len(name.encode("utf-16-le")) + 2 for _, name, _ in entries)
    buffer = ctypes.create_string_buffer(size + names)
    base = ctypes.addressof(buffer)
    offset, text = 0, size
    for k, (pid, name, threads) in enumerate(entries):
        block = 0x100 + 0x50 * len(threads)
        struct.pack_into("<II", buffer, offset, block if k < len(entries) - 1 else 0, len(threads))
        raw = name.encode("utf-16-le")
        struct.pack_into("<HH", buffer, offset + 0x38, len(raw), len(raw) + 2)
        struct.pack_into("<Q", buffer, offset + 0x40, base + text)
        struct.pack_into("<Q", buffer, offset + 0x50, pid)
        struct.pack_into("<q", buffer, offset + 0x20, (created or {}).get(pid, 0))
        for i, (tid, state) in enumerate(threads):
            at = offset + 0x100 + i * 0x50
            struct.pack_into("<QQ", buffer, at + 0x28, pid, tid)
            struct.pack_into("<I", buffer, at + 0x44, state)
        buffer[text : text + len(raw)] = raw
        offset, text = offset + block, text + len(raw) + 2
    return buffer


def test_x_plane_s_workers_read_on_windows() -> None:
    """Windows (not tried on one with X-Plane, 2026-10-01): the system's list of processes and
    their threads, here a buffer laid out as it gives it, each thread's name as
    GetThreadDescription would give it; a thread ready or running counts. Where X-Plane names no
    thread so, none is found and Live goes without (Streamer.x_plane_pool)."""
    buffer = system_processes(
        [
            (4, "System", [(8, 5)]),
            (42, "X-Plane.exe", [(100, 2), (101, 5), (102, 1), (103, 2), (104, 5)]),
        ]
    )
    names = {
        100: "Pool worker (background - 1)",  # running
        101: "Pool worker (background - 2)",  # waiting
        102: "Pool worker (background - 3)",  # ready
        103: "Main Thread",
        104: "",
    }
    listed = [(p, name, len(threads)) for p, name, threads in machine.windows_processes(buffer)]
    assert listed == [(4, "System", 1), (42, "X-Plane.exe", 5)]
    assert machine.windows_pool_in(buffer, 42, lambda pid, tid: names[tid]) == (2, 3)
    assert machine.windows_pool_in(buffer, 4, lambda pid, tid: "") == (0, 0)
    assert machine.windows_pool_in(buffer, 43, lambda pid, tid: "") is None
