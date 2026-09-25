"""Tests of the texture pipeline against a local tile server (spec pipeline-textures.md s. 10).

The server (a thread) serves deterministic flat-colour PNG tiles at ``/tiles/{z}/{x}/{y}.png``
and can be told to answer 404, a placeholder, a permanent 500 or a single 500 for given tiles.
Textures are built at ZL6 (tiles 16..47), which keeps the parent levels (ZL5..ZL1) real.
"""

from __future__ import annotations

import io
import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from orthostudio.graph import Store, digest_bytes
from orthostudio.imagery.chunks import ChunkContainer, ChunkStatus, ChunkStore
from orthostudio.imagery.grid import TextureId, texture_tiles
from orthostudio.imagery.providers import PlaceholderRule, Provider
from orthostudio.net import FetchResult
from orthostudio.pipeline import textures as textures_mod
from orthostudio.pipeline.parents import ParentCache, parents_blob, read_parents_blob
from orthostudio.pipeline.rule import RULE_NAME, TextureDdsParams, dds_key
from orthostudio.pipeline.textures import (
    TextureJob,
    TexturesSpec,
    build_textures,
    chunk_entry_for,
)
from orthostudio.textures.bcdecode import decode_dds
from orthostudio.textures.dds import parse_header
from orthostudio.textures.ter import TerKind, TerParams, ter_center, ter_filename, ter_text

ZL = 6
PLACEHOLDER_HEADER = "X-Test-Tile"

# --- tile server ----------------------


def tile_colour(z: int, x: int, y: int) -> tuple[int, int, int]:
    return ((x * 37 + z * 11) % 200 + 20, (y * 59 + z * 7) % 200 + 20, (x * y + z * 13) % 200 + 20)


def tile_png(z: int, x: int, y: int) -> bytes:
    im = Image.new("RGB", (256, 256), tile_colour(z, x, y))
    buf = io.BytesIO()
    im.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


_PLACEHOLDER_BODY = tile_png(0, 0, 0)[:200]  # not an image on purpose: never decoded


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.hits: dict[tuple[int, int, int], int] = defaultdict(int)
        self.not_found: set[tuple[int, int, int]] = set()
        self.placeholder: set[tuple[int, int, int]] = set()
        self.error_always: set[tuple[int, int, int]] = set()
        self.error_once: set[tuple[int, int, int]] = set()
        self.cache: dict[tuple[int, int, int], bytes] = {}

    def body(self, key: tuple[int, int, int]) -> bytes:
        data = self.cache.get(key)
        if data is None:
            data = self.cache[key] = tile_png(*key)
        return data

    def total_hits(self) -> int:
        return sum(self.hits.values())


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: _State

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _send(
        self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def do_GET(self) -> None:
        parts = self.path.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "tiles":
            self._send(400, b"bad route", "text/plain")
            return
        z, x = int(parts[1]), int(parts[2])
        y = int(parts[3].split(".")[0])
        key = (z, x, y)
        st = self.state
        with st.lock:
            st.hits[key] += 1
            count = st.hits[key]
        if key in st.not_found:
            self._send(404, b"no tile", "text/plain")
        elif key in st.placeholder:
            self._send(200, _PLACEHOLDER_BODY, "image/png", {PLACEHOLDER_HEADER: "no-tile"})
        elif key in st.error_always or (key in st.error_once and count == 1):
            self._send(500, b"boom", "text/plain")
        else:
            self._send(200, st.body(key), "image/png")


class TileServer:
    def __init__(self) -> None:
        self.state = _State()
        handler = type("Handler", (_Handler,), {"state": self.state})
        # The fetcher opens its first window (8 connections) at once: the default listen
        # backlog of 5 drops the extra SYNs, which macOS retransmits ~100 ms later, so a few
        # requests of the first burst look "late" on the server. A real provider has no such
        # backlog; give the test server room for the whole window.
        ThreadingHTTPServer.request_queue_size = 128
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        self.base = f"http://{host}:{port}"

    def provider(self) -> Provider:
        return Provider(
            code="T",
            url_template=f"{self.base}/tiles/{{zoom}}/{{x}}/{{y}}.png",
            max_zl=19,
            max_in_flight=16,
            placeholder=PlaceholderRule(header_name=PLACEHOLDER_HEADER, header_value="no-tile"),
        )

    def reset_hits(self) -> None:
        with self.state.lock:
            self.state.hits.clear()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server() -> Iterator[TileServer]:
    srv = TileServer()
    yield srv
    srv.close()


# --- helpers ----------------------

T_SEA = TextureId(til_x=16, til_y=16, zl=ZL, provider="T")
T_LAND = TextureId(til_x=32, til_y=16, zl=ZL, provider="T")
JOBS = [
    TextureJob(T_SEA, (TerKind.LAND, TerKind.WATER_OVERLAY, TerKind.SEA_OVERLAY)),
    TextureJob(T_LAND, (TerKind.LAND, TerKind.WATER_OVERLAY)),
]


@pytest.fixture
def mask_dir(tmp_path: Path) -> Path:
    """One mask for cell (16, 16) at ZL6: water (0) on the left half, land (255) on the right."""
    d = tmp_path / "masks"
    d.mkdir()
    mask = np.full((4096, 4096), 255, dtype=np.uint8)
    mask[:, :2048] = 0
    mask[:, 1900:2048] = np.linspace(0, 255, 148, dtype=np.uint8)[None, :]
    Image.fromarray(mask, mode="L").save(d / "16_16.png")
    return d


def make_spec(
    server: TileServer, tmp_path: Path, mask_dir: Path, **overrides: object
) -> TexturesSpec:
    def lookup(x: int, y: int) -> Path | None:
        p = mask_dir / f"{y}_{x}.png"
        return p if p.is_file() else None

    fields: dict[str, object] = dict(
        lat=43,
        lon=5,
        provider=server.provider(),
        zl=ZL,
        jobs=list(JOBS),
        out_dir=tmp_path / "out",
        chunks_root=tmp_path / "chunks",
        store_root=tmp_path / "store",
        mask_lookup=lookup,
        mask_zl=ZL,
        workers=0,
        quiet=True,
        fsync=False,
        max_in_flight=16,
        start_in_flight=8,
        hedge_after_s=2.0,
        timeout_s=5.0,
        max_attempts=2,
    )
    fields.update(overrides)
    return TexturesSpec(**fields)  # type: ignore[arg-type]


def chunk_means(dds_path: Path) -> np.ndarray:
    """Mean RGB of every 256² chunk of level 0, shape (16, 16, 3)."""
    level0 = decode_dds(dds_path.read_bytes())[0][:, :, :3]
    return level0.reshape(16, 256, 16, 256, 3).mean(axis=(1, 3))


def expect_colours(t: TextureId) -> np.ndarray:
    out = np.zeros((16, 16, 3))
    for i, (x, y) in enumerate(texture_tiles(t)):
        out[i // 16, i % 16] = tile_colour(t.zl, x, y)
    return out


# --- unit: classification, keys, blobs ----------------------


def _result(
    status: int, body: bytes, headers: dict[str, str] | None = None, error: str | None = None
) -> FetchResult:
    return FetchResult("k", status, body, headers or {}, 0.01, 1, False, error)


def test_chunk_entry_classification(server: TileServer) -> None:
    p = server.provider()
    png = tile_png(6, 1, 2)
    ok = chunk_entry_for(p, _result(200, png, {"content-type": "image/png"}))
    assert ok.status is ChunkStatus.OK and ok.data == png and ok.content_type == "image/png"
    assert chunk_entry_for(p, _result(404, b"")).status is ChunkStatus.MISSING
    ph = chunk_entry_for(p, _result(200, b"x", {PLACEHOLDER_HEADER.lower(): "no-tile"}))
    assert ph.status is ChunkStatus.PLACEHOLDER and ph.data == b""
    html = chunk_entry_for(p, _result(200, b"<html>", {"content-type": "text/html"}))
    assert html.status is ChunkStatus.ERROR and html.content_type == "IMG_BAD_CONTENT_TYPE"
    garbage = chunk_entry_for(p, _result(200, b"not an image", {"content-type": "image/jpeg"}))
    assert garbage.status is ChunkStatus.ERROR and garbage.content_type == "IMG_TILE_CORRUPTED"
    truncated = chunk_entry_for(p, _result(200, png[:300], {"content-type": "image/png"}))
    assert truncated.status is ChunkStatus.ERROR and truncated.content_type == "IMG_TILE_CORRUPTED"
    err = chunk_entry_for(p, _result(0, b"", error="NET_TIMEOUT"))
    assert err.status is ChunkStatus.ERROR and err.content_type == "NET_TIMEOUT"
    forbidden = chunk_entry_for(p, _result(403, b""))
    assert forbidden.status is ChunkStatus.ERROR
    assert forbidden.content_type == "NET_UNEXPECTED_STATUS"
    # the reason codes survive the container format (imagery-chunks.md section 3)
    c = ChunkContainer()
    for i, e in enumerate((html, garbage, err, forbidden)):
        c.set(i, e)
    back = ChunkContainer.from_bytes(c.to_bytes())
    assert [back.get(i).content_type for i in range(4)] == [
        "IMG_BAD_CONTENT_TYPE",
        "IMG_TILE_CORRUPTED",
        "NET_TIMEOUT",
        "NET_UNEXPECTED_STATUS",
    ]


def test_key_depends_on_inputs_and_params_only() -> None:
    base = TextureDdsParams(provider="BI", zl=14, encoder="ispc", encoder_version="1.0.1")
    k = dds_key(base, chunks_digest="00" * 32, mask_digest=None, parents_digest=None)
    assert k == dds_key(base, chunks_digest="00" * 32, mask_digest=None, parents_digest=None)
    assert k != dds_key(base, chunks_digest="11" * 32, mask_digest=None, parents_digest=None)
    assert k != dds_key(base, chunks_digest="00" * 32, mask_digest="22" * 32, parents_digest=None)
    assert k != dds_key(base, chunks_digest="00" * 32, mask_digest=None, parents_digest="33" * 32)
    crop = base.model_copy(update={"mask_crop": (0, 0, 1024)})
    assert k != dds_key(crop, chunks_digest="00" * 32, mask_digest=None, parents_digest=None)
    blur = base.model_copy(update={"sea_texture_blur": 1.0})
    assert k != dds_key(blur, chunks_digest="00" * 32, mask_digest=None, parents_digest=None)
    assert RULE_NAME == "texture.dds"


def test_parents_blob_round_trip_and_cache(tmp_path: Path) -> None:
    from orthostudio.pipeline.parents import ParentTile

    t = TextureId(16, 16, 6, "T")
    parents = {(8, 8, 5): ParentTile(b"jpegbytes"), (4, 4, 4): ParentTile(None)}
    blob = parents_blob(t, parents)
    t_back, back = read_parents_blob(blob)
    assert (t_back.til_x, t_back.til_y, t_back.zl) == (16, 16, 6)
    assert back == {(8, 8, 5): b"jpegbytes", (4, 4, 4): None}
    # the blob is order-independent and content-addressed
    assert parents_blob(t, dict(reversed(list(parents.items())))) == blob
    cache = ParentCache(tmp_path, "T")
    assert cache.lookup(8, 8, 5) is None
    cache.put(8, 8, 5, b"body")
    cache.put(9, 8, 5, None)
    fresh = ParentCache(tmp_path, "T")
    assert fresh.lookup(8, 8, 5) == ParentTile(b"body")
    assert fresh.lookup(9, 8, 5) == ParentTile(None) and fresh.lookup(9, 8, 5).is_tombstone
    # dette D3: parents live in an ordinary ChunkContainer at the parent level (the tiles no
    # chain reached are NOT_FETCHED), not in one flat file per tile.
    from orthostudio.imagery.chunks import ChunkContainer, ChunkStatus

    blob_path = tmp_path / "T" / "_parents" / "5" / "0_0.chunks"
    assert blob_path.is_file() and cache.path(8, 8, 5) == blob_path
    saved = ChunkContainer.from_bytes(blob_path.read_bytes())
    assert saved.get(8 * 16 + 8) == saved.get(cache._index(cache.texture_id(8, 8, 5), 8, 8))
    assert saved.get(8 * 16 + 8).status is ChunkStatus.OK
    assert saved.get(8 * 16 + 8).data == b"body"
    assert saved.get(8 * 16 + 9).status is ChunkStatus.MISSING  # tombstone of (9, 8, 5)
    assert len(saved.indices(ChunkStatus.NOT_FETCHED)) == 254


def test_container_digest_ignores_fetch_time() -> None:
    from orthostudio.imagery.chunks import ChunkEntry

    a = ChunkContainer()
    b = ChunkContainer()
    a.set(0, ChunkEntry(ChunkStatus.OK, b"abc", "image/png", 1))
    b.set(0, ChunkEntry(ChunkStatus.OK, b"abc", "image/png", 999))
    assert a.digest() == b.digest()
    assert digest_bytes(a.to_bytes()) != digest_bytes(b.to_bytes())


# --- P1: cold, warm, no-change, parameter change --------------------------------------------------


@pytest.mark.parametrize("workers", [0, 2])
def test_cold_then_warm_runs(
    server: TileServer, tmp_path: Path, mask_dir: Path, workers: int
) -> None:
    spec = make_spec(server, tmp_path, mask_dir, workers=workers)
    report = build_textures(spec)
    assert report.ok, report.errors
    assert report.counts["built"] == 2 and report.counts["hits"] == 0
    assert report.counts["tiles_fetched"] == 512 and server.state.total_hits() == 512
    out = tmp_path / "out"
    sea_dds = out / "textures" / "16_16_T6.dds"
    land_dds = out / "textures" / "16_32_T6.dds"
    assert sea_dds.stat().st_size == 22_369_776 and parse_header(sea_dds.read_bytes()).fmt == "bc3"
    assert (
        land_dds.stat().st_size == 11_184_952 and parse_header(land_dds.read_bytes()).fmt == "bc1"
    )
    assert (out / "textures" / "water_transition.png").is_file()
    # pixels: every chunk has the colour the server gave it (BC1 quantisation only)
    assert np.abs(chunk_means(land_dds) - expect_colours(T_LAND)).max() < 5
    # alpha of the masked texture: water on the left, land on the right
    alpha = decode_dds(sea_dds.read_bytes())[0][:, :, 3]
    assert alpha[:, :1800].max() == 0 and alpha[:, 2100:].min() == 255
    # .ter files, byte-identical to ter_text
    for job in JOBS:
        lat, lon = ter_center(job.texture)
        for kind in job.kinds:
            path = out / "terrain" / ter_filename(job.texture, kind)
            assert path.read_text() == ter_text(
                job.texture, kind, lat_med=lat, lon_med=lon, params=TerParams()
            )
    assert len(list((out / "terrain").glob("*.ter"))) == 5
    # containers are in the chunk store, complete
    cs = ChunkStore(tmp_path / "chunks")
    for job in JOBS:
        c = cs.read(job.texture)
        assert c is not None and c.complete() and len(c.indices(ChunkStatus.OK)) == 256
    # the artefacts are in the store, hard-linked into the output
    store = Store(tmp_path / "store")
    assert len(store) == 2
    for o in report.outcomes:
        assert o.status == "built" and o.key and store.has(o.key)
        assert Path(o.dds_path).stat().st_nlink == 2 or not spec.link
    store.close()

    # warm run: no request, nothing encoded
    server.reset_hits()
    report2 = build_textures(make_spec(server, tmp_path, mask_dir, workers=workers))
    assert report2.ok
    assert server.state.total_hits() == 0
    assert report2.counts["hits"] == 2 and report2.counts["built"] == 0
    assert report2.counts["tiles_fetched"] == 0 and report2.counts["tiles_cached"] == 512
    assert [o.key for o in report2.outcomes] == [o.key for o in report.outcomes]

    # a parameter consumed by the masked texture only re-encodes that texture
    report3 = build_textures(
        make_spec(server, tmp_path, mask_dir, workers=workers, sea_texture_blur=200.0)
    )
    assert report3.ok and server.state.total_hits() == 0
    by_name = {o.name: o for o in report3.outcomes}
    assert by_name["16_16_T6"].status == "built" and by_name["16_32_T6"].status == "hit"


# --- P2: parent fallback ----------------------


def test_parent_fallback_and_placeholder_chain(
    server: TileServer, tmp_path: Path, mask_dir: Path
) -> None:
    st = server.state
    # chunk (18, 20) of T_LAND... use T_LAND tiles x 32..47, y 16..31
    st.not_found.add((ZL, 33, 17))  # parent (16, 8, 5) exists
    st.placeholder.add((ZL, 40, 24))  # parent (20, 12, 5) is a placeholder too -> (10, 6, 4)
    st.placeholder.add((ZL - 1, 20, 12))
    st.not_found.add((ZL, 46, 30))
    st.not_found.add((ZL, 47, 30))  # same parent (23, 15, 5) as (46, 30): fetched once
    spec = make_spec(server, tmp_path, mask_dir, jobs=[JOBS[1]])
    report = build_textures(spec)
    assert report.ok, report.errors
    o = report.outcomes[0]
    assert o.status == "built" and o.from_fallback == 4 and o.unfilled == 0
    assert o.not_found == 3 and o.placeholders == 1
    assert report.counts["parents_total"] == 4  # (16,8,5) (20,12,5) (23,15,5) then (10,6,4)
    assert st.hits[(5, 23, 15)] == 1 and st.hits[(5, 16, 8)] == 1 and st.hits[(4, 10, 6)] == 1
    means = chunk_means(tmp_path / "out" / "textures" / "16_32_T6.dds")
    expected = expect_colours(T_LAND)
    expected[1, 1] = tile_colour(5, 16, 8)
    expected[8, 8] = tile_colour(4, 10, 6)
    expected[14, 14] = tile_colour(5, 23, 15)
    expected[14, 15] = tile_colour(5, 23, 15)
    assert np.abs(means - expected).max() < 5
    # the parent cache remembers bodies and tombstones
    cache = ParentCache(tmp_path / "chunks", "T")
    assert cache.lookup(20, 12, 5) is not None and cache.lookup(20, 12, 5).is_tombstone
    assert cache.lookup(16, 8, 5).body is not None
    # warm run: the container is complete (404/placeholder are answers), parents come from the cache
    server.reset_hits()
    report2 = build_textures(make_spec(server, tmp_path, mask_dir, jobs=[JOBS[1]]))
    assert report2.ok and st.total_hits() == 0
    assert report2.outcomes[0].status == "hit" and report2.outcomes[0].key == o.key
    # parent cache lost: the complete container still gets its parents fetched again (same key)
    import shutil

    shutil.rmtree(tmp_path / "chunks" / "T" / "_parents")
    server.reset_hits()
    report3 = build_textures(make_spec(server, tmp_path, mask_dir, jobs=[JOBS[1]]))
    assert report3.ok and st.total_hits() == 4 and report3.counts["parents_fetched"] == 4
    assert report3.outcomes[0].status == "hit" and report3.outcomes[0].key == o.key


def test_chunk_without_any_parent_is_reported(
    server: TileServer, tmp_path: Path, mask_dir: Path
) -> None:
    st = server.state
    st.not_found.add((ZL, 33, 17))
    for d in range(1, 6):
        st.not_found.add((ZL - d, 33 >> d, 17 >> d))
    report = build_textures(make_spec(server, tmp_path, mask_dir, jobs=[JOBS[1]]))
    o = report.outcomes[0]
    assert o.status == "built" and o.unfilled == 1 and o.from_fallback == 0
    assert report.counts["parents_total"] == 5
    assert any(e["code"] == "IMG_TILE_MISSING" for e in report.errors)
    assert not report.missing  # the texture exists, degraded


# --- P3: errors, incomplete container, retry ------------------------------------------------------


def test_error_tile_leaves_texture_incomplete_then_retry_fetches_only_it(
    server: TileServer, tmp_path: Path, mask_dir: Path
) -> None:
    st = server.state
    st.error_always.add((ZL, 35, 19))
    st.error_once.add((ZL, 36, 19))  # retried by the fetcher itself
    spec = make_spec(server, tmp_path, mask_dir, jobs=list(JOBS))
    report = build_textures(spec)
    assert not report.ok
    by_name = {o.name: o for o in report.outcomes}
    land, sea = by_name["16_32_T6"], by_name["16_16_T6"]
    assert sea.status == "built"
    assert land.status == "incomplete" and land.errors == 1
    assert land.error is not None and land.error["code"] == "IMG_TILE_MISSING"
    assert any(e["code"] == "TEX_MISSING" for e in report.errors)
    out = tmp_path / "out"
    assert not (out / "textures" / "16_32_T6.dds").exists()
    assert (out / "terrain" / "16_32_T6.ter").is_file()  # the DSF references it
    container = ChunkStore(tmp_path / "chunks").read(T_LAND)
    assert container is not None and container.indices(ChunkStatus.ERROR) == [3 * 16 + 3]
    assert st.hits[(ZL, 36, 19)] == 2
    # the provider recovers: only the failed tile is fetched
    st.error_always.clear()
    server.reset_hits()
    report2 = build_textures(make_spec(server, tmp_path, mask_dir, jobs=list(JOBS)))
    assert report2.ok, report2.errors
    assert st.total_hits() == 1 and st.hits[(ZL, 35, 19)] == 1
    by_name2 = {o.name: o for o in report2.outcomes}
    assert by_name2["16_32_T6"].status == "built" and by_name2["16_16_T6"].status == "hit"
    assert (out / "textures" / "16_32_T6.dds").stat().st_size == 11_184_952


def test_sea_kind_without_mask_is_failed_without_download(
    server: TileServer, tmp_path: Path
) -> None:
    """Spec section 3 (P1 review): a WET overlay over a DXT1 texture would hide X-Plane's water,
    a situation Ortho4XP never produces, so the texture is failed (no DDS, no request), its ``.ter``
    are still written and the run ends with ``TEX_MISSING``."""
    empty = tmp_path / "nomasks"
    empty.mkdir()
    report = build_textures(make_spec(server, tmp_path, empty, jobs=[JOBS[0]]))
    assert not report.ok
    o = report.outcomes[0]
    assert o.status == "failed" and o.error is not None and o.error["code"] == "MASK_STALE"
    assert "masks" in o.error["remedy"]
    assert server.state.total_hits() == 0 and report.counts["tiles_total"] == 0
    assert not (tmp_path / "out" / "textures" / "16_16_T6.dds").exists()
    assert (tmp_path / "out" / "terrain" / "16_16_T6_sea_overlay.ter").is_file()
    assert any(e["code"] == "TEX_MISSING" for e in report.errors)
    # the land texture of the same run is built normally
    report2 = build_textures(make_spec(server, tmp_path, empty, out_dir=tmp_path / "out2"))
    by_name = {o.name: o for o in report2.outcomes}
    assert by_name["16_32_T6"].status == "built" and by_name["16_16_T6"].status == "failed"
    assert server.state.total_hits() == 256


def test_progress_callback_and_cancel(server: TileServer, tmp_path: Path, mask_dir: Path) -> None:
    snapshots: list[object] = []
    cancel = threading.Event()

    def progress(s: object) -> None:
        snapshots.append(s)

    report = build_textures(make_spec(server, tmp_path, mask_dir, progress=progress))
    assert report.ok and snapshots
    last = snapshots[-1]
    assert last.tiles_total == 512 and last.textures_total == 2  # type: ignore[attr-defined]
    # cancellation before the run: everything is cancelled, nothing is fetched
    cancel.set()
    server.reset_hits()
    t0 = time.perf_counter()
    report2 = build_textures(make_spec(server, tmp_path / "again", mask_dir, cancel=cancel))
    assert time.perf_counter() - t0 < 5.0
    assert report2.cancelled and not report2.ok


def test_downloads_start_at_the_ceiling_of_the_provider(
    server: TileServer, tmp_path: Path, mask_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user's tile at Esri Clarity climbed from 106 to 330 requests/s over 90 s (2026-09-15):
    the downloads started at 64 in flight and gained one per round up to 192."""
    started: list[tuple[int, int]] = []
    real = textures_mod.Fetcher

    class Recording(real):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)  # type: ignore[arg-type]
            started.append((self.max_in_flight, self.start_in_flight))

    monkeypatch.setattr(textures_mod, "Fetcher", Recording)
    spec = make_spec(server, tmp_path, mask_dir, start_in_flight=None, max_in_flight=16)
    report = build_textures(spec)
    assert report.ok and started and all(pair == (16, 16) for pair in started)


def test_the_build_obeys_the_rate_the_provider_names(
    server: TileServer, tmp_path: Path, mask_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``server_req_per_s`` set the estimate alone until 0.1.14: a user who wrote 3 in his own
    source to spare a small server watched the build ask for hundreds a second and get blocked
    (2026-09-24). It is now the ceiling the fetcher starts requests at."""
    seen: list[float | None] = []
    real = textures_mod.Fetcher

    class Recording(real):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)  # type: ignore[arg-type]
            seen.append(self.req_per_s)

    monkeypatch.setattr(textures_mod, "Fetcher", Recording)
    slow = server.provider().model_copy(update={"server_req_per_s": 500.0})
    report = build_textures(make_spec(server, tmp_path, mask_dir, provider=slow))
    assert report.ok and seen and all(rate == 500.0 for rate in seen)


def test_fetcher_cancelled_before_workers_start_returns(server: TileServer) -> None:
    """Regression: a cancel arriving right after dispatch used to leave the fetcher hanging."""
    import asyncio

    from orthostudio.net import Fetcher, FetchRequest

    async def run() -> int:
        cancel = asyncio.Event()
        fetcher = Fetcher(max_in_flight=8, start_in_flight=8, timeout_s=5.0)
        reqs = [
            FetchRequest(key=i, url=f"{server.base}/tiles/6/{16 + i % 16}/{16 + i // 16}.png")
            for i in range(200)
        ]

        async def trip() -> None:
            await asyncio.sleep(0)  # dispatch has just created its first tasks
            cancel.set()

        async with fetcher:
            _, results = await asyncio.gather(trip(), fetcher.fetch_many(reqs, cancel=cancel))
        return sum(1 for r in results if r.error == "SYS_CANCELLED")

    t0 = time.perf_counter()
    cancelled = asyncio.run(asyncio.wait_for(run(), timeout=15))
    assert time.perf_counter() - t0 < 5.0
    assert cancelled >= 1


def test_running_out_of_open_files_is_not_called_a_permission() -> None:
    """A user's textures failed with "Too many open files", and the page said "Fix permissions on
    the path" with no path (2026-09-15): the words name what happened."""
    import errno

    err = textures_mod._coded(OSError(errno.EMFILE, "Too many open files"))
    assert err.code == "SYS_WRITE_FAILED" and "Too many files were open" in err.message
    assert "permission" not in err.remedy.lower() and "Retry the missing ones" in err.remedy
    other = textures_mod._coded(PermissionError(errno.EACCES, "Permission denied", "/tiles/x.dds"))
    assert "permission" in other.remedy.lower() and "/tiles/x.dds" in other.message


def test_a_texture_the_source_has_nothing_for_is_not_shipped_as_grey(
    server: TileServer, tmp_path: Path, mask_dir: Path
) -> None:
    """Every piece refused, so the assembler painted the whole square with the mean of its
    filled neighbours -- of which there were none -- and the texture was called *built*: 11 MB
    of flat grey, installed, with nothing said. A square outside what a source covers, or a
    level finer than it holds, lands here for every texture of the tile (found in review,
    2026-09-23)."""
    st = server.state
    for job in JOBS:
        for x, y in texture_tiles(job.texture):
            st.not_found.add((ZL, x, y))
            for up in range(1, 8):  # and every parent it would fall back to
                st.not_found.add((ZL - up, x >> up, y >> up))

    report = build_textures(make_spec(server, tmp_path, mask_dir, workers=1))
    assert not report.ok, "a tile whose imagery does not exist is not a tile"
    assert report.counts["built"] == 0 and report.counts["incomplete"] == len(JOBS)

    for outcome in report.outcomes:
        assert outcome.status == "incomplete"
        assert outcome.dds_path is None, "nothing is published"
    assert not list((tmp_path / "out" / "textures").glob("*.dds"))

    said = " ".join(str(e.get("message", "")) for e in report.errors)
    assert "no imagery at all" in said
    remedies = " ".join(str(e.get("remedy", "")) for e in report.errors)
    assert "outside what this source covers" in remedies and "lower level" in remedies


def test_a_source_of_the_users_under_the_same_name_with_another_address_is_asked_again(
    tmp_path: Path, mask_dir: Path
) -> None:
    """A source of the user's is named after what they typed, and its downloads were kept under
    that name: remove it, add another of the same name with another address (or change the address
    in `sources.toml`), and the build reused the first address's images without asking the new one
    once. The address now names the folder of a source of the user's; a shipped source keeps its
    code, so nothing it already downloaded moves.
    """
    first, second = TileServer(), TileServer()
    try:

        def mine(srv: TileServer) -> Provider:
            return srv.provider().model_copy(update={"code": "Mine", "custom": True})

        jobs = [TextureJob(T_LAND._replace(provider="Mine"), (TerKind.LAND,))]
        spec = make_spec(first, tmp_path, mask_dir, provider=mine(first), jobs=jobs)
        assert build_textures(spec).ok
        assert first.state.total_hits() == 256

        again = make_spec(second, tmp_path, mask_dir, provider=mine(second), jobs=jobs)
        report = build_textures(again)
        assert report.ok
        assert second.state.total_hits() == 256, "the new address must be asked, not the old kept"
        assert report.counts["tiles_cached"] == 0

        # and the same address twice is the same folder: nothing asked the second time
        second.reset_hits()
        assert build_textures(again).ok and second.state.total_hits() == 0
    finally:
        first.close()
        second.close()
