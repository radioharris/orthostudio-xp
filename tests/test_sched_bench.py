"""Scheduler overhead: 200 trivial nodes, built then hit (spec scheduler.md section 5).

Prints per-node costs; the bound is loose so the test is a smoke check, the numbers are the
point. Run with ``-s`` (and ``nice -n 10``) to read them.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from orthostudio.graph import Rule, RuleParams, RunContext, Store
from orthostudio.model import ArtifactRef
from orthostudio.sched import Event, Node, NodeContext, Scheduler, Stats


class TrivialParams(RuleParams):
    n: int = 0


def _fn(ctx: RunContext) -> None:
    ctx.out.write_bytes(b"t")


TRIVIAL = Rule(name="bench.trivial", version=1, fn=_fn, params=TrivialParams, inputs=("a",))


def run_trivial(ctx: NodeContext) -> ArtifactRef:
    a = ctx.inputs["a"]
    return ctx.produce(lambda out: out.write_bytes((a.digest[:8] if a else "-").encode()))


def _chains(chains: int, length: int) -> list[Node]:
    """``chains`` independent chains of ``length`` nodes (200 nodes for 20 x 10)."""
    roots: list[Node] = []
    for c in range(chains):
        prev: Node | None = None
        for i in range(length):
            prev = Node(
                f"c{c}/n{i}",
                TRIVIAL,
                TrivialParams(n=c * length + i),
                {"a": prev},
                kind="cpu",
                run=run_trivial,
            )
        assert prev is not None
        roots.append(prev)
    return roots


def _run(store: Store, targets: list[Node]) -> tuple[float, list[Event]]:
    sched = Scheduler(store, cpu_workers=4, cpu_in_threads=True, stats_interval_s=10.0)
    for t in targets:
        sched.add(t)
    events: list[Event] = []
    t0 = time.perf_counter()
    refs = asyncio.run(sched.run([t.id for t in targets], on_event=events.append))
    wall = time.perf_counter() - t0
    assert len(refs) == len(targets) and not sched.failed
    return wall, events


def test_bench_200_trivial_nodes(tmp_path: Path) -> None:
    store = Store(tmp_path / "store", fsync=False)
    n = 200
    build_wall, ev1 = _run(store, _chains(20, 10))
    hit_wall, ev2 = _run(store, _chains(20, 10))
    stats1 = [e for e in ev1 if isinstance(e, Stats)][-1]
    stats2 = [e for e in ev2 if isinstance(e, Stats)][-1]
    assert stats1.done == n and stats1.hits == 0
    assert stats2.done == n and stats2.hits == n
    load = getattr(os, "getloadavg", lambda: (0.0, 0.0, 0.0))()
    print(
        f"\nscheduler bench: {n} nodes (20 chains x 10, thread mode, 4 cpu slots)"
        f"\n  build: {build_wall * 1000:.0f} ms total, {build_wall / n * 1000:.2f} ms/node"
        f"\n  hit:   {hit_wall * 1000:.0f} ms total, {hit_wall / n * 1000:.2f} ms/node"
        f"\n  events: {len(ev1)} / {len(ev2)}; load average {load[0]:.2f}"
    )
    # A Windows runner of the CI, its store's files scanned as they are written, took 55 ms per
    # built node (2026-09-15): the bound there catches a regression, not a slow disk.
    slow = 2.0 if os.name == "nt" else 1.0
    assert build_wall / n < 0.05 * slow, "more than 50 ms of overhead per built node"
    assert hit_wall / n < 0.02 * slow, "more than 20 ms per hit"
