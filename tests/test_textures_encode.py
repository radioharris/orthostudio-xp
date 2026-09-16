"""encode_dds: sizes, round trips on synthetic images, encoder chain and invariants."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from orthostudio.textures import dds, encode
from orthostudio.textures.bcdecode import decode_blocks, decode_dds
from orthostudio.textures.refine import refine_blocks

pytestmark = pytest.mark.skipif(
    "ispc" not in encode.available_encoders(), reason="needs ispc_texcomp"
)


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return 99.0 if mse == 0 else 10 * np.log10(255.0**2 / mse)


def _gradient(n: int = 64) -> np.ndarray:
    img = np.zeros((n, n, 4), np.uint8)
    ramp = np.linspace(0, 255, n).astype(np.uint8)
    img[:, :, 0] = ramp[None, :]
    img[:, :, 1] = ramp[:, None]
    img[:, :, 2] = 128
    img[:, :, 3] = np.random.default_rng(0).integers(0, 256, (n, n))
    return img


@pytest.mark.parametrize("fmt", ["bc1", "bc3"])
def test_round_trip_synthetic(fmt: str) -> None:
    img = _gradient()
    data = encode.encode_dds(img, fmt, encoder="ispc")
    assert len(data) == dds.expected_size(64, 64, fmt)
    header = dds.parse_header(data)
    assert (header.width, header.height, header.fmt, header.mip_count) == (64, 64, fmt, 7)
    levels = decode_dds(data)
    assert len(levels) == 7 and levels[-1].shape == (1, 1, 4)
    assert _psnr(levels[0][:, :, :3], img[:, :, :3]) > 33
    if fmt == "bc3":
        assert _psnr(levels[0][:, :, 3], img[:, :, 3]) > 28
    else:
        assert np.all(levels[0][:, :, 3] == 255)


def test_no_mips_and_rgb_input() -> None:
    data = encode.encode_dds(_gradient()[:, :, :3], "bc1", mips=False, encoder="ispc")
    assert len(data) == dds.expected_size(64, 64, "bc1", mips=False)
    assert dds.parse_header(data).mip_count == 1


def test_tiny_levels_are_padded_to_a_block() -> None:
    img = np.zeros((2, 2, 4), np.uint8)
    img[:, :, 0] = 200
    img[:, :, 3] = 255
    data = encode.encode_dds(img, "bc1", encoder="ispc")
    assert len(data) == 128 + 8 + 8
    levels = decode_dds(data)
    assert levels[0].shape == (2, 2, 4) and abs(int(levels[0][0, 0, 0]) - 200) <= 8
    assert len(encode.encode_blocks(np.zeros((6, 6, 4), np.uint8), "bc3", encoder="ispc")) == 4 * 16


def test_deterministic() -> None:
    img = _gradient()
    assert encode.encode_dds(img, "bc3", encoder="ispc") == encode.encode_dds(
        img, "bc3", encoder="ispc"
    )


def test_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        encode.encode_dds(_gradient(), "bc7")
    with pytest.raises(ValueError, match="uint8"):
        encode.encode_dds(_gradient().astype(np.uint16), "bc1")
    with pytest.raises(ValueError, match="power-of-two"):
        encode.encode_dds(np.zeros((12, 12, 4), np.uint8), "bc1")
    with pytest.raises(ValueError, match="unknown encoder"):
        encode.encode_dds(_gradient(), "bc1", encoder="bogus")  # type: ignore[arg-type]


def test_encoder_chain_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(encode.NVCOMPRESS_ENV, raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    assert encode.find_nvcompress() is None
    with pytest.raises(encode.EncoderUnavailableError, match="nvcompress not found"):
        encode.encode_dds(_gradient(), "bc1", encoder="nvcompress")
    monkeypatch.setattr(encode, "_ispc", lambda: None)
    assert encode.available_encoders() == []
    with pytest.raises(encode.EncoderUnavailableError, match="pip install ispc-texcomp"):
        encode.encode_dds(_gradient(), "bc1")


def test_force_four_colour_mode() -> None:
    good = struct.pack("<HHI", 0xF800, 0x001F, 0xE4E4E4E4)  # c0 > c1, indices 0,1,2,3 repeated
    inverted = struct.pack("<HHI", 0x001F, 0xF800, 0xE4E4E4E4 ^ 0x55555555)
    assert encode.count_three_colour_blocks(inverted, "bc1") == (1, 0)
    fixed = encode.force_four_colour_mode(inverted, "bc1")
    assert fixed == good
    assert np.array_equal(decode_blocks(fixed, 4, 4, "bc1"), decode_blocks(good, 4, 4, "bc1"))
    equal = struct.pack("<HHI", 0x1234, 0x1234, 0xAAAAAAAA)
    assert encode.count_three_colour_blocks(equal, "bc1") == (0, 1)
    assert encode.force_four_colour_mode(equal, "bc1") == struct.pack("<HHI", 0x1234, 0x1234, 0)
    # BC3: the colour block is the second half
    alpha = bytes(8)
    assert encode.force_four_colour_mode(alpha + inverted, "bc3") == alpha + good
    assert encode.count_three_colour_blocks(
        encode.encode_blocks(_gradient(), "bc1", encoder="ispc"), "bc1"
    ) == (0, 0)


def test_refine_never_increases_error() -> None:
    rng = np.random.default_rng(1)
    img = rng.integers(0, 256, (64, 64, 4), np.uint8)
    img[:, :, 3] = 255
    base = encode.encode_blocks(img, "bc1", encoder="ispc")
    refined = refine_blocks(base, img, "bc1", passes=2)
    assert len(refined) == len(base)
    err_base = np.sum(
        (decode_blocks(base, 64, 64, "bc1")[:, :, :3].astype(float) - img[:, :, :3]) ** 2
    )
    err_ref = np.sum(
        (decode_blocks(refined, 64, 64, "bc1")[:, :, :3].astype(float) - img[:, :, :3]) ** 2
    )
    assert err_ref <= err_base
    assert encode.count_three_colour_blocks(refined, "bc1") == (0, 0)
    assert refine_blocks(base, img, "bc1", passes=0) == base
    via_api = encode.encode_dds(img, "bc3", mips=False, encoder="ispc", refine_passes=1)
    assert len(via_api) == dds.expected_size(64, 64, "bc3", mips=False)


def test_single_colour_tables_are_within_one_code() -> None:
    for bits in (5, 6):
        e0, e1 = (t.astype(int) for t in encode._single_colour_table(bits))
        x0 = (e0 << (8 - bits)) | (e0 >> (2 * bits - 8))
        x1 = (e1 << (8 - bits)) | (e1 >> (2 * bits - 8))
        interp = (2 * x0 + x1 + 1) // 3
        assert np.abs(interp - np.arange(256)).max() <= 1


def test_flat_blocks_use_optimal_endpoints() -> None:
    img = np.empty((8, 8, 4), np.uint8)
    img[:] = (100, 50, 201, 255)
    raw = encode._encode_blocks_ispc(img, "bc1")
    fixed = encode.encode_blocks(img, "bc1", encoder="ispc")
    target = np.array((100, 50, 201))
    err_raw = np.abs(decode_blocks(raw, 8, 8, "bc1")[:, :, :3].astype(int) - target).max()
    err_fixed = np.abs(decode_blocks(fixed, 8, 8, "bc1")[:, :, :3].astype(int) - target).max()
    assert err_fixed <= 1 <= err_raw
    assert encode.count_three_colour_blocks(fixed, "bc1") == (0, 0)
    img[0, 0, 0] = 101  # block 0 is no longer uniform and is left to ispc; blocks 1-3 are fixed
    partial = encode.encode_flat_blocks(raw, img, "bc1")
    assert partial[:8] == raw[:8] and partial[8:] == fixed[8:]
    with pytest.raises(ValueError, match="multiple of 4"):
        encode.encode_flat_blocks(raw, np.zeros((6, 6, 4), np.uint8), "bc1")
