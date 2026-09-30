# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Which built textures to make coarser when the pack goes over its disk budget.

Only when it goes over (the user's choice, 2026-09-29: textures kept serve the next flights there, a
way back and a hold, at no work and no disk writes), those behind the aircraft first, then the least
recently needed at their sharpness, and only where nothing can show it. A texture counts as needed
while X-Plane will read it as sharp as it is built (``Streamer.plan``); until 2026-09-30 every
texture within 300 km counted, so the oldest built went first, which on a flight's way back were
those ahead of the aircraft (the Norway flight of 2026-09-29, found by the review of 2026-09-30).
The pack may also take the room the imagery cache leaves (``Streamer.texture_budget``).

X-Plane 12 holds a window of DSF tiles round the aircraft: 4 columns of longitude by 3 rows of
latitude, its columns centred on the tile boundary nearest the aircraft, its rows on the aircraft's
own row. It moves its columns 0.15 degree past a column's middle in the direction of travel, its
rows 0.1 degree past a row's edge, and lets go of the tiles left behind at once. The measurement
flight (2026-09-29) went from LFBO to LFMT and back at FL300: X-Plane moved at 1.65 and 3.65 east,
then at 3.35 and 2.35 back west, loading again the columns it had let go of. The starts at LEMD,
LEBL and LFBO showed the same window. So behind the aircraft X-Plane holds 1.65 to 2.65 columns,
133 to 213 km at 43.6 N; the 300 km kept until then held about twice that. The replayable test
flights over Norway (``docs/specs/live/flights.md``, the same day) found the same window at 60.5 N,
in degrees, not kilometres: its columns moved at 10.65, 11.65 and 12.65 east and at 12.35, 11.35 and
10.35 back west, each new one 1.35 degree ahead (74 km there, 108 at 43.6 N), and its rows at 61.1
north and 60.9 back south.

- A sharp texture goes back to 256 px (its small levels are in the file: nothing to download) when
  no tile it lies on is in the window X-Plane may hold round the aircraft or the camera
  (:func:`xplane_tiles`, with HYSTERESIS_DEG either way for where X-Plane moves it). The planner
  checks too that X-Plane reads only its 256 px level from there, beyond its 512 px ring and the
  camera's reach, so that a window other than measured (another latitude, another X-Plane) still
  shows nothing. X-Plane reads 256 px of a tile when it loads it again, and the planner builds the
  texture sharp again before X-Plane wants it so, as any texture ahead.
- A 256 px texture goes back to the neutral clone only beyond 300 km (EVICT_SAFE_KM): within that
  distance the planner keeps every texture at its 256 px level (X-Plane loads tiles up to 230 km
  out).

Nothing is lost that X-Plane could miss: the probe flight saw it open a texture again 1,276 times
for a sharper level and never for a coarser one.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping
from functools import cache

from . import dds
from .geo import Texture
from .region import NEUTRAL_LEVEL

EVICT_SAFE_KM = 300.0  # beyond: a 256 px texture may go back to the neutral clone
# X-Plane's window of DSF tiles (the module's docstring), and how far past where it moves it may be
# left: 0.15 degree measured on columns (43.6 and 60.5 N), 0.1 on rows (60.5 N)
XPLANE_COLUMNS = 4
XPLANE_ROWS = 3
HYSTERESIS_DEG = 0.25
COLUMN_MOVE_DEG = 0.15  # X-Plane moves its columns this far past a column's middle...
ROW_MOVE_DEG = 0.1  # ... and its rows this far past a row's edge, in the direction of travel
TARGET = 0.9  # evict down to 90 % of the budget, not just under it: no eviction at every step


def xplane_tiles(lat: float, lon: float) -> set[tuple[int, int]]:
    """The DSF tiles, (latitude, longitude) of their south-west corners, that X-Plane may hold
    with the aircraft at (``lat``, ``lon``): its window wherever its lag may have left it."""
    columns: set[int] = set()
    for at in (lon - HYSTERESIS_DEG, lon + HYSTERESIS_DEG):
        middle = math.floor(at + 0.5)  # the tile boundary nearest
        columns.update(range(middle - XPLANE_COLUMNS // 2, middle + XPLANE_COLUMNS // 2))
    rows: set[int] = set()
    for at in (lat - HYSTERESIS_DEG, lat + HYSTERESIS_DEG):
        middle = math.floor(at)  # the row itself
        rows.update(range(middle - XPLANE_ROWS // 2, middle + XPLANE_ROWS // 2 + 1))
    return {(row, (column + 180) % 360 - 180) for row in rows for column in columns}


class XPlaneWindow:
    """Where X-Plane's window is, followed as it moves it (the module's docstring), and when it
    will move it next: the tile boundary its columns are centred on, the row its rows are centred
    on. Taken again from the aircraft's position at a start or a jump."""

    def __init__(self) -> None:
        self.column: int | None = None  # the tile boundary nearest the aircraft when last moved
        self.row: int | None = None

    def follow(self, lat: float, lon: float) -> None:
        if (
            self.column is None
            or self.row is None
            or abs(lon - self.column) > 2
            or (abs(lat - self.row - 0.5) > 2)
        ):
            self.column, self.row = math.floor(lon + 0.5), math.floor(lat)
            return
        while lon > self.column + 0.5 + COLUMN_MOVE_DEG:
            self.column += 1
        while lon < self.column - 0.5 - COLUMN_MOVE_DEG:
            self.column -= 1
        while lat > self.row + 1 + ROW_MOVE_DEG:
            self.row += 1
        while lat < self.row - ROW_MOVE_DEG:
            self.row -= 1

    def next_move_s(self, lat: float, lon: float, gs_ms: float, trk_deg: float) -> float | None:
        """Seconds until X-Plane moves its window, the aircraft going on as it goes (its ground
        speed and track); None when it does not move."""
        if self.column is None or self.row is None or gs_ms <= 0:
            return None
        t = math.radians(trk_deg)
        north = gs_ms * math.cos(t) / 111_195.0  # degrees a second
        east = gs_ms * math.sin(t) / (111_195.0 * max(0.01, math.cos(math.radians(lat))))
        times = []
        if east > 0:
            times.append((self.column + 0.5 + COLUMN_MOVE_DEG - lon) / east)
        elif east < 0:
            times.append((self.column - 0.5 - COLUMN_MOVE_DEG - lon) / east)
        if north > 0:
            times.append((self.row + 1 + ROW_MOVE_DEG - lat) / north)
        elif north < 0:
            times.append((self.row - ROW_MOVE_DEG - lat) / north)
        times = [s for s in times if s >= 0]
        return min(times) if times else None


def tiles_of(texture: Texture) -> set[tuple[int, int]]:
    """The DSF tiles a texture lies on: two or four where it crosses a tile's edge."""
    south, west, north, east = texture.bounds
    return {
        (row, column)
        for row in range(math.floor(south), math.floor(north - 1e-9) + 1)
        for column in range(math.floor(west), math.floor(east - 1e-9) + 1)
    }


@cache
def texture_bytes(level: int, top: int = 0) -> int:
    """Disk a texture built from ``level`` takes in a file whose sharpest level is ``top``: its
    levels from that one down, and the copy of that level above it where there is one
    (orthostudio.live.texture.needs_copy). The rest shares the neutral template's blocks."""
    from .texture import needs_copy

    if level >= NEUTRAL_LEVEL:
        return 0
    copy = dds.level_layout(level - 1)[0][1] if needs_copy(level, top) else 0
    return 4096 + copy + sum(length for _, length in dds.level_layout(level))


def usage(built: dict[str, int], top_of: Callable[[str], int] | None = None) -> int:
    """The disk of the textures built; ``top_of``: each one's file size (its sharpest level), 4096
    px for all when not given."""
    if top_of is None:
        return levels_usage(Counter(built.values()))
    return sum(texture_bytes(level, top_of(name)) for name, level in built.items())


def levels_usage(levels: Mapping[int, int]) -> int:
    """The disk of textures counted by level."""
    return sum(texture_bytes(level) * n for level, n in levels.items())


def pick(
    built: dict[str, int],
    seen: dict[str, float],
    coarsen: Callable[[str], bool],
    budget: int,
    top_of: Callable[[str], int] = lambda _: 0,
    neutral: Callable[[str], bool] | None = None,
    behind: Callable[[str], bool] = lambda _: False,
) -> list[tuple[str, int]]:
    """[(texture, new level)] bringing ``built`` to 90 % of ``budget``, or as close as it can:
    sharp textures ``coarsen`` allows back to 256 px, then 256 px ones ``neutral`` allows (the same
    when not given) back to the neutral clone; those ``behind`` the aircraft first, then the least
    recently seen. ``top_of``: each texture's file size, as :func:`usage` takes it."""
    total = usage(built, top_of)
    if total <= budget:
        return []
    neutral = coarsen if neutral is None else neutral
    target = budget * TARGET
    out: list[tuple[str, int]] = []

    def first(name: str) -> tuple[bool, float]:
        return not behind(name), seen.get(name, 0.0)

    sharp = sorted((n for n, lv in built.items() if lv < 4 and coarsen(n)), key=first)
    for name in sharp:
        top = top_of(name)
        total -= texture_bytes(built[name], top) - texture_bytes(4, top)
        out.append((name, 4))
        if total <= target:
            return out
    small = sorted((n for n, lv in built.items() if lv == 4 and neutral(n)), key=first)
    for name in small:
        total -= texture_bytes(4, top_of(name))
        out.append((name, NEUTRAL_LEVEL))
        if total <= target:
            break
    return out
