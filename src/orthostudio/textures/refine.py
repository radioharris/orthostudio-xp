# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Least-squares endpoint refinement of BC1 colour blocks (what nvtt's fast mode does).

Optional, pure numpy: each pass solves the 2x2 normal equations for the two endpoints given
the current indices, requantises to 5:6:5, reassigns indices and keeps the result only where
the block error decreased. Two passes bring ispc_texcomp's BC1 to nvcompress -fast quality
(+0.4 dB) at roughly 1.6 s of CPU per pass per 4096² level; see
``docs/benchmarks/dds-encoder.md``.
"""

from __future__ import annotations

import numpy as np

from orthostudio.textures.dds import BLOCK_BYTES, check_format

_BETA = np.array([0.0, 1.0, 1 / 3, 2 / 3], dtype=np.float32)
_SHIFTS = np.arange(0, 32, 2, dtype=np.uint32)
CHUNK_BLOCKS = 1 << 16


def _expand_565_f(c: np.ndarray) -> np.ndarray:
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return np.stack(
        [(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], axis=-1
    ).astype(np.float32)


def _pack_565(rgb: np.ndarray) -> np.ndarray:
    r = np.clip(np.rint(rgb[:, 0] * (31 / 255)), 0, 31).astype(np.uint16)
    g = np.clip(np.rint(rgb[:, 1] * (63 / 255)), 0, 63).astype(np.uint16)
    b = np.clip(np.rint(rgb[:, 2] * (31 / 255)), 0, 31).astype(np.uint16)
    return (r << 11) | (g << 5) | b


def _palette(c0: np.ndarray, c1: np.ndarray) -> np.ndarray:
    """4-colour palette (n, 4, 3) float32 as a decoder reconstructs it."""
    p0 = _expand_565_f(c0)
    p1 = _expand_565_f(c1)
    p2 = np.floor((2 * p0 + p1 + 1) * np.float32(1 / 3))
    p3 = np.floor((p0 + 2 * p1 + 1) * np.float32(1 / 3))
    return np.stack([p0, p1, p2, p3], axis=1)


def block_pixels(rgba: np.ndarray) -> np.ndarray:
    """(h, w, 3|4) uint8 with sides multiple of 4 -> (n, 16, 3) float32 RGB in block order."""
    h, w = rgba.shape[:2]
    if h % 4 or w % 4:
        raise ValueError("sides must be multiples of 4")
    rgb = rgba[:, :, :3]
    return (
        rgb.reshape(h // 4, 4, w // 4, 4, 3)
        .transpose(0, 2, 1, 3, 4)
        .reshape(-1, 16, 3)
        .astype(np.float32)
    )


def refine_colour_blocks(colour: np.ndarray, pixels: np.ndarray, passes: int) -> np.ndarray:
    """Refine (n, 8) BC1 colour blocks against their (n, 16, 3) float32 source pixels."""
    n = len(colour)
    c0 = colour[:, 0].astype(np.uint16) | (colour[:, 1].astype(np.uint16) << 8)
    c1 = colour[:, 2].astype(np.uint16) | (colour[:, 3].astype(np.uint16) << 8)
    bits = np.ascontiguousarray(colour[:, 4:8]).view("<u4")[:, 0]
    idx = ((bits[:, None] >> _SHIFTS[None, :]) & 3).astype(np.intp)
    # Only 4-colour blocks are refined; 3-colour ones (c0 <= c1) are left untouched.
    four = c0 > c1

    def reconstruct(pal: np.ndarray, ind: np.ndarray) -> np.ndarray:
        return np.take_along_axis(pal, ind[:, :, None].repeat(3, axis=2), axis=1)

    best = ((reconstruct(_palette(c0, c1), idx) - pixels) ** 2).sum(axis=(1, 2))
    for _ in range(passes):
        b = _BETA[idx]
        a = np.float32(1) - b
        aa = (a * a).sum(1)
        ab = (a * b).sum(1)
        bb = (b * b).sum(1)
        r0 = np.einsum("ni,nic->nc", a, pixels)
        r1 = np.einsum("ni,nic->nc", b, pixels)
        det = aa * bb - ab * ab
        ok = four & (det > 1e-4)
        det = np.where(ok, det, np.float32(1))
        e0 = (bb[:, None] * r0 - ab[:, None] * r1) / det[:, None]
        e1 = (aa[:, None] * r1 - ab[:, None] * r0) / det[:, None]
        q0 = _pack_565(e0)
        q1 = _pack_565(e1)
        pal = _palette(q0, q1)
        dist = ((pixels[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)
        nidx = dist.argmin(-1)
        nsse = np.take_along_axis(dist, nidx[:, :, None], axis=2)[:, :, 0].sum(1)
        better = ok & (nsse < best) & (q0 != q1)
        c0 = np.where(better, q0, c0)
        c1 = np.where(better, q1, c1)
        idx = np.where(better[:, None], nidx, idx)
        best = np.where(better, nsse, best)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    idx = np.where(swap[:, None], idx ^ 1, idx)
    packed = (idx.astype(np.uint32) << _SHIFTS[None, :]).sum(1, dtype=np.uint32)
    out = np.empty((n, 8), dtype=np.uint8)
    out[:, 0] = c0 & 0xFF
    out[:, 1] = c0 >> 8
    out[:, 2] = c1 & 0xFF
    out[:, 3] = c1 >> 8
    out[:, 4:8] = packed.view(np.uint8).reshape(n, 4)
    return out


def refine_blocks(blocks: bytes, rgba: np.ndarray, fmt: str, passes: int = 2) -> bytes:
    """Refine the colour part of one level of ``fmt`` blocks against its padded source."""
    fmt = check_format(fmt)
    if passes <= 0:
        return blocks
    bs = BLOCK_BYTES[fmt]
    arr = np.frombuffer(blocks, dtype=np.uint8).reshape(-1, bs).copy()
    pixels = block_pixels(rgba)
    if len(pixels) != len(arr):
        raise ValueError("block count does not match the image")
    for start in range(0, len(arr), CHUNK_BLOCKS):
        stop = start + CHUNK_BLOCKS
        arr[start:stop, bs - 8 :] = refine_colour_blocks(
            arr[start:stop, bs - 8 :], pixels[start:stop], passes
        )
    return arr.tobytes()
