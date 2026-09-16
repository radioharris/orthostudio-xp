# Coastline and sea: from `natural=coastline` ways to SEA lines and sea seeds

Status: P4 wave 1 (`src/orthostudio/vectors/coast.py`).
Origin: Ortho4XP `src/O4_Vector_Map.py:363-451` (`include_sea`) and
`src/O4_Vector_Utils.py:848-999` (`coastline_to_MultiPolygon`, `bd_coord`, `bd_point`),
with `src/O4_OSM_Utils.py:587-640` (`OSM_to_MultiLineString`) and
`src/O4_Vector_Utils.py:739-776` (`cut_to_tile`) as the input conversion.
Acceptance: the SEA layer and the SEA seeds of tile +43+005 (Marseille), measured below in 7.

The noding of the resulting lines is `docs/specs/vectors-pslg.md`; what the mesh does with the
markers is `docs/specs/mesh-build.md`. Inland water (`WATER` / `SEA_EQUIV`) is
`include_water` and belongs to the *waterroads* builder: this spec only **records** its
thresholds (6) so the two builders agree on who owns which marker.

## 1. What the stage does

`include_sea` turns one OSM layer -- the ways carrying `natural=coastline` -- into two
things, and only two:

1. **a line layer**: the coastline geometry cut to the tile, inserted with marker `SEA`
   (bit 2) and a z sampled from the DEM (`tile.dem.alt_vec`), `O4_Vector_Map.py:407-413`;
2. **region seeds**: one point per connected sea polygon, marker `SEA`,
   `O4_Vector_Map.py:437-451`. Triangle4XP floods the `SEA` attribute from each seed until it
   meets an edge carrying the `SEA` bit, so the seeds alone decide which side of the coast is
   water. Getting them wrong flips a whole tile.

No polygon is ever *encoded* for the sea: the sea area exists only to place the seeds.
There is therefore **no `min_area` / `max_area` / `simplify` filter on the sea** -- those
belong to `include_water` (6). A sea polygon of 1 m² still gets a seed.

Coordinates are tile-local, `x = lon - tile.lon`, `y = lat - tile.lat`, the convention of
`vectors-pslg.md` 1.

## 2. Input conversion

### 2.1 Which ways (`O4_Vector_Map.py:364-401`)

`queries = ['way["natural"="coastline"]']`, `tags_of_interest = []`. When
`<tile>_custom_coastline.osm` (or a directory of them) exists, that file replaces the query
**and sets `custom_source = True`** (`O4_Vector_Map.py:369-389`), which changes rule 4.2.
**Keep**: `CoastParams.custom_source`. The curated file was read from review 5 until decision 0010,
which removed every reading of an Ortho4XP folder: a curated coastline has no source now
(`vectors-assembly.md` 7.1).

With a warm Ortho4XP cache the layer is re-read with
`input_tags = target_tags = {"w": [("natural", "coastline")]}` (`O4_OSM_Utils.py:402-425`), so
`dicosmfirst["w"]` holds exactly the ways that carry the tag. OrthoStudio XP takes the same set from
the OrthoStudio XP snapshot (`osm-source.md`) or from the re-read cache. **Keep.**

### 2.2 Way order (`O4_OSM_Utils.py:596`)

`for wayid in osm_layer.dicosmfirst["w"]` iterates a **Python `set` of the negative internal
ids** `-1, -2, ... -n` assigned in file order, so the way order is the hash order of that set,
not the file order. It is deterministic (int hashes are the ints, except `hash(-1) == -2`) and
it decides the insertion order of the `SEA` lines, hence which node wins z where two coastline
ways cross. **Keep** for parity, reproduced by `way_set_order(n)` (which literally builds
that set); `CoastParams.ways_in_set_order = False` keeps file order. Measured identical on the
reference tile: the 427 inserted ways come out in the same order as Ortho4XP (7).

### 2.3 Rounding and LineString construction (`O4_OSM_Utils.py:608-631`)

`numpy.round(nodes - [lon, lat], 7)`, then `geometry.LineString(way)`; a way that cannot
become a LineString (fewer than 2 points) is silently dropped (`except: pass`).
**Keep** both, including the 7 decimals (`vectors-pslg.md` 2.8 assigns the 7-decimal input
rounding to the layer builders, i.e. here).

Nodes are deduplicated by `(lon, lat)` when the cache is read (`O4_OSM_Utils.py:94-103`), so
two OSM nodes at the same coordinate become one; this can make a way *self-touching* or a
segment zero-length. The noder drops zero-length segments (`vectors-pslg.md` 2.12).
**Keep** (no geometric consequence).

### 2.4 `cut_to_tile` (`O4_Vector_Utils.py:739-776`)

* `cut_to_tile(g)`: `g ∩ [0,1]²`.
* `cut_to_tile(g, strictly_inside=True)`: `(g ∩ [0,1]²) \ ∂[0,1]²`, the difference removing
  only the parts *collinear* with a tile side (a difference with a zero-area line). A chain
  that merely ends on a side keeps that end point. **Keep**: this is what makes 4.1 work
  (open chains end exactly on the border) while a coastline running *along* the border is
  dropped.

## 3. The SEA line layer (`O4_Vector_Map.py:405-413`)

```
encode_MultiLineString(cut_to_tile(coastline, strictly_inside=True),
                       tile.dem.alt_vec, "SEA", check=True, refine=False)
```

`encode_MultiLineString` (`O4_Vector_Utils.py:437-462`) then applies `cut_to_tile` **again**,
non-strict, per line, and inserts each resulting part as one way. The second cut is an
identity on the reference tile (427 parts in, 427 ways out) but it is kept: it is the only
thing that flattens a `GeometryCollection` the first cut may return.

* `refine=False`: coastline segments are **not** densified. **Keep.**
* z: one `alt_vec` sample per vertex. The DEM is not this builder's business
  (`docs/specs/dem.md`); `build_sea_layers` returns the geometry and
  `CoastResult.to_layers(alt_vec)` attaches z. **Keep.**
* marker `SEA` = 2, never `SEA | WATER`: the 1 609 edges of the reference `.poly` carrying
  marker 3 are coastline edges that a `WATER` polygon later re-inserted, the OR of
  `vectors-pslg.md` 2.5. **Keep** (not this builder's doing).

## 4. Sea polygons (`coastline_to_MultiPolygon`, `O4_Vector_Utils.py:848-980`)

The input is rebuilt at `O4_Vector_Map.py:415-436` and is **not** the line layer of 3:

| | geometry used |
|---|---|
| rings (`line.is_ring` on the *uncut* coastline) | kept whole, **not cut** |
| non-rings | `cut_to_tile(..., strictly_inside=True)` then `ops.linemerge` |

`linemerge` is what turns the 267 clipped pieces of the reference tile into 10 chains; it is
applied to the non-rings only because it is expensive (comment, `O4_Vector_Map.py:415-417`).
**Keep**, including the asymmetry: a ring that leaves the tile stays a full ring and is cut
later, at 4.4, while an open chain is cut first.

### 4.1 Classification (`O4_Vector_Utils.py:885-921`)

OSM convention: **land on the left** of the way's direction. Hence, for a closed ring,

* `LinearRing(line).is_ccw` -> land inside -> **island** (line 887);
* otherwise -> water inside -> **interior sea** (line 890).

An open chain is a `segment [bd_coord(first), bd_coord(last), coords]` (line 914) and must
**reach the tile border**: `min(|x - int(x)|, |y - int(y)|) > 1e-5` on either end sets
`osm_error` (lines 893-913). That 1e-5 *is* the "nearly closed" tolerance -- an endpoint up to
1e-5 in local degrees (≈ 1.1 m in latitude) off a side is still accepted and later projected
onto the border by `bd_coord`.

The tolerance is **asymmetric**, and that is Ortho4XP's, not a transcription accident: `int()`
truncates towards zero, so `|x - int(x)|` is the distance to `x = 0` for every `x` in `[0, 1)`
and only becomes the distance to `x = 1` at `x >= 1`. Since `cut_to_tile` clips to `x <= 1`,
an endpoint is tolerated within 1e-5 of the **west or south** side but must be **exactly** on
the east or north one. The clip does produce exact 1.0 there, so real data passes; hand-edited
data 1e-6 short of the east side does not. **Keep** (`CoastParams.border_tolerance`), tested
both ways.

On `osm_error`, Ortho4XP logs the bad points and returns an **empty** MultiPolygon (lines 917-925):
the SEA lines stay in the graph but the tile gets **no sea seed at all**, so nothing floods and the
sea silently becomes land. OrthoStudio XP raises `OSM_COAST_OPEN_END` instead (registry decision of
P1: BLOCKING / STOP) -- **fix**; `CoastParams.on_bad_coastline = "record"` restores the Ortho4XP
degradation, and the error is then only recorded in `CoastResult.errors`.

### 4.2 `custom_source` (`O4_Vector_Utils.py:887`)

`if custom_source or is_ccw:` -- a user-supplied coastline has **every** ring treated as an
island, whatever its orientation, because hand-drawn data rarely respects the OSM winding.
**Keep.**

### 4.3 Closing the open chains along the border (`O4_Vector_Utils.py:850-877, 926-965`)

`bd_coord(pt)` is the arclength of the projection of `pt` on the closed polyline
`(0,0) → (0,1) → (1,1) → (1,0) → (0,0)`, i.e. on the tile border walked **clockwise** from the
south-west corner, in `[0, 4)` (`O4_Vector_Utils.py:982-989`). `bd_point(c)` is its inverse,
modulo 4 (lines 991-999). Walking the border clockwise keeps the tile interior on the right,
which is also the water side of a coastline way: chaining "way forward, then border forward"
therefore encloses the water.

```
bdcoords = sorted(ends + inits)
while bdcoords:
    start at bdcoords[0]
    if the current coord is an init: append that chain's points, jump to its end coord
    else: walk the border, emitting bd_point(k) for every integer k in (coord, next bdcoord]
    until we come back to the starting coord  ->  one closed polygon
    remove the init/end pair of every consumed chain from bdcoords
```

Details that matter, all **kept**:

* `inits.index(coord)` and `bdcoords.index(coord)` use exact float equality and return the
  **first** match: two chains starting at the same arclength are ambiguous and Ortho4XP picks the
  first. The same chain is then walked twice and the second `bdcoords.index` raises a bare
  `ValueError` that Ortho4XP does not catch, killing step 1. OrthoStudio XP turns it into
  `OSM_COAST_TRIPLE_JUNCTION` with the lat/lon of the junction -- **fix** (see 5).
* the border walk emits only the **corners** (`ceil(coord)`, then +1) between two endpoints;
  no other border vertex is added. The 2048-segment gluing border of `vectors-pslg.md` 2.9 is
  a different, later layer.
* a chain whose end arclength is exactly an integer (an endpoint **on a tile corner**) makes
  `ceil(coord) == coord`, so `bd_point` re-emits that corner: the polygon gets a duplicated
  point. `Polygon(...).buffer(0)` (line 967) absorbs it. **Keep**.
* the walk wraps with `next_coord + 4` when the next endpoint is `bdcoords[0]` (lines 869-872).
* `count == 1000` aborts the walk and returns an empty MultiPolygon -- a way oriented the wrong way
  makes the walk never close (lines 941-948). OrthoStudio XP raises `OSM_COAST_ORIENTATION`
  (**fix**, same `on_bad_coastline` switch). `CoastParams.max_border_walk = 1000`.
* **no open chain at all** (`if not bdpolys`, lines 957-958): the whole tile `[(0,0), (0,1),
  (1,1), (1,0)]` becomes the outer polygon. This is what makes an island in the open sea work.
  It also means a tile whose only coastline is an *interior sea* ring is turned inside out
  (the land becomes sea). **Keep** (parity); listed as a candidate fix in 8.

### 4.4 Islands and interior seas (`O4_Vector_Utils.py:966-980`)

```
outpol = unary_union([Polygon(p).buffer(0) for p in bdpolys])
inpol  = ensure_MultiPolygon(cut_to_tile(unary_union(
             [Polygon(r).buffer(0) for r in islands + interior_seas])))
sea    = ensure_MultiPolygon(outpol.symmetric_difference(inpol))
```

The symmetric difference does both jobs at once: an island inside `outpol` is removed, an
interior sea outside `outpol` is added. **Keep.**

Consequence, **kept for parity and flagged**: islands and interior seas are unioned
*together* before the symmetric difference, so an **island inside a lake** is swallowed by the
lake (`union(lake, island) == lake`) and comes out as water. See 8.

`buffer(0)` on each ring is what repairs a self-intersecting OSM ring. A ring with fewer than
4 coordinates would raise in `Polygon()`, which Ortho4XP does not catch; it cannot happen, because
shapely's `is_ring` means closed **and simple**, so a self-retracing closed line such as
`[(a), (b), (a)]` is *not* a ring and goes to the open-chain branch in both Ortho4XP and
OrthoStudio XP (where its ends are then judged against the border, 4.1). OrthoStudio XP keeps the
`len < 4` guard as dead-man's protection and drops the ring instead of raising -- **fix**,
defensive only.

## 5. Failure modes, one by one

| case | Ortho4XP | OrthoStudio XP |
|---|---|---|
| open chain ending inside the tile | `osm_error`, empty MultiPolygon, no seed (`O4_Vector_Utils.py:917-925`) | `OSM_COAST_OPEN_END` raised (or legacy behaviour + `errors`) |
| way oriented the wrong way (walk never closes) | 1000 steps then empty MultiPolygon (lines 941-948) | `OSM_COAST_ORIENTATION` |
| three chains meeting at one border point | `bdcoords.remove` raises, empty MultiPolygon (lines 950-965) | `OSM_COAST_TRIPLE_JUNCTION`, with the lat/lon Ortho4XP prints |
| chain ending exactly on a corner | duplicate corner point, absorbed by `buffer(0)` | same |
| self-retracing closed line | not a ring (not simple): open chain, so `osm_error` | same classification, then `OSM_COAST_OPEN_END` |
| ring with < 4 coordinates | would raise in `Polygon()`; unreachable | dropped (guard) |
| two chains sharing an init arclength | uncaught `ValueError`, step 1 dies | `OSM_COAST_TRIPLE_JUNCTION` |
| way with < 2 points | dropped silently (`O4_OSM_Utils.py:629-630`) | dropped, counted in `stats.ways_dropped` |
| no coastline way at all | `include_sea` returns before any of this (`O4_Vector_Map.py:403`) | empty `CoastResult`, no seed |
| only rings, no open chain | whole tile is sea, minus the islands | same |
| only an interior-sea ring | tile turned inside out | same (parity), warned in `stats` |
| island inside a lake | island swallowed by the lake | same (parity), counted in `stats.islands_swallowed` |

The last two are the only *semantic* Ortho4XP behaviours OrthoStudio XP reproduces while believing
them wrong; both are counted so the decision report can show them.

## 6. Recorded, not implemented here: WATER and SEA_EQUIV

`include_water` (`O4_Vector_Map.py:454-590`) is the *waterroads* builder's. Recorded here so
the two agree on the markers:

* queries: `rel|way["natural"="water"]`, `rel|way["waterway"="riverbank"]`,
  `way["waterway"="dock"]`; `tags_of_interest = ["name"]` (lines 528-541).
* `large_lake_threshold = tile.max_area * 1e6 / (lat_to_m * lon_to_m(tile.lat + 0.5))`
  (lines 455-457), i.e. `max_area` km² expressed in local square degrees; `max_area` defaults
  to 200 km² (`config/models.py:163`).
* a polygon with `pol.area >= large_lake_threshold` becomes **`SEA_EQUIV`** (bit 4), the
  others **`WATER`** (bit 1) -- `filter_large_lakes`, lines 459-500. A named polygon whose
  name is in `good_imagery_list` stays `WATER`; that list is the empty tuple in Ortho4XP
  (`O4_Vector_Map.py:16`), so the branch never fires.
* both classes are encoded with `area_limit = tile.min_area / 10000` and
  `simplify = tile.water_simplification * m_to_lat` (lines 550-589). Note that
  `min_area / 10000` is Ortho4XP's rough km² -> square-degree conversion, not an exact one.
* consumption: the mesh treats `SEA_EQUIV` exactly like `WATER` for smoothing
  (`O4_Mesh_Utils.py:262-265`) -- the difference is the water mask, which is why the log says
  "will be masked like the sea".
* `SEA` and `SEA_EQUIV` never come from the same layer: the coastline builder emits only
  `SEA`, the water builder only `WATER` and `SEA_EQUIV`. `CoastResult.sea_equiv` is an
  **injected pass-through** (empty in wave 1) so the assembler has one object carrying every
  sea-class polygon, exactly as airport layers are injected (plan arbitration A2).

## 7. OrthoStudio XP contract

```python
build_sea_layers(osm_data, tile, params=CoastParams(), *, sea_equiv=None) -> CoastResult
```

* `osm_data`: the `natural=coastline` layer, in any of four shapes.
  * **the integration path**: `osmdata.load(cache_or_snapshot, layer="coastline",
    tile=tile).ways_with()` -- `WayGeometry` objects whose `coords` are already tile-local,
    already rounded to 7 decimals and already in Ortho4XP's way order, so neither the origin
    shift nor `way_set_order` is applied again. Proved identical to the direct read in
    `tests/test_vectors_coast_oracle.py` (427/427 ways, seeds to 0.0).
  * an `OsmWaysSource` (`orthostudio.sources.osm.OsmSnapshot` satisfies the protocol: `nodes` with
    `.id/.lat/.lon`, `ways` with `.id/.nodes/.tags`) -- WGS84, converted here.
  * a sequence of WGS84 `(lon, lat)` arrays -- same.
  * a shapely geometry already in tile-local coordinates -- used as is.

  Passing an `osmdata.OsmData` *store* raises a `TypeError` naming `ways_with()`: its `nodes`
  and `ways` are the dictionaries of `OSM_layer`, not sequences, and reading them as a
  snapshot would silently give nonsense.
* `sea_equiv`: the large-lake MultiPolygon of the water builder, carried through untouched
  (6). Empty in wave 1, exactly as the airport layers are injected empty (arbitration A2).
* `CoastResult` carries `sea_lines` (the SEA linework), `sea_polygons`, `sea_equiv`,
  `islands`, `interior_seas`, `open_chains_closed`, `seeds` (S, 2), `stats` and `errors`.
  `result.to_layers(alt_vec)` is the `node_layers` layer list -- `[(sea_lines, SEA, z)]` with
  `z = alt_vec(coordinates)`; `result.seed_records()` is the `(x, y, marker)` list
  `write_poly_file` wants.
* `coastline_to_multipolygon(coastline, tile, params) -> SeaArea` is the reconstruction of 4
  on its own, for a caller that already has the linework.
* Determinism: for a given input the output is bit-for-bit reproducible, way order included
  (2.2).

## 8. Acceptance, measured on +43+005

Reference: the layers Ortho4XP actually inserted, recorded in
`fixtures/large/oracle/+43+005_zl14_BI/noding/layers+43+005.npz` (runs of equal marker; the
`SEA` run is 427 ways), and the seeds of
`fixtures/large/oracle/+43+005_zl14_BI/build/Data+43+005.poly` (2 615 seeds, 5 of marker 2).
Input: the warm Ortho4XP cache `+43+005_coastline.osm.bz2` (39 545 nodes, 424 tagged ways).
No network.

| artefact | target | measured |
|---|---|---|
| SEA ways | 427, same coordinates | 427 / 427 identical as a set **and in the same order**, exact float equality (`==` on the raw doubles, not a tolerance) |
| SEA seeds | 5, at 1e-9 | 5 / 5, max deviation **2.24e-16** -- exact at the 15 decimals Ortho4XP writes |
| SEA edges of the `.poly` | every marker-2 segment on one of our lines | 38 936 segments (37 327 `SEA` + 1 609 `SEA\|WATER`), 116 808 probes (both ends + midpoint), max distance to our linework **6.27e-10** |
| our lines covered by those edges | equal total length | 4.803909646074406 vs 4.803909646057731, relative gap **3.5e-12** (the noder's 9-decimal snap) |
| topology | 5 sea patches, 5 border polygons | identical; 160 OSM rings + 5 more closed by `linemerge`, 165 islands, 0 interior seas, 5 open chains |
| sea area | -- | 0.217159614 square degrees, identical to Ortho4XP's algorithm re-run on the same input (delta 0.0) |

The `.poly` edge check is the *indirect* one asked for by the plan: the noder splits the 427
ways into 38 936 edges, so equality is stated as "every marker-2 edge of Ortho4XP lies on one of
our lines, and every one of our lines is covered by marker-2 edges". The 6.27e-10 is the
9-decimal snap of `snap_to_grid` (`vectors-pslg.md` 2.8), i.e. at most 5e-10 per coordinate,
not a disagreement about geometry.

### 8.1 Cost

The stage is GEOS-bound, not Python-bound: on the reference tile `build_sea_layers` takes
**0.153 s** against **0.172 s** for the same algorithm written the Ortho4XP way (x1.12, M4 Pro,
`nice -n 10`, load 2.5, best of 5). Two thirds of it are three GEOS calls that both versions
make identically -- the two `cut_to_tile` of the whole coastline, `line_merge`, the
`union_all` of 165 island rings and the final `symmetric_difference`. What vectorising buys
(`bd_coord` / `bd_point` over arrays, `intersection` over the line array, one `buffer` call
for all rings, numpy concatenation of the chains instead of Python list `+=`, the island and
lake unions computed once instead of twice) is the remaining third. **There is no x10 to win
here and none is claimed**: step 1's 23 s were in `insert_way`, which is the noder's
(`vectors-pslg.md`, measured x20), not in the coastline reconstruction.

## 9. Wanted differences and candidate fixes

**Wanted differences (implemented):**

1. bad coastline data raises a typed `OsxpError` instead of silently producing a sea-free tile
   (4.1, 4.3); `on_bad_coastline="record"` restores Ortho4XP.
2. degenerate rings are dropped instead of raising (4.4), and the `ValueError` Ortho4XP lets
   escape when two chains share an init arclength becomes `OSM_COAST_TRIPLE_JUNCTION` (4.3).
3. `bd_coord` / `bd_point` are vectorised (`shapely.line_locate_point` /
   `line_interpolate_point` over arrays), the per-line second cut is one vectorised
   `intersection`, the rings are buffered in one call, the island and lake unions are computed
   once instead of twice, and the chains are concatenated with numpy instead of Python list
   `+=`. Same values, no semantic change (8.1).

**Candidate fixes, NOT implemented (parity first):**

4. island inside a lake is swallowed (4.4). Fix: symmetric-difference the islands and the
   interior seas separately, innermost first. Needs a real tile that exhibits it.
5. a tile whose only coastline is an interior-sea ring comes out inverted (4.3). Fix: when
   there is no open chain, take the tile as sea only if at least one ring is an island.
6. the first-match float equality of `inits.index` (4.3) picks arbitrarily between two chains
   starting at the same arclength. Fix: pair them by proximity of the chain ends.
