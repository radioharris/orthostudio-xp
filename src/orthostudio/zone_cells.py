# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The squares of about 1 km a zone will take, as the build takes them (``map-zones.md`` 3.2).

A zone does not stop on its outline: every mesh cell it covers a part of takes its level, so the
sharp area runs in straight lines up to a cell beyond what was drawn, and a zone under the zones
above it in the list may take none. That was seen only in X-Plane, after the build (idea 7,
2026-10-11). Here the cells are found by the build's own code (``dsf.zones``: the same zone image,
the same cell edges, the same first-zone-wins rule), so what the map shows is what is built.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import pi
from typing import Any

import numpy as np
from shapely import geometry, ops

from orthostudio.dsf.zones import _zone_image, cell_edges, cells_touched
from orthostudio.imagery.grid import texture_at
from orthostudio.imagery.providers import Provider
from orthostudio.model import TileRef
from orthostudio.zones import (
    MAX_TILE_ENTRIES,
    Zone,
    ZoneEntry,
    tiles_touched,
    too_many_zones,
    zones_for_tile,
)

__all__ = ["cell_bounds", "zone_cells"]


def cell_bounds(tile: TileRef, mesh_zl: int) -> tuple[np.ndarray, np.ndarray]:
    """The longitudes and latitudes of the mesh cells' edges, west and north first, clamped into
    the tile: the same edges as :func:`orthostudio.dsf.zones.cell_edges`, before they are rounded
    onto the zone image."""
    lat, lon = tile.lat, tile.lon
    first = texture_at(lat + 1, lon, mesh_zl, "")
    last = texture_at(lat, lon + 1, mesh_zl, "")
    side = float(2 ** (mesh_zl - 1))
    til_x = np.arange(first.til_x, last.til_x + 17, 16, dtype=np.float64)
    til_y = np.arange(first.til_y, last.til_y + 17, 16, dtype=np.float64)
    lons = np.clip((til_x / side - 1.0) * 180.0, lon, lon + 1)
    lats = np.clip(360.0 / pi * np.arctan(np.exp(pi * (1.0 - til_y / side))) - 90.0, lat, lat + 1)
    return lons, lats


def zone_cells(
    zones: Sequence[Zone],
    provider: str,
    *,
    mesh_zl: int = 19,
    registry: Mapping[str, Provider] | None = None,
) -> list[dict[str, Any]]:
    """For each zone, in the document's order: ``{id, count, area}``. ``count`` is how many cells
    it takes; ``area`` the ground they cover, the cells merged into polygons, each a list of rings
    ``[[lon, lat], ...]``, the outer one first, then its holes: a zone over a whole tile is twelve
    thousand cells at ``mesh_zl`` 19, and the page draws one outline. A zone whose count is 0
    raises nothing: the zones above it take every cell it covers.

    ``ZONE_INVALID`` (a level above the source's or above ``mesh_zl``) and ``ZONE_TOO_MANY`` are
    raised as the build raises them, from :func:`orthostudio.zones.zones_for_tile`.
    """
    taken: dict[str, list[geometry.Polygon]] = {zone.id: [] for zone in zones}
    counts: dict[str, int] = dict.fromkeys(taken, 0)
    tiles = sorted({t for zone in zones for t in tiles_touched(zone)}, key=lambda t: (t.lat, t.lon))
    for tile in tiles:
        entries: list[ZoneEntry] = []
        owners: list[str] = []
        for zone in zones:
            parts = zones_for_tile([zone], tile, provider, registry=registry, mesh_zl=mesh_zl)
            entries.extend(parts)
            owners.extend([zone.id] * len(parts))
        if not entries:
            continue
        if len(entries) > MAX_TILE_ENTRIES:
            raise too_many_zones(len(entries), tile=tile)
        image, _values = _zone_image(tile, entries, 0, "")
        touched = cells_touched(image, *cell_edges(tile, mesh_zl))
        lons, lats = cell_bounds(tile, mesh_zl)
        # value 1 is the tile's own level; value v >= 2 is entries[len - (v - 1)] (the image paints
        # the list backwards, so that the first zone, painted last, holds the highest value)
        for row, col in zip(*np.nonzero(touched >= 2), strict=True):
            owner = owners[len(entries) - (int(touched[row, col]) - 1)]
            counts[owner] += 1
            west, east = float(lons[col]), float(lons[col + 1])
            south, north = float(lats[row + 1]), float(lats[row])
            taken[owner].append(geometry.box(west, south, east, north))
    return [
        {"id": zone.id, "count": counts[zone.id], "area": _rings(taken[zone.id])} for zone in zones
    ]


def _rings(cells: Sequence[geometry.Polygon]) -> list[list[list[list[float]]]]:
    """The cells merged: one entry per polygon, its outer ring then its holes, ``[lon, lat]``.
    The cells share their edges exactly (one array of edges per tile), so they merge cleanly."""
    if not cells:
        return []
    merged = ops.unary_union(cells)
    polygons = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    out = []
    for poly in polygons:
        poly = poly.simplify(0)  # the corners left along straight runs of cells
        rings = [poly.exterior, *poly.interiors]
        out.append([[[round(x, 6), round(y, 6)] for x, y in ring.coords] for ring in rings])
    return out
