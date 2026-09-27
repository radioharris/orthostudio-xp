# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the Live window's map draws over the base map: the textures built, by sharpness; the
regions installed; a route's points.

The textures built are the pack's record (``_live/ortho.json``: each texture's level, and those
grown to 8192 px), read again only when the streamer has saved it since. Over a small view the map
gets each texture's rectangle. Over a wide one, more than :data:`DETAIL_DEG` a side, it gets one
entry per one-degree square instead: how many textures are built there and the sharpest. Tens of
thousands of rectangles would be too many for the page.
"""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any

from . import aoregions
from .geo import parse_texture_name
from .pack import load_built

DETAIL_DEG = 3.0  # a view wider than this, in latitude or longitude, groups the small textures
WIDE_SHARPEST = 2  # ...those coarser than 1024 px (level 2): the many at 256 px around the flight
MAX_TEXTURES = 15_000  # the most rectangles one answer holds, the sharpest first
ROUTE_POINTS = 400  # a route's points, at most, as the map draws it

Row = tuple[float, float, float, float, int, str]  # south, west, north, east, level, name

_lock = threading.Lock()
_cache: dict[Path, tuple[float, list[Row]]] = {}
_bounds: dict[str, tuple[float, float, float, float] | None] = {}  # a name's rectangle, once


def _rectangle(name: str) -> tuple[float, float, float, float] | None:
    got = _bounds.get(name, False)
    if got is False:
        tex = parse_texture_name(name)
        got = None if tex is None else tex.bounds
        _bounds[name] = got
    return got


def rows_of(textures: dict[str, int], grown: set[str]) -> list[Row]:
    """(south, west, north, east, level) of every texture built: level -1 is 8192 px, 0 is 4096
    px, 1 is 2048..."""
    rows = []
    for name, level in textures.items():
        box = _rectangle(name)
        if box is None or not isinstance(level, int) or level > 4:
            continue
        rows.append((*box, -1 if name in grown else level, name))
    return rows


def _built(pack: Path) -> list[Row]:
    """The pack's record, read again only once the streamer has saved it since."""
    marker = pack / "_live" / "ortho.json"
    try:
        mtime = marker.stat().st_mtime
    except OSError:
        return []
    with _lock:
        hit = _cache.get(pack)
        if hit is not None and hit[0] == mtime:
            return hit[1]
    meta = load_built(pack)
    rows = rows_of(meta.get("textures", {}), set(meta.get("grown", [])))
    with _lock:
        _cache[pack] = (mtime, rows)
    return rows


def built_in(
    pack: Path,
    west: float,
    south: float,
    east: float,
    north: float,
    rows: list[Row] | None = None,
) -> dict[str, Any]:
    """The textures built in a view: ``{"textures": [[s, w, n, e, level], ...], "squares": [[lat,
    lon, count, sharpest], ...], "more": bool}``. Each texture is drawn on its own, the sharpest
    first, but in a view wider than :data:`DETAIL_DEG` those coarser than 1024 px are grouped by
    one-degree square: 256 px ones are thousands around a flight, and one colour for a whole
    square hid the 4096 px textures near the aircraft behind its 8192 px ones (a user,
    2026-09-27). Beyond :data:`MAX_TEXTURES`, the coarsest go to the squares too (``more``).
    ``rows``: the running streamer's, from its memory; else the pack's record."""
    rows = [
        r
        for r in (_built(pack) if rows is None else rows)
        if r[0] < north and r[2] > south and r[1] < east and r[3] > west
    ]
    wide = north - south > DETAIL_DEG or east - west > DETAIL_DEG
    one_by_one = sorted((r for r in rows if not wide or r[4] <= WIDE_SHARPEST), key=lambda r: r[4])
    grouped = [r for r in rows if wide and r[4] > WIDE_SHARPEST]
    more = len(one_by_one) > MAX_TEXTURES
    if more:
        grouped += one_by_one[MAX_TEXTURES:]
        one_by_one = one_by_one[:MAX_TEXTURES]
    squares: dict[tuple[int, int], list[int]] = {}
    for s, w, n, e, level, _ in grouped:
        key = (math.floor((s + n) / 2), math.floor((w + e) / 2))
        got = squares.setdefault(key, [0, level])
        got[0] += 1
        got[1] = min(got[1], level)
    return {
        "textures": [[round(v, 5) for v in r[:4]] + [r[4]] for r in one_by_one],
        "squares": [[lat, lon, c, best] for (lat, lon), (c, best) in squares.items()],
        "more": more,
    }


def record(pack: Path) -> dict[str, Any]:
    """The pack's record: what was built, grown, left patchy, and when each was last in reach."""
    return load_built(pack)


def textures_at(
    lat: float,
    lon: float,
    textures: dict[str, int],
    grown: set[str],
    patchy: set[str],
    seen: dict[str, float],
    now: float,
) -> list[dict[str, Any]]:
    """The textures built under (lat, lon), the smallest first (an airport's ZL18 over the
    ZL16 around it), each as the map's tooltip tells it: its zoom and source, the size built and
    the zoom of the photos that makes, the disk it takes, its ground, when it was last in reach,
    and whether it waits to be built again for pieces that did not come."""
    from .evict import texture_bytes
    from .geo import haversine_m

    found = []
    for name, level in textures.items():
        box = _rectangle(name)
        if box is None or not isinstance(level, int) or level > 4:
            continue
        south, west, north, east = box
        if not (south <= lat < north and west <= lon < east):
            continue
        tex = parse_texture_name(name)
        level = -1 if name in grown else level
        width = haversine_m((south + north) / 2, west, (south + north) / 2, east) / 1000
        height = haversine_m(south, west, north, west) / 1000
        found.append(
            {
                "name": name.removesuffix(".dds"),
                "zoom": tex.zoom,
                "provider": tex.provider,
                "px": 8192 if level < 0 else 4096 >> level,
                "photos_zoom": tex.zoom - level,
                "grown": level < 0,
                "patchy": name in patchy,
                "bytes": texture_bytes(level),
                "km": [round(width, 2), round(height, 2)],
                "seen_s": round(now - seen[name]) if name in seen else None,
                "area": width * height,
            }
        )
    found.sort(key=lambda t: t.pop("area"))
    return found


def regions_installed(installed: set[str]) -> dict[str, Any]:
    """The one-degree squares of each region installed, for the map to shade:
    ``{"eur": [[lat, lon], ...], ...}`` (the map of every region's squares, orthostudio/live/
    aoregions_map.json)."""
    names, cells = aoregions._map()
    wanted = {i + 1: name for i, name in enumerate(names) if name in installed}
    out: dict[str, list[list[int]]] = {name: [] for name in wanted.values()}
    for cell, index in enumerate(cells):
        name = wanted.get(index)
        if name is not None:
            out[name].append([cell // 360 - 90, cell % 360 - 180])
    return out


def route_points(points: list[tuple[float, float]]) -> list[list[float]]:
    """A route as the map draws it: at most :data:`ROUTE_POINTS` points, its ends kept."""
    if len(points) <= ROUTE_POINTS:
        return [[round(a, 5), round(b, 5)] for a, b in points]
    step = (len(points) - 1) / (ROUTE_POINTS - 1)
    picked = [points[round(i * step)] for i in range(ROUTE_POINTS)]
    return [[round(a, 5), round(b, 5)] for a, b in picked]
