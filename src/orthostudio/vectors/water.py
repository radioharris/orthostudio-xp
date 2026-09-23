"""Inland water layers: the successor of ``include_water`` in Ortho4XP.

Specification: ``docs/specs/vectors-water-roads.md`` sections 2 and 5. Origin:
``O4_Vector_Map.py:453-589`` (``include_water``, ``filter_large_lakes``) and the two helpers
it leans on, ``O4_Vector_Utils.py:659-736`` (``MultiPolygon_to_Indexed_Polygons``) and
``:365-436`` (``encode_MultiPolygon``).

The builder returns *layers*, never a vector map: a layer is the ``(geometry, marker, z)``
triple :func:`orthostudio.vectors.noding.node_layers` takes, so the assembler decides the priority
order and the noder stays the only writer of the planar graph.

``encode_multipolygon`` (``encode_MultiPolygon``) is shared with the road builder, which
imports it from here; the geometry helpers Ortho4XP keeps in ``O4_Vector_Utils`` (``cut_to_tile``,
``ensure_MultiPolygon``) live in :mod:`orthostudio.vectors.geom`, transcribed once (review 5).

Nothing here touches the network.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from math import cos, pi
from typing import NamedTuple, Protocol

import numpy as np
import shapely
from numpy.typing import NDArray
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from shapely import geometry
from shapely.geometry.base import BaseGeometry

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.geom import cut_to_tile, polygons_of
from orthostudio.vectors.noding import MARKERS, Layer
from orthostudio.vectors.osmdata import OsmData, PolygonGeometry

__all__ = [
    "M_TO_LAT",
    "EncodedLayer",
    "LakeDecision",
    "WaterParams",
    "WaterResult",
    "build_water_layers",
    "cut_to_tile",
    "encode_multipolygon",
    "lon_to_m",
    "merge_overlapping_polygons",
    "scale_x",
]

EARTH_RADIUS = 6378137.0
"""``O4_Geo_Utils.py:5``."""
LAT_TO_M = pi * EARTH_RADIUS / 180
"""Metres per degree of latitude (``O4_Geo_Utils.py:6``)."""
M_TO_LAT = 1 / LAT_TO_M
"""Degrees of latitude per metre (``O4_Geo_Utils.py:7``)."""

EventHandler = Callable[[OsxpError], None]
"""What the builders call instead of ``UI.vprint``; see ``docs/specs/errors.md``."""


def lon_to_m(lat: float) -> float:
    """Metres per degree of longitude at that latitude (``O4_Geo_Utils.py:10-11``)."""
    return LAT_TO_M * cos(pi * lat / 180)


def scale_x(tile: TileRef) -> float:
    """``scalx = cos((lat + 0.5) * pi / 180)``: the anisotropy of the tile-local frame.

    ``O4_Vector_Map.py:27`` sets the module-level ``O4_Vector_Utils.scalx`` once per tile;
    OrthoStudio XP passes it around instead, so two tiles may be built at the same time.
    """
    return cos((tile.lat + 0.5) * pi / 180)


class Altitudes(Protocol):
    """What the builders need of a DEM: :meth:`orthostudio.dem.Dem.alt_vec`."""

    def alt_vec(self, way: NDArray[np.floating]) -> NDArray[np.float64]:
        """Altitude in metres at each ``(x, y)`` row of ``way``."""


def zero_alt(way: NDArray[np.floating]) -> NDArray[np.float64]:
    """``dem=None``: flat zero, as ``O4_Vector_Utils.dummy_alt`` (``:1376``) does."""
    return np.zeros(len(way), dtype=np.float64)


def _alt_of(dem: Altitudes | None) -> Callable[[NDArray[np.floating]], NDArray[np.float64]]:
    return zero_alt if dem is None else dem.alt_vec


# -- parameters and results --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WaterParams:
    """The five Ortho4XP settings that shape the inland water layers.

    ``min_area`` and ``max_area`` are in km², ``water_simplification`` in metres
    (``O4_Config_Utils.py:143-162``).
    """

    min_area: float = 0.001
    max_area: float = 200.0
    water_simplification: float = 0.0
    clean_bad_geometries: bool = True
    good_imagery_list: tuple[str, ...] = ()
    """Named lakes kept as water however large they are (``O4_Vector_Map.py:16``, empty)."""


DEFAULT_WATER_PARAMS = WaterParams()
"""The Ortho4XP defaults, as a module-level singleton (ruff B008)."""


@dataclass(frozen=True, slots=True)
class LakeDecision:
    """One water body compared against ``max_area`` (the decision report of the stage)."""

    osm_id: int
    kind: str
    name: str
    area_km2: float
    masked: bool
    """True when the body is encoded as ``SEA_EQUIV`` and will be masked like the sea."""


class EncodedLayer(NamedTuple):
    """What :func:`encode_multipolygon` returns: one layer plus its seeds."""

    geometry: geometry.MultiPolygon
    """The polygons in insertion order; their rings are the layer's linework."""
    z: NDArray[np.float64]
    """One altitude per coordinate of ``shapely.get_coordinates(geometry)``."""
    seeds: NDArray[np.float64]
    """(S, 2) one representative point per polygon."""
    dropped: int
    """Polygons dropped by ``area_limit`` or by an empty cut."""

    @property
    def is_empty(self) -> bool:
        """True when nothing survived the cut and the area limit."""
        return len(self.geometry.geoms) == 0

    def layer(self, marker: str | int) -> Layer:
        """The ``node_layers`` triple for this geometry."""
        bits = MARKERS[marker] if isinstance(marker, str) else int(marker)
        return (self.geometry, bits, self.z)


@dataclass(frozen=True, slots=True)
class WaterResult:
    """The inland water contribution to the PSLG of one tile."""

    layers: tuple[Layer, ...] = ()
    """``WATER`` then ``SEA_EQUIV``, empty layers omitted (spec 2.6)."""
    seeds: dict[str, NDArray[np.float64]] = field(default_factory=dict)
    """Marker name -> (S, 2) seeds, ready for ``triangle_files.write_poly_file``."""
    water: geometry.MultiPolygon = field(default_factory=geometry.MultiPolygon)
    """The merged, cut ``WATER`` polygons (what the masks and the report want)."""
    sea_equiv: geometry.MultiPolygon = field(default_factory=geometry.MultiPolygon)
    """The merged, cut ``SEA_EQUIV`` polygons."""
    lakes: tuple[LakeDecision, ...] = ()
    """Every body compared against ``max_area``, masked or not."""
    counts: dict[str, int] = field(default_factory=dict)
    """``elements``, ``polygons``, ``merged``, ``water``, ``sea_equiv``, ``dropped``."""


# -- geometry helpers (shared with roads.py) ---------------------------------------------------


def refine_way(way: NDArray[np.float64], max_length: float, scalx: float) -> NDArray[np.float64]:
    """Insert points so that no segment is longer than ``max_length`` metres.

    ``O4_Vector_Utils.py:1114-1141``: the number of insertions is
    ``int(length_in_m // max_length)`` per segment, and the new points are evenly spaced with
    the very same barycentric formula (``j/(ins+1)``), so that the coordinates match to the
    last bit. The segment lengths and each segment's insertions are computed with numpy; the
    walk over the segments themselves stays a Python loop (review 5: the docstring used to
    claim more). Its cost is linear in the inserted points and stays under a second for
    5 000 points, which is why it has not been worth a single ``np.repeat`` pass yet.
    """
    if len(way) < 2:
        return np.asarray(way, dtype=np.float64)
    pts = np.asarray(way, dtype=np.float64)
    d = pts[1:] - pts[:-1]
    seg_m = np.sqrt(d[:, 0] ** 2 * scalx**2 + d[:, 1] ** 2) * LAT_TO_M
    ins = (seg_m // max_length).astype(np.int64)
    out = np.empty((len(pts) + int(ins.sum()), 2), dtype=np.float64)
    k = 0
    for i in range(len(pts) - 1):
        out[k] = pts[i]
        k += 1
        n = int(ins[i])
        if n:
            # The two divisions of :1129-1135, not ``1 - j/(ins+1)``: the difference is one
            # ulp and it showed up as four vertices 1e-9 apart on the reference tile.
            j = np.arange(1, n + 1, dtype=np.float64)
            ahead = (j / (n + 1)).reshape(n, 1)
            behind = ((n + 1 - j) / (n + 1)).reshape(n, 1)
            out[k : k + n] = ahead * pts[i + 1] + behind * pts[i]
            k += n
    out[k] = pts[-1]
    return out


def encode_multipolygon(
    polygons: Iterable[geometry.Polygon],
    *,
    pol_to_alt: Callable[[NDArray[np.floating]], NDArray[np.float64]],
    area_limit: float = 1e-10,
    simplify: float = 0.0,
    refine: float = 0.0,
    scalx: float = 1.0,
    cut: bool = True,
) -> EncodedLayer:
    """``encode_MultiPolygon`` (``O4_Vector_Utils.py:365-436``) as one layer.

    Per polygon, in order: cut to the tile, simplify, explode, drop what is at or below
    ``area_limit``, orient (exterior counter-clockwise), refine, sample the altitude on the
    exterior then on each hole, and plant one seed at the representative point. The rings of
    the returned ``MultiPolygon`` come out in that order, which is the order
    ``shapely.get_coordinates`` walks, so ``z`` lines up with it (spec 2.4).
    """
    kept: list[geometry.Polygon] = []
    altitudes: list[NDArray[np.float64]] = []
    seeds: list[NDArray[np.float64]] = []
    dropped = 0
    for pol in polygons:
        cutted: BaseGeometry = cut_to_tile(pol) if cut else pol
        if simplify:
            cutted = cutted.simplify(simplify)
        for part in polygons_of(cutted):
            if part.area <= area_limit:
                dropped += 1
                continue
            try:
                oriented = geometry.polygon.orient(part)
            except Exception:  # Ortho4XP skips on any failure of orient (:396-399)
                dropped += 1
                continue
            rings: list[NDArray[np.float64]] = []
            for ring in (oriented.exterior, *oriented.interiors):
                if ring.is_empty:
                    continue
                way = np.array(ring.coords, dtype=np.float64)
                if refine:
                    way = refine_way(way, refine, scalx)
                rings.append(way)
                altitudes.append(np.asarray(pol_to_alt(way), dtype=np.float64).reshape(-1))
            kept.append(geometry.Polygon(rings[0], rings[1:]))
            seeds.append(np.array(oriented.representative_point().coords[0], dtype=np.float64))
    return EncodedLayer(
        geometry.MultiPolygon(kept),
        np.concatenate(altitudes) if altitudes else np.zeros(0, dtype=np.float64),
        np.array(seeds, dtype=np.float64).reshape(-1, 2),
        dropped,
    )


# -- the merge (the point of the module) -------------------------------------------------------


def merge_overlapping_polygons(
    polygons: Sequence[geometry.Polygon],
    *,
    merge: bool = True,
    on_event: EventHandler | None = None,
) -> list[geometry.Polygon]:
    """``MultiPolygon_to_Indexed_Polygons`` (``O4_Vector_Utils.py:659-736``), vectorised.

    Ortho4XP walks the polygons by decreasing bounding-box area and merges each one into every
    already-accepted polygon it overlaps *with a positive area*, one ``intersection`` call per
    candidate pair. OrthoStudio XP builds the same relation in one pass — an ``STRtree`` for the
    candidate pairs, then a ``T********`` DE-9IM test, which is "the interiors meet", i.e. exactly
    "the intersection has an area" for polygons — labels the connected components and unions each
    component once. Same components, same polygons; see spec 2.3 for the proof and 7.1 for the one
    difference (the order inside a merged component).

    ``merge=False`` is ``clean_bad_geometries=False``: every polygon is kept as it comes.
    """
    valid = [p for p in polygons if p.area and p.is_valid]
    if not merge:
        return list(polygons)
    if not valid:
        return []
    order = np.argsort(
        np.array([-geometry.box(*p.bounds).area for p in valid], dtype=np.float64), kind="stable"
    )
    ranked = np.array([valid[i] for i in order], dtype=object)
    try:
        labels, n_components = _overlap_components(ranked)
    except (ValueError, shapely.errors.ShapelyError) as exc:  # pragma: no cover - defensive
        if on_event is not None:
            on_event(
                OsxpError(
                    "OSM_WATER_MERGE_FAILED",
                    context={"reason": str(exc)},
                    message=f"Water bodies could not be merged ({exc}); they are kept separate.",
                    remedy="Nothing to do; the lakes and rivers are built one by one.",
                )
            )
        return [p for p in ranked]
    # A component takes the rank of its last member: every new member of a group deletes the
    # group's entries and appends the merged parts at the end of ``dico_pol`` (:679-691).
    last = np.zeros(n_components, dtype=np.int64)
    np.maximum.at(last, labels, np.arange(len(ranked)))
    out: list[geometry.Polygon] = []
    for component in np.argsort(last, kind="stable"):
        members = ranked[labels == component]
        if len(members) == 1:
            out.append(members[0])
            continue
        out.extend(polygons_of(shapely.union_all(members)))
    return out


def _overlap_components(ranked: NDArray[np.object_]) -> tuple[NDArray[np.int32], int]:
    """Connected components of "these two polygons overlap with a positive area"."""
    pairs = shapely.STRtree(ranked).query(ranked, predicate="intersects").T
    pairs = pairs[pairs[:, 0] < pairs[:, 1]]
    if len(pairs):
        overlapping = shapely.relate_pattern(ranked[pairs[:, 0]], ranked[pairs[:, 1]], "T********")
        pairs = pairs[overlapping]
    size = len(ranked)
    graph = coo_matrix(
        (np.ones(len(pairs), dtype=np.int8), (pairs[:, 0], pairs[:, 1])), shape=(size, size)
    )
    n_components, labels = connected_components(graph, directed=False)
    return labels, n_components


# -- the builder -------------------------------------------------------------------------------


def _element_areas(polygons: Sequence[PolygonGeometry]) -> dict[tuple[str, int], float]:
    """Total area per OSM element: a relation is judged whole (``:733``), not part by part."""
    areas: dict[tuple[str, int], float] = {}
    for item in polygons:
        key = (item.kind, item.id)
        areas[key] = areas.get(key, 0.0) + item.polygon.area
    return areas


def build_water_layers(
    osm_data: OsmData,
    tile: TileRef,
    params: WaterParams = DEFAULT_WATER_PARAMS,
    dem: Altitudes | None = None,
    *,
    on_event: EventHandler | None = None,
) -> WaterResult:
    """The ``WATER`` and ``SEA_EQUIV`` layers of one tile (``include_water``, ``:453-589``).

    ``osm_data`` is the ``water`` layer store (or a user file loaded with ``layer=None``);
    ``dem`` gives the vertex altitudes and may be ``None`` in tests, which zeroes them.
    """
    polygons = osm_data.multipolygons_with(on_skip=lambda code, ctx: _report(on_event, code, ctx))
    threshold = params.max_area * 1e6 / (LAT_TO_M * lon_to_m(tile.lat + 0.5))
    element_area = _element_areas(polygons)
    lakes: list[LakeDecision] = []
    seen: set[tuple[str, int]] = set()
    water_pols: list[geometry.Polygon] = []
    sea_pols: list[geometry.Polygon] = []
    for item in polygons:
        key = (item.kind, item.id)
        area = element_area[key]
        masked = area >= threshold
        if masked:
            name = item.tags.get("name", "")
            if name and name in params.good_imagery_list:
                masked = False
            if key not in seen:
                area_km2 = area * LAT_TO_M * lon_to_m(tile.lat + 0.5) / 1e6
                lakes.append(LakeDecision(item.id, item.kind, name, area_km2, masked))
                if masked and on_event is not None:
                    on_event(
                        OsxpError(
                            "OSM_LAKE_TREATED_AS_SEA",
                            context={
                                "name": name or f"{item.kind}{item.id}",
                                "area_km2": f"{area_km2:.0f}",
                            },
                        )
                    )
        seen.add(key)
        (sea_pols if masked else water_pols).append(item.polygon)
    merged_water = merge_overlapping_polygons(
        water_pols, merge=params.clean_bad_geometries, on_event=on_event
    )
    merged_sea = merge_overlapping_polygons(
        sea_pols, merge=params.clean_bad_geometries, on_event=on_event
    )
    alt = _alt_of(dem)
    area_limit = params.min_area / 10000
    simplify = params.water_simplification * M_TO_LAT
    encoded_water = encode_multipolygon(
        merged_water, pol_to_alt=alt, area_limit=area_limit, simplify=simplify
    )
    encoded_sea = encode_multipolygon(
        merged_sea, pol_to_alt=alt, area_limit=area_limit, simplify=simplify
    )
    layers: list[Layer] = []
    seeds: dict[str, NDArray[np.float64]] = {}
    for name, encoded in (("WATER", encoded_water), ("SEA_EQUIV", encoded_sea)):
        if encoded.is_empty:
            continue
        layers.append(encoded.layer(name))
        seeds[name] = encoded.seeds
    return WaterResult(
        layers=tuple(layers),
        seeds=seeds,
        water=encoded_water.geometry,
        sea_equiv=encoded_sea.geometry,
        lakes=tuple(lakes),
        counts={
            "elements": len(element_area),
            "polygons": len(polygons),
            "merged": len(merged_water) + len(merged_sea),
            "water": len(encoded_water.geometry.geoms),
            "sea_equiv": len(encoded_sea.geometry.geoms),
            "dropped": encoded_water.dropped + encoded_sea.dropped,
        },
    )


def _report(on_event: EventHandler | None, code: str, context: dict[str, object]) -> None:
    if on_event is not None:
        on_event(OsxpError(code, context=context))
