"""X-Plane 12's own relief as an elevation source: ``custom_dem = "XP12"`` (OrthoStudio XP only).

Spec: ``docs/specs/dem.md`` section 12. Decision: ``docs/decisions/0007-relief-xplane.md``.

Every X-Plane 12 Global Scenery DSF carries an ``elevation`` raster in its ``DEMS`` atom:
1201 x 1201 signed 16-bit posts (``DEMI`` flags ``0x5``), 3 arc-seconds, metres, scale 1,
offset 0, rows south to north, the sea at 0 m. Every tile sampled on a full install had that
shape. It is the geometry of a 3" ``.hgt`` cell, so :mod:`orthostudio.dem.raster` sends a cell down
the path Ortho4XP uses for one (void fill, 3x bilinear refinement to 3601) and through the same
3x3 assembly as ``View``.

This module only *reads* the raster; it does not import :mod:`orthostudio.dem.raster` (which imports
it), so there is no cycle.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray

from orthostudio.dsf.container import DsfFormatError, parse_dsf
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef

__all__ = [
    "ELEVATION_RASTER",
    "XP12_INPUTS",
    "XP12_SOURCE",
    "RasterInfo",
    "dem_rasters",
    "elevation_posts",
    "read_elevation_posts",
]

F32 = NDArray[np.float32]

XP12_SOURCE = "XP12"
"""The ``custom_dem`` value that selects X-Plane 12's relief."""

ELEVATION_RASTER = "elevation"
"""Name of the relief raster in ``DEFN/DEMN``."""

XP12_INPUTS: dict[str, tuple[int, int]] = {
    "xp12": (0, 0),
    "xp12_s": (-1, 0),
    "xp12_n": (1, 0),
    "xp12_w": (0, -1),
    "xp12_e": (0, 1),
    "xp12_sw": (-1, -1),
    "xp12_se": (-1, 1),
    "xp12_nw": (1, -1),
    "xp12_ne": (1, 1),
}
"""Inputs of the rule ``orthostudio.dem``: the Global Scenery DSF of each cell of the 3x3 block, as
``(dlat, dlon)`` from the tile. Their digests enter the key, so an X-Plane update that changes
a DSF rebuilds the relief."""

NODATA_I16 = -32768
"""The void post of a signed 16-bit raster; OrthoStudio XP's ``NODATA`` has the same value."""

_DTYPES: dict[tuple[int, int], str] = {
    (0, 4): "<f4",
    (1, 1): "i1",
    (1, 2): "<i2",
    (1, 4): "<i4",
    (2, 1): "u1",
    (2, 2): "<u2",
    (2, 4): "<u4",
}
"""``(DEMI flags & 3, bytes per post)`` -> numpy dtype (0 float, 1 signed, 2 unsigned)."""


class RasterInfo(NamedTuple):
    """One ``DEMI`` atom: ``<BBHIIff``."""

    version: int
    bpp: int
    flags: int
    width: int
    height: int
    scale: float
    offset: float


def dem_rasters(demn: bytes, dems: bytes) -> dict[str, tuple[RasterInfo, bytes]]:
    """Name -> ``(info, data)`` of every raster of a DSF, from its DEMN and DEMS payloads.

    Raises :class:`DsfFormatError` when the atoms do not pair up with the names.
    """
    names = [n.decode("ascii", "replace") for n in demn.split(b"\0") if n]
    infos: list[RasterInfo] = []
    datas: list[bytes] = []
    pos = 0
    while pos < len(dems):
        if pos + 8 > len(dems):
            raise DsfFormatError(f"dangling {len(dems) - pos} bytes in DEMS")
        atom_id = dems[pos : pos + 4][::-1]
        (size,) = struct.unpack_from("<I", dems, pos + 4)
        if size < 8 or pos + size > len(dems):
            raise DsfFormatError(f"DEMS sub-atom at {pos} has size {size}")
        body = dems[pos + 8 : pos + size]
        if atom_id == b"DEMI":
            if len(body) < 20:
                raise DsfFormatError(f"DEMI at {pos} holds {len(body)} bytes, not 20")
            infos.append(RasterInfo(*struct.unpack_from("<BBHIIff", body)))
        elif atom_id == b"DEMD":
            datas.append(body)
        pos += size
    if not len(names) == len(infos) == len(datas):
        raise DsfFormatError(
            f"{len(names)} raster names, {len(infos)} DEMI and {len(datas)} DEMD atoms"
        )
    return {name: (info, data) for name, info, data in zip(names, infos, datas, strict=True)}


def elevation_posts(data: bytes, *, tile: TileRef) -> F32:
    """The elevation raster of an in-memory Global Scenery DSF: float32 metres, north to south.

    Voids of an integer raster (its most negative value) become ``-32768``. Raises
    ``DSF_SOURCE_CORRUPTED`` when the DSF has no usable elevation raster.
    """
    try:
        dsf = parse_dsf(data)
        demn = dsf.find("DEFN/DEMN")
        dems = dsf.find("DEMS")
        if demn is None or dems is None:
            raise DsfFormatError("no DEMN or DEMS atom")
        rasters = dem_rasters(demn.payload(data), dems.payload(data))
        if ELEVATION_RASTER not in rasters:
            raise DsfFormatError(f"no {ELEVATION_RASTER!r} raster among {sorted(rasters)}")
        info, body = rasters[ELEVATION_RASTER]
        dtype = _DTYPES.get((info.flags & 3, info.bpp))
        if dtype is None:
            raise DsfFormatError(f"elevation raster has flags {info.flags:#x}, {info.bpp} bpp")
        if not info.flags & 4:
            raise DsfFormatError("elevation raster is pixel-centred, not post-centred")
        if info.width < 2 or info.height < 2:
            raise DsfFormatError(f"elevation raster is {info.width}x{info.height}")
        if len(body) != info.width * info.height * info.bpp:
            raise DsfFormatError(
                f"elevation raster holds {len(body)} bytes for {info.width}x{info.height}"
            )
    except DsfFormatError as exc:
        raise OsxpError(
            "DSF_SOURCE_CORRUPTED", context={"tile": tile.name, "reason": str(exc)}
        ) from exc
    raw = np.frombuffer(body, dtype=dtype).reshape(info.height, info.width)[::-1]
    alt = raw.astype(np.float64) * float(info.scale) + float(info.offset)
    if raw.dtype.kind == "i":
        alt[raw == np.iinfo(raw.dtype).min] = NODATA_I16
    return alt.astype(np.float32)


def read_elevation_posts(path: Path, lat: int, lon: int) -> F32:
    """:func:`elevation_posts` of the DSF file at ``path`` (plain or 7z), for cell ``(lat, lon)``.

    Raises ``DSF_GLOBAL_SCENERY_MISSING``, ``DSF_GLOBAL_SCENERY_COPY_FAILED``,
    ``DSF_SOURCE_DECOMPRESS_FAILED`` or ``DSF_SOURCE_CORRUPTED``.
    """
    from orthostudio.dsf.xp12 import read_global_scenery_dsf  # py7zr is only needed here

    tile = TileRef(lat, lon)
    return elevation_posts(read_global_scenery_dsf(path, tile), tile=tile)
