"""Jobs: one build at a time in a dedicated thread, a resumable journal, an aggregated state.

Spec: ``docs/specs/api.md`` section 5. A :class:`Job` wraps one ``build_tiles`` call; its
``on_event`` (called in the scheduler's loop thread) normalises every typed event
(``orthostudio.sched.events``, and the ``Phase`` of ``orthostudio.pipeline.build``) into one JSON
object, appends it to the journal (memory + a ``.jsonl`` file under ``<OSXP_HOME>/jobs``) and
updates the per-tile, per-stage state the page reads. Its ``stats`` are the job's own, over every
phase (``orthostudio.api.progress``), not the last scheduler's. :class:`JobManager` owns the
threads, the FIFO queue and the past jobs found on disk.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import functools
import json
import logging
import math
import os
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orthostudio.api.progress import (
    ROLE_KIND,
    EtaSmoother,
    TextureCost,
    estimate,
    line_factor,
    node_seconds,
    observe_progress,
    read_line_history,
    texture_cost,
    weight_of,
    weighted_progress,
)
from orthostudio.api.stages import STAGES, Stage, action_for, parse_node_id, stage_of
from orthostudio.errors import OsxpError, wrap
from orthostudio.fsutil import atomic_write_text
from orthostudio.imagery.chunks import ChunkStore
from orthostudio.imagery.grid import TextureId
from orthostudio.imagery.providers import load_registry
from orthostudio.model import TileRef
from orthostudio.pipeline.build import (
    BuildEnv,
    BuildEvent,
    BuildReport,
    BuildSpec,
    Phase,
    build_tiles,
    default_subprocess_slots,
)
from orthostudio.pipeline.home import osxp_home
from orthostudio.pipeline.textures import default_workers
from orthostudio.sched import Done, Failed, Progress, Started, Stats

__all__ = [
    "CancelRequested",
    "Job",
    "JobBusyError",
    "JobManager",
    "TileInBuildError",
    "default_jobs_dir",
    "error_json",
    "stage_status",
]

log = logging.getLogger("orthostudio.api.jobs")

LOG_PERIOD_S = 1.0
"""At most one ``log`` event per node per second (textures progress lines)."""
STATS_MIN_PERIOD_S = 0.5
"""The scheduler sends ``Stats`` after every node and every second; the journal keeps at most
two ``stats`` lines a second (a burst of sixty hits was sixty lines)."""
STATS_PERIOD_S = 1.0
"""...and at least one a second while the job runs, gaps between phases included."""
TICK_S = 0.25
JOB_STATUSES = ("queued", "running", "done", "failed", "cancelled")
NODE_ENDED = ("done", "hit", "failed", "skipped", "cancelled")


class CancelRequested(BaseException):
    """Raised from ``on_event`` to stop the scheduler cooperatively (spec 5.1)."""


class JobBusyError(Exception):
    """A job is queued or running; only one build at a time."""


class TileInBuildError(Exception):
    """Tiles asked for are in a build already, running or queued: a tile is in one build at a time.
    A second one would build it again once the first ends, and install it a second time."""

    def __init__(self, tiles: Sequence[str], job_ids: Sequence[str]) -> None:
        super().__init__(f"{' '.join(tiles)} already in {' '.join(job_ids)}")
        self.tiles = list(tiles)
        self.job_ids = list(job_ids)


def default_jobs_dir() -> Path:
    return osxp_home() / "jobs"


def error_json(err: OsxpError | Mapping[str, Any]) -> dict[str, Any]:
    """The API rendering of an error: ``OsxpError.to_dict`` plus the page's ``action``.

    ``action`` is the page's (``retry`` / ``settings`` / ``none``); the scheduler's
    ``stop`` / ``continue`` moves to ``node_action``.
    """
    d = dict(err.to_dict()) if isinstance(err, OsxpError) else dict(err)
    d["node_action"] = d.get("action")
    d["action"] = action_for(str(d.get("code", "")))
    return d


def _now() -> float:
    return time.time()


def _new_id() -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}"


# -- state ----------------------------------------------------------------------------------------


@dataclass(slots=True)
class _NodeState:
    node: str
    role: str
    stage: Stage | None
    status: str = "pending"
    key: str | None = None
    hit: bool | None = None
    wall_s: float = 0.0
    fraction: float = 0.0
    started_at: float | None = None
    error: dict[str, Any] | None = None
    cause: str | None = None
    kind: str = ""
    group: str = ""
    """Speed group of the estimate: ``textures`` / ``textures_cached`` for a textures node."""
    weight_s: float = 0.0
    """Expected seconds (``api/progress.py``): the node's weight in progress and time left."""
    ended_at: float | None = None
    fraction0: float | None = None
    """Fraction the node reported last before it rose, and when (``fraction0_at``): the offset
    of its extrapolation."""
    fraction0_at: float | None = None
    fraction_at: float | None = None
    rate: float | None = None
    """Recent rate of its fraction per second (``progress.observe_progress``)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "role": self.role,
            "status": self.status,
            "key": self.key,
            "hit": self.hit,
            "wall_s": round(self.wall_s, 3),
            "fraction": round(self.fraction, 4),
            "weight_s": round(weight_of(self), 3),
        }


@dataclass(slots=True)
class _TileState:
    tile: str
    provider: str
    zl: int
    install: bool
    target_role: str
    nodes: dict[str, _NodeState] = field(default_factory=dict)

    def stage_nodes(self, stage: Stage) -> list[_NodeState]:
        return [n for n in self.nodes.values() if n.stage == stage]

    def stage_dict(self, stage: Stage, job_status: str | None = None) -> dict[str, Any]:
        nodes = self.stage_nodes(stage)
        if not nodes:
            status = "skipped" if stage == "install" and not self.install else "pending"
            return {"status": status, "fraction": 0.0, "wall_s": 0.0, "nodes": []}
        return {
            "status": stage_status([n.status for n in nodes], job_status),
            "fraction": round(weighted_progress(nodes), 4),
            "wall_s": round(sum(n.wall_s for n in nodes), 3),
            "nodes": [n.to_dict() for n in nodes],
        }

    def errors(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for n in self.nodes.values():
            if n.error is not None and n.cause is None:
                out.append({**n.error, "node": n.node, "stage": n.stage, "tile": self.tile})
        return out

    def status(self, job_status: str) -> str:
        if any(n.status == "failed" for n in self.nodes.values()):
            return "failed"
        target = [n for n in self.nodes.values() if n.role == self.target_role]
        if target and all(n.status in ("done", "hit") for n in target):
            return "done"
        if job_status == "cancelled":
            return "cancelled"
        if job_status in ("done", "failed"):
            skipped = any(n.status == "skipped" for n in self.nodes.values())
            return "failed" if skipped else job_status
        if any(n.status != "pending" for n in self.nodes.values()):
            return "running"
        return "pending"

    def to_dict(self, job_status: str) -> dict[str, Any]:
        return {
            "tile": self.tile,
            "provider": self.provider,
            "zl": self.zl,
            "status": self.status(job_status),
            "stages": {s: self.stage_dict(s, job_status) for s in STAGES},
            "errors": self.errors(),
        }


def stage_status(statuses: Sequence[str], job_status: str | None = None) -> str:
    """A stage's status from its nodes' (spec 5.4).

    ``running`` only while one of its nodes runs. ``waiting`` when some did real work (``done``)
    and the others have not started: the assembly of a tile read ``running`` for 220 s while its
    rasters and overlay were built and its pack waited for the textures. Hits alone do not
    start a stage: with the others still to run it is ``pending`` (a tile whose OSM data was
    there read ``running`` from the first second). Once the job is over (``job_status``)
    nothing runs or waits: such a stage is ``cancelled`` when the job was, else ``skipped``.
    """
    if "failed" in statuses:
        return "failed"
    if "cancelled" in statuses:
        return "cancelled"
    if "skipped" in statuses:
        return "skipped"
    if statuses and all(s in ("done", "hit") for s in statuses):
        return "hit" if all(s == "hit" for s in statuses) else "done"
    if "running" not in statuses and "done" not in statuses:
        return "pending"
    if job_status in ("done", "failed", "cancelled"):
        return "cancelled" if job_status == "cancelled" else "skipped"
    return "running" if "running" in statuses else "waiting"


def _expected_nodes(spec: BuildSpec) -> list[tuple[str, str]]:
    """The node ids a spec declares (``pipeline-build.md`` 2 and 8), as ``(id, role)``.

    A prediction from the spec alone, so that the page has every row before phase 0 ends; the
    main graph's ``Phase`` event then replaces it with what ``declare`` declared (rows added,
    unused ones dropped). ``coastline`` is always declared: it reads the tile's OSM snapshot, as
    the vector stage does.
    """
    name, level = spec.tile.name, spec.level
    out: list[tuple[str, str]] = []
    if spec.osm_fetch:
        # Runs in phase 0 only when the tile's OSM data is missing; otherwise phase 0 reports
        # the row as reused (a hit) before any download starts.
        out.append((f"{name}/osm", "osm"))
    out += [(f"{name}/dem", "dem"), (f"{name}/vectors", "vectors")]
    out += [(f"{name}/coastline", "coastline"), (f"{name}/mesh", "mesh")]
    out.append((f"{name}/masks", "masks"))
    if spec.xp12_rasters:
        out.append((f"{name}/xp12", "xp12"))
    out.append((f"{name}/{level}/dsf", "dsf"))
    out.append((f"{name}/{level}/textures", "textures"))
    if spec.overlay:
        out.append((f"{name}/overlay", "overlay"))
    out.append((f"{name}/{level}/pack", "pack"))
    if spec.install:
        out.append((f"{name}/{level}/install", "install"))
    return out


# -- job ------------------------------------------------------------------------------------------


def _relief_of(spec: Any) -> str:
    """Where the tiles of ``spec`` take their heights: ``xplane`` (X-Plane 12's own relief, or
    ``view`` in the test suite), ``copernicus`` (``COP30``), ``usgs`` (``NED1/3``), or ``file``
    (the user's own)."""
    custom = str(spec.config.get("custom_dem", "") or "").strip()
    if not custom:
        return str(spec.relief)
    return {"COP30": "copernicus", "NED1/3": "usgs"}.get(custom, "file")


class Job:
    """One build: its specs, its journal and its aggregated state (thread-safe)."""

    def __init__(
        self,
        specs: Sequence[BuildSpec],
        *,
        install: bool,
        request: Mapping[str, Any] | None,
        journal_path: Path,
        job_id: str | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.id = job_id or _new_id()
        self.specs = list(specs)
        self.install = install
        self.request = dict(request) if request is not None else None
        self.journal_path = journal_path
        self.status = "queued"
        self.created_at = _now()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.report: dict[str, Any] | None = None
        self.decisions: list[dict[str, Any]] = []
        self.job_error: dict[str, Any] | None = None
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._file: Any = None
        self._last_log: dict[str, float] = {}
        self._stats: dict[str, Any] | None = None
        self._clock: Callable[[], float] = clock if clock is not None else time.perf_counter
        """One clock for the journal's ``ts``, the node times and the estimate."""
        self._t0 = self._clock()
        self._run_t0: float | None = None
        self._tiles: dict[str, _TileState] = {}
        self._loaded = False
        # -- job-level progress (api/progress.py) --
        self._phase = "build"
        """``build``, or ``data`` while a build that downloads its OSM data before its graph (an
        older engine's phase 0, the tests) says so."""
        self._phase_at: float | None = None
        """When the ``build`` phase began (the declaration of the main graph follows)."""
        self._declared = False
        self._progress = 0.0
        self._last_stats_at: float | None = None
        self._slots: dict[str, int] = {
            "overpass": 1,
            "net": 1,
            "io": 2,
            "subprocess": default_subprocess_slots(),
            "cpu": default_workers(),
        }
        self._texture_cost: dict[str, TextureCost] = {}
        self._prior: dict[str, float] = {}
        """The speed the groups start from: ``textures`` from the line's recent builds."""
        self._tiles_of_specs = {spec.tile.name for spec in self.specs}
        self._eta = EtaSmoother()
        """What ``stats`` publishes of the estimate: it moves at a bounded pace."""
        self._ticker: threading.Thread | None = None
        self._ticker_stop = threading.Event()
        for spec in self.specs:
            # the tile's own textures, all downloaded: prepare() refines it before the build
            self._texture_cost[f"{spec.tile.name}/{spec.level}"] = texture_cost(spec, zones=False)
        for spec in self.specs:
            ts = _TileState(
                spec.tile.name,
                spec.provider,
                spec.zl,
                spec.install,
                "install" if spec.install else "pack",
            )
            for node_id, role in _expected_nodes(spec):
                ts.nodes[node_id] = self._new_row(node_id, role)
            self._tiles.setdefault(spec.tile.name, ts)

    # -- journal ----------------------------------------------------------------------------

    def _open_file(self) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.journal_path.open("a", encoding="utf-8")

    def _append(self, event: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            entry: dict[str, Any] = {
                "seq": len(self._events) + 1,
                "ts": round(self._clock() - self._t0, 3),
                "event": event,
                **fields,
            }
            self._events.append(entry)
            if self._file is not None:
                try:
                    self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    self._file.flush()
                except OSError:
                    log.exception("journal write failed for job %s", self.id)
            return entry

    def events(self, after: int = 0) -> list[dict[str, Any]]:
        """Journal entries with ``seq > after`` (from disk for a job loaded from a past run)."""
        with self._lock:
            if self._loaded and not self._events:
                self._events = _read_journal(self.journal_path)
            return [e for e in self._events if e["seq"] > after]

    @property
    def last_seq(self) -> int:
        with self._lock:
            return len(self._events) if not self._loaded else len(self.events())

    @property
    def finished(self) -> bool:
        return self.status in ("done", "failed", "cancelled")

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    # -- control -----------------------------------------------------------------------------

    def cancel(self) -> bool:
        if self.finished:
            return False
        self._cancel.set()
        return True

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    # -- events from the scheduler (loop thread of the job) -----------------------------------

    def _cost_of(self, node_id: str, role: str) -> TextureCost | None:
        parts = node_id.split("/")
        if role != "textures" or len(parts) < 3:
            return None
        return self._texture_cost.get("/".join(parts[:2]))

    def _weigh(self, st: _NodeState) -> None:
        """Set the row's weight and speed group (``api/progress.py``)."""
        cost = self._cost_of(st.node, st.role)
        st.weight_s = node_seconds(
            st.role,
            textures_s=cost.seconds if cost is not None else None,
            tiles=len(self._tiles_of_specs),
        )
        st.group = cost.group if cost is not None else ""

    def _new_row(self, node_id: str, role: str) -> _NodeState:
        st = _NodeState(node_id, role, stage_of(role), kind=ROLE_KIND.get(role, ""))
        self._weigh(st)
        return st

    def _node(self, node_id: str) -> _NodeState:
        tile, role = parse_node_id(node_id)
        ts = self._tiles.get(tile)
        if ts is None:
            ts = _TileState(tile, "", 0, False, "pack")
            self._tiles[tile] = ts
        st = ts.nodes.get(node_id)
        if st is None:
            st = self._new_row(node_id, role)
            ts.nodes[node_id] = st
        return st

    def on_event(self, event: BuildEvent) -> None:
        """``build_tiles`` callback: normalise, journal, aggregate; raise on cancel."""
        with self._lock:
            self._handle(event)
        if self._cancel.is_set():
            raise CancelRequested(self.id)

    def _handle(self, event: BuildEvent) -> None:
        if isinstance(event, Stats):
            # The scheduler's numbers are its phase's; they only tell that time has passed.
            self._emit_stats(min_period=STATS_MIN_PERIOD_S)
            return
        if isinstance(event, Phase):
            self._on_phase(event)
            return
        now = self._clock()
        st = self._node(event.node_id)
        tile = parse_node_id(event.node_id)[0]
        base = {"tile": tile, "stage": st.stage, "node": st.node, "role": st.role}
        if isinstance(event, Started):
            if self._phase_at is None and st.role != "osm":
                # A build that does not announce its phases (a fake): its main graph runs.
                self._phase, self._phase_at, self._declared = "build", now, True
            if st.status not in ("done", "hit"):  # a second pass re-declares what it built
                st.status = "running"
                st.key = event.key
                st.started_at = now
                st.fraction, st.ended_at = 0.0, None
                st.fraction0 = st.fraction0_at = st.fraction_at = st.rate = None
            st.kind = event.kind
            self._append(
                "started", **base, key=event.key, kind=event.kind, weight_s=_weight_json(st)
            )
        elif isinstance(event, Progress):
            observe_progress(st, event.fraction, now)
            self._append(
                "progress",
                **base,
                fraction=round(st.fraction, 4),
                message=event.message,
                weight_s=_weight_json(st),
            )
            last_log = self._last_log.get(st.node, now - LOG_PERIOD_S)
            if st.stage == "imagery" and now - last_log >= LOG_PERIOD_S:
                self._last_log[st.node] = now
                self._append("log", **base, message=event.message)
        elif isinstance(event, Done):
            if not (event.hit and st.status == "done"):  # keep what the first pass built
                st.status = "hit" if event.hit else "done"
                st.key = event.key
                st.hit = event.hit
                st.wall_s = event.wall_s
                st.ended_at = now
            st.fraction = 1.0
            self._append(
                "done",
                **base,
                key=event.key,
                hit=event.hit,
                wall_s=round(event.wall_s, 3),
                weight_s=_weight_json(st),
            )
        elif isinstance(event, Failed):
            skipped = event.cause is not None
            cancelled = event.error.code == "SYS_CANCELLED"
            st.status = "skipped" if skipped else ("cancelled" if cancelled else "failed")
            st.error = None if cancelled and not skipped else error_json(event.error)
            st.cause = event.cause
            st.ended_at = now
            if st.started_at is not None:
                st.wall_s = now - st.started_at
            self._append(
                "failed",
                **base,
                error=error_json(event.error),
                skipped=skipped,
                cause=event.cause,
                weight_s=_weight_json(st),
            )

    # -- phases (api.md 5.6) -----------------------------------------------------------------

    def _on_phase(self, event: Phase) -> None:
        now = self._clock()
        if event.name == "data":
            self._phase = "data"
        elif self._phase_at is None:
            self._phase, self._phase_at = "build", now
        for node_id, key in event.reused:
            self._reuse(node_id, key)
        if event.name == "build" and event.nodes is not None:
            self._reconcile(event.nodes)
            self._declared = True
        self._emit_stats()

    def _reuse(self, node_id: str, key: str | None) -> None:
        """A row that will not run because what it produces is there: a hit, journaled."""
        st = self._node(node_id)
        if st.status != "pending":
            return
        st.status, st.hit, st.fraction, st.wall_s = "hit", True, 1.0, 0.0
        st.key = key
        tile = parse_node_id(node_id)[0]
        self._append(
            "done", tile=tile, stage=st.stage, node=st.node, role=st.role, key=key, hit=True,
            wall_s=0.0, weight_s=0.0,
        )  # fmt: skip

    def _reconcile(self, nodes: Sequence[tuple[str, str, str]]) -> None:
        """Replace the predicted rows by the declared nodes; the rows that did run stay.

        An ``osm`` row stays too: the graph declares the downloads it runs, and a pending row it
        does not declare had its data already (a snapshot in the store, a phase 0 that reported
        nothing), so it becomes a hit. A tile the graph left out (its OSM layers could not be
        had) keeps only its ``osm`` row."""
        ids = {node_id for node_id, _kind, _rule in nodes}
        for node_id, kind, _rule in nodes:
            st = self._node(node_id)
            st.kind = kind
            if st.status in ("failed", "skipped", "cancelled"):  # a second pass runs it again
                st.status, st.error, st.cause = "pending", None, None
                st.fraction, st.wall_s, st.started_at, st.ended_at = 0.0, 0.0, None, None
                st.fraction0 = st.fraction0_at = st.fraction_at = st.rate = None
            if st.status == "pending":
                self._weigh(st)
        for ts in self._tiles.values():
            for node_id, st in list(ts.nodes.items()):
                if node_id in ids or st.status != "pending":
                    continue
                if st.role == "osm":
                    self._reuse(node_id, None)
                else:
                    del ts.nodes[node_id]

    # -- job-level statistics ----------------------------------------------------------------

    def prepare(
        self,
        env: Any = None,
        *,
        has_chunks: Callable[[TextureId], bool] | None = None,
        history: Sequence[tuple[float, Mapping[str, Any]]] | None = None,
    ) -> None:
        """Refine the weights before the build starts (job thread; nothing has run yet).

        The textures whose chunks are on disk cost an encoding, not a download (a stat per
        sampled texture of the chunk store, ``has_chunks`` in tests); the zones add their
        textures; a download costs more from a provider that takes fewer requests at once;
        the CPU slots are the environment's workers. The downloads
        start at the speed the line showed on the latest builds of each provider, read from
        the texture reports next to the environment's logs (``history`` in tests: ``(mtime,
        report)``). Never raises: a failure keeps the provisional weights.
        """
        costs: dict[str, TextureCost] = {}
        prior: dict[str, float] = {}
        try:
            registry = env.registry if isinstance(env, BuildEnv) else None
            flights: dict[str, tuple[int | None, float | None]] = {}
            for spec in self.specs:
                if registry is None:
                    registry = load_registry(spec.registry_path)
                provider = registry.get(spec.provider)
                in_flight = spec.max_in_flight or (provider.max_in_flight if provider else None)
                server = provider.server_req_per_s if provider else None
                flights[f"{spec.tile.name}/{spec.level}"] = (in_flight, server)
                check = has_chunks if has_chunks is not None else ChunkStore(spec.chunks_root).has
                costs[f"{spec.tile.name}/{spec.level}"] = texture_cost(
                    spec, check, in_flight=in_flight, server_req_per_s=server
                )
            reports = history
            if reports is None and isinstance(env, BuildEnv):
                reports = read_line_history(env.logs)
            if reports:
                prior = self._line_prior(costs, flights, reports)
        except Exception:
            log.exception("job %s: the weights of the progress could not be refined", self.id)
            costs, prior = {}, {}
        with self._lock:
            self._texture_cost.update(costs)
            self._prior = prior
            # nothing has run: a line the ticker published from the provisional weights (while
            # the environment was created) is not a position the range must move away from
            self._eta = EtaSmoother()
            if isinstance(env, BuildEnv):
                self._slots["cpu"] = max(1, int(env.workers))
            for ts in self._tiles.values():
                for st in ts.nodes.values():
                    if st.status == "pending":
                        self._weigh(st)

    def _line_prior(
        self,
        costs: Mapping[str, TextureCost],
        flights: Mapping[str, tuple[int | None, float | None]],
        reports: Sequence[tuple[float, Mapping[str, Any]]],
    ) -> dict[str, float]:
        """The ``textures`` speed to start from: each provider's :func:`line_factor`, weighted
        by the download seconds of the tiles that use it; empty without history."""
        now = time.time()
        total = weighted = 0.0
        for spec in self.specs:
            key = f"{spec.tile.name}/{spec.level}"
            cost = costs.get(key)
            if cost is None or cost.download_s <= 0:
                continue
            in_flight, server = flights.get(key, (None, None))
            factor = line_factor(
                reports,
                provider=spec.provider,
                now=now,
                in_flight=in_flight,
                server_req_per_s=server,
            )
            if factor is None:
                continue
            total += cost.download_s
            weighted += cost.download_s * factor
        return {"textures": weighted / total} if total > 0 else {}

    def unfinished_tiles(self) -> list[str]:
        """The tiles this job has not finished: a finished one (packed, and installed when the job
        installs) is not touched again by the job, a failed one may be by its second pass."""
        with self._lock:
            return [name for name, ts in self._tiles.items() if ts.status(self.status) != "done"]

    def begin(self) -> None:
        """The job thread starts the build: the clock of ``elapsed_s`` starts here."""
        with self._lock:
            self.started_at = _now()
            self.status = "running"
            self._run_t0 = self._clock()

    def _stats_now(self) -> dict[str, Any]:
        now = self._clock()
        rows = [n for ts in self._tiles.values() for n in ts.nodes.values()]
        counts = {"running": 0, "pending": 0, "done": 0, "failed": 0, "hits": 0}
        for n in rows:
            if n.status in ("running", "pending"):
                counts[n.status] += 1
            elif n.status in ("done", "hit"):
                counts["done"] += 1
                counts["hits"] += 1 if n.status == "hit" else 0
            else:
                counts["failed"] += 1
        est = estimate(
            rows,
            now=now,
            phase=self._phase,
            declared=self._declared,
            phase_started_at=self._phase_at,
            slots=self._slots,
            prior=self._prior,
        )
        if self.finished:
            low: float | None = 0.0
            high: float | None = 0.0
        else:
            published = self._eta.update(now, est.eta_s, est.band)
            low, high = published if published is not None else (None, None)
        progress = 1.0 if self.status == "done" else est.progress
        self._progress = min(1.0, max(self._progress, progress))
        origin = self._run_t0 if self._run_t0 is not None else self._t0
        return {
            **counts,
            "elapsed_s": round(max(0.0, now - origin), 3),
            "progress": math.floor(self._progress * 10_000) / 10_000,
            "eta_low_s": None if low is None else round(low, 1),
            "eta_high_s": None if high is None else round(high, 1),
            "phase": self._phase,
        }

    def _emit_stats(self, *, min_period: float = 0.0) -> None:
        """Journal a ``stats`` line unless one was written less than ``min_period`` ago."""
        with self._lock:
            now = self._clock()
            if self._last_stats_at is not None and now - self._last_stats_at < min_period:
                return
            self._last_stats_at = now
            self._stats = self._stats_now()
            self._append("stats", stats=self._stats)

    def start_ticker(self) -> None:
        """A thread that keeps ``stats`` coming while no scheduler runs (between phases)."""
        self._ticker_stop.clear()
        self._ticker = threading.Thread(target=self._tick, name=f"osxp-job-{self.id}-stats")
        self._ticker.daemon = True
        self._ticker.start()

    def stop_ticker(self) -> None:
        self._ticker_stop.set()
        if self._ticker is not None:
            self._ticker.join(timeout=2.0)
            self._ticker = None

    def _tick(self) -> None:
        while not self._ticker_stop.wait(TICK_S):
            with self._lock:
                if self.finished:
                    return
                self._emit_stats(min_period=STATS_PERIOD_S)

    # -- end of the run (job thread) ----------------------------------------------------------

    def _finish(self, status: str, report: BuildReport | None, env: BuildEnv | None) -> None:
        with self._lock:
            self.status = status
            self.finished_at = _now()
            if report is not None:
                self.report = report.to_dict()
                self._adopt_report(report)
            self.decisions = self._decisions(env)
            self._emit_stats()  # the last one: progress 1 when done, nothing left
            self._append(
                "finished",
                status=status,
                report=self.report,
                decisions=self.decisions,
                error=self.job_error,
            )
            if self._file is not None:
                with contextlib.suppress(OSError):
                    self._file.close()
                self._file = None

    def _adopt_report(self, report: BuildReport) -> None:
        """Nodes the report knows and the events did not (the second pass, ``repair``)."""
        for t in report.tiles:
            for n in t.nodes:
                if n.id in self._tiles.get(t.tile, _TileState("", "", 0, False, "")).nodes:
                    continue
                st = self._node(n.id)
                st.status = {"built": "done"}.get(n.status, n.status)
                st.key = n.key
                st.wall_s = n.wall_s
                if n.error is not None:
                    st.error = error_json(n.error)
                    st.cause = n.cause

    def _decisions(self, env: BuildEnv | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ts in self._tiles.values():
            counts = {"hit": 0, "built": 0, "failed": 0, "skipped": 0}
            per_stage: dict[str, float] = dict.fromkeys(STAGES, 0.0)
            degraded: dict[str, dict[str, Any]] = {}
            for n in ts.nodes.values():
                if n.status == "hit":
                    counts["hit"] += 1
                elif n.status == "done":
                    counts["built"] += 1
                elif n.status in ("failed", "skipped"):
                    counts[n.status] += 1
                if n.stage is not None:
                    per_stage[n.stage] += n.wall_s
                if n.error is not None and n.error.get("severity") in ("degraded", "info"):
                    d = degraded.setdefault(
                        str(n.error["code"]),
                        {"code": n.error["code"], "count": 0, "message": n.error["message"]},
                    )
                    d["count"] += 1
            out.append({"tile": ts.tile, "kind": "nodes", **counts})
            out.append(
                {
                    "tile": ts.tile,
                    "kind": "stage_time",
                    "seconds": {k: round(v, 2) for k, v in per_stage.items()},
                }
            )
            for d in degraded.values():
                out.append({"tile": ts.tile, "kind": "degraded", **d})
            tex = _textures_decision(env, ts)
            if tex is not None:
                out.append({"tile": ts.tile, "kind": "textures", **tex})
        if self.report is not None:
            for t in self.report.get("tiles", []):
                pack_dir = t.get("pack_dir")
                if pack_dir:
                    out.append(
                        {
                            "tile": t["tile"],
                            "kind": "pack",
                            "path": pack_dir,
                            "bytes": _dir_bytes(Path(pack_dir)),
                            "installed": bool(t.get("installed")),
                        }
                    )
        return out

    # -- views -------------------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "install": self.install,
                "tiles": [t.tile for t in self._tiles.values()],
                "provider": self.specs[0].provider if self.specs else None,
                "zl": self.specs[0].zl if self.specs else None,
                # which relief the tiles are built on, so that Works says it while it builds
                # (a user missed it during a build, 2026-09-17)
                "relief": _relief_of(self.specs[0]) if self.specs else None,
                "ok": self.status == "done",
            }

    def state(self) -> dict[str, Any]:
        with self._lock:
            tiles = [t.to_dict(self.status) for t in self._tiles.values()]
            errors = [e for t in tiles for e in t["errors"]]
            if self.job_error is not None:
                errors.append({**self.job_error, "node": None, "stage": None, "tile": None})
            eta = None
            stats = self._stats
            if stats is not None and not self.finished and stats.get("eta_low_s") is not None:
                eta = {"low_s": stats["eta_low_s"], "high_s": stats.get("eta_high_s")}
            return {
                **self.summary(),
                "request": self.request,
                "tiles": tiles,
                "errors": errors,
                "stats": self._stats,
                "eta": eta,
                "last_seq": self.last_seq,
                "report": self.report,
                "decisions": self.decisions,
            }

    # -- persistence -------------------------------------------------------------------------

    def state_path(self) -> Path:
        return self.journal_path.with_suffix(".json")

    def save_state(self) -> None:
        doc = {**self.state(), "specs": [_spec_to_json(s) for s in self.specs]}
        atomic_write_text(self.state_path(), json.dumps(doc, ensure_ascii=False, indent=1))

    @classmethod
    def load(cls, state_path: Path) -> Job:
        """A finished job read back from ``<id>.json`` (events come from the ``.jsonl``)."""
        doc = json.loads(state_path.read_text(encoding="utf-8"))
        specs = [_spec_from_json(s) for s in doc.get("specs", [])]
        job = cls(
            specs,
            install=bool(doc.get("install")),
            request=doc.get("request"),
            journal_path=state_path.with_suffix(".jsonl"),
            job_id=str(doc["id"]),
        )
        job.status = str(doc.get("status", "failed"))
        job.created_at = float(doc.get("created_at") or 0.0)
        job.started_at = doc.get("started_at")
        job.finished_at = doc.get("finished_at")
        job.report = doc.get("report")
        job.decisions = list(doc.get("decisions") or [])
        job.job_error = doc.get("job_error")
        job._stats = doc.get("stats")
        job._loaded = True
        for t in doc.get("tiles", []):
            ts = job._tiles.get(t["tile"])
            if ts is None:
                continue
            saved = [n for stage in t.get("stages", {}).values() for n in stage.get("nodes", [])]
            if saved:
                # The saved rows are the job's; a row predicted from the spec today that the job
                # never had (the prediction grew since) is not left pending in a finished job.
                ids = {n["node"] for n in saved}
                for node_id in [i for i in ts.nodes if i not in ids]:
                    del ts.nodes[node_id]
            for n in saved:
                st = job._node(n["node"])
                st.status = n["status"]
                st.key = n.get("key")
                st.hit = n.get("hit")
                st.wall_s = float(n.get("wall_s") or 0.0)
                st.fraction = float(n.get("fraction") or 0.0)
                if n.get("weight_s") is not None:
                    st.weight_s = float(n["weight_s"])
            if any(n["role"] != "osm" and n["status"] != "pending" for n in saved):
                # Written before phase 0 reported its reused rows: the tile went past phase 0,
                # so an OSM row that never ran had its data already.
                for st in ts.nodes.values():
                    if st.role == "osm" and st.status == "pending":
                        st.status, st.hit, st.fraction = "hit", True, 1.0
            for e in t.get("errors", []):
                if e.get("node") in ts.nodes:
                    ts.nodes[e["node"]].error = {
                        k: v for k, v in e.items() if k not in ("node", "stage", "tile")
                    }
        job._done.set()
        return job


def _weight_json(st: _NodeState) -> float:
    """The weight a page uses for the row's share of its stage (0 when the node does not run)."""
    return round(weight_of(st), 3)


def _read_journal(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    with contextlib.suppress(ValueError):
                        out.append(json.loads(line))
    except OSError:
        return []
    return out


def _dir_bytes(path: Path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                with contextlib.suppress(OSError):
                    total += os.stat(os.path.join(root, f)).st_size
    except OSError:
        return 0
    return total


def _textures_decision(env: BuildEnv | None, ts: _TileState) -> dict[str, Any] | None:
    """Read the textures report ``build_tiles`` wrote next to the logs (spec 5.5)."""
    if env is None:
        return None
    level = f"{ts.provider}{ts.zl}"
    try:
        candidates = sorted(
            env.logs.glob(f"textures-{ts.tile}-{level}-*.json"),
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        return None
    if not candidates:
        return None
    try:
        doc = json.loads(candidates[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    outcomes = doc.get("outcomes", [])
    counts = doc.get("counts", {})
    return {
        "total": len(outcomes),
        "built": int(counts.get("built", 0)),
        "hits": int(counts.get("hits", 0)),
        "missing": sum(1 for o in outcomes if o.get("status") in ("incomplete", "failed")),
        "parent_fallback": sum(int(o.get("from_fallback", 0)) for o in outcomes),
        "placeholders": sum(int(o.get("placeholders", 0)) for o in outcomes),
        # chunks a transient failure sent to the second pass, and those it brought back
        "second_pass": int(counts.get("chunks_second_pass", 0)),
        "recovered": int(counts.get("chunks_recovered", 0)),
        "report": str(candidates[-1]),
    }


_SPEC_JSON_FIELDS = (
    "provider",
    "zl",
    "out_dir",
    "global_scenery_dir",
    "config",
    "install",
    "custom_scenery",
    "overlay",
    "xp12_rasters",
    "creation_agent",
    "store_root",
    "chunks_root",
    "workdir",
    "workers",
    "encoder",
    "link",
    "library_path",
)


def _spec_to_json(spec: BuildSpec) -> dict[str, Any]:
    out: dict[str, Any] = {"tile": spec.tile.name}
    for name in _SPEC_JSON_FIELDS:
        v = getattr(spec, name)
        out[name] = str(v) if isinstance(v, Path) else v
    return out


_PATH_FIELDS = frozenset(
    {
        "out_dir",
        "global_scenery_dir",
        "custom_scenery",
        "store_root",
        "chunks_root",
        "workdir",
        "library_path",
    }
)


def _spec_from_json(doc: Mapping[str, Any]) -> BuildSpec:
    kw: dict[str, Any] = {}
    for name in _SPEC_JSON_FIELDS:
        if name not in doc:
            continue
        v = doc[name]
        kw[name] = Path(v) if name in _PATH_FIELDS and v is not None else v
    return BuildSpec(tile=TileRef.parse(str(doc["tile"])), **kw)


# -- manager ---------------------------------------------------------------------------------------

BuildFn = Callable[..., BuildReport]
EnvFactory = Callable[..., Any]


def _call_env_factory(factory: EnvFactory | None, specs: Sequence[BuildSpec]) -> Any:
    """``factory(specs)``; a zero-argument factory (the P2b contract's literal form) too."""
    if factory is None:
        return None
    try:
        return factory(specs)
    except TypeError as exc:
        if "positional argument" not in str(exc):
            raise
        return factory()


class JobManager:
    """One active job, a FIFO of queued ones, the past jobs of this home (spec 5). A tile is in one
    of them at most."""

    def __init__(
        self,
        *,
        jobs_dir: Path | None = None,
        build: BuildFn | None = None,
        env_factory: EnvFactory | None = BuildEnv.create,
    ) -> None:
        self.jobs_dir = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
        self.build: BuildFn = (
            build if build is not None else functools.partial(build_tiles, handle_sigint=False)
        )
        self.env_factory = env_factory
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._queue: deque[Job] = deque()
        self._active: Job | None = None
        self._threads: dict[str, threading.Thread] = {}
        self._load_past()

    # -- past jobs ---------------------------------------------------------------------------

    def _load_past(self) -> None:
        if not self.jobs_dir.is_dir():
            return
        for path in sorted(self.jobs_dir.glob("*.json")):
            try:
                job = Job.load(path)
            except (OSError, ValueError, KeyError, TypeError):
                log.warning("unreadable past job %s", path)
                continue
            self._jobs[job.id] = job

    # -- queries -----------------------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def active(self) -> Job | None:
        with self._lock:
            if self._active is not None:
                return self._active
            return self._queue[0] if self._queue else None

    def building_tiles(self) -> dict[str, str]:
        """The tiles in a build, each with its job's id: those the running job has not finished
        (a user was refused a tile the running build had finished and installed), and every tile
        of the queued jobs."""
        with self._lock:
            out: dict[str, str] = {}
            if self._active is not None:
                for tile in self._active.unfinished_tiles():
                    out.setdefault(tile, self._active.id)
            for job in self._queue:
                for tile in sorted(job._tiles_of_specs):
                    out.setdefault(tile, job.id)
            return out

    def queue_position(self, job_id: str) -> int:
        """1 for the queued job that starts next, 2 for the one after it; 0 for a job not waiting
        (running, finished, unknown)."""
        with self._lock:
            for position, job in enumerate(self._queue, start=1):
                if job.id == job_id:
                    return position
            return 0

    # -- control -----------------------------------------------------------------------------

    def start(
        self,
        specs: Sequence[BuildSpec],
        *,
        install: bool = False,
        request: Mapping[str, Any] | None = None,
        queue: bool = False,
    ) -> Job:
        """Create and start a job; ``JobBusyError`` when one is active (``queue=True`` waits
        behind it), ``TileInBuildError`` when a tile of ``specs`` is in the active or a queued
        job."""
        if not specs:
            raise ValueError("no tile to build")
        with self._lock:
            busy = self._active is not None or bool(self._queue)
            if busy and not queue:
                current = self._active if self._active is not None else self._queue[0]
                raise JobBusyError(current.id)
            taken = self.building_tiles()
            tiles = sorted({spec.tile.name for spec in specs} & taken.keys())
            if tiles:
                raise TileInBuildError(tiles, sorted({taken[tile] for tile in tiles}))
            job = Job(
                specs, install=install, request=request, journal_path=self.jobs_dir / "pending"
            )
            job.journal_path = self.jobs_dir / f"{job.id}.jsonl"
            self._jobs[job.id] = job
            if busy:
                self._queue.append(job)
            else:
                self._launch(job)
            return job

    def _launch(self, job: Job) -> None:
        self._active = job
        thread = threading.Thread(target=self._run, args=(job,), name=f"osxp-job-{job.id}")
        thread.daemon = True
        self._threads[job.id] = thread
        thread.start()

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.finished:
                return False
            if job in self._queue:
                self._queue.remove(job)
                job._finish("cancelled", None, None)
                with contextlib.suppress(OSError):
                    job.save_state()
                job._done.set()
                return True
            return job.cancel()

    def cancel_all(self) -> list[str]:
        """Cancel the queued jobs, then the running one (Quit): in that order, so that no queued job
        starts when the running one ends. The ids cancelled, the running job's last."""
        with self._lock:
            cancelled = [job.id for job in list(self._queue) if self.cancel(job.id)]
            if self._active is not None and self.cancel(self._active.id):
                cancelled.append(self._active.id)
            return cancelled

    def retry(self, job_id: str, *, queue: bool = False) -> Job:
        """A new job with the same specs (the committed artefacts become hits)."""
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        request = {**(job.request or {}), "retry_of": job.id}
        specs = list(job.specs)
        return self.start(specs, install=job.install, request=request, queue=queue)

    def forget_finished(self) -> Sequence[str]:  # a list; `list` in this class is the method
        """Empty the job list (the page's trash on Works): the finished jobs leave it and their
        state and journal files are deleted, so a restart does not bring them back. The active job
        and the queued ones stay; the tiles the jobs built are not touched. Returns the ids gone,
        newest first."""
        with self._lock:
            gone = [
                job
                for job in self._jobs.values()
                if job.finished and job is not self._active and job not in self._queue
            ]
            for job in gone:
                del self._jobs[job.id]
                for path in (job.state_path(), job.journal_path):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        log.warning("cannot delete %s", path)
        return [job.id for job in sorted(gone, key=lambda j: j.created_at, reverse=True)]

    def close(self, timeout: float = 10.0) -> None:
        """Cancel what runs and join the threads (tests, server shutdown)."""
        self.cancel_all()
        for t in list(self._threads.values()):
            t.join(timeout)

    # -- the job thread ----------------------------------------------------------------------

    def _run(self, job: Job) -> None:
        job.begin()
        try:
            job._open_file()
        except OSError:
            log.exception("cannot open the journal of job %s", job.id)
        job._append("log", message=f"job {job.id} started", tile=None, stage=None)
        job.start_ticker()
        env: Any = None
        report: BuildReport | None = None
        status = "failed"
        try:
            env = _call_env_factory(self.env_factory, job.specs)
            job.prepare(env)
            if job.cancel_requested:
                raise CancelRequested(job.id)
            report = self.build(job.specs, on_event=job.on_event, env=env)
            if report.cancelled:
                status = "cancelled"
            elif report.ok:
                status = "done"
        except CancelRequested:
            status = "cancelled"
        except Exception as exc:  # every failure of the build reaches the page
            err = wrap(exc)
            log.warning("job %s failed: %s", job.id, err)
            job.job_error = error_json(err)
            job._append("failed", tile=None, stage=None, node=None, role=None, error=job.job_error)
        finally:
            job.stop_ticker()
            job._finish(status, report, env if isinstance(env, BuildEnv) else None)
            with contextlib.suppress(OSError):
                job.save_state()
            with self._lock:
                if self._active is job:
                    self._active = None
                self._threads.pop(job.id, None)
                if self._queue:
                    self._launch(self._queue.popleft())
            job._done.set()
