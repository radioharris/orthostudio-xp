# Probe flight 1: Nice towards Geneva, 26 September 2026

> Measured with X-StreamOrtho, OrthoStudio XP Live's research project, whose probe kit is not part
> of OrthoStudio XP. Its commands and paths are X-StreamOrtho's.

X-Plane 12.4.4-b1 (Apple Silicon, Metal), macOS 26.6.2, M4 Pro 48 GB. ToLiss A320neo, cockpit view,
LFMN departure, climb to FL240 at 425 kt northbound over the Alps. 35 minutes traced, 26.5 minutes
tracked, 215 km flown. The probe pack: 35 tiles of AutoOrtho's `z_ao_eur` mesh with 8,415 test
textures as real files. Texture quality "High" (`_tex_res 4`) until 19:39:25, then "Maximum"; X-Plane
froze at 19:41:07. The report and the freeze's thread sample are kept beside this page, in
`2026-09-26-probe-flight-1/`; the 950 MB trace was not kept.

## How X-Plane reads an ortho texture

- **It maps the file, it does not read it.** Per texture: `stat` of a `.ktx2` twin (absent), `stat`
  of the `.dds`, `open`, `lseek` to the end (the size) and back, `mmap` of the whole file read-only
  and private, page faults on the pages it needs, `close` about a millisecond later. No `read()`.
- **First open, at DSF load: 256 px only.** 3,691 of 3,735 textures were first opened for the header
  and level 4 (256 px) and smaller: about 45 KB of an 11 MB file.
- **Every sharper level is a new open.** 1,276 upgrades, 1,276 through a new `open()` + `mmap`;
  none through a mapping kept from before. A file replaced with `rename()` is therefore seen at
  the next upgrade.
- **Levels come in distance rings**, the same law for both texture zooms: ground resolution
  proportional to distance, about 0.18 m per pixel per km (3.4 m/px at 21 km for ZL16).

| level | ZL16 texture (7 km) | ZL18 texture (1.8 km) |
|---|---|---|
| 4 (256 px) | at DSF load, 130-230 km | at DSF load |
| 3 (512 px) | 72 km | 18 km |
| 2 (1024 px) | 38 km | 10 km |
| 1 (2048 px) | 21 km | 5 km |
| 0 (4096 px) | under 12 km, **only with texture quality Maximum** | 3 km, same |

- **Texture quality below Maximum drops the top level.** 27 minutes at "High": no texture ever
  read at 4096 px, not even overhead (24 ZL16 textures passed within 5 km, all stopped at 2048).
  1 min 40 at "Maximum": 6 textures read at 4096 px, all within 12 km.

## Timing and demand

- DSFs of the tiles ahead are opened about 170 km from the tile's centre.
- From a tile's DSF load to 2048 px of its textures: p10 8 min, median 14 min (en route).
- At 380-425 kt, X-Plane asked for 2048 px of 11 textures a minute (median; 13 at most). Level 0 at
  "Maximum" would be about a quarter of that (a 12 km radius against 21 km, squared).
- 1.2 GB of test texture data read from the disk in 35 minutes.

## Swap test (rename() of a clone of template B over a texture, during the flight)

| swapped | textures | read the new file | where X-Plane first read it |
|---|---|---|---|
| 15-40 km ahead | 163 | 143 | 38 km (next ring), 11 s after the swap |
| 50-100 km ahead | 19 | 8 | 71 km (next ring) |
| 90-160 km ahead | 372 | 147 | 134 km: its first open, at DSF load |

The rest were not opened again before the freeze. Delivered before the DSF load, a texture is read
from the new file from its first open on: X-Plane never sees the placeholder.

## Measurement notes

- `fs_usage` on macOS 26 prints a `pread` offset as `(bytes << 32) | offset`.
- Page faults show as disk reads (`PgIn[A] D=<block> B=<bytes> <path>`) without a file offset; the
  test textures being APFS clones of two templates, the block gives the offset and the version.
- The kernel reads 128 KB ahead of the header page and a page or two behind a fault: a level
  counts as asked for when at least half of it was read in one open.

## The freeze

1 min 40 after switching texture quality to Maximum (X-Plane logged "Runloop is backed up ...
estimated runtime: 59.515s" at once), the main thread blocked for good in
`-[_MTLCommandBuffer waitUntilScheduled]`, under `XPPDoModernDrawingHook` (plugin panel drawing:
the ToLiss displays), while the sound thread kept running. No thread was in a file read or a page
fault, and there was no FUSE file system at all. `2026-09-26-probe-flight-1/xplane-freeze-sample.txt` holds the
3-second sample of all 68 threads.
