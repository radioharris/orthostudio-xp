"""Shared helpers for the network benchmarks (statistics, placeholder detection, machine load)."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import platform
import statistics
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

SCRATCH = Path(
    os.environ.get(
        "OSXP_BENCH_SCRATCH",
        str(Path(__file__).resolve().parents[3] / ".orthostudio" / "bench" / "network"),
    )
)

# Bing "no imagery" placeholder: a 1 033 byte PNG (white tile with a small camera), served with
# HTTP 200 and the header ``X-VE-Tile-Info: no-tile``. Ortho4XP recognises it by the
# Content-Length only (O4_Imagery_Utils.py:1019-1030).
BING_PLACEHOLDER_BYTES = 1033
BING_PLACEHOLDER_HEADER = "x-ve-tile-info"
BING_PLACEHOLDER_VALUE = "no-tile"

BING_HOSTS: dict[str, str] = {
    # what Ortho4XP ships in Providers/Global/BI.lay (clear HTTP/1.1)
    "ortho4xp_r": "http://r{n}.ortho.tiles.virtualearth.net/tiles/a{q}.jpeg?g=136",
    # what AutoOrtho uses (single host, HTTPS)
    "autoortho_ssl": "https://t.ssl.ak.tiles.virtualearth.net/tiles/a{q}.jpeg?g=15312",
    # what the osxp plan proposes (4 hosts, HTTPS, HTTP/2)
    "plan_ecn": "https://ecn.t{n}.tiles.virtualearth.net/tiles/a{q}.jpeg?g=15312",
}


def bing_url(template: str, quadkey: str, index: int = 0) -> str:
    """Fill a Bing URL template; ``{n}`` rotates over the 4 shards (0-3)."""
    return template.format(n=index % 4, q=quadkey)


def is_bing_placeholder(status: int, size: int, headers: Mapping[str, str]) -> bool:
    """True when a response is Bing's "no imagery here" tile rather than a photo.

    The header is authoritative; the byte count is kept as a second signal because some proxies
    strip custom headers.
    """
    if status != 200:
        return False
    lowered = {k.lower(): v for k, v in headers.items()}
    if lowered.get(BING_PLACEHOLDER_HEADER, "").strip().lower() == BING_PLACEHOLDER_VALUE:
        return True
    return size == BING_PLACEHOLDER_BYTES and "png" in lowered.get("content-type", "")


def percentiles(
    values: Iterable[float], points: Iterable[float] = (50, 90, 99)
) -> dict[str, float]:
    """Nearest-rank percentiles of a sample, keyed ``p50``, ``p90``... Empty sample -> empty."""
    data = sorted(values)
    if not data:
        return {}
    out: dict[str, float] = {}
    for p in points:
        rank = max(0, min(len(data) - 1, math.ceil(p / 100 * len(data)) - 1))
        out[f"p{p:g}"] = data[rank]
    return out


def summarize_latencies(values: list[float]) -> dict[str, float]:
    """min / mean / percentiles / max of a latency sample in seconds."""
    if not values:
        return {}
    out = {"n": float(len(values)), "min": min(values), "mean": statistics.fmean(values)}
    out.update(percentiles(values, (50, 90, 99, 99.9)))
    out["max"] = max(values)
    return out


def machine_snapshot() -> dict[str, Any]:
    """Load averages, CPU count and timestamp, to be stored with every measurement."""
    load1, load5, load15 = os.getloadavg()
    return {
        "timestamp": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "host": platform.node(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "loadavg": [round(load1, 2), round(load5, 2), round(load15, 2)],
    }


def dump_json(path: Path, payload: Any) -> None:
    """Write pretty JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, default=str) + "\n")


def load_quadkeys(path: Path) -> list[str]:
    """Read one quadkey per line."""
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def evenly_spaced(items: list[str], count: int) -> list[str]:
    """``count`` items spread evenly over the list (deterministic sample)."""
    if count >= len(items):
        return list(items)
    step = len(items) / count
    return [items[int(i * step)] for i in range(count)]
