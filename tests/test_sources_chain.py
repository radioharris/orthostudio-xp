"""The order a tile's map data is asked in (``docs/specs/osm-prepared.md``).

The night of 2026-09-22 every build stopped on its Data step, and the days after it showed why a
prepared library cannot simply be trusted: 22 % of the tiles the one public library lists hold no
road at all, and a tile that straddles a border is cut at that border with a valid file and a
matching checksum to show for it. These tests are mostly about refusing, not about reading.
"""

from __future__ import annotations

import bz2
from pathlib import Path

from orthostudio.model import TileRef
from orthostudio.sources.chain import Chain, FolderSource
from orthostudio.sources.osm import LAYERS, OsmNode, OsmSnapshot, OsmWay, SnapshotStore, layers_for

TILE = TileRef(43, 5)
SPECS = layers_for(1)

XML = b"""<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="osmium">
 <node id="1" lat="43.5" lon="5.5"/>
 <node id="2" lat="43.6" lon="5.6"/>
 <way id="10">
  <nd ref="1"/><nd ref="2"/>
  <tag k="highway" v="motorway"/>
 </way>
</osm>
"""


def _snapshot(layer: str, *, ways: int = 1) -> OsmSnapshot:
    spec = LAYERS[layer]
    nodes = tuple(OsmNode(i, 43.5 + i / 100, 5.5, {}) for i in range(1, 3))
    made = tuple(OsmWay(10 + i, (1, 2), {"highway": "motorway"}) for i in range(ways))
    return OsmSnapshot(
        tile=TILE,
        layer=layer,
        selectors=tuple(spec.selectors),
        query="",
        mirror="baked:test",
        fetched_at="2026-09-23T00:00:00Z",
        generator="test",
        osm_base="",
        nodes=nodes,
        ways=made,
        relations=(),
        digest="d" * 64,
    )


def _our_library(root: Path, *, layers: list[str] | None = None) -> Path:
    store = SnapshotStore(root)
    for spec in SPECS:
        if layers is not None and spec.name not in layers:
            continue
        store.save(_snapshot(spec.name))
    return root


# -- reading ----------------------------------------------------------------------------------


def test_a_folder_in_our_own_format_is_read(tmp_path: Path) -> None:
    got = FolderSource(_our_library(tmp_path / "lib")).layers(TILE, SPECS)
    assert got is not None
    assert sorted(got) == sorted(s.name for s in SPECS)
    assert got["big_roads"].ways[0].tags["highway"] == "motorway"
    assert got["big_roads"].mirror == "baked:test"  # the tile remembers where it came from


def test_a_folder_in_ortho4xps_format_is_read(tmp_path: Path) -> None:
    """A user asked for what Ortho4XP's OSM folder gives him, and the library he downloads
    publishes that same shape (2026-09-23)."""
    root = tmp_path / "their-folder"
    for spec in SPECS:
        path = root / TILE.folder / TILE.name / f"{TILE.name}_{spec.name}.osm.bz2"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bz2.compress(XML))
    got = FolderSource(root).layers(TILE, SPECS)
    assert got is not None and sorted(got) == sorted(s.name for s in SPECS)
    assert got["water"].ways[0].id == 10
    assert got["water"].mirror == "folder"


def test_a_flat_folder_is_read_too(tmp_path: Path) -> None:
    """Whoever drops files by hand does not always rebuild the cells and tiles of a tree."""
    root = tmp_path / "flat"
    root.mkdir()
    for spec in SPECS:
        (root / f"{TILE.name}_{spec.name}.osm.bz2").write_bytes(bz2.compress(XML))
    assert FolderSource(root).layers(TILE, SPECS) is not None


# -- refusing ---------------------------------------------------------------------------------


def test_a_folder_short_of_one_layer_gives_none(tmp_path: Path) -> None:
    """All or nothing: a coastline truncated at a border under roads that are complete is how an
    incoherent tile is made, and nothing would say so."""
    kept = [s.name for s in SPECS if s.name != "water"]
    assert FolderSource(_our_library(tmp_path / "lib", layers=kept)).layers(TILE, SPECS) is None


def test_an_empty_road_layer_is_not_an_answer(tmp_path: Path) -> None:
    """2026-09-19 and again 2026-09-23: 1 559 of the 7 132 tiles the public library lists hold no
    road at all. A build that took them would lay scenery without roads and report success."""
    root = _our_library(tmp_path / "lib")
    empty = root / "osm" / TILE.folder / TILE.name / f"{TILE.name}_big_roads.osm.json.zst"
    empty.write_bytes(b"x" * 20)
    assert FolderSource(root).layers(TILE, SPECS) is None


def test_an_empty_coastline_is_an_answer(tmp_path: Path) -> None:
    """Emptiness is the truth for an inland tile: our own bake of Luxembourg writes 26 bytes."""
    root = tmp_path / "their-folder"
    root.mkdir()
    for spec in SPECS:
        path = root / f"{TILE.name}_{spec.name}.osm.bz2"
        body = b"<osm version='0.6'></osm>" if spec.name == "coastline" else XML
        path.write_bytes(bz2.compress(body))
    got = FolderSource(root).layers(TILE, SPECS)
    assert got is not None and got["coastline"].counts["ways"] == 0


def test_an_unreadable_file_does_not_stop_the_build(tmp_path: Path) -> None:
    root = _our_library(tmp_path / "lib")
    bad = root / "osm" / TILE.folder / TILE.name / f"{TILE.name}_water.osm.json.zst"
    bad.write_bytes(b"not a zstd document, but long enough to pass the size test" * 5)
    assert FolderSource(root).layers(TILE, SPECS) is None


# -- the chain --------------------------------------------------------------------------------


class _Fake:
    def __init__(self, name: str, answer: object) -> None:
        self.name = name
        self.answer = answer
        self.asked = 0

    def layers(self, tile: TileRef, specs: object) -> dict[str, OsmSnapshot] | None:
        self.asked += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer  # type: ignore[return-value]


def test_the_first_source_holding_the_whole_tile_wins() -> None:
    whole = {s.name: _snapshot(s.name) for s in SPECS}
    empty, full, never = _Fake("folder", None), _Fake("library", whole), _Fake("public", whole)
    got = Chain([empty, full, never]).layers(TILE, SPECS)
    assert got and got.source == "library"
    assert never.asked == 0  # nothing is asked after one has answered
    assert got.notes == ("folder: not held",)


def test_a_source_that_breaks_is_set_aside_for_the_build() -> None:
    """A library exists to save seconds; one that hangs or throws would cost them."""
    broken = _Fake("library", RuntimeError("no answer"))
    chain = Chain([broken], give_up_after=2)
    for _ in range(4):
        assert not chain.layers(TILE, SPECS)
    assert broken.asked == 2  # twice, then never again in this build
    assert chain.failures["library"] == 2


def test_a_source_that_answers_short_is_refused() -> None:
    """Saying yes means holding the whole tile."""
    short = _Fake("library", {"big_roads": _snapshot("big_roads")})
    got = Chain([short]).layers(TILE, SPECS)
    assert not got and got.notes and "missing" in got.notes[0]


def test_nothing_prepared_leaves_the_tile_to_overpass() -> None:
    got = Chain([]).layers(TILE, SPECS)
    assert not got and got.source == "" and got.snapshots is None
