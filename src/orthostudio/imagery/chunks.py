"""Per-texture container of raw tiles (``.chunks`` files) and its store.

Specification: ``docs/specs/imagery-chunks.md``. Invented here: Ortho4XP keeps no raw
tiles, only the assembled 4096² JPEG (``O4_Imagery_Utils.py:1329-1368``).
"""

from __future__ import annotations

import os
import struct
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

import blake3

from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_bytes
from orthostudio.imagery.grid import TEXTURE_TILES, TextureId

__all__ = [
    "CHUNK_COUNT",
    "CONTENT_TYPES",
    "HEADER_SIZE",
    "MAGIC",
    "REASON_CODES",
    "ChunkContainer",
    "ChunkEntry",
    "ChunkStatus",
    "ChunkStore",
]

MAGIC = b"OSXPCHK1"
CHUNK_COUNT = TEXTURE_TILES * TEXTURE_TILES  # 256
_PREAMBLE = struct.Struct("<8sII")  # magic, count, reserved
_INDEX_ENTRY = struct.Struct("<QIBBI")  # offset, length, status, content type, fetched_at
HEADER_SIZE = _PREAMBLE.size + CHUNK_COUNT * _INDEX_ENTRY.size  # 4624
_DIGEST_PREFIX = b"osxp-chunks-digest-1"
_CORRUPT_CODE = "IMG_CACHE_INCOMPLETE"  # until IMG_CHUNKS_CORRUPTED is registered (spec s. 6)
READ_ATTEMPTS = 10
"""How many times :meth:`ChunkStore.read` tries on Windows while a writer renames the file."""

CONTENT_TYPES: dict[int, str] = {
    0: "",
    1: "image/jpeg",
    2: "image/png",
    3: "image/webp",
    4: "image/gif",
    5: "image/bmp",
    6: "image/tiff",
    7: "text/html",
    8: "text/plain",
    9: "application/json",
    10: "application/xml",
    11: "text/xml",
    # 100-109: reason codes of ERROR entries (spec section 2), kept so that a container read
    # back still says why a tile failed
    100: "NET_TIMEOUT",
    101: "NET_CONNECTION_FAILED",
    102: "NET_RATE_LIMITED",
    103: "NET_SERVER_ERROR",
    104: "NET_UNEXPECTED_STATUS",
    105: "SYS_CANCELLED",
    106: "IMG_BAD_CONTENT_TYPE",
    107: "IMG_TILE_CORRUPTED",
    255: "application/octet-stream",
}
"""Content type codes of the index (an unknown type is stored as 255)."""
REASON_CODES = frozenset(name for code, name in CONTENT_TYPES.items() if 100 <= code < 110)
"""Reason codes an ``ERROR`` entry may carry in ``content_type``."""
_CONTENT_CODES = {name: code for code, name in CONTENT_TYPES.items()}
_OTHER_CODE = 255


class ChunkStatus(IntEnum):
    """State of one tile of a texture (``docs/specs/imagery-chunks.md`` section 2)."""

    OK = 0
    MISSING = 1
    PLACEHOLDER = 2
    ERROR = 3
    NOT_FETCHED = 4
    """Never asked for: the tile is unknown, not a hole (dette D3).

    A container written before this status exists cannot hold it, so reading old files is
    unaffected; a *new* container only carries it where a partial container is wanted (the
    parent cache of ``orthostudio.pipeline.parents``), never in a texture container, where a tile
    still to be downloaded stays ``MISSING``.
    """


def normalize_content_type(value: str) -> str:
    """Lower-case media type without parameters (``image/jpeg; charset=x`` -> ``image/jpeg``)."""
    return value.split(";", 1)[0].strip().lower()


def _content_code(value: str) -> int:
    if value in REASON_CODES:
        return _CONTENT_CODES[value]
    return _CONTENT_CODES.get(normalize_content_type(value), _OTHER_CODE)


@dataclass(frozen=True, slots=True)
class ChunkEntry:
    """One tile: status, raw body as received, media type, fetch time (unix seconds)."""

    status: ChunkStatus
    data: bytes = b""
    content_type: str = ""
    fetched_at: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.status, ChunkStatus):
            object.__setattr__(self, "status", ChunkStatus(self.status))
        if self.fetched_at < 0 or self.fetched_at > 0xFFFFFFFF:
            raise ValueError("fetched_at must fit an unsigned 32-bit integer")
        if len(self.data) > 0xFFFFFFFF:
            raise ValueError("chunk data too large")


def _corrupt(path: Path | None, reason: str) -> OsxpError:
    where = str(path) if path is not None else "<bytes>"
    return OsxpError(
        _CORRUPT_CODE,
        context={"path": where, "reason": reason, "texture": path.stem if path else "", "count": 0},
        message=f"Chunk container {where} is unreadable ({reason}); it is discarded.",
        remedy="The texture is downloaded again; if it repeats, check the disk.",
    )


class ChunkContainer:
    """The 256 tiles of one texture, in :func:`orthostudio.imagery.grid.texture_tiles` order."""

    __slots__ = ("entries",)

    def __init__(self, entries: Iterable[ChunkEntry] | None = None) -> None:
        if entries is None:
            self.entries: list[ChunkEntry] = [ChunkEntry(ChunkStatus.MISSING)] * CHUNK_COUNT
        else:
            self.entries = list(entries)
            if len(self.entries) != CHUNK_COUNT:
                raise ValueError(f"expected {CHUNK_COUNT} entries, got {len(self.entries)}")
            for e in self.entries:
                if not isinstance(e, ChunkEntry):
                    raise TypeError("entries must be ChunkEntry instances")

    def __len__(self) -> int:
        return CHUNK_COUNT

    def __iter__(self) -> Iterator[ChunkEntry]:
        return iter(self.entries)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ChunkContainer):
            return NotImplemented
        return self.entries == other.entries

    def get(self, i: int) -> ChunkEntry:
        """Entry ``i`` (0-255, row-major)."""
        if not 0 <= i < CHUNK_COUNT:
            raise IndexError(f"chunk index {i} outside [0, {CHUNK_COUNT})")
        return self.entries[i]

    def set(self, i: int, entry: ChunkEntry) -> None:
        """Replace entry ``i``."""
        if not 0 <= i < CHUNK_COUNT:
            raise IndexError(f"chunk index {i} outside [0, {CHUNK_COUNT})")
        if not isinstance(entry, ChunkEntry):
            raise TypeError("entry must be a ChunkEntry")
        self.entries[i] = entry

    def indices(self, status: ChunkStatus) -> list[int]:
        """Indices of the entries in ``status``."""
        return [i for i, e in enumerate(self.entries) if e.status is status]

    def complete(self) -> bool:
        """True when every tile has a final answer (no ``ERROR``, no ``NOT_FETCHED``)."""
        return all(
            e.status not in (ChunkStatus.ERROR, ChunkStatus.NOT_FETCHED) for e in self.entries
        )

    def digest(self) -> str:
        """blake3 of statuses, content types and bodies; independent of times and offsets."""
        h = blake3.blake3(_DIGEST_PREFIX)
        for e in self.entries:
            h.update(struct.pack("<BBI", int(e.status), _content_code(e.content_type), len(e.data)))
            h.update(e.data)
        return h.hexdigest()

    def to_bytes(self) -> bytes:
        """Serialise (``docs/specs/imagery-chunks.md`` section 3)."""
        index = bytearray()
        offset = HEADER_SIZE
        for e in self.entries:
            index += _INDEX_ENTRY.pack(
                offset, len(e.data), int(e.status), _content_code(e.content_type), e.fetched_at
            )
            offset += len(e.data)
        blobs = b"".join(e.data for e in self.entries)
        return _PREAMBLE.pack(MAGIC, CHUNK_COUNT, 0) + bytes(index) + blobs

    @classmethod
    def from_bytes(cls, data: bytes, *, path: Path | None = None) -> ChunkContainer:
        """Parse and validate; raises ``OsxpError`` (``IMG_*``) on any inconsistency."""
        if len(data) < HEADER_SIZE:
            raise _corrupt(path, f"file is {len(data)} bytes, header needs {HEADER_SIZE}")
        magic, count, _reserved = _PREAMBLE.unpack_from(data, 0)
        if magic != MAGIC:
            raise _corrupt(path, f"bad magic {magic!r}")
        if count != CHUNK_COUNT:
            raise _corrupt(path, f"entry count {count}, expected {CHUNK_COUNT}")
        entries: list[ChunkEntry] = []
        expected_offset = HEADER_SIZE
        for i in range(CHUNK_COUNT):
            offset, length, status, code, fetched_at = _INDEX_ENTRY.unpack_from(
                data, _PREAMBLE.size + i * _INDEX_ENTRY.size
            )
            if offset != expected_offset:
                raise _corrupt(path, f"entry {i}: offset {offset}, expected {expected_offset}")
            if offset + length > len(data):
                raise _corrupt(
                    path, f"entry {i}: blob ends at {offset + length}, file has {len(data)} bytes"
                )
            if status not in ChunkStatus.__members__.values():
                raise _corrupt(path, f"entry {i}: unknown status {status}")
            if code not in CONTENT_TYPES:
                raise _corrupt(path, f"entry {i}: unknown content type code {code}")
            blob = data[offset : offset + length]
            entries.append(ChunkEntry(ChunkStatus(status), blob, CONTENT_TYPES[code], fetched_at))
            expected_offset = offset + length
        if expected_offset != len(data):
            raise _corrupt(path, f"{len(data) - expected_offset} trailing bytes")
        return cls(entries)


class ChunkStore:
    """``<root>/<folder>/<zl>/<til_y>_<til_x>.chunks`` with atomic writes.

    The folder of a provider is its code, unless ``folders`` names another: a source of the
    user's is kept under its address as well (``providers.cache_name``)."""

    def __init__(
        self, root: Path, *, fsync: bool = True, folders: Mapping[str, str] | None = None
    ) -> None:
        self.root = Path(root)
        self.fsync = fsync
        self.folders = dict(folders or {})

    def path(self, t: TextureId) -> Path:
        folder = self.folders.get(t.provider, t.provider)
        return self.root / folder / str(t.zl) / f"{t.til_y}_{t.til_x}.chunks"

    def has(self, t: TextureId) -> bool:
        return self.path(t).is_file()

    def read(self, t: TextureId) -> ChunkContainer | None:
        """The container, ``None`` when absent; ``OsxpError`` when corrupted."""
        p = self.path(t)
        for attempt in range(READ_ATTEMPTS):
            try:
                data = p.read_bytes()
                break
            except FileNotFoundError:
                return None
            except PermissionError:
                # Windows: a container that a writer is renaming over cannot be opened for an
                # instant (POSIX readers see the old file or the new one, never an error).
                if os.name != "nt" or attempt == READ_ATTEMPTS - 1:
                    raise
                time.sleep(0.01 * (attempt + 1))
        return ChunkContainer.from_bytes(data, path=p)

    def write(self, t: TextureId, c: ChunkContainer) -> Path:
        """Write to a temporary file in the same directory, fsync, then rename over the target."""
        return atomic_write_bytes(self.path(t), c.to_bytes(), fsync=self.fsync)

    def delete(self, t: TextureId) -> bool:
        """Remove the container; True when it existed."""
        try:
            self.path(t).unlink()
        except FileNotFoundError:
            return False
        return True


def now_unix() -> int:
    """Current time as the unsigned 32-bit seconds stored in ``fetched_at``."""
    return int(time.time()) & 0xFFFFFFFF
