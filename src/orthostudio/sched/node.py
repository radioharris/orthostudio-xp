# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Scheduler nodes and the context a node run receives.

Spec: ``docs/specs/scheduler.md`` sections 2.1 and 2.2. A :class:`Node` is a P0 ``Rule``
applied to frozen params and named inputs, plus the *kind* of pool it needs, its RAM cost
and the callable that produces its artefact. :class:`NodeContext` gives that callable the
store, a working directory, the resolved inputs, a progress callback and a cancel token, and
helpers that apply the P0 write protocol (``begin`` / ``commit``).
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from orthostudio.errors import OsxpError
from orthostudio.graph.errors import InvalidNodeError
from orthostudio.graph.rule import ResolvedInput, Rule, RuleParams, RunContext
from orthostudio.graph.store import ArtifactInfo, Build, InputRef, Store
from orthostudio.model import ArtifactRef
from orthostudio.sched.events import NodeKind

__all__ = [
    "CancelToken",
    "Node",
    "NodeContext",
    "NodeRun",
    "artifact_ref",
    "own_bytes",
    "run_context",
    "run_p0_rule",
]

_KINDS: tuple[NodeKind, ...] = ("cpu", "net", "subprocess", "io")


class CancelToken(Protocol):
    """What a node sees of cancellation: a ``threading.Event`` or a ``multiprocessing.Event``."""

    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


def artifact_ref(info: ArtifactInfo) -> ArtifactRef:
    """The :class:`ArtifactRef` of an index row."""
    return ArtifactRef(info.key, info.digest, info.path, info.rule, info.kind, info.size)


def own_bytes(path: Path) -> int:
    """The bytes of the files under ``path`` that take room of their own: a hard link to a file
    already in the store (an image in a tile's folder, a DSF in its pack) takes none, a symbolic
    link the length of its target. What a step really put on the disk, its index size counting a
    linked image as many times as it is linked."""
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not os.path.isdir(path) or os.path.islink(path):
        return st.st_size if st.st_nlink <= 1 else 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if st.st_nlink <= 1:
                total += st.st_size
    return total


@dataclass(slots=True)
class NodeContext:
    """Everything a node run receives (spec 2.1)."""

    node_id: str
    rule: Rule
    params: RuleParams
    key: str
    recipe: str
    inputs: Mapping[str, ArtifactRef | None]
    store: Store
    workdir: Path
    """An empty directory owned by this run; removed after success, kept after a failure."""
    progress: Callable[[float, str], None]
    cancel_event: CancelToken
    written: int = 0
    """Bytes this run put on the disk: what it committed to the store (:meth:`commit`, links to
    what the store holds left out) and what it says it wrote elsewhere (:meth:`wrote`: a cache of
    image pieces, relief files, images committed by its own workers)."""
    downloaded: int = 0
    """Bytes this run received from the network (:meth:`fetched`)."""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    set_idle: Callable[[bool], None] = lambda _idle: None
    """Say the run is holding its slot without using it (a spaced retry waiting), so another node
    of the same kind may start meanwhile: a build has one network slot, and a tile waiting for a
    handful of stuck image pieces left the line idle for the whole batch (a user, 2026-09-18).
    The slot is taken back without waiting when the run resumes, so at most one extra node of that
    kind runs during a retry round -- rounds ask at a low concurrency by design."""

    # -- giving the slot back while waiting ------------------------------------------------

    @contextlib.contextmanager
    def idle(self) -> Iterator[None]:
        """Hold the slot back for the block (``with ctx.idle(): await pause``)."""
        self.set_idle(True)
        try:
            yield
        finally:
            self.set_idle(False)

    # -- inputs ----------------------------------------------------------------------------

    def input_path(self, name: str) -> Path:
        """Path of a present input, or ``LookupError``."""
        ref = self.inputs[name]
        if ref is None:
            raise LookupError(f"input {name!r} of node {self.node_id!r} is absent")
        return ref.path

    def input_refs(self) -> list[InputRef]:
        """The index edges of this artefact (parent keys only when they are in the store)."""
        refs: list[InputRef] = []
        for name in sorted(self.inputs):
            ref = self.inputs[name]
            if ref is None:
                refs.append(InputRef(name, None, None))
            else:
                key = ref.key if self.store.has(ref.key) else None
                refs.append(InputRef(name, ref.digest, key))
        return refs

    # -- what the run put on the disk and took from the network --------------------------

    def wrote(self, nbytes: int) -> None:
        """Count ``nbytes`` written outside :meth:`commit` (thread-safe)."""
        with self._lock:
            self.written += max(0, int(nbytes))

    def fetched(self, nbytes: int) -> None:
        """Count ``nbytes`` received from the network (thread-safe)."""
        with self._lock:
            self.downloaded += max(0, int(nbytes))

    # -- cancellation ----------------------------------------------------------------------

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check_cancelled(self) -> None:
        """Raise ``SYS_CANCELLED`` when the run was cancelled (call it between steps)."""
        if self.cancel_event.is_set():
            raise OsxpError("SYS_CANCELLED", context={"node": self.node_id})

    # -- write protocol --------------------------------------------------------------------

    def existing(self) -> ArtifactRef | None:
        """The artefact at :attr:`key` if the store already holds it (touched), else None."""
        if not self.store.has(self.key):
            return None
        self.store.touch(self.key, self.input_refs())
        info = self.store.info(self.key)
        assert info is not None
        return artifact_ref(info)

    def begin(self) -> Build:
        """Start the P0 write protocol for this node's key (use as a context manager)."""
        return self.store.begin(self.rule.name, self.key, self.rule.kind)

    def commit(self, build: Build) -> ArtifactRef:
        """Commit a build started with :meth:`begin`; the returned ref is what ``run`` returns."""
        info, adopted = build.commit(
            version=self.rule.version, recipe=self.recipe, inputs=self.input_refs()
        )
        if not adopted:  # adopted: another process had put it there, ours went away
            self.wrote(own_bytes(info.path))
        return artifact_ref(info)

    def produce(self, writer: Callable[[Path], object]) -> ArtifactRef:
        """``begin``, let ``writer`` fill the output path, ``commit``: the common case."""
        with self.begin() as build:
            writer(build.out)
            return self.commit(build)


NodeRun = Callable[[NodeContext], ArtifactRef]


def run_context(ctx: NodeContext, build: Build) -> RunContext:
    """What the rule's P0 ``fn`` receives for a build started with ``ctx.begin()``."""
    resolved = {
        name: ResolvedInput(name, None, None)
        if ref is None
        else ResolvedInput(name, ref.digest, ref.path, ref.key)
        for name, ref in ctx.inputs.items()
    }
    return RunContext(
        rule=ctx.rule,
        key=ctx.key,
        params=ctx.params,
        inputs=resolved,
        out=build.out,
        scratch=build.scratch,
    )


def run_p0_rule(ctx: NodeContext) -> ArtifactRef:
    """Default ``run``: apply the rule's P0 ``fn`` through the write protocol."""
    with ctx.begin() as build:
        ctx.rule.fn(run_context(ctx, build))
        return ctx.commit(build)


@dataclass(eq=False, slots=True)
class Node:
    """One vertex of the build graph (spec 2.1). Compared by identity, addressed by ``id``."""

    id: str
    rule: Rule
    params: RuleParams
    inputs: dict[str, Node | ArtifactRef | None] = field(default_factory=dict)
    kind: NodeKind = "cpu"
    ram_mb: int | None = None
    """Peak RAM of one run; ``None`` takes the rule's declaration."""
    run: NodeRun | None = None
    """Produces the artefact at ``ctx.key``; ``None`` runs ``rule.fn`` (P0 protocol)."""
    lane: str | None = None
    """A limit of its own, counted instead of its kind's slots (``Scheduler(lanes=...)``): the
    OSM downloads share the Overpass mirrors' quota, not the network slot of the imagery. Only
    for a kind that runs in a thread (not ``cpu``)."""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise InvalidNodeError("node id must be a non-empty string")
        if not isinstance(self.rule, Rule):
            raise InvalidNodeError(f"node {self.id!r}: rule must be a Rule")
        if not isinstance(self.params, self.rule.params):
            raise InvalidNodeError(
                f"node {self.id!r}: rule {self.rule.name!r} expects "
                f"{self.rule.params.__name__}, got {type(self.params).__name__}"
            )
        given = dict(self.inputs)
        if set(given) != set(self.rule.inputs):
            raise InvalidNodeError(
                f"node {self.id!r}: rule {self.rule.name!r} inputs are "
                f"{list(self.rule.inputs)}, node gives {sorted(given)}"
            )
        for name, dep in given.items():
            if dep is not None and not isinstance(dep, (Node, ArtifactRef)):
                raise InvalidNodeError(
                    f"node {self.id!r}: input {name!r} must be a Node, an ArtifactRef or None"
                )
        self.inputs = {k: given[k] for k in sorted(given)}
        if self.kind not in _KINDS:
            raise InvalidNodeError(f"node {self.id!r}: kind must be one of {_KINDS}")
        if self.lane is not None and (self.kind == "cpu" or not self.lane):
            raise InvalidNodeError(f"node {self.id!r}: a lane needs a thread kind and a name")
        if self.ram_mb is None:
            self.ram_mb = self.rule.ram_mb
        elif self.ram_mb < 0:
            raise InvalidNodeError(f"node {self.id!r}: ram_mb must be >= 0")

    def node_inputs(self) -> list[Node]:
        """The inputs that are nodes (edges of the DAG), in input-name order."""
        return [dep for dep in self.inputs.values() if isinstance(dep, Node)]

    def __repr__(self) -> str:
        return f"Node({self.id!r}, {self.rule.name}@{self.rule.version}, {self.kind})"
