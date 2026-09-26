# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Minimal synchronous executor over a declared DAG of nodes.

Resolves inputs depth-first, computes each node's key from its rule, params and input
*digests*, skips what the store already holds, builds the rest, and pins the target on
request. Early cutoff is a consequence of keying by input digests: a rebuilt upstream whose
bytes did not change leaves every downstream key unchanged.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from orthostudio.graph.digest import check_digest, digest_bytes, digest_path
from orthostudio.graph.errors import BudgetExceededError, InvalidNodeError, RuleFailedError
from orthostudio.graph.keys import key_for
from orthostudio.graph.rule import ResolvedInput, Rule, RuleParams, RunContext
from orthostudio.graph.store import InputRef, Store

__all__ = [
    "Event",
    "Executor",
    "Node",
    "NodeFinished",
    "NodeResult",
    "NodeStarted",
    "PlanEntry",
    "RunReport",
    "Source",
]


@dataclass(frozen=True, slots=True)
class Source:
    """An external input identified by the digest of its content (a file, a download...)."""

    digest: str
    path: Path | None = None
    label: str = ""

    def __post_init__(self) -> None:
        check_digest(self.digest, "source digest")

    @classmethod
    def from_path(cls, path: str | Path, label: str = "") -> Source:
        p = Path(path)
        return cls(digest_path(p)[0], p, label or p.name)

    @classmethod
    def from_bytes(cls, data: bytes, label: str = "") -> Source:
        return cls(digest_bytes(data), None, label)


class Node:
    """A rule applied to frozen params and to named inputs (nodes, sources or ``None``)."""

    __slots__ = ("inputs", "params", "rule")

    def __init__(
        self,
        rule: Rule,
        params: RuleParams | None = None,
        inputs: Mapping[str, Node | Source | None] | None = None,
    ) -> None:
        if params is None:
            params = rule.params()
        if not isinstance(params, rule.params):
            raise InvalidNodeError(
                f"rule {rule.name!r} expects {rule.params.__name__}, got {type(params).__name__}"
            )
        given = dict(inputs or {})
        if set(given) != set(rule.inputs):
            raise InvalidNodeError(
                f"rule {rule.name!r} inputs are {list(rule.inputs)}, node gives {sorted(given)}"
            )
        for name, dep in given.items():
            if dep is not None and not isinstance(dep, (Node, Source)):
                raise InvalidNodeError(f"input {name!r} must be a Node, a Source or None")
        self.rule = rule
        self.params = params
        self.inputs: dict[str, Node | Source | None] = {k: given[k] for k in sorted(given)}

    def __repr__(self) -> str:
        return f"Node({self.rule.name}@{self.rule.version})"


@dataclass(frozen=True, slots=True)
class NodeResult:
    key: str
    digest: str
    path: Path
    status: Literal["hit", "built"]
    seconds: float
    size: int
    identical_to_previous: bool = False
    """True when a *rebuilt* artefact has the same bytes as an older key of the same rule."""


@dataclass(frozen=True, slots=True)
class NodeStarted:
    node: Node
    key: str


@dataclass(frozen=True, slots=True)
class NodeFinished:
    node: Node
    result: NodeResult


Event = NodeStarted | NodeFinished


@dataclass(frozen=True, slots=True)
class RunReport:
    target: NodeResult
    results: dict[Node, NodeResult] = field(default_factory=dict)

    @property
    def built(self) -> list[Node]:
        return [n for n, r in self.results.items() if r.status == "built"]

    @property
    def hits(self) -> list[Node]:
        return [n for n, r in self.results.items() if r.status == "hit"]


@dataclass(frozen=True, slots=True)
class PlanEntry:
    node: Node
    key: str | None
    status: Literal["hit", "build", "unknown"]
    """``unknown``: some input is not built yet, so the key cannot be computed."""


class Executor:
    """Runs nodes against a :class:`Store`; one node at a time, in dependency order."""

    def __init__(
        self,
        store: Store,
        *,
        on_event: Callable[[Event], None] | None = None,
        ram_budget_mb: int | None = None,
    ) -> None:
        self.store = store
        self.on_event = on_event
        self.ram_budget_mb = ram_budget_mb

    def run(self, target: Node, *, pin: str | None = None) -> RunReport:
        """Build ``target`` and everything it needs; optionally pin it under ``pin``."""
        memo: dict[Node, NodeResult] = {}
        result = self._resolve(target, memo)
        if pin is not None:
            self.store.pin(pin, result.key)
        return RunReport(target=result, results=memo)

    def plan(self, target: Node) -> list[PlanEntry]:
        """Without building anything, say what a run would hit, build or cannot yet key."""
        entries: list[PlanEntry] = []
        digests: dict[Node, str | None] = {}

        def visit(node: Node) -> str | None:
            if node in digests:
                return digests[node]
            inputs: dict[str, str | None] = {}
            unknown = False
            for name, dep in node.inputs.items():
                if dep is None:
                    inputs[name] = None
                elif isinstance(dep, Source):
                    inputs[name] = dep.digest
                else:
                    d = visit(dep)
                    if d is None:
                        unknown = True
                    inputs[name] = d
            if unknown:
                entries.append(PlanEntry(node, None, "unknown"))
                digests[node] = None
                return None
            key, _ = key_for(node.rule, node.params, inputs)
            digest = self.store.digest_of(key)
            entries.append(PlanEntry(node, key, "hit" if digest else "build"))
            digests[node] = digest
            return digest

        visit(target)
        return entries

    # -- internals -----------------------------------------------------------------------

    def _resolve(self, node: Node, memo: dict[Node, NodeResult]) -> NodeResult:
        if node in memo:
            return memo[node]
        resolved: dict[str, ResolvedInput] = {}
        for name, dep in node.inputs.items():
            if dep is None:
                resolved[name] = ResolvedInput(name, None, None)
            elif isinstance(dep, Source):
                resolved[name] = ResolvedInput(name, dep.digest, dep.path)
            else:
                r = self._resolve(dep, memo)
                resolved[name] = ResolvedInput(name, r.digest, r.path, r.key)
        key, recipe = key_for(node.rule, node.params, {n: r.digest for n, r in resolved.items()})
        refs = [InputRef(r.name, r.digest, r.key) for r in resolved.values()]
        self._emit(NodeStarted(node, key))
        t0 = time.perf_counter()
        store = self.store
        if store.has(key):
            store.touch(key, refs)
            info = store.info(key)
            assert info is not None
            result = NodeResult(
                key, info.digest, info.path, "hit", time.perf_counter() - t0, info.size
            )
        else:
            result = self._build(node, key, recipe, resolved, refs, t0)
        memo[node] = result
        self._emit(NodeFinished(node, result))
        return result

    def _build(
        self,
        node: Node,
        key: str,
        recipe: str,
        resolved: dict[str, ResolvedInput],
        refs: list[InputRef],
        t0: float,
    ) -> NodeResult:
        rule = node.rule
        if self.ram_budget_mb is not None and rule.ram_mb > self.ram_budget_mb:
            raise BudgetExceededError(
                f"rule {rule.name!r} declares {rule.ram_mb} MB, budget is {self.ram_budget_mb} MB"
            )
        with self.store.begin(rule.name, key, rule.kind) as build:
            ctx = RunContext(
                rule=rule,
                key=key,
                params=node.params,
                inputs=resolved,
                out=build.out,
                scratch=build.scratch,
            )
            try:
                rule.fn(ctx)
            except Exception as exc:
                raise RuleFailedError(rule.name, key, exc) from exc
            info, _adopted = build.commit(version=rule.version, recipe=recipe, inputs=refs)
        twins = [k for k in self.store.keys_with_digest(rule.name, info.digest) if k != key]
        return NodeResult(
            key,
            info.digest,
            info.path,
            "built",
            time.perf_counter() - t0,
            info.size,
            identical_to_previous=bool(twins),
        )

    def _emit(self, event: Event) -> None:
        if self.on_event is not None:
            self.on_event(event)
