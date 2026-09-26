# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The three shoreline profiles and the halo crop (`orthostudio.masks.profiles`)."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageFilter

from orthostudio.errors import OsxpError
from orthostudio.masks.profiles import (
    WATER_TRANSITION,
    _convolve_rows,
    _hat_kernel,
    blur_mask,
    blur_widths,
    halo_px,
    rocks_width,
    sand_blur,
    sand_width,
    sea_level_for,
    three_steps_widths,
)

ZL = 14
LAT = 43


def _reference_sand(pre: np.ndarray, blur_width: int) -> np.ndarray:
    """``O4_Mask_Utils.py:665-679``, copied so the port is compared with the original."""
    b = np.array(pre)
    kernel = np.array(range(1, 2 * blur_width))
    kernel[blur_width:] = range(blur_width - 1, 0, -1)
    kernel = kernel / blur_width**2
    for i in range(len(b)):
        b[i] = np.convolve(b[i], kernel, "same")
    b = b.transpose()
    for i in range(len(b)):
        b[i] = np.convolve(b[i], kernel, "same")
    b = b.transpose()
    return np.array(2 * np.minimum(b, 127), dtype=np.uint8)


@pytest.fixture(scope="module")
def three_level() -> np.ndarray:
    """A synthetic pre-mask with the only three values a real one can hold."""
    rng = np.random.default_rng(20260912)
    coarse = rng.choice(np.array([0, 99, 255], dtype=np.uint8), (24, 24))
    return np.kron(coarse, np.ones((16, 16), dtype=np.uint8)).astype(np.uint8)


@pytest.mark.parametrize(
    ("ratio", "level"), [(0.0, 221), (0.1, 173), (0.25, 99), (0.5, 23), (1.0, 0), (2.0, 0)]
)
def test_sea_level_table(ratio: float, level: int) -> None:
    """``O4_Mask_Utils.py:69-71`` on Ortho4XP's own ``water_transition.png``."""
    assert sea_level_for(ratio) == level


def test_water_transition_table_is_monotone_and_128_long() -> None:
    assert len(WATER_TRANSITION) == 128
    assert list(WATER_TRANSITION) == sorted(WATER_TRANSITION)
    assert WATER_TRANSITION[0] == 0 and WATER_TRANSITION[-1] == 253


def test_blur_widths_of_the_reference_tile() -> None:
    assert sand_width(100, LAT, ZL) == 14
    assert rocks_width(100, LAT, ZL) == pytest.approx(7.214, abs=1e-3)
    assert three_steps_widths([100, 200, 100], LAT, ZL) == pytest.approx(
        [14.43, 28.86, 14.43], abs=1e-2
    )
    assert blur_widths("none", 100, LAT, ZL) == 0.0


def test_masks_width_type_is_checked() -> None:
    with pytest.raises(OsxpError, match="masks_width"):
        blur_widths("3steps", 100, LAT, ZL)
    with pytest.raises(OsxpError, match="masks_width"):
        blur_widths("sand", [100, 200, 100], LAT, ZL)
    with pytest.raises(OsxpError, match="masks_width"):
        three_steps_widths([100, 200], LAT, ZL)


def test_hat_kernel_is_a_box_convolved_with_itself() -> None:
    kernel = _hat_kernel(14)
    assert kernel.shape == (27,)
    box = np.ones(14)
    assert kernel * 196 == pytest.approx(np.convolve(box, box))


def test_sand_matches_the_ortho4xp_code(three_level: np.ndarray) -> None:
    assert np.array_equal(sand_blur(three_level, 14), _reference_sand(three_level, 14))


def test_row_memoisation_is_transparent() -> None:
    """Identical rows are convolved once; the result must not depend on that."""
    rng = np.random.default_rng(7)
    array = np.repeat(rng.choice(np.array([0, 99, 255], np.uint8), (8, 400)), 5, axis=0)
    kernel = _hat_kernel(9)
    plain = np.stack([np.convolve(r, kernel, "same").astype(np.uint8) for r in array])
    assert np.array_equal(_convolve_rows(array, kernel), plain)


def test_crop_matches_full_margin(three_level: np.ndarray) -> None:
    """The ``sand`` halo of ``blur_width - 1`` keeps every byte of the useful window."""
    blur_width = 5
    halo = blur_width - 1
    full = sand_blur(three_level, blur_width)
    window = slice(64, three_level.shape[0] - 64)
    crop = slice(64 - halo, three_level.shape[0] - 64 + halo)
    cropped = sand_blur(np.ascontiguousarray(three_level[crop, crop]), blur_width)
    assert np.array_equal(cropped[halo:-halo, halo:-halo], full[window, window])


def test_halo_is_the_reach_of_the_profile() -> None:
    assert halo_px("sand", 100, LAT, ZL) == 15
    assert halo_px("sand", 100, LAT, ZL, distance_masks_too=True) == 67
    assert halo_px("rocks", 100, LAT, ZL) == 35
    assert halo_px("3steps", [100, 200, 100], LAT, ZL) == 57
    assert halo_px("rocks", 30000, LAT, ZL) == 1024  # capped at the Ortho4XP margin


def test_rocks_crop_matches_full_margin() -> None:
    """Pillow's Gaussian has an exact support, so the rocks halo is also loss-free."""
    rng = np.random.default_rng(3)
    coarse = rng.choice(np.array([0, 255], dtype=np.uint8), (10, 10))
    pre = np.kron(coarse, np.ones((40, 40), dtype=np.uint8)).astype(np.uint8)
    halo = halo_px("rocks", 100, LAT, ZL)
    full = blur_mask(pre, masking_mode="rocks", masks_width=100, lat=LAT, mask_zl=ZL, sea_level=99)
    window = slice(halo + 20, pre.shape[0] - halo - 20)
    crop = slice(20, pre.shape[0] - 20)
    cropped = blur_mask(
        np.ascontiguousarray(pre[crop, crop]),
        masking_mode="rocks",
        masks_width=100,
        lat=LAT,
        mask_zl=ZL,
        sea_level=99,
    )
    assert np.array_equal(cropped[halo:-halo, halo:-halo], full[window, window])


def test_three_steps_paints_a_decreasing_ladder() -> None:
    pre = np.zeros((400, 400), dtype=np.uint8)
    pre[:, :200] = 255
    out = blur_mask(
        pre,
        masking_mode="3steps",
        masks_width=[100, 200, 100],
        lat=LAT,
        mask_zl=ZL,
        sea_level=99,
    )
    profile = out[200, 200:]
    assert profile[0] > profile[10] >= profile[30]
    assert profile[-1] == 0
    assert np.all(np.diff(profile.astype(int)) <= 0)


def test_unknown_mode_copies_the_pre_mask(three_level: np.ndarray) -> None:
    out = blur_mask(
        three_level, masking_mode="none", masks_width=100, lat=LAT, mask_zl=ZL, sea_level=99
    )
    assert np.array_equal(out, three_level) and out is not three_level


def test_zero_blur_width_copies_the_pre_mask(three_level: np.ndarray) -> None:
    out = blur_mask(
        three_level, masking_mode="sand", masks_width=0, lat=LAT, mask_zl=ZL, sea_level=99
    )
    assert np.array_equal(out, three_level)


def test_pillow_gaussian_support_bounds_the_real_one() -> None:
    """The computed support must never be smaller than what Pillow actually moves."""
    from orthostudio.masks.distance import pil_blur_support

    for sigma in (0.3, 1.0, 2.0, 4.29, 7.21):
        n = 4 * pil_blur_support(sigma) + 21
        probe = np.zeros((3, n), dtype=np.uint8)
        probe[:, : n // 2] = 255
        blurred = np.array(
            Image.fromarray(probe).filter(ImageFilter.GaussianBlur(sigma)), dtype=np.uint8
        )
        lit = np.flatnonzero(blurred[1] > 0)
        assert int(lit.max() - (n // 2 - 1)) <= pil_blur_support(sigma)
