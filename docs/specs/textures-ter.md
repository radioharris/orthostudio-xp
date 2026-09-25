# Textures: X-Plane `.ter` terrain definitions

Status: P1, written before the code. Code: `src/orthostudio/textures/ter.py`.

## 1. The rule in plain language

Every (texture, triangle type, overlay flag) that the DSF references gets a `.ter` text file
in `terrain/` that tells X-Plane how to draw the texture: its centre and ground size, the
DDS, whether it is water (blended with X-Plane water) or land, decals and shadows. The DSF
names the file (`terrain/<name>.ter`) and the file names the texture (`../textures/<name>.dds`).

## 2. Origin in Ortho4XP: `src/O4_DSF_Utils.py:261-357` (`create_terrain_file`)

Inputs: `tri_type` (0 land, 1 inland water, 2 sea), `is_overlay`, the texture attributes
and the tile parameters `water_tech`, `imprint_masks_to_dds`, `mask_zl`, `use_decal_on_terrain`,
`decal_on_sea`,
`terrain_casts_shadows`, plus the module global `use_test_texture`.

| Line | Directive | Condition |
|---|---|---|
| 275-278 | file name `<texture>` + `""` / `"_water"` / `"_sea"` + `"_overlay"` if overlay + `.ter` | `tri_type` 0/1/2, `is_overlay` |
| 280-281 | texture replaced by `test_texture.dds` | `use_test_texture` |
| 285 | `A`, `800`, `TERRAIN`, blank line | always |
| 287-300 | `LOAD_CENTER {lat_med:.5f} {lon_med:.5f} {size} 4096` with `(lat_med, lon_med) = gtile_to_wgs84(til_x + 8, til_y + 8, zl)` and `size = int(webmercator_pixel_size(lat_med, zl) * 4096)` (`2π·6378137·cos(lat)/2**(zl+8)`) | always |
| 302 | `BASE_TEX_NOWRAP ../textures/<texture>.dds` | always |
| 305-307 | `WATER_COLOR_MASK` | `tri_type in (1, 2)` and not overlay (XP12 water) |
| 308-317 | `BORDER_TEX ../textures/water_transition.png` (+ copy of `Utils/water_transition.png` into `textures/`) | `tri_type == 1` (overlay), or `tri_type == 2 and is_overlay == "ratio_water"` — the latter is dead code: `is_overlay` is a bool |
| 319-336 | `LOAD_CENTER_BORDER {lat_med:.5f} {lon_med:.5f} {size} {4096 // 2**(zl - mask_zl)}` then `BORDER_TEX ../textures/<til_y>_<til_x>_ZL<zl>.png` | `tri_type == 2` and not `imprint_masks_to_dds` (mask kept as an external PNG) |
| 341-344 | `DECAL_LIB lib/g10/decals/maquify_2_green_key.dcl` | `use_decal_on_terrain` and land (`tri_type == 0`), the sea (2) only with `decal_on_sea`, inland water (1) never. Ortho4XP writes it on land and sea alike (`tri_type != 1`), which `decal_on_sea` restores: a user asked for the land alone, 2026-09-17. The hint says `maquify_1`, the code writes `maquify_2`. OrthoStudio XP's DSF step writes no decal since 2026-09-26: the pack writes the line (`with_decal`, `takes_decal`), with the decal chosen in Settings (`expert.decal`), so that turning decals on or off or choosing another builds the pack alone; `ter_text` keeps the rule for its fidelity |
| 346-349 | `WET` / `NO_ALPHA` | `tri_type in (1, 2)` / land |
| 351-352 | `NO_SHADOW` | `tri_type in (1, 2)` or not `terrain_casts_shadows` |

Which combinations are produced (`O4_DSF_Utils.py:669-710` and `895-912`):

- sea triangles (`tri_type == 2`) whose texture passes `needs_mask` get `is_overlay =
  (water_tech == "XP11 + bathy") or not imprint_masks_to_dds`; the others go to
  `terrain_Water` (no `.ter`);
- inland water (`tri_type == 1`) is always an overlay;
- land (`tri_type == 0`) is never an overlay.

So `(1, False)` (XP12 inland water) and `(0, True)` are unreachable in Ortho4XP. The reference tile
(+43+005, `water_tech = XP11 + bathy`, `imprint_masks_to_dds = True`) has 17 land, 15
`_water_overlay` and 7 `_sea_overlay` files = 39.

Parameters that are **not** consumed by the `.ter` although they sit next to it in the cfg:
`ratio_water` (baked into `water_transition.png` by the mask stage), `ratio_bathy`,
`normal_map_strength` (DSF normals), `sea_texture_blur` (imagery), `overlay_lod` (DSF).

## 3. Inputs and outputs

```
class TerKind(StrEnum): LAND, WATER, WATER_OVERLAY, SEA, SEA_OVERLAY
    .tri_type -> 0 | 1 | 2 ; .overlay -> bool ; TerKind.of(tri_type, overlay)
class TerParams(frozen dataclass): water_tech="XP11 + bathy", imprint_masks_to_dds=True,
    mask_zl=14, use_decal_on_terrain=False, terrain_casts_shadows=True, use_test_texture=False
def ter_filename(t, kind) -> str                    # "<y>_<x>_<prov><zl>[_water|_sea][_overlay].ter"
def ter_text(t, kind, *, lat_med, lon_med, params) -> str
def ter_center(t) -> (lat_med, lon_med)             # grid.tile_to_wgs84(til_x + 8, til_y + 8, zl)
def load_center_size(lat_med, zl) -> int            # int(grid.webmercator_pixel_size * 4096)
def sea_kind(params) -> TerKind                     # SEA_OVERLAY under Ortho4XP's rule, SEA for XP12
def border_mask_filename(t) -> str                  # "<y>_<x>_ZL<zl>.png"
```

`WATER` (XP12 inland water) is kept because the function supports it and XP12 water tech may
use it later; it is documented as unreachable in Ortho4XP.

## 4. Decision

| Rule | Decision |
|---|---|
| every directive and its condition, the `.5f` formatting, `int()` truncation of the size | keep, byte-identical |
| file name suffixes | keep |
| `is_overlay == "ratio_water"` branch | drop (dead) |
| copy of `water_transition.png` into `textures/` | out of `ter_text` (pure function); the integrator copies it once per tile when any `WATER_OVERLAY` terrain exists (`WATER_TRANSITION_PNG` names it) |
| `use_test_texture` global | ported as a `TerParams` field |
| `DECAL_LIB` written on sea and land alike (`tri_type != 1`), against the comment above it | keep (byte fidelity); flagged in the code |

## 5. Acceptance tests (`tests/test_textures_ter.py`)

- Oracle, until decision 0010 removed it: the 39 `.ter` of Ortho4XP's frozen build of +43+005
  were reproduced from their names, `ter_center`, and `TerParams` read from
  `Ortho4XP_+43+005.cfg`, byte for byte (`\n` endings, one blank line after `TERRAIN`).
- Unit: every directive path (`WATER_COLOR_MASK` for XP12 sea, `LOAD_CENTER_BORDER` +
  `BORDER_TEX` mask when not imprinting, `DECAL_LIB` on land and sea but not inland water,
  `NO_SHADOW` on land when shadows are off, `test_texture.dds`), `TerKind.of` round trip,
  `sea_kind`, and the 5-decimal formatting.

## 6. Wanted differences from Ortho4XP

None in the text. The file is produced by a pure function; writing, the `water_transition.png`
copy and the DSF `TERT` entry are the integrator's.
