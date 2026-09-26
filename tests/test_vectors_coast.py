# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Unit tests of the coastline builder: the limit cases spec vectors-coastline.md 5 lists."""

from __future__ import annotations

import numpy as np
import pytest
from shapely import geometry

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.coast import (
    CoastParams,
    CoastTopology,
    _walk_border,
    bd_coord,
    bd_point,
    build_sea_layers,
    coastline_multilinestring,
    cut_to_tile,
    ensure_multilinestring,
    ensure_multipolygon,
    way_set_order,
)

TILE = TileRef(43, 5)


def mls(*lines: list[tuple[float, float]]) -> geometry.MultiLineString:
    return geometry.MultiLineString([geometry.LineString(line) for line in lines])


# --------------------------------------------------------------------------- helpers


def test_bd_coord_is_clockwise_from_the_south_west_corner() -> None:
    pts = np.array([(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.5), (0.5, 1.0)])
    assert bd_coord(pts).tolist() == [0.0, 1.0, 2.0, 3.0, 0.5, 1.5]


def test_bd_point_is_the_inverse_modulo_four() -> None:
    got = bd_point([0.0, 1.0, 2.0, 3.0, 4.0, 4.5])
    assert got.tolist() == [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0], [0, 0.5]]


def test_bd_coord_projects_a_point_slightly_off_the_border() -> None:
    # The 1e-5 tolerance of spec 4.1: an endpoint 3e-6 inside is still on the border.
    assert bd_coord(np.array([(3e-6, 0.4)])).tolist() == pytest.approx([0.4])


def test_bd_coord_of_nothing_is_empty() -> None:
    assert bd_coord(np.zeros((0, 2))).shape == (0,)
    assert bd_point([]).shape == (0, 2)


def test_cut_to_tile_keeps_an_end_on_the_border_but_drops_a_line_along_it() -> None:
    crossing = geometry.LineString([(-0.5, 0.5), (0.5, 0.5)])
    strict = cut_to_tile(crossing, strictly_inside=True)
    assert strict.geom_type == "LineString"
    assert min(x for x, _ in strict.coords) == 0.0
    along = geometry.LineString([(0.0, 0.2), (0.0, 0.8)])
    assert cut_to_tile(along, strictly_inside=True).is_empty
    assert not cut_to_tile(along).is_empty


def test_ensure_helpers_filter_by_type() -> None:
    coll = geometry.GeometryCollection(
        [geometry.Point(0, 0), geometry.LineString([(0, 0), (1, 1)])]
    )
    assert len(ensure_multilinestring(coll).geoms) == 1
    assert ensure_multipolygon(coll).is_empty
    assert ensure_multilinestring(geometry.Point(0, 0)).is_empty
    square = geometry.Polygon([(0, 0), (1, 0), (1, 1)])
    assert len(ensure_multipolygon(square).geoms) == 1
    assert ensure_multipolygon(geometry.MultiPolygon([square])).equals(
        geometry.MultiPolygon([square])
    )


def test_way_set_order_is_a_permutation() -> None:
    for count in (0, 1, 2, 5, 424):
        order = way_set_order(count)
        assert sorted(order) == list(range(count))


# --------------------------------------------------------------------------- inputs


def test_ways_are_rounded_to_seven_decimals_and_made_local() -> None:
    ways = [np.array([(5.123456789, 43.987654321), (5.2, 44.0)])]
    lines, read, dropped = coastline_multilinestring(ways, TILE, CoastParams())
    assert (read, dropped) == (1, 0)
    assert next(iter(lines.geoms[0].coords)) == (0.1234568, 0.9876543)


def test_a_way_with_one_point_is_dropped() -> None:
    ways = [np.array([(5.5, 43.5)]), np.array([(5.1, 43.1), (5.2, 43.2)])]
    lines, read, dropped = coastline_multilinestring(ways, TILE, CoastParams())
    assert (read, dropped, len(lines.geoms)) == (2, 1, 1)


def test_a_tile_local_geometry_is_taken_as_is() -> None:
    given = mls([(0.1, 0.1), (0.2, 0.2)])
    lines, read, dropped = coastline_multilinestring(given, TILE, CoastParams())
    assert lines.equals(given) and (read, dropped) == (1, 0)


class _Node:
    def __init__(self, i: int, lon: float, lat: float) -> None:
        self.id, self.lon, self.lat = i, lon, lat


class _Way:
    def __init__(self, i: int, nodes: list[int], tags: dict[str, str]) -> None:
        self.id, self.nodes, self.tags = i, nodes, tags


class _Source:
    def __init__(self, nodes: list[_Node], ways: list[_Way]) -> None:
        self.nodes, self.ways = nodes, ways


def test_a_snapshot_like_source_is_filtered_by_tag() -> None:
    nodes = [_Node(1, 5.1, 43.1), _Node(2, 5.2, 43.2), _Node(3, 5.3, 43.3)]
    ways = [
        _Way(10, [1, 2], {"natural": "coastline"}),
        _Way(11, [2, 3], {"waterway": "riverbank"}),
        _Way(12, [1, 3], {}),  # no tag at all: kept, see CoastParams.tag_filter
    ]
    lines, read, _ = coastline_multilinestring(_Source(nodes, ways), TILE, CoastParams())
    assert (read, len(lines.geoms)) == (2, 2)


def test_an_incomplete_way_is_dropped_not_fatal() -> None:
    nodes = [_Node(1, 5.1, 43.1)]
    ways = [_Way(10, [1, 999], {"natural": "coastline"})]
    lines, read, dropped = coastline_multilinestring(_Source(nodes, ways), TILE, CoastParams())
    assert (read, dropped, lines.is_empty) == (1, 1, True)


# --------------------------------------------------------------------------- topology


def test_no_coastline_at_all_gives_no_seed() -> None:
    result = build_sea_layers(geometry.MultiLineString(), TILE, CoastParams())
    assert result.sea_polygons.is_empty and len(result.seeds) == 0
    assert result.to_layers() == []


def test_one_open_chain_cuts_the_tile_in_two_and_seeds_the_water_side() -> None:
    # Land on the left: going east along y = 0.5, the water is to the south.
    result = build_sea_layers(mls([(-0.1, 0.5), (1.1, 0.5)]), TILE, CoastParams())
    assert result.stats.open_chains == 1
    assert result.stats.sea_polygons == 1
    assert result.sea_polygons.area == pytest.approx(0.5)
    ((x, y),) = result.seeds
    assert y < 0.5
    assert result.seed_records() == [(pytest.approx(x), pytest.approx(y), 2)]


def test_the_chain_is_reversed_and_the_other_half_becomes_the_sea() -> None:
    result = build_sea_layers(mls([(1.1, 0.5), (-0.1, 0.5)]), TILE, CoastParams())
    assert result.sea_polygons.area == pytest.approx(0.5)
    assert result.seeds[0][1] > 0.5


def test_a_ccw_ring_is_an_island_in_an_all_sea_tile() -> None:
    island = [(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6), (0.4, 0.4)]
    result = build_sea_layers(mls(island), TILE, CoastParams())
    assert result.stats.tile_assumed_sea and result.stats.islands == 1
    assert result.sea_polygons.area == pytest.approx(1 - 0.04)
    assert result.stats.inverted_tile is False


def test_a_cw_ring_alone_inverts_the_tile_as_in_ortho4xp() -> None:
    lake = [(0.4, 0.4), (0.4, 0.6), (0.6, 0.6), (0.6, 0.4), (0.4, 0.4)]
    result = build_sea_layers(mls(lake), TILE, CoastParams())
    assert result.stats.interior_seas == 1
    assert result.stats.inverted_tile is True  # spec 4.3 / 8, kept for parity
    assert result.sea_polygons.area == pytest.approx(1 - 0.04)


def test_custom_source_forces_every_ring_to_an_island() -> None:
    lake = [(0.4, 0.4), (0.4, 0.6), (0.6, 0.6), (0.6, 0.4), (0.4, 0.4)]
    result = build_sea_layers(mls(lake), TILE, CoastParams(custom_source=True))
    assert (result.stats.islands, result.stats.interior_seas) == (1, 0)


def test_an_interior_sea_inside_the_land_is_added_by_the_symmetric_difference() -> None:
    coast = [(-0.1, 0.5), (1.1, 0.5)]  # sea to the south
    lake = [(0.3, 0.7), (0.3, 0.8), (0.4, 0.8), (0.4, 0.7), (0.3, 0.7)]  # cw, in the north
    result = build_sea_layers(mls(coast, lake), TILE, CoastParams())
    assert result.stats.interior_seas == 1
    assert result.sea_polygons.area == pytest.approx(0.5 + 0.01)
    assert result.stats.sea_polygons == 2


def test_an_island_inside_a_lake_is_swallowed_as_in_ortho4xp() -> None:
    lake = [(0.2, 0.2), (0.2, 0.8), (0.8, 0.8), (0.8, 0.2), (0.2, 0.2)]  # cw, interior sea
    island = [(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6), (0.4, 0.4)]  # ccw, inside it
    coast = [(-0.1, 0.05), (1.1, 0.05)]
    result = build_sea_layers(mls(coast, lake, island), TILE, CoastParams())
    assert result.stats.islands_swallowed == 1
    # the island is not subtracted from the lake: Ortho4XP's union loses it (spec 4.4)
    assert result.sea_polygons.area == pytest.approx(0.05 + 0.36)


def test_a_chain_ending_on_a_corner_is_accepted() -> None:
    result = build_sea_layers(mls([(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)]), TILE, CoastParams())
    assert result.stats.sea_polygons == 1
    assert result.sea_polygons.area == pytest.approx(0.5)


def test_two_chains_make_two_sea_patches() -> None:
    result = build_sea_layers(
        mls([(-0.1, 0.2), (1.1, 0.2)], [(1.1, 0.8), (-0.1, 0.8)]), TILE, CoastParams()
    )
    assert result.stats.border_polygons == 2
    assert result.sea_polygons.area == pytest.approx(0.2 + 0.2)


# --------------------------------------------------------------------------- failures


def test_a_chain_stopping_inside_the_tile_raises_open_end() -> None:
    with pytest.raises(OsxpError) as excinfo:
        build_sea_layers(mls([(-0.1, 0.5), (0.5, 0.5)]), TILE, CoastParams())
    assert excinfo.value.code == "OSM_COAST_OPEN_END"
    assert "43" in str(excinfo.value.context["points"])


def test_record_mode_degrades_instead_of_raising() -> None:
    params = CoastParams(on_bad_coastline="record")
    result = build_sea_layers(mls([(-0.1, 0.5), (0.5, 0.5)]), TILE, params)
    assert result.sea_polygons.is_empty and len(result.seeds) == 0
    assert [e.code for e in result.errors] == ["OSM_COAST_OPEN_END"]
    # the SEA lines are still there: Ortho4XP encodes them before rebuilding the topology
    assert len(result.sea_lines.geoms) == 1


def test_the_border_tolerance_is_asymmetric_as_in_ortho4xp() -> None:
    # |x - int(x)| truncates towards zero, so 3e-6 from the *west* side passes ...
    west = build_sea_layers(mls([(3e-6, 0.5), (1.1, 0.5)]), TILE, CoastParams())
    assert west.stats.sea_polygons == 1
    # ... while 3e-6 from the *east* side does not: int(0.999997) is 0, so the distance
    # Ortho4XP measures is 0.999997 (spec 4.1). Only an exact 1.0 passes there.
    with pytest.raises(OsxpError) as excinfo:
        build_sea_layers(mls([(-0.1, 0.5), (1.0 - 3e-6, 0.5)]), TILE, CoastParams())
    assert excinfo.value.code == "OSM_COAST_OPEN_END"


def test_a_walk_that_never_closes_raises_orientation() -> None:
    # Two chains forming a cycle (1.5 -> 2.5 -> 1.5) that does not contain the smallest
    # arclength the walk starts from: Ortho4XP spins 1000 times and gives up (spec 4.3).
    topo = CoastTopology(
        chain_coords=[np.zeros((2, 2))] * 3,
        inits=[1.5, 2.5, 0.2],
        ends=[2.5, 1.5, 0.3],
    )
    with pytest.raises(OsxpError) as excinfo:
        _walk_border(topo, CoastParams(max_border_walk=20), TILE)
    assert excinfo.value.code == "OSM_COAST_ORIENTATION"


def test_three_chains_meeting_on_the_border_raise_triple_junction() -> None:
    # Ortho4XP lets a bare ValueError escape here; OrthoStudio XP names the junction (spec 5).
    junction = [
        [(0.0, 0.5), (0.3, 0.2), (0.5, 0.0)],
        [(0.0, 0.5), (0.3, 0.8), (0.5, 1.0)],
        [(0.9, 0.0), (0.6, 0.5), (0.0, 0.5)],
    ]
    with pytest.raises(OsxpError) as excinfo:
        build_sea_layers(mls(*junction), TILE, CoastParams())
    assert excinfo.value.code == "OSM_COAST_TRIPLE_JUNCTION"
    assert 43.0 <= excinfo.value.context["lat"] <= 44.0
    assert 5.0 <= excinfo.value.context["lon"] <= 6.0


def test_a_self_retracing_closed_line_is_not_a_ring() -> None:
    # shapely's is_ring means closed *and* simple, so this one goes to the open-chain
    # branch and its ends are judged against the border, exactly as in Ortho4XP (spec 4.4).
    degenerate = geometry.LineString([(0.3, 0.3), (0.4, 0.4), (0.3, 0.3)])
    assert degenerate.is_ring is False
    with pytest.raises(OsxpError) as excinfo:
        build_sea_layers(
            geometry.MultiLineString([degenerate, geometry.LineString([(-0.1, 0.5), (1.1, 0.5)])]),
            TILE,
            CoastParams(),
        )
    assert excinfo.value.code == "OSM_COAST_OPEN_END"


# --------------------------------------------------------------------------- outputs


def test_to_layers_carries_the_sea_marker_and_the_dem_z() -> None:
    result = build_sea_layers(mls([(-0.1, 0.5), (1.1, 0.5)]), TILE, CoastParams())
    ((geom, marker, z),) = result.to_layers(lambda way: np.full(len(way), 7.0))
    assert marker == 2
    assert geom.equals(result.sea_lines)
    assert z is not None and set(np.unique(z)) == {7.0}
    assert result.to_layers()[0][2] is None


def test_sea_equiv_is_carried_through_untouched() -> None:
    lake = geometry.MultiPolygon([geometry.Polygon([(0.1, 0.1), (0.2, 0.1), (0.2, 0.2)])])
    result = build_sea_layers(mls([(-0.1, 0.5), (1.1, 0.5)]), TILE, CoastParams(), sea_equiv=lake)
    assert result.sea_equiv.equals(lake)
    empty = build_sea_layers(geometry.MultiLineString(), TILE, CoastParams(), sea_equiv=lake)
    assert empty.sea_equiv.equals(lake)


def test_sea_lines_are_cut_to_the_tile() -> None:
    result = build_sea_layers(mls([(-0.5, 0.5), (1.5, 0.5)]), TILE, CoastParams())
    xs = [x for line in result.sea_lines.geoms for x, _ in line.coords]
    assert min(xs) == 0.0 and max(xs) == 1.0


def test_way_geometries_are_taken_as_already_local_and_in_order() -> None:
    class _G:
        def __init__(self, coords: np.ndarray) -> None:
            self.coords = coords

    given = [
        _G(np.array([(0.1, 0.1), (0.2, 0.2)])),
        _G(np.array([(0.3, 0.3), (0.4, 0.4)])),
    ]
    lines, read, dropped = coastline_multilinestring(given, TILE, CoastParams())
    assert (read, dropped) == (2, 0)
    # no origin shift, no re-rounding, and the caller's order is kept (osmdata.ways_with()
    # already iterates first["w"], i.e. Ortho4XP's own order)
    assert next(iter(lines.geoms[0].coords)) == (0.1, 0.1)


def test_an_osmdata_store_is_refused_with_a_hint() -> None:
    class _Store:
        def __init__(self) -> None:
            self.nodes = {1: (5.1, 43.1)}
            self.ways = {-1: [1]}

    with pytest.raises(TypeError, match="ways_with"):
        coastline_multilinestring(_Store(), TILE, CoastParams())
