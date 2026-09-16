# Textures: water masks imprinted as the alpha channel

Status: P1, written before the code. Code: `src/orthostudio/textures/imprint.py`.
Measurements: section 6.

## 1. The rule in plain language

Sea water is rendered by X-Plane under the orthophoto; the orthophoto's alpha channel says
where the sea shows through (0 = water, 255 = land, in between = shoreline blend). The alpha
comes from a mask image built at `mask_zl` (14 by default) for the 4096² texture grid of that
zoom level, stored as `Masks/<+40+000>/<+43+005>/<til_y>_<til_x>.png` (8-bit grey).

For a texture at `zl >= mask_zl` the mask of the texture at `mask_zl` that contains it is
cropped to the sub-square covering the texture (side `4096 / 2**(zl - mask_zl)`). If that
**raw** crop is at most 30 everywhere the texture needs no mask (it is DXT1 and its sea
triangles go to X-Plane's own water terrain). Otherwise the crop is resampled bicubically to
4096² and becomes the alpha channel of the texture, which is then encoded as DXT5.

## 2. Origin in Ortho4XP

| Where | What |
|---|---|
| `src/O4_Mask_Utils.py:38-60` (`needs_mask`) | `zl < mask_zl` → no mask; `factor = 2**(zl - mask_zl)`; `m_til_x = (int(til_x / factor) // 16) * 16` (same for y); `rx = int((til_x - factor*m_til_x) / 16)`; mask file `FNAMES.mask_dir(lat, lon)/FNAMES.legacy_mask(m_til_x, m_til_y)`; absent → no mask; crop `(x0, y0, x0 + 4096 // factor, y0 + 4096 // factor)` with `x0 = int(rx * 4096 / factor)`; `max <= 30` → no mask, else the cropped image |
| `src/O4_File_Names.py:75-76, 334-344` | `mask_dir = Masks/<round>/<short>`, `legacy_mask = "<y>_<x>.png"`, per-texture mask `"<y>_<x>_ZL<zl>.png"` |
| `src/O4_DSF_Utils.py:669-760` | which texture is checked: for every sea triangle (`tri_type == 2`) the texture of its barycentre at `mesh_zl`; textures whose crop fails `needs_mask` are remembered in `skipped_terrains_for_masking`; a masked texture is rebuilt when its DDS is absent, smaller than 20 MB (a DXT1) or older than the mask; the crop is saved as `textures/<y>_<x>_ZL<zl>.png` and consumed (then deleted) by `convert_texture` |
| `src/O4_Imagery_Utils.py:2318-2334, 2380-2408` (`convert_texture`) | `mask_im = Image.open(...).convert("L")`; `big_image.putalpha(mask_im.resize((4096, 4096), Image.BICUBIC))`; PNG RGBA saved for `nvcompress -bc3` |
| `src/O4_Imagery_Utils.py:2336-2360` | the GeoTIFF export path recomputes the same crop and threshold inline (duplicate of `needs_mask`) |
| `src/O4_Imagery_Utils.py:2182-2189, 2243-2250` (`combine_textures`) | for a layer of priority `"mask"` in a **combined** provider, the layer's imagery is blurred with `GaussianBlur(sea_texture_blur * 2**(true_zl - 17))` before compositing |
| `src/O4_Imagery_Utils.py:2255-2265` | anti-halo on the layer's extent mask: where the layer imagery is nearly white (`sum(RGB) >= 735`) or nearly black (`<= 35`) and the mask is in the transition (`1 <= mask <= 253`), the mask is set to 0 (the layer is not used there); then the priority weighting (`low` / `medium` / `high` / `mask`) and `Image.composite` |

Order of operations in `combine_textures`, per layer from last to first: colour filter → sea
blur (mask layers only) → crop/resize when the layer's `max_zl` is below `zl` → anti-halo on
the extent mask → priority weighting → composite over the accumulated image.

## 3. Inputs and outputs

- `load_mask(path) -> np.ndarray`: L uint8 `(4096, 4096)`; unreadable file or wrong size →
  `OsxpError("MASK_FILE_UNREADABLE")`.
- `mask_for_texture(t, mask_zl, mask_lookup) -> np.ndarray | None`: `None` when `t.zl <
  mask_zl`, when `mask_lookup(m_til_x, m_til_y)` returns `None` (no mask for that cell of the
  `mask_zl` grid) **or when the raw crop is at most 30 everywhere** (review: one rule for the
  module and the pipeline); otherwise the crop resampled to 4096² (identity when `zl ==
  mask_zl`). `mask_lookup` abstracts where the masks are; `masks_dir_lookup(masks_dir)` builds it
  over the masks artefact.
- `needs_mask_for_texture(t, mask_zl, mask_lookup) -> bool`: the Ortho4XP decision alone (the
  raw crop exceeds 30), without resampling.
- `mask_cell(t, mask_zl) -> (m_til_x, m_til_y, x0, y0, side)`, `mask_crop_raw(full, x0, y0,
  side)` (the Ortho4XP `small_img`, what the threshold is evaluated on and what the external
  border PNG holds) and `mask_crop(full, x0, y0, side)` (resampled to 4096²) expose the
  steps for the pipeline's recipe.
- `needs_mask(mask) -> bool`: `mask.max() > 30`, to be applied to the **raw** crop.
- `imprint(rgb, alpha, *, sea_texture_blur=0.0, zl, clean_halo=False) -> np.ndarray`: RGBA
  `(4096, 4096, 4)`; `alpha` is copied as the fourth channel. With `sea_texture_blur > 0` the
  RGB is Gaussian-blurred with radius `sea_texture_blur * 2**(zl - 17)` and the blurred pixels
  replace the originals where the mask says water, weighted by `(255 - alpha) / 255`. With
  `clean_halo` the alpha is set to 0 where `1 <= alpha <= 253` and the imagery is nearly white
  or nearly black (Ortho4XP's rule, applied to the water alpha).
- `clean_halo_mask(mask, rgb)` and `sea_blur_radius(sea_texture_blur, zl)` expose the two
  Ortho4XP rules for the combined-provider compositor of P4.

## 4. Decision

| Rule | Decision |
|---|---|
| mask cell and crop arithmetic, threshold 30 | keep (test: the 7 masked textures of +43+005 are exactly the 7 for which `needs_mask` is true; the 10 others have no mask file) |
| bicubic resampling of the crop when `zl > mask_zl` | keep, Pillow `BICUBIC` |
| threshold evaluated on the crop before resampling | **keep** (review: the earlier "tolerated difference", evaluating the threshold on the resampled mask, was withdrawn because bicubic overshoot pushed a crop whose maximum is 30 next to zeros above 30 and the module API then contradicted the pipeline). `mask_for_texture` and `needs_mask_for_texture` decide on `mask_crop_raw`, exactly as `O4_Mask_Utils.py:56-60`; test: a 2048² window of 30s and 0s at `zl = mask_zl + 1` gives `None` / False although the resampled crop peaks above 30 |
| `putalpha` | keep |
| sea blur (`sea_texture_blur`) | **extended**: Ortho4XP only blurs the sea layer of combined providers; OrthoStudio XP applies the same radius to the water part of any imprinted texture (the hint of the parameter, "smoothen some sea imageries where the wave pattern was too present", applies just as well to Bing over the Mediterranean). Off by default, off in the reference cfg, so the oracle is unaffected. To be confirmed by the user; **until then the CLI does not wire the cfg's `sea_texture_blur` into it** (an Ortho4XP single-provider tile has no blur whatever the cfg says); the extension is reached through the explicit `--sea-blur` option only (`pipeline-textures.md` section 9) |
| anti-halo | ported as `clean_halo_mask`; not applied by default in `imprint` (Ortho4XP does not apply it to the water alpha either) |
| DXT1/DXT5 rebuild rules by file size and mtime (`O4_DSF_Utils.py:715-758`) | drop: the store key of the texture covers the mask digest and `imprint_masks_to_dds` |
| per-texture `<y>_<x>_ZL<zl>.png` written then deleted | drop when imprinting (in-memory); when `imprint_masks_to_dds` is false the file is the `BORDER_TEX` of the `.ter` and is written as in Ortho4XP: the raw crop, `4096 // factor` px (`pipeline-textures.md` section 3) |
| GeoTIFF duplicate of `needs_mask` | drop (one implementation) |

## 5. Acceptance tests (`tests/test_textures_imprint.py`; the oracle part left with decision 0010)

- `mask_for_texture` at `zl == mask_zl` returns the mask unchanged; at `zl = mask_zl + 1` and
  `+ 2` the crop offsets follow the Ortho4XP formula (synthetic mask with distinct quadrants).
- `needs_mask`: `max == 30` → False, `31` → True.
- Oracle: for the 7 DXT5 textures of the reference, `imprint(JPEG Ortho4XP, mask_for_texture)`
  compared to level 0 of the reference DDS decoded (`orthostudio.textures.bcdecode`):
  alpha equal on ≥ 98.5 % of the pixels, within 1 on ≥ 99 %, `max |Δ| <= 16` (BC3 3-bit
  alpha indices in gradient blocks); RGB PSNR ≥ 32 dB (BC1 quantisation). Measured values in
  section 6. For the 10 DXT1 textures `mask_for_texture` returns `None`.
- The reference DDS whose mask is barely above the threshold (6000_8416: 0.012 % of the pixels
  above 30) is DXT5, as `needs_mask` says.
- `imprint` with `sea_texture_blur` leaves land pixels untouched and changes water pixels;
  `clean_halo_mask` zeroes only transition pixels over white/black imagery.

## 6. Measurements (reference DDS level 0 versus `imprint(JPEG Ortho4XP, mask)`)

| Texture | alpha exact | within 1 | within 4 | max abs | RGB PSNR (dB) |
|---|---|---|---|---|---|
| 5984_8416 | 99.39 % | 99.67 % | 99.93 % | 16 | 34.46 |
| 5984_8432 | 99.74 % | 99.87 % | 99.97 % | 15 | 32.55 |
| 6000_8416 | 99.99 % | 99.99 % | 99.999 % | 14 | 48.01 |
| 6000_8432 | 98.84 % | 99.38 % | 99.86 % | 16 | 35.14 |
| 6000_8448 | 99.02 % | 99.47 % | 99.88 % | 16 | 34.81 |
| 6000_8464 | 99.72 % | 99.86 % | 99.98 % | 14 | 34.92 |
| 6016_8448 | 99.86 % | 99.92 % | 99.98 % | 15 | 47.63 |

The alpha differences sit on the shoreline gradient where BC3 has 8 alpha codes per 4×4
block; the RGB differences are BC1's. The mask itself is therefore reproduced exactly: the
residual is the encoder's.

## 7. Wanted differences from Ortho4XP

- Sea blur available for single providers (section 4), off by default and not driven by the
  Ortho4XP cfg.
- No PNG round trip, no file-size heuristics, no mtime. The threshold is the Ortho4XP one, on the
  raw crop (the resampled-crop variant of the first draft was withdrawn by the review).
