# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Parser of the tile configuration (Ortho4XP's format): a full file and twisted cases, no eval."""

from __future__ import annotations

from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.tilefiles import TILE_PARAMETERS, parse_tile_cfg, tile_config, tile_defaults

SAMPLE_CFG = (
    "apt_smoothing_pix=8\n"
    "road_level=1\n"
    "road_banking_limit=0.5\n"
    "lane_width=4\n"
    "max_levelled_segs=200000\n"
    "water_simplification=0\n"
    "min_area=0.001\n"
    "max_area=200\n"
    "clean_bad_geometries=True\n"
    "mesh_zl=19\n"
    "curvature_tol=2\n"
    "apt_curv_tol=0.5\n"
    "apt_curv_ext=0.5\n"
    "coast_curv_tol=1\n"
    "coast_curv_ext=0.5\n"
    "limit_tris=3\n"
    "min_angle=10\n"
    "sea_smoothing_mode=zero\n"
    "water_smoothing=10\n"
    "iterate=0\n"
    "mask_zl=14\n"
    "masks_width=100\n"
    "masking_mode=sand\n"
    "use_masks_for_inland=False\n"
    "imprint_masks_to_dds=True\n"
    "distance_masks_too=False\n"
    "masks_use_DEM_too=False\n"
    "masks_custom_extent=\n"
    "cover_airports_with_highres=False\n"
    "cover_extent=1\n"
    "cover_zl=18\n"
    "water_tech=XP11 + bathy\n"
    "ratio_bathy=1.0\n"
    "ratio_water=0.25\n"
    "overlay_lod=25000\n"
    "sea_texture_blur=0\n"
    "normal_map_strength=1\n"
    "terrain_casts_shadows=True\n"
    "use_decal_on_terrain=False\n"
    "custom_dem=\n"
    "fill_nodata=True\n"
    "default_website=BI\n"
    "default_zl=14\n"
    "zone_list=[]\n"
)
"""A complete tile configuration: the 44 variables, as a tile built at BI ZL14 carries them."""


def test_a_complete_tile_cfg() -> None:
    values = parse_tile_cfg(SAMPLE_CFG, path=Path("tile.cfg"), strict=True)
    assert len(values) == 44 and set(values) == set(TILE_PARAMETERS)
    assert values["mask_zl"] == 14 and values["imprint_masks_to_dds"] is True
    assert values["water_tech"] == "XP11 + bathy"
    assert values["zone_list"] == [] and values["masks_custom_extent"] == ""
    assert values["default_website"] == "BI" and values["default_zl"] == 14
    assert values["masks_width"] == 100 and values["ratio_bathy"] == 1.0
    assert values["cover_airports_with_highres"] == "False"
    assert values["sea_texture_blur"] == 0.0 and isinstance(values["sea_texture_blur"], float)
    for name, value in values.items():
        expected = TILE_PARAMETERS[name].type
        assert isinstance(value, expected) or expected is list, name


def test_tile_config_lookup(tmp_path: Path) -> None:
    (tmp_path / "Ortho4XP_+43+005.cfg").write_text("mask_zl=15\n")
    assert tile_config(tmp_path)["mask_zl"] == 15
    assert tile_config(tmp_path, lat=43, lon=5)["mask_zl"] == 15
    merged = tile_config(tmp_path, with_defaults=True)
    assert merged["mask_zl"] == 15 and merged["water_tech"] == "XP11 + bathy"
    assert len(merged) == 44
    with pytest.raises(OsxpError) as exc:
        tile_config(tmp_path, lat=44, lon=5)
    assert exc.value.code == "CFG_TILE_FILE_MISSING"


def test_tile_config_falls_back_to_generic_name(tmp_path: Path) -> None:
    (tmp_path / "Ortho4XP.cfg").write_text("default_zl=17\n")
    assert tile_config(tmp_path, lat=43, lon=5) == {"default_zl": 17}


def test_defaults_are_fresh_copies() -> None:
    a, b = tile_defaults(), tile_defaults()
    a["zone_list"].append(1)
    assert b["zone_list"] == [] and TILE_PARAMETERS["zone_list"].default == []


def test_twisted_syntax() -> None:
    text = (
        "﻿# comment\r\n"
        "\r\n"
        "  mask_zl = 15  \r\n"
        "water_tech='XP12'\n"
        'default_website="Arc@"\n'
        "masks_width=[50, 100]\n"
        "overlay_lod=1e3\n"
        "zone_list=[[[43.0, 5.0, 43.1, 5.1], 18, 'BI']]\n"
        "zone_list.append([[43.2, 5.2, 43.3, 5.3], 17, 'SP'])\n"
        "imprint_masks_to_dds=False\n"
        "custom_dem=/path/with=equals\n"
        "new_param_from_1_50=whatever\n"
    )
    v = parse_tile_cfg(text)
    assert v["mask_zl"] == 15 and v["water_tech"] == "XP12" and v["default_website"] == "Arc@"
    assert v["masks_width"] == [50, 100] and v["overlay_lod"] == 1000.0
    assert v["zone_list"] == [
        [[43.0, 5.0, 43.1, 5.1], 18, "BI"],
        [[43.2, 5.2, 43.3, 5.3], 17, "SP"],
    ]
    assert v["imprint_masks_to_dds"] is False
    assert v["custom_dem"] == "/path/with=equals"
    assert v["new_param_from_1_50"] == "whatever"
    with pytest.raises(OsxpError) as exc:
        parse_tile_cfg("new_param_from_1_50=whatever\n", strict=True)
    assert exc.value.code == "CFG_LINE_INVALID" and exc.value.context["line"] == 1


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("mask_zl\n", "CFG_LINE_INVALID"),
        ("=3\n", "CFG_LINE_INVALID"),
        ("mask_zl=fourteen\n", "CFG_VALUE_INVALID"),
        ("mask_zl=14.0\n", "CFG_VALUE_INVALID"),
        ("ratio_water=\n", "CFG_VALUE_INVALID"),
        ("imprint_masks_to_dds=__import__('os').system('true')\n", "CFG_VALUE_INVALID"),
        ("zone_list=[1, 2\n", "CFG_VALUE_INVALID"),
        ("zone_list.append(os.getcwd())\n", "CFG_LINE_INVALID"),
        ("zone_list.append([1]\n", "CFG_LINE_INVALID"),
        ("masks_width=1+1\n", "CFG_VALUE_INVALID"),
    ],
)
def test_invalid_lines_raise(text: str, code: str) -> None:
    with pytest.raises(OsxpError) as exc:
        parse_tile_cfg(text)
    assert exc.value.code == code


def test_no_eval_of_strings() -> None:
    # str parameters are taken verbatim, whatever they look like
    v = parse_tile_cfg("custom_dem=__import__('os').system('true')\n")
    assert v["custom_dem"] == "__import__('os').system('true')"
