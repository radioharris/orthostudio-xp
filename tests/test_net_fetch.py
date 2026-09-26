# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Tests of ``orthostudio.net.fetch`` against a local HTTP/1.1 server (no real network).

Spec: ``docs/specs/net-download.md`` section 3. The server (a thread) simulates the cases the
fetcher must handle: normal bodies, 404, 500 then 200, 503 then 200, 429 with ``Retry-After``,
a 5 s straggler, a dropped connection, a slow route and a hanging route.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import math
import socket
import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orthostudio.net import USER_AGENT, Fetcher, FetchRequest, FetchResult, FetchStats, fetch_all
from orthostudio.net import fetch as fetch_mod

# --- local server -------------------------------------------------------------------------------


def body_for(n: int) -> bytes:
    """Deterministic pseudo-tile body, 12-20 KB."""
    unit = b"tile-%d|" % n
    size = 12_000 + (n * 997) % 8_000
    return (unit * (size // len(unit) + 1))[:size]


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.hits: dict[str, list[float]] = defaultdict(list)

    def hit(self, path: str) -> int:
        with self.lock:
            self.hits[path].append(time.monotonic())
            return len(self.hits[path])


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: _State

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _send(self, status: int, body: bytes, extra: dict[str, str] | None = None) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", "image/jpeg" if status == 200 else "text/plain")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass  # client went away (hedge loser, cancellation)

    def do_GET(self) -> None:
        parts = self.path.strip("/").split("/")
        route, arg = parts[0], (parts[1] if len(parts) > 1 else "0")
        count = self.state.hit(self.path)
        if route == "ok":
            self._send(200, body_for(int(arg)))
        elif route == "whoami":
            self._send(200, (self.headers.get("User-Agent") or "").encode())
        elif route == "notfound":
            self._send(404, b"no tile")
        elif route == "flaky":
            self._send(500 if count == 1 else 200, b"oops" if count == 1 else body_for(1))
        elif route == "overload":
            self._send(503 if count == 1 else 200, b"busy" if count == 1 else body_for(2))
        elif route == "ratelimit":
            if count == 1:
                self._send(429, b"slow down", {"Retry-After": "1"})
            else:
                self._send(200, body_for(3))
        elif route == "ratelimit-always":
            self._send(429, b"slow down", {"Retry-After": "1"})
        elif route == "slow":
            if count == 1:
                time.sleep(5.0)
            self._send(200, body_for(4))
        elif route == "drop":
            if count == 1:
                self.close_connection = True
                with contextlib.suppress(OSError):
                    self.connection.shutdown(socket.SHUT_RDWR)
                return
            self._send(200, body_for(5))
        elif route == "delay":
            time.sleep(int(arg) / 1000.0)
            self._send(200, body_for(6))
        elif route == "hang":
            time.sleep(30.0)
            self._send(200, body_for(7))
        else:
            self._send(400, b"unknown route")


class LocalServer:
    def __init__(self) -> None:
        self.state = _State()
        handler = type("Handler", (_Handler,), {"state": self.state})
        # The fetcher opens its first window (8 connections) at once: the default listen
        # backlog of 5 drops the extra SYNs, which macOS retransmits ~100 ms later, so a few
        # requests of the first burst look "late" on the server. A real provider has no such
        # backlog; give the test server room for the whole window.
        ThreadingHTTPServer.request_queue_size = 128
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        self.base = f"http://{host}:{port}"

    def url(self, path: str) -> str:
        return f"{self.base}/{path.lstrip('/')}"

    def hits(self, path: str) -> list[float]:
        return list(self.state.hits[path])

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture(scope="module")
def server() -> Iterator[LocalServer]:
    srv = LocalServer()
    yield srv
    srv.close()


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


async def fetch(
    fetcher: Fetcher, requests: list[FetchRequest], **kwargs: object
) -> list[FetchResult]:
    async with fetcher:
        return await fetcher.fetch_many(requests, **kwargs)  # type: ignore[arg-type]


# --- tests --------------------------------------------------------------------------------------


def test_every_request_says_who_is_asking(server: LocalServer) -> None:
    """Nothing named us before: the services that carry us for nothing could not tell who was
    knocking, Overpass asks a client to name itself, and the server of the ANADEM relief answers
    403 to a request without a User-Agent (measured 2026-09-20). A caller may still set its own."""
    reqs = [
        FetchRequest(key="default", url=server.url("whoami"), host_group="g"),
        FetchRequest(
            key="own", url=server.url("whoami/2"), headers={"User-Agent": "mine"}, host_group="g"
        ),
    ]
    said = {r.key: r.body.decode() for r in run(fetch(Fetcher(), reqs))}
    assert said["default"] == USER_AGENT
    assert USER_AGENT.startswith("OrthoStudio-XP/") and "github.com" in USER_AGENT
    assert said["own"] == "mine"


def test_normal_bodies_in_request_order(server: LocalServer) -> None:
    reqs = [FetchRequest(key=i, url=server.url(f"ok/{i}"), host_group="g") for i in range(50)]
    seen: list[FetchResult] = []
    fetcher = Fetcher(max_in_flight=8, start_in_flight=8)
    results = run(fetch(fetcher, reqs, on_result=seen.append))
    assert [r.key for r in results] == list(range(50))
    for r in results:
        assert r.status == 200 and r.error is None and r.attempts == 1 and not r.hedged
        assert r.body == body_for(r.key)
        assert r.headers["content-length"] == str(len(r.body))
        assert r.elapsed > 0
    assert sorted(r.key for r in seen) == list(range(50))
    st = fetcher.stats()
    assert st.done == st.total == 50 and st.errors == 0 and st.retries == 0 and st.hedges == 0
    assert st.bytes == sum(len(body_for(i)) for i in range(50))
    assert st.in_flight == 0


def test_404_is_an_answer_not_an_error(server: LocalServer) -> None:
    results = run(fetch(Fetcher(), [FetchRequest("k", server.url("notfound/1"))]))
    (r,) = results
    assert r.status == 404 and r.error is None and r.attempts == 1
    assert len(server.hits("/notfound/1")) == 1


def test_500_then_200_is_retried_once(server: LocalServer) -> None:
    fetcher = Fetcher()
    (r,) = run(fetch(fetcher, [FetchRequest("k", server.url("flaky/1"))]))
    assert r.status == 200 and r.error is None and r.attempts == 2 and r.body == body_for(1)
    assert fetcher.stats().retries == 1
    hits = server.hits("/flaky/1")
    assert len(hits) == 2 and 0.45 <= hits[1] - hits[0] < 2.0  # back-off 0.5 s (+25 % jitter)


def test_503_then_200_halves_the_window(server: LocalServer) -> None:
    fetcher = Fetcher(max_in_flight=16, start_in_flight=16)
    (r,) = run(fetch(fetcher, [FetchRequest("k", server.url("overload/1"), host_group="p")]))
    assert r.status == 200 and r.attempts == 2
    assert fetcher.windows()["p"] == 8


def test_429_retry_after_is_obeyed(server: LocalServer) -> None:
    fetcher = Fetcher(max_in_flight=16, start_in_flight=16)
    reqs = [FetchRequest(i, server.url(f"ok/{i}"), host_group="p") for i in range(3)]
    reqs.append(FetchRequest("rl", server.url("ratelimit/1"), host_group="p"))
    seen_throttled: list[bool] = []
    results = run(fetch(fetcher, reqs, on_stats=lambda s: seen_throttled.append(s.throttled)))
    rl = results[-1]
    assert rl.status == 200 and rl.error is None and rl.attempts == 2 and rl.body == body_for(3)
    hits = server.hits("/ratelimit/1")
    assert len(hits) == 2 and hits[1] - hits[0] >= 0.99
    assert fetcher.windows()["p"] == 8
    assert True in seen_throttled
    assert all(r.status == 200 for r in results[:-1])


def test_429_exhausted_reports_rate_limited(server: LocalServer) -> None:
    """With the pushback budget closed (``max_pushbacks=0``) a 429 costs an attempt as before."""
    fetcher = Fetcher(max_attempts=1, max_pushbacks=0)
    (r,) = run(fetch(fetcher, [FetchRequest("k", server.url("ratelimit/2"))]))
    assert r.status == 429 and r.error == "NET_RATE_LIMITED" and r.attempts == 1
    assert r.body == b"slow down" and r.headers["retry-after"] == "1"
    assert fetcher.stats().errors == 1


def test_429_obeyed_does_not_cost_an_attempt(server: LocalServer) -> None:
    """Spec R2 (amended by the P1 review): a 429 whose pause is obeyed is re-queued without
    consuming ``max_attempts``; ``attempts`` still counts the transfers."""
    fetcher = Fetcher(max_attempts=1)
    (r,) = run(fetch(fetcher, [FetchRequest("k", server.url("ratelimit/4"))]))
    assert r.status == 200 and r.error is None and r.attempts == 2
    hits = server.hits("/ratelimit/4")
    assert len(hits) == 2 and hits[1] - hits[0] >= 0.99
    assert fetcher.stats().errors == 0 and fetcher.stats().retries == 1


def test_429_budget_is_bounded(server: LocalServer) -> None:
    """A provider that never stops answering 429 still ends with ``NET_RATE_LIMITED``."""
    fetcher = Fetcher(max_attempts=1, max_pushbacks=2)
    (r,) = run(fetch(fetcher, [FetchRequest("k", server.url("ratelimit-always/1"))]))
    assert r.status == 429 and r.error == "NET_RATE_LIMITED" and r.attempts == 3
    assert len(server.hits("/ratelimit-always/1")) == 3


def test_hedge_beats_a_straggler(server: LocalServer) -> None:
    fetcher = Fetcher(hedge_after_s=0.3, timeout_s=10.0)
    t0 = time.monotonic()
    (r,) = run(fetch(fetcher, [FetchRequest("k", server.url("slow/1"), host_group="p")]))
    wall = time.monotonic() - t0
    assert r.status == 200 and r.body == body_for(4) and r.error is None
    assert r.hedged and r.attempts == 2
    assert wall < 2.0, wall
    assert fetcher.stats().hedges == 1 and fetcher.stats().retries == 0
    assert len(server.hits("/slow/1")) == 2


def test_a_refused_connection_lowers_the_window(server: LocalServer) -> None:
    """A server that defends itself by refusing the connection says no as plainly as one
    answering 429 or 503. Until 0.1.14 only those two lowered the window, so a server that
    blocks a caller it finds too eager was knocked on by the whole window until the attempts
    ran out; a user watching his imagery fail said so (2026-09-24)."""
    fetcher = Fetcher(max_in_flight=16, start_in_flight=16)
    (r,) = run(fetch(fetcher, [FetchRequest("k", server.url("drop/9"), host_group="p")]))
    assert r.status == 200 and r.attempts == 2
    assert fetcher.windows()["p"] == 8


def test_a_rate_ceiling_spaces_the_requests(server: LocalServer) -> None:
    """``req_per_s`` starts one request every ``1 / req_per_s`` seconds per host group, whatever
    the window allows. A server that counts requests rather than connections (Apache with
    mod_evasive, which many small services run) blocks a caller for seconds after a burst, and
    a window of 16 on a fast line is a burst however few connections it holds."""
    ceiling, n = 10.0, 6
    rate = ceiling * fetch_mod.RATE_START  # what a group starts at, and climbs from
    fetcher = Fetcher(max_in_flight=16, start_in_flight=16, req_per_s=ceiling)
    paths = [f"ok/{900 + i}" for i in range(n)]  # a range of its own: the hits are per path
    reqs = [FetchRequest(i, server.url(path), host_group="p") for i, path in enumerate(paths)]
    t0 = time.monotonic()
    results = run(fetch(fetcher, reqs))
    elapsed = time.monotonic() - t0
    assert all(r.status == 200 for r in results)
    # a gate can only make a run longer, so the floor is the safe assertion
    assert elapsed >= (n - 1) / rate * 0.9, elapsed
    starts = sorted(t for path in paths for t in server.hits(f"/{path}"))
    gaps = [b - a for a, b in itertools.pairwise(starts)]
    assert min(gaps) >= 0.5 / rate, gaps


def test_the_rate_climbs_while_a_server_answers_and_never_passes_its_ceiling() -> None:
    """``server_req_per_s`` is a ceiling to approach, not a speed to hold.

    Every rate in the registry was measured once from one machine, and EOX's 224 turned out to be
    eleven times what a user's address could get before the server stopped answering (2026-09-24).
    So a group starts at a quarter and climbs a step per round of answers, which converges on what
    this line and this route actually allow instead of on what ours did.
    """
    group = fetch_mod._Group("p", 8, 8, 100.0)
    assert group.rate == 25.0  # a quarter of the ceiling
    ok = fetch_mod._Outcome("ok", 200, latency=0.05)
    for _ in range(fetch_mod.RATE_ROUND):
        group.record(ok, 0.0)
    assert group.rate == 25.0 + 100.0 / fetch_mod.RATE_STEPS
    for _ in range(fetch_mod.RATE_ROUND * fetch_mod.RATE_STEPS * 2):
        group.record(ok, 0.0)
    assert group.rate == 100.0  # and it stops there


def test_the_rate_falls_with_the_window_and_not_below_its_floor() -> None:
    """A server pushing back says the same thing whether it counts connections or requests, so
    the rate falls on the same signal and by the same factor. The floor keeps a build moving."""
    group = fetch_mod._Group("p", 64, 64, 100.0)
    group.completions_since_decrease = 1 << 30
    group.record(fetch_mod._Outcome("pushback", 429), 0.0)
    assert group.rate == 12.5 and group.window == 32  # both halved
    for _ in range(20):
        group.completions_since_decrease = 1 << 30
        group.record(fetch_mod._Outcome("pushback", 429), 0.0)
    assert group.rate == 100.0 * fetch_mod.RATE_FLOOR  # never below a tenth of the ceiling


def test_a_group_without_a_ceiling_is_not_paced_at_all() -> None:
    """Bing and Esri declare no rate: nothing must change for them."""
    group = fetch_mod._Group("p", 64, 64)
    assert group.rate is None
    for _ in range(fetch_mod.RATE_ROUND * 3):
        group.record(fetch_mod._Outcome("ok", 200, latency=0.05), 0.0)
    assert group.rate is None
    group.completions_since_decrease = 1 << 30
    group.record(fetch_mod._Outcome("pushback", 429), 0.0)
    assert group.rate is None and group.window == 32


def test_dropped_connection_is_retried(server: LocalServer) -> None:
    fetcher = Fetcher()
    (r,) = run(fetch(fetcher, [FetchRequest("k", server.url("drop/1"))]))
    assert r.status == 200 and r.body == body_for(5) and r.error is None
    assert r.attempts == 2
    assert fetcher.stats().retries == 1


def test_timeout_exhausted_reports_net_timeout(server: LocalServer) -> None:
    fetcher = Fetcher(timeout_s=0.3, max_attempts=2, max_in_flight=16, start_in_flight=16)
    t0 = time.monotonic()
    (r,) = run(fetch(fetcher, [FetchRequest("k", server.url("hang/1"), host_group="p")]))
    wall = time.monotonic() - t0
    assert r.status == 0 and r.error == "NET_TIMEOUT" and r.attempts == 2 and r.body == b""
    assert 1.0 <= wall < 3.0, wall
    assert fetcher.windows()["p"] == 12  # 16 x 0.75; the second timeout is inside the cooldown


def test_aimd_window_climbs_to_the_ceiling(server: LocalServer) -> None:
    fetcher = Fetcher(max_in_flight=16, start_in_flight=4)
    reqs = [FetchRequest(i, server.url(f"ok/{i}"), host_group="up") for i in range(400)]
    results = run(fetch(fetcher, reqs))
    assert all(r.status == 200 for r in results)
    assert fetcher.windows()["up"] == 16


def test_aimd_window_halves_on_latency_spike(server: LocalServer) -> None:
    fetcher = Fetcher(max_in_flight=16, start_in_flight=16)
    fast = [FetchRequest(i, server.url(f"ok/{i}"), host_group="down") for i in range(300)]
    run(fetch(fetcher, fast))
    assert fetcher.windows()["down"] == 16
    slow = [FetchRequest(i, server.url("delay/300"), host_group="down") for i in range(60)]
    results = run(fetch(fetcher, slow))
    assert all(r.status == 200 for r in results)
    assert fetcher.windows()["down"] < 16  # halved at least once (then at most +1 per round)
    assert fetcher.stats().throttled


def test_cancel_returns_within_a_second(server: LocalServer) -> None:
    async def scenario() -> tuple[list[FetchResult], float]:
        cancel = asyncio.Event()
        fetcher = Fetcher(max_in_flight=8, start_in_flight=8)
        reqs = [FetchRequest(i, server.url("delay/300"), host_group="c") for i in range(200)]

        async def trigger() -> None:
            await asyncio.sleep(0.5)
            cancel.set()

        trigger_task = asyncio.create_task(trigger())
        async with fetcher:
            t0 = time.monotonic()
            results = await fetcher.fetch_many(reqs, cancel=cancel)
            wall = time.monotonic() - t0
        await trigger_task
        return results, wall

    results, wall = run(scenario())
    assert wall < 1.5, wall  # 0.5 s until the cancel, then < 1 s
    assert len(results) == 200
    delivered = [r for r in results if r.error is None]
    cancelled = [r for r in results if r.error == "SYS_CANCELLED"]
    assert 1 <= len(delivered) < 60
    assert len(delivered) + len(cancelled) == 200
    assert all(r.status == 0 and r.body == b"" for r in cancelled)


def test_stats_rate_and_final_snapshot(server: LocalServer) -> None:
    snapshots: list[FetchStats] = []
    reqs = [FetchRequest(i, server.url("delay/50"), host_group="s") for i in range(80)]
    fetcher = Fetcher(max_in_flight=8, start_in_flight=8)
    t0 = time.monotonic()
    results = run(fetch(fetcher, reqs, on_stats=snapshots.append))
    wall = time.monotonic() - t0
    assert wall >= 0.4  # 80 x 50 ms / 8
    assert len(snapshots) <= 4 * math.ceil(wall) + 1
    assert len(snapshots) >= 2
    final = snapshots[-1]
    assert final.done == final.total == 80 and final.in_flight == 0
    assert final.bytes == sum(len(r.body) for r in results) == 80 * len(body_for(6))
    assert final.errors == 0 and not final.throttled
    assert any(s.in_flight > 0 for s in snapshots[:-1])
    assert final.req_per_s > 10


def test_fetch_all_synchronous_helper(server: LocalServer) -> None:
    reqs = [FetchRequest(i, server.url(f"ok/{i}")) for i in range(10)]
    results = fetch_all(reqs, max_in_flight=4, start_in_flight=4)
    assert [r.key for r in results] == list(range(10))
    assert all(r.status == 200 and r.body == body_for(r.key) for r in results)


def test_host_groups_are_independent(server: LocalServer) -> None:
    fetcher = Fetcher(max_in_flight=16, start_in_flight=16)
    reqs = [FetchRequest("a", server.url("ratelimit/3"), host_group="A")]
    reqs += [FetchRequest(i, server.url(f"ok/{i}"), host_group="B") for i in range(5)]
    results = run(fetch(fetcher, reqs))
    assert results[0].status == 200 and results[0].attempts == 2
    assert all(r.status == 200 and r.attempts == 1 for r in results[1:])
    assert fetcher.windows() == {"A": 8, "B": 16}


def test_transport_failure_keeps_curls_error_line(server: LocalServer) -> None:
    """Spec R4 (2026-09-13 review): a request given up says what happened on the wire."""
    (hung,) = run(
        fetch(Fetcher(timeout_s=0.3, max_attempts=1), [FetchRequest("h", server.url("hang/3"))])
    )
    assert hung.error == "NET_TIMEOUT" and hung.status == 0
    assert hung.detail.startswith("curl: (28) Operation timed out after")
    assert "curl.se" not in hung.detail and "Failed to perform" not in hung.detail
    (dropped,) = run(fetch(Fetcher(max_attempts=1), [FetchRequest("d", server.url("drop/2"))]))
    assert dropped.error == "NET_CONNECTION_FAILED" and dropped.detail.startswith("curl: (")
    (answer,) = run(fetch(Fetcher(max_attempts=1), [FetchRequest("f", server.url("flaky/3"))]))
    assert answer.error == "NET_SERVER_ERROR" and answer.status == 500 and answer.detail == ""


def test_run_overrides_cap_in_flight_and_attempts_for_one_run(server: LocalServer) -> None:
    """Spec R4: a caller's retry round runs at low concurrency and with its own attempts on the
    same fetcher (same session, window and pause state); the next run is back to the
    constructor's values."""
    fetcher = Fetcher(max_in_flight=16, start_in_flight=16, timeout_s=0.3, max_attempts=4)

    async def scenario() -> tuple[float, int, FetchResult, FetchResult]:
        async with fetcher:
            t0 = time.monotonic()
            capped = await fetcher.fetch_many(
                [FetchRequest(i, server.url("delay/150"), host_group="o") for i in range(12)],
                limit_in_flight=2,
            )
            wall = time.monotonic() - t0
            assert all(r.status == 200 and r.error is None for r in capped)
            window = fetcher.windows()["o"]
            (hung,) = await fetcher.fetch_many(
                [FetchRequest("h", server.url("hang/4"), host_group="o")], max_attempts=1
            )
            (flaky,) = await fetcher.fetch_many(
                [FetchRequest("f", server.url("flaky/4"), host_group="o")]
            )
            return wall, window, hung, flaky

    wall, window, hung, flaky = run(scenario())
    assert wall >= 0.85, wall  # 12 x 150 ms at 2 in flight; 16 in flight would take ~0.15 s
    assert window == 16  # the cap does not move the AIMD window
    assert hung.error == "NET_TIMEOUT" and hung.attempts == 1
    assert flaky.status == 200 and flaky.attempts == 2  # a 500 retried: max_attempts is 4 again
    with pytest.raises(ValueError):
        run(fetch(Fetcher(), [], limit_in_flight=0))
    with pytest.raises(ValueError):
        run(fetch(Fetcher(), [], max_attempts=0))


def test_empty_request_list(server: LocalServer) -> None:
    snapshots: list[FetchStats] = []
    assert run(fetch(Fetcher(), [], on_stats=snapshots.append)) == []
    assert len(snapshots) == 1 and snapshots[0].total == 0


def test_callback_exception_surfaces(server: LocalServer) -> None:
    def boom(_: FetchResult) -> None:
        raise ValueError("callback failed")

    reqs = [FetchRequest(i, server.url(f"ok/{i}")) for i in range(20)]
    with pytest.raises(ValueError, match="callback failed"):
        run(fetch(Fetcher(), reqs, on_result=boom))


def test_constructor_validation() -> None:
    with pytest.raises(ValueError):
        Fetcher(max_in_flight=0)
    with pytest.raises(ValueError):
        Fetcher(max_attempts=0)
    with pytest.raises(ValueError):
        Fetcher(hedge_after_s=0)
    with pytest.raises(ValueError):
        Fetcher(req_per_s=0)
    f = Fetcher(max_in_flight=8, start_in_flight=64)
    assert f.start_in_flight == 8
    assert Fetcher().req_per_s is None  # no ceiling unless the provider names one


# --- pure helpers -------------------------------------------------------------------------------


def test_retry_after_parsing() -> None:
    assert fetch_mod._retry_after({"retry-after": "7"}) == 7.0
    assert fetch_mod._retry_after({}) is None
    assert fetch_mod._retry_after({"retry-after": "soon"}) is None
    later = fetch_mod._retry_after({"retry-after": "Thu, 01 Jan 2099 00:00:00 GMT"})
    assert later is not None and later > 3600
    past = fetch_mod._retry_after({"retry-after": "Thu, 01 Jan 2004 00:00:00 GMT"})
    assert past == 0.0


def test_transport_detail_is_curls_own_line() -> None:
    long_message = (
        "Failed to perform, curl: (92) HTTP/2 stream 5 was not closed cleanly: INTERNAL_ERROR "
        "(err 2). See https://curl.se/libcurl/c/libcurl-errors.html first for more details."
    )
    assert fetch_mod._transport_detail(RuntimeError(long_message)) == (
        "curl: (92) HTTP/2 stream 5 was not closed cleanly: INTERNAL_ERROR (err 2)."
    )
    assert fetch_mod._transport_detail(RuntimeError("")) == "RuntimeError"
    assert len(fetch_mod._transport_detail(RuntimeError("x" * 999))) == fetch_mod.DETAIL_MAX_CHARS


def test_backoff_schedule() -> None:
    for attempts, base in [(1, 0.5), (2, 1.0), (3, 2.0), (4, 4.0), (7, 4.0)]:
        values = {fetch_mod._backoff(attempts) for _ in range(50)}
        assert all(base <= v <= base * 1.25 for v in values)


def test_group_pause_schedule_without_retry_after() -> None:
    g = fetch_mod._Group("g", 16, 32)
    now = 1000.0
    assert g.pause(None, now) == pytest.approx(now + 5.0)
    assert g.pause(None, now + 1) == pytest.approx(now + 11.0)
    assert g.pause(None, now + 2) == pytest.approx(now + 22.0)
    assert g.pause(None, now + 3) == pytest.approx(now + 33.0)  # capped at 30 s
    assert g.pause(None, now + 200) == pytest.approx(now + 205.0)  # reset after 60 s quiet
    assert g.pause(0.1, now + 300) == pytest.approx(now + 301.0)  # Retry-After clamped to >= 1
    assert g.pause(999, now + 400) == pytest.approx(now + 520.0)  # ... and <= 120


def _latency_run(pattern: list[float], start: int = 128) -> tuple[int, int]:
    """The lowest window and the number of decreases of a group fed answers of these latencies."""
    g = fetch_mod._Group("g", start, 128)
    lowest, decreases, last = g.window, 0, g.last_decrease_at
    for i, latency in enumerate(pattern):
        g.record(fetch_mod._Outcome("ok", 200, latency=latency), i / 300)
        lowest = min(lowest, g.window)
        if g.last_decrease_at != last:
            decreases, last = decreases + 1, g.last_decrease_at
    return lowest, decreases


def test_group_latency_signal_is_a_rising_tail_not_a_mix_of_sea_and_land() -> None:
    """A user's coastal tile at Esri Clarity fell to 40 requests/s (2026-09-15): sea answers in
    60 ms, land in 400 ms, and the p90 of such a mix above twice its median halved the window
    39 times, down to 8, though the server was not congested."""
    coast = [0.06, 0.06, 0.06, 0.45, 0.45] * 800
    assert _latency_run(coast) == (128, 0)
    assert _latency_run([0.082, 0.09, 0.1, 0.114, 0.07] * 800) == (128, 0)  # Bing at 128
    # the sea, then the land: one halving, not a fall to the floor
    assert _latency_run([0.06] * 1000 + [0.4] * 3000) == (64, 1)
    # a server that does slow down is still seen, each time its tail doubles
    lowest, decreases = _latency_run([0.1] * 1000 + [0.5] * 600, start=64)
    assert decreases == 1 and lowest < 64
    assert _latency_run([0.1] * 1000 + [0.25] * 1000 + [0.6] * 1000, start=64)[1] == 2


def test_group_decrease_once_per_round() -> None:
    g = fetch_mod._Group("g", 64, 128)
    push = fetch_mod._Outcome("pushback", 429)
    g.record(push, 0.0)
    assert g.window == 32
    g.record(push, 0.0)
    assert g.window == 32  # cooldown: one decrease per round
    for i in range(32):
        g.record(fetch_mod._Outcome("ok", 200, latency=0.05), float(i))
    g.record(push, 40.0)
    assert g.window == 16
    assert g.min_window == 8
    for _ in range(5):
        g.record(fetch_mod._Outcome("timeout"), 100.0)
        for i in range(g.window):
            g.record(fetch_mod._Outcome("ok", 200, latency=0.05), 100.0 + i)
    assert 8 <= g.window <= 9  # floor 8, plus at most one additive step
