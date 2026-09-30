# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Stream ortho textures to X-Plane as real files: follow the aircraft, build ahead, rename() in.

Every two seconds the planner takes the aircraft's position, track and speed (X-Plane's UDP;
from its last two positions when X-Plane says no speed while it moves:
:func:`motion_from_positions`) and, for every texture within 300 km, the level X-Plane will read of
it over the next minutes: the rings of ``orthostudio.live.rings`` applied to its distance from the
path ahead. Textures built coarser than that are queued, the most urgent first: those nearest to
the ring where X-Plane will want them.
A builder thread downloads their pieces (the chunk cache, then the downloader), encodes them in a
process pool (``orthostudio.live.pools``: forked by a small process of their own, started again if
it loses one) and replaces each file with rename(). X-Plane opens a texture again for every sharper
level, so it reads the new file at its next ring.

At a flight's start, OrthoStudio XP Live's X-Plane plugin holds X-Plane on its loading screen until
everything it will read there is built (``orthostudio.live.gate``). The streamer starts building
when the user clicks Start Flight. Without the plugin, or after a jump it did not see coming,
X-Plane loaded the area before its textures were there: once everything it may already show is
built, the streamer asks it to reload the scenery.

External views take the camera away from the aircraft, and X-Plane reads what is in the files at
once. So every ring round the aircraft and its path is widened by ``reach_km``, the distance the
camera may go, even in the cockpit: an external view comes without warning, a zoom takes the
camera out faster than any build, and X-Plane never reads a level again (the reach only in
external views, 2026-09-29, would have let a quick zoom out find the neutral colour). The camera
itself, where it is, counts in three dimensions from its own height, as X-Plane measures, with no
reach round it.

One zoom above the mesh's (AutoOrtho's max_zoom 17, or 19 near airports), a texture's file is
8192 px from the pack's layout on (``orthostudio.live.region``), and X-Plane reads it as such from
the start. Its photos are built as those of a 4096 px file, at the same distances: X-Plane, which
counts a file's levels from its top, reads each level a ring earlier, and finds there its photos
already (the camera's reach) or, for the far levels, their copy (``orthostudio.live.texture``). The
8192 px level itself gets its photos, one zoom above the mesh's, a quarter farther than X-Plane
reads it (12 km at ZL16), all around the aircraft, its path ahead and the camera, in three
dimensions as X-Plane measures, so not from high up. At a start, X-Plane waits for all of them. In
flight, photos late as X-Plane is about to read them get the 4096 px level copied up in their
place first (:attr:`Streamer.copied_up`): the ortho as at ZL16, never the neutral colour. No camera
reach for that level: a camera taken farther, near the ground, may find the neutral colour there.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import queue
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import dds, machine, pools
from .aoregions import REGION_NAMES, REGION_NAMES_FR, region_of
from .cache import IMAGE, NO_IMAGERY, ChunkCache, Key
from .cameralog import AWAY_M
from .evict import EVICT_SAFE_KM, TARGET, XPlaneWindow, pick, tiles_of, usage, xplane_tiles
from .flightplan import MOVING_MS, ROUTE_LOOKAHEAD_MIN, FlightPlan, last_plan
from .geo import bearing_deg, haversine_m, parse_texture_name
from .mapdata import route_points
from .names import KEEP, RING, Entry, Names, RegionTextures, squares_round
from .pack import MARKER, load_built
from .region import (
    NEUTRAL_LEVEL,
    REGION_VERSION,
    Zooms,
    ensure_neutral,
    neutral_file,
    region_version,
    sources_of_pack,
)
from .rings import LOAD_LEVEL, MIN_LEVEL, finest_readable, ring_km, screen_scale, top_level_for
from .route import KM_PER_DEG, Route
from .texture import parent_keys, piece_keys, pieces_of, worker_ignores_ctrl_c

PLACEHOLDER_KM = 300.0  # every texture within this has at least its 256 px level
LOAD_KM = (
    230.0  # X-Plane loads a tile's DSF, reading 256 px, from about this far (probe: 130-230 km)
)
# The path ahead the planner looks along, straight on the track. 90 seconds keep about two minutes
# before X-Plane reads each level, at any speed; five minutes made a turn sweep 38 km of path all
# around the aircraft: 233 textures at 8192 px where X-Plane read 133, and the encoding cost frames
# (the mountain test pair of 2026-09-28; simulated on its path, 329 built for 145, none missed).
LOOKAHEAD_MIN = 1.5
PLAN_ASK_EVERY_S = 60.0  # SimBrief is asked for the pilot's last plan at most this often
JUMP_KM = 20.0  # a position this far from the last one is a new start: a teleport, a new flight
PLAN_EVERY_S = 2.0
REPORT_EVERY_S = 1.0  # what the Live window is told, this often
STALL_S = 60.0  # pieces awaited this long and none arriving: no internet, X-Plane is let go
# A piece of a texture X-Plane waits for (urgency <= 0), awaited this long: the texture is built
# without it, from the zoom below, as a piece that failed, and built again later with it. At a
# start at LSGG, one piece Bing never sent held X-Plane 20 s, the downloader's four tries.
STUCK_S = 10.0
PATCH_RETRY_S = 60.0  # a texture built without such pieces is built again after this, doubled
PATCH_RETRY_MAX_S = 1800.0  # each time they still do not come, up to this
LEVELS = range(MIN_LEVEL, LOAD_LEVEL + 1)  # -1 (8192 px) to 4 (256 px)
BATCH_PIECES = 1024
WINDOW_PIECES = 1024  # pieces asked for and not answered yet, across textures
# pieces the builder holds for textures on their way (their photos in memory): past this it takes
# only textures X-Plane may be showing already. It counted textures until 2026-09-30: textures that
# could not be encoded while X-Plane loaded scenery filled it, and urgent ones waited behind them
# (Norway flights: 49 and 103 late 8192 px levels given the copy of their 4096 px one)
HELD_PIECES = 8 * BATCH_PIECES
EVICT_EVERY_S = 10.0
# textures encoded at once while X-Plane flies, all workers while it waits: by default the share of
# a computer of 14 cores, each computer's own from its cores (orthostudio.live.machine)
FLIGHT_ENCODES = 4
# 8192 px textures encoded at once at most, each in a process of its own, which ends with it and
# gives its memory back: twelve workers kept 14 GB after such textures, and a user's Mac, short of
# memory, froze X-Plane (2026-09-28). By default a computer of 48 GB's, each one's own from its
# memory.
SHARP_WORKERS = 4
# The photos of the levels this sharp and sharper (4096 and 8192 px) are not kept in the imagery
# cache: the texture built holds them, and they took most of what the cache wrote (2026-09-28).
# Built again after being made coarser, a texture downloads them again, as the first time.
UNCACHED_FROM = 0
COPY_UP_AT = 1.1  # an 8192 px level not built yet this near where X-Plane reads it: late
STUTTER_MS = 100  # a frame of X-Plane this long, as its plugin measures it, is logged as a stutter
# textures made coarser per round, a round every EVICT_EVERY_S: small rounds, never while X-Plane
# loads scenery (yielding), since a round of 500 files rewritten met a hitch of 387 ms (2026-09-28)
EVICT_BATCH = 50
FRESH_S = (
    2.0  # X-Plane's position is live if it came this recently: X-Plane is running, not loading
)
ELSEWHERE_S = 10.0  # X-Plane flying away from an expected start this long: it is not coming there
LOADING_S = 120.0  # a click on Start Flight this recent: X-Plane is loading that flight
DATAREFS = [
    ("lat", "sim/flightmodel/position/latitude"),
    ("lon", "sim/flightmodel/position/longitude"),
    ("alt_m", "sim/flightmodel/position/elevation"),
    ("gs_ms", "sim/flightmodel/position/groundspeed"),
    ("trk_deg", "sim/flightmodel/position/hpath"),
    ("px", "sim/flightmodel/position/local_x"),
    ("py", "sim/flightmodel/position/local_y"),
    ("pz", "sim/flightmodel/position/local_z"),
    ("vx", "sim/graphics/view/view_x"),
    ("vy", "sim/graphics/view/view_y"),
    ("vz", "sim/graphics/view/view_z"),
    ("agl_m", "sim/flightmodel/position/y_agl"),
    ("win_w", "sim/graphics/view/window_width"),  # X-Plane's image: what the rings scale with
    ("fov", "sim/graphics/view/field_of_view_deg"),
    ("scenery_loading", "sim/graphics/scenery/async_scenery_load_in_progress"),
    # the aircraft's true heading, for the window's map: as X-Plane's, where its track leaves it
    # (wind, the ground, a position set from outside that gives it no speed: a user, 2026-09-29)
    ("hdg_deg", "sim/flightmodel/position/psi"),
]
# X-Plane loading scenery in flight (its flag above), and this long after: Live pauses, nothing
# downloaded, encoded, copied up or made coarser, only the squares' files X-Plane needs made (their
# release waits too). Over Norway, Live's work during X-Plane's loads delayed the third tile of a
# column by 1 to 7.5 minutes in each of five flights; paused, none came more than 24 s after the
# first, near X-Plane alone's 19 s (2026-09-30). Until then Live held back only its encoding, for
# 30 s, after the mountain test pair's hitches as X-Plane loaded DSFs (Live 146 and 387 ms while it
# encoded and evicted, AutoOrtho none over 40 ms, 2026-09-28). At most YIELD_MAX_S of each load,
# so that one X-Plane would not end for another reason holds Live no longer; counted again from
# each move of X-Plane's window while its flag stays up: one flag of 519 s over three columns, Live
# back at work after 120 s, the second column's last tile came after 237 s (2026-09-30).
YIELD_AFTER_S = 5.0
YIELD_MAX_S = 120.0
# the system's word on its memory, read this often in flight (machine.memory_pressure): short of
# it, one texture encoded at once and no 8192 px one ahead of time; very short, only what X-Plane
# reads now
PRESSURE_EVERY_S = 1.0


@dataclass
class Fix:
    lat: float
    lon: float
    alt_m: float = 0.0
    gs_ms: float = 0.0
    trk_deg: float = 0.0
    cam_lat: float | None = None  # the camera, which external views take away from the aircraft
    cam_lon: float | None = None
    agl_m: float = 0.0  # the aircraft's height above the ground
    cam_agl_m: float | None = None  # the camera's


def camera_position(lat: float, lon: float, plane_xyz, view_xyz) -> tuple[float, float]:
    """Where the camera is, from the aircraft's position and both in X-Plane's OpenGL frame
    (x east, y up, z south, metres)."""
    east = view_xyz[0] - plane_xyz[0]
    north = -(view_xyz[2] - plane_xyz[2])
    k = KM_PER_DEG * 1000.0
    return lat + north / k, lon + east / (k * max(0.01, math.cos(math.radians(lat))))


# faster between two positions: a jump, not a flight (Mach 3)
MAX_MOVING_MS = 1000.0


def motion_from_positions(before: Fix | None, before_at: float, fix: Fix, at: float) -> None:
    """X-Plane saying no speed while the aircraft moves (its position set from outside: a drone,
    a replay): ``fix``'s ground speed and track taken from its last two positions, so that the path
    ahead is planned still. In the measurement flight X-Plane said 0 kt at 600 kt, and the ZL17
    photos came under the aircraft only (the user: "le vert devant l'avion arrive tard",
    2026-09-29)."""
    if before is None or fix.gs_ms > MOVING_MS or at - before_at < 0.5:
        return
    speed = haversine_m(before.lat, before.lon, fix.lat, fix.lon) / (at - before_at)
    if MOVING_MS < speed < MAX_MOVING_MS:
        fix.gs_ms = speed
        fix.trk_deg = bearing_deg(before.lat, before.lon, fix.lat, fix.lon)


def ahead(fix: Fix, minutes: float) -> tuple[float, float]:
    """Where the aircraft will be in ``minutes`` on its track at its ground speed."""
    d = max(fix.gs_ms, 0.0) * minutes * 60.0 / 1000.0
    t = math.radians(fix.trk_deg)
    lat = fix.lat + d * math.cos(t) / KM_PER_DEG
    lon = fix.lon + d * math.sin(t) / (KM_PER_DEG * max(0.01, math.cos(math.radians(fix.lat))))
    return lat, lon


@dataclass
class _Job:
    """One texture on its way: pieces, then parents for the pieces without imagery, then
    encoding."""

    name: str
    level: int
    urgency: float
    keys: list[Key]
    pieces: dict[Key, bytes | None]
    missing: set[Key]
    parents: dict[Key, bytes | None] = field(default_factory=dict)
    parents_missing: set[Key] = field(default_factory=set)
    stage: str = "pieces"
    patched: set[Key] = field(default_factory=set)  # pieces that did not come: the zoom below's


class TextureIndex:
    """The region's textures by the 1-degree tile of their centre: (name, lat, lon, zoom, kinds,
    source), the last two the files its region has for it (``orthostudio.live.names``). From
    names, those two are 0."""

    def __init__(self, textures):
        self.by_tile: dict[tuple[int, int], list[Entry]] = defaultdict(list)
        for item in textures:
            if isinstance(item, str):
                tex = parse_texture_name(item)
                if tex is None:
                    continue
                lat, lon = tex.center
                item = (item, lat, lon, tex.zoom, 0, 0)
            self.by_tile[(math.floor(item[1]), math.floor(item[2]))].append(item)

    def __len__(self) -> int:
        return sum(len(v) for v in self.by_tile.values())

    def around(self, lat: float, lon: float, km: float) -> Iterator[Entry]:
        dlat = km / KM_PER_DEG + 1
        dlon = km / (KM_PER_DEG * max(0.05, math.cos(math.radians(min(89.0, abs(lat) + dlat))))) + 1
        for tl in range(math.floor(lat - dlat), math.floor(lat + dlat) + 1):
            for tn in range(math.floor(lon - dlon), math.floor(lon + dlon) + 1):
                yield from self.by_tile.get((tl, tn), ())


class Streamer:
    def __init__(
        self,
        pack_dir: Path,
        cache: ChunkCache,
        downloader_factory: Callable,
        finest_quality: int,
        max_zoom: int = 16,
        max_zoom_airports: int = 18,
        margin: float = 1.25,
        workers: int = 4,
        xplane=None,
        provider: str = "BI",
        disk_budget: int = 20 * 10**9,
        cache_budget: int = 10 * 10**9,
        reach_km: float = 0.0,
        log: Callable[[str], None] = print,
        covered: Collection[tuple[int, int]] = (),
        simbrief_user: str = "",
        installed_regions: Callable[[], Collection[str]] = frozenset,
        tint_8192: bool = False,
        flight_encodes: int = FLIGHT_ENCODES,
        sharp_builds: int = SHARP_WORKERS,
        memory_pressure: Callable[[], int] = machine.memory_pressure,
    ):
        """``downloader_factory(on_piece)`` makes the downloader
        (``orthostudio.live.downloader.Downloader``) the
        builder feeds: ``submit(key, priority)``, ``close()``, and its counters."""
        self.pack_dir = pack_dir
        self.cache = cache
        self.downloader_factory = downloader_factory
        self.downloader = None
        self.provider = provider
        self.from_cache = 0
        self.pieces_pending = 0
        self.finest_quality = finest_quality
        self.max_zoom = max_zoom
        self.max_zoom_airports = max_zoom_airports
        self.margin = margin
        self.reach_km = reach_km  # how far from the aircraft an external view may take the camera
        # textures an external view's camera reached before Live built the level X-Plane reads
        # there: the neutral colour on the screen, or a far level's copy (_read_now)
        self.outran: dict[str, int] = {}
        self.workers = workers
        # this computer's share while X-Plane flies, and of 8192 px builds (live.machine)
        self.flight_encodes = flight_encodes
        self.sharp_builds = sharp_builds
        self._memory_pressure = memory_pressure
        self.pressure = machine.NORMAL  # the system's word on its memory, in flight
        self._pressure_at = -PRESSURE_EVERY_S
        self.xp = xplane  # anything with .command(name): the UDP link, or a test's stand-in
        self.log = log
        self.tint_8192 = tint_8192  # the test: the 8192 px levels built pink (texture.tinted)
        # X-Plane flies (not waiting on a start, not away): the encoders leave it its cores
        self.flying = False
        self.yield_until = 0.0  # monotonic: Live holds back until then (x_plane_loads)
        self._load_since: float | None = None
        self._load_long = False  # the pause going on passed YIELD_MAX_S: said once
        self._load_cut = False  # the load going on outlasted a pause: said at its end
        self._hold_from: float | None = None  # the pause's start: the flag up, or the window moved
        self._window = XPlaneWindow()  # where X-Plane's window is (follow_window)
        self.encoding_now = 0  # textures being encoded, for the log of a stutter
        self.ready_now = 0  # textures with all their photos, waiting for an encoder
        # 8192 px levels whose photos are late: the 4096 px level copied up into them meanwhile
        # (orthostudio.live.texture.copy_up), so that X-Plane never reads the neutral colour there
        self.to_copy: set[str] = set()
        self.copied_up: set[str] = set()
        self.meta = load_built(pack_dir)
        self.built: dict[str, int] = dict(self.meta.get("textures", {}))
        self.built_version = 0  # one more at each change of built: the window's map
        # built without pieces that did not come: when to build them again, and how many times
        self.patchy: dict[str, tuple[float, int]] = {
            n: (0.0, 0) for n in self.meta.get("patchy", []) if n in self.built
        }
        self.seen: dict[str, float] = dict(
            self.meta.get("seen", {})
        )  # when a built texture was last in reach
        self.disk_budget = disk_budget
        self.cache_budget = cache_budget
        self._cache_size = 0  # the imagery cache's disk, as the last eviction round measured it
        self.evicted = 0
        self.cache_freed = 0
        # the region's textures, from its regions' terrain folders: the pack makes their files per
        # square round the aircraft (orthostudio.live.names). A pack of before, not made over yet
        # (X-Plane was running), has every texture's file already.
        self.sources = sources_of_pack(pack_dir)
        per_square = bool(self.sources) and region_version(pack_dir) >= REGION_VERSION
        region = RegionTextures(self.sources) if per_square else None
        self.index = TextureIndex(
            region.entries()
            if region is not None
            else (e.name for e in os.scandir(pack_dir / "textures") if e.name.endswith(".dds"))
        )
        others = region.others if region is not None else None  # the rest of it freed
        # the zoom set: each texture's file is laid out for it (region.relayout), smaller below the
        # mesh's zoom, 8192 px one zoom above
        self.zooms = Zooms(max_zoom, max_zoom_airports)
        self._tops: dict[str, int] = {}  # each texture's file size, once known (top_of)
        zooms = {entry[3] for textures in self.index.by_tile.values() for entry in textures}
        self.reads_8192 = {  # the zooms whose 8192 px level X-Plane reads, at its texture quality
            z for z in zooms if finest_readable(self.zooms.top(z), finest_quality) == MIN_LEVEL
        }
        tops = {self.zooms.top(z) for z in zooms}
        for top in tops:  # the neutral texture of every size a file may take
            ensure_neutral(pack_dir, top)
        # the pilot's last SimBrief plan, the path ahead while the aircraft is on it
        self.simbrief_user = simbrief_user.strip()
        self.flight_plan: FlightPlan | None = None
        self.on_route = False
        self._plan_asked = -math.inf
        # X-Plane's image, the last it said, from one run to the next: every ring scales with it
        self.screen: dict | None = None
        self.scale = 1.0
        screen = self.meta.get("screen")
        if isinstance(screen, dict):
            self.set_screen(screen.get("width"), screen.get("fov"), quiet=True)
        # squares whose ground X-Plane takes from the user's own tiles (orthostudio.live.covered):
        # a texture only there is never read, and never built
        self.theirs = self._theirs(set(covered))
        # where no region installed has a mesh: the region to install, for the window and X-Plane
        self.installed_regions = installed_regions
        self.notice: dict | None = None
        self._notice_square: tuple[int, int] | None = None
        self.lock = threading.Lock()
        self.queue: dict[str, tuple[float, int]] = {}  # name -> (urgency km, level)
        self.in_flight: dict[str, tuple[float, int]] = {}
        self.stop = threading.Event()
        self.read_early = False
        self.last_fix: Fix | None = None
        self.done = Counter()
        self.errors = 0
        self.crashed: BaseException | None = None
        self.last_progress = time.monotonic()  # a piece arrived, or none was awaited
        self._thread: threading.Thread | None = None
        # the files of the squares round the aircraft, a start and the camera (_want_squares)
        self.names: Names | None = None
        if others is not None:
            self.names = Names(
                pack_dir,
                self.index.by_tile,
                self.sources,
                self.top_of,
                self.lock,
                busy=self._holds,
                forget=self._forget_missing,
                log=log,
                others=others,
            )
            self.names.yielding = self.yielding
        self._points: dict[str, tuple[float, float]] = {}  # "aircraft", "camera", "start"

    # --- the squares' files -------------------------------------------------------------------

    def _holds(self, name: str) -> bool:
        """Under ``self.lock``: whether a texture is built, queued or building, its file never
        taken away with its square's (orthostudio.live.names)."""
        return name in self.built or name in self.queue or name in self.in_flight

    def _forget_missing(self, name: str) -> None:
        """Under ``self.lock``: a texture recorded as built whose file was gone (taken away by
        hand), its name made again from the neutral texture, is forgotten, so that the planner
        builds it again. Not one the builder wrote over that new name meanwhile, its file its own:
        at a start the names are made while the first textures are built, and 168 good ones were
        built twice (Norway, 2026-09-30)."""
        if name not in self.built:
            return
        if self.names is not None and not self.names.leads_to_neutral(name):
            return
        self.built.pop(name)
        self.seen.pop(name, None)
        self.patchy.pop(name, None)
        self.built_version += 1
        self.log(f"  {name} was built but its file is gone: built again")

    def _want_squares(self, fix: Fix, starting: bool) -> None:
        """The squares whose files the pack holds: X-Plane's window and a ring round the aircraft,
        the camera and a start X-Plane is loading, all kept until the flight is there
        (:meth:`arrived`); the rest a ring farther taken away (orthostudio.live.names)."""
        if self.names is None:
            return
        if starting:
            self._points["start"] = (fix.lat, fix.lon)
        else:
            self._points["aircraft"] = (fix.lat, fix.lon)
            if fix.cam_lat is not None and fix.cam_lon is not None:
                self._points["camera"] = (fix.cam_lat, fix.cam_lon)
            else:
                self._points.pop("camera", None)
        self._want((fix.lat, fix.lon))

    def _want(self, center: tuple[float, float]) -> None:
        points = list(self._points.values())
        self.names.want(squares_round(points, RING), squares_round(points, KEEP), center)

    def hold_at(self, lat: float, lon: float) -> None:
        """Where X-Plane says it loads the flight it holds (the plugin's HOLD): its squares made
        too, whatever start the click gave, which a start from the map or a saved situation may
        miss (the review of 2026-09-30)."""
        if self.names is not None:
            self._points["hold"] = (lat, lon)
            self._want((lat, lon))

    def arrived(self) -> None:
        """The start X-Plane loaded is where it flies now, or it did not go there: its squares are
        kept only as the aircraft's."""
        self._points.pop("start", None)
        self._points.pop("hold", None)

    def squares_ready(self, lat: float, lon: float) -> bool:
        """Whether the files of X-Plane's window round (``lat``, ``lon``) are made: a start's, for
        the plugin to let X-Plane go. True where the pack has every file (a pack of before), or
        where making them failed (the log says it; X-Plane is not held for ever)."""
        names = self.names
        if names is None or names.crashed is not None:
            return True
        return names.ready(squares_round([(lat, lon)], 0))

    # --- planning -----------------------------------------------------------------------------

    def refresh_flight_plan(self) -> None:
        """Ask SimBrief for the pilot's last plan, in a thread of its own, at most once a minute:
        when a flight starts, or X-Plane is found flying somewhere new (a new plan may have been
        made meanwhile). Used only while the aircraft flies it."""
        if not self.simbrief_user or time.monotonic() - self._plan_asked < PLAN_ASK_EVERY_S:
            return
        self._plan_asked = time.monotonic()

        def fetch() -> None:
            plan, why = last_plan(self.simbrief_user)
            if plan is None:
                self.log(f"  SimBrief: {why}; Live looks {LOOKAHEAD_MIN * 60:.0f} s straight ahead")
                return
            known = self.flight_plan
            self.flight_plan = plan
            if known is None or known.points != plan.points:
                self.log(
                    f"  SimBrief: your last plan, {plan.name}: Live follows it while the aircraft "
                    f"is on it"
                )

        threading.Thread(target=fetch, name="live-simbrief", daemon=True).start()

    def set_screen(self, width: float | None, fov: float | None, quiet: bool = False) -> None:
        """X-Plane's image, as it says it (``window_width``, ``field_of_view_deg``): the rings grow
        with it (:func:`orthostudio.live.rings.screen_scale`)."""
        if not width or not fov:
            return
        scale = screen_scale(width, fov)
        before, self.scale = self.scale, scale
        self.screen = {"width": round(width), "fov": round(fov, 1)}
        if not quiet and abs(scale - before) > 0.05:
            self.log(
                f"  X-Plane's image: {round(width)} px, field of view {fov:.0f} deg: X-Plane reads "
                f"4096 px up to {ring_km(0, 16) * scale:.0f} km (distances x{scale:.2f})"
            )

    def _theirs(self, covered: set[tuple[int, int]]) -> set[str]:
        """The textures whose every square is in ``covered``: X-Plane draws another pack there. A
        texture reaching into another square is built, for that square's sake."""
        out = set()
        for square in covered:
            for name, *_ in self.index.by_tile.get(square, ()):
                tex = parse_texture_name(name)
                south, west, north, east = tex.bounds
                squares = {
                    (lat, lon)
                    for lat in range(math.floor(south), math.ceil(north))
                    for lon in range(math.floor(west), math.ceil(east))
                }
                if squares <= covered:
                    out.add(name)
        return out

    def level_of(self, name: str) -> int:
        return self.built.get(name, NEUTRAL_LEVEL)

    def encode_cap(self) -> int:
        """Textures encoded at once: all the workers while X-Plane waits on a start or is away,
        this computer's share while it flies (``flight_encodes``), its cores left to it; one while
        the computer is short of memory (:meth:`memory_state`)."""
        if not self.flying:
            return self.workers
        if self.memory_state() >= machine.SHORT:
            return 1
        return min(self.workers, self.flight_encodes)

    def memory_state(self, now: float | None = None) -> int:
        """In flight, the system's word on its memory (``machine.memory_pressure``), read at most
        every PRESSURE_EVERY_S, a change logged; NORMAL while X-Plane waits on a start or is away,
        where nothing is drawn to stutter."""
        if not self.flying:
            self.pressure = machine.NORMAL
            return self.pressure
        now = time.monotonic() if now is None else now
        if now - self._pressure_at >= PRESSURE_EVERY_S:
            self._pressure_at = now
            level = self._memory_pressure()
            if level != self.pressure:
                self.log(
                    "  this computer is very short of memory: Live encodes only what X-Plane reads"
                    " now, one texture at a time"
                    if level >= machine.VERY_SHORT
                    else "  this computer is short of memory: Live encodes one texture at a time,"
                    " no 8192 px one ahead of time"
                    if level >= machine.SHORT
                    else "  memory back: Live encodes as this computer's share allows"
                )
                self.pressure = level
        return self.pressure

    def may_encode(self, urgency: float, level: int) -> bool:
        """Whether a texture ready to encode may start now: none while X-Plane loads scenery in
        flight (:meth:`yielding`); else what X-Plane reads already always, the rest not while the
        computer is very short of memory, an 8192 px level ahead of time not while it is short."""
        if self.yielding():
            return False
        if urgency <= 0:
            return True
        if self.pressure >= machine.VERY_SHORT:
            return False
        return not (level < 0 and self.pressure >= machine.SHORT)

    def x_plane_loads(self, loading: bool, now: float | None = None) -> None:
        """X-Plane's scenery load flag, in flight: Live pauses while it is up and YIELD_AFTER_S
        after, for at most YIELD_MAX_S from the flag going up or X-Plane's window moving
        (:meth:`follow_window`). The log says how long X-Plane loaded and whether Live paused all
        of it: the load's length alone read as Live's pause, 597 s where Live had held back 35
        (Norway, 2026-09-30)."""
        now = time.monotonic() if now is None else now
        if not (loading and self.flying):
            if self._load_since is not None:
                took = now - self._load_since
                self.log(
                    f"  X-Plane loaded scenery in {took:.0f} s: Live paused "
                    + (
                        f"at most {YIELD_MAX_S:.0f} s from each move of its window"
                        if self._load_cut
                        else "meanwhile"
                    )
                )
            self._load_since = self._hold_from = None
            self._load_long = self._load_cut = False
            return
        if self._load_since is None:
            self._load_since = self._hold_from = now
        if self._hold_from is not None and now - self._hold_from < YIELD_MAX_S:
            self.yield_until = now + YIELD_AFTER_S
        elif not self._load_long:
            self._load_long = self._load_cut = True
            self.log(
                f"  X-Plane still loads scenery {YIELD_MAX_S:.0f} s on: Live works again until "
                "its window moves"
            )

    def follow_window(self, fix: Fix, now: float | None = None) -> None:
        """In flight, X-Plane's window followed (orthostudio.live.evict.XPlaneWindow): moved while
        its load flag is still up, Live pauses again, YIELD_MAX_S from there."""
        now = time.monotonic() if now is None else now
        if not self.flying:
            self._window = XPlaneWindow()  # a start: taken again from where X-Plane loads
            return
        if self._window.follow(fix.lat, fix.lon) and self._load_since is not None:
            self._hold_from = now
            self._load_long = False
            self.yield_until = max(self.yield_until, now + YIELD_AFTER_S)

    def yielding(self) -> bool:
        """Whether Live pauses for X-Plane's scenery load (:meth:`x_plane_loads`)."""
        return time.monotonic() < self.yield_until

    def top_of(self, name: str) -> int:
        """The sharpest level of a texture's file, its size as the pack lays it out (0: 4096 px,
        1: 2048, -1: 8192)."""
        top = self._tops.get(name)
        if top is None:
            tex = parse_texture_name(name)
            top = self._tops[name] = self.zooms.top(tex.zoom) if tex is not None else 0
        return top

    def neutral_for(self, top: int) -> str | None:
        """The neutral texture of a file whose sharpest level is ``top``: what it is built over."""
        path = self.pack_dir / neutral_file(top)
        return str(path) if path.exists() else None

    def _level(self, distance_km: float, zoom: int, top: int, finest: int) -> tuple[int, float]:
        """The level a texture needs built at ``distance_km`` from the camera (in X-Plane's
        image), and how far out X-Plane reads that level of it (km, with the margin, before the
        image's scale). X-Plane counts a file's levels from its top, so it reads each level of an
        8192 px file (``top`` -1) where it reads the next smaller one of a 4096 px file, a ring
        farther out: its 4096 px and 2048 px levels are built that ring earlier (its 8192 px
        level is :meth:`_sharpest`'s; its farther levels have the copy above them,
        ``texture.needs_copy``). The camera's reach alone did it only within its own distance."""
        level = top_level_for(distance_km, zoom, finest, self.margin)
        ahead = top < 0 and 0 < level <= 2 and level - 1 >= max(finest, 0)
        if ahead:
            level -= 1
        read = ring_km(level + (1 if ahead else 0), zoom) if level < LOAD_LEVEL else LOAD_KM
        return level, read * self.margin

    def _read_now(self, distance_km: float, zoom: int, top: int, finest: int) -> int | None:
        """The level X-Plane reads now of a texture ``distance_km`` from the camera (three
        dimensions, in X-Plane's image), counted from the file's top as X-Plane does, no sharper
        than ``finest``. None past the far ring (72 km at ZL16), where the level read at load
        serves, and for an 8192 px level, whose late photos get the copy of the 4096 px one
        (:attr:`copied_up`)."""
        for index in range(LOAD_LEVEL):
            if distance_km <= ring_km(index, zoom):
                level = max(top + index, finest)
                return level if level >= 0 else None
        return None

    def _sharpest(self, zoom: int, s: float, s_now: float, starting: bool) -> float | None:
        """Whether an 8192 px file gets the photos of its 8192 px level now, and how urgent they
        are (as :meth:`plan` counts it); None: the copy of its 4096 px level stays there for now.

        X-Plane reads that level where it reads a 4096 px file's 4096 px one (12 km at ZL16, 3 km
        at ZL18). The photos come within a quarter more of the path ahead, the aircraft or the
        camera (``s``), urgent once X-Plane reads them (``s_now``: from the aircraft and the
        camera). Both are in three dimensions, from the heights above the ground, as X-Plane
        measures: none from high up. No camera reach (the module's docstring). At a start, X-Plane
        waits for all of them."""
        read = ring_km(0, zoom) * self.scale  # X-Plane's image: the rings grow with it
        built = read * self.margin
        if s > built:
            return None
        return s_now - (built if starting else read)

    def plan(
        self, fix: Fix, x_plane_there: bool = True, starting: bool = False
    ) -> dict[str, tuple[float, int]]:
        """Queue every texture within reach built coarser than X-Plane will read it.

        ``x_plane_there`` False: a place prepared before X-Plane loads it (``--from``), which a new
        start there must not reload. ``starting``: a start X-Plane has read nothing of yet: it
        waits for every 8192 px level around (:meth:`_sharpest`).
        """
        jumped = x_plane_there and (
            self.last_fix is None
            or haversine_m(fix.lat, fix.lon, self.last_fix.lat, self.last_fix.lon) > JUMP_KM * 1000
        )
        path = Route(["now", "ahead"], [(fix.lat, fix.lon), ahead(fix, LOOKAHEAD_MIN)])
        route = None  # the flight plan ahead, when the aircraft follows it, beside the track
        plan = self.flight_plan
        if plan is not None and not starting:
            km = max(fix.gs_ms, 0.0) * ROUTE_LOOKAHEAD_MIN * 60.0 / 1000.0
            track = fix.trk_deg if fix.gs_ms > MOVING_MS else None
            along = plan.ahead(fix.lat, fix.lon, km, track)
            if along is not None:
                route = Route(["route"] * (len(along) + 1), [(fix.lat, fix.lon), *along])
        self.on_route = route is not None
        camera = (fix.cam_lat, fix.cam_lon) if fix.cam_lat is not None else None
        reach = self.reach_km
        now = time.time()
        # an external view: textures its camera reaches before Live, noted as the reach's test
        cam_along = cam_up = 0.0
        if camera is not None:
            cam_along = haversine_m(fix.lat, fix.lon, camera[0], camera[1]) / 1000.0
            cam_up = 0.0 if fix.cam_agl_m is None else (fix.cam_agl_m - fix.agl_m) / 1000.0
        away = (  # not while X-Plane waits on a start, nor after it read early (a jump)
            camera is not None
            and x_plane_there
            and not starting
            and not (jumped or self.read_early)
            and math.hypot(cam_along, cam_up) * 1000.0 >= AWAY_M
        )
        outran: dict[str, int] = {}
        # heights above the ground: X-Plane measures from the camera in three dimensions, and
        # at cruise a texture right under the aircraft is 10 km away, too far for its 8192 px
        h = max(0.0, fix.agl_m) / 1000.0
        h_cam = h if fix.cam_agl_m is None else max(0.0, fix.cam_agl_m) / 1000.0
        jobs: dict[str, tuple[float, int]] = {}
        late: list[str] = []  # 8192 px levels to copy the 4096 px one into now (copied_up)
        for name, lat, lon, zoom, *_ in self.index.around(fix.lat, fix.lon, PLACEHOLDER_KM):
            if name in self.theirs:  # X-Plane shows the user's own tile there
                continue
            d_now = haversine_m(fix.lat, fix.lon, lat, lon) / 1000.0
            if d_now > PLACEHOLDER_KM:
                continue
            d = path.distance_km(lat, lon)
            if route is not None:
                d = min(d, route.distance_km(lat, lon))
            s, s_now = math.hypot(d, h), math.hypot(d_now, h)  # the same in three dimensions
            top = self.top_of(name)
            finest = finest_readable(top, self.finest_quality)
            # the aircraft and its path: the camera may be anywhere within the reach of them, near
            # the ground, a moment after the cockpit, so the rings are widened by it, along the
            # ground
            level, read_km = self._level(max(0.0, d - reach) / self.scale, zoom, top, finest)
            ring = LOAD_KM if level == LOAD_LEVEL else read_km * self.scale + reach
            urgency = d_now - ring
            if camera is not None:
                # the camera where it is: X-Plane reads from it in three dimensions, so one high
                # up or far out needs only the small levels, and no reach around it (it is there).
                # On the ground's distance and widened by the reach, a camera taken 57 km out at
                # cruise had Live build 4096 px levels around the point below it (2026-09-28).
                d_cam = haversine_m(camera[0], camera[1], lat, lon) / 1000.0
                s_cam = math.hypot(d_cam, h_cam)
                s, s_now = min(s, s_cam), min(s_now, s_cam)
                if away:
                    reads = self._read_now(s_cam / self.scale, zoom, top, finest)
                    unbuilt = reads is not None and self.level_of(name) > reads
                    if unbuilt and name not in self.outran:
                        outran[name] = reads
                level_cam, read_cam = self._level(s_cam / self.scale, zoom, top, finest)
                if level_cam <= level and level_cam != LOAD_LEVEL:
                    urgency_cam = s_cam - read_cam * self.scale
                    urgency = urgency_cam if level_cam < level else min(urgency, urgency_cam)
                    level = level_cam
            if level == 0 and zoom in self.reads_8192:
                sharp = self._sharpest(zoom, s, s_now, starting)
                if sharp is not None:
                    level, urgency = MIN_LEVEL, sharp
                    if (  # its photos late, X-Plane about to read that level: the copy goes in
                        not starting
                        and self.level_of(name) == 0
                        and name not in self.copied_up
                        and s_now <= ring_km(0, zoom) * self.scale * COPY_UP_AT
                    ):
                        late.append(name)
            if name in self.built and level <= self.built[name]:
                self.seen[name] = now  # X-Plane will read it as sharp as it is: still needed so
            if self.level_of(name) <= level:
                patch = self.patchy.get(name)
                if patch is None or patch[0] > now:
                    continue
                # built without pieces that did not come: again, as sharp, never waited for
                level, urgency = self.level_of(name), max(urgency, 1.0)
            jobs[name] = (urgency, level)
        if outran:
            self.outran.update(outran)
            counts = Counter(outran.values())
            self.log(
                f"  camera ahead of Live: {len(outran)} textures read sharper than built ("
                + ", ".join(f"{dds.size_of(lv)} px: {n}" for lv, n in sorted(counts.items()))
                + f"), the camera {cam_along:.1f} km from the aircraft, "
                f"{max(0.0, fix.cam_agl_m or 0.0) / 1000.0:.1f} km up, the reach {reach:.0f} km"
            )
        with (
            self.lock
        ):  # the queue and the reload flag together: the builder must not see one without the other
            self.queue = {
                n: j
                for n, j in jobs.items()
                if not (n in self.in_flight and self.in_flight[n][1] <= j[1])
            }
            self.to_copy.update(late)
            for n, (
                u,
                lv,
            ) in self.in_flight.items():  # urgent as this plan says, not as the one that took it
                self.in_flight[n] = (jobs[n][0] if n in jobs else max(u, 1.0), lv)
            if jumped:
                self.read_early = True
                self.refresh_flight_plan()  # X-Plane flying somewhere new: its plan may be new
            self.last_fix = fix
        self._want_squares(fix, starting)
        return jobs

    def take_batch(
        self, max_pieces: int = BATCH_PIECES, urgent_only: bool = False
    ) -> list[tuple[str, int, float]]:
        """The most urgent queued textures, up to ``max_pieces`` pieces (at least one texture);
        ``urgent_only``: only those X-Plane may be showing already (urgency <= 0)."""
        with self.lock:
            batch: list[tuple[str, int, float]] = []
            pieces = 0
            for name, (urgency, level) in sorted(self.queue.items(), key=lambda kv: kv[1][0]):
                if urgent_only and urgency > 0:
                    break  # sorted: none after it is urgent either
                if name in self.in_flight:  # one build at a time: its file is the last one's
                    continue
                n = pieces_of(level)
                if batch and pieces + n > max_pieces:
                    break
                batch.append((name, level, urgency))
                self.in_flight[name] = (urgency, level)
                pieces += n
            for name, _, _ in batch:
                del self.queue[name]
        return batch

    def _urgent_locked(self) -> dict[str, int]:
        """The textures X-Plane may already be showing (urgency <= 0), queued or being built, each
        once (queued sharper while built coarser, too), with the level it is wanted at. Under
        ``self.lock``."""
        out = {n: lv for n, (u, lv) in self.in_flight.items() if u <= 0}
        out.update((n, lv) for n, (u, lv) in self.queue.items() if u <= 0)
        return out

    def _urgent(self) -> dict[str, int]:
        with self.lock:
            return self._urgent_locked()

    def urgent_left(self) -> int:
        """Textures X-Plane may already be showing (urgency <= 0), queued or being built."""
        return len(self._urgent())

    def covers(self, lat: float, lon: float) -> bool:
        """Whether the region set up has textures near there (AutoOrtho's z_ao_eur: Europe)."""
        return any(
            haversine_m(lat, lon, tlat, tlon) < 100_000
            for _, tlat, tlon, *_ in self.index.around(lat, lon, 100.0)
        )

    def notice_at(self, lat: float | None, lon: float | None) -> dict | None:
        """Where the pack has no mesh but a region has one: {"id", "region", "installed"}, the
        region to install (or installed since Live started: used from its next start). None over
        the pack's mesh, and where no region has any (the open sea). Looked at again when the
        position enters another square."""
        if lat is None or lon is None:
            self.notice, self._notice_square = None, None
            return None
        square = (math.floor(lat), math.floor(lon))
        if square == self._notice_square:
            return self.notice
        self._notice_square = square
        region = None if self.covers(lat, lon) else region_of(lat, lon)
        if region is None:
            self.notice = None
        elif self.notice is None or self.notice["region"] != region:
            self.notice = {
                "id": int(time.time()) % 1_000_000_000,
                "region": region,
                "installed": region in set(self.installed_regions()),
            }
        return self.notice

    def urgent_work(self) -> tuple[int, int]:
        """The urgent textures, and the image pieces they take (256 for a 4096 px level, 1 for 256
        px):
        what a progress bar follows, since a sharp texture is so much more work than a small one."""
        levels = self._urgent().values()
        return len(levels), sum(pieces_of(lv) for lv in levels)

    def stalled_s(self) -> float:
        """How long pieces have been awaited with none arriving (0 while they arrive)."""
        return time.monotonic() - self.last_progress

    def maybe_say_built(self) -> bool:
        """X-Plane read the area before its textures were built (a start it did not wait for, a
        jump): once they are, the log says what shows. No reload of the scenery is asked: X-Plane's
        keeps the textures it holds (2026-09-27; the user, 2026-09-29: "ça ne fait rien")."""
        with self.lock:  # the queue and the flag as one plan left them
            urgent = len(self._urgent_locked())
            due = self.read_early and self.last_fix is not None and urgent == 0
            if due:
                self.read_early = False
        if due:
            self.log(
                "everything X-Plane may show is built; what it read before stays the neutral "
                "colour until it loads that place again (a new flight there, or X-Plane restarted)"
            )
        return due

    def built_now(self, known: int = -1) -> tuple[int, dict[str, int] | None]:
        """(version, textures built), copied under the lock for the window's map; nothing copied
        when ``known`` is the version already: the map asks every few seconds."""
        with self.lock:
            if known == self.built_version:
                return self.built_version, None
            return self.built_version, dict(self.built)

    def facts_now(self) -> tuple[dict[str, int], set[str], dict[str, float]]:
        """(built, patchy, seen), copied under the lock: the map's tooltip."""
        with self.lock:
            return dict(self.built), set(self.patchy), dict(self.seen)

    # --- building -----------------------------------------------------------------------------

    def _finished(self, fut: Future, name: str, level: int, patched: bool = False) -> None:
        try:
            fut.result()
            with self.lock:  # the status line and the save copy them whole, from the main thread
                self.built[name] = level
                self.built_version += 1
                self.done[level] += 1
                self.copied_up.discard(name)  # its file written again: no copy there any more
                if patched:  # built again later, a little later each time
                    tries = self.patchy.get(name, (0.0, 0))[1]
                    wait = min(PATCH_RETRY_MAX_S, PATCH_RETRY_S * 2**tries)
                    self.patchy[name] = (time.time() + wait, tries + 1)
                else:
                    self.patchy.pop(name, None)
        except Exception as e:  # the planner queues it again
            self.errors += 1
            self.log(f"  failed to build {name} at {dds.size_of(level)} px: {e}")
        finally:
            with self.lock:
                self.in_flight.pop(name, None)

    def build_loop(self) -> None:
        try:
            self._build_loop()
        except BaseException as e:  # the planner must not go on queueing for nobody
            import traceback

            self.crashed = e
            self.log("the builder stopped:\n" + "".join(traceback.format_exception(e)))
            self.stop.set()

    def _build_loop(self) -> None:
        """Each texture on its own: its pieces from the cache or asked for, then parents for pieces
        without imagery, then the encoding pool as soon as it has them all."""
        from .texture import build_job, copy_up

        answers: queue.Queue[tuple[Key, int, bytes | None]] = queue.Queue()
        self.downloader = self.downloader_factory(
            lambda key, status, body: answers.put((key, status, body))
        )
        if hasattr(self.downloader, "hold"):
            self.downloader.hold = self.yielding  # nothing asked while X-Plane loads scenery
        jobs: dict[str, _Job] = {}
        waiting: dict[Key, set[str]] = defaultdict(set)
        requested: set[Key] = set()
        asked_at: dict[Key, float] = {}  # when each piece awaited was asked for
        encoding: dict[Future, _Job] = {}
        ready: list[_Job] = []  # pieces all in, waiting for an encoder (submit_ready)
        copying: dict[str, Future] = {}  # late 8192 px levels getting their copy (copy_late)
        to_cache: list[tuple[Key, int, bytes | None]] = []

        def known(keys: list[Key]) -> dict[Key, bytes | None]:
            got = self.cache.get_many(self.provider, keys)
            self.from_cache += len(got)
            if got:
                self.last_progress = time.monotonic()
            return {k: (body if status == IMAGE else None) for k, (status, body) in got.items()}

        def ask(job: _Job, keys) -> None:
            for k in keys:
                waiting[k].add(job.name)
                if k not in requested:
                    requested.add(k)
                    asked_at[k] = time.monotonic()
                    self.downloader.submit(k, job.urgency)

        def advance(job: _Job, pool) -> None:
            if job.stage == "pieces" and not job.missing:
                lacking = [k for k in job.keys if job.pieces.get(k) is None]
                pkeys = list(dict.fromkeys(pk for k in lacking for pk in parent_keys(k)))
                job.parents = known(pkeys) if pkeys else {}
                job.parents_missing = set(pkeys) - set(job.parents)
                job.stage = "parents"
                ask(job, job.parents_missing)
            if job.stage == "parents" and not job.parents_missing:
                job.stage = "ready"
                ready.append(job)
                submit_ready(pool)

        def submit_ready(pool) -> None:
            """The most urgent textures ready to encode, as many as the encoders may take now:
            all while X-Plane waits, this computer's share while it flies (``encode_cap``); an
            8192 px level in a process of its own (``sharp_pool``, ``sharp_builds`` of them), one
            getting its copy only once it has it. Urgent as the last plan says, not as when it was
            taken: a texture far then may be read now (the planner updates ``in_flight``)."""
            with self.lock:
                now = {n: u for n, (u, _) in self.in_flight.items()}
            for j in ready:
                j.urgency = now.get(j.name, j.urgency)
            cap = self.encode_cap()  # the memory's state read with it (memory_state)
            while len(encoding) < cap:
                free = [
                    j
                    for j in ready
                    if j.name not in copying and self.may_encode(j.urgency, j.level)
                ]
                if not free:
                    break
                job = min(free, key=lambda j: j.urgency)
                ready.remove(job)
                job.stage = "encoding"
                top = self.top_of(job.name)
                fut = (sharp_pool if job.level < 0 else pool).submit(
                    build_job,
                    (
                        job.name,
                        job.level,
                        job.pieces,
                        job.parents,
                        str(self.pack_dir / "textures" / job.name),
                        self.neutral_for(top),
                        top,
                        self.tint_8192,
                        self.built.get(job.name),  # its levels kept below a sharper build
                    ),
                )
                encoding[fut] = job

        def copy_late(pool) -> None:
            """The late 8192 px levels the planner found (``to_copy``): the 4096 px level copied
            up into each, in a worker, before its photos are encoded; not once they are being."""
            with self.lock:
                late = [n for n in self.to_copy if n not in copying]
            busy = {job.name for job in encoding.values()}
            for name in late:
                if name in busy or self.built.get(name) != 0:
                    with self.lock:
                        self.to_copy.discard(name)
                    continue
                top = self.top_of(name)
                neutral = self.neutral_for(top)
                copying[name] = pool.submit(
                    copy_up,
                    self.pack_dir / "textures" / name,
                    None if neutral is None else Path(neutral),
                    top,
                )

        def copies_done() -> None:
            for name in [n for n, f in copying.items() if f.done()]:
                fut = copying.pop(name)
                with self.lock:
                    self.to_copy.discard(name)
                try:
                    fut.result()
                except Exception as e:
                    self.log(f"  could not copy up the 4096 px level of {name}: {e}")
                    continue
                with self.lock:
                    self.copied_up.add(name)
                self.log(
                    f"  {name}: its 8192 px photos late, its 4096 px level copied up meanwhile"
                )

        def answer(key: Key, status: int, body: bytes | None, pool) -> None:
            requested.discard(key)
            asked_at.pop(key, None)
            value = body if status == IMAGE else None
            failed = status not in (IMAGE, NO_IMAGERY)  # every try failed: not "no imagery here"
            names = waiting.pop(key, set())
            if not failed:
                if status != IMAGE or not baked_in(key, names):
                    to_cache.append((key, status, value))
                self.last_progress = time.monotonic()
            for name in names:
                job = jobs.get(name)
                if job is not None:
                    without(job, key, value, failed, pool)

        def baked_in(key: Key, names: set[str]) -> bool:
            """A photo only textures built to UNCACHED_FROM or sharper asked for, as one of their
            own pieces (not a parent): not kept, the texture built holds it."""
            wanting = [jobs.get(name) for name in names]
            return bool(wanting) and all(
                job is not None and job.level <= UNCACHED_FROM and key in job.missing
                for job in wanting
            )

        def without(job: _Job, key: Key, value: bytes | None, failed: bool, pool) -> None:
            """The piece's answer to the job, or its absence (``failed``: built again later)."""
            if key in job.missing:
                job.missing.discard(key)
                job.pieces[key] = value
            if key in job.parents_missing:
                job.parents_missing.discard(key)
                job.parents[key] = value
            if failed:
                job.patched.add(key)
            advance(job, pool)

        def give_up_stuck(pool) -> None:
            """Pieces awaited more than STUCK_S by a texture X-Plane waits for: it is built without
            them, from the zoom below. The downloader goes on, and what it brings is cached for
            the texture's next build."""
            now = time.monotonic()
            stuck = [k for k, t in asked_at.items() if now - t > STUCK_S]
            if not stuck:
                return
            with self.lock:
                urgent = {n for n, (u, _) in self.in_flight.items() if u <= 0}
            for key in stuck:
                for name in [n for n in waiting.get(key, ()) if n in urgent]:
                    waiting[key].discard(name)
                    job = jobs.get(name)
                    if job is not None and not job.patched:
                        self.log(
                            f"  a piece of {name} has not come for {STUCK_S:.0f} s: built from "
                            f"the zoom below meanwhile, and again once it comes"
                        )
                    if job is not None:
                        without(job, key, None, True, pool)

        last_flush = last_evict = time.monotonic()
        with (
            pools.RenewingPool(
                lambda: ProcessPoolExecutor(
                    max_workers=self.workers,
                    initializer=worker_ignores_ctrl_c,
                    mp_context=pools.context(),
                ),
                self.log,
            ) as pool,
            pools.RenewingPool(
                lambda: ProcessPoolExecutor(
                    max_workers=min(self.sharp_builds, self.workers),
                    initializer=worker_ignores_ctrl_c,
                    mp_context=pools.context(),
                    max_tasks_per_child=1,
                ),
                self.log,
            ) as sharp_pool,
        ):
            try:
                while not self.stop.is_set() or encoding:
                    if not self.stop.is_set() and not self.yielding():
                        held = sum(len(j.keys) for j in jobs.values())
                        room = WINDOW_PIECES - len(requested)
                        if room > 0:
                            batch = (
                                self.take_batch(min(room, HELD_PIECES - held))
                                if held < HELD_PIECES
                                else self.take_batch(room, urgent_only=True)  # what X-Plane shows
                            )
                            for name, level, urgency in batch:
                                keys = piece_keys(parse_texture_name(name), level)
                                have = known(keys)
                                job = _Job(
                                    name, level, urgency, keys, dict(have), set(keys) - set(have)
                                )
                                jobs[name] = job
                                ask(job, job.missing)
                                advance(job, pool)
                    try:
                        answer(*answers.get(timeout=0.05), pool)
                        while True:
                            answer(*answers.get_nowait(), pool)
                    except queue.Empty:
                        pass
                    give_up_stuck(pool)
                    copies_done()
                    if not self.yielding():
                        copy_late(pool)
                    for fut in [f for f in encoding if f.done()]:
                        job = encoding.pop(fut)
                        jobs.pop(job.name, None)
                        self._finished(fut, job.name, job.level, bool(job.patched))
                    submit_ready(pool)
                    self.encoding_now = len(encoding)
                    self.ready_now = len(ready)
                    self.pieces_pending = len(requested)
                    if not requested:  # nothing awaited: nothing stalled
                        self.last_progress = time.monotonic()
                    if to_cache and (len(to_cache) >= 256 or time.monotonic() - last_flush > 1.0):
                        self.cache.put_many(self.provider, to_cache)
                        to_cache.clear()
                        last_flush = time.monotonic()
                    self.maybe_say_built()
                    if time.monotonic() - last_evict > EVICT_EVERY_S and not self.yielding():
                        self.evict()
                        last_evict = time.monotonic()
                    if self.stop.is_set() and not encoding:
                        break
            finally:
                if to_cache:
                    self.cache.put_many(self.provider, to_cache)
                self.downloader.close()
                with (
                    self.lock
                ):  # textures still waiting for pieces are dropped: the planner asks again
                    for name, job in jobs.items():
                        if job.stage != "encoding":
                            self.in_flight.pop(name, None)

    def may_coarsen(self, name: str) -> bool:
        """Whether a sharp texture may go back to 256 px (orthostudio.live.evict): X-Plane holds
        none of the tiles it lies on round the aircraft or the camera, and reads only its 256 px
        level from there, beyond its 512 px ring and the camera's reach."""
        fix = self.last_fix
        if fix is None:
            return True
        tex = parse_texture_name(name)
        if tex is None:
            return False
        points = [(fix.lat, fix.lon)] + (
            [(fix.cam_lat, fix.cam_lon)] if fix.cam_lat is not None else []
        )
        held = set().union(*(xplane_tiles(lat, lon) for lat, lon in points))
        if tiles_of(tex) & held:
            return False
        lat, lon = tex.center
        read_km = ring_km(3, tex.zoom) * self.margin * self.scale + self.reach_km
        return all(haversine_m(p[0], p[1], lat, lon) > read_km * 1000 for p in points)

    def far(self, name: str) -> bool:
        """Beyond anything X-Plane has loaded: more than 300 km from the aircraft and the camera.
        A 256 px texture may go back to the neutral clone there (orthostudio.live.evict)."""
        fix = self.last_fix
        if fix is None:
            return True
        tex = parse_texture_name(name)
        if tex is None:
            return False
        lat, lon = tex.center
        points = [(fix.lat, fix.lon)] + (
            [(fix.cam_lat, fix.cam_lon)] if fix.cam_lat is not None else []
        )
        return all(haversine_m(p[0], p[1], lat, lon) > EVICT_SAFE_KM * 1000 for p in points)

    def behind(self, name: str) -> bool:
        """Whether a texture lies behind the aircraft, its track taken as ahead: what the disk
        budget makes coarser first. Nothing is behind an aircraft that does not move."""
        fix = self.last_fix
        tex = parse_texture_name(name)
        if fix is None or tex is None or fix.gs_ms <= MOVING_MS:
            return False
        lat, lon = tex.center
        turn = (bearing_deg(fix.lat, fix.lon, lat, lon) - fix.trk_deg + 180.0) % 360.0 - 180.0
        return abs(turn) > 90.0

    def texture_budget(self) -> int:
        """The textures' share of Live's disk space, with the room the imagery cache leaves in its
        own: one space, as the user sets it (the imagery held 1.7 GB of its 20 when the textures
        filled theirs over Norway, 2026-09-29)."""
        return self.disk_budget + max(0, self.cache_budget - self._cache_size)

    def evict(self) -> int:
        """Keep the pack under its disk budget and the imagery cache under its own. Builder
        thread."""
        from .texture import downgrade

        with self.lock:
            busy = set(self.in_flight) | set(self.queue)
        self._cache_size = self.cache.size_bytes()
        picks = pick(
            self.built,
            self.seen,
            lambda n: n not in busy and self.may_coarsen(n),
            self.texture_budget(),
            self.top_of,
            neutral=lambda n: n not in busy and self.far(n),
            behind=self.behind,
        )
        for name, level in picks[:EVICT_BATCH]:
            path = self.pack_dir / "textures" / name
            try:
                if level >= NEUTRAL_LEVEL and self.names is not None:
                    if self.names.holds(name):  # on a square made: the neutral texture's name
                        self.names.pool.replace(self.top_of(name), path)
                    else:  # far from every square made: no file there at all
                        path.unlink(missing_ok=True)
                else:
                    top = dds.read_top(path)  # the file's size from its header: it keeps it
                    downgrade(path, level, self.pack_dir / neutral_file(top), file_top=top)
            except OSError as e:
                self.log(f"  could not make {name} coarser: {e}")
                continue
            with self.lock:
                self.built_version += 1
                self.patchy.pop(name, None)
                self.copied_up.discard(name)
                if level >= NEUTRAL_LEVEL:
                    self.built.pop(name, None)
                    self.seen.pop(name, None)
                else:
                    self.built[name] = level
            self.evicted += 1
        if picks:
            self.log(
                f"  disk budget: {min(len(picks), EVICT_BATCH)} textures made coarser, "
                f"{usage(self.built, self.top_of) / 1e9:.1f} GB of "
                f"{self.texture_budget() / 1e9:.0f} GB now"
            )
        if self._cache_size > self.cache_budget:
            self.cache_freed += self.cache.evict_oldest(
                self._cache_size - int(self.cache_budget * TARGET)
            )
        return len(picks)

    def start(self) -> None:
        self._thread = threading.Thread(target=self.build_loop, name="live-builder", daemon=True)
        self._thread.start()
        if self.names is not None:
            self.names.start()

    def close(self) -> None:
        self.stop.set()
        if self._thread is not None:
            self._thread.join()
        if self.names is not None:
            self.names.close()
        self.save()

    def save(self) -> None:
        with self.lock:  # the builder changes them meanwhile
            textures = dict(self.built)
            seen = {n: round(t) for n, t in self.seen.items() if n in textures}
            patchy = sorted(n for n in self.patchy if n in textures)
        meta = load_built(self.pack_dir)
        if self.screen is not None:
            meta["screen"] = self.screen
        meta["textures"] = textures
        meta["seen"] = seen
        meta.pop("grown", None)  # a pack's record from when textures grew
        meta["patchy"] = patchy
        meta["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        tmp = self.pack_dir / MARKER.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta))
        os.replace(tmp, self.pack_dir / MARKER)

    def snapshot(self) -> dict:
        """What the streamer is doing, counted by level (0 the sharpest, 4 the 256 px one): the
        status line's figures, and the Live window's."""
        with self.lock:  # counted here: the builder changes all of them meanwhile
            queued = Counter(level for _, level in self.queue.values())
            urgent = sum(1 for u, _ in self.queue.values() if u <= 0)
            flying = len(self.in_flight)
            built = dict(self.built)
            done = dict(self.done)
        dl = self.downloader
        return {
            "first_level": MIN_LEVEL,  # the lists below: levels -1 (8192 px) to 4 (256 px)
            "queued": [queued.get(lv, 0) for lv in LEVELS],
            "urgent": urgent,
            "building": flying,
            "ready": self.ready_now,  # of those, their photos in, waiting for an encoder
            "pieces_awaited": self.pieces_pending,
            "built": [done.get(lv, 0) for lv in LEVELS],  # since the streamer started
            "downloaded": getattr(dl, "downloaded", 0),
            "downloaded_bytes": getattr(dl, "bytes", 0),
            "from_cache": self.from_cache,
            "failed": getattr(dl, "failed", 0),
            "disk_bytes": usage(built, self.top_of),
            "disk_budget": self.texture_budget(),
            "read_early": self.read_early,
            "at_8192": sum(1 for level in built.values() if level < 0),  # their 8192 px photos
            "tint_8192": self.tint_8192,
            "flight_plan": None
            if self.flight_plan is None
            else {
                "name": self.flight_plan.name,
                "following": self.on_route,
                "points": route_points(self.flight_plan.points),  # the window's map draws it
            },
            # X-Plane's image, and how far it reads 4096 px of a ZL16 texture there
            "screen": None
            if self.screen is None
            else {**self.screen, "scale": self.scale, "km_4096": ring_km(0, 16) * self.scale},
        }

    def status(self) -> str:
        s = self.snapshot()

        def by_level(counts: list[int]) -> str:  # "4096 px: 3, 2048 px: 12"
            return ", ".join(
                f"{dds.size_of(lv)} px: {n}" for lv, n in zip(LEVELS, counts, strict=True) if n
            )

        q = by_level(s["queued"]) or "empty"
        d = by_level(s["built"]) or "nothing yet"
        return (
            f"queue {q} (urgent {s['urgent']}), building {s['building']} "
            f"({s['ready']} ready to encode), "
            f"{s['pieces_awaited']} pieces awaited | built {d} | "
            f"pieces {s['downloaded']} downloaded ({s['downloaded_bytes'] / 1e6:.0f} MB), "
            f"{s['from_cache']} from cache"
            + (f", {s['failed']} failed" if s["failed"] else "")
            + f" | disk {s['disk_bytes'] / 1e9:.1f}/{s['disk_budget'] / 1e9:.0f} GB"
            + (" | X-Plane read early" if s["read_early"] else "")
        )


def read_before_ready(ready_at: float | None, xplane_started: float | None) -> bool:
    """With ``--from``, whether X-Plane may have read textures before they were built: it started
    before the area was ready (or we cannot tell). Then the log says so once everything is."""
    return ready_at is None or xplane_started is None or xplane_started < ready_at


@dataclass
class Arrival:
    """A flight X-Plane is about to load, or is loading: where, and what the plugin was told."""

    lat: float
    lon: float
    name: str
    # "from" (--from), "click" (Freeflight.prf), "plugin"
    how: str
    since: float
    ready_at: float | None = None  # when everything X-Plane will read there was built
    held: bool = False  # the plugin held X-Plane for it
    released: bool = False  # ... and let it go with everything built
    stalled: bool = False  # ... or let it go because nothing downloaded any more
    elsewhere_since: float | None = None  # X-Plane running somewhere else since then
    work: int = 0  # the most image pieces seen to build for it: the progress bar's whole
    loading: bool = (
        True  # X-Plane went through a load since: its position stopped, or the plugin spoke
    )

    def progress(self, work_left: int) -> float:
        self.work = max(self.work, work_left)
        return 1.0 - work_left / self.work if self.work else 1.0

    def near(self, lat: float, lon: float) -> bool:
        return haversine_m(self.lat, self.lon, lat, lon) <= JUMP_KM * 1000.0

    def certain(self, now: float) -> bool:
        """Said by X-Plane itself (a click just now) or by the user (``--from``): X-Plane goes
        there,
        even while it still runs where it was."""
        return self.how == "from" or (self.how == "click" and now - self.since < LOADING_S)


def longest_frame(said: str) -> int | None:
    """The longest frame of the second the plugin's ask says (``max=123 n=45``: 123 ms, 45 frames),
    None when it says none (an older plugin)."""
    for word in said.split():
        if word.startswith("max="):
            with contextlib.suppress(ValueError):
                return int(word[4:])
    return None


def may_have_read_early(arrival: Arrival, xplane_started: float | None) -> bool:
    """Whether X-Plane, now there, may have read textures of the start before they were built."""
    if arrival.released:
        return False  # the plugin held it until everything was built
    if arrival.ready_at is not None and arrival.ready_at - arrival.since < 1.0:
        return False  # already built when X-Plane was told to start there
    if arrival.how == "from" and not arrival.held:
        return read_before_ready(arrival.ready_at, xplane_started)
    return True


def notice_text(notice: dict) -> str:
    """The notice as the log says it (the window and the plugin have their own words)."""
    name = REGION_NAMES.get(notice["region"], notice["region"])
    if notice.get("installed"):
        return f"{name} is installed: used from Live's next start"
    return f"no mesh here: install {name} in the Live window"


def plugin_note(notice: dict, language: str) -> str:
    """The notice as the plugin shows it in X-Plane, in the Live window's language."""
    if language != "fr":
        return f"Live: {notice_text(notice)}"
    name = REGION_NAMES_FR.get(notice["region"], notice["region"])
    if notice.get("installed"):
        return f"Live : {name} est installée, elle servira au prochain démarrage de Live"
    return f"Live : pas de mesh ici, installez {name} dans la fenêtre de Live"


_EXPECTED = {
    "from": "building around {} before X-Plane loads it",
    "click": "X-Plane is starting a flight at {}: building there",
    # X-Plane also saves its start when it quits, and nothing tells the two apart yet
    "click_running": (
        "X-Plane's next start is {} (a new flight, or X-Plane quitting): building there"
    ),
    "plugin": "X-Plane is loading a flight at {}: building there",
}


class Rate:
    """The download rate over the last few seconds, in megabytes a second."""

    def __init__(self, window_s: float = 3.0):
        self.window_s = window_s
        self.samples: list[tuple[float, int]] = []

    def mbs(self, now: float, total_bytes: int) -> float:
        self.samples.append((now, total_bytes))
        while len(self.samples) > 2 and now - self.samples[1][0] >= self.window_s:
            self.samples.pop(0)
        t0, b0 = self.samples[0]
        return (total_bytes - b0) / 1e6 / (now - t0) if now > t0 else 0.0


def progress_report(
    streamer: Streamer,
    values: dict[str, float],
    arrival: Arrival | None,
    now: float,
    last_packet: float,
    mbs: float,
) -> dict:
    """What the Live window shows: what X-Plane sends, the start it loads, the streamer's figures.

    ``data`` is ``none`` before X-Plane sends its position, ``fresh`` while it does, and ``stale``
    once it stops: X-Plane loads a flight, or has quit."""
    aircraft = None
    if values.get("lat"):
        aircraft = {
            "lat": values["lat"],
            "lon": values.get("lon", 0.0),
            "alt_ft": values.get("alt_m", 0.0) * 3.28084,
            "gs_kt": values.get("gs_ms", 0.0) * 1.94384,
            "trk": values.get("trk_deg", 0.0),
            "hdg": values.get("hdg_deg", values.get("trk_deg", 0.0)),
        }
    fix = streamer.last_fix
    camera_km = None
    camera = None
    if fix is not None and fix.cam_lat is not None and fix.cam_lon is not None:
        camera_km = haversine_m(fix.lat, fix.lon, fix.cam_lat, fix.cam_lon) / 1000.0
        camera = {"lat": fix.cam_lat, "lon": fix.cam_lon}
    start = None
    if arrival is not None:
        left, work = streamer.urgent_work()
        start = {
            "name": arrival.name,
            "held": arrival.held and not arrival.released,  # X-Plane waits on its loading screen
            "ready": arrival.ready_at is not None,
            "left": left,
            "progress": arrival.progress(work),
            "seconds": now - arrival.since,
        }
    fresh = now - last_packet < FRESH_S
    if aircraft is not None and fresh:
        here = (aircraft["lat"], aircraft["lon"])
    elif arrival is not None:
        here = (arrival.lat, arrival.lon)
    else:
        here = (None, None)
    return {
        "data": "none" if aircraft is None else "fresh" if fresh else "stale",
        "aircraft": aircraft,
        "camera_km": camera_km,
        "camera": camera,
        "start": start,
        "mbs": mbs,
        "notice": streamer.notice_at(*here),
        "streamer": streamer.snapshot(),
    }


def run(
    streamer: Streamer,
    xp,
    start: Fix | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    log: Callable[[str], None] = print,
    gate=None,
    start_name: str | None = None,
    report: Callable[[dict], None] | None = None,
    language: Callable[[], str] = lambda: "en",
) -> None:
    """Follow X-Plane over UDP until Ctrl-C (or ``should_stop``), planning every two seconds.

    ``gate`` (``orthostudio.live.gate.Gate``) hears of a flight's start from Freeflight.prf and
    from the plugin,
    and holds X-Plane until everything it will read there is built. ``report`` is given every
    second what the Live window shows (:func:`progress_report`)."""
    from .scenery import xplane_started_at

    fields = [name for name, _ in DATAREFS]
    values: dict[str, float] = {}
    arrival: Arrival | None = None
    last_plan = last_status = last_save = last_packet = last_subscribe = last_wait = 0.0
    before: tuple[Fix, float] | None = None  # the last position planned, and when
    last_report = 0.0

    rate = Rate()

    def expect(lat: float, lon: float, name: str, how: str) -> None:
        nonlocal arrival
        # A click while X-Plane runs is a start only once X-Plane stops to load it: X-Plane also
        # writes Freeflight.prf when it quits.
        arrival = Arrival(
            lat,
            lon,
            name,
            how,
            time.time(),
            loading=not ("lat" in values and time.time() - last_packet < FRESH_S),
        )
        if gate is not None:  # the gate's word to a waiting X-Plane while this start is planned
            gate.hold_at(name, language())
        streamer.plan(Fix(lat, lon), x_plane_there=False, starting=True)
        arrival.progress(streamer.urgent_work()[1])
        streamer.refresh_flight_plan()  # a new flight may have a new plan
        if streamer.covers(lat, lon):
            said = "click_running" if how == "click" and not arrival.loading else how
            log(_EXPECTED[said].format(name))
        else:
            notice = streamer.notice_at(lat, lon)
            what = notice_text(notice) if notice else "no region has a mesh there"
            log(f"{name}: no mesh there, X-Plane shows its own scenery ({what})")

    from .cameralog import CameraLog

    camera = CameraLog(  # how far external views go, and the reach they needed
        streamer.pack_dir / "_live" / "camera.json",
        first_ring=1 if 16 in streamer.reads_8192 else 0,
    )
    # Nothing guessed: the start is the one the user chooses in X-Plane (the click, or the plugin
    # as the flight loads), or gives with --from. X-Plane's last start was built at once, and the
    # last SimBrief plan asked for and drawn, as if the user meant to fly them again (a user,
    # 2026-09-27: "nothing says I want to fly my last flight").
    streamer.start()
    if start is not None:
        expect(start.lat, start.lon, start_name or f"{start.lat:.3f} {start.lon:.3f}", "from")
    try:
        while not should_stop():
            if streamer.crashed is not None:
                raise SystemExit(f"the streamer stopped: {streamer.crashed!r}")
            if streamer.names is not None and streamer.names.crashed is not None:
                raise SystemExit(f"the squares' files stopped: {streamer.names.crashed!r}")
            now = time.time()
            mbs = rate.mbs(now, getattr(streamer.downloader, "bytes", 0))
            # X-Plane flying, no start pending: the encoders leave it its cores (submit_ready)
            streamer.flying = "lat" in values and now - last_packet < FRESH_S and arrival is None
            if gate is not None:
                clicked = gate.new_start()
                if clicked is not None:
                    expect(*clicked, "click")
                running = "lat" in values and now - last_packet < FRESH_S
                requests = gate.requests()
                late = gate.stood_in()
                if late:
                    log(
                        f"  Live was busy {max(late):.1f} s: the gate told X-Plane to wait "
                        f"meanwhile ({len(late)} times)"
                    )
                for kind, lat, lon, addr, why in requests:
                    if kind == "ASK":  # the plugin, every second of a flight: a notice for it?
                        frame = longest_frame(why)  # and its longest frame that second
                        if frame is not None and frame >= STUTTER_MS:
                            log(
                                f"  X-Plane stuttered: a frame of {frame} ms; Live was encoding "
                                f"{streamer.encoding_now} textures, downloading {mbs:.1f} MB/s"
                            )
                        notice, lang = streamer.notice, language()
                        note = plugin_note(notice, lang) if notice else None
                        gate.note(addr, note, notice, lang=lang)
                        continue
                    if (
                        running
                        and why != "flight"
                        and not (arrival is not None and arrival.certain(now))
                    ):
                        if kind == "HOLD":  # a livery changed in flight: nothing loads
                            gate.answer(addr, True, 0, lang=language())
                        continue
                    if arrival is None or (
                        arrival.how not in ("click", "from") and not arrival.near(lat, lon)
                    ):
                        expect(lat, lon, f"{lat:.3f} {lon:.3f}", "plugin")
                    arrival.loading = True
                    if kind == "HOLD":
                        streamer.hold_at(lat, lon)
                        left, work = streamer.urgent_work()
                        done = arrival.progress(work)
                        took = now - arrival.since
                        arrival.held = True
                        # no time limit: X-Plane waits while pieces arrive, however long a start
                        # takes, and goes on only once nothing has arrived for a minute
                        stalled = left > 0 and streamer.stalled_s() > STALL_S
                        # and the files of its squares made (orthostudio.live.names), where the
                        # start was chosen and where X-Plane says it loads it
                        ready = (
                            left == 0
                            and streamer.squares_ready(arrival.lat, arrival.lon)
                            and streamer.squares_ready(lat, lon)
                        )
                        gate.answer(
                            addr,
                            ready or stalled,
                            left,
                            arrival.name,
                            done,
                            took,
                            mbs,
                            lang=language(),
                        )
                        if stalled and not arrival.stalled:
                            arrival.stalled = True
                            log(
                                f"  nothing downloaded for {STALL_S:.0f} s: X-Plane goes on with "
                                f"{left} textures to build around {arrival.name} (is the internet "
                                f"there?)"
                            )
                        elif ready and not arrival.released:
                            arrival.released = True
                            log(
                                f"  X-Plane goes on after {took:.0f} s: everything it reads "
                                f"around {arrival.name} is built"
                            )
                        elif left and now - last_wait >= 5:
                            last_wait = now
                            log(
                                f"  X-Plane waits on its loading screen: {left} textures to build "
                                f"around {arrival.name} "
                                f"({done:.0%}, {took:.0f} s, {mbs:.1f} MB/s)"
                            )
                        elif not ready and not stalled and now - last_wait >= 5:
                            last_wait = now
                            log(
                                "  X-Plane waits on its loading screen: the files of its squares "
                                f"around {arrival.name} are being made"
                            )
            if (
                arrival is not None
                and arrival.ready_at is None
                and streamer.urgent_left() == 0
                and streamer.squares_ready(arrival.lat, arrival.lon)
            ):
                arrival.ready_at = now
                log(f"  everything X-Plane will read around {arrival.name} is built")
            if now - last_packet > 5 and now - last_subscribe > 5:
                xp.subscribe()
                last_subscribe = now
            packet = xp.receive(timeout=0.2)
            now = time.time()
            if packet:
                last_packet = now
                for idx, v in packet.items():
                    if 0 <= idx < len(fields):
                        values[fields[idx]] = v
                streamer.x_plane_loads(values.get("scenery_loading", 0.0) >= 0.5)
            if now - last_plan >= PLAN_EVERY_S and (values.get("lat") or arrival is not None):
                last_plan = now
                streamer.set_screen(values.get("win_w"), values.get("fov"))
                if now - last_packet < FRESH_S and arrival is None:  # flying, not loading
                    camera.note(values, now, streamer.scale)
                fix = None
                if values.get("lat") and values.get("lon"):
                    fix = Fix(
                        values["lat"],
                        values["lon"],
                        values.get("alt_m", 0.0),
                        values.get("gs_ms", 0.0),
                        values.get("trk_deg", 0.0),
                        agl_m=values.get("agl_m", 0.0),
                    )
                    if all(k in values for k in ("px", "pz", "vx", "vz")):
                        fix.cam_lat, fix.cam_lon = camera_position(
                            fix.lat,
                            fix.lon,
                            (values["px"], values.get("py", 0.0), values["pz"]),
                            (values["vx"], values.get("vy", 0.0), values["vz"]),
                        )
                        # the camera's height: the aircraft's, and how far above it the camera is
                        fix.cam_agl_m = fix.agl_m + values.get("vy", 0.0) - values.get("py", 0.0)
                    motion_from_positions(*(before or (None, 0.0)), fix, now)
                    before = (fix, now)
                running = fix is not None and now - last_packet < FRESH_S
                if arrival is not None and not running:
                    arrival.loading = True  # X-Plane stopped sending its position: it is loading
                if arrival is None:
                    if fix is not None:
                        streamer.plan(
                            fix
                        )  # a jump is a new start: X-Plane read it before it was built
                        streamer.follow_window(fix)
                elif running and arrival.near(fix.lat, fix.lon):  # X-Plane is there
                    if arrival.loading and may_have_read_early(arrival, xplane_started_at()):
                        streamer.read_early = True  # (never loading: X-Plane was there all along)
                    streamer.plan(fix, x_plane_there=False)
                    streamer.arrived()
                    arrival = None
                elif running:  # X-Plane runs somewhere else
                    if arrival.elsewhere_since is None:
                        arrival.elsewhere_since = now
                    elif now - arrival.elsewhere_since > ELSEWHERE_S:
                        log(f"  X-Plane did not start at {arrival.name}")
                        streamer.arrived()
                        arrival = None
                    if arrival is not None and arrival.certain(
                        now
                    ):  # it is about to leave for there
                        streamer.plan(
                            Fix(arrival.lat, arrival.lon), x_plane_there=False, starting=True
                        )
                    else:
                        streamer.plan(fix, x_plane_there=False)
                else:  # X-Plane is loading the flight, or not there yet
                    streamer.plan(Fix(arrival.lat, arrival.lon), x_plane_there=False, starting=True)
            if now - last_status >= 10:
                last_status = now
                where = (
                    f"{values['lat']:.3f} {values['lon']:.3f} "
                    f"{values.get('alt_m', 0) * 3.28084:6.0f} ft "
                    f"{values.get('gs_ms', 0) * 1.94384:4.0f} kt"
                    if values.get("lat")
                    else "waiting for X-Plane"
                )
                if arrival is not None:
                    where += (
                        f" (start {arrival.name}"
                        + (", X-Plane held" if arrival.held and not arrival.released else "")
                        + ")"
                    )
                elif streamer.last_fix is not None and streamer.last_fix.cam_lat is not None:
                    cam_km = (
                        haversine_m(
                            streamer.last_fix.lat,
                            streamer.last_fix.lon,
                            streamer.last_fix.cam_lat,
                            streamer.last_fix.cam_lon,
                        )
                        / 1000.0
                    )
                    if cam_km > 0.5:
                        where += f" (camera {cam_km:.1f} km away)"
                log(f"  {where} | {streamer.status()} | {mbs:.1f} MB/s")
            if report is not None and now - last_report >= REPORT_EVERY_S:
                last_report = now
                report(progress_report(streamer, values, arrival, now, last_packet, mbs))
            if now - last_save >= 30:
                streamer.save()
                camera.save()
                last_save = now
    except KeyboardInterrupt:
        pass
    finally:
        streamer.close()
        camera.save()
        for line in camera.summary():
            log(f"  {line}")
        if streamer.outran:
            log(
                f"  camera ahead of Live: {len(streamer.outran)} textures in this run, "
                f"with a reach of {streamer.reach_km:.0f} km"
            )
