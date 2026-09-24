"""OpenStreetMap vector data: Overpass mirrors and OrthoStudio XP snapshots.

Specification: ``docs/specs/osm-source.md``. Policy already fixed by ADR 0005 decision 4
(mirror order) and ``docs/specs/net-download.md`` section 5.5 (two requests in flight per
cluster, health check, three attempts across mirrors, never the 2^n back-off of Ortho4XP).

Two independent pieces live here:

- the **client**: a mirror registry declared by machine, a circuit breaker, fail-over, and
  ``[out:json]`` queries built from the selectors of ``O4_Vector_Map.py``;
- the **snapshot**: OrthoStudio XP's own content-keyed record of one layer (zstd + orjson), the
  input of the vector stage and the source of the snapshot label.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import blake3
import orjson
import zstandard

from orthostudio.errors import OsxpError
from orthostudio.home import data_root
from orthostudio.model import TileRef
from orthostudio.net.certs import ca_bundle

__all__ = [
    "ATTEMPT_DELAY_S",
    "CONNECT_TIMEOUT_S",
    "COOLDOWN_S",
    "DEFAULT_QUERY_TIMEOUT_S",
    "HEALTH_TIMEOUT_S",
    "LAYERS",
    "MAX_ATTEMPTS",
    "MAX_IN_FLIGHT",
    "MIRRORS",
    "PROGRESS_PERIOD_S",
    "ROUNDS",
    "SNAPSHOT_FORMAT",
    "USER_AGENT",
    "Attempt",
    "CurlTransport",
    "HttpReply",
    "LayerSpec",
    "Mirror",
    "MirrorBoard",
    "MirrorHealth",
    "OsmMember",
    "OsmNode",
    "OsmRelation",
    "OsmSnapshot",
    "OsmWay",
    "OverpassClient",
    "SnapshotStore",
    "Transport",
    "layers_for",
    "osm_progress_message",
    "overpass_query",
    "parse_overpass_json",
    "shared_board",
    "snapshot_from_overpass",
    "snapshot_label",
    "split_elements",
]

SNAPSHOT_FORMAT = "osxp-osm-snapshot-1"
USER_AGENT = "orthostudio/0.0.1 (+OSM vector data for X-Plane scenery)"

DEFAULT_QUERY_TIMEOUT_S = 120
"""``[out:json][timeout:n]``: what the server is told it may spend (spec section 3)."""

CONNECT_TIMEOUT_S = 5.0
HEALTH_TIMEOUT_S = 5.0
COOLDOWN_S = 600.0
QUOTA_COOLDOWN_S = 60.0
BUSY_STATUSES = frozenset({502, 503, 504})
"""Answers that mean the machine is busy or its upstream is, not that it is broken."""
BUSY_COOLDOWN_S = 20.0
"""Set aside for a machine that answered 504, 503 or a remark: busy, not broken.

It used to take the cooldown built for a machine that is down, 600 seconds and doubling,
and nothing showed because the rounds gave every breaker back before asking again. With
the breakers as the one clock, that mis-tuning became a layer that gave up after one round
where it used to ask fifteen times (measured against v0.1.14, 2026-09-25). Twenty seconds
is what the old round pause was, now carried by the breaker that knows why it is set."""
"""How long a server that refused our address is left alone when it names no delay itself.

Most of them name none. Long enough that a build stops asking, short enough that the next tile
tries again; the doubling cooldown meant for a machine that is down would shut the whole cluster
for ten minutes, then twenty (found in review, 2026-09-23)."""

MAX_COOLDOWN_S = 3600.0
MAX_ATTEMPTS = 5
"""Attempts across mirrors for one layer: one per entry of the registry, so that the last resorts
are still reached when the three ordinary ones are down (2026-09-22: two of them were)."""
ATTEMPT_DELAY_S = 5.0
ROUNDS = 5
"""Times the whole registry is asked for one layer, when what refused it may pass.

An Overpass machine that answers 504, 429 or nothing is busy, not broken: it answers the same
query a minute later. One round over the mirrors and then a failed build wastes everything the
tile had already downloaded, and on a bad evening every build failed that way (2026-09-22).

Three rounds twenty seconds apart is one minute of patience, and a user whose address had spent
its quota watched all five mirrors refuse and the build give up while the quota needed minutes
(2026-09-24). These servers count queries per address and free a slot on their own clock; the
only thing to do is wait for it. What bounds the waiting is not this count but the tile's own
deadline (``pipeline.native.OsmJob.timeout_s``), and the rounds stop early of their own accord
when nothing that refused could pass, or when every server is still set aside.
"""
MAX_IN_FLIGHT = 2
MIN_INTERVAL_S = 1.0
PROGRESS_PERIOD_S = 1.0
"""Seconds between two progress reports of a tile while its layers are in flight."""


# -- mirrors -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Mirror:
    """One Overpass *machine* (never a round-robin name: ``net-download.md`` section 5.5)."""

    code: str
    interpreter: str
    cluster: str
    status_url: str | None = None
    last_resort: bool = False
    note: str = ""


MIRRORS: tuple[Mirror, ...] = (
    Mirror(
        code="de",
        interpreter="https://overpass-api.de/api/interpreter",
        cluster="de",
        status_url="https://overpass-api.de/api/status",
        note="the public front door of the German cluster; answered when both its machine "
        "names refused (2026-09-22)",
    ),
    Mirror(
        code="z",
        interpreter="https://z.overpass-api.de/api/interpreter",
        cluster="de",
        status_url="https://z.overpass-api.de/api/status",
        note="second machine of the DE cluster; shares its per-IP quota of 2 with lz4",
    ),
    Mirror(
        code="lz4",
        interpreter="https://lz4.overpass-api.de/api/interpreter",
        cluster="de",
        status_url="https://lz4.overpass-api.de/api/status",
        note="65.109.112.52; silent on 2026-09-22, kept for the day it answers again",
    ),
    Mirror(
        code="fr",
        interpreter="https://overpass.openstreetmap.fr/api/interpreter",
        cluster="fr",
        status_url="https://overpass.openstreetmap.fr/api/status",
        last_resort=True,
        note="refuses every query since 2026-09-22 (HTTP 403, white-listed usages only); kept "
        "as a last resort so that it serves again by itself the day it reopens",
    ),
    Mirror(
        code="mailru",
        interpreter="https://maps.mail.ru/osm/tools/overpass/api/interpreter",
        cluster="mailru",
        status_url="https://maps.mail.ru/osm/tools/overpass/api/status",
        last_resort=True,
        note="third-party clone, last resort only (ADR 0005 decision 4)",
    ),
)
"""Registry of ADR 0005 decision 4, amended 2026-09-22, in order.

That day every build failed with ``OSM_LAYER_UNAVAILABLE``, here and for two users:
``lz4.overpass-api.de`` answered ``HTTP 504`` or nothing at all and ``overpass.openstreetmap.fr``
now refuses every query with ``HTTP 403 This service is only available to white-listed usages``,
which left the last resort alone, itself failing. ``.fr`` stays, but as a last resort: it is not
asked while another name answers, its refusal costs 0.1 s when one is needed, and the day it
serves the public again it does so without waiting for a release.

``overpass-api.de`` leads the list although decision 4 wrote it off as a round-robin name. It is
the public entry point of the German cluster, and that evening it was the only name of that
cluster to answer at all: a query it served came back from 65.109.112.52, ``lz4``'s own machine,
which was returning 504 under its own name. What decision 4 feared, a name hiding which machine
was asked, costs nothing here: the quota, the minimum interval and the breaker are held per
*cluster*, and all three German names are one cluster and one per-IP quota.

**A mirror enters this list only once it has been seen to hold the whole planet.** The Swiss
instance ``overpass.osm.ch``, tried first that day, answers ``200`` with an empty ``elements``
list outside Switzerland: it would have built tiles without airports, water or coastline and
reported nothing wrong. An empty answer is a legitimate one (a tile may have no coastline), so
no code can tell the difference; only the check before adding can. A caller may pass its own
tuple.
"""


# -- layers and queries --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LayerSpec:
    """One vector layer of a tile and its Overpass selectors."""

    name: str
    selectors: tuple[str, ...]
    tags_of_interest: tuple[str, ...]
    origin: str
    min_road_level: int = 0


_SMALL_ROAD_SELECTORS: tuple[tuple[int, str], ...] = (
    (2, 'way["highway"="tertiary"]'),
    (3, 'way["highway"="unclassified"]'),
    (3, 'way["highway"="residential"]'),
    (4, 'way["highway"="service"]'),
    (5, 'way["highway"="track"]'),
)
"""``O4_Vector_Map.py:290-299``: the selectors added at each ``road_level``."""

_CANCEL_POLL_S = 0.1
"""How often :meth:`OverpassClient.fetch_tile` polls its cancellation token."""

LAYERS: Mapping[str, LayerSpec] = {
    spec.name: spec
    for spec in (
        LayerSpec(
            name="airports",
            selectors=('node["aeroway"]', 'way["aeroway"]', 'rel["aeroway"]'),
            tags_of_interest=("all",),
            origin="O4_Vector_Map.py:185-193",
        ),
        LayerSpec(
            name="big_roads",
            selectors=(
                'way["highway"="motorway"]',
                'way["highway"="trunk"]',
                'way["highway"="primary"]',
                'way["highway"="secondary"]',
                'way["railway"="rail"]',
                'way["railway"="narrow_gauge"]',
            ),
            tags_of_interest=("bridge", "tunnel"),
            origin="O4_Vector_Map.py:261-275",
            min_road_level=1,
        ),
        LayerSpec(
            name="small_roads",
            selectors=tuple(sel for _, sel in _SMALL_ROAD_SELECTORS),
            tags_of_interest=("bridge", "tunnel"),
            origin="O4_Vector_Map.py:290-306",
            min_road_level=2,
        ),
        LayerSpec(
            name="coastline",
            selectors=('way["natural"="coastline"]',),
            tags_of_interest=(),
            origin="O4_Vector_Map.py:391-399",
        ),
        LayerSpec(
            name="water",
            selectors=(
                'rel["natural"="water"]',
                'rel["waterway"="riverbank"]',
                'way["natural"="water"]',
                'way["waterway"="riverbank"]',
                'way["waterway"="dock"]',
            ),
            tags_of_interest=("name",),
            origin="O4_Vector_Map.py:525-539",
        ),
    )
}
"""The five Ortho4XP layers, selectors copied verbatim (spec section 3)."""


def layers_for(road_level: int = 1) -> tuple[LayerSpec, ...]:
    """The layers a tile needs at ``road_level`` (four at the Ortho4XP default of 1).

    ``small_roads`` appears at ``road_level >= 2`` and grows with it
    (``O4_Vector_Map.py:288-299``); ``big_roads`` disappears at ``road_level = 0``
    (``:253-254``: stage 1 skips the roads entirely).
    """
    out: list[LayerSpec] = []
    for spec in LAYERS.values():
        if road_level < spec.min_road_level:
            continue
        if spec.name == "small_roads":
            selectors = tuple(sel for level, sel in _SMALL_ROAD_SELECTORS if road_level >= level)
            out.append(
                LayerSpec(
                    name=spec.name,
                    selectors=selectors,
                    tags_of_interest=spec.tags_of_interest,
                    origin=spec.origin,
                    min_road_level=spec.min_road_level,
                )
            )
        else:
            out.append(spec)
    return tuple(out)


def _bbox(tile: TileRef, *, spaces: bool = False) -> str:
    sep = ", " if spaces else ","
    return "(" + sep.join(str(v) for v in (tile.lat, tile.lon, tile.lat + 1, tile.lon + 1)) + ")"


def overpass_query(
    selectors: Sequence[str],
    tile: TileRef,
    timeout_s: int = DEFAULT_QUERY_TIMEOUT_S,
    at: str = "",
) -> str:
    """The OrthoStudio XP query for one layer: JSON, children recursed, ``qt`` order (spec
    section 3).

    ``at`` asks the servers for the map as it stood at that moment (``[date:"..."]``, what
    Overpass calls attic data). Nothing in a build uses it: it is how a baked tile is proved,
    by asking for the very state its extract was cut from, so that any difference at all is a
    fault of ours and not two days of the world being edited (2026-09-23).
    """
    box = _bbox(tile)
    union = "".join(f"{sel}{box};" for sel in selectors)
    when = f'[date:"{at}"]' if at else ""
    return f"[out:json][timeout:{timeout_s}]{when};({union});(._;>>;);out body qt;"


# -- elements and snapshot -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OsmNode:
    """An OSM node: real id, WGS84 degrees, tags as delivered (never escaped)."""

    id: int
    lat: float
    lon: float
    tags: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OsmWay:
    """An OSM way: ordered node ids."""

    id: int
    nodes: tuple[int, ...]
    tags: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OsmMember:
    """One member of a relation."""

    type: str
    ref: int
    role: str


@dataclass(frozen=True, slots=True)
class OsmRelation:
    """An OSM relation: members in order, tags."""

    id: int
    members: tuple[OsmMember, ...]
    tags: Mapping[str, str] = field(default_factory=dict)


def _coord(value: float) -> str:
    """``"{:.7f}"``: the coordinate format of ``O4_OSM_Utils.py:302-303``."""
    return f"{value:.7f}"


def parse_overpass_json(body: bytes) -> tuple[list[OsmNode], list[OsmWay], list[OsmRelation]]:
    """Split an Overpass JSON document into nodes, ways and relations, order preserved.

    Raises :class:`ValueError` when the document is not an Overpass answer; the caller turns
    that into ``OSM_RESPONSE_TRUNCATED``.
    """
    doc = orjson.loads(body)
    if not isinstance(doc, dict) or not isinstance(doc.get("elements"), list):
        raise ValueError("not an Overpass JSON document (no 'elements' list)")
    return split_elements(doc["elements"])


def split_elements(
    elements: Sequence[Mapping[str, Any]],
) -> tuple[list[OsmNode], list[OsmWay], list[OsmRelation]]:
    """Typed nodes, ways and relations of a list of Overpass JSON elements, order preserved."""
    nodes: list[OsmNode] = []
    ways: list[OsmWay] = []
    relations: list[OsmRelation] = []
    for el in elements:
        kind = el.get("type")
        tags = {str(k): str(v) for k, v in (el.get("tags") or {}).items()}
        if kind == "node":
            nodes.append(OsmNode(int(el["id"]), float(el["lat"]), float(el["lon"]), tags))
        elif kind == "way":
            ways.append(OsmWay(int(el["id"]), tuple(int(n) for n in el.get("nodes") or ()), tags))
        elif kind == "relation":
            members = tuple(
                OsmMember(str(m.get("type", "")), int(m["ref"]), str(m.get("role", "")))
                for m in el.get("members") or ()
            )
            relations.append(OsmRelation(int(el["id"]), members, tags))
    return nodes, ways, relations


def _canonical(
    nodes: Sequence[OsmNode], ways: Sequence[OsmWay], relations: Sequence[OsmRelation]
) -> bytes:
    """Mirror-, date- and order-independent bytes of the data (spec section 5, content key)."""
    doc: list[dict[str, Any]] = []
    for n in sorted(nodes, key=lambda x: x.id):
        doc.append(
            {
                "t": "n",
                "i": n.id,
                "y": _coord(n.lat),
                "x": _coord(n.lon),
                "g": dict(sorted(n.tags.items())),
            }
        )
    for w in sorted(ways, key=lambda x: x.id):
        doc.append({"t": "w", "i": w.id, "n": list(w.nodes), "g": dict(sorted(w.tags.items()))})
    for r in sorted(relations, key=lambda x: x.id):
        doc.append(
            {
                "t": "r",
                "i": r.id,
                "m": [[m.type, m.ref, m.role] for m in r.members],
                "g": dict(sorted(r.tags.items())),
            }
        )
    return orjson.dumps(doc, option=orjson.OPT_SORT_KEYS)


@dataclass(frozen=True, slots=True)
class OsmSnapshot:
    """One layer of one tile as OrthoStudio XP keeps it (format ``osxp-osm-snapshot-1``)."""

    tile: TileRef
    layer: str
    selectors: tuple[str, ...]
    query: str
    mirror: str
    fetched_at: str
    generator: str
    osm_base: str
    nodes: tuple[OsmNode, ...]
    ways: tuple[OsmWay, ...]
    relations: tuple[OsmRelation, ...]
    digest: str

    @property
    def is_empty(self) -> bool:
        """Whether this layer holds nothing at all.

        Not the same as a small file: an empty snapshot of ours still carries its metadata, 233
        to 281 bytes of it, so the size threshold that catches an empty bzip2 XML document misses
        this entirely (2026-09-23). Emptiness is the truth for a coastline inland; it never is for
        roads or water, and a build that took such a layer would lay scenery without them and say
        nothing.
        """
        return not (self.nodes or self.ways or self.relations)

    @property
    def counts(self) -> dict[str, int]:
        """``{"nodes": .., "ways": .., "relations": ..}``."""
        return {
            "nodes": len(self.nodes),
            "ways": len(self.ways),
            "relations": len(self.relations),
        }

    def meta(self) -> dict[str, Any]:
        """Everything but the elements (what the ``.meta.json`` sidecar holds)."""
        return {
            "format": SNAPSHOT_FORMAT,
            "tile": self.tile.name,
            "layer": self.layer,
            "selectors": list(self.selectors),
            "query": self.query,
            "mirror": self.mirror,
            "fetched_at": self.fetched_at,
            "generator": self.generator,
            "osm_base": self.osm_base,
            "digest": self.digest,
            "counts": self.counts,
        }

    def elements(self) -> list[dict[str, Any]]:
        """The elements in the Overpass JSON shape, in the order the mirror sent them."""
        out: list[dict[str, Any]] = []
        for n in self.nodes:
            el: dict[str, Any] = {"type": "node", "id": n.id, "lat": n.lat, "lon": n.lon}
            if n.tags:
                el["tags"] = dict(n.tags)
            out.append(el)
        for w in self.ways:
            el = {"type": "way", "id": w.id, "nodes": list(w.nodes)}
            if w.tags:
                el["tags"] = dict(w.tags)
            out.append(el)
        for r in self.relations:
            el = {
                "type": "relation",
                "id": r.id,
                "members": [{"type": m.type, "ref": m.ref, "role": m.role} for m in r.members],
            }
            if r.tags:
                el["tags"] = dict(r.tags)
            out.append(el)
        return out

    def to_json(self) -> bytes:
        """The whole document (metadata + elements), uncompressed."""
        doc = self.meta()
        doc["elements"] = self.elements()
        return orjson.dumps(doc)

    @classmethod
    def from_json(cls, raw: bytes) -> OsmSnapshot:
        """Inverse of :meth:`to_json`; raises :class:`ValueError` on a foreign document."""
        doc = orjson.loads(raw)
        if not isinstance(doc, dict) or doc.get("format") != SNAPSHOT_FORMAT:
            raise ValueError(f"not a {SNAPSHOT_FORMAT} document")
        nodes, ways, relations = split_elements(doc["elements"])
        return cls(
            tile=TileRef.parse(str(doc["tile"])),
            layer=str(doc["layer"]),
            selectors=tuple(str(s) for s in doc.get("selectors", ())),
            query=str(doc.get("query", "")),
            mirror=str(doc.get("mirror", "")),
            fetched_at=str(doc.get("fetched_at", "")),
            generator=str(doc.get("generator", "")),
            osm_base=str(doc.get("osm_base", "")),
            nodes=tuple(nodes),
            ways=tuple(ways),
            relations=tuple(relations),
            digest=str(doc["digest"]),
        )


def _utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def snapshot_from_overpass(
    tile: TileRef,
    layer: LayerSpec | str,
    body: bytes,
    *,
    mirror: str = "",
    query: str = "",
    fetched_at: str | None = None,
) -> OsmSnapshot:
    """Build a snapshot from one Overpass JSON answer (spec section 5)."""
    spec = LAYERS[layer] if isinstance(layer, str) else layer
    doc = orjson.loads(body)
    nodes, ways, relations = parse_overpass_json(body)
    osm3s = doc.get("osm3s") or {}
    return OsmSnapshot(
        tile=tile,
        layer=spec.name,
        selectors=tuple(spec.selectors),
        query=query or overpass_query(spec.selectors, tile),
        mirror=mirror,
        fetched_at=fetched_at or _utc_now(),
        generator=str(doc.get("generator", "")),
        osm_base=str(osm3s.get("timestamp_osm_base", "")),
        nodes=tuple(nodes),
        ways=tuple(ways),
        relations=tuple(relations),
        digest=blake3.blake3(_canonical(nodes, ways, relations)).hexdigest(),
    )


def snapshot_label(snapshots: Iterable[OsmSnapshot]) -> str:
    """``osm-<12 hex>``: the stable content label of a tile's OSM data (arbitration A4).

    An opaque string naming the state of the OSM data behind a tile, content-based: a refetch
    of unchanged data gives the same label.
    """
    lines = sorted(f"{s.layer}:{s.digest}" for s in snapshots)
    if not lines:
        return "osm-empty"
    return "osm-" + blake3.blake3("\n".join(lines).encode()).hexdigest()[:12]


# -- snapshot store ------------------------------------------------------------------------


class SnapshotStore:
    """Where the OrthoStudio XP snapshots
    live: ``<root>/osm/<folder>/<tile>/<tile>_<layer>.osm.json.zst``."""

    def __init__(self, root: Path | None = None, *, level: int = 10) -> None:
        self.root = Path(root) if root is not None else data_root()
        self.level = level

    def path_for(self, tile: TileRef, layer: str) -> Path:
        """Canonical path of the compressed snapshot."""
        return self.root / "osm" / tile.folder / tile.name / f"{tile.name}_{layer}.osm.json.zst"

    def meta_path_for(self, tile: TileRef, layer: str) -> Path:
        """Sidecar with the metadata only (digest, counts, mirror, date): cheap to read."""
        return self.path_for(tile, layer).with_suffix("").with_suffix(".meta.json")

    def save(self, snapshot: OsmSnapshot, *, sidecar: bool = True) -> Path:
        """Write the snapshot and its sidecar atomically; raises ``OSM_CACHE_WRITE_FAILED``.

        ``sidecar=False`` writes the snapshot alone. A cache wants the sidecar, which reads the
        digest without unpacking; a library to be published wants only what its manifest names,
        and a file the manifest does not name is a file nobody can check (2026-09-23).
        """
        path = self.path_for(snapshot.tile, snapshot.layer)
        blob = zstandard.ZstdCompressor(level=self.level).compress(snapshot.to_json())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, blob)
            if sidecar:
                _atomic_write(
                    self.meta_path_for(snapshot.tile, snapshot.layer),
                    orjson.dumps(snapshot.meta(), option=orjson.OPT_INDENT_2),
                )
        except OSError as exc:
            raise OsxpError(
                "OSM_CACHE_WRITE_FAILED", context={"path": path, "reason": str(exc)}
            ) from exc
        return path

    def load(self, tile: TileRef, layer: str) -> OsmSnapshot | None:
        """The stored snapshot, or ``None`` when there is none.

        A file that exists but cannot be read raises ``OSM_CACHE_UNREADABLE``: the caller
        deletes it and downloads the layer again.
        """
        path = self.path_for(tile, layer)
        if not path.is_file():
            return None
        try:
            raw = zstandard.ZstdDecompressor().decompress(path.read_bytes())
            return OsmSnapshot.from_json(raw)
        except (OSError, ValueError, KeyError, zstandard.ZstdError, orjson.JSONDecodeError) as exc:
            raise OsxpError(
                "OSM_CACHE_UNREADABLE", context={"path": path, "reason": str(exc)}
            ) from exc

    def digest_for(self, tile: TileRef, layer: str) -> str | None:
        """Content key of the stored layer, read from the sidecar; ``None`` when absent."""
        meta = self.meta_path_for(tile, layer)
        if not meta.is_file():
            return None
        try:
            doc = orjson.loads(meta.read_bytes())
            return str(doc["digest"])
        except (OSError, KeyError, orjson.JSONDecodeError) as exc:
            raise OsxpError(
                "OSM_CACHE_UNREADABLE", context={"path": meta, "reason": str(exc)}
            ) from exc


def _atomic_write(path: Path, blob: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, path)


# -- transport -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpReply:
    """One HTTP answer, or a transport failure (``status = 0`` and ``error`` set)."""

    status: int
    body: bytes
    headers: Mapping[str, str]
    elapsed_s: float
    error: str | None = None
    wire_bytes: int = 0
    """Bytes received for the body, as sent (gzip); 0 when the transport does not say: the
    body's length then stands for them."""


class Transport(Protocol):
    """What :class:`OverpassClient` needs from an HTTP client (tests inject their own)."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
        read_timeout_s: float = float(DEFAULT_QUERY_TIMEOUT_S),
    ) -> HttpReply: ...

    async def aclose(self) -> None: ...


class CurlTransport:
    """Default transport: one long-lived ``curl_cffi`` session (ADR 0005 decision 2)."""

    def __init__(self) -> None:
        self._session: Any | None = None

    def _get_session(self) -> Any:
        if self._session is None:
            from curl_cffi.requests import AsyncSession

            # HTTP/1.1, pinned. Left to negotiate, a session of this shape stalled until its
            # timeout, and pinned to HTTP/2 it stopped at exactly one mebibyte received out of
            # four -- a flow-control window that never reopened. An Overpass answer runs to tens
            # of megabytes and two are in flight at a time, so multiplexing buys nothing here
            # (2026-09-23).
            self._session = AsyncSession(verify=ca_bundle(), http_version="v1")
        return self._session

    async def request(
        self,
        method: str,
        url: str,
        *,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
        read_timeout_s: float = float(DEFAULT_QUERY_TIMEOUT_S),
    ) -> HttpReply:
        session = self._get_session()
        t0 = time.perf_counter()
        try:
            r = await session.request(
                method,
                url,
                data=dict(data) if data else None,
                headers=dict(headers) if headers else None,
                timeout=(connect_timeout_s, read_timeout_s),
            )
        except Exception as exc:  # any transport failure is one outcome here
            return HttpReply(0, b"", {}, time.perf_counter() - t0, f"{type(exc).__name__}: {exc}")
        body = bytes(r.content)
        return HttpReply(
            status=int(r.status_code),
            body=body,
            headers={k.lower(): v for k, v in r.headers.items()},
            elapsed_s=time.perf_counter() - t0,
            wire_bytes=int(getattr(r, "download_size", 0) or 0) or len(body),
        )

    async def aclose(self) -> None:
        if self._session is not None:
            with contextlib.suppress(Exception):
                await self._session.close()
            self._session = None


# -- client --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MirrorHealth:
    """What the client knows about one mirror."""

    code: str
    healthy: bool
    state: Literal["closed", "half-open", "open"]
    status: int | None = None
    elapsed_s: float = 0.0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Attempt:
    """One query sent to one mirror, kept for the UI and the logs."""

    layer: str
    mirror: str
    ok: bool
    status: int
    elapsed_s: float
    bytes: int
    error: str | None = None


@dataclass(slots=True)
class _MirrorState:
    mirror: Mirror
    open_until: float = 0.0
    cooldown_s: float = COOLDOWN_S
    given_cooldown_s: float = COOLDOWN_S
    """What the client asked for, restored by a reset rather than the module's own constant."""
    failures: int = 0
    successes: int = 0
    requests: int = 0
    last_error: str | None = None
    last_request_at: float = -1e9
    rate_limited: bool = False
    """The server named a delay (``Retry-After``): a round does not reopen it."""

    def state(self, now: float) -> Literal["closed", "half-open", "open"]:
        if self.open_until <= 0.0:
            return "closed"
        return "open" if now < self.open_until else "half-open"


def _later(open_until: float, wait: float) -> float:
    """The later of a reopen already set and one ``wait`` from now.

    A failure may put a reopen off; it may never bring one forward. The doctor probes every
    mirror without asking the breaker, so running it because builds were failing replaced the
    hour a server had asked for with the ten minutes of an ordinary failure (found in review,
    2026-09-23).
    """
    return max(open_until, time.monotonic() + wait)


class MirrorBoard:
    """The breaker state of every mirror, shared by the clients given the same board.

    A build downloads each tile with a client of its own, in a thread and an event loop of its
    own. With a board per client every tile found a dead mirror again by its timeouts: on
    2026-09-14 ``lz4`` refused connections and each tile lost 10 s to it (``osm-source.md`` 8).
    The builds of one process share :func:`shared_board`, so a mirror put aside stays aside for
    its cooldown, whatever tile or build comes next; a client made without a board keeps one to
    itself. Thread-safe: the clients of a batch run in different threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, _MirrorState] = {}

    def register(self, mirrors: Iterable[Mirror], cooldown_s: float) -> None:
        """Give a state to the mirrors the board does not know yet (known ones keep theirs)."""
        with self._lock:
            for m in mirrors:
                if m.code not in self._states:
                    self._states[m.code] = _MirrorState(
                        m, cooldown_s=cooldown_s, given_cooldown_s=cooldown_s
                    )

    def state(self, code: str, now: float) -> Literal["closed", "half-open", "open"]:
        with self._lock:
            return self._states[code].state(now)

    def health(self, code: str, now: float) -> MirrorHealth:
        with self._lock:
            st = self._states[code]
            state = st.state(now)
            return MirrorHealth(
                code=code, healthy=state != "open", state=state, error=st.last_error
            )

    def open(
        self,
        code: str,
        *,
        reason: str,
        seconds: float | None = None,
        quota: bool = False,
        busy: bool = False,
    ) -> None:
        with self._lock:
            st = self._states[code]
            st.failures += 1
            st.last_error = reason
            # a server that named a delay is left alone until it has passed, whatever a round of
            # this layer or of another would like. A failure that is not a refusal never clears
            # it: the doctor probes every mirror without asking the breaker, so running it
            # because builds were failing turned an hour the server asked for into an immediate
            # retry (found in review, 2026-09-23)
            st.rate_limited = st.rate_limited or quota
            if quota:
                # What was refused is our address, not this machine, so the wait is what the
                # server asked for and the doubling meant for a machine that is down does not
                # apply. Telling the two apart by whether a ``Retry-After`` header came is what
                # the first attempt at this did, and the servers mostly send none: a 429 then
                # shut the whole cluster for ten minutes, then twenty, then forty, which is the
                # outage of 2026-09-22 made by us rather than by them (found in review,
                # 2026-09-23). A delay longer than an hour is still capped, since no build waits
                # that long for one layer.
                wait = QUOTA_COOLDOWN_S if seconds is None else max(seconds, 1.0)
                st.open_until = _later(st.open_until, min(wait, MAX_COOLDOWN_S))
            elif busy:
                # Busy is not broken: a 504 means the machine or its upstream could not finish
                # this query, and the same query a moment later is answered. It took the cooldown
                # built for a machine that is down, six hundred seconds and doubling, and nothing
                # showed while the rounds gave every breaker back before asking again. With the
                # breakers as the one clock, that mis-tuning cost a layer two thirds of its
                # attempts (measured against v0.1.14, 2026-09-25). No doubling either: a server
                # busy twice is busy, not failing.
                st.open_until = _later(st.open_until, seconds if seconds else BUSY_COOLDOWN_S)
            else:
                st.open_until = _later(st.open_until, st.cooldown_s)
                st.cooldown_s = min(st.cooldown_s * 2, MAX_COOLDOWN_S)

    def close(self, code: str, cooldown_s: float) -> None:
        with self._lock:
            st = self._states[code]
            st.open_until = 0.0
            st.failures = 0
            st.cooldown_s = cooldown_s
            st.successes += 1
            st.last_error = None
            st.rate_limited = False

    def failure(self, code: str) -> None:
        with self._lock:
            self._states[code].failures += 1

    def request(self, code: str) -> None:
        with self._lock:
            self._states[code].requests += 1

    def last_errors(self) -> dict[str, str]:
        """Why each mirror put aside was put aside, for the message the user reads."""
        with self._lock:
            out: dict[str, str] = {}
            for code, st in self._states.items():
                if st.last_error:
                    out[code] = st.last_error
            return out

    def soonest(self, codes: Iterable[str]) -> float:
        """The earliest instant one of ``codes`` may be asked again, 0 when any is open now.

        The one clock of the rounds: each breaker carries what its server said, and this is the
        first moment any of them is ready. Asking earlier finds nobody and ends the layer.
        """
        with self._lock:
            times = [self._states[c].open_until for c in codes if c in self._states]
        return min(times) if times else 0.0

    def reopen(self, codes: Iterable[str]) -> None:
        """Give these mirrors another chance, except one that asked to be left alone.

        What a round of :meth:`OverpassClient.fetch_layer` needs, and what ``reset`` was wrongly
        used for: a global reset discards the ``Retry-After`` a server asked for (the way to turn
        a rate limit into a ban), forgets the doubling, and reaches across the other layers of the
        same tile, which re-try a machine already known dead (2026-09-23).
        """
        with self._lock:
            for code in codes:
                st = self._states.get(code)
                if st is None or st.rate_limited:
                    continue
                st.open_until = 0.0

    def reset(self) -> None:
        """Give every mirror another chance, cooldowns back to their start.

        A build the user asked for again says something the breaker cannot know: that the user
        has waited, or fixed their network, or that a mirror is back. Without this, a night when
        two mirrors were down left every later build failing in 4 s for up to an hour, and the
        only way out was to quit the app (2026-09-22).
        """
        with self._lock:
            for st in self._states.values():
                st.open_until = 0.0
                st.failures = 0
                st.cooldown_s = st.given_cooldown_s
                st.last_error = None
                st.rate_limited = False


_SHARED_BOARD = MirrorBoard()


def shared_board() -> MirrorBoard:
    """The board of this process, which the builds' clients share (:class:`MirrorBoard`)."""
    return _SHARED_BOARD


LAYER_WORDS = {
    "airports": "airports",
    "big_roads": "roads",
    "small_roads": "small roads",
    "water": "water",
    "coastline": "coastline",
}
"""What each layer is called for somebody who flies rather than maps."""

_RATE_FLOOR_MB_S = 0.05
"""Below this the average since the tile started says nothing, so it is left out."""

_WAIT_STEP_S = 15
"""How coarsely the wait is told: a line that changes every second is a line nobody reads."""


def osm_progress_message(
    tile: TileRef,
    asked: Sequence[str],
    received: Sequence[str],
    wire_bytes: int,
    elapsed_s: float,
) -> str:
    """The progress line of a tile's map data.

    A map data server sends nothing until it has worked the whole answer out: it queues the
    question, computes, then delivers in one burst. So the line sat at ``0/4 OSM layers`` with
    the average rate falling towards ``0.0 MB/s`` for minutes, which is also exactly what a
    build that has stopped looks like. A user watching it said it told him nothing, and he was
    right (2026-09-23).

    It now says what is being waited for, names the layers in words a pilot knows, and gives a
    rate only when something is really coming down: an average over a long wait is not a rate.
    The rate keeps its brackets, where the Works page reads it (``ui.md`` 2.2).
    """
    if not received:
        waiting = ", ".join(LAYER_WORDS.get(name, name) for name in asked)
        text = f"{tile.name}: waiting for the map data server ({waiting})"
        # how long, in steps of a quarter minute: the wait is reported every second so the page
        # knows the step is alive, and a line that changes every second is a line nobody reads
        waited = int(elapsed_s // _WAIT_STEP_S) * _WAIT_STEP_S
        return f"{text}, {waited} s" if waited else text
    got = ", ".join(LAYER_WORDS.get(name, name) for name in received)
    text = f"{tile.name}: {len(received)} of {len(asked)} back: {got}"
    rate = wire_bytes / 1e6 / elapsed_s if wire_bytes > 0 and elapsed_s > 0 else 0.0
    if rate >= _RATE_FLOOR_MB_S:
        text += f" ({rate:.1f} MB/s)"
    return text


class OverpassClient:
    """Downloads OSM layers from the mirror registry, with a breaker and fail-over.

    Policy: ``docs/specs/osm-source.md`` section 4 and ``docs/specs/net-download.md`` 5.5.
    At most ``max_in_flight`` requests per *cluster*, ``min_interval_s`` between two of them,
    each layer to the least busy cluster, ``max_attempts`` attempts across mirrors (waiting
    ``attempt_delay_s`` only before another machine of the cluster that just failed), and a
    circuit breaker, kept on ``board``, that puts a mirror aside for ``cooldown_s`` (doubling,
    capped).
    """

    def __init__(
        self,
        mirrors: Sequence[Mirror] = MIRRORS,
        transport: Transport | None = None,
        *,
        query_timeout_s: int = DEFAULT_QUERY_TIMEOUT_S,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
        health_timeout_s: float = HEALTH_TIMEOUT_S,
        cooldown_s: float = COOLDOWN_S,
        busy_cooldown_s: float = BUSY_COOLDOWN_S,
        max_attempts: int = MAX_ATTEMPTS,
        attempt_delay_s: float = ATTEMPT_DELAY_S,
        rounds: int = ROUNDS,
        max_in_flight: int = MAX_IN_FLIGHT,
        min_interval_s: float = MIN_INTERVAL_S,
        allow_last_resort: bool = True,
        user_agent: str = USER_AGENT,
        board: MirrorBoard | None = None,
        at: str = "",
    ) -> None:
        if not mirrors:
            raise ValueError("at least one mirror is needed")
        if max_attempts < 1 or max_in_flight < 1:
            raise ValueError("max_attempts and max_in_flight must be >= 1")
        self.mirrors = tuple(mirrors)
        self.transport: Transport = transport if transport is not None else CurlTransport()
        self._owns_transport = transport is None
        self.query_timeout_s = query_timeout_s
        self.at = at  # only the bake's proof sets this; a build always asks for today
        self.connect_timeout_s = connect_timeout_s
        self.health_timeout_s = health_timeout_s
        self.cooldown_s = cooldown_s
        self.busy_cooldown_s = busy_cooldown_s
        self.max_attempts = max_attempts
        self.attempt_delay_s = attempt_delay_s
        self.rounds = max(1, rounds)
        self.max_in_flight = max_in_flight
        self.min_interval_s = min_interval_s
        self.allow_last_resort = allow_last_resort
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }
        self.attempts: list[Attempt] = []
        self._board = board if board is not None else MirrorBoard()
        self._board.register(self.mirrors, cooldown_s)
        self._order = {m.code: i for i, m in enumerate(self.mirrors)}
        self._clusters = {m.cluster for m in self.mirrors}
        self._busy: dict[str, int] = dict.fromkeys(self._clusters, 0)
        """Requests given to each cluster and not ended, waiting for its slot or in flight: the
        next layer goes to the least busy cluster."""
        self._gates: dict[str, asyncio.Semaphore] = {}
        self._cluster_last: dict[str, float] = dict.fromkeys(self._clusters, -1e9)
        self._cluster_locks: dict[str, asyncio.Lock] = {}

    # -- lifecycle ---------------------------------------------------------------------

    async def __aenter__(self) -> OverpassClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the transport when this client created it."""
        if self._owns_transport:
            await self.transport.aclose()

    # -- breaker -----------------------------------------------------------------------

    def health_snapshot(self) -> dict[str, MirrorHealth]:
        """The breaker state of every mirror, without sending anything."""
        now = time.monotonic()
        return {m.code: self._board.health(m.code, now) for m in self.mirrors}

    def _open(
        self,
        code: str,
        *,
        reason: str,
        seconds: float | None = None,
        quota: bool = False,
        busy: bool = False,
    ) -> None:
        self._board.open(code, reason=reason, seconds=seconds, quota=quota, busy=busy)

    def _open_cluster(
        self, cluster: str, *, reason: str, seconds: float | None = None, quota: bool = False
    ) -> None:
        for m in self.mirrors:
            if m.cluster == cluster:
                self._open(m.code, reason=reason, seconds=seconds, quota=quota)

    def _close(self, code: str) -> None:
        self._board.close(code, self.cooldown_s)

    def _pick(self, tried: set[str]) -> Mirror | None:
        """The mirror of the next attempt: not tried yet for this layer, its breaker not open,
        the last resort only when no other is left. Among them the least busy cluster, then the
        registry's order: with two clusters healthy, four layers are in flight at once instead
        of two (``osm-source.md`` 4)."""
        now = time.monotonic()
        usable = [
            m
            for m in self.mirrors
            if m.code not in tried and self._board.state(m.code, now) != "open"
        ]
        ordinary = [m for m in usable if not m.last_resort]
        pool = ordinary if ordinary else (usable if self.allow_last_resort else [])
        if not pool:
            return None
        return min(
            pool,
            key=lambda m: (
                self._busy[m.cluster] >= self.max_in_flight,
                self._busy[m.cluster],
                self._order[m.code],
            ),
        )

    # -- one request -------------------------------------------------------------------

    def _gate(self, cluster: str) -> asyncio.Semaphore:
        gate = self._gates.get(cluster)
        if gate is None:
            gate = asyncio.Semaphore(self.max_in_flight)
            self._gates[cluster] = gate
        return gate

    def _lock(self, cluster: str) -> asyncio.Lock:
        lock = self._cluster_locks.get(cluster)
        if lock is None:
            lock = asyncio.Lock()
            self._cluster_locks[cluster] = lock
        return lock

    async def _send(
        self,
        mirror: Mirror,
        query: str,
        *,
        read_timeout_s: float | None = None,
        recheck: bool = True,
    ) -> HttpReply | None:
        """POST one query, respecting the per-cluster quota and the minimum interval.

        ``None`` (nothing sent) when ``recheck`` and the mirror's breaker opened while the query
        waited for its slot: another layer found it dead meanwhile, and the query goes elsewhere
        instead of waiting for the same timeout (four layers did, on 2026-09-14).
        """
        async with self._gate(mirror.cluster):
            async with self._lock(mirror.cluster):
                wait = self._cluster_last[mirror.cluster] + self.min_interval_s - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                if recheck and self._board.state(mirror.code, time.monotonic()) == "open":
                    return None
                self._cluster_last[mirror.cluster] = time.monotonic()
            self._board.request(mirror.code)
            return await self.transport.request(
                "POST",
                mirror.interpreter,
                data={"data": query},
                headers=self.headers,
                connect_timeout_s=self.connect_timeout_s,
                read_timeout_s=(
                    float(self.query_timeout_s + 30) if read_timeout_s is None else read_timeout_s
                ),
            )

    # -- health ------------------------------------------------------------------------

    async def check_health(self) -> dict[str, MirrorHealth]:
        """Probe every mirror once (spec section 4, health check) and update the breakers.

        ``GET <status_url>`` with a 5 s timeout, or a minimal query when no status URL is
        declared. A 403 alone does not disqualify (``overpass.openstreetmap.fr`` refuses the
        status endpoint while serving queries); no answer or a 5xx opens the breaker.
        """
        results = await asyncio.gather(*(self._probe(m) for m in self.mirrors))
        return {h.code: h for h in results}

    async def _probe(self, mirror: Mirror) -> MirrorHealth:
        if mirror.status_url:
            reply = await self.transport.request(
                "GET",
                mirror.status_url,
                headers=self.headers,
                connect_timeout_s=self.connect_timeout_s,
                read_timeout_s=self.health_timeout_s,
            )
        else:
            sent = await self._send(
                mirror,
                "[out:json][timeout:10];node(id:1);out ids;",
                read_timeout_s=self.health_timeout_s,
                recheck=False,
            )
            assert sent is not None
            reply = sent
        healthy = reply.error is None and (reply.status < 500 and reply.status != 429)
        if healthy:
            if reply.status == 200:
                self._close(mirror.code)
        elif reply.status == 429:
            self._open_cluster(
                mirror.cluster,
                reason="health: HTTP 429",
                seconds=slot_wait_s(reply.body.decode("utf-8", "replace")),
                quota=True,
            )
        else:
            self._open(mirror.code, reason=reply.error or f"health: HTTP {reply.status}")
        now = time.monotonic()
        return MirrorHealth(
            code=mirror.code,
            healthy=healthy,
            state=self._board.state(mirror.code, now),
            status=reply.status or None,
            elapsed_s=reply.elapsed_s,
            error=reply.error,
        )

    # -- layers ------------------------------------------------------------------------

    async def fetch_layer(
        self,
        tile: TileRef,
        layer: LayerSpec | str,
        *,
        road_level: int = 1,
        on_reply: Callable[[HttpReply], None] | None = None,
        deadline: float | None = None,
    ) -> OsmSnapshot:
        """One layer of one tile, from the first mirror that answers (spec section 4).

        ``on_reply`` sees every answer (the tile counts the bytes received). Raises
        ``OsxpError("OSM_LAYER_UNAVAILABLE")`` when every attempt failed.

        ``deadline`` is when the caller stops waiting (``time.monotonic``). Without it, a layer
        would begin its third round, sleep forty seconds and ask three machines while the tile
        had five seconds left, and the build then said "timed out" instead of naming the servers
        that refused (2026-09-23).
        """
        spec = self._resolve(layer, road_level)
        query = overpass_query(spec.selectors, tile, self.query_timeout_s, self.at)
        reasons: list[str] = []
        for round_no in range(self.rounds):
            if round_no:
                # Every mirror refused. One clock decides when to ask again: the breakers, which
                # carry what each server said (a 429's Retry-After, the slot its status page
                # names, or the cooldown of a machine that is down). Waiting a schedule of our
                # own instead woke the round before the servers were ready, found nobody to ask
                # and gave up in a minute (found in review, 2026-09-24).
                if deadline is None:
                    # The wait is what the servers named, and the caller's deadline is the budget
                    # for it. No deadline, no budget: one round and the answer, rather than a
                    # sleep of whatever length a server happened to ask for.
                    reasons.append("no deadline to wait against")
                    break
                now = time.monotonic()
                free = self._board.soonest(m.code for m in self.mirrors)
                pause = max(self.attempt_delay_s, free - now)
                if now + pause >= deadline:
                    reasons.append("no time left for another round")
                    break
                await asyncio.sleep(pause)
                reasons.append(f"round {round_no + 1}")
            snap, asked = await self._one_round(tile, spec, query, reasons, on_reply)
            if snap is not None:
                return snap
            if round_no and not asked:
                # the breakers were just given back and there was still nobody to ask: every
                # server is in a cooldown this round's pause will not outlast. Waiting another
                # forty seconds to be told the same thing costs every tile of the batch
                # (2026-09-23, the address having spent its quota)
                reasons.append("every server is still set aside")
                break
            if not self._worth_another_round(reasons):
                break
            if deadline is not None and time.monotonic() >= deadline:
                reasons.append("no time left")
                break
        detail = "; ".join(reasons)
        quota = [r for r in reasons if "RATE_LIMITED" in r]
        if quota and len(quota) >= len([r for r in reasons if ": OSM_" in r]) - 1:
            # every server that answered refused for the same reason: say that one thing
            raise OsxpError(
                "OSM_LAYER_UNAVAILABLE",
                context={"layer": spec.name, "tile": tile.name, "attempts": detail},
                message=(
                    f"The map data servers are not taking more requests from your address, so "
                    f"{spec.name} for tile {tile.name} could not be downloaded ({detail})."
                ),
                remedy=(
                    "These servers count requests per internet address, and a whole region is "
                    "hundreds of them. Wait a few minutes and build again, or build fewer tiles "
                    "at a time; the tiles already finished are kept."
                ),
            )
        raise OsxpError(
            "OSM_LAYER_UNAVAILABLE",
            context={"layer": spec.name, "tile": tile.name, "attempts": detail},
            message=(
                f"OSM layer {spec.name} for tile {tile.name} could not be obtained from any "
                + (f"mirror ({detail})." if detail else "mirror.")
            ),
        )

    @staticmethod
    def _worth_another_round(reasons: Sequence[str]) -> bool:
        """Whether anything that refused may pass: a busy machine, never a refusal of principle.

        ``.fr``'s 403 ("white-listed usages") is the same in a minute; a 504, a 429 and a silence
        are not.
        """
        passing = ("OSM_MIRROR_UNREACHABLE", "OSM_MIRROR_RATE_LIMITED", "OSM_RESPONSE")
        return any(
            any(code in why for code in passing) or "REJECTED (HTTP 5" in why for why in reasons
        )

    async def _one_round(
        self,
        tile: TileRef,
        spec: LayerSpec,
        query: str,
        reasons: list[str],
        on_reply: Callable[[HttpReply], None] | None,
    ) -> tuple[OsmSnapshot | None, int]:
        """One pass over the registry: the snapshot and how many mirrors were actually asked.

        The count matters: a round that asked nobody at all, because every mirror is still set
        aside, has nothing to say and the next round will have nothing either.
        """
        tried: set[str] = set()
        attempt = 0
        failed_cluster: str | None = None
        while attempt < self.max_attempts:
            mirror = self._pick(tried)
            if mirror is None:
                aside = self._board.last_errors()
                left = [f"{m.code}: {aside[m.code]}" for m in self.mirrors if m.code in aside]
                reasons.append("no mirror left to try" + (f" ({'; '.join(left)})" if left else ""))
                break
            if mirror.cluster == failed_cluster:
                # another machine of the cluster that just pushed back: give the cluster a
                # moment. A mirror elsewhere, and a sibling of a machine that simply did not
                # answer, are asked at once (2026-09-22: lz4 was dead and z, its sibling, was
                # the only mirror left; waiting 5 s for it made no sense).
                await asyncio.sleep(self.attempt_delay_s)
            self._busy[mirror.cluster] += 1
            try:
                sent = await self._send(mirror, query)
            finally:
                self._busy[mirror.cluster] -= 1
            if sent is None:
                continue  # its breaker opened while this layer waited: no attempt spent
            reply = sent
            attempt += 1
            tried.add(mirror.code)
            if on_reply is not None:
                on_reply(reply)
            outcome = self._classify(mirror, reply)
            if reply.status == 429:
                # the cluster is now set aside for a guess; its status page says when a slot
                # actually frees, and one small request buys the whole build that number, since
                # the board is shared by every tile in flight (found attacking this, 2026-09-24)
                await self._ask_when_free(mirror)
            if outcome is None:
                self._close(mirror.code)
                self.attempts.append(
                    Attempt(
                        spec.name, mirror.code, True, reply.status, reply.elapsed_s, len(reply.body)
                    )
                )
                return snapshot_from_overpass(
                    tile, spec, reply.body, mirror=mirror.code, query=query
                ), attempt
            code, reason = outcome
            failed_cluster = mirror.cluster if _cluster_pushed_back(reply) else None
            reasons.append(f"{mirror.code}: {code} ({reason})")
            self.attempts.append(
                Attempt(
                    spec.name,
                    mirror.code,
                    False,
                    reply.status,
                    reply.elapsed_s,
                    len(reply.body),
                    code,
                )
            )
        return None, attempt

    async def fetch_tile(
        self,
        tile: TileRef,
        *,
        road_level: int = 1,
        layers: Sequence[LayerSpec | str] | None = None,
        cancel: threading.Event | None = None,
        timeout_s: float | None = None,
        progress: Callable[[float, str], None] | None = None,
    ) -> dict[str, OsmSnapshot]:
        """Every layer the tile needs, concurrently within the per-cluster quotas.

        ``cancel`` is the caller's (thread-based) cancellation token: it is polled while the
        layers are in flight, and a set token cancels the in-flight requests and raises
        ``SYS_CANCELLED``. ``timeout_s`` bounds the whole tile the same way
        (``NET_TIMEOUT``). Without them a Ctrl-C left the node downloading (review 4, C3).
        ``progress(fraction, message)`` hears of each layer received, and every
        :data:`PROGRESS_PERIOD_S` meanwhile: the layers received out of the tile's
        (:func:`osm_progress_message`) and the download rate so far.
        """
        specs = (
            layers_for(road_level)
            if layers is None
            else tuple(self._resolve(x, road_level) for x in layers)
        )
        if cancel is not None and cancel.is_set():
            raise OsxpError("SYS_CANCELLED", context={"stage": "osm", "tile": tile.name})
        t0 = time.monotonic()
        asked = [s.name for s in specs]
        back: list[str] = []
        wire = [0]

        def count(reply: HttpReply) -> None:
            wire[0] += reply.wire_bytes or len(reply.body)

        def report() -> None:
            if progress is not None:
                message = osm_progress_message(tile, asked, back, wire[0], time.monotonic() - t0)
                progress(len(back) / len(asked) if asked else 1.0, message)

        async def one(spec: LayerSpec) -> OsmSnapshot:
            snap = await self.fetch_layer(tile, spec, on_reply=count, deadline=deadline)
            back.append(spec.name)
            report()
            return snap

        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        gather = asyncio.gather(*(one(s) for s in specs))
        if cancel is None and timeout_s is None and progress is None:
            return {s.layer: s for s in await gather}
        task = asyncio.ensure_future(gather)
        reported = time.monotonic()
        while True:
            done, _ = await asyncio.wait([task], timeout=_CANCEL_POLL_S)
            if done:
                return {s.layer: s for s in await task}
            if time.monotonic() - reported >= PROGRESS_PERIOD_S:
                reported = time.monotonic()
                report()
            if cancel is not None and cancel.is_set():
                reason: tuple[str, dict[str, Any]] = (
                    "SYS_CANCELLED",
                    {"stage": "osm", "tile": tile.name},
                )
            elif deadline is not None and time.monotonic() >= deadline:
                reason = ("NET_TIMEOUT", {"host": "overpass", "timeout": timeout_s})
            else:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, OsxpError):
                await task
            raise OsxpError(reason[0], context=reason[1])

    def fetch_tile_sync(
        self,
        tile: TileRef,
        *,
        road_level: int = 1,
        layers: Sequence[LayerSpec | str] | None = None,
        cancel: threading.Event | None = None,
        timeout_s: float | None = None,
        progress: Callable[[float, str], None] | None = None,
    ) -> dict[str, OsmSnapshot]:
        """Synchronous wrapper of :meth:`fetch_tile` for callers outside an event loop."""

        async def run() -> dict[str, OsmSnapshot]:
            try:
                return await self.fetch_tile(
                    tile,
                    road_level=road_level,
                    layers=layers,
                    cancel=cancel,
                    timeout_s=timeout_s,
                    progress=progress,
                )
            finally:
                await self.aclose()

        return asyncio.run(run())

    # -- helpers -----------------------------------------------------------------------

    @staticmethod
    def _resolve(layer: LayerSpec | str, road_level: int) -> LayerSpec:
        if isinstance(layer, LayerSpec):
            return layer
        for spec in layers_for(max(road_level, LAYERS[layer].min_road_level)):
            if spec.name == layer:
                return spec
        return LAYERS[layer]

    async def _ask_when_free(self, mirror: Mirror) -> float | None:
        """Ask a mirror's status page when this address may query again, and hold the cluster
        until then. ``None`` when it has no status page, does not answer, or says neither.

        Overpass counts queries per internet address and frees a slot on its own clock. We set a
        guessed minute instead of reading the number it publishes, so a build gave up while the
        quota needed longer (a user, 2026-09-24). One request, at the moment we are about to wait
        anyway, and every tile of the build learns it through the shared board.
        """
        if not mirror.status_url:
            return None
        reply = await self.transport.request(
            "GET",
            mirror.status_url,
            headers=self.headers,
            connect_timeout_s=self.connect_timeout_s,
            read_timeout_s=self.health_timeout_s,
        )
        if reply.error is not None or reply.status != 200:
            return None
        wait = slot_wait_s(reply.body.decode("utf-8", "replace"))
        if wait is None:
            return None
        # the same reason the 429 already set: a user reads what his mirrors answered, and
        # "a slot in 0 s" beside a failed build told him nothing (found in review, 2026-09-24)
        self._open_cluster(mirror.cluster, reason="HTTP 429", seconds=wait, quota=True)
        return wait

    def _classify(self, mirror: Mirror, reply: HttpReply) -> tuple[str, str] | None:
        """``None`` when the answer is usable, else ``(error code, reason)`` (spec 4)."""
        if reply.error is not None:
            self._open(mirror.code, reason=reply.error)
            return "OSM_MIRROR_UNREACHABLE", reply.error
        if reply.status == 429:
            delay = _retry_after(reply.headers)
            self._open_cluster(mirror.cluster, reason="HTTP 429", seconds=delay, quota=True)
            # not a failure of the machine: the public servers count requests per address, and a
            # whole region is hundreds of them. Said in those words, since "HTTP 429" told a user
            # nothing while he wondered why his builds had turned random (2026-09-23).
            if not delay:
                waited = "usually a few minutes"
            elif delay < 90:
                waited = f"try again in about {delay:.0f} s"
            else:
                waited = f"try again in about {delay / 60:.0f} min"
            return "OSM_MIRROR_RATE_LIMITED", f"too many requests from your address, {waited}"
        if reply.status != 200:
            busy = reply.status in BUSY_STATUSES
            self._open(
                mirror.code,
                reason=f"HTTP {reply.status}",
                seconds=self.busy_cooldown_s if busy else None,
                busy=busy,
            )
            return "OSM_MIRROR_REJECTED", f"HTTP {reply.status}"
        try:
            doc = orjson.loads(reply.body)
        except orjson.JSONDecodeError:
            self._board.failure(mirror.code)
            return "OSM_RESPONSE_TRUNCATED", "body is not JSON"
        if not isinstance(doc, dict) or not isinstance(doc.get("elements"), list):
            self._board.failure(mirror.code)
            return "OSM_RESPONSE_TRUNCATED", "no 'elements' list"
        remark = doc.get("remark")
        if remark:
            self._board.failure(mirror.code)
            return "OSM_RESPONSE_ERROR", str(remark)[:200]
        return None


def _cluster_pushed_back(reply: HttpReply) -> bool:
    """Whether the failure says the *cluster* is loaded, and not that one machine is dead.

    A 429, a 5xx and a 200 the query was too heavy for (a ``remark``, a truncated body) are the
    cluster's answer: its other machine is asked after ``attempt_delay_s``. No answer at all, or
    a refusal such as a 403, is that machine's own: its sibling is asked at once.
    """
    if reply.error is not None:
        return False
    return reply.status == 429 or reply.status >= 500 or reply.status == 200


_SLOT_NOW = re.compile(r"(\d+)\s+slots?\s+available\s+now", re.I)
_SLOT_AFTER = re.compile(r"Slot available after:[^,]*,\s*in\s+(-?\d+)\s+seconds?", re.I)


def slot_wait_s(body: str) -> float | None:
    """Seconds until this address may query again, read from an Overpass ``/api/status`` body.

    These servers count queries per internet address and their status page says exactly when the
    next slot frees::

        Rate limit: 4
        4 slots available now.

    or, when they are all taken::

        Slot available after: 2026-09-24T21:36:12Z, in 140 seconds.

    We guessed sixty seconds instead of reading it, and a user whose address had spent its quota
    watched five mirrors refuse and the build give up after a minute (2026-09-24). ``None`` when
    the body says neither, so the caller keeps its own guess.
    """
    if not body:
        return None
    now_free = _SLOT_NOW.search(body)
    if now_free and int(now_free.group(1)) > 0:
        return 0.0
    waits = [int(found) for found in _SLOT_AFTER.findall(body)]
    return float(max(0, min(waits))) if waits else None


def _retry_after(headers: Mapping[str, str]) -> float | None:
    """The delay a server asks for, in seconds, however it writes it.

    RFC 9110 allows a count of seconds or a date, and a server is free to write ``5.5``. Reading
    only a bare integer sent everything else down the path meant for a machine that is down
    (found in review, 2026-09-23).
    """
    raw = (headers.get("retry-after") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    import datetime as dt

    now = dt.datetime.now(when.tzinfo or dt.UTC)
    return max(0.0, (when - now).total_seconds())
