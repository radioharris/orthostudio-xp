# Inland water, roads and patches: the layers of `include_water`, `include_roads`, `include_patches`

Status: shipped (`src/orthostudio/vectors/water.py`, `roads.py`, `patches.py`); written for P4
wave 1.
Origin: Ortho4XP `src/O4_Vector_Map.py:228-355` (`include_roads`), `:453-589`
(`include_water`), `:639-968` (`include_patches`, `keep_obj8`), and the helpers of
`src/O4_Vector_Utils.py` they call: `MultiPolygon_to_Indexed_Polygons:659-736`,
`encode_MultiPolygon:365-436`, `cut_to_tile:739-777`, `improved_buffer:1021-1069`,
`weighted_normals:1071-1094`, `shift_way:1096-1098`, `refine_way:1114-1141`,
`length_in_meters:1002-1011`.

Acceptance: PLAN level B (set equivalence) against the recorded layers of the reference tile
`+43+005` and against its `.poly` seeds; measured in section 8. The OSM store these builders
read is `docs/specs/vectors-osm-layers.md`; what they feed is `docs/specs/vectors-pslg.md`
(`node_layers`) and, past Triangle4XP, `docs/specs/mesh-build.md`.

## 1. What the three builders do

Step 1 of Ortho4XP inserts its layers in decreasing priority (`vectors-pslg.md` 2.6): airports and
patches, roads, coastline, inland water, orthophoto grid, gluing border. This spec covers
three of them; every builder returns *layers ready for* `node_layers`, never a vector map, so
that the assembler decides the order and the noder stays the only writer of the graph.

| builder | marker(s) | geometry | seeds |
|---|---|---|---|
| `build_water_layers` | `WATER` then `SEA_EQUIV` | polygons, merged, cut to the tile | one per polygon |
| `build_road_layers` | `INTERP_ALT` | buffered polygons around banked roads | one per polygon |
| `build_patch_layers` | `INTERP_ALT`, `DUMMY` | patch ways and OBJ8 triangles | one per closed patch, one per triangle |

Wave 1 does **not** port the airports (`O4_Airport_Utils.py`); where Ortho4XP passes airport data
to the road builder, OrthoStudio XP takes an optional injected input (arbitration A2, section 5.1).

## 2. Rules of the water builder

Each rule: what Ortho4XP does, where, and the OrthoStudio XP decision (**keep** / **fix** = wanted
difference / **drop**).

### 2.1 What counts as inland water (`:525-546`)

Selectors `rel["natural"="water"]`, `rel["waterway"="riverbank"]`, `way["natural"="water"]`,
`way["waterway"="riverbank"]`, `way["waterway"="dock"]`, tag of interest `name`. **Keep**;
they live once in `orthostudio.sources.osm.LAYERS["water"]` and the selection itself is
`OsmData.multipolygons_with()` (`vectors-osm-layers.md` 5): first-catch ways that are closed
and valid, then relations as `union(outer) - union(inner)`. A user file
(`custom_water(.osm)` / `custom_water_dir`) replaces the query and keeps every tag
(`:509-522`). OrthoStudio XP read it from review 5 until decision 0010; a build reads no Ortho4XP
folder now, so it has no source.

**Dry rainwater basins are left out** (`is_dry_basin`, wanted difference 10): a way or relation
tagged `water=basin` and `intermittent=yes` is dropped before the areas are summed, and
`counts["dry_basins"]` says how many. For it the layer keeps two more tags of interest, `water`
and `intermittent`, beside `name`. A basin that is not intermittent (a treatment basin) and an
intermittent lake or pond stay water.

### 2.2 Lakes larger than `max_area` (`filter_large_lakes`, `:456-500`)

`large_lake_threshold = max_area * 1e6 / (lat_to_m * lon_to_m(lat + 0.5))` — square degrees of
the tile-local frame. An element whose area reaches it becomes `SEA_EQUIV` (masked like the
sea) unless its `name` is in `good_imagery_list`, a tuple that is **empty** in Ortho4XP
(`O4_Vector_Map.py:16`). **Keep**, with the list exposed as `WaterParams.good_imagery_list`,
empty by default, and every decision reported (`LakeDecision`, error code
`OSM_LAKE_TREATED_AS_SEA`). No setting reaches that list: the rule's parameters do not carry it
and `layers.build_layers` builds `WaterParams` without it, so a build keeps it empty.

The threshold is applied to the **element**, not to its parts: a relation whose polygons sum
past the threshold goes to the sea in one piece (`multipol.area` at `:733`, before the
explosion at `:737-756`). **Keep**: the parts of one relation are grouped by
`(kind, osm_id)` and the sum is tested once.

### 2.3 Merging overlapping polygons (`MultiPolygon_to_Indexed_Polygons:659-736`) — the point

Ortho4XP walks the polygons sorted by **bounding-box area, largest first**, keeps an rtree of what
it has accepted so far and, for each new polygon, computes `pol.intersection(other).area` for
every candidate whose box overlaps; the candidates with a positive intersection area are
deleted, `unary_union`-ed with the new polygon, and the parts of the result are re-inserted
with new ids. An invalid or zero-area polygon is dropped with a log line. Whether the merge
happens at all is the `clean_bad_geometries` switch.

**Keep the result, fix the algorithm.** OrthoStudio XP builds the overlap graph in one pass —
`STRtree.query(..., predicate="intersects")` for the candidate pairs, then
`shapely.relate_pattern(a, b, "T********")` to keep only the pairs whose *interiors* meet,
which is exactly "the intersection has an area" for polygons — labels the connected components
(`scipy.sparse.csgraph`) and calls `union_all` once per component.

Why the result is the same: the incremental merge is a union-find over the relation "the two
polygons overlap with positive area". The relation is stable under union (the union of a group
covers exactly the area of its members, so a polygon overlapping the union overlaps a member),
so Ortho4XP's fixpoint is the connected component. The final *order* of the polygons is
reproduced too: a component takes the rank of its **last** member in the sorted walk, because
every new member deletes the group's ids and appends the merged parts at the end of the
dictionary; a polygon that never merges keeps its own rank. Within a component the order is
the one `union_all` gives, which is not necessarily the one the incremental `unary_union`
gave: a wanted difference with no effect on the set of ways (section 7.1).

`merge_overlappings=False` (`clean_bad_geometries=False`) keeps every polygon as it comes,
invalid ones included (`add_pol`, `:693-696`). **Keep**.

### 2.4 Encoding a multipolygon (`encode_MultiPolygon:365-436`)

For each polygon of the collection, in order:

1. `cut_to_tile` — intersection with `[0, 1]^2` (`:739-777`), unless `cut=False`;
2. `simplify(water_simplification * m_to_lat)` when that value is non-zero (`0` is the
   default and is falsy, so no simplification at all);
3. explode to polygons, drop those whose `area <= area_limit` (`min_area / 10000` for water:
   `min_area` is in km² but the comparison is in square degrees, so the threshold is
   `min_area / 10000` deg² ≈ `min_area` km² at 45°) — **keep, including the unit slip**;
4. `shapely.geometry.polygon.orient` (exterior counter-clockwise, holes clockwise), the
   comment at `:395` says it matters for `pol_to_alt`;
5. `refine_way(way, refine)` when a refinement length is given (roads: 100 m);
6. `z = pol_to_alt(way)` on the exterior ring, then on each interior ring;
7. one seed per polygon at `representative_point()`, keyed by the marker name.

**Keep** all of it (`encode_multipolygon` in `water.py`, shared with the road builder). The
ring order — exterior, then interiors, polygon after polygon — is the order
`shapely.get_coordinates` gives for a `MultiPolygon`, which is the order `node_layers` wants
for its `z` array, so a layer is one `MultiPolygon` and one `z` vector, not 1 650 separate
insertions.

### 2.5 Altitudes (`:558`, `:581`)

Water rings take `tile.dem.alt_vec`, point by point. **Keep**. Since `alt_vec` is pointwise,
sampling the whole layer at once is identical to sampling way by way. `dem=None` gives a
zero `z` and is only for tests and for callers that fill `z` themselves.

### 2.6 Two layers, in this order (`:554-587`)

`WATER` first, then `SEA_EQUIV`. **Keep**: `WaterResult.layers` is `(water, sea_equiv)`,
empty layers omitted.

## 3. Rules of the road builder

### 3.1 Which roads (`:253-320`)

`road_level = 0` disables the whole stage (`:253-254`). Level 1 uses the `big_roads` layer
(motorway, trunk, primary, secondary, `railway=rail`, `railway=narrow_gauge`); levels 2 to 5
add the `small_roads` layer with tertiary, then unclassified and residential, then service,
then track. **Keep**, and note where the split lives: choosing the selectors belongs to the
OSM fetch (`orthostudio.sources.osm.layers_for(road_level)`), so `build_road_layers` receives the
stores it must read, in Ortho4XP's order (`big_roads` then `small_roads`), and only checks
`road_level == 0`.

Ways tagged `bridge` or `tunnel` are skipped (`tags_for_exclusion`, `:257-259` and
`O4_OSM_Utils.py:596-603`). **Keep** (`OsmData.ways_with(exclude=ROAD_EXCLUSION_TAGS)`).

### 3.2 Which roads are levelled (`road_is_too_much_banked`, `:229-249`)

A way is levelled when

1. its **first or last** point falls inside `apt_array` — a 1001×1001 boolean raster of the
   airport neighbourhoods, indexed `[1000 - round(y*1000), round(x*1000)]` clamped to
   `[0, 1000]` (`O4_Airport_Utils.py:910-921`) — the road must be levelled where it meets a
   flattened airport; **or**
2. the DEM altitude along the way and along the way shifted by `lane_width` metres to its
   left differ by at least `road_banking_limit` anywhere:
   `(|alt_vec(way) - alt_vec(shift_way(way, lane_width))| >= road_banking_limit).any()`.

Test 2 is skipped (the way is *not* levelled) once the number of vertices already accepted
reaches `max_levelled_segs` (`:242-243`); the counter is fed with `len(way)` per accepted way
and **restarts at zero for the second OSM layer**, because it is local to each
`OSM_to_MultiLineString` call (`O4_OSM_Utils.py:594`, `:626`). **Keep**, counter included,
and note that the ways already accepted keep their place: the cap is a budget spent in
iteration order, which makes the result order-dependent (section 7.3).

`shift_way(way, shift) = way + shift * m_to_lat * weighted_normals(way, "left")`
(`:1096-1098`); `weighted_normals` is the mean of the two adjacent segment normals,
normalised in the anisotropic metric `scalx = cos((lat + 0.5) * pi / 180)`, with the
`1e-6` guards of `:1071-1094` and the closed-way special case. **Keep**, vectorised.

### 3.3 Buffering (`:325-341`)

```
road_area = improved_buffer(banked.difference(improved_buffer(apt_area, lane_width + 2, 0, 0)),
                            lane_width, 2, 0.5)
```

`improved_buffer(geom, bw, sw, sl)` (`:1021-1069`): metres to degrees, `x` scaled by `scalx`,
`buffer(bw + sw, join_style=2, mitre_limit=1.5, resolution=1)`, `buffer(-sw, ...)`,
`simplify(sl)` when non-zero, `x` unscaled. Growing then shrinking is what closes the small
holes between nearby roads. **Keep** exactly, arguments included (`2` m of separation,
`0.5` m of simplification), `resolution=1` included: `quad_segs=1`, the angular ends of Ortho4XP
1.40, unless `rounded_road_ends` asks for the round ends of Ortho4XP 1.30 (3.7; section 7,
difference 9).

The airport area subtracted is the `treated_area` of `include_airports` (runways, taxiways,
aprons and patches), buffered by `lane_width + 2` metres: a road inside an airport is already
flattened by the airport, and a second constraint there would fight it. In wave 1 the input is
empty unless the caller injects it (section 5.1). **Keep as an injected input.**

The `.difference(...)` is called **unconditionally**, empty airport area included, and that is
not a detail: GEOS re-nodes the linework of the left operand, so `difference(POLYGON EMPTY)`
turns the 2 783 banked ways of +43+005 into 2 936 line strings and moves the buffered outline
by 8e-9 deg². Skipping it "because it is a no-op" cost, against the Ortho4XP airport-free
reference, 60 nodes in excess and 110 missing, 113 + 163 uncommon edges, 79 nodes whose
`INTERP_ALT` altitude was off (up to 6 cm) and 2 of the 911 road seeds displaced by 1.9e-6
(P4 integration, `docs/benchmarks/p4-vectors.md` 2). **Never short-circuit it.**

### 3.4 Encoding (`:344-346`)

`encode_MultiPolygon(road_area, alt_vec_shift, "INTERP_ALT", check=True, refine=100)`:
the polygon boundary is refined every 100 m and each of its points takes the DEM altitude
**`lane_width` metres to the left of the boundary**, i.e. inside the road, not on its edge
(`alt_vec_shift`, `:251-252`). That is what makes the levelled surface flat-ish instead of
following the cut slope. **Keep**, the shift included.

### 3.5 The flat network (`:348-362`)

The branch that would insert the non-banked roads as `DUMMY` lines is `if False and ...`
since 23/02/2024 and the roads rejected by the banking test are dropped. **Drop** (the
rejected ways are counted and reported, not inserted).

### 3.6 Smoothing along the outline (OrthoStudio XP's own, off by default)

`road_smoothing` (`expert.road_smoothing`, 0, 100 or 250 m, 0 by default) replaces
`alt_vec_shift` in 3.4 by `smooth_along(ring, alt_vec_shift(ring), road_smoothing)`: each ring of
the encoded road area, already refined every 100 m, is sampled every 2 m of its length
(`SMOOTH_STEP_M`, the last stretch evened out so the samples are equally spaced), each sample
takes the mean of the samples within half the length on either side, running on past the start
of the ring, and each point of the ring takes the value at its place. A ring shorter than the
length takes its mean all round. The points do not move: the road area, the seeds, the triangles
and the counts are those of 3.3 and 3.4, only the altitudes Triangle4XP interpolates between change.

Why: the levelled roads follow the relief's small waves, 30 m apart on a 30 m relief, and
X-Plane's roads, draped on the mesh, bump over them; Ortho4XP 1.30 on the same relief bumps the same
(KSME, `+37-085`, 2026-10-26). Measured there on a 1" relief, road_level 1, banking limit 0,
lane width 9 m, against the same tile unsmoothed: same points and triangles, same build time, only
the 129 111 vertices of the levelled roads and airports moved, the 1 133 075 others identical.
Road points moved, median / 90 % / most: 100 m 0.26 / 0.91 / 9.6 m; 250 m 0.63 / 2.21 / 21.7 m.
This code at 100 m on the same square and relief: 0.27 / 0.93 / 9.61 m, the 128 851 points of the
flattened triangles alone; of the 123 points moved by more than 5 m, 66 lie within 150 m of a
bridge or a tunnel, where the smoothed height falls between the valley or the hill and the track.
50 m moved almost nothing (median 0.13 m) and 500 m planed real slopes (993 points by more than
10 m): the page offers off, 100 m and 250 m (chosen on 2026-10-26). The largest moves are
probably at interchanges, where one outline holds roads at two heights.

0 leaves the key of `orthostudio.vectors` as it was (`VectorsParams.canonical` drops the field),
so no tile built before is built again; any other length is in the key, and a value the page
does not offer is refused (`CFG_VALUE_INVALID`).

### 3.7 Round or angular ends (OrthoStudio XP's own, angular by default)

`rounded_road_ends` (`expert.rounded_road_ends`, off by default) draws the road bands of 3.3 and
the taxiway bands (`airports-geometry.md`) with `quad_segs=8`, the round ends and corners of
Ortho4XP 1.30 (difference 9); off, `quad_segs=1`, the angular ones of Ortho4XP 1.40 and of
OrthoStudio XP up to 1.1.0 (`improved_buffer(..., rounded=...)`). The other bands buffer polygons,
whose mitred corners `quad_segs` does not touch.

Why an option, and off: round ends take the bumps out of the ground across the roads at a
crossroad (KSME, 2026-10-24), and OrthoStudio XP 1.2.0 drew them always; but every dead end of a
dense network gets one, and the one buffer of the whole levelled network grows faster than the
network. Measured on 1.2.0 against 1.1.0, the same tile, its OSM and relief, the tracing alone on
the Mac: Atlanta (`+33-085`) at the main roads 18.3 s and 2.5 GB against 16.5 s and 2.3 GB; a
country tile (`+45+002`) at the service roads 71 s and 5.9 GB against 50 s and 4.2 GB; Atlanta at
the service roads with two million points to level 25 min 38 s and 23.9 GB against 11 min 46 s and 14.5 GB,
which three tiles of a batch at once took past a user's 64 GB. Nobody reported the bumps of the
angular ends, which Ortho4XP 1.40 draws too.

The shape is the rule's version, never a field of the key: angular ends are `orthostudio.vectors`
at version 12 (`VECTORS_ANGULAR`), what 1.1.0 ran, and round ends at 13 (`VECTORS`), what 1.2.0
ran; the tile's setting picks the rule (`vectors_rule`) and the rule that runs tells the builders
(`LayerRequest.rounded_road_ends`). Nothing else of the tracing changed between 1.1.0 and 1.2.0,
and the parameters of the key are the same, so a tile 1.1.0 traced is found as it is with the box
off, and one 1.2.0 traced with the box on; each is traced again only with the other shape.

## 4. Rules of the patch builder (`:639-968`)

The terrain step says each phase as it starts, so a long one is never silent: "flattening the
airports into the relief", "the patch <name>" for every patch file or folder as its reading
starts (`build_patch_layers(..., on_file=)`), then "the runways, taxiways and aprons". A large
patch with a large airport on a 1 m relief of one's own took twelve silent minutes on a user's
tile (EDDM, 2026-10-18).

Patches are `<tile>/*.patch.osm` files (JOSM XML, read with every tag kept; Ortho4XP holds them
under `Patches/<10° cell>/<tile>`, and `pipeline.build.patches_folder` reads either tree, or the
tile's own directory named as the folder) and
directories of OBJ8 files. A file none of whose ways could be used is reported as
`OSM_PATCH_INVALID` ("no way of this file could be used") unless it already said what it refused,
and the stage logs what it did use (`N patch polygon(s), M line(s)...`): a patch that changed
nothing used to be silent, and read as a feature that did not work (2026-09-17). Ortho4XP calls them from `include_airports`, before the runways, so
that a patched airport is skipped by the airport builder (`patches_list`, `:1045-1046`).
Wave 1 delivered the reader and the layers; the build wires them since 2026-09-17, from the
folder Settings names (`expert.patches_dir`, `pipeline-build.md`), not from Ortho4XP's own.

0. **Coordinates** (`:681-684`): a patch way is *not* rounded to 7 decimals, unlike every
   other OSM way (`vectors-osm-layers.md` 5); the reader subtracts the tile origin and keeps
   every digit JOSM wrote. **Keep** (`_way_coords`, which bypasses `OsmData.node_coords`).
1. **Way order** (`:673-676`): the ways carrying at least one tag first, then the untagged
   ones, "due to altitude being first done kept for all". Ortho4XP iterates a `set`; OrthoStudio XP
   iterates in reading order inside each of the two groups (section 7.4).
2. **Altitude of a way** (`:685-717`, first match wins): `cst_alt_abs` = constant absolute;
   `cst_alt_rel` = mean DEM altitude of the way plus the value; `var_alt_rel` = DEM altitude
   plus the value; `altitude` (deprecated) = constant, or the mean DEM altitude when the value
   does not parse; `altitude_high`/`altitude_low` = the ramp of rule 4; nothing = the DEM.
3. **Altitude of a node** (`:783-794`): `alt_abs` overrides that node's altitude, `alt_rel`
   offsets it from the DEM. Not applied to a ramp way (`cplx_way`).
4. **Ramps** (`:711-770`): a closed way of exactly 5 points (4 corners); `short_high` is the
   last two points, `short_low` the second and third; `cell_size` (10 m), `profile`
   (`plane`, `spline` = `3x²-2x³`, `tanh` with `steepness` = 2) and the two altitudes give
   `cuts_long = int(length / cell_size)` cuts along the ramp; the way is rebuilt with
   `cuts_long + 1` points per long side and the altitudes follow the profile. The
   `cuts_long - 1` **cross bars** are then inserted as `DUMMY` edges (`:806-813`), so that the
   ramp triangulates in strips. **Keep**, including the length formula
   `sqrt((dx·cos(lat))² + dy²) · 111120` — note `cos(lat)`, not `cos(lat + 0.5)` as elsewhere.
5. **Closed vs open** (`:795-822`): a closed way becomes an `INTERP_ALT` polygon with a seed
   at its representative point, an open way a `DUMMY` line. An invalid or zero-area closed
   way is skipped.
6. **OBJ8** (`:875-968`): the first line of the file must contain `ANCHOR` followed by
   `lon lat alt heading` (or `lon lat heading`, the altitude then read from the DEM); `VT`
   lines give vertices rotated by the heading and placed around the anchor,
   `x = round(lon_anchor + lonscale·X - tile.lon, 7)`, `y = round(lat_anchor - latscale·Z -
   tile.lat, 7)`, `z = y_obj + alt_anchor`, with `latscale = m_to_lat` and
   `lonscale = latscale / cos(lat_anchor)`; `IDX` lines give the index arrays and `TRIS
   offset count` the triangles. Degenerate triangles (two equal vertices) are skipped; each
   triangle contributes its three edges as `INTERP_ALT` and a seed at its centroid.
   **Keep**; the union of the triangles is the object's contribution to `patches_area`.
7. `patches_area` (the union of the closed patches and of the OBJ8 triangles) and
   `patches_list` (one entry per `.patch.osm` file and per OBJ8 directory) are returned for
   wave 2. **Keep**.

## 5. OrthoStudio XP contract

```python
# water.py
build_water_layers(osm_data, tile, params=WaterParams(), dem=None, *, on_event=None, dry=None)
    -> WaterResult(layers, seeds, water, sea_equiv, lakes, counts, dry)
    # dry: DryAerodromes(outlines, surfaces), tile-local; the pieces of water inside the outlines
    # that the surfaces touch are taken out of the merged polygons before they are encoded and
    # seeded (water.without, aerodrome-dry.md); WaterResult.dry is what was taken, counts["dry"]
    # its pieces, only when there are some
merge_overlapping_polygons(polygons, *, merge=True, on_event=None) -> list[Polygon]
encode_multipolygon(polygons, *, pol_to_alt, area_limit=1e-10, simplify=0.0, refine=0.0,
                    scalx=1.0, cut=True) -> EncodedLayer(geometry, z, seeds, dropped)
EncodedLayer.layer(marker) -> (geometry, bits, z)
refine_way / scale_x / lon_to_m      # cut_to_tile is re-exported from orthostudio.vectors.geom

# roads.py
build_road_layers(osm_data, tile, dem, params=RoadParams(), airports=None, *, on_event=None)
    -> RoadResult(layers, seeds, area, counts)
AirportAreas(array, area)          # the wave-2 injection point (A2)
weighted_normals / shift_way / refine_way / improved_buffer / length_in_meters / smooth_along

# patches.py
build_patch_layers(directory, tile, dem=None, *, on_event=None)
    -> PatchResult(layers, seeds, area, names, counts)
    # `directory` IS `Patches/<tile>`; a directory that does not exist gives an empty result
    # *and* an `OSM_PATCH_INVALID` event, so it is never mistaken for an empty one (review 5)
read_patch_file(path, tile, dem, *, runs=None, on_event=None) -> (runs, seeds, area, counts)
read_obj8(path, tile, dem=None) -> Obj8(triangles, altitudes, seeds, area) | None
tanh_profile / spline_profile / plane_profile
```

`osm_data` is an :class:`orthostudio.vectors.osmdata.OsmData` store (`vectors-osm-layers.md`), the
one place that reads an OrthoStudio XP snapshot or a patch file. The road builder takes one
store or the pair ``(big_roads, small_roads)``.

* `layers` is a tuple of `node_layers` layers: `(geometry, marker, z)` with `z` aligned to
  `shapely.get_coordinates(geometry)`; the assembler concatenates the builders' layers in the
  priority order of `vectors-pslg.md` 2.6 and calls `node_layers` once.
* `seeds` maps a marker **name** to an `(S, 2)` array, which is what
  `triangle_files.write_poly_file` takes.
* `scalx = cos((lat + 0.5)·π/180)` (`scale_x(tile)`) is passed explicitly wherever a metric is
  needed; the builders never touch a module-level global, unlike `O4_Vector_Utils.scalx`, so
  two tiles may be built at the same time.
* Errors are reported through `on_event(OsxpError)` and never raised for one bad feature: a
  single invalid way must not lose a tile (`errors.md`).

### 5.1 Airports as an injected input (arbitration A2)

`build_road_layers(..., airports=AirportAreas(array=..., area=...))`. `array` is the
1001×1001 boolean raster of rule 3.2.1 and `area` the polygon subtracted in rule 3.3.
`airports=None` means "no airport in this build" — an all-false raster and an empty area,
which is exactly what Ortho4XP computes for a tile without aerodromes. Wave 2 fills it from
`O4_Airport_Utils`' successor (`stage.layers.airport_areas()` of `orthostudio.airports_vec`); the
oracle test of section 8 filled it from the reference tile's own `Data+43+005.apt` to prove the
road builder matched Ortho4XP with airports too, until decision 0010 (2026-09-14) removed it.

## 6. Costs

Measured on 2026-09-12 (M4 Pro, `nice -n 10`, load average 2.6), Ortho4XP run in its own
interpreter (the one that has `rtree`) on the very polygons OrthoStudio XP hands it:

| input | Ortho4XP | OrthoStudio XP | ratio |
|---|---|---|---|
| the 4 271 water polygons of `+43+005` | 0.220 s | 0.117 s | ×1.9 |
| 40 000 overlapping squares (8 330 groups) | 13.04 s | 1.22 s | ×10.7 |

Both give the same polygons (4 248 and 8 330). So the ×3-10 the plan expected from this
function is real, but only where the water data is dense: on the reference tile the merge is
0.22 s and the rtree keeps the candidate lists short. The stage is not where step 1 spends its
time — the noder is (`vectors-pslg.md`, ×20 measured) — and this spec does not claim
otherwise. End to end on `+43+005`: the water store reads in 0.36 s and the water builder
takes 0.41 s (4 271 polygons in, 1 365 out); the road store reads in 1.03 s and the road
builder takes 2.01 s, of which 1.53 s is the banking test alone (25 750 ways, two `alt_vec`
and one `weighted_normals` each).

## 7. Wanted differences

1. **Vectorised merge** (2.3): same components, same polygons; the order *inside* a merged
   component follows `union_all` rather than the incremental `unary_union`. Measured on
   `+43+005`: identical polygon count and identical total area to 1e-15.
2. **No rtree**: `shapely.STRtree` replaces `rtree.index.Index` everywhere. Same candidate
   sets (both are bounding-box indexes), no ctypes call per query.
3. **`max_levelled_segs` order** (3.2): Ortho4XP spends its budget in the iteration order of a `set`
   of negative ids; OrthoStudio XP spends it in the reading order of the store. Same set of ways as
   long as the cap is not reached (it is not on `+43+005`: 34 176 vertices against a budget of
   200 000); beyond the cap the two selections differ and only the count is promised.
4. **Patch way order** (4.1): reading order inside the tagged and untagged groups instead of
   `set` order. Only reachable when two patch ways of the same file overlap.
5. **One layer per marker run** instead of one insertion per way: the noder gives the same
   result (`vectors-pslg.md` 2.4, "insertion time"), and the recorded Ortho4XP layers of the
   reference tile are themselves grouped into runs.
6. **Reported, not printed**: every skipped way, relation, patch and large lake becomes an
   `OsxpError` in the decision report instead of a `UI.vprint` line.
7. **OBJ8 index arrays** (4.6): `TRIS offset count` slices the concatenation of the `IDX`
   values, which is what the format means; Ortho4XP uses `offset` as an `IDX` *line* number
   (`:944-948`) and therefore only reads the first `TRIS` of a file correctly. Same result for
   a single-`TRIS` object, which is what the patch workflow produces.
8. **A ramp shorter than one cell** (4.4): Ortho4XP leaves `alti_way` unset when `cuts_long == 0`
   (`:750`) and either crashes or reuses the previous way's altitudes; OrthoStudio XP falls back on
   the DEM.
9. **`resolution=1` is `quad_segs=8`**: shapely 1.8, which Ortho4XP 1.30 runs on, draws the
   rounded ends and corners of a band with 8 segments a quadrant whatever the resolution when
   the join is a mitre (an L-shaped line buffered: 37 points with `resolution=1` as with 8);
   shapely 2 honours the 1 (9 points), and the angular bands it gives (Ortho4XP 1.40, and
   OrthoStudio XP up to vectors version 12) bent the roads laid on them: triangles cut along the
   band's corners tilted the road at the KSME crossroad (2026-10-24). `quad_segs=8` gives the
   bands of Ortho4XP 1.30 point for point: on KSME's roads 290 341 points, as shapely 1.8, against
   271 064 with `quad_segs=1`, the same area. Against `quad_segs=1` on 200 tiles (the 72 witness
   tiles and 128 drawn at random): all built, 59 identical, triangles -1.2 % to +1.1 %, and the
   ground away from the roads as close to the relief (median gap 1.312 m, then 1.313 m).
   Since 2026-10-27 it is an option, off by default (3.7): at the service roads of a dense city
   the round ends doubled the tracing's time and memory.
10. **Dry rainwater basins are not water** (2.1). OpenStreetMap maps rainwater basins as
   `natural=water` + `water=basin` (retention, detention, infiltration), most marked
   `intermittent=yes` because they are dry grass most of the year; Ortho4XP draws them as water,
   and a user saw X-Plane's water over the photo's grass at Leipzig/Halle (EDDP, `+51+012`, idea
   #13, 2026-10-11). Measured on `+51+012`, default settings, the prepared library of 7 October
   2026: 558 basins left out of the tile, none of the 37 around the airport left as water; the
   treatment basin beside it (not intermittent) and the ponds stay water. `min_area` was the only
   way around it before, and it took every pond of the tile under its area too. The rule
   `orthostudio.vectors` went to version 2 with it, so a tile built again recomputes its water.

## 8. Acceptance

Tests `tests/test_vectors_water.py`, `test_vectors_roads.py`, `test_vectors_patches.py`. The
comparisons with Ortho4XP below were made by `test_vectors_water_oracle.py` and
`test_vectors_roads_oracle.py`, which left with decision 0010.

1. **Water, against the 1 650 `WATER` ways Ortho4XP recorded** (`noding/layers+43+005.npz`): 1 650 /
   1 650 rings, 80 957 / 80 957 vertices, **79 067 / 79 067 segments identical at 1e-9, none on
   either side alone**; 1 365 / 1 365 seeds, largest disagreement 1e-12 (the `.poly` is text with
   15 decimals), and every reference seed falls inside one of the OrthoStudio XP polygons.
   Altitudes: 78 922 shared nodes, 154 differ (up to 1.27 m), every one of them within 57 m of an
   airport boundary — Ortho4XP smooths its raster over the airports *before* the water layer
   (`O4_Vector_Map.py:206-212`), which is wave 2.
2. **Water merge**: 4 248 polygons and the same total area to 1e-12 as the real
   `MultiPolygon_to_Indexed_Polygons` run in the legacy interpreter on the same input.
3. **Roads, against the `INTERP_ALT` ways Ortho4XP recorded**, with the airport areas injected
   from `Data+43+005.apt`: 90 956 segments built, **all of them** Ortho4XP segments at 1e-9;
   982 seeds, all of them reference seeds; 90 956 shared nodes, 50 disagreeing on `z` (up to
   0.76 m), all within 61 m of an airport boundary. The `z` of a road ring is the DEM sampled
   4 m to the left, so this also proves `weighted_normals`, `refine_way` and `improved_buffer`
   — and it took the exact two divisions of `refine_way` (`:1129-1135`) to get there: the
   algebraically equal `1 - j/(ins+1)` moved four vertices by 1e-9.
4. **What is missing is the helipads**: the 646 segments Ortho4XP has and OrthoStudio XP has not are
   52 whole ways; 49 of them match an OSM `aeroway=helipad` way coordinate for coordinate and 3 are
   the hexagons Ortho4XP grows around a helipad *node* (`O4_Airport_Utils.py:1370-1450`). That is
   `flatten_helipads`, wave 2, and it accounts for the 52 extra `INTERP_ALT` seeds of the reference
   `.poly` (1 034 against 982).
5. **Roads without airports** (the wave-1 configuration of A3): 2 783 ways levelled instead of
   4 467 and 911 polygons instead of 982 — the roads Ortho4XP levels *because* they touch an
   airport. What the smaller network builds is the same surface bar 7.6e-8 deg² (682 m²,
   3.5e-4 of the road area) of slivers where the grow-and-shrink buffer misses a neighbour.
6. **`road_level = 3`**: the volume grows. There is no `small_roads` cache on the reference
   machine and no network is allowed, so this test uses a synthetic layer and is *not* a
   fidelity test.
7. **Patches**: a `.patch.osm` file exercising each altitude tag, a ramp with its cross bars,
   and an OBJ8 object, all checked against values computed by hand in the test. Until decision
   0010 (2026-09-14) the test also read the one real patch of the reference machine
   (`Patches/+39-078` of its Ortho4XP folder, 24 polygons).

## 9. Not covered here

The coastline (`include_sea`, `coastline_to_MultiPolygon`) and the assembly of all the layers
have their own specs; the airports and the elevation smoothing over them came with wave 2
(`airports-integration.md`). No setting or page reaches `good_imagery_list` (2.2).

## 9. Review-5 corrections

* `refine_way` computes the segment lengths and each segment's insertions with numpy but
  walks the segments in a Python loop; its docstring claimed more. Cost is linear in the
  inserted points (under a second for 5 000), so it is left as it is, honestly described.
* `ensure_MultiPolygon` and `cut_to_tile` are no longer transcribed here: both come from
  `orthostudio.vectors.geom` (`vectors-assembly.md` 11), with the same bodies the oracle measured.
* Every OSM layer of the road level is **required** by `layers.build_layers`; a missing
  `small_roads` at `road_level >= 2` used to build a different tile in silence.
