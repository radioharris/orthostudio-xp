# Imagery chunks: the per-texture container of raw tiles and its store

Status: P1, written before `src/orthostudio/imagery/chunks.py` (tests
`tests/test_imagery_chunks.py`). Origin: invented here. Ortho4XP keeps no raw tiles: the 256 tiles
of a texture are pasted into one `Image` and saved as a 4096² JPEG q75
(`O4_Imagery_Utils.py:1329-1368`, `:1733-1743`), which loses the per-tile status (a white fill for a
missing tile is saved and never questioned again, `IMG_CACHE_INCOMPLETE` in `docs/specs/errors.md`).
Decision: **new mechanism**; the Ortho4XP JPEG cache is kept as a read-only fallback.

## 1. The rule in plain language

For every texture the store keeps one file holding the 256 tiles **as received** (JPEG or
PNG bytes, untouched) with a status per tile. Re-encoding never happens before assembly, the
file is written atomically, and its content digest is what the artefact graph hashes.

## 2. Statuses

| Status | Value | Meaning | `data` |
|---|---|---|---|
| `OK` | 0 | an image body was received | the bytes |
| `MISSING` | 1 | not fetched yet, or the provider has no such tile (HTTP 404) | empty |
| `PLACEHOLDER` | 2 | HTTP 200 "no imagery" tile recognised by `is_placeholder` (tombstone, `net-download.md` R5) | empty (the placeholder body is not kept) |
| `ERROR` | 3 | transport or server failure after the fetcher's retries, or a 200 that is not a complete image; **retryable** | empty, `content_type` holds the reason code (`NET_TIMEOUT`, `NET_CONNECTION_FAILED`, `NET_RATE_LIMITED`, `NET_SERVER_ERROR`, `NET_UNEXPECTED_STATUS`, `SYS_CANCELLED`, `IMG_BAD_CONTENT_TYPE`, `IMG_TILE_CORRUPTED`), persisted by the format (section 3) |
| `NOT_FETCHED` | 4 | never asked for: the tile is **unknown**, not a hole (dette D3) | empty |

`complete()` is `True` when no entry is `ERROR` or `NOT_FETCHED`: every tile has a final
answer, the texture can be assembled (missing and placeholder tiles go to the parent
fallback, `assemble.py`). `indices(status)` lists the entries of one status. A new container
has 256 `MISSING` entries.

### 2.1 `NOT_FETCHED` and the parent cache (dette D3)

`MISSING` conflates two things in P1: "not downloaded yet" and "the provider has no such tile
(404)". For a *texture* container that is harmless, because every tile of a texture is asked
for: what is still `MISSING` at the end of a run is a hole either way. For a **partial**
container it is wrong, which is why `orthostudio.pipeline.parents.ParentCache` used to keep one flat
file per parent tile: only a fraction of a parent-level texture is ever consulted, and the
untouched tiles would have read back as 404.

`NOT_FETCHED` is that missing state. The parent cache now stores its tiles in ordinary
containers, in a store of its own:

```
<chunks_root>/<folder>/_parents/<zl>/<til_y>_<til_x>.chunks
```

with `OK` + body for a parent that was downloaded, `MISSING` for a tombstone (404 or
placeholder: the provider answered and has nothing there) and `NOT_FETCHED` everywhere else.
The directory is the same as the pre-D3 flat cache, so nothing else moves; the store is
**separate from the texture store** on purpose: a partial container must never be read as the
texture container of that zoom level, which the texture pipeline owns and assumes complete.
`ParentCache.lookup` still reads a pre-D3 `<y>_<x>.tile` file when the container has
`NOT_FETCHED` there, so a warm cache survives the upgrade; nothing writes that form again.

The meaning of `MISSING` in a texture container is unchanged in P1 (`textures.py` keeps
creating containers full of `MISSING` for "to download"). Narrowing it to "404 only"
everywhere is a pipeline change, not a format change; see the `dette` report.

## 3. File format (`to_bytes` / `from_bytes`), little-endian

```
offset  size  field
0       8     magic  b"OSXPCHK1"  (the trailing digit is the format version)
8       4     u32    entry count, always 256
12      4     u32    reserved, 0
16      4608  index: 256 x 18 bytes, in texture_tiles order (row-major, y then x)
              u64 offset (absolute, from the start of the file)
              u32 length
              u8  status
              u8  content type code
              u32 fetched_at (unix seconds, 0 when unknown)
4624    ...   blobs, concatenated in index order, no padding
```

Content type codes: 0 `""`, 1 `image/jpeg`, 2 `image/png`, 3 `image/webp`, 4 `image/gif`,
5 `image/bmp`, 6 `image/tiff`, 7 `text/html`, 8 `text/plain`, 9 `application/json`,
10 `application/xml`, 11 `text/xml`, 255 `application/octet-stream` (any other type; the
original string is not kept). Types are normalised before coding: lower case, parameters
after `;` dropped. Codes 100-107 are the **reason codes** of `ERROR` entries (review: the
reason used to come back as `application/octet-stream`): 100 `NET_TIMEOUT`, 101
`NET_CONNECTION_FAILED`, 102 `NET_RATE_LIMITED`, 103 `NET_SERVER_ERROR`, 104
`NET_UNEXPECTED_STATUS`, 105 `SYS_CANCELLED`, 106 `IMG_BAD_CONTENT_TYPE`, 107
`IMG_TILE_CORRUPTED` (`REASON_CODES`). A file written before this table read them as 255.

Status 4 (`NOT_FETCHED`) is new in dette D3. **Reading is backwards compatible**: no file
written before it can contain a 4, so every existing container reads unchanged and the magic
stays `OSXPCHK1`. Forward compatibility is deliberately not offered: a reader built before D3
that meets a 4 raises the same "unknown status" corruption error as for any unknown byte, and
discards the file. That is acceptable because the only containers holding a 4 are the parent
cache's own files (section 2.1), which are a cache: discarded means refetched, never wrong
pixels. A texture container never holds a 4.

Validation on read: magic and version, count, file length, every blob inside the file and
within the file, offsets contiguous in index order (no gap, no overlap, no trailing bytes),
status and code known. Any failure raises
`OsxpError` (see section 6) naming the path and the reason, so the caller discards the file
and fetches again; a truncated file is never partially trusted.

## 4. Digest

`digest()` is the blake3 hex of `b"osxp-chunks-digest-1"` followed, for each entry in order,
by `struct.pack("<BBI", status, code, len(data))` and the data. It ignores `fetched_at` and
the byte offsets, so re-writing the same tiles at another time gives the same digest, and a
changed status or body changes it. Two containers with the same digest assemble to the same
texture.

## 5. Store layout and write protocol (`ChunkStore`)

`<root>/<folder>/<zl>/<til_y>_<til_x>.chunks`, e.g. `BI/14/6016_8448.chunks`. The folder of a
provider is its code unless `ChunkStore(folders=...)` names another: a source of the user's is kept
under its address as well (`imagery-providers.md` 4.1, `cache_name`). `write`
serialises to `<name>.tmp-<pid>-<rand8>` in the same directory, fsyncs (unless
`ChunkStore(root, fsync=False)`), then `os.replace` onto the final name (`orthostudio.fsutil.
atomic_write_bytes`, shared by every writer of the project): a reader sees the
old file or the new one, never a partial one; an abandoned tmp file is ignored by `read` and
`has` (they only look at the final name). `read` returns `None` when the file is absent and
raises on a corrupted one.

## 6. No legacy fallback

Until decision 0010 the module could read a texture of Ortho4XP's JPEG cache (`legacy_jpeg_path`,
`read_legacy_texture`), for the comparisons with Ortho4XP. It no longer can: nothing of an Ortho4XP
folder enters OrthoStudio XP.

Error codes: the registry (`src/orthostudio/errors.py`, outside this chantier) has no "chunk
container corrupted" code. Until `IMG_CHUNKS_CORRUPTED` is registered, the container raises
`IMG_CACHE_INCOMPLETE` (degraded / continue, remedy "downloaded again") with an explicit
message and `context={"path", "reason"}`.

## 7. Acceptance tests

| # | Test | Where |
|---|---|---|
| C1 | round trip `from_bytes(to_bytes(c))` for random statuses, bodies and types; index at the documented offsets; `len(to_bytes) == 4624 + sum(len(data))` | `test_imagery_chunks.py` |
| C2 | digest stable across `fetched_at`, across processes (fixed golden value for a fixed container) and changed by one byte or one status | idem |
| C3 | truncated file at every 97th byte, bad magic, wrong count, offset past EOF, unknown status: `OsxpError` with an `IMG_` code and the path in the context | idem |
| C4 | store: `path`, `has`, `read` of an absent texture is `None`, `write` leaves no tmp file, a concurrent reader never sees a partial file (write then read under a thread), a corrupted file raises | idem |
| C6 | `NOT_FETCHED` round-trips through `to_bytes`/`from_bytes`, makes `complete()` false, and a container written before D3 (statuses 0-3 only) reads back unchanged | `test_dette_chunks_parents.py` |
| C7 | the parent cache writes one container per parent-level texture with `NOT_FETCHED` everywhere it was not asked, reads back bodies and tombstones after a restart, and still reads a pre-D3 flat `.tile` cache | `test_dette_chunks_parents.py` |
