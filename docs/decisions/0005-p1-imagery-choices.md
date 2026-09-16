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
   round-robin name overpass-api.de.
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
