# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Which of AutoOrtho's regions has the mesh of each one-degree square of the planet.

Live says "no mesh here: install North America" when a flight starts where no region installed has
a mesh (``orthostudio.live.aoregions.region_of``). This tool writes the map it reads,
``src/orthostudio/live/aoregions_map.json``, from the central directory of every mesh zip of the
newest release of each region on GitHub (``kubilus1/autoortho-scenery``): 154 zips, 604 MB of
central directories in September 2026, read over HTTP ranges, the zips themselves never downloaded.
A square in two regions is the first region's, in :data:`orthostudio.live.aoregions.REGION_IDS`.

Format ``osxp-live-regions-1``::

    {"format": "osxp-live-regions-1", "source": ..., "made": "2026-09-27",
     "releases": {"eur": "0.0.51", ...}, "regions": ["eur", "na", ...],
     "squares": {"eur": 1981, ...}, "map": "<base64 of zlib of 64,800 bytes>"}

Byte ``(lat + 90) * 360 + (lon + 180)`` of the map, for the square whose south-west corner is
(lat, lon), is 0 where no region has a mesh, else 1 + the region's index in ``regions``.

Usage (from the repository root)::

    uv run python tools/live/aoregions_map.py
"""

from __future__ import annotations

import base64
import json
import re
import struct
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from orthostudio.live import aoregions  # noqa: E402

OUT = ROOT / "src" / "orthostudio" / "live" / "aoregions_map.json"
DSF = re.compile(r"Earth nav data/[+-]\d\d[+-]\d\d\d/([+-]\d\d)([+-]\d\d\d)\.dsf$")
TAIL = 65_557  # the end record and its longest comment


def squares_of(client: httpx.Client, asset: aoregions.Asset) -> set[tuple[int, int]]:
    """The squares whose DSF a mesh zip holds, from its central directory alone."""
    start = max(0, asset.size - TAIL)
    tail = client.get(asset.url, headers={"Range": f"bytes={start}-{asset.size - 1}"}).content
    at = tail.rfind(b"PK\x05\x06")
    if at < 0:
        raise SystemExit(f"{asset.name}: no end record")
    cd_size, cd_offset = struct.unpack("<II", tail[at + 12 : at + 20])
    cd = client.get(
        asset.url, headers={"Range": f"bytes={cd_offset}-{cd_offset + cd_size - 1}"}
    ).content
    found, pos = set(), 0
    while pos + 46 <= len(cd) and cd[pos : pos + 4] == b"PK\x01\x02":
        name_len, extra_len, comment_len = struct.unpack("<HHH", cd[pos + 28 : pos + 34])
        name = cd[pos + 46 : pos + 46 + name_len].decode("utf-8", "replace")
        m = DSF.search(name)
        if m:
            found.add((int(m[1]), int(m[2])))
        pos += 46 + name_len + extra_len + comment_len
    return found


def main() -> None:
    t0 = time.monotonic()
    regions = aoregions.catalog(ROOT / "build" / "aoregions", refresh=True)
    order = [r for r in aoregions.REGION_IDS if r in regions]
    cells = bytearray(180 * 360)
    counts: dict[str, int] = {}
    with httpx.Client(follow_redirects=True, timeout=120) as client, ThreadPoolExecutor(8) as pool:
        for index, region_id in enumerate(order, start=1):
            squares: set[tuple[int, int]] = set()
            for got in pool.map(lambda a: squares_of(client, a), regions[region_id].mesh):
                squares |= got
            counts[region_id] = len(squares)
            for lat, lon in squares:
                cell = (lat + 90) * 360 + (lon + 180)
                if not cells[cell]:  # a square in two regions is the first one's
                    cells[cell] = index
            print(f"{region_id}: {len(squares)} squares", flush=True)
    doc = {
        "format": "osxp-live-regions-1",
        "source": f"https://github.com/{aoregions.REPO}",
        "made": time.strftime("%Y-%m-%d"),
        "releases": {r: regions[r].release for r in order},
        "regions": order,
        "squares": counts,
        "map": base64.b64encode(zlib.compress(bytes(cells), 9)).decode(),
    }
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"{OUT}: {OUT.stat().st_size} bytes, in {time.monotonic() - t0:.0f} s")


if __name__ == "__main__":
    main()
