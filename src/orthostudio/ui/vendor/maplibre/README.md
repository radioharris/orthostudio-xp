# MapLibre GL JS 5.24.0 and its Leaflet bridge (vendored)

The street map of the page draws vector tiles, which Leaflet alone cannot: OpenFreeMap serves
OpenStreetMap as vector tiles only (`api/basemap.py`). MapLibre GL renders them, inside the
Leaflet map, through the bridge.

Sources, from the npm registry, verified against the published integrity (2026-09-19):

| File | Package | Integrity |
|---|---|---|
| `maplibre-gl.js`, `maplibre-gl.css` | `maplibre-gl@5.24.0` | `sha512-ALyFxgtd5R+65UqZ/++lOqwWcC0SNho9c27fYSyLmG7AfnAul2o46F05aDJGPbFU57wos9dgcIySHs0Xe6ia3A==` |
| `leaflet-maplibre-gl.js` | `@maplibre/maplibre-gl-leaflet@0.1.4` | `sha512-k57wcndZIvq3m08g2je4pjUPGr43ffNW3j+gXbymt8Vi2l1lUERUi1UmdTZcTPN8LuDDYOMWRNBaSLwpAu02ew==` |

Copied unchanged from `package/dist/` (MapLibre) and `package/` (the bridge). Licences beside
them: `LICENSE.txt` (BSD-3-Clause, MapLibre contributors) and `LICENSE-leaflet-bridge` (ISC),
both compatible with OrthoStudio XP's GPL v3.

Why the 5 series rather than 6: MapLibre 6 ships ES modules only, and the bridge loads the two
libraries from the globals `L` and `maplibregl`, as the page does with Leaflet. The 5 series has
the classic build that fits a page with no build step (`docs/specs/ui.md` section 1).

Why vendored: the page never contacts another origin. To upgrade, download the tarball, check its
integrity against `https://registry.npmjs.org/maplibre-gl/<version>`, replace the files and this
note.
