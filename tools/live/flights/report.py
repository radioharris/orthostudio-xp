# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What a test flight saw, from the files it left in its folder (``fly.py``):

- ``Log.txt``, X-Plane's log from the scenario's start: the DSF tiles X-Plane loaded, each load
  marked by the test pilot where it started ("scenery load at LAT LON, path T s"), so the tiles
  logged after a mark came with it; those before the first mark are the start's;
- ``frames.csv`` and ``events.txt``, the test pilot's: every frame of the path, and what it did;
- ``run.json``, the runner's: how long X-Plane took to start and fly;
- ``system.csv``, the computer every few seconds (``sampler.py``): what the start and the path
  cost in downloads, disk writes, processor and memory;
- ``threads.csv``, X-Plane's threads at the same moments (``sampler.py``, since 2026-09-30);
- ``live.txt`` or ``autoortho.txt``, the streamer's output, when one ran.

The report says, for each load, where the aircraft was, which tiles came and when, which of them
X-Plane had loaded before in the flight (so had let go of since), and whether Live's model of
X-Plane's window (``orthostudio.live.evict.xplane_tiles``) held them: a tile X-Plane loads outside
it could have its texture made coarser while X-Plane shows it. Then what X-Plane and the Mac did
during each load: X-Plane's busiest threads against their pace outside the loads, what it read from
the disks, the Mac's memory and heat (a tile of Norway came 7.5 minutes after the two others of
its column, 2026-09-30, and X-Plane's log says nothing of why).

    uv run python tools/live/flights/report.py <a flight's folder>
"""

from __future__ import annotations

import contextlib
import csv
import itertools
import json
import math
import re
import statistics
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PILOT = "OrthoStudio XP Test Pilot: "
MARK = re.compile(r"scenery load at (-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?), path (-?\d+(?:\.\d+)?) s")
LOADED = re.compile(r"scenery loaded in (\d+(?:\.\d+)?) s")
DSF = re.compile(
    r"DSF load time: \d+ for file (.*?)/Earth nav data/[^/]+/([+-]\d\d)([+-]\d\d\d)\.dsf"
)
STAMP = re.compile(r"^(\d+):(\d\d):(\d\d(?:\.\d+)?) ")  # X-Plane's clock, at a line's start
FLYING = 2  # the test pilot's state, in frames.csv
KM_DEG = 111.195
SHOT_S = 1.0  # a screenshot's own long frame, and the frames just after it: left out
GAP_S = 60.0  # X-Plane loading no tile this long within a load: said

Tile = tuple[int, int]  # (latitude, longitude) of its south-west corner


@dataclass
class Load:
    """One of X-Plane's scenery loads: where the aircraft was when it started (None: the loads
    before the test pilot's first mark, X-Plane's start), and the tiles it brought, by pack."""

    lat: float | None = None
    lon: float | None = None
    path_s: float | None = None  # the path's time; None before the path began
    packs: dict[str, set[Tile]] = field(default_factory=dict)
    seconds: float | None = None  # how long X-Plane took, from the test pilot's end mark
    came: dict[Tile, float] = field(default_factory=dict)  # a tile's last DSF, X-Plane's clock

    @property
    def tiles(self) -> set[Tile]:
        return set().union(*self.packs.values()) if self.packs else set()


def _stamp(line: str) -> float | None:
    m = STAMP.match(line)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


def scenery_loads(lines: Iterable[str], launched: bool = True) -> list[Load]:
    """The loads of a scenario's part of Log.txt. X-Plane writes its log late, so a part may begin
    with the end of the flight before it: unless X-Plane was ``launched`` for this scenario (its
    start's loads come before the test pilot reads the scenario), the DSFs before the test pilot's
    "scenario read" are left out."""
    loads = [Load()]
    reading = launched
    for line in lines:
        if PILOT in line:
            reading = reading or "scenario read" in line
            m = MARK.search(line)
            if m:
                path_s = float(m[3])
                loads.append(Load(float(m[1]), float(m[2]), path_s if path_s >= 0 else None))
            m = LOADED.search(line)
            if m:
                loads[-1].seconds = float(m[1])
            continue
        m = DSF.search(line)
        if m and reading:
            pack = m[1].rstrip("/").split("/")[-1]
            tile = (int(m[2]), int(m[3]))
            loads[-1].packs.setdefault(pack, set()).add(tile)
            t = _stamp(line)
            if t is not None:
                loads[-1].came[tile] = max(t, loads[-1].came.get(tile, t))
    return loads


def arrival_lines(ld: Load) -> list[str]:
    """When each tile of a load came, from its first (X-Plane's clock: its last DSF logged), and
    the gaps of more than GAP_S where X-Plane loaded none."""
    if len(ld.came) < 2:
        return []
    order = sorted(ld.came.items(), key=lambda kv: kv[1])
    first = order[0][1]
    out = [
        "  - tiles as they came, from the first: "
        + ", ".join(f"{_fmt_tiles([tile])} {t - first:.0f} s" for tile, t in order)
    ]
    for (_, before), (tile, t) in itertools.pairwise(order):
        if t - before > GAP_S:
            out.append(f"  - NO TILE FOR {t - before:.0f} s, then {_fmt_tiles([tile])}")
    return out


def span(values: Iterable[int]) -> str:
    v = sorted(set(values))
    if not v:
        return "none"
    if v == list(range(v[0], v[-1] + 1)):
        return f"{v[0]}..{v[-1]} ({len(v)})"
    return f"{v} ({len(v)})"


def _fmt_tiles(tiles: Iterable[Tile]) -> str:
    return " ".join(f"{la:+03d}{lo:+04d}" for la, lo in sorted(tiles)) or "-"


@dataclass
class Frames:
    count: int
    seconds: float
    mean_ms: float
    p50_ms: float
    p99_ms: float
    max_ms: float
    over_50: int
    over_100: int
    worst: list[tuple[float, float, int]]  # (path s, frame ms, X-Plane loading)
    shots_left_out: int = 0  # frames of the scenario's screenshots, not counted


def frames_of(path: Path, shots: Iterable[float] = ()) -> Frames | None:
    """The path's frames, those of the scenario's screenshots (``shots``, path seconds) left out:
    a screenshot takes a frame of about 100 ms, the same in every run."""
    if not path.is_file():
        return None
    shots = sorted(shots)
    rows, left_out = [], 0
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            try:
                if int(r["state"]) != FLYING:
                    continue
                row = (float(r["path_s"]), float(r["frame_ms"]), int(r.get("loading") or 0))
            except (KeyError, TypeError, ValueError):
                continue  # a line cut short by X-Plane quitting
            if any(t <= row[0] <= t + SHOT_S for t in shots):
                left_out += 1
                continue
            rows.append(row)
    if len(rows) < 2:
        return None
    ms = sorted(r[1] for r in rows)
    worst = sorted(rows, key=lambda r: -r[1])[:5]
    return Frames(
        shots_left_out=left_out,
        count=len(rows),
        seconds=rows[-1][0] - rows[0][0],
        mean_ms=statistics.fmean(ms),
        p50_ms=ms[len(ms) // 2],
        p99_ms=ms[min(len(ms) - 1, int(len(ms) * 0.99))],
        max_ms=ms[-1],
        over_50=sum(1 for m in ms if m > 50),
        over_100=sum(1 for m in ms if m > 100),
        worst=worst,
    )


def _frac(x: float) -> float:
    return x - math.floor(x)


def window_lines(loads: list[Load], model: Callable[[float, float], set[Tile]] | None) -> list[str]:
    """The loads, one paragraph each (the module's docstring)."""
    out: list[str] = []
    seen: set[Tile] = set()
    before = [ld for ld in loads if ld.path_s is None]
    during = [ld for ld in loads if ld.path_s is not None]
    start_tiles = set().union(*(ld.tiles for ld in before)) if before else set()
    if start_tiles:
        out.append(
            f"- **start**: {len(start_tiles)} tiles, columns {span(lo for _, lo in start_tiles)}, "
            f"rows {span(la for la, _ in start_tiles)}"
        )
        marked = [ld for ld in before if ld.lat is not None]
        if marked:
            ld = marked[-1]
            out.append(f"  - aircraft at {ld.lat:.3f} {ld.lon:+.3f} (placed by the test pilot)")
    else:
        out.append("- **start**: no load (X-Plane was there already)")
    seen |= start_tiles
    for k, ld in enumerate(during, 1):
        tiles = ld.tiles
        assert ld.lat is not None and ld.lon is not None
        again = tiles & seen
        seen |= tiles
        east = [lo for _, lo in tiles if lo > ld.lon]
        west = [lo for _, lo in tiles if lo + 1 <= ld.lon]
        ahead = ""
        if tiles and (east or west) and len({lo for _, lo in tiles}) == 1:
            lo = next(iter({lo for _, lo in tiles}))
            edge = lo - ld.lon if lo > ld.lon else ld.lon - (lo + 1)
            km = edge * KM_DEG * math.cos(math.radians(ld.lat))
            ahead = f", its near edge {edge:.2f} deg ({km:.0f} km) {'east' if east else 'west'}"
        rows = {la for la, _ in tiles}
        row_note = ""
        if tiles and len(rows) == 1:
            la = next(iter(rows))
            edge = la - ld.lat if la > ld.lat else ld.lat - (la + 1)
            side = "north" if la > ld.lat else "south"
            row_note = f", its near edge {edge:.2f} deg ({edge * KM_DEG:.0f} km) {side}"
        out.append(
            f"- **load {k}** at path {ld.path_s:.0f} s, aircraft {ld.lat:.3f} {ld.lon:+.3f} "
            f"(lat x.{_frac(ld.lat) * 1000:03.0f}, lon x.{_frac(ld.lon) * 1000:03.0f}): "
            f"{len(tiles)} tiles" + (f" in {ld.seconds:.1f} s" if ld.seconds is not None else "")
        )
        if tiles:
            out.append(f"  - columns {span(lo for _, lo in tiles)}{ahead}")
            out.append(f"  - rows {span(rows)}{row_note}")
            out.append(f"  - tiles {_fmt_tiles(tiles)}")
            out += arrival_lines(ld)
        if again:
            out.append(f"  - LOADED AGAIN (X-Plane had let go of them): {_fmt_tiles(again)}")
        if model is not None and tiles:
            outside = tiles - model(ld.lat, ld.lon)
            if outside:
                out.append(f"  - OUTSIDE LIVE'S MODEL of X-Plane's window: {_fmt_tiles(outside)}")
            else:
                out.append("  - inside Live's model of X-Plane's window")
    return out


def _events(path: Path) -> list[str]:
    return path.read_text(errors="replace").splitlines() if path.is_file() else []


def _streamer_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = path.read_text(errors="replace").splitlines()
    keep = [
        ln
        for ln in lines
        if re.search(r"(?i)error|traceback|exception|stopped|ahead of live|outran", ln)
    ]
    return [f"{len(lines)} lines of output", *keep[:40]]


def _number(text: str | None) -> float | None:
    """A reading of 2026-09-30's (``sampler.py``): none in an older file, or where it failed."""
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


# system.csv's readings of 2026-09-30 (sampler.FIELDS), under their names here
LATER_FIELDS = {
    "xp_footprint": "xplane_footprint_mb",
    "xp_read": "xplane_read_mb",
    "xp_pageins": "xplane_pageins",
    "free_pct": "mem_free_pct",
    "pressure": "pressure",
    "compressed": "compressed_mb",
    "swapped": "swapped_mb",
    "heat": "heat",
    # 2026-10-04: of the app (the streamer), OrthoStudio XP's engine and the window's web view
    "e_mb": "engine_rss_mb",
    "e_cpu": "engine_cpu_s",
    "w_mb": "web_rss_mb",
    "w_cpu": "web_cpu_s",
    # 2026-10-19
    "xp_written": "xplane_written_mb",
}


def _system_rows(path: Path) -> list[dict]:
    rows = []
    try:
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                try:
                    row = {
                        "wall": float(r["wall"]),
                        "xp_mb": float(r["xplane_rss_mb"]),
                        "xp_cpu": float(r["xplane_cpu_s"]),
                        "s_mb": float(r["streamer_rss_mb"]),
                        "s_cpu": float(r["streamer_cpu_s"]),
                        "net": int(r["net_in"]),
                        "disks": dict(
                            (k, int(v))
                            for k, v in (d.split("=") for d in r["disks"].split(";") if d)
                        ),
                    }
                except (KeyError, ValueError):
                    continue
                for key, name in LATER_FIELDS.items():
                    row[key] = _number(r.get(name))
                rows.append(row)
    except OSError:
        return []
    return rows


def _around(rows: list[dict], t0: float, t1: float) -> tuple[dict, dict, list[dict]] | None:
    """The samples round a moment's span, a few seconds apart: the nearest before its start, the
    nearest after its end, and those between."""
    if not rows:
        return None
    a = ([r for r in rows if r["wall"] <= t0] or rows[:1])[-1]
    b = ([r for r in rows if r["wall"] >= t1] or rows[-1:])[0]
    if b["wall"] <= a["wall"]:
        return None
    return a, b, [r for r in rows if a["wall"] <= r["wall"] <= b["wall"]]


def mac_words(within: list[dict]) -> list[str]:
    """What the Mac went through meanwhile (2026-09-30's readings): its least free memory, its
    memory's worst pressure, the most compressed and swapped out, its worst heat."""

    def most(key: str, lowest: bool = False) -> float | None:
        values = [r[key] for r in within if r.get(key) is not None]
        return (min(values) if lowest else max(values)) if values else None

    out = []
    free = most("free_pct", lowest=True)
    if free is not None:
        out.append(f"memory free at least {free:.0f} %")
    pressure = most("pressure")
    if pressure is not None:
        out.append(f"pressure {PRESSURE_WORDS.get(int(pressure), int(pressure))}")
    compressed = most("compressed")
    if compressed is not None:
        out.append(f"compressed at most {compressed / 1024:.1f} GB")
    swapped = most("swapped")
    if swapped is not None:
        out.append(f"swapped out at most {swapped / 1024:.1f} GB" if swapped else "nothing swapped")
    heat = most("heat")
    if heat is not None:
        out.append(f"heat {HEAT_WORDS.get(int(heat), int(heat))} at worst")
    return out


PRESSURE_WORDS = {1: "normal", 2: "WARNING", 4: "CRITICAL"}  # as sampler.PRESSURE
HEAT_WORDS = {0: "nominal", 1: "moderate", 2: "heavy", 3: "trapping", 4: "sleeping"}  # sampler.HEAT


def costs(rows: list[dict], run: dict) -> dict[str, dict]:
    """What the start (the scenario sent to the path's start) and the path cost the computer
    (``system.csv``): minutes, GB downloaded, GB written to each disk of X-Plane's and the
    streamer's, and X-Plane's and the streamer's cores and memory (GB, median and most)."""
    out: dict[str, dict] = {}
    disks = sorted({d for d in (run.get("disks") or {}).values() if d})
    for name, t0, t1 in (
        ("start", run.get("sent_wall"), run.get("flying_wall")),
        ("path", run.get("flying_wall"), run.get("done_wall")),
    ):
        if t0 is None or t1 is None:
            continue
        # the samples round the phase, a few seconds apart: the nearest there are
        around = _around(rows, t0, t1)
        if around is None:
            continue
        a, b, within = around
        span = max(b["wall"] - a["wall"], 1e-9)
        xp_mem = sorted(r["xp_mb"] for r in within)
        s_mem = sorted(r["s_mb"] for r in within)
        whole = sorted(r["xp_footprint"] for r in within if r.get("xp_footprint") is not None)
        read = None
        if b.get("xp_read") is not None:  # X-Plane started meanwhile: what it read since
            read = (b["xp_read"] - (a.get("xp_read") or 0)) / 1024
        wrote = None
        if b.get("xp_written") is not None:
            wrote = (b["xp_written"] - (a.get("xp_written") or 0)) / 1024
        out[name] = {
            "xplane_whole_gb": whole[len(whole) // 2] / 1024 if whole else None,
            "xplane_whole_gb_most": whole[-1] / 1024 if whole else None,
            "xplane_read_gb": read if read is None or read >= 0 else None,
            "xplane_written_gb": wrote if wrote is None or wrote >= 0 else None,
            "mac": mac_words(within),
            "minutes": (t1 - t0) / 60,
            "downloaded_gb": (b["net"] - a["net"]) / 1e9,
            "written_gb": {
                d: (b["disks"][d] - a["disks"][d]) / 1e9
                for d in disks
                if d in a["disks"] and d in b["disks"]
            },
            "xplane_cores": (b["xp_cpu"] - a["xp_cpu"]) / span,
            "xplane_gb": xp_mem[len(xp_mem) // 2] / 1024,
            "xplane_gb_most": xp_mem[-1] / 1024,
            "streamer": any(s_mem),
            "streamer_cores": (b["s_cpu"] - a["s_cpu"]) / span,
            "streamer_gb": s_mem[len(s_mem) // 2] / 1024,
            "streamer_gb_most": s_mem[-1] / 1024,
            "engine": _part(a, b, within, "e_mb", "e_cpu", span),
            "web": _part(a, b, within, "w_mb", "w_cpu", span),
        }
    return out


def _part(a: dict, b: dict, within: list[dict], mb: str, cpu: str, span: float) -> dict | None:
    """A part of the app's (``sampler``: its engine, its web view) over a phase: its cores, its
    memory (GB, median and most); None where it was not measured."""
    mem = sorted(r[mb] for r in within if r.get(mb) is not None)
    if not mem or a.get(cpu) is None or b.get(cpu) is None:
        return None
    return {
        # never below none: a process's time is read rounded, and an idle engine's went back 0.01 s
        "cores": max(0.0, (b[cpu] - a[cpu]) / span),
        "gb": mem[len(mem) // 2] / 1024,
        "most": mem[-1] / 1024,
    }


def computer_lines(rows: list[dict], run: dict) -> list[str]:
    """The costs (``costs``), a line for the start and one for the path."""
    out = []
    for name, c in costs(rows, run).items():
        parts = [f"downloaded {c['downloaded_gb']:.2f} GB"]
        parts += [f"{d} written {gb:.2f} GB" for d, gb in c["written_gb"].items()]
        parts.append(
            f"X-Plane {c['xplane_cores']:.2f} core, "
            f"{c['xplane_gb']:.1f} GB (most {c['xplane_gb_most']:.1f})"
        )
        if c.get("xplane_whole_gb") is not None:
            parts[-1] += (
                f", {c['xplane_whole_gb']:.1f} GB with its GPU's "
                f"(most {c['xplane_whole_gb_most']:.1f})"
            )
        if c.get("xplane_read_gb") is not None:
            parts[-1] += f", read {c['xplane_read_gb']:.2f} GB"
        if c.get("xplane_written_gb") is not None:
            parts[-1] += f", wrote {c['xplane_written_gb']:.2f} GB"
        if c["streamer"]:
            parts.append(
                f"streamer {c['streamer_cores']:.2f} core, "
                f"{c['streamer_gb']:.1f} GB (most {c['streamer_gb_most']:.1f})"
            )
            for key, what in (("engine", "OrthoStudio XP's engine"), ("web", "its web view")):
                if c.get(key):
                    parts[-1] += (
                        f"; of it, {what} {c[key]['cores']:.2f} core, {c[key]['gb']:.2f} GB"
                    )
        if c.get("mac"):
            parts.append("the computer: " + ", ".join(c["mac"]))
        out.append(f"- **{name}** ({c['minutes']:.1f} min): " + ", ".join(parts))
    return out


def _thread_samples(path: Path) -> tuple[dict[str, str], dict[str, list[tuple[float, float, str]]]]:
    """``threads.csv``: each thread's name, and its (moment, processor s, state) samples."""
    names: dict[str, str] = {}
    samples: dict[str, list[tuple[float, float, str]]] = {}
    try:
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                try:
                    wall, cpu = float(r["wall"]), float(r["cpu_s"])
                except (KeyError, TypeError, ValueError):
                    continue
                thread = r.get("thread") or "?"
                names[thread] = r.get("name") or names.get(thread, "")
                samples.setdefault(thread, []).append((wall, cpu, r.get("state") or ""))
    except OSError:
        return {}, {}
    return names, samples


def _cores(points: list[tuple[float, float, str]], t0: float, t1: float) -> tuple[float, float]:
    """(processor s, seconds) a thread used over a span, from its samples nearest round it: the
    last at or before its start, the first at or after its end (a thread born or ended within: its
    first or its last)."""
    if len(points) < 2:
        return 0.0, 0.0
    a = ([p for p in points if p[0] <= t0] or points[:1])[-1]
    b = ([p for p in points if p[0] >= t1] or points[-1:])[0]
    if b[0] <= a[0]:
        return 0.0, 0.0
    return max(0.0, b[1] - a[1]), b[0] - a[0]


THREADS_SAID = 6  # busiest threads said for each load
SLOWED_BY = 0.1  # cores: a thread this much slower during a load than outside is said


def _thread_label(thread: str, name: str) -> str:
    return f"`{name}`" if name else f"thread …{thread[-4:]}"


def load_lines(folder: Path, loads: list[Load], run: dict) -> list[str]:
    """What X-Plane and the Mac did during each load of the path (``threads.csv`` and 2026-09-30's
    readings of ``system.csv``), against the rest of the path: X-Plane's cores, its busiest threads
    and those slowed the most, what it read from the disks, the Mac's memory and heat."""
    names, samples = _thread_samples(folder / "threads.csv")
    rows = _system_rows(folder / "system.csv")
    flying, done = run.get("flying_wall"), run.get("done_wall")
    if not samples or flying is None or done is None:
        return []
    spans = []
    for k, ld in enumerate([ld for ld in loads if ld.path_s is not None], 1):
        t0 = flying + ld.path_s
        spans.append((k, ld, t0, min(done, t0 + ld.seconds) if ld.seconds is not None else done))

    def rates(thread: str) -> tuple[float, list[float]]:
        """A thread's cores outside the loads, and during each."""
        points = samples[thread]
        cpu_path, path_s = _cores(points, flying, done)
        during = [_cores(points, t0, t1) for _, _, t0, t1 in spans]
        outside_s = path_s - sum(s for _, s in during)
        outside = max(0.0, cpu_path - sum(c for c, _ in during)) / outside_s if outside_s > 0 else 0
        return outside, [c / s if s > 0 else 0.0 for c, s in during]

    all_rates = {thread: rates(thread) for thread in samples}
    out = []
    for i, (k, ld, t0, t1) in enumerate(spans):
        length = max(t1 - t0, 1e-9)
        words = [f"{length:.0f} s" + ("" if ld.seconds is not None else ", to the path's end")]
        around = _around(rows, t0, t1)
        if around is not None:
            a, b, within = around
            span = max(b["wall"] - a["wall"], 1e-9)
            words.append(f"X-Plane {(b['xp_cpu'] - a['xp_cpu']) / span:.2f} cores")
            if a.get("xp_read") is not None and b.get("xp_read") is not None:
                words.append(f"read {(b['xp_read'] - a['xp_read']) / span:.1f} MB/s")
            if a.get("xp_pageins") is not None and b.get("xp_pageins") is not None:
                words.append(f"{(b['xp_pageins'] - a['xp_pageins']) / span:.0f} page-ins/s")
            words += mac_words(within)
        out.append(f"- **load {k}** at path {ld.path_s:.0f} s: " + ", ".join(words))

        def said(chosen: list[tuple[str, tuple[float, list[float]]]], i: int = i) -> str:
            return "; ".join(
                f"{_thread_label(t, names.get(t, ''))} {r[1][i]:.2f} cores "
                f"({r[0]:.2f} outside the loads)"
                for t, r in chosen
            )

        busiest = [
            (t, r)
            for t, r in sorted(all_rates.items(), key=lambda kv: -kv[1][1][i])[:THREADS_SAID]
            if r[1][i] > 0
        ]
        if busiest:
            out.append("  - busiest threads: " + said(busiest))
        slowed = sorted(
            ((t, r) for t, r in all_rates.items() if r[1][i] < r[0] - SLOWED_BY),
            key=lambda kv: kv[1][1][i] - kv[1][0],
        )[:3]
        if slowed:
            out.append("  - slowed the most: " + said(slowed))
    return out


def _shots_of(folder: Path) -> list[float]:
    """The screenshots' times, from the scenario as sent."""
    try:
        text = (folder / "scenario.txt").read_text(errors="replace")
    except OSError:
        return []
    shots = []
    for line in text.splitlines():
        words = line.split("#", 1)[0].split()
        if len(words) >= 2 and words[0] == "screenshot":
            with contextlib.suppress(ValueError):
                shots.append(float(words[1]))
    return shots


_CLOCK = re.compile(r"^(\d+):(\d\d):(\d\d(?:\.\d+)?) ")


def _clock_s(line: str) -> float | None:
    m = _CLOCK.match(line)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


def flight_load_s(lines: list[str]) -> float | None:
    """How long X-Plane took to load the flight, from its own log: from its loading of the flight
    (« to state_NormalLoading », the moment a click on Start Flight gives) to its first line after
    the load (« state_NormalLoading to state_Normal »: its loading screen gone), on X-Plane's own
    clock, which stands still while it loads (the user's « depuis le début du vol jusqu'à
    l'apparition de l'image », 2026-10-09). None where the log does not say."""
    start = load_end = None
    for i, line in enumerate(lines):
        if start is None and "to state_NormalLoading" in line:
            start = _clock_s(line)
        elif start is not None and "state_NormalLoading to state_Normal" in line:
            load_end = i
            break
    if start is None or load_end is None:
        return None
    for line in lines[load_end + 1 :]:
        t = _clock_s(line)
        if t is not None and t > start:
            return t - start
    return None


def start_split(folder: Path, run: dict) -> dict:
    """The start as a pilot lives it, for a series' first flight (X-Plane started for it): the
    flight's load to its first image (``flight_load_s``), then that image to X-Plane running (its
    tenth frame: ``running_wall`` - ``image_wall``, the runner's, a poll apart)."""
    out: dict = {}
    if not str(run.get("xplane_start", "")).startswith("started"):
        return out
    if (folder / "Log.txt").is_file():
        load = flight_load_s((folder / "Log.txt").read_text(errors="replace").splitlines())
        if load is not None:
            out["load_s"] = load
    if run.get("image_wall") is not None and run.get("running_wall") is not None:
        out["frozen_s"] = max(0.0, run["running_wall"] - run["image_wall"])
    return out


def metrics(folder: Path) -> dict:
    """A flight's few numbers, for the table of a campaign."""
    run = json.loads((folder / "run.json").read_text()) if (folder / "run.json").is_file() else {}
    frames = frames_of(folder / "frames.csv", _shots_of(folder))
    loads = []
    if (folder / "Log.txt").is_file():
        launched = str(run.get("xplane_start", "started")).startswith("started")
        text = (folder / "Log.txt").read_text(errors="replace").splitlines()
        loads = [ld for ld in scenery_loads(text, launched) if ld.path_s is not None]
    shots = (
        sorted((folder / "screenshots").glob("*.png")) if (folder / "screenshots").is_dir() else []
    )
    return {
        "run": run,
        "frames": frames,
        "costs": costs(_system_rows(folder / "system.csv"), run),
        "loads": len(loads),
        "shots": shots,
        "start": start_split(folder, run),
    }


def xplane_window_model() -> Callable[[float, float], set[Tile]] | None:
    try:
        from orthostudio.live.evict import xplane_tiles
    except ImportError:
        return None
    return xplane_tiles


BLOCK = 32  # px: the photos compared block by block
SAME_BELOW = 4.0  # mean difference, of 255: a block alike in every photo (sky, aircraft, panel)
OVERLAY_PX = 64  # X-Plane's frame counter, top left: left out


def sharpness(paths: list[Path]) -> dict[str, Any] | None:
    """The screenshots of one moment of one flight, taken with the same camera (a scenario's own,
    or the user's replayed), compared: where they differ is the ground, whose textures differ; the
    sky, the aircraft and the panel are alike. On those blocks, each photo's mean gradient (the
    detail it shows): ``{"values": [...], "vs_first": [...], "ground": share of the photo}``. None
    when fewer than two photos, or no block differs."""
    import numpy as np
    from PIL import Image

    photos = [p for p in paths if p is not None and Path(p).is_file()]
    if len(photos) < 2 or len(photos) != len(paths):
        return None
    grays = [np.asarray(Image.open(p).convert("L"), dtype=np.float32) for p in photos]
    h = min(g.shape[0] for g in grays) // BLOCK * BLOCK
    w = min(g.shape[1] for g in grays) // BLOCK * BLOCK
    grays = [g[:h, :w] for g in grays]

    def blocks(a: Any) -> Any:  # (rows, cols, BLOCK, BLOCK)
        return a.reshape(h // BLOCK, BLOCK, w // BLOCK, BLOCK).swapaxes(1, 2)

    def energy(g: Any) -> Any:  # the mean gradient of each block
        gx = np.zeros_like(g)
        gy = np.zeros_like(g)
        gx[:, 1:] = np.abs(np.diff(g, axis=1))
        gy[1:, :] = np.abs(np.diff(g, axis=0))
        return blocks(gx + gy).mean(axis=(2, 3))

    first = blocks(grays[0])
    differ = np.zeros((h // BLOCK, w // BLOCK), dtype=bool)
    for g in grays[1:]:
        differ |= np.abs(blocks(g) - first).mean(axis=(2, 3)) > SAME_BELOW
    differ[: OVERLAY_PX // BLOCK, : (OVERLAY_PX * 8) // BLOCK] = False
    if not differ.any():
        return None
    values = [float(energy(g)[differ].mean()) for g in grays]
    return {
        "values": [round(v, 2) for v in values],
        "vs_first": [round(100.0 * (v / values[0] - 1.0), 1) if values[0] else 0.0 for v in values],
        "ground": round(float(differ.mean()), 3),
    }


def _written(c: dict) -> str:
    return " + ".join(f"{gb:.2f}" for gb in c["written_gb"].values()) or "?"


def _cost(m: dict, phase: str, fmt: Callable[[dict], str]) -> str:
    c = m["costs"].get(phase)
    return fmt(c) if c else "?"


def _split(m: dict, key: str) -> str:
    value = m.get("start", {}).get(key)
    return "?" if value is None else f"{value:.0f}"


def _frames(m: dict, fmt: Callable[[Frames], str]) -> str:
    return fmt(m["frames"]) if m["frames"] else "?"


def _sharpness_row(ms: list[dict]) -> list[str]:
    """Each run's screenshots against the first run's, moment by moment (``sharpness``): the mean
    difference of the detail on the ground, in %."""
    count = min((len(m["shots"]) for m in ms), default=0)
    if len(ms) < 2 or count == 0:
        return ["?"] * len(ms)
    diffs: list[list[float]] = [[] for _ in ms]
    for k in range(count):
        got = sharpness([m["shots"][k] for m in ms])
        if got:
            for i, v in enumerate(got["vs_first"]):
                diffs[i].append(v)
    if not diffs[0]:
        return ["?"] * len(ms)
    return ["the reference"] + [
        f"{statistics.fmean(d):+.1f} % ({len(d)} photos)" for d in diffs[1:]
    ]


def campaign_table(runs: list[tuple[str, Path]], only: set[str] | None = None) -> str:
    """The runs of a campaign side by side, a table per scenario: ``runs`` is (what ran, the
    series' folder); ``only``: those scenarios alone (a comparison's, where a run flew others)."""
    by_scenario: dict[str, list[tuple[str, dict]]] = {}
    for label, series in runs:
        for folder in sorted(p for p in series.iterdir() if p.is_dir() and "-" in p.name):
            name = folder.name.split("-", 1)[1]
            if only is not None and name not in only:
                continue
            if (folder / "run.json").is_file():
                by_scenario.setdefault(name, []).append((label, metrics(folder)))
    out = []
    for name, cells in by_scenario.items():
        ms = [m for _, m in cells]
        rows = [
            ("outcome", [m["run"].get("outcome", "?") for m in ms]),
            (
                "the flight's load to its first image (s)",
                [_split(m, "load_s") for m in ms],
            ),
            ("then that image to X-Plane running (s)", [_split(m, "frozen_s") for m in ms]),
            ("start", [_cost(m, "start", lambda c: f"{c['minutes']:.1f} min") for m in ms]),
            (
                "start: downloaded, written (GB)",
                [
                    _cost(m, "start", lambda c: f"{c['downloaded_gb']:.2f}, {_written(c)}")
                    for m in ms
                ],
            ),
            (
                "path: frames a second",
                [_frames(m, lambda f: f"{f.count / max(f.seconds, 1e-9):.1f}") for m in ms],
            ),
            (
                "frame time: mean, 99th, worst (ms)",
                [
                    _frames(m, lambda f: f"{f.mean_ms:.1f}, {f.p99_ms:.0f}, {f.max_ms:.0f}")
                    for m in ms
                ],
            ),
            (
                "frames over 50, 100 ms",
                [_frames(m, lambda f: f"{f.over_50}, {f.over_100}") for m in ms],
            ),
            (
                "path: downloaded, written (GB)",
                [
                    _cost(m, "path", lambda c: f"{c['downloaded_gb']:.2f}, {_written(c)}")
                    for m in ms
                ],
            ),
            (
                "path: streamer's cores, memory (GB)",
                [
                    _cost(m, "path", lambda c: f"{c['streamer_cores']:.2f}, {c['streamer_gb']:.1f}")
                    for m in ms
                ],
            ),
            (
                "path: X-Plane's cores, memory (GB)",
                [
                    _cost(m, "path", lambda c: f"{c['xplane_cores']:.2f}, {c['xplane_gb']:.1f}")
                    for m in ms
                ],
            ),
            *_app_rows(ms),
            ("X-Plane's scenery loads in the path", [str(m["loads"]) for m in ms]),
            ("screenshots' sharpness, against the first", _sharpness_row(ms)),
        ]
        out += [f"## {name}", ""]
        out.append("| | " + " | ".join(label for label, _ in cells) + " |")
        out.append("|---" * (len(cells) + 1) + "|")
        out += [f"| {title} | " + " | ".join(values) + " |" for title, values in rows]
        out.append("")
    return "\n".join(out)


def _app_rows(ms: list[dict]) -> list[tuple[str, list[str]]]:
    """The app's own rows (``fly.py --streamer app``, 2026-10-04), where a run measured it: its
    start, its engine and its web view in flight, and the computer's memory meanwhile."""
    if not any(c.get("web") for m in ms for c in m["costs"].values()):
        return []

    def part(key: str) -> Callable[[dict], str]:
        return lambda c: f"{c[key]['cores']:.2f}, {c[key]['gb']:.2f}" if c.get(key) else "-"

    return [
        (
            "start: streamer's cores, memory most (GB)",
            [
                _cost(
                    m, "start", lambda c: f"{c['streamer_cores']:.2f}, {c['streamer_gb_most']:.1f}"
                )
                for m in ms
            ],
        ),
        (
            "path: of it, OrthoStudio XP's engine (cores, GB)",
            [_cost(m, "path", part("engine")) for m in ms],
        ),
        (
            "path: of it, the window's web view (cores, GB)",
            [_cost(m, "path", part("web")) for m in ms],
        ),
        (
            "path: the computer",
            [_cost(m, "path", lambda c: ", ".join(c["mac"]) or "-") for m in ms],
        ),
    ]


def report(folder: Path) -> str:
    run = json.loads((folder / "run.json").read_text()) if (folder / "run.json").is_file() else {}
    lines = [f"# {run.get('scenario', folder.name)}", ""]
    if run:
        settings = []
        if run.get("ao_settings"):
            settings.append("its defaults" if run["ao_settings"] == "default" else "your settings")
        if run.get("zooms"):
            settings.append("ZL{}/{}".format(*run["zooms"]))
        if run.get("cold"):
            settings.append("cold: nothing built, nothing downloaded")
        if run.get("hold_whole_load"):
            settings.append("Live idle during X-Plane's loads")
        if run.get("frozen_in_flight"):
            settings.append("Live frozen in flight: nothing built after the start")
        if run.get("with_osxp") is not None:
            settings.append(
                "OrthoStudio XP open beside it" if run["with_osxp"] else "Live alone in it"
            )
        lines += [
            f"- streamer: {run.get('streamer', '?')}"
            + (f" ({', '.join(settings)})" if settings else ""),
            f"- X-Plane: {run.get('xplane_start', '?')}",
        ]
        split = start_split(folder, run)
        if "load_s" in split:
            lines.append(
                f"- the flight's load to its first image: {split['load_s']:.0f} s (X-Plane's clock)"
            )
        if "frozen_s" in split:
            lines.append(f"- then that image to X-Plane running: {split['frozen_s']:.0f} s")
        for key, label in (
            ("to_flying_s", "from the scenario sent to the path's start"),
            ("flight_s", "the path, on the wall's clock"),
        ):
            if key in run:
                lines.append(f"- {label}: {run[key]:.0f} s")
        if run.get("outcome") != "done":
            lines.append(f"- OUTCOME: {run.get('outcome')}")
        lines.append("")
    frames = frames_of(folder / "frames.csv", _shots_of(folder))
    lines.append("## Frames")
    if frames:
        if frames.shots_left_out:
            lines.append(f"- the screenshots' {frames.shots_left_out} frames left out")
        lines += [
            f"- {frames.count} frames over {frames.seconds:.0f} s of path: "
            f"{frames.count / max(frames.seconds, 1e-9):.0f} a second",
            f"- frame time: mean {frames.mean_ms:.1f} ms, median {frames.p50_ms:.1f}, "
            f"99th percentile {frames.p99_ms:.1f}, worst {frames.max_ms:.1f}",
            f"- over 50 ms: {frames.over_50}; over 100 ms: {frames.over_100}",
            "- worst: "
            + ", ".join(
                f"{ms:.0f} ms at {t:.0f} s{' (loading)' if ld else ''}"
                for t, ms, ld in frames.worst
            ),
        ]
    else:
        lines.append("- none recorded")
    lines += ["", "## X-Plane's scenery loads"]
    log = folder / "Log.txt"
    loads: list[Load] = []
    if log.is_file():
        launched = str(run.get("xplane_start", "started")).startswith("started")
        loads = scenery_loads(log.read_text(errors="replace").splitlines(), launched)
        lines += window_lines(loads, xplane_window_model())
    else:
        lines.append("- no Log.txt")
    during = load_lines(folder, loads, run)
    if during:
        lines += [
            "",
            "## X-Plane and the computer during the loads (cores: processor time a second)",
        ]
        lines += during
    events = _events(folder / "events.txt")
    if events:
        lines += ["", "## The test pilot (X-Plane's clock, s)", "", "```", *events, "```"]
    computer = computer_lines(_system_rows(folder / "system.csv"), run)
    if computer:
        lines += ["", "## The computer", *computer]
    for name, title in (("live.txt", "Live"), ("autoortho.txt", "AutoOrtho")):
        output = _streamer_lines(folder / name)
        if output:
            lines += ["", f"## {title}", "", "```", *output, "```"]
    shots = sorted((folder / "screenshots").glob("*")) if (folder / "screenshots").is_dir() else []
    if shots:
        lines += ["", "## Screenshots", *(f"- {p.name}" for p in shots)]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    print(report(Path(argv[0])), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
