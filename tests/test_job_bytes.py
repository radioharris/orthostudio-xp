# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What a build step really wrote on the disk and received from the network, counted as it runs,
not worked out before: Works' final report shows both per step (a user, 2026-10-16)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_link_or_copy
from orthostudio.graph import Rule, RuleParams, RunContext, Store
from orthostudio.imagery.chunks import ChunkContainer, ChunkEntry, ChunkStatus, ChunkStore
from orthostudio.imagery.grid import TextureId
from orthostudio.meter import counting, received, written
from orthostudio.model import ArtifactRef
from orthostudio.sched import Done, Event, Failed, Node, NodeContext, Scheduler
from orthostudio.sched.node import own_bytes


class Params(RuleParams):
    size: int = 5000
    fetch: int = 1234
    fail: bool = False


def _unused(ctx: RunContext) -> None:  # the nodes below bring their own run
    raise AssertionError


RULE = Rule(name="fake.bytes", version=1, fn=_unused, params=Params, inputs=(), ram_mb=0)


def run_bytes(ctx: NodeContext) -> ArtifactRef:
    """Receive ``fetch`` bytes, then commit a file of ``size`` bytes (module level: picklable)."""
    params = ctx.params
    assert isinstance(params, Params)
    ctx.fetched(params.fetch)
    if params.fail:
        raise OsxpError("NET_TIMEOUT", context={"url": "x", "seconds": 1})
    return ctx.produce(lambda out: out.write_bytes(b"x" * params.size))


def _run(store: Store, node: Node, *, threads: bool) -> list[Event]:
    sched = Scheduler(store, cpu_in_threads=threads, cpu_workers=1)
    events: list[Event] = []
    sched.add(node)
    asyncio.run(sched.run([node.id], on_event=events.append))
    return events


@pytest.mark.parametrize("threads", [True, False])
def test_a_step_counts_what_it_committed_and_received_and_a_hit_counts_nothing(
    tmp_path: Path, threads: bool
) -> None:
    store = Store(tmp_path / "store", fsync=False)
    node = Node("t/bytes", RULE, Params(), {}, kind="cpu", run=run_bytes)
    done = [e for e in _run(store, node, threads=threads) if isinstance(e, Done)]
    assert [(d.hit, d.written, d.downloaded) for d in done] == [(False, 5000, 1234)]
    again = Node("t/bytes", RULE, Params(), {}, kind="cpu", run=run_bytes)
    done = [e for e in _run(store, again, threads=threads) if isinstance(e, Done)]
    assert [(d.hit, d.written, d.downloaded) for d in done] == [(True, 0, 0)]


def test_a_failed_step_keeps_what_it_had_received(tmp_path: Path) -> None:
    store = Store(tmp_path / "store", fsync=False)
    node = Node("t/bytes", RULE, Params(fetch=777, fail=True), {}, kind="net", run=run_bytes)
    failed = [e for e in _run(store, node, threads=True) if isinstance(e, Failed)]
    assert [(f.written, f.downloaded) for f in failed] == [(0, 777)]


def test_a_link_to_what_the_store_holds_takes_no_room(tmp_path: Path) -> None:
    folder = tmp_path / "pack"
    folder.mkdir()
    (folder / "own.ter").write_bytes(b"t" * 100)
    image = tmp_path / "image.dds"
    image.write_bytes(b"d" * 4000)
    os.link(image, folder / "image.dds")
    assert own_bytes(folder) == 100
    assert own_bytes(folder / "own.ter") == 100
    assert own_bytes(folder / "image.dds") == 0


def test_a_copy_counts_and_a_link_does_not(tmp_path: Path) -> None:
    src = tmp_path / "a.dsf"
    src.write_bytes(b"s" * 300)
    seen: list[int] = []
    with counting(wrote=seen.append):
        atomic_link_or_copy(src, tmp_path / "linked.dsf", link=True)
        atomic_link_or_copy(src, tmp_path / "copied.dsf", link=False)
    assert seen == [300]


def test_the_counters_count_only_where_a_step_bound_them() -> None:
    got: list[tuple[str, int]] = []
    received(10)  # a probe, a test: nobody counts
    written(10)
    with counting(lambda n: got.append(("in", n)), lambda n: got.append(("out", n))):
        received(5)
        written(7)
        received(0)  # nothing came: nothing said

        async def overpass() -> None:  # Overpass' coroutines run in the step's own thread
            received(3)

        asyncio.run(overpass())
    received(9)
    assert got == [("in", 5), ("out", 7), ("in", 3)]


def test_the_cache_of_image_pieces_counts_what_its_containers_grew_by(tmp_path: Path) -> None:
    grew: list[int] = []
    chunks = ChunkStore(tmp_path / "chunks", fsync=False, on_write=grew.append)
    t = TextureId(24000, 16000, 16, "BI")
    container = ChunkContainer()
    chunks.write(t, container)
    container.set(0, ChunkEntry(ChunkStatus.OK, b"j" * 2000, "image/jpeg", 1))
    chunks.write(t, container)
    chunks.write(t, container)  # the same again: nothing more on the disk
    assert sum(grew) == chunks.path(t).stat().st_size
    assert len(grew) == 2


def test_the_report_sums_what_every_pass_of_a_step_did(tmp_path: Path) -> None:
    """A second pass, or a retry, adds what it did to what the first did; a hit adds nothing."""
    from orthostudio.pipeline.build import _Collector

    ref = ArtifactRef("a" * 64, "b" * 64, tmp_path, RULE.name, "file", 1)
    node = Node("t/bytes", RULE, Params(), {}, kind="net", run=run_bytes)
    collector = _Collector(None)
    collector(Failed("t/bytes", OsxpError("NET_TIMEOUT", context={"url": "x", "seconds": 1}),
                     None, 0, 400))  # fmt: skip
    collector(Done("t/bytes", "a" * 64, False, 1.0, ref, 5000, 1234))
    collector(Done("t/bytes", "a" * 64, True, 0.0, ref))
    outcome = collector.outcome("bytes", node)
    assert (outcome.written, outcome.downloaded) == (5000, 1634)
