"""Assembly of the vector layers and the rule ``orthostudio.vectors@1``.

Spec: ``docs/specs/vectors-assembly.md`` sections 2, 5, 6. The layers are synthetic and the
checks are structural.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon

from orthostudio.dem.dem import Dem
from orthostudio.errors import OsxpError
from orthostudio.graph import ResolvedInput, RunContext
from orthostudio.model import TileRef
from orthostudio.vectors.assemble import (
    LAYERS_NPZ,
    STATS_JSON,
    AssemblyParams,
    VectorLayer,
    VectorLayers,
    assemble_vectors,
    node_file_name,
    poly_file_name,
    to_vector_layers,
)
from orthostudio.vectors.noding import MARKERS, check_planar, node_layers
from orthostudio.vectors.rule import (
    VECTORS,
    LayerBuild,
    LayerRequest,
    VectorsJob,
    VectorsParams,
    run_vectors,
    vectors_job,
)
from orthostudio.vectors.seeds import polygon_seeds, seed_rows
from orthostudio.vectors.triangle_files import read_node_file, read_poly_file

TILE = TileRef(43, 5)
SMALL = AssemblyParams(mesh_zl=14, border_segments=8)


@dataclass(frozen=True)
class FakeDem:
    """An elevation that is easy to read back: ``z = 100 x + y``."""

    maximum: float = 1000.0

    @property
    def alt_dem(self) -> np.ndarray:
        return np.full((2, 2), self.maximum, dtype=np.float32)

    def alt_vec(self, way: np.ndarray) -> np.ndarray:
        points = np.asarray(way, dtype=np.float64)
        return 100.0 * points[:, 0] + points[:, 1]


def square(x: float, y: float, side: float = 0.1) -> Polygon:
    return Polygon([(x, y), (x + side, y), (x + side, y + side), (x, y + side)])


def polygon_layer(name: str, marker: str, *polygons: Polygon) -> VectorLayer:
    geometry = MultiPolygon(list(polygons))
    seeds, _ = polygon_seeds(geometry)
    z = np.zeros(shapely.count_coordinates(geometry))
    return VectorLayer(name, geometry, MARKERS[marker], z, seeds)


def line_layer(name: str, marker: str, *lines: LineString) -> VectorLayer:
    geometry = MultiLineString(list(lines))
    return VectorLayer(
        name, geometry, MARKERS[marker], np.zeros(shapely.count_coordinates(geometry))
    )


def sample_layers() -> VectorLayers:
    return VectorLayers(
        patches=[polygon_layer("patches", "INTERP_ALT", square(0.30, 0.30))],
        airports=[
            polygon_layer("runways", "RUNWAY", square(0.10, 0.10)),
            polygon_layer("hangars", "HANGAR", square(0.15, 0.15)),
        ],
        roads=[polygon_layer("roads", "INTERP_ALT", square(0.40, 0.40))],
        coastline=[line_layer("coastline", "SEA", LineString([(0.0, 0.6), (1.0, 0.65)]))],
        water=[polygon_layer("water", "WATER", square(0.70, 0.70), square(0.85, 0.85))],
    )


# ----------------------------------------------------------------------------- order
def test_pass_order_is_the_one_of_ortho4xp() -> None:
    result = assemble_vectors(sample_layers(), TILE, FakeDem(), SMALL)
    assert [layer["name"] for layer in result.stats["layers"]] == [
        "patches",
        "runways",
        "hangars",
        "roads",
        "coastline",
        "water",
        "grid_vertical",
        "grid_horizontal",
        "border",
    ]


def test_patches_come_before_the_airports() -> None:
    ordered = sample_layers().ordered()
    assert ordered[0].name == "patches"
    assert [layer.name for layer in ordered[1:3]] == ["runways", "hangars"]


def test_priority_decides_the_altitude_of_a_crossing() -> None:
    """The pre-existing layer wins: the road's z, not the water's (vectors-pslg.md 2.4)."""
    road = LineString([(0.2, 0.5), (0.8, 0.5)])
    water = LineString([(0.5, 0.2), (0.5, 0.8)])
    layers = VectorLayers(
        roads=[VectorLayer("roads", road, MARKERS["INTERP_ALT"], np.array([10.0, 20.0]))],
        water=[VectorLayer("water", water, MARKERS["WATER"], np.array([-5.0, -5.0]))],
    )
    result = assemble_vectors(layers, TILE, FakeDem(), SMALL)
    crossing = np.flatnonzero(
        (np.abs(result.graph.nodes[:, 0] - 0.5) < 1e-12)
        & (np.abs(result.graph.nodes[:, 1] - 0.5) < 1e-12)
    )
    assert len(crossing) == 1
    assert result.graph.nodes[crossing[0], 2] == pytest.approx(15.0)


# ----------------------------------------------------------------------------- grid, seeds
def test_an_empty_tile_still_gets_its_grid_border_and_default_seed() -> None:
    result = assemble_vectors(VectorLayers(), TILE, FakeDem(), SMALL)
    assert [layer["name"] for layer in result.stats["layers"]] == [
        "grid_vertical",
        "grid_horizontal",
        "border",
    ]
    assert result.stats["seeds_defaulted"] is True
    assert np.array_equal(result.seeds[MARKERS["SEA"]], [[1000.0, 1000.0]])
    assert set(np.unique(result.graph.markers)) == {MARKERS["DUMMY"]}


def test_a_flat_tile_at_sea_level_seeds_at_the_centre() -> None:
    result = assemble_vectors(VectorLayers(), TILE, FakeDem(maximum=0.0), SMALL)
    assert np.array_equal(result.seeds[MARKERS["SEA"]], [[0.5, 0.5]])


def test_seeds_follow_the_layer_order_and_the_marker_order() -> None:
    result = assemble_vectors(sample_layers(), TILE, FakeDem(), SMALL)
    assert result.stats["seeds_by_marker"] == {
        "WATER": 2,
        "INTERP_ALT": 2,
        "RUNWAY": 1,
        "HANGAR": 1,
    }
    rows = seed_rows(result.seeds)
    assert np.array_equal(rows[:, 2], [1, 1, 8, 8, 16, 128])
    # the two INTERP_ALT seeds are the patch first, then the road (layer order)
    assert rows[2, 0] < rows[3, 0]


def test_the_grid_altitudes_come_from_the_elevation() -> None:
    result = assemble_vectors(VectorLayers(), TILE, FakeDem(), SMALL)
    nodes = result.graph.nodes
    assert np.allclose(nodes[:, 2], 100.0 * nodes[:, 0] + nodes[:, 1], atol=1e-6)


# ----------------------------------------------------------------------------- artefact
def test_the_written_files_are_the_graph_that_was_assembled(tmp_path: Path) -> None:
    result = assemble_vectors(sample_layers(), TILE, FakeDem(), SMALL, out_dir=tmp_path)
    nodes = read_node_file(tmp_path / node_file_name(TILE))
    poly = read_poly_file(tmp_path / poly_file_name(TILE), nodes.first_index)
    assert len(nodes.xy) == len(result.graph.nodes)
    assert np.allclose(nodes.xy, result.graph.nodes[:, :2], atol=5e-10)
    assert np.array_equal(poly.segments, result.graph.edges)
    assert np.array_equal(poly.markers, result.graph.markers)
    assert len(poly.holes) == 0
    assert np.allclose(poly.seeds, seed_rows(result.seeds))
    stats = json.loads((tmp_path / STATS_JSON).read_text())
    assert stats["tile"] == "+43+005" and stats["nodes"] == len(nodes.xy)
    assert stats["edges_by_marker"]["SEA"] > 0


def test_the_replay_file_holds_the_segments_that_were_noded(tmp_path: Path) -> None:
    result = assemble_vectors(sample_layers(), TILE, FakeDem(), SMALL, out_dir=tmp_path)
    data = np.load(tmp_path / LAYERS_NPZ)
    coords, offsets, markers, owner = (
        data["coords"],
        data["offsets"],
        data["markers"],
        data["layer"],
    )
    assert list(data["names"]) == [layer.name for layer in result.layers]
    replayed = []
    for index in range(len(result.layers)):
        parts = np.flatnonzero(owner == index)
        lines = [coords[offsets[p] : offsets[p + 1]] for p in parts]
        geometry = MultiLineString([LineString(line[:, :2]) for line in lines])
        z = np.concatenate([line[:, 2] for line in lines])
        replayed.append((geometry, int(markers[parts[0]]), z))
    again = node_layers(replayed)
    assert np.array_equal(again.nodes, result.graph.nodes)
    assert np.array_equal(again.edges, result.graph.edges)
    assert np.array_equal(again.markers, result.graph.markers)


def test_airport_bounds_are_published_for_the_mesh(tmp_path: Path) -> None:
    layers = VectorLayers(airport_bounds=np.array([[0.1, 0.1, 0.2, 0.25]]))
    assemble_vectors(layers, TILE, FakeDem(), SMALL, out_dir=tmp_path)
    payload = json.loads((tmp_path / "airports.json").read_text())
    assert payload == {"airports": [{"bounds": [0.1, 0.1, 0.2, 0.25]}]}


def test_no_airports_json_when_there_is_no_airport(tmp_path: Path) -> None:
    assemble_vectors(VectorLayers(), TILE, FakeDem(), SMALL, out_dir=tmp_path)
    assert not (tmp_path / "airports.json").exists()


def test_the_result_is_a_planar_graph() -> None:
    params = AssemblyParams(mesh_zl=14, border_segments=8, check_planar=True)
    result = assemble_vectors(sample_layers(), TILE, FakeDem(), params)
    assert result.stats["planarity_violations"] == 0
    assert check_planar(result.graph.nodes, result.graph.edges) == 0


def test_an_unnamed_marker_is_refused_at_the_layer() -> None:
    with pytest.raises(ValueError, match="no attribute name"):
        VectorLayer("bad", LineString([(0, 0), (1, 1)]), 3)


# ----------------------------------------------------------------------------- rule
def test_the_rule_declares_what_it_consumes_and_nothing_else() -> None:
    assert (VECTORS.name, VECTORS.version, VECTORS.kind) == ("orthostudio.vectors", 1, "dir")
    assert VECTORS.inputs == ("airports", "dem", "osm", "patches")
    assert set(VECTORS.consumed) == {
        "tile",
        "road_level",
        "road_banking_limit",
        "lane_width",
        "max_levelled_segs",
        "water_simplification",
        "min_area",
        "max_area",
        "clean_bad_geometries",
        "mesh_zl",
        # wave 2: the airport smoothing rewrites Data<tile>.alt, and the exact grid order
        # changes the PSLG (``airports-integration.md`` 5)
        "apt_smoothing_pix",
        "exact_grid_order",
    }


def test_an_unconsumed_configuration_field_is_ignored() -> None:
    params = VECTORS.bind({"tile": "+43+005", "mesh_zl": 18, "cover_zl": 17, "min_angle": 10})
    assert isinstance(params, VectorsParams)
    assert (params.tile, params.mesh_zl) == ("+43+005", 18)
    assert params.assembly().mesh_zl == 18


def _dem() -> Dem:
    return Dem(
        tile=TILE,
        alt_dem=np.full((4, 4), 120.0, dtype=np.float32),
        x0=-0.01,
        y0=-0.01,
        x1=1.01,
        y1=1.01,
        nxdem=4,
        nydem=4,
    )


def _dem_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "dem"
    _dem().save(directory)
    return directory


def _context(tmp_path: Path, dem: Path) -> RunContext:
    out = tmp_path / "out"
    scratch = tmp_path / "scratch"
    out.mkdir()
    scratch.mkdir()
    osm = tmp_path / "osm"
    osm.mkdir()
    return RunContext(
        rule=VECTORS,
        key="0" * 64,
        params=VectorsParams(tile=TILE.name, mesh_zl=14),
        inputs={
            "osm": ResolvedInput("osm", "a" * 64, osm),
            "dem": ResolvedInput("dem", "b" * 64, dem),
            "patches": ResolvedInput("patches", None, None),
            "airports": ResolvedInput("airports", None, None),
        },
        out=out,
        scratch=scratch,
    )


def test_the_rule_assembles_the_layers_it_is_given(tmp_path: Path) -> None:
    seen: list[LayerRequest] = []

    def builder(request: LayerRequest) -> LayerBuild:
        seen.append(request)
        # wave 2: a builder hands back the elevation every family sampled, because the
        # airports smoothed it (``airports-integration.md`` R-I1)
        return LayerBuild(layers=sample_layers(), dem=_dem())

    ctx = _context(tmp_path, _dem_dir(tmp_path))
    with vectors_job(VectorsJob(build_layers=builder)):
        result = run_vectors(ctx)
    assert len(seen) == 1
    assert seen[0].tile == TILE and seen[0].airports is None and seen[0].patches is None
    assert seen[0].params.mesh_zl == 14
    assert (ctx.out / node_file_name(TILE)).is_file()
    assert (ctx.out / poly_file_name(TILE)).is_file()
    assert (ctx.out / LAYERS_NPZ).is_file() and (ctx.out / STATS_JSON).is_file()
    assert result.stats["seeds"] == 6


def test_without_an_injected_builder_the_rule_resolves_the_wired_one(tmp_path: Path) -> None:
    """P4 integration: ``LAYER_BUILDER`` now resolves, so the refusal moves to the input.

    Before the wiring this asserted that ``orthostudio.vectors.layers`` was missing. The module
    exists, so the lazy resolution succeeds and the first thing that can go wrong is the OSM
    input: an empty directory is neither a snapshot store nor an Ortho4XP cache, and the error
    says so instead of silently building a tile with no vector at all.
    """
    ctx = _context(tmp_path, _dem_dir(tmp_path))
    with pytest.raises(OsxpError) as excinfo, vectors_job(VectorsJob()):
        run_vectors(ctx)
    assert excinfo.value.code == "OSM_LAYER_UNAVAILABLE"
    assert "snapshot" in str(excinfo.value)


def test_a_dem_that_is_not_an_osxp_artefact_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-dem"
    empty.mkdir()
    ctx = _context(tmp_path, empty)
    with pytest.raises(OsxpError) as excinfo:
        run_vectors(ctx)
    assert excinfo.value.code == "DEM_FILE_UNREADABLE"


# ----------------------------------------------------------------------------- builder adapter
def test_a_builder_result_becomes_ordered_passes() -> None:
    """What ``coast``/``water``/``roads``/``patches`` return: triples plus a seed map."""
    lake = square(0.2, 0.2)
    sea = square(0.6, 0.6)
    layers = [
        (MultiPolygon([lake]), MARKERS["WATER"], np.zeros(5)),
        (MultiPolygon([sea]), MARKERS["SEA_EQUIV"], np.zeros(5)),
    ]
    seeds = {"WATER": np.array([[0.25, 0.25]]), "SEA_EQUIV": np.array([[0.65, 0.65]])}
    passes = to_vector_layers("water", layers, seeds)
    assert [p.name for p in passes] == ["water_0", "water_1"]
    assert [p.marker for p in passes] == [MARKERS["WATER"], MARKERS["SEA_EQUIV"]]
    assert np.array_equal(passes[0].seeds, [[0.25, 0.25]])
    assert np.array_equal(passes[1].seeds, [[0.65, 0.65]])


def test_a_single_pass_keeps_the_family_name() -> None:
    passes = to_vector_layers("roads", [(MultiPolygon([square(0.1, 0.1)]), 8, None)])
    assert [p.name for p in passes] == ["roads"] and passes[0].seeds is None


def test_seeds_of_one_marker_go_to_its_first_pass() -> None:
    layers = [
        (MultiPolygon([square(0.1, 0.1)]), MARKERS["INTERP_ALT"], np.zeros(5)),
        (MultiPolygon([square(0.3, 0.3)]), MARKERS["INTERP_ALT"], np.zeros(5)),
    ]
    passes = to_vector_layers("patches", layers, {"INTERP_ALT": np.array([[0.15, 0.15]])})
    assert len(passes[0].seeds) == 1 and passes[1].seeds is None


def test_a_seed_no_layer_carries_is_refused() -> None:
    with pytest.raises(ValueError, match="no layer carries"):
        to_vector_layers("water", [], {"WATER": np.array([[0.5, 0.5]])})


def test_the_wave_1_builders_return_that_shape() -> None:
    """Guard on the contract the four layer builders and this module share."""
    modules = (
        pytest.importorskip("orthostudio.vectors.water"),
        pytest.importorskip("orthostudio.vectors.roads"),
    )
    for module, name in zip(modules, ("WaterResult", "RoadResult"), strict=True):
        result = getattr(module, name)()
        assert isinstance(result.layers, tuple)
        assert isinstance(result.seeds, dict)
        assert to_vector_layers(name, result.layers, result.seeds) == []
