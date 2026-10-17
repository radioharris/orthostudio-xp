# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Region seeds (``docs/specs/vectors-assembly.md`` section 4)."""

from __future__ import annotations

import numpy as np
import pytest
import shapely
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon

from orthostudio.airports_vec.encode import _runway_seeds
from orthostudio.model import TileRef
from orthostudio.vectors.noding import MARKERS
from orthostudio.vectors.seeds import (
    CENTRE_SEED,
    MIN_LAND_ALTITUDE,
    OUTSIDE_SEED,
    SEED_MARGIN_M,
    SeedSet,
    default_seeds,
    inner_points,
    marker_name,
    polygon_seeds,
    seed_rows,
)
from orthostudio.vectors.water import LAT_TO_M, lon_to_m, seed_margin


def square(x: float, y: float, side: float = 1.0) -> Polygon:
    return Polygon([(x, y), (x + side, y), (x + side, y + side), (x, y + side)])


# ----------------------------------------------------------------------------- polygon_seeds
def test_one_seed_per_polygon_in_polygon_order() -> None:
    polygons = MultiPolygon([square(0, 0), square(5, 5), square(9, 1)])
    seeds, skipped = polygon_seeds(polygons, margin=0.0)
    assert skipped == 0
    assert seeds.shape == (3, 2)
    for seed, polygon in zip(seeds, polygons.geoms, strict=True):
        assert polygon.contains(shapely.Point(seed))


def test_seed_is_inside_even_when_the_centroid_is_not() -> None:
    horseshoe = Polygon([(0, 0), (3, 0), (3, 1), (1, 1), (1, 2), (3, 2), (3, 3), (0, 3)])
    assert not horseshoe.contains(horseshoe.centroid)
    seeds, skipped = polygon_seeds(horseshoe, margin=0.0)
    assert skipped == 0 and len(seeds) == 1
    assert horseshoe.contains(shapely.Point(seeds[0]))


def test_a_polygon_with_a_hole_seeds_outside_the_hole() -> None:
    ring = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(2, 2), (8, 2), (8, 8), (2, 8)]])
    seeds, _ = polygon_seeds(ring, margin=0.0)
    assert ring.contains(shapely.Point(seeds[0]))


def test_lines_never_seed() -> None:
    seeds, skipped = polygon_seeds(LineString([(0, 0), (1, 1)]), margin=0.0)
    assert seeds.shape == (0, 2) and skipped == 0


def test_empty_polygons_are_skipped_and_counted() -> None:
    seeds, skipped = polygon_seeds(
        GeometryCollection([Polygon(), square(0, 0), Polygon()]), margin=0.0
    )
    assert len(seeds) == 1 and skipped == 2


def test_polygons_inside_a_collection_of_multipolygons_are_found() -> None:
    geometry = GeometryCollection([MultiPolygon([square(0, 0), square(3, 3)])])
    seeds, skipped = polygon_seeds(geometry, margin=0.0)
    assert len(seeds) == 2 and skipped == 0


# ----------------------------------------------------------------------------- SeedSet
def test_insertion_order_is_kept_inside_one_marker() -> None:
    seeds = SeedSet()
    seeds.add(MARKERS["WATER"], np.array([[0.1, 0.1]]))
    seeds.add(MARKERS["SEA"], np.array([[0.2, 0.2], [0.3, 0.3]]))
    seeds.add(MARKERS["WATER"], np.array([[0.4, 0.4]]))
    assert len(seeds) == 4
    assert np.array_equal(seeds.to_dict()[MARKERS["WATER"]], [[0.1, 0.1], [0.4, 0.4]])
    assert seeds.counts() == {"WATER": 2, "SEA": 2}


def test_written_order_is_by_marker_value_then_insertion() -> None:
    seeds = SeedSet()
    seeds.add(MARKERS["HANGAR"], np.array([[0.9, 0.9]]))
    seeds.add(MARKERS["WATER"], np.array([[0.1, 0.1], [0.2, 0.2]]))
    seeds.add(MARKERS["SEA"], np.array([[0.5, 0.5]]))
    rows = seed_rows(seeds.to_dict())
    assert np.array_equal(rows[:, 2], [1, 1, 2, 128])
    assert np.array_equal(rows[:2, 0], [0.1, 0.2])


def test_none_and_empty_seeds_are_no_ops() -> None:
    seeds = SeedSet()
    seeds.add(MARKERS["WATER"], None)
    seeds.add(MARKERS["WATER"], np.zeros((0, 2)))
    assert len(seeds) == 0 and seeds.to_dict() == {}


def test_add_polygons_counts_what_geos_refuses() -> None:
    seeds = SeedSet()
    seeds.add_polygons(MARKERS["WATER"], GeometryCollection([Polygon(), square(0, 0)]), margin=0.0)
    assert len(seeds) == 1 and seeds.skipped == 1


def test_an_unnamed_combination_of_bits_is_refused() -> None:
    with pytest.raises(ValueError, match="no attribute name"):
        SeedSet().add(MARKERS["WATER"] | MARKERS["SEA"], np.array([[0.5, 0.5]]))
    assert marker_name(MARKERS["SEA_EQUIV"]) == "SEA_EQUIV"


# ----------------------------------------------------------------------------- defaults
def test_a_tile_with_relief_gets_one_seed_outside_the_tile() -> None:
    seeds = default_seeds(1253.0)
    assert list(seeds) == [MARKERS["SEA"]]
    assert np.array_equal(seeds[MARKERS["SEA"]], [OUTSIDE_SEED])


def test_a_tile_flat_at_sea_level_is_all_sea() -> None:
    seeds = default_seeds(0.4)
    assert np.array_equal(seeds[MARKERS["SEA"]], [CENTRE_SEED])
    # the threshold is ">= 1", not "> 1"
    assert np.array_equal(default_seeds(MIN_LAND_ALTITUDE)[MARKERS["SEA"]], [OUTSIDE_SEED])


def test_the_default_applies_only_to_a_completely_empty_map() -> None:
    seeds = SeedSet()
    seeds.add(MARKERS["WATER"], np.array([[0.5, 0.5]]))
    assert list(seeds.finalise(0.0)) == [MARKERS["WATER"]]
    assert list(SeedSet().finalise(0.0)) == [MARKERS["SEA"]]


def test_seed_rows_of_an_empty_map() -> None:
    assert seed_rows({}).shape == (0, 3)


# ----------------------------------------------------------------------------- the margin
#: Haneda's six runway outlines as the vectors stage of +35+139 hands them to ``_runway_seeds``
#: (tile-local degrees, recorded 2026-10-16): the first and the fourth, the third and the sixth
#: are the same runways drawn twice, a few millimetres apart.
HANEDA_RUNWAYS = [
    [
        (0.7609474685777922, 0.5491819091519807),
        (0.7767461685777921, 0.5675843091519808),
        (0.7773792314222079, 0.5672240908480193),
        (0.7615805314222079, 0.5488216908480192),
    ],
    [
        (0.8031858131683014, 0.5242081682953117),
        (0.8217650131683013, 0.5407484682953116),
        (0.8223349868316986, 0.5403241317046883),
        (0.8037557868316987, 0.5237838317046883),
    ],
    [
        (0.8051276160223975, 0.539074210744317),
        (0.7858972160223975, 0.5661703107443171),
        (0.7865659839776026, 0.566484889255683),
        (0.8057963839776026, 0.5393887892556829),
    ],
    [
        (0.7778078318170525, 0.5677232901247344),
        (0.7612054318170525, 0.5483847901247345),
        (0.7605723681829475, 0.5487450098752655),
        (0.7771747681829475, 0.5680835098752655),
    ],
    [
        (0.7853074043185062, 0.5364718301173652),
        (0.7684914043185063, 0.5601697301173653),
        (0.7691601956814937, 0.5604842698826348),
        (0.7859761956814937, 0.5367863698826347),
    ],
    [
        (0.7868936921471719, 0.5660231770197373),
        (0.8054532921471719, 0.5398695770197373),
        (0.8047845078528281, 0.5395550229802628),
        (0.7862249078528281, 0.5657086229802628),
    ],
]
TOKYO = TileRef(35, 139)


def _nearest_outline(seeds: np.ndarray, outlines: list[Polygon]) -> float:
    """Distance from the seeds to the nearest runway outline, in tile-local degrees."""
    edges = shapely.union_all([shapely.boundary(p) for p in outlines])
    return float(shapely.distance(shapely.points(seeds), edges).min())


def test_the_margin_is_ten_centimetres_along_the_shortest_degree_of_the_tile() -> None:
    assert abs(SEED_MARGIN_M - 0.10) < 1e-12
    margin = seed_margin(TOKYO)
    assert margin * lon_to_m(36) == pytest.approx(0.10)  # the tile's edge furthest from the equator
    assert margin * LAT_TO_M > 0.10
    assert seed_margin(TileRef(-36, 139)) == pytest.approx(margin)  # same distance south


def test_haneda_runways_left_a_seed_a_millimetre_from_an_outline_and_now_none() -> None:
    outlines = [Polygon(ring) for ring in HANEDA_RUNWAYS]
    as_ortho4xp = np.array(_runway_seeds(outlines, 0.0))
    # what flooded the tile: rounding the outlines to 7 decimals moves them by up to 6 mm
    assert _nearest_outline(as_ortho4xp, outlines) * lon_to_m(36) < 0.002
    margin = seed_margin(TOKYO)
    now = np.array(_runway_seeds(outlines, margin))
    assert _nearest_outline(now, outlines) >= margin * (1 - 1e-9)
    assert 0 < len(now) < len(as_ortho4xp)  # the slivers lost theirs, the runways kept theirs


def test_a_polygon_far_from_its_edges_keeps_ortho4xps_point() -> None:
    polygons = np.array([square(0, 0), square(2, 0, 0.5)], dtype=object)
    expected = shapely.point_on_surface(polygons)
    assert shapely.equals_exact(inner_points(polygons, 0.01), expected, tolerance=0).all()


def test_a_point_too_near_an_edge_moves_inside_by_the_margin() -> None:
    # a thick foot and a 2-unit-thin leg: the middle line of the box only crosses the leg, so
    # point_on_surface lies in it, 1 from its edges
    ell = Polygon([(0, 0), (40, 0), (40, 10), (2, 10), (2, 40), (0, 40)])
    near = shapely.point_on_surface(ell)
    assert shapely.distance(near, ell.boundary) < 1.5
    moved = inner_points(np.array([ell], dtype=object), 1.5)[0]
    assert ell.contains(moved)
    assert shapely.distance(moved, ell.boundary) >= 1.5 - 1e-9


def test_a_sliver_thinner_than_twice_the_margin_gets_no_seed() -> None:
    sliver = Polygon([(0, 0), (1, 0), (1, 1e-7), (0, 1e-7)])
    seeds, skipped = polygon_seeds(MultiPolygon([sliver, square(2, 0)]), margin=1e-6)
    assert skipped == 1
    assert seeds.shape == (1, 2) and square(2, 0).contains(shapely.points(seeds[0]))


def test_a_triangle_keeps_its_centroid_when_it_is_far_enough() -> None:
    triangles = np.array(
        [Polygon([(0, 0), (3, 0), (0, 3)]), Polygon([(0, 0), (30, 0), (0, 0.3)])], dtype=object
    )
    centroids = shapely.centroid(triangles)
    points = inner_points(triangles, 0.12, chosen=centroids)
    assert shapely.equals_exact(points[0], centroids[0], tolerance=0)  # 1 from its edges
    assert not shapely.equals_exact(points[1], centroids[1], tolerance=0)  # 0.1 from: moved
    assert shapely.distance(points[1], triangles[1].boundary) >= 0.12 - 1e-9
