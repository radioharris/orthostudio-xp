# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Coastline and sea: ``natural=coastline`` ways to SEA lines and SEA region seeds.

Successor of ``include_sea`` (Ortho4XP ``O4_Vector_Map.py:363-451``) and of
``coastline_to_MultiPolygon`` (``O4_Vector_Utils.py:848-999``). The rules, their origin and
the wanted differences are written down in ``docs/specs/vectors-coastline.md``; in short:

* the coastline is inserted as *lines* with marker ``SEA``, never as polygons;
* the sea polygons exist only to place one Triangle4XP region seed each -- get them wrong and
  a whole tile flips between land and water;
* OSM orients a coastline way with **land on its left**: a counter-clockwise ring is an
  island, a clockwise one an interior sea, and an open chain is closed by walking the tile
  border clockwise from where it leaves the tile to where the next chain enters it;
* islands and interior seas are then removed from / added to that area with one symmetric
  difference.

Everything that Ortho4XP does with a Python loop over shapely scalars (``project`` /
``interpolate`` per endpoint, list concatenation per vertex) is done here over arrays; the
chain walk itself stays sequential because it is a topological traversal, but it costs one
step per chain, not per vertex.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from math import ceil
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely import geometry
from shapely.geometry.base import BaseGeometry

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.geom import (
    UNIT_SIDES,
    UNIT_SQUARE,
    cut_to_tile,
    ensure_multilinestring,
    ensure_multipolygon,
    wrap_local_x,
)
from orthostudio.vectors.noding import MARKERS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from orthostudio.vectors.noding import Layer

__all__ = [
    "CoastParams",
    "CoastResult",
    "CoastStats",
    "CoastTopology",
    "OsmWaysSource",
    "SeaArea",
    "build_sea_layers",
    "coastline_multilinestring",
    "coastline_to_multipolygon",
    "cut_to_tile",
    "ensure_multilinestring",
    "ensure_multipolygon",
    "way_set_order",
]

F64 = NDArray[np.float64]

#: The tile in local coordinates, counter-clockwise (``O4_Vector_Utils.py:743-753``); it is :
# :data:`orthostudio.vectors.geom.UNIT_SQUARE`, kept under its Ortho4XP name for this
# module's readers.
TILE_SQUARE = UNIT_SQUARE
#: Its boundary as a line, subtracted by ``strictly_inside`` (``:766-774``).
TILE_SIDES = UNIT_SIDES
#: The border walked **clockwise** from the south-west corner, the parametrisation of
#: ``bd_coord`` / ``bd_point`` (``O4_Vector_Utils.py:982-999``). Total length 4.
BORDER_CW = geometry.LineString([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)])


# --------------------------------------------------------------------------------------
# Small geometry helpers, transcribed once in orthostudio.vectors.geom
# --------------------------------------------------------------------------------------


def bd_coord(points: NDArray[np.floating]) -> F64:
    """Arclength of each point projected on :data:`BORDER_CW`, in ``[0, 4)``.

    Vectorised ``O4_Vector_Utils.py:982-989`` (one ``LineString.project`` call per point).
    """
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if pts.size == 0:
        return np.zeros(0, dtype=np.float64)
    located = shapely.line_locate_point(BORDER_CW, shapely.points(pts))
    return np.asarray(located, dtype=np.float64)


def bd_point(coords: NDArray[np.floating] | Sequence[float]) -> F64:
    """Inverse of :func:`bd_coord`, modulo 4. Vectorised ``O4_Vector_Utils.py:991-999``."""
    values = np.asarray(coords, dtype=np.float64).reshape(-1)
    if values.size == 0:
        return np.zeros((0, 2), dtype=np.float64)
    pts = shapely.line_interpolate_point(BORDER_CW, np.mod(values, 4.0))
    return shapely.get_coordinates(pts)


def way_set_order(count: int) -> list[int]:
    """Indices of ``count`` ways in the order Ortho4XP iterates them.

    ``O4_OSM_Utils.py:596`` loops over ``dicosmfirst["w"]``, a Python ``set`` of the internal
    ids ``-1, -2, ... -count`` added in file order, so the iteration order is the hash order
    of that set (``hash(-1) == -2``, every other int hashes to itself). The set is rebuilt the
    same way here rather than re-deriving CPython's probing: same insertions, same order.
    Measured identical to Ortho4XP on the 424 coastline ways of +43+005.
    """
    ids: set[int] = set()
    for k in range(1, count + 1):
        ids.add(-k)
    return [-osmid - 1 for osmid in ids]


# --------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------


@runtime_checkable
class OsmWaysSource(Protocol):
    """What :func:`build_sea_layers` needs of an OSM layer: nodes by id and ordered ways.

    ``orthostudio.sources.osm.OsmSnapshot`` satisfies it; so does any object exposing ``nodes``
    (``.id``, ``.lat``, ``.lon``) and ``ways`` (``.id``, ``.nodes``, ``.tags``).
    """

    @property
    def nodes(self) -> Sequence[Any]: ...

    @property
    def ways(self) -> Sequence[Any]: ...


@dataclass(frozen=True, slots=True)
class CoastParams:
    """Knobs of the coastline builder; the defaults are Ortho4XP's, except where noted."""

    custom_source: bool = False
    """User-supplied coastline: every ring is an island whatever its winding
    (``O4_Vector_Utils.py:887``)."""
    round_digits: int = 7
    """Decimals of the local coordinates (``O4_OSM_Utils.py:608-618``)."""
    border_tolerance: float = 1e-5
    """How far an open chain may stop from a tile side and still count as reaching it
    (``O4_Vector_Utils.py:893-913``)."""
    max_border_walk: int = 1000
    """Steps after which a non-closing walk is declared faulty (``:941-948``)."""
    ways_in_set_order: bool = True
    """Reproduce the set iteration order of Ortho4XP (see :func:`way_set_order`)."""
    tag_filter: tuple[str, str] | None = ("natural", "coastline")
    """Tag a way must carry to be used, as Ortho4XP's ``dicosmfirst`` filter does. A way with no
    tags at all is kept: a snapshot may not carry them."""
    on_bad_coastline: Literal["raise", "record"] = "raise"
    """``raise``: bad OSM data raises the ``OSM_COAST_*`` error the registry declares BLOCKING
    (OrthoStudio XP decision). ``record``: Ortho4XP's behaviour, an empty sea area and no seed at
    all, the error only recorded in :attr:`CoastResult.errors`."""


@dataclass(frozen=True, slots=True)
class CoastStats:
    """Counts, for the decision report and ``stats.json``."""

    ways_read: int = 0
    ways_dropped: int = 0
    sea_lines: int = 0
    rings: int = 0
    islands: int = 0
    interior_seas: int = 0
    open_chains: int = 0
    border_polygons: int = 0
    sea_polygons: int = 0
    seeds: int = 0
    islands_swallowed: int = 0
    tile_assumed_sea: bool = False
    inverted_tile: bool = False


@dataclass(frozen=True, slots=True)
class CoastResult:
    """Everything the PSLG assembler needs from the coastline layer."""

    sea_lines: geometry.MultiLineString = field(default_factory=geometry.MultiLineString)
    """The SEA linework in tile-local coordinates, ready for ``node_layers``."""
    sea_polygons: geometry.MultiPolygon = field(default_factory=geometry.MultiPolygon)
    """The sea area; one region seed per polygon."""
    sea_equiv: geometry.MultiPolygon = field(default_factory=geometry.MultiPolygon)
    """Injected pass-through: the large lakes the *water* builder marks SEA_EQUIV (spec 6).

    **Reserved, not read in wave 1** (review 5): this builder stores what it is given and
    nothing downstream reads it back -- the SEA_EQUIV layer is emitted by the water family
    itself. It is the slot wave 2 needs to treat a large lake as sea when it closes the
    coastline; carrying it does not, on its own, justify building water before the coast."""
    islands: geometry.MultiPolygon = field(default_factory=geometry.MultiPolygon)
    """Counter-clockwise rings, i.e. land."""
    interior_seas: geometry.MultiPolygon = field(default_factory=geometry.MultiPolygon)
    """Clockwise rings, i.e. water enclosed by land."""
    open_chains_closed: geometry.MultiPolygon = field(default_factory=geometry.MultiPolygon)
    """The polygons obtained by closing the open chains along the tile border, before the
    islands and interior seas are taken into account."""
    seeds: F64 = field(default_factory=lambda: np.zeros((0, 2), dtype=np.float64))
    """(S, 2) representative points of :attr:`sea_polygons`, marker ``SEA``."""
    stats: CoastStats = field(default_factory=CoastStats)
    errors: tuple[OsxpError, ...] = ()
    """Non-fatal problems, when ``on_bad_coastline='record'`` kept them non-fatal."""

    def to_layers(
        self, alt_vec: Callable[[NDArray[np.floating]], NDArray[np.floating]] | None = None
    ) -> list[Layer]:
        """The layer list for ``node_layers``: one SEA layer, z sampled by ``alt_vec``.

        ``alt_vec`` is ``Dem.alt_vec`` (``docs/specs/dem.md``); ``None`` leaves z at 0.
        """
        if self.sea_lines.is_empty:
            return []
        z = None
        if alt_vec is not None:
            coords = shapely.get_coordinates(self.sea_lines)
            z = np.asarray(alt_vec(coords), dtype=np.float64)
        return [(self.sea_lines, MARKERS["SEA"], z)]

    def seed_records(self) -> list[tuple[float, float, int]]:
        """``(x, y, marker)`` triples, the shape ``write_poly_file`` wants."""
        return [(float(x), float(y), MARKERS["SEA"]) for x, y in self.seeds]


# --------------------------------------------------------------------------------------
# Input conversion
# --------------------------------------------------------------------------------------


def _ways_from_source(source: OsmWaysSource, params: CoastParams) -> list[F64]:
    """WGS84 ``(lon, lat)`` arrays of the tagged ways, in source order."""
    coords = {int(n.id): (float(n.lon), float(n.lat)) for n in source.nodes}
    out: list[F64] = []
    for way in source.ways:
        tags = getattr(way, "tags", None) or {}
        if params.tag_filter is not None and tags:
            key, value = params.tag_filter
            if tags.get(key) != value:
                continue
        try:
            out.append(np.array([coords[int(n)] for n in way.nodes], dtype=np.float64))
        except KeyError:
            # An incomplete way (a node the layer does not carry): Ortho4XP would raise on the
            # same data because ``dicosmn_id_map`` would miss the id; dropping is the safe
            # transcription (spec 5).
            out.append(np.zeros((0, 2), dtype=np.float64))
    return out


def _is_snapshot_like(osm_data: Any) -> bool:
    """True for an ``OsmSnapshot``-shaped source: ``nodes`` and ``ways`` as *sequences*.

    ``orthostudio.vectors.osmdata.OsmData`` also has ``nodes`` and ``ways``, but as the
    *dictionaries* of ``OSM_layer``; it is rejected here so the caller gets the clear message below
    instead of a silent misreading.
    """
    nodes, ways = getattr(osm_data, "nodes", None), getattr(osm_data, "ways", None)
    if nodes is None or ways is None:
        return False
    if isinstance(nodes, Mapping) or isinstance(ways, Mapping):
        raise TypeError(
            "this looks like an OsmData store: pass its ways_with() geometries "
            "(already tile-local) rather than the store itself"
        )
    return True


def coastline_multilinestring(
    osm_data: Any,
    tile: TileRef,
    params: CoastParams | None = None,
) -> tuple[geometry.MultiLineString, int, int]:
    """The coastline in tile-local coordinates: ``OSM_to_MultiLineString`` for one layer.

    ``osm_data`` may be

    * a shapely geometry already in tile-local coordinates -- used as is;
    * an :class:`OsmWaysSource` (an ``OsmSnapshot``): WGS84 nodes and ways, converted here and
      reordered by :func:`way_set_order`;
    * a sequence of ``(n, 2)`` WGS84 ``(lon, lat)`` arrays -- same treatment;
    * a sequence of objects carrying a ``coords`` array (``osmdata.WayGeometry``): those are
      **already** tile-local, rounded, and in Ortho4XP's order, so neither the origin shift nor
      the reordering is applied again.

    Returns the linework, the number of ways read and the number dropped
    (``O4_OSM_Utils.py:587-640``).
    """
    params = params or CoastParams()
    if isinstance(osm_data, BaseGeometry):
        lines = ensure_multilinestring(osm_data)
        return lines, len(lines.geoms), 0
    ways: Sequence[NDArray[np.floating]]
    already_local = False
    if _is_snapshot_like(osm_data):
        ways = _ways_from_source(osm_data, params)
    else:
        items = list(osm_data)
        if items and hasattr(items[0], "coords") and not isinstance(items[0], BaseGeometry):
            ways = [np.asarray(item.coords, dtype=np.float64) for item in items]
            already_local = True
        else:
            ways = items
    if already_local or not params.ways_in_set_order:
        order: Sequence[int] = range(len(ways))
    else:
        order = way_set_order(len(ways))
    origin = np.zeros((1, 2)) if already_local else np.array([[tile.lon, tile.lat]], np.float64)
    parts: list[geometry.LineString] = []
    dropped = 0
    for index in order:
        way = np.asarray(ways[index], dtype=np.float64) - origin
        if not already_local:
            if way.ndim == 2 and way.shape[1] == 2:
                way[:, 0] = wrap_local_x(way[:, 0])
            way = np.round(way, params.round_digits)
        if way.ndim != 2 or len(way) < 2:
            dropped += 1
            continue
        parts.append(geometry.LineString(way))
    return geometry.MultiLineString(parts), len(ways), dropped


def _encoded_lines(coastline: BaseGeometry) -> geometry.MultiLineString:
    """What ``encode_MultiLineString`` inserts: each line cut to the tile again, flattened.

    ``O4_Vector_Utils.py:437-462`` with ``skip_cut=False`` and ``refine=False``. The second
    cut is an identity on data already cut, but it flattens a GeometryCollection.
    """
    lines = ensure_multilinestring(coastline)
    if lines.is_empty:
        return geometry.MultiLineString()
    cut = shapely.intersection(np.asarray(lines.geoms, dtype=object), TILE_SQUARE)
    parts: list[geometry.LineString] = []
    for piece in cut:
        parts.extend(g for g in ensure_multilinestring(piece).geoms if not g.is_empty)
    return geometry.MultiLineString(parts)


# --------------------------------------------------------------------------------------
# Sea polygons
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class CoastTopology:
    """Rings and open chains of the coastline, classified as Ortho4XP does."""

    islands: list[F64] = field(default_factory=list)
    interior_seas: list[F64] = field(default_factory=list)
    chain_coords: list[F64] = field(default_factory=list)
    inits: list[float] = field(default_factory=list)
    ends: list[float] = field(default_factory=list)
    bad_points: list[tuple[float, float]] = field(default_factory=list)


def _classify(coastline: geometry.MultiLineString, params: CoastParams) -> CoastTopology:
    """``O4_Vector_Utils.py:885-921``: rings by winding, open chains by their border ends."""
    topo = CoastTopology()
    open_chains: list[F64] = []
    for line in coastline.geoms:
        coords = shapely.get_coordinates(line)
        if line.is_ring:
            if len(coords) < 4:
                continue  # degenerate ring: Ortho4XP would raise in Polygon() (spec 4.4)
            if params.custom_source or geometry.LinearRing(coords).is_ccw:
                topo.islands.append(coords)
            else:
                topo.interior_seas.append(coords)
        else:
            open_chains.append(coords)
    if not open_chains:
        return topo
    ends_xy = np.array([[c[0], c[-1]] for c in open_chains], dtype=np.float64)
    # |x - int(x)| and |y - int(y)|: the distance to the nearest tile side, Ortho4XP style.
    offsets = np.abs(ends_xy - np.trunc(ends_xy)).min(axis=2)
    bad = offsets > params.border_tolerance
    for row, col in zip(*np.nonzero(bad), strict=True):
        x, y = ends_xy[row, col]
        topo.bad_points.append((float(y), float(x)))
    arclengths = bd_coord(ends_xy.reshape(-1, 2)).reshape(-1, 2)
    topo.chain_coords = open_chains
    topo.inits = [float(v) for v in arclengths[:, 0]]
    topo.ends = [float(v) for v in arclengths[:, 1]]
    return topo


def _walk_border(topo: CoastTopology, params: CoastParams, tile: TileRef) -> list[F64]:
    """``O4_Vector_Utils.py:926-965``: chain the open chains through the tile border.

    Kept sequential and kept float-exact: ``inits.index`` / ``bdcoords.index`` /
    ``bdcoords.remove`` are Ortho4XP's own first-match-by-value semantics, ambiguity included.
    """
    inits = topo.inits
    ends = topo.ends
    bdcoords = sorted(ends + inits)
    bdpolys: list[F64] = []

    def encode_to_next(coord: float, new_way: list[F64], remove_coords: list[float]) -> float:
        if coord in inits:
            idx = inits.index(coord)
            new_way.append(topo.chain_coords[idx])
            next_coord = ends[idx]
            remove_coords.append(coord)
            remove_coords.append(next_coord)
            return next_coord
        try:
            idx = bdcoords.index(coord)
        except ValueError as exc:
            # Ortho4XP lets this ValueError escape and kills step 1 (spec 5): it happens when a
            # chain is walked twice because two chains share an init arclength.
            x, y = bd_point([coord])[0]
            raise OsxpError(
                "OSM_COAST_TRIPLE_JUNCTION",
                context={
                    "tile": tile.name,
                    "lat": float(y) + tile.lat,
                    "lon": float(x) + tile.lon,
                },
            ) from exc
        if idx < len(bdcoords) - 1:
            next_coord = bdcoords[idx + 1]
            loop_limit = next_coord
        else:
            next_coord = bdcoords[0]
            loop_limit = next_coord + 4
        corners = np.arange(float(ceil(coord)), loop_limit, 1.0)
        if corners.size:
            new_way.append(bd_point(corners))
        return next_coord

    while bdcoords:
        new_way: list[F64] = []
        remove_coords: list[float] = []
        first_coord = bdcoords[0]
        next_coord = encode_to_next(first_coord, new_way, remove_coords)
        count = 0
        while next_coord != first_coord:
            count += 1
            next_coord = encode_to_next(next_coord, new_way, remove_coords)
            if count == params.max_border_walk:
                raise OsxpError(
                    "OSM_COAST_ORIENTATION", context={"tile": tile.name, "steps": count}
                )
        if new_way:
            bdpolys.append(np.concatenate(new_way))
        for coord in remove_coords:
            try:
                bdcoords.remove(coord)
            except ValueError as exc:
                x, y = bd_point([coord])[0]
                raise OsxpError(
                    "OSM_COAST_TRIPLE_JUNCTION",
                    context={
                        "tile": tile.name,
                        "lat": float(y) + tile.lat,
                        "lon": float(x) + tile.lon,
                    },
                ) from exc
    return bdpolys


def _ring_polygons(rings: Sequence[NDArray[np.floating]]) -> NDArray[np.object_]:
    """``Polygon(r).buffer(0)`` for every ring, in one vectorised buffer call.

    ``buffer(0)`` is what repairs a self-intersecting OSM ring (``O4_Vector_Utils.py:966``).
    """
    if not rings:
        return np.empty(0, dtype=object)
    polygons = np.array([geometry.Polygon(r) for r in rings], dtype=object)
    return np.asarray(shapely.buffer(polygons, 0), dtype=object)


def _union(polygons: NDArray[np.object_]) -> BaseGeometry:
    """``unary_union`` of an array of polygons, empty when there is none."""
    if polygons.size == 0:
        return geometry.MultiPolygon()
    return shapely.union_all(polygons)


def _count_swallowed(islands: NDArray[np.object_], lakes: NDArray[np.object_]) -> int:
    """Islands entirely inside an interior sea: Ortho4XP loses them in the union (spec 4.4)."""
    if islands.size == 0 or lakes.size == 0:
        return 0
    hits = shapely.STRtree(lakes).query(islands, predicate="covered_by")
    return int(np.unique(hits[0]).size) if hits.size else 0


@dataclass(frozen=True, slots=True)
class SeaArea:
    """What :func:`coastline_to_multipolygon` reconstructs from the coastline topology."""

    sea: geometry.MultiPolygon
    """The sea area: the border-closed polygons, symmetric-differenced with the rings."""
    islands: geometry.MultiPolygon
    interior_seas: geometry.MultiPolygon
    border_polygons: geometry.MultiPolygon
    """The polygons obtained by closing the open chains along the tile border."""
    topology: CoastTopology
    chains_closed: int
    """How many polygons the border walk produced (0 when the tile is assumed all sea)."""
    islands_swallowed: int


def coastline_to_multipolygon(
    coastline: geometry.MultiLineString,
    tile: TileRef,
    params: CoastParams | None = None,
) -> SeaArea:
    """``O4_Vector_Utils.py:848-980``: the sea area of the tile.

    Raises ``OSM_COAST_OPEN_END`` / ``OSM_COAST_ORIENTATION`` / ``OSM_COAST_TRIPLE_JUNCTION``
    on faulty OSM data; the caller decides what to do with them (:class:`CoastParams`).
    """
    params = params or CoastParams()
    topo = _classify(coastline, params)
    if topo.bad_points:
        points = [
            (round(lat + tile.lat, 7), round(lon + tile.lon, 7)) for lat, lon in topo.bad_points
        ]
        raise OsxpError("OSM_COAST_OPEN_END", context={"tile": tile.name, "points": points[:8]})
    bdpolys = _walk_border(topo, params, tile)
    chains_closed = len(bdpolys)
    if not bdpolys:
        bdpolys = [np.array([(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)])]
    border = _ring_polygons([p for p in bdpolys if len(p) >= 3])
    islands = _ring_polygons(topo.islands)
    lakes = _ring_polygons(topo.interior_seas)
    outpol = _union(border)
    # union(union(islands), union(lakes)) is Ortho4XP's unary_union of the concatenation, one
    # union_all cheaper: the two halves are needed separately anyway.
    island_union = _union(islands)
    lake_union = _union(lakes)
    inpol = ensure_multipolygon(cut_to_tile(shapely.union(island_union, lake_union)))
    return SeaArea(
        sea=ensure_multipolygon(outpol.symmetric_difference(inpol)),
        islands=ensure_multipolygon(island_union),
        interior_seas=ensure_multipolygon(lake_union),
        border_polygons=ensure_multipolygon(outpol),
        topology=topo,
        chains_closed=chains_closed,
        islands_swallowed=_count_swallowed(islands, lakes),
    )


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def build_sea_layers(
    osm_data: Any,
    tile: TileRef,
    params: CoastParams | None = None,
    *,
    sea_equiv: geometry.MultiPolygon | None = None,
) -> CoastResult:
    """Build the SEA line layer and the SEA region seeds of ``tile``.

    ``osm_data`` is the ``natural=coastline`` layer: an :class:`OsmWaysSource`, a sequence of
    WGS84 ``(lon, lat)`` arrays, or a shapely geometry already in tile-local coordinates.
    ``sea_equiv`` is the large-lake MultiPolygon of the *water* builder, carried through
    untouched so the assembler has a single object for every sea-class region (spec 6); it is
    empty in wave 1, exactly like the airport layers of arbitration A2.

    The order of operations is Ortho4XP's (``O4_Vector_Map.py:402-451``): the lines are encoded
    first, from the *strictly* cut coastline, then the topology is rebuilt from a *different*
    cut -- rings uncut, open chains cut and line-merged -- and the seeds come last.
    """
    params = params or CoastParams()
    coastline, ways_read, ways_dropped = coastline_multilinestring(osm_data, tile, params)
    empty_stats = CoastStats(ways_read=ways_read, ways_dropped=ways_dropped)
    if coastline.is_empty:
        return CoastResult(sea_equiv=sea_equiv or geometry.MultiPolygon(), stats=empty_stats)

    sea_lines = _encoded_lines(cut_to_tile(coastline, strictly_inside=True))

    loops = [line for line in coastline.geoms if line.is_ring]
    remainder = ensure_multilinestring(
        cut_to_tile(
            geometry.MultiLineString([line for line in coastline.geoms if not line.is_ring]),
            strictly_inside=True,
        )
    )
    if not remainder.is_empty:
        remainder = ensure_multilinestring(shapely.line_merge(remainder))
    rebuilt = geometry.MultiLineString(list(remainder.geoms) + loops)

    errors: tuple[OsxpError, ...] = ()
    empty = geometry.MultiPolygon()
    try:
        area = coastline_to_multipolygon(rebuilt, tile, params)
    except OsxpError as exc:
        if params.on_bad_coastline == "raise":
            raise
        errors = (exc,)
        area = SeaArea(empty, empty, empty, empty, CoastTopology(), 0, 0)

    seeds = np.zeros((0, 2), dtype=np.float64)
    if not area.sea.is_empty:
        seeds = shapely.get_coordinates(shapely.point_on_surface(np.asarray(area.sea.geoms)))

    topo = area.topology
    tile_assumed_sea = not topo.chain_coords and not area.sea.is_empty
    stats = CoastStats(
        ways_read=ways_read,
        ways_dropped=ways_dropped,
        sea_lines=len(sea_lines.geoms),
        rings=len(loops),
        islands=len(topo.islands),
        interior_seas=len(topo.interior_seas),
        open_chains=len(topo.chain_coords),
        border_polygons=area.chains_closed,
        sea_polygons=len(area.sea.geoms),
        seeds=len(seeds),
        islands_swallowed=area.islands_swallowed,
        tile_assumed_sea=tile_assumed_sea,
        inverted_tile=tile_assumed_sea and bool(topo.interior_seas) and not topo.islands,
    )
    return CoastResult(
        sea_lines=sea_lines,
        sea_polygons=area.sea,
        sea_equiv=sea_equiv or geometry.MultiPolygon(),
        islands=area.islands,
        interior_seas=area.interior_seas,
        open_chains_closed=area.border_polygons,
        seeds=seeds,
        stats=stats,
        errors=errors,
    )
