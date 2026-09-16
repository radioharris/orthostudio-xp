"""Review 5 (fidelity lens): what the native vector stage *reads* and what it *declares*.

Everything here runs in a second and touches no network. It checks the two halves of the
cache-key contract -- "the rule declares exactly the parameters it consumes" and "everything
it reads is either a parameter or a keyed input" -- and it re-derives, from the formulas
transcribed in the tests themselves, the arithmetic the stage inherits (orthogrid, gluing border,
default seed, seed section).

Tests marked ``xfail(strict=True)`` state the behaviour the fidelity target requires and that
the code does **not** have yet: they are the review's findings, expressed so that fixing the
defect turns them red-as-XPASS instead of leaving them silent.
"""

from __future__ import annotations

from math import atan, cos, exp, floor, pi
from pathlib import Path

import numpy as np
import pytest

from orthostudio.dem.dem import Dem
from orthostudio.graph import key_for
from orthostudio.model import TileRef
from orthostudio.sources.osm import LAYERS, OsmSnapshot, SnapshotStore
from orthostudio.vectors.grid import BORDER_SEGMENTS, gluing_border, ortho_grid_abscissae
from orthostudio.vectors.layers import build_layers
from orthostudio.vectors.noding import MARKERS
from orthostudio.vectors.rule import VECTORS, LayerRequest, VectorsParams
from orthostudio.vectors.seeds import default_seeds
from orthostudio.vectors.triangle_files import write_poly_file

TILE = TileRef(43, 5)


# -- 1. the rule declares exactly what it consumes ------------------------------------------


STAGE_1_VARIABLES = frozenset(
    {
        "apt_smoothing_pix", "road_level", "road_banking_limit", "lane_width",
        "max_levelled_segs", "water_simplification", "min_area", "max_area",
        "clean_bad_geometries", "mesh_zl", "custom_dem", "fill_nodata",
    }
)  # fmt: skip
"""The tile variables Ortho4XP's ``build_poly_file`` reads (``O4_Vector_Map.py:19-178``,
``O4_Airport_Utils.py``)."""


def test_the_parameters_are_ortho4xps_stage_1_minus_two_documented_absences() -> None:
    """``VectorsParams`` must track the variables Ortho4XP's stage 1 reads.

    The two absences are the ones the rule's docstring justifies: ``custom_dem`` / ``fill_nodata``
    enter through the ``orthostudio.dem@1`` input, which is stronger. The one field the native
    side has and Ortho4XP has not is ``exact_grid_order``, a fidelity switch of the noder
    (``airports-integration.md`` 5), besides the ``tile`` every stage keys on. Any *other*
    divergence is a silently wrong cache, so this test is the alarm for a parameter added on one
    side only.
    """
    native = set(VectorsParams.model_fields)
    assert STAGE_1_VARIABLES - native == {"custom_dem", "fill_nodata"}
    assert native - STAGE_1_VARIABLES == {"exact_grid_order", "tile"}


def test_every_declared_parameter_changes_the_artefact_key() -> None:
    """A parameter that does not enter the key is a parameter the store cannot tell apart."""
    inputs = {"osm": "a" * 64, "dem": "b" * 64, "patches": None, "airports": None}
    base = VectorsParams(tile=TILE.name)
    reference = key_for(VECTORS, base, inputs)
    changed = {
        "tile": "+44+005",
        "road_level": 3,
        "road_banking_limit": 0.75,
        "lane_width": 6.0,
        "max_levelled_segs": 1000,
        "water_simplification": 2.0,
        "min_area": 0.5,
        "max_area": 50.0,
        "clean_bad_geometries": False,
        "mesh_zl": 17,
        "apt_smoothing_pix": 4,
        "exact_grid_order": True,
    }
    assert set(changed) == set(VectorsParams.model_fields)
    for name, value in changed.items():
        other = base.model_copy(update={name: value})
        assert key_for(VECTORS, other, inputs) != reference, name


def test_every_input_of_the_rule_changes_the_artefact_key() -> None:
    """``patches`` and ``airports`` are optional, not decorative: present must differ absent."""
    inputs: dict[str, str | None] = {
        "osm": "a" * 64,
        "dem": "b" * 64,
        "patches": None,
        "airports": None,
    }
    params = VectorsParams(tile=TILE.name)
    reference = key_for(VECTORS, params, inputs)
    for name in ("osm", "dem", "patches", "airports"):
        assert key_for(VECTORS, params, inputs | {name: "c" * 64}) != reference, name


# -- 2. a patch folder reaches the layers ----------------------------------------------------


def _patch_file(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "review5.patch.osm").write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n<osm version='0.6' generator='JOSM'>\n"
        "  <node id='-1' lat='43.2' lon='5.2' />\n"
        "  <node id='-2' lat='43.2' lon='5.3' />\n"
        "  <node id='-3' lat='43.3' lon='5.3' />\n"
        "  <node id='-4' lat='43.3' lon='5.2' />\n"
        "  <way id='-1'>\n"
        "    <nd ref='-1'/>\n    <nd ref='-2'/>\n    <nd ref='-3'/>\n"
        "    <nd ref='-4'/>\n    <nd ref='-1'/>\n"
        "    <tag k='cst_alt_abs' v='42'/>\n"
        "  </way>\n</osm>",
        encoding="utf-8",
    )


def _empty_snapshot(layer: str) -> OsmSnapshot:
    return OsmSnapshot(
        tile=TILE, layer=layer, selectors=LAYERS[layer].selectors, query="q", mirror="test",
        fetched_at="2026-09-12T00:00:00Z", generator="test", osm_base="2026-09-12T00:00:00Z",
        nodes=(), ways=(), relations=(), digest="0" * 64,
    )  # fmt: skip


def _flat_raster() -> Dem:
    """What ``build_layers`` takes since wave 2: a raster and its window, not a sampler.

    The airport smoothing rebuilds the elevation over the aerodromes and every family samples
    the result, so the stage needs an ``orthostudio.dem@1`` artefact
    (``docs/specs/airports-integration.md`` R-I1).
    """
    return Dem(
        tile=TILE,
        alt_dem=np.full((16, 16), 100.0, dtype=np.float32),
        x0=-0.01,
        y0=-0.01,
        x1=1.01,
        y1=1.01,
        nxdem=16,
        nydem=16,
    )


def test_a_patch_of_the_tile_reaches_the_layers(tmp_path: Path) -> None:
    """The patch folder given to the stage becomes an ``INTERP_ALT`` layer.

    Was ``xfail(strict=True)``: ``build_layers`` appended ``Patches/<tile>`` to a folder that was
    already the tile's, and every patch was dropped in silence. The stage now reads the folder it
    is given as it is.
    """
    _patch_file(tmp_path / "patches")
    osm = tmp_path / "osm"
    for layer in ("coastline", "water", "airports"):
        SnapshotStore(osm).save(_empty_snapshot(layer))
    request = LayerRequest(
        tile=TILE,
        params=VectorsParams(tile=TILE.name, road_level=0),
        osm=osm,
        dem=_flat_raster(),
        patches=tmp_path / "patches",
    )
    built = build_layers(request)
    assert len(built.layers.patches) == 1


# -- 3. the arithmetic the stage inherits ----------------------------------------------------


def _reference_grid(lat: int, lon: int, mesh_zl: int) -> tuple[list[float], list[float]]:
    """``O4_Vector_Map.py:98-117`` transcribed literally, with Ortho4XP's own conversions."""

    def wgs84_to_orthogrid(lat_: float, lon_: float, zl: int) -> tuple[int, int]:
        ratio_x = lon_ / 180
        ratio_y = np.log(np.tan((90 + lat_) * pi / 360)) / pi
        mult = 2 ** (zl - 5)
        return (int((ratio_x + 1) * mult) * 16, int((1 - ratio_y) * mult) * 16)

    til_xul, til_yul = wgs84_to_orthogrid(lat + 1, lon, mesh_zl)
    til_xlr, til_ylr = wgs84_to_orthogrid(lat, lon + 1, mesh_zl)
    xgrid, ygrid = {0.0, 1.0}, {0.0, 1.0}
    for til_x in range(til_xul + 16, til_xlr + 1, 16):
        pos_x = til_x / (2 ** (mesh_zl - 1)) - 1
        xgrid.add(pos_x * 180 - lon)
    for til_y in range(til_yul + 16, til_ylr + 1, 16):
        pos_y = 1 - til_y / (2 ** (mesh_zl - 1))
        ygrid.add(360 / pi * atan(exp(pi * pos_y)) - 90 - lat)
    return sorted(xgrid), sorted(ygrid)


@pytest.mark.parametrize("mesh_zl", [15, 16, 17, 18, 19])
@pytest.mark.parametrize("tile", [TileRef(43, 5), TileRef(-23, -47), TileRef(60, 11)])
def test_the_orthogrid_abscissae_are_bit_for_bit_ortho4xps(tile: TileRef, mesh_zl: int) -> None:
    """The grid decides where DUMMY edges cut the tile; an ULP here moves a node key.

    ``+43+005`` at ``mesh_zl=19`` was the only combination the reference build exercised, so the
    southern hemisphere and the other zoom levels are checked against the formula itself.
    """
    ours_x, ours_y = ortho_grid_abscissae(tile, mesh_zl)
    ref_x, ref_y = _reference_grid(tile.lat, tile.lon, mesh_zl)
    assert len(ours_x) == len(ref_x) and len(ours_y) == len(ref_y)
    for a, b in zip(ours_x, ref_x, strict=True):
        assert a == b, (tile.name, mesh_zl, a, b)
    for a, b in zip(ours_y, ref_y, strict=True):
        assert a == b, (tile.name, mesh_zl, a, b)


def test_the_gluing_border_is_2048_exact_segments_in_ortho4xps_order() -> None:
    """``O4_Vector_Map.py:136-152``: south, north, west, east, abscissae ``k / 2048``."""
    assert BORDER_SEGMENTS == 2048
    border = gluing_border()
    assert len(border.geoms) == 4
    steps = np.arange(0, 2049) / 2048
    expected = [
        np.column_stack([steps, np.zeros(2049)]),
        np.column_stack([steps, np.ones(2049)]),
        np.column_stack([np.zeros(2049), steps]),
        np.column_stack([np.ones(2049), steps]),
    ]
    for line, want in zip(border.geoms, expected, strict=True):
        got = np.asarray(line.coords)
        assert got.shape == want.shape
        assert (got == want).all()  # exact: a power of two is exact in binary
    # every abscissa survives the 9-decimal snap as a distinct key
    assert len(np.unique(np.round(steps, 9))) == 2049


def test_the_default_seed_is_ortho4xps_two_cases() -> None:
    """``O4_Vector_Map.py:160-166``: outside the tile with relief, at its centre without."""
    assert list(default_seeds(1.0)[MARKERS["SEA"]][0]) == [1000.0, 1000.0]
    assert list(default_seeds(0.999)[MARKERS["SEA"]][0]) == [0.5, 0.5]
    assert list(default_seeds(1261.0)[MARKERS["SEA"]][0]) == [1000.0, 1000.0]


def test_the_seed_section_is_sorted_by_attribute_value_and_written_to_15_decimals(
    tmp_path: Path,
) -> None:
    """``O4_Vector_Utils.py:596-615``: ``sorted(dico_attributes, key=value)``, stable inside."""
    path = tmp_path / "x.poly"
    write_poly_file(
        path,
        np.zeros((0, 2), dtype=np.int64),
        np.zeros(0, dtype=np.uint8),
        seeds={
            MARKERS["INTERP_ALT"]: np.array([[0.3, 0.3], [0.1, 0.1]]),
            MARKERS["SEA"]: np.array([[0.2, 0.2]]),
            MARKERS["WATER"]: np.array([[0.4, 0.4]]),
        },
    )
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    tail = lines[lines.index("4") :]
    assert tail == [
        "4",
        "1 0.400000000000000 0.400000000000000 1",
        "2 0.200000000000000 0.200000000000000 2",
        "3 0.300000000000000 0.300000000000000 8",
        "4 0.100000000000000 0.100000000000000 8",
    ]


def test_the_attribute_bits_are_ortho4xps_dictionary() -> None:
    """``O4_Vector_Utils.py:43-53``; a shifted bit silently repaints a whole terrain class."""
    assert MARKERS == {
        "DUMMY": 0,
        "WATER": 1,
        "SEA": 2,
        "SEA_EQUIV": 4,
        "INTERP_ALT": 8,
        "RUNWAY": 16,
        "TAXIWAY": 32,
        "APRON": 64,
        "HANGAR": 128,
    }


def test_the_folder_of_a_tile_is_ortho4xps_round_latlon() -> None:
    """``TileRef.folder`` must be ``floor(lat/10)*10`` -- the snapshot layout depends on it."""
    for lat, lon in ((43, 5), (-23, -47), (-1, -1), (0, 0), (60, 11)):
        cell_lat = floor(lat / 10) * 10
        cell_lon = floor(lon / 10) * 10
        expect = f"{cell_lat:+.0f}".zfill(3) + f"{cell_lon:+.0f}".zfill(4)
        assert TileRef(lat, lon).folder == expect


def test_the_anisotropy_factor_is_the_one_ortho4xp_sets_for_the_whole_stage() -> None:
    """``O4_Vector_Map.py:26``: ``VECT.scalx = cos((lat + 0.5) * pi / 180)``."""
    from orthostudio.vectors.water import scale_x

    for lat in (43, -23, 60, 0):
        assert scale_x(TileRef(lat, 5)) == cos((lat + 0.5) * pi / 180)
