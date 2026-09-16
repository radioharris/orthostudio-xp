"""Water masks as alpha: cell arithmetic, threshold and imprint rules."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from orthostudio.errors import OsxpError
from orthostudio.imagery.grid import TextureId
from orthostudio.textures.imprint import (
    clean_halo_mask,
    imprint,
    load_mask,
    mask_cell,
    mask_file_name,
    mask_for_texture,
    masks_dir_lookup,
    needs_mask,
    sea_blur_radius,
)


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return 99.0 if mse == 0 else 10 * np.log10(255.0**2 / mse)


def _quadrant_mask() -> np.ndarray:
    """4096² mask whose 4x4 sub-squares of 1024 px carry distinct flat values (40..220)."""
    m = np.empty((4096, 4096), dtype=np.uint8)
    for ry in range(4):
        for rx in range(4):
            m[ry * 1024 : (ry + 1) * 1024, rx * 1024 : (rx + 1) * 1024] = 40 + 12 * (4 * ry + rx)
    return m


@pytest.fixture
def masks_dir(tmp_path: Path) -> Path:
    Image.fromarray(_quadrant_mask(), mode="L").save(tmp_path / mask_file_name(8448, 6016))
    return tmp_path


def test_mask_file_name_and_lookup(masks_dir: Path) -> None:
    assert mask_file_name(8448, 6016) == "6016_8448.png"
    lookup = masks_dir_lookup(masks_dir)
    assert lookup(8448, 6016) == masks_dir / "6016_8448.png"
    assert lookup(8464, 6016) is None


def test_load_mask_errors(tmp_path: Path) -> None:
    bad = tmp_path / "x.png"
    bad.write_bytes(b"nope")
    with pytest.raises(OsxpError) as exc:
        load_mask(bad)
    assert exc.value.code == "MASK_FILE_UNREADABLE"
    small = tmp_path / "small.png"
    Image.new("L", (256, 256)).save(small)
    with pytest.raises(OsxpError) as exc:
        load_mask(small)
    assert exc.value.code == "MASK_FILE_UNREADABLE"


def test_mask_cell_arithmetic_follows_ortho4xp() -> None:
    # same zoom: identity
    assert mask_cell(TextureId(8448, 6016, 14, "BI"), 14) == (8448, 6016, 0, 0, 4096)
    # one level up: the mask texture holds 2x2 textures, each on a 2048 px window
    assert mask_cell(TextureId(16896, 12032, 15, "BI"), 14) == (8448, 6016, 0, 0, 2048)
    assert mask_cell(TextureId(16912, 12032, 15, "BI"), 14) == (8448, 6016, 2048, 0, 2048)
    assert mask_cell(TextureId(16896, 12048, 15, "BI"), 14) == (8448, 6016, 0, 2048, 2048)
    # two levels up: 4x4 textures, 1024 px windows
    assert mask_cell(TextureId(33840, 24080, 16, "BI"), 14) == (8448, 6016, 3072, 1024, 1024)
    with pytest.raises(ValueError):
        mask_cell(TextureId(0, 0, 13, "BI"), 14)


def test_mask_for_texture_below_mask_zl_or_absent(masks_dir: Path) -> None:
    lookup = masks_dir_lookup(masks_dir)
    assert mask_for_texture(TextureId(4224, 3008, 13, "BI"), 14, lookup) is None
    assert mask_for_texture(TextureId(8464, 6016, 14, "BI"), 14, lookup) is None


def test_mask_for_texture_same_zoom_is_the_file(masks_dir: Path) -> None:
    m = mask_for_texture(TextureId(8448, 6016, 14, "BI"), 14, masks_dir_lookup(masks_dir))
    assert m is not None and np.array_equal(m, _quadrant_mask())


@pytest.mark.parametrize(
    ("t", "value"),
    [
        (TextureId(16896, 12032, 15, "BI"), None),  # top-left half: four sub-squares
        (TextureId(33840, 24080, 16, "BI"), 40 + 12 * (4 * 1 + 3)),  # (rx=3, ry=1) sub-square
        (TextureId(33840, 24112, 16, "BI"), 40 + 12 * (4 * 3 + 3)),  # bottom-right
    ],
)
def test_mask_for_texture_crops_and_resamples(
    masks_dir: Path, t: TextureId, value: int | None
) -> None:
    m = mask_for_texture(t, 14, masks_dir_lookup(masks_dir))
    assert m is not None and m.shape == (4096, 4096) and m.dtype == np.uint8
    if value is None:
        # zl 15 top-left: the four values of the top-left 2048² window, each on 2048² now
        assert m[100, 100] == 40 and m[100, 3000] == 52 and m[3000, 100] == 88
        assert m[3000, 3000] == 100
    else:
        assert (m == value).all()


def test_needs_mask_threshold() -> None:
    m = np.zeros((4096, 4096), dtype=np.uint8)
    m[10, 10] = 30
    assert needs_mask(m) is False
    m[10, 10] = 31
    assert needs_mask(m) is True


def test_sea_blur_radius() -> None:
    assert sea_blur_radius(0, 14) == 0
    assert sea_blur_radius(8, 17) == 8
    assert sea_blur_radius(8, 14) == 1
    assert sea_blur_radius(1, 19) == 4


def test_clean_halo_mask_only_in_transition_over_white_or_black() -> None:
    rgb = np.full((4, 4, 3), 100, dtype=np.uint8)
    rgb[0, 0] = 245  # sum 735: white
    rgb[0, 1] = (255, 255, 224)  # sum 734: not white
    rgb[1, 0] = (11, 12, 12)  # sum 35: black
    rgb[1, 1] = 255  # white but mask is 254: not transition
    rgb[2, 2] = 255  # white but mask is 0
    mask = np.full((4, 4), 128, dtype=np.uint8)
    mask[1, 1] = 254
    mask[2, 2] = 0
    mask[3, 3] = 255
    out = clean_halo_mask(mask, rgb)
    assert out[0, 0] == 0 and out[1, 0] == 0
    assert out[0, 1] == 128 and out[1, 1] == 254 and out[2, 2] == 0 and out[3, 3] == 255
    assert mask[0, 0] == 128, "input untouched"


def test_imprint_puts_alpha_and_keeps_rgb() -> None:
    rng = np.random.default_rng(1)
    rgb = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
    alpha = rng.integers(0, 256, size=(64, 64), dtype=np.uint8)
    out = imprint(rgb, alpha, zl=14)
    assert out.shape == (64, 64, 4) and out.dtype == np.uint8
    assert np.array_equal(out[:, :, :3], rgb) and np.array_equal(out[:, :, 3], alpha)
    with pytest.raises(ValueError):
        imprint(rgb, alpha[:32], zl=14)


def test_imprint_sea_blur_touches_water_only() -> None:
    rng = np.random.default_rng(2)
    rgb = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
    alpha = np.full((64, 64), 255, dtype=np.uint8)
    alpha[:, 32:] = 0  # right half is water
    out = imprint(rgb, alpha, sea_texture_blur=16, zl=17)  # radius 16 px
    assert np.array_equal(out[:, :32, :3], rgb[:, :32])
    assert not np.array_equal(out[:, 32:, :3], rgb[:, 32:])
    assert out[:, 32:, :3].std() < rgb[:, 32:].std()
    assert np.array_equal(out[:, :, 3], alpha)


def test_imprint_clean_halo_flag() -> None:
    rgb = np.full((8, 8, 3), 255, dtype=np.uint8)
    alpha = np.full((8, 8), 100, dtype=np.uint8)
    assert (imprint(rgb, alpha, zl=14)[:, :, 3] == 100).all()
    assert (imprint(rgb, alpha, zl=14, clean_halo=True)[:, :, 3] == 0).all()
