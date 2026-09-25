"""The baked library a build reads over HTTP, and the key it opens with.

Specification: ``docs/specs/osm-prepared.md``. The files are what ``tools/bake`` writes: one
snapshot per tile and per layer, under ``osm/<cell>/<tile>/<tile>_<layer>.osm.json.zst``, listed
by a ``manifest.json`` that gives each one its size and the content digest of its layer.

**The key.** Every request carries a token, the manifest included, and the server answers nothing
without it. It does not make the library secret -- whoever unpacks the app finds the token -- and
that is not what it is for. It is for being able to close the door again: the address stays out
of the README, the forum and the release notes, so nobody finds the library by looking around,
and the day someone settles in, the next version carries another token. The format is a second
door: Ortho4XP reads bzip2 OSM XML and this is zstd JSON, so even a file that leaked cannot be
dropped into it (2026-09-23).

**What is refused.** The manifest must hold every layer of the tile, none of them empty, and each
file must carry the digest the manifest announced. Anything else and the tile goes to the next
source, which is what ``chain.py`` is for.
"""

from __future__ import annotations

import functools
import io
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import orjson
import zstandard

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.sources.osm import LayerSpec, OsmSnapshot, layers_for, narrowed
from orthostudio.sources.prepared import EMPTY_LAYER_BYTES as EMPTY_BYTES


class LibraryError(OsxpError):
    """The library announced a tile and then did not serve it (``OSM_LIBRARY_INCOMPLETE``).

    Not the same as not holding it: a tile absent from the manifest costs nothing and the chain
    moves on quietly, while a tile announced and refused costs four requests, for every tile of
    the batch. Raised so that the chain counts it and sets the library aside (``chain.Chain``);
    the case is an upload still in progress, a file deleted under the manifest, or a key revoked
    mid-build.
    """

    def __init__(self, reason: str, *, tile: str = "", layer: str = "") -> None:
        super().__init__(
            "OSM_LIBRARY_INCOMPLETE", context={"tile": tile, "layer": layer, "reason": reason}
        )


__all__ = [
    "LIBRARY_TIMEOUT_S",
    "MAX_SNAPSHOT_BYTES",
    "LibraryError",
    "LibraryIndex",
    "LibrarySource",
    "parse_manifest",
    "shipped_library",
]

log = logging.getLogger("orthostudio.sources.library")

SHIPPED_NAME = "library.json"
"""Where a release carries the address and the key: written when the installers are built, from
the secrets of the repository, never committed.

The repository is public, so neither may live in it. A build from source therefore has no key,
reaches no library, and downloads every tile live, which is the behaviour of every version before
this one. Whoever runs their own server fills the two settings instead.
"""
MANIFEST_NAME = "manifest.json"
FORMAT = "osxp-baked-1"
CONNECT_TIMEOUT_S = 5.0
"""What reaching the server may take."""
LIBRARY_TIMEOUT_S = 30.0
"""How long a request may go without receiving a byte. A library exists to save seconds; one that
hangs would cost them, so silence is a failure and the chain moves on (``osm-prepared.md`` 4).

Silence, not slowness. A file of the planet library weighs up to 61 MB (Tokyo's small roads) and
its manifest 28 MB; a limit on the whole transfer, 35 s as it first was, failed them below 14 and
6.5 Mbit/s, and two such failures set the library aside for the whole build (2026-09-25)."""
INDEX_TTL_S = 6 * 3600.0
"""How long the manifest kept on disk stands before it is asked for again."""
RETRY_PAUSE_S = 120.0
"""After a manifest that answered badly -- a 502, a cut connection -- how long before it is asked
again. One hiccup used to close the library for the whole job, all forty tiles of it, so a build
that started during a restart of the server never read a single prepared tile (review F7)."""
MAX_LAYER_BYTES = 120_000_000
"""What one compressed layer may weigh. The heaviest of a continent is a few megabytes; the cap
is there so that nothing downloads a file of any size because a manifest said to."""
MAX_SNAPSHOT_BYTES = 800_000_000
"""What one layer may weigh unpacked. zstd unpacks fast enough that a small file can ask for all
the memory of the machine, so the reader is told where to stop: 1.5 kB of ours unpacks to 50 MB,
and the same trick scales as far as one cares to take it (measured 2026-09-23)."""


def unpack(body: bytes, limit: int = MAX_SNAPSHOT_BYTES) -> bytes:
    """A zstd frame unpacked, and never more than ``limit`` bytes of it.

    ``decompress(..., max_output_size=)`` does not do this: a frame that declares its own size is
    unpacked to that size whatever the limit says, which is exactly the case of a file made to be
    too big. So the declared size is read first, and a frame that declares none is read through a
    stream that stops on its own.
    """
    try:
        declared = zstandard.frame_content_size(body)
    except zstandard.ZstdError as exc:
        raise ValueError(f"not a zstd frame ({exc})") from exc
    if declared > limit:
        raise ValueError(f"unpacks to {declared} bytes, more than the {limit} allowed")
    if declared >= 0:
        return bytes(zstandard.ZstdDecompressor().decompress(body))
    with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(body)) as reader:
        out = reader.read(limit + 1)
    if len(out) > limit:
        raise ValueError(f"unpacks to more than the {limit} bytes allowed")
    return bytes(out)


FetchFn = Callable[[Sequence[str], Mapping[str, str]], list[tuple[int, bytes]]]
"""``(urls, headers) -> [(status, body), ...]``: injected, so the tests reach nothing.

A tile's layers are asked for together, in one call: one round trip instead of four, and the
whole tile is on disk while a live query would still be planning its first answer.
"""


def _http_get_many(urls: Sequence[str], headers: Mapping[str, str]) -> list[tuple[int, bytes]]:
    """Plain requests, one connection, nothing adaptive.

    Not the imagery fetcher: that one fills a window of several requests before it starts, and a
    single file left it waiting until its timeout -- 123 s for a file curl downloads in 0.6
    (2026-09-23). It is built for thousands of small chunks; a library asks for four files.
    """
    import asyncio

    from curl_cffi.requests import AsyncSession

    from orthostudio.net.certs import ca_bundle

    async def run() -> list[tuple[int, bytes]]:
        # HTTP/1.1, pinned. Left to negotiate, a session stalled on the larger files until its
        # timeout; pinned to HTTP/2 it stalled at exactly one mebibyte received out of four,
        # which is a flow-control window that never reopened. A library asks for four files at a
        # time, so multiplexing buys nothing and a protocol without windows is what fits
        # (2026-09-23, the same files in 0.35 s).
        async with AsyncSession(verify=ca_bundle(), http_version="v1") as session:

            async def one(url: str) -> tuple[int, bytes]:
                # Streamed, because that is where curl_cffi puts the read limit on silence (no
                # byte for LIBRARY_TIMEOUT_S) instead of on the whole transfer: a 61 MB file of
                # the planet library arrives however slow the line, as long as it arrives
                name = url.rsplit("/", 1)[-1]
                try:
                    async with session.stream(
                        "GET",
                        url,
                        headers=dict(headers),
                        timeout=(CONNECT_TIMEOUT_S, LIBRARY_TIMEOUT_S),
                    ) as answer:
                        pieces: list[bytes] = []
                        size = 0
                        async for piece in answer.aiter_content():
                            size += len(piece)
                            if size > MAX_LAYER_BYTES:  # longer than any file of ours: not read
                                log.info("library: %s is longer than any file of ours", name)
                                return 0, b""
                            pieces.append(piece)
                        return int(answer.status_code), b"".join(pieces)
                except Exception as exc:  # unreachable, refused, cut, silent: the chain moves on
                    log.info("library: %s could not be read (%s)", name, exc)
                    return 0, b""

            return list(await asyncio.gather(*(one(url) for url in urls)))

    return asyncio.run(run())


# -- the manifest -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LibraryIndex:
    """What the library holds: one entry per tile and layer, plus the date it was baked."""

    extracted: str
    road_level: int
    bake: str = ""
    """Twelve characters naming this bake, from what it holds. What a build says it read, so a
    scenery can be traced to the data it was made from (review P4)."""
    files: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    """``{"<tile>/<layer>": {"path": ..., "digest": ..., "bytes": ...}}``."""

    def entry(self, tile: TileRef, layer: str) -> Mapping[str, object] | None:
        return self.files.get(f"{tile.name}/{layer}")

    @property
    def tiles(self) -> set[str]:
        return {key.split("/", 1)[0] for key in self.files}


def parse_manifest(body: bytes) -> LibraryIndex | None:
    """The manifest as the baking tool writes it, or ``None`` when it is not one of ours."""
    try:
        doc = orjson.loads(body)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        return None
    files: dict[str, dict[str, object]] = {}
    for path, meta in (doc.get("files") or {}).items():
        if not isinstance(meta, dict):
            continue
        tile, layer = str(meta.get("tile", "")), str(meta.get("layer", ""))
        if tile and layer:
            files[f"{tile}/{layer}"] = {
                "path": str(path),
                "digest": str(meta.get("digest", "")),
                "bytes": int(meta.get("bytes", 0) or 0),
            }
    return LibraryIndex(
        extracted=str(doc.get("extracted", "")),
        road_level=int(doc.get("road_level", 1) or 1),
        bake=str(doc.get("bake", "") or ""),
        files=files,
    )


# -- the source -------------------------------------------------------------------------------


class LibrarySource:
    """The layers of a tile, read from a baked library over HTTP, with its token."""

    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        name: str = "library",
        fetch: FetchFn | None = None,
        cache_dir: Path | None = None,
        ttl_s: float = INDEX_TTL_S,
    ) -> None:
        self.base = base_url.rstrip("/")
        """Where the library is. It is never written to a log: ``serve.log`` is the file the page
        asks a user to send with a report, the address of this one deliberately stays out of
        print, and every line below names the source rather than the server (2026-09-23)."""
        self.token = token
        self.name = name
        self.fetch: FetchFn = fetch if fetch is not None else _http_get_many
        self.cache_dir = cache_dir
        self.ttl_s = ttl_s
        self.index: LibraryIndex | None = None
        self.stamp = ""
        """When the data was cut, as the manifest gives it: what the page shows beside the
        source, since a prepared library is weeks behind and nothing else would say so."""
        self._read_at = 0.0
        self._lock = threading.Lock()
        """The two network slots of a batch share one source: without this they both download the
        manifest, and both count the same failure twice (review F5)."""
        self._closed = False
        """The door answered 401 or 403: the key is wrong and asking again will not change it."""
        self._retry_at = 0.0
        """A failure that may pass: nothing is asked of the library before this moment."""
        self._refused: set[str] = set()
        """Layers this library was not baked for: said once, not once per tile."""

    # -- the manifest, read once and kept ----------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _load_index(self) -> LibraryIndex | None:
        with self._lock:  # one manifest for the whole batch, whichever slot asks first
            return self._load_index_locked()

    def _load_index_locked(self) -> LibraryIndex | None:
        if self.index is not None and time.monotonic() - self._read_at < self.ttl_s:
            return self.index
        if self._closed:
            return None
        if time.monotonic() < self._retry_at:
            return self._kept()  # asked too recently: the copy on disk, or nothing
        try:
            ((status, body),) = self.fetch([f"{self.base}/{MANIFEST_NAME}"], self._headers())
        except Exception as exc:  # a library must never stop a build
            log.info("OSM_LIBRARY_UNREACHABLE: the %s could not be asked (%s)", self.name, exc)
            self._retry_at = time.monotonic() + RETRY_PAUSE_S
            return self._kept()
        if status in (401, 403):
            # the door, not a fault: the token is wrong or absent, and it will be at the next
            # tile as well
            log.info("OSM_LIBRARY_KEY_REFUSED: the %s refused the key (HTTP %s)", self.name, status)
            self._closed = True
            return None
        if status != 200:
            # a restart, a 502, a connection cut: the same question in two minutes may well be
            # answered, and until then the copy on disk is better than nothing (review F7)
            log.info("OSM_LIBRARY_UNREACHABLE: the %s answered HTTP %s", self.name, status)
            self._retry_at = time.monotonic() + RETRY_PAUSE_S
            return self._kept()
        index = parse_manifest(body)
        if index is None:
            log.warning("%s: manifest is not a %s document", self.name, FORMAT)
            self._closed = True  # whatever is at that address, it is not a library of ours
            return None
        self._adopt(index)
        if self.cache_dir is not None:
            self._keep(body)
        return index

    def _adopt(self, index: LibraryIndex) -> None:
        self.index = index
        self.stamp = f"{index.extracted[:10]} #{index.bake}" if index.bake else index.extracted[:10]
        self._read_at = time.monotonic()

    def _kept(self) -> LibraryIndex | None:
        """The manifest kept on disk, when the library cannot be reached.

        It was written at every build and read back at none, so a library briefly unreachable
        sent every tile of the job to the live servers although its contents were on disk
        (review F6). A copy is only ever a list of what to ask for: every file it names is still
        checked against the digest it names, so an old copy costs a refusal, never a wrong tile.
        """
        if self.index is not None:
            return self.index
        if self.cache_dir is None:
            return None
        try:
            body = (self.cache_dir / f"{self.name}-manifest.json").read_bytes()
        except OSError:
            return None
        index = parse_manifest(body)
        if index is None:
            return None
        log.info("%s: unreachable, going by the manifest kept on disk (%s)", self.name,
                 index.extracted[:10])  # fmt: skip
        self._adopt(index)
        return index

    def _keep(self, body: bytes) -> None:
        assert self.cache_dir is not None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{self.name}-manifest.json").write_bytes(body)
        except OSError:  # a library that cannot be kept is still usable
            pass

    # -- the layers ---------------------------------------------------------------------------

    def _answers(self, specs: Sequence[LayerSpec], index: LibraryIndex) -> bool:
        """Whether the library was baked for at least the layers this build is asking for.

        The manifest says at which road level it was baked, and that settles it for every tile at
        once. It used to be found out by downloading the whole tile and reading the selectors
        inside it, once per tile, to refuse it every time (review F2).

        At least, not exactly: the planet is baked once, at road level 5, and a file of it holds
        every road a lower level asks for, the extra being cut away tile by tile
        (:func:`~orthostudio.sources.osm.narrowed`). Asking for exactly the baked level sent every
        build at the default level 1 to the public servers (2026-09-25).
        """
        baked = {spec.name: set(spec.selectors) for spec in layers_for(index.road_level)}
        wrong = [
            s.name for s in specs if s.name not in baked or not set(s.selectors) <= baked[s.name]
        ]
        if not wrong:
            return True
        if not self._refused.issuperset(wrong):
            self._refused.update(wrong)
            log.info(
                "%s: baked at road level %s, which does not answer for %s; the live servers do",
                self.name,
                index.road_level,
                ", ".join(sorted(wrong)),
            )
        return False

    def layers(self, tile: TileRef, specs: Sequence[LayerSpec]) -> dict[str, OsmSnapshot] | None:
        index = self._load_index()
        if index is None:
            return None
        if not self._answers(specs, index):
            return None
        wanted: list[tuple[LayerSpec, Mapping[str, object]]] = []
        for spec in specs:
            entry = index.entry(tile, spec.name)
            if entry is None:
                return None  # all or nothing: the library does not hold this tile
            size = int(entry.get("bytes", 0) or 0)
            if size < EMPTY_BYTES:
                # under this a file holds nothing at all, not even the wrapper of an empty
                # layer, which is a truncated upload rather than an empty square
                log.info("%s: %s of %s is a truncated file", self.name, spec.name, tile.name)
                return None
            if size > MAX_LAYER_BYTES:
                log.warning("%s: %s of %s is announced at %s bytes", self.name, spec.name,
                            tile.name, size)  # fmt: skip
                return None
            wanted.append((spec, entry))

        urls = [f"{self.base}/{entry['path']}" for _spec, entry in wanted]
        answers = self.fetch(urls, self._headers())
        if len(answers) != len(wanted):
            return None
        out: dict[str, OsmSnapshot] = {}
        for (spec, entry), answer in zip(wanted, answers, strict=True):
            snap = self._one(tile, spec, entry, answer)
            if snap is None:
                return None
            out[spec.name] = snap
        return out

    def _one(
        self,
        tile: TileRef,
        spec: LayerSpec,
        entry: Mapping[str, object],
        answer: tuple[int, bytes],
    ) -> OsmSnapshot | None:
        status, body = answer
        if status != 200 or not body:
            raise LibraryError(f"answered HTTP {status}", tile=tile.name, layer=spec.name)
        announced_bytes = int(entry.get("bytes", 0) or 0)
        if len(body) > MAX_LAYER_BYTES or (announced_bytes and len(body) != announced_bytes):
            # the manifest gives every file its size, so anything else is not that file, and a
            # reader that unpacks first and asks afterwards is a reader that can be handed
            # anything at all (review F4)
            raise LibraryError(
                f"it weighs {len(body)} bytes, not the {announced_bytes} announced",
                tile=tile.name,
                layer=spec.name,
            )
        try:
            snap = OsmSnapshot.from_json(unpack(body))
        except (ValueError, KeyError, zstandard.ZstdError, orjson.JSONDecodeError) as exc:
            raise LibraryError(
                f"it is unreadable ({exc})", tile=tile.name, layer=spec.name
            ) from exc
        announced = str(entry.get("digest", ""))
        if announced and snap.digest != announced:
            # not the file the manifest lists: a copy kept too long, or a library rebaked under
            # our feet. Either way what we hold about this library is wrong, so it is set aside
            # and its manifest read again at the next build, rather than every tile paying for it.
            raise LibraryError("it is not the file announced", tile=tile.name, layer=spec.name)
        if snap.layer != spec.name or snap.tile.name != tile.name:
            log.warning("%s: %s of %s holds another tile or layer", self.name, spec.name, tile.name)
            return None
        if snap.is_empty and not announced and spec.name != "coastline":
            # Emptiness is refused from whoever cannot prove it. A digest that matches does
            # prove it: this is the file the bake wrote, and a square of Atlantic off the Sahara
            # really has no road, no airport and no lake, only a coastline. Refusing those sent
            # every empty square of a continent to the public servers to be told the same thing
            # (2026-09-23). What emptiness must never mean is a bake cut short, and that is the
            # coverage polygon's business, not this line's.
            log.info("%s: %s of %s holds nothing and says so on nobody's word", self.name,
                     spec.name, tile.name)  # fmt: skip
            return None
        cut = narrowed(snap, spec)
        if cut is None:
            # the same layer name, a different question: small_roads baked at road level 2 holds
            # tertiary roads and no more, and a build asking for level 5 wants the tracks too.
            # Taking it would give a scenery quietly missing them (2026-09-23). The other way
            # round is fine: a file baked for more is cut down to exactly what was asked.
            log.info(
                "%s: %s of %s was baked for other selectors; the next source takes over",
                self.name,
                spec.name,
                tile.name,
            )
            return None
        return cut


@functools.cache
def shipped_library() -> tuple[str, str]:
    """The address and key this build carries, or two empty strings when it carries none."""
    try:
        raw = (resources.files("orthostudio") / SHIPPED_NAME).read_bytes()
        doc = orjson.loads(raw)
    except (OSError, ModuleNotFoundError, orjson.JSONDecodeError):
        return "", ""
    if not isinstance(doc, dict):
        return "", ""
    return str(doc.get("url", "") or ""), str(doc.get("token", "") or "")
