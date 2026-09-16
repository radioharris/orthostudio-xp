"""Airport discovery, surface attachment, size filter, boundaries and neighbourhood raster.

Specification: ``docs/specs/airports-discovery.md``. Successor of five functions of Ortho4XP
Ortho4XP ``src/O4_Airport_Utils.py``:

* ``discover_airport_names`` (``:19-177``) -> :func:`discover_names`;
* ``attach_surfaces_to_airports`` (``:177-329``) -> :func:`attach_surfaces`;
* ``discard_unwanted_airports`` (``:682-700``) -> :func:`discard_unwanted`;
* ``update_airport_boundaries`` (``:805-838``) -> :func:`update_boundaries`;
* ``list_airports_and_runways`` (``:847-910``) -> :func:`listing`;
* ``build_airport_array`` (``:910-924``) -> :func:`airport_array`.

The one change of substance is the attachment: Ortho4XP answers "which aerodrome owns this
surface?" with a Python double loop of ``linestring.intersects(boundary)``; OrthoStudio XP asks a
``shapely.STRtree`` for the candidates and keeps the smallest index, which *is* Ortho4XP's "first
airport in insertion order" (spec section 5). Everything else — the key precedence, the
representative points, the 3500 m fallback, the orphan airports, the size thresholds, the
union order of the final boundary, the inclusive slice of the raster — is reproduced as it is.

Nothing here reads ``apt.dat`` (arbitration B5), writes a file, or touches the network.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely import affinity, geometry, ops
from shapely.geometry.base import BaseGeometry

from orthostudio.airports_vec.model import (
    CATEGORIES,
    M_TO_LAT,
    MAX_SMOOTHING_PIX,
    Airport,
    AirportKey,
    AirportSet,
    Category,
    KeyType,
    great_circle_m,
    has_boundary,
    m_to_lon,
)
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.geom import ensure_multipolygon
from orthostudio.vectors.osmdata import OsmData

__all__ = [
    "AERODROME_VALUES",
    "AirportRow",
    "DiscoverParams",
    "EventHandler",
    "airport_array",
    "attach_surfaces",
    "discard_unwanted",
    "discover",
    "discover_names",
    "listing",
    "live_boundary",
    "local_bounds",
    "update_boundaries",
]

log = logging.getLogger("orthostudio.airports_vec.discover")

EventHandler = Callable[[str, dict[str, Any]], None]
"""``on_event(code, context)``: what Ortho4XP prints at level 1 or 2, as a registry code."""

AERODROME_VALUES = ("aerodrome", "airstrip")
"""``O4_Airport_Utils.py:23-26``: an element is an aerodrome by one of its tag **values**."""

_NAME_TAGS = ("name:en", "name:alt", "name")
"""``:43-58``, in precedence order."""

_UNESCAPE = (("&quot;", '"'), ("&apos;", "'"))
"""``:45-47``: the only two XML entities Ortho4XP undoes in an airport name."""

_KEY_TAGS: tuple[tuple[str, KeyType, int | None], ...] = (
    ("icao", "icao", 4),
    ("iata", "iata", 3),
    ("local_ref", "local_ref", None),
)
"""``:29-42``: tag, resulting key type, truncation."""

_NO_NAME = "****"
_NO_CLOSEST = 99999.0
"""``:210``: Ortho4XP's initial "closest distance", below which a candidate must fall."""


@dataclass(frozen=True, slots=True)
class DiscoverParams:
    """The constants of the Ortho4XP airport chain, named so a tile can move them later."""

    attach_radius_m: float = 3500.0
    """``:224``: a loose surface joins the nearest aerodrome within this distance."""
    min_boundary_area_m2: float = 5000.0
    """``:687``: an aerodrome whose OSM outline is smaller than this is dropped."""
    min_runway_area_m2: float = 2500.0
    """``:693``: an aerodrome without an outline needs at least this much runway."""
    max_name_length: int = 60
    """``:59-60``: a longer name becomes ``name[:57] + "..."``."""
    array_size: int = 1001
    """``:911``: the side of the airport neighbourhood raster."""
    array_pad_m: float = 1500.0
    """``:914-915``: how far around an airport that raster reaches."""


DEFAULTS = DiscoverParams()


@dataclass(frozen=True, slots=True)
class AirportRow:
    """One line of ``list_airports_and_runways`` (``:847-910``), as data rather than print."""

    key: AirportKey
    code: str
    """The key for an ICAO / IATA / ``local_ref`` airport, ``"****"`` for the others."""
    name: str
    runways: int
    lon: float
    lat: float


# -- the whole chantier, in one call ---------------------------------------------------------


def discover(
    store: OsmData,
    tile: TileRef,
    params: DiscoverParams = DEFAULTS,
    *,
    on_event: EventHandler | None = None,
) -> AirportSet:
    """Discovery then attachment, the first two calls of ``include_airports`` (``:197-198``).

    ``store`` is the ``airports`` layer read by :mod:`orthostudio.vectors.osmdata`; ``tile`` is
    carried for the callers that go on to :func:`discard_unwanted` and is not used here — the
    discovery works in absolute degrees (spec R7).
    """
    airports = discover_names(store, params, on_event=on_event)
    attach_surfaces(airports, store, params, on_event=on_event)
    _ = tile
    return airports


# -- R1 to R7: discovery ---------------------------------------------------------------------


def discover_names(
    store: OsmData,
    params: DiscoverParams = DEFAULTS,
    *,
    on_event: EventHandler | None = None,
) -> AirportSet:
    """The aerodromes of the layer, in Ortho4XP's order (``discover_airport_names:19-177``).

    Relations, then ways, then nodes; inside each, the order the *tag* dictionary was filled,
    which is the order the OSM file spells the elements. That order fixes everything the
    attachment does afterwards (spec section 1).
    """
    airports = AirportSet(store=store)
    for kind in ("r", "w", "n"):
        for osm_id, tags in list(store.tags[kind].items()):
            if not _is_aerodrome(tags):
                continue
            _discover_one(airports, store, kind, osm_id, tags, params, on_event)
    return airports


def _is_aerodrome(tags: dict[str, str]) -> bool:
    """``:23-26``: one of the tag **values** is ``aerodrome`` or ``airstrip``."""
    values = tags.values()
    return any(value in values for value in AERODROME_VALUES)


def _discover_one(
    airports: AirportSet,
    store: OsmData,
    kind: str,
    osm_id: int,
    tags: dict[str, str],
    params: DiscoverParams,
    on_event: EventHandler | None,
) -> None:
    """One aerodrome element: key (R2, R5), name (R3), point (R4), boundary (R6, R7)."""
    identity: tuple[AirportKey, KeyType] | None = None
    for tag, candidate_type, cut in _KEY_TAGS:
        if tag not in tags:
            continue
        code = tags[tag][:cut] if cut is not None else tags[tag]
        if code in airports:
            return  # :32-33, :37-38, :42-43 -- the element is skipped whole
        identity = (code, candidate_type)
        break
    name = _airport_name(tags, params)
    try:
        repr_node = _repr_node(store, kind, osm_id)
    except (KeyError, IndexError, ValueError) as exc:
        # Ortho4XP lets this one through and dies inside numpy (difference 2).
        _event(
            on_event, "OSM_AIRPORT_TAG_INVALID", {"point": f"{kind}{osm_id}", "reason": str(exc)}
        )
        return
    if identity is None:
        identity = _fallback_identity(airports, name, repr_node)
        if identity is None:
            return
    key, key_type = identity
    airport = airports.add(
        Airport(
            key=key,
            key_type=key_type,
            name=name,
            repr_node=repr_node,
            smoothing_pix=_smoothing_pix(tags, on_event, str(key)),
            elevation=_elevation(tags),
        )
    )
    try:
        airport.boundary = _boundary(store, kind, osm_id)
    except (KeyError, IndexError, ValueError, shapely.errors.ShapelyError) as exc:
        # :169-177: a presumably erroneous aerodrome tag; the airport is dropped whole.
        _event(
            on_event,
            "OSM_AIRPORT_TAG_INVALID",
            {"point": f"{repr_node[0]:.6f},{repr_node[1]:.6f}", "reason": str(exc)},
        )
        airports.pop(key)
        return
    boundary = live_boundary(airport)
    if boundary is not None and not boundary.is_valid:
        _event(on_event, "OSM_AIRPORT_BOUNDARY_INVALID", {"airport": str(key)})
        airport.boundary = None


def _fallback_identity(
    airports: AirportSet, name: str, repr_node: tuple[float, float]
) -> tuple[AirportKey, KeyType] | None:
    """``:89-99``: with no code tag, the key is the name, or the point when there is no name.

    ``None`` means "skip this element". Ortho4XP tests the *name* against the record first, even
    when the name is the placeholder ``"****"``, and only then falls back to the mean point,
    which is tested in turn: so a second unnamed aerodrome whose nodes average to the very
    same point is dropped, and so is a second aerodrome of the same name. **Keep**, the order
    of the two tests included.
    """
    if name in airports:
        return None
    if name != _NO_NAME:
        return (name, "name")
    if repr_node in airports:
        return None
    return (repr_node, "repr_node")


def _airport_name(tags: dict[str, str], params: DiscoverParams) -> str:
    """``:43-63``: ``name:en`` then ``name:alt`` then ``name``, two entities undone, cut at 60."""
    name = _NO_NAME
    for tag in _NAME_TAGS:
        if tag in tags:
            name = tags[tag]
            for entity, char in _UNESCAPE:
                name = name.replace(entity, char)
            break
    if len(name) >= params.max_name_length:
        name = name[: params.max_name_length - 3] + "..."
    return name


def _repr_node(store: OsmData, kind: str, osm_id: int) -> tuple[float, float]:
    """``:64-88``: the node itself, or the mean of the way's / first outer ring's nodes.

    Absolute degrees, unrounded, ``numpy.mean`` over the ``(n, 2)`` array exactly as Ortho4XP sums
    it — the closing node of a closed way is counted twice, and the value reaches both a
    ``< 3500`` comparison and, for an unnamed airport, a dictionary key.
    """
    if kind == "n":
        return store.nodes[osm_id]
    node_ids = store.ways[osm_id] if kind == "w" else store.relations[osm_id]["outer"][0]
    mean = np.mean(np.array([store.nodes[i] for i in node_ids]), axis=0)
    return (float(mean[0]), float(mean[1]))


def _smoothing_pix(
    tags: dict[str, str], on_event: EventHandler | None = None, airport: str = "?"
) -> int | None:
    """``:105-113``: an integer tag that overrides the tile's ``apt_smoothing_pix``.

    Difference 7 of ``airports-geometry.md`` (review 6): a value outside
    ``0..MAX_SMOOTHING_PIX`` is ignored, as an unparsable one is, and reported with
    ``OSM_AIRPORT_SMOOTHING_INVALID``. Ortho4XP takes it as is: a negative width crashes the
    convolution, a huge one blocks the build for minutes on a single OSM tag.
    """
    try:
        value = int(tags["smoothing_pix"])
    except (KeyError, ValueError):
        return None
    if not 0 <= value <= MAX_SMOOTHING_PIX:
        _event(
            on_event,
            "OSM_AIRPORT_SMOOTHING_INVALID",
            {"airport": airport, "value": value, "range": f"0..{MAX_SMOOTHING_PIX}"},
        )
        return None
    return value


def _elevation(tags: dict[str, str]) -> float | None:
    """The OSM ``ele`` tag; additive, consumed by nothing in the port (spec difference 5)."""
    try:
        return float(tags["ele"])
    except (KeyError, ValueError):
        return None


def _boundary(store: OsmData, kind: str, osm_id: int) -> BaseGeometry | None:
    """``:114-159``: a way is one polygon, a relation the union of its outer rings, a node none.

    Absolute degrees, unrounded: that is what the attachment intersects and what the size
    filter measures (spec R7).
    """
    if kind == "w":
        return geometry.Polygon(np.array([store.nodes[i] for i in store.ways[osm_id]]))
    if kind == "r":
        return ops.unary_union(
            [
                geometry.Polygon(np.array([store.nodes[i] for i in ring]))
                for ring in store.relations[osm_id]["outer"]
            ]
        )
    return None


# -- R8 to R12: attachment -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Surface:
    """One ``aeroway`` element to attach, in Ortho4XP's own order (spec R8)."""

    category: Category
    element_id: int
    coords: NDArray[np.float64]
    tags: dict[str, str]
    as_relation: bool


def attach_surfaces(
    airports: AirportSet,
    store: OsmData,
    params: DiscoverParams = DEFAULTS,
    *,
    on_event: EventHandler | None = None,
) -> AirportSet:
    """Give every ``aeroway`` surface an airport (``attach_surfaces_to_airports:177-329``).

    The four categories in Ortho4XP's order, then the runway relations; inside each, the ways in
    the order the store read them. The record grows as orphans are met (R12) and the
    nearest-airport fallback sees those new entries, so the second half of the pass stays
    strictly sequential.

    The **first** half does not: which aerodrome boundary a surface meets depends on the
    surface and on the boundaries, never on the record's growth (the airports R12 creates have
    no boundary). So every surface is tested against the whole index in one
    ``STRtree.query`` call, and the sequential loop below only walks the answers (spec
    section 5).
    """
    surfaces = _surfaces(store, on_event)
    owners = airports.with_boundary()
    found = _owners_of(owners, surfaces)
    for i, surface in enumerate(surfaces):
        owner = owners[found[i]] if found[i] >= 0 else None
        if owner is None:
            point = np.mean(surface.coords, axis=0)
            pt_check = (float(point[0]), float(point[1]))
            owner = _nearest(airports, pt_check, params.attach_radius_m)
            if owner is None:
                owner = _orphan(airports, surface.tags, pt_check)
        if surface.as_relation:
            owner.runway_rels.append(surface.element_id)
        else:
            owner.ways[surface.category].append(surface.element_id)
    return airports


def _surfaces(store: OsmData, on_event: EventHandler | None) -> list[_Surface]:
    """Every surface to attach, in Ortho4XP's order: the four categories, then the runway rels."""
    out: list[_Surface] = []
    for category in CATEGORIES:
        for way_id in _ways_tagged(store, category):
            coords = np.array([store.nodes[i] for i in store.ways[way_id]], dtype=np.float64)
            if len(coords) < 2:
                # Ortho4XP dies inside ``LineString`` here (difference 2).
                _event(
                    on_event,
                    "OSM_AIRPORT_SURFACE_INVALID",
                    {"surface": category, "airport": "?", "osm_id": way_id},
                )
                continue
            out.append(_Surface(category, way_id, coords, store.tags["w"][way_id], False))
    for rel_id in _relations_tagged(store, "runway"):
        ring = store.relations[rel_id]["outer"][0]
        coords = np.array([store.nodes[i] for i in ring], dtype=np.float64)
        if len(coords) < 2:
            _event(
                on_event,
                "OSM_AIRPORT_SURFACE_INVALID",
                {"surface": "runway", "airport": "?", "osm_id": rel_id},
            )
            continue
        out.append(_Surface("runway", rel_id, coords, store.tags["r"][rel_id], True))
    return out


def _owners_of(owners: Sequence[Airport], surfaces: Sequence[_Surface]) -> NDArray[np.intp]:
    """``:194-201`` for every surface at once: the index of the first boundary it meets, or -1.

    The tree is queried with the very GEOS predicate Ortho4XP calls, in bulk; the smallest tree
    index reported for a surface is the earliest airport in insertion order, because
    ``owners`` is in that order. That is exactly what Ortho4XP's ``break`` selects.
    """
    none = np.full(len(surfaces), -1, dtype=np.intp)
    if not owners or not surfaces:
        return none
    lengths = np.fromiter((len(s.coords) for s in surfaces), dtype=np.intp, count=len(surfaces))
    indices = np.repeat(np.arange(len(surfaces), dtype=np.intp), lengths)
    lines = shapely.linestrings(np.concatenate([s.coords for s in surfaces]), indices=indices)
    tree = shapely.STRtree([a.boundary for a in owners])
    hits = tree.query(lines, predicate="intersects")
    if not hits.size:
        return none
    best = np.full(len(surfaces), len(owners), dtype=np.intp)
    np.minimum.at(best, hits[0], hits[1])
    return np.where(best < len(owners), best, none)


def _ways_tagged(store: OsmData, category: Category) -> list[int]:
    """``:180-186``: every way of the store whose kept ``aeroway`` tag is this category."""
    tagged = store.tags["w"]
    return [
        way_id
        for way_id in store.ways
        if way_id in tagged and tagged[way_id].get("aeroway") == category
    ]


def _relations_tagged(store: OsmData, category: str) -> list[int]:
    """``:253-259``: the relations carrying ``aeroway=runway`` (``runway_as_rel``)."""
    tagged = store.tags["r"]
    return [
        rel_id
        for rel_id in store.relations
        if rel_id in tagged and tagged[rel_id].get("aeroway") == category
    ]


def _nearest(airports: AirportSet, point: tuple[float, float], radius_m: float) -> Airport | None:
    """``:202-225``: the first strict minimum of ``GEO.dist`` over the whole record.

    Scalar on purpose (spec section 5): the record grows while the categories are walked, and
    an ulp on this distance moves a runway from one aerodrome to another.
    """
    closest: Airport | None = None
    closest_dist = _NO_CLOSEST
    for airport in airports:
        dist = great_circle_m(point, airport.repr_node)
        if dist < closest_dist:
            closest_dist = dist
            closest = airport
    if closest is not None and closest_dist < radius_m:
        return closest
    return None


def _orphan(airports: AirportSet, tags: dict[str, str], point: tuple[float, float]) -> Airport:
    """``:226-252``: a surface far from every aerodrome makes its own, keyed by name or point.

    Ortho4XP assigns into the record unconditionally, so a surface named like an existing airport
    **replaces** it, emptying its surfaces. Reproduced: it is observable on ``+43+005``.
    """
    name = tags.get("name")
    if name is not None:
        return airports.add(
            Airport(key=name, key_type="name", name=name, repr_node=point, orphan=True)
        )
    return airports.add(
        Airport(key=point, key_type="repr_node", name=_NO_NAME, repr_node=point, orphan=True)
    )


# -- R13 to R16: filter, boundaries, raster, listing ------------------------------------------


def discard_unwanted(
    airports: AirportSet,
    tile: TileRef,
    params: DiscoverParams = DEFAULTS,
    *,
    on_event: EventHandler | None = None,
) -> AirportSet:
    """Drop the aerodromes that are too small (``discard_unwanted_airports:682-700``).

    An airport with an OSM outline is judged on that outline and on nothing else; one without
    is judged on its reconstructed runway area, which the runway module of this wave must have
    filled (spec R13).
    """
    scale = M_TO_LAT * m_to_lon(tile.lat)
    min_boundary = params.min_boundary_area_m2 * scale
    min_runway = params.min_runway_area_m2 * scale
    for airport in list(airports):
        boundary = live_boundary(airport)
        if boundary is not None:
            if boundary.area < min_boundary:
                _drop(airports, airport, "boundary", on_event)
            continue
        runway = _required_area(airport, "runway")
        if runway.area < min_runway:
            _drop(airports, airport, "runway", on_event)
    return airports


def _drop(
    airports: AirportSet, airport: Airport, reason: str, on_event: EventHandler | None
) -> None:
    _event(on_event, "OSM_AIRPORT_TOO_SMALL", {"airport": str(airport.key), "reason": reason})
    airports.pop(airport.key)


def _required_area(airport: Airport, field_name: str) -> BaseGeometry:
    """One of the four built areas, or a coded error saying which module has not run.

    Ortho4XP raises ``AttributeError`` here, and treating the area as empty would silently discard
    airports or shrink boundaries -- the class of failure ``errors.md`` exists to stop
    (spec difference 3).
    """
    area = getattr(airport.areas, field_name)
    if area is None:
        raise OsxpError(
            "OSM_AIRPORT_INFO_UNAVAILABLE",
            context={"tile": "?", "airport": str(airport.key), "missing": field_name},
            message=f"The {field_name} area of airport {airport.key!r} has not been built.",
            remedy="Run the runway reconstruction and the hangar / apron / taxiway area "
            "builders before the size filter and the boundary update.",
        )
    return area  # type: ignore[no-any-return]


def update_boundaries(airports: AirportSet, tile: TileRef) -> AirportSet:
    """Replace every boundary by the tile-local union of the airport's surfaces (``:805-838``).

    Union order: taxiway, apron, hangar, runway, then the translated OSM outline when there
    was one; then ``buffer(0).simplify(0.00001)`` and ``ensure_MultiPolygon``. After this call
    every boundary is a tile-local ``MultiPolygon``, which is what the smoothing, the
    neighbourhood raster and the DSF cover read.
    """
    for airport in airports:
        built = ops.unary_union(
            [
                _required_area(airport, "taxiway"),
                _required_area(airport, "apron"),
                _required_area(airport, "hangar"),
                _required_area(airport, "runway"),
            ]
        )
        boundary = live_boundary(airport)
        if boundary is not None:
            local = affinity.translate(boundary, -tile.lon, -tile.lat)
            built = ops.unary_union([local, built])
        airport.boundary = ensure_multipolygon(built.buffer(0).simplify(0.00001))
    return airports


def airport_array(
    airports: AirportSet,
    tile: TileRef,
    params: DiscoverParams = DEFAULTS,
    *,
    on_event: EventHandler | None = None,
) -> NDArray[np.bool_]:
    """The airport neighbourhood raster (``build_airport_array:910-924``).

    ``array_size x array_size`` booleans over the tile, rows counted from the north, each airport's
    bounding box grown by ``array_pad_m`` and written with Ortho4XP's **inclusive** slice.
    ``orthostudio.vectors.roads.AirportAreas.contains`` reads it back with Ortho4XP's indexing.
    """
    last = params.array_size - 1
    array = np.zeros((params.array_size, params.array_size), dtype=bool)
    x_shift = params.array_pad_m * m_to_lon(tile.lat)
    y_shift = params.array_pad_m * M_TO_LAT
    for airport in airports:
        boundary = live_boundary(airport)
        if boundary is None:
            # Ortho4XP reaches ``round(nan)`` and dies; an airport with no surface at all has no
            # neighbourhood either (difference 2).
            _event(on_event, "OSM_AIRPORT_BOUNDARY_INVALID", {"airport": str(airport.key)})
            continue
        (xmin, ymin, xmax, ymax) = boundary.bounds
        colmin = max(round((xmin - x_shift) * last), 0)
        colmax = min(round((xmax + x_shift) * last), last)
        rowmax = min(round(((1 - ymin) + y_shift) * last), last)
        rowmin = max(round(((1 - ymax) - y_shift) * last), 0)
        array[rowmin : rowmax + 1, colmin : colmax + 1] = True
    return array


def local_bounds(airports: AirportSet) -> NDArray[np.float64]:
    """``(A, 4)`` tile-local ``(x_min, y_min, x_max, y_max)``, one row per airport.

    What ``VectorLayers.airport_bounds`` carries and what ``airports.json`` spells
    (``docs/specs/vectors-assembly.md`` section 7), for the DSF stage to cover with
    high-resolution imagery (arbitration B4). Call it **after** :func:`update_boundaries`:
    before that, the boundary is in absolute degrees.
    """
    rows = [
        boundary.bounds
        for boundary in (live_boundary(airport) for airport in airports)
        if boundary is not None
    ]
    return np.array(rows, dtype=np.float64).reshape(-1, 4)


_LISTING_ORDER: tuple[KeyType, ...] = ("icao", "iata", "local_ref", "name", "repr_node")
"""``:849-874``: the five groups of the airport listing, in Ortho4XP's order."""


def listing(airports: AirportSet) -> list[AirportRow]:
    """The rows of ``list_airports_and_runways`` (``:847-910``), sorted but not formatted."""
    rows: list[AirportRow] = []
    for key_type in _LISTING_ORDER:
        group = sorted(a.key for a in airports if a.key_type == key_type)  # type: ignore[type-var]
        for key in group:
            airport = airports[key]
            rows.append(
                AirportRow(
                    key=airport.key,
                    code=airport.code or _NO_NAME,
                    name=airport.name,
                    runways=airport.areas.runway_count(),
                    lon=airport.repr_node[0],
                    lat=airport.repr_node[1],
                )
            )
    return rows


def live_boundary(airport: Airport) -> BaseGeometry | None:
    """The airport's boundary when Ortho4XP's ``if apt["boundary"]`` is true, else ``None``.

    ``:195``, ``:685`` and ``:817`` all test the boundary for truth, which in shapely means
    "not ``None`` and not empty". Returning the geometry instead of a boolean is what lets the
    call sites use it without an ``assert`` that ``python -O`` would delete (review 5).
    """
    return airport.boundary if has_boundary(airport) else None


def _event(on_event: EventHandler | None, code: str, context: dict[str, Any]) -> None:
    if on_event is not None:
        on_event(code, context)
