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

DETAIL_DEG = 3.0  # a view wider than this, in latitude or longitude, gets squares, not textures
MAX_TEXTURES = 15_000  # the most rectangles one answer holds
ROUTE_POINTS = 400  # a route's points, at most, as the map draws it

_lock = threading.Lock()
_cache: dict[Path, tuple[float, list[tuple[float, float, float, float, int]]]] = {}


def _built(pack: Path) -> list[tuple[float, float, float, float, int]]:
    """(south, west, north, east, level) of every texture built: level -1 is 8192 px, 0 is 4096
    px, 1 is 2048... The record is read again only once the streamer has saved it since."""
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
    grown = set(meta.get("grown", []))
    rows = []
    for name, level in meta.get("textures", {}).items():
        tex = parse_texture_name(name)
        if tex is None or not isinstance(level, int) or level > 4:
            continue
        south, west, north, east = tex.bounds
        rows.append((south, west, north, east, -1 if name in grown else level))
    with _lock:
        _cache[pack] = (mtime, rows)
    return rows


def built_in(pack: Path, west: float, south: float, east: float, north: float) -> dict[str, Any]:
    """The textures built in a view: ``{"textures": [[s, w, n, e, level], ...]}`` for a small
    view, ``{"squares": [[lat, lon, count, sharpest], ...]}`` for a wide one; ``more`` when a
    small view had more than :data:`MAX_TEXTURES`."""
    rows = [
        r for r in _built(pack) if r[0] < north and r[2] > south and r[1] < east and r[3] > west
    ]
    if north - south > DETAIL_DEG or east - west > DETAIL_DEG:
        squares: dict[tuple[int, int], list[int]] = {}
        for s, w, n, e, level in rows:
            key = (math.floor((s + n) / 2), math.floor((w + e) / 2))
            got = squares.setdefault(key, [0, level])
            got[0] += 1
            got[1] = min(got[1], level)
        return {"squares": [[lat, lon, c, best] for (lat, lon), (c, best) in squares.items()]}
    more = len(rows) > MAX_TEXTURES
    return {
        "textures": [[round(v, 5) for v in r[:4]] + [r[4]] for r in rows[:MAX_TEXTURES]],
        "more": more,
    }


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
