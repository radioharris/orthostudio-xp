# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The test flights' parts of their own on Windows (tools/live/flights: ``sampler_windows.py``,
``fly.py``'s stop): the system's list of processes as Windows lays it out, read on any system;
on Windows itself, what it gives read and checked against what it says elsewhere, and a streamer
stopped by a Ctrl-Break to its process group, as the runner stops Live there."""

import ctypes
import os
import struct
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

FLIGHTS = Path(__file__).resolve().parents[1] / "tools" / "live" / "flights"
sys.path.insert(0, str(FLIGHTS))

import sampler_windows  # noqa: E402

WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="what Windows itself gives")


def process_buffer(entries: list[dict]) -> ctypes.Array:
    """A SystemProcessInformation buffer as a 64-bit Windows lays it out: each process's fields
    where Windows writes them, and its threads after it."""
    size = sum(0x100 + 0x50 * len(e["threads"]) for e in entries)
    names = sum(len(e["name"].encode("utf-16-le")) + 2 for e in entries)
    buffer = ctypes.create_string_buffer(size + names)
    base = ctypes.addressof(buffer)
    offset, text = 0, size
    for k, e in enumerate(entries):
        block = 0x100 + 0x50 * len(e["threads"])
        struct.pack_into(
            "<II", buffer, offset, block if k < len(entries) - 1 else 0, len(e["threads"])
        )
        struct.pack_into("<I", buffer, offset + 0x10, e["hard_faults"])
        struct.pack_into("<qq", buffer, offset + 0x28, e["user"], e["kernel"])
        raw = e["name"].encode("utf-16-le")
        struct.pack_into("<HH", buffer, offset + 0x38, len(raw), len(raw) + 2)
        struct.pack_into("<Q", buffer, offset + 0x40, base + text)
        struct.pack_into("<QQ", buffer, offset + 0x50, e["pid"], e["parent"])
        struct.pack_into("<Q", buffer, offset + 0x90, e["working_set"])
        struct.pack_into("<Q", buffer, offset + 0xC8, e["private"])
        struct.pack_into("<qq", buffer, offset + 0xE8, e["read"], e["written"])
        for i, (tid, kernel, user, state, priority) in enumerate(e["threads"]):
            at = offset + 0x100 + i * 0x50
            struct.pack_into("<qq", buffer, at, kernel, user)
            struct.pack_into("<QQ", buffer, at + 0x28, e["pid"], tid)
            struct.pack_into("<i", buffer, at + 0x38, priority)
            struct.pack_into("<I", buffer, at + 0x44, state)
        buffer[text : text + len(raw)] = raw
        offset, text = offset + block, text + len(raw) + 2
    return buffer


def test_the_process_list_read_as_windows_lays_it_out():
    buffer = process_buffer(
        [
            {
                "pid": 4, "parent": 0, "name": "System", "user": 0, "kernel": 50_000_000,
                "working_set": 1 << 20, "private": 1 << 20, "read": 0, "written": 0,
                "hard_faults": 0, "threads": [(8, 50_000_000, 0, 5, 16)],
            },
            {
                "pid": 4242, "parent": 900, "name": "X-Plane.exe", "user": 600_000_000,
                "kernel": 100_000_000, "working_set": 9 << 30, "private": 12 << 30,
                "read": 3 << 30, "written": 1 << 20, "hard_faults": 777,
                "threads": [(100, 10_000_000, 20_000_000, 2, 8), (101, 0, 5_000_000, 5, 8)],
            },
        ]
    )  # fmt: skip
    rows = sampler_windows.parse(buffer)
    assert [(p.pid, p.parent, p.name) for p in rows] == [
        (4, 0, "System"),
        (4242, 900, "X-Plane.exe"),
    ]
    xp = rows[1]
    assert xp.cpu_s == 70.0  # user and kernel, in 100 ns
    assert (xp.working_set, xp.private, xp.read, xp.written, xp.hard_faults) == (
        9 << 30, 12 << 30, 3 << 30, 1 << 20, 777,
    )  # fmt: skip
    assert [(t.tid, t.cpu_s, t.state, t.priority) for t in xp.threads] == [
        (100, 3.0, 2, 8),
        (101, 0.5, 5, 8),
    ]
    assert sampler_windows.processes(rows)[1] == (4242, 900, 9 * 1024, 70.0, "X-Plane.exe")
    assert sampler_windows.usage(4242, rows) == {
        "footprint": 12 << 30, "read": 3 << 30, "written": 1 << 20, "pageins": 777,
    }  # fmt: skip


def test_thread_states_as_a_mac_s():
    """The report counts a Mac's running threads: on a processor or ready for one."""
    assert [sampler_windows.mac_state(s) for s in (1, 2, 3, 7)] == [1, 1, 1, 1]
    assert sampler_windows.mac_state(5) == 3  # waiting
    assert sampler_windows.mac_state(4) == 5  # ended


@windows_only
def test_this_process_read_from_windows_list():
    import threading

    from orthostudio.live import machine

    named = threading.Event()
    done = threading.Event()

    def worker():
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentThread.restype = ctypes.c_void_p
        kernel32.SetThreadDescription.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.SetThreadDescription(kernel32.GetCurrentThread(), "Pool worker (background - 1)")
        named.set()
        done.wait(10)

    t = threading.Thread(target=worker)
    t.start()
    try:
        assert named.wait(5)
        rows = sampler_windows.snapshot()
        me = [p for p in rows if p.pid == os.getpid()]
        assert me and me[0].name.lower().startswith("python")
        assert me[0].working_set > 10 << 20 and me[0].cpu_s > 0
        names = [name for _, name, _, _, _ in sampler_windows.threads(os.getpid(), rows)]
        assert "Pool worker (background - 1)" in names
        assert machine.xplane_pool(os.getpid())[1] >= 1
    finally:
        done.set()
        t.join()


@windows_only
def test_the_memory_network_and_disks_read():
    memory = sampler_windows.memory()
    assert 0 <= memory["free_pct"] <= 100
    assert memory["pressure"] in (1, 2, 4)
    assert memory["swapped_mb"] is not None and memory["swapped_mb"] >= 0
    received = sampler_windows.net_in()
    assert received > 0  # what the network adapters received since they came up
    said = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-NetAdapterStatistics | Measure-Object -Property ReceivedBytes -Sum).Sum"],
        capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    if said.isdigit():  # Windows' own count, a moment later: as large, give or take the moment
        assert received <= int(said) * 1.05 + 10_000_000
        assert received >= int(said) * 0.5
    disks = sampler_windows.disk_bytes()
    assert "C:" in disks and disks["C:"][0] > 0 and disks["C:"][1] > 0
    assert sampler_windows.whole_disk(Path.cwd()) == os.path.splitdrive(os.getcwd())[0].upper()


@windows_only
def test_a_streamer_stopped_by_a_ctrl_break_to_its_group(tmp_path):
    """As fly.py stops Live on Windows: its group's Ctrl-Break, which Live takes as a Ctrl-C and
    stops as after one (orthostudio.live.__main__); a Ctrl-C cannot be sent to a group there."""
    import fly

    said = tmp_path / "said.txt"
    script = textwrap.dedent(
        f"""
        import signal, time
        signal.signal(signal.SIGBREAK, signal.default_int_handler)  # as Live's command line
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            open({str(said)!r}, "w").write("stopped as after a Ctrl-C")
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    )
    time.sleep(1.5)
    fly.stop_tree(child.pid, timeout_s=30)
    assert child.wait(30) == 0
    assert said.read_text() == "stopped as after a Ctrl-C"


@windows_only
def test_live_takes_a_ctrl_break_as_a_ctrl_c(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "live.toml").write_text("enabled = true\n")
    code = (
        "import signal\n"
        "from orthostudio.live import __main__ as m\n"
        "m._root()\n"
        "print(signal.getsignal(signal.SIGBREAK) is signal.default_int_handler)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "OSXP_HOME": str(home)},
    )
    assert out.stdout.strip() == "True", out.stderr


def test_a_streamer_s_ended_processes_keep_their_time(monkeypatch):
    """Windows has no ps -S: the time of the streamer's processes that ended (an 8192 px
    texture's) is kept by the sampler; the streamer's own end breaks nothing."""
    import sampler

    monkeypatch.setattr(sampler, "WINDOWS", True)
    s = sampler.Sampler(Path("unused.csv"), streamer_pid=10)
    # (pid, parent, MB, CPU s, name): Live, a worker, an 8192 px texture's process
    assert s._ended([(10, 1, 100, 5.0, "python.exe"), (11, 10, 50, 2.0, "python.exe"),
                     (12, 10, 50, 3.0, "python.exe")]) == 0.0  # fmt: skip
    assert s._ended([(10, 1, 100, 6.0, "python.exe"), (11, 10, 50, 2.5, "python.exe")]) == 3.0
    assert s._ended([]) == 3.0 + 6.0 + 2.5  # Live gone too: all of it kept, nothing broken


def _kit_flight_script():
    """The Windows kit's flight script (tools/live/windows_kit/vol.py), as a module."""
    import importlib.util

    path = FLIGHTS.parents[0] / "windows_kit" / "vol.py"
    spec = importlib.util.spec_from_file_location("kit_vol", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_kit_reads_windows_background_work(monkeypatch):
    vol = _kit_flight_script()

    def process(pid: int, name: str, cpu_s: float):
        return sampler_windows.Process(pid, 4, name, cpu_s, 0, 0, 0, 0, 0)

    snapshots = iter(
        [
            [process(10, "SearchIndexer.exe", 10.0), process(11, "X-Plane.exe", 5.0)],
            [process(10, "SearchIndexer.exe", 10.03), process(11, "X-Plane.exe", 9.0)],
        ]
    )
    monkeypatch.setattr(vol.sampler_windows, "snapshot", lambda: next(snapshots))
    used = vol.background_cores(window_s=0.1)
    assert list(used) == ["SearchIndexer.exe"]  # X-Plane is not Windows' background work
    assert abs(used["SearchIndexer.exe"] - 0.3) < 1e-6


def test_the_kit_s_flights_wait_for_windows_background_work(monkeypatch, capsys):
    """Each flight in the same conditions: begun once Windows' indexer, updates and antivirus
    are quiet, as they were not on the Shadow on 2026-10-02."""
    vol = _kit_flight_script()
    readings = iter([{"SearchIndexer.exe": 0.6, "TiWorker.exe": 0.25}, {"SearchIndexer.exe": 0.05}])
    monkeypatch.setattr(vol, "background_cores", lambda: next(readings))
    assert vol.wait_quiet(max_s=60) is True
    assert "SearchIndexer.exe 0.60, TiWorker.exe 0.25" in capsys.readouterr().out
    assert vol.wait_quiet(max_s=0) is False  # it goes all the same, and says so
    assert "le vol part quand meme" in capsys.readouterr().out


def test_the_kit_s_flights_are_the_runner_s(monkeypatch):
    """Each of the kit's choices, as the runner takes it (2026-10-04): the first, the comparison,
    Live alone in the app then with OrthoStudio XP open, each cold at 16/18 over Toulouse."""
    import fly

    vol = _kit_flight_script()
    taken = []
    monkeypatch.setattr(fly, "run", lambda a: taken.append(a) or 0)
    for args in vol.RUNS["1"]:
        assert fly.main(args) == 0
    alone, beside = taken
    for a in (alone, beside):
        assert (a.streamer, a.cold, a.zooms, a.scenarios) == (
            "app",
            True,
            (16, 18),
            ["toulouse-low"],
        )
    assert (alone.with_osxp, beside.with_osxp) == (False, True)
    assert [vol.label_of(r) for r in vol.RUNS["5"]] == [
        "Live seul dans l'app", "Live avec OrthoStudio XP ouvert", "X-Plane seul",
    ]  # fmt: skip
    for choice in ("2", "3", "4", "5"):
        for args in vol.RUNS[choice]:
            assert fly.main(args) == 0


def test_the_kit_s_folder_short_and_without_accents(monkeypatch):
    """On the user's Shadow (2026-10-04) the kit's folder « ... vols de la fenêtre unique ... »
    downloaded nothing (its « ê »: curl could not open its certificates' file) and, twice in the
    path after Windows' « Extraire tout », was too long for a region's list to be kept."""
    monkeypatch.syspath_prepend(str(FLIGHTS.parent))
    import windows_kit

    vol = _kit_flight_script()
    root = Path("/k")
    assert vol.unfit(root / "OSXP-vols-Windows" / "OSXP-vols-Windows") == ""
    assert "accent" in vol.unfit(root / "vols de la fen\u00eatre unique")
    assert "caracteres" in vol.unfit(root / ("x" * vol.LONGEST_PATH))
    assert windows_kit.NAME.isascii()
    # unpacked on a Desktop, twice in the path: still short enough
    desktop = "C:\\Users\\Jean-Christophe\\Desktop"
    assert len(f"{desktop}\\{windows_kit.NAME}\\{windows_kit.NAME}") <= vol.LONGEST_PATH
