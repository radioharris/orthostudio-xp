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


def clamp_bathymetry(dems: bytes) -> bytes:
    """Copy of a ``DEMS`` payload with the second large raster clamped to ``elevation - 2``.

    ``O4_DSF_Utils.py:418-441``: sub-atoms longer than 100 bytes are counted; the first is
    the elevation, the second the bathymetry, ``int16`` little-endian.
    """
    out = bytearray()
    pos, large, elev = 0, 0, b""
    while pos < len(dems):
        if pos + 8 > len(dems):
            raise DsfFormatError(f"dangling {len(dems) - pos} bytes in DEMS")
        header = dems[pos : pos + 8]
        (size,) = struct.unpack_from("<I", header, 4)
        if size < 8 or pos + size > len(dems):
            raise DsfFormatError(f"DEMS sub-atom at {pos} has size {size}")
        body = dems[pos + 8 : pos + size]
        if size > LARGE_SUBATOM:
            large += 1
            if large == 1:
                elev = body
            elif large == 2:
                bathy = np.frombuffer(body, dtype="<i2")
                safe = np.frombuffer(elev, dtype="<i2") - np.int16(2)
                body = np.minimum(bathy, safe).astype("<i2").tobytes()
        out += header + body
        pos += size
    return bytes(out)


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
