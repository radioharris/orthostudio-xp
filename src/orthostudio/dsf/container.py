"""Structural reader for X-Plane DSF files (atom tree, properties, MD5 footer).

Only what a byte-level comparison needs: the atom tree is walked so that a difference can be
named ("GEOD/POOL[12] at offset ..."), the HEAD/PROP key/value pairs are decoded so that
``sim/creation_agent`` can be ignored, and the trailing MD5 is verified. Nothing is decoded
below the atom level. Layout as written by Ortho4XP (``O4_DSF_Utils.py``): magic
``XPLNEDSF``, version 1, atoms ``HEAD DEFN GEOD CMDS [DEMS]``, 16-byte MD5 footer.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path

DSF_MAGIC = b"XPLNEDSF"
DSF_VERSION = 1
MD5_SIZE = 16
ATOM_HEADER = 8

# Container atoms and the sub-atoms they may hold. CMDS is a flat command stream.
CONTAINERS: dict[str, frozenset[str]] = {
    "HEAD": frozenset({"PROP"}),
    "DEFN": frozenset({"TERT", "OBJT", "POLY", "NETW", "DEMN"}),
    "GEOD": frozenset({"POOL", "SCAL", "PO32", "SC32"}),
    "DEMS": frozenset({"DEMI", "DEMD"}),
}
CREATION_AGENT = "sim/creation_agent"


class DsfFormatError(ValueError):
    """The file is not a DSF file this module understands."""


@dataclass
class Atom:
    """One atom: ``name`` in human order (``HEAD``, not ``DAEH``), offsets from file start."""

    name: str
    offset: int
    size: int
    children: list[Atom] | None = None

    @property
    def payload_offset(self) -> int:
        """Offset of the first payload byte."""
        return self.offset + ATOM_HEADER

    @property
    def payload_size(self) -> int:
        """Number of payload bytes."""
        return self.size - ATOM_HEADER

    def payload(self, data: bytes) -> bytes:
        """The payload bytes of this atom in ``data``."""
        return data[self.payload_offset : self.offset + self.size]


@dataclass
class DsfFile:
    """Parsed structure of a DSF file (no geometry decoding)."""

    atoms: list[Atom] = field(default_factory=list)
    md5_ok: bool = False
    size: int = 0

    def find(self, path: str) -> Atom | None:
        """Find an atom by path such as ``HEAD/PROP`` or ``GEOD/POOL[3]``."""
        level = self.atoms
        found: Atom | None = None
        for part in path.split("/"):
            name, _, idx = part.partition("[")
            wanted = int(idx.rstrip("]")) if idx else 0
            same = [a for a in level if a.name == name]
            if wanted >= len(same):
                return None
            found = same[wanted]
            level = found.children or []
        return found


def _atom_name(raw: bytes) -> str:
    return raw[::-1].decode("ascii", errors="replace")


def parse_atoms(data: bytes, start: int, end: int, depth: int = 0) -> list[Atom]:
    """Parse consecutive atoms in ``data[start:end]``, descending into known containers."""
    atoms: list[Atom] = []
    pos = start
    while pos < end:
        if pos + ATOM_HEADER > end:
            raise DsfFormatError(f"dangling {end - pos} bytes at offset {pos}")
        name = _atom_name(data[pos : pos + 4])
        (size,) = struct.unpack_from("<I", data, pos + 4)
        if size < ATOM_HEADER or pos + size > end:
            raise DsfFormatError(f"atom {name} at offset {pos} has size {size}, exceeds {end}")
        atom = Atom(name=name, offset=pos, size=size)
        if depth == 0 and name in CONTAINERS:
            atom.children = parse_atoms(data, pos + ATOM_HEADER, pos + size, depth + 1)
        atoms.append(atom)
        pos += size
    return atoms


def parse_dsf(data: bytes) -> DsfFile:
    """Parse the atom tree of an in-memory DSF file and verify its MD5 footer."""
    if len(data) < len(DSF_MAGIC) + 4 + MD5_SIZE:
        raise DsfFormatError(f"file too short ({len(data)} bytes)")
    if data[:8] != DSF_MAGIC:
        raise DsfFormatError(f"bad magic {data[:8]!r}")
    (version,) = struct.unpack_from("<I", data, 8)
    if version != DSF_VERSION:
        raise DsfFormatError(f"unsupported DSF version {version}")
    body_end = len(data) - MD5_SIZE
    atoms = parse_atoms(data, 12, body_end)
    md5_ok = hashlib.md5(data[:body_end]).digest() == data[body_end:]
    return DsfFile(atoms=atoms, md5_ok=md5_ok, size=len(data))


def read_dsf(path: str | Path) -> tuple[bytes, DsfFile]:
    """Read a DSF file and parse its structure."""
    data = Path(path).read_bytes()
    return data, parse_dsf(data)


def parse_properties(payload: bytes) -> list[tuple[str, str]]:
    """Decode a PROP payload: NUL-terminated key/value string pairs, in file order."""
    parts = payload.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) % 2:
        raise DsfFormatError("PROP atom holds an odd number of strings")
    return [
        (parts[i].decode("utf-8", "replace"), parts[i + 1].decode("utf-8", "replace"))
        for i in range(0, len(parts), 2)
    ]


def properties(data: bytes, dsf: DsfFile) -> list[tuple[str, str]]:
    """The HEAD/PROP pairs of a parsed DSF, or an empty list if absent."""
    prop = dsf.find("HEAD/PROP")
    return parse_properties(prop.payload(data)) if prop else []


def atom_paths(dsf: DsfFile) -> list[str]:
    """Flat list of atom paths, e.g. ``['HEAD', 'HEAD/PROP', 'DEFN', 'DEFN/TERT', ...]``."""
    out: list[str] = []

    def walk(atoms: list[Atom], prefix: str) -> None:
        seen: dict[str, int] = {}
        for a in atoms:
            k = seen.get(a.name, 0)
            seen[a.name] = k + 1
            label = f"{a.name}[{k}]" if k or sum(x.name == a.name for x in atoms) > 1 else a.name
            path = f"{prefix}{label}"
            out.append(path)
            if a.children is not None:
                walk(a.children, path + "/")

    walk(dsf.atoms, "")
    return out
