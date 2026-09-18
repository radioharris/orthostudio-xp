# Map, zones and regions (P5, first part)

Status: written by the architect before the code, 2026-09-13. Three work packages run in
parallel on **disjoint files** (section 8); an integration pass wires them, a review follows.
Origin in Ortho4XP: the Tk map of `O4_GUI_Utils.py` (tile picking, `zone_list` drawing:
Ctrl+click = one texture square, Shift+click = free polygon points, Ctrl+Shift+click = points
snapped to the texture grid) and `O4_DSF_Utils.zone_list_to_ortho_dico` (already ported:
`orthostudio.dsf.zones.texture_map`). Decision: **new front end, same zone semantics**.

## 1. Vocabulary (the user's, keep it in every visible string)

- **Tuile / tile**: the 1°×1° square, one X-Plane DSF, chosen on the map.
- **Zone**: a polygon drawn on the map that asks for another zoom level, and optionally another
  provider, inside the tiles it covers.
- **Région / region**: a zone that covers more than one tile. It is not a separate object: the
  engine clips every zone to every tile it touches (the user asked for multi-tile regions as
  first-class objects, instead of copying one polygon into each tile's `zone_list`).
- **Texture**: the internal 4096 px unit, 16×16 web-mercator tiles at its zoom level.

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| M1 | Leaflet 1.9.4, vendored in `src/orthostudio/ui/vendor/leaflet/` (integrity-checked, BSD-2), loaded as a classic script before `app.js`; no plugin | the user chose it; no CDN, no build step (`ui.md` 1) |
| M2 | The base map is served by the engine: `GET /api/map/{provider}/{z}/{x}/{y}`, with a disk cache | the page never contacts another origin; the user sees the imagery they will get |
| M3 | One zones document, `$OSXP_HOME/zones.json`, format `osxp-zones-1`; `GET`/`PUT /api/zones` | zones outlive a build and a page reload; one file is inspectable |
| M4 | **Priority = order**: index 0 wins where zones overlap | it is Ortho4XP's `zone_list` order (`_zone_image` paints the list reversed, the first entry last) |
| M5 | A zone is clipped per tile at build time into Ortho4XP's `zone_list`; the result enters `DsfParams` | the DSF, `.ter` and texture keys already follow `zone_list`; a tile no zone touches keeps its key |
| M6 | Holes of a clipped polygon are dropped (exterior rings only) | Ortho4XP fills exterior rings (`ImageDraw.polygon`); documented, not silent (warning count) |
| M7 | Cmd+click on macOS stands for Ctrl+click | Ctrl+click is the context menu on macOS |

## 3. The zones document (`osxp-zones-1`)

```json
{
 "format": "osxp-zones-1",
 "zones": [
  {"id": "lsgg-18", "name": "LSGG", "zl": 18, "provider": null, "photo_look": null,
   "polygon": [[6.090, 46.225], [6.130, 46.225], [6.130, 46.250], [6.090, 46.250]]}
 ]
}
```

- `polygon`: `[lon, lat]` pairs (GeoJSON order), 3 to 2000 vertices, ring **not** closed (a
  last vertex equal to the first is dropped on load), longitudes in [-180, 180], latitudes in
  [-85, 85] (web-mercator), a valid simple polygon (`shapely` `is_valid`; else `ZONE_INVALID`
  with the reason `explain_validity` gives).
- `zl`: integer 12 to 20. When `provider` is set, at most that provider's `max_zl`; when it is
  `null` the tile's provider applies and the check happens when the build is planned.
- `provider`: `null` (the tile's provider) or a registry code.
- `photo`: `{look, brightness, contrast, saturation}`, the colours of the photos inside the zone.
  `look` `null` inherits the tile's, `custom` uses the three numbers, the named looks are
  `config.overrides.PHOTO_LOOKS` (a user asked for colours per zone and per tile, 2026-09-18). `zones.photo_zone_entries` clips
  the zones that name one into the tile's `photo_zones` setting, and the textures stage gives
  each texture the colours of the **first** zone holding its centre
  (`pipeline.build.photo_zone_colours`): a texture is one file, so it cannot carry two looks, and
  the rule is the one the zoom level already follows. A tile with no such zone keeps the artefact
  key it had (`TileTexturesParams.canonical`).

The document also holds **what each tile carries of its own**, which is where a pilot sets it:

```json
"tiles": {"+46+006": {"photo": {"look": "softer", "brightness": 0, "contrast": 0, "saturation": 0}}}
```

Three levels, each inheriting the one above until it names its own: **Settings**, then the
**tile** (`zones.with_tile_photo`, read by `api.specs.request_tiles`), then the **zone**
(`with_photo_zones`). A build takes them as they are when it starts, so two builds of the same
tile can carry different colours; only the textures are re-encoded, nothing is downloaded again.
- `id`: 1 to 64 characters `[A-Za-z0-9_-]`, unique in the document; `name`: at most 80
  characters (may be empty).
- At most 500 zones in a document.

The **revision** of the file is the SHA-256 of its bytes in hex, `""` when there is no file
(`orthostudio.zones.zones_revision`).

`GET /api/zones` answers `200` whenever the file is absent or can be read from disk, with the
header `ETag: "<revision>"` (and `Cache-Control: no-store`):

```json
{"format": "osxp-zones-1", "revision": "<sha256 hex of the file, or \"\">",
 "zones": [ ... ],
 "problems": [{"zone": "<id or null>", "index": 0, "code": "ZONE_INVALID",
               "reason": "...", "message": "..."}]}
```

The saved document is read zone by zone and **never refused whole**
(`orthostudio.zones.read_saved_zones`): one bad zone, written by hand or by another version, locks
neither the page nor the builds of other tiles (review of 2026-09-13). Each zone has at most one
problem:

- a valid zone is listed normalised;
- a zone that fails validation (unknown provider, invalid polygon, `zl` above the provider's
  `max_zl`, an id already used by a zone listed before it, a zone after the 500th:
  `ZONE_TOO_MANY`...) is listed **as stored** when the page can show it -- a string `id`, an
  integer `zl`, a `polygon` list of `[lon, lat]` number pairs, a `provider` and a `name` that
  are strings, null or absent -- with a problem giving its `zone` id and its `index` (position
  in the file's `zones`);
- an entry the page cannot show (no id or polygon, wrong types, not an object) is left out of
  `zones` and reported with `"zone": null` and its `index`; except a zone that passed validation
  itself (a repeated id, past the 500th, stored as `"zl": "18"` which validation accepts): it is
  listed normalised;
- keys other than `format` and `zones`: one problem with `"zone": null, "index": null`, the zones
  are listed all the same;
- a file that is not JSON (UTF-8; `NaN`, `Infinity` and numbers beyond a double refused), or not
  an `osxp-zones-1` object (not an object, another `format`, `zones` not a list, or keys but
  neither `format` nor `zones`, as in a GeoJSON file): `"zones": []` and one problem with
  `"zone": null, "index": null` whose `reason` names the file;
- a file that cannot be read at all (permissions, a folder in its place): `422 ZONE_INVALID`
  naming the path.

`message` is the rendered message of the error (what `error_json` gives), `reason` its short
cause. `GET` never writes the file.

`PUT /api/zones` validates the whole document, writes it atomically (the previous file is kept
as `zones.json.bak`) and answers `200` with the document normalised (closing vertex dropped,
coordinates rounded to 9 decimals) plus `"revision"` (of the new file) and `"problems": []`,
with the `ETag` header. Any invalid zone refuses the whole document (`422`, `ZONE_INVALID`,
context `zone` = its id, `reason`); the document is validated before any revision is compared.
The optional header `If-Match` carries the revision the page loaded, with or without quotes:
when it is not the current file's revision the answer is **`409 ZONE_CONFLICT`** (context
`path`) and nothing is written, so two windows never overwrite each other's zones (the page
reloads them). Without `If-Match` (the command line, a script) nothing is compared.

## 4. From zones to a tile's `zone_list` (`src/orthostudio/zones.py`)

```python
def load_zones(path: Path) -> ZonesDocument
def save_zones(path: Path, doc: ZonesDocument) -> None
def zones_for_tile(zones: Sequence[Zone], tile: TileRef, provider: str) -> list[ZoneEntry]
def tiles_touched(zone: Zone) -> list[TileRef]
def zone_textures(zone: Zone, tile: TileRef) -> int   # estimate helper
```

`zones_for_tile` clips each zone, in document order, to `box(lon, lat, lon + 1, lat + 1)`. Each
resulting polygon part with an area above 1e-10 deg² becomes one entry
`([lat0, lon0, lat1, lon1, ..., lat0, lon0], zl, provider)` -- the Ortho4XP format of
`orthostudio.dsf.params.Zone`: latitude first, ring closed, 9 decimals -- with `provider` = the
zone's or the tile's. The parts of one zone stay adjacent, so the priority of M4 survives the
clipping. More than 254 entries for one tile is `ZONE_TOO_MANY` (the priority image is 8-bit,
`orthostudio.dsf.zones._MAX_ZONES`). A zone above the provider's `max_zl` is `ZONE_INVALID`.

## 5. Builds and estimates

`PlanRequest` and `JobRequest` gain `zones: list[ZoneModel] | None = None`:

- `None` (no `zones` in the request): the saved document, read zone by zone as `GET` reads it
  (section 3). Only its valid zones touching at least one requested tile are used. A zone with a
  problem that touches a requested tile refuses the request, naming it (`422 ZONE_INVALID`, or
  `ZONE_TOO_MANY` past the 500th; context `zone`, `index`, `path`); problems on zones touching
  none of the requested tiles are ignored. A file no zone can be read from (not JSON, not an
  `osxp-zones-1` object, unreadable) refuses every request (`422 ZONE_INVALID` naming the path).
  Where a zone with a problem lies: its polygon, clipped like a valid zone's, when its vertices
  make a valid polygon; else the bounding box of the vertices that can be read (a
  self-intersecting outline, fewer than 3 vertices); a zone without one usable vertex touches no
  tile.
- a list (possibly empty): exactly those zones, validated whole; the saved file is not read.
  The page sends the zones of its list whose bounding box reaches a requested tile, in list
  order (10.2): a zone elsewhere changes nothing in that build.

For every tile of the request, `zones_for_tile(...)` goes into that tile's
`BuildSpec.config["zone_list"]` **only when it is not empty**, so a tile no zone touches is
built exactly as before (same keys). The CLI gains `osxp build --zones FILE`, where `FILE` is an
`osxp-zones-1` document or a GeoJSON `FeatureCollection` of `Polygon` / `MultiPolygon` features
whose `properties` carry `zl` and optionally `provider`, `name`, `id`.

The estimate (`orthostudio.estimate`, `POST /api/plan`) counts the textures of the zones on top of
the tile's own: for each entry, the textures at the zone's zoom level whose square intersects the
clipped polygon. It stays an upper bound, and says so.

## 6. The base map (`GET /api/map/{provider}/{z}/{x}/{y}`)

- `provider` must be a registry code (else `404`, `CFG_PROVIDER_UNKNOWN`); `z` from 1 to
  `min(19, max_zl)`; `x`, `y` in `[0, 2^z)` (else `422`, `CFG_VALUE_INVALID`).
- Served from `<data folder>/mapcache/<provider>/<z>/<x>/<y>` when present (`$OSXP_HOME` unless
  Settings chose another data folder); otherwise fetched once through `orthostudio.net.fetch` with
  `orthostudio.imagery.providers.tile_url`, written atomically, and served. While the data folder
  is missing (its disk unplugged), tiles are fetched and served without the cache, which is neither
  read nor written (`map_api.mapcache_root`). A placeholder answer (`is_placeholder`) or a `404` from the provider is remembered as
  `<y>.none` and answered `204 No Content` (the page draws nothing there).
- Upstream failure: `502` with the `NET_*` error of the fetch; nothing is cached.
- At most 8 concurrent upstream requests per provider, and never above its `max_in_flight`;
  one long-lived client for the process, not one per request.
- `Content-Type` is the provider's; `Cache-Control: max-age=86400`.
- `osxp clean --images` empties `mapcache` as well as `chunks`.

## 7. The page

### 7.0 The user knows what they are doing (requirement, 2026-09-13)

The user, who flies X-Plane and has used Ortho4XP, finds Ortho4XP too technical to understand.
The page must make every step and its consequence obvious to someone who has never heard of
zoom levels, textures or DSF files. This overrides any detail below that contradicts it.

1. **A guided flow of four visible steps** on the Plan screen, each with one sentence saying
   what it does: 1. Choose the tiles (on the map). 2. Add more detail where you want it
   (optional). 3. Check what it will cost (disk, download, time). 4. Build and install.
2. **No hidden gesture is ever required.** Drawing starts from visible buttons ("Add detail:
   rectangle", "Add detail: free shape"), and a banner over the map says what to do next
   ("Click two opposite corners", "Click to add points, double-click to finish, Esc to
   cancel"). The Ortho4XP gestures of section 1 stay as **shortcuts** for experienced users,
   listed in a small "Shortcuts" help, never needed.
3. **Plain words first.** The main labels never show ZL, texture, DSF, mesh or mask. A zoom
   level is a named detail level with its meaning at the zone's latitude, for example
   "Standard, about 2 m per pixel" (16), "Sharp, about 1 m" (17), "Very sharp, about 50 cm"
   (18), "Maximum, about 25 cm" (19), computed as `156543.03 * cos(lat) / 2^zl`. "ZL18" appears
   only as secondary text for users who know it. The provider is "Imagery source".
4. **Consequences before action.** Each zone shows its approximate extra size ("about
   1.2 GB"), computed in the page from the textures it covers (about 11 MB each); the selection
   shows its total; step 3 gives the engine's numbers and warns in red when the disk is short.
5. **State on the map, with a legend**: installed tiles, selected tiles, the tiles of the running
   build (being built, waiting, failed), zones by detail level.
6. **First visit**: a hint on the map ("Zoom to the area you want, then click squares to choose
   tiles").
7. **Language**: English by default whatever the browser's language; French stays available in
   the language switch (the user's decision).

### 7.1 Elements

Plan screen, above the existing inputs (which stay):

1. **Map** (`L.map`), base layer `api/map/<provider>/{z}/{x}/{y}` for the provider selected in
   the Plan form, `maxNativeZoom = min(19, max_zl)`, attribution = the provider's text (no link,
   Leaflet's prefix disabled). Initial view: the selected tiles, else the installed ones, else
   Europe.
2. **Tile grid** at map zoom ≥ 5, drawn for the viewport only: click toggles the tile in the
   selection (the existing chips list stays in sync); installed tiles (`/api/library`) have a
   green outline, which follows each install of a running build; the tiles of the running build
   pulse while worked on, dashed while waiting, and so are the tiles of the builds waiting in the
   queue (`ui.md` 2.1); a click on a tile in a build does not choose it; labels (`+46+006`) at
   zoom ≥ 7.
3. **Zone tools** (section 7.0 comes first: visible buttons and a banner drive drawing; the
   gestures below are shortcuts), active at map zoom ≥ 9: a *detail level* select for the next
   zone (default 18, shown in plain words) and an *imagery source* select (default "same as
   the tile").
   - Ctrl+click (Cmd+click on macOS): a square zone, the texture at that zoom level containing
     the point; saved at once.
   - Shift+click: next vertex of a free polygon; Enter or double-click closes it (3 vertices at
     least); Esc cancels.
   - Ctrl+Shift+click (Cmd+Shift+click): next vertex snapped to the nearest corner of the
     texture grid at that zoom level.
4. **Zone list** beside the map: name (editable), zoom level, provider, "région · N tuiles"
   when it covers several tiles, move up/down (priority, M4), delete; selecting a zone
   highlights it on the map; Delete removes the selected zone. Zones are coloured by zoom
   level (legend). A trash above the list deletes every zone after asking (`ui.md`, Step 2).
5. Every change is saved with `PUT /api/zones` (debounced 500 ms); an error is a toast.
6. The estimate and *Build* send `tiles` (the selection) and `zones` (the list shown). A zone
   that touches no selected tile gets a hint ("outside the selected tiles").
7. Mock mode (`?mock=1`): `mock/zones.json`; the base layer is a canvas grid layer (no request).

Every visible string goes through `t()` in French and English; controls keep keyboard access.

### 7.2 Country borders (user request, 2026-09-13)

The user asked for the borders on the map, and for a less visible tile grid (its lines are now
white at 28 % opacity: installed and selected tiles carry the colour).

- **Data**: Natural Earth 1:10m *Admin 0 - Boundary Lines* (land) 5.1.2, public domain, turned by
  `tools/borders/build_borders.py` into `src/orthostudio/ui/vendor/borders/borders.json` (512 KB,
  format `osxp-borders-1`; source, checksums and processing in the README beside it). Nothing is
  fetched from another origin: the page reads that static file the first time the borders are shown.
- **Drawing** (`map.js`): one Leaflet polyline per class in the pane `osxpBorders` (z-index 340,
  under the grid): warm dashes for the international boundaries, fainter short dashes for the
  disputed, indefinite and line-of-control ones. The attribution says "Borders: Natural Earth"
  while they are on the map.
- **Zoom**: shown up to map zoom 10 (`BORDERS_MAX_ZOOM`). The data is accurate to a few hundred
  metres, which would show against the imagery closer in (checked on the Rhine at Basel: on the
  river at zoom 10). The legend then reads "Country borders (zoom out to see them)".
- **Switch**: a checkbox in the legend, on by default, remembered in `localStorage`
  (`orthostudio.mapBorders`); the legend keeps the keyboard focus on it when it is rebuilt. A file
  that cannot be read (OrthoStudio XP being restarted, for instance) leaves the rest of the map
  working and the legend reading "Country borders unavailable for now (trying again by itself)": it
  is read again after 5 s, 15 s, 1 min, then every 5 min (`bordersRetryDelay`) while the borders are
  wanted and the map is zoomed out, and at once when the box is ticked again or the browser is back
  online (user request, 2026-09-13: a failure used to last until the page was reloaded).
- `geo.js` `decodeBorders` reads the format; `tests/test_ui_static.py` checks it under node against
  an independent reader, and that the vendored file still holds the France-Switzerland border at
  Geneva.

## 8. Work packages (disjoint files)

| Package | Owns (creates or edits) | Must not touch |
|---|---|---|
| **A. Zones engine** | `src/orthostudio/zones.py`, `src/orthostudio/api/zones_api.py` (an `APIRouter`), `src/orthostudio/api/models.py`, `src/orthostudio/api/specs.py`, `src/orthostudio/estimate.py`, `src/orthostudio/cli.py` (`--zones`), `src/orthostudio/errors.py` + `docs/specs/errors.md` (`ZONE_INVALID`, `ZONE_TOO_MANY`), `tests/test_zones*.py`, `tests/test_api_zones.py` | `src/orthostudio/ui/`, `src/orthostudio/api/app.py`, `src/orthostudio/clean.py` |
| **B. Map proxy** | `src/orthostudio/api/map_api.py` (an `APIRouter` + its client and cache), `src/orthostudio/clean.py` (mapcache), `tests/test_api_map.py`, `tests/test_clean.py` (additions only) | `src/orthostudio/ui/`, `src/orthostudio/api/app.py`, the files of A |
| **C. Page** | `src/orthostudio/ui/**` (except `vendor/`), `tests/test_ui_static.py`, `docs/specs/ui.md` | everything under `src/orthostudio/` outside `ui/` |
| **Integration** | `src/orthostudio/api/app.py` (include both routers), `docs/specs/api.md`, conflicts, full suite, a real run in a browser | |

Each package runs `ruff format` and `ruff check` **on its own files only**,
`mypy src/orthostudio`, and its tests; a question of architecture is written down and escalated,
not improvised.

## 9. Acceptance

- A zone over four tiles compiles into four `zone_list`s whose union is the zone, priority kept;
  a MultiPolygon clip gives adjacent entries; a tile without zones keeps its DSF key.
- `PUT` then `GET /api/zones` round-trips; an invalid polygon refuses the document.
- A job with a square ZL18 zone over LSGG has a `zone_list` on `+46+006` only, and its estimate
  counts more textures than without it.
- The map proxy serves a tile from a stub fetcher, then from the cache without fetching;
  a placeholder is `204` and remembered; an unknown provider is `404`.
- In a browser (integration): the map shows imagery, a click selects a tile, Cmd+click creates
  a square zone that survives a reload, the estimate shows the extra textures.

## 10. Integration decisions (2026-09-13)

- **Upstream client of the map** (package B). `orthostudio.net.fetch.Fetcher` serves one batch at a
  time and a cancelled batch leaves a transfer running, so the proxy keeps one long-lived
  client object holding, per provider, a small pool of reusable fetchers (at most 8, one per
  request in flight; a cancelled one is closed, never reused). Accepted: it is what "one
  long-lived client" meant, without batching unrelated tiles behind the slowest one.
- **Zones above `mesh_zl` are refused** (package A, `ZONE_INVALID` with the remedy to raise
  `mesh_zl`): the DSF gives each mesh cell one texture, so a ZL20 zone on the default mesh
  (ZL19) would be stretched without a word. Accepted; the page offers detail levels up to 19.
- **`ZONE` error domain** added to the registry (`ZONE_INVALID`, `ZONE_TOO_MANY`), 422 in the
  API like `CFG_`.
- **Request body limit** raised from 256 kB to 4 MB (`MAX_BODY_BYTES`): hundreds of detailed
  polygons fit; the 500 × 2000 vertex bound of section 3 is not reachable through the API, and
  that is fine.
- **The CLI does not read the saved document**: `osxp build` uses zones only with `--zones FILE`.
  The page, through the API, uses the saved document when a request carries no `zones`.
- **`create_app(map_fetch=...)`** injects the map's upstream fetch, so the application's tests
  never reach a provider.
- **Abandoned map requests** (package B, review of 2026-09-13). Leaflet asks for the tiles of
  every level a zoom passes through and aborts most of them; the proxy fetched them all, and the
  tiles on screen queued behind them (stub at 0.5 s: 48 abandoned requests, next tile in 3.5 s).
  The proxy now counts the requests waiting for each tile. A request whose client goes away stops
  waiting: under uvicorn that is the `http.disconnect` of a closed connection, awaited on
  `receive()` in a task of its own, since a polled `Request.is_disconnected()` never sees it
  behind the `BaseHTTPMiddleware` of `create_app`; under `httpx.ASGITransport` the request is
  cancelled. A tile left without requests gives its slot back unfetched when its turn comes, and
  a free slot goes to the most recent request (LIFO; a tile asked for again moves up), still at
  most 8 per provider and never above `max_in_flight`. A fetch already under way when its last
  request leaves finishes and is cached as usual. Accepted: with the same stub, the next tile
  takes 0.5 s when the abandoned requests were queued, 0.9 s when 8 of them were already fetching.

### Review of 2026-09-13 (engine side)

- **A saved zones document is never refused whole** (sections 3 and 5). A `zones.json` holding
  one invalid zone (`"provider": "GO2"`) made `GET /api/zones` answer `422`, the page then
  stopped editing and left `zones` out of its requests, and `/api/plan` and `/api/jobs` refused
  every tile, even far from that zone. The saved document is now read zone by zone
  (`read_saved_zones`); `load_zones` stays the strict reading (the first problem raises), and
  `PUT` still refuses a whole invalid document. A zone that passed validation itself but repeats
  an id or comes after the 500th, and whose stored form the page cannot show (validation is lax:
  `"18"` is a zoom level), is listed normalised rather than left out.
- **Revisions** (section 3): `ETag` on both answers, `If-Match` on `PUT`, `409 ZONE_CONFLICT`,
  a new registry code (blocking, stop; context `path`; remedy: reload the zones). The revision
  is compared and the file written under the router's lock.
- **Cross-site requests are refused.** `<img src="http://127.0.0.1:8641/api/map/BI/19/...">` on
  any website made OrthoStudio XP fetch map tiles: the guard only checked `Host`. The middleware of
  `create_app` now refuses with `403 SYS_FORBIDDEN_ORIGIN` (an API-only code, like
  `SYS_FORBIDDEN_HOST`, which gets the same full error shape) any request under `/api/` whose
  `Sec-Fetch-Site` header is present and is neither `same-origin` nor `none`. Requests without
  the header (curl, scripts) and `/`, `/static/` are not affected.
- **`clean()` guesses no map cache**: `mapcache_root` defaults to `None`, which leaves the map
  cache alone; `osxp clean` passes `<data folder>/mapcache` (`graph-keys.md` 9.1). The default used to
  be the `mapcache` beside `chunks_root`, emptied for a caller passing its own chunks folder.

### 10.2 Integration of the review fixes (2026-09-13)

- **The page sends the zones that reach the selected tiles** (`geo.tilesInBounds`, bounding box
  of the vertices, valid polygon or not), not its whole list: a request carrying an explicit
  `zones` list is validated whole, so a red zone near Geneva used to refuse an estimate for Cape
  Town. Checked in a browser against the real engine.
- **The page's files are served with `Cache-Control: no-cache`** (`app.PAGE_CACHE_CONTROL`). A
  browser kept an old `geo.js` beside a new `map.js` after the update and the page did not start
  (an ES module importing a missing export). Revalidation is cheap on a local server; a browser
  that cached the files before this change needs one hard reload.

