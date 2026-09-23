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


# -- the library someone else publishes -------------------------------------------------------


def test_the_public_library_is_inert_without_a_whitelist() -> None:
    """No verification, no use. A build that cannot reach our own library has no whitelist, and
    then it must not read theirs either: 22 % of the tiles they list hold no road at all, and
    nothing in a file says it is short (2026-09-19, unchanged 2026-09-23)."""
    from orthostudio.sources.prepared import PublicSource

    asked: list[object] = []
    source = PublicSource(fetch=lambda urls: asked.append(urls) or [])  # type: ignore[arg-type]
    assert source.layers(TILE, SPECS) is None
    assert asked == []  # not even their manifest is read


def test_the_public_library_serves_only_the_tiles_we_verified() -> None:
    from orthostudio.sources.prepared import PreparedIndex, PublicSource, layer_path

    files = {layer_path(TILE, spec.name): {"sha256": "", "size": 5_000} for spec in SPECS}
    index = PreparedIndex(version="2026-08-08", files=files)
    calls: list[int] = []

    def fetch(urls):  # type: ignore[no-untyped-def]
        calls.append(len(urls))
        return [(200, bz2.compress(XML)) for _ in urls]

    allowed = PublicSource([TILE.name], index=index, fetch=fetch)
    got = allowed.layers(TILE, SPECS)
    assert got is not None and sorted(got) == sorted(s.name for s in SPECS)

    elsewhere = PublicSource(["+00+000"], index=index, fetch=fetch)
    assert elsewhere.layers(TILE, SPECS) is None
    assert calls == [len(SPECS)]  # the second one asked nothing


def test_a_whitelisted_tile_with_an_empty_road_layer_is_still_refused() -> None:
    """The whitelist says the tile was complete when we looked; the manifest says this file is
    empty now. The smaller claim wins."""
    from orthostudio.sources.prepared import PreparedIndex, PublicSource, layer_path

    files = {
        layer_path(TILE, spec.name): {
            "sha256": "",
            "size": 120 if spec.name == "big_roads" else 5_000,
        }
        for spec in SPECS
    }
    source = PublicSource([TILE.name], index=PreparedIndex(files=files), fetch=lambda urls: [])
    assert source.layers(TILE, SPECS) is None


# -- inside a build ---------------------------------------------------------------------------


def test_the_build_asks_the_prepared_sources_before_overpass(tmp_path: Path) -> None:
    """The whole point: a tile whose layers are already prepared is not downloaded at all."""
    from orthostudio.pipeline.native import OsmJob

    whole = {s.name: _snapshot(s.name) for s in SPECS}
    said: list[str] = []
    job = OsmJob(
        chain=Chain([_Fake("library", whole)]),
        progress=lambda fraction, message: said.append(message),
    )
    assert job.run(TILE, SPECS) == whole
    assert said and "from library" in said[0]  # the page says where it came from


def test_asking_for_fresh_data_goes_straight_to_the_live_servers() -> None:
    """A prepared library is weeks behind by design, which is the whole reason for pressing
    Refresh (``osm-prepared.md`` 1)."""
    from orthostudio.pipeline.native import OsmJob

    library = _Fake("library", {s.name: _snapshot(s.name) for s in SPECS})
    job = OsmJob(chain=Chain([library]), refresh=True, fetch=lambda tile, specs: {"live": True})
    assert job.run(TILE, SPECS) == {"live": True}
    assert library.asked == 0


def test_a_build_without_prepared_sources_behaves_as_before() -> None:
    from orthostudio.pipeline.native import OsmJob

    job = OsmJob(fetch=lambda tile, specs: {"live": True})
    assert job.run(TILE, SPECS) == {"live": True}


# -- the whole workflow, failure by failure ---------------------------------------------------


def test_every_step_of_the_chain_falls_through_to_the_next(tmp_path: Path) -> None:
    """The scenario a production day serves: a folder that holds nothing, a library that breaks,
    a public library nobody verified, and the live servers behind them all."""
    whole = {s.name: _snapshot(s.name) for s in SPECS}
    folder = FolderSource(tmp_path / "empty")  # nothing in it
    broken = _Fake("library", RuntimeError("no answer"))
    public = _Fake("xpconnect", None)  # inert: no whitelist
    live = _Fake("overpass", whole)

    chain = Chain([folder, broken, public, live])
    got = chain.layers(TILE, SPECS)
    assert got and got.source == "overpass"
    assert got.notes == (
        "folder: not held",
        "library: RuntimeError: no answer",
        "xpconnect: not held",
    )

    # and the broken one is asked once more, then set aside for the rest of the build
    for _ in range(3):
        chain.layers(TILE, SPECS)
    assert broken.asked == 2


def test_a_source_that_answers_nothing_never_stops_the_build() -> None:
    """Whatever a source does -- raising, returning nonsense, holding half a tile -- the tile
    ends up with the live servers rather than with an error."""
    whole = {s.name: _snapshot(s.name) for s in SPECS}
    for bad in (
        _Fake("odd", RuntimeError("boom")),
        _Fake("odd", {"big_roads": _snapshot("big_roads")}),
        _Fake("odd", {}),
        _Fake("odd", None),
    ):
        got = Chain([bad, _Fake("overpass", whole)]).layers(TILE, SPECS)
        assert got and got.source == "overpass"


# -- what the user is told (review M4) ----------------------------------------------------------


def test_a_setting_that_names_nothing_is_said_before_the_build(tmp_path: Path) -> None:
    """A folder that is not there, an address without a key: the setting then does nothing at
    all, and looked exactly like a tile the library does not cover."""
    from orthostudio.sources.chain import settings_trouble

    assert settings_trouble({}) == []
    assert settings_trouble({"osm_folder": str(tmp_path)}) == []  # a folder that exists

    codes = [t.code for t in settings_trouble({"osm_folder": str(tmp_path / "nowhere")})]
    assert codes == ["OSM_PREPARED_FOLDER_MISSING"]

    (trouble,) = settings_trouble({"osm_library": "https://example.invalid/data"})
    assert trouble.code == "OSM_LIBRARY_KEY_REFUSED"
    assert "no key" in trouble.message and "Settings" in trouble.remedy

    (trouble,) = settings_trouble({"osm_library_token": "a-key"})
    assert trouble.code == "OSM_LIBRARY_UNREACHABLE"


def test_a_source_set_aside_says_so_once() -> None:
    """The difference between a build that reads prepared tiles and one that queues behind the
    public servers for an hour: it used to happen in the log alone."""
    from orthostudio.sources.chain import Chain

    class Broken:
        name = "library"

        def layers(self, tile, specs):  # type: ignore[no-untyped-def]
            raise RuntimeError("the door is shut")

    said: list[str] = []
    chain = Chain([Broken()], say=said.append)
    for _ in range(5):
        chain.layers(TileRef(43, 5), list(layers_for(1)))
    assert len(said) == 1, "said once, not once per tile"
    assert "library" in said[0] and "map data live" in said[0]
    assert "Nothing to do" in said[0], "the remedy travels with the message"


def test_a_copied_library_keeps_its_proofs_in_a_folder(tmp_path: Path) -> None:
    """Whoever copies a library to disk gets what the library gets: a square that really holds
    no road is taken, because the manifest says what each file's content must hash to. A folder
    without a manifest keeps the strict rule (2026-09-23)."""
    import json

    from orthostudio.sources.osm import SnapshotStore

    root = _our_library(tmp_path / "lib")
    store = SnapshotStore(root)
    empty = OsmSnapshot(
        tile=TILE,
        layer="big_roads",
        selectors=tuple(LAYERS["big_roads"].selectors),
        query="",
        mirror="baked:test",
        fetched_at="2026-09-23T00:00:00Z",
        generator="test",
        osm_base="",
        nodes=(),
        ways=(),
        relations=(),
        digest="f" * 64,
    )
    store.save(empty, sidecar=False)
    assert FolderSource(root).layers(TILE, SPECS) is None, "nothing vouches for it yet"

    files = {}
    for spec in SPECS:
        path = store.path_for(TILE, spec.name)
        snap = store.load(TILE, spec.name)
        assert snap is not None
        files[str(path.relative_to(root))] = {
            "tile": TILE.name,
            "layer": spec.name,
            "digest": snap.digest,
            "bytes": path.stat().st_size,
        }
    manifest = {
        "format": "osxp-baked-1",
        "extracted": "2026-09-22",
        "road_level": 1,
        "files": files,
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    got = FolderSource(root).layers(TILE, SPECS)
    assert got is not None and got["big_roads"].is_empty
