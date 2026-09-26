# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Which mip level X-Plane reads of an ortho texture at a given distance, as the probe measured it.

First probe flight, 2026-09-26 (``docs/specs/live/probe-flight-1.md``): X-Plane aims at a ground
resolution proportional to distance, about 0.18 m per pixel per km. A ZL16 texture, about 7 km
across, is read at level 3 (512 px) from 72 km, level 2 from 38 km, level 1 from 21 km and level 0
(4096 px) under 12 km, the last only with texture quality at Maximum; a ZL18 texture follows the
same law four times closer. At DSF load, 130-230 km away, X-Plane reads level 4 (256 px) only.
"""

from __future__ import annotations

LOAD_LEVEL = 4  # the level X-Plane reads when it loads a tile
RINGS_ZL16_KM = {3: 72.0, 2: 38.0, 1: 21.0, 0: 12.0}
DEFAULT_MARGIN = 1.25  # build for a ring a quarter wider than measured


def ring_km(level: int, zoom: int) -> float:
    """Distance within which X-Plane reads ``level`` of a texture of ``zoom``."""
    return RINGS_ZL16_KM[level] * 2.0 ** (16 - zoom)


def top_level_for(
    distance_km: float, zoom: int, finest: int, margin: float = DEFAULT_MARGIN
) -> int:
    """The sharpest level X-Plane will read of a texture of ``zoom`` whose centre comes within
    ``distance_km`` of the camera, never sharper than ``finest``."""
    for level in range(max(finest, 0), LOAD_LEVEL):
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


def finest_for_zoom_cap(zoom: int, max_zoom: int) -> int:
    """The sharpest level worth building when imagery stops at ``max_zoom`` (AutoOrtho's setting):
    level k of a texture of ``zoom`` is imagery at ``zoom - k``."""
    return max(0, min(LOAD_LEVEL, zoom - max_zoom))
