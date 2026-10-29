# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The masks on a lane of their own (``pipeline.build.COAST_LANE``): a tile's masks, and so its
images, start once its meshes are done, not once the batch's last mesh is (27 tiles, 2026-10-29)."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from pathlib import Path

from orthostudio.graph import Rule, RuleParams, RunContext, Store
from orthostudio.model import ArtifactRef
from orthostudio.sched import Node, NodeContext, Scheduler


class StepParams(RuleParams):
    tag: str = ""


def _write(ctx: RunContext) -> None:
    ctx.out.write_text(ctx.params.model_dump_json())


MESH = Rule(name="fake.mesh", version=1, fn=_write, params=StepParams, kind="file")
MASKS = Rule(name="fake.masks", version=1, fn=_write, params=StepParams, inputs=("m",), kind="file")

_LOCK = threading.Lock()
SPANS: dict[str, tuple[float, float]] = {}


def _step(seconds: float) -> Callable[[NodeContext], ArtifactRef]:
    def run(ctx: NodeContext) -> ArtifactRef:
        start = time.perf_counter()
        time.sleep(seconds)
        ref = ctx.produce(lambda out: out.write_text(ctx.node_id))
        with _LOCK:
            SPANS[ctx.node_id] = (start, time.perf_counter())
        return ref

    return run


def _run(tmp_path: Path, lane: str | None) -> tuple[float, float]:
    """Eight tiles, each a mesh then its masks, on two subprocess slots (and, with the lane, one
    slot of the masks' own): when the first masks started, and when the last mesh did."""
    SPANS.clear()
    sched = Scheduler(
        Store(tmp_path / "store", fsync=False),
        subprocess_slots=2,
        cpu_in_threads=True,
        lanes={"coast": 1} if lane is not None else {},
    )
    for i in range(8):
        mesh = Node(f"t{i}/mesh", MESH, StepParams(tag=f"m{i}"), {}, kind="subprocess")
        mesh.run = _step(0.15)
        sched.add(mesh)
        masks = Node(
            f"t{i}/masks", MASKS, StepParams(tag=f"k{i}"), {"m": mesh}, kind="subprocess", lane=lane
        )
        masks.run = _step(0.02)
        sched.add(masks)
    refs = asyncio.run(sched.run(list(sched.nodes)))
    assert len(refs) == 16 and not sched.failed
    first_masks = min(s for node, (s, _) in SPANS.items() if node.endswith("/masks"))
    last_mesh = max(s for node, (s, _) in SPANS.items() if node.endswith("/mesh"))
    return first_masks, last_mesh


def test_on_shared_slots_no_masks_start_before_the_last_mesh(tmp_path: Path) -> None:
    """What 1.2.2 did: every slot that came free went to a mesh, whose path is longer, so the
    first masks waited for the last mesh to start."""
    first_masks, last_mesh_start = _run(tmp_path, None)
    assert first_masks >= last_mesh_start


def test_on_their_own_lane_the_masks_start_while_meshes_run(tmp_path: Path) -> None:
    first_masks, last_mesh_start = _run(tmp_path, "coast")
    assert first_masks < last_mesh_start - 0.1
