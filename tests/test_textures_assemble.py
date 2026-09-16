"""Assembling a 4096² texture from 256 chunks: paste order, parent fallback, neighbour fill."""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import pytest
from PIL import Image

from orthostudio.errors import OsxpError
from orthostudio.imagery.chunks import ChunkStatus
from orthostudio.imagery.grid import TextureId
from orthostudio.textures import assemble
from orthostudio.textures.assemble import (
    assemble_texture,
    assemble_texture_detailed,
    chunk_origin,
    decode_tile,
    parent_fallback,
)


@dataclass
class Entry:
    status: int
    data: bytes = b""
    content_type: str = "image/png"
    fetched_at: int = 0


@dataclass
class Container:
    entries: list[Entry]


def _encode(rgb: np.ndarray, fmt: str = "PNG", **kw: object) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, fmt, **kw)
    return buf.getvalue()


def _synthetic_texture(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:4096, 0:4096]
    rgb = np.empty((4096, 4096, 3), dtype=np.uint8)
    rgb[:, :, 0] = (xx // 16) % 256
    rgb[:, :, 1] = (yy // 16) % 256
    rgb[:, :, 2] = rng.integers(0, 256, size=(4096, 4096), dtype=np.uint8)
    return rgb


def _split(rgb: np.ndarray, fmt: str = "PNG", **kw: object) -> list[bytes]:
    out = []
    for i in range(256):
        x0, y0 = chunk_origin(i)
        out.append(_encode(rgb[y0 : y0 + 256, x0 : x0 + 256], fmt, **kw))
    return out


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return 99.0 if mse == 0 else 10 * np.log10(255.0**2 / mse)


@pytest.fixture(scope="module")
def synthetic() -> np.ndarray:
    return _synthetic_texture()


@pytest.fixture(scope="module")
def png_chunks(synthetic: np.ndarray) -> list[bytes]:
    return _split(synthetic)


def test_chunk_origin_is_row_major() -> None:
    assert chunk_origin(0) == (0, 0)
    assert chunk_origin(1) == (256, 0)
    assert chunk_origin(16) == (0, 256)
    assert chunk_origin(255) == (3840, 3840)
    with pytest.raises(ValueError):
        chunk_origin(256)


def test_decode_tile_modes_and_errors() -> None:
    rgb = np.full((256, 256, 3), (10, 20, 30), dtype=np.uint8)
    assert np.array_equal(decode_tile(_encode(rgb)), rgb)
    assert np.array_equal(decode_tile(_encode(rgb, "JPEG", quality=100)), rgb)
    grey = _encode(np.full((256, 256), 77, dtype=np.uint8))
    assert np.array_equal(decode_tile(grey), np.full((256, 256, 3), 77, dtype=np.uint8))
    rgba = np.dstack([rgb, np.full((256, 256), 9, dtype=np.uint8)])
    assert decode_tile(_encode(rgba)).shape == (256, 256, 3)
    with pytest.raises(OsxpError) as exc:
        decode_tile(b"<html>not an image</html>")
    assert exc.value.code == "IMG_TILE_CORRUPTED"
    with pytest.raises(OsxpError) as exc:
        decode_tile(_encode(np.zeros((128, 128, 3), dtype=np.uint8)))
    assert exc.value.code == "IMG_TILE_CORRUPTED"


def test_assemble_png_round_trip(synthetic: np.ndarray, png_chunks: list[bytes]) -> None:
    container = Container([Entry(ChunkStatus.OK, d) for d in png_chunks])
    rgb, missing = assemble_texture(container)
    assert rgb.shape == (4096, 4096, 3) and rgb.dtype == np.uint8
    assert missing == []
    assert np.array_equal(rgb, synthetic)


def test_wrong_entry_count_is_rejected() -> None:
    with pytest.raises(ValueError):
        assemble_texture(Container([Entry(ChunkStatus.OK, b"")] * 255))


def test_missing_chunk_without_fallback_gets_neighbour_mean(
    synthetic: np.ndarray, png_chunks: list[bytes]
) -> None:
    entries = [Entry(ChunkStatus.OK, d) for d in png_chunks]
    entries[17] = Entry(ChunkStatus.MISSING)  # row 1, col 1: eight neighbours available
    entries[255] = Entry(ChunkStatus.PLACEHOLDER)  # corner: three neighbours
    result = assemble_texture_detailed(Container(entries))
    assert result.unfilled == [17, 255] and result.from_fallback == []
    assert result.missing == [17, 255] and not result.complete
    x0, y0 = chunk_origin(17)
    block = result.rgb[y0 : y0 + 256, x0 : x0 + 256]
    assert (block == block[0, 0]).all(), "filled chunk is a flat colour"
    neighbours = [
        synthetic[r * 256 : (r + 1) * 256, c * 256 : (c + 1) * 256].reshape(-1, 3).mean(axis=0)
        for r in (0, 1, 2)
        for c in (0, 1, 2)
        if (r, c) != (1, 1)
    ]
    expected = np.rint(np.mean(neighbours, axis=0)).astype(np.uint8)
    assert np.array_equal(block[0, 0], expected)
    # everything else is untouched
    mask = np.ones((16, 16), dtype=bool)
    mask[1, 1] = mask[15, 15] = False
    big = np.repeat(np.repeat(mask, 256, axis=0), 256, axis=1)
    assert np.array_equal(result.rgb[big], synthetic[big])


def test_all_missing_gives_mid_grey() -> None:
    rgb, missing = assemble_texture(Container([Entry(ChunkStatus.ERROR)] * 256))
    assert missing == list(range(256))
    assert (rgb == 128).all()


def test_corrupted_ok_chunk_behaves_like_error(png_chunks: list[bytes]) -> None:
    entries = [Entry(ChunkStatus.OK, d) for d in png_chunks]
    entries[3] = Entry(ChunkStatus.OK, b"garbage")
    calls: list[int] = []

    def fallback(i: int) -> np.ndarray | None:
        calls.append(i)
        return np.full((256, 256, 3), 200, dtype=np.uint8)

    result = assemble_texture_detailed(Container(entries), fallback)
    assert calls == [3] and result.from_fallback == [3] and result.unfilled == []
    assert result.complete and result.missing == [3]
    assert (result.rgb[0:256, 768:1024] == 200).all()


def test_fallback_shape_is_checked(png_chunks: list[bytes]) -> None:
    entries = [Entry(ChunkStatus.OK, d) for d in png_chunks]
    entries[0] = Entry(ChunkStatus.MISSING)
    with pytest.raises(ValueError):
        assemble_texture(Container(entries), lambda _i: np.zeros((128, 128, 3), dtype=np.uint8))


# ------------------------------------------------------------------- parent fallback (Ortho4XP)


def _quadrant_parent(colours: list[tuple[int, int, int]]) -> np.ndarray:
    """256² parent with four flat quadrants: TL, TR, BL, BR."""
    rgb = np.empty((256, 256, 3), dtype=np.uint8)
    rgb[:128, :128] = colours[0]
    rgb[:128, 128:] = colours[1]
    rgb[128:, :128] = colours[2]
    rgb[128:, 128:] = colours[3]
    return rgb


def test_parent_fallback_crops_the_right_quadrant() -> None:
    t = TextureId(til_x=8448, til_y=6016, zl=14, provider="BI")
    parent = _quadrant_parent([(10, 0, 0), (0, 20, 0), (0, 0, 30), (40, 40, 40)])
    asked: list[tuple[int, int, int]] = []

    def get_parent(x: int, y: int, zl: int) -> bytes | None:
        asked.append((x, y, zl))
        return _encode(parent) if zl == 13 else None

    fb = parent_fallback(t, get_parent)
    # chunk 0 = (8448, 6016): parent (4224, 3008); 8448 - 2*4224 = 0, 6016 - 2*3008 = 0 -> TL
    tl = fb(0)
    assert tl is not None and (tl == (10, 0, 0)).all()
    assert asked == [(4224, 3008, 13)]
    # chunk 1 = (8449, 6016): x offset (8449 - 8448) * 256 // 2 = 128 -> TR
    tr = fb(1)
    assert tr is not None and (tr == (0, 20, 0)).all()
    # chunk 17 = (8449, 6017) -> BR
    br = fb(17)
    assert br is not None and (br == (40, 40, 40)).all()
    # chunk 16 = (8448, 6017) -> BL
    bl = fb(16)
    assert bl is not None and (bl == (0, 0, 30)).all()


def test_parent_fallback_walks_up_five_levels_only() -> None:
    t = TextureId(til_x=8448, til_y=6016, zl=14, provider="BI")
    asked: list[tuple[int, int, int]] = []

    def none(x: int, y: int, zl: int) -> bytes | None:
        asked.append((x, y, zl))
        return None

    assert parent_fallback(t, none)(0) is None
    assert asked == [
        (4224, 3008, 13),
        (2112, 1504, 12),
        (1056, 752, 11),
        (528, 376, 10),
        (264, 188, 9),
    ]
    assert len(asked) == assemble.MAX_PARENT_LEVELS == 5


def test_parent_fallback_upsamples_bicubically_at_depth_two() -> None:
    t = TextureId(til_x=16, til_y=32, zl=5, provider="X")
    parent = np.zeros((256, 256, 3), dtype=np.uint8)
    parent[:, :, 0] = np.arange(256, dtype=np.uint8)[None, :]  # horizontal ramp

    def get_parent(x: int, y: int, zl: int) -> bytes | None:
        return _encode(parent) if zl == 3 else None

    # chunk 3 = (19, 32): d=2 parent (4, 8); x0 = (19 - 4*4) * 256 // 4 = 192, side 64
    out = parent_fallback(t, get_parent)(3)
    assert out is not None
    expected = np.asarray(
        Image.fromarray(parent).crop((192, 0, 256, 64)).resize((256, 256), Image.BICUBIC)
    )
    assert np.array_equal(out, expected)
    assert 190 <= out[128, 0, 0] <= 196 and out[128, 255, 0] >= 250


def test_parent_fallback_skips_undecodable_parent() -> None:
    t = TextureId(til_x=0, til_y=0, zl=3, provider="X")
    bodies = {2: b"broken", 1: _encode(np.full((256, 256, 3), 5, dtype=np.uint8))}
    out = parent_fallback(t, lambda x, y, zl: bodies.get(zl))(0)
    assert out is not None and (out == 5).all()


def test_assemble_reports_fallback_and_unfilled_separately(png_chunks: list[bytes]) -> None:
    t = TextureId(til_x=8448, til_y=6016, zl=14, provider="BI")
    entries = [Entry(ChunkStatus.OK, d) for d in png_chunks]
    entries[5] = Entry(ChunkStatus.MISSING)
    entries[9] = Entry(ChunkStatus.MISSING)
    parent = _encode(np.full((256, 256, 3), 66, dtype=np.uint8))

    def get_parent(x: int, y: int, zl: int) -> bytes | None:
        return parent if (x, y, zl) == (4226, 3008, 13) else None  # parent of chunk 5 only

    result = assemble_texture_detailed(Container(entries), parent_fallback(t, get_parent))
    assert result.from_fallback == [5] and result.unfilled == [9]
    assert result.missing == [5, 9]
    assert (result.rgb[0:256, 1280:1536] == 66).all()
