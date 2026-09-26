# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Step 4: health of the public Overpass mirrors with the four Ortho4XP queries for a cell.

The four layers Ortho4XP fetches for a tile (O4_Vector_Map.py: airports, big_roads, coastline,
water) are rewritten as single ``[out:json]`` queries with ``(._;>>;); out body qt;`` and sent
once each to every mirror, sequentially per mirror (mirrors run in parallel). Records wall time,
bytes, element count, the ``remark`` field (Overpass reports timeouts and memory limits inside
a 200 response) and the data timestamp of each mirror.

Usage::

    uv run python tools/bench/network/overpass_health.py --lat 43 --lon 5 --timeout 120
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
import orjson
from common import SCRATCH, dump_json, machine_snapshot

MIRRORS: dict[str, str] = {
    "overpass-api.de": "https://overpass-api.de/api/interpreter",
    "lz4.overpass-api.de": "https://lz4.overpass-api.de/api/interpreter",
    "z.overpass-api.de": "https://z.overpass-api.de/api/interpreter",
    "overpass.kumi.systems": "https://overpass.kumi.systems/api/interpreter",
    "maps.mail.ru": "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "overpass.openstreetmap.fr": "https://overpass.openstreetmap.fr/api/interpreter",
    "overpass.private.coffee": "https://overpass.private.coffee/api/interpreter",
    "overpass.osm.jp": "https://overpass.osm.jp/api/interpreter",
}

# Selectors exactly as in Ortho4XP O4_Vector_Map.py (road_level 1: no small_roads layer).
LAYERS: dict[str, list[str]] = {
    "airports": ['node["aeroway"]', 'way["aeroway"]', 'rel["aeroway"]'],
    "big_roads": [
        'way["highway"="motorway"]',
        'way["highway"="trunk"]',
        'way["highway"="primary"]',
        'way["highway"="secondary"]',
        'way["railway"="rail"]',
        'way["railway"="narrow_gauge"]',
    ],
    "coastline": ['way["natural"="coastline"]'],
    "water": [
        'rel["natural"="water"]',
        'rel["waterway"="riverbank"]',
        'way["natural"="water"]',
        'way["waterway"="riverbank"]',
        'way["waterway"="dock"]',
    ],
}


def build_query(selectors: list[str], lat: int, lon: int, timeout: int) -> str:
    """Overpass QL for one layer of the 1 degree cell, JSON output, children recursed."""
    bbox = f"({lat},{lon},{lat + 1},{lon + 1})"
    union = "".join(f"{sel}{bbox};" for sel in selectors)
    return f"[out:json][timeout:{timeout}];({union});(._;>>;);out body qt;"


def ortho4xp_query(selectors: list[str], lat: int, lon: int) -> str:
    """The equivalent request Ortho4XP issues (O4_OSM_Utils.get_overpass_data), for the record."""
    bbox = f"({lat}, {lon}, {lat + 1}, {lon + 1})"
    return "(" + "".join(f"{sel}{bbox};" for sel in selectors) + ");(._;>>;);out meta;"


async def one_query(client: httpx.AsyncClient, url: str, layer: str, query: str) -> dict[str, Any]:
    """POST one query, return timing and a light parse of the answer."""
    t0 = time.perf_counter()
    rec: dict[str, Any] = {"layer": layer}
    try:
        r = await client.post(url, data={"data": query})
    except httpx.HTTPError as exc:
        rec.update(
            error=f"{type(exc).__name__}: {exc}", elapsed_s=round(time.perf_counter() - t0, 2)
        )
        return rec
    body = r.content
    rec.update(
        status=r.status_code,
        elapsed_s=round(time.perf_counter() - t0, 2),
        bytes=len(body),
        content_encoding=r.headers.get("content-encoding"),
        http_version=r.http_version,
    )
    if r.status_code == 200:
        try:
            doc = orjson.loads(body)
        except orjson.JSONDecodeError as exc:
            rec["error"] = f"bad json: {exc}"
            return rec
        rec["elements"] = len(doc.get("elements", []))
        rec["osm3s"] = doc.get("osm3s", {})
        if "remark" in doc:
            rec["remark"] = doc["remark"]
    else:
        rec["body_head"] = body[:300].decode("utf-8", "replace")
    return rec


async def probe_mirror(name: str, url: str, lat: int, lon: int, timeout: float) -> dict[str, Any]:
    """Status endpoint, then the four layer queries one after the other."""
    out: dict[str, Any] = {"mirror": name, "url": url, "queries": []}
    async with httpx.AsyncClient(
        http2=True,
        trust_env=False,
        timeout=httpx.Timeout(timeout, connect=15.0),
        headers={
            "User-Agent": "osxp-bench/0.0 (network benchmark; contact: github OrthoStudio XP)"
        },
    ) as client:
        t0 = time.perf_counter()
        try:
            st = await client.get(url.rsplit("/", 1)[0] + "/status")
            out["status_endpoint"] = {
                "status": st.status_code,
                "elapsed_s": round(time.perf_counter() - t0, 2),
                "head": st.text[:400],
            }
        except httpx.HTTPError as exc:
            out["status_endpoint"] = {"error": f"{type(exc).__name__}: {exc}"}
        for layer, selectors in LAYERS.items():
            q = build_query(selectors, lat, lon, int(timeout))
            rec = await one_query(client, url, layer, q)
            out["queries"].append(rec)
            print(
                f"  {name:26s} {layer:10s} "
                + (
                    f"{rec['status']} {rec['elapsed_s']:6.1f} s {rec['bytes'] / 1e6:6.2f} MB "
                    f"{rec.get('elements', '-')} elements"
                    + (f" REMARK: {rec['remark'][:80]}" if rec.get("remark") else "")
                    if "status" in rec
                    else f"ERROR {rec['error']}"
                ),
                flush=True,
            )
    return out


async def run_all(lat: int, lon: int, timeout: float, mirrors: dict[str, str]) -> list[dict]:
    """All mirrors in parallel, one query at a time per mirror."""
    return await asyncio.gather(
        *(probe_mirror(n, u, lat, lon, timeout) for n, u in mirrors.items())
    )


def main() -> None:
    """Probe every mirror and write the report."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lat", type=int, default=43)
    ap.add_argument("--lon", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--mirrors", nargs="*", default=list(MIRRORS))
    ap.add_argument("--out", type=Path, default=SCRATCH / "overpass_health.json")
    args = ap.parse_args()
    mirrors = {m: MIRRORS[m] for m in args.mirrors}
    before = machine_snapshot()
    print("queries:")
    for layer, sel in LAYERS.items():
        print(f"  {layer}: {build_query(sel, args.lat, args.lon, int(args.timeout))}")
    t0 = time.perf_counter()
    results = asyncio.run(run_all(args.lat, args.lon, args.timeout, mirrors))
    wall = time.perf_counter() - t0
    dump_json(
        args.out,
        {
            "machine_before": before,
            "machine_after": machine_snapshot(),
            "cell": [args.lat, args.lon],
            "timeout_s": args.timeout,
            "wall_s": round(wall, 1),
            "queries": {
                k: build_query(v, args.lat, args.lon, int(args.timeout)) for k, v in LAYERS.items()
            },
            "ortho4xp_queries": {
                k: ortho4xp_query(v, args.lat, args.lon) for k, v in LAYERS.items()
            },
            "mirrors": results,
        },
    )
    print(f"total wall {wall:.0f} s, written {args.out}")


if __name__ == "__main__":
    main()
