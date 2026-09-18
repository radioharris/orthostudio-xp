"""Colours of the photo: brightness, contrast and saturation, before the DDS encoding.

A user of the X-Plane.Org page edited his screenshots by hand because "most of the sat images
are either to bright or to saturated" (2026-09-18). The adjustment belongs here, at the last
step that reads the assembled image: the downloaded pieces are untouched, so changing your mind
re-encodes the tile without downloading anything again.

Ortho4XP does the same with its ``Filters/*.flt`` files (brightness-contrast, saturation, levels,
sharpness, blur), which one source of its registry used. OrthoStudio XP keeps the three that
change what a pilot sees, in the same order: brightness, then contrast, then saturation.

Each value is a *deviation*: ``0.0`` changes nothing, ``-0.3`` takes 30 % away, ``0.2`` adds
20 %. :func:`photo_unchanged` says when the three of them leave the image alone, which is what
keeps the artefact key of every tile built before this existed (``TextureDdsParams.canonical``).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

__all__ = ["LUMA", "adjust_photo", "photo_unchanged"]

LUMA: tuple[float, float, float] = (0.299, 0.587, 0.114)
"""ITU-R BT.601 grey, the one Pillow's ``ImageEnhance.Color`` and Ortho4XP's filters use."""

_ROWS = 512
"""Rows converted to float at a time: a 4096x4096 texture in one go would be 200 MB."""


def photo_unchanged(brightness: float, contrast: float, saturation: float) -> bool:
    """Whether these three leave every pixel as it is."""
    return brightness == 0.0 and contrast == 0.0 and saturation == 0.0


def adjust_photo(
    rgb: NDArray[np.uint8],
    *,
    brightness: float = 0.0,
    contrast: float = 0.0,
    saturation: float = 0.0,
) -> NDArray[np.uint8]:
    """``rgb`` (H, W, 3 uint8) with the three adjustments applied, or itself when they are zero.

    Brightness scales every channel, contrast pushes it away from mid-grey, saturation away from
    the pixel's own grey. Values are clipped to 0-255, so a strong setting flattens the extremes
    rather than wrapping them.
    """
    if photo_unchanged(brightness, contrast, saturation):
        return rgb
    out = np.empty_like(rgb)
    for start in range(0, rgb.shape[0], _ROWS):
        block = rgb[start : start + _ROWS].astype(np.float32)
        if brightness:
            block *= 1.0 + brightness
        if contrast:
            block = (block - 127.5) * (1.0 + contrast) + 127.5
        if saturation:
            grey = block @ np.asarray(LUMA, dtype=np.float32)
            block = grey[..., None] + (block - grey[..., None]) * (1.0 + saturation)
        # rounded, not truncated: truncation alone would darken every pixel by half a step
        np.rint(block, out=block)
        np.clip(block, 0.0, 255.0, out=block)
        out[start : start + _ROWS] = block.astype(np.uint8)
    return out
