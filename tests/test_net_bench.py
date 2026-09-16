"""Real-network checks of ``orthostudio.net.fetch`` against the Bing imagery CDN (``-m network``).

Never run in CI. The tile set is the texture-aligned ZL16 grid of the +43+005 cell used by the
P0 benchmark (``docs/benchmarks/network.md``, s. 0): x 33664..33871, y 23824..24095, 56 576
tiles in texture order. Reference numbers from P0: 1 436 req/s at 128 in flight with
``curl --parallel`` and 0.32 ms CPU per request with curl_cffi at 64 in flight.

Skipped unless the run selects the marker (``-m network``) or ``OSXP_NETWORK_TESTS=1`` is set.
Run with ``-s`` to see the numbers::

    nice -n 10 uv run pytest tests/test_net_bench.py -m network -s
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from orthostudio.net import Fetcher, FetchRequest

resource = pytest.importorskip("resource", reason="getrusage is POSIX only")

pytestmark = pytest.mark.network


@pytest.fixture(autouse=True)
def _only_when_asked(request: pytest.FixtureRequest) -> None:
    wanted = "network" in (request.config.option.markexpr or "")
    if not wanted and os.environ.get("OSXP_NETWORK_TESTS") != "1":
        pytest.skip("real network: run with -m network or OSXP_NETWORK_TESTS=1")


BING_TEMPLATE = "https://ecn.t{n}.tiles.virtualearth.net/tiles/a{q}.jpeg?g=15312"
X_RANGE = range(33664, 33872)
Y_RANGE = range(23824, 24096)
ZL = 16


def quadkey(x: int, y: int, zl: int) -> str:
    """Bing quadkey of web-mercator tile (x, y) at zoom ``zl``."""
    digits = []
    for i in range(zl, 0, -1):
        mask = 1 << (i - 1)
        digits.append(str((1 if x & mask else 0) + (2 if y & mask else 0)))
    return "".join(digits)


def texture_order_quadkeys() -> list[str]:
    """All tiles of the cell, 256 tiles of one 16x16 texture then the next (a build's order)."""
    keys: list[str] = []
    for ty in range(Y_RANGE.start, Y_RANGE.stop, 16):
        for tx in range(X_RANGE.start, X_RANGE.stop, 16):
            for y in range(ty, ty + 16):
                for x in range(tx, tx + 16):
                    keys.append(quadkey(x, y, ZL))
    return keys


def requests_for(keys: list[str], offset: int, count: int) -> list[FetchRequest]:
    sub = keys[offset : offset + count]
    return [
        FetchRequest(key=q, url=BING_TEMPLATE.format(n=i % 4, q=q), host_group="BI")
        for i, q in enumerate(sub)
    ]


def cpu_seconds() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF)
    return ru.ru_utime + ru.ru_stime


def test_quadkey_matches_bing_documentation() -> None:
    assert quadkey(3, 5, 3) == "213"
    assert len(texture_order_quadkeys()) == 56_576


def test_bing_200_tiles_all_200_with_bodies() -> None:
    keys = texture_order_quadkeys()
    step = len(keys) // 200
    sample = [keys[i * step] for i in range(200)]
    reqs = [
        FetchRequest(key=q, url=BING_TEMPLATE.format(n=i % 4, q=q), host_group="BI")
        for i, q in enumerate(sample)
    ]

    async def go() -> tuple[list, object]:
        async with Fetcher(max_in_flight=64, start_in_flight=32, hedge_after_s=0.5) as f:
            res = await f.fetch_many(reqs)
            return res, f.stats()

    results, stats = asyncio.run(go())
    assert len(results) == 200
    bad = [r for r in results if r.status != 200 or r.error is not None or not r.body]
    assert not bad, bad[:3]
    placeholders = sum(1 for r in results if r.headers.get("x-ve-tile-info") == "no-tile")
    photos = [r for r in results if r.headers.get("x-ve-tile-info") != "no-tile"]
    assert all(len(r.body) > 1_000 for r in photos)  # uniform sea JPEGs are ~1.65 KB
    assert all("image/" in r.headers.get("content-type", "") for r in results)
    print(
        f"\n200 tiles: {stats.req_per_s:.0f} req/s (5 s window), {stats.bytes / 1e6:.2f} MB, "
        f"placeholders {placeholders}, hedges {stats.hedges}, retries {stats.retries}"
    )


def test_bench_5000_tiles_at_128_in_flight() -> None:
    """Short benchmark: req/s and CPU per request at the Bing ceiling (spec R7 gate)."""
    count = int(os.environ.get("OSXP_NET_BENCH_COUNT", "5000"))
    reqs = requests_for(texture_order_quadkeys(), 10_000, count)
    snapshots: list[float] = []

    async def go() -> tuple[list, object, float, float]:
        async with Fetcher(max_in_flight=128, start_in_flight=128, hedge_after_s=0.5) as f:
            cpu0, t0 = cpu_seconds(), time.perf_counter()
            res = await f.fetch_many(reqs, on_stats=lambda s: snapshots.append(s.req_per_s))
            wall, cpu = time.perf_counter() - t0, cpu_seconds() - cpu0
            return res, f.stats(), wall, cpu

    results, stats, wall, cpu = asyncio.run(go())
    assert len(results) == count and all(r.status == 200 and r.error is None for r in results)
    assert stats.done == count and stats.errors == 0
    load = getattr(os, "getloadavg", lambda: (0.0, 0.0, 0.0))()
    print(
        f"\n{count} tiles at 128 in flight: {wall:.2f} s, {count / wall:.0f} req/s, "
        f"{stats.bytes / wall / 1e6:.1f} MB/s, CPU {cpu / count * 1e3:.3f} ms/req "
        f"({cpu / wall:.2f} cores), hedges {stats.hedges}, retries {stats.retries}, "
        f"peak 5 s rate {max(snapshots):.0f} req/s, loadavg {load[0]:.1f}"
    )
    assert count / wall > 200  # far below this means HTTP/1.1 fallback or a broken client
