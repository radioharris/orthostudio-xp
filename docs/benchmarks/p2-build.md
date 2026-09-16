# P2a `osxp build` versus Ortho4XP, tile +43+005 (Bing)

Date: 2026-09-12, 10:40-10:55 local. Machine: Mac M4 Pro (14 cores, 48 GB), macOS 25.6.0,
Python 3.14.7, ispc_texcomp 1.0.1, curl_cffi 0.16 (HTTP/2), DSFTool 2.3.0-b2 universal.
Every run under `nice -n 10`; uptime 5 days 15 h; load average before each run is in the
tables (other work was running: 2.7-4.4). Scheduler: 12 CPU workers, 3 subprocess slots,
1 net slot, 2 io slots, RAM budget 60 % of 48 GB.

Reference: `docs/benchmarks/baseline-ortho4xp.md` (ZL14, 63.2 s median of 3, warm caches) and the
ZL16 run of the same Ortho4XP commit (`fixtures/large/oracle/+43+005_zl16_BI/runs`: 250.7 s =
vectors 31.8, mesh 7.8, masks 4.3, DSF + DDS 206.9). Both tools use the Ortho4XP caches for OSM, DEM
and the vector/mesh/mask stages (in P2 OrthoStudio XP ran them as Ortho4XP subprocesses, until
decision 0009 removed that path); OrthoStudio XP's own store and chunk store were **empty** before run A, so
every Bing tile was downloaded (the Ortho4XP ZL14 figure has its JPEG cache warm, its ZL16
figure cold).

Commands (the driver script and every JSON report live in the session scratch directory
`p2/integration/`: `validate.py`, `validate-*.json`, `validate.log`; the CLI equivalent of
run A is below):

```bash
export OSXP_HOME=<scratch>/home2
nice -n 10 uv run osxp build --tile +43+005 --provider BI --zl 14 --out <scratch>/out2 \
    --legacy-dir ../Ortho4XP --osm-snapshot 2026-09-12
nice -n 10 uv run python <scratch>/p2/integration/validate.py all
```

Outputs of every run were compared with the frozen Ortho4XP builds (`compare_dsf`, `.ter` bytes,
DDS sizes, overlay MD5): see section 4.

## 1. One tile, cold OrthoStudio XP store (run A, ZL14) and second run (B)

| Node | kind | Ortho4XP stage (s) | OrthoStudio XP (s) | Note |
|---|---|---:|---:|---|
| `+43+005/vectors` (legacy.vectors) | subprocess | 31.6 | 30.95 | Ortho4XP step 1 in a subprocess, OSM cache warm |
| `+43+005/mesh` (legacy.mesh) | subprocess | 7.5 | 8.32 | step 2 + `mesh.npz` |
| `+43+005/masks` (legacy.masks) | subprocess | 4.3 | 4.59 | step 2.5, 7 masks |
| `+43+005/xp12` (xp12.rasters) | io | (in step 3, 7z) | 0.43 | py7zr in memory, overlapped |
| `+43+005/overlay` (tile.overlay) | subprocess | 3.0 (step 4) | 3.02 | DSFTool twice, overlapped with step 1 |
| `+43+005/BI14/dsf` (tile.dsf) | cpu | ~8-10 (in step 3) | 1.44 | numpy encoder, worker process |
| `+43+005/BI14/textures` (tile.textures) | net | ~10-12 (in step 3) | 4.37 | 4 352 tiles at 1 306 req/s (63 MB, 3.4 s) overlapped with 17 encodes (3.5 s) |
| `+43+005/BI14/pack` (tile.pack) | io | manual | 0.02 | hard links + `orthostudio.toml` |
| **total wall** | | **63.2** (+ install by hand) | **49.9** | load 2.66 before, 3.19 after |
| **second run, nothing changed (B)** | | 63.2 (no cache) | **0.01** (0.29 s for the whole CLI process) | 8 hits, no subprocess, no request |

The critical path is the legacy chain (31 + 8.3 + 4.6 = 44 s of the 49.9 s): the overlay and the
rasters run in parallel with step 1, the DSF and the textures follow the masks. The osxp-native
part (DSF + textures + pack) takes 5.8 s where Ortho4XP's step 3 took 19.9 s with a warm JPEG
cache. The x10 target of `PLAN.md` waits for P3/P4 (native vectors, mesh and masks); P2a's
gain on a single cold tile is x1.27, and infinity on an unchanged one.

## 2. ZL16 (run C, store warm for the legacy nodes, chunks cold)

| Node | Ortho4XP (s) | OrthoStudio XP (s) |
|---|---:|---:|
| vectors, mesh, masks, xp12, overlay | 31.8 + 7.8 + 4.3 + (7z) + (overlay) | 5 hits, 0.00 |
| `+43+005/BI16/dsf` | in the 206.9 of step 3 | 1.54 |
| `+43+005/BI16/textures` (179 textures, 45 824 tiles, 741 MB at 1 521 req/s) | in the 206.9 | 31.14 (fetch 30.2 overlapped with encode 30.1) |
| `+43+005/BI16/pack` | | 0.17 |
| **total** | **250.7** | **33.1** (x7.6; x6.2 against step 3 alone) |

A user who has built the tile once at ZL14 gets it at ZL16 in 33 s: the DSF depends on the
mesh, masks and rasters digests and on `default_zl`, the textures on the DSF; nothing else moves.

## 3. Partial rebuilds and batches

| Run | Hits | Built | Wall (s) | Detail |
|---|---|---|---:|---|
| D: `masks_width=200` (ZL14) | vectors, mesh, xp12, overlay | masks 5.11, dsf 1.64, textures 1.10, pack 0.04 | **8.2** | inside the textures node: 7 coastal DDS re-encoded, 10 hits, 0 tiles fetched |
| E: `--install` on a copy of Custom Scenery | 8 | install 0.06 | 0.1 | two symlinks, `scenery_packs.ini` ordered (`yAutoOrtho_Overlays` > `yOrtho4XP_Overlays` > `zOrtho4XP_+43+005` > `z_ao_eur` > `z_autoortho`), `.bak`, 2 library rows |
| F1: batch `+43+005` + `+43+004` (ZL14, `+43+004` cold: OSM through the Ortho4XP Overpass client) | see below | | | |
| F2: batch `+43+005` at ZL14 + ZL15 | vectors, mesh, masks, xp12, overlay, BI14/dsf, BI14/textures, BI14/pack | BI15/dsf 1.66, BI15/textures 14.95 (56 textures, 14 336 tiles, 1 134 req/s), BI15/pack 0.06 | **17.0** | the five tile-level nodes are one object for both specs |

**F1, the cold neighbour.** `+43+004` is not in the Ortho4XP OSM cache, so its stage 1 goes
through the Ortho4XP Overpass client (`overpass-api.de`, mirror `DE` in the checkout's cfg). The
mirror answered "rejected" then "too busy" with doubling waits (2, 4, ... 128 s) for the whole
run; with the 8 min cap (`stage1_timeout_s=480`) the node failed with `LegacyStageTimeout`
after 480.2 s (first attempt, 10:45-10:53), and with a 2 min cap after 120.2 s (second
attempt, 10:54). Meanwhile `+43+005/vectors` and `mesh` were built (33.0 s, 8.7 s: the tile's
keys had changed, see section 6) in parallel with the waiting neighbour, and `+43+004/xp12`
and `overlay` succeeded (0.3 s, 2.3 s: they only need the Global Scenery). The first attempt
exposed a cascade: `+43+005/masks` takes `+43+004/mesh` as its `nb_w` input, so the
neighbour's failure skipped `+43+005` entirely. The second pass of `build_tiles` (spec
`pipeline-build.md` 4) now rebuilds such a tile with the failed neighbour treated as absent:
in the second attempt `+43+005` was reported `ok` (all hits, the masks without neighbour
already in the store) and `+43+004` `failed` with `SYS_INTERNAL_ERROR` on `vectors` and the
other seven nodes `skipped` with `cause=+43+004/vectors`. This is the PLAN.md risk "Overpass"
as measured today: a cold tile cannot be built through the Ortho4XP client on this network; P4's
own OSM client with mirrors and the land-polygons coastline is the fix, not a P2 setting.

## 4. Fidelity of the outputs (oracle)

| Run | DSF | `.ter` | DDS | Overlay |
|---|---|---|---|---|
| A (ZL14) | identical atom by atom to the Ortho4XP reference except `sim/creation_agent` (the agent's name was 5 bytes shorter than `Ortho4XP`: 32 442 865 vs 32 442 870); with `--creation-agent Ortho4XP` the bytes and the MD5 footer are identical (`tests/test_dsf_oracle.py`) | 39/39 identical | 17/17, same sizes and formats (7 DXT5, 10 DXT1); PSNR/SSIM gate of P1 unchanged (same `texture.dds` rule) | MD5 `8116be8821b41a66f90d031d8a683899` = Ortho4XP |
| C (ZL16) | identical except the agent (32 515 268 vs 32 515 273) | 356/356 | 179/179 | idem |

## 5. Notes on the runs

* Runs A-E were measured with the first version of the graph, whose legacy nodes did not carry
  the tile in their key; the batch run F1 revealed it (`+43+004/vectors` hit `+43+005`'s
  artefact) and the params models now include `tile`. Keys changed, timings did not: the
  stage code, inputs and outputs are the same.
* Load average was 2.7-4.4 during the runs (other work on the machine), against 1.8-2.3 for the
  Ortho4XP baseline: the OrthoStudio XP figures are, if anything, pessimistic.
* Everything measured through `build_tiles` in a script; the CLI adds about 0.3 s of process
  start-up (`osxp build` second run: 0.29 s wall for the whole process).

## 6. Where the time goes now

Of the 49.9 s of a cold ZL14 tile, 44 s are the three Ortho4XP stages run unchanged in
subprocesses (88 %). Everything osxp-native is either overlapped (overlay, rasters) or short
(DSF 1.4 s, textures 4.4 s for a cold chunk store, pack 0.02 s). The per-texture cost learnt by
the scheduler (`sched.cost.tile.textures/texture`) is 0.24-0.27 s of wall per texture
(download and encode overlapped), which `osxp plan` uses for its compute estimate.

The scheduler overhead is invisible at this scale (spec `scheduler.md` 5: ~0.5 ms per node).
The second-run figure (0.01 s inside `build_tiles`, 0.29 s for the CLI process) is bounded by
the key computation of eight nodes and one sqlite round trip each.

## 7. Correction round (P2a review), final run G

Date: 2026-09-12, 11:52 local, same machine, `nice -n 10`, uptime 5 days 16 h 35, load average
2.92 before / 3.72 after. `OSXP_HOME` fresh (store and chunk store **empty**), Ortho4XP caches warm:

```bash
OSXP_HOME=<scratch>/p2/final/home nice -n 10 uv run osxp build --tile +43+005 --provider BI \
    --zl 14 --out <scratch>/p2/final/out --legacy-dir ../Ortho4XP \
    --creation-agent Ortho4XP --json
```

No `--osm-snapshot`: the stage-1 key carries the new default label of the OSM cache state
(`cache-<12 hex>`, `pipeline-build.md` 2.1), and the overlay node carries the checkout's
`Ortho4XP.cfg` exclusions (`ovl_exclude_pol=[0]`, `ovl_exclude_net=[]`).

| Node | G (s) | A (s, section 1) |
|---|---:|---:|
| `+43+005/vectors` | 31.99 | 30.95 |
| `+43+005/mesh` | 8.48 | 8.32 |
| `+43+005/masks` | 4.56 | 4.59 |
| `+43+005/xp12` | 0.43 | 0.43 |
| `+43+005/overlay` | 3.16 | 3.02 |
| `+43+005/BI14/dsf` | 1.49 | 1.44 |
| `+43+005/BI14/textures` | 4.35 | 4.37 |
| `+43+005/BI14/pack` | 0.02 | 0.02 |
| **scheduler elapsed / CLI wall** | **51.2 / 51.4** | 49.9 |
| second run, nothing changed | 0.0 (8 hits; 0.30 s for the CLI process) | 0.01 |

Fidelity (script `p2/final/validate_final.py`, JSON `validate.json`): DSF **byte-identical** to the
frozen Ortho4XP build, MD5 `9763ba632190af2c46cf0fdceaca1ade` on both sides (32 442 870 bytes);
39/39 `.ter` identical, none extra; overlay MD5 `8116be8821b41a66f90d031d8a683899` = Ortho4XP; DDS
gate 17/17 strict (dPSNR within -0.36 dB, alpha within -1.43 dB); `Ortho4XP_+43+005.cfg` (44 lines)
present in the pack and listed in `orthostudio.toml`; no `.bak` file anywhere under the output
directory. The second run hit every node without `--osm-snapshot` (the label is derived from the
cache files, stable across days).

Timing: the correction round costs nothing measurable (+1.3 s on a run whose Ortho4XP stages vary
by +-1 s with the load); the stage-1 gate is a no-op on a single tile.
