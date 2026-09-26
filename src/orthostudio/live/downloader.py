# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Imagery pieces downloaded one by one, the most urgent first, each delivered as soon as it
arrives.

One HTTP/2 session (curl_cffi) lives in a thread with its event loop, for the whole run: connections
stay warm. Up to ``max_in_flight`` requests run at once, taken from a priority queue, so a piece
asked for urgently overtakes a backlog. A piece that is slow or retried delays only the texture that
needs it: nothing waits for a whole batch, which is what made the first streamer stall for 40 s
behind one slow piece.

A 429 or 503 pauses every request to the provider for its Retry-After (5 s when it names none);
transport errors and other 5xx are retried after 0.5, 1, 2 s; "no imagery here" (the provider's
placeholder answer, 204 or 404) is reported as such, and so is a piece that failed every attempt.

A piece not there after HEDGE_S is asked of another of the provider's servers as well, and the first
good answer wins. Otherwise the last pieces of a start can hold X-Plane for half a minute: once, two
pieces kept LFMN waiting 30 s, and one of them never came.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import threading
import time
from collections.abc import Callable

from .cache import IMAGE, NO_IMAGERY, Key
from .providers import Provider, no_imagery, tile_url
from .providers import provider as provider_named

FAILED = 3
HEDGE_S = 2.0


class Downloader:
    def __init__(
        self,
        provider,
        on_piece: Callable[[Key, int, bytes | None], None],
        max_in_flight: int | None = None,
        timeout_s: float = 8.0,
        attempts: int = 4,
    ):
        """``provider``: a code of ``orthostudio.live.providers``, or a Provider. ``on_piece(key,
        status, body)``
        is called from the downloader's thread for every piece asked for."""
        self.p: Provider = provider_named(provider) if isinstance(provider, str) else provider
        self.on_piece = on_piece
        self.max_in_flight = max_in_flight or min(self.p.max_in_flight, 96)
        self.timeout_s = timeout_s
        self.attempts = attempts
        self.downloaded = self.bytes = self.no_imagery = self.failed = self.retries = (
            self.hedged
        ) = 0
        self.in_flight = 0
        self._paused_until = 0.0
        self._seq = itertools.count()
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="live-downloader", daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._error is not None:
            raise self._error

    # --- the caller's side --------------------------------------------------------------------

    def submit(self, key: Key, priority: float) -> None:
        """Ask for a piece; lower ``priority`` first."""
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (priority, next(self._seq), key))

    def queued(self) -> int:
        return self._queue.qsize()

    def close(self) -> None:
        if self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._stop.set)
            self._thread.join(timeout=10)

    # --- the downloader's thread --------------------------------------------------------------

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except BaseException as e:  # reported to the caller if it happens while starting
            self._error = e
            self._ready.set()

    async def _main(self) -> None:
        import certifi
        from curl_cffi.requests import AsyncSession

        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._stop = asyncio.Event()
        self._session = AsyncSession(
            max_clients=max(256, 2 * self.max_in_flight + 16),
            http_version="v2tls",
            allow_redirects=True,
            timeout=self.timeout_s,
            verify=certifi.where(),
        )
        workers = [asyncio.create_task(self._worker()) for _ in range(self.max_in_flight)]
        self._ready.set()
        await self._stop.wait()
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await self._session.close()

    async def _worker(self) -> None:
        while True:
            _, _, key = await self._queue.get()
            status, body = await self._fetch(key)
            with contextlib.suppress(Exception):  # the caller's problem, not the downloader's
                self.on_piece(key, status, body)

    async def _get(self, url: str):
        self.in_flight += 1
        try:
            return await self._session.get(url, headers=dict(self.p.headers))
        except asyncio.CancelledError:
            raise
        except Exception:  # timeout, refused connection, reset...
            return None
        finally:
            self.in_flight -= 1

    async def _hedged(self, x: int, y: int, z: int, attempt: int):
        """One request, and the same of another server if the first is slow: the first good answer
        (or the last bad one)."""
        first = asyncio.ensure_future(self._get(tile_url(self.p, x, y, z, 2 * attempt)))
        done, _ = await asyncio.wait({first}, timeout=HEDGE_S)
        if done:
            return first.result()
        self.hedged += 1
        pending = {
            first,
            asyncio.ensure_future(self._get(tile_url(self.p, x, y, z, 2 * attempt + 1))),
        }
        answer = None
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    r = task.result()
                    if r is not None and r.status_code == 200 and r.content:
                        return r
                    answer = answer or r
            return answer
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def _fetch(self, key: Key):
        z, x, y = key
        if z > self.p.max_zl:
            return NO_IMAGERY, None
        for attempt in range(self.attempts):
            wait = self._paused_until - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            r = await self._hedged(x, y, z, attempt)
            if r is not None:
                if r.status_code == 200 and r.content:
                    if no_imagery(self.p, dict(r.headers.items())):
                        self.no_imagery += 1
                        return NO_IMAGERY, None
                    self.downloaded += 1
                    self.bytes += len(r.content)
                    return IMAGE, r.content
                if r.status_code in (204, 404):
                    self.no_imagery += 1
                    return NO_IMAGERY, None
                if r.status_code in (429, 503):
                    after = r.headers.get("retry-after", "")
                    delay = float(after) if after.strip().isdigit() else 5.0
                    self._paused_until = max(
                        self._paused_until, time.monotonic() + min(delay, 60.0)
                    )
            self.retries += 1
            await asyncio.sleep(0.5 * 2**attempt)
        self.failed += 1
        return FAILED, None
