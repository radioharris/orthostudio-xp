# Airports in the planar graph: runways, taxiways, aprons, hangars, helipads

Status: P4 wave 2, chantier `aptencode` (`src/orthostudio/airports_vec/encode.py`,
`src/orthostudio/airports_vec/helipads.py`).
Origin: Ortho4XP `src/O4_Airport_Utils.py` — `encode_runways_taxiways_and_aprons`
(`:1038-1344`), `encode_hangars` (`:1344-1370`), `flatten_helipads` (`:1370-1464`),
`build_airport_array` (`:910-921`) — and the helpers of `src/O4_Vector_Utils.py` they call:
`weighted_alt:1250-1280`, `least_square_fit_altitude_along_way:1182-1212`,
`refine_way:1114-1141`, `shift_way:1096-1098`, `length_in_meters:1002-1011`,
`improved_buffer:1021-1069`, `cut_to_tile:739-777`, `ensure_MultiPolygon:779-793`.
Call site: `O4_Vector_Map.include_airports:180-222`.

> **Integration (P4 wave 2).** The questions this spec leaves to the integrator are
> answered in `docs/specs/airports-integration.md` section 6, and the end-to-end numbers
> are in `docs/benchmarks/p4-airports.md`.

Acceptance: PLAN level B against the reference tile `+43+005`, in two steps —
**way by way** against the 2 592 recorded `insert_way` calls of the airport block of
`fixtures/large/oracle/+43+005_zl14_BI/noding/layers+43+005.npz` (same count, same order, same
marker, coordinates to 1e-9, z to 1e-6 m), then **end to end** by assembling those layers with
Ortho4XP's own road / coastline / water passes and comparing the result with
`build/Data+43+005.{node,poly}`. Measured in section 10.

This spec covers the *encoding* only. Where the airport geometry comes from (`AirportSet`:
runway reconstruction, hangar / apron / taxiway areas, boundaries) is the `aptdata` /
`aptgeom` chantiers' business, and the elevation smoothing over airports
(`smooth_raster_over_airports`, `:923-1034`) is the DEM half of wave 2: this module **reads**
the already smoothed raster, it does not produce it (arbitration B2).

## 1. What the stage does

`include_airports` is the first thing step 1 of Ortho4XP does, and it produces the
highest-priority layers of the PSLG (`docs/specs/vectors-pslg.md` 2.6). In insertion order:

| pass | marker | geometry | z | seeds |
|---|---|---|---|---|
| runway outline, per runway polygon | `RUNWAY` (16) | the polygon boundary, re-sampled at every traverse abscissa | the airport altitude field (section 4) | one per connected sub-region of the runway union (section 6.1) |
| runway traverses, per runway polygon | `DUMMY` (0) | one chord per refinement step, clipped to the runway | idem | none |
| taxiways, per airport | `TAXIWAY` (32) | the cleaned taxiway area, exterior then holes, refined at 20 m | idem | one per polygon |
| aprons, per tagged way | `APRON` (64) | the OSM way refined at 15 m | idem | one per polygon |
| hangars, all airports | `HANGAR` (128) | the hangar polygons cut to the tile | **flat**, the mean of the raster over the outline | one per polygon |
| helipads | `INTERP_ALT` (8) | hexagons and OSM helipad polygons not already covered | **flat**, the mean of the raster over the outline | one per polygon |

Everything is in tile-local coordinates (`x = lon - tile.lon`, `y = lat - tile.lat`), the
anisotropic frame of `vectors-pslg.md` 2.7 with `scalx = cos((lat + 0.5) * pi / 180)`.

The stage also publishes the two *footprints* only it can build: `treated_area` (what the
roads are subtracted from and what a helipad must not touch) and one bounding box per airport
(`airports.json`, the curvature weight map of the mesh stage, `docs/specs/mesh-build.md` 3.2).
It carries a third, the 1001 x 1001 road raster of `build_airport_array`, which the surface
builder owns and hands over.

## 2. Inputs and outputs

```python
encode_airports(
    airports: Sequence[AirportLike],      # aptdata / aptgeom, in Ortho4XP's dict order
    tile: TileRef,
    dem: Elevation,                       # the raster AFTER smooth_raster_over_airports
    params: AirportEncodeParams = DEFAULT_ENCODE_PARAMS,
    *,
    osm: OsmData,                         # the "airports" layer, as OSM_layer holds it
    patch_names: Collection[Hashable] = (),  # patches_list: airports a patch overrides
    patches_area: BaseGeometry | None = None,
    array: NDArray[np.bool_] | None = None,   # build_airport_array, built by the area module
    on_event: EventHandler | None = None,
) -> AirportLayers
```

`AirportLike` is a **protocol**, not a class: this module states what it reads and the
airport-record chantiers decide what they build (section 3). `AirportView` is its concrete
form and `AirportView.of(key, runways, surfaces)` assembles one from the records those
chantiers return (`airports_vec.runways.AirportRunways`, `airports_vec.areas.AirportSurfaces`),
which is the single line the pipeline needs. `AirportLayers` carries

* `layers`: `(geometry, marker, z)` triples in insertion order, consecutive ways of the same
  marker grouped into one pass — exactly what `orthostudio.vectors.assemble.to_vector_layers` takes,
  and what `VectorLayers.airports` is;
* `seeds`: `{marker name: (S, 2)}` in encoding order, for the same call;
* `area`: `treated_area`, and `array`: the 1001 x 1001 boolean raster —
  `AirportLayers.airport_areas()` returns them as the `orthostudio.vectors.roads.AirportAreas` that
  `build_road_layers` already takes (`NO_AIRPORTS` is the wave-1 placeholder for it);
* `bounds`: `(A, 4)` `(xmin, ymin, xmax, ymax)`, one row per airport in input order, for
  `VectorLayers.airport_bounds`;
* `counts`: what went in and what came out, for `stats.json`.

The DEM is read through the `Elevation` protocol of `orthostudio.vectors.assemble` (`alt_vec`),
never through a file. The **order** of `airports` is part of the contract: Ortho4XP iterates a
`dict`, so its insertion order decides the order of the passes, of the seeds and of `airports.json`.

## 3. What an airport is, as this module reads it

`AirportLike` mirrors Ortho4XP's `dico_airports[key]` one for one (`O4_Airport_Utils.py:106-830`),
with the tuples given names:

| protocol member | Ortho4XP | note |
|---|---|---|
| `key` | the dict key | ICAO / IATA / `local_ref` / `****`; only compared with `patch_names` |
| `runways` | `apt["runway"][1] + apt["runway"][2]` | **areas first, then lines**, each a `RunwayLike` |
| `runway_area` | `apt["runway"][0]` | union of the runway polygons |
| `taxiway_area`, `taxiway_ways` | `apt["taxiway"]` | area, then the OSM way ids of the centre lines |
| `apron_area`, `apron_ways` | `apt["apron"]` | idem |
| `hangars` | `apt["hangar"]` | MultiPolygon |
| `boundary` | `apt["boundary"]` | MultiPolygon, only its `bounds` is read here |

`RunwayLike` is `(polygon, start, end, width)` — `start`/`end` are the `(2,)` endpoints of the
centre line in tile-local degrees and `width` is in **metres** (`:640-667`).

**Decision (keep, with a bridge).** The `runways` sequence keeps Ortho4XP's concatenation order
because the altitude field and the seeds depend on it. The module never reads a pickle: the
`Data<tile>.apt` of Ortho4XP is a pickle of shapely objects (arbitration B3); only the comparison
tests loaded one, behind an allow-list, until decision 0010 removed them.

## 4. The altitude field (the hot spot)

`weighted_alt` (`O4_Vector_Utils.py:1250-1280`) gives **every** vertex of a runway, traverse,
taxiway or apron its z. Ortho4XP calls it once per node, in Python, with an `rtree` query inside; on
`+43+005` that is 14 091 calls (the hangars and the helipads read the raster instead). It is the one
thing in this chantier worth vectorising.

### 4.1 Building the field (`:1052-1088`)

Per airport, one entry per runway **and** per taxiway centre line:

* runway: `center_way = vstack((start, end))`, `steps = int(max(100, length_m // 7))`,
  least-squares fit **with weights**, nominal width = the runway width;
* taxiway: the OSM way's nodes minus the tile origin, **unrounded** (`:1077-1082`;
  contrast with the aprons at `:1262`, which round to 7 decimals), `steps` by the same rule,
  fit **without weights**, nominal width = 15 m.

`least_square_fit_altitude_along_way` (`:1182-1212`) scales the way by `scalx` in x, samples
the DEM at `steps + 1` points evenly spaced *along the scaled line* (unscaling x before
sampling), and returns `numpy.polyfit(t, alt, 7)` — with weights
`(max(k, steps - k) + steps // 2) ** 2`, which pull the fit towards the two ends. **Keep**,
transcribed literally; the sampling loop becomes `shapely.line_interpolate_point` on an array
(same GEOS call, same bits) and the fit stays `numpy.polyfit`.

### 4.2 Evaluating it (`:1250-1280`)

For a node `(x, y)`, with `X = x * scalx`:

1. candidates = the fits whose **bounding box** meets `(X ± 0.003, y ± 0.003)`;
2. `weight = exp(-distance(point, line) * lat_to_m / (2 * width))`,
   `alti += polyval(fit, line.project(point, normalized=True)) * weight`;
3. no candidate (`weights < 1e-6`) → the raw DEM altitude;
4. within `eps2 = 0.0003` of a side of the **scaled** unit square, blend linearly with the raw
   DEM altitude, `alpha = min(X, 1 - X, y, 1 - y) / eps2`.

**Keep**, including the two oddities: the border test uses the scaled abscissa `X` against
`1`, and the box query is a bounding-box query (`rtree`), not a distance query, so a fit whose
box reaches the node counts even when the line itself is far away.

**Fix (vectorised, same result to the last bits but one):** the per-node Python loop becomes, per
batch of nodes, one `shapely.STRtree.query` (bounding boxes, the same candidate set as `rtree`), one
vectorised `shapely.distance` / `shapely.line_locate_point`, one Horner pass over the gathered
coefficients (the very loop `numpy.polyval` runs) and one `np.add.at` to accumulate. The **only**
wanted difference is the summation order inside a node with several candidates: Ortho4XP adds in
`rtree` order, OrthoStudio XP in candidate-pair order. Measured effect on `+43+005`: section 10.

## 5. Runways (`:1093-1203`)

### 5.1 The dead branch

`:1093-1137` iterates `for ... in []` (the "runways as lines" branch, commented out in Ortho4XP).
**Drop**: it cannot run.

### 5.2 One runway polygon (`:1139-1203`)

```
refine_size = max(length_m // 100, 10)          # metres, floor division
way   = refine_way(vstack((start, end)), refine_size)
way_r = shift_way(way, width, "right");  way_l = shift_way(way, width, "left")
for pol in ensure_MultiPolygon(cut_to_tile(runway_pol)).geoms:
    boundary  = pol.exterior
    abscissae = [boundary.project(Point(c)) for c in boundary.coords]
    for k in 1 .. len(way) - 1:
        lin = LineString([way_r[k], way_l[k]]).intersection(runway_pol)   # the UNCUT polygon
        if lin is a LineString:  keep (project(first), project(last)) and both abscissae
    way = round([boundary.interpolate(a) for a in sorted(set(abscissae)) + [0]], 7)
    insert_way(way, RUNWAY)
    for (a1, a2) in traverses:  insert_way(round([interpolate(a1), interpolate(a2)], 7), DUMMY)
```

Four things are worth naming because they are observable, and all four are **kept**:

1. the outline is not the polygon's own ring: it is the ring **re-sampled at the sorted union**
   of its vertices' abscissae and of every traverse end, then closed by repeating abscissa 0.
   That is what makes every traverse end an existing node of the outline instead of a crossing;
2. the traverses are clipped against the **uncut** `runway_pol`, so a runway leaving the tile
   keeps chords whose ends are outside the cut polygon; `project` then returns the nearest
   abscissa on the cut ring. Reproduced as is;
3. `way` is **reassigned** to the outline inside the polygon loop (`:1173`), so a runway cut into
   two or more pieces uses the previous piece's outline as its refinement grid for the next piece,
   and `way_r[k]` / `way_l[k]` then raise `IndexError`, which Ortho4XP swallows (`:1157-1168`).
   OrthoStudio XP reproduces the reassignment and the silent skip, and reports it as
   `OSM_AIRPORT_SURFACE_INVALID` (`surface="runway traverse"`, degraded, continue) instead of losing
   it — no new code was added to the registry for wave 2 (`src/orthostudio/errors.py` is out of this
   chantier's scope);
4. every coordinate inserted is rounded to **7 decimals** (`:1174`, `:1191`), the same
   rounding OSM input gets (`vectors-pslg.md` 2.8).

`geometry.LineString` intersections that come back empty are `LINESTRING EMPTY`, whose `geom_type`
is `LineString`: Ortho4XP then raises `IndexError` on `coords[0]` and catches it. OrthoStudio XP
tests `is_empty` instead — same outcome, no exception.

### 5.3 Flatness

A runway is **not** flat by construction here: the z of its outline comes from the altitude
field, whose fit is a degree-7 polynomial along the centre line weighted towards the ends.
What makes the runway usable is that the *raster* was smoothed over the airport first
(`smooth_raster_over_airports`), so the fit is already nearly constant, and that the mesh
takes its z from the vector attribute (`attr >= INTERP_ALT`). The acceptance test therefore
measures the **span** of the z of each runway outline and compares it with Ortho4XP's, rather than
asserting a constant.

**Measured on `+43+005`** (42 runway outlines): median span **2.37 m**, maximum **18.60 m**,
37 of the 42 above 0.5 m -- and every one of them equal to Ortho4XP's to 2e-13 m. So a runway is
*not* flat after step 1, in Ortho4XP or here; what X-Plane flattens is what the apt.dat scenery
does with it. Making the vector z constant per runway would be a **wanted difference** with a
visible effect on the mesh; it is not taken here, and the assertion above exists so that
taking it later is a deliberate, measured decision (`blocage`).

## 6. Seeds

### 6.1 Runways (`:1195-1203`)

For each encoded polygon `p` of the airport, with `others = union(everything else)`:
one seed per part of `p - others`, then one seed per part of `p & others`. Crossing runways
therefore get a seed in each of the four quadrants **and** one in the crossing itself — which
is the point: after Triangle4XP floods the regions, the crossing is a region of its own and
must not be left unattributed. **Keep**, including the `pol2 != pol` comparison (shapely's
exact-coordinate equality) and the order (all differences of `p`, then all intersections).

### 6.2 Taxiways, aprons, hangars, helipads

One `representative_point()` per encoded polygon, in encoding order (`:1229`, `:1275`,
`:1362`, `:1455`). **Keep**, through `orthostudio.vectors.seeds.polygon_seeds`.

### 6.3 Where they end up

The seeds of one marker are written together, sorted by marker value
(`vectors-assembly.md` 4.3); this module returns them by marker name and the assembler does
the rest. `RUNWAY` seeds come from all airports in order, then `TAXIWAY`, then `APRON`
(`:1315-1320`), then `HANGAR` (`:1364-1368`), then the helipads' `INTERP_ALT` (`:1456-1460`).

## 7. Taxiways and aprons

### 7.1 Cleaning the taxiway area (`:1205-1213`)

```
cleaned = improved_buffer(taxiway_area - (improved_buffer(runway_area, 5, 0, 0)
                                          | improved_buffer(hangars, 20, 0, 0)), 3, 2, 0.5)
```
and **Ortho4XP writes it back into the airport** (`:1215`), so the `treated_area` returned to the
caller — the one the roads are subtracted from and the one a helipad must miss — uses the
*cleaned* taxiways for the airports it encoded and the *raw* ones for the airports a patch
overrides. **Keep**; since `AirportLike` is read-only here, the cleaned areas are carried in
the result instead of being written back.

Each polygon of `cut_to_tile(cleaned)` that is valid, non-empty and larger than `1e-9` deg^2
is encoded: exterior then holes, `refine_way(..., 20 m)`, rounded to 7 decimals (`:1217-1232`).

### 7.2 Aprons (`:1234-1277`)

Only a way **tagged `include`** by the user in their local copy is encoded, refined at 15 m.
Two kept quirks: the guard reads `runway_pol.is_valid` — the *leftover loop variable* of the
runway loop, which is the last runway polygon of the last airport that had one, and raises
`NameError` (caught, apron skipped) when no airport has had one yet; and the coordinates are
rounded to 7 decimals **twice** (before and after refinement, `:1262-1269`). **Keep** both,
and report the skip as `OSM_AIRPORT_SURFACE_INVALID` (`surface="apron"`).

On `+43+005` no apron carries the tag, so this path has **no oracle coverage**; it is covered
by a synthetic test and flagged in section 10.

## 8. Hangars (`:1344-1370`)

Per airport (patched ones skipped), per polygon of `cut_to_tile(hangars)`: sample the raster
over the **unrounded** exterior; if `max - min <= 1.5 m`, insert the outline with a constant z
equal to the mean, and seed it. Otherwise the hangar is dropped — outline and seed. **Keep**.

## 9. Helipads (`:1370-1464`, `helipads.py`)

1. every closed OSM way tagged `aeroway=helipad`, rounded to 7 decimals, non-empty, valid, of
   non-zero area and **not intersecting `treated_area`**, becomes a polygon;
2. every OSM **node** tagged `aeroway=helipad` that falls neither in those polygons nor in
   `treated_area` becomes a regular hexagon of radius 9 m (`7` vertices, `k * pi / 3`,
   `m_to_lon(lat)` in x and `m_to_lat` in y), rounded to 7 decimals;
3. the union, cut to the tile, is inserted polygon by polygon with a **constant** z (the mean
   of the raster over the outline) and marker `INTERP_ALT`, one seed each.

**Keep**, including the two dead blocks Ortho4XP leaves commented out at `:1424-1428` and
`:1443-1447` (the per-polygon insertion before the union) — **drop** those, they cannot run.
Note that step 2 tests the centre against the union of step 1 **before** the cut to the tile,
and step 3 cuts: a helipad straddling the tile side is kept and clipped. **Keep.**

On `+43+005`, 55 helipads survive the two exclusions and their union, cut to the tile, gives
**52 flattened polygons**. They matter for a second reason: Ortho4XP inserts them with the same
marker as the roads that follow, so the recorded `INTERP_ALT` run of the reference tile
(1 304 ways) is *52 helipads then 1 252 roads*, not 1 304 roads. Wave 1 read that run as one
road pass; the integrator must split it (`tests/test_aptencode_oracle.py` does).

## 10. Acceptance and measurements

Reference tile `+43+005`, the inputs being the `.apt` of the reference build (loaded in the
test only), the warm `+43+005_airports.osm.bz2` of the Ortho4XP checkout and the **smoothed**
`Data+43+005.alt` of the reference build.

### 10.1 Way by way (`test_the_ways_are_the_ones_ortho4xp_inserted`)

**2 644 ways emitted, 2 644 expected**, in the same order and with the same marker: 42
`RUNWAY`, 2 411 `DUMMY` traverses, 40 `TAXIWAY`, 0 `APRON` (no apron of this tile carries the
`include` tag), 99 `HANGAR`, 52 `INTERP_ALT` helipads. Coordinates: **maximum difference
0.0** — every one of the 15 591 vertices is bit-identical to Ortho4XP's. Altitudes: **maximum
difference 4.0e-13 m**, the summation-order difference of section 4.2 and nothing else (the
`.node` file carries 9 decimals, so this is 3 orders of magnitude below what it can express).

### 10.2 Seeds (`test_the_seeds_are_the_ones_of_the_poly`)

84 `RUNWAY`, 28 `TAXIWAY`, 99 `HANGAR`, 52 `INTERP_ALT` = **263 seeds**, each within 5e-16 of
the corresponding seed of `Data+43+005.poly` (the file carries 15 decimals). The order is the
file's: the airport seeds of a marker come first, so the 52 helipad seeds are the first 52 of
the 1 034 `INTERP_ALT` seeds.

### 10.3 End to end (`test_the_whole_pslg_matches_the_reference`)

These layers, plus Ortho4XP's own recorded road (1 252 ways), coastline and water passes, plus the
grid and border the assembler builds itself:

| noder | nodes | edges | markers | z |
|---|---|---|---|---|
| wave 1 (`f4ea5c5`) | 247 272 / 247 282 | 271 310 / 271 320 | 0 mismatch | 0 above 1e-9 outside the 10 merged pairs |
| with the `nodes10` fix | **247 282 / 247 282** | **271 320 / 271 320** | 0 mismatch | 0 above 1e-9 |

With the second, `Data+43+005.node` has **247 279 of its 247 282 lines byte-identical** to
Ortho4XP's (the 3 others are 9-decimal ties on grid abscissae, `x = 0.4052734375` and
`0.6689453125`, already described in `vectors-pslg.md` 2.8) and every edge and marker is the
reference's. The node *order* still differs (the noder numbers nodes by creation inside a
pass), so the files are not byte-identical; the seed block is
(`test_the_seed_section_is_byte_identical`). The graph is planar (0 violations).

### 10.4 Cost (`nice -n 10`, M4 Pro, load 4.2)

| step | time |
|---|---|
| `encode_airports` on the 18 airports of the tile | **0.33 s** |
| of which the altitude field, 14 091 vertices (280 k evaluations) | 0.15 s |
| the same arithmetic one node at a time (the loop of Ortho4XP) | 10.0 s, i.e. **67x** |
| noding the 95 passes into the graph | 0.25 s (0.04 s if the passes were merged per marker, which would move the z of the crossings) |

The whole airport block therefore costs about 0.6 s of the vector stage, which measured 5.13 s
without airports in wave 1 against 26.70 s for Ortho4XP's step 1 as a whole.

## 11. What this spec does not cover

The airport geometry itself (`discover_airport_names`, `attach_surfaces_to_airports`,
`sort_and_reconstruct_runways`, `discard_unwanted_airports`, `build_*_areas`,
`update_airport_boundaries`), the smoothing of the raster over the airports, the published
airport record, and the high-resolution imagery cover of airports
(`cover_airports_with_highres`, consumed by the DSF stage, arbitration B4).
