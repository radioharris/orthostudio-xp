"""Settings models: defaults, levels, validation (spec settings.md sections 2 and 4.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orthostudio.config import (
    LEVELS,
    Advanced,
    CoastTransition,
    Essential,
    Expert,
    Relief,
    Settings,
    settings_from_dict,
)
from orthostudio.errors import OsxpError


def test_levels_and_field_counts() -> None:
    assert LEVELS == ("essential", "advanced", "expert")
    assert list(Settings.model_fields) == list(LEVELS)
    # region is P5; overlays and data_dir are OrthoStudio XP's own, and so is check_updates
    assert len(Essential.model_fields) == 10
    assert len(Advanced.model_fields) == 14
    assert len(Expert.model_fields) == 25


def test_defaults_are_the_ortho4xp_defaults() -> None:
    s = Settings()
    e, a, x = s.essential, s.advanced, s.expert
    assert (e.provider, e.zoom_level, e.water_rendering, e.xplane_dir, e.data_dir) == (
        "BI",
        16,
        "XP11 + bathy",
        None,
        None,
    )
    assert (e.airports.mode, e.airports.zoom_level, e.airports.extent_km) == ("off", 18, 1.0)
    assert (e.coast_transition.profile, e.coast_transition.width_m) == ("sand", 100.0)
    assert (e.relief.source, e.relief.file, e.relief.fill_nodata) == ("auto", "", "nearest")
    assert (a.curvature_tol, a.limit_tris, a.road_level, a.min_area, a.max_area) == (
        2.0,
        3.0,
        1,
        0.001,
        200.0,
    )
    assert (a.sea_smoothing_mode, a.water_smoothing, a.apt_smoothing_pix) == ("zero", 10, 8)
    assert (a.ratio_water_pct, a.ratio_bathy, a.overlay_lod_km) == (25.0, 1.0, 25.0)
    assert (a.use_masks_for_inland, a.imprint_masks_to_dds, a.terrain_casts_shadows) == (
        False,
        True,
        True,
    )
    assert (x.mesh_zl, x.mask_zl, x.min_angle, x.lane_width, x.max_levelled_segs) == (
        19,
        14,
        10.0,
        4.0,
        200000,
    )
    assert (x.ovl_exclude_pol, x.ovl_exclude_net) == ([0], [])


def test_models_are_frozen_and_reject_unknown_fields() -> None:
    s = Settings()
    with pytest.raises(ValidationError):
        s.essential.zoom_level = 17  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Advanced(nope=1)  # type: ignore[call-arg]
    changed = s.model_copy(update={"essential": s.essential.model_copy(update={"zoom_level": 17})})
    assert changed.essential.zoom_level == 17
    assert s.essential.zoom_level == 16


@pytest.mark.parametrize(
    ("profile", "width", "ok"),
    [
        ("sand", 100, True),
        ("rocks", 0, True),
        ("3steps", [20, 50, 30], True),
        ("sand", [20, 50, 30], False),
        ("3steps", 100, False),
        ("3steps", [20, 50], False),
        ("3steps", [20, -1, 30], False),
        ("sand", -5, False),
    ],
)
def test_coast_transition_width_shape(profile: str, width: object, ok: bool) -> None:
    if ok:
        CoastTransition(profile=profile, width_m=width)  # type: ignore[arg-type]
    else:
        with pytest.raises(ValidationError):
            CoastTransition(profile=profile, width_m=width)  # type: ignore[arg-type]


def test_relief_file_required_with_source_file() -> None:
    with pytest.raises(ValidationError):
        Relief(source="file", file="  ")
    assert Relief(source="file", file="/dem/x.tif").file == "/dem/x.tif"
    assert Relief(source="auto", file="/dem/x.tif").file == "/dem/x.tif"  # kept for later


@pytest.mark.parametrize(
    ("level", "field", "bad"),
    [
        ("essential", "zoom_level", 21),
        ("essential", "provider", ""),
        ("advanced", "road_level", 6),
        ("advanced", "ratio_water_pct", 101),
        ("advanced", "curvature_tol", 0),
        ("advanced", "limit_tris", 5.5),
        ("expert", "mesh_zl", 15),
        ("expert", "mask_zl", 17),
        ("expert", "min_angle", 31),
        ("expert", "normal_map_strength", 1.5),
        ("expert", "ovl_exclude_pol", "0"),
    ],
)
def test_bounds_and_enums(level: str, field: str, bad: object) -> None:
    with pytest.raises(OsxpError) as info:
        settings_from_dict({level: {field: bad}})
    assert info.value.code == "CFG_VALUE_INVALID"
    assert info.value.context["name"] == f"{level}.{field}"


def test_settings_from_dict_ignores_unknown_keys(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="orthostudio.config"):
        s = settings_from_dict(
            {"essential": {"zoom_level": 15, "future": 1}, "advanced": {}, "unknown": {}}
        )
    assert s.essential.zoom_level == 15
    assert s.advanced == Advanced()
    assert [r.message for r in caplog.records] == [
        "unknown setting 'essential.future' ignored",
        "unknown setting 'unknown' ignored",
    ]
