"""``Settings`` -> the Ortho4XP tile variables ``BuildSpec.config`` accepts (spec section 4.4).

Names are those of ``tilefiles.TILE_PARAMETERS`` plus the two overlay settings; values
carry the type Ortho4XP declares (``masks_width`` keeps its int/float-or-list quirk). Provider and
zoom level are *not* emitted: they are ``BuildSpec.provider`` / ``.zl``, chosen per request.
"""

from __future__ import annotations

from typing import Any

from orthostudio.config.models import Advanced, Settings

__all__ = ["AIRPORT_MODES", "to_build_overrides"]

AIRPORT_MODES: dict[str, str] = {
    "off": "False",
    "on": "True",
    "icao": "ICAO",
    "existing": "Existing",
}
"""OrthoStudio XP ``airports.mode`` -> Ortho4XP ``cover_airports_with_highres``."""

# advanced / expert fields whose Ortho4XP name and unit are unchanged (OrthoStudio XP name ->
# Ortho4XP name).
_SAME: tuple[tuple[str, str], ...] = (
    ("curvature_tol", "curvature_tol"),
    ("limit_tris", "limit_tris"),
    ("road_level", "road_level"),
    ("min_area", "min_area"),
    ("max_area", "max_area"),
    ("sea_smoothing_mode", "sea_smoothing_mode"),
    ("water_smoothing", "water_smoothing"),
    ("apt_smoothing_pix", "apt_smoothing_pix"),
    ("ratio_bathy", "ratio_bathy"),
    ("use_masks_for_inland", "use_masks_for_inland"),
    ("imprint_masks_to_dds", "imprint_masks_to_dds"),
    ("terrain_casts_shadows", "terrain_casts_shadows"),
    ("mesh_zl", "mesh_zl"),
    ("mask_zl", "mask_zl"),
    ("apt_curv_tol", "apt_curv_tol"),
    ("apt_curv_ext", "apt_curv_ext"),
    ("coast_curv_tol", "coast_curv_tol"),
    ("coast_curv_ext", "coast_curv_ext"),
    ("min_angle", "min_angle"),
    ("road_banking_limit", "road_banking_limit"),
    ("lane_width", "lane_width"),
    ("max_levelled_segs", "max_levelled_segs"),
    ("water_simplification", "water_simplification"),
    ("masks_use_dem_too", "masks_use_DEM_too"),
    ("masks_custom_extent", "masks_custom_extent"),
    ("distance_masks_too", "distance_masks_too"),
    ("sea_texture_blur", "sea_texture_blur"),
    ("normal_map_strength", "normal_map_strength"),
    ("use_decal_on_terrain", "use_decal_on_terrain"),
    # OrthoStudio XP's own, under its own name: Ortho4XP has no such setting, and the
    # switch reached nothing before this line (found on 2026-09-18, shipped in 0.1.3).
    ("decal_on_sea", "decal_on_sea"),
    ("ovl_exclude_pol", "ovl_exclude_pol"),
    ("ovl_exclude_net", "ovl_exclude_net"),
)


_ADVANCED = frozenset(Advanced.model_fields)


def _masks_width(width: float | list[float]) -> int | float | list[float]:
    if isinstance(width, list):
        return [float(v) for v in width]
    return int(width) if float(width).is_integer() else float(width)


PHOTO_LOOKS: dict[str, tuple[float, float, float]] = {
    "as_delivered": (0.0, 0.0, 0.0),
    "softer": (-0.03, 0.0, -0.15),
    "much_softer": (-0.06, -0.03, -0.30),
}
"""``look`` -> (brightness, contrast, saturation). A user found most aerial imagery too bright
and too saturated (2026-09-18); ``custom`` uses the three expert values instead."""


def _photo(look: str, x: Any) -> dict[str, float]:
    """The three colour values a build consumes, from the look chosen."""
    if look == "custom":
        values = (float(x.photo_brightness), float(x.photo_contrast), float(x.photo_saturation))
    else:
        values = PHOTO_LOOKS[look]
    names = ("photo_brightness", "photo_contrast", "photo_saturation")
    return dict(zip(names, values, strict=True))


def _custom_dem(relief: Any) -> str:
    """Ortho4XP's ``custom_dem``: a file, the name of a source, or empty for the default relief.

    ``copernicus`` is OrthoStudio XP's own source (``dem/sources.py``, a user asked 2026-09-17)
    and ``usgs`` the USGS 3DEP at 1/3 arc-second, the sharpest relief of the United States (a
    user asked for other sources, "especially for the US and Canada", 2026-09-18): the name goes
    where Ortho4XP puts a source name.
    """
    if relief.source == "file":
        return str(relief.file)
    if relief.source == "copernicus":
        return "COP30"
    if relief.source == "usgs":
        return "NED1/3"
    if relief.source == "canada":
        # Canada's lidar covers the part of the country that has been flown: it is laid *over*
        # Copernicus, which answers everywhere else (``dem/hrdem.py``, same user, same day).
        return "COP30;HRDEM"
    return ""


def to_build_overrides(settings: Settings) -> dict[str, Any]:
    """The tile variables and overlay settings of ``settings`` under their Ortho4XP names."""
    e, a, x = settings.essential, settings.advanced, settings.expert
    out: dict[str, Any] = {
        "cover_airports_with_highres": AIRPORT_MODES[e.airports.mode],
        "cover_zl": int(e.airports.zoom_level),
        "cover_extent": float(e.airports.extent_km),
        "masking_mode": e.coast_transition.profile,
        "masks_width": _masks_width(e.coast_transition.width_m),
        "water_tech": e.water_rendering,
        "custom_dem": _custom_dem(e.relief),
        "fill_nodata": e.relief.fill_nodata == "nearest",
        "ratio_water": float(a.ratio_water_pct) / 100.0,
        "overlay_lod": float(a.overlay_lod_km) * 1000.0,
        **_photo(e.photo_look, x),
    }
    for osxp_name, ortho4xp_name in _SAME:
        value = getattr(a, osxp_name) if osxp_name in _ADVANCED else getattr(x, osxp_name)
        out[ortho4xp_name] = list(value) if isinstance(value, list) else value
    return out
