# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The decals a tile may name: the fine ground detail X-Plane draws over the photo near the ground.

X-Plane 12 ships them in ``Resources/default scenery/1000 decals`` and exports each as
``lib/g10/decals/<name>``; the land and sea terrain files of a tile name one on their ``DECAL_LIB``
line (``docs/specs/textures-ter.md``). The list is the one of setdecal, a tool of the X-Plane.Org
forum that rewrites that line in the tiles already built: its author asked for the same choice
here (2026-09-25). Five of its names are left out because X-Plane 12.4.4 no longer exports them
(``grass_and_asphalt_3``, ``mid_freq_test``, ``rail_dry_drp``, ``rail_dry_grass_emb``,
``rail_dry_grd``): a tile naming one would ask X-Plane for a decal it does not have.
"""

from __future__ import annotations

__all__ = ["DECALS", "DECAL_LIBRARY", "DEFAULT_DECAL", "decal_lib"]

DECAL_LIBRARY = "lib/g10/decals/"
"""Where X-Plane's library exports its decals."""

DEFAULT_DECAL = "maquify_2_green_key.dcl"
"""Ortho4XP's, the only one before the choice existed: a tile built with it keeps its key."""

DECALS: tuple[str, ...] = (
    "AGB_grass_and_asphalt_1.dcl",
    "AGS_shrub_dirt_green_key_1.dcl",
    "AGS_suburban_garden_1.dcl",
    "AGS_suburban_garden_1_proj.dcl",
    "AGS_suburban_garden_vdry_1.dcl",
    "AGS_suburban_wet_1.dcl",
    "AG_concrete.dcl",
    "LF_grey_1_mid.dcl",
    "apt_asphalt.dcl",
    "apt_concrete.dcl",
    "apt_concrete_tiles1.dcl",
    "apt_grass.dcl",
    "apt_markings1.dcl",
    "apt_markings2.dcl",
    "apt_markings3.dcl",
    "apt_wood_planks1.dcl",
    "asphalt_and_stony_dirt.dcl",
    "asphalt_gravel_edge.dcl",
    "bridges18.dcl",
    "cracked_dirt_and_asphalt.dcl",
    "cracked_dirt_and_asphalt2.dcl",
    "dry_grass_and_stony_dirt_1.dcl",
    "dry_grass_and_stony_dirt_2.dcl",
    "flinty_dirt_1.dcl",
    "flinty_dirt_2.dcl",
    "flinty_dirt_3.dcl",
    "grass_and_asphalt_1.dcl",
    "grass_and_asphalt_2.dcl",
    "grass_and_asphalt_green_key.dcl",
    "grass_and_asphalt_green_key2.dcl",
    "grass_and_shrubs.dcl",
    "grass_and_stony_dirt_1.dcl",
    "grass_and_stony_dirt_1_fine.dcl",
    "grass_dirt_and_RGB_LF.dcl",
    "grasses_combo.dcl",
    "grasses_combo_2.dcl",
    "grasses_combo_3.dcl",
    "industrial_asphalt.dcl",
    "industrial_outlay.dcl",
    "long_grass_and_gravel_1.dcl",
    "long_grass_and_shrubs.dcl",
    "low_freq_mod_1.dcl",
    "maquify_1_alpha_key.dcl",
    "maquify_1_green_key.dcl",
    "maquify_1_mod_key.dcl",
    "maquify_2_green_key.dcl",
    "park_grass_wet.dcl",
    "rail_ballast_dry.dcl",
    "rail_ballast_gray_dry.dcl",
    "rail_trackbed_brown.dcl",
    "road_EU_dry.dcl",
    "road_EU_rural.dcl",
    "road_hwy.dcl",
    "road_res_dry.dcl",
    "road_res_dry_grass_edge.dcl",
    "road_res_dry_gravel_edge.dcl",
    "road_res_junc_dry.dcl",
    "shingle.dcl",
    "shrub_and_dirt_1.dcl",
    "shrub_and_dirt_2.dcl",
    "shrubs_asphalt.dcl",
    "stony_dirt_1.dcl",
    "stony_dirt_2.dcl",
    "test_decal.dcl",
    "very_long_grass.dcl",
    "very_long_grass_and_dirt.dcl",
    "very_long_grass_modulator2.dcl",
)
"""In setdecal's order (byte order: capitals first)."""


def decal_lib(name: str) -> str:
    """The library path a terrain file names: ``lib/g10/decals/<name>``."""
    return DECAL_LIBRARY + name
