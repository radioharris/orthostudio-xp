# Compared with Ortho4XP, September 2026

This comparison stood in the README from 0.1.0 to 0.1.22. It stays here as it was
measured: OrthoStudio XP as it was in September 2026, against Ortho4XP's code of March 2026, on a
Mac. Each figure comes from the records of this folder. The build times of 1.0, measured alone:
[build-times-1.0.md](build-times-1.0.md).

Same machine (Apple M4 Pro), same tiles, same imagery. Ortho4XP here is its current code, the
master branch of its GitHub repository at commit `26ec00a` (March 2026), which its author
describes as in transition to 1.40; its last release is 1.31. The measurements were made on a
Mac, where Ortho4XP's texture compression tool runs under Rosetta; on Windows and Linux that tool
runs natively, and the gap in compression would be smaller.

| | Ortho4XP | OrthoStudio XP |
|---|---:|---:|
| Build a tile again with nothing changed (Marseille, ZL14) | 63.2 s | **0.3 s** |
| Build a tile built once at ZL14 again at ZL16 | 250.7 s | **33.1 s** |
| Imagery of a ZL16 tile, nothing cached (179 textures) | 206.9 s | **34.6 s** |
| Roads, water, coast and airports of a tile | 31.8 s | **6.6 s** |
| Relief mesh / water masks of a tile | 8.5 s / 4.7 s | **2.8 s / 0.8 s** |
| Download rate from Bing | 219 requests/s | **1,436 requests/s** |
| Compressing one texture | 1.15 s (x86 tool under Rosetta) | **0.33 s** (native, all cores) |

## A real build

Six ZL16 tiles around Lake Geneva and the Alps, built and installed from the page on an M4 Pro:
1,347 textures, 321,792 image pieces, 4.64 GB, every image downloaded. **5 min 22 s**, no error,
no retry. The home connection was used at 16.5 MB/s (132 Mbit/s) on average, 99 to 162 Mbit/s
depending on the tile. No elevation file to download (the relief comes from X-Plane 12), and a
stalled image piece is asked for again instead of costing the tile. For scale, Ortho4XP took
250.7 s for a single ZL16 tile with its caches warm, which would make about 25 minutes for six
tiles built one after the other. Details: [batch-6-tiles-zl16.md](batch-6-tiles-zl16.md).

## Where the time goes

In these measurements Ortho4XP runs its steps one after the other, on 1.8 of the 14 cores on
average for a tile built again. It keeps its downloads (map data, elevation, images) and the
files of the last build, and you choose which steps to run again; a step run again is computed in
full. OrthoStudio XP runs the steps of all the tiles as a graph over every core, downloads over
HTTP/2 with up to 128 requests in flight, compresses textures in-process, and files every result
under what produced it: it finds by itself the steps whose inputs did not change, and does not
run them again.
