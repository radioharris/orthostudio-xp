# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The DDS layout X-Plane reads for an ortho texture: 4096 x 4096 at the mesh's zoom.

It mirrors AutoOrtho's ``pydds``: BC1 (DXT1), 128-byte header, every mip level down to 1 x 1 (13
levels for 4096), 11,184,952 bytes in all. Knowing where each level starts turns an offset X-Plane
reads into "X-Plane wanted mip level k", and lets a file hold only some of its levels, the others
left as a hole of a sparse file.

A level is named for 4096 px: level 1 is 2048 px, level -1 8192. A texture whose zoom is set below
the mesh's is a smaller file, and one zoom above it an 8192 px one, as AutoOrtho sizes them: the
file's sharpest level is its ``top`` (:func:`size_of`, :func:`level_layout`).
"""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence

HEADER_SIZE = 128
BLOCK_BYTES = 8  # BC1
TEXTURE_SIZE = 4096
LEVELS = 13  # 4096 down to 1


def mip_layout(size: int = TEXTURE_SIZE) -> list[tuple[int, int]]:
    """[(offset, length)] of every mip level of a square BC1 DDS, down to 1 x 1."""
    out = []
    offset = HEADER_SIZE
    width = size
    while width >= 1:
        blocks = max(1, width // 4)
        length = blocks * blocks * BLOCK_BYTES
        out.append((offset, length))
        offset += length
        width //= 2
    return out


def size_of(top: int) -> int:
    """The side of a texture whose sharpest level is ``top``: 4096 px at 0, 2048 at 1, 8192 at
    -1."""
    return TEXTURE_SIZE >> top if top >= 0 else TEXTURE_SIZE << -top


def top_of_side(side: int) -> int:
    """The sharpest level of a texture ``side`` px wide: 0 at 4096 px, 1 at 2048, -1 at 8192."""
    return round(math.log2(TEXTURE_SIZE / side))


def read_top(path) -> int:
    """The sharpest level of a DDS file, from the width its header gives."""
    with open(path, "rb") as f:
        head = f.read(20)
    return top_of_side(int.from_bytes(head[16:20], "little"))


def level_layout(top: int) -> list[tuple[int, int]]:
    """``mip_layout`` of a texture whose sharpest level is ``top``: entry k is level ``top + k``."""
    return mip_layout(size_of(top))


def file_size(size: int = TEXTURE_SIZE) -> int:
    offset, length = mip_layout(size)[-1]
    return offset + length


def mips_touched(offset: int, length: int, layout: Sequence[tuple[int, int]]) -> list[int]:
    """Mip levels a read of ``length`` bytes at ``offset`` overlaps; -1 stands for the header."""
    end = offset + max(length, 1)
    out = [-1] if offset < HEADER_SIZE else []
    for level, (start, size) in enumerate(layout):
        if offset < start + size and end > start:
            out.append(level)
    return out


def header(size: int = TEXTURE_SIZE) -> bytes:
    """The 128-byte DDS header AutoOrtho writes for a BC1 texture with a full mip chain."""
    flags = (
        0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000
    )  # CAPS HEIGHT WIDTH PIXELFORMAT MIPMAPCOUNT LINEARSIZE
    linear_size = max(1, size * size >> 4) * BLOCK_BYTES
    caps = 0x1000 | 0x400000  # TEXTURE | MIPMAP
    out = struct.pack(
        "<4s7I44s2I4s5I5I",
        b"DDS ",
        124,
        flags,
        size,
        size,
        linear_size,
        0,
        len(mip_layout(size)),
        b"\0" * 44,
        32,
        0x4,  # pixel format size, DDPF_FOURCC
        b"DXT1",
        0,
        0,
        0,
        0,
        0,
        caps,
        0,
        0,
        0,
        0,
    )
    assert len(out) == HEADER_SIZE
    return out
