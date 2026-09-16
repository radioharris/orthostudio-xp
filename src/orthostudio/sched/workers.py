"""Execution of one node outside the event loop: in a worker thread or a worker process.

Both paths build a :class:`NodeContext`, call the node's ``run`` (or the P0 rule through
``run_p0_rule``) and return an :class:`Outcome` that never raises: an ``OsxpError`` travels
as its ``to_dict()`` (the class is not picklable) and is rebuilt on the parent side.
"""

from __future__ import annotations

import contextlib
import logging
import signal
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orthostudio.errors import OsxpError, wrap
from orthostudio.graph.rule import Rule, RuleParams
from orthostudio.graph.store import Store
from orthostudio.model import ArtifactRef
from orthostudio.sched.node import CancelToken, NodeContext, NodeRun, run_p0_rule

__all__ = ["CpuJob", "Outcome", "cpu_entry", "cpu_init", "error_from_dict", "execute"]

log = logging.getLogger("orthostudio.sched.workers")


@dataclass(slots=True)
class Outcome:
    """What comes back from a node run (picklable)."""

    ref: ArtifactRef | None
    error: dict[str, Any] | None
    wall_s: float
    traceback: str = ""


@dataclass(slots=True)
class CpuJob:
    """Everything a worker process needs to run one node (picklable)."""

    node_id: str
    rule: Rule
    params: RuleParams
    key: str
    recipe: str
    inputs: dict[str, ArtifactRef | None]
    store_root: Path
    fsync: bool
    workdir: Path
    run: NodeRun | None


def error_from_dict(doc: Mapping[str, Any]) -> OsxpError:
    """Rebuild an ``OsxpError`` from ``to_dict()`` (cause kept as text in the context)."""
    context = dict(doc.get("context") or {})
    if doc.get("cause"):
        context.setdefault("cause", doc["cause"])
    return OsxpError(
        str(doc["code"]),
        context=context,
        message=doc.get("message"),
        remedy=doc.get("remedy"),
        severity=doc.get("severity"),
        action=doc.get("action"),
    )


def execute(
    *,
    node_id: str,
    rule: Rule,
    params: RuleParams,
    key: str,
    recipe: str,
    inputs: Mapping[str, ArtifactRef | None],
    store: Store,
    workdir: Path,
    progress: Callable[[float, str], None],
    cancel_event: CancelToken,
    run: NodeRun | None,
) -> Outcome:
    """Run one node to completion; never raises."""
    t0 = time.perf_counter()
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        ctx = NodeContext(
            node_id=node_id,
            rule=rule,
            params=params,
            key=key,
            recipe=recipe,
            inputs=dict(inputs),
            store=store,
            workdir=workdir,
            progress=progress,
            cancel_event=cancel_event,
        )
        fn = run_p0_rule if run is None else run
        ref = fn(ctx)
        if not isinstance(ref, ArtifactRef):
            raise OsxpError(
                "SYS_INTERNAL_ERROR",
                context={
                    "type": "TypeError",
                    "detail": f"node {node_id!r} returned {type(ref).__name__}, not ArtifactRef",
                },
            )
        return Outcome(ref, None, time.perf_counter() - t0)
    except Exception as exc:
        return Outcome(None, wrap(exc).to_dict(), time.perf_counter() - t0, traceback.format_exc())


# -- worker process side ---------------------------------------------------------------------

_CANCEL: CancelToken | None = None
_PROGRESS: Any = None
_STORE: Store | None = None


def cpu_init(cancel_event: CancelToken, progress_queue: Any) -> None:
    """``ProcessPoolExecutor`` initializer: keep the shared primitives, ignore Ctrl-C."""
    global _CANCEL, _PROGRESS
    _CANCEL, _PROGRESS = cancel_event, progress_queue
    with contextlib.suppress(Exception):
        signal.signal(signal.SIGINT, signal.SIG_IGN)


def _worker_store(root: Path, fsync: bool) -> Store:
    global _STORE
    resolved = Path(root).expanduser().resolve()
    if _STORE is None or _STORE.root != resolved:
        if _STORE is not None:
            _STORE.close()
        _STORE = Store(resolved, fsync=fsync)
    return _STORE


class _NeverCancelled:
    def is_set(self) -> bool:
        return False

    def wait(self, timeout: float | None = None) -> bool:
        if timeout is not None:
            time.sleep(timeout)
        return False


def cpu_entry(job: CpuJob) -> Outcome:
    """Entry point of a ``cpu`` node in a worker process."""
    queue = _PROGRESS
    node_id = job.node_id

    def progress(fraction: float, message: str) -> None:
        if queue is not None:
            with contextlib.suppress(Exception):
                queue.put((node_id, float(fraction), str(message)))

    try:
        store = _worker_store(job.store_root, job.fsync)
    except Exception as exc:
        return Outcome(None, wrap(exc).to_dict(), 0.0, traceback.format_exc())
    return execute(
        node_id=node_id,
        rule=job.rule,
        params=job.params,
        key=job.key,
        recipe=job.recipe,
        inputs=job.inputs,
        store=store,
        workdir=job.workdir,
        progress=progress,
        cancel_event=_CANCEL if _CANCEL is not None else _NeverCancelled(),
        run=job.run,
    )
