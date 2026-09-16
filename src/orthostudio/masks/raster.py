"""Rasterisation of the water pre-mask of one cell.

Spec: ``docs/specs/masks-build.md`` section 2 (``build_water_pre_mask``,
``O4_Mask_Utils.py:260-328``). The drawing is Pillow's ``ImageDraw.polygon`` with the very
same float pixel coordinates Ortho4XP passes it: that is what makes byte identity reachable.

Ortho4XP always works on a 6144 x 6144 image (a 4096 px cell plus a 1024 px margin on each side).
OrthoStudio XP keeps that image, except that the margin may be narrowed to what the chosen profile
can reach (:func:`orthostudio.masks.profiles.halo_px`); the drawing itself is unchanged, Pillow
clips the polygons to the image.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw

from orthostudio.masks.water import WaterTriangles, cell_pixel_origin, tile_pixel_corners
from orthostudio.model import TileRef

__all__ = [
    "CELL_PX",
    "FULL_MARGIN_PX",
    "extent_polygons",
    "pre_mask",
]

CELL_PX = 4096
"""Side of a mask, in pixels (16 tiles of 256 px)."""

FULL_MARGIN_PX = 1024
"""Ortho4XP's working margin on each side (``O4_Mask_Utils.py:267``)."""


def extent_polygons(tiles: Iterable[TileRef], mask_zl: int) -> list[list[tuple[int, int]]]:
    """The white quadrilateral of every tile whose mesh is available, in absolute pixels."""
    return [tile_pixel_corners(t, mask_zl) for t in tiles]


def _shift(polygon: Sequence[tuple[int, int]], px0: int, py0: int) -> list[float]:
    out: list[float] = []
    for x, y in polygon:
        out.append(x - px0)
        out.append(y - py0)
    return out


def pre_mask(
    til_x: int,
    til_y: int,
    tris: WaterTriangles,
    extents: Sequence[Sequence[tuple[int, int]]],
    *,
    mask_zl: int,
    sea_level: int,
    margin: int = FULL_MARGIN_PX,
) -> NDArray[np.uint8]:
    """The three-level pre-mask of one cell: 255 land, ``sea_level`` inland water, 0 sea.

    ``extents`` are the polygons of :func:`extent_polygons`; ``tris`` the triangles this cell
    must draw. The returned array is ``(CELL_PX + 2 * margin)`` square.
    """
    px0, py0 = cell_pixel_origin(til_x, til_y, mask_zl, margin)
    side = CELL_PX + 2 * margin
    image = Image.new("L", (side, side), "black")
    draw = ImageDraw.Draw(image)
    for polygon in extents:
        draw.polygon(_shift(polygon, px0, py0), fill="white")
    for corners, fill in ((tris.inland, sea_level), (tris.sea, 0)):
        if corners.shape[0] == 0:
            continue
        flat = (corners - np.array([px0, py0], dtype=np.float64)).reshape(-1, 6).tolist()
        for triangle in flat:
            draw.polygon(triangle, fill=fill)
    del draw
    return np.asarray(image, dtype=np.uint8)


def custom_pre_mask(coverage: NDArray[np.uint8], sea_level: int) -> NDArray[np.uint8]:
    """The custom-extent contribution (``O4_Mask_Utils.py:370-392``), ``NameError`` fixed.

    ``coverage`` is the extent rasterised to 4096 x 4096 by the imagery layer (Ortho4XP's
    ``IMG.has_data(..., mask_size=(4096, 4096), is_sharp_resize=False)``); the array is
    scaled to the inland-water grey. Ortho4XP computes this array, stores it in ``custom_array``
    and then reads an undefined ``custom_mask`` (``:170``), so no Ortho4XP tile with
    ``masks_custom_extent`` has ever reached that line without crashing.
    """
    if coverage.shape != (CELL_PX, CELL_PX):
        raise ValueError(f"custom extent must be {CELL_PX}x{CELL_PX}, got {coverage.shape}")
    return (coverage.astype(np.float64) * (sea_level / 255)).astype(np.uint8)
