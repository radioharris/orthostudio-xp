# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Elevation and bathymetry rasters copied from an X-Plane 12 Global Scenery DSF.

Port of ``extract_elevation_and_bathymetry_data`` (``O4_DSF_Utils.py:360-453``) with ``py7zr``
in memory instead of the ``7z`` binary. Spec: ``docs/specs/dsf-xp12-rasters.md``.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import py7zr
from py7zr.io import Py7zIO, WriterFactory

from orthostudio.dsf.container import DsfFormatError, parse_dsf
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef

__all__ = [
    "DEMO_AREAS",
    "SEVENZIP_MAGIC",
    "Xp12Rasters",
    "clamp_bathymetry",
    "extract_xp12_rasters",
    "global_scenery_dsf",
    "rasters_from_dsf",
    "read_global_scenery_dsf",
]

SEVENZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"
LARGE_SUBATOM = 100
"""A DEMS sub-atom longer than this is raster data, not a ``DEMI`` info (``:428``)."""
EXPECTED_NAMES = ("elevation", "sea_level")
DEMO_AREAS = "X-Plane 12 Demo Areas"
"""The folder X-Plane 12's installer puts beside ``X-Plane 12 Global Scenery`` for the areas of its
demo, and the only one with some of their tiles even when every part of the world is installed:
Maui to Kauai, the coast of Oregon and Washington, south-east Alaska (``+20-160`` of the Global
Scenery is an empty folder). X-Plane draws both as its own scenery; a user who had installed them
all read that Hawaii's was not (2026-09-22)."""


@dataclass(frozen=True)
class Xp12Rasters:
    """Payloads of the ``DEFN/DEMN`` and ``DEMS`` atoms to copy into the ortho DSF."""

    demn: bytes
    dems: bytes


class _MemoryFile(Py7zIO):
    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, s: bytes | bytearray) -> int:
        return self.buffer.write(s)

    def read(self, size: int | None = None) -> bytes:
        return self.buffer.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.buffer.seek(offset, whence)

    def flush(self) -> None:
        self.buffer.flush()

    def size(self) -> int:
        return len(self.buffer.getbuffer())


class _MemoryFactory(WriterFactory):
    def __init__(self) -> None:
        self.files: dict[str, _MemoryFile] = {}

    def create(self, filename: str) -> Py7zIO:
        f = _MemoryFile()
        self.files[filename] = f
        return f


def global_scenery_dsf(global_scenery_dir: Path, tile: TileRef) -> Path:
    """``<dir>/Earth nav data/<10x10>/<tile>.dsf`` (``:363-367``), else the same file in the
    ``DEMO_AREAS`` folder beside ``<dir>`` when only that one has it; the first when neither
    does, the file the messages name."""
    path = Path(global_scenery_dir) / tile.dsf_relpath
    if not path.is_file():
        demo = Path(global_scenery_dir).parent / DEMO_AREAS / tile.dsf_relpath
        if demo.is_file():
            return demo
    return path


def read_global_scenery_dsf(path: Path, tile: TileRef) -> bytes:
    """The bytes of a Global Scenery DSF file, decompressed in memory when it is a 7z archive.

    Raises ``DSF_GLOBAL_SCENERY_MISSING``, ``DSF_GLOBAL_SCENERY_COPY_FAILED`` or
    ``DSF_SOURCE_DECOMPRESS_FAILED``; ``tile`` only names the error context.
    """
    path = Path(path)
    if not path.is_file():
        raise OsxpError(
            "DSF_GLOBAL_SCENERY_MISSING", context={"tile": tile.name, "path": str(path)}
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise OsxpError(
            "DSF_GLOBAL_SCENERY_COPY_FAILED",
            context={"tile": tile.name, "path": str(path), "reason": str(exc)},
        ) from exc
    if not raw.startswith(SEVENZIP_MAGIC):
        return raw
    factory = _MemoryFactory()
    try:
        with py7zr.SevenZipFile(io.BytesIO(raw), "r") as archive:
            archive.extractall(factory=factory)
    except Exception as exc:  # py7zr raises many types (Bad7zFile, lzma errors, ...)
        raise OsxpError(
            "DSF_SOURCE_DECOMPRESS_FAILED",
            context={"tile": tile.name, "path": str(path), "reason": str(exc)},
        ) from exc
    members = [f for f in factory.files.values() if f.size() > 0]
    if not members:
        raise OsxpError(
            "DSF_SOURCE_DECOMPRESS_FAILED",
            context={"tile": tile.name, "path": str(path), "reason": "empty archive"},
        )
    return members[0].buffer.getvalue()


_DEM_INFO = struct.Struct("<BBHIIff")
"""A ``DEMI`` atom: version, bytes per post, flags, width, height, scale, offset."""


@dataclass(frozen=True)
class _DemInfo:
    """What a ``DEMI`` says of the raster after it: its size, and whether its values lie on the
    tile's edges (post-centric, flag 4) or at the centres of cells (area-centric)."""

    bpp: int
    flags: int
    width: int
    height: int

    @property
    def post(self) -> bool:
        return bool(self.flags & 4)

    @property
    def int16(self) -> bool:
        return self.bpp == 2 and self.flags & 3 == 1


def clamp_bathymetry(dems: bytes) -> bytes:
    """Copy of a ``DEMS`` payload with the second large raster clamped to ``elevation - 2``.

    ``O4_DSF_Utils.py:418-441``: sub-atoms longer than 100 bytes are counted; the first is
    the elevation, the second the bathymetry, ``int16`` little-endian. Ortho4XP takes them post by
    post, which holds when both have the same size, as every Global Scenery DSF of X-Plane 12.4.4
    has (1201 x 1201, the 18,197 of the owner's). A user's +32+012 had its bathymetry at 256 x 256
    by cell, without X-Plane 12's season and sound rasters: every square of his stopped there,
    and Ortho4XP would have too (issue 17, 2026-10-23). Each bathymetry value is then clamped by
    the elevation at its own place (:func:`_clamped`), and the raster keeps its size.
    """
    out = bytearray()
    pos, large, elev = 0, 0, b""
    info: _DemInfo | None = None
    elev_info: _DemInfo | None = None
    while pos < len(dems):
        if pos + 8 > len(dems):
            raise DsfFormatError(f"dangling {len(dems) - pos} bytes in DEMS")
        header = dems[pos : pos + 8]
        (size,) = struct.unpack_from("<I", header, 4)
        if size < 8 or pos + size > len(dems):
            raise DsfFormatError(f"DEMS sub-atom at {pos} has size {size}")
        body = dems[pos + 8 : pos + size]
        if header[:4][::-1] == b"DEMI" and len(body) >= _DEM_INFO.size:
            _version, bpp, flags, width, height, _scale, _offset = _DEM_INFO.unpack_from(body)
            info = _DemInfo(bpp, flags, width, height)
        if size > LARGE_SUBATOM:
            large += 1
            if large == 1:
                elev, elev_info = body, info
            elif large == 2:
                body = _clamped(np.frombuffer(body, dtype="<i2"), elev, elev_info, info)
        out += header + body
        pos += size
    return bytes(out)


def _clamped(bathy: np.ndarray, elev: bytes, e: _DemInfo | None, b: _DemInfo | None) -> bytes:
    """``bathy`` clamped to the elevation minus 2 m at the place of each of its values.

    Rasters of one size are taken post by post, as Ortho4XP takes them: the same bytes as it
    gives. Of two sizes, the elevation is read at each bathymetry value's place, between its four
    nearest posts, each raster placed by its own centring. A raster that cannot be placed (no
    ``DEMI``, not 16-bit integers, a size its data does not have) is left as X-Plane wrote it,
    which X-Plane draws on its own scenery."""
    heights = np.frombuffer(elev, dtype="<i2")
    if len(heights) == len(bathy):
        return np.minimum(bathy, heights - np.int16(2)).astype("<i2").tobytes()
    if (
        e is None
        or b is None
        or not (e.int16 and b.int16)
        or e.width * e.height != len(heights)
        or b.width * b.height != len(bathy)
        or min(e.width, e.height) < 2
    ):
        return bathy.astype("<i2").tobytes()
    grid = heights.reshape(e.height, e.width).astype(np.float64)
    cols = _place(b.width, b.post, e.width, e.post)
    rows = _place(b.height, b.post, e.height, e.post)
    c0 = np.minimum(np.floor(cols).astype(np.int64), e.width - 2)
    r0 = np.minimum(np.floor(rows).astype(np.int64), e.height - 2)
    fc = (cols - c0)[None, :]
    fr = (rows - r0)[:, None]
    g00 = grid[np.ix_(r0, c0)]
    g01 = grid[np.ix_(r0, c0 + 1)]
    g10 = grid[np.ix_(r0 + 1, c0)]
    g11 = grid[np.ix_(r0 + 1, c0 + 1)]
    ground = (g00 * (1 - fc) + g01 * fc) * (1 - fr) + (g10 * (1 - fc) + g11 * fc) * fr
    safe = np.floor(ground) - 2
    clamped = np.minimum(bathy.reshape(b.height, b.width).astype(np.float64), safe)
    return clamped.astype("<i2").tobytes()


def _place(n: int, post: bool, m: int, m_post: bool) -> np.ndarray:
    """Where each of ``n`` values along one side of the tile falls among ``m`` others, as a
    fractional index, clipped to them: a post-centric raster lies on the tile's edges, an
    area-centric one at the centres of its cells."""
    k = np.arange(n, dtype=np.float64)
    where = k / (n - 1) if post and n > 1 else (k + 0.5) / n
    index = where * (m - 1) if m_post else where * m - 0.5
    return np.clip(index, 0.0, m - 1.0)


def rasters_from_dsf(data: bytes, *, tile: TileRef | None = None) -> Xp12Rasters:
    """The ``DEMN`` and clamped ``DEMS`` payloads of an in-memory Global Scenery DSF."""
    context = {"tile": tile.name if tile else "?"}
    try:
        dsf = parse_dsf(data)
    except DsfFormatError as exc:
        raise OsxpError("DSF_SOURCE_CORRUPTED", context={**context, "reason": str(exc)}) from exc
    demn = dsf.find("DEFN/DEMN")
    dems = dsf.find("DEMS")
    if demn is None or dems is None:
        raise OsxpError(
            "DSF_SOURCE_CORRUPTED", context={**context, "reason": "no DEMN or DEMS atom"}
        )
    names = demn.payload(data).split(b"\0")
    if tuple(n.decode("ascii", "replace") for n in names[:2]) != EXPECTED_NAMES:
        raise OsxpError(
            "DSF_SOURCE_CORRUPTED",
            context={**context, "reason": f"rasters are {names[:2]!r}, not elevation/sea_level"},
        )
    try:
        clamped = clamp_bathymetry(dems.payload(data))
    except DsfFormatError as exc:
        raise OsxpError("DSF_SOURCE_CORRUPTED", context={**context, "reason": str(exc)}) from exc
    return Xp12Rasters(demn=demn.payload(data), dems=clamped)


def extract_xp12_rasters(global_scenery_dir: Path, tile: TileRef) -> Xp12Rasters:
    """``extract_elevation_and_bathymetry_data`` (``:360-453``) for ``tile``.

    Raises ``DSF_GLOBAL_SCENERY_MISSING``, ``DSF_GLOBAL_SCENERY_COPY_FAILED``,
    ``DSF_SOURCE_DECOMPRESS_FAILED`` or ``DSF_SOURCE_CORRUPTED``.
    """
    path = global_scenery_dsf(global_scenery_dir, tile)
    return rasters_from_dsf(read_global_scenery_dsf(path, tile), tile=tile)
