# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Executor over a small vectors -> mesh -> masks DAG: hits, scoped invalidation, cutoff."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from orthostudio.graph import (
    BudgetExceededError,
    Event,
    Executor,
    InvalidNodeError,
    Node,
    NodeFinished,
    NodeStarted,
    RuleFailedError,
    RuleParams,
    RunContext,
    Source,
    Store,
    rule,
)


class VecParams(RuleParams):
    road_level: int = 1


class MeshParams(RuleParams):
    curvature_tol: float = 2.0


class MaskParams(RuleParams):
    masks_width: int = 100


class Calls:
    def __init__(self) -> None:
        self.log: list[str] = []


@pytest.fixture
def calls() -> Calls:
    return Calls()


@pytest.fixture
def rules(calls: Calls) -> dict[str, Any]:
    @rule(name="vectors", version=1, params=VecParams, inputs=("osm", "dem"), ram_mb=800)
    def vectors(ctx: RunContext) -> None:
        calls.log.append("vectors")
        # road_level is capped at 2 so that 2 and 3 produce identical bytes (early cutoff)
        level = min(ctx.params.road_level, 2)
        dem = ctx.inputs["dem"]
        ctx.out.write_bytes(
            ctx.input_path("osm").read_bytes() + f"|lvl{level}|{dem.present}".encode()
        )

    @rule(name="mesh", version=1, params=MeshParams, inputs=("vectors",), kind="dir")
    def mesh(ctx: RunContext) -> None:
        calls.log.append("mesh")
        (ctx.out / "tris.bin").write_bytes(ctx.input_path("vectors").read_bytes()[::-1])
        (ctx.out / "tol.txt").write_text(str(ctx.params.curvature_tol))

    @rule(name="masks", version=1, params=MaskParams, inputs=("mesh", "nb_n"), ram_mb=1000)
    def masks(ctx: RunContext) -> None:
        calls.log.append("masks")
        nb = ctx.inputs["nb_n"]
        tris = (ctx.input_path("mesh") / "tris.bin").read_bytes()
        ctx.out.write_bytes(tris + f"|w{ctx.params.masks_width}|nb={nb.digest}".encode())

    return {"vectors": vectors, "mesh": mesh, "masks": masks}


@pytest.fixture
def osm(tmp_path: Path) -> Path:
    p = tmp_path / "osm.json"
    p.write_bytes(b"coastline")
    return p


@pytest.fixture
def dag(rules: dict[str, Any], osm: Path) -> Callable[..., Node]:
    def build(cfg: dict[str, Any], *, dem: Source | None = None, nb: Node | None = None) -> Node:
        v = Node(
            rules["vectors"], rules["vectors"].bind(cfg), {"osm": Source.from_path(osm), "dem": dem}
        )
        m = Node(rules["mesh"], rules["mesh"].bind(cfg), {"vectors": v})
        return Node(rules["masks"], rules["masks"].bind(cfg), {"mesh": m, "nb_n": nb})

    return build


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "store", fsync=False) as s:
        yield s


CFG = {"road_level": 1, "curvature_tol": 2.0, "masks_width": 100, "default_zl": 16}


def _status(report: Any) -> dict[str, str]:
    return {n.rule.name: r.status for n, r in report.results.items()}


def test_first_run_builds_everything_then_hits(store: Store, dag: Any, calls: Calls) -> None:
    ex = Executor(store)
    r1 = ex.run(dag(CFG), pin="tile:+43+005")
    assert _status(r1) == {"vectors": "built", "mesh": "built", "masks": "built"}
    assert calls.log == ["vectors", "mesh", "masks"]
    assert r1.target.path.read_bytes().startswith(b"eslaF|1lvl|eniltsaoc")  # reversed vectors
    assert store.pins() == {"tile:+43+005": r1.target.key}
    r2 = ex.run(dag(CFG))
    assert _status(r2) == {"vectors": "hit", "mesh": "hit", "masks": "hit"}
    assert calls.log == ["vectors", "mesh", "masks"]
    assert r2.target.key == r1.target.key
    assert len(r2.hits) == 3 and r2.built == []


def test_unconsumed_parameter_changes_nothing(store: Store, dag: Any, calls: Calls) -> None:
    ex = Executor(store)
    ex.run(dag(CFG))
    r = ex.run(dag({**CFG, "default_zl": 18}))
    assert _status(r) == {"vectors": "hit", "mesh": "hit", "masks": "hit"}
    assert len(calls.log) == 3


def test_parameter_change_invalidates_only_consumers_downstream(
    store: Store, dag: Any, calls: Calls
) -> None:
    ex = Executor(store)
    ex.run(dag(CFG))
    calls.log.clear()
    r = ex.run(dag({**CFG, "masks_width": 200}))
    assert _status(r) == {"vectors": "hit", "mesh": "hit", "masks": "built"}
    assert calls.log == ["masks"]
    calls.log.clear()
    r = ex.run(dag({**CFG, "curvature_tol": 3.0}))
    assert _status(r) == {"vectors": "hit", "mesh": "built", "masks": "built"}
    assert calls.log == ["mesh", "masks"]
    assert len(store) == 3 + 1 + 2


def test_early_cutoff_stops_at_identical_bytes(store: Store, dag: Any, calls: Calls) -> None:
    ex = Executor(store)
    ex.run(dag({**CFG, "road_level": 2}))
    calls.log.clear()
    r = ex.run(dag({**CFG, "road_level": 3}))  # vectors bytes identical to road_level 2
    assert _status(r) == {"vectors": "built", "mesh": "hit", "masks": "hit"}
    assert calls.log == ["vectors"]
    by_name = {n.rule.name: res for n, res in r.results.items()}
    assert by_name["vectors"].identical_to_previous is True
    assert by_name["mesh"].identical_to_previous is False
    # two vectors artefacts share one digest; the mesh edge now points at the latest one
    assert len(store.keys_with_digest("vectors", by_name["vectors"].digest)) == 2
    assert store.refcount(by_name["vectors"].key) == 1
    r = ex.run(dag({**CFG, "road_level": 1}))
    assert _status(r) == {"vectors": "built", "mesh": "built", "masks": "built"}


def test_source_content_and_absent_inputs_are_part_of_the_key(
    store: Store, dag: Any, osm: Path, calls: Calls, tmp_path: Path
) -> None:
    ex = Executor(store)
    k0 = ex.run(dag(CFG)).target.key
    dem = tmp_path / "dem.hgt"
    dem.write_bytes(b"\x00" * 16)
    r = ex.run(dag(CFG, dem=Source.from_path(dem)))
    assert _status(r) == {"vectors": "built", "mesh": "built", "masks": "built"}
    assert r.target.key != k0
    osm.write_bytes(b"coastline v2")
    r = ex.run(dag(CFG))
    assert _status(r) == {"vectors": "built", "mesh": "built", "masks": "built"}
    assert len(calls.log) == 9


def test_neighbour_node_as_input(store: Store, dag: Any, rules: dict[str, Any], osm: Path) -> None:
    ex = Executor(store)
    with pytest.raises(RuleFailedError):  # Source.from_bytes has no path to read
        ex.run(Node(rules["vectors"], VecParams(), {"osm": Source.from_bytes(b"x"), "dem": None}))
    nb = Node(
        rules["vectors"], VecParams(road_level=1), {"osm": Source.from_path(osm), "dem": None}
    )
    r = ex.run(dag(CFG, nb=nb))
    vec = [res for n, res in r.results.items() if n.rule.name == "vectors"]
    # two distinct Node objects with the same recipe share one key: built once, hit once
    assert len(vec) == 2 and len({v.key for v in vec}) == 1
    assert sorted(v.status for v in vec) == ["built", "hit"]
    assert r.target.path.read_bytes().endswith(b"|nb=" + vec[0].digest.encode())


def test_failure_leaves_no_trace_and_chains_cause(store: Store, rules: dict[str, Any]) -> None:
    @rule(name="broken", version=1)
    def broken(ctx: RunContext) -> None:
        ctx.out.write_bytes(b"partial")
        raise ValueError("no sea in tile")

    ex = Executor(store)
    with pytest.raises(RuleFailedError) as info:
        ex.run(Node(broken))
    assert isinstance(info.value.__cause__, ValueError)
    assert info.value.rule == "broken"
    assert len(store) == 0 and store.fsck().clean


def test_rule_that_writes_nothing_is_a_commit_error(store: Store) -> None:
    from orthostudio.graph import CommitError

    @rule(name="lazy", version=1)
    def lazy(ctx: RunContext) -> None:
        pass

    with pytest.raises(CommitError):
        Executor(store).run(Node(lazy))
    assert store.fsck().clean


def test_events_and_ram_budget(store: Store, dag: Any) -> None:
    events: list[Event] = []
    ex = Executor(store, on_event=events.append, ram_budget_mb=900)
    with pytest.raises(BudgetExceededError):
        ex.run(dag(CFG))  # masks declares 1000 MB
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["NodeStarted", "NodeFinished"] * 2 + ["NodeStarted"]
    assert isinstance(events[0], NodeStarted) and isinstance(events[1], NodeFinished)
    assert events[1].result.status == "built"
    ex = Executor(store, on_event=events.append, ram_budget_mb=4000)
    r = ex.run(dag(CFG))
    assert _status(r) == {"vectors": "hit", "mesh": "hit", "masks": "built"}


def test_plan_reports_hit_build_unknown(store: Store, dag: Any) -> None:
    ex = Executor(store)
    plan = ex.plan(dag(CFG))
    assert [(e.node.rule.name, e.status) for e in plan] == [
        ("vectors", "build"),
        ("mesh", "unknown"),
        ("masks", "unknown"),
    ]
    ex.run(dag(CFG))
    plan = ex.plan(dag({**CFG, "masks_width": 300}))
    assert [(e.node.rule.name, e.status) for e in plan] == [
        ("vectors", "hit"),
        ("mesh", "hit"),
        ("masks", "build"),
    ]
    assert all(e.key for e in plan)


def test_node_validation(rules: dict[str, Any]) -> None:
    with pytest.raises(InvalidNodeError):
        Node(rules["mesh"], VecParams(), {"vectors": None})
    with pytest.raises(InvalidNodeError):
        Node(rules["mesh"], MeshParams(), {})
    with pytest.raises(InvalidNodeError):
        Node(rules["mesh"], MeshParams(), {"vectors": "not a node"})  # type: ignore[dict-item]
    n = Node(rules["mesh"], MeshParams(), {"vectors": None})
    assert repr(n) == "Node(mesh@1)"


def test_why_after_run_tells_the_whole_story(store: Store, dag: Any) -> None:
    ex = Executor(store)
    r = ex.run(dag(CFG), pin="tile:+43+005")
    prov = store.why(r.target.key)
    assert prov.info.rule == "masks" and prov.params == {"masks_width": 100}
    names = [i.name for i in prov.inputs]
    assert names == ["mesh", "nb_n"]
    assert prov.inputs[1].digest is None
    mesh_key = prov.inputs[0].key
    assert mesh_key is not None
    assert store.why(mesh_key).dependents == ((r.target.key, "mesh"),)
    text = store.explain(r.target.key, depth=2)
    assert "masks@1" in text and "mesh@1" in text and "vectors@1" in text
