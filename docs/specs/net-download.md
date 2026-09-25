# Spec: the download client (`orthostudio.net`)

Status: **P1, final** for the generic fetcher (`src/orthostudio/net/fetch.py`). Sections 1-4 are the
contract implemented in P1; section 5 keeps the P0 requirements that belong to later layers
(placeholders, parent memo, Overpass) so that the measurements that justify them stay in one
place. Measurements: `docs/benchmarks/network.md` (2026-09-12); section numbers "s. N" below
refer to it. Amended after a user build lost three tiles to five timed-out requests
(2026-09-13): R3, R4, section 5.6 and the new fields of section 1, marked **(review
2026-09-13)**.

Origin in Ortho4XP: `O4_Imagery_Utils.py:1003-1080` (`http_request_to_image`: `requests.Session`
per call, `'[200]' in str(r)`, 8 retries with `time.sleep(2**k)`), `O4_Tile_Utils.py:21-40` and
`O4_Imagery_Utils.py:1329-1368` (16 threads per texture, joined before the next texture),
`O4_OSM_Utils.py:12-19, 513-585` (Overpass servers and back-off).
Decision: **replaced** (a wanted difference on every point, section 6). The only network rule
kept from Ortho4XP, the parent fallback for missing tiles (up to 6 levels), belongs to the imagery
layer, not to this module.

## 1. Scope and contract

`orthostudio.net.fetch` downloads many small bodies (16-30 KB tiles, s. 1) over HTTP with bounded,
self-adjusting concurrency. It knows nothing about providers: no placeholder detection, no parent
fallback, no URL templates. Those live in `orthostudio.imagery`. It is one asyncio task group; the
workers of a build never touch a socket, they ask the store, and the store asks this module.

```
FetchRequest(key, url, headers={}, host_group="")     host_group = concurrency key (provider code)
FetchResult(key, status, body, headers, elapsed, attempts, hedged, error, detail="")
FetchStats(done, total, bytes, in_flight, req_per_s, errors, retries, hedges, throttled)
Fetcher(max_in_flight=128, start_in_flight=64, hedge_after_s=3.0, timeout_s=20.0,
        max_attempts=4, http2=True, max_pushbacks=12, pushback_budget_s=120.0)
  await fetch_many(requests, on_result=None, on_stats=None, cancel=None,
                   keep_results=True, limit_in_flight=None, max_attempts=None)
                   -> list[FetchResult]
  stats() -> FetchStats
fetch_all(requests, **kwargs) -> list[FetchResult]     synchronous helper (asyncio.run)
```

Result semantics:

- `status` is the HTTP status of the answer that was kept (0 when no answer was ever received);
  `body` its bytes (in memory; empty when there was no answer); `headers` its response headers
  with **lower-cased names**.
- `error` is `None` whenever an HTTP answer with a status that this module does not retry was
  received, **including 404 and other 4xx**: a 404 is an answer, and what it means (parent fallback,
  placeholder) is the imagery layer's decision. `error` is an `orthostudio.errors` code when the
  request was given up: `NET_TIMEOUT`, `NET_CONNECTION_FAILED`, `NET_RATE_LIMITED` (429 after the
  last attempt), `NET_SERVER_ERROR` (5xx after the last attempt), `SYS_CANCELLED`. When an error
  carries a last answer (429, 5xx), `status`/`body`/`headers` hold that answer.
- `attempts` counts the HTTP transfers started for the request, hedges and pushed-back
  transfers included; `hedged` is true when a hedge was issued for it (whichever copy won);
  `elapsed` is the wall time from the first dispatch to the delivery of the result.
- **(review 2026-09-13)** `detail` is libcurl's error line for the last transport failure of a
  request given up (`curl: (28) Operation timed out after 20001 milliseconds with 0 bytes
  received`, `curl: (56) Recv failure: Connection reset by peer`, `curl: (92) HTTP/2 stream 5
  was not closed cleanly: INTERNAL_ERROR`), without curl_cffi's prefix and documentation link,
  at most 200 characters; empty when the kept outcome is an HTTP answer (its status says it).
  The curl code tells a timeout from a reset or a stream error, the byte count whether the
  headers had arrived. Before this field the texture log could only say `IMG_TILE_MISSING`;
  the kind of failure had to be dug out of the chunk container.
- **(review 2026-09-13)** `limit_in_flight` caps the requests admitted per group for one run
  below the AIMD window (the window keeps adapting; hedge cap `max(1, min(window, limit) //
  2)`), and `max_attempts` replaces the constructor's for one run. The next run is back to the
  constructor's values. They exist for a caller's spaced retry rounds (5.6), which must keep the
  session, the window and the pause state of the group, so that a `Retry-After` still running
  is obeyed, instead of opening a new `Fetcher`.
- With `keep_results=False` (review) the returned list carries the results **without their
  bodies** (empty `body`); the bodies reach `on_result` only. A caller that consumes through
  the callback does not pay a second copy of every body for the life of the run.
- The returned list is in **request order**; `on_result` is called as results complete, in
  completion order, from the event loop thread (it must be quick: append to a queue, write a
  file).

## 2. Requirements and rules (implemented in P1)

### R1. Protocol and connections

- HTTP/2 over TLS is the normal case: 64 HTTP/1.1 connections gave 223 req/s and 3 ms CPU per
  request against 1 041 req/s and 0.8 ms in HTTP/2 (s. 3); the Ortho4XP pattern (16 clear HTTP/1.1
  connections) tops at 219 req/s (s. 5). The client asks libcurl for "HTTP/2 over TLS, HTTP/1.1
  in clear" (`CURL_HTTP_VERSION_2TLS`), so a local `http://` test server and an HTTP/1.1-only
  provider work unchanged, with the lower concurrency the registry gives them. `http2=False`
  forces HTTP/1.1.
- One `AsyncSession` (one libcurl multi handle, one connection cache) for the life of the
  `Fetcher`: connections are opened at the first request and kept alive between textures. The
  ramp of the TCP and HTTP/2 windows (748 -> 974 req/s in the first 10-20 s at 64 in flight,
  s. 2) is paid once per build, not once per texture as in Ortho4XP.
- Hostname rotation (`ecn.t0..t3`, four HTTP/2 connections, 100 streams each, s. 1) is done by
  the URL the imagery layer builds (`{switch:...}` in the template); this module does not
  rewrite URLs.
- **Every request says who is asking**: `User-Agent: OrthoStudio-XP/<version>
  (+<repository>)` (`USER_AGENT`), which a caller may replace per request. Nothing was sent
  before: services that carry us for nothing could not tell who was knocking, Overpass asks a
  client to name itself, and a server may simply refuse an anonymous request, as the Brazilian
  water agency's does for the ANADEM relief (403 without one, 206 with any, measured
  2026-09-20). Checked against the providers after the change: Bing, Esri and Overpass answer
  as before.

### R2. Concurrency per host group: AIMD

State per `host_group` (all requests with the same key share it):
`window` (allowed in flight), `in_flight`, `paused_until`, a ring of the last 1 000 latencies,
and counters. Default `host_group=""` is a group like any other.

- **Start** at `start_in_flight` (64 for Bing: 867 req/s, p99 156 ms, zero push-back, s. 2).
- **Additive increase**: +1 when `window` successful completions have been observed since the
  last change (one "round"), until `max_in_flight` (128 for Bing: 1 436 req/s, last level with
  p99.9 < 0.6 s and no transfer above 1 s, s. 2). A success for AIMD is any HTTP answer that
  is not a push-back (2xx, 3xx, 404 and other 4xx except 429).
- **Multiplicative decrease** (`window = max(min_in_flight, window // 2)`, `min_in_flight = 8`
  or `start_in_flight` if smaller): on any **429 or 503**, and on a latency signal: p90 of the
  last 100 completions > 2 x the p90 of the completions since the last decrease (at most the last
  1 000; the ring is emptied at each decrease) **and** p90 >= 0.2 s (below that, the spread is
  jitter, not congestion: Bing at 128 in flight has p50 82 ms and p90 114 ms, s. 2). A tail that
  rises, not a wide one: the reference was the median of the ring until 2026-09-15, when a user's
  coastal tile at Esri Clarity fell to 40 requests/s for 25 s. A piece of sea answers in 60 ms, a
  piece of land in 400 ms: a texture of both has a p90 far above its median with no congestion,
  and the window halved round after round down to 8 (39 halvings on such a mix in
  `test_group_latency_signal_is_a_rising_tail_not_a_mix_of_sea_and_land`; now none, one halving
  from sea to land, and a server whose tail doubles is still seen). A timeout multiplies by 0.75, and since 0.1.14 a **refused or dropped connection** halves like
  a 503: a server that defends itself by closing the door says no as plainly as one answering
  429, and until then only the two statuses lowered the window, so such a server was knocked on
  by the whole window until the attempts ran out (a user's EOX source, 2026-09-24). Other 5xx are
  retried but do not move the window (a single 500 is not a congestion signal). After a decrease no further
  decrease is taken until `window` new completions have been observed (100 for the latency
  signal, so that the recent ring holds only post-decrease samples): one burst of 429, or one
  stalled connection whose 32 streams complete late together, halves once, not once per
  answer. (The first Bing run of P1 without that rule fell from 128 to 12 on a single burst.)
- **Pause on 429**: no new dispatch for the group until `Retry-After` (integer seconds or HTTP
  date, clamped to [1, 120] s) or, without it, 5 s, doubling on repeats within 60 s up to 30 s.
  The affected request is re-queued **at the tail** of the group's queue and retried after
  the pause. (Review.) A 429 obeyed is not a failure of the request: it does **not** count
  towards `max_attempts`. It has its own bound per request, `max_pushbacks` (12) and
  `pushback_budget_s` (120 s of cumulated pause), after which a 429 costs an attempt like any
  other failure and the request ends with `NET_RATE_LIMITED`. The tail re-queue spreads a
  storm over the queue instead of letting the first window of requests absorb it: with a
  window of 8, forty 429 with `Retry-After: 1` used to exhaust `max_attempts = 4` for eight
  requests and fail their texture although the provider had only asked to wait; now every
  request sees at most one pushback and none is lost.
- The benchmark never saw a 429/503 from Bing in 315 000 requests (s. 2); this branch is
  verified against the local test server, not against Bing.

### R2b. Requests a second per host group

`Fetcher(req_per_s=)` (`None`: no ceiling) is a **ceiling the group approaches, not a speed it
holds**. The group starts at `RATE_START` (a quarter) of it, climbs `ceiling / RATE_STEPS` per
`RATE_ROUND` answers, falls with the window in `_decrease` on the same signals and by the same
factor, and never goes below `RATE_FLOOR` (a tenth) nor above the ceiling. At most one request is
started every `1 / rate` seconds per `host_group`, on top of R2's window: the group holds
`next_start`, dispatch admits nothing before it, and the loop sleeps until then. The two do not
compound: what a group achieves is `min(window / latency, rate)`, so whichever binds, binds, and
a push-back that halves both halves the result once.

**Why a ceiling and not a speed.** Every rate in the registry was measured once, from one machine, on one day. EOX's 224 was real here and eleven times what a user's address could get before the server stopped answering (2026-09-24). A figure like that is worth keeping as a limit nobody should pass, and worthless as an instruction. Climbing to it converges on what this line and this route allow, and costs 1.1 % of a full tile: measured, not guessed, which is why there is no exception for a rate a user declared themselves. It comes from the provider's
`server_req_per_s` (`imagery-providers.md` 4) for the build (`pipeline/textures.py`) and for the
probe (`estimate.probe`, which asked for every chunk of a texture at once).

**Why a second limit.** R2 counts connections; a server may count requests. Apache with
mod_evasive, which many small services run, serves a few images and then blocks the caller for
seconds, and every request sent while blocked pushes the end of the block further away. On a fast
line a window of 16 is hundreds of requests a second, so `max_in_flight` alone cannot slow a
caller down to what such a server accepts. A user who wrote `server_req_per_s = 3` in his own
source to spare one watched the build ask for hundreds a second and fail (2026-09-24): the field
set the estimate alone.

### R3. Hedging stragglers

- Motivation: at 128 in flight p99.9 is 0.54 s and max 0.75 s, but at 192 two transfers took
  4-5 s and curl_cffi saw one request in 10 000 hang until its 30 s timeout (s. 2, s. 3).
  Without hedging one such request costs 30 s of wall time and blocks its texture.
- Rule: a transfer still without a complete answer after `hedge_after_s` is doubled: a second
  transfer of the same request is started (same URL; the imagery layer's URL rotation and
  libcurl's connection pool decide which connection it takes). The first **successful** answer
  wins and the other copy is cancelled (libcurl removes the easy handle: `RST_STREAM` on HTTP/2,
  a closed connection on HTTP/1.1). If the first copy to finish failed, the other is awaited.
  Both failing = the attempt failed.
- Hedges do not consume a window slot (they are 0.05-0.1 % of requests: 58 of 50 000 above
  0.5 s at 128, s. 2) but are capped at `max(1, window // 2)` in flight per group, so a dead
  network cannot double the load.
- Default `hedge_after_s` is 3 s in the constructor (safe for slow lines); the imagery layer
  passes a provider-tuned value (0.5 s for Bing, ~5 x p50, s. 2).
- **(review 2026-09-13)** Over HTTP/2 a hedge (same URL, hence same hostname) is multiplexed on
  the connection of the primary, and so is the retry of R4: neither changes the route. That is
  why neither helped against the per-URL stalls of R4. The retry rounds of the textures ask
  another hostname of the provider in each round (5.6).

### R4. Attempts, timeouts, back-off

- Per-transfer timeout `timeout_s` (libcurl's total timeout; the connect phase is bounded by
  `min(10, timeout_s)`). Bodies are read whole (no streaming).
- Retried, up to `max_attempts` failed transfers (hedges count, 429 obeyed within the
  pushback budget of R2 does not): connection failures, timeouts, 5xx, 429 beyond the budget
  (after the pause of R2). Back-off between attempts: 0.5 s, 1 s, 2 s, 4 s (cap), plus up to
  25 % random jitter; the slot is released during the back-off.
- Never retried: 2xx, 3xx (redirects are followed by libcurl, max 5), 4xx other than 429.
- After the last attempt the request is delivered with the error code of its last failure and
  the last answer if there was one (section 1).
- **(review 2026-09-13) Attempts and back-off unchanged; long stalls are the texture layer's
  job (5.6).** Evidence: the first six-tile build of a user (Bing ZL16, job
  `20260913-183959-fe0d`, 212 000 requests over four tiles at 460-850 req/s) lost three tiles to
  five failed requests out of 212 000, all `NET_TIMEOUT` (reason code kept in the chunk
  containers). Each time the 255 other chunks of the texture arrived within the same second
  and the failed chunk came 43 s later: a transfer and its hedge (issued after 1 s) timed out at
  20 s, 1-1.25 s of back-off (two failures already: hedges count), then the second dispatch and
  its hedge timed out as well, four failures = `max_attempts`. The run counters agree (+46+008:
  1 error, 1 retry, i.e. two dispatches). A URL that stalls for more than 40 s while the provider
  serves hundreds of other requests per second is not helped by this module's retries:
  - more attempts: every dispatch of a stalled request costs 21 s and holds a window slot, on
    the same connection (R3); `max_attempts = 8` would have waited ~90 s with retries 1-4 s
    apart, and would double the time to report a provider outage;
  - a longer back-off cap: a stalled request with hedges only ever waits `_backoff(2)` = 1 s
    between its two dispatches; the 4 s cap is not reached with 4 attempts, so raising it
    changes nothing for a stall, and fast failures (reset, 503) are covered by 0.5-2 s;
  - P0 saw zero 5xx and zero curl errors in the 270 000 requests of its sustained runs (s. 2);
    the only failure measured was a hang, one in 10 000 transfers with curl_cffi (s. 3), which
    the hedge covers when the hang belongs to the transfer.
  The in-fetcher budget therefore stays tuned for blips (a reset, a 503, a straggler) and for
  failing fast on an outage. The remedy lives where the context is: the texture pipeline asks
  again, once the rest of the tile is done, for the chunks whose failure says "try later", in
  rounds spaced by 5, 15 and 45 s (5.6).

### R5. Cancellation

- `cancel: asyncio.Event`. When set, no new transfer is started, every in-flight transfer is
  cancelled (handle removed from the multi), and `fetch_many` returns within **1 s** with a
  `SYS_CANCELLED` result for every request not yet delivered. Results already delivered are
  kept.
- Closing the `Fetcher` (`aclose()`, or the `async with` block) cancels the same way.

### R6. Statistics

- `FetchStats` is a snapshot: `done` (results delivered), `total` (requests of the current
  `fetch_many`), `bytes` (bodies received, hedge losers excluded), `in_flight` (transfers
  running, hedges included), `req_per_s` (completions over the last 5 s), `errors` (results
  delivered with an error), `retries` (transfers started beyond the first per request, hedges
  excluded), `hedges` (hedge transfers started), `throttled` (some group is paused or has had a
  multiplicative decrease in the last 10 s).
- `on_stats` is called at most **4 times per second** (every 0.25 s while something is in
  flight, and once at the end), `stats()` may be called at any time from the loop thread.
  `windows()` exposes the current AIMD window per host group (tests, UI).

### R7. Client library and cost

- **curl_cffi** (`requests.AsyncSession`, libcurl multi with HTTP/2 in C): 0.32 ms CPU per
  request against 0.79 ms for httpx-h2 (s. 3); at 1 400 req/s that is 0.45 core versus 1.1.
  Cancellation is `curl_multi_remove_handle`, which is what hedging needs. No browser
  impersonation, TLS verification on (certifi bundle).
- The one-in-10 000 hang seen in P0 with curl_cffi (s. 3) is the case R3 covers; the P1 gate
  is "5 000 tiles at 128 in flight complete with zero result missing" (section 3).

## 3. Acceptance tests (`tests/test_net_fetch.py`, `tests/test_net_bench.py`)

Unit tests run against a local HTTP/1.1 server in a thread (no network), which serves:
deterministic 200 bodies; 404; 500 once then 200; 503 once then 200; 429 with `Retry-After: 1`
once then 200; a straggler that sleeps 5 s on the first request and answers at once on the
second; a connection dropped without an answer once then 200; a slow route (0.5 s) for the
latency signal.

| Test | Expectation |
|---|---|
| normal | 200, body bytes and length as served, `error is None`, `attempts == 1`, results in request order, `on_result` called once per request |
| 404 | `status == 404`, `error is None`, `attempts == 1` (no retry) |
| 500 then 200 | `status == 200`, `attempts == 2`, `retries == 1` in the stats |
| 429 with `Retry-After: 1` | `status == 200`, second transfer >= 1 s after the first, `throttled` seen true, window halved for the group; with `max_attempts = 1` the request still succeeds (the 429 cost no attempt) and `attempts == 2`; with `max_pushbacks = 0` it ends `NET_RATE_LIMITED` after one transfer; a route that always answers 429 ends `NET_RATE_LIMITED` after `1 + max_pushbacks` transfers |
| straggler 5 s, `hedge_after_s = 0.3` | `status == 200` in < 2 s, `hedged`, `hedges == 1` in the stats |
| dropped connection | `status == 200`, `attempts == 2`, and the window halved for the group (R2) |
| `req_per_s = 10`, 6 requests | the run lasts at least `(6 - 1) / 10` s and no two requests reach the server closer than `1 / 20` s (R2b) |
| AIMD up | start 4, max 16, 400 fast requests: window of the group reaches 16 |
| AIMD down | after 300 fast completions, 200 slow ones (0.5 s): window of the group is halved at least once |
| cancellation | 200 requests to a slow route, `cancel.set()` after 0.3 s: `fetch_many` returns within 1 s, undelivered requests carry `SYS_CANCELLED` |
| transport detail (review 2026-09-13) | a timeout given up has `detail` starting `curl: (28) Operation timed out after`, without curl_cffi's prefix or link; a dropped connection `curl: (`; a 500 given up has `status == 500` and an empty `detail`; the helper keeps curl's line of an HTTP/2 stream error and caps it at 200 characters |
| per-run overrides (review 2026-09-13) | `limit_in_flight = 2` on a fetcher at 16: 12 requests of 150 ms take >= 0.85 s and the window stays 16; `max_attempts = 1` on a fetcher at 4: one transfer; the next run without overrides retries a 500 again; 0 for either raises `ValueError` |
| stats | `on_stats` called <= 4 times per second of run, final `done == total`, `bytes` = sum of bodies |
| `fetch_all` | synchronous helper returns the same results |

Network tests (`-m network`, never in CI): 200 real Bing tiles on `ecn.t{0-3}`, `g=15312`,
all `status == 200` with bodies; and a short benchmark, 5 000 tiles at 128 in flight, reporting
req/s and CPU per request (`resource.getrusage`) to compare with P0's 1 436 req/s (curl
--parallel) and 0.32 ms (curl_cffi at 64 in flight). Results are recorded in
`docs/benchmarks/network.md` by whoever runs them. First P1 run (2026-09-12 03:35, load 4.5,
`nice -n 10`, same line as P0, `g=15312`): 5 000 tiles at 128 in flight in 4.70 s =
**1 065 req/s**, 17.5 MB/s, **0.348 ms CPU per request** (0.37 core), 3 hedges, 0 retries,
0 errors; a raw `curl_cffi` semaphore loop at 128 on the same line minutes earlier gave
905 req/s and 0.339 ms (p50 139 ms against 82 ms in P0: the line, not the client, was slower
that night). The fetcher costs +3-14 % CPU over the bare client for AIMD, hedging and stats.

## 4. Inputs, outputs, non-goals

- Input: a sequence of `FetchRequest`; output: one `FetchResult` per request. Bodies stay in
  memory (16-30 KB typical, s. 1); large downloads (DEM archives, Global Scenery) will use a
  streaming variant of this module, not `fetch_many`.
- Not here: placeholder detection (header, hash, size), parent-404 memo, Overpass mirror
  policy, AutoOrtho co-existence (Bing ceiling shared at 64), 403 ban probing. They are
  specified in section 5 for the layers that own them.

## 5. Requirements kept for later layers (from the P0 measurements)

### 5.1 Bing hosts (imagery registry)

- Template `https://ecn.t{n}.tiles.virtualearth.net/tiles/a{quadkey}.jpeg?g=15312`, `n`
  rotating 0-3 per request. `t.ssl.ak.tiles.virtualearth.net` is the declared fallback host;
  the clear HTTP/1.1 `r{n}.ortho` host of Ortho4XP is dropped (same bytes, no TLS, no HTTP/2; s. 1).
- `g=` is mandatory and free; it is part of the CDN cache key, so it is a constant in the
  provider registry and never varies between runs (s. 1).
- Per-provider ceilings live in the registry (TOML): Bing 128, HTTP/1.1-only providers 16,
  Overpass 2. The global ceiling is the sum, never more than 256 streams. 192 buys 33 % more on
  a fast line but brought the 4-5 s stragglers (s. 2): expert setting only.
- If AutoOrtho is running (its process is detected), the Bing ceiling is shared: OrthoStudio XP uses
  at most 64 so that the pair stays under the 128 measured as safe.

### 5.2 Placeholder detection (imagery)

- Bing "no imagery" tile: HTTP 200, `Content-Type: image/png`, 1 033 bytes,
  `X-VE-Tile-Info: no-tile`, blake3
  `1abc2dcd9d153b229306d34096a74a2e97587a1b5a74f00b75990207d3e0b484` (s. 1), identical on all
  hosts. 5.7 % of ZL16 requests of +43+005 are placeholders (s. 2).
- Detection order: (1) the header, authoritative; (2) the body hash against the registry's
  known placeholder hashes; (3) the byte count + PNG content type as a last signal, logged as
  "placeholder by size". Ortho4XP used (3) only (`O4_Imagery_Utils.py:1019-1030`).
- A placeholder is stored as a tombstone (`PLACEHOLDER` status in the chunk container), never
  re-fetched during the same build; it feeds the parent fallback, it is not an error.

### 5.3 Parent-404 memo (imagery)

- Bing has no ZL16 imagery over open sea and returns the placeholder for entire textures
  (44/300 of the widened area, s. 1). Ortho4XP tries the parent tile, then its parent, up to 6
  levels, per tile (`O4_Imagery_Utils.py:1272-1289`).
- Rule: when a tile is a placeholder at ZL z, the request for its parent at z-1 is issued once
  and memoised for the build; the three other children read the memo. A placeholder parent
  covers all 4^k descendants. Every texture reports how many of its 256 tiles came from which
  level.

### 5.4 Push-back beyond 429 (imagery / provider health)

- A 403 on a provider that answered 200 seconds ago is a ban signal: window to 8, 30 s pause,
  then 3 probe requests; three consecutive 403 on probes = provider marked blocked for the
  build (`NET_FORBIDDEN`), textures already downloaded are kept.
- The UI shows the push-back state per provider and the process logs each transition with
  counts, so that a user report contains the numbers.

### 5.5 Overpass (vectors)

- Mirrors are declared **by machine, not by name** (s. 4): `overpass-api.de` round-robins over
  the machines behind `lz4.overpass-api.de` and `z.overpass-api.de` with one per-IP quota of 2
  for the three names; `overpass.kumi.systems` is a CNAME of `overpass.private.coffee` (six
  weeks stale, 30 s gateway timeouts); `overpass.osm.jp` is dead. Decided order:
  `lz4.overpass-api.de`, `overpass.openstreetmap.fr`, `maps.mail.ru` (last resort).
- At most 2 requests in flight per machine and on the whole DE cluster; queries are
  `[out:json][timeout:120]` with `(._;>>;); out body qt;` (45.7 MB for +43+005 against 78 MB
  of XML in Ortho4XP), sent with a real `User-Agent`, cached compressed on receipt.
- Health check `GET /api/status`, 5 s timeout; no answer or 5xx: skip the machine 10 minutes.
  A 403 on `/api/status` alone does not disqualify (`.fr`); a 429 puts the machine **and** the
  other machine of its cluster aside for 10 minutes (s. 4).
- Connect timeout 5 s; a 200 with a `remark` (timeout, memory) is retried once on the next
  healthy machine. 3 attempts across machines, then `OSM_UNAVAILABLE`: another cluster's machine
  is asked at once, another machine of the cluster that just failed after 5 s. Never the 2^n
  back-off of Ortho4XP (up to 5 min 40 per query).
- **One clock for when to ask again** (0.1.15): the breakers, and nothing else. Each carries what
  its server said (a 429's `Retry-After`, the slot its `/api/status` page names, the cooldown of a
  machine that is down), `MirrorBoard.soonest` gives the first moment any of them is ready, and a
  round waits exactly that, floored at `attempt_delay_s`. The caller's `deadline` is the budget: a
  wait that does not fit ends the layer at once, saying so, and a caller with no deadline gets one
  round. A schedule of our own beside the breakers is what made the two fight, the round waking
  before the servers were ready, finding nobody to ask, and giving up in a minute -- the very bug
  the patience had been raised to fix (found in review, 2026-09-24).
- **Patience for a spent quota** (0.1.15): these servers count queries per internet address and
  free a slot on their own clock, in minutes. Three rounds twenty seconds apart gave one minute,
  and a user watched all five mirrors answer 429/403/504 and the build give up while the quota
  needed longer (2026-09-24). The rounds are now five and wait for the breakers as above, inside a tile deadline that went from 5 to 15 minutes; raising the rounds under a five-minute deadline would have changed nothing, which is how the minute came about. The rounds still stop early when nothing that refused could pass. And the `/api/status` page says exactly when the next slot frees (`4 slots available now.` or `Slot available after: ..., in 140 seconds.`); `slot_wait_s` reads it, and a 429 **during a layer** asks that machine's page, one GET at the moment we are about to wait anyway, and holds the cluster until the slot it names. It was read only by Checks when first written, which is nowhere a build goes.
- Each layer goes to the least busy cluster: two healthy clusters take four layers of a tile at
  once, two each. The breakers are the process's (`MirrorBoard`): a machine one tile found dead
  is not asked by the next tile, nor by the next build, until its cooldown ends
  (`osm-source.md` 4 and 8, 2026-09-14).

### 5.6 Transient failures after the fetcher's attempts (textures, review 2026-09-13)

What R4 leaves to the texture layer, specified in `pipeline-textures.md` section 4.1 and tested
by `tests/test_pipeline_second_pass.py`:

- A chunk whose request ended with a failure that says "try later" is asked again before its
  texture is declared incomplete: `NET_TIMEOUT`, `NET_CONNECTION_FAILED`, `NET_RATE_LIMITED`
  (429 beyond the pushback budget), and status 502, 503, 504, 408, 425 or **403**, whatever the
  code carrying it. 403 was final until 0.1.14, and it is how a server that blocks a caller it
  finds too eager usually says so: a user's own EOX source served two textures, then answered
  4 000 chunks in eight seconds with no bytes, and not one was asked again (2026-09-24). A 403
  that is a plain refusal now costs the bounded rounds of one pass and says the same thing, with
  how many rounds it took. Not a 500 (the server's own answer for that URL, already asked
  `max_attempts` times), not a 401 or a 451 (no waiting changes them), not a body that is not an
  image. A 404 and a placeholder are answers, never errors: they go to
  the parent fallback (5.3), which is not applied to a transient failure.
- Rounds after pauses of 5, 15 and 45 s, once every chunk of the tile has its first-pass
  answer and the parent rounds are over. Each round starts with a probe of two tiles the
  provider already served (none answered: the provider or the line is down, the retries stop),
  runs on the same `Fetcher` with `max_attempts = 2` and **(second review)**
  `limit_in_flight = min(max(8, pending // 4), max_in_flight // 2)`: at least the AIMD floor,
  about four waves per round whatever the number of chunks waiting, at most half the provider's
  ceiling (64 for Bing), and never more than the group's AIMD window. It asks another
  `{switch:}` hostname than the previous pass, and is abandoned when 8 requests failed without a
  single answer. 429 and `Retry-After` are obeyed as in R2 and cost no attempt.
- Cost: nothing when nothing failed; about 5 s when a chunk is recovered in the first round.
  **(second review)** At most `second_pass_max_s` = 180 s per run of the textures (one provider
  and zoom level), pauses, probes and rounds included, whatever the number of chunks waiting: a
  pause that would end past it is not taken, a probe or round still running is cancelled, the
  chunks left stay `ERROR` and the log says so (`second_pass_capped`). The textures node holds
  the build's single network slot meanwhile, and this limit is what bounds the wait of the other
  tiles; the earlier "about 2 min 10 s" (three rounds of 21 s) only held for up to 8 stuck
  chunks. When the provider is down: the first pause and a probe of two requests (under a
  second when connections are refused, 21 s when packets are dropped).

## 6. Wanted differences from Ortho4XP

| Ortho4XP | OrthoStudio XP | Why |
|---|---|---|
| 16 threads per texture, joined, new `Session` each | one session for the build, 64-128 streams over 4 HTTP/2 connections | 867-1 436 req/s vs 0.105 s per request per thread (s. 2, s. 5) |
| status tested by `'[200]' in str(r)` | status code, headers and body returned as data; the caller checks | silent white textures |
| 8 retries with 2^n sleeps, up to 4 min per tile | AIMD + hedge + bounded attempts with 0.5-4 s back-off | minutes lost on one request |
| fixed 16 in flight, whatever the answers | window climbs to the ceiling, halves on 429/503/latency, pauses on `Retry-After` | polite under push-back, fast otherwise |
| a straggler blocks its texture | hedge after `hedge_after_s`, loser cancelled | 1 in 10 000 hangs 30 s (s. 3) |
| no retry by default (`check_tms_response = False`, `O4_Imagery_Utils.py:1063, 1073-1078`): a failed request leaves a white 256² square in the saved texture (`:1286`, `:1575-1582`) | in-run attempts (R4), then the spaced rounds of the textures (5.6); a chunk still missing leaves the texture incomplete and the tile uncommitted, and the next run fetches that chunk only | a white square is never questioned again; a transient failure costs time, not a wrong texture (review 2026-09-13) |
| no cancellation (Tk stays frozen) | cooperative `Event`, < 1 s | user can stop a build |
| Overpass: 4 hard-coded servers, 3 dead | registry by machine with health check, cluster-aware quota (5.5) | 429 and connect hangs from the same cluster (s. 4) |
