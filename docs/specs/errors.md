# Errors: silent degradations of Ortho4XP and the OrthoStudio XP error codes

Status: P0 inventory, written before `orthostudio.errors` (this spec) and implemented by
`src/orthostudio/errors.py`. Origin lines refer to Ortho4XP at commit `26ec00a` (its GitHub master
branch, March 2026). Test: `tests/test_errors_registry.py` checks that this table and the registry
agree.

## Why

Ortho4XP has 180 bare `except:` and reports most failures with a `print` at verbosity 1,
a `return 0` that the caller ignores, or nothing at all. The consequences reach the user in
X-Plane, minutes later, with no link to the cause: white textures, a tile without sea, a flat
tile at 0 m, an airport that is not flattened, a DSF without the XP12 elevation rasters, a GUI
frozen because a worker thread died with `UI.is_working` still set. The command line
(`Ortho4XP.py:66-73`) prints `Crash!` and exits with code 0.

OrthoStudio XP replaces all of this by one exception type carrying a **stable code**, a message, a
remedy, a severity and a machine-readable context. The UI shows "code + action", the API emits the
JSON rendering, the decision report lists every `continue` decision taken for a tile.

## Vocabulary

| Field | Values | Meaning |
|---|---|---|
| `severity` | `blocking` | the output of this node cannot be correct; nothing downstream for this tile is built |
| | `degraded` | an output is produced but differs from what was asked; listed in the decision report |
| | `info` | a decision OrthoStudio XP took on its own that the user may want to know |
| `action` | `stop` | the graph node fails; dependants of this tile are skipped; other tiles continue |
| | `continue` | the node succeeds; the event is recorded and shown |

Code format: `DOMAIN_SNAKE_CASE`, domains
`OSM_ DEM_ MESH_ MASK_ IMG_ TEX_ DSF_ XP_ CFG_ NET_ SYS_
ZONE_`. Codes are stable once published: a code is never renamed or reused, only deprecated.

Rows marked *by design* describe situations OrthoStudio XP prevents structurally (content-addressed
keys, typed configuration, DAG ordering). Their codes exist so that `osxp import-ortho4xp` and
`osxp doctor` can diagnose a legacy build directory with the same vocabulary.

Column "Origin in Ortho4XP": `file.py:first-last` inside Ortho4XP's `src/` (or its root for
`Ortho4XP.py`); `new` means no Ortho4XP counterpart exists.

## Inventory

### OSM: vector data from Overpass and its interpretation

| Code | Origin in Ortho4XP | Condition | Effect in Ortho4XP today | Severity | OrthoStudio XP | Remedy shown to the user |
|---|---|---|---|---|---|---|
| OSM_MIRROR_UNREACHABLE | O4_OSM_Utils.py:569-577 | Connection error or 60 s timeout on the chosen Overpass mirror (3 of the 4 hard-coded mirrors are dead) | Same mirror retried with 2^n s waits, 8 tries = 5 min 40 s per query, 4 to 17 sequential queries per tile; one line at verbosity 1 | info | continue | Another server is tried at once, and the whole list again after a pause. |
| OSM_MIRROR_REJECTED | O4_OSM_Utils.py:559-568 | HTTP status other than 200 (429, 504) from the mirror | Same blind backoff on the same mirror | info | continue | Waiting before retrying on another mirror; reduce parallel OSM requests if it persists. |
| OSM_MIRROR_RATE_LIMITED | O4_OSM_Utils.py:559-568 | HTTP 429 from a mirror: the per-address quota of the public servers, reached by a user who built a whole state in one go (2026-09-23) | Same blind backoff, nothing said | info | continue | These servers count requests per internet address, and a whole region is hundreds of them: wait a few minutes and build again, or build fewer tiles at a time. Another server is tried meanwhile. |
| OSM_RESPONSE_TRUNCATED | O4_OSM_Utils.py:540-548; O4_OSM_Utils.py:262-270 | Body does not end with `</osm>` (transfer cut) | Retried when downloading; when read from the `.osm.bz2` cache the layer is dropped with `return 0` and the tile is built without it | info | continue | The query is retried; a truncated cached file is deleted and fetched again. |
| OSM_RESPONSE_ERROR | O4_OSM_Utils.py:548-556 | Body of at most 1000 bytes containing `error` (query too large, server runtime error) | Retried unchanged | info | continue | The bounding box is split into smaller queries. |
| OSM_LAYER_UNAVAILABLE | O4_OSM_Utils.py:444-458; O4_OSM_Utils.py:578-579; O4_Vector_Map.py:401; O4_Vector_Map.py:541; O4_Vector_Map.py:277; O4_Vector_Map.py:195 | Every try failed for one layer: `OSM_queries_to_OSM_layer` returns 0, the calling `include_*` returns 0 and `build_poly_file` carries on | Step 1 ends with "normal exit" and the tile has **no sea**, or no lakes, or no roads. For the airport layer, `tile.dem` is never loaded and `include_roads` crashes on `None`: traceback in the console, `UI.is_working` stays set and the GUI is frozen | blocking | stop | Build again: a new build asks every source and every server afresh. If it keeps failing, the public servers are having a bad day; wait a while, or build fewer tiles at a time. The tiles already finished are kept. |
| OSM_LIBRARY_KEY_REFUSED | new | The prepared library answers 401 or 403: the key this version carries is not the one the server now takes | No counterpart: Ortho4XP has no prepared library | info | continue | This version's key is no longer accepted: the map data is downloaded from the public servers instead, which is slower but gives the same scenery. Updating OrthoStudio XP brings a current key. |
| OSM_LIBRARY_UNREACHABLE | new | The library's manifest could not be read at all (no answer, 5xx, a document that is not one of ours) | No counterpart | info | continue | The map data is downloaded from the public servers instead, which is slower but gives the same scenery. Nothing to do. |
| OSM_LIBRARY_INCOMPLETE | new | A tile the manifest lists and the library then refuses, or serves as a file that is not the one announced: an upload in progress, a rebake under our feet | No counterpart | degraded | continue | The library is left alone for the rest of this build and the map data comes from the public servers. If it keeps happening, the library is being rebuilt: build again later. |
| OSM_PREPARED_SET_ASIDE | new | A prepared source failed twice in one build and is not asked again for it | No counterpart | info | continue | Nothing to do: the scenery is the same, the download is slower. A folder or an address in Settings that names nothing is the usual cause. |
| OSM_PREPARED_FOLDER_MISSING | new | The folder named in Settings does not exist, so the setting does nothing and every tile goes to the public servers | No counterpart | info | continue | Point the setting at the folder that holds the prepared layers, or clear it: an empty setting sends every tile to the public servers, which is what happens now anyway. |
| OSM_CACHE_UNREADABLE | O4_OSM_Utils.py:64-76 | Cached `.osm.bz2` cannot be opened (corrupted, interrupted write) | "Could not open ... (corrupted ?)", `return 0`, layer silently dropped | degraded | continue | The corrupted cache file is deleted and the layer downloaded again. |
| OSM_CACHE_WRITE_FAILED | O4_OSM_Utils.py:284-292 | Cannot write an OSM snapshot (disk full, permissions); Ortho4XP's case was its `.osm.bz2` cache | Message at verbosity 1, data used once and downloaded again next run | info | continue | Free disk space or fix permissions on the cache directory; the build continues. |
| OSM_COAST_OPEN_END | O4_Vector_Utils.py:897-924 | A `natural=coastline` way ends inside the tile (unfinished coastline in OSM) | "ERROR in OSM coastline data" at verbosity 1, returns an empty polygon: no SEA seed, the sea is rendered as land ("tile without sea") | blocking | stop | Fix the coastline in OpenStreetMap at the given positions, then build this tile again. |
| OSM_COAST_ORIENTATION | O4_Vector_Utils.py:941-947 | Boundary walk loops 1000 times: a coastline way is drawn with water on the left | Same: empty sea polygon, tile without sea | blocking | stop | Reverse the faulty way in OSM/JOSM or use the land-polygons coastline. |
| OSM_COAST_TRIPLE_JUNCTION | O4_Vector_Utils.py:949-960 | Three coastline ways meet at one node | Same: empty sea polygon, tile without sea | blocking | stop | Fix the junction in OpenStreetMap, then build this tile again. |
| OSM_WAY_NOT_CLOSED | O4_OSM_Utils.py:652-659 | A water or airport way used as a polygon is not closed | Written to Log.txt only; the lake or apron is missing | info | continue | Nothing to do; the feature is listed in the decision report. |
| OSM_WAY_INVALID | O4_OSM_Utils.py:675-686 | Polygon is self-intersecting or has zero area | Log.txt only; the feature is missing | info | continue | Nothing to do; correct the geometry in OSM if it matters. |
| OSM_RELATION_INVALID | O4_OSM_Utils.py:692-756 | A multipolygon relation yields an invalid ring after union/difference | Log.txt only; the whole relation (lake, riverbank) is missing | info | continue | Nothing to do; listed in the decision report. |
| OSM_WATER_MERGE_FAILED | O4_Vector_Map.py:548-553; O4_Vector_Map.py:570-575 | `MultiPolygon_to_Indexed_Polygons` raises while merging overlapping water polygons | Bare `except: return 0`: **all inland water of the tile is dropped**, Step 1 still says "normal exit" | blocking | stop | Retry with `clean_bad_geometries` disabled, or provide a custom water file for the tile. |
| OSM_LAKE_TREATED_AS_SEA | O4_Vector_Map.py:454-517 | An inland water body is larger than `max_area` km² | Message at verbosity 1: masked and rendered like the sea | info | continue | Raise `max_area` or add the lake name to `good_imagery_list` to keep the orthophoto. |
| OSM_AIRPORT_TAG_INVALID | O4_Airport_Utils.py:165-175 | An `aeroway=aerodrome` element has an unusable geometry | Warning at verbosity 2 (hidden by default), airport popped: not flattened, not covered at `cover_zl` | degraded | continue | Check the aerodrome element in OSM near the given point; the airport is skipped. |
| OSM_AIRPORT_BOUNDARY_INVALID | O4_Airport_Utils.py:152-164 | Aerodrome boundary is an invalid polygon | Verbosity 2: boundary set to None, the airport keeps its runways only | degraded | continue | Fix the aerodrome outline in OSM; runways are still flattened. |
| OSM_AIRPORT_TOO_SMALL | O4_Airport_Utils.py:682-698 | Boundary under 5000 m² or runway area under 2500 m² (model aircraft, helipads) | Dropped silently | info | continue | Nothing to do; listed in the decision report. |
| OSM_RUNWAY_REJECTED | O4_Airport_Utils.py:377-401; O4_Airport_Utils.py:421-442 | Runway polygon too far from a rectangle (Hausdorff > 0.0008°) or invalid | "!Bad runway" at verbosity 1; the runway is not flattened while the rest of the airport is | degraded | continue | Edit `airports.osm.bz2` in JOSM as the message suggests, or tag the way `custom=yes` to bypass the check. |
| OSM_AIRPORT_SURFACE_INVALID | O4_Airport_Utils.py:720-728; O4_Airport_Utils.py:752-767 | A hangar or apron area cannot be turned into a polygon | Verbosity 2 only; surface skipped | info | continue | Nothing to do; listed in the decision report. |
| OSM_AIRPORT_SMOOTHING_INVALID | O4_Airport_Utils.py:105-113; O4_Airport_Utils.py:948-959 | An aerodrome's `smoothing_pix` tag is an integer outside 0..1000 | Taken as is: a negative width makes `numpy.convolve` raise (the whole Step 1 dies), a huge one builds a kernel of that size and blocks the build for minutes | degraded | continue | Fix the `smoothing_pix` tag of the aerodrome in OSM, or remove it; the tile's `apt_smoothing_pix` is used meanwhile. |
| OSM_AIRPORT_INFO_UNAVAILABLE | O4_Airport_Utils.py:835-845; O4_DSF_Utils.py:119-131; O4_Mesh_Utils.py:137-150 | The pickled airport list of Step 1 cannot be written, or was erased by `cleaning_level` | Steps 2 and 3 warn at verbosity 1 and silently ignore `cover_airports`: no high-ZL cover, no `apt_curv_tol` refinement | degraded | continue | Rebuild the vector stage; OrthoStudio XP keeps the airport list in the store with the vectors. *By design.* |
| OSM_PATCH_INVALID | O4_Vector_Map.py:657-666; O4_Vector_Map.py:826-828; O4_Vector_Map.py:858-875 | A `.patch.osm` file is unreadable, contains an invalid polygon or a malformed ANCHOR line | "skipped" at verbosity 1 or 2; the patch is silently absent from the mesh | degraded | continue | Fix the patch file (name given); the tile is built without it. |

### DEM: elevation sources

| Code | Origin in Ortho4XP | Condition | Effect in Ortho4XP today | Severity | OrthoStudio XP | Remedy shown to the user |
|---|---|---|---|---|---|---|
| DEM_DOWNLOAD_FAILED | O4_DEM_Utils.py:695-701; O4_DEM_Utils.py:828-862; O4_DEM_Utils.py:385-393 | Viewfinderpanoramas/USGS answer 404 or fail 6 times for the tile itself | 404 logged at verbosity 2 only, `ensure_elevation` returns 0 and the raster is **filled with zeros**: the whole tile is flat at 0 m; nothing is cached so the 404 is re-requested at every run | blocking | stop | Check the source URL for this cell; place a `.hgt`/GeoTIFF manually in `Elevation_data/` or choose another source. |
| DEM_TILE_UNAVAILABLE | O4_DEM_Utils.py:380-395 | The tile's own cell has no elevation data from the chosen source: X-Plane 12 Global Scenery DSF absent or unusable, archive refused (the `dem1` 404s), file unreadable | The cell is filled with zeros like a neighbour: the whole tile is flat at 0 m and nothing is said (OrthoStudio XP did the same until decision 0007) | blocking | stop | Install this region of the X-Plane 12 Global Scenery (the default relief), choose another relief source, or give your own elevation file. |
| DEM_OVERLAY_UNAVAILABLE | new | A source laid over another (`"COP30;HRDEM"`) has no data for the tile: Canada's lidar covers the part of the country that has been flown | The base relief is used alone there, which is what a composite is for | degraded | continue | Nothing to do: the source covers part of the country only, and the base relief answers for the rest. |
| DEM_OVERLAY_COARSER | new | The user's own file for this square is coarser than the relief it is laid over (a 1" file of one's own over the USGS 1/3") | The finer of the two answers, so the relief chosen is used over this square | degraded | continue | Nothing to do. Put a file at least as fine as the relief you chose in the folder, or choose a coarser relief, if you want your own file to answer here. |
| DEM_NEIGHBOUR_UNAVAILABLE | O4_DEM_Utils.py:380-395 | Same failure for one of the 8 neighbouring cells used for the 36 px margin | Silent (`verbose` is False for neighbours): margin at 0 m, altitude step along the tile border | degraded | continue | The border is smoothed from the tile's own data; provide the neighbour DEM to remove the seam. |
| DEM_SOURCE_MANUAL_DOWNLOAD | O4_DEM_Utils.py:735-745 | Source SRTM or ALOS selected (`MANUAL_SOURCES`) but the file of the tile's own cell is not present locally (no direct download since OpenTopography changed its policy); a downloaded source with no file says `DEM_TILE_UNAVAILABLE` instead | Warning at verbosity 1, then zeros: flat tile | blocking | stop | Download the file manually as `{expected_name}` into `Elevation_data/`, or switch to Viewfinderpanoramas. |
| DEM_FILE_UNREADABLE | O4_DEM_Utils.py:464-476; O4_DEM_Utils.py:488-500; O4_DEM_Utils.py:555-567 | `.hgt`, `.raw` or raster file truncated or corrupted | "replaced with zero altitude" at verbosity 1, flat tile | blocking | stop | Delete `{path}` so it is downloaded again, or replace it. |
| DEM_RASTER_LIBRARY_MISSING | O4_DEM_Utils.py:11-16; O4_DEM_Utils.py:571-582 | A GeoTIFF `custom_dem` is given but GDAL is not installed | "unsupported raster (install Gdal)" then zeros: flat tile | blocking | stop | Convert the DEM to `.hgt`, or to an uncompressed GeoTIFF. |
| DEM_NODATA_UNDECLARED | O4_DEM_Utils.py:512-518 | Raster has no nodata value in its metadata | Assumes -32768, verbosity 1 | info | continue | Nothing to do unless holes appear; declare nodata in the raster. |
| DEM_EPSG_UNDECLARED | O4_DEM_Utils.py:527-535 | Raster has no CRS | Assumes EPSG:4326, verbosity 1 | info | continue | Nothing to do if the raster is in geographic coordinates. |
| DEM_EPSG_UNSUPPORTED | O4_DEM_Utils.py:536-548 | Raster CRS is neither 4326 nor 4269 | "result is likely to be non sense" and continues: terrain shifted or scaled | blocking | stop | Reproject the DEM to EPSG:4326 (e.g. `gdalwarp -t_srs EPSG:4326`). |
| DEM_VOIDS_FILLED_WITH_ZERO | O4_DEM_Utils.py:36-44; O4_DEM_Utils.py:866-881; O4_DEM_Utils.py:894-905 | 10 000 or more nodata pixels, or holes still open after 20 dilations | INFO/WARNING at verbosity 1-2, holes set to 0 m: craters in mountains, glaciers at sea level | degraded | continue | Use a DEM without voids for this cell or set `fill_nodata` to `nearest` with a distance transform (OrthoStudio XP fills any hole). |
| DEM_CELL_ASSUMED_OCEAN | O4_DEM_Utils.py:382-383 | `world_tiles.png` marks the cell as ocean | Zeros without any message; small islands absent from that bitmap are flat | info | continue | Provide a DEM file for the cell if it is not open sea. |
| DEM_CACHE_STALE | O4_Mesh_Utils.py:576-591 | The `.alt` written by Step 1 does not match the current `custom_dem` size | Step 2 stops with a clear message | blocking | stop | Rebuild the vector stage. *By design:* the DEM is part of the vectors key. |

### MESH: Triangle4XP and its inputs

| Code | Origin in Ortho4XP | Condition | Effect in Ortho4XP today | Severity | OrthoStudio XP | Remedy shown to the user |
|---|---|---|---|---|---|---|
| MESH_INPUT_MISSING | O4_Mesh_Utils.py:560-575; O4_Mask_Utils.py:88-97; O4_Tile_Utils.py:58-63 | Step 2, 2.5 or 3 launched without the file of the previous step | Clear message and stop, but the user must know the step order | blocking | stop | Run the build; OrthoStudio XP orders the stages itself. *By design.* |
| MESH_TILE_ASSUMED_SEA | O4_Vector_Map.py:160-166 | No SEA seed found and the DEM maximum is below 1 m | Whole tile seeded as sea without a message; combined with a failed DEM (zeros) a land tile becomes water | degraded | continue | Check the DEM and the coastline of this tile; force land with `sea_seed = none`. |
| MESH_QUALITY_RELAXED | O4_Mesh_Utils.py:703-725 | Triangle4XP exits non-zero on the first run | "could not achieve the requested quality", retried; the retry sets `mesh_cmd[-5]` which is `nodata`, not `min_angle`: the second run uses nodata=0 and the same angle | degraded | continue | The mesh is rebuilt with `min_angle=0`; check the OSM layers reported as invalid for this tile. |
| MESH_TRIANGULATION_FAILED | O4_Mesh_Utils.py:726-737; O4_Mesh_Utils.py:829-842 | Triangle4XP fails twice or `triangle` fails on an extent | Message asks to file a bug; extents: "triangle crashed" printed, then the extent PNG is built from a missing file | blocking | stop | Report with the `.node/.poly` files exported by `osxp debug export-pslg`; check available memory. |
| MESH_TRIANGLE_BUDGET_REACHED | O4_Mesh_Utils.py:640-652 | The Steiner point budget derived from `limit_tris` is exhausted | Triangle4XP stops refining; coarser mesh without any message | info | continue | Raise `limit_tris` or `curvature_tol` for this tile. |
| MESH_WEIGHT_MAP_INCOMPLETE | O4_Mesh_Utils.py:190-201; O4_Mesh_Utils.py:657 | `coast_curv_tol` differs from `curvature_tol` and the hidden Overpass query for the coastline fails inside Step 2 | `return 0` ignored: weight map stays flat, coast refinement silently dropped | degraded | continue | The coastline layer of the vector stage is reused; nothing to do. *By design.* |

### MASK: sea masks

| Code | Origin in Ortho4XP | Condition | Effect in Ortho4XP today | Severity | OrthoStudio XP | Remedy shown to the user |
|---|---|---|---|---|---|---|
| MASK_NEIGHBOUR_MESH_MISSING | O4_Mask_Utils.py:223-240 | A neighbouring tile has no `.mesh` yet | Silently ignored: masks along that border see no sea on the other side, hard edge in the water at the tile boundary | degraded | continue | Build the neighbour first or let OrthoStudio XP use the neighbour's OSM water polygon (`--neighbour-water osm`). |
| MASK_NEIGHBOUR_MESH_UNREADABLE | O4_Mask_Utils.py:432-439 | A neighbour `.mesh` exists but cannot be opened | "could not be read. Skipped." at verbosity 1 | degraded | continue | Rebuild the neighbour mesh `{path}`. |
| MASK_STALE | O4_DSF_Utils.py:715-745; O4_Imagery_Utils.py:2340-2360; O4_Mask_Utils.py:242-255 | Masks were built for an older mesh or older parameters; masks are only deleted when Step 2.5 is run again | Step 3 reuses whatever `.png` exists, decides DXT1/DXT5 from a 20 MB size heuristic and mtime comparison | degraded | continue | Rebuild the masks. *By design:* masks are keyed by mesh and parameters. |
| MASK_DISTANCE_MISSING | O4_Bathymetry.py:207-211; O4_Bathymetry.py:8-14 | `water_tech = XP12` but `distance_masks_too` is off, or the `_dist.png` is missing | `continue` in the loop: depth ratio floored at 0.1 everywhere, flat bathymetry without a message | degraded | continue | Distance masks are enabled automatically with XP12 water; rebuild the masks. |
| MASK_FILE_UNREADABLE | O4_Imagery_Utils.py:2340-2360; O4_Mask_Utils.py:38-60 | A mask PNG is truncated or corrupted | `Image.open` raises inside a conversion worker: the thread dies, remaining textures are never converted, `is_working` stays set | blocking | stop | Delete `{path}` and rebuild the masks of this tile. |
| MASK_CUSTOM_EXTENT_INVALID | O4_Mask_Utils.py:156-176; O4_Mask_Utils.py:372-392 | `masks_custom_extent` names a missing or unreadable extent; in Ortho4XP the code path itself has a `NameError` (`custom_mask` vs `custom_array`) | Worker thread dies, masks not built, GUI frozen | blocking | stop | Check the extent name and PNG in `Extents/`. |

### IMG: imagery download and assembly

| Code | Origin in Ortho4XP | Condition | Effect in Ortho4XP today | Severity | OrthoStudio XP | Remedy shown to the user |
|---|---|---|---|---|---|---|
| IMG_TILE_PLACEHOLDER | O4_Imagery_Utils.py:1020-1032 | Bing returns its 1033-byte camera image or ArcGIS its 2521-byte "no data" tile | Treated as 404 and the parent tile is used | info | continue | Nothing to do; the area has no imagery at this zoom. |
| IMG_TILE_PARENT_FALLBACK | O4_Imagery_Utils.py:1275-1286 | A 256 px tile is missing at the requested zoom level | Silently replaced by a crop of the parent, up to 6 levels above | info | continue | Nothing to do; listed in the decision report with the fallback depth. |
| IMG_TILE_MISSING | O4_Imagery_Utils.py:1283-1287; O4_Imagery_Utils.py:1574-1582 | No parent tile within 6 levels, or a non-webmercator provider without data | "filled with white there" at verbosity 1; the white 4096² JPEG is **saved in the cache and never questioned again** | degraded | continue | The texture is marked incomplete; use "Retry missing textures" once the provider is back. |
| IMG_TILE_CORRUPTED | O4_Imagery_Utils.py:1037-1046 | HTTP 200 with an undecodable image body | Retried up to `max_baddata_retries`, then white | info | continue | Retried; if it persists the provider is degraded. |
| IMG_BAD_CONTENT_TYPE | O4_Imagery_Utils.py:1049-1055 | HTTP 200 whose body is HTML or JSON (expired token, dead provider, captive portal) | `break`, white texture | degraded | continue | The provider needs a token or is retired; run `osxp doctor --providers`. |
| IMG_CACHE_INCOMPLETE | O4_Imagery_Utils.py:1733-1743 | A cached JPEG built with white fills is reused because the file exists | Considered present forever; the white patch survives every rebuild | degraded | continue | The chunk store records missing chunks; "Retry missing textures" downloads only those. |
| IMG_COLOR_FILTER_FAILED | O4_Imagery_Utils.py:2087-2136 | The `.flt` filter raises during processing | Bare `except: return im`: texture kept unfiltered without a message | info | continue | Check the filter definition `{filter}`. |
| IMG_COMBINED_NO_DATA | O4_Imagery_Utils.py:626-644; O4_Imagery_Utils.py:1690-1700 | A combined provider has no layer with data for the tile or for one texture | Tile-level: Step 3 exits; texture-level: `return 0`, the DSF references a texture that is never built | degraded | continue | Add a fallback layer to the `.comb` file (e.g. Bing) or change the provider for this zone. |
| IMG_EXTENT_UNTESTABLE | O4_Imagery_Utils.py:985-991 | Extent PNG or mask cannot be read while testing coverage | "Could not test coverage" and the layer is treated as having no data | degraded | continue | Check the extent files in `Extents/{dir}/`. |
| IMG_EXTENT_DATA_MISSING | O4_Imagery_Utils.py:700-719 | A LowRes extent needs an `.osm.bz2` that is absent or empty | Step 3 exits or the extent is skipped | blocking | stop | Download the extent data as documented for `{extent}` or remove the layer. |
| IMG_LOCAL_TILE_MISSING | O4_Imagery_Utils.py:1228-1249 | A `local_tms` provider file is absent on disk | Verbosity 2, white tile | degraded | continue | Check the local imagery directory of provider `{provider}`. |
| IMG_CUSTOM_URL_MODULE_INVALID | O4_Imagery_Utils.py:24-40 | `Providers/O4_Custom_URL.py` has a syntax or import error | Printed once at start-up; every provider relying on it silently produces white textures | degraded | continue | Fix or remove the token plugin `{path}`. |

### TEX: DDS textures

| Code | Origin in Ortho4XP | Condition | Effect in Ortho4XP today | Severity | OrthoStudio XP | Remedy shown to the user |
|---|---|---|---|---|---|---|
| TEX_MISSING | O4_Tile_Utils.py:21-40; O4_DSF_Utils.py:715-752 | A texture referenced by the DSF was not produced (download returned 0, conversion failed, build stopped) | `download_textures` silently drops it from the convert queue; X-Plane logs "failed to load texture" and shows the terrain without ortho | blocking | stop | Use "Retry missing textures"; the DSF is installed only once every texture exists. |
| TEX_ENCODE_FAILED | O4_Imagery_Utils.py:2528-2547 | `nvcompress` exits non-zero | Retried 10 times, 1 s apart, then "Could not convert texture" at verbosity 1 and no DDS | blocking | stop | The next encoder of the fallback chain is tried; if all fail, run `osxp doctor --encoder`. |
| TEX_ENCODER_UNAVAILABLE | O4_Imagery_Utils.py:56-76 | `Utils/mac/nvcompress` is x86_64 only: without Rosetta on Apple Silicon, or with the binary not executable, every conversion fails | Same 10-try loop per texture, no DDS at all, an hour of retries for a ZL16 tile | blocking | stop | ispc_texcomp is used in-process; if it cannot load, install the fallback encoder (`osxp doctor --encoder`). |
| TEX_GEOTIFF_TOOL_MISSING | O4_Imagery_Utils.py:2497-2510 | GeoTIFF export requested without `gdal_translate` | "Could not geotag texture (gdal not present ?)" | degraded | continue | Install GDAL for GeoTIFF export; DDS output is unaffected. |

### DSF: DSF assembly, Global Scenery rasters, overlays

| Code | Origin in Ortho4XP | Condition | Effect in Ortho4XP today | Severity | OrthoStudio XP | Remedy shown to the user |
|---|---|---|---|---|---|---|
| DSF_GLOBAL_SCENERY_MISSING | O4_DSF_Utils.py:360-372; O4_DSF_Utils.py:1082; O4_DSF_Utils.py:1326-1329 | The X-Plane 12 Global Scenery DSF for the tile is not found (`custom_overlay_src` unset or demo install) | `exit_message` printed but `build_dsf` continues with empty `DEMS`/`DEMN`: **DSF without elevation rasters**, Step 3 says "normal exit"; XP12 water rendering degraded | blocking | stop | Set the X-Plane directory; Global Scenery for `{tile}` must be installed (X-Plane installer, "Global Scenery"). |
| DSF_GLOBAL_SCENERY_COPY_FAILED | O4_DSF_Utils.py:379-386; O4_Overlay_Utils.py:57-63 | Copy of the Global Scenery DSF to `tmp/` fails (disk full, permissions) | Same silent continuation without rasters | blocking | stop | Free disk space or fix permissions on the OrthoStudio XP store. |
| DSF_SOURCE_DECOMPRESS_FAILED | O4_DSF_Utils.py:391-398; O4_Overlay_Utils.py:68-79 | Global Scenery DSF is 7z-compressed and `7z` is absent (Homebrew on macOS) or fails | `os.system` return code ignored, the `.7z` is removed, `getsize` raises in the DSF thread: no DSF written, "could not rename DSF file" later, GUI frozen | blocking | stop | py7zr decompresses in-process; if the file is corrupted, reinstall Global Scenery for `{tile}`. |
| DSF_SOURCE_CORRUPTED | O4_DSF_Utils.py:400-405 | Decompressed file does not start with `XPLNEDSF` | "Corrupted DSF file", continues without rasters | blocking | stop | Reinstall the Global Scenery tile `{tile}` in X-Plane. |
| DSF_ACTIVATION_FAILED | O4_Tile_Utils.py:154-166 | Renaming `.dsf.tmp` to `.dsf` fails (file locked by X-Plane, disk error) | "tile is not actived" at verbosity 0, Step 3 still reports "normal exit"; X-Plane sees no DSF | blocking | stop | Close X-Plane or unlock the file, then run "Install" again. |
| DSF_POOL_OVERFLOW | O4_DSF_Utils.py:1150-1160 | More than 65 535 vertices land in one 16-bit pool | `struct.pack("<H")` raises inside the DSF thread: no DSF, GUI frozen | blocking | stop | Internal error; report the tile and parameters (`osxp debug report`). |
| DSF_MESH_OUTSIDE_TILE | new | A mesh triangle falls in a ZL grid cell outside the 1x1 degree tile (mesh built for another tile, corrupted `.mesh`, or a `til_x/til_y` off by one at the tile border) | Ortho4XP indexes its zone arrays without a bound check: the cell wraps to another row of the array and the triangle silently gets the terrain of an unrelated texture | blocking | stop | The mesh of `{tile}` is outside the tile; rebuild the mesh. |
| DSF_OVERLAY_SOURCE_MISSING | O4_Overlay_Utils.py:44-51 | Overlay extraction requested without a valid `custom_overlay_src` | Clear message, Step 4 stops | blocking | stop | Set the X-Plane directory or the overlay source scenery. |
| DSF_OVERLAY_TOOL_FAILED | O4_Overlay_Utils.py:90-100; O4_Overlay_Utils.py:186-197 | `DSFTool` fails | `returncode` read without `wait()` is `None`: the failure is never detected, the next `open()` raises | blocking | stop | Check the DSFTool binary (`osxp doctor`); report the source DSF if it persists. |

### XP: X-Plane installation

| Code | Origin in Ortho4XP | Condition | Effect in Ortho4XP today | Severity | OrthoStudio XP | Remedy shown to the user |
|---|---|---|---|---|---|---|
| XP_RUNNING | new | X-Plane is running while a pack is installed or `scenery_packs.ini` is rewritten | Ortho4XP never checks; the GUI toggles symlinks at any time (O4_GUI_Utils.py:1713-1760) and X-Plane may crash or ignore the change | blocking | stop | Quit X-Plane, then run "Install" again. |
| XP_DIR_NOT_FOUND | new | `custom_scenery_dir` is empty or does not exist | The GUI creates the link relative to the current directory (`os.path.join("", ...)`, O4_GUI_Utils.py:1516-1520) without any message | blocking | stop | Choose the X-Plane 12 folder in Settings (`osxp doctor` detects it). |
| XP_GLOBAL_SCENERY_NOT_FOUND | new | `Global Scenery/X-Plane 12 Global Scenery` is absent from the X-Plane folder | Only discovered per tile as DSF_GLOBAL_SCENERY_MISSING | blocking | stop | Install Global Scenery from the X-Plane installer. |
| XP_SCENERY_PACKS_UNWRITABLE | new | `scenery_packs.ini` cannot be written (permissions, read-only volume) | Ortho4XP does not manage this file; the user edits it by hand | blocking | stop | Fix permissions on `{path}`; the pack is built but not activated. |
| XP_LINK_FAILED | new | Symlink or NTFS junction cannot be created, or the app cannot follow the junction it made (Windows privileges, existing folder, the app under RedirectionGuard: `install.md` 4) | Uncaught `OSError` in the GUI callback (O4_GUI_Utils.py:1760-1800) | blocking | stop | Enable Developer Mode on Windows or copy the pack into Custom Scenery; under RedirectionGuard, quit the app and open it again from the Start menu. |
| XP_PACK_CONFLICT | new | The same tile is provided by another pack (AutoOrtho, another folder of that name) | No check; the pack order decides silently | info | continue | Order in `scenery_packs.ini` is adjusted; remove the duplicate `{pack}` if unwanted. |

### CFG: configuration

| Code | Origin in Ortho4XP | Condition | Effect in Ortho4XP today | Severity | OrthoStudio XP | Remedy shown to the user |
|---|---|---|---|---|---|---|
| CFG_GLOBAL_FILE_MISSING | O4_Config_Utils.py:480-481 | No `Ortho4XP.cfg` at the root | "Reverting to default values" | info | continue | A default configuration is written. |
| CFG_LINE_INVALID | O4_Config_Utils.py:466-478; O4_Config_Utils.py:548-575 | A line of the global or tile config fails the `exec()` conversion | Global: message at verbosity 1; tile: verbosity 2 only. The value silently keeps its default | blocking | stop | Fix line `{line}` of `{path}`; OrthoStudio XP validates the whole TOML before building. |
| CFG_TILE_FILE_MISSING | O4_Config_Utils.py:528-537 | No tile config found in the build directory | Defaults used with a message at verbosity 0 | info | continue | A tile configuration is created from the global one. |
| CFG_TILE_WRITE_FAILED | O4_Config_Utils.py:592-611; O4_Config_Utils.py:1072-1084 | Tile or global config cannot be written | Message; the build continues with in-memory values | degraded | continue | Fix permissions on `{path}`. |
| CFG_ZONE_LIST_INVALID | O4_Config_Utils.py:553-561; O4_DSF_Utils.py:110-200 | `zone_list` is not a well-formed Python list (typo, unbalanced brackets, unknown provider) | `exec()` fails, error at verbosity 2 only: **all zones dropped**, the whole tile is built at `default_zl` | blocking | stop | Fix the zone definitions (zone `{index}`); zones are validated and drawn before the build. |
| CFG_PROVIDER_UNKNOWN | O4_Imagery_Utils.py:1744-1757 | A zone or `default_website` names a provider not loaded from `Providers/` | Per texture: "Unknown provider ... or it has no data" at verbosity 1, texture not built, DSF references it | blocking | stop | Choose a provider from the list; run `osxp doctor --providers` to see which are alive. |
| CFG_PROVIDER_DEFINITION_INVALID | O4_Imagery_Utils.py:200-500 | A `.lay` file has an invalid field (request_type, epsg, wms size, capabilities) | Printed once at start-up, then the provider simply does not exist | blocking | stop | Fix `{path}` (field `{field}`) or remove the provider. |
| CFG_PROVIDER_OUT_OF_COVERAGE | new | The imagery source of a build covers one country (`extent_bounds` of the registry) and a tile of the build lies outside that rectangle (a user found the sources of several countries mixed in the list, 2026-09-14) | A single source is used as it is (O4_Imagery_Utils.py:1626-1630): outside its country its server has no image, a lower zoom level is tried, then the texture is filled with white (O4_Imagery_Utils.py:1575-1582) | blocking | stop | Choose a source that covers these tiles: Bing Maps and Esri cover the whole world. |
| CFG_DATA_DIR_INVALID | new | The data folder chosen in Settings (`essential.data_dir`) is not an absolute path, is a file, cannot be written, is inside X-Plane's `Custom Scenery`, or is on a disk that cannot hard-link files (exFAT, FAT32: each texture, linked into the store and into its pack, would be written three times); `why` names the case | Ortho4XP writes its folders beside its scripts, wherever they are | blocking | stop | Choose a folder on a disk formatted APFS or Mac OS Extended (Mac), NTFS (Windows) or ext4 (Linux). |
| CFG_DATA_DIR_MISSING | new | The data folder chosen in Settings is not there (its external disk unplugged): a build, an estimate or the choice of that folder is refused rather than filling a folder of the computer's own disk | Ortho4XP creates the folders it misses where it is told | blocking | stop | Plug in the disk it is on, or choose another folder in Settings. |
| CFG_SIMBRIEF_USER_MISSING | new | The Plan's SimBrief button was pressed with no name in Settings | Ortho4XP has no flight plan of any kind | blocking | stop | Set your SimBrief name in Settings; nothing is asked of simbrief.com before that. |
| CFG_SIMBRIEF_USER_UNKNOWN | new | SimBrief refuses the name saved in Settings (their HTTP 400, "Unknown UserID") | – | blocking | stop | Check the name in Settings: it is the SimBrief name, or the pilot ID. |
| CFG_SIMBRIEF_PLAN_EMPTY | new | The last plan of that user says nowhere it goes (no airports, no navigation log with coordinates) | – | blocking | stop | Generate a flight plan on simbrief.com, or type the airports of the route by hand. |
| CFG_LATLON_INVALID | O4_GUI_Utils.py:425-446; Ortho4XP.py:44-50 | Latitude outside [-85, 84] or longitude outside [-180, 179], or not integers | GUI: error at verbosity 0; CLI: usage line and `sys.exit()` | blocking | stop | Enter integer tile coordinates within the web-mercator range. |
| CFG_VALUE_INVALID | O4_Mesh_Utils.py:640-644; O4_Config_Utils.py:1087-1129 | A parameter is outside its type or range (`limit_tris` not a number, negative width) | `limit_tris` falls back to 5 M with a verbosity-1 warning; other values are only checked when the GUI "Apply" button is used | blocking | stop | Set `{name}` to a value of type `{type}` in range `{range}`. |
| CFG_DEM_SOURCE_INVALID | O4_DEM_Utils.py:822-825; O4_Mesh_Utils.py:592-598; O4_Mesh_Utils.py:619-626; O4_Mask_Utils.py:132-136 | `custom_dem` names an unknown source or an unreadable path | "Unknown elevation source" then zeros in Step 1; Steps 2 and 2.5 stop with "check your custom_dem entry" | blocking | stop | Choose a source from the list or a readable raster path for `custom_dem`. |
| CFG_BUILD_DIR_UNWRITABLE | O4_Config_Utils.py:502-521 | Build directory is read-only or cannot be created | Clear message and `raise Exception` caught by the GUI as "Process aborted" | blocking | stop | Fix permissions on `{path}` or choose another output directory. |

### NET: transport-level events (raised with the provider or mirror in context)

| Code | Origin in Ortho4XP | Condition | Effect in Ortho4XP today | Severity | OrthoStudio XP | Remedy shown to the user |
|---|---|---|---|---|---|---|
| NET_OFFLINE | new | No route to any host (all hosts fail their first requests) | Each texture part waits `2 s x max_connect_retries`; Overpass waits up to 5 min 40 s; nothing tells the user the machine is offline | blocking | stop | Check the network connection; cached data is still usable (`--offline`). |
| NET_CONNECTION_FAILED | O4_Imagery_Utils.py:1073-1088 | DNS, TCP or TLS failure on one request | A new `Session` is opened, 2 s sleep, retried up to `max_connect_retries`, then white | info | continue | Retried with backoff. |
| NET_TIMEOUT | O4_Imagery_Utils.py:1013-1017; O4_OSM_Utils.py:534 | No answer within `http_timeout` (10 s) or 60 s for Overpass | Counted as a connection failure | info | continue | Retried; a slow host is hedged with a second request. |
| NET_RATE_LIMITED | O4_Imagery_Utils.py:1066-1071 | HTTP 429 or a provider quota message | Falls in "Unmanaged Server answer", `break`, white texture | degraded | continue | Requests to `{host}` are slowed down (AIMD); wait or use another provider. |
| NET_FORBIDDEN | O4_Imagery_Utils.py:1057-1060 | HTTP 403 | "IP banned?" at verbosity 2, `break`, white texture | blocking | stop | Downloads from `{host}` are paused; wait before retrying, check the provider's terms of use. |
| NET_SERVER_ERROR | O4_Imagery_Utils.py:1061-1065 | HTTP 5xx | Retried only when `check_tms_response` is set, otherwise white | info | continue | Retried with backoff. |
| NET_UNEXPECTED_STATUS | O4_Imagery_Utils.py:1066-1071 | Any other HTTP status (redirect loop, 401, 410) | "Unmanaged Server answer", white texture | degraded | continue | The provider `{provider}` answered `{status}`; run `osxp doctor --providers`. |

### SYS: process, tools and machine

| Code | Origin in Ortho4XP | Condition | Effect in Ortho4XP today | Severity | OrthoStudio XP | Remedy shown to the user |
|---|---|---|---|---|---|---|
| SYS_DISK_FULL | new | Free space below the estimate for the planned build, or `ENOSPC` while writing | Any `OSError` in a worker thread is uncaught: partial files, thread dies, GUI frozen | blocking | stop | Free at least `{needed}` on `{volume}` or lower the cache quota. |
| SYS_WRITE_FAILED | O4_Imagery_Utils.py:1596-1605; O4_DSF_Utils.py:379-386; O4_Overlay_Utils.py:201-211; O4_Scenery_Utils.py:5-16; Ortho4XP.py:27-33 | A file or directory cannot be created or written (permissions, read-only volume) | Messages vary; several sites `return 0` and the build continues without the file | blocking | stop | Fix permissions on `{path}`. |
| SYS_TOOL_MISSING | O4_Mesh_Utils.py:17-31; O4_Mesh_Utils.py:689; O4_Overlay_Utils.py:17-25; O4_Imagery_Utils.py:56-76; O4_DEM_Utils.py:11-16 | A required executable is absent or not executable (Triangle4XP, DSFTool, 7z, nvcompress, GDAL) | `Popen` raises `FileNotFoundError` inside a thread, or `os.system` returns 127 unnoticed | blocking | stop | Run `osxp doctor`; reinstall `{tool}` or the OrthoStudio XP bundle. |
| SYS_ROSETTA_MISSING | O4_Imagery_Utils.py:56-58 | macOS on Apple Silicon without Rosetta 2 while an x86_64-only helper is needed | Every `nvcompress` call fails; 10 retries per texture | blocking | stop | Not needed by OrthoStudio XP's in-process encoder; install Rosetta only if you opt for the nvcompress fallback. |
| SYS_RESOURCE_MISSING | Ortho4XP.py:21-24; O4_Mask_Utils.py:70-72 | A bundled resource is absent (`Utils/`, `water_transition.png`) | Start-up exit, or `Image.open` raises inside the mask stage | blocking | stop | Reinstall OrthoStudio XP; the bundle is incomplete (`{path}`). |
| SYS_INTERNAL_ERROR | Ortho4XP.py:66-73; O4_GUI_Utils.py:455-538; O4_UI_Utils.py:5-8 | Any unexpected exception in a stage | CLI prints `Crash!` and exits 0; GUI threads have no handler, `UI.is_working` stays True until restart | blocking | stop | Report the error with `osxp debug report` (includes tile, parameters and the traceback). |
| SYS_CANCELLED | O4_UI_Utils.py:58-70; O4_Mesh_Utils.py:689-702; O4_Tile_Utils.py:21-40 | The user pressed Stop | `red_flag` is polled between steps only; Triangle4XP and in-flight downloads run to completion | info | stop | Nothing to do; the build resumes from the last completed node. |
| SYS_UPSTREAM_FAILED | new | A graph node is skipped because a node it depends on failed (`root` and the upstream code are in the context) | Ortho4XP has no graph: a failed step leaves the next steps to crash on the missing file, or the GUI thread simply stops | info | stop | Fix the upstream failure and relaunch; finished nodes are reused. |
| SYS_OUT_OF_MEMORY | O4_Mesh_Utils.py:726-737 | A stage exceeds available memory (Triangle4XP on dense tiles, 4096² RGBA compositing) | Triangle4XP is killed by the OS and the hint is "limited amount of RAM"; Python `MemoryError` is uncaught elsewhere | blocking | stop | Close other applications or lower the parallelism (`--jobs`); the scheduler keeps a memory budget. |
| SYS_WORKING_DIR_INVALID | Ortho4XP.py:4; O4_UI_Utils.py:5; O4_File_Names.py:8 | Ortho4XP launched from another directory than its root | Relative paths: "Missing Utils directory" or empty directories created next to the caller | blocking | stop | *By design:* OrthoStudio XP uses absolute per-user directories; `import-ortho4xp` asks for the legacy root. |
| SYS_PACK_NOT_OSXP | new | The page's Delete or `osxp uninstall --delete` names a tile folder OrthoStudio XP did not build: a tile imported from Ortho4XP, a folder without `orthostudio.toml` (it may hold the user's own files), a link, or the pack of another tile | Ortho4XP keeps no record of who made a folder: the trash of its GUI (O4_GUI_Utils.py:1662-1670) deletes a tile's whole build folder with `shutil.rmtree`, whatever it holds, leaves its link in Custom Scenery pointing at nothing and hides a failure below verbosity 3 | blocking | stop | Nothing was deleted. Uninstall takes the tile out of X-Plane without deleting anything; to delete the folder itself, delete it by hand. |

### ZONE: zones drawn on the map

The zones document `osxp-zones-1` (`$OSXP_HOME/zones.json`, `GET`/`PUT /api/zones`,
`osxp build --zones FILE`) and its clipping into each tile's `zone_list`
(`docs/specs/map-zones.md`). `ZONE_INVALID` refuses a whole document that is being saved:
`PUT /api/zones` answers `422` and writes nothing. A document already saved is never refused
whole (`map-zones.md` 3 and 5): `GET /api/zones` lists every problem next to the zones, and a
build is refused only for the tiles a zone with a problem touches (or for every tile when the
file cannot be read as zones at all). The context names the zone (`zone`, its id; `index`, its
position, when it comes from a saved file) and the `reason`.

| Code | Origin in Ortho4XP | Condition | Effect in Ortho4XP today | Severity | OrthoStudio XP | Remedy shown to the user |
|---|---|---|---|---|---|---|
| ZONE_INVALID | O4_GUI_Utils.py:1189-1203; O4_DSF_Utils.py:199-206 | A zone has fewer than 3 or more than 2000 vertices, a coordinate outside [-180, 180] x [-85, 85], a polygon that is not simple (`explain_validity` gives the reason), a zoom level outside 12-20, above its provider's `max_zl` (the tile's provider when the zone has none, checked when the build is planned) or above the tile's `mesh_zl` (the DSF gives each mesh cell one texture), an unknown provider, an id used twice; or the document itself is not JSON, or not an `osxp-zones-1` object | The zone list is saved and painted without any check: `ImageDraw.polygon` fills a self-intersecting polygon with the even-odd rule: the parts the outline winds over twice silently keep what lies beneath (the tile's zoom level or a zone of lower priority) | blocking | stop | Fix or delete zone `{zone}` on the map (or in the zones file); nothing that depends on it is saved or built until it is valid. |
| ZONE_TOO_MANY | O4_DSF_Utils.py:199-206 | More than 254 `zone_list` entries for one tile once the zones are clipped (the priority image is 8-bit: the base zone is 1, zones 2 to 255), or more than 500 zones in the document | Values above 255 are clamped by PIL: every zone past the 254th is painted 255 and silently takes the zoom level and provider of one of them | blocking | stop | Delete or merge zones: a tile takes at most 254 zone parts (a zone cut by the tile edges counts once per part), the document at most 500 zones. |
| ZONE_CONFLICT | new | `PUT /api/zones` carries `If-Match` with a revision that is no longer the file's: another window (or a program) saved the zones after this page loaded them | Ortho4XP has one window per process and writes `zone_list` into the tile's configuration: two instances editing the same tile silently keep the last save | blocking | stop | Reload the zones to see the saved ones, then make the change again; nothing was written. |

## Rendering

`OsxpError.to_json()` produces a single JSON object, keys sorted, UTF-8, no whitespace:

```json
{"action":"stop","cause":null,"code":"DSF_GLOBAL_SCENERY_MISSING","context":{"path":"/X-Plane 12/Global Scenery/X-Plane 12 Global Scenery/Earth nav data/+40+000/+43+005.dsf","tile":"+43+005"},"domain":"DSF","message":"Global Scenery DSF for +43+005 was not found.","remedy":"Set the X-Plane directory; Global Scenery for +43+005 must be installed (X-Plane installer, \"Global Scenery\").","schema":1,"severity":"blocking"}
```

`render_json(exc)` accepts any exception: an `OsxpError` is rendered as above, anything else is
wrapped as `SYS_INTERNAL_ERROR` with the exception type and text in `context`.

## Wanted differences from Ortho4XP

- Nothing is filled with white, zero or "nothing" silently: every such decision has a code and
  appears in the decision report; a white texture becomes `IMG_TILE_MISSING` + `TEX_MISSING`
  and blocks installation until "Retry missing textures" succeeds.
- A layer that Overpass could not deliver stops the tile (`OSM_LAYER_UNAVAILABLE`) instead of
  producing a tile without sea. No fallback exists for it: the remedies say what to fix in
  OpenStreetMap, since promising one that does not exist is worse than promising nothing
  (2026-09-23).
- A DEM that could not be obtained stops the tile (`DEM_DOWNLOAD_FAILED`) instead of a flat tile.
- A missing Global Scenery DSF stops the tile (`DSF_GLOBAL_SCENERY_MISSING`) instead of a DSF
  without `DEMS`.
- Failures never leave the engine in a "working" state: a node fails, the scheduler records the
  error and moves on to other tiles.
