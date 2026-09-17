# Settings: the typed configuration (essential / advanced / expert)

Status: P2b, written before the code of `src/orthostudio/config/`. Tests: `tests/test_config_*.py`.
Origin: Ortho4XP `O4_Config_Utils.py:16-352` (`cfg_vars`: names, defaults, types, allowed values and
the author's hints); which parameters survive, at which level and with which unit is the rewrite's
choice, recorded here.

## 1. Scope and decisions

Ortho4XP keeps 58 flat variables in `Ortho4XP.cfg` (application + tile) and `Ortho4XP_<tile>.cfg`
(44 tile variables), read by `exec()` on each line. OrthoStudio XP replaces this with **one typed
TOML file**, `~/.orthostudio/config.toml` (root from `pipeline.home.osxp_home()`, so `$OSXP_HOME`
applies), validated by frozen pydantic models organised in three levels: the user sees the essential
parameters first, then 14 advanced ones, and the 19 expert ones only on request.

Decisions:

- **Defaults are the Ortho4XP defaults**, value for value, after unit conversion where the unit
  changes (D2). A user who never opens the settings gets exactly the Ortho4XP tile.
- **Hints are the author's hints**, copied verbatim from `cfg_vars` (English). They are what the
  UI shows as tooltip. The i18n dictionary of the UI may translate them; this module carries
  the English original as the single source. Three parameters have an empty hint in Ortho4XP
  (`default_website`, `default_zl`, the `zone_list` that disappears); OrthoStudio XP writes its own,
  marked "(OrthoStudio XP)" in the table.
- **Bounds and enumerations** come from the `values` tuples of `cfg_vars` where Ortho4XP declares
  them (`road_level`, `mesh_zl`, `mask_zl`, `sea_smoothing_mode`, `masking_mode`,
  `cover_airports_with_highres`, `water_tech`). Where Ortho4XP declares none, OrthoStudio XP adds
  only the bound that makes the value meaningful (non-negative counts and distances, `[0, 1]` for
  ratios, zoom levels within what the providers and `mesh_zl` allow). Every bound is listed in
  section 2; none is stricter than what the Ortho4XP GUI let the user type.
- **One file, three tables** (`[essential]`, `[advanced]`, `[expert]`), not a separate
  `expert.toml`: one file is simpler to back up, to import and to show as "inherited / overridden"
  in the UI; the level is still explicit in the table name.
- **No `exec`, no `eval`** anywhere: `tomllib` reads, a 40-line emitter writes (no dependency;
  `tomli_w` is not installed).
- **`region` is not a setting**: the map's zones live in `zones.json` (`map-zones.md`). The
  essential level has 8 groups; `airports`, `coast_transition` and `relief` are sub-models, so it
  holds 14 leaf fields.

### 1.1 The count

The plan of the rewrite announced 8 / 14 / 20. Counting the leaves of the models:

| Level | Planned | Leaves here | Note |
|---|---|---|---|
| essential | 8 | 14 | 8 groups, `region` excepted, `overlays` and `data_dir` added; `airports` has 3 leaves, `coast_transition` 2, `relief` 3 |
| advanced | 14 | 14 | |
| expert | 20 | 19 | the plan's expert list named 19 parameters under the heading "20" |
| total | 42 | 47 | |

The 19 expert parameters plus the 14 advanced plus the 11 Ortho4XP variables behind the
essential level (section 2) make 44 = the 58 `cfg_vars` minus the 14 that disappear
(section 3). Nothing of Ortho4XP is lost or counted twice.

## 2. The parameters

Columns: OrthoStudio XP name (path in the TOML file), type and validation, unit, default, the
Ortho4XP variable, and the hint (verbatim from `cfg_vars` unless marked "(OrthoStudio XP)"). Unit
"-" means dimensionless. `E` = enumeration, `[a, b]` = inclusive bounds.

### 2.1 Essential (`[essential]`)

| OrthoStudio XP | type / validation | unit | default | Ortho4XP | hint |
|---|---|---|---|---|---|
| `provider` | str, non-empty | - | `BI` | `default_website` | (OrthoStudio XP) Code of the imagery provider (see `osxp doctor --providers` for the ones alive and their maximum zoom level). |
| `zoom_level` | int `[10, 20]` | ZL | 16 | `default_zl` | (OrthoStudio XP) Zoom level of the imagery over the whole tile: ZL16 is about 2.4 m/px at mid latitudes, each level doubles the resolution and quadruples the download; `mesh_zl` caps it. |
| `airports.mode` | E `off` / `on` / `icao` / `existing` | - | `off` | `cover_airports_with_highres` (`False` / `True` / `ICAO` / `Existing`) | When set, textures above airports will be upgraded to a higher zoomlevel, the imagery being the same as the one they would otherwise receive. Can be limited to airports with an ICAO code for tiles with so many airports. Exceptional: use "Existing" to (try to) derive custom zl zones from the textures directory of an existing tile. |
| `airports.zoom_level` | int `[14, 20]` | ZL | 18 | `cover_zl` | The zoomlevel with which to cover the airports zone when high_zl_airports is set. Note that if the cover_zl is lower than the zoomlevel which would otherwise be applied on a specific zone, the latter is used. |
| `airports.extent_km` | float `>= 0` | km | 1.0 | `cover_extent` | The extent (in km) past the airport boundary taken into account for higher ZL. Note that for VRAM efficiency higher ZL textures are fully used on their whole extent as soon as part of them are needed. |
| `coast_transition.profile` | E `sand` / `rocks` / `3steps` | - | `sand` | `masking_mode` | A selection of three tentative masking algorithms (still looking for the Holy Grail...). [...] The transition with rocks is more abrupt than with sand. |
| `coast_transition.width_m` | float `>= 0`, or a list of three floats `>= 0` when the profile is `3steps` (a scalar is required by `sand` / `rocks`) | m | 100 | `masks_width` | Maximum extent of the masks perpendicularly to the coastline (rough definition). NOTE: The value is now in meters, it used to be in ZL14 pixel size in earlier verions, the scale is roughly one to ten between both. |
| `water_rendering` | E `XP11 + bathy` / `XP12` | - | `XP11 + bathy` | `water_tech` | Water tech type. XP12 uses a new (partly in construction) rendering tech, XP11 + bathy uses a more traditionnal blend. Both allows for 3D water. |
| `relief.source` | E `auto` / `file` / `copernicus` | - | `auto` | `custom_dem` (empty = auto) | Path to an elevation data file to be used instead of the default Viewfinderpanoramas.org ones (J. de Ferranti). [...] |
| `relief.file` | str (path; required non-empty when `source` is `file`) | - | `""` | `custom_dem` | same hint |
| `relief.fill_nodata` | E `nearest` / `zero` | - | `nearest` | `fill_nodata` (`True` = nearest) | When set, the no_data values in the raster will be filled by a nearest neighbour algorithm. If unset, they are turned into zero (can be useful for rasters with no_data over the whole oceanic part or partial LIDAR data). |
| `overlays` | E `xplane` / `none` | - | `xplane` | - (OrthoStudio XP) | Roads, railways, power lines, forests and buildings over the photo tiles, taken from X-Plane's own scenery into `yOrthoStudio_Overlays`. `none` builds none (the page's builds: `BuildSpec.overlay = False`; `osxp build` keeps `--overlay/--no-overlay`) and takes a tile's own out of X-Plane when it is built again, for simHeaven X-World or another pack that brings them (user request, 2026-09-13; `install.md` 3). |
| `xplane_dir` | str or absent (`None` = detected by `install.detect_xplane`) | - | `None` | `custom_scenery_dir` (its parent) | Your X-Plane Custom Scenery. Used only for "1-click" creation (or deletion) of symbolic links from Ortho4XP tiles to there. |
| `data_dir` | str or absent (`None` = `$OSXP_HOME`); `PUT /api/settings` accepts a new folder only when `orthostudio.home.check_data_dir` does (absolute, found, writable, outside X-Plane's `Custom Scenery`, on a disk that hard-links files) and no build runs or waits | - | `None` | - (OrthoStudio XP) | The folder of the tiles OrthoStudio XP builds, of the imagery it downloads and of its caches, several GB per tile: on an external disk, for instance (user request, 2026-09-15; `pipeline-textures.md` 2). Empty: OrthoStudio XP's own folder. Its disk must hard-link files (APFS, Mac OS Extended, NTFS, ext4; not exFAT or FAT32). What was downloaded before stays where it is. |

`region` (map, replaces `lat`/`lon`/`zone_list`) is P5.

### 2.2 Advanced (`[advanced]`)

| OrthoStudio XP | type / validation | unit | default | Ortho4XP | hint |
|---|---|---|---|---|---|
| `curvature_tol` | float `> 0` | - | 2 | `curvature_tol` | This parameter is intrinsically linked the mesh final density. [...] A higher curvature tolerance yields fewer triangles. |
| `limit_tris` | float `[0, 5]` | M triangles | 3 | `limit_tris` | If non zero, approx upper bound _in millions_ on the number of final triangles in the mesh. Note: When 0 we impose a hard limit of 5M, to keep X-Plane comfortable. For high resolution DEMS you _should_ use it. |
| `road_level` | int E `0..5` | - | 1 | `road_level` | Allows to level the mesh along roads and railways. Zero means nothing such is included; "1" looks for banking ways among motorways, primary and secondary roads and railway tracks; "2" adds tertiary roads; "3" brings residential and unclassified roads; "4" takes service roads, and 5 finishes with tracks. [...] |
| `min_area` | float `>= 0` | km² | 0.001 | `min_area` | Minimum area (in km^2) a water patch needs to be in order to be included in the mesh as such. Contiguous water patches are merged before area computation. |
| `max_area` | float `>= 0` | km² | 200 | `max_area` | Any water patch larger than this quantity (in km^2) will be masked like the sea. |
| `sea_smoothing_mode` | E `zero` / `mean` / `none` | - | `zero` | `sea_smoothing_mode` | Zero means that all nodes of sea triangles are set to zero elevation. With mean, some kind of smoothing occurs [...] |
| `water_smoothing` | int `>= 0` | passes | 10 | `water_smoothing` | Number of smoothing passes over all inland water triangles (sequentially set to their mean elevation). |
| `apt_smoothing_pix` | int `>= 0` | px | 8 | `apt_smoothing_pix` | How much gaussian blur is applied to the elevation raster for the look up of altitude over airports. Unit is the evelation raster pixel size. |
| `ratio_water_pct` | float `[0, 100]` | % | 25 | `ratio_water` (× 0.01) | Inland water rendering is made of two layers [...] At zero, the orthophoto is fully opaque and X-Plane water cannot be seen ; at 1 the orthophoto is fully transparent and only the X-Plane water is seen. |
| `ratio_bathy` | float `[0, 1]` | - | 1.0 | `ratio_bathy` | Bathymetry multiplier for near shore vertices. In the range [0,1]. |
| `use_masks_for_inland` | bool | - | false | `use_masks_for_inland` | Will use masks for the inland water (lakes, rivers, etc) too, instead of the default constant transparency level determined by ratio_water. This is VRAM expensive and presumably not really worth the price. |
| `imprint_masks_to_dds` | bool | - | true | `imprint_masks_to_dds` | Will apply masking directly to dds textures [...] |
| `terrain_casts_shadows` | bool | - | true | `terrain_casts_shadows` | If unset, the terrain itself will not cast (but still receive!) shadows. [...] |
| `overlay_lod_km` | float `>= 0` | km | 25 | `overlay_lod` (× 1000, metres) | Distance until which overlay imageries (that is orthophotos over water) are drawn. [...] |

### 2.3 Expert (`[expert]`)

| OrthoStudio XP | type / validation | unit | default | Ortho4XP | hint |
|---|---|---|---|---|---|
| `mesh_zl` | int E `16..20` | ZL | 19 | `mesh_zl` | The mesh will be preprocessed to accept later any combination of imageries up to and including a zoomlevel equal to mesh_zl. [...] |
| `mask_zl` | int E `14..16` | ZL | 14 | `mask_zl` | The zoomlevel at which the (sea) water masks are built. [...] |
| `apt_curv_tol` | float `> 0` | - | 0.5 | `apt_curv_tol` | If smaller, it supersedes curvature_tol over airports neighbourhoods. |
| `apt_curv_ext` | float `>= 0` | km | 0.5 | `apt_curv_ext` | Extent (in km) around the airports where apt_curv_tol applies. |
| `coast_curv_tol` | float `> 0` | - | 1 | `coast_curv_tol` | If smaller, it supersedes curvature_tol along the coastline. |
| `coast_curv_ext` | float `>= 0` | km | 0.5 | `coast_curv_ext` | Extent (in km) around the coastline where coast_curv_tol applies. |
| `min_angle` | float `[0, 30]` | ° | 10 | `min_angle` | The mesh algorithm will try to not have mesh triangles with (smallest for water / second smallest for regular land) angle less than the value (in deg) of min_angle. |
| `road_banking_limit` | float `>= 0` | m | 0.5 | `road_banking_limit` | How much sloped does a roads need to be to be in order to be included in the mesh levelling process. [...] |
| `lane_width` | float `> 0` | m | 4 | `lane_width` | With (in meters) to be used for buffering that part of the road network that requires levelling. |
| `max_levelled_segs` | int `>= 0` | segments | 200000 | `max_levelled_segs` | This limits the total number of roads segments included for mesh levelling [...] |
| `water_simplification` | float `>= 0` | m | 0 | `water_simplification` | In case the OSM data for water areas would become too large, this parameter (in meter) can be used for node simplification. |
| `masks_use_dem_too` | bool | - | false | `masks_use_DEM_too` | If you have acces to high resolutions DEMs [...] |
| `masks_custom_extent` | str | - | `""` | `masks_custom_extent` | Yet another tentative to draw masks with maximizing the use of the good imagery part. [...] |
| `distance_masks_too` | bool | - | false | `distance_masks_too` | This will additionally build distance to coastline masks [...] |
| `sea_texture_blur` | float `>= 0` | m | 0 | `sea_texture_blur` | For layers of type "mask" in combined providers imageries, determines the extent (in meters) of the blur radius applied. [...] |
| `normal_map_strength` | float `[0, 1]` | - | 1 | `normal_map_strength` | Orthophotos by essence already contain the part of the shading burned in [...] the default is now 1 which means exact normals. |
| `patches_dir` | str | - | `""` | – | Folder of hand-made mesh patches (`<tile>/*.patch.osm`, files written with JOSM; patches published for Ortho4XP fit as they are). Empty: `$OSXP_HOME/patches` when that folder exists, else none. |
| `use_decal_on_terrain` | bool | - | false | `use_decal_on_terrain` | Terrain files for all but water triangles will contain the maquify_1_green_key.dcl decal directive. [...] |
| `decal_on_sea` | bool | - | false | – | The decals go on land only. With this on they go on the sea as well, as Ortho4XP writes them; lakes and rivers never have them. |
| `ovl_exclude_pol` | list of int or str | - | `[0]` | `ovl_exclude_pol` | Indices of polygon types which one would like to left aside in the extraction of overlays. [...] |
| `ovl_exclude_net` | list of int or str | - | `[]` | `ovl_exclude_net` | Indices of road types which one would like to left aside in the extraction of overlays. [...] |

Where a hint is abbreviated with "[...]" above, the code carries the full text.

## 3. What disappears (18 variables, 14 of them `cfg_vars`)

| Ortho4XP | Replaced by |
|---|---|
| `max_convert_slots` | the CPU pool of the scheduler (`sched`, all cores) |
| `masks_build_slots` | idem (this name is not in `cfg_vars` Ortho4XP; it was a GUI-era variable) |
| `max_threads` | the network pool of `orthostudio.net` (64-128 in flight, per provider; not in `cfg_vars`, it lives in the provider `.lay` files) |
| `http_timeout` | `net` constants with hedging (ADR 0005) |
| `max_connect_retries`, `max_baddata_retries` | per-host backoff in `net`; missing textures become `TEX_MISSING` + "retry the missing ones" |
| `check_tms_response` | always on: a server error never yields a silent white texture (`errors.md`) |
| `overpass_server_choice` | the mirror list of ADR 0005 with automatic fail-over |
| `verbosity` | structured logs and the job journal (`api`) |
| `cleaning_level` | the content-addressed store: nothing is "cleaned", artefacts are keyed and garbage-collected by quota |
| `skip_downloads`, `skip_converts` | the DAG builds only what is missing; there is no step to skip |
| `custom_overlay_src` | `install.detect_xplane()` + Global Scenery detection (`pipeline.build.resolve_global_scenery`), or an explicit choice in the API request |
| `custom_build_dir` (and its trailing-`/` grouped mode) | `BuildSpec.out_dir`; the library records where each pack is |
| `clean_bad_geometries` | always on (the GEOS noder of P4 validates geometries) |
| `iterate` | automatic: a local DEM (`relief.source = "file"`) triggers the `-r` refinement (P3) |
| `lat` / `lon` | the tile list of the request (`tiles: ["+43+005"]`), later the region (P5) |
| `zone_list` | the ZL zones of the map (P5) |

`custom_scenery_dir` is not dropped: it becomes `essential.xplane_dir` (its parent folder).

## 4. Behaviour

### 4.1 Models (`models.py`)

`Settings(essential: Essential, advanced: Advanced, expert: Expert)`; every model is frozen
(`model_config = ConfigDict(frozen=True, extra="forbid")`) so a loaded configuration cannot be
mutated by a build step; changes go through
`model_copy(update=...)` and `save_settings`. Each field carries
`Field(default, description=<hint>, json_schema_extra={"unit", "hint", "level", "ortho4xp"})`;
`ortho4xp` is the Ortho4XP variable name, or `None` for a field with no Ortho4XP counterpart.

Cross-field rules (model validators): `coast_transition.width_m` must be a scalar for
`sand`/`rocks` and a list of exactly three values for `3steps` (Ortho4XP `masking_mode` hint);
`relief.file` must be non-empty when `relief.source == "file"`.

### 4.2 File (`store.py`)

`load_settings(path=None) -> Settings`: `path` defaults to `<osxp_home>/config.toml`. An absent file
returns the defaults (no error, nothing written). A file that is not valid TOML raises
`CFG_LINE_INVALID` (path, line from `tomllib`'s message). Unknown keys are ignored with a `logging`
warning (a file written by a newer OrthoStudio XP still loads). A value that fails validation raises
`CFG_VALUE_INVALID` with `name` = the dotted path (`advanced.road_level`), `value`, `type` and
`range` from the schema.

`save_settings(settings, path=None) -> None`: emits TOML with the three tables in order and
the fields in model order, writes atomically (`fsutil.atomic_write_text`: temp file + rename)
and keeps the previous file as `config.toml.bak` before replacing it. `None` values (`xplane_dir`
and `data_dir` today) are omitted. Round trip: `load_settings(p) == s` after `save_settings(s, p)`
for any valid `s`.

The emitter (`toml_dumps`) supports what the models need: str, bool, int, float (including
`inf`/`nan` as TOML spells them), lists of scalars, nested dicts as tables. Strings are
always basic (double-quoted) with escapes for `"`, `\` and control characters. Keys are
bare when they match `[A-Za-z0-9_-]+`, quoted otherwise.

### 4.3 Schema (`schema.py`)

The page no longer shows the Ortho4XP hints of the schema: it asks questions and gives plain labels
of its own (`ui.md` 2.4, `settings-plain-language.md`); the hints stay in the schema for other
clients and the tests.

`settings_schema() -> dict`: the pydantic JSON schema of `Settings` with every `$ref`
inlined (the UI walks a tree, not a `$defs` table), and on every leaf property the keys
`unit`, `hint`, `level`, `ortho4xp`, `default`, and `enum` when the type is a `Literal`.
Properties that are sub-models (`airports`, ...) carry `level` too. Acceptance: 47 leaves,
each with the four keys, `enum` present on the 9 enumerated fields.

### 4.4 Build overrides (`overrides.py`)

`to_build_overrides(settings) -> dict[str, object]`: the Ortho4XP tile variables (names of
`tilefiles.TILE_PARAMETERS`) and the two overlay settings, typed as `BuildSpec.config`
expects them (`pipeline/build.py`, `BuildSpec.tile_config`):

- `cover_airports_with_highres` in `{"False", "True", "ICAO", "Existing"}` from `airports.mode`;
  `cover_zl`, `cover_extent`;
- `masking_mode`, `masks_width` (an int/float, or the list of three for `3steps`);
- `water_tech`; `custom_dem` (`""` when `relief.source == "auto"`, `"COP30"` when `copernicus`);
  `fill_nodata` (bool);
- `ratio_water = ratio_water_pct / 100`; `overlay_lod = overlay_lod_km * 1000`;
- every other advanced/expert field under its Ortho4XP name (`masks_use_DEM_too`);
- `ovl_exclude_pol` / `ovl_exclude_net` as lists.

Not emitted: `default_website` / `default_zl` (they are `BuildSpec.provider` / `.zl`, set
per request; emitting them here would silently override the request), `zone_list`,
`iterate`, `clean_bad_geometries` (their Ortho4XP defaults apply), and `xplane_dir` (it is
`BuildSpec.custom_scenery`, resolved by the caller). Acceptance: `BuildSpec(..., config=
to_build_overrides(Settings())).tile_config()` equals `tile_defaults()` for the emitted keys;
every emitted value has the type `TILE_PARAMETERS` declares (or, for `masks_width`, the
int/float/list quirk); ints are never emitted where Ortho4XP declares float and vice versa.

### 4.5 Import of an Ortho4XP configuration (removed)

`import_legacy_cfg`, which turned an `Ortho4XP.cfg` or an `Ortho4XP_<tile>.cfg` into `Settings`,
had no command or page behind it; decision 0010 removed it with the other readers of an Ortho4XP
folder. The import of Ortho4XP *tiles* reads their tile cfg (`install.md` 5).

## 5. Errors

| Situation | Code |
|---|---|
| `config.toml` is not valid TOML | `CFG_LINE_INVALID` |
| a value fails validation (load or `Settings(...)`) | `CFG_VALUE_INVALID` |
| the file cannot be written | `CFG_TILE_WRITE_FAILED` (the registry's write error for configurations) |

## 6. Wanted differences from Ortho4XP

- `ratio_water` is edited in percent, `overlay_lod` in kilometres; the build
  receives the Ortho4XP units.
- `masks_width` keeps the Ortho4XP dual shape (scalar or three values) but is validated against
  the profile at load time; Ortho4XP discovered the mismatch at mask time with a traceback.
- `fill_nodata` is an explicit choice (`nearest` / `zero`) instead of a boolean.
- `custom_dem` splits into `source` + `file`, so the path is kept when switching back to
  `auto`.
- `cover_airports_with_highres` values are lower-case words; the build receives the Ortho4XP
  capitalised strings.
