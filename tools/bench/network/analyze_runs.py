"""Post-processing of ``curl_sustained`` raw samples: throughput over time and stragglers.

Usage::

    uv run python tools/bench/network/analyze_runs.py SCRATCH/curl_run64.tsv [more.tsv...]
"""

from __future__ import annotations

import argparse
import collections
import csv
from pathlib import Path
from typing import Any

from common import summarize_latencies


def load_tsv(path: Path) -> list[dict[str, Any]]:
    """Read the raw per-transfer table written by curl_sustained.py."""
    with path.open() as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    for r in rows:
        r["code"] = int(r["code"])
        r["bytes"] = int(r["bytes"])
        r["time_total"] = float(r["time_total"])
        r["time_ttfb"] = float(r["time_ttfb"])
        r["t_done"] = float(r["t_done"])
    return rows


def buckets(rows: list[dict[str, Any]], width: float = 5.0) -> list[dict[str, float]]:
    """Requests per second and MB/s per time bucket of ``width`` seconds."""
    acc: dict[int, list[int]] = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        b = int(r["t_done"] // width)
        acc[b][0] += 1
        acc[b][1] += r["bytes"]
    out = []
    for b in sorted(acc):
        n, size = acc[b]
        out.append(
            {
                "t": b * width,
                "req_per_s": round(n / width, 1),
                "mb_per_s": round(size / width / 1e6, 2),
            }
        )
    return out


def report(path: Path) -> None:
    """Print a compact report for one run."""
    rows = load_tsv(path)
    ok = [r for r in rows if r["code"] == 200]
    lat = summarize_latencies([r["time_total"] for r in ok])
    ttfb = summarize_latencies([r["time_ttfb"] for r in ok])
    med = lat.get("p50", 0)
    slow = sorted(ok, key=lambda r: -r["time_total"])[:8]
    sizes = [r["bytes"] for r in ok if r["tile_info"] != "no-tile"]
    print(f"== {path.name}: {len(rows)} transfers, {len(ok)} ok")
    print("   time_total:", {k: round(v, 3) for k, v in lat.items()})
    print("   ttfb      :", {k: round(v, 3) for k, v in ttfb.items()})
    print(
        f"   photo bytes: mean {sum(sizes) / len(sizes):.0f}, "
        f"{dict((k, round(v)) for k, v in summarize_latencies([float(s) for s in sizes]).items())}"
    )
    print(
        f"   > 10 x median ({10 * med:.2f} s): {sum(1 for r in ok if r['time_total'] > 10 * med)}"
    )
    print(
        f"   > 1 s: {sum(1 for r in ok if r['time_total'] > 1.0)}, "
        f"> 0.5 s: {sum(1 for r in ok if r['time_total'] > 0.5)}"
    )
    print(
        "   slowest:", [(round(r["time_total"], 2), r["bytes"], r["url"][-40:]) for r in slow[:5]]
    )
    print("   per 5 s :", " ".join(f"{b['req_per_s']:.0f}" for b in buckets(rows)))
    print("   MB/s 5 s:", " ".join(f"{b['mb_per_s']:.1f}" for b in buckets(rows)))


def main() -> None:
    """Report every TSV given on the command line."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tsv", nargs="+", type=Path)
    for p in ap.parse_args().tsv:
        report(p)


if __name__ == "__main__":
    main()
