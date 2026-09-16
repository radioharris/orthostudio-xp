# Imagery grid: web-mercator tiles, textures and their names

Status: P1, written before `src/orthostudio/imagery/grid.py` (tests `tests/test_imagery_grid.py`).
Origin: Ortho4XP `src/O4_Geo_Utils.py:66-150` and `src/O4_File_Names.py:350-410`. Decision: **keep,
bit for bit**. The grid is the contract between the mesh (`.ter` files, ST coordinates), the masks
(`<y>_<x>.png`) and the textures; any drift would break the texture names that imported Ortho4XP
tiles carry.

## 1. The rule in plain language

X-Plane orthophoto textures are squares of 16 x 16 web-mercator tiles of 256 px at a zoom
level `zl` (4096 x 4096 px). A texture is identified by the Google/XYZ indices
`(til_x_left, til_y_top)` of its top-left tile, both multiples of 16. Tile indices grow
eastwards (x) and southwards (y), origin at the top-left of the web-mercator world
(lat 85.05°, lon -180°).

## 2. Ported functions

| OrthoStudio XP (`orthostudio.imagery.grid`) | Ortho4XP | Formula (kept verbatim) |
|---|---|---|
| `wgs84_to_tile(lat, lon, zl) -> (x, y)` floats | new (the continuous version of `wgs84_to_gtile`) | `x = (lon / 180 + 1) * 2 ** (zl - 1)`, `y = (1 - log(tan((90 + lat) * pi / 360)) / pi) * 2 ** (zl - 1)` |
| `wgs84_to_gtile(lat, lon, zl) -> (int, int)` | `O4_Geo_Utils.py:79-87` | pixel = `round((ratio + 1) * 2 ** (zl + 7))`, tile = `pixel // 256`. **Rounds to the nearest pixel first**: a point 0.4 px west of a tile edge falls in the eastern tile. Kept because it is what Ortho4XP requests; it is only used to pick the tile of a point, never to build textures. |
| `tile_to_wgs84(x, y, zl) -> (lat, lon)` | `gtile_to_wgs84`, `:66-77` | `lon = (x / 2 ** (zl - 1) - 1) * 180`, `lat = 360 / pi * atan(exp(pi * (1 - y / 2 ** (zl - 1)))) - 90`; top-left corner of the tile; accepts floats |
| `texture_at(lat, lon, zl, provider) -> TextureId` | `wgs84_to_orthogrid`, `:127-134` | `mult = 2 ** (zl - 5)`; `til_x = int((lon / 180 + 1) * mult) * 16`; `til_y = int((1 - log(tan((90 + lat) * pi / 360)) / pi) * mult) * 16`. **Truncation**, not rounding: the texture containing the point. `int()` truncates towards zero; for lat > 85.05° or `lon = -180` exactly the result can be `-0 * 16 = 0`, same as Ortho4XP. |
| `quadkey(x, y, zl)` | `gtile_to_quadkey`, `:109-124` | digit `a + 2 b` per level from the coarsest; `quadkey(0, 0, 0) == ""` |
| `webmercator_pixel_size(lat, zl)` | `:32-34` | `2 * pi * 6378137 * cos(pi * lat / 180) / 2 ** (zl + 8)` metres |
| `st_coord(lat, lon, til_x, til_y, zl)` | `:137-150` | `s = (lon / 180 + 1) * mult - til_x // 16`, `t = 1 - ((1 - ratio_y) * mult - til_y // 16)`, both clamped to [0, 1]. Documented and ported (one line each); P1 does not consume it (the DSF stage will). |

Derived helpers (no Ortho4XP counterpart, defined here):

* `texture_bbox(t) -> (lat_max, lon_min, lat_min, lon_max)`: corners
  `tile_to_wgs84(til_x, til_y)` and `tile_to_wgs84(til_x + 16, til_y + 16)`.
* `texture_tiles(t)`: the 256 `(x, y)` pairs of the texture, **row-major** (`y` outer,
  `x` inner), i.e. index `i = 16 * (y - til_y) + (x - til_x)`. This order is the layout of
  the chunk container (`imagery-chunks.md`) and the paste order of Ortho4XP's
  `build_texture_from_tilbox` (`O4_Imagery_Utils.py:1336-1350`, `monty` outer, `montx` inner).
* `textures_covering(lat0, lon0, lat1, lon1, zl, provider)`: the textures of the closed
  rectangle, computed like `build_texture_region` (`O4_Imagery_Utils.py:1933-1940`) and the
  mesh-time enumeration (`O4_DSF_Utils.py:176-180`): `texture_at(lat_max, lon_min)` to
  `texture_at(lat_min, lon_max)` **inclusive**, stepping 16, y outer then x. Both corners are
  included even when they lie exactly on a texture edge, which is why a 1° cell at ZL14 (whose
  south edge lat 43 falls inside row 6016) yields 4 x 5 = 20 textures.
* `parent_tile(x, y, levels)`: `(x >> levels, y >> levels)` for the parent fallback.

## 3. Texture naming (`O4_File_Names.py:350-410`)

* `texture_name(t) = f"{til_y}_{til_x}_{provider}{zl}"`: **y first**, then x, then the
  provider code immediately followed by the zoom level with no separator (`6016_8448_BI14`).
  Used for the JPEG cache (`.jpg`), the DDS (`.dds`), the `.ter` files and the mask crop.
  The `g2xpl_16` special case (`:355-364`) is dropped (that provider is not in the registry).
* `parse_texture_name(name)`: the inverse. Because the provider code may end with digits (`PDOK18`)
  and the ZL is glued to it, the split is ambiguous in theory; the rule applied is: take the last
  two digits as the ZL when they form a number in [10, 22], else the last digit (ZL < 10).
  `5952_8416_PDOK1814 -> (PDOK18, 14)`, `5952_8416_PDOK189 -> (PDOK18, 9)`,
  `5952_8416_BI9 -> (BI, 9)`. The only ambiguous case (a code ending in a digit used at a ZL below
  10, e.g. `PDOK1` at ZL 8 versus `PDOK` at ZL 18) does not occur: OrthoStudio XP never builds
  textures below ZL 10. The name's `til_x`/`til_y` must be non-negative multiples of 16 or
  `ValueError` is raised.
* Legacy JPEG cache directory (`jpeg_file_dir_from_attributes`, `:373-408`), layout
  `grouped` (default): `Orthophotos/<round10(lat)><round10(lon)>/<lat><lon>/<code>_<zl>/`
  with `{:+.0f}` zero-padded to 3 (lat) and 4 (lon) characters and `floor(x / 10) * 10` for
  the group: `Orthophotos/+40+000/+43+005/BI_14/6016_8448_BI14.jpg`. Layout `code`
  (Lux) uses `Orthophotos/<code>/<code>_<zl>/`, layout `normal` `Orthophotos/<lat><lon>/...`.
  Implemented by `orthostudio.imagery.chunks.legacy_jpeg_path` (read-only fallback).

## 4. Acceptance tests

| # | Test | Where |
|---|---|---|
| G1 | `texture_at` equals `GEO.wgs84_to_orthogrid` **bit for bit** on 10^6 pseudo-random points (seed 20260912: 800 000 uniform in lat (-85, 85) x lon (-180, 180), 200 000 on and next to degree and texture edges), ZL 10-19, the legacy function executed by the Ortho4XP virtualenv in a subprocess (`src` never imports legacy code). Same test for `wgs84_to_gtile`, `tile_to_wgs84` (float equality), `quadkey`, `webmercator_pixel_size`, `st_coord` on 100 000 points. | `test_imagery_oracle.py::test_grid_matches_legacy_geo_utils` (marker `oracle`) |
| G2 | `textures_covering(44, 5, 43, 6, 14, "BI")` is 20 textures (x 8416..8464, y 5952..6016) and the 17 DDS of the reference build are exactly that set minus `{6016_8416, 6016_8432, 6016_8464}`, the three textures of the south row (open sea south of Marseille, no land triangle, hence no `.ter` and no texture in the DSF). | `test_imagery_oracle.py::test_reference_tile_textures` |
| G3 | round trips: `tile_to_wgs84(wgs84_to_tile(p)) == p` to 1e-9; `texture_at` of any point of `texture_bbox(t)` interior is `t`; southern and western hemispheres; known quadkeys (`quadkey(3, 5, 3) == "213"`, Bing documentation) and the Marseille tile `033020133002333`-style values recorded in the audit. | `test_imagery_grid.py` |

## 5. Wanted differences from Ortho4XP

None in the numbers. Differences in shape only: functions take and return typed tuples
(`TextureId(til_x, til_y, zl, provider)`), no module globals, no pyproj dependency in this
module (Ortho4XP builds `Transformer`s at import time).
