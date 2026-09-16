"""Parent tiles for the fallback of missing chunks: cache, memo and the recipe blob.

Spec: ``docs/specs/pipeline-textures.md`` section 5 and ``docs/specs/imagery-chunks.md``
section 2. A parent tile is a raw body at a lower zoom level; a tombstone records that the
provider has nothing there (placeholder or 404).

Dette D3: the cache used to be one flat file per parent tile
(``<chunks_root>/<provider>/_parents/<zl>/<y>_<x>.tile``, zero length = tombstone) because a
partially filled ``ChunkContainer`` had no way to say "this tile was never asked for" and
would have been misread as "404". ``ChunkStatus.NOT_FETCHED`` says it, so parents are stored
in ordinary containers at the parent level, under
``<chunks_root>/<provider>/_parents/<zl>/<til_y>_<til_x>.chunks`` (the same directory as
before, so an old flat cache is still read once and then ignored). The directory is a
*separate store*: a parent container is partial by nature and must never be mistaken for the
texture container of that parent level, which the texture pipeline owns.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from orthostudio.imagery.chunks import (
    CHUNK_COUNT,
    ChunkContainer,
    ChunkEntry,
    ChunkStatus,
    ChunkStore,
    now_unix,
)
from orthostudio.imagery.grid import TEXTURE_TILES, TextureId, texture_tiles

__all__ = [
    "BLOB_MAGIC",
    "ParentCache",
    "ParentKey",
    "ParentTile",
    "parent_chain",
    "parents_blob",
    "read_parents_blob",
    "resolve_parents",
]

ParentKey = tuple[int, int, int]
"""``(x, y, zl)`` of a tile."""

BLOB_MAGIC = b"OSXPPRT1"
_BLOB_HEADER = struct.Struct("<8sIIBI")  # magic, til_x, til_y, zl, count
_BLOB_ENTRY = struct.Struct("<BIIBI")  # zl, x, y, has_body, length
_PARENTS_DIR = "_parents"


@dataclass(frozen=True, slots=True)
class ParentTile:
    """A cached parent: ``body`` is ``None`` for a tombstone (placeholder or 404)."""

    body: bytes | None

    @property
    def is_tombstone(self) -> bool:
        return self.body is None


class ParentCache:
    """Parent tiles of one provider: run memo, read-through of the chunk store, own store.

    The own store holds one :class:`ChunkContainer` per parent-level texture, with
    ``NOT_FETCHED`` for the tiles no chain has reached yet (dette D3).
    """

    def __init__(self, chunks_root: Path, provider: str, *, fsync: bool = False) -> None:
        self.root = Path(chunks_root) / provider / _PARENTS_DIR
        self.provider = provider
        self.fsync = fsync
        self._memo: dict[ParentKey, ParentTile] = {}
        self._chunk_store = ChunkStore(chunks_root, fsync=fsync)
        self._containers: dict[TextureId, ChunkContainer | None] = {}
        # own store: <chunks_root>/<provider>/_parents/<zl>/<til_y>_<til_x>.chunks
        self._store = ChunkStore(Path(chunks_root) / provider, fsync=fsync)
        self._own: dict[TextureId, ChunkContainer] = {}

    def texture_id(self, x: int, y: int, zl: int) -> TextureId:
        """Parent-level container holding tile ``(x, y)`` (provider ``_parents``)."""
        return TextureId(
            x // TEXTURE_TILES * TEXTURE_TILES, y // TEXTURE_TILES * TEXTURE_TILES, zl, _PARENTS_DIR
        )

    def path(self, x: int, y: int, zl: int) -> Path:
        """Container file holding tile ``(x, y, zl)`` (dette D3: was one file per tile)."""
        return self._store.path(self.texture_id(x, y, zl))

    def _flat_path(self, x: int, y: int, zl: int) -> Path:
        """Pre-D3 flat cache entry, still read (once) so a warm cache is not thrown away."""
        return self.root / str(zl) / f"{y}_{x}.tile"

    @staticmethod
    def _index(t: TextureId, x: int, y: int) -> int:
        return TEXTURE_TILES * (y - t.til_y) + (x - t.til_x)

    def _own_container(self, t: TextureId) -> ChunkContainer:
        c = self._own.get(t)
        if c is None:
            try:
                c = self._store.read(t)
            except Exception:  # corrupted: a cache, so it is simply rebuilt
                c = None
            if c is None:
                c = ChunkContainer([ChunkEntry(ChunkStatus.NOT_FETCHED)] * CHUNK_COUNT)
            self._own[t] = c
        return c

    # -- lookups -----------------------------------------------------------------------

    def lookup(self, x: int, y: int, zl: int) -> ParentTile | None:
        """The parent if known (memo, complete container at ``zl``, flat cache), else None."""
        key = (x, y, zl)
        hit = self._memo.get(key)
        if hit is not None:
            return hit
        from_container = self._from_container(x, y, zl)
        if from_container is not None:
            self._memo[key] = from_container
            return from_container
        tile = self._from_own(x, y, zl)
        if tile is None:
            tile = self._from_flat(x, y, zl)
        if tile is not None:
            self._memo[key] = tile
        return tile

    def _from_own(self, x: int, y: int, zl: int) -> ParentTile | None:
        """The cache's own container: ``NOT_FETCHED`` (or ``ERROR``) means "unknown"."""
        t = self.texture_id(x, y, zl)
        entry = self._own_container(t).get(self._index(t, x, y))
        if entry.status is ChunkStatus.OK:
            return ParentTile(entry.data)
        if entry.status in (ChunkStatus.MISSING, ChunkStatus.PLACEHOLDER):
            return ParentTile(None)
        return None

    def _from_flat(self, x: int, y: int, zl: int) -> ParentTile | None:
        try:
            data = self._flat_path(x, y, zl).read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return None
        return ParentTile(data if data else None)

    def _from_container(self, x: int, y: int, zl: int) -> ParentTile | None:
        t = TextureId(
            x // TEXTURE_TILES * TEXTURE_TILES,
            y // TEXTURE_TILES * TEXTURE_TILES,
            zl,
            self.provider,
        )
        if t not in self._containers:
            container = None
            if self._chunk_store.has(t):
                try:
                    container = self._chunk_store.read(t)
                except Exception:  # corrupted container: ignored here, rebuilt by its own run
                    container = None
            self._containers[t] = (
                container if container is not None and container.complete() else None
            )
        container = self._containers[t]
        if container is None:
            return None
        entry = container.get(TEXTURE_TILES * (y - t.til_y) + (x - t.til_x))
        if entry.status is ChunkStatus.OK:
            return ParentTile(entry.data)
        if entry.status in (ChunkStatus.MISSING, ChunkStatus.PLACEHOLDER):
            return ParentTile(None)
        return None

    # -- writes ------------------------------------------------------------------------

    def put(self, x: int, y: int, zl: int, body: bytes | None) -> None:
        """Record a fetched parent (``None`` = tombstone) in the memo and on disk.

        A tombstone is stored as ``MISSING``: the provider answered and has nothing there.
        The container is rewritten at once, so an interrupted run keeps what it downloaded.
        """
        tile = ParentTile(body)
        self._memo[(x, y, zl)] = tile
        t = self.texture_id(x, y, zl)
        container = self._own_container(t)
        entry = (
            ChunkEntry(ChunkStatus.OK, body, "", now_unix())
            if body
            else ChunkEntry(ChunkStatus.MISSING, b"", "", now_unix())
        )
        container.set(self._index(t, x, y), entry)
        self._store.write(t, container)

    def forget(self, x: int, y: int, zl: int) -> None:
        self._memo.pop((x, y, zl), None)


# --- the recipe blob -----------------------------------------------------------------------


def parents_blob(t: TextureId, parents: Mapping[ParentKey, ParentTile]) -> bytes:
    """Serialise the parents consulted for texture ``t`` (sorted, tombstones included)."""
    keys = sorted(parents)
    out = bytearray(_BLOB_HEADER.pack(BLOB_MAGIC, t.til_x, t.til_y, t.zl, len(keys)))
    for x, y, zl in keys:
        body = parents[(x, y, zl)].body
        out += _BLOB_ENTRY.pack(zl, x, y, 0 if body is None else 1, len(body or b""))
        if body:
            out += body
    return bytes(out)


def read_parents_blob(data: bytes) -> tuple[TextureId, dict[ParentKey, bytes | None]]:
    """Inverse of :func:`parents_blob`; the provider of the returned id is empty."""
    if len(data) < _BLOB_HEADER.size:
        raise ValueError("parents blob truncated")
    magic, til_x, til_y, zl, count = _BLOB_HEADER.unpack_from(data, 0)
    if magic != BLOB_MAGIC:
        raise ValueError(f"bad parents blob magic {magic!r}")
    offset = _BLOB_HEADER.size
    parents: dict[ParentKey, bytes | None] = {}
    for _ in range(count):
        p_zl, x, y, has_body, length = _BLOB_ENTRY.unpack_from(data, offset)
        offset += _BLOB_ENTRY.size
        if has_body:
            parents[(x, y, p_zl)] = bytes(data[offset : offset + length])
            offset += length
        else:
            parents[(x, y, p_zl)] = None
    if offset != len(data):
        raise ValueError("parents blob has trailing bytes")
    return TextureId(til_x, til_y, zl, ""), parents


def parent_chain(x: int, y: int, zl: int, levels: int) -> Iterable[ParentKey]:
    """``(x >> d, y >> d, zl - d)`` for ``d = 1..levels`` while ``zl - d >= 0``."""
    for d in range(1, levels + 1):
        if zl - d < 0:
            return
        yield (x >> d, y >> d, zl - d)


def resolve_parents(
    t: TextureId,
    container: ChunkContainer,
    lookup: Callable[[int, int, int], ParentTile | None],
    levels: int,
    known: Mapping[ParentKey, ParentTile] | None = None,
) -> tuple[dict[ParentKey, ParentTile], set[ParentKey]]:
    """Walk the parent chain of every ``MISSING``/``PLACEHOLDER`` chunk of ``container``.

    Returns the parents consulted (tombstones included, the body that ends each chain) and
    the parents still unknown (``lookup`` returned ``None``): the chains that stop there wait
    for a fetch. ``known`` seeds the walk with parents already resolved.
    """
    holes = container.indices(ChunkStatus.MISSING) + container.indices(ChunkStatus.PLACEHOLDER)
    parents: dict[ParentKey, ParentTile] = dict(known or {})
    unknown: set[ParentKey] = set()
    if not holes:
        return parents, unknown
    tiles = texture_tiles(t)
    for i in sorted(holes):
        x, y = tiles[i]
        for key in parent_chain(x, y, t.zl, levels):
            tile = parents.get(key)
            if tile is None:
                tile = lookup(*key)
            if tile is None:
                unknown.add(key)
                break
            parents[key] = tile
            if not tile.is_tombstone:
                break
    return parents, unknown
