# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Vectorised numpy decoder for BC1 (DXT1) and BC3 (DXT5) blocks.

Used to measure the quality of encoded textures and to read reference DDS files in tests and
benchmarks. Decoding follows the D3D reference: 5/6/5 endpoints expanded by bit replication,
colour interpolation rounded to nearest, 3-colour mode when ``color0 <= color1``.
"""

from __future__ import annotations

import numpy as np

from orthostudio.textures.dds import BLOCK_BYTES, check_format, split_levels


def _expand_565(c: np.ndarray) -> np.ndarray:
    """Expand packed 5:6:5 colours (uint16, shape n) to (n, 3) uint8 RGB by bit replication."""
    r5 = ((c >> 11) & 0x1F).astype(np.uint16)
    g6 = ((c >> 5) & 0x3F).astype(np.uint16)
    b5 = (c & 0x1F).astype(np.uint16)
    rgb = np.stack([(r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2)], axis=-1)
    return rgb.astype(np.uint8)


def _bc1_palette(colour_blocks: np.ndarray) -> np.ndarray:
    """Palette (n, 4, 4) RGBA of BC1 colour blocks given as (n, 8) uint8."""
    c0 = colour_blocks[:, 0].astype(np.uint16) | (colour_blocks[:, 1].astype(np.uint16) << 8)
    c1 = colour_blocks[:, 2].astype(np.uint16) | (colour_blocks[:, 3].astype(np.uint16) << 8)
    p0 = _expand_565(c0).astype(np.int32)
    p1 = _expand_565(c1).astype(np.int32)
    four = (c0 > c1)[:, None]
    p2 = np.where(four, (2 * p0 + p1 + 1) // 3, (p0 + p1 + 1) // 2)
    p3 = np.where(four, (p0 + 2 * p1 + 1) // 3, 0)
    palette = np.zeros((len(c0), 4, 4), dtype=np.uint8)
    palette[:, 0, :3] = p0
    palette[:, 1, :3] = p1
    palette[:, 2, :3] = p2
    palette[:, 3, :3] = p3
    palette[:, :, 3] = 255
    palette[:, 3, 3] = np.where(four[:, 0], 255, 0)
    return palette


def _bc1_pixels(colour_blocks: np.ndarray) -> np.ndarray:
    """Decode (n, 8) BC1 colour blocks to (n, 16, 4) RGBA pixels in row-major block order."""
    palette = _bc1_palette(colour_blocks)
    bits = colour_blocks[:, 4:8].copy().view("<u4")[:, 0]
    shifts = np.arange(0, 32, 2, dtype=np.uint32)
    idx = (bits[:, None] >> shifts[None, :]) & 3
    return np.take_along_axis(palette, idx[:, :, None].astype(np.intp), axis=1)


def _bc3_alpha(alpha_blocks: np.ndarray) -> np.ndarray:
    """Decode (n, 8) BC4-style alpha blocks to (n, 16) uint8."""
    a0 = alpha_blocks[:, 0].astype(np.int32)
    a1 = alpha_blocks[:, 1].astype(np.int32)
    eight = (a0 > a1)[:, None]
    i = np.arange(1, 7)[None, :]
    interp8 = ((7 - i) * a0[:, None] + i * a1[:, None] + 3) // 7
    i5 = np.arange(1, 5)[None, :]
    interp6 = ((5 - i5) * a0[:, None] + i5 * a1[:, None] + 2) // 5
    pal6 = np.concatenate(
        [interp6, np.zeros((len(a0), 1), np.int32), np.full((len(a0), 1), 255, np.int32)], axis=1
    )
    palette = np.empty((len(a0), 8), dtype=np.int32)
    palette[:, 0] = a0
    palette[:, 1] = a1
    palette[:, 2:] = np.where(eight, interp8, pal6)
    padded = np.zeros((len(a0), 8), dtype=np.uint8)
    padded[:, :6] = alpha_blocks[:, 2:8]
    bits = padded.view("<u8")[:, 0]
    shifts = np.arange(0, 48, 3, dtype=np.uint64)
    idx = ((bits[:, None] >> shifts[None, :]) & np.uint64(7)).astype(np.intp)
    return np.take_along_axis(palette, idx, axis=1).astype(np.uint8)


def decode_blocks(data: bytes | memoryview | np.ndarray, width: int, height: int, fmt: str):
    """Decode one level of ``fmt`` blocks to an (height, width, 4) uint8 RGBA array."""
    fmt = check_format(fmt)
    bw, bh = (width + 3) // 4, (height + 3) // 4
    bs = BLOCK_BYTES[fmt]
    raw = np.frombuffer(data, dtype=np.uint8) if not isinstance(data, np.ndarray) else data
    if raw.size != bw * bh * bs:
        raise ValueError(f"expected {bw * bh * bs} bytes of {fmt} data, got {raw.size}")
    blocks = raw.reshape(bw * bh, bs)
    if fmt == "bc1":
        pixels = _bc1_pixels(blocks)
    else:
        pixels = _bc1_pixels(blocks[:, 8:16])
        pixels[:, :, 3] = _bc3_alpha(blocks[:, 0:8])
    image = pixels.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return np.ascontiguousarray(image[:height, :width])


def decode_dds(data: bytes | bytearray | memoryview) -> list[np.ndarray]:
    """Decode every mip level of a DXT1/DXT5 DDS file image to RGBA arrays."""
    header, levels = split_levels(data)
    return [
        decode_blocks(blob, w, h, header.fmt)
        for blob, (w, h) in zip(levels, header.level_dims, strict=True)
    ]
