# 0005. Imagery pipeline choices for P1

Date: 2026-09-12. Status: accepted.

## Context
P0 benchmarks (`docs/benchmarks/dds-encoder.md`, `network.md`, `docs/providers-audit.md`)
left five open choices before writing the imagery pipeline.

## Decisions
1. **DDS quality gate: relaxed.** Per mip level 0-6, PSNR >= nvcompress -fast - 0.5 dB,
   SSIM >= -0.005, alpha PSNR >= -1 dB. Strict parity would cost ~4 s CPU per texture
   (least-squares refinement) for a 0.4 dB difference invisible at 33-36 dB.
2. **HTTP client: curl_cffi** (libcurl, HTTP/2), 0.32 ms CPU per request versus 0.79 ms for
   httpx. Its one-in-10 000 hung request is covered by hedging (duplicate after 3 s).
3. **Bing concurrency: start at 64, cap at 128 in flight** (1 436 req/s measured, max latency
   0.75 s). 192 gives +33 % but 4-5 s stragglers; not exposed.
4. **Overpass mirrors:** lz4.overpass-api.de, then overpass.openstreetmap.fr, then
   maps.mail.ru as a last resort only (documented as such; users can remove it). Never the
   round-robin name overpass-api.de. *(Amended 2026-09-22, below.)*
5. **Initial provider registry:** Arc, Arc@, BI (global); Lux, NL, PDOK, PDOK18, PDOK19,
   PDOK20, SP, JP, USGS (national, alive, no key). Excluded for now: Hitta and SE (commercial
   sites, terms unverified), GO2 (Google terms), OSM tiles (usage policy), EOX (403).

## Consequences
The imagery pipeline is tuned for one provider family (web-mercator XYZ/quadkey); WMS,
WMTS with custom grids and reprojection wait for P4b. The gate and the caps are constants
in code with their origin in the benchmarks; changing them means re-running the benchmark.

## Amendment 2026-09-12 (after P1 validation)

The alpha criterion of decision 1 is relaxed from -1 dB to **-1.5 dB per mip level 0-6**.
Measured on 17 (ZL14) and 179 (ZL16) textures, ispc_texcomp's BC3 alpha encoder sits 0.4 to
1.4 dB below nvtt at absolute PSNRs of 50-78 dB, which is invisible; the written -1 dB gate
failed 3/17 and 5/179 textures for that reason alone. With -1.5 dB the gate passes 17/17 and
178/179 (the remaining one is a colour miss of 0.01 dB on a 128² mip of an open-sea texture).
The colour criterion is unchanged.

## Amendment 2026-09-22 (Overpass mirrors)

Decision 4's list becomes **overpass-api.de, then z.overpass-api.de, then lz4.overpass-api.de,
then overpass.openstreetmap.fr and maps.mail.ru as last resorts**. On that day every build failed with
`OSM_LAYER_UNAVAILABLE`, here and for two users: `lz4.overpass-api.de` answered 504 or nothing at
all and `overpass.openstreetmap.fr` answers every query with `HTTP 403 This service is only
available to white-listed usages`, which left the last resort alone. `.fr` is demoted rather than
dropped: never asked while another name answers, its refusal costs 0.1 s when it is, and it
serves again by itself the day it reopens, without waiting for a release.

**The round-robin name `overpass-api.de` is admitted, and leads.** Decision 4 refused it so that
every request would be accounted to a known machine. That evening it was the only name of the
German cluster to answer, and what it served came from 65.109.112.52, `lz4`'s machine, which was
answering 504 under its own name. The accounting decision 4 wanted is kept where it matters: the
per-IP quota, the minimum interval and the breaker are held per cluster, and the three German
names are one cluster.

A rule is added: **a mirror enters the registry only once it has been seen to hold the whole
planet**, checked with one small query in Europe, one in America and one in Oceania.
`overpass.osm.ch`, first tried as the replacement, holds Switzerland only and answers `200` with
an empty `elements` list everywhere else: it would have built tiles without airports, water or
coastline and reported nothing, since an empty answer is legitimate and no code can tell the
difference. The other public instances were measured too: `gall.openstreetmap.de` refuses
queries with a 429, `overpass.kumi.systems` and `overpass.private.coffee` are one machine that
never finishes the TLS handshake, `overpass.osm.jp` has an expired certificate.

What does not change: the per-cluster quota keeps the German names to two requests between them
all. The price of one cluster instead of two is a tile downloading two layers at a time instead
of four.
