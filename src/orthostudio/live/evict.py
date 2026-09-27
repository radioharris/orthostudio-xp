# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Which built textures to make coarser when the pack goes over its disk budget.

Only textures more than 300 km from the aircraft and from the camera: beyond what X-Plane has
loaded, where it reads nothing. Even there nothing is lost that X-Plane could miss: the probe flight
saw it open a texture again 1,276 times for a sharper level and never for a coarser one, and a
texture that comes back within reach is rebuilt by the planner before X-Plane wants it sharp.

The least recently seen go first: a sharp texture back to 256 px (its small levels are in the file:
nothing to download), and if that is not enough, 256 px ones back to the neutral clone.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from functools import cache

from . import dds
from .region import NEUTRAL_LEVEL

EVICT_SAFE_KM = 300.0
TARGET = 0.9  # evict down to 90 % of the budget, not just under it: no eviction at every step


@cache
def texture_bytes(level: int) -> int:
    """Disk a texture built from ``level`` takes: its levels from that one down, whatever the size
    of its file (a neutral clone, and the neutral larger levels, share the template's blocks)."""
    if level >= NEUTRAL_LEVEL:
        return 0
    return 4096 + sum(length for _, length in dds.level_layout(level))


def usage(built: dict[str, int]) -> int:
    return levels_usage(Counter(built.values()))


def levels_usage(levels: Mapping[int, int]) -> int:
    """The disk of textures counted by level."""
    return sum(texture_bytes(level) * n for level, n in levels.items())


def pick(
    built: dict[str, int],
    seen: dict[str, float],
    far: Callable[[str], bool],
    budget: int,
) -> list[tuple[str, int]]:
    """[(texture, new level)] bringing ``built`` to 90 % of ``budget``, or as close as it can."""
    total = usage(built)
    if total <= budget:
        return []
    target = budget * TARGET
    out: list[tuple[str, int]] = []
    sharp = sorted(
        (n for n, lv in built.items() if lv < 4 and far(n)), key=lambda n: seen.get(n, 0.0)
    )
    for name in sharp:
        total -= texture_bytes(built[name]) - texture_bytes(4)
        out.append((name, 4))
        if total <= target:
            return out
    small = sorted(
        (n for n, lv in built.items() if lv == 4 and far(n)), key=lambda n: seen.get(n, 0.0)
    )
    for name in small:
        total -= texture_bytes(4)
        out.append((name, NEUTRAL_LEVEL))
        if total <= target:
            break
    return out
