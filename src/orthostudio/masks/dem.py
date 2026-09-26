# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The elevation part of a pre-mask (``masks_use_DEM_too``), as a pure function.

Spec: ``docs/specs/masks-build.md`` sections 2 and 8. Origin: ``O4_Mask_Utils.py:330-368``
(``build_dem_pre_mask``) and ``O4_Imagery_Utils.py:2043-2081`` (``gdalwarp_alternative``).

``orthostudio.sources.dem`` does not exist yet, so this module never opens an elevation file: it
takes the boolean "above ``mask_altitude_above``" array and the WGS84 box it covers, and
returns the 255/0 array the rasteriser takes the maximum with. **TODO(P3-dem)**: the rule
will pass the array read from the ``dem`` input artefact once that rule exists.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageFilter
from pyproj import CRS, Transformer

from orthostudio.masks.raster import CELL_PX
from orthostudio.masks.water import cell_pixel_origin

__all__ = ["MASK_ALTITUDE_ABOVE", "dem_pre_mask", "mesh_warp", "pix_to_wgs84"]

MASK_ALTITUDE_ABOVE = 0.5
"""Metres above which the DEM counts as land (``O4_Mask_Utils.py:19``)."""

_WEBM = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(3857), always_xy=True)


def pix_to_wgs84(pix_x: float, pix_y: float, zl: int) -> tuple[float, float]:
    """``(lat, lon)`` of an absolute web-mercator pixel (``O4_Geo_Utils.py:97-104``)."""
    rat_x = pix_x / (2 ** (zl + 7)) - 1
    rat_y = 1 - pix_y / (2 ** (zl + 7))
    return (360 / math.pi * math.atan(math.exp(math.pi * rat_y)) - 90, rat_x * 180)


def mesh_warp(
    source: Image.Image,
    s_bbox: tuple[float, float, float, float],
    s_epsg: int,
    t_bbox: tuple[float, float, float, float],
    t_epsg: int,
    t_size: tuple[int, int],
) -> Image.Image:
    """Ortho4XP's ``gdalwarp_alternative``: an 8x8 mesh transform between two projections.

    Ported verbatim from ``O4_Imagery_Utils.py:2043-2081`` (bounding boxes are
    ``(ulx, uly, lrx, lry)``).
    """
    s_ulx, s_uly, s_lrx, s_lry = s_bbox
    t_ulx, t_uly, t_lrx, t_lry = t_bbox
    s_w, s_h = source.size
    t_w, t_h = t_size
    inv = Transformer.from_crs(CRS.from_epsg(t_epsg), CRS.from_epsg(s_epsg), always_xy=True)
    meshes: list[tuple[tuple[int, int, int, int], list[int]]] = []
    steps = 8
    x_step = t_w / float(steps)
    y_step = t_h / float(steps)
    y = 0.0
    for _ in range(steps):
        x = 0.0
        for _ in range(steps):
            quad = (int(x), int(y), int(x + x_step), int(y + y_step))
            s_quad: list[int] = []
            for t_pixx, t_pixy in (
                (quad[0], quad[1]),
                (quad[0], quad[3]),
                (quad[2], quad[3]),
                (quad[2], quad[1]),
            ):
                t_x = t_ulx + t_pixx / t_w * (t_lrx - t_ulx)
                t_y = t_uly - t_pixy / t_h * (t_uly - t_lry)
                s_x, s_y = inv.transform(t_x, t_y)
                s_quad.append(round((s_x - s_ulx) / (s_lrx - s_ulx) * s_w))
                s_quad.append(round((s_uly - s_y) / (s_uly - s_lry) * s_h))
            meshes.append((quad, s_quad))
            x += x_step
        y += y_step
    return source.transform(t_size, Image.Transform.MESH, meshes, Image.Resampling.BICUBIC)


def dem_pre_mask(
    above: NDArray[np.bool_],
    dem_bbox: tuple[float, float, float, float],
    til_x: int,
    til_y: int,
    *,
    mask_zl: int,
    margin: int,
) -> NDArray[np.uint8]:
    """255 where the elevation is above sea level, on the cell's working grid.

    ``above`` is the super level set of the DEM at :data:`MASK_ALTITUDE_ABOVE` metres and
    ``dem_bbox`` is ``(lon_min, lon_max, lat_min, lat_max)`` of that array, exactly what
    Ortho4XP's ``DEM.super_level_set`` returns (``O4_DEM_Utils.py:209-235``).
    """
    side = CELL_PX + 2 * margin
    if not above.any():
        return np.zeros((side, side), dtype=np.uint8)
    px0, py0 = cell_pixel_origin(til_x, til_y, mask_zl, margin)
    lat_max, lon_min_t = pix_to_wgs84(px0, py0, mask_zl)
    lat_min, lon_max_t = pix_to_wgs84(px0 + side, py0 + side, mask_zl)
    del lon_min_t, lon_max_t
    lon_min, lon_max, lat_min_s, lat_max_s = dem_bbox
    x0, y0 = _WEBM.transform(lon_min, lat_max_s)
    x1, y1 = _WEBM.transform(lon_max, lat_min_s)
    del lat_min, lat_max
    source = Image.fromarray(above.astype(np.uint8) * 255)
    warped = mesh_warp(
        source,
        (lon_min, lat_max_s, lon_max, lat_min_s),
        4326,
        (x0, y0, x1, y1),
        3857,
        (side, side),
    )
    warped = warped.filter(ImageFilter.GaussianBlur(0.3 * 2 ** (mask_zl - 14)))
    return (np.array(warped, dtype=np.uint8) > 0).astype(np.uint8) * 255
