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

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

__all__ = ["LUMA", "adjust_photo", "adjust_photo_inside", "photo_unchanged"]

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


def adjust_photo_inside(
    rgb: NDArray[np.uint8],
    ring: Sequence[float],
    *,
    brightness: float = 0.0,
    contrast: float = 0.0,
    saturation: float = 0.0,
    feather_px: int = 24,
) -> NDArray[np.uint8]:
    """``rgb`` with the three adjustments applied **inside** ``ring`` only, with a soft edge.

    ``ring`` is ``x0, y0, x1, y1, ...`` in this image's own pixels. A zone of the page used to
    colour whole texture files, the one holding its centre, so a zone smaller than a texture
    (6.4 km at ZL16) changed nothing at all and said nothing either (a user, 2026-09-23). The
    colours are a calculation on the pixels, so nothing stops them from following the shape that
    was drawn.

    The edge is blurred over ``feather_px`` (about 40 m at ZL16 in mid-latitudes) so the join does
    not show, and only the rectangle the ring covers is touched, so a small zone costs little on a
    4096² texture.
    """
    if photo_unchanged(brightness, contrast, saturation) or len(ring) < 6:
        return rgb
    from PIL import Image, ImageDraw, ImageFilter

    height, width = rgb.shape[0], rgb.shape[1]
    xs = [float(ring[i]) for i in range(0, len(ring) - 1, 2)]
    ys = [float(ring[i]) for i in range(1, len(ring), 2)]
    pad = max(feather_px, 1) * 2
    x0 = max(0, int(min(xs)) - pad)
    y0 = max(0, int(min(ys)) - pad)
    x1 = min(width, int(max(xs)) + pad + 1)
    y1 = min(height, int(max(ys)) + pad + 1)
    if x1 <= x0 or y1 <= y0:  # the zone does not reach this texture
        return rgb

    stencil = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(stencil).polygon(
        [(x - x0, y - y0) for x, y in zip(xs, ys, strict=True)], fill=255
    )
    if feather_px > 0:
        stencil = stencil.filter(ImageFilter.GaussianBlur(feather_px / 2))
    weight = np.asarray(stencil, dtype=np.float32)[..., None] / 255.0
    if not weight.any():
        return rgb

    out = rgb.copy()
    window = out[y0:y1, x0:x1]
    changed = adjust_photo(window, brightness=brightness, contrast=contrast, saturation=saturation)
    blended = window.astype(np.float32) * (1.0 - weight) + changed.astype(np.float32) * weight
    np.rint(blended, out=blended)
    np.clip(blended, 0.0, 255.0, out=blended)
    out[y0:y1, x0:x1] = blended.astype(np.uint8)
    return out
