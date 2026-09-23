"""The baked library read over HTTP, and the door it opens with (``osm-prepared.md``).

Nothing here touches the network: the transport is injected, and the library it serves is baked
by our own tools in a temporary folder, manifest included.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import zstandard

from orthostudio.model import TileRef
from orthostudio.sources.chain import Chain
from orthostudio.sources.library import LibrarySource, parse_manifest
from orthostudio.sources.osm import LAYERS, OsmNode, OsmSnapshot, OsmWay, SnapshotStore, layers_for

TILE = TileRef(43, 5)
SPECS = layers_for(1)
TOKEN = "a-key-that-ships-with-the-app"


def _snapshot(layer: str, digest: str = "d" * 64) -> OsmSnapshot:
    spec = LAYERS[layer]
    return OsmSnapshot(
        tile=TILE,
        layer=layer,
        selectors=tuple(spec.selectors),
        query="",
        mirror="baked:france",
        fetched_at="2026-09-23T00:00:00Z",
        generator="orthostudio bake_tile",
        osm_base="",
        nodes=(OsmNode(1, 43.5, 5.5, {}), OsmNode(2, 43.6, 5.6, {})),
        ways=(OsmWay(10, (1, 2), {"highway": "motorway"}),),
        relations=(),
        digest=digest,
    )


def _library(root: Path, *, digests: Mapping[str, str] | None = None) -> dict[str, bytes]:
    """A library on disk plus the bytes a server would answer, keyed by URL path."""
    store = SnapshotStore(root)
    files: dict[str, dict[str, object]] = {}
    served: dict[str, bytes] = {}
    for spec in SPECS:
        digest = (digests or {}).get(spec.name, "d" * 64)
        snap = _snapshot(spec.name, digest)
        path = store.save(snap)
        rel = str(path.relative_to(root))
        files[rel] = {
            "tile": TILE.name,
            "layer": spec.name,
            "digest": digest,
            "bytes": path.stat().st_size,
        }
        served[rel] = path.read_bytes()
    manifest = {
        "format": "osxp-baked-1",
        "source": "france-osxp.osm.pbf",
        "extracted": "2026-09-21T00:00:00Z",
        "road_level": 1,
        "layers": [s.name for s in SPECS],
        "tiles": [TILE.name],
        "files": files,
    }
    served["manifest.json"] = json.dumps(manifest).encode()
    return served


class _Server:
    """Answers what the library holds, and 403 to anyone without the key."""

    def __init__(self, served: Mapping[str, bytes], *, token: str = TOKEN) -> None:
        self.served = dict(served)
        self.token = token
        self.asked: list[str] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
        path = url.split("/data/", 1)[1]
        self.asked.append(path)
        if headers.get("Authorization") != f"Bearer {self.token}":
            return 403, b"forbidden"
        body = self.served.get(path)
        return (200, body) if body is not None else (404, b"")


def _source(served: Mapping[str, bytes], token: str = TOKEN, **kw: object) -> LibrarySource:
    server = _Server(served)
    src = LibrarySource("https://example.invalid/data", token, fetch=server, **kw)  # type: ignore[arg-type]
    src.server = server  # type: ignore[attr-defined]
    return src


# -- reading ----------------------------------------------------------------------------------


def test_the_library_gives_the_layers_of_a_tile_it_holds(tmp_path: Path) -> None:
    src = _source(_library(tmp_path / "lib"))
    got = src.layers(TILE, SPECS)
    assert got is not None and sorted(got) == sorted(s.name for s in SPECS)
    assert got["big_roads"].ways[0].tags["highway"] == "motorway"
    assert src.index is not None and src.index.extracted == "2026-09-21T00:00:00Z"


def test_the_manifest_is_read_once(tmp_path: Path) -> None:
    src = _source(_library(tmp_path / "lib"))
    src.layers(TILE, SPECS)
    src.layers(TILE, SPECS)
    assert src.server.asked.count("manifest.json") == 1  # type: ignore[attr-defined]


def test_a_tile_the_library_does_not_hold_is_not_asked_for(tmp_path: Path) -> None:
    src = _source(_library(tmp_path / "lib"))
    assert src.layers(TileRef(10, 10), SPECS) is None
    assert src.server.asked == ["manifest.json"]  # type: ignore[attr-defined]


# -- the door ---------------------------------------------------------------------------------


def test_without_the_key_the_library_gives_nothing(tmp_path: Path) -> None:
    """The token does not make the library secret; it makes the door closable. Whoever does not
    carry it -- another program, a crawler -- is told nothing at all (2026-09-23)."""
    src = _source(_library(tmp_path / "lib"), token="")
    assert src.layers(TILE, SPECS) is None
    assert src.index is None


def test_a_wrong_key_is_not_retried_all_build_long(tmp_path: Path) -> None:
    src = _source(_library(tmp_path / "lib"), token="the-old-key")
    for _ in range(3):
        assert src.layers(TILE, SPECS) is None
    assert src.server.asked == ["manifest.json"]  # type: ignore[attr-defined]


# -- refusing ---------------------------------------------------------------------------------


def test_a_file_that_is_not_the_one_announced_is_refused(tmp_path: Path) -> None:
    """A stale copy, or a library rebaked under our feet: either way it is not what was
    verified."""
    served = dict(_library(tmp_path / "lib"))
    manifest = json.loads(served["manifest.json"])
    for meta in manifest["files"].values():
        if meta["layer"] == "water":
            meta["digest"] = "0" * 64
    served["manifest.json"] = json.dumps(manifest).encode()
    assert _source(served).layers(TILE, SPECS) is None


def test_an_empty_road_layer_is_refused_before_it_is_downloaded(tmp_path: Path) -> None:
    served = dict(_library(tmp_path / "lib"))
    manifest = json.loads(served["manifest.json"])
    for meta in manifest["files"].values():
        if meta["layer"] == "big_roads":
            meta["bytes"] = 120
    served["manifest.json"] = json.dumps(manifest).encode()
    src = _source(served)
    assert src.layers(TILE, SPECS) is None
    assert src.server.asked == ["manifest.json"]  # type: ignore[attr-defined] - nothing fetched


def test_a_corrupted_file_is_refused(tmp_path: Path) -> None:
    served = dict(_library(tmp_path / "lib"))
    key = next(k for k in served if k.endswith("_water.osm.json.zst"))
    served[key] = b"not a zstd frame"
    assert _source(served).layers(TILE, SPECS) is None


def test_a_file_holding_another_tile_is_refused(tmp_path: Path) -> None:
    """The path says one thing, the document another: trust the document."""
    served = dict(_library(tmp_path / "lib"))
    key = next(k for k in served if k.endswith("_water.osm.json.zst"))
    other = _snapshot("water")
    doc = other.to_json().replace(b'"tile":"+43+005"', b'"tile":"+44+005"')
    served[key] = zstandard.ZstdCompressor(level=10).compress(doc)
    assert _source(served).layers(TILE, SPECS) is None


def test_a_foreign_manifest_is_not_read(tmp_path: Path) -> None:
    served = {"manifest.json": json.dumps({"format": "someone-elses-1", "files": {}}).encode()}
    assert _source(served).layers(TILE, SPECS) is None
    assert parse_manifest(b"{}") is None and parse_manifest(b"not json") is None


# -- in the chain -----------------------------------------------------------------------------


def test_the_chain_falls_through_to_the_next_source(tmp_path: Path) -> None:
    """A library that holds nothing for this tile costs one manifest and no more."""
    closed = _source(_library(tmp_path / "lib"), token="wrong")
    open_one = _source(_library(tmp_path / "lib2"))
    got = Chain([closed, open_one]).layers(TILE, SPECS)
    assert got and got.source == "library"
    assert got.notes == ("library: not held",)


# -- the key a release carries ----------------------------------------------------------------


def test_the_address_and_key_are_read_in_three_steps(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """What the user set, then what the build carries, then nothing at all. The repository is
    public, so it holds neither: a build from source reaches no library and downloads every tile
    live, as every version before this one (2026-09-23)."""
    from orthostudio.sources import chain as chain_mod
    from orthostudio.sources.library import LibrarySource

    monkeypatch.setattr(chain_mod, "shipped_library", lambda: ("https://carried", "carried-key"))

    # nothing set: the build's own address and key answer
    (carried,) = [s for s in chain_mod.sources_from_settings({}) if isinstance(s, LibrarySource)]
    assert (carried.base, carried.token) == ("https://carried", "carried-key")

    # the user's own server wins over it
    mine = {"osm_library": "https://mine/data", "osm_library_token": "my-key"}
    (chosen,) = [s for s in chain_mod.sources_from_settings(mine) if isinstance(s, LibrarySource)]
    assert (chosen.base, chosen.token) == ("https://mine/data", "my-key")

    # a build from source carries nothing: no library in the chain at all
    monkeypatch.setattr(chain_mod, "shipped_library", lambda: ("", ""))
    assert not [s for s in chain_mod.sources_from_settings({}) if isinstance(s, LibrarySource)]


def test_this_repository_carries_no_key() -> None:
    """The one test that must never be made to pass by adding a file."""
    from orthostudio.sources.library import shipped_library

    shipped_library.cache_clear()
    assert shipped_library() == ("", "")
