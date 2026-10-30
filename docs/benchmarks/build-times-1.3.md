# Build times of 1.3: twelve tiles, against 1.2.1

What 1.3.0 changes for a batch of tiles: the same twelve tiles built by 1.2.1 and by 1.3.0, on a
large Mac and on a Windows PC of 16 GB, around a big city and along a coast.

- **Date**: the night of 29 to 30 October 2026, from 23:08.
- **Machines**: a MacBook Pro, Apple M4 Pro (10 performance and 4 efficiency cores), 48 GB of
  memory, macOS 26.6.2; a Windows 11 PC, 8 logical processors, 16 GB of memory. Both ran at the
  same time, sharing Bing and the map library.
- **Connection**: home connections, not measured that night.
- **OrthoStudio XP**: 1.2.1 (tag `v1.2.1`) and the code of 1.3.0 (commit `6e7811c5`), run from
  checkouts; on the PC with the Python of the installed app.
- **Tiles**: around Atlanta, the 4 by 3 block `+33-086` to `+35-083` (Atlanta, Chattanooga,
  Athens, the Smoky Mountains: dense and rural together); in Brittany, the 6 by 2 block `+47-005`
  to `+48+000`, eight of them along the sea.
- **Settings**: heavy ones, where 1.2.1 suffered most. Bing (BI) at ZL16, Copernicus relief,
  roads flattened down to the service roads (`road_level` 4) with their rounded ends, 2 000 000
  road points (`max_levelled_segs`), `curvature_tol` 1, `limit_tris` 5, `coast_curv_tol` 0.5,
  `apt_smoothing_pix` 32, airports at ZL18, *Memory for building* at its default, medium (1.2.1
  has no such setting). The map data from the library the app carries. On the Mac, X-Plane's
  overlays and rasters as usual; the PC has no X-Plane scenery installed, so both versions built
  there without them.
- **Starting state**: an empty data folder for each run: nothing cached, every image downloaded.
- **When it stops**: once the twelfth DSF is written, that is once every tile's terrain is done.
  The images started beside the terrain and go on after; the line, not the version, sets their
  pace. A run is also stopped when less than 6 % of the memory stays free for 30 s.
- **Order**: Atlanta with 1.2.1 first, Brittany with 1.2.1 last, so that the hour favours neither
  version; a minute between runs. One run a case: a gap under about 5 % is within what Bing and the
  map server vary from one run to the next.
- **Command**: the command line's own build, each step's start and end taken from the build's
  events, the memory of the build and every program it started read every 2 s (on the Mac their
  footprint, on the PC their private bytes):

```bash
export OSXP_HOME=/tmp/osxp-measure-1          # a folder of its own, never your real one
orthostudio build --tile +33-086 --tile +33-085 --tile +33-084 --tile +33-083 \
  --tile +34-086 --tile +34-085 --tile +34-084 --tile +34-083 \
  --tile +35-086 --tile +35-085 --tile +35-084 --tile +35-083 \
  --provider BI --zl 16 --relief copernicus --memory medium \
  --out "$OSXP_HOME/tiles" --quiet --json \
  --set road_level=4 --set max_levelled_segs=2000000 --set curvature_tol=1 --set limit_tris=5 \
  --set coast_curv_tol=0.5 --set apt_smoothing_pix=32 --set rounded_road_ends=True \
  --set osm_library=<address> --set osm_library_token=<key>
# 1.2.1: the same without --memory; on the PC: --no-xp12-rasters --no-overlay as well
```

## The Mac, 48 GB

Minutes from the build's start:

| | Atlanta, 1.2.1 | Atlanta, 1.3.0 | Brittany, 1.2.1 | Brittany, 1.3.0 |
|---|---:|---:|---:|---:|
| First mesh started | 32.1 | 1.7 | 6.0 | 0.3 |
| First images started | 35.6 | 5.8 | 7.9 | 1.5 |
| Last tracing done | 37.4 | 8.2 | 6.9 | 3.4 |
| **Last DSF done: the terrain of the twelve tiles** | **38.0** | **8.8** | **8.3** | **3.8** |
| Tiles whose images had started by then | 3 | 6 | 2 | 6 |
| Memory of the build at its highest | 41.6 GB | 19.4 GB | 36.9 GB | 17.4 GB |
| Memory left free at the lowest | 69 % | 86 % | 87 % | 87 % |

1.2.1 traced every tile inside the app itself, 5 to 15 minutes a tracing around Atlanta, and
meshed none before the last was traced. 1.3.0 traces each tile in a program of its own, a big
city's roads in pieces on several cores, and meshes each tile as soon as it is traced.

## The Windows PC, 16 GB

Minutes from the build's start:

| | Atlanta, 1.2.1 | Atlanta, 1.3.0 | Brittany, 1.2.1 | Brittany, 1.3.0 |
|---|---:|---:|---:|---:|
| First mesh started | | 11.5 | 18.5 | 0.8 |
| First images started | | 20.8 | 25.3 | 4.0 |
| Last tracing done | | 28.4 | 22.9 | 9.8 |
| **Last DSF done: the terrain of the twelve tiles** | **out of memory at 4.7** | **31.6** | **25.7** | **11.6** |
| Tiles whose images had started by then | | 9 | 2 | 4 |
| Memory of the build at its highest | 16.4 GB, the app alone | 14.4 GB | 9.1 GB | 13.0 GB |
| Memory left free at the lowest | 2 % | 18 % | 37 % | 38 % |

Around Atlanta, 1.2.1 started three tracings together inside the app, which grew from 3.6 GB to
15.0 GB in two and a half minutes, before any image: the run was stopped at 4.7 min with 2 % of
the memory free. Beside them, one tile's map data had failed from the library and was being
fetched from the public servers. 1.3.0 starts a tracing only when the memory its roads take is
free, and built the twelve tiles. In Brittany, 1.3.0 holds more memory at once than 1.2.1, since
it runs more steps side by side, within its *Memory for building*.

## At the default settings

A smaller gain where the roads are fewer: the same PC, the twelve tiles around Atlanta, every
setting at its default (the main roads, 200 000 road points), EOX at ZL14 and Copernicus, on
29 October 2026 from 10:06. This pass ran with the code of 1.3.0 as it stood that morning
(commit `a473d4b9`), before its last four changes to the build (the mesh's memory, EOX's 50
images a second, the airports of EOX tiles and Coast on a lane of its own), and was not run again
since.

| Minutes from the build's start | 1.2.1 | 1.3.0, 29 October |
|---|---:|---:|
| First images started | 7.1 | 2.4 |
| Last tracing done | 4.4 | 3.3 |
| **Last DSF done** | **7.2** | **4.5** |

The water masks reserved 5.1 GB each in 1.2.1, one tile at a time; 0.3 GB in 1.3.0 where there
is no water to draw, 0.8 to 2.3 GB around lakes.
