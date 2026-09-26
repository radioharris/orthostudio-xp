# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The map proxy, ``GET /api/map/{provider}/{z}/{x}/{y}`` (``docs/specs/map-zones.md`` section 6).

``httpx.ASGITransport`` on a temporary ``OSXP_HOME``; the upstream is a stub ``fetch`` (no
network), except in the two tests of the real client, which talk to a loopback server. One test
runs the proxy under uvicorn on a loopback port, to close connections the way a browser does.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from PIL import Image
from starlette.middleware.base import BaseHTTPMiddleware

import test_api_fakes as fakes
from orthostudio.api.map_api import MapProxy, sniff_image_type
from orthostudio.imagery.providers import Provider, cache_name, load_registry, tile_url
from orthostudio.net.fetch import FetchRequest, FetchResult

anyio_backend = fakes.anyio_backend
home = fakes.home


def _image(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (52, 94, 38)).save(buf, format=fmt)
    return buf.getvalue()


JPEG = _image("JPEG")
PNG = _image("PNG")


def _result(
    request: FetchRequest,
    status: int = 200,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    error: str | None = None,
) -> FetchResult:
    return FetchResult(request.key, status, body, dict(headers or {}), 0.01, 1, False, error)


def _jpeg(request: FetchRequest) -> FetchResult:
    return _result(request, 200, JPEG, {"content-type": "image/jpeg"})


class Upstream:
    """Stub of the upstream client: records the requests and their concurrency per provider.

    ``reply(request)`` makes the answer; while ``gate`` is set and not released, every request
    waits in flight (a ``threading.Event`` when the proxy runs in a server thread). ``delay_s``,
    read when a request starts, is its round-trip once through the gate.
    """

    def __init__(self, reply: Callable[[FetchRequest], FetchResult] = _jpeg) -> None:
        self.reply = reply
        self.requests: list[FetchRequest] = []
        self.gate: asyncio.Event | threading.Event | None = None
        self.delay_s = 0.0
        self.in_flight: dict[str, int] = {}
        self.peak: dict[str, int] = {}

    async def __call__(self, request: FetchRequest) -> FetchResult:
        self.requests.append(request)
        delay_s = self.delay_s
        group = request.host_group
        self.in_flight[group] = self.in_flight.get(group, 0) + 1
        self.peak[group] = max(self.peak.get(group, 0), self.in_flight[group])
        try:
            while self.gate is not None and not self.gate.is_set():
                await asyncio.sleep(0.002)
            if delay_s:
                await asyncio.sleep(delay_s)
            return self.reply(request)
        finally:
            self.in_flight[group] -= 1


def _client(proxy: MapProxy) -> Any:
    app = FastAPI()
    app.include_router(proxy.router)
    return fakes.client_for(app)


async def _until(condition: Callable[[], bool], timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.005)


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []


# --- the cache ----------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_tile_is_fetched_once_then_served_from_the_cache(home: Path) -> None:
    up = Upstream()
    async with _client(MapProxy(fetch=up)) as c:
        r = await c.get("/api/map/BI/3/4/2")
        assert r.status_code == 200, r.text
        assert r.content == JPEG and r.headers["content-type"] == "image/jpeg"
        assert r.headers["cache-control"] == "max-age=86400"
        (request,) = up.requests
        assert request.url == tile_url(load_registry()["BI"], 4, 2, 3)
        assert request.host_group == "BI"
        cached = home / "mapcache" / "BI" / "3" / "4" / "2"
        assert _files(home / "mapcache") == [cached] and cached.read_bytes() == JPEG
        r = await c.get("/api/map/BI/3/4/2")
        assert r.status_code == 200 and r.content == JPEG
        assert r.headers["content-type"] == "image/jpeg"
        assert len(up.requests) == 1  # from the cache


@pytest.mark.anyio
async def test_a_source_of_the_users_is_cached_under_its_address(home: Path) -> None:
    """A source of the user's keeps its map tiles under its address as well as its code, and the
    page asks them with that folder in the URL (``?v=``, map.js ``tileVersion``), which the route
    takes as it takes any tile: the same name with another address is asked again, not served
    the first address's images from either cache."""
    up = Upstream()

    def mine(host: str) -> Provider:
        return Provider(
            code="Mine", url_template=f"https://{host}/{{zoom}}/{{x}}/{{y}}.jpg", max_zl=19,
            custom=True,
        )  # fmt: skip

    first, second = mine("a.example"), mine("b.example")
    for p in (first, second):
        async with _client(MapProxy(fetch=up, registry={"Mine": p})) as c:
            r = await c.get(f"/api/map/Mine/3/4/2?v={cache_name(p)}")
            assert r.status_code == 200, r.text
    assert [r.url for r in up.requests] == [
        "https://a.example/3/4/2.jpg",
        "https://b.example/3/4/2.jpg",
    ], "the second address is asked, not answered from the first one's cache"
    assert _files(home / "mapcache") == sorted(
        home / "mapcache" / cache_name(p) / "3" / "4" / "2" for p in (first, second)
    )


@pytest.mark.anyio
async def test_the_type_comes_from_the_bytes_and_the_provider_s_headers_are_sent(
    home: Path,
) -> None:
    keyed = Provider(
        code="Keyed",
        url_template="https://tiles.example/{zoom}/{x}/{y}?key=abc",
        max_zl=18,
        headers={"Authorization": "Bearer abc", "Referer": "https://tiles.example/"},
    )
    up = Upstream(lambda r: _result(r, 200, PNG, {"content-type": "application/octet-stream"}))
    async with _client(MapProxy(registry={"Keyed": keyed}, fetch=up)) as c:
        r = await c.get("/api/map/Keyed/18/5/6")
        assert r.status_code == 200 and r.content == PNG
        assert r.headers["content-type"] == "image/png"
        r = await c.get("/api/map/Keyed/18/5/6")
        assert r.headers["content-type"] == "image/png"  # read back from the cached bytes
    (request,) = up.requests
    assert request.url == "https://tiles.example/18/5/6?key=abc"
    assert request.headers == keyed.headers and request.host_group == "Keyed"
    assert sniff_image_type(JPEG) == "image/jpeg" and sniff_image_type(b"<html>") is None
    assert sniff_image_type(b"RIFF\x10\x00\x00\x00WEBPVP8 ") == "image/webp"


@pytest.mark.anyio
async def test_a_placeholder_or_a_404_is_remembered_as_nothing(home: Path) -> None:
    def reply(request: FetchRequest) -> FetchResult:
        if request.key[3] == 1:  # Bing's "no imagery here": HTTP 200 with its header
            headers = {"content-type": "image/png", "x-ve-tile-info": "no-tile"}
            return _result(request, 200, PNG, headers)
        return _result(request, 404, b"Not Found", {"content-type": "text/plain"})

    up = Upstream(reply)
    async with _client(MapProxy(fetch=up)) as c:
        for y in (1, 2):
            r = await c.get(f"/api/map/BI/5/10/{y}")
            assert r.status_code == 204 and r.content == b"", r.text
            assert r.headers["cache-control"] == "max-age=86400"
        folder = home / "mapcache" / "BI" / "5" / "10"
        assert _files(home / "mapcache") == [folder / "1.none", folder / "2.none"]
        assert (folder / "1.none").read_text() == "placeholder (header)\n"
        assert (folder / "2.none").read_text() == "404\n"
        for y in (1, 2):
            assert (await c.get(f"/api/map/BI/5/10/{y}")).status_code == 204
    assert len(up.requests) == 2


# --- validation ---------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_an_unknown_provider_is_404_and_an_index_out_of_range_422(home: Path) -> None:
    up = Upstream()
    async with _client(MapProxy(fetch=up)) as c:
        r = await c.get("/api/map/NOPE/3/4/2")
        assert r.status_code == 404
        err = r.json()["error"]
        assert err["code"] == "CFG_PROVIDER_UNKNOWN" and "NOPE" in err["message"] and err["remedy"]
        bad = ("BI/0/0/0", "BI/20/0/0", "JP/19/0/0", "BI/3/8/0", "BI/3/0/8", "BI/3/-1/0",
               "BI/3/0/abc", "BI/3/1.5/0", "BI/%C2%B2/0/0", "BI/3/0/99999999999")  # fmt: skip
        for path in bad:
            r = await c.get(f"/api/map/{path}")
            assert r.status_code == 422, path
            assert r.json()["error"]["code"] == "CFG_VALUE_INVALID", path
        r = await c.get("/api/map/JP/19/0/0")  # GSI serves up to 18
        assert "range 1..18" in r.json()["error"]["remedy"]
        assert up.requests == []
        for path in ("BI/1/1/1", "BI/19/524287/524287", "JP/18/0/262143"):
            assert (await c.get(f"/api/map/{path}")).status_code == 200, path
    assert len(up.requests) == 3


# --- upstream failures --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_an_upstream_failure_is_502_with_its_code_and_nothing_is_cached(home: Path) -> None:
    html = {"content-type": "text/html"}
    cases: dict[int, tuple[dict[str, Any], str]] = {  # x -> (answer, code)
        0: ({"status": 0, "error": "NET_TIMEOUT"}, "NET_TIMEOUT"),
        1: ({"status": 0, "error": "NET_CONNECTION_FAILED"}, "NET_CONNECTION_FAILED"),
        2: ({"status": 503, "body": b"busy", "error": "NET_SERVER_ERROR"}, "NET_SERVER_ERROR"),
        3: ({"status": 429, "error": "NET_RATE_LIMITED"}, "NET_RATE_LIMITED"),
        4: ({"status": 403, "body": b"denied"}, "NET_FORBIDDEN"),
        5: ({"status": 401, "body": b"token?"}, "NET_UNEXPECTED_STATUS"),
        6: ({"body": b"<html>a key is needed</html>", "headers": html}, "IMG_BAD_CONTENT_TYPE"),
        7: ({"body": JPEG[: len(JPEG) // 2], "headers": {"content-type": "image/jpeg"}},
            "IMG_TILE_CORRUPTED"),
    }  # fmt: skip
    up = Upstream(lambda r: _result(r, **cases[r.key[2]][0]))
    async with _client(MapProxy(fetch=up)) as c:
        for x, (_answer, code) in cases.items():
            r = await c.get(f"/api/map/BI/4/{x}/3")
            assert r.status_code == 502, (x, r.text)
            err = r.json()["error"]
            assert err["code"] == code and err["context"]["provider"] == "BI", x
            assert err["context"]["host"].endswith(".tiles.virtualearth.net") and err["remedy"]
            assert r.headers["cache-control"] == "no-store"
        assert _files(home / "mapcache") == []
        r = await c.get("/api/map/BI/4/0/3")  # asked upstream again, not remembered
        assert r.status_code == 502 and "timed out" in r.json()["error"]["message"]
    assert len(up.requests) == len(cases) + 1


@pytest.mark.anyio
async def test_a_provider_the_proxy_cannot_ask_fails_with_a_code_and_nothing_hangs(
    home: Path,
) -> None:
    registry = {
        "BI": load_registry()["BI"],
        "Ftp": Provider(code="Ftp", url_template="ftp://tiles.example/{zoom}/{x}/{y}", max_zl=19),
        "Crlf": Provider(
            code="Crlf",
            url_template="https://tiles.example/{zoom}/{x}/{y}.jpg",
            max_zl=19,
            headers={"X-Token": "abc\r\nHost: elsewhere.example"},
        ),
    }
    calls: list[FetchRequest] = []

    async def hang(request: FetchRequest) -> FetchResult:  # an upstream that never answers
        calls.append(request)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with _client(MapProxy(registry=registry, fetch=hang, deadline_s=0.2)) as c:
        t0 = time.monotonic()
        r = await c.get("/api/map/BI/3/4/2")
        assert time.monotonic() - t0 < 5.0
        assert r.status_code == 502 and r.json()["error"]["code"] == "NET_TIMEOUT"
        for code in ("Ftp", "Crlf"):
            r = await c.get(f"/api/map/{code}/3/4/2")
            assert r.status_code == 422, code
            err = r.json()["error"]
            assert err["code"] == "CFG_PROVIDER_DEFINITION_INVALID" and code in err["message"]
    assert len(calls) == 1 and _files(home / "mapcache") == []


# --- concurrency --------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_concurrent_requests_for_one_tile_share_one_fetch(home: Path) -> None:
    up = Upstream()
    up.gate = asyncio.Event()
    async with _client(MapProxy(fetch=up)) as c:
        tasks = [asyncio.create_task(c.get("/api/map/BI/12/2140/1490")) for _ in range(6)]
        await _until(lambda: len(up.requests) == 1)
        await asyncio.sleep(0.1)  # the five others reach the flight meanwhile
        up.gate.set()
        responses = await asyncio.gather(*tasks)
    assert [r.status_code for r in responses] == [200] * 6
    assert all(r.content == JPEG for r in responses)
    assert len(up.requests) == 1


@pytest.mark.anyio
async def test_at_most_8_requests_per_provider_and_never_above_its_max_in_flight(
    home: Path,
) -> None:
    few = Provider(code="Few", url_template="https://few.example/{zoom}/{x}/{y}", max_zl=19,
                   max_in_flight=3)  # fmt: skip
    up = Upstream()
    up.gate = asyncio.Event()
    async with _client(MapProxy(registry={"BI": load_registry()["BI"], "Few": few}, fetch=up)) as c:
        urls = [f"/api/map/BI/10/{x}/300" for x in range(20)]
        urls += [f"/api/map/Few/10/{x}/300" for x in range(10)]
        tasks = [asyncio.create_task(c.get(u)) for u in urls]
        await _until(lambda: up.in_flight.get("BI") == 8 and up.in_flight.get("Few") == 3)
        await asyncio.sleep(0.1)
        assert up.in_flight == {"BI": 8, "Few": 3}
        up.gate.set()
        responses = await asyncio.gather(*tasks)
    assert all(r.status_code == 200 for r in responses)
    assert up.peak == {"BI": 8, "Few": 3} and len(up.requests) == 30


# --- requests that go away ----------------------------------------------------------------------
#
# Leaflet asks for the tiles of every level a zoom passes through and aborts most of them.

ROUND_TRIP_S = 0.5
"""Long enough that the time a loaded runner takes to schedule the tasks stays small beside
it: at 0.25 s a correct run measured 0.57 s on a busy Windows runner (2026-09-19)."""


@pytest.mark.anyio
async def test_abandoned_requests_never_reach_upstream_and_the_next_tile_takes_one_round_trip(
    home: Path,
) -> None:
    up = Upstream()
    up.gate = asyncio.Event()
    proxy = MapProxy(fetch=up)
    async with _client(proxy) as c:
        shown = [asyncio.create_task(c.get(f"/api/map/BI/12/{x}/1000")) for x in range(8)]
        await _until(lambda: len(up.requests) == 8)  # every slot is busy
        passed = [asyncio.create_task(c.get(f"/api/map/BI/12/{x}/1001")) for x in range(40)]
        await _until(lambda: len(proxy.flights()) == 48)
        for task in passed:
            task.cancel()
        await asyncio.gather(*passed, return_exceptions=True)
        assert sorted(proxy.flights().values()) == [0] * 40 + [1] * 8
        up.delay_s = ROUND_TRIP_S  # every fetch that starts from now on takes a round-trip
        up.gate.set()
        assert [r.status_code for r in await asyncio.gather(*shown)] == [200] * 8
        t0 = time.monotonic()
        r = await c.get("/api/map/BI/12/2000/2000")
        elapsed = time.monotonic() - t0
        assert r.status_code == 200 and r.content == JPEG
        assert proxy.flights() == {}
    # Fetching the 40 abandoned tiles first would take 5 round-trips (2.5 s) before this one.
    assert elapsed < 3 * ROUND_TRIP_S, elapsed
    fetched = sorted(request.key[2:] for request in up.requests)
    assert fetched == [(x, 1000) for x in range(8)] + [(2000, 2000)]
    assert len(_files(home / "mapcache")) == 9


@pytest.mark.anyio
async def test_a_tile_still_wanted_by_one_request_is_fetched_and_abandoned_fetches_finish(
    home: Path,
) -> None:
    two = Provider(code="Two", url_template="https://two.example/{zoom}/{x}/{y}", max_zl=19,
                   max_in_flight=2)  # fmt: skip

    def reply(request: FetchRequest) -> FetchResult:
        if request.key[2] == 1:  # a body cut short
            return _result(request, 200, JPEG[: len(JPEG) // 2], {"content-type": "image/jpeg"})
        return _jpeg(request)

    up = Upstream(reply)
    up.gate = asyncio.Event()
    proxy = MapProxy(registry={"Two": two}, fetch=up)
    async with _client(proxy) as c:
        # Two fetches under way, then their requests go away: they finish all the same.
        under_way = [asyncio.create_task(c.get(f"/api/map/Two/10/{x}/0")) for x in (0, 1)]
        await _until(lambda: len(up.requests) == 2)
        # One tile asked for twice while both slots are busy; one of the two requests goes away.
        twice = [asyncio.create_task(c.get("/api/map/Two/10/5/5")) for _ in range(2)]
        await _until(lambda: proxy.flights().get("Two/10/5/5") == 2)
        for task in (*under_way, twice[0]):
            task.cancel()
        await asyncio.gather(*under_way, twice[0], return_exceptions=True)
        assert proxy.flights() == {"Two/10/0/0": 0, "Two/10/1/0": 0, "Two/10/5/5": 1}
        up.gate.set()
        r = await twice[1]
        assert r.status_code == 200 and r.content == JPEG
        await _until(lambda: proxy.flights() == {})
    assert sorted(request.key[2:] for request in up.requests) == [(0, 0), (1, 0), (5, 5)]
    folder = home / "mapcache" / "Two" / "10"
    assert _files(home / "mapcache") == [folder / "0" / "0", folder / "5" / "5"]  # not the cut one


@pytest.mark.anyio
async def test_a_free_slot_goes_to_the_most_recent_request(home: Path) -> None:
    one = Provider(code="One", url_template="https://one.example/{zoom}/{x}/{y}", max_zl=19,
                   max_in_flight=1)  # fmt: skip
    up = Upstream()
    up.gate = asyncio.Event()
    proxy = MapProxy(registry={"One": one}, fetch=up)
    async with _client(proxy) as c:
        tasks = {9: [asyncio.create_task(c.get("/api/map/One/10/9/0"))]}
        await _until(lambda: len(up.requests) == 1)  # the only slot is busy
        for x in (1, 2, 3, 4):  # queued in this order
            tasks[x] = [asyncio.create_task(c.get(f"/api/map/One/10/{x}/0"))]
            await _until(lambda x=x: f"One/10/{x}/0" in proxy.flights())
        tasks[1].append(asyncio.create_task(c.get("/api/map/One/10/1/0")))  # asked for again
        await _until(lambda: proxy.flights()["One/10/1/0"] == 2)
        (abandoned,) = tasks.pop(3)
        abandoned.cancel()
        await asyncio.gather(abandoned, return_exceptions=True)
        up.gate.set()
        responses = await asyncio.gather(*(t for waiting in tasks.values() for t in waiting))
        assert [r.status_code for r in responses] == [200] * 5
    assert [request.key[2] for request in up.requests] == [9, 1, 4, 2]  # 3: nobody waited


class _PassThrough(BaseHTTPMiddleware):
    """Stands for the application's guard, also a ``BaseHTTPMiddleware``: behind one,
    ``Request.is_disconnected()`` never turns true, and the proxy must still see clients leave."""

    async def dispatch(self, request: Any, call_next: Any) -> Any:
        return await call_next(request)


def _wait_for(condition: Callable[[], bool], timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.005)


@contextlib.contextmanager
def _uvicorn(app: Any) -> Iterator[int]:
    """uvicorn serving ``app`` on a loopback port, in a thread of its own; yields the port."""
    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_config=None))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        _wait_for(lambda: server.started or not thread.is_alive())
        assert server.started, "uvicorn did not start"
        yield sock.getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        sock.close()


def _ask(port: int, path: str) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", port), timeout=10.0)
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
    return s


def _status(s: socket.socket) -> int:
    with s:
        answer = b"".join(iter(lambda: s.recv(65536), b""))
    return int(answer.split(b" ", 2)[1])


def test_under_uvicorn_a_client_that_disconnects_stops_waiting_and_its_tile_is_not_fetched(
    home: Path,
) -> None:
    up = Upstream()
    up.gate = threading.Event()
    proxy = MapProxy(fetch=up)
    app = FastAPI()
    app.add_middleware(_PassThrough)
    app.include_router(proxy.router)
    with _uvicorn(app) as port:
        try:
            shown = [_ask(port, f"/api/map/BI/10/{x}/300") for x in range(8)]
            _wait_for(lambda: len(up.requests) == 8)  # every slot is busy
            passed = [_ask(port, f"/api/map/BI/10/{x}/301") for x in range(40)]
            _wait_for(lambda: len(proxy.flights()) == 48)
            for s in passed:
                s.close()  # how a browser aborts an image over HTTP/1.1
            _wait_for(lambda: sorted(proxy.flights().values()) == [0] * 40 + [1] * 8)
        finally:
            up.gate.set()  # nothing stays in flight to hold the server's shutdown
        assert [_status(s) for s in shown] == [200] * 8
        _wait_for(lambda: proxy.flights() == {})
    assert sorted(request.key[2:] for request in up.requests) == [(x, 300) for x in range(8)]
    assert len(_files(home / "mapcache")) == 8


# --- the real client, against a loopback server -------------------------------------------------


class _Backlog(ThreadingHTTPServer):
    request_queue_size = 64  # eight fetchers connect at once
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # a fetcher closed with its connection (the deadline test) is not a server error
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)


class _Tiles(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits: list[str]

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.hits.append(self.path)
        if self.path.startswith("/slow/"):
            time.sleep(1.0)
        missing = self.path.endswith("/404")
        body = b"no tile" if missing else JPEG
        try:
            self.send_response(404 if missing else 200)
            self.send_header("Content-Type", "text/plain" if missing else "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass  # the client went away (cancelled)


@pytest.fixture
def loopback() -> Iterator[tuple[str, list[str]]]:
    hits: list[str] = []
    server = _Backlog(("127.0.0.1", 0), type("Handler", (_Tiles,), {"hits": hits}))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", hits
    server.shutdown()
    server.server_close()


@pytest.mark.anyio
async def test_the_real_client_reuses_its_fetchers_and_closes_with_the_application(
    home: Path, loopback: tuple[str, list[str]]
) -> None:
    base, hits = loopback
    tiles = Provider(code="Loop", url_template=base + "/t/{zoom}/{x}/{y}", max_zl=19)
    proxy = MapProxy(registry={"Loop": tiles})
    client = proxy.client
    assert client is not None
    app = FastAPI()
    app.include_router(proxy.router)
    async with app.router.lifespan_context(app), fakes.client_for(app) as c:
        for wave in range(2):
            urls = [f"/api/map/Loop/10/{x}/{wave}" for x in range(12)]
            responses = await asyncio.gather(*(c.get(u) for u in urls))
            assert [r.status_code for r in responses] == [200] * 12
            assert all(r.content == JPEG for r in responses)
            assert 1 <= client.fetchers()["Loop"] <= 8  # lent again, not one per request
        assert len(hits) == 24
        responses = await asyncio.gather(*(c.get(f"/api/map/Loop/10/{x}/0") for x in range(12)))
        assert all(r.status_code == 200 for r in responses) and len(hits) == 24
        r = await c.get("/api/map/Loop/10/3/404")
        assert r.status_code == 204 and (home / "mapcache/Loop/10/3/404.none").is_file()
    assert client.closed and client.fetchers() == {}


@pytest.mark.anyio
async def test_a_fetch_cut_by_the_deadline_is_502_and_its_fetcher_is_not_lent_again(
    home: Path, loopback: tuple[str, list[str]]
) -> None:
    base, _hits = loopback
    slow = Provider(code="Slow", url_template=base + "/slow/{zoom}/{x}/{y}", max_zl=19)
    tiles = Provider(code="Loop", url_template=base + "/t/{zoom}/{x}/{y}", max_zl=19)
    proxy = MapProxy(registry={"Slow": slow, "Loop": tiles}, deadline_s=0.3)
    client = proxy.client
    assert client is not None
    try:
        async with _client(proxy) as c:
            r = await c.get("/api/map/Slow/10/1/1")
            assert r.status_code == 502 and r.json()["error"]["code"] == "NET_TIMEOUT"
            assert client.fetchers().get("Slow", 0) == 0
            r = await c.get("/api/map/Loop/10/1/1")
            assert r.status_code == 200 and r.content == JPEG
    finally:
        await proxy.aclose()
    assert client.fetchers() == {}


# --- in the application -------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_application_serves_the_map(home: Path) -> None:
    """``create_app`` includes the map router (integration of map-zones.md 8); ``map_fetch``
    keeps the request on the machine."""
    from orthostudio.api.app import create_app
    from orthostudio.api.jobs import JobManager

    manager = JobManager(jobs_dir=home / "jobs", build=fakes.FakeBuild(), env_factory=None)
    up = Upstream()
    app = create_app(
        env_factory=None, jobs=manager, settings_path=home / "config.toml", map_fetch=up
    )
    try:
        async with fakes.client_for(app) as c:
            r = await c.get("/api/map/BI/3/4/2")
            assert r.status_code == 200 and r.content == JPEG
            r = await c.get("/api/map/NOPE/3/4/2")
            assert r.status_code == 404
            err = r.json()["error"]
            assert err["code"] == "CFG_PROVIDER_UNKNOWN" and err["action"] == "settings"
            r = await c.get("/api/map/BI/3/4/2", headers={"Host": "evil.example"})
            assert r.status_code == 400  # the application's Host guard covers the map too
    finally:
        manager.close()
    assert (home / "mapcache" / "BI" / "3" / "4" / "2").read_bytes() == JPEG
    assert len(up.requests) == 1
