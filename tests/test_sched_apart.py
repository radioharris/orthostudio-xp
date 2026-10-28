# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A P0 rule run in a process of its own (``orthostudio.sched.apart``, scheduler spec 2.3)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import os
import pickle
import signal
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.graph import Rule, RuleParams, RunContext, Store
from orthostudio.model import ArtifactRef
from orthostudio.sched import Done, Event, Failed, Node, NodeContext, Progress, Scheduler
from orthostudio.sched.apart import ProcessLink, run_p0_rule_apart

# --- a fake rule (module level: the process finds it by reference) ------------------------------


class ApartParams(RuleParams):
    mode: str = "write"
    tile: str = "+45+005"
    pid_file: str = ""
    saved_file: str = ""


_LINK: ProcessLink | None = None


@contextlib.contextmanager
def _bind(link: ProcessLink) -> Iterator[None]:
    global _LINK
    _LINK = link
    try:
        yield
    finally:
        _LINK = None


def _apart_fn(ctx: RunContext) -> None:
    params = ctx.params
    assert isinstance(params, ApartParams)
    if params.pid_file:
        Path(params.pid_file).write_text(str(os.getpid()))
    if _LINK is not None:
        _LINK.progress(0.5, "half way")
        _LINK.idle(True)
        _LINK.idle(False)
        _LINK.wrote(1000)
        _LINK.fetched(2000)
    logging.getLogger("orthostudio.test_apart").warning("traced in %d", os.getpid())
    if params.mode == "fail":
        raise OsxpError("MESH_TRIANGULATION_FAILED", context={"tile": params.tile, "reason": "x"})
    if params.mode == "crash":
        raise ValueError("a bug in the tracing")
    if params.mode == "memory":
        raise MemoryError
    if params.mode == "exit":
        os._exit(3)
    if params.mode == "kill":
        os.kill(os.getpid(), signal.SIGKILL)
    if params.mode == "sleep":
        time.sleep(60)
    if params.mode == "obey":  # stops by itself once asked
        while _LINK is not None and not _LINK.cancel.is_set():
            time.sleep(0.05)
        raise OsxpError("SYS_CANCELLED", context={"node": "t/vectors"})
    if params.mode == "save":  # once asked to stop, saves what it has for 4 s, as images do
        while _LINK is not None and not _LINK.cancel.is_set():
            time.sleep(0.05)
        time.sleep(4.0)
        Path(params.saved_file).write_text("kept")
        raise OsxpError("SYS_CANCELLED", context={"node": "t/textures"})
    ctx.out.write_text(f"pid={os.getpid()}")


APART = Rule(name="fake.apart", version=1, fn=_apart_fn, params=ApartParams, kind="file")


def run_apart(ctx: NodeContext) -> ArtifactRef:
    return run_p0_rule_apart(ctx, _bind)


# --- helpers ------------------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "store", fsync=False)


def _run(store: Store, **params: str) -> tuple[Scheduler, dict[str, ArtifactRef], list[Event]]:
    sched = Scheduler(store, cpu_in_threads=True, subprocess_slots=1)
    sched.add(Node("t/vectors", APART, ApartParams(**params), {}, kind="subprocess", run=run_apart))
    events: list[Event] = []
    refs = asyncio.run(sched.run(["t/vectors"], on_event=events.append))
    return sched, refs, events


def _failure(events: list[Event]) -> OsxpError:
    failed = [e for e in events if isinstance(e, Failed)]
    assert len(failed) == 1, events
    return failed[0].error


def _apart_children() -> list[multiprocessing.process.BaseProcess]:
    return [p for p in multiprocessing.active_children() if p.name.startswith("osxp-")]


# --- tests --------------------------------------------------------------------------------------


def test_the_rule_runs_in_another_process_and_the_engine_commits(store: Store) -> None:
    _, refs, events = _run(store)
    ref = refs["t/vectors"]
    text = ref.path.read_text()
    assert text.startswith("pid=") and int(text[4:]) != os.getpid()
    assert store.has(ref.key)
    assert any(isinstance(e, Done) and not e.hit for e in events)
    assert not _apart_children()


def test_its_progress_reaches_the_node(store: Store) -> None:
    _, _, events = _run(store)
    said = [e.message for e in events if isinstance(e, Progress)]
    assert "half way" in said


def test_its_log_lines_reach_the_engine_log(store: Store) -> None:
    seen: list[logging.LogRecord] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    logger = logging.getLogger("orthostudio.test_apart")
    handler = Keep(logging.DEBUG)
    logger.addHandler(handler)
    try:
        _run(store)
    finally:
        logger.removeHandler(handler)
    lines = [r.getMessage() for r in seen]
    ours = f"traced in {os.getpid()}"
    assert any(line.startswith("traced in ") and line != ours for line in lines)


def test_an_error_of_the_rule_comes_back_with_its_code(store: Store) -> None:
    sched, refs, events = _run(store, mode="fail")
    assert not refs and sched.failed
    assert _failure(events).code == "MESH_TRIANGULATION_FAILED"
    assert not _apart_children()


def test_a_bug_comes_back_with_the_traceback_of_the_process(store: Store) -> None:
    _, _, events = _run(store, mode="crash")
    error = _failure(events)
    assert error.code == "SYS_INTERNAL_ERROR"
    assert error.context["type"] == "ValueError"
    # where it failed in the process, not only where the engine heard of it
    assert "_apart_fn" in str(error.context.get("traceback", ""))


def test_memory_running_out_in_the_process_says_so(store: Store) -> None:
    _, _, events = _run(store, mode="memory")
    error = _failure(events)
    assert error.code == "SYS_OUT_OF_MEMORY"
    assert error.context["stage"] == "apart" and error.context["tile"] == "+45+005"


@pytest.mark.skipif(sys.platform == "win32", reason="no SIGKILL on Windows")
def test_a_process_the_system_kills_reads_as_memory_running_out(store: Store) -> None:
    _, _, events = _run(store, mode="kill")
    assert _failure(events).code == "SYS_OUT_OF_MEMORY"
    assert not _apart_children()


def test_a_process_that_ends_without_a_word_is_an_internal_error(store: Store) -> None:
    _, _, events = _run(store, mode="exit")
    error = _failure(events)
    assert error.code == "SYS_INTERNAL_ERROR" and "exit code 3" in error.context["detail"]


def test_cancelling_ends_the_process_at_once(store: Store, tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    sched = Scheduler(store, cpu_in_threads=True, subprocess_slots=1)
    params = ApartParams(mode="sleep", pid_file=str(pid_file))
    sched.add(Node("t/vectors", APART, params, {}, kind="subprocess", run=run_apart))
    events: list[Event] = []

    def cancel_once_started() -> None:
        end = time.monotonic() + 60
        while not pid_file.exists() and time.monotonic() < end:
            time.sleep(0.05)
        sched.cancel()

    canceller = threading.Thread(target=cancel_once_started)
    canceller.start()
    t0 = time.monotonic()
    refs = asyncio.run(sched.run(["t/vectors"], on_event=events.append))
    canceller.join()
    assert pid_file.exists() and not refs
    assert time.monotonic() - t0 < 50  # the process slept 60 s: it was ended, not waited for
    assert not _apart_children()


def test_the_tracing_rules_go_to_their_process_by_reference() -> None:
    from orthostudio.vectors.rule import VECTORS, VECTORS_ANGULAR, apart_job

    for rule in (VECTORS, VECTORS_ANGULAR):
        again = pickle.loads(pickle.dumps(rule))
        assert again == rule and again.fn is rule.fn
    assert pickle.loads(pickle.dumps(apart_job)) is apart_job


def test_what_it_tells_the_node_reaches_the_engines_context(store: Store) -> None:
    """The textures lend their slot while they wait and count the bytes for Works' report."""
    _, _, events = _run(store)
    done = [e for e in events if isinstance(e, Done)]
    assert done and done[0].downloaded == 2000
    assert done[0].written >= 1000  # what it said it wrote, and what the engine committed


def _cancel_once_started(sched: Scheduler, pid_file: Path) -> threading.Thread:
    def cancel() -> None:
        end = time.monotonic() + 60
        while not pid_file.exists() and time.monotonic() < end:
            time.sleep(0.05)
        sched.cancel()

    thread = threading.Thread(target=cancel)
    thread.start()
    return thread


def test_a_process_asked_to_stop_stops_by_itself(store: Store, tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    sched = Scheduler(store, cpu_in_threads=True, subprocess_slots=1)
    params = ApartParams(mode="obey", pid_file=str(pid_file))
    sched.add(Node("t/vectors", APART, params, {}, kind="subprocess", run=run_apart))
    canceller = _cancel_once_started(sched, pid_file)
    t0 = time.monotonic()
    refs = asyncio.run(sched.run(["t/vectors"]))
    canceller.join()
    assert not refs and time.monotonic() - t0 < 20
    assert not _apart_children()


@pytest.mark.parametrize("grace", [None, 30.0])
def test_a_process_given_more_time_finishes_its_save(
    store: Store, tmp_path: Path, grace: float | None
) -> None:
    """The images save the pieces they received once cancelled, which the next build does not ask
    again: given their own time they finish it; a tracing, with nothing to keep, is ended after
    ``CANCEL_GRACE_S``."""
    pid_file, saved = tmp_path / "pid", tmp_path / "saved"

    def run(ctx: NodeContext) -> ArtifactRef:
        if grace is None:
            return run_p0_rule_apart(ctx, _bind)
        return run_p0_rule_apart(ctx, _bind, cancel_grace_s=grace)

    sched = Scheduler(store, cpu_in_threads=True, subprocess_slots=1)
    params = ApartParams(mode="save", pid_file=str(pid_file), saved_file=str(saved))
    sched.add(Node("t/textures", APART, params, {}, kind="subprocess", run=run))
    canceller = _cancel_once_started(sched, pid_file)
    refs = asyncio.run(sched.run(["t/textures"]))
    canceller.join()
    assert not refs and not _apart_children()
    assert saved.exists() == (grace is not None)
