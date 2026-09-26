# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The reader of OpenStreetMap layers in the shape Ortho4XP caches them (``sources/prepared.py``).

OSM 0.6 XML, bzip2 or plain, one file a layer: what a folder of the user's own may hold. The
public library published in the same format (xpconnect) was read here as well while the chain was
written, and its tests went with it (2026-09-25). The folder is tested in ``test_sources_chain.py``.
"""

from __future__ import annotations

import bz2

from orthostudio.model import TileRef
from orthostudio.sources.osm import LAYERS
from orthostudio.sources.prepared import snapshot_from_xml

TILE = TileRef(46, 6)


def osm_xml(*, generator: str = "osmium/1.16.0", meta: str = "") -> bytes:
    """One layer as a file of this shape holds it: OSM 0.6 XML, no ``<meta>`` unless asked for."""
    head = f'<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="{generator}">\n'
    body = (
        (f'  <meta osm_base="{meta}"/>\n' if meta else "")
        + '  <node id="1" lat="46.5" lon="6.5"/>\n'
        + '  <node id="2" lat="46.6" lon="6.6">\n'
        + '    <tag k="natural" v="water"/>\n'
        + "  </node>\n"
        + '  <way id="10">\n'
        + '    <nd ref="1"/>\n    <nd ref="2"/>\n'
        + '    <tag k="waterway" v="riverbank"/>\n'
        + "  </way>\n"
        + '  <relation id="20">\n'
        + '    <member type="way" ref="10" role="outer"/>\n'
        + '    <tag k="type" v="multipolygon"/>\n'
        + "  </relation>\n"
    )
    return (head + body + "</osm>\n").encode()


def test_a_compressed_layer_is_read_whole() -> None:
    snap = snapshot_from_xml(
        bz2.compress(osm_xml()), TILE, "water", mirror="folder", osm_base_default="2026-09-01"
    )
    assert snap.tile == TILE and snap.layer == "water" and snap.mirror == "folder"
    assert snap.selectors == tuple(LAYERS["water"].selectors)
    assert [n.id for n in snap.nodes] == [1, 2]
    assert snap.nodes[1].tags == {"natural": "water"}
    assert snap.ways[0].nodes == (1, 2) and snap.ways[0].tags == {"waterway": "riverbank"}
    assert snap.relations[0].members[0].role == "outer"
    assert snap.osm_base == "2026-09-01"  # the date given, the file carries none
    assert snap.digest and snap.fetched_at


def test_a_plain_document_is_read_too() -> None:
    """Not every file is compressed; the reader looks at the first bytes, not at the name."""
    snap = snapshot_from_xml(osm_xml(meta="2026-09-10T00:00:00Z"), TILE, "water", mirror="m")
    assert snap.osm_base == "2026-09-10T00:00:00Z"  # what the document says wins
    assert snap.generator == "osmium/1.16.0" and len(snap.nodes) == 2
