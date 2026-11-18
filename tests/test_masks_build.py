# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Building a whole tile of masks: cells, skipping, index, workers (`orthostudio.masks.build`)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from orthostudio.imagery.grid import texture_at, tile_to_wgs84
from orthostudio.masks.build import (
    MASKS_INDEX_FORMAT,
    CellJob,
    build_cell,
    build_masks,
    mask_file_name,
    worker_count,
)
from orthostudio.masks.profiles import cell_latitude, halo_px, sea_level_for
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


def test_two_rows_of_squares_make_a_soft_mask_they_share_the_same(tmp_path: Path) -> None:
    """A mask cell across the line between two rows of squares is made by the square on each
    side, each from the two meshes: the ``soft`` fade counts its width at the cell, not at the
    square, so both write the very same bytes (a region's zips held 1,947 masks of one name that
    differed, each square counting at its own middle, Europe, 2026-11-17)."""
    south, north = TileRef(43, 5), TileRef(44, 5)
    cell = texture_at(44.0, 5.5, ZL, "")
    straddling = (cell.til_x, cell.til_y)
    above = (cell.til_x, cell.til_y - 16)
    meshes = {south: _sea_mesh([straddling]), north: _sea_mesh([above])}
    for tile in (south, north):
        build_masks(
            tmp_path / tile.name, meshes, tile, masking_mode="soft", masks_width=100, workers=1
        )
    name = mask_file_name(*straddling)
    made = [(tmp_path / tile.name / name).read_bytes() for tile in (south, north)]
    assert made[0] == made[1]
    mask = np.array(Image.open(tmp_path / south.name / name))
    assert len(set(np.unique(mask).tolist())) > 2  # a real band, not a hard edge


def test_our_fade_draws_the_water_as_its_outline_covers_the_pixels() -> None:
    """Pillow fills a triangle up to and with its right and bottom edges, on corners rounded to
    whole pixels: a square of water 4 pixels wide came out 5, the row too many east and south.
    Ortho4XP's fades keep that drawing byte for byte; OrthoStudio XP's own (``exact_edges``, the
    ``soft`` fade) gives that row back to the land, and keeps a stream one pixel wide."""
    from orthostudio.masks.water import cell_pixel_origin

    margin = 16
    px0, py0 = cell_pixel_origin(8432, 6000, ZL, margin)
    x0, y0 = px0 + margin + 300, py0 + margin + 300
    square = [
        [(x0, y0), (x0 + 4, y0), (x0 + 4, y0 + 4)],
        [(x0, y0), (x0 + 4, y0 + 4), (x0, y0 + 4)],
    ]
    sx = x0 + 40  # a stream one pixel wide, from north to south
    stream = [
        [(sx, y0), (sx + 1, y0), (sx + 1, y0 + 30)],
        [(sx, y0), (sx + 1, y0 + 30), (sx, y0 + 30)],
    ]
    tris = WaterTriangles(np.array(square + stream, dtype=np.float64), np.zeros((0, 3, 2)))
    extents = extent_polygons([TILE], ZL)
    o4xp = pre_mask(8432, 6000, tris, extents, mask_zl=ZL, sea_level=99, margin=margin)
    ours = pre_mask(
        8432, 6000, tris, extents, mask_zl=ZL, sea_level=99, margin=margin, exact_edges=True
    )

    def span(mask: np.ndarray, x: int, y: int, w: int, h: int) -> tuple[list[int], list[int]]:
        water = mask[y - py0 : y - py0 + h, x - px0 : x - px0 + w] == 0
        cols = (np.nonzero(water.any(axis=0))[0] + x - x0).tolist()
        rows = (np.nonzero(water.any(axis=1))[0] + y - y0).tolist()
        return cols, rows

    assert span(o4xp, x0 - 2, y0 - 2, 9, 9) == ([0, 1, 2, 3, 4], [0, 1, 2, 3, 4])
    assert span(ours, x0 - 2, y0 - 2, 9, 9) == ([0, 1, 2, 3], [0, 1, 2, 3])
    assert span(o4xp, sx - 2, y0 - 2, 5, 35)[0] == [40, 41]
    assert span(ours, sx - 2, y0 - 2, 5, 35)[0] == [40]  # the stream stays
    assert np.array_equal(o4xp == 255, (ours == 255) & (o4xp == 255))  # only water given back


def test_only_our_fade_draws_the_edges_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cell's job asks for the exact edges with ``soft`` alone: Ortho4XP's profiles draw as
    Ortho4XP does."""
    import orthostudio.masks.build as build

    asked: list[bool] = []

    def recording(*args: object, **kw: object) -> np.ndarray:
        asked.append(bool(kw.get("exact_edges")))
        return pre_mask(*args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(build, "pre_mask", recording)
    tris = water_triangles({TILE: _sea_mesh([(8432, 6000)])}, TILE, ZL)[(8432, 6000)]
    for mode in ("sand", "rocks", "soft"):
        margin = halo_px(mode, 100, TILE.lat, ZL, cell_lat=cell_latitude(6000, ZL))
        build_cell(_job(8432, 6000, tris, masking_mode=mode, margin=margin))
    assert asked == [False, False, True]
