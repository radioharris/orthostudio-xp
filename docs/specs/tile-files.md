# Tile files: the formats of Ortho4XP that OrthoStudio XP keeps

Package `src/orthostudio/tilefiles/`. Tests: `tests/test_tilefiles_*.py` (synthetic files). Origin:
Ortho4XP's output formats.

## 1. What the package holds

- the **44 tile variables** (`TILE_PARAMETERS`) and the text of their settings file: the tile
  configuration of a build uses Ortho4XP's names, `--set` and the API parse values with them, and
  every pack carries the values it was built with in `tile_settings.cfg` (decision 0011);
- the **index of a tile's mask PNGs**, named as Ortho4XP names them, over the masks artefact;
- the **tile folder names** (`short_latlon`, `round_latlon`, `long_latlon`);
- the reader of the **`terrain/*.ter` and the `Ortho4XP_<tile>.cfg` of a tile Ortho4XP built**,
  which `osxp import-ortho4xp` uses to learn a tile's provider and zoom level.

## 2. Inputs read

| File | Written by Ortho4XP in | Reader |
|---|---|---|
| `terrain/*.ter` of an imported tile | `O4_DSF_Utils.py:261-357` (`create_terrain_file`) | `terrain.read_ter`, `terrain.list_textures` |
| `Ortho4XP_<tile>.cfg` (fallback `Ortho4XP.cfg`) of an imported tile | `O4_Config_Utils.py:585-604` (`write_to_config`) | `config.tile_config` |
| `<y>_<x>.png` of the masks artefact | `O4_Mask_Utils.py:64-260` (`build_masks`), names `O4_File_Names.py:334-335` | `masks.masks_index` |

`<tile>` is `short_latlon` (`+43+005`) and `<10x10>` is `round_latlon` (`+40+000`),
`O4_File_Names.py:24-41`. `paths.py` ports these two formatters.

## 3. Rules ported

### 3.1 `.ter` file name and content (`O4_DSF_Utils.py:261-357`)

Rule. A terrain file is named after its texture, `{til_y}_{til_x}_{provider}{zl}` (the DDS
stem, `O4_File_Names.py:413-440`), followed by a suffix that encodes the triangle type and the
overlay flag:

| `tri_type` | `is_overlay` | suffix | meaning |
|---|---|---|---|
| 0 | False | `` | land |
| 1 | True | `_water_overlay` | inland water; **always** an overlay (`O4_DSF_Utils.py:883`) |
| 1 | False | `_water` | never produced by Ortho4XP (kept for completeness) |
| 2 | True | `_sea_overlay` | sea with mask, `water_tech == "XP11 + bathy"` or `imprint_masks_to_dds == False` (`O4_DSF_Utils.py:711-716`) |
| 2 | False | `_sea` | sea with mask, `water_tech == "XP12"` and masks imprinted |

Sea triangles whose mask crop has a maximum <= 30 (`O4_Mask_Utils.py:38-60`) get no `.ter`
at all: they go to the built-in `terrain_Water` (`O4_DSF_Utils.py:763-764`). Hence the
number of `.ter` files is the number of (texture, kind) pairs actually used by the DSF, not
3 x textures.

Content (line by line, in this order): `A`, `800`, `TERRAIN`, blank,
`LOAD_CENTER {lat_med:.5f} {lon_med:.5f} {size_m} 4096` where `(lat_med, lon_med) =
gtile_to_wgs84(til_x + 8, til_y + 8, zl)` and `size_m = int(webmercator_pixel_size(lat_med,
zl) * 4096)`, `BASE_TEX_NOWRAP ../textures/{stem}.dds`, then optional `WATER_COLOR_MASK`,
`BORDER_TEX ../textures/water_transition.png`, `LOAD_CENTER_BORDER ...` + `BORDER_TEX
../textures/{y}_{x}_ZL{zl}.png`, `DECAL_LIB ...`, then `WET` (water kinds) or `NO_ALPHA`
(land), then `NO_SHADOW` (water kinds, or land when `terrain_casts_shadows` is off).

Decision: **keep** the naming and read it back. The reader does not interpret the flag lines
beyond keeping them (`TerFile.directives`); the P1 writer (`orthostudio.textures.ter`) owns the
writing rule and its own spec.

Ambiguity handled: `{provider}{zl}` is not separable by a regex when the provider code ends
with digits (`PDOK18` at ZL 16 gives `PDOK1816`). The reader uses the `LOAD_CENTER`
longitude: for each candidate split (`zl` = last 1 or 2 digits), `lon_med(til_x, zl)` is
recomputed and must match the file's `LOAD_CENTER` to 5e-5 degrees (the file holds 5
decimals); exactly one candidate matches because `lon_med` is a strictly different function
of `zl` for a fixed `til_x` (`til_x` is a multiple of 16 <= 2^zl - 16). If the file has no
`LOAD_CENTER` line, the shortest provider code (2-digit `zl`) wins when `zl` is in 10..19,
else the 1-digit one; a `zl` hint can be passed to remove the ambiguity.

Provider codes may contain `_` (review): Ortho4XP accepts any `.lay` file name as a code and
ships `g2xpl_16`. The name is split from the right: the known kind suffix (`_water`, `_sea`,
`_overlay`, or none) is taken off first, the `{provider}{zl}` split is then decided as above,
so `6016_8448_My_Prov14_sea_overlay.ter` reads as provider `My_Prov`, ZL 14, `sea_overlay`.

Acceptance: on synthetic `.ter` files, `list_textures` groups the kinds of each texture; every
texture stem equals the `BASE_TEX_NOWRAP` stem of its files; `LOAD_CENTER` recomputed from the parsed
`TextureId` reproduces the file's values to 1e-5. (Measured on the frozen Ortho4XP build of +43+005
until decision 0010: 17 textures, 39 pairs, the 7 `sea_overlay` textures exactly the 7 DXT5 ones.)

### 3.2 Tile configuration (`O4_Config_Utils.py:523-583` and `585-604`)

Rule. `Ortho4XP_<tile>.cfg` is a flat `key=value` file, one parameter per line, written with
`str(value)` (`write_to_config`); an OrthoStudio XP pack's `tile_settings.cfg` has the same form.
Blank lines and lines starting with `#` are ignored. When reading, Ortho4XP strips one pair of
surrounding quotes (compatibility with <= 1.20), then: for `bool` and `list` parameters it **`exec`s
the value as Python** (`self.var = <value>`), for the others it calls the declared type (`int`,
`float`, `str`) on the string. Any error is swallowed (message at verbosity 2) and the parameter
keeps its default; an unknown key is a `KeyError`, also swallowed. Lines of the form
`zone_list.append([...])` (<= 1.20) are `exec`ed too.

Decision: **fix**.
- Values of `bool`/`list` parameters (and the `zone_list.append` form) are parsed with
  `ast.literal_eval`, never `exec`/`eval`. Everything `str(value)` can write for these
  parameters is a literal.
- A line without `=` or a value that does not convert raises `CFG_LINE_INVALID` /
  `CFG_VALUE_INVALID` instead of silently keeping the default (see `docs/specs/errors.md`).
- The line is split on the **first** `=`; Ortho4XP splits on every `=` and drops any line with two
  (none of the 44 tile parameters can legitimately contain one, so this is only more robust).
- Unknown keys are kept as raw strings under their own name, so that nothing written by a
  newer or older Ortho4XP is lost; `strict=True` turns them into `CFG_LINE_INVALID`.
- The declared types come from `cfg_vars` (`O4_Config_Utils.py:16-352`), ported as the table
  `TILE_PARAMETERS` (name, type, default) for the 44 tile parameters written by
  `write_to_config` (`list_tile_vars`, `O4_Config_Utils.py:423-430`). Quirks kept as is:
  `masks_width` is declared `list` but its default is the integer 100 (so it may be an int or a
  list), `cover_airports_with_highres` is a `str` among `False/True/ICAO/Existing`.
- `with_defaults=True` merges the Ortho4XP defaults for parameters absent from the file.

Acceptance: a complete tile cfg, as a BI ZL14 tile carries it, parses to 44 typed values with `mask_zl == 14`,
`imprint_masks_to_dds is True`, `water_tech == "XP11 + bathy"`, `zone_list == []`,
`masks_custom_extent == ""`, `default_website == "BI"`, `default_zl == 14`; twisted cases
(quotes, spaces, `zone_list.append`, `masks_width=[50, 100]`, `1e3`, comments, CRLF, BOM,
unknown keys, `eval`-only strings such as `__import__('os')`) behave as specified.

### 3.3 Mask files (`O4_Mask_Utils.py:23-60`, `O4_File_Names.py:334-345`)

Rule. Masks are built at `mask_zl` (default 14), one 4096x4096 8-bit PNG per mask texture,
named `{m_til_y}_{m_til_x}.png` (Ortho4XP keeps them in `Masks/<10x10>/<tile>/`), where `(m_til_x, m_til_y)` are
the top-left 256 px tile indices of the mask texture at `mask_zl` (multiples of 16). A
texture `(til_x, til_y, zl)` with `zl >= mask_zl` maps to the mask texture
`m_til = (int(til / factor) // 16) * 16` with `factor = 2 ** (zl - mask_zl)`, and to the
sub-window `(rx, ry) = int((til - factor * m_til) / 16)` of side `4096 // factor` pixels at
offset `(rx * 4096 / factor, ry * 4096 / factor)`. A texture with `zl < mask_zl` never gets a
mask. `_dist.png` files and the `Combined_imagery/` sub-directory are other products and are
not indexed.

Decision: **keep**. `masks_index` returns a lookup `(m_til_x, m_til_y) -> Path | None` over the
files present; `mask_tile_for` ports the index arithmetic so that the imprint stage
(`orthostudio.textures.imprint.mask_for_texture`) does not re-derive it. The index reads one flat
directory of `{y}_{x}.png`, the masks artefact of `orthostudio.masks@1`.

Acceptance: `mask_tile_for` at
ZL16 for texture `(8448, 6016)` (ZL14 texture `(8448 * 4 = 33792, 24064)`) gives back
`(8448, 6016)` with `factor 4` and window side 1024; `lookup` returns `None` for an absent
mask, never raises.

## 4. Public interface (`src/orthostudio/tilefiles/`)

```python
list_textures(build_dir) -> list[tuple[TextureId, tuple[TerKind, ...]]]   # sorted by (zl, til_y, til_x)
read_ter(path) -> TerFile                                                   # texture, kind, load_center, base_tex, directives
parse_ter_name(name, *, zl=None) -> tuple[TextureId, TerKind]
tile_config(build_dir, *, lat=None, lon=None, strict=False, with_defaults=False) -> dict[str, Any]
parse_tile_cfg(text, *, path=None, strict=False) -> dict[str, Any]
TILE_PARAMETERS: dict[str, TileParameter]                                  # name -> (type, default)
tile_cfg_text(values) -> str, tile_defaults() -> dict[str, Any], tile_cfg_values(provider=, zl=, overrides=)
masks_index(masks_dir, mask_zl) -> MaskIndex                               # callable (til_x, til_y) -> Path | None
mask_tile_for(til_x, til_y, zl, mask_zl) -> MaskWindow | None
short_latlon(lat, lon), round_latlon(lat, lon), long_latlon(lat, lon), tile_cfg_path(build_dir, lat, lon)
```

`TextureId` is `orthostudio.imagery.grid`'s. `TerKind` is a `StrEnum` with the values `land`,
`water`, `water_overlay`, `sea`, `sea_overlay` (the same strings the P1 `.ter` writer uses).

## 5. Errors

| Situation | Code |
|---|---|
| build dir or `terrain/` missing | `SYS_WORKING_DIR_INVALID` (message names the directory) |
| tile cfg absent (and no `Ortho4XP.cfg`) | `CFG_TILE_FILE_MISSING` |
| cfg line without `=`, unknown key in strict mode, unparsable literal | `CFG_LINE_INVALID` |
| cfg value not of the declared type | `CFG_VALUE_INVALID` |
| `.ter` name not of the Ortho4XP form, or content inconsistent with the name | `DSF_SOURCE_CORRUPTED` (message names the file) |

`DSF_SOURCE_CORRUPTED` is borrowed with a custom message: the registry has no code for a malformed
`.ter` file.

## 6. Wanted differences from Ortho4XP

- No `exec`/`eval` anywhere (3.2).
- Configuration errors are reported, not swallowed (3.2).
- Unknown configuration keys are preserved (3.2).
- `.ter` files are parsed from their content, not only their name, and checked against it (3.1).
