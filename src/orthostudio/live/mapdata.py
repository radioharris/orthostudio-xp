# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the Live window's map draws over the base map: the textures built, by sharpness; the
regions installed; a route's points.

The textures built are the pack's record (``_live/ortho.json``: each texture's level), read again
only when the streamer has saved it since. Over a small view the map
gets each texture's rectangle. Over a wide one, more than :data:`DETAIL_DEG` a side, it gets one
entry per one-degree square instead: how many textures are built there and the sharpest. Tens of
thousands of rectangles would be too many for the page.
"""

from __future__ import annotations

import contextlib
import math
import threading
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

from . import dds
from .geo import parse_texture_name
from .pack import load_built

DETAIL_DEG = 3.0  # a view wider than this, in latitude or longitude, groups the blurry textures
WIDE_BLURRIEST = (
    15  # ...those whose photos are coarser than ZL15: the many at 256 px around a flight
)
MAX_TEXTURES = 15_000  # the most rectangles one answer holds, the sharpest first
ROUTE_POINTS = 400  # a route's points, at most, as the map draws it

# south, west, north, east, the zoom of its photos, name
Row = tuple[float, float, float, float, int, str]

_lock = threading.Lock()
_cache: dict[Path, tuple[float, list[Row]]] = {}
_bounds: dict[str, tuple[float, float, float, float, int] | None] = {}  # a name's, once


def _rectangle(name: str) -> tuple[float, float, float, float, int] | None:
    """A texture's rectangle and its zoom."""
    got = _bounds.get(name, False)
    if got is False:
        tex = parse_texture_name(name)
        got = None if tex is None else (*tex.bounds, tex.zoom)
        _bounds[name] = got
    return got


def rows_of(textures: dict[str, int], top_of: Callable[[str], int] | None = None) -> list[Row]:
    """Every texture built: its rectangle and the zoom of its photos, which says how sharp it is
    on the ground whatever its size (an airport's ZL18 texture at 4096 px has photos of ZL18, a
    ZL16 one at 8192 px photos of ZL17: the first is the sharper, with fewer pixels). ``top_of``:
    each one's size for the zoom set, the most X-Plane shows of it (:func:`shown`)."""
    rows = []
    for name, level in textures.items():
        box = _rectangle(name)
        if box is None or not isinstance(level, int) or level > 4:
            continue
        south, west, north, east, zoom = box
        shown_level = level if top_of is None else shown(level, top_of(name))
        rows.append((south, west, north, east, zoom - shown_level, name))
    return rows


def zoom_tops(pack: Path) -> Callable[[str], int] | None:
    """Each texture's size for the pack's zoom (:func:`shown`); None for a record without its
    pack's zoom, a past flight's."""
    from .region import REGION_MARKER, file_tops

    return file_tops(pack) if (pack / REGION_MARKER).exists() else None


def shown(level: int, top: int) -> int:
    """The level X-Plane shows of a texture built from ``level`` for a zoom whose file's sharpest
    level is ``top``: a new zoom keeps a sharper level recorded until the texture's square is made
    again, its file set to the new size then (orthostudio.live.region.set_zooms)."""
    return max(level, top)


def _built(pack: Path) -> list[Row]:
    """The pack's record, read again only once the streamer has saved it since."""
    from .region import REGION_MARKER

    marker = pack / "_live" / "ortho.json"
    try:
        mtime = marker.stat().st_mtime
    except OSError:
        return []
    with contextlib.suppress(OSError):  # a new zoom changes what X-Plane shows of each
        mtime += (pack / REGION_MARKER).stat().st_mtime
    with _lock:
        hit = _cache.get(pack)
        if hit is not None and hit[0] == mtime:
            return hit[1]
    meta = load_built(pack)
    rows = rows_of(meta.get("textures", {}), zoom_tops(pack))
    with _lock:
        _cache[pack] = (mtime, rows)
    return rows


def outside(row: Row, covered: Collection[tuple[int, int]]) -> list[Row]:
    """What X-Plane may show of a texture beside the user's own tiles: its part in each square it
    reaches that is not theirs, the whole texture when it reaches none of theirs, nothing when it
    lies in theirs only (X-Plane shows the tiles there). A texture over a tile's edge counted, and
    grouped when zoomed out, in the tile's square painted the whole tile grey (a user,
    2026-09-27)."""
    south, west, north, east, photos, name = row
    squares = [
        (lat, lon)
        for lat in range(math.floor(south), math.ceil(north))
        for lon in range(math.floor(west), math.ceil(east))
    ]
    if not any(square in covered for square in squares):
        return [row]
    return [
        (max(south, lat), max(west, lon), min(north, lat + 1), min(east, lon + 1), photos, name)
        for lat, lon in squares
        if (lat, lon) not in covered
    ]


def built_in(
    pack: Path,
    west: float,
    south: float,
    east: float,
    north: float,
    rows: list[Row] | None = None,
    covered: Collection[tuple[int, int]] = (),
) -> dict[str, Any]:
    """The textures built in a view, each by the zoom of its photos, how sharp it is on the
    ground: ``{"textures": [[s, w, n, e, photos zoom], ...], "squares": [[lat, lon, count,
    sharpest photos zoom], ...], "more": bool}``. Each texture is drawn on its own, the sharpest
    first; in a view wider than :data:`DETAIL_DEG`, those whose photos are coarser than ZL15 are
    grouped by one-degree square: the 256 px ones are thousands around a flight, and one colour
    for a whole square hid the sharp textures near the aircraft (a user, 2026-09-27). Beyond
    :data:`MAX_TEXTURES`, the blurriest go to the squares too (``more``). ``rows``: the running
    streamer's, from its memory; else the pack's record. ``covered``: the squares of the user's
    own tiles, where the textures are left out, cut at their edges (:func:`outside`)."""
    rows = [
        r
        for r in (_built(pack) if rows is None else rows)
        if r[0] < north and r[2] > south and r[1] < east and r[3] > west
    ]
    if covered:
        rows = [part for r in rows for part in outside(r, covered)]
    wide = north - south > DETAIL_DEG or east - west > DETAIL_DEG
    one_by_one = sorted(
        (r for r in rows if not wide or r[4] >= WIDE_BLURRIEST), key=lambda r: -r[4]
    )
    grouped = [r for r in rows if wide and r[4] < WIDE_BLURRIEST]
    more = len(one_by_one) > MAX_TEXTURES
    if more:
        grouped += one_by_one[MAX_TEXTURES:]
        one_by_one = one_by_one[:MAX_TEXTURES]
    squares: dict[tuple[int, int], list[int]] = {}
    for s, w, n, e, photos, _ in grouped:
        key = (math.floor((s + n) / 2), math.floor((w + e) / 2))
        got = squares.setdefault(key, [0, photos])
        got[0] += 1
        got[1] = max(got[1], photos)
    return {
        "textures": [[round(v, 5) for v in r[:4]] + [r[4]] for r in one_by_one],
        "squares": [[lat, lon, c, best] for (lat, lon), (c, best) in squares.items()],
        "more": more,
    }


def record(pack: Path) -> dict[str, Any]:
    """The pack's record: what was built, left patchy, and when each was last in reach."""
    return load_built(pack)


def textures_at(
    lat: float,
    lon: float,
    textures: dict[str, int],
    patchy: set[str],
    seen: dict[str, float],
    now: float,
    top_of: Callable[[str], int] = lambda _: 0,
    shown_top: Callable[[str], int] | None = None,
) -> list[dict[str, Any]]:
    """The textures built under (lat, lon), the smallest first (an airport's ZL18 over the
    ZL16 around it), each as the map's tooltip tells it: its zoom and source, the size built and
    the zoom of the photos that makes, the disk it takes (``top_of``: its file's size), its
    ground, when it was last in reach, and whether it waits to be built again for pieces that did
    not come. ``shown_top``: each one's size for the zoom set, the most X-Plane shows of it."""
    from .evict import texture_bytes
    from .geo import haversine_m

    found = []
    for name, level in textures.items():
        box = _rectangle(name)
        if box is None or not isinstance(level, int) or level > 4:
            continue
        south, west, north, east, _ = box
        if not (south <= lat < north and west <= lon < east):
            continue
        tex = parse_texture_name(name)
        seen_level = level if shown_top is None else shown(level, shown_top(name))
        width = haversine_m((south + north) / 2, west, (south + north) / 2, east) / 1000
        height = haversine_m(south, west, north, west) / 1000
        found.append(
            {
                "name": name.removesuffix(".dds"),
                "zoom": tex.zoom,
                "provider": tex.provider,
                "px": dds.size_of(seen_level),
                "photos_zoom": tex.zoom - seen_level,
                "patchy": name in patchy,
                "bytes": texture_bytes(level, top_of(name)),
                "km": [round(width, 2), round(height, 2)],
                "seen_s": round(now - seen[name]) if name in seen else None,
                "area": width * height,
            }
        )
    found.sort(key=lambda t: t.pop("area"))
    return found


def regions_installed(installed: set[str]) -> dict[str, Any]:
    """The one-degree squares of each of OrthoStudio XP's regions installed, for the map to shade:
    ``{"eur": [[lat, lon], ...], ...}`` (:func:`orthostudio.live.meshmap.squares_of`)."""
    from orthostudio.live.meshmap import squares_of

    return squares_of(installed)


def route_points(points: list[tuple[float, float]]) -> list[list[float]]:
    """A route as the map draws it: at most :data:`ROUTE_POINTS` points, its ends kept."""
    if len(points) <= ROUTE_POINTS:
        return [[round(a, 5), round(b, 5)] for a, b in points]
    step = (len(points) - 1) / (ROUTE_POINTS - 1)
    picked = [points[round(i * step)] for i in range(ROUTE_POINTS)]
    return [[round(a, 5), round(b, 5)] for a, b in picked]
