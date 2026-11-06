# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The other sources' pieces a search or a check reads (``clouds.md`` 4.2).

Each source's pieces are kept like a build's parent tiles (``ParentCache``), so what one search
or check fetched the next reads from the disk. What a source answered is said per piece: a
``picture``; ``no_picture`` (none there, a placeholder); ``did_not_answer`` (a timeout, the
network, a server error); ``refused`` (403, 429, pushback); ``not_a_picture`` (a page instead).
Only a picture or none is kept: asked again, the others may answer (:data:`ASK_AGAIN`). Laying
never asks a source: it lays frozen parts (``textures/clouds.py``).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from orthostudio.errors import OsxpError
from orthostudio.imagery.chunks import ChunkStatus
from orthostudio.imagery.providers import Provider, cache_name, tile_url
from orthostudio.model import TileRef
from orthostudio.net import Fetcher, FetchRequest, FetchResult
from orthostudio.pipeline.parents import ParentCache
from orthostudio.pipeline.textures import chunk_entry_for

__all__ = ["ANSWERS", "ASK_AGAIN", "PROPOSABLE", "SourcePieces", "other_sources"]

ANSWERS = (
    "clear",
    "cloudy",
    "same_photo",
    "no_picture",
    "did_not_answer",
    "refused",
    "not_a_picture",
)
"""What a source can answer under a cloud: what it has there (clear, cloudy), the tile's own photo,
no picture, or what a piece of it said (``SourcePieces.kind``); each has its words in the page
(``clouds.answer.<answer>``)."""
ASK_AGAIN = frozenset({"did_not_answer", "refused", "not_a_picture"})
"""The answers asking the source again may change: what a piece of it said, not what it has."""
PROPOSABLE = frozenset({"clear", "cloudy"}) | ASK_AGAIN
"""The answers under which a source may be chosen for a cloud: what it has there, or what asking
it again may change. The same photo would bring the cloud back; with no picture, nothing to lay."""

Key = tuple[int, int, int]
"""``(x, y, zl)`` of a source's piece."""


def _failed_kind(r: FetchResult) -> str:
    """What a piece that is neither a picture nor none answered: refused for now (401, 403, 429,
    the server's pushback), something that is not a picture (a page, another status), or no
    answer (a timeout, the network, a server error)."""
    if r.error == "NET_RATE_LIMITED" or r.status in (401, 403, 429):
        return "refused"
    if r.error is None and r.status:
        return "not_a_picture"
    return "did_not_answer"


def other_sources(tile: TileRef, own: str, registry: Mapping[str, Provider]) -> dict[str, Provider]:
    """Every source of the registry covering ``tile`` but its own (and the one its own is):
    a source of one country only where it reaches; one that is another (``same_as``) once."""
    mine = registry[own].same_as if own in registry else None
    return {
        c: p
        for c, p in registry.items()
        if c not in (own, mine)
        and not (p.same_as and p.same_as in registry)
        and p.covers(tile.lat, tile.lon)
    }


class SourcePieces:
    """The pieces of some sources, fetched once and read from the disk after."""

    def __init__(self, chunks_root: Path, providers: Mapping[str, Provider]) -> None:
        self.providers = dict(providers)
        self.caches = {
            c: ParentCache(chunks_root, cache_name(p), fsync=False)
            for c, p in self.providers.items()
        }
        self.failed: dict[tuple[str, Key], str] = {}
        """What a piece asked in this run answered when it is neither a picture nor none."""

    def body(self, code: str, key: Key) -> bytes | None:
        """The picture the source has at ``key``, or ``None``."""
        hit = self.caches[code].lookup(*key)
        return None if hit is None else hit.body

    def kind(self, code: str, key: Key) -> str:
        """What the source answered at ``key`` (module doc), ``did_not_answer`` when never asked."""
        if (code, key) in self.failed:
            return self.failed[(code, key)]
        hit = self.caches[code].lookup(*key)
        if hit is None:
            return "did_not_answer"
        return "no_picture" if hit.body is None else "picture"

    def fetch(
        self,
        todo: Mapping[str, Iterable[Key]],
        *,
        again: bool = False,
        cancel: threading.Event | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Fetch ``todo`` (``source -> keys``) the disk does not hold; ``again`` asks once more
        those it holds as no picture (Check again: a source may have one now). Stopped through
        ``cancel``, ``SYS_CANCELLED``: what was not fetched is no answer of the source."""
        wanted: dict[str, list[Key]] = {}
        for code, keys in todo.items():
            if code not in self.caches:
                continue
            mine = [
                k
                for k in sorted(set(keys))
                if (hit := self.caches[code].lookup(*k)) is None or (again and hit.body is None)
            ]
            if mine:
                wanted[code] = mine
        if wanted:
            asyncio.run(self._fetch(wanted, cancel, progress))
        if cancel is not None and cancel.is_set():
            raise OsxpError("SYS_CANCELLED")

    async def _fetch(
        self,
        wanted: Mapping[str, list[Key]],
        cancel: threading.Event | None,
        progress: Callable[[int, int], None] | None,
    ) -> None:
        total, done = sum(len(v) for v in wanted.values()), 0
        stop = asyncio.Event()

        async def watch() -> None:  # a threading event relayed to the fetcher's loop
            while cancel is not None and not cancel.is_set():
                await asyncio.sleep(0.2)
            stop.set()

        async def one(code: str, keys: list[Key]) -> None:
            nonlocal done
            provider, cache = self.providers[code], self.caches[code]

            def keep(r: FetchResult) -> None:
                nonlocal done
                done += 1
                if progress is not None:
                    progress(done, total)
                entry = chunk_entry_for(provider, r)
                if entry.status == ChunkStatus.OK:
                    cache.put(*r.key, entry.data)
                elif entry.status in (ChunkStatus.MISSING, ChunkStatus.PLACEHOLDER):
                    cache.put(*r.key, None)
                else:
                    self.failed[(code, r.key)] = _failed_kind(r)

            fetcher = Fetcher(
                max_in_flight=provider.max_in_flight,
                start_in_flight=provider.max_in_flight,
                req_per_s=provider.server_req_per_s,
                pushback_status=provider.pushback_status,
            )
            requests = [
                FetchRequest(
                    key=k,
                    url=tile_url(provider, *k),
                    headers=dict(provider.headers),
                    host_group=provider.code,
                )
                for k in keys
            ]
            async with fetcher:
                await fetcher.fetch_many(requests, on_result=keep, cancel=stop, keep_results=False)

        watcher = asyncio.get_running_loop().create_task(watch()) if cancel is not None else None
        try:
            await asyncio.gather(*(one(c, k) for c, k in sorted(wanted.items())))
        finally:
            if watcher is not None:
                watcher.cancel()
