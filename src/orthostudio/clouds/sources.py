# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The other sources' pieces a search or a check reads (``clouds.md`` 4.2).

Each source's pieces are kept like a build's parent tiles (``ParentCache``), so what one search
or check fetched the next reads from the disk. What a source answered is said per piece: a
``picture``; ``no_picture`` (none there, a placeholder); ``did_not_answer`` (a timeout, the
network, a server error); ``refused`` (403, 429, pushback); ``not_a_picture`` (a page instead).
Only a picture or none is kept: asked again, the others may answer (:data:`ASK_AGAIN`). Laying
never asks a source: it lays frozen parts (``clouds/layer.py``).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

import numpy as np

from orthostudio.errors import OsxpError
from orthostudio.imagery.chunks import ChunkStatus
from orthostudio.imagery.grid import TextureId, texture_bbox
from orthostudio.imagery.providers import Provider, cache_name, tile_url
from orthostudio.model import TileRef
from orthostudio.net import Fetcher, FetchRequest, FetchResult
from orthostudio.pipeline.parents import ParentCache
from orthostudio.pipeline.textures import chunk_entry_for
from orthostudio.zones import MIN_ZL

__all__ = [
    "ANSWERS",
    "ASK_AGAIN",
    "COARSEST",
    "JUDGED",
    "PROPOSABLE",
    "SourcePieces",
    "at_level",
    "code_of",
    "level_of",
    "other_sources",
    "read_at",
    "resolve",
    "tops",
    "tops_under",
]

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
JUDGED = 2
"""The way a cloud's images are judged (``find.check``), kept with each: images judged another way
are as if never got. Judged on half the layer, every cloud under some 30 ha was said to have no
picture in every source, and stayed so (``find.check``, 2026-11-06)."""

Key = tuple[int, int, int]
"""``(x, y, zl)`` of a source's piece."""

COARSEST = MIN_ZL
"""The coarsest level a cloud's source is read at, the zones' and the Plan's: some sources show
another photo there, blurred but with no clouds, where the level above shows cumulus (Esri World
Imagery at ZL11 over tropical forest, a user, 2026-11-14)."""


def at_level(code: str, zl: int) -> str:
    """The source ``code`` read at ``zl`` at most: ``Arc:11``. Each is a source of its own for
    the clouds (their screening, checks and menu), its pieces the source's own. Made by
    :func:`code_of` alone, so that one reading has one code."""
    return f"{code}:{zl}"


def level_of(code: str) -> tuple[str, int | None]:
    """The source and the level of ``code`` (:func:`at_level`); ``None``: its sharpest. A list
    file's text, whatever it holds, never raises: ``Arc:²`` is a source of that name."""
    base, _, zl = code.rpartition(":")
    level = base and zl.isascii() and zl.isdigit() and len(zl) <= 2
    return (base, int(zl)) if level else (code, None)


def code_of(base: str, zl: int | None, tops: Mapping[str, int]) -> str:
    """The one code of the source ``base`` read at ``zl`` for a tile (``tops``): the source
    itself at its sharpest there or finer (Bing at ZL16 on a ZL16 tile, EOX asked ZL16, which
    has none finer than ZL14), ``Bing:12`` below. Two codes of one reading had the same images
    got twice, a change counted where there was none, and the sharp one ranked as blurred (a
    review, 2026-11-14)."""
    top = tops.get(base)
    return base if zl is None or top is None or zl >= top else at_level(base, zl)


def read_at(code: str, tops: Mapping[str, int]) -> int | None:
    """The level the tile reads ``code`` at (:func:`code_of`): its own, else the source's
    sharpest there (``tops``); ``None`` for a source the tile does not know."""
    base, zl = level_of(code)
    return zl if zl is not None else tops.get(base)


def resolve(code: str, registry: Mapping[str, Provider]) -> Provider | None:
    """The source ``code`` names, read no finer than its level: the registry's source with that
    level as its finest. ``None`` when its source is unknown or the level is not one it has
    coarser than its finest, down to :data:`COARSEST`."""
    base, zl = level_of(code)
    provider = registry.get(base)
    if provider is None or zl is None:
        return provider
    if not COARSEST <= zl < provider.max_zl:
        return None
    return provider.model_copy(update={"max_zl": zl})


def tops(textures: Iterable[TextureId], registry: Mapping[str, Provider]) -> dict[str, int]:
    """The sharpest level each source is read at for a tile of ``textures``: its finest, or the
    finest texture's when coarser. A cloud's own are :func:`tops_under`."""
    finest = max((t.zl for t in textures), default=0)
    return {code: min(finest, p.max_zl) for code, p in registry.items()}


def tops_under(
    textures: Iterable[TextureId], tile_tops: Mapping[str, int]
) -> Callable[[Sequence[Sequence[float]]], dict[str, int]]:
    """For the clouds of a tile: the sharpest level each source is read at under one cloud (its
    rings), the finest of the textures its outline's box reaches (the tile's ``tile_tops``
    capped there): a cloud in a ZL16 square of a tile with an airport at ZL18 was offered ZL18
    and ZL17, read as ZL16, and its ZL16 photo ranked as blurred (two reviews, 2026-11-14). Its
    levels go from it down to :data:`COARSEST`. The textures' boxes are read once."""
    by_level: dict[int, list[tuple[float, float, float, float]]] = {}
    for t in textures:
        by_level.setdefault(t.zl, []).append(texture_bbox(t))
    levels = sorted(((zl, np.array(b)) for zl, b in by_level.items()), reverse=True)

    def under(rings: Sequence[Sequence[float]]) -> dict[str, int]:
        lats = [float(v) for r in rings for v in r[0::2]]
        lons = [float(v) for r in rings for v in r[1::2]]
        if not lats:
            return dict(tile_tops)
        south, north, west, east = min(lats), max(lats), min(lons), max(lons)
        for zl, b in levels:  # (lat_max, lon_min, lat_min, lon_max), the finest first
            if ((b[:, 2] < north) & (b[:, 0] > south) & (b[:, 1] < east) & (b[:, 3] > west)).any():
                return {code: min(top, zl) for code, top in tile_tops.items()}
        return dict(tile_tops)

    return under


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
