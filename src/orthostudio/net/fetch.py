"""Bounded, self-adjusting HTTP fetcher on top of curl_cffi (libcurl multi, HTTP/2).

Rules and their justification: ``docs/specs/net-download.md`` (R1-R7). In short:

- one ``AsyncSession`` for the life of the ``Fetcher`` (connections kept alive);
- per ``host_group`` AIMD window: +1 per round of successes up to ``max_in_flight``, halved on
  429/503, on a refused connection or on a latency spike, x0.75 on a timeout, paused on 429
  (``Retry-After`` obeyed);
- an optional ``req_per_s`` ceiling per host group, on top of the window: one request started
  every ``1 / req_per_s`` seconds, for a server that counts requests rather than connections;
- hedging: a transfer without an answer after ``hedge_after_s`` is doubled, the first good
  answer wins, the loser is cancelled;
- bounded attempts with a short exponential back-off for transport errors and 5xx; a 429
  obeyed with its pause does not cost an attempt but has its own bounded budget (count and
  total pause time); 4xx other than 429 are answers, not errors;
- cooperative cancellation through an ``asyncio.Event`` (returns within a second);
- sliding statistics delivered at most four times per second;
- per-run overrides of the in-flight ceiling and of the attempts, so that a caller can run a
  spaced, low-concurrency retry round on the same session, AIMD and pause state (spec R4);
- the transport error of a request given up is kept as text (``FetchResult.detail``), so that
  a failure reported to the user says what happened on the wire.

Nothing here knows what a tile or a provider is.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import functools
import heapq
import random
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Literal

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException, Timeout

from orthostudio import __version__
from orthostudio.net.certs import ca_bundle

__all__ = ["USER_AGENT", "FetchRequest", "FetchResult", "FetchStats", "Fetcher", "fetch_all"]

# --- public data types --------------------------------------------------------------------------


@dataclass(slots=True)
class FetchRequest:
    """One GET to perform. ``host_group`` is the concurrency key (typically the provider code)."""

    key: Any
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    host_group: str = ""


@dataclass(slots=True)
class FetchResult:
    """Outcome of one request.

    ``status`` is 0 when no HTTP answer was ever received. ``error`` is ``None`` for any answer
    this module does not retry (2xx, 3xx, 4xx other than 429), else an ``orthostudio.errors`` code:
    ``NET_TIMEOUT``, ``NET_CONNECTION_FAILED``, ``NET_RATE_LIMITED``, ``NET_SERVER_ERROR``,
    ``SYS_CANCELLED``. Header names are lower-cased. ``detail`` is libcurl's error line of the
    last transport failure (``curl: (28) Operation timed out after 20001 milliseconds with 0
    bytes received``), empty when the kept outcome is an HTTP answer.
    """

    key: Any
    status: int
    body: bytes
    headers: dict[str, str]
    elapsed: float
    attempts: int
    hedged: bool
    error: str | None
    detail: str = ""


@dataclass(slots=True, frozen=True)
class FetchStats:
    """Snapshot of a ``fetch_many`` run (see spec R6)."""

    done: int
    total: int
    bytes: int
    in_flight: int
    req_per_s: float
    errors: int
    retries: int
    hedges: int
    throttled: bool
    """Our own window was lowered recently, or a group is paused: the fetcher is holding back.

    This is mostly **us**: the window is lowered on a latency spike, which happens all the time
    while a healthy download hunts for its right size. It is a developer's figure and says
    nothing about the server."""
    pushed_back: bool = False
    """A server answered 429 and we are waiting out the delay it asked for. This one is the
    server, and it is the only one worth telling a user about (found on a user's own screen,
    2026-09-24: "the source is asking us to slow down" beside 1 269 requests a second)."""


# --- constants (spec R2-R4, R6) ---------------------------------------------------------------

MIN_IN_FLIGHT = 8
LATENCY_RING = 1000
LATENCY_RECENT = 100
LATENCY_CHECK_EVERY = 25
LATENCY_FLOOR_S = 0.2
LATENCY_RATIO = 2.0
TIMEOUT_DECREASE = 0.75
PAUSE_BASE_S = 5.0
PAUSE_MAX_S = 30.0
PAUSE_REPEAT_WINDOW_S = 60.0
RETRY_AFTER_MIN_S = 1.0
RETRY_AFTER_MAX_S = 120.0
MAX_PUSHBACKS = 12
PUSHBACK_BUDGET_S = 120.0
BACKOFF_BASE_S = 0.5
BACKOFF_MAX_S = 4.0
BACKOFF_JITTER = 0.25
STATS_PERIOD_S = 0.25
RATE_WINDOW_S = 5.0
THROTTLED_MEMORY_S = 10.0
CONNECT_TIMEOUT_MAX_S = 10.0
MAX_REDIRECTS = 5

USER_AGENT = f"OrthoStudio-XP/{__version__} (+https://github.com/radioharris/orthostudio-xp)"
"""Who is asking, on every request. Nothing was sent before, which is impolite towards services
that carry us for nothing and blind when one of them wants to know who is knocking: Overpass asks
a client to name itself, and the Brazilian water agency, which serves the ANADEM relief, answers
403 to a request without one (measured 2026-09-20). A caller may still override it per request."""
DETAIL_MAX_CHARS = 200

_Kind = Literal["ok", "pushback", "server", "timeout", "connect"]


@dataclass(slots=True)
class _Outcome:
    kind: _Kind
    status: int = 0
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    latency: float = 0.0
    detail: str = ""

    @property
    def error_code(self) -> str | None:
        return {
            "ok": None,
            "pushback": "NET_RATE_LIMITED",
            "server": "NET_SERVER_ERROR",
            "timeout": "NET_TIMEOUT",
            "connect": "NET_CONNECTION_FAILED",
        }[self.kind]


@dataclass(slots=True)
class _Pending:
    index: int
    request: FetchRequest
    started_at: float = 0.0
    attempts: int = 0
    hedged: bool = False
    last: _Outcome | None = None
    pushbacks: int = 0
    pushback_wait_s: float = 0.0

    @property
    def failures(self) -> int:
        """Transfers that count towards ``max_attempts`` (429 obeyed with a pause excluded)."""
        return self.attempts - self.pushbacks


class _Group:
    """AIMD state of one ``host_group`` (spec R2)."""

    __slots__ = (
        "completions_since_decrease",
        "delayed",
        "hedges_in_flight",
        "in_flight",
        "last_decrease_at",
        "last_pause_at",
        "latencies",
        "max_window",
        "min_window",
        "name",
        "next_pause_s",
        "next_start",
        "paused_until",
        "ready",
        "seq",
        "successes_since_change",
        "wake",
        "window",
    )

    def __init__(self, name: str, start: int, maximum: int) -> None:
        self.name = name
        self.window = start
        self.max_window = maximum
        self.min_window = min(MIN_IN_FLIGHT, start)
        self.in_flight = 0
        self.next_start = 0.0
        self.hedges_in_flight = 0
        self.paused_until = 0.0
        self.last_pause_at = -1e9
        self.next_pause_s = PAUSE_BASE_S
        self.last_decrease_at = -1e9
        self.latencies: deque[float] = deque(maxlen=LATENCY_RING)
        self.successes_since_change = 0
        self.completions_since_decrease = 1 << 30
        self.ready: deque[_Pending] = deque()
        self.delayed: list[tuple[float, int, bool, _Pending]] = []
        self.seq = 0
        self.wake = asyncio.Event()

    # -- signals ---------------------------------------------------------------------------------

    def record(self, outcome: _Outcome, now: float) -> None:
        self.completions_since_decrease += 1
        kind = outcome.kind
        if kind == "ok":
            self.latencies.append(outcome.latency)
            self.successes_since_change += 1
            if self.successes_since_change >= self.window and self.window < self.max_window:
                self.window += 1
                self.successes_since_change = 0
            since = self.completions_since_decrease
            # The recent ring must hold only post-decrease samples before a new check.
            if (
                since >= LATENCY_RECENT
                and since % LATENCY_CHECK_EVERY == 0
                and self._latency_spike()
            ):
                self._decrease(0.5, now)
        elif kind in ("pushback", "connect") or (kind == "server" and outcome.status == 503):
            # A server that defends itself by refusing the connection says no as plainly as one
            # answering 429, and said nothing to us before 0.1.14: the window stayed wide and we
            # kept knocking until the attempts ran out (a user, 2026-09-24). ``_decrease`` halves
            # once per round of completions, so a stray reset on a healthy line costs one round.
            self._decrease(0.5, now)
        elif kind == "timeout":
            self._decrease(TIMEOUT_DECREASE, now)
        # other 5xx: the URL's own answer, retried, window untouched

    def pause(self, retry_after: float | None, now: float) -> float:
        """Pause dispatch after a 429; returns the instant the pause ends."""
        if retry_after is not None:
            length = min(max(retry_after, RETRY_AFTER_MIN_S), RETRY_AFTER_MAX_S)
        else:
            if now - self.last_pause_at > PAUSE_REPEAT_WINDOW_S:
                self.next_pause_s = PAUSE_BASE_S
            length = self.next_pause_s
            self.next_pause_s = min(self.next_pause_s * 2, PAUSE_MAX_S)
        self.last_pause_at = now
        self.paused_until = max(self.paused_until, now + length)
        return self.paused_until

    def throttled(self, now: float) -> bool:
        return now < self.paused_until or now - self.last_decrease_at < THROTTLED_MEMORY_S

    def _latency_spike(self) -> bool:
        """The p90 of the last 100 answers above twice the p90 of every answer since the last
        decrease (at most 1 000), and at least :data:`LATENCY_FLOOR_S`: a tail that rises.

        The median of the ring was the reference until 2026-09-15, when a user's coastal tile at
        Esri Clarity fell to 40 requests/s: a piece of sea answers in 60 ms, a piece of land in
        400 ms, so a texture of both has a p90 far above its median without any congestion, and
        the window halved round after round down to 8. The ring is emptied at each decrease
        (:meth:`_decrease`): the ground of the answers before it (the sea) is no reference for
        the ones after it (the land)."""
        if len(self.latencies) < LATENCY_RECENT:
            return False
        answers = list(self.latencies)
        recent = sorted(answers[-LATENCY_RECENT:])
        p90 = recent[int(0.9 * (len(recent) - 1))]
        if p90 < LATENCY_FLOOR_S:
            return False
        answers.sort()
        return p90 > LATENCY_RATIO * answers[int(0.9 * (len(answers) - 1))]

    def _decrease(self, factor: float, now: float) -> None:
        if self.completions_since_decrease < self.window:
            return  # one decrease per round
        self.window = max(self.min_window, int(self.window * factor))
        self.successes_since_change = 0
        self.completions_since_decrease = 0
        self.last_decrease_at = now
        self.latencies.clear()  # the reference of the latency signal starts again

    # -- queue -----------------------------------------------------------------------------------

    def push_delayed(self, pending: _Pending, not_before: float, *, tail: bool = False) -> None:
        """Re-queue ``pending`` at ``not_before``; at the head of the queue (a retry keeps its
        place) or at the tail (a pushed-back request lets the others go first, so the same
        few requests do not absorb a whole 429 storm)."""
        self.seq += 1
        heapq.heappush(self.delayed, (not_before, self.seq, tail, pending))

    def promote_due(self, now: float) -> None:
        while self.delayed and self.delayed[0][0] <= now:
            _, _, tail, pending = heapq.heappop(self.delayed)
            if tail:
                self.ready.append(pending)
            else:
                self.ready.appendleft(pending)

    def idle(self) -> bool:
        return not self.ready and not self.delayed and self.in_flight == 0


# --- the fetcher --------------------------------------------------------------------------------


class Fetcher:
    """Downloads many small bodies with per-host-group AIMD concurrency (spec section 1)."""

    def __init__(
        self,
        *,
        max_in_flight: int = 128,
        start_in_flight: int = 64,
        hedge_after_s: float = 3.0,
        timeout_s: float = 20.0,
        max_attempts: int = 4,
        req_per_s: float | None = None,
        http2: bool = True,
        max_pushbacks: int = MAX_PUSHBACKS,
        pushback_budget_s: float = PUSHBACK_BUDGET_S,
    ) -> None:
        if max_in_flight < 1 or start_in_flight < 1:
            raise ValueError("in-flight limits must be >= 1")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if req_per_s is not None and req_per_s <= 0:
            raise ValueError("req_per_s must be > 0")
        if hedge_after_s <= 0 or timeout_s <= 0:
            raise ValueError("hedge_after_s and timeout_s must be > 0")
        if max_pushbacks < 0 or pushback_budget_s < 0:
            raise ValueError("pushback limits must be >= 0")
        self.max_in_flight = max_in_flight
        self.start_in_flight = min(start_in_flight, max_in_flight)
        self.hedge_after_s = hedge_after_s
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.req_per_s = req_per_s
        self._spacing = 0.0 if req_per_s is None else 1.0 / req_per_s
        self.max_pushbacks = max_pushbacks
        self.pushback_budget_s = pushback_budget_s
        self.http2 = http2
        self._session: AsyncSession | None = None
        self._session_loop: asyncio.AbstractEventLoop | None = None
        self._groups: dict[str, _Group] = {}
        self._busy = False
        self._cancelled = False
        self._keep_results = True
        self._run_limit: int | None = None
        self._run_attempts = max_attempts
        self._workers: set[asyncio.Task[None]] = set()
        self._completions: deque[float] = deque()
        self._run_started_at = 0.0
        self._done = 0
        self._total = 0
        self._bytes = 0
        self._transfers_in_flight = 0
        self._errors = 0
        self._retries = 0
        self._hedges = 0

    # -- lifecycle -------------------------------------------------------------------------------

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Cancel whatever is in flight and close the session."""
        self._cancelled = True
        for task in list(self._workers):
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        session, self._session = self._session, None
        self._session_loop = None
        if session is not None:
            await session.close()

    def _ensure_session(self) -> AsyncSession:
        loop = asyncio.get_running_loop()
        if self._session is not None and self._session_loop is loop:
            return self._session
        # A session is bound to the loop it was created on (libcurl multi + loop readers).
        pool = max(256, 2 * self.max_in_flight + 16)
        self._session = AsyncSession(
            max_clients=pool,
            http_version="v2tls" if self.http2 else "v1",
            allow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            timeout=self._timeout(),
            verify=ca_bundle(),
        )
        self._session_loop = loop
        return self._session

    def _timeout(self) -> float | tuple[float, float]:
        if self.timeout_s > CONNECT_TIMEOUT_MAX_S:
            return (CONNECT_TIMEOUT_MAX_S, self.timeout_s - CONNECT_TIMEOUT_MAX_S)
        return self.timeout_s

    def _group(self, name: str) -> _Group:
        group = self._groups.get(name)
        if group is None:
            group = self._groups[name] = _Group(name, self.start_in_flight, self.max_in_flight)
        return group

    # -- introspection ---------------------------------------------------------------------------

    def windows(self) -> dict[str, int]:
        """Current AIMD window per host group (for tests and the UI)."""
        return {name: g.window for name, g in self._groups.items()}

    def stats(self) -> FetchStats:
        """Snapshot of the current (or last) ``fetch_many`` run."""
        now = _now()
        while self._completions and now - self._completions[0] > RATE_WINDOW_S:
            self._completions.popleft()
        span = min(RATE_WINDOW_S, max(now - self._run_started_at, STATS_PERIOD_S))
        return FetchStats(
            done=self._done,
            total=self._total,
            bytes=self._bytes,
            in_flight=self._transfers_in_flight,
            req_per_s=len(self._completions) / span,
            errors=self._errors,
            retries=self._retries,
            hedges=self._hedges,
            throttled=any(g.throttled(now) for g in self._groups.values()),
            pushed_back=any(now < g.paused_until for g in self._groups.values()),
        )

    # -- the run ---------------------------------------------------------------------------------

    async def fetch_many(
        self,
        requests: Iterable[FetchRequest],
        on_result: Callable[[FetchResult], None] | None = None,
        on_stats: Callable[[FetchStats], None] | None = None,
        cancel: asyncio.Event | None = None,
        *,
        keep_results: bool = True,
        limit_in_flight: int | None = None,
        max_attempts: int | None = None,
    ) -> list[FetchResult]:
        """Fetch every request; results are returned in request order.

        ``on_result`` is called from the loop as each result completes, ``on_stats`` at most
        four times per second and once at the end. Setting ``cancel`` stops everything within
        a second; undelivered requests come back with ``error="SYS_CANCELLED"``. With
        ``keep_results=False`` the bodies are handed to ``on_result`` only: the returned list
        holds the results with an empty ``body`` (a caller consuming through the callback does
        not pay a second copy of every body for the life of the run).

        ``limit_in_flight`` caps the requests admitted per group for this run below the AIMD
        window (which keeps adapting), and ``max_attempts`` replaces the constructor's for this
        run. They exist for a caller's spaced retry rounds: a round at low concurrency on the
        same session keeps the group's pause after a 429 and its window (spec R4).
        """
        if self._busy:
            raise RuntimeError("fetch_many is already running on this Fetcher")
        if limit_in_flight is not None and limit_in_flight < 1:
            raise ValueError("limit_in_flight must be >= 1")
        if max_attempts is not None and max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        reqs = list(requests)
        results: list[FetchResult | None] = [None] * len(reqs)
        self._keep_results = keep_results
        self._run_limit = limit_in_flight
        self._run_attempts = self.max_attempts if max_attempts is None else max_attempts
        self._busy = True
        self._cancelled = False
        self._reset_counters(len(reqs))
        callback_error: list[BaseException] = []
        try:
            if cancel is not None and cancel.is_set():
                self._cancelled = True
            elif reqs:
                session = self._ensure_session()
                for group in self._groups.values():
                    group.wake = asyncio.Event()  # events are bound to the running loop
                for index, req in enumerate(reqs):
                    self._group(req.host_group).ready.append(_Pending(index, req))
                dispatchers = [
                    asyncio.create_task(
                        self._dispatch(g, session, results, on_result, callback_error)
                    )
                    for g in self._groups.values()
                    if not g.idle()
                ]
                helpers: list[asyncio.Task[None]] = []
                if on_stats is not None:
                    helpers.append(asyncio.create_task(self._ticker(on_stats)))
                if cancel is not None:
                    helpers.append(asyncio.create_task(self._watch_cancel(cancel)))
                try:
                    await asyncio.gather(*dispatchers)
                finally:
                    for task in helpers:
                        task.cancel()
                    await asyncio.gather(*helpers, return_exceptions=True)
        finally:
            self._busy = False
            self._run_limit = None
            self._run_attempts = self.max_attempts
            for group in self._groups.values():
                group.ready.clear()
                group.delayed.clear()
        if callback_error:
            raise callback_error[0]
        out: list[FetchResult] = []
        for index, res in enumerate(results):
            if res is None:
                res = FetchResult(reqs[index].key, 0, b"", {}, 0.0, 0, False, "SYS_CANCELLED")
                self._done += 1
                self._errors += 1
            out.append(res)
        if on_stats is not None:
            on_stats(self.stats())
        return out

    def _reset_counters(self, total: int) -> None:
        self._completions.clear()
        self._run_started_at = _now()
        self._done = 0
        self._total = total
        self._bytes = 0
        self._transfers_in_flight = 0
        self._errors = 0
        self._retries = 0
        self._hedges = 0

    async def _ticker(self, on_stats: Callable[[FetchStats], None]) -> None:
        while True:
            await asyncio.sleep(STATS_PERIOD_S)
            on_stats(self.stats())

    async def _watch_cancel(self, cancel: asyncio.Event) -> None:
        await cancel.wait()
        self._cancelled = True
        for task in list(self._workers):
            task.cancel()
        for group in self._groups.values():
            group.wake.set()

    async def _dispatch(
        self,
        group: _Group,
        session: AsyncSession,
        results: list[FetchResult | None],
        on_result: Callable[[FetchResult], None] | None,
        callback_error: list[BaseException],
    ) -> None:
        """Admit requests of one group while its window and pause allow (spec R2)."""
        while True:
            now = _now()
            group.promote_due(now)
            if self._cancelled:
                if group.in_flight == 0:
                    return
            elif group.idle():
                return
            elif (
                group.ready
                and now >= group.paused_until
                and now >= group.next_start
                and group.in_flight < self._admission(group)
            ):
                pending = group.ready.popleft()
                group.in_flight += 1
                group.next_start = now + self._spacing  # the server's own rate, 0 when it has none
                task = asyncio.create_task(
                    self._work(pending, group, session, results, on_result, callback_error)
                )
                self._workers.add(task)
                # The slot is released in a done callback, not in the coroutine: a task
                # cancelled before its first step never runs its own ``finally``.
                task.add_done_callback(functools.partial(self._release_slot, group))
                continue
            timeout: float | None = None
            if not self._cancelled:
                if group.ready and now < group.paused_until:
                    timeout = group.paused_until - now
                if group.ready and now < group.next_start:
                    wait = group.next_start - now
                    timeout = wait if timeout is None else min(timeout, wait)
                if group.delayed:
                    due = group.delayed[0][0] - now
                    timeout = due if timeout is None else min(timeout, due)
            group.wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(group.wake.wait(), timeout)

    def _admission(self, group: _Group) -> int:
        """Requests the group may have in flight now: its window, capped for this run."""
        if self._run_limit is None:
            return group.window
        return min(group.window, self._run_limit)

    async def _work(
        self,
        pending: _Pending,
        group: _Group,
        session: AsyncSession,
        results: list[FetchResult | None],
        on_result: Callable[[FetchResult], None] | None,
        callback_error: list[BaseException],
    ) -> None:
        """One admitted attempt (with its hedge), then deliver or re-queue."""
        if pending.attempts == 0:
            pending.started_at = _now()
        else:
            self._retries += 1
        try:
            outcome = await self._attempt(pending, group, session)
            now = _now()
            group.record(outcome, now)
            pending.last = outcome
            if outcome.kind == "pushback" and not self._cancelled:
                # A 429 obeyed is not a failure of the request: it is re-queued at the tail
                # after the group's pause, within a budget of its own (spec R2).
                not_before = group.pause(_retry_after(outcome.headers), now)
                wait = not_before - now
                if (
                    pending.pushbacks < self.max_pushbacks
                    and pending.pushback_wait_s + wait <= self.pushback_budget_s
                ):
                    pending.pushbacks += 1
                    pending.pushback_wait_s += wait
                    group.push_delayed(pending, not_before, tail=True)
                    return
            if outcome.kind == "ok" or pending.failures >= self._run_attempts or self._cancelled:
                result = FetchResult(
                    key=pending.request.key,
                    status=outcome.status,
                    body=outcome.body,
                    headers=outcome.headers,
                    elapsed=now - pending.started_at,
                    attempts=pending.attempts,
                    hedged=pending.hedged,
                    error=outcome.error_code,
                    detail=outcome.detail,
                )
                self._deliver(pending.index, result, results, on_result, callback_error)
            elif outcome.kind == "pushback":
                # pushback budget exhausted: the pause is still obeyed, and it costs an attempt
                group.push_delayed(pending, group.paused_until, tail=True)
            else:
                group.push_delayed(pending, now + _backoff(pending.failures))
        except asyncio.CancelledError:
            pass  # the run is being cancelled; fetch_many fills in SYS_CANCELLED

    def _release_slot(self, group: _Group, task: asyncio.Task[None]) -> None:
        self._workers.discard(task)
        group.in_flight -= 1
        group.wake.set()

    def _deliver(
        self,
        index: int,
        result: FetchResult,
        results: list[FetchResult | None],
        on_result: Callable[[FetchResult], None] | None,
        callback_error: list[BaseException],
    ) -> None:
        if self._keep_results:
            results[index] = result
        else:
            results[index] = FetchResult(
                result.key,
                result.status,
                b"",
                result.headers,
                result.elapsed,
                result.attempts,
                result.hedged,
                result.error,
                result.detail,
            )
        self._done += 1
        self._bytes += len(result.body)
        self._completions.append(_now())
        if result.error is not None:
            self._errors += 1
        if on_result is not None:
            try:
                on_result(result)
            except BaseException as exc:  # surfaced by fetch_many
                if not callback_error:
                    callback_error.append(exc)
                self._cancelled = True
                for task in list(self._workers):
                    if task is not asyncio.current_task():
                        task.cancel()
                for g in self._groups.values():
                    g.wake.set()

    async def _attempt(self, pending: _Pending, group: _Group, session: AsyncSession) -> _Outcome:
        """One transfer, doubled after ``hedge_after_s`` (spec R3)."""
        req = pending.request
        pending.attempts += 1
        primary = asyncio.create_task(self._transfer(session, req))
        tasks: set[asyncio.Task[_Outcome]] = {primary}
        hedge_started = False
        try:
            done, _ = await asyncio.wait(tasks, timeout=self.hedge_after_s)
            if primary in done:
                return primary.result()
            hedge_cap = max(1, self._admission(group) // 2)
            if (
                pending.failures < self._run_attempts
                and group.hedges_in_flight < hedge_cap
                and not self._cancelled
            ):
                hedge_started = True
                group.hedges_in_flight += 1
                self._hedges += 1
                pending.hedged = True
                pending.attempts += 1
                tasks.add(asyncio.create_task(self._transfer(session, req)))
            last: _Outcome | None = None
            while tasks:
                done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    outcome = task.result()
                    if outcome.kind == "ok":
                        return outcome
                    last = _prefer(last, outcome)
            assert last is not None
            return last
        finally:
            if hedge_started:
                group.hedges_in_flight -= 1
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _transfer(self, session: AsyncSession, req: FetchRequest) -> _Outcome:
        """One HTTP transfer; never raises except ``CancelledError``."""
        self._transfers_in_flight += 1
        t0 = _now()
        try:
            try:
                headers = {"User-Agent": USER_AGENT, **(req.headers or {})}
                response = await session.request(
                    "GET", req.url, headers=headers, timeout=self._timeout()
                )
            except Timeout as exc:
                return _Outcome("timeout", latency=_now() - t0, detail=_transport_detail(exc))
            except RequestException as exc:
                return _Outcome("connect", latency=_now() - t0, detail=_transport_detail(exc))
            status = int(response.status_code)
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            body = response.content
            if status == 429:
                kind: _Kind = "pushback"
            elif status >= 500:
                kind = "server"
            else:
                kind = "ok"
            return _Outcome(kind, status, body, headers, _now() - t0)
        finally:
            self._transfers_in_flight -= 1


# --- helpers ------------------------------------------------------------------------------------


def _now() -> float:
    return time.monotonic()


def _backoff(attempts: int) -> float:
    base = min(BACKOFF_BASE_S * 2 ** (attempts - 1), BACKOFF_MAX_S)
    return base * (1.0 + random.random() * BACKOFF_JITTER)


def _transport_detail(exc: BaseException) -> str:
    """libcurl's own error line, without curl_cffi's prefix and documentation link.

    ``Failed to perform, curl: (28) Operation timed out after 20001 milliseconds with 0 bytes
    received. See https://curl.se/...`` becomes ``curl: (28) Operation timed out after 20001
    milliseconds with 0 bytes received.``: the curl code tells a timeout from a reset or an
    HTTP/2 stream error, and the byte count whether the headers had arrived.
    """
    text = " ".join(str(exc).split())
    start = text.find("curl: (")
    if start > 0:
        text = text[start:]
    end = text.find(" See https://curl.se/")
    if end > 0:
        text = text[:end]
    return text[:DETAIL_MAX_CHARS] or type(exc).__name__


def _prefer(current: _Outcome | None, candidate: _Outcome) -> _Outcome:
    """Keep the most informative failure: an HTTP answer beats a transport error."""
    if current is None:
        return candidate
    rank = {"pushback": 3, "server": 2, "timeout": 1, "connect": 0}
    return candidate if rank[candidate.kind] >= rank[current.kind] else current


def _retry_after(headers: dict[str, str]) -> float | None:
    """Seconds from ``Retry-After`` (delta-seconds or HTTP-date), ``None`` when absent/invalid."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    raw = raw.strip()
    if raw.isdigit():
        return float(raw)
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    delta = (when - dt.datetime.now(dt.UTC)).total_seconds()
    return max(delta, 0.0)


def fetch_all(requests: Iterable[FetchRequest], **kwargs: Any) -> list[FetchResult]:
    """Synchronous helper: a fresh ``Fetcher(**kwargs)`` run to completion with ``asyncio.run``."""

    async def run() -> list[FetchResult]:
        async with Fetcher(**kwargs) as fetcher:
            return await fetcher.fetch_many(requests)

    return asyncio.run(run())
