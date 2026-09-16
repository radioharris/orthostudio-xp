# Noding of the vector layers (hot spot n°1 of Ortho4XP)

Date: 2026-09-12. Machine: Mac M4 Pro 14 cores / 48 GB, macOS 25.6, Python 3.14.7,
numpy 2.5.3, shapely 2.1.2 (GEOS 3.13.1). Other work was running: 1-minute load 3.0-4.4
during every measurement (`os.getloadavg`), all runs under `nice -n 10`.
Tile: +43+005 (Marseille), Ortho4XP configuration of the baseline (`road_level=1`,
`mesh_zl=19`, `water_simplification=0`, `min_area=0.001`, `max_area=200`).

## What was measured

1. **Legacy reference.** `tools/bench/noding/record_legacy_layers.py` runs
   `O4_Vector_Map.build_poly_file` with warm OSM/DEM caches, records every `insert_way`
   call (6 197 ways, 236 090 vertices, 229 893 segments, 100 consecutive runs of equal
   marker) and times it:

   | | s |
   |---|---|
   | `insert_way` total (insert_node + insert_edge + rtree) | **23.13** |
   | `snap_to_grid(9)` | 0.90 |
   | whole `build_poly_file` (OSM parsing, buffering, DEM sampling, files included) | 33.71 |
   | same step, baseline runs 1-3 without instrumentation (`baseline/run*.json`) | 30.5 / 30.4 / 30.9 |
   | `insert_edge` alone under cProfile (DIAGNOSTIC.md) | 28.6 |

   The `.node`/`.poly` it writes are byte-identical to the baseline's.
2. **OrthoStudio XP `node_layers`** (`src/orthostudio/vectors/noding.py`),
   `tools/bench/noding/bench_noding.py`, `--repeat 3 --synthetic 200000`, results in
   `bench_result.json` (scratchpad `p0/noding/`):

   | run | input | layers | wall s (3 runs) | best |
   |---|---|---|---|---|
   | replay of the recorded Ortho4XP layers | 229 893 segments | 100 | 1.188 / 1.138 / 1.144 | **1.14** |
   | identity (legacy PSLG fed back, one layer per marker) | 271 320 edges | 8 | 1.102 / 1.093 / 1.081 | 1.08 |
   | synthetic un-noded set (roads, coast walks, 2 000 lakes, 110x110 grid, 4x2048 border) | 199 852 segments -> 377 141 nodes, 553 354 edges | 6 | 1.805 / 1.808 / 1.803 | 1.80 |

   Speed-up on the same machine and load: **23.1 s -> 1.14 s, x20** on the insertion
   itself; step 1 of Ortho4XP as a whole (33.7 s) is left with the layer builders (~9 s) to port.

   Where the 1.14 s goes (cProfile of the replay): the 96 small airport passes 0.48 s
   (~5 ms each: STRtree build + `np.unique`), WATER 0.20 s, border 0.18 s, grid 0.28 s
   (two passes over the whole graph), INTERP_ALT 0.13 s, SEA 0.09 s. Half of the total is
   `np.unique`/`argsort` (node identity and edge dedup over the whole graph at every pass);
   packing node keys in one int64 instead of a structured dtype already cut 1.86 s to 1.14 s.
   Next gains, if ever needed: re-chain only the old edges that received an event
   (the other ones carry their node ids over), and merge the 96 airport passes.

## Fidelity of the replay against the Ortho4XP `.node`/`.poly`

Comparison as sets (`bench_noding.compare`): nodes matched to the nearest legacy node within
1.5e-9, edges as unordered pairs of matched nodes.

| | |
|---|---|
| OrthoStudio XP nodes | 247 272, **all** matched within 1.5e-9; 246 638 (99.74 %) with the exact 9-decimal key |
| legacy nodes | 247 282; the 10 unmatched ones are 10 pairs of legacy nodes 1e-9 apart (a grid crossing and the border vertex it coincides with, rounded on different sides of a 9th-decimal tie); OrthoStudio XP merges each pair |
| edges | 271 280 common of 271 320 legacy / 271 310 OrthoStudio XP; the 40 / 30 others are the edges of those 10 pairs |
| markers on common edges | 0 mismatch |
| z on the 107 230 nodes of INTERP_ALT-class edges | **0 mismatch > 1e-9** (max 5.0e-10) |
| z on all common nodes | 9 mismatches, all DUMMY border nodes of the merged pairs (z of the crossing kept, Ortho4XP kept the vertex's) |
| planar invariant on the OrthoStudio XP output (`check_planar`) | 0 violations |
| identity run | exact: same 247 282 nodes, 271 320 edges, z and markers |

The 634 nodes matched within 1e-9 but not by exact key are grid crossings whose true abscissa is a
9-decimal tie (`k/2048`, ZL-19 grid columns): Ortho4XP rounds its floating-point noise either way,
OrthoStudio XP takes the exact grid coordinate and rounds half-to-even. Mesh-wise the difference is
0.1 mm.

## What this bench does not prove

* **One tile.** Marseille has coast, airports, roads at level 1 and a ZL-19 grid; no
  SEA_EQUIV, APRON, patches, custom coastline, `road_level >= 2`, simplified water
  (`water_simplification > 0` produces self-touching polygons) or high-latitude anisotropy.
  The 6-tile P0 set is the real acceptance.
* **Same-layer chain interpolation.** OrthoStudio XP interpolates a same-layer crossing on the
  earlier way's original segment, Ortho4XP on its current piece; identical here, not proven in
  general (see spec §2.4).
* **Parallel-but-not-collinear pairs are not split** (Ortho4XP rule kept); a tile where two
  near-parallel edges actually cross would leak in both tools.
* **Triangle4XP downstream.** The comparison stops at the PSLG; Triangle's output on a
  PSLG whose nodes are permuted or moved by 1e-9 is not bit-identical (PLAN level B), and
  the mesh comparison (triangles ±5 %, altitude RMS) is the oracle harness's job.
* **Timing under load.** Load 3-4 from other work; the numbers are upper bounds.
* **Synthetic set.** Checks speed and planarity only; there is no reference for it.

## Re-run

```bash
cd $OSXP_LEGACY_DIR && PATH=/opt/homebrew/bin:$PATH nice -n 10 .venv/bin/python \
    /path/to/orthostudio/tools/bench/noding/record_legacy_layers.py <data_dir> 43 5
cd /path/to/orthostudio-xp && nice -n 10 uv run python tools/bench/noding/bench_noding.py \
    --data <data_dir> --repeat 3 --synthetic 200000 --out result.json
OSXP_NODING_ORACLE_DIR=<data_dir> uv run pytest tests/test_vectors_*.py -q
```
