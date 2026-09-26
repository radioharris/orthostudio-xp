# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
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
    source: NDArray[np.uint8] | None = None,
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

    ``source`` is the image the zone's colours are read from, the photograph as it was delivered,
    where ``rgb`` already carries the square's own. A zone **replaces** what the square asked for,
    it does not add to it: that is what the page paints and what ``map-zones.md`` says, and adding
    them meant a zone set to "as delivered" inside a softened square changed nothing at all, which
    is the very complaint the zones were made for (2026-09-23).
    """
    same = source is None or source is rgb
    if (same and photo_unchanged(brightness, contrast, saturation)) or len(ring) < 6:
        return rgb
    from PIL import Image, ImageDraw, ImageFilter

    height, width = rgb.shape[0], rgb.shape[1]
    xs = [float(ring[i]) for i in range(0, len(ring) - 1, 2)]
    ys = [float(ring[i]) for i in range(1, len(ring), 2)]
    pad = max(feather_px, 1) * 2
    # The soft edge is drawn on a stencil that reaches ``pad`` **past** the texture, and only
    # then cut to the texture. Cut first, the part of the edge belonging to the texture next door
    # was lost and the blur held the near side at full strength: a zone whose edge ran within a
    # feather's width of a join showed the whole step in one pixel, at exactly the join the soft
    # edge exists to hide (found in review, 2026-09-23). ``pad`` bounds the stencil, so a zone far
    # larger than the texture costs no more than one a little larger than it.
    fx0 = max(-pad, int(min(xs)) - pad)
    fy0 = max(-pad, int(min(ys)) - pad)
    fx1 = min(width + pad, int(max(xs)) + pad + 1)
    fy1 = min(height + pad, int(max(ys)) + pad + 1)
    x0, y0 = max(0, fx0), max(0, fy0)
    x1, y1 = min(width, fx1), min(height, fy1)
    if x1 <= x0 or y1 <= y0:  # the zone does not reach this texture
        return rgb

    stencil = Image.new("L", (fx1 - fx0, fy1 - fy0), 0)
    ImageDraw.Draw(stencil).polygon(
        [(x - fx0, y - fy0) for x, y in zip(xs, ys, strict=True)], fill=255
    )
    if feather_px > 0:
        stencil = stencil.filter(ImageFilter.GaussianBlur(feather_px / 2))
    mask = np.asarray(stencil, dtype=np.uint8)[y0 - fy0 : y1 - fy0, x0 - fx0 : x1 - fx0]
    if not mask.any():
        return rgb

    out = rgb.copy()
    window = out[y0:y1, x0:x1]
    base = window if same else source[y0:y1, x0:x1]
    # a band at a time, as :func:`adjust_photo` does: a zone covering a whole 4096² texture held
    # 770 MB in one go where the rule declares 250, and several textures encode at once, so a
    # machine with little memory lost the build (found in review, 2026-09-23)
    for start in range(0, window.shape[0], _ROWS):
        stop = start + _ROWS
        weight = mask[start:stop, :, None].astype(np.float32) / 255.0
        changed = adjust_photo(
            base[start:stop], brightness=brightness, contrast=contrast, saturation=saturation
        )
        band = window[start:stop].astype(np.float32)
        band *= 1.0 - weight
        band += changed.astype(np.float32) * weight
        np.rint(band, out=band)
        np.clip(band, 0.0, 255.0, out=band)
        window[start:stop] = band.astype(np.uint8)
    return out
