# Build times of 1.0: one tile, from nothing and again

What a user waits for with OrthoStudio XP 1.0: a first build of a tile at ZL16 and at ZL18, each
step on its own, and what building it again costs after a change, a stop or nothing at all.

- **Date**: 2026-10-10, 00:10 to 00:22.
- **Machine**: Apple M4 Pro (10 performance and 4 efficiency cores), 48 GB of memory, macOS
  26.6.2.
- **Connection**: a home connection, measured with macOS's `networkQuality -u` (download only)
  before and after each series: 445 and 453 Mbit/s (A), 447 and 418 (B), 444 and 485 (C), 453 and
  420 (D).
- **OrthoStudio XP**: the code of 1.0 (commit `5fba5cb6`), run from a checkout.
- **Tile**: `+46+006` (Geneva, Lake Geneva and the foot of the Alps), Bing (BI).
- **Settings**: a new user's, unchanged (relief from X-Plane 12, roads, forests and buildings
  from X-Plane, decals off, no zone), plus the address and key of the map library, which the
  installed app carries and a checkout does not. Nothing installed in X-Plane.
- **Starting state**: an empty data folder for each series (A, B, C, D below): nothing cached,
  every image downloaded.
- **Command**: `tools/bench/build_times.py`, which builds the tile through the app's own path
  (`make_specs` with the saved settings, then `build_tiles`) and takes each step's time from the
  build's report:

```bash
export OSXP_HOME=/tmp/osxp-measure-A          # a folder of its own, never your real one
python tools/bench/build_times.py --library-from ~/.orthostudio/config.toml
python tools/bench/build_times.py --label A1 --out A1.json                  # ZL16, nothing cached
python tools/bench/build_times.py --label A2 --out A2.json                  # nothing changed
python tools/bench/build_times.py --set essential.photo_look=softer --label A3 --out A3.json
# A5: a zones.json with the ZL18 zone below, put in $OSXP_HOME, then the same command
python tools/bench/build_times.py --set essential.relief.source=copernicus --label A6 --out A6.json
python tools/bench/build_times.py --set expert.use_decal_on_terrain=true --label A7 --out A7.json
python tools/bench/build_times.py --set expert.decal=grass_and_stony_dirt_1.dcl --label A8 --out A8.json
# series B in a new folder: --stop-at 0.5, then the same command again
# series C in a new folder: --zl 18, twice; series D in a new folder: A1 again
```

The ZL18 zone of A5 is the rectangle 6.090 to 6.130 E, 46.225 to 46.250 N (Geneva airport,
LSGG).

## First build, nothing cached

| | ZL16 (A1) | ZL16 again (D1) | ZL18 (C1) |
|---|---:|---:|---:|
| **Whole tile** | **53.0 s** | **55.0 s** | **387.4 s (6 min 27 s)** |
| Textures (DDS, 4096 × 4096) | 213 | 213 | 2,902 |
| Images downloaded | 733 MB | 733 MB | 8,464 MB |
| Imagery step | 33.9 s, 21.7 MB/s | 35.8 s, 20.5 MB/s | 364.2 s, 23.2 MB/s |
| Data folder after the build | 3.8 GB | 3.8 GB | 41 GB |
| of which images / cache | 0.73 / 3.1 GB | 0.73 / 3.1 GB | 8.5 / 33.5 GB |
| DSF | 37 MB | 37 MB | 38 MB |

The tile's folder holds the same files as the cache (hard links), so it adds nothing to these
sizes. The imagery step's rate counts the images downloaded over the step, compression included.

Each step of the tile, in seconds; they run side by side where nothing holds them back, and
*from* is when the step started:

| Step | ZL16: time | from | ZL18: time | from |
|---|---:|---:|---:|---:|
| Map data (from the library) | 2.7 | 0.0 | 2.9 | 0.0 |
| Relief (X-Plane 12's own) | 4.5 | 0.0 | 4.8 | 0.0 |
| X-Plane's sea level and depth rasters | 0.4 | 0.0 | 0.4 | 0.0 |
| Overlays (roads, forests, buildings) | 3.2 | 0.0 | 3.4 | 0.0 |
| Coastline | 0.0 | 2.7 | 0.0 | 2.9 |
| Roads, water and airports traced | 7.0 | 4.6 | 7.4 | 4.8 |
| Mesh | 3.5 | 11.6 | 3.7 | 12.3 |
| Water masks | 0.9 | 15.1 | 0.9 | 16.0 |
| DSF | 2.4 | 16.0 | 3.0 | 16.9 |
| Imagery: downloaded, assembled, compressed | 33.9 | 18.8 | 364.2 | 20.3 |
| Tile folder | 0.2 | 52.7 | 2.8 | 384.5 |

At ZL18 the imagery took 10.7 times as long as at ZL16, for 13.6 times the textures and 11.5
times the images downloaded.

## The same tile afterwards

| | Time | Downloaded | Steps built again |
|---|---:|---:|---|
| Built again, nothing changed (A2; C2 at ZL18) | under 0.1 s (ZL18: 0.1 s) | nothing | none |
| Photo colours toned down (A3) | 13.9 s | nothing | imagery (compressed again from the kept images), tile folder |
| A ZL18 zone around the airport (A5) | 6.6 s | 19.7 MB | DSF, imagery (6 textures more), tile folder |
| The relief switched to Copernicus (A6) | 39.7 s | the relief file, no image | relief 14.9 s, tracing 8.9 s, mesh 5.9 s, masks 0.9 s, DSF 4.6 s, imagery 3.8 s, tile folder |
| Decals turned on (A7) | 0.2 s | nothing | tile folder (terrain files) |
| Another decal (A8) | 0.1 s | nothing | tile folder (terrain files) |

## Stopped halfway, then started again

Series B, from an empty data folder: the build was stopped once half the imagery was done, as
*Stop* does, then started again.

| | Time | Downloaded |
|---|---:|---:|
| First run, stopped (B1) | 37.8 s | 360.2 MB |
| Started again, to the end (B2) | 17.6 s | 372.8 MB |
| **Together** | **55.4 s** | **733.0 MB** |

Together they downloaded what a build that was not stopped downloads (A1, 733.1 MB), and B2
built only the imagery and the tile folder.
