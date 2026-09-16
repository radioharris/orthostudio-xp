"""Road and railway levelling layers: the successor of ``include_roads`` in Ortho4XP.

Specification: ``docs/specs/vectors-water-roads.md`` sections 3 and 5. Origin:
``O4_Vector_Map.py:228-355`` and the metric helpers of ``O4_Vector_Utils.py`` it uses:
``weighted_normals:1071-1094``, ``shift_way:1096-1098``, ``improved_buffer:1021-1069``,
``length_in_meters:1002-1011``.

What the stage does: among the roads and railways of the tile, find those that are *banked* --
whose ground differs by more than ``road_banking_limit`` between their centre line and their
side -- buffer them by ``lane_width``, and hand the resulting polygons to the noder as
``INTERP_ALT``, so that Triangle4XP levels the mesh under them. Roads that are flat enough are
left to the DEM.

The airport areas Ortho4XP computes in ``include_airports`` enter here as an *injected* input
(arbitration A2): wave 1 has no airport builder, so ``airports=None`` builds a tile as if it
had no aerodrome, and wave 2 will fill :class:`AirportAreas` in.

The metric helpers of ``O4_Vector_Utils`` that only the roads need in wave 1 live here;
``encode_multipolygon``, ``cut_to_tile`` and ``refine_way``, which the water builder needs
too, live in ``water.py`` (they would all live in one shared module if P4 had not been cut
file by file).

Nothing here touches the network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from shapely import affinity, geometry
from shapely.geometry.base import BaseGeometry

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.geom import polygons_of
from orthostudio.vectors.noding import Layer
from orthostudio.vectors.osmdata import OsmData, SkipHandler
from orthostudio.vectors.tags import ROAD_EXCLUSION_TAGS
from orthostudio.vectors.water import (
    LAT_TO_M,
    M_TO_LAT,
    Altitudes,
    EventHandler,
    encode_multipolygon,
    refine_way,
    scale_x,
)

__all__ = [
    "AirportAreas",
    "RoadParams",
    "RoadResult",
    "build_road_layers",
    "improved_buffer",
    "length_in_meters",
    "refine_way",
    "shift_way",
    "weighted_normals",
]

APT_ARRAY_SIZE = 1001
"""Side of the airport raster (``O4_Airport_Utils.py:911``)."""

ROAD_REFINE_M = 100.0
"""Maximum segment length of a buffered road polygon (``O4_Vector_Map.py:345``)."""

BUFFER_SEPARATION_M = 2.0
"""Grow-then-shrink margin that closes the gaps between nearby roads (``:339``)."""

BUFFER_SIMPLIFY_M = 0.5
"""Simplification of the buffered road area (``:340``)."""

APT_BUFFER_EXTRA_M = 2.0
"""The airport area is buffered by ``lane_width + 2`` metres before subtraction (``:335``)."""


# -- metric helpers (``O4_Vector_Utils.py``, module-level ``scalx`` made explicit) --------------


def length_in_meters(way: NDArray[np.floating] | BaseGeometry, scalx: float) -> float:
    """Length of a way or a geometry in the anisotropic frame (``:1002-1011``)."""
    line = (
        geometry.LineString(np.asarray(way, dtype=np.float64))
        if not isinstance(way, BaseGeometry)
        else way
    )
    return float(affinity.scale(line, scalx, 1, origin=(0, 0)).length * LAT_TO_M)


def weighted_normals(
    way: NDArray[np.floating], side: str = "left", scalx: float = 1.0
) -> NDArray[np.float64]:
    """Unit normal at each point of ``way``, averaged between its two segments (``:1071-1094``).

    Transcribed statement by statement, ``1e-6`` guards included: the shift of a road is what
    decides whether it is levelled at all, so any drift here changes the tile.
    """
    pts = np.asarray(way, dtype=np.float64)
    n = len(pts)
    if n < 2:
        return np.zeros((n, 2), dtype=np.float64)
    sign = np.array([[-1 / scalx, 1]]) if side == "left" else np.array([[1 / scalx, -1]])
    tg = pts[1:] - pts[:-1]
    tg[:, 0] *= scalx
    tg = tg / (1e-6 + np.linalg.norm(tg, axis=1)).reshape(n - 1, 1)
    tg = np.vstack([tg, tg[-1]])
    if n > 2:
        scale = 1e-6 + np.linalg.norm(tg[1:-1] + tg[:-2], axis=1).reshape(n - 2, 1)
        tg[1:-1] = (tg[1:-1] + tg[:-2]) / scale
        if (pts[0] == pts[-1]).all():
            scale_closed = 1e-6 + np.linalg.norm(tg[0] + tg[-1])
            tg[0] = tg[-1] = (tg[0] + tg[-1]) / scale_closed
    return np.roll(tg, 1, axis=1) * sign


def shift_way(
    way: NDArray[np.floating], shift: float, side: str = "left", scalx: float = 1.0
) -> NDArray[np.float64]:
    """``way`` moved ``shift`` metres to its left (or right) (``:1096-1098``)."""
    return np.asarray(way, dtype=np.float64) + shift * M_TO_LAT * weighted_normals(way, side, scalx)


def improved_buffer(
    geom: BaseGeometry,
    buffer_width: float,
    separation_width: float,
    simplify_length: float,
    scalx: float,
) -> BaseGeometry:
    """Buffer in metres, grown then shrunk so small holes stay closed (``:1021-1069``).

    ``join_style=2`` (mitre), ``mitre_limit=1.5`` and ``quad_segs=1`` (Ortho4XP spells it
    ``resolution``, the name shapely 2.1 deprecates) are Ortho4XP's; they are
    what gives the buffered roads their angular outline, so they are not "improvements" to
    revisit -- they are the shape of the reference mesh.
    """
    bw = buffer_width * M_TO_LAT
    sw = separation_width * M_TO_LAT
    sl = simplify_length * M_TO_LAT
    scaled = affinity.affine_transform(geom, [scalx, 0, 0, 1, 0, 0])
    out = scaled.buffer(bw + sw, join_style=2, mitre_limit=1.5, quad_segs=1)
    out = out.buffer(-1 * sw, join_style=2, mitre_limit=1.5, quad_segs=1)
    if sl:
        out = out.simplify(sl)
    return affinity.affine_transform(out, [1 / scalx, 0, 0, 1, 0, 0])


# -- parameters and results --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoadParams:
    """The four Ortho4XP settings of the road stage (``O4_Config_Utils.py:122-142``)."""

    road_level: int = 1
    road_banking_limit: float = 0.5
    lane_width: float = 4.0
    max_levelled_segs: int = 200000


DEFAULT_ROAD_PARAMS = RoadParams()
"""The Ortho4XP defaults, as a module-level singleton (ruff B008)."""


@dataclass(frozen=True, slots=True)
class AirportAreas:
    """The two airport inputs of ``include_roads`` (arbitration A2, spec 5.1).

    ``array`` is the 1001x1001 boolean raster of the airport neighbourhoods
    (``build_airport_array``, ``O4_Airport_Utils.py:910-921``) and ``area`` the flattened
    surfaces themselves (``treated_area``, ``O4_Vector_Map.py:217``). Both default to "no
    airport", which is what a tile without aerodrome gives in Ortho4XP too.
    """

    array: NDArray[np.bool_] | None = None
    area: BaseGeometry = field(default_factory=geometry.Polygon)

    def contains(self, point: NDArray[np.floating]) -> bool:
        """Does this tile-local point fall in an airport neighbourhood? (``:231-240``)."""
        if self.array is None:
            return False
        col, row = np.minimum(np.maximum(np.round(np.asarray(point) * 1000), 0), 1000)
        return bool(self.array[int(1000 - row), int(col)])


NO_AIRPORTS = AirportAreas()
"""A tile with no aerodrome: an empty raster and an empty area."""


@dataclass(frozen=True, slots=True)
class RoadResult:
    """The road contribution to the PSLG of one tile."""

    layers: tuple[Layer, ...] = ()
    """One ``INTERP_ALT`` layer, or nothing when no road needs levelling."""
    seeds: dict[str, NDArray[np.float64]] = field(default_factory=dict)
    """``{"INTERP_ALT": (S, 2)}``, one seed per buffered polygon."""
    area: geometry.MultiPolygon = field(default_factory=geometry.MultiPolygon)
    """The buffered road area, cut to the tile."""
    counts: dict[str, int] = field(default_factory=dict)
    """``ways``, ``levelled``, ``rejected``, ``vertices``, ``polygons``, ``dropped``."""


# -- the builder -------------------------------------------------------------------------------


def _banked_ways(
    store: OsmData,
    dem: Altitudes,
    params: RoadParams,
    airports: AirportAreas,
    scalx: float,
    on_skip: SkipHandler | None = None,
) -> tuple[list[geometry.LineString], int, int]:
    """The ways of one store that need levelling (``road_is_too_much_banked``, ``:229-249``).

    The ``filtered_segs`` budget is local to the store, exactly as it is local to each
    ``OSM_to_MultiLineString`` call in Ortho4XP (``O4_OSM_Utils.py:594``).
    """
    banked: list[geometry.LineString] = []
    filtered_segs = 0
    rejected = 0
    for way in store.ways_with(exclude=ROAD_EXCLUSION_TAGS, on_skip=on_skip):
        coords = way.coords
        if airports.contains(coords[0]) or airports.contains(coords[-1]):
            too_banked = True
        elif filtered_segs >= params.max_levelled_segs:
            too_banked = False
        else:
            shifted = shift_way(coords, params.lane_width, "left", scalx)
            drop = np.abs(dem.alt_vec(coords) - dem.alt_vec(shifted))
            too_banked = bool((drop >= params.road_banking_limit).any())
        if not too_banked:
            rejected += 1
            continue
        banked.append(geometry.LineString(coords))
        filtered_segs += len(coords)
    return banked, filtered_segs, rejected


def build_road_layers(
    osm_data: OsmData | Sequence[OsmData],
    tile: TileRef,
    dem: Altitudes,
    params: RoadParams = DEFAULT_ROAD_PARAMS,
    airports: AirportAreas | None = None,
    *,
    on_event: EventHandler | None = None,
) -> RoadResult:
    """The ``INTERP_ALT`` layer of the roads of one tile (``include_roads``, ``:228-355``).

    ``osm_data`` is the ``big_roads`` store, or ``(big_roads, small_roads)`` from
    ``road_level >= 2``; which selectors filled them is the OSM stage's business
    (``orthostudio.sources.osm.layers_for``), not this builder's (spec 3.1).
    """
    on_skip: SkipHandler | None = None
    if on_event is not None:
        handler = on_event

        def on_skip(code: str, context: dict[str, object]) -> None:
            handler(OsxpError(code, context=context))

    if not params.road_level:
        return RoadResult(counts={"ways": 0, "levelled": 0, "rejected": 0})
    stores = [osm_data] if isinstance(osm_data, OsmData) else list(osm_data)
    scalx = scale_x(tile)
    apt = NO_AIRPORTS if airports is None else airports
    banked: list[geometry.LineString] = []
    vertices = 0
    rejected = 0
    for store in stores:
        lines, segs, skipped = _banked_ways(store, dem, params, apt, scalx, on_skip)
        banked.extend(lines)
        vertices += segs
        rejected += skipped
    counts = {
        "ways": sum(len(store.first["w"]) for store in stores),
        "levelled": len(banked),
        "rejected": rejected,
        "vertices": vertices,
    }
    if not banked:
        return RoadResult(counts=counts | {"polygons": 0, "dropped": 0})
    network: BaseGeometry = geometry.MultiLineString(banked)
    # Unconditional, as in Ortho4XP (``O4_Vector_Map.py:330-333``), even when the airport area is
    # empty: GEOS re-nodes the linework of the left operand, so ``difference(POLYGON EMPTY)``
    # turns 2 783 ways into 2 936 and moves the buffered outline by 8e-9 deg2 on +43+005.
    # Skipping it "because it is a no-op" cost 60 nodes and 2 road polygons against the Ortho4XP
    # no-airport reference (P4 integration, docs/benchmarks/p4-vectors.md).
    network = network.difference(
        improved_buffer(apt.area, params.lane_width + APT_BUFFER_EXTRA_M, 0, 0, scalx)
    )
    road_area = improved_buffer(
        network, params.lane_width, BUFFER_SEPARATION_M, BUFFER_SIMPLIFY_M, scalx
    )

    def alt_vec_shift(way: NDArray[np.floating]) -> NDArray[np.float64]:
        """``:251-252``: the altitude ``lane_width`` metres to the left, i.e. on the road."""
        return dem.alt_vec(shift_way(way, params.lane_width, "left", scalx))

    encoded = encode_multipolygon(
        polygons_of(road_area),
        pol_to_alt=alt_vec_shift,
        refine=ROAD_REFINE_M,
        scalx=scalx,
    )
    counts |= {"polygons": len(encoded.geometry.geoms), "dropped": encoded.dropped}
    if encoded.is_empty:
        return RoadResult(counts=counts)
    return RoadResult(
        layers=(encoded.layer("INTERP_ALT"),),
        seeds={"INTERP_ALT": encoded.seeds},
        area=encoded.geometry,
        counts=counts,
    )
