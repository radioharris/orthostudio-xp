# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Mip chain generation: box filter in linear light (nvtt behaviour) or on stored values."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra import numpy as hnp

from orthostudio.textures.mips import (
    MIP_MODES,
    box_down_uint8,
    from_linear,
    mip_chain,
    to_linear_lut,
)


def _checker(n: int, alpha: tuple[int, int] = (255, 255)) -> np.ndarray:
    a = np.zeros((n, n, 4), dtype=np.uint8)
    odd = (np.indices((n, n)).sum(axis=0) & 1).astype(bool)
    a[odd, :3] = 255
    a[:, :, 3] = np.where(odd, alpha[1], alpha[0]).astype(np.uint8)
    return a


@pytest.mark.parametrize("mode", MIP_MODES)
def test_chain_shapes(mode: str) -> None:
    chain = mip_chain(np.zeros((64, 32, 4), np.uint8), mode=mode)
    assert [c.shape for c in chain] == [
        (64, 32, 4),
        (32, 16, 4),
        (16, 8, 4),
        (8, 4, 4),
        (4, 2, 4),
        (2, 1, 4),
        (1, 1, 4),
    ]
    assert all(c.dtype == np.uint8 for c in chain)
    assert len(mip_chain(np.zeros((64, 64, 3), np.uint8), mode=mode, levels=3)) == 3
    assert mip_chain(np.zeros((64, 64, 3), np.uint8), mode=mode, levels=1)[0].shape == (64, 64, 3)


def test_checkerboard_average_depends_on_mode() -> None:
    img = _checker(16, alpha=(0, 255))
    none = mip_chain(img, mode="none")[1]
    g22 = mip_chain(img, mode="gamma22")[1]
    srgb = mip_chain(img, mode="srgb")[1]
    assert np.all(none[:, :, :3] == 128)  # (0 + 255 + 0 + 255 + 2) >> 2
    assert np.all(g22[:, :, :3] == round(0.5 ** (1 / 2.2) * 255))  # 186
    assert np.all(srgb[:, :, :3] == round((1.055 * 0.5 ** (1 / 2.4) - 0.055) * 255))  # 188
    # alpha is averaged on stored values whatever the mode
    for level in (none, g22, srgb):
        assert np.all(level[:, :, 3] == 128)


@pytest.mark.parametrize("mode", MIP_MODES)
def test_constant_image_stays_constant(mode: str) -> None:
    img = np.empty((32, 32, 4), np.uint8)
    img[:] = (17, 200, 99, 3)
    for level in mip_chain(img, mode=mode):
        assert np.all(level.reshape(-1, 4) == (17, 200, 99, 3))


def test_transfer_curves_round_trip_every_code() -> None:
    codes = np.arange(256, dtype=np.uint8)
    for mode in ("gamma22", "srgb"):
        assert np.array_equal(from_linear(to_linear_lut(mode)[codes], mode), codes)


def test_linear_light_is_brighter_than_stored_average() -> None:
    rng = np.random.default_rng(0)
    img = rng.integers(0, 256, (64, 64, 3), np.uint8)
    none = mip_chain(img, mode="none")[3].astype(float).mean()
    g22 = mip_chain(img, mode="gamma22")[3].astype(float).mean()
    assert g22 > none + 1.0


def test_errors() -> None:
    with pytest.raises(ValueError, match="power-of-two"):
        mip_chain(np.zeros((6, 8, 4), np.uint8))
    with pytest.raises(ValueError, match="unknown mip mode"):
        mip_chain(np.zeros((8, 8, 4), np.uint8), mode="linear")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="uint8"):
        mip_chain(np.zeros((8, 8, 4), np.float32))


@settings(max_examples=25, deadline=None)
@given(hnp.arrays(np.uint8, st.sampled_from([(8, 8, 4), (16, 8, 3)]), elements=st.integers(0, 255)))
def test_none_mode_is_box_filter_within_parent_range(img: np.ndarray) -> None:
    chain = mip_chain(img, mode="none")
    assert np.array_equal(chain[1], box_down_uint8(img))
    h, w = img.shape[:2]
    blocks = img.reshape(h // 2, 2, w // 2, 2, -1)
    assert np.all(chain[1] >= blocks.min(axis=(1, 3))) and np.all(
        chain[1] <= blocks.max(axis=(1, 3))
    )
