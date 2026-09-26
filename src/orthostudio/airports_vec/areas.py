# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Airport surfaces: the hangar, apron and taxiway areas of every aerodrome.

Specification: ``docs/specs/airports-geometry.md`` section 4. Origin: Ortho4XP
``src/O4_Airport_Utils.py`` ``build_hangar_areas`` (700-734), ``build_apron_areas`` (734-775)
and ``build_taxiway_areas`` (775-805), with ``improved_buffer`` (``O4_Vector_Utils.py``
1021-1062), already ported as :func:`orthostudio.vectors.roads.improved_buffer`.

All three do the same thing: the OSM ways attached to an airport become tile-local polygons
rounded to 7 decimals, and are then unioned -- hangars and taxiways through ``improved_buffer``
(2 m and 15 m), aprons raw. They run **after** the runways and the size filter, and **before**
:func:`orthostudio.airports_vec.discover.update_boundaries`, which unions the four areas into the
footprint the elevation smoothing and the imagery cover read.

Nothing here touches the network.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from shapely import geometry, ops

from orthostudio.airports_vec.model import Airport, AirportSet
from orthostudio.airports_vec.runways import EventHandler
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.geom import ensure_multipolygon
from orthostudio.vectors.osmdata import COORD_DIGITS, OsmData
from orthostudio.vectors.roads import improved_buffer
from orthostudio.vectors.water import scale_x

__all__ = [
    "DEFAULT_AREA_PARAMS",
    "AreaParams",
    "AreaResult",
    "build_areas",
]


@dataclass(frozen=True, slots=True)
class AreaParams:
    """The buffers of the three surface builders, in metres (``:729-731``, ``:786-788``)."""

    hangar_buffer: float = 2.0
    hangar_separation: float = 1.0
    hangar_simplify: float = 0.5
    taxiway_buffer: float = 15.0
    """A taxiway is 30 m wide whatever OSM says: no ``width`` tag is read here (``:787``)."""
    taxiway_separation: float = 3.0
    taxiway_simplify: float = 0.5


DEFAULT_AREA_PARAMS = AreaParams()
"""Ortho4XP's constants, as a module-level singleton (ruff B008)."""


@dataclass(frozen=True, slots=True)
class AreaResult:
    """What :func:`build_areas` did, beside filling the record."""

    hangars: int = 0
    """Hangar ways that became a polygon."""
    aprons: int = 0
    taxiways: int = 0
    skipped: int = 0
    """Ways that did not make a valid polygon and were left out."""
    events: tuple[OsxpError, ...] = ()


def _local(store: OsmData, node_ids: Sequence[int], tile: TileRef) -> NDArray[np.float64]:
    """Node ids to tile-local coordinates, rounded to 7 decimals (``:706-712``)."""
    degrees = np.array([store.nodes[node_id] for node_id in node_ids], dtype=np.float64)
    return np.round(degrees - np.array([[tile.lon, tile.lat]], dtype=np.float64), COORD_DIGITS)


def _polygons_of_ways(
    store: OsmData,
    way_ids: Iterable[int],
    tile: TileRef,
    surface: str,
    airport: Airport,
    events: list[OsxpError],
    on_event: EventHandler | None,
) -> list[geometry.Polygon]:
    """Every way as a tile-local polygon; an invalid one is reported and skipped.

    Ortho4XP catches the ``Polygon()`` constructor and the ``is_valid`` test separately, with two
    messages and one outcome (``:714-733``, ``:745-775``); OrthoStudio XP reports one code with
    a reason.
    """
    out: list[geometry.Polygon] = []
    for way_id in way_ids:
        try:
            pol = geometry.Polygon(_local(store, store.ways[way_id], tile))
            valid = pol.is_valid
        except (KeyError, ValueError):
            valid = False
        if not valid:
            err = OsxpError(
                "OSM_AIRPORT_SURFACE_INVALID",
                context={"surface": surface, "airport": str(airport.key), "way": way_id},
            )
            events.append(err)
            if on_event is not None:
                on_event(err)
            continue
        out.append(pol)
    return out


def build_areas(
    airports: AirportSet,
    store: OsmData,
    tile: TileRef,
    params: AreaParams = DEFAULT_AREA_PARAMS,
    *,
    on_event: EventHandler | None = None,
) -> AreaResult:
    """Build the hangar, apron and taxiway areas of every airport of ``airports``.

    Fills :attr:`~orthostudio.airports_vec.model.SurfaceAreas.hangar`, ``apron`` and ``taxiway`` in
    place, as Ortho4XP overwrites the three fields that held the way ids; the way ids stay in
    :attr:`orthostudio.airports_vec.model.Airport.ways`, which is what Ortho4XP keeps as the second
    element of its ``apron`` and ``taxiway`` tuples.

    Call it on the set :func:`orthostudio.airports_vec.discover.discard_unwanted` returned: Ortho4XP
    builds these areas for the airports that survived and for no others (``:200-203``).
    """
    scalx = scale_x(tile)
    events: list[OsxpError] = []
    counts = {"hangars": 0, "aprons": 0, "taxiways": 0}
    for airport in airports:
        hangars = _polygons_of_ways(
            store, airport.ways["hangar"], tile, "hangar", airport, events, on_event
        )
        counts["hangars"] += len(hangars)
        airport.areas.hangar = ensure_multipolygon(
            improved_buffer(
                ops.unary_union(hangars),
                params.hangar_buffer,
                params.hangar_separation,
                params.hangar_simplify,
                scalx,
            )
        )
        aprons = _polygons_of_ways(
            store, airport.ways["apron"], tile, "apron", airport, events, on_event
        )
        counts["aprons"] += len(aprons)
        airport.areas.apron = ensure_multipolygon(ops.unary_union(aprons))
        taxiway_ids = airport.ways["taxiway"]
        counts["taxiways"] += len(taxiway_ids)
        lines = geometry.MultiLineString(
            [geometry.LineString(_local(store, store.ways[i], tile)) for i in taxiway_ids]
        )
        airport.areas.taxiway = ensure_multipolygon(
            improved_buffer(
                lines,
                params.taxiway_buffer,
                params.taxiway_separation,
                params.taxiway_simplify,
                scalx,
            )
        )
    return AreaResult(
        hangars=counts["hangars"],
        aprons=counts["aprons"],
        taxiways=counts["taxiways"],
        skipped=len(events),
        events=tuple(events),
    )
