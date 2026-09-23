"""Progress and time remaining of a whole job (spec ``docs/specs/api.md`` 5.6).

The replay feeds the events of a real six-tile job -- the journal of the build the Works screen
misreported, trimmed without changing its timing (``tests/data/jobs``) -- through ``Job`` at
their recorded times and at their real cadence (a progress line every half second, the
scheduler's ticks, the job's own one-second ticker), with the ``Phase`` events ``build_tiles``
now sends, and checks the ``stats`` lines a page would have received. The unit tests pin the
rules one at a time: progress that never goes back, an elapsed time that spans the phases, the
OSM row of a tile whose data is there, the stage statuses, the weights a page needs, the shape
of ``stats``, a published range that does not jump. Nothing here touches the network or Ortho4XP.
"""

from __future__ import annotations

import dataclasses
import gzip
import itertools
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from orthostudio.api import jobs as jobs_mod
from orthostudio.api import progress
from orthostudio.api.jobs import Job, JobManager, stage_status
from orthostudio.errors import OsxpError
from orthostudio.graph import Store
from orthostudio.imagery.grid import TextureId, textures_covering
from orthostudio.imagery.providers import load_registry
from orthostudio.model import ArtifactRef, TileRef
from orthostudio.pipeline import build as build_mod
from orthostudio.pipeline import native
from orthostudio.pipeline import textures as textures_mod
from orthostudio.pipeline.build import (
    BuildEnv,
    BuildReport,
    BuildSpec,
    Phase,
    build_tiles,
    run_osm_phase,
)
from orthostudio.sched import Done, Failed, Progress, Started, Stats

DATA = Path(__file__).parent / "data" / "jobs" / "six-tiles-bi16-20260913.jsonl.gz"
STATS_KEYS = {
    "running",
    "pending",
    "done",
    "failed",
    "hits",
    "elapsed_s",
    "progress",
    "eta_low_s",
    "eta_high_s",
    "phase",
}
RULES = {
    "osm": "orthostudio.osm",
    "dem": "orthostudio.dem",
    "coastline": "orthostudio.coastline",
    "vectors": "orthostudio.vectors",
    "mesh": "orthostudio.mesh",
    "masks": "orthostudio.masks",
    "xp12": "xp12.rasters",
    "dsf": "tile.dsf",
    "textures": "tile.textures",
    "overlay": "tile.overlay",
    "pack": "tile.pack",
    "install": "tile.install",
}
KEY = "a" * 64
REF = ArtifactRef(KEY, "0" * 64, Path("/nonexistent"), "r", "dir")


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _spec(tile: str, *, zl: int = 16, install: bool = True, **kw: Any) -> BuildSpec:
    base: dict[str, Any] = {
        "provider": "BI",
        "out_dir": Path("/nonexistent/tiles"),
        "custom_scenery": Path("/nonexistent/Custom Scenery") if install else None,
        "store_root": Path("/nonexistent/store"),
        "chunks_root": Path("/nonexistent/chunks"),
        "relief": "xplane",
    }
    base.update(kw)
    return BuildSpec(tile=TileRef.parse(tile), zl=zl, install=install, **base)


def _job(specs: list[BuildSpec], clock: Clock, tmp_path: Path | None = None) -> Job:
    journal = (tmp_path or Path("/nonexistent")) / "job.jsonl"
    job = Job(specs, install=specs[0].install, request=None, journal_path=journal, clock=clock)
    job.begin()
    return job


def _central(stats: dict[str, Any]) -> float:
    """The estimate inside the published range (``low = eta (1 - s b)``, ``high = eta (1 + (2 -
    s) b)``, ``s = BAND_LOW_SHARE``)."""
    low, high = stats["eta_low_s"], stats["eta_high_s"]
    return low + (progress.BAND_LOW_SHARE / 2) * (high - low)


# -- the replay --------------------------------------------------------------------------


def _replay() -> tuple[Job, list[tuple[float, dict[str, Any]]], float]:
    """The fixture through ``Job`` as the live job runs: its events at their times, a ``stats``
    line at every scheduler tick (0.5 s apart at least) and from the ticker (one a second).

    The engine that wrote the journal queued the elevations on the network slot: its rows are
    predicted with that kind (the current engine reads X-Plane's relief in a subprocess slot)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(progress.ROLE_KIND, "dem", "net")
        return _replay_journal()


def _replay_journal() -> tuple[Job, list[tuple[float, dict[str, Any]]], float]:
    raw = gzip.decompress(DATA.read_bytes()).decode("utf-8")
    lines = [json.loads(line) for line in raw.splitlines()]
    meta, rows = lines[0], lines[1:]
    specs = [_spec(t, zl=meta["zl"], install=meta["install"]) for t in meta["tiles"]]
    warm = {TileRef.parse(t) for t in meta["warm_tiles"]}
    cold_x = min(
        textures_covering(s.tile.lat + 1, s.tile.lon, s.tile.lat, s.tile.lon + 1, s.zl, "BI")[
            0
        ].til_x
        for s in specs
        if s.tile not in warm
    )

    def has_chunks(t: TextureId) -> bool:
        # the warm tiles' chunks were on disk; the cold ones had a few textures (18 of 216-234)
        return t.til_x < cold_x or (t.til_x // 16 + t.til_y // 16) % 13 == 0

    clock = Clock()
    job = _job(specs, clock)
    job.prepare(None, has_chunks=has_chunks)
    nodes = [r for r in rows if "node" in r]
    osm_ran = sorted(
        {r["node"] for r in nodes if r["event"] == "started" and r["node"].endswith("/osm")}
    )
    kinds = {r["node"]: r["kind"] for r in nodes if r["event"] == "started"}
    main = tuple((n, k, RULES[n.split("/")[-1]]) for n, k in kinds.items() if n not in osm_ran)
    osm_end = max(r["ts"] for r in nodes if r["event"] == "done" and r["node"] in osm_ran)
    main_start = min(r["ts"] for r in nodes if r["event"] == "started" and r["node"] not in osm_ran)
    reused = tuple((f"{t}/osm", None) for t in meta["tiles"] if f"{t}/osm" not in osm_ran)
    # what build_tiles sends: phase 0 with the reused rows; the build phase once phase 0 has
    # also written the four downloaded tiles into the Ortho4XP cache (8.2 s after the last one);
    # its nodes once declared (0.2 s)
    assert main_start - osm_end > 8.0
    stream: list[tuple[float, int, Any]] = [
        (
            0.01,
            0,
            Phase(
                "data", nodes=tuple((n, "net", "orthostudio.osm") for n in osm_ran), reused=reused
            ),
        ),
        (main_start - 0.2, 0, Phase("build")),
        (main_start - 0.001, 0, Phase("build", nodes=main)),
    ]
    for r in rows:
        ts, event = r["ts"], r["event"]
        if event == "stats":  # the scheduler's tick: its numbers are ignored
            stream.append((ts, 1, Stats(0, 0, 0, 0, 0, 0.0, 0.0)))
        elif event == "started":
            stream.append((ts, 1, Started(r["node"], r["kind"], KEY)))
        elif event == "progress":
            stream.append((ts, 1, Progress(r["node"], r["fraction"], "")))
        elif event == "done":
            stream.append((ts, 1, Done(r["node"], KEY, r["hit"], r["wall_s"], REF)))
        elif event == "failed":
            stream.append((ts, 1, Failed(r["node"], OsxpError(r["code"]), r["cause"])))
    stream.sort(key=lambda item: (item[0], item[1]))
    next_tick = jobs_mod.TICK_S
    for ts, _order, event in stream:
        while next_tick <= ts:  # the job's ticker thread
            clock.t = next_tick
            job._emit_stats(min_period=jobs_mod.STATS_PERIOD_S)
            next_tick += jobs_mod.TICK_S
        clock.t = ts
        job._handle(event)
    clock.t = meta["finished_ts"]
    stats = [(e["ts"], e["stats"]) for e in job.events() if e["event"] == "stats"]
    return job, stats, float(meta["finished_ts"])


@pytest.fixture(scope="module")
def replayed() -> tuple[Job, list[tuple[float, dict[str, Any]]], float]:
    return _replay()


def _at(stats: list[tuple[float, dict[str, Any]]], t: float) -> dict[str, Any]:
    return min(stats, key=lambda item: abs(item[0] - t))[1]


def test_replay_predicts_the_end_of_a_real_six_tile_job(
    replayed: tuple[Job, list[tuple[float, dict[str, Any]]], float],
) -> None:
    """From 20 % progress, the central estimate stays within 27 % of the real end (538 s); the
    old per-phase ``Stats`` were off by 57 % on the same window and their range never held the
    end. The first 250 s are 20 % early *because the line slowed*: the first three tiles'
    textures took their expected time within 5 %, the last three 1.4-1.8 times it; once that
    shows, the estimate closes in, and over the last 30 % of the job it is within 13 %."""
    _, stats, end = replayed
    window = [(t, st) for t, st in stats if st["progress"] >= 0.2 and t < end]
    assert window and window[0][0] < 130  # 20 % is reached in the first quarter
    errors = sorted(abs(t + _central(st) - end) / end for t, st in window)
    assert errors[-1] <= 0.27  # measured 0.265
    assert errors[int(0.9 * (len(errors) - 1))] <= 0.22  # measured 0.21
    assert all(abs(t + _central(st) - end) / end <= 0.13 for t, st in window if t >= 0.7 * end)
    inside = sum(1 for t, st in window if t + st["eta_low_s"] <= end <= t + st["eta_high_s"])
    assert inside / len(window) >= 0.62  # measured 0.68


def test_replay_publishes_a_range_that_does_not_jump(
    replayed: tuple[Job, list[tuple[float, dict[str, Any]]], float],
) -> None:
    """At the real cadence, the predicted end of consecutive ``stats`` lines moves by at most
    ``max(ETA_SLEW x time left, ETA_SLEW_MIN_S)`` per second: 8.7 s at worst on this job,
    10.9 s for the middle of the range, where it jumped by 85 s then 27 s back within a second
    when a warm tile started encoding, and a line comes at least every 1.25 s."""
    _, stats, end = replayed
    running = [(t, st) for t, st in stats if t < end and st["eta_low_s"] is not None]
    assert len(running) > 400
    worst_end = worst_mid = 0.0
    for (ta, a), (tb, b) in itertools.pairwise(running):
        dt = tb - ta
        allowed = dt * max(progress.ETA_SLEW * _central(a), progress.ETA_SLEW_MIN_S)
        moved = abs((tb + _central(b)) - (ta + _central(a)))
        assert moved <= allowed + 0.3, (ta, tb, moved, allowed)  # 0.1 s rounding of the range
        worst_end = max(worst_end, moved)
        mid_a = ta + (a["eta_low_s"] + a["eta_high_s"]) / 2
        mid_b = tb + (b["eta_low_s"] + b["eta_high_s"]) / 2
        worst_mid = max(worst_mid, abs(mid_b - mid_a))
    assert worst_end <= 10.0 and worst_mid <= 12.5
    assert max(tb - ta for (ta, _a), (tb, _b) in itertools.pairwise(stats)) <= 1.25


def test_replay_progress_elapsed_and_phase(
    replayed: tuple[Job, list[tuple[float, dict[str, Any]]], float],
) -> None:
    job, stats, end = replayed
    values = [st["progress"] for _t, st in stats]
    assert values == sorted(values) and values[0] >= 0.0
    # 1 once every node ended (the last textures node failed at 538.0 s), not before
    assert values[-1] == 1.0 and all(st["progress"] < 1.0 for t, st in stats if t < 537.9)
    # elapsed spans both phases (the journal's ts count from the job's creation, a hair earlier)
    assert all(abs(st["elapsed_s"] - t) < 0.01 for t, st in stats)
    assert all(st["eta_low_s"] is not None for t, st in stats if t < end)
    # phase 0 ends when the Ortho4XP cache is written (113 s), not with the last download (105 s)
    assert _at(stats, 50)["phase"] == "data" and _at(stats, 110)["phase"] == "data"
    assert _at(stats, 115)["phase"] == "build"
    # two of the four downloads done at 60 s (11 % of the job): the page read "50 %"
    assert 0.05 <= _at(stats, 60)["progress"] <= 0.15
    # counts are the whole job's, not the phase's: 6 tiles x 12 rows
    first = stats[0][1]
    assert first["running"] + first["pending"] + first["done"] + first["failed"] == 72
    assert first["hits"] == 2  # the two OSM rows whose data was there
    job._finish("failed", None, None)
    last = job.state()
    assert last["stats"]["progress"] == 1.0 and last["stats"]["eta_low_s"] == 0.0
    assert last["eta"] is None
    for tile in last["tiles"]:
        data = tile["stages"]["data"]
        assert data["status"] == "done" and data["fraction"] == 1.0
        osm = next(n for n in data["nodes"] if n["role"] == "osm")
        assert osm["status"] in ("done", "hit")
    t005 = next(t for t in last["tiles"] if t["tile"] == "+46+005")
    osm = next(n for n in t005["stages"]["data"]["nodes"] if n["role"] == "osm")
    assert osm["status"] == "hit" and osm["hit"] is True and osm["fraction"] == 1.0
    assert osm["weight_s"] == 0.0


# -- rules, one at a time ----------------------------------------------------------------


def test_every_stage_leaves_a_line_in_the_log(tmp_path: Path) -> None:
    """A build that stopped while downloading its OpenStreetMap data showed a bar going nowhere and
    wrote nothing: only the images wrote log lines (a user on Linux, 2026-09-22). The file gets one
    line per node every ten seconds, the job's own every second."""
    import logging

    from orthostudio.api.jobs import FILE_LOG_PERIOD_S

    lines: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: lines.append(record.getMessage())  # type: ignore[method-assign]
    file_log = logging.getLogger("orthostudio.api.jobs")
    file_log.addHandler(handler)
    level = file_log.level
    file_log.setLevel(logging.INFO)
    try:
        clock = Clock()
        job = _job([_spec("+46+006", install=False)], clock, tmp_path)
        node = "+46+006/osm"
        job.on_event(Started(node, "net", KEY))
        job.on_event(Progress(node, 0.4, "2 of 5 layers, 1.2 MB, 42 s"))
        clock.t += 0.2
        job.on_event(Progress(node, 0.4, ""))  # nothing to say, nothing written
        clock.t += FILE_LOG_PERIOD_S + 1
        job.on_event(Progress(node, 0.6, "3 of 5 layers, 2.0 MB, 53 s"))
        job.on_event(Done(node, KEY, False, 53.0, REF))
    finally:
        file_log.removeHandler(handler)
        file_log.setLevel(level)
    logged = [e for e in job.events() if e["event"] == "log"]
    assert [e["message"] for e in logged] == [
        "2 of 5 layers, 1.2 MB, 42 s",
        "3 of 5 layers, 2.0 MB, 53 s",
    ]
    assert all(e["stage"] == "data" for e in logged)
    # the file: the two progress lines ten seconds apart, and the node's end
    assert [x for x in lines if "layers" in x] == [
        f"{node}: 2 of 5 layers, 1.2 MB, 42 s",
        f"{node}: 3 of 5 layers, 2.0 MB, 53 s",
    ]
    assert f"{node}: done in 53.0 s" in lines


def test_stage_status_rules() -> None:
    assert stage_status(["pending", "pending"]) == "pending"
    assert stage_status(["running", "pending"]) == "running"
    assert stage_status(["done", "running", "pending"]) == "running"
    assert stage_status(["done", "pending"]) == "waiting"  # real work done, nothing runs
    assert stage_status(["hit", "done", "pending"]) == "waiting"
    assert stage_status(["hit", "pending", "pending"]) == "pending"  # nothing done yet
    assert stage_status(["done", "hit"]) == "done"
    assert stage_status(["hit", "hit"]) == "hit"
    assert stage_status(["done", "failed", "pending"]) == "failed"
    assert stage_status(["done", "skipped"]) == "skipped"
    assert stage_status(["cancelled", "skipped"]) == "cancelled"
    # once the job is over nothing runs or waits any more
    assert stage_status(["done", "pending"], "failed") == "skipped"
    assert stage_status(["done", "running"], "cancelled") == "cancelled"
    assert stage_status(["hit", "pending"], "cancelled") == "pending"
    assert stage_status(["done", "pending"], "running") == "waiting"
    assert stage_status(["pending", "pending"], "cancelled") == "pending"
    assert stage_status(["done", "done"], "done") == "done"


def test_replay_stages_read_what_their_nodes_do(
    replayed: tuple[Job, list[tuple[float, dict[str, Any]]], float],
) -> None:
    """The two stages the review saw read ``running`` for minutes: +46+005's data (its OSM
    row a hit, nothing else started) and +46+008's assembly (rasters and overlay built at
    114-116 s, its pack waiting for the textures until they failed at 335 s)."""
    clock = Clock()
    job = _job([_spec("+46+008")], clock)
    clock.t = 0.1
    job.on_event(Phase("data", nodes=(), reused=(("+46+008/osm", None),)))
    assert job.state()["tiles"][0]["stages"]["data"]["status"] == "pending"
    for node, t in (("+46+008/xp12", 1.0), ("+46+008/overlay", 2.0)):
        clock.t = t
        job.on_event(Started(node, "io", KEY))
        assert job.state()["tiles"][0]["stages"]["assembly"]["status"] == "running"
        clock.t = t + 0.5
        job.on_event(Done(node, KEY, False, 0.5, REF))
    assert job.state()["tiles"][0]["stages"]["assembly"]["status"] == "waiting"
    clock.t = 3.0
    job.on_event(Started("+46+008/BI16/dsf", "cpu", KEY))
    assert job.state()["tiles"][0]["stages"]["assembly"]["status"] == "running"


def test_osm_row_reused_by_phase_0_is_a_journaled_hit(tmp_path: Path) -> None:
    clock = Clock()
    job = _job([_spec("+46+005", zl=14, install=False)], clock, tmp_path)
    clock.t = 0.2
    job.on_event(Phase("data", nodes=(), reused=(("+46+005/osm", "b" * 64),)))
    (done,) = [e for e in job.events() if e["event"] == "done"]
    assert done["node"] == "+46+005/osm" and done["hit"] is True and done["wall_s"] == 0.0
    assert done["stage"] == "data" and done["role"] == "osm" and done["key"] == "b" * 64
    (tile,) = job.state()["tiles"]
    data = tile["stages"]["data"]
    osm = next(n for n in data["nodes"] if n["role"] == "osm")
    assert osm["status"] == "hit" and osm["fraction"] == 1.0 and osm["weight_s"] == 0.0
    assert data["status"] == "pending"  # a hit is not work: nothing of the stage has started
    assert done["weight_s"] == 0.0
    stats = [e for e in job.events() if e["event"] == "stats"][-1]["stats"]
    assert stats["hits"] == 1 and stats["phase"] == "data"
    # a hit weighs nothing: nothing of this job's own work is done yet
    assert stats["progress"] == 0.0


def test_an_osm_row_the_declared_graph_does_not_hold_becomes_a_hit(tmp_path: Path) -> None:
    """The build declares the OSM downloads it runs as nodes of its graph: a pending ``osm`` row
    it does not declare had its data already (a stored snapshot, or an older engine's phase 0
    that reported nothing), so it is a hit, journaled before the graph starts; a declared one
    stays pending until its node runs."""
    clock = Clock()
    job = _job([_spec("+46+005", zl=14, install=False), _spec("+46+006", zl=14, install=False)],
               clock, tmp_path)  # fmt: skip
    clock.t = 1.0
    job.on_event(Phase("build"))
    assert job._tiles["+46+005"].nodes["+46+005/osm"].status == "pending"  # not declared yet
    declared = (
        ("+46+005/dem", "subprocess", "orthostudio.dem"),
        ("+46+006/osm", "net", "orthostudio.osm"),
        ("+46+006/dem", "subprocess", "orthostudio.dem"),
    )
    job.on_event(Phase("build", nodes=declared))
    job.on_event(Started("+46+005/dem", "subprocess", KEY))
    osm = job._tiles["+46+005"].nodes["+46+005/osm"]
    assert osm.status == "hit" and osm.fraction == 1.0
    kinds = [(e["event"], e.get("node")) for e in job.events()]
    assert kinds.index(("done", "+46+005/osm")) < kinds.index(("started", "+46+005/dem"))
    assert job._tiles["+46+006"].nodes["+46+006/osm"].status == "pending"
    assert job._stats_now()["phase"] == "build"


def test_a_build_without_phases_still_switches_the_hint_but_invents_no_hit(
    tmp_path: Path,
) -> None:
    """A fake that plays tile after tile: the second tile's OSM row runs later, for real."""
    clock = Clock()
    job = _job([_spec("+46+005", zl=14, install=False), _spec("+46+006", zl=14, install=False)],
               clock, tmp_path)  # fmt: skip
    clock.t = 1.0
    job.on_event(Started("+46+005/dem", "net", KEY))
    assert job._stats_now()["phase"] == "build"
    assert job._tiles["+46+006"].nodes["+46+006/osm"].status == "pending"
    assert not [e for e in job.events() if e["event"] == "done"]


def test_progress_never_decreases_and_elapsed_spans_phases(tmp_path: Path) -> None:
    clock = Clock()
    spec = _spec("+46+007", zl=14, install=False)
    job = _job([spec], clock, tmp_path)
    seen: list[dict[str, Any]] = []

    def at(t: float, *events: Any) -> None:
        clock.t = t
        for e in events:
            job.on_event(e)
        seen.append(job._stats_now())

    at(0.1, Phase("data", nodes=(("+46+007/osm", "net", "orthostudio.osm"),)))
    at(0.2, Started("+46+007/osm", "net", KEY), Progress("+46+007/osm", 0.0, "4 layers"))
    # the phase-0 scheduler's own Stats: its numbers must not reach the job's
    at(10.0, Stats(running=1, pending=0, done=0, failed=0, hits=0, elapsed_s=9.8, eta_s=1.0))
    at(26.0, Done("+46+007/osm", KEY, False, 25.8, REF))
    at(27.0, Phase("build"))
    # the main graph declares one row the spec did not predict and one more mesh (#2)
    declared = (
        *(
            (f"+46+007/{r}", progress.ROLE_KIND[r], RULES[r])
            for r in ("dem", "coastline", "vectors", "mesh", "masks", "xp12", "overlay")
        ),
        ("+46+007/mesh#2", "subprocess", "orthostudio.mesh"),
        ("+46+007/BI14/dsf", "cpu", "tile.dsf"),
        ("+46+007/BI14/textures", "net", "tile.textures"),
        ("+46+007/BI14/pack", "io", "tile.pack"),
    )
    at(30.0, Phase("build", nodes=declared))
    # the main scheduler restarts its clock: its elapsed_s is 0.1 here
    at(30.1, Stats(running=0, pending=12, done=0, failed=0, hits=0, elapsed_s=0.1, eta_s=5.0))
    for i, (node, kind, _rule) in enumerate(declared):
        t = 31.0 + 2 * i
        at(t, Started(node, kind, KEY))
        if node.endswith("textures"):
            for k in range(1, 5):
                at(t + 0.3 * k, Progress(node, k / 5, ""))
        at(t + 1.5, Done(node, KEY, node.endswith("xp12"), 1.0, REF))
    job._finish("done", None, None)
    seen.append(job.state()["stats"])
    values = [s["progress"] for s in seen]
    assert values == sorted(values) and values[-1] == 1.0
    elapsed = [s["elapsed_s"] for s in seen]
    assert elapsed == sorted(elapsed) and seen[2]["elapsed_s"] == pytest.approx(10.0)
    assert seen[6]["elapsed_s"] == pytest.approx(30.1)  # not the main scheduler's 0.1
    assert seen[2]["running"] == 1 and seen[2]["pending"] >= 9  # the job's rows, not phase 0's
    assert seen[0]["phase"] == "data" and seen[5]["phase"] == "build"
    assert seen[-1]["eta_low_s"] == 0.0 and seen[-1]["eta_high_s"] == 0.0
    rows = job._tiles["+46+007"].nodes
    assert "+46+007/coastline" in rows and "+46+007/mesh#2" in rows


def test_declared_graph_replaces_the_predicted_rows(tmp_path: Path) -> None:
    clock = Clock()
    spec = _spec("+43+005", zl=14, install=False, overlay=False)
    job = _job([spec], clock, tmp_path)
    predicted = set(job._tiles["+43+005"].nodes)
    assert {"+43+005/osm", "+43+005/dem", "+43+005/coastline"} <= predicted
    # no coastline source after all: the mesh is declared without its coastline node
    nodes = (
        ("+43+005/dem", "net", "orthostudio.dem"),
        ("+43+005/vectors", "subprocess", "orthostudio.vectors"),
        ("+43+005/mesh", "subprocess", "orthostudio.mesh"),
        ("+43+005/masks", "subprocess", "orthostudio.masks"),
        ("+43+005/xp12", "io", "xp12.rasters"),
        ("+43+005/BI14/dsf", "cpu", "tile.dsf"),
        ("+43+005/BI14/textures", "net", "tile.textures"),
        ("+43+005/BI14/pack", "io", "tile.pack"),
    )
    clock.t = 1.0
    job.on_event(Phase("build", nodes=nodes))
    rows = job._tiles["+43+005"].nodes
    assert set(rows) == {n for n, _k, _r in nodes} | {"+43+005/osm"}
    assert rows["+43+005/osm"].status == "hit"  # phase 0 is over: its data was there
    assert rows["+43+005/vectors"].weight_s == pytest.approx(progress.ROLE_SECONDS["vectors"])
    tile = job.state()["tiles"][0]
    assert tile["stages"]["data"]["status"] == "pending"  # a hit and rows still to run
    assert [n["role"] for n in tile["stages"]["data"]["nodes"]] == ["osm", "dem", "vectors"]


def test_a_second_pass_runs_skipped_rows_again_and_keeps_what_it_built(tmp_path: Path) -> None:
    clock = Clock()
    job = _job([_spec("+43+005", zl=14, install=False)], clock, tmp_path)
    clock.t = 1.0
    job.on_event(Started("+43+005/mesh", "subprocess", KEY))
    clock.t = 3.0
    job.on_event(Done("+43+005/mesh", KEY, False, 2.0, REF))
    err = OsxpError("SYS_UPSTREAM_FAILED", context={"upstream": "x", "root": "x", "code": "X"})
    job.on_event(Failed("+43+005/masks", err, cause="+43+004/mesh"))
    assert job.state()["tiles"][0]["stages"]["coast"]["status"] == "skipped"
    before = job._stats_now()["progress"]
    clock.t = 5.0
    job.on_event(
        Phase("build", nodes=(("+43+005/mesh", "subprocess", "orthostudio.mesh"),
                              ("+43+005/masks", "subprocess", "orthostudio.masks")))
    )  # fmt: skip
    rows = job._tiles["+43+005"].nodes
    assert rows["+43+005/masks"].status == "pending" and rows["+43+005/masks"].error is None
    job.on_event(Started("+43+005/mesh", "subprocess", KEY))
    job.on_event(Done("+43+005/mesh", KEY, True, 0.0, REF))  # a hit now: it was built before
    assert rows["+43+005/mesh"].status == "done" and rows["+43+005/mesh"].wall_s == 2.0
    assert job._stats_now()["progress"] >= before  # held, never back


def test_stats_shape_in_the_event_and_the_state_and_a_line_every_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the manager: phases, a gap without any scheduler (the declaration), the end."""
    monkeypatch.setattr(jobs_mod, "STATS_PERIOD_S", 0.1)
    monkeypatch.setattr(jobs_mod, "TICK_S", 0.02)

    def build(
        specs: list[BuildSpec], *, on_event: Callable[[Any], None], env: Any = None
    ) -> BuildReport:
        on_event(Phase("data", nodes=(), reused=(("+43+005/osm", None),)))
        on_event(Phase("build"))
        time.sleep(0.6)  # declaring a big graph: no event at all
        nodes = [
            (node, progress.ROLE_KIND[role], RULES[role])
            for node, role in jobs_mod._expected_nodes(specs[0])
            if role != "osm"
        ]
        on_event(Phase("build", nodes=tuple(nodes)))
        for node, kind, _rule in nodes:
            on_event(Started(node, kind, KEY))  # type: ignore[arg-type]
            on_event(Done(node, KEY, False, 0.01, REF))
            on_event(Stats(running=0, pending=0, done=1, failed=0, hits=0, elapsed_s=0.0, eta_s=0))
        return BuildReport([], 0.7, len(nodes), 0, 0, False, "/store", "/out")

    mgr = JobManager(jobs_dir=tmp_path / "jobs", build=build, env_factory=None)
    job = mgr.start([_spec("+43+005", zl=14, install=False)])
    assert job.wait(10.0) and job.status == "done"
    events = job.events()
    stats = [e for e in events if e["event"] == "stats"]
    assert all(set(e["stats"]) == STATS_KEYS for e in stats)
    state = job.state()
    assert set(state["stats"]) == STATS_KEYS and state["stats"] == stats[-1]["stats"]
    assert stats[-1]["stats"]["progress"] == 1.0 and stats[-1]["stats"]["eta_high_s"] == 0.0
    assert events[-1]["event"] == "finished" and events[-2]["event"] == "stats"
    phases = [e["stats"]["phase"] for e in stats]
    assert phases[0] == "data" and phases[-1] == "build"
    # the gap of 0.6 s was covered by the ticker, one line per (patched) period
    gap = [e for e in stats if e["stats"]["phase"] == "build" and e["stats"]["pending"] > 0]
    assert len(gap) >= 4
    ts = [e["ts"] for e in stats]
    assert max(b - a for a, b in itertools.pairwise(ts)) < 0.35
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


def test_a_past_job_keeps_the_rows_it_saved(tmp_path: Path) -> None:
    """A state written before ``coastline`` was predicted, and before phase 0 reported its
    reused rows (the OSM row saved ``pending``), is read back as the job ended."""
    spec = _spec("+43+005", zl=14, install=False)
    job = Job([spec], install=False, request=None, journal_path=tmp_path / "old.jsonl")
    job.status = "done"
    doc = {**job.state(), "specs": [jobs_mod._spec_to_json(spec)]}
    for stage in doc["tiles"][0]["stages"].values():
        stage["nodes"] = [
            {**n, "status": "pending" if n["role"] == "osm" else "done"}
            for n in stage["nodes"]
            if n["role"] != "coastline"
        ]
    (tmp_path / "old.json").write_text(json.dumps(doc), encoding="utf-8")
    past = Job.load(tmp_path / "old.json")
    tile = past.state()["tiles"][0]
    rows = [n for stage in tile["stages"].values() for n in stage["nodes"]]
    assert rows and all(n["status"] == ("hit" if n["role"] == "osm" else "done") for n in rows)
    assert "coastline" not in {n["role"] for n in rows}
    assert tile["stages"]["data"]["status"] == "done"


def test_rows_and_events_carry_the_weight_a_page_needs(tmp_path: Path) -> None:
    """The page draws a step's bar from its rows; without the weights it averaged them and the
    next refresh snapped the bar back. ``weight_s`` is the weight of the stage fraction: the
    row's expected seconds, 0 when it does not run (a hit, a skip before it started)."""
    clock = Clock()
    job = _job([_spec("+43+005", zl=14, install=False)], clock, tmp_path)
    job.prepare(None, has_chunks=lambda t: False)
    clock.t = 0.1
    job.on_event(Phase("data", nodes=(), reused=(("+43+005/osm", None),)))
    events = [
        Started("+43+005/dem", "net", KEY),
        Done("+43+005/dem", KEY, False, 2.0, REF),
        Started("+43+005/vectors", "subprocess", KEY),
        Done("+43+005/vectors", KEY, True, 0.0, REF),
        Started("+43+005/coastline", "io", KEY),
        Progress("+43+005/coastline", 0.5, "half"),
    ]
    for i, event in enumerate(events):
        clock.t = 1.0 + i
        job.on_event(event)
    err = OsxpError("SYS_UPSTREAM_FAILED", context={"upstream": "x", "root": "x", "code": "X"})
    job.on_event(Failed("+43+005/masks", err, cause="+43+005/mesh"))
    by_event = {(e["event"], e["node"]): e for e in job.events() if e.get("node")}
    rows = job._tiles["+43+005"].nodes
    assert by_event[("done", "+43+005/osm")]["weight_s"] == 0.0
    assert by_event[("started", "+43+005/dem")]["weight_s"] == rows["+43+005/dem"].weight_s > 0
    assert by_event[("done", "+43+005/dem")]["weight_s"] == pytest.approx(
        rows["+43+005/dem"].weight_s, abs=1e-3
    )
    assert by_event[("started", "+43+005/vectors")]["weight_s"] > 0
    assert by_event[("done", "+43+005/vectors")]["weight_s"] == 0.0  # a hit
    assert by_event[("progress", "+43+005/coastline")]["weight_s"] > 0
    assert by_event[("failed", "+43+005/masks")]["weight_s"] == 0.0  # skipped before it started
    tile = job.state()["tiles"][0]
    for stage in tile["stages"].values():
        if not stage["nodes"]:
            continue
        parts = [
            (n["weight_s"], 1.0 if n["status"] not in ("pending", "running") else n["fraction"])
            for n in stage["nodes"]
            if n["status"] != "pending"
        ]
        total = sum(n["weight_s"] for n in stage["nodes"])
        if total > 0:
            expected = sum(w * f for w, f in parts) / total
            assert stage["fraction"] == pytest.approx(expected, abs=2e-3)
    data = tile["stages"]["data"]
    assert data["status"] == "running"  # the coastline runs
    assert {n["role"]: n["weight_s"] for n in data["nodes"]}["vectors"] == 0.0


def test_prepare_publishes_the_refined_estimate_at_once(tmp_path: Path) -> None:
    """A line published from the provisional weights (all downloads) before ``prepare`` saw
    the chunks on disk must not hold the range up while it slowly comes down."""
    clock = Clock()
    job = _job([_spec("+46+006", zl=16, install=False)], clock, tmp_path)
    clock.t = 0.3
    provisional = job._stats_now()
    job.prepare(None, has_chunks=lambda t: True)
    clock.t = 0.6
    refined = job._stats_now()
    assert refined["eta_high_s"] < 0.8 * provisional["eta_high_s"]


def test_the_smoother_bounds_how_fast_the_published_end_moves() -> None:
    smoother = progress.EtaSmoother()
    low, high = smoother.update(0.0, 300.0, 0.3) or (0.0, 0.0)
    assert low == pytest.approx(300 * (1 - 0.15)) and high == pytest.approx(300 * (1 + 0.45))
    # the estimate jumps by 100 s: the published end moves 4 % of the 299 s left in a second
    smoother.update(1.0, 399.0, 0.3)
    assert smoother.end == pytest.approx(300.0 + 0.04 * 299.0)
    # it goes back: the end follows at the same bounded pace, never in the past
    for t in range(2, 40):
        smoother.update(float(t), 300.0 - t, 0.3)
    assert smoother.end is not None and 300.0 <= smoother.end < 300.5
    # near the end the pace is at least 2 s per second: 10 s left toward 1 s takes ~4 s
    smoother = progress.EtaSmoother()
    smoother.update(0.0, 10.0, 0.2)
    for t in (1.0, 2.0, 3.0, 4.0, 5.0):
        smoother.update(t, 1.0, 0.2)
    assert smoother.end == pytest.approx(6.0)
    assert smoother.update(6.0, None, 0.2) is None
    # the band moves by 0.01 a second
    smoother.update(7.0, 5.0, 0.45)
    assert smoother.band == pytest.approx(0.2 + 0.01 * 2)


def test_a_warm_tile_does_not_speak_for_the_downloads(tmp_path: Path) -> None:
    """Encoding cached chunks and downloading them are limited by different things: a warm
    tile's pace changes the estimate of the other warm tiles, not of the cold ones."""
    clock = Clock()
    warm, cold = _spec("+46+006", zl=16, install=False), _spec("+46+007", zl=16, install=False)
    job = _job([warm, cold], clock, tmp_path)
    job.prepare(None, has_chunks=lambda t: t.til_x < 34032)
    rows = {**job._tiles["+46+006"].nodes, **job._tiles["+46+007"].nodes}
    warm_row, cold_row = rows["+46+006/BI16/textures"], rows["+46+007/BI16/textures"]
    assert warm_row.group == "textures_cached" and cold_row.group == "textures"
    assert cold_row.weight_s > 4 * warm_row.weight_s
    before = progress._speeds(list(rows.values()), 0.0)[0]
    clock.t = 10.0
    job.on_event(Started("+46+006/BI16/textures", "net", KEY))
    clock.t = 10.0 + 3 * warm_row.weight_s  # three times slower than expected
    job.on_event(Done("+46+006/BI16/textures", KEY, False, 3 * warm_row.weight_s, REF))
    after = progress._speeds(list(rows.values()), clock.t)[0]
    assert after["textures_cached"] > 1.5 and after["textures"] == before["textures"] == 1.0


# The texture reports of the six-tile batch of 2026-09-13 at 20:58 (docs/benchmarks/batch-6-tiles-
# zl16.md), tile after tile: whole time (s), chunks downloaded, chunks already on disk.
BATCH_REPORTS = [
    (63.3, 59904, 0),
    (48.1, 49920, 4608),
    (42.0, 55296, 4608),
    (38.3, 50688, 4608),
    (44.4, 50688, 4608),
    (48.9, 55296, 4608),
]
BATCH_END_S = 322.4


def _report(
    total_s: float, fetched: int, cached: int, *, provider: str = "BI", cancelled: bool = False
) -> dict[str, Any]:
    return {
        "provider": provider,
        "cancelled": cancelled,
        "counts": {"tiles_fetched": fetched, "tiles_cached": cached},
        "timings": {"total_s": total_s},
    }


def test_the_line_speed_comes_from_the_latest_reports_of_the_provider(tmp_path: Path) -> None:
    now = 1_000_000.0
    batch = [(now - 600 + 60 * i, _report(*r)) for i, r in enumerate(BATCH_REPORTS)]
    factor = progress.line_factor(batch, provider="BI", now=now)
    # seconds per downloaded texture: 0.189 0.190 0.220 0.222 0.242 0.270, over 0.30 s
    assert factor is not None and 0.73 <= factor <= 0.745
    ignored = [
        (now, _report(500.0, 59904, 0, cancelled=True)),
        (now, _report(500.0, 59904, 0, provider="GO2")),
        (now, _report(20.0, 0, 59904)),  # a warm tile measures the processor
        (now, _report(90.0, 2048, 0)),  # eight textures: too few
        (now, {"provider": "BI", "counts": None, "timings": "?"}),
    ]
    assert progress.line_factor(batch + ignored, provider="BI", now=now) == factor
    # the same line toward a provider taking 16 requests at once: its textures weigh 2.05 s
    small = progress.line_factor(batch, provider="BI", now=now, in_flight=16)
    assert small == pytest.approx(factor * 0.30 / 2.05)
    # a slow line three days ago weighs little against this evening's
    old = [(now - 72 * 3600 - i, _report(210.0, 59904, 0)) for i in range(3)]
    assert progress.line_factor(old + batch[:3], provider="BI", now=now) < 1.0
    assert progress.line_factor(batch[:1], provider="BI", now=now) is None
    assert progress.line_factor([], provider="BI", now=now) is None
    # read from the logs, newest first, skipping what is not a report
    logs = tmp_path / "logs"
    logs.mkdir()
    for i, (_at, doc) in enumerate(batch[:3]):
        path = logs / f"textures-+46+00{5 + i}-BI16-{i:012x}.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        os.utime(path, (now + i, now + i))
    (logs / "textures-broken.json").write_text("{", encoding="utf-8")
    (logs / "overlay-+46+005-0.json").write_text("{}", encoding="utf-8")
    read = progress.read_line_history(logs)
    assert [at for at, _doc in read] == [now + 2, now + 1, now]
    assert read[0][1]["timings"]["total_s"] == 42.0
    assert progress.read_line_history(tmp_path / "missing") == []


def test_the_first_estimate_starts_from_the_line_of_recent_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The six-tile batch of 2026-09-13 ended in 322 s; its first estimate, from the constant
    weight of a texture, was 416 s (the range 323-697 s, measured). Started from the speed its
    own six reports show (0.73 of the weight), the same estimate is 315 s, and the range holds
    the end. That engine queued the elevations on the network slot, and so does this estimate."""
    monkeypatch.setitem(progress.ROLE_KIND, "dem", "net")
    tiles = [f"+46+{lon:03d}" for lon in range(5, 11)]
    specs = [_spec(t, zl=16) for t in tiles]
    now = time.time()
    history = [(now - 600 + 60 * i, _report(*r)) for i, r in enumerate(BATCH_REPORTS)]

    def first_stats(reports: list[tuple[float, dict[str, Any]]] | None) -> dict[str, Any]:
        clock = Clock()
        job = _job(specs, clock, tmp_path)
        # the cold tiles had a few textures on disk (18 of 216-234), their OSM data cached
        job.prepare(
            None,
            has_chunks=lambda t: (t.til_x // 16 + t.til_y // 16) % 13 == 0,
            history=reports,
        )
        job._handle(Phase("data", nodes=(), reused=tuple((f"{t}/osm", None) for t in tiles)))
        clock.t = 0.2
        job._handle(Phase("build"))
        clock.t = 0.5
        return job._stats_now()

    constant, recent = first_stats(None), first_stats(history)
    assert _central(constant) >= 1.2 * BATCH_END_S  # measured 416 s
    assert abs(_central(recent) - BATCH_END_S) <= 0.1 * BATCH_END_S  # measured 315 s
    assert recent["eta_low_s"] <= BATCH_END_S <= recent["eta_high_s"]


def test_a_provider_that_takes_fewer_requests_weighs_more() -> None:
    spec = _spec("+46+008", install=False)
    bing = progress.texture_cost(spec, in_flight=128)
    small = progress.texture_cost(spec, in_flight=16)
    assert bing.seconds == pytest.approx(216 * progress.TEXTURE_DOWNLOAD_S)
    per_texture = progress.TEXTURE_CACHED_S + 8 * (
        progress.TEXTURE_DOWNLOAD_S - progress.TEXTURE_CACHED_S
    )
    assert small.seconds == pytest.approx(216 * per_texture) and small.group == "textures"
    cached = progress.texture_cost(spec, lambda t: True, in_flight=16)
    assert cached.download_s == 0.0 and cached.group == "textures_cached"


def test_a_server_slower_than_its_requests_in_flight_weighs_its_own_speed() -> None:
    """Counted from its 192 requests at once alone, Esri Clarity was expected three times faster
    than it downloads (437 and 456 req/s for two tiles of a user, 522 measured), and Japan's server
    five times: the time left was far too short (2026-09-15)."""
    spec = _spec("+46+008", install=False)
    assert progress.texture_download_s(128) == pytest.approx(0.25)  # Bing: unchanged
    assert progress.texture_download_s(192) == pytest.approx(0.25 * 128 / 192)  # the line alone
    assert progress.texture_download_s(192, 522) == pytest.approx(256 / 522)  # Esri Clarity
    assert progress.texture_download_s(64, 103) == pytest.approx(256 / 103)  # Japan
    assert progress.texture_download_s(16, 130) == pytest.approx(2.0)  # the line is slower
    clarity = progress.texture_cost(spec, in_flight=192, server_req_per_s=522)
    assert clarity.download_s == pytest.approx(216 * 256 / 522)
    # a user's tile of Esri Clarity: 213 textures, 54 528 pieces in 125.6 s
    now = 1_000_000.0
    reports = [(now - 60 * i, _report(125.6, 54528, 0, provider="Arc@")) for i in range(2)]
    measured = 125.6 / 213
    factor = progress.line_factor(
        reports, provider="Arc@", now=now, in_flight=192, server_req_per_s=522
    )
    assert factor == pytest.approx(measured / (progress.TEXTURE_CACHED_S + 256 / 522))
    assert 1.0 < factor < 1.2  # the model is near: before, 0.59 s against 0.22 s, a factor of 2.7
    old = progress.line_factor(reports, provider="Arc@", now=now, in_flight=192)
    assert old is not None and old > 2.5


def test_extrapolation_starts_when_the_node_moves_and_trusts_time_too(tmp_path: Path) -> None:
    """A warm tile sat at 50 % for six seconds, then gained 17 % in half a second: neither the
    plateau (its rate would be too slow) nor that first half-second (fully trusted by its gain)
    may drive the estimate."""
    clock = Clock()
    job = _job([_spec("+46+006", zl=16, install=False)], clock, tmp_path)
    node = "+46+006/BI16/textures"
    clock.t = 0.0
    job.on_event(Started(node, "net", KEY))
    for i in range(12):  # 0.5 s apart at 50 %
        clock.t = 2.0 + 0.5 * i
        job.on_event(Progress(node, 0.5, "tiles 0/0"))
    row = job._tiles["+46+006"].nodes[node]
    assert row.fraction0 == 0.5 and row.fraction0_at == pytest.approx(7.5)
    assert progress._extrapolation(row, 7.6) is None
    clock.t = 8.0
    job.on_event(Progress(node, 0.67, "encoding"))
    assert progress._extrapolation(row, 8.0) is None  # moving for 0.5 s only
    for k in range(1, 5):
        clock.t = 8.0 + 0.5 * k
        job.on_event(Progress(node, 0.67 + 0.025 * k, "encoding"))
    ext = progress._extrapolation(row, clock.t)
    assert ext is not None
    _left, trust = ext
    assert trust == pytest.approx((clock.t - 7.5 - 2.0) / (progress.TRUST_AFTER_S - 2.0), abs=1e-6)
    assert trust < 0.2  # the gain alone would trust it fully


def test_the_second_pass_speaks_plainly() -> None:
    snapshot = dataclasses.replace(
        textures_mod.ProgressSnapshot(
            tiles_done=10, tiles_total=20, parents_done=0, parents_total=0, req_per_s=12.0,
            bytes=0, in_flight=0, hedges=0, retries=0, net_errors=0, throttled=False,
            textures_total=4, built=1, hits=1, failed=0, incomplete=0, encoding=0, eta_s=None,
            elapsed_s=1.0,
        ),
        second_pass_chunks=3,
    )  # fmt: skip
    waiting = build_mod.textures_progress_message("BI16", snapshot)
    assert waiting.endswith(", 3 image piece(s) to ask for again")
    trying = build_mod.textures_progress_message(
        "BI16",
        dataclasses.replace(
            snapshot, second_pass_round=2, second_pass_rounds=3, second_pass_wait_s=8.2
        ),
    )
    assert trying.endswith(", asking again for 3 image piece(s), try 2 of 3, in 8 s")
    assert "chunk" not in waiting + trying and "round" not in waiting + trying


def test_the_textures_message_says_when_nothing_is_downloaded() -> None:
    """A user asked why building a tile again with other colours downloaded the imagery: it does
    not, but "tiles 0/0 (0 req/s)" under a step called Imagery read like a download
    (2026-09-18)."""
    snapshot = textures_mod.ProgressSnapshot(
        tiles_done=0, tiles_total=0, parents_done=0, parents_total=0, req_per_s=0.0,
        bytes=0, in_flight=0, hedges=0, retries=0, net_errors=0, throttled=False,
        textures_total=213, built=137, hits=0, failed=0, incomplete=0, encoding=0, eta_s=None,
        elapsed_s=1.0,
    )  # fmt: skip
    message = build_mod.textures_progress_message("BI16", snapshot)
    assert message == (
        "BI16: textures 137/213, nothing to download (the image pieces are in the cache)"
    )
    assert "tiles 0/0" not in message and "req/s" not in message
    # with pieces to fetch, the line counts them and carries the rate the page reads
    fetching = build_mod.textures_progress_message(
        "BI16", dataclasses.replace(snapshot, tiles_total=54528, tiles_done=720), 10.0
    )
    assert fetching == "BI16: tiles 720/54528, textures 137/213 (0 req/s, 10.0 MB/s)"


def test_the_textures_message_carries_the_download_rate() -> None:
    """The Works page shows the Imagery step's MB/s from this line (test_ui_static checks the
    page reads it); an unknown or zero rate is left out rather than written as 0."""
    snapshot = textures_mod.ProgressSnapshot(
        tiles_done=10, tiles_total=20, parents_done=0, parents_total=0, req_per_s=1392.4,
        bytes=0, in_flight=0, hedges=0, retries=0, net_errors=0, throttled=False,
        textures_total=4, built=1, hits=1, failed=0, incomplete=0, encoding=0, eta_s=None,
        elapsed_s=1.0,
    )  # fmt: skip
    assert build_mod.textures_progress_message("BI16", snapshot, 21.64).endswith(
        "(1392 req/s, 21.6 MB/s)"
    )
    for unknown in (None, 0.0):
        assert "MB/s" not in build_mod.textures_progress_message("BI16", snapshot, unknown)


def test_a_downloaded_relief_reports_what_it_received_and_its_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user asked for the rate wherever the network works: a relief that downloads (not X-Plane
    12's own) reports each file received and the rate, where the page reads it."""
    from orthostudio.dem import rule as dem_rule
    from orthostudio.dem.sources import Download

    seen: list[tuple[float, str]] = []
    answers: list[Download] = []

    class Ctx:
        cancel_event = None

        def progress(self, fraction: float, message: str) -> None:
            seen.append((fraction, message))

    def fetched(url: str) -> Download:
        return Download(url, body=b"x" * 3_000_000, status=200)

    def rule(ctx: Any) -> None:
        answers.append(dem_rule._job().download("http://viewfinderpanoramas.org/dem3/L32.zip"))

    monkeypatch.setattr(build_mod.dem_sources, "http_download", fetched)
    monkeypatch.setattr(build_mod, "run_p0_rule", rule)
    build_mod._dem_run(cast(Any, None), _spec("+46+006", zl=14))(Ctx())
    assert answers and answers[0].ok
    ((fraction, message),) = seen
    assert fraction == 0.0 and message.startswith("+46+006: elevation, 1 file(s) (")
    assert message.endswith(" MB/s)")
    assert (
        build_mod.dem_download_message(TileRef(46, 6), 0, 0, 1.0) == "+46+006: elevation, 0 file(s)"
    )


# -- weights -------------------------------------------------------------------------------


def test_node_weights_move_from_alone_to_batch() -> None:
    assert progress.node_seconds("mesh", tiles=1) == progress.ROLE_SECONDS["mesh"]
    assert progress.node_seconds("mesh", tiles=6) == progress.BATCH_SECONDS["mesh"]
    two = progress.node_seconds("mesh", tiles=2)
    assert progress.ROLE_SECONDS["mesh"] < two < progress.BATCH_SECONDS["mesh"]
    assert progress.node_seconds("osm", tiles=6) == progress.ROLE_SECONDS["osm"]
    assert progress.node_seconds("textures", textures_s=12.5) == 12.5
    assert progress.node_seconds("unknown", tiles=3) == progress.DEFAULT_SECONDS


def test_texture_weights_count_the_tile_and_the_cached_chunks() -> None:
    spec = _spec("+46+008")
    count = len(textures_covering(47, 8, 46, 9, 16, "BI"))
    assert count == 216
    assert progress.texture_seconds(spec) == pytest.approx(count * progress.TEXTURE_DOWNLOAD_S)
    assert progress.texture_seconds(spec, lambda t: True) == pytest.approx(
        count * progress.TEXTURE_CACHED_S
    )
    # zones at a finer level add their textures (Geneva's ZL18 zone added 142 to 213)
    zone = [[46.30, 6.10, 46.30, 6.20, 46.20, 6.20, 46.20, 6.10, 46.30, 6.10], 18, "BI"]
    zoned = _spec("+46+006", config={"zone_list": [zone]})
    assert progress.texture_seconds(zoned) > progress.texture_seconds(_spec("+46+006"))
    assert progress.texture_seconds(zoned, zones=False) == progress.texture_seconds(
        _spec("+46+006")
    )


def test_prepare_reads_the_chunk_store_and_keeps_weights_on_error(tmp_path: Path) -> None:
    spec = _spec("+43+005", zl=14, install=False, chunks_root=tmp_path / "chunks")
    job = Job([spec], install=False, request=None, journal_path=tmp_path / "j.jsonl")
    textures = job._tiles["+43+005"].nodes["+43+005/BI14/textures"]
    cold = textures.weight_s
    for t in textures_covering(44, 5, 43, 6, 14, "BI"):
        path = tmp_path / "chunks" / "BI" / "14" / f"{t.til_y}_{t.til_x}.chunks"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    job.prepare(None)
    assert textures.weight_s == pytest.approx(cold * progress.TEXTURE_CACHED_S / 0.30)

    def boom(t: TextureId) -> bool:
        raise OSError("unreadable")

    job.prepare(None, has_chunks=boom)  # logged, weights kept
    assert textures.weight_s == pytest.approx(cold * progress.TEXTURE_CACHED_S / 0.30)


# -- the engine announces its phases -----------------------------------------------------


def _native_spec(tmp_path: Path, **kw: Any) -> BuildSpec:
    return BuildSpec(
        tile=TileRef(43, 5),
        provider="BI",
        zl=14,
        out_dir=tmp_path / "out",
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        workdir=tmp_path / "work",
        overlay=False,
        xp12_rasters=False,
        **kw,
    )


def _empty_layers(tile: TileRef, specs: Any) -> dict[str, Any]:
    """An OSM download answering every layer with an empty extract."""
    from orthostudio.sources.osm import OsmSnapshot

    return {
        s.name: OsmSnapshot(
            tile=tile,
            layer=s.name,
            selectors=s.selectors,
            query="q",
            mirror="test",
            fetched_at="",
            generator="",
            osm_base="",
            nodes=(),
            ways=(),
            relations=(),
            digest="0" * 64,
        )
        for s in specs
    }


def test_phase_0_announces_the_rows_it_downloads_and_those_it_reuses(tmp_path: Path) -> None:
    warm = _native_spec(tmp_path)
    cold = _native_spec(tmp_path)
    cold.tile = TileRef(44, 5)
    env = BuildEnv.create([warm, cold])
    with native.osm_job(native.OsmJob(fetch=_empty_layers)):  # +43+005 is stored beforehand
        (stored,) = run_osm_phase([warm], env, handle_sigint=False).values()
    assert stored.ref is not None
    events: list[Any] = []

    def fetch(tile: TileRef, specs: Any) -> dict[str, Any]:
        raise RuntimeError("offline")  # the download fails: phase 0 reports it, not fatal

    with native.osm_job(native.OsmJob(fetch=fetch)):
        run_osm_phase([warm, cold], env, on_event=events.append, handle_sigint=False)
    phase = events[0]
    assert isinstance(phase, Phase) and phase.name == "data"
    assert phase.nodes == (("+44+005/osm", "net", "orthostudio.osm"),)
    assert phase.reused == (("+43+005/osm", stored.ref.key),)
    assert isinstance(events[1], Started) and events[1].node_id == "+44+005/osm"
    assert not any(isinstance(e, Phase) for e in events[1:])


def _failing_env(tmp_path: Path, tiles: list[TileRef]) -> tuple[BuildEnv, list[BuildSpec]]:
    """The batch of review 2: no DSFTool, fake Global Scenery bytes and an OSM download that
    fails (:func:`_no_network`): nothing builds, in milliseconds and without the
    network."""
    gs = tmp_path / "Global Scenery"
    for tile in tiles:
        dsf = gs / tile.dsf_relpath
        dsf.parent.mkdir(parents=True, exist_ok=True)
        dsf.write_bytes(b"XPLNEDSF-not-really-" + tile.name.encode())
    env = BuildEnv(
        store=Store(tmp_path / "store", fsync=False),
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        workdir=tmp_path / "work",
        global_scenery=gs,
        dsftool=None,
        registry=load_registry(),
        workers=1,
        library_path=tmp_path / "library.sqlite",
    )
    specs = [
        BuildSpec(
            tile=tile,
            provider="BI",
            zl=14,
            out_dir=tmp_path / "out",
            store_root=tmp_path / "store",
            chunks_root=tmp_path / "chunks",
            # X-Plane's relief, from the fake DSF: the elevation of a tile whose OSM download
            # fails still runs (it does not need OSM data), and a downloaded one would reach out
            relief="xplane",
            workdir=tmp_path / "work",
        )
        for tile in tiles
    ]
    return env, specs


def _no_network(tile: TileRef, layers: Any) -> dict[str, Any]:
    raise OsxpError("NET_CONNECTION_FAILED", context={"url": "overpass", "reason": "test"})


def test_build_tiles_announces_the_build_phase_then_its_nodes(tmp_path: Path) -> None:
    env, specs = _failing_env(tmp_path, [TileRef(43, 5)])
    events: list[Any] = []
    with native.osm_job(native.OsmJob(fetch=_no_network)):
        report = build_tiles(specs, on_event=events.append, cpu_in_threads=True, env=env,
                             handle_sigint=False)  # fmt: skip
    assert not report.ok
    phases = [e for e in events if isinstance(e, Phase)]
    assert [(p.name, p.nodes is None) for p in phases] == [("build", True), ("build", False)]
    declared = phases[1].nodes
    assert declared is not None and ("+43+005/osm", "net", "orthostudio.osm") in declared
    assert ("+43+005/vectors", "subprocess", "orthostudio.vectors") in declared
    assert phases[1].reused == ()
    (tile,) = report.tiles
    assert tile.error is not None and tile.error.role == "osm"  # the download's own error
    assert tile.error.error is not None and tile.error.error["code"] == "NET_CONNECTION_FAILED"
    vectors = next(n for n in tile.nodes if n.role == "vectors")
    # both its inputs fell (the fake DSF breaks the elevation too): the first to fall
    assert vectors.status == "skipped" and vectors.cause in ("+43+005/osm", "+43+005/dem")
    assert tile.osm["status"] == "failed"


def test_a_tile_does_not_wait_for_the_other_tiles_downloads(tmp_path: Path) -> None:
    """No phase 0 any more: a tile's elevation starts while a download runs, and the downloads
    still run one at a time on their lane."""
    env, specs = _failing_env(tmp_path, [TileRef(43, 5), TileRef(43, 4)])
    order: list[tuple[str, str]] = []

    def slow(tile: TileRef, layers: Any) -> dict[str, Any]:
        time.sleep(0.3)
        return _no_network(tile, layers)

    def record(event: Any) -> None:
        if isinstance(event, (Started, Done, Failed)):
            order.append((type(event).__name__, event.node_id))

    with native.osm_job(native.OsmJob(fetch=slow)):
        build_tiles(specs, on_event=record, cpu_in_threads=True, env=env, handle_sigint=False)
    osm = [i for i, (kind, node) in enumerate(order) if node.endswith("/osm")]
    starts = [i for i in osm if order[i][0] == "Started"]
    ends = [i for i in osm if order[i][0] != "Started"]
    assert len(starts) == 2 and len(ends) == 2
    assert starts[1] > ends[0]  # one download at a time
    dem_starts = [i for i, (kind, node) in enumerate(order) if kind == "Started" and "/dem" in node]
    assert len(dem_starts) == 2 and max(dem_starts) < ends[0]  # no tile waited for them


def test_the_estimate_queues_a_builds_downloads_on_their_lane(tmp_path: Path) -> None:
    """The time left of a build whose downloads run in its graph: one tile at a time on their
    lane, each tile's chain after its own download, less than the phase-0 wait before every
    tile, and the whole lane at least."""
    tiles = ["+46+005", "+46+006", "+46+007"]
    rows = (
        "osm", "dem", "coastline", "vectors", "mesh", "masks", "xp12", "overlay",
    )  # fmt: skip

    def estimate_in(phase: str) -> float:
        clock = Clock()
        job = _job([_spec(t, zl=14, install=False) for t in tiles], clock, tmp_path)
        if phase == "data":
            job.on_event(
                Phase("data", nodes=tuple((f"{t}/osm", "net", RULES["osm"]) for t in tiles))
            )
        job.on_event(Phase("build"))
        declared = tuple(
            (f"{t}/{r}", "net" if r == "osm" else progress.ROLE_KIND[r], RULES[r])
            for t in tiles
            for r in rows
        ) + tuple(
            (f"{t}/BI14/{r}", progress.ROLE_KIND[r], RULES[r])
            for t in tiles
            for r in ("dsf", "textures", "pack")
        )
        if phase == "build":
            job.on_event(Phase("build", nodes=declared))
        clock.t = 0.5
        est = progress.estimate(
            [n for ts in job._tiles.values() for n in ts.nodes.values()],
            now=clock.t,
            phase=phase,
            declared=phase == "build",
            phase_started_at=0.0,
            slots=job._slots,
        )
        assert est.eta_s is not None
        return est.eta_s

    in_graph, phase_0 = estimate_in("build"), estimate_in("data")
    osm_s = 3 * progress.ROLE_SECONDS["osm"]
    assert in_graph >= osm_s and in_graph < phase_0


def test_a_real_batch_through_the_manager(tmp_path: Path) -> None:
    """``build_tiles`` itself behind a ``JobManager``: the declared graph, its OSM downloads
    included, and the end."""
    env, specs = _failing_env(tmp_path, [TileRef(43, 5), TileRef(43, 4)])

    def build(specs: list[BuildSpec], **kw: Any) -> BuildReport:
        with native.osm_job(native.OsmJob(fetch=_no_network)):
            return build_tiles(specs, handle_sigint=False, cpu_in_threads=True, **kw)

    mgr = JobManager(jobs_dir=tmp_path / "jobs", build=build, env_factory=lambda _specs: env)
    job = mgr.start(specs)
    assert job.wait(60.0) and job.status == "failed"
    events = job.events()
    stats = [e["stats"] for e in events if e["event"] == "stats"]
    assert {s["phase"] for s in stats} == {"build"}
    assert [s["progress"] for s in stats] == sorted(s["progress"] for s in stats)
    assert stats[-1]["progress"] == 1.0 and stats[-1]["eta_low_s"] == 0.0
    state = job.state()
    for tile in state["tiles"]:
        rows = [n for stage in tile["stages"].values() for n in stage["nodes"]]
        assert all(n["status"] != "pending" for n in rows)  # nothing left behind
        osm = next(n for n in rows if n["role"] == "osm")
        vectors = next(n for n in rows if n["role"] == "vectors")
        assert osm["status"] == "failed" and vectors["status"] == "skipped"
        assert tile["stages"]["data"]["status"] == "failed"
    assert state["stats"]["done"] + state["stats"]["failed"] == sum(
        len(stage["nodes"]) for tile in state["tiles"] for stage in tile["stages"].values()
    )


def test_a_step_that_stops_reporting_does_not_announce_a_billion_hours() -> None:
    """A user read "Remaining 1 308 980 335 h 33 min" on a build whose imagery step had stopped
    moving (2026-09-23). His journal shows why: the step reported twice a second the whole time,
    always the same 196 textures of 696, so nothing was silent in the literal sense while the
    recent rate was averaged with zero four times a second. The time left is what is missing
    divided by that rate.

    This drives the real path, report by report, rather than describing a row by hand: the guard
    written before it watched for a node that stops sending, which this step never does.
    """
    from orthostudio.api.jobs import _NodeState
    from orthostudio.api.progress import ETA_MAX_S, SILENT_S, _extrapolation, observe_progress

    def moving_row(name: str) -> Any:
        row = _NodeState(node=name, role="textures", stage="imagery")
        row.status, row.started_at, row.weight_s = "running", 0.0, 400.0
        for i in range(40):  # it moves: nothing to 28 %, as his did
            observe_progress(row, 0.28 * (i + 1) / 40, 1.0 + 0.5 * i)
        return row

    row = moving_row("+36-118/BI17/textures")
    last_move = 1.0 + 0.5 * 39
    healthy = _extrapolation(row, last_move)
    assert healthy is not None and 0 < healthy[0] < 3600.0

    t = last_move
    for _ in range(600):  # then five minutes of the same figure, twice a second
        t += 0.5
        observe_progress(row, 0.28, t)
    assert row.fraction == pytest.approx(0.28)
    assert row.fraction_at == pytest.approx(t)  # it never stopped talking
    assert _extrapolation(row, t) is None, "a step that gains nothing is silent, whatever it says"

    # briefly quiet, the estimate may grow, but it may not run away
    quiet = _extrapolation(moving_row("n"), last_move + SILENT_S - 1.0)
    assert quiet is not None and quiet[0] < 9 * healthy[0]
    assert quiet[0] < ETA_MAX_S  # never a billion hours again


def test_a_figure_repeated_is_not_a_measurement() -> None:
    """The time left is what is missing divided by the recent rate, so anything that feeds that
    rate a zero while the step is only repeating itself makes the answer grow without end. This
    is the guard itself: it fails if the gain is taken out of what feeds the average, which is
    the fault a user met as "1 308 980 335 h" (2026-09-23)."""
    from orthostudio.api.jobs import _NodeState
    from orthostudio.api.progress import SILENT_S, _extrapolation, observe_progress

    row = _NodeState(node="+36-118/BI17/textures", role="textures", stage="imagery")
    row.status, row.started_at, row.weight_s = "running", 0.0, 400.0
    for i in range(40):
        observe_progress(row, 0.28 * (i + 1) / 40, 1.0 + 0.5 * i)
    measured = row.rate
    assert measured is not None and measured > 0

    t = 1.0 + 0.5 * 39
    for _ in range(int(SILENT_S / 0.5) - 4):  # it keeps talking, and says the same thing
        t += 0.5
        observe_progress(row, 0.28, t)
    assert row.rate == pytest.approx(measured), "the rate is measured on what was gained"

    still = _extrapolation(row, t)
    assert still is not None, "not yet silent: it is still answering"
    left, _trust = still
    assert 0 < left < 3600.0


def test_a_step_that_goes_quiet_speaks_for_its_neighbours_less() -> None:
    """One running node's extrapolation stands in for the ones queued behind it. Its rate was
    damped by its silence while its say was not, so the longer it said nothing the more of the
    whole estimate rested on it (found in review, 2026-09-23)."""
    from orthostudio.api.jobs import _NodeState
    from orthostudio.api.progress import REPORT_GRACE_S, _extrapolation, observe_progress

    def moving() -> Any:
        row = _NodeState(node="+36-118/BI17/textures", role="textures", stage="imagery")
        row.status, row.started_at, row.weight_s = "running", 0.0, 400.0
        for i in range(40):
            observe_progress(row, 0.28 * (i + 1) / 40, 1.0 + 0.5 * i)
        return row

    last_move = 1.0 + 0.5 * 39
    answering = _extrapolation(moving(), last_move + REPORT_GRACE_S)
    quiet = _extrapolation(moving(), last_move + 40.0)
    assert answering is not None and quiet is not None
    assert quiet[1] < answering[1] / 2, "its say falls as its silence grows"
    assert quiet[1] > 0.0, "and it does not vanish: it is still the only thing measured here"


def test_the_top_of_the_range_ends_where_the_estimate_ends() -> None:
    """The estimate itself stops at a day and the band widens the top by up to 1.7, so an
    estimate of 14 hours was published past the day. The first attempt at this capped
    ``Estimate.high_s``, which nothing reads: the range the page is shown is made by
    ``EtaSmoother.update`` from ``eta_s`` and the band, and that is where it has to hold
    (found in review, 2026-09-23)."""
    from orthostudio.api.jobs import _NodeState
    from orthostudio.api.progress import ETA_MAX_S, EtaSmoother, estimate

    rows = []
    for i in range(400):
        row = _NodeState(node=f"+36-118/BI17/n{i}", role="textures", stage="imagery")
        row.status, row.weight_s = "pending", 200.0
        rows.append(row)
    done = _NodeState(node="+36-118/BI17/first", role="textures", stage="imagery")
    done.status, done.weight_s, done.wall_s, done.ended_at, done.fraction = (
        "done",
        10.0,
        10.0,
        0.0,
        1.0,
    )
    rows.append(done)

    est = estimate(rows, now=100.0, phase="build", declared=True)
    assert est.eta_s is not None and est.eta_s > 20 * 3600.0, "close to the day it allows"
    assert est.high_s is not None and est.high_s <= ETA_MAX_S
    assert est.low_s is not None and est.low_s >= 0.0

    # what the page is actually given: jobs.py publishes this pair as eta_low_s / eta_high_s
    published = EtaSmoother().update(100.0, est.eta_s, est.band)
    assert published is not None
    low_s, high_s = published
    assert high_s <= ETA_MAX_S, f"the page is shown {high_s / 3600:.1f} h"
    assert 0.0 <= low_s <= high_s
