# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
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
    exact_edges: bool = False,
) -> NDArray[np.uint8]:
    """The three-level pre-mask of one cell: 255 land, ``sea_level`` inland water, 0 sea.

    ``extents`` are the polygons of :func:`extent_polygons`; ``tris`` the triangles this cell
    must draw. The returned array is ``(CELL_PX + 2 * margin)`` square. ``exact_edges``: the
    water as its outline covers the pixels (:func:`_without_rim`), for OrthoStudio XP's own
    fade; Ortho4XP's keep Ortho4XP's drawing, byte for byte.
    """
    px0, py0 = cell_pixel_origin(til_x, til_y, mask_zl, margin)
    side = CELL_PX + 2 * margin
    image = Image.new("L", (side, side), "black")
    draw = ImageDraw.Draw(image)
    for polygon in extents:
        draw.polygon(_shift(polygon, px0, py0), fill="white")
    land = np.asarray(image) == 255 if exact_edges else None
    for corners, fill in ((tris.inland, sea_level), (tris.sea, 0)):
        if corners.shape[0] == 0:
            continue
        flat = (corners - np.array([px0, py0], dtype=np.float64)).reshape(-1, 6).tolist()
        for triangle in flat:
            draw.polygon(triangle, fill=fill)
    del draw
    out = np.asarray(image, dtype=np.uint8)
    return out if land is None else _without_rim(out, land)


def _without_rim(pre: NDArray[np.uint8], land: NDArray[np.bool_]) -> NDArray[np.uint8]:
    """``pre`` with the row of pixels Pillow adds east and south of the water given back to the
    land under it. The water's corners come rounded to whole pixels (``water.wgs84_to_pix``, as
    Ortho4XP's), and Pillow fills a triangle up to and with its right and bottom edges: every
    surface of water came one pixel wider to the east and the south, about 6 m at ``mask_zl`` 14.
    The fade then began inside the water on those banks, a sharp line where the north and west
    ones faded (a river by XC00DD, 2026-11-18: 151-164 of 255 at the bank against 227-240). A
    water pixel goes back to land where land lies east of it and water west of it, or land south
    and water north: the rim alone, never a stream one pixel wide."""
    water = (pre != 255) & land
    rim = np.zeros_like(water)
    rim[:, 1:-1] |= water[:, 1:-1] & ~water[:, 2:] & water[:, :-2]
    rim[1:-1, :] |= water[1:-1, :] & ~water[2:, :] & water[:-2, :]
    out = pre.copy()
    out[rim] = 255
    return out


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
