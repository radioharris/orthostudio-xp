"""Distance masks and the reach of Pillow's Gaussian blur (`orthostudio.masks.distance`)."""

from __future__ import annotations

import numpy as np
import pytest

from orthostudio.masks.distance import (
    DEFAULT_EDT_OFFSET_PX,
    distance_mask,
    edt_px,
    pil_blur_reach,
    pil_blur_support,
    pil_box_radius,
)


def test_edt_of_a_half_plane_is_the_column_index() -> None:
    inside = np.zeros((5, 10), dtype=bool)
    inside[:, 3:] = True
    d = edt_px(inside)
    assert d.dtype == np.float32
    assert list(d[2, :8]) == [0, 0, 0, 1, 2, 3, 4, 5]


def test_pil_box_radius_follows_pillow() -> None:
    """``ImagingGaussianBlur``: three box passes, Gwosdek equations 7, 11 and 14."""
    assert pil_box_radius(1.0) == pytest.approx(0.25)
    assert pil_box_radius(0.0) == 0.0
    assert pil_blur_support(1.0) == 3
    assert pil_blur_support(0.3) == 3


def test_pil_blur_reach_of_one_step_is_three_pixels() -> None:
    """Ortho4XP's ``3steps`` dilation step (``GaussianBlur(1) > 0``) grows an edge by 3 px."""
    assert pil_blur_reach(1.0) == 3
    assert pil_blur_reach(0.0) == 0
    assert pil_blur_reach(9.6) == 25
    assert pil_blur_reach(1.0) <= pil_blur_support(1.0)


def test_distance_mask_is_zero_on_land_and_saturates_offshore() -> None:
    margin = 67
    side = 4096 + 2 * margin
    pre = np.zeros((side, side), dtype=np.uint8)
    pre[:, : margin + 10] = 255  # land on the west edge only
    out = distance_mask(pre, mask_zl=14, margin=margin)
    assert out.shape == (4096, 4096)
    assert out[100, 0] == 0 and out[100, 9] == 0
    # 4 units per pixel at ZL14, with the half-pixel offset of the level set
    assert out[100, 14] == int((5 - DEFAULT_EDT_OFFSET_PX) * 4)
    assert out[100, 2000] == 255


def test_distance_mask_of_a_mask_without_water_is_all_zero() -> None:
    margin = 16
    pre = np.full((4096 + 2 * margin, 4096 + 2 * margin), 255, dtype=np.uint8)
    assert distance_mask(pre, mask_zl=14, margin=margin).max() == 0


@pytest.mark.parametrize("mask_zl", [14, 15, 16])
def test_distance_scale_follows_the_zoom_level(mask_zl: int) -> None:
    margin = 8
    side = 4096 + 2 * margin
    pre = np.zeros((side, side), dtype=np.uint8)
    pre[:, : margin + 1] = 255
    out = distance_mask(pre, mask_zl=mask_zl, margin=margin)
    scale = 2 ** (16 - mask_zl)
    assert out[0, 3] == int((3 - DEFAULT_EDT_OFFSET_PX) * scale)
