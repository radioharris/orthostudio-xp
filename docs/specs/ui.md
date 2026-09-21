# UI: the web page (P2b four screens, P5 map and zones)

Status: P2b, written before the code of `src/orthostudio/ui/`; the map, the zones and the guided
Plan (P5, 2026-09-13) follow `docs/specs/map-zones.md` section 7, whose section 7.0 (the user's
requirement) wins over any detail here. Tests: `tests/test_ui_*.py` (static checks served through
FastAPI `StaticFiles` with `httpx.AsyncClient`, geometry run under `node`; no browser). Origin: the
four-screen journey of the rewrite's plan. The Tk GUI of Ortho4XP (`O4_GUI_Utils.py`) is **not**
ported; its parameter hints survive through `/api/settings/schema`, and its zone gestures survive as
shortcuts.

## 1. Scope and constraints

One directory of static files served by the engine (`create_app(ui_dir=...)`): `index.html`
at `/`, everything else under `/static/`. No build step, no bundler, no package manager, no
CDN, no font download: native ES modules, `fetch`, `EventSource`, system fonts. One library,
Leaflet 1.9.4, is vendored and loaded as a classic script before `app.js` (decision M1 of
`map-zones.md`). The page never contacts anything but its own origin (`/api/...`, the base
map included); the only outbound traffic of OrthoStudio XP stays in the engine (imagery providers,
Overpass).

| File | Role |
|---|---|
| `index.html` | shell: navigation, four `<section>` screens, status bar, `<template>`s |
| `styles.css` | design tokens (light and dark, detail-level colours), layout, components |
| `i18n.js` | the dictionary (`en`, `fr`), `t()`, number/duration/size formatters |
| `app.js` | state, API client (real or mock), rendering functions, SSE handling |
| `map.js` | the Plan's map: base layer (aerial or street), tile grid, airports, zone drawing, zone list, sizes, zones persistence |
| `geo.js` | geometry without DOM (tiles, textures, polygons, the `osxp-zones-1` checks); run by the tests |
| `vendor/leaflet/` | Leaflet 1.9.4 (`leaflet.js`, `leaflet.css`, `LICENSE`, `README.md`), not edited |
| `vendor/maplibre/` | MapLibre GL 5.24.0 and its Leaflet bridge, for the street map's vector tiles; loaded only when that map is asked for |
| `mock/*.json` | one file per API response, used by `?mock=1` and by the tests |
| `__init__.py` | `ui_dir()` and `STATIC_FILES` for `create_app` and the tests; nothing else |

Language: **English by default, whatever the browser's language** (the user's decision,
`map-zones.md` 7.0.7); the switch in the top bar offers French and remembers a choice under
`localStorage["orthostudio.language"]` (the former key `orthostudio.lang` was written from the
browser's language on every visit, so it is not read). `index.html` carries English text. Every
visible string goes through `t(key)`; keys are literal so the test can check that each one exists in
both languages, and dynamic keys go through `tOpt()` or a table of literal calls.

Accessibility and rendering: `prefers-color-scheme` drives the theme (manual override kept in
`localStorage`), `prefers-reduced-motion` disables the progress animations, focus rings are
visible, numbers use `font-variant-numeric: tabular-nums`, controls are native (`<select>`,
`<input>`, `<button>`, `<details>`), the navigation is a `<nav>` of buttons with
`aria-current`. No icon font: the few glyphs are inline SVG in `index.html`.

**Text styles**, as in a word processor's template (a user found eleven sizes of text and titles
standing 0 to 14px above their text, and asked for one style per kind of text, 2026-09-21). Every
text is one of seven styles, defined once at the top of `styles.css` with tokens:

| Style | Look | For |
|---|---|---|
| Title 1 | 18px, bold | the four screens |
| Title 2 | 15px, bold | a card, a column or a dialog: the Plan's steps, Settings' questions, *For experts*, *Disk space*, *Jobs*, a job, a dialog |
| Title 3 | 13.5px, bold | inside a card or a column: a group of expert settings, the presets, the parts of the cost, *Errors*, *Decisions* |
| Text | 13.5px | what the page says |
| Secondary | 12.5px, grey | the sentence under a title, the text under a field, notes |
| Label | 12.5px, bold, grey | a field's name, a column's heading |
| Small | 11.5px | units, tags, counts, the notes of a zone or a job |

And one set of spaces: a title stands 6px above what follows it (`--title-gap`), a label 4px above
its field (`--label-gap`), a field 5px above the text under it (`--help-gap`), a paragraph 8px above
the next (`--gap`); the blocks of a card, and the cards, are 12px apart (`--block-gap`), the parts of
a screen 20px (`--section-gap`); cards are padded 12px 14px (`--card-pad`). Controls (buttons,
fields, pills), the map and the top and status bars keep sizes of their own; a test lists them.
Every button has a frame: the quiet, frameless ones read as text (*My sources…*, then *Show in
Finder*, 2026-09-21). Every list draws its own up and down arrows (`--select-arrow`, one SVG per
theme in its `--fg-2`): Chromium, in Windows' window and in Chrome, drew a chevron almost touching
the right edge where the Mac's window drew its arrows clear of it (a user put the two side by side,
2026-09-21). A field's title is text: a click on a label naming a field outside it is left to the
text (`app.js` `isTitleClick`), so a double-click selects the name to copy instead of putting the
caret in the field or ticking a box (2026-09-21); a label around its own box ("On", a question's
choice) still ticks it. A check box of *For experts* is named by its title (`aria-labelledby`)
without being tied to it: tied, a press on the title showed the box pressed, a flash. Settings' questions are cards of a line each (`inline-block`) in their two
columns: as blocks, WebKit carried the space under the first column's last card to the top of the
second, 12px lower than the first in the Mac's window and not in Windows'.

## 2. The four screens

### 2.1 Plan

The Plan is **four numbered steps beside a map**, each with one sentence saying what it does
(`map-zones.md` 7.0.1). Wide screens: the map on the left (sticky, `100vh - 120px` high,
420 to 820 px), the steps on the right (340 to 440 px); below 960 px the map comes first and
the steps follow. Main labels use plain words: never ZL, texture, DSF, mesh or mask (7.0.3);
"ZL18" only appears after a detail level's name.

**Detail levels** (`map.js` `detailLabel`): a name and the ground size of a pixel at a latitude,
`156543.03392 × cos(lat) / 2^zl`, rounded for reading (`fmtGround`: whole metres from 0.95 m,
else centimetres by 10 from 50 cm and by 5 below), then the zoom level as secondary text:
12 Very coarse, 13 Coarse, 14 Low, 15 Medium, 16 Standard, 17 Sharp, 18 Very sharp, 19 Maximum,
20 Ultra, e.g. "Very sharp, about 40 cm per pixel · ZL18" at 46° N.

#### The map (`map.js`)

- **Base layer**: `L.tileLayer("api/map/<provider>/{z}/{x}/{y}")` for the imagery source of
  step 1 (changing it swaps the layer), `maxNativeZoom = min(19, max_zl)`, map zoom 3 to 20,
  `noWrap`, bounds ±85.06°. Attribution: the provider's `attribution` text, HTML-escaped
  (Leaflet writes it with `innerHTML`), Leaflet's prefix disabled, so no link is rendered.
  When six tiles fail and none loads (a `502`, or `204` everywhere), a notice over the map says
  that no imagery came from that source for this view. Mock mode: a canvas `L.GridLayer`
  painting a neutral grid with the theme's tokens, no request.
- **Initial view**: the selected tiles, else the installed ones (`GET /api/library`, loaded at
  boot), else Europe; `fitBounds` with at most zoom 9, without animation. The map is created the
  first time the Plan is shown (Leaflet needs a visible container) and `invalidateSize()` runs on
  each return. The page shows its screen before the engine answers (2.5, *The first screen*): a
  map drawn before the library came opened on Europe, and takes the view of the installed tiles
  when the library comes, unless it was moved meanwhile (`map.js` `libraryChanged`, `firstView`).
- **Tile grid** (SVG, pane under the zones): 1° lines for the viewport only from zoom 5; selected
  tiles outlined in the accent colour (blue) and installed tiles in green at every zoom; a tile both
  installed and chosen keeps its green outline and shows its blue one 4px inside it, both 2.5px
  (`is-both`), each over a 5px line of `--map-casing` (`osxp-tile-casing`: dark on the dark theme,
  light on the light one), which leaves a line between and around them against the photo (`map.js`
  `insetBox`). Drawn on the same line, the green hid the blue; a thin blue beside the green hardly
  showed (a user, 2026-09-22, chose this among four drawings on the photo). The green alone when the
  tile is too small on the screen to hold both, under 16px (zoom 4 and out; zoom 5, where tiles are
  chosen, gives 22px): zoomed out on the world, the map shows which tiles are installed (the same
  user). Tile outlines are drawn on whole pixels (`shape-rendering: crispEdges`): Leaflet leaves the
  vector layer between two pixels after some moves, and blended over two pixels the parting line
  faded at some zooms and not at others (the same user); on whole pixels WebKit widens each line to
  the pixel, which the 2.5px gives back, so a Retina screen shows 3px of green, 1px of casing and
  3px of blue at every zoom. Labels (`+46+006`) from zoom 7, in the north-west corner of the visible
  part of each tile when it can hold them, at most 400 cells.
- **The running build on the map** (user request, 2026-09-14: the selection empties when a build
  starts, and nothing then showed which tiles were being built): over the rest, a tile of the
  build one of whose steps runs pulses in the accent colour, one that waits for its turn (or
  between two steps) is dashed, one that failed is dashed red (`app.js` `buildingTiles`; still
  plain, without the pulse, for a user who reduces motion). A finished tile leaves that layer. The
  map follows the job the page watches (`watchJob`, also started at boot when `GET /api/status`
  names an active job), drawn again only when a tile's state changes. The tiles of the other
  builds of the queue are dashed, as waiting (`buildingOnMap`). Off Works the page watches the
  build under way: when the one it watched ends, it follows the next (`followBuildUnderWay`). The Library is read again a
  moment after each tile's install and when the build ends, so installed tiles turn green one after
  the other.
- **Legend over the map** (7.0.5): installed, selected, the tiles of the running build (being
  built, waiting, failed: only the states on the map), and the detail levels in use by the
  zones (plus the level being drawn), in their colours: Ortho4XP's (15 cyan, 16 green, 17 yellow,
  18 orange, 19 red) extended to 12-14 and 20, shared by both themes because they sit on imagery.
- **First-visit hint** (7.0.6): "Zoom to the area you want, then click squares to choose
  tiles", with *Got it*; gone for good (`localStorage["orthostudio.mapHintDone"]`) once dismissed or
  once a tile is selected.
- Leaflet options: `boxZoom` off (Shift+drag would zoom instead of adding a point),
  `doubleClickZoom` off (a double-click finishes a free shape); controls restyled with the
  page's tokens; the map container is a stacking context (`z-index: 0`) so Leaflet's panes stay
  under the page's toasts and popovers.

**Clicks on the map**, in order:

1. While drawing (below), a plain click adds a rectangle corner or a shape point.
2. From zoom 9, a click inside a zone selects the first zone of the list containing the point
   (the one on top); a click outside every zone while one is selected only deselects it.
   Below zoom 9 zones are not picked, so the tiles under a region stay clickable.
3. From zoom 5, a click toggles the tile under the point in the selection (`toggleTile` in
   `app.js`: the chips and the map show one selection, `state.tiles`); below zoom 5 a toast
   asks to zoom in a little. A tile in a build, under way or waiting, is not chosen (user
   request, 2026-09-14: it would be built a second time once that build ended): a toast says
   "+46+006 is already in a build, under way or waiting: you can choose it again once that build
   ends." (`tilesInBuilds` over the jobs the page knows).

#### Step 1 · Choose the tiles

The chips of the selection (removable), "N tile(s) selected", and the approximate size of the
selection before any estimate (7.0.4): "About 3.2 GB of imagery, including 705 MB of extra
detail". On the line of the count, shown when tiles are selected, a small trash, *Unselect all
tiles* (user request, 2026-09-14), empties the selection at once. Unlike the trash of the zones
and of the jobs it does not ask: nothing on the disk changes (tiles already built stay installed)
and the selection is not saved anyway. The estimate goes with it, the focus moves to step 1 and a
toast says "Tiles unselected: N." Under the chips, a chosen tile that has hand-made patches says
so, with its files: "-20-044 will be built with your patches: SBCF.patch.osm." (`GET
/api/patches`, read when the page opens, when the Plan shows and after Settings are saved; a user
took "Patches: none" in a report for his patch not being found, when it was for another square,
2026-09-21). Then:

- **Imagery source** (`<select>` from `GET /api/providers`), grouped since a user found the
  sources of several countries mixed in one list, and asked for Bing and Esri first (2026-09-14;
  `sources.js` `sourceGroups`): *Whole world* (Bing Maps, Esri World Imagery, Esri World Imagery
  Clarity, in the engine's order), *For your tiles* (a country's source whose rectangle meets
  every tile chosen), *My sources* (those the user added), then *Other countries* (*By country*
  when no tile is chosen), by country name, each labelled with its country ("Netherlands · PDOK
  2020"). `NL`, the same imagery as `PDOK`, is listed only while it is the one selected; a source
  greyed when `alive === false`. The groups follow the tiles as they change; the source selected
  stays. Under the field, in red, the tiles the source does not cover ("Netherlands · PDOK 2020
  does not cover +46+006: it is one country's source (Netherlands). Choose a Whole world source
  for these tiles."), before the estimate refuses them; then the attribution.
- **My sources…** (a small button under the field; user request, 2026-09-14: Apple Maps and
  Google Maps, much used in the USA, which OrthoStudio XP does not ship, `imagery-providers.md`
  4.1): a dialog that says in a yellow box that the user adds the source themselves, that its
  terms of use apply to them, and that OrthoStudio XP does not ship it nor check that they may
  download its images. It lists the sources added (name, code, address, *Remove*) and has a form:
  *Name*, *Address of a tile* (`{x}`, `{y}` and `{zoom}`, or `{quadkey}`, in place of its
  numbers), *Highest detail level* (19). The address is checked in the page first (http or https,
  the tile's place). *Try* asks for one tile where the page is looking (the middle of the first
  tile chosen, else of the map): "It works: a JPEG image of 23 KB arrived.", or "No image arrived
  (answer 403): check the address." *Add* saves it and selects it in step 1; *Remove* says when
  zones use it (`SYS_SOURCE_IN_USE`). Every list of sources follows at once (step 1, the zones',
  Settings).
- **Detail level**: 12-19 clipped to the source's `max_zl`, labelled as above at the latitude
  of the first tile (centre of the cell, `lat + 0.5`), else the map centre's, else 45°; the
  help under it names that latitude.
- *Other ways: airport, tile names, coordinates* (`<details>`), the P2b inputs, unchanged and
  cumulative into the same selection:
  - ICAO code with completion from `GET /api/airports?q=` (debounced 200 ms, 10 results,
    custom listbox for keyboard use) and a radius in km (default 15) → every 1° cell
    intersecting the bounding box of the circle (`dlat = r / 111.2`,
    `dlon = r / (111.2 cos lat)`);
  - a free text of tile names `+43+005 +43+006` (regex `[+-]\d\d[+-]\d\d\d`, separators
    space, comma, newline);
  - latitude / longitude → tile `floor(lat)`, `floor(lon)` formatted `%+03d%+04d`.

  The tiles of a build under way or waiting are left out of what these add, and step 3's message
  line says so: "Already in a build, under way or waiting, so left out: +46+006."

The request to `/api/plan` and `/api/jobs` always carries the explicit `tiles: [...]` list
(never `{icao, radius_km}`), so what the user sees is what is built.

**Sizes in the page** (`geo.js`, the engine's counting rules; 11.184952 MB per texture, a
4096² BC1 DDS with mipmaps, `orthostudio.estimate.BC1_BYTES`): a tile is `textures_covering` its
corners at the step's level; a zone adds, per tile it covers, the textures of its level whose
square overlaps the zone clipped to the tile by more than 1e-10 deg² (bounding box beyond
2·10⁶ clip operations, an upper bound like the engine's). The selection's extra detail counts
a texture shared by several zones once per tile; a zone at the tiles' own level and source
adds nothing. These are approximations; step 3 gives the engine's numbers.

#### Step 2 · Add more detail where you want it (optional)

- **Detail level of new zones** (default 18, sized at the map centre, levels above the chosen
  source's maximum disabled) and **Imagery source of new zones** ("Same as the tiles" = `null`,
  or a registry code). Choosing a source that cannot reach the level lowers the level and says
  so in a toast.
- **Add detail: rectangle** and **Add detail: free shape** (7.0.2): no gesture is needed. A
  banner over the map says what to do next and holds the buttons *Point at the map centre*
  (keyboard use, with a crosshair at the centre), *Remove the last point*, *Finish* (free shape,
  3 points at least) and *Cancel*:
  - below map zoom 9: "Zoom in closer to draw (the + button or the mouse wheel)"; clicks then
    only flash the banner;
  - rectangle: "Click two opposite corners of the rectangle. Esc to cancel", then "Now click
    the opposite corner"; a dashed rectangle follows the pointer; two corners apart make a
    4-vertex zone (south-west, south-east, north-east, north-west);
  - free shape: "Click to add points, double-click to finish, Esc to cancel (n point(s))";
    a dashed line joins the last point, the pointer and the first point. Finishing refuses
    fewer than 3 points and a ring that crosses or touches itself (`polygonIsSimple`), so the
    engine's validity check (`shapely`) is not the first to see it. At most 2000 points.
- **Shortcuts** (the Ortho4XP gestures, `map-zones.md` 1 and M7, listed in a closed
  `<details>`; never needed), active from map zoom 9:
  - Ctrl+click (⌘+click on macOS; macOS turns Ctrl+click into a `contextmenu`, handled too, and a
    click and a context menu at the same place within 500 ms act once): a zone the size of the
    texture of the chosen level containing the point (`textureSquare`: the texture of
    `orthostudio.imagery.grid.texture_at`, its corners by `tile_to_wgs84`); the same zone twice
    selects the existing one;
  - Shift+click: a point of a free shape (starts one, with its banner);
  - Ctrl+Shift+click (⌘+Shift+click): a point snapped to the nearest corner of the texture
    grid of that level (`snapToTextureCorner`, Ortho4XP's `newPointGrid`);
  - Enter or double-click finishes the shape, Esc cancels, Backspace removes the last point;
    Delete (⌫ on macOS) removes the selected zone. Keys are ignored while a text field or a
    select has the focus, and Delete/Backspace only act with the focus on the map, the zone
    list or nowhere.
- **New zones**: id `z<time base 36><3 random>` (unique), name "Zone N", the level and source
  of the selects, inserted **before the first zone of the same or a lower level**, so a finer
  zone drawn inside a coarser one wins, as Ortho4XP's `save_zone_list` sorted `zone_list` by level;
  the user reorders afterwards. The new zone is selected.
- **Zone list** (`<ol>`, priority order, index 0 wins where zones overlap, M4, said in one sentence
  when there are two zones or more): per zone a colour bar, the **name** (text, 80 characters), ↑ /
  ↓ (priority), × (delete), the **detail level** (labels sized at the zone's latitude), the
  **imagery source**, then notes: the zone's approximate size ("about 2.04 GB", or "nothing extra"
  at the tiles' level and source), "region · N tiles" when it covers several tiles (positive-area
  rule of `map-zones.md` 4), "outside the selected tiles" -- followed by **Add N tile(s)**, which
  chooses the tiles the zone falls in, since a zone builds nothing on its own and a build makes
  whole tiles (a user asked what happens to a zone drawn without tiles, 2026-09-18); never done on
  its own, a region can hold many tiles and each one is a download --, "sharper than <source> provides" when a
  zone without its own source is above the tiles' source's maximum, and "OrthoStudio XP cannot use
  this zone: <reason>" (row framed in red, zone dashed on the map) for a problem of the saved file
  naming it or a `ZONE_*` refusal naming it (below). A detail level the page does not offer (20, or
  anything a hand-edited file holds) is shown as it is, never as the first option. Focus in a row
  (or a click on it) selects the zone, highlights it on the map and pans to it when it is out of
  view; a zone picked on the map scrolls the list (never the page). The list keeps the focus and the
  caret of the control being edited across re-renders.
- **Delete all zones** (user request, 2026-09-13): a small trash above the list, on the line of
  the priority sentence, shown when the list has zones and loaded. It asks first ("Delete all the
  zones?", the number of zones, "Tiles already built do not change: their sharper areas stay
  until they are built again"; *Keep them* has the focus, Esc keeps them), then empties the list,
  its marks and the selection, moves the focus to the zones step and says "Zones deleted: N."
  The usual debounced `PUT /api/zones` saves the empty list; a drawing in progress stays.
- **Loading** (M3): `GET /api/zones` at boot answers `{"format", "revision", "zones",
  "problems"}` with `200` whenever the file is absent or readable (the engine's contract after
  the review of 2026-09-13).
  Every zone of `zones` is listed, valid or not: a zone named by a problem is marked with the
  problem's `reason` and stays editable and deletable (a missing or repeated id is replaced,
  so that two rows never act on each other). The problems naming no zone of the list (an
  unreadable entry, a file that is not JSON) form a notice above the list: "Some saved zones
  could not be read, so they are not in the list. Your next change to the zones will save the
  list as it is, without them", then each problem's `message`; the same notice counts the
  marked zones. Editing stays enabled. Only a network failure or a `5xx` disables drawing and
  editing ("Zones unavailable: ..." with *Retry*), since a save would overwrite zones the page
  could not read; any other refusal of the GET is shown like a broken file (empty list, one
  notice).
- **Saving**: every change (create, delete, rename, level, source, order) schedules
  `PUT /api/zones` 500 ms after the last one, one request at a time, with the whole document
  `{"format": "osxp-zones-1", "zones": [{id, name, zl, provider, polygon}]}` (polygons as open
  rings of `[lon, lat]` rounded to 9 decimals) and the headers `Content-Type:
  application/json`, `Accept: application/json` and **`If-Match: "<revision>"`**, the revision
  of the last GET or successful PUT (`""` when no file exists; no header only when the engine
  sent no revision). The `200` answer gives the new `revision` and clears the notice and the
  marks of the document; its zones are not applied back (the page already sends normalised
  coordinates). `keepalive` is asked for every save and granted only below 60 KB of UTF-8
  (browsers refuse a keepalive body above 64 KiB), so a save under way when the page closes
  still arrives. When the page becomes hidden (`visibilitychange`, and `pagehide`), a save
  waiting for its delay starts at once; while a save waits or runs, a `beforeunload` listener
  (installed only then) starts it and asks before leaving.
- **Conflict**: `409` (`ZONE_CONFLICT`: another window or a hand edit changed the file, nothing
  was written) reloads the zones with GET, replaces the list (the edits not saved are dropped)
  and says so in a toast and a line that stays until the next successful save: "The zones were
  changed in another window or in the zones file. Your last change was not saved; the list
  shows the saved zones again."
- **Refusals**: another failure of a save is a toast and a persistent line "Zones not saved:
  ..." with *Retry*. A `ZONE_*` error of a save, a plan or a job shows the engine's `message`
  (it names the zone and says why; the page's generic text for the code is only a fallback) and
  marks the zone named by `context.zone`. The marks of a save's refusals and of the file's
  problems go with the next successful save, those of a plan or job with the next accepted
  plan or job; changing the level or source of a marked zone checks it again with
  `zoneProblem` (`geo.js`), which removes the mark or replaces its reason. Engine errors are
  read from the `{"error": {...}}` envelope of the API.

#### Step 3 · Check what it will cost

- *X-Plane 12* (`app.js` `xplaneNotice`), shown only when `GET /api/status` knows no X-Plane
  folder: "X-Plane 12 was not found on this computer. OrthoStudio XP needs its folder: it takes
  the relief, roads, forests and buildings from it, and adds the tiles to it.", or, when Settings
  holds a folder that is not one any more, "The X-Plane 12 folder chosen in Settings was not found:
  <path>." with the same reason; its button *Choose the X-Plane 12 folder* opens Settings with the
  focus in the folder field of *Where is X-Plane 12 installed?*. Without it the engine refuses any
  estimate or build (`api.md` 2.2). User report, 2026-09-14: the Windows app in a virtual machine,
  X-Plane on the Mac around it, answered *Estimate* with "XP_GLOBAL_SCENERY_NOT_FOUND: Global
  Scenery is not installed in <not detected>." and nothing on what to do.
- *Build settings*: one sentence of plain words on what the build will use besides the tiles,
  the source and the level (`settings.js` `settingsSummary`: airports, coast, water, relief,
  overlays), and a *Change in Settings* button (section 2.4). The settings are the single source
  of truth: step 1's source and level are saved with `PUT /api/settings` when the user presses
  *Build*; the request carries `provider` and `zoom_level` only, `overrides` is not used by the
  page.
- **The estimate, live** (user request, 2026-09-14: pressing *Estimate* before *Build* was
  tedious): `POST /api/plan` with `{tiles, provider, zoom_level, zones}`, sent by itself 400 ms
  after the last change of the tiles, the source, the level, the zones or the saved settings, and
  when the Plan is shown again (the free disk space may have changed). One estimate takes 0.1 to
  0.5 s for one to six tiles on an M4 Pro. Until the answer, the figures of before stay, dimmed,
  with "Working out the cost…"; an answer older than the last change is dropped (`planChanged`,
  `estimate`). There is no button: *Estimate again* shows only after a refusal. That state sits
  beside the step's title, where it takes no room: on a line of its own under the build settings
  it stood as 24px of nothing between them and the cost (a user, 2026-09-21). The answer gives
  the two cost centres, in plain words:
  - *Network: your line* — downloads (requests), MB, time range `seconds_low`–`seconds_high`,
    measured throughput `mbps_measured` (or "not measured");
  - *Compute: your Mac* — images to prepare (textures), already prepared, seconds;
  - *Disk* — the engine's verdict (`app.js` `diskVerdict`): space needed is `disk.needed_gb` (images
    and downloads), then `free_gb` labelled "OK" in green when `disk.ok`, else "insufficient" in red
    after a red line "Not enough free disk space: this build needs about X, and OrthoStudio XP asks
    for twice that to be free (Y free now)" (7.0.4); `disk.ok` is the engine's rule, free > 2 ×
    needed (`orthostudio.estimate.Estimate.disk_ok`), applied by the page itself only to an answer
    without it (`dds_gb` standing for a missing `needed_gb`). The engine's `SYS_DISK_FULL` warning
    gets no second card under the red line;
  - *Warnings* — one card per code with message and remedy (the page carries the message
    and remedy of every warning code it knows in `i18n.js`, `ZONE_INVALID`, `ZONE_TOO_MANY`
    and `ZONE_CONFLICT` included, but a `ZONE_*` card uses the engine's own message and remedy
    when it has them; unknown codes show the code and whatever `message`/`remedy` strings the
    response carries);
  - per tile: already done / to build / images / downloads / MB.
- Any change of tiles, source, level or zones (not a rename) works the estimate out again.
- A refusal of the estimate or of *Build* by the engine is an error card under the button, as in Works
  (`renderPlanError`): the page's words for its code and the remedy, and a *Settings* button when
  its `action` is `settings`, which for `XP_DIR_NOT_FOUND`, `XP_GLOBAL_SCENERY_NOT_FOUND` and
  `DSF_GLOBAL_SCENERY_MISSING` puts the focus in the X-Plane folder field (in Works too). A
  refusal for want of X-Plane reads `GET /api/status` again, so that the notice above follows. A
  sentence of the page's own (no tile, zones not loaded, a build already running) and a network
  failure stay a line of text. The next estimate that succeeds takes the card of a refused one
  away.

#### Step 4 · Build and install

**Build and install** (primary) and **Build only** → `POST /api/jobs` with the same body plus
`install: true|false` and `queue: true`. Both are active as soon as tiles are chosen, with no
estimate to ask for first. They are disabled, the reason under them, only when no tile is chosen
("Choose tiles first (step 1)."), when the estimate was refused ("The estimate was refused: see
step 3.") or when the disk cannot hold the build ("Not enough disk space: about 8.3 GB needed, and
OrthoStudio XP asks for twice that to be free (6.1 GB free)."). Otherwise the line under them,
10 px below, sums the estimate up: "About 1.88 GB to download (5 – 9 min) and 8.31 GB on disk."
A click while the estimate of a change is under way waits for it, and starts nothing the estimate
refused or the disk cannot hold; both buttons stay disabled until the engine answered, so that a
second click never asks twice. A success empties the selection (a later build would build these
tiles again with its own): the map shows the tiles of the build in their own style meanwhile.

- Nothing else runs (`queue_position` 0): Works shows the new job.
- **A build under way or waiting** (user request, 2026-09-14): this one waits for its turn. Step 4
  says it before the click, under the buttons: "A build is under way: this one will wait for its
  turn." After it, the Plan stays, where more tiles can be chosen and queued; a toast says "Build
  queued behind N other(s): it starts as soon as the one before it ends.", and the map dashes its
  tiles.
- `409 SYS_TILE_IN_BUILD` (a tile queued from another window meanwhile): step 3's message line
  names the tiles, "Take them out of the selection, or wait for that build to end.", and the job
  list is read again; `409 SYS_BUSY` means that a tile is being deleted: "Try again in a moment."
  (an engine older than the page, without the queue, still means *a build is already running*).

**Requests** of the Plan: `zones` is always the list shown (possibly empty), invalid zones
included, so that the engine names them. When the zones could not be loaded (network or `5xx`),
the estimate and *Build* load them again first; if that fails too nothing is sent and step 3 says
so ("Nothing was sent: the zones of step 2 could not be loaded..."), since an empty list would
build without the saved zones and leaving `zones` out would build zones the page never showed.

### 2.2 Works

Job list (`GET /api/jobs`, newest first, status pill, tiles, provider, ZL, started). Beside its
title, a small trash (user request, 2026-09-13), shown when a finished job is listed: it asks
first ("Clear the job list?", the number of finished jobs, their progress and log deleted, the
tiles kept in the Library and in X-Plane, and "The build in progress stays in the list" when one
runs; *Keep them* has the focus), then `POST /api/jobs/clear`, reads the list again and says
"Jobs removed from the list: N." When the job shown was cleared, the running job is shown
instead, else none. The selected job (default: the running one, else the newest) shows, from
top to bottom:

- **Header**: the job, its status, *Stop* while it runs (`POST /api/jobs/{id}/cancel`). A job
  waiting in the queue says *waiting*, its button reads *Remove from the queue* (the same request:
  it never starts, "Build removed from the queue."), and a line under the bar says "Waiting: this
  build starts as soon as the one before it ends."; no time left is shown until it starts. The
  retry of the missing ones asks with `{queue: true}`: during a build it waits too ("Retry of the
  missing ones queued: it starts after the build under way."), and the job shown stays.
- **The whole job's numbers**, each label with a tooltip saying they are for the whole job:
  - *Progress*: `stats.progress` (0 to 1, weighted over every tile and step, never decreasing),
    shown `42 %` (rounded down, so that 100 % means finished) above an overall bar;
  - *Elapsed*: ticks every second in the page, from the last `stats.elapsed_s` plus the time
    since those stats arrived; once the job ended, `finished_at - started_at`; a figure arriving
    a little late never sets the clock back;
  - *Remaining*, while the job runs only: `eta_low_s`–`eta_high_s` formatted `1 min 40 – 3 min`,
    or *estimating…* while they are `null`;
  - *Throughput* when the stats carry `req_per_s` / `mb_per_s`.
  - While `stats.phase` is `"data"` (an older engine, which downloaded every tile's map data
    before building), a line under the bar: "Downloading map data (airports, roads, coastline,
    water) for N tile(s) before building" (`orthostudio.sources.osm.LAYERS`), N counting the tiles
    whose `osm` row is not a hit. The current engine downloads a tile's map data in its Data step,
    and says `build`.
- **One row per tile**, six step cells *Data · Terrain · Coast · Imagery · Assembly · Install*.
  Each cell has a dot, its name, a thin progress bar (`role="progressbar"`, `aria-valuenow`, the
  words in `aria-valuetext`) and one line of plain words (the full text in the cell's tooltip):

  | Step status | Bar | Words |
  |---|---|---|
  | `pending` (no row runs and none did real work; hits alone do not start a step) | empty | *pending* (*not started* once the job ended) |
  | `running` (a row runs) | accent colour, the step's `fraction`; the dot pulses | `42 %` (*running* at 0), then the download rate when the running row's line carries one (`21.6 MB/s`: Imagery's textures, the OSM layers of Data, a downloaded relief, `downloadRate`), else the last `message` -- which says "nothing to download (the image pieces are in the cache)" when a tile is built again with no fetching, since the counts of a step called Imagery read like a download (a user, 2026-09-18); the line of a row that ended is dropped |
  | `waiting` (rows did real work, the others have not started, none runs) | paler accent, the step's `fraction`; the dot does not pulse | *waiting · 42 %*; tooltip: partly done, the rest waits for its turn |
  | `done` | full, green | *done · 12 s* (the time from 1 s) |
  | `hit` | full, the dimmer `--hit` colour | *already done*; tooltip: kept from an earlier build, nothing to redo |
  | `failed` | full, red | *failed*; tooltip: the error's code and words |
  | `skipped` | dashed, grey, as far as its rows got | *skipped*; tooltip: an earlier step of this tile failed |
  | `cancelled` | dashed, grey, as far as its rows got | *cancelled* |
  | install of a build without install | empty, dashed | *no install* |
  | `running` or `waiting` in a job that ended (an older engine only: the current one says `skipped` or `cancelled` then) | dashed, grey, as far as its rows got | *stopped* |

  "As far as its rows got" counts rows: 1 for a row that ended with a result (done or hit), its
  fraction for one that ran until it was stopped. The percent is rounded down, and the engine's
  four-decimal rounding never shows as a percent less (`app.js` `stepView`, exported for the
  tests).

- **Errors** as cards: code, message, remedy, severity tag, and an action button: `retry` →
  *Retry the missing ones* → `POST /api/jobs/{id}/retry` (a new job, whose id the page follows);
  `settings` → *Settings* → screen 4; `none` → no button.
- **The log**, a `<details>` of the last 500 `log` entries, each "time of day, tile, message"
  (`ts` counts seconds from the job's creation: the time is `created_at + ts`). It is built once
  per job and language and never rebuilt: new lines are appended, the oldest leave the top past
  500, it stays open or closed as the user left it, and it follows new lines only while scrolled
  to its end (scrolled up, the lines under the eyes stay still). The summary counts its lines.
- When the job is finished, the **report**: already done / to build / failed (`report.hits`,
  `built`, `failed`), elapsed, size and *Install n / m*; per step, hits, built, failed and time
  (from `report.tiles[].nodes[]`, by `role`); the decisions (textures, built, cached, parent
  fallback, placeholders, missing; "Steps not done (an earlier step failed)" for
  `SYS_UPSTREAM_FAILED` when there are some; "Image pieces asked for again after a pause" and
  "Image pieces recovered after a pause" whenever a textures decision's `second_pass` is above 0,
  both rows, a zero included). Each tile counts once (`app.js` `reportTotals`,
  `decisionCounts`): its size and whether it is in X-Plane come from the report's tile when it
  says (`pack_bytes`, `installed`), else from its `pack` decision; its missing textures from its
  `textures` decision, else from its `TEX_MISSING` error's `context.count`. (The engine gives both
  for the same tiles: adding them read "Install 6 / 6" for 3 tiles installed out of 6.) Three
  columns under two titles on one line, *Final report* over the figures and the steps and
  *Decisions* over its list (a user saw *Decisions* stand lower than *Final report*, 2026-09-21);
  one column under 1080px, each title above its own part.

**Rendering.** The panel is built once per job and language (`buildJobView`), then updated in
place (`updateJobView`): texts and attributes change only when they differ, so tooltips, the focus
of *Stop* and the bars' width transition survive. Events redraw through a throttle: at most four
times a second (`RENDER_EVERY_MS = 250`), drawing the state as it is then.

**Node to step mapping.** The journal gives each node entry its `stage`. Otherwise the page maps
the node's `role`, or the last `/`-separated segment of its id without `#n`, with the engine's
table (`app.js` `ROLE_STEP` = `orthostudio.api.stages.ROLE_STAGE`, a test keeps them equal): `osm`,
`coastline`, `dem`, `vectors` → data; `mesh` → terrain; `masks` → coast; `textures` → imagery;
`xp12`, `dsf`, `overlay`, `pack` → assembly; `install` → install. The rule names of the P2b
contract (`tile.dsf`, `tile.textures`, ...; `NODE_STEP`) still map, for older journals.

**Events** (`GET /api/jobs/{id}/events`, `EventSource`; the SSE event name is the entry's `event`,
its `data` the flat journal entry of `docs/specs/api.md` 5.2):

| `event` | Fields the page reads |
|---|---|
| `started` | `seq`, `ts`, `tile`, `stage`, `node`, `role`, `weight_s` (`key`, `kind` unused) |
| `progress` | `tile`, `stage`, `node`, `role`, `fraction` (0 to 1), `message`, `weight_s` |
| `done` | `tile`, `stage`, `node`, `role`, `hit`, `wall_s`, `weight_s`; a node with nothing to do (the OSM row of a tile whose map data is there) gets `done` with `hit: true`, with or without `started` |
| `failed` | `tile`, `stage`, `node`, `role`, `error` (`OsxpError.to_dict()`), `skipped`, `cause`, `weight_s`; `SYS_CANCELLED` without a cause is a cancelled node; no node: the build itself raised |
| `log` | `message`, `tile`, `stage`, `ts` |
| `stats` | `stats: {running, pending, done, failed, hits, elapsed_s, progress, eta_low_s, eta_high_s, phase}`, the whole job's: `elapsed_s` since the job started, `progress` never decreasing, the ETA range `null` while unknown, `phase` `"data"` while map data downloads first, then `"build"`; sent about every second |
| `finished` | `status`, `report`, `decisions`, `error` |

The page applies the entries to its copy of the job (`app.js` `applyEvent`), as the engine's
`Job._handle` does: `started` runs a node unless it ended with a result, `progress` moves its
fraction, `done` ends it (a hit found by a second pass keeps what the first pass built), `failed`
ends it failed, skipped (with a cause) or cancelled (`SYS_CANCELLED`); each node entry also sets the
node's `weight_s`. The step's status and fraction then follow the engine's rules from its nodes
(`stepTotals` = `orthostudio.api.jobs.stage_status` and
`orthostudio.api.progress.weighted_progress`): failed, cancelled, skipped; done once every row ended
(hit when all were hits); pending while no row runs and none did real work; running while a row
runs; waiting otherwise. The fraction weighs each row by `weight_s`, the weight the engine's own
stage fraction uses (0 for a hit, and for a row skipped or cancelled before it started): 1 for an
ended row, its fraction while it runs, 0 otherwise; by count when nothing weighs. Recomputed so, the
page's steps are the engine's after every entry (a test drives the engine's `Job` and compares,
`tests/test_ui_static.py`). A recompute never moves a bar back between two reads (the rows no entry
touched keep the weights of the last read); the next read may. `failed` adds an error card, one per
failure (a node skipped because of it adds none).

`GET /api/jobs/{id}` answers `tiles[].stages[stage] = {status, fraction, wall_s, nodes: [{node,
role, status, key, hit, wall_s, fraction, weight_s}]}` (once the job ended, a stage whose rows
partly ran is `skipped`, or `cancelled` when the job was), `stats` (the same object), `errors` (with
`stage`) and `last_seq`. `normalizeJob` **replaces** the page's steps with those stages at every
read. The page reads the job again after each `done` and `failed` it applies and on `finished`, one
read at a time (another is queued when one is under way); the entries that arrive during a read are
applied again to its answer. Every entry carries `seq`: one the job already reflects (`seq <=
last_seq` of the state read last, or already applied) changes nothing, except a `log` line, which
the state does not carry. So the whole journal the stream replays when the page subscribes costs
nothing, and the log is rebuilt from it. `finished` closes the stream (always: an `EventSource` left
open reconnects every two seconds once the engine ends the stream).

**Older engines** (a `osxp serve` started before the update): stats without `progress` or `phase`
count `elapsed_s` per phase, so *Elapsed* comes from `started_at` and the clock, and *Progress*
from the steps' fractions, each step weighing its node count (a step without nodes one, the
install step of a build that does not install nothing). Their rows carry no `weight_s`: the page
weighs them equally, as those engines did, and takes a step's status from its rows by the rules
above rather than the engine's word (which called "pending" a step with rows done and rows
waiting), so that a read and the next entry agree; a step they still call running once the job
ended shows *stopped*. Entries of the P2b contract (`step`, `node_id`, stats sent flat, `eta_s`
for a range of `0.8 × eta_s`–`1.5 × eta_s`) are read too.

### 2.3 Library

**Disk space** (below the table, `GET /api/disk`, `POST /api/clean`): the data used by the tiles on
this computer, the data no tile needs any more, the downloaded images, the map background and the
**downloaded relief**, each with a plain tooltip. *Free space…* asks first, in a modal dialog that
says what goes and how much (Escape keeps everything), with **two** checkboxes, one for the
downloaded images and one for the relief: a square of relief is 40 MB from Copernicus against
800 MB of imagery at ZL16, and comes back much faster, so the choices are separate. The relief was
counted by nothing until a user emptied everything and found 1.4 GB of it left (2026-09-18). It is
disabled while a build runs and when there is nothing to free, and ends with a toast of what came
back. A 409 `SYS_BUSY` shows as a card in the Library's words.

What is on this computer, what is in X-Plane, and what each button will do, in plain words
(`map-zones.md` 7.0). The lead says it: removing a tile from X-Plane keeps its files, so it can
be added back at once; deleting it also frees the disk space (tiles built by OrthoStudio XP only).

**Rows.** `GET /api/library` answers one row per pack: the tile pack (`kind: "ortho"`) and, for a
tile with overlays, a row of the shared `yOrthoStudio_Overlays` pack (`kind: "overlay"`), which the
engine installs, removes and deletes with its tile. The table shows the tile packs only (`app.js`
`libraryTiles`; a row without `kind` counts as one), in the engine's order; an Ortho4XP import and
an osxp build of the same tile are two rows.

| Column | Content |
|---|---|
| Tile | `+43+005`, the pack's `path` in the `title` tooltip; a *files missing* pill when `present === false` |
| Imagery | the source's code and the detail level's name, the zoom level as secondary text ("BI · Standard ZL16"); tooltip: the source's name from `GET /api/providers` and the detail level at the tile's latitude; an em dash when neither is known |
| In X-Plane | *yes* / *no* pill (`installed`) |
| Size | `size_bytes` (`fmtBytes`), an em dash when `null`. The header's tooltip (dotted underline) says that, for a tile built by OrthoStudio XP, most of it is shared with OrthoStudio XP's cache, which the status bar counts under Store: the two are not on the disk twice |
| Built by | *OrthoStudio XP* / *Ortho4XP* |
| (actions) | two columns, so that the buttons line up from row to row |

**Actions** (`{name}` is the row's `name`, else the last component of its path; every change
sends `{"path": <the row's path>}` as JSON, `app.js` `libraryRequest`, since the name alone is
ambiguous when the tile was built into two output folders):

- **Roads twice** (user request, 2026-09-14: AutoOrtho or XPME with OrthoStudio XP; `api.md` 2.3,
  `install.md` 4.3): when another active pack's overlays hold the square of a tile X-Plane shows, a
  yellow line above the table says "Roads, forests and buildings twice on 1 square(s) (+43+005):
  AutoOrtho draws them too." with *Leave these roads to AutoOrtho* (`POST /api/library/overlays`
  with `use: "others"`), and the row shows a *roads twice* pill. The packs are named as a user knows
  them (AutoOrtho, XPME, Ortho4XP). A square whose roads were left to a pack no longer active gets a
  red line, "No roads, forests or buildings on 1 square(s)...", with *Take OrthoStudio XP's roads
  back*, and a *no roads* pill. A square left to another pack shows a *roads: AutoOrtho* pill that
  is a button: it takes OrthoStudio XP's roads back. A toast says that X-Plane takes the change into
  account at its next start; a refusal (X-Plane running, a tile in a build) is a card. A tile in a
  build is left out of the lines;
- **A tile in a build** (user request, 2026-09-14): a row OrthoStudio XP built whose tile is in
  a build under way or waiting shows a *being built* pill, and its X-Plane button is disabled,
  with a tooltip: the end of the build decides what X-Plane shows of the tile. While any build is
  under way or waiting, *Delete…* is disabled on every row (the engine deletes only between
  builds) and a line above the table says so. The Ortho4XP row of the same square stays free. A
  refusal the page did not foresee (`SYS_TILE_IN_BUILD`, another window) is a card in the
  Library's words;
- in X-Plane: *Remove from X-Plane* → `POST /api/library/{name}/uninstall`; its tooltip says
  that the files stay on this computer and the tile can be added back at once;
- not in X-Plane: *Add to X-Plane* → `POST /api/library/{name}/install` (X-Plane shows it at
  its next start);
- built by OrthoStudio XP only: *Delete…* (danger style), after the confirmation below →
  `POST /api/library/{name}/delete`. OrthoStudio XP never deletes a tile of Ortho4XP;
- imported from Ortho4XP, in its place: *Remove from the list* → `POST /api/library/{name}/forget`
  (user request, 2026-09-21): the Library forgets the tile and its files stay where Ortho4XP put
  them; a toast says so. Disabled while the tile is in X-Plane, with a tooltip saying to remove it
  from X-Plane first: off the list, the Library could no longer take it out;
- files missing (`present === false`): no X-Plane button, only *Delete…* (OrthoStudio XP rows); the
  engine then forgets the tile. An imported tile still in X-Plane keeps *Remove from X-Plane*, its
  way to *Remove from the list*.

While a request runs, the row's buttons are disabled (and the row `aria-busy`), across
re-renders too, so a double click sends nothing twice. When the engine answers, success or
refusal, the library is loaded again (the map's installed tiles with it,
`planMap.libraryChanged()`) and the status bar too, since a refusal may come after the engine
changed something (a deletion stopped halfway, a tile deleted meanwhile); a reload that fails
keeps the rows shown and says why in a toast. Focus lost with a disabled or re-rendered button
goes back to the same row, else to the row now in its place (after a delete), else to the
screen's title; it is never taken from where the user moved it.

**Confirmation** (`index.html` `#library-delete`, `app.js` `confirmDelete`): a native modal
`<dialog role="alertdialog">` placed outside the screens, on the page's tokens. Title "Delete tile
+45+005?"; then "It is removed from X-Plane and its files are deleted from this computer (about
1.2 GB)." (X-Plane is named only when the tile is installed, the size only when `size_bytes` is
known) and "The imagery already downloaded stays in the cache, so building it again later is
faster."; for a tile whose files are missing, one sentence: its files are already gone, and it is
removed from this list (and from X-Plane when installed). Buttons *Keep it*, which has the focus
first, and *Delete* (solid danger style). Escape keeps the tile (the browser's own handling, and
the page's in case the browser skips it); the rest of the page is inert while the dialog is open,
and the browser gives the focus back to *Delete…* when it closes. A success shows the toast
"Tile +45+005 deleted: 1.1 GB freed." from `freed_bytes`, or "Tile +45+005 deleted." below 1 MB
(a tile built a few minutes ago frees next to nothing at once: its cache stays for a build that
may still use it). When the answer's `warning` is a string (the tile is deleted, but its cache
space could not be freed), the toast gives no size: "Tile +45+005 deleted. The space it took in
the cache will be freed later."

**Errors** become error cards in `#library-errors` (a polite live region), shown once the library is
loaded again, naming the tile and cleared by the next action, like on Works. For the refusals the
Library knows, the card is blocking (the change did not happen), has no action button (the remedy is
never in Settings) and says what happened in the Library's own words, since the engine's speak of
installing, paths, `orthostudio.toml` or `osxp clean`; the engine's message and remedy stay in the
tooltip of the card's message (`app.js` `libraryCardContent`):

| Code | When | The card says |
|---|---|---|
| `XP_RUNNING` (409) | any change while X-Plane runs, a delete of a tile X-Plane does not show included | X-Plane is running: OrthoStudio XP does not change its scenery while it runs. Quit X-Plane, then try again. |
| `SYS_BUSY` (409) | a delete while a build runs | A build is running: tiles can be deleted only between builds. Wait for the build to finish (see Works), or stop it, then delete the tile again. |
| `SYS_PACK_NOT_OSXP` (409) | a delete of a pack OrthoStudio XP did not build (nothing is touched) | This tile was not built by OrthoStudio XP, so OrthoStudio XP does not delete it. Nothing was deleted. You can remove it from X-Plane, or delete its folder yourself. |
| `SYS_PACK_IN_XPLANE` (409) | *Remove from the list* of a tile X-Plane shows (the button is disabled then; another window) | This tile is in X-Plane. Remove it from X-Plane first, then from the list. |
| `SYS_PACK_NOT_IMPORTED` (409) | *Remove from the list* of a tile OrthoStudio XP built | OrthoStudio XP built this tile. Delete takes it away, and the list with it. |
| `SYS_WRITE_FAILED` | a deletion stopped halfway (a file held open) | OrthoStudio XP could not delete all of this tile's files. Quit X-Plane and any program that may use them, then delete the tile again: what is already deleted stays deleted. |
| `SYS_WORKING_DIR_INVALID` (422) | the row's pack is no longer where the library had it | This tile was deleted or moved meanwhile. The list is up to date now. |
| `XP_PACK_CONFLICT` (409) | X-Plane's folder of that name is something else, or a link to another copy of the tile | X-Plane already has a folder of this name, and it is not this copy of the tile. Remove the other copy from X-Plane, or move or rename that folder in Custom Scenery, then try again. |

Any other error (a network failure included) shows the engine's words, or the page's for the
codes `i18n.js` knows.

*Import my Ortho4XP tiles…* is one button (user report, 2026-09-21: a field, *Choose…* beside it
and *Import* were two buttons for one thing, and the field left empty imported nothing without a
word). It asks for the folder in the platform's own dialog, opened by the engine (`POST
/api/choose-folder`, "Choose the Ortho4XP folder (the one holding Ortho4XP.py)": a page cannot learn
the full path of a folder picked in the browser), starting at the folder imported last, else at
the Ortho4XP folder of the newest imported tile (`app.js` `ortho4xpStart`), then imports it: `POST
/api/library/import-ortho4xp {folder}`. The line under the button counts the tiles, not the
overlays pack beside them, and names the folder ("2 tile(s) imported from ~/Ortho4XP."); finding
none, it says where it looked (`searched`). A cancel changes nothing. Where no dialog opens (a
Linux without zenity or kdialog: `501 SYS_NO_FOLDER_DIALOG`, a toast; an engine older than the
page), the field *Ortho4XP folder* appears and the button imports what is typed in it. The status
bar's count is `library_count`, which counts tiles, not rows.

### 2.4 Settings

In plain words (user request, 2026-09-13: "sand, land, lakes, radius... nobody understands them";
what each setting really does, and the wording of every question: `settings-plain-language.md`).
`settings.js` holds the descriptions and pure functions (tested under node) and draws the screen;
`app.js` owns the saved settings, the draft and the buttons. The screen is drawn again on each
answer, apart, and only what differs is put in (`app.js` `morphChildren`): a text, an attribute, a
field's value, a control whose attributes changed; what has not changed is not touched, whatever
the change (a user saw the whole screen flash in the Mac's window each time a field was left, and a
number answer late under its own arrows, and asked for one method for every case, 2026-09-21). A
canvas drawn later says what it shows in `data-version`. The draft keeps its identity (`setDraft`),
since the parts left in place keep handlers that write into it. The focus, the caret and the
window's scroll position survive a redraw (user report, 2026-09-14: an answer changed at the bottom
brought the top of the screen back, since removing the focused answer made the browser lay out the
half-drawn page). Nothing is saved before *Save*; *Undo
my changes* goes back to the saved settings, *Default values* to the schema's defaults
(Ortho4XP's, and OrthoStudio XP's own `overlays`), the X-Plane folder and the data folder kept,
since they are this computer's and no look of the tiles; a draft that differs from the saved settings says
"Changes not saved yet". The action bar sticks to the bottom.

1. **Presets**: three buttons, *Recommended* (ZL16, main airports at ZL18), *Best quality* (ZL17,
   every airfield at ZL18), *Light on disk* (ZL15, nothing extra), each with its size per tile
   (the study's section 5). A preset sets only the detail level, the airport cover, the coast
   fade and width, the water and the lakes; the imagery source, the relief, the overlays and every
   expert setting stay as they are. The button whose values the draft holds is pressed, and a line
   says which preset the answers match, or none.
2. **Questions**, cards packed in two columns, each a `<fieldset>` whose legend is the question,
   with one sentence on what it changes and radio choices (the recommended one tagged *recommended*,
   a note under a choice when it helps). First what OrthoStudio XP needs to know of the user's
   X-Plane (user request, 2026-09-13): the X-Plane 12 folder (the detected one from `/api/status`,
   the other X-Plane 12 of the machine named under it when the status gives any (`xplane.others`:
   a user's tile went into an X-Plane 12 he had forgotten, 2026-09-17), and a field for another),
   then where the tiles and the downloaded imagery go
   (`essential.data_dir`, user request 2026-09-15: an external disk): the folder in use from the
   status's `data_dir`, warned when its disk is unplugged, a field for another with *Choose the
   folder for the tiles…*, and two notes, that the tiles already built stay where they are and keep working
   (a user did not understand the first wording, which asked to delete them before building into
   the new folder, 2026-09-15: that step is gone), and which disk formats can hold the data on the
   engine's platform (exFAT and FAT32 are refused), and that the folder must be outside X-Plane's
   Custom Scenery, where OrthoStudio XP puts its own links (a user typed his Custom Scenery there
   and was only refused once he had saved, 2026-09-17), then roads,
   forests and buildings (X-Plane's / none,
   `essential.overlays`, whose sentence starts "Using simHeaven X-World? Choose “None from
   OrthoStudio XP”", since a user looked for the simHeaven option and did not find it at the end of
   the list; **when the engine finds such a pack in Custom Scenery** the question answers itself:
   `xplane.packs_of_their_own` of `/api/status` names it, *None* becomes the recommended answer and
   both answers say why, because a user of the X-Plane.Org page had X-World installed, kept ours,
   and had everything drawn twice, 2026-09-20. On a **first run**, with no settings file yet, such
   a pack also makes *None* the answer *chosen*, written to the file so that the page, a build and
   the command line agree: a user who never opens Settings is the one this happened to. Nothing is
   written when nothing was adjusted). Then the look of the tiles: whose photos (a select of the providers, an unavailable
   one disabled); how much detail (ZL15-18 in plain names with the ground size at 45°, the
   multiplier of disk and time, levels above the source's `max_zl` disabled); airports (main ones
   with an ICAO code / every airfield / no, naming the airport level); coast fade (sand / rocks);
   how far out to sea (50 / 100 / 200 m, hidden for a fade in three steps); water near the coast;
   lakes and rivers (10 / 25 / 50 %); relief (X-Plane 12 / my file, then the file's path, warned
   while empty, and its holes). A value set by hand that no choice offers (a width of 80 m, lakes at
   40 %, the airport mode `existing`, the `3steps` profile) is shown as one more choice,
   never hidden.
3. **For experts** (`<details>`, closed): a band with a chevron, its title, how many settings are
   behind it and its one-line lead, all readable while it is closed, and whether it was open is
   remembered in `localStorage` (a user found the old one-word summary too well hidden,
   2026-09-18). It holds every other setting in groups (airports, coast and sea,
   lakes and rivers, terrain, roads, light and ground, X-Plane objects), each with a plain label, a
   one-sentence note, its unit in the control's frame and its name in Ortho4XP as a small badge, on the
   aligned grid of the old form (enum as a select with named options for the road levels, the sea
   level at the shore and the coast fade precision; boolean as a switch; number with its
   `minimum`/`maximum`; list as comma-separated text). The fade in three steps is one text field of
   three widths ("100, 200, 100"): three valid widths set `profile = "3steps"`, an empty field goes
   back to `sand` at 100 m, anything else is refused with a message. The settings OrthoStudio XP no
   longer offers (`ratio_bathy`, `imprint_masks_to_dds`, `mesh_zl`, `masks_custom_extent`, and
   `masks_use_dem_too`, which needs Ortho4XP's own coast step now that OrthoStudio XP ships no
   Ortho4XP) appear under *No longer offered* only when their value is not the default.
   *Folder of hand-made mesh patches* (`expert.patches_dir`) is the one expert field holding a
   folder: it takes two columns, its placeholder is the folder used when it is left empty
   (`<status.home>/patches`) and *Choose…* beside it opens the platform's dialog (a user of the
   X-Plane.Org page asked what to type in it, 2026-09-17). Under its note, what the folder shown
   holds, saved or not (`GET /api/patches?dir=`, asked again when the field changes): "Patches
   found for 1 tile(s): -20-044.", the first six tiles named and the rest counted, "No tile has
   patches in this folder yet." or "This folder does not exist."

*Are the photo colours right for you?* (`essential.photo_look`) offers the look in plain words --
as delivered, toned down, toned down a lot, my own values -- and, on *my own values*, the three
expert numbers appear under it as sliders, so nobody has to go hunting for them (2026-09-18).
Under the answers, **the preview**: one image of the ground where the map is looking
(`GET /api/photo-sample` with the chosen source and the map's centre), drawn twice, as delivered
and as the answer would encode it. `ui/colour.js` computes it in the browser, with the arithmetic
of `textures/colour.py`, and a test holds the two equal to within one step
(`tests/test_ui_colour.py`), so the preview is what the build will write. No sample yet (the map
has not drawn, or nothing came back) simply leaves the preview out. Under the two images, a line
says which square it is and where (`+46+006 (46.2°, 6.1°)`) and that it is the centre of the map
in Plan, which is how a pilot knows what they are judging (a user asked, 2026-09-18).

The same two images appear in **step 1 of the Plan**, under *Photo colours*, which sits beside
the imagery source and the detail level and sets the colours of the **squares chosen**: one square
selected changes that one, six change the six, which is also "one colour for this build" (a user,
2026-09-18). Its choices are *Same as Settings* and the four of the Settings question; *My own
values* shows the same three sliders. When the squares chosen disagree, the control says so and
sets nothing until one is picked. The colours travel with the zones, in the map's document
(`map-zones.md` 3), so they are remembered; `ui/preview.js` draws the two images and `app.js`
gives it the sample and the words (`plan.colours_where`, `plan.colours_note`).

Each **zone** keeps its own control in its row, with the sliders under it on *My own values*: it
inherits its square while it says *Colours: same as the tile*, and wins inside its polygon
otherwise.

**The map shows the result, not a thumbnail** (a user asked, 2026-09-18). A square or a zone that
carries its own colours is repainted on the map itself: a `L.GridLayer` on the `osxpColours` pane
(z-index 250, over the imagery and under the grid) draws each map tile again through
`ui/colour.js` -- the arithmetic a test holds equal to the engine's -- and clips it to the
region's polygon, so only what carries its own colours changes and the rest stays the imagery
underneath. The squares are painted first and the zones after, from the last of the document to
the first, since what is painted last is what a build would apply: a zone wins inside its polygon,
and where two overlap the one higher in the list wins (`build.photo_zone_colours` gives a texture
the colours of the first zone holding its centre). Painting the squares last hid the colours of
every zone drawn inside one (a user, 2026-09-18). The layer exists only while something carries its own colours, and follows every change of
the choices, of the zones and of the squares. In the mock mode it paints its own ground image, so
it can be seen and measured with no network.

**Airports on the map** (a user of the X-Plane.Org page asked for an OSM background to tell
whether a square holds the airport he wants, 2026-09-19): a checkbox in the legend draws the
airports of the view, from the index the app ships (`GET /api/airports/in`), so nothing is
downloaded and they show **over the aerial imagery**, where a runway is not always obvious. Off
unless the user asks, remembered in `localStorage` (`osxp.mapAirports`). Below zoom
`AIRPORTS_MIN_ZOOM` (8) there would be thousands of them and the line says to zoom in; from there
the rings alone say where the airports are, and the codes join them at `AIRPORTS_LABEL_ZOOM` (9),
since half a continent of codes is a wall of text (same user). Above the threshold,
the view is read with a quarter of padding, rounded to a tenth of a degree so that panning a
little asks nothing, and capped at `AIRPORTS_LIMIT` (200). Only the airports **carrying a real
ICAO code** are drawn (`icao_only`): 57 % of a full index has an identifier of X-Plane's own
instead (`XED0051`, `XLF001D`), which a pilot cannot read on a map (same user). Each
airport is a ring, its code beside it, and its name in the tooltip; the pane sits over the labels
and under the zones, and takes no pointer event. The code is offset by the icon's **anchor**
(`[-13, 7]`), not by a transform in the stylesheet: Leaflet writes its own transform on the
element to place it and throws the stylesheet's away, which left the ring sitting on the first
letters twice over (a user, 2026-09-19). A code that would land on another airport's ring, or on
a code already placed, is left out; its ring stays and its name is still in the tooltip. Measured
on a dense view: 55 rings, 44 codes, no code over a ring and no two codes touching.

**A street map beside the photo** (same user, same day): a third checkbox in the legend swaps the
aerial imagery for OpenStreetMap, rendered by OpenFreeMap and served by the engine
(`api/basemap.py`). It is vector, so the page loads MapLibre GL and its Leaflet bridge
(`vendor/maplibre/`, about a megabyte) **the first time it is asked for**, never on a page that
stays on the photo; until they are there the imagery stays, and a failure says so in the legend
and goes back to the imagery. The colours of a square or a zone are not repainted over it: they
would tint roads and houses and say nothing about a build. Off by default, remembered in
`localStorage` (`osxp.mapStreet`), with OpenFreeMap's attribution in the map's corner.

**Marks are outlines, never fills** (a user, 2026-09-18). A chosen square, an installed one, a
zone: each is a stroke and nothing else, so no translucent colour lies about the ground under it
now that the map shows the very colours a build will encode. The tile being built still pulses,
on its stroke.

A tile in the Library carries **other colours** when the pack on the disk was built with colours
other than the ones its square asks for now (`photoDiffers`, from the `photo` of `GET /api/library`
and the square's own): the map shows what a build would give, this says what X-Plane holds today,
and building the tile again applies it (a user asked what happens to an installed tile,
2026-09-18). A pack built with the plain colours, or before they existed, records none and is
never marked.

The other way round is a button of step 1, **Go back to the colours already built**: it appears
when a chosen square has a pack on the disk built with other colours, names the tile (or how many
squares), and gives each square the colours of *its own* pack -- a named look of the menu when one
matches, its numbers otherwise (`tilesBuiltOtherwise`, `map.setEachTilePhoto`). Nothing is built
again: the tile on the disk does not move, and the mark in the Library goes quiet because the two
now agree (a user asked to be able to go back to the colours already installed, 2026-09-18). The
pack of an installed tile answers first, being the one X-Plane shows; a pack that recorded nothing
answers the plain colours, as the mark reads it too.

Step 1 also says, **whether or not anything is chosen**, how many squares and zones carry their
own colours -- a map repainted from an old visit must never be a mystery (same user) -- and
carries **Give the colours back to Settings** beside it: it clears every square and every zone,
installed or not, and says how many. Sparing installed tiles only made the button vanish while a
pilot worked on one; their pack records the colours it was built with, so the setting has nothing
to keep for them, and the Library says when the two no longer agree.

Step 1 carries no thumbnail: the map says it better, and one line points at it. Settings keeps its
two images, having no map.

A test holds the settings the screen offers (questions, expert fields, retired) equal to the
schema's leaves, so a setting added to the engine cannot be left out of the page.

Layout of the expert fields (user request, 2026-09-13: a hint clamped to three lines that
opened on hover pushed the fields around it, and controls did not line up): a grid of equal
columns (`minmax(260px, 1fr)`); each field spans three rows shared with the fields beside it
(`grid-template-rows: subgrid`: label line, control, note), so the controls of a row line up
even when a label wraps; every control fills its column. A number and its unit share one frame,
the unit after the field and so after its arrows (inside the field, the Mac's arrows covered it,
2026-09-21); the Plan's radius is drawn the same way. A field holding a folder
takes two columns (`.field-span2`, one column again under 700 px): a path and its *Choose…* button
do not fit in one. Nothing changes size on hover. *Save* → `PUT /api/settings` with the whole document; *Reset* reloads
`GET /api/settings` (or the schema defaults on the *Defaults* button). Validation errors
from the engine (`422`) are shown next to the form, in the page's words for the code with its remedy:
`XP_DIR_NOT_FOUND` names the folder and, for one that exists, the subfolders it lacks (`Resources`,
`Custom Scenery`), since a user without X-Plane read only that the folder was not found.

### 2.5 Status bar

From `GET /api/status`: version, X-Plane path and whether it runs (a warning when it does,
since installation is refused then), doctor summary (`n ok · n warn · n fail`, the details in
a popover), library count, and OrthoStudio XP's folder, or the data folder chosen in Settings with
a *disk not plugged in* pill while it is missing. The store and chunk sizes come from
`GET /api/sizes`, asked after each status and not awaited (`loadSizes`): "…" until they come, then
that line alone is drawn again (`renderDiskSizes`), so the doctor's popover stays open. On Windows
their walk opens every file of the store, and the page used to wait for them. Language and theme
switches. A refusal for want of the data folder (`CFG_DATA_DIR_MISSING`, `CFG_DATA_DIR_INVALID`)
has a *Settings* button that focuses its field. The library count follows every read of the library
(a build that ends, a tile installed or deleted), and a build that ends reads the status again for
the sizes (a user saw "0 tile(s) in the library" stay after builds, 2026-09-15).

**The first screen** (2026-09-22): the page shows the screen of its address at once, then fills it
in as the engine answers. It used to wait for every answer (status, zones, library, sources,
settings, their schema, jobs) before showing any screen; the menu is wired before that wait, so
users on Windows, where the status was slow, saw the menu alone until they clicked a tab. The
Plan's source and detail level, and Settings, are drawn as soon as their own answers are in
(`boot`, `early`), the map takes its view from the library when it comes, the screen is drawn
again once every answer is in, and until the status says, Settings reads *Looking for X-Plane 12…*
rather than *Not detected*. The engine's part is `api.md` (`GET /api/status`, `GET /api/sizes`).

**When the engine does not answer** (`renderEngineBanner`): a banner at the top of every screen,
the same one that announces an engine older than the page. An engine that answers *with an error*
gets it too, naming what it said and offering *Reload the page*, and it goes as soon as a status
comes back. Before that, `GET /api/status` failing wrote one line at the foot of the page and left
every screen as empty as it was drawn: a user on Windows saw the fields greyed out, had nothing to
go on, and the thread guessed for two days (flusi.info, 2026-09-20). An engine that answers nothing
at all is still the *stopped* screen, through the presence ping.

**Your own elevation files** (2026-09-19): under the relief question, *Do you have elevation files
of your own?* takes a folder (`essential.relief.folder`, a field and a *Choose the folder…* button
like the other folders of Settings). It says what the files must be called, that subfolders are
searched and any resolution read, and that the relief chosen above answers wherever the folder has
nothing. A user of the X-Plane.Org page has the lidar models of Europe by the hundred and asked to
name the folder once (`dem.md` 3.0a).

**Paths with a tilde** (2026-09-19): wherever the page *reads out* a folder (this bar, the
Library's line about which X-Plane it speaks of, Settings' detected folder and data folder, the
Plan's notice about a saved X-Plane that is gone), what lies under the home folder is written
`~/...` (`i18n.js` `homely`, fed by `user_home` of the status). It is shorter to read, and the
name of whoever runs OrthoStudio XP stays out of the pictures posted with a report. Fields the
user edits, and every path the engine is asked to open or to save, keep the path itself.

**Pinned bars** (user request, 2026-09-14): the top bar stays at the top of the window and the
status bar at its bottom, wherever the page is scrolled (`position: sticky`). The status bar's
height, measured by `app.js` `trackStatusbarHeight` into `--statusbar-h` (it grows when its items
wrap), keeps Settings' sticky *Save* bar and the toast above it; `scroll-padding` keeps what the
page scrolls to (a card, a focused field) between the two bars, and the Plan's map column sticks
below the top bar.

**Quit** (top bar, user request 2026-09-13; shown when `can_quit`): asks first in a modal dialog
("Quit OrthoStudio XP?", the page will not work until OrthoStudio XP is opened again, and, when a
job runs, that quitting stops it while what is built is kept, and that the N builds waiting are
cancelled too; *Stay* has the focus), then
`POST /api/quit` (`force` when a job runs) and covers the page with "OrthoStudio XP is stopped. You
can close this tab. To use OrthoStudio XP again, open the OrthoStudio XP app."

**The line under a job's title** gives the provider and the zoom level, the relief the tiles are
built on (`relief` of the job: *X-Plane relief*, *Copernicus relief*, *own relief file*; nothing
from an engine that does not say), then whether the build installs.

**Presence** (2026-09-17): the page says it is open, `POST /api/presence`, at load, every 30 s and
when it shows again. When the engine does not answer twice, 3 s apart (an answer with an error does
not count), the page covers itself with the same stopped screen, whose text says that OrthoStudio XP
no longer answers: it was stopped, or it stopped by itself a few minutes after its last page closed
(`api.md` section 6). Not in the mock mode.

**The file manager** (user request 2026-09-13): the label names the platform's own
(`status.platform`: "Show in Finder", "Show in File Explorer", "Open the folder") and every button
calls `POST /api/reveal`: a folder icon on each Library row (the tile's folder, a third action
column), a button under Disk space (the data folder, `status.data_dir`, where the sizes are
measured; hidden while its disk is unplugged, when a line under Disk space says so), and one
beside the detected X-Plane folder and one beside the data folder in Settings. An error is a
toast.

**Choosing a folder** (user request, 2026-09-15: the X-Plane folder had to be typed): *Choose the
X-Plane folder…* beside the field of *Where is X-Plane 12 installed?* and *Choose the folder for the
tiles…* beside the one of *Where should the tiles and the downloaded imagery go?* in Settings (one
label for both read as the same button), *Import my Ortho4XP tiles…* in the Library (above) and
*Choose…* beside *Folder of hand-made mesh patches* under *For experts*,
ask the engine to open the platform's own dialog
(`POST /api/choose-folder`: the Finder's, the File Explorer's, zenity's or kdialog's), starting in
the folder typed or detected. The folder chosen fills the field, as if typed (Settings still saves
with *Save*); a cancel changes nothing; a system without a dialog says to type the path (toast).

**Saved settings reach the Plan** (user report 2026-09-13: a preset saved in Settings left the
Plan at its old level): after *Save*, the Plan takes the saved source and detail level and drops
an estimate made with the previous settings. It also reads `GET /api/status` again before saying
*Saved* (the X-Plane folder may have changed: the *Detected* line of Settings, step 3's notice and
the status bar follow) and drops a refusal step 3 still showed. The folder field's hint "empty =
the detected folder" shows only when a folder was detected. Leaving Settings with answers not
saved says so in a toast; nothing is saved by leaving.

## 3. Mock mode

`index.html?mock=1` makes the API client read `static/mock/<name>.json` instead of
`/api/...`. `osxp serve --mock` opens that address, which is the way to reach it without typing
one. The switch is read from the query and from a query written after the route (`#plan?mock=1`,
which silently showed the real engine before), and `mock`, `mock=true`, `mock=yes` and `mock=on`
all mean `mock=1` (`pageParams`, `isOn`; a user, 2026-09-18). An option written after the route is
kept as the route changes (`HASH_OPTIONS`), so moving to another screen and reloading stays in the
mock rather than landing on the real engine. Airports are filtered client-side from `mock/airports.json`. This is the development
and visual-test harness: it must exercise every rendering path, and the mock files must satisfy
the interface contract so that they double as fixtures for the API chantier.

Jobs in mock mode run like the engine's, whether a page listens or not (`app.js`, "mock jobs"):
`POST /api/jobs` starts one, its nodes those of the one tile of `mock/job.json` (the engine's state
of a job that just started, each row with its `weight_s`) renamed for each tile. Its journal gets
the entries of `mock/job_events.json` (`osxp-mock-journal-1`: journal entries in the engine's flat
shape, `weight_s` included, with a `delay_ms`, `{tile}` and `{level}` filled in, `seq` and `ts`
added when written) and a `stats` entry every second (the whole job's progress, never decreasing,
elapsed time, a range once there is something to go by, the phase). The run: map data first, one
tile after the other, the first tile of a job of several already having it (its OSM row turns into a
hit, without `started`); then the tiles build side by side, each with a cache hit (`xp12`); the last
tile of a job of several misses a texture (`TEX_MISSING`, a `retry` action), so its pack and install
are skipped (entries marked `only: "failing"`, the others `only: "ok"`). `GET /api/jobs/{id}`
answers the engine's state from the nodes (`Job.state()`; the stages computed by the page's own
`stepTotals`, which the tests hold to the engine's rules, and turned `skipped` or `cancelled` once
the job ended), `GET /api/jobs` the summaries before `mock/jobs.json`; the stream replays the
journal, then follows it. *Stop* ends the running nodes with `SYS_CANCELLED`, then the job
(`cancelled`, no report); a second *Stop* answers `409 SYS_BUSY`, like a build while one runs.
*Retry* starts a new job with the same tiles where every node the job before built comes back as a
hit (`started`, then `done` with `hit: true`), and the missing texture does not happen again.
`mock/job_done.json` is a real 6-tile build (BI ZL16): its journal replayed through the engine's
`Job` gave the tiles, stages, rows (with `weight_s`), errors and stats; the report and decisions are
the real job's (trimmed: no traceback, short keys): 3 tiles installed, 3 missing a texture with
their pack and install skipped; `mock/jobs.json` lists it. `?mock=1&speed=10` runs the mock builds
ten times faster (the tests use 40).

Library in mock mode: `mock/library.json` has the engine's row shape (`kind`, `size_bytes`,
`present`, `built_by` `osxp` or `ortho4xp`, in the engine's order): three OrthoStudio XP
tiles, each with its row of the shared overlays pack, one of them installed and one with its files
missing, and two Ortho4XP imports, one installed; `mock/status.json` `library_count` is the number
of tiles, and the mock's `GET /api/status` counts them again once the library changed. `install` and
`uninstall` flip `installed` (the OrthoStudio XP overlay rows follow: the shared pack stays in
X-Plane while one OrthoStudio XP tile is). The three changes find the row by name and `path` like
the engine (`422 SYS_WORKING_DIR_INVALID` for a path the library does not have) and answer
`409 XP_RUNNING` while X-Plane runs, a delete of a tile X-Plane does not show included. `delete`
answers `409 SYS_BUSY` while a mock build runs and `409 SYS_PACK_NOT_OSXP` for an Ortho4XP pack;
otherwise it removes the tile's rows (its overlay row included) and answers `osxp-delete-1` with a
`freed_bytes` (0 when the files were already missing) and `warning: null`. Error paths:
`?mock=1&fail=xplane` says that X-Plane is running; `fail=no-xplane` knows no X-Plane folder
(status `detected: false`, the doctor's `xplane` check a warning, the settings' `xplane_dir` null)
and refuses `POST /api/plan` and `POST /api/jobs` with 422 `XP_DIR_NOT_FOUND`, until *Save* sends
a folder, which the status then gives as detected; `fail=no-data-disk` keeps the data folder on a
disk that is not plugged in (`/Volumes/SSD/OrthoStudio`: the status's `data_dir.present` false, the
settings' `data_dir` that folder) and refuses `POST /api/plan` and `POST /api/jobs` with 422
`CFG_DATA_DIR_MISSING` until *Save* sends another folder; in every mode, saving a data folder
whose name says exFAT or FAT32 is refused with 422 `CFG_DATA_DIR_INVALID` (`why: links`), and
changing it while a mock build runs with 409 `SYS_BUSY`; `fail=busy` refuses every delete as if a
build were running; `fail=clean` deletes the tile but answers a `warning` (and `freed_bytes` 0), as
when the cache space could not be freed; `fail=slow-status` answers the status after 6 s, as it
came on users' Windows (2.5, *The first screen*). `GET /api/sizes` answers the sizes the status
used to carry.

Map and zones in mock mode: the base layer is the neutral canvas grid (no request);
`GET /api/zones` answers `mock/zones.json` (three zones: LFML and LSGG at level 18, the
Calanques region at 17 with the `Arc` source over `+43+005` and `+43+006`; a `revision` and
empty `problems`); `PUT /api/zones` answers `409` `ZONE_CONFLICT` unless `If-Match` is the
current revision in quotes, then validates the document with `geo.js` `validateZonesDocument`
(the format rules of `map-zones.md` 3: ids, names, levels, source maximum, 3 to 2000 vertices,
ranges, simple ring) and keeps it in memory with a new revision, or answers `422`
`{"error": {"code": "ZONE_INVALID", "message", "context": {zone, reason}}}`. `POST /api/plan`
and `POST /api/jobs` refuse invalid request zones the same way; the plan adds the zones'
textures to the totals with the page's counting rules and gives the disk verdict (`needed_gb`
= images + downloads, `ok` when free > 2 × needed, `SYS_DISK_FULL` otherwise). Error paths:
`?mock=1&fail=zones` refuses every PUT; `fail=zone-conflict` changes the zones behind the
page's back (the first zone renamed "… (edited elsewhere)") before its first save, which gets
the `409`; `fail=zone-problem` adds a zone above its source's maximum ("Étang de Berre", level
19 with `USGS`) named by a problem, and a problem naming no zone (an unreadable entry);
`fail=disk` gives an estimate with 1.5 × the space needed free.

## 4. Acceptance tests (`tests/test_ui_static.py`)

Served through `FastAPI` + `StaticFiles` with `httpx.AsyncClient(transport=ASGITransport)`:

1. `GET /` returns `index.html`; every `src`/`href` it references exists on disk and is
   served with 200.
2. Every `t('…')` / `t("…")` key in `app.js` and every `data-i18n="…"` in `index.html` exists
   in both `fr` and `en` (the dictionary is parsed by evaluating `i18n.js` with `node`, so
   the test skips when `node` is missing); both languages have the same key set.
3. No `http://` or `https://` URL appears in `index.html`, `app.js`, `map.js`, `geo.js`,
   `i18n.js`, `styles.css` (mock files may carry provider attribution text but no URL
   either); Leaflet's own files are not checked, and `map.js` disables its attribution prefix.
4. `node --check` passes on every top-level `.js` file (skipped without `node`).
5. Every mock JSON parses and has the top-level keys of the contract (`status`, `providers`,
   `plan`, `job`, `library`, `settings`, `settings_schema`, `zones`).
6. `ROLE_STEP` of `app.js` equals the engine's `ROLE_STAGE`; `stepOfNode` maps a role, the last
   segment of an id and the rule names of the P2b contract, and nothing for Object's own keys.
7. The only scripts `index.html` carries are `static/vendor/leaflet/leaflet.js` then the module
   `static/app.js`, the only stylesheets `static/vendor/leaflet/leaflet.css` then
   `static/styles.css` (decision M1; MapLibre is added by `map.js` when the street map is asked
   for, never before); every literal `t()` key of `app.js`, `map.js`, `geo.js` exists in both
   languages and none of them calls `t()` with a dynamic key.
8. `mock/zones.json` follows the `osxp-zones-1` format (with a 64-hex `revision` and empty
   `problems`, as GET answers it), and `validateZonesDocument` accepts it
   and refuses a zone above its source's maximum and a crossing ring.
9. `geo.js` against the engine (`orthostudio.imagery.grid`): the Ctrl+click square is
   `texture_bbox(texture_at(...))` within 1e-9° at six points (both hemispheres, levels 12 to
   20); the snapped point is Ortho4XP's `newPointGrid` corner; a tile's texture count is
   `len(textures_covering(...))`; a texture zone overlaps exactly one texture; tiles touched,
   simple rings, the insertion rule and zone ids behave as section 2.1 says.
10. Zones persistence and engine answers, run under `node` against the page's own functions:
    `readZonesDocument` keeps a zone with a problem, marks it, keeps the other problems for the
    notice and makes ids unique; `ifMatch` quotes the revision (`""` included, no header without
    one); `keepaliveAllowed` stops below 60 000 bytes of UTF-8; `map.js` sends every save with
    `ifMatch(zs.revision)` and `keepalive`, handles `ZONE_CONFLICT`, listens to
    `visibilitychange` and `beforeunload` and calls no `fetch` of its own; `planRequest` always
    carries `zones`; `codeWords` prefers the engine's message for `ZONE_*` codes; `diskVerdict`
    follows `needed_gb` and `ok` (free > 2 × needed when absent), and `renderPlanPanel` reads no
    `dds_gb`.
11. Works (section 2.2), under `node`: `mock/job_events.json` holds flat journal entries (the
    fields of each event with `weight_s`, 0 for a hit or a skip, roles on their stage, no `step`,
    `node_id` or `data`), `job.json` and `job_done.json` the engine's state and `jobs.json` its
    summary; `applyEvent` reads a trimmed real journal (stages, nodes, `stats.stats`, an OSM hit, a
    failure and the node skipped because of it, one error card), skips what the state already
    reflects and still reads the P2b fields; `stepTotals` gives, for rows built with the engine's
    `_NodeState` and sent as its `to_dict`, the status and fraction of the engine's `stage_status`
    and `weighted_progress`; a three-tile build driven through the engine's `Job` (reused OSM row,
    downloads, the graph declared, hits, a missing texture): the page's steps equal the engine's
    state after every entry without any read, and no bar that shows a share of its step moves back
    whether the reads arrive at once, late (entries applied again to them) or ahead of the stream;
    the phase-data hint names the OSM layers (`orthostudio.sources.osm.LAYERS`);
    `normalizeJob` replaces the steps with the stages of a read, and a running step's bar does not
    move back; `jobProgress`, `jobElapsed` and `etaRange` with a current and an older engine;
    `logDelta` and the rendering code (the log built once, appended to, following only at its
    end; events drawn through the throttle); `reportTotals` and `decisionCounts` count each tile
    once on `job_done.json`, and a second pass that recovered nothing keeps both of its rows; a
    3-tile mock build (`speed=40`) streams dense `seq`, whole-job stats from phase `data` to
    `build`, weights, the OSM hit and downloads, a hit per tile and the failure (the mock's stages
    use the page's rules, so no comparison of the two is claimed); a retry reuses what was built
    and *Stop* cancels the running node.

## 5. Wanted differences from Ortho4XP

- No per-tile configuration window, no "step 1/2/2.5/3" buttons: one *Build* action, the
  engine decides what to redo from the store.
- Parameters shown by level with units and hints; the 18 dropped parameters of PLAN §5 do not
  appear.
- Errors are cards with a code and an action, never a console line.
- The page shows the cost before the build (two cost centres) rather than after, and an
  approximate size as soon as tiles or zones are chosen.
- Zones are drawn from visible buttons with a banner saying what to do; Ortho4XP's Ctrl/Shift
  gestures remain as shortcuts. One zone may cover several tiles (a region) instead of one
  `zone_list` per tile, and its priority is its place in a list the user reorders, instead of
  Ortho4XP's sort by zoom level (which only decides where a new zone is inserted).

## 6. Out of scope (later phases)

GeoJSON import/export from the page (the CLI reads GeoJSON zones, `map-zones.md` 5), editing
the vertices of an existing zone (delete and redraw), undo, quick-preview mode (P5);
pause/resume and the "continue offline" Overpass button (engine support first); quota and
purge by region (P3); a redesign of the Settings screen and of the essential settings form
(a later package).
