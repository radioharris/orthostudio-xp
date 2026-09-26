# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Asynchronous DAG scheduler over the artefact store (spec ``docs/specs/scheduler.md``).

One event loop owns the state; nodes execute in a ``ProcessPoolExecutor`` (``cpu``) or in
threads of a shared ``ThreadPoolExecutor`` (``subprocess``, ``net``, ``io``; ``cpu`` too in
thread mode). Admission is by kind slots, RAM budget and remaining critical path; hits and
same-key deduplication never take a slot. Every event reaches ``on_event`` in the loop thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import logging
import os
import re
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from multiprocessing import get_context
from pathlib import Path
from typing import Any, Literal

import blake3

from orthostudio.errors import OsxpError, wrap
from orthostudio.graph.errors import InvalidNodeError
from orthostudio.graph.keys import key_for
from orthostudio.graph.store import InputRef, Store
from orthostudio.model import ArtifactRef
from orthostudio.sched.costs import CostModel
from orthostudio.sched.events import Done, Event, Failed, NodeKind, Progress, Started, Stats
from orthostudio.sched.node import Node, artifact_ref
from orthostudio.sched.workers import CpuJob, Outcome, cpu_entry, cpu_init, error_from_dict, execute

__all__ = ["PlanEntry", "Scheduler", "default_cpu_workers"]

log = logging.getLogger("orthostudio.sched")

_WORKDIR_SAFE = re.compile(r"[^A-Za-z0-9+._-]+")
_State = Literal["pending", "ready", "waiting", "running", "done", "failed"]


def _lane_pool(name: str) -> str:
    """The running counter of a lane, apart from the kinds' counters."""
    return f"lane:{name}"


def default_cpu_workers() -> int:
    """``os.cpu_count() - 2``, at least 1 (PLAN section 3: cores - 2 CPU tasks)."""
    return max(1, (os.cpu_count() or 2) - 2)


@dataclass(frozen=True, slots=True)
class PlanEntry:
    node_id: str
    key: str | None
    status: Literal["hit", "build", "unknown"]
    seconds: float
    """Estimated cost when the node must (or may) be built; 0 for a hit."""


@dataclass(slots=True)
class _Rec:
    node: Node
    order: int
    state: _State = "pending"
    deps_left: int = 0
    dependants: list[str] = field(default_factory=list)
    priority: float = 0.0
    key: str | None = None
    recipe: str | None = None
    started_at: float = 0.0
    last_fraction: float = 0.0
    ref: ArtifactRef | None = None
    workdir: Path | None = None
    resolved: dict[str, ArtifactRef | None] | None = None
    idle: bool = False
    """The run gave its slot back while it waits (``NodeContext.idle``): another node of the
    same kind may start, and this one is not counted until it resumes."""

    @property
    def terminal(self) -> bool:
        return self.state in ("done", "failed")


class Scheduler:
    """Runs a graph of :class:`Node` against a :class:`Store` (spec section 2)."""

    def __init__(
        self,
        store: Store,
        *,
        cpu_workers: int | None = None,
        subprocess_slots: int = 1,
        net_slots: int = 2,
        io_slots: int = 4,
        ram_budget_mb: int | None = None,
        fail_fast: bool = False,
        workdir: Path | None = None,
        stats_interval_s: float = 1.0,
        cancel_grace_s: float = 5.0,
        cpu_in_threads: bool = False,
        costs: CostModel | None = None,
        lanes: Mapping[str, int] | None = None,
    ) -> None:
        self.store = store
        self.cpu_workers = default_cpu_workers() if cpu_workers is None else max(1, cpu_workers)
        self.slots: dict[NodeKind, int] = {
            "cpu": self.cpu_workers,
            "subprocess": max(1, subprocess_slots),
            "net": max(1, net_slots),
            "io": max(1, io_slots),
        }
        self.lanes: dict[str, int] = {name: max(1, n) for name, n in (lanes or {}).items()}
        """Limits of their own for the nodes that name them (``Node.lane``), beside the slots."""
        self.ram_budget_mb = ram_budget_mb
        self.fail_fast = fail_fast
        self.workdir = Path(workdir) if workdir is not None else store.root / ".work"
        self.stats_interval_s = stats_interval_s
        self.cancel_grace_s = cancel_grace_s
        self.cpu_in_threads = cpu_in_threads
        self.costs = costs if costs is not None else CostModel(store)
        self.failed: dict[str, OsxpError] = {}
        self._nodes: dict[str, Node] = {}
        self._cancel = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_event: Callable[[Event], None] | None = None
        self._recs: dict[str, _Rec] = {}
        self._wake: asyncio.Event | None = None
        self._finished: deque[tuple[str, Future[Outcome]]] = deque()
        self._mp_cancel: Any = None
        self._mp_queue: Any = None
        self._procs: ProcessPoolExecutor | None = None
        self._threads: ThreadPoolExecutor | None = None
        self._drainer: threading.Thread | None = None

    # -- graph -------------------------------------------------------------------------------

    def add(self, node: Node) -> Node:
        """Register ``node`` and every node reachable through its inputs; returns ``node``."""
        stack = [node]
        while stack:
            n = stack.pop()
            known = self._nodes.get(n.id)
            if known is n:
                continue
            if known is not None:
                raise InvalidNodeError(f"node id {n.id!r} already registered with another node")
            if n.lane is not None and n.lane not in self.lanes:
                raise InvalidNodeError(f"node {n.id!r}: lane {n.lane!r} has no limit here")
            self._nodes[n.id] = n
            stack.extend(n.node_inputs())
        return node

    @property
    def nodes(self) -> Mapping[str, Node]:
        return self._nodes

    def _closure(self, targets: Iterable[str]) -> list[str]:
        """Ids reachable from ``targets`` in topological order (inputs first); detects cycles."""
        order: list[str] = []
        colour: dict[str, int] = {}

        def visit(n: Node, trail: list[str]) -> None:
            c = colour.get(n.id, 0)
            if c == 2:
                return
            if c == 1:
                cycle = [*trail[trail.index(n.id) :], n.id]
                raise InvalidNodeError("cycle in the graph: " + " -> ".join(cycle))
            colour[n.id] = 1
            trail.append(n.id)
            for dep in n.node_inputs():
                if self._nodes.get(dep.id) is not dep:
                    raise InvalidNodeError(f"node {dep.id!r} (input of {n.id!r}) is not registered")
                visit(dep, trail)
            trail.pop()
            colour[n.id] = 2
            order.append(n.id)

        for t in targets:
            if t not in self._nodes:
                raise InvalidNodeError(f"unknown target {t!r}")
            visit(self._nodes[t], [])
        return order

    def plan(self, targets: list[str]) -> list[PlanEntry]:
        """Without running: key and hit / build / unknown of every node the targets need."""
        entries: list[PlanEntry] = []
        digests: dict[str, str | None] = {}
        for nid in self._closure(targets):
            node = self._nodes[nid]
            inputs: dict[str, str | None] = {}
            unknown = False
            for name, dep in node.inputs.items():
                if dep is None:
                    inputs[name] = None
                elif isinstance(dep, ArtifactRef):
                    inputs[name] = dep.digest
                else:
                    d = digests[dep.id]
                    unknown = unknown or d is None
                    inputs[name] = d
            cost = self.costs.estimate(node.rule)
            if unknown:
                entries.append(PlanEntry(nid, None, "unknown", cost))
                digests[nid] = None
                continue
            key, _ = key_for(node.rule, node.params, inputs)
            digest = self.store.digest_of(key)
            digests[nid] = digest
            entries.append(
                PlanEntry(nid, key, "hit" if digest else "build", 0.0 if digest else cost)
            )
        return entries

    # -- control -----------------------------------------------------------------------------

    def cancel(self) -> None:
        """Stop admitting nodes and ask the running ones to stop (thread-safe, idempotent)."""
        self._cancel.set()
        if self._mp_cancel is not None:
            with contextlib.suppress(Exception):
                self._mp_cancel.set()
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(wake.set)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # -- run ---------------------------------------------------------------------------------

    async def run(
        self, targets: list[str], on_event: Callable[[Event], None] | None = None
    ) -> dict[str, ArtifactRef]:
        """Build the targets; returns the refs of those that succeeded (see ``failed``)."""
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._on_event = on_event
        self._cancel.clear()
        self.failed = {}
        self._finished.clear()
        self._wake = asyncio.Event()
        self._t0 = time.perf_counter()
        self._busy_s = 0.0
        self._counts: dict[str, int] = {"done": 0, "hits": 0, "failed": 0}
        self._running: dict[str, int] = {k: 0 for k in self.slots}
        self._running.update({_lane_pool(name): 0 for name in self.lanes})
        self._ram_in_flight = 0
        self._inflight_keys: dict[str, str] = {}
        self._waiters: dict[str, list[str]] = {}
        self._ready: list[tuple[float, int, str]] = []
        self._recs = self._prepare(targets)
        self._start_pools()
        try:
            await self._loop_until_done()
        except asyncio.CancelledError:
            # The task itself was cancelled (a second Ctrl-C): abandon the run at once, no
            # second grace period for the nodes that did not answer the first cancel.
            self.cancel()
            await self._settle_cancel(grace_s=0.0)
            raise
        except BaseException:
            # KeyboardInterrupt (no signal handler on the loop: Windows, handle_sigint=False),
            # an on_event callback raising a BaseException, SystemExit: the running nodes must
            # still be asked to stop, else _stop_pools would wait for every subprocess to end.
            self.cancel()
            await self._settle_cancel()
            raise
        finally:
            self.costs.flush()
            self._stop_pools()
            self._loop = None
            self._wake = None
        return {t: r.ref for t in targets if (r := self._recs[t]).ref is not None}

    # -- preparation ---------------------------------------------------------------------------

    def _prepare(self, targets: list[str]) -> dict[str, _Rec]:
        order = self._closure(targets)
        recs = {nid: _Rec(self._nodes[nid], i) for i, nid in enumerate(order)}
        for nid, rec in recs.items():
            deps = {d.id for d in rec.node.node_inputs()}
            rec.deps_left = len(deps)
            for d in deps:
                recs[d].dependants.append(nid)
        for nid in reversed(order):  # dependants are later in topological order
            rec = recs[nid]
            downstream = max((recs[d].priority for d in rec.dependants), default=0.0)
            rec.priority = self.costs.estimate(rec.node.rule) + downstream
        for rec in recs.values():
            if rec.deps_left == 0:
                self._mark_ready(rec)
        return recs

    def _mark_ready(self, rec: _Rec) -> None:
        rec.state = "ready"
        heapq.heappush(self._ready, (-rec.priority, rec.order, rec.node.id))

    def _start_pools(self) -> None:
        cpu_in_procs = not self.cpu_in_threads and any(
            r.node.kind == "cpu" for r in self._recs.values()
        )
        n_threads = self.slots["subprocess"] + self.slots["net"] + self.slots["io"]
        n_threads += sum(self.lanes.values())
        if not cpu_in_procs:
            n_threads += self.slots["cpu"]
        self._threads = ThreadPoolExecutor(max_workers=n_threads, thread_name_prefix="osxp-sched")
        if cpu_in_procs:
            ctx = get_context("spawn")
            self._mp_cancel = ctx.Event()
            self._mp_queue = ctx.Queue()
            self._procs = ProcessPoolExecutor(
                max_workers=self.slots["cpu"],
                mp_context=ctx,
                initializer=cpu_init,
                initargs=(self._mp_cancel, self._mp_queue),
            )
            self._drainer = threading.Thread(
                target=self._drain_progress, name="osxp-sched-progress", daemon=True
            )
            self._drainer.start()

    def _stop_pools(self) -> None:
        cancelled = self._cancel.is_set()
        if self._threads is not None:
            self._threads.shutdown(wait=not cancelled, cancel_futures=True)
            self._threads = None
        if self._procs is not None:
            if cancelled:
                _terminate_workers(self._procs)
            self._procs.shutdown(wait=not cancelled, cancel_futures=True)
            self._procs = None
        if self._mp_queue is not None:
            with contextlib.suppress(Exception):
                self._mp_queue.put(None)
            if self._drainer is not None:
                self._drainer.join(timeout=2.0)
            with contextlib.suppress(Exception):
                self._mp_queue.close()
        self._drainer = None
        self._mp_queue = None
        self._mp_cancel = None

    def _drain_progress(self) -> None:
        queue, loop = self._mp_queue, self._loop
        assert queue is not None and loop is not None
        while True:
            try:
                item = queue.get()
            except (EOFError, OSError):
                return
            if item is None:
                return
            node_id, fraction, message = item
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._progress, node_id, fraction, message)

    # -- main loop -----------------------------------------------------------------------------

    async def _loop_until_done(self) -> None:
        assert self._wake is not None
        while True:
            self._admit()
            if all(r.terminal for r in self._recs.values()):
                break
            if self._cancel.is_set():
                await self._settle_cancel()
                break
            if not any(r.state == "running" for r in self._recs.values()):
                # nothing runs and nothing can be admitted: the graph is stuck
                for rec in self._recs.values():
                    if not rec.terminal:
                        self._fail(
                            rec,
                            OsxpError(
                                "SYS_INTERNAL_ERROR",
                                context={"type": "SchedulerStall", "detail": rec.node.id},
                            ),
                        )
                break
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.stats_interval_s)
            except TimeoutError:
                self._emit(self._stats())
            self._wake.clear()
            self._collect()

    def _collect(self) -> None:
        while self._finished:
            nid, fut = self._finished.popleft()
            rec = self._recs[nid]
            try:
                outcome = fut.result()
            except BaseException as exc:  # pool broken, pickling failure, cancelled future
                outcome = Outcome(None, _wrap_dict(exc), 0.0)
            self._finish_running(rec, outcome)

    def _pool(self, node: Node) -> str:
        """The counter a running node takes: its lane's, else its kind's."""
        return _lane_pool(node.lane) if node.lane is not None else node.kind

    def _limit(self, node: Node) -> int:
        return self.lanes[node.lane] if node.lane is not None else self.slots[node.kind]

    def _set_idle(self, node_id: str, idle: bool) -> None:
        """A running node gives its slot back while it waits, or takes it again (loop thread).

        Taking it back never waits: a node that has already done its work must not queue behind
        the one that started meanwhile. At most one extra node of that kind runs until the waiting
        one is done, which is what a spaced retry round costs (``pipeline/textures.py``).
        """
        rec = self._recs.get(node_id)
        if rec is None or rec.state != "running" or rec.idle == idle:
            return
        rec.idle = idle
        self._running[self._pool(rec.node)] += -1 if idle else 1
        if idle and self._wake is not None:
            self._wake.set()  # a slot came free: look for something to start

    def _finish_running(self, rec: _Rec, outcome: Outcome) -> None:
        if rec.idle:
            rec.idle = False  # the slot was already given back while it waited
        else:
            self._running[self._pool(rec.node)] -= 1
        self._ram_in_flight -= rec.node.ram_mb or 0
        self._busy_s += outcome.wall_s
        assert rec.key is not None
        self._inflight_keys.pop(rec.key, None)
        waiters = self._waiters.pop(rec.key, [])
        if outcome.error is not None:
            error = error_from_dict(outcome.error)
            if outcome.traceback:
                error.context["traceback"] = outcome.traceback
                log.debug("node %s failed:\n%s", rec.node.id, outcome.traceback)
            self._fail(rec, error)
            for w in waiters:
                self._fail_downstream(self._recs[w], rec, error)
            return
        ref = outcome.ref
        assert ref is not None
        if ref.key != rec.key:
            error = OsxpError(
                "SYS_INTERNAL_ERROR",
                context={
                    "type": "KeyMismatch",
                    "detail": f"node {rec.node.id!r} committed {ref.key[:16]}, "
                    f"expected {rec.key[:16]}",
                },
            )
            self._fail(rec, error)
            for w in waiters:  # in-flight twins of the same key: skipped with this cause
                self._fail_downstream(self._recs[w], rec, error)
            return
        self.costs.observe(rec.node.rule, outcome.wall_s)
        if rec.workdir is not None:
            shutil.rmtree(rec.workdir, ignore_errors=True)
        self._complete(rec, ref, hit=False, wall_s=outcome.wall_s)
        for w in waiters:
            self._complete(self._recs[w], ref, hit=True, wall_s=0.0)

    # -- admission ---------------------------------------------------------------------------

    def _admit(self) -> None:
        """Dispatch, hit or park every ready node, best priority first, until nothing moves."""
        if self._cancel.is_set():
            return
        while True:
            parked: list[tuple[float, int, str]] = []
            moved = False
            while self._ready:
                item = heapq.heappop(self._ready)
                rec = self._recs[item[2]]
                if rec.state != "ready":
                    continue
                if self._try_start(rec):
                    moved = True
                else:
                    parked.append(item)
            for item in parked:
                heapq.heappush(self._ready, item)
            if not moved or not self._ready:
                return

    def _try_start(self, rec: _Rec) -> bool:
        """Hit, join an in-flight twin, or dispatch when a slot and the RAM allow it."""
        if rec.key is None:
            inputs: dict[str, ArtifactRef | None] = {}
            for name, dep in rec.node.inputs.items():
                if dep is None:
                    inputs[name] = None
                elif isinstance(dep, ArtifactRef):
                    inputs[name] = dep
                else:
                    dref = self._recs[dep.id].ref
                    assert dref is not None
                    inputs[name] = dref
            rec.key, rec.recipe = key_for(
                rec.node.rule,
                rec.node.params,
                {n: (r.digest if r is not None else None) for n, r in inputs.items()},
            )
            rec.resolved = inputs
        key = rec.key
        assert rec.resolved is not None
        if self.store.has(key):
            refs = _input_refs(self.store, rec.resolved)
            self.store.touch(key, refs)
            info = self.store.info(key)
            assert info is not None
            self._emit(Started(rec.node.id, rec.node.kind, key))
            self._complete(rec, artifact_ref(info), hit=True, wall_s=0.0)
            return True
        if key in self._inflight_keys:
            rec.state = "waiting"
            self._waiters.setdefault(key, []).append(rec.node.id)
            self._emit(Started(rec.node.id, rec.node.kind, key))
            return True
        if self._running[self._pool(rec.node)] >= self._limit(rec.node):
            return False
        ram = rec.node.ram_mb or 0
        if (
            self.ram_budget_mb is not None
            and self._ram_in_flight + ram > self.ram_budget_mb
            and self._ram_in_flight > 0
        ):
            return False
        self._dispatch(rec)
        return True

    def _dispatch(self, rec: _Rec) -> None:
        assert self._loop is not None and self._threads is not None
        node = rec.node
        key, recipe = rec.key, rec.recipe
        assert key is not None and recipe is not None
        assert rec.resolved is not None
        inputs = rec.resolved
        rec.state = "running"
        rec.started_at = time.perf_counter()
        rec.last_fraction = 0.0
        self._running[self._pool(node)] += 1
        self._ram_in_flight += node.ram_mb or 0
        self._inflight_keys[key] = node.id
        rec.workdir = self.workdir / _workdir_name(node.id)
        shutil.rmtree(rec.workdir, ignore_errors=True)
        self._emit(Started(node.id, node.kind, key))
        fut: Future[Outcome]
        if node.kind == "cpu" and self._procs is not None:
            job = CpuJob(
                node_id=node.id,
                rule=node.rule,
                params=node.params,
                key=key,
                recipe=recipe,
                inputs=inputs,
                store_root=self.store.root,
                fsync=self.store.fsync,
                workdir=rec.workdir,
                run=node.run,
            )
            fut = self._procs.submit(cpu_entry, job)
        else:
            loop = self._loop
            node_id = node.id

            def progress(fraction: float, message: str) -> None:
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(self._progress, node_id, fraction, message)

            def set_idle(idle: bool) -> None:
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(self._set_idle, node_id, idle)

            fut = self._threads.submit(
                execute,
                node_id=node.id,
                rule=node.rule,
                params=node.params,
                key=key,
                recipe=recipe,
                inputs=inputs,
                store=self.store,
                workdir=rec.workdir,
                progress=progress,
                cancel_event=self._cancel,
                run=node.run,
                set_idle=set_idle,
            )
        node_id = node.id
        fut.add_done_callback(lambda f: self._task_done(node_id, f))

    def _task_done(self, node_id: str, fut: Future[Outcome]) -> None:
        """Runs in a pool thread: hand the future to the loop."""
        loop, wake = self._loop, self._wake
        if loop is None or wake is None:
            return

        def post() -> None:
            self._finished.append((node_id, fut))
            wake.set()

        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(post)

    # -- completion, failure, cancellation ---------------------------------------------------

    def _complete(self, rec: _Rec, ref: ArtifactRef, *, hit: bool, wall_s: float) -> None:
        rec.state = "done"
        rec.ref = ref
        rec.last_fraction = 1.0
        self._counts["done"] += 1
        if hit:
            self._counts["hits"] += 1
        assert rec.key is not None
        self._emit(Done(rec.node.id, rec.key, hit, wall_s, ref))
        for d in rec.dependants:
            drec = self._recs[d]
            drec.deps_left -= 1
            if drec.deps_left == 0 and drec.state == "pending":
                self._mark_ready(drec)
        self._emit(self._stats())

    def _fail(self, rec: _Rec, error: OsxpError, cause: str | None = None) -> None:
        if rec.terminal:
            return
        rec.state = "failed"
        self._counts["failed"] += 1
        self.failed[rec.node.id] = error
        self._emit(Failed(rec.node.id, error, cause))
        root = cause if cause is not None else rec.node.id
        for d in rec.dependants:
            self._fail_downstream(self._recs[d], rec, error, root)
        self._emit(self._stats())
        if self.fail_fast and cause is None and not self._cancel.is_set():
            self.cancel()

    def _fail_downstream(
        self, rec: _Rec, upstream: _Rec, error: OsxpError, root: str | None = None
    ) -> None:
        if rec.terminal:
            return
        root = root if root is not None else upstream.node.id
        skipped = OsxpError(
            "SYS_UPSTREAM_FAILED",
            context={"upstream": upstream.node.id, "root": root, "code": error.code},
            message=f"Node {rec.node.id} skipped: {root} failed with {error.code}.",
            remedy="Fix the upstream failure and relaunch; finished nodes are reused.",
        )
        self._fail(rec, skipped, cause=root)

    def _progress(self, node_id: str, fraction: float, message: str) -> None:
        rec = self._recs.get(node_id)
        if rec is None or rec.state != "running":
            return  # late progress after completion is dropped (causal order per node)
        rec.last_fraction = min(1.0, max(0.0, float(fraction)))
        self._emit(Progress(node_id, rec.last_fraction, message), from_callback=True)

    async def _settle_cancel(self, grace_s: float | None = None) -> None:
        """After ``cancel()``: wait for running nodes (grace), then fail whatever is left.

        ``grace_s`` overrides ``cancel_grace_s`` (0 abandons the run without waiting).
        """
        assert self._wake is not None
        grace = self.cancel_grace_s if grace_s is None else grace_s
        deadline = time.perf_counter() + grace
        while any(r.state == "running" for r in self._recs.values()):
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=remaining)
            self._wake.clear()
            self._collect()
        cancelled = OsxpError("SYS_CANCELLED")
        for rec in list(self._recs.values()):
            if not rec.terminal:
                if rec.state == "running":
                    self._running[self._pool(rec.node)] -= 1
                    self._ram_in_flight -= rec.node.ram_mb or 0
                    assert rec.key is not None
                    self._inflight_keys.pop(rec.key, None)
                self._fail(rec, cancelled)

    # -- events and statistics ---------------------------------------------------------------

    def _emit(self, event: Event, *, from_callback: bool = False) -> None:
        """Hand one event to the listener; ``from_callback`` when nothing above can catch.

        A listener stops a run by raising (the API's ``CancelRequested``), and that only works
        where the caller can see it: from a callback of the loop, asyncio would print the whole
        traceback in the log instead, where it reads like a crash. A user who cancelled a build
        saw two of them (2026-09-19). The run is being stopped anyway, and the next event from the
        coroutine carries the refusal.
        """
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception:
            log.exception("on_event raised for %r", event)
        except BaseException:
            if not from_callback:
                raise
            log.debug("on_event asked to stop the run, from a callback (%r)", event)

    def _stats(self) -> Stats:
        now = time.perf_counter()
        elapsed = now - self._t0
        running = pending = 0
        remaining = 0.0
        busy = self._busy_s
        for rec in self._recs.values():
            if rec.state == "running":
                running += 1
                cost = self.costs.estimate(rec.node.rule)
                remaining += cost * (1.0 - rec.last_fraction)
                busy += now - rec.started_at
            elif rec.state in ("pending", "ready", "waiting"):
                pending += 1
                remaining += self.costs.estimate(rec.node.rule)
        parallelism = 1.0
        if elapsed > 0:
            parallelism = max(1.0, min(busy / elapsed, float(sum(self.slots.values()))))
        return Stats(
            running=running,
            pending=pending,
            done=self._counts["done"],
            failed=self._counts["failed"],
            hits=self._counts["hits"],
            elapsed_s=elapsed,
            eta_s=remaining / parallelism,
        )


# -- helpers -----------------------------------------------------------------------------------


def _input_refs(store: Store, inputs: Mapping[str, ArtifactRef | None]) -> list[InputRef]:
    refs: list[InputRef] = []
    for name in sorted(inputs):
        ref = inputs[name]
        if ref is None:
            refs.append(InputRef(name, None, None))
        else:
            refs.append(InputRef(name, ref.digest, ref.key if store.has(ref.key) else None))
    return refs


def _wrap_dict(exc: BaseException) -> dict[str, Any]:
    return wrap(exc if isinstance(exc, Exception) else RuntimeError(repr(exc))).to_dict()


def _workdir_name(node_id: str) -> str:
    safe = _WORKDIR_SAFE.sub("_", node_id)[:60]
    return f"{safe}-{blake3.blake3(node_id.encode()).hexdigest()[:8]}"


def _terminate_workers(pool: ProcessPoolExecutor) -> None:
    terminate = getattr(pool, "terminate_workers", None)
    if callable(terminate):  # Python 3.14+
        with contextlib.suppress(Exception):
            terminate()
        return
    procs = getattr(pool, "_processes", None) or {}
    for p in list(procs.values()):
        with contextlib.suppress(Exception):
            p.terminate()
