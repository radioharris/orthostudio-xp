"""P2a adversarial review, robustness lens: the scheduler under interruption.

No network, no Ortho4XP. The first three tests were ``xfail(strict=True)`` findings of the review
(``docs/specs/scheduler.md`` 2.6, ``pipeline-build.md`` 4); they are green since the P2a
correction round (BaseException cancels the run, a second cancel abandons it at once, the
twins of a KeyMismatch node are skipped with a cause).
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orthostudio.graph import Rule, RuleParams, Store
from orthostudio.graph.store import _pid_alive
from orthostudio.model import ArtifactRef
from orthostudio.sched import Event, Failed, Node, NodeContext, Scheduler, Started

# -- fake rules (module level) ---------------------------------------------------------------------


class P(RuleParams):
    seconds: float = 0.05
    tag: str = ""


def _p0(ctx) -> None:
    ctx.out.write_bytes(b"x")


SRC = Rule(name="rev.src", version=1, fn=_p0, params=P, inputs=(), ram_mb=0)


def run_polling(ctx: NodeContext) -> ArtifactRef:
    """Cooperative: polls the cancel token every 10 ms."""
    params = ctx.params
    assert isinstance(params, P)
    end = time.perf_counter() + params.seconds
    while time.perf_counter() < end:
        ctx.check_cancelled()
        time.sleep(0.01)
    return ctx.produce(lambda out: out.write_bytes(b"ok"))


def run_blocking(ctx: NodeContext) -> ArtifactRef:
    """Non-cooperative: a node that never looks at the cancel token (a stuck subprocess)."""
    params = ctx.params
    assert isinstance(params, P)
    time.sleep(params.seconds)
    return ctx.produce(lambda out: out.write_bytes(b"late"))


def run_wrong_key(ctx: NodeContext) -> ArtifactRef:
    ref = ctx.produce(lambda out: out.write_bytes(b"z"))
    return ArtifactRef("0" * 64, ref.digest, ref.path, ref.rule, ref.kind, ref.size)


def run_child_process(ctx: NodeContext) -> ArtifactRef:
    """What a subprocess node must do: own a child and kill it when the token is set."""
    pid_file = ctx.workdir / "child.pid"
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    pid_file.write_text(str(proc.pid))
    try:
        while proc.poll() is None:
            if ctx.cancelled:
                if os.name == "nt":  # no process groups to signal
                    proc.terminate()
                else:
                    os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=3)
                ctx.check_cancelled()
            time.sleep(0.02)
    finally:
        if proc.poll() is None:
            proc.kill()
    return ctx.produce(lambda out: out.write_bytes(b"child done"))


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "store", fsync=False)


def _node(node_id: str, *, kind: str = "cpu", run=run_polling, **params) -> Node:
    return Node(node_id, SRC, P(**params), {}, kind=kind, run=run)  # type: ignore[arg-type]


# -- findings of the review, fixed --------------------------------------------------------------


def test_keyboard_interrupt_in_loop_cancels_running_nodes(store: Store) -> None:
    sched = Scheduler(store, cpu_in_threads=True, cpu_workers=2, cancel_grace_s=5.0)
    sched.add(_node("a", seconds=3.0))
    sched.add(_node("b", seconds=3.0))

    def on_event(event) -> None:
        if isinstance(event, Started) and event.node_id == "b":
            raise KeyboardInterrupt

    t0 = time.perf_counter()
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(sched.run(["a", "b"], on_event=on_event))
    elapsed = time.perf_counter() - t0
    assert sched.cancelled, "running nodes were never asked to stop"
    assert elapsed < 1.5, f"run() waited {elapsed:.1f} s for nodes it should have cancelled"


def test_second_cancel_abandons_the_run_promptly(store: Store) -> None:
    sched = Scheduler(
        store, cpu_in_threads=True, cpu_workers=1, subprocess_slots=2, cancel_grace_s=1.0
    )
    for i in range(2):
        sched.add(_node(f"stuck{i}", kind="subprocess", run=run_blocking, seconds=2.5))

    async def main() -> float:
        task = asyncio.ensure_future(sched.run(["stuck0", "stuck1"]))
        await asyncio.sleep(0.2)
        sched.cancel()  # first Ctrl-C: cooperative
        await asyncio.sleep(0.2)
        t0 = time.perf_counter()
        task.cancel()  # second Ctrl-C: abandon
        with pytest.raises(asyncio.CancelledError):
            await task
        return time.perf_counter() - t0

    elapsed = asyncio.run(main())
    assert elapsed < 0.6, f"the second cancel took {elapsed:.2f} s (one more grace period)"


def test_wrong_key_twin_fails_with_a_cause(store: Store) -> None:
    sched = Scheduler(store, cpu_in_threads=True, cpu_workers=2)
    first = _node("t1", run=run_wrong_key, seconds=0.2, tag="twin")
    second = _node("t2", run=run_wrong_key, seconds=0.2, tag="twin")  # same key as t1
    sched.add(first)
    sched.add(second)
    events: list[Event] = []
    asyncio.run(sched.run(["t1", "t2"], on_event=events.append))
    failed = {e.node_id: e for e in events if isinstance(e, Failed)}
    assert set(failed) == {"t1", "t2"}
    # The two share a key, so one runs and meets the fault and the other waits on it and is
    # failed with it as the cause. Which of the two runs is the scheduler's business and changes
    # with the load, and so does whether the second was admitted before the first had finished:
    # if it was not, there was nothing in flight to wait on and it meets the same fault on its
    # own. Naming t1 as the one that runs made this fail twice in loaded runs (2026-09-23).
    met = [n for n, e in failed.items() if e.error.context.get("type") == "KeyMismatch"]
    assert met, {n: e.error.to_dict() for n, e in failed.items()}
    for name, event in failed.items():
        if name in met:
            assert event.cause is None, event.error.to_dict()
        else:
            assert event.cause in met, event.error.to_dict()


# -- checks that pass (what the review confirmed) -----------------------------------------------


def test_cancel_reaches_a_subprocess_node_and_its_child_dies(store: Store, tmp_path: Path) -> None:
    """The cooperative token is handed to subprocess nodes; a node that honours it (as the mesh
    rule does with its Triangle4XP child) kills its child within the grace period."""
    sched = Scheduler(
        store, cpu_in_threads=True, cpu_workers=1, subprocess_slots=1, cancel_grace_s=5.0,
        workdir=tmp_path / "work",
    )  # fmt: skip
    sched.add(_node("child", kind="subprocess", run=run_child_process))

    async def main() -> float:
        task = asyncio.ensure_future(sched.run(["child"]))
        await asyncio.sleep(0.5)
        t0 = time.perf_counter()
        sched.cancel()
        await task
        return time.perf_counter() - t0

    elapsed = asyncio.run(main())
    assert elapsed < 2.0
    assert sched.failed["child"].code == "SYS_CANCELLED"
    pid_files = list((tmp_path / "work").rglob("child.pid"))
    assert pid_files, "the failed node's workdir must be kept for inspection (spec 2.6)"
    pid = int(pid_files[0].read_text())
    deadline = time.perf_counter() + 3.0
    while time.perf_counter() < deadline and _pid_alive(pid):  # os.kill(pid, 0) kills on Windows
        time.sleep(0.05)
    assert not _pid_alive(pid), f"child {pid} survived the cancellation"


def test_cancelled_run_leaves_the_store_consistent_and_a_rerun_completes(store: Store) -> None:
    """Nothing partial is indexed by an interrupted run; the rerun builds what is missing."""
    sched = Scheduler(store, cpu_in_threads=True, cpu_workers=1, cancel_grace_s=2.0)
    sched.add(_node("fast", seconds=0.05, tag="fast"))
    sched.add(_node("slow", seconds=3.0, tag="slow"))

    async def main() -> None:
        task = asyncio.ensure_future(sched.run(["fast", "slow"]))
        await asyncio.sleep(0.4)
        sched.cancel()
        await task

    asyncio.run(main())
    assert sched.failed["slow"].code == "SYS_CANCELLED"
    assert not store.fsck().tmp_leftovers, "a cancelled build must abort its tmp dir"
    rerun = Scheduler(store, cpu_in_threads=True, cpu_workers=1)
    rerun.add(_node("fast", seconds=0.05, tag="fast"))
    rerun.add(_node("slow", seconds=3.0, tag="slow"))
    events: list[Event] = []
    refs = asyncio.run(rerun.run(["fast", "slow"], on_event=events.append))
    assert set(refs) == {"fast", "slow"}
    hits = {e.node_id: e.hit for e in events if type(e).__name__ == "Done"}
    assert hits == {"fast": True, "slow": False}


def test_stall_detector_never_hangs_when_the_ram_budget_cannot_be_met(store: Store) -> None:
    """A lone node larger than the budget is admitted when nothing runs (spec 2.3)."""
    sched = Scheduler(store, cpu_in_threads=True, cpu_workers=2, ram_budget_mb=100)
    a = Node("big-a", SRC, P(seconds=0.05, tag="a"), {}, kind="cpu", ram_mb=5000, run=run_polling)
    b = Node("big-b", SRC, P(seconds=0.05, tag="b"), {}, kind="cpu", ram_mb=5000, run=run_polling)
    sched.add(a)
    sched.add(b)
    t0 = time.perf_counter()
    refs = asyncio.run(sched.run(["big-a", "big-b"]))
    assert set(refs) == {"big-a", "big-b"}
    assert time.perf_counter() - t0 < 2.0
