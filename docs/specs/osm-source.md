# OSM source: Overpass mirrors and snapshots

Status: P3, written before `src/orthostudio/sources/osm.py` (tests `tests/test_osm_source.py`),
trimmed by decisions 0009 and 0010. Origin:
Ortho4XP `src/O4_OSM_Utils.py:12-19` (mirror list), `:284-390` (`OSM_layer.write_to_file`, the cache
serialisation), `:392-464` (`OSM_queries_to_OSM_layer`, cache recycling at `:423`), `:465-512`
(`OSM_query_to_OSM_layer`, recycling at `:490`), `:513-585` (`get_overpass_data`, the retry loop),
`:50-283` (`OSM_layer.update_dicosm`, the reader), `src/O4_File_Names.py:24-41` and `:265-272`
(`osm_cached`), `src/O4_Vector_Map.py:185-193, 256-306, 391-399, 525-539` (the per-layer queries).
Policy already decided: ADR 0005 decision 4 (mirror order) and `docs/specs/net-download.md` section
5.5 (quota, health check, attempts). Measurements: `docs/benchmarks/network.md` section 4. Decision:
**replace the client and the cache**. OrthoStudio XP downloads JSON from a health-checked mirror
registry and keeps its own snapshot, which its vector stage reads. (Until decision 0009 it also
wrote Ortho4XP's `.osm.bz2` cache, and until decision 0010 it could read one as an equivalent
source.)

## 1. The rule in plain language

For one 1° tile, five vector layers are wanted from Overpass (four at the default
`road_level`). Ortho4XP asks a hard-coded server for XML with `out meta`, retries the *same*
server eight times with waits doubling to 128 s, and writes what it kept to
`OSM_data/<folder>/<tile>/<tile>_<suffix>.osm.bz2`. Three of its four servers are dead or
stale and the fourth is a round-robin name whose second machine stopped answering this IP
(`docs/benchmarks/network.md` section 4), so a cold tile times out today.

OrthoStudio XP asks a *machine* (never a round-robin name) for `[out:json]`, at most two requests in
flight per machine and per cluster, sends each layer to the least busy cluster (two clusters take
the four layers of a tile at once), gives up on a machine quickly and moves to another one at
once, remembers a dead machine for every tile of the process, and keeps one snapshot per layer.

Nothing in this module imports or reads Ortho4XP (decision 0010).

## 2. The mirror registry

Declared **by name**, in the order of ADR 0005 decision 4 as amended on 2026-09-22.

| Code | Interpreter | Cluster | Last resort | Note |
|---|---|---|---|---|
| `de` | `https://overpass-api.de/api/interpreter` | `de` | no | the public entry point of the German cluster; the only one of its three names that answered on the evening of 2026-09-22 |
| `z` | `https://z.overpass-api.de/api/interpreter` | `de` | no | one machine of the German cluster |
| `lz4` | `https://lz4.overpass-api.de/api/interpreter` | `de` | no | 65.109.112.52 ("lambert"), the same machine `overpass-api.de` served that evening while this name answered 504 |
| `fr` | `https://overpass.openstreetmap.fr/api/interpreter` | `fr` | **yes** | refuses every query since 2026-09-22 (`HTTP 403`, white-listed usages only); kept as a last resort, where its refusal costs 0.1 s, so that it serves again by itself the day it reopens |
| `mailru` | `https://maps.mail.ru/osm/tools/overpass/api/interpreter` | `mailru` | **yes** | third-party clone, used only when the others are unusable; a user may remove it from the registry |

**Why this list changed (2026-09-22).** Every build failed with `OSM_LAYER_UNAVAILABLE`, here
and for two users on other continents. Measured that day: `lz4.overpass-api.de` answered `504`
or nothing at all and `overpass.openstreetmap.fr` answered every query with `HTTP 403 This
service is only available to white-listed usages`, which left the last resort alone, itself
answering 504. `.fr` stays, but as a last resort: it is never asked while another name answers,
its refusal costs 0.1 s when it is, and the day it serves the public again it does so without
waiting for a release.

**The round-robin name leads the list**, against decision 4, which wrote it off. That evening it
was the only name of the German cluster to answer, and the query it served came back from
65.109.112.52 -- `lz4`'s own machine, which was returning 504 under its own name. What decision 4
feared, a name that hides which machine was asked, costs nothing: the quota, the minimum interval
and the breaker are held per **cluster**, and the three German names are one cluster and one
per-IP quota. What it buys is three chances inside that cluster instead of one. Measured right
after the change, on a bad evening: tile `+43+005`, four layers in 46 s, two served by `de` and
two by the last resort, after `z` and `lz4` had refused.

**A mirror enters the registry only once it has been seen to hold the whole planet.** Every
other public instance was measured the same day and none is usable: `overpass.osm.ch` **holds
Switzerland only** and answers `200` with an empty `elements` list everywhere else (tiles
without airports, water or coastline, and nothing to show for it, since an empty answer is a
legitimate one that no code can tell from this); `gall.openstreetmap.de` answers `/api/status`
but refuses queries with a 429; `overpass.kumi.systems` and `overpass.private.coffee` are one
machine (193.219.97.30) that accepts the connection and never finishes the TLS handshake;
`overpass.osm.jp` has an expired certificate. A regional instance may enter the registry the day
one is verified, with a cluster of its own, which would again run four layers at a time. The
check is three small `aeroway` queries, one in
Europe, one in America, one in Oceania: a mirror that answers `0 elements` to any of them is a
regional extract.

`Mirror(code, interpreter, status_url, cluster, last_resort, note)` is frozen. A caller may
pass its own tuple of mirrors to `OverpassClient`; the default is `MIRRORS`.

**Politeness** (`net-download.md` 5.5): `max_in_flight = 2` per cluster (not per code, so the
DE cluster stays at two with both `z` and `lz4` in the registry), a `min_interval_s = 1.0` between two
requests of the same cluster, a real `User-Agent` (`orthostudio/<version> (+OSM vector data for
X-Plane scenery)`), `Accept-Encoding: gzip`, POST `data=<QL>` (no query string), and the
`[out:json][timeout:<n>]` setting that lets the server cut a runaway query itself.

## 3. The queries, layer by layer

Selectors copied verbatim from `O4_Vector_Map.py`; only the output format changes.

| Layer (cache suffix) | Origin | Selectors | `tags_of_interest` of Ortho4XP |
|---|---|---|---|
| `airports` | `:185-193` | `node["aeroway"]`, `way["aeroway"]`, `rel["aeroway"]` | `["all"]` |
| `big_roads` | `:261-275` | `way["highway"="motorway"]`, `way["highway"="trunk"]`, `way["highway"="primary"]`, `way["highway"="secondary"]`, `way["railway"="rail"]`, `way["railway"="narrow_gauge"]` | `["bridge", "tunnel"]` |
| `small_roads` | `:290-306` | `way["highway"="tertiary"]`; `road_level >= 3` adds `way["highway"="unclassified"]`, `way["highway"="residential"]`; `>= 4` adds `way["highway"="service"]`; `>= 5` adds `way["highway"="track"]` | `["bridge", "tunnel"]` |
| `coastline` | `:391-399` | `way["natural"="coastline"]` | `[]` |
| `water` | `:525-539` | `rel["natural"="water"]`, `rel["waterway"="riverbank"]`, `way["natural"="water"]`, `way["waterway"="riverbank"]`, `way["waterway"="dock"]` | `["name"]` |

`small_roads` exists only at `road_level >= 2`; `layers_for(road_level)` returns the layers a
tile needs (four at the Ortho4XP default `road_level = 1`).

**A layer held for more roads answers a build that wants fewer** (`narrowed`, 0.1.15). The road
levels differ in one place only, `small_roads`, and only by `way["highway"=…]` selectors added one
at a time, so a snapshot baked at level 5 holds every road level 3 asks for and two kinds more.
Handing it over whole would flatten the mesh under tracks and service roads the user's settings say
nothing about, so the extra is dropped on reading: a way is kept when its tags match one of the
wanted selectors, a node when a kept way names it or when no way names it at all, and the digest is
taken again. The result is what that level would have been given, proved against real bakes of
`+47+013` from the Austrian extract (same 393 698 nodes, same 34 813 ways, same digest as a level 3
baked from the same data, 2026-09-25). `selector_tag` reads only the `way["k"="v"]` shape, and a
layer carrying any other selector is refused rather than guessed at.

Without this a library baked at one level served only that level and the two below `small_roads`
(0, 1 and its own), so a user who chose *+ streets* in Settings fell back to the live servers
although the bake held every road he wanted.

The bounding box of a tile is `(lat, lon, lat + 1, lon + 1)` — south, west, north, east — as
in `O4_OSM_Utils.py:557-561`.

OrthoStudio XP query (`overpass_query`):

```
[out:json][timeout:120];(<sel1>(43,5,44,6);<sel2>(43,5,44,6););(._;>>;);out body qt;
```

Ortho4XP query (`O4_OSM_Utils.py:557-562`):

```
(<sel1>(43, 5, 44, 6);<sel2>(43, 5, 44, 6););(._;>>;);out meta;
```

Differences, all wanted: **JSON instead of XML** (45.7 MB against 78 MB for the four layers of
`+43+005`, `network.md` section 4), **`out body` instead of `out meta`** (`version`,
`timestamp`, `changeset`, `uid`, `user` are never read: `update_dicosm` ignores them and
`write_to_file` writes `version="1"`), `[timeout:n]` declared to the server. The union,
the recursion `(._;>>;)` and `qt` ordering are identical, so the element set is identical.

## 4. Answer handling and failure policy

A reply is **usable** when the status is 200, the body parses as JSON, and the document has no
`remark` field (Overpass reports a query timeout or a memory cut inside a 200). An empty
`elements` list is a valid answer (a tile with no coastline).

| Situation | Code emitted | Mirror effect | Next |
|---|---|---|---|
| connect error, read timeout, no body | `OSM_MIRROR_UNREACHABLE` | breaker open `cooldown_s` (600 s) | next mirror |
| HTTP 429 | `OSM_MIRROR_REJECTED` | breaker open on the **whole cluster** for `max(cooldown_s, Retry-After)` | next mirror |
| HTTP 5xx (504 above all) | `OSM_MIRROR_REJECTED` | breaker open `cooldown_s` | next mirror |
| other non-200 | `OSM_MIRROR_REJECTED` | breaker open `cooldown_s` | next mirror |
| 200, body not JSON / cut | `OSM_RESPONSE_TRUNCATED` | one failure recorded, no breaker | next mirror |
| 200 with `remark` | `OSM_RESPONSE_ERROR` | one failure recorded, no breaker | next mirror |
| every mirror exhausted | `OsxpError("OSM_LAYER_UNAVAILABLE")` raised | — | — |

**Which mirror.** Among the mirrors not tried yet for the layer and whose breaker is not open, the
last resort only when no other is left, the client takes the one whose cluster has the fewest
requests given and not ended (waiting for a slot or in flight), then the registry's order. Two
healthy clusters run the four layers of a tile two and two instead of two at a time; since
2026-09-22 the three ordinary names are one cluster, so a tile downloads two layers at a time. A
request that waited for its cluster's slot while another layer found the machine dead is not sent:
it picks again, and no attempt is spent (2026-09-14: the layers queued behind a dead `lz4` each
waited for its 5 s connect timeout).

**Breaker.** Three states per mirror: *closed*, *open* until `open_until` (600 s, doubled at
each consecutive opening up to 3 600 s), *half-open* once the cooldown has passed — the next
request is a probe, a success closes the breaker and resets the cooldown, a failure re-opens
it. A last-resort mirror is only chosen when every non-last-resort mirror is open or already
tried for this layer. The states live on a `MirrorBoard`: the build's OSM downloads share the
process's (`shared_board()`), so a mirror one tile found dead is not asked by the next tile of
the same build; a client made without a board keeps one of its own.

**A new build clears them** (`board.reset()`, called by the job runner when a job starts). The
breaker knows that a machine failed a minute ago; it cannot know that the user has since waited,
fixed their connection, or that the machine is back. Kept across builds, it turned one bad night
into an hour of builds failing in four seconds each, with quitting the app as the only way out
(2026-09-22). Within one build the cooldown still holds, which is what it was written for.

**Rounds.** The whole registry is asked up to `rounds = 3` times for one layer, `round_pause_s`
= 20 s before the second and 40 s before the third, the breakers cleared between them. A machine
that answers 504, 429 or nothing is busy, not broken, and answers the same query a minute later;
one pass and then a failed build threw away everything the tile had downloaded, which is how
every build failed on the evening of 2026-09-22. A round is only repeated when something that
refused may pass: `.fr`'s 403 will be the same in a minute, a 504 will not.

**Attempts.** `max_attempts = 5` *across mirrors* within a round, one per entry of the registry,
so the last resorts are still reached when the three ordinary entries are down (2026-09-22: they were, for
two of the four layers of a tile). A mirror of another cluster is
asked at once; `attempt_delay_s = 5` is waited only before another machine of a cluster that
**pushed back** — a 429, a 5xx, or a 200 whose query it could not finish (a `remark`, a
truncated body). A machine that does not answer at all, or refuses with a 403, says nothing
about its cluster, and its sibling is asked at once: on 2026-09-22 `lz4` was dead and `z`, its
sibling, was the only mirror left to ask. Never the 2^n back-off of Ortho4XP (which costs up to 5 min 40 s per query, `errors.md`
OSM_MIRROR_UNREACHABLE).

**Progress.** `fetch_tile(progress=...)` reports each layer received, and every second while
layers are in flight: `+46+006: 2/4 OSM layers (1.4 MB/s)` (`osm_progress_message`), the fraction
being the layers received, the rate the bytes received so far (as sent, gzip included,
`HttpReply.wire_bytes`) over the time since the tile started. The build's OSM node forwards it
to the Works page.

**Health check.** `GET <status_url>` with a 5 s timeout, or a minimal query
`[out:json][timeout:10];node(id:1);out ids;` when the mirror declares no status URL. No
answer or a 5xx puts the mirror aside for `cooldown_s`; a 403 on the status endpoint alone does
not (a mirror may refuse it and still serve queries). The health check is optional: `fetch_layer` works without it and the breaker learns
from the queries themselves.

## 5. The OrthoStudio XP snapshot (format `osxp-osm-snapshot-1`)

One file per (tile, layer), zstd level 10 over an orjson document:

```json
{"format": "osxp-osm-snapshot-1", "tile": "+43+005", "layer": "coastline",
 "selectors": ["way[\"natural\"=\"coastline\"]"], "query": "[out:json]…",
 "mirror": "de", "fetched_at": "2026-09-12T09:41:02Z",
 "generator": "Overpass API 0.7.62.11 87bfad18", "osm_base": "2026-09-11T20:46:21Z",
 "digest": "<blake3-64hex>", "counts": {"nodes": 39790, "ways": 179, "relations": 0},
 "elements": [{"type": "node", "id": …, "lat": …, "lon": …, "tags": {…}}, …]}
```

`elements` keeps the Overpass JSON element shape (`node`/`way`/`relation`, `nodes`,
`members`) so that P4 reads the file with no conversion. Elements are stored **in the order
the mirror sent them** (`qt`, which keeps neighbours close and helps P4's spatial passes);
the digest and the Ortho4XP file sort by id, so neither depends on that order.

**Content key.** `digest = blake3(canonical)` where `canonical` is orjson with sorted keys over
the elements sorted by `(type rank n<w<r, id)`, tags sorted, `lat`/`lon` rendered with
`"{:.7f}"`. It therefore ignores the mirror, the date, the generator and the `qt` order: two
downloads of unchanged data give the same digest. `digest` is the cache key P4 will put in its
`RuleParams`, and it is what `snapshot_label` hashes.

**Path.** `SnapshotStore(root).path_for(tile, layer)` =
`<root>/osm/<folder>/<tile>/<tile>_<layer>.osm.json.zst` (default root: `osxp_home()`, i.e.
`~/.orthostudio`). One canonical path per (tile, layer), the digest inside; a sidecar
`<tile>_<layer>.meta.json` (`meta_path_for`) holds the same document without `elements`, so
`digest_for` costs one small read instead of a decompression. An unreadable or truncated file raises
`OSM_CACHE_UNREADABLE`, a failed write `OSM_CACHE_WRITE_FAILED`; both are written atomically (temp
file + `os.replace`).

**Snapshot label (arbitration A4).** `snapshot_label(snapshots)` returns
`osm-<12 hex>` = blake3 of the sorted `"<layer>:<digest>"` lines: an opaque string that changes
only when the OSM data behind a tile changes, content-based, so a refetch of unchanged data gives
the same label. It names the data in the build report; the graph itself keys the vector stage on
the digest of its `osm` input (`pipeline-build.md` 8.5).

## 6. The order a snapshot is read in

Ortho4XP's reader, `OSM_layer.update_dicosm` (`O4_OSM_Utils.py:50-283`), numbers its internal ids
in reading order (`:99`, `:107`, `:119`), so the reading order decides the content of every
structure it returns and the iteration order of the `dicosmfirst["w"]` set that stage 1 walks. It
read the `.osm.bz2` file `OSM_layer.write_to_file` (`:284-390`) wrote. The vector stage walks a
snapshot in that file's order (`osmdata._feed_snapshot`), so it builds the structures Ortho4XP built
from the same data:

1. **all nodes, then all ways, then all relations, each group sorted by OSM id**. The sort is
   load-bearing, not cosmetic: sorted by id is exactly what Ortho4XP received from Overpass, whose
   query carries no `qt` (`O4_OSM_Utils.py:562`: `out meta;`), and it makes the result independent
   of the mirror that answered;
2. coordinates rounded to 7 decimals, Ortho4XP's `"{:.7f}"` (`:302-303`), so two copies of a node
   land on the same `(lon, lat)` key of the node deduplication (`:92-96`);
3. only `type="way"` members with `role` `outer` or `inner`: the reader logs and drops anything else
   (`:130-141`);
4. a way whose node list is empty, or which references a node absent from the snapshot, is skipped
   (the reader deletes empty ways at `:176-183` and crashes on an unknown `nd`).

The snapshot itself keeps the mirror's `qt` order (section 5); only the walk sorts. Until decision
0010 this section also specified the Ortho4XP file, the tests wrote such files from snapshots, and
Ortho4XP's own parser read them back **id for id** equal on `coastline` (424 ways, 39 545 nodes),
`airports` (1 041 ways, 3 relations) and `water` (4 734 ways, 113 relations).

### 6.1 Cancellation, timeout and the state of the cache

* `OverpassClient.fetch_tile(..., cancel=None, timeout_s=None)` polls the caller's
  (thread-based) event every 0.1 s while the layers are in flight, cancels the in-flight
  requests and raises `SYS_CANCELLED`; `timeout_s` bounds the whole tile (`NET_TIMEOUT`).
  `fetch_tile_sync` forwards both. This is what makes a Ctrl-C during phase 0 stop the
  download instead of waiting for it (`pipeline-build.md` 8.3).
* `--osm-refresh` changes the key of the snapshot, so a tile whose snapshot is stored downloads a
  fresh one.
* the reader maps a corrupt snapshot to `OSM_CACHE_UNREADABLE` with the path and a remedy, instead
  of letting an `OSError` become `SYS_INTERNAL_ERROR`.

## 7. The rule

The rule wrapper (`orthostudio.osm@1`) is **not** in this module: it lives in
`orthostudio.pipeline.native`, which binds the client to the graph (`pipeline-build.md` 8.3). This
module exposes pure functions and a client. P4 made the snapshots the direct input of the vector
stage (no XML, no bz2 on that path), which is why the `.osm.bz2` writer left the build path and,
with decision 0009, the product; decision 0010 removed the reader of that file.

## 8. Measurements

Mirror-by-mirror timings: `docs/benchmarks/network.md` section 4 (the four layers of
`+43+005`, once each, sequentially: 22.3 s on `lz4`, 24.2 s on `.fr`, 20.4 s on
`maps.mail.ru`; timeouts and 429 on the names Ortho4XP uses). This module was measured once, with
one run of the four layers of `+43+005` through `OverpassClient` (M4 Pro, load 3.6, one run,
2026-09-12):

| Layer | Mirror | Wall | Answer (JSON) | OrthoStudio XP snapshot (zstd 10) | Ortho4XP file (bz2 9) | Nodes / ways / rels |
|---|---|---|---|---|---|---|
| airports | lz4 | 2.1 s | 1.18 MB | 0.13 MB | 0.13 MB | 9 550 / 1 041 / 3 |
| big_roads | lz4 | 9.7 s | 27.55 MB | 2.93 MB | 2.71 MB | 163 297 / 25 751 / 0 |
| coastline | **fr** | 24.2 s | 4.41 MB | 0.48 MB | 0.50 MB | 39 545 / 424 / 0 |
| water | fr | 6.3 s | 12.55 MB | 1.56 MB | 1.58 MB | 117 300 / 4 734 / 113 |
| **total** | | **42.3 s** | 45.7 MB | 5.10 MB | 4.92 MB | |

Health check of the three mirrors in parallel: 10.0 s, dominated by `maps.mail.ru`, which did
not answer and was put aside without being used; `lz4` answered its status in 0.21 s, `.fr`
in 0.27 s **with a 403** and was kept (spec section 4).

**2026-09-14, a dead `lz4`.** From the user's Mac, `lz4.overpass-api.de` accepted no connection
(TCP connect never answered), `z.overpass-api.de` answered 504 to a one-node query, and `.fr`
answered it in 0.18 s. Three tiles downloaded one after the other, as a build does, with
`OverpassClient` and a transport recording every request (`fetch_tile`, four layers each):

| Tile | Before | After (least busy cluster, immediate fail-over, shared board) |
|---|---:|---:|
| `+45+005` | 24.2 s: 4 requests to `lz4`, 5 s each, then `.fr` 5 s later | 14.1 s: 2 requests to `lz4`, the other two layers on `.fr` from the start |
| `+45+006` | 20.2 s (the same 4 timeouts) | 9.8 s: `.fr` only |
| `+44+005` | 18.3 s (the same) | 8.4 s |
| **three tiles** | **62.7 s** | **32.3 s** |

52.1, 35.3 and 17.8 MB of JSON, sent without gzip by `.fr`. Before, every tile lost about 10 s
to timeouts and 5 s delays; after, only the first tile of the process meets the dead machine.

The 24.2 s of the coastline is the fail-over doing its job, not a slow mirror: `lz4` answered
**HTTP 429 after 15.3 s** (the DE cluster allows 2 slots per IP and the two previous layers
had used them), the breaker opened the whole `de` cluster, the client waited its 5 s and
`.fr` answered in 3.8 s. Ortho4XP in the same situation retries the *same* server eight times with
waits doubling to 128 s. Writing the Ortho4XP file cost 0.08 to 2.37 s per layer (bz2 level 9,
single-threaded), 3.5 s for the four, when the build still wrote it.

Fidelity of the download against the Ortho4XP cache of the same tile, measured with one query before
decision 0010: coastline `+43+005`, **424 ways and 39 545 nodes on both sides, overlap 1.0000** on
the identifier sets; the `.osm.bz2` OrthoStudio XP wrote then was 0.50 MB against 0.59 MB for the
Ortho4XP cache of the same layer, which carries the `out meta` attributes OrthoStudio XP does not
request.
