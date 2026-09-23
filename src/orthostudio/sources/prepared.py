"""OpenStreetMap layers already cut per tile: ``https://xpconnect.me/orthoforge-data``.

Overpass is the bottleneck of a build that is otherwise ours: 8 to 15 s a tile, two requests in
flight per address, and a refusal costs the tile. OrthoForge bakes the same five layers Ortho4XP
asks for, one file per tile and per layer, in the format Ortho4XP caches them (OSM 0.6 XML,
bzip2), and serves them free of charge and without a quota. Measured 2026-09-19: 7 132 tiles,
1.2 MB for a median tile, read in 0.25 s, against 8.4 s for the same tile through Overpass.

Nothing here is a dependency: a tile the index does not hold, a service that does not answer, a
file whose digest does not match, all go to Overpass as before. The index is the manifest they
publish (every file with its sha256), kept on disk and asked for again with its ETag, which costs
nothing when nothing changed.

What a build records stays the same: one :class:`~orthostudio.sources.osm.OsmSnapshot` a layer,
with the date the data was extracted, so a tile says where its map data came from.
"""

from __future__ import annotations

import bz2
import contextlib
import hashlib
import io
import json
import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import blake3

from orthostudio.dem.sources import round_latlon
from orthostudio.model import TileRef
from orthostudio.sources.osm import (
    LAYERS,
    LayerSpec,
    OsmMember,
    OsmNode,
    OsmRelation,
    OsmSnapshot,
    OsmWay,
    _canonical,
    osm_progress_message,
)

log = logging.getLogger("orthostudio.sources.prepared")

__all__ = [
    "MANIFEST_URL",
    "PREPARED_BASE",
    "PreparedIndex",
    "PublicSource",
    "http_get",
    "http_get_many",
    "layer_path",
    "load_index",
    "shared_index",
    "snapshot_from_xml",
    "snapshots_for",
    "tile_snapshots",
]

PREPARED_BASE = "https://xpconnect.me/orthoforge-data"
"""Where the prepared layers are served from (OrthoForge, free, no account, ODbL data)."""

MANIFEST_URL = f"{PREPARED_BASE}/manifest.json"
"""Every file with its sha256 and its size, about 5.5 MB; read again only when it changed."""

INDEX_TTL_S = 24 * 3600.0
"""How long the copy on disk is trusted before its ETag is offered again."""

EMPTY_LAYER_BYTES = 200
"""A layer file smaller than this holds no element at all: not an answer, whoever publishes it
(1 559 of their 7 132 tiles hold no road, measured 2026-09-19 and unchanged on 2026-09-23)."""
MAX_LAYER_BYTES = 120_000_000
"""What one layer may weigh, compressed: the heaviest of the set is Tokyo at 81 MB."""

MANIFEST_TIMEOUT_S = 30.0
"""A build waits no longer than this for the manifest (measured: 0.7 s cold, 0.25 s with its
ETag). A service that hangs must cost a few seconds at the start of a run, not a build."""

RETRY_AFTER_S = 600.0
"""After a manifest that could not be read at all, the next builds of the run go straight to
Overpass for ten minutes instead of waiting for it again."""


def layer_path(tile: TileRef, layer: str) -> str:
    """``+40+000/+46+006/+46+006_water.osm.bz2``: where one layer of one tile lives."""
    return f"{round_latlon(tile.lat, tile.lon)}/{tile.name}/{tile.name}_{layer}.osm.bz2"


@dataclass(frozen=True, slots=True)
class PreparedIndex:
    """The manifest: which files exist, what they weigh and what they hash to."""

    version: str = ""
    files: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    etag: str = ""

    def covers(self, tile: TileRef, layers: Iterable[LayerSpec | str]) -> bool:
        """Whether every layer of ``tile`` is in the manifest (they always come together)."""
        names = [spec if isinstance(spec, str) else spec.name for spec in layers]
        return bool(names) and all(layer_path(tile, name) in self.files for name in names)

    def digest_of(self, tile: TileRef, layer: str) -> str:
        """The sha256 the manifest gives for one layer, or an empty string."""
        entry = self.files.get(layer_path(tile, layer)) or {}
        return str(entry.get("sha256", ""))

    def size_of(self, tile: TileRef, layer: str) -> int:
        """What the manifest says the file weighs, or 0 when it does not list it."""
        entry = self.files.get(layer_path(tile, layer)) or {}
        return int(entry.get("size", 0) or 0)

    def stamp(self, tile: TileRef, layers: Iterable[LayerSpec | str]) -> str:
        """What the data of this tile is, in twelve characters, for the key of the OSM node.

        The digests of its layers, not the manifest's version: a new bake that leaves a tile alone
        must not rebuild it, and one that changes it must.
        """
        names = sorted(spec if isinstance(spec, str) else spec.name for spec in layers)
        marks = "".join(f"{name}:{self.digest_of(tile, name)};" for name in names)
        return blake3.blake3(marks.encode()).hexdigest()[:12] if marks else ""


def load_index(
    cache_dir: Path,
    fetch: Callable[[str, Mapping[str, str]], tuple[int, bytes, Mapping[str, str]]],
    *,
    ttl_s: float = INDEX_TTL_S,
    now: Callable[[], float] = time.time,
) -> PreparedIndex | None:
    """The manifest, from disk when it is fresh, else asked for again with its ETag.

    ``fetch(url, headers)`` answers ``(status, body, headers)`` and never raises; a service that
    does not answer leaves the copy on disk in charge, and no copy at all answers ``None``, which
    sends the build to Overpass.
    """
    path = cache_dir / "manifest.json"
    kept = _read_kept(path)
    if kept is not None and now() - _mtime(path) < ttl_s:
        return kept
    headers = {"If-None-Match": kept.etag} if kept is not None and kept.etag else {}
    try:
        status, body, answer = fetch(MANIFEST_URL, headers)
    except Exception as exc:  # never a reason to fail a build
        log.debug("prepared: the manifest could not be asked for (%s)", exc)
        return kept
    if status == 304 and kept is not None:
        _touch(path)
        return kept
    if status != 200 or not body:
        log.debug("prepared: the manifest answered %s", status)
        return kept
    index = _parse_manifest(body, str(answer.get("etag", "")))
    if index is None:
        return kept
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        (cache_dir / "manifest.etag").write_text(index.etag, encoding="utf-8")
    except OSError as exc:
        log.debug("prepared: the manifest was not kept (%s)", exc)
    return index


def _parse_manifest(body: bytes, etag: str) -> PreparedIndex | None:
    try:
        doc = json.loads(body)
        files = doc["files"]
    except (ValueError, KeyError, TypeError):
        log.debug("prepared: the manifest could not be read")
        return None
    if not isinstance(files, dict):
        return None
    return PreparedIndex(version=str(doc.get("version", "")), files=files, etag=etag)


def _read_kept(path: Path) -> PreparedIndex | None:
    if not path.is_file():
        return None
    try:
        etag = (path.parent / "manifest.etag").read_text(encoding="utf-8").strip()
    except OSError:
        etag = ""
    try:
        return _parse_manifest(path.read_bytes(), etag)
    except OSError:
        return None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _touch(path: Path) -> None:
    """The copy on disk is current: its date says so, and the next build asks nothing."""
    with contextlib.suppress(OSError):
        path.touch()


def snapshot_from_xml(
    body: bytes,
    tile: TileRef,
    layer: LayerSpec | str,
    *,
    mirror: str,
    fetched_at: str = "",
    osm_base_default: str = "",
) -> OsmSnapshot:
    """One layer read from an OSM 0.6 XML document (bzip2 or plain), as a snapshot.

    The document is dropped as it is read, element by element: a dense layer is 45 MB of XML and
    there is no reason to hold it twice (measured: 0.55 s for the big roads of Marseille). Only
    the root is cleared, never an element still being read: clearing one wipes its attributes,
    and a ``<tag>`` or a ``<nd>`` emptied before its parent ends would take the map with it.

    ``osm_base_default`` stands in for the date the data was extracted when the document does not
    carry it: the prepared files are cut with osmium, which writes no ``<meta>``, and the manifest
    dates the whole bake.
    """
    spec = LAYERS[layer] if isinstance(layer, str) else layer
    raw = bz2.decompress(body) if body[:3] == b"BZh" else body
    nodes: list[OsmNode] = []
    ways: list[OsmWay] = []
    relations: list[OsmRelation] = []
    osm_base = ""
    reading = ET.iterparse(io.BytesIO(raw), events=("start", "end"))
    _start, root = next(reading)  # the <osm> tag, read before anything is dropped
    generator = str(root.get("generator", ""))
    for event, el in reading:
        if event != "end":
            continue
        tag = el.tag
        if tag == "node":
            nodes.append(
                OsmNode(
                    id=int(el.get("id", "0")),
                    lat=float(el.get("lat", "0")),
                    lon=float(el.get("lon", "0")),
                    tags=_tags(el),
                )
            )
        elif tag == "way":
            ways.append(
                OsmWay(
                    id=int(el.get("id", "0")),
                    nodes=tuple(int(nd.get("ref", "0")) for nd in el.findall("nd")),
                    tags=_tags(el),
                )
            )
        elif tag == "relation":
            relations.append(
                OsmRelation(
                    id=int(el.get("id", "0")),
                    members=tuple(
                        OsmMember(
                            type=str(m.get("type", "")),
                            ref=int(m.get("ref", "0")),
                            role=str(m.get("role", "")),
                        )
                        for m in el.findall("member")
                    ),
                    tags=_tags(el),
                )
            )
        elif tag == "meta":
            osm_base = str(el.get("osm_base", ""))
        elif tag != "osm":
            continue  # a <tag>, a <nd>, a <member>: its parent has not been read yet
        root.clear()  # every element read so far, with its children, let go of
    return OsmSnapshot(
        tile=tile,
        layer=spec.name,
        selectors=tuple(spec.selectors),
        query="",  # prepared files are not the answer to a query of ours
        mirror=mirror,
        fetched_at=fetched_at or _utc_now(),
        generator=generator,
        osm_base=osm_base or osm_base_default,
        nodes=tuple(nodes),
        ways=tuple(ways),
        relations=tuple(relations),
        digest=blake3.blake3(_canonical(nodes, ways, relations)).hexdigest(),
    )


def _tags(element: ET.Element) -> dict[str, str]:
    return {
        str(t.get("k", "")): str(t.get("v", ""))
        for t in element.findall("tag")
        if t.get("k") is not None
    }


def _utc_now() -> str:
    import datetime as dt

    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def tile_snapshots(
    tile: TileRef,
    specs: Sequence[LayerSpec],
    index: PreparedIndex,
    fetch: Callable[[Sequence[str]], Sequence[tuple[int, bytes]]],
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, OsmSnapshot] | None:
    """Every layer of ``tile`` read from the prepared files, or ``None`` to fall back.

    The five layers travel together, in one call: one connection, one round trip, and the tile
    is on disk while Overpass would still be planning its first answer.

    ``None`` on anything at all: a layer missing from the manifest, a service that does not
    answer, a file too large to be one of theirs, a digest that does not match what the manifest
    promised, XML that cannot be read. Overpass then does what it has always done.
    """
    if not index.covers(tile, specs):
        return None
    paths = [layer_path(tile, spec.name) for spec in specs]
    started = time.monotonic()
    try:
        answers = list(fetch([f"{PREPARED_BASE}/{path}" for path in paths]))
    except Exception as exc:  # never a reason to fail a build
        log.info("prepared: %s could not be read (%s); Overpass takes over", tile.name, exc)
        return None
    if len(answers) != len(specs):
        return None
    for path, (status, body) in zip(paths, answers, strict=True):
        if status != 200 or not body or len(body) > MAX_LAYER_BYTES:
            log.info("prepared: %s answered %s; Overpass takes over", path, status)
            return None
    wire = sum(len(body) for _status, body in answers)
    elapsed = time.monotonic() - started
    out: dict[str, OsmSnapshot] = {}
    for done, (spec, path, (_status, body)) in enumerate(
        zip(specs, paths, answers, strict=True), start=1
    ):
        expected = index.digest_of(tile, spec.name)
        if expected and hashlib.sha256(body).hexdigest() != expected:
            log.warning("prepared: %s is not what the manifest promised; Overpass takes over", path)
            return None
        try:
            out[spec.name] = snapshot_from_xml(
                body, tile, spec, mirror=PREPARED_BASE, osm_base_default=index.version
            )
        except Exception as exc:
            log.info("prepared: %s could not be read (%s); Overpass takes over", path, exc)
            return None
        if progress is not None:
            # The very line Overpass writes, rate included (``ui.md`` 2.2: every step that
            # reaches the network shows its MB/s), over the time this tile's layers took.
            progress(
                done / len(specs),
                osm_progress_message(tile, done, len(specs), wire, elapsed),
            )
    return out


_SHARED: dict[str, Any] = {"index": None, "at": 0.0}
_SHARED_LOCK = threading.Lock()


def http_get(
    url: str, headers: Mapping[str, str] | None = None, *, timeout_s: float = 120.0
) -> tuple[int, bytes, Any]:
    """One request through the fetcher the rest of the engine uses; never raises."""
    from orthostudio.net.fetch import FetchRequest, fetch_all

    results = fetch_all(
        [FetchRequest(key=url, url=url, headers=dict(headers or {}), host_group="prepared")],
        timeout_s=timeout_s,
        max_attempts=2,
        max_in_flight=4,
        start_in_flight=2,
    )
    if not results:
        return 0, b"", {}
    answer = results[0]
    return int(answer.status), answer.body or b"", answer.headers


def http_get_many(urls: Sequence[str]) -> list[tuple[int, bytes]]:
    """The layers of one tile, together, in request order: one session, one handshake."""
    from orthostudio.net.fetch import FetchRequest, fetch_all

    results = fetch_all(
        [FetchRequest(key=url, url=url, host_group="prepared") for url in urls],
        timeout_s=120.0,
        max_attempts=2,
        max_in_flight=max(1, len(urls)),
        start_in_flight=max(1, len(urls)),
    )
    return [(int(answer.status), answer.body or b"") for answer in results]


def shared_index(cache_dir: Path, *, ttl_s: float = INDEX_TTL_S) -> PreparedIndex | None:
    """The manifest of this process, read once and kept: every build of a run shares it.

    A run that could not read it at all does not try again before :data:`RETRY_AFTER_S`, so a
    service that is down costs one wait, not one per build.
    """
    with _SHARED_LOCK:
        index, at = _SHARED["index"], float(_SHARED["at"])
        if at and time.time() - at < (ttl_s if index is not None else RETRY_AFTER_S):
            return index  # type: ignore[no-any-return]
        index = load_index(
            cache_dir,
            lambda url, headers: http_get(url, headers, timeout_s=MANIFEST_TIMEOUT_S),
            ttl_s=ttl_s,
        )
        _SHARED["index"], _SHARED["at"] = index, time.time()
        return index


def snapshots_for(
    index: PreparedIndex, progress: Callable[[float, str], None] | None = None
) -> Callable[[TileRef, Sequence[LayerSpec]], Any]:
    """The callable an ``OsmJob`` takes: the prepared layers of a tile, or ``None``."""

    def prepared(tile: TileRef, specs: Sequence[LayerSpec]) -> dict[str, OsmSnapshot] | None:
        return tile_snapshots(tile, specs, index, http_get_many, progress)

    return prepared


class PublicSource:
    """The library another project publishes, used only where we have verified it ourselves.

    They bake the same five layers per tile and serve them free of charge, which is worth having
    for the parts of the world we will not bake. But their coverage cannot be told from their
    manifest: measured 2026-09-19 and again unchanged on 2026-09-23, 1 559 of the 7 132 tiles they
    list hold no road at all, and a tile that straddles a border is cut at that border -- Geneva
    holds 10 110 road ways where a live query returns 24 307 -- with a valid file and a matching
    checksum to show for it.

    Hence the whitelist, which travels with our own manifest: the tiles we have compared, against
    a live count or against our own bake. **An empty whitelist makes this source inert**, so a
    build that cannot reach our library never reads theirs either: no verification, no use
    (``osm-prepared.md`` 3).
    """

    def __init__(
        self,
        allowed: Iterable[str] | Callable[[], Iterable[str]] = (),
        *,
        name: str = "xpconnect",
        index: PreparedIndex | None = None,
        cache_dir: Path | None = None,
        fetch: Callable[[Sequence[str]], Sequence[tuple[int, bytes]]] | None = None,
    ) -> None:
        self._allowed = allowed
        self.name = name
        self.index = index
        self.cache_dir = cache_dir
        self.fetch = fetch if fetch is not None else http_get_many
        self._looked = False

    def allowed(self) -> frozenset[str]:
        """The whitelist, read when it is needed: our own manifest carries it, and our library is
        asked before this one."""
        source = self._allowed
        return frozenset(source() if callable(source) else source)

    def _read_index(self) -> PreparedIndex | None:
        if self.index is not None or self._looked:
            return self.index
        self._looked = True
        if self.cache_dir is not None:
            self.index = shared_index(self.cache_dir)
        return self.index

    def layers(self, tile: TileRef, specs: Sequence[LayerSpec]) -> dict[str, OsmSnapshot] | None:
        if tile.name not in self.allowed():
            return None  # not verified: not used, whatever their manifest claims
        index = self._read_index()
        if index is None or not index.covers(tile, specs):
            return None
        for spec in specs:
            if index.size_of(tile, spec.name) < EMPTY_LAYER_BYTES and spec.name != "coastline":
                log.info(
                    "prepared: %s of %s is empty; the next source takes over",
                    spec.name,
                    tile.name,
                )
                return None
        return tile_snapshots(tile, specs, index, self.fetch)
