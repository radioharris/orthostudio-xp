"""The airport discovery rules on small hand-built stores.

``docs/specs/airports-discovery.md`` R1 to R16, one test per rule: each is pinned so a later
change says which one it broke. The stores are built by filling :class:`OsmData`'s public
dictionaries directly, in the order the reader would have filled them -- that order is the
specification (spec section 1).
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from shapely import geometry

from orthostudio.airports_vec import discover as apt
from orthostudio.airports_vec.model import (
    Airport,
    AirportSet,
    SurfaceAreas,
    great_circle_m,
    m_to_lon,
)
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.osmdata import OsmData

TILE = TileRef(43, 5)


# -- store builder ----------------------------------------------------------------------------


class StoreBuilder:
    """Fill an :class:`OsmData` the way the Ortho4XP reader does: negative ids, reading order."""

    def __init__(self) -> None:
        self.store = OsmData(tile=TILE)
        self._node = 0
        self._way = 0
        self._rel = 0

    def node(self, lon: float, lat: float, **tags: str) -> int:
        self._node -= 1
        self.store.nodes[self._node] = (lon, lat)
        if tags:
            self.store.tags["n"][self._node] = dict(tags)
            self.store.first["n"].add(self._node)
        return self._node

    def way(self, points: list[tuple[float, float]], **tags: str) -> int:
        self._way -= 1
        self.store.ways[self._way] = [self.node(lon, lat) for lon, lat in points]
        if tags:
            self.store.tags["w"][self._way] = dict(tags)
            self.store.first["w"].add(self._way)
        return self._way

    def relation(self, rings: list[list[tuple[float, float]]], **tags: str) -> int:
        self._rel -= 1
        outer = [[self.node(lon, lat) for lon, lat in ring] for ring in rings]
        self.store.relations[self._rel] = {"outer": outer, "inner": []}
        self.store.relations_orig[self._rel] = {"outer": [], "inner": []}
        if tags:
            self.store.tags["r"][self._rel] = dict(tags)
            self.store.first["r"].add(self._rel)
        return self._rel


def square(x: float, y: float, side: float) -> list[tuple[float, float]]:
    """A closed square of ``side`` degrees with its south-west corner at ``(x, y)``."""
    return [(x, y), (x + side, y), (x + side, y + side), (x, y + side), (x, y)]


def areas_of(*, runway: float = 0.0) -> SurfaceAreas:
    """A filled :class:`SurfaceAreas`, as the other modules of the wave would leave it."""
    pol = geometry.Polygon(square(5.5, 43.5, runway)) if runway else geometry.MultiPolygon()
    return SurfaceAreas(
        runway=pol,
        taxiway=geometry.MultiPolygon(),
        apron=geometry.MultiPolygon(),
        hangar=geometry.MultiPolygon(),
    )


# -- R1 to R7: discovery ----------------------------------------------------------------------


def test_an_aerodrome_is_recognised_by_any_tag_value() -> None:
    """R1: the test is on the tag **values**, so ``landuse=aerodrome`` counts too."""
    b = StoreBuilder()
    b.way(square(5.1, 43.1, 0.01), landuse="aerodrome", icao="LFXA")
    b.way(square(5.3, 43.1, 0.01), aeroway="airstrip", icao="LFXB")
    b.way(square(5.5, 43.1, 0.01), aeroway="taxiway", icao="LFXC")
    airports = apt.discover_names(b.store)
    assert [a.key for a in airports] == ["LFXA", "LFXB"]


def test_relations_come_before_ways_before_nodes() -> None:
    """R1: the ``("r", "w", "n")`` loop fixes the record order, hence the attachment."""
    b = StoreBuilder()
    b.node(5.5, 43.5, aeroway="aerodrome", icao="LFXN")
    b.way(square(5.1, 43.1, 0.01), aeroway="aerodrome", icao="LFXW")
    b.relation([square(5.3, 43.3, 0.01)], aeroway="aerodrome", icao="LFXR")
    airports = apt.discover_names(b.store)
    assert [a.key for a in airports] == ["LFXR", "LFXW", "LFXN"]


def test_the_key_precedence_and_its_truncations() -> None:
    """R2: ``icao[:4]``, then ``iata[:3]``, then the whole ``local_ref``."""
    b = StoreBuilder()
    b.way(square(5.1, 43.1, 0.01), aeroway="aerodrome", icao="LFMLX", iata="MRSX")
    b.way(square(5.3, 43.1, 0.01), aeroway="aerodrome", iata="NCEX")
    b.way(square(5.5, 43.1, 0.01), aeroway="aerodrome", local_ref="LF1234567")
    airports = apt.discover_names(b.store)
    assert [(a.key, a.key_type) for a in airports] == [
        ("LFML", "icao"),
        ("NCE", "iata"),
        ("LF1234567", "local_ref"),
    ]


def test_a_second_element_with_a_known_key_is_skipped_whole() -> None:
    """R2: ``continue`` -- the duplicate adds nothing, not even its boundary."""
    b = StoreBuilder()
    b.way(square(5.1, 43.1, 0.01), aeroway="aerodrome", icao="LFML", name="first")
    b.way(square(5.3, 43.1, 0.02), aeroway="aerodrome", icao="LFML", name="second")
    airports = apt.discover_names(b.store)
    assert len(airports) == 1
    assert airports["LFML"].name == "first"


def test_the_name_precedence_its_two_entities_and_its_cut() -> None:
    """R3: ``name:en`` wins, ``&quot;`` and ``&apos;`` are undone, ``&amp;`` is not."""
    b = StoreBuilder()
    b.way(square(5.1, 43.1, 0.01), aeroway="aerodrome", icao="LFXA", name="fr", **{"name:en": "en"})
    b.way(
        square(5.3, 43.1, 0.01),
        aeroway="aerodrome",
        icao="LFXB",
        name="A&quot;B&apos;C&amp;D",
    )
    b.way(square(5.5, 43.1, 0.01), aeroway="aerodrome", icao="LFXC", name="x" * 60)
    b.way(square(5.7, 43.1, 0.01), aeroway="aerodrome", icao="LFXD")
    airports = apt.discover_names(b.store)
    names = [a.name for a in airports]
    assert names[0] == "en"
    assert names[1] == "A\"B'C&amp;D"
    assert names[2] == "x" * 57 + "..." and len(names[2]) == 60
    assert names[3] == "****"


def test_the_representative_point_is_the_mean_of_every_node() -> None:
    """R4: ``numpy.mean`` over the way, the closing node of a closed way counted twice."""
    b = StoreBuilder()
    b.way(square(5.0, 43.0, 0.2), aeroway="aerodrome", icao="LFXA")
    airport = apt.discover_names(b.store)["LFXA"]
    coords = np.array(square(5.0, 43.0, 0.2))
    assert airport.repr_node == tuple(np.mean(coords, axis=0))


def test_the_fallback_key_is_the_name_then_the_point() -> None:
    """R5: no code tag -> the name; no name -> the mean point, itself deduplicated."""
    b = StoreBuilder()
    b.way(square(5.1, 43.1, 0.01), aeroway="aerodrome", name="Field")
    b.way(square(5.3, 43.1, 0.01), aeroway="aerodrome", name="Field")
    b.way(square(5.5, 43.1, 0.01), aeroway="aerodrome")
    airports = apt.discover_names(b.store)
    keys = [a.key for a in airports]
    assert keys[0] == "Field"
    assert len(keys) == 2
    assert isinstance(keys[1], tuple)
    assert airports[keys[1]].key_type == "repr_node"


def test_smoothing_pix_is_kept_only_when_it_is_an_integer() -> None:
    """R6: ``int()`` or nothing, silently."""
    b = StoreBuilder()
    b.way(square(5.1, 43.1, 0.01), aeroway="aerodrome", icao="LFXA", smoothing_pix="12")
    b.way(square(5.3, 43.1, 0.01), aeroway="aerodrome", icao="LFXB", smoothing_pix="wide")
    airports = apt.discover_names(b.store)
    assert [a.smoothing_pix for a in airports] == [12, None]


def test_a_node_aerodrome_has_no_boundary_a_relation_unions_its_rings() -> None:
    """R7: way -> polygon, relation -> union of outer rings, node -> ``None``."""
    b = StoreBuilder()
    b.node(5.5, 43.5, aeroway="aerodrome", icao="LFXN")
    b.relation([square(5.1, 43.1, 0.01), square(5.3, 43.1, 0.01)], aeroway="aerodrome", icao="LFXR")
    airports = apt.discover_names(b.store)
    assert airports["LFXN"].boundary is None
    boundary = airports["LFXR"].boundary
    assert boundary is not None
    assert boundary.geom_type == "MultiPolygon"
    assert boundary.area == pytest.approx(2e-4)


def test_an_invalid_boundary_is_dropped_and_reported() -> None:
    """R7: the airport stays, the boundary becomes ``None``, ``OSM_AIRPORT_BOUNDARY_INVALID``."""
    bowtie = [(5.0, 43.0), (5.1, 43.1), (5.1, 43.0), (5.0, 43.1), (5.0, 43.0)]
    b = StoreBuilder()
    b.way(bowtie, aeroway="aerodrome", icao="LFXA")
    events: list[tuple[str, dict[str, object]]] = []
    airports = apt.discover_names(b.store, on_event=lambda c, ctx: events.append((c, ctx)))
    assert airports["LFXA"].boundary is None
    assert [code for code, _ in events] == ["OSM_AIRPORT_BOUNDARY_INVALID"]


def test_an_unbuildable_boundary_removes_the_airport() -> None:
    """R7: Ortho4XP's bare ``except`` pops the key; OrthoStudio XP
    reports ``OSM_AIRPORT_TAG_INVALID``."""
    b = StoreBuilder()
    way_id = b.way([(5.0, 43.0), (5.1, 43.1)], aeroway="aerodrome", icao="LFXA")
    b.store.ways[way_id] = b.store.ways[way_id][:1]  # a one-node way: no polygon
    events: list[str] = []
    airports = apt.discover_names(b.store, on_event=lambda c, _ctx: events.append(c))
    assert len(airports) == 0
    assert events == ["OSM_AIRPORT_TAG_INVALID"]


# -- R8 to R12: attachment --------------------------------------------------------------------


def test_a_surface_joins_the_first_boundary_it_meets() -> None:
    """R10: two overlapping aerodromes, the earliest in the record wins."""
    b = StoreBuilder()
    b.way(square(5.0, 43.0, 0.1), aeroway="aerodrome", icao="LFXA")
    b.way(square(5.0, 43.0, 0.1), aeroway="aerodrome", icao="LFXB")
    runway = b.way([(5.02, 43.02), (5.08, 43.08)], aeroway="runway")
    airports = apt.discover(b.store, TILE)
    assert airports["LFXA"].ways["runway"] == [runway]
    assert airports["LFXB"].ways["runway"] == []


def test_the_categories_are_walked_in_ortho4xp_order() -> None:
    """R8: runway, taxiway, apron, hangar -- it decides which orphan is created first."""
    b = StoreBuilder()
    b.way([(6.0, 44.0), (6.001, 44.001)], aeroway="hangar", name="H")
    b.way([(7.0, 44.0), (7.001, 44.001)], aeroway="runway", name="R")
    airports = apt.discover(b.store, TILE)
    assert [a.key for a in airports] == ["R", "H"]


def test_a_loose_surface_joins_the_nearest_airport_within_3500_m() -> None:
    """R11: ``GEO.dist`` against every ``repr_node``, first strict minimum, 3500 m."""
    b = StoreBuilder()
    b.node(5.0, 43.0, aeroway="aerodrome", icao="LFXA")
    near = (5.0, 43.0 + 3000 * apt.M_TO_LAT)
    far = (5.0, 43.0 + 4000 * apt.M_TO_LAT)
    assert great_circle_m((5.0, 43.0), near) < 3500 < great_circle_m((5.0, 43.0), far)
    r1 = b.way([near, (near[0] + 1e-6, near[1])], aeroway="runway")
    b.way([far, (far[0] + 1e-6, far[1])], aeroway="runway", name="Far")
    airports = apt.discover(b.store, TILE)
    assert airports["LFXA"].ways["runway"] == [r1]
    assert airports["Far"].key_type == "name"


def test_an_orphan_surface_makes_its_own_airport() -> None:
    """R12: keyed by ``name``, or by the mean point when the surface has no name."""
    b = StoreBuilder()
    way = b.way([(6.0, 44.0), (6.002, 44.002)], aeroway="apron")
    airports = apt.discover(b.store, TILE)
    key = airports.keys()[0]
    assert isinstance(key, tuple)
    assert key == pytest.approx((6.001, 44.001))
    assert airports[key].key_type == "repr_node"
    assert airports[key].name == "****"
    assert airports[key].ways["apron"] == [way]


def test_an_orphan_named_like_an_airport_replaces_it() -> None:
    """R12: ``dico_airports[name] = {...}`` overwrites; the ``+43+005`` witness is ``voisin``."""
    b = StoreBuilder()
    b.way(square(5.0, 43.0, 0.1), aeroway="aerodrome", name="Shared")
    b.way([(5.02, 43.02), (5.08, 43.08)], aeroway="runway")
    b.way([(8.0, 44.0), (8.002, 44.002)], aeroway="hangar", name="Shared")
    airports = apt.discover(b.store, TILE)
    assert len(airports) == 1
    shared = airports["Shared"]
    assert shared.boundary is None, "the aerodrome's outline is gone with its entry"
    assert shared.ways["runway"] == [], "and so are the surfaces attached before it"
    assert len(shared.ways["hangar"]) == 1


def test_a_runway_relation_lands_in_runway_rels() -> None:
    """R8: the relation pass fills ``runway_as_rel``, never ``ways["runway"]``."""
    b = StoreBuilder()
    b.way(square(5.0, 43.0, 0.1), aeroway="aerodrome", icao="LFXA")
    rel = b.relation([square(5.02, 43.02, 0.01)], aeroway="runway")
    airports = apt.discover(b.store, TILE)
    assert airports["LFXA"].runway_rels == [rel]
    assert airports["LFXA"].ways["runway"] == []


def test_a_one_node_surface_is_skipped_instead_of_crashing() -> None:
    """Difference 2: Ortho4XP dies inside ``LineString``; OrthoStudio XP reports and carries on."""
    b = StoreBuilder()
    b.way(square(5.0, 43.0, 0.1), aeroway="aerodrome", icao="LFXA")
    way = b.way([(5.02, 43.02), (5.03, 43.03)], aeroway="runway")
    b.store.ways[way] = b.store.ways[way][:1]
    events: list[str] = []
    airports = apt.discover(b.store, TILE, on_event=lambda c, _ctx: events.append(c))
    assert events == ["OSM_AIRPORT_SURFACE_INVALID"]
    assert airports["LFXA"].ways["runway"] == []


# -- R13 to R16: filter, boundaries, raster, listing -------------------------------------------


def test_the_size_filter_uses_the_boundary_then_the_runway_area() -> None:
    """R13: 5000 m2 on an outline, 2500 m2 on a runway area, and never both."""
    scale = apt.M_TO_LAT * m_to_lon(TILE.lat)
    big = (6000 * scale) ** 0.5
    small = (4000 * scale) ** 0.5
    airports = AirportSet()
    airports.add(Airport("BIG", "icao", "big", (5.5, 43.5), geometry.Polygon(square(0, 0, big))))
    airports.add(
        Airport("TINY", "icao", "tiny", (5.5, 43.5), geometry.Polygon(square(0, 0, small)))
    )
    kept = Airport("KEPT", "icao", "kept", (5.5, 43.5))
    kept.areas = areas_of(runway=(3000 * scale) ** 0.5)
    airports.add(kept)
    gone = Airport("GONE", "icao", "gone", (5.5, 43.5))
    gone.areas = areas_of(runway=(2000 * scale) ** 0.5)
    airports.add(gone)
    events: list[str] = []
    apt.discard_unwanted(airports, TILE, on_event=lambda c, _ctx: events.append(c))
    assert [a.key for a in airports] == ["BIG", "KEPT"]
    assert events == ["OSM_AIRPORT_TOO_SMALL", "OSM_AIRPORT_TOO_SMALL"]


def test_the_size_filter_refuses_to_guess_when_the_runways_are_missing() -> None:
    """Difference 3: Ortho4XP raises ``AttributeError``, OrthoStudio XP a coded error."""
    airports = AirportSet()
    airports.add(Airport("NOAREA", "icao", "no area", (5.5, 43.5)))
    with pytest.raises(OsxpError) as caught:
        apt.discard_unwanted(airports, TILE)
    assert caught.value.code == "OSM_AIRPORT_INFO_UNAVAILABLE"


def test_update_boundaries_translates_and_unions() -> None:
    """R14: the OSM outline moves into tile-local degrees and joins the built surfaces."""
    airports = AirportSet()
    airport = Airport("LFXA", "icao", "a", (5.5, 43.5), geometry.Polygon(square(5.2, 43.2, 0.1)))
    airport.areas = SurfaceAreas(
        runway=geometry.Polygon(square(0.6, 0.6, 0.1)),
        taxiway=geometry.MultiPolygon(),
        apron=geometry.MultiPolygon(),
        hangar=geometry.MultiPolygon(),
    )
    airports.add(airport)
    apt.update_boundaries(airports, TILE)
    boundary = airports["LFXA"].boundary
    assert boundary is not None
    assert boundary.geom_type == "MultiPolygon"
    assert len(boundary.geoms) == 2
    assert boundary.bounds == pytest.approx((0.2, 0.2, 0.7, 0.7))


def test_the_raster_grows_each_airport_by_1500_m_and_is_inclusive() -> None:
    """R15: 1001x1001, rows from the north, Ortho4XP's **inclusive** ``[rowmin:rowmax + 1]``
    slice."""
    side = 0.05
    airports = AirportSet()
    airport = Airport("LFXA", "icao", "a", (5.5, 43.5))
    airport.boundary = geometry.Polygon(square(0.4, 0.3, side))
    airports.add(airport)
    array = apt.airport_array(airports, TILE)
    assert array.shape == (1001, 1001)

    x_shift = 1500 * m_to_lon(TILE.lat)
    y_shift = 1500 * apt.M_TO_LAT
    colmin = round((0.4 - x_shift) * 1000)
    colmax = round((0.4 + side + x_shift) * 1000)
    rowmin = round((1 - 0.3 - side - y_shift) * 1000)
    rowmax = round((1 - 0.3 + y_shift) * 1000)
    # Both ends are *inside* the box: that is the one-pixel-wider slice of ``:921``.
    assert array[rowmin, colmin] and array[rowmax, colmax]
    assert not array[rowmin - 1, colmin] and not array[rowmax + 1, colmax]
    assert not array[rowmin, colmin - 1] and not array[rowmax, colmax + 1]
    assert array.sum() == (rowmax - rowmin + 1) * (colmax - colmin + 1)


def test_the_raster_skips_an_airport_with_no_boundary_left() -> None:
    """Difference 2: Ortho4XP reaches ``round(nan)`` on an empty boundary and dies."""
    airports = AirportSet()
    airports.add(Airport("LFXA", "icao", "a", (5.5, 43.5), geometry.MultiPolygon()))
    events: list[str] = []
    array = apt.airport_array(airports, TILE, on_event=lambda c, _ctx: events.append(c))
    assert not array.any()
    assert events == ["OSM_AIRPORT_BOUNDARY_INVALID"]


def test_the_listing_groups_by_key_type_then_sorts() -> None:
    """R16: icao, iata, local_ref, name, repr_node; each group sorted; ``****`` elsewhere."""
    airports = AirportSet()
    for key, key_type in (
        ("Zulu", "name"),
        ("LFXB", "icao"),
        ("Alpha", "name"),
        ("LFXA", "icao"),
        ("NCE", "iata"),
    ):
        airports.add(Airport(key, key_type, key, (5.5, 43.5)))  # type: ignore[arg-type]
    rows = apt.listing(airports)
    assert [row.key for row in rows] == ["LFXA", "LFXB", "NCE", "Alpha", "Zulu"]
    assert [row.code for row in rows] == ["LFXA", "LFXB", "NCE", "****", "****"]
    assert all(row.runways == 0 for row in rows)


def test_local_bounds_is_what_airports_json_carries() -> None:
    """``VectorLayers.airport_bounds``: ``(A, 4)`` tile-local boxes, empty when there is none."""
    airports = AirportSet()
    airport = Airport("LFXA", "icao", "a", (5.5, 43.5))
    airport.boundary = geometry.Polygon(square(0.1, 0.2, 0.3))
    airports.add(airport)
    airports.add(Airport("LFXB", "icao", "b", (5.5, 43.5), geometry.MultiPolygon()))
    bounds = apt.local_bounds(airports)
    assert bounds.shape == (1, 4)
    assert bounds[0] == pytest.approx([0.1, 0.2, 0.4, 0.5])
    assert apt.local_bounds(AirportSet()).shape == (0, 4)


# -- section 5: the index against the double loop ----------------------------------------------


def _naive_owner(owners: list[Airport], line: geometry.LineString) -> int:
    """Ortho4XP's ``:194-201``, verbatim, for the scaling comparison."""
    for i, airport in enumerate(owners):
        if airport.boundary is not None and line.intersects(airport.boundary):
            return i
    return -1


@pytest.mark.parametrize("n_airports", [8, 64, 512])
def test_the_index_answers_what_the_double_loop_answers(
    n_airports: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """Spec section 5: same owner for every surface, and the gain grows with the record."""
    rng = np.random.default_rng(43005)
    airports = AirportSet()
    for i in range(n_airports):
        x, y = rng.random(2) * 0.9
        airport = Airport(f"A{i:04d}", "icao", f"a{i}", (5.0 + x, 43.0 + y))
        airport.boundary = geometry.Polygon(square(5.0 + x, 43.0 + y, 0.02))
        airports.add(airport)
    owners = airports.with_boundary()
    # Half the surfaces sit on an aerodrome (the loop breaks early, on average halfway) and
    # half sit nowhere (the loop walks the whole record): neither extreme alone.
    surfaces = []
    lines = []
    for i in range(600):
        if i % 2:
            x, y = rng.random(2) * 0.9
        else:
            centre = owners[int(rng.integers(n_airports))].boundary
            assert centre is not None
            x, y = centre.bounds[0] - 5.0 + 0.005, centre.bounds[1] - 43.0 + 0.005
        coords = np.array([(5.0 + x, 43.0 + y), (5.0 + x + 0.005, 43.0 + y + 0.005)])
        surfaces.append(apt._Surface("runway", -i - 1, coords, {}, False))
        lines.append(geometry.LineString(coords))

    t0 = time.perf_counter()
    fast = apt._owners_of(owners, surfaces)
    fast_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    slow = [_naive_owner(owners, line) for line in lines]
    slow_s = time.perf_counter() - t0

    assert list(fast) == slow
    with capsys.disabled():
        print(
            f"\n  {n_airports:4d} boundaries x 600 surfaces:"
            f" double loop {slow_s * 1000:7.2f} ms, index {fast_s * 1000:6.2f} ms,"
            f" x{slow_s / fast_s:.1f}"
        )


# -- the seam with the rest of the wave --------------------------------------------------------


def test_the_record_exposes_ortho4xps_flat_field_names() -> None:
    """The seam with the geometry modules of the wave (spec 2.1).

    They name the record structurally, with ``dico_airports``' own keys; the record exposes
    them as read-only views of :attr:`Airport.ways` and :attr:`Airport.runway_rels`, and the
    set exposes its store and itself as a mapping. Checked as a contract rather than against
    the sibling module's ``Protocol`` classes, which are still moving.
    """
    b = StoreBuilder()
    b.way(square(5.0, 43.0, 0.1), aeroway="aerodrome", icao="LFXA", smoothing_pix="4")
    runway = b.way([(5.02, 43.02), (5.08, 43.08)], aeroway="runway")
    taxiway = b.way([(5.03, 43.03), (5.07, 43.07)], aeroway="taxiway")
    rel = b.relation([square(5.04, 43.04, 0.005)], aeroway="runway")
    airports = apt.discover(b.store, TILE)

    record = airports["LFXA"]
    assert record.runway == [runway] == record.ways["runway"]
    assert record.taxiway == [taxiway] == record.ways["taxiway"]
    assert record.apron == [] and record.hangar == []
    assert record.runway_as_rel == [rel] == record.runway_rels
    assert record.smoothing_pix == 4
    assert record.boundary is not None

    assert airports.store is b.store
    assert list(airports.airports) == airports.keys()
    assert airports.airports["LFXA"] is record
    with pytest.raises(TypeError):
        airports.airports["LFXB"] = record  # type: ignore[index]


def test_a_hand_built_record_says_so_instead_of_crashing() -> None:
    """A record with no store is a coded error, not an ``AttributeError`` three frames down."""
    airports = AirportSet()
    with pytest.raises(OsxpError) as caught:
        _ = airports.store
    assert caught.value.code == "OSM_AIRPORT_INFO_UNAVAILABLE"
