# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The computer during a test flight, every SAMPLE_S, into ``system.csv``: X-Plane's and the
streamer's memory and CPU time (the streamer with every process it started), the bytes received
from the network, and the bytes written to each disk since the computer started. The report turns
them into what the start and the path cost. macOS for now (``ps``, ``netstat``, ``ioreg``)."""

from __future__ import annotations

import contextlib
import csv
import re
import subprocess
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
]
XPLANE_NAMES = ("X-Plane", "X-Plane-x86_64", "X-Plane.exe")


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


def processes() -> list[tuple[int, int, float, float, str]]:
    """(pid, parent, memory MB, CPU s, command) of every process."""
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


def tree(rows: Iterable[tuple[int, int, float, float, str]], root: int) -> tuple[float, float]:
    """Memory MB and CPU s of ``root`` and every process under it."""
    rows = list(rows)
    children: dict[int, list[int]] = {}
    for pid, parent, *_ in rows:
        children.setdefault(parent, []).append(pid)
    wanted, todo = set(), [root]
    while todo:
        pid = todo.pop()
        if pid not in wanted:
            wanted.add(pid)
            todo += children.get(pid, [])
    mem = sum(r[2] for r in rows if r[0] in wanted)
    cpu = sum(r[3] for r in rows if r[0] in wanted)
    return mem, cpu


def net_in() -> int:
    """Bytes received by the network interfaces (en*), since they came up."""
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


def disk_writes() -> dict[str, int]:
    """Bytes written through each disk driver since the computer started, under the name of the
    first disk below it (disk0: the Mac's own SSD)."""
    out: dict[str, int] = {}
    write, name = None, None
    for line in _run(["ioreg", "-c", "IOBlockStorageDriver", "-r", "-w", "0", "-l"]).splitlines():
        if line.startswith("+-o IOBlockStorageDriver"):
            if write is not None and name:
                out[name] = write
            write, name = None, None
            continue
        m = re.search(r'"Bytes \(Write\)"=(\d+)', line)
        if m and write is None:
            write = int(m[1])
        m = re.search(r'"BSD Name" = "(disk\d+)"', line)
        if m and name is None:
            name = m[1]
    if write is not None and name:
        out[name] = write
    return out


def whole_disk(path: Path) -> str | None:
    """The physical disk a folder is on (disk0), as ``disk_writes`` names it."""
    lines = _run(["df", "-P", str(path)]).splitlines()
    m = re.match(r"/dev/(disk\d+)", lines[-1]) if len(lines) > 1 else None
    if not m:
        return None
    store = _run(["diskutil", "apfs", "list", m[1]])
    physical = re.search(r"Physical Store\s+(disk\d+)", store)  # an APFS container's own disk
    return physical[1] if physical else m[1]


class Sampler(threading.Thread):
    """``system.csv``, one line every SAMPLE_S until stopped."""

    def __init__(self, out: Path, streamer_pid: int | None = None):
        super().__init__(daemon=True)
        self.out, self.streamer_pid = out, streamer_pid
        self._stop_event = threading.Event()
        self.first = threading.Event()  # its first line written: what follows is measured

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=15)

    def sample(self) -> list:
        wall = time.time()  # when it began: the counters are read after it
        rows = processes()
        xp = [r for r in rows if Path(r[4]).name in XPLANE_NAMES]
        xp_mem = sum(r[2] for r in xp)
        xp_cpu = sum(r[3] for r in xp)
        s_mem, s_cpu = tree(rows, self.streamer_pid) if self.streamer_pid else (0.0, 0.0)
        disks = ";".join(f"{k}={v}" for k, v in sorted(disk_writes().items()))
        return [
            f"{wall:.1f}",
            f"{xp_mem:.0f}",
            f"{xp_cpu:.1f}",
            f"{s_mem:.0f}",
            f"{s_cpu:.1f}",
            net_in(),
            disks,
        ]

    def run(self) -> None:
        with self.out.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(FIELDS)
            while True:
                w.writerow(self.sample())
                f.flush()
                self.first.set()
                if self._stop_event.is_set():
                    return  # a last line once stopped: the end of what was measured
                self._stop_event.wait(SAMPLE_S)
