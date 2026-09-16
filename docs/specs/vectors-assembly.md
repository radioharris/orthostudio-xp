# Vector assembly: ordering the layers, the orthophoto grid, the seeds, the Triangle files

Status: P4 wave 1 (`src/orthostudio/vectors/assemble.py`, `grid.py`, `seeds.py`, `rule.py`).
Origin: Ortho4XP `src/O4_Vector_Map.py` (`build_poly_file`, lines 19-179) and
`src/O4_Vector_Utils.py` (`Vector_Map.encode_MultiPolygon` 380-434, `snap_to_grid` 469-535,
`write_node_file` 537-559, `write_poly_file` 561-615).
Companion specs: `vectors-pslg.md` (the noder, written in P0 — it is the *contract of this
stage*, not a restatement), `mesh-build.md` section 3 (what the mesh reads back),
`graph-keys.md` (how the rule is keyed).

This spec covers the *glue*: which layer goes in before which, what the orthophoto grid and
the gluing border are made of, where seeds come from and in what order they are written, and
what the artefact directory holds. It covers **no geometry building**: coastline closure,
water simplification, road buffering and patches are the layer builders' specs; airports are
wave 2 (arbitration A1).

## 1. What the stage does

```
    OSM snapshot ──► layer builders (coastline, water, roads, patches, [airports]) ─┐
                                                                                    ▼
    DEM ──► orthophoto grid + gluing border (this spec, section 3) ──►  assemble_vectors
                                                                                    │
                                        node_layers (vectors-pslg.md)  ◄────────────┘
                                                                                    │
                          Data<tile>.node + Data<tile>.poly + layers.npz + stats.json
```

`assemble_vectors` owns four decisions and nothing else:

1. the **order** the layers are noded in, which is the z and marker priority (section 2);
2. the **orthophoto grid** and the **gluing border**, which it builds itself (section 3);
3. the **seeds**: accumulation, the empty-map default, the output order (section 4);
4. the **artefact**: the two Triangle files and the two side files (section 5).

## 2. Layer order = priority

### 2.1 The order of Ortho4XP

`build_poly_file` calls, in this order (`O4_Vector_Map.py:52-152`):

| # | call | line | marker(s) | note |
|---|---|---|---|---|
| 1 | `include_patches` (from inside `include_airports`) | `:215` | INTERP_ALT (`:914-960`) | **before** the runways |
| 2 | `APT.encode_runways_taxiways_and_aprons` | `:216` | RUNWAY, DUMMY traverses, TAXIWAY, APRON | per airport |
| 3 | `APT.encode_hangars` | `:219` | HANGAR | |
| 4 | `APT.flatten_helipads` | `:220` | INTERP_ALT | |
| 5 | `include_roads` | `:63` | INTERP_ALT | buffered banked roads |
| 6 | `include_sea` | `:74` | SEA | |
| 7 | `include_water` | `:84` | WATER then SEA_EQUIV | |
| 8 | orthophoto grid | `:96-130` | DUMMY | verticals, then horizontals |
| 9 | gluing border | `:136-152` | DUMMY | four polylines |

**Keep.** Rule A-ORDER: `VectorLayers.ordered()` returns exactly
`patches + airports + roads + coastline + water + grid_vertical + grid_horizontal + border`.

Wanted correction of the brief: the brief of this chantier states the order as "airports > roads >
coast > water > **patches** > grid > gluing". The source says patches are inserted *first*, at
`O4_Vector_Map.py:215`, before `encode_runways_taxiways_and_aprons` at `:216`. Priority decides z
where two layers touch, so a patch under a runway keeps its own altitude in Ortho4XP; OrthoStudio XP
follows the source. No effect on the reference tile (Marseille has no patch). Recorded as a blocage
for the architect.

### 2.2 Why the grid is two layers and the border a third

The noder resolves a layer against the graph accumulated so far, which is what gives the
"the pre-existing edge decides z" rule (vectors-pslg.md 2.4). Inside one layer it uses each
way's *original* segment instead of its current piece — an approximation that would bite
exactly where a horizontal grid line crosses a vertical one already split by roads and water.
Ortho4XP inserts every vertical line before every horizontal one (one `MultiLineString`,
`insert_way` per line, `O4_Vector_Map.py:118-130`) and the border in a separate call
(`:136-152`), so splitting them into three consecutive layers is *faithful*, not a
simplification: the verticals never meet each other, nor do the horizontals, nor the four
border polylines except at the tile corners, where they share an end point.

### 2.3 Airports as an injected input (arbitration A2)

`VectorLayers.airports` is a plain sequence of already-built layers (geometry, marker, z per
coordinate, seeds), empty in wave 1. The assembler never calls an airport module. Wave 2 fills
it from `O4_Airport_Utils.py:1038-1462` in the order of section 2.1 (runways and their DUMMY
traverses, taxiways, aprons, hangars, helipads) and fills `VectorLayers.airport_bounds`, which
becomes `airports.json` (`mesh-build.md` 3.2, read by `orthostudio.mesh` for the curvature weights).
While it is absent the mesh stage reports `MESH_WEIGHT_MAP_INCOMPLETE`, which is the truth.

### 2.4 What a layer builder hands over

The four wave-1 builders (`orthostudio.vectors.coast`, `water`, `roads`, `patches`) return the same
pair: `layers`, a tuple of `(geometry, marker, z)` triples in insertion order — the triple
`node_layers` takes — and `seeds`, a map from attribute *name* to `(S, 2)` points for the
whole family. `assemble.to_vector_layers(name, layers, seeds)` turns that pair into named
passes: each attribute's seeds are attached to the first pass carrying it, which reproduces
the written order exactly (section 4.3), and a seed no pass carries raises instead of being
written into a region no edge bounds.

A `VectorLayer` is therefore never built by hand outside a test: the builder produces the
triples, the adapter names them, `VectorLayers` orders the families.

## 3. Orthophoto grid and gluing border (`grid.py`)

### 3.1 Abscissae (`O4_Vector_Map.py:96-117`)

```
(til_xul, til_yul) = wgs84_to_orthogrid(lat + 1, lon,     mesh_zl)
(til_xlr, til_ylr) = wgs84_to_orthogrid(lat,     lon + 1, mesh_zl)
for til_x in range(til_xul + 16, til_xlr + 1, 16):  x = (til_x / 2**(mesh_zl-1) - 1) * 180 - lon
for til_y in range(til_yul + 16, til_ylr + 1, 16):  y = 360/pi * atan(exp(pi * (1 - til_y / 2**(mesh_zl-1)))) - 90 - lat
```

then `x = 0`, `x = 1`, `y = 0`, `y = 1` are added to the two **sets** and each set is sorted.
`wgs84_to_orthogrid` is `O4_Geo_Utils.py:127-134` (truncation towards zero, times 16): a
group of 16x16 web-mercator tiles at `mesh_zl` is one 4096x4096 orthophoto.

**Keep, bit for bit.** The arithmetic is reproduced in the same order and the same types
(Python floats, `int()` truncation, set deduplication by exact equality), because the result
is a 9-decimal node key: recomputing `x` as, say, `til_x * 180 / 2**(mesh_zl-1) - 180 - lon`
is algebraically identical and numerically different. OrthoStudio XP reuses
`orthostudio.imagery.grid.texture_at` (the P1 transcription of `wgs84_to_orthogrid`, already
bit-tested) instead of a second copy of the formula.

Measured on +43+005 at `mesh_zl = 19`: 93 vertical and 127 horizontal lines, *all 220
identical bit for bit to the ways Ortho4XP inserted* (fixture `layers+43+005.npz`).

### 3.2 Lines (`O4_Vector_Map.py:131-135`)

`eps = 2**-5`; each vertical is `[(x, -eps), (x, 1 + eps)]`, each horizontal
`[(-eps, y), (1 + eps, y)]`: two points, deliberately overshooting the tile so that a grid
line crosses the border instead of ending on it. Markers DUMMY, `skip_cut=True` (no
`cut_to_tile`), `check=True`. **Keep.**

### 3.3 Gluing border (`O4_Vector_Map.py:136-152`)

Four polylines of `segs = 2048` segments: `y = 0`, `y = 1`, `x = 0`, `x = 1`, each with
abscissae `numpy.arange(0, segs + 1) / segs`, in that order, marker DUMMY. Neighbouring tiles
therefore carry the same border vertices and the meshes glue. **Keep**, including the
division `k / 2048` (exact in binary, which is why `k/2048` values are 9-decimal ties — see
vectors-pslg.md 2.8).

### 3.4 Altitudes

`tile.dem.alt_vec(way)` per way for grid and border (`O4_Vector_Map.py:127, 149`). OrthoStudio XP
calls `Elevation.alt_vec` once per line group, which is identical (the query is per point).
**Measured**: on the 8 636 grid and border points of +43+005, `orthostudio.dem.Dem.alt_vec` on the
reference `Data+43+005.alt` reproduces the recorded z with a maximum difference of **0.0**.

Note for wave 2: the raster Ortho4XP samples here is the one *smoothed over airports*
(`APT.smooth_raster_over_airports`, called at `O4_Vector_Map.py:213`, before the grid). In
wave 1 the DEM is not smoothed, so grid z over an airport differs from Ortho4XP's by the smoothing
— which is the very thing arbitration A3 removes from the comparison by building the reference
without airports.

## 4. Seeds (`seeds.py`)

### 4.1 Where a seed comes from

One seed per polygon, at `polygon.representative_point()`, appended **after** that polygon's
rings are inserted (`O4_Vector_Utils.py:409-424`); the polygon is the one that was actually
encoded, i.e. after `cut_to_tile`, `simplify`, the `area <= area_limit` filter and
`geometry.polygon.orient` (`:405-418`). A failure of `representative_point` is caught, logged
at verbosity 2 and the polygon keeps its edges but gets no seed (`:419-424`).

**Keep.** Consequence for the module boundary: the seed of a layer is the layer builder's
business, because only the builder knows the polygons it kept. `seeds.polygon_seeds` is the
shared primitive (vectorised `shapely.point_on_surface`, with a per-polygon fallback that
skips the ones GEOS refuses and counts them); `assemble_vectors` only accumulates what each
layer hands it, in layer order then polygon order.

Lines never seed: `encode_MultiLineString` has no seed branch (`:436-467`). The orthophoto
grid, the gluing border, the coastline layer (inserted as lines, `include_sea`) and the
airport DUMMY traverses therefore contribute none; the sea seeds come from the polygons
`coastline_to_MultiPolygon` builds (`O4_Vector_Map.py:441-451`).

### 4.2 The empty-map default (`O4_Vector_Map.py:160-166`)

```python
if not vector_map.seeds:
    vector_map.seeds["SEA"] = (
        [array([1000, 1000])] if tile.dem.alt_dem.max() >= 1 else [array([0.5, 0.5])]
    )
```

A seed outside the unit square floods nothing: a tile with relief and no vector data stays
land. A tile whose whole raster is below 1 m is all sea. **Keep**, including the test on the
*whole* raster (which is 1.01 degrees wide, not the unit square) and the `>= 1` comparison.
The default applies only when the map has **no seed at all**, not per marker.

### 4.3 Output order (`O4_Vector_Utils.py:596-615`)

Markers are visited in increasing attribute value (`sorted(dico_attributes.items(), key=value)`)
and, inside one marker, in insertion order; the index column is 1-based over the whole list;
coordinates are written with 15 decimals. **Keep** — this is already
`triangle_files.write_poly_file`, which sorts stably by marker value. OrthoStudio XP keys the seed
map by the attribute *value* (the names are a bijection with the values, `O4_Vector_Utils.py:44-54`)
so an assembled marker such as `WATER | SEA_EQUIV` would be an error rather than a silent misfile;
none exists in Ortho4XP, every `encode_*` call passes a single name.

Measured on +43+005: 2 615 seeds, 1 365 WATER, 1 034 INTERP_ALT, 99 HANGAR, 84 RUNWAY,
28 TAXIWAY, 5 SEA — 211 of them (RUNWAY, TAXIWAY, HANGAR) belong to wave 2.

## 5. Artefact

```
Data<tile>.node   Triangle nodes, "N 2 1 0" then "i x y z" (9 decimals, 1-based)
Data<tile>.poly   "0 2 1 0", "M 1", "i n0 n1 marker", 0 holes, S seeds (15 decimals)
layers.npz        the layers as they were inserted, one entry per *part* (a LineString, or
                  one ring of a polygon): coords (K, 3) f64 x/y/z, offsets (P+1,) i64,
                  markers (P,) u8, layer (P,) i32 = index of the pass, names (L,) <U32.
                  A tile can be re-noded and profiled from it with no network and no OSM
                  parsing; re-noding it gives back the same graph, bit for bit
                  (test_vectors_assemble.py::test_the_replay_file_holds_the_segments...)
stats.json        counts, per-marker edge and seed counts, timings, planarity. Edge markers
                  are labelled by their bits ("WATER|SEA"): an edge carries the OR of
                  everything covering it, unlike a layer or a seed
airports.json     the (A, 4) tile-local bounding boxes, for the curvature weight map of the
                  mesh stage (mesh-build.md 3.2)
Data<tile>.alt    wave 2: the elevation raster AFTER the airport smoothing -- the one Ortho4XP
                  writes and the one the mesh, the masks and the DSF read (B2)
dem.json          its window, in the osxp-dem-1 format DemSpec.from_dir reads, so the mesh
                  node can take this artefact as its `dem` input
airports.npz      wave 2: OrthoStudio XP's airport record, geometries as WKB
airports.wkb.json its scalars (key, key_type, name, repr_node, way ids, runway axes); the DSF's
                  high-resolution airport cover reads the pair (airports-integration.md 4)
```

Cost on the reference tile (M4 Pro, `nice -n 10`, 1-minute load 3.8): assembly **1.14 s**
(of which the noder 1.13 s), writing **0.53 s** (`.node` 0.23, `.poly` 0.18, `layers.npz`
0.13), total **1.70 s**. Ortho4XP spent 23.13 s in `insert_way` and 0.90 s in `snap_to_grid` on
the same layers (measured before decision 0010), so the ported part is **x21**.

The two text files are what `orthostudio.mesh` reads (`mesh/build.py:338-359`), which is why the
format is Ortho4XP's and not a binary of our own; ADR 0004's binary variant replaces both at once,
on both sides.

`snap_to_grid(9)` (`O4_Vector_Map.py:167`) is not a step here: the noder *is* snapped to 9
decimals by construction (vectors-pslg.md 2.8), which is where its 10 merged node pairs come
from.

## 6. Parameters of the rule `orthostudio.vectors@1` (`rule.py`)

Consumed, and only these (`grep "tile\." O4_Vector_Map.py O4_Airport_Utils.py`):

| param | used by | line |
|---|---|---|
| `tile` | the artefact is tile-specific | |
| `road_level` | which OSM road layers exist, whether roads are inserted | `:63, 228-361` |
| `road_banking_limit`, `lane_width`, `max_levelled_segs` | road filtering and buffering | `:228-361` |
| `water_simplification`, `min_area`, `max_area` | water layer | `:454-589` |
| `clean_bad_geometries` | coastline and water repair | `:363-452` |
| `mesh_zl` | the orthophoto grid | `:96-117` |
| `apt_smoothing_pix` | the airport elevation smoothing, hence the bytes of `Data<tile>.alt` | `O4_Airport_Utils.py:924-1038` |
| `exact_grid_order` | OrthoStudio XP only: insert each horizontal grid line as its own pass, which reproduces Ortho4XP's intra-layer cutting and closes the last residual nodes (3 on +43+005), at twelve times the noding before review 6 and 1.6 times since the incremental insertion (`vectors-pslg.md` 2.13) | — |

Not parameters: `custom_dem` and `fill_nodata` (the elevation arrives as the `dem` input, keyed by
its digest — same decision as `orthostudio.mesh` and `orthostudio.masks`) and `iterate` (forced to
0 at `O4_Vector_Map.py:23`).

Inputs: `osm` (the snapshot of `orthostudio.osm@1`), `dem`, `patches` (optional: a folder of
`.patch.osm` files; a build gives none, decision 0010), `airports` (optional and **always
absent**: wave 2 builds the aerodromes inside the stage, from the `aeroway` layer of the `osm`
input, exactly as `include_airports` does, so the declared input has no reader and is refused if one
is passed — it is kept declared so that every key minted so far keeps its meaning).

Arbitration A7, superseded by decision 0008 on 2026-09-13: the rule is **not wired by default**.
Wave 2 completed it, and the reason it is still opt-in is measured rather than structural: the PSLG
is Ortho4XP's, but Ortho4XP's *node numbering* is not reproducible, so the mesh and the DSF are
equivalent and not byte-identical (`docs/benchmarks/p4-airports.md` 5 makes the case and names the
condition for flipping it).

Where the layers come from: `assemble_vectors` takes them, it does not build them. The rule
gets them from an injected builder (`VectorsJob.build_layers`, bound with `vectors_job()`, the
same pattern as `MeshJob`, `MasksJob` and `OsmJob`), whose default resolves
`orthostudio.vectors.layers:build_layers` — the entry point the layer-builder chantiers of wave 1
converge on, written in section 7.

## 7. Wiring the builders (`layers.py`)

`build_layers(request) -> LayerBuild` is the only module that knows *which* builder feeds
*which* family and *where* its input comes from; it builds no geometry and decides no order.
Since wave 2 it returns the elevation as well as the layers: the airports smooth the raster
before anything samples it (`O4_Vector_Map.py:213`), so the raster the rule assembles with,
samples the orthophoto grid from and publishes as `Data<tile>.alt` is the one the builders
used, not the one the rule read (`airports-integration.md` R-I1).
`request` is a `rule.LayerRequest` (tile, params, the `osm` input path, the elevation, the
optional `patches` and `airports` paths); it is typed loosely so that `layers.py` never
imports the rule that imports it.

### 7.1 The OSM input (`open_osm_source`)

The input is an `orthostudio.osm@1` artefact, recognised by its `snapshot.json` or by
`osm/<folder>/<tile>/<tile>_coastline.osm.json.zst`, and read with `SnapshotStore.load` then
`osmdata.load(snapshot)`. Anything else raises `OSM_LAYER_UNAVAILABLE` naming the directory: a
vector stage that silently produced an empty tile is exactly the failure mode the rewrite exists to
remove. Until decision 0010 the stage also accepted Ortho4XP's `.osm.bz2` cache (arbitration A6: the
two sources gave the same store, `vectors-osm-layers.md`).

Every layer `layers_for(road_level)` asks for is **required** (review 5: `small_roads` used to be
tolerated, which built a different tile without a word; Ortho4XP would download it), and since
wave 2 that includes `airports`: the aerodromes are built from it inside the stage
(`docs/specs/airports-integration.md`), so the pipeline demands it again (`_vectors_osm_input`).

Ortho4XP's `include_sea` (`O4_Vector_Map.py:366-389`) and `include_water` (`:505-521`) prefer the
user's `<tile>_custom_{coastline,water}.osm.bz2` of its `OSM_data/` folder to the downloaded
layer, and the coastline one sets `custom_source=True`, which makes every ring an island whatever
its winding (`O4_Vector_Utils.py:887`). OrthoStudio XP read those files from review 5 to decision
0010; a build no longer reads an Ortho4XP folder, so they have no source. `CoastParams.custom_source`
stays in the coastline builder.

### 7.2 Build order and the one coupling

Families are built in Ortho4XP's own order (`O4_Vector_Map.build_poly_file`), with a single
exception that is a *construction* order, not an insertion order: **water is built before the
coastline** because the large lakes it classifies as sea-equivalent are the `sea_equiv`
pass-through of `CoastResult` (arbitration A2, `vectors-coastline.md` 6). The insertion order
is unchanged — `VectorLayers.ordered()` still puts the coastline before the water, and the
`SEA_EQUIV` layer still travels with the water family, where `include_water` encodes it
(`O4_Vector_Map.py:454-590`).

The `sea_equiv` pass-through itself is **reserved, not read in wave 1**: `build_sea_layers`
stores it in `CoastResult.sea_equiv` and nothing downstream reads it back (review 5). The
build order above is kept because wave 2 needs the slot filled before the coastline is closed,
not because wave 1 gains anything from it.

Cancellation is polled **between families**, between two airports of the encoder, before every
noding pass and before the artefact is written (`AssemblyParams.cancel`, review 6); nothing below
that is interruptible, so the worst-case latency of a Ctrl-C is one road-banking scan or one noding
pass — measured at about 2 s on the dense reference tile (review 5). `on_skip` goes to the
`orthostudio.vectors.layers` logger at DEBUG, so a malformed way is recorded and never printed over
a build; a coded `on_event` is logged at the level of its registry severity (DEGRADED -> WARNING,
INFO -> INFO) and counted by code in `stats.json` under `events`, beside the family counters under
`families` (review 6: a rejected runway used to be invisible).

### 7.3 Wave 1 leaves two slots empty

`VectorLayers.airports` is `()` and the road builder is given `roads.NO_AIRPORTS`;
`airport_bounds` is `None`, so the artefact has no `airports.json`. `_airport_areas` and
`_airport_bounds` are the two three-line functions wave 2 fills in, and nothing else in this
module changes shape (arbitration A2).

An `airports` **input that is actually present** is refused (`SYS_INTERNAL_ERROR`, detail
`airport layers are wave 2`) instead of being read and dropped: it is a declared input of the
rule, so it enters the cache key, and keying an artefact on data it does not contain is worse
than failing (review 5).

## 8. Acceptance

| # | Test | What it proves |
|---|---|---|
| A1 | `test_vectors_grid.py::test_grid_matches_legacy_ways` (oracle) | the 220 grid lines and the 4 border polylines are **bit-identical** to the ways Ortho4XP inserted |
| A2 | `test_vectors_grid.py` unit | abscissae come from the same formula, 16-tile step, the tile edges are in the set exactly once, eps overshoot, 2048 segments |
| A3 | `test_vectors_seeds.py` | one seed per polygon at `point_on_surface`, insertion order, invalid polygons skipped and counted, the two defaults, the sort by marker value |
| A4 | `test_vectors_assemble.py` | `ordered()` is the order of section 2.1; an empty `VectorLayers` still produces grid + border + the default seed; the airports input is injected, never built; the files are re-readable and the graph is planar |
| A5 | `test_vectors_assemble_oracle.py::test_assembly_matches_legacy_pslg` | the whole assembly, fed the recorded layers of +43+005 **and its own grid and border**, matches Ortho4XP's `.node`/`.poly` at the level of vectors-pslg.md: same nodes at 1.5e-9, same unordered edges, same markers, same z |
| A6 | `test_vectors_assemble_oracle.py::test_seed_section_is_byte_identical` | the seed section written from the reference seeds is byte-identical to Ortho4XP's |
| A7 | `test_p4_integration.py` (oracle) | the **whole wave-1 chain** — OSM cache -> `layers.build_layers` -> `assemble_vectors` -> `.node`/`.poly` — against an Ortho4XP stage 1 run with an **empty airport cache** (arbitration A3), fabricated offline by the test itself |

A5 is the wave-1 proof required by arbitration A3/A4. Because the fixture holds the layers
*with* airports, the run includes them as an injected `airports` input, which is exactly what
wave 2 will do; the wave-1 pipeline simply passes an empty one. It also carries Ortho4XP's own
seeds, attached to the first pass of each attribute, which is enough to reproduce the written
order exactly (section 4.3).

Measured, 2026-09-12 (`tests/test_vectors_assemble_oracle.py`, numbers from
`bench_noding.compare`):

| | OrthoStudio XP | Ortho4XP |
|---|---|---|
| nodes | **247 282**, all matched within 1.5e-9, 0 in excess, 0 missing | 247 282 |
| edges | **271 320**, 0 in excess, 0 missing | 271 320 |
| markers on the common edges | **0 mismatch** | |
| z on the 107 230 nodes of INTERP_ALT-class edges | **0 mismatch > 1e-9** (max 5.0e-10) | |
| z on all common nodes | **0 mismatch > 1e-9** | |
| seeds | 2 615, section **byte-identical** | 2 615 |
| planarity (`check_planar`) | **0 violation** | |

Updated 2026-09-12 (wave 2): the ten near-duplicate nodes Ortho4XP writes 1e-9 apart used to be
merged here, which cost 10 nodes and 10 edges; the `nodes10` chantier found the two causes
(the axis-aligned coordinate substitution, and Cramer's rule disagreeing with LAPACK on 60 %
of the intersection parameters) and corrected the solver, so the replayed assembly is now
**equal** to Ortho4XP's PSLG and not merely equivalent. Byte-identity of the two *files* is still
out of reach, for a different reason: Ortho4XP numbers a crossing when it creates it, in the order
its libspatialindex R-tree returns the candidates, and that order is a property of the index,
not of the geometry (`docs/benchmarks/p4-airports.md` 3).

### 8.1 Wave 1 without airports (A7, arbitration A3)

The reference of A5 is the fixture, which has airports; A7 adds the reference wave 1 is
actually accountable for. `tests/test_p4_integration.py` copies the three warm `.osm.bz2`
caches into a throw-away Ortho4XP root, writes an **empty and valid** airport cache next to
them, runs stage 1 there through `tools/oracle`'s `LegacyRunner` (no network: the log says
`* Recycling OSM data from` for the four layers) and compares. Measured, 2026-09-12, numbers
and their analysis in `docs/benchmarks/p4-vectors.md`:

| | OrthoStudio XP wave 1 | Ortho4XP without airports |
|---|---|---|
| nodes | **220 481**, all matched within 1.5e-9, 0 in excess, 0 missing (since `nodes10`) | 220 481 |
| edges | **239 234**, 0 in excess, 0 missing | 239 234, **0 marker mismatch** |
| z on INTERP_ALT-class nodes | **0 mismatch > 1e-9** | |
| seeds | 1 365 WATER + 5 SEA + 911 INTERP_ALT; section **byte-identical** | same |
| headers | `.poly` and `.node` identical (`220481 2 1 0`) | |
| planarity | **0 violation** | |
| stage wall clock | **5.13 s** as a graph node | 26.70 s (**x5.2**) |

### 8.2 Wave 2, with airports

`tests/test_p4v2_oracle.py` compares the same stage, airports included, with the complete
frozen build: 247 282 / 271 320 on both sides, 0 marker mismatch, seed block byte-identical,
`Data<tile>.alt` byte-identical, 39/39 `.ter` byte-identical, and — in canonical node order,
with `exact_grid_order` on — a byte-identical `.node`, `.poly`, mesh and DSF. Numbers:
`docs/benchmarks/p4-airports.md`. The stage is 6.55 s against 31.8 s.

Downstream, fed to the same Triangle4XP and the same DSF encoder, the two PSLGs give meshes
of 567 286 and 567 290 vertices that are **equivalent but not byte-identical**: the ten merged
nodes renumber Triangle's input and its refinement is order-sensitive. The 39 `.ter` are
byte-identical, the DSF differs by 962 bytes and by the *order* of the same 40 terrain names.
The elevation is not involved (the same mesh comes out of both rasters, byte for byte).

### 8.3 What the proof does **not** cover (review 5)

The equivalence above is proven at *one* point of the parameter space, +43+005 at
`road_level` 1 and `mesh_zl` 19. Known holes, to be closed by a second oracle tile:

| hole | why it is open | what would close it |
|---|---|---|
| `SEA_EQUIV` (marker 4) | the reference tile never produces one (markers observed: 0, 1, 2, 3, 8), so neither `max_area` nor the "large lake = sea" path is exercised end to end | an oracle tile with a lake above `max_area` |
| `road_level >= 2` | the fixture has no `small_roads` cache, so the layer never reaches the assembly in an oracle run | a tile whose cache carries `small_roads` |
| patches | +43+005 has none; the wiring is covered by unit tests since review 5, not by the oracle | an oracle tile with a `Patches/<tile>` folder |
| custom coastline / water | no fixture carries one; covered by unit tests only | a tile with a curated file, compared against Ortho4XP fed the same file |
| other knobs | `min_area`, `water_simplification`, `clean_bad_geometries` and `mesh_zl != 19` stay at their defaults in the oracle | parameter sweeps against Ortho4XP (cheap: stage 1 only) |
| `APRON` (marker 64) and four airport rules | no apron of +43+005 carries the `include` tag, none carries a `smoothing_pix` tag or a `custom` tag, no runway is a relation, no rejection path fires (`airports-geometry.md` 8) | the same second oracle tile |

Known limit inherited from Ortho4XP, documented by review 6 and left as is: **a runway across two
tiles steps at the seam.** Each tile fits the degree-7 altitude polynomial of the runway
(`AltitudeField`) on *its own* smoothed raster, and each raster is re-blended to raw at its own
border. Measured on a 45 m runway crossing lon 5.0, building `+43+005` and `+43+004` from the same
OSM and the same elevation field: 16 RUNWAY vertices on the seam on each side, 3 in common, **0.74
m** apart. Same arithmetic in Ortho4XP, so not a fidelity defect. A continuous seam would take, for
runway vertices at x in {0, 1} or y in {0, 1}, the raw raster altitude instead of the polynomial --
a wanted difference to measure before taking
(`tests/test_review6_robustesse_cas_limites.py::test_defect_a_runway_across_two_tiles_steps_at_the_seam`).

The coastline conventions themselves are covered *outside* the tile: review 5 compared 17
coastline configurations against `O4_Vector_Utils.coastline_to_MultiPolygon` run by Ortho4XP's own
interpreter, and the orthophoto grid bit for bit for `mesh_zl` 15 to 19 on three tiles
including a southern one.

## 9. Wanted differences

1. ~~The 10 pairs of Ortho4XP nodes 1e-9 apart that the noder merges~~ — **closed** by the
   `nodes10` chantier (`vectors-pslg.md` 2.8): the node and edge sets are now equal, with or
   without airports. What remains, and is genuinely not reachable, is Ortho4XP's *numbering*: it
   numbers a crossing when it creates it, walking the candidates in the order its
   libspatialindex R-tree returns them, so the mesh and the DSF stay equivalent and not
   byte-identical (`p4-airports.md` 3 isolates it: in canonical order both become
   byte-identical). `AssemblyParams.exact_grid_order` closes the last 3 nodes of 247 282 at
   twelve times the noding cost (1.6 times since review 6), and is off by default.
2. Patch priority: section 2.1, the source rather than the brief.
3. No `snap_to_grid` pass and no isolated vertices (vectors-pslg.md 2.12).
4. Seeds are keyed by attribute value instead of attribute name; the written order is the
   same because the values are the sort key in Ortho4XP too.
5. A way crossing the antimeridian is **folded**, not mirrored: `x = lon - tile.lon` is Ortho4XP's
   formula and it fabricates a coastline running the wrong way across tiles `±179`
   (`geom.wrap_local_x`, review 5). The fold is exact and is a no-op everywhere else.
6. The user's curated coastline and water files of Ortho4XP's `OSM_data/` folder are not read
   (section 7.1, decision 0010).
7. The artefact is written all-or-nothing (`.part` files renamed at the end) where Ortho4XP writes
   `.node` then `.poly` in place.

## 10. Not covered here

Coastline closure, water polygons, road buffering and banking, patches, the 7-decimal input
rounding (`vectors-pslg.md` 2.8) — the layer builders. Airports and the elevation smoothing
over them — wave 2. The `.ele` file of an `iterate` round — `mesh-build.md`.

## 11. Shared geometry helpers (`geom.py`)

`O4_Vector_Utils` keeps four small helpers that every builder needs; review 5 found
`ensure_MultiPolygon` (`:779-793`) transcribed three times and `cut_to_tile` (`:739-777`)
twice with two different signatures — agreeing, which is the state in which one of them
drifts. They now live once in `orthostudio.vectors.geom`, with the bodies P4 was measured with,
and `coast` / `water` / `roads` import them:

| helper | origin | shape |
|---|---|---|
| `cut_to_tile(geom, xmin=0, xmax=1, ymin=0, ymax=1, *, strictly_inside=False)` | `:739-777` | intersect with the window; `strictly_inside` also subtracts its four sides (a *line*), which is what lets the coastline see where a chain leaves the tile |
| `ensure_multilinestring` | `:795-811` | anything to a `MultiLineString`, other types dropped |
| `ensure_multipolygon` | `:779-791` | anything to a `MultiPolygon`, other types dropped; an empty part of a `MultiPolygon` is **kept** |
| `polygons_of` | same | the list form the water and road builders work with; an empty part is **dropped** — the only difference, which is why both exist |
| `wrap_local_x` | no origin: a wanted difference (section 9.5) | folds a tile-local abscissa by one turn of the globe, exactly, and only outside `[-180, 180]` |
