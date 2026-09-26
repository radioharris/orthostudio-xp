# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The elevation part of a pre-mask and the Ortho4XP mesh warp (`orthostudio.masks.dem`)."""

from __future__ import annotations

import numpy as np
from PIL import Image

from orthostudio.imagery.grid import tile_to_wgs84
from orthostudio.masks.dem import MASK_ALTITUDE_ABOVE, dem_pre_mask, mesh_warp, pix_to_wgs84
from orthostudio.masks.raster import CELL_PX
from orthostudio.masks.water import cell_pixel_origin, wgs84_to_pix

ZL = 14
CELL = (8432, 6000)


def test_pix_to_wgs84_is_the_inverse_of_wgs84_to_pix() -> None:
    lat, lon = tile_to_wgs84(CELL[0], CELL[1], ZL)
    px, py = wgs84_to_pix(lat, lon, ZL)
    back_lat, back_lon = pix_to_wgs84(px, py, ZL)
    assert abs(back_lat - lat) < 1e-6 and abs(back_lon - lon) < 1e-9


def test_mask_altitude_threshold_is_the_ortho4xp_one() -> None:
    assert MASK_ALTITUDE_ABOVE == 0.5


def test_mesh_warp_of_an_identity_box_keeps_the_image() -> None:
    """4326 to 4326 over the same box is a resize, not a reprojection."""
    source = Image.fromarray((np.indices((64, 64))[1] * 4).astype(np.uint8))
    box = (5.0, 43.5, 5.5, 43.0)
    out = mesh_warp(source, box, 4326, box, 4326, (64, 64))
    assert out.size == (64, 64)
    diff = np.abs(np.array(out, np.int32) - np.array(source, np.int32))
    assert diff.max() <= 1


def test_dem_pre_mask_without_land_is_black() -> None:
    margin = 16
    above = np.zeros((32, 32), dtype=bool)
    out = dem_pre_mask(above, (5.0, 5.1, 43.0, 43.1), *CELL, mask_zl=ZL, margin=margin)
    assert out.shape == (CELL_PX + 2 * margin, CELL_PX + 2 * margin)
    assert out.max() == 0


def test_dem_pre_mask_marks_the_covered_part_of_the_cell() -> None:
    margin = 16
    side = CELL_PX + 2 * margin
    px0, py0 = cell_pixel_origin(*CELL, ZL, margin)
    lat_max, lon_min = pix_to_wgs84(px0, py0, ZL)
    lat_min, lon_max = pix_to_wgs84(px0 + side, py0 + side, ZL)
    above = np.ones((64, 64), dtype=bool)
    out = dem_pre_mask(
        above, (lon_min, lon_max, lat_min, lat_max), *CELL, mask_zl=ZL, margin=margin
    )
    assert set(np.unique(out).tolist()) <= {0, 255}
    assert out[side // 2, side // 2] == 255
