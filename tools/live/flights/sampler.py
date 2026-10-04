# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The computer during a test flight, every SAMPLE_S, into ``system.csv``: X-Plane's and the
streamer's memory and CPU time (the streamer with every process it started), the bytes received
from the network, and the bytes written to each disk since the computer started. The report turns
them into what the start and the path cost. On a Mac with ``ps``, ``netstat`` and ``ioreg``; on
Windows the same readings from the system's own lists (``sampler_windows.py``, 2026-10-01).

Since 2026-09-30, to see why X-Plane took 7.5 minutes to load a tile over Norway, where its log
says nothing: X-Plane's whole memory (its GPU's included), the bytes it read from the disks and its
page-ins; the Mac's free memory, pressure, memory compressed and swapped, and heat; the bytes read
from each disk; and, into ``threads.csv``, each of X-Plane's threads: its name, processor time and
state. All read from outside, as Activity Monitor does, without attaching to X-Plane (``sample``
cannot: X-Plane is signed with the hardened runtime).

Since 2026-10-01, as the analysis of the late tiles asked: the cores the system lets work
(``kern.sched_recommended_cores``: fewer when it is hot), and into ``processes.csv`` every other
process using more than OTHERS_MIN_CORES between two lines (Spotlight, a browser in front...).

Since 2026-10-04, for the app itself in its one window (``fly.py --streamer app``): the streamer is
the whole app, its web view's processes counted in (a Mac's are not under it: WebKit's, started by
the system, are found by the program they work for), and of it, OrthoStudio XP's engine and the
web view apart: what OrthoStudio XP open beside Live costs."""

from __future__ import annotations

import contextlib
import csv
import ctypes
import ctypes.util
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from pathlib import Path

SAMPLE_S = 5.0
FIELDS = [
    "wall",
    "xplane_rss_mb",
    "xplane_cpu_s",
    "streamer_rss_mb",
    "streamer_cpu_s",
    "net_in",
    "disks",
    # 2026-09-30: X-Plane from outside, the Mac's memory and heat, the disks' reads
    "xplane_footprint_mb",
    "xplane_read_mb",
    "xplane_pageins",
    "mem_free_pct",
    "pressure",
    "compressed_mb",
    "swapped_mb",
    "heat",
    "disk_reads",
    # 2026-10-01: the cores the system lets work
    "cores_allowed",
    # 2026-10-04: of the app (the streamer), OrthoStudio XP's engine and the window's web view
    "engine_rss_mb",
    "engine_cpu_s",
    "web_rss_mb",
    "web_cpu_s",
]
THREAD_FIELDS = ["wall", "thread", "name", "cpu_s", "state", "priority"]
PROCESS_FIELDS = ["wall", "pid", "name", "cores"]
OTHERS_MIN_CORES = 0.05  # a process using more between two lines goes into processes.csv
XPLANE_NAMES = ("X-Plane", "X-Plane-x86_64", "X-Plane.exe")
WEBKIT = "com.apple.WebKit."  # a Mac's web view processes: WebContent, GPU, Networking
WEBVIEW2 = "msedgewebview2.exe"  # Windows': under the program that shows the window
# a thread's state, as the kernel says it (proc_threadinfo's pth_run_state)
THREAD_STATES = {1: "running", 2: "stopped", 3: "waiting", 4: "uninterruptible", 5: "halted"}
# the Mac's heat, as it tells its apps (com.apple.system.thermalpressurelevel)
HEAT = {0: "nominal", 1: "moderate", 2: "heavy", 3: "trapping", 4: "sleeping"}
# the system's word on its memory (kern.memorystatus_vm_pressure_level)
PRESSURE = {1: "normal", 2: "warning", 4: "critical"}


def _run(cmd: list[str]) -> str:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout if done.returncode == 0 else ""


def cpu_seconds(text: str) -> float:
    """``ps``'s cumulative time: ``MM:SS.ss``, or ``H:MM:SS`` and ``D-HH:MM:SS`` when long."""
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    seconds = 0.0
    for part in text.split(":"):
        seconds = seconds * 60 + float(part)
    return days * 86400 + seconds


WINDOWS = sys.platform == "win32"


def processes() -> list[tuple[int, int, float, float, str]]:
    """(pid, parent, memory MB, CPU s, command) of every process."""
    if WINDOWS:
        import sampler_windows

        return sampler_windows.processes()
    rows = []
    # -S: the time of the children that ended counted in their parent's (Live's 8192 px workers
    # end after each texture: without it, their work would be lost)
    for line in _run(["ps", "-axS", "-o", "pid=,ppid=,rss=,time=,comm="]).splitlines():
        parts = line.split(None, 4)
        if len(parts) == 5:
            try:
                rows.append(
                    (
                        int(parts[0]),
                        int(parts[1]),
                        int(parts[2]) / 1024,
                        cpu_seconds(parts[3]),
                        parts[4],
                    )
                )
            except ValueError:
                continue
    return rows


def _members(rows: Iterable[tuple[int, int, float, float, str]], root: int) -> set[int]:
    """``root`` and every process under it."""
    children: dict[int, list[int]] = {}
    for pid, parent, *_ in rows:
        children.setdefault(parent, []).append(pid)
    wanted, todo = set(), [root]
    while todo:
        pid = todo.pop()
        if pid not in wanted:
            wanted.add(pid)
            todo += children.get(pid, [])
    return wanted


def tree(rows: Iterable[tuple[int, int, float, float, str]], root: int) -> tuple[float, float]:
    """Memory MB and CPU s of ``root`` and every process under it."""
    rows = list(rows)
    wanted = _members(rows, root)
    mem = sum(r[2] for r in rows if r[0] in wanted)
    cpu = sum(r[3] for r in rows if r[0] in wanted)
    return mem, cpu


def net_in() -> int:
    """Bytes received by the network interfaces (en*), since they came up."""
    if WINDOWS:
        import sampler_windows

        with contextlib.suppress(OSError, AttributeError):
            return sampler_windows.net_in()
        return 0
    total, seen = 0, set()
    for line in _run(["netstat", "-ibn"]).splitlines():
        parts = line.split()
        if (
            len(parts) >= 7
            and parts[0].startswith("en")
            and "<Link#" in parts[2]
            and parts[0] not in seen
        ):
            seen.add(parts[0])
            with contextlib.suppress(ValueError):
                total += int(parts[6])
    return total


def disk_bytes(text: str | None = None) -> dict[str, tuple[int, int]]:
    """(bytes written, bytes read) through each disk driver since the computer started, under the
    name of the first disk below it (disk0: the Mac's own SSD); on Windows each fixed volume's."""
    if WINDOWS and text is None:
        import sampler_windows

        with contextlib.suppress(OSError, AttributeError):
            return sampler_windows.disk_bytes()
        return {}
    if text is None:
        text = _run(["ioreg", "-c", "IOBlockStorageDriver", "-r", "-w", "0", "-l"])
    out: dict[str, tuple[int, int]] = {}
    write = read = name = None
    for line in text.splitlines():
        if line.startswith("+-o IOBlockStorageDriver"):
            if write is not None and name:
                out[name] = (write, read or 0)
            write = read = name = None
            continue
        m = re.search(r'"Bytes \(Write\)"=(\d+)', line)
        if m and write is None:
            write = int(m[1])
        m = re.search(r'"Bytes \(Read\)"=(\d+)', line)
        if m and read is None:
            read = int(m[1])
        m = re.search(r'"BSD Name" = "(disk\d+)"', line)
        if m and name is None:
            name = m[1]
    if write is not None and name:
        out[name] = (write, read or 0)
    return out


def disk_writes() -> dict[str, int]:
    """Bytes written through each disk driver since the computer started (``disk_bytes``)."""
    return {name: w for name, (w, _) in disk_bytes().items()}


# --- X-Plane and the Mac, from outside (macOS: libproc, sysctl, notify) -------------------------

_LIBS: dict[str, ctypes.CDLL | None] = {}


def _lib(name: str) -> ctypes.CDLL | None:
    """libproc or the C library, None where there is none (not a Mac)."""
    if name not in _LIBS:
        _LIBS[name] = None
        if sys.platform == "darwin":
            path = ctypes.util.find_library(name) if name == "proc" else None
            with contextlib.suppress(OSError):
                _LIBS[name] = ctypes.CDLL(path, use_errno=True)
    return _LIBS[name]


class _ThreadInfo(ctypes.Structure):  # struct proc_threadinfo
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


class _Usage(ctypes.Structure):  # struct rusage_info_v4, as far as what is read here
    _fields_ = [
        ("uuid", ctypes.c_uint8 * 16),
        ("user_time", ctypes.c_uint64),
        ("system_time", ctypes.c_uint64),
        ("pkg_idle_wkups", ctypes.c_uint64),
        ("interrupt_wkups", ctypes.c_uint64),
        ("pageins", ctypes.c_uint64),
        ("wired_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("phys_footprint", ctypes.c_uint64),
        ("proc_start_abstime", ctypes.c_uint64),
        ("proc_exit_abstime", ctypes.c_uint64),
        ("child_user_time", ctypes.c_uint64),
        ("child_system_time", ctypes.c_uint64),
        ("child_pkg_idle_wkups", ctypes.c_uint64),
        ("child_interrupt_wkups", ctypes.c_uint64),
        ("child_pageins", ctypes.c_uint64),
        ("child_elapsed_abstime", ctypes.c_uint64),
        ("diskio_bytesread", ctypes.c_uint64),
        ("diskio_byteswritten", ctypes.c_uint64),
        ("rest", ctypes.c_uint64 * 32),  # the fields after, room for the kernel to fill
    ]


PROC_PIDTHREADINFO, PROC_PIDLISTTHREADS, RUSAGE_INFO_V4 = 5, 6, 4

_RESPONSIBLE: dict[tuple[int, str], int | None] = {}


def responsible(pid: int, command: str = "") -> int | None:
    """The program ``pid`` works for, as macOS keeps it (``responsibility_get_pid_responsible_for_
    pid``): a web view's processes are started by the system, not under the program that shows the
    page, and this names that program. None off a Mac, or where it will not say."""
    key = (pid, command)
    if key not in _RESPONSIBLE:
        _RESPONSIBLE[key] = None
        if sys.platform == "darwin":
            with contextlib.suppress(OSError, AttributeError):
                lib = ctypes.CDLL("/usr/lib/system/libquarantine.dylib")
                ask = lib.responsibility_get_pid_responsible_for_pid
                ask.argtypes, ask.restype = [ctypes.c_int], ctypes.c_int
                found = ask(pid)
                _RESPONSIBLE[key] = found if found > 0 else None
    return _RESPONSIBLE[key]


def web_view(
    rows: Iterable[tuple[int, int, float, float, str]], app: int
) -> list[tuple[int, int, float, float, str]]:
    """The processes of the web view of the program ``app``: on a Mac WebKit's working for it, on
    Windows WebView2's under it."""
    rows = list(rows)
    if sys.platform == "darwin":
        return [r for r in rows if WEBKIT in r[4] and responsible(r[0], r[4]) == app]
    under = _members(rows, app)
    return [
        r
        for r in rows
        if r[0] in under and r[4].replace("\\", "/").rsplit("/", 1)[-1].lower() == WEBVIEW2
    ]


def threads(pid: int) -> list[tuple[int, str, float, int, int]]:
    """(thread, name, processor s, state, priority) of each of a process's threads: libproc's
    word, as for Activity Monitor; [] where it cannot be read."""
    if WINDOWS:
        import sampler_windows

        return sampler_windows.threads(pid)
    lib = _lib("proc")
    if lib is None:
        return []
    handles = (ctypes.c_uint64 * 4096)()
    size = lib.proc_pidinfo(
        pid, PROC_PIDLISTTHREADS, ctypes.c_uint64(0), handles, ctypes.sizeof(handles)
    )
    out = []
    for handle in handles[: max(0, size) // 8]:
        info = _ThreadInfo()
        got = lib.proc_pidinfo(
            pid,
            PROC_PIDTHREADINFO,
            ctypes.c_uint64(handle),
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if got == ctypes.sizeof(info):
            out.append(
                (
                    handle,
                    info.name.decode(errors="replace"),
                    (info.user_ns + info.system_ns) / 1e9,
                    info.run_state,
                    info.curpri,
                )
            )
    return out


def usage(pid: int) -> dict[str, int]:
    """A process's whole memory (phys_footprint: its GPU's allocations included, as Activity
    Monitor's Memory column), the bytes it read from and wrote to the disks, its page-ins; {} where
    they cannot be read."""
    if WINDOWS:
        import sampler_windows

        return sampler_windows.usage(pid)
    lib = _lib("proc")
    if lib is None:
        return {}
    info = _Usage()
    if lib.proc_pid_rusage(pid, RUSAGE_INFO_V4, ctypes.byref(info)) != 0:
        return {}
    return {
        "footprint": info.phys_footprint,
        "read": info.diskio_bytesread,
        "written": info.diskio_byteswritten,
        "pageins": info.pageins,
    }


def sysctl_int(name: str) -> int | None:
    lib = _lib("c")
    if lib is None:
        return None
    value, size = ctypes.c_int64(0), ctypes.c_size_t(8)
    if lib.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
        return None
    return value.value


class _Swap(ctypes.Structure):  # struct xsw_usage
    _fields_ = [
        ("total", ctypes.c_uint64),
        ("avail", ctypes.c_uint64),
        ("used", ctypes.c_uint64),
        ("pagesize", ctypes.c_uint32),
        ("encrypted", ctypes.c_uint32),
    ]


_heat_token: ctypes.c_int | None = None


def heat() -> int | None:
    """The Mac's heat as it tells its apps (HEAT: 2 is heavy, the processors slowed)."""
    global _heat_token
    lib = _lib("c")
    if lib is None:
        return None
    if _heat_token is None:
        token = ctypes.c_int(0)
        if lib.notify_register_check(b"com.apple.system.thermalpressurelevel", ctypes.byref(token)):
            return None
        _heat_token = token
    state = ctypes.c_uint64(0)
    if lib.notify_get_state(_heat_token, ctypes.byref(state)) != 0:
        return None
    return state.value


def memory() -> dict[str, int | None]:
    """The Mac's memory: free (%, as the system reckons it), its pressure (PRESSURE), MB held
    compressed and MB swapped out to the disk."""
    if WINDOWS:
        import sampler_windows

        return sampler_windows.memory()
    page = sysctl_int("vm.pagesize") or 16384
    compressed = sysctl_int("vm.compressor.pages_compressed")
    swap = _Swap()
    size = ctypes.c_size_t(ctypes.sizeof(swap))
    lib = _lib("c")
    swapped = None
    if lib is not None and not lib.sysctlbyname(
        b"vm.swapusage", ctypes.byref(swap), ctypes.byref(size), None, 0
    ):
        swapped = swap.used
    return {
        "free_pct": sysctl_int("kern.memorystatus_level"),
        "pressure": sysctl_int("kern.memorystatus_vm_pressure_level"),
        "compressed_mb": None if compressed is None else compressed * page // 2**20,
        "swapped_mb": None if swapped is None else swapped // 2**20,
    }


def cores_allowed() -> int | None:
    """How many cores the system lets work now (``kern.sched_recommended_cores``, a mask: all of
    them unless it takes some away, hot), None where it does not say."""
    mask = sysctl_int("kern.sched_recommended_cores")
    return None if mask is None else bin(mask).count("1")


def busy_processes(
    rows: Iterable[tuple[int, int, float, float, str]],
    before: dict[int, tuple[float, float]],
    wall: float,
) -> list[list]:
    """The lines of ``processes.csv``: each process that used more than OTHERS_MIN_CORES since the
    line before (``before``: each pid's CPU s and when, updated here)."""
    out = []
    for pid, _, _, cpu, command in rows:
        then = before.get(pid)
        before[pid] = (cpu, wall)
        if then is None or wall <= then[1]:
            continue
        cores = (cpu - then[0]) / (wall - then[1])
        if cores > OTHERS_MIN_CORES:
            out.append([f"{wall:.1f}", pid, Path(command).name, f"{cores:.2f}"])
    return out


def whole_disk(path: Path) -> str | None:
    """The physical disk a folder is on (disk0), as ``disk_writes`` names it; its volume on
    Windows ("C:")."""
    if WINDOWS:
        import sampler_windows

        return sampler_windows.whole_disk(path)
    lines = _run(["df", "-P", str(path)]).splitlines()
    m = re.match(r"/dev/(disk\d+)", lines[-1]) if len(lines) > 1 else None
    if not m:
        return None
    store = _run(["diskutil", "apfs", "list", m[1]])
    physical = re.search(r"Physical Store\s+(disk\d+)", store)  # an APFS container's own disk
    return physical[1] if physical else m[1]


def _blank(value: object) -> object:
    return "" if value is None else value


class Sampler(threading.Thread):
    """``system.csv``, one line every SAMPLE_S until stopped, and ``threads.csv`` and
    ``processes.csv`` beside it: X-Plane's threads and the busy processes at each line (the
    module's docstring)."""

    def __init__(
        self,
        out: Path,
        streamer_pid: int | None = None,
        *,
        engine_pid: int | None = None,
        web_of: int | None = None,
    ):
        """``engine_pid``: OrthoStudio XP's engine, under the app; ``web_of``: the program whose
        window's web view is the app's too (``fly.py --streamer app``)."""
        super().__init__(daemon=True)
        self.out, self.streamer_pid = out, streamer_pid
        self.engine_pid, self.web_of = engine_pid, web_of
        self.threads_out = out.with_name("threads.csv")
        self.processes_out = out.with_name("processes.csv")
        self._before: dict[int, tuple[float, float]] = {}  # each process's CPU s, and when
        # Windows: the streamer's processes that ended (an 8192 px texture's), their time kept, as
        # a Mac's ps -S counts it in their parent's
        self._tree_cpu: dict[int, float] = {}
        self._ended_cpu = 0.0
        self._stop_event = threading.Event()
        self.first = threading.Event()  # its first line written: what follows is measured

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=15)

    def sample(self) -> tuple[list, list[list], list[list]]:
        """The line of ``system.csv``, and those of ``threads.csv`` and ``processes.csv``."""
        wall = time.time()  # when it began: the counters are read after it
        rows = processes()
        xp = [r for r in rows if Path(r[4]).name in XPLANE_NAMES]
        xp_mem = sum(r[2] for r in xp)
        xp_cpu = sum(r[3] for r in xp)
        s_mem, s_cpu = tree(rows, self.streamer_pid) if self.streamer_pid else (0.0, 0.0)
        if WINDOWS and self.streamer_pid:
            s_cpu += self._ended(rows)
        web = web_view(rows, self.web_of) if self.web_of else []
        if web and sys.platform == "darwin":  # not under the app there: counted in by hand
            s_mem += sum(r[2] for r in web)
            s_cpu += sum(r[3] for r in web)
        engine = tree(rows, self.engine_pid) if self.engine_pid else None
        disks = disk_bytes()
        pid = max(xp, key=lambda r: r[2])[0] if xp else None  # X-Plane itself, not a helper
        use = usage(pid) if pid else {}
        mem = memory()
        line = [
            f"{wall:.1f}",
            f"{xp_mem:.0f}",
            f"{xp_cpu:.1f}",
            f"{s_mem:.0f}",
            f"{s_cpu:.1f}",
            net_in(),
            ";".join(f"{k}={w}" for k, (w, _) in sorted(disks.items())),
            _blank(use.get("footprint", 0) // 2**20 if use else None),
            _blank(use.get("read", 0) // 2**20 if use else None),
            _blank(use.get("pageins")),
            _blank(mem["free_pct"]),
            _blank(mem["pressure"]),
            _blank(mem["compressed_mb"]),
            _blank(mem["swapped_mb"]),
            _blank(heat()),
            ";".join(f"{k}={r}" for k, (_, r) in sorted(disks.items())),
            _blank(cores_allowed()),
            f"{engine[0]:.0f}" if engine else "",
            f"{engine[1]:.1f}" if engine else "",
            f"{sum(r[2] for r in web):.0f}" if self.web_of else "",
            f"{sum(r[3] for r in web):.1f}" if self.web_of else "",
        ]
        thread_lines = [
            [f"{wall:.1f}", f"{handle:x}", name, f"{cpu:.3f}", state, priority]
            for handle, name, cpu, state, priority in (threads(pid) if pid else [])
        ]
        return line, thread_lines, busy_processes(rows, self._before, wall)

    def _ended(self, rows: list[tuple[int, int, float, float, str]]) -> float:
        """The processor time of the streamer's processes that have ended since it started."""
        under = {r[0]: r[3] for r in rows}  # each process's CPU s
        members = _members(rows, self.streamer_pid) if self.streamer_pid else set()
        for pid, cpu in list(self._tree_cpu.items()):
            if pid not in under:  # ended
                self._ended_cpu += cpu
                del self._tree_cpu[pid]
        for pid in members & under.keys():  # the streamer itself may have ended too
            self._tree_cpu[pid] = under[pid]
        return self._ended_cpu

    def run(self) -> None:
        with (
            self.out.open("w", newline="") as f,
            self.threads_out.open("w", newline="") as t,
            self.processes_out.open("w", newline="") as pr,
        ):
            w, tw, pw = csv.writer(f), csv.writer(t), csv.writer(pr)
            w.writerow(FIELDS)
            tw.writerow(THREAD_FIELDS)
            pw.writerow(PROCESS_FIELDS)
            while True:
                line, thread_lines, process_lines = self.sample()
                w.writerow(line)
                tw.writerows(thread_lines)
                pw.writerows(process_lines)
                f.flush()
                t.flush()
                pr.flush()
                self.first.set()
                if self._stop_event.is_set():
                    return  # a last line once stopped: the end of what was measured
                self._stop_event.wait(SAMPLE_S)
