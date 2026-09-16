# Airports in the vector stage: wiring, the smoothed raster and the two published records

Status: P4 wave 2 integration (`src/orthostudio/airports_vec/stage.py`, `artefact.py`,
`src/orthostudio/vectors/layers.py`, `src/orthostudio/vectors/rule.py`,
`src/orthostudio/pipeline/native.py`). Origin: Ortho4XP `src/O4_Vector_Map.py` — `include_airports`
(`:181-222`) and the order in which `build_poly_file` calls it (`:19-179`). Companion specs:
`airports-discovery.md` (the record), `airports-geometry.md` (runways, areas, smoothing),
`airports-encoding.md` (the PSLG passes), `vectors-assembly.md` (the assembler this feeds), `dem.md`
section 9.2 (the blocker this closes).

This spec covers no geometry. It covers **the order of the calls**, **which elevation raster
every other family sees**, **what the artefact publishes** and **what enters the cache key**.

## 1. The chain, and why its order is load-bearing

`O4_Vector_Map.include_airports` (`:181-222`) followed by `:223-226`:

```
 1  discover_airport_names            :196   ─┐
 2  attach_surfaces_to_airports       :197    │  airports-discovery.md
 3  sort_and_reconstruct_runways      :198    │  airports-geometry.md  §3
 4  discard_unwanted_airports         :199    │  airports-discovery.md  R13
 5  build_hangar/apron/taxiway_areas  :200-202│  airports-geometry.md  §4
 6  update_airport_boundaries         :203    │  airports-discovery.md  R14
 7  list_airports_and_runways         :204   ─┘
 8  DEM(...)                          :206          the raster is read HERE, not before
 9  smooth_raster_over_airports       :213          airports-geometry.md §6
10  include_patches                   :214          orthostudio.vectors.patches
11  encode_runways_taxiways_aprons    :215          airports-encoding.md §5-7
12  treated_area = patches | surfaces :219
13  encode_hangars                    :220
14  flatten_helipads                  :221
15  build_airport_array               :222          airports-discovery.md R15
16  include_roads(apt_array, apt_area):223
17  include_sea / include_water       :224-226
```

**R-I1 — the raster is smoothed before anything samples it.** Step 9 mutates
`tile.dem.alt_dem` in place, and steps 10, 11, 13, 14, 16, 17 and the orthophoto grid all
sample that same object afterwards. In OrthoStudio XP the smoothing returns a *new* `Dem`
(`smooth_dem_over_airports`, an artefact must not be mutated) and **that** `Dem` is the one
handed to the patches, the airport encoder, the roads, the water, the coastline, the grid,
the gluing border and the default seed. Wave 1 passed the raw raster to all of them, which
was right only because it had no airports; keeping it would now put a runway's edge 13.6 m
off (`dem.md` 9.2: 21 923 samples differ, max 13.567 m).
Origin: `O4_Vector_Map.py:206-213` then `:214-226`; decision **keep**.

**R-I2 — the airport geometry is built before the raster is read.** Steps 1-7 use only OSM, step
8 reads the DEM. OrthoStudio XP keeps the order because step 9 needs the finished boundaries, not
because the DEM is expensive. Decision **keep**.

**R-I3 — patches are encoded after the smoothing and before the runways.** They are the
first pass of the PSLG (`vectors-assembly.md` 2.1) and their names and area are inputs of
step 11 (an airport a patch overrides is not encoded). Decision **keep**.

**R-I4 — the roads see the airports.** `include_roads` takes `apt_array` (step 15) and
`treated_area` (step 12). Wave 1 passed `NO_AIRPORTS`, which levels a road that Ortho4XP leaves
alone and buffers a road across a runway. Decision **keep**; the raster comes from
`discover.airport_array` (arbitration on the duplicate: section 6).

## 2. One call: `orthostudio.airports_vec.stage`

`build_airports(store, tile, params, on_event)` runs steps 1-7 and returns the `AirportSet`;
`smooth_elevation(dem, airports, params)` runs step 9; `encode(airports, tile, dem, ...)`
runs steps 11-15 through `encode_airports`. The seam between the record
(`model.Airport`) and the encoder (`encode.AirportLike`) is `views_of`, a nine-field
projection with no computation — resolving blocker 2 of the `aptgeom` chantier in favour of
its option (a), a view built by the integrator, because neither side then imports the other.

Arbitration on the duplicated rules (blocker 1 of `aptdata`): `discover.discard_unwanted`,
`discover.update_boundaries` and `discover.airport_array` are the ones called. They work on
the `AirportSet` and its insertion order, which is what the three rules read and write.
`areas.py` no longer holds a copy.

## 3. Which elevation the mesh reads (arbitration B2)

Ortho4XP writes **one** `Data<tile>.alt`, and it is the smoothed one: `DEM.write_to_file` is
called from `O4_Vector_Map.build_poly_file:169`, after step 9. Triangle4XP, the masks and the
DSF all read that file.

Decision: `orthostudio.dem@1` keeps publishing the **raw** raster (it is a root of the graph, ADR
0006: it cannot depend on the OSM airports), and `orthostudio.vectors@1` publishes the **smoothed**
one beside its PSLG, together with a `dem.json` carrying the window
(`osxp-dem-1`, the format `orthostudio.mesh.build.DemSpec.from_dir` reads). The mesh node's `dem`
input then points at the **vectors** node. This is option (c) of the `aptgeom` chantier's
blocker 4, and it makes `--dem vectors` literally true again.

Acceptance: `Data+43+005.alt` of the artefact is byte-identical to
`fixtures/large/oracle/+43+005_zl14_BI/build/Data+43+005.alt`.

## 4. The published airport records (arbitration B3, revised by decision 0009)

| File | Format | Read by |
|---|---|---|
| `airports.npz` + `airports.wkb.json` | npz of WKB geometries + a JSON sidecar of the scalar fields (format `osxp-airports-1`) | **the native contract**: the DSF airport cover (`orthostudio.dsf.zones.airport_covers`), `read_airports` |
| `airports.json` | `{"airports":[{"bounds":[…]}]}` (wave 1 already wrote it; a view of the same airports, bounds identical) | `orthostudio.mesh.weights.read_airport_bounds` |

**R-I5 — no pickle.** OrthoStudio XP neither writes nor reads a `pickle`. Wave 2 also published
Ortho4XP's `dico_airports` as `Data<tile>.apt`, a pickle Ortho4XP's own stage 2 read back, and the
engine read such a file through an exact allow-list for the artefacts of Ortho4XP's stage 1.
Review 6 had found that reader to be a bare `pickle.load` in the DSF cover, next to an allow-list
that admitted `eval`. Decision 0009 removed Ortho4XP's stages, and the bridge and its reader with
them: `tests/test_p4v2_stage.py::test_nothing_in_the_engine_pickles` and
`tests/test_review6_robustesse_architecture.py` keep any pickle out of `src/`. The allow-list reader
the comparison tests used went with them, by decision 0010.

**The same airports in Ortho4XP's form.** Until decision 0010, `apt_dictionary(airports)` rebuilt the
dictionary Ortho4XP pickles (its nine keys, in its insertion order) for the comparisons; on
`+43+005` it equalled the frozen pickle in `equals_exact(0)`, every field of all 18 airports. It
went with the comparisons.

**Atomicity (review 6).** `write_elevation_and_airports` stages `Data<tile>.alt`, `dem.json`,
`airports.npz` and `airports.wkb.json` under `.part` names and renames the set at the end; a failure
of any of them leaves none behind.

**Coded events (review 6).** The builders' events are logged at the level of their registry
severity (DEGRADED -> WARNING, INFO -> INFO) and `stats.json` carries `families` (the per-family
counters, `runways_rejected` included) and `events` (the count per code).

## 5. What enters the cache key

`VectorsParams` gains **`apt_smoothing_pix`** (Ortho4XP's configuration) and **`exact_grid_order`**
(an OrthoStudio XP fidelity switch, off by default, which changes the artefact and therefore enters
the key; corrected by review 6, this line used to say "nothing else"): a sweep of Ortho4XP's
`src/O4_Airport_Utils.py` for `tile.<attribute>` yields `dem`, `lat`, `lon` and `apt_smoothing_pix`,
and the first three are not configuration. The airport OSM layer enters through the digest of the
`osm` input, the elevation through the digest of the `dem` input, the patches through the digest of
the `patches` input.

**R-I6 — a consumed parameter that is not declared is a silently wrong cache.** The test
`test_p4v2_rule.py::test_every_consumed_parameter_is_in_the_key` changes each declared field
in turn and asserts the key moves, and asserts that `apt_smoothing_pix` in particular changes
both the key **and** the bytes of `Data<tile>.alt`.

## 6. Decisions taken here that other chantiers asked for

| Question | Decision | Asked by |
|---|---|---|
| `discard_unwanted` / `update_boundaries` / `airport_array`: `discover.py` or `areas.py`? | `discover.py`; `areas.py` holds no copy | `aptdata` blocker 1 |
| canonical airport record | `model.Airport` / `model.AirportSet`; `encode.AirportLike` stays a protocol, `views_of` is the seam | `aptdata` blocker 2, `aptgeom` blocker 2, `aptencode` blocker 3 |
| `build_runways(airports, store, tile, params)` | accepted as delivered (option (a)) | `aptgeom` blocker 1 |
| who writes the smoothed `Data<tile>.alt` | `orthostudio.vectors@1` (option (c)) | `aptgeom` blocker 4 |
| who writes `Data<tile>.apt` | `airports_vec/artefact.py`, called by the rule; nobody since decision 0009 | `aptgeom` blocker 5, `aptencode` blocker 7 |
| the recorded `INTERP_ALT` run is helipads **then** roads | the integration splits it; `tests/test_vectors_assemble_oracle.py` updated | `aptencode` blocker 1 |
| who passes the 1001×1001 raster | the layer builder, from `discover.airport_array` | `aptencode` blocker 2 |
| `_Runs` promoted? | yes: `orthostudio.vectors.patches.Runs` is public and `encode.py` imports it (corrected by review 6) | `aptencode` blocker 5 |

## 7. Acceptance

All in `tests/test_p4v2_oracle.py` unless said otherwise; measured 2026-09-12 on +43+005,
numbers and method in `docs/benchmarks/p4-airports.md`.

| # | Target | Reached |
|---|---|---|
| A1 | `Data+43+005.alt` byte-identical to the fixture | **yes** — 0 of 13 490 929 float32 samples differ (`::test_the_alt_of_the_artefact_is_byte_identical`) |
| A2 | PSLG node and edge sets equal to `Data+43+005.{node,poly}`, markers 16/32/128 included, seeds, headers | **yes** — 247 282 / 247 282 nodes, 271 320 / 271 320 edges, 0 marker mismatch, seed block byte-identical (`::test_the_pslg_is_the_one_of_ortho4xp`, `::test_the_seed_block_and_the_headers_are_byte_identical`). Marker 64 (APRON) is 0 on both sides and stays unexercised |
| A3 | every runway's altitude equals Ortho4XP's, runway by runway | **yes** — 42 runways, spans equal to 2.3e-13 m, means to 1.1e-13 m. They are **not** flat, in Ortho4XP either (median span 2.371 m): `::test_the_runways_carry_ortho4xps_altitudes_and_are_not_flat` |
| A4 | `Data+43+005.mesh` built on that PSLG vs the fixture | **no** — +1 055 bytes, +9 vertices of 603 649; the airport terrain triangle counts are identical (`::test_the_mesh_is_equivalent_and_not_byte_identical`) |
| A5 | DSF and the 39 `.ter` at the end of the chain | `.ter` **39/39 byte-identical**; DSF differs by 76 bytes, first difference in the terrain *order* of the DEFN atom (`::test_the_ter_files_are_byte_identical_and_the_dsf_is_not`) |
| A6 | what A4 and A5 are missing | the node **numbering** of Ortho4XP, and only that: in canonical order with `exact_grid_order` the `.node`, the `.poly`, the mesh and the DSF are byte-identical (`::test_in_canonical_order_the_mesh_and_the_dsf_are_byte_identical`) |
| A7 | the DSF built on Ortho4XP's own mesh and masks is still byte-identical | **yes** — `tests/test_dsf_oracle.py`, unchanged |
| A8 | every declared parameter moves the key | **yes** — `tests/test_p4v2_rule.py::test_every_consumed_parameter_is_in_the_key` |
