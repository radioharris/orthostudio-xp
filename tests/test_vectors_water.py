"""Unit tests of the inland water builder (``docs/specs/vectors-water-roads.md`` 2).

No network: the OSM input is written as a small Ortho4XP-style ``.osm`` file
and read back through ``OsmData``, which is the production path.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from shapely import geometry

from orthostudio.model import TileRef
from orthostudio.vectors.noding import MARKERS
from orthostudio.vectors.osmdata import OsmData
from orthostudio.vectors.water import (
    LAT_TO_M,
    WaterParams,
    build_water_layers,
    cut_to_tile,
    encode_multipolygon,
    lon_to_m,
    merge_overlapping_polygons,
    refine_way,
    scale_x,
)

TILE = TileRef(43, 5)


class FlatDem:
    """A DEM whose altitude is a known function of the position, to check the z alignment."""

    def alt_vec(self, way: np.ndarray) -> np.ndarray:
        pts = np.asarray(way, dtype=np.float64)
        return 100 * pts[:, 0] + pts[:, 1]


def square(x: float, y: float, side: float) -> geometry.Polygon:
    return geometry.box(x, y, x + side, y + side)


def write_osm(path: Path, body: str) -> Path:
    """An Ortho4XP-shaped ``.osm`` file (the reader splits lines on the quote character)."""
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="test">\n'
        + body
        + "</osm>"
    )
    return path


def water_ways(ways: list[tuple[list[tuple[float, float]], dict[str, str]]]) -> str:
    """Closed ways tagged ``natural=water``, with their nodes."""
    out: list[str] = []
    node_id = 1
    way_id = 1
    bodies: list[str] = []
    for coords, tags in ways:
        ids: list[int] = []
        for lon, lat in coords:
            out.append(f'  <node id="{node_id}" lat="{lat:.7f}" lon="{lon:.7f}" version="1"/>\n')
            ids.append(node_id)
            node_id += 1
        refs = "".join(f'    <nd ref="{i}"/>\n' for i in [*ids, ids[0]])
        tagged = "".join(f'    <tag k="{k}" v="{v}"/>\n' for k, v in tags.items())
        bodies.append(f'  <way id="{way_id}" version="1">\n{refs}{tagged}  </way>\n')
        way_id += 1
    return "".join(out) + "".join(bodies)


def load(tmp_path: Path, body: str, layer: str | None = "water") -> OsmData:
    return OsmData.load(write_osm(tmp_path / "layer.osm", body), layer=layer, tile=TILE)


# -- the merge -----------------------------------------------------------------------------


def test_polygons_that_only_touch_are_not_merged() -> None:
    """Ortho4XP merges on ``intersection.area``, so a shared edge is not an overlap (spec 2.3)."""
    out = merge_overlapping_polygons([square(0, 0, 1), square(1, 0, 1)])
    assert len(out) == 2


def test_overlapping_polygons_are_merged() -> None:
    out = merge_overlapping_polygons([square(0, 0, 1), square(0.5, 0.5, 1)])
    assert len(out) == 1
    assert out[0].area == pytest.approx(2 - 0.25)


def test_merge_is_transitive() -> None:
    """A and C do not meet, but both meet B: Ortho4XP's incremental merge joins the three."""
    out = merge_overlapping_polygons([square(0, 0, 1), square(0.9, 0, 1), square(1.8, 0, 1)])
    assert len(out) == 1


def test_a_merged_group_takes_the_rank_of_its_last_member() -> None:
    """Order of ``dico_pol``: a group is re-appended each time it grows (spec 2.3)."""
    lonely = square(10, 10, 0.5)  # smallest bounding box: last in the sorted walk
    big_a, big_b = square(0, 0, 2), square(1, 1, 2)
    out = merge_overlapping_polygons([big_a, lonely, big_b])
    assert len(out) == 2
    # big_a and big_b merge when big_b arrives (rank 1), the lonely square has rank 2.
    assert out[0].area == pytest.approx(big_a.union(big_b).area)
    assert out[1].equals(lonely)


def test_merge_disabled_keeps_every_polygon() -> None:
    """``clean_bad_geometries = False`` is ``add_pol`` (``O4_Vector_Utils.py:693``)."""
    pols = [square(0, 0, 1), square(0.5, 0.5, 1)]
    assert len(merge_overlapping_polygons(pols, merge=False)) == 2


def test_merge_drops_empty_and_invalid_polygons() -> None:
    bowtie = geometry.Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
    assert not bowtie.is_valid
    out = merge_overlapping_polygons([square(0, 0, 1), bowtie, geometry.Polygon()])
    assert len(out) == 1


# -- encoding ------------------------------------------------------------------------------


def test_encode_cuts_to_the_tile() -> None:
    out = encode_multipolygon([square(0.5, 0.5, 1)], pol_to_alt=FlatDem().alt_vec)
    assert len(out.geometry.geoms) == 1
    assert out.geometry.geoms[0].bounds == (0.5, 0.5, 1.0, 1.0)


def test_encode_drops_polygons_at_or_below_the_area_limit() -> None:
    out = encode_multipolygon(
        [square(0.1, 0.1, 0.001), square(0.2, 0.2, 0.1)],
        pol_to_alt=FlatDem().alt_vec,
        area_limit=1e-5,
    )
    assert len(out.geometry.geoms) == 1
    assert out.dropped == 1


def test_encode_aligns_z_with_get_coordinates() -> None:
    """``z`` must follow exterior-then-holes, polygon after polygon (spec 2.4)."""
    import shapely

    holed = geometry.Polygon(
        [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)],
        [[(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)]],
    )
    out = encode_multipolygon([holed, square(0.2, 0.2, 0.05)], pol_to_alt=FlatDem().alt_vec)
    coords = shapely.get_coordinates(out.geometry)
    assert len(out.z) == len(coords)
    np.testing.assert_allclose(out.z, FlatDem().alt_vec(coords))


def test_encode_orients_the_exterior_counter_clockwise() -> None:
    clockwise = geometry.Polygon([(0.1, 0.1), (0.1, 0.5), (0.5, 0.5), (0.5, 0.1)])
    out = encode_multipolygon([clockwise], pol_to_alt=FlatDem().alt_vec)
    assert out.geometry.geoms[0].exterior.is_ccw


def test_encode_plants_one_seed_per_polygon_inside_it() -> None:
    out = encode_multipolygon(
        [square(0.1, 0.1, 0.2), square(0.5, 0.5, 0.2)], pol_to_alt=FlatDem().alt_vec
    )
    assert out.seeds.shape == (2, 2)
    for seed, pol in zip(out.seeds, out.geometry.geoms, strict=True):
        assert pol.contains(geometry.Point(seed))


def test_refine_way_matches_the_ortho4xp_formula() -> None:
    """``refine_way`` is transcribed statement by statement (``O4_Vector_Utils.py:1114``)."""
    scalx = scale_x(TILE)
    rng = np.random.default_rng(7)
    way = np.cumsum(rng.normal(0, 0.002, size=(12, 2)), axis=0)

    def reference(way: np.ndarray, max_length: float) -> np.ndarray:
        new_way = []
        for i in range(len(way) - 1):
            new_way.append(way[i])
            ins = int(
                np.sqrt(np.sum((way[i] - way[i + 1]) ** 2 * np.array([[scalx**2, 1]])))
                * LAT_TO_M
                // max_length
            )
            new_way.extend(
                [
                    (
                        j / (ins + 1) * way[i + 1][0] + (ins + 1 - j) / (ins + 1) * way[i][0],
                        j / (ins + 1) * way[i + 1][1] + (ins + 1 - j) / (ins + 1) * way[i][1],
                    )
                    for j in range(1, ins + 1)
                ]
            )
        new_way.append(way[-1])
        return np.array(new_way)

    for max_length in (30.0, 100.0, 250.0):
        mine = refine_way(way, max_length, scalx)
        theirs = reference(way, max_length)
        assert mine.shape == theirs.shape
        assert np.array_equal(mine, theirs)  # bit for bit, not just close


def test_cut_to_tile_strictly_inside_removes_the_border() -> None:
    line = geometry.LineString([(0.0, 0.2), (0.5, 0.2)])
    assert cut_to_tile(line).length == pytest.approx(0.5)
    assert cut_to_tile(geometry.LineString([(0, 0), (1, 0)]), strictly_inside=True).is_empty


# -- the builder ---------------------------------------------------------------------------


def test_build_water_layers_gives_one_water_layer(tmp_path: Path) -> None:
    body = water_ways(
        [([(5.2, 43.2), (5.3, 43.2), (5.3, 43.3), (5.2, 43.3)], {"natural": "water"})]
    )
    result = build_water_layers(load(tmp_path, body), TILE, WaterParams(), FlatDem())
    assert [marker for _, marker, _ in result.layers] == [MARKERS["WATER"]]
    assert result.counts["water"] == 1
    assert result.seeds["WATER"].shape == (1, 2)
    assert not result.lakes


def test_a_lake_larger_than_max_area_becomes_sea_equiv(tmp_path: Path) -> None:
    """``filter_large_lakes`` (``O4_Vector_Map.py:456-500``); the threshold is in km²."""
    body = water_ways(
        [
            (
                [(5.1, 43.1), (5.6, 43.1), (5.6, 43.6), (5.1, 43.6)],
                {"natural": "water", "name": "Big"},
            )
        ]
    )
    data = load(tmp_path, body)
    result = build_water_layers(data, TILE, WaterParams(max_area=200), FlatDem())
    assert [marker for _, marker, _ in result.layers] == [MARKERS["SEA_EQUIV"]]
    assert len(result.lakes) == 1
    assert result.lakes[0].masked
    assert result.lakes[0].area_km2 == pytest.approx(
        0.25 * LAT_TO_M * lon_to_m(43.5) / 1e6, rel=1e-9
    )


def test_a_named_lake_of_the_good_imagery_list_stays_water(tmp_path: Path) -> None:
    body = water_ways(
        [
            (
                [(5.1, 43.1), (5.6, 43.1), (5.6, 43.6), (5.1, 43.6)],
                {"natural": "water", "name": "Big"},
            )
        ]
    )
    params = WaterParams(max_area=200, good_imagery_list=("Big",))
    result = build_water_layers(load(tmp_path, body), TILE, params, FlatDem())
    assert [marker for _, marker, _ in result.layers] == [MARKERS["WATER"]]
    assert result.lakes[0].masked is False


def test_min_area_drops_the_small_patches(tmp_path: Path) -> None:
    body = water_ways(
        [
            (
                [(5.2, 43.2), (5.2001, 43.2), (5.2001, 43.2001), (5.2, 43.2001)],
                {"natural": "water"},
            ),
            ([(5.3, 43.3), (5.4, 43.3), (5.4, 43.4), (5.3, 43.4)], {"natural": "water"}),
        ]
    )
    result = build_water_layers(load(tmp_path, body), TILE, WaterParams(min_area=0.01), FlatDem())
    assert result.counts["water"] == 1
    assert result.counts["dropped"] == 1


def test_no_water_gives_no_layer(tmp_path: Path) -> None:
    result = build_water_layers(load(tmp_path, ""), TILE, WaterParams(), FlatDem())
    assert result.layers == ()
    assert result.seeds == {}


def test_water_simplification_reduces_the_vertex_count(tmp_path: Path) -> None:
    coords = [(5.2 + 0.001 * k, 43.2 + 0.0000001 * (k % 2)) for k in range(40)]
    coords += [(5.24, 43.25), (5.2, 43.25)]
    body = water_ways([(coords, {"natural": "water"})])
    data = load(tmp_path, body)
    plain = build_water_layers(data, TILE, WaterParams(), FlatDem())
    simplified = build_water_layers(data, TILE, WaterParams(water_simplification=10.0), FlatDem())
    assert len(simplified.layers[0][2]) < len(plain.layers[0][2])


def test_layers_carry_the_dem_altitude(tmp_path: Path) -> None:
    body = water_ways(
        [([(5.2, 43.2), (5.3, 43.2), (5.3, 43.3), (5.2, 43.3)], {"natural": "water"})]
    )
    result = build_water_layers(load(tmp_path, body), TILE, WaterParams(), FlatDem())
    import shapely

    geom, _, z = result.layers[0]
    np.testing.assert_allclose(z, FlatDem().alt_vec(shapely.get_coordinates(geom)))


def test_without_a_dem_the_altitude_is_zero(tmp_path: Path) -> None:
    body = water_ways(
        [([(5.2, 43.2), (5.3, 43.2), (5.3, 43.3), (5.2, 43.3)], {"natural": "water"})]
    )
    result = build_water_layers(load(tmp_path, body), TILE)
    assert np.all(result.layers[0][2] == 0)
