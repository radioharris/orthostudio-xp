# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Pixels: decoding imagery pieces, the smaller levels, BC1 blocks.

Pillow decodes the pieces, numpy makes each smaller level as the average of four texels in linear
light (averaging the stored, gamma-encoded values darkens the distance), and Intel's ISPC texture
compressor (``ispc_texcomp``) encodes BC1. Every block leaves in BC1's four-colour mode: a block
whose first endpoint is not the larger is in three-colour mode, where index 3 is black.
"""

from __future__ import annotations

import io

import numpy as np

PIECE = 256
GAMMA = 2.2


def decode_piece(body: bytes) -> np.ndarray:
    """A 256 x 256 imagery piece (JPEG or PNG) as RGB uint8; ValueError if it is not one."""
    from PIL import Image

    with Image.open(io.BytesIO(body)) as im:
        if im.size != (PIECE, PIECE):
            raise ValueError(f"piece is {im.size[0]}x{im.size[1]}, not {PIECE}x{PIECE}")
        return np.asarray(im.convert("RGB"))


_TO_LINEAR = ((np.arange(256, dtype=np.float32) / 255.0) ** GAMMA).astype(np.float32)


STRIP = 512  # rows of a level taken to linear light at a time: an 8192 px level whole is 800 MB


def _half(lin: np.ndarray) -> np.ndarray:
    """The level below, in linear light: the average of each four texels."""
    return 0.25 * (lin[0::2, 0::2] + lin[1::2, 0::2] + lin[0::2, 1::2] + lin[1::2, 1::2])


def _stored(lin: np.ndarray) -> np.ndarray:
    """A level in linear light back to stored values, uint8, one float array at a time."""
    out = np.power(lin, 1.0 / GAMMA)
    out *= 255.0
    np.round(out, out=out)
    np.clip(out, 0, 255, out=out)
    return out.astype(np.uint8)


def mip_chain(rgb: np.ndarray) -> list[np.ndarray]:
    """``rgb`` (square, power-of-two side) and every smaller level down to 1 x 1, RGB uint8. The
    first smaller level is made a strip of rows at a time, the same values: an 8192 px texture's
    encoding held 2 GB at once, and a user's Mac, out of memory, froze X-Plane (2026-09-28)."""
    side = rgb.shape[0]
    if rgb.shape[1] != side or side & (side - 1):
        raise ValueError(f"mip_chain needs a square power-of-two image, got {rgb.shape[1]}x{side}")
    out = [rgb]
    if side == 1:
        return out
    lin = np.empty((side // 2, side // 2, rgb.shape[2]), dtype=np.float32)
    for y in range(0, side, STRIP):
        lin[y // 2 : (y + STRIP) // 2] = _half(_TO_LINEAR[rgb[y : y + STRIP]])
    out.append(_stored(lin))
    while lin.shape[0] > 1:
        lin = _half(lin)
        out.append(_stored(lin))
    return out


def expand_565(c: np.ndarray) -> np.ndarray:
    """Packed 5:6:5 colours (uint16, shape n) as (n, 3) RGB uint8, by bit replication as a GPU
    reads them."""
    r5 = (c >> 11) & 0x1F
    g6 = (c >> 5) & 0x3F
    b5 = c & 0x1F
    rgb = np.stack([(r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2)], axis=-1)
    return rgb.astype(np.uint8)


def four_colour(blocks: bytes) -> bytes:
    """Put every BC1 block in four-colour mode (first endpoint larger), keeping its colours.

    Equal endpoints: all texels are that colour, index 0 everywhere. First endpoint smaller: swap
    them; indices 0 and 1 swap too, and 2 (their midpoint in three-colour mode) and 3 (black, never
    meant for an opaque texture) become 2, the colour nearest the midpoint.
    """
    a = np.frombuffer(blocks, dtype=np.uint8).reshape(-1, 8).copy()
    c0 = a[:, 0].astype(np.uint16) | (a[:, 1].astype(np.uint16) << 8)
    c1 = a[:, 2].astype(np.uint16) | (a[:, 3].astype(np.uint16) << 8)
    idx = a[:, 4:8].copy().view("<u4").ravel()
    flat = c0 == c1
    idx[flat] = 0
    three = c0 < c1
    if three.any():
        a[three, 0:2], a[three, 2:4] = a[three, 2:4].copy(), a[three, 0:2].copy()
        old = idx[three]
        new = np.zeros_like(old)
        remap = np.array([1, 0, 2, 2], dtype=np.uint32)
        for texel in range(16):
            shift = np.uint32(2 * texel)
            new |= remap[(old >> shift) & np.uint32(3)] << shift
        idx[three] = new
    a[:, 4:8] = idx.view(np.uint8).reshape(-1, 4)
    return a.tobytes()


def encode_bc1(rgb: np.ndarray) -> bytes:
    """BC1 blocks of an RGB level (sides under 4 are padded, as the format wants 4 x 4 blocks)."""
    import ispc_texcomp

    h, w = rgb.shape[:2]
    ph, pw = max(4, (h + 3) // 4 * 4), max(4, (w + 3) // 4 * 4)
    rgba = np.empty((ph, pw, 4), dtype=np.uint8)
    rgba[:h, :w, :3] = rgb
    rgba[:h, :w, 3] = 255
    if (ph, pw) != (h, w):  # edge-replicate into the padding
        rgba[h:, :w] = rgba[h - 1 : h, :w]
        rgba[:, w:] = rgba[:, w - 1 : w]
    size = (ph // 4) * (pw // 4) * 8
    # ispc_texcomp 1.0.1 returns width x height bytes for BC1: the blocks, then as much again unused
    blocks = ispc_texcomp.compress_blocks_bc1(ispc_texcomp.RGBASurface(rgba, pw, ph))[:size]
    return four_colour(blocks)


def encode_levels(rgb: np.ndarray) -> list[bytes]:
    """BC1 blocks of ``rgb`` and of every smaller level down to 1 x 1."""
    return [encode_bc1(level) for level in mip_chain(rgb)]
