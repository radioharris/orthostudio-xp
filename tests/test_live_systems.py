# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live on macOS, Windows and Linux (orthostudio.live.scenery, pools): where it runs, how it finds
X-Plane among the system's processes, how it starts X-Plane, and its encoders. Each system's way is
taken on any system, the system's own parts faked; what only a system can do is tried there."""

import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from orthostudio import live
from orthostudio.live import machine, pools, scenery
from orthostudio.live.gate import Gate
from orthostudio.live.texture import worker_ignores_ctrl_c
from orthostudio.live.xpudp import XPlaneUDP
from test_live_machine import system_processes


@pytest.mark.parametrize(
    ("platform", "runs"),
    [("darwin", True), ("win32", True), ("linux", True), ("freebsd14", False)],
)
def test_where_live_runs(monkeypatch, platform, runs):
    monkeypatch.setattr(sys, "platform", platform)
    assert live.supported() is runs


def write_stat(proc: Path, pid: int, name: str, state: str, start_ticks: int) -> None:
    """A process's ``/proc/<pid>/stat``: its name, its state (3rd field), its start (22nd)."""
    (proc / str(pid)).mkdir(parents=True)
    fields = [state, *["0"] * 18, str(start_ticks), *["0"] * 30]
    (proc / str(pid) / "stat").write_text(f"{pid} ({name}) {' '.join(fields)}\n")


def test_linux_s_processes_from_proc(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "sysconf", lambda name: 100, raising=False)  # clock ticks a second
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "stat").write_text("cpu  1 2 3 4\nbtime 1700000000\nprocesses 99\n")
    write_stat(proc, 1, "systemd", "S", 100)
    write_stat(proc, 4242, "X-Plane-x86_64", "R", 25_000)  # 250 s after the boot
    write_stat(proc, 4300, "X-Plane-x86_64", "Z", 30_000)  # has quit, its parent not told yet
    write_stat(proc, 77, "a (strange) name", "S", 200)
    (proc / "self").mkdir()  # no process's number
    assert scenery._linux_processes(proc) == {
        1: ("systemd", 1_700_000_001.0),
        4242: ("X-Plane-x86_64", 1_700_000_250.0),
        77: ("a (strange) name", 1_700_000_002.0),
    }
    assert scenery._linux_processes(tmp_path / "none") is None


def test_windows_processes_and_their_starts():
    """Windows' list (here a buffer laid out as it gives it): each process's start, in 100 ns
    since 1601; none for the system's first ones."""
    since_1601 = (1_700_000_000 + 11_644_473_600) * 10_000_000
    buffer = system_processes(
        [(0, "", []), (4, "System", [(8, 5)]), (42, "X-Plane.exe", [(100, 2), (101, 5)])],
        created={42: since_1601 + 5_000_000},
    )
    assert machine.windows_starts(buffer) == {
        0: ("", 0.0),
        4: ("System", 0.0),
        42: ("X-Plane.exe", 1_700_000_000.5),
    }


@pytest.mark.parametrize(
    ("platform", "reader"),
    [("darwin", "_darwin_processes"), ("linux", "_linux_processes")],
)
def test_each_system_s_own_list(monkeypatch, platform, reader):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(scenery, reader, lambda: {7: ("X-Plane", 1.0)})
    assert scenery._processes() == {7: ("X-Plane", 1.0)}


def test_windows_s_own_list(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(machine, "windows_process_starts", lambda: {9: ("X-Plane.exe", 2.0)})
    assert scenery._processes() == {9: ("X-Plane.exe", 2.0)}
    monkeypatch.setattr(sys, "platform", "freebsd14")
    assert scenery._processes() is None


def test_x_plane_on_windows_in_any_case(monkeypatch):
    monkeypatch.setattr(scenery, "_xplane_running", lambda: pytest.fail("tasklist was run"))
    monkeypatch.setattr(
        scenery, "_processes", lambda: {4: ("System", 0.0), 9: ("x-plane.EXE", 5.0)}
    )
    monkeypatch.setattr(sys, "platform", "win32")
    assert scenery.xplane_running()
    assert scenery.xplane_pid() == 9
    assert scenery.xplane_started_at() == 5.0
    monkeypatch.setattr(sys, "platform", "linux")  # a name is a name there
    assert not scenery.xplane_running()
    assert scenery.xplane_pid() is None


def test_the_other_programs_streaming_ortho_by_their_processes(monkeypatch, tmp_path):
    """XPME and AutoOrtho, as each system names their processes (the user's Mac: XPME 4.7.22's
    window « XPlane Map Enhancement » and its server « XplaneMapEnhancement », 2026-10-08):
    Live refuses to start beside either."""
    (tmp_path / "Custom Scenery").mkdir()
    for names, found in (
        (["XPlane Map Enhancement", "XplaneMapEnhancement"], ["XPME"]),  # a Mac
        (["XPlane Map Enhancement.exe"], ["XPME"]),  # Windows
        (["XplaneMapEnhanc"], ["XPME"]),  # Linux keeps 15 characters
        (["autoortho"], ["AutoOrtho"]),
        (["AutoOrtho.EXE", "XplaneMapEnhancement.exe"], ["XPME", "AutoOrtho"]),
        (["X-Plane", "OrthoStudio XP", "python3.14"], []),
    ):
        listed = {pid: (name, 0.0) for pid, name in enumerate(names, 10)}
        monkeypatch.setattr(scenery, "_processes", lambda listed=listed: listed)
        assert scenery.ortho_streamers(tmp_path) == found, names


def test_xpme_by_the_folder_it_mounts(monkeypatch, tmp_path):
    """Its packs are links into the folder it mounts while it runs (``~/XPME-fuse`` on a Mac),
    which says it where the list of processes cannot be read."""
    scenery_dir = tmp_path / "X-Plane 12" / "Custom Scenery"
    scenery_dir.mkdir(parents=True)
    fuse = tmp_path / "XPME-fuse"
    fuse.mkdir()
    (scenery_dir / "XPME_Europe").symlink_to(fuse / "XPME_Europe")
    monkeypatch.setattr(scenery, "_processes", lambda: None)
    mounted = set()
    monkeypatch.setattr(scenery.os.path, "ismount", lambda p: os.path.realpath(p) in mounted)
    assert scenery.ortho_streamers(tmp_path / "X-Plane 12") == []  # XPME stopped: nothing there
    mounted.add(os.path.realpath(fuse))
    assert scenery.ortho_streamers(tmp_path / "X-Plane 12") == ["XPME"]


@pytest.mark.parametrize(
    ("platform", "program"),
    [("darwin", "X-Plane.app"), ("win32", "X-Plane.exe"), ("linux", "X-Plane-x86_64")],
)
def test_x_plane_started_as_a_double_click_starts_it(monkeypatch, tmp_path, platform, program):
    started = []
    monkeypatch.setattr(scenery, "_spawn", lambda cmd, cwd=None: started.append((cmd, cwd)))
    monkeypatch.setattr(sys, "platform", platform)
    xplane = tmp_path / "X-Plane 12"
    xplane.mkdir()
    with pytest.raises(FileNotFoundError, match=program):
        scenery.launch_xplane(xplane)
    if platform == "darwin":
        (xplane / program).mkdir()
    else:
        (xplane / program).write_bytes(b"")
    scenery.launch_xplane(xplane)
    if platform == "darwin":
        assert started == [(["/usr/bin/open", str(xplane / program)], None)]
    else:
        assert started == [([str(xplane / program)], xplane)]  # from its folder


class _Started:
    """What subprocess.Popen gives, for the Windows flags."""

    returncode = 0

    def __init__(self, *args, creationflags=0, **kwargs):
        self.flags = creationflags

    def wait(self, timeout=None):
        return 0


def test_on_windows_x_plane_leaves_live_s_job_where_it_may(monkeypatch):
    """Out of Live's job, so that X-Plane stays when Live goes; in it where the job keeps its
    processes (Windows refuses then)."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000, raising=False)
    tried = []

    def popen(*args, **kwargs):
        tried.append(kwargs["creationflags"])
        if kwargs["creationflags"] & 0x01000000 and len(tried) == 1:
            raise PermissionError("access denied")
        return _Started(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    child = scenery._spawn(["C:\\X-Plane 12\\X-Plane.exe"], cwd=Path("C:\\X-Plane 12"))
    assert child.flags & 0x200
    assert [bool(f & 0x01000000) for f in tried] == [True, False]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX programs")
def test_a_program_started_is_waited_for():
    """A thread of Live's waits for what it starts: one that has quit is not left listed, as a
    zombie X-Plane would seem to run."""
    child = scenery._spawn(["/bin/sh", "-c", "exit 3"])
    deadline = time.monotonic() + 10
    while child.returncode is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert child.returncode == 3


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux's sessions")
def test_on_linux_x_plane_has_a_session_of_its_own():
    child = scenery._spawn(["/bin/sh", "-c", "sleep 2"])
    try:
        assert os.getsid(child.pid) == child.pid  # Ctrl-C in Live's terminal never reaches it
    finally:
        child.kill()


def test_windows_starts_each_encoder_anew(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert pools.context().get_start_method() == "spawn"
    monkeypatch.setattr(
        pools.forkserver, "ensure_running", lambda: pytest.fail("a fork server on Windows")
    )
    pools.start_server()


def test_an_encoder_started_anew_runs_live_s_code():
    """A new Python for each encoder, as on Windows (here on any system): it imports what it
    runs, the encoders' start included, and nothing of the process that started it; and it runs
    below X-Plane's threads."""
    with ProcessPoolExecutor(
        max_workers=1,
        mp_context=pools.multiprocessing.get_context("spawn"),
        initializer=worker_ignores_ctrl_c,
    ) as pool:
        if hasattr(os, "nice"):
            assert pool.submit(os.nice, 0).result(timeout=120) == min(19, os.nice(0) + 10)
        else:
            assert pool.submit(os.getpid).result(timeout=120) != os.getpid()


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]  # nobody listens there once closed


def test_x_plane_not_started_yet_is_no_error():
    """Live starts before X-Plane and asks its port, shut for now: Windows answers the next read
    with WSAECONNRESET (told not to, xpudp.no_port_unreachable), macOS and Linux with nothing."""
    xp = XPlaneUDP(["sim/flightmodel/position/latitude"], port=_closed_port())
    try:
        for _ in range(3):
            xp.subscribe()
            assert xp.receive(timeout=0.2) is None
    finally:
        xp.close()


class _ResetOnce:
    """A socket as Windows leaves it after a datagram to a shut port: one read fails, then the
    next datagrams, then nothing."""

    def __init__(self, datagrams):
        self.reads = [ConnectionResetError(10054, "reset"), *datagrams, BlockingIOError()]

    def recvfrom(self, size):
        got = self.reads.pop(0)
        if isinstance(got, BaseException):
            raise got
        return got

    def settimeout(self, timeout):
        pass

    def sendto(self, data, addr):
        pass

    def close(self):
        pass


def test_a_reset_on_windows_is_read_past(tmp_path):
    xp = XPlaneUDP(["sim/flightmodel/position/latitude"], port=_closed_port())
    xp.sock.close()
    xp.sock = _ResetOnce([])
    assert xp.receive(timeout=0.1) is None  # not an error: nothing came
    gate = Gate(tmp_path, lambda icao: None, port=_closed_port())
    try:
        gate.sock.close()
        gate.sock = _ResetOnce([(b"LIVE1 LOAD 43.5 5.2", ("127.0.0.1", 5000))])
        assert [kind for kind, *_ in gate.requests()] == ["LOAD"]  # read past the reset
    finally:
        gate.close()
