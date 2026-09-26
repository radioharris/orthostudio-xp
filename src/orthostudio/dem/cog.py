# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Read the window of a tile out of a tiled GeoTIFF, from a file or over HTTP byte ranges.

A relief published as one huge tiled GeoTIFF cannot be read the way the other sources are. The
ANADEM terrain model of South America (``dem/sources.py``) is 52 files of about 2 GB, one per MGRS
zone; the square of one X-Plane tile is 37 MB of it. The files are tiled 512 by 512 and deflated,
and their server serves byte ranges, so the tiles the square needs are the only bytes that travel.
The same reader opens a huge file the user has on his disk, which Pillow would decode whole.

Nothing here knows about a source or a provider: it is given the bytes of a header, then a way to
read byte ranges, and it answers the window as a float32 array with its geometry.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from orthostudio.errors import OsxpError

__all__ = [
    "HEADER_BYTES",
    "TiffInfo",
    "Window",
    "read_header",
    "read_window",
    "window_for",
    "write_geotiff",
]

HEADER_BYTES = 262_144
"""What to read of a file before anything else: the IFD, and the tables of tile offsets and byte
counts it points at. A zone of 2 GB has 2 596 tiles, so two tables of 10 KB, written right after
the directory: a quarter of a megabyte is room to spare, and one request."""

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 16: 8}
_TILE_WIDTH, _TILE_LENGTH, _TILE_OFFSETS, _TILE_COUNTS = 322, 323, 324, 325
_PIXEL_SCALE, _TIEPOINT, _GEO_KEYS, _NODATA = 33550, 33922, 34735, 42113


@dataclass(frozen=True, slots=True)
class TiffInfo:
    """What a tiled GeoTIFF says about itself, and where each of its tiles lies."""

    width: int
    height: int
    tile_width: int
    tile_height: int
    offsets: tuple[int, ...]
    counts: tuple[int, ...]
    step_x: float
    step_y: float
    west: float
    north: float
    epsg: int
    nodata: float
    compression: int
    predictor: int

    @property
    def across(self) -> int:
        """Tiles per row."""
        return (self.width + self.tile_width - 1) // self.tile_width

    def covers(self, lat: float, lon: float) -> bool:
        """Whether the point is inside the image (its own edge included)."""
        south = self.north - self.height * self.step_y
        east = self.west + self.width * self.step_x
        return self.west <= lon <= east and south <= lat <= self.north


@dataclass(frozen=True, slots=True)
class Window:
    """A rectangle of pixels, and where its first point sits."""

    row0: int
    col0: int
    rows: int
    cols: int
    north: float
    west: float
    step_x: float
    step_y: float

    def tiles(self, info: TiffInfo) -> list[int]:
        """The indices of the file's tiles this window falls in, row by row."""
        first_row, last_row = (
            self.row0 // info.tile_height,
            (self.row0 + self.rows - 1) // info.tile_height,
        )
        first_col, last_col = (
            self.col0 // info.tile_width,
            (self.col0 + self.cols - 1) // info.tile_width,
        )
        return [
            ty * info.across + tx
            for ty in range(first_row, last_row + 1)
            for tx in range(first_col, last_col + 1)
        ]

    def bytes_needed(self, info: TiffInfo) -> int:
        return sum(info.counts[i] for i in self.tiles(info) if i < len(info.counts))


def _bad(path: str, reason: str) -> OsxpError:
    return OsxpError("DEM_FILE_UNREADABLE", context={"path": path, "reason": reason})


def read_header(head: bytes, *, path: str = "<bytes>") -> TiffInfo:
    """Read the first directory of a tiled GeoTIFF out of its first bytes.

    ``head`` is the beginning of the file, :data:`HEADER_BYTES` of it or more. Classic TIFF and
    BigTIFF are both read. A file that is not tiled, or not a single band of float32, raises
    ``DEM_FILE_UNREADABLE`` rather than being half read.
    """
    if len(head) < 16:
        raise _bad(path, "too short to be a TIFF")
    order = head[:2]
    if order not in (b"II", b"MM"):
        raise _bad(path, f"not a TIFF (starts with {head[:2]!r})")
    bo = "<" if order == b"II" else ">"
    magic = struct.unpack(bo + "H", head[2:4])[0]
    if magic == 43:  # BigTIFF
        offset_size = struct.unpack(bo + "H", head[4:6])[0]
        if offset_size != 8:
            raise _bad(path, f"BigTIFF with {offset_size}-byte offsets")
        first = struct.unpack(bo + "Q", head[8:16])[0]
        count = struct.unpack(bo + "Q", head[first : first + 8])[0]
        start, entry_size, off_fmt = first + 8, 20, "Q"
    elif magic == 42:
        first = struct.unpack(bo + "I", head[4:8])[0]
        count = struct.unpack(bo + "H", head[first : first + 2])[0]
        start, entry_size, off_fmt = first + 2, 12, "I"
    else:
        raise _bad(path, f"not a TIFF (magic {magic})")
    if start + count * entry_size > len(head):
        raise _bad(path, "the directory lies beyond the bytes read")

    tags: dict[int, tuple[int, int, bytes]] = {}
    for i in range(count):
        entry = head[start + i * entry_size : start + (i + 1) * entry_size]
        if magic == 43:
            tag, typ, n = struct.unpack(bo + "HHQ", entry[:12])
            raw = entry[12:20]
        else:
            tag, typ, n = struct.unpack(bo + "HHI", entry[:8])
            raw = entry[8:12]
        tags[tag] = (typ, n, raw)

    def values(tag: int, *, needed: bool = True) -> list[Any]:
        if tag not in tags:
            if needed:
                raise _bad(path, f"no tag {tag}")
            return []
        typ, n, raw = tags[tag]
        size = _TYPE_SIZE.get(typ, 0) * n
        if size == 0:
            raise _bad(path, f"tag {tag} has type {typ}")
        if size <= len(raw):
            blob = raw[:size]
        else:
            at = struct.unpack(bo + off_fmt, raw[: struct.calcsize(off_fmt)])[0]
            if at + size > len(head):
                raise _bad(
                    path, f"tag {tag} lies beyond the bytes read ({at + size} > {len(head)})"
                )
            blob = head[at : at + size]
        code = {1: "B", 2: "s", 3: "H", 4: "I", 12: "d", 16: "Q", 11: "f"}.get(typ)
        if code is None:
            raise _bad(path, f"tag {tag} has type {typ}")
        if code == "s":
            return [blob.decode("ascii", "replace").strip("\x00")]
        return list(struct.unpack(bo + f"{n}{code}", blob))

    if _TILE_WIDTH not in tags or _TILE_OFFSETS not in tags:
        raise _bad(path, "not tiled (this reader needs a tiled GeoTIFF)")
    if values(258)[0] != 32 or values(339, needed=False)[:1] != [3]:
        raise _bad(path, "not one band of float32")
    scale = values(_PIXEL_SCALE)
    tie = values(_TIEPOINT)
    if len(scale) < 2 or len(tie) < 6 or scale[0] <= 0 or scale[1] <= 0:
        raise _bad(path, "no usable ModelPixelScale/ModelTiepoint")
    keys = values(_GEO_KEYS, needed=False)
    epsg = 0
    for i in range(4, len(keys) - 3, 4):
        if keys[i] == 2048:  # GeographicTypeGeoKey
            epsg = int(keys[i + 3])
    nodata = float(values(_NODATA, needed=False)[0]) if _NODATA in tags else float("nan")
    return TiffInfo(
        width=int(values(256)[0]),
        height=int(values(257)[0]),
        tile_width=int(values(_TILE_WIDTH)[0]),
        tile_height=int(values(_TILE_LENGTH)[0]),
        offsets=tuple(int(v) for v in values(_TILE_OFFSETS)),
        counts=tuple(int(v) for v in values(_TILE_COUNTS)),
        step_x=float(scale[0]),
        step_y=float(scale[1]),
        west=float(tie[3]),
        north=float(tie[4]),
        epsg=epsg,
        nodata=nodata,
        compression=int(values(259, needed=False)[0]) if 259 in tags else 1,
        predictor=int(values(317, needed=False)[0]) if 317 in tags else 1,
    )


def window_for(info: TiffInfo, south: float, north: float, west: float, east: float) -> Window:
    """The pixels of the image that cover the box, clipped to what the image holds.

    The grid is the file's own: the window starts on a whole pixel, and its first point's
    coordinates are returned, so that nothing is resampled here.
    """
    # A hair of tolerance: (west + 0.01 - west) / 0.001 is 9.999999999 in floating point, and a
    # bare floor would take a pixel more on each side for nothing.
    grain = 1e-9
    col0 = int(np.floor((west - info.west) / info.step_x + grain))
    col1 = int(np.ceil((east - info.west) / info.step_x - grain))
    row0 = int(np.floor((info.north - north) / info.step_y + grain))
    row1 = int(np.ceil((info.north - south) / info.step_y - grain))
    col0, col1 = max(col0, 0), min(col1, info.width)
    row0, row1 = max(row0, 0), min(row1, info.height)
    if col1 <= col0 or row1 <= row0:
        raise OsxpError(
            "DEM_TILE_UNAVAILABLE",
            context={
                "cell": "",
                "source": "tiled GeoTIFF",
                "reason": "the box is outside the file",
            },
        )
    return Window(
        row0=row0,
        col0=col0,
        rows=row1 - row0,
        cols=col1 - col0,
        north=info.north - row0 * info.step_y,
        west=info.west + col0 * info.step_x,
        step_x=info.step_x,
        step_y=info.step_y,
    )


def _decode(blob: bytes, info: TiffInfo, path: str) -> NDArray[np.float32]:
    if info.compression in (8, 32946):
        blob = zlib.decompress(blob)
    elif info.compression != 1:
        raise _bad(path, f"compression {info.compression} is not read here")
    if info.predictor not in (1, 3):
        raise _bad(path, f"predictor {info.predictor}")
    expected = info.tile_width * info.tile_height * 4
    if len(blob) != expected:
        raise _bad(path, f"a tile holds {len(blob)} bytes, not {expected}")
    return np.frombuffer(blob, dtype="<f4").reshape(info.tile_height, info.tile_width)


def read_window(
    info: TiffInfo,
    window: Window,
    ranges: Callable[[Sequence[tuple[int, int]]], Sequence[bytes]],
    *,
    path: str = "<bytes>",
) -> NDArray[np.float32]:
    """The window as a float32 array, read tile by tile through ``ranges``.

    ``ranges`` is given ``(offset, length)`` pairs and answers their bytes in the same order; it
    is where a file read or an HTTP range request lives. A tile that is missing from the file (a
    zone's edge) leaves its part at ``info.nodata``.
    """
    wanted = window.tiles(info)
    asked = [(info.offsets[i], info.counts[i]) for i in wanted if i < len(info.offsets)]
    blobs = list(ranges(asked))
    if len(blobs) != len(asked):
        raise _bad(path, f"{len(blobs)} answers for {len(asked)} ranges")
    out = np.full((window.rows, window.cols), info.nodata, dtype=np.float32)
    for index, blob in zip((i for i in wanted if i < len(info.offsets)), blobs, strict=True):
        ty, tx = divmod(index, info.across)
        block = _decode(blob, info, path)
        top, left = ty * info.tile_height, tx * info.tile_width
        r0 = max(window.row0, top)
        r1 = min(window.row0 + window.rows, top + info.tile_height)
        c0 = max(window.col0, left)
        c1 = min(window.col0 + window.cols, left + info.tile_width)
        if r1 <= r0 or c1 <= c0:
            continue
        out[r0 - window.row0 : r1 - window.row0, c0 - window.col0 : c1 - window.col0] = block[
            r0 - top : r1 - top, c0 - left : c1 - left
        ]
    return out


_STRIP_ROWS = 64
"""Rows per strip of a window written back to disk: a strip of a 3 700 point square is 950 KB
raw, which deflate takes down to a fifth and a reader picks up on its own."""


def write_geotiff(
    path: Any,
    values: NDArray[np.float32],
    *,
    north: float,
    west: float,
    step_x: float,
    step_y: float,
    nodata: float,
    epsg: int = 4326,
) -> None:
    """Write ``values`` as a small deflated GeoTIFF, float32, one band, EPSG ``epsg``.

    What the window of a huge file becomes on disk, so that the rest of the engine reads it as it
    reads any other elevation file: this writes the twelve tags a reader needs and nothing else.
    """
    import pathlib

    rows, cols = values.shape
    strips = [
        zlib.compress(np.ascontiguousarray(values[r : r + _STRIP_ROWS], dtype="<f4").tobytes(), 6)
        for r in range(0, rows, _STRIP_ROWS)
    ]
    offsets, counts, at = [], [], 8  # the header is eight bytes, the strips follow it
    for blob in strips:
        offsets.append(at)
        counts.append(len(blob))
        at += len(blob)

    nodata_text = f"{nodata:g}".encode("ascii") + b"\x00"
    geo_keys = [1, 1, 0, 2, 1024, 0, 1, 2, 2048, 0, 1, epsg]
    # (tag, type, count, payload) with the payload written out of line when it is over four bytes
    entries: list[tuple[int, int, int, bytes]] = [
        (256, 4, 1, struct.pack("<I", cols)),
        (257, 4, 1, struct.pack("<I", rows)),
        (258, 3, 1, struct.pack("<HH", 32, 0)),
        (259, 3, 1, struct.pack("<HH", 8, 0)),
        (262, 3, 1, struct.pack("<HH", 1, 0)),
        (273, 4, len(offsets), struct.pack(f"<{len(offsets)}I", *offsets)),
        (277, 3, 1, struct.pack("<HH", 1, 0)),
        (278, 4, 1, struct.pack("<I", _STRIP_ROWS)),
        (279, 4, len(counts), struct.pack(f"<{len(counts)}I", *counts)),
        (284, 3, 1, struct.pack("<HH", 1, 0)),
        (339, 3, 1, struct.pack("<HH", 3, 0)),
        (33550, 12, 3, struct.pack("<3d", step_x, step_y, 0.0)),
        (33922, 12, 6, struct.pack("<6d", 0.0, 0.0, 0.0, west, north, 0.0)),
        (34735, 3, len(geo_keys), struct.pack(f"<{len(geo_keys)}H", *geo_keys)),
        (42113, 2, len(nodata_text), nodata_text),
    ]
    ifd_at = at
    values_at = ifd_at + 2 + 12 * len(entries) + 4
    body, tail = bytearray(struct.pack("<H", len(entries))), bytearray()
    for tag, typ, count, payload in entries:
        if len(payload) <= 4:
            inline = payload + b"\x00" * (4 - len(payload))
        else:
            inline = struct.pack("<I", values_at + len(tail))
            tail += payload
        body += struct.pack("<HHI", tag, typ, count) + inline
    body += struct.pack("<I", 0)  # no second directory
    out = pathlib.Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        f.write(b"II" + struct.pack("<HI", 42, ifd_at))  # where the directory begins
        for blob in strips:
            f.write(blob)
        f.write(bytes(body))
        f.write(bytes(tail))
