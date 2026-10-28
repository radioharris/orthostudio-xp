# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""One P0 rule run in a process started for it alone (spec ``docs/specs/scheduler.md`` 2.3).

The tracing (``orthostudio.vectors``) ran in a thread of the engine's own process, and two things
came of it. What it took of the memory stayed with the engine: a user's row
of 23 dense tiles ran out of 64 GB, and went through once the engine was started again (2026-10).
And its Python work held the interpreter the downloads run in: images came at 60 % of the line
while tracings ran (25 tiles at the service roads, 0.3 MB/s, 2026-10-27). In a process of its own,
all its memory goes back to the system when it ends, and the downloads keep the interpreter.

The engine keeps everything else: it opens the build (``NodeContext.begin``), commits and counts
it. The process only runs the rule's function on the paths it is given, and tells the engine
through a :class:`ProcessLink` what the node's context would have been told: its progress, the
slot it lends while it waits, the bytes it wrote and received; it logs into the engine's log.
Cancelling asks the process to stop (``ProcessLink.cancel``) and ends it after
:data:`CANCEL_GRACE_S`; what it wrote stays in the build directory, which the failed build removes.
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import queue
import signal
import time
import traceback
from collections.abc import Callable
from contextlib import AbstractContextManager
from multiprocessing import get_context
from typing import Any

from orthostudio.errors import OsxpError, wrap
from orthostudio.graph.rule import RunContext
from orthostudio.logs import ROOT_LOGGER
from orthostudio.model import ArtifactRef
from orthostudio.parentwatch import leave_with_parent
from orthostudio.sched.node import NodeContext, run_context
from orthostudio.sched.workers import error_from_dict

__all__ = ["Bind", "ProcessLink", "run_p0_rule_apart"]

log = logging.getLogger("orthostudio.sched.apart")


class ProcessLink:
    """The process's side of the node's context: what it says goes to the engine's
    ``NodeContext`` (``progress``, ``set_idle``, ``wrote``, ``fetched``), and ``cancel`` is set
    when the engine asks it to stop."""

    def __init__(self, messages: Any, cancel: Any) -> None:
        self._messages = messages
        self.cancel = cancel

    def _say(self, *item: object) -> None:
        with contextlib.suppress(Exception):
            self._messages.put(item)

    def progress(self, fraction: float, message: str) -> None:
        self._say("progress", float(fraction), str(message))

    def idle(self, idle: bool) -> None:
        self._say("idle", bool(idle))

    def wrote(self, nbytes: int) -> None:
        self._say("wrote", int(nbytes))

    def fetched(self, nbytes: int) -> None:
        self._say("fetched", int(nbytes))


Bind = Callable[[ProcessLink], AbstractContextManager[object]]
"""Entered in the process around the rule's function, given its link to the engine: what the rule
reads from its context there (``vectors_job``, the build environment of the textures). Pickled:
a module-level callable, or a ``functools.partial`` of one over picklable values."""

POLL_S = 0.2
"""How often the engine looks at the cancellation while it waits for the process."""

EXIT_GRACE_S = 10.0
"""How long a process that said it was done may take to end before it is ended."""

CANCEL_GRACE_S = 3.0
"""How long a process asked to stop may take to stop by itself before it is ended."""


class _ProcessTracebackError(Exception):
    """The process's own traceback, chained under the error it raised (as ``concurrent.futures``
    does): the engine's log keeps where the rule failed, not only where the engine heard it."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text

    def __str__(self) -> str:
        return self.text


def run_p0_rule_apart(ctx: NodeContext, bind: Bind | None = None) -> ArtifactRef:
    """``run_p0_rule``, with the rule's function run in a process started for it alone."""
    with ctx.begin() as build:
        _run_in_process(ctx, run_context(ctx, build), bind)
        return ctx.commit(build)


def _run_in_process(ctx: NodeContext, run_ctx: RunContext, bind: Bind | None) -> None:
    mp = get_context("spawn")
    messages: Any = mp.Queue()
    cancel = mp.Event()
    level = logging.getLogger(ROOT_LOGGER).getEffectiveLevel()
    # not a daemon: a rule may start processes of its own (the textures' encoding pool); the
    # engine ends it below whatever happens, and it leaves by itself if the engine is gone
    proc = mp.Process(
        target=_main,
        args=(run_ctx, bind, messages, cancel, level),
        name=f"osxp-{ctx.node_id}",
    )
    proc.start()
    end: tuple[Any, ...] | None = None
    try:
        end = _wait(ctx, proc, messages, cancel)
    finally:
        if end is not None:  # it said its last word and is ending by itself
            proc.join(EXIT_GRACE_S)
        if proc.is_alive():  # cancelled, or the engine failed while it waited
            proc.terminate()
        proc.join()
        with contextlib.suppress(Exception):
            messages.close()
    if end[0] == "done":
        return
    if ctx.cancel_event.is_set():
        raise OsxpError("SYS_CANCELLED", context={"node": ctx.node_id})
    if end[0] == "failed":
        _, doc, text = end
        raise error_from_dict(doc) from _ProcessTracebackError(text)
    raise _ended_error(ctx, proc.exitcode)


def _wait(ctx: NodeContext, proc: Any, messages: Any, cancel: Any) -> tuple[Any, ...]:
    """Relay what the process says until it says it is done or failed, or ends without a word;
    a cancel is passed on, and given :data:`CANCEL_GRACE_S` to be obeyed."""
    stop_by: float | None = None
    while True:
        if stop_by is None and ctx.cancel_event.is_set():
            cancel.set()
            stop_by = time.monotonic() + CANCEL_GRACE_S
        if stop_by is not None and time.monotonic() >= stop_by:
            raise OsxpError("SYS_CANCELLED", context={"node": ctx.node_id})
        # read before the queue: what a process said before it ended is in the queue by then
        alive = proc.is_alive()
        try:
            item = messages.get(timeout=POLL_S)
        except queue.Empty:
            if alive:
                continue
            return ("ended",)
        if isinstance(item, logging.LogRecord):
            logging.getLogger(item.name).handle(item)
        elif item[0] == "progress":
            ctx.progress(item[1], item[2])
        elif item[0] == "idle":
            ctx.set_idle(item[1])
        elif item[0] == "wrote":
            ctx.wrote(item[1])
        elif item[0] == "fetched":
            ctx.fetched(item[1])
        else:
            return tuple(item)


def _ended_error(ctx: NodeContext, exitcode: int | None) -> OsxpError:
    """A process that ended without a word: killed by the system for its memory, or a crash."""
    killed = getattr(signal, "SIGKILL", None)
    if killed is not None and exitcode == -killed:
        return _out_of_memory(ctx)
    return OsxpError(
        "SYS_INTERNAL_ERROR",
        context={"type": "ProcessEnded", "detail": f"{ctx.node_id}: exit code {exitcode}"},
    )


def _out_of_memory(ctx: NodeContext | RunContext) -> OsxpError:
    return OsxpError(
        "SYS_OUT_OF_MEMORY",
        context={
            "stage": ctx.rule.name.rsplit(".", 1)[-1],
            "tile": str(getattr(ctx.params, "tile", "")),
        },
    )


# -- the process's side --------------------------------------------------------------------------


def _main(run_ctx: RunContext, bind: Bind | None, messages: Any, cancel: Any, level: int) -> None:
    """The process: run the rule's function, then say ``done`` or ``failed``."""
    with contextlib.suppress(Exception):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    leave_with_parent()
    # every record at the engine's level goes to the engine, which writes it where it writes its own
    root = logging.getLogger()
    root.handlers[:] = [logging.handlers.QueueHandler(messages)]
    root.setLevel(level)

    link = ProcessLink(messages, cancel)
    try:
        with bind(link) if bind is not None else contextlib.nullcontext():
            run_ctx.rule.fn(run_ctx)
    except MemoryError:
        messages.put(("failed", _out_of_memory(run_ctx).to_dict(), traceback.format_exc()))
    except BaseException as exc:
        messages.put(("failed", wrap(exc).to_dict(), traceback.format_exc()))
    else:
        messages.put(("done",))
    finally:
        messages.close()
        messages.join_thread()
