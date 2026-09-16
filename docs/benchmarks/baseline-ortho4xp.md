# Ortho4XP baseline, tile +43+005 at ZL14 (Bing), warm caches, no profiler

Date: 2026-09-12. Machine: Apple M4 Pro, 14 cores, 48 GB, macOS 26.6.2, Python 3.14.7 in the
legacy `.venv`. Ortho4XP at commit `26ec00acce4a2b42b79d2b618e491c1310f5d0b0`
(`v1.31-91-g26ec00a`, "Update OSM provider configuration (#301)", 2026-03-14), unmodified
tracked files, `Ortho4XP.cfg` with `max_convert_slots=4`, `custom_overlay_src` pointing at the
X-Plane 12 Global Scenery (DEMS/DEMN extraction with 7z).

This is the number OrthoStudio XP has to beat. It is the reference for the "x10" target of
`docs/plan/PLAN.md`; the profiled figure of `docs/plan/DIAGNOSTIC.md` (108.9 s) is explained
below and must not be used as the denominator.

## Scenario

"Warm caches, outputs deleted": the OSM extracts (`OSM_data/+40+000/+43+005/*.osm.bz2`,
6.7 MB), the DEM (`Elevation_data/`, viewfinderpanoramas) and the JPEG imagery cache
(`Orthophotos/+40+000/+43+005/BI_14/`, 57 MB) are on disk; the tile directory
`zOrtho4XP_+43+005` is deleted before each run; the mask cache (`Masks/`) is rebuilt by Ortho4XP
itself at every run (it has no cache key). The four stages are called in-process, headless,
in the order the GUI uses: `build_poly_file`, `build_mesh`, `build_masks`, `build_tile`.
Each stage is timed with `time.perf_counter` and `resource.getrusage` (self and children),
without cProfile. Nothing else of note was running on the machine (load average 1.8 to 2.3).

Commands (the original runs used the ancestor of this script, `baseline.py`, same
measurement code; the script below reproduces them):

```bash
export OSXP_LEGACY_DIR=../Ortho4XP
uv run python tools/oracle/run_legacy.py --lat 43 --lon 5 --provider BI --zl 14 \
    --build-dir "$SCRATCH/CustomScenery" --out "$SCRATCH/run.json" --repeat 3
uv run python tools/oracle/summarize_runs.py "$SCRATCH"/run_*.json
```

## Result: median of 3 runs

| Stage | Wall median (s) | min / max | CPU self (s) | CPU children (s) | Cores | Max RSS (MB) |
|---|---:|---:|---:|---:|---:|---:|
| build_poly_file (step 1, OSM vectors, .poly/.node) | 31.6 | 30.5 / 32.5 | 30.5 | 0.0 | 0.97 | 662 |
| build_mesh (step 2, DEM + Triangle4XP, .mesh) | 7.5 | 7.4 / 7.5 | 4.7 | 2.4 | 0.95 | 662 |
| build_masks (step 2.5, 7 masks, 4 threads) | 4.3 | 4.2 / 4.3 | 9.0 | 0.0 | 2.10 | 1192 |
| build_tile (step 3, DSF + 17 DDS via nvcompress) | 19.9 | 19.7 / 20.0 | 21.8 | 44.2 | 3.31 | 2029 |
| **total** | **63.2** | 61.9 / 64.3 | 66.0 | 46.7 | 1.78 | 2029 |

Run-to-run spread is 4 % on the total (61.9 to 64.3 s). "Cores" is CPU time (self plus
children) divided by wall time; the children are Triangle4XP (step 2), 7z and nvcompress
(step 3). Raw reports: `run1.json`, `run2.json`, `run3.json` in the session scratchpad; the
outputs of run 3 are frozen in `fixtures/oracle/+43+005_zl14_BI/manifest.json`.

Outputs: DSF 32.4 MB (atoms HEAD 92 B, DEFN 1.5 kB, GEOD 15.2 MB, CMDS 9.9 MB, DEMS 7.3 MB),
17 DDS textures 4096x4096 with 13 mip levels (10 DXT1 at 11.18 MB, 7 DXT5 at 22.37 MB, 268 MB
in all), 39 `.ter`, 7 mask PNGs 4096x4096, intermediates `.mesh` 69 MB, `.alt` 54 MB, `.node`
10.9 MB, `.poly` 6.0 MB, `.apt` 103 kB. Total on disk 421 MiB, 64 files in the tile directory.

## Why DIAGNOSTIC.md says 108.9 s

`docs/plan/DIAGNOSTIC.md` was measured under `cProfile` on the same tile, same caches, the
day before. cProfile instruments every Python function call, and Ortho4XP is dominated by very
small calls in very long loops (12.6 M `struct.pack` in the DSF writer, 7.4 M `str.format`
in the mesh text I/O, 330 k rtree/`are_encroached` calls in the vector step), so the
profiler roughly doubles the Python-bound stages while leaving the native children
(Triangle4XP, nvcompress, 7z) untouched:

| Stage | Profiled (DIAGNOSTIC) | Unprofiled (this file) | Ratio |
|---|---:|---:|---:|
| build_poly_file | 42.2 s | 31.6 s | 1.34 |
| build_mesh | 10.3 s | 7.5 s | 1.37 |
| build_masks | 6.6 s | 4.3 s | 1.53 |
| build_tile | 49.5 s | 19.9 s | 2.49 |
| total | 108.9 s | 63.2 s | 1.72 |

`build_tile` suffers most because the pure-Python DSF encoder (about 35 s profiled, some
8 s unprofiled) is nothing but tiny calls, and because under the profiler it no longer
overlaps with the 4 nvcompress slots. The relative picture of the DIAGNOSTIC (where the
time goes, what is single-threaded) stays valid; its absolute numbers are inflated.

Consequences for the plan:

- Target "x10" is measured against **63 s**, that is about **6 s** for this tile with warm
  caches, and against the corresponding unprofiled figures for the other 5 reference tiles.
- The machine is idle 82 % of the time even in this unprofiled run (1.78 cores of 14):
  the two single-threaded Python stages (steps 1 and 2, 39 s, 0.97 cores) are 62 % of the
  wall time.
- The step-3 children (44 s of CPU for nvcompress under Rosetta plus 7z) exceed the wall
  time of the step: DDS encoding is already parallel in Ortho4XP, so the gain there comes from
  a faster encoder (ispc_texcomp, 53 to 64 ms per texture measured in ADR 0003), not from
  more parallelism.

## Reproducibility of the Ortho4XP outputs (fourth run)

A fourth run was made with `tools/oracle/run_legacy.py` itself (`nice -n 10`) while other
work was running on the machine (load average 5.0 before, 7.1 after; the baseline runs were at
1.8 to 2.3): 66.0 s in all (build_poly_file 33.5 s, build_mesh 8.1 s, build_masks 4.4 s,
build_tile 20.1 s), 4 % above the median, which bounds the sensitivity of this benchmark to
background load at this level.

Its outputs were checked against the frozen manifest with `orthostudio.oracle`:

- the 64 files of the tile directory (DSF, 17 DDS, 39 `.ter`, tile cfg, `.mesh/.node/.poly/
  .alt/.apt`) are **byte-identical** to the baseline (same sha256), and so are the 7 mask
  PNGs rebuilt in `Masks/`;
- `compare_dsf` finds no difference (MD5 footer valid on both), `compare_dds` gives PSNR
  infinite and SSIM 1.0 on all 13 mip levels of a DXT1 and a DXT5 texture (nvcompress is
  deterministic), `compare_ter` is identical.

So Ortho4XP with warm caches is deterministic on this tile, and the manifest can be used as a strict
byte-level oracle for the stages OrthoStudio XP reimplements (DSF, `.ter`, masks, intermediates);
only the DDS comparison has to go through PSNR/SSIM, because OrthoStudio XP uses a different
encoder. Cost of the comparators on 4096x4096 textures: about 0.5 s to decode 13 levels, 3 to 4.5 s
for `compare_dds` with SSIM on every level (`max_levels` limits it).

## ZL16 on the same tile (single run, cold imagery cache)

Date: 2026-09-12, same machine, same Ortho4XP commit, same `Ortho4XP.cfg`. One run with
`tools/oracle/run_legacy.py --zl 16` (`nice -n 10`), while a P1 work package was being
written on the machine (load average 2.0 before, 6.1 after; 2.4 ten minutes later). The
`Orthophotos/+40+000/+43+005/BI_16/` cache was **cold**: step 3 downloaded the 179 textures
(45 824 Bing tiles at 256 px, 656 MB of JPEG) and converted them in the same stage, so the
step-3 figure is "download + convert" and is not comparable with the ZL14 line above. Steps
1, 2 and 2.5 do not depend on the zoom level and reproduce the ZL14 medians within 2-5 %.
Three `.hgt` files of neighbouring cells (N43E006, N44E005, N44E006) were fetched at the
start of step 1, which did not change its duration.

Report: `fixtures/large/oracle/+43+005_zl16_BI/runs/run1.json` (+ `.log`); outputs flattened
into `fixtures/large/oracle/+43+005_zl16_BI/build/` (same layout as the ZL14 fixture) and
frozen in `fixtures/oracle/+43+005_zl16_BI/manifest.json` (356 `.ter` and the tile cfg copied).

| Stage | Wall (s) | CPU self (s) | CPU children (s) | Cores | Max RSS (MB) |
|---|---:|---:|---:|---:|---:|
| build_poly_file | 31.8 | 30.9 | 0.0 | 0.97 | 659 |
| build_mesh | 7.8 | 4.9 | 2.5 | 0.95 | 659 |
| build_masks (mask_zl stays 14: 7 masks, byte-identical to ZL14) | 4.3 | 8.9 | 0.0 | 2.08 | 1124 |
| build_tile (DSF + 179 textures: download **and** nvcompress) | 206.9 | 94.7 | 436.3 | 2.57 | 1817 |
| **total** | **250.7** | 139.4 | 438.8 | 2.31 | 1817 |

Outputs: DSF 32.5 MB (HEAD 92 B, DEFN 12.9 kB, GEOD 15.2 MB, CMDS 9.9 MB, DEMS 7.3 MB),
179 DDS (152 DXT1 + 27 DXT5, 2.30 GB), 356 `.ter` (178 land, 151 `_water_overlay`, 27
`_sea_overlay`; one texture is sea-only), intermediates identical in size to ZL14
(`.mesh` 69 MB, `.alt` 54 MB, `.node` 10.9 MB, `.poly` 6.0 MB). 2.36 GiB on disk, 543 files.
The 7 masks of `Masks/+40+000/+43+005/` have the same sha256 as the ZL14 capture (mask_zl
is 14 in both runs), so they were not copied again.

What the step-3 number says and does not say:

- The Ortho4XP log has no timestamps, so download and conversion cannot be separated. The
  download queue (16 threads, one texture at a time) finished just before the last of the
  179 conversions, and the 4 nvcompress slots needed 436 s / 4 = 109 s of CPU in all, so the
  stage was bound by the download for most of its 207 s: about 45 800 tiles in roughly
  200 s, that is **~220-230 requests per second**, in line with `docs/benchmarks/network.md`
  (Ortho4XP downloads one texture at a time with 16 threads).
- `max_convert_slots=4` leaves the conversion CPU at 2.1 cores average over the stage; the
  self CPU (94.7 s) is the DSF encoder (about 8 s at ZL14, larger here because of the 356
  terrain definitions and 179 textures) plus the JPEG decoding, mask imprint and PNG
  round-trip done in Python for the 27 DXT5 textures.
- For the P1 target this gives the two numbers to beat with warm caches deleted outputs:
  the ZL14 line (**19.9 s** for 17 textures, 63.2 s in all) and, once the ZL16 cache is
  warm, a rerun of this stage (not measured here: the run was made once to keep the network
  load polite; the JPEG cache is now warm at `BI_16/` for a later warm-cache run).
