"""settings_schema(): inlined, every leaf carries unit/hint/level/ortho4xp (spec section 4.3)."""

from __future__ import annotations

import json

from orthostudio.config import LEVELS, Settings, leaf_properties, settings_schema
from orthostudio.config.hints import ORTHO4XP_HINTS
from orthostudio.tilefiles import TILE_PARAMETERS

EXPECTED_LEAVES = {"essential": 16, "advanced": 14, "expert": 24}
ENUMS = {
    "essential.airports.mode": ["off", "on", "icao", "existing"],
    "essential.coast_transition.profile": ["sand", "rocks", "3steps"],
    "essential.water_rendering": ["XP11 + bathy", "XP12"],
    "essential.overlays": ["xplane", "none"],
    "essential.relief.source": ["auto", "file", "copernicus", "usgs", "canada", "south_america"],
    "essential.photo_look": ["as_delivered", "softer", "much_softer", "custom"],
    "essential.relief.fill_nodata": ["nearest", "zero"],
    "advanced.road_level": [0, 1, 2, 3, 4, 5],
    "advanced.sea_smoothing_mode": ["zero", "mean", "none"],
    "expert.mesh_zl": [16, 17, 18, 19, 20],
    "expert.mask_zl": [14, 15, 16],
}


def test_schema_is_inlined_json_and_lists_levels() -> None:
    schema = settings_schema()
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in schema
    assert schema["x-levels"] == list(LEVELS)
    for level in LEVELS:
        assert schema["properties"][level]["level"] == level
        assert schema["properties"][level]["type"] == "object"


def test_every_leaf_has_unit_hint_level_ortho4xp_default() -> None:
    leaves = leaf_properties()
    assert len(leaves) == sum(EXPECTED_LEAVES.values()) == 54
    for level, count in EXPECTED_LEAVES.items():
        assert sum(1 for k in leaves if k.startswith(level + ".")) == count
    for path, prop in leaves.items():
        level = path.split(".", 1)[0]
        for key in ("unit", "hint", "level", "ortho4xp", "default"):
            assert key in prop, (path, key)
        assert prop["level"] == level, path
        assert isinstance(prop["unit"], str)
        assert prop["hint"] and prop["description"] == prop["hint"], path
        assert prop["ortho4xp"] is None or isinstance(prop["ortho4xp"], str)


def test_hints_are_the_ortho4xp_hints_verbatim() -> None:
    leaves = leaf_properties()
    own = {"default_website", "default_zl"}  # empty hint in Ortho4XP, written by OrthoStudio XP
    for path, prop in leaves.items():
        name = prop["ortho4xp"]
        if name in own or name is None:
            continue
        assert prop["hint"] == ORTHO4XP_HINTS[name], path


def test_enums_and_units() -> None:
    leaves = leaf_properties()
    for path, values in ENUMS.items():
        assert leaves[path]["enum"] == values, path
    with_enum = {p for p, prop in leaves.items() if "enum" in prop}
    assert with_enum == set(ENUMS)
    assert leaves["advanced.ratio_water_pct"]["unit"] == "%"
    assert leaves["advanced.overlay_lod_km"]["unit"] == "km"
    assert leaves["advanced.min_area"]["unit"] == "km²"
    assert leaves["expert.min_angle"]["unit"] == "°"
    assert leaves["essential.zoom_level"]["unit"] == "ZL"
    assert leaves["advanced.limit_tris"]["unit"] == "M"


def test_ortho4xp_names_cover_the_surviving_tile_parameters() -> None:
    """The 44 tile parameters minus the 5 dropped ones all have an OrthoStudio XP field."""
    referenced = {prop["ortho4xp"] for prop in leaf_properties().values()} - {None}
    dropped = {"clean_bad_geometries", "iterate", "zone_list"}
    tile = set(TILE_PARAMETERS) - dropped
    assert tile <= referenced
    assert referenced - tile == {"ovl_exclude_pol", "ovl_exclude_net", "custom_scenery_dir"}


def test_schema_defaults_match_the_models() -> None:
    dumped = Settings().model_dump(mode="json")
    for path, prop in leaf_properties().items():
        node: object = dumped
        for part in path.split("."):
            assert isinstance(node, dict)
            node = node[part]
        assert prop["default"] == node, path
