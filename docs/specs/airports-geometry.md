# Airport geometry: runways, surfaces and the elevation smoothed over them

Status: P4 wave 2, chantier `aptgeom` (`src/orthostudio/airports_vec/runways.py`, `areas.py`,
`smoothing.py`; tests `tests/test_aptgeom_*.py`).
Origin: Ortho4XP `src/O4_Airport_Utils.py` — `sort_and_reconstruct_runways` (329-682),
`discard_unwanted_airports` (683-698), `build_hangar_areas` (700-734), `build_apron_areas`
(734-775), `build_taxiway_areas` (775-805), `update_airport_boundaries` (806-829),
`build_airport_array` (910-921), `smooth_raster_over_airports` (924-1038) — plus the helpers
of `src/O4_Vector_Utils.py` it calls (`min_bounding_rectangle` 1292-1316,
`buffer_simple_way` 1101-1110, `weighted_normals` 1071-1094, `length_in_meters` 1002-1011,
`improved_buffer` 1021-1062, `ensure_MultiPolygon` 779-791) and `O4_Geo_Utils.dist` (19-28).

> **Integration (P4 wave 2).** The questions this spec leaves to the integrator are
> answered in `docs/specs/airports-integration.md` section 6, and the end-to-end numbers
> are in `docs/benchmarks/p4-airports.md`.

Companion specs: `vectors-pslg.md` (the graph these surfaces end up in), `vectors-assembly.md`
section 2.3 (where the airport layers are inserted), `dem.md` sections 9 and 9.2 (the raster
this stage smooths, and why the DEM rule could not do it itself), `airports-index.md`
(`apt.dat`, a different subject — arbitration B5).

Acceptance, PLAN level A on the elevation and level A on the geometry:

| artefact | target | measured 2026-09-12 |
|---|---|---|
| `Data+43+005.alt` | **byte-identical** to the Ortho4XP build | **byte-identical**, 53 963 716 / 53 963 716 bytes, 0 differing float32 |
| runway / taxiway / apron / hangar / boundary geometry | identical to `Data+43+005.apt` | **`equals_exact(1e-12)` on all 18 airports**, 0 symmetric difference |
| `.poly` edges marked RUNWAY, TAXIWAY, HANGAR | consistent with the geometry above | max distance of an edge mid-point to the matching outline **6.1e-8** (RUNWAY), **6.2e-8** (TAXIWAY), **4.7e-10** (HANGAR) |

## 1. What this chantier does, and what it does not

```
  discover.discover(store, tile)                     -> AirportSet          (aptdata)
      |
      +--> runways.build_runways(airports, store, tile)                     THIS CHANTIER
      |        fills areas.runway, runway_as_area, runway_as_line
      +--> discover.discard_unwanted(airports, tile)                        (aptdata)
      |        reads areas.runway
      +--> areas.build_areas(airports, store, tile)                         THIS CHANTIER
      |        fills areas.hangar, areas.apron, areas.taxiway
      +--> discover.update_boundaries(airports, tile)                       (aptdata)
      |        unions the four areas into Airport.boundary, tile-local
      +--> smoothing.smooth_dem_over_airports(dem, airports)  -> Dem        THIS CHANTIER
               Dem.write_alt  ->  Data<tile>.alt
      +--> encode.encode_airports(...)                                      (aptlayers)
```

The three functions of this chantier run *inside* that chain and write into the shared record: they
fill :class:`orthostudio.airports_vec.model.SurfaceAreas` in place, exactly where Ortho4XP
overwrites ``apt["runway"]``, ``apt["hangar"]``, ``apt["apron"]`` and ``apt["taxiway"]``, and return
a *report* (counts and coded events), not the geometry.

**In scope**: turning the OSM ways already attached to an airport into the four surface
families, the airport footprint they define, and the elevation raster smoothed over that
footprint.

**Out of scope, by arbitration**:

* finding the airports and attaching the ways to them (`discover_airport_names` 19-172,
  `attach_surfaces_to_airports` 174-327) — chantier `aptdata`, section 2;
* encoding the surfaces as PSLG layers with their altitudes, traverses and seeds
  (`encode_runways_taxiways_and_aprons` 1038-1339, `encode_hangars` 1341-1373,
  `flatten_helipads` 1375-1462) — chantier `aptlayers`;
* writing `Data<tile>.apt` and the clean artefact beside it (arbitration B3) — the rule;
* the high-resolution imagery cover of airports (arbitration B4) — the DSF node;
* `apt.dat` (arbitration B5): nothing here reads it. The geometry starts at the OSM
  `aeroway` layer and only there.

## 2. The record this chantier fills (`aptdata`'s `model.py`)

Neither module builds the airport dictionary; both read and complete one.
:mod:`orthostudio.airports_vec.model`, delivered by the `aptdata` chantier, is the
shared vocabulary:

```
Airport.ways: dict[Category, list[int]]   # "runway" | "taxiway" | "apron" | "hangar"
Airport.runway_rels: list[int]            # Ortho4XP's runway_as_rel
Airport.boundary: BaseGeometry | None     # the OSM outline, in DEGREES, until update_boundaries
Airport.smoothing_pix: int | None
Airport.areas: SurfaceAreas               # runway, runway_as_area, runway_as_line,
                                          # taxiway, apron, hangar -- None until built
AirportSet                                # an ordered mapping, iterated in Ortho4XP's order
```

Three properties of that record are contractual, not incidental:

* **the order of `AirportSet` is Ortho4XP's `dico_airports` order.** Two airports whose smoothing
  windows overlap are smoothed one after the other *in place* (section 5.4), so a different
  order gives a different raster;
* **`boundary` is in degrees** until `discover.update_boundaries` moves it to tile-local
  coordinates. Ortho4XP keeps it in degrees from `discover_airport_names:121-152` until
  `update_airport_boundaries:812` translates it. This chantier therefore never reads
  `Airport.boundary` before `update_boundaries` has run -- only `smoothing.py` reads it, and
  it runs after;
* **the surface lists are OSM way ids of `store.ways`**, in the order Ortho4XP appended them,
  because that order decides the order of the merged runway parts (section 3.3) and the order
  of the polygons a union is built from.

`orthostudio.vectors.osmdata.OsmData` is already id-for-id identical to Ortho4XP's `OSM_layer`
(`vectors-osm-layers.md`), which is what makes this reachable: `store.nodes`, `store.ways`,
`store.relations[id]["outer"]` and `store.tags["w" | "r"]` are Ortho4XP's `dicosmn`, `dicosmw`,
`dicosmr`, `dicosmtags`. `AirportSet` does not carry the store, so the two builders take it as
their second argument.

**Every area is `None` until its builder has run, and empty (not `None`) after.** That is how
`discover.discard_unwanted` tells "this airport has no runway" from "the runway module has not
run" (`_required_area`), so `build_runways` assigns an empty `MultiPolygon` rather than
skipping an airport with nothing attached.

## 3. Runways (`runways.py`)

`sort_and_reconstruct_runways` is the subtlest function of the airport stage, because OSM
encodes a runway in two incompatible ways and sometimes in both at once. The rule, stated
once:

> **A runway is a rectangle.** OSM gives it either as a closed way around its outline (an
> *area* runway) or as a centre line, possibly cut into several parts (a *linear* runway).
> An area runway is accepted only if it really is close to a rectangle, and its axis and
> width are read off its minimum bounding rectangle. A linear runway is first re-assembled
> from its parts, then grown into a rectangle around its centre line. When the two encodings
> describe the same runway, the area one wins and only borrows the axis and width of the
> linear one.

### 3.1 Area runways (`:352-427` for ways, `:449-534` for relations)

The way is an area runway when its first and last node are the same
(`dicosmw[wayid][0] == dicosmw[wayid][-1]`); a relation tagged `aeroway=runway` always is,
through its first outer ring. In both cases:

1. the node coordinates become tile-local and are **rounded to 7 decimals**
   (`numpy.round(coords - [lon, lat], 7)`), the same rounding the OSM layers get
   (`vectors-pslg.md` 2.8);
2. an empty or invalid polygon is rejected (`OSM_RUNWAY_REJECTED`, reason
   `invalid geometry`);
3. `area < 1e-7` (square degrees, about 1 200 m² at this latitude) is dropped silently:
   Ortho4XP's comment says "probably for aeromodelism only";
4. unless the element carries a `custom` tag, the **Hausdorff distance** between the polygon
   and its minimum bounding rectangle must be `<= 0.0008` degrees (about 89 m). Beyond that
   the shape is not a runway and is rejected (`OSM_RUNWAY_REJECTED`, reason
   `not a rectangle`). This is the escape hatch a user gets by tagging `custom=yes` in JOSM;
5. the axis comes from the **minimum bounding rectangle** (`min_bounding_rectangle:1292-1316`:
   rotating-calipers over the convex hull in the `scalx`-scaled frame, the smallest-area
   rectangle wins, ties go to the first edge tested). Of the rectangle's four corners
   `r0 r1 r2 r3`, if `|r0 r1| < |r1 r2|` then `start = (r0+r1)/2`, `end = (r2+r3)/2`,
   `width = |r0 r1|`, else `start = (r1+r2)/2`, `end = (r0+r3)/2`, `width = |r1 r2|`:
   the short side is the width and the axis joins the mid-points of the two short sides.
   Lengths are metric in the anisotropic frame (`length_in_meters`).

**Keep**, statement for statement. One **wanted difference**: Ortho4XP computes the minimum
bounding rectangle twice per runway (`:371` for the Hausdorff test, `:399` for the corners);
OrthoStudio XP computes it once. The function is deterministic, and the fixture agrees to `1e-12`.

### 3.2 Linear runways: the width (`:426-437`, `:628-634`)

A way that is not closed goes into the `linear` list with the `width` tag as a float, or `0`
when the tag is missing or unparsable. After the parts are merged:

* a known width becomes `width + 10` metres (5 m of shoulder on each side);
* an unknown width becomes `30 + runway_length // 1000` metres, the length being the
  **great-circle distance** between the first and last node in degrees
  (`O4_Geo_Utils.dist`), not the metric of the local frame. `//` is a float floor division,
  so the width grows by one metre per kilometre of runway, in steps.

**Keep**, including the floor division and the great-circle formula, because the width enters
the polygon and therefore the mesh.

### 3.3 Linear runways: merging the parts (`:536-625`)

OSM splits a runway at displaced thresholds, surface changes and stopways. Ortho4XP re-assembles
them with a loop that restarts from scratch after every merge:

```
repeat until no merge happened:
  for i in 0 .. len(linear)-2:
    dir_i = arctan2(dlon_i, dlat_i)              # note the argument order, section 3.6
    for j in i+1 .. len(linear)-1:
      dir_j = ...
      if min(|{-2pi, -pi, 0, pi, 2pi} - (dir_i - dir_j)|) >= 0.2: continue
      if the two parts share an end node (four cases, four ways to concatenate):
          replace them by the concatenation, appended at the END of the list;
          the merged width is max(width_i, width_j);
          restart the whole scan
```

Three details are load-bearing and are kept:

* **the direction test comes first.** Two different runways often share an end node in OSM
  (a threshold on a taxiway crossing); parts whose headings differ by more than 0.2 rad
  (11.5 degrees) modulo pi are not the same runway. The set `{-2pi, -pi, 0, pi, 2pi}` makes
  the test insensitive to the direction the way is drawn in;
* **the merged part is appended at the end** of `linear`, not left in place: the order of
  `runways_as_line` in the output follows that, and so does the order of the polygons in the
  union;
* **the scan restarts at `i = 0`** after each merge (the two nested `break`s), so a runway in
  five parts is assembled in four passes over the list, always joining the earliest pair.

The four concatenations, with `a = linear[i]`, `b = linear[j]`:
`a[-1] == b[0]` gives `a + b[1:]`; `a[-1] == b[-1]` gives `a + b[-2::-1]`;
`a[0] == b[0]` gives `a[::-1] + b[1:]`; `a[0] == b[-1]` gives `b + a[1:]`.

### 3.4 Linear runways: the rectangle (`:626-664`)

The axis is `[first node, last node]` of the merged part (**not** the polyline: a runway is
straight), rounded to 7 decimals in tile-local coordinates; the polygon is
`buffer_simple_way(axis, width)` (`:1101-1110`), which offsets the axis by `width/2` on each
side along the *weighted normals* of the way (`weighted_normals:1071-1094`, already ported and
proven in `orthostudio.vectors.roads`) and closes the ring by repeating the first offset point.

### 3.5 Duplicates: when both encodings describe one runway (`:645-663`)

For each linear runway, in list order, against each area runway, in list order:

```
if area(intersection) > 0.6 * min(area(linear), area(area_runway)):
    the area runway keeps its polygon but takes the START, END and WIDTH of the linear one
    the linear runway is dropped
```

**Keep**, including the asymmetry (the *area* polygon survives because it is the true
outline; the *linear* axis survives because a centre line is a better axis than the axis of
a minimum bounding rectangle) and the first-match-wins break.

### 3.6 Two oddities kept for parity

* `numpy.arctan2(*(node_end - node_start))` unpacks a `(lon, lat)` difference into
  `arctan2(y=dlon, x=dlat)`: the angle is measured from the north, and the `scalx` anisotropy
  is ignored. It is a *comparison* between two headings computed the same way, so the
  convention cancels; the 0.2 rad threshold is slightly anisotropic and that is Ortho4XP's
  threshold. **Keep**;
* `min_bounding_rectangle` initialises `min_area = 9999` (square degrees). A polygon whose
  bounding rectangle is larger than that would raise; no runway on earth is. **Keep**, with
  an explicit `RUNWAY_MAX_RECT_AREA` constant and an `OsxpError` instead of an
  `UnboundLocalError`.

### 3.7 Discarding an airport (`discard_unwanted_airports:683-698`)

Not this chantier's code -- `discover.discard_unwanted` owns it -- but it is this chantier's
*contract*: it runs **after** `build_runways` and **before** `build_areas`, and it reads
`areas.runway`. An airport with an outline is judged on the outline (5 000 m²), one without on
its runway area (2 500 m²). On the reference tile 37 airports enter and **18** survive, and
`build_areas` therefore builds surfaces for 18, not 37 -- which is what makes the apron and
taxiway way lists match Ortho4XP's.

## 4. Surfaces (`areas.py`)

All four builders turn ways into tile-local polygons the same way: coordinates minus
`(lon, lat)`, **rounded to 7 decimals**, one `shapely.Polygon` per way.

| surface | origin | construction | invalid way |
|---|---|---|---|
| hangar | `:700-734` | `ensure_MultiPolygon(improved_buffer(union, 2, 1, 0.5))` | skipped silently |
| apron | `:734-775` | `ensure_MultiPolygon(union)`, **no buffer** | skipped, `OSM_AIRPORT_SURFACE_INVALID` |
| taxiway | `:775-805` | lines, then `ensure_MultiPolygon(improved_buffer(lines, 15, 3, 0.5))` | — a taxiway is a line, it is never invalid |

`improved_buffer(g, w, s, l)` (`:1021-1062`) grows by `w + s`, shrinks by `s` and simplifies
by `l`, all in metres, in the `scalx`-scaled frame, with `join_style=2`, `mitre_limit=1.5`
and `quad_segs=1`: the angular outline it produces *is* the shape of the reference mesh, not
an artefact to smooth away. It is already ported in `orthostudio.vectors.roads.improved_buffer` and
is called, not re-written.

Three consequences of Ortho4XP's exact spelling, kept:

* a hangar is buffered by 2 m and a taxiway by 15 m from its centre line — a taxiway is
  therefore 30 m wide whatever OSM says, `width` tags are not read here;
* **aprons are not buffered at all**, so two aprons sharing an edge stay two polygons;
* the apron and taxiway way id lists stay in `Airport.ways`, which is exactly the second
  element of Ortho4XP's `(area, wayid_list)` pairs: the encoder needs the individual ways again,
  since taxiway altitudes are fitted per way and an apron is only encoded when its own way
  carries an `include` tag (`:1288-1296`).

### 4.1 The airport footprint (`update_airport_boundaries:806-829`)

`discover.update_boundaries` owns it; it is listed here because it is what this chantier's
three areas are *for*, and because the elevation smoothing reads its result:

```
boundary = union(taxiway_area, apron_area, hangar_area, runway_area)      # tile-local
if the airport had an OSM outline:
    boundary = union(translate(outline, -lon, -lat), boundary)
boundary = ensure_MultiPolygon(boundary.buffer(0).simplify(0.00001))
```

The union order is Ortho4XP's and is kept: a floating-point union is not associative, and this
boundary is the input of a byte-identity target. It is also the moment the outline stops being
in degrees (section 2). A surface still `None` at that point raises rather than being read as
empty, which is why `build_areas` must run on the whole surviving set.

## 5. Smoothing the elevation over airports (`smoothing.py`)

`smooth_raster_over_airports:924-1038` is the only writer of `Data<tile>.alt` on Ortho4XP's normal
path (`dem.md` 9.2). It is the reason a runway is flat in X-Plane: the mesh under an airport
takes its altitude from a raster that has been blurred over the airport footprint, so a
30 m-resolution DEM step in the middle of a runway disappears.

The pure half — the mask-weighted separable blur and the re-blending of the raster border —
is **already ported and proven bit for bit** as `orthostudio.dem.raster.smoothen` and
`orthostudio.dem.raster.smooth_over_regions` (`dem.md` 9.2). This module contributes the other half:
*which* regions, in *what order*, with *what margins and parameters*.

### 5.1 `max_pix`: the width of the border re-blend (`:925-934`)

`max_pix = apt_smoothing_pix`, raised to the largest `smoothing_pix` any airport carries.
`max_pix == 0` means "no smoothing at all": the raster is written unchanged. **Keep**,
including the fact that `max_pix` is computed over **every** airport, even one whose own
window is later skipped.

### 5.2 `upscale`: rasterising the mask above the DEM resolution (`:940-942`)

```
upscale = max(ceil(ystep * lat_to_m / 10), 1)
```

The airport mask is drawn `upscale` times finer than the DEM and then resized down with a
**bicubic** filter, so that the edge of a runway is anti-aliased instead of stair-stepped —
Ortho4XP's comment says "target 10 m of pixel size at most to avoid aliasing". On the reference
tile `ystep` is 1.02/3673 degrees, i.e. 30.9 m, so `upscale = 4` and the mask is drawn at
7.7 m. **Keep**: the resize filter decides the grey values of the mask, which decide the
blend, which decides the bytes.

### 5.3 The window of one airport (`:944-954`)

```
pix    = int(airport.smoothing_pix) if it has one else apt_smoothing_pix   (0 -> skip)
colmin = max(floor((xmin - x0) / xstep) - pix, 0)
colmax = min(ceil ((xmax - x0) / xstep) + pix, nxdem - 1)
rowmin = max(floor((y1 - ymax) / ystep) - pix, 0)
rowmax = min(ceil ((y1 - ymin) / ystep) + pix, nydem - 1)
skip when colmin >= colmax or rowmin >= rowmax
```

`(xmin, ymin, xmax, ymax)` are the bounds of the **boundary** alone — the margin `pix` is
what lets the blur reach past it, and the surfaces are all inside it by construction
(section 4.1). The row axis runs north to south, hence `y1 - ymax` for the top row.

### 5.4 The mask of one airport (`:955-1000`)

The mask is drawn from `union(boundary, runway_area, hangar_area, taxiway_area, apron_area)`
— the boundary again, and the four surfaces again, in **that** order. Redundant after
section 4.1, and kept: `unary_union` of the same set in another order can differ in the last
bits, and this is a byte-identity target.

Each exterior ring is filled white (255) and each interior ring black (0), in ring order, at
the upscaled resolution, with `round()` on the pixel coordinates and Pillow's even-odd
polygon fill; then the image is resized to the window with `Image.BICUBIC`. A hole inside a
hole would be lost (black over black); no airport has one.

**Order matters twice**: the polygons are filled in the order `ensure_MultiPolygon` returns
them, and the regions are smoothed in the airport order of section 2 **on the running
raster** — two airports whose windows overlap see each other's blur.

### 5.5 The border re-blend (`:1002-1033`)

After every region, the first and last `max_pix` rows and columns are linearly faded back to
their original values, so that the smoothing never moves a value on the tile border and
neighbouring tiles still glue. Implemented once in
`orthostudio.dem.raster.smooth_over_regions(..., preserve_boundary=True)`, called here.

### 5.6 Contract

```python
smooth_dem_over_airports(dem: Dem, airports: Sequence[Footprint],
                         params: SmoothingParams = ...) -> Dem
```

returns a **new** `Dem` sharing every field but `alt_dem` (Ortho4XP mutates `tile.dem.alt_dem` in
place; a cached artefact must not be mutated under its digest). `Footprint` is structural:
anything with `boundary`, `surfaces` and `smoothing_pix`, which is what `areas.AirportAreas`
is. The caller writes the result with `Dem.write_alt`, which is what produces
`Data<tile>.alt`.

## 6. Wanted differences

1. The minimum bounding rectangle is computed once per runway instead of twice (section 3.1).
2. `min_bounding_rectangle` raises `SYS_INTERNAL_ERROR` instead of `UnboundLocalError` when a
   polygon is degenerate or larger than `RUNWAY_MAX_RECT_AREA` (section 3.6).
3. Rejections are `OsxpError` values handed to an `on_event` callback
   (`OSM_RUNWAY_REJECTED`, `OSM_AIRPORT_SURFACE_INVALID`) instead of `UI.vprint` lines, and
   they are counted in the returned report (`errors.md`).
4. `smooth_dem_over_airports` returns a new `Dem` instead of mutating one and writing the file
   itself (section 5.6). It never writes `Data<tile>.alt`; the rule does.
5. `build_apron_areas` catches a `Polygon()` constructor failure *and* an invalid polygon with
   two different messages for the same outcome (`:751-775`); OrthoStudio XP reports one code with a
   `reason`.
6. An area that has not been built is `None`, not an empty geometry (section 2). Ortho4XP cannot
   tell the two apart and `discard_unwanted_airports` raises `AttributeError` when the runway
   module has not run.
7. A tile with **no airport at all** still runs the border re-blend, because `max_pix` is 8 and
   not 0 (section 5.1). That is Ortho4XP's behaviour, and it is not a no-op in float32: on a
   200x200 raster of random metres, 102 of 40 000 samples move by at most 4e-6 m. Measured
   rather than hidden behind an early return.
8. **`max_pix` is clamped to the raster**: `min(max_pix, nydem, nxdem)` (`smoothing.py`,
   `smooth_dem_over_airports`). Ortho4XP's border re-blend (`:1017-1033`) indexes `max_pix` rows
   from each side and raises `IndexError` on a raster narrower than that; OrthoStudio XP returns a
   narrower blend instead. Unreachable on a real raster with the tile setting (3 673 samples,
   `apt_smoothing_pix <= 1000`); it was reachable through an OSM tag, hence difference 9.
   Declared by review 6, pinned by `tests/test_review6_fidelite_chaine.py`.
9. **An OSM `smoothing_pix` tag outside `0..MAX_SMOOTHING_PIX` (1000) is refused, not
   clamped**: `discover._smoothing_pix` ignores it -- the airport takes the tile's
   `apt_smoothing_pix`, as for an unparsable tag -- and reports
   `OSM_AIRPORT_SMOOTHING_INVALID` (degraded); `smoothing._tag_pix` applies the same bound to a
   footprint built by other means. Ortho4XP takes the integer as is (`:105-113`): a negative
   width empties the triangular kernel and `numpy.convolve` kills step 1, a huge one
   materialises a kernel of that size (review 6 killed a 30 000 000 run after five minutes at
   1.15 GB). Refused rather than clamped because a tag of 5000 is a typo, not a request for
   1000; the bound is the one `VectorsParams` already applies to `apt_smoothing_pix`. No effect
   on `+43+005` (no tag) or on any value in range (`tests/test_review6_robustesse_cas_limites.py`).

## 7. Acceptance

| # | test | proves |
|---|---|---|
| A1 | `test_aptgeom_oracle.py::test_alt_is_byte_identical_to_ortho4xp` | `Data+43+005.alt`, rebuilt from the native DEM through the whole airport chain, is **byte-identical** to the Ortho4XP build |
| A2 | `test_aptgeom_oracle.py::test_alt_is_byte_identical_from_the_reference_airports` | the same, with the footprints read from `Data+43+005.apt` -- separates a smoothing defect from a geometry defect |
| A3 | `test_aptgeom_oracle.py::test_runways_match_the_reference_pickle` | per airport: same runway area, same `as_area` / `as_line` lists, same polygons at `1e-12`, same start, end and width |
| A4 | `test_aptgeom_oracle.py::test_surfaces_match_the_reference_pickle` | hangars, aprons, taxiways and the updated boundary, all at `1e-12` and with a zero symmetric difference |
| A5 | `test_aptgeom_oracle.py::test_poly_markers_lie_on_the_built_surfaces` | every RUNWAY, TAXIWAY and HANGAR edge of the reference `.poly` has its mid-point on the matching outline |
| A6 | `test_aptgeom_oracle.py::test_the_airport_set_matches_the_reference` | the chain produces Ortho4XP's 18 airports, in order, with the same way lists |
| A7 | `test_aptgeom_oracle.py::test_the_native_raster_differs_only_by_the_smoothing` | the premise of A1: before smoothing the two rasters differ on exactly 21 923 samples, by at most 13.57 m |
| A8 | `test_aptgeom_oracle.py::test_written_alt_file_is_byte_identical` | the artefact, not only the array: `Dem.write_alt` of the smoothed raster |
| A9 | `test_aptgeom_geometry.py` (23 tests, no oracle) | the rectangle rule, the `custom` bypass, the aeromodelism drop, the invalid ring, the relation branch, the two width rules, the four merge cases, the append-at-the-end order, the merged width, the duplicate rule, the buffers, the invalid apron, the empty airport |
| A10 | `test_aptgeom_smoothing.py` (20 tests, no oracle) | `max_pix`, `upscale`, the window arithmetic, the clamping, the three skip rules, the per-airport override, the mask and its holes, the untouched input, the border, `apt_smoothing_pix = 0`, the overlapping-airport order |

### 7.1 Measured, 2026-09-12, +43+005 at ZL14

| quantity | OrthoStudio XP | Ortho4XP |
|---|---|---|
| airports discovered / kept | 37 / **18** | 37 / 18 |
| runways reconstructed | **42** (7 as area, 35 as line), 4 merges of linear parts | 42 (7 / 35) |
| runways rejected | **0** (0 invalid, 0 beyond the Hausdorff limit, 0 below `min_area`) | 0 |
| surfaces built | 196 hangar ways, 49 apron ways, 279 taxiway ways, **0 skipped** | same |
| runway / apron / taxiway / hangar / boundary geometry | `equals_exact(1e-12)` on **all 18 airports**, symmetric difference **0.0** | reference |
| `Data+43+005.alt` | **byte-identical**, 53 963 716 of 53 963 716 bytes, **0** differing float32 | reference |
| `.poly` RUNWAY edges (5 952) | max distance of an edge mid-point to a runway ring **6.07e-8** deg | reference |
| `.poly` TAXIWAY edges (4 289) | max distance to the cleaned taxiway outline **6.20e-8** deg | reference |
| `.poly` HANGAR edges (735) | max distance to the hangar outline **4.73e-10** deg | reference |

The two 6e-8 residuals are the 7-decimal rounding the encoder applies to the points it
interpolates *along these very outlines* (`:1180-1196`): half an ulp of 1e-7 on each axis is
7.07e-8, so the measured maximum is at the rounding bound and there is nothing left under it.

Cost (M4 Pro, `nice -n 10`, load average 3.4, median of 7 runs): `build_runways` **3.8 ms**,
`build_areas` **23.9 ms**, `smooth_dem_over_airports` **20.2 ms**, the whole chain from the
loaded OSM store to the smoothed raster **68 ms**. Ortho4XP does not time its airport stage
separately (`legacy_timing.json` holds `build_poly_file` as a whole, 33.7 s), so no ratio is
claimed here; the comparison belongs to the integrator's end-to-end measurement.

## 8. What the proof does not cover

* **APRON edges**: the reference tile has no apron tagged `include`, so the `.poly` carries 0
  APRON edge and A5 cannot exercise that marker. The apron *geometry* is proven by A4.
* **`smoothing_pix`**: no airport of +43+005 carries the tag, so `max_pix` is
  `apt_smoothing_pix` (8) everywhere and the per-airport override is only covered by A10.
* **`custom` runways**: no runway of +43+005 carries the tag; the bypass is covered by A9.
* **relation runways**: 3 relations exist in the layer and 0 are attached as runways on this
  tile; the relation branch is covered by A9 only.
* **rejections**: 0 runways were rejected on the reference tile, so the two rejection paths and
  the aeromodelism drop are unit-tested only.
* **a raster other than 3"**: `upscale` is 4 on every tile whose DEM is 3 arc-seconds;
  `upscale = 1` and `upscale = 10` are covered by A10 only.
* a second oracle tile would close the first five; it is the same hole
  `vectors-assembly.md` 8.2 lists for wave 1.
