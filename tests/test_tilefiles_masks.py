# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Mask index and mask-window arithmetic (Ortho4XP's)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orthostudio.tilefiles import MaskWindow, mask_tile_for, masks_index


def _reference_needs_mask_indices(til_x: int, til_y: int, zl: int, mask_zl: int) -> tuple:
    # verbatim port of O4_Mask_Utils.py:41-53 for the test only
    factor = 2 ** (zl - mask_zl)
    m_til_x = (int(til_x / factor) // 16) * 16
    m_til_y = (int(til_y / factor) // 16) * 16
    rx = int((til_x - factor * m_til_x) / 16)
    ry = int((til_y - factor * m_til_y) / 16)
    return m_til_x, m_til_y, int(rx * 4096 / factor), int(ry * 4096 / factor), 4096 // factor


@pytest.mark.parametrize(
    ("til_x", "til_y", "zl", "mask_zl"),
    [
        (8448, 6016, 14, 14),
        (33792, 24064, 16, 14),
        (33808, 24080, 16, 14),
        (33856, 24112, 16, 14),
        (135424, 96256, 18, 14),
        (16896, 12032, 15, 14),
        (8448, 6016, 14, 12),
    ],
)
def test_mask_tile_for_matches_the_reference(til_x: int, til_y: int, zl: int, mask_zl: int) -> None:
    w = mask_tile_for(til_x, til_y, zl, mask_zl)
    assert w is not None
    mx, my, x0, y0, side = _reference_needs_mask_indices(til_x, til_y, zl, mask_zl)
    assert w == MaskWindow(mx, my, 2 ** (zl - mask_zl), x0, y0, side)
    assert w.m_til_x % 16 == 0 and w.m_til_y % 16 == 0
    assert w.file_name == f"{my}_{mx}.png"


def test_mask_tile_for_window_offsets() -> None:
    assert mask_tile_for(33808, 24080, 16, 14) == MaskWindow(8448, 6016, 4, 1024, 1024, 1024)
    assert mask_tile_for(33856, 24112, 16, 14) == MaskWindow(8464, 6016, 4, 0, 3072, 1024)
    assert mask_tile_for(8448, 6016, 13, 14) is None


def test_masks_index_filters(tmp_path: Path) -> None:
    flat = tmp_path / "masks"
    flat.mkdir()
    for name in (
        "6016_8448.png",
        "6000_8432.png",
        "6016_8448_dist.png",
        "6017_8448.png",
        "99999999_8448.png",
        "readme.txt",
    ):
        (flat / name).write_bytes(b"")
    (flat / "Combined_imagery").mkdir()
    (flat / "Combined_imagery" / "6000_8416.png").write_bytes(b"")
    idx = masks_index(flat, 14)
    assert sorted(idx.entries) == [(8432, 6000), (8448, 6016)] and len(idx) == 2
    assert idx(8448, 6016) == flat / "6016_8448.png"
    assert idx(8416, 6000) is None
    assert len(masks_index(tmp_path / "nowhere", 14)) == 0


def test_masks_index_for_texture(tmp_path: Path) -> None:
    (tmp_path / "6016_8448.png").write_bytes(b"")
    idx = masks_index(tmp_path, 14)
    hit = idx.for_texture(33808, 24080, 16)
    assert hit is not None and hit[1].name == "6016_8448.png" and hit[0].x0 == 1024
    assert idx.for_texture(33792, 24000, 16) is None
    assert idx.for_texture(8448, 6016, 13) is None
