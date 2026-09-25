"""OpenStreetMap layers in the shape Ortho4XP caches them: OSM 0.6 XML, bzip2, one file a layer.

What a folder of the user's own may hold (``chain.FolderSource``): Ortho4XP keeps the answers of
its queries under ``OSM_data/<cell>/<tile>/<tile>_<layer>.osm.bz2``, and a user who already has
them asked for a build to read them instead of queueing behind a public server (2026-09-23).

Such a file says nothing about itself: no selectors, often no date. So it is read under the
caller's layer, and the road-level layers are never read from it at all
(:data:`ROAD_LEVEL_LAYERS`). The library this project bakes has a format of its own, which does
say, and is read by ``library.py``.

The public library another project publishes in this same format (OrthoForge, served by
xpconnect) was read here too, for the tiles we had verified, until 0.1.16: once the whole planet
was baked here, it covered nothing ours does not (2026-09-25).
"""

from __future__ import annotations

import bz2
import io
from xml.etree import ElementTree as ET

import blake3

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
)

__all__ = ["EMPTY_LAYER_BYTES", "ROAD_LEVEL_LAYERS", "snapshot_from_xml"]

EMPTY_LAYER_BYTES = 200
"""A layer file smaller than this holds no element at all: not an answer, whoever publishes it
(1 559 of their 7 132 tiles hold no road, measured 2026-09-19 and unchanged on 2026-09-23)."""

ROAD_LEVEL_LAYERS = frozenset({"small_roads"})
"""Layers whose question depends on the build's road level.

An OSM XML file says nothing about the question it answers: ``small_roads`` baked for tertiary
roads and ``small_roads`` baked for tracks as well are the same file name, the same format, and
one of them is missing every forest track. Our own format carries its selectors and is checked
against them; XML cannot be, so these layers are not read from an XML source at all (review S2).
"""


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
    carry it: a file cut with osmium writes no ``<meta>``.
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
