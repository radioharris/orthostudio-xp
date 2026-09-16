# P4 wave 2: airports in the native vector stage, end to end

Date: 2026-09-12. Machine: Mac M4 Pro 14 cores / 48 GB, macOS 25.6, Python 3.14.7,
numpy 2.5.3, shapely 2.1.2 (GEOS 3.13.1), Triangle4XP from `native/triangle4xp/build`.
Every run under `nice -n 10`; 1-minute load 1.9–3.5 during the measurements (`uptime` noted
before and after each). Tile +43+005 (Marseille, 18 aerodromes including LFML), ZL14 `BI`,
`road_level=1`, `mesh_zl=19`, `apt_smoothing_pix=8`.

**Zero network.** No Overpass request, no imagery provider, no elevation download, and
Ortho4XP was never launched for these numbers: the inputs are the four warm `.osm.bz2`
caches of the Ortho4XP checkout (`+43+005_airports.osm.bz2` included), `Elevation_data` with
`download=no_download`, and the frozen reference build
`fixtures/large/oracle/+43+005_zl14_BI`. The Ortho4XP timings quoted below come from
`docs/benchmarks/baseline-ortho4xp.md`, measured on this machine on this tile.

Reference for wave 1: `docs/benchmarks/p4-vectors.md` (the same stage **without** airports).

## 1. What wave 2 wired

`orthostudio.vectors@1` now runs the whole of `O4_Vector_Map.include_airports` (`:181-222`) inside
itself: discovery, runways, surfaces, boundaries, **the elevation smoothing**, the PSLG
encoding of runways, taxiways, aprons, hangars and helipads, and the 1001×1001 neighbourhood
raster the roads read. Its artefact gained four files: the smoothed `Data<tile>.alt` with its
`dem.json`, OrthoStudio XP's own airport record (`airports.npz` + `airports.wkb.json`) and the
`Data<tile>.apt` bridge Ortho4XP's stage 2 and the DSF airport cover still read (arbitration B3).
Spec: `docs/specs/airports-integration.md`.

## 2. Fidelity, in the order of the chain

### 2.1 `Data+43+005.alt` — **byte-identical** (arbitration B2, blocker B1 of `dem.md` 9.2)

| | value |
|---|---|
| bytes | 53 963 716 / 53 963 716 |
| float32 samples differing | **0** of 13 490 929 |
| the premise (raster before the smoothing vs Ortho4XP's) | 21 923 samples differ, worst 13.567 m |

The blocker P3 left open is closed: the raster the mesh, the masks and the DSF read is Ortho4XP's,
to the byte. Proof: `tests/test_p4v2_oracle.py::test_the_alt_of_the_artefact_is_byte_identical`.

### 2.2 The PSLG — set-equal to `Data+43+005.{node,poly}`

| | OrthoStudio XP | Ortho4XP | |
|---|---|---|---|
| nodes | 247 282 | 247 282 | 0 invented, 0 missing |
| nodes at the identical 9-decimal key | 247 279 | — | the 3 residual ones, paired within 1e-9 |
| edges (unordered) | 271 320 | 271 320 | 0 in excess, 0 missing **as a set at 1.5e-9**; at the exact 9-decimal key, the 11 edges incident to the 3 residual nodes differ (review 6) |
| marker disagreements on the common edges | **0** | | |
| altitude, worst difference | **5.0e-10 m** | | over all 247 282 nodes |
| `INTERP_ALT` altitudes (107 230 nodes) | 0 above 1e-9 | | worst 5.0e-10 m |
| region seeds | 2 615 | 2 615 | **block byte-identical** |
| headers (`.node`, `.poly`, holes) | `247282 2 1 0`, `0 2 1 0`, `0` | identical | |

Edges by marker, both sides: DUMMY 45 157, WATER 79 951, SEA 37 327, WATER|SEA 1 609,
INTERP_ALT 96 300, **RUNWAY 5 952, TAXIWAY 4 289, HANGAR 735**. APRON (64) is 0 on both
sides: no apron of this tile carries the `include` tag the encoder requires
(`O4_Airport_Utils.py:1288-1296`), so that marker is **not exercised here** — a declared hole
in the proof, the same one `airports-geometry.md` 8 lists.

Seeds by marker, both sides: WATER 1 365, SEA 5, INTERP_ALT 1 034, RUNWAY 84, TAXIWAY 28,
HANGAR 99.

**What the airports are worth.** Against the airport-free reference of wave 1
(220 481 nodes / 239 234 edges / 2 281 seeds):

| | added by the airports |
|---|---|
| nodes | **26 801** (11 % of the PSLG) |
| edges | **32 086** |
| seeds | **334** — of which 263 are airport seeds proper (84 RUNWAY + 28 TAXIWAY + 99 HANGAR + 52 helipads) and 71 are roads: the airport neighbourhood raster and the treated area change which roads `include_roads` levels (`O4_Vector_Map.py:223-355`), so a tile with aerodromes is not the airport-free tile plus airports |

### 2.3 The airport passes, way by way

The 2 644 `insert_way` calls Ortho4XP recorded for this tile (`layers+43+005.npz`), against the
output of the **whole native chain** — OSM read, discovery, runway reconstruction, surface
builders, boundaries, smoothing, encoding:

| | value |
|---|---|
| ways | 2 644 / 2 644, same order |
| markers | 42 RUNWAY, 2 411 DUMMY (traverses), 40 TAXIWAY, 99 HANGAR, 52 INTERP_ALT — identical |
| worst coordinate difference | **0.0** (bit for bit, 15 591 vertices) |
| worst altitude difference | **4.0e-13 m** |

### 2.4 Runways are **not** flat — in Ortho4XP either

The brief asked for an explicit check that every runway has a constant altitude, equal to
Ortho4XP's. The second half holds; the first does not, and it does not hold for Ortho4XP
either. What flattens a runway is the raster smoothing plus the degree-7 fit along the centre
line (`O4_Airport_Utils.py:1120-1180`); the outline follows that fit, so its altitude spans:

| | OrthoStudio XP | Ortho4XP | difference |
|---|---|---|---|
| runways measured | 42 | 42 | |
| median altitude span of a runway outline | 2.371 m | 2.371 m | **2.3e-13 m** |
| worst | 18.603 m | 18.603 m | |
| mean altitude of a runway | | | **1.1e-13 m** |
| runways spanning more than 0.5 m | 37 | 37 | |
| runways flat to 1e-6 m | **0** | **0** | |

So: OrthoStudio XP reproduces Ortho4XP's runway altitudes exactly, and the X-Plane "a runway is
flat" invariant is **not** what step 1 produces. Making runways flat is a product decision, not a
fidelity one; it would move the mesh and the DSF and belongs to an architect's arbitration. The
numbers above are frozen in `tests/test_p4v2_oracle.py` so that such a change is deliberate and
measured. (`airports-encoding.md` 5.3 reaches the same conclusion from the encoder alone; this is
the same measurement made through the full native chain.)

### 2.5 The mesh — equivalent, not byte-identical

The oracle is validated first: rebuilding the fixture's mesh from the fixture's **own**
`.node`/`.poly` gives its bytes back (md5 `1f29a2637feac3264c0dcf8bbe8a21b5`, 69 171 409
bytes). Then, from our PSLG, same Triangle4XP, same options, same raster, same coastline:

| | Ortho4XP's PSLG | ours |
|---|---|---|
| mesh bytes | 69 171 409 | 69 172 464 (**+1 055**) |
| vertices | 603 649 | 603 658 (**+9**) |
| triangles | 1 197 758 | 1 197 776 |
| altitude range | −9.6411383422 … 1250.9275580394 | identical |
| vertex positions present in both | 602 346 of 603 649 | |
| worst altitude difference on a shared position | 4.229 m (282 vertices above 1 m) | `water_smoothing` iterates over the mesh graph, whose neighbourhoods the extra Steiner points change |

Triangles by terrain attribute, and this is the line that matters for the airports:

| attribute | Ortho4XP | ours |
|---|---|---|
| **RUNWAY (16)** | **10 315** | **10 315** |
| **TAXIWAY (32)** | **4 331** | **4 331** |
| **HANGAR (128)** | **546** | **546** |
| SEA (2) | 81 925 | 81 925 |
| ground (0) | 886 579 | 886 612 |
| WATER (1) | 107 881 | 107 876 |
| WATER\|SEA (3) | 8 787 | 8 781 |
| INTERP_ALT (8) | 97 189 | 97 185 |
| 9 / 10 | 108 / 97 | 108 / 97 |

Every airport terrain is assigned to exactly the same number of triangles; what moves is the
generic ground and water, by 33 triangles of 1.2 million, where Triangle put its extra Steiner
points.

### 2.6 The `.ter` files — **39/39 byte-identical**; the DSF — not

| | value |
|---|---|
| `.ter` files | 39 / 39, same names, **byte-identical** |
| texture jobs | 17, identical |
| DSF | 32 442 794 bytes vs 32 442 870 (**−76**) |
| first difference | `DEFN/TERT`, byte 22 of the payload: the *order* in which the terrains were created, which follows the triangle order |

Both DSFs are built with the **frozen ZL14 masks of the Ortho4XP build** and the same X-Plane 12
rasters, so the only variable is the mesh; in a real `--stages native` build the masks are
rebuilt from our own mesh, and review 5 measured that they then differ from Ortho4XP's on about a
thousandth of their pixels (`tests/test_review5_fidelite_pslg.py`). Control: the same
`build_dsf` fed the fixture's own mesh reproduces the reference DSF byte for byte, so the
difference measured here is the mesh and nothing else.

## 3. Where the last difference comes from — and it is not geometry

The `nodes10` chantier established that Ortho4XP numbers a crossing when it creates it, walking
the candidates in the order its libspatialindex R-tree returns them
(`O4_Vector_Utils.py:127-134`) — a property of that index's node splits, not of the geometry.
226 375 of our 247 283 `.node` lines are already at the same index; the rest is a local
permutation.

Two experiments separate the numbering from everything else.

**(a) Canonical order on both sides.** Sort the nodes lexicographically, renumber the edges,
sort them: 176 of 247 283 `.node` lines and 458 `.poly` lines still differ; the mesh is
**+91 bytes in size** and the DSF **+26 bytes in size**. Those are size deltas, not the count
of differing bytes (review 6): the 3 nodes shift every later record, so 60 406 287 bytes of
the mesh and 16 500 661 bytes of the DSF differ in place. That residue is the 3 nodes of
section 2.2 -- small in geometry, not in bytes.

The number of residual nodes **depends on the input**: 3 on `+43+005`, 17 (the same 3 plus 14,
on three vertical grid lines) on the synthetic aeroway layer review 6 placed on the same tile
(`tests/test_review6_fidelite_synthetique.py`). `exact_grid_order` closes it to 0 in both.

**(b) Canonical order, with `exact_grid_order`.** The option inserts every horizontal grid
line as its own pass, which is what Ortho4XP's sequence amounts to (it cuts the chain it has
already built *while* a layer is inserted). Then:

| | value |
|---|---|
| nodes at the identical 9-decimal key | **247 282 / 247 282**, 0 ours-only, 0 reference-only |
| canonical `Data+43+005.node` | **byte-identical** (247 283 lines) |
| canonical `Data+43+005.poly` | **byte-identical** (273 942 lines) |
| mesh from those two | **byte-identical** (69 190 420 bytes, both sides) |
| DSF from that mesh | **byte-identical** |

**Conclusion.** Every coordinate, every altitude, every marker and every seed of Ortho4XP Ortho4XP's
step 1 is reproduced exactly, and the whole downstream chain — Triangle4XP, the post-processing, the
DSF encoder — is exact at equal input. What is left between OrthoStudio XP and a byte-identical DSF
is one thing: **the order in which Ortho4XP hands its nodes and edges to Triangle**. Reproducing it
would mean reimplementing libspatialindex's traversal, which `nodes10` advised against and this
chantier did not attempt.

## 4. Speed

### 4.1 The stage

| | Ortho4XP stage 1 (with airports) | `orthostudio.vectors@1` (with airports) | ratio |
|---|---|---|---|
| the node, artefact written | **31.8 s** | **6.55 s** | **×4.9** |

Ortho4XP's figure is `build_poly_file` from `docs/benchmarks/baseline-ortho4xp.md` (31.8 s; 31.6 s
mean for the whole stage, 30.5 s best, 33.7 s when instrumented). Ortho4XP was not re-run for this
document — the network rule of the chantier — and nothing here depends on a new measurement of it.

Best of 3 on our side (7.41 / 6.55 / 7.62 s wall; the figure is the best, as in
`p4-vectors.md`); peak RSS 984 MB.

| step | s | |
|---|---|---|
| `orthostudio.dem@1` (its own node) | 0.09 | outside the 6.55 s |
| read the four OSM layers | 1.66 | of which ≈0.33 s for `airports` (1 041 ways, 606 surfaces) |
| **airports** (discover → runways → areas → boundaries → smooth → encode) | **0.39** | 18 aerodromes, 42 runways, 2 644 ways, 15 591 vertices |
| roads | 2.17 | 25 750 ways, 4 467 levelled, 982 polygons |
| water | 0.40 | |
| coastline | 0.16 | |
| patches | 0.00 | +43+005 has none |
| **layers total** | **4.73** | `orthostudio.vectors.layers.build_layers` |
| grid + border + noding + seeds | 1.77 | of which `node_layers` **1.21** |
| publish `.alt`, `dem.json`, the two airport records | 0.03 | 54 MB of raster included |

**What the airports cost each side.** OrthoStudio XP: 6.55 − 5.13 = **1.42 s** on top of wave
1 (0.33 s of reading, 0.39 s of airport work, and the rest is the 11 % of extra PSLG the noder and
the writer carry). Ortho4XP: 31.8 − 26.70 = **5.1 s**. The airport chain itself is the cheapest part
of the stage; the expensive thing about airports is that they make the graph bigger.

`exact_grid_order` (section 3b) costs the noding: **17.73 s** for the node instead of 6.55 s
(`node_layers` 12.29 s instead of 1.21 s). Still faster than Ortho4XP, and off by default: it buys
3 nodes out of 247 282 and does not make the mesh byte-identical on its own.
*Re-measured after the incremental insertion of review 6* (`nice -n 10`, load 2.6, best of 1):
`node_layers` **1.67 s** with the switch against 1.05 s without (layers + assembly + writing
6.34 s against 5.67 s). The switch now costs 0.6 s instead of 11 s; whether that makes it the
default is the architect's decision, since it changes the key of every native vector artefact.

### 4.1b A tile dense in aerodromes (review 6)

Each aerodrome adds about five noding passes. The noder used to re-derive the whole graph at
every pass; review 6 measured the resulting quadratic term on synthetic tiles (whole rule,
`mesh_zl` 12, each aerodrome with two crossing runways, a taxiway and a hangar), and the
incremental insertion of `vectors-pslg.md` 2.13 removed it. Same machine, `nice -n 10`:

| aerodromes | passes | nodes | noding before (review 6, load ~2.9) | noding after (load ~1.2) |
|---|---|---|---|---|
| 36 | 184 | 39 007 | 0.99 s | 0.29 s (before the x-first prefilter) |
| 81 | 409 | 66 524 | 4.04 s | 0.89 s (idem) |
| 144 | 724 | 102 143 | 11.7 s | 2.14 s (idem) |
| 324 | 1 624 | 197 531 | **53.2 s** | **3.76 s** (rule total 9.0 s instead of 58.7 s) |

`+43+005` is unchanged (100 passes: 1.02 s before, 1.01 s after, `NodedGraph` byte-identical).
Ortho4XP was not measured on these tiles. The x4.9 of section 4.1 is a single-tile figure; what
this table says is that a dense tile no longer turns it round.

### 4.1c The real build, through the scheduler (review-6 fix pass, 2026-09-13)

```
OSXP_HOME=<scratch>/home nice -n 10 uv run osxp build --tile +43+005 --provider BI --zl 14 \
    --stages native --vectors native --creation-agent Ortho4XP --legacy-dir <Ortho4XP> --out <scratch>/out
```

The first attempt failed in 0.2 s: the vector node was declared `cpu` with a run closure and a
decorated rule function, neither of which pickles for the spawned worker, so **no real
`--vectors native` build had ever run** (the graph tests use `cpu_in_threads=True`; every
figure above was measured node by node). The node is now a `subprocess`-slot node like the
legacy recipe (`tests/test_p4v2fix.py::test_every_cpu_node_of_a_native_build_pickles`).

Second attempt, `uptime` load 1.47, empty store, warm chunk store copied from the P4 wave-1
verification (0 texture request), DEM miss memo of an earlier run (0 elevation request), OSM from
the Ortho4XP warm cache (0 Overpass request): **10 built, 0 failed, 13.2 s scheduler, 13.66 s wall**
(dem 0.1, coastline 0.2, xp12 0.4, overlay 4.3, vectors 6.2, mesh 2.4, masks 0.7, dsf 1.7, textures
1.5, pack 0.0). Against the frozen Ortho4XP build: `Data+43+005.alt` byte-identical (md5
`abb7a099…`); PSLG 247 282 / 271 320, 3 nodes and 11 edges differ at the exact key, 0 marker
mismatch, seeds and headers identical; mesh +1 055 bytes, +9 vertices, +18 triangles (DUMMY +33,
WATER -5, WATER\|SEA -6, INTERP_ALT -4; RUNWAY, TAXIWAY, HANGAR exact); DSF -76 bytes; 39 /
39 `.ter` byte-identical. `stats.json` now reports `events: {OSM_AIRPORT_TOO_SMALL: 19}`.

### 4.2 The chain to the DSF, textures excluded

Same scheduler-free node-by-node measurement as `p4-vectors.md` 3.3, on the real tile (with
its airports), best of 3 for the nodes measured here.

| node | `--vectors legacy` | `--vectors native` |
|---|---|---|
| dem | — (inside stage 1) | 0.09 |
| coastline | 0.11 | 0.11 |
| xp12 rasters | 0.39 | 0.39 |
| **vectors** | **31.80** | **6.55** |
| mesh | 2.46 | 2.46 |
| masks | 0.93 | 0.93 |
| dsf | 1.39 | 1.39 |
| **wall to the DSF** | **37.07 s** | **11.91 s** (**×3.1**) |

Two rows are quoted, not re-measured: `vectors` under `--vectors legacy` is Ortho4XP's own stage 1
(`baseline-ortho4xp.md`, 31.8 s — running it again was not worth 32 s and a write into the user's
checkout), and `masks` comes from `p4-vectors.md` 3.3 (it reads the mesh and the same four
frozen mask sets). Everything else is a best of 3 measured for this document.

### 4.3 The whole ZL14 tile

The texture stage was **not** re-run: it needs the imagery chunks, and the network budget of
this chantier is zero. The only node whose cost changed is the vector node, so the tile figure
is an arithmetic substitution and is labelled as one:

| | wall |
|---|---|
| Ortho4XP, whole tile (`baseline-ortho4xp.md`) | **63.2 s** |
| OrthoStudio XP today, `--vectors legacy` | **41 s** (the chantier brief's figure; `p2-build.md` measured 49.9 s end to end with the install, `p3-native.md` 44.19 s for the scheduler) |
| OrthoStudio XP, `--vectors native` = 41 − (31.80 − 6.55) + 0.09 (the elevation node) | **≈ 15.8 s** (projection) |
| ratio against Ortho4XP | **≈ ×4.0** |

## 5. Should `--vectors native` become the default?

**Not yet — and the reason is not a number in this file.**

What argues for it, all measured above: the PSLG is Ortho4XP's, as a set and to the byte once the
numbering is taken out; `Data<tile>.alt` and the 39 `.ter` are byte-identical; the stage is
×4.9 and the tile ×4.0; the native path also removes the last use of Ortho4XP's Overpass client
from a normal build (3 of its 4 servers are dead, and one failure costs 5 min 40).

What argues against it, and what should lift it:

1. **One tile.** Everything above is +43+005. The APRON marker is not exercised on it, nor is
   a `smoothing_pix` tag, a `custom` tag on a runway, a runway encoded as a relation, or
   either rejection path — five rules that only unit tests cover
   (`airports-geometry.md` 8). A second tile (a dense metropolitan one) and a third (coastal,
   no aerodrome) would close that, and the benchmark harness to do it is the one used here.
2. **A rebuild changes bytes.** The mesh moves by 9 vertices of 603 649 and the DSF by 76
   bytes of 32 million. Nothing measurable in the simulator — the altitude range is identical
   and the terrain assignment is the same — but a user who rebuilds a tile they already have
   gets a different file, and the default should not do that until a second tile confirms the
   size of the difference.

3. **Closed by the review-6 fixes** (were conditions of the flip): the Ortho4XP harness no longer
   reaches the network (`LegacyRunner(offline=True)`, three viewfinderpanoramas GETs refused
   and asserted per stage-1 run); the airport-free mesh parity lost to `nodes10` is published
   (-4 / +2 before, +46 / +92 after, `p4-vectors.md`); the noding is no longer quadratic in the
   number of aerodromes (section 4.1b); an OSM `smoothing_pix` tag can no longer crash or stall
   the stage; the DSF node no longer unpickles the bridge without an allow-list; rejected
   runways reach the log at WARNING and `stats.json`.

Recommendation: keep `--vectors legacy` the default, document `--vectors native` as the fast
path (the CLI help now does), and flip it after tiles 2 and 3 are measured. The decision is
the architect's; this file is the evidence.

**Flipped on 2026-09-13 (decision 0008)** after tiles 2 and 3 were measured: section 7.

**Blocker 5 of the integration report is rejected** (review 6): the frozen `Data+43+005.apt` is not
stale. Both interpreters run shapely 2.1.2 / GEOS 3.13.1, and the airports OrthoStudio XP builds
equal it in `equals_exact(0)`, every field of the 18 airports
(`tests/test_p4v2_oracle.py::test_the_airports_are_exactly_the_frozen_pickle`; the bridge it then
published was removed by decision 0009). The "7 of 18"
disagreement came from the Ortho4XP driver of `tests/test_aptdata_oracle.py`, which never set
`O4_Vector_Utils.scalx`; with it set, 18 of 18. The fixture must not be regenerated.

## 6. Non-regression

| | |
|---|---|
| `--vectors legacy` DSF | byte-identical to the fixture (`tests/test_dsf_oracle.py`, unchanged and green) |
| `--vectors legacy` `.ter` | 39/39 byte-identical (same test) |
| wave-1 comparison with the airport-free Ortho4XP | `tests/test_p4_integration.py`, 20 tests green, now at 220 481 / 239 234 on both sides |
| whole suite (oracle included) | **2 032 passed, 14 skipped, 1 failed** in 12 min 22 s. The failure is `test_install_scenery_packs.py::test_real_scenery_packs_ini_is_read_only_and_reordered_on_a_copy`: it reads the user's real X-Plane `scenery_packs.ini` and asserts a hard-coded list of pack names that the installation no longer has. It fails identically with this chantier stashed, and `nodes10` reported it before this one started |

## 7. How to re-run

```bash
uv run pytest tests/test_p4v2_oracle.py -q -o addopts=''      # the whole chain, ~45 s
uv run pytest tests/test_p4v2_stage.py tests/test_p4v2_rule.py -q -o addopts=''
uv run pytest tests/test_p4_integration.py -q -o addopts=''   # wave 1, airport-free reference
uv run pytest tests/test_dsf_oracle.py -q -o addopts=''       # the legacy chain, unchanged
```

## 7. Tiles 2 and 3 (2026-09-13): the flip

Two more frozen references, made with Ortho4XP on this machine
(`tools/oracle/run_legacy.py`, then `tools/oracle/freeze_build.py`, textures left out), ZL14
`BI`, `road_level=1`, `mesh_zl=19`, Ortho4XP's own relief. The OrthoStudio XP side is
`osxp build --stages native --vectors native --relief view`, compared artefact by artefact with
`tools/oracle/compare_native.py`. The OSM layers were fetched once by OrthoStudio XP's client, which
writes Ortho4XP's `.osm.bz2` cache, so Ortho4XP read the same data. Its stage 3 downloaded only the
imagery.

| | +43+005 Marseille (sections 2-4) | +50+008 Frankfurt | +39+003 eastern Mallorca |
|---|---|---|---|
| PSLG nodes, Ortho4XP = OrthoStudio XP | 247 282 | 282 184 | 62 979 |
| nodes not at the exact 9-decimal key | 3 | 26, all within 1e-9 | 0 |
| PSLG edges, Ortho4XP = OrthoStudio XP | 271 320 | 311 831 | 75 434 |
| edges or nodes only on one side | 0 | 0 | 0 |
| marker disagreements | 0 | 0 | 0 |
| worst node altitude difference | 5.0e-10 m | 1.0e-9 m | 0 |
| seed and hole blocks, headers | byte-identical | byte-identical | byte-identical |
| `Data<tile>.alt` | byte-identical | byte-identical | byte-identical |
| mesh vertices Ortho4XP / OrthoStudio XP | 603 649 / 603 658 | 475 129 / 475 141 | 155 686 / 155 671 |
| mesh triangles Ortho4XP / OrthoStudio XP | 1 197 758 / 1 197 776 | 940 442 / 940 466 | 301 970 / 301 940 |
| worst altitude on a shared vertex | 4.229 m | 2.847 m (170 above 1 m) | 1.470 m (2 above 1 m) |
| RUNWAY / TAXIWAY / HANGAR triangles | equal | 8 112 / 11 217 / 90, equal | 614 / 24 / 10, equal |
| DSF bytes Ortho4XP / OrthoStudio XP | +76 | 26 696 361 / 26 696 672 | 14 103 193 / 14 102 352 |
| DSF terrains | same list | same 49, another order | same 29, another order |
| `.ter` byte-identical | 39 / 39 | 48 / 48 | 28 / 28 |

Edges by marker, both sides equal. Frankfurt: DUMMY 51 515, WATER 90 116, INTERP_ALT
152 472, RUNWAY 6 878, TAXIWAY 10 716, HANGAR 134. Mallorca: DUMMY 32 053, WATER 5 648,
SEA 28 285, WATER|SEA 1 815, INTERP_ALT 7 053, RUNWAY 540, TAXIWAY 28, HANGAR 12.

What moves is what moved on Marseille: Triangle4XP refines in the order of Ortho4XP's node
numbering, so the Steiner points differ by a few dozen and the water smoothing follows them.
The terrain order in the DSF is the order of first use along the triangles, hence another
permutation of the same terrains. On Mallorca the lowest sea-floor vertex differs by 9 cm
(−4.628 m against −4.720 m), for the same reason.

Indicative timings, one run each, not under `nice`:

| | Ortho4XP stage 1 | OrthoStudio XP vector node |
|---|---|---|
| +50+008 | 39.3 s | 8.2 s |
| +39+003 | 8.3 s | 1.0 s |

Still not exercised by any of the three tiles: the APRON marker, because no apron carries the
`include` tag, and a coastal tile without any aerodrome, since Mallorca has small ones.
Frozen as tests: `tests/test_native_vectors_tiles_oracle.py`.

