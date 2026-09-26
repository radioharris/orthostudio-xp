# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Scheduler acceptance tests (spec docs/specs/scheduler.md section 3): no network, no Ortho4XP.

Fake rules sleep in 10 ms slices while polling the cancel token, write a few bytes and log
their start / end instants. Everything runs in thread mode except the process-pool tests.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from orthostudio.errors import OsxpError
from orthostudio.graph import InvalidNodeError, Rule, RuleParams, RunContext, Store
from orthostudio.model import ArtifactRef
from orthostudio.sched import (
    CostModel,
    Done,
    Event,
    Failed,
    Node,
    NodeContext,
    Progress,
    Scheduler,
    Started,
    Stats,
)

# --- fake rules (module level: picklable for the process pool) ----------------------------------


class SleepParams(RuleParams):
    seconds: float = 0.05
    tag: str = ""
    payload: str = "x"
    fail: bool = False


@dataclass(slots=True)
class Span:
    node_id: str
    kind: str
    rule: str
    start: float
    end: float


_LOG_LOCK = threading.Lock()
LOG: list[Span] = []


def _noop(ctx: RunContext) -> None:
    ctx.out.write_bytes(b"p0:" + str(ctx.params.model_dump()).encode())


SRC = Rule(name="fake.src", version=1, fn=_noop, params=SleepParams, inputs=(), ram_mb=0)
MID = Rule(name="fake.mid", version=1, fn=_noop, params=SleepParams, inputs=("a",), ram_mb=0)
JOIN = Rule(name="fake.join", version=1, fn=_noop, params=SleepParams, inputs=("a", "b"), ram_mb=0)


def _poll_sleep(ctx: NodeContext, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        ctx.check_cancelled()
        time.sleep(0.01)


def run_sleep(ctx: NodeContext) -> ArtifactRef:
    params = ctx.params
    assert isinstance(params, SleepParams)
    t0 = time.perf_counter()
    ctx.progress(0.0, "start")
    _poll_sleep(ctx, params.seconds / 2)
    ctx.progress(0.5, "half")
    if params.fail:
        raise OsxpError("MESH_TRIANGULATION_FAILED", context={"tile": ctx.node_id, "reason": "x"})
    _poll_sleep(ctx, params.seconds / 2)
    digests = "|".join(r.digest[:8] if r else "-" for _, r in sorted(ctx.inputs.items()))
    content = f"{params.payload}|{digests}|pid={os.getpid()}"

    def writer(out: Path) -> None:
        out.write_bytes(content.encode())

    ref = ctx.produce(writer)
    with _LOG_LOCK:
        LOG.append(Span(ctx.node_id, "?", ctx.rule.name, t0, time.perf_counter()))
    return ref


def run_stable(ctx: NodeContext) -> ArtifactRef:
    """Like run_sleep but the bytes do not depend on the pid (early cutoff / dedup tests)."""
    params = ctx.params
    assert isinstance(params, SleepParams)
    t0 = time.perf_counter()
    _poll_sleep(ctx, params.seconds)
    digests = "|".join(r.digest[:8] if r else "-" for _, r in sorted(ctx.inputs.items()))
    ref = ctx.produce(lambda out: out.write_bytes(f"{params.payload}|{digests}".encode()))
    with _LOG_LOCK:
        LOG.append(Span(ctx.node_id, "?", ctx.rule.name, t0, time.perf_counter()))
    return ref


def run_crash(ctx: NodeContext) -> ArtifactRef:
    """Simulates a process dying mid-build: a tmp dir with partial output is left behind."""
    build = ctx.begin()
    build.out.write_bytes(b"partial")
    raise RuntimeError("simulated crash")


def run_wrong_key(ctx: NodeContext) -> ArtifactRef:
    ref = ctx.produce(lambda out: out.write_bytes(b"z"))
    return ArtifactRef("0" * 64, ref.digest, ref.path, ref.rule, ref.kind, ref.size)


# --- helpers ------------------------------------------------------------------------------------


def _node(
    node_id: str,
    rule: Rule,
    *,
    kind: str = "cpu",
    ram_mb: int | None = None,
    run: Callable[[NodeContext], ArtifactRef] | None = run_sleep,
    lane: str | None = None,
    **kw: Any,
) -> Node:
    """``kw`` mixes SleepParams fields and the rule's inputs (nodes, refs or None)."""
    params = SleepParams(**{k: v for k, v in kw.items() if k in SleepParams.model_fields})
    deps = {k: v for k, v in kw.items() if k in rule.inputs}
    return Node(node_id, rule, params, deps, kind=kind, ram_mb=ram_mb, run=run, lane=lane)  # type: ignore[arg-type]


def _run(sched: Scheduler, targets: list[str]) -> tuple[dict[str, ArtifactRef], list[Event]]:
    events: list[Event] = []
    refs = asyncio.run(sched.run(targets, on_event=events.append))
    return refs, events


def _spans(rule: str | None = None, ids: set[str] | None = None) -> list[Span]:
    with _LOG_LOCK:
        return [
            s for s in LOG if (rule is None or s.rule == rule) and (ids is None or s.node_id in ids)
        ]


def _max_concurrent(spans: list[Span]) -> int:
    points = sorted([(s.start, 1) for s in spans] + [(s.end, -1) for s in spans])
    cur = best = 0
    for _, d in points:
        cur += d
        best = max(best, cur)
    return best


def _done(events: list[Event]) -> dict[str, Done]:
    return {e.node_id: e for e in events if isinstance(e, Done)}


def _failed(events: list[Event]) -> dict[str, Failed]:
    return {e.node_id: e for e in events if isinstance(e, Failed)}


@pytest.fixture(autouse=True)
def _clear_log() -> None:
    with _LOG_LOCK:
        LOG.clear()


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "store", fsync=False)


def _sched(store: Store, **kw: object) -> Scheduler:
    kw.setdefault("cpu_in_threads", True)
    kw.setdefault("cpu_workers", 4)
    return Scheduler(store, **kw)  # type: ignore[arg-type]


# --- S1-S4: pools, limits, RAM ------------------------------------------------------------------


def test_kinds_overlap(store: Store) -> None:
    sched = _sched(store, cpu_workers=3, subprocess_slots=2, io_slots=2)
    for i in range(3):
        sched.add(_node(f"cpu{i}", SRC, kind="cpu", seconds=0.2, tag=f"c{i}"))
    for i in range(2):
        sched.add(_node(f"sub{i}", SRC, kind="subprocess", seconds=0.2, tag=f"s{i}"))
    for i in range(2):
        sched.add(_node(f"io{i}", SRC, kind="io", seconds=0.2, tag=f"i{i}"))
    t0 = time.perf_counter()
    refs, _ = _run(sched, list(sched.nodes))
    wall = time.perf_counter() - t0
    assert len(refs) == 7 and not sched.failed
    # one after the other they take 1.4 s; a loaded Windows runner took 0.61 s overlapping
    assert wall < 1.0, wall
    assert _max_concurrent(_spans()) >= 6


def test_cpu_limit_never_exceeded(store: Store) -> None:
    sched = _sched(store, cpu_workers=3)
    for i in range(8):
        sched.add(_node(f"n{i}", SRC, seconds=0.1, tag=str(i)))
    refs, _ = _run(sched, list(sched.nodes))
    assert len(refs) == 8
    assert _max_concurrent(_spans()) == 3


def test_subprocess_and_net_limits(store: Store) -> None:
    sched = _sched(store, subprocess_slots=1, net_slots=2)
    for i in range(3):
        sched.add(_node(f"s{i}", SRC, kind="subprocess", seconds=0.08, tag=f"s{i}"))
    for i in range(4):
        sched.add(_node(f"n{i}", SRC, kind="net", seconds=0.08, tag=f"n{i}"))
    _run(sched, list(sched.nodes))
    assert _max_concurrent(_spans(ids={"s0", "s1", "s2"})) == 1
    assert _max_concurrent(_spans(ids={"n0", "n1", "n2", "n3"})) == 2


def test_a_lane_has_its_own_limit_beside_the_kinds_slots(store: Store) -> None:
    """The OSM downloads of a batch share the Overpass quota, not the imagery's network slot: a
    download on the ``overpass`` lane runs while a ``net`` node runs, one lane node at a time."""
    sched = _sched(store, net_slots=1, lanes={"overpass": 1})
    for i in range(2):
        sched.add(_node(f"net{i}", SRC, kind="net", seconds=0.12, tag=f"net{i}"))
    for i in range(3):
        sched.add(_node(f"osm{i}", SRC, kind="net", lane="overpass", seconds=0.08, tag=f"o{i}"))
    refs, _ = _run(sched, list(sched.nodes))
    assert len(refs) == 5 and not sched.failed
    assert _max_concurrent(_spans(ids={"net0", "net1"})) == 1
    assert _max_concurrent(_spans(ids={"osm0", "osm1", "osm2"})) == 1
    assert _max_concurrent(_spans()) == 2  # one of each at once
    with pytest.raises(InvalidNodeError, match="no limit here"):
        _sched(store).add(_node("x", SRC, kind="net", lane="overpass", seconds=0.01, tag="x"))
    with pytest.raises(InvalidNodeError, match="thread kind"):
        _node("y", SRC, kind="cpu", lane="overpass", seconds=0.01, tag="y")


@pytest.mark.parametrize(("budget", "expected"), [(1000, 1), (1300, 2), (None, 4)])
def test_ram_budget(store: Store, budget: int | None, expected: int) -> None:
    sched = _sched(store, cpu_workers=4, ram_budget_mb=budget)
    for i in range(4):
        sched.add(_node(f"m{i}", SRC, ram_mb=600, seconds=0.1, tag=f"{budget}-{i}"))
    refs, _ = _run(sched, list(sched.nodes))
    assert len(refs) == 4
    assert _max_concurrent(_spans()) == expected


def test_oversized_node_is_admitted_alone(store: Store) -> None:
    sched = _sched(store, ram_budget_mb=1000)
    sched.add(_node("big", SRC, ram_mb=5000, seconds=0.02))
    sched.add(_node("small", SRC, ram_mb=100, seconds=0.02, tag="s"))
    refs, _ = _run(sched, ["big", "small"])
    assert set(refs) == {"big", "small"}


# --- S5, S14: critical path and costs -----------------------------------------------------------


def test_critical_path_starts_first(store: Store) -> None:
    sched = _sched(store, cpu_workers=1)
    sched.add(_node("lone", SRC, seconds=0.02, tag="lone"))
    a = _node("a", SRC, seconds=0.02, tag="a")
    b = _node("b", MID, seconds=0.02, a=a)
    c = _node("c", MID, seconds=0.02, a=b)
    sched.add(c)
    _run(sched, ["lone", "c"])
    order = [s.node_id for s in sorted(_spans(), key=lambda s: s.start)]
    # a (remaining path 3 s) and b (2 s) beat lone (1 s); c and lone tie -> insertion order
    assert order == ["a", "b", "lone", "c"]


def test_costs_learned_persisted_and_used_for_priority(store: Store) -> None:
    sched = _sched(store)
    sched.add(_node("x", SRC, seconds=0.05, tag="x"))
    _run(sched, ["x"])
    entry = sched.costs.entry("fake.src@1")
    assert entry is not None and entry.n == 1 and 0.04 < entry.ewma < 0.5
    reloaded = CostModel(store)
    assert reloaded.estimate_name("fake.src@1") == pytest.approx(entry.ewma)
    assert reloaded.estimate_name("never.seen@1") == 1.0

    # a lone node whose rule is known to be slow beats a chain of three cheap ones
    LOG.clear()
    costs = CostModel(store)
    costs.set("fake.join@1", 50.0)
    sched2 = _sched(store, cpu_workers=1, costs=costs)
    a = _node("a", SRC, seconds=0.02, tag="a")
    b = _node("b", MID, seconds=0.02, a=a)
    c = _node("c", MID, seconds=0.02, a=b)
    slow = _node("slow", JOIN, seconds=0.02, a=None, b=None)
    sched2.add(c)
    sched2.add(slow)
    _run(sched2, ["c", "slow"])
    assert sorted(_spans(), key=lambda s: s.start)[0].node_id == "slow"


def test_ewma_blends() -> None:
    m = CostModel(None, alpha=0.5)
    assert m.estimate(SRC) == 1.0
    m.observe(SRC, 4.0)
    assert m.estimate(SRC) == 4.0
    m.observe(SRC, 2.0)
    assert m.estimate(SRC) == 3.0


# --- S6, S7, S10, S11: hits, early cutoff, resume, dedup ----------------------------------------


def _graph(tag: str, payload: str = "same", run: Callable = run_stable) -> Node:
    a = _node(f"{tag}/src", SRC, seconds=0.02, tag=tag, payload=payload, run=run)
    b = _node(f"{tag}/mid", MID, seconds=0.02, a=a, run=run)
    return _node(f"{tag}/join", JOIN, seconds=0.02, a=b, b=a, run=run)


def test_second_run_is_all_hits(store: Store) -> None:
    sched = _sched(store)
    sched.add(_graph("t"))
    refs1, ev1 = _run(sched, ["t/join"])
    assert all(not d.hit for d in _done(ev1).values())
    assert len(_spans()) == 3
    LOG.clear()
    sched2 = _sched(store)
    sched2.add(_graph("t"))
    refs2, ev2 = _run(sched2, ["t/join"])
    assert refs1 == refs2
    assert all(d.hit for d in _done(ev2).values()) and len(_done(ev2)) == 3
    assert _spans() == []


def test_early_cutoff_stops_downstream(store: Store) -> None:
    sched = _sched(store)
    sched.add(_graph("t"))
    _, ev1 = _run(sched, ["t/join"])
    LOG.clear()
    # a changed upstream parameter that yields the same bytes rebuilds the upstream only
    a = _node("t/src", SRC, seconds=0.02, tag="other-tag", payload="same", run=run_stable)
    b = _node("t/mid", MID, seconds=0.02, a=a, run=run_stable)
    j = _node("t/join", JOIN, seconds=0.02, a=b, b=a, run=run_stable)
    sched2 = _sched(store)
    sched2.add(j)
    _, ev2 = _run(sched2, ["t/join"])
    done = _done(ev2)
    assert not done["t/src"].hit and done["t/mid"].hit and done["t/join"].hit
    assert done["t/src"].key != _done(ev1)["t/src"].key
    assert done["t/mid"].key == _done(ev1)["t/mid"].key
    assert [s.node_id for s in _spans()] == ["t/src"]


def test_resume_after_simulated_crash(store: Store) -> None:
    a = _node("a", SRC, seconds=0.02, tag="a", run=run_stable)
    crash = _node("b", MID, seconds=0.02, a=a, run=run_crash)
    sched = _sched(store)
    sched.add(crash)
    refs, _ = _run(sched, ["b"])
    assert refs == {} and set(sched.failed) == {"b"}
    assert sched.failed["b"].code == "SYS_INTERNAL_ERROR"
    leftovers = list((store.root / "fake.mid").rglob("*.tmp-*"))
    assert len(leftovers) == 1, "the simulated crash must leave a partial build behind"
    assert len(store) == 1  # only `a` is indexed

    a2 = _node("a", SRC, seconds=0.02, tag="a", run=run_stable)
    b2 = _node("b", MID, seconds=0.02, a=a2, run=run_stable)
    sched2 = _sched(store)
    sched2.add(b2)
    refs2, ev2 = _run(sched2, ["b"])
    done = _done(ev2)
    assert done["a"].hit and not done["b"].hit
    assert refs2["b"].path.read_bytes() == f"x|{done['a'].ref.digest[:8]}".encode()
    assert not sched2.failed


def test_two_tiles_sharing_a_recipe_run_it_once(store: Store) -> None:
    sched = _sched(store)
    # two tiles, two node objects with the same recipe under different ids
    shared1 = _node("+43+005/dem", SRC, seconds=0.15, tag="dem", run=run_stable)
    shared2 = _node("+43+006/dem", SRC, seconds=0.15, tag="dem", run=run_stable)
    m1 = _node("+43+005/mesh", MID, seconds=0.02, tag="m1", a=shared1, run=run_stable)
    m2 = _node("+43+006/mesh", MID, seconds=0.02, tag="m2", a=shared2, run=run_stable)
    sched.add(m1)
    sched.add(m2)
    refs, ev = _run(sched, ["+43+005/mesh", "+43+006/mesh"])
    assert set(refs) == {"+43+005/mesh", "+43+006/mesh"}
    assert len(_spans("fake.src")) == 1
    done = _done(ev)
    assert done["+43+005/dem"].key == done["+43+006/dem"].key
    assert sorted([done["+43+005/dem"].hit, done["+43+006/dem"].hit]) == [False, True]
    assert refs["+43+005/mesh"].key != refs["+43+006/mesh"].key


# --- S8, S9: cancellation and failure -----------------------------------------------------------


def test_cancel_under_one_second(store: Store) -> None:
    sched = _sched(store, cpu_workers=2)
    for i in range(6):
        sched.add(_node(f"n{i}", SRC, seconds=2.0, tag=str(i)))
    events: list[Event] = []

    async def go() -> dict[str, ArtifactRef]:
        asyncio.get_running_loop().call_later(0.2, sched.cancel)
        return await sched.run(list(sched.nodes), on_event=events.append)

    t0 = time.perf_counter()
    refs = asyncio.run(go())
    wall = time.perf_counter() - t0
    assert wall < 1.0, wall
    assert refs == {}
    assert set(sched.failed) == {f"n{i}" for i in range(6)}
    assert all(e.code == "SYS_CANCELLED" for e in sched.failed.values())
    assert len(store) == 0 and store.fsck().tmp_leftovers == ()
    assert sched.cancelled


def test_failure_skips_dependants_and_keeps_independent_branches(store: Store) -> None:
    a = _node("a", SRC, seconds=0.02, tag="a", fail=True)
    b = _node("b", MID, seconds=0.02, a=a)
    c = _node("c", SRC, seconds=0.1, tag="c")
    sched = _sched(store)
    sched.add(b)
    sched.add(c)
    refs, ev = _run(sched, ["b", "c"])
    failed = _failed(ev)
    assert failed["a"].error.code == "MESH_TRIANGULATION_FAILED" and failed["a"].cause is None
    # dette D1: a node skipped because an upstream failed carries SYS_UPSTREAM_FAILED, not
    # SYS_CANCELLED, so a batch report tells a user-stopped run from a broken dependency.
    assert failed["b"].error.code == "SYS_UPSTREAM_FAILED" and failed["b"].cause == "a"
    assert failed["b"].error.context["root"] == "a"
    assert "b" not in {s.node_id for s in _spans()}
    assert set(refs) == {"c"} and _done(ev)["c"].hit is False


def test_fail_fast_cancels_the_rest(store: Store) -> None:
    a = _node("a", SRC, seconds=0.02, tag="a", fail=True)
    c = _node("c", SRC, seconds=10.0, tag="c")
    d = _node("d", MID, seconds=0.02, a=c)
    sched = _sched(store, fail_fast=True)
    sched.add(a)
    sched.add(d)
    t0 = time.perf_counter()
    refs, _ = _run(sched, ["a", "d"])
    # c, left to run, would take 10 s; cancelled it costs nothing (0.03 s here). The margin is what
    # a loaded runner takes to get the threads scheduled at all: 0.8 s, then 1 s, then 2 s failed
    # in turn on a busy Windows runner, the last one at 3.2 s (2026-09-19).
    assert time.perf_counter() - t0 < 5.0
    assert refs == {}
    assert sched.failed["a"].code == "MESH_TRIANGULATION_FAILED"
    assert sched.failed["c"].code == "SYS_CANCELLED"  # cancelled in flight by fail_fast
    assert sched.failed["d"].code == "SYS_UPSTREAM_FAILED"  # dette D1: skipped, c fell first


def test_wrong_key_is_an_internal_error(store: Store) -> None:
    sched = _sched(store)
    sched.add(_node("w", SRC, run=run_wrong_key))
    refs, _ = _run(sched, ["w"])
    assert refs == {} and sched.failed["w"].code == "SYS_INTERNAL_ERROR"
    assert "KeyMismatch" in sched.failed["w"].message


# --- S13, S15: events, P0 rules, plan, validation -----------------------------------------------


def test_events_order_thread_and_stats(store: Store) -> None:
    sched = _sched(store, cpu_workers=2)
    a = _node("a", SRC, seconds=0.05, tag="a")
    b = _node("b", MID, seconds=0.05, a=a)
    sched.add(b)
    main = threading.get_ident()
    threads: set[int] = set()
    events: list[Event] = []

    def on_event(e: Event) -> None:
        threads.add(threading.get_ident())
        events.append(e)

    asyncio.run(sched.run(["b"], on_event=on_event))
    assert threads == {main}
    for nid in ("a", "b"):
        mine = [e for e in events if getattr(e, "node_id", None) == nid]
        assert isinstance(mine[0], Started) and mine[0].kind == "cpu"
        assert isinstance(mine[-1], Done)
        assert all(isinstance(e, Progress) for e in mine[1:-1]) and len(mine) >= 3
        assert [e.fraction for e in mine[1:-1] if isinstance(e, Progress)] == [0.0, 0.5]
    stats = [e for e in events if isinstance(e, Stats)]
    assert stats and stats[-1].done == 2 and stats[-1].running == 0 and stats[-1].pending == 0
    assert stats[-1].failed == 0 and stats[-1].eta_s == 0.0 and stats[0].pending >= 1


def test_p0_rule_without_run(store: Store) -> None:
    sched = _sched(store)
    sched.add(_node("p", SRC, run=None, tag="p0"))
    refs, ev = _run(sched, ["p"])
    assert refs["p"].path.read_bytes().startswith(b"p0:")
    assert refs["p"].rule == "fake.src" and store.has(refs["p"].key)
    assert not _done(ev)["p"].hit


def test_plan_reports_hit_build_unknown(store: Store) -> None:
    sched = _sched(store)
    sched.add(_graph("t"))
    before = {e.node_id: e for e in sched.plan(["t/join"])}
    assert before["t/src"].status == "build" and before["t/src"].seconds == 1.0
    assert before["t/mid"].status == "unknown" and before["t/join"].status == "unknown"
    _run(sched, ["t/join"])
    sched2 = _sched(store)
    sched2.add(_graph("t"))
    after = {e.node_id: e for e in sched2.plan(["t/join"])}
    assert {e.status for e in after.values()} == {"hit"}
    assert all(e.seconds == 0.0 for e in after.values())


def test_validation_errors(store: Store) -> None:
    sched = _sched(store)
    a = _node("a", SRC)
    sched.add(a)
    with pytest.raises(InvalidNodeError, match="already registered"):
        sched.add(_node("a", SRC, tag="other"))
    with pytest.raises(InvalidNodeError, match="unknown target"):
        asyncio.run(sched.run(["nope"]))
    with pytest.raises(InvalidNodeError, match="inputs are"):
        Node("bad", MID, SleepParams(), {}, run=run_sleep)
    with pytest.raises(InvalidNodeError, match="kind"):
        Node("bad", SRC, SleepParams(), {}, kind="gpu", run=run_sleep)  # type: ignore[arg-type]
    x = _node("x", MID, a=a)
    x.inputs["a"] = x  # a cycle
    sched2 = _sched(store)
    sched2.add(x)
    with pytest.raises(InvalidNodeError, match="cycle"):
        asyncio.run(sched2.run(["x"]))


def test_artifact_ref_inputs_are_accepted(store: Store) -> None:
    sched = _sched(store)
    sched.add(_node("a", SRC, tag="a", run=run_stable))
    refs, _ = _run(sched, ["a"])
    sched2 = _sched(store)
    sched2.add(_node("b", MID, a=refs["a"], run=run_stable))
    refs2, _ = _run(sched2, ["b"])
    assert refs2["b"].path.read_bytes() == f"x|{refs['a'].digest[:8]}".encode()
    assert store.why(refs2["b"].key).inputs[0].key == refs["a"].key


# --- S12: real worker processes -----------------------------------------------------------------


def test_process_pool_runs_progress_and_errors(store: Store) -> None:
    sched = Scheduler(store, cpu_workers=2)
    for i in range(3):
        sched.add(_node(f"p{i}", SRC, seconds=0.05, tag=str(i)))
    sched.add(_node("bad", SRC, seconds=0.02, tag="bad", fail=True))
    sched.add(_node("io", SRC, kind="io", seconds=0.02, tag="io"))
    refs, ev = _run(sched, list(sched.nodes))
    assert set(refs) == {"p0", "p1", "p2", "io"}
    pids = {int(refs[f"p{i}"].path.read_bytes().split(b"pid=")[1]) for i in range(3)}
    assert os.getpid() not in pids and len(pids) <= 2
    assert int(refs["io"].path.read_bytes().split(b"pid=")[1]) == os.getpid()
    progress = [e for e in ev if isinstance(e, Progress) and e.node_id.startswith("p")]
    assert {e.fraction for e in progress} == {0.0, 0.5}
    assert sched.failed["bad"].code == "MESH_TRIANGULATION_FAILED"
    assert sched.failed["bad"].context["tile"] == "bad"


def test_process_pool_cancel(store: Store) -> None:
    sched = Scheduler(store, cpu_workers=2, cancel_grace_s=2.0)
    for i in range(3):
        sched.add(_node(f"p{i}", SRC, seconds=5.0, tag=str(i)))

    async def go() -> dict[str, ArtifactRef]:
        asyncio.get_running_loop().call_later(0.5, sched.cancel)
        return await sched.run(list(sched.nodes))

    t0 = time.perf_counter()
    refs = asyncio.run(go())
    assert time.perf_counter() - t0 < 2.5
    assert refs == {} and all(e.code == "SYS_CANCELLED" for e in sched.failed.values())
    assert len(store) == 0


def run_idle_then_work(ctx: NodeContext) -> ArtifactRef:
    """A node that waits without using its slot, as the second pass of a tile does."""
    params = ctx.params
    assert isinstance(params, SleepParams)
    t0 = time.perf_counter()
    with ctx.idle():
        time.sleep(params.seconds)
    _poll_sleep(ctx, params.seconds)
    ref = ctx.produce(lambda out: out.write_bytes(params.payload.encode()))
    with _LOG_LOCK:
        LOG.append(Span(ctx.node_id, "?", ctx.rule.name, t0, time.perf_counter()))
    return ref


def test_a_waiting_node_lends_its_network_slot(store: Store) -> None:
    """One tile waiting between two spaced retry rounds left the whole batch's line idle: a
    build has one network slot (a user, 2026-09-18). While it waits, the next node may start.
    """
    sched = _sched(store, net_slots=1)
    sched.add(_node("waiter", SRC, kind="net", seconds=0.25, tag="w", run=run_idle_then_work))
    sched.add(_node("next", SRC, kind="net", seconds=0.1, tag="n"))
    _run(sched, ["waiter", "next"])
    spans = _spans(ids={"waiter", "next"})
    assert _max_concurrent(spans) == 2, "the waiting node never lent its slot"
    # and the slot is counted once: the run ends without the counter going negative
    assert sched._running["net"] == 0


def test_a_listener_that_stops_the_run_never_prints_from_a_callback(store: Store) -> None:
    """A listener stops a run by raising, and the scheduler lets that through where the caller can
    see it. From a callback of the loop nothing can: asyncio would print the whole traceback in the
    log, which reads like a crash (a user who cancelled a build saw two, 2026-09-19).
    """

    class Stop(BaseException):
        pass

    seen: list[str] = []

    def listener(event: Event) -> None:
        seen.append(type(event).__name__)
        if isinstance(event, Progress):  # progress alone reaches the listener from a callback
            raise Stop

    sched = _sched(store)
    sched.add(_node("a", SRC, seconds=0.2, tag="a"))
    refs = asyncio.run(sched.run(["a"], on_event=listener))
    assert refs and "Progress" in seen  # the node ran, reported, and the run went through

    # from the coroutine, where the caller can see it, the same refusal is let through
    with pytest.raises(Stop):
        sched._emit(Progress("a", 0.5, "half"))
