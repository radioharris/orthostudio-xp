# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Native elevation stage: sources, raster assembly, altitude queries, rule ``orthostudio.dem@1``.

Spec: ``docs/specs/dem.md``. Replaces the elevation half of Ortho4XP's step 1
(``O4_DEM_Utils.py``). Nothing here imports the Ortho4XP sources.
"""

from __future__ import annotations

from orthostudio.dem.dem import Dem, alt_file_name, expected_alt_size, resolve_source
from orthostudio.dem.raster import (
    CombinedRaster,
    FillNodata,
    RasterRead,
    Region,
    build_combined_raster,
    fill_nodata_nearest,
    nodata_to_zero,
    read_elevation_from_file,
    smooth_over_regions,
    smoothen,
    upsample_1201_to_3601,
    world_tiles,
)
from orthostudio.dem.rule import DEM_RULE, DemJob, DemParams, build_dem, dem_job
from orthostudio.dem.sources import (
    CellState,
    Download,
    EnsureOptions,
    EnsureResult,
    NegativeMemo,
    Source,
    elevation_path,
    ensure_elevation,
    extract_view_zip,
    hem_latlon,
    view_url,
)

__all__ = [
    "DEM_RULE",
    "CellState",
    "CombinedRaster",
    "Dem",
    "DemJob",
    "DemParams",
    "Download",
    "EnsureOptions",
    "EnsureResult",
    "FillNodata",
    "NegativeMemo",
    "RasterRead",
    "Region",
    "Source",
    "alt_file_name",
    "build_combined_raster",
    "build_dem",
    "dem_job",
    "elevation_path",
    "ensure_elevation",
    "expected_alt_size",
    "extract_view_zip",
    "fill_nodata_nearest",
    "hem_latlon",
    "nodata_to_zero",
    "read_elevation_from_file",
    "resolve_source",
    "smooth_over_regions",
    "smoothen",
    "upsample_1201_to_3601",
    "view_url",
    "world_tiles",
]
