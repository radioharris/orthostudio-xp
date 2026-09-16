"""The typed OSM store: the reader rules R1-R11 and the geometry helpers.

Specification: ``docs/specs/vectors-osm-layers.md``. Every test names the rule of the spec
and the lines of ``O4_OSM_Utils.py`` it reproduces.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from shapely import geometry

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.sources.osm import (
    LAYERS,
    OsmNode,
    OsmSnapshot,
    OsmWay,
)
from orthostudio.vectors.osmdata import OsmData
from orthostudio.vectors.tags import (
    LAKE_NAME_TAG,
    LAYER_TAGS,
    ROAD_EXCLUSION_TAGS,
    layer_tags,
    tags_from_selectors,
)

TILE = TileRef(43, 5)


# -- building test files -----------------------------------------------------------------


def osm_text(body: str, *, closed: bool = True) -> str:
    """An Ortho4XP-shaped document around ``body`` (the grammar of ``osm-source.md`` section 6)."""
    head = '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="Ortho4XP">\n'
    return head + body + ("</osm>" if closed else "")


def node(osm_id: int, lat: float, lon: float, **tags: str) -> str:
    head = f'  <node id="{osm_id}" lat="{lat:.7f}" lon="{lon:.7f}" version="1"'
    if not tags:
        return head + "/>\n"
    inner = "".join(f'    <tag k="{k}" v="{v}"/>\n' for k, v in tags.items())
    return head + ">\n" + inner + "  </node>\n"


def way(osm_id: int, refs: list[int], **tags: str) -> str:
    out = f'  <way id="{osm_id}" version="1">\n'
    out += "".join(f'    <nd ref="{r}"/>\n' for r in refs)
    out += "".join(f'    <tag k="{k}" v="{v}"/>\n' for k, v in tags.items())
    return out + "  </way>\n"


def relation(osm_id: int, members: list[tuple[str, int, str]], **tags: str) -> str:
    out = f'  <relation id="{osm_id}" version="1">\n'
    out += "".join(f'    <member type="{t}" ref="{r}" role="{role}"/>\n' for t, r, role in members)
    out += "".join(f'    <tag k="{k}" v="{v}"/>\n' for k, v in tags.items())
    return out + "  </relation>\n"


def write_osm(tmp_path: Path, body: str, *, name: str = "x.osm", closed: bool = True) -> Path:
    path = tmp_path / name
    path.write_text(osm_text(body, closed=closed), encoding="utf-8")
    return path


# -- tags.py (spec section 3) --------------------------------------------------------------


def test_tag_sets_of_the_water_layer_leave_nodes_empty() -> None:
    """``tags_of_interest`` is appended per selector type (``O4_OSM_Utils.py:409-419``)."""
    tags = LAYER_TAGS["water"]
    assert tags.input_tags["r"] == (("natural", "water"), ("waterway", "riverbank"))
    # the interest loop runs after *each* selector, so ("name", "") lands between the two
    assert tags.target_tags["r"] == (
        ("natural", "water"),
        ("name", ""),
        ("waterway", "riverbank"),
    )
    assert ("name", "") in tags.target_tags["w"]
    assert tags.target_tags["n"] == ()
    assert not tags.keeps("n", "name", "Etang de Berre")
    assert tags.keeps("w", "name", "Etang de Berre")
    assert not tags.is_first("w", "name", "Etang de Berre")
    assert tags.is_first("w", "natural", "water")


def test_tag_sets_of_the_airports_layer_keep_everything() -> None:
    """``tags_of_interest = ["all"]`` (``O4_Vector_Map.py:186``)."""
    tags = LAYER_TAGS["airports"]
    for kind in ("n", "w", "r"):
        assert ("all", "") in tags.target_tags[kind]
        assert tags.keeps(kind, "whatever", "value")
        assert tags.is_first(kind, "aeroway", "runway")
        assert not tags.is_first(kind, "whatever", "value")


def test_tag_sets_of_the_road_layers() -> None:
    """Key-only selectors, valued selectors, and the two exclusion tags (``:261-275``)."""
    tags = LAYER_TAGS["big_roads"]
    assert ("highway", "motorway") in tags.input_tags["w"]
    assert ("railway", "narrow_gauge") in tags.input_tags["w"]
    assert tags.keeps("w", "bridge", "yes")
    assert not tags.is_first("w", "bridge", "yes")
    assert tags.input_tags["n"] == () and tags.input_tags["r"] == ()
    assert set(ROAD_EXCLUSION_TAGS) == {"bridge", "tunnel"}
    assert LAKE_NAME_TAG == "name"


def test_tags_from_selectors_handles_a_key_only_selector() -> None:
    """``items[3]`` raises for ``way["aeroway"]``; Ortho4XP falls back to ``(key, "")``."""
    inp, tgt = tags_from_selectors(('way["aeroway"]',), ("all",))
    assert inp["w"] == (("aeroway", ""),)
    assert tgt["w"] == (("aeroway", ""), ("all", ""))


def test_layer_tags_accepts_a_spec_for_the_road_level() -> None:
    spec = LAYERS["small_roads"]
    assert layer_tags(spec).input_tags["w"] == layer_tags("small_roads").input_tags["w"]


# -- R1: nodes (O4_OSM_Utils.py:86-104) ----------------------------------------------------


def test_nodes_are_renumbered_negatively_in_reading_order(tmp_path: Path) -> None:
    path = write_osm(tmp_path, node(10, 43.5, 5.5) + node(11, 43.25, 5.25))
    data = OsmData.load(path, tile=TILE)
    assert list(data.nodes) == [-1, -2]
    assert data.nodes[-1] == (5.5, 43.5)
    assert data.nodes[-2] == (5.25, 43.25)


def test_two_nodes_at_the_same_coordinates_become_one(tmp_path: Path) -> None:
    """The deduplication key is the ``(lon, lat)`` pair, not the OSM id (``:94-98``)."""
    body = node(10, 43.5, 5.5) + node(11, 43.5, 5.5) + way(1, [10, 11, 10])
    data = OsmData.load(write_osm(tmp_path, body), tile=TILE)
    assert len(data.nodes) == 1
    assert data.ways[-1] == [-1, -1, -1]


# -- R2: ways (O4_OSM_Utils.py:105-114, 182-189) -------------------------------------------


def test_an_empty_way_is_dropped_and_gives_its_id_back(tmp_path: Path) -> None:
    body = node(10, 43.5, 5.5) + node(11, 43.6, 5.6) + way(1, []) + way(2, [10, 11])
    data = OsmData.load(write_osm(tmp_path, body), tile=TILE)
    assert list(data.ways) == [-1]
    assert data.ways[-1] == [-1, -2]
    assert data.first["w"] == {-1}


def test_an_empty_way_loses_its_tags_and_its_first_catch(tmp_path: Path) -> None:
    body = node(10, 43.5, 5.5) + way(1, [], natural="water")
    data = OsmData.load(write_osm(tmp_path, body), layer="water", tile=TILE)
    assert data.ways == {} and data.tags["w"] == {} and data.first["w"] == set()


# -- R3, R4: the tag filter (O4_OSM_Utils.py:163-181) ---------------------------------------


def test_without_a_filter_every_way_is_a_first_catch(tmp_path: Path) -> None:
    """What Ortho4XP does for custom and patch files (``O4_Vector_Map.py:371, 509, 660``)."""
    body = node(10, 43.5, 5.5) + node(11, 43.6, 5.6) + way(1, [10, 11], cst_alt_abs="12")
    data = OsmData.load(write_osm(tmp_path, body), tile=TILE)
    assert data.first["w"] == {-1}
    assert data.tags["w"][-1] == {"cst_alt_abs": "12"}


def test_a_child_way_without_the_query_tag_is_not_a_first_catch(tmp_path: Path) -> None:
    body = (
        node(10, 43.5, 5.5)
        + node(11, 43.6, 5.6)
        + way(1, [10, 11], natural="water", name="Lake")
        + way(2, [10, 11], name="Pulled by the recursion")
    )
    data = OsmData.load(write_osm(tmp_path, body), layer="water", tile=TILE)
    assert data.first["w"] == {-1}
    assert data.tags["w"][-1] == {"natural": "water", "name": "Lake"}
    assert data.tags["w"][-2] == {"name": "Pulled by the recursion"}


def test_a_tag_outside_target_tags_is_not_stored(tmp_path: Path) -> None:
    body = (
        node(10, 43.5, 5.5) + node(11, 43.6, 5.6) + way(1, [10, 11], highway="motorway", lanes="3")
    )
    data = OsmData.load(write_osm(tmp_path, body), layer="big_roads", tile=TILE)
    assert data.tags["w"][-1] == {"highway": "motorway"}


def test_tag_values_are_stored_raw(tmp_path: Path) -> None:
    """The Ortho4XP reader never unescapes (``osm-source.md`` 6.5); neither does OrthoStudio XP."""
    body = node(10, 43.5, 5.5, name="A&amp;B&#x0a;C")
    data = OsmData.load(write_osm(tmp_path, body), tile=TILE)
    assert data.tags["n"][-1]["name"] == "A&amp;B&#x0a;C"


# -- R5 to R9: relations (O4_OSM_Utils.py:128-261) ------------------------------------------


def _square(offset: int, lat: float, lon: float) -> str:
    """Four nodes of a unit square, ids ``offset..offset+3``."""
    return (
        node(offset, lat, lon)
        + node(offset + 1, lat, lon + 0.1)
        + node(offset + 2, lat + 0.1, lon + 0.1)
        + node(offset + 3, lat + 0.1, lon)
    )


def test_a_closed_member_way_is_taken_as_is(tmp_path: Path) -> None:
    body = (
        _square(10, 43.2, 5.2)
        + way(1, [10, 11, 12, 13, 10])
        + relation(1, [("way", 1, "outer")], natural="water", type="multipolygon")
    )
    data = OsmData.load(write_osm(tmp_path, body), layer="water", tile=TILE)
    assert data.relations[-1]["outer"] == [[-1, -2, -3, -4, -1]]
    assert data.relations_orig[-1] == {"outer": [-1], "inner": []}
    assert data.first["r"] == {-1}


def test_two_open_ways_are_stitched_into_one_ring(tmp_path: Path) -> None:
    """R7 (``:216-243``): the second way is walked backwards and closes the ring."""
    body = (
        _square(10, 43.2, 5.2)
        + way(1, [10, 11, 12])
        + way(2, [10, 13, 12])
        + relation(1, [("way", 1, "outer"), ("way", 2, "outer")], natural="water")
    )
    data = OsmData.load(write_osm(tmp_path, body), layer="water", tile=TILE)
    assert data.relations[-1]["outer"] == [[-1, -2, -3, -4, -1]]


def test_an_unpaired_open_end_drops_the_whole_relation(tmp_path: Path) -> None:
    """R6 (``:190-215``): the id is given back, so the next relation reuses it."""
    skipped: list[tuple[str, dict]] = []
    body = (
        _square(10, 43.2, 5.2)
        + way(1, [10, 11, 12])
        + way(2, [10, 11, 12, 13, 10])
        + relation(1, [("way", 1, "outer")], natural="water")
        + relation(2, [("way", 2, "outer")], natural="water")
    )
    data = OsmData.load(
        write_osm(tmp_path, body),
        layer="water",
        tile=TILE,
        on_skip=lambda code, ctx: skipped.append((code, ctx)),
    )
    assert list(data.relations) == [-1]
    assert data.relations[-1]["outer"] == [[-1, -2, -3, -4, -1]]
    assert data.first["r"] == {-1}
    assert [code for code, _ in skipped] == ["OSM_RELATION_INVALID"]


def test_a_relation_without_an_outer_ring_is_dropped(tmp_path: Path) -> None:
    """R9 (``:253-260``)."""
    body = (
        _square(10, 43.2, 5.2)
        + way(1, [10, 11, 12, 13, 10])
        + relation(1, [("way", 1, "inner")], natural="water")
    )
    data = OsmData.load(write_osm(tmp_path, body), layer="water", tile=TILE)
    assert data.relations == {} and data.relations_orig == {} and data.first["r"] == set()


def test_members_that_are_not_outer_or_inner_ways_are_ignored(tmp_path: Path) -> None:
    """R5 (``:128-146``): a node member silently, anything else with a report."""
    skipped: list[str] = []
    body = (
        _square(10, 43.2, 5.2)
        + way(1, [10, 11, 12, 13, 10])
        + relation(
            1,
            [
                ("way", 1, "outer"),
                ("node", 10, "label"),
                ("way", 1, "subarea"),
                ("way", 99, "outer"),
            ],
            natural="water",
        )
    )
    data = OsmData.load(
        write_osm(tmp_path, body),
        layer="water",
        tile=TILE,
        on_skip=lambda code, ctx: skipped.append(code),
    )
    assert data.relations_orig[-1]["outer"] == [-1]
    assert skipped == ["OSM_RELATION_INVALID"]


def test_member_ways_leave_the_first_set_only_without_a_filter(tmp_path: Path) -> None:
    """R8 (``:244-252``): the asymmetry of Ortho4XP, reproduced on purpose."""
    body = (
        _square(10, 43.2, 5.2)
        + way(1, [10, 11, 12, 13, 10], natural="water")
        + relation(1, [("way", 1, "outer")], natural="water")
    )
    path = write_osm(tmp_path, body)
    assert OsmData.load(path, layer="water", tile=TILE).first["w"] == {-1}
    assert OsmData.load(path, tile=TILE).first["w"] == set()


# -- R10: a truncated file -----------------------------------------------------------------


def test_a_file_without_the_closing_tag_is_an_error(tmp_path: Path) -> None:
    path = write_osm(tmp_path, node(10, 43.5, 5.5), closed=False)
    with pytest.raises(OsxpError) as excinfo:
        OsmData.load(path, tile=TILE)
    assert excinfo.value.code == "OSM_CACHE_UNREADABLE"


# -- several sources in one store ----------------------------------------------------------


def test_update_merges_a_second_file_and_keeps_the_counters(tmp_path: Path) -> None:
    """``O4_Vector_Map.py:379-388``: several files merged into one layer."""
    first = write_osm(
        tmp_path, node(10, 43.5, 5.5) + node(11, 43.6, 5.6) + way(1, [10, 11]), name="a.osm"
    )
    second = write_osm(
        tmp_path, node(1, 43.5, 5.5) + node(2, 43.7, 5.7) + way(9, [1, 2]), name="b.osm"
    )
    data = OsmData.load(first, tile=TILE)
    data.update(second)
    assert len(data.nodes) == 3
    assert data.ways == {-1: [-1, -2], -2: [-1, -3]}
    assert data.first["w"] == {-1, -2}


# -- the snapshot source (spec section 2.1) -------------------------------------------------


def _snapshot(nodes, ways, relations, layer: str = "water") -> OsmSnapshot:
    return OsmSnapshot(
        tile=TILE,
        layer=layer,
        selectors=LAYERS[layer].selectors,
        query="",
        mirror="test",
        fetched_at="",
        generator="",
        osm_base="",
        nodes=tuple(nodes),
        ways=tuple(ways),
        relations=tuple(relations),
        digest="0" * 64,
    )


def test_a_snapshot_way_with_an_unknown_node_is_skipped() -> None:
    """The rule the Ortho4XP cache writer already applies (``osm-source.md`` 6.4)."""
    snapshot = _snapshot([OsmNode(1, 43.1, 5.1)], [OsmWay(2, (1, 99), {"natural": "water"})], [])
    assert OsmData.load(snapshot, layer="water").ways == {}


# -- geometry (spec section 5) --------------------------------------------------------------


def test_way_coordinates_are_tile_local_and_rounded_to_seven_decimals(tmp_path: Path) -> None:
    body = (
        node(10, 43.123456789, 5.123456789)
        + node(11, 43.5, 5.5)
        + way(1, [10, 11], highway="motorway")
    )
    data = OsmData.load(write_osm(tmp_path, body), layer="big_roads", tile=TILE)
    (geom,) = data.ways_with()
    assert geom.coords.shape == (2, 2)
    np.testing.assert_allclose(geom.coords[0], [0.1234568, 0.1234568])
    np.testing.assert_allclose(geom.coords[1], [0.5, 0.5])
    assert geom.tags == {"highway": "motorway"}


def test_ways_with_excludes_bridges_and_tunnels(tmp_path: Path) -> None:
    body = (
        node(10, 43.5, 5.5)
        + node(11, 43.6, 5.6)
        + way(1, [10, 11], highway="motorway")
        + way(2, [10, 11], highway="trunk", bridge="yes")
    )
    data = OsmData.load(write_osm(tmp_path, body), layer="big_roads", tile=TILE)
    kept = data.ways_with(exclude=ROAD_EXCLUSION_TAGS)
    assert [g.tags["highway"] for g in kept] == ["motorway"]
    assert len(data.ways_with()) == 2


def test_ways_with_narrows_the_selection_to_the_given_tags(tmp_path: Path) -> None:
    body = (
        node(10, 43.5, 5.5)
        + node(11, 43.6, 5.6)
        + way(1, [10, 11], aeroway="runway")
        + way(2, [10, 11], aeroway="taxiway")
    )
    data = OsmData.load(write_osm(tmp_path, body), layer="airports", tile=TILE)
    assert [g.id for g in data.ways_with([("aeroway", "runway")])] == [-1]
    assert len(data.ways_with(["aeroway"])) == 2


def test_a_way_of_one_point_is_dropped(tmp_path: Path) -> None:
    body = node(10, 43.5, 5.5) + way(1, [10], highway="motorway")
    data = OsmData.load(write_osm(tmp_path, body), layer="big_roads", tile=TILE)
    assert data.ways_with() == []


def test_multipolygons_from_a_closed_way(tmp_path: Path) -> None:
    body = _square(10, 43.2, 5.2) + way(1, [10, 11, 12, 13, 10], natural="water")
    data = OsmData.load(write_osm(tmp_path, body), layer="water", tile=TILE)
    (poly,) = data.multipolygons_with()
    assert poly.kind == "w"
    assert poly.polygon.equals(geometry.Polygon([(0.2, 0.2), (0.3, 0.2), (0.3, 0.3), (0.2, 0.3)]))


def test_an_open_way_is_reported_and_skipped(tmp_path: Path) -> None:
    skipped: list[str] = []
    body = _square(10, 43.2, 5.2) + way(1, [10, 11, 12], natural="water")
    data = OsmData.load(write_osm(tmp_path, body), layer="water", tile=TILE)
    assert data.multipolygons_with(on_skip=lambda code, ctx: skipped.append(code)) == []
    assert skipped == ["OSM_WAY_NOT_CLOSED"]


def test_a_relation_subtracts_its_inner_rings(tmp_path: Path) -> None:
    body = (
        _square(10, 43.0, 5.0)
        + node(20, 43.02, 5.02)
        + node(21, 43.02, 5.04)
        + node(22, 43.04, 5.04)
        + node(23, 43.04, 5.02)
        + way(1, [10, 11, 12, 13, 10])
        + way(2, [20, 21, 22, 23, 20])
        + relation(1, [("way", 1, "outer"), ("way", 2, "inner")], natural="water", name="Donut")
    )
    data = OsmData.load(write_osm(tmp_path, body), layer="water", tile=TILE)
    polys = data.multipolygons_with()
    assert [p.kind for p in polys] == ["r"]
    assert polys[0].tags == {"natural": "water", "name": "Donut"}
    assert len(polys[0].polygon.interiors) == 1
    assert polys[0].polygon.area == pytest.approx(0.01 - 0.0004)


def test_node_coords_without_a_tile_returns_degrees(tmp_path: Path) -> None:
    body = node(10, 43.5, 5.5) + node(11, 43.6, 5.6) + way(1, [10, 11])
    data = OsmData.load(write_osm(tmp_path, body))
    np.testing.assert_allclose(data.node_coords([-1, -2]), [[5.5, 43.5], [5.6, 43.6]])
