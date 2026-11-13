# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Which mip level X-Plane reads of an ortho texture at a given distance, as the probe measured it.

First probe flight, 2026-09-26 (``docs/specs/live/probe-flight-1.md``): X-Plane aims at a ground
resolution proportional to distance, about 0.18 m per pixel per km. A ZL16 texture, about 7 km
across, was read at level 3 (512 px) from 72 km, level 2 from 38 km, level 1 from 21 km and level 0
(4096 px) under 12 km, the last only with texture quality at Maximum; a ZL18 texture follows the
same law four times closer. At DSF load, 130-230 km away, X-Plane reads level 4 (256 px) only.

Those rings were 12 % short (a start at KOWB, 2026-10-22, ``docs/specs/live/levels-test.md``):
X-Plane, loading the flight, opened for more than 256 px every ZL16 texture whose centre was within
80.8 km of the aircraft and none beyond, every ZL18 one within 20.2 km, against 72 and 18; and the
textures it showed neutral, Live having built them a level coarser, lay at 21.4 to 21.6 km (2048 px)
and 40.6 to 42.0 km (1024 px), past 21 and 38. Each ring is the measured one times 1.13, the
farthest texture opened sharper being at 80.9 km (72 x 1.124).

X-Plane counts a file's levels from its top, whatever the file's size (an fs_usage trace in flight,
2026-09-28): an 8192 px file read whole at 12 km, its 4096 px level from 21 km. So the rings are
those of a 4096 px file's levels. The photos of each level are built by them, whatever the zoom set
(:func:`file_top`): a smaller file is read later than that, which is safe, and an 8192 px one a ring
earlier, where it finds the photos already, built farther for the camera's reach, or a copy of the
level below (orthostudio.live.texture). Its 8192 px level, read where a 4096 px file's 4096 px one
is, is the streamer's own rule.

The rings were measured on one image, 2704 px wide with a 65° field of view. Probe flight 2
(``docs/specs/live/probe-flight-2.md``) found X-Plane reading each level farther on a wider image
and with a narrower field of view, as the width of a pixel on the ground goes: :func:`screen_scale`.
"""

from __future__ import annotations

import math

LOAD_LEVEL = 4  # the level X-Plane reads when it loads a tile
MIN_LEVEL = -1  # 8192 px: one zoom above the mesh's, the most AutoOrtho builds
AIRPORT_ZOOM = 18  # the mesh's textures round its airports (AutoOrtho's ZL18 within 0.5 km)
RINGS_ZL16_KM = {3: 81.4, 2: 42.9, 1: 23.7, 0: 13.6}  # probe flight 1 x 1.13 (2026-10-22)
# rings built this much wider than X-Plane reads them: in flight to keep ahead of the aircraft
# (1.25 on the probe flight's rings until they were found 12 % short, the same distances since),
# and at a start since 2026-11-11 and standing since 2026-11-12, X-Plane's 4096 px ring lying
# past ring 0 (13.9 km near LAX)
DEFAULT_MARGIN = 1.11
REFERENCE_WIDTH_PX = 2704  # X-Plane's image when the rings were measured
REFERENCE_FOV_DEG = 65.0
MAX_SCALE = 4.0  # a field of view under about 17° (binoculars): its far rings are left to chance


def ring_km(level: int, zoom: int) -> float:
    """Distance within which X-Plane reads ``level`` of a texture of ``zoom``."""
    return RINGS_ZL16_KM[level] * 2.0 ** (16 - zoom)


def screen_scale(width_px: float | None, fov_deg: float | None) -> float:
    """How much farther than the rings X-Plane reads each level on an image ``width_px`` wide with
    a horizontal field of view of ``fov_deg``: as the ground a pixel covers shrinks (probe flight 2:
    x1.35 to x1.67 for an image 1.71 times wider, x1.83 to x2.09 from 65° to 33.5°, whose
    tangents' ratio is 2.11). Never under 1: a smaller image reads the levels nearer than
    measured, and building them farther costs a little, where building them nearer would show the
    neutral colour. At most MAX_SCALE. 1 when X-Plane has not said."""
    if not width_px or not fov_deg or not 1.0 <= fov_deg <= 170.0:
        return 1.0
    ratio = math.tan(math.radians(REFERENCE_FOV_DEG / 2)) / math.tan(math.radians(fov_deg / 2))
    return min(MAX_SCALE, max(1.0, width_px / REFERENCE_WIDTH_PX * ratio))


def top_level_for(
    distance_km: float, zoom: int, finest: int, margin: float = DEFAULT_MARGIN
) -> int:
    """The sharpest level whose photos a texture of ``zoom`` needs once its centre comes within
    ``distance_km`` of the camera, never sharper than ``finest``, nor than 4096 px: an 8192 px
    level is the streamer's own rule (the module's docstring)."""
    for level in range(max(finest, 0), LOAD_LEVEL):
        if distance_km <= ring_km(level, zoom) * margin:
            return level
    return LOAD_LEVEL


def zoom_for(zoom: int, max_zoom: int, max_zoom_airports: int) -> int:
    """The zoom set for a texture of ``zoom``: AutoOrtho's rule (getortho.py
    ``_get_target_zoom_level``), the one near airports for the mesh's ZL18 textures, which are its
    airports' surroundings, the maximum zoom for the others."""
    return max_zoom_airports if zoom == AIRPORT_ZOOM else max_zoom


def file_top(zoom: int, max_zoom: int) -> int:
    """The sharpest level of a texture of ``zoom`` whose imagery goes to ``max_zoom``: level k is
    imagery at ``zoom - k``. As AutoOrtho sizes it, below the mesh's zoom it is a smaller texture
    (level 1: 2048 px) and above it an 8192 px one, one zoom above at most (level -1)."""
    return max(MIN_LEVEL, min(LOAD_LEVEL, zoom - max_zoom))
