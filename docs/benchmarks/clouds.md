# Clouds: what the search and laying measured

The figures behind the rules of `docs/specs/clouds.md`, which cites them by their number. The Mac
is the development machine (Apple silicon), the Shadow a Windows cloud computer. B1 to B3 were
measured on the 1.4.0 test versions (rc), whose detector, search and outline this version keeps;
B4 and B5 on this version, the clouds laid in the finished tile's folder.

## B1. The quick and the finer readings (2026-11-04)

The same built textures read once at about 15 to 20 m a pixel, and a second time a level finer,
the two merged:

| Tile | Machine | Once: clouds, time | The finer alone | Both merged: clouds, time |
|---|---|---|---|---|
| +24-102 (Mexico) | Mac | 21, 50.8 s | 62, three of 32 to 53 ha the first missed | 55, 201.4 s |
| +56-131 (Alaska) | Mac | 19, 25.5 s | 56, a cluster of cumulus whole where the first took a corner | 42, 83.5 s |
| +52+004 (Netherlands) | Shadow | 11, 32.9 s | 4, the veil of 1 395 ha over the Wadden Sea mostly lost | 16, 147.6 s, the veil kept |

The finer reading alone finds the small cumulus and loses the large pale veils, hence both. Its
second reading with the detector's first network alone instead of both (Mac): -02-049 (Brazil)
215.7 s to 130.8 s, 169 of its 200 clouds where they were; +24-102 197.4 s to 119.8 s, the three
known cumulus found. The first reading with one network was rejected: +24-102 gave 37 clouds
instead of 21, 18 % less surface.

## B2. The detector

- OpenCV gives PyTorch's class on 99.995 % of the pixels of a random patch
  (`tools/clouds/export_detector.py`).
- Downloaded where the environment has no OpenCV (Mac, 2026-11-03): 48 MB from PyPI in 3.3 s.
- Memory, +24-102 at the finer level (12 805 x 11 652 pixels): the answers kept one row of
  patches at a time, the search's peak 2.8 GB reading once, 3.6 GB with the finer reading. One
  search at a time for the app (`clouds.md` R7).

## B3. Outlines that touch themselves (Mac, 2026-11-05)

Twelve synthetic fields of 2000 x 2000 pixels (random noise, seed 1, blurred over 6 to 14
pixels; thick cloud above 2.0 standard deviations, thin between 1.6 and 2.0, the shadow the same
field moved 25 pixels), found as the search finds clouds: of 2 385 clouds, 75 had an outline that
touches itself (3 %). Their convex hull was 1.52 times the cloud's area in the median, 2.44 at
most; the outline made one piece (`find._outline`) 1.01 and 1.10, every one simple.

## B4. The real path on +56-131 (Mac, 2026-11-06)

A clone of a test home, the tile Bing ZL14 (24 textures of 4096 px, BC1), the engine
`osxp serve` and the page in the app's browser; Apply's and Restore's times from the click on the
question's button to the end of the task, read by the page every 0.1 s:

| Step | Time, what changed |
|---|---|
| The tile built again from the Library, everything in the cache | 3 s; the three textures the test versions had laid clouds in came back identical, byte for byte, to the originals those versions kept |
| **Find clouds**: 47 clouds; the two readings, then the other sources fetched and judged (Esri, Esri Clarity, EOX, USGS) | about a minute |
| Two ticks (Esri over a glacier, 11 km²; EOX over a valley, 9 km²): their images got | both in within 10 s of the first tick |
| **Apply** with these two clouds: three textures laid | 1.8 s; the 21 others untouched; in the three, the same size and header but the stamp, 0.4 to 3.3 % of the first level's blocks made again; the textures as built kept beside, the same files (no space taken) |
| A build again from the Library, the clouds held | the folder found whole, nothing written: the textures the same bytes |
| One laid texture put back as built by hand, as a build places it; a build again | the cloud reaching it laid again by the build's call: every texture the same bytes as Apply laid them |
| **Restore** | 0.5 s; the 24 textures the build's, the same files as before Apply |

## B5. After against the texture as laid (Mac, 2026-11-06)

`tools/clouds/after_vs_built.py`: After as the map shows it (Before with the cloud's preview over
it) against the tile drawn from its textures as laid, what X-Plane shows, at the map's level (12),
the mean absolute difference per channel (0 to 255), the clouds of B4:

| Cloud | Inside | Soft edge | Outside | What the replacement changed inside |
|---|---|---|---|---|
| Esri over a glacier, 11 km² | 4.3, 4.1, 4.7 | 3.6, 3.4, 3.8 | 0.08, 0.08, 0.08 | 30.4, 30.7, 35.9 |
| EOX over a valley, 9 km² | 2.6, 2.2, 2.7 | 3.4, 2.9, 3.2 | 0.12, 0.10, 0.12 | 74.6, 67.0, 68.8 |

What is left inside is the textures' BC1 compression on a contrasted ground (snow and rock); the
means of the two images agree within a level. Inside, the figures are those the test versions
gave when the build laid the clouds. On the soft edge and just outside, the levels of the
blocks a cloud reaches are now made from the texture's first level as built, itself compressed,
rather than from the photo before compression: a tenth of a level more.
