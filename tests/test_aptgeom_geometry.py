# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Unit tests of the airport geometry rules (``docs/specs/airports-geometry.md`` A7).

No reference build, no network: every input is a hand-built OSM store and a hand-built
:class:`orthostudio.airports_vec.model.AirportSet`, so the rules the reference tile never exercises
-- a rejected runway, a ``custom`` bypass, a relation runway, the four merge cases, the duplicate
rule, an invalid surface -- are covered here.
"""

from __future__ import annotations

import numpy as np
import pytest
from shapely import geometry

from orthostudio.airports_vec import areas as apt_areas
from orthostudio.airports_vec import runways as apt_runways
from orthostudio.airports_vec.model import Airport, AirportSet
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.osmdata import OsmData

TILE = TileRef(43, 5)
M_TO_LAT = 1 / (np.pi * 6378137 / 180)
M_TO_LON = M_TO_LAT / np.cos(np.pi * 43 / 180)


def _airport(**ways: list[int]) -> Airport:
    airport = Airport(key="APT", key_type="icao", name="test", repr_node=(5.0, 43.0))
    for name, ids in ways.items():
        airport.ways[name] = ids  # type: ignore[index]
    return airport


def _set(*airports: Airport) -> AirportSet:
    out = AirportSet()
    for airport in airports:
        out.add(airport)
    return out


def _store() -> OsmData:
    return OsmData(tile=TILE)


def _way(store: OsmData, way_id: int, points: list[tuple[float, float]], **tags: str) -> int:
    """Add a way in degrees; ``points`` are metres east and north of the tile corner."""
    node_ids = []
    for index, (east, north) in enumerate(points):
        node_id = -(abs(way_id) * 1000 + index + 1)
        store.nodes[node_id] = (5 + east * M_TO_LON, 43 + north * M_TO_LAT)
        node_ids.append(node_id)
    store.ways[way_id] = node_ids
    if tags:
        store.tags["w"][way_id] = dict(tags)
    return way_id


def _closed(store: OsmData, way_id: int, points: list[tuple[float, float]], **tags: str) -> int:
    out = _way(store, way_id, points, **tags)
    store.ways[out] = store.ways[out] + [store.ways[out][0]]
    return out


def _rectangle(cx: float, cy: float, length: float, width: float) -> list[tuple[float, float]]:
    return [
        (cx - length / 2, cy - width / 2),
        (cx + length / 2, cy - width / 2),
        (cx + length / 2, cy + width / 2),
        (cx - length / 2, cy + width / 2),
    ]


def _build(store: OsmData, airport: Airport, on_event: object = None) -> apt_runways.RunwayResult:
    return apt_runways.build_runways(
        _set(airport),
        store,
        TILE,
        on_event=on_event,  # type: ignore[arg-type]
    )


# -- area runways ------------------------------------------------------------------------------


def test_a_rectangular_closed_way_becomes_an_area_runway() -> None:
    store = _store()
    _closed(store, -1, _rectangle(3000, 3000, 1000, 45))
    airport = _airport(runway=[-1])
    result = _build(store, airport)
    assert result.runways == 1
    assert len(airport.areas.runway_as_area) == 1
    assert airport.areas.runway_as_line == ()
    runway = airport.areas.runway_as_area[0]
    assert runway.width == pytest.approx(45, abs=0.2)
    # the axis joins the mid-points of the two short sides
    assert runway.start[1] == pytest.approx(runway.end[1], abs=1e-9)
    assert abs(runway.end[0] - runway.start[0]) == pytest.approx(1000 * M_TO_LON, rel=1e-3)
    assert airport.areas.runway is not None
    assert airport.areas.runway.area == pytest.approx(runway.polygon.area, rel=1e-9)


def test_a_shape_too_far_from_a_rectangle_is_rejected() -> None:
    store = _store()
    _closed(store, -1, [(0, 0), (2000, 0), (2000, 200), (200, 200), (200, 2000), (0, 2000)])
    airport = _airport(runway=[-1])
    events: list[OsxpError] = []
    result = _build(store, airport, events.append)
    assert result.rejected == 1
    assert airport.areas.runway_as_area == ()
    assert events[0].code == "OSM_RUNWAY_REJECTED"
    assert events[0].context["reason"] == "not a rectangle"


def test_the_custom_tag_bypasses_the_rectangle_test() -> None:
    store = _store()
    _closed(
        store,
        -1,
        [(0, 0), (2000, 0), (2000, 200), (200, 200), (200, 2000), (0, 2000)],
        custom="yes",
    )
    airport = _airport(runway=[-1])
    result = _build(store, airport)
    assert result.rejected == 0
    assert len(airport.areas.runway_as_area) == 1


def test_a_tiny_closed_way_is_dropped_without_an_event() -> None:
    store = _store()
    _closed(store, -1, _rectangle(100, 100, 20, 4))
    airport = _airport(runway=[-1])
    result = _build(store, airport)
    assert airport.areas.runway_as_area == ()
    assert result.rejected == 0
    assert result.dropped_small == 1


def test_an_invalid_closed_way_is_rejected() -> None:
    store = _store()
    _closed(store, -1, [(0, 0), (1000, 45), (1000, 0), (0, 45)])
    airport = _airport(runway=[-1])
    events: list[OsxpError] = []
    result = _build(store, airport, events.append)
    assert result.rejected == 1
    assert events[0].context["reason"] == "invalid geometry"


def test_a_relation_runway_takes_its_first_outer_ring() -> None:
    store = _store()
    _closed(store, -1, _rectangle(3000, 3000, 1000, 45))
    store.relations[-7] = {"outer": [store.ways[-1]], "inner": []}
    store.tags["r"][-7] = {"aeroway": "runway"}
    airport = _airport()
    airport.runway_rels = [-7]
    _build(store, airport)
    assert len(airport.areas.runway_as_area) == 1


# -- linear runways ----------------------------------------------------------------------------


def test_a_tagged_width_gets_ten_metres_of_shoulder() -> None:
    store = _store()
    _way(store, -1, [(0, 3000), (1000, 3000)], width="40")
    airport = _airport(runway=[-1])
    _build(store, airport)
    assert airport.areas.runway_as_line[0].width == pytest.approx(50.0)


def test_an_untagged_width_grows_by_one_metre_per_kilometre() -> None:
    store = _store()
    _way(store, -1, [(0, 3000), (2500, 3000)])
    airport = _airport(runway=[-1])
    _build(store, airport)
    # 30 + floor(2500 / 1000) = 32
    assert airport.areas.runway_as_line[0].width == pytest.approx(32.0)


def test_an_unreadable_width_tag_falls_back_to_the_length_rule() -> None:
    store = _store()
    _way(store, -1, [(0, 3000), (2500, 3000)], width="about 40 m")
    airport = _airport(runway=[-1])
    _build(store, airport)
    assert airport.areas.runway_as_line[0].width == pytest.approx(32.0)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ([(0, 3000), (500, 3000)], [(500, 3000), (1000, 3000)]),  # a[-1] == b[0]
        ([(0, 3000), (500, 3000)], [(1000, 3000), (500, 3000)]),  # a[-1] == b[-1]
        ([(500, 3000), (0, 3000)], [(500, 3000), (1000, 3000)]),  # a[0] == b[0]
        ([(500, 3000), (1000, 3000)], [(0, 3000), (500, 3000)]),  # a[0] == b[-1]
    ],
)
def test_the_four_ways_two_parts_can_be_joined(
    first: list[tuple[float, float]], second: list[tuple[float, float]]
) -> None:
    store = _store()
    _way(store, -1, first)
    _way(store, -2, second)
    # the shared point must be one node, as OSM spells it
    shared_a = 0 if first[0] == (500, 3000) else 1
    shared_b = 0 if second[0] == (500, 3000) else 1
    store.ways[-2][shared_b] = store.ways[-1][shared_a]
    airport = _airport(runway=[-1, -2])
    result = _build(store, airport)
    assert result.merged == 1
    assert len(airport.areas.runway_as_line) == 1
    runway = airport.areas.runway_as_line[0]
    assert abs(runway.end[0] - runway.start[0]) == pytest.approx(1000 * M_TO_LON, rel=1e-3)


def test_two_runways_sharing_an_end_point_are_not_joined() -> None:
    store = _store()
    _way(store, -1, [(0, 3000), (1000, 3000)])
    _way(store, -2, [(1000, 3000), (1000, 4000)])
    store.ways[-2][0] = store.ways[-1][1]
    airport = _airport(runway=[-1, -2])
    result = _build(store, airport)
    assert result.merged == 0
    assert len(airport.areas.runway_as_line) == 2


def test_the_merged_part_goes_to_the_end_of_the_list() -> None:
    """Three parts: the untouched one keeps its place, the merge is appended (spec 3.3)."""
    store = _store()
    _way(store, -1, [(0, 3000), (500, 3000)], width="40")
    _way(store, -2, [(0, 6000), (900, 6000)], width="60")
    _way(store, -3, [(500, 3000), (1000, 3000)], width="50")
    store.ways[-3][0] = store.ways[-1][1]
    airport = _airport(runway=[-1, -2, -3])
    result = _build(store, airport)
    assert result.merged == 1
    widths = [part.width for part in airport.areas.runway_as_line]
    assert widths == pytest.approx([70.0, 60.0])  # the lone part first, then the merge


def test_the_merged_width_is_the_largest_of_the_parts() -> None:
    store = _store()
    _way(store, -1, [(0, 3000), (500, 3000)], width="30")
    _way(store, -2, [(500, 3000), (1000, 3000)], width="45")
    store.ways[-2][0] = store.ways[-1][1]
    airport = _airport(runway=[-1, -2])
    _build(store, airport)
    assert airport.areas.runway_as_line[0].width == pytest.approx(55.0)


# -- duplicates --------------------------------------------------------------------------------


def test_a_linear_copy_of_an_area_runway_only_lends_its_axis() -> None:
    store = _store()
    _closed(store, -1, _rectangle(3000, 3000, 1000, 45))
    _way(store, -2, [(2500, 3000), (3500, 3000)], width="60")
    airport = _airport(runway=[-1, -2])
    _build(store, airport)
    assert len(airport.areas.runway_as_area) == 1
    assert airport.areas.runway_as_line == ()
    part = airport.areas.runway_as_area[0]
    assert part.width == pytest.approx(70.0)  # the linear one's, not the rectangle's
    raw = geometry.Polygon(
        np.round(np.array([store.nodes[n] for n in store.ways[-1]]) - np.array([5.0, 43.0]), 7)
    )
    assert part.polygon.area == pytest.approx(raw.area)


def test_a_distinct_linear_runway_is_kept_beside_the_area_one() -> None:
    store = _store()
    _closed(store, -1, _rectangle(3000, 3000, 1000, 45))
    _way(store, -2, [(3000, 500), (3000, 1500)], width="60")
    airport = _airport(runway=[-1, -2])
    _build(store, airport)
    assert len(airport.areas.runway_as_area) == 1
    assert len(airport.areas.runway_as_line) == 1


def test_an_airport_with_no_runway_gets_an_empty_area_not_none() -> None:
    """``discard_unwanted`` tells "no runway" from "never built" by that ``None`` (spec 3.7)."""
    store = _store()
    airport = _airport()
    _build(store, airport)
    assert airport.areas.runway is not None
    assert airport.areas.runway.is_empty


# -- surfaces ----------------------------------------------------------------------------------


def _areas(store: OsmData, airport: Airport, on_event: object = None) -> apt_areas.AreaResult:
    return apt_areas.build_areas(
        _set(airport),
        store,
        TILE,
        on_event=on_event,  # type: ignore[arg-type]
    )


def test_hangars_are_buffered_by_two_metres_and_aprons_are_not() -> None:
    store = _store()
    _closed(store, -1, _rectangle(1000, 1000, 100, 100))  # hangar
    _closed(store, -2, _rectangle(2000, 2000, 100, 100))  # apron
    airport = _airport(hangar=[-1], apron=[-2])
    _areas(store, airport)
    raw = geometry.Polygon(
        np.round(np.array([store.nodes[n] for n in store.ways[-2]]) - np.array([5.0, 43.0]), 7)
    )
    assert airport.areas.apron is not None
    assert airport.areas.apron.area == pytest.approx(raw.area, rel=1e-9)
    assert airport.areas.hangar is not None
    assert airport.areas.hangar.area > raw.area * 1.03  # 104 m x 104 m against 100 x 100


def test_a_taxiway_line_becomes_a_thirty_metre_ribbon() -> None:
    store = _store()
    _way(store, -1, [(0, 5000), (1000, 5000)])
    airport = _airport(taxiway=[-1])
    result = _areas(store, airport)
    assert airport.areas.taxiway is not None
    (_, ymin, _, ymax) = airport.areas.taxiway.bounds
    assert (ymax - ymin) / M_TO_LAT == pytest.approx(30, abs=1.0)
    assert result.taxiways == 1


def test_an_invalid_apron_is_reported_and_skipped() -> None:
    store = _store()
    _closed(store, -1, [(0, 0), (100, 100), (100, 0), (0, 100)])
    airport = _airport(apron=[-1])
    events: list[OsxpError] = []
    result = _areas(store, airport, events.append)
    assert airport.areas.apron is not None and airport.areas.apron.is_empty
    assert result.skipped == 1
    assert events[0].code == "OSM_AIRPORT_SURFACE_INVALID"
    assert events[0].context["surface"] == "apron"


def test_an_airport_with_no_way_at_all_builds_empty_geometry() -> None:
    store = _store()
    airport = _airport()
    _areas(store, airport)
    for name in ("hangar", "apron", "taxiway"):
        area = getattr(airport.areas, name)
        assert area is not None and area.is_empty, name
