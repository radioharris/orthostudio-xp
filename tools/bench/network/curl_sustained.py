# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Step 2: sustained download benchmark with ``curl --parallel`` against one Bing host template.

curl is used as the reference client (libcurl, HTTP/2 multiplexing in C) so that the numbers
describe the CDN and the line, not a Python client. Bodies go to /dev/null; one ``--write-out``
line per transfer is streamed to this script, which aborts the run when the provider pushes back
(429/403 above a threshold, or a burst of consecutive failures) and then summarises.

Usage::

    uv run python tools/bench/network/curl_sustained.py --quadkeys FILE --count 50000 \\
        --inflight 64 --generation 15311 --label run64

The ``--generation`` value only changes the ``g=`` query parameter; the CDN keys its cache on
the full URL, so a fresh value gives an edge-cold run and a repeated one an edge-warm run.
"""

from __future__ import annotations

import argparse
import collections
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common import (
    BING_HOSTS,
    BING_PLACEHOLDER_BYTES,
    SCRATCH,
    bing_url,
    dump_json,
    load_quadkeys,
    machine_snapshot,
    summarize_latencies,
)

# One tab-separated line per transfer. %header{} and %{exitcode} need curl >= 7.84.
WRITE_OUT = "\t".join(
    [
        "%{http_code}",
        "%{size_download}",
        "%{time_total}",
        "%{time_starttransfer}",
        "%{http_version}",
        "%{num_connects}",
        "%{exitcode}",
        "%{remote_ip}",
        "%header{x-ve-tile-info}",
        "%header{x-cache-remote}",
        "%{url}",
    ]
)
FIELDS = (
    "code",
    "bytes",
    "time_total",
    "time_ttfb",
    "http_version",
    "num_connects",
    "exitcode",
    "remote_ip",
    "tile_info",
    "x_cache",
    "url",
)


@dataclass
class Sample:
    """One completed transfer as reported by curl."""

    code: int
    bytes: int
    time_total: float
    time_ttfb: float
    http_version: str
    num_connects: int
    exitcode: int
    remote_ip: str
    tile_info: str
    x_cache: str
    url: str
    t_done: float

    @property
    def ok(self) -> bool:
        """A 200 with a non-empty body and no curl error."""
        return self.code == 200 and self.exitcode == 0 and self.bytes > 0

    @property
    def placeholder(self) -> bool:
        """Bing 'no-tile' PNG."""
        return self.ok and (
            self.tile_info.lower() == "no-tile" or self.bytes == BING_PLACEHOLDER_BYTES
        )


def parse_line(line: str, t_done: float) -> Sample | None:
    """Parse one ``--write-out`` line; None when the line is not ours."""
    parts = line.rstrip("\n").split("\t")
    if len(parts) != len(FIELDS):
        return None
    try:
        return Sample(
            code=int(parts[0] or 0),
            bytes=int(float(parts[1] or 0)),
            time_total=float(parts[2] or 0),
            time_ttfb=float(parts[3] or 0),
            http_version=parts[4],
            num_connects=int(parts[5] or 0),
            exitcode=int(parts[6] or 0),
            remote_ip=parts[7],
            tile_info=parts[8],
            x_cache=parts[9],
            url=parts[10],
            t_done=t_done,
        )
    except ValueError:
        return None


@dataclass
class Guard:
    """Abort policy: provider push-back or failure bursts stop the run immediately."""

    min_samples: int = 300
    max_pushback_ratio: float = 0.01
    max_consecutive_failures: int = 25
    consecutive_failures: int = 0
    pushback: int = 0
    total: int = 0
    reason: str | None = None

    def observe(self, s: Sample) -> None:
        """Update counters; set ``reason`` when the run must stop."""
        self.total += 1
        if s.code in (429, 403):
            self.pushback += 1
        if s.ok:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            self.reason = f"{self.consecutive_failures} consecutive failures"
        elif self.total >= self.min_samples:
            ratio = self.pushback / self.total
            if ratio > self.max_pushback_ratio:
                self.reason = f"429/403 ratio {ratio:.2%} > 1 %"


@dataclass
class RunStats:
    """Everything collected during one run."""

    samples: list[Sample] = field(default_factory=list)
    t_start: float = 0.0
    t_end: float = 0.0
    aborted: str | None = None


def write_curl_config(urls: list[str], path: Path) -> None:
    """curl ``--config`` file: one url/output pair per transfer."""
    with path.open("w") as f:
        for u in urls:
            f.write(f'url = "{u}"\noutput = "/dev/null"\n')


def curl_command(config: Path, inflight: int, max_time: float, http2: bool) -> list[str]:
    """Build the curl command line."""
    cmd = [
        shutil.which("curl") or "curl",
        "--silent",
        "--show-error",
        "--parallel",
        "--parallel-max",
        str(inflight),
        "--max-time",
        str(max_time),
        "--connect-timeout",
        "10",
        "--retry",
        "0",
        "--write-out",
        WRITE_OUT + "\n",
        "--config",
        str(config),
    ]
    if http2:
        cmd.insert(1, "--http2")
    return cmd


def run_curl(cmd: list[str], guard: Guard, expected: int, report_every: float = 10.0) -> RunStats:
    """Run curl, stream its write-out lines, enforce the guard, report progress."""
    stats = RunStats()
    stats.t_start = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    lock = threading.Lock()
    stderr_lines: list[str] = []

    def drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line.rstrip())

    threading.Thread(target=drain_stderr, daemon=True).start()
    last_report = stats.t_start
    last_n = 0
    last_bytes = 0
    for line in proc.stdout:
        now = time.perf_counter()
        s = parse_line(line, now - stats.t_start)
        if s is None:
            continue
        with lock:
            stats.samples.append(s)
            guard.observe(s)
        if guard.reason and stats.aborted is None:
            stats.aborted = guard.reason
            print(f"!!! ABORT: {guard.reason} after {guard.total} transfers", flush=True)
            proc.kill()
            break
        if now - last_report >= report_every:
            n = len(stats.samples)
            total_bytes = sum(x.bytes for x in stats.samples)
            dt = now - last_report
            codes = collections.Counter(x.code for x in stats.samples[last_n:])
            print(
                f"  t={now - stats.t_start:6.1f}s done={n}/{expected} "
                f"rate={(n - last_n) / dt:6.1f} req/s "
                f"{(total_bytes - last_bytes) / dt / 1e6:5.2f} MB/s codes={dict(codes)}",
                flush=True,
            )
            last_report, last_n, last_bytes = now, n, total_bytes
    proc.wait()
    stats.t_end = time.perf_counter()
    if stderr_lines:
        errs = collections.Counter(stderr_lines)
        print(f"  curl stderr ({len(stderr_lines)} lines): {errs.most_common(5)}")
    return stats


def summarize(stats: RunStats, inflight: int, template: str, generation: int) -> dict[str, Any]:
    """Aggregate a run into the numbers reported in docs/benchmarks/network.md."""
    ss = stats.samples
    wall = stats.t_end - stats.t_start
    ok = [s for s in ss if s.ok]
    photos = [s for s in ok if not s.placeholder]
    total_bytes = sum(s.bytes for s in ss)
    codes = collections.Counter(s.code for s in ss)
    exitcodes = collections.Counter(s.exitcode for s in ss if s.exitcode)
    lat = [s.time_total for s in ok]
    ttfb = [s.time_ttfb for s in ok]
    # stragglers: transfers slower than 10 x the median
    med = summarize_latencies(lat).get("p50", 0.0)
    stragglers = sorted((s.time_total for s in ok if med and s.time_total > 10 * med), reverse=True)
    return {
        "template": template,
        "generation": generation,
        "inflight": inflight,
        "requests": len(ss),
        "wall_s": round(wall, 2),
        "req_per_s": round(len(ss) / wall, 1) if wall else 0,
        "mb_per_s": round(total_bytes / wall / 1e6, 2) if wall else 0,
        "bytes_total": total_bytes,
        "ok": len(ok),
        "placeholders": len(ok) - len(photos),
        "photo_bytes_mean": round(sum(s.bytes for s in photos) / len(photos), 1) if photos else 0,
        "codes": {str(k): v for k, v in sorted(codes.items())},
        "curl_exitcodes": {str(k): v for k, v in sorted(exitcodes.items())},
        "http_versions": dict(collections.Counter(s.http_version for s in ss)),
        "connections_opened": sum(s.num_connects for s in ss),
        "distinct_edges": len({s.remote_ip for s in ss if s.remote_ip}),
        "edge_cache_hits": sum(1 for s in ok if not s.x_cache),
        "latency_s": summarize_latencies(lat),
        "ttfb_s": summarize_latencies(ttfb),
        "stragglers_over_10x_median": len(stragglers),
        "worst_10_s": [round(x, 2) for x in stragglers[:10]],
        "aborted": stats.aborted,
    }


def main() -> None:
    """Run one concurrency level and write the summary + raw samples."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--quadkeys", type=Path, required=True)
    ap.add_argument("--count", type=int, default=50_000)
    ap.add_argument("--offset", type=int, default=0, help="start index in the quadkey list")
    ap.add_argument("--inflight", type=int, default=64)
    ap.add_argument("--host", default="plan_ecn", choices=list(BING_HOSTS))
    ap.add_argument("--generation", type=int, default=15312, help="value of the g= parameter")
    ap.add_argument("--max-time", type=float, default=60.0, help="per transfer, seconds")
    ap.add_argument("--no-http2", action="store_true")
    ap.add_argument("--label", default=None)
    ap.add_argument("--outdir", type=Path, default=SCRATCH)
    args = ap.parse_args()

    keys = load_quadkeys(args.quadkeys)
    keys = keys[args.offset : args.offset + args.count]
    template = (
        BING_HOSTS[args.host]
        .replace("g=136", f"g={args.generation}")
        .replace("g=15312", f"g={args.generation}")
    )
    urls = [bing_url(template, q, i) for i, q in enumerate(keys)]
    label = args.label or f"{args.host}_c{args.inflight}_g{args.generation}"
    config = args.outdir / f"curl_{label}.cfg"
    write_curl_config(urls, config)
    cmd = curl_command(config, args.inflight, args.max_time, not args.no_http2)
    before = machine_snapshot()
    print(f"=== {label}: {len(urls)} urls, {args.inflight} in flight, load {before['loadavg']}")
    print("   ", " ".join(cmd[:-2]), "--config", config.name)
    guard = Guard()
    stats = run_curl(cmd, guard, len(urls))
    summary = summarize(stats, args.inflight, template, args.generation)
    summary["machine_before"] = before
    summary["machine_after"] = machine_snapshot()
    summary["command"] = " ".join(cmd)
    dump_json(args.outdir / f"curl_{label}.json", summary)
    raw = args.outdir / f"curl_{label}.tsv"
    with raw.open("w") as f:
        f.write("\t".join((*FIELDS, "t_done")) + "\n")
        for s in stats.samples:
            f.write("\t".join(str(getattr(s, name)) for name in (*FIELDS, "t_done")) + "\n")
    lat = summary["latency_s"]
    print(
        f"=== {label}: {summary['requests']} req in {summary['wall_s']} s = "
        f"{summary['req_per_s']} req/s, {summary['mb_per_s']} MB/s, codes {summary['codes']}, "
        f"curl exit {summary['curl_exitcodes']}, placeholders {summary['placeholders']}, "
        f"conns {summary['connections_opened']}, edges {summary['distinct_edges']}, "
        f"edge hits {summary['edge_cache_hits']}, "
        f"p50 {lat.get('p50', 0):.3f} p90 {lat.get('p90', 0):.3f} p99 {lat.get('p99', 0):.3f} "
        f"max {lat.get('max', 0):.2f}, stragglers {summary['stragglers_over_10x_median']}, "
        f"aborted={summary['aborted']}"
    )
    if summary["aborted"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
