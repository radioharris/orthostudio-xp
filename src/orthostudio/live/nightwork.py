# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The night textures of Live's textures built, made while it is night in X-Plane.

The streamer builds the day textures (orthostudio.live.streamer); this follows them. Each texture
built gets its night texture (orthostudio.live.night) from the same level down, a quarter of its
side: a 4096 px day texture a 1024 px night one, a 256 px one far away a 64 px one, so the far
towns light up early and cheaply, as the far ground does. Nothing is made by day: the sun's height
in X-Plane (``sim/graphics/scenery/sun_pitch_degrees``) says when. From :data:`NIGHT_SUN_DEG`, a
little before sunset, every texture built gets its night, those nearest the aircraft first, before
X-Plane lights its towns itself (round -6°, measured at Nice on 2026-11-10: nothing at 17:25,
lit at 17:50).

The work runs in one process of its own at a lower priority (orthostudio.live.texture's
initializer), after the streamer's: a night texture never holds up a day one. The textures built
at 1024 px or more (:data:`NEAR_LEVEL`, within about 40 km) get theirs first, right after their
day texture, and a start waits for them; the far ones follow, nearest first. Never in Live's
encoders: made in each texture's own build job, the night took each of the twelve 155 MB more
(27 MB at rest, 182 MB once it had made one; the user's Mac, 2026-11-10), against one process.
A texture's night is made from two things only, each read from the disk: X-Plane's roads of its
squares (the Global Scenery's DSF, else the overlays of Live's regions, cut from it) and NASA's
grid of the square (``<region>/night/<square>.png``, installed with the region).
``OSXP_LIVE_NIGHT_GRIDS`` names another folder of grids, looked in first: the test flights'.
"""

from __future__ import annotations

import heapq
import math
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from . import dds
from .evict import tiles_of
from .geo import parse_texture_name

if TYPE_CHECKING:
    from .names import NightPool

NIGHT_SUN_DEG = 20.0
"""The sun's height from which the night textures are made, right after the day ones: about an
hour and a half before sunset. X-Plane never reads again a level it took, so a night texture must
be there before X-Plane reads its texture, as the day one: one made after it stays black where
X-Plane does not come closer (the user's Live flight, 2026-11-10)."""

IN_FLIGHT = 2
"""Night textures being made at once."""

MARGIN_DEG = 0.1
"""How far round its square a density grid reaches, so a texture on an edge finds its grid."""

GRID_STEP_DEG = 1.0 / 240.0
"""A density grid's cell: Black Marble's 15 arc-seconds."""

START_MARGIN_DEG = 5.0
"""At a start, the sun's height is worked out from the time X-Plane saved (:func:`sun_at_start`),
to a few degrees: its night textures are made for a start up to this much higher than
:data:`NIGHT_SUN_DEG`, a few seconds more at a start round dusk rather than an airport left dark."""

NEAR_LEVEL = 2
"""The coarsest day level whose night goes first, and which a start waits for: 1024 px, within
about 40 km."""

START_KM = 10.0
"""A start also waits for every night texture this close to it, as for its day ones (BLOCK_KM)."""

NIGHT_WAIT_S = 60.0
"""How long a start waits for its night textures once its day ones are built, at most: a night
process stuck never holds X-Plane on its loading screen, and what is left is made in flight
(X-Plane reads it as it comes closer). At LSGG the night took 25 s (2026-11-10)."""

_ROADS: dict = {}  # the night process's roads (orthostudio.live.night.Roads), by DSF
_ROADS_KEPT = 9  # the squares round the aircraft, 3 x 3: each read once while it flies there


def _night_bytes(top: int) -> int:
    """A night texture's bytes from level ``top`` down (its larger levels are a hole)."""
    from .region import NIGHT_TOP

    return sum(length for _, length in dds.level_layout(NIGHT_TOP)[top - NIGHT_TOP :])


def sun_at_start(prefs: Path) -> float | None:
    """The sun's height in degrees at the start X-Plane saved in ``Freeflight.prf`` (``_time``:
    the day of the year, the local time, the latitude and longitude), or None. The local time is
    taken for the mean solar time of the place's time zone, which a zone's width, summer time and
    the equation of time leave a few degrees out: :data:`START_MARGIN_DEG`."""
    import json

    try:
        with open(prefs, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("_time "):
                    lt = json.loads(line[len("_time ") :]).get("local_time", {})
                    day = float(lt["day_of_year"])
                    hours = float(lt["time_in_24_hours"])
                    lat = float(lt["latitude"])
                    lon = float(lt["longitude"])
                    break
            else:
                return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    solar = hours + (lon / 15.0 - round(lon / 15.0))  # the zone's mean time to the place's
    decl = -23.44 * math.cos(math.radians(360.0 / 365.0 * (day + 10)))
    hour_angle = math.radians(15.0 * (solar - 12.0))
    phi, d = math.radians(lat), math.radians(decl)
    s = math.sin(phi) * math.sin(d) + math.cos(phi) * math.cos(d) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def square_name(lat: int, lon: int) -> str:
    return f"{lat:+03d}{lon:+04d}"


def density_of(path: Path, lat: int, lon: int):
    """The density grid of square ``lat``, ``lon`` (orthostudio.live.night.Density), or None."""
    from PIL import Image

    from .night import Density

    try:
        grid = np.asarray(Image.open(path).convert("L"))
    except (OSError, ValueError):
        return None
    return Density(grid, lon - MARGIN_DEG, lat + 1 + MARGIN_DEG, GRID_STEP_DEG)


def roads_from(paths: Iterable[Path]):
    """X-Plane's roads of the DSFs given, a :class:`orthostudio.live.night.Roads` each, read once
    (:func:`_read_apart`) and the last few kept (:data:`_ROADS_KEPT`): a texture's squares are four
    at most, and the textures are given nearest the aircraft first."""
    parts = []
    for path in paths:
        key = str(path)
        if key not in _ROADS:
            if len(_ROADS) >= _ROADS_KEPT:
                _ROADS.pop(next(iter(_ROADS)))
            _ROADS[key] = _read_apart(Path(path))
        if len(_ROADS[key]):
            parts.append(_ROADS[key])
    return parts


def _read_apart(path: Path):
    """The roads of a DSF read in a Python of its own, which ends with it: a DSF read in the night's
    process left it 70 to 120 MB more each square for good (Python's memory left in pieces by
    150,000 roads' objects), its roads being 4 to 11 MB (2026-11-10). A second more a square."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(
        1, mp_context=multiprocessing.get_context("spawn"), max_tasks_per_child=1
    ) as pool:
        return pool.submit(read_roads, str(path)).result()


def read_roads(path: str):
    """The roads of the DSF at ``path`` (the Global Scenery's, or an overlay of Live's), none if it
    is missing or damaged."""
    from orthostudio.dsf.xp12 import read_global_scenery_dsf
    from orthostudio.model import TileRef

    from .geo import dsf_tile_of
    from .night import Roads, roads_of

    try:
        tile = TileRef(*(dsf_tile_of(path) or (0, 0)))
        return roads_of(read_global_scenery_dsf(Path(path), tile))
    except Exception:  # no lights from it
        return Roads.empty()


def _degrees(point: tuple[float, float], at: tuple[float, float]) -> float:
    """From ``at`` to ``point`` (latitude, longitude), in degrees of latitude."""
    dx = (point[1] - at[1]) * math.cos(math.radians(at[0]))
    return math.hypot(point[0] - at[0], dx)


def make_night(pack: Path, name: str, level: int, dsfs, grid: str | None) -> None:
    """The night texture of ``name`` made from day level ``level`` and written over its black
    name, from its level down."""
    from .imaging import encode_bc1
    from .night import LIT_SIDE, draw, lights_at, lit_levels, night_rgb, seed_of
    from .region import NIGHT_FOLDER, NIGHT_TOP, ensure_night_neutral
    from .texture import write_sparse

    tex = parse_texture_name(name)
    if tex is None:
        raise ValueError(f"not a texture: {name}")
    level = max(0, level)  # an 8192 px day texture: the night one at its sharpest too
    density = None
    if grid is not None:
        lat, lon = (int(v) for v in Path(grid).stem.replace("+", " +").replace("-", " -").split())
        density = density_of(Path(grid), lat, lon)
    # drawn sharp, at the night texture's full side, then made smaller for a build far away
    roads = roads_from(Path(d) for d in dsfs)
    lights = draw(roads, density, tex.x, tex.y, tex.zoom, LIT_SIDE,
                  seed=seed_of(tex.x, tex.y))  # fmt: skip
    lights = lights_at(lights, level)
    if density is None:
        lights = lights * 0.85  # what the grid gives on average in a town
    levels = [encode_bc1(lv) for lv in lit_levels(night_rgb(lights))]
    out = pack / NIGHT_FOLDER / (name[: -len(".dds")] + "_LIT.dds")
    black = ensure_night_neutral(pack)
    write_sparse(out, NIGHT_TOP + level, levels, base=black, file_top=NIGHT_TOP)


def night_job(job: tuple) -> tuple[str, int, float]:
    """In the night's process: the night texture of a texture built. ``job``: (pack, name, level,
    the DSFs of its squares, its density grid or None). Returns (name, level, seconds)."""
    pack, name, level, dsfs, grid = job
    t = time.monotonic()
    make_night(Path(pack), name, level, dsfs, grid)
    return name, max(0, level), time.monotonic() - t


class NightWork:
    """The streamer's night textures: which are made at which level, which wait, the worker."""

    def __init__(
        self,
        pack_dir: Path,
        global_scenery: Path | None,
        overlays: Path | None,
        grids: Iterable[Path],
        log: Callable[[str], None] = print,
        executor=None,
        prefs: Path | None = None,
    ):
        self.pack_dir = pack_dir
        self.global_scenery = global_scenery
        self.overlays = overlays
        self.grids = list(grids)  # where the density grids are: each region's night folder
        self.log = log
        self.sun: float | None = None
        self.made: dict[str, int] = {}  # name: the day level its night texture was made from
        self.waiting: dict[str, int] = {}
        self.in_flight: dict[str, int] = {}
        self.seconds = 0.0
        self.lock = threading.Lock()
        self._executor = executor
        self._pool: NightPool | None = None  # once a texture went back to black
        self.prefs = prefs  # X-Plane's Freeflight.prf: the time of a start (:func:`sun_at_start`)
        self.start_at: tuple[float, float] | None = None
        self._dsfs: dict[tuple[int, int], Path | None] = {}  # each square's, looked for once
        self._centres: dict[str, tuple[float, float] | None] = {}  # each texture's, read once
        self._waited_since: float | None = None  # the start's day textures built, its nights not
        self.gave_up = False
        self.failed = 0

    @property
    def night(self) -> bool:
        return self.sun is not None and self.sun < NIGHT_SUN_DEG

    def executor(self):
        """The night's process, forked as the encoders are (orthostudio.live.pools: never from
        Live's own process, which may crash a process it starts), started again if it dies."""
        with self.lock:  # the streamer's threads and the plan loop's
            if self._executor is None:
                from . import pools
                from .texture import worker_ignores_ctrl_c

                self._executor = pools.RenewingPool(
                    lambda: ProcessPoolExecutor(
                        1, initializer=worker_ignores_ctrl_c, mp_context=pools.context()
                    ),
                    self.log,
                )
            return self._executor

    def built(self, name: str, level: int) -> None:
        """The streamer built ``name`` at ``level``: its night follows, if it is night."""
        if self.night:
            self.want(name, level)

    def want(self, name: str, level: int) -> None:
        with self.lock:
            level = max(0, level)
            if self.made.get(name, 99) <= level or self.in_flight.get(name, 99) <= level:
                return
            self.waiting[name] = min(level, self.waiting.get(name, 99))

    def start(
        self, lat: float, lon: float, built: Callable[[], dict[str, int]] | None = None
    ) -> None:
        """A start planned there: night there if the time X-Plane saved says so, with a margin
        (:data:`START_MARGIN_DEG`), its night textures then made before X-Plane goes on, those of
        the textures ``built`` already too. Live may learn of a start before X-Plane saves its
        time, and build round it by the time of the flight before: at LSGG, 8,744 textures built
        by a day's time got no night when X-Plane then gave 23:30 (the user's flight,
        2026-11-10)."""
        self.start_at = (lat, lon)
        self._waited_since, self.gave_up = None, False
        sun = sun_at_start(self.prefs) if self.prefs is not None else None
        if sun is not None:
            was = self.night
            self.sun = sun - START_MARGIN_DEG  # night from that much higher, at a start
            if self.night:
                lit = self._nightfall(built) if not was and built is not None else 0
                self.log(
                    f"  a start at night (the sun at about {sun:.0f}°): its towns lit first"
                    + (f", {lit} textures built already" if lit else "")
                )

    def start_left(self, day_left: int = 0) -> int:
        """Night textures still to make which a start waits for, as for its photos (0 by day):
        every near one (:data:`NEAR_LEVEL`) and all within :data:`START_KM`. X-Plane never reads
        again a level it took: a night made after it loaded its area stays black there until it
        comes closer (the user's Live flight, 2026-11-10). ``day_left``: the start's day textures
        still to build; once none is, the nights are waited for :data:`NIGHT_WAIT_S` at most."""
        if self.start_at is None or not self.night or self.gave_up:
            return 0
        with self.lock:
            todo = list(self.waiting.items()) + list(self.in_flight.items())
        start = self.start_at
        left = sum(
            1
            for n, level in todo
            if level <= NEAR_LEVEL or self._distance(n, start) * 111.32 <= START_KM
        )
        if left == 0 or day_left > 0:
            self._waited_since = None
            return left
        now = time.monotonic()
        if self._waited_since is None:
            self._waited_since = now
        if now - self._waited_since > NIGHT_WAIT_S:
            self.gave_up = True
            self.log(
                f"  X-Plane goes on: {left} night textures round the start not made in "
                f"{NIGHT_WAIT_S:.0f} s, made in flight"
            )
            return 0
        return left

    def back_to_black(self, name: str, keep_name: bool) -> None:
        """``name`` made neutral by the disk budget: its night texture black again (``keep_name``:
        its square is made, X-Plane may read it), or gone with it; made again if night comes."""
        from .names import NightPool, night_name
        from .region import NIGHT_FOLDER

        path = self.pack_dir / NIGHT_FOLDER / night_name(name)
        self.forget(name)
        if not path.exists():
            return
        if keep_name:
            if self._pool is None:
                self._pool = NightPool(self.pack_dir)
            self._pool.replace(0, path)
        else:
            path.unlink(missing_ok=True)

    def bytes(self) -> int:
        """What the night textures made take on the disk, as their levels count it."""
        from .region import NIGHT_TOP

        with self.lock:
            levels = list(self.made.values())
        return sum(_night_bytes(NIGHT_TOP + level) for level in levels)

    def forget(self, name: str) -> None:
        """``name`` back to neutral (the disk budget): its night back to black, made again later."""
        with self.lock:
            self.made.pop(name, None)
            self.waiting.pop(name, None)
            self._centres.pop(name, None)

    def tick(
        self,
        sun: float | None,
        at: tuple[float, float] | None,
        built: Callable[[], dict[str, int]],
    ) -> None:
        """X-Plane's sun now, where the aircraft is and the textures built: the night textures to
        make queued when night falls, and given to the worker nearest first."""
        was = self.night
        if sun is not None:
            self.sun = sun
        if not self.night:
            return
        if not was:
            lit = self._nightfall(built)
            self.log(f"  night in X-Plane (the sun at {self.sun:.1f}°): {lit} textures lit")
        self._give(at)

    def _nightfall(self, built: Callable[[], dict[str, int]]) -> int:
        """Night has come: every texture built gets its night (the nearest first, :meth:`_give`).
        Returns how many."""
        textures = built()
        for name, level in textures.items():
            self.want(name, level)
        return len(textures)

    def _give(self, at: tuple[float, float] | None) -> None:
        with self.lock:
            free = IN_FLIGHT - len(self.in_flight)
            if free <= 0 or not self.waiting:
                return
            waiting = self.waiting
            names = heapq.nsmallest(free, waiting, key=lambda n: self._order(n, waiting[n], at))
            jobs = [(n, self.waiting.pop(n)) for n in names]
            for name, level in jobs:
                self.in_flight[name] = level
        for name, level in jobs:
            fut = self.executor().submit(night_job, self._job(name, level))
            fut.add_done_callback(lambda f, n=name, lv=level: self._done(f, n, lv))

    def _job(self, name: str, level: int) -> tuple:
        tex = parse_texture_name(name)
        squares = sorted(tiles_of(tex)) if tex is not None else []
        dsfs = [str(p) for p in (self._dsf(lat, lon) for lat, lon in squares) if p is not None]
        lat, lon = (math.floor(v) for v in tex.center) if tex is not None else (0, 0)
        file = f"{square_name(lat, lon)}.png"
        grid = next((d / file for d in self.grids if (d / file).is_file()), None)
        return (str(self.pack_dir), name, level, dsfs, str(grid) if grid else None)

    def _dsf(self, lat: int, lon: int) -> Path | None:
        from .geo import dsf_rel_path

        if (lat, lon) in self._dsfs:
            return self._dsfs[(lat, lon)]
        found = None
        for root in (self.global_scenery, self.overlays):
            if root is not None:
                path = root / dsf_rel_path(lat, lon)
                if path.is_file():
                    found = path
                    break
        self._dsfs[(lat, lon)] = found
        return found

    def _done(self, fut: Future, name: str, level: int) -> None:
        with self.lock:
            self.in_flight.pop(name, None)
            if name not in self.waiting:
                self._centres.pop(name, None)
            try:
                _, _, seconds = fut.result()
            except Exception as e:  # tried again at the next sharper build, or the next night
                self.failed += 1
                if self.failed <= 5:
                    self.log(f"  the night texture of {name} failed: {type(e).__name__}: {e}")
                return
            self.made[name] = min(level, self.made.get(name, 99))
            self.seconds += seconds

    def _order(self, name: str, level: int, at: tuple[float, float] | None) -> tuple:
        """The near ones first (:data:`NEAR_LEVEL`), nearest the aircraft first; then the far
        ones a square at a time, the nearest square first: given nearest first, they went round
        the aircraft from square to square and the night's process read a square's roads again
        for nearly every one, a texture a second (3,951 reads for LSGG's 8,535 far textures
        over 46 squares, against 136 a square at a time; the user's flight, 2026-11-10)."""
        near = self._distance(name, at)
        centre = self._centres.get(name)
        if level <= NEAR_LEVEL or centre is None or at is None:
            return (level > NEAR_LEVEL, 0.0, near)
        square = (math.floor(centre[0]) + 0.5, math.floor(centre[1]) + 0.5)
        return (True, round(_degrees(square, at), 6), near)

    def _distance(self, name: str, at: tuple[float, float] | None) -> float:
        """In degrees of latitude, from ``at``; each name read once (thousands wait at dusk, and
        this runs at each of the plan loop's turns)."""
        centre = self._centres.get(name)
        if centre is None:
            tex = parse_texture_name(name)
            centre = self._centres[name] = tex.center if tex is not None else None
        if centre is None or at is None:
            return 0.0
        return _degrees(centre, at)

    def close(self) -> None:
        with self.lock:
            made = len(self.made)
        if made:
            self.log(f"  {made} night textures made")
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
