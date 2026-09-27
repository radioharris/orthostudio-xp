# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The squares X-Plane takes from another pack than Live's: the user's own ortho tiles.

For each 1-degree square, X-Plane draws the ground of the first pack of its list
(``scenery_packs.ini``, top first) holding a DSF of that square that is not an overlay (a DSF with
the property ``sim/overlay 1`` only adds roads, forests, buildings or an airport over the ground).
OrthoStudio XP lists the tiles it built above Live's pack, as it lists them above AutoOrtho
(``orthostudio.install.scenery_packs``, which takes ``z_osxp_live`` for a base layer), and the
tiles it imported from Ortho4XP too. Where such a tile is, X-Plane never reads Live's textures: Live
downloads nothing there, and a start there waits for none of them.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from orthostudio.install.scenery_packs import SceneryPacks

from .geo import dsf_tile_of
from .scenery import _ini

DSF_MAGIC = b"XPLNEDSF"
SEVEN_ZIP = b"7z\xbc\xaf\x27\x1c"
ATOM = 8  # an atom's id and length


def _atoms(data: bytes, start: int, end: int):
    """(id, payload start, payload end) of the atoms in ``data[start:end]``; ids read as written
    in the specification (``HEAD``), though a DSF stores them reversed."""
    pos = start
    while pos + ATOM <= end:
        length = int.from_bytes(data[pos + 4 : pos + 8], "little")
        if length < ATOM:
            return
        yield data[pos : pos + 4][::-1], pos + ATOM, min(pos + length, end)
        pos += length


def _head(path: Path) -> bytes:
    """The start of a DSF, as far as its HEAD atom goes: unpacked first from its 7z archive, the
    form X-Plane's own scenery takes."""
    with open(path, "rb") as f:
        start = f.read(12 + ATOM)
        if start.startswith(SEVEN_ZIP):
            return _unpacked(path)
        if not start.startswith(DSF_MAGIC) or len(start) < 12 + ATOM:
            return b""
        length = int.from_bytes(start[16:20], "little")  # the first atom: HEAD
        return start + f.read(max(0, length - ATOM))


def _unpacked(path: Path) -> bytes:
    import py7zr

    try:
        with tempfile.TemporaryDirectory() as tmp, py7zr.SevenZipFile(path, "r") as archive:
            archive.extractall(path=tmp)
            files = [p for p in Path(tmp).rglob("*") if p.is_file()]
            return files[0].read_bytes() if len(files) == 1 else b""
    except Exception:  # py7zr raises its own errors as well as OSError: not a DSF we can read
        return b""


def dsf_properties(path: Path) -> dict[str, str]:
    """The properties of a DSF (the PROP atom of its HEAD), empty when it cannot be read."""
    data = _head(path)
    if not data.startswith(DSF_MAGIC):
        return {}
    for atom, start, end in _atoms(data, 12, len(data)):
        if atom != b"HEAD":
            continue
        for inner, istart, iend in _atoms(data, start, end):
            if inner == b"PROP":
                words = data[istart:iend].split(b"\0")
                return {
                    words[i].decode("utf-8", "replace"): words[i + 1].decode("utf-8", "replace")
                    for i in range(0, len(words) - 1, 2)
                }
    return {}


def is_overlay(path: Path) -> bool:
    return dsf_properties(path).get("sim/overlay") == "1"


def _folder(xplane: Path, written: str) -> Path:
    path = Path(written.replace("\\", "/"))
    return path if path.is_absolute() else xplane / path


def covered_squares(xplane: Path, below: str) -> dict[tuple[int, int], str]:
    """``{(lat, lon): pack}``: the squares whose ground X-Plane takes from an enabled pack listed
    above ``below`` (Live's link), for a DSF there that is not an overlay. OrthoStudio XP's tiles
    and those imported from Ortho4XP are taken for ground without reading their DSF."""
    out: dict[tuple[int, int], str] = {}
    for entry in SceneryPacks.load(_ini(xplane)).entries:  # the top first
        if entry.name == below:
            break
        if not entry.enabled or entry.is_global_airports:
            continue
        nav = _folder(xplane, entry.path) / "Earth nav data"
        if not nav.is_dir():
            continue
        for dsf in sorted(nav.glob("*/*.dsf")):
            square = dsf_tile_of(str(dsf))
            if square is None or square in out:
                continue
            if entry.is_ortho or not is_overlay(dsf):
                out[square] = entry.name
    return out
