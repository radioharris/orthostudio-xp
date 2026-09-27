# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Which mip level X-Plane reads of an ortho texture at a given distance, as the probe measured it.

First probe flight, 2026-09-26 (``docs/specs/live/probe-flight-1.md``): X-Plane aims at a ground
resolution proportional to distance, about 0.18 m per pixel per km. A ZL16 texture, about 7 km
across, is read at level 3 (512 px) from 72 km, level 2 from 38 km, level 1 from 21 km and level 0
(4096 px) under 12 km, the last only with texture quality at Maximum; a ZL18 texture follows the
same law four times closer. At DSF load, 130-230 km away, X-Plane reads level 4 (256 px) only.

A level is the same ground resolution whatever the file's size (level 1 is 2048 px of the texture's
area), so the rings hold for a texture made smaller or larger by the zoom set, which only moves the
sharpest level there is to read (:func:`file_top`). Level -1, 8192 px, is read under 6 km: the law
carried one level on, not measured.
"""

from __future__ import annotations

LOAD_LEVEL = 4  # the level X-Plane reads when it loads a tile
MIN_LEVEL = -1  # 8192 px: one zoom above the mesh's, the most AutoOrtho builds
RINGS_ZL16_KM = {3: 72.0, 2: 38.0, 1: 21.0, 0: 12.0, -1: 6.0}
DEFAULT_MARGIN = 1.25  # build for a ring a quarter wider than measured


def ring_km(level: int, zoom: int) -> float:
    """Distance within which X-Plane reads ``level`` of a texture of ``zoom``."""
    return RINGS_ZL16_KM[level] * 2.0 ** (16 - zoom)


def top_level_for(
    distance_km: float, zoom: int, finest: int, margin: float = DEFAULT_MARGIN
) -> int:
    """The sharpest level X-Plane will read of a texture of ``zoom`` whose centre comes within
    ``distance_km`` of the camera, never sharper than ``finest``."""
    for level in range(max(finest, MIN_LEVEL), LOAD_LEVEL):
        if distance_km <= ring_km(level, zoom) * margin:
            return level
    return LOAD_LEVEL


def finest_for_quality(tex_res: int | None) -> int:
    """The sharpest level X-Plane reads at its texture quality preference (``_tex_res``).

    Measured: 5 (Maximum) reads level 0, 4 stops at level 1. Each notch below is assumed to drop one
    more level. Unknown: level 0, which builds the most and is never wrong.
    """
    if tex_res is None:
        return 0
    return max(0, min(LOAD_LEVEL, 5 - tex_res))


def zoom_for(zoom: int, max_zoom: int, max_zoom_airports: int) -> int:
    """The zoom set for a texture of ``zoom``: AutoOrtho's rule (getortho.py
    ``_get_target_zoom_level``), the one near airports for the mesh's ZL18 textures, which are its
    airports' surroundings, the maximum zoom for the others."""
    return max_zoom_airports if zoom == 18 else max_zoom


def file_top(zoom: int, max_zoom: int) -> int:
    """The sharpest level of a texture of ``zoom`` whose imagery goes to ``max_zoom``: level k is
    imagery at ``zoom - k``. As AutoOrtho sizes it, below the mesh's zoom it is a smaller texture
    (level 1: 2048 px) and above it an 8192 px one, one zoom above at most (level -1)."""
    return max(MIN_LEVEL, min(LOAD_LEVEL, zoom - max_zoom))


def finest_readable(top: int, quality: int) -> int:
    """The sharpest level X-Plane reads of a texture whose sharpest is ``top``, at its texture
    quality (:func:`finest_for_quality`, 0 at Maximum). Whether the quality drops levels from the
    file's own size or from 4096 px was not measured: the sharper of the two is built, so that
    nothing X-Plane reads is left neutral."""
    return min(top + quality, max(top, quality))
