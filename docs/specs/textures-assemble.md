# Textures: assembling a 4096² texture from 256 web-mercator chunks

Status: P1, written before the code. Code: `src/orthostudio/textures/assemble.py`.
Measurements: section 6 (scratchpad script `bench_decode.py`, M4 Pro, `nice -n 10`, load 4.7).

## 1. The rule in plain language

A texture at zoom level `zl` is the 16×16 grid of 256 px web-mercator chunks whose top-left
chunk is `(til_x, til_y)` (both multiples of 16). Chunk `i` (0..255, row-major: `i = 16*row +
col`) is pasted at pixel `(256*col, 256*row)`. The result is an RGB 4096×4096 image.

A chunk that the provider did not deliver (placeholder, 404, network failure) is replaced by the
corresponding quadrant of the nearest available parent chunk (`zl-1`, `zl-2`, ...), cropped and
upsampled bicubically. When no parent is available either, Ortho4XP silently pastes white;
OrthoStudio XP fills the chunk with the mean colour of its already-filled neighbours **and reports
its index** so that the texture is marked incomplete (`IMG_TILE_MISSING`) and can be retried.

## 2. Origin in Ortho4XP

| Where | What |
|---|---|
| `src/O4_Imagery_Utils.py:1329-1368` (`build_texture_from_tilbox`) | `Image.new("RGB", (4096, 4096))`, chunk `(montx, monty)` pasted at `(256*montx, 256*monty)`, 16 download threads |
| `src/O4_Imagery_Utils.py:1305-1322` (`get_and_paste_wmts_part`) | `big_image.paste(small_image, (x0, y0))`; the pasted image is whatever Pillow decoded (JPEG or PNG, any mode) |
| `src/O4_Imagery_Utils.py:1003-1092` (`http_request_to_image`) | Bing placeholder (`Content-Length: 1033` on `virtualearth`) and ArcGIS placeholder (`2521` on `arcgisonline`) are turned into `[404]`; an undecodable body is retried (`max_baddata_retries`), any other failure ends with `(0, status)` |
| `src/O4_Imagery_Utils.py:1244-1289` (`get_wmts_image`) | parent fallback: on `[404]` the chunk indices are halved (`til_x //= 2`, `til_y //= 2`, `tilematrix -= 1`, `down_sample += 1`) and the request is repeated; when `down_sample >= 6` a **white** 256² image is returned with `success = 0`. A successful parent is cropped at `x0 = (til_x_orig - 2**d * til_x) * 256 // 2**d` (same for y), size `256 // 2**d`, then `resize((256, 256), Image.BICUBIC)` |
| `src/O4_Imagery_Utils.py:1274-1286` | non-webmercator providers and any non-`[404]` failure give white immediately |
| `src/O4_Imagery_Utils.py:1572-1582` | the texture is saved as JPEG (Pillow defaults: quality 75, 4:2:0) even when a part is white; a single line at verbosity 1 says "it was filled with white there" |

## 3. Inputs and outputs

`decode_tile(data: bytes) -> np.ndarray`: RGB uint8 `(256, 256, 3)` from a JPEG or PNG body
(Pillow; palette, grey and RGBA inputs are converted to RGB). Any other size or an undecodable
body raises `OsxpError("IMG_TILE_CORRUPTED")`.

`assemble_texture(container, fallback=None) -> (rgb, missing)`:

- `container.entries[i]` (256 entries, order of `texture_tiles`) has `status` (`ChunkStatus`:
  `OK = 0`, `MISSING = 1`, `PLACEHOLDER = 2`, `ERROR = 3`) and `data`.
- Chunk `i` is decoded when its status is `OK`; an `OK` chunk whose body does not decode is
  treated like `ERROR` (Ortho4XP would have retried it at download time).
- Every other chunk is asked to `fallback(i)`; the callable returns an RGB `(256, 256, 3)`
  array or `None`. In P1 the fallback is injected; `parent_fallback()` builds the Ortho4XP one
  from a `get_parent(x, y, zl) -> bytes | None` accessor (the integrator wires it to the
  chunk store and fetcher).
- Chunks that neither the container nor the fallback provided are filled with the mean colour
  of their filled 8-neighbours (falling back to the mean of the whole texture, then to
  mid-grey 128) once every other chunk is in place.
- `missing` lists, in ascending order, every index **not taken from the container** (filled by
  the fallback or by the neighbourhood mean). `assemble_texture_detailed()` returns the same
  image with the two lists separated (`from_fallback`, `unfilled`), which is what the
  integrator uses to raise `IMG_TILE_PARENT_FALLBACK` (info) and `IMG_TILE_MISSING`
  (degraded, texture retried later).

`parent_fallback(t, get_parent, *, max_levels=5)`: for chunk `i` at `(x, y, zl)` tries
`(x >> d, y >> d, zl - d)` for `d = 1..max_levels`; the first body that decodes is cropped
with the Ortho4XP formula and upsampled with Pillow `BICUBIC`. The Ortho4XP loop gives up when
`down_sample` reaches 6, so it requests parents at `d = 1..5`: **five** levels, not six.
`max_levels=5` is the default; the integrator may raise it.

## 4. Decision

| Rule | Decision |
|---|---|
| grid geometry and paste order | keep (byte-identical for lossless chunks, test `test_assemble_png_round_trip`) |
| parent crop formula and bicubic upsampling | keep, Pillow does the resize so that the pixels match Ortho4XP |
| five parent levels | keep as the default |
| white fill when no parent exists | **fix**: neighbourhood mean plus the index in `missing`; Ortho4XP's white square in the middle of the sea is the most reported silent degradation (`docs/specs/errors.md`, `IMG_TILE_MISSING`) |
| JPEG q75 re-encoding of the assembled texture | **drop**: OrthoStudio XP keeps the raw chunks in the chunk container and encodes DDS from the assembled array; no second lossy step (the Ortho4XP cache costs 3-4 dB on top of the provider's JPEG) |
| 16 threads per texture | drop (network is the fetcher's business) |

## 5. Acceptance tests (`tests/test_textures_assemble.py`)

- 256 PNG chunks cut from a synthetic image reassemble **byte-identically**.
- 256 JPEG q75 chunks cut from an Ortho4XP texture reassemble at PSNR ≥ 40 dB against that texture
  (measured 64.8 dB on 5984_8448; JPEG-on-JPEG at the same quality is nearly idempotent).
  Measured with the Ortho4XP caches, until decision 0010.
- A missing chunk with an available parent is replaced by the parent quadrant, bicubic, and
  reported in `missing`; the crop offsets follow the Ortho4XP formula (test against a hand-built
  parent whose quadrants have distinct colours, at `d = 1, 2, 5`; `d = 6` is never asked).
- A missing chunk without parent is filled with its neighbours' mean and reported.
- An `OK` chunk with a corrupted body behaves like `ERROR`.
- `decode_tile` refuses wrong sizes and undecodable bodies with `IMG_TILE_CORRUPTED`.

## 6. Measurements (decode paths, 256 chunks of one 4096² texture, mean of 3 runs)

| Path | JPEG q75 (16.6 kB/chunk) | PNG (122 kB/chunk) |
|---|---|---|
| `Image.open` + `np.asarray` | 195 µs/chunk, 50 ms/texture | 1 028 µs/chunk, 263 ms |
| `Image.open` + `load` + `frombuffer(tobytes)` | 194 µs | 1 090 µs |
| `draft("RGB", (256, 256))` then `asarray` | 208 µs | 1 037 µs |
| Pillow `paste` into `Image.new` (Ortho4XP) | 61 ms/texture | 297 ms |
| numpy slice assignment (OrthoStudio XP) | 53 ms/texture | 279 ms |
| parent crop + bicubic resize | 317 µs/chunk | — |

Draft mode brings nothing at native size (it only helps when downscaling by 2+), `frombuffer`
is not faster than `asarray`, and the numpy slice assignment saves 13 % over `paste`. Decoding
dominates: 50 ms per texture, 11 s for a ZL16 tile (221 textures) on one core, negligible
against the 0.33 s BC1 encode per texture. So: `Image.open` + `np.asarray`, no draft mode,
no `frombuffer`.

BC1 quantisation of the reference DDS against the Ortho4XP JPEG measures 32.5-48 dB (level 0,
seven textures); this is the "±JPEG" band any RGB comparison to an Ortho4XP DDS must allow.

## 7. Wanted differences from Ortho4XP

- Irrecoverable chunks: neighbourhood mean and a reported index instead of silent white.
- No JPEG re-encoding of the assembled texture.
- The parent fallback is a pure function over an accessor; Ortho4XP re-downloads parents for every
  missing chunk of every texture (no memo). Memoisation is the integrator's/store's job.
