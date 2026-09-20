# Native DEM: sources, combined raster, `Dem.alt_vec`, rule `orthostudio.dem@1`

Status: P3, implemented in `src/orthostudio/dem/` (tests `tests/test_dem_*.py`).
Origin: Ortho4XP `src/O4_DEM_Utils.py` (1002 lines), plus the two callers that decide
*when* the raster is written: `O4_Vector_Map.py:206-213` (step 1 builds the DEM) and
`O4_Airport_Utils.py:924-1034` (`smooth_raster_over_airports`, which writes
`Data<tile>.alt`), and `O4_Mesh_Utils.py:556-620` (step 2 only re-reads it and checks its
size).
Acceptance: PLAN level A (byte-identical) on the combined raster, `alt_vec`, `upsample`,
`fill_nodata` and `smoothen`; see section 9 for the one artefact that is *not* byte-identical
on its own (`Data<tile>.alt` carries airport smoothing, which is not elevation work) and
section 11 for the measurements.

## 1. What the stage does

The DEM stage answers one question for the rest of the pipeline: **what is the altitude at
(lon, lat)?** Everything else is packaging.

1. **Ensure** the elevation files of the 3x3 block of 1-degree cells centred on the tile are
   on disk (download, unzip).
2. **Read** each cell into a float32 array (`.hgt` big-endian int16, `.raw` little-endian
   int16, or a GeoTIFF).
3. **Assemble** them into one raster that overflows the tile by 36 pixels on each side, so
   that the mesh of a tile has correct altitudes right up to (and slightly past) its border.
4. **Fill** the voids (nearest neighbour, or zero).
5. Serve `alt_vec(way)` — a barycentric interpolation on the triangulated raster — to the
   vector stage (roads, patches, airport anchors) and to the mesh stage.
6. Write `Data<tile>.alt`: the raster, float32, row-major, no header.

OrthoStudio XP adds nothing to that list. It changes *how*, not *what* (section 8).

## 2. Naming and layout (kept verbatim)

`O4_File_Names.py:24-56, 290-328`.

| Function | Result |
|---|---|
| `hem_latlon(43, 5)` | `N43E005` |
| `round_latlon(43, 5)` | `+40+000` (10-degree cell, floor towards minus infinity) |
| `base_file_name(43, 5)` | `<Elevation_data>/+40+000/N43E005` |
| `elevation_path("View", ...)` | `<base>.hgt` |
| `elevation_path("SRTM", ...)` | `<base>_SRTMv3.hgt` |
| `elevation_path("ALOS", ...)` | `<base>_ALOS3W30.tif` |
| `elevation_path("COP30", ...)` | `<base>_COP30.tif` (OrthoStudio XP's own, 3.0) |
| `elevation_path("NED1", ...)` | `<base>_NED1.tif` |
| `elevation_path("NED1/3", ...)` | `<base>_NED13.tif` |
| `generic_tif(43, 5)` | `<base>.tif` (implicit source when no `custom_dem` is set) |

**Keep**: `orthostudio.dem.sources` reproduces these names exactly in its own elevation directory
(section 9.4). Until decision 0010 that let a build read the `Elevation_data/` of an Ortho4XP folder
as it was; it no longer reads one.

## 3. Sources

### 3.0 `COP30` (Copernicus DEM GLO-30) — OrthoStudio XP's own

Added for a user who asked for a finer mesh than X-Plane's (2026-09-17). One GeoTIFF of
3600 x 3600 posts per one-degree cell, 1 arc-second, on the public store of the Open Data
programme, without an account:
`https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_<N46>_00_<E006>_00_DEM/<same>.tif`
(`sources.cop30_url`). Measured 2026-09-17: 20 to 40 MB a cell, 1.7 s to fetch and 0.3 s to read
on a 130 Mbit/s line. The file is kept as `<cell>_COP30.tif` in the elevation folder, and a cell
all at sea has no file: its 404 goes to the negative memo and the cell degrades to 0 m, as a
missing `View` cell does. Its geometry is `ALOS`'s (posts at the centre of each arc-second cell),
and it is assembled from the 3 x 3 block like the other global sources, so that tile borders meet.
The files declare no nodata value and have none (their voids are filled at the source):
`Dem.load` drops `DEM_NODATA_UNDECLARED` for this source.

**Cells are not square above 50°** (fixed 2026-09-19). The product keeps about 30 m on the ground
rather than one arc-second, so from 50° of latitude a cell carries 2400 columns instead of 3600,
then 1800 (60°), 1200 (70°), 720 (75°) and 360 (80°), to the north and to the south alike; the rows
stay 3600. Read as they came, those cells were `DEM_FILE_UNREADABLE` and the tile was refused:
**nobody above 50° could build with Copernicus**, nor with Canada's lidar, which is laid over it (a
user in Alberta reported it twice). `raster._to_base_columns` interpolates the columns onto the
grid the block assembles, which invents nothing: at 53° a 1.5" step in longitude is 28 m against
31 m for one second of latitude, so the cell is square in metres and the finer grid only restores
the shape the assembly expects. Measured afterwards on `+53-114`: the mesh builds, and the model
gives 714 m at Edmonton International (published 723 m) and 667 m at Edmonton City Centre
(published 671 m).

### 3.0b ANADEM, the relief of South America

Added for a user of the X-Plane.Org page (2026-09-20). ANADEM v1.0 is a **terrain** model of South
America: the Federal University of Rio Grande do Sul and Brazil's water agency take the vegetation
bias out of Copernicus GLO-30 with a model trained on remote sensing (Laipelt et al., 2024,
`https://hge-iph.github.io/anadem/`). Measured here over the Amazon, `S10W060`, 262 144 points:
**Copernicus stands 16.7 m above ANADEM on average** (median 18.1 m), higher on 96 % of the points,
and its surface is noisier (2.41 m between neighbours against 1.26 m). That is the canopy, and an
airstrip in the forest sits on top of it.

It is laid **over Copernicus** (`custom_dem = "COP30;ANADEM"`, Settings' relief *The relief of South
America*), as Canada's lidar is: outside the zones published, Copernicus answers alone.

The agency serves one tiled GeoTIFF per MGRS zone, **about 2 GB**, 52 of them (`ANADEM_ZONES`), and
their server takes byte ranges. A square is 37 MB of a zone, so nothing else travels: `dem/cog.py`
reads the header in one request (`HEADER_BYTES`, enough for the directory and the tables of 2 596
tile offsets), asks for the tiles the square falls in, and writes them as a small deflated GeoTIFF
of that square (`write_geotiff`), which the rest of the engine reads like any other elevation file.
Measured: a whole square of the Amazon in **9 s**, 33 MB kept. A one-degree square never straddles a
zone, whose sides are multiples of six degrees of longitude and eight of latitude.

The same reader opens a huge tiled GeoTIFF a user has on his disk, which Pillow would decode whole
(`MAX_POINTS` refuses a zone of 671 million points).

### 3.0a A folder of one's own files

Added for a user of the X-Plane.Org page who has the lidar models of Europe by the hundred, one
file per one-degree square, and asked to name the folder once rather than a file per tile
(2026-09-19). Settings' *Do you have elevation files of your own?* takes a folder; it travels in
`custom_dem` as **one more overlay**, `COP30;/path/to/folder` (or `XP12;...`, the pipeline putting
the base in front), so the square the folder holds takes its file and every other square keeps the
relief chosen. A partial set therefore builds every tile. The overlay is written into the raster
before it is stored (section 8.2): the stages after the relief read a file, not the object.

`sources.cell_file_in_folder` finds the file: any depth under the folder, the stem being the cell
(`N47E011`), and `.hgt`, `.tif`, `.tiff` or `.raw`. **The finest wins**: the same sets come at 3",
1" and 0.5" under the same names, one folder each, and a user who has all three keeps all three, so
the candidate with the most points is taken (the size for a raw format, the header for a GeoTIFF),
then the suffix order, then the shortest path. The answer therefore never depends on the order the
disk hands the files over. Any resolution is read, the
side being worked out from the size of the file: 3" (1201, upsampled to 3601), 1" (3601) and 0.5"
(7201) were built end to end. Named as the relief itself rather than over one, a folder must hold
the square: the tile is refused rather than built flat (decision 0007), and the message names the
folder.

**What a file may be.** Its own window is read from the file: a raster covering a whole country
serves every tile inside it, as long as it is in WGS 84 (or NAD 83, near enough). Another
projection is refused, `DEM_EPSG_UNSUPPORTED`, **before a pixel is read**: national models at 10 or
20 m come in a metric grid (Sonny's Switzerland at 10 m is UTM 32N, 851 million points, 914 MB),
and decoding one only to turn it down would have filled 3.4 GB. Ortho4XP reads the same file, warns
that "result is likely to be non sense" and builds it anyway (`O4_DEM_Utils.py:536-548`); it also
needs GDAL for any GeoTIFF, which OrthoStudio XP does not. A raster of more than `MAX_POINTS`
(400 million, 1.6 GB of floats) is refused with a plain reason rather than filling the memory.

**What enters the key.** The folder's path alone would not do: replacing a file with a better
version of itself leaves the path as it was, and the tile would come back from the store unchanged.
`pipeline.build` looks the file up for each square and puts its name, its size and the time it was
last written into `DemParams.own_stamp`. A file restored with an old timestamp is therefore not
noticed, which is the accepted limit.

### 3.0b `HRDEM` (Canada's lidar, NRCan) — OrthoStudio XP's own

Added for the same user as the USGS option, who flies in Canada and asked for a source there
(2026-09-18). Airborne lidar, 1 to 2 m, **bare earth** (the `dtm` coverage), served by the WCS of
the datacube of Natural Resources Canada:
`https://datacube.services.geo.ca/wrapper/ogc/elevation-hrdem-mosaic`
(`datacube.services.geo.ca/ows/elevation` redirects there). WCS 1.1.1, `image/geotiff`, EPSG:4326
in its URN form, whose axes are **latitude first**: written the other way round the service
computes an inverted extent and answers 500 (measured). `hrdem.hrdem_url` builds it.

Why it is worth a module of its own (`dem/hrdem.py`):

* **The coverage is partial** — only the part of the country that has been flown. A cheap
  coarse request over the whole cell (128 x 128 posts, 260 KB, ~2 s) says where the lidar is:
  measured 2026-09-18, 100 % over `+45-076` (Ottawa), 44 % over `+50-115` (the Rockies), 0 % over
  `+54-072` (northern Quebec). A cell with none is `MISSING` and remembered; the blocks with none
  are never asked for. A service that **does not answer** raises `ProbeError` instead, and the
  cell is missing *without* being remembered: a bad minute must not hide the lidar for the month
  the memo keeps a miss (a user in Alberta, whose square the mosaic covers at 85 %, reported that
  it would not load, 2026-09-19). It is therefore an **overlay**, laid over Copernicus
  (`custom_dem = "COP30;HRDEM"`), which answers everywhere else: `DEM_OVERLAY_UNAVAILABLE` says
  so, and the build goes on.
* **Bare earth.** Copernicus GLO-30 is a surface model and carries the tree canopy; a forest
  reads as a 10-20 m plateau. The DTM is the ground, which is what a mesh under photo scenery
  wants.
* **The answers must be read here.** They are big-endian, uncompressed, tiled float32 GeoTIFFs;
  Pillow reads their tags but decodes their samples in the wrong byte order, so `read_wcs_tiff`
  reads the tiles with numpy at the offsets the tags give.
* **A window is served a post or two short** of what was asked (62 x 62 for 64 x 64, 999 x 1000
  for 1000 x 1000): each block is asked for with a post of margin and placed where **its own**
  geometry says, at the centre of its first post -- placing on the tie point's corner shifts a
  block half a post, which moved heights by metres (measured against a single-request reference:
  0.013 m mean, 0.000 m median, over 129 600 posts).

A cell is 4 x 4 blocks of 901 posts, written as `<cell>_HRDEM.hgt` (big-endian int16, `-32768`
for the voids, the format section 5 already reads): 3601 posts a side, one arc-second, ~26 MB.
The lidar is far finer, but a cell at 1/3" would be 466 MB from the service and 233 MB on the
disk, while the gain that counts -- measured ground instead of a radar surface -- is already
there at 1".

### 3.1 `View` (viewfinderpanoramas, J. de Ferranti) — Ortho4XP's default

`O4_DEM_Utils.py:595-735`. Two resolutions:

* **1"** (`dem1/<n43e005>.zip`) for the 34 hard-coded `(lat, lon)` pairs of lines 599-635
  (the Alps), one zip per cell;
* otherwise **3"** (`dem3/<L><NN>.zip`), a 4-degree x 6-degree block named by de Ferranti's
  grid: number `31 + lon // 6` zero-padded to 2, letter `alphabet[lat // 4]` for the north
  (`"S" + alphabet[(-1 - lat) // 4]` for the south). 22 of those blocks are 1" despite
  living under `dem3/` (the list at lines 645-667 only selects `resol = 1`, which changes the
  recycling test below, not the URL).

**Recycling test** (lines 669-676): the local file is reused when it exists **and**
(`resol == 3` **or** its size is at least `25934402` bytes). 25934402 is the size of an
uncompressed 3601x3601 `.hgt` plus 2: a 1201x1201 file (2884802 bytes) therefore never
satisfies it.

**Extraction** (lines 736-767): every member of the zip whose basename parses as
`[NS]dd[EW]ddd*` is written to `elevation_path("View", lat0, lon0)`, and an existing file is
**not** overwritten unless it is at most as large as the archived member ("we don't wish to
overwrite a 1" version by downloading the whole archive of a nearby 3" one"). Members whose
name does not parse are skipped. **Keep**, including the `S`/`W` detection by substring.

### 3.2 `SRTM` and `ALOS`

`O4_DEM_Utils.py:768-778`: since OpenTopography closed its public bucket, Ortho4XP prints a
warning and returns failure; the download code after it is dead (`return 0` precedes it).
**Keep the behaviour, name it**: OrthoStudio XP recycles the local file if present, else raises
`DEM_SOURCE_MANUAL_DOWNLOAD` -- from the 3x3 assembly too, for the tile's own cell, since these
two sources are the ones whose file a user places by hand (`MANUAL_SOURCES`); a neighbour still
degrades to zeros exactly as Ortho4XP does. No dead code is ported. A **downloaded** source that
has no file for the tile's own cell raises `DEM_TILE_UNAVAILABLE` instead: there is nothing to
place by hand, the source simply does not cover it.

### 3.3 `NED1`, `NED1/3`

`O4_DEM_Utils.py:809-828`:
`https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/<1|13>/TIFF/current/<tid>/USGS_<1|13>_<tid>.tif`
with `tid = n|s + |lat+1| + w|e + |lon|`. **Keep** (the `tid` expression of Ortho4XP has a
precedence oddity — `tid = tid + "w" if lon < 0 else "e"` drops the accumulated string in the `else`
branch, so an eastern tile gets `tid = "e"` and the URL is wrong; OrthoStudio XP builds the correct
`n43e005` form: **fix**, declared, unreachable in practice because NED covers only
western longitudes).

Settings offers `NED1/3` as *the detailed relief of the United States* (`relief.source = "usgs"`,
a user of the X-Plane.Org page asked for other sources "especially for the US and Canada",
2026-09-18). Measured on the USGS bucket the same day, per one-degree cell: **410 MB** at
1/3" (n41w106), 449 MB in Alaska (n62w150), 51 MB at 1" -- against ~40 MB for Copernicus. The
coverage is the United States: a Canadian cell answers 404 (n51w114), the cell is `MISSING`, and
`require_own_cell` refuses the build (`DEM_TILE_UNAVAILABLE`, decision 0007) instead of building a
flat tile; the page's remedy names the two sources that cover the region. Canada has no equivalent
free 1/3" set: Copernicus is the answer there until NRCan's HRDEM (1-2 m lidar, partial coverage,
WCS) or MRDEM (30 m) is read.

### 3.4 Negative memo (the fix that motivated this module)

Ortho4XP issues the *same three* `dem1` requests on every single run of the reference tile
(`N43E006`, `N44E005`, `N44E006`): the local 3" files exist but fail the 1"-size test, the
`dem1` zips do not exist on the server, the 404 is not remembered. Measured on the reference
machine: `DEM(43, 5)` takes **1100 ms**, of which **128 ms** is computation (same call with
those three cells short-circuited) -- 970 ms, 88 % of the stage, is three HTTP round trips
that are guaranteed to fail and whose failure changes nothing.

**Fix**: `orthostudio.dem.sources.NegativeMemo` is a JSON file (`<data folder>/dem/misses.json`)
mapping a URL to the unix time of the last refusal. A URL that was refused less than `ttl_s` ago
(default 30 days) is not requested again; `NegativeMemo.forget(url)` and deleting the file are the
escape hatches. A 404 is recorded; a timeout or a 5xx is **not** (the server was
merely unavailable).

**The memo does not change any output.** Whether the request is skipped or answered with a
404, the cell degrades to the same zeros (section 4.3). This is proven, not assumed:
`test_dem_oracle.py::test_combined_raster_is_byte_identical_to_ortho4xp` compares the full
53 963 716 bytes against Ortho4XP, and
`test_dem_oracle.py::test_the_memo_does_not_change_the_raster` rebuilds them with the three
URLs already in the memo and a download hook that fails the test if it is ever called.

### 3.5 What is *not* ported

`http_request` (lines 830-866): six attempts, `2**n` seconds of sleep, `"[20" in str(r)` for status
detection, no connection reuse across calls. OrthoStudio XP uses `orthostudio.net.fetch.Fetcher`
(HTTP/2, AIMD, polite 429, cancellation). The retry ladder is the Fetcher's; the "4xx and 3xx are
final, 5xx are retried" decision of Ortho4XP is preserved by `sources.py` on top of it.

## 4. The combined raster (`build_combined_raster`, lines 350-441)

### 4.1 Geometry

| source | `base` | `overlap` | `beyond` | `x0 = y0` | `x1 = y1` | `nxdem = nydem` |
|---|---|---|---|---|---|---|
| `View`, `SRTM` | 3601 | 1 | 36 | -0.01 | 1.01 | 3673 |
| `ALOS` | 3600 | 0 | 36 | -0.01 + 1/7200 | 1.01 - 1/7200 | 3672 |

`epsg = 4326`, `nodata = -32768`. The coordinates are **tile-local**: `x = lon - tile.lon`,
`y = lat - tile.lat`. 36 pixels at 1" is 0.01 degree, hence `x0 = -0.01`: the raster is a
proper 1.02 x 1.02 degree window. **Keep.**

The `overlap = 1` of `.hgt` is the shared row/column between adjacent cells (a 3601 grid
covers 0..1 inclusive); `ALOS` pixels are area-centred, which is why the window is shifted by
half a pixel and no row is dropped.

### 4.2 Placement

For each of the nine cells (`itertools.product((lat, lat-1, lat+1), (lon, lon-1, lon+1))`),
the centre cell fills `alt[36:-36, 36:-36]` and each neighbour contributes the 36 rows or
columns **on its side of the seam, skipping the shared line**: west gives
`tmp[:, -37:-1]`, east `tmp[:, 1:37]`, north `tmp[-37:-1, :]`, south `tmp[1:37, :]`, and the
corners the corresponding 36x36 block (`ov = 0` for ALOS drops the `-1`). Rows run
north-to-south, columns west-to-east. **Keep**, transcribed as a table
(`raster.PLACEMENTS`) instead of a nine-branch `if`.

### 4.3 Missing cells

A cell contributes `numpy.zeros((base, base), float32)` when

* `world_tiles.png[89 - lat, (180 + lon) % 360]` is 0 (the cell is open sea: 22 369 of
  64 800 cells are land), or
* `ensure_elevation` failed (no local file and no successful download).

**Keep.** The world bitmap is copied verbatim into `src/orthostudio/dem/data/world_tiles.png`
(3703 bytes, 360x180, GPL v3 by derivation like the rest); no Ortho4XP folder is read at run time
(decision 0010). OrthoStudio XP raises `DEM_CELL_ASSUMED_OCEAN` (info) and
`DEM_NEIGHBOUR_UNAVAILABLE` (debug) as events instead of printing, and never turns a missing
*neighbour* into a failure — only a missing centre cell is worth a warning.

## 5. Reading a file (`read_elevation_from_file`, lines 441-593)

| extension | rule |
|---|---|
| `.hgt` | `n = round(sqrt(size / 2))`, big-endian int16 -> float32, shape `(n, n)`. When `n == 1201`: fill the voids *first*, then `upsample` to 3601 (section 6). `x0 = y0 = 0`, `x1 = y1 = 1`, nodata -32768. |
| `.raw` | little-endian int16 (`array("h")`, host order — little on every machine OrthoStudio XP supports), shape `(n, n)`, **rows reversed** (`[::-1]`: the format is south-up). Same window. |
| anything else | a raster read through the optional raster path (section 5.1). |

Any exception degrades to `numpy.zeros((base_if_error, base_if_error))` with the unit window.
**Keep**, but the silent `except:` becomes a `DEM_FILE_UNREADABLE` event carrying the reason,
and the degraded array is still returned (the build must not die because one neighbour cell is
corrupt).

### 5.1 GeoTIFF without GDAL (**fix**)

Ortho4XP needs `osgeo.gdal`, which is not installable from PyPI. OrthoStudio XP reads the TIFF with
Pillow and the geo keys from the TIFF tags directly:

* `ModelPixelScaleTag` (33550) gives `(dx, -dy)`, `ModelTiepointTag` (33922) the raster-to-model
  tie point; together they are the six-value `GetGeoTransform()` of GDAL;
* `GDAL_NODATA` (42113) is the nodata string; absent -> -32768 with `DEM_NODATA_UNDECLARED`;
* `GeoKeyDirectoryTag` (34735) key 2048 (`GeographicTypeGeoKey`) or 3072
  (`ProjectedCSTypeGeoKey`) gives the EPSG code; absent -> 4326 with `DEM_EPSG_UNDECLARED`.
  4326 and 4269 are accepted (as in Ortho4XP, line 531), anything else raises
  `DEM_EPSG_UNSUPPORTED` instead of Ortho4XP's "result is likely to be non sense" warning
  (**fix**: a wrong CRS silently produces a broken tile).

Window, identical to lines 545-549 and assuming `AREA_OR_POINT = Area`:
`x0 = geo[0] + geo[1]/2 - lon`, `y1 = geo[3] + geo[5]/2 - lat`,
`x1 = x0 + (nx - 1) * geo[1]`, `y0 = y1 + (ny - 1) * geo[5]`.
Nodata pixels are rewritten to -32768 and `nodata` is then -32768, as in lines 517-524.

A TIFF Pillow cannot decode (tiled, compressed with an exotic codec) raises
`DEM_RASTER_LIBRARY_MISSING`, whose remedy already names the optional raster extra.

## 6. `upsample` 1201 -> 3601 (lines 910-956)

Piecewise-bilinear on a 3x3 refinement: the source sample lands on every third row and column, and
the two intermediate positions get weights 2/3 and 1/3 (edges) or 4/9, 2/9, 2/9, 1/9 (interiors).
OrthoStudio XP computes the same nine sub-grids with array slices instead of a 1201-iteration Python
loop. The intermediate arithmetic is float64 in both versions (a Python `2/3` promotes the float32
slice), and the result is stored into a float32 array, so the two are **bit-identical**; proven on
the real N43E005 file and on random
rasters (`test_dem_raster.py::test_upsample_matches_the_reference_bitwise`).

The last row and column are a special case in Ortho4XP (`if i == 1200: break` after filling
`3 * 1200`): rows 3601 does not exist, so nothing is lost. **Keep.**

## 7. Voids (`fill_nodata_values_with_nearest_neighbor`, lines 866-910)

Iterative 4-neighbour dilation, up to 20 steps; the extremum is the **maximum** of the four
shifted arrays when `nodata < 0` (the usual case) and the minimum otherwise; the borders are
replicated, not wrapped. A raster with 10 000 or more nodata pixels is refused outright
(return 0) and the caller falls back to "everything to zero"; a raster that still has voids
after 20 steps gets them set to 0. **Keep**, exactly, including the 10 000 threshold and the
"first pass only" nature of the test (it is evaluated before the first dilation, never again).

`fill_nodata` is tri-state in Ortho4XP through Python truthiness: `True` = nearest neighbour,
`"to zero"` = zeros, `False` = *step 1 passes `"to zero"` anyway*
(`O4_Vector_Map.py:210`: `tile.fill_nodata or "to zero"`). OrthoStudio XP makes it an explicit enum
`FillNodata = "nearest" | "zero" | "none"` and the rule maps the Ortho4XP boolean
(`fill_nodata=True -> "nearest"`, `False -> "zero"`), so the default path is unchanged and
`"none"` becomes reachable for a caller that wants the raw voids.

## 8. `Dem` and `alt_vec`

### 8.1 `alt_vec_nostrict` (lines 283-311) — the hot path

Clamp `(x, y)` into the window, map to pixel coordinates `px, py` (continuous), take
`nx = int(px)` and `Nminusny = Ny - int(py)`, and interpolate **on the triangle** of the pixel
square that contains the point: with `rx = px - nx` and `ry = py - int(py)`,

```
rx >= ry:  (1 - rx) * t1 + ry * t2 + (rx - ry) * t3
rx <  ry:  (1 - ry) * t1 + rx * t2 + (ry - rx) * t4
```

where `t1 = alt[Nminusny, nx]` (south-west), `t2` the north-east, `t3` the south-east and `t4`
the north-west corner, each index clamped at the last row/column. This is the same
triangulation Triangle4XP sees, which is why the mesh has no altitude seam.

Ortho4XP evaluates `t1..t4` with **four Python list comprehensions over `zip`** — one interpreter
round trip per point per corner. OrthoStudio XP indexes the array (`alt[rows, cols]`). The
arithmetic is unchanged: the corner values are float32, every coefficient is float64, both versions
promote to float64 before the multiply, so the results are **bit-identical**, proven on 100 000
pseudo-random points of the reference tile against an Ortho4XP
subprocess (`test_dem_oracle.py::test_alt_vec_matches_ortho4xp_bitwise`).

**Kept quirks**, because they are observable:

* `nx = px.astype(uint16)` truncates towards zero *after* the clamp, so `x = x1` gives
  `nx = Nx` and the `(nx + 1) * (nx < Nx) + Nx * (nx == Nx)` guard folds the east column onto
  itself. OrthoStudio XP keeps the uint16 cast (a raster of more than 65 535 pixels would break both
  engines identically) and the same guard expression.
* `Nminusny - 1` is clamped by multiplication (`* (Nminusny >= 1)`), not by `max`.
* Out-of-window points are clamped, never refused: `alt_vec` is total.

### 8.2 `alt_vec_strict` (lines 313-327) and composites

`alt_strict` rounds to the nearest pixel and returns `nodata` outside the window; a
`custom_dem` of the form `"base;overlay1;overlay2"` builds one `Dem` per overlay and overlays
them in order (`alt_vec_composite`, lines 329-334: later overlays win where they have data).
**Keep**, including the priority order.

**Difference, and it matters: the overlays are written into the raster** (`_lay_into`, called by
`Dem.build`), not kept beside it. Ortho4XP builds a tile in one run and can ask its overlays at
every point; here the raster is written to the store and read again by the vector and mesh
stages, which know nothing of an overlay. Kept beside it, a user's own file was found, keyed and
then quietly dropped: the tile came out of the base alone. Found on 2026-09-19 on a real build of
`+46+006` whose lidar folder changed nothing at all, and true of `COP30;HRDEM` just the same.
Each overlay is laid nearest-point where it has data, exactly as `alt_vec_strict` would have read
it, and **the finest step in the room wins, both ways**: an overlay sharper than the base raises the
whole window to its own grid first (bilinear, block by block, up to `MAX_COMPOSITE_SIDE = 12 000`
points a side, which a 1/3" overlay over an assembled window stays under), and an overlay coarser
than the base is left aside with `DEM_OVERLAY_COARSER`, which names the file and both steps (a user
with Sonny's 1" sets and the USGS 1/3" relief asked which one answers, 2026-09-20; the American sets
of one's own are made from that very source). Equal steps go to the overlay, which is what a file
of one's own is for. The comparison is on the *file*, not on the raster in hand (`_file_step`):
`read_elevation_from_file` refines a 1201 point `.hgt` to 3601 as Ortho4XP does, so a 3" file would
otherwise pass for a 1" one. `meta.json` names what was
laid in `laid_over`, and the overlay's file joins `cells`. Difference: Ortho4XP returns a float32 array when every
point is inside and float64 otherwise (`numpy.array` of a mixed list); OrthoStudio XP always returns
float64 (**fix**, value-preserving, float32 -> float64 is exact).

### 8.3 `smoothen` (lines 956-1002) and airport smoothing

`smoothen(raster, pix_width, mask, preserve_boundary)` is a **separable triangular-kernel
convolution weighted by the mask** (the name suggests a relaxation; it is not one): the kernel
is `[1, 2, ..., pix+1, ..., 2, 1] / (pix+1)**2`, applied on rows then on columns to
`raster * mask` and to `mask` separately, and the quotient is blended back with the original by
the mask value. `preserve_boundary` linearly re-blends the `pix` outermost rows and columns
with the input. OrthoStudio XP ports it verbatim and **keeps the per-row `numpy.convolve` loop on
purpose**: a vectorised accumulation over the kernel taps sums the taps in a different order
and stops being bit-identical past a width of about four (measured 1.5e-05 m at
`pix_width = 8` on a float32 raster). Smoothing runs on airport windows, not on the whole
raster, so the loop is not on any hot path. Bit-identity is proven against the real Ortho4XP
module (`test_dem_oracle.py::test_smoothen_matches_ortho4xp_bitwise`) and against a transcription
(`test_dem_raster.py::test_smoothen_matches_the_reference_bitwise`).

`smooth_over_regions(alt, regions, max_pix, preserve_boundary)` is the pure-function half of
`O4_Airport_Utils.py:924-1034`: it takes already-rasterised regions
`(rowmin, rowmax, colmin, colmax, mask, pix)` and applies `smoothen` to each window, then the
global boundary re-blend. **The other half** — turning the airport dictionary into those
masks (unary union of boundary, runways, hangars, taxiways, aprons; 10 m upscale;
`Image.BICUBIC` downscale) — is airport/vector work and is *not* in this module (section 9).

## 9. Artefact and rule `orthostudio.dem@1`

`kind = "dir"`, `ram_mb = 400` (two 3673x3673 float32 arrays plus a 3601 float64 working copy;
measured peak 331 MB, section 11). Params (and only these — the key must not move when an
unrelated setting changes):

| param | type | meaning |
|---|---|---|
| `tile` | `str` | `"+43+005"`; the output depends on it |
| `custom_dem` | `str` | Ortho4XP's `custom_dem`: empty (View, or `<base>.tif` if present), a source name, a file path, or `"a;b;c"` for a composite |
| `fill_nodata` | `bool` | Ortho4XP's tri-state, as its boolean (section 7) |
| `dem1_local_fallback` | `bool` | osxp-only, default `False` (section 9.1) |

No input: the DEM is a root of the graph (its real input, the elevation files, is external
state; the rule is keyed by its params and re-run when they change). Files written to `ctx.out`:

```
Data<tile>.alt   the raster, float32 row-major, no header (4 * nxdem * nydem bytes)
dem.npy          the same array as a .npy (mmap-able by the mesh and the masks rules)
meta.json        {"format": "osxp-dem-1", "tile", "source", "epsg", "x0", "y0", "x1", "y1",
                  "nodata", "nxdem", "nydem", "min", "max", "mean", "nodata_pixels",
                  "cells": [{"cell", "state", "path"} x 9]}
```

`Dem.load(dir)` reads it back through `numpy.load(mmap_mode="r")`, so a consumer that only
needs `alt_vec` never copies 54 MB.

### 9.1 The one deliberate behaviour switch

`dem1_local_fallback = True` uses the local 3" file when the 1" zip cannot be downloaded,
instead of zeroing the cell. It is **off by default** because turning it on changes
`Data<tile>.alt` (the north, east and north-east margins of the reference tile stop being
zeros) and therefore the mesh. Marked TODO in `sources.py`: see blocker B2.

### 9.2 What this rule does **not** produce

`Data<tile>.alt` as found in an Ortho4XP build directory is the raster **after airport smoothing**
(`O4_Airport_Utils.py:1034` is the only writer on the normal path). On the reference tile that
smoothing moves 21 923 of 13 490 929 pixels (0.163 %), by at most 13.57 m. Airport polygons
come from OSM, i.e. from the vector stage (P4), not from the elevation stage, and rule A5
forbids this module from reaching into it. So:

* `orthostudio.dem@1` writes the **unsmoothed** raster and is byte-identical to Ortho4XP's
  `DEM` object;
* `smooth_over_regions` is the exact function the airport stage must call to reach the
  smoothed bytes;
* the integrator wires `orthostudio.airports/vectors -> smooth_over_regions -> Data<tile>.alt`.

**Closed, P4 wave 2.** That wiring exists:
`orthostudio.airports_vec.smoothing.smooth_dem_over_airports` calls `smooth_over_regions` with the
airport footprints and `orthostudio.vectors@1` publishes the result as its own `Data<tile>.alt`
(with a `dem.json` beside it, so the mesh node can take the vector artefact as its `dem` input).
Measured on +43+005: **byte-identical** to the reference build, 0 of 13 490 929 float32 samples
differing (`tests/test_p4v2_oracle.py::test_the_alt_of_the_artefact_is_byte_identical`,
`docs/specs/airports-integration.md` 3, `docs/benchmarks/p4-airports.md` 2.1). `orthostudio.dem@1`
itself is unchanged and still publishes the raw raster: it is a root of the graph and cannot depend
on OSM (ADR 0006).

See blocker B1.

### 9.3 What the key does **not** follow (known limit)

`orthostudio.dem@1` is a graph root: its key is a pure function of its params, and its real input --
the files under `Elevation_data/` -- is external state (section 9). The consequence, which this
spec used to leave implicit:

> **Changing the content of an elevation file, or of the file `custom_dem` points at, does
> not change the key.** The stored artefact stays valid and is returned as a hit, with the
> values of the *first* build. To rebuild: delete the artefact (the store path `osxp why`
> prints), or change a parameter.

Closing this properly means turning a `custom_dem` that is a **path** into a source edge
(content digest) at declaration time, exactly as `_coastline_node` does for the Ortho4XP
`.osm.bz2`; that adds an input to the rule and therefore changes every stored key, so it is
reported as a blocker rather than done in a fix pass. The cells of the elevation directory cannot
be keyed that way at all (there are nine of them per tile, downloaded on demand).

### 9.4 Cancellation and atomic writes

* `DemJob.cancel` reaches `EnsureOptions.cancel`, which is polled before every download and
  between the nine cells of the 3x3 block (`build_combined_raster`); it raises
  `SYS_CANCELLED`, so a cancelled node commits nothing.
* every elevation file OrthoStudio XP writes goes through `_atomic_write_bytes`
  (`<name>.part-<pid>` then `os.replace`), so a download killed half way does not leave a stump a
  later run accepts for ever;
* the cells are looked for, and downloaded into, `elevation_dir` (`$OSXP_ELEVATION_DIR`, else
  `<data folder>/elevation`); no Ortho4XP folder is read (decision 0010).
* a local `NED1` / `NED1/3` cell is also checked for the TIFF byte-order mark before it is
  accepted (`is_file()` alone accepted a truncated file, which a killed download leaves);
  the `View` branch already checked the 1" size (`FULL_HGT_SIZE`).

## 10. Acceptance tests

| # | Test | Level |
|---|---|---|
| A1 | `test_combined_raster_is_byte_identical_to_ortho4xp` (53 963 716 bytes, `+43+005`, source View, fill nearest) | A |
| A2 | `test_alt_vec_matches_ortho4xp_bitwise` (100 000 points, `numpy.array_equal` on the raw bytes) | A |
| A3 | `test_upsample_matches_the_reference_bitwise` (real N43E005 + 20 random 1201 rasters) | A |
| A4 | `test_fill_nodata_matches_the_reference_bitwise` (30 random rasters with voids, incl. the 10 000 and 20-step limits) | A |
| A5 | `test_smoothen_matches_the_reference_bitwise` (20 random raster/mask/pix combinations, both `preserve_boundary`) | A |
| A6 | `test_alt_strict_and_composite_match_ortho4xp` | A |
| A7 | `test_rule_artifact_roundtrip` (`Dem.load` of the artefact equals the built object; `meta.json` schema) | - |
| A8 | `test_negative_memo_*`, `test_a_1sec_cell_with_only_a_3sec_file_is_downloaded_again`, `test_the_memo_does_not_change_the_raster` | A (bytes) |
| A9 | `test_sources_names_and_urls` (the 34 dem1 cells, the 22 1"-blocks, zip extraction rules) | - |

The tests marked A ran against Ortho4XP in a subprocess until decision 0010 removed them; A3 to A5
compare with transcriptions of Ortho4XP written into `tests/test_dem_raster.py` and remain. Nothing
in `src/` imports the Ortho4XP sources.

## 11. Measurements

M4 Pro (14 cores, 48 GB), macOS 15.6, `nice -n 10`, `uptime` = 5 days, load average 4.6.
Tile `+43+005`, source View, `fill_nodata = nearest`: nine 1-degree cells of which two are
ocean, three are the unavailable `dem1` cells, four are real 1201 `.hgt` files that must be
void-filled and upsampled to 3601.

| Operation | Ortho4XP | OrthoStudio XP | Ratio |
|---|---|---|---|
| 3x3 assembly, cold page cache (files copied to a fresh path) | - | **96 ms** | - |
| 3x3 assembly, warm (min of 5) | 128 ms (network short-circuited) | **74 ms** | x1.7 |
| 3x3 assembly, as a user actually runs it | **1100 ms** (3 forced 404s) | **74 ms** | **x14.9** |
| `alt_vec` on 100 000 points | 99 ms | **6.5 ms** | **x15.2** |
| `write_alt` (53 963 716 bytes) | 4 ms | 4 ms | x1 |
| Peak process RSS for the whole stage | 429 MB | **387 MB** | -10 % |
| Python peak allocation (`tracemalloc`) | - | 227 MB | - |

The raster itself is 54 MB; the declared `ram_mb = 400` covers the raster, the four 3601
intermediates of the upsample and the fill working arrays with margin.

Where the x15 on `alt_vec` comes from: Ortho4XP builds four Python lists of 100 000 `numpy.float32`
scalars per call (`[self.alt_dem[i][j] for i, j in zip(...)]`), i.e. 400 000 interpreter round trips
and 400 000 temporary objects; OrthoStudio XP issues four fancy-indexing gathers. The arithmetic
that follows is identical, which is why the results are bit for bit equal.

Where the x1.7 on the assembly comes from: the work is dominated by four `upsample(1201 -> 3601)`
calls, which Ortho4XP runs as a 1201-iteration Python loop over nine slice assignments each (10 809
slice operations) and OrthoStudio XP as nine whole-array slice assignments.

Reproduce with `OSXP_BENCH=1 uv run pytest tests/test_dem_bench.py -q -s -o addopts=''`.

## 12. X-Plane 12's relief (`XP12`, OrthoStudio XP only)

Decision 0007. Module `orthostudio.dem.xplane` (reading), `orthostudio.dem.raster` (assembly).

* **Source.** Every Global Scenery DSF holds, in `DEFN/DEMN` + `DEMS`, a raster named
  `elevation`: 1201 x 1201 signed 16-bit posts (`DEMI` flags `0x5`: integer, post-centred),
  scale 1, offset 0, rows **south to north**, sea at 0 m. The reader pairs each `DEMI` with
  the following `DEMD`, flips the rows, applies scale and offset, and maps the most negative
  integer to `-32768`. Other shapes are accepted and refined bilinearly; a pixel-centred or
  missing `elevation` raster is `DSF_SOURCE_CORRUPTED`.
* **Assembly.** Same geometry as `View` (3601 posts, 1-post overlap, 36 posts beyond, window
  `[-0.01, 1.01]`). A 1201 cell is void-filled and refined by `upsample_1201_to_3601`, so the
  same posts give the same raster as a 3" `.hgt`, to the bit (`tests/test_dem_xplane.py`).
* **Cells.** A cell with a DSF is `local`, even where `world_tiles` says sea. Without one, a
  sea cell is `ocean` and a land cell `missing`; both contribute zeros.
* **Shared border.** Before refinement, each border post of the tile becomes the mean of the
  tiles that carry it: two along a side, up to four at a corner (`math.fsum`, so the order of
  the terms does not matter). X-Plane's rasters differ by up to 31 m on some borders
  (`+46+006`/`+46+007`), and two adjacent OrthoStudio XP tiles must meet on one line.
* **Key.** The rule has nine inputs, `xp12`, `xp12_n` ... `xp12_sw`: the DSF of each cell,
  absent when X-Plane has none. Under the store the rule reads only those files. The pipeline
  refuses to declare the node when the tile's own DSF is missing (`DEM_TILE_UNAVAILABLE`,
  remedy: install that region of the Global Scenery, or `--relief view`).
* **Own cell.** `Dem.build` raises `DEM_TILE_UNAVAILABLE` when the tile's own cell is
  `missing`, for every source. Ortho4XP builds a flat tile instead. An unreadable file now counts
  as `missing` too, where Ortho4XP counted it as available.

## 13. Not there yet

* No reprojection: a DEM that is not EPSG:4326/4269 is refused, not warped.
* `create_normal_map` and `super_level_set` (lines 175-230) are not ported: the first is a
  debugging helper, the second belongs to the masks stage and is re-derived there from the
  mask rasters.
* The negative memo is per-URL, not per-host; a host that is entirely down is retried at the
  next build.
* No streaming of a cell that is larger than memory (an ALOS 1-degree tile is 26 MB; the
  question only arises for a national 1/9" DEM).
