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

from collections.abc import Collection
from pathlib import Path

from orthostudio.install.scenery_packs import SceneryPacks

from .geo import dsf_tile_of
from .scenery import _ini, place_line

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


HEAD_BYTES = 1 << 16  # a DSF's HEAD, its properties, is within this


class _EnoughError(Exception):
    """The start of a zipped DSF is in: nothing more to unpack."""


def _head(path: Path) -> bytes:
    """The start of a DSF, as far as its HEAD atom goes: unpacked first from its 7z archive, the
    form X-Plane's own scenery takes, and only its start (Global Forests: 5 MB a DSF, thousands)."""
    with open(path, "rb") as f:
        start = f.read(12 + ATOM)
        if start.startswith(SEVEN_ZIP):
            return _unpacked(path)
        if not start.startswith(DSF_MAGIC) or len(start) < 12 + ATOM:
            return b""
        length = int.from_bytes(start[16:20], "little")  # the first atom: HEAD
        return start + f.read(max(0, min(length, HEAD_BYTES) - ATOM))


def _unpacked(path: Path) -> bytes:
    """The start of the DSF a 7z archive holds: unpacking stops once HEAD_BYTES are in."""
    import py7zr
    from py7zr.io import Py7zIO, WriterFactory

    class Start(Py7zIO):
        def __init__(self) -> None:
            self.data = bytearray()

        def write(self, s: bytes | bytearray) -> int:
            self.data += s
            if len(self.data) >= HEAD_BYTES:
                raise _EnoughError
            return len(s)

        def read(self, size: int | None = None) -> bytes:
            return bytes(self.data)

        def seek(self, offset: int, whence: int = 0) -> int:
            return 0

        def flush(self) -> None:
            pass

        def size(self) -> int:
            return len(self.data)

    class Factory(WriterFactory):
        def __init__(self) -> None:
            self.product = Start()

        def create(self, filename: str) -> Py7zIO:
            return self.product

    factory = Factory()
    try:
        with py7zr.SevenZipFile(path, "r") as archive:
            archive.extract(factory=factory)
    except _EnoughError:
        pass
    except Exception:  # py7zr raises its own errors as well as OSError: not a DSF we can read
        return b""
    return bytes(factory.product.data)


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


def is_ground(path: Path) -> bool:
    """A DSF read, and not an overlay: the ground of its square. One that cannot be read is not
    taken for ground: Live then builds there, where the other way X-Plane could show green."""
    properties = dsf_properties(path)
    return bool(properties) and properties.get("sim/overlay") != "1"


def _folder(xplane: Path, written: str) -> Path:
    path = Path(written.replace("\\", "/"))
    return path if path.is_absolute() else xplane / path


def covered_squares(
    xplane: Path,
    below: str,
    region: Collection[tuple[int, int]] | None = None,
    packs: SceneryPacks | None = None,
) -> dict[tuple[int, int], str]:
    """``{(lat, lon): pack}``: the squares of ``region`` (Live's pack's; all when None) whose
    ground X-Plane takes from an enabled pack listed above ``below`` (Live's link), in ``packs``
    (X-Plane's list as it is on the disk when None).

    A pack is ground or overlays as one of its DSFs there says, read up to its properties: packs
    are one or the other, and simHeaven X-World or Global Forests hold thousands of zipped DSFs,
    which a first version read every one of, for minutes. OrthoStudio XP's tiles and those
    imported from Ortho4XP are ground without reading."""
    wanted = None if region is None else set(region)
    out: dict[tuple[int, int], str] = {}
    listed = packs if packs is not None else SceneryPacks.load(_ini(xplane))
    for entry in listed.entries:  # the top first
        if entry.name == below:
            break
        if not entry.enabled or entry.is_global_airports:
            continue
        nav = _folder(xplane, entry.path) / "Earth nav data"
        if not nav.is_dir():
            continue
        squares: dict[tuple[int, int], Path] = {}
        for dsf in nav.glob("*/*.dsf"):
            square = dsf_tile_of(str(dsf))
            if square is not None and (wanted is None or square in wanted):
                squares.setdefault(square, dsf)
        if not squares:
            continue
        if not entry.is_ortho and not is_ground(squares[min(squares)]):
            continue  # roads, forests, buildings, an airport: over the ground only
        for square in squares:
            out.setdefault(square, entry.name)
    return out


def own_squares(xplane: Path, below: str, own_tiles: str = "first") -> dict[tuple[int, int], str]:
    """The squares X-Plane takes from the user's own scenery, as :func:`covered_squares` finds
    them, with Live's line where it is listed, else where Live's next start lists it
    (:func:`orthostudio.live.scenery.place_line`, in memory only: the list on the disk stays as it
    is). The Live window shows them while Live is stopped too: what its next flight shows."""
    packs = SceneryPacks.load(_ini(xplane))
    place_line(xplane, packs, own_tiles)
    return covered_squares(xplane, below, packs=packs)
