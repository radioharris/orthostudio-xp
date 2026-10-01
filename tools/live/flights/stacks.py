# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What X-Plane's threads compute while a test flight's scenery load waits (2026-10-01), taken with
the user's password: nothing else can look into X-Plane, signed with the hardened runtime
(``sample`` cannot attach to it).

    sudo /usr/bin/python3 tools/live/flights/stacks.py

Started once before a series of test flights (``fly.py``), it waits for X-Plane, follows its log,
and for each flight takes:

- X-Plane's threads' stacks (``spindump``, 10 s every 10 ms, X-Plane alone): once in cruise, and
  during each scenery load from FIRST_S after it began, then every EVERY_S until it ends. The
  analysis of 2026-10-01 found X-Plane's 7 background workers busy for up to 7 minutes while the
  third tile of a column waited, and nothing says on what;
- once the series is over, the kernel's own light samples of X-Plane over each whole flight
  (``spindump -microstackshots``), where the system keeps them;

and over the whole session the processors' and the graphics' power and frequencies and the
thermal pressure every 5 s (``powermetrics``). It stops when the series says it is done
(``build/flights/stacks/series-done``), after ``--hours``, or at Ctrl-C. It changes nothing: it
reads X-Plane's log and samples. Its files, in ``build/flights/stacks/<date time>/``, are given to
the user who ran ``sudo``. Python 3.9 at least (macOS's own), the standard library only.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import re
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
STACKS = REPO / "build" / "flights" / "stacks"
WATCHING = "watching.json"  # in STACKS while it watches: where its files go
SERIES_DONE = "series-done"  # in STACKS, written by the series once its last flight has stopped
PILOT = "OrthoStudio XP Test Pilot: "
LOAD = re.compile(r"scenery load at (-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?), path (-?\d+(?:\.\d+)?) s")
LOADED = re.compile(r"scenery loaded in (\d+(?:\.\d+)?) s")
MESH = re.compile(
    r"DSF load time: (\d+) for file .*/Earth nav data/[^/]+/([+-]\d\d[+-]\d\d\d)\.dsf \((\d+) tris"
)
FIRST_S = 40.0  # a load's first sample, this long after it began: past a load that goes well
EVERY_S = 75.0  # then one this often while it lasts
CRUISE_FROM_S = 300.0  # the path's time from which the cruise sample may be taken...
QUIET_S = 60.0  # ... once no load has run for this long
MOST = 12  # samples of a flight at most
SPINDUMP = "/usr/sbin/spindump"
POWERMETRICS = "/usr/bin/powermetrics"
SAMPLE = ["10", "10"]  # spindump's duration (s) and interval (ms)


def owner() -> tuple[int, int]:
    """The user who ran ``sudo``: the files are theirs."""
    return int(os.environ.get("SUDO_UID", os.getuid())), int(
        os.environ.get("SUDO_GID", os.getgid())
    )


def give(path: Path) -> None:
    uid, gid = owner()
    with contextlib.suppress(OSError):
        os.chown(path, uid, gid)


def xplane_pid() -> int | None:
    done = subprocess.run(["/usr/bin/pgrep", "-x", "X-Plane"], capture_output=True, text=True)
    pids = [int(p) for p in done.stdout.split() if p.isdigit()]
    return min(pids) if pids else None


class Session:
    """The files of one watch, and what it says as it goes."""

    def __init__(self, out: Path):
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        give(out.parent)
        give(out)
        self.log_path = out / "watch.log"
        self.samples_path = out / "samples.csv"
        with self.samples_path.open("w", newline="") as f:
            csv.writer(f).writerow(
                ["wall", "flight", "xplane_pid", "label", "path_s", "load", "load_s", "meshes",
                 "took_s", "file", "outcome"]
            )  # fmt: skip
        give(self.samples_path)

    def say(self, text: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {text}"
        print(line, flush=True)
        with self.log_path.open("a") as f:
            f.write(line + "\n")
        give(self.log_path)

    def row(self, values: list) -> None:
        with self.samples_path.open("a", newline="") as f:
            csv.writer(f).writerow(values)


class Flight:
    """One X-Plane run: its log followed, its loads, its samples."""

    def __init__(self, n: int, pid: int):
        self.n, self.pid = n, pid
        self.began = time.time()
        self.path_wall: float | None = None  # the test pilot began the path then
        self.done = False  # the path flown: no more samples
        self.load: dict | None = None  # the load going on
        self.loads = 0
        self.last_load_end = 0.0
        self.samples = 0
        self.cruise_taken = False
        self.ended: float | None = None  # X-Plane gone then
        self.offset = 0
        self.inode: int | None = None
        self.rest = b""

    def path_s(self, now: float) -> float | None:
        return None if self.path_wall is None else now - self.path_wall


def new_lines(log: Path, flight: Flight) -> list[str]:
    """X-Plane's log lines written since the last call (it writes a new log at each start)."""
    try:
        st = log.stat()
    except OSError:
        return []
    if flight.inode != st.st_ino or st.st_size < flight.offset:
        flight.inode, flight.offset, flight.rest = st.st_ino, 0, b""
    if st.st_size == flight.offset:
        return []
    with log.open("rb") as f:
        f.seek(flight.offset)
        data = f.read()
    flight.offset += len(data)
    data = flight.rest + data
    lines = data.split(b"\n")
    flight.rest = lines.pop()  # a line not ended yet
    return [line.decode(errors="replace") for line in lines]


def read(flight: Flight, lines: list[str], now: float, session: Session) -> None:
    """The test pilot's marks and X-Plane's mesh DSFs, in the flight's state."""
    for line in lines:
        if line.startswith(PILOT):
            said = line[len(PILOT) :]
            m = LOAD.search(said)
            if said.startswith("flying the path"):
                flight.path_wall = now
                session.say(f"flight {flight.n}: the path begins")
            elif m:
                flight.loads += 1
                flight.load = {"n": flight.loads, "began": now, "path": float(m.group(3)),
                               "next": now + FIRST_S, "meshes": []}  # fmt: skip
                session.say(f"flight {flight.n}: load {flight.loads} at path {m.group(3)} s")
            elif LOADED.search(said):
                if flight.load is not None:
                    session.say(f"flight {flight.n}: load {flight.load['n']} over")
                flight.load, flight.last_load_end = None, now
            elif said.startswith(("the path flown", "done")):
                flight.done = True
        else:
            m = MESH.search(line)
            if m and int(m.group(3)) > 0 and flight.load is not None:
                flight.load["meshes"].append(m.group(2))


def spindump(flight: Flight, label: str, session: Session) -> None:
    """X-Plane's threads' stacks, SAMPLE long, into the session's folder."""
    now = time.time()
    path = flight.path_s(now)
    load = flight.load
    name = f"{flight.n}-{label}.spindump.txt"
    began = time.monotonic()
    try:
        done = subprocess.run(
            [SPINDUMP, str(flight.pid), *SAMPLE, "-onlyTarget", "-noProcessingWhileSampling",
             "-o", str(session.out / name)],
            capture_output=True, text=True, timeout=240,
        )  # fmt: skip
        outcome = "ok" if done.returncode == 0 else f"exit {done.returncode}: {done.stderr[-200:]}"
    except (OSError, subprocess.TimeoutExpired) as e:
        outcome = f"failed: {e}"
    took = time.monotonic() - began
    give(session.out / name)
    flight.samples += 1
    session.row(
        [f"{now:.1f}", flight.n, flight.pid, label, "" if path is None else f"{path:.0f}",
         "" if load is None else load["n"], "" if load is None else f"{now - load['began']:.0f}",
         "" if load is None else " ".join(load["meshes"]), f"{took:.1f}", name, outcome]
    )  # fmt: skip
    session.say(f"flight {flight.n}: {label} sampled in {took:.0f} s ({outcome})")


def maybe_sample(flight: Flight, session: Session) -> None:
    """A sample when one is due: during a load that lasts, or once in cruise."""
    now = time.time()
    path = flight.path_s(now)
    if path is None or flight.done or flight.samples >= MOST:
        return
    load = flight.load
    if load is not None:
        if now >= load["next"]:
            spindump(flight, f"load{load['n']}-{now - load['began']:.0f}s", session)
            load["next"] = now + EVERY_S
    elif (
        not flight.cruise_taken
        and path >= CRUISE_FROM_S
        and now - max(flight.last_load_end, flight.path_wall or 0.0) >= QUIET_S
    ):
        flight.cruise_taken = True
        spindump(flight, f"cruise-{path:.0f}s", session)


def microstackshots(flight: Flight, session: Session) -> None:
    """The kernel's light samples of X-Plane over the whole flight, once the series is over (not
    between two flights: the next one's start would be missed meanwhile)."""
    name = f"{flight.n}-microstackshots.txt"
    try:
        done = subprocess.run(
            [SPINDUMP, "-microstackshots", "-microstackshots_pid", str(flight.pid),
             "-microstackshots_starttime", f"{flight.began:.0f}",
             "-microstackshots_endtime", f"{flight.ended or time.time():.0f}",
             "-o", str(session.out / name)],
            capture_output=True, text=True, timeout=600,
        )  # fmt: skip
        outcome = "ok" if done.returncode == 0 else f"exit {done.returncode}: {done.stderr[-200:]}"
    except (OSError, subprocess.TimeoutExpired) as e:
        outcome = f"failed: {e}"
    give(session.out / name)
    session.say(f"flight {flight.n}: the kernel's samples of the flight ({outcome})")


def powermetrics(session: Session) -> subprocess.Popen | None:
    """The processors' and the graphics' power and frequencies, and the thermal pressure, every
    5 s for the whole session."""
    out = session.out / "powermetrics.plist"
    try:
        proc = subprocess.Popen(
            [POWERMETRICS, "--samplers", "cpu_power,gpu_power,thermal", "-i", "5000",
             "-f", "plist", "-o", str(out)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )  # fmt: skip
    except OSError as e:
        session.say(f"powermetrics not started: {e}")
        return None
    time.sleep(3)
    if proc.poll() is not None:
        err = proc.stderr.read().decode(errors="replace")[-300:] if proc.stderr else ""
        session.say(f"powermetrics stopped at once: {err}")
        return None
    give(out)
    return proc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    user = os.environ.get("SUDO_USER") or os.environ.get("USER", "")
    p.add_argument("--xplane", type=Path, default=Path("/Users") / user / "X-Plane 12")
    p.add_argument("--hours", type=float, default=4.0, help="stop after this long at most")
    a = p.parse_args(argv)
    if os.geteuid() != 0:
        print("run it with sudo: spindump and powermetrics need the administrator's rights")
        return 2
    STACKS.mkdir(parents=True, exist_ok=True)
    give(STACKS)
    with contextlib.suppress(OSError):
        (STACKS / SERIES_DONE).unlink()  # an earlier series'
    session = Session(STACKS / datetime.now().strftime("%Y-%m-%d %H%M%S"))
    watching = STACKS / WATCHING
    watching.write_text(
        json.dumps({"out": str(session.out), "pid": os.getpid(), "since": time.time()})
    )
    give(watching)
    session.say(f"watching for X-Plane ({a.xplane}); files in {session.out}")
    stop = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.append(True))
    power = powermetrics(session)
    deadline = time.time() + a.hours * 3600
    flight: Flight | None = None
    flown: list[Flight] = []
    log = a.xplane / "Log.txt"
    try:
        while not stop and time.time() < deadline:
            pid = xplane_pid()
            if flight is not None and pid != flight.pid:
                flight.ended = time.time()
                session.say(f"flight {flight.n}: X-Plane gone")
                flight = None
            if pid is not None and flight is None:
                flight = Flight(len(flown) + 1, pid)
                flown.append(flight)
                session.say(f"flight {flight.n}: X-Plane up (pid {pid})")
            if flight is not None:
                read(flight, new_lines(log, flight), time.time(), session)
                maybe_sample(flight, session)
            elif (STACKS / SERIES_DONE).exists():
                session.say("the series is done")
                break
            time.sleep(1.0)
    finally:
        if power is not None:
            power.send_signal(signal.SIGINT)
            with contextlib.suppress(subprocess.TimeoutExpired):
                power.wait(timeout=20)
        with contextlib.suppress(OSError):
            watching.unlink()
        for each in flown:
            microstackshots(each, session)
        for path in session.out.iterdir():
            give(path)
        session.say("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
