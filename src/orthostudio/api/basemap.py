# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The street map of the page: ``GET /api/basemap/...``, a caching proxy in front of OpenFreeMap.

A user of the X-Plane.Org page asked for an OSM map beside the aerial imagery, as Ortho4XP offers
in its preview window, to tell whether a square holds the airport or the landmark he is after
(2026-09-19). Ortho4XP points straight at ``tile.openstreetmap.org`` with a browser's User-Agent
and a Referer of its own; the OSM Foundation asks distributed applications not to do that, so
OrthoStudio XP reads **OpenFreeMap** instead: the same OpenStreetMap data, rendered and served for
free, without a key and without a quota (``https://openfreemap.org``).

OpenFreeMap serves **vector** tiles, which the page draws with MapLibre GL. Everything it needs
(the style, the vector tiles, the glyphs, the sprites and the low-zoom relief raster) goes through
this router, for three reasons:

* the page holds no address of another origin, which a test keeps true;
* what is downloaded is cached under ``<data folder>/mapcache/openfreemap``, so panning over a
  region already seen costs nothing and the *Free space* button frees it with the rest of the map
  background;
* the style is rewritten on the way out, so its own URLs point back here.

Nothing here is needed to build a tile: with the service unreachable the street map fails to draw
and the aerial imagery, which is the default, is untouched.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from orthostudio.fsutil import atomic_write_bytes
from orthostudio.home import data_root_missing, default_mapcache_root

log = logging.getLogger("orthostudio.api.basemap")

__all__ = [
    "BASEMAP_ATTRIBUTION",
    "OPENFREEMAP_URL",
    "STYLE_NAME",
    "basemap_router",
    "rewrite_style",
]

OPENFREEMAP_URL = "https://tiles.openfreemap.org"
"""Where the style, the vector tiles, the glyphs and the sprites come from."""

STYLE_NAME = "liberty"
"""The style asked for: the one that looks like the map on openstreetmap.org."""

BASEMAP_ATTRIBUTION = "© OpenFreeMap © OpenMapTiles, data © OpenStreetMap contributors"
"""Shown in the map's corner when the street map is on, as the service asks."""

CACHE_DIR = "openfreemap"
"""Under ``<data folder>/mapcache``: the *Free space* button already empties that folder."""

DEADLINE_S = 20.0
"""No request outlives this: the street map fails to draw, it never hangs the page."""

MAX_BYTES = 8_000_000
"""A vector tile is 100 to 300 KB and a sprite sheet under a megabyte: anything larger is not
something this proxy should be passing on."""

_TYPES = {
    ".pbf": "application/x-protobuf",
    ".json": "application/json",
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
}


def rewrite_style(doc: Any, prefix: str = "/api/basemap") -> Any:
    """The style with every OpenFreeMap address pointing back at this router.

    MapLibre reads the tiles, the glyphs and the sprites from the addresses the style gives, so
    rewriting them here is what keeps the page from talking to another origin. ``prefix`` is the
    **absolute** address of this router in a served answer: the vector tiles are fetched from a
    web worker, which has no page to resolve a relative address against and refuses it outright
    ("Failed to parse URL", measured 2026-09-19).
    """
    if isinstance(doc, str):
        if doc.startswith(OPENFREEMAP_URL):
            return prefix + doc[len(OPENFREEMAP_URL) :]
        return doc
    if isinstance(doc, list):
        return [rewrite_style(item, prefix) for item in doc]
    if isinstance(doc, dict):
        return {key: rewrite_style(value, prefix) for key, value in doc.items()}
    return doc


def _is_json(path: str) -> bool:
    """Whether the answer at that path is JSON: the style, and the sources that name the tiles.

    The service serves them without an extension (``styles/liberty``, ``planet``), so the
    extensions it does use are what tells the others apart.
    """
    return not any(path.endswith(suffix) for suffix in _TYPES if suffix != ".json")


def content_type(path: str) -> str:
    """What to answer for a path of the service, from its extension."""
    for suffix, kind in _TYPES.items():
        if path.endswith(suffix):
            return kind
    return "application/octet-stream"


def _cache_path(root: Path | None, path: str) -> Path | None:
    """Where a piece is kept, or ``None`` when the data folder is not there to write into.

    A document with no extension takes one: ``planet`` is both an answer of its own and the folder
    the vector tiles live under, and a file cannot also be a directory.
    """
    if root is None or data_root_missing() is not None:
        return None
    clean = path.strip("/").replace("..", "_")
    if _is_json(clean) and not clean.endswith(".json"):
        clean += ".json"
    return root / CACHE_DIR / clean


def _fetch(url: str) -> tuple[bytes | None, str]:
    """One request through the fetcher the rest of the engine uses; never raises."""
    from orthostudio.net.fetch import FetchRequest, fetch_all

    results = fetch_all(
        [FetchRequest(key=url, url=url, host_group="openfreemap")],
        timeout_s=DEADLINE_S,
        max_attempts=2,
        max_in_flight=4,
        start_in_flight=2,
    )
    if not results:
        return None, "NET_CONNECTION_FAILED"
    answer = results[0]
    if answer.error is not None:
        return None, answer.error
    if not (200 <= answer.status < 300) or not answer.body:
        return None, f"HTTP {answer.status}"
    if len(answer.body) > MAX_BYTES:
        return None, "the answer is larger than this proxy passes on"
    return answer.body, ""


def basemap_router(
    cache_root: Any = default_mapcache_root,
    *,
    fetch: Callable[[str], tuple[bytes | None, str]] | None = None,
) -> APIRouter:
    """The router: ``app.include_router(basemap_router())``.

    ``cache_root`` is called for the map cache's folder, so a data folder chosen in Settings (or
    unplugged) is honoured on every request, as the imagery proxy does. ``fetch`` replaces the
    upstream request, which is how the tests run without a network.
    """
    ask = fetch if fetch is not None else _fetch
    router = APIRouter()

    def cached(path: str) -> bytes | None:
        target = _cache_path(cache_root(), path)
        if target is None or not target.is_file():
            return None
        try:
            return target.read_bytes()
        except OSError:
            return None

    def keep(path: str, body: bytes) -> None:
        target = _cache_path(cache_root(), path)
        if target is None:
            return
        try:
            atomic_write_bytes(target, body, fsync=False)
        except OSError as exc:  # a full disk must not break the map
            log.debug("basemap: %s not cached (%s)", path, exc)

    def failed(path: str, why: str) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": "NET_CONNECTION_FAILED",
                    "severity": "degraded",
                    "message": f"The street map could not be read ({why}).",
                    "remedy": "The aerial imagery is unaffected; try again later.",
                    "context": {"path": path},
                }
            },
            status_code=502,
        )

    def upstream(path: str, prefix: str = "/api/basemap") -> Response:
        """Serve one piece, from the cache or from the service.

        The bytes are kept as the service sent them; a JSON answer (the style, and the tile source
        that names where the vector tiles really are) is rewritten on the way out, so the page
        follows addresses of this router alone.
        """
        body = cached(path)
        if body is None:
            body, why = ask(f"{OPENFREEMAP_URL}/{path.lstrip('/')}")
            if body is None:
                return failed(path, why)
            keep(path, body)
        if _is_json(path):
            try:
                doc = json.loads(body)
            except ValueError:
                return failed(path, "its JSON could not be read")
            # Not kept by the browser: the addresses inside are this engine's, and this engine
            # answers on the port it was started with. A style cached for a week outlived the
            # port it named (measured 2026-09-19).
            return JSONResponse(rewrite_style(doc, prefix), headers=_JSON_HEADERS)
        return Response(body, media_type=content_type(path), headers=_HEADERS)

    def prefix_of(request: Request) -> str:
        """This router's absolute address, as the client reached it."""
        return str(request.base_url).rstrip("/") + "/api/basemap"

    @router.get("/api/basemap/style")
    async def style(request: Request) -> Response:
        """The style of the street map: the one address the page has to know."""
        import asyncio

        return await asyncio.to_thread(upstream, f"styles/{STYLE_NAME}", prefix_of(request))

    @router.get("/api/basemap/{path:path}")
    async def piece(path: str, request: Request) -> Response:
        """A vector tile, a glyph range, a sprite or the low-zoom raster of the style."""
        import asyncio

        return await asyncio.to_thread(upstream, path, prefix_of(request))

    return router


_HEADERS = {"Cache-Control": "private, max-age=604800"}
"""A week, for the pieces whose address carries a version (the tiles, the glyphs, the sprites,
the low-zoom raster); the cache on disk holds them anyway."""

_JSON_HEADERS = {"Cache-Control": "no-cache"}
"""The style and the tile sources name this engine's own address: they are read again each time."""
