"""The files of a tile, in the formats Ortho4XP defined and OrthoStudio XP keeps.

The 44 tile variables and the text of their settings file (``tile_settings.cfg`` in an
OrthoStudio XP pack, ``Ortho4XP_<tile>.cfg`` in a tile Ortho4XP built), the index of a tile's mask
PNGs, the tile folder names, and the reader of the ``terrain/*.ter`` files, with which the Library
import learns the provider and zoom level of a tile Ortho4XP built. Spec:
``docs/specs/tile-files.md``.
"""

from orthostudio.tilefiles._grid import TextureId
from orthostudio.tilefiles.config import (
    TILE_PARAMETERS,
    TileParameter,
    parse_tile_cfg,
    tile_cfg_text,
    tile_cfg_values,
    tile_config,
    tile_defaults,
)
from orthostudio.tilefiles.masks import (
    MaskIndex,
    MaskWindow,
    mask_tile_for,
    masks_index,
)
from orthostudio.tilefiles.paths import (
    long_latlon,
    round_latlon,
    short_latlon,
    tile_cfg_path,
)
from orthostudio.tilefiles.terrain import (
    TerFile,
    TerKind,
    list_textures,
    parse_ter_name,
    read_all_ter,
    read_ter,
    ter_stem,
)

__all__ = [
    "TILE_PARAMETERS",
    "MaskIndex",
    "MaskWindow",
    "TerFile",
    "TerKind",
    "TextureId",
    "TileParameter",
    "list_textures",
    "long_latlon",
    "mask_tile_for",
    "masks_index",
    "parse_ter_name",
    "parse_tile_cfg",
    "read_all_ter",
    "read_ter",
    "round_latlon",
    "short_latlon",
    "ter_stem",
    "tile_cfg_path",
    "tile_cfg_text",
    "tile_cfg_values",
    "tile_config",
    "tile_defaults",
]
