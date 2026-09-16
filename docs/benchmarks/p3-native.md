# P3: the native stages inside `osxp build` (+43+005)

Machine: Mac mini M4 Pro, 14 cores, 48 GB, macOS 15.6, Python 3.14.7, `nice -n 10`, uptime
5 d 19 h, load 2.4-3.6 before every run (noted per run below). Ortho4XP checkout
`../Ortho4XP` with warm OSM and elevation caches; OrthoStudio XP stores **empty** at
the start of each numbered run (a fresh `$OSXP_HOME`). 2026-09-12.

Reference: `docs/benchmarks/baseline-ortho4xp.md` (Ortho4XP itself, ZL14, 63.2 s median of 3) and
`docs/benchmarks/p2-build.md` (OrthoStudio XP with the three Ortho4XP stages, 49.9 s). The
OrthoStudio XP figures below were re-measured today on the same machine, so the two OrthoStudio XP
columns are directly comparable; the Ortho4XP column is the frozen baseline.

Every run: `osxp build --tile +43+005 --provider BI --zl <14|16> --legacy-dir <Ortho4XP>
--creation-agent Ortho4XP --stages <legacy|native>`. The commands, the JSON reports and the
comparison script are in the session scratchpad; the fidelity assertions are held by
`tests/test_p3_oracle.py`.

## 1. One tile, ZL14, cold OrthoStudio XP store

| Node | rule (legacy stages) | Ortho4XP (s) | OrthoStudio XP legacy (s) | rule (native stages) | OrthoStudio XP native (s) | gain |
|---|---|---:|---:|---|---:|---:|
| osm | -- (Ortho4XP fetches inside step 1) | -- | -- | `orthostudio.osm@1` | not declared (cache warm) | -- |
| coastline | -- | -- | -- | `orthostudio.coastline@1` | 0.13 | -- |
| vectors | `legacy.vectors@1` | 31.6 | 31.80 | `legacy.vectors@1` | 32.99 | x1.0 |
| mesh | `legacy.mesh@1` | 7.5 | 8.51 | `orthostudio.mesh@1` | **2.79** | **x3.1** |
| masks | `legacy.masks@1` | 4.3 | 4.69 | `orthostudio.masks@1` | **0.78** | **x6.0** |
| xp12 | `xp12.rasters@1` | (in step 3) | 0.43 | idem | 0.42 | |
| overlay | `tile.overlay@1` | 3.0 | 3.00 | idem | 3.12 | |
| dsf | `tile.dsf@1` | (in step 3) | 1.51 | idem | 1.92 | see 5 |
| textures | `tile.textures@1` | (in step 3) | 4.29 | idem | 5.22 | |
| pack | `tile.pack@1` | by hand | 0.03 | idem | 0.02 | |
| **scheduler total** | | **63.2** | **51.29** | | **44.19** | **x1.43 / x1.16** |
| **CLI wall** | | | 51.72 | | 44.65 | |

Load before/after: legacy 3.01 / 3.20, native 2.58 / 3.49. Both runs had a **cold** chunk
store (4 352 Bing tiles, 63 MB, downloaded twice on purpose so the two totals compare).

Reading of the table: the two stages P3 actually replaces went from 13.20 s to 3.57 s
(**x3.7**); the whole tile only went from 51.3 s to 44.2 s (x1.16) because
`legacy.vectors` -- the stage P4 replaces -- is now **75 % of the critical path** (33.0 s of
44.2 s). Everything else on the tile adds up to 11.2 s. The x10 of `PLAN.md` is a P4 figure;
what P3 buys is measured here honestly: 9.6 s off a 51 s tile, and a mesh + masks pair that
is no longer worth caching to disk between experiments.

Detail of the two native stages (from the module reports, confirmed here):

* `orthostudio.mesh` 2.79 s = 0.30 PSLG text read + 0.015 weight map + 1.13 Triangle4XP (binary
  exchange) + 0.005 sidecar read + 0.36 post-processing + 0.89 writing the 69 MB `.mesh` +
  0.01 npz. Two thirds of what is left is the text `.mesh` file, kept for the Ortho4XP fall-back
  and the oracle;
* `orthostudio.masks` 0.78 s = 0.33 mesh read + 7 cells over 8 worker processes. Ortho4XP uses four
  threads under the GIL for the same seven masks.

## 2. ZL16 (store warm for the tile-level nodes, chunks cold)

| Node | OrthoStudio XP native (s) | note |
|---|---:|---|
| osm, coastline, vectors, mesh, masks, xp12, overlay | 0.00 (7 hits) | `mask_zl` is 14 at every level |
| `+43+005/BI16/dsf` | 1.95 | 356 `.ter` |
| `+43+005/BI16/textures` | 30.52 | 179 textures, 45 824 tiles, ~1 550 req/s |
| `+43+005/BI16/pack` | 0.18 | |
| **total** | **33.10** | Ortho4XP: 250.7 s (`p2-build.md` 2) -> **x7.6** |

Identical to the P2a figure (33.1 s): at ZL16 nothing of the native work is on the path, which
is the point -- the recipe of the mesh does not change the imagery.

## 3. Fidelity (the reason the stages may be wired at all)

| Artefact | Target | Result |
|---|---|---|
| `Data+43+005.alt` (from `legacy.vectors`) | byte-identical | **identical** (md5 `abb7a099…`) |
| `Data+43+005.mesh` (`orthostudio.mesh@1`) | byte-identical to the frozen Ortho4XP build | **identical**, 69 171 409 B, md5 `1f29a263…` |
| 7 masks (`orthostudio.masks@1`, `sand`) | byte-identical to `fixtures/large/oracle/+43+005_zl14_BI/masks` | **7/7 identical**, and identical to what `legacy.masks` produced in the same session |
| `+43+005.dsf` ZL14 | byte-identical (`--creation-agent Ortho4XP`) | **identical**, 32 442 870 B, md5 `9763ba63…`; `compare_dsf` identical, MD5 footer verified |
| 39 `.ter` ZL14 | identical | **39/39** |
| 17 DDS ZL14 | P1 gate | byte-identical to the DDS the legacy-stage run produced from the same chunks (**17/17**); against Ortho4XP's own DDS, direct PSNR 31.2 dB worst, inside the P1 range (31.2-47.4 dB) |
| `+43+005.dsf` ZL16 | byte-identical | **identical**, 32 515 273 B, md5 `bacb7f22…` |
| 356 `.ter` ZL16 | identical | **356/356** |
| 179 DDS ZL16 | P1 gate | worst direct PSNR 29.6 dB, inside the P1 range (29.6-50.5 dB) |

Mixed recipes were checked too: `--stages native --legacy-stage mesh` (Ortho4XP mesh + native
masks) gives the same 7 masks and the same byte-identical DSF (3.74 s, mesh from the store).

### 3.1 The bytes that had to be chased: `mesh.npz` carries more precision than `.mesh`

The first native run produced a DSF whose **first** difference from Ortho4XP's was at
`GEOD/POOL[5]`, payload byte 38 706 (`0x71` vs `0x70`). Re-measured byte by byte in review 4: the
file has the same size (32 442 870 bytes) and differs on **23 bytes** over several pools (file
offsets 591 194, 2 513 916, 6 588 280, 7 916 314, 7 916 624, ...); the "one byte" of the first
write-up was `compare_dsf.first_difference`, which names the first one, not their count. Cause:
`tile.dsf` used to read the `mesh.npz` twin, and `orthostudio.mesh` writes into it the
**full-precision doubles** of Triangle4XP, where the text `.mesh` keeps 15 significant digits of
`z/100000`. Measured on the tile: 53 225 of 603 649 longitudes differ by up to 8.9e-16 deg and
451 285 of 603 649 elevations by up to 5.0e-11 m -- enough for a few elevations to fall on the other
side of a u16 rounding boundary.

Fix (`pipeline/build.py`): the DSF node reads the **text** `.mesh` when the artefact has one,
which is what Ortho4XP's step 3 does. Cost 0.32 s instead of 0.006 s, visible in the table above
(dsf 1.92 s against 1.51 s). Normals are *not* affected: `_normals_as_ortho4xp` rounds both sides
to the same value on all 1 207 298 components (0 difference), as `mesh-build.md` 6.1 says.

## 4. What the cache does (native stages, same store)

| Run | Hits | Built | Wall (s) |
|---|---|---|---:|
| nothing changed | 9 | 0 | **0.02** (0.47 s for the whole CLI process) |
| `--set masks_width=200` | coastline, vectors, mesh, xp12, overlay | masks 0.80, dsf 1.83, textures 1.00, pack 0.02 | **4.08** |
| `--set curvature_tol=1.5` | coastline, vectors, xp12, overlay | mesh 3.11, masks 0.75, dsf 2.22, textures 0.91, pack 0.01 | **7.44** |
| `--dem native` (same store) | vectors, coastline, xp12, overlay | dem 1.21, mesh 2.6, masks 0.7, dsf 1.8, textures 0.2, pack 0.0 | **7.49** |

`masks_width` does not touch the mesh (it is not one of its parameters) and `curvature_tol`
does not touch the vector stage; the same P2a runs cost 8.2 s and (not measured in P2a)
a full `legacy.mesh`. The elevation settings (`custom_dem`, `fill_nodata`) are absent from
`MeshParams` on purpose: they reach the mesh through the digest of its `dem` input.

## 5. `--dem native`: measured, and not the default

`orthostudio.dem@1` builds the raster in **1.21 s** inside the graph (warm elevation cells, negative
memo cold). It is *not* wired by default, because the two producers of `Data<tile>.alt` do
not agree (spec `pipeline-build.md` 8.2):

| | samples differing | worst | consequence |
|---|---:|---:|---|
| `orthostudio.dem@1` vs the raster stage 1 writes | 21 923 / 13 490 929 (0.163 %) | 13.57 m | mesh 69 191 966 B instead of 69 171 409 B (different md5), DSF 32 447 302 B instead of 32 442 870 B, first difference at `GEOD/POOL[0]` |

The 0.163 % is `smooth_raster_over_airports` (`O4_Airport_Utils.py:924-1034`), which consumes
the OSM airport polygons and therefore belongs to the vector stage; `orthostudio.dem` publishes the
raster *before* it (its blocker B1). `--dem native` is measured, tested and documented; it
becomes the default in P4, when the vector stage stops producing a raster of its own. With
`--stages legacy` it is refused outright (nothing would read it).

Note that while the vector stage is Ortho4XP, the DEM node is also *redundant*: stage 1 builds the
same raster anyway, so `--dem native` adds 1.2 s rather than saving the 1.1 s the `dem`
benchmark measures against Ortho4XP's own elevation step.

## 6. Cold tile: `+43+004` at ZL12, no Ortho4XP OSM cache at all

`../Ortho4XP/OSM_data/+40+000/+43+004/` was **empty** before the run
(checked), and the tile has no OrthoStudio XP snapshot. Load 2.42 before, 4.56 after.

| Node | (s) | note |
|---|---:|---|
| `+43+004/osm` (`orthostudio.osm@1`, phase 0) | **24.6** | 4 layers: airports, big_roads, water on `lz4.overpass-api.de`, coastline on `overpass.openstreetmap.fr` (the client failed over on its own); 6 594 + 98 328 + 6 599 + 324 648 nodes; the four `.osm.bz2` written into the Ortho4XP cache |
| coastline | 0.02 | from the OrthoStudio XP snapshot, not from the file it just wrote |
| vectors (Ortho4XP step 1) | 34.20 | **log: `* Recycling OSM data from ./OSM_data/+40+000/+43+004/…` for all four layers** -- Ortho4XP sent nothing to Overpass |
| mesh (`orthostudio.mesh@1`) | 3.18 | |
| masks (`orthostudio.masks@1`) | 0.91 | |
| xp12 / overlay / dsf / textures / pack | 0.33 / 2.30 / 1.68 / 6.46 / 0.01 | ZL12, 2 textures, 512 tiles |
| **total** | **74.93** | of which 24.6 s of OSM download |
| **same command again** | **0.01** | 9 hits, 0 requests, label unchanged (`osm-ff77783918ca`) |

This is the point of arbitration A3 and A4 together. For comparison, `p2-build.md` 3 (run F1)
measured the same cold tile through Ortho4XP's own Overpass client: the mirror answered "rejected"
then "too busy" with doubling waits (2, 4, … 128 s) and the stage had to be abandoned. The
OrthoStudio XP client failed over between mirrors after its first refusal and finished in 24.6 s.

The second run costs nothing **because the snapshot label is content-based** (A4). Without the
stored-snapshot reuse added for this integration, the label would have fallen back to the
mtime digest of the Ortho4XP cache that the first run had just written, and stage 1 would have
rebuilt for 34 s on the second run -- measured before the fix, and the reason for it.

## 7. What is not measured here

* a batch of native tiles (neighbour masks across a batch): only the graph shape is tested;
* `3steps` / `rocks` / `distance_masks_too` end to end: those profiles are measured in
  `masks-build.md` 6, and their fidelity is a declared tolerance, not byte-identity;
* the RAM of a whole native build: the nodes declare 400 MB (dem), 900 MB (mesh) and
  `300 + 800 x workers` MB (masks, 6.7 GB at 8 workers on this machine) against a 28 GB
  budget, so the scheduler never had to serialise anything on RAM in these runs.

## 8. Re-run after the review-4 fix pass (2026-09-12, 15:47)

Same machine, `uptime` 5 d 20 h, load 3.40 before / 3.88 after, `nice -n 10`, **empty**
`$OSXP_HOME`, warm Ortho4XP OSM and elevation caches, cold chunk store:

```
osxp build --tile +43+005 --provider BI --zl 14 --stages native \
          --legacy-dir ../Ortho4XP --creation-agent Ortho4XP
```

| Node | s | note |
|---|---:|---|
| coastline | 0.1 | source `legacy-cache` (now printed on the result line) |
| xp12 | 0.4 | |
| overlay | 3.1 | |
| vectors | 33.0 | `legacy.vectors@1` |
| mesh | 2.8 | `orthostudio.mesh@1`, cancellable sidecar |
| masks | 0.7 | `orthostudio.masks@1`, pool sized by the node (8 workers budgeted, 7 cells) |
| dsf | 1.9 | |
| textures | 5.1 | 4 352 tiles, 17 textures, ~414-1 500 req/s |
| pack | 0.0 | |
| **scheduler total** | **44.0** | 9 built, 0 hits, 0 failed |
| **CLI wall** | **44.5** | `/usr/bin/time -p real 44.45` |

Unchanged from section 1 (44.19 / 44.65 s): the fix pass touched cancellation, the worker
count, the OSM bridge and the reporting, none of which is on the critical path.

Fidelity of that very run, against `fixtures/large/oracle/+43+005_zl14_BI/build`:

| Artefact | Result |
|---|---|
| `+43+005.dsf` | **byte-identical**, 32 442 870 B, md5 `9763ba632190af2c46cf0fdceaca1ade`; `compare_dsf` identical, no first difference |
| 39 `.ter` | **39/39 identical**, same names |
| 17 DDS | same layout 17/17; direct level-0 PSNR against Ortho4XP's own DDS **31.2 dB worst**, 33.5 median, 47.1 best -- the P1 range for ZL14 (31.2-47.4 dB); min over every mip level 30.6 dB |

The CLI line now reads `+43+005 BI14 [native, coastline legacy-cache]: ok <pack>`.
