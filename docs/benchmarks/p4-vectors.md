# P4 wave 1: the native vector stage, end to end

> **Superseded in two places, 2026-09-12 (wave 2).** (1) The residue of *ten merged nodes*
> that this file measures throughout is gone: the `nodes10` chantier corrected the
> intersection solver and the wave-1 PSLG is now **220 481 / 239 234** on both sides, with
> nothing "only in the reference" — so section 2's "10 nodes / 30 + 40 edges" and section 4's
> conclusion read as history. (2) The remaining obstacle to a byte-identical mesh is not the
> merged nodes but Ortho4XP's **node numbering**, which comes from its spatial index.
> Current numbers, airports included: `docs/benchmarks/p4-airports.md`. The measurements
> below are otherwise unchanged and were not re-run.
>
> **What `nodes10` cost the airport-free mesh (review 6, measured 2026-09-12).** Same
> fabricated root, same Triangle4XP, same raster and options, Ortho4XP run offline:
>
> | noding | PSLG, nodes / edges | mesh, vertices / triangles vs Ortho4XP |
> |---|---|---|
> | committed (`HEAD`, before `nodes10`) | 220 471 / 239 224 | **-4 / +2** |
> | with `nodes10` | 220 481 / 239 234 (= Ortho4XP) | **+46 / +92** |
>
> Triangles per attribute, native minus Ortho4XP, with `nodes10`: DUMMY +35, WATER +7, SEA +20,
> WATER\|SEA +34, INTERP_ALT -4, INTERP_ALT\|WATER 0, INTERP_ALT\|SEA 0. The PSLG got
> better (its node *set* is now Ortho4XP's) and the mesh got further, by eleven times: Triangle4XP's
> refinement is order-sensitive, and the ten nodes the PSLG gained renumber everything after
> them. It is a property of the numbering, not a geometric difference, and it is declared
> rather than bounded: `tests/test_p4_integration.py` and `tests/test_review5_fidelite_pslg.py`
> freeze +46 / +92 (they had been loosened from 10 to 50 / 100 without a published
> measurement), and `tests/test_review6_fidelite_synthetique.py` replays both nodings.

Date: 2026-09-12. Machine: Mac M4 Pro 14 cores / 48 GB, macOS 25.6, Python 3.14.7,
numpy 2.5.3, shapely 2.1.2 (GEOS 3.13.1), Triangle4XP from `native/triangle4xp/build`.
Other work was running: 1-minute load 2.6-3.3 during every measurement (`os.getloadavg`),
every run under `nice -n 10`. Tile +43+005 (Marseille), `road_level=1`, `mesh_zl=19`,
`water_simplification=0`, `min_area=0.001`, `max_area=200`, `clean_bad_geometries=True`.

**Zero network.** No Overpass request, no imagery provider, no elevation download: the three
warm `.osm.bz2` caches of the Ortho4XP checkout, and `DemJob(download=no_download)` on both sides
(Ortho4XP's own run fails to fetch the same three View `dem1` cells, so forbidding them keeps the
two sides on the same raster — see section 5).

## 1. The reference: Ortho4XP *without* airports (arbitration A3)

Wave 1 ports the coastline, the water, the roads, the patches and the assembly; airports are wave
2. Comparing its PSLG with the frozen fixture (which has airports) would compare two different
things, so the reference is fabricated: a throw-away Ortho4XP root whose `src`, `Providers`,
`Elevation_data`… are symbolic links to the real checkout and whose `OSM_data` holds the three warm
caches plus an **empty and valid** `+43+005_airports.osm.bz2` (117 bytes, written by
`write_legacy_osm` from an empty snapshot; the writer is in `tests/ortho4xp_osm.py` since decision
0009). Ortho4XP then prints
`* Recycling OSM data from` for all four layers and contacts no server.

```
uv run python .../run_legacy_noapt.py      # LegacyRunner, stage 1, timeout 300 s
```

| | value |
|---|---|
| wall (driver report) | **25.35 s** (25.5 / 27.1 s over two runs; 27.1 s at `verbosity=3`) |
| CPU / max RSS | 24.4 s / 612 MB |
| nodes / edges / seeds | 220 481 / 239 234 / 2 281 |
| edges by marker | DUMMY 38 808, WATER 79 947, SEA 37 327, WATER\|SEA 1 609, INTERP_ALT 81 543 |
| seeds by marker | WATER 1 365, SEA 5, INTERP_ALT 911 |
| markers present | `{0, 1, 2, 3, 8}` — no RUNWAY, TAXIWAY, APRON or HANGAR, as intended |

For reference, the same tile **with** airports (the frozen fixture) has 247 282 nodes,
271 320 edges and 2 615 seeds: the airports are 26 801 nodes, 32 086 edges and 334 seeds,
i.e. 11 % of the PSLG. That is the size of wave 2.

Reproduced by `tests/test_p4_integration.py` (module fixture `reference`).

## 2. Fidelity of `orthostudio.vectors@1` against that reference

`bench_noding.compare`, nodes matched to the nearest reference node within 1.5e-9, edges as
unordered pairs of matched nodes.

| | OrthoStudio XP | Ortho4XP without airports |
|---|---|---|
| nodes | 220 471, **all** matched within 1.5e-9 (219 810 by exact 9-decimal key) | 220 481 |
| nodes only on one side | **0** | 10 |
| reference node pairs closer than 1.5e-9 | | **10** |
| edges | 239 224 | 239 234; **239 194 common**, 30 ours / 40 theirs |
| markers on the common edges | **0 mismatch** | |
| z on the 81 543 nodes of INTERP_ALT-class edges | **0 mismatch > 1e-9** (max 5.0e-10) | |
| z on all common nodes | 9 mismatches, the DUMMY border nodes of the merged pairs | |
| seeds | 1 365 WATER + 5 SEA + 911 INTERP_ALT, max deviation **5.6e-16** | same counts |
| `.poly` holes + seeds section | **byte-identical** (2 283 lines) | |
| `.poly` header | `0 2 1 0`, identical | |
| `.node` header | `220471 2 1 0` | `220481 2 1 0` |
| `Data+43+005.node` | 193 684 of 220 472 lines byte-identical at the same index | |
| planarity (`check_planar`) | **0 violation** | |

The whole residue is the **ten node pairs that Ortho4XP writes 1e-9 apart and the noder merges**
— an accepted difference of P0 (`docs/specs/vectors-pslg.md`, wanted differences). All ten
sit on the south (`y=0`) or north (`y=1`) edge of the tile, at an orthogrid abscissa that
rounds to two values one unit of the 9th decimal apart (`0.405273437` and `0.405273438`), and
every edge touching them is DUMMY. The 30 + 40 uncommon edges are exactly their edges.

**Byte-identity of the `.node` and the `.poly` is therefore out of reach for wave 1 — and it
is out of reach for reasons that have nothing to do with the airports.**

### 2.1 What the reference caught that no unit test could

A first run of the same comparison was worse: 60 nodes in excess and 110 missing, 113 + 163
uncommon edges, 79 `INTERP_ALT` altitudes off by up to 6 cm, and 2 of the 911 road seeds
displaced by 1.9e-6. The cause was one line of `roads.py`: it skipped
`network.difference(improved_buffer(airport_area, ...))` when the airport area was empty,
which in wave 1 is always. The call is *not* a no-op — GEOS re-nodes the left operand, so
`difference(POLYGON EMPTY)` turns the 2 783 banked ways into 2 936 line strings and moves the
buffered outline by 8e-9 deg². Ortho4XP calls it unconditionally; OrthoStudio XP now does too
(`docs/specs/vectors-water-roads.md` 3.3). With the airports injected, as the roads chantier
tested it, the branch was never taken and the bug was invisible: **it is the airport-free
reference of A3 that made it appear.**

## 3. Speed

### 3.1 The stage as a whole

| | Ortho4XP stage 1, no airports | `orthostudio.vectors@1`, no airports | ratio |
|---|---|---|---|
| as a pipeline node (artefact written) | **26.70 s** | **5.13 s** | **x5.2** |
| standalone (best of 3) | 25.35 s | 5.04 s | x5.0 |

For scale, the *same* stage **with** airports is 30.5-32.5 s on this machine
(`docs/benchmarks/baseline-ortho4xp.md`: 31.6 s mean, 30.5 s best; 33.7 s when instrumented in
`noding.md`), so the airports are about 4-6 s of Ortho4XP's stage 1, the elevation smoothing
included. What wave 2 will add to the 5.1 s of OrthoStudio XP is not measured here, and nothing in
this file should be read as predicting it.

### 3.2 Where the 5.0 s of OrthoStudio XP go (best of 3 each)

| step | s | what it does |
|---|---|---|
| elevation (`orthostudio.dem@1`) | 0.07 | 3673² raster, outside the 5.04 s (its own node) |
| read `big_roads` | 0.89 | 163 297 nodes, 25 750 ways (bz2 decompression is half of it) |
| read `water` | 0.34 | 117 298 nodes, 4 734 ways, 113 relations |
| read `coastline` | 0.10 | 39 545 nodes, 424 ways |
| build roads | **1.80** | banking test on 25 750 ways, buffer, 911 polygons |
| build water | 0.39 | 4 271 polygons -> merge -> 1 365 encoded |
| build coastline | 0.14 | 427 lines, 165 islands, 5 seeds |
| patches | 0.00 | +43+005 has none |
| **layers total** | **3.76** | `orthostudio.vectors.layers.build_layers` |
| grid + border + noding + seeds | **0.83** | of which `node_layers` 0.83 |
| write `.node` + `.poly` + `layers.npz` | 0.45 | `np.savetxt` dominates (ADR 0004 would remove it) |

The noding, hot spot n°1 of Ortho4XP, is 0.83 s here against the 23.13 s of `insert_way` measured
in P0 on the same tile (`docs/benchmarks/noding.md`) — that is where the gain comes from. The
layer builders are at parity with Ortho4XP (measured by their own chantiers: coastline x1.11,
water merge x1.9, OSM reader x1.01 on the `.osm.bz2` path), so the honest figure for the
stage is **x5, not x10**: 3.8 s of the 5.0 s is geometry that Ortho4XP also spends. The next
lever is the road banking test (1.53 s of the 1.80 s: two `alt_vec` and one
`weighted_normals` per way, vectorisable across ways).

### 3.3 The whole chain to the DSF (textures excluded)

`declare(...)` up to the DSF node, `--stages native`, one scheduler, same machine.

| node | `--vectors legacy` | `--vectors native` |
|---|---|---|
| dem | — (inside stage 1) | 0.12 |
| coastline | 0.12 | 0.16 |
| xp12 rasters | 0.41 | 0.40 |
| **vectors** | **26.70** | **5.13** |
| mesh | 2.49 | 2.51 |
| masks | 0.92 | 0.93 |
| dsf | 1.75 | 1.76 |
| **wall of the graph** | **31.86 s** | **10.45 s** (**x3.0**) |

## 4. What the rest of the chain does with those vectors

Both sides: same Triangle4XP, same options, same coastline weight map, same elevation.

| artefact | Ortho4XP vectors (no airports) | OrthoStudio XP vectors (no airports) |
|---|---|---|
| mesh vertices | 567 290 | 567 286 |
| mesh triangles | 1 125 050 | 1 125 052 |
| mesh md5 | `49089c41…` | `1ff1eca7…` — **not identical** |
| triangles by attribute | 0: 844 623, 1: 107 695, 2: 81 950, 3: 8 769, 8: 81 822, 9: 94, 10: 97 | 0: 844 592, 1: 107 699, 2: 81 949, 3: 8 803, 8: 81 818, 9: 94, 10: 97 |
| altitude range | −9.6411383422 … 1251.7556685727 | identical |
| masks | 7 PNG | same 7 names, 5 byte-identical; the other two differ on 343 pixels of 33.6 M (0.001 %), amplitude ≤ 8/255 |
| `.ter` | 39 | same 39 names, **all byte-identical** |
| DSF | 31 486 270 B | 31 487 232 B (+962, +0.003 %); first difference `DEFN/TERT`, the **order** of the 40 terrain names (the set is the same) |

> **Replayability (review 5).** The mesh and mask lines of this table are re-run by the
> repository: `tests/test_review5_fidelite_pslg.py` rebuilds both meshes and reconstructs the
> seven masks from them and confirms them (5 of 7 byte-identical, 343 pixels, delta <= 8).
> The **`.ter` and DSF lines are a one-off measurement** made in a scratchpad script that is
> not in the repository: no test replays them today. Treat them as a recorded observation,
> not as a checked invariant; re-measuring them means building both DSFs from the two PSLGs
> and comparing with `orthostudio.oracle.compare_dsf` / `compare_ter`, which needs the ZL14 imagery
> of the fixture and a few minutes.

Why the mesh differs, and why it is not the elevation: four meshes were built, crossing the
two PSLGs with the two rasters.

| | Ortho4XP raster | OrthoStudio XP raster |
|---|---|---|
| Ortho4XP PSLG | md5 `49089c41…` | md5 `49089c41…` |
| OrthoStudio XP PSLG | md5 `1ff1eca7…` | md5 `1ff1eca7…` |

**The raster changes nothing** (the two rasters differ on 204 samples of 13 490 929, max
1.5e-5 m, all in columns 3-5 of the −0.01…1.01 window, i.e. outside the tile), **the PSLG
changes everything downstream**: the ten merged nodes renumber Triangle's input file, and its
refinement is order-sensitive, so the Steiner points move. Measured on the two meshes:

* 220 031 of the 220 471 PSLG nodes are mesh vertices on both sides (440 are dropped on each
  side by the same rule), and the Steiner points are 346 815 against 346 809, **326 899 of
  them at the same place** (94.3 %);
* 546 269 horizontal positions are shared; the altitude differs on 107 425 of them, but by
  **at most 4.03 m** (337 above 1 m, 18 639 above 0.1 m, the rest below) on a tile whose
  relief spans 1 261 m. The cause is `water_smoothing=10`, which iterates over the mesh graph
  whose neighbourhoods the extra Steiner points change.

So: **wave 2 is not the only obstacle left.** Two remain, and this is the measurement that
separates them:

1. **the airports (wave 2)** — 11 % of the PSLG on this tile, plus
   `smooth_raster_over_airports` (21 719 raster samples, up to 13.57 m), plus
   `Data<tile>.apt` that `cover_airports_with_highres` and the mesh weight map read;
2. **the ten merged duplicate nodes (P0)** — 0.005 % of the PSLG, but enough to make the
   mesh, the masks and the DSF non-byte-identical. Reaching byte-identity end to end would
   mean the noder keeping two nodes 1e-9 apart apart, which `vectors-pslg.md` decided against.
   That decision now has a measured price: an equivalent-but-different mesh and DSF.
   Review 5 measured the lever: it is **not** a keying rule (the snapped keys of the ten pairs
   already differ, so no keying change can act on them) but the axis-aligned substitution of
   `noding.py:274-277`; replaying without it recovers 8 of the 10 nodes and 8 of the 10 edges,
   and 2 remain unexplained. See `vectors-pslg.md` 2.8.

## 5. Elevation: what the native DEM really costs

`docs/specs/pipeline-build.md` 8.2 says the native raster and the one Ortho4XP writes differ on
21 923 samples of 13 490 929 (up to 13.57 m). This run splits that figure:

| | differing samples | max |
|---|---|---|
| `orthostudio.dem@1` vs Ortho4XP **without** airports | 204 (0.0015 %), columns 3-5 only, outside the tile | 1.5e-5 m |
| Ortho4XP without airports vs the fixture (with airports) | 21 719 | 13.57 m |
| `orthostudio.dem@1` vs the fixture | 21 923 | 13.57 m |

`smooth_raster_over_airports` is **the whole** difference; the 204 remaining samples never
reach Triangle4XP (proved above: the mesh is byte-identical across the two rasters). So
`--dem native` is not a fidelity risk in itself — the airport smoothing of wave 2 is.

Network note: building the raster attempts three View `dem1` archives (`n43e006`, `n44e005`,
`n44e006`) that the local checkout holds only at 3″; Ortho4XP attempts exactly the same three and
fails too (verified: the files' mtimes are unchanged and `N44E006.hgt` still does not exist).
Both benchmarks therefore run with the network forbidden, which is what makes them equal.

## 6. Non-regression

`uv run pytest tests/test_p3_oracle.py -q -o addopts=''` — 5 passed in 37.4 s: with the
default `--vectors legacy`, the ZL14 DSF, the mesh, the masks and the 39 `.ter` of +43+005
are still **byte-identical** to the frozen Ortho4XP build. The `--vectors` option changes nothing
for a build that does not ask for it.

## 7. How to re-run

```sh
# the whole P4 integration, reference fabrication included (about 60 s)
uv run pytest tests/test_p4_integration.py -q -o addopts=''
# the P3 non-regression (the default chain is untouched)
uv run pytest tests/test_p3_oracle.py -q -o addopts=''
# a native build, from the Ortho4XP warm OSM cache
uv run osxp build --tile +43+005 --provider BI --zl 14 \
    --vectors native --stages native --legacy-dir /path/to/Ortho4XP --dry-run
```
