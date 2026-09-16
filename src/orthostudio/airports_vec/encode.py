"""Airports in the planar graph: runways, taxiways, aprons and hangars.

Origin: Ortho4XP ``src/O4_Airport_Utils.py`` -- ``encode_runways_taxiways_and_aprons``
(``:1038-1344``) and ``encode_hangars`` (``:1344-1370``) -- called from
``O4_Vector_Map.include_airports`` (``:180-222``). The helipads live next door
in :mod:`orthostudio.airports_vec.helipads`.

This module **encodes** airport geometry that someone else built: it takes the airports as a
sequence of objects shaped like Ortho4XP's ``dico_airports`` entries (:class:`AirportLike`), the
airport OSM layer for the ways whose ids those objects carry, and the elevation raster
**after** it was smoothed over the airports, and it returns the passes, the seeds and the
footprints the rest of the stage needs. It writes no file and never touches the noder: the
assembler orders the passes (``docs/specs/vectors-assembly.md``).

The hot spot of Ortho4XP is here: ``weighted_alt`` (``O4_Vector_Utils.py:1250-1280``) gives every
vertex of a runway, a traverse, a taxiway or an apron its altitude, one Python call and one
rtree query at a time -- 14 091 of them on the reference tile. :class:`AltitudeField` does
the same arithmetic in batches, 67 times faster (section 4 of the spec).

Spec: ``docs/specs/airports-encoding.md``.
"""

from __future__ import annotations

import threading
from collections.abc import Collection, Hashable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely import STRtree, affinity, geometry, ops
from shapely.geometry.base import BaseGeometry

from orthostudio.airports_vec.helipads import HELIPAD_RADIUS_M, HelipadResult, encode_helipads
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.geom import cut_to_tile, ensure_multipolygon
from orthostudio.vectors.noding import MARKERS, Layer
from orthostudio.vectors.osmdata import COORD_DIGITS, OsmData
from orthostudio.vectors.patches import Runs
from orthostudio.vectors.roads import AirportAreas, improved_buffer, shift_way
from orthostudio.vectors.seeds import polygon_seeds
from orthostudio.vectors.water import (
    LAT_TO_M,
    Altitudes,
    EventHandler,
    refine_way,
    scale_x,
)

__all__ = [
    "DEFAULT_ENCODE_PARAMS",
    "AirportEncodeParams",
    "AirportLayers",
    "AirportLike",
    "AirportView",
    "AltitudeField",
    "RunwayLike",
    "RunwaySetLike",
    "SurfaceSetLike",
    "airport_bounds",
    "altitude_field",
    "encode_airports",
]

# -- Ortho4XP's constants
# ---------------------------------------------------------------------------

RUNWAY_CHUNKS = 100
"""Longitudinal chunks a runway is cut into (``O4_Airport_Utils.py:12``)."""

CHUNK_MIN_SIZE = 10.0
"""...as long as a chunk stays at least this long, in metres (``:14``)."""

ALT_FIT_DEGREE = 7
"""Degree of the least-squares altitude fit along a way (``O4_Vector_Utils.py:1188``)."""

ALT_FIT_SAMPLE_M = 7.0
"""One fit sample per 7 m of way, floor of ``length // 7`` (``:1064``, ``:1085``)."""

TAXIWAY_ALT_WIDTH_M = 15.0
"""Nominal width of a taxiway in the altitude weighting (``:1087``)."""

TAXIWAY_REFINE_M = 20.0
"""Maximum segment length of an encoded taxiway outline (``:1221``, ``:1227``)."""

APRON_REFINE_M = 15.0
"""Maximum segment length of an encoded apron outline (``:1266``)."""

RUNWAY_CLEARANCE_M = 5.0
"""The runway area is grown by this before being cut out of the taxiways (``:1244``)."""

HANGAR_CLEARANCE_M = 20.0
"""...and the hangars by this (``:1245``)."""

TAXIWAY_BUFFER_M = (3.0, 2.0, 0.5)
"""``improved_buffer(..., 3, 2, 0.5)`` of the cleaned taxiway area (``:1240-1249``)."""

TAXIWAY_MIN_AREA = 1e-9
"""Below this (square degrees) a taxiway polygon is not encoded (``:1218``)."""

HANGAR_FLATNESS_M = 1.5
"""A hangar whose raster span exceeds this is dropped, outline and seed (``:1357``)."""

APRON_INCLUDE_TAG = "include"
"""Only an apron a user tagged by hand in their local copy is encoded (``:1237-1241``)."""

WEIGHTED_ALT_EPS1 = 0.003
"""Half-side of the box that selects the fits around a node (``O4_Vector_Utils.py:1251``)."""

WEIGHTED_ALT_EPS2 = 0.0003
"""Within this of a tile side, the field is blended with the raw raster (``:1252``)."""

WEIGHTED_ALT_MIN = 1e-6
"""Below this total weight, the node keeps the raw raster altitude (``:1272``)."""

# -- what this module reads of an airport -------------------------------------------------------


@runtime_checkable
class RunwayLike(Protocol):
    """One reconstructed runway: ``(polygon, start, end, width)`` (``:640-667``)."""

    @property
    def polygon(self) -> geometry.Polygon:
        """Its surface, in tile-local degrees."""

    @property
    def start(self) -> NDArray[np.float64]:
        """``(2,)``: one end of the centre line."""

    @property
    def end(self) -> NDArray[np.float64]:
        """``(2,)``: the other end."""

    @property
    def width(self) -> float:
        """Width in **metres**; it is also the weighting width of the altitude field."""


@runtime_checkable
class AirportLike(Protocol):
    """One entry of Ortho4XP's ``dico_airports``, with its tuples given names (spec section 3).

    Only these members are read here. Building them -- name discovery, runway reconstruction,
    hangar / apron / taxiway areas, boundary -- belongs to the airport data chantier; the
    order of the sequence handed to :func:`encode_airports` is Ortho4XP's dict order and decides
    the order of the passes, of the seeds and of ``airports.json``.
    """

    @property
    def key(self) -> Hashable:
        """ICAO, IATA or ``local_ref``, and the representative point when an airport has
        none (``discover_airport_names``, ``:106-140``): hashable, not necessarily a string.
        It is only ever compared with ``patch_names``."""

    @property
    def runways(self) -> Sequence[RunwayLike]:
        """``apt["runway"][1] + apt["runway"][2]``: **areas first, then lines** (``:1060``)."""

    @property
    def runway_area(self) -> BaseGeometry:
        """``apt["runway"][0]``: the union of the runway polygons."""

    @property
    def taxiway_area(self) -> BaseGeometry:
        """``apt["taxiway"][0]``, before the cleaning of ``:1240-1249``."""

    @property
    def taxiway_ways(self) -> Sequence[int]:
        """``apt["taxiway"][1]``: the OSM ids of the taxiway centre lines."""

    @property
    def apron_area(self) -> BaseGeometry:
        """``apt["apron"][0]``."""

    @property
    def apron_ways(self) -> Sequence[int]:
        """``apt["apron"][1]``: the OSM ids of the apron outlines."""

    @property
    def hangars(self) -> BaseGeometry:
        """``apt["hangar"]``."""

    @property
    def boundary(self) -> BaseGeometry:
        """``apt["boundary"]``; only its ``bounds`` is read here."""


@runtime_checkable
class RunwaySetLike(Protocol):
    """``orthostudio.airports_vec.runways.AirportRunways``, as :meth:`AirportView.of` reads it."""

    @property
    def area(self) -> BaseGeometry:
        """The union of the runway outlines (``apt["runway"][0]``)."""

    @property
    def runways(self) -> Sequence[RunwayLike]:
        """``as_area + as_line``, in that order (``:1058``)."""


@runtime_checkable
class SurfaceSetLike(Protocol):
    """``orthostudio.airports_vec.areas.AirportSurfaces``, as :meth:`AirportView.of` reads it."""

    @property
    def hangars(self) -> BaseGeometry: ...

    @property
    def aprons(self) -> BaseGeometry: ...

    @property
    def apron_ways(self) -> Sequence[int]: ...

    @property
    def taxiways(self) -> BaseGeometry: ...

    @property
    def taxiway_ways(self) -> Sequence[int]: ...

    @property
    def boundary(self) -> BaseGeometry: ...


@dataclass(frozen=True, slots=True)
class AirportView:
    """One airport as the encoder reads it: the concrete form of :class:`AirportLike`.

    The wave builds an airport in three places -- the record and its way ids
    (:mod:`orthostudio.airports_vec.model`), the reconstructed runways
    (:mod:`orthostudio.airports_vec.runways`) and the surface areas
    (:mod:`orthostudio.airports_vec.areas`) -- and this is where the three meet. :meth:`of`
    assembles one from the two records those modules return, so the pipeline's wiring stays a single
    expression and this module keeps depending on a protocol rather than on their layout.
    """

    key: Hashable
    runways: tuple[RunwayLike, ...] = ()
    runway_area: BaseGeometry = field(default_factory=geometry.Polygon)
    taxiway_area: BaseGeometry = field(default_factory=geometry.Polygon)
    taxiway_ways: tuple[int, ...] = ()
    apron_area: BaseGeometry = field(default_factory=geometry.Polygon)
    apron_ways: tuple[int, ...] = ()
    hangars: BaseGeometry = field(default_factory=geometry.Polygon)
    boundary: BaseGeometry = field(default_factory=geometry.Polygon)

    @classmethod
    def of(
        cls,
        key: Hashable,
        runways: RunwaySetLike | None = None,
        surfaces: SurfaceSetLike | None = None,
    ) -> AirportView:
        """Assemble the view from an ``AirportRunways`` and an ``AirportSurfaces``.

        Either may be missing -- an airport whose runways were never reconstructed, or one
        whose areas were not built -- and the missing half is then empty, which encodes to
        nothing instead of raising.
        """
        empty = geometry.Polygon()
        return cls(
            key=key,
            runways=tuple(runways.runways) if runways is not None else (),
            runway_area=runways.area if runways is not None else empty,
            taxiway_area=surfaces.taxiways if surfaces is not None else empty,
            taxiway_ways=tuple(surfaces.taxiway_ways) if surfaces is not None else (),
            apron_area=surfaces.aprons if surfaces is not None else empty,
            apron_ways=tuple(surfaces.apron_ways) if surfaces is not None else (),
            hangars=surfaces.hangars if surfaces is not None else empty,
            boundary=surfaces.boundary if surfaces is not None else empty,
        )


@dataclass(frozen=True, slots=True)
class AirportEncodeParams:
    """The numbers Ortho4XP hard-codes, gathered so a profile can move them.

    The defaults **are** Ortho4XP's, the values the encoding was measured with, so changing one
    is a wanted difference that must be measured, not a preference.
    """

    runway_chunks: int = RUNWAY_CHUNKS
    chunk_min_size: float = CHUNK_MIN_SIZE
    taxiway_refine_m: float = TAXIWAY_REFINE_M
    apron_refine_m: float = APRON_REFINE_M
    hangar_flatness_m: float = HANGAR_FLATNESS_M
    helipad_radius_m: float = HELIPAD_RADIUS_M


DEFAULT_ENCODE_PARAMS = AirportEncodeParams()
"""Ortho4XP's settings, as a module-level singleton (ruff B008)."""


@dataclass(frozen=True, slots=True)
class AirportLayers:
    """The airport contribution to the PSLG of one tile, plus the footprints others read."""

    layers: tuple[Layer, ...] = ()
    """``(geometry, marker, z)`` in insertion order, equal markers grouped into one pass."""
    seeds: dict[str, NDArray[np.float64]] = field(default_factory=dict)
    """``{"RUNWAY": (S, 2), "TAXIWAY": ..., "APRON": ..., "HANGAR": ..., "INTERP_ALT": ...}``."""
    area: BaseGeometry = field(default_factory=geometry.Polygon)
    """``treated_area`` (``O4_Vector_Map.py:217``): the airport surfaces **and** the patches."""
    surfaces: BaseGeometry = field(default_factory=geometry.Polygon)
    """The airport half of it alone: runways, cleaned taxiways and aprons (``:1336-1343``)."""
    array: NDArray[np.bool_] | None = None
    """``build_airport_array``: 1001x1001 booleans, ``True`` near an airport (``:910-921``).

    Built by the surface module and passed through :func:`encode_airports`; ``None`` means
    *no raster was given*, and a road is then levelled on its banking alone -- which is a
    different tile, not a lighter one (``orthostudio.vectors.roads.AirportAreas.contains``)."""
    bounds: NDArray[np.float64] = field(default_factory=lambda: np.zeros((0, 4), dtype=np.float64))
    """``(A, 4)`` bounding boxes, one row per airport in input order (``airports.json``)."""
    counts: dict[str, int] = field(default_factory=dict)
    """``airports``, ``patched``, ``runways``, ``traverses``, ``taxiways``, ``aprons``,
    ``hangars``, ``hangars_dropped``, ``helipads``, ``ways``, ``vertices``."""

    def airport_areas(self) -> AirportAreas:
        """The two inputs ``orthostudio.vectors.roads.build_road_layers`` takes (``:228-355``)."""
        return AirportAreas(array=self.array, area=self.area)


# -- the altitude field -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AltitudeField:
    """``weighted_alt`` over one airport, evaluated by batches (``:1250-1280``).

    Ortho4XP builds, per airport, one degree-7 least-squares fit of the raster along each runway
    centre line and each taxiway centre line, indexes them by bounding box, and gives a node
    the weighted mean of the fits whose box reaches it -- weight ``exp(-d / 2w)``, ``d`` the
    distance to the line in metres and ``w`` the nominal width of that surface. Nodes with no
    fit nearby, and nodes within ``eps2`` of a tile side, fall back to (or blend with) the
    raster.

    The only wanted difference is the order the weighted terms are summed in: Ortho4XP adds them
    in rtree order, here they are added by ``np.add.at`` in candidate-pair order, which moves
    a z by at most a few ulps (spec 4.2).
    """

    lines: NDArray[np.object_]
    """``(K,)`` centre lines, scaled by ``scalx`` in x, as Ortho4XP indexes them."""
    coefficients: NDArray[np.float64]
    """``(K, degree + 1)`` polynomial coefficients, highest degree first (``numpy.polyfit``)."""
    widths: NDArray[np.float64]
    """``(K,)`` nominal widths in metres."""
    scalx: float
    dem: Altitudes
    tree: STRtree | None = None

    @classmethod
    def build(
        cls,
        lines: Sequence[geometry.LineString],
        coefficients: Sequence[NDArray[np.float64]],
        widths: Sequence[float],
        scalx: float,
        dem: Altitudes,
    ) -> AltitudeField:
        array = np.empty(len(lines), dtype=object)
        array[:] = list(lines)
        fits = (
            np.asarray(coefficients, dtype=np.float64).reshape(len(lines), -1)
            if len(lines)
            else np.zeros((0, ALT_FIT_DEGREE + 1), dtype=np.float64)
        )
        return cls(
            lines=array,
            coefficients=fits,
            widths=np.asarray(widths, dtype=np.float64).reshape(-1),
            scalx=scalx,
            dem=dem,
            tree=STRtree(array) if len(array) else None,
        )

    def __call__(self, way: NDArray[np.floating]) -> NDArray[np.float64]:
        """The altitude of every ``(x, y)`` row of ``way``, in metres."""
        points = np.asarray(way, dtype=np.float64).reshape(-1, 2)
        raster = np.asarray(self.dem.alt_vec(points), dtype=np.float64)
        if self.tree is None or not len(points):
            return raster
        x = points[:, 0] * self.scalx
        y = points[:, 1]
        eps = WEIGHTED_ALT_EPS1
        node, line = self.tree.query(shapely.box(x - eps, y - eps, x + eps, y + eps))
        alti = np.zeros(len(points), dtype=np.float64)
        weights = np.zeros(len(points), dtype=np.float64)
        if len(node):
            probes = shapely.points(np.column_stack([x, y]))[node]
            targets = self.lines[line]
            distance = shapely.distance(probes, targets) * LAT_TO_M
            weight = np.exp(-distance / (2 * self.widths[line]))
            abscissa = shapely.line_locate_point(targets, probes, normalized=True)
            np.add.at(alti, node, _polyval_rows(self.coefficients[line], abscissa) * weight)
            np.add.at(weights, node, weight)
        fitted = np.divide(alti, weights, out=np.zeros_like(alti), where=weights > 0)
        out = np.where(weights < WEIGHTED_ALT_MIN, raster, fitted)
        # The border blend of :1274-1278, on the **scaled** abscissa, as Ortho4XP writes it.
        eps2 = WEIGHTED_ALT_EPS2
        near = (x < eps2) | (x > 1 - eps2) | (y < eps2) | (y > 1 - eps2)
        alpha = np.minimum(np.minimum(x, 1 - x), np.minimum(y, 1 - y)) / eps2
        blended = alpha * fitted + (1 - alpha) * raster
        return np.where(near & (weights >= WEIGHTED_ALT_MIN), blended, out)


def _polyval_rows(coefficients: NDArray[np.float64], t: NDArray[np.float64]) -> NDArray[np.float64]:
    """``numpy.polyval`` per row, with the very same Horner loop (``numpy/lib/polynomial``)."""
    out = np.zeros(len(t), dtype=np.float64)
    for column in range(coefficients.shape[1]):
        out = out * t + coefficients[:, column]
    return out


def _fit_altitude(
    way: NDArray[np.float64],
    steps: int,
    dem: Altitudes,
    scalx: float,
    *,
    weighted: bool,
) -> tuple[geometry.LineString, NDArray[np.float64]]:
    """``least_square_fit_altitude_along_way`` (``O4_Vector_Utils.py:1182-1212``).

    The samples are taken **along the scaled line**, then unscaled in x before the raster is
    read; the weights of the runway variant pull the fit towards the two ends, which is what
    keeps a runway from stepping at its thresholds (``:1205-1210``).
    """
    line = affinity.affine_transform(geometry.LineString(way), [scalx, 0, 0, 1, 0, 0])
    t = np.arange(steps + 1) / steps
    samples = shapely.get_coordinates(
        shapely.line_interpolate_point(line, t, normalized=True)
    ) * np.array([1 / scalx, 1])
    altitudes = np.asarray(dem.alt_vec(samples), dtype=np.float64)
    if not weighted:
        return line, np.polyfit(t, altitudes, ALT_FIT_DEGREE)
    k = np.arange(steps + 1)
    w = (np.maximum(k, steps - k) + steps // 2) ** 2
    return line, np.polyfit(t, altitudes, ALT_FIT_DEGREE, w=w)


def _steps(length_m: float, params: AirportEncodeParams) -> int:
    """``int(max(runway_chunks, length // 7))`` (``:1063``, ``:1084``)."""
    return int(max(params.runway_chunks, length_m // ALT_FIT_SAMPLE_M))


def _length_m(way: NDArray[np.float64], scalx: float) -> float:
    """``length_in_meters`` without building a shapely object (``:1002-1011``)."""
    d = np.asarray(way, dtype=np.float64)
    d = d[1:] - d[:-1]
    return float(np.sqrt(d[:, 0] ** 2 * scalx**2 + d[:, 1] ** 2).sum() * LAT_TO_M)


def _centre_line(data: OsmData, way_id: int, tile: TileRef) -> NDArray[np.float64]:
    """A taxiway centre line in local coordinates, **unrounded** (``:1077-1082``).

    The apron outlines two dozen lines below are rounded to 7 decimals (``:1262``); these are
    not, because they only ever feed the least-squares fit.
    """
    raw = np.array([data.nodes[i] for i in data.ways[way_id]], dtype=np.float64).reshape(-1, 2)
    return raw - np.array([[tile.lon, tile.lat]], dtype=np.float64)


def altitude_field(
    airport: AirportLike,
    tile: TileRef,
    dem: Altitudes,
    params: AirportEncodeParams = DEFAULT_ENCODE_PARAMS,
    *,
    osm: OsmData,
    scalx: float | None = None,
) -> AltitudeField:
    """Build the altitude field of one airport (``:1052-1088``): runways, then taxiways."""
    sx = scale_x(tile) if scalx is None else scalx
    lines: list[geometry.LineString] = []
    coefficients: list[NDArray[np.float64]] = []
    widths: list[float] = []
    for runway in airport.runways:
        centre = np.vstack((runway.start, runway.end))
        line, fit = _fit_altitude(
            centre, _steps(_length_m(centre, sx), params), dem, sx, weighted=True
        )
        lines.append(line)
        coefficients.append(fit)
        widths.append(float(runway.width))
    for way_id in airport.taxiway_ways:
        if way_id not in osm.ways:
            continue
        centre = _centre_line(osm, way_id, tile)
        if len(centre) < 2:
            continue
        line, fit = _fit_altitude(
            centre, _steps(_length_m(centre, sx), params), dem, sx, weighted=False
        )
        lines.append(line)
        coefficients.append(fit)
        widths.append(TAXIWAY_ALT_WIDTH_M)
    return AltitudeField.build(lines, coefficients, widths, sx, dem)


# -- the passes ---------------------------------------------------------------------------------


def _report(on_event: EventHandler | None, code: str, context: dict[str, object]) -> None:
    """What Ortho4XP swallows in a bare ``except``: reported, never raised (spec 5.2, 7.2)."""
    if on_event is not None:
        on_event(OsxpError(code, context=context))


def _split(z: NDArray[np.float64], sizes: Sequence[int]) -> list[NDArray[np.float64]]:
    """Give each way of a batch its slice of the altitudes."""
    edges = np.cumsum([0, *sizes])
    return [z[edges[i] : edges[i + 1]] for i in range(len(sizes))]


def _encode_runways(
    airport: AirportLike,
    runs: Runs,
    field_of: AltitudeField,
    scalx: float,
    params: AirportEncodeParams,
    on_event: EventHandler | None,
) -> tuple[list[geometry.Polygon], geometry.Polygon | None, int]:
    """Runway outlines and their traverses (``O4_Airport_Utils.py:1139-1203``).

    Returns the encoded polygons (the seeds of section 6.1 are computed from them), the last
    runway polygon *iterated* -- the leftover loop variable the apron branch reads at ``:1271``
    -- and how many traverses were inserted.
    """
    encoded: list[geometry.Polygon] = []
    last: geometry.Polygon | None = None
    traverses_total = 0
    for runway in airport.runways:
        last = runway.polygon
        centre = np.vstack((runway.start, runway.end))
        refine_size = max(_length_m(centre, scalx) // params.runway_chunks, params.chunk_min_size)
        way = refine_way(centre, refine_size, scalx)
        right = shift_way(way, runway.width, "right", scalx)
        left = shift_way(way, runway.width, "left", scalx)
        for polygon in ensure_multipolygon(cut_to_tile(runway.polygon)).geoms:
            boundary = polygon.exterior
            abscissae = [boundary.project(geometry.Point(c)) for c in boundary.coords]
            traverses: list[tuple[float, float]] = []
            skipped = 0
            for k in range(1, len(way)):
                if k >= len(right) or k >= len(left):
                    # Ortho4XP raises IndexError here and swallows it (:1157-1168): ``way`` was
                    # reassigned to the previous piece's outline, which is longer.
                    skipped += 1
                    continue
                chord = geometry.LineString([right[k], left[k]]).intersection(runway.polygon)
                if chord.geom_type != "LineString" or chord.is_empty:
                    continue
                coords = list(chord.coords)
                first = boundary.project(geometry.Point(coords[0]))
                last_abscissa = boundary.project(geometry.Point(coords[-1]))
                traverses.append((first, last_abscissa))
                abscissae += [first, last_abscissa]
            if skipped:
                _report(
                    on_event,
                    "OSM_AIRPORT_SURFACE_INVALID",
                    {"surface": "runway traverse", "airport": airport.key, "count": skipped},
                )
            outline = np.round(
                np.array(
                    [boundary.interpolate(a).coords[0] for a in [*sorted(set(abscissae)), 0.0]],
                    dtype=np.float64,
                ),
                COORD_DIGITS,
            )
            chords = [
                np.round(
                    np.array(
                        [
                            boundary.interpolate(first).coords[0],
                            boundary.interpolate(second).coords[0],
                        ],
                        dtype=np.float64,
                    ),
                    COORD_DIGITS,
                )
                for first, second in traverses
            ]
            batch = [outline, *chords]
            altitudes = _split(field_of(np.vstack(batch)), [len(part) for part in batch])
            runs.add(geometry.LineString(outline), MARKERS["RUNWAY"], altitudes[0])
            for chord_way, chord_z in zip(chords, altitudes[1:], strict=True):
                runs.add(geometry.LineString(chord_way), MARKERS["DUMMY"], chord_z)
            traverses_total += len(chords)
            encoded.append(polygon)
            way = outline  # the reassignment of :1173, kept because it is observable
    return encoded, last, traverses_total


def _runway_seeds(polygons: Sequence[geometry.Polygon]) -> list[NDArray[np.float64]]:
    """One seed per sub-region of the runway union, crossings included (``:1195-1203``).

    For each encoded polygon: first the parts it does not share with any other, then the parts
    it does. Two crossing runways therefore leave a seed in the crossing itself, which is a
    region of its own once Triangle4XP has cut the graph.
    """
    seeds: list[NDArray[np.float64]] = []
    for polygon in polygons:
        others = ops.unary_union([other for other in polygons if other != polygon])
        for part in (polygon.difference(others), polygon.intersection(others)):
            points, _ = polygon_seeds(ensure_multipolygon(part))
            seeds.extend(np.asarray(points, dtype=np.float64).reshape(-1, 2))
    return seeds


def _encode_taxiways(
    airport: AirportLike,
    runs: Runs,
    field_of: AltitudeField,
    scalx: float,
    params: AirportEncodeParams,
) -> tuple[BaseGeometry, list[NDArray[np.float64]], int]:
    """The cleaned taxiway area, its seeds and how many polygons were encoded (``:1205-1232``).

    The cleaned area is returned instead of being written back into the airport
    (Ortho4XP mutates ``apt["taxiway"]`` at ``:1215``): it is the one that goes into
    ``treated_area``.
    """
    cleaned = improved_buffer(
        airport.taxiway_area.difference(
            improved_buffer(airport.runway_area, RUNWAY_CLEARANCE_M, 0, 0, scalx).union(
                improved_buffer(airport.hangars, HANGAR_CLEARANCE_M, 0, 0, scalx)
            )
        ),
        *TAXIWAY_BUFFER_M,
        scalx,
    )
    seeds: list[NDArray[np.float64]] = []
    encoded = 0
    for polygon in ensure_multipolygon(cut_to_tile(cleaned)).geoms:
        if not polygon.is_valid or polygon.is_empty or polygon.area < TAXIWAY_MIN_AREA:
            continue
        rings = [np.array(polygon.exterior.coords, dtype=np.float64)]
        rings += [np.array(ring.coords, dtype=np.float64) for ring in polygon.interiors]
        ways = [
            np.round(refine_way(ring, params.taxiway_refine_m, scalx), COORD_DIGITS)
            for ring in rings
        ]
        altitudes = _split(field_of(np.vstack(ways)), [len(way) for way in ways])
        for way, z in zip(ways, altitudes, strict=True):
            runs.add(geometry.LineString(way), MARKERS["TAXIWAY"], z)
        points, _ = polygon_seeds(polygon)
        seeds.extend(np.asarray(points, dtype=np.float64).reshape(-1, 2))
        encoded += 1
    return cleaned, seeds, encoded


def _encode_aprons(
    airport: AirportLike,
    runs: Runs,
    field_of: AltitudeField,
    scalx: float,
    params: AirportEncodeParams,
    osm: OsmData,
    last_runway: geometry.Polygon | None,
    on_event: EventHandler | None,
) -> tuple[list[NDArray[np.float64]], int]:
    """The aprons a user tagged ``include`` by hand (``:1234-1277``).

    ``last_runway`` is Ortho4XP's leftover loop variable (``runway_pol`` at ``:1271``): the guard
    really does read the last runway polygon of the last airport that had one, and raises
    ``NameError`` -- caught, apron skipped -- when no airport has had one yet. Kept, reported.
    """
    seeds: list[NDArray[np.float64]] = []
    encoded = 0
    for way_id in airport.apron_ways:
        tags = osm.tags["w"].get(way_id, {})
        if APRON_INCLUDE_TAG not in tags or way_id not in osm.ways:
            continue
        way = np.round(
            refine_way(osm.node_coords(osm.ways[way_id]), params.apron_refine_m, scalx),
            COORD_DIGITS,
        )
        # shapely refuses a ring of fewer than three points, where Ortho4XP raises and catches
        polygon = geometry.Polygon(way) if len(way) >= 3 else geometry.Polygon()
        if polygon.is_empty or last_runway is None or not last_runway.is_valid:
            _report(
                on_event,
                "OSM_AIRPORT_SURFACE_INVALID",
                {"surface": "apron", "airport": airport.key, "way": way_id},
            )
            continue
        runs.add(geometry.LineString(way), MARKERS["APRON"], field_of(way))
        points, _ = polygon_seeds(polygon)
        seeds.extend(np.asarray(points, dtype=np.float64).reshape(-1, 2))
        encoded += 1
    return seeds, encoded


def _encode_hangars(
    airports: Sequence[AirportLike],
    patch_names: Collection[Hashable],
    runs: Runs,
    dem: Altitudes,
    params: AirportEncodeParams,
) -> tuple[list[NDArray[np.float64]], int, int]:
    """Flat hangar outlines (``encode_hangars``, ``:1344-1370``).

    A hangar whose raster span exceeds 1.5 m is dropped, outline **and** seed: Ortho4XP would
    rather leave the terrain alone than flatten a building on a slope.
    """
    seeds: list[NDArray[np.float64]] = []
    encoded = 0
    dropped = 0
    for airport in airports:
        if airport.key in patch_names:
            continue
        for polygon in ensure_multipolygon(cut_to_tile(airport.hangars)).geoms:
            way = np.array(polygon.exterior.coords, dtype=np.float64)
            altitudes = np.asarray(dem.alt_vec(way), dtype=np.float64)
            if altitudes.max() - altitudes.min() > params.hangar_flatness_m:
                dropped += 1
                continue
            z = np.full(len(way), float(np.mean(altitudes)), dtype=np.float64)
            runs.add(geometry.LineString(way), MARKERS["HANGAR"], z)
            points, _ = polygon_seeds(polygon)
            seeds.extend(np.asarray(points, dtype=np.float64).reshape(-1, 2))
            encoded += 1
    return seeds, encoded, dropped


# -- footprints ---------------------------------------------------------------------------------


def airport_bounds(airports: Sequence[AirportLike]) -> NDArray[np.float64]:
    """``(A, 4)`` bounding boxes of the airport boundaries, in input order.

    What ``airports.json`` carries and what the curvature weight map of the mesh stage reads
    (``O4_Mesh_Utils.py:140-160``, ``docs/specs/mesh-build.md`` 3.2). An airport whose
    boundary has no finite bounds is skipped rather than crashing the stage (**fix**: Ortho4XP
    raises ``ValueError`` on the unpacking).
    """
    rows = []
    for airport in airports:
        bounds = airport.boundary.bounds
        if len(bounds) == 4 and all(np.isfinite(bounds)):
            rows.append(list(bounds))
    return np.asarray(rows, dtype=np.float64).reshape(-1, 4)


# -- the stage ----------------------------------------------------------------------------------


def encode_airports(
    airports: Sequence[AirportLike],
    tile: TileRef,
    dem: Altitudes,
    params: AirportEncodeParams = DEFAULT_ENCODE_PARAMS,
    *,
    osm: OsmData,
    patch_names: Collection[Hashable] = (),
    patches_area: BaseGeometry | None = None,
    array: NDArray[np.bool_] | None = None,
    on_event: EventHandler | None = None,
    cancel: threading.Event | None = None,
) -> AirportLayers:
    """Encode every airport of the tile into PSLG passes, seeds and footprints.

    The order is Ortho4XP's (``O4_Vector_Map.py:215-222``): per airport, runway outlines and their
    traverses, then taxiways, then aprons; then the hangars of every airport; then the
    helipads, which need the finished ``treated_area``. ``patches_area`` is what the patch
    builder encoded (``orthostudio.vectors.patches.PatchResult.area``) and ``patch_names`` its
    ``names``: an airport a patch overrides is not encoded at all, but it still counts in the
    footprints, exactly as in Ortho4XP.

    ``dem`` must be the raster **after** ``smooth_raster_over_airports``; feeding the raw one
    gives a mesh that steps at every runway threshold. ``array`` is the 1001x1001
    neighbourhood raster of the roads: pass the one
    :func:`orthostudio.airports_vec.areas.airport_array` built beside the surfaces, or leave it out
    and it is built here from the same function. ``cancel`` is read before every airport
    and raises ``SYS_CANCELLED`` (review 6).
    """
    scalx = scale_x(tile)
    runs = Runs()
    seeds: dict[str, list[NDArray[np.float64]]] = {
        "RUNWAY": [],
        "TAXIWAY": [],
        "APRON": [],
        "HANGAR": [],
        "INTERP_ALT": [],
    }
    counts = dict.fromkeys(
        (
            "airports",
            "patched",
            "runways",
            "traverses",
            "taxiways",
            "aprons",
            "hangars",
            "hangars_dropped",
            "helipads",
        ),
        0,
    )
    surfaces: list[BaseGeometry] = []
    cleaned_taxiways: dict[int, BaseGeometry] = {}
    last_runway: geometry.Polygon | None = None

    for index, airport in enumerate(airports):
        if cancel is not None and cancel.is_set():
            raise OsxpError(
                "SYS_CANCELLED", context={"stage": "vectors airports", "tile": tile.name}
            )
        if airport.key in patch_names:
            counts["patched"] += 1
            continue
        counts["airports"] += 1
        counts["runways"] += len(airport.runways)
        field_of = altitude_field(airport, tile, dem, params, osm=osm, scalx=scalx)
        polygons, last, traverses = _encode_runways(
            airport, runs, field_of, scalx, params, on_event
        )
        last_runway = last if last is not None else last_runway
        counts["traverses"] += traverses
        seeds["RUNWAY"].extend(_runway_seeds(polygons))
        cleaned, taxi_seeds, taxiways = _encode_taxiways(airport, runs, field_of, scalx, params)
        cleaned_taxiways[index] = cleaned
        seeds["TAXIWAY"].extend(taxi_seeds)
        counts["taxiways"] += taxiways
        apron_seeds, aprons = _encode_aprons(
            airport, runs, field_of, scalx, params, osm, last_runway, on_event
        )
        seeds["APRON"].extend(apron_seeds)
        counts["aprons"] += aprons

    hangar_seeds, hangars, dropped = _encode_hangars(airports, patch_names, runs, dem, params)
    seeds["HANGAR"].extend(hangar_seeds)
    counts["hangars"] = hangars
    counts["hangars_dropped"] = dropped

    # ``:1336-1343``: the union is taken over **every** airport, patched ones included, and
    # over the *cleaned* taxiway area of those that were encoded (``:1215``).
    for index, airport in enumerate(airports):
        surfaces.append(airport.runway_area)
        surfaces.append(cleaned_taxiways.get(index, airport.taxiway_area))
        surfaces.append(airport.apron_area)
    surface_area = ops.unary_union(surfaces) if surfaces else geometry.Polygon()
    treated = ops.unary_union(
        [patches_area if patches_area is not None else geometry.Polygon(), surface_area]
    )

    helipads: HelipadResult = encode_helipads(
        osm, tile, dem, treated, radius_m=params.helipad_radius_m
    )
    for way, z in helipads.ways:
        runs.add(geometry.LineString(way), MARKERS["INTERP_ALT"], z)
    seeds["INTERP_ALT"].extend(np.asarray(helipads.seeds, dtype=np.float64).reshape(-1, 2))
    counts["helipads"] = helipads.found

    runs.close()
    layers = tuple(runs.layers)
    counts["ways"] = sum(len(shapely.get_parts(geom)) for geom, _, _ in layers)
    counts["vertices"] = sum(len(z) for _, _, z in layers if z is not None)
    return AirportLayers(
        layers=layers,
        seeds={
            name: np.asarray(points, dtype=np.float64).reshape(-1, 2)
            for name, points in seeds.items()
            if points
        },
        area=treated,
        surfaces=surface_area,
        array=array,
        bounds=airport_bounds(airports),
        counts=counts,
    )
