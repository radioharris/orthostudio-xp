# Checks before a release

What automatic tests cannot see: an installer on a machine that never had OrthoStudio XP, and the
tiles in X-Plane, in flight. Since decision 0010 no test compares OrthoStudio XP's tiles with
Ortho4XP's any more: flights are the only check of what a pilot sees. Tick each box, note the
tile and what you saw, and keep screenshots of anything wrong.

## 1. Install, on each system

- [ ] **Windows 10 or 11 PC with X-Plane 12** (the systems most pilots fly on).
- [ ] **Mac** (Apple Silicon, macOS 14 or later), from the `.dmg`.
- [ ] **Linux** (optional for a beta: announced as experimental).

On each one:

- [ ] The installer runs; the first-launch warning (unsigned app) is the one the README describes.
- [ ] Opening the app opens the page; the status bar finds X-Plane 12 and its folder is right.
- [ ] The doctor (status bar) shows no failed check.
- [ ] Installing the same version again over the running app works (Windows: the installer stops
      it; macOS: quit it first).
- [ ] *Quit* stops it; opening the app again brings the page back.

## 2. Build a varied set of tiles

At the default settings (*Recommended*), one tile of each kind, each with its main airport sharper:

| Case | Example tile | Why |
|---|---|---|
| Sea coast | `+43+005` (Marseille) | photo over water, coastline, water transition |
| Big lake | `+46+006` (Geneva) | lake shores and transparency |
| High mountains | `+46+007` or `+45+006` (Alps) | relief under the photo, steep slopes |
| Big airport | `+49+002` (Paris CDG) | flat runways, alignment, the ZL18 zone |
| Another continent | a tile of your choice outside Europe | provider coverage, other latitudes |

For each build:

- [ ] The estimate (step 3) is close to what the build took (disk, time).
- [ ] No error card in Works; if there is one, its words say what to do.
- [ ] The Library lists the tile, "in X-Plane" is yes, the folder button opens it.

## 3. In flight (X-Plane closed during the builds, started after)

Fly over each tile low (1,000 to 3,000 ft) and at cruise (FL300 or more), with inside and outside
views, zoomed in and out.

- [ ] **Photo and relief agree**: ridges, valleys and lakes of the photo sit on the relief; no
      stretched texture on slopes; no flat tile.
- [ ] **Coast and water**: no photo of sea over land nor of land over water; the transition to
      X-Plane's water looks natural; waves and reflections where expected.
- [ ] **Lakes and rivers**: shores fit; the lakes look as the Settings answer says.
- [ ] **Airports**: runways and taxiways where the photo shows them, flat, no step at their edges;
      the airport zone is sharper; X-Plane's airport sits on it.
- [ ] **Tile edges**: fly along a tile boundary: no gap, seam, colour jump or relief step.
- [ ] **Roads, forests, buildings** (X-Plane's overlays): present, not on water nor on runways.
- [ ] **Night and dusk**: lights along the roads, no bright tile edges.
- [ ] **Performance**: frame rate close to X-Plane's own scenery; no long stutter when a new tile
      loads; video memory not exhausted.
- [ ] **Removing** the tiles from X-Plane (Library, X-Plane closed) gives back X-Plane's own scenery
      at the next start.

## 4. What to write down for each defect

The tile, the position (latitude and longitude, or the nearest airport and a heading), the altitude
and view, a screenshot, the settings (preset or answers changed), and `serve.log` if a build
went wrong (its folder is in the release notes).
