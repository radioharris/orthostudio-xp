# Network benchmarks (P0, "unknown number 1" of the plan)

One-off scripts measuring the imagery CDN (Bing) and the Overpass mirrors from the developer's
machine. Results and the commands that produced them are copied into
`docs/benchmarks/network.md`; the requirements derived from them live in
`docs/specs/net-download.md`. All scripts are stand-alone (run from this directory's path, they
import `common.py` next to them), write their JSON/TSV into the scratch directory named by
`OSXP_BENCH_SCRATCH` (default: the session scratchpad) and never touch the repository.

| Script | Step | What it measures |
|---|---|---|
| `quadkeys.py` | 0 | web-mercator tile math, quadkeys of a 1 degree cell (texture order, optional texture alignment) |
| `bing_hosts.py` | 1 | the three known Bing host templates on the same N tiles: status, bytes, protocol, `X-VE-Tile-Info`, byte identity |
| `curl_sustained.py` | 2 | `curl --parallel` at a fixed concurrency on 50 000 tiles: req/s, MB/s, latency percentiles, codes, placeholders, stragglers; aborts on 429/403 > 1 % or failure bursts |
| `analyze_runs.py` | 2 | throughput per 5 s bucket and slowest transfers from the TSV of a run |
| `py_clients.py` | 3 | httpx (h2, h1, trust_env) versus curl_cffi: req/s and CPU ms per request |
| `overpass_health.py` | 4 | eight Overpass mirrors, the four Ortho4XP layer queries of a cell as `[out:json]`: time, size, elements, remarks |

Run everything with `nice -n 10` and note `uptime`, other benchmarks share the machine.
The tests in `tests/test_network_bench.py` cover the pure functions (no network).
