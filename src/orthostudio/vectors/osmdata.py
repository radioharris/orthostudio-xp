# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The OSM store of a vector layer: nodes, ways, relations, tags, multipolygons.

Specification: ``docs/specs/vectors-osm-layers.md``. Successor of ``OSM_layer`` and
``OSM_layer.update_dicosm`` in Ortho4XP (``O4_OSM_Utils.py:22-283``), plus the two
geometry helpers that read it (``:587-641`` ``OSM_to_MultiLineString``, ``:643-772``
``OSM_to_MultiPolygon``).

What it reproduces, id for id (spec section 4):

* internal negative ids assigned in reading order, recycled when an element is dropped;
* node deduplication on the exact ``(lon, lat)`` pair;
* the ``input_tags`` / ``target_tags`` filter and the "first catch" set;
* the multipolygon reconstruction, its open-end check and its ring stitching order.

Two sources are accepted: an OrthoStudio XP snapshot (``orthostudio.sources.osm``), which every
layer of a build comes from, and a JOSM ``.osm`` text file, which is what a ``*.patch.osm`` is. A
snapshot is walked in the order Ortho4XP spelled the elements of the same data -- nodes, ways,
relations, each group sorted by OSM id -- so the internal ids, and therefore the structures, are
the ones Ortho4XP built (spec section 2.1).

Nothing here touches the network.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import numpy as np
import orjson
import shapely
import zstandard
from numpy.typing import NDArray
from shapely import geometry, ops

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.sources.osm import LayerSpec, OsmSnapshot
from orthostudio.vectors.geom import wrap_local_x
from orthostudio.vectors.tags import LayerTags, OsmType, layer_tags

__all__ = [
    "COORD_DIGITS",
    "OsmData",
    "PolygonGeometry",
    "SkipHandler",
    "WayGeometry",
    "load",
]

COORD_DIGITS = 7
"""Decimals kept when OSM degrees become tile-local coordinates (``O4_OSM_Utils.py:604-615``)."""

SkipHandler = Callable[[str, dict[str, Any]], None]
"""``on_skip(code, context)``: what Ortho4XP prints at level 2, as a registry code."""

_ROLES = ("outer", "inner")


@dataclass(frozen=True, slots=True)
class WayGeometry:
    """One way as the layer builders want it: local coordinates and the tags that were kept."""

    id: int
    coords: NDArray[np.float64]
    tags: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PolygonGeometry:
    """One polygon and the element it came from (``kind`` is ``"w"`` or ``"r"``)."""

    id: int
    kind: OsmType
    polygon: geometry.Polygon
    tags: Mapping[str, str]


@dataclass
class OsmData:
    """The six dictionaries of ``OSM_layer``, with the same keys and the same contents.

    ``nodes`` maps an internal id to ``(lon, lat)`` in degrees; ``ways`` maps an internal id
    to its list of internal node ids; ``relations`` holds the *reconstructed* rings (lists of
    node ids, closed) per role and ``relations_orig`` the member way ids per role; ``first``
    holds the ids of the directly queried elements per OSM type; ``tags`` the tags that passed
    the filter, per OSM type.
    """

    tile: TileRef | None = None
    nodes: dict[int, tuple[float, float]] = field(default_factory=dict)
    ways: dict[int, list[int]] = field(default_factory=dict)
    relations: dict[int, dict[str, list[list[int]]]] = field(default_factory=dict)
    relations_orig: dict[int, dict[str, list[int]]] = field(default_factory=dict)
    first: dict[OsmType, set[int]] = field(
        default_factory=lambda: {"n": set(), "w": set(), "r": set()}
    )
    tags: dict[OsmType, dict[int, dict[str, str]]] = field(
        default_factory=lambda: {"n": {}, "w": {}, "r": {}}
    )

    # -- private state, the counters and the coordinate index of O4_OSM_Utils.py:26-33 ------
    _by_coord: dict[tuple[float, float], int] = field(default_factory=dict, repr=False)
    _next_node_id: int = field(default=-1, repr=False)
    _next_way_id: int = field(default=-1, repr=False)
    _next_rel_id: int = field(default=-1, repr=False)

    # -- loading ---------------------------------------------------------------------------

    @classmethod
    def load(
        cls,
        source: Path | str | OsmSnapshot,
        *,
        layer: str | LayerSpec | LayerTags | None = None,
        tile: TileRef | None = None,
        on_skip: SkipHandler | None = None,
    ) -> OsmData:
        """Read one source into a new store.

        ``layer`` selects the tag filter (a name, a :class:`LayerSpec` or a
        :class:`LayerTags`); ``None`` keeps every tag and marks every way and relation as a
        first catch, which is what Ortho4XP does for patch files (``O4_Vector_Map.py:660``).
        """
        if tile is None and isinstance(source, OsmSnapshot):
            tile = source.tile
        data = cls(tile=tile)
        data.update(source, layer=layer, on_skip=on_skip)
        return data

    def update(
        self,
        source: Path | str | OsmSnapshot,
        *,
        layer: str | LayerSpec | LayerTags | None = None,
        on_skip: SkipHandler | None = None,
    ) -> OsmData:
        """Add one more source to this store (``update_dicosm``, ``O4_OSM_Utils.py:50``).

        The node deduplication and the id counters carry over between calls, the per-source id
        maps do not: that is how Ortho4XP merges several files into one layer
        (``O4_Vector_Map.py:379-388``).
        """
        filters = _as_tags(layer)
        builder = _Builder(self, filters, on_skip)
        if isinstance(source, OsmSnapshot):
            # Same envelope as the file branch: a malformed snapshot handed over *in memory*
            # (what ``layer_store`` does through ``SnapshotStore.load``) used to raise a bare
            # Python exception instead of a registry code (review 5).
            with _unreadable({"snapshot": str(getattr(source, "tile", "") or "?")}):
                _feed_snapshot(builder, source)
            return self
        with _unreadable({"path": str(source)}):
            path = Path(source)
        if path.suffix == ".zst":
            with _unreadable({"path": str(path)}):
                _feed_snapshot(builder, _snapshot_from_file(path))
        else:
            _feed_file(builder, path)
        return self

    # -- counts ----------------------------------------------------------------------------

    @property
    def counts(self) -> dict[str, int]:
        """``{"nodes", "ways", "relations", "first_ways", "first_rels"}``."""
        return {
            "nodes": len(self.nodes),
            "ways": len(self.ways),
            "relations": len(self.relations),
            "first_ways": len(self.first["w"]),
            "first_rels": len(self.first["r"]),
        }

    # -- geometry --------------------------------------------------------------------------

    def node_coords(self, ids: Sequence[int]) -> NDArray[np.float64]:
        """``(n, 2)`` local coordinates of these nodes, rounded to 7 decimals.

        ``x = lon - tile.lon``, ``y = lat - tile.lat`` (``O4_OSM_Utils.py:604-615``), the
        abscissa folded by :func:`orthostudio.vectors.geom.wrap_local_x` so that a way crossing the
        antimeridian is not mirrored into the tile. Without a tile the raw degrees are
        returned unrounded.
        """
        raw = np.array([self.nodes[i] for i in ids], dtype=np.float64).reshape(-1, 2)
        if self.tile is None:
            return raw
        origin = np.array([[self.tile.lon, self.tile.lat]], dtype=np.float64)
        local = raw - origin
        local[:, 0] = wrap_local_x(local[:, 0])
        return np.round(local, COORD_DIGITS)

    def ways_with(
        self,
        tags: Iterable[str | tuple[str, str]] | None = None,
        *,
        exclude: Iterable[str] = (),
        on_skip: SkipHandler | None = None,
    ) -> list[WayGeometry]:
        """The selected ways as coordinate arrays (``OSM_to_MultiLineString:587-641``).

        ``tags is None`` selects ``first["w"]``, exactly what Ortho4XP iterates; a non-empty
        ``tags`` narrows that selection to the ways carrying one of those keys or
        ``(key, value)`` pairs. ``exclude`` drops the ways carrying one of its keys
        (``tags_for_exclusion``, ``:596-603``). A way of fewer than two points is dropped, as
        Ortho4XP drops it through the ``except`` around ``LineString`` (``:616-624``).
        """
        excluded = frozenset(exclude)
        out: list[WayGeometry] = []
        for way_id in self._select("w", tags):
            way_tags = self.tags["w"].get(way_id, {})
            if excluded and not excluded.isdisjoint(way_tags):
                continue
            coords = self.node_coords(self.ways[way_id])
            if len(coords) < 2:
                if on_skip is not None:
                    on_skip("OSM_WAY_INVALID", {"osm_id": way_id, "reason": "fewer than 2 nodes"})
                continue
            out.append(WayGeometry(way_id, coords, way_tags))
        return out

    def multipolygons_with(
        self,
        tags: Iterable[str | tuple[str, str]] | None = None,
        *,
        exclude: Iterable[str] = (),
        on_skip: SkipHandler | None = None,
    ) -> list[PolygonGeometry]:
        """The selected ways and relations as polygons (``OSM_to_MultiPolygon:643-772``).

        Ways first, in the order ``first["w"]`` iterates, then relations: a closed way becomes
        a polygon, a relation becomes ``union(outer) - union(inner)`` exploded into polygons.
        Empty and invalid polygons are dropped and reported through ``on_skip``.
        """
        excluded = frozenset(exclude)
        out: list[PolygonGeometry] = []
        for way_id in self._select("w", tags):
            way_tags = self.tags["w"].get(way_id, {})
            if excluded and not excluded.isdisjoint(way_tags):
                continue
            nodes = self.ways[way_id]
            if not nodes or nodes[0] != nodes[-1]:
                _skip(on_skip, "OSM_WAY_NOT_CLOSED", {"osm_id": way_id})
                continue
            polygon = _polygon(self.node_coords(nodes))
            if polygon is None or not polygon.area:
                continue
            if not polygon.is_valid:
                _skip(on_skip, "OSM_WAY_INVALID", {"osm_id": way_id})
                continue
            out.append(PolygonGeometry(way_id, "w", polygon, way_tags))
        for rel_id in self._select("r", tags):
            rel_tags = self.tags["r"].get(rel_id, {})
            if excluded and not excluded.isdisjoint(rel_tags):
                continue
            out.extend(self._relation_polygons(rel_id, rel_tags, on_skip))
        return out

    def _relation_polygons(
        self, rel_id: int, rel_tags: Mapping[str, str], on_skip: SkipHandler | None
    ) -> list[PolygonGeometry]:
        """``union(outer).difference(union(inner))``, exploded (``:690-756``)."""
        rings = self.relations[rel_id]
        try:
            outer = ops.unary_union(
                [p for p in map(_polygon, map(self.node_coords, rings["outer"])) if _valid(p)]
            )
            inner = ops.unary_union(
                [p for p in map(_polygon, map(self.node_coords, rings["inner"])) if _valid(p)]
            )
            multipol = outer.difference(inner)
        except (ValueError, shapely.errors.ShapelyError) as exc:
            _skip(on_skip, "OSM_RELATION_INVALID", {"osm_id": rel_id, "reason": str(exc)})
            return []
        out: list[PolygonGeometry] = []
        for pol in getattr(multipol, "geoms", [multipol]):
            if not isinstance(pol, geometry.Polygon) or not pol.area:
                continue
            if not pol.is_valid:
                _skip(on_skip, "OSM_RELATION_INVALID", {"osm_id": rel_id})
                continue
            out.append(PolygonGeometry(rel_id, "r", pol, rel_tags))
        return out

    def _select(self, kind: OsmType, tags: Iterable[str | tuple[str, str]] | None) -> Iterator[int]:
        """The first-catch ids of ``kind``, narrowed to those carrying one of ``tags``."""
        wanted = None if tags is None else frozenset(tags)
        for osm_id in self.first[kind]:
            if kind == "w" and osm_id not in self.ways:
                continue
            if kind == "r" and osm_id not in self.relations:
                continue
            if wanted is None:
                yield osm_id
                continue
            element_tags = self.tags[kind].get(osm_id, {})
            if any(k in wanted or (k, v) in wanted for k, v in element_tags.items()):
                yield osm_id


def load(
    source: Path | str | OsmSnapshot,
    *,
    layer: str | LayerSpec | LayerTags | None = None,
    tile: TileRef | None = None,
    on_skip: SkipHandler | None = None,
) -> OsmData:
    """Module-level alias of :meth:`OsmData.load`."""
    return OsmData.load(source, layer=layer, tile=tile, on_skip=on_skip)


# -- the reader ------------------------------------------------------------------------------


def _as_tags(layer: str | LayerSpec | LayerTags | None) -> LayerTags | None:
    if layer is None or isinstance(layer, LayerTags):
        return layer
    return layer_tags(layer)


class _Builder:
    """The state machine of ``update_dicosm``, driven by either source (spec section 4)."""

    __slots__ = ("_cur", "_kind", "_node_ids", "_on_skip", "_open", "_tags", "_way_ids", "d")

    def __init__(self, data: OsmData, tags: LayerTags | None, on_skip: SkipHandler | None):
        self.d = data
        self._tags = tags
        self._on_skip = on_skip
        # Per-source maps from the source's own id (kept as text, as Ortho4XP does) to the
        # internal id (O4_OSM_Utils.py:58-59).
        self._node_ids: dict[str, int] = {}
        self._way_ids: dict[str, int] = {}
        self._kind: OsmType = "n"
        self._cur = 0
        self._open: dict[str, dict[int, list[int]]] = {}

    # R1 ----------------------------------------------------------------------------------
    def node(self, osm_id: str, lon: float, lat: float) -> None:
        """``:86-104``: deduplicate on the exact ``(lon, lat)`` pair, renumber, index."""
        data = self.d
        key = (lon, lat)
        known = data._by_coord.get(key)
        if known is not None:
            internal = known
        else:
            internal = data._next_node_id
            data._by_coord[key] = internal
            data.nodes[internal] = key
            data._next_node_id -= 1
        self._node_ids[osm_id] = internal
        self._kind = "n"
        self._cur = internal

    # R2 ----------------------------------------------------------------------------------
    def open_way(self, osm_id: str) -> None:
        """``:105-114``: a way takes the next id; with no filter it is a first catch."""
        data = self.d
        internal = data._next_way_id
        data._next_way_id -= 1
        self._way_ids[osm_id] = internal
        data.ways[internal] = []
        if self._tags is None:
            data.first["w"].add(internal)
        self._kind = "w"
        self._cur = internal

    def node_ref(self, ref: str) -> None:
        """``:115-116``: a way references a node by the source id of this source."""
        self.d.ways[self._cur].append(self._node_ids[ref])

    def close_way(self) -> None:
        """``:182-189``: an empty way is dropped and **gives its id back**."""
        data = self.d
        way_id = self._cur
        if data.ways.get(way_id):
            return
        del data.ways[way_id]
        data._next_way_id += 1
        data.first["w"].discard(way_id)
        data.tags["w"].pop(way_id, None)

    # R5 ----------------------------------------------------------------------------------
    def open_relation(self, osm_id: str) -> None:
        """``:117-127``: a relation takes the next id and opens its open-end tables."""
        data = self.d
        internal = data._next_rel_id
        data._next_rel_id -= 1
        data.relations[internal] = {"outer": [], "inner": []}
        data.relations_orig[internal] = {"outer": [], "inner": []}
        self._open = {"outer": {}, "inner": {}}
        if self._tags is None:
            data.first["r"].add(internal)
        self._kind = "r"
        self._cur = internal

    def member(self, member_type: str, ref: str, role: str) -> None:
        """``:128-162``: keep the ``outer`` / ``inner`` ways, index the open ends."""
        data = self.d
        if member_type != "way" or role not in _ROLES:
            if member_type != "node":
                _skip(
                    self._on_skip,
                    "OSM_RELATION_INVALID",
                    {"osm_id": self._cur, "reason": f"member type {member_type!r} role {role!r}"},
                )
            return
        way_id = self._way_ids.get(ref)
        if way_id is None or way_id not in data.ways:
            return
        nodes = data.ways[way_id]
        data.relations_orig[self._cur][role].append(way_id)
        if nodes[0] == nodes[-1]:
            data.relations[self._cur][role].append(nodes)
            return
        ends = self._open[role]
        for end in (nodes[0], nodes[-1]):
            ends.setdefault(end, []).append(way_id)

    # R6, R7, R8, R9 ------------------------------------------------------------------------
    def close_relation(self) -> None:
        """``:190-261``: check the open ends, stitch the rings, drop what Ortho4XP drops."""
        data = self.d
        rel_id = self._cur
        for role in _ROLES:
            for ways in self._open[role].values():
                if len(ways) != 2:
                    _skip(self._on_skip, "OSM_RELATION_INVALID", {"osm_id": rel_id})
                    self._drop_relation(rel_id)
                    return
        for role in _ROLES:
            ends = self._open[role]
            while ends:
                data.relations[rel_id][role].append(_stitch(data.ways, ends))
        if self._tags is None:
            # R8 (:244-252): only without a tag filter are the member ways taken out of the
            # first-catch set, so a custom water file does not draw its rings twice.
            orig = data.relations_orig[rel_id]
            for way_id in orig["outer"] + orig["inner"]:
                data.first["w"].discard(way_id)
        if not data.relations[rel_id]["outer"]:
            self._drop_relation(rel_id)

    def _drop_relation(self, rel_id: int) -> None:
        data = self.d
        del data.relations[rel_id]
        del data.relations_orig[rel_id]
        data._next_rel_id += 1
        data.first["r"].discard(rel_id)
        data.tags["r"].pop(rel_id, None)

    # R3, R4 ----------------------------------------------------------------------------
    def tag(self, key: str, value: str) -> None:
        """``:163-181``: store the tag when the filter keeps it, then mark the first catch."""
        data = self.d
        kind = self._kind
        filters = self._tags
        if filters is not None and not filters.keeps(kind, key, value):
            return
        store = data.tags[kind]
        known = store.get(self._cur)
        if known is None:
            store[self._cur] = {key: value}
        else:
            known[key] = value
        if filters is not None and filters.is_first(kind, key, value):
            data.first[kind].add(self._cur)


def _stitch(ways: Mapping[int, list[int]], ends: dict[int, list[int]]) -> list[int]:
    """One ring of node ids from the open ends of a role (``O4_OSM_Utils.py:217-243``).

    Starts at the first key of ``ends`` in insertion order and walks from end to end, always
    taking the other way of the pair; each way contributes its nodes minus its last one,
    reversed when it is met backwards. The starting node closes the ring.
    """
    start_end = next(iter(ends))
    way_id = ends[start_end][0]
    first_node = ways[way_id][0]
    end_node = ways[way_id][-1]
    ring: list[int] = list(ways[way_id][:-1])
    while end_node != first_node:
        pair = ends[end_node]
        way_id = pair[1] if pair[0] == way_id else pair[0]
        previous = end_node
        nodes = ways[way_id]
        if nodes[0] == previous:
            end_node = nodes[-1]
            ring.extend(nodes[:-1])
        else:
            end_node = nodes[0]
            ring.extend(nodes[-1:0:-1])
        del ends[previous]
    ring.append(first_node)
    del ends[first_node]
    return ring


@contextlib.contextmanager
def _unreadable(context: dict[str, str]) -> Iterator[None]:
    """Turn any reading accident into ``OSM_CACHE_UNREADABLE``, ``OsxpError`` untouched.

    The list of exception types is the one a hand-written scanner over untrusted data can
    produce: a short read, a missing field, a coordinate that is not a number, an object of
    the wrong shape.
    """
    try:
        yield
    except OsxpError:
        raise
    except (
        OSError,
        EOFError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
    ) as exc:
        raise OsxpError(
            "OSM_CACHE_UNREADABLE",
            context={**context, "reason": f"{type(exc).__name__}: {exc}"},
            remedy="Delete the file and rebuild, or run with --osm-refresh to fetch the "
            "layer again with the OrthoStudio XP client.",
        ) from exc


def _feed_file(builder: _Builder, path: Path) -> None:
    """Read a ``.osm`` text file (a patch) exactly as ``update_dicosm`` reads it (UTF-8)."""
    with _unreadable({"path": str(path)}), path.open("r", encoding="utf-8") as handle:
        _scan(builder, handle)


def _scan(builder: _Builder, handle: IO[str]) -> None:
    """``:79-283``: the line splitter of Ortho4XP, field by field.

    The separator is the quote character of the ``<osm …>`` line, and fields are picked by
    position: this is not an XML parser, and reproducing it is the point.
    """
    first_line = handle.readline()
    if "<osm " not in first_line:
        first_line = handle.readline()
    sep = "'" if "'" in first_line else '"'
    closed = False
    for line in handle:
        items = line.split(sep)
        head = items[0]
        if "<node id=" in head:
            lon, lat = _node_coords(items)
            builder.node(items[1], lon, lat)
        elif "<way id=" in head:
            builder.open_way(items[1])
        elif "<nd ref=" in head:
            builder.node_ref(items[1])
        elif "<relation id=" in head:
            builder.open_relation(items[1])
        elif "<member type=" in head:
            builder.member(items[1], items[3], items[5] if len(items) > 5 else "")
        elif "<tag k=" in head:
            builder.tag(items[1], items[3])
        elif "</way" in head:
            builder.close_way()
        elif "</relation>" in head:
            builder.close_relation()
        elif "</osm>" in head:
            closed = True
    if not closed:
        raise ValueError("no closing </osm> tag: the file is truncated")


def _node_coords(items: list[str]) -> tuple[float, float]:
    """``:89-93``: find ``lat=`` and ``lon=`` among the fields of a node line."""
    if len(items) > 5 and items[2] == " lat=" and items[4] == " lon=":
        return float(items[5]), float(items[3])
    lon = lat = None
    for j, item in enumerate(items):
        if item == " lat=":
            lat = float(items[j + 1])
        elif item == " lon=":
            lon = float(items[j + 1])
    if lon is None or lat is None:
        raise ValueError(f"node line without lat/lon: {items[:2]!r}")
    return lon, lat


def _feed_snapshot(builder: _Builder, snapshot: OsmSnapshot) -> None:
    """Walk a snapshot in the order Ortho4XP's cache file spelled it (spec section 2.1).

    All nodes, then all ways, then all relations, each group sorted by OSM id, ways with an
    unknown or empty node list skipped (``docs/specs/osm-source.md`` section 6), so the internal
    ids are the ones Ortho4XP assigned to the same data.
    """
    known: set[int] = set()
    for node in sorted(snapshot.nodes, key=lambda n: n.id):
        known.add(node.id)
        # The cache file carries ``"{:.7f}"`` coordinates (``O4_OSM_Utils.py:302-303``) and the
        # deduplication key is the exact pair of floats, so the snapshot is rounded the same
        # way: both sources then dedupe identically whatever the mirror sent.
        builder.node(
            str(node.id), round(float(node.lon), COORD_DIGITS), round(float(node.lat), COORD_DIGITS)
        )
        for key, value in node.tags.items():
            builder.tag(key, value)
    for way in sorted(snapshot.ways, key=lambda w: w.id):
        if not way.nodes or any(n not in known for n in way.nodes):
            continue
        builder.open_way(str(way.id))
        for ref in way.nodes:
            builder.node_ref(str(ref))
        for key, value in way.tags.items():
            builder.tag(key, value)
        builder.close_way()
    for relation in sorted(snapshot.relations, key=lambda r: r.id):
        builder.open_relation(str(relation.id))
        for member in relation.members:
            if member.type != "way" or member.role not in _ROLES:
                continue
            builder.member(member.type, str(member.ref), member.role)
        for key, value in relation.tags.items():
            builder.tag(key, value)
        builder.close_relation()


def _snapshot_from_file(path: Path) -> OsmSnapshot:
    """One ``.osm.json.zst`` snapshot, decompressed (``SnapshotStore.load`` without the store)."""
    try:
        raw = zstandard.ZstdDecompressor().decompress(path.read_bytes())
        return OsmSnapshot.from_json(raw)
    except (OSError, ValueError, KeyError, zstandard.ZstdError, orjson.JSONDecodeError) as exc:
        raise OsxpError(
            "OSM_CACHE_UNREADABLE", context={"path": str(path), "reason": str(exc)}
        ) from exc


# -- small helpers ---------------------------------------------------------------------------


def _polygon(coords: NDArray[np.float64]) -> geometry.Polygon | None:
    """``Polygon(coords)`` or ``None`` when shapely refuses it (``:673-689``)."""
    if len(coords) < 4:
        return None
    try:
        return geometry.Polygon(coords)
    except (ValueError, shapely.errors.ShapelyError):
        return None


def _valid(polygon: geometry.Polygon | None) -> bool:
    """``:707-710``: a ring that is not a valid polygon never enters the union."""
    return polygon is not None and polygon.is_valid


def _skip(on_skip: SkipHandler | None, code: str, context: dict[str, Any]) -> None:
    if on_skip is not None:
        on_skip(code, context)
