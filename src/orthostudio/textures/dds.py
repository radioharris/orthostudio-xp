# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""DDS container as written by Ortho4XP (nvcompress) and read by X-Plane.

Only the subset used for orthophoto textures is modelled: 2D, block-compressed DXT1 (BC1) or
DXT5 (BC3), full mip chain down to 1x1. Spec: ``docs/specs/textures-dds.md``.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Format = Literal["bc1", "bc3"]

MAGIC = b"DDS "
HEADER_SIZE = 128  # 4-byte magic + 124-byte DDS_HEADER
FOURCC: dict[str, bytes] = {"bc1": b"DXT1", "bc3": b"DXT5"}
FORMAT_OF_FOURCC: dict[bytes, Format] = {b"DXT1": "bc1", b"DXT5": "bc3"}
BLOCK_BYTES: dict[str, int] = {"bc1": 8, "bc3": 16}

# DDS_HEADER.dwFlags
DDSD_CAPS = 0x1
DDSD_HEIGHT = 0x2
DDSD_WIDTH = 0x4
DDSD_PIXELFORMAT = 0x1000
DDSD_MIPMAPCOUNT = 0x20000
DDSD_LINEARSIZE = 0x80000
# DDS_PIXELFORMAT.dwFlags
DDPF_FOURCC = 0x4
# DDS_HEADER.dwCaps
DDSCAPS_COMPLEX = 0x8
DDSCAPS_TEXTURE = 0x1000
DDSCAPS_MIPMAP = 0x400000

_HEADER_STRUCT = struct.Struct("<4s7I11I2I4s5I5I")  # 128 bytes


def check_format(fmt: str) -> Format:
    """Return ``fmt`` if it is a supported block format, raise ``ValueError`` otherwise."""
    if fmt not in BLOCK_BYTES:
        raise ValueError(f"unsupported DDS format {fmt!r}, expected 'bc1' or 'bc3'")
    return fmt  # type: ignore[return-value]


def full_mip_count(width: int, height: int) -> int:
    """Number of levels of a full mip chain down to 1x1 (13 for 4096x4096)."""
    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")
    return max(width, height).bit_length()


def mip_dims(width: int, height: int, count: int) -> list[tuple[int, int]]:
    """Dimensions of ``count`` successive mip levels, each side halved and floored at 1."""
    dims: list[tuple[int, int]] = []
    w, h = width, height
    for _ in range(count):
        dims.append((w, h))
        w, h = max(1, w // 2), max(1, h // 2)
    return dims


def level_size(width: int, height: int, fmt: str) -> int:
    """Byte size of one block-compressed level (blocks are 4x4, partial blocks count)."""
    return ((width + 3) // 4) * ((height + 3) // 4) * BLOCK_BYTES[check_format(fmt)]


def expected_size(width: int, height: int, fmt: str, *, mips: bool = True) -> int:
    """Total file size: header plus every level (11 184 952 for a 4096² DXT1 with mips)."""
    count = full_mip_count(width, height) if mips else 1
    return HEADER_SIZE + sum(level_size(w, h, fmt) for w, h in mip_dims(width, height, count))


def build_header(width: int, height: int, fmt: str, mip_count: int) -> bytes:
    """Build the 128-byte DDS header (magic included) for a 2D block-compressed texture.

    Matches nvcompress's layout field for field except ``dwReserved1``, left at zero
    (nvtt writes its 'UVER'/'NVTT' tags there; X-Plane ignores the field).
    """
    fmt = check_format(fmt)
    if mip_count < 1 or mip_count > full_mip_count(width, height):
        raise ValueError(f"mip_count {mip_count} out of range for {width}x{height}")
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE
    caps = DDSCAPS_TEXTURE
    if mip_count > 1:
        flags |= DDSD_MIPMAPCOUNT
        caps |= DDSCAPS_COMPLEX | DDSCAPS_MIPMAP
    return _HEADER_STRUCT.pack(
        MAGIC,
        124,
        flags,
        height,
        width,
        level_size(width, height, fmt),  # dwPitchOrLinearSize: bytes of the top level
        0,  # dwDepth
        mip_count,
        *([0] * 11),  # dwReserved1
        32,  # DDS_PIXELFORMAT.dwSize
        DDPF_FOURCC,
        FOURCC[fmt],
        0,
        0,
        0,
        0,
        0,  # dwRGBBitCount, masks
        caps,
        0,
        0,
        0,
        0,  # dwCaps2..4, dwReserved2
    )


@dataclass(frozen=True)
class DdsHeader:
    """Fields of a parsed DDS header that matter for orthophoto textures."""

    width: int
    height: int
    fmt: Format
    mip_count: int
    flags: int
    caps: int
    linear_size: int
    reserved1: bytes

    @property
    def level_dims(self) -> list[tuple[int, int]]:
        return mip_dims(self.width, self.height, self.mip_count)

    @property
    def level_sizes(self) -> list[int]:
        return [level_size(w, h, self.fmt) for w, h in self.level_dims]

    @property
    def data_size(self) -> int:
        return sum(self.level_sizes)


def parse_header(data: bytes | bytearray | memoryview) -> DdsHeader:
    """Parse the first 128 bytes of a DDS file; raise ``ValueError`` on anything unsupported."""
    if len(data) < HEADER_SIZE:
        raise ValueError("DDS data shorter than its 128-byte header")
    fields = _HEADER_STRUCT.unpack(bytes(data[:HEADER_SIZE]))
    magic, size, flags, height, width, linear_size, _depth, mip_count = fields[:8]
    reserved1 = struct.pack("<11I", *fields[8:19])
    pf_size, pf_flags, fourcc = fields[19:22]
    caps = fields[27]
    if magic != MAGIC or size != 124 or pf_size != 32:
        raise ValueError("not a DDS file")
    if not pf_flags & DDPF_FOURCC or fourcc not in FORMAT_OF_FOURCC:
        raise ValueError(f"unsupported DDS pixel format {fourcc!r}")
    if not flags & DDSD_MIPMAPCOUNT:
        mip_count = 1
    return DdsHeader(
        width=width,
        height=height,
        fmt=FORMAT_OF_FOURCC[fourcc],
        mip_count=max(1, mip_count),
        flags=flags,
        caps=caps,
        linear_size=linear_size,
        reserved1=reserved1,
    )


def assemble(width: int, height: int, fmt: str, levels: Sequence[bytes]) -> bytes:
    """Concatenate header and block data of successive levels into a DDS file image."""
    fmt = check_format(fmt)
    dims = mip_dims(width, height, len(levels))
    for (w, h), blob in zip(dims, levels, strict=True):
        if len(blob) != level_size(w, h, fmt):
            raise ValueError(
                f"level {w}x{h} has {len(blob)} bytes, expected {level_size(w, h, fmt)}"
            )
    return b"".join((build_header(width, height, fmt, len(levels)), *levels))


def split_levels(data: bytes | bytearray | memoryview) -> tuple[DdsHeader, list[memoryview]]:
    """Return the parsed header and one zero-copy view per mip level."""
    header = parse_header(data)
    view = memoryview(data)
    if len(view) < HEADER_SIZE + header.data_size:
        raise ValueError("DDS data truncated")
    levels: list[memoryview] = []
    offset = HEADER_SIZE
    for size in header.level_sizes:
        levels.append(view[offset : offset + size])
        offset += size
    return header, levels
