# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Runway reconstruction: OSM ways and relations to the rectangles X-Plane flattens.

Specification: ``docs/specs/airports-geometry.md`` section 3. Origin: Ortho4XP
``src/O4_Airport_Utils.py`` ``sort_and_reconstruct_runways`` (329-682), with the helpers of
``src/O4_Vector_Utils.py`` it calls (``min_bounding_rectangle`` 1292-1316,
``buffer_simple_way`` 1101-1110, ``weighted_normals`` 1071-1094, ``length_in_meters``
1002-1011).

The rule, in one sentence: *a runway is a rectangle*. OSM spells it either as a closed way
around its outline or as a centre line cut into parts; this module accepts the first only when
it really is close to a rectangle, re-assembles the second, grows it to a rectangle, and drops
the linear copy when both describe the same runway.

It runs on the record :mod:`orthostudio.airports_vec.discover` produced and fills three of the six
slots of :class:`orthostudio.airports_vec.model.SurfaceAreas`; ``discard_unwanted`` and
``update_boundaries`` read them back.

Nothing here touches the network.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import atan2

import numpy as np
from numpy.typing import NDArray
from shapely import affinity, geometry, ops
from shapely.geometry.base import BaseGeometry

from orthostudio.airports_vec.model import M_TO_LAT, Airport, AirportSet, RunwayPart, great_circle_m
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.geom import ensure_multipolygon
from orthostudio.vectors.osmdata import COORD_DIGITS, OsmData
from orthostudio.vectors.roads import length_in_meters, weighted_normals
from orthostudio.vectors.water import scale_x

__all__ = [
    "DEFAULT_RUNWAY_PARAMS",
    "RUNWAY_MAX_RECT_AREA",
    "EventHandler",
    "RunwayParams",
    "RunwayResult",
    "buffer_simple_way",
    "build_runways",
    "min_bounding_rectangle",
]

RUNWAY_MAX_RECT_AREA = 9999.0
"""``min_bounding_rectangle:1296`` starts its search at 9999 square degrees."""

EventHandler = Callable[[OsxpError], None]
"""``docs/specs/errors.md``: what the builders call instead of ``UI.vprint``."""


@dataclass(frozen=True, slots=True)
class RunwayParams:
    """The constants of ``sort_and_reconstruct_runways``, named so a test can move them."""

    min_area: float = 1e-7
    """Below this many square degrees a closed way is aeromodelism, not a runway (``:368``)."""
    rectangle_tolerance: float = 0.0008
    """Hausdorff distance, in degrees, beyond which a shape is not a rectangle (``:375``)."""
    merge_angle: float = 0.2
    """Radians: two runway parts heading further apart than this are two runways (``:572``)."""
    duplicate_overlap: float = 0.6
    """Overlap ratio above which a linear runway is the area one again (``:648``)."""
    width_margin_m: float = 10.0
    """Shoulders added to a tagged runway width (``:635``)."""
    width_base_m: float = 30.0
    """Width of a runway with no ``width`` tag, before the per-kilometre term (``:637``)."""


DEFAULT_RUNWAY_PARAMS = RunwayParams()
"""Ortho4XP's constants, as a module-level singleton (ruff B008)."""


@dataclass(frozen=True, slots=True)
class RunwayResult:
    """What :func:`build_runways` did, beside filling the record."""

    runways: int = 0
    """Reconstructed runways, area and linear together."""
    merged: int = 0
    """Pairs of linear runway parts that were joined (spec section 3.3)."""
    rejected: int = 0
    """Closed ways or relation rings refused as runways."""
    dropped_small: int = 0
    """Closed ways below :attr:`RunwayParams.min_area` (aeromodelism)."""
    events: tuple[OsxpError, ...] = ()
    """The rejections, as coded errors."""


# -- metric helpers (``O4_Vector_Utils.py``, module-level ``scalx`` made explicit) --------------


def min_bounding_rectangle(pol: BaseGeometry, scalx: float) -> geometry.Polygon:
    """Smallest-area rectangle around ``pol`` in the anisotropic frame (``:1292-1316``).

    Rotating calipers over the convex hull: every hull edge is tried as the rectangle's
    direction and the smallest area wins, ties going to the first edge tested. Ortho4XP relies on
    an unbound local when the search finds nothing; OrthoStudio XP raises a coded error instead
    (spec section 3.6).
    """
    scaled = affinity.affine_transform(pol, [scalx, 0, 0, 1, 0, 0]).convex_hull
    way = np.asarray(scaled.exterior.coords, dtype=np.float64)
    edges = way[1:] - way[:-1]
    min_area = RUNWAY_MAX_RECT_AREA
    best: tuple[int, float, float, float, float, float] | None = None
    for i in range(len(edges)):
        angle = atan2(edges[i, 1], edges[i, 0])
        (xmin, ymin, xmax, ymax) = affinity.rotate(
            scaled, -1 * angle, origin=(way[i][0], way[i][1]), use_radians=True
        ).bounds
        test_area = (ymax - ymin) * (xmax - xmin)
        if test_area < min_area:
            min_area = test_area
            best = (i, angle, xmin, ymin, xmax, ymax)
    if best is None:
        raise OsxpError(
            "SYS_INTERNAL_ERROR",
            context={"type": "ValueError", "detail": "no bounding rectangle for this polygon"},
            message="A runway outline has no minimum bounding rectangle.",
        )
    (i, angle, xmin, ymin, xmax, ymax) = best
    return affinity.affine_transform(
        affinity.rotate(
            geometry.box(xmin, ymin, xmax, ymax),
            angle,
            origin=(way[i][0], way[i][1]),
            use_radians=True,
        ),
        [1 / scalx, 0, 0, 1, 0, 0],
    )


def buffer_simple_way(way: NDArray[np.floating], width: float, scalx: float) -> NDArray[np.float64]:
    """Closed ring ``width`` metres wide around a polyline (``:1101-1110``)."""
    pts = np.asarray(way, dtype=np.float64)
    offset = width * M_TO_LAT
    normals = weighted_normals(pts, "left", scalx)
    return np.concatenate(
        (
            pts - 0.5 * offset * normals,
            (pts + 0.5 * offset * normals)[::-1],
            pts[:1] - 0.5 * offset * normals[:1],
        )
    )


def _local(store: OsmData, node_ids: Sequence[int], tile: TileRef) -> NDArray[np.float64]:
    """Node ids to tile-local coordinates, rounded to 7 decimals (``:355-362``)."""
    degrees = np.array([store.nodes[node_id] for node_id in node_ids], dtype=np.float64)
    return np.round(degrees - np.array([tile.lon, tile.lat], dtype=np.float64), COORD_DIGITS)


def _axis_of(
    pol: geometry.Polygon, scalx: float
) -> tuple[NDArray[np.float64], NDArray[np.float64], float]:
    """Axis and width of an area runway, read off its bounding rectangle (``:398-414``)."""
    rect = np.asarray(min_bounding_rectangle(pol, scalx).exterior.coords, dtype=np.float64)
    if length_in_meters(rect[0:2], scalx) < length_in_meters(rect[1:3], scalx):
        return (rect[0] + rect[1]) / 2, (rect[2] + rect[3]) / 2, length_in_meters(rect[0:2], scalx)
    return (rect[1] + rect[2]) / 2, (rect[0] + rect[3]) / 2, length_in_meters(rect[1:3], scalx)


# -- the stage ----------------------------------------------------------------------------------


def build_runways(
    airports: AirportSet,
    store: OsmData,
    tile: TileRef,
    params: RunwayParams = DEFAULT_RUNWAY_PARAMS,
    *,
    on_event: EventHandler | None = None,
) -> RunwayResult:
    """Reconstruct every airport's runways (``sort_and_reconstruct_runways``, ``:329-682``).

    Fills :attr:`~orthostudio.airports_vec.model.SurfaceAreas.runway`, ``runway_as_area`` and
    ``runway_as_line`` on every airport of ``airports``, in place, as Ortho4XP overwrites
    ``apt["runway"]``; the returned value is the report, not the geometry.
    """
    scalx = scale_x(tile)
    events: list[OsxpError] = []
    counts = {"runways": 0, "merged": 0, "rejected": 0, "dropped_small": 0}

    def reject(airport: Airport, reason: str) -> None:
        counts["rejected"] += 1
        err = OsxpError(
            "OSM_RUNWAY_REJECTED", context={"airport": str(airport.key), "reason": reason}
        )
        events.append(err)
        if on_event is not None:
            on_event(err)

    for airport in airports:
        as_area: list[RunwayPart] = []
        linear: list[list[int]] = []
        linear_width: list[float] = []
        for way_id in airport.ways["runway"]:
            nodes = store.ways[way_id]
            if nodes[0] != nodes[-1]:
                linear.append(list(nodes))
                linear_width.append(_tagged_width(store.tags["w"].get(way_id)))
                continue
            outcome = _area_runway(
                _local(store, nodes, tile),
                scalx,
                params,
                "custom" in store.tags["w"].get(way_id, {}),
            )
            if isinstance(outcome, str):
                if outcome == "too small":
                    counts["dropped_small"] += 1
                else:
                    reject(airport, outcome)
                continue
            as_area.append(outcome)
        for rel_id in airport.runway_rels:
            ring = store.relations[rel_id]["outer"][0]
            outcome = _area_runway(
                _local(store, ring, tile),
                scalx,
                params,
                "custom" in store.tags["r"].get(rel_id, {}),
            )
            if isinstance(outcome, str):
                if outcome == "too small":
                    counts["dropped_small"] += 1
                else:
                    reject(airport, outcome)
                continue
            as_area.append(outcome)
        linear, linear_width, joins = _merge_runway_parts(store, linear, linear_width, params)
        counts["merged"] += joins
        as_line = _grow_linear_runways(store, tile, scalx, linear, linear_width, as_area, params)
        airport.areas.runway = ensure_multipolygon(
            ops.unary_union([runway.polygon for runway in as_area + as_line])
        )
        airport.areas.runway_as_area = tuple(as_area)
        airport.areas.runway_as_line = tuple(as_line)
        counts["runways"] += len(as_area) + len(as_line)
    return RunwayResult(
        runways=counts["runways"],
        merged=counts["merged"],
        rejected=counts["rejected"],
        dropped_small=counts["dropped_small"],
        events=tuple(events),
    )


def _tagged_width(tags: Mapping[str, str] | None) -> float:
    """The ``width`` tag as a float, ``0`` when absent or unreadable (``:428-437``)."""
    try:
        return float((tags or {})["width"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def _area_runway(
    coords: NDArray[np.float64], scalx: float, params: RunwayParams, custom: bool
) -> RunwayPart | str:
    """One closed way or relation ring as a runway, or the reason it is not (``:352-427``)."""
    pol = geometry.Polygon(coords)
    if pol.is_empty or not pol.is_valid:
        return "invalid geometry"
    if pol.area < params.min_area:
        return "too small"
    if not custom and min_bounding_rectangle(pol, scalx).hausdorff_distance(pol) > (
        params.rectangle_tolerance
    ):
        return "not a rectangle"
    start, end, width = _axis_of(pol, scalx)
    return RunwayPart(polygon=pol, start=start, end=end, width=width)


def _heading(store: OsmData, part: Sequence[int]) -> float:
    """``arctan2(dlon, dlat)`` of a runway part (``:541-547``; the argument order is Ortho4XP's)."""
    delta = np.array(store.nodes[part[-1]]) - np.array(store.nodes[part[0]])
    return float(np.arctan2(*delta))


def _same_heading(a: float, b: float, tolerance: float) -> bool:
    """Two headings equal modulo pi, within ``tolerance`` radians (``:563-573``)."""
    turns = np.array([-2 * np.pi, -np.pi, 0.0, np.pi, 2 * np.pi])
    return bool(np.min(np.abs(turns - (a - b))) < tolerance)


def _joined(a: list[int], b: list[int]) -> list[int] | None:
    """The four ways two runway parts can share an end node (``:574-622``)."""
    if a[-1] == b[0]:
        return a + b[1:]
    if a[-1] == b[-1]:
        return a + b[-2::-1]
    if a[0] == b[0]:
        return a[-1::-1] + b[1:]
    if a[0] == b[-1]:
        return b + a[1:]
    return None


def _merge_runway_parts(
    store: OsmData,
    linear: list[list[int]],
    linear_width: list[float],
    params: RunwayParams,
) -> tuple[list[list[int]], list[float], int]:
    """Re-assemble the parts OSM cut a runway into (``:536-625``, spec section 3.3)."""
    joins = 0
    grouped = False
    while not grouped:
        grouped = True
        for i in range(len(linear) - 1):
            head_i = _heading(store, linear[i])
            for j in range(i + 1, len(linear)):
                if not _same_heading(head_i, _heading(store, linear[j]), params.merge_angle):
                    continue
                merged = _joined(linear[i], linear[j])
                if merged is None:
                    continue
                width = max(linear_width[i], linear_width[j])
                linear = [linear[k] for k in range(len(linear)) if k not in (i, j)] + [merged]
                linear_width = [
                    linear_width[k] for k in range(len(linear_width)) if k not in (i, j)
                ] + [width]
                grouped = False
                joins += 1
                break
            if not grouped:
                break
    return linear, linear_width, joins


def _grow_linear_runways(
    store: OsmData,
    tile: TileRef,
    scalx: float,
    linear: Sequence[Sequence[int]],
    linear_width: Sequence[float],
    as_area: list[RunwayPart],
    params: RunwayParams,
) -> list[RunwayPart]:
    """Grow every centre line into a rectangle and drop the duplicates (``:626-663``)."""
    as_line: list[RunwayPart] = []
    origin = np.array([tile.lon, tile.lat], dtype=np.float64)
    for node_ids, tagged in zip(linear, linear_width, strict=True):
        first = store.nodes[node_ids[0]]
        last = store.nodes[node_ids[-1]]
        length_m = great_circle_m(first, last)
        start = np.round(np.array(first) - origin, COORD_DIGITS)
        end = np.round(np.array(last) - origin, COORD_DIGITS)
        # Ortho4XP spells the two branches out (``:634-638``); the floor division is its own.
        width = tagged + params.width_margin_m if tagged else params.width_base_m + length_m // 1000
        pol = geometry.Polygon(buffer_simple_way(np.vstack((start, end)), width, scalx))
        for index, other in enumerate(as_area):
            overlap = other.polygon.intersection(pol).area
            if overlap > params.duplicate_overlap * min(pol.area, other.polygon.area):
                as_area[index] = RunwayPart(
                    polygon=other.polygon, start=start, end=end, width=width
                )
                break
        else:
            as_line.append(RunwayPart(polygon=pol, start=start, end=end, width=width))
    return as_line
