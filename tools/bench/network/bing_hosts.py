# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Step 1: compare the three known Bing tile hosts on the same sample of tiles.

For each host template (Ortho4XP's ``r{0-3}.ortho``, AutoOrtho's ``t.ssl.ak``, the plan's
``ecn.t{0-3}``) fetch the same N quadkeys with httpx (HTTP/2 negotiated when offered) and record
status, bytes, protocol, ``X-VE-Tile-Info`` and a hash of the body so that hosts can be compared
byte for byte.

Usage::

    uv run python tools/bench/network/bing_hosts.py --quadkeys FILE --count 300 --inflight 32
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import statistics
import time
from pathlib import Path
from typing import Any

import blake3
import httpx
from common import (
    BING_HOSTS,
    SCRATCH,
    bing_url,
    dump_json,
    evenly_spaced,
    is_bing_placeholder,
    load_quadkeys,
    machine_snapshot,
    summarize_latencies,
)

KEPT_HEADERS = (
    "content-type",
    "content-length",
    "x-ve-tile-info",
    "x-ve-tilemeta-product-ids",
    "x-ve-tilemeta-capturedatemaxyymm",
    "x-cache-remote",
    "server",
    "cache-control",
)


async def fetch_one(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, url: str, quadkey: str
) -> dict[str, Any]:
    """GET one tile and return a flat record."""
    async with sem:
        t0 = time.perf_counter()
        try:
            r = await client.get(url)
        except httpx.HTTPError as exc:
            return {
                "quadkey": quadkey,
                "url": url,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed": time.perf_counter() - t0,
            }
        body = r.content
        rec: dict[str, Any] = {
            "quadkey": quadkey,
            "url": url,
            "status": r.status_code,
            "http_version": r.http_version,
            "bytes": len(body),
            "elapsed": time.perf_counter() - t0,
            "blake3": blake3.blake3(body).hexdigest(),
            "placeholder": is_bing_placeholder(r.status_code, len(body), r.headers),
            "headers": {k: r.headers.get(k) for k in KEPT_HEADERS if k in r.headers},
        }
        return rec


async def probe_host(
    name: str, template: str, quadkeys: list[str], inflight: int, timeout: float
) -> dict[str, Any]:
    """Fetch all quadkeys on one host template and summarise."""
    sem = asyncio.Semaphore(inflight)
    limits = httpx.Limits(max_connections=inflight, max_keepalive_connections=inflight)
    t0 = time.perf_counter()
    async with httpx.AsyncClient(
        http2=True, trust_env=False, timeout=timeout, limits=limits
    ) as client:
        tasks = [
            fetch_one(client, sem, bing_url(template, q, i), q) for i, q in enumerate(quadkeys)
        ]
        records = await asyncio.gather(*tasks)
    wall = time.perf_counter() - t0
    ok = [r for r in records if r.get("status") == 200]
    statuses = collections.Counter(r.get("status", "error") for r in records)
    versions = collections.Counter(r.get("http_version", "-") for r in records)
    tile_info = collections.Counter(r.get("headers", {}).get("x-ve-tile-info", "-") for r in ok)
    sizes = [r["bytes"] for r in ok if not r["placeholder"]]
    summary = {
        "host": name,
        "template": template,
        "requests": len(records),
        "wall_s": round(wall, 3),
        "req_per_s": round(len(records) / wall, 1),
        "statuses": dict(statuses),
        "http_versions": dict(versions),
        "errors": [r["error"] for r in records if "error" in r][:10],
        "placeholders": sum(1 for r in ok if r["placeholder"]),
        "x_ve_tile_info": dict(tile_info),
        "bytes_total": sum(r["bytes"] for r in ok),
        "photo_bytes": {
            "n": len(sizes),
            "min": min(sizes, default=0),
            "median": statistics.median(sizes) if sizes else 0,
            "mean": round(statistics.fmean(sizes), 1) if sizes else 0,
            "max": max(sizes, default=0),
        },
        "edge_cache_hits": sum(1 for r in ok if "x-cache-remote" not in r["headers"]),
        "latency_s": summarize_latencies([r["elapsed"] for r in records if "elapsed" in r]),
        "product_ids": dict(
            collections.Counter(
                r["headers"].get("x-ve-tilemeta-product-ids", "-") for r in ok
            ).most_common(8)
        ),
    }
    return {"summary": summary, "records": records}


def cross_host_agreement(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """How many quadkeys yielded byte-identical bodies on every host."""
    by_host = {
        name: {r["quadkey"]: r.get("blake3") for r in res["records"] if r.get("status") == 200}
        for name, res in results.items()
    }
    names = list(by_host)
    if len(names) < 2:
        return {}
    common = set.intersection(*(set(v) for v in by_host.values()))
    identical = sum(1 for q in common if len({by_host[n][q] for n in names}) == 1)
    return {"common_quadkeys": len(common), "byte_identical": identical}


def main() -> None:
    """Run the probe on all hosts and write JSON + a printed summary."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--quadkeys", type=Path, required=True)
    ap.add_argument("--count", type=int, default=300)
    ap.add_argument("--inflight", type=int, default=32)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--hosts", nargs="*", default=list(BING_HOSTS))
    ap.add_argument("--out", type=Path, default=SCRATCH / "bing_hosts.json")
    args = ap.parse_args()

    sample = evenly_spaced(load_quadkeys(args.quadkeys), args.count)
    before = machine_snapshot()
    results: dict[str, dict[str, Any]] = {}
    for name in args.hosts:
        template = BING_HOSTS[name]
        print(f"--- {name}: {template}")
        results[name] = asyncio.run(probe_host(name, template, sample, args.inflight, args.timeout))
        s = results[name]["summary"]
        lat = s["latency_s"]
        print(
            f"    {s['requests']} req in {s['wall_s']} s ({s['req_per_s']} req/s), "
            f"statuses {s['statuses']}, http {s['http_versions']}, "
            f"placeholders {s['placeholders']}, tile-info {s['x_ve_tile_info']}, "
            f"photo bytes median {s['photo_bytes']['median']} mean {s['photo_bytes']['mean']}, "
            f"edge hits {s['edge_cache_hits']}, "
            f"latency p50 {lat.get('p50', 0):.3f} p90 {lat.get('p90', 0):.3f} "
            f"p99 {lat.get('p99', 0):.3f} max {lat.get('max', 0):.3f}"
        )
        if s["errors"]:
            print("    errors:", s["errors"][:3])
    agreement = cross_host_agreement(results)
    print("cross-host byte agreement:", agreement)
    dump_json(
        args.out,
        {
            "machine_before": before,
            "machine_after": machine_snapshot(),
            "sample_size": len(sample),
            "inflight": args.inflight,
            "agreement": agreement,
            "hosts": {n: r["summary"] for n, r in results.items()},
            "records": {n: r["records"] for n, r in results.items()},
        },
    )
    print("written", args.out)


if __name__ == "__main__":
    main()
