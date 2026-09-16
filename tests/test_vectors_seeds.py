"""Region seeds (``docs/specs/vectors-assembly.md`` section 4)."""

from __future__ import annotations

import numpy as np
import pytest
import shapely
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon

from orthostudio.vectors.noding import MARKERS
from orthostudio.vectors.seeds import (
    CENTRE_SEED,
    MIN_LAND_ALTITUDE,
    OUTSIDE_SEED,
    SeedSet,
    default_seeds,
    marker_name,
    polygon_seeds,
    seed_rows,
)


def square(x: float, y: float, side: float = 1.0) -> Polygon:
    return Polygon([(x, y), (x + side, y), (x + side, y + side), (x, y + side)])


# ----------------------------------------------------------------------------- polygon_seeds
def test_one_seed_per_polygon_in_polygon_order() -> None:
    polygons = MultiPolygon([square(0, 0), square(5, 5), square(9, 1)])
    seeds, skipped = polygon_seeds(polygons)
    assert skipped == 0
    assert seeds.shape == (3, 2)
    for seed, polygon in zip(seeds, polygons.geoms, strict=True):
        assert polygon.contains(shapely.Point(seed))


def test_seed_is_inside_even_when_the_centroid_is_not() -> None:
    horseshoe = Polygon([(0, 0), (3, 0), (3, 1), (1, 1), (1, 2), (3, 2), (3, 3), (0, 3)])
    assert not horseshoe.contains(horseshoe.centroid)
    seeds, skipped = polygon_seeds(horseshoe)
    assert skipped == 0 and len(seeds) == 1
    assert horseshoe.contains(shapely.Point(seeds[0]))


def test_a_polygon_with_a_hole_seeds_outside_the_hole() -> None:
    ring = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(2, 2), (8, 2), (8, 8), (2, 8)]])
    seeds, _ = polygon_seeds(ring)
    assert ring.contains(shapely.Point(seeds[0]))


def test_lines_never_seed() -> None:
    seeds, skipped = polygon_seeds(LineString([(0, 0), (1, 1)]))
    assert seeds.shape == (0, 2) and skipped == 0


def test_empty_polygons_are_skipped_and_counted() -> None:
    seeds, skipped = polygon_seeds(GeometryCollection([Polygon(), square(0, 0), Polygon()]))
    assert len(seeds) == 1 and skipped == 2


def test_polygons_inside_a_collection_of_multipolygons_are_found() -> None:
    geometry = GeometryCollection([MultiPolygon([square(0, 0), square(3, 3)])])
    seeds, skipped = polygon_seeds(geometry)
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
    seeds.add_polygons(MARKERS["WATER"], GeometryCollection([Polygon(), square(0, 0)]))
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
