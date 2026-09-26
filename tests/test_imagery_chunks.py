# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Unit tests of the chunk container and store (``docs/specs/imagery-chunks.md``, C1-C4)."""

from __future__ import annotations

import random
import struct
import threading
from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.imagery.chunks import (
    CHUNK_COUNT,
    CONTENT_TYPES,
    HEADER_SIZE,
    MAGIC,
    ChunkContainer,
    ChunkEntry,
    ChunkStatus,
    ChunkStore,
    normalize_content_type,
)
from orthostudio.imagery.grid import TextureId

T = TextureId(8448, 6016, 14, "BI")


def _random_container(seed: int) -> ChunkContainer:
    rng = random.Random(seed)
    entries = []
    for i in range(CHUNK_COUNT):
        status = rng.choice(list(ChunkStatus))
        if status is ChunkStatus.OK:
            data = rng.randbytes(rng.randint(1, 3000))
            ct = rng.choice(["image/jpeg", "image/png", "image/webp; q=1", "IMAGE/JPEG"])
        elif status is ChunkStatus.ERROR:
            data, ct = b"", rng.choice(["", "timeout", "text/html"])
        else:
            data, ct = b"", ""
        entries.append(
            ChunkEntry(status, data, ct, fetched_at=rng.randint(0, 2**32 - 1) if i % 3 else 0)
        )
    return ChunkContainer(entries)


def _fixed_container() -> ChunkContainer:
    entries = [
        ChunkEntry(ChunkStatus.OK, bytes([i]) * (i + 1), "image/jpeg", 1_700_000_000 + i)
        for i in range(CHUNK_COUNT)
    ]
    entries[7] = ChunkEntry(ChunkStatus.PLACEHOLDER)
    entries[100] = ChunkEntry(ChunkStatus.MISSING)
    return ChunkContainer(entries)


# ---------------------------------------------------------------- container


def test_new_container_is_all_missing_but_complete() -> None:
    c = ChunkContainer()
    assert len(c) == CHUNK_COUNT
    assert c.indices(ChunkStatus.MISSING) == list(range(CHUNK_COUNT))
    assert c.complete()
    c.set(3, ChunkEntry(ChunkStatus.ERROR, b"", "timeout"))
    assert not c.complete()
    assert c.indices(ChunkStatus.ERROR) == [3]
    assert c.get(3).content_type == "timeout"
    with pytest.raises(IndexError):
        c.get(256)
    with pytest.raises(ValueError):
        ChunkContainer([ChunkEntry(ChunkStatus.OK)] * 5)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_round_trip(seed: int) -> None:
    c = _random_container(seed)
    blob = c.to_bytes()
    assert blob[:8] == MAGIC
    assert len(blob) == HEADER_SIZE + sum(len(e.data) for e in c)
    back = ChunkContainer.from_bytes(blob)
    for a, b in zip(c, back, strict=True):
        assert a.status is b.status and a.data == b.data and a.fetched_at == b.fetched_at
        assert b.content_type == (
            normalize_content_type(a.content_type)
            if normalize_content_type(a.content_type) in CONTENT_TYPES.values()
            else "application/octet-stream"
        )
    assert back.digest() == c.digest()
    assert ChunkContainer.from_bytes(back.to_bytes()) == back


def test_index_layout_at_documented_offsets() -> None:
    c = _fixed_container()
    blob = c.to_bytes()
    count, reserved = struct.unpack_from("<II", blob, 8)
    assert (count, reserved) == (256, 0)
    off0, len0, st0, ct0, at0 = struct.unpack_from("<QIBBI", blob, 16)
    assert (off0, len0, st0, ct0, at0) == (HEADER_SIZE, 1, 0, 1, 1_700_000_000)
    off7, len7, st7, ct7, at7 = struct.unpack_from("<QIBBI", blob, 16 + 7 * 18)
    assert (len7, st7, ct7, at7) == (0, 2, 0, 0)
    assert off7 == HEADER_SIZE + sum(i + 1 for i in range(7))
    assert blob[off0 : off0 + 1] == b"\x00"


def test_digest_is_stable_and_sensitive() -> None:
    c = _fixed_container()
    golden = c.digest()
    assert len(golden) == 64
    # independent of fetch times
    shifted = ChunkContainer([ChunkEntry(e.status, e.data, e.content_type, 0) for e in c])
    assert shifted.digest() == golden
    one_byte = ChunkContainer(
        [
            ChunkEntry(e.status, (b"\xff" + e.data[1:]) if i == 0 else e.data, e.content_type)
            for i, e in enumerate(c)
        ]
    )
    assert one_byte.digest() != golden
    one_status = ChunkContainer(
        [
            ChunkEntry(ChunkStatus.ERROR if i == 100 else e.status, e.data, e.content_type)
            for i, e in enumerate(c)
        ]
    )
    assert one_status.digest() != golden
    one_type = ChunkContainer(
        [
            ChunkEntry(e.status, e.data, "image/png" if i == 1 else e.content_type)
            for i, e in enumerate(c)
        ]
    )
    assert one_type.digest() != golden


def test_digest_golden_value() -> None:
    entries = [ChunkEntry(ChunkStatus.MISSING)] * CHUNK_COUNT
    entries[0] = ChunkEntry(ChunkStatus.OK, b"abc", "image/jpeg", 42)
    c = ChunkContainer(entries)
    # frozen value: a change here means the digest definition changed (flag day)
    assert c.digest() == GOLDEN_DIGEST


GOLDEN_DIGEST = "10416b55a2476eff5547c1a87abf4a56b96c48392602812ff4241dd891fd6149"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda b: b[:100], "header needs"),
        (lambda b: b"OSXPCHK2" + b[8:], "bad magic"),
        (lambda b: b[:8] + struct.pack("<II", 255, 0) + b[16:], "entry count"),
        (lambda b: b[:-1], "blob ends"),
        (lambda b: b + b"x", "trailing"),
        (lambda b: b[:16] + struct.pack("<QIBBI", HEADER_SIZE + 1, 1, 0, 1, 0) + b[34:], "offset"),
        (
            lambda b: b[:16] + struct.pack("<QIBBI", HEADER_SIZE, 1, 9, 1, 0) + b[34:],
            "unknown status",
        ),
        (
            lambda b: b[:16] + struct.pack("<QIBBI", HEADER_SIZE, 1, 0, 42, 0) + b[34:],
            "content type code",
        ),
    ],
)
def test_corrupted_bytes_raise_coded_error(mutate, reason: str) -> None:
    blob = _fixed_container().to_bytes()
    with pytest.raises(OsxpError) as info:
        ChunkContainer.from_bytes(mutate(blob), path=Path("/store/BI/14/6016_8448.chunks"))
    err = info.value
    assert err.code.startswith("IMG_")
    assert err.context["path"] == str(Path("/store/BI/14/6016_8448.chunks"))
    assert reason in err.context["reason"]
    assert reason in err.message


def test_truncated_at_every_97th_byte() -> None:
    blob = _fixed_container().to_bytes()
    for cut in range(0, len(blob), 97):
        with pytest.raises(OsxpError):
            ChunkContainer.from_bytes(blob[:cut])


def test_entry_validation() -> None:
    with pytest.raises(ValueError):
        ChunkEntry(ChunkStatus.OK, b"", "", fetched_at=2**32)
    e = ChunkEntry(0, b"x")  # type: ignore[arg-type]
    assert e.status is ChunkStatus.OK
    assert normalize_content_type("Image/JPEG; charset=binary") == "image/jpeg"


# ---------------------------------------------------------------- store


def test_store_paths_and_lifecycle(tmp_path: Path) -> None:
    store = ChunkStore(tmp_path / "chunks")
    assert store.path(T) == tmp_path / "chunks" / "BI" / "14" / "6016_8448.chunks"
    assert not store.has(T)
    assert store.read(T) is None
    c = _random_container(9)
    final = store.write(T, c)
    assert final == store.path(T) and store.has(T)
    assert [p.name for p in final.parent.iterdir()] == ["6016_8448.chunks"]  # no tmp left
    assert store.read(T) == ChunkContainer.from_bytes(c.to_bytes())  # types normalised
    # overwrite is atomic and idempotent
    c2 = _random_container(10)
    store.write(T, c2)
    assert store.read(T) == ChunkContainer.from_bytes(c2.to_bytes())
    assert [p.name for p in final.parent.iterdir()] == ["6016_8448.chunks"]
    assert store.delete(T) and not store.delete(T)
    assert store.read(T) is None


def test_store_reports_corruption_with_path(tmp_path: Path) -> None:
    store = ChunkStore(tmp_path, fsync=False)
    store.write(T, _fixed_container())
    p = store.path(T)
    p.write_bytes(p.read_bytes()[:5000])
    with pytest.raises(OsxpError) as info:
        store.read(T)
    assert info.value.context["path"] == str(p)
    assert store.has(T)  # the caller decides to delete and refetch


def test_reader_never_sees_a_partial_file(tmp_path: Path) -> None:
    store = ChunkStore(tmp_path, fsync=False)
    containers = [_random_container(s) for s in range(4)]
    digests = {c.digest() for c in containers}
    stop = threading.Event()
    seen: list[str] = []
    failures: list[BaseException] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                c = store.read(T)
            except BaseException as exc:
                failures.append(exc)
                return
            if c is not None:
                seen.append(c.digest())

    th = threading.Thread(target=reader)
    th.start()
    for _ in range(25):
        for c in containers:
            store.write(T, c)
    stop.set()
    th.join()
    assert not failures
    assert seen and set(seen) <= digests
