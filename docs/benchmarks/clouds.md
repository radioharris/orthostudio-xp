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
| **Fine search**: 47 clouds; the two readings, then the other sources fetched and judged (Esri, Esri Clarity, EOX, USGS) | about a minute |
| Two ticks (Esri over a glacier, 11 km²; EOX over a valley, 9 km²): their images got | both in within 10 s of the first tick |
| **Apply** with these two clouds: three textures laid | 1.8 s; the 21 others untouched; in the three, the same size and header but the stamp, 0.4 to 3.3 % of the first level's blocks made again; the textures as built kept beside, the same files (no space taken) |
| A build again from the Library, the clouds held | the folder found whole, nothing written: the textures the same bytes |
| One laid texture put back as built by hand, as a build places it; a build again | the cloud reaching it laid again by the build's call: every texture the same bytes as Apply laid them |
| **Restore** | 0.5 s; the 24 textures the build's, the same files as before Apply |

## B5. After against the texture as laid (Mac, 2026-11-06)

`tools/clouds/after_vs_built.py`: After as the map shows it while Apply is to lay the cloud
(Before with its preview over it) against the tile drawn from its textures as laid, what X-Plane
shows, at the map's level (12), the mean absolute difference per channel (0 to 255), the clouds
of B4:

| Cloud | Inside | Soft edge | Outside | What the replacement changed inside |
|---|---|---|---|---|
| Esri over a glacier, 11 km² | 4.3, 4.1, 4.7 | 3.6, 3.4, 3.8 | 0.08, 0.08, 0.08 | 30.4, 30.7, 35.9 |
| EOX over a valley, 9 km² | 2.6, 2.2, 2.7 | 3.4, 2.9, 3.2 | 0.12, 0.10, 0.12 | 74.6, 67.0, 68.8 |

What is left inside is the textures' BC1 compression on a contrasted ground (snow and rock); the
means of the two images agree within a level. Inside, the figures are those the test versions
gave when the build laid the clouds. On the soft edge and just outside, the levels of the
blocks a cloud reaches are now made from the texture's first level as built, itself compressed,
rather than from the photo before compression: a tenth of a level more.

## B6. Faster once the mode works (Mac, 2026-11-07)

Measured on the owner's -02-049 (Bing ZL16, 198 clouds) copied into a test home, and on +56-131:

| What | Before | After |
|---|---|---|
| Opening the mode, the tile's picture already drawn | 3.75 s | 0.07 s (the picture drawn only when its file is missing, `picture_file`) |
| The sources judged under every cloud of -02-049 (594 cloud and source pairs) | 94 s, a window at a time | 3.4 s, the windows grouped to the detector's patch (`classify_many`); 18 answers of 594 and 4 best sources of 198 differ, borderline veils, the fits never |
| Laying ten clouds over seven textures | 4.1 s | 0.7 s (four textures at once, `LAYING_AT_ONCE`, and a BC1 level decoded a palette colour at a time as one word: the same pixels) |

## B7. The memory a laying takes (Mac, 2026-11-07)

47 clouds over 18 textures of 4096 px on +56-131, each run in a process of its own, the most the
process held above what it held before (`ru_maxrss`), the tile put back as built after each; the
bytes laid the same in every run:

| Textures at once | Time | Memory, a level decoded whole | Memory, a level decoded by bands (`DECODED_AT_ONCE`) |
|---|---|---|---|
| 1 | 6.2 s | +0.78 GB | +0.48 GB |
| 2 | 3.3 s | +1.37 GB | +0.75 GB |
| 4 | 1.9 s | +2.55 GB | +1.35 GB |

A level of 4096 px decoded whole took some 350 MB meanwhile for its 64 MB of pixels, 90 MB by
bands, in the same time.

## B8. The mode never stands still for a picture (Windows and Mac, 2026-11-07)

+52+004 (Bing ZL14, a DSF of 314 MB, 3 M triangles), built by the installed 1.4.0rc8 on a Windows
server (AMD EPYC, 8 threads), the same gestures on rc8 as released and with the change, the app
started again before each; and +56-131 on the Mac (43 MB DSF). A picture of the tile at the map's
level, ZL12, 14 Mpx:

| What | Before | After |
|---|---|---|
| A picture drawn, the DSF already read (Windows) | 10.3 s, 9.5 s of it filling the triangles | 1.0 s (where each texture shows kept with the triangles, `picture._layout`: the same pixels) |
| The same on the Mac, +56-131 | 1.28 s | 0.34 s |
| A picture drawn, the DSF not read yet (Windows) | 27 s, 16.6 s of it reading the DSF | the same, drawn aside |
| Opening the mode with no picture, then a quick search (Windows) | first answer 27.3 s, the page empty and still; search done at 46 s | every answer at once but three of 2.6 to 4.2 s while the DSF is read; the picture at 27 s; search done at 46 s |
| Apply of one cloud, the answers meanwhile (Windows) | one answer of 10.1 s, the new After 11.9 s after the click | none over 73 ms, the new After 3.2 s after the click, **Drawing the tile…** for 1.2 s of it |

The answers still slow while a DSF is read share the processor with that reading (Python runs one
thread at a time): once per tile after each start, the first search or picture reading it.

## B9. A tile of 200 clouds (Mac, 2026-11-07)

The owner's -02-050 (Bing ZL16, 142 textures): a quick search found 645 clouds and kept the 200
largest, all ticked and applied, one of 1 670 km² over 49 textures; its list 4.9 MB, 32 000
points of outlines, 124 sidecars. Measured on a copy (the tile's files linked, its list and
pictures copied), in a process of its own, then through a server of its own:

| What | Before | After |
|---|---|---|
| A reading of the mode (`GET /api/clouds/{tile}`) | 0.62 s (1.0 to 3.4 s in the owner's log, the page reading meanwhile) | 0.095 s, 0.10 s through the server |
| A tick or an untick | 1.28 s (1.4 to 1.8 s in the owner's log) | 0.31 s |

Where it went: each reading checked again the 124 sidecars and the list, every number of every
outline (5 million calls a reading) and each sidecar's digest (0.78 s of 1.13 under a profiler),
and the plan wrote the big cloud's outline once per texture it reaches to digest each texture's
clouds (0.17 s). Now a file is checked once while it stays the same (`tile.read_checked`), and a
plan writes each cloud once (`clouds_digest(..., said)`): the same digests. What a tick still
takes is mostly writing the list of 4.9 MB and reading it back.

## B10. Getting the images of a cloud of 1 670 km² (Mac, 2026-11-07)

Cloud 1 of the owner's -02-050: 1 670 km² over 49 textures of 4096 px, EOX,
its 1 326 pieces already on the disk. In the owner's app it took about 48 s (18 s making its 49
parts, 23 s judging and its preview). Measured on a copy, getting its images (`find.check`) alone,
in a process of its own, the most it held (`ru_maxrss`, with the tile's picture):

| What | Time | Memory |
|---|---|---|
| Before: each layer's colours in linear light computed for each pixel, one texture at a time | 42.0 s | 3.87 GB |
| Read in a table of the 256 values (`Layer.linear`), one at a time | 27.6 s | 3.52 GB |
| The same, two at a time | 17.5 s | 4.21 GB |
| The same, four at a time (`LAYING_AT_ONCE`) | 12.7 s | 4.91 GB |

Its judgement, its entry and its preview are the very same, byte for byte, in all of them, three
runs of four at a time included. Where the 42 s went: the layer of each texture reduced to the
map's level twice (once to judge the source, once for the preview, in the colours matched then),
31.6 s of them, mostly raising every pixel's colours to the power 2.2 and averaging them; the
layers made 5.4 s, the detector 3.6 s. Cloud 2 (244 km², 12 textures): 2.7 s.

## B11. A tick near clouds applied (Mac, 2026-11-07)

A copy of the owner's -02-050 (142 textures, its picture 5 827 px a side), clouds applied then
the tile built again, the owner's gesture: a small cloud ticked on a texture already showing
clouds replaced. Each texture Apply makes again was drawn as built under the previews, so each
such tick drew the whole tile again: 0.80 to 0.92 s (water 0.32, the textures read 0.25, laid
0.21; its JPEG 0.07), then up to 0.5 s before the page read it, 1.4 to 2.1 s from the click to
the picture. Now a texture keeping the clouds it shows is drawn as X-Plane shows it, with the
previews of the clouds it gains (`FolderPlan.bare`): the same tick draws nothing, its preview
alone, 0.3 to 0.4 s from the click.

Both ways against what X-Plane shows after the real Apply, the difference per pixel (0 to 255,
the largest of the three channels) in the clouds' boxes; outside them both are exact:

| Case | Made again | Drawn as built, before / now | Mean, before / now | 99 %, before / now |
|---|---|---|---|---|
| Six new clouds, one applied unticked | 10 | 10 / 4 | 0.60 / 0.60 | 7 / 7 |
| Four small new clouds beside applied ones | 4 | 4 / 0 | 0.69 / 0.23 | 7 / 5 |

Closer now where clouds are kept: they show as laid, rather than by their previews.

## B12. From a click to the cloud done on screen (Mac, 2026-11-07)

The copy of -02-050 (200 clouds), a window of the app's kind (WebKit), small clouds ticked one at a
time whose images are made in about 0.3 s; the page's times against the engine's (when the cloud's
task ended), eight clicks each way, back to back:

| The tile read again after a click | From the click to the cloud done | After the engine ended |
|---|---|---|
| Every 0.5 s | 0.84 to 1.16 s | 0.32 to 0.65 s |
| Every 0.2 s for 3 s, then 0.5 s | 0.56 to 0.79 s | 0.11 to 0.32 s |

Each reading takes the engine about 0.05 s on that tile. The first tick right after the mode opens
waits for the tile's picture begun as it opened (1.8 s there), then its images.

