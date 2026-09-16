# Native mesh stage (`orthostudio.mesh@1`): weight map, Triangle4XP, altitude post-processing

Status: P3, written **before** the code of `src/orthostudio/mesh/weights.py`,
`src/orthostudio/mesh/postprocess.py`, `src/orthostudio/mesh/build.py` and
`src/orthostudio/mesh/rule.py`. Tests: `tests/test_meshbuild_weights.py`,
`tests/test_meshbuild_postprocess.py`, `tests/test_meshbuild_rule.py` (unit); until decision 0010,
`tests/test_meshbuild_oracle.py` compared the stage with Ortho4XP's build of +43+005.

Origin: Ortho4XP's `src/O4_Mesh_Utils.py`

| What | Where in Ortho4XP |
|---|---|
| `build_mesh` (stage 2 driver, command line, retry, clean-up) | `O4_Mesh_Utils.py:540-783` |
| `build_curv_tol_weight_map` (1001² curvature-tolerance weights) | `O4_Mesh_Utils.py:132-228` |
| `post_process_nodes_altitudes` (water / sea / interpolated altitudes) | `O4_Mesh_Utils.py:230-324` |
| `write_mesh_file` (the `.mesh` text writer) | `O4_Mesh_Utils.py:326-372`, already ported in `mesh_file.write_mesh_text` (`docs/specs/mesh-file.md`) |
| Attribute bit set `DUMMY … HANGAR` | `O4_Vector_Utils.py:44-54` |
| `lat_to_m`, `lon_to_m`, `m_to_lat`, `m_to_lon` | `O4_Geo_Utils.py:4-16` |
| DEM window (`x0 y0 x1 y1`, `nodata`, `nxdem`, `nydem`) | `O4_DEM_Utils.py:350-374` (`build_combined_raster`) |
| Consumers of the `.mesh`: masks and DSF | `O4_Mask_Utils.py:432-620`, `O4_DSF_Utils.py:471-473` |

This spec **replaces stage 2 of Ortho4XP**, which no longer runs inside OrthoStudio XP (decision
0009). Ortho4XP's own output was the oracle until decision 0010; the tests now check the stage
against transcriptions of Ortho4XP's code written into them.

## 1. The rule in plain language

Stage 2 turns the *planar straight-line graph* of the vector stage (`Data<tile>.node` +
`Data<tile>.poly`: 247 282 vertices and 271 320 constrained segments on +43+005) and the
elevation raster of the DEM stage (`Data<tile>.alt`) into the triangulated terrain of the
tile (`Data<tile>.mesh`: 603 649 vertices, 1 197 758 triangles). Three things happen:

1. **Weight map.** A 1001 × 1001 float32 raster over the tile says, per 1/1000° cell, by how
   much the curvature tolerance must be divided there. It is 1 everywhere except over
   airports (`curvature_tol / apt_curv_tol`) and near the coastline
   (`curvature_tol / coast_curv_tol`): those places get a finer mesh. Triangle4XP reads it
   with one `fread` and uses it in its `triunsuitable()` test.
2. **Triangle4XP.** The sidecar (ADR 0004, `docs/specs/mesh-triangle-io.md`) computes a
   constrained, quality, curvature-adaptive Delaunay triangulation and returns, per vertex,
   `x y z u v alt`: position, altitude sampled in the DEM, the two normal components, and
   the vector altitude carried over from the input `.node`; per triangle, three corners and
   the *plagued* region attribute (a bit set: `WATER 1`, `SEA 2`, `SEA_EQUIV 4`,
   `INTERP_ALT 8`, `RUNWAY 16`, `TAXIWAY 32`, `APRON 64`, `HANGAR 128`).
3. **Altitude post-processing.** Inland water is flattened by `water_smoothing` Gauss-Seidel
   sweeps, sea is forced to 0 (or averaged, or clamped to ≥ 0), and the vertices of
   airport / road / patch triangles take the altitude the vector stage computed for them and
   lose their normal. Then the `.mesh` text file is written.

The rule also publishes what the masks stage needs from the mesh (`water_tris.npz`,
section 7) so that stage 2.5 never has to re-parse a 69 MB text file.

## 2. Inputs

### 2.1 `vectors` (artefact, `kind=dir`)

| File | Required | Read by |
|---|---|---|
| `Data<tile>.node` | yes | Triangle4XP (converted to `OSXPNOD1`), and its header gives `input_nodes` |
| `Data<tile>.poly` | yes (unless `iterate`) | Triangle4XP (converted to `OSXPPOL1`) |
| `airports.json` | no | the weight map (section 3.2) |

The producer is `orthostudio.vectors@1`, whose directory holds `Data<tile>.{node,poly,alt}`,
`dem.json` and `airports.json` beside its airport record (`airports-integration.md` 4).

### 2.2 `dem` (artefact, `kind=dir`)

| File | Required | Content |
|---|---|---|
| `Data<tile>.alt` | yes | `nydem` rows of `nxdem` float32, row 0 = north, exactly `DEM.write_to_file` |
| `dem.json` | no (inferred) | `{"format": "osxp-dem-1", "nxdem": …, "nydem": …, "x0": …, "y0": …, "x1": …, "y1": …, "nodata": …, "epsg": 4326}` |

`dem.json` is the contract this stage needs from the DEM stage: the five numbers
Triangle4XP takes on its command line cannot be recovered from the raster alone. **Until
that file exists** (blocage B1) the reader infers them from the size of `Data<tile>.alt`
under the View/SRTM layout of `O4_DEM_Utils.py:354-362` — `nxdem = nydem = sqrt(size/4)`,
`margin = (nxdem - 3601) / 2 / 3600`, `x0 = y0 = -margin`, `x1 = y1 = 1 + margin`,
`nodata = -32768` — which reproduces the reference tile exactly (3673 × 3673, ±0.01°) and
raises `MESH_INPUT_MISSING` when the size is not a square of an odd 1″ grid.

### 2.3 `coastline` (artefact, `kind=dir`, optional)

`coastline.npz`: `format = "osxp-coastline-nodes-1"`, `nodes` float64 `(C, 2)` = absolute
`lon, lat` of every node of the OSM `natural=coastline` ways covering the tile (the whole
Overpass answer, *not* clipped: the clipping to the tile is part of the weight rule, section
3.3). This stage **never downloads anything** — that is the whole point of taking the
coastline as an input; Ortho4XP re-queries Overpass in the middle of stage 2
(`O4_Mesh_Utils.py:166-198`), which is why its stage 2 can hang on a cold tile.

When the input is absent the coastline term of the weight map is skipped and
`MESH_WEIGHT_MAP_INCOMPLETE` (degraded, continue) is recorded in `stats.json`. When
`coast_curv_tol == curvature_tol` the term is skipped without any warning, like Ortho4XP.

## 3. The weight map (`weights.py`)

### 3.1 Grid

`numpy.ones((1001, 1001), dtype=float32)`. Row `r` is latitude `lat + 1 - r/1000`
(row 0 = north edge), column `c` is longitude `lon + c/1000`. Written raw with
`tofile` to `Data<tile>.weight` (4 008 004 bytes). Triangle4XP reads exactly 1001 × 1001
float32 (`Triangle4XP.c` main, one `fread`), so the size is not negotiable.

### 3.2 Airports (`O4_Mesh_Utils.py:133-159`)

Applied only when `apt_curv_tol != curvature_tol and apt_curv_tol > 0`. For every airport
bounding box `(xmin, ymin, xmax, ymax)` in **tile-relative degrees** (that is how Ortho4XP
stores it: `dico_airports[icao]["boundary"].bounds` of a shapely geometry built in
tile-relative coordinates):

```
x_shift = 1000 * apt_curv_ext * m_to_lon(lat)        # apt_curv_ext is in km
y_shift = 1000 * apt_curv_ext * m_to_lat
colmin  = max(round((xmin - x_shift) * 1000), 0)
colmax  = min(round((xmax + x_shift) * 1000), 1000)
rowmax  = min(round(((1 - ymin) + y_shift) * 1000), 1000)
rowmin  = max(round(((1 - ymax) - y_shift) * 1000), 0)
weight[rowmin:rowmax + 1, colmin:colmax + 1] = curvature_tol / apt_curv_tol
```

with `m_to_lat = 180 / (pi * 6378137)` and `m_to_lon(lat) = m_to_lat / cos(pi*lat/180)`.
`round` is Python's / numpy's round-half-to-even; the slice is inclusive on both ends
(`rowmax + 1`), and the clamp to 1000 combined with `+ 1` makes the last row and column
reachable. This is an **assignment**, not a maximum: a later airport can lower an earlier
one (all airports share the same value, so this only matters if `apt_curv_tol >
curvature_tol`, where the map legitimately drops below 1). Kept as is.

Source of the boxes: `airports.json` — `{"airports": [{"bounds": [xmin, ymin, xmax, ymax]}, …]}`,
tile-relative degrees, the boxes of the `boundary` of every airport Ortho4XP pickles into
`Data<tile>.apt` at the end of stage 1. OrthoStudio XP reads no pickle: the vector stage writes the
file (`vectors-assembly.md` 5). A missing or
unreadable file is not fatal: the airport term is skipped and `MESH_WEIGHT_MAP_INCOMPLETE`
recorded, exactly like Ortho4XP's `except: dico_airports = {}` (`O4_Mesh_Utils.py:140-149`).

### 3.3 Coastline (`O4_Mesh_Utils.py:160-228`)

Applied only when `coast_curv_tol != curvature_tol`. For every coastline **node** whose
`lon ∈ [tile.lon, tile.lon + 1]` and `lat ∈ [tile.lat, tile.lat + 1]` (inclusive, `<` / `>`
in Ortho4XP):

```
x_shift = 1000 * coast_curv_ext * m_to_lon(lat)      # lat = the tile's integer latitude
y_shift = coast_curv_ext / 111.12                    # NOT 1000 * coast_curv_ext * m_to_lat
colmin  = max(round((lonp - lon - x_shift) * 1000), 0)
colmax  = min(round((lonp - lon + x_shift) * 1000), 1000)
rowmax  = min(round((lat + 1 - latp + y_shift) * 1000), 1000)
rowmin  = max(round((lat + 1 - latp - y_shift) * 1000), 0)
weight[rowmin:rowmax+1, colmin:colmax+1] = maximum(…, curvature_tol / coast_curv_tol)
```

Two Ortho4XP oddities are **kept** (they are what the reference tile was built with, and both
are harmless):

* the latitude half-width uses the hard-coded `111.12` km/degree instead of `m_to_lat`
  (a 0.18 % difference: 4 rows either way instead of 4, here);
* the window is a **rectangle per node**, so a coastline is thickened node by node, not
  segment by segment. A way whose nodes are more than `2 * coast_curv_ext` apart gets a
  dashed refinement band. Reproduced as is (a fix would change the mesh of every coastal
  tile and belongs to a separate decision).

**Vectorisation, and why not `maximum_filter`.** The natural reading of "a rectangular window per
node" is `scipy.ndimage.maximum_filter` with a rectangular footprint, but that is *not* the same set
of cells: the two edges of the window are rounded independently (`round(u - s)` and `round(u + s)`
for `u = (lonp - lon) * 1000`), so the width alternates between `floor(2s)` and `ceil(2s)` cells
depending on the fractional part of `u`, while a filter of fixed footprint centred on `round(u)`
always paints the same width. Measured on +43+005 (38 266 nodes inside the tile, half-widths
6.14 and 4.50 cells): the filter disagrees with Ortho4XP on **1 687 cells** of the 1 002
001. OrthoStudio XP therefore paints the exact integer windows with a 2-D difference array
(`+1 / -1` at the four corners of every rectangle, then `cumsum` on both axes, then `> 0`), which is
exact by construction, needs one pass over the nodes and one over the grid, and costs 8.8 ms for the
whole tile against 80 ms for Ortho4XP's Python loop. The equivalence is elementary: with a single
constant value, "the union of the rectangles" is "the cells whose corner-count is positive".

### 3.4 Order

Airports first (assignment), coastline second (maximum), exactly as Ortho4XP. On +43+005 with
`curvature_tol=2, apt_curv_tol=0.5, coast_curv_tol=1`: 960 665 cells at 1.0, 30 355 at 2.0
(coast), 10 981 at 4.0 (airports) — **41 336 cells ≠ 1, maximum 4**, the numbers measured in
P0.

## 4. The Triangle4XP command (`build.py`)

Reconstituted from `O4_Mesh_Utils.py:632-670` and proved equal to Ortho4XP's on +43+005 by
`tests/test_mesh_triangle_io.py::test_oracle_marseille_official_vs_osxp_text_vs_binary`.

```
argv[0]  <triangle_bin>                       native/triangle4xp/build/Triangle4XP
argv[1]  -pq{min_angle:.9g}{A|r}uYB{Q|V}{P}{b}S{max_steiner}
argv[2]  {lon_to_m(lat):.9g}        scalx     metres per degree of longitude at the tile's latitude
argv[3]  {lat_to_m:.9g}             scaly     111319.491
argv[4]  {nxdem}                              DEM columns
argv[5]  {nydem}                              DEM rows
argv[6]  {x0:.9g}  argv[7] {y0:.9g}           DEM window, tile-relative degrees
argv[8]  {x1:.9g}  argv[9] {y1:.9g}
argv[10] {nodata:.9g}
argv[11] {curvature_tol:.9g}
argv[12] <alt file>
argv[13] <weight file>
argv[14] <poly file>                          .poly ⇒ b->poly = 1 (PSLG mode)
```

* `lat_to_m = pi * 6378137 / 180`, `lon_to_m(lat) = lat_to_m * cos(pi*lat/180)`, with
  `lat` the tile's **integer** latitude (Ortho4XP uses `tile.lat`, not the tile centre;
  `VECT.scalx` at `O4_Mesh_Utils.py:545` uses `lat + 0.5` but that is the vector stage's
  own scale, not Triangle's).
* switches: `-p` PSLG, `-q{min_angle}` quality, `-A` region attributes (or `-r` refine when
  `iterate > 0`), `-u` the Triangle4XP `triunsuitable()` curvature test, `-Y` no Steiner
  point on the boundary, `-B` no boundary markers in the output, `-Q` quiet, `-P` no output
  `.poly`, `-b` binary exchange (OrthoStudio XP, ADR 0004), `-S{n}` Steiner point budget.
* `max_steiner`, verbatim from Ortho4XP: `max_tris = float(limit_tris) * 1e6`, replaced by
  `5e6` when `≤ 0` or `≥ 5e7`; `max_steiner = max(max_tris / 1.9 - input_nodes, 5e5)`;
  appended as `"S" + str(max_steiner)` — the `repr` of the float, digits and all.
  On +43+005: `S1331665.3684210528`.
* `{:.9g}` everywhere it is used by Ortho4XP, so the numbers Triangle parses are the same
  doubles (`%.9g` round-trips `float32`-grade constants and both sides go through `atof`).

Reference command for +43+005 (OrthoStudio XP, binary mode):

```
Triangle4XP -pq10AuYBQPbS1331665.3684210528 81413.9217 111319.491 3673 3673 \
  -0.01 -0.01 1.01 1.01 -32768 2 <alt> <weight> <poly>
```

### 4.1 Wanted differences on the command

| | Ortho4XP | OrthoStudio XP | Why |
|---|---|---|---|
| Exchange format | text `.node`/`.poly` in, text `.1.node`/`.1.ele` out | `-b`, binary (`docs/specs/mesh-triangle-io.md`) | 4.4 s → 1.25 s, proved bit-identical |
| `{:n}` on `nxdem`, `nydem` | locale-dependent (`3,673` under a grouping locale) | `str(int(...))` | Ortho4XP bug, invisible in the C locale |
| Verbosity | `Q` or `V` depending on the UI | always `Q`, stdout captured | stdout is a log line, not a UI |
| Output `.poly` | `P` only when `cleaning_level` (default 1) | always `P` | OrthoStudio XP never reads the output `.poly` |
| **Retry after a failure** | `mesh_cmd[-5] = "0"` — which is **`nodata`**, not the switch string (`O4_Mesh_Utils.py:711`) | the switch string is rebuilt with `min_angle = 0` | see below |

**The retry bug.** Ortho4XP announces "it will be tempted now with no angle constraint
(i.e. min_angle=0)" and then overwrites `mesh_cmd[-5]`. The command has 15 elements, so
`[-5]` is `argv[10]` — the `nodata` value. The retry therefore keeps `-q10` (so it usually
fails again for the same reason) and additionally sets `nodata = 0`, which changes the
`alt[...] == no_data` test of `altitude()` and silently turns every 0 m sample into a hole.
**Corrected (wanted difference):** OrthoStudio XP rebuilds the switch string with `min_angle = 0`
(`-pq0AuYB…`) and leaves every positional parameter untouched, records
`MESH_QUALITY_RELAXED` (degraded, continue) in `stats.json`, and raises
`MESH_TRIANGULATION_FAILED` (blocking) if the retry fails too. No fixture covers the retry
(the reference tile succeeds on the first run); it is tested on a synthetic PSLG whose
first run is made to fail by an impossible `min_angle`.

### 4.2 `iterate` (TODO, untested)

`iterate = n > 0` refines an existing mesh: inputs are `Data<tile>.<n>.{node,poly,alt}`,
the switch is `-r` instead of `-A`, and the post-processed node table must be written back
as `Data<tile>.<n+1>.node` for the next round. `-b` is refused with `-r`
(`Triangle4XP.c:3448`), so OrthoStudio XP falls back to the text exchange in that mode. There is no
fixture for it and the vector stage does not produce the numbered inputs yet: implemented
for completeness, marked TODO in the code, and excluded from the fidelity claim.

## 5. Post-processing (`postprocess.py`)

Input: the `(N, 6)` table of the output `.1.node` — `x y z u v alt` — and the `(M, 3)`
corners with their `(M,)` attribute. `O4_Mesh_Utils.py:230-324`, in order:

1. **Classification.** A triangle whose attribute is `0` needs nothing. Otherwise
   `attr >= INTERP_ALT (8)` → *interp_alt*; else `attr & SEA (2)` → *sea*; else
   `attr & WATER (1)` or `attr & SEA_EQUIV (4)` → *water*. Note `>= 8`, not `& 8`: a runway
   (16), a taxiway (32), an apron (64) and a hangar (128) are all treated as *interp_alt*.
2. **Inland water smoothing**, `water_smoothing` times (default 10): for every *water*
   triangle, its three vertices take the mean of their three altitudes. This is a
   Gauss-Seidel sweep — the assignment is immediate, so **the order of the triangles
   changes the result**.
3. **Sea smoothing**: `sea_smoothing_mode == "zero"` → `z = 0`; `"mean"` → the same
   Gauss-Seidel mean as water; anything else → `z = max(z, 0)`.
4. **Airports, roads, patches**: every vertex of an *interp_alt* triangle takes `alt`
   (column 5, the altitude the vector stage attached to the input vertex) as its altitude
   and gets a zero normal.

Steps 1, 3 (`zero` and the `max` variant) and 4 are idempotent per vertex, so OrthoStudio XP does
them with numpy fancy indexing on the unique vertex indices — same result, no loop. Step 2 (and
`sea_smoothing_mode == "mean"`) are order-dependent and are discussed below.

### 5.1 The two Ortho4XP quirks that the byte-identity target forces us to keep

**(a) `line[-2] == "0"` — `O4_Mesh_Utils.py:245`.** Ortho4XP decides "this triangle is a dummy"
by looking at the **last character of the line before the newline**, which is the last
decimal digit of the attribute. It therefore also skips every attribute that is a multiple
of ten: `10 = SEA|INTERP_ALT`, `20`, `30`, …, `160 = TAXIWAY|HANGAR`. On +43+005 there are
**97 triangles with attribute 10** that Ortho4XP does *not* treat as `INTERP_ALT`; treating
them correctly changes **52 lines** of the `.mesh` (measured: the 291 corners collapse to 52
distinct vertices not already fixed by another triangle). Reproducing the skip is necessary
for the `.mesh` to be byte-identical.
**(b) Set iteration order.** `water_tris`, `sea_tris` and `interp_alt_tris` are Python
`set`s of `(v1, v2, v3)` tuples of ints, filled in `.1.ele` order. The water smoothing
iterates the *set*, whose order is CPython's hash order — deterministic (int hashing is not
randomised) but unrelated to the file order. Measured on +43+005 with the default
`water_smoothing = 10`: iterating in `.1.ele` order instead of set order changes **97 622
of the 603 649 vertex lines** of the `.mesh`. Byte-identity therefore requires building the
same `set` and iterating it.

Both quirks are behind a parameter, defaulting to Ortho4XP's behaviour:

| Parameter | Default | Meaning |
|---|---|---|
| `skip_multiples_of_ten` | `true` | reproduce `line[-2] == "0"`: skip every attribute `% 10 == 0` |
| `water_in_set_order` | `true` | smooth inland water in CPython set order (else: `.mesh` triangle order) |

Turning either off produces a *better* mesh (the 97 triangles get their interpolated
altitude; the water sweep becomes order-independent of an implementation detail) but is no
longer byte-identical to Ortho4XP. **Blocage B2**: which one OrthoStudio XP ships by default is an
architecture decision, not a mesh-stage decision, because it decides whether the DSF oracle
can stay byte-exact. Shipped default: the faithful one.

`water_in_set_order = false` also opens the door to a vectorised sweep (Jacobi instead of
Gauss-Seidel) later; it is *not* implemented that way today — the loop is the same, only
the order changes — so the two options cost the same.

### 5.2 Cost

The water loop is the only Python-level loop left: `water_smoothing` sweeps over the water
triangles (107 881 on +43+005, so 1.08 M iterations). It runs on a plain Python list of
altitudes (`z.tolist()`), which avoids numpy scalar boxing, and writes back once.

## 6. Output: `Data<tile>.mesh` and `mesh.npz`

Built through `mesh_file.MeshData` and `mesh_file.write_mesh_text` (`docs/specs/mesh-file.md`):
`vertices[:, 0] = x + tile.lon`, `vertices[:, 1] = y + tile.lat`, `vertices[:, 2] = z`
(metres), `normals = (u, v)` as float32, `tris = corners - 1` (0-based int32),
`tri_attr = attr` (uint8), `extra = {"version": "2", "dimension": "3"}`. `mesh.npz` is the
npz twin of the same object, so stages 2.5 and 3 never parse the text file.

### 6.1 Normals: the answer to `mesh-file.md` section 8.3

`mesh-file.md` left the type of `MeshData.normals` to this chantier once OrthoStudio XP produces the
mesh itself. **Decision: keep float32, and keep `orthostudio.dsf.encode._normals_as_ortho4xp`.**

The normals in `mesh.npz` are no longer on the 2-decimal grid of the text file: they are the
doubles Triangle4XP computed from the DEM (`u = dx/|n|`, `v = dy/|n|`), rounded to float32.
The `.mesh` text writer prints `%.2f` of them, which is what Ortho4XP wrote — hence the byte
identity. So the npz and the text file **differ** on 909 347 of the 1 207 298 components,
and that is correct on both sides.

What matters is that a consumer sees the same numbers whichever it reads. Measured on
+43+005: `_normals_as_ortho4xp` (`round(float64(x), 2)`) applied to the npz values and to the
text values agrees on **all 1 207 298 components**, so the normals cost nothing. The
*elevations* do: the npz keeps the full doubles and the text file 15 significant digits of
`z/100000`, so 451 285 of 603 649 elevations differ by up to 5.0e-11 m and a DSF built from
the npz differs from Ortho4XP's on **23 bytes** spread over several pools (`pipeline-build.md`
8.8). A `mesh.npz` derived *from the text file* (rounded values) and the one `orthostudio.mesh`
writes (the sidecar's full precision) do not carry the same numbers, which is why the DSF reads
`Data<tile>.mesh`. Section 8.3's warning ("do not round a
natively computed normal") does not apply yet: these normals still come from Ortho4XP's own DEM
sampling inside the sidecar, and the byte-identity target *requires* the 2-decimal
quantisation. It will apply when OrthoStudio XP computes normals from its own geometry — blocage B4.

## 7. Output: `water_tris.npz` (contract with the masks stage)

`numpy.savez` (uncompressed), keys:

| Key | dtype / shape | Meaning |
|---|---|---|
| `format` | str | `"osxp-water-tris-1"` |
| `tile` | str | `"+43+005"` |
| `tri_index` | int32 `(K,)` | index of the triangle in `MeshData.tris` / in the `Triangles` block of the `.mesh` (0-based), **ascending** |
| `corners` | int32 `(K, 3)` | 0-based vertex indices into `MeshData.vertices` (a copy of `tris[tri_index]`) |
| `water_bits` | uint8 `(K,)` | `attr & 7`, i.e. `WATER 1 \| SEA 2 \| SEA_EQUIV 4` — Ortho4XP's `tri_type & has_water` with `has_water = 7` (`O4_Mask_Utils.py:441`) |
| `attr` | uint8 `(K,)` | the full attribute, for the record |
| `bary` | float64 `(K, 2)` | barycentre **(lon, lat)**, computed as Ortho4XP does: `(l1 + l2 + l3) / 3` in corner order |

`K` = the number of triangles with `attr != 0 and attr & 7 != 0` (198 798 on +43+005:
107 881 with bits 1, 81 925 with bits 2, 8 787 with bits 3, 108 with 1 — attribute 9 — and
97 with 2 — attribute 10).

**How the masks stage uses it** (`O4_Mask_Utils.py:466-620`, one artefact per tile, nine
read per mask build):

* corner coordinates: `mesh.vertices[wt["corners"]][:, :, :2]` from the `mesh.npz` sitting
  in the same artefact directory — they are not duplicated here (9.5 MB saved per tile);
* the mask cell of a triangle: `wgs84_to_orthogrid(bary_lat, bary_lon, mask_zl)` on `bary`,
  plus the spill into the neighbouring cells when the barycentre falls in the outer quarter
  at `mask_zl + 2` (`a = (til_x2 // 16) % 4`, `b = (til_y2 // 16) % 4`, spill when `a` or
  `b` is 0 or 3);
* `use_masks_for_inland = false` → keep `water_bits >= 2` for the sea masks and
  `water_bits == 1` for the "inland water near the shoreline" second pass;
  `use_masks_for_inland = true` → keep everything.

**Why the bucketing by mask cell is *not* done here** (this is a deliberate refusal of the
literal wording of the task): the cell of a triangle depends on `mask_zl`, which is a
parameter of the *masks* stage. A rule must declare the parameters it consumes and only
those (`docs/specs/graph-keys.md`); making `orthostudio.mesh` consume `mask_zl` would rebuild the
mesh — Triangle4XP included — every time the user changes the mask resolution, although the
mesh does not depend on it. `water_tris.npz` therefore carries exactly the *mesh-side*
half of Ortho4XP's `record_water_tris`, pre-filtered and pre-barycentred, and the masks stage
does the zoom-dependent half with two vectorised `wgs84_to_orthogrid` calls. Recorded as
**blocage B3** for the masks chantier, which only has to confirm this split.

## 8. Output: `stats.json`

```json
{"format": "osxp-mesh-stats-1", "tile": "+43+005", "n_input_nodes": 247282,
 "n_input_segments": 271320, "n_vertices": 603649, "n_triangles": 1197758,
 "n_water_tris": 198798, "weight_cells_changed": 41336, "weight_max": 4.0,
 "n_coast_nodes": 39545, "n_airports": 18, "steiner_budget": 1331665.3684210528,
 "triangle_argv": ["…"], "retried_without_min_angle": false,
 "timings_s": {"weights": …, "inputs": …, "triangle": …, "read_outputs": …,
               "postprocess": …, "mesh_text": …, "npz": …, "total": …},
 "warnings": [{"code": "MESH_WEIGHT_MAP_INCOMPLETE", "context": {…}}]}
```

`MESH_TRIANGLE_BUDGET_REACHED` (info) is recorded when the number of output triangles
reaches `0.99 * limit_tris * 1e6`: that is how the user learns the mesh is coarser than
`curvature_tol` asked for.

## 9. The rule `orthostudio.mesh@1` (`rule.py`)

* `kind = "dir"`, artefact = `Data<tile>.mesh`, `mesh.npz`, `water_tris.npz`, `stats.json`.
* inputs: `vectors` (required), `dem` (required), `coastline` (optional, may resolve absent).
* `cancel`: the rule takes the scheduler's token through `MeshJob` / `mesh_job(...)` (bound
  by `pipeline.build._mesh_run`). `build_mesh_native` polls the sidecar every 0.1 s instead of
  blocking in `subprocess.run`, kills it (SIGTERM then SIGKILL) and raises `SYS_CANCELLED`,
  and checks the token again between the sidecar and the post-processing. Without it a
  cancelled build waited up to `timeout_s = 1800` s or leaked the child (review 4, C4).
* `ram_mb = 900`: Triangle4XP peaks at ~430 MB (54 MB `.alt` + 4 MB `.weight` + the mesh
  structures) and the Python side holds the `(N, 6)` table (29 MB), the triangles (14 MB)
  and the text writer's chunks; measured `maxrss` in section 11.
* params, **exactly what the computation consumes**:

| Param | Default | Consumed by |
|---|---|---|
| `tile` | `""` | the artefact is tile-specific (same convention as the legacy rules) |
| `curvature_tol` | 2.0 | weight map (numerator) and `argv[11]` |
| `apt_curv_tol` | 0.5 | weight map |
| `apt_curv_ext` | 0.5 | weight map |
| `coast_curv_tol` | 1.0 | weight map |
| `coast_curv_ext` | 0.5 | weight map |
| `limit_tris` | 3.0 | `max_steiner` |
| `min_angle` | 10.0 | `-q` |
| `sea_smoothing_mode` | `"zero"` | post-processing |
| `water_smoothing` | 10 | post-processing |
| `iterate` | 0 | `-r` vs `-A`, input file names (TODO) |
| `skip_multiples_of_ten` | `true` | post-processing (section 5.1a) |
| `water_in_set_order` | `true` | post-processing (section 5.1b) |

`custom_dem` and `fill_nodata` are **not** consumed: the DEM enters through the digest of
the `dem` input. `mesh_zl` is not consumed either: the PSLG enters through the digest of
`vectors`, the coastline through the digest of `coastline`.

## 10. Acceptance tests

| # | Test | Assertion |
|---|---|---|
| 1 | `test_meshbuild_oracle.py::test_weight_map_is_byte_identical` | the weight map built from the tile's airports (the boxes of `Data+43+005.apt`, as `airports.json`) and the cached OSM coastline equals `fixtures/.../Data+43+005.weight` byte for byte (4 008 004 bytes) |
| 2 | `…::test_mesh_is_byte_identical` | `build_mesh_native` on `fixtures/.../Data+43+005.{node,poly,alt}` produces a `Data+43+005.mesh` equal to the fixture byte for byte (69 171 409 bytes) |
| 3 | `…::test_binary_and_text_exchange_give_the_same_mesh` | the same build with `-b` and without produces the same `.mesh` bytes (both byte-identical to the fixture) |
| 4 | `…::test_water_tris_match_the_mesh` | every row of `water_tris.npz` matches `read_mesh(.mesh)`: `attr & 7 != 0`, corners equal, barycentre equal to `vertices[corners].mean` at 1 ulp, and `K` equals the count computed from the `.mesh` alone |
| 5 | `test_meshbuild_weights.py` | synthetic: the difference-array painting equals a literal transcription of Ortho4XP's loop on 3 000 random nodes and 25 random boxes (3 seeds), clamping at the four borders, empty inputs, `apt_curv_tol == curvature_tol` and `apt_curv_tol <= 0` disable the airport term, a ratio below 1 survives the coastline maximum, the `airports.json` / `.apt` / `coastline.npz` readers and the restricted unpickler |
| 6 | `test_meshbuild_postprocess.py` | synthetic: classification table (all 256 attributes, both `skip_multiples_of_ten` values), `sea_smoothing_mode` in the three modes, water smoothing against a literal transcription, `water_tris.npz` round trip |
| 7 | `test_meshbuild_rule.py` | the rule declares exactly the 13 params above, `kind=dir`, inputs `(coastline, dem, vectors)`, and its key changes when any consumed param changes and not otherwise; `DemSpec` inference and `dem.json`; the restricted unpickler refuses a payload that is not a shapely/builtin graph |
| 8 | `test_meshbuild_rule.py::test_retry_relaxes_min_angle_and_not_nodata` | with a sidecar wrapper that fails once, the retry command carries `-pq0` and the *same* `nodata` as the first attempt, `MESH_QUALITY_RELAXED` is recorded, and the mesh is produced; a sidecar that always fails raises `MESH_TRIANGULATION_FAILED` with its exit code |
| 9 | `test_meshbuild_oracle.py::test_the_two_legacy_quirks_change_the_mesh` | turning a quirk off really moves the mesh: `skip_multiples_of_ten=False` -> 52 differing lines, `water_in_set_order=False` -> 97 622 |

## 11. Measurements (M4 Pro, `nice -n 10`, load average 4.4-5.0, other chantiers running)

Reference: Ortho4XP's `build_mesh` on +43+005 ZL14, warm caches, **7.431 s wall**
(`fixtures/large/oracle/+43+005_zl14_BI/runs/run1.json`, `self_cpu 4.709 s`,
`children_cpu 2.403 s`, `maxrss 644 MB` for the whole Ortho4XP process).

Measured by `tests/test_meshbuild_oracle.py` (`-s` prints them):

| Step | OrthoStudio XP, binary exchange | OrthoStudio XP, text exchange | Ortho4XP |
|---|---|---|---|
| read the PSLG (Ortho4XP text `.node` + `.poly`) and convert | 0.300 s | 0.312 s | (inside Triangle4XP) |
| weight map (39 545 coastline nodes, 18 airports) | **0.017 s** | 0.016 s | ~0.08 s of Python loops + an Overpass round trip on a cold tile |
| Triangle4XP | 1.141 s | 2.451 s | ~2.4 s |
| read `.1.node` + `.1.ele` | 0.005 s | 1.889 s | ~2.5 s (Python `float()` per token) |
| post-processing (classify + 10 water sweeps + sea + interp) | 0.345 s | 0.326 s | ~1.4 s |
| write `Data<tile>.mesh` (69 MB of text) | 0.942 s | 0.922 s | ~2.5 s |
| write `mesh.npz` + `water_tris.npz` | 0.014 s | 0.012 s | — |
| **total** | **2.79 s** | 5.95 s | **7.431 s** |

**2.7x** end to end. Two remarks on the honest number:

* the 0.30 s of "read the PSLG" is the *transition* cost of taking Ortho4XP's text `.node` /
  `.poly`; the native vector stage (P4) hands over binary tables and that line goes to
  ~0.005 s. Without it: 2.49 s, **3.0x**.
* the 0.94 s of `.mesh` text (34 % of the stage) is written for consumers that are not
  OrthoStudio XP: the Ortho4XP stage 2.5 fallback, the community mesh format, and the oracle
  comparisons. Every OrthoStudio XP stage reads `mesh.npz` instead. Dropping the text file when no
  legacy consumer is wired would put the stage at 1.85 s, **4.0x**; that is an integrator's
  decision, and the artefact contract keeps the file for now.

Memory: `maxrss` 417 MB for the Python process (the `(N, 6)` node table is 29 MB, the text
writer's chunks dominate) and 304 MB for the Triangle4XP child (54 MB `.alt` + 4 MB
`.weight` + the mesh structures). Declared `ram_mb = 900` leaves the margin the scheduler
needs when two tiles overlap.

## 12. Wanted differences from Ortho4XP, summary

1. The coastline is an **input**, never a download (section 2.3).
2. The retry relaxes `min_angle`, not `nodata` (section 4.1).
3. Binary exchange with the sidecar (already specified in `mesh-triangle-io.md`).
4. `str(int(...))` instead of `{:n}` for the DEM dimensions (locale bug).
5. The output `.poly` is never written, stdout is always quiet.
6. A short or missing `.alt` / `.weight` is an error (`MESH_INPUT_MISSING`) instead of
   meshing on uninitialised memory.
7. The post-processed node table is only written back when `iterate > 0` (Ortho4XP always
   rewrites it, and nothing reads it otherwise).
8. Everything vectorised except the order-dependent water sweep.
9. `skip_multiples_of_ten` / `water_in_set_order` exist at all: Ortho4XP has no choice (section 5.1).
10. **An airport box that misses the tile entirely is dropped.** Ortho4XP clamps `rowmin` at 0 and
    `rowmax` at 1000 but then slices `weight[rowmin:rowmax + 1]`: when the box lies wholly
    north of the tile, `rowmax + 1` is *negative*, and a negative slice bound in Python counts
    from the end, so Ortho4XP refines rows 0 to `1000 + rowmax` — most of the tile — at airport
    resolution. Same on the west side for the columns. OrthoStudio XP paints nothing for such a box.
    No airport of the reference tile triggers it (stage 1 only collects airports whose
    boundary meets the tile), so the byte-identity is unaffected; tested explicitly in
    `test_meshbuild_weights.py::test_a_box_entirely_outside_the_tile_is_dropped_unlike_ortho4xp`.

## 13. Blocages

* **B1 — `dem.json`.** The DEM stage must publish `nxdem, nydem, x0, y0, x1, y1, nodata`
  next to `Data<tile>.alt`; they are not derivable from the raster in general (ALOS uses
  3672 samples and a half-pixel offset, a user GeoTIFF anything). Until then the reader
  infers the View/SRTM layout from the file size. Exact and tested on the reference tile,
  wrong for ALOS or a custom DEM.
* **B2 — `skip_multiples_of_ten` / `water_in_set_order` defaults** (section 5.1): project-wide
  decision, byte-identity versus correctness.
* **B3 — `water_tris.npz` stops at the mesh-side half** (section 7): the masks stage does
  the `mask_zl`-dependent bucketing. *Agreed with the masks chantier*: `orthostudio.masks.water`
  reads `format` / `corners` / `water_bits` / `bary` from this file and does the
  `wgs84_to_orthogrid` half itself.
* **B5 — the Triangle4XP binary is not part of the key.** The sidecar produces the vertices,
  and its path comes from `$OSXP_TRIANGLE4XP` / `PATH` / `native/triangle4xp/build`: rebuilding
  it (another patch, another compiler, other options) leaves every stored mesh valid.
  Closing it means either a fourth input on the rule (`triangle`, a source edge on the binary,
  content digest — my preference, it is what `_coastline_node` already does for the Ortho4XP
  `.osm.bz2`) or a `triangle_build` digest parameter; both change the rule's signature and
  invalidate every stored mesh, which is a decision for this chantier plus the integrator, so
  the fix pass only wrote it down. **Known limit of the cache today**: after rebuilding the
  sidecar, drop the `orthostudio.mesh` artefacts (`osxp gc`) or change a parameter.
* **B4 — normals stay quantised to 1/100** (section 6.1). The DSF encoder rounds them to two
  decimals because Ortho4XP's `.mesh` only carried two, and that is what keeps the DSF
  byte-identical. OrthoStudio XP now has the full-precision normals in `mesh.npz`: feeding them to
  the encoder unrounded would give smoother shading on shallow slopes and break the DSF oracle. Same
  family of decision as B2, and it belongs to the DSF chantier plus a project-wide "byte-identity or
  quality" call.
