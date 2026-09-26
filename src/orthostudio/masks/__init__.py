# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Water transparency masks: step 2.5 of Ortho4XP, natively.

Specification: ``docs/specs/masks-build.md``. The consumer side (which texture needs a mask
and how a mask becomes an alpha channel) is ``orthostudio.textures.imprint``.
"""

from orthostudio.masks.build import (
    MASKS_INDEX_FORMAT,
    CellJob,
    CellResult,
    MasksIndex,
    build_cell,
    build_masks,
    mask_file_name,
    worker_count,
)
from orthostudio.masks.dem import MASK_ALTITUDE_ABOVE, dem_pre_mask, mesh_warp
from orthostudio.masks.distance import distance_mask, edt_px, pil_blur_reach
from orthostudio.masks.profiles import (
    MASKING_MODES,
    WATER_TRANSITION,
    MaskingMode,
    MasksWidth,
    blur_mask,
    blur_widths,
    halo_px,
    rocks_blur,
    sand_blur,
    sea_level_for,
    three_steps_blur,
)
from orthostudio.masks.raster import (
    CELL_PX,
    FULL_MARGIN_PX,
    custom_pre_mask,
    extent_polygons,
    pre_mask,
)
from orthostudio.masks.rule import MASKS, MasksParams, read_mesh_artifact
from orthostudio.masks.water import (
    NEIGHBOUR_OFFSETS,
    MaskRange,
    WaterTriangles,
    cell_pixel_origin,
    mask_cells,
    water_triangles,
)

__all__ = [
    "CELL_PX",
    "FULL_MARGIN_PX",
    "MASKING_MODES",
    "MASKS",
    "MASKS_INDEX_FORMAT",
    "MASK_ALTITUDE_ABOVE",
    "NEIGHBOUR_OFFSETS",
    "WATER_TRANSITION",
    "CellJob",
    "CellResult",
    "MaskRange",
    "MaskingMode",
    "MasksIndex",
    "MasksParams",
    "MasksWidth",
    "WaterTriangles",
    "blur_mask",
    "blur_widths",
    "build_cell",
    "build_masks",
    "cell_pixel_origin",
    "custom_pre_mask",
    "dem_pre_mask",
    "distance_mask",
    "edt_px",
    "extent_polygons",
    "halo_px",
    "mask_cells",
    "mask_file_name",
    "mesh_warp",
    "pil_blur_reach",
    "pre_mask",
    "read_mesh_artifact",
    "rocks_blur",
    "sand_blur",
    "sea_level_for",
    "three_steps_blur",
    "water_triangles",
    "worker_count",
]
