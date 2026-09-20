"""The typed settings: 14 essential / 14 advanced / 19 expert leaves (``docs/specs/settings.md``).

Frozen pydantic models. Defaults are the Ortho4XP defaults (``O4_Config_Utils.py`` ``cfg_vars``)
after the unit conversions of spec section 2; hints are the author's, verbatim
(:mod:`orthostudio.config.hints`). Each field carries ``unit``, ``hint``, ``level`` and ``ortho4xp``
(its name in Ortho4XP, or ``None``) in ``json_schema_extra`` for the UI.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orthostudio.config.hints import ORTHO4XP_HINTS

__all__ = [
    "LEVELS",
    "Advanced",
    "AirportCoverage",
    "CoastTransition",
    "Essential",
    "Expert",
    "Relief",
    "Settings",
]

LEVELS: tuple[str, ...] = ("essential", "advanced", "expert")

_FROZEN = ConfigDict(frozen=True, extra="forbid")

# Hints written by OrthoStudio XP for the three Ortho4XP variables whose cfg_vars hint is empty.
_OSXP_HINTS = {
    "default_website": (
        "Code of the imagery provider (see `osxp doctor --providers` for the ones alive and "
        "their maximum zoom level)."
    ),
    "default_zl": (
        "Zoom level of the imagery over the whole tile: ZL16 is about 2.4 m/px at mid "
        "latitudes, each level doubles the resolution and quadruples the download; mesh_zl "
        "caps it."
    ),
}


def _meta(level: str, ortho4xp: str | None, unit: str, hint: str | None = None) -> dict[str, Any]:
    if hint is None:
        if ortho4xp is None:
            raise ValueError("a field without an Ortho4XP counterpart needs an explicit hint")
        hint = ORTHO4XP_HINTS.get(ortho4xp) or _OSXP_HINTS[ortho4xp]
    return {"unit": unit, "hint": hint, "level": level, "ortho4xp": ortho4xp}


def _field(default: Any, level: str, ortho4xp: str | None, unit: str, **kw: Any) -> Any:
    hint = kw.pop("hint", None)
    meta = _meta(level, ortho4xp, unit, hint)
    return Field(default, description=meta["hint"], json_schema_extra=meta, **kw)


# -- essential -----------------------------------------------------------------------------


class AirportCoverage(BaseModel):
    """Ortho4XP ``cover_airports_with_highres`` / ``cover_zl`` / ``cover_extent``."""

    model_config = _FROZEN

    mode: Literal["off", "on", "icao", "existing"] = _field(
        "off", "essential", "cover_airports_with_highres", ""
    )
    zoom_level: int = _field(18, "essential", "cover_zl", "ZL", ge=14, le=20)
    extent_km: float = _field(1.0, "essential", "cover_extent", "km", ge=0)


class CoastTransition(BaseModel):
    """Ortho4XP ``masking_mode`` / ``masks_width``."""

    model_config = _FROZEN

    profile: Literal["sand", "rocks", "3steps"] = _field("sand", "essential", "masking_mode", "")
    width_m: float | list[float] = _field(100.0, "essential", "masks_width", "m")

    @model_validator(mode="after")
    def _check_width_shape(self) -> CoastTransition:
        w = self.width_m
        if isinstance(w, list):
            if self.profile != "3steps":
                raise ValueError(
                    f"profile {self.profile!r} takes a single width, not a list ({w!r})"
                )
            if len(w) != 3:
                raise ValueError(
                    f"profile '3steps' takes exactly three widths [a, b, c], got {w!r}"
                )
            if any(v < 0 for v in w):
                raise ValueError(f"widths must be >= 0, got {w!r}")
        else:
            if self.profile == "3steps":
                raise ValueError(
                    "profile '3steps' takes three widths [a, b, c], got a single value"
                )
            if w < 0:
                raise ValueError(f"width_m must be >= 0, got {w!r}")
        return self


class Relief(BaseModel):
    """Ortho4XP ``custom_dem`` (empty = the default global DEM) and ``fill_nodata``."""

    model_config = _FROZEN

    source: Literal["auto", "file", "copernicus", "usgs", "canada", "south_america"] = _field(
        "auto", "essential", "custom_dem", ""
    )
    file: str = _field("", "essential", "custom_dem", "")
    folder: str = _field(
        "",
        "essential",
        None,
        "",
        hint=(
            "A folder of elevation files of your own, one per one-degree square, named after that "
            "square as the SRTM format does (N47E011.hgt, or .tif), subfolders included. Each tile "
            "takes its own file from it, whatever its resolution; where the folder has nothing, "
            "the relief chosen above is used. A user of the X-Plane.Org page has the lidar models "
            "of Europe by the hundred and asked to name the folder once (2026-09-19)."
        ),
    )
    fill_nodata: Literal["nearest", "zero"] = _field("nearest", "essential", "fill_nodata", "")

    @model_validator(mode="after")
    def _check_file(self) -> Relief:
        if self.source == "file" and not self.file.strip():
            raise ValueError("relief.source is 'file' but relief.file is empty")
        return self


class Essential(BaseModel):
    """The essential parameters: the questions the Settings screen asks first."""

    model_config = _FROZEN

    provider: str = _field("BI", "essential", "default_website", "", min_length=1)
    zoom_level: int = _field(16, "essential", "default_zl", "ZL", ge=10, le=20)
    airports: AirportCoverage = Field(
        default_factory=AirportCoverage,
        description="Higher-resolution imagery over airports.",
        json_schema_extra={"level": "essential"},
    )
    coast_transition: CoastTransition = Field(
        default_factory=CoastTransition,
        description="Profile and width of the sea masks along the coastline.",
        json_schema_extra={"level": "essential"},
    )
    water_rendering: Literal["XP11 + bathy", "XP12"] = _field(
        "XP11 + bathy", "essential", "water_tech", ""
    )
    relief: Relief = Field(
        default_factory=Relief,
        description="Elevation source: the global DEM, or a local raster file.",
        json_schema_extra={"level": "essential"},
    )
    overlays: Literal["xplane", "none"] = _field(
        "xplane",
        "essential",
        None,
        "",
        hint=(
            "Roads, railways, power lines, forests and buildings over the photo tiles, taken "
            "from X-Plane's own scenery into the shared yOrthoStudio_Overlays pack. 'none' builds "
            "none and takes a tile's own out of X-Plane when it is built again, for simHeaven "
            "X-World or another pack that brings them."
        ),
    )
    photo_look: Literal["as_delivered", "softer", "much_softer", "custom"] = _field(
        "as_delivered",
        "essential",
        None,
        "",
        hint=(
            "Colours of the aerial photos: as the provider delivers them, or toned down. A user "
            "found most of them too bright and too saturated (2026-09-18). Applied when the "
            "textures are encoded, so changing it builds the tile again without downloading "
            "anything. 'custom' uses the three expert values."
        ),
    )
    xplane_dir: str | None = _field(None, "essential", "custom_scenery_dir", "")
    data_dir: str | None = _field(
        None,
        "essential",
        None,
        "",
        hint=(
            "The folder of the tiles OrthoStudio XP builds, of the imagery it downloads and of its "
            "caches, several GB per tile: on an external disk, for instance. Empty: OrthoStudio "
            "XP's own folder (~/.orthostudio). Its disk must hard-link files (APFS, Mac OS "
            "Extended, NTFS, ext4; not exFAT or FAT32). What was downloaded before stays where "
            "it is."
        ),
    )


# -- advanced ------------------------------------------------------------------------------


class Advanced(BaseModel):
    """The 14 advanced parameters."""

    model_config = _FROZEN

    curvature_tol: float = _field(2.0, "advanced", "curvature_tol", "", gt=0)
    limit_tris: float = _field(3.0, "advanced", "limit_tris", "M", ge=0, le=5)
    road_level: Literal[0, 1, 2, 3, 4, 5] = _field(1, "advanced", "road_level", "")
    min_area: float = _field(0.001, "advanced", "min_area", "km²", ge=0)
    max_area: float = _field(200.0, "advanced", "max_area", "km²", ge=0)
    sea_smoothing_mode: Literal["zero", "mean", "none"] = _field(
        "zero", "advanced", "sea_smoothing_mode", ""
    )
    water_smoothing: int = _field(10, "advanced", "water_smoothing", "passes", ge=0)
    apt_smoothing_pix: int = _field(8, "advanced", "apt_smoothing_pix", "px", ge=0)
    ratio_water_pct: float = _field(25.0, "advanced", "ratio_water", "%", ge=0, le=100)
    ratio_bathy: float = _field(1.0, "advanced", "ratio_bathy", "", ge=0, le=1)
    use_masks_for_inland: bool = _field(False, "advanced", "use_masks_for_inland", "")
    imprint_masks_to_dds: bool = _field(True, "advanced", "imprint_masks_to_dds", "")
    terrain_casts_shadows: bool = _field(True, "advanced", "terrain_casts_shadows", "")
    overlay_lod_km: float = _field(25.0, "advanced", "overlay_lod", "km", ge=0)


# -- expert --------------------------------------------------------------------------------


class Expert(BaseModel):
    """The expert parameters, under *For experts* on the Settings screen."""

    model_config = _FROZEN

    mesh_zl: Literal[16, 17, 18, 19, 20] = _field(19, "expert", "mesh_zl", "ZL")
    mask_zl: Literal[14, 15, 16] = _field(14, "expert", "mask_zl", "ZL")
    apt_curv_tol: float = _field(0.5, "expert", "apt_curv_tol", "", gt=0)
    apt_curv_ext: float = _field(0.5, "expert", "apt_curv_ext", "km", ge=0)
    coast_curv_tol: float = _field(1.0, "expert", "coast_curv_tol", "", gt=0)
    coast_curv_ext: float = _field(0.5, "expert", "coast_curv_ext", "km", ge=0)
    min_angle: float = _field(10.0, "expert", "min_angle", "°", ge=0, le=30)
    road_banking_limit: float = _field(0.5, "expert", "road_banking_limit", "m", ge=0)
    lane_width: float = _field(4.0, "expert", "lane_width", "m", gt=0)
    max_levelled_segs: int = _field(200000, "expert", "max_levelled_segs", "segments", ge=0)
    water_simplification: float = _field(0.0, "expert", "water_simplification", "m", ge=0)
    masks_use_dem_too: bool = _field(False, "expert", "masks_use_DEM_too", "")
    masks_custom_extent: str = _field("", "expert", "masks_custom_extent", "")
    distance_masks_too: bool = _field(False, "expert", "distance_masks_too", "")
    sea_texture_blur: float = _field(0.0, "expert", "sea_texture_blur", "m", ge=0)
    normal_map_strength: float = _field(1.0, "expert", "normal_map_strength", "", ge=0, le=1)
    use_decal_on_terrain: bool = _field(False, "expert", "use_decal_on_terrain", "")
    decal_on_sea: bool = _field(
        False,
        "expert",
        None,
        "",
        hint="The decals go on land only. With this on they go on the sea as well, as Ortho4XP "
        "writes them; lakes and rivers never have them.",
    )
    photo_brightness: float = _field(
        0.0,
        "expert",
        None,
        "",
        ge=-0.5,
        le=0.5,
        hint="Brightness of the photo, used when the colours are 'my own values'. 0 leaves it as "
        "delivered, -0.1 takes a tenth of the light away.",
    )
    photo_contrast: float = _field(
        0.0,
        "expert",
        None,
        "",
        ge=-0.5,
        le=0.5,
        hint="Contrast of the photo, used when the colours are 'my own values'. Below 0 the "
        "ground flattens, above 0 the darks and the lights pull apart.",
    )
    photo_saturation: float = _field(
        0.0,
        "expert",
        None,
        "",
        ge=-1.0,
        le=0.5,
        hint="Colour of the photo, used when the colours are 'my own values'. -1 is grey, -0.3 "
        "takes a third of the colour away, 0 leaves it as delivered.",
    )
    patches_dir: str = _field(
        "",
        "expert",
        None,
        "",
        hint=(
            "Folder of hand-made mesh patches: one directory per tile "
            "(<tile>/*.patch.osm, files written with JOSM; patches published for Ortho4XP fit "
            "as they are, with their Patches/<10 degree cell>/<tile> tree). Left empty, the "
            "patches folder of OrthoStudio XP's own folder is used when it exists."
        ),
    )
    ovl_exclude_pol: list[int | str] = _field([0], "expert", "ovl_exclude_pol", "")
    ovl_exclude_net: list[int | str] = _field([], "expert", "ovl_exclude_net", "")


# -- root ----------------------------------------------------------------------------------


class Settings(BaseModel):
    """The whole configuration (``~/.orthostudio/config.toml``)."""

    model_config = _FROZEN

    essential: Essential = Field(default_factory=Essential)
    advanced: Advanced = Field(default_factory=Advanced)
    expert: Expert = Field(default_factory=Expert)
