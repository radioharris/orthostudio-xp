"""``dem/cog.py``: the window of a tiled GeoTIFF, read from a file or over byte ranges.

No network: a small tiled TIFF is built here, byte for byte, and read back. Spec:
``docs/specs/dem.md`` section 3.0b.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

from orthostudio.dem.cog import (
    HEADER_BYTES,
    read_header,
    read_window,
    window_for,
    write_geotiff,
)
from orthostudio.dem.raster import read_elevation_from_file
from orthostudio.errors import OsxpError

TILE = 16
NORTH, WEST, STEP = -9.0, -60.0, 0.001


def tiled_tiff(values: np.ndarray, *, tile: int = TILE, deflate: bool = True) -> bytes:
    """A tiled float32 GeoTIFF of ``values``, as the published reliefs are written."""
    rows, cols = values.shape
    across, down = (cols + tile - 1) // tile, (rows + tile - 1) // tile
    blobs = []
    for ty in range(down):
        for tx in range(across):
            block = np.full((tile, tile), -9999.0, dtype="<f4")
            part = values[ty * tile : ty * tile + tile, tx * tile : tx * tile + tile]
            block[: part.shape[0], : part.shape[1]] = part
            raw = block.tobytes()
            blobs.append(zlib.compress(raw, 6) if deflate else raw)
    offsets, counts, at = [], [], 8
    for blob in blobs:
        offsets.append(at)
        counts.append(len(blob))
        at += len(blob)
    ifd_at = at
    geo = [1, 1, 0, 2, 1024, 0, 1, 2, 2048, 0, 1, 4326]
    entries = [
        (256, 4, 1, struct.pack("<I", cols)),
        (257, 4, 1, struct.pack("<I", rows)),
        (258, 3, 1, struct.pack("<HH", 32, 0)),
        (259, 3, 1, struct.pack("<HH", 8 if deflate else 1, 0)),
        (262, 3, 1, struct.pack("<HH", 1, 0)),
        (277, 3, 1, struct.pack("<HH", 1, 0)),
        (284, 3, 1, struct.pack("<HH", 1, 0)),
        (322, 3, 1, struct.pack("<HH", tile, 0)),
        (323, 3, 1, struct.pack("<HH", tile, 0)),
        (324, 4, len(offsets), struct.pack(f"<{len(offsets)}I", *offsets)),
        (325, 4, len(counts), struct.pack(f"<{len(counts)}I", *counts)),
        (339, 3, 1, struct.pack("<HH", 3, 0)),
        (33550, 12, 3, struct.pack("<3d", STEP, STEP, 0.0)),
        (33922, 12, 6, struct.pack("<6d", 0.0, 0.0, 0.0, WEST, NORTH, 0.0)),
        (34735, 3, len(geo), struct.pack(f"<{len(geo)}H", *geo)),
        (42113, 2, 6, b"-9999\x00"),
    ]
    values_at = ifd_at + 2 + 12 * len(entries) + 4
    body, tail = bytearray(struct.pack("<H", len(entries))), bytearray()
    for tag, typ, count, payload in entries:
        if len(payload) <= 4:
            inline = payload + b"\x00" * (4 - len(payload))
        else:
            inline = struct.pack("<I", values_at + len(tail))
            tail += payload
        body += struct.pack("<HHI", tag, typ, count) + inline
    body += struct.pack("<I", 0)
    return b"II" + struct.pack("<HI", 42, ifd_at) + b"".join(blobs) + bytes(body) + bytes(tail)


def ground(rows: int = 64, cols: int = 80) -> np.ndarray:
    a = np.arange(rows * cols, dtype=np.float32).reshape(rows, cols) % 511 + 0.25
    a[3:7, 5:9] = -9999.0  # a hole, as a published relief has over water
    return a


def ranges_of(blob: bytes):
    """A ``ranges`` function over bytes in hand, counting what it was asked for."""
    asked: list[tuple[int, int]] = []

    def read(parts):
        asked.extend(parts)
        return [blob[at : at + size] for at, size in parts]

    return read, asked


# -- the header ------------------------------------------------------------------------------


def test_header_says_the_geometry_and_where_the_tiles_lie() -> None:
    blob = tiled_tiff(ground())
    info = read_header(blob)
    assert (info.width, info.height) == (80, 64)
    assert (info.tile_width, info.tile_height) == (TILE, TILE)
    assert info.across == 5 and len(info.offsets) == 5 * 4
    assert info.epsg == 4326 and info.nodata == -9999.0 and info.compression == 8
    assert info.step_x == pytest.approx(STEP) and info.west == pytest.approx(WEST)
    assert info.covers(NORTH - 0.01, WEST + 0.01) and not info.covers(NORTH + 1, WEST)


def test_a_file_this_reader_cannot_use_says_so() -> None:
    with pytest.raises(OsxpError) as not_tiff:
        read_header(b"not a tiff at all, really not")
    assert not_tiff.value.code == "DEM_FILE_UNREADABLE"
    with pytest.raises(OsxpError):
        read_header(b"II" + struct.pack("<HI", 42, 8) + b"\x00" * 40)  # no tiles
    with pytest.raises(OsxpError):
        read_header(b"")


# -- the window ------------------------------------------------------------------------------


def test_the_window_is_the_box_on_the_files_own_grid() -> None:
    info = read_header(tiled_tiff(ground()))
    south, north = NORTH - 0.02, NORTH - 0.01
    west, east = WEST + 0.01, WEST + 0.03
    w = window_for(info, south, north, west, east)
    assert w.row0 == 10 and w.col0 == 10 and w.rows == 10 and w.cols == 20
    assert w.north == pytest.approx(NORTH - 0.01) and w.west == pytest.approx(WEST + 0.01)
    # the tiles it falls in, and what they weigh, before a single byte travels
    assert sorted(w.tiles(info)) == [0, 1, 5, 6]
    assert w.bytes_needed(info) == sum(info.counts[i] for i in (0, 1, 5, 6))


def test_a_box_outside_the_file_is_refused() -> None:
    info = read_header(tiled_tiff(ground()))
    with pytest.raises(OsxpError) as excinfo:
        window_for(info, 10.0, 11.0, 10.0, 11.0)
    assert excinfo.value.code == "DEM_TILE_UNAVAILABLE"


# -- reading ---------------------------------------------------------------------------------


def test_only_the_tiles_of_the_window_are_read() -> None:
    values = ground()
    blob = tiled_tiff(values)
    info = read_header(blob)
    w = window_for(info, NORTH - 0.02, NORTH - 0.01, WEST + 0.01, WEST + 0.03)
    read, asked = ranges_of(blob)
    got = read_window(info, w, read)
    assert got.shape == (w.rows, w.cols)
    assert np.array_equal(got, values[w.row0 : w.row0 + w.rows, w.col0 : w.col0 + w.cols])
    assert len(asked) == 4  # four tiles out of twenty
    assert sum(size for _at, size in asked) < len(blob) // 2


def test_a_window_over_a_hole_keeps_the_hole() -> None:
    values = ground()
    blob = tiled_tiff(values)
    info = read_header(blob)
    w = window_for(info, NORTH - 0.008, NORTH - 0.002, WEST + 0.004, WEST + 0.010)
    read, _asked = ranges_of(blob)
    got = read_window(info, w, read)
    assert (got == info.nodata).any()
    assert np.array_equal(got, values[w.row0 : w.row0 + w.rows, w.col0 : w.col0 + w.cols])


def test_an_uncompressed_file_is_read_too() -> None:
    values = ground(32, 32)
    blob = tiled_tiff(values, deflate=False)
    info = read_header(blob)
    assert info.compression == 1
    w = window_for(info, NORTH - 0.03, NORTH, WEST, WEST + 0.03)
    read, _ = ranges_of(blob)
    assert np.array_equal(read_window(info, w, read), values[: w.rows, : w.cols])


# -- writing what was read -------------------------------------------------------------------


def test_the_window_written_back_is_read_by_the_engines_own_reader(tmp_path: Path) -> None:
    """What a square of ANADEM becomes on disk: a small GeoTIFF the rest of the engine opens
    like any other elevation file, holes included."""
    values = ground(40, 40)
    out = tmp_path / "S10W060_ANADEM.tif"
    write_geotiff(out, values, north=-9.0, west=-60.0, step_x=STEP, step_y=STEP, nodata=-9999.0)
    assert out.stat().st_size < values.nbytes  # deflated
    read = read_elevation_from_file(out, -10, -60)
    back = np.asarray(read.alt_dem, dtype=np.float32)
    assert read.epsg == 4326 and (read.nxdem, read.nydem) == (40, 40)
    assert np.array_equal(back[values != -9999.0], values[values != -9999.0])
    assert (back[values == -9999.0] == read.nodata).all()  # the holes, said in our own words


def test_the_header_of_a_published_relief_fits_in_one_read() -> None:
    """A zone of 2 GB has 2 596 tiles: two tables of 10 KB after the directory. The first read
    must hold them, or the reader would ask twice for every square."""
    assert HEADER_BYTES >= 2 * 2596 * 4 + 4096
