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

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import orjson
import zstandard

from orthostudio.model import TileRef
from orthostudio.sources.chain import EMPTY_BYTES
from orthostudio.sources.osm import LayerSpec, OsmSnapshot

__all__ = ["LIBRARY_TIMEOUT_S", "LibraryIndex", "LibrarySource", "parse_manifest"]

log = logging.getLogger("orthostudio.sources.library")

MANIFEST_NAME = "manifest.json"
FORMAT = "osxp-baked-1"
LIBRARY_TIMEOUT_S = 30.0
"""What one request may take. A library exists to save seconds; one that hangs would cost them,
so a slow answer is a failure and the chain moves on (``osm-prepared.md`` 4)."""
INDEX_TTL_S = 6 * 3600.0
"""How long the manifest kept on disk stands before it is asked for again."""


FetchFn = Callable[[str, Mapping[str, str]], tuple[int, bytes]]
"""``(url, headers) -> (status, body)``: injected, so the tests reach nothing."""


def _http_get(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
    from orthostudio.sources.prepared import http_get

    status, body, _ = http_get(url, headers, timeout_s=LIBRARY_TIMEOUT_S)
    return status, body


# -- the manifest -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LibraryIndex:
    """What the library holds: one entry per tile and layer, plus the date it was baked."""

    extracted: str
    road_level: int
    files: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    """``{"<tile>/<layer>": {"path": ..., "digest": ..., "bytes": ...}}``."""
    verified_elsewhere: tuple[str, ...] = ()
    """Tiles of another publisher's library we have compared and found complete: the whitelist
    that decides whether that library may be used at all (``osm-prepared.md`` 3)."""

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
        files=files,
        verified_elsewhere=tuple(str(t) for t in (doc.get("verified_elsewhere") or ())),
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
        self.token = token
        self.name = name
        self.fetch: FetchFn = fetch if fetch is not None else _http_get
        self.cache_dir = cache_dir
        self.ttl_s = ttl_s
        self.index: LibraryIndex | None = None
        self._read_at = 0.0
        self._missing = False
        """The manifest could not be read: the library is skipped without being asked again."""

    # -- the manifest, read once and kept ----------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _load_index(self) -> LibraryIndex | None:
        if self.index is not None and time.monotonic() - self._read_at < self.ttl_s:
            return self.index
        if self._missing:
            return None
        status, body = self.fetch(f"{self.base}/{MANIFEST_NAME}", self._headers())
        if status != 200:
            # 401 or 403 is the door, not a fault: the token is wrong or absent
            log.info("%s: manifest answered HTTP %s", self.name, status)
            self._missing = True
            return None
        index = parse_manifest(body)
        if index is None:
            log.warning("%s: manifest is not a %s document", self.name, FORMAT)
            self._missing = True
            return None
        self.index = index
        self._read_at = time.monotonic()
        if self.cache_dir is not None:
            self._keep(body)
        return index

    def _keep(self, body: bytes) -> None:
        assert self.cache_dir is not None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{self.name}-manifest.json").write_bytes(body)
        except OSError:  # a library that cannot be kept is still usable
            pass

    # -- the layers ---------------------------------------------------------------------------

    def layers(self, tile: TileRef, specs: Sequence[LayerSpec]) -> dict[str, OsmSnapshot] | None:
        index = self._load_index()
        if index is None:
            return None
        wanted: list[tuple[LayerSpec, Mapping[str, object]]] = []
        for spec in specs:
            entry = index.entry(tile, spec.name)
            if entry is None:
                return None  # all or nothing: the library does not hold this tile
            size = int(entry.get("bytes", 0) or 0)
            if size < EMPTY_BYTES and spec.name != "coastline":
                log.info("%s: %s of %s is empty", self.name, spec.name, tile.name)
                return None
            wanted.append((spec, entry))

        out: dict[str, OsmSnapshot] = {}
        for spec, entry in wanted:
            snap = self._one(tile, spec, entry)
            if snap is None:
                return None
            out[spec.name] = snap
        return out

    def _one(
        self, tile: TileRef, spec: LayerSpec, entry: Mapping[str, object]
    ) -> OsmSnapshot | None:
        url = f"{self.base}/{entry['path']}"
        status, body = self.fetch(url, self._headers())
        if status != 200 or not body:
            log.info("%s: %s of %s answered HTTP %s", self.name, spec.name, tile.name, status)
            return None
        try:
            snap = OsmSnapshot.from_json(zstandard.ZstdDecompressor().decompress(body))
        except (ValueError, KeyError, zstandard.ZstdError, orjson.JSONDecodeError) as exc:
            log.warning("%s: %s of %s unreadable (%s)", self.name, spec.name, tile.name, exc)
            return None
        announced = str(entry.get("digest", ""))
        if announced and snap.digest != announced:
            # the file is not the one the manifest lists: a stale copy, or a library rebaked
            # under our feet. Either way it is not what was verified.
            log.warning("%s: %s of %s is not the file announced", self.name, spec.name, tile.name)
            return None
        if snap.layer != spec.name or snap.tile.name != tile.name:
            log.warning("%s: %s of %s holds another tile or layer", self.name, spec.name, tile.name)
            return None
        return snap
