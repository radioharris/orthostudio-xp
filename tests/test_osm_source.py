"""Unit tests for ``orthostudio.sources.osm``: queries, snapshots, store, mirrors, client.

No network: the client is driven either by a scripted in-process transport or by a real
local HTTP server serving recorded Overpass JSON answers. Spec: ``docs/specs/osm-source.md``.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import orjson
import pytest

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.sources.osm import (
    LAYERS,
    MIRRORS,
    SNAPSHOT_FORMAT,
    CurlTransport,
    HttpReply,
    Mirror,
    MirrorBoard,
    MirrorHealth,
    OsmSnapshot,
    OverpassClient,
    SnapshotStore,
    layers_for,
    osm_progress_message,
    overpass_query,
    parse_overpass_json,
    snapshot_from_overpass,
    snapshot_label,
)

TILE = TileRef(43, 5)


# -- recorded answers ------------------------------------------------------------------------


def overpass_answer(elements: list[dict[str, object]], **extra: object) -> bytes:
    """A recorded Overpass JSON document with the usual envelope."""
    doc: dict[str, object] = {
        "version": 0.6,
        "generator": "Overpass API 0.7.62.11 87bfad18",
        "osm3s": {
            "timestamp_osm_base": "2026-09-11T20:46:21Z",
            "copyright": "The data included in this document is from www.openstreetmap.org.",
        },
        "elements": elements,
    }
    doc.update(extra)
    return orjson.dumps(doc)


COASTLINE_ELEMENTS: list[dict[str, object]] = [
    {"type": "node", "id": 101, "lat": 43.1000000, "lon": 5.1000000},
    {"type": "node", "id": 102, "lat": 43.2000000, "lon": 5.2000000},
    {"type": "node", "id": 103, "lat": 43.3000000, "lon": 5.3000000, "tags": {"place": "islet"}},
    {"type": "way", "id": 201, "nodes": [101, 102, 103], "tags": {"natural": "coastline"}},
]
COASTLINE = overpass_answer(COASTLINE_ELEMENTS)

WATER_ELEMENTS: list[dict[str, object]] = [
    {"type": "node", "id": 1, "lat": 43.5, "lon": 5.5},
    {"type": "node", "id": 2, "lat": 43.6, "lon": 5.5},
    {"type": "node", "id": 3, "lat": 43.6, "lon": 5.6},
    {"type": "way", "id": 11, "nodes": [1, 2, 3, 1]},
    {
        "type": "relation",
        "id": 21,
        "members": [
            {"type": "way", "ref": 11, "role": "outer"},
            {"type": "node", "ref": 1, "role": "label"},
        ],
        "tags": {"natural": "water", "name": 'Lac "bleu" & vert'},
    },
]
WATER = overpass_answer(WATER_ELEMENTS)


# -- layers and queries ----------------------------------------------------------------------


def test_layer_registry_matches_the_ortho4xp_selectors() -> None:
    assert LAYERS["airports"].selectors == (
        'node["aeroway"]',
        'way["aeroway"]',
        'rel["aeroway"]',
    )
    assert LAYERS["coastline"].selectors == ('way["natural"="coastline"]',)
    assert LAYERS["water"].selectors[0] == 'rel["natural"="water"]'
    assert LAYERS["big_roads"].selectors[-1] == 'way["railway"="narrow_gauge"]'


@pytest.mark.parametrize(
    ("road_level", "names"),
    [
        (0, ("airports", "coastline", "water")),
        (1, ("airports", "big_roads", "coastline", "water")),
        (2, ("airports", "big_roads", "small_roads", "coastline", "water")),
    ],
)
def test_layers_for_follows_road_level(road_level: int, names: tuple[str, ...]) -> None:
    assert {s.name for s in layers_for(road_level)} == set(names)


@pytest.mark.parametrize(("road_level", "count"), [(2, 1), (3, 3), (4, 4), (5, 5), (6, 5)])
def test_small_roads_selectors_grow_with_road_level(road_level: int, count: int) -> None:
    (small,) = [s for s in layers_for(road_level) if s.name == "small_roads"]
    assert len(small.selectors) == count
    assert small.selectors[0] == 'way["highway"="tertiary"]'


def test_overpass_query_is_json_with_the_ortho4xp_union() -> None:
    q = overpass_query(LAYERS["coastline"].selectors, TILE, 120)
    assert q == (
        '[out:json][timeout:120];(way["natural"="coastline"](43,5,44,6););(._;>>;);out body qt;'
    )


# -- mirrors ---------------------------------------------------------------------------------


def test_mirror_registry_follows_adr_0005() -> None:
    codes = [m.code for m in MIRRORS]
    assert codes == ["lz4", "fr", "mailru"]
    hosts = " ".join(m.interpreter for m in MIRRORS)
    assert "//overpass-api.de" not in hosts  # never the round-robin name
    assert "kumi" not in hosts and "osm.jp" not in hosts
    assert [m.last_resort for m in MIRRORS] == [False, False, True]
    assert MIRRORS[0].cluster == "de"


# -- parsing, digest, snapshot ---------------------------------------------------------------


def test_parse_overpass_json_splits_by_type() -> None:
    nodes, ways, relations = parse_overpass_json(WATER)
    assert [n.id for n in nodes] == [1, 2, 3]
    assert ways[0].nodes == (1, 2, 3, 1)
    assert relations[0].members[1].type == "node"
    assert relations[0].tags["name"] == 'Lac "bleu" & vert'


def test_parse_overpass_json_rejects_a_foreign_document() -> None:
    with pytest.raises(ValueError, match="Overpass"):
        parse_overpass_json(b'{"hello": 1}')


def test_digest_ignores_mirror_date_and_order() -> None:
    a = snapshot_from_overpass(TILE, "coastline", COASTLINE, mirror="lz4")
    shuffled = overpass_answer(list(reversed(COASTLINE_ELEMENTS)))
    b = snapshot_from_overpass(TILE, "coastline", shuffled, mirror="mailru")
    assert a.digest == b.digest
    assert len(a.digest) == 64


def test_digest_changes_when_a_tag_changes() -> None:
    a = snapshot_from_overpass(TILE, "coastline", COASTLINE)
    changed = json.loads(COASTLINE)
    changed["elements"][3]["tags"]["natural"] = "cliff"
    b = snapshot_from_overpass(TILE, "coastline", orjson.dumps(changed))
    assert a.digest != b.digest


def test_snapshot_json_round_trip() -> None:
    a = snapshot_from_overpass(TILE, "water", WATER, mirror="fr")
    b = OsmSnapshot.from_json(a.to_json())
    assert b == a
    assert orjson.loads(a.to_json())["format"] == SNAPSHOT_FORMAT
    assert a.counts == {"nodes": 3, "ways": 1, "relations": 1}


def test_snapshot_from_json_rejects_a_foreign_document() -> None:
    with pytest.raises(ValueError, match=SNAPSHOT_FORMAT):
        OsmSnapshot.from_json(b'{"format": "something-else"}')


def test_snapshot_label_is_stable_and_content_based() -> None:
    a = snapshot_from_overpass(TILE, "coastline", COASTLINE, mirror="lz4")
    again = snapshot_from_overpass(TILE, "coastline", COASTLINE, mirror="mailru")
    water = snapshot_from_overpass(TILE, "water", WATER)
    assert snapshot_label([a]) == snapshot_label([again])
    assert snapshot_label([a, water]) == snapshot_label([water, a])
    assert snapshot_label([a]) != snapshot_label([a, water])
    assert snapshot_label([a]).startswith("osm-")
    assert len(snapshot_label([a])) == 16
    assert snapshot_label([]) == "osm-empty"


# -- store -----------------------------------------------------------------------------------


def test_store_round_trip(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    snap = snapshot_from_overpass(TILE, "coastline", COASTLINE, mirror="lz4")
    path = store.save(snap)
    assert path == tmp_path / "osm" / "+40+000" / "+43+005" / "+43+005_coastline.osm.json.zst"
    assert store.load(TILE, "coastline") == snap
    assert store.digest_for(TILE, "coastline") == snap.digest
    meta = orjson.loads(store.meta_path_for(TILE, "coastline").read_bytes())
    assert meta["counts"]["ways"] == 1 and "elements" not in meta


def test_store_misses_return_none(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    assert store.load(TILE, "water") is None
    assert store.digest_for(TILE, "water") is None


def test_store_raises_on_a_corrupted_file(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    snap = snapshot_from_overpass(TILE, "coastline", COASTLINE)
    path = store.save(snap)
    path.write_bytes(b"not zstd at all")
    with pytest.raises(OsxpError) as err:
        store.load(TILE, "coastline")
    assert err.value.code == "OSM_CACHE_UNREADABLE"


# -- a scripted transport --------------------------------------------------------------------


class ScriptedTransport:
    """Answers per mirror host from a scripted queue; records what was sent."""

    def __init__(self, script: Mapping[str, list[HttpReply]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.sent: list[tuple[str, str]] = []
        self.in_flight = 0
        self.peak = 0
        self.by_host: dict[str, int] = {}
        self.peak_by_host: dict[str, int] = {}
        self.delay = 0.0
        self.host_delay: dict[str, float] = {}

    async def request(
        self,
        method: str,
        url: str,
        *,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 150.0,
    ) -> HttpReply:
        host = url.split("/")[2]
        self.sent.append((host, (data or {}).get("data", method)))
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        self.by_host[host] = self.by_host.get(host, 0) + 1
        self.peak_by_host[host] = max(self.peak_by_host.get(host, 0), self.by_host[host])
        try:
            delay = self.host_delay.get(host, self.delay)
            if delay:
                await asyncio.sleep(delay)
            queue = self.script.get(host)
            if not queue:
                return HttpReply(0, b"", {}, 0.0, "ScriptExhausted")
            return queue.pop(0) if len(queue) > 1 else queue[0]
        finally:
            self.in_flight -= 1
            self.by_host[host] -= 1

    async def aclose(self) -> None:
        return None


def ok(body: bytes = COASTLINE) -> HttpReply:
    return HttpReply(200, body, {"content-type": "application/json"}, 0.01)


def client(transport: ScriptedTransport, **kw: object) -> OverpassClient:
    params: dict[str, object] = {
        "attempt_delay_s": 0.0,
        "min_interval_s": 0.0,
        "cooldown_s": 60.0,
    }
    params.update(kw)
    return OverpassClient(MIRRORS, transport, **params)  # type: ignore[arg-type]


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


# -- client behaviour ------------------------------------------------------------------------


def test_first_mirror_answers() -> None:
    t = ScriptedTransport({"lz4.overpass-api.de": [ok()]})
    c = client(t)
    snap = asyncio.run(c.fetch_layer(TILE, "coastline"))
    assert snap.mirror == "lz4"
    assert snap.counts == {"nodes": 3, "ways": 1, "relations": 0}
    assert [a.mirror for a in c.attempts] == ["lz4"]
    assert c.health_snapshot()["lz4"].state == "closed"


def test_429_fails_over_and_opens_the_whole_cluster() -> None:
    t = ScriptedTransport(
        {
            "lz4.overpass-api.de": [HttpReply(429, b"", {"retry-after": "30"}, 0.01)],
            "overpass.openstreetmap.fr": [ok()],
        }
    )
    c = client(t)
    snap = asyncio.run(c.fetch_layer(TILE, "coastline"))
    assert snap.mirror == "fr"
    assert c.health_snapshot()["lz4"].state == "open"
    assert [a.error for a in c.attempts] == ["OSM_MIRROR_REJECTED", None]


def test_504_fails_over() -> None:
    t = ScriptedTransport(
        {
            "lz4.overpass-api.de": [HttpReply(504, b"gateway timeout", {}, 0.01)],
            "overpass.openstreetmap.fr": [ok()],
        }
    )
    c = client(t)
    assert asyncio.run(c.fetch_layer(TILE, "coastline")).mirror == "fr"
    assert c.health_snapshot()["lz4"].state == "open"


def test_transport_failure_fails_over() -> None:
    t = ScriptedTransport(
        {
            "lz4.overpass-api.de": [HttpReply(0, b"", {}, 5.0, "ConnectTimeout")],
            "overpass.openstreetmap.fr": [ok()],
        }
    )
    c = client(t)
    assert asyncio.run(c.fetch_layer(TILE, "coastline")).mirror == "fr"
    assert c.attempts[0].error == "OSM_MIRROR_UNREACHABLE"


def test_a_remark_answer_is_retried_elsewhere() -> None:
    remark = overpass_answer([], remark="runtime error: Query timed out in 'query' at line 1")
    t = ScriptedTransport(
        {
            "lz4.overpass-api.de": [HttpReply(200, remark, {}, 0.01)],
            "overpass.openstreetmap.fr": [ok()],
        }
    )
    c = client(t)
    assert asyncio.run(c.fetch_layer(TILE, "coastline")).mirror == "fr"
    assert c.attempts[0].error == "OSM_RESPONSE_ERROR"
    # a remark is not the mirror's fault: its breaker stays closed
    assert c.health_snapshot()["lz4"].state == "closed"


def test_a_truncated_body_is_retried_elsewhere() -> None:
    t = ScriptedTransport(
        {
            "lz4.overpass-api.de": [HttpReply(200, COASTLINE[:120], {}, 0.01)],
            "overpass.openstreetmap.fr": [ok()],
        }
    )
    c = client(t)
    assert asyncio.run(c.fetch_layer(TILE, "coastline")).mirror == "fr"
    assert c.attempts[0].error == "OSM_RESPONSE_TRUNCATED"


def test_an_empty_answer_is_a_valid_answer() -> None:
    t = ScriptedTransport({"lz4.overpass-api.de": [ok(overpass_answer([]))]})
    snap = asyncio.run(client(t).fetch_layer(TILE, "coastline"))
    assert snap.counts == {"nodes": 0, "ways": 0, "relations": 0}


def test_the_last_resort_mirror_is_used_last() -> None:
    t = ScriptedTransport(
        {
            "lz4.overpass-api.de": [HttpReply(504, b"", {}, 0.01)],
            "overpass.openstreetmap.fr": [HttpReply(504, b"", {}, 0.01)],
            "maps.mail.ru": [ok()],
        }
    )
    c = client(t)
    assert asyncio.run(c.fetch_layer(TILE, "coastline")).mirror == "mailru"
    assert [a.mirror for a in c.attempts] == ["lz4", "fr", "mailru"]


def test_the_last_resort_mirror_can_be_refused() -> None:
    t = ScriptedTransport(
        {
            "lz4.overpass-api.de": [HttpReply(504, b"", {}, 0.01)],
            "overpass.openstreetmap.fr": [HttpReply(504, b"", {}, 0.01)],
            "maps.mail.ru": [ok()],
        }
    )
    c = client(t, allow_last_resort=False)
    with pytest.raises(OsxpError) as err:
        asyncio.run(c.fetch_layer(TILE, "coastline"))
    assert err.value.code == "OSM_LAYER_UNAVAILABLE"
    assert "maps.mail.ru" not in {h for h, _ in t.sent}


def test_every_mirror_down_raises_osm_layer_unavailable() -> None:
    t = ScriptedTransport({})
    c = client(t)
    with pytest.raises(OsxpError) as err:
        asyncio.run(c.fetch_layer(TILE, "coastline"))
    assert err.value.code == "OSM_LAYER_UNAVAILABLE"
    assert err.value.context["layer"] == "coastline"
    assert err.value.context["tile"] == "+43+005"
    assert len(t.sent) == 3  # max_attempts, one per mirror, never eight on the same one


def test_the_breaker_keeps_a_dead_mirror_out_of_the_next_layer() -> None:
    t = ScriptedTransport(
        {
            "lz4.overpass-api.de": [HttpReply(504, b"", {}, 0.01)],
            "overpass.openstreetmap.fr": [ok()],
        }
    )
    c = client(t)
    asyncio.run(c.fetch_layer(TILE, "coastline"))
    asyncio.run(c.fetch_layer(TILE, "water"))
    assert [h for h, _ in t.sent] == [
        "lz4.overpass-api.de",
        "overpass.openstreetmap.fr",
        "overpass.openstreetmap.fr",
    ]


def test_the_breaker_half_opens_after_the_cooldown() -> None:
    t = ScriptedTransport(
        {
            "lz4.overpass-api.de": [HttpReply(504, b"", {}, 0.01), ok()],
            "overpass.openstreetmap.fr": [ok()],
        }
    )
    c = client(t, cooldown_s=0.05)
    asyncio.run(c.fetch_layer(TILE, "coastline"))
    assert c.health_snapshot()["lz4"].state == "open"
    asyncio.run(asyncio.sleep(0.06))
    assert c.health_snapshot()["lz4"].state == "half-open"
    assert asyncio.run(c.fetch_layer(TILE, "water")).mirror == "lz4"
    assert c.health_snapshot()["lz4"].state == "closed"


def test_no_more_than_two_requests_in_flight_per_cluster() -> None:
    """Two a cluster; each layer goes to the least busy one, so the two clusters answer four
    layers at once, and the last resort is not asked while they answer."""
    t = ScriptedTransport({"lz4.overpass-api.de": [ok()], "overpass.openstreetmap.fr": [ok()]})
    t.delay = 0.02
    c = client(t)
    got = asyncio.run(c.fetch_tile(TILE, road_level=2))
    assert set(got) == {"airports", "big_roads", "small_roads", "coastline", "water"}
    assert t.peak_by_host == {"lz4.overpass-api.de": 2, "overpass.openstreetmap.fr": 2}
    assert t.peak == 4 and "maps.mail.ru" not in {h for h, _ in t.sent}


def test_a_mirror_found_dead_is_not_waited_for_again() -> None:
    """2026-09-14: lz4 refused connections, and the four layers of every tile each waited for
    its 5 s connect timeout, then 5 s more before asking the next mirror. The layers waiting for
    lz4's slot now go elsewhere once it is found dead, at once, and so does the next tile."""
    t = ScriptedTransport(
        {
            "lz4.overpass-api.de": [HttpReply(0, b"", {}, 0.2, "ConnectTimeout")],
            "overpass.openstreetmap.fr": [ok()],
        }
    )
    t.host_delay = {"lz4.overpass-api.de": 0.2}
    board = MirrorBoard()
    c = client(t, attempt_delay_s=5.0, max_in_flight=1, board=board)

    async def timed(cl: OverpassClient) -> float:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await cl.fetch_tile(TILE, road_level=1)
        return loop.time() - t0

    assert asyncio.run(timed(c)) < 2.0  # no 5 s wait before another cluster's mirror
    hosts = [h for h, _ in t.sent]
    assert hosts.count("lz4.overpass-api.de") == 1  # the others did not queue behind it
    assert c.health_snapshot()["lz4"].state == "open"

    t.sent.clear()
    nxt = client(t, attempt_delay_s=5.0, board=board)  # the next tile of the build
    asyncio.run(timed(nxt))
    assert {h for h, _ in t.sent} == {"overpass.openstreetmap.fr"}
    assert client(t).health_snapshot()["lz4"].state == "closed"  # a board of its own


def test_the_tile_reports_each_layer_and_its_download_rate() -> None:
    t = ScriptedTransport({"lz4.overpass-api.de": [ok()], "overpass.openstreetmap.fr": [ok()]})
    t.delay = 0.01
    seen: list[tuple[float, str]] = []
    got = asyncio.run(
        client(t).fetch_tile(TILE, road_level=1, progress=lambda f, m: seen.append((f, m)))
    )
    assert len(got) == 4
    layer_reports = [f for f, _ in seen]
    assert [f for f in layer_reports if f in (0.25, 0.5, 0.75, 1.0)] == [0.25, 0.5, 0.75, 1.0]
    assert seen[-1][1].startswith("+43+005: 4/4 OSM layers (") and seen[-1][1].endswith(" MB/s)")
    assert osm_progress_message(TILE, 0, 4, 0, 3.0) == "+43+005: 0/4 OSM layers"
    assert osm_progress_message(TILE, 2, 4, 4_200_000, 3.0) == "+43+005: 2/4 OSM layers (1.4 MB/s)"


def test_the_minimum_interval_between_two_requests_is_respected() -> None:
    t = ScriptedTransport({"lz4.overpass-api.de": [ok()]})
    c = client(t, min_interval_s=0.05, max_in_flight=1)

    async def two() -> float:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await c.fetch_tile(TILE, layers=["coastline", "water"])
        return loop.time() - t0

    assert asyncio.run(two()) >= 0.05


# -- a local HTTP server ---------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Serves the recorded answers of ``server.plan`` (one entry per request path)."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:  # silence the test output
        return

    def _reply(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # BaseHTTPRequestHandler naming
        status, body, headers = self.server.plan(self.path, b"")  # type: ignore[attr-defined]
        self._reply(status, body, headers)

    def do_POST(self) -> None:  # BaseHTTPRequestHandler naming
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        status, body, headers = self.server.plan(self.path, payload)  # type: ignore[attr-defined]
        self._reply(status, body, headers)


@pytest.fixture
def local_overpass():  # type: ignore[no-untyped-def]
    """A local Overpass-like server; the test sets ``srv.answers`` per path."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.answers = {}  # type: ignore[attr-defined]
    srv.seen = []  # type: ignore[attr-defined]

    def plan(path: str, payload: bytes) -> tuple[int, bytes, dict[str, str]]:
        srv.seen.append((path, payload.decode()))  # type: ignore[attr-defined]
        return srv.answers.get(path, (404, b"{}", {}))  # type: ignore[attr-defined]

    srv.plan = plan  # type: ignore[attr-defined]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _local_mirrors(port: int) -> tuple[Mirror, ...]:
    base = f"http://127.0.0.1:{port}"
    return (
        Mirror("first", f"{base}/first/interpreter", "a", f"{base}/first/status"),
        Mirror("second", f"{base}/second/interpreter", "b", f"{base}/second/status"),
    )


def test_local_server_health_and_failover(local_overpass) -> None:  # type: ignore[no-untyped-def]
    port = local_overpass.server_address[1]
    local_overpass.answers = {
        "/first/status": (403, b"forbidden", {}),  # the .fr case: 403 does not disqualify
        "/second/status": (200, b"connected as: 1\n", {}),
        "/first/interpreter": (429, b"", {"Retry-After": "5"}),
        "/second/interpreter": (200, COASTLINE, {}),
    }
    c = OverpassClient(
        _local_mirrors(port),
        CurlTransport(),
        attempt_delay_s=0.0,
        min_interval_s=0.0,
        cooldown_s=30.0,
    )

    async def go() -> tuple[dict[str, MirrorHealth], OsmSnapshot]:
        async with c:
            health = await c.check_health()
            snap = await c.fetch_layer(TILE, "coastline")
            return health, snap

    health, snap = asyncio.run(go())
    assert health["first"].healthy is True and health["first"].status == 403
    assert health["second"].healthy is True
    assert snap.mirror == "second"
    assert snap.generator.startswith("Overpass API")
    assert snap.osm_base == "2026-09-11T20:46:21Z"
    sent = [body for path, body in local_overpass.seen if path.endswith("interpreter")]
    assert all(b.startswith("data=%5Bout%3Ajson%5D") for b in sent)


def test_local_server_unhealthy_mirror_is_put_aside(local_overpass) -> None:  # type: ignore[no-untyped-def]
    port = local_overpass.server_address[1]
    local_overpass.answers = {
        "/first/status": (503, b"", {}),
        "/second/status": (200, b"ok", {}),
        "/first/interpreter": (200, COASTLINE, {}),
        "/second/interpreter": (200, COASTLINE, {}),
    }
    c = OverpassClient(
        _local_mirrors(port), CurlTransport(), attempt_delay_s=0.0, min_interval_s=0.0
    )

    async def go() -> OsmSnapshot:
        async with c:
            await c.check_health()
            return await c.fetch_layer(TILE, "coastline")

    assert asyncio.run(go()).mirror == "second"
