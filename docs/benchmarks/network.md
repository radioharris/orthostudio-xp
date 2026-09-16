# Network benchmark: Bing imagery CDN, Python clients, Overpass mirrors

Date: 2026-09-12, 02:35-03:05 local (00:35-01:05 UTC). Machine: Mac M4 Pro (14 cores, 48 GB), macOS
25.6.0, Python 3.14, `/usr/bin/curl` 8.7.1 (nghttp2 1.68.1), httpx 0.28.1 (h2), curl_cffi
0.16.3. Load average during the runs: 2.6 to 6.4 (other P0 benchmarks were running on the same
machine; the network runs were started with `nice -n 10`). Residential fibre line, ISP unknown; the
highest sustained rate observed was 32 MB/s over 5 s, so the line is at least that.

Summary: Bing (same bytes on all three hosts) took 315 000 requests in 20 minutes (270 000 of them
in six sustained HTTP/2 runs) with zero 429/403; throughput scaled linearly from 401 req/s at 32 in
flight to 1 909 req/s at 192 (1 436 req/s, 21.6 MB/s at 128) with p50 latency 69-95 ms; placeholders
are identified by `X-VE-Tile-Info: no-tile`; httpx-h2 costs 0.79 ms CPU per request, curl_cffi
0.32 ms; of eight Overpass mirrors three are live and fast (`lz4.overpass-api.de`, `maps.mail.ru`,
`overpass.openstreetmap.fr`), the DE cluster is two machines behind three names with a shared quota
of 2, and three mirrors are dead or six weeks stale.

Scripts: `tools/bench/network/` (see its README). Raw outputs (JSON summaries, one TSV line per
transfer for every curl run) are in the session scratch directory, not in the repository.

## 0. The tile set

`quadkeys.py --lat 43 --lon 5 --zl 16`: the 1 degree cell +43+005 covers x 33678..33859
(182 columns) and y 23830..24080 (251 rows) at ZL16, 45 682 tiles. Widened to whole 16 x 16
textures as Ortho4XP does (`--texture-aligned`): x 33664..33871, y 23824..24095,
13 x 17 = 221 textures, **56 576 tiles**. The list is in texture order (256 tiles of one texture,
then the next), which is the access pattern of a build. All Bing runs below use the first
50 000 of that list, except the 32 in-flight run (first 20 000).

## 1. The three Bing hosts (300 tiles each, httpx, 32 in flight)

Command: `bing_hosts.py --quadkeys aligned.txt --count 300 --inflight 32`
(300 tiles spread evenly over the 56 576, the same 300 for every host).

| Host template | Protocol | Status | req/s | p50 / p90 / p99 latency | Placeholders |
|---|---|---|---|---|---|
| `http://r{0-3}.ortho.tiles.virtualearth.net/tiles/a{q}.jpeg?g=136` (Ortho4XP `BI.lay`) | HTTP/1.1, clear | 200 x 300 | 222 | 114 / 164 / 279 ms | 44 |
| `https://t.ssl.ak.tiles.virtualearth.net/tiles/a{q}.jpeg?g=15312` (AutoOrtho) | HTTP/2, TLS 1.3 | 200 x 300 | 317 | 86 / 179 / 301 ms | 44 |
| `https://ecn.t{0-3}.tiles.virtualearth.net/tiles/a{q}.jpeg?g=15312` (plan) | HTTP/2, TLS 1.3 | 200 x 300 | 287 | 86 / 163 / 256 ms | 44 |

Facts established:

- The three hosts are the **same Akamai property**: 300/300 bodies byte-identical (blake3),
  same `X-VE-*` headers, same `Server: Microsoft-IIS/10.0`. From this network every hostname
  (`ecn.t0..t3`, `t.ssl.ak`) resolved to the **same IP** (193.246.105.81); the four `ecn.t{n}`
  shards are therefore not four servers, only four hostnames (four HTTP/2 connections).
- `https://r{n}.ortho.tiles.virtualearth.net` fails TLS (certificate does not cover the name):
  the legacy host is HTTP/1.1 in clear only.
- HTTP/2 server settings (raw `h2` probe): `MAX_CONCURRENT_STREAMS 100`,
  `INITIAL_WINDOW_SIZE 65535`, `MAX_FRAME_SIZE 16384`. 100 streams per connection, so 4 hostnames
  give a ceiling of 400 concurrent requests before curl has to open extra connections.
- The `g=` query parameter is **mandatory** (HTTP 400 without it) but its value is free
  (`g=1`, `g=136`, `g=15312` return identical bytes). The edge cache key includes the full URL:
  the same tile with a new `g` value is a `TCP_MISS` again. A fresh `g` value per run is how the
  runs below are made edge-cold; a repeated value gives an edge-warm run.
- The **placeholder** ("no imagery") is HTTP 200, `Content-Type: image/png`, 1 033 bytes,
  header `X-VE-Tile-Info: no-tile`, blake3 `1abc2dcd9d153b229306d34096a74a2e97587a1b5a74f00b
  75990207d3e0b484`, identical on all hosts. 44/300 in the sample (14.7 % of the widened area,
  open sea to the south), 2 852/50 000 (5.7 %) in the sustained runs. Ortho4XP detects it by the
  byte count only (`O4_Imagery_Utils.py:1019-1030`).
- Photo tiles: mean 15.9 KB, p50 16.8, p90 21.5, p99 26.2, max 29.3 KB; the 50 000 tiles weigh
  751 274 429 bytes. Capture dates (`X-VE-TILEMETA-CaptureDateMaxYYMM`): 2022-08 (97/300),
  2024-06 (52), 2024-07 (41), 2017-10 (22), 2020-07 (15).
- `X-Cache-Remote` is present on edge misses (it names the parent Akamai node) and absent on
  hits; that is how the "edge hits" columns below are counted.

Decision: **`https://ecn.t{0-3}.tiles.virtualearth.net/tiles/a{q}.jpeg?g=15312`** (the plan's
choice is confirmed): HTTP/2 + TLS, four hostnames = four multiplexed connections, same bytes as
the legacy host. `t.ssl.ak` is an equivalent single-hostname fallback.

## 2. Sustained downloads with `curl --parallel` (HTTP/2, bodies to /dev/null)

Command per run (`curl_sustained.py`), e.g. 128 in flight:

```
/usr/bin/curl --http2 --silent --show-error --parallel --parallel-max 128 --max-time 60 \
  --connect-timeout 10 --retry 0 --write-out '<11 tab-separated fields>\n' --config curl_run128.cfg
```

with one `url = ".../a{quadkey}.jpeg?g=<generation>"` / `output = "/dev/null"` pair per tile in
the config file, hosts rotating `ecn.t0..t3`. A guard reads the write-out stream and kills curl
if 429/403 exceed 1 % (after 300 transfers) or 25 consecutive transfers fail. Latencies are
curl's `time_total` per transfer (queueing inside curl excluded, so they measure the CDN).

| In flight | Requests | Wall | req/s | MB/s | p50 | p90 | p99 | p99.9 | max | Conns | > 0.5 s | Edge hits |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 32 | 20 000 | 49.8 s | 401 | 6.5 | 73 ms | 101 | 262 | 327 | 0.63 s | 4 | 2 | 0 |
| 64 | 50 000 | 57.7 s | **867** | 13.0 | 69 ms | 94 | 156 | 292 | 1.11 s | 4 | 8 | 0 |
| 96 | 50 000 | 44.1 s | **1 133** | 17.0 | 77 ms | 113 | 242 | 378 | 0.94 s | 4 | 1 | 0 |
| 128 | 50 000 | 34.8 s | **1 436** | 21.6 | 82 ms | 114 | 255 | 542 | 0.75 s | 4 | 58 | 0 |
| 192 | 50 000 | 26.2 s | **1 909** | 28.7 | 95 ms | 128 | 255 | 390 | 5.08 s | 7 | 4 | 0 |
| 128, edge-warm | 50 000 | 25.7 s | 1 946 | 29.2 | 63 ms | 90 | 159 | 378 | 0.48 s | 5 | 0 | 12 500 |

Generations used: 15311 (64), 15310 (96), 15309 (128 and 128-warm), 15308 (192), 15307 (32).

Observations:

- **No push-back at all**: 270 000 requests in the six runs (315 000 in 20 minutes with the
  probes, the Python clients and the Ortho4XP-pattern run), 100 % HTTP 200, zero 429, zero 403,
  zero curl error, zero truncated body. The guard never fired.
- **Throughput scales linearly with concurrency** up to 192 in flight (401, 867, 1 133, 1 436,
  1 909 req/s): the CDN was not the limit. Latency per request barely moves (p50 69 -> 95 ms),
  so by Little's law the rate is concurrency / latency; the line reached 32 MB/s in 5 s buckets
  at 192 in flight and at 128 warm. The ceiling of this line was not found; the ceiling of the
  CDN was not found either.
- Rate within a run **rises during the first 10-20 s** (748 -> 974 req/s per 5 s bucket at 64,
  1 252 -> 1 551 at 128) as TCP and HTTP/2 flow-control windows open; a build that starts the
  download at t = 0 gets the ramp for free.
- **Stragglers exist but are rare and short**: over the 270 000 curl transfers, 3 above 1 s (1.11 s
  at 64, and 5.08 s + 4.06 s at 192, both TTFB-bound on the same hostname), 73 above 0.5 s. At
  128 in flight p99.9 is 0.54 s. curl_cffi in step 3 had one transfer that never completed inside
  its 30 s timeout (1 in 10 000). A hedge timer at ~5 x p50 (0.4-0.5 s) re-issues at most 0.1 %
  of requests.
- **Edge-warm** (same tiles again): p50 drops from 82 to 63 ms and the rate at 128 rises from
  1 436 to 1 946 req/s; only 12 500 of 50 000 (one hostname in four) were served from the
  first edge tier, the rest came from the parent tier with the same latency as cold. A second
  build of the same region a day later is not much faster on the network; the local raw-tile
  store is what makes it fast.
- With 192 in flight curl opened 3 extra connections after 12-23 s (7 total); 4 connections
  x 100 streams = 400 was not the reason (48 per connection), curl does it when a connection's
  window is exhausted. Above 128 in flight the marginal gain is on the line, not the CDN, and
  it comes with the 4-5 s stragglers.

For a ZL16 tile of 56 576 requests and 0.85 GB: **at 128 in flight the download takes ~40 s
edge-cold on this line** (56 576 / 1 436), 65 s at 64 in flight. The plan's floor of 90-120 s
at 800 req/s was pessimistic for this line; on a 10 MB/s line the same 0.85 GB is 85 s whatever
the concurrency.

Sixteen HTTP/1.1 connections, as Ortho4XP uses per texture, are measured in section 5.

## 3. Python clients (5 000 tiles, 64 in flight, edge-warm `g=15309`, two rounds)

Command: `py_clients.py --quadkeys aligned.txt --count 5000 --offset 10000 --inflight 64
--generation 15309 --rounds 2`. Load average at start: 6.4 (other benchmarks running).
CPU is `resource.getrusage(RUSAGE_SELF)` user + system of the process, divided by requests.

| Client | Round | req/s | MB/s | CPU per request | Cores at that rate | p50 / p99 |
|---|---|---|---|---|---|---|
| httpx AsyncClient, http2=True, trust_env=False | 1 | 1 041 | 17.1 | **0.789 ms** | 0.82 | 57 / 152 ms |
| httpx AsyncClient, http2=True, trust_env=False | 2 | 1 077 | 17.7 | 0.791 ms | 0.85 | 56 / 143 ms |
| curl_cffi AsyncSession (libcurl, HTTP/2 by ALPN) | 1 | 1 067 | 17.5 | **0.324 ms** | 0.35 | 51 / 245 ms |
| curl_cffi AsyncSession | 2 | 167 (*) | 2.7 | 0.317 ms | 0.05 | 53 / 146 ms |
| httpx AsyncClient, http2=False (64 HTTP/1.1 connections) | 1 | 223 | 3.7 | 2.987 ms | 0.67 | 216 / 1 140 ms |
| httpx AsyncClient, http2=False | 2 | 239 | 3.9 | 2.800 ms | 0.67 | 203 / 961 ms |
| httpx h2, trust_env=True (1 000 req) | 1 / 2 | 907 / 884 | 15.6 / 15.2 | 0.820 / 0.854 ms | 0.74 | 61 / 168 ms |
| httpx h2, trust_env=False (1 000 req) | 1 / 2 | 906 / 860 | 15.6 / 14.8 | 0.839 / 0.840 ms | 0.76 | 55 / 213 ms |

(*) one request of 5 000 never completed and hit the 30 s client timeout; the other 4 999 were
done in about 5 s (same p50 as round 1). This is the straggler case: without hedging one request
in 10 000 costs 30 s of wall time.

Observations:

- At 64 in flight both HTTP/2 clients saturate at ~1 050 req/s, above the 867 req/s of curl at
  the same concurrency in section 2 because the tiles were edge-warm here.
- **CPU: httpx-h2 0.79 ms per request, curl_cffi 0.32 ms.** At 1 400 req/s httpx would need
  1.1 cores, curl_cffi 0.45. Both fit the plan's dedicated network process; httpx is above the
  0.5 ms/req gate the plan set, curl_cffi is under it.
- HTTP/1.1 with 64 connections is 4.5 x slower and 3.6 x more CPU per request than HTTP/2 in
  httpx: the h2 multiplexing is what matters, not the client library.
- `trust_env` makes no difference in httpx (it reads the environment once per client). The 7 s
  of `_scproxy` in the Ortho4XP profile come from `requests`, which calls the macOS proxy lookup on
  every request of every new `Session`; the fix is one long-lived client, not the flag.

## 4. Overpass mirrors (the four Ortho4XP layer queries of +43+005, once each, timeout 120 s)

Command: `overpass_health.py --lat 43 --lon 5 --timeout 120` (all mirrors in parallel, the four
queries sequentially per mirror; `User-Agent: osxp-bench/0.0`; POST `data=`; httpx). The queries,
converted from `O4_Vector_Map.py` / `O4_OSM_Utils.get_overpass_data`:

```
[out:json][timeout:120];(node["aeroway"](43,5,44,6);way["aeroway"](43,5,44,6);rel["aeroway"](43,5,44,6););(._;>>;);out body qt;
[out:json][timeout:120];(way["highway"="motorway"](43,5,44,6);way["highway"="trunk"](43,5,44,6);way["highway"="primary"](43,5,44,6);way["highway"="secondary"](43,5,44,6);way["railway"="rail"](43,5,44,6);way["railway"="narrow_gauge"](43,5,44,6););(._;>>;);out body qt;
[out:json][timeout:120];(way["natural"="coastline"](43,5,44,6););(._;>>;);out body qt;
[out:json][timeout:120];(rel["natural"="water"](43,5,44,6);rel["waterway"="riverbank"](43,5,44,6);way["natural"="water"](43,5,44,6);way["waterway"="riverbank"](43,5,44,6);way["waterway"="dock"](43,5,44,6););(._;>>;);out body qt;
```

Answers (decompressed JSON): airports 1.18 MB / 10 594 elements, big_roads 27.55 MB / 189 047,
coastline 4.41 MB / 39 969, water 12.55 MB / 122 147; 45.7 MB in total for the four layers
against 78 MB of XML `out meta` in Ortho4XP (identical element counts on every live mirror).

| Mirror | Resolves to | `/api/status` | airports | big_roads | coastline | water | Data timestamp |
|---|---|---|---|---|---|---|---|
| `overpass-api.de` | 162.55.144.139 + 65.109.112.52 (round robin) | 200, "Rate limit: 2" | 2.6 s | 22.8 s | **429** after 15 s | **429** after 15 s | 00:47:46Z (live) |
| `lz4.overpass-api.de` | 65.109.112.52 ("lambert") | 200, rate limit 2 | 2.0 s | 8.1 s | 1.9 s | 10.3 s | live |
| `z.overpass-api.de` | 162.55.144.139 ("gall") | 200, rate limit 2 | 2.6 s | 22.6 s | **429** | **429** | live |
| `overpass.kumi.systems` | CNAME `overpass.private.coffee` = 193.219.97.30 | 200, rate limit 0 | **timeout 120 s** | 65.0 s (27.39 MB, 188 518 el.) | **timeout 120 s** | **504** after 31 s | 2026-07-28 (6 weeks old) |
| `maps.mail.ru/osm/tools/overpass` | 95.163.216.90 | 200, rate limit 0 | 2.5 s | 6.8 s | 6.7 s | 4.4 s | live |
| `overpass.openstreetmap.fr` | 45.147.209.254 | **403** (status endpoint blocked; queries work) | 7.7 s | 10.1 s | 2.1 s | 4.3 s | live, no gzip |
| `overpass.private.coffee` | 193.219.97.30 | 200 (1.1 s) | **504** after 31 s | 504 | 504 | 504 | - |
| `overpass.osm.jp` | 163.43.31.254 | **TLS certificate is for `openstreetmap.jp` only**; with verification off, 404 | dead | dead | dead | dead | - |

Observations:

- **Three names, two servers**: `overpass-api.de` round-robins between the machines behind
  `lz4.` and `z.`; they share one per-IP quota of 2 slots. The parallel probe put three
  concurrent queries on two servers and got the 429s: they were self-inflicted, and they are
  exactly what a client that treats the three names as three mirrors will get.
- **After the 429 episode the "gall" machine (162.55.144.139) stopped answering this IP**: a
  sequential retest 5 minutes later gave `ConnectError`/`ConnectTimeout` on all four queries for
  both `overpass-api.de` and `z.overpass-api.de`, and `curl https://z.overpass-api.de/api/status`
  connected in TCP but got no byte within 10 s, while `lz4.` answered in 0.18 s. Through the
  round-robin name, one DNS answer in two hangs for the connect timeout. That is the Ortho4XP
  symptom recorded in `docs/plan/DIAGNOSTIC.md` ("overpass-api.de coupait").
- `kumi.systems` and `private.coffee` are the **same machine**, six weeks behind on data, with
  30 s gateway timeouts and 65 s for the roads: unusable for a build.
- The four layers on one healthy mirror, sequentially: **20.4 s** (`maps.mail.ru`), 22.3 s
  (`lz4`), 24.2 s (`.fr`). The 43 s of the Ortho4XP diagnostic was two mirrors and XML.
- `/api/status` is a usable health check (0.2-0.3 s) on the DE cluster and `maps.mail.ru`;
  `.fr` refuses it (403) while serving queries, so a 403 on `/api/status` must not disqualify a
  mirror by itself.

## 5. The Ortho4XP pattern for comparison: 16 HTTP/1.1 connections, clear `r{0-3}`

Command: `curl_sustained.py --count 10000 --inflight 16 --host ortho4xp_r --generation 136
--no-http2` (clear HTTP/1.1 on `r{0-3}.ortho.tiles.virtualearth.net`, the pattern of one Ortho4XP
texture download with its 16 threads, but with keep-alive across the whole run, which Ortho4XP does
not have because it creates a new `Session` per texture).

| In flight | Requests | Wall | req/s | MB/s | p50 | p90 | p99 | max | Connections opened |
|---|---|---|---|---|---|---|---|---|---|
| 16 x HTTP/1.1 | 10 000 | 45.7 s | **219** | 3.5 | 67 ms | 87 | 279 | 0.58 s | 27 |

Per-request latency is the same as in HTTP/2 (the CDN answers in ~65 ms either way); the
difference is only how many requests are in flight. Ortho4XP's best case for a ZL16 tile is
therefore 56 576 / 219 = 258 s of pure download, and the measured Ortho4XP rate is lower
(0.105 s per request per thread, joins between textures: ~150 req/s, 490 s at ZL16) because it
reconnects for every texture. At 128 HTTP/2 streams, 50 000 of these tiles took 35 s in
section 2, about 40 s for the whole texture-aligned tile.

## 6. Esri World Imagery Clarity (`Arc@`): 64 or 128 requests in flight

Date: 2026-09-15, 00:50, the user's home connection (the one that gave Bing 16.5 MB/s, section 2
and `batch-6-tiles-zl16.md`), no build running. A user building five ZL16 tiles with `Arc@` saw
3 to 4.5 MB/s (about 290 requests/s at 64 in flight) and asked whether OrthoStudio XP was really
faster than Ortho4XP. OrthoStudio XP's own `Fetcher` (AIMD, `start_in_flight` = `max_in_flight`,
hedge after 3 s, 20 s timeout), 39 x 39 = 1 521 ZL16 pieces per run around a city the user had never
downloaded, bodies thrown away, the two settings alternated (64, 128, 128, 64) against the time of
day:

| City | In flight | Wall (s) | Requests/s | MB/s | KB per piece | p50 / p95 (s) | Errors |
|---|---:|---:|---:|---:|---:|---|---:|
| Lyon | 64 | 7.8 | 195 | 4.58 | 23.5 | 0.203 / 0.423 | 0 |
| Bordeaux | 128 | 4.6 | 332 | 7.52 | 22.7 | 0.358 / 0.527 | 0 |
| Lille | 128 | 3.8 | 403 | 7.07 | 17.6 | 0.272 / 0.468 | 0 |
| Toulouse | 64 | 9.7 | 157 | 3.51 | 22.4 | 0.204 / 0.364 | 0 |

At 64 in flight the fetcher also lowered its window once on a latency rise (`throttled` in its
stats); at 128 it did not. Mean: 176 requests/s and 4.0 MB/s at 64, 368 requests/s and 7.3 MB/s at
128, every answer a 200. The server answers each request in about the same time either way, so
twice the requests in flight give about twice the throughput: `Arc@` gets `max_in_flight = 128`,
Bing's measured ceiling. Bing stays three to four times faster on this line (950 to 1 400
requests/s). 256 was not tried (the user allowed a few thousand pieces), nor `Arc`, the HTTP/1.1
host at 16 in flight.

## 7. Every server of the registry, to the most requests in flight it takes

Date: 2026-09-15, 08:08-08:20, the user's home connection, no build running (OrthoStudio XP not
started). The user asked for connection tests going to the most requests each provider allows.
OrthoStudio XP's own `Fetcher` (AIMD, `start_in_flight` = the level tried, hedge after 3 s, 20 s
timeout, two attempts), one server at a time, ZL16 pieces never asked for in the run (a new block
around a city per step), bodies thrown away, 8 s between steps. A step aims at 8 s from the rate of
the step before (1 024 to 12 100 pieces). The rate kept is the **bulk** rate: the requests answered
between the 10th and the 90th percent of a step over the time between them. In the first attempt
of this run, six Bing requests out of 4 096 timed out after two attempts and made the step last 36
s, 113 requests/s by wall time for a bulk of more than 1 000: a build fetches such pieces again in
its second pass. A server stops rising at the first 429 or 403, past 1 % of errors, past a p95 of
5 s, or when the bulk rate gains less than 15 % on the step before; the services of a state stop at
128. 101 414 pieces, 1.42 GB, no 429 and no 403 anywhere.

| Server (city) | In flight | Pieces | Bulk req/s | Bulk MB/s | Mean window | p50 / p95 (s) | Answers | Stopped because |
|---|---:|---:|---:|---:|---:|---|---|---|
| Bing `BI` (Angers) | 128 | 10 000 | 1 123 | 16.1 | 48 | 0.106 / 0.170 | 9 999 x 200, 1 timeout | |
| | 192 | 12 100 | 527 | 6.9 | 58 | 0.096 / 0.197 | 12 100 x 200 | no gain |
| Esri Clarity `Arc@` (Rennes) | 128 | 3 025 | 411 | 7.6 | 128 | 0.300 / 0.522 | 3 025 x 200 | |
| | 192 | 5 041 | 522 | 8.1 | 192 | 0.405 / 0.588 | 5 041 x 200 | |
| | 256 | 5 625 | 448 | 6.3 | 256 | 0.559 / 0.727 | 5 623 x 200, 2 x 502 | no gain |
| Esri `Arc` (Dijon) | 16 | 1 024 | 249 | 4.1 | 16 | 0.062 / 0.090 | all 200 | |
| | 32 | 4 096 | 512 | 5.4 | 32 | 0.060 / 0.080 | all 200 | |
| | 64 | 8 281 | 915 | 11.4 | 62 | 0.066 / 0.100 | all 200 | |
| | 128 | 12 100 | 1 124 | 12.2 | 85 | 0.072 / 0.110 | all 200 | |
| | 256 | 12 100 | 1 116 | 12.8 | 75 | 0.070 / 0.125 | all 200 | no gain |
| USGS (Denver) | 64 | 1 024 | 168 | 5.0 | 64 | 0.378 / 0.541 | all 200 | |
| | 128 | 2 704 | 280 | 5.3 | 128 | 0.459 / 0.577 | all 200 | last level |
| PDOK (Utrecht) | 16 | 1 024 | 122 | 2.8 | 16 | 0.120 / 0.208 | all 200 | |
| | 32 | 2 025 | 181 | 3.7 | 26 | 0.128 / 0.267 | all 200 | |
| | 64 | 2 916 | 108 | 1.8 | 17 | 0.130 / 0.431 | all 200 | no gain |
| geoportail.lu `Lux` (Luxembourg) | 16 | 1 024 | 130 | 3.4 | 15 | 0.035 / 0.133 | all 200 | |
| | 32 | 2 116 | 102 | 1.3 | 11 | 0.035 / 1.283 | 2 114 x 200, 2 timeouts | no gain |
| PNOA `SP` (Zaragoza) | 32 | 1 024 | 152 | 2.4 | 16 | 0.071 / 0.436 | all 200 | |
| | 64 | 2 500 | 468 | 6.9 | 42 | 0.080 / 0.172 | all 200 | |
| | 128 | 7 569 | 584 | 7.5 | 42 | 0.084 / 0.179 | all 200 | last level |
| GSI `JP` (Osaka) | 8 | 1 024 | 11 | 0.2 | 8 | 0.749 / 0.974 | all 200 | |
| | 16 | 1 024 | 22 | 0.4 | 16 | 0.749 / 0.974 | all 200 | |
| | 32 | 1 024 | 46 | 0.9 | 32 | 0.749 / 0.976 | all 200 | |
| | 64 | 1 024 | 103 | 1.2 | 64 | 0.739 / 0.896 | 878 x 200, 146 x 404 (sea) | last level |

"Mean window" is the fetcher's own in-flight count over the middle of the step: below the level
tried, its AIMD lowered the window on a rise of latency. Reading:

- **Bing and Esri `Arc` stop at the line**, about 1 100 requests/s and 12 to 16 MB/s, with a p50 of
  70 to 110 ms: the servers answer as fast at 256 as at 16, and the AIMD holds the window near 50
  to 85. Bing stays at 128 (192 gave less, the line's traffic of the moment). `Arc` was at 16, a
  guess for an HTTP/1.1 host: 128 makes it **4.5 times faster**, as fast as Bing on this line.
- **Esri Clarity `Arc@` is the server's limit**: its latency grows with the load (p50 0.30 s at
  128, 0.56 s at 256) and two 502 came at 256. 192 gave 27 % more than 128: `max_in_flight` 192.
- **PDOK and geoportail.lu slow down early**: the Netherlands at 64 (from 181 to 108 requests/s),
  Luxembourg at 32 (from 130 to 102, a p95 of 1.3 s and two timeouts). PDOK goes from 16 to 32,
  Luxembourg stays at 16. The five Dutch layers are one host.
- **USGS, Spain and Japan** rose without an error up to the last level tried: USGS 128 (from 64),
  PNOA 128 (from 32; the AIMD settles near 42), GSI 64 (from 8). From Europe each GSI answer takes
  0.75 s, so the requests per second follow the requests in flight: a ZL16 tile of Japan goes from
  about 70 minutes of download at 8 to about 8 at 64.

Single runs at one hour of one day: the order of magnitude and the step where a server stops
matter, not the last digit. The script is kept with the session's scratch files, not in the
repository; `MEASURED_IN_FLIGHT` in `tests/test_imagery_providers.py` holds the values set.
