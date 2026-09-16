"""Step 3: Python HTTP clients on the same tiles: httpx (HTTP/2 and HTTP/1.1) versus curl_cffi.

Measures wall time, requests per second and CPU per request (``resource.getrusage`` of this
process, user + system) for a fixed number of tile downloads at a fixed concurrency. Run it
against tiles the CDN already has at the edge (a ``g=`` value used by a previous curl run) so
that the client, not the CDN, is what is measured.

Usage::

    uv run python tools/bench/network/py_clients.py --quadkeys FILE --count 5000 --offset 10000 \\
        --inflight 64 --generation 15311
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import resource
import time
from pathlib import Path
from typing import Any

import httpx
from common import (
    BING_HOSTS,
    SCRATCH,
    bing_url,
    dump_json,
    load_quadkeys,
    machine_snapshot,
    summarize_latencies,
)


def cpu_seconds() -> float:
    """User + system CPU consumed by this process so far."""
    ru = resource.getrusage(resource.RUSAGE_SELF)
    return ru.ru_utime + ru.ru_stime


async def run_httpx(
    urls: list[str], inflight: int, *, http2: bool, trust_env: bool, timeout: float
) -> dict[str, Any]:
    """Download all URLs with one httpx.AsyncClient and ``inflight`` concurrent requests."""
    sem = asyncio.Semaphore(inflight)
    limits = httpx.Limits(max_connections=inflight, max_keepalive_connections=inflight)
    codes: collections.Counter[Any] = collections.Counter()
    versions: collections.Counter[str] = collections.Counter()
    lat: list[float] = []
    total = 0

    async def one(client: httpx.AsyncClient, url: str) -> None:
        nonlocal total
        async with sem:
            t0 = time.perf_counter()
            try:
                r = await client.get(url)
            except httpx.HTTPError as exc:
                codes[type(exc).__name__] += 1
                return
            lat.append(time.perf_counter() - t0)
            codes[r.status_code] += 1
            versions[r.http_version] += 1
            total += len(r.content)

    cpu0, t0 = cpu_seconds(), time.perf_counter()
    async with httpx.AsyncClient(
        http2=http2, trust_env=trust_env, timeout=timeout, limits=limits
    ) as client:
        await asyncio.gather(*(one(client, u) for u in urls))
    wall, cpu = time.perf_counter() - t0, cpu_seconds() - cpu0
    return _summary(
        f"httpx-{'h2' if http2 else 'h1'}-trust_env={trust_env}",
        urls,
        wall,
        cpu,
        total,
        codes,
        versions,
        lat,
    )


async def run_curl_cffi(urls: list[str], inflight: int, *, timeout: float) -> dict[str, Any]:
    """Download all URLs with curl_cffi.requests.AsyncSession (libcurl multi, HTTP/2 by ALPN)."""
    from curl_cffi.requests import AsyncSession, RequestsError

    sem = asyncio.Semaphore(inflight)
    codes: collections.Counter[Any] = collections.Counter()
    versions: collections.Counter[str] = collections.Counter()
    lat: list[float] = []
    total = 0

    async def one(session: AsyncSession, url: str) -> None:
        nonlocal total
        async with sem:
            t0 = time.perf_counter()
            try:
                r = await session.get(url, timeout=timeout)
            except RequestsError as exc:
                codes[type(exc).__name__] += 1
                return
            lat.append(time.perf_counter() - t0)
            codes[r.status_code] += 1
            versions[str(r.http_version)] += 1
            total += len(r.content)

    cpu0, t0 = cpu_seconds(), time.perf_counter()
    async with AsyncSession(max_clients=inflight) as session:
        await asyncio.gather(*(one(session, u) for u in urls))
    wall, cpu = time.perf_counter() - t0, cpu_seconds() - cpu0
    return _summary("curl_cffi-async", urls, wall, cpu, total, codes, versions, lat)


def _summary(
    name: str,
    urls: list[str],
    wall: float,
    cpu: float,
    total: int,
    codes: collections.Counter[Any],
    versions: collections.Counter[str],
    lat: list[float],
) -> dict[str, Any]:
    n = len(urls)
    return {
        "client": name,
        "requests": n,
        "wall_s": round(wall, 3),
        "req_per_s": round(n / wall, 1),
        "mb_per_s": round(total / wall / 1e6, 2),
        "cpu_s": round(cpu, 3),
        "cpu_ms_per_req": round(cpu / n * 1e3, 3),
        "cpu_cores_used": round(cpu / wall, 2),
        "codes": {str(k): v for k, v in codes.items()},
        "http_versions": dict(versions),
        "latency_s": summarize_latencies(lat),
        "loadavg": machine_snapshot()["loadavg"],
    }


def main() -> None:
    """Run each client on the same URL list and write a JSON report."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--quadkeys", type=Path, required=True)
    ap.add_argument("--count", type=int, default=5000)
    ap.add_argument("--offset", type=int, default=10_000)
    ap.add_argument("--inflight", type=int, default=64)
    ap.add_argument("--host", default="plan_ecn", choices=list(BING_HOSTS))
    ap.add_argument("--generation", type=int, default=15311)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument(
        "--trust-env-count",
        type=int,
        default=1000,
        help="requests for the trust_env=True comparison (0 to skip)",
    )
    ap.add_argument("--rounds", type=int, default=2, help="repeat the whole plan (round 1 warms)")
    ap.add_argument("--out", type=Path, default=SCRATCH / "py_clients.json")
    args = ap.parse_args()

    keys = load_quadkeys(args.quadkeys)[args.offset : args.offset + args.count]
    template = BING_HOSTS[args.host].replace("g=15312", f"g={args.generation}")
    urls = [bing_url(template, q, i) for i, q in enumerate(keys)]
    results: list[dict[str, Any]] = []
    print("before:", machine_snapshot()["loadavg"])
    plan = [
        (
            "httpx h2",
            lambda: run_httpx(
                urls, args.inflight, http2=True, trust_env=False, timeout=args.timeout
            ),
        ),
        ("curl_cffi", lambda: run_curl_cffi(urls, args.inflight, timeout=args.timeout)),
        (
            "httpx h1",
            lambda: run_httpx(
                urls, args.inflight, http2=False, trust_env=False, timeout=args.timeout
            ),
        ),
    ]
    if args.trust_env_count:
        sub = urls[: args.trust_env_count]
        plan += [
            (
                "httpx h2 trust_env=True",
                lambda: run_httpx(
                    sub, args.inflight, http2=True, trust_env=True, timeout=args.timeout
                ),
            ),
            (
                "httpx h2 trust_env=False",
                lambda: run_httpx(
                    sub, args.inflight, http2=True, trust_env=False, timeout=args.timeout
                ),
            ),
        ]
    for rnd in range(1, args.rounds + 1):
        print(f"--- round {rnd}")
        for label, coro in plan:
            res = asyncio.run(coro())
            res["round"] = rnd
            results.append(res)
            _print(label, res)
    dump_json(
        args.out,
        {
            "machine": machine_snapshot(),
            "inflight": args.inflight,
            "template": template,
            "results": results,
        },
    )
    print("written", args.out)


def _print(label: str, res: dict[str, Any]) -> None:
    lat = res["latency_s"]
    print(
        f"{label:26s} {res['requests']:5d} req {res['wall_s']:7.2f} s "
        f"{res['req_per_s']:7.1f} req/s {res['mb_per_s']:5.2f} MB/s "
        f"cpu {res['cpu_ms_per_req']:.3f} ms/req ({res['cpu_cores_used']} cores) "
        f"codes {res['codes']} http {res['http_versions']} "
        f"p50 {lat.get('p50', 0):.3f} p99 {lat.get('p99', 0):.3f}"
    )


if __name__ == "__main__":
    main()
