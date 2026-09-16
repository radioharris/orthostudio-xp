"""``orthostudio.vectors@1`` after wave 2: the cache key, the artefact layout, the stage selection.

Rule R-I6 of ``docs/specs/airports-integration.md``: *a consumed parameter that is not
declared is a silently wrong cache.* The first test below is the one that enforces it -- it
moves every declared field in turn and asserts the key moves -- and the second shows why it
matters, by changing ``apt_smoothing_pix`` and watching the elevation raster change.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from shapely import geometry

from orthostudio.airports_vec.model import Airport, AirportSet, SurfaceAreas
from orthostudio.airports_vec.smoothing import smooth_dem_over_airports
from orthostudio.airports_vec.stage import AirportParams, require_dem
from orthostudio.dem.dem import Dem
from orthostudio.errors import OsxpError
from orthostudio.graph.keys import key_for
from orthostudio.model import TileRef
from orthostudio.pipeline.native import native_reasons, resolve_stages
from orthostudio.vectors.rule import DEM_JSON, VECTORS, LayerBuild, VectorsParams

TILE = TileRef(43, 5)
TRIANGLE = Path(__file__).resolve().parents[1] / "native" / "triangle4xp" / "build" / "Triangle4XP"


def _moved(name: str, value: object) -> bool:
    inputs = {"osm": "a" * 64, "dem": "b" * 64, "patches": None, "airports": None}
    base = VectorsParams(tile=TILE.name)
    other = base.model_copy(update={name: value})
    return key_for(VECTORS, base, inputs)[0] != key_for(VECTORS, other, inputs)[0]


def test_every_consumed_parameter_is_in_the_key() -> None:
    """R-I6: change any declared field and the artefact address must move."""
    probes: dict[str, object] = {
        "tile": "+44+005",
        "road_level": 2,
        "road_banking_limit": 0.7,
        "lane_width": 5.0,
        "max_levelled_segs": 1000,
        "water_simplification": 1.0,
        "min_area": 0.01,
        "max_area": 100.0,
        "clean_bad_geometries": False,
        "mesh_zl": 18,
        "apt_smoothing_pix": 4,
        "exact_grid_order": True,
    }
    assert set(probes) == set(VectorsParams.model_fields), "a field was added without a probe"
    for name, value in probes.items():
        assert _moved(name, value), f"{name} does not enter the key"


def test_the_inputs_are_in_the_key_too() -> None:
    params = VectorsParams(tile=TILE.name)
    base = {"osm": "a" * 64, "dem": "b" * 64, "patches": None, "airports": None}
    key = key_for(VECTORS, params, base)[0]
    assert key != key_for(VECTORS, params, {**base, "osm": "c" * 64})[0]
    assert key != key_for(VECTORS, params, {**base, "dem": "c" * 64})[0]
    assert key != key_for(VECTORS, params, {**base, "patches": "c" * 64})[0]


def test_an_unknown_field_is_refused() -> None:
    """``extra="forbid"``: a typo must not silently leave the stage unconfigured."""
    with pytest.raises(ValidationError):
        VectorsParams(tile=TILE.name, apt_smooting_pix=4)


@pytest.mark.parametrize("value", [-1, 10_000])
def test_an_absurd_smoothing_width_is_refused(value: int) -> None:
    with pytest.raises(OsxpError) as excinfo:
        VectorsParams(tile=TILE.name, apt_smoothing_pix=value)
    assert excinfo.value.code == "CFG_VALUE_INVALID"


# -- why the parameter has to be in the key ------------------------------------------------


def _dem(side: int = 64) -> Dem:
    rng = np.random.default_rng(7)
    return Dem(
        tile=TILE,
        alt_dem=(100 * rng.random((side, side))).astype(np.float32),
        x0=0.0,
        y0=0.0,
        x1=1.0,
        y1=1.0,
        nxdem=side,
        nydem=side,
    )


def _one_airport() -> AirportSet:
    square = geometry.MultiPolygon([geometry.box(0.4, 0.4, 0.6, 0.6)])
    return AirportSet(
        {
            "LFXX": Airport(
                key="LFXX",
                key_type="icao",
                name="x",
                repr_node=(5.5, 43.5),
                boundary=square,
                areas=SurfaceAreas(runway=square, taxiway=square, apron=square, hangar=square),
            )
        }
    )


def test_apt_smoothing_pix_changes_the_raster() -> None:
    """The parameter the key must carry: it rewrites ``Data<tile>.alt``."""
    dem = _dem()
    airports = _one_airport()
    eight = smooth_dem_over_airports(dem, list(airports), AirportParams(8).smoothing())
    four = smooth_dem_over_airports(dem, list(airports), AirportParams(4).smoothing())
    zero = smooth_dem_over_airports(dem, list(airports), AirportParams(0).smoothing())
    assert not np.array_equal(eight.alt_dem, four.alt_dem)
    assert not np.array_equal(eight.alt_dem, dem.alt_dem)
    assert zero.alt_dem is dem.alt_dem, "0 disables the stage entirely (no border re-blend)"
    # the artefact is a copy: the input raster is never mutated (it is memory-mapped)
    assert np.array_equal(dem.alt_dem, _dem().alt_dem)


def test_the_elevation_must_be_a_dem_and_says_so() -> None:
    with pytest.raises(OsxpError) as excinfo:
        require_dem(object(), TILE)  # type: ignore[arg-type]
    assert excinfo.value.code == "DEM_FILE_UNREADABLE"
    dem = _dem()
    assert require_dem(dem, TILE) is dem


# -- the artefact ---------------------------------------------------------------------------


def test_the_artefact_publishes_the_smoothed_raster_and_the_two_records(tmp_path: Path) -> None:
    from orthostudio.mesh.build import DemSpec
    from orthostudio.vectors.assemble import VectorLayers
    from orthostudio.vectors.rule import write_elevation_and_airports

    dem = _dem()
    airports = _one_airport()
    write_elevation_and_airports(
        tmp_path, TILE, LayerBuild(layers=VectorLayers(), dem=dem, airports=airports)
    )
    names = {path.name for path in tmp_path.iterdir()}
    assert names == {
        f"Data{TILE.name}.alt",
        DEM_JSON,
        "airports.npz",
        "airports.wkb.json",
    }
    assert not any(path.name.endswith(".part") for path in tmp_path.iterdir())
    raster = np.fromfile(tmp_path / f"Data{TILE.name}.alt", dtype=np.float32)
    assert np.array_equal(raster.reshape(dem.nydem, dem.nxdem), dem.alt_dem)
    # the mesh stage can take this directory as its ``dem`` input
    spec = DemSpec.from_dir(tmp_path, TILE)
    assert (spec.nxdem, spec.nydem) == (dem.nxdem, dem.nydem)
    assert spec.alt_path == tmp_path / f"Data{TILE.name}.alt"
    spec.check(TILE.name)


# -- stage selection -------------------------------------------------------------------------


def _cfg(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "iterate": 0,
        "masks_use_DEM_too": False,
        "masks_custom_extent": "",
        "cover_airports_with_highres": "False",
        "road_level": 1,
    }
    return base | over


needs_triangle = pytest.mark.skipif(
    not TRIANGLE.is_file(), reason="Triangle4XP not built (native/triangle4xp/build)"
)


@needs_triangle
def test_the_airport_cover_is_built_by_the_stages() -> None:
    """``cover_airports_with_highres`` reads the airport record the vector stage publishes."""
    assert "vectors" not in native_reasons(_cfg(cover_airports_with_highres="True"))
    resolve_stages(_cfg(cover_airports_with_highres="ICAO"), triangle=TRIANGLE)


@needs_triangle
def test_the_default_elevation_of_the_mesh_is_the_vector_stage() -> None:
    """``--dem vectors``: the mesh reads the raster the vector stage smoothed over the airports."""
    choice = resolve_stages(_cfg(), triangle=TRIANGLE)
    assert choice.dem == "vectors" and choice.to_dict() == {"dem": "vectors"}
    assert resolve_stages(_cfg(), dem="native", triangle=TRIANGLE).dem == "native"
