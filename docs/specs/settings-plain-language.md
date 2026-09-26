# Settings in plain language: what each setting really does

Status: research written on 2026-09-13 before any wording or code, **implemented the same day** in
the Settings screen (`src/orthostudio/ui/settings.js`, `docs/specs/ui.md` 2.4), with these
differences: presets set only the answers they list and leave the imagery source, the relief, the
overlays and the expert settings alone; a question on roads, forests and buildings was added
(`essential.overlays`, for simHeaven X-World users); the X-Plane folder is shown as detected with a
field for another one; "Removed / automatic" settings stay in the model and appear only when set by
hand (`mesh_zl` is not derived by the engine: it stays 19). Scope: the 45 leaves of
`src/orthostudio/config/models.py` at the time (12 essential, 14 advanced, 19 expert). The map
(`region`, P5) is out of scope.

Why: the user flies X-Plane, has used Ortho4XP and finds its settings incomprehensible ("sand,
land, lakes, radius... nobody understands them"). The page still shows Ortho4XP's names and
Ortho4XP's help texts verbatim (`config/hints.py`). Several of those texts are vague or wrong, so
the real effect of every setting is established here from the code first.

Conventions:

- `Ortho4XP X.py:n` is `../Ortho4XP/src/X.py`, line n (commit `26ec00a`).
  `Ortho4XP T4XP.c:n` is its `Utils/src/Triangle4XP.c`.
- `OrthoStudio XP a/b.py:n` is `src/orthostudio/a/b.py` in this repository (HEAD at the commit
  "Housekeeping: osxp clean, osxp uninstall..." plus the working tree of 2026-09-13). `T4XP:n` is
  `native/triangle4xp/Triangle4XP.c`, OrthoStudio XP's copy of the same program.
- **Measured**: a figure from `docs/benchmarks/` (tile `+43+005` Marseille unless stated, M4 Pro,
  Bing). **Computed**: produced for this document by calling OrthoStudio XP's own functions on the
  frozen fixtures, without building anything (section 9). **Estimate**: arithmetic on those, said
  each time.
- The build path assumed is the default one, which the web page also runs: OrthoStudio XP's own
  stages, relief from X-Plane 12 (ADR 0007, ADR 0008, ADR 0009).
- Recommendation per setting: **Shown** (a question in plain words), **Folded** (under "For
  experts", with a plain label), **Removed / automatic** (not offered; OrthoStudio XP decides).

## 0. Summary

| Recommendation | Count | Settings |
|---|---:|---|
| Shown | 10 | provider, zoom level, airports (mode), coast profile, coast width, water near the coast, relief source, relief file and its holes (both only when a file is chosen), photo on lakes and rivers |
| Folded | 30 | everything else that has a visible effect or a real use |
| Removed / automatic | 5 | `xplane_dir` (detected), `ratio_bathy` (merged into the distance masks), `imprint_masks_to_dds` (always on), `mesh_zl` (derived), `masks_custom_extent` (broken until P4b) |

The questions of the essential page, in order (the region comes first, from the map):

1. Whose aerial photos do you want on the ground?
2. How much detail do you want on the ground?
3. More detail around airports?
4. How should the coast fade into the sea?
5. How far out to sea should the photo reach?
6. Which water near the coast?
7. How much of the photo on lakes and rivers?
8. Where should the terrain heights come from? (then: which file? are there holes in it?)

Findings that change what the page may say (details in section 6):

- `ratio_bathy` does nothing between 0.1 and 1 unless distance masks are built, which they are
  not by default.
- `ratio_water` is not a linear transparency: it picks a row of a non-linear ramp; at the
  default 25 % the lakes probably keep about 58 % of the photo, at 50 % only about 18 %.
- `rocks` drops the photo to about 57 % right at the shoreline and reaches zero at about
  1.7 × the width; `sand` starts at about 93 % and is gone at about 0.95 × the width.
- `XP12` water is silently ignored when `imprint_masks_to_dds` is off, and it only changes the
  masked sea: rivers and lakes under `max_area` are drawn the same way in both modes.
- A relief file that does not cover the tile is stretched from its edge, not set to 0 m.
- `mask_zl` saves no memory with the default imprinted masks, and a texture below `mask_zl`
  loses the photo over its sea without any message.
- `apt_curv_tol` applies whether smaller or larger than `curvature_tol`; the hint says "if
  smaller".
- Nothing refuses an airport zoom level above `mesh_zl` (map zones are refused above it by the
  P5 work in progress, `zones.py:550-567`).

### 0.1 Index of the 45 settings

| # | OrthoStudio XP key | Ortho4XP name | Default | Recommendation | Plain label |
|---:|---|---|---|---|---|
| 1 | `essential.provider` | `default_website` | `BI` | Shown | Whose aerial photos |
| 2 | `essential.zoom_level` | `default_zl` | 16 | Shown | Detail on the ground |
| 3 | `essential.airports.mode` | `cover_airports_with_highres` | `off` | Shown | More detail around airports |
| 4 | `essential.airports.zoom_level` | `cover_zl` | 18 | Folded | Detail level at airports |
| 5 | `essential.airports.extent_km` | `cover_extent` | 1 km | Folded | Margin around airports |
| 6 | `essential.coast_transition.profile` | `masking_mode` | `sand` | Shown (`3steps` folded) | How the coast fades into the sea |
| 7 | `essential.coast_transition.width_m` | `masks_width` | 100 m | Shown (three choices) | How far out to sea the photo reaches |
| 8 | `essential.water_rendering` | `water_tech` | `XP11 + bathy` | Shown | Which water near the coast |
| 9 | `essential.relief.source` | `custom_dem` (empty) | `auto` | Shown | Where terrain heights come from |
| 10 | `essential.relief.file` | `custom_dem` | `""` | Shown when a file is chosen | Elevation file |
| 11 | `essential.relief.fill_nodata` | `fill_nodata` | `nearest` | Shown when a file is chosen | Holes in the file |
| 12 | `essential.xplane_dir` | `custom_scenery_dir` (its parent) | detected | Automatic | X-Plane 12 folder |
| 13 | `advanced.curvature_tol` | `curvature_tol` | 2 | Folded | Terrain detail |
| 14 | `advanced.limit_tris` | `limit_tris` | 3 M | Folded | Maximum triangles per tile |
| 15 | `advanced.road_level` | `road_level` | 1 | Folded | Flatten the ground under roads |
| 16 | `advanced.min_area` | `min_area` | 0.001 km² | Folded | Smallest pond drawn as water |
| 17 | `advanced.max_area` | `max_area` | 200 km² | Folded, listed in the plan | Lakes that look like the sea |
| 18 | `advanced.sea_smoothing_mode` | `sea_smoothing_mode` | `zero` | Folded | Sea level at the shoreline |
| 19 | `advanced.water_smoothing` | `water_smoothing` | 10 | Folded | Flatten lakes and rivers |
| 20 | `advanced.apt_smoothing_pix` | `apt_smoothing_pix` | 8 | Folded | Smooth the ground of airports |
| 21 | `advanced.ratio_water_pct` | `ratio_water` (× 100) | 25 % | Shown | Photo on lakes and rivers |
| 22 | `advanced.ratio_bathy` | `ratio_bathy` | 1.0 | Removed (merged into #40) | — |
| 23 | `advanced.use_masks_for_inland` | `use_masks_for_inland` | false | Folded | Fade lakes and rivers like the sea |
| 24 | `advanced.imprint_masks_to_dds` | `imprint_masks_to_dds` | true | Removed (always on) | — |
| 25 | `advanced.terrain_casts_shadows` | `terrain_casts_shadows` | true | Folded | Hills cast shadows |
| 26 | `advanced.overlay_lod_km` | `overlay_lod` (÷ 1000) | 25 km | Folded | Photo over water visible up to |
| 27 | `expert.mesh_zl` | `mesh_zl` | 19 | Automatic | — |
| 28 | `expert.mask_zl` | `mask_zl` | 14 | Folded, guarded | Coast fade precision |
| 29 | `expert.apt_curv_tol` | `apt_curv_tol` | 0.5 | Folded | Terrain detail around airports |
| 30 | `expert.apt_curv_ext` | `apt_curv_ext` | 0.5 km | Folded | ... its margin |
| 31 | `expert.coast_curv_tol` | `coast_curv_tol` | 1 | Folded | Terrain detail along the coast |
| 32 | `expert.coast_curv_ext` | `coast_curv_ext` | 0.5 km | Folded | ... its margin |
| 33 | `expert.min_angle` | `min_angle` | 10° | Folded | Minimum triangle angle |
| 34 | `expert.road_banking_limit` | `road_banking_limit` | 0.5 m | Folded | Side slope that triggers flattening |
| 35 | `expert.lane_width` | `lane_width` | 4 m | Folded | Half-width of the flattened strip |
| 36 | `expert.max_levelled_segs` | `max_levelled_segs` | 200 000 | Folded | Road points flattened per layer |
| 37 | `expert.water_simplification` | `water_simplification` | 0 m | Folded | Simplify lake and river outlines |
| 38 | `expert.masks_use_dem_too` | `masks_use_DEM_too` | false | Folded (with a relief file) | Use the relief for the coast fade |
| 39 | `expert.masks_custom_extent` | `masks_custom_extent` | `""` | Removed (broken) | — |
| 40 | `expert.distance_masks_too` | `distance_masks_too` | false | Folded | Shallow water near the shore |
| 41 | `expert.sea_texture_blur` | `sea_texture_blur` | 0 | Folded | Blur the photo over the sea |
| 42 | `expert.normal_map_strength` | `normal_map_strength` | 1 | Folded | Sun shading of slopes |
| 43 | `expert.use_decal_on_terrain` | `use_decal_on_terrain` | false | Folded | Fine ground detail at very low height (decals) |
| 43b | `expert.decal_on_sea` | – | false | Folded | Fine ground detail on the sea too |
| 43c | `expert.decal` | none | `maquify_2_green_key.dcl` | Folded (list) | Ground decal |
| 44 | `expert.ovl_exclude_pol` | `ovl_exclude_pol` | `[0]` | Folded (checklist) | X-Plane objects to remove |
| 45 | `expert.ovl_exclude_net` | `ovl_exclude_net` | `[]` | Folded | X-Plane networks to remove |

## 1. What a tile is made of

Every explanation below relies on these facts, all read in the code.

1. **Land** triangles show the photo, opaque (`.ter` `NO_ALPHA`: Ortho4XP `O4_DSF_Utils.py:346-349`,
   OrthoStudio XP `textures/ter.py:147`).
2. **Inland water** (lakes under `max_area`, rivers, docks) is drawn twice: X-Plane's water, and
   over it the photo at a fixed transparency chosen by `ratio_water` (`.ter`
   `BORDER_TEX water_transition.png`: Ortho4XP `O4_DSF_Utils.py:308-317, 978-984, 1007-1040`;
   OrthoStudio XP `textures/ter.py:138-139`, `dsf/encode.py:382-385`). This does not depend
   on `water_rendering`.
3. **Sea** triangles (OSM `natural=coastline`) and lakes above `max_area` get a **mask**: a grey
   image computed from the mesh, saying how much of the photo stays visible, from opaque at the
   shoreline to nothing at about the chosen width, 100 m by default (Ortho4XP
   `O4_Mask_Utils.py:648-811`, OrthoStudio XP `masks/profiles.py`). The fade exists only on the
   water side of the OSM coastline: land stays opaque. When the mask of a texture never exceeds
   30/255, the sea triangles of that texture are plain X-Plane water, and the texture is not
   downloaded unless land needs it (Ortho4XP `O4_Mask_Utils.py:38-60`, `O4_DSF_Utils.py:676-768`;
   OrthoStudio XP `dsf/encode.py:206-222, 270-276`). The mask is written into the texture's alpha
   channel, which doubles its size: DXT5 22.4 MB instead of DXT1 11.2 MB (OrthoStudio
   XP `estimate.py:51-53`).
4. The masked sea is drawn either as the photo **over** X-Plane's water (`XP11 + bathy`) or
   **as** X-Plane 12 water coloured by the photo (`XP12`) (Ortho4XP `O4_DSF_Utils.py:702-706`).
5. The **mesh** follows the relief (Triangle4XP adds triangles where the terrain bends), is
   flattened under runways, taxiways and, from `road_level`, roads, and its water is levelled
   (Ortho4XP `O4_Mesh_Utils.py:230-324`).
6. Forests, autogen, roads and power lines come back as a separate **overlay pack**
   (`yOrthoStudio_Overlays`) extracted from X-Plane's Global Scenery; only the polygon and road
   types listed in `ovl_exclude_*` are left out (`docs/specs/overlays.md`).

### 1.1 Reference numbers

Ground size from `156 543 m × cos(latitude) / 2^ZL` (OrthoStudio XP `ui/geo.js:38-41`), given at
45°. Texture counts of `+43+005`: "DSF list" is what the build really uses (measured); "map" counts
every texture of the tile including open sea (computed, section 9).

| | ZL14 | ZL15 | ZL16 | ZL17 | ZL18 |
|---|---|---|---|---|---|
| Name on the page (`ui/i18n.js`) | Low | Medium | Standard | Sharp | Very sharp |
| Ground per pixel at 45° | 6.8 m | 3.4 m | 1.7 m | 0.84 m | 0.42 m |
| One texture (4096 px) at 45° | 28 km | 14 km | 6.9 km | 3.5 km | 1.7 km |
| Textures, DSF list (measured) | 17 (7 DXT5) | 56 | 179 (27 DXT5) | ≈ 640 (estimate) | |
| Textures, map (computed) | | 63 | 221 | 792 | |
| DDS on disk | 0.27 GB | ≈ 0.7 GB (estimate) | 2.30 GB | ≈ 8 GB (estimate) | |
| Photo download | 63 MB, 4 352 tiles | 14 336 tiles | 741 MB, 45 824 tiles | ≈ 3 GB (estimate) | |
| Texture stage, empty photo cache | 4.4-5.2 s | 15.0 s | 30.5-34.6 s | ≈ 2-2.5 min (estimate) | |

Sources: `baseline-ortho4xp.md`, `p1-imagery.md`, `p2-build.md` sections 1-3, `p3-native.md`
sections 1-2. The rest of a native tile (elevation, coastline, vectors, mesh, masks, DSF,
overlay) takes 13.2 s on `+43+005` with OSM cached (`p4-airports.md` 4.1c); an uncached tile
adds its OSM download, 24.6 s on `+43+004` (`p3-native.md` 6). The DSF does not depend on the
zoom level: 32.4 MB on `+43+005` (1.2 M triangles), 26.7 MB on `+50+008`, 14.1 MB on
`+39+003` (`p4-airports.md` 7).

### 1.2 The three coast fades, computed

`orthostudio.masks.profiles.blur_mask` (a byte-exact port of Ortho4XP for `sand` and `rocks`, a
measured approximation for `3steps`, `masks-build.md` 6.3) applied to a straight synthetic coastline
at 43.5° N, `mask_zl` 14 (6.93 m per mask pixel), `ratio_water` 25 %, then Ortho4XP's last rule
(land forced back to opaque). Value = opacity of the photo over the sea (100 % = only the photo, 0 =
only X-Plane's water):

| Profile, width | at the shore | 15 m | 30 m | 50 m | 75 m | 100 m | 150 m | 200 m | 300 m | below 50 % from | 0 from |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `sand` 50 m | 85 % | 41 % | 12 % | 0 | 0 | 0 | 0 | 0 | 0 | 17 m | 45 m |
| `sand` 100 m | 93 % | 67 % | 45 % | 21 % | 5 % | 0 | 0 | 0 | 0 | 31 m | 94 m |
| `sand` 200 m | 96 % | 82 % | 70 % | 53 % | 38 % | 22 % | 5 % | 0 | 0 | 59 m | 170 m |
| `rocks` 50 m | 59 % | 38 % | 24 % | 12 % | 3 % | 0 | 0 | 0 | 0 | 10 m | 94 m |
| `rocks` 100 m | 57 % | 45 % | 35 % | 25 % | 18 % | 11 % | 2 % | 0 | 0 | 17 m | 170 m |
| `rocks` 200 m | 58 % | 51 % | 46 % | 38 % | 32 % | 26 % | 18 % | 11 % | 2 % | 24 m | 336 m |
| `3steps` 100/200/100 m | 82 % | 68 % | 56 % | 44 % | 40 % | 39 % | 39 % | 39 % | 13 % | 45 m | 350 m |
| `3steps` 50/100/50 m | 71 % | 53 % | 43 % | 39 % | 39 % | 38 % | 8 % | 0 | 0 | 24 m | 184 m |

A real coast is not straight, and in the tile the mask is scaled up (4× on a ZL16 texture,
bicubic) before it becomes the alpha channel; the shape of the curves is what matters.

## 2. Essential settings (12)

Each entry: identity line (OrthoStudio XP key · Ortho4XP name · default · allowed values), then
**Does**, **In X-Plane**, **Cost**, **Recommendation**, and for shown settings the **Question** in
English and French (recommended option first and in bold, then what it changes), and **Picture**
when one explains better than words.

### 2.1 `essential.provider`

`default_website` · `BI` · a code of `imagery/registry.toml`: 12 providers, `BI` Bing, `Arc`
and `Arc@` Esri, national ones for Luxembourg, the Netherlands (4), Spain, Japan, the USA.

- **Does.** The provider of the base zone, the one that covers the whole tile; a zone drawn on the
  map can override it locally (Ortho4XP `O4_DSF_Utils.py:183-206`; OrthoStudio XP
  `api/specs.py:166-181`, `pipeline/build.py:421`, `dsf/zones.py:152-170`). The request is refused
  when the zoom level is above the provider's `max_zl` (19 for Bing, "assumed" in the registry).
- **In X-Plane.** The photos themselves: colour balance, date and season, haze or clouds in the
  pictures, sharpness at a given zoom level. National providers only cover their country
  (`extent_bounds`). OrthoStudio XP has no colour filters and no soft blending between two providers
  yet (`.flt` and `.comb` are P4b), so a change of provider inside a region leaves a seam.
- **Cost.** Download speed depends on the host: `max_in_flight` as measured on 2026-09-15 (Bing
  and Esri World Imagery about 1 100 requests/s, the line's limit; Esri Clarity 520; Spain 580;
  USGS 280; the Netherlands 180; Luxembourg 130; Japan 100; `docs/benchmarks/network.md` 7). A change keeps the vectors, mesh and masks and rebuilds the
  DSF and every texture (new download).
- **Recommendation. Shown.** A real choice with a visible result, and the page can show the
  photos themselves.
- **Question.**
  - EN: *Whose aerial photos do you want on the ground?* — **Bing, worldwide (recommended)** ·
    Esri World Imagery, worldwide · The national photos of this country (listed only where they
    exist). *Changes the photos themselves: colours, date, season and sharpness differ from one
    source to another.*
  - FR : *Quelles photos aériennes voulez-vous au sol ?* — **Bing, monde entier (conseillé)** ·
    Esri World Imagery, monde entier · Les photos nationales de ce pays (proposées seulement là
    où elles existent). *Change les photos elles-mêmes : couleurs, date de prise de vue, saison
    et netteté varient d'une source à l'autre.*
- **Picture.** The same 1 km square seen through two or three providers at ZL16, cut from
  `GET /api/map/{provider}/{z}/{x}/{y}` (the map proxy, `map-zones.md` M2): thumbnails under the
  options.

### 2.2 `essential.zoom_level`

`default_zl` · 16 · 10 to 20, refused above the provider's `max_zl` (`api/specs.py:176-181`).

- **Does.** The zoom level of every texture of the base zone; a texture is 16 × 16 web-mercator
  tiles of that level, 4096 px (Ortho4XP `O4_DSF_Utils.py:183-224`; OrthoStudio XP
  `dsf/zones.py:152-170, 233-245`).
- **In X-Plane.** How crisp fields, roads, roofs and runways look when flying low: 1.7 m per pixel
  at ZL16, 0.84 m at ZL17 (45°). More textures are loaded around the aircraft at higher levels
  (memory and loading not measured). The terrain shape does not change (same DSF size at ZL14 and
  ZL16). Below ZL14, the default `mask_zl`, the sea gets no photo at all: plain X-Plane water from
  the coastline, with a hard edge (Ortho4XP `O4_Mask_Utils.py:22-26, 38-41`; OrthoStudio
  XP `dsf/encode.py:215-216`).
- **Cost.** About × 3.2 to × 4 per level in textures, disk, download and texture time (table
  1.1). Changing it later keeps vectors, mesh and masks: ZL14 to ZL16 took 33 s (measured,
  `p2-build.md` 2).
- **Recommendation. Shown**, with the names the map already uses (`detail.*` in `ui/i18n.js`)
  and the ground size computed at the tile's latitude.
- **Question.**
  - EN: *How much detail do you want on the ground?* — **Standard, about 1.7 m per pixel · ZL16
    (recommended)** · Sharp, about 0.8 m per pixel · ZL17, about 4 times the disk space,
    download and time · Medium, about 3.4 m per pixel · ZL15, about a quarter. *Changes how
    crisp the ground looks when you fly low; each step up multiplies disk space, download and
    build time by about 4.*
  - FR : *Quel niveau de détail voulez-vous au sol ?* — **Standard, environ 1,7 m par pixel ·
    ZL16 (conseillé)** · Net, environ 0,8 m par pixel · ZL17, environ 4 fois plus de disque, de
    téléchargement et de temps · Moyen, environ 3,4 m par pixel · ZL15, environ 4 fois moins.
    *Change la netteté du sol quand on vole bas ; chaque cran de plus multiplie par 4 environ
    la place sur le disque, le téléchargement et le temps de construction.*
- **Picture.** The same 300 m square (a village and a road) at ZL14, 15, 16 and 17, cut from the
  chunk store (`imagery/chunks.py`; ZL14 and ZL16 of `+43+005` were downloaded by the
  benchmarks), each captioned with its ground size and the GB per tile.

### 2.3 `essential.airports.mode`

`cover_airports_with_highres` (`False` / `True` / `ICAO` / `Existing`) · `off` · `off`, `on`,
`icao`, `existing`.

- **Does.** For each airport of the tile's OSM data, the bounding rectangle of its outline is
  widened by `extent_km`, rounded outwards to whole textures at `airports.zoom_level`, and every
  mesh cell inside gets at least that zoom level (Ortho4XP `O4_DSF_Utils.py:114-173, 216-218`;
  OrthoStudio XP `dsf/zones.py:178-200, 240-242`). An airport is an OSM element whose tag values
  include `aerodrome` or `airstrip`; it is dropped when its outline is under 5 000 m² or, without an
  outline, its runway under 2 500 m² (Ortho4XP `O4_Airport_Utils.py:19-42, 682-697`; OrthoStudio XP
  `airports_vec/discover.py:75-105`). `icao` keeps the airports with an OSM `icao` tag. `existing`
  (take the zoom levels of an older tile's `textures/`) is accepted by the settings model but
  refused at build time (`pipeline/build.py:754-765`).
- **In X-Plane.** Sharper runways, markings, aprons and surroundings for taxiing, take-off and
  short final. Airports come from OpenStreetMap, not from X-Plane's `apt.dat`: an airfield
  without an OSM aerodrome tag is not covered. Where the sharp rectangle ends, the sharpness
  changes (visibility of that step: question Q12).
- **Cost.** Computed with OrthoStudio XP's texture map on the three frozen tiles (extra textures on
  a ZL16 tile, DXT1 assumed, section 9). The ZL16 textures stay (neighbouring cells still use them):
  the cost adds.

  | Tile (airports, of which ICAO) | `icao` at ZL17 | `icao` at ZL18 | `on` at ZL18 |
  |---|---|---|---|
  | `+43+005` Marseille (18, 7) | +32 textures, +0.36 GB | +76, +0.85 GB | +130, +1.45 GB |
  | `+50+008` Frankfurt (32, 14) | +54, +0.60 GB | +134, +1.50 GB | +261, +2.92 GB |
  | `+39+003` Mallorca east (6, 2) | +8, +0.09 GB | +13, +0.15 GB | +29, +0.32 GB |

  About 0.1 GB per ICAO airport at ZL18, and about 4 MB of download per extra texture (256
  Bing tiles at the 16 kB measured at ZL16; estimate). The plan does not count them yet
  (section 8). A change rebuilds the DSF and downloads the new textures only.
- **Recommendation. Shown.** The recommended answer deviates from Ortho4XP, whose default is off:
  sharp airports are what a VFR pilot sees most closely, and the cost is bounded and shown.
- **Question.**
  - EN: *More detail around airports?* — **Around the main airports, those with an ICAO code:
    Very sharp · ZL18 (recommended)** · Around every airfield OpenStreetMap knows: Very sharp ·
    ZL18 · No, the same detail as the rest. *Adds a very sharp area of a few kilometres around
    each airport, for taxiing, take-off and landing; it costs extra disk space, about 0.1 GB
    per main airport.*
  - FR : *Plus de détail autour des aéroports ?* — **Autour des aéroports principaux, ceux qui
    ont un code OACI : Très net · ZL18 (conseillé)** · Autour de tous les terrains connus
    d'OpenStreetMap : Très net · ZL18 · Non, le même détail que le reste. *Ajoute une zone très
    nette de quelques kilomètres autour de chaque aéroport, pour le roulage, le décollage et
    l'atterrissage ; cela prend de la place en plus, environ 0,1 Go par aéroport principal.*
- **Picture.** The tile on the map with the texture grid and the rectangles that the cover
  upgrades (the same `texture_map` computation as the table), and a ZL16 against ZL18 crop of
  one runway threshold. Zones drawn on the map (P5 step 2) and this cover end in the same
  texture map; the page can draw both in one layer.

### 2.4 `essential.airports.zoom_level`

`cover_zl` · 18 · 14 to 20.

- **Does.** The zoom level of the airport rectangles; a cell whose zone is already higher keeps its
  own (maximum) (Ortho4XP `O4_DSF_Utils.py:147-164, 218`; OrthoStudio XP
  `dsf/zones.py:191-196, 242`).
- **In X-Plane.** 0.84 m per pixel at ZL17, 0.42 m at ZL18, 0.21 m at ZL19 (45°).
- **Cost.** About × 2.4 extra textures per level (computed, `+43+005`, `icao`, 1 km: ZL17 +32,
  ZL18 +76, ZL19 +209). Not checked against `mesh_zl` nor against the provider's `max_zl`
  (only the base zoom level is, `api/specs.py:176-181`): 20 is accepted with Bing and
  `mesh_zl` 19 (section 8).
- **Recommendation. Folded.** The airports question sets 18; an expert may choose 17 (lighter)
  or 19.
- **Expert label.** EN *Detail level at airports* · FR *Niveau de détail des aéroports*.

### 2.5 `essential.airports.extent_km`

`cover_extent` · 1.0 km · ≥ 0.

- **Does.** How much the airport's bounding rectangle is widened before it is rounded outwards to
  whole textures (Ortho4XP `O4_DSF_Utils.py:141-164`; OrthoStudio XP `dsf/zones.py:183-196`). It
  starts from the rectangle around the whole OSM outline, not from the runway.
- **In X-Plane.** The sharp area reaches further along the approach and around the field.
  Because of the rounding to textures of about 1.7 km at ZL18, a small change often changes
  nothing.
- **Cost.** Computed, `+43+005`, `icao`, ZL18: 0.5 km +56 textures, 1 km +76, 2 km +135.
- **Recommendation. Folded** (1 km).
- **Expert label.** EN *Margin around airports (km)* · FR *Marge autour des aéroports (km)*.

### 2.6 `essential.coast_transition.profile`

`masking_mode` · `sand` · `sand`, `rocks`, `3steps`.

- **Does.** The shape of the sea mask's fade (Ortho4XP `O4_Mask_Utils.py:648-811`; OrthoStudio XP
  `masks/profiles.py:215-245` for `sand` and `rocks`, byte-identical to Ortho4XP, and `:297-317` for
  `3steps`, 97.9-99.9 % of pixels equal, `masks-build.md` 6.3). `sand`: a smooth blur, then
  doubled and clipped. `rocks`: the water is first shrunk, blurred, then pushed through a steep
  curve. `3steps`: a fade to the grey of `ratio_water`, a band at that grey, then a fade to
  nothing. Numbers in section 1.2.
- **In X-Plane.** `sand`: the photo of the shallow water (sand bottom, reefs, harbour basins)
  fades gradually into X-Plane's water; about 93 % of the photo at the shoreline with 100 m.
  `rocks`: a clear step at the shoreline, where the photo drops to about 57 %, then a faint
  tail about 1.7 times as long as the width. `3steps`: a wide band of half-transparent photo
  (39 % at the default `ratio_water`), for lagoons and shallow bays (all three to be checked in
  X-Plane: Q13). The fade applies to the sea, to lakes above `max_area`, and to all inland
  water when `use_masks_for_inland` is on.
- **Cost.** No change on disk or download (the same coastal textures are DXT5 in all
  profiles). Masks of `+43+005` (7 files): `sand` 1.07 s, `rocks` 1.32 s, `3steps` 1.69 s; memory
  per worker 242 / 703 / 981 MB (measured, `masks-build.md` 6.1-6.2). A change rebuilds masks,
  DSF and coastal textures: 4.1 s for a width change (measured, `p3-native.md` 4).
- **Recommendation. Shown** with `sand` and `rocks`; `3steps` **folded**: it needs three widths
  (the model refuses a single one, `config/models.py:83-104`) and its band depends on
  `ratio_water`.
- **Question.**
  - EN: *How should the coast fade into the sea?* — **Gradually, like a sandy beach
    (recommended)** · With a clear edge, like a rocky shore. *Near the shore the aerial photo
    still shows on the water, then gives way to X-Plane's own water; this sets how that fade
    looks.*
  - FR : *Comment la côte doit-elle se fondre dans la mer ?* — **Progressivement, comme une plage
    de sable (conseillé)** · Avec un bord marqué, comme une côte rocheuse. *Près du rivage, la
    photo aérienne reste visible sur l'eau, puis laisse place à l'eau d'X-Plane ; ce choix règle
    l'allure de ce fondu.*
- **Picture.** Two parts. (1) A chart of photo opacity against distance offshore for the three
  profiles, computed exactly as in section 1.2. (2) The same 2 km of the Marseille coast from the
  ZL14 fixture textures, composited over a flat water colour with the three sets of reference
  masks kept in `fixtures/large/oracle/+43+005_zl14_BI/` (`masks/`, `masks_rocks/`,
  `masks_3steps/`). Caption it as a sketch: X-Plane's water is not a flat colour.

### 2.7 `essential.coast_transition.width_m`

`masks_width` · 100 m · ≥ 0; a list of three when the profile is `3steps`.

- **Does.** `sand`: blur over `int(width / pixel)` mask pixels (Ortho4XP `O4_Mask_Utils.py:659-679`;
  OrthoStudio XP `masks/profiles.py:82-84, 215-220`). `rocks`: a blur radius of `width / 2`, in mask
  pixels (`:661-662, 680-724`; OrthoStudio XP `:87-89, 228-245`). `3steps`: the three lengths. A
  mask pixel is 6.9 m at `mask_zl` 14 and 43.5° N, so widths are rounded to about 7 m, and a `sand`
  width under one pixel gives no fade at all (hard edge, no photo on the sea).
- **In X-Plane.** Computed (section 1.2): with `sand`, a width of 50 m leaves no photo beyond
  45 m, 100 m none beyond 94 m, 200 m none beyond 170 m (and still 53 % at 50 m offshore).
  `rocks` reaches zero at about 1.7 times the width, `3steps` at roughly the sum of its three
  lengths. Wider: more of the photo's shallow-water colours on the sea, and an OSM coastline
  that does not match the photo is hidden. Narrower: X-Plane's water (waves, reflections) comes
  closer to the shore.
- **Cost.** Nothing measurable on disk or download: at ZL14, 200 m gave the same 7 coastal
  textures and fetched no tile (measured, `p2-build.md` 3 run D). Rebuild 4.1 s.
- **Recommendation. Shown** as three choices; a free value, and the three `3steps` lengths,
  folded.
- **Question.**
  - EN: *How far out to sea should the photo reach?* — **About 100 m (recommended)** · About
    50 m (harbours, cliffs, deep water close to the shore) · About 200 m (shallow bays, lagoons,
    reefs). *Beyond that distance you only see X-Plane's water. Wider hides mismatches between
    the photo and the map's coastline; narrower brings X-Plane's waves closer to the shore.*
  - FR : *Jusqu'où, en mer, la photo doit-elle aller ?* — **Environ 100 m (conseillé)** ·
    Environ 50 m (ports, falaises, eau profonde près du bord) · Environ 200 m (baies peu
    profondes, lagunes, récifs). *Au-delà, on ne voit plus que l'eau d'X-Plane. Plus large, cela
    masque les écarts entre la photo et le trait de côte de la carte ; plus étroit, les vagues
    d'X-Plane arrivent plus près du rivage.*
- **Picture.** The three `sand` curves on the chart of 2.6, and the composite of 2.6 redone with
  `sand` masks rebuilt by `orthostudio.masks.build_masks` from `Data+43+005.mesh` at 50, 100 and
  200 m (about 1 s each).

### 2.8 `essential.water_rendering`

`water_tech` · `XP11 + bathy` · `XP11 + bathy`, `XP12`.

- **Does.** Only the masked sea terrains change (Ortho4XP `O4_DSF_Utils.py:702-706`; OrthoStudio XP
  `textures/ter.py:92-95`, `dsf/encode.py:281`). `XP11 + bathy`: each masked sea triangle is drawn
  as the photo, alpha = mask, as an overlay on top of X-Plane's water (`terrain_Water`), and that
  overlay is cut at `overlay_lod` (Ortho4XP `:791-804, 838-872, 1215`; OrthoStudio XP
  `dsf/encode.py:611-613, 459`). `XP12`: the same triangle is drawn once, as a terrain whose `.ter`
  says `WATER_COLOR_MASK` (the texture's alpha used as the water colour mask), with the depth ratio
  in its vertices, no separate water under it, and no distance cut (Ortho4XP
  `:305-307, 805-816, 839`; OrthoStudio XP `textures/ter.py:136-137`, `dsf/encode.py:379-381`).
  Inland water, sea without mask and every DDS are identical in both modes. With
  `imprint_masks_to_dds` off, `XP12` is silently the same as `XP11 + bathy` (Ortho4XP `:706`).
- **In X-Plane.** To be seen (Q1). From the code: `XP11 + bathy` keeps X-Plane's water whole
  under the photo, but the photo layer is not drawn beyond 25 km, so from altitude the coast
  becomes a hard photo/water edge; `XP12` lets X-Plane 12's water use the photo as its colour,
  at every distance.
- **Cost.** None: same textures; only the DSF and `.ter` files change (DSF node about 2 s).
- **Recommendation. Shown**, but the two options need the screenshots of Q1; until then keep
  `XP11 + bathy` preselected, the tested Ortho4XP default.
- **Question.**
  - EN: *Which water near the coast?* — **The photo laid over X-Plane's water, Ortho4XP's usual
    look (recommended)** · X-Plane 12's own water, coloured by the photo. *Changes only the sea,
    and very large lakes, close to the shore: how the photo's shallow-water colours mix with
    X-Plane's waves and reflections, and whether they are still drawn from far away.*
  - FR : *Quelle eau près des côtes ?* — **La photo posée sur l'eau d'X-Plane, l'aspect habituel
    d'Ortho4XP (conseillé)** · L'eau d'X-Plane 12, colorée par la photo. *Ne change que la mer, et
    les très grands lacs, près du rivage : la façon dont les couleurs de la photo se mêlent aux
    vagues et aux reflets d'X-Plane, et si elles restent visibles de loin.*
- **Picture.** Two X-Plane screenshots of the same coast, same time and weather (Q1). This one
  cannot be produced from OrthoStudio XP's data.

### 2.9 `essential.relief.source` and 2.10 `essential.relief.file`

`custom_dem` (empty = `auto`) · `auto` and `""` · `auto` or `file`; the path is required with
`file` (`config/models.py:107-120`).

- **Does.** `auto` leaves `custom_dem` empty, so the native elevation stage takes X-Plane 12's
  own relief: the `elevation` raster of the tile's Global Scenery DSF (1201 × 1201 posts, 3
  arc-seconds, sea at 0 m), refined to 3601 posts like a 3" `.hgt`, with the 8 neighbours for the
  borders and each border post averaged between the tiles that carry it (OrthoStudio XP
  `pipeline/build.py:1533-1570`, `dem/xplane.py:1-60`, ADR 0007). No Global Scenery DSF for the
  tile: the build is refused (`DEM_TILE_UNAVAILABLE`) rather than made flat. `file` puts the
  path in `custom_dem` (`config/overrides.py:80`): an `.hgt`, a `.raw`, or a GeoTIFF in EPSG 4326
  or 4269, read with Pillow (OrthoStudio XP `dem/raster.py:155-178, 224-263`; Ortho4XP needed GDAL,
  `O4_DEM_Utils.py:441-593`). An unreadable file is refused (`DEM_TILE_UNAVAILABLE`,
  `dem/dem.py:345-378`) where Ortho4XP built the tile flat. The file replaces X-Plane's relief for
  the whole tile. Where the file does not reach, positions are clamped to its edge, so the edge
  heights are stretched to the tile border, Q17 (Ortho4XP `O4_DEM_Utils.py:237-261`,
  `T4XP.c:15198-15204`; OrthoStudio XP `dem/dem.py:237-243`, `T4XP:14963-14967`). Ortho4XP's syntax
  `base;overlay` and `{latlon}` still works in the string (`dem/dem.py:80-98`), but X-Plane's
  relief cannot be the base of such a composite: its inputs are only declared when `custom_dem`
  is empty (`pipeline/build.py:1548`). Choosing a file does not start the `-r` refinement that
  `settings.md` 3 announces (section 6.3).
- **In X-Plane.** The shape of mountains, valleys, cliffs and embankments under the photos.
  X-Plane's relief is 90 m data: the tile matches the default scenery around it (within a few
  metres of AutoOrtho's and XPME's base packages, ADR 0007), but small landforms are missing. A
  detailed file (a 25 m national model, 1-5 m LIDAR) adds them, with more triangles; at its
  border the tile may no longer meet the neighbouring scenery.
- **Cost.** Nothing to download in either case. A detailed file makes the mesh denser, up to
  `limit_tris`. Replacing the content of the same file does not rebuild anything
  (`dem.md` 9.3).
- **Recommendation. Shown**; the file picker only when "my own file" is chosen.
- **Question.**
  - EN: *Where should the terrain heights come from?* — **X-Plane 12's own relief (recommended:
    it matches the scenery around, nothing to download)** · My own elevation file (GeoTIFF or
    HGT), for example a detailed national elevation model. Then *Which file?* with the note *It
    must cover the whole tile: beyond its edge the heights are stretched.* *Changes the shape of
    hills, valleys and cliffs under the photos.*
  - FR : *D'où doivent venir les altitudes du relief ?* — **Le relief d'X-Plane 12 (conseillé :
    il se raccorde au décor voisin, rien à télécharger)** · Mon propre fichier d'altitudes
    (GeoTIFF ou HGT), par exemple un modèle numérique de terrain national détaillé. Puis *Quel
    fichier ?* avec la note *Il doit couvrir toute la tuile : au-delà de son bord, les altitudes
    sont étirées.* *Change la forme des collines, des vallées et des falaises sous les photos.*
- **Picture.** Two hillshades of the raster the mesh reads (`Data<tile>.alt`, published by the
  vector stage), X-Plane's relief next to the file, shaded with a numpy gradient; optionally a
  wireframe of `mesh.npz` over the same valley.

### 2.11 `essential.relief.fill_nodata`

`fill_nodata` (true = `nearest`) · `nearest` · `nearest`, `zero`.

- **Does.** What becomes of the voids of the elevation raster. `nearest`: only when the raster has
  fewer than 10 000 void pixels, each void takes the highest of its 4 neighbours, 20 rounds at most,
  and whatever is left goes to 0 m; with 10 000 voids or more, every void goes to 0 m (Ortho4XP
  `O4_DEM_Utils.py:51-60, 866-907`; OrthoStudio XP `dem/dem.py:148-165`, `dem/raster.py:325-352`,
  with the event `DEM_VOIDS_FILLED_WITH_ZERO`). `zero`: every void goes to 0 m. X-Plane 12's relief
  has no voids (ADR 0007): with `auto` this setting does nothing.
- **In X-Plane.** A void left at 0 m is a pit, or a flat area at sea level, in the terrain; a
  filled void is a small plateau at the height around it.
- **Cost.** None.
- **Recommendation. Shown only with a file**, as a sub-question; hidden otherwise.
- **Question.**
  - EN: *Are there holes in your file?* — **Fill the small holes with the heights around them
    (recommended)** · Put every hole at sea level, 0 m (for files that leave the sea empty).
    *Only matters for files with missing values; large holes end up at sea level either way.*
  - FR : *Votre fichier a-t-il des trous ?* — **Boucher les petits trous avec les altitudes
    voisines (conseillé)** · Mettre tous les trous au niveau de la mer, 0 m (pour les fichiers
    qui laissent la mer vide). *Ne compte que pour les fichiers avec des valeurs manquantes ; les
    grands trous finissent au niveau de la mer dans les deux cas.*

### 2.12 `essential.xplane_dir`

`custom_scenery_dir` (OrthoStudio XP keeps its parent) · none (detected) · a folder.

- **Does.** The folder holding `Resources/` and `Custom Scenery/` (`install/xplane.py:79-83`).
  Empty: `$OSXP_XPLANE_DIR`, then X-Plane's own list of installations, then the usual folders
  (`install/xplane.py:98-121`). OrthoStudio XP installs the tiles there (link and
  `scenery_packs.ini`), and reads from it the Global Scenery (overlay pack, sea-level and depth
  rasters, default relief: `pipeline/build.py:212-237, 1533-1570`) and `apt.dat` for the ICAO search
  (`airports.py:546-563`). In Ortho4XP it only served the "1-click" links
  (`O4_GUI_Utils.py:1516, 1608, 1721, 1747`); the Global Scenery was a separate setting,
  `custom_overlay_src`, which OrthoStudio XP dropped.
- **In X-Plane.** Nothing directly. Without it: no installation, no overlay, no X-Plane relief
  (the build is refused).
- **Cost.** None.
- **Recommendation. Automatic.** Detected; asked only when detection fails, plus a "Change..."
  button. A saved folder that is no longer valid is treated as "not detected"
  (`api/specs.py` `resolve_xplane`); step 3 of the Plan names it ("The X-Plane 12 folder chosen in
  Settings was not found: <path>"), and says "X-Plane 12 was not found on this computer" when none
  is known, with a button to this question (`ui.md` 2.1).
- **Question (only when needed).**
  - EN: *Where is X-Plane 12 installed?* — **The detected folder (recommended)** · Choose another
    folder...
  - FR : *Où est installé X-Plane 12 ?* — **Le dossier détecté (conseillé)** · Choisir un autre
    dossier...

## 3. Advanced settings (14)

### 3.1 `advanced.curvature_tol`

`curvature_tol` · 2 · > 0, no unit.

- **Does.** Triangle4XP splits a land or water triangle while *longest edge in metres × largest
  terrain curvature under it × local weight* exceeds `curvature_tol` (`T4XP:7324-7348`, curvature
  computed from the raster, capped at 8 / pixel size, `T4XP:17099-17140`; same rule in Ortho4XP
  `T4XP.c:7297-7321, 16561-16600`; passed on the command line, Ortho4XP `O4_Mesh_Utils.py:679`,
  OrthoStudio XP `mesh/build.py:246-270`). Nothing is split below one raster pixel (about 30 m), and
  runway, taxiway and road triangles are never split (`T4XP:7261`). It is a product of a length and
  a curvature, not a distance in metres (OrthoStudio XP's own docstring says otherwise, section
  6.3). The weight comes from `apt_curv_tol` and `coast_curv_tol`.
- **In X-Plane.** Lower: the terrain follows ridges, valley floors and embankments more closely.
  Higher: smoother, rounder relief. With X-Plane's relief (90 m data refined to 30 m pixels)
  there is little extra detail to catch; with a detailed file the difference is larger (Q16).
- **Cost.** Triangles and DSF size, roughly 21 bytes per triangle (derived: the GEOD and CMDS
  atoms of `+43+005` weigh 25.1 MB for 1.2 M triangles), capped by `limit_tris`. A change
  rebuilds mesh, masks, DSF and coastal textures: 7.4 s for 1.5 (measured, `p3-native.md` 4).
  Triangle counts at other values: not measured.
- **Recommendation. Folded.** No preset uses it until Q16 shows a visible gain with X-Plane's
  relief.
- **Picture (for experts).** A wireframe of the triangles of `mesh.npz` over one valley at 2 and
  at 1, drawn on the hillshade of `Data<tile>.alt`, with the triangle count of each.
- **Expert label.** EN *Terrain detail (lower = finer mesh)* · FR *Finesse du relief (plus petit
  = maillage plus fin)*.

### 3.2 `advanced.limit_tris`

`limit_tris` · 3 (millions) · 0 to 5 in OrthoStudio XP (Ortho4XP took any value; 0 or ≥ 50 meant 5).

- **Does.** Limits the points Triangle4XP may add to `max(limit × 10⁶ / 1.9 − input nodes, 500 000)`
  (Ortho4XP `O4_Mesh_Utils.py:640-650`; OrthoStudio XP `mesh/build.py:213-224`). The final count is
  close to the limit, but the 500 000 floor lets a tile with very dense vector data exceed it, and
  the X-Plane 12 recut of coastal water adds triangles later in the DSF. OrthoStudio XP warns when
  the mesh reaches 99 % of the limit (`MESH_TRIANGLE_BUDGET_REACHED`, `mesh/build.py:536-545`).
- **In X-Plane.** Nothing while the limit is not reached: the three reference tiles have 0.30,
  0.94 and 1.20 M triangles (`p4-airports.md` 7). When it is reached, part of the relief stays
  coarser than `curvature_tol` asks. More triangles cost drawing time (not measured).
- **Cost.** None of its own: it is a cap.
- **Recommendation. Folded.**
- **Expert label.** EN *Maximum triangles per tile (millions)* · FR *Nombre maximal de triangles
  par tuile (millions)*.

### 3.3 `advanced.road_level`

`road_level` · 1 · 0 to 5.

- **Does.** Finds the roads and railways whose ground slopes sideways (`road_banking_limit`), and
  flattens the mesh across a band of 2 × `lane_width` under them: the band becomes polygons whose
  altitude is the centre line's, with flat normals (Ortho4XP `O4_Vector_Map.py:228-355`,
  `O4_Mesh_Utils.py:297-306`; OrthoStudio XP `vectors/roads.py:215-300`,
  `mesh/postprocess.py:184-188`). Level 1: motorway, trunk, primary, secondary, rail and
  narrow-gauge rail; 2 adds tertiary; 3 unclassified and residential; 4 service; 5 track (Ortho4XP
  `O4_Vector_Map.py:261-299`; OrthoStudio XP `sources/osm.py:160-166, 225-249`). Bridges and tunnels
  are never flattened; ways that start or end within 1.5 km of an airport are always flattened
  (Ortho4XP `O4_Vector_Map.py:229-241`, `O4_Airport_Utils.py:910-921`; OrthoStudio XP
  `vectors/roads.py:228-236`). 0 downloads no road data.
- **In X-Plane.** Expected (Q8): the roads and railways of the overlay pack lie flat on
  hillsides instead of floating on one side and sinking into the slope on the other. Nothing
  changes on flat ground.
- **Cost.** Level 2 or more downloads an extra OSM layer (`small_roads`, not measured) and adds
  many triangles. A change rebuilds vectors, mesh, masks, DSF and coastal textures: about 12 s
  natively on `+43+005` with OSM cached (sum of the node times measured in `p4-airports.md`
  4.1c).
- **Recommendation. Folded.**
- **Picture (for experts).** A crop of `mesh.npz` around a mountain road with the flattened
  triangles (attribute `INTERP_ALT`) filled in colour over the photo.
- **Expert label.** EN *Flatten the ground under roads: none / main roads / + minor roads /
  + streets / + service roads / + tracks* · FR *Aplanir le sol sous les routes : aucune / grands
  axes / + petites routes / + rues / + voies de desserte / + chemins*.

### 3.4 `advanced.min_area`

`min_area` · 0.001 km² · ≥ 0.

- **Does.** After clipping to the tile and simplification, a water polygon of at most
  `min_area / 10 000` square degrees is left out of the mesh (Ortho4XP `O4_Vector_Map.py:562, 584`,
  `O4_Vector_Utils.py:391`; OrthoStudio XP `vectors/water.py:236, 390`). The division assumes
  1 square degree = 10 000 km²; at 45° it is about 8 700 km², so the real threshold is about 0.87 ×
  `min_area`. Overlapping polygons of the tile are merged first.
- **In X-Plane.** Ponds and pools under the threshold are only photo: no X-Plane water, no
  reflections, nothing a seaplane can land on.
- **Cost.** Negligible.
- **Recommendation. Folded.**
- **Expert label.** EN *Smallest pond drawn as water (km²)* · FR *Plus petit plan d'eau traité
  comme de l'eau (km²)*.

### 3.5 `advanced.max_area`

`max_area` · 200 km² · ≥ 0.

- **Does.** An OSM water element, way or relation, whose **whole** area (not the part inside the
  tile) is at least `max_area` becomes "sea equivalent": masked like the sea and drawn with
  `water_rendering`; `water_smoothing` still applies to it and `sea_smoothing_mode` does not
  (Ortho4XP `O4_Vector_Map.py:453-503`, `O4_OSM_Utils.py:687-690, 733`, `O4_DSF_Utils.py:477-479`,
  `O4_Mesh_Utils.py:257-264`; OrthoStudio XP `vectors/water.py:352-386`, `dsf/recut.py:20-31`).
  Ortho4XP's exception list `good_imagery_list` is empty and cannot be set (`O4_Vector_Map.py:16`).
  OrthoStudio XP reports each decision (`OSM_LAKE_TREATED_AS_SEA`, `errors.py:210-216`,
  and `WaterResult.lakes`).
- **In X-Plane.** A big lake looks like the sea: the photo near its shores, X-Plane's water in
  the middle. Under the threshold the photo covers the whole lake at the fixed transparency of
  `ratio_water`. Example: Lake Geneva, about 580 km², is sea-like at the default (Q11).
- **Cost.** A sea-like lake saves the textures of its open water (not downloaded when their mask
  stays under 30), but its shore textures become DXT5, twice the size.
- **Recommendation. Folded**, and surfaced: the plan and the build report should name the lakes
  treated like the sea, with a way to keep the photo over one of them.
- **Picture.** The tile on the map with its water from the vector stage (`WaterResult.water` and
  `.sea_equiv`) in two colours, "photo over the whole lake" and "like the sea", with the area of
  each lake from `WaterResult.lakes`.
- **Expert label.** EN *Lakes larger than this look like the sea (km²)* · FR *Les lacs plus
  grands que cette surface sont traités comme la mer (km²)*.

### 3.6 `advanced.sea_smoothing_mode`

`sea_smoothing_mode` · `zero` · `zero`, `mean`, `none`.

- **Does.** After meshing, for the vertices of sea triangles (OSM coastline only, not big lakes):
  `zero` sets them to 0 m, coastline vertices included; `mean` sets each triangle to the mean of its
  corners, one pass; `none` only raises negative altitudes to 0 m (Ortho4XP
  `O4_Mesh_Utils.py:278-296`; OrthoStudio XP `mesh/postprocess.py:172-182`). X-Plane's relief
  already has the sea at 0 m, so the modes differ at the shoreline, where the relief can be above
  0 m (cliffs, quays, an OSM coastline drawn inland of the relief's coast).
- **In X-Plane.** `zero` pulls the land edge down to sea level all along the shore (a short
  slope, or a small wall at cliffs and quays); `none` keeps the shore's height, so a sea
  triangle touching a cliff can tilt above sea level; `mean` is in between (Q14).
- **Cost.** None.
- **Recommendation. Folded.**
- **Picture (for experts).** Altitude profile along a line crossing a cliff coast, read from
  `mesh.npz` built with each mode.
- **Expert label.** EN *Sea level at the shoreline: force to 0 m / average / keep the relief* ·
  FR *Niveau de la mer au rivage : forcer à 0 m / moyenner / garder le relief*.

### 3.7 `advanced.water_smoothing`

`water_smoothing` · 10 passes · ≥ 0.

- **Does.** N passes over the inland water triangles (lakes, rivers, and lakes above
  `max_area`), each setting a triangle's three corners to their mean, one triangle after the
  other (Ortho4XP `O4_Mesh_Utils.py:266-277`; OrthoStudio XP `mesh/postprocess.py:163-170`).
- **In X-Plane.** Flatter lakes and rivers: fewer bumps and steps on the water and along its
  banks. Rivers keep their overall descent (the averaging is local). 0: the water follows the
  raw relief.
- **Cost.** Negligible.
- **Recommendation. Folded.**
- **Expert label.** EN *Flatten lakes and rivers (passes)* · FR *Aplanir lacs et rivières
  (passes)*.

### 3.8 `advanced.apt_smoothing_pix`

`apt_smoothing_pix` · 8 raster pixels · ≥ 0 (OrthoStudio XP refuses above 1 000,
`vectors/rule.py:116-131`).

- **Does.** Inside each airport's footprint (outline, runways, taxiways, aprons, hangars) the
  elevation raster is replaced by an average over ± N pixels with a triangular kernel, weighted so
  that only the footprint counts (Ortho4XP `O4_Airport_Utils.py:924-1034`,
  `O4_DEM_Utils.py:956-983`; OrthoStudio XP `airports_vec/smoothing.py:105-150`,
  `dem/raster.py:680-770`). An OSM `smoothing_pix` tag on the airport overrides it. A pixel is
  1 arc-second (about 31 m north-south): 8 means ± 250 m.
- **In X-Plane.** Smoother ground on the airfield: runway and taxiway profiles (their polygons
  take their heights from this raster) and the grass between them. 0: the raw relief (Q15).
- **Cost.** None.
- **Recommendation. Folded.**
- **Expert label.** EN *Smooth the ground of airports (raster pixels)* · FR *Lisser le sol des
  aéroports (pixels du relief)*.

### 3.9 `advanced.ratio_water_pct`

`ratio_water` × 100 · 25 % · 0 to 100.

- **Does.** Inland water vertices get the coordinates (0, ratio) in `water_transition.png`, the
  border texture of the lake overlay (Ortho4XP `O4_DSF_Utils.py:308-317, 978-984`; OrthoStudio XP
  `dsf/encode.py:382-385`, `textures/ter.py:138-139`): the photo's opacity is the grey of the row
  that value picks. That ramp is not linear. Read like the photo coordinates of the same vertices
  (from the bottom of the image) with the grey as opacity, which matches both ends of Ortho4XP's
  hint, the photo keeps 99 % at 0, 87 % at 10 %, **58 % at 25 %**, 18 % at 50 %, 4 % at 75 %, 0 at
  100 % (computed from the PNG; Q2). The same value also sets the grey of inland water inside the
  sea masks, read at ratio + 0.1 instead: 99/255 = 39 % at 25 % (Ortho4XP `O4_Mask_Utils.py:69-72`;
  OrthoStudio XP `masks/profiles.py:67-75`). That grey is the band of `3steps` and softens the sea
  fade next to lagoons and river mouths.
- **In X-Plane.** Low: lakes and rivers show the photo's own water (green rivers, silt,
  reflections caught in the photo) with little of X-Plane's water. High: X-Plane's water
  (reflections, waves, an even colour) lightly tinted by the photo.
- **Cost.** Nothing on disk. A change rebuilds masks, DSF and coastal textures (about 4 s).
- **Recommendation. Shown**, as three choices labelled by what one sees (labels to confirm by
  Q2).
- **Question.**
  - EN: *How much of the photo on lakes and rivers?* — **Mostly the photo, with some of X-Plane's
    water (recommended, 25 %)** · Almost only the photo (10 %) · Mostly X-Plane's water, lightly
    tinted (50 %). *Lakes and rivers are X-Plane water with the photo laid over it at a fixed
    transparency; this sets that transparency.*
  - FR : *Quelle part de la photo sur les lacs et les rivières ?* — **Surtout la photo, avec un
    peu de l'eau d'X-Plane (conseillé, 25 %)** · Presque uniquement la photo (10 %) · Surtout
    l'eau d'X-Plane, légèrement teintée (50 %). *Lacs et rivières sont de l'eau X-Plane recouverte
    de la photo avec une transparence fixe ; ce choix règle cette transparence.*
- **Picture.** A lake and a river cut from a fixture texture, composited over a flat water colour
  at the opacity read from `water_transition.png` for 10, 25 and 50 %. A sketch until Q2
  confirms the row orientation.

### 3.10 `advanced.ratio_bathy`

`ratio_bathy` · 1.0 · 0 to 1.

- **Does.** For each water vertex, a depth ratio: 0 on the coastline, otherwise
  `max(min(10 × ratio_bathy × d / 255, 1), 0.1)`, where d is the value of the distance mask, or
  255 when there is none (Ortho4XP `O4_Bathymetry.py:8-13, 187-223`, `O4_DSF_Utils.py:811-816,
  858-863, 1027-1031`; OrthoStudio XP `dsf/bathy.py:49-101`, `dsf/encode.py:572-573`). Without
  `distance_masks_too`, the default, every value from 0.1 to 1 writes exactly the same DSF: full
  depth everywhere except on the coastline itself. Only a value under 0.1 changes something,
  and then for all water, sea and lakes alike. With distance masks, full depth is reached
  `25.5 / ratio_bathy` distance units from the shore; a unit is one ZL16 pixel (1.7 m at 45°):
  about 43 m at 1.0, 430 m at 0.1.
- **In X-Plane.** How deep X-Plane 12 treats the water near the shore (Q9).
- **Cost.** None.
- **Recommendation. Removed**, merged into `distance_masks_too` (4.14: one expert switch, "shallow
  water near the shore") because it has no effect without it.

### 3.11 `advanced.use_masks_for_inland`

`use_masks_for_inland` · false · bool.

- **Does.** Every inland water triangle is treated as sea: masks around lakes and rivers, an alpha
  channel in their textures, no fixed transparency from `ratio_water`, `water_rendering` applied to
  them, and DSF pools of 35 000 points instead of 50 000 (Ortho4XP
  `O4_DSF_Utils.py:477-479, 495-498`, `O4_Mask_Utils.py:474-482, 587`; OrthoStudio XP
  `dsf/recut.py:20-31`, `dsf/params.py:65-68`, `masks/water.py:230, 272-276`).
- **In X-Plane.** Lakes and rivers get the coast fade: photo along the banks, X-Plane water in
  the middle of wide lakes and rivers. A river narrower than about twice the coast width stays
  mostly photo (inference from the profiles of 1.2).
- **Cost.** Every texture holding a lake or a river becomes DXT5, 22.4 MB instead of 11.2 MB;
  on a tile full of ponds and rivers that can be most textures (estimate). More mask cells to
  build.
- **Recommendation. Folded.**
- **Expert label.** EN *Fade lakes and rivers like the sea (bigger textures)* · FR *Fondu des
  lacs et rivières comme en mer (textures plus lourdes)*.

### 3.12 `advanced.imprint_masks_to_dds`

`imprint_masks_to_dds` · true · bool.

- **Does.** True: the mask is written into the alpha channel of each coastal texture (DXT5).
  False: the texture stays DXT1 and a crop of the mask is shipped as a separate PNG of
  `4096 / 2^(ZL − mask_zl)` pixels (1024² at ZL16), referenced by the `.ter`
  (`LOAD_CENTER_BORDER`, `BORDER_TEX`); the sea terrain then becomes an overlay whatever
  `water_rendering` says (Ortho4XP `O4_DSF_Utils.py:319-336, 704-706, 715-742`,
  `O4_Imagery_Utils.py:2319-2372`; OrthoStudio XP `textures/ter.py:94, 140-143`,
  `pipeline/textures.py:831-833`).
- **In X-Plane.** The same fade is intended. The `XP12` water choice stops working. Video memory:
  the hint says imprinting saves it, but at ZL16 the separate mask is a 1024² image while
  imprinting adds 11.2 MB to each coastal texture, so the reverse is more likely (Q7).
- **Cost.** False saves 11.2 MB per coastal texture (27 of 179 at ZL16 on `+43+005`: 0.30 GB of
  2.30 GB) and adds one PNG per coastal texture.
- **Recommendation. Removed** (always true): the saving is small, the `XP12` option depends on
  it, and the memory claim is unverified. Revisit after Q7.

### 3.13 `advanced.terrain_casts_shadows`

`terrain_casts_shadows` · true · bool.

- **Does.** False adds `NO_SHADOW` to the land `.ter` files; water always has it (Ortho4XP
  `O4_DSF_Utils.py:351-352`; OrthoStudio XP `textures/ter.py:148-149`).
- **In X-Plane.** Hills and mountains of the tile stop casting shadows (they still receive
  them), where X-Plane draws terrain shadows at all (Q3).
- **Cost.** Nothing on disk; drawing cost not measured.
- **Recommendation. Folded.**
- **Expert label.** EN *Hills cast shadows* · FR *Le relief projette des ombres*.

### 3.14 `advanced.overlay_lod_km`

`overlay_lod` ÷ 1000 · 25 km · ≥ 0.

- **Does.** The far drawing distance written on every overlay patch of the DSF (Ortho4XP
  `O4_DSF_Utils.py:1215`; OrthoStudio XP `dsf/encode.py:449-460`): always the lakes and rivers, and
  the coastal sea in `XP11 + bathy` mode. Land and `XP12` sea are not overlays and have no limit.
- **In X-Plane.** Beyond this distance lakes and rivers show only X-Plane's water and, with
  `XP11 + bathy`, the coastal photo disappears, leaving a hard photo/water edge along the coast.
  Higher is better from altitude; lower may help the frame rate (Q6).
- **Cost.** Nothing on disk; drawing cost not measured.
- **Recommendation. Folded.**
- **Expert label.** EN *Photo over water visible up to (km)* · FR *Photo sur l'eau visible
  jusqu'à (km)*.

## 4. Expert settings (19)

### 4.1 `expert.mesh_zl`

`mesh_zl` · 19 · 16 to 20.

- **Does.** Inserts the texture grid of that zoom level into the mesh as edges, so that no triangle
  straddles two textures (Ortho4XP `O4_Vector_Map.py:96-128`; OrthoStudio XP
  `vectors/grid.py:60-100`, `vectors/assemble.py:298`), and is the grid on which the DSF decides the
  texture of each triangle (Ortho4XP `O4_DSF_Utils.py:175-224`; OrthoStudio XP
  `dsf/zones.py:207-245`). An airport zoom level above `mesh_zl` is accepted without any check (map
  zones are refused above it by the P5 work in progress, `zones.py:550-567`); by the arithmetic of
  `dsf/zones.py:242-245` its texture would cover only part of each mesh cell, the rest getting
  clamped texture coordinates (not tested: Q18).
- **In X-Plane.** Nothing by itself. A lower value saves triangles ("a few tens of thousands"
  per the hint, not measured).
- **Cost.** A change rebuilds everything from the vectors.
- **Recommendation. Automatic:** `max(19, the highest zoom level of the request)`, and refuse
  any zoom level above it. Every provider of the registry stops at 19, except Luxembourg (20,
  assumed).

### 4.2 `expert.mask_zl`

`mask_zl` · 14 · 14, 15, 16.

- **Does.** The resolution of the masks: one 4096² mask per texture of that zoom level, so 6.8 m per
  pixel at 14 and 1.7 m at 16 (45°). A texture at a higher level uses a crop of it scaled up:
  1024 px stretched to 4096 at ZL16, 256 px at ZL18 (Ortho4XP `O4_Mask_Utils.py:22-60, 658`;
  OrthoStudio XP `textures/imprint.py:95-131`). A texture below `mask_zl` never gets a mask: its sea
  is plain X-Plane water from the coastline, and nothing says so (Ortho4XP
  `O4_Mask_Utils.py:24, 39`; OrthoStudio XP
  `dsf/encode.py:215-216`, `pipeline/textures.py:816-817`).
- **In X-Plane.** 15 or 16: a coast fade that follows rocks, quays and coves more closely,
  mostly on ZL17 and ZL18 textures (Q12). Set above the lowest zoom level used on a coast: that
  coast loses the photo over its water.
- **Cost.** About 4 times (15) or 16 times (16) more mask cells to compute (7 masks and about
  1 s at 14 on `+43+005`; estimate). No change in texture size while masks are imprinted: the
  alpha channel is 4096² either way.
- **Recommendation. Folded, guarded:** never above the lowest zoom level used where there is
  sea.
- **Picture (for experts).** The alpha channel of one ZL18 coastal texture made from a
  `mask_zl` 14 mask (a 256 px crop stretched 16 times) next to one made at 16, both from
  `orthostudio.textures.imprint.mask_for_texture`.
- **Expert label.** EN *Coast fade precision* · FR *Précision du fondu côtier*.

### 4.3 `expert.apt_curv_tol`

`apt_curv_tol` · 0.5 · > 0.

- **Does.** In a rectangle around each airport (the bounding box of its outline widened by
  `apt_curv_ext`) the curvature is multiplied by `curvature_tol / apt_curv_tol`, 4 by default, so
  the mesh there is refined as if `curvature_tol` were `apt_curv_tol`. The weight is assigned
  whether the value is smaller or larger than `curvature_tol` (Ortho4XP `O4_Mesh_Utils.py:133-160`;
  OrthoStudio XP `mesh/weights.py:131-145`): a larger value makes the mesh coarser around airports,
  which the hint does not say.
- **In X-Plane.** Terrain around airports closer to the relief data: approach slopes,
  embankments, valleys under the final. Runways and taxiways themselves are flat polygons and are
  not refined.
- **Cost.** More triangles around airports (not measured).
- **Recommendation. Folded.**
- **Expert label.** EN *Terrain detail around airports* · FR *Finesse du relief autour des
  aéroports*.

### 4.4 `expert.apt_curv_ext`

`apt_curv_ext` · 0.5 km · ≥ 0.

- **Does.** The widening of that rectangle, from the bounding box of the airport's outline (Ortho4XP
  `O4_Mesh_Utils.py:150-157`; OrthoStudio XP `mesh/weights.py:131-141`).
- **In X-Plane / Cost.** How far from the field the finer terrain reaches; more triangles.
- **Recommendation. Folded.**
- **Expert label.** EN *Terrain detail around airports: margin (km)* · FR *Finesse du relief
  autour des aéroports : marge (km)*.

### 4.5 `expert.coast_curv_tol`

`coast_curv_tol` · 1 · > 0.

- **Does.** Around every OSM coastline node inside the tile, in a square of ± `coast_curv_ext`, the
  curvature weight becomes `max(weight, curvature_tol / coast_curv_tol)`: it only acts when smaller
  than `curvature_tol` (Ortho4XP `O4_Mesh_Utils.py:161-220`; OrthoStudio XP
  `mesh/weights.py:146-167`). Sea coast only (`natural=coastline`), not lakes.
- **In X-Plane.** Finer relief along sea cliffs and coastal hills.
- **Cost.** More triangles along the coast (not measured).
- **Recommendation. Folded.**
- **Expert label.** EN *Terrain detail along the coast* · FR *Finesse du relief le long des
  côtes*.

### 4.6 `expert.coast_curv_ext`

`coast_curv_ext` · 0.5 km · ≥ 0.

- **Does.** Half-size of those squares (Ortho4XP `O4_Mesh_Utils.py:207-220`; OrthoStudio XP
  `mesh/weights.py:156-167`).
- **In X-Plane / Cost.** How far inland and out to sea the finer terrain reaches; more
  triangles.
- **Recommendation. Folded.**
- **Expert label.** EN *Terrain detail along the coast: margin (km)* · FR *Finesse du relief le
  long des côtes : marge (km)*.

### 4.7 `expert.min_angle`

`min_angle` · 10° · 0 to 30.

- **Does.** Triangle4XP splits water triangles whose smallest angle is under `min_angle`, and land
  triangles larger than a raster pixel whose second smallest angle is under it, "a risk of a sharp
  wall" in the source (`T4XP:7354-7369`; Ortho4XP `T4XP.c:7327-7342`; switch `-pq`, Ortho4XP
  `O4_Mesh_Utils.py:651-654`, OrthoStudio XP `mesh/build.py:229-237`). When Triangle4XP fails,
  OrthoStudio XP retries with 0 and says so (`MESH_QUALITY_RELAXED`, `mesh/build.py:462-476`);
  Ortho4XP announced the same retry but overwrote the `nodata` argument instead and kept the
  angle (`O4_Mesh_Utils.py:711`).
- **In X-Plane.** Fewer long thin triangles on steep slopes, which show as spikes or walls;
  higher values add triangles.
- **Cost.** Triangles (not measured).
- **Recommendation. Folded.**
- **Expert label.** EN *Minimum triangle angle (°)* · FR *Angle minimal des triangles (°)*.

### 4.8 `expert.road_banking_limit`

`road_banking_limit` · 0.5 m · ≥ 0.

- **Does.** A way is flattened when, at any one of its nodes, the relief at the centre line and the
  relief `lane_width` to its left differ by at least this much (Ortho4XP `O4_Vector_Map.py:229-249`;
  OrthoStudio XP `vectors/roads.py:228-236`). With 4 m, 0.5 m is a 12.5 % side slope.
- **In X-Plane.** Lower: more roads flattened, more triangles. Higher: only the steepest ones.
- **Recommendation. Folded.**
- **Expert label.** EN *Side slope that triggers flattening (m over one lane)* · FR *Dévers qui
  déclenche l'aplanissement (m sur une voie)*.

### 4.9 `expert.lane_width`

`lane_width` · 4 m · > 0.

- **Does.** The offset used to measure the side slope, and the half-width of the flattened band, 8 m
  wide by default (Ortho4XP `O4_Vector_Map.py:245, 251, 327-340`, `O4_Vector_Utils.py:1021-1069`;
  OrthoStudio XP `vectors/roads.py:233, 293-302`). Airports are cut out of the band with a margin of
  `lane_width + 2` m.
- **In X-Plane.** The width of the flat strip under a road; too narrow and wide roads still tilt
  at their edges.
- **Recommendation. Folded.**
- **Expert label.** EN *Half-width of the flattened strip (m)* · FR *Demi-largeur de la bande
  aplanie (m)*.

### 4.10 `expert.max_levelled_segs`

`max_levelled_segs` · 200 000 · ≥ 0.

- **Does.** Once the flattened ways of one OSM layer total this many **nodes**, the other ways of
  that layer are not flattened, except those near an airport; the count starts again for the
  small-roads layer; ways are taken in the order of the OSM data, so which ones miss out is
  arbitrary (Ortho4XP `O4_Vector_Map.py:240-241`, `O4_OSM_Utils.py:595-629`; OrthoStudio XP
  `vectors/roads.py:215-240`).
- **In X-Plane.** On very dense networks some roads are not flattened.
- **Recommendation. Folded.**
- **Expert label.** EN *Maximum road points flattened per layer* · FR *Nombre maximal de points
  de route aplanis par couche*.

### 4.11 `expert.water_simplification`

`water_simplification` · 0 m · ≥ 0.

- **Does.** Simplifies every inland water polygon, not the sea coastline, with that tolerance
  converted with the latitude scale on both axes (Ortho4XP `O4_Vector_Map.py:563, 585`,
  `O4_Vector_Utils.py:388-389`; OrthoStudio XP `vectors/water.py:233-234, 391`).
- **In X-Plane.** Straighter lake shores and river banks that drift away from the photo; fewer
  triangles.
- **Recommendation. Folded.**
- **Expert label.** EN *Simplify lake and river outlines (m)* · FR *Simplifier les contours des
  lacs et rivières (m)*.

### 4.12 `expert.masks_use_dem_too`

`masks_use_DEM_too` · false · bool.

- **Does.** Adds to the sea mask every place where the relief is under 0.5 m, slightly blurred
  (Ortho4XP `O4_Mask_Utils.py:19, 123-137, 150-155, 332-370`). OrthoStudio XP's native masks stage
  cannot read the relief yet, so this switch sends the masks stage back to Ortho4XP
  (`pipeline/native.py:143-144`), which reads Ortho4XP's own relief source (`custom_dem`, else
  viewfinderpanoramas), not X-Plane's relief used for the mesh (`legacy/params.py:80-98`).
- **In X-Plane.** With a very detailed relief file (5 m or finer per the hint) a sea fade that
  follows the real waterline; with coarse data, blocky coasts.
- **Cost.** Masks built by Ortho4XP: 4.3 s instead of 0.8 s on `+43+005` (measured,
  `p3-native.md` 1).
- **Recommendation. Folded**, offered only with a relief file.
- **Expert label.** EN *Use the relief to draw the coast fade (detailed files only)* · FR
  *Utiliser le relief pour dessiner le fondu côtier (fichiers détaillés seulement)*.

### 4.13 `expert.masks_custom_extent`

`masks_custom_extent` · `""` · the name of an extent drawn in JOSM.

- **Does.** In Ortho4XP it adds a hand-drawn "good imagery" area to the masks, but the code then
  reads an undefined name (`custom_mask`) and fails on every mask cell that reaches it
  (`O4_Mask_Utils.py:157-175`; `masks-build.md` 2). In OrthoStudio XP the extent rasteriser is P4b;
  setting it sends the masks stage back to Ortho4XP (`pipeline/native.py:145-146`), which meets
  that failure.
- **Recommendation. Removed** until P4b.

### 4.14 `expert.distance_masks_too`

`distance_masks_too` · false · bool.

- **Does.** Builds a second mask per cell, the distance to the shore (0 on land, then ZL16 pixels,
  saturated at 255), which the DSF reads to set the depth ratio of sea vertices together with
  `ratio_bathy` (Ortho4XP `O4_Mask_Utils.py:184-198`, `O4_Bathymetry.py:187-223`; OrthoStudio XP
  `masks/build.py:224`, `masks/distance.py`, `dsf/bathy.py:49-88`, `pipeline/build.py:671-693`).
  Without it the depth ratio is 0 on the coastline and full at the next vertex, however far away
  that vertex is.
- **In X-Plane.** Expected (Q9): water that looks shallower in the first tens of metres off
  beaches, quays and rocks, instead of a sudden drop.
- **Cost.** Masks of `+43+005`: 1.83 s instead of 1.07 s, 962 MB per worker (measured,
  `masks-build.md` 6.1-6.2). The distance masks stay in the store; they are not part of the
  tile.
- **Recommendation. Folded**, together with `ratio_bathy`, as one switch.
- **Expert label.** EN *Shallow water near the shore (experimental)* · FR *Eau peu profonde près
  du rivage (expérimental)*.

### 4.15 `expert.sea_texture_blur`

`sea_texture_blur` · 0 · ≥ 0; the unit is a ZL17 pixel (0.84 m at 45°), not a metre.

- **Does.** In Ortho4XP it only blurs the "mask" layers of combined providers
  (`O4_Imagery_Utils.py:1856-1862, 2184-2190, 2245-2251`); a single provider such as Bing is never
  blurred. `osxp build`, and so the web page, applies it to the water part of every masked texture
  of any provider, mixing in a blurred copy weighted by `(255 − alpha) / 255`
  (`textures/imprint.py:177-231`, reached through `pipeline/build.py:628, 853`). OrthoStudio XP has
  no combined providers yet.
- **In X-Plane.** Softer frozen waves, sun glints and boats in the photo over the sea near the
  shore; land untouched (Q19).
- **Cost.** Coastal textures re-encoded.
- **Recommendation. Folded.**
- **Expert label.** EN *Blur the photo over the sea (hides frozen waves)* · FR *Flouter la photo
  sur la mer (efface les vagues figées)*.

### 4.16 `expert.normal_map_strength`

`normal_map_strength` · 1 · 0 to 1.

- **Does.** Scales the terrain normals written in the DSF: 1 = the real slope, 0 = every vertex lit
  as if flat (Ortho4XP `O4_DSF_Utils.py:572-577`; OrthoStudio XP `dsf/encode.py:587-589`). Water,
  roads and airports have flat normals anyway (Ortho4XP `O4_Mesh_Utils.py:297-306`; the water
  entries write 32768, `dsf/encode.py:382, 408`).
- **In X-Plane.** Expected (Q4): lower means less sun shading of slopes added on top of the
  shading already in the photo; the hint warns that shadows change too.
- **Recommendation. Folded.**
- **Expert label.** EN *Sun shading of slopes (0 = none, 1 = real)* · FR *Ombrage des pentes par
  le soleil (0 = aucun, 1 = réel)*.

### 4.17 `expert.use_decal_on_terrain`

`use_decal_on_terrain` · false · bool.

- **Does.** Adds `DECAL_LIB lib/g10/decals/maquify_2_green_key.dcl` to the land and masked-sea
  `.ter` files, not to inland water (Ortho4XP `O4_DSF_Utils.py:342-344`; OrthoStudio XP
  `textures/ter.py:144-146`). The hint names `maquify_1` and says "all but water". Which decal is
  `expert.decal` (4.17c). The pack writes the line, not the DSF step (2026-09-26): turning it on
  or off assembles a built tile's pack again and nothing else.
- **In X-Plane.** Expected: fine grain on the ground seen from very low; whether X-Plane 12 still
  ships that decal is Q5.
- **Recommendation. Folded.**
- **Expert label.** EN *Fine ground detail at very low height (decals)* · FR *Grain du sol à très
  basse hauteur (decals)*. X-Plane 12 does ship the decal
  (`Resources/default scenery/1000 decals`), checked 2026-09-17, and the label names it "decals"
  since a user could not find the setting.

### 4.17b `expert.decal_on_sea` (OrthoStudio XP only)

`–` · false · bool.

- **Does.** Puts the decal on the masked sea too, as Ortho4XP does. Off, the decal goes on land
  alone: a user found the grain wrong on the sea and asked for the land alone (2026-09-17).
  Inland water never has it, in either case. The pack writes it, like the switch above.
- **Recommendation. Folded**, next to the decal setting it completes.
- **Expert label.** EN *Fine ground detail on the sea too* · FR *Grain du sol aussi sur la mer*.

### 4.17c `expert.decal` (OrthoStudio XP only)

No Ortho4XP name · `maquify_2_green_key.dcl` · one of 67 names (`orthostudio/decals.py`).

- **Does.** Names the decal of the `DECAL_LIB` line, `lib/g10/decals/<name>`, when
  `use_decal_on_terrain` is on; off, it reaches nothing. The list is the one of setdecal, a tool of
  the X-Plane.Org forum that rewrites that line in tiles already built, whose author asked for the
  choice here (2026-09-25), less `grass_and_asphalt_3`, `mid_freq_test`, `rail_dry_drp`,
  `rail_dry_grass_emb` and `rail_dry_grd`, which X-Plane 12.4.4 no longer exports. A name outside
  the list (`--set decal=...`) is `CFG_VALUE_INVALID` before anything is built.
- **In X-Plane.** Laminar's own words for the two in question: `maquify_2_green_key` draws shrubby
  vegetation on what is vaguely green and stony dirt elsewhere, in a 25 m pattern;
  `grass_and_stony_dirt_1`, setdecal's default, rough grass on greenish areas and stony dirt
  elsewhere, in 9 m.
- **Cost of a change.** The pack writes the decal (`PackParams.decal`, `with_decal`) and the
  DSF step none, so this choice, and the two switches above, leave the DSF and the textures hits
  and assemble the pack alone: a second on +43+005 at ZL14 (39 terrain files, 17 of land). A tile
  built without decals keeps every key, unless *on the sea too* was ticked with the decals off;
  that one, and one built with decals before 0.1.18, is built again once.
- **Recommendation. Folded (list)**, under the two decal switches.
- **Expert label.** EN *Ground decal* · FR *Decal du sol*; the default reads
  *maquify_2_green_key.dcl (Ortho4XP)*.

### 4.18 `expert.ovl_exclude_pol`

`ovl_exclude_pol` · `[0]` · definition indices, or fragments of definition names (`!` inverts).

- **Does.** Polygon types of the Global Scenery left out of the overlay pack. Index 0 is the first
  `POLYGON_DEF` of the source, `lib/g12/beaches.bch` on the XP12 tiles sampled (Ortho4XP
  `O4_Overlay_Utils.py:120-151`; OrthoStudio XP `overlays/exclusions.py:27-33, 97-114`,
  `overlays.md` 2). Names are safer than indices: `.for` forests, `.fac` facades, `.ags` autogen,
  `.lin` airport borders (`overlays.md` 2). OrthoStudio XP also keeps the objects Ortho4XP dropped
  (`keep_objects`, not a setting of the model).
- **In X-Plane.** The listed types disappear on the tile: the beaches by default, because
  X-Plane's beaches follow its own coastline, not the photo's; forests, facades or autogen if
  they are listed.
- **Cost.** Only the overlay is rebuilt (3-4 s measured).
- **Recommendation. Folded**, as a checklist in plain words: Beaches (removed, recommended) ·
  Forests · Autogen · Facades.
- **Picture.** None needed; the checklist can list the definitions really present in the tile
  with their counts, from the overlay stage's statistics (`FilterStats.polygon_defs`,
  `overlays/textfilter.py:36-61`).
- **Expert label.** EN *X-Plane objects to remove on top of the photos* · FR *Éléments d'X-Plane
  à retirer par-dessus les photos*.

### 4.19 `expert.ovl_exclude_net`

`ovl_exclude_net` · `[]` · road type numbers, or `"*"` for all.

- **Does.** Road, railway and power-line segment types left out of the overlay pack (Ortho4XP
  `O4_Overlay_Utils.py:159-172`; OrthoStudio XP `overlays/exclusions.py:56-58, 73-88, 117-123`). The
  hint's 22001 for power lines is the number in XP11's `roads.net`; the XP12 Global Scenery uses
  `lib/g10/roads_EU.net` (`overlays.md` 2), whose numbers OrthoStudio XP has not established (Q10).
- **In X-Plane.** The removed types disappear, for example power lines.
- **Recommendation. Folded.**
- **Expert label.** EN *X-Plane networks to remove (roads, railways, power lines)* · FR *Réseaux
  d'X-Plane à retirer (routes, voies ferrées, lignes électriques)*.

## 5. Presets

Three presets set the values below; everything not listed keeps the Ortho4XP default. The only
deviation of **Recommended** from Ortho4XP is the airport cover.

| Setting | Recommended | Best quality | Light on disk |
|---|---|---|---|
| `essential.provider` | `BI` | `BI` | `BI` |
| `essential.zoom_level` | 16 | 17 | 15 |
| `essential.airports.mode` | `icao` | `on` | `off` |
| `essential.airports.zoom_level` | 18 | 18 | 18 (unused) |
| `essential.airports.extent_km` | 1.0 | 1.0 | 1.0 (unused) |
| `essential.coast_transition` | `sand`, 100 m | `sand`, 100 m | `sand`, 100 m |
| `essential.water_rendering` | `XP11 + bathy` | `XP11 + bathy` (until Q1) | `XP11 + bathy` |
| `essential.relief` | `auto` | `auto` | `auto` |
| `advanced.ratio_water_pct` | 25 | 25 | 25 |
| everything else | Ortho4XP defaults | Ortho4XP defaults | Ortho4XP defaults |

Deliberately left out: a finer mesh (`curvature_tol`) in Best quality, because X-Plane's relief
holds little finer detail (Q16); `mask_zl` 15 in Best quality until Q12 shows a gain;
`imprint_masks_to_dds` off in Light on disk (about 13 % less on a coastal ZL16 tile) because it
disables the `XP12` water and its memory effect is unknown (Q7).

What each preset costs, per tile. `+43+005` is coastal with 18 airports (7 ICAO); `+50+008` is
inland with 32 airports (14 ICAO). Kinds of figures as defined at the top.

| | Recommended | Best quality | Light on disk |
|---|---|---|---|
| Ground detail (45°) | 1.7 m/px, airports 0.42 m/px | 0.84 m/px, every airfield 0.42 m/px | 3.4 m/px |
| Textures `+43+005` | 179 measured + 76 computed = 255 | ≈ 640 estimate + 115 computed ≈ 755 | 56 measured |
| DDS on disk `+43+005` | 2.30 GB measured + 0.85 GB computed ≈ **3.2 GB** | ≈ 8.3 GB + 1.3 GB ≈ **9.6 GB** (estimate) | ≈ **0.6-0.8 GB** (estimate) |
| Photo download `+43+005` | 741 MB measured + ≈ 0.31 GB ≈ **1.05 GB** | ≈ **3.1 GB** (estimate) | ≈ **0.2 GB** (estimate) |
| Build time `+43+005`, photos not cached | ≈ 45 s of textures + 13 s ≈ **1 min** (estimate) | ≈ 2 min 15 s + 13 s ≈ **2.5 min** (estimate) | 15.0 s measured + 13 s ≈ **30 s** |
| DDS on disk `+50+008` | ≈ 2.55 + 1.50 ≈ **4.0 GB** (estimate) | ≈ 9.5 + 2.6 ≈ **12 GB** (estimate) | ≈ **0.8 GB** (estimate) |

How the estimates were made: ZL17 keeps the ZL16 ratio between the DSF list and the map
(179 / 221) and its share of DXT5 (27 of 179); a texture is 11.18 MB (DXT1) or 22.37 MB (DXT5)
and about 4.1 MB of Bing download (741 MB / 179 at ZL16); texture time is 0.17-0.19 s per
texture, the ZL16 measurement on this machine and line (1 332-1 550 requests/s), so a slower
line scales it; "13 s" is the rest of a native tile with OSM cached (`p4-airports.md` 4.1c). An
uncached tile adds its OSM download (24.6 s measured on `+43+004`). On top of the DDS: the DSF
(14-32 MB), the overlay DSF, about 0.25 GB of intermediate artefacts per tile in the store
(`estimate.py:58`), and the downloaded photos, which the store keeps for rebuilds.

## 6. Where the real effect differs from Ortho4XP's help text

### 6.1 The help text is wrong

1. **`ratio_bathy`**, "Bathymetry multiplier for near shore vertices": without distance masks, the
   default, any value from 0.1 to 1 writes the same DSF; it applies to every non-coast water vertex,
   sea and lakes, and coast vertices are always 0. Evidence: Ortho4XP `O4_Bathymetry.py:8-13` with
   `node_bathy = 255` when no `_dist.png` exists (`:196-223`); OrthoStudio XP
   `dsf/bathy.py:60-63, 96-101`; `dsf-terrain-assignment.md` 3.3.
2. **`apt_curv_tol`**, "If smaller, it supersedes curvature_tol": the weight is assigned whether
   smaller or larger, so a larger value coarsens the mesh around airports; only `coast_curv_tol`
   takes a maximum. Ortho4XP `O4_Mesh_Utils.py:158-160` against `:217-220`; OrthoStudio XP
   `mesh/weights.py:142-144` against `:165-167`.
3. **`custom_dem`**, "Regions of the tile that are not covered by the raster are mapped to zero
   altitude": positions outside the raster are clamped to its edge, so the edge heights are
   stretched; only no-data inside the raster can end at 0 m. Ortho4XP `O4_DEM_Utils.py:237-261`,
   `T4XP.c:15198-15204`; OrthoStudio XP `dem/dem.py:237-243`. Also out of date for OrthoStudio XP:
   "requires Gdal" (Pillow reads the GeoTIFF, `dem/raster.py:224-263`) and "default
   Viewfinderpanoramas" (the default is X-Plane 12's relief, ADR 0007).
4. **`use_decal_on_terrain`**, "maquify_1_green_key.dcl ... all but water triangles": the code
   writes `lib/g10/decals/maquify_2_green_key.dcl`, on land and on the masked sea. Ortho4XP
   `O4_DSF_Utils.py:342-344`; OrthoStudio XP `textures/ter.py:144-146`; `textures-ter.md` 1.
5. **`mask_zl`**, "the reason for this (VRAM saving) parameter": with masks imprinted (the
   default) the mask is the alpha of a 4096² DXT5 whatever `mask_zl`; memory and disk only
   change with separate PNG masks (`4096 / 2^(ZL − mask_zl)` px). Not said: a texture below
   `mask_zl` gets no photo over its sea. Ortho4XP `O4_DSF_Utils.py:319-336`,
   `O4_Mask_Utils.py:22-26, 38-41`; OrthoStudio XP `dsf/encode.py:215-216`.
6. **`imprint_masks_to_dds`**, "reduce the overall VRAM footprint": at ZL16 with `mask_zl` 14 the
   separate mask is 1024² while imprinting adds 11.2 MB per coastal texture; the claim can only
   hold near ZL = `mask_zl`, where the separate mask is itself 4096² (how X-Plane stores it:
   Q7). Not said: turning it off forces
   the XP11 overlay whatever `water_tech` says. Ortho4XP `O4_DSF_Utils.py:319-336, 704-706`.
7. **`sea_texture_blur`**, "extent (in meters) of the blur radius": the radius is
   `sea_texture_blur × 2^(ZL − 17)` pixels, so the unit is a ZL17 pixel (0.84 m at 45°, 1.19 m
   at the equator); and Ortho4XP only applies it to "mask" layers of combined providers, never to a
   single provider. Ortho4XP `O4_Imagery_Utils.py:1856-1862, 2184-2190, 2245-2251`.
8. **`fill_nodata`**, "filled by a nearest neighbour algorithm": only with fewer than 10 000
   void pixels, and by at most 20 rounds of "highest of 4 neighbours"; beyond that, voids go to
   0 m. Ortho4XP `O4_DEM_Utils.py:866-907`; OrthoStudio XP `dem/raster.py:325-352`.
9. **`ratio_water`**, "determines how much transparency is applied to the orthophoto": the value
   is a coordinate in `water_transition.png`, a non-linear ramp; read from the bottom with the grey
   as opacity, the photo keeps 58 % at 25 % and 18 % at 50 % (Q2). The same setting also
   changes the sea masks, through another row (ratio + 0.1): 39 % at 25 %. Ortho4XP
   `O4_DSF_Utils.py:308-317, 983`, `O4_Mask_Utils.py:72`, `Utils/water_transition.png`.
10. **`masking_mode` (3steps)**, "transparency level is kept constant equal to ratio_water": the
    band is the mask grey of row (ratio + 0.1), 99/255 = 39 % at 25 %, not the opacity the lakes
    get from the same value (item 9). Ortho4XP `O4_Mask_Utils.py:72, 780`.
11. **`custom_scenery_dir`**, "Used only for 1-click creation (or deletion) of symbolic links": in
    OrthoStudio XP the X-Plane folder also provides the Global Scenery (overlays, sea rasters,
    default relief) and `apt.dat`. OrthoStudio XP `pipeline/build.py:212-237,
    1533-1570`, `airports.py:546-563`.

### 6.2 The help text is right but leaves out what matters

12. **`masks_width`**, "Maximum extent of the masks": true for `sand` (gone at about 0.95 × the
    width); `rocks` reaches zero at about 1.7 × the width, `3steps` at about the sum of its
    lengths; widths are rounded to mask pixels (about 7 m at `mask_zl` 14) and a `sand` width
    under one pixel removes the fade. Section 1.2; Ortho4XP `O4_Mask_Utils.py:658-664`.
13. **`masking_mode`**, "The transition with rocks is more abrupt": concretely `rocks` drops to
    about 57 % at the shoreline and then fades slowly, `sand` starts at about 93 % and fades
    smoothly. Section 1.2.
14. **`cover_extent`**, "past the airport boundary": past the bounding rectangle of the outline,
    then rounded outwards to whole textures of `cover_zl` (1.7 km at ZL18). Ortho4XP
    `O4_DSF_Utils.py:141-164`.
15. **`cover_airports_with_highres`**: the airports are OSM aerodromes and airstrips, not
    X-Plane's `apt.dat`; small ones are dropped (outline under 5 000 m², runway under 2 500 m²);
    "ICAO" means an OSM `icao` tag; `Existing` is refused by OrthoStudio XP. Ortho4XP
    `O4_Airport_Utils.py:19-42, 682-697`; OrthoStudio XP `pipeline/build.py:754-765`.
16. **`max_area`**: judged per OSM element on its whole area, not the part inside the tile; the
    exception list `good_imagery_list` is empty and cannot be set. Ortho4XP `O4_Vector_Map.py:16,
    453-503`, `O4_OSM_Utils.py:687-690, 733`.
17. **`min_area`**, "(in km^2)": converted with 1 square degree = 10 000 km² (about 0.87 × the
    stated area at 45°) and judged per polygon after clipping to the tile. Ortho4XP
    `O4_Vector_Map.py:562, 584`, `O4_Vector_Utils.py:391`.
18. **`apt_smoothing_pix`**, "gaussian blur": a triangular kernel, normalised inside the airport
    footprint only; one pixel is one arc-second. Ortho4XP `O4_DEM_Utils.py:956-983`,
    `O4_Airport_Utils.py:924-1034`.
19. **`max_levelled_segs`**, "total number of roads segments": counts nodes, per OSM layer (the
    count restarts for small roads); ways near an airport are flattened anyway; which ways miss
    out depends on OSM order. Ortho4XP `O4_OSM_Utils.py:595-629`, `O4_Vector_Map.py:229-241`.
20. **`road_level`**: level 1 also takes trunk roads and narrow-gauge railways; bridges and tunnels
    are never flattened; ways ending within 1.5 km of an airport always are. Ortho4XP
    `O4_Vector_Map.py:229-241, 261-268`, `O4_Airport_Utils.py:910-921`. The advice to purge
    `small_roads.osm` does not apply to OrthoStudio XP, whose OSM snapshots are keyed by their
    layers (`sources/osm.py:225-249`).
21. **`limit_tris`**, "approx upper bound": a budget of added points with a floor of 500 000, so
    dense vector data can exceed it; the XP12 recut adds triangles afterwards. Ortho4XP
    `O4_Mesh_Utils.py:640-650`, `O4_Bathymetry.py:16-183`.
22. **`min_angle`**: accurate, but Ortho4XP's fallback message ("tempted now with no angle
    constraint") is false: the retry overwrote the `nodata` argument and kept the angle. Ortho4XP
    `O4_Mesh_Utils.py:700-712`; fixed in OrthoStudio XP `mesh/build.py:462-476`.
23. **`water_tech`**, "Both allows for 3D water": not said that only the masked sea (and lakes
    above `max_area`) changes, that lakes and rivers are drawn the same in both, and that
    `XP11 + bathy` is cut at `overlay_lod` while `XP12` sea is not. Ortho4XP
    `O4_DSF_Utils.py:702-706, 910, 1215`.
24. **`overlay_lod`**, "orthophotos over water": lakes and rivers always, the coastal sea only in
    `XP11 + bathy` mode or with separate PNG masks. Same references.
25. **`ovl_exclude_net`**, "Powerlines have index 22001 in XP11 roads.net": the XP12 Global
    Scenery uses `lib/g10/roads_EU.net` (`overlays.md` 2); its power-line number is not
    established (Q10).
26. **`use_masks_for_inland`**, "VRAM expensive": also puts those waters under `water_tech` and
    lowers the DSF pool capacity (35 000 instead of 50 000 points). Ortho4XP
    `O4_DSF_Utils.py:477-479, 495-498`.
27. **`mesh_zl`**, "put a limitation on the maximum allowed imagery zoomlevel": Ortho4XP enforces
    nothing; OrthoStudio XP now refuses map zones above it (`zones.py:550-567`, P5 in progress) but
    not the airport cover (Q18). Ortho4XP `O4_DSF_Utils.py:219-224`; OrthoStudio
    XP `dsf/zones.py:242-245`.
28. **`masks_custom_extent`**: fails in Ortho4XP on every cell that reaches the custom extent
    (`custom_mask` undefined). Ortho4XP `O4_Mask_Utils.py:170-175`.
29. **`default_website`, `default_zl`**: no help text at all in
    Ortho4XP (`O4_Config_Utils.py:268-269`).

### 6.3 OrthoStudio XP's own texts that are wrong or stale

- `config/models.py:38-42`, hint of `default_zl`: "ZL16 is about 2.4 m/px at mid latitudes".
  2.39 m is the equator; at 45° it is 1.7 m (`ui/geo.js:38-41` computes it right).
- `mesh/rule.py:72`: `curvature_tol` is "Maximum deviation, in metres, between the mesh and the
  DEM". It is a unitless product of an edge length and a curvature (`T4XP:7344`).
- `docs/specs/settings.md` 3: a relief file "triggers the `-r` refinement" automatically. Not
  implemented: `iterate` stays 0, and a build refuses a non-zero `iterate` (decision 0009).
- `docs/specs/textures-imprint.md` 4: "the CLI does not wire the cfg's `sea_texture_blur`". True
  of the former `osxp textures` (`cli.py:118-124`, removed by decision 0010), not of `osxp build`
  nor of the web page
  (`pipeline/build.py:1833-1836` then `:853`).
- `errors.py:210-216`, remedy of `OSM_LAKE_TREATED_AS_SEA`: "add the lake to
  `good_imagery_list`". No setting reaches that list.
- `errors.py:346-351`, remedy of `DEM_VOIDS_FILLED_WITH_ZERO`: "set fill_nodata to nearest". The
  event is also raised when `nearest` was set and the raster had 10 000 voids or more
  (`dem/dem.py:158-165`).

## 7. Open questions a quick test in X-Plane would settle

Each test builds the same tile twice, differing by one setting, and compares the same view at the
same time of day and weather. Suggested tiles: `+43+005` (Marseille: sea, calanques, Étang de
Berre, 7 ICAO airports), `+46+006` (Geneva: big lake, Jura, LSGG), `+45+006` (Annecy: lake under
200 km², Alps).

1. **Q1, water near the coast (`water_rendering`).** `+43+005`, ZL16, `XP11 + bathy` against
   `XP12`. Look at the Calanques and the Vieux-Port at 1 000 ft, then from 10 000 ft and 30 km
   away. Settles which option to recommend and the two pictures of 2.8: do the photo's
   shallow-water colours appear under X-Plane 12's waves in `XP12`, and does the coastal photo
   vanish beyond 25 km in `XP11 + bathy` only?
2. **Q2, lakes (`ratio_water`).** `+45+006` at 10, 25 and 50 %. If 10 % looks almost like the
   photo, 25 % mostly like the photo and 50 % mostly like X-Plane water, the reading of 3.9 is
   right and its labels hold. If 50 % still looks mostly like the photo, or 25 % already like
   plain X-Plane water, X-Plane reads `water_transition.png` another way and the labels must be
   recomputed from the observed opacities.
3. **Q3, terrain shadows (`terrain_casts_shadows`).** `+45+006`, true against false, low evening
   sun: do the mountains stop casting shadows into the valleys, and does the frame rate move?
4. **Q4, slope shading (`normal_map_strength`).** Same tile, 1 against 0, low sun: does 0 remove
   a doubled shading on slopes, and what happens to shadows?
5. **Q5, decal (`use_decal_on_terrain`).** Build with true; does `Log.txt` report a missing
   `lib/g10/decals/maquify_2_green_key.dcl`, and is any grain visible on grass at 20-50 ft?
6. **Q6, photo over water at a distance (`overlay_lod`).** `+46+006` at FL100 with 25 and
   50 km: from what distance do the lake tint and the coastal photo disappear, and what do frame
   rate and video memory do?
7. **Q7, separate masks (`imprint_masks_to_dds`).** `+43+005` ZL16, true against false: video
   memory in use over Marseille, and any visible difference at the coast.
8. **Q8, roads on slopes (`road_level`).** A hilly tile (Jura on `+46+006`), 0 against 1: along a
   road cut into a slope, do X-Plane's roads float or sink with 0 and lie flat with 1?
9. **Q9, shallow water (`distance_masks_too` with `ratio_bathy`).** `+43+005`, `XP12` water,
   distance masks on against off: the colour of the water in the first 50 m off a beach and
   around a pier.
10. **Q10, power lines (`ovl_exclude_net`).** List the road types of `roads_EU.net` used in the
    DSFTool text of an XP12 Global Scenery tile, exclude the power-line candidate, and check that
    the pylons disappear and nothing else does.
11. **Q11, big lake (`max_area`).** `+46+006` with 200 (Lake Geneva like the sea) against 1 000
    (photo over the whole lake): which looks better from 3 000 ft and from FL100?
12. **Q12, coast precision and airport edge (`mask_zl`, `airports.zoom_level`).** Nice
    (`+43+007`), ZL16 with ICAO airports at ZL18, `mask_zl` 14 against 16: blocky or shifted
    fade along the runway by the sea, and how visible the ZL16/ZL18 sharpness step is at 500 ft
    and at 3 000 ft.
13. **Q13, coast look (profile and width).** One coastal spot with `sand` 100, `rocks` 100 and
    `sand` 200: pick the pictures of 2.6 and 2.7, and check whether the step of `rocks` at the
    shoreline reads as a rocky edge or as a defect.
14. **Q14, cliffs (`sea_smoothing_mode`).** The Calanques, `zero` against `none`: a step at the
    foot of the cliffs, or a tilted water surface?
15. **Q15, airport ground (`apt_smoothing_pix`).** LSGG on `+46+006`, 8 against 0: taxi and
    take off; bumps on the taxiways and the runway?
16. **Q16, mesh density on X-Plane's relief (`curvature_tol`).** `+45+006`, 2 against 1: the
    triangle counts in the build report, the look of the ridges, and the frame rate. No visible
    gain means the presets keep it out.
17. **Q17, relief file smaller than the tile (`relief.file`).** A GeoTIFF over half of a tile:
    are the heights of the uncovered half stretched from the file's edge, as the code says, and
    how does the tile meet its neighbour there?
18. **Q18, detail above `mesh_zl`.** `mesh_zl` 18 with ICAO airports at ZL19: smeared or misplaced
    textures on the airports would confirm that OrthoStudio XP must refuse a zoom level
    above `mesh_zl`.
19. **Q19, sea blur (`sea_texture_blur`, OrthoStudio XP extension).** `+43+005`, 0 against 10: are
    frozen waves and boats in the photo softened near the shore, and does the blur show an edge?

## 8. Gaps found while reading, for the implementation

Not settings wording, but the page will meet them:

- **Zoom level above `mesh_zl`**: `airports.zoom_level`, and a base zoom level of 20 with a
  provider whose `max_zl` is 20, are accepted by the model, the API and the DSF
  (`config/models.py:71, 128, 185`, `api/specs.py:176-181`, `dsf/zones.py:242-245`); only map
  zones are refused (`zones.py:550-567`).
- **`mask_zl` above the lowest zoom level** of a coastal texture removes the photo over that sea
  without a message (`dsf/encode.py:215-216`); `MASK_STALE` in the texture stage never fires,
  because the DSF has already made that sea plain water.
- **`airports.zoom_level` against the provider**: only the base zoom level is checked against
  `max_zl` (`api/specs.py:176-181`).
- **`airports.mode = existing`** passes validation and fails at build time
  (`pipeline/build.py:754-765`).
- **The plan ignores the airport cover** until a DSF exists: `estimate._tile_textures` counts the
  tile and its zones only (`estimate.py:274-298`). With `icao` preselected, the page would
  underestimate disk and download by up to 60 % on a tile like `+50+008`.
- **`masks_use_dem_too` and `masks_custom_extent`** silently switch the masks stage to Ortho4XP
  (`pipeline/native.py:143-146`), with another relief source in the first case and a known crash
  in the second.
- **`XP12` water with `imprint_masks_to_dds` off** is silently `XP11 + bathy`
  (`textures/ter.py:94`).
- **A relief file** that misses part of the tile is stretched, and one that misses the tile
  entirely gives a nearly flat tile without refusal (the clamp of `dem/dem.py:242-243`; not
  tested); a change of the file's content does not rebuild (`dem.md` 9.3).
- **`3steps`** needs three widths; switching the profile with one width is refused by the model
  (`config/models.py:97-101`), so the page must offer three fields or keep `3steps` folded.
- **An invalid saved X-Plane folder** is reported as "not detected" (`api/specs.py`
  `resolve_xplane`). Addressed 2026-09-14: step 3 of the Plan names it, and a plan or a job
  refused for it says which folder (`api.md` 2.2).

## 9. How the computed figures were produced

Nothing was built. Two short scripts called OrthoStudio XP's own functions with the repository's
virtual environment (`.venv/bin/python`, from the repository root).

Coast fade profiles (section 1.2):

```python
import numpy as np
from orthostudio.masks.profiles import blur_mask, sea_level_for
from orthostudio.imagery.grid import webmercator_pixel_size

px = webmercator_pixel_size(43.5, 14)  # 6.931 m per mask pixel
pre = np.zeros((256, 1400), dtype=np.uint8)
pre[:, :700] = 255  # land west, sea east
out = blur_mask(
    pre, masking_mode="sand", masks_width=100.0, lat=43, mask_zl=14, sea_level=sea_level_for(0.25)
)
row = np.maximum((pre > 0).astype(np.uint8) * 255, out)[128, 700:]  # Ortho4XP: land back to 255
opacity_at = lambda d: 100 * row[int(d // px)] / 255  # percent of photo d metres offshore
```

Airport cover (sections 2.3-2.5 and 5), measured before decision 0010 removed the frozen Ortho4XP
build and its pickle reader, so this snippet no longer runs as it is:

```python
import sys
from pathlib import Path
from orthostudio.dsf.params import DsfParams
from orthostudio.dsf.zones import AirportCover, texture_map
from orthostudio.model import TileRef

sys.path.insert(0, "tests")
from ortho4xp_apt import load_legacy_apt  # the frozen Ortho4XP pickle, through its allow-list

tile = TileRef.parse("+43+005")
dico = load_legacy_apt(Path("fixtures/large/oracle/+43+005_zl14_BI/build/Data+43+005.apt"))
airports = [
    AirportCover(*entry["boundary"].bounds, entry["key_type"] == "icao") for entry in dico.values()
]


def textures(mode, zl=16, cover_zl=18, extent=1.0):
    p = DsfParams(
        default_zl=zl, cover_airports_with_highres=mode, cover_zl=cover_zl, cover_extent=extent
    )
    tm = texture_map(tile, p, airports=airports)
    return len(
        set(zip(tm.tex_x.ravel().tolist(), tm.tex_y.ravel().tolist(), tm.zl.ravel().tolist()))
    )


extra = textures("ICAO") - textures("False")  # +76 on +43+005
```

The texture map counts every texture of the tile, open sea included, so its base counts are upper
bounds of what a DSF uses (221 against the 179 measured at ZL16 on `+43+005`); the extra textures of
the airport cover are the difference of two such counts. The `water_transition.png` values of
3.9 were read from `orthostudio.masks.profiles.WATER_TRANSITION` at rows `round((1 − r) × 127)`
(bottom-up reading) and checked against `Utils/water_transition.png` of Ortho4XP.
