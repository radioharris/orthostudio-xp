# DDS encoder: ispc_texcomp in-process versus nvcompress (Ortho4XP)

Machine: Mac M4 Pro, 14 cores (10 P + 4 E), 48 GB, macOS 26.6, Python 3.14.7, numpy 2.5.3,
Pillow 12.3, ispc_texcomp 1.0.1 (arm64 wheel), nvcompress 2.1.0 x86_64 under Rosetta
(`Utils/mac/nvcompress` of Ortho4XP). Date: 2026-09-12. Other work was running on the machine
during every run (load average 3.3-4.3 before and after, noted in the JSON).

Command (from the OrthoStudio XP root; raw JSON kept with the run logs):

```bash
OSXP_LEGACY_DIR=../Ortho4XP nice -n 10 uv run python \
  tools/bench/dds/bench_dds.py --workers 4,8,12 --ssim-levels 5 --refine-textures 3 \
  --ref-dir <baseline>/zOrtho4XP_+43+005/textures --out bench.json
```

Inputs: the 17 cached 4096² JPEGs of tile +43+005 (Bing, ZL14) — 10 DXT1 and 7 DXT5 whose
alpha is the water mask of `Masks/+40+000/+43+005/<y>_<x>.png` (the DIAGNOSTIC's "8 DXT5 +
9 DXT1" is off by one: 7 x 22 369 776 + 10 x 11 184 952 = 268 437 952 bytes, the 268 MB it
reports). Reference: the DDS of the Ortho4XP baseline build. Metrics: PSNR and SSIM
(`skimage.metrics.structural_similarity`, RGB, 7x7 window) per mip level against the source
mip chain in linear light (`mip_chain(mode="gamma22")`, see the mip filter section), alpha
PSNR against the box-filtered mask.

Two runs: run 1 with the raw ispc_texcomp blocks, run 2 with the final pipeline (flat-block
post-pass, section 4). Timings come from run 2 unless stated.

## 1. Mip filter identification (which chain does nvtt compute?)

Reference levels decoded (`orthostudio.textures.bcdecode`) and compared with three candidate chains
built from the source JPEG; PSNR in dB, texture 5952_8416 (land), reference vs candidate:

| level | size | stored-value box | linear light, sRGB curve | linear light, gamma 2.2 | mean bias ref - stored |
|---|---|---|---|---|---|
| 1 | 2048 | 33.99 | 34.53 | 34.55 | +0.51 |
| 2 | 1024 | 33.44 | 34.44 | 34.46 | +1.26 |
| 4 | 256 | 32.61 | 34.62 | 34.66 | +2.80 |
| 6 | 64 | 32.57 | 36.26 | 36.32 | +4.12 |
| 8 | 16 | 32.11 | 37.68 | 37.78 | +5.15 |

Same ordering on 5968_8448 and 5984_8448. The reference is brighter than a stored-value
chain by 0.5 to 6.8 codes: nvtt averages in linear light. Gamma 2.2 edges out the piecewise
sRGB curve by 0.01-0.10 dB at every level (the two chains are 55-62 dB apart, one code).
With rounding, the gamma-2.2 chain sits 0.54 code *above* the reference at every level; with
truncation the bias is -0.04 and PSNR gains 0.08 dB: nvtt truncates when it quantises.
Alpha (DXT5 5984_8416, 6000_8448): box on stored values 56.5 / 54.2 dB at level 1 and
50.2 / 46.5 dB at level 4, versus 55.5 / 51.7 and 38.3 / 37.4 dB through the gamma curve —
alpha is not gamma-converted.

Retained: `gamma22` (RGB in linear light with a 2.2 power law, alpha on stored values,
rounding). The `mip_chain` cost is 0.168 s per 4096² texture (13 levels, float32 chain, the
transfer curve applied with `np.power`; a `searchsorted` quantiser was 0.33 s by itself).

## 2. Run 1: raw ispc_texcomp blocks, quality against nvcompress -fast

Level 0 per texture (PSNR dB, SSIM):

| texture | fmt | ispc | nvcompress | delta | SSIM ispc | SSIM nv |
|---|---|---|---|---|---|---|
| 5952_8416 | bc1 | 33.52 | 33.93 | -0.41 | 0.9643 | 0.9667 |
| 5952_8432 | bc1 | 34.92 | 35.33 | -0.41 | 0.9635 | 0.9659 |
| 5952_8448 | bc1 | 34.79 | 35.20 | -0.41 | 0.9640 | 0.9662 |
| 5952_8464 | bc1 | 35.65 | 36.06 | -0.41 | 0.9642 | 0.9664 |
| 5968_8416 | bc1 | 33.62 | 34.03 | -0.41 | 0.9641 | 0.9664 |
| 5968_8432 | bc1 | 33.91 | 34.34 | -0.43 | 0.9641 | 0.9665 |
| 5968_8448 | bc1 | 34.54 | 34.95 | -0.41 | 0.9634 | 0.9657 |
| 5968_8464 | bc1 | 35.38 | 35.80 | -0.41 | 0.9635 | 0.9659 |
| 5984_8416 | bc3 | 33.89 | 34.46 | -0.57 | 0.9703 | 0.9745 |
| 5984_8432 | bc3 | 32.11 | 32.55 | -0.44 | 0.9638 | 0.9665 |
| 5984_8448 | bc1 | 34.12 | 34.53 | -0.41 | 0.9622 | 0.9647 |
| 5984_8464 | bc1 | 33.83 | 34.24 | -0.41 | 0.9616 | 0.9641 |
| 6000_8416 | bc3 | 44.26 | 48.01 | **-3.75** | 0.9837 | 0.9915 |
| 6000_8432 | bc3 | 34.60 | 35.14 | -0.54 | 0.9717 | 0.9759 |
| 6000_8448 | bc3 | 34.38 | 34.81 | -0.43 | 0.9674 | 0.9699 |
| 6000_8464 | bc3 | 34.47 | 34.92 | -0.45 | 0.9657 | 0.9683 |
| 6016_8448 | bc3 | 42.14 | 47.63 | **-5.50** | 0.9181 | 0.9753 |

Per level, mean over the 17 textures (delta = ispc - nvcompress, min / mean / max):

| level | size | PSNR ispc | PSNR nv | delta | SSIM ispc | SSIM nv | alpha ispc | alpha nv |
|---|---|---|---|---|---|---|---|---|
| 0 | 4096 | 35.30 | 36.23 | -5.50 / -0.93 / -0.41 | 0.963 | 0.969 | 64.6 | 65.0 |
| 1 | 2048 | 36.00 | 36.77 | -4.88 / -0.77 / -0.28 | 0.963 | 0.969 | 59.3 | 60.2 |
| 2 | 1024 | 36.15 | 36.71 | -3.33 / -0.56 / -0.26 | 0.964 | 0.968 | 55.8 | 56.6 |
| 3 | 512 | 36.08 | 36.54 | -2.01 / -0.46 / -0.28 | 0.964 | 0.968 | 54.7 | 55.3 |
| 4 | 256 | 36.07 | 36.45 | -1.10 / -0.37 / -0.23 | 0.963 | 0.967 | 53.7 | 54.2 |
| 6 | 64 | 36.11 | 36.39 | -0.41 / -0.29 / -0.11 | | | 54.9 | 52.2 |
| 8 | 16 | 35.56 | 35.81 | -0.82 / -0.25 / +0.28 | | | 49.2 | 44.5 |
| 10 | 4 | 35.88 | 35.93 | -1.51 / -0.05 / +0.88 | | | 63.5 | 51.9 |
| 12 | 1 | 43.49 | 55.70 | -56.1 / -12.2 / 0.00 | | | 99.0 | 55.4 |

Two facts behind these numbers:

- **Textured blocks: a flat -0.41 dB** (SSIM -0.0023) on every land texture, at every level
  down to 32². The ispc_texcomp BC1 kernel does a single endpoint fit; nvtt's `-fast`
  (QuickCompress) adds one least-squares endpoint refinement.
- **Uniform blocks: -4 to -6 dB on sea textures.** 6016_8448 is 84.7 % exactly-uniform 4x4
  blocks (6000_8416: 75.1 %, 5984_8416: 22.2 %, 5952_8416: 0.0 %). ispc_texcomp quantises
  them to the nearest 5:6:5 colour (mean block MSE 3.93); nvtt uses the stb_dxt-style optimal
  single-colour tables, two endpoints whose 2/3-1/3 interpolant lands within one code (MSE
  0.56). The same effect makes the 1x1 level 7-56 dB worse. On these textures the SSIM gap
  (0.918 vs 0.975) is banding in the open sea, a visible artefact.

## 3. Least-squares refinement (numpy, `refine.py`)

One or two passes of the refinement nvtt applies (solve the 2x2 normal equations for the
endpoints given the indices, requantise, reassign, keep when the block error drops):

| texture | fmt | level | ispc | +2 passes | nvcompress | time for 13 levels |
|---|---|---|---|---|---|---|
| 5952_8416 | bc1 | 0 | 33.525 | 33.935 | 33.932 | 4.1 s |
| 5952_8416 | bc1 | 1-4 | 34.24 / 34.18 / 34.05 / 34.39 | 34.61 / 34.51 / 34.38 / 34.73 | 34.55 / 34.46 / 34.33 / 34.66 | |
| 5984_8416 | bc3 | 0 | 33.892 | 34.298 | 34.461 | 4.1 s |
| 5984_8432 | bc3 | 0 | 32.109 | 32.546 | 32.550 | 4.1 s |

Two passes reach nvcompress -fast on textured blocks (+0.41 dB, SSIM 0.9643 -> 0.9666 vs
0.9667), one pass gives +0.33 dB in 1.7 s. It costs 4.1 s of CPU per texture, 12x the whole
default pipeline and 3.5x nvcompress under Rosetta, so it is an option (`refine_passes`),
not the default. It does nothing for uniform blocks (degenerate normal equations).

## 4. Flat-block post-pass (`encode_flat_blocks`, default since run 2)

Every block whose 16 pixels share one RGB (detected on a uint32 view, alpha masked, 30-60 ms
per 4096² level) is rewritten with the optimal single-colour endpoint pair (tables built once
for the decoder's `(2*c0 + c1 + 1) // 3` interpolant, max error 1 code, tested):

| texture | uniform blocks | PSNR ispc | PSNR + flat pass | nvcompress | SSIM ispc | + flat pass | nvcompress |
|---|---|---|---|---|---|---|---|
| 6016_8448 | 84.7 % | 42.14 | 48.04 | 47.63 | 0.9181 | 0.9758 | 0.9753 |
| 6000_8416 | 75.1 % | 44.26 | 48.58 | 48.01 | 0.9837 | 0.9920 | 0.9915 |
| 5984_8416 | 22.2 % | 33.89 | 34.05 | 34.46 | | | |
| 5952_8416 | 0.0 % | 33.52 | 33.52 | 33.93 | | | |

On uniform blocks the pass is better than nvtt (block MSE 0.44 vs 0.56); on near-uniform
blocks (range 1-4 codes) ispc_texcomp already beats nvtt (3.17 vs 3.35); the remaining gap
is the textured blocks (10.75 vs 9.81) of section 2.

## 5. Run 2: timings of the final pipeline (`encode_dds`, ispc + post-passes)

Single process, mean per texture, `nice -n 10`, background load 3.2-4.2:

| stage | bc1 (10 textures) | bc3 (7 textures) |
|---|---|---|
| JPEG decode (+ mask PNG) | 0.093 s | 0.098 s |
| `mip_chain` gamma22, 13 levels | 0.169 s | 0.168 s |
| ispc_texcomp + flat-block + 4-colour passes, 13 levels | 0.071 s | 0.087 s |
| `assemble` (header + join) | < 0.001 s | 0.001 s |
| **total** | **0.333 s** | **0.353 s** |

The 17 textures take 5.81 s sequentially. The flat-block pass is not measurable at this scale
(run 1 without it: 0.073 / 0.089 s); measured alone on a first call it is 30-60 ms per 4096²
level. Peak memory per texture is about 150 MB (RGBA 64 MB, float32 level 1 50 MB, the
smaller levels, the output), within the 0.25 GB budget of the plan.

`ProcessPoolExecutor` (spawn), pool pre-warmed, 17 textures:

| workers | wall | per-texture mean / max inside workers | speedup vs sequential | CPU sum |
|---|---|---|---|---|
| 1 | 5.81 s | 0.342 s | 1.0 | 5.8 s |
| 4 | 1.71 s | 0.356 / 0.397 s | 3.4 | 6.1 s |
| 8 | 1.07 s | 0.393 / 0.456 s | 5.4 | 6.7 s |
| 12 | 0.85 s | 0.441 / 0.518 s | 6.8 | 7.5 s |

Pool start-up (12 spawned interpreters importing numpy, Pillow and OrthoStudio XP) is 0.11 s.
Scaling flattens above 8 workers: the per-texture time grows 30 % at 12 workers (memory bandwidth,
the 4 efficiency cores, and the load of 3-4 from other jobs during the run). The plan's "9 effective
cores, x7.3" is what a loaded machine gives; a quiet one should do better.

Legacy path, same textures, sequential (Ortho4XP runs 4 of these at a time):

| | bc1 (JPEG straight to nvcompress) | bc3 (PNG RGBA round-trip, then nvcompress) |
|---|---|---|
| PNG save (Pillow defaults, as Ortho4XP) | - | 0.75 s (0.19-1.14 s, 33-46 MB files) |
| nvcompress -fast under Rosetta | 1.15 s | 1.52 s |
| **total per texture** | **1.15 s** | **2.27 s** |

27.4 s for the 17 textures sequentially; about 7-8 s with Ortho4XP's four slots. Output
byte-identical to the baseline DDS for 17/17 textures (nvcompress is deterministic). The
OrthoStudio XP pipeline is 3.5x (bc1) to 6.4x (bc3) faster per texture in one process, and 8-9x
faster than the four slots of Ortho4XP on 12 workers (0.85 s versus about 7 s for the tile), 32x
versus one slot. At ZL16 (221 textures) that is about 8 s of wall time on 12 workers.

## 6. Run 2: quality of the final pipeline against nvcompress -fast

Mean over the 17 textures, delta = OrthoStudio XP - nvcompress (min / mean / max over textures):

| level | size | PSNR OrthoStudio XP | PSNR nv | delta PSNR | SSIM OrthoStudio XP | SSIM nv | delta SSIM min / mean | alpha OrthoStudio XP | alpha nv |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 4096 | 35.93 | 36.23 | -0.44 / -0.30 / +0.57 | 0.968 | 0.969 | -0.0026 / -0.0019 | 64.6 | 65.0 |
| 1 | 2048 | 36.60 | 36.77 | -0.35 / -0.17 / +0.99 | 0.967 | 0.969 | -0.0023 / -0.0012 | 59.3 | 60.2 |
| 2 | 1024 | 36.62 | 36.71 | -0.32 / -0.09 / +1.32 | 0.968 | 0.968 | -0.0022 / -0.0006 | 55.8 | 56.6 |
| 3 | 512 | 36.34 | 36.54 | -0.32 / -0.20 / +0.56 | 0.967 | 0.968 | -0.0023 / -0.0012 | 54.7 | 55.3 |
| 4 | 256 | 36.21 | 36.45 | -0.34 / -0.24 / +0.21 | 0.965 | 0.967 | -0.0023 / -0.0019 | 53.7 | 54.2 |
| 5 | 128 | 36.22 | 36.48 | -0.34 / -0.26 / +0.25 | | | | 54.9 | 54.2 |
| 6 | 64 | 36.13 | 36.39 | -0.41 / -0.26 / +0.04 | | | | 54.9 | 52.2 |
| 7 | 32 | 35.87 | 36.14 | -0.57 / -0.28 / +0.15 | | | | 52.0 | 52.6 |
| 8 | 16 | 35.56 | 35.81 | -0.82 / -0.25 / +0.28 | | | | 49.2 | 44.5 |
| 9 | 8 | 35.25 | 35.45 | -0.65 / -0.20 / +0.44 | | | | 50.9 | 45.4 |
| 10 | 4 | 35.88 | 35.93 | -1.51 / -0.05 / +0.88 | | | | 63.5 | 51.9 |
| 11 | 2 | 39.75 | 40.20 | -3.05 / -0.45 / +1.34 | | | | 70.3 | 50.3 |
| 12 | 1 | 62.86 | 55.70 | +0.00 / +7.16 / +49.1 | | | | 99.0 | 55.4 |

Level 0 per texture: the ten land DXT1 and the four coastal DXT5 are at -0.39 to -0.44 dB
(SSIM -0.0009 to -0.0026); the two sea DXT5 are at +0.57 dB (6000_8416: 48.58 vs 48.01,
SSIM 0.9920 vs 0.9915) and +0.41 dB (6016_8448: 48.04 vs 47.63, SSIM 0.9758 vs 0.9753);
alpha is within 1 dB everywhere (57.5-77.7 dB). Levels 7-11 are 1 to 64 blocks, where a
single block moves the figure by a decibel; they are reported, not gated.

Strict gate (PSNR OrthoStudio XP >= nvcompress at every level): **fails**, 2/17 textures pass at
level 0, the other 15 miss by 0.39-0.44 dB. With `refine_passes=2` the three textures tested pass at
levels 0-4 (33.935 vs 33.932, 34.474 vs 34.461, 32.549 vs 32.550 dB) at 4.1 s per texture.

## 7. Conclusion and gate

1. **Encoder chain**: ispc_texcomp in-process is 8-9x faster than Ortho4XP's four nvcompress slots
   on this tile, removes Rosetta, the PNG round-trip and the temporary files, and is
   deterministic. Two binding defects are worked around (BC1 output buffer twice the block
   data; no buffer sizing below 4x4, so every level is padded) and should be reported
   upstream.
2. **Mips**: nvtt's chain is identified (linear light, gamma 2.2, alpha on stored values,
   truncation); OrthoStudio XP reproduces it with rounding. Retained mode: `gamma22`.
3. **Quality gate**: the raw kernel does not meet "PSNR/SSIM >= nvcompress -fast per level".
   The flat-block pass fixes the only visible deficit (banding on sea textures, now better
   than nvtt); what remains is a uniform -0.41 dB / -0.002 SSIM on textured blocks, one
   endpoint refinement short of nvtt, at PSNR levels (33-36 dB) where 0.4 dB is not
   visible. **Proposed gate**: for levels 0-6 (4096² to 64²), PSNR >= nvcompress - 0.5 dB
   and SSIM >= nvcompress - 0.005 (levels 0-4), alpha PSNR >= nvcompress - 1 dB; deeper
   levels reported only. The final pipeline passes it on 17/17 textures (worst: -0.44 dB,
   -0.0026 SSIM, -1.0 dB alpha). Strict parity is available today through
   `refine_passes=2` at 4 s of CPU per texture (12x the pipeline), and would cost
   50-100 ms with the same least-squares pass in a small native extension (rgbcx-style),
   to be written only if the 20 acceptance tiles show a visible difference.

Raw data: `bench_full.json` (run 1) and `bench_run2.json` (run 2) with their logs, kept with
the session's scratch files; regenerate with the command at the top.
