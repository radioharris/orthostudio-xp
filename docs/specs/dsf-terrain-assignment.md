# DSF: which texture and terrain every triangle gets

Status: P2, written before the code of `src/orthostudio/dsf/` (modules `zones.py`, `recut.py`,
`bathy.py`, the terrain registry in `encode.py`). Tests: `tests/test_dsf_reference.py` (recut, zone
map and terrains against transcriptions of Ortho4XP on synthetic meshes),
`tests/test_dsf_encode.py`; until decision 0010, `tests/test_dsf_oracle.py` (17 textures / 39 `.ter`
at ZL14, 179 / 356 at ZL16, `DEFN/TERT` order). Companion specs: `dsf-encoding.md` (pools, commands,
atoms) and `dsf-xp12-rasters.md`.

Origin in Ortho4XP: `src/O4_DSF_Utils.py:110-257` (`zone_list_to_ortho_dico`),
`:464-489` (mesh read, type remap, recut, bathymetry), `:640-1042` (the two triangle loops
that create terrains and `.ter` files), `src/O4_Bathymetry.py` (whole file),
`src/O4_Mask_Utils.py:38-60` (`needs_mask`), `src/O4_Geo_Utils.py:127-150`.

## 1. The rule in plain language

The mesh of a tile (`Data<tile>.mesh`) is a list of vertices (lon, lat, altitude, two normal
components) and of triangles carrying an attribute. The DSF stage decides, for every
triangle, (a) whether it is land, inland water or sea, (b) which orthophoto texture covers
its barycentre (zoom level and provider can vary inside the tile through `zone_list` and the
airport upgrade), (c) which X-Plane **terrain** it is drawn with: a `.ter` file named after
the texture and the kind of triangle, or the built-in `terrain_Water`. Sea triangles are
also drawn a second time with `terrain_Water` when their `.ter` is an overlay. Before any of
this, water triangles touching the coast are re-cut so that no water triangle has all three
corners on the coastline (X-Plane 12 water rendering, `O4_Bathymetry.py`).

## 2. Inputs

| Input | Source | Read by |
|---|---|---|
| `Data<tile>.mesh` | `O4_Mesh_Utils.py:326-372` (`write_mesh_file`) | `orthostudio.mesh.mesh_file.read_mesh` |
| masks `Masks/<10x10>/<tile>/<y>_<x>.png` at `mask_zl` | `O4_Mask_Utils.py` | `masks: Callable[[int, int], Path | None]` (same abstraction as `orthostudio.textures.imprint.mask_for_texture`) |
| distance masks `<y>_<x>_dist.png` (only when `distance_masks_too`) | `O4_Mask_Utils.py`, name `O4_File_Names.py:337-338` | optional `distance_masks` callable of the same shape |
| airports (only when `cover_airports_with_highres` in `True`/`ICAO`) | Ortho4XP: the `Data<tile>.apt` pickle of step 1 (`O4_DSF_Utils.py:116-122`); OrthoStudio XP: the airport record of the vector stage (`airports-integration.md` 4) | `airports: Sequence[AirportCover]` = `(xmin, ymin, xmax, ymax, is_icao)` in tile-relative degrees, produced by `zones.airport_covers(vectors_dir)` |
| existing textures (only when `cover_airports_with_highres == "Existing"`) | `textures/*.dds` of the existing build (`:231-257`) | `existing_textures: Iterable[TextureId]` |
| `DsfParams` | tile `.cfg` | contract of P2a (`params.py`) |

### 2.1 The `.mesh` file (`O4_Mesh_Utils.py:326-372` and `:847-894`)

```
MeshVersionFormatted 2
Dimension 3
<blank>
Vertices
<N>
lon lat z/100000 0        x N   (%.15f each, lon and lat absolute)
<blank>
Normals
<N>
u v 0                     x N   (%.2f each)
<blank>
Triangles
<M>
n1 n2 n3 attr             x M   (1-based vertex numbers, attr integer)
```

`read_mesh_file` (`:847-894`) parses `float(x)` per token, multiplies the third column by
`100000` **after** parsing (`node_coords[2::5] *= 100000`, `:865`), and stores the triangle
attribute as read (`int(x) - 1` then `+ 1`, `:888-891`). `MeshData` (contract,
`orthostudio.mesh.mesh_file`) holds `vertices (N, 3) float64 = (lon, lat, z * 100000)`, `normals
(N, 2)` float32, `tris (M, 3) int32` 0-based, `tri_attr (M,) uint8`. The private fallback
reader parses with `numpy.fromstring(sep=" ")`, verified bit-identical to `float()` on the
reference file (603 649 vertices), multiplies by `100000` in float64 exactly as Ortho4XP and
keeps float64 normals; the encoder recovers Ortho4XP's float64 normals from float32 ones
(`dsf-encoding.md` section 2).

## 3. Rules ported

### 3.1 Triangle type remap (`O4_DSF_Utils.py:475-480`)

`has_water = 7` when `mesh_version >= 1.3` else `3`. For every triangle,
`t = attr & has_water`; `t = 0` if `t == 0`, else `2` if `t > 1 or use_masks_for_inland`,
else `1`. So bit 1 = inland water, bits 2 and 4 = sea; `use_masks_for_inland` turns inland
water into sea (masked). Other attribute bits (8 airport, 16 road, 32 patch, 128 hangar)
are ignored here. **Keep.**

### 3.2 Recut of coastal water triangles (`O4_Bathymetry.py:16-183`)

Definitions, with `tri_types` in {0, 1, 2}:

- `node_types[n] |= 1 << tri_type` over the three corners of every triangle (`:27-32`);
- a **coast node** has the land bit and a water bit: `(node_types & 1) and (node_types & 6)`
  (`:33`);
- a **coast triangle** has at least one coast node (`:36-42`);
- for coast triangles only, `edge_type[(m, n)] = edge_type[(n, m)] = OR of 1 << tri_type`
  over the coast triangles containing the edge (`:45-57`);
- an edge is **cut** when it has no land bit and both ends are coast nodes (`:70-82`); the cut
  node is the midpoint `(coords[a] + coords[b]) / 2.0` of the five columns (lon, lat, z, u, v),
  `node_types = edge type`, not coast. Cut nodes are numbered from `N` in the order of first
  appearance of the edge in the coast-triangle scan (`edge_type.items()` iteration order,
  triangles in order, edges `(a,b)`, `(b,c)`, `(c,a)`; both directions are inserted at once so
  the undirected first appearance is the dict order).
- then every **water coast triangle** (`tri_types != 0 and tri_is_coast`, `:92-94`) is
  re-cut in triangle order (`:95-168`), with `C = (a,b) cut`, `A = (b,c) cut`, `B = (c,a) cut`:
  - no cut edge: if **every** edge has the land bit (`edge_type & 1` on all three), a barycentre
    node `(coords[a] + coords[b] + coords[c]) / 3.0` (`node_types` = the triangle *type* value 1 or
    2, not its bit — an Ortho4XP quirk kept: a sea barycentre reads as inland water for the
    distance-mask lookup of 3.3; not coast) is appended and the triangle becomes `(a, b, g)` in
    place plus `(b, c, g)`, `(c, a, g)` appended (`:103-121`); otherwise the triangle is left alone;
  - one cut `x` (the cut node), the ring `L = [a, C?, b, A?, c, B?] * 2` read from the last cut
    node: `(x, y, z, t) = L[s1:s1+4]` gives `(x, y, z)` in place and `(x, z, t)` appended
    (`:153-159`);
  - two cuts, read from the vertex opening the only uncut edge: `(x, y, z, t, u) = L[s2:s2+5]`
    gives `(x, y, z)` in place, `(z, t, u)` and `(x, z, u)` appended (`:145-152`);
  - three cuts: `(x, y, z, t, u, v) = L[s1:s1+6]` gives `(x, y, z)` in place, `(z, t, u)`,
    `(u, v, x)`, `(x, z, u)` appended (`:161-168`).
  Appended triangles keep the type of the original and are numbered after `M` in that order.

The recut changes triangle **order** (in-place replacement plus appended triangles) and adds
nodes; both orders matter for the byte-identity of the DSF (first-encounter order of pool
entries and of pools, `dsf-encoding.md`). **Keep, vectorised** (numpy), tested against a
line-by-line transcription of the Ortho4XP loops on random meshes.

### 3.3 Depth ratio per node (`O4_Bathymetry.py:187-223` and `:8-13`)

`node_bathy = 255` for every node; for the sea nodes (`node_types & 4`) whose texture at
`mask_zl` (`wgs84_to_orthogrid(lat, lon, mask_zl)`) has a distance mask
`<m_til_y>_<m_til_x>_dist.png`, `node_bathy = mask[int((1 - t) * 4095), int(s * 4095)]` with
`(s, t) = st_coord(lat, lon, m_til_x, m_til_y, mask_zl)`. Then, per node,
`ratio_bathy = 0` for a coast node, else `max(min(10 * ratio_bathy_param * node_bathy / 255,
1), 0.1)`; the DSF stores `int(65535 * ratio_bathy)` (truncation, `:815, 862, 1030`).
Without distance masks (the default, `distance_masks_too = False`) every non-coast water node
gets `65535` and every coast node `0`. **Keep** (`distance_masks` optional input; `None`
means no file exists, as in Ortho4XP when the files are absent).

### 3.4 Texture of a mesh cell (`O4_DSF_Utils.py:110-257`)

The tile is covered by the grid of textures at `mesh_zl` (default 19), from
`wgs84_to_orthogrid(lat + 1, lon, mesh_zl)` to `wgs84_to_orthogrid(lat, lon + 1, mesh_zl)`
inclusive, step 16 (`:176-181`, same inclusive enumeration as `imagery.grid.textures_covering`).
Every mesh cell `(til_x, til_y)` maps to `(til_x_text, til_y_text, zl, provider)`:

1. A 4096² priority image is drawn (`:113`, `:199-206`): the base zone (the whole tile,
   `default_zl`, `default_website`) with value 1, then **`zone_list` reversed** with values
   2, 3, ... (`[base] + zone_list[::-1]`, so the first line of `zone_list` is drawn last and
   wins). A zone is `([lat0, lon0, lat1, lon1, ...], zl, provider)`; its polygon in pixels is
   `(round((lon_i - lon) * 4095), round((lat + 1 - lat_i) * 4095))` (Python `round`, half to
   even) filled with Pillow `ImageDraw.polygon` (`:200-205`). Pillow's rasterisation is the
   rule; OrthoStudio XP calls the same function with the same integer polygon.
2. The pixel of a cell is the clamped centre of the cell: `(latp, lonp) =
   gtile_to_wgs84(til_x + 8, til_y + 8, mesh_zl)` clamped to the tile, `x = round((lonp - lon)
   * 4095)`, `y = round((lat + 1 - latp) * 4095)` (`:183-191`).
3. Airports (`cover_airports_with_highres` in `("True", "ICAO")`, `:114-173`): for each
   airport (all of them, or only `key_type == "icao"`), the bounds of its boundary
   (tile-relative degrees) are extended by `1000 * cover_extent * m_to_lon(lat)` in longitude
   and `1000 * cover_extent * m_to_lat` in latitude (`m_to_lat = 180 / (pi * 6378137)`,
   `m_to_lon(lat) = m_to_lat / cos(pi * lat / 180)` with **the tile's integer latitude**),
   snapped outwards to the texture grid at `cover_zl` (`wgs84_to_orthogrid` of the north-west
   corner, `gtile_to_wgs84` of `+16` for the south-east corner, `:147-164`), clamped to
   `[0, 1]`, and the rectangle `[round((1 - ymax) * 4095) : round((1 - ymin) * 4095) + 1,
   round(xmin * 4095) : round(xmax * 4095) + 1]` of a boolean 4096² array is set. A cell whose
   pixel is set gets `zl = max(zl, cover_zl)` (`:217-218`).
4. The texture of the cell at its `zl` is `16 * (int(til / 2 ** (mesh_zl - zl)) // 16)` for
   both indices (`:219-224`).
5. `"Existing"` (`:231-257`): every `textures/*.dds` of the existing build (name
   `<y>_<x>_<provider><zl>.dds`, `zl` = the two characters before `.dds`) claims all the mesh
   cells it covers unless the cell already has a **strictly** higher `zl`.

OrthoStudio XP keeps steps 1-5 (`zones.texture_map`), airports given as bounds by the caller (read
from the vector stage's record by `zones.airport_covers`, never from a pickle), and evaluates the lookup
for every triangle with numpy: `wgs84_to_orthogrid` of the barycentre
`((lon1 + lon2 + lon3) / 3, (lat1 + lat2 + lat3) / 3)` at `mesh_zl` (`:653-665`, float64, same order
of operations), then an index into the cell grid. A barycentre outside the enumerated grid is
impossible for a mesh clamped to the tile (`Triangle4XP` clamps `x, y` to `[0, 1]`); it raises
`SYS_INTERNAL_ERROR` (message "mesh outside tile") rather than a `KeyError`; a dedicated
`DSF_MESH_OUTSIDE_TILE` code is proposed to the integrator (`errors.py` is outside this
work package).

### 3.5 Terrains, `.ter` files and overlays (`O4_DSF_Utils.py:640-1042`)

Two passes over the triangles, in triangle order: first the sea triangles (`tri_type == 2`,
`:641-872`), then the others (`:875-1040`). A terrain is keyed by
`(texture_attributes, tri_type)`; the registry starts with `terrain_Water` at index 0
(`:627-628`) and indices grow in creation order, which is the order of the `DEFN/TERT`
names (`terrain/<name>.ter`, `:766, 941`).

- Sea triangle: the texture is checked once per `(texture, 2)` with `needs_mask`
  (`O4_Mask_Utils.py:38-60`, ported as `orthostudio.textures.imprint.needs_mask_for_texture`: the
  raw crop of the `mask_zl` mask covering the texture must exceed 30 somewhere; no mask file or
  `zl < mask_zl` means no). No mask -> the triangle goes to `terrain_Water` (`terrain_idx = 0`,
  `:768`) and the `(texture, 2)` pair is remembered as skipped. Mask -> a new terrain with
  `is_overlay = (water_tech == "XP11 + bathy") or not imprint_masks_to_dds` (`:704-706`), kind
  `SEA_OVERLAY` or `SEA` (`textures-ter.md`), and the texture is queued for download (`TextureJob`).
  An overlay sea triangle is **also** drawn with `terrain_Water` (`:840-872`).
- Land or inland water triangle: a new terrain on first sight, `is_overlay = tri_type == 1`
  (`:910`), kind `LAND` or `WATER_OVERLAY`; inland water is also drawn with `terrain_Water`
  (`:1008-1040`).
- The `.ter` text is `orthostudio.textures.ter.ter_text` with `TerParams` taken from `DsfParams`
  (`create_terrain_file`, `:261-357`, already ported byte for byte).

Outputs of this spec: the terrain registry (index -> texture, kind), `ter_files: dict[name ->
text]`, `textures: list[TextureJob(texture, kinds: frozenset[TerKind])]` in first-creation
order of the texture. Acceptance: the reference ZL14 build gives 17 textures and 39 `.ter`
(17 land, 15 `_water_overlay`, 7 `_sea_overlay`) with the exact texts of
`fixtures/large/oracle/+43+005_zl14_BI/build/terrain/`; ZL16 gives 179 and 356; the
`DEFN/TERT` order equals the reference DSF's.

## 4. Decision

| Rule | Decision |
|---|---|
| type remap, `use_masks_for_inland` | keep |
| recut (all four cases, node and triangle numbering) | keep, vectorised; a `dsf-encoding` byte-identity depends on it |
| depth ratio from distance masks, `int()` truncation, coast = 0 | keep |
| zone image with Pillow, reversed `zone_list`, Python `round` | keep (Pillow is a dependency; the rasteriser is the rule) |
| airport upgrade from the step-1 pickle | keep the arithmetic; the bounds come from the vector stage's record, `build_dsf` takes bounds |
| `"Existing"` mode | keep; the caller passes the `TextureId` list |
| `needs_mask` on the raw crop, threshold 30 | keep (reuse `orthostudio.textures.imprint`) |
| DXT1/DXT5 rebuild heuristics, `os.remove` of stale mask PNGs (`:684-693, 715-758`) | drop: keys and the store replace them (`textures-imprint.md`) |
| `download_queue` | replaced by the returned `TextureJob` list |
| `KeyError` on a barycentre outside the grid | fix: coded error (`SYS_INTERNAL_ERROR` until `DSF_MESH_OUTSIDE_TILE` exists) |

## 5. Wanted differences from Ortho4XP

None in the produced bytes. The mask decision, the `.ter` text and the recut are shared
with P1/P3 modules instead of being re-implemented.
