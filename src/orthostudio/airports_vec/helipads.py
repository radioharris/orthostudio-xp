"""Helipads: the flat ``INTERP_ALT`` polygons of ``flatten_helipads``.

Origin: Ortho4XP ``src/O4_Airport_Utils.py:1370-1464``. A helipad is flattened, never
textured differently: the marker is ``INTERP_ALT``, the altitude is the mean of the raster
over the outline, and a helipad already covered by a runway, a taxiway, an apron or a patch
is skipped -- those surfaces have their own altitude and a second flat polygon inside them
would fight it.

Spec: ``docs/specs/airports-encoding.md`` section 9.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from math import cos, pi, sin

import numpy as np
from numpy.typing import NDArray
from shapely import geometry, ops
from shapely.geometry.base import BaseGeometry

from orthostudio.airports_vec.model import m_to_lon
from orthostudio.model import TileRef
from orthostudio.vectors.geom import cut_to_tile, ensure_multipolygon
from orthostudio.vectors.osmdata import COORD_DIGITS, OsmData
from orthostudio.vectors.seeds import polygon_seeds
from orthostudio.vectors.tags import OsmType
from orthostudio.vectors.water import M_TO_LAT, Altitudes

__all__ = [
    "HELIPAD_ANGLE",
    "HELIPAD_RADIUS_M",
    "HELIPAD_SIDES",
    "HELIPAD_TAG",
    "HelipadResult",
    "encode_helipads",
    "helipad_areas",
]

HELIPAD_TAG = ("aeroway", "helipad")
"""The tag that makes a way or a node a helipad (``:1377-1381``, ``:1411-1415``)."""

HELIPAD_RADIUS_M = 9.0
"""Radius of the hexagon a helipad *node* is grown into (``:1424-1432``)."""

HELIPAD_SIDES = 6
"""``range(7)`` vertices at ``k * pi / 3``: a closed hexagon (``:1426-1431``)."""

HELIPAD_ANGLE = pi / 3
"""The angle step of that hexagon, spelled as ``:1427-1430`` spells it."""


@dataclass(frozen=True, slots=True)
class HelipadResult:
    """What the helipads add to the PSLG of one tile."""

    ways: tuple[tuple[NDArray[np.float64], NDArray[np.float64]], ...] = ()
    """``(xy, z)`` per inserted outline, in insertion order; every ``z`` is constant."""
    seeds: NDArray[np.float64] = field(default_factory=lambda: np.zeros((0, 2), dtype=np.float64))
    """One ``representative_point`` per inserted polygon (``:1455``)."""
    found: int = 0
    """Helipads recognised before the cut to the tile (Ortho4XP's ``total``,
    ``:1403``/``:1441``)."""
    area: BaseGeometry = field(default_factory=geometry.Polygon)
    """Their union, cut to the tile: what was really flattened."""


def _tagged(data: OsmData, kind: OsmType) -> list[int]:
    """Ids of the elements of ``kind`` carrying ``aeroway=helipad``, in reading order.

    Ortho4XP walks ``dicosmw`` / ``dicosmn`` (insertion order, which is the reading order) and
    tests the tag dictionary (``:1377-1381``, ``:1411-1415``). ``OsmData`` keeps both in the
    same order, so iterating the tag map of that type gives the same sequence.
    """
    key, value = HELIPAD_TAG
    tags = data.tags[kind]
    source: Iterable[int] = data.ways if kind == "w" else data.nodes
    return [osm_id for osm_id in source if tags.get(osm_id, {}).get(key) == value]


def helipad_areas(
    data: OsmData,
    tile: TileRef,
    treated_area: BaseGeometry,
    *,
    radius_m: float = HELIPAD_RADIUS_M,
) -> tuple[geometry.MultiPolygon, int]:
    """The helipad polygons of the tile and how many were recognised (``:1372-1450``).

    Closed ways first (``:1376-1403``), then the nodes grown into hexagons (``:1405-1441``);
    a node inside a way-helipad **or** inside ``treated_area`` is dropped, and the ways
    themselves are dropped when they meet ``treated_area``. The union is cut to the tile at
    the end only, so a helipad on the tile side is kept and clipped (``:1443-1445``).
    """
    polygons: list[geometry.Polygon] = []
    found = 0
    origin = np.array([[tile.lon, tile.lat]], dtype=np.float64)
    for way_id in _tagged(data, "w"):
        nodes = data.ways[way_id]
        if not nodes or nodes[0] != nodes[-1]:
            continue
        way = np.round(
            np.array([data.nodes[i] for i in nodes], dtype=np.float64).reshape(-1, 2) - origin,
            COORD_DIGITS,
        )
        if len(way) < 4:
            continue
        polygon = geometry.Polygon(way)
        if (
            polygon.is_empty
            or not polygon.is_valid
            or not polygon.area
            or polygon.intersects(treated_area)
        ):
            continue
        polygons.append(polygon)
        found += 1
    way_area = ops.unary_union(polygons)
    ring = np.array(
        [
            [
                cos(k * HELIPAD_ANGLE) * radius_m * m_to_lon(tile.lat),
                sin(k * HELIPAD_ANGLE) * radius_m * M_TO_LAT,
            ]
            for k in range(HELIPAD_SIDES + 1)
        ],
        dtype=np.float64,
    )
    for node_id in _tagged(data, "n"):
        centre = np.round(np.array(data.nodes[node_id], dtype=np.float64) - origin[0], COORD_DIGITS)
        point = geometry.Point(centre)
        if point.intersects(way_area) or point.intersects(treated_area):
            continue
        polygons.append(geometry.Polygon(np.round(centre + ring, COORD_DIGITS)))
        found += 1
    return ensure_multipolygon(cut_to_tile(ops.unary_union(polygons))), found


def encode_helipads(
    data: OsmData,
    tile: TileRef,
    dem: Altitudes,
    treated_area: BaseGeometry,
    *,
    radius_m: float = HELIPAD_RADIUS_M,
) -> HelipadResult:
    """Flatten every helipad of the tile (``flatten_helipads``, ``:1370-1464``).

    Each polygon of :func:`helipad_areas` is inserted as its exterior ring with a **constant**
    altitude -- the mean of the raster over that ring (``:1452-1454``) -- and seeded. The two
    blocks Ortho4XP keeps commented out inside the collecting loops (``:1424-1428``,
    ``:1443-1447``) are dropped: they cannot run.
    """
    area, found = helipad_areas(data, tile, treated_area, radius_m=radius_m)
    ways: list[tuple[NDArray[np.float64], NDArray[np.float64]]] = []
    kept: list[geometry.Polygon] = []
    for polygon in area.geoms:
        if polygon.is_empty or not polygon.is_valid or not polygon.area:
            continue
        way = np.array(polygon.exterior.coords, dtype=np.float64)
        z = np.full(len(way), float(np.mean(dem.alt_vec(way))), dtype=np.float64)
        ways.append((way, z))
        kept.append(polygon)
    seeds = polygon_seeds(geometry.MultiPolygon(kept))[0] if kept else np.zeros((0, 2))
    return HelipadResult(tuple(ways), seeds, found, area)
