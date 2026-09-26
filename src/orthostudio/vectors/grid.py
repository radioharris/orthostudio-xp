# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Orthophoto grid and gluing border: the two DUMMY layers every tile gets for free.

Successor of ``O4_Vector_Map.build_poly_file`` lines 96-152. Two families of constraint
edges that carry no attribute (marker ``DUMMY``) but must be mesh edges:

* the **orthophoto grid**, one line per boundary between two 4096x4096 textures at
  ``mesh_zl`` (a texture is 16 x 16 web-mercator tiles), so that no triangle straddles two
  textures;
* the **gluing border**, the four sides of the tile cut in 2048 equal segments, so that two
  neighbouring tiles share the same border vertices and their meshes glue.

Both are described in ``docs/specs/vectors-assembly.md`` section 3. Everything here is
arithmetic on Python floats, in Ortho4XP's order: the result is a node key rounded to 9 decimals
and an algebraically equivalent formula is not numerically equivalent.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely.geometry import LineString, MultiLineString

from orthostudio.imagery.grid import TEXTURE_TILES, texture_at, tile_to_wgs84
from orthostudio.model import TileRef

__all__ = [
    "BORDER_SEGMENTS",
    "GRID_MARGIN",
    "GridLines",
    "gluing_border",
    "grid_and_border",
    "ortho_grid_abscissae",
    "ortho_grid_lines",
]

GRID_MARGIN = 2.0**-5
"""``eps`` of ``O4_Vector_Map.py:131``: grid lines overshoot the tile by 1/32 of a degree."""

BORDER_SEGMENTS = 2048
"""``segs`` of ``O4_Vector_Map.py:137``: segments per side of the gluing border."""

AltVec = Callable[[NDArray[np.floating]], NDArray[np.float64]]
"""``Dem.alt_vec``: metres above sea level at each ``(x, y)`` row, tile-local coordinates."""


class GridLines(NamedTuple):
    """One insertion pass of DUMMY lines, ready to become a layer of the noder."""

    name: str
    """``grid_vertical``, ``grid_horizontal`` or ``border``."""
    geometry: MultiLineString
    z: NDArray[np.float64]
    """One altitude per coordinate of ``shapely.get_coordinates(geometry)``."""


def ortho_grid_abscissae(tile: TileRef, mesh_zl: int) -> tuple[list[float], list[float]]:
    """Tile-local x of the vertical grid lines and y of the horizontal ones.

    ``O4_Vector_Map.py:98-117``: the texture grid at ``mesh_zl`` is walked in steps of 16
    web-mercator tiles from the north-west corner of the tile to its south-east one, the two
    tile edges are added, and each family is deduplicated (by exact float equality, as a
    Python ``set`` does) and sorted.

    The two conversions are ``orthostudio.imagery.grid``'s transcriptions of ``wgs84_to_orthogrid``
    and ``gtile_to_wgs84`` (``O4_Geo_Utils.py:66-77, 127-134``), so there is exactly one copy
    of each formula in OrthoStudio XP.
    """
    north_west = texture_at(tile.lat + 1, tile.lon, mesh_zl, "")
    south_east = texture_at(tile.lat, tile.lon + 1, mesh_zl, "")
    xgrid: set[float] = set()
    ygrid: set[float] = set()
    for til_x in range(north_west.til_x + TEXTURE_TILES, south_east.til_x + 1, TEXTURE_TILES):
        _, lon = tile_to_wgs84(til_x, north_west.til_y, mesh_zl)
        xgrid.add(lon - tile.lon)
    for til_y in range(north_west.til_y + TEXTURE_TILES, south_east.til_y + 1, TEXTURE_TILES):
        lat, _ = tile_to_wgs84(north_west.til_x, til_y, mesh_zl)
        ygrid.add(lat - tile.lat)
    xgrid.add(0)
    xgrid.add(1)
    ygrid.add(0)
    ygrid.add(1)
    return sorted(xgrid), sorted(ygrid)


def ortho_grid_lines(tile: TileRef, mesh_zl: int) -> tuple[MultiLineString, MultiLineString]:
    """The vertical and the horizontal grid lines (``O4_Vector_Map.py:131-135``).

    They are two separate geometries because Ortho4XP inserts every vertical before every
    horizontal, and that order decides the altitude of their crossings (spec 2.2).
    """
    xgrid, ygrid = ortho_grid_abscissae(tile, mesh_zl)
    eps = GRID_MARGIN
    vertical = MultiLineString([LineString([(x, 0.0 - eps), (x, 1.0 + eps)]) for x in xgrid])
    horizontal = MultiLineString([LineString([(0.0 - eps, y), (1.0 + eps, y)]) for y in ygrid])
    return vertical, horizontal


def gluing_border(segments: int = BORDER_SEGMENTS) -> MultiLineString:
    """The four sides of the tile as polylines of ``segments`` segments (``:136-152``).

    Order and orientation are Ortho4XP's: south, north, west, east, each from 0 to 1. The
    abscissae are ``k / segments``, exact in binary for a power of two, which is what makes
    two neighbouring tiles agree on their common vertices to the last bit.
    """
    steps = np.arange(0, segments + 1) / segments
    zeros = np.zeros(segments + 1)
    ones = np.ones(segments + 1)
    return MultiLineString(
        [
            LineString(np.column_stack([steps, zeros])),
            LineString(np.column_stack([steps, ones])),
            LineString(np.column_stack([zeros, steps])),
            LineString(np.column_stack([ones, steps])),
        ]
    )


def grid_and_border(
    tile: TileRef, mesh_zl: int, alt_vec: AltVec, *, segments: int = BORDER_SEGMENTS
) -> list[GridLines]:
    """The three DUMMY passes that close a tile's vector data, in insertion order.

    ``alt_vec`` samples the elevation raster (``O4_Vector_Map.py:127, 149``: the grid and the
    border take their altitude from the DEM, never from a vector). It is called once per pass
    instead of once per line, which is identical: the query has no memory.
    """
    vertical, horizontal = ortho_grid_lines(tile, mesh_zl)
    out = []
    for name, geometry in (
        ("grid_vertical", vertical),
        ("grid_horizontal", horizontal),
        ("border", gluing_border(segments)),
    ):
        coords = shapely.get_coordinates(geometry)
        out.append(GridLines(name, geometry, np.asarray(alt_vec(coords), dtype=np.float64)))
    return out
