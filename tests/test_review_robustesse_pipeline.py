"""Adversarial review of the P1 texture pipeline: failures in the middle of a run.

A local tile server (thread) can be told to rate-limit, to slow down, to die (connections
dropped, listener closed) or to refuse a tile for good, and it timestamps every request.
The tests that encoded a defect at review time (429 storm, swallowed OSError, parent round
waiting for the encoders, retained bodies, Ctrl-C, duplicate jobs) now pass: their ``xfail``
markers were removed by the P1 fixes.
"""

from __future__ import annotations

import io
import multiprocessing
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from orthostudio.imagery.chunks import ChunkStatus, ChunkStore
from orthostudio.imagery.grid import TextureId
from orthostudio.imagery.providers import PlaceholderRule, Provider
from orthostudio.pipeline import textures as tex_mod
from orthostudio.pipeline.textures import TextureJob, TexturesSpec, build_textures
from orthostudio.textures.ter import TerKind

ZL = 6
PLACEHOLDER_HEADER = "X-Test-Tile"
Key = tuple[int, int, int]


def tile_png(z: int, x: int, y: int) -> bytes:
    colour = ((x * 37 + z * 11) % 200 + 20, (y * 59 + z * 7) % 200 + 20, (x * y) % 200 + 20)
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), colour).save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.log: list[tuple[float, Key, int]] = []  # (monotonic, tile, status sent)
        self.hits: dict[Key, int] = defaultdict(int)
        self.not_found: set[Key] = set()
        self.error_always: set[Key] = set()
        self.cache: dict[Key, bytes] = {}
        self.rate_limit_first_n = 0  # the first N requests get 429 + Retry-After: 1
        self.delay_s = 0.0
        self.die_after: int | None = None  # after N requests: drop connections, close listener
        self.dead = False
        self.on_hit: threading.Event | None = None
        self.on_hit_at = 0
        self.total = 0

    def body(self, key: Key) -> bytes:
        data = self.cache.get(key)
        if data is None:
            data = self.cache[key] = tile_png(*key)
        return data

    def total_hits(self) -> int:
        return sum(self.hits.values())

    def hits_with_status(self, status: int) -> list[float]:
        return [t for t, _, s in self.log if s == status]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: _State
    server_ref: TileServer

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _send(self, key: Key, status: int, body: bytes, ctype: str, extra: dict | None) -> None:
        with self.state.lock:
            self.state.log.append((time.monotonic(), key, status))
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
        z, x = int(parts[1]), int(parts[2])
        y = int(parts[3].split(".")[0])
        key = (z, x, y)
        st = self.state
        with st.lock:
            st.hits[key] += 1
            st.total += 1
            n = st.total
            rate_limited = n <= st.rate_limit_first_n
            if st.die_after is not None and n > st.die_after:
                st.dead = True
        if st.on_hit is not None and n >= st.on_hit_at:
            st.on_hit.set()
        if st.dead:
            self.server_ref.kill_listener()
            self.close_connection = True  # no answer at all: the peer sees a dropped connection
            return
        if st.delay_s:
            time.sleep(st.delay_s)
        if rate_limited:
            self._send(key, 429, b"slow down", "text/plain", {"Retry-After": "1"})
        elif key in st.not_found:
            self._send(key, 404, b"no tile", "text/plain", None)
        elif key in st.error_always:
            self._send(key, 500, b"boom", "text/plain", None)
        else:
            self._send(key, 200, st.body(key), "image/png", None)


class TileServer:
    def __init__(self, port: int = 0, state: _State | None = None) -> None:
        self.state = state or _State()
        handler = type("Handler", (_Handler,), {"state": self.state, "server_ref": self})
        ThreadingHTTPServer.allow_reuse_address = True
        # The fetcher opens its first window (8 connections) at once: the default listen
        # backlog of 5 drops the extra SYNs, which macOS retransmits ~100 ms later, so a few
        # requests of the first burst look "late" on the server. A real provider has no such
        # backlog; give the test server room for the whole window.
        ThreadingHTTPServer.request_queue_size = 128
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._killed = False
        self._kill_lock = threading.Lock()

    def kill_listener(self) -> None:
        with self._kill_lock:
            if self._killed:
                return
            self._killed = True
        threading.Thread(target=self._shutdown, daemon=True).start()

    def _shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def provider(self) -> Provider:
        return Provider(
            code="T",
            url_template=f"{self.base}/tiles/{{zoom}}/{{x}}/{{y}}.png",
            max_zl=19,
            max_in_flight=16,
            placeholder=PlaceholderRule(header_name=PLACEHOLDER_HEADER, header_value="no-tile"),
        )

    def close(self) -> None:
        if not self._killed:
            self._killed = True
            self.httpd.shutdown()
            self.httpd.server_close()


@pytest.fixture
def server() -> Iterator[TileServer]:
    srv = TileServer()
    yield srv
    srv.close()


T_SEA = TextureId(til_x=16, til_y=16, zl=ZL, provider="T")
T_LAND = TextureId(til_x=32, til_y=16, zl=ZL, provider="T")
JOBS = [
    TextureJob(T_SEA, (TerKind.LAND, TerKind.WATER_OVERLAY, TerKind.SEA_OVERLAY)),
    TextureJob(T_LAND, (TerKind.LAND, TerKind.WATER_OVERLAY)),
]


@pytest.fixture
def mask_dir(tmp_path: Path) -> Path:
    d = tmp_path / "masks"
    d.mkdir()
    mask = np.full((4096, 4096), 255, dtype=np.uint8)
    mask[:, :2048] = 0
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


def _no_child_processes() -> bool:
    return not multiprocessing.active_children()


# --- 1. the provider dies in the middle of the run, then comes back ------------------------------


def test_server_dies_mid_run_containers_persist_and_resume_fetches_only_the_rest(
    server: TileServer, tmp_path: Path, mask_dir: Path
) -> None:
    st = server.state
    st.die_after = 300  # T_SEA complete (256), T_LAND 44 tiles in, then nothing answers
    t0 = time.perf_counter()
    report = build_textures(make_spec(server, tmp_path, mask_dir, workers=2))
    # Windows reports a refused connection to a local port after about two seconds (its TCP stack
    # retries the SYN), where POSIX answers at once: the same retries take twice as long there.
    assert time.perf_counter() - t0 < (90 if os.name == "nt" else 30)
    assert _no_child_processes()
    assert not report.ok
    by_name = {o.name: o for o in report.outcomes}
    assert by_name["16_16_T6"].status == "built"
    land = by_name["16_32_T6"]
    assert land.status == "incomplete" and land.errors > 0
    assert any(e["code"] == "TEX_MISSING" for e in report.errors)
    # what was received is on disk, the rest is ERROR
    cs = ChunkStore(tmp_path / "chunks")
    c = cs.read(T_LAND)
    assert c is not None and not c.complete()
    errors = c.indices(ChunkStatus.ERROR)
    oks = c.indices(ChunkStatus.OK)
    assert len(errors) + len(oks) == 256 and len(oks) >= 40
    assert not (tmp_path / "out" / "textures" / "16_32_T6.dds").exists()
    assert (tmp_path / "out" / "terrain" / "16_32_T6.ter").is_file()
    # the provider comes back on the same port: only the ERROR tiles are fetched
    again = TileServer(port=server.port)
    try:
        report2 = build_textures(make_spec(again, tmp_path, mask_dir, workers=2))
        assert report2.ok, report2.errors
        assert again.state.total_hits() == len(errors)
        by2 = {o.name: o for o in report2.outcomes}
        assert by2["16_16_T6"].status == "hit" and by2["16_32_T6"].status == "built"
        assert (tmp_path / "out" / "textures" / "16_32_T6.dds").stat().st_size == 11_184_952
    finally:
        again.close()


# --- 2. a 429 storm in the middle of the run ------------------------------------------------------


def test_429_pause_is_polite_no_dispatch_during_retry_after(
    server: TileServer, tmp_path: Path, mask_dir: Path
) -> None:
    st = server.state
    st.rate_limit_first_n = 40
    build_textures(make_spec(server, tmp_path, mask_dir, max_attempts=4))
    limited = st.hits_with_status(429)
    assert len(limited) == 40
    # Politeness: nothing new is dispatched during a Retry-After pause. A request that arrives
    # well after a 429 (the local round trip is ~1 ms) but before that 429's Retry-After
    # expired was dispatched during the pause.
    all_hits = sorted(t for t, _, _ in st.log)
    violations = [t for t in all_hits if any(t429 + 0.05 < t < t429 + 0.95 for t429 in limited)]
    assert violations == [], f"{len(violations)} requests dispatched during a Retry-After pause"


def test_429_storm_longer_than_max_attempts_does_not_lose_tiles(
    server: TileServer, tmp_path: Path, mask_dir: Path
) -> None:
    st = server.state
    st.rate_limit_first_n = 40  # window 8 -> 4 rounds of 429 for the same 8 requests, +1 s each
    report = build_textures(make_spec(server, tmp_path, mask_dir, max_attempts=4))
    assert report.ok, [e["code"] for e in report.errors]
    assert report.counts["tiles_error"] == 0


# --- 3. cancellation in the middle of the download, then resume ---------------------------------


def test_cancel_mid_download_keeps_received_tiles_and_resumes(
    server: TileServer, tmp_path: Path, mask_dir: Path
) -> None:
    st = server.state
    st.delay_s = 0.02
    st.on_hit = threading.Event()
    st.on_hit_at = 120
    cancel = threading.Event()

    def trip() -> None:
        st.on_hit.wait(20)  # type: ignore[union-attr]
        cancel.set()

    threading.Thread(target=trip, daemon=True).start()
    t0 = time.perf_counter()
    report = build_textures(make_spec(server, tmp_path, mask_dir, workers=2, cancel=cancel))
    assert time.perf_counter() - t0 < 15
    assert report.cancelled and not report.ok
    assert _no_child_processes()
    total_before = st.total_hits()
    assert 120 <= total_before < 512
    cs = ChunkStore(tmp_path / "chunks")
    c = cs.read(T_SEA)
    assert c is not None and not c.complete()
    oks = len(c.indices(ChunkStatus.OK))
    assert oks >= 100
    # resume: only what was not received is fetched (the fetcher may have answered a few more
    # than the container recorded when the cancel landed; never fewer)
    st.delay_s = 0.0
    st.total = 0
    st.hits.clear()
    report2 = build_textures(make_spec(server, tmp_path, mask_dir, workers=2))
    assert report2.ok, report2.errors
    assert st.total_hits() == 512 - oks
    assert report2.counts["tiles_cached"] == oks


# --- 4. an OSError while publishing must be reported with a code, not swallowed ---------------


@pytest.mark.skipif(
    getattr(os, "geteuid", lambda: 1)() == 0, reason="root ignores directory permissions"
)
@pytest.mark.skipif(os.name == "nt", reason="Windows ignores the write bit of a directory")
def test_unwritable_terrain_dir_gives_a_coded_error(
    server: TileServer, tmp_path: Path, mask_dir: Path
) -> None:
    out = tmp_path / "out"
    (out / "terrain").mkdir(parents=True)
    (out / "textures").mkdir()
    os.chmod(out / "terrain", stat.S_IRUSR | stat.S_IXUSR)
    try:
        report = build_textures(make_spec(server, tmp_path, mask_dir, jobs=[JOBS[1]]))
    finally:
        os.chmod(out / "terrain", stat.S_IRWXU)
    assert not report.ok
    o = report.outcomes[0]
    assert o.status == "failed"
    # the PermissionError raised by write_ter_files is reported with a code and the path
    assert o.error is not None, {e["code"] for e in report.errors}
    assert o.error["code"] in {"SYS_WRITE_FAILED", "SYS_DISK_FULL", "SYS_PERMISSION_DENIED"}
    assert "terrain" in o.error["context"]["path"]


# --- 5. parent rounds must not wait for the encoding of the first round --------------------------


def test_parent_round_starts_without_waiting_for_the_encoders(
    server: TileServer, tmp_path: Path, mask_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = server.state
    st.not_found.add((ZL, 33, 17))  # T_LAND needs parent (16, 8, 5)
    real = tex_mod._worker_run
    encode_started: list[float] = []

    def slow_worker(job: tex_mod.WorkerJob) -> tex_mod.WorkerResult:
        encode_started.append(time.monotonic())
        time.sleep(2.0)
        return real(job)

    monkeypatch.setattr(tex_mod, "_worker_run", slow_worker)
    report = build_textures(make_spec(server, tmp_path, mask_dir, jobs=list(JOBS)))
    assert report.ok, report.errors
    parent_hit = [t for t, k, _ in st.log if k == (5, 16, 8)]
    assert len(parent_hit) == 1
    last_child = max(t for t, k, _ in st.log if k[0] == ZL)
    # the sea texture is complete early and its (slow) encode must not delay the parent fetch
    assert encode_started and encode_started[0] < parent_hit[0]
    assert parent_hit[0] - last_child < 1.0, (
        f"parent fetched {parent_hit[0] - last_child:.1f} s after the last child tile"
    )


# --- 6. memory: the parent process must not keep 4 MB of bodies per finished texture ------------


def test_finished_textures_release_their_chunk_bodies(
    server: TileServer, tmp_path: Path, mask_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tex_mod._Pipeline] = []
    real_run = tex_mod._Pipeline.run

    async def run(self: tex_mod._Pipeline) -> tex_mod.TexturesReport:
        captured.append(self)
        return await real_run(self)

    monkeypatch.setattr(tex_mod._Pipeline, "run", run)
    report = build_textures(make_spec(server, tmp_path, mask_dir))
    assert report.ok and captured
    held = {
        st.outcome.name: sum(len(e.data) for e in st.container.entries)
        for st in captured[0].states
        if st.outcome is not None and st.outcome.status == "built"
    }
    assert held and max(held.values()) == 0, f"bodies still held at the end of the run: {held}"


# --- 7. Ctrl-C in the CLI path (asyncio.run + KeyboardInterrupt) --------------------------------

_DRIVER = """
import sys, threading
from pathlib import Path
from orthostudio.imagery.providers import Provider
from orthostudio.imagery.grid import TextureId
from orthostudio.pipeline.textures import TextureJob, TexturesSpec, build_textures
from orthostudio.textures.ter import TerKind
base, root = sys.argv[1], Path(sys.argv[2])
prov = Provider(code="T", url_template=base + "/tiles/{zoom}/{x}/{y}.png", max_zl=19,
                max_in_flight=16)
jobs = [TextureJob(TextureId(16, 16, 6, "T"), (TerKind.LAND,)),
        TextureJob(TextureId(32, 16, 6, "T"), (TerKind.LAND,))]
spec = TexturesSpec(lat=43, lon=5, provider=prov, zl=6, jobs=jobs, out_dir=root / "out",
                    chunks_root=root / "chunks", store_root=root / "store", workers=1, quiet=True,
                    fsync=False, max_in_flight=16, start_in_flight=8, timeout_s=5.0, max_attempts=2)
print("READY", flush=True)
report = build_textures(spec)
print("DONE", report.cancelled, report.counts, flush=True)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT delivery differs on Windows")
def test_keyboard_interrupt_persists_received_tiles_and_exits_cleanly(
    server: TileServer, tmp_path: Path
) -> None:
    st = server.state
    st.delay_s = 0.02
    st.on_hit = threading.Event()
    st.on_hit_at = 100
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER)
    proc = subprocess.Popen(
        [sys.executable, str(driver), server.base, str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    try:
        assert st.on_hit.wait(60), "the driver never reached 100 requests"
        proc.send_signal(signal.SIGINT)
        _out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    hits = st.total_hits()
    assert 100 <= hits < 512
    assert _no_child_processes()
    cs = ChunkStore(tmp_path / "chunks")
    c = cs.read(T_SEA)
    # SIGINT takes the cooperative cancel path: received tiles persisted, report returned
    assert c is not None, f"Ctrl-C lost {hits} received tiles (rc={proc.returncode}, {err})"
    assert len(c.indices(ChunkStatus.OK)) >= 90
    assert "Traceback" not in err
    assert proc.returncode == 0 and "DONE True" in _out


# --- 8. duplicate jobs ------------------------------------------------------------------------


def test_duplicate_texture_jobs_are_fetched_once(
    server: TileServer, tmp_path: Path, mask_dir: Path
) -> None:
    report = build_textures(make_spec(server, tmp_path, mask_dir, jobs=[JOBS[1], JOBS[1]]))
    assert report.ok
    assert server.state.total_hits() == 256
    assert len(report.outcomes) == 1 and report.counts["textures"] == 1
