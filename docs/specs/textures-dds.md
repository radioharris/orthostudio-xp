# Textures: DDS container, mip chain and BC1/BC3 encoding

Status: P0, written before the P1 imagery stage. Code: `src/orthostudio/textures/` (`dds.py`,
`mips.py`, `encode.py`, `refine.py`, `bcdecode.py`). Measurements: `docs/benchmarks/dds-encoder.md`.

## 1. The rule in plain language

Every 4096x4096 orthophoto texture of a tile is stored as a DDS file with a full mip chain
(13 levels, down to 1x1). Textures without water are DXT1 (BC1, 8 bytes per 4x4 block,
alpha ignored); textures on which a water mask is imprinted are DXT5 (BC3, 16 bytes per block,
8-bit interpolated alpha where 255 = land and 0 = water). X-Plane reads the header, the format
and the levels; nothing else in the file matters to it.

## 2. Origin in Ortho4XP

| Where | What |
|---|---|
| `src/O4_Imagery_Utils.py:2380-2453` | conversion: a masked texture gets `big_image.putalpha(mask)` and is saved as a temporary PNG RGBA (`tmp/*.png`); an unmasked one is passed to nvcompress straight from the cached JPEG |
| `src/O4_Imagery_Utils.py:2435-2452` | `nvcompress -bc1 -fast in out` or `nvcompress -bc3 -fast in out` (nvtt 2.1.0, x86_64 binaries in `Utils/{mac,win,lin}`) ; no `-alpha`, no `-mipfilter`, no `-nomips` |
| `src/O4_Mask_Utils.py:38-60` (`needs_mask`) | the mask crop for the texture is read from `Masks/<lat lon>/<y>_<x>.png`; if its maximum is <= 30 the texture is not masked (DXT1) |
| `src/O4_DSF_Utils.py:715-758` | a texture is (re)built when its file is missing, when `imprint_masks_to_dds` is on and the file is smaller than 20 000 000 bytes (a DXT1 where a DXT5 is needed), when the mask is newer than the file, or when imprinting is off and the file is larger than 20 000 000 bytes |
| `src/O4_Imagery_Utils.py:2396-2408` | once imprinted, the per-texture mask PNG in `textures/` is deleted |

## 3. Expected file layout (verified on the +43+005 baseline, 17 textures)

Sizes: DXT1 11 184 952 bytes, DXT5 22 369 776 bytes, that is 128 bytes of header plus the sum
of the 13 levels (4096² ... 1²; a level smaller than 4x4 still occupies one block).

128-byte header, all little-endian, as nvcompress writes it:

| Offset | Field | Value |
|---|---|---|
| 0 | magic | `DDS ` |
| 4 | dwSize | 124 |
| 8 | dwFlags | 0x000A1007 = CAPS, HEIGHT, WIDTH, PIXELFORMAT, MIPMAPCOUNT, LINEARSIZE |
| 12 / 16 | dwHeight / dwWidth | 4096 / 4096 |
| 20 | dwPitchOrLinearSize | size of level 0: 8 388 608 (DXT1) or 16 777 216 (DXT5) |
| 24 | dwDepth | 0 |
| 28 | dwMipMapCount | 13 |
| 32 | dwReserved1[11] | nvtt writes `UVER`, 0, `NVTT`, 0x00020100 in entries 7-10; **OrthoStudio XP writes zeros** |
| 76 | ddspf.dwSize / dwFlags | 32 / 0x4 (FOURCC) |
| 84 | ddspf.dwFourCC | `DXT1` or `DXT5` (masks and bit count 0) |
| 108 | dwCaps | 0x00401008 = COMPLEX, TEXTURE, MIPMAP |
| 112 | dwCaps2..4, dwReserved2 | 0 |

`build_header()` reproduces this byte for byte apart from `dwReserved1` (test:
`tests/test_textures_dds.py::test_header_matches_reference_except_writer_tag`). Without mips
the MIPMAPCOUNT flag and the COMPLEX/MIPMAP caps are dropped.

## 4. Mip chain (measured, not read: nvtt is not part of Ortho4XP's sources)

Experiment (`docs/benchmarks/dds-encoder.md`, section "Mip filter identification"): the levels of
the reference DDS were decoded and compared with three candidate chains computed from the
source JPEG.

- **RGB is box-filtered (2x2 average) in linear light with a 2.2 power law**, not on stored
  values and not with the piecewise sRGB curve: at level 4 (256²) the reference is 34.6 dB from
  the gamma-2.2 chain, 32.6 dB from the stored-value chain, and the mismatch grows with depth
  (37.7 vs 32.1 dB at level 8). gamma 2.2 beats piecewise sRGB by 0.01-0.1 dB at every level.
- **Alpha is box-filtered on its stored values** (no transfer curve): 50.2 dB vs 38.3 dB at
  level 4 for the DXT5 texture 5984_8416.
- **nvtt truncates when it quantises a level back to 8 bits**: the reference levels are on
  average 0.5 code darker than a rounded chain (bias -0.54 with rounding, -0.04 with floor).
- Each level is filtered from the previous one in float, without intermediate quantisation
  (the chain is carried in float32 linear light in OrthoStudio XP as well).

Decision: **keep** the linear-light box filter (`mip_chain(mode="gamma22")`, the default), **fix**
the truncation (OrthoStudio XP rounds to nearest: wanted difference, +0.5 code on every level below
0, imperceptible), keep `"srgb"` (piecewise curve) and `"none"` (stored values, fast, darkens
distant mips) as options. The alpha channel is always averaged on stored values.

## 5. Block encoding

### 5.1 nvcompress -fast (Ortho4XP)

- Never emits a 3-colour block with `color0 < color1`. It does emit single-colour blocks with
  `color0 == color1` and indices 0 or 2 only (90 of 1 048 576 blocks in level 0 of
  5952_8416): decoded in 3-colour mode these give the same colour, never the transparent
  black of index 3.
- Deterministic: re-running nvcompress on the cached JPEG reproduces the baseline DDS byte
  for byte (17/17 textures), so the baseline is a usable oracle for the encoder alone.
- Quality on the 17 real textures, level 0: 33.9-34.9 dB PSNR against the source, SSIM 0.967-0.975.

### 5.2 OrthoStudio XP: ispc_texcomp, then nvcompress, else error

`encode_dds(rgba, fmt, mips=True, *, mip_mode="gamma22", encoder="auto", refine_passes=0)`
returns the complete file image. The chain is:

1. `ispc_texcomp` 1.0.1 (in-process, MIT wheel): `compress_blocks_bc1` / `compress_blocks_bc3`
   on an RGBA uint8 surface. Two quirks of the binding are handled in `encode.py`: for BC1 it
   returns `width*height` bytes (twice the block data; the blocks are contiguous at the start,
   the tail is uninitialised) and it does not size its buffer for surfaces smaller than 4x4
   (heap overflow), so every level is padded to a multiple of 4 by edge replication before
   the call, exactly as nvtt clamps block coordinates to the image.
2. `nvcompress` as a subprocess when a binary is found (`$OSXP_NVCOMPRESS`, then PATH): PNG
   round-trip, the tool
   filters its own mips (truncating), the result is re-headered.
3. `EncoderUnavailableError` naming the remedy (`pip install ispc-texcomp`, or an nvcompress
   binary).

Post-passes applied to every level whatever the encoder (`encode_level`):

- `encode_flat_blocks`: every block whose 16 pixels share one RGB is rewritten with the
  optimal single-colour endpoint pair (stb_dxt/nvtt technique: two endpoints whose 2/3-1/3
  interpolant lands within one code of the colour; tables built once for the decoder's
  `(2*c0 + c1 + 1) // 3` formula). ispc_texcomp alone quantises such blocks to the nearest
  5:6:5 colour, up to 4 codes off, which costs 4-6 dB and visible banding on sea textures
  (75-85 % of their blocks are uniform). 30-60 ms per 4096² level.
- `force_four_colour_mode`: `color0 > color1`, or `color0 == color1` with all indices 0.
  Neither encoder in the chain produces a 3-colour block on the 17 textures; this is a 10 ms
  safety net.
- `refine_passes` (optional, off): least-squares endpoint refinement, `refine.py`.

### 5.3 Quality of ispc_texcomp against nvcompress -fast

On textured blocks ispc_texcomp's BC1 is 0.41 dB PSNR **below** nvcompress -fast (SSIM
-0.0023), at every level down to 32², on both DXT1 colour and DXT5 colour blocks; DXT5 alpha
is within 1 dB. With the flat-block pass, sea textures come out **above** nvcompress
(6016_8448: 48.0 vs 47.6 dB, SSIM 0.976 vs 0.975) and the 1x1 level is exact.
`refine_passes=2` (the refinement nvtt applies once, in numpy) closes the remaining gap
(33.935 vs 33.932 dB on 5952_8416) at 4 s of CPU per texture instead of 0.4 s. The gate
decision is documented in `docs/benchmarks/dds-encoder.md`.

## 6. Inputs and outputs

- Input: one `(4096, 4096, 3|4)` uint8 array (RGB from the imagery stage, alpha from the mask
  stage: 255 land, 0 water; a mask whose maximum is <= 30 means "no mask", DXT1).
- Output: `bytes` of the DDS file; the caller writes it (`.tmp` then rename) next to the
  `.ter` files. Same input, same bytes (tested).
- No temporary file, no PNG, no subprocess on the default path.

## 7. Acceptance tests

- `tests/test_textures_dds.py`: sizes (11 184 952 / 22 369 776 / 2872 for 64²), header
  byte-identical to the baseline apart from `dwReserved1`, container round trips.
- `tests/test_textures_mips.py`: level shapes, mode arithmetic on a checkerboard
  (128 / 186 / 188 for none / gamma22 / srgb), alpha on stored values, transfer-curve round
  trip on all 256 codes, hypothesis property for the stored-value box filter.
- `tests/test_textures_bcdecode.py`: decoder on hand-built blocks (4-colour, 3-colour with
  transparent black, both BC3 alpha modes, pixel order, cropping).
- `tests/test_textures_encode.py`: round trips, tiny levels, determinism, encoder chain
  errors, 4-colour invariant, refinement never increases the block error.
- measured on the real DXT5 texture 5984_8416 of Ortho4XP's cache until decision 0010 removed the
  test: both encoders give the exact sizes and 13 levels, OrthoStudio XP is within 0.6 dB of
  nvcompress at levels 0-6 and above 33 dB, alpha above 50 dB; and nvcompress's mips are closer to
  the linear-light chain than to the stored-value chain at levels 2-6.

## 8. Wanted differences from Ortho4XP

| Difference | Why |
|---|---|
| `dwReserved1` zeroed instead of nvtt's `UVER`/`NVTT` tags | X-Plane ignores the field; no writer identification needed |
| Levels quantised by rounding, nvtt truncates | removes a -0.5 code bias on every mip |
| ispc_texcomp blocks instead of nvtt QuickCompress blocks | not byte-comparable; PSNR/SSIM gate per level instead (benchmark) |
| Single-colour blocks written with indices 0 (nvtt: 0 or 2) | same decoded pixels, simpler invariant |
| No PNG round-trip, no subprocess, no `tmp/` | 1.2-1.7 s per masked texture saved, no leftover files |
| Uniform blocks encoded with the optimal endpoint pair, indices 2 (nvtt: same technique, indices 0 or 2) | same decoded colour within one code; block MSE 0.44 vs 0.56 |
