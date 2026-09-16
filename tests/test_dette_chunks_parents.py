"""Dette D3: ``ChunkStatus.NOT_FETCHED`` and the parent cache stored in real containers.

The flat cache (one zero-or-more-byte ``.tile`` file per parent tile) existed only because a
container could not say "never asked for". It can now, so parents live in ordinary
containers at the parent level, in a store of their own
(``docs/specs/imagery-chunks.md`` section 2.1).
"""

from __future__ import annotations

from pathlib import Path

from orthostudio.imagery.chunks import (
    CHUNK_COUNT,
    ChunkContainer,
    ChunkEntry,
    ChunkStatus,
    ChunkStore,
)
from orthostudio.imagery.grid import TextureId
from orthostudio.pipeline.parents import ParentCache, ParentTile

# -- the status ------------------------------------------------------------------------------


def test_not_fetched_round_trips_and_is_not_complete() -> None:
    c = ChunkContainer([ChunkEntry(ChunkStatus.NOT_FETCHED)] * CHUNK_COUNT)
    assert not c.complete()
    back = ChunkContainer.from_bytes(c.to_bytes())
    assert back == c and back.indices(ChunkStatus.NOT_FETCHED) == list(range(CHUNK_COUNT))
    c.set(0, ChunkEntry(ChunkStatus.OK, b"x", "image/jpeg", 1))
    assert not c.complete()  # one tile is still unknown
    full = ChunkContainer([ChunkEntry(ChunkStatus.MISSING)] * CHUNK_COUNT)
    assert full.complete()  # unchanged meaning for a texture container


def test_a_container_written_before_d3_reads_back_unchanged() -> None:
    """Backwards compatibility: statuses 0-3 only, same magic, same offsets."""
    entries = [ChunkEntry(ChunkStatus.MISSING)] * CHUNK_COUNT
    entries[0] = ChunkEntry(ChunkStatus.OK, b"body", "image/jpeg", 7)
    entries[1] = ChunkEntry(ChunkStatus.PLACEHOLDER)
    entries[2] = ChunkEntry(ChunkStatus.ERROR, b"", "NET_TIMEOUT", 9)
    old = ChunkContainer(entries)
    raw = old.to_bytes()
    assert raw[:8] == b"OSXPCHK1"
    assert max(raw[16 + 18 * i + 12] for i in range(CHUNK_COUNT)) <= 3  # no 4 is written
    assert ChunkContainer.from_bytes(raw) == old
    assert ChunkContainer.from_bytes(raw).digest() == old.digest()


def test_the_digest_separates_not_fetched_from_missing() -> None:
    a = ChunkContainer([ChunkEntry(ChunkStatus.MISSING)] * CHUNK_COUNT)
    b = ChunkContainer([ChunkEntry(ChunkStatus.NOT_FETCHED)] * CHUNK_COUNT)
    assert a.digest() != b.digest()


# -- the parent cache ------------------------------------------------------------------------


def test_parents_are_stored_in_a_container_at_the_parent_level(tmp_path: Path) -> None:
    cache = ParentCache(tmp_path, "T", fsync=False)
    assert cache.lookup(300, 200, 12) is None  # nothing known yet
    cache.put(300, 200, 12, b"jpeg-body")
    cache.put(301, 200, 12, None)  # tombstone: the provider has nothing there

    path = cache.path(300, 200, 12)
    assert path == tmp_path / "T" / "_parents" / "12" / "192_288.chunks"
    saved = ChunkContainer.from_bytes(path.read_bytes())
    t = cache.texture_id(300, 200, 12)
    assert (t.til_x, t.til_y, t.zl) == (288, 192, 12)
    assert saved.get(8 * 16 + 12).status is ChunkStatus.OK
    assert saved.get(8 * 16 + 12).data == b"jpeg-body"
    assert saved.get(8 * 16 + 13).status is ChunkStatus.MISSING
    assert len(saved.indices(ChunkStatus.NOT_FETCHED)) == CHUNK_COUNT - 2


def test_a_restart_reads_bodies_tombstones_and_unknowns_apart(tmp_path: Path) -> None:
    cache = ParentCache(tmp_path, "T", fsync=False)
    cache.put(300, 200, 12, b"jpeg-body")
    cache.put(301, 200, 12, None)
    fresh = ParentCache(tmp_path, "T", fsync=False)
    assert fresh.lookup(300, 200, 12) == ParentTile(b"jpeg-body")
    assert fresh.lookup(301, 200, 12) == ParentTile(None)
    assert fresh.lookup(301, 200, 12).is_tombstone
    # a neighbour in the very same container was never asked for: still unknown, not a 404
    assert fresh.lookup(302, 200, 12) is None
    assert fresh.lookup(300, 201, 12) is None


def test_a_pre_d3_flat_cache_is_still_read(tmp_path: Path) -> None:
    flat = tmp_path / "T" / "_parents" / "12"
    flat.mkdir(parents=True)
    (flat / "200_300.tile").write_bytes(b"old-body")
    (flat / "200_301.tile").write_bytes(b"")  # old tombstone
    cache = ParentCache(tmp_path, "T", fsync=False)
    assert cache.lookup(300, 200, 12) == ParentTile(b"old-body")
    assert cache.lookup(301, 200, 12) == ParentTile(None)
    assert cache.lookup(302, 200, 12) is None


def test_the_parent_store_never_collides_with_a_texture_container(tmp_path: Path) -> None:
    """A partial container must not be readable as the texture of that zoom level."""
    cache = ParentCache(tmp_path, "T", fsync=False)
    cache.put(300, 200, 12, b"jpeg-body")
    texture = TextureId(288, 192, 12, "T")
    store = ChunkStore(tmp_path, fsync=False)
    assert store.path(texture) != cache.path(300, 200, 12)
    assert not store.has(texture)


def test_a_corrupted_parent_container_is_ignored_not_raised(tmp_path: Path) -> None:
    cache = ParentCache(tmp_path, "T", fsync=False)
    cache.put(300, 200, 12, b"jpeg-body")
    cache.path(300, 200, 12).write_bytes(b"not a container at all")
    fresh = ParentCache(tmp_path, "T", fsync=False)
    assert fresh.lookup(300, 200, 12) is None  # unknown: it is only a cache, so refetch
    fresh.put(300, 200, 12, b"again")
    assert ParentCache(tmp_path, "T").lookup(300, 200, 12) == ParentTile(b"again")


def test_a_complete_texture_container_is_still_the_first_source(tmp_path: Path) -> None:
    """Unchanged behaviour: a parent already downloaded as a texture is reused as is."""
    entries = [ChunkEntry(ChunkStatus.MISSING)] * CHUNK_COUNT
    entries[8 * 16 + 12] = ChunkEntry(ChunkStatus.OK, b"from-texture", "image/jpeg", 1)
    ChunkStore(tmp_path, fsync=False).write(TextureId(288, 192, 12, "T"), ChunkContainer(entries))
    cache = ParentCache(tmp_path, "T", fsync=False)
    assert cache.lookup(300, 200, 12) == ParentTile(b"from-texture")
    assert cache.lookup(301, 200, 12) == ParentTile(None)  # MISSING in a complete container
