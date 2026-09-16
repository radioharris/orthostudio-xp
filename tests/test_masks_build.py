"""Building a whole tile of masks: cells, skipping, index, workers (`orthostudio.masks.build`)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from orthostudio.imagery.grid import tile_to_wgs84
from orthostudio.masks.build import (
    MASKS_INDEX_FORMAT,
    CellJob,
    build_cell,
    build_masks,
    mask_file_name,
    worker_count,
)
from orthostudio.masks.profiles import halo_px, sea_level_for
from orthostudio.masks.raster import CELL_PX, custom_pre_mask, extent_polygons, pre_mask
from orthostudio.masks.water import WaterTriangles, mask_cells, water_triangles
from orthostudio.mesh.mesh_file import MeshData
from orthostudio.model import TileRef

TILE = TileRef(43, 5)
ZL = 14


def _sea_mesh(cells: list[tuple[int, int]]) -> MeshData:
    """A mesh whose water triangles cover the middle half of each given cell.

    The middle half keeps every barycentre in an inner quarter, so Ortho4XP's duplication into
    the neighbouring cells does not fire and each cell is tested on its own.
    """
    vertices: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    for til_x, til_y in cells:
        lat0, lon0 = tile_to_wgs84(til_x + 4, til_y + 4, ZL)
        lat1, lon1 = tile_to_wgs84(til_x + 12, til_y + 12, ZL)
        base = len(vertices)
        vertices += [(lon0, lat0, 0.0), (lon1, lat0, 0.0), (lon1, lat1, 0.0), (lon0, lat1, 0.0)]
        tris += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    return MeshData(
        vertices=np.array(vertices, dtype=np.float64),
        normals=np.zeros((len(vertices), 2), dtype=np.float32),
        tris=np.array(tris, dtype=np.int32),
        tri_attr=np.full(len(tris), 2, dtype=np.uint8),
    )


def _job(til_x: int, til_y: int, tris: WaterTriangles, **kw: object) -> CellJob:
    defaults: dict[str, object] = {
        "mask_zl": ZL,
        "masking_mode": "sand",
        "masks_width": 100,
        "sea_level": sea_level_for(0.25),
        "margin": halo_px("sand", 100, TILE.lat, ZL),
        "lat": TILE.lat,
    }
    defaults.update(kw)
    return CellJob(til_x, til_y, tris, extent_polygons([TILE], ZL), **defaults)  # type: ignore[arg-type]


def test_mask_file_names() -> None:
    assert mask_file_name(8432, 6000) == "6000_8432.png"
    assert mask_file_name(8432, 6000, distance=True) == "6000_8432_dist.png"


def test_worker_count_is_bounded_by_the_cells() -> None:
    assert worker_count(3, 8) == 3
    assert worker_count(0, 8) == 1
    assert worker_count(10, 2) == 2
    assert worker_count(10, 0) >= 1


def test_a_cell_without_water_is_not_written(tmp_path: Path) -> None:
    empty = WaterTriangles(np.zeros((0, 3, 2)), np.zeros((0, 3, 2)))
    result = build_cell(_job(8432, 6000, empty))
    assert result.mask is None and not result.written


def test_a_cell_covered_by_water_everywhere_is_not_written() -> None:
    """A uniformly black blurred mask is dropped, exactly as in Ortho4XP (``:168``)."""
    mesh = _sea_mesh([(8432, 6000)])
    by_cell = water_triangles({TILE: mesh}, TILE, ZL)
    # no extent polygon at all: the pre-mask is pure sea
    job = CellJob(
        8432,
        6000,
        by_cell[(8432, 6000)],
        [],
        ZL,
        "sand",
        100,
        sea_level_for(0.25),
        halo_px("sand", 100, TILE.lat, ZL),
        lat=TILE.lat,
    )
    assert build_cell(job).mask is None


def test_a_real_cell_is_written_and_has_a_transition() -> None:
    mesh = _sea_mesh([(8432, 6000)])
    by_cell = water_triangles({TILE: mesh}, TILE, ZL)
    result = build_cell(_job(8432, 6000, by_cell[(8432, 6000)]))
    assert result.mask is not None
    assert result.mask.shape == (CELL_PX, CELL_PX)
    assert result.mask.min() == 0 and result.mask.max() == 255
    values = set(np.unique(result.mask).tolist())
    assert len(values) > 2  # a real transition band, not a hard edge


def test_build_masks_writes_pngs_and_an_index(tmp_path: Path) -> None:
    mesh = _sea_mesh([(8432, 6000), (8448, 6000)])
    index = build_masks(tmp_path, {TILE: mesh}, TILE, workers=1)
    assert index.seconds > 0
    doc = json.loads((tmp_path / "index.json").read_text())
    assert doc["format"] == MASKS_INDEX_FORMAT
    assert doc["tile"] == "+43+005"
    assert doc["mask_zl"] == ZL
    rng = mask_cells(TILE, ZL)
    assert doc["range"]["til_x_min"] == rng.til_x_min
    names = {m["file"] for m in doc["masks"]}
    assert names == {"6000_8432.png", "6000_8448.png"}
    for name in names:
        assert Image.open(tmp_path / name).size == (CELL_PX, CELL_PX)
    assert all(m["dist"] is None for m in doc["masks"])


def test_build_masks_with_distance_masks(tmp_path: Path) -> None:
    mesh = _sea_mesh([(8432, 6000)])
    build_masks(tmp_path, {TILE: mesh}, TILE, workers=1, distance_masks_too=True)
    doc = json.loads((tmp_path / "index.json").read_text())
    assert doc["masks"][0]["dist"] == "6000_8432_dist.png"
    dist = np.array(Image.open(tmp_path / "6000_8432_dist.png"), dtype=np.uint8)
    assert dist.shape == (CELL_PX, CELL_PX)
    assert dist.max() > 0


def test_build_masks_in_a_process_pool_gives_the_same_files(tmp_path: Path) -> None:
    mesh = _sea_mesh([(8432, 6000), (8448, 6000)])
    one = tmp_path / "one"
    many = tmp_path / "many"
    build_masks(one, {TILE: mesh}, TILE, workers=1)
    build_masks(many, {TILE: mesh}, TILE, workers=2)
    for path in sorted(one.glob("*.png")):
        assert path.read_bytes() == (many / path.name).read_bytes()


def test_cells_outside_the_tile_range_are_skipped(tmp_path: Path) -> None:
    """Ortho4XP returns early for a cell of the neighbour grid (``O4_Mask_Utils.py:136-141``)."""
    mesh = _sea_mesh([(8400, 6000)])
    index = build_masks(tmp_path, {TILE: mesh}, TILE, workers=1)
    assert index.masks == []
    assert not list(tmp_path.glob("*.png"))


def test_custom_pre_mask_scales_to_the_inland_grey() -> None:
    coverage = np.full((CELL_PX, CELL_PX), 255, dtype=np.uint8)
    out = custom_pre_mask(coverage, 99)
    assert out.max() == 99
    with pytest.raises(ValueError, match="4096"):
        custom_pre_mask(np.zeros((4, 4), dtype=np.uint8), 99)


def test_dem_pre_mask_is_taken_into_account() -> None:
    """``masks_use_DEM_too`` raises land where the elevation says so (``:146-149``)."""
    mesh = _sea_mesh([(8432, 6000)])
    by_cell = water_triangles({TILE: mesh}, TILE, ZL)
    margin = halo_px("sand", 100, TILE.lat, ZL)
    side = CELL_PX + 2 * margin
    without = build_cell(_job(8432, 6000, by_cell[(8432, 6000)]))
    dem = np.full((side, side), 255, dtype=np.uint8)
    with_dem = build_cell(_job(8432, 6000, by_cell[(8432, 6000)], dem=dem))
    assert without.mask is not None
    assert with_dem.mask is None  # the DEM says "land everywhere": uniform, not written


def test_pre_mask_margin_changes_nothing_inside_the_cell() -> None:
    mesh = _sea_mesh([(8432, 6000)])
    tris = water_triangles({TILE: mesh}, TILE, ZL)[(8432, 6000)]
    extents = extent_polygons([TILE], ZL)
    wide = pre_mask(8432, 6000, tris, extents, mask_zl=ZL, sea_level=99, margin=64)
    tight = pre_mask(8432, 6000, tris, extents, mask_zl=ZL, sea_level=99, margin=16)
    assert np.array_equal(wide[64:-64, 64:-64], tight[16:-16, 16:-16])
