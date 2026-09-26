# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Unit tests of the airport encoder: the altitude field, the four surfaces and their seeds.

The altitude field is checked against a **literal transcription** of
``O4_Vector_Utils.weighted_alt`` (``:1250-1280``) and of
``least_square_fit_altitude_along_way`` (``:1182-1212``) written in this file: the vectorised
version must agree with the loop it replaces, on the same inputs, to the last bits.

Spec: ``docs/specs/airports-encoding.md``.
"""

from __future__ import annotations

from math import exp

import numpy as np
import pytest
from numpy.typing import NDArray
from shapely import affinity, geometry

from orthostudio.airports_vec.encode import (
    ALT_FIT_DEGREE,
    DEFAULT_ENCODE_PARAMS,
    WEIGHTED_ALT_EPS1,
    WEIGHTED_ALT_EPS2,
    WEIGHTED_ALT_MIN,
    AirportView,
    AltitudeField,
    _fit_altitude,
    _runway_seeds,
    altitude_field,
    encode_airports,
)
from orthostudio.model import TileRef
from orthostudio.vectors.noding import MARKERS
from orthostudio.vectors.osmdata import OsmData
from orthostudio.vectors.water import LAT_TO_M, scale_x

TILE = TileRef(43, 5)
SCALX = scale_x(TILE)


class Ramp:
    """A DEM that is a plane: altitude ``100 + 1000 x + 2000 y`` metres, in tile-local degrees."""

    def alt_vec(self, way: NDArray[np.floating]) -> NDArray[np.float64]:
        points = np.asarray(way, dtype=np.float64).reshape(-1, 2)
        return 100.0 + 1000.0 * points[:, 0] + 2000.0 * points[:, 1]

    def alt(self, node) -> float:
        return float(self.alt_vec(np.array([node], dtype=np.float64))[0])


class Bumpy(Ramp):
    """The ramp plus a bump, so that a degree-7 fit is not exact and the weights matter."""

    def alt_vec(self, way: NDArray[np.floating]) -> NDArray[np.float64]:
        points = np.asarray(way, dtype=np.float64).reshape(-1, 2)
        return super().alt_vec(points) + 30.0 * np.sin(60.0 * points[:, 0])


# -- the two transcriptions the vectorised code must agree with ---------------------------------


def reference_fit(way, steps, dem, weighted):
    """``least_square_fit_altitude_along_way`` (``O4_Vector_Utils.py:1182-1212``), literally."""
    line = affinity.affine_transform(geometry.LineString(way), [SCALX, 0, 0, 1, 0, 0])
    t = np.arange(steps + 1) / steps
    coords = np.array(
        geometry.LineString([line.interpolate(x, normalized=True) for x in t]).coords
    ) * np.array([1 / SCALX, 1])
    altitudes = dem.alt_vec(coords)
    if not weighted:
        return line, np.polyfit(t, altitudes, 7)
    w = (np.maximum(np.arange(steps + 1), steps - np.arange(steps + 1)) + steps // 2) ** 2
    return line, np.polyfit(t, altitudes, 7, w=w)


def reference_weighted_alt(node, fits, dem):
    """``weighted_alt`` (``O4_Vector_Utils.py:1250-1280``), literally, rtree spelled out."""
    eps1, eps2 = WEIGHTED_ALT_EPS1, WEIGHTED_ALT_EPS2
    alti = 0.0
    weights = 0.0
    x, y = node[0] * SCALX, node[1]
    point = geometry.Point((x, y))
    for line, fit, width in fits:
        xmin, ymin, xmax, ymax = line.bounds
        if xmax < x - eps1 or xmin > x + eps1 or ymax < y - eps1 or ymin > y + eps1:
            continue
        distance = point.distance(line) * LAT_TO_M
        weight = exp(-distance / (2 * width))
        alti += np.polyval(fit, line.project(point, normalized=True)) * weight
        weights += weight
    if weights < WEIGHTED_ALT_MIN:
        return dem.alt(node)
    if x < eps2 or x > 1 - eps2 or y < eps2 or y > 1 - eps2:
        alpha = min(x / eps2, (1 - x) / eps2, y / eps2, (1 - y) / eps2)
        return alpha * alti / weights + (1 - alpha) * dem.alt(node)
    return alti / weights


# -- the altitude field --------------------------------------------------------------------------


@pytest.mark.parametrize("weighted", [True, False])
def test_the_fit_is_the_one_of_ortho4xp(weighted: bool) -> None:
    way = np.array([[0.2, 0.3], [0.6, 0.42]])
    dem = Bumpy()
    line, fit = _fit_altitude(way, 137, dem, SCALX, weighted=weighted)
    ref_line, ref_fit = reference_fit(way, 137, dem, weighted)
    assert line.equals_exact(ref_line, 0.0)
    assert fit.shape == (ALT_FIT_DEGREE + 1,)
    assert np.array_equal(fit, ref_fit)


def test_the_field_agrees_with_the_reference_loop() -> None:
    dem = Bumpy()
    fits = []
    for way, width in (
        (np.array([[0.2, 0.3], [0.6, 0.42]]), 45.0),
        (np.array([[0.25, 0.5], [0.5, 0.2]]), 15.0),
        (np.array([[0.0001, 0.0002], [0.02, 0.03]]), 15.0),
    ):
        line, fit = reference_fit(way, 100, dem, True)
        fits.append((line, fit, width))
    field = AltitudeField.build(
        [f[0] for f in fits], [f[1] for f in fits], [f[2] for f in fits], SCALX, dem
    )
    rng = np.random.default_rng(4)
    nodes = np.vstack(
        [
            rng.uniform(0.15, 0.65, size=(400, 2)),
            np.array([[0.0, 0.0], [0.0002, 0.0002], [0.9999, 0.5], [0.5, 0.99995]]),
            np.array([[0.9, 0.9]]),  # far from every fit: the raster wins
        ]
    )
    ours = field(nodes)
    reference = np.array([reference_weighted_alt(node, fits, dem) for node in nodes])
    assert np.abs(ours - reference).max() < 1e-9
    assert ours[-1] == dem.alt(nodes[-1])


def test_an_airport_without_any_surface_falls_back_to_the_raster() -> None:
    dem = Ramp()
    field = altitude_field(AirportView(key="X"), TILE, dem, osm=OsmData(tile=TILE))
    nodes = np.array([[0.3, 0.4], [0.5, 0.6]])
    assert np.array_equal(field(nodes), dem.alt_vec(nodes))


# -- the surfaces ----------------------------------------------------------------------------------


def _runway(x0: float, y0: float, x1: float, y1: float, width_deg: float = 0.001):
    """A straight runway as Ortho4XP builds it: an outline, a centre line and a width in metres."""
    start, end = np.array([x0, y0]), np.array([x1, y1])
    axis = end - start
    normal = np.array([-axis[1], axis[0]])
    normal = normal / np.linalg.norm(normal) * width_deg
    polygon = geometry.Polygon(
        [start + normal, end + normal, end - normal, start - normal, start + normal]
    )

    class _R:
        def __init__(self):
            self.polygon = polygon
            self.start = start
            self.end = end
            self.width = width_deg * LAT_TO_M

    return _R()


def _osm(ways: dict[int, list[int]], nodes: dict[int, tuple[float, float]], tags=None) -> OsmData:
    return OsmData(
        tile=TILE,
        nodes=dict(nodes),
        ways=dict(ways),
        tags={"n": {}, "w": dict(tags or {}), "r": {}},
    )


def test_one_runway_gives_an_outline_traverses_and_one_seed() -> None:
    airport = AirportView(key="LFXX", runways=(_runway(0.2, 0.3, 0.3, 0.3),))
    result = encode_airports([airport], TILE, Ramp(), osm=_osm({}, {}))
    markers = [marker for _, marker, _ in result.layers]
    assert markers == [MARKERS["RUNWAY"], MARKERS["DUMMY"]]
    assert result.counts["runways"] == 1
    assert result.counts["traverses"] > 10
    assert len(result.seeds["RUNWAY"]) == 1
    outline, _, z = result.layers[0]
    assert outline.geoms[0].coords[0] == outline.geoms[0].coords[-1]  # the ring is closed
    # the rounding of :1174 is the 7 decimals of the OSM input
    coords = np.array(outline.geoms[0].coords)
    assert np.array_equal(coords, np.round(coords, 7))
    assert len(z) == len(coords)


def test_two_crossing_runways_seed_their_crossing() -> None:
    airport = AirportView(
        key="LFXX",
        runways=(_runway(0.2, 0.3, 0.4, 0.3), _runway(0.3, 0.2, 0.3, 0.4)),
    )
    result = encode_airports([airport], TILE, Ramp(), osm=_osm({}, {}))
    seeds = result.seeds["RUNWAY"]
    # two runways: two "mine alone" parts each, plus the crossing counted once per runway
    assert len(seeds) == 6
    crossing = geometry.Point(0.3, 0.3).buffer(0.0011)
    assert sum(crossing.contains(geometry.Point(s)) for s in seeds) == 2


def test_a_runway_is_flat_to_the_smoothing_of_its_raster() -> None:
    """Spec 5.3: flatness is a property of the smoothed raster, not of the encoder."""
    runway = _runway(0.2, 0.3, 0.35, 0.3)

    class Flat(Ramp):
        def alt_vec(self, way):
            return np.full(len(np.asarray(way).reshape(-1, 2)), 412.0)

    result = encode_airports(
        [AirportView(key="A", runways=(runway,))], TILE, Flat(), osm=_osm({}, {})
    )
    z = result.layers[0][2]
    assert np.abs(z - 412.0).max() < 1e-9
    # on a sloping raster the runway is not flat, and that is Ortho4XP's behaviour
    sloped = encode_airports(
        [AirportView(key="A", runways=(runway,))], TILE, Ramp(), osm=_osm({}, {})
    )
    assert np.ptp(sloped.layers[0][2]) > 1.0


def test_taxiways_are_cut_out_of_the_runways_and_seeded() -> None:
    taxi = geometry.Polygon([(0.2, 0.32), (0.4, 0.32), (0.4, 0.33), (0.2, 0.33)])
    airport = AirportView(
        key="LFXX",
        runways=(_runway(0.2, 0.3, 0.4, 0.3),),
        runway_area=geometry.MultiPolygon([_runway(0.2, 0.3, 0.4, 0.3).polygon]),
        taxiway_area=taxi,
    )
    result = encode_airports([airport], TILE, Ramp(), osm=_osm({}, {}))
    markers = [marker for _, marker, _ in result.layers]
    assert MARKERS["TAXIWAY"] in markers
    assert len(result.seeds["TAXIWAY"]) == result.counts["taxiways"] == 1
    taxiway_layer = next(layer for layer in result.layers if layer[1] == MARKERS["TAXIWAY"])
    assert not taxiway_layer[0].intersects(airport.runway_area.buffer(-1e-6))


def test_only_an_apron_tagged_include_is_encoded() -> None:
    nodes = {-1: (5.5, 43.5), -2: (5.51, 43.5), -3: (5.51, 43.51), -4: (5.5, 43.51)}
    ways = {-1: [-1, -2, -3, -4, -1], -2: [-1, -2, -3, -4, -1]}
    osm = _osm(ways, nodes, tags={-1: {"aeroway": "apron", "include": "yes"}})
    airport = AirportView(key="LFXX", runways=(_runway(0.2, 0.3, 0.4, 0.3),), apron_ways=(-1, -2))
    result = encode_airports([airport], TILE, Ramp(), osm=osm)
    assert result.counts["aprons"] == 1
    assert len(result.seeds["APRON"]) == 1
    assert any(marker == MARKERS["APRON"] for _, marker, _ in result.layers)


def test_an_apron_without_any_runway_before_it_is_skipped() -> None:
    """The ``runway_pol`` leftover of ``:1271``: no runway yet means ``NameError``, so no apron."""
    nodes = {-1: (5.5, 43.5), -2: (5.51, 43.5), -3: (5.51, 43.51), -4: (5.5, 43.51)}
    osm = _osm({-1: [-1, -2, -3, -4, -1]}, nodes, tags={-1: {"include": "yes"}})
    events = []
    result = encode_airports(
        [AirportView(key="LFXX", apron_ways=(-1,))],
        TILE,
        Ramp(),
        osm=osm,
        on_event=events.append,
    )
    assert result.counts["aprons"] == 0
    assert [e.code for e in events] == ["OSM_AIRPORT_SURFACE_INVALID"]


def test_a_hangar_on_a_slope_is_dropped_and_a_flat_one_is_levelled() -> None:
    flat = geometry.Polygon([(0.3, 0.3), (0.3002, 0.3), (0.3002, 0.3002), (0.3, 0.3002)])
    steep = geometry.Polygon([(0.5, 0.5), (0.51, 0.5), (0.51, 0.51), (0.5, 0.51)])
    result = encode_airports(
        [AirportView(key="A", hangars=geometry.MultiPolygon([flat, steep]))],
        TILE,
        Ramp(),
        osm=_osm({}, {}),
    )
    assert result.counts["hangars"] == 1
    assert result.counts["hangars_dropped"] == 1
    assert len(result.seeds["HANGAR"]) == 1
    _, marker, z = result.layers[0]
    assert marker == MARKERS["HANGAR"]
    assert np.ptp(z) == 0.0


def test_a_patched_airport_is_not_encoded_but_still_counts_in_the_footprint() -> None:
    airport = AirportView(
        key="LFXX",
        runways=(_runway(0.2, 0.3, 0.4, 0.3),),
        runway_area=geometry.MultiPolygon([_runway(0.2, 0.3, 0.4, 0.3).polygon]),
    )
    result = encode_airports([airport], TILE, Ramp(), osm=_osm({}, {}), patch_names={"LFXX"})
    assert result.layers == ()
    assert result.counts["patched"] == 1
    assert not result.surfaces.is_empty
    assert result.area.equals(result.surfaces)


def test_the_bounds_follow_the_input_order() -> None:
    first = AirportView(key="A", boundary=geometry.box(0.1, 0.1, 0.2, 0.2))
    second = AirportView(key="B", boundary=geometry.box(0.5, 0.4, 0.6, 0.7))
    result = encode_airports([first, second], TILE, Ramp(), osm=_osm({}, {}))
    assert result.bounds.tolist() == [[0.1, 0.1, 0.2, 0.2], [0.5, 0.4, 0.6, 0.7]]
    assert result.airport_areas().area.equals(result.area)


def test_the_view_assembles_the_records_of_the_other_modules() -> None:
    class Runways:
        area = geometry.box(0.1, 0.1, 0.2, 0.2)
        runways = (_runway(0.1, 0.15, 0.2, 0.15),)

    class Surfaces:
        hangars = geometry.MultiPolygon()
        aprons = geometry.MultiPolygon()
        apron_ways = (-3,)
        taxiways = geometry.MultiPolygon()
        taxiway_ways = (-1, -2)
        boundary = geometry.box(0.0, 0.0, 0.3, 0.3)

    view = AirportView.of("LFXX", Runways(), Surfaces())
    assert view.key == "LFXX"
    assert view.taxiway_ways == (-1, -2)
    assert view.runway_area.equals(Runways.area)
    assert view.boundary.equals(Surfaces.boundary)
    assert AirportView.of("LFYY").runways == ()


def test_empty_input_gives_an_empty_result() -> None:
    result = encode_airports([], TILE, Ramp(), osm=_osm({}, {}))
    assert result.layers == ()
    assert result.seeds == {}
    assert result.bounds.shape == (0, 4)
    assert result.area.is_empty


def test_runway_seeds_of_disjoint_polygons_are_one_each() -> None:
    a = geometry.box(0.1, 0.1, 0.2, 0.2)
    b = geometry.box(0.5, 0.5, 0.6, 0.6)
    assert len(_runway_seeds([a, b])) == 2


def test_the_defaults_are_the_ones_of_ortho4xp() -> None:
    assert DEFAULT_ENCODE_PARAMS.runway_chunks == 100
    assert DEFAULT_ENCODE_PARAMS.chunk_min_size == 10.0
    assert DEFAULT_ENCODE_PARAMS.taxiway_refine_m == 20.0
    assert DEFAULT_ENCODE_PARAMS.apron_refine_m == 15.0
    assert DEFAULT_ENCODE_PARAMS.hangar_flatness_m == 1.5
