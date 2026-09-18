"""The second pass of the textures: a transient failure costs time, not the tile.

Spec: ``docs/specs/pipeline-textures.md`` section 4.1 and ``docs/specs/net-download.md`` R4. Most
tests replace ``orthostudio.net.Fetcher`` in the pipeline by a scripted fake, so that a chunk can
time out N times, a provider can die between two passes or a round can be abandoned without waiting
for real timeouts. Two tests use a local HTTP server instead, for what only the real fetcher does:
classifying a real timeout (and keeping curl's error line) and obeying a 429.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import io
import json
import os
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from orthostudio.imagery.chunks import ChunkStatus, ChunkStore
from orthostudio.imagery.grid import TextureId, texture_tiles
from orthostudio.imagery.providers import PlaceholderRule, Provider
from orthostudio.net import FetchRequest, FetchResult, FetchStats
from orthostudio.pipeline import textures as tex_mod
from orthostudio.pipeline.textures import (
    SECOND_PASS_ATTEMPTS,
    SECOND_PASS_GIVE_UP_AFTER,
    SECOND_PASS_IN_FLIGHT,
    SECOND_PASS_MAX_S,
    SECOND_PASS_PROBES,
    ProgressSnapshot,
    TextureJob,
    TexturesSpec,
    build_textures,
    write_report,
)
from orthostudio.textures.ter import TerKind

ZL = 6
T_LAND = TextureId(til_x=32, til_y=16, zl=ZL, provider="T")  # tiles x 32..47, y 16..31
T_OTHER = TextureId(til_x=16, til_y=16, zl=ZL, provider="T")  # tiles x 16..31, y 16..31
BAD = (ZL, 35, 19)  # chunk 3 * 16 + 3 of T_LAND
BAD_INDEX = 51
PLACEHOLDER_HEADER = "X-Test-Tile"
TIMEOUT_DETAIL = "curl: (28) Operation timed out after 20001 milliseconds with 0 bytes received."
RESET_DETAIL = "curl: (56) Recv failure: Connection reset by peer."
Tile = tuple[int, int, int]


def tile_png(z: int, x: int, y: int) -> bytes:
    colour = ((x * 37 + z * 11) % 200 + 20, (y * 59 + z * 7) % 200 + 20, (x * y) % 200 + 20)
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), colour).save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


# --- a scripted stand-in for
# orthostudio.net.Fetcher ------------------------------------------------------

_TILE_RE = re.compile(r"/tiles/(\d+)/(\d+)/(\d+)\.png$")


@dataclass
class Call:
    at: float
    phase: int
    request: FetchRequest
    result: FetchResult
    limit_in_flight: int | None
    max_attempts: int | None

    @property
    def tile(self) -> Tile:
        return _tile_of(self.request.url)

    @property
    def kind(self) -> str:
        """``c`` first pass, ``p`` parent, ``k`` probe, ``s`` second-pass round."""
        return str(self.request.key[0])


def _tile_of(url: str) -> Tile:
    m = _TILE_RE.search(url)
    assert m is not None, url
    z, x, y = (int(v) for v in m.groups())
    return z, x, y


Answer = Callable[[FetchRequest, Tile, int, int], FetchResult]
"""(request, tile, n-th request of that tile, phase) -> result; phase 0 is the first pass, then
the number of probe batches seen so far (the round of the second pass)."""


def ok(req: FetchRequest, tile: Tile) -> FetchResult:
    return FetchResult(
        req.key, 200, tile_png(*tile), {"content-type": "image/png"}, 0.05, 1, False, None
    )


def timeout(req: FetchRequest) -> FetchResult:
    return FetchResult(req.key, 0, b"", {}, 43.0, 4, True, "NET_TIMEOUT", TIMEOUT_DETAIL)


def reset(req: FetchRequest) -> FetchResult:
    return FetchResult(req.key, 0, b"", {}, 0.2, 2, False, "NET_CONNECTION_FAILED", RESET_DETAIL)


def server_error(req: FetchRequest, status: int) -> FetchResult:
    return FetchResult(req.key, status, b"oops", {}, 1.5, 4, False, "NET_SERVER_ERROR")


Delay = Callable[[FetchRequest, Tile, int, int], float]
"""Seconds a request takes in the concurrent mode of the fake (same arguments as ``Answer``)."""


class FakeFetcher:
    """Answers through ``answer`` and records each call and its run options.

    Sequential, every request answered at once, by default. With ``delay`` it runs up to
    ``limit_in_flight`` requests at a time, each taking ``delay(...)`` seconds, and a request
    still in flight when the run's cancel event is set is not delivered, as a transfer the
    real fetcher cancels.
    """

    def __init__(
        self, answer: Answer, log: list[Call], delay: Delay | None = None, **kwargs: Any
    ) -> None:
        self.answer = answer
        self.log = log
        self.delay = delay
        self.kwargs = kwargs
        self.hits: dict[Tile, int] = defaultdict(int)
        self.phase = 0
        self.in_flight = 0
        self.max_in_flight: dict[str, int] = defaultdict(int)
        """Most requests seen in flight at once, per request kind (``c``, ``k``, ``s``, ``p``)."""
        self._stats = FetchStats(0, 0, 0, 0, 0.0, 0, 0, 0, False)

    async def __aenter__(self) -> FakeFetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def stats(self) -> FetchStats:
        return self._stats

    async def fetch_many(
        self,
        requests: Iterable[FetchRequest],
        on_result: Callable[[FetchResult], None] | None = None,
        on_stats: Callable[[FetchStats], None] | None = None,
        cancel: asyncio.Event | None = None,
        *,
        keep_results: bool = True,
        limit_in_flight: int | None = None,
        max_attempts: int | None = None,
    ) -> list[FetchResult]:
        reqs = list(requests)
        if reqs and reqs[0].key[0] == "k":
            self.phase += 1
        if self.delay is not None:
            return await self._concurrent(
                reqs, on_result, on_stats, cancel, keep_results, limit_in_flight, max_attempts
            )
        out: list[FetchResult] = []
        done = errors = size = 0
        for req in reqs:
            await asyncio.sleep(0)
            if cancel is not None and cancel.is_set():
                out.append(FetchResult(req.key, 0, b"", {}, 0.0, 0, False, "SYS_CANCELLED"))
                continue
            tile = _tile_of(req.url)
            self.hits[tile] += 1
            result = self.answer(req, tile, self.hits[tile], self.phase)
            self.log.append(
                Call(time.monotonic(), self.phase, req, result, limit_in_flight, max_attempts)
            )
            done += 1
            errors += result.error is not None
            size += len(result.body)
            if on_result is not None:
                on_result(result)
            out.append(result if keep_results else dataclasses.replace(result, body=b""))
        self._stats = FetchStats(done, len(reqs), size, 0, 0.0, errors, 0, 0, False)
        if on_stats is not None:
            on_stats(self._stats)
        return out

    async def _concurrent(
        self,
        reqs: list[FetchRequest],
        on_result: Callable[[FetchResult], None] | None,
        on_stats: Callable[[FetchStats], None] | None,
        cancel: asyncio.Event | None,
        keep_results: bool,
        limit_in_flight: int | None,
        max_attempts: int | None,
    ) -> list[FetchResult]:
        assert self.delay is not None
        delay = self.delay
        slots = asyncio.Semaphore(limit_in_flight or int(self.kwargs.get("max_in_flight") or 16))
        results: list[FetchResult | None] = [None] * len(reqs)
        kind = str(reqs[0].key[0]) if reqs else ""

        async def one(k: int, req: FetchRequest) -> None:
            async with slots:
                if cancel is not None and cancel.is_set():
                    return
                tile = _tile_of(req.url)
                self.hits[tile] += 1
                n = self.hits[tile]
                self.in_flight += 1
                self.max_in_flight[kind] = max(self.max_in_flight[kind], self.in_flight)
                try:
                    seconds = delay(req, tile, n, self.phase)
                    if cancel is None:
                        await asyncio.sleep(seconds)
                    else:
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(cancel.wait(), seconds)
                finally:
                    self.in_flight -= 1
                if cancel is not None and cancel.is_set():
                    return  # cancelled in flight: never delivered
                result = self.answer(req, tile, n, self.phase)
                self.log.append(
                    Call(time.monotonic(), self.phase, req, result, limit_in_flight, max_attempts)
                )
                if on_result is not None:
                    on_result(result)
                results[k] = result if keep_results else dataclasses.replace(result, body=b"")

        await asyncio.gather(*(one(k, req) for k, req in enumerate(reqs)))
        out = [
            r if r is not None else FetchResult(req.key, 0, b"", {}, 0.0, 0, False, "SYS_CANCELLED")
            for r, req in zip(results, reqs, strict=True)
        ]
        errors = sum(1 for r in out if r.error is not None)
        self._stats = FetchStats(len(out), len(reqs), 0, 0, 0.0, errors, 0, 0, False)
        if on_stats is not None:
            on_stats(self._stats)
        return out


def install_fake(monkeypatch: pytest.MonkeyPatch, answer: Answer) -> list[Call]:
    log: list[Call] = []
    monkeypatch.setattr(tex_mod, "Fetcher", lambda **kw: FakeFetcher(answer, log, **kw))
    return log


def install_concurrent_fake(
    monkeypatch: pytest.MonkeyPatch, answer: Answer, delay: Delay
) -> tuple[list[Call], list[FakeFetcher]]:
    log: list[Call] = []
    made: list[FakeFetcher] = []

    def make(**kw: Any) -> FakeFetcher:
        made.append(FakeFetcher(answer, log, delay, **kw))
        return made[-1]

    monkeypatch.setattr(tex_mod, "Fetcher", make)
    return log, made


def fake_provider(template: str = "http://fake.invalid/tiles/{zoom}/{x}/{y}.png") -> Provider:
    return Provider(code="T", url_template=template, max_zl=19, max_in_flight=16)


def make_spec(tmp_path: Path, provider: Provider, **overrides: object) -> TexturesSpec:
    fields: dict[str, object] = dict(
        lat=43,
        lon=5,
        provider=provider,
        zl=ZL,
        jobs=[TextureJob(T_LAND, (TerKind.LAND,))],
        out_dir=tmp_path / "out",
        chunks_root=tmp_path / "chunks",
        store_root=tmp_path / "store",
        workers=0,
        quiet=True,
        fsync=False,
        max_in_flight=16,
        start_in_flight=8,
        hedge_after_s=2.0,
        timeout_s=5.0,
        max_attempts=2,
        second_pass_pauses_s=(0.05, 0.05, 0.05),
    )
    fields.update(overrides)
    return TexturesSpec(**fields)  # type: ignore[arg-type]


def calls_for(log: list[Call], tile: Tile) -> list[Call]:
    return [c for c in log if c.tile == tile and c.kind in ("c", "s")]


# --- 1. a transient failure is recovered, on another host, and counted ----------------------------


def test_chunk_failing_transiently_is_recovered_by_the_second_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def answer(req: FetchRequest, tile: Tile, n: int, phase: int) -> FetchResult:
        if tile == BAD and n <= 2:  # first pass and round 1 time out, round 2 answers
            return timeout(req)
        return ok(req, tile)

    log = install_fake(monkeypatch, answer)
    snapshots: list[ProgressSnapshot] = []
    provider = fake_provider("http://{switch:a,b,c}.fake.invalid/tiles/{zoom}/{x}/{y}.png")
    report = build_textures(make_spec(tmp_path, provider, progress=snapshots.append))

    assert report.ok, report.errors
    (o,) = report.outcomes
    assert o.status == "built" and o.second_pass == 1 and o.recovered == 1 and o.errors == 0
    c = report.counts
    assert c["chunks_second_pass"] == 1 and c["chunks_recovered"] == 1
    assert c["second_pass_rounds"] == 2 and c["tiles_error"] == 0
    assert not [e for e in report.errors if e["code"] in ("IMG_TILE_MISSING", "TEX_MISSING")]
    # what the chunk met is kept, recovered or not
    (f,) = o.failures
    assert f["chunk"] == BAD_INDEX and f["tile"] == [35, 19] and f["code"] == "NET_TIMEOUT"
    assert f["status"] == 0 and f["detail"] == TIMEOUT_DETAIL and f["recovered"] is True
    assert f["passes"] == 3 and f["transfers"] == 4 + 4 + 1

    bad = calls_for(log, BAD)
    assert [c.kind for c in bad] == ["c", "s", "s"]
    # each round asks another host of the {switch:} template ((35 + 19) % 3 = 0 first)
    hosts = [c.request.url.split("//")[1].split(".")[0] for c in bad]
    assert hosts == ["a", "b", "c"]
    # the rounds are polite: low concurrency, few attempts, a probe of known tiles first
    for call in log:
        if call.kind in ("s", "k"):
            assert call.limit_in_flight == SECOND_PASS_IN_FLIGHT
            assert call.max_attempts == SECOND_PASS_ATTEMPTS
    probes = [c for c in log if c.kind == "k"]
    assert len(probes) == 2 * SECOND_PASS_PROBES
    assert all(c.result.error is None and c.tile != BAD for c in probes)
    first_s = next(i for i, c in enumerate(log) if c.kind == "s")
    assert log[first_s - 1].kind == "k"

    # the container on disk is complete and holds the body of the recovered chunk
    container = ChunkStore(tmp_path / "chunks").read(T_LAND)
    assert container is not None and container.complete()
    assert container.get(BAD_INDEX).status is ChunkStatus.OK
    assert container.get(BAD_INDEX).data == tile_png(*BAD)
    assert (tmp_path / "out" / "textures" / "16_32_T6.dds").is_file()

    # the texture log says it in plain counts
    path = tmp_path / "textures.json"
    write_report(report, path)
    doc = json.loads(path.read_text())
    assert doc["counts"]["chunks_second_pass"] == 1 and doc["counts"]["chunks_recovered"] == 1
    assert doc["counts"]["second_pass_rounds"] == 2
    assert doc["timings"]["second_pass_s"] >= 0.1  # two pauses of 0.05 s
    assert doc["outcomes"][0]["second_pass"] == 1 and doc["outcomes"][0]["recovered"] == 1
    assert doc["outcomes"][0]["failures"][0]["code"] == "NET_TIMEOUT"


# --- 2. a chunk that never comes back ends TEX_MISSING after the bounded rounds -----------------


def test_chunk_failing_for_good_ends_incomplete_after_the_bounded_rounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def answer(req: FetchRequest, tile: Tile, n: int, phase: int) -> FetchResult:
        return reset(req) if tile == BAD else ok(req, tile)

    log = install_fake(monkeypatch, answer)
    t0 = time.perf_counter()
    report = build_textures(make_spec(tmp_path, fake_provider()))
    assert time.perf_counter() - t0 < 30

    assert not report.ok
    (o,) = report.outcomes
    assert o.status == "incomplete" and o.second_pass == 1 and o.recovered == 0 and o.errors == 1
    c = report.counts
    assert c["chunks_second_pass"] == 1 and c["chunks_recovered"] == 0
    assert c["second_pass_rounds"] == 3 and c["tiles_error"] == 1
    assert len(calls_for(log, BAD)) == 1 + 3  # the first pass, then one request per round
    err = o.error
    assert err is not None and err["code"] == "IMG_TILE_MISSING" and err["chunks"] == [BAD_INDEX]
    assert "NET_CONNECTION_FAILED" in err["message"]
    assert "still failing after 3 retry rounds" in err["message"]
    (f,) = err["failures"]
    assert f["code"] == "NET_CONNECTION_FAILED" and f["detail"] == RESET_DETAIL
    assert f["passes"] == 4 and f["recovered"] is False
    assert any(e["code"] == "TEX_MISSING" for e in report.errors)
    container = ChunkStore(tmp_path / "chunks").read(T_LAND)
    assert container is not None and container.indices(ChunkStatus.ERROR) == [BAD_INDEX]
    assert container.get(BAD_INDEX).content_type == "NET_CONNECTION_FAILED"
    assert not (tmp_path / "out" / "textures" / "16_32_T6.dds").exists()
    assert (tmp_path / "out" / "terrain" / "16_32_T6.ter").is_file()


# --- 3. a provider that does not answer known tiles stops the second pass -------------------------


def test_unanswered_probe_stops_the_second_pass_without_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def answer(req: FetchRequest, tile: Tile, n: int, phase: int) -> FetchResult:
        if tile == BAD or req.key[0] == "k":  # the provider died after the first pass
            return reset(req)
        return ok(req, tile)

    log = install_fake(monkeypatch, answer)
    t0 = time.perf_counter()
    report = build_textures(make_spec(tmp_path, fake_provider(), second_pass_pauses_s=(0.05, 30.0)))
    assert time.perf_counter() - t0 < 10  # the 30 s pause of round 2 is never taken

    (o,) = report.outcomes
    assert o.status == "incomplete" and o.second_pass == 1 and o.recovered == 0
    assert report.counts["second_pass_rounds"] == 0  # no chunk was asked again
    assert len(calls_for(log, BAD)) == 1
    assert len([c for c in log if c.kind == "k"]) == SECOND_PASS_PROBES
    assert (
        o.error is not None and "did not answer tiles it had already served" in o.error["message"]
    )


def test_nothing_answered_in_the_run_means_no_second_pass_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = install_fake(monkeypatch, lambda req, tile, n, phase: timeout(req))
    report = build_textures(make_spec(tmp_path, fake_provider(), second_pass_pauses_s=(0.05, 30.0)))
    (o,) = report.outcomes
    assert o.status == "incomplete" and o.second_pass == 256 and report.counts["tiles_error"] == 256
    assert len(log) == 256  # the first pass only: no probe, no round
    assert o.error is not None and "no tile of the run was answered" in o.error["message"]
    assert len(o.error["failures"]) == 8  # the detail is capped per texture


# --- 4. answers and final errors never wait for a second pass -----------------------------------


@pytest.mark.parametrize(
    ("code", "status", "retryable"),
    [
        ("NET_TIMEOUT", 0, True),
        ("NET_CONNECTION_FAILED", 0, True),
        ("NET_RATE_LIMITED", 429, True),
        ("NET_SERVER_ERROR", 502, True),
        ("NET_SERVER_ERROR", 503, True),
        ("NET_SERVER_ERROR", 504, True),
        ("NET_SERVER_ERROR", 500, False),
        ("NET_UNEXPECTED_STATUS", 403, False),
        ("IMG_BAD_CONTENT_TYPE", 200, False),
        ("IMG_TILE_CORRUPTED", 200, False),
        ("SYS_CANCELLED", 0, False),
    ],
)
def test_which_failures_get_a_second_pass(code: str, status: int, retryable: bool) -> None:
    failure = tex_mod._ChunkFailure(code, status, 4, False, 1.0, "")
    assert failure.retryable is retryable


def test_404_placeholder_and_final_errors_take_no_second_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = (ZL, 40, 20)  # T_LAND: a 404, filled from its parent
    placeholder = (ZL, 41, 21)  # T_LAND: a placeholder, filled from its parent
    final = (ZL, 20, 20)  # T_OTHER: a 500 ...
    transient = (ZL, 21, 21)  # ... and a timeout in the same texture: reported at once

    def answer(req: FetchRequest, tile: Tile, n: int, phase: int) -> FetchResult:
        if tile == missing:
            return FetchResult(req.key, 404, b"no tile", {}, 0.05, 1, False, None)
        if tile == placeholder:
            return FetchResult(
                req.key, 200, b"x", {PLACEHOLDER_HEADER.lower(): "no-tile"}, 0.05, 1, False, None
            )
        if tile == final:
            return server_error(req, 500)
        if tile == transient:
            return timeout(req)
        return ok(req, tile)

    log = install_fake(monkeypatch, answer)
    provider = Provider(
        code="T",
        url_template="http://fake.invalid/tiles/{zoom}/{x}/{y}.png",
        max_zl=19,
        max_in_flight=16,
        placeholder=PlaceholderRule(header_name=PLACEHOLDER_HEADER, header_value="no-tile"),
    )
    jobs = [TextureJob(T_LAND, (TerKind.LAND,)), TextureJob(T_OTHER, (TerKind.LAND,))]
    t0 = time.perf_counter()
    report = build_textures(make_spec(tmp_path, provider, jobs=jobs, second_pass_pauses_s=(30.0,)))
    assert time.perf_counter() - t0 < 20  # no pause was taken

    by_name = {o.name: o for o in report.outcomes}
    land, other = by_name["16_32_T6"], by_name["16_16_T6"]
    assert land.status == "built" and land.from_fallback == 2 and land.second_pass == 0
    assert other.status == "incomplete" and other.second_pass == 0
    assert report.counts["chunks_second_pass"] == 0 and report.counts["second_pass_rounds"] == 0
    assert not [c for c in log if c.kind in ("k", "s")]
    assert other.error is not None
    assert {f["code"] for f in other.error["failures"]} == {"NET_SERVER_ERROR", "NET_TIMEOUT"}
    assert {f["status"] for f in other.error["failures"]} == {500, 0}


def test_chunk_recovered_as_404_still_gets_its_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 obtained by a round is an answer: the parent round runs after it (section 5)."""

    def answer(req: FetchRequest, tile: Tile, n: int, phase: int) -> FetchResult:
        if tile == BAD:
            if n == 1:
                return timeout(req)
            return FetchResult(req.key, 404, b"no tile", {}, 0.05, 1, False, None)
        return ok(req, tile)

    log = install_fake(monkeypatch, answer)
    report = build_textures(make_spec(tmp_path, fake_provider()))
    assert report.ok, report.errors
    (o,) = report.outcomes
    assert o.status == "built" and o.recovered == 1 and o.not_found == 1 and o.from_fallback == 1
    assert report.counts["parents_total"] == 1
    parent = [c for c in log if c.kind == "p"]
    assert [c.tile for c in parent] == [(ZL - 1, 35 >> 1, 19 >> 1)]
    assert parent[0].at > max(c.at for c in log if c.kind == "s")


def test_partial_recovery_is_written_before_the_next_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No body is held between rounds: what round 1 obtained is on disk when round 2 starts."""
    early, late = BAD, (ZL, 36, 19)  # chunks 51 and 52 of T_LAND
    on_disk_at_round_2: list[ChunkStatus] = []

    def answer(req: FetchRequest, tile: Tile, n: int, phase: int) -> FetchResult:
        if req.key[0] == "k" and phase == 2 and not on_disk_at_round_2:
            container = ChunkStore(tmp_path / "chunks").read(T_LAND)
            assert container is not None
            on_disk_at_round_2.extend(container.get(i).status for i in (51, 52))
        if tile == early and phase == 0:
            return timeout(req)
        if tile == late and phase <= 1:
            return timeout(req)
        return ok(req, tile)

    install_fake(monkeypatch, answer)
    report = build_textures(make_spec(tmp_path, fake_provider()))
    assert report.ok, report.errors
    assert report.counts["chunks_recovered"] == 2 and report.counts["second_pass_rounds"] == 2
    assert on_disk_at_round_2 == [ChunkStatus.OK, ChunkStatus.ERROR]


# --- 5. cancellation during a pause ---------------------------------------------------------------


def test_cancel_during_a_second_pass_pause_returns_within_a_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(
        monkeypatch,
        lambda req, tile, n, phase: timeout(req) if tile == BAD else ok(req, tile),
    )
    cancel = threading.Event()
    cancelled_at: list[float] = []

    def progress(s: ProgressSnapshot) -> None:
        if s.second_pass_wait_s > 0 and not cancel.is_set():
            assert s.second_pass_chunks == 1 and s.second_pass_round == 1
            assert s.second_pass_rounds == 1 and s.eta_s is not None and s.eta_s >= 1.0
            cancelled_at.append(time.perf_counter())
            cancel.set()

    spec = make_spec(
        tmp_path, fake_provider(), second_pass_pauses_s=(30.0,), progress=progress, cancel=cancel
    )
    report = build_textures(spec)
    returned = time.perf_counter()
    assert cancelled_at, "the pause was never reported by the progress snapshots"
    assert returned - cancelled_at[0] < 1.0
    assert report.cancelled and report.outcomes[0].status == "cancelled"
    # the failed chunk stays ERROR on disk: the next run asks for it again
    container = ChunkStore(tmp_path / "chunks").read(T_LAND)
    assert container is not None and container.indices(ChunkStatus.ERROR) == [BAD_INDEX]


# --- 6. a round where nothing gets through is abandoned -----------------------------------------


def test_round_without_any_answer_is_abandoned_and_untried_chunks_go_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tiles = texture_tiles(T_LAND)
    stuck = {(ZL, *tiles[i]) for i in range(100, 120)}  # 20 chunks

    def answer(req: FetchRequest, tile: Tile, n: int, phase: int) -> FetchResult:
        if tile in stuck and phase <= 1:  # the first pass and round 1 fail, round 2 answers
            return timeout(req)
        return ok(req, tile)

    log = install_fake(monkeypatch, answer)
    report = build_textures(make_spec(tmp_path, fake_provider()))
    assert report.ok, report.errors
    c = report.counts
    assert c["chunks_second_pass"] == 20 and c["chunks_recovered"] == 20
    assert c["second_pass_rounds"] == 2
    round1 = [call.tile for call in log if call.kind == "s" and call.phase == 1]
    round2 = [call.tile for call in log if call.kind == "s" and call.phase == 2]
    in_order = [(ZL, *tiles[i]) for i in range(100, 120)]
    assert round1 == in_order[:SECOND_PASS_GIVE_UP_AFTER]  # abandoned after 8 failures
    assert round2 == in_order[SECOND_PASS_GIVE_UP_AFTER:] + in_order[:SECOND_PASS_GIVE_UP_AFTER]
    assert c["tiles_total"] == 256 + len(round1) + len(round2)


# --- 6b. many pending chunks: wider rounds, and a time limit for the whole second pass ------------

LAND_JOBS = [TextureJob(T_LAND, (TerKind.LAND,)), TextureJob(T_OTHER, (TerKind.LAND,))]


def many_failed(count_per_texture: int) -> set[Tile]:
    """The first ``count_per_texture`` chunks of T_LAND and of T_OTHER."""
    return {(ZL, *texture_tiles(t)[i]) for t in (T_LAND, T_OTHER) for i in range(count_per_texture)}


@pytest.mark.parametrize(
    ("pending", "ceiling", "expected"),
    [(1, 128, 8), (40, 128, 10), (400, 128, 64), (400, 16, 8), (2, 1, 1), (3000, 100, 50)],
)
def test_round_concurrency_scales_with_the_pending_chunks_within_half_the_ceiling(
    tmp_path: Path, pending: int, ceiling: int, expected: int
) -> None:
    pipeline = tex_mod._Pipeline(make_spec(tmp_path, fake_provider(), max_in_flight=ceiling))
    assert pipeline._round_limit(pending) == expected


def test_many_pending_chunks_finish_within_the_time_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """400 chunks after an outage: at 64 in flight the round takes 7 waves; at a fixed 8 it would
    take 50 waves (1 s) and the 0.6 s limit would end it. Windows sleeps in 15.6 ms steps, so a
    20 ms wave lasts about 31 ms there: 7 waves still fit in 1.2 s and 50 do not."""
    limit_s = 1.2 if os.name == "nt" else 0.6
    failed = many_failed(200)

    def answer(req: FetchRequest, tile: Tile, n: int, phase: int) -> FetchResult:
        return reset(req) if tile in failed and phase == 0 else ok(req, tile)

    def delay(req: FetchRequest, tile: Tile, n: int, phase: int) -> float:
        return 0.02 if req.key[0] in ("s", "k") else 0.0

    log, made = install_concurrent_fake(monkeypatch, answer, delay)
    spec = make_spec(
        tmp_path,
        fake_provider(),
        jobs=LAND_JOBS,
        max_in_flight=128,
        second_pass_pauses_s=(0.05, 0.05, 0.05),
        second_pass_max_s=limit_s,
    )
    report = build_textures(spec)
    assert report.ok, report.errors
    c = report.counts
    assert c["chunks_second_pass"] == 400 and c["chunks_recovered"] == 400
    assert c["second_pass_rounds"] == 1 and c["second_pass_capped"] == 0
    assert report.timings["second_pass_s"] < limit_s
    round1 = [call for call in log if call.kind == "s"]
    assert len(round1) == 400 and {call.limit_in_flight for call in round1} == {64}
    (fetcher,) = made
    assert fetcher.max_in_flight["s"] == 64  # the round used its width, and never more


def test_time_limit_ends_the_second_pass_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chunks that stall in every round: the limit cancels the round in flight, the pass ends,
    the chunks stay ERROR with their reason, and the log says the limit ended it."""
    failed = many_failed(40)

    def answer(req: FetchRequest, tile: Tile, n: int, phase: int) -> FetchResult:
        return timeout(req) if tile in failed else ok(req, tile)

    def delay(req: FetchRequest, tile: Tile, n: int, phase: int) -> float:
        return 30.0 if req.key[0] == "s" else 0.0  # a stall longer than the limit

    log, made = install_concurrent_fake(monkeypatch, answer, delay)
    spec = make_spec(
        tmp_path,
        fake_provider(),
        jobs=LAND_JOBS,
        max_in_flight=128,
        second_pass_pauses_s=(0.05, 0.05, 0.05),
        second_pass_max_s=0.5,
    )
    t0 = time.perf_counter()
    report = build_textures(spec)
    assert time.perf_counter() - t0 < 5.0
    c = report.counts
    assert c["second_pass_capped"] == 1 and c["second_pass_rounds"] == 1
    assert c["chunks_second_pass"] == 80 and c["chunks_recovered"] == 0
    assert 0.5 <= report.timings["second_pass_s"] < 1.5
    assert made[0].max_in_flight["s"] == 20  # 80 pending // 4, below 128 // 2
    assert not [call for call in log if call.kind == "s"]  # every retry was cut in flight
    assert all(o.status == "incomplete" for o in report.outcomes)
    for o in report.outcomes:
        assert o.error is not None and "reached its time limit (0.5 s)" in o.error["message"]
    assert any(e["code"] == "TEX_MISSING" for e in report.errors)
    container = ChunkStore(tmp_path / "chunks").read(T_LAND)
    assert container is not None and len(container.indices(ChunkStatus.ERROR)) == 40
    assert {container.get(i).content_type for i in range(40)} == {"NET_TIMEOUT"}
    path = tmp_path / "textures.json"
    write_report(report, path)
    assert json.loads(path.read_text())["counts"]["second_pass_capped"] == 1


def test_a_pause_ending_past_the_time_limit_is_not_waited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(
        monkeypatch, lambda req, tile, n, phase: reset(req) if tile == BAD else ok(req, tile)
    )
    spec = make_spec(
        tmp_path, fake_provider(), second_pass_pauses_s=(0.05, 30.0), second_pass_max_s=2.0
    )
    t0 = time.perf_counter()
    report = build_textures(spec)
    assert time.perf_counter() - t0 < 5.0  # neither the 30 s pause nor the rest of the 2 s limit
    c = report.counts
    assert c["second_pass_rounds"] == 1 and c["second_pass_capped"] == 1
    assert report.outcomes[0].status == "incomplete"
    assert report.timings["second_pass_s"] < 1.0


def test_default_time_limit_covers_three_stalled_rounds() -> None:
    """Three rounds that stall for their whole transfer and hedge (21 s) fit in the limit."""
    stalled_round_s = 20.0 + 1.0
    assert sum(tex_mod.SECOND_PASS_PAUSES_S) + 3 * stalled_round_s < SECOND_PASS_MAX_S


def test_unanswered_probe_sends_no_wide_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With 400 chunks waiting and room for 64 in flight, a line that is down gets two probe
    requests, not a burst of retries."""
    failed = many_failed(200)

    def answer(req: FetchRequest, tile: Tile, n: int, phase: int) -> FetchResult:
        return reset(req) if tile in failed or req.key[0] == "k" else ok(req, tile)

    log, _ = install_concurrent_fake(monkeypatch, answer, lambda req, tile, n, phase: 0.0)
    spec = make_spec(
        tmp_path,
        fake_provider(),
        jobs=LAND_JOBS,
        max_in_flight=128,
        second_pass_pauses_s=(0.05, 30.0),
    )
    t0 = time.perf_counter()
    report = build_textures(spec)
    assert time.perf_counter() - t0 < 10.0
    assert report.counts["chunks_second_pass"] == 400 and report.counts["second_pass_rounds"] == 0
    assert len([call for call in log if call.kind == "k"]) == SECOND_PASS_PROBES
    assert not [call for call in log if call.kind == "s"]


def test_cancel_during_a_wide_round_returns_within_a_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = many_failed(200)

    def answer(req: FetchRequest, tile: Tile, n: int, phase: int) -> FetchResult:
        return reset(req) if tile in failed and phase == 0 else ok(req, tile)

    def delay(req: FetchRequest, tile: Tile, n: int, phase: int) -> float:
        return 30.0 if req.key[0] == "s" else 0.0

    _, made = install_concurrent_fake(monkeypatch, answer, delay)
    cancel = threading.Event()
    cancelled_at: list[float] = []

    def trip() -> None:
        deadline = time.perf_counter() + 20
        while time.perf_counter() < deadline:
            if made and made[0].in_flight >= 64:
                cancelled_at.append(time.perf_counter())
                cancel.set()
                return
            time.sleep(0.01)

    threading.Thread(target=trip, daemon=True).start()
    spec = make_spec(
        tmp_path,
        fake_provider(),
        jobs=LAND_JOBS,
        max_in_flight=128,
        second_pass_pauses_s=(0.05,),
        cancel=cancel,
    )
    report = build_textures(spec)
    returned = time.perf_counter()
    assert cancelled_at, "the round never had 64 requests in flight"
    assert returned - cancelled_at[0] < 1.0
    assert report.cancelled and {o.status for o in report.outcomes} == {"cancelled"}
    assert report.counts["second_pass_capped"] == 0


# --- 7. the renderer shows the wait ---------------------------------------------------------------


def test_progress_line_shows_the_second_pass() -> None:
    s = ProgressSnapshot(
        tiles_done=256,
        tiles_total=256,
        parents_done=0,
        parents_total=0,
        req_per_s=0.0,
        bytes=0,
        in_flight=0,
        hedges=0,
        retries=0,
        net_errors=1,
        throttled=False,
        textures_total=1,
        built=0,
        hits=0,
        failed=0,
        incomplete=0,
        encoding=0,
        eta_s=12.0,
        elapsed_s=3.0,
        second_pass_chunks=1,
        second_pass_round=2,
        second_pass_rounds=3,
        second_pass_wait_s=12.0,
    )
    assert "retrying 1 chunk(s), round 2/3 in 12s" in tex_mod._Progress.render(s)
    waiting = dataclasses.replace(s, second_pass_round=0, second_pass_wait_s=0.0)
    assert "| 1 chunk(s) to retry |" in tex_mod._Progress.render(waiting)


# --- 8. with the real fetcher: a real timeout, a real 429 -----------------------------------------


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.hits: dict[Tile, int] = defaultdict(int)
        # per request of the tile: "200" "429" "502" "503" "hang" "slow" (0.15 s, then 200)
        self.script: dict[Tile, list[str]] = {}
        self.log: list[tuple[float, Tile, str]] = []
        self.slow_in_flight = 0
        self.slow_max = 0


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
        tile = _tile_of(self.path)
        st = self.state
        with st.lock:
            st.hits[tile] += 1
            script = st.script.get(tile, [])
            n = st.hits[tile]
            action = script[min(n, len(script)) - 1] if script else "200"
            st.log.append((time.monotonic(), tile, action))
        if action == "hang":
            time.sleep(3.0)
            return
        if action == "slow":
            with st.lock:
                st.slow_in_flight += 1
                st.slow_max = max(st.slow_max, st.slow_in_flight)
            time.sleep(0.15)  # below the 0.2 s latency floor of the AIMD: the window stays put
            with st.lock:
                st.slow_in_flight -= 1
        if action == "429":
            self._send(429, b"slow down", "text/plain", {"Retry-After": "1"})
        elif action in ("502", "503"):
            self._send(int(action), b"busy", "text/plain")
        else:
            self._send(200, tile_png(*tile), "image/png")


@pytest.fixture
def server() -> Iterator[tuple[str, _State]]:
    state = _State()
    handler = type("Handler", (_Handler,), {"state": state})
    ThreadingHTTPServer.request_queue_size = 128
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}", state
    httpd.shutdown()
    httpd.server_close()


def test_429_in_a_round_is_obeyed_and_is_not_a_failure(
    tmp_path: Path, server: tuple[str, _State]
) -> None:
    base, state = server
    state.script[BAD] = ["503", "429", "200"]  # first pass, then round 1: a 429, then the tile
    provider = fake_provider(base + "/tiles/{zoom}/{x}/{y}.png")
    report = build_textures(make_spec(tmp_path, provider, max_attempts=1))
    assert report.ok, report.errors
    (o,) = report.outcomes
    assert o.status == "built" and o.recovered == 1
    assert report.counts["second_pass_rounds"] == 1  # the 429 did not cost a round
    (f,) = o.failures
    assert f["code"] == "NET_SERVER_ERROR" and f["status"] == 503 and f["passes"] == 2
    assert f["transfers"] == 1 + 2  # the 503, then the 429 and the tile in one round
    seen = [(t, a) for t, tile, a in state.log if tile == BAD]
    assert [a for _, a in seen] == ["503", "429", "200"]
    assert seen[2][0] - seen[1][0] >= 0.95  # Retry-After: 1 obeyed


def test_real_timeout_is_classified_and_curls_error_line_kept(
    tmp_path: Path, server: tuple[str, _State]
) -> None:
    base, state = server
    state.script[BAD] = ["hang"]
    provider = fake_provider(base + "/tiles/{zoom}/{x}/{y}.png")
    spec = make_spec(
        tmp_path,
        provider,
        timeout_s=0.5,
        hedge_after_s=5.0,
        max_attempts=1,
        second_pass_pauses_s=(0.05,),
    )
    report = build_textures(spec)
    (o,) = report.outcomes
    assert o.status == "incomplete" and o.second_pass == 1
    assert o.error is not None
    (f,) = o.error["failures"]
    assert f["code"] == "NET_TIMEOUT" and f["status"] == 0
    assert f["detail"].startswith("curl: (28) ")
    assert f["passes"] == 2 and f["transfers"] == 1 + SECOND_PASS_ATTEMPTS
    assert state.hits[BAD] == 1 + SECOND_PASS_ATTEMPTS


def test_real_round_never_has_more_in_flight_than_its_limit(
    tmp_path: Path, server: tuple[str, _State]
) -> None:
    """160 chunks answered 502 in the first pass (a 502 does not halve the window), then 0.15 s
    each: the round asks min(max(8, 160 // 4), 64 // 2) = 32 at once on the wire, not more,
    and not the old fixed 8."""
    base, state = server
    for x, y in texture_tiles(T_LAND)[:160]:
        state.script[(ZL, x, y)] = ["502", "slow"]
    provider = fake_provider(base + "/tiles/{zoom}/{x}/{y}.png")
    spec = make_spec(
        tmp_path,
        provider,
        max_in_flight=64,
        start_in_flight=64,
        max_attempts=1,
        hedge_after_s=5.0,
        second_pass_pauses_s=(0.05,),
    )
    report = build_textures(spec)
    assert report.ok, report.errors
    assert report.counts["chunks_recovered"] == 160 and report.counts["second_pass_capped"] == 0
    assert SECOND_PASS_IN_FLIGHT < state.slow_max <= 32
