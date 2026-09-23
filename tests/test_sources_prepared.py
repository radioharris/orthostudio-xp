"""Unit tests for ``orthostudio.sources.prepared``: the OSM layers already cut per tile.

No network at all: the manifest and the files are handed in as plain functions. What is checked
is what the module promises -- the tile's data enters the key, anything wrong falls back to
Overpass, and the copy of the manifest on disk is asked for again only with its ETag.
Spec: ``docs/specs/osm-source.md`` section 6.
"""

from __future__ import annotations

import bz2
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from orthostudio.model import TileRef
from orthostudio.sources.osm import LAYERS, layers_for
from orthostudio.sources.prepared import (
    MANIFEST_URL,
    MAX_LAYER_BYTES,
    PreparedIndex,
    layer_path,
    load_index,
    snapshot_from_xml,
    tile_snapshots,
)

TILE = TileRef(46, 6)
SPECS = list(layers_for(1))


# -- documents -------------------------------------------------------------------------------


def osm_xml(*, generator: str = "osmium/1.16.0", meta: str = "") -> bytes:
    """One layer as OrthoForge bakes it: OSM 0.6 XML, no ``<meta>`` unless asked for."""
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


def manifest(files: Mapping[str, bytes], version: str = "2026-09-01") -> bytes:
    doc = {
        "version": version,
        "files": {
            path: {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
            for path, body in files.items()
        },
    }
    return json.dumps(doc).encode()


def baked(tile: TileRef = TILE, specs: Sequence[object] = ()) -> dict[str, bytes]:
    """The five layers of a tile, compressed as they are served."""
    names = [s.name for s in (specs or SPECS)]  # type: ignore[attr-defined]
    return {layer_path(tile, name): bz2.compress(osm_xml()) for name in names}


def index_of(files: Mapping[str, bytes], version: str = "2026-09-01") -> PreparedIndex:
    doc = json.loads(manifest(files, version))
    return PreparedIndex(version=doc["version"], files=doc["files"], etag='"v1"')


# -- what the manifest says ------------------------------------------------------------------


def test_the_index_answers_for_the_tiles_it_holds() -> None:
    index = index_of(baked())
    assert index.covers(TILE, SPECS)
    assert not index.covers(TileRef(47, 7), SPECS)
    assert not index.covers(TILE, [LAYERS["small_roads"]])  # a layer it does not hold
    assert not index.covers(TILE, [])  # nothing asked for is not "covered"
    assert index.digest_of(TILE, "water") == hashlib.sha256(bz2.compress(osm_xml())).hexdigest()
    assert index.digest_of(TILE, "small_roads") == ""


def test_the_stamp_follows_the_tiles_own_layers() -> None:
    """A bake that changes this tile makes its node run again; one that changes another does not."""
    files = baked()
    index = index_of(files)
    same = index.stamp(TILE, SPECS)
    assert len(same) == 12
    assert index_of(files, version="2026-10-01").stamp(TILE, SPECS) == same  # version is not it
    assert index.stamp(TILE, reversed(SPECS)) == same  # the order they are asked in is not it

    elsewhere = dict(files) | baked(TileRef(47, 7))
    assert index_of(elsewhere).stamp(TILE, SPECS) == same  # another tile changed: nothing to do

    changed = dict(files)
    changed[layer_path(TILE, "water")] = bz2.compress(osm_xml(generator="osmium/2"))
    assert index_of(changed).stamp(TILE, SPECS) != same

    assert PreparedIndex().stamp(TILE, []) == ""  # nothing asked for, nothing keyed


# -- reading one tile ------------------------------------------------------------------------


def fetcher(answers: Mapping[str, tuple[int, bytes]]):
    """A fetch of the whole tile, answering ``(status, body)`` per URL, in order."""

    def fetch(urls: Sequence[str]) -> list[tuple[int, bytes]]:
        return [answers.get(url.rsplit("/orthoforge-data/", 1)[-1], (404, b"")) for url in urls]

    return fetch


def served(files: Mapping[str, bytes]) -> dict[str, tuple[int, bytes]]:
    return {path: (200, body) for path, body in files.items()}


def test_a_tile_is_read_from_the_prepared_files() -> None:
    files = baked()
    index = index_of(files)
    seen: list[tuple[float, str]] = []
    snaps = tile_snapshots(
        TILE, SPECS, index, fetcher(served(files)), lambda f, m: seen.append((f, m))
    )
    assert snaps is not None
    assert sorted(snaps) == sorted(s.name for s in SPECS)
    water = snaps["water"]
    assert (
        water.tile == TILE and water.layer == "water" and water.mirror.endswith("orthoforge-data")
    )
    assert [n.id for n in water.nodes] == [1, 2]
    assert water.nodes[1].tags == {"natural": "water"}
    assert water.ways[0].nodes == (1, 2) and water.ways[0].tags == {"waterway": "riverbank"}
    assert water.relations[0].members[0].role == "outer"
    assert water.osm_base == "2026-09-01"  # the bake's date, the files carry none
    assert water.digest and water.fetched_at
    # every network step says how fast it went (``ui.md`` 2.2)
    assert [round(f, 2) for f, _ in seen] == [0.25, 0.5, 0.75, 1.0]
    assert seen[-1][1].startswith(f"{TILE.name}: 4/4 OSM layers")


def test_a_tile_the_index_does_not_hold_goes_to_overpass() -> None:
    called: list[Sequence[str]] = []

    def fetch(urls: Sequence[str]) -> list[tuple[int, bytes]]:
        called.append(urls)
        return []

    assert tile_snapshots(TileRef(47, 7), SPECS, index_of(baked()), fetch) is None
    assert called == []  # nothing is asked for a tile the manifest does not name


@pytest.mark.parametrize(
    "spoil",
    [
        pytest.param(lambda files: {**files, layer_path(TILE, "water"): b""}, id="empty"),
        pytest.param(lambda files: {**files, layer_path(TILE, "water"): b"not bzip2"}, id="broken"),
        pytest.param(
            lambda files: {**files, layer_path(TILE, "water"): bz2.compress(b"<osm><nope")},
            id="cut short",
        ),
    ],
)
def test_anything_wrong_with_a_file_falls_back(spoil) -> None:
    files = baked()
    index = index_of(files)  # the manifest promises the good files
    assert tile_snapshots(TILE, SPECS, index, fetcher(served(spoil(files)))) is None


def test_a_file_that_is_not_what_the_manifest_promised_falls_back() -> None:
    files = baked()
    index = index_of(files)
    other = {**files, layer_path(TILE, "water"): bz2.compress(osm_xml(generator="somewhere else"))}
    assert tile_snapshots(TILE, SPECS, index, fetcher(served(other))) is None


def test_a_service_that_does_not_answer_falls_back() -> None:
    files = baked()
    index = index_of(files)
    assert tile_snapshots(TILE, SPECS, index, fetcher({})) is None  # 404 everywhere

    def raises(urls: Sequence[str]) -> list[tuple[int, bytes]]:
        raise OSError("the name does not resolve")

    assert tile_snapshots(TILE, SPECS, index, raises) is None

    def short(urls: Sequence[str]) -> list[tuple[int, bytes]]:
        return [(200, b"")]  # fewer answers than layers

    assert tile_snapshots(TILE, SPECS, index, short) is None


def test_a_file_too_large_to_be_one_of_theirs_falls_back() -> None:
    files = baked()
    index = index_of(files)
    huge = {**served(files), layer_path(TILE, "water"): (200, b"x" * (MAX_LAYER_BYTES + 1))}
    assert tile_snapshots(TILE, SPECS, index, fetcher(huge)) is None


def test_a_plain_document_is_read_too() -> None:
    """Not every file is compressed; the reader looks at the first bytes, not at the name."""
    snap = snapshot_from_xml(osm_xml(meta="2026-09-10T00:00:00Z"), TILE, "water", mirror="m")
    assert snap.osm_base == "2026-09-10T00:00:00Z"  # what the document says wins
    assert snap.generator == "osmium/1.16.0" and len(snap.nodes) == 2


# -- the manifest on disk --------------------------------------------------------------------


def answering(*, status: int = 200, body: bytes = b"", etag: str = '"v1"'):
    calls: list[Mapping[str, str]] = []

    def fetch(url: str, headers: Mapping[str, str]) -> tuple[int, bytes, Mapping[str, str]]:
        assert url == MANIFEST_URL
        calls.append(dict(headers))
        return status, body, {"etag": etag}

    return fetch, calls


def test_the_manifest_is_kept_and_asked_for_again_with_its_etag(tmp_path: Path) -> None:
    files = baked()
    fetch, calls = answering(body=manifest(files))
    index = load_index(tmp_path, fetch)
    assert index is not None and index.covers(TILE, SPECS) and index.etag == '"v1"'
    assert calls == [{}]  # nothing on disk yet: no ETag to offer
    assert (tmp_path / "manifest.json").is_file()

    # fresh on disk: read from there, nothing asked
    assert load_index(tmp_path, fetch) is not None
    assert len(calls) == 1

    # past its time: the ETag goes out and 304 comes back, the copy stays in charge
    not_modified, calls_304 = answering(status=304)
    again = load_index(tmp_path, not_modified, ttl_s=0.0)
    assert again is not None and again.covers(TILE, SPECS)
    assert calls_304 == [{"If-None-Match": '"v1"'}]
    # and it is young again: the next build asks nothing
    assert load_index(tmp_path, answering(status=500)[0]) is not None


def test_a_manifest_that_cannot_be_read_never_stops_a_build(tmp_path: Path) -> None:
    def raises(url: str, headers: Mapping[str, str]) -> tuple[int, bytes, Mapping[str, str]]:
        raise OSError("the name does not resolve")

    assert load_index(tmp_path, raises) is None  # nothing on disk, nothing to offer
    assert load_index(tmp_path, answering(status=503)[0]) is None
    assert load_index(tmp_path, answering(body=b"{ not json")[0]) is None
    assert load_index(tmp_path, answering(body=b'{"files": "not a map"}')[0]) is None
    assert not (tmp_path / "manifest.json").exists()  # nothing bad was kept

    # with a copy on disk, every one of those leaves it in charge
    load_index(tmp_path, answering(body=manifest(baked()))[0])
    for fetch in (raises, answering(status=503)[0], answering(body=b"{ not json")[0]):
        kept = load_index(tmp_path, fetch, ttl_s=0.0)
        assert kept is not None and kept.covers(TILE, SPECS)


def test_a_manifest_read_again_replaces_the_one_on_disk(tmp_path: Path) -> None:
    load_index(tmp_path, answering(body=manifest(baked()))[0])
    wider = dict(baked()) | baked(TileRef(47, 7))
    fresh = load_index(
        tmp_path, answering(body=manifest(wider, "2026-10-01"), etag='"v2"')[0], ttl_s=0.0
    )
    assert fresh is not None and fresh.version == "2026-10-01" and fresh.etag == '"v2"'
    assert fresh.covers(TileRef(47, 7), SPECS)
    kept = load_index(tmp_path, answering(status=500)[0], ttl_s=0.0)
    assert kept is not None and kept.etag == '"v2"'  # the new ETag was kept beside it
