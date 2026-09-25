"""A flight plan turned into the squares to build and the line to draw (``flight-plan.md``).

One computation gives both, so what the map draws is what gets chosen. The first flight plan drew
straight lines on the map, counted squares along straight lines in degrees and measured along the
sphere: three routes that parted on a long leg, KJFK to EGLL counting 84 squares where the great
circle crosses 89, 83 of them others (review of 2026-09-23). Here the plan's points are joined by
great circles, cut into steps of :data:`STEP_KM`, and that one path is what the page draws and what
the squares are counted on: every square the path enters, corners included, and every square within
the airport field's radius of it, since a square seven kilometres beside the route is seen from the
aircraft (a user, 2026-09-25).

Nothing here reads a file or the network but :func:`has_scenery`, which asks whether X-Plane has a
scenery file for a square: a square it has none for (open sea, or a region not installed) cannot
be built, a build stops on it (``DSF_GLOBAL_SCENERY_MISSING``), so a plan leaves it out and says
how many it left.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from orthostudio.airports import EARTH_RADIUS_KM, haversine_km, tiles_within
from orthostudio.model import TileRef

__all__ = [
    "STEP_KM",
    "great_circle",
    "has_scenery",
    "path_of",
    "pieces_of",
    "plan_of",
    "squares_along",
    "squares_near",
]

STEP_KM = 10.0
"""The length of one step of the drawn path. A straight step of this length drawn on the map
leaves the great circle by metres, far below the width of the line and of any square."""

CORRIDOR_STEP_KM = 1.0
"""How finely the corridor is sampled: a square is found within half this of its true distance."""

LatLon = tuple[float, float]


def _xyz(lat: float, lon: float) -> tuple[float, float, float]:
    phi, lam = math.radians(lat), math.radians(lon)
    return (math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi))


def _latlon(x: float, y: float, z: float) -> LatLon:
    return (math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x)))


def great_circle(a: LatLon, b: LatLon, step_km: float = STEP_KM) -> list[LatLon]:
    """The great circle from ``a`` towards ``b``, one point per step, ``a`` included, ``b`` not.

    Two points on opposite sides of the Earth have no single great circle between them; no flight
    plan has such a leg, and the straight step is kept rather than a direction invented.
    """
    pa, pb = _xyz(*a), _xyz(*b)
    dot = max(-1.0, min(1.0, sum(u * v for u, v in zip(pa, pb, strict=True))))
    angle = math.acos(dot)
    steps = max(1, math.ceil(angle * EARTH_RADIUS_KM / step_km))
    sin_angle = math.sin(angle)
    if sin_angle < 1e-12:
        return [a]
    out: list[LatLon] = []
    for i in range(steps):
        f = i / steps
        wa = math.sin((1 - f) * angle) / sin_angle
        wb = math.sin(f * angle) / sin_angle
        out.append(_latlon(*(wa * u + wb * v for u, v in zip(pa, pb, strict=True))))
    out[0] = a  # the plan's own point, not its round trip through three dimensions
    return out


def path_of(points: Sequence[LatLon], step_km: float = STEP_KM) -> list[LatLon]:
    """The plan's points joined by great circles: the one path everything else is taken from."""
    path: list[LatLon] = []
    for a, b in pairwise(points):
        path.extend(great_circle(a, b, step_km))
    if points:
        path.append(points[-1])
    out: list[LatLon] = []
    for p in path:
        if not out or p != out[-1]:
            out.append(p)
    return out


def _unwrapped(lon_from: float, lon_to: float) -> float:
    """``lon_to`` moved by whole turns to lie within half a turn of ``lon_from``."""
    while lon_to - lon_from > 180.0:
        lon_to -= 360.0
    while lon_to - lon_from < -180.0:
        lon_to += 360.0
    return lon_to


def pieces_of(path: Sequence[LatLon]) -> list[list[LatLon]]:
    """The path cut where it crosses the antimeridian, as a chart draws it.

    The map cannot be panned beyond ±180°, so a line drawn with unrolled longitudes left Tokyo at
    -220° out of reach (review of 2026-09-23). Each piece stays within the world; the cut is placed
    on the meridian itself, at the latitude where the step crosses it.
    """
    if not path:
        return []
    pieces: list[list[LatLon]] = [[path[0]]]
    for (lat0, lon0), (lat1, lon1) in pairwise(path):
        u = _unwrapped(lon0, lon1)
        if -180.0 <= u <= 180.0:
            pieces[-1].append((lat1, lon1))
            continue
        edge = 180.0 if u > 180.0 else -180.0
        t = (edge - lon0) / (u - lon0)
        lat = lat0 + t * (lat1 - lat0)
        pieces[-1].append((lat, edge))
        pieces.append([(lat, -edge), (lat1, lon1)])
    return [p for p in pieces if len(p) > 1] or [pieces[0]]


def _wrap_square_lon(lon: int) -> int:
    return ((lon + 180) % 360) - 180


def _square(lat: int, lon: int) -> TileRef:
    return TileRef(max(-90, min(89, lat)), _wrap_square_lon(lon))


def _squares_on_step(a: LatLon, b: LatLon) -> list[TileRef]:
    """Every square the straight step from ``a`` to ``b`` enters, in order (a grid walk).

    A step that only touches a square's edge at its very end does not enter it: the next step,
    which starts there, does if the path goes on into it.
    """
    y0, x0 = a
    y1 = b[0]
    x1 = _unwrapped(x0, b[1])
    cx, cy = math.floor(x0), math.floor(y0)
    dx, dy = x1 - x0, y1 - y0
    step_x = 1 if dx > 0 else -1
    step_y = 1 if dy > 0 else -1
    inf = math.inf
    t_x = ((cx + (1 if dx > 0 else 0)) - x0) / dx if dx else inf
    t_y = ((cy + (1 if dy > 0 else 0)) - y0) / dy if dy else inf
    d_x = abs(1.0 / dx) if dx else inf
    d_y = abs(1.0 / dy) if dy else inf
    out = [_square(cy, cx)]
    while True:
        t = min(t_x, t_y)
        if t >= 1.0:
            return out
        if t_x < t_y:
            cx += step_x
            t_x += d_x
        elif t_y < t_x:
            cy += step_y
            t_y += d_y
        else:  # through a corner exactly: the diagonal square, not the two it only touches
            cx += step_x
            cy += step_y
            t_x += d_x
            t_y += d_y
        out.append(_square(cy, cx))


def squares_along(path: Sequence[LatLon]) -> list[TileRef]:
    """Every square the path enters, in the order it is flown, each once."""
    seen: set[TileRef] = set()
    out: list[TileRef] = []
    steps = list(pairwise(path)) if len(path) > 1 else [(path[0], path[0])]
    for a, b in steps:
        for square in _squares_on_step(a, b):
            if square not in seen:
                seen.add(square)
                out.append(square)
    return out


def squares_near(
    points: Sequence[LatLon], radius_km: float, step_km: float = CORRIDOR_STEP_KM
) -> list[TileRef]:
    """Every square within ``radius_km`` of the plan's path, in the order it is first approached.

    The squares the path enters are always there, found exactly; with a radius, so is every square
    whose nearest point lies within it of a point of the path, sampled every ``step_km`` (the rule
    of the airport field, ``airports.tiles_within``, along the whole flight). The route passed seven
    kilometres from the corner of +46+011 without choosing it, and that square is seen from the
    aircraft (a user, 2026-09-25).
    """
    fine = path_of(points, step_km)
    seen: set[TileRef] = set()
    out: list[TileRef] = []

    def take(squares: Iterable[TileRef]) -> None:
        for square in squares:
            if square not in seen:
                seen.add(square)
                out.append(square)

    steps = list(pairwise(fine)) if len(fine) > 1 else [(fine[0], fine[0])]
    for a, b in steps:
        if radius_km > 0:
            take(tiles_within(a[0], a[1], radius_km))
        take(_squares_on_step(a, b))
    if radius_km > 0:
        take(tiles_within(fine[-1][0], fine[-1][1], radius_km))
    return out


def has_scenery(global_scenery: Path) -> Callable[[TileRef], bool]:
    """Whether X-Plane has a scenery file for a square: a build needs it (see the module)."""
    from orthostudio.dsf.xp12 import global_scenery_dsf

    return lambda square: global_scenery_dsf(global_scenery, square).is_file()


def _point(p: Mapping[str, Any] | LatLon) -> LatLon:
    if isinstance(p, Mapping):
        return (float(p["lat"]), float(p["lon"]))
    return (float(p[0]), float(p[1]))


def plan_of(
    points: Iterable[Mapping[str, Any] | LatLon],
    *,
    radius_km: float,
    has_land: Callable[[TileRef], bool] | None = None,
    step_km: float = STEP_KM,
) -> dict[str, Any]:
    """The squares of a flight plan and the line that shows them.

    ``ends``: the squares within ``radius_km`` of the departure, then of the arrival (the same
    rule as the airport field). ``along``: every other square within ``radius_km`` of the path
    (:func:`squares_near`), in the order it is flown, which is the order a plan too long for one
    build is cut in. ``has_land``, when given, leaves out the squares X-Plane has no scenery for and
    counts them in ``left_out``.
    """
    pts = [_point(p) for p in points]
    if len(pts) < 2:
        raise ValueError("a flight plan needs two points at least")
    path = path_of(pts, step_km)
    ends: list[TileRef] = []
    for lat, lon in (pts[0], pts[-1]):
        for square in tiles_within(lat, lon, radius_km):
            if square not in ends:
                ends.append(square)
    in_ends = set(ends)
    along = [s for s in squares_near(pts, radius_km) if s not in in_ends]
    left_out = 0
    if has_land is not None:
        kept_ends = [s for s in ends if has_land(s)]
        kept_along = [s for s in along if has_land(s)]
        left_out = len(ends) - len(kept_ends) + len(along) - len(kept_along)
        ends, along = kept_ends, kept_along
    length = sum(haversine_km(*a, *b) for a, b in pairwise(pts))
    lats = [lat for lat, _ in path]
    lons = [path[0][1]]
    for _, lon in path[1:]:
        lons.append(_unwrapped(lons[-1], lon))  # one frame, so a Pacific crossing stays together
    west, east = min(lons), max(lons)
    return {
        "path": [
            [[round(lat, 5), round(lon, 5)] for lat, lon in piece] for piece in pieces_of(path)
        ],
        "squares": {"ends": [s.name for s in ends], "along": [s.name for s in along]},
        "left_out": left_out,
        "length_km": round(length, 1),
        "bounds": {
            "south": round(min(lats), 5),
            "north": round(max(lats), 5),
            "west": round(west, 5),
            "east": round(east, 5),
        },
    }
