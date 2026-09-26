# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Locate and materialise the Global Scenery DSF of a tile (spec section 2, lines 40-79).

The source folder is the one just above ``Earth nav data`` (Ortho4XP's ``custom_overlay_src``,
typically ``<X-Plane>/Global Scenery/X-Plane 12 Global Scenery``, or ``X-Plane 12 Demo Areas``
beside it for a tile only they have). X-Plane 12 ships its DSFs
as 7z archives (``7z\\xbc\\xaf\\x27\\x1c`` magic, one member); py7zr extracts them in-process
into the work directory. A plain DSF is used where it is: DSFTool only reads it.
"""

from __future__ import annotations

from pathlib import Path

import py7zr

from orthostudio.dsf.xp12 import global_scenery_dsf
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.overlays.dsftool import DSF_MAGIC

__all__ = [
    "SEVENZIP_MAGIC",
    "XP12_GLOBAL_SCENERY",
    "SourceInfo",
    "materialize_source",
    "overlay_source_path",
    "resolve_global_scenery_dir",
]

SEVENZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"
XP12_GLOBAL_SCENERY = Path("Global Scenery") / "X-Plane 12 Global Scenery"
EARTH_NAV_DATA = "Earth nav data"


class SourceInfo:
    """Where the usable DSF is and how it was obtained."""

    __slots__ = ("compressed", "extracted", "path", "size", "source")

    def __init__(
        self, source: Path, path: Path, *, compressed: bool, size: int, extracted: int | None
    ) -> None:
        self.source = source
        self.path = path
        self.compressed = compressed
        self.size = size
        self.extracted = extracted


def resolve_global_scenery_dir(global_scenery_dir: Path) -> Path:
    """The folder holding ``Earth nav data``.

    ``global_scenery_dir`` may be that folder itself, or an X-Plane root (then
    ``Global Scenery/X-Plane 12 Global Scenery`` under it). Returned unchanged when neither
    matches: the caller reports the missing file with its full path.
    """
    d = Path(global_scenery_dir)
    if (d / EARTH_NAV_DATA).is_dir():
        return d
    xp12 = d / XP12_GLOBAL_SCENERY
    if (xp12 / EARTH_NAV_DATA).is_dir():
        return xp12
    return d


def overlay_source_path(global_scenery_dir: Path, lat: int, lon: int) -> Path:
    """``<dir>/Earth nav data/+40+000/+43+005.dsf`` (``O4_Overlay_Utils.py:40-44``), or the one
    of X-Plane 12's Demo Areas (:func:`orthostudio.dsf.xp12.global_scenery_dsf`)."""
    root = resolve_global_scenery_dir(global_scenery_dir)
    return global_scenery_dsf(root, TileRef(lat, lon))


def _read_head(path: Path, n: int) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def materialize_source(src: Path, workdir: Path, *, tile: str) -> SourceInfo:
    """Make ``src`` usable by DSFTool: extract it into ``workdir`` when it is a 7z archive.

    Raises ``DSF_OVERLAY_SOURCE_MISSING``, ``DSF_SOURCE_DECOMPRESS_FAILED`` (not exactly one
    member, py7zr error, empty member) or ``DSF_SOURCE_CORRUPTED`` (no ``XPLNEDSF`` magic).
    """
    src, workdir = Path(src), Path(workdir)
    if not src.is_file():
        raise OsxpError("DSF_OVERLAY_SOURCE_MISSING", context={"tile": tile, "path": str(src)})
    size = src.stat().st_size
    head = _read_head(src, len(DSF_MAGIC))
    if head.startswith(SEVENZIP_MAGIC):
        path = _extract_7z(src, workdir, tile=tile)
        extracted = path.stat().st_size
        head = _read_head(path, len(DSF_MAGIC))
        if head != DSF_MAGIC:
            raise OsxpError("DSF_SOURCE_CORRUPTED", context={"tile": tile, "path": str(src)})
        return SourceInfo(src, path, compressed=True, size=size, extracted=extracted)
    if head != DSF_MAGIC:
        raise OsxpError("DSF_SOURCE_CORRUPTED", context={"tile": tile, "path": str(src)})
    return SourceInfo(src, src, compressed=False, size=size, extracted=None)


def _extract_7z(src: Path, workdir: Path, *, tile: str) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        with py7zr.SevenZipFile(src, "r") as archive:
            members = [
                info.filename for info in archive.list() if not getattr(info, "is_directory", False)
            ]
            if len(members) != 1:
                raise OsxpError(
                    "DSF_SOURCE_DECOMPRESS_FAILED",
                    context={
                        "tile": tile,
                        "path": str(src),
                        "reason": f"{len(members)} members in the archive, expected 1",
                    },
                )
            archive.extract(path=workdir, targets=members)
    except OsxpError:
        raise
    except Exception as exc:  # py7zr raises its own hierarchy plus OSError/EOFError
        raise OsxpError(
            "DSF_SOURCE_DECOMPRESS_FAILED",
            context={"tile": tile, "path": str(src), "reason": f"{type(exc).__name__}: {exc}"},
        ) from exc
    out = workdir / members[0]
    if not out.is_file() or out.stat().st_size == 0:
        raise OsxpError(
            "DSF_SOURCE_DECOMPRESS_FAILED",
            context={"tile": tile, "path": str(src), "reason": f"member {members[0]} not written"},
        )
    return out
