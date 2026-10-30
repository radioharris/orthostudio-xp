# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The tracings on a lane of their own (``pipeline.build.TRACING_LANE``): a tile's mesh starts
once its tracing is done, not once the batch's last tracing is (25 tiles, 2026-10-28)."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

from orthostudio.graph import Rule, RuleParams, RunContext, Store
from orthostudio.model import ArtifactRef
from orthostudio.sched import Node, NodeContext, Scheduler


class StepParams(RuleParams):
    tag: str = ""


def _write(ctx: RunContext) -> None:
    ctx.out.write_text(ctx.params.model_dump_json())


TRACE = Rule(name="fake.vectors", version=1, fn=_write, params=StepParams, kind="file")
MESH = Rule(name="fake.mesh", version=1, fn=_write, params=StepParams, inputs=("v",), kind="file")

_LOCK = threading.Lock()
SPANS: dict[str, tuple[float, float]] = {}


def _slow(ctx: NodeContext) -> ArtifactRef:
    start = time.perf_counter()
    time.sleep(0.15)
    ref = ctx.produce(lambda out: out.write_text(ctx.node_id))
    with _LOCK:
        SPANS[ctx.node_id] = (start, time.perf_counter())
    return ref


def _run(tmp_path: Path, lane: str | None) -> tuple[float, float]:
    """Eight tiles, each a tracing then its mesh, on two subprocess slots (and, with the lane, two
    slots of the tracings' own): when the first mesh started, and when the last tracing did."""
    SPANS.clear()
    sched = Scheduler(
        Store(tmp_path / "store", fsync=False),
        subprocess_slots=2,
        cpu_in_threads=True,
        lanes={"tracing": 2} if lane is not None else {},
    )
    for i in range(8):
        params = StepParams(tag=f"v{i}")
        trace = Node(f"t{i}/vectors", TRACE, params, {}, kind="subprocess", lane=lane, run=_slow)
        sched.add(trace)
        mesh = Node(f"t{i}/mesh", MESH, StepParams(tag=f"m{i}"), {"v": trace}, kind="subprocess")
        mesh.run = _slow
        sched.add(mesh)
    refs = asyncio.run(sched.run(list(sched.nodes)))
    assert len(refs) == 16 and not sched.failed
    first_mesh = min(s for node, (s, _) in SPANS.items() if node.endswith("/mesh"))
    last_trace = max(s for node, (s, _) in SPANS.items() if node.endswith("/vectors"))
    return first_mesh, last_trace


def test_on_shared_slots_no_mesh_starts_before_the_last_tracing(tmp_path: Path) -> None:
    """What 1.2.1 did: every slot that came free went to a tracing, whose path is longer, so the
    first mesh waited for the last tracing to start (it may start before that one ends)."""
    first_mesh, last_trace_start = _run(tmp_path, None)
    assert first_mesh >= last_trace_start


def test_on_their_own_lane_the_meshes_start_while_tracings_run(tmp_path: Path) -> None:
    first_mesh, last_trace_start = _run(tmp_path, "tracing")
    assert first_mesh < last_trace_start - 0.1
