# Masks: water transparency images built from the mesh (`orthostudio.masks`)

Status: P3, written before the code of `src/orthostudio/masks/` (tests `tests/test_masks_*.py`).
Replaces step 2.5 of Ortho4XP (`src/O4_Mask_Utils.py`, 1054 lines). The consumer side
(which texture needs a mask, how the crop becomes an alpha channel) is **not** here: it is
`docs/specs/textures-imprint.md`, already delivered, and is not touched by this work.

## 1. The rule in plain language

A mask is a 4096 x 4096 grey image, one per cell of the `mask_zl` texture grid that the tile
overlaps. 255 means "the orthophoto is opaque" (land), 0 means "let X-Plane's own water show
through" (open sea), and the values between are the shoreline transition. The mask is built
from the **triangles of the mesh** whose Triangle4XP attribute says water: they are drawn
black over a white background covering the land of every 1 degree tile whose mesh exists, and
the black is then feathered outwards over `masks_width` metres by one of three profiles
(`sand`, `rocks`, `3steps`). A cell that comes out uniformly white or uniformly black is not
written at all: `textures-imprint.md` treats a missing mask as "this texture has no water".

Because a mask cell straddles tile borders, the cell is built with a 1024 px working margin
on each side (6144 x 6144 in total) fed by the meshes of the tile **and of its 8 neighbours**;
a neighbour whose mesh does not exist stays black, which is Ortho4XP's way of saying "no sea
beyond that border". Water triangles falling in the outer quarter of a cell are additionally
recorded in the neighbouring cell so its margin is not empty.

## 2. Origin in Ortho4XP

| Where | What |
|---|---|
| `O4_Mask_Utils.py:64-221` (`build_masks`) | the whole stage: grey level of inland water from `Utils/water_transition.png`, mesh presence check, destination `Masks/<round>/<short>` (or its `Combined_imagery` subdirectory), neighbour meshes, deletion of the old masks, `record_water_tris`, one `build_mask` per cell of `dico_sea` over a 4-slot pool, DEM pre-mask, custom-extent pre-mask, `blur_mask`, land forced to 255, crop to 4096, write if not uniform, optional `_dist` mask |
| `O4_Mask_Utils.py:69-71` | `sea_level = water_transition.png.getpixel((0, 127 * (1 - min(1, 0.1 + ratio_water))))`; the `y` index is a float and Pillow truncates it |
| `O4_Mask_Utils.py:136-141` | cells outside `[til_x_min, til_x_max] x [til_y_min, til_y_max]` (the tile's own range at `mask_zl`) are skipped |
| `O4_Mask_Utils.py:223-242` (`select_neighbor_meshes`) | the 3x3 block of `Data<tile>.mesh` around the tile, sibling `zOrtho4XP_*` directories (or the same one when `grouped`); a file that is not there is simply absent |
| `O4_Mask_Utils.py:244-258` (`delete_old_masks_in_tile`) | every `<y>_<x>.png` of the tile's range is removed before the build |
| `O4_Mask_Utils.py:260-328` (`build_water_pre_mask`) | 6144² black image; white polygon over the extent of each available mesh; grey (`sea_level`) polygons for the inland triangles; black polygons for the water triangles; `ImageDraw.polygon` with float pixel coordinates |
| `O4_Mask_Utils.py:330-368` (`build_dem_pre_mask`) | DEM super level set at `mask_altitude_above = 0.5` m over the cell's bbox, reprojected 4326 -> 3857 by an 8x8 mesh transform, `GaussianBlur(0.3 * 2**(mask_zl - 14))`, `> 0` -> 255; maximum with the water pre-mask |
| `O4_Mask_Utils.py:370-392` (`build_custom_pre_mask`) | a custom extent rasterised to 4096², scaled by `sea_level / 255`; **broken in Ortho4XP**: the function returns `custom_mask_array`, the caller stores it in `custom_array` and then reads `custom_mask` (`:170`), a `NameError` on every tile with `masks_custom_extent` |
| `O4_Mask_Utils.py:394-646` (`record_water_tris`) | text parse of each mesh; `has_water = 7` when the mesh version is >= 1.3 else `3`; a triangle is water when `attr != 0`, `attr & has_water != 0` and not (`attr & has_water == 1` and not `use_masks_for_inland`); its barycentre picks the cell at `mask_zl`; cells outside the tile range +/- 16 are dropped; the quarter of the cell at `mask_zl + 2` (`a`, `b` in 0..3) duplicates the triangle into the 1, 2 or 3 neighbouring cells whose margin it falls in; a second pass adds the *inland* triangles (`attr & has_water == 1`) to `dico_inland`, only for cells that already have sea, without quarter duplication |
| `O4_Mask_Utils.py:648-813` (`blur_mask`) | `pxscal = webmercator_pixel_size(lat + 0.5, mask_zl)`; `sand`: `blur_width = int(masks_width / pxscal)`, hat kernel `1..bw..1` divided by `bw**2`, `numpy.convolve` row by row **into a uint8 array** then column by column, then `2 * minimum(x, 127)`; `rocks`: `blur_width = masks_width / (2 * pxscal)`, `GaussianBlur(bw/1.7) > 0` then `GaussianBlur(bw)`, a `tan`-based gamma 2.5 transfer curve, then `maximum` with `GaussianBlur(2**(mask_zl-14))` of the pre-mask; `3steps`: `blur_width = [L / pxscal for L in masks_width]`, `int(transin/3)` dilations by `GaussianBlur(1) > 0` writing a parabolic ramp from 255 to `sea_level`, a dilate-then-erode middle zone at constant `sea_level`, `int(transout/3)` dilations writing a linear ramp from `sea_level` to 0, then a global `GaussianBlur(2)` |
| `O4_Mask_Utils.py:160-166` | `blured = maximum((pre_mask > 0) * 255, blured)[1024:5120, 1024:5120]`: **every** pixel that was not black in the pre-mask is forced back to 255, inland grey included |
| `O4_Mask_Utils.py:168-198` | writes `<y>_<x>.png` unless `max == 0` or `min == 255`; with `distance_masks_too`, `skfmm.distance(2*(pre>0)-1, narrow=255/2**(16-mask_zl))`, masked values filled with `-99999`, land zeroed, cropped, scaled by `2**(16-mask_zl)`, `minimum(-minimum(d,0), 255)` -> `<y>_<x>_dist.png` |
| `O4_Mask_Utils.py:815-1054` (`triangulation_to_image`) | **not part of step 2.5**: it rasterises a Triangle4XP `.1.node/.1.ele` pair for the *Auto* extents of combined providers (`O4_Imagery_Utils.py:734`). It belongs to `orthostudio.imagery` (P4b) and is not ported here |
| `O4_Geo_Utils.py:88-105` | `wgs84_to_pix` / `pix_to_wgs84`, the 256 * tile-unit pixel grid the masks live on |

## 3. Contract

```python
# rule.py
from orthostudio.masks import MASKS, MasksJob, MasksParams, masks_job, read_mesh_artifact

# water.py
from orthostudio.masks import NEIGHBOUR_OFFSETS, MaskRange, MeshWaterTris, WaterTriangles
from orthostudio.masks import cell_pixel_origin, mask_cells, read_water_tris, water_triangles

# raster.py
from orthostudio.masks import CELL_PX, custom_pre_mask, extent_polygons, pre_mask

# profiles.py
from orthostudio.masks import WATER_TRANSITION, blur_mask, blur_widths, halo_px, rocks_blur
from orthostudio.masks import sand_blur, sea_level_for, three_steps_blur

# distance.py
from orthostudio.masks import distance_mask, edt_px, pil_blur_reach, pil_blur_support

# dem.py
from orthostudio.masks import dem_pre_mask, mesh_warp

# build.py
from orthostudio.masks import CellJob, CellResult, MasksIndex, build_cell, build_masks, worker_count
```

* `mask_cells(tile, mask_zl) -> MaskRange` gives `til_x_min/til_y_min/til_x_max/til_y_max`
  (`texture_at` on the two corners, `O4_Mask_Utils.py:244-247`).
* `water_triangles(meshes, tile, mask_zl, *, use_masks_for_inland=False, water_tris=None)
  -> dict[(til_x, til_y), WaterTriangles]` where `meshes` maps a `TileRef` (the tile and any
  of its 8 neighbours) to a `MeshData` and `water_tris` optionally carries the mesh rule's
  `water_tris.npz` (`MeshWaterTris`, `docs/specs/mesh-build.md` section 7). `WaterTriangles`
  holds `sea` and `inland`, each a float64 `(n, 3, 2)` array of **absolute web-mercator pixel
  coordinates** at `mask_zl`.
* `pre_mask(til_x, til_y, tris, extents, *, mask_zl, sea_level, margin) -> np.uint8`
  draws the Ortho4XP image; `margin` is the working margin (1024 in Ortho4XP, `halo_px` here).
* `halo_px(masking_mode, masks_width, lat, mask_zl, *, distance_masks_too=False,
  full_margin=1024)` (in `profiles.py`) is that margin.
* `blur_mask(pre, *, masking_mode, masks_width, lat, mask_zl, sea_level) -> np.uint8`.
* `distance_mask(pre, *, mask_zl, margin) -> np.uint8 (4096, 4096)`.
* `build_cell(...) -> CellResult(mask: np.ndarray | None, dist: np.ndarray | None)`.
* `build_masks(out_dir, meshes, tile, *, mask_zl, masks_width, masking_mode,
  use_masks_for_inland, ratio_water, distance_masks_too, water_tris=None, dem_cells=None,
  custom_cells=None, workers=None, cancel=None, executor=None) -> MasksIndex`
  writes `<til_y>_<til_x>.png`, optionally `<til_y>_<til_x>_dist.png`, and `index.json`.
  **`cancel`**: the event is polled before every cell and *raises* `SYS_CANCELLED`; the
  pending cells of the pool are cancelled (`shutdown(cancel_futures=True)`) and `index.json`
  is written only after the last cell. A cancelled run therefore leaves nothing a store could
  commit -- it used to `break` and return normally, which committed a truncated directory
  under the key of the complete one (review 4, finding C2).
* `MasksJob(workers, cancel)` + `masks_job(...)` (in `rule.py`) is how the pipeline gives the
  node its pool size and its cancellation token, the way `dem_job` / `osm_job` do. There is
  no module-level hook any more: two concurrent nodes would have shared it.
* `MASKS` is the rule `orthostudio.masks@1`, `kind="dir"`, `ram_mb=1100`, inputs
  `mesh`, `nb_n`, `nb_ne`, `nb_e`, `nb_se`, `nb_s`, `nb_sw`, `nb_w`, `nb_nw`, `dem`,
  `custom_extent`; every neighbour, `dem` and `custom_extent` may be `None` (a distinct,
  keyed state: `graph-keys.md` invariant K4).

`MasksParams` declares **exactly** what the stage reads: `tile`, `mask_zl`, `masks_width`,
`masking_mode`, `use_masks_for_inland`, `ratio_water`, `masks_use_DEM_too`,
`distance_masks_too`, `masks_custom_extent`. `custom_dem` and `fill_nodata` are *not* params:
the elevation is an input artefact (its digest keys the mask), which is what
`graph-keys.md` asks for and what lets a mask survive an unrelated DEM setting.

The output directory is the one `textures-imprint.masks_dir_lookup` and
`pipeline/build._distance_lookup` already read: file names `<til_y>_<til_x>.png` and
`<til_y>_<til_x>_dist.png` and `index.json` (`format` is `osxp-masks-1`).

## 4. Decisions

| Rule | Decision |
|---|---|
| water triangle selection, `has_water` by mesh version | keep, vectorised over `MeshData.tri_attr` |
| barycentre -> cell, range filter, quarter duplication into 1-3 neighbour cells | keep, bit for bit (`texture_at` on `float64` barycentres computed as `(a+b+c)/3`) |
| inland triangles only in cells that already have sea, no quarter duplication | keep (it is what makes the inland grey visible only near a shore) |
| `dico_sea` also holds the inland triangles when `use_masks_for_inland` | keep |
| 8 neighbours by mesh presence; absent = no sea beyond the border | keep, as 9 explicit rule inputs instead of a directory scan |
| white extent polygon per available mesh | keep |
| `ImageDraw.polygon` with float pixel coordinates | keep, same Pillow call (this is what makes byte identity reachable at all) |
| 1024 px working margin | **narrowed to what the profile can reach** (`halo_px`): `sand` needs `blur_width - 1` px, every other mode keeps the full 1024. Proven identical on the reference: the hat kernel has half-width `bw - 1`, so a pixel of the final 4096² window can only depend on input pixels within `bw - 1` of it, and `numpy.convolve` in `'same'` mode over a cropped row computes the *same* 27-term dot product for every interior position (`test_masks_profiles.py::test_crop_matches_full_margin`) |
| `sand` = hat convolution with a **uint8 truncation between the two passes** | keep exactly. The only reproduction that is bit-exact is `numpy.convolve` itself: the float64 result is rounded by cblas `ddot`, and an exact-integer double box (which is what the kernel mathematically is) differs on the pixels whose exact sum is a multiple of `bw**2` (measured: 22 pixels per 300² of synthetic three-level data, `max diff 2`). Speed comes from the halo crop and from memoising identical rows, never from changing the arithmetic |
| `rocks` = `GaussianBlur` chain | keep, same Pillow calls, full 1024 margin |
| `3steps` = dozens of `GaussianBlur(1) > 0` dilations | **replaced** by an EDT: the profile is a function of the distance to the water boundary alone, so it is computed once with `scipy.ndimage.distance_transform_edt` and mapped through the same value ladder, with the thresholds at multiples of the measured reach of one `GaussianBlur(1) > 0` step (3 px) and the middle zone at `reach(R_buf) - reach(R_buf - R_sea)`. Tolerance in section 6 |
| `_dist` = `skfmm.distance` with a narrow band | **replaced** by `scipy.ndimage.distance_transform_edt` in float32. `skfmm` is a fast-marching approximation of the distance to the zero level set of `2*(pre>0)-1`, which sits half a pixel inside the land; the EDT measures to the nearest land *pixel centre*, so `d_edt - 0.5` is the comparable quantity. Tolerance in section 6. Removes the `scikit-fmm` dependency |
| `masks_use_DEM_too` | kept as a **pure function** `dem_pre_mask(above, ...)` over a boolean super-level-set array plus its bbox; the reprojection is the Ortho4XP 8x8 mesh transform. The rule takes the elevation as an optional input because `orthostudio.sources.dem` does not exist yet (section 8) |
| `masks_custom_extent` `NameError` | **fixed**: the custom array is used, not an undefined name. The rasterisation of the extent itself belongs to `orthostudio.imagery` (P4b); the rule takes it as an optional input (section 8) |
| land forced back to 255 after the blur, inland grey included | keep (surprising, but it is what the reference masks contain) |
| uniform masks not written | keep |
| deletion of the old masks before the build | drop: an artefact directory is written once, in a store, under a key |
| `masks_build_slots = 4` threads under the GIL | drop: a process pool, one cell per task, sized by the pipeline (section 6.5) |
| `Combined_imagery` destination (`for_imagery=True`) | out of scope: it belongs to the combined-provider pipeline (P4b), and nothing in OrthoStudio XP calls it yet |
| `triangulation_to_image` | not ported (it is an imagery extent helper, see section 2) |

## 5. Acceptance tests

`tests/test_masks_*.py`. The oracle ones, against Ortho4XP's masks of +43+005, left with decision
0010; their results stand in section 6.3.

1. `test_masks_oracle.py::test_sand_masks_are_byte_identical` (mark `oracle`): from
   `build/Data+43+005.mesh` alone, with the 8 neighbours absent, the 7 files of
   `masks/` are reproduced **byte for byte** (same PNG bytes, not only the same pixels), and
   no eighth file is produced.
2. `test_masks_water.py`: a synthetic mesh with one triangle per quarter of a cell lands in
   exactly the 1, 2 or 4 cells Ortho4XP would fill; the attribute filter is checked on the eight
   interesting values of `attr & has_water` and on both mesh versions; a precomputed
   `water_tris.npz` gives the same cells as a search through the mesh.
3. `test_crop_matches_full_margin` and `test_rocks_crop_matches_full_margin`: the `halo_px`
   crop gives the same bytes as the full margin, for the hat convolution and for Pillow's
   Gaussian chain; `test_pillow_gaussian_support_bounds_the_real_one` checks the support
   formula against Pillow itself.
4. `test_row_memoisation_is_transparent` and `test_sand_matches_the_ortho4xp_code`: the memoised
   convolution equals the plain one, and `sand_blur` equals a literal transcription of
   `O4_Mask_Utils.py:665-679`.
5. `test_sea_level_table`: `sea_level_for(r)` matches `Utils/water_transition.png` for
   `r` in 0, 0.1, 0.25, 0.5, 1 (values 221, 169, 99, 23, 0).
6. `test_rocks_masks_are_byte_identical` and `test_3steps_masks_match_within_the_declared_
   tolerance` (mark `oracle`, skipped unless `OSXP_MASKS_ROCKS_DIR` / `OSXP_MASKS_3STEPS_DIR`
   point at an Ortho4XP run): byte identity for `rocks`, section 6.3 for `3steps`.
7. `test_distance_masks_match_within_the_declared_tolerance` (same gate,
   `OSXP_MASKS_DIST_DIR`): section 6.3 against the `skfmm` masks.
8. `test_masks_rule.py`: the rule's consumed set is exactly the nine parameters of section 3;
   an absent neighbour keys differently from a present one; the artefact of a rebuilt tile is
   read back by `orthostudio.textures.imprint.masks_dir_lookup` and by
   `pipeline.build._distance_lookup`.
9. `test_masks_build.py`: a cell without water, a cell that is all water and a cell the DEM
   turns into land are not written; the index lists what is; a 2-process build gives the same
   bytes as a sequential one; cells of the neighbour grid are skipped.
10. `test_masks_dem.py` and `test_masks_distance.py`: the 8x8 mesh warp, the DEM pre-mask,
   the reach and the support of Pillow's blur, and the distance mask on a half-plane.

## 6. Measurements (M4 Pro 14 cores, load average 4.2, `nice -n 10`, 2026-09-12)

Reference tile +43+005, ZL14, 7 masks out of 10 candidate cells, `ratio_water = 0.25`
(`sea_level = 99`), `pxscal = 6.930` m. Ortho4XP is the same machine, its own four threads,
`Data+43+005.mesh` of the fixture as the only mesh (the eight neighbours absent).

### 6.1 Wall clock

| Profile | Ortho4XP (step 2.5, 4 threads) | OrthoStudio XP 1 worker | 4 | 8 | OrthoStudio XP end to end | gain |
|---|---|---|---|---|---|---|
| `sand`, `masks_width = 100` | 4.56 s / 4.66 s | 1.40 s | 0.82 s | 0.74 s | **1.07 s** | **4.3x** |
| `rocks`, `masks_width = 100` | 4.54 s | 2.98 s | 1.30 s | 0.99 s | **1.32 s** | **3.4x** |
| `3steps`, `100 / 200 / 100` | 6.91 s | 4.76 s | 1.84 s | 1.36 s | **1.69 s** | **4.1x** |
| `sand` + `distance_masks_too` | 10.00 s | 5.32 s | 2.02 s | 1.50 s | **1.83 s** | **5.5x** |

"end to end" adds the 0.33 s of `read_mesh` on the 69 MB text file, which Ortho4XP pays *twice*
inside its own 4.56 s (it re-parses the mesh for the inland pass). Per mask, `sand`:
0.023 s of rasterisation (about 90 000 `ImageDraw.polygon` calls over the 7 cells) and
0.139 s of blur; `rocks` 0.378 s, `3steps` 0.634 s, `_dist` 0.150 s on top of `sand`.
`water_triangles` sorts the 1.2 M triangles of the mesh into 13 cells (244 255 triangle
copies after the quarter duplication) in **0.038 s**, against Ortho4XP's two Python passes over
the text file.

Where the time went, for `sand`: the 1024 px working margin cropped to 15 px is worth
2.2x (6144² -> 4126²), memoising identical rows of the convolution another 1.2x, and the
process pool 1.9x. The remaining 0.139 s per mask is `numpy.convolve` itself, called once
per output pixel by numpy (one cblas `ddot` of 27 terms each); it is kept because it is the
only bit-exact reproduction (section 4).

### 6.2 Memory, one cell in flight per worker

| Profile | peak RSS of one worker | parent |
|---|---|---|
| `sand` | 242 MB | 438 MB (the mesh) |
| `rocks` | 703 MB | 438 MB |
| `3steps` | 981 MB | 441 MB |
| `sand` + `_dist` | 962 MB | 439 MB |

The rule declares `ram_mb = 1100`: the base plus the cost of **one** cell of the heaviest
profile. The real cost is `300 + 800 x workers` MB, and it is the *pipeline* that knows the
worker count, so `pipeline/build.py:_masks_ram_mb` computes that number for the node it
declares and hands the very same count to the rule through `MasksJob` (section 6.5). A caller
that builds the node without `declare()` gets the one-worker figure, which is the honest
minimum.

The distance masks are the peak: one 4230 x 4230 cell measured at **814 MB** of RSS
(`sand`/100 m, `nice -n 10`), dominated by the index arrays SciPy allocates inside
`distance_transform_edt`; `edt_px` writes the float64 field into an array of its own
(`distances=`) so at least the returned copy is not allocated twice.

### 6.5 How many workers (one owner, one parser)

`$OSXP_MASKS_WORKERS` has **one** parser, `orthostudio.masks.build.env_workers`; `0`, a negative
value and junk mean *unset*, never "all cores" (three readers used to disagree on exactly that: the
budget said one process while the pool started `os.cpu_count()`).

| Who | What it decides |
|---|---|
| `pipeline.build.masks_workers(env)` | `env_workers()` if set, else `min(MAX_WORKERS=8, batch workers)`. The single decision. |
| `pipeline.build._masks_ram_mb(env)` | `300 + 800 x masks_workers(env)`: the node's `ram_mb`. |
| `pipeline.build._masks_run(env)` | binds `MasksJob(workers=masks_workers(env), cancel=...)`. |
| `masks.build.worker_count(cells, workers)` | the argument, else `env_workers()`, else `min(8, cpu_count)`; capped by the number of cells. |

The default is capped at 8 on purpose: the node holds one `subprocess` slot while starting a
pool, so an uncapped default made the declared `ram_mb` describe a pool that was not the one
running, and the scheduler's RAM budget could not do its job.

### 6.3 Fidelity

| Artefact | Target | Reached | Evidence |
|---|---|---|---|
| `sand`, 7 masks | byte identical | **byte identical** (PNG bytes, not only pixels), and exactly those 7 cells | `test_masks_oracle.py::test_sand_masks_are_byte_identical`, also through a 4-process pool |
| `rocks`, 7 masks | byte identical | **byte identical** | Ortho4XP run with `masking_mode=rocks`, `test_rocks_masks_are_byte_identical` |
| `3steps`, 7 masks | semantic, declared tolerance | 97.87 - 99.94 % of pixels **equal**, 99.75 - 99.99 % within 16, worst `max` 98, worst mean 0.139 | Ortho4XP run with `masking_mode=3steps`, `masks_width=[100,200,100]` |
| `_dist`, 7 masks | semantic, declared tolerance | 97.78 - 99.86 % equal, >= 99.996 % **within 1**, `max` 2 (out of 255), mean <= 0.023 | Ortho4XP run with `distance_masks_too=True` |

Per mask, `3steps` (equal %, within 16 %, max): 5984_8416 99.29 / 99.94 / 70 .
5984_8432 99.70 / 99.99 / 52 . 6000_8416 99.87 / 99.97 / 39 . 6000_8432 97.87 / 99.75 / 75 .
6000_8448 98.42 / 99.80 / 98 . 6000_8464 99.94 / 99.99 / 32 . 6016_8448 99.63 / 99.95 / 62.
The deviations sit on the diagonals of the shoreline: after `k` steps Ortho4XP's
`GaussianBlur(1) > 0` reaches `3k` pixels along the axes but only about `2.5k` along a
diagonal (measured on a square: 3, 6, 9, 12, 15 against 1.41, 4.24, 7.07, 9.90, 12.73), while
the Euclidean transform reaches `3k` in every direction; one step of the ladder is 39 grey
levels wide on the inward ramp, hence a `max` in the tens on the pixels that change step.

Per mask, `_dist` (equal %, within 1 %): 5984_8416 98.38 / 99.999 . 5984_8432 99.57 / 99.999 .
6000_8416 99.86 / 100.000 . 6000_8432 97.78 / 99.996 . 6000_8448 98.22 / 99.998 .
6000_8464 98.92 / 100.000 . 6016_8448 99.63 / 100.000. One unit is 4 pixels at ZL14, so a
`max` of 2 units is half a pixel: exactly the offset between `skfmm`'s zero level set and the
nearest land pixel, which `DEFAULT_EDT_OFFSET_PX` already corrects on average.

### 6.4 Integer arithmetic is *not* a substitute for `numpy.convolve`

The `sand` kernel is mathematically a box convolved with itself over `bw**2`, so the exact
result is `floor(sum(a_i * k_i) / bw**2)` and can be had from two integer cumulative sums,
three times faster. It is **wrong**: `numpy.convolve` accumulates in float64 through cblas
`ddot`, and on the pixels whose exact sum is a multiple of `bw**2` the float lands just
below the integer and truncates one lower. Measured on 300² of synthetic three-level data,
`bw = 14`: 22 differing pixels, `max` difference 2 after the `2 * min(x, 127)`; on the real
pre-mask of 6000_8432, 14.3 M of the 37.7 M pixels have an exact sum that is a multiple of
196 (every flat area), of which only 250 are not a constant window. The float path is kept.

## 7. Wanted differences from Ortho4XP

* No deletion pass, no `for_imagery` variant, no `Masks/` directory: the artefact is keyed.
* `_dist` by EDT (float32) instead of `skfmm` (float64, narrow band): `scikit-fmm` disappears
  from the dependency list.
* `3steps` by EDT instead of dozens of blur-and-threshold passes.
* The custom-extent `NameError` is fixed.
* The working margin is cropped to what the profile can reach, for `sand` only.
* A process pool instead of 4 GIL-bound threads.

## 8. Not there yet (blocking questions, least engaging option implemented)

* **`water_tris.npz`**: the native mesh rule is expected to publish the water triangles per
  cell. Its format is not written yet, so this module reads the mesh itself
  (`mesh_file.read_mesh` / `read_mesh_npz`) and exposes `water_triangles(meshes, ...)` so the
  precomputed arrays can be substituted without touching the rasteriser.
* **Elevation source**: `masks_use_DEM_too` needs `orthostudio.sources.dem`, which does not exist.
  `dem_pre_mask` is written and tested as a pure function; the rule raises
  `MASK_FILE_UNREADABLE`-style `OsxpError("DEM_FILE_UNREADABLE")` when the flag is set and the
  `dem` input is absent. **TODO** marked in the code.
* **Custom extents**: `masks_custom_extent` needs the extent rasteriser of `orthostudio.imagery`
  (P4b). The pure function is written; the rule raises `MASK_CUSTOM_EXTENT_INVALID` when the
  flag is set and the `custom_extent` input is absent. **TODO** marked in the code.
* **Number of workers inside one rule** (*resolved for the RAM, still open for the slots*):
  the pool size is now decided by the pipeline and declared with the node's `ram_mb`
  (section 6.5), so what the scheduler budgets is what the node uses. What remains open is
  the *slots* accounting: the node holds one `subprocess` slot while running `workers`
  processes, so with `subprocess_slots = 3` a batch can hold three such pools. That is
  bounded by the RAM budget today (3 x 6.7 GB against 28.8 GB on a 48 GB machine), not by the
  slot count; teaching the scheduler "this node wants N slots" is a scheduler change and is
  reported as a blocker.
