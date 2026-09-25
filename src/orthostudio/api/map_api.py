"""The page's base map: ``GET /api/map/{provider}/{z}/{x}/{y}`` (``docs/specs/map-zones.md`` 6).

A caching proxy in front of the imagery providers of the registry: the page never contacts
another origin, and it shows the imagery a build would download.

- ``provider`` is a registry code (else ``404 CFG_PROVIDER_UNKNOWN``); ``z`` is 1 to
  ``min(19, max_zl)``, ``x`` and ``y`` are in ``[0, 2**z)`` (else ``422 CFG_VALUE_INVALID``).
- A tile is served from ``<data folder>/mapcache/<provider>/<z>/<x>/<y>`` when present; otherwise it
  is fetched once through ``orthostudio.net.fetch`` (``tile_url``, the provider's headers, its code
  as the host group), written atomically and served. The cache keeps the bytes only: the
  ``Content-Type`` is read back from the magic bytes (JPEG, PNG, WebP, GIF), with
  ``Cache-Control: max-age=86400``. While the data folder chosen in Settings is missing (its disk
  unplugged), the map is served without the cache: nothing is written on the computer's own disk.
- A placeholder (``is_placeholder``) or a ``404`` is remembered as ``<y>.none`` and answered
  ``204 No Content``: the page draws nothing there.
- An upstream failure is a ``502`` carrying the error of the fetch (``NET_*``, or ``IMG_*`` for
  a body that is not a complete image); nothing is cached. A provider the proxy cannot ask
  (a URL that is not http or https, a header that cannot be sent) is a ``422``, and no fetch
  outlives ``UPSTREAM_DEADLINE_S``: a tile fails with a code, it never hangs.
- At most 8 upstream requests per provider are in flight, never more than its
  ``max_in_flight``; concurrent requests for one tile share one fetch.
- A request waits for its tile only while its client is there. Leaflet asks for the tiles of
  every level a zoom passes through and aborts most of them: a request whose client goes away
  stops waiting, and a tile left without requests is not fetched when its turn comes. A slot
  that frees up goes to the most recent request, so the tiles on screen start first. A fetch
  already under way when its last request leaves finishes, and is cached like any other.

The upstream client (:class:`TileClient`) is one object for the life of the application; the
router's lifespan closes it when the server stops. ``Fetcher.fetch_many`` runs one batch at a
time, and a single ``Fetcher`` would hold every tile of the map behind the slowest tile of the
batch before it, so the client keeps long-lived fetchers per provider, as many as requests in
flight (at most 8), each lent to one request at a time and reused with its open connections.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.types import Receive

from orthostudio.api.jobs import error_json
from orthostudio.errors import OsxpError, wrap
from orthostudio.fsutil import atomic_write_bytes
from orthostudio.home import data_root_missing, default_mapcache_root
from orthostudio.imagery.chunks import normalize_content_type
from orthostudio.imagery.providers import (
    Provider,
    cache_name,
    is_placeholder,
    load_registry,
    tile_url,
)
from orthostudio.net.fetch import Fetcher, FetchRequest, FetchResult
from orthostudio.textures.assemble import image_body_complete

__all__ = [
    "CACHE_CONTROL",
    "MAPCACHE_DIR",
    "MAX_MAP_ZL",
    "NONE_SUFFIX",
    "PROVIDER_CONCURRENCY",
    "UPSTREAM_DEADLINE_S",
    "MapProxy",
    "TileClient",
    "TileFetch",
    "map_router",
    "mapcache_root",
    "sniff_image_type",
    "unservable",
]

log = logging.getLogger("orthostudio.api.map")

MAPCACHE_DIR = "mapcache"
"""The cache directory in the data folder (``osxp clean --images`` empties it)."""

MAX_MAP_ZL = 19
"""Deepest zoom level the map asks for, whatever the provider's ``max_zl``."""

PROVIDER_CONCURRENCY = 8
"""Upstream requests in flight per provider (and never more than its ``max_in_flight``)."""

CACHE_CONTROL = "max-age=86400"
NONE_SUFFIX = ".none"

UPSTREAM_DEADLINE_S = 30.0
"""Bound on one upstream fetch, retries and pauses included."""

_CLIENT_GONE = 499
"""Status of the answer to a request whose client has gone away (nginx's "client closed
request"); the server drops it, nobody reads it."""

# A map tile is worth one retry and a short 429 pause, not the minutes a build may spend on a
# texture (``docs/specs/net-download.md`` R2-R4). Worst case with these values: about 12 s.
MAP_TIMEOUT_S = 10.0
MAP_HEDGE_AFTER_S = 3.0
MAP_MAX_ATTEMPTS = 2
MAP_MAX_PUSHBACKS = 1
MAP_PUSHBACK_BUDGET_S = 10.0

TileFetch = Callable[[FetchRequest], Awaitable[FetchResult]]
"""One upstream request; :meth:`TileClient.fetch` unless a test injects its own."""

_TileKey = tuple[str, int, int, int]
"""A map tile: provider code, zoom level, column, row."""

_DIGITS = re.compile(r"[0-9]{1,10}")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def sniff_image_type(body: bytes) -> str | None:
    """Media type of an image from its magic bytes (JPEG, PNG, WebP, GIF), else ``None``.

    The cache keeps the bytes alone, so the type is read back from them; a server that labels a
    JPEG ``application/octet-stream`` is served as ``image/jpeg`` all the same.
    """
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "image/webp"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def unservable(p: Provider) -> OsxpError | None:
    """Why the proxy cannot ask ``p`` for tiles (``CFG_PROVIDER_DEFINITION_INVALID``), or ``None``.

    Only ``http`` and ``https`` URLs are fetched (libcurl would read ``file://`` as well), and
    every header the provider declares must be sendable as it is.
    """
    field = reason = ""
    scheme = urlsplit(tile_url(p, 0, 0, 1)).scheme.lower()
    if scheme not in ("http", "https"):
        field = "url_template"
        reason = f"its URL scheme {scheme or '(none)'!r} is not http or https"
    for name, value in p.headers.items():
        if not reason and (not _HEADER_NAME.fullmatch(name) or any(c in value for c in "\r\n\x00")):
            field, reason = "headers", f"its header {name!r} cannot be sent"
    if not reason:
        return None
    return OsxpError(
        "CFG_PROVIDER_DEFINITION_INVALID",
        context={
            "path": f"provider {p.code}",
            "field": field,
            "reason": reason,
            "provider": p.code,
        },
        message=f"Provider {p.code} cannot be shown on the map: {reason}.",
        remedy="Fix the provider definition (an http or https URL template, plain request "
        "headers) or choose another provider.",
    )


def _error_response(err: OsxpError, status: int) -> JSONResponse:
    """The API's error shape (``app.py``): ``{"error": error_json(err)}`` with its status."""
    return JSONResponse(
        {"error": error_json(err)}, status_code=status, headers={"Cache-Control": "no-store"}
    )


@dataclass(frozen=True, slots=True)
class _Answer:
    """What the proxy answers for one tile: an image, nothing (``204``), or an error."""

    kind: Literal["image", "none", "error"]
    body: bytes = b""
    content_type: str = ""
    note: str = ""
    error: OsxpError | None = None
    status: int = 0

    def response(self) -> Response:
        if self.kind == "image":
            headers = {"Cache-Control": CACHE_CONTROL}
            return Response(self.body, media_type=self.content_type, headers=headers)
        if self.kind == "none":
            return Response(status_code=204, headers={"Cache-Control": CACHE_CONTROL})
        assert self.error is not None
        return _error_response(self.error, self.status)


def _failed(err: OsxpError) -> _Answer:
    return _Answer("error", error=err, status=500 if err.code == "SYS_INTERNAL_ERROR" else 502)


def _index(name: str, raw: str, lo: int, hi: int) -> int:
    value = int(raw) if _DIGITS.fullmatch(raw) else None
    if value is None or not lo <= value <= hi:
        raise OsxpError(
            "CFG_VALUE_INVALID",
            context={"name": name, "value": raw[:32], "type": "integer", "range": f"{lo}..{hi}"},
        )
    return value


# --- the disk cache ---------------------------------------------------------------------------


def _marker(path: Path) -> Path:
    return path.with_name(path.name + NONE_SUFFIX)


def mapcache_root() -> Path | None:
    """``<data folder>/mapcache``; ``None`` while the data folder is missing (disk unplugged)."""
    return None if data_root_missing() is not None else default_mapcache_root()


def _read_cache(path: Path | None) -> _Answer | None:
    """The cached answer of a tile, ``None`` when it must be fetched (always without a cache).

    A file that is not an image (a copy damaged by hand) counts as absent: it is fetched again
    and replaced.
    """
    if path is None:
        return None
    try:
        body = path.read_bytes()
    except OSError:
        body = b""
    content_type = sniff_image_type(body)
    if content_type is not None:
        return _Answer("image", body, content_type)
    if _marker(path).is_file():
        return _Answer("none")
    return None


def _write_cache(path: Path | None, answer: _Answer) -> None:
    """Remember an image or a ``.none`` marker; a cache that cannot be written is only logged."""
    if path is None:
        return
    try:
        if answer.kind == "image":
            atomic_write_bytes(path, answer.body)
            _marker(path).unlink(missing_ok=True)
        elif answer.kind == "none":
            atomic_write_bytes(_marker(path), answer.note.encode("utf-8") + b"\n")
    except OSError as exc:
        log.warning("map cache: %s not written (%s)", path, exc)


# --- the upstream client ----------------------------------------------------------------------


class TileClient:
    """The proxy's upstream client, one per application (see the module docstring).

    ``fetch`` lends an idle ``Fetcher`` of the request's host group, or creates one, for one
    ``fetch_many`` run, then keeps it for the next request with its connections. The number of
    fetchers of a group is the number of its requests in flight, which the proxy bounds. A run
    that is cancelled or raises leaves transfers behind, so its fetcher is closed, not lent
    again.
    """

    def __init__(
        self,
        *,
        timeout_s: float = MAP_TIMEOUT_S,
        hedge_after_s: float = MAP_HEDGE_AFTER_S,
        max_attempts: int = MAP_MAX_ATTEMPTS,
        max_pushbacks: int = MAP_MAX_PUSHBACKS,
        pushback_budget_s: float = MAP_PUSHBACK_BUDGET_S,
    ) -> None:
        self.timeout_s = timeout_s
        self._make = functools.partial(
            Fetcher,
            max_in_flight=1,
            start_in_flight=1,
            hedge_after_s=hedge_after_s,
            timeout_s=timeout_s,
            max_attempts=max_attempts,
            max_pushbacks=max_pushbacks,
            pushback_budget_s=pushback_budget_s,
        )
        self._idle: dict[str, list[Fetcher]] = {}
        self._fetchers: dict[Fetcher, str] = {}
        self._closing: set[asyncio.Task[None]] = set()
        self.closed = False

    def fetchers(self) -> dict[str, int]:
        """Open fetchers per host group (tests, diagnostics)."""
        counts: dict[str, int] = {}
        for group in self._fetchers.values():
            counts[group] = counts.get(group, 0) + 1
        return counts

    async def fetch(self, request: FetchRequest) -> FetchResult:
        """Run ``request`` on a fetcher of its host group; ``SYS_CANCELLED`` once closed."""
        if self.closed:
            return FetchResult(request.key, 0, b"", {}, 0.0, 0, False, "SYS_CANCELLED")
        idle = self._idle.setdefault(request.host_group, [])
        if idle:
            fetcher = idle.pop()
        else:
            fetcher = self._make()
            self._fetchers[fetcher] = request.host_group
        try:
            (result,) = await fetcher.fetch_many([request])
        except BaseException:
            if fetcher in self._fetchers:  # else aclose() has closed it already
                self._discard(fetcher)
            raise
        if not self.closed and fetcher in self._fetchers:
            idle.append(fetcher)
        return result

    def _discard(self, fetcher: Fetcher) -> None:
        del self._fetchers[fetcher]
        task = asyncio.get_running_loop().create_task(fetcher.aclose())
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    async def aclose(self) -> None:
        """Cancel the transfers in flight and close every fetcher."""
        self.closed = True
        fetchers = list(self._fetchers)
        self._fetchers.clear()
        self._idle.clear()
        closing = [f.aclose() for f in fetchers]
        await asyncio.gather(*closing, *list(self._closing), return_exceptions=True)


# --- flights and slots ------------------------------------------------------------------------


@dataclass(eq=False, slots=True)
class _Flight:
    """One tile on its way: the task that answers it and the number of requests waiting for it."""

    waiters: int = 0
    task: asyncio.Task[_Answer | None] = field(init=False)


class _Slots:
    """The upstream slots of one provider: at most ``size`` of its flights fetch at once.

    A slot that frees up goes to the flight asked for most recently (LIFO), and a queued flight
    asked for again moves to the top: after a zoom, the tiles on screen start before those of
    the levels the page passed through. A flight whose requests have all gone away still takes
    its turn, and gives the slot back at once (:meth:`MapProxy._fly`).
    """

    def __init__(self, size: int) -> None:
        self.size = size
        self.busy = 0
        self._queue: dict[_Flight, asyncio.Future[None]] = {}  # oldest first

    async def acquire(self, flight: _Flight) -> None:
        # A flight queues only when every slot is busy, and ``busy`` drops only once nobody is
        # queued: a free slot never jumps the queue.
        if self.busy < self.size:
            self.busy += 1
            return
        ticket: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._queue[flight] = ticket
        try:
            await ticket
        except BaseException:
            if self._queue.get(flight) is ticket:
                del self._queue[flight]
            elif ticket.done() and not ticket.cancelled():
                self.release()  # the slot came with the cancellation: pass it on
            raise

    def prefer(self, flight: _Flight) -> None:
        """Move ``flight`` to the top of the queue, if it is queued: it was just asked for again."""
        ticket = self._queue.pop(flight, None)
        if ticket is not None:
            self._queue[flight] = ticket

    def release(self) -> None:
        while self._queue:
            _flight, ticket = self._queue.popitem()  # the most recent
            if not ticket.done():
                ticket.set_result(None)  # the slot changes hands, ``busy`` stays
                return
        self.busy -= 1


async def _client_gone(receive: Receive) -> None:
    """Return once the client of a request has gone away (the ASGI ``http.disconnect``).

    uvicorn sends that message as soon as the connection closes, which is how a browser aborts
    an image over HTTP/1.1. It is awaited in a task of its own: ``Request.is_disconnected()``,
    polled, never sees it behind a ``BaseHTTPMiddleware``, which the application's guard is.
    Under ``httpx.ASGITransport`` a request that goes away is cancelled instead, and this task
    is cancelled with it.
    """
    while (await receive())["type"] != "http.disconnect":
        pass


# --- the proxy --------------------------------------------------------------------------------


class MapProxy:
    """State of the map endpoint: registry, cache root, flights in progress, upstream client.

    ``home`` is called at each request (``$OSXP_HOME`` may change between tests); ``registry``
    defaults to the embedded providers, loaded once; ``fetch`` replaces the upstream client
    (then ``client`` is ``None``). ``router`` is the ``APIRouter`` to include in the
    application; its lifespan calls :meth:`aclose`.
    """

    def __init__(
        self,
        cache_root: Callable[[], Path | None] = mapcache_root,
        *,
        registry: Mapping[str, Provider] | None = None,
        fetch: TileFetch | None = None,
        deadline_s: float = UPSTREAM_DEADLINE_S,
    ) -> None:
        self._cache_root = cache_root
        self._registry: dict[str, Provider] | None = None if registry is None else dict(registry)
        self.client: TileClient | None
        self._fetch: TileFetch
        if fetch is None:
            self.client = TileClient()
            self._fetch = self.client.fetch
        else:
            self.client = None
            self._fetch = fetch
        self._deadline_s = deadline_s
        self._timeout_s = self.client.timeout_s if self.client is not None else deadline_s
        self._limits: dict[str, _Slots] = {}
        self._flights: dict[_TileKey, _Flight] = {}
        self._unservable: dict[str, OsxpError | None] = {}
        self.router = self._build_router()

    def providers(self) -> Mapping[str, Provider]:
        if self._registry is not None:  # given by a test
            return self._registry
        # read each time: a source the user adds shows at once (load_registry keeps the rest)
        return load_registry()

    def cache_path(self, p: Provider, z: int, x: int, y: int) -> Path | None:
        """``<cache root>/<folder>/<z>/<x>/<y>`` (the marker adds ``.none``), ``None`` without a
        cache root. The folder is the code, and for a source of the user's its address as well
        (``cache_name``), so the map never shows the images of an address it no longer has."""
        root = self._cache_root()
        return None if root is None else root / cache_name(p) / str(z) / str(x) / str(y)

    def _lookup(
        self, p: Provider, zl: int, col: int, row: int
    ) -> tuple[Path | None, _Answer | None]:
        path = self.cache_path(p, zl, col, row)  # a stat of the data folder: off the event loop
        return path, _read_cache(path)

    def flights(self) -> dict[str, int]:
        """The tiles on their way and the requests waiting for each, ``{"BI/12/2140/1490": 2}``
        (tests, diagnostics). A queued tile whose requests have all gone away counts ``0``."""
        items = list(self._flights.items())
        return {"/".join(map(str, key)): flight.waiters for key, flight in items}

    # -- the endpoint --------------------------------------------------------------------------

    async def tile(
        self, provider: str, z: str, x: str, y: str, receive: Receive | None = None
    ) -> Response:
        """``GET /api/map/{provider}/{z}/{x}/{y}``.

        ``receive`` is the request's ASGI channel: when given, a client that goes away while
        its tile is on its way stops waiting for it (:meth:`_join`).
        """
        p = self.providers().get(provider)
        if p is None:
            err = OsxpError("CFG_PROVIDER_UNKNOWN", context={"provider": provider[:64]})
            return _error_response(err, 404)
        try:
            zl = _index("z", z, 1, min(MAX_MAP_ZL, p.max_zl))
            col = _index("x", x, 0, 2**zl - 1)
            row = _index("y", y, 0, 2**zl - 1)
        except OsxpError as exc:
            return _error_response(exc, 422)
        if p.code not in self._unservable:
            self._unservable[p.code] = unservable(p)
        problem = self._unservable[p.code]
        if problem is not None:
            return _error_response(problem, 422)
        path, answer = await asyncio.to_thread(self._lookup, p, zl, col, row)
        if answer is None:
            answer = await self._join(p, zl, col, row, path, receive)
            if answer is None:  # the client has gone away: the server drops this answer
                return Response(status_code=_CLIENT_GONE, headers={"Cache-Control": "no-store"})
        return answer.response()

    async def _join(
        self,
        p: Provider,
        zl: int,
        col: int,
        row: int,
        path: Path | None,
        receive: Receive | None,
    ) -> _Answer | None:
        """This request's answer, from the flight of its tile (started when there is none).

        The request is one of the flight's waiters until the answer comes or its client goes
        away (``None``, when ``receive`` is watched). Leaving never cancels the fetch the other
        waiters share; a flight left without waiters is not fetched when its turn comes.
        """
        loop = asyncio.get_running_loop()
        key = (p.code, zl, col, row)
        flight = self._flights.get(key)
        if flight is None:
            flight = _Flight()
            flight.task = loop.create_task(self._fly(flight, p, zl, col, row, path))
            flight.task.add_done_callback(functools.partial(self._landed, key, flight))
            self._flights[key] = flight
        else:
            self._limit(p).prefer(flight)
        flight.waiters += 1
        gone = None if receive is None else loop.create_task(_client_gone(receive))
        try:
            if gone is None:
                return await asyncio.shield(flight.task)
            await asyncio.wait((flight.task, gone), return_when=asyncio.FIRST_COMPLETED)
            if flight.task.done():
                return flight.task.result()
            gone.result()  # the error of a receive() that failed, if that is why it returned
            return None
        finally:
            flight.waiters -= 1
            if gone is not None:
                gone.cancel()

    def _landed(self, key: _TileKey, flight: _Flight, task: asyncio.Task[_Answer | None]) -> None:
        if self._flights.get(key) is flight:
            del self._flights[key]
        if not task.cancelled():
            task.exception()  # retrieved even when every waiter went away

    def _unwanted(self, key: _TileKey, flight: _Flight) -> bool:
        """Whether every request for the flight's tile has gone away. The flight then leaves the
        table at once: a new request for the tile starts a flight of its own."""
        if flight.waiters > 0:
            return False
        if self._flights.get(key) is flight:
            del self._flights[key]
        return True

    def _limit(self, p: Provider) -> _Slots:
        slots = self._limits.get(p.code)
        if slots is None:
            slots = _Slots(min(PROVIDER_CONCURRENCY, p.max_in_flight))
            self._limits[p.code] = slots
        return slots

    async def _fly(
        self, flight: _Flight, p: Provider, zl: int, col: int, row: int, path: Path | None
    ) -> _Answer | None:
        """Fetch the tile once its provider has a free slot; ``None`` when nobody waits for it."""
        key = (p.code, zl, col, row)
        request = FetchRequest(
            key=key,
            url=tile_url(p, col, row, zl),
            headers=dict(p.headers),
            host_group=p.code,
        )
        slots = self._limit(p)
        await slots.acquire(flight)
        try:
            if self._unwanted(key, flight):
                return None
            # A flight that landed between this request's miss and now has written the tile.
            cached = await asyncio.to_thread(_read_cache, path)
            if cached is not None:
                return cached
            if self._unwanted(key, flight):  # its last request went away during the read
                return None
            try:
                async with asyncio.timeout(self._deadline_s):
                    result = await self._fetch(request)
            except TimeoutError:
                context = {**self._context(p, request), "timeout": self._deadline_s}
                return _failed(OsxpError("NET_TIMEOUT", context=context))
            except OsxpError as exc:
                return _failed(exc)
            except Exception as exc:
                log.exception("map: fetching %s failed", request.url)
                return _failed(wrap(exc))
        finally:
            slots.release()
        answer = self._classify(p, request, result)
        if answer.kind != "error":
            await asyncio.to_thread(_write_cache, path, answer)
        return answer

    # -- what an upstream answer means ---------------------------------------------------------

    @staticmethod
    def _context(p: Provider, request: FetchRequest) -> dict[str, Any]:
        _code, zl, col, row = request.key
        where = f"{zl}/{col}/{row}"
        host = urlsplit(request.url).hostname or request.url
        return {"provider": p.code, "host": host, "url": request.url, "tile": where, "chunk": where}

    def _classify(self, p: Provider, request: FetchRequest, r: FetchResult) -> _Answer:
        context = {**self._context(p, request), "status": r.status}
        if r.error is not None:
            return _failed(self._fetch_error(r.error, context, r))
        if r.status == 404:
            return _Answer("none", note="404")
        if r.status != 200:
            code = "NET_FORBIDDEN" if r.status == 403 else "NET_UNEXPECTED_STATUS"
            return _failed(OsxpError(code, context=context))
        signal = is_placeholder(p, r.headers, r.body)
        if signal is not None:
            if signal == "size":
                tile, size = context["tile"], len(r.body)
                log.info("map: %s %s is a placeholder by its size (%d B)", p.code, tile, size)
            return _Answer("none", note=f"placeholder ({signal})")
        content_type = sniff_image_type(r.body)
        declared = normalize_content_type(r.headers.get("content-type", ""))
        if content_type is None and not declared.startswith("image/"):
            context["content_type"] = declared or "no content type"
            return _failed(OsxpError("IMG_BAD_CONTENT_TYPE", context=context))
        if content_type is None or not image_body_complete(r.body):
            return _failed(OsxpError("IMG_TILE_CORRUPTED", context=context))
        return _Answer("image", r.body, content_type)

    def _fetch_error(self, code: str, context: dict[str, Any], r: FetchResult) -> OsxpError:
        attempts = f"no answer after {r.attempts} attempt(s)"
        context = {**context, "timeout": self._timeout_s, "reason": attempts}
        if code == "SYS_CANCELLED":
            return OsxpError(
                code,
                context=context,
                message=f"The map request to {context['host']} was cancelled.",
                remedy="Nothing to do: the page asks for the tile again when it shows it.",
            )
        try:
            return OsxpError(code, context=context)
        except ValueError:  # a code outside the registry, from an injected fetch
            return OsxpError("NET_CONNECTION_FAILED", context={**context, "reason": code})

    # -- life cycle ----------------------------------------------------------------------------

    def _build_router(self) -> APIRouter:
        @contextlib.asynccontextmanager
        async def lifespan(_app: Any) -> AsyncIterator[None]:
            try:
                yield
            finally:
                try:
                    await self.aclose()
                except Exception:
                    log.exception("map: closing the upstream client failed")

        async def map_tile(provider: str, z: str, x: str, y: str, request: Request) -> Response:
            return await self.tile(provider, z, x, y, request.receive)

        router = APIRouter(lifespan=lifespan)
        router.add_api_route(
            "/api/map/{provider}/{z}/{x}/{y}",
            map_tile,
            methods=["GET"],
            response_class=Response,
            name="map_tile",
        )
        return router

    async def aclose(self) -> None:
        """Stop the flights in progress and close the upstream client."""
        flights = [flight.task for flight in self._flights.values()]
        for task in flights:
            task.cancel()
        if flights:
            await asyncio.gather(*flights, return_exceptions=True)
        if self.client is not None:
            await self.client.aclose()


def map_router(
    cache_root: Callable[[], Path | None] = mapcache_root,
    *,
    registry: Mapping[str, Provider] | None = None,
    fetch: TileFetch | None = None,
) -> APIRouter:
    """The router of ``GET /api/map/{provider}/{z}/{x}/{y}``: ``app.include_router(map_router())``.

    See :class:`MapProxy` for the arguments. Including the router adds its lifespan to the
    application's, which closes the upstream client when the server stops.
    """
    return MapProxy(cache_root, registry=registry, fetch=fetch).router
