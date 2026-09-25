"""The baked library read over HTTP, and the door it opens with (``osm-prepared.md``).

Nothing here touches the network: the transport is injected, and the library it serves is baked
by our own tools in a temporary folder, manifest included.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
import zstandard

from orthostudio.model import TileRef
from orthostudio.sources.chain import Chain
from orthostudio.sources.library import LibraryError, LibrarySource, parse_manifest
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

    def __call__(self, urls: Sequence[str], headers: Mapping[str, str]) -> list[tuple[int, bytes]]:
        out: list[tuple[int, bytes]] = []
        for url in urls:
            path = url.split("/data/", 1)[1]
            self.asked.append(path)
            if headers.get("Authorization") != f"Bearer {self.token}":
                out.append((403, b"forbidden"))
                continue
            body = self.served.get(path)
            out.append((200, body) if body is not None else (404, b""))
        return out


def _source(served: Mapping[str, bytes], token: str = TOKEN, **kw: object) -> LibrarySource:
    server = _Server(served)
    src = LibrarySource("https://example.invalid/data", token, fetch=server, **kw)  # type: ignore[arg-type]
    src.server = server  # type: ignore[attr-defined]
    return src


# -- reading ---------------------------------------------------------------------------------------


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


# -- the door --------------------------------------------------------------------------------------


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


# -- refusing --------------------------------------------------------------------------------------


def test_a_file_that_is_not_the_one_announced_is_refused(tmp_path: Path) -> None:
    """A stale copy, or a library rebaked under our feet: either way it is not what was
    verified."""
    served = dict(_library(tmp_path / "lib"))
    manifest = json.loads(served["manifest.json"])
    for meta in manifest["files"].values():
        if meta["layer"] == "water":
            meta["digest"] = "0" * 64
    served["manifest.json"] = json.dumps(manifest).encode()
    src = _source(served)
    with pytest.raises(LibraryError):  # the library is wrong, not merely short of this tile
        src.layers(TILE, SPECS)
    assert not Chain([src]).layers(TILE, SPECS)  # and the chain carries on to the next source


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
    src = _source(served)
    with pytest.raises(LibraryError):
        src.layers(TILE, SPECS)
    assert not Chain([src]).layers(TILE, SPECS)


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


# -- in the chain ----------------------------------------------------------------------------------


def test_the_chain_falls_through_to_the_next_source(tmp_path: Path) -> None:
    """A library that holds nothing for this tile costs one manifest and no more."""
    closed = _source(_library(tmp_path / "lib"), token="wrong")
    open_one = _source(_library(tmp_path / "lib2"))
    got = Chain([closed, open_one]).layers(TILE, SPECS)
    assert got and got.source.startswith("library")
    assert "2026-09-21" in got.source  # the page says how old the data is
    assert got.notes == ("library: not held",)


# -- the key a release carries ---------------------------------------------------------------------


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


# -- what a production library does wrong ----------------------------------------------------------


def test_a_library_that_announces_what_it_does_not_hold_is_set_aside(tmp_path: Path) -> None:
    """The case of an upload in progress: the manifest is there, the files are not yet. Every
    tile then costs four requests before Overpass is asked, for every tile of the batch."""
    served = dict(_library(tmp_path / "lib"))
    for key in [k for k in served if k.endswith(".zst")]:
        del served[key]  # announced, not there
    src = _source(served)
    chain = Chain([src])
    for _ in range(4):
        assert not chain.layers(TILE, SPECS)
    assert chain.failures.get("library", 0) >= 2, "a library that lies must be set aside"


def test_layers_baked_for_another_road_level_are_refused(tmp_path: Path) -> None:
    """A library baked at road level 2 holds tertiary roads and no more. A build asking for level
    5 wants tracks as well: the same layer name, a different question. Taking it would give a
    scenery quietly missing every forest track, and nothing would say so."""
    from orthostudio.sources.osm import layers_for

    rich = layers_for(5)  # tertiary, unclassified, residential, service, track
    poor = layers_for(2)  # tertiary alone
    assert len(dict(zip([s.name for s in rich], rich, strict=True))["small_roads"].selectors) > len(
        dict(zip([s.name for s in poor], poor, strict=True))["small_roads"].selectors
    )

    served = dict(_library(tmp_path / "lib"))  # baked with the level-1 layers
    src = _source(served)
    got = src.layers(TILE, [s for s in rich if s.name in {"big_roads", "water"}])
    assert got is None or all(
        got[name].selectors == next(s.selectors for s in rich if s.name == name) for name in got
    )


# -- a library that is briefly unreachable (review F6, F7) -----------------------------------------


class _Flaky:
    """Answers what it is told to, in order, so a hiccup can be followed by an answer."""

    def __init__(self, served: Mapping[str, bytes], statuses: Sequence[int]) -> None:
        self.served = dict(served)
        self.statuses = list(statuses)
        self.asked: list[str] = []

    def __call__(self, urls: Sequence[str], headers: Mapping[str, str]) -> list[tuple[int, bytes]]:
        out: list[tuple[int, bytes]] = []
        for url in urls:
            path = url.split("/data/", 1)[1]
            self.asked.append(path)
            status = self.statuses.pop(0) if self.statuses else 200
            body = self.served.get(path)
            out.append((200, body) if status == 200 and body is not None else (status, b""))
        return out


def test_one_bad_gateway_does_not_close_the_library_for_the_whole_job(tmp_path: Path) -> None:
    """A restart of the server, a connection cut: it used to latch the library shut for the rest
    of the job, so a build started at the wrong second read no prepared tile at all (F7)."""
    served = _library(tmp_path / "lib")
    flaky = _Flaky(served, [502])
    src = LibrarySource("https://example.invalid/data", TOKEN, fetch=flaky)  # type: ignore[arg-type]
    src._retry_at = 0.0

    assert src.layers(TILE, SPECS) is None  # the hiccup
    src._retry_at = 0.0  # time passes
    assert src.layers(TILE, SPECS) is not None, "the library must be asked again"


def test_a_refused_key_is_not_asked_again(tmp_path: Path) -> None:
    served = _library(tmp_path / "lib")
    flaky = _Flaky(served, [403, 200])
    src = LibrarySource("https://example.invalid/data", TOKEN, fetch=flaky)  # type: ignore[arg-type]
    assert src.layers(TILE, SPECS) is None
    src._retry_at = 0.0
    assert src.layers(TILE, SPECS) is None
    assert flaky.asked == ["manifest.json"], "a wrong key does not become right by asking again"


def test_an_unreachable_library_goes_by_the_copy_it_kept(tmp_path: Path) -> None:
    """The manifest was written at every build and read back at none, so a library down for a
    minute sent every tile of the job to the live servers (F6)."""
    served = _library(tmp_path / "lib")
    cache = tmp_path / "cache"
    first = _source(served, cache_dir=cache)
    assert first.layers(TILE, SPECS) is not None
    assert (cache / "library-manifest.json").is_file()

    down = LibrarySource(
        "https://example.invalid/data",
        TOKEN,
        fetch=_Flaky(served, [0]),  # type: ignore[arg-type]
        cache_dir=cache,
    )
    assert down.index is None
    assert down.layers(TILE, SPECS) is not None, "the tile is on their server, the list on ours"


# -- what a library may not make us do (review F2, F4) ---------------------------------------------


def test_a_library_baked_for_other_layers_costs_no_download(tmp_path: Path) -> None:
    """It used to download the whole tile, read the selectors inside and refuse it, once per
    tile. The manifest says the road level, and that settles it for the whole build (F2)."""
    from orthostudio.sources.osm import layers_for

    src = _source(_library(tmp_path / "lib"))  # baked at road level 1
    rich = [s for s in layers_for(5) if s.name in {"big_roads", "small_roads"}]
    assert src.layers(TILE, rich) is None
    assert src.server.asked == ["manifest.json"]  # type: ignore[attr-defined]


def test_a_file_that_does_not_weigh_what_the_manifest_says_is_refused(tmp_path: Path) -> None:
    served = dict(_library(tmp_path / "lib"))
    key = next(k for k in served if k.endswith("_water.osm.json.zst"))
    served[key] = served[key] + b"\x00" * 40  # the same file, forty bytes heavier
    src = _source(served)
    with pytest.raises(LibraryError, match="weighs"):
        src.layers(TILE, SPECS)


def test_a_small_file_that_unpacks_to_a_huge_one_is_refused() -> None:
    """1.5 kB of zstd unpacks to 50 MB, and the same trick scales as far as one cares to take
    it. ``max_output_size`` does not stop it: a frame that declares its size is unpacked to that
    size whatever the limit says (measured 2026-09-23)."""
    from orthostudio.sources.library import unpack

    blob = zstandard.ZstdCompressor(level=10).compress(b"x" * 50_000_000)
    assert len(blob) < 2000
    with pytest.raises(ValueError, match="more than"):
        unpack(blob, limit=1_000_000)
    assert unpack(blob) == b"x" * 50_000_000  # under the real cap it is read as usual


# -- emptiness that is the truth -------------------------------------------------------------------


def _empty(layer: str) -> OsmSnapshot:
    snap = _snapshot(layer)
    return OsmSnapshot(
        tile=snap.tile,
        layer=layer,
        selectors=snap.selectors,
        query="",
        mirror=snap.mirror,
        fetched_at=snap.fetched_at,
        generator=snap.generator,
        osm_base="",
        nodes=(),
        ways=(),
        relations=(),
        digest="e" * 64,
    )


def _library_with_empty(
    root: Path, layer: str, *, announce_digest: bool = True
) -> dict[str, bytes]:
    """A library where one layer really holds nothing, as a square of ocean does."""
    store = SnapshotStore(root)
    files: dict[str, dict[str, object]] = {}
    served: dict[str, bytes] = {}
    for spec in SPECS:
        snap = _empty(spec.name) if spec.name == layer else _snapshot(spec.name)
        path = store.save(snap)
        rel = str(path.relative_to(root))
        files[rel] = {
            "tile": TILE.name,
            "layer": spec.name,
            "digest": snap.digest if announce_digest else "",
            "bytes": path.stat().st_size,
        }
        served[rel] = path.read_bytes()
    served["manifest.json"] = json.dumps(
        {
            "format": "osxp-baked-1",
            "source": "france-osxp.osm.pbf",
            "extracted": "2026-09-21T00:00:00Z",
            "road_level": 1,
            "layers": [s.name for s in SPECS],
            "tiles": [TILE.name],
            "files": files,
        }
    ).encode()
    return served


def test_an_empty_layer_the_digest_proves_is_taken(tmp_path: Path) -> None:
    """A square of Atlantic off the Sahara has no road, no airport and no lake, only a
    coastline. Refusing those sent every empty square of a continent to the public servers to be
    told the same thing (2026-09-23)."""
    src = _source(_library_with_empty(tmp_path / "lib", "big_roads"))
    got = src.layers(TILE, SPECS)
    assert got is not None and got["big_roads"].is_empty


def test_an_empty_layer_nobody_vouches_for_is_still_refused(tmp_path: Path) -> None:
    """Without a digest in the manifest, emptiness is a claim and not a proof."""
    src = _source(_library_with_empty(tmp_path / "lib", "big_roads", announce_digest=False))
    assert src.layers(TILE, SPECS) is None


def test_a_truncated_file_is_refused_before_it_is_downloaded(tmp_path: Path) -> None:
    """Below the size of an empty layer's own wrapper there is no document at all."""
    served = dict(_library(tmp_path / "lib"))
    manifest = json.loads(served["manifest.json"])
    for meta in manifest["files"].values():
        if meta["layer"] == "big_roads":
            meta["bytes"] = 40
    served["manifest.json"] = json.dumps(manifest).encode()
    src = _source(served)
    assert src.layers(TILE, SPECS) is None
    assert src.server.asked == ["manifest.json"]  # type: ignore[attr-defined]


# -- one bake at road level 5 serves every level (2026-09-25) --------------------------------------


def _roads(level: int) -> OsmSnapshot:
    """``small_roads`` as a bake at ``level`` writes it: one way of every class that level asks
    for, in the order the levels add them, so a lower level's file is a prefix of a higher one's."""
    import blake3

    from orthostudio.sources.osm import _canonical, selector_tag

    spec = next(s for s in layers_for(level) if s.name == "small_roads")
    classes = [selector_tag(sel)[1] for sel in spec.selectors]  # type: ignore[index]
    nodes = tuple(OsmNode(i, 43.1 + i / 100, 5.1, {}) for i in range(1, 2 * len(classes) + 1))
    ways = tuple(
        OsmWay(100 + i, (2 * i + 1, 2 * i + 2), {"highway": c}) for i, c in enumerate(classes)
    )
    return OsmSnapshot(
        tile=TILE,
        layer="small_roads",
        selectors=tuple(spec.selectors),
        query="",
        mirror="baked:planet",
        fetched_at="2026-09-13T23:59:59Z",
        generator="orthostudio bake_tile",
        osm_base="",
        nodes=nodes,
        ways=ways,
        relations=(),
        digest=blake3.blake3(_canonical(nodes, ways, ())).hexdigest(),
    )


def _planet(root: Path) -> dict[str, bytes]:
    """A library baked like the planet one: road level 5, every layer of it."""
    store = SnapshotStore(root)
    files: dict[str, dict[str, object]] = {}
    served: dict[str, bytes] = {}
    for spec in layers_for(5):
        snap = _roads(5) if spec.name == "small_roads" else _snapshot(spec.name)
        path = store.save(snap)
        rel = str(path.relative_to(root))
        files[rel] = {"tile": TILE.name, "layer": spec.name, "digest": snap.digest,
                      "bytes": path.stat().st_size}  # fmt: skip
        served[rel] = path.read_bytes()
    manifest = {"format": "osxp-baked-1", "source": "planet-l5.osm.pbf",
                "extracted": "2026-09-13T23:59:59Z", "road_level": 5, "files": files}  # fmt: skip
    served["manifest.json"] = json.dumps(manifest).encode()
    return served


def test_a_library_baked_at_road_level_5_answers_every_level_below_it(tmp_path: Path) -> None:
    """The planet is baked once, at road level 5. Asked for exactly the baked level, the library
    sent every build at the default level 1 to the public servers; asked for at least it, each
    level gets its own roads and no others, bit for bit what a bake at that level writes."""
    served = _planet(tmp_path / "lib")
    for level in range(6):
        specs = layers_for(level)
        got = _source(served).layers(TILE, specs)
        assert got is not None, f"road level {level} refused"
        assert sorted(got) == sorted(s.name for s in specs)
        for spec in specs:
            assert got[spec.name].selectors == tuple(spec.selectors), (level, spec.name)
        if level >= 2:
            assert got["small_roads"].digest == _roads(level).digest, level
            assert got["small_roads"].ways == _roads(level).ways, level
            assert got["small_roads"].nodes == _roads(level).nodes, level


def test_a_library_baked_below_the_level_asked_is_still_refused(tmp_path: Path) -> None:
    """Narrowing only takes away: a library at road level 2 cannot invent the tracks of level 5."""
    served = _planet(tmp_path / "lib")
    manifest = json.loads(served["manifest.json"])
    manifest["road_level"] = 2
    served["manifest.json"] = json.dumps(manifest).encode()
    src = _source(served)
    assert src.layers(TILE, layers_for(5)) is None
    assert src.server.asked == ["manifest.json"]  # type: ignore[attr-defined]


# -- slow is not silent (2026-09-25) ---------------------------------------------------------------


def test_a_slow_file_arrives_and_a_silent_one_does_not(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A file of the planet library weighs up to 61 MB (Tokyo's small roads) and its manifest
    28 MB. A limit on the whole transfer, 35 s as it first was, failed them below 14 and 6.5
    Mbit/s, and two such failures set the library aside for the whole build. What a hang looks
    like is silence, not slowness: the limit is on the time without a byte."""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from orthostudio.sources import library as lib

    monkeypatch.setattr(lib, "CONNECT_TIMEOUT_S", 0.5)
    monkeypatch.setattr(lib, "LIBRARY_TIMEOUT_S", 1.0)  # 1.5 s in all, as the first client had it
    body = bytes(range(256)) * 60  # 15 kB, sent over three seconds

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.path == "/trickle":  # slow and steady: 30 pieces, 0.1 s apart
                for i in range(30):
                    self.wfile.write(body[i * 512 : (i + 1) * 512])
                    self.wfile.flush()
                    time.sleep(0.1)
            else:  # the headers, then nothing: a server that has stopped answering
                self.wfile.flush()
                time.sleep(9.0)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        started = time.monotonic()
        slow, silent = lib._http_get_many([f"{base}/trickle", f"{base}/stall"], {})
        took = time.monotonic() - started
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert slow == (200, body), "a file still arriving is waited for"
    assert silent[0] == 0 and silent[1] == b"", "a silent one is given up"
    # curl measures the speed once a second, so silence is noticed a second or two after the
    # limit, and long before the nine seconds the server would have taken to hang up
    assert took < 6.0, f"the silent one was waited for {took:.1f} s"
