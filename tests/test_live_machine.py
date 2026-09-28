# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's share of the computer it runs on (``orthostudio.live.machine``): X-Plane first, on macOS,
Linux and Windows alike."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
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
    assert machine._windows_memory_gb() is not None


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
