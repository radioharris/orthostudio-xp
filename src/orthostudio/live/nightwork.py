# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The night textures of Live's textures built, made while it is night in X-Plane.

The streamer builds the day textures (orthostudio.live.streamer); this follows them. Each texture
built gets its night texture (orthostudio.live.night) from the same level down, counted from the
top of its file, a quarter of its side: a 4096 px day texture a 1024 px night one, a 256 px one far
away a 64 px one, so the far towns light up early and cheaply, as the far ground does. Night, for
Live, is from :data:`NIGHT_AHEAD_H` before sunset to sunrise, by X-Plane's time at the aircraft
(:func:`night_at`): X-Plane never reads again a level it took, so a night texture must be there
before X-Plane reads its texture, as the day one.

One rule, in one place (:meth:`NightWork.pump`, at each turn of the streamer's loop): how many
processes make the night textures (a team at a start at night, one in flight, none by day), which
textures they get, and what is done with what they made. The processes' jobs only say they ended.
A texture's night is made from two things only, each read from the disk: X-Plane's roads of its
squares (the Global Scenery's DSF, else the overlays of Live's regions, cut from it) and NASA's
grid of the square (``<region>/night/<square>.png``, installed with the region).
``OSXP_LIVE_NIGHT_GRIDS`` names another folder of grids, looked in first: the test flights'.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from .evict import tiles_of, xplane_tiles
from .geo import parse_texture_name

if TYPE_CHECKING:
    from .names import NightPool

NIGHT_AHEAD_H = 2.0
"""The night textures are made from this long before sunset, and until sunrise: a start this close
to sunset waits for them, and in flight they are there before X-Plane lights its towns (round -6°,
measured at Nice on 2026-11-10: nothing at 17:25, lit at 17:50). By the sun's height alone (below
20°), a winter's day in Europe was night all day long (the review of 2026-11-11)."""

MARGIN_DEG = 0.1
"""How far round its square a density grid reaches, so a texture on an edge finds its grid."""

GRID_STEP_DEG = 1.0 / 240.0
"""A density grid's cell: Black Marble's 15 arc-seconds."""

FAR_DRAW = 2
"""A night texture is drawn this many levels above its own, four times its side, and made
smaller: a 64 px one far away drawn at 256 px rather than 1024, with most of its light (its lamps
a pixel each: 0.87 to 0.99 of it on average at ZL16, 0.73 and 0.87 round LSGG at ZL18, computed,
docs/specs/live.md),
up to ten times faster in the country (2026-11-11)."""

FAR_DRAW_MIN = 128

NEAR_LEVEL = 2
"""The coarsest level whose night goes first: 1024 px, within about 40 km."""

NIGHT_WAIT_S = 180.0
"""How long a start waits for its night textures once its day ones are built, at most: what is
left is made in flight (X-Plane reads it as it comes closer, no sooner)."""

TEAM_MAX = 8
"""At a start at night, this many processes at most (and no more than Live's encoders) make the
night textures X-Plane reads there, as the day ones are: one process made LSGG's 8,700 in 13
minutes, and those X-Plane read before stayed black (the user, 2026-11-11: « paralléliser le
démarrage des textures et attendre que tout soit fait comme pour le jour »). One in flight."""

JOB_WORK = 4.0
"""A job's work, in night textures drawn at 1024 px: the textures of one square, their roads read
once for them, up to this much drawing (:func:`_work`): four near ones, or tens of far ones,
drawn small. Jobs of four textures whatever their size left the processes waiting between two
turns of Live's loop, far ones taking a few milliseconds: 3,157 of an LSGG start's 8,535 made in
24 s, against all of them (2026-11-11)."""

TRIES = 2
"""A night texture that failed this many times is left for the flight (the log says why)."""

LOST_MAX = 5
"""Processes lost within a minute past which the night stops for the flight, never Live."""

_ROADS: dict = {}  # a process's roads (orthostudio.live.night.Roads), by DSF
_ROADS_KEPT = 9  # the squares round the aircraft, 3 x 3: each read once while it flies there


def sun_height(day: float, solar: float, lat: float) -> float:
    """The sun's height in degrees on ``day`` of the year at ``solar`` hours of mean solar time."""
    decl = -23.44 * math.cos(math.radians(360.0 / 365.0 * (day + 10)))
    hour_angle = math.radians(15.0 * (solar - 12.0))
    phi, d = math.radians(lat), math.radians(decl)
    s = math.sin(phi) * math.sin(d) + math.cos(phi) * math.cos(d) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def night_at(day: float, solar: float, lat: float, unsure_h: float = 0.0) -> bool:
    """Night for Live: the sun below the horizon at ``solar`` hours, or within
    :data:`NIGHT_AHEAD_H`; ``unsure_h``: the time known to that many hours earlier too."""
    when = (solar - unsure_h, solar, solar + NIGHT_AHEAD_H)
    return min(sun_height(day, h, lat) for h in when) < 0.0


START_UNSURE_H = 2.0
"""X-Plane saves a start's local time, its zone's, up to two hours ahead of the sun's in summer
time (Central Europe's: Nice's towns lit at 17:50 local on 10 November): night at a start is night
at any of those hours, until the plugin gives X-Plane's UTC time as it loads the flight
(:meth:`NightWork.tick`). Taken for the zone of its longitude alone, a start at dawn in Europe was
day; with this doubt alone, a start at Geneva at 17:30 in June waited for its night textures in
daylight (the reviews of 2026-11-11)."""


def _solar_at_start(prefs: Path) -> tuple[float, float, float] | None:
    """The start X-Plane saved in ``Freeflight.prf`` (``_time``): (day of the year, solar hours,
    latitude), or None. X-Plane saves its local time alone: taken for the mean solar time of the
    zone the longitude falls in, an hour or two out where the zone is wider or in summer time
    (Nice's is Central Europe's: its towns lit at 17:50 local on 10 November); the flight's own
    time sets it right in its first seconds (:meth:`NightWork.tick`)."""
    try:
        with open(prefs, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("_time "):
                    lt = json.loads(line[len("_time ") :]).get("local_time", {})
                    lon = float(lt["longitude"])
                    solar = float(lt["time_in_24_hours"]) + lon / 15.0 - round(lon / 15.0)
                    return float(lt["day_of_year"]), solar, float(lt["latitude"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return None


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


ROADS_FOLDER = Path("_live") / "roads"
"""Each square's roads, kept in the pack once read from its DSF (:func:`roads_file`): read, the
62 squares round LSGG took 64 s, a second each, and the start's team read a square again for
each of its neighbours; from their files, a few milliseconds (2026-11-11)."""

ROADS_LOCK_S = 30.0
"""How long a process waits for another reading the same square, at most (a DSF takes about a
second, Marseille's two). A lock left by a process that died goes at Live's next start
(orthostudio.live.region.remove_temps)."""

ROADS_KEPT_MB = 1000
"""The roads' files kept at most, the oldest dropped (4.8 MB a square on average round LSGG), within
Live's share for them (orthostudio.live.streamer.ROADS_SHARE): the night's loop keeps them to it."""


def roads_file(pack: Path, dsf: Path) -> Path:
    """Where the roads of ``dsf`` are kept in the pack: named after the DSF, its size and its
    time, so a Global Scenery updated is read again. Raises ``OSError`` for a DSF gone."""
    import hashlib

    st = os.stat(dsf)
    key = hashlib.sha1(f"{Path(dsf).resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()
    return pack / ROADS_FOLDER / f"{Path(dsf).stem}-{key[:12]}.npz"


def roads_from(paths: Iterable[Path], pack: Path | None = None, apart: bool = True):
    """X-Plane's roads of the DSFs given, a :class:`orthostudio.live.night.Roads` each: from their
    file in ``pack`` (:func:`roads_file`), else read and their file written; the last few kept in
    memory (:data:`_ROADS_KEPT`). ``apart``: a DSF read in a Python of its own
    (:func:`_read_apart`), for a process that lives the whole flight."""
    parts = []
    for path in paths:
        key = str(path)
        if key not in _ROADS:
            if len(_ROADS) >= _ROADS_KEPT:
                _ROADS.pop(next(iter(_ROADS)))
            try:
                _ROADS[key] = _roads_of(Path(path), pack, apart)
            except Exception as e:  # its job's other textures fail at once (night_batch)
                _ROADS[key] = e.with_traceback(None)  # not the read's memory with it
        roads = _ROADS[key]
        if isinstance(roads, Exception):
            raise roads
        if len(roads):
            parts.append(roads)
    return parts


def _roads_of(dsf: Path, pack: Path | None, apart: bool = True):
    from .night import Roads

    def read():
        return _read_apart(dsf) if apart else read_roads(str(dsf))

    if pack is None:
        return read()
    try:
        file = roads_file(pack, dsf)
    except OSError:  # the DSF gone: no lights from it
        return Roads.empty()
    roads = _load_roads(file)
    if roads is not None:
        return roads
    # one process reads a square, the others wait for its file: at a start the team's processes
    # read the same squares at once, the first start at LSGG 30 s long for it (2026-11-11)
    file.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_square(file)
    try:
        roads = _load_roads(file)  # written while we waited
        if roads is not None:
            return roads
        roads = read()  # only a read that worked is kept: one that failed raises
        with contextlib.suppress(OSError):  # the disk full: read again next time
            roads.save(file)
        return roads
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                os.unlink(lock)


def _load_roads(file: Path):
    from .night import Roads

    if not file.is_file():
        return None
    try:
        roads = Roads.load(file)
    except Exception:  # damaged, cut short: read again
        return None
    with contextlib.suppress(OSError):
        os.utime(file)  # used: kept the longest
    return roads


def _lock_square(file: Path) -> Path | None:
    """The square's lock (a file made only if none is there), waited for while another process
    reads it, until its roads' file is there; :data:`ROADS_LOCK_S` at most from the lock's own
    time: one a process that died left is not waited for (a team whose process died: Python ends
    the others with it). None when it could not be had: the square read all the same."""
    lock = file.with_suffix(".lock")
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return lock
        except FileExistsError:
            try:
                old = time.time() - os.stat(lock).st_mtime > ROADS_LOCK_S
            except OSError:  # gone meanwhile: taken at the next turn
                old = False
            if file.is_file() or old:
                return None
            time.sleep(0.05)
        except OSError:
            return None


def prune_roads(folder: Path, cap: int) -> int:
    """The oldest roads' files dropped past ``cap`` bytes; returns the bytes kept."""
    files = []
    with contextlib.suppress(OSError):
        for entry in os.scandir(folder):
            if entry.name.endswith(".npz"):
                with contextlib.suppress(OSError):
                    st = entry.stat()
                    files.append((st.st_mtime, st.st_size, entry.path))
    total = sum(size for _, size, _ in files)
    for _, size, path in sorted(files):
        if total <= cap:
            break
        with contextlib.suppress(OSError):
            os.unlink(path)
            total -= size
    return total


def _read_apart(path: Path):
    """The roads of a DSF read in a Python of its own, which ends with it: a DSF read in the night's
    process left it 70 to 120 MB more each square for good (Python's memory left in pieces by
    150,000 roads' objects), its roads being 4 to 11 MB (2026-11-10). A second more a square."""
    import multiprocessing

    from .texture import worker_ignores_ctrl_c

    with ProcessPoolExecutor(
        1,
        mp_context=multiprocessing.get_context("spawn"),
        max_tasks_per_child=1,
        initializer=worker_ignores_ctrl_c,  # a Ctrl-C is Live's to take, not a trace of its own
    ) as pool:
        return pool.submit(read_roads, str(path)).result()


def read_roads(path: str):
    """The roads of the DSF at ``path`` (the Global Scenery's, or an overlay of Live's). A read
    that fails raises, and is tried again: kept as a square without roads, a passing failure (the
    memory short) left it dark for good (the review of 2026-11-11)."""
    from orthostudio.dsf.xp12 import read_global_scenery_dsf
    from orthostudio.model import TileRef

    from .geo import dsf_tile_of
    from .night import roads_of

    tile = TileRef(*(dsf_tile_of(path) or (0, 0)))
    return roads_of(read_global_scenery_dsf(Path(path), tile))


def _work(level: int) -> float:
    """A night texture's drawing, in 1024 px drawings: it is drawn at four times its side
    (:data:`FAR_DRAW`), 128 px at least."""
    side = max(FAR_DRAW_MIN, 1024 >> max(0, level - FAR_DRAW))
    return (side / 1024.0) ** 2


def _degrees(point: tuple[float, float], at: tuple[float, float]) -> float:
    """From ``at`` to ``point`` (latitude, longitude), in degrees of latitude."""
    dx = (point[1] - at[1]) * math.cos(math.radians(at[0]))
    return math.hypot(point[0] - at[0], dx)


def make_night(
    pack: Path,
    name: str,
    level: int,
    dsfs,
    grid: str | None,
    gain: float | None = None,
    apart: bool = True,
) -> bool:
    """The night texture of ``name`` made from level ``level`` (counted from the top of the
    texture's file) and written over its black name, from its level down; its lights times
    ``gain`` (:data:`orthostudio.live.night.GAIN` when None). False: no light there, its black
    name kept, which takes no disk."""
    from .imaging import encode_bc1
    from .night import GAIN, LIT_SIDE, draw, lights_at, lit_levels, night_rgb, seed_of, warm_bc1
    from .region import NIGHT_FOLDER, NIGHT_TOP, ensure_night_neutral
    from .texture import write_sparse

    tex = parse_texture_name(name)
    if tex is None:
        raise ValueError(f"not a texture: {name}")
    level = max(0, level)
    density = None
    if grid is not None:
        lat, lon = (int(v) for v in Path(grid).stem.replace("+", " +").replace("-", " -").split())
        density = density_of(Path(grid), lat, lon)
    roads = roads_from((Path(d) for d in dsfs), pack, apart)
    # a far texture drawn at four times its side, not at 1024 px (FAR_DRAW)
    side = max(FAR_DRAW_MIN, LIT_SIDE >> max(0, level - FAR_DRAW))
    lights = draw(roads, density, tex.x, tex.y, tex.zoom, side, seed=seed_of(tex.x, tex.y))
    lights = lights_at(lights, level - (LIT_SIDE // side).bit_length() + 1)
    if density is None:
        lights = lights * 0.85  # what the grid gives on average in a town
    rgb = night_rgb(lights, GAIN if gain is None else gain)
    out = pack / NIGHT_FOLDER / (name[: -len(".dds")] + "_LIT.dds")
    ensure_night_neutral(pack)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not rgb.any():  # no light (the sea, a forest): the black name, not 684 KB of zeros
        from .names import NightPool

        NightPool(pack).replace(0, out)
        return False
    levels = [warm_bc1(encode_bc1(lv)) for lv in lit_levels(rgb)]
    # its larger levels a hole, which reads as zeros, black in BC1, at no cost to read: a clone
    # of the black texture, they were 700 KB that X-Plane read from the disk for each night
    # texture, 64 px or not (4.9 GB more read in the Nice flight, 2026-11-11)
    write_sparse(out, NIGHT_TOP + level, levels, file_top=NIGHT_TOP)
    return True


def night_batch(jobs: list[tuple]) -> list[tuple]:
    """In a night process: the night textures of a job, one after the other. ``jobs``: (pack,
    name, level, the DSFs of its squares, its density grid or None, the lights' gain, apart) each.
    Returns (name, seconds, whether it holds lights) each, or (name, the error, False) for one
    that failed."""
    out: list[tuple] = []
    for pack, name, level, dsfs, grid, gain, apart in jobs:
        t = time.monotonic()
        try:
            lit = make_night(Path(pack), name, level, dsfs, grid, gain, apart)
        except Exception as e:  # the others made all the same
            out.append((name, f"{type(e).__name__}: {e}", False))
        else:
            out.append((name, time.monotonic() - t, lit))
    # a square whose roads could not be read is read again at its next job: kept for the flight's
    # process, a passing failure left the square dark for the flight (the review of 2026-11-11)
    for key in [k for k, roads in _ROADS.items() if isinstance(roads, Exception)]:
        del _ROADS[key]
    return out


def _pool(size: int):
    """The night's processes: ``size`` of them, forked as the encoders are (orthostudio.live.pools:
    never from Live's own process), at their lower priority."""
    from . import pools
    from .texture import worker_ignores_ctrl_c

    return ProcessPoolExecutor(size, initializer=worker_ignores_ctrl_c, mp_context=pools.context())


class NightWork:
    """The streamer's night textures: which are wanted, being made, made; one rule in
    :meth:`pump`."""

    def __init__(
        self,
        pack_dir: Path,
        global_scenery: Path | None,
        overlays: Path | None,
        grids: Iterable[Path],
        log: Callable[[str], None] = print,
        make_pool: Callable[[int], Any] | None = None,
        prefs: Path | None = None,
        gain: float | None = None,
        lights: bool = True,
    ):
        self.pack_dir = pack_dir
        self.global_scenery = global_scenery
        self.overlays = overlays
        self.grids = list(grids)  # where the density grids are: each region's night folder
        self.log = log
        self.prefs = prefs  # X-Plane's Freeflight.prf: the time of a start
        self.gain = gain  # the lights' strength (orthostudio.live.night.LIGHTS); None: GAIN
        # off in the settings while the pack's terrain files still name night textures (turned off
        # while X-Plane ran, region.set_night): none made, those there counted and followed
        self.lights = lights
        # the level each night texture holding lights was made from (kept in the textures'
        # record): what came back from its job; gone once put back black with none running
        # (Windows refusing it: still lit). Counted with those being made (:meth:`counted`)
        self.lit: dict[str, int] = {}
        self.team = 1  # the processes at a start at night: Live's encoders (the streamer's)
        self.top_of: Callable[[str], int] = lambda name: 0  # a file's sharpest level (streamer's)
        self.night = False
        self.lock = threading.Lock()
        self.wanted: dict[str, int] = {}  # name: the level its night is wanted from
        self.running: dict[str, int] = {}
        self.made: dict[str, int] = {}
        self.changed: set[str] = set()  # made neutral or coarser while its night was being made
        self.tries: dict[str, int] = {}
        self.failed = 0
        self.seconds = 0.0
        self.broken = False  # processes lost too often: no more night for the flight
        self.closed = False
        self._make_pool = make_pool or _pool
        self._pool: Any = None
        self._size = 0
        self._jobs: dict[Any, int] = {}  # each pool's jobs not ended yet
        self._ended: list[tuple] = []  # (future, its names and levels, its pool): pump reads them
        self._lost: list[float] = []
        self._pumped = 0.0
        self.roads_cap = ROADS_KEPT_MB * 10**6  # the streamer's share for them (ROADS_SHARE)
        self._pruned = 0.0
        self._at: tuple[float, float] | None = None  # where the aircraft is
        self._centres: dict[str, tuple[float, float] | None] = {}
        self._dsfs: dict[tuple[int, int], Path | None] = {}
        self._pool_names: NightPool | None = None
        # a start: where, X-Plane's squares round it, the nights it waits for
        self.start_at: tuple[float, float] | None = None
        self._window: set[tuple[int, int]] | None = None
        self._start_set: set[str] | None = None
        self._waited_since: float | None = None
        self.gave_up = False
        self.waiting = False  # the start waits for its photos or its night textures: the team
        # X-Plane held in flight for its texture quality raised (streamer.Sharpening): the team
        # too, X-Plane waiting for the night textures of the sharper levels
        self.rush = False

    # --- what the streamer says ------------------------------------------------------------

    def want(self, name: str, level: int) -> None:
        """``name``'s night wanted from ``level`` (the streamer's, from 4096 px): X-Plane counts a
        file's levels from its top, so its night is counted so too, or the night of a 2048 px
        file was a hole, black, where X-Plane reads it (the review of 2026-11-11)."""
        level = max(0, level - max(0, self.top_of(name)))
        with self.lock:
            if self.made.get(name, 99) <= level:
                return
            if name not in self.changed and self.running.get(name, 99) <= level:
                return  # the one being made does
            self.wanted[name] = min(level, self.wanted.get(name, 99))

    def built(self, name: str, level: int) -> None:
        """The streamer built ``name`` at ``level``: its night follows, if it is night."""
        if self.night:
            self.want(name, level)

    def tick(
        self,
        at: tuple[float, float] | None,
        when: tuple[float, float] | None,
        built: Callable[[], dict[str, int]],
    ) -> None:
        """Where the aircraft is, or the start, and X-Plane's day of the year and UTC hours there
        (``when``, None when not known): when night comes, every texture built is wanted (the
        nearest first). The caller gives the time that is right: in flight X-Plane's own; at a
        start the one the plugin read in X-Plane as it loads it (orthostudio.live.gate), never
        the one X-Plane sends meanwhile, the flight before's (it took a start at night chosen in
        flight for day)."""
        if at is not None:
            self._at = at
        if when is None or self._at is None:
            return
        solar = (when[1] + self._at[1] / 15.0) % 24.0  # UTC to the aircraft's solar time
        self._set_night(night_at(when[0], solar, self._at[0]), built, "in X-Plane")

    def start(
        self, lat: float, lon: float, built: Callable[[], dict[str, int]] | None = None
    ) -> None:
        """A start planned there: night there by the time X-Plane saved for it, its night textures
        then made before X-Plane goes on, those of X-Plane's squares round it (:meth:`start_left`).
        Live may learn of a start before X-Plane saves its time, and build round it by the time of
        the flight before: at LSGG, 8,744 textures built by a day's time got no night when X-Plane
        then gave 23:30 (the user's flight, 2026-11-10)."""
        self.start_at = (lat, lon)
        self._window = xplane_tiles(lat, lon)
        self._start_set, self._waited_since, self.gave_up = None, None, False
        self.waiting = True
        saved = _solar_at_start(self.prefs) if self.prefs is not None else None
        if saved is not None:
            self._set_night(night_at(*saved, unsure_h=START_UNSURE_H), built, "at the start")

    def arrived(self) -> None:
        """The start is over (X-Plane flies there, or went elsewhere: the streamer's ``arrived``):
        one process again."""
        self.start_at = self._window = self._start_set = None

    @property
    def start_night(self) -> bool:
        """A start that waits for its night textures: at night there, its processes working, not
        given up (:meth:`start_left`); the start's bar counts them only then."""
        return self.start_at is not None and self.night and not self.gave_up and not self.broken

    def start_left(self, day_left: int = 0) -> int:
        """Night textures still to make which a start waits for, as for its photos (0 by day): those
        of X-Plane's squares round it (its whole pack's, other regions' too, 11,142 at a Paris
        start, the review of 2026-11-11), of the textures built when its photos were (those built
        after, farther, are made in flight: 27 s more at LSGG, 2026-11-11). ``day_left``: the
        start's day textures still to build; once none is, :data:`NIGHT_WAIT_S` at most."""
        if not self.start_night:
            return 0
        with self.lock:
            pending = [n for n in {*self.wanted, *self.running} if self._in_window(n)]
            if day_left > 0:
                self._start_set = None
                left = len(pending)
            else:
                if self._start_set is None:
                    self._start_set = set(pending)
                left = sum(1 for n in self._start_set if n in self.wanted or n in self.running)
        self.waiting = left > 0 or day_left > 0
        if left == 0 or day_left > 0:
            self._waited_since = None
            return left
        now = time.monotonic()
        if self._waited_since is None:
            self._waited_since = now
        if now - self._waited_since > NIGHT_WAIT_S:
            self.gave_up, self.waiting = True, False
            self.log(
                f"  the start waits no more for its night textures: {left} not made in "
                f"{NIGHT_WAIT_S:.0f} s, made in flight"
            )
            return 0
        return left

    def left_of(self, names: Iterable[str]) -> int:
        """Of ``names``, those whose night texture is still to make, or being made, while it is
        night (0 by day, or with the night given up for the flight): what X-Plane, held for its
        texture quality raised, waits for after their day textures (streamer.sharpen_count).
        A night that failed twice is not wanted any more: it counts no more."""
        if not self.night or self.broken or self.closed:
            return 0
        with self.lock:
            return sum(1 for n in names if n in self.wanted or n in self.running)

    def back_to_black(self, name: str) -> None:
        """``name`` made neutral or coarser: its night as its day file (:meth:`_black`), made again
        if it is wanted. One being made meanwhile is not counted made, and is put back so once
        made (:meth:`pump`): counted at the level it was given, a night 1024 px wide stayed over a
        texture made 256 px (the review of 2026-11-11)."""
        with self.lock:
            self.made.pop(name, None)
            self.wanted.pop(name, None)
            if name in self.running:
                self.changed.add(name)
        self._black(name)

    # --- the one rule ------------------------------------------------------------------------

    def pump(self) -> None:
        """At each turn of the streamer's loop, X-Plane running or not yet: what the processes
        made recorded; as many processes as the moment wants (a team while a start at night waits,
        from its first texture built until it waits for none; one in flight; none by day); and
        two jobs a process, a few
        textures of a square each, the nearest first. Only here: jobs given from their pool's own
        thread blocked it for good when one of its processes died (Python 3.14, the review of
        2026-11-11)."""
        now = time.monotonic()
        if now - self._pumped < 0.25:
            return
        self._pumped = now
        self._collect()
        if now - self._pruned > 60.0:  # the roads kept within their share
            self._pruned = now
            prune_roads(self.pack_dir / ROADS_FOLDER, self.roads_cap)
        size = 0
        if self.night and not self.broken and not self.closed:
            # the team while the start waits, not until X-Plane flies there: kept after its night
            # textures were made, it held its memory while X-Plane loaded, or for good after a
            # Prepare never flown (the review of 2026-11-11); and while X-Plane is held in flight
            # for its texture quality raised (:attr:`rush`)
            starting = (self.start_at is not None and self.waiting) or self.rush
            size = min(max(1, self.team), TEAM_MAX) if starting else 1
        if self._pool is not None and self._size != size and not self._jobs.get(self._pool):
            self._pool.shutdown(wait=False)  # its processes end, their memory with them
            self._jobs.pop(self._pool, None)
            self._pool = None
        if self._pool is None and size:
            try:
                self._pool, self._size = self._make_pool(size), size
            except OSError as e:  # the fork server did not start
                self._lost_one(None, e)
                return
            if size > 1:
                self.log(f"  {size} processes make the night textures X-Plane waits for")
        if self._pool is not None and self._size == size:
            self._give(size)

    def _collect(self) -> None:
        with self.lock:
            ended, self._ended = self._ended, []
        black: list[str] = []
        for fut, batch, pool in ended:
            try:
                results = fut.result()
            except BaseException as e:  # its pool lost or closed: the textures given again
                results = None
                if not self.closed:
                    self._lost_one(pool, e)
            with self.lock:
                self._ended_job(pool)
                for i, (name, level) in enumerate(batch):
                    self.running.pop(name, None)
                    said, lit = (None, False) if results is None else results[i][1:]
                    if name in self.changed:  # neutral or coarser meanwhile: as its day file
                        self.changed.discard(name)
                        black.append(name)
                    elif said is None:
                        self.wanted[name] = min(level, self.wanted.get(name, 99))
                    elif isinstance(said, str):
                        self._failed(name, level, said)
                    else:
                        self.made[name] = min(level, self.made.get(name, 99))
                        self.tries.pop(name, None)
                        self.seconds += said
                        if lit:  # its file written again: what it holds now
                            self.lit[name] = level
                        else:  # the black name, which takes nothing
                            self.lit.pop(name, None)
        for name in black:
            with contextlib.suppress(OSError):  # black again at its next eviction
                self._black(name)

    def _ended_job(self, pool: Any) -> None:
        """One job of ``pool`` less, ``self.lock`` held: a pool no longer used forgotten with its
        last."""
        left = self._jobs[pool] = self._jobs.get(pool, 0) - 1
        if left <= 0 and pool is not self._pool:
            del self._jobs[pool]

    def _failed(self, name: str, level: int, error: str) -> None:
        """A night texture that failed, ``self.lock`` held: given again once, then left."""
        self.failed += 1
        tries = self.tries[name] = self.tries.get(name, 0) + 1
        if self.failed <= 5:
            again = "tried again" if tries < TRIES else "left for this flight"
            self.log(f"  the night texture of {name} failed ({error}): {again}")
        if tries < TRIES:
            self.wanted[name] = min(level, self.wanted.get(name, 99))

    def _lost_one(self, pool: Any, why: BaseException) -> None:
        """A process lost (the memory short, or it crashed): its pool closed, a new one made at
        the next turn; past :data:`LOST_MAX` in a minute, no more night for the flight."""
        if pool is not None and pool is self._pool:
            self._pool = None
            pool.shutdown(wait=False, cancel_futures=True)
        elif pool is not None:
            return  # its pool is gone already: one loss, said once
        now = time.monotonic()
        self._lost = [t for t in self._lost if now - t < 60.0] + [now]
        if len(self._lost) > LOST_MAX:
            self.broken = True
            self.log(f"  the night's processes keep stopping ({why}): no more night textures")
        else:
            self.log(f"  a night process stopped ({type(why).__name__}): started again")

    def _give(self, size: int) -> None:
        """Two jobs a process, each the textures of one square up to :data:`JOB_WORK`, the nearest
        first: never a texture already being made (two at once wrote it in either order, a coarse
        one last left it black where X-Plane looks)."""
        pool = self._pool
        with self.lock:
            room = 2 * size - self._jobs.get(pool, 0)
            if room <= 0 or not self.wanted:
                return
            free = {n: lv for n, lv in self.wanted.items() if n not in self.running}
        # sorted outside the lock: the streamer's threads want textures meanwhile
        at = self._at or self.start_at
        names = sorted(free, key=lambda n: self._order(n, free[n], at))
        with self.lock:
            order: list[list[tuple[str, int]]] = []
            filling: dict[tuple[int, int] | None, tuple[list[tuple[str, int]], list[float]]] = {}
            open_jobs = 0  # jobs that may take more: none, and no room, the rest waits
            for name in names:
                if len(order) >= room and not open_jobs:
                    break  # 28,800 wanted at nightfall went through the lock to the end each time
                level = self.wanted.get(name)
                if level is None or name in self.running:  # made neutral meanwhile
                    continue
                centre = self._centre(name)
                square = (math.floor(centre[0]), math.floor(centre[1])) if centre else None
                job, work = filling.get(square, (None, [0.0]))
                if job is None or work[0] >= JOB_WORK:
                    if len(order) >= room:
                        continue
                    job, work = [], [0.0]
                    order.append(job)
                    filling[square] = (job, work)
                    open_jobs += 1
                job.append((name, level))
                work[0] += _work(level)
                if work[0] >= JOB_WORK:
                    open_jobs -= 1
            for batch in order:
                for name, level in batch:
                    del self.wanted[name]
                    self.running[name] = level
            self._jobs[pool] = self._jobs.get(pool, 0) + len(order)
        apart = size == 1  # one process lives the whole flight: a DSF read apart (_read_apart)
        for i, batch in enumerate(order):
            jobs = [self._job(name, level, apart) for name, level in batch]
            try:
                fut = pool.submit(night_batch, jobs)
            except Exception as e:  # its pool lost: what is left wanted again
                with self.lock:
                    for _ in order[i:]:
                        self._ended_job(pool)
                    for rest in order[i:]:
                        for name, level in rest:
                            self.running.pop(name, None)
                            self.wanted[name] = min(level, self.wanted.get(name, 99))
                self._lost_one(pool, e)
                return
            fut.add_done_callback(lambda f, b=batch, p=pool: self._end(f, b, p))

    def _end(self, fut: Future, batch: list[tuple[str, int]], pool: Any) -> None:
        """A job ended: said, nothing more (its pool's own thread)."""
        with self.lock:
            self._ended.append((fut, batch, pool))

    # --- helpers ------------------------------------------------------------------------------

    def _set_night(self, night: bool, built: Callable[[], dict[str, int]] | None, why: str) -> None:
        night = night and self.lights
        was, self.night = self.night, night
        if night and not was and built is not None:
            textures = built()
            for name, level in textures.items():
                self.want(name, level)
            self.log(f"  night {why}: its towns lit first, {len(textures)} textures built")

    def _in_window(self, name: str) -> bool:
        """Whether ``name`` lies in X-Plane's squares round the start (every name, no start)."""
        if self._window is None:
            return True
        centre = self._centre(name)
        return centre is not None and (
            (math.floor(centre[0]), math.floor(centre[1])) in self._window
        )

    def _black(self, name: str) -> None:
        """``name``'s night as its day file, read from the disk when it is put back: black where
        the day file is (X-Plane may read it), gone with it. Told beforehand, a square made
        meanwhile was left naming a night deleted (the review of 2026-11-11)."""
        from .names import NightPool, night_name
        from .region import NIGHT_FOLDER

        path = self.pack_dir / NIGHT_FOLDER / night_name(name)
        if path.exists():
            if (self.pack_dir / "textures" / name).exists():
                if self._pool_names is None:
                    self._pool_names = NightPool(self.pack_dir)
                self._pool_names.replace(0, path)
            else:
                path.unlink(missing_ok=True)
        with self.lock:  # once done (refused, Windows: it still holds its lights), none running
            if name not in self.running:  # (one running writes its lights after: pump then)
                self.lit.pop(name, None)

    def _job(self, name: str, level: int, apart: bool) -> tuple:
        tex = parse_texture_name(name)
        squares = sorted(tiles_of(tex)) if tex is not None else []
        dsfs = [str(p) for p in (self._dsf(lat, lon) for lat, lon in squares) if p is not None]
        lat, lon = (math.floor(v) for v in tex.center) if tex is not None else (0, 0)
        file = f"{square_name(lat, lon)}.png"
        grid = next((d / file for d in self.grids if (d / file).is_file()), None)
        return (
            str(self.pack_dir),
            name,
            level,
            dsfs,
            str(grid) if grid else None,
            self.gain,
            apart,
        )

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

    def _order(self, name: str, level: int, at: tuple[float, float] | None) -> tuple:
        """The start's first (X-Plane's squares round it), then the near ones (:data:`NEAR_LEVEL`),
        nearest the aircraft first; then the far ones a square at a time, the nearest square first:
        given nearest first, they went round the aircraft from square to square and each needed
        its square's roads again (3,951 reads for LSGG's 8,535 far textures, against 136)."""
        near = self._distance(name, at)
        centre = self._centre(name)
        first = not self._in_window(name)
        if level <= NEAR_LEVEL or centre is None or at is None:
            return (first, level > NEAR_LEVEL, 0.0, near)
        square = (math.floor(centre[0]) + 0.5, math.floor(centre[1]) + 0.5)
        return (first, True, round(_degrees(square, at), 6), near)

    def _centre(self, name: str) -> tuple[float, float] | None:
        if name not in self._centres:
            tex = parse_texture_name(name)
            self._centres[name] = tex.center if tex is not None else None
        return self._centres[name]

    def _distance(self, name: str, at: tuple[float, float] | None) -> float:
        centre = self._centre(name)
        if centre is None or at is None:
            return 0.0
        return _degrees(centre, at)

    def counted(self) -> dict[str, int]:
        """The night textures the disk counts, at the sharpest level each may hold: those made
        with lights, and those being made, which may be written whatever happens to Live (a job
        that fails writes nothing). Noted when given instead, one that failed stayed counted for
        good (the review of 2026-11-11)."""
        with self.lock:
            out = dict(self.lit)
            for name, level in self.running.items():
                out[name] = min(level, out.get(name, level))
        return out

    def close(self) -> None:
        with self.lock:
            self.closed = True
            made, failed = len(self.made), self.failed
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        if made:
            self.log(f"  {made} night textures made")
        if failed > 5:
            self.log(f"  {failed} night textures failed (the first five are said above)")
