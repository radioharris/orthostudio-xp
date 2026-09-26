# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""DDS header and container tests (sizes are those of Ortho4XP / nvcompress output)."""

from __future__ import annotations

import pytest

from orthostudio.textures import dds

# 128-byte header of the Ortho4XP reference texture 5952_8416_BI14.dds (DXT1, 4096², 13 mips).
REF_DXT1_HEADER = (
    bytes.fromhex("444453207c00000007100a00001000000010000000008000000000000d000000")
    + bytes(28)
    + b"UVER"
    + bytes(4)
    + b"NVTT"
    + bytes([0, 1, 2, 0])
    + (32).to_bytes(4, "little")
    + (4).to_bytes(4, "little")
    + b"DXT1"
    + bytes(20)
    + (0x401008).to_bytes(4, "little")
    + bytes(16)
)
REF_DXT5_HEADER = REF_DXT1_HEADER.replace(b"DXT1", b"DXT5").replace(
    (8388608).to_bytes(4, "little"), (16777216).to_bytes(4, "little")
)


def _zero_reserved1(header: bytes) -> bytes:
    return header[:32] + bytes(44) + header[76:]


def test_sizes_of_a_4096_texture() -> None:
    assert dds.full_mip_count(4096, 4096) == 13
    assert dds.expected_size(4096, 4096, "bc1") == 11_184_952
    assert dds.expected_size(4096, 4096, "bc3") == 22_369_776
    assert dds.expected_size(4096, 4096, "bc1", mips=False) == 128 + 8_388_608
    assert dds.level_size(4096, 4096, "bc1") == 8_388_608
    assert dds.level_size(4096, 4096, "bc3") == 16_777_216


def test_small_and_partial_blocks() -> None:
    assert dds.level_size(1, 1, "bc1") == 8
    assert dds.level_size(2, 2, "bc3") == 16
    assert dds.level_size(6, 6, "bc1") == 4 * 8
    assert dds.expected_size(64, 64, "bc1") == 2872  # matches nvcompress on a 64x64 input
    assert dds.mip_dims(8, 2, dds.full_mip_count(8, 2)) == [(8, 2), (4, 1), (2, 1), (1, 1)]
    assert dds.full_mip_count(1, 1) == 1


def test_header_matches_reference_except_writer_tag() -> None:
    assert len(REF_DXT1_HEADER) == 128
    built = dds.build_header(4096, 4096, "bc1", 13)
    assert len(built) == dds.HEADER_SIZE
    assert built == _zero_reserved1(REF_DXT1_HEADER)
    assert dds.build_header(4096, 4096, "bc3", 13) == _zero_reserved1(REF_DXT5_HEADER)


def test_parse_reference_header() -> None:
    h = dds.parse_header(REF_DXT5_HEADER)
    assert (h.width, h.height, h.fmt, h.mip_count) == (4096, 4096, "bc3", 13)
    assert h.linear_size == 16_777_216
    assert h.flags & dds.DDSD_MIPMAPCOUNT and h.flags & dds.DDSD_LINEARSIZE
    assert h.caps == dds.DDSCAPS_TEXTURE | dds.DDSCAPS_COMPLEX | dds.DDSCAPS_MIPMAP
    assert h.reserved1[28:32] == b"UVER" and h.reserved1[36:40] == b"NVTT"
    assert h.data_size == 22_369_776 - 128
    assert h.level_dims[0] == (4096, 4096) and h.level_dims[-1] == (1, 1)


def test_header_round_trip_without_mips() -> None:
    h = dds.parse_header(dds.build_header(256, 128, "bc1", 1))
    assert (h.width, h.height, h.fmt, h.mip_count) == (256, 128, "bc1", 1)
    assert not h.flags & dds.DDSD_MIPMAPCOUNT
    assert h.caps == dds.DDSCAPS_TEXTURE


def test_assemble_and_split_round_trip() -> None:
    levels = [
        bytes([k]) * dds.level_size(w, h, "bc3") for k, (w, h) in enumerate(dds.mip_dims(8, 8, 4))
    ]
    data = dds.assemble(8, 8, "bc3", levels)
    assert len(data) == dds.expected_size(8, 8, "bc3")
    header, parts = dds.split_levels(data)
    assert header.mip_count == 4
    assert [bytes(p) for p in parts] == levels


def test_errors() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        dds.build_header(4, 4, "bc7", 1)
    with pytest.raises(ValueError, match="mip_count"):
        dds.build_header(4, 4, "bc1", 5)
    with pytest.raises(ValueError, match="expected"):
        dds.assemble(4, 4, "bc1", [b"\0" * 7])
    with pytest.raises(ValueError, match="not a DDS"):
        dds.parse_header(b"XXXX" + bytes(124))
    with pytest.raises(ValueError, match="truncated"):
        dds.split_levels(dds.build_header(4, 4, "bc1", 1) + b"\0" * 4)
