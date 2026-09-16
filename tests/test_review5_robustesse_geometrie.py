"""Adversarial review 5 of P4 wave 1: geometric edge cases and portability.

Degenerate OSM ways, self-intersecting rings, polygons with holes, empty geometries, tiles in
the southern hemisphere and on the antimeridian. Nothing here needs the network.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely import geometry

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.assemble import AssemblyParams, VectorLayer, VectorLayers, assemble_vectors
from orthostudio.vectors.coast import CoastParams, build_sea_layers
from orthostudio.vectors.grid import gluing_border, grid_and_border, ortho_grid_abscissae
from orthostudio.vectors.noding import MARKERS
from orthostudio.vectors.osmdata import OsmData
from orthostudio.vectors.seeds import polygon_seeds
from orthostudio.vectors.triangle_files import read_node_file, read_poly_file
from orthostudio.vectors.water import (
    WaterParams,
    build_water_layers,
    encode_multipolygon,
    refine_way,
)

MARSEILLE = TileRef.parse("+43+005")


class FlatDem:
    def __init__(self, value: float = 0.0) -> None:
        self.alt_dem = np.full((8, 8), value, dtype=np.float32)

    def alt_vec(self, way):
        return np.zeros(len(np.asarray(way, dtype=np.float64).reshape(-1, 2)), dtype=np.float64)


def _osm(body: str, path: Path) -> Path:
    doc = (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<osm version='0.6' generator='review5'>\n" + body + "</osm>\n"
    )
    path.write_text(doc, encoding="utf-8")
    return path


def _nodes(points: list[tuple[float, float]], first: int = 1) -> str:
    return "".join(
        f"  <node id='{first + i}' lat='{lat:.7f}' lon='{lon:.7f}' />\n"
        for i, (lon, lat) in enumerate(points)
    )


def _way(way_id: int, refs: list[int], tags: dict[str, str] | None = None) -> str:
    out = [f"  <way id='{way_id}'>\n"]
    out += [f"    <nd ref='{r}' />\n" for r in refs]
    out += [f"    <tag k='{k}' v='{v}' />\n" for k, v in (tags or {}).items()]
    out.append("  </way>\n")
    return "".join(out)


# -- 1. degenerate OSM ways ------------------------------------------------------------------


def test_a_way_of_one_node_and_a_way_of_zero_nodes_are_dropped_with_a_code(tmp_path: Path) -> None:
    """``ways_with`` drops what ``LineString`` would refuse, and says so (Ortho4XP swallows it)."""
    body = (
        _nodes([(5.5, 43.5), (5.6, 43.6)])
        + _way(10, [1], {"natural": "coastline"})
        + _way(11, [], {"natural": "coastline"})
        + _way(12, [1, 2], {"natural": "coastline"})
    )
    data = OsmData.load(_osm(body, tmp_path / "c.osm"), layer="coastline", tile=MARSEILLE)
    skipped: list[tuple[str, dict]] = []
    ways = data.ways_with(on_skip=lambda code, ctx: skipped.append((code, ctx)))
    assert [code for code, _ in skipped] == ["OSM_WAY_INVALID"]
    # the empty way gave its internal id back, so the third way reuses it (Ortho4XP :182-189)
    assert [w.id for w in ways] == [-2]
    assert sorted(data.ways) == [-2, -1]


def test_a_ring_of_three_coordinates_produces_no_polygon(tmp_path: Path) -> None:
    """``<4`` coordinates cannot be a ring; the water builder must not crash on it."""
    body = _nodes([(5.5, 43.5), (5.6, 43.5)]) + _way(10, [1, 2, 1], {"natural": "water"})
    data = OsmData.load(_osm(body, tmp_path / "w.osm"), layer="water", tile=MARSEILLE)
    result = build_water_layers(data, MARSEILLE, WaterParams(), FlatDem())
    assert result.layers == ()
    assert result.counts["polygons"] == 0


def test_a_self_intersecting_ring_is_dropped_before_it_can_be_reported(tmp_path: Path) -> None:
    """A symmetric bow-tie has zero signed area, so the ``area`` test fires before ``is_valid``.

    Faithful to ``O4_OSM_Utils.py:673-682`` (``if not pol.area: continue`` comes first), so
    this is a *wanted* difference from "every drop is reported" -- but it means the most
    common broken OSM ring leaves no trace at all, in either implementation.
    """
    bowtie = [(5.1, 43.1), (5.2, 43.2), (5.1, 43.2), (5.2, 43.1), (5.1, 43.1)]
    body = _nodes(bowtie[:-1]) + _way(10, [1, 2, 3, 4, 1], {"natural": "water"})
    data = OsmData.load(_osm(body, tmp_path / "w.osm"), layer="water", tile=MARSEILLE)
    skipped: list[str] = []
    polygons = data.multipolygons_with(on_skip=lambda code, ctx: skipped.append(code))
    assert polygons == []
    assert skipped == [], "no code: the zero-area test fired first"
    # an asymmetric bow-tie does have an area and is then reported
    lopsided = [(5.1, 43.1), (5.9, 43.9), (5.1, 43.9), (5.6, 43.1), (5.1, 43.1)]
    body2 = _nodes(lopsided[:-1]) + _way(10, [1, 2, 3, 4, 1], {"natural": "water"})
    data2 = OsmData.load(_osm(body2, tmp_path / "w2.osm"), layer="water", tile=MARSEILLE)
    reported: list[str] = []
    assert data2.multipolygons_with(on_skip=lambda c, ctx: reported.append(c)) == []
    assert reported == ["OSM_WAY_INVALID"]


def test_a_way_whose_nodes_are_all_identical_survives_as_a_zero_length_line(
    tmp_path: Path,
) -> None:
    """A zero-length way reaches shapely; the noder must drop its segments, not the tile."""
    body = _nodes([(5.5, 43.5)]) + _way(10, [1, 1, 1], {"natural": "coastline"})
    data = OsmData.load(_osm(body, tmp_path / "c.osm"), layer="coastline", tile=MARSEILLE)
    ways = data.ways_with()
    assert len(ways) == 1 and len(ways[0].coords) == 3
    result = build_sea_layers(ways, MARSEILLE, CoastParams())
    assert result.stats.ways_read == 1
    assert result.stats.islands == 0, "a 3-coordinate ring is not a polygon"
    # nothing usable left: the whole tile becomes sea, which is Ortho4XP's behaviour too
    assert result.stats.tile_assumed_sea is True
    assert result.seeds.shape == (1, 2)


def test_a_polygon_with_a_hole_keeps_its_hole_and_gets_one_seed_inside_the_ring() -> None:
    """The hole must survive ``encode_multipolygon`` and the seed must not land in it."""
    outer = geometry.Polygon(
        [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)],
        [[(0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)]],
    )
    encoded = encode_multipolygon([outer], pol_to_alt=lambda w: np.zeros(len(w)))
    assert len(encoded.geometry.geoms) == 1
    kept = encoded.geometry.geoms[0]
    assert len(kept.interiors) == 1
    assert kept.area == pytest.approx(outer.area)
    assert len(encoded.z) == shapely.count_coordinates(encoded.geometry)
    seed = geometry.Point(encoded.seeds[0])
    assert kept.contains(seed)


def test_a_polygon_entirely_outside_the_tile_is_dropped_not_encoded_empty() -> None:
    far = geometry.Polygon([(3.0, 3.0), (3.1, 3.0), (3.1, 3.1), (3.0, 3.1)])
    encoded = encode_multipolygon([far], pol_to_alt=lambda w: np.zeros(len(w)))
    assert encoded.is_empty
    assert encoded.dropped == 0  # the cut emptied it before the area test
    assert encoded.seeds.shape == (0, 2)


def test_seeds_of_an_empty_or_non_polygonal_geometry_are_empty_not_an_exception() -> None:
    assert polygon_seeds(geometry.MultiPolygon()) == (pytest.approx(np.zeros((0, 2))), 0)
    points, skipped = polygon_seeds(geometry.LineString([(0, 0), (1, 1)]))
    assert points.shape == (0, 2) and skipped == 0


# -- 2. the tile square: southern hemisphere, antimeridian, absurd zoom -----------------------


@pytest.mark.parametrize(
    "name",
    ["+43+005", "-44+005", "-44-070", "+43+179", "+43-180", "+00+000", "-01-001", "+60+010"],
)
def test_the_ortho_grid_stays_inside_the_tile_everywhere_on_the_globe(name: str) -> None:
    """Grid abscissae must start at 0, end at 1, be sorted and strictly increasing."""
    tile = TileRef.parse(name)
    xs, ys = ortho_grid_abscissae(tile, 16)
    for values in (xs, ys):
        assert values[0] == 0 and values[-1] == 1, (name, values[0], values[-1])
        assert all(a < b for a, b in itertools.pairwise(values)), name
        assert all(0.0 <= v <= 1.0 for v in values), name


def test_defect_the_last_ortho_grid_line_can_fall_a_hair_short_of_the_tile_edge() -> None:
    """``x = 1`` is *added*, and the computed line nearest it is kept as well.

    On a tile whose eastern edge falls on a texture boundary the computed value and the
    literal ``1`` are the same float and the set merges them. When they differ by one ulp the
    tile gets two grid lines a few 1e-16 apart, which the noder then snaps onto the same node:
    harmless here, but it is the only place where the grid depends on float equality.
    """
    for lon in range(-180, 180, 7):
        tile = TileRef.parse(f"+43{lon:+04d}")
        xs, _ = ortho_grid_abscissae(tile, 16)
        gaps = np.diff(np.asarray(xs))
        assert (gaps > 0).all()
        assert gaps.min() > 1e-12, (lon, gaps.min())


@pytest.mark.parametrize("zl", [1, 4, 12, 19, 24])
def test_the_ortho_grid_survives_every_legal_zoom_level(zl: int) -> None:
    xs, ys = ortho_grid_abscissae(MARSEILLE, zl)
    assert xs[0] == 0 and xs[-1] == 1 and ys[0] == 0 and ys[-1] == 1
    assert len(xs) >= 2 and len(ys) >= 2


def test_an_out_of_range_mesh_zl_is_a_coded_refusal(tmp_path: Path) -> None:
    """Was ``test_defect_an_out_of_range_mesh_zl_raises_a_bare_valueerror_not_a_coded_error``.

    The defect: ``VectorsParams.mesh_zl`` was a plain ``int``; a tile config carrying 25 (or 0 after
    a bad edit) reached ``orthostudio.imagery.grid._check_zl``, which raises ``ValueError``, not one
    of the registry codes, so the stage failed with a traceback instead of ``CFG_VALUE_INVALID``.
    Fixed: the parameter is validated where it is declared.
    """
    from orthostudio.errors import OsxpError as _OsxpError
    from orthostudio.vectors.rule import MAX_MESH_ZL, VectorsParams

    assert VectorsParams(tile="+43+005", mesh_zl=MAX_MESH_ZL).mesh_zl == MAX_MESH_ZL
    for value in (25, -1):
        with pytest.raises(_OsxpError) as err:
            VectorsParams(tile="+43+005", mesh_zl=value)
        assert err.value.code == "CFG_VALUE_INVALID"
        assert err.value.context["name"] == "mesh_zl"
    # the grid itself still refuses the same values, one layer below
    with pytest.raises(ValueError, match="zoom level"):
        ortho_grid_abscissae(MARSEILLE, 25)


def test_the_gluing_border_is_bit_exact_and_shared_by_two_neighbouring_tiles() -> None:
    border = gluing_border()
    coords = shapely.get_coordinates(border)
    assert len(coords) == 4 * 2049
    south = np.asarray(border.geoms[0].coords)
    north = np.asarray(border.geoms[1].coords)
    assert (south[:, 0] == north[:, 0]).all(), "the two horizontal sides share their abscissae"
    assert south[1024, 0] == 0.5  # a power of two: exact in binary


def test_the_grid_and_border_altitudes_line_up_with_their_coordinates() -> None:
    dem = FlatDem()
    for lines in grid_and_border(MARSEILLE, 16, dem.alt_vec):
        assert len(lines.z) == shapely.count_coordinates(lines.geometry), lines.name


# -- 3. coastline topology edge cases ----------------------------------------------------------


def test_a_coastline_that_never_touches_the_border_raises_a_coded_error() -> None:
    """An open chain ending inside the tile is bad OSM data: ``OSM_COAST_OPEN_END``."""
    chain = np.array([[5.2, 43.2], [5.3, 43.3], [5.4, 43.25]])
    with pytest.raises(OsxpError) as err:
        build_sea_layers([chain], MARSEILLE, CoastParams())
    assert err.value.code == "OSM_COAST_OPEN_END"


def test_the_record_mode_of_the_coastline_keeps_the_error_instead_of_raising() -> None:
    chain = np.array([[5.2, 43.2], [5.3, 43.3], [5.4, 43.25]])
    result = build_sea_layers([chain], MARSEILLE, CoastParams(on_bad_coastline="record"))
    assert [e.code for e in result.errors] == ["OSM_COAST_OPEN_END"]
    assert result.sea_polygons.is_empty and result.seeds.shape == (0, 2)


def test_an_empty_coastline_layer_gives_an_empty_result_not_a_sea_tile() -> None:
    result = build_sea_layers([], MARSEILLE, CoastParams())
    assert result.sea_lines.is_empty
    assert result.seeds.shape == (0, 2)
    assert result.stats.tile_assumed_sea is False


def test_a_single_island_ring_floods_the_rest_of_the_tile_with_sea() -> None:
    """A CCW ring is land: the sea is the tile minus the island, with one seed outside it."""
    ring = np.array([[5.4, 43.4], [5.6, 43.4], [5.6, 43.6], [5.4, 43.6], [5.4, 43.4]])
    result = build_sea_layers([ring], MARSEILLE, CoastParams())
    assert result.stats.islands == 1
    assert result.stats.sea_polygons == 1
    assert result.stats.tile_assumed_sea is True
    seed = geometry.Point(result.seeds[0])
    assert not geometry.Polygon(ring - np.array([[5.0, 43.0]])).contains(seed)


def test_the_coastline_works_the_same_way_in_the_southern_hemisphere() -> None:
    tile = TileRef.parse("-44+005")
    ring = np.array([[5.4, -43.6], [5.6, -43.6], [5.6, -43.4], [5.4, -43.4], [5.4, -43.6]])
    result = build_sea_layers([ring], tile, CoastParams())
    assert result.stats.islands == 1
    assert result.stats.sea_polygons == 1
    coords = shapely.get_coordinates(result.sea_lines)
    assert ((coords >= 0) & (coords <= 1)).all(), "tile-local coordinates leave the unit square"


def test_a_way_crossing_the_antimeridian_stays_where_the_data_put_it() -> None:
    """Was ``test_defect_a_way_crossing_the_antimeridian_is_mirrored_into_the_tile``.

    The defect: tile-local ``x`` is ``lon - tile.lon``, so on tile ``+43+179`` a point at ``-179.9``
    landed at ``x = -358.9`` and the segment 179.9E -> 179.9W, which should leave the tile eastwards
    over 0.2 degree, became a segment running *westwards across the whole tile* -- a fabricated
    coastline, produced in silence. Fixed: :func:`orthostudio.vectors.geom.wrap_local_x` folds the
    abscissa by one turn of the globe, exactly and only when it is outside ``[-180, 180]``.

    The chain is still reported as open: its *other* end really does stop inside the tile,
    which is what ``OSM_COAST_OPEN_END`` is for. The point it names is now the true one.
    """
    tile = TileRef.parse("+43+179")
    crossing = np.array([[179.9, 43.5], [-179.9, 43.5]])
    result = build_sea_layers([crossing], tile, CoastParams(on_bad_coastline="record"))
    coords = shapely.get_coordinates(result.sea_lines)
    assert coords.tolist() == [[0.9, 0.5], [1.0, 0.5]], "it leaves the tile eastwards"
    assert [e.code for e in result.errors] == ["OSM_COAST_OPEN_END"]
    assert result.errors[0].context["points"] == [[43.5, 179.9]]


def test_a_way_crossing_the_antimeridian_westwards_is_folded_too() -> None:
    """The other side of the same fold, on the tile whose western edge is the antimeridian."""
    tile = TileRef.parse("+43-180")
    crossing = np.array([[179.9, 43.5], [-179.9, 43.5]])
    result = build_sea_layers([crossing], tile, CoastParams(on_bad_coastline="record"))
    coords = shapely.get_coordinates(result.sea_lines)
    assert coords.tolist() == [[0.0, 0.5], [0.1, 0.5]], "it enters the tile from the west"


def test_the_longitude_fold_is_bit_exact_inside_the_usual_range() -> None:
    """Every ordinary tile must be untouched, to the last bit, by the antimeridian fix."""
    from orthostudio.vectors.geom import wrap_local_x

    values = np.array([-180.0, -1.0, -0.1234567, 0.0, 0.5, 0.9999999, 1.0, 180.0])
    assert (wrap_local_x(values) == values).all()
    # outside it, one exact turn of 360 is added: the residue is the input's own
    assert wrap_local_x(np.array([-358.9]))[0] == -358.9 + 360.0


# -- 4. the assembly on an empty tile ----------------------------------------------------------


def test_an_empty_tile_still_writes_a_valid_node_and_poly_pair(tmp_path: Path) -> None:
    """No OSM data at all: grid + border + the single default SEA seed (spec 4.2)."""
    dem = FlatDem(120.0)
    out = assemble_vectors(
        VectorLayers(), MARSEILLE, dem, AssemblyParams(mesh_zl=16), out_dir=tmp_path
    )
    nodes = read_node_file(tmp_path / "Data+43+005.node")
    poly = read_poly_file(tmp_path / "Data+43+005.poly", nodes.first_index)
    assert nodes.first_index == 1
    assert len(nodes.xy) == len(out.graph.nodes)
    assert poly.seeds.shape == (1, 3)
    assert tuple(poly.seeds[0]) == (1000.0, 1000.0, float(MARKERS["SEA"]))
    assert set(np.unique(poly.markers)) == {MARKERS["DUMMY"]}
    assert out.stats["seeds_defaulted"] is True


def test_a_flat_tile_at_sea_level_is_flooded_from_its_centre(tmp_path: Path) -> None:
    out = assemble_vectors(VectorLayers(), MARSEILLE, FlatDem(0.0), AssemblyParams(mesh_zl=16))
    assert tuple(out.seeds[MARKERS["SEA"]][0]) == (0.5, 0.5)


def test_a_layer_whose_geometry_is_empty_is_carried_without_breaking_the_replay_file(
    tmp_path: Path,
) -> None:
    layers = VectorLayers(
        water=[VectorLayer("water", geometry.MultiPolygon(), MARKERS["WATER"], None, None)]
    )
    out = assemble_vectors(
        layers, MARSEILLE, FlatDem(120.0), AssemblyParams(mesh_zl=16), out_dir=tmp_path
    )
    with np.load(tmp_path / "layers.npz") as npz:
        assert next(iter(npz["names"])) == "water"
        assert npz["layer"].max() == len(out.layers) - 1
    assert out.stats["layers"][0]["coordinates"] == 0


def test_a_layer_name_longer_than_32_characters_survives_the_replay_file(
    tmp_path: Path,
) -> None:
    """Was ``test_defect_a_layer_name_longer_than_32_characters_is_silently_truncated``.

    The defect: ``_write_layers_npz`` stored the names as ``<U32`` and numpy truncated without
    a word, so two layers could end up sharing a name in the replay file. Fixed: no explicit
    dtype, numpy sizes the strings.
    """
    long_name = "a_very_long_layer_name_that_exceeds_the_limit"
    assert len(long_name) > 32
    layers = VectorLayers(
        water=[
            VectorLayer(
                long_name,
                geometry.MultiPolygon([geometry.Polygon([(0.1, 0.1), (0.2, 0.1), (0.2, 0.2)])]),
                MARKERS["WATER"],
                np.zeros(4),
                None,
            )
        ]
    )
    assemble_vectors(layers, MARSEILLE, FlatDem(120.0), AssemblyParams(16), out_dir=tmp_path)
    with np.load(tmp_path / "layers.npz") as npz:
        assert str(npz["names"][0]) == long_name


def test_a_mismatched_z_is_refused_loudly_by_the_noder() -> None:
    layers = VectorLayers(
        water=[
            VectorLayer(
                "water",
                geometry.LineString([(0.1, 0.1), (0.2, 0.2)]),
                MARKERS["WATER"],
                np.zeros(5),
                None,
            )
        ]
    )
    with pytest.raises(ValueError, match="z has"):
        assemble_vectors(layers, MARSEILLE, FlatDem(), AssemblyParams(16))


def test_a_seed_whose_attribute_no_layer_carries_is_refused() -> None:
    from orthostudio.vectors.assemble import to_vector_layers

    with pytest.raises(ValueError, match="seeds for"):
        to_vector_layers(
            "water",
            [(geometry.LineString([(0, 0), (1, 1)]), MARKERS["WATER"], None)],
            {"SEA": np.zeros((1, 2))},
        )


# -- 5. refine_way ------------------------------------------------------------------------------


def test_refine_way_is_a_python_loop_whose_cost_is_linear_in_the_inserted_points() -> None:
    """The only per-vertex Python loop left in the hot path (spec claims vectorisation)."""
    import time

    way = np.column_stack([np.linspace(0, 1, 5000), np.linspace(0, 1, 5000)])
    started = time.perf_counter()
    out = refine_way(way, 1.0, 0.73)
    elapsed = time.perf_counter() - started
    assert len(out) > len(way)
    assert elapsed < 5.0, f"refine_way took {elapsed:.2f}s on 5000 points"


def test_refine_way_on_a_degenerate_way_returns_it_unchanged() -> None:
    single = np.array([[0.5, 0.5]])
    assert refine_way(single, 10.0, 1.0).shape == (1, 2)
    assert refine_way(np.zeros((0, 2)), 10.0, 1.0).shape == (0, 2)
