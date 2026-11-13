# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A DSF's properties, read from its head only: whether a pack's DSF is ground or overlays
(``sim/overlay 1``), for X-Plane's list of sceneries (:meth:`SceneryPacks.is_mesh
<orthostudio.install.scenery_packs.SceneryPacks.is_mesh>`) and Live's squares
(:mod:`orthostudio.live.covered`)."""

from __future__ import annotations

from pathlib import Path

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
    """The properties of a DSF (the PROP atom of its HEAD), empty when it cannot be read (a
    broken link, no right to read it: a start stopped there once, the review of 2026-11-13)."""
    try:
        data = _head(path)
    except OSError:
        return {}
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
