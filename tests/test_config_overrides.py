"""to_build_overrides(): names and types BuildSpec.config accepts (spec section 4.4)."""

from __future__ import annotations

from pathlib import Path

from orthostudio.cli import _parse_sets
from orthostudio.config import (
    Advanced,
    CoastTransition,
    Essential,
    Relief,
    Settings,
    to_build_overrides,
)
from orthostudio.config.models import AirportCoverage
from orthostudio.model import TileRef
from orthostudio.pipeline.build import OVERLAY_SETTINGS, BuildSpec
from orthostudio.tilefiles import OSXP_PARAMETERS, TILE_PARAMETERS, tile_defaults

NOT_EMITTED = {"default_website", "default_zl", "zone_list", "iterate", "clean_bad_geometries"}


def _spec(config: dict[str, object], tmp_path: Path) -> BuildSpec:
    return BuildSpec(
        tile=TileRef(43, 5),
        provider="BI",
        zl=14,
        out_dir=tmp_path,
        config=config,
    )


def test_default_overrides_reproduce_the_ortho4xp_tile_defaults(tmp_path: Path) -> None:
    ov = to_build_overrides(Settings())
    expected = (set(TILE_PARAMETERS) - NOT_EMITTED) | set(OVERLAY_SETTINGS) - {"keep_objects"}
    # photo_zones comes from the zones drawn on the map, not from the settings
    assert set(ov) == expected | (set(OSXP_PARAMETERS) - {"photo_zones"})
    cfg = _spec(ov, tmp_path).tile_config()
    defaults = tile_defaults()
    for name, value in ov.items():
        if name in defaults:
            assert cfg[name] == defaults[name], name
            assert value == defaults[name], name
    assert cfg["default_website"] == "BI" and cfg["default_zl"] == 14
    assert cfg["zone_list"] == [] and cfg["iterate"] == 0


def test_emitted_types_match_the_declared_ortho4xp_types() -> None:
    ov = to_build_overrides(Settings())
    for name, value in ov.items():
        if name in OSXP_PARAMETERS:  # OrthoStudio XP's own, typed the same way
            assert isinstance(value, OSXP_PARAMETERS[name].type), name
            continue
        if name not in TILE_PARAMETERS:
            assert isinstance(value, list), name
            continue
        declared = TILE_PARAMETERS[name].type
        if name == "masks_width":
            assert isinstance(value, int | float | list)
        elif declared is float:
            assert type(value) is float, name
        elif declared is int:
            assert type(value) is int, name
        elif declared is bool:
            assert type(value) is bool, name
        else:
            assert isinstance(value, declared), name


def test_conversions_and_mappings(tmp_path: Path) -> None:
    s = Settings(
        essential=Essential(
            airports=AirportCoverage(mode="icao", zoom_level=19, extent_km=2),
            coast_transition=CoastTransition(profile="3steps", width_m=[20, 50, 30]),
            water_rendering="XP12",
            relief=Relief(source="file", file="/dem/x.tif", fill_nodata="zero"),
        ),
        advanced=Advanced(ratio_water_pct=40, overlay_lod_km=12.5),
    )
    ov = to_build_overrides(s)
    assert ov["cover_airports_with_highres"] == "ICAO"
    assert ov["cover_zl"] == 19 and ov["cover_extent"] == 2.0
    assert ov["masking_mode"] == "3steps" and ov["masks_width"] == [20.0, 50.0, 30.0]
    assert ov["water_tech"] == "XP12"
    assert ov["custom_dem"] == "/dem/x.tif" and ov["fill_nodata"] is False
    assert ov["ratio_water"] == 0.4 and ov["overlay_lod"] == 12500.0
    assert ov["masks_use_DEM_too"] is False
    cfg = _spec(ov, tmp_path).tile_config()
    assert cfg["masks_width"] == [20.0, 50.0, 30.0] and cfg["custom_dem"] == "/dem/x.tif"

    auto = Settings(essential=Essential(relief=Relief(source="auto", file="/dem/x.tif")))
    assert to_build_overrides(auto)["custom_dem"] == ""
    # the two sources OrthoStudio XP downloads itself go where Ortho4XP puts a source name
    cop = Settings(essential=Essential(relief=Relief(source="copernicus")))
    assert to_build_overrides(cop)["custom_dem"] == "COP30"
    usgs = Settings(essential=Essential(relief=Relief(source="usgs")))
    assert to_build_overrides(usgs)["custom_dem"] == "NED1/3"
    for mode, ortho4xp_value in (("off", "False"), ("on", "True"), ("existing", "Existing")):
        s2 = Settings(essential=Essential(airports=AirportCoverage(mode=mode)))  # type: ignore[arg-type]
        assert to_build_overrides(s2)["cover_airports_with_highres"] == ortho4xp_value


def test_overrides_are_accepted_by_the_cli_set_grammar() -> None:
    """``str(value)`` of every override parses back as ``--set name=value`` (Ortho4XP cfg lines)."""
    ov = to_build_overrides(Settings())
    parsed = _parse_sets([f"{k}={v}" for k, v in ov.items()])
    assert parsed == ov


def test_scalar_width_keeps_ortho4xp_int_when_integral() -> None:
    ov = to_build_overrides(Settings())
    assert ov["masks_width"] == 100 and type(ov["masks_width"]) is int
    s = Settings(essential=Essential(coast_transition=CoastTransition(width_m=120.5)))
    assert to_build_overrides(s)["masks_width"] == 120.5


def test_the_photo_look_and_the_decals_on_the_sea_reach_the_build() -> None:
    """Two settings of OrthoStudio XP's own; the decals reached nothing before (2026-09-18)."""
    from orthostudio.config.models import Expert
    from orthostudio.config.overrides import PHOTO_LOOKS

    for look, (b, c, sat) in PHOTO_LOOKS.items():
        ov = to_build_overrides(Settings(essential=Essential(photo_look=look)))  # type: ignore[arg-type]
        assert (ov["photo_brightness"], ov["photo_contrast"], ov["photo_saturation"]) == (b, c, sat)
    mine = Settings(
        essential=Essential(photo_look="custom"),
        expert=Expert(photo_brightness=0.1, photo_saturation=-0.4),
    )
    ov = to_build_overrides(mine)
    assert ov["photo_brightness"] == 0.1 and ov["photo_saturation"] == -0.4
    # the sliders are ignored while the look is not 'custom'
    unused = Settings(
        essential=Essential(photo_look="softer"), expert=Expert(photo_saturation=-0.4)
    )
    assert to_build_overrides(unused)["photo_saturation"] == PHOTO_LOOKS["softer"][2]
    assert to_build_overrides(Settings())["decal_on_sea"] is False
    assert to_build_overrides(Settings(expert=Expert(decal_on_sea=True)))["decal_on_sea"] is True
