# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Distances: the ``_dist`` masks and the reach of Pillow's Gaussian blur.

Spec: ``docs/specs/masks-build.md`` section 4. Origin: ``O4_Mask_Utils.py:181-198`` (the
``skfmm`` distance masks used by the XP12 bathymetry cut-off).

``scikit-fmm`` solves the eikonal equation over the signed field ``2 * (pre > 0) - 1``, whose
zero level set sits half a pixel inside the land, in float64, and only inside a narrow band.
``scipy.ndimage.distance_transform_edt`` gives the exact Euclidean distance to the nearest
land *pixel*, in float32, everywhere: subtracting half a pixel makes the two comparable.
"""

from __future__ import annotations

import functools
import math

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageFilter
from scipy import ndimage

__all__ = [
    "DEFAULT_EDT_OFFSET_PX",
    "distance_mask",
    "edt_px",
    "pil_blur_reach",
    "pil_blur_support",
    "pil_box_radius",
]

DEFAULT_EDT_OFFSET_PX = 0.5
"""Half a pixel: the offset between the ``skfmm`` zero level set and the nearest land pixel."""


def edt_px(inside: NDArray[np.bool_]) -> NDArray[np.float32]:
    """Euclidean distance, in pixels, from every ``True`` cell to the nearest ``False`` one.

    The float64 field is written into a caller-owned array (``distances=``) instead of being
    returned, which saves one full-size allocation per cell; the peak of the transform itself
    is still dominated by SciPy's internal index arrays (measured 814 MB of RSS for one
    4230 x 4230 cell, ``masks-build.md`` 4), which is what the node budgets for.
    """
    out = np.empty(inside.shape, dtype=np.float64)
    ndimage.distance_transform_edt(inside, distances=out)
    return out.astype(np.float32)


def pil_box_radius(sigma: float, passes: int = 3) -> float:
    """Pillow's box radius for a Gaussian of standard deviation ``sigma``.

    ``ImagingGaussianBlur`` (``src/libImaging/BoxBlur.c``) approximates the Gaussian with
    ``passes`` box blurs whose radius comes from Gwosdek et al., equations 7, 11 and 14.
    """
    if sigma <= 0:
        return 0.0
    sigma2 = sigma * sigma / passes
    length = math.sqrt(12.0 * sigma2 + 1.0)
    whole = math.floor((length - 1.0) / 2.0)
    frac = (2 * whole + 1) * (whole * (whole + 1) - 3 * sigma2)
    frac /= 6 * (sigma2 - (whole + 1) * (whole + 1))
    return whole + frac


def pil_blur_support(sigma: float, passes: int = 3) -> int:
    """How far one ``GaussianBlur(sigma)`` can move a non-zero value, in pixels.

    Three box passes of radius ``r`` reach ``3 * ceil(r)``; this is the *exact* support, so a
    working image cropped to this halo gives the same interior bytes as the full one.
    """
    return passes * math.ceil(pil_box_radius(sigma, passes))


@functools.lru_cache(maxsize=64)
def pil_blur_reach(sigma: float) -> int:
    """How far ``GaussianBlur(sigma) > 0`` grows a straight edge, in pixels.

    Pillow approximates a Gaussian with three box passes and quantises to 8 bits between
    them, so the reach of the ``> 0`` threshold Ortho4XP uses as a dilation is neither the
    mathematical support nor a simple multiple of ``sigma``; it is probed once per radius on
    a synthetic half-plane (measured: 3 px at ``sigma = 1``, 25 px at ``sigma = 9.6``).
    """
    if sigma <= 0:
        return 0
    width = max(64, int(16 * sigma) + 8)
    probe = np.zeros((3, 2 * width), dtype=np.uint8)
    probe[:, :width] = 255
    blurred = np.array(
        Image.fromarray(probe).filter(ImageFilter.GaussianBlur(sigma)), dtype=np.uint8
    )
    lit = np.flatnonzero(blurred[1] > 0)
    return int(lit.max() - (width - 1)) if lit.size else 0


def distance_mask(
    pre: NDArray[np.uint8],
    *,
    mask_zl: int,
    margin: int,
    offset_px: float = DEFAULT_EDT_OFFSET_PX,
) -> NDArray[np.uint8]:
    """The ``<y>_<x>_dist.png`` of one cell (``O4_Mask_Utils.py:181-198``).

    0 on land, then the distance to the shore in units of ``2 ** (16 - mask_zl)`` pixels,
    saturating at 255 (which is what Ortho4XP's narrow band and its ``-99999`` fill produce
    beyond the band).
    """
    scale = 2 ** (16 - mask_zl)
    distance = edt_px(pre == 0) - np.float32(offset_px)
    np.maximum(distance, 0, out=distance)
    distance *= scale
    np.minimum(distance, 255, out=distance)
    side = pre.shape[0] - 2 * margin
    cropped = distance[margin : margin + side, margin : margin + side]
    return cropped.astype(np.uint8)
