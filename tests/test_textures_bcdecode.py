"""BC1/BC3 decoder against hand-built blocks."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from orthostudio.textures import dds
from orthostudio.textures.bcdecode import decode_blocks, decode_dds

RED, BLUE = 0xF800, 0x001F  # 5:6:5


def _bc1(c0: int, c1: int, index: int) -> bytes:
    return struct.pack("<HHI", c0, c1, int(f"{index:02b}" * 16, 2))


def test_bc1_four_colour_palette() -> None:
    assert np.all(decode_blocks(_bc1(RED, BLUE, 0), 4, 4, "bc1").reshape(-1, 4) == (255, 0, 0, 255))
    assert np.all(decode_blocks(_bc1(RED, BLUE, 1), 4, 4, "bc1").reshape(-1, 4) == (0, 0, 255, 255))
    assert np.all(
        decode_blocks(_bc1(RED, BLUE, 2), 4, 4, "bc1").reshape(-1, 4) == (170, 0, 85, 255)
    )
    assert np.all(
        decode_blocks(_bc1(RED, BLUE, 3), 4, 4, "bc1").reshape(-1, 4) == (85, 0, 170, 255)
    )


def test_bc1_three_colour_mode_has_transparent_black() -> None:
    assert np.all(
        decode_blocks(_bc1(BLUE, RED, 2), 4, 4, "bc1").reshape(-1, 4) == (128, 0, 128, 255)
    )
    assert np.all(decode_blocks(_bc1(BLUE, RED, 3), 4, 4, "bc1").reshape(-1, 4) == (0, 0, 0, 0))


def test_bc1_pixel_order_and_565_expansion() -> None:
    # pixel i uses bits 2i..2i+1, row-major inside the block
    indices = sum(((i % 4) << (2 * i)) for i in range(16))
    block = struct.pack("<HHI", 0x0020, 0x0000, indices)  # c0 = green 1/63 -> 4 after expansion
    px = decode_blocks(block, 4, 4, "bc1")
    assert np.array_equal(px[0, :, 1], [4, 0, 3, 1])
    assert np.array_equal(px[3, :, 1], [4, 0, 3, 1])


def test_bc3_alpha_modes() -> None:
    def alpha_block(a0: int, a1: int, index: int) -> bytes:
        bits = int(f"{index:03b}" * 16, 2)
        return bytes([a0, a1]) + bits.to_bytes(6, "little")

    colour = _bc1(RED, BLUE, 0)
    for index, expected in ((0, 255), (1, 0), (2, 219), (7, 36)):
        px = decode_blocks(alpha_block(255, 0, index) + colour, 4, 4, "bc3")
        assert np.all(px[:, :, 3] == expected) and np.all(px[:, :, :3] == (255, 0, 0))
    for index, expected in ((0, 0), (1, 250), (2, 50), (6, 0), (7, 255)):
        px = decode_blocks(alpha_block(0, 250, index) + colour, 4, 4, "bc3")
        assert np.all(px[:, :, 3] == expected)


def test_partial_blocks_are_cropped() -> None:
    blocks = _bc1(RED, BLUE, 0) * 4
    px = decode_blocks(blocks, 6, 5, "bc1")
    assert px.shape == (5, 6, 4)
    with pytest.raises(ValueError, match="expected"):
        decode_blocks(blocks, 4, 4, "bc1")


def test_decode_dds_levels() -> None:
    levels = [
        _bc1(RED, BLUE, k % 4) * (dds.level_size(w, h, "bc1") // 8)
        for k, (w, h) in enumerate(dds.mip_dims(8, 8, 4))
    ]
    out = decode_dds(dds.assemble(8, 8, "bc1", levels))
    assert [o.shape for o in out] == [(8, 8, 4), (4, 4, 4), (2, 2, 4), (1, 1, 4)]
    assert tuple(out[0][0, 0]) == (255, 0, 0, 255) and tuple(out[1][0, 0]) == (0, 0, 255, 255)
    assert tuple(out[3][0, 0]) == (85, 0, 170, 255)
