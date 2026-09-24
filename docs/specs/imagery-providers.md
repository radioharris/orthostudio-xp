# Imagery providers: registry, URL grammar, placeholders

Status: P1, written before `src/orthostudio/imagery/providers.py` and the embedded registry
`src/orthostudio/imagery/registry.toml` (tests `tests/test_imagery_providers.py`). Origin: Ortho4XP
`src/O4_Imagery_Utils.py:92-490` (`.lay` / `.ext` readers), `:1003-1090` (`http_request_to_image`,
placeholder rule), `:1150-1230` (`get_wmts_image`, URL grammar), `Providers/**/*.lay` (definitions).
Audit of the 71 definitions: `docs/providers-audit.md` (its tool, which read Ortho4XP's provider
files, and the `.lay` reader were removed by decision 0010). Decision: **keep the grammar, replace the
storage** (TOML registry instead of `.lay` files parsed with `eval`), **restrict the initial set**
to the providers the user chose, and **add** what the Ortho4XP definitions lack (concurrency
ceiling, placeholder signature, attribution, terms).

## 1. The rule in plain language

A provider is a web-mercator tile server: a URL template into which the tile indices are
substituted, optional request headers, a maximum zoom level, a rule to recognise "no imagery here"
answers, and an optional coverage extent. OrthoStudio XP ships a registry of providers that were
alive in September 2026 without a key or token; the user may load another TOML file.

## 2. The URL grammar (`O4_Imagery_Utils.py:1160-1196`, kept)

Placeholders substituted in order, plain `str.replace`:

| Placeholder | Value | Note |
|---|---|---|
| `{zoom}` | `zl` | |
| `{x}` | `x` | |
| `{y}` | `y` | |
| `{\|y\|}` | `abs(y) - 1` | kept verbatim from Ortho4XP (used by no registry provider) |
| `{-y}` | `2 ** zl - 1 - y` | TMS row numbering |
| `{quadkey}` | `quadkey(x, y, zl)` | Bing |
| `{switch:a,b,c}` | one of `a`, `b`, `c` (stripped) | Ortho4XP draws with `random.choice` (`:1192-1196`); OrthoStudio XP is **deterministic**: `servers[(x + y) % n]`, or the caller's `switch=` index (the fetcher's hedge keeps the URL, hence the host and, over HTTP/2, the connection, `net-download.md` R3; the rounds of the textures' second pass ask `switch = x + y + round`, another host per round, `pipeline-textures.md` 4.1). Wanted difference: reproducible URLs for the store keys. |
| `{zoom:02d}` | `zl` zero-padded to 2 digits | **addition**: PDOK's WMTS matrix identifiers are `00`..`19` |

`{xcenter}`, `{ycenter}`, `{size}` (custom grids) are not part of the P1 grammar: every
registry provider is web-mercator. A template that still contains `{...}` after substitution
raises `ValueError` at registry load time, not at request time.

WMTS definitions (`request_type=wmts`, NL and PDOK*) are expressed as a `url_template` built once
from the Ortho4XP concatenation (`:1197-1215`):
`<url_prefix>&SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=<layers>&STYLE=&FORMAT=image/<image_type>&TILEMATRIXSET=<tilematrixset>&TILEMATRIX={zoom:02d}&TILEROW={y}&TILECOL={x}`.
This is exact because the `EPSG:3857` TileMatrixSet of `Providers/Netherland/capabilities.xml` is
the standard web-mercator pyramid (top-left `-20037508.342789244 20037508.342789244`,
ScaleDenominator `559082264.0287176 / 2^k`, 256 px, identifiers `00`..`19`): Ortho4XP would compute
the same tile indices through its custom-grid path (`:1409-1424`) up to floating point noise on
exact tile edges, and resample nothing (`downscale = 0`).

## 3. The `.lay` reader (removed)

The registry was converted once from Ortho4XP's `.lay` files, with a reader that kept the comment
rules of `initialize_providers_dict` (`O4_Imagery_Utils.py:219-231`) and parsed `fake_headers` with
`ast.literal_eval`, never `eval`. The runtime never read `.lay` files; decision 0010 removed the
reader with the rest of the tooling that read Ortho4XP's files.

## 4. The registry (`registry.toml`, `load_registry`)

```toml
schema = 1
[providers.BI]
grid_type = "webmercator"
url_template = "https://ecn.t{switch:0,1,2,3}.tiles.virtualearth.net/tiles/a{quadkey}.jpeg?g=15312"
max_zl = 19
max_in_flight = 128
placeholder = { header_name = "X-VE-Tile-Info", header_value = "no-tile", size = 1033, blake3 = "1abc2dcd..." }
attribution = "..."
terms_url = "..."
```

`Provider` and `PlaceholderRule` are frozen pydantic models (`extra="forbid"`: a typo in a
TOML key is an error at load time). Fields: `code`, `grid_type` (only `webmercator`),
`url_template`, `max_zl`, `tile_size` (256), `headers` (extra request headers; the fetcher's
own defaults apply otherwise), `max_in_flight`, `server_req_per_s`, `placeholder`, `extent` (name),
`extent_bounds` (**addition**: `(lon_min, lat_min, lon_max, lat_max)` copied from the
`mask_bounds` of the Ortho4XP `.ext` file so that the coverage test needs no extent files),
`attribution`, `terms_url`, and three **additions** of 2026-09-14: `name` (a short name for the
page's lists, "Bing Maps"; `attribution` stays the credit line), `same_as` (another code of the very
same imagery: `NL` is `PDOK`, still accepted, listed once) and `custom` (a source the user added,
section 4.1). Codes match `^[A-Za-z0-9_@-]{1,32}$` (the Ortho4XP codes contain `@`).
`load_registry()` keeps the file's order: Bing Maps, then Esri, first in every list (a user asked
for them first).

**Coverage** (a user found the sources of several countries mixed in one list, 2026-09-14):
`Provider.covers(lat, lon)` is true when the 1-degree tile meets `extent_bounds`, always without
them. The bounds are a rectangle around the country, so a tile near a border may still lack
imagery; the check stops the plain mistake, a Dutch source for a tile in Egypt, whose server has
no image there. `make_specs` refuses a tile the chosen source does not cover
(`CFG_PROVIDER_OUT_OF_COVERAGE`, naming the tiles: the estimate and the build), and a zone whose
own source does not meet its polygon is `ZONE_INVALID`.

### 4.1 The sources a user adds (`$OSXP_HOME/sources.toml`)

A user asked for Apple Maps and Google Maps, much used in the USA (2026-09-14). Apple publishes no
tiles to download (its imagery only goes through MapKit, with a key of an Apple developer account),
and Google's terms forbid downloading and storing its tiles outside its APIs (`GO2` was left out of
the registry for that reason, `docs/providers-audit.md`). OrthoStudio XP ships neither; a user may
add a source of their own instead, at their own risk: its terms apply to them.

`sources.toml` has the registry's schema. `load_registry()` without a path adds its sources after
the shipped ones, each `custom`, with `max_in_flight` 16 unless the entry says otherwise
(`USER_SOURCE_IN_FLIGHT`: an unknown host is asked politely). An entry that is not a valid provider,
or that takes a shipped code, is left out and logged (`read_user_sources` returns the problems), so
that a broken file never stops every build. The file is read again only when its modification time
or size changed. The page writes it through `POST /api/sources` and `DELETE /api/sources/{code}`
(`api.md` 2): a code of the name's letters and digits (`new_source_code`, at most 24, `_2`, `_3`...
when taken), the address checked first (http or https, and `{x}` `{y}` `{zoom}` or `{quadkey}`).
Tests never read the machine's own file: `tests/conftest.py` points it elsewhere unless a test sets
`$OSXP_HOME`.

### Initial content (decided by the user; 12 providers, and EOX since 0.1.14)

| Code | Ortho4XP file | Template (OrthoStudio XP) | max_zl | in flight | Placeholder | Extent |
|---|---|---|---|---|---|---|
| BI | Global/BI.lay | `https://ecn.t{switch:0,1,2,3}.tiles.virtualearth.net/tiles/a{quadkey}.jpeg?g=15312` (Ortho4XP: `http://r{switch:0,1,2,3}.ortho.tiles.virtualearth.net/tiles/a{quadkey}.jpeg?g=136`, HTTP/1.1 clear; same bytes, see `docs/benchmarks/network.md` s. 1) | 19 | 128 (measured ceiling) | header `X-VE-Tile-Info: no-tile`, 1 033 bytes, blake3 `1abc2dcd…b484` | global |
| Arc | Global/Arc.lay | `https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{y}/{x}` (Ortho4XP: `http://`, the server redirects to https) | 19 | 128 (measured, `docs/benchmarks/network.md` 7; was 16, an HTTP/1.1 guess) | 2 521 bytes (Ortho4XP rule `:1029-1032`, `arcgisonline` in the URL) | global |
| Arc@ | Global/Arc@.lay | unchanged (`https://clarity.maptiles.arcgis.com/...`) | 19 | 192 (HTTP/2, measured: `docs/benchmarks/network.md` 6 and 7) | none: the Ortho4XP rule tests `arcgisonline` in the URL, which this host does not contain; **open point**, to be measured | global |
| EOX | Global/EOX.lay, EOX2.lay (`a.s2maps-tiles.eu`, refused: 403 in the audit) | `https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{zoom}/{y}/{x}.jpg`, EOX's Sentinel-2 mosaic of 2024 ("EOxCloudless"), added for a pilot flying IFR long haul (2026-09-22); non-commercial use (CC BY-NC-SA 4.0), credit shown | 14 (10 m to the pixel) | 32 (measured: 8, 16, 32, 64 gave 76, 138, 224, 240 req/s) | none; over open sea a plain dark blue 256x256 PNG, which decodes like the JPEGs | global |
| Lux | Luxembourg/Lux.lay | `https://{switch:wmts1,wmts2}.geoportail.lu/opendata/wmts/ortho_latest/GLOBAL_WEBMERCATOR_4_V3/{zoom}/{x}/{y}.jpeg` (Ortho4XP: `http://`) | 20 | 16 (measured: slower at 32) | none | Luxembourg (bounds of `Extents/LowRes/Luxembourg.ext`) |
| NL, PDOK | Netherland/NL.lay, PDOK.lay (identical definitions, layer `Actueel_ortho25`) | WMTS concatenation, section 2 | 19 (matrices `00`..`19`) | 32 (measured: slower at 64) | none | Netherlands `3.06,50.72,7.26,53.76` |
| PDOK18 / PDOK19 / PDOK20 | Netherland/PDOK1{8,9}.lay, PDOK20.lay | same, layers `2018_ortho25`, `2019_ortho25`, `2020_ortho25` | 19 | 32 (the same host) | none | Netherlands |
| SP | Spain/SP.lay | over HTTPS (`https://www.ign.es/wmts/pnoa-ma/...GoogleMapsCompatible&TILEMATRIX={zoom}&TILEROW={y}&TILECOL={x}`): the .lay's `http://` answers the same image over `https://` (2026-09-22) | 19 | 128 (measured; the .lay said `max_threads=32`) | none | Spain (`Extents/LowRes/Spain.ext`) |
| JP | Japan/JP.lay | `https://cyberjapandata.gsi.go.jp/xyz/ort/{zoom}/{x}/{y}.jpg` (Ortho4XP: `http://`) | 18 | 64 (measured; the .lay said `max_threads=8`) | none | Japan `122.9,20.4,154.0,45.6` (Yonaguni to Minamitorishima, Okinotorishima to Hokkaido) |
| USGS | United_States/USGS.lay | unchanged | 19 | 128 (HTTP/2, measured) | none | United States `-180.0,17.5,-64.0,71.5` (Puerto Rico and Hawaii to Alaska) |

The `extent` of Lux, SP, JP and USGS is an **addition** (their `.lay` files declare none, i.e.
global); the bounds of Lux and SP come from `Extents/LowRes/Luxembourg.ext` and `Spain.ext`, those
of JP and USGS from the countries' extreme points.

`max_zl` is **not** in any of these Ortho4XP files (Ortho4XP then has no limit and lets the server
answer whatever it answers). The values above are the finest levels these services are
documented to serve (Bing 19 outside cities, Esri 19, PDOK 19 from the capabilities, GSI
`ort` 18); they cap what the UI offers and where the parent fallback starts, and each is
marked `# assumed` in the TOML until a network probe (`osxp doctor --providers`) confirms it.
`max_in_flight` is what each server took on 2026-09-15, raised step by step until it refused,
slowed down or gave no more (`docs/benchmarks/network.md` section 7; a user asked to go to the
most each provider allows): Bing 128 (the line's limit), Esri `Arc` 128 (it was 16, an HTTP/1.1
guess: 4.5 times faster), Esri Clarity `Arc@` 192 (two 502 at 256), USGS 128, Spain 128, Japan 64,
the Netherlands 32 (slower at 64), Luxembourg 16 (slower at 32, with timeouts). The services of a
state were not tried past 128. The fetcher's AIMD (R2 of `net-download.md`) still lowers the window
of a server that slows down. `server_req_per_s` is the rate a server gave there when it, not the
line, was the limit: Esri Clarity 522, Spain 584, USGS 280, the Netherlands 181, Luxembourg 130,
Japan 103 (none for Bing and Esri `Arc`, which kept up with the line). **EOX is 90, and not a measurement of ours**: we read 224 from here on 2026-09-22 with no error, and a user's builds kept failing on it from an address where the server stops answering around 90 (2026-09-24), which is the figure it now carries. A throughput one machine obtained is not a ceiling a server tolerates from everyone, so since 0.1.15 the fetcher **approaches** whatever is declared rather than holding it: a quarter to begin with, climbing while the server answers, falling when it does not (`net-download.md` R2b). The others have not been checked against a second address. Since 0.1.14 it is
also the **ceiling the fetcher starts requests at** (`net-download.md` R8, `Fetcher(req_per_s=)`),
for the build and for the probe: a server that counts requests rather than connections blocks a
caller it finds too eager, and `max_in_flight` alone does not slow one down on a fast line. The
shipped numbers are those servers' own ceilings, so the cap binds only where the server already
was the limit; a source of a user's own may name a much lower rate. The time left of a build
and the Plan's estimate never count faster downloads (`api.md` 5.6, `estimate.ProbeResult`):
counted from its requests in flight alone, Esri Clarity was expected three times faster than a
user's builds downloaded, and Japan five times faster than its measured rate (2026-09-15). `headers` is empty for
all twelve: none declares `fake_headers`; the fetcher sends its own User-Agent.
`attribution` / `terms_url` are indicative strings for the UI, taken from the audit's
`POLICY_NOTES` and the services' public pages; they are not legal review.

## 5. Placeholder detection (`is_placeholder(provider, headers, body)`)

Ortho4XP (`:1019-1032`): a response whose `Content-Length` is `1033` on a `virtualearth` URL, or
`2521` on an `arcgisonline` URL, is treated as a 404 (parent fallback). OrthoStudio XP keeps both
numbers and adds, per `net-download.md` R5, the authoritative header for Bing and the blake3
of the known body. `is_placeholder` returns the signal that fired (`"header"`, `"blake3"`,
`"size"`) or `None`; the fetcher logs `"size"` matches so that a change on the provider side
is visible. Content-Length is not trusted: the size rule uses `len(body)`.

## 6. Acceptance tests

| # | Test | Where |
|---|---|---|
| P2 | the registry's BI URL for the Marseille tile of the audit is `https://ecn.t0.tiles.virtualearth.net/tiles/a120222133031221.jpeg?g=15312` (switch 0) and rotates over the four hosts | `test_imagery_providers.py` |
| P3 | registry loads, every code is unique and equals its table key, every template consumes all its placeholders, the WMTS templates give `TILEMATRIX=09` at ZL 9 | `test_imagery_providers.py` |
| P4 | placeholder rule: header, hash, size signals, in that order, on a synthetic 1 033-byte body | `test_imagery_providers.py` |

## 7. Wanted differences from Ortho4XP

| Ortho4XP | OrthoStudio XP | Why |
|---|---|---|
| `.lay` text, `eval()` on `fake_headers` and `in_GUI` | TOML, pydantic, `ast.literal_eval` only in the import tool | code execution from a data file |
| 71 definitions, 27 alive | 12 alive without key, chosen by the user | dead entries produce white textures silently (`IMG_TILE_MISSING`) |
| `random.choice` on `{switch:}` | deterministic index | reproducible URLs; the retry rounds of the textures rotate the index (`pipeline-textures.md` 4.1) |
| HTTP/1.1 clear Bing host | HTTPS HTTP/2 `ecn.t{0-3}` | 4.7x throughput, same bytes (`network.md`) |
| placeholder by `Content-Length` only | header, body hash, then size | provider change visible |
| no concurrency ceiling per provider (16 threads per texture) | `max_in_flight` per provider | politeness and measured ceilings |
| `in_GUI`, `imagery_dir`, `color_filters` | dropped | the UI lists the registry; the store layout is OrthoStudio XP's; filters are a later stage |
