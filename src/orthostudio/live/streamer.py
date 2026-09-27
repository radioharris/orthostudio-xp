# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Stream ortho textures to X-Plane as real files: follow the aircraft, build ahead, rename() in.

Every two seconds the planner takes the aircraft's position, track and speed (X-Plane's UDP) and,
for every texture within 300 km, the level X-Plane will read of it over the next minutes: the rings
of ``orthostudio.live.rings`` applied to its distance from the path ahead. Textures built coarser
than that are queued, the most urgent first: those nearest to the ring where X-Plane will want them.
A builder thread downloads their pieces (the chunk cache, then the downloader), encodes them in a
process pool and replaces each file with rename(). X-Plane opens a texture again for every sharper
level, so it reads the new file at its next ring.

At a flight's start, OrthoStudio XP Live's X-Plane plugin holds X-Plane on its loading screen until
everything it will read there is built (``orthostudio.live.gate``). The streamer starts building
when the user clicks Start Flight. Without the plugin, or after a jump it did not see coming,
X-Plane loaded the area before its textures were there: once everything it may already show is
built, the streamer asks it to reload the scenery.

External views take the camera away from the aircraft, and X-Plane reads what is in the files at
once. So every ring is widened by ``reach_km``, the distance the camera may go.

One zoom above the mesh's (AutoOrtho's max_zoom 17, or 19 near airports), a texture's file starts
at 4096 px and grows to 8192 px, its 8192 px level built in the same rename (``orthostudio.live
.region``). X-Plane reads a texture's size again whenever it opens it for a sharper level, but
never reads again a level it has: a texture grows before X-Plane reads its 4096 px level, else it
stays 4096 px until X-Plane loads it again. A 4096 px file is never a placeholder, so growing needs
no camera reach: in flight, the textures on the path ahead grow while they are still farther than
X-Plane reads 4096 px, and only where X-Plane will read them at 8192 px, which it does not from high
up: those distances are in three dimensions. At a start, X-Plane has read nothing yet, and
``start_mode`` says which textures it waits for at 8192 px (:data:`START_MODES`).
"""

from __future__ import annotations

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

from . import dds
from .aoregions import REGION_NAMES, REGION_NAMES_FR, region_of
from .cache import IMAGE, NO_IMAGERY, ChunkCache, Key
from .evict import EVICT_SAFE_KM, TARGET, levels_usage, pick, usage
from .flightplan import MOVING_MS, ROUTE_LOOKAHEAD_MIN, FlightPlan, last_plan
from .geo import haversine_m, parse_texture_name
from .pack import MARKER, load_built
from .region import NEUTRAL_LEVEL, Zooms, ensure_neutral, neutral_file
from .rings import LOAD_LEVEL, MIN_LEVEL, finest_readable, ring_km, screen_scale, top_level_for
from .route import KM_PER_DEG, Route
from .texture import parent_keys, piece_keys, pieces_of, worker_ignores_ctrl_c

PLACEHOLDER_KM = 300.0  # every texture within this has at least its 256 px level
LOAD_KM = (
    230.0  # X-Plane loads a tile's DSF, reading 256 px, from about this far (probe: 130-230 km)
)
LOOKAHEAD_MIN = 5.0  # the path ahead the planner looks along
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
RELOAD_COMMAND = "sim/operation/reload_scenery"
# What X-Plane waits for at a start, where textures grow to 8192 px:
# - full: every 8192 px texture of the start, as far as the camera may go (the longest wait);
# - around: 8192 px wherever X-Plane reads 4096 px while the flight loads (ring 0: about 12 km,
#   3 km for the airports' textures), so that nothing around the aircraft is left at 4096 px;
# - normal: 4096 px only;
# - reload: 4096 px only; then the 8192 px textures of "around" are built, and a reload of the
#   scenery is offered (Offer) to show them.
# Beyond the start, all four grow the textures ahead alike.
START_MODES = ("full", "around", "normal", "reload")
OFFER_KM = 10.0  # the reload is offered while the aircraft stays this near its start
OFFER_URGENCY = 0.5  # just after what X-Plane may be showing: the user waits for the button
RELOADED_S = 300.0  # a start this soon after a reload asked, at the same place, is that reload
EVICT_EVERY_S = 30.0
EVICT_BATCH = 500  # textures made coarser per round, so that the builder is never held up long
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
]


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
    """The region's textures by 1-degree tile: (name, lat, lon, zoom)."""

    def __init__(self, names):
        self.by_tile: dict[tuple[int, int], list[tuple[str, float, float, int]]] = defaultdict(list)
        for name in names:
            tex = parse_texture_name(name)
            if tex is not None:
                lat, lon = tex.center
                self.by_tile[(math.floor(lat), math.floor(lon))].append((name, lat, lon, tex.zoom))

    def __len__(self) -> int:
        return sum(len(v) for v in self.by_tile.values())

    def around(self, lat: float, lon: float, km: float) -> Iterator[tuple[str, float, float, int]]:
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
        start_mode: str = "full",
        covered: Collection[tuple[int, int]] = (),
        simbrief_user: str = "",
        installed_regions: Callable[[], Collection[str]] = frozenset,
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
        self.workers = workers
        self.xp = xplane  # anything with .command(name): the UDP link, or a test's stand-in
        self.log = log
        if start_mode not in START_MODES:
            raise ValueError(f"start_mode {start_mode!r}: one of {', '.join(START_MODES)}")
        self.start_mode = start_mode
        self.meta = load_built(pack_dir)
        self.built: dict[str, int] = dict(self.meta.get("textures", {}))
        # the textures whose file is 8192 px: each built from its 8192 px level
        self.grown = {n for n in self.meta.get("grown", []) if self.built.get(n) == MIN_LEVEL}
        # built without pieces that did not come: when to build them again, and how many times
        self.patchy: dict[str, tuple[float, int]] = {
            n: (0.0, 0) for n in self.meta.get("patchy", []) if n in self.built
        }
        self.seen: dict[str, float] = dict(
            self.meta.get("seen", {})
        )  # when a built texture was last in reach
        self.disk_budget = disk_budget
        self.cache_budget = cache_budget
        self.evicted = 0
        self.cache_freed = 0
        self.index = TextureIndex(
            e.name for e in os.scandir(pack_dir / "textures") if e.name.endswith(".dds")
        )
        # the zoom set: each texture's file is laid out for it (region.relayout), smaller below the
        # mesh's zoom; one zoom above, 4096 px files that grow to 8192 px where X-Plane reads that
        self.zooms = Zooms(max_zoom, max_zoom_airports)
        zooms = {z for textures in self.index.by_tile.values() for *_, z in textures}
        self.growing = {  # at X-Plane's texture quality, whether it reads 8192 px at all
            z for z in zooms if finest_readable(self.zooms.top(z), finest_quality) == MIN_LEVEL
        }
        tops = {self.zooms.start_top(z) for z in zooms} | ({MIN_LEVEL} if self.growing else set())
        for top in tops:  # the neutral texture of every size a file may take
            ensure_neutral(pack_dir, top)
        self.offer: Offer | None = None  # the reload offered after a start (start_mode "reload")
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
        self.reload_pending = False
        self.last_fix: Fix | None = None
        self.done = Counter()
        self.errors = 0
        self.crashed: BaseException | None = None
        self.last_progress = time.monotonic()  # a piece arrived, or none was awaited
        self._thread: threading.Thread | None = None

    # --- planning -----------------------------------------------------------------------------

    def refresh_flight_plan(self) -> None:
        """Ask SimBrief for the pilot's last plan, in a thread of its own, at most once a minute:
        when Live starts, and when a flight does (a new plan may have been made meanwhile)."""
        if not self.simbrief_user or time.monotonic() - self._plan_asked < PLAN_ASK_EVERY_S:
            return
        self._plan_asked = time.monotonic()

        def fetch() -> None:
            plan, why = last_plan(self.simbrief_user)
            if plan is None:
                self.log(
                    f"  SimBrief: {why}; Live looks {LOOKAHEAD_MIN:.0f} minutes straight ahead"
                )
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

    def start_top_of(self, name: str) -> int:
        """The sharpest level of a texture's file as the pack lays it out (0: 4096 px, 1: 2048)."""
        tex = parse_texture_name(name)
        return self.zooms.start_top(tex.zoom) if tex is not None else 0

    def top_of(self, name: str) -> int:
        """The sharpest level of a texture's file now: its start size, -1 (8192 px) once grown."""
        return MIN_LEVEL if name in self.grown else self.start_top_of(name)

    def neutral_for(self, top: int) -> str | None:
        """The neutral texture of a file whose sharpest level is ``top``: what it is built over."""
        path = self.pack_dir / neutral_file(top)
        return str(path) if path.exists() else None

    def _growth(self, zoom: int, d: float, d_now: float, starting: bool, offer) -> float | None:
        """Whether a 4096 px texture of a zoom that goes to 8192 px grows now, and then how urgent
        it is (as :meth:`plan` counts it); None: it stays 4096 px for now.

        ``d``: from the path ahead and the camera; ``d_now``: from the aircraft and the camera; both
        in three dimensions, from their heights above the ground, as X-Plane measures. The other
        levels keep the distances on the ground: one built too few would show the neutral colour,
        where a texture that does not grow stays 4096 px."""
        k = self.scale  # X-Plane's image: the rings grow with it
        r8 = ring_km(MIN_LEVEL, zoom) * self.margin * k
        r4 = ring_km(0, zoom) * self.margin * k  # where X-Plane reads 4096 px while a flight loads
        r0 = ring_km(0, zoom) * k
        if starting:  # X-Plane reads nothing here before the start's textures are built
            if self.start_mode == "full" and d - self.reach_km <= r8:
                return d_now - r8 - self.reach_km  # X-Plane waits for it
            if self.start_mode == "around" and d <= r4:
                return d_now - r4
            return None
        if offer is not None and d <= r4:  # X-Plane reads it again at the reload
            return OFFER_URGENCY + d_now / 1000.0
        if d <= r8 and d_now > r0:  # ahead, before X-Plane reads its 4096 px level
            return d_now - r0
        return None

    def plan(
        self, fix: Fix, x_plane_there: bool = True, starting: bool = False
    ) -> dict[str, tuple[float, int]]:
        """Queue every texture within reach built coarser than X-Plane will read it.

        ``x_plane_there`` False: a place prepared before X-Plane loads it (``--from``), which a new
        start there must not reload. ``starting``: a start X-Plane has read nothing of yet, whose
        8192 px textures ``start_mode`` chooses.
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
        now = time.time()
        # heights above the ground: X-Plane measures from the camera in three dimensions, and
        # at cruise a texture right under the aircraft is 10 km away, too far for its 8192 px
        h = max(0.0, fix.agl_m) / 1000.0
        h_cam = h if fix.cam_agl_m is None else max(0.0, fix.cam_agl_m) / 1000.0
        offer = None if starting else self._offer_near(fix)
        offer_left = 0
        jobs: dict[str, tuple[float, int]] = {}
        for name, lat, lon, zoom in self.index.around(fix.lat, fix.lon, PLACEHOLDER_KM):
            if name in self.theirs:  # X-Plane shows the user's own tile there
                continue
            d_now = haversine_m(fix.lat, fix.lon, lat, lon) / 1000.0
            if d_now > PLACEHOLDER_KM:
                continue
            if name in self.built:
                self.seen[name] = now
            d = path.distance_km(lat, lon)
            if route is not None:
                d = min(d, route.distance_km(lat, lon))
            s, s_now = math.hypot(d, h), math.hypot(d_now, h)  # the same in three dimensions
            if camera is not None:
                d_cam = haversine_m(camera[0], camera[1], lat, lon) / 1000.0
                if d_cam < d:
                    d, d_now = d_cam, min(d_now, d_cam)
                s_cam = math.hypot(d_cam, h_cam)
                s, s_now = min(s, s_cam), min(s_now, s_cam)
            top = self.top_of(name)
            level = top_level_for(
                max(0.0, d - self.reach_km) / self.scale,  # the rings grow with X-Plane's image
                zoom,
                finest_readable(top, self.finest_quality),
                self.margin,
            )
            ring = (
                LOAD_KM
                if level == LOAD_LEVEL
                else ring_km(level, zoom) * self.margin * self.scale + self.reach_km
            )
            urgency = d_now - ring
            if level == 0 and top == 0 and zoom in self.growing:
                grow = self._growth(zoom, s, s_now, starting, offer)
                if grow is not None:
                    level, urgency = MIN_LEVEL, grow
                    if offer is not None and self.level_of(name) > level:
                        offer_left += 1
            if self.level_of(name) <= level:
                patch = self.patchy.get(name)
                if patch is None or patch[0] > now:
                    continue
                # built without pieces that did not come: again, as sharp, never waited for
                level, urgency = self.level_of(name), max(urgency, 1.0)
            jobs[name] = (urgency, level)
        if offer is not None:
            offer.counted(offer_left, sum(1 for u, _ in jobs.values() if u <= 0), self.log)
        with (
            self.lock
        ):  # the queue and the reload flag together: the builder must not see one without the other
            self.queue = {
                n: j
                for n, j in jobs.items()
                if not (n in self.in_flight and self.in_flight[n][1] <= j[1])
            }
            for n, (
                u,
                lv,
            ) in self.in_flight.items():  # urgent as this plan says, not as the one that took it
                self.in_flight[n] = (jobs[n][0] if n in jobs else max(u, 1.0), lv)
            if jumped:
                self.reload_pending = True
            self.last_fix = fix
        return jobs

    def take_batch(self, max_pieces: int = BATCH_PIECES) -> list[tuple[str, int, float]]:
        """The most urgent queued textures, up to ``max_pieces`` pieces (at least one texture)."""
        with self.lock:
            batch: list[tuple[str, int, float]] = []
            pieces = 0
            for name, (urgency, level) in sorted(self.queue.items(), key=lambda kv: kv[1][0]):
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
            for _, tlat, tlon, _ in self.index.around(lat, lon, 100.0)
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

    def maybe_reload(self) -> bool:
        with self.lock:  # the queue and the flag as one plan left them
            urgent = len(self._urgent_locked())
            due = self.reload_pending and self.last_fix is not None and urgent == 0
            if due:
                self.reload_pending = False
        if due:
            if self.xp is not None:
                self.xp.command(RELOAD_COMMAND)
            self.log("everything X-Plane may show is built: asked it to reload the scenery")
        return due

    # --- the reload offered after a start (start_mode "reload") ------------------------------

    def offer_reload(self, lat: float, lon: float) -> None:
        """X-Plane has started there at 4096 px: build the 8192 px textures around the aircraft,
        as a start in "around" would have waited for, and offer a reload once they are built."""
        if self.start_mode != "reload" or not self.growing:
            return
        o = self.offer
        if (
            o is not None
            and o.asked is not None
            and time.time() - o.asked < RELOADED_S
            and haversine_m(o.lat, o.lon, lat, lon) <= OFFER_KM * 1000
        ):
            return  # the start X-Plane went through was the reload itself
        self.offer = Offer(lat, lon, time.time())
        self.log(
            "  X-Plane started at 4096 px: building the 8192 px textures around the aircraft, "
            "then a reload of the scenery is offered"
        )

    def _offer_near(self, fix: Fix) -> Offer | None:
        """The offer, while the aircraft is still at its start and the reload not asked."""
        o = self.offer
        if o is None or o.asked is not None:
            return None
        if haversine_m(o.lat, o.lon, fix.lat, fix.lon) > OFFER_KM * 1000:
            self.offer = None
            self.log(
                "  the aircraft has left its start: no reload offered any more (in flight, the "
                "textures ahead grow to 8192 px with none)"
            )
            return None
        return o

    def reload_now(self) -> bool:
        """The reload offered, asked for: the Live window's button, or the plugin's in X-Plane."""
        o = self.offer
        if o is None or not o.ready or o.asked is not None:
            return False
        o.asked = time.time()
        if self.xp is not None:
            self.xp.command(RELOAD_COMMAND)
        self.log("  reloading the scenery: X-Plane reads the 8192 px textures around the aircraft")
        return True

    def offer_state(self) -> dict | None:
        """The offer as the Live window and the plugin show it: ``building`` (``left`` textures to
        go), ``ready``, or ``asked``; ``id`` tells one offer from the next."""
        o = self.offer
        if o is None:
            return None
        state = "asked" if o.asked is not None else "ready" if o.ready else "building"
        return {"state": state, "left": max(0, o.left), "id": int(o.since)}

    # --- building -----------------------------------------------------------------------------

    def _finished(self, fut: Future, name: str, level: int, patched: bool = False) -> None:
        try:
            fut.result()
            with self.lock:  # the status line and the save copy them whole, from the main thread
                self.built[name] = level
                self.done[level] += 1
                if level < 0:  # its file is 8192 px now
                    self.grown.add(name)
                if patched:  # built again later, a little later each time
                    tries = self.patchy.get(name, (0.0, 0))[1]
                    wait = min(PATCH_RETRY_MAX_S, PATCH_RETRY_S * 2**tries)
                    self.patchy[name] = (time.time() + wait, tries + 1)
                else:
                    self.patchy.pop(name, None)
        except Exception as e:  # the planner queues it again
            self.errors += 1
            self.log(f"  failed to build {name} at level {level}: {e}")
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
        from .texture import build_job

        answers: queue.Queue[tuple[Key, int, bytes | None]] = queue.Queue()
        self.downloader = self.downloader_factory(
            lambda key, status, body: answers.put((key, status, body))
        )
        jobs: dict[str, _Job] = {}
        waiting: dict[Key, set[str]] = defaultdict(set)
        requested: set[Key] = set()
        asked_at: dict[Key, float] = {}  # when each piece awaited was asked for
        encoding: dict[Future, _Job] = {}
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
                job.stage = "encoding"
                top = min(job.level, self.top_of(job.name))  # 8192 px for its level -1: it grows
                fut = pool.submit(
                    build_job,
                    (
                        job.name,
                        job.level,
                        job.pieces,
                        job.parents,
                        str(self.pack_dir / "textures" / job.name),
                        self.neutral_for(top),
                        top,
                    ),
                )
                encoding[fut] = job

        def answer(key: Key, status: int, body: bytes | None, pool) -> None:
            requested.discard(key)
            asked_at.pop(key, None)
            value = body if status == IMAGE else None
            failed = status not in (IMAGE, NO_IMAGERY)  # every try failed: not "no imagery here"
            if not failed:
                to_cache.append((key, status, value))
                self.last_progress = time.monotonic()
            for name in waiting.pop(key, ()):
                job = jobs.get(name)
                if job is not None:
                    without(job, key, value, failed, pool)

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
        with ProcessPoolExecutor(
            max_workers=self.workers, initializer=worker_ignores_ctrl_c
        ) as pool:
            try:
                while not self.stop.is_set() or encoding:
                    if not self.stop.is_set() and len(encoding) < self.workers * 4:
                        room = WINDOW_PIECES - len(requested)
                        if room > 0:
                            for name, level, urgency in self.take_batch(room):
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
                    for fut in [f for f in encoding if f.done()]:
                        job = encoding.pop(fut)
                        jobs.pop(job.name, None)
                        self._finished(fut, job.name, job.level, bool(job.patched))
                    self.pieces_pending = len(requested)
                    if not requested:  # nothing awaited: nothing stalled
                        self.last_progress = time.monotonic()
                    if to_cache and (len(to_cache) >= 256 or time.monotonic() - last_flush > 1.0):
                        self.cache.put_many(self.provider, to_cache)
                        to_cache.clear()
                        last_flush = time.monotonic()
                    self.maybe_reload()
                    if time.monotonic() - last_evict > EVICT_EVERY_S:
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

    def far(self, name: str) -> bool:
        """Beyond anything X-Plane has loaded: more than 300 km from the aircraft and the camera."""
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

    def evict(self) -> int:
        """Keep the pack under its disk budget and the imagery cache under its own. Builder
        thread."""
        from .texture import downgrade

        with self.lock:
            busy = set(self.in_flight) | set(self.queue)
        picks = pick(
            self.built, self.seen, lambda n: n not in busy and self.far(n), self.disk_budget
        )
        for name, level in picks[:EVICT_BATCH]:
            path = self.pack_dir / "textures" / name
            start = self.start_top_of(name)  # a grown texture goes back to its start size
            try:
                downgrade(
                    path,
                    level,
                    self.pack_dir / neutral_file(start),
                    file_top=dds.read_top(path),
                    to_top=start,
                )
            except OSError as e:
                self.log(f"  could not make {name} coarser: {e}")
                continue
            with self.lock:
                self.grown.discard(name)
                self.patchy.pop(name, None)
                if level >= NEUTRAL_LEVEL:
                    self.built.pop(name, None)
                    self.seen.pop(name, None)
                else:
                    self.built[name] = level
            self.evicted += 1
        if picks:
            self.log(
                f"  disk budget: {min(len(picks), EVICT_BATCH)} textures made coarser, "
                f"{usage(self.built) / 1e9:.1f} GB of {self.disk_budget / 1e9:.0f} GB now"
            )
        size = self.cache.size_bytes()
        if size > self.cache_budget:
            self.cache_freed += self.cache.evict_oldest(size - int(self.cache_budget * TARGET))
        return len(picks)

    def start(self) -> None:
        self._thread = threading.Thread(target=self.build_loop, name="live-builder", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.stop.set()
        if self._thread is not None:
            self._thread.join()
        self.save()

    def save(self) -> None:
        with self.lock:  # the builder changes them meanwhile
            textures = dict(self.built)
            seen = {n: round(t) for n, t in self.seen.items() if n in textures}
            grown = sorted(self.grown)
            patchy = sorted(n for n in self.patchy if n in textures)
        meta = load_built(self.pack_dir)
        if self.screen is not None:
            meta["screen"] = self.screen
        meta["textures"] = textures
        meta["seen"] = seen
        meta["grown"] = grown
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
            levels = Counter(self.built.values())
            done = dict(self.done)
            grown = len(self.grown)
        dl = self.downloader
        return {
            "first_level": MIN_LEVEL,  # the lists below: levels -1 (8192 px) to 4 (256 px)
            "queued": [queued.get(lv, 0) for lv in LEVELS],
            "urgent": urgent,
            "building": flying,
            "pieces_awaited": self.pieces_pending,
            "built": [done.get(lv, 0) for lv in LEVELS],  # since the streamer started
            "downloaded": getattr(dl, "downloaded", 0),
            "downloaded_bytes": getattr(dl, "bytes", 0),
            "from_cache": self.from_cache,
            "failed": getattr(dl, "failed", 0),
            "disk_bytes": levels_usage(levels),
            "disk_budget": self.disk_budget,
            "reload_pending": self.reload_pending,
            "grown": grown,  # textures at 8192 px
            "start_mode": self.start_mode,
            "offer": self.offer_state(),
            "flight_plan": None
            if self.flight_plan is None
            else {"name": self.flight_plan.name, "following": self.on_route},
            # X-Plane's image, and how far it reads 4096 px of a ZL16 texture there
            "screen": None
            if self.screen is None
            else {**self.screen, "scale": self.scale, "km_4096": ring_km(0, 16) * self.scale},
        }

    def status(self) -> str:
        s = self.snapshot()

        def by_level(counts: list[int]) -> str:
            return " ".join(f"L{lv}:{n}" for lv, n in zip(LEVELS, counts, strict=True) if n)

        q = by_level(s["queued"]) or "empty"
        d = by_level(s["built"]) or "nothing yet"
        return (
            f"queue {q} (urgent {s['urgent']}), building {s['building']}, "
            f"{s['pieces_awaited']} pieces awaited | built {d} | "
            f"pieces {s['downloaded']} downloaded ({s['downloaded_bytes'] / 1e6:.0f} MB), "
            f"{s['from_cache']} from cache"
            + (f", {s['failed']} failed" if s["failed"] else "")
            + f" | disk {s['disk_bytes'] / 1e9:.1f}/{s['disk_budget'] / 1e9:.0f} GB"
            + (" | reload pending" if s["reload_pending"] else "")
            + _offer_status(s["offer"])
        )


def _offer_status(offer: dict | None) -> str:
    if offer is None or offer["state"] == "asked":
        return ""
    if offer["state"] == "ready":
        return " | 8192 px ready around the aircraft: reload offered"
    return f" | 8192 px around the aircraft: {offer['left']} to build before the reload"


def reload_after_prepared_start(ready_at: float | None, xplane_started: float | None) -> bool:
    """With ``--from``, whether X-Plane may have read textures before they were built: it started
    before the area was ready (or we cannot tell). Then it gets a reload once everything is."""
    return ready_at is None or xplane_started is None or xplane_started < ready_at


@dataclass
class Offer:
    """A reload of the scenery offered after a start, in the "reload" start mode: X-Plane started
    with 4096 px textures, and once the 8192 px ones around the aircraft are built, a reload shows
    them (X-Plane reads a texture's levels again only when it loads it again)."""

    lat: float
    lon: float
    since: float
    left: int = -1  # 8192 px textures still to build around the aircraft (-1: not counted yet)
    ready: bool = False
    asked: float | None = None  # when the reload was asked

    def counted(self, left: int, urgent: int, log: Callable[[str], None]) -> None:
        """What the planner found: ready once every 8192 px texture is built, and nothing X-Plane
        may be showing is left (the reload reads everything again)."""
        was = self.ready
        self.left = left
        self.ready = left == 0 and urgent == 0
        if self.ready and not was:
            log(
                "  the 8192 px textures around the aircraft are built: reload the scenery to see "
                "them (the button in X-Plane, or in the Live window)"
            )


@dataclass
class Arrival:
    """A flight X-Plane is about to load, or is loading: where, and what the plugin was told."""

    lat: float
    lon: float
    name: str
    # "from" (--from), "last" (the last start, at launch), "click" (Freeflight.prf), "plugin"
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


def needs_reload(arrival: Arrival, xplane_started: float | None) -> bool:
    """Whether X-Plane, now there, may have read textures of the start before they were built."""
    if arrival.released:
        return False  # the plugin held it until everything was built
    if arrival.ready_at is not None and arrival.ready_at - arrival.since < 1.0:
        return False  # already built when X-Plane was told to start there
    if arrival.how in ("from", "last") and not arrival.held:
        return reload_after_prepared_start(arrival.ready_at, xplane_started)
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
    "last": "the last flight started at {}: building there while X-Plane starts",
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
        }
    fix = streamer.last_fix
    camera_km = None
    if fix is not None and fix.cam_lat is not None and fix.cam_lon is not None:
        camera_km = haversine_m(fix.lat, fix.lon, fix.cam_lat, fix.cam_lon) / 1000.0
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
    guess_last: bool = True,
    report: Callable[[dict], None] | None = None,
    reload_asked: Callable[[], bool] = lambda: False,
    language: Callable[[], str] = lambda: "en",
) -> None:
    """Follow X-Plane over UDP until Ctrl-C (or ``should_stop``), planning every two seconds.

    ``gate`` (``orthostudio.live.gate.Gate``) hears of a flight's start from Freeflight.prf and
    from the plugin,
    and holds X-Plane until everything it will read there is built. ``report`` is given every
    second what the Live window shows (:func:`progress_report`). ``reload_asked`` says, once, that
    the Live window's button asked for the reload offered (:meth:`Streamer.reload_now`)."""
    from .scenery import xplane_started_at

    fields = [name for name, _ in DATAREFS]
    values: dict[str, float] = {}
    arrival: Arrival | None = None
    last_plan = last_status = last_save = last_packet = last_subscribe = last_wait = 0.0
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

    camera = CameraLog(streamer.pack_dir / "_live" / "camera.json")  # how far external views go
    streamer.start()
    streamer.refresh_flight_plan()
    if start is not None:
        expect(start.lat, start.lon, start_name or f"{start.lat:.3f} {start.lon:.3f}", "from")
    elif gate is not None and guess_last:
        last = gate.last_start()
        if last is not None:
            expect(*last, "last")
    try:
        while not should_stop():
            if streamer.crashed is not None:
                raise SystemExit(f"the streamer stopped: {streamer.crashed!r}")
            now = time.time()
            mbs = rate.mbs(now, getattr(streamer.downloader, "bytes", 0))
            if gate is not None:
                clicked = gate.new_start()
                if clicked is not None:
                    expect(*clicked, "click")
                running = "lat" in values and now - last_packet < FRESH_S
                for kind, lat, lon, addr, why in gate.requests():
                    if kind == "ASK":  # the plugin, every second of a flight: a reload offered?
                        notice, lang = streamer.notice, language()
                        note = plugin_note(notice, lang) if notice else None
                        gate.offer(addr, streamer.offer_state(), note, notice, lang=lang)
                        continue
                    if kind == "RELOAD":  # its button, in X-Plane
                        streamer.reload_now()
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
                        left, work = streamer.urgent_work()
                        done = arrival.progress(work)
                        took = now - arrival.since
                        arrival.held = True
                        # no time limit: X-Plane waits while pieces arrive, however long a start
                        # takes, and goes on only once nothing has arrived for a minute
                        stalled = left > 0 and streamer.stalled_s() > STALL_S
                        gate.answer(
                            addr,
                            left == 0 or stalled,
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
                                f"there?); the scenery is reloaded once they are built"
                            )
                        elif left == 0 and not arrival.released:
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
            if reload_asked():
                streamer.reload_now()
            if arrival is not None and arrival.ready_at is None and streamer.urgent_left() == 0:
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
            if now - last_plan >= PLAN_EVERY_S and (values.get("lat") or arrival is not None):
                last_plan = now
                streamer.set_screen(values.get("win_w"), values.get("fov"))
                if now - last_packet < FRESH_S and arrival is None:  # flying, not loading
                    camera.note(values, now)
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
                running = fix is not None and now - last_packet < FRESH_S
                if arrival is not None and not running:
                    arrival.loading = True  # X-Plane stopped sending its position: it is loading
                if arrival is None:
                    if fix is not None:
                        streamer.plan(
                            fix
                        )  # a jump is a new start: X-Plane gets a reload once it is built
                elif running and arrival.near(fix.lat, fix.lon):  # X-Plane is there
                    if arrival.loading and needs_reload(arrival, xplane_started_at()):
                        streamer.reload_pending = (
                            True  # (never loading: X-Plane was there all along)
                        )
                    streamer.plan(fix, x_plane_there=False)
                    streamer.offer_reload(fix.lat, fix.lon)
                    arrival = None
                elif running:  # X-Plane runs somewhere else
                    if arrival.elsewhere_since is None:
                        arrival.elsewhere_since = now
                    elif now - arrival.elsewhere_since > ELSEWHERE_S:
                        log(f"  X-Plane did not start at {arrival.name}")
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
