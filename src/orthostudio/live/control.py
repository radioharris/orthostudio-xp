# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The Live window's stream: started and stopped from its page, run in a thread of its own.

It runs what ``python -m orthostudio.live stream`` runs (:mod:`orthostudio.live.session`), and
keeps for the page what the terminal would have shown: the state, the figures of the last second
(:func:`orthostudio.live.streamer.progress_report`), and the log.

The states:

- ``idle``: nothing streams;
- ``starting``: the checks, the region set up (seconds, the first time), the pack and the plugin
  put in X-Plane;
- ``streaming``: following X-Plane;
- ``stopping``: asked to stop, the builder finishing its textures;
- ``leaving``: stopped while X-Plane still runs; it reads its textures from the pack, which is
  taken out once X-Plane quits, unless the user leaves it installed;
- ``deleting``: the built textures, the downloaded imagery or both being forgotten, nothing
  streaming.
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orthostudio.decals import DECALS
from orthostudio.live import aoregions, session, supported
from orthostudio.live.evict import usage
from orthostudio.live.pack import (
    LINK_NAME,
    FolderError,
    check_folder,
    default_live_root,
    default_pack_dir,
    live_root,
    load_built,
)
from orthostudio.live.region import file_tops
from orthostudio.live.scenery import (
    autoortho_mounted,
    autoortho_regions,
    autoortho_scenery,
    default_sources,
    is_installed,
    launch_xplane,
    ortho_streamers,
    osxp_regions,
    xplane_running,
)
from orthostudio.live.settings import LiveSettings, load_settings

API = 1  # the page's /api/live answers, as the page reads them
CHECKS_S = 3.0  # the checks are looked at again this often: pgrep and disks, not every second
CATALOG_S = 600.0  # AutoOrtho's list of regions asked for again this often (GitHub: hourly)
OWN_S = 30.0  # the user's own tiles looked for again this often, or when X-Plane's list changes
LOG_LINES = 2000
IDLE, STARTING, STREAMING = "idle", "starting", "streaming"
STOPPING, LEAVING, DELETING = "stopping", "leaving", "deleting"
SUGGEST_S = 300.0  # the departure of the pilot's last SimBrief plan, asked at most this often
FRESH_S = 30.0  # as often, while the window looks for a new plan (the pilot back from simbrief.com)
NOW_S = 5.0  # as often, asked by the button of the window's plan line: at once, but no faster


def resolve_departure(text: str) -> tuple[float, float]:
    """Where a departure the window asks to prepare is: an airport X-Plane knows (its ICAO code),
    or "lat,lon". Raises ValueError, saying why, where there is none."""
    from orthostudio.live.route import Route

    try:
        return Route.resolve(session.xplane_dir(None), [text]).points[0]
    except (SystemExit, session.RefusedError) as e:
        raise ValueError(str(e)) from e


LEAVE_HINT = "Leave it installed, in the window, to stop waiting"
STOP_HINT = "Stop in the window to end"


SOURCE_ENV = "OSXP_LIVE_SOURCE"
"""The region a test flight's Live streams onto, as ``stream --source`` names it: a cold run of the
app (``tools/live/flights/fly.py --streamer app --cold``), whose folder of its own holds no region
(a Shadow PC without AutoOrtho, 2026-10-04). Set for the app's process by the test flights alone."""


def stream_sources(xp: Path) -> list[Path]:
    """The regions the stream lays its photos on: a test flight's (SOURCE_ENV), else those
    installed. The checks read the same: a cold test flight's window said « No mesh yet » while
    Live streamed onto the region the flight gave it (the user's Shadow, 2026-10-04)."""
    named = os.environ.get(SOURCE_ENV)
    return [Path(named)] if named else default_sources(xp)


def options_for(settings: LiveSettings, *, fresh: bool) -> session.Options:
    named = os.environ.get(SOURCE_ENV)
    return session.Options(
        source=Path(named) if named else None,
        provider=settings.provider,
        disk_budget_gb=settings.textures_gb,  # Live's one disk space, shared
        cache_budget_gb=settings.imagery_gb,
        max_zoom=settings.max_zoom,
        max_zoom_airports=settings.max_zoom_airports,
        simbrief_user=settings.simbrief_user,
        overlays=settings.overlays,
        language=settings.language,
        tint_8192=settings.tint_8192,
        season=settings.season,
        looks=settings.seasons.looks(),
        decal=settings.decal,
        own_tiles=settings.own_tiles,
        fresh=fresh,
    )


def _osxp_simbrief() -> str | None:
    try:
        from orthostudio.config.store import load_settings as osxp_settings

        return (osxp_settings().essential.simbrief_user or "").strip() or None
    except Exception:  # OrthoStudio XP's settings unreadable: nothing to offer
        return None


_own: tuple[float, tuple[str, float, str], dict[tuple[int, int], str]] = (0.0, ("", -1.0, ""), {})


def own_tiles(xp: Path, choice: str = "first") -> dict[tuple[int, int], str]:
    """``{(lat, lon): pack}``: the squares X-Plane takes from the user's own scenery (a tile
    OrthoStudio XP built, one imported from Ortho4XP, another ortho), Live stopped or not
    (:func:`orthostudio.live.covered.own_squares`, ``choice`` the setting own_tiles). Looked for
    again when X-Plane's list of packs or the choice changes, else every OWN_S: about 0.1 s each
    time, where the checks are every 3 s."""
    global _own
    from orthostudio.live.covered import own_squares
    from orthostudio.live.scenery import _ini

    try:
        seen = (str(xp), _ini(xp).stat().st_mtime, choice)
    except OSError:
        return {}
    at, was, got = _own
    if was == seen and time.monotonic() - at < OWN_S:
        return got
    try:
        got = own_squares(xp, LINK_NAME, choice)
    except OSError:
        got = {}
    _own = (time.monotonic(), seen, got)
    return got


def made_by(pack: str) -> str:
    """Who made a pack of the user's: "osxp" (a tile OrthoStudio XP built), "ortho4xp" (one
    imported from Ortho4XP), "other" (another scenery's ground)."""
    from orthostudio.install.scenery_packs import IMPORTED_PACK_PREFIX, ORTHO_PREFIX

    if pack.startswith(ORTHO_PREFIX):
        return "osxp"
    return "ortho4xp" if pack.startswith(IMPORTED_PACK_PREFIX) else "other"


def gather_checks() -> dict[str, Any]:
    """What streaming needs, as the page lists it."""
    from orthostudio.live import machine, plugin

    cores, ram_gb = machine.machine()
    out: dict[str, Any] = {
        # this computer, and the memory ZL17 and ZL19 want: the page warns below it
        "machine": {"cores": cores, "ram_gb": round(ram_gb), "sharp_ram_gb": machine.SHARP_RAM_GB},
        "supported": supported(),
        "xplane": None,
        "xplane_problem": None,
        "xplane_running": xplane_running(),
        "mesh": None,
        "mesh_found": False,
        "region_ready": False,
        "autoortho_mounted": False,
        "ortho_streamers": [],  # the other programs streaming ortho that run (XPME, AutoOrtho)
        "installed": False,
        "plugin": plugin.availability(),
        "folder": str(live_root()),
        "folder_default": str(default_live_root()),
        "folder_problem": None,
        "textures_bytes": usage(
            load_built(default_pack_dir()).get("textures", {}), file_tops(default_pack_dir())
        ),
        # the SimBrief name OrthoStudio XP has, which the window offers to take: never asked of
        # simbrief.com unless the pilot takes it into Live's settings
        "osxp_simbrief": _osxp_simbrief(),
        "imagery_bytes": session.imagery_bytes(),
    }
    try:
        xp = session.xplane_dir(None)
    except session.RefusedError as e:
        out["xplane_problem"] = str(e)
        return out
    try:
        check_folder(live_root(), custom_scenery=xp / "Custom Scenery")
    except FolderError as e:
        out["folder_problem"] = str(e)
    sources = stream_sources(xp)
    out.update(
        xplane=str(xp),
        # the regions installed, by name, or where they would be
        mesh=", ".join(s.name for s in sources) if sources else str(autoortho_scenery(xp)),
        mesh_regions=[s.name.removeprefix("z_ao_") for s in sources],
        mesh_found=bool(sources),
        region_ready=session.region_ready(default_pack_dir(), sources),
        autoortho_mounted=autoortho_mounted(xp),
        ortho_streamers=ortho_streamers(xp),
        installed=is_installed(xp, LINK_NAME),
        # the squares X-Plane takes from the user's own tiles: Live downloads nothing there
        own_tiles=[
            [lat, lon, pack]
            for (lat, lon), pack in sorted(own_tiles(xp, load_settings().own_tiles).items())
        ],
    )
    return out


def our_regions(root: Path) -> list[dict[str, Any]]:
    """Live's own mesh as the page lists it: each region installed (``regions/z_osxp_<id>``, a
    folder or a link to one on another disk) with its size on the disk, written in its marker once
    at the install: counting millions of small files at every status would take minutes."""
    out = []
    for folder in osxp_regions(root):
        try:
            marker = json.loads((folder / aoregions.MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            marker = {}
        size = marker.get("size") if isinstance(marker, dict) else None
        out.append(
            {
                "id": folder.name.removeprefix("z_osxp_"),
                "size": size if isinstance(size, int) else None,
                "elsewhere": folder.is_symlink(),
                # where it really is: the folder holding it, the link's target's for one elsewhere
                "folder": str(folder.resolve().parent),
            }
        )
    return out


class LiveControl:
    """One stream at a time, for the page. The session's steps and the checks can be replaced, for
    the tests."""

    def __init__(
        self,
        *,
        prepare: Callable[..., session.Prepared] = session.prepare,
        follow: Callable[..., None] = session.follow,
        finish: Callable[..., bool] = session.finish,
        uninstall: Callable[..., None] = session.uninstall,
        forget_textures: Callable[..., None] = session.forget_textures,
        forget_imagery: Callable[..., None] = session.forget_imagery,
        launch: Callable[..., None] = launch_xplane,
        checks: Callable[[], dict[str, Any]] = gather_checks,
        settings: Callable[[], LiveSettings] = load_settings,
        echo: Callable[[str], None] = print,
        catalog: Callable[..., dict] | None = None,
        installer: Callable[..., Any] | None = None,
        log_file: Path | None = None,
        resolve: Callable[[str], tuple[float, float]] = resolve_departure,
        simbrief: Callable[[str], tuple[Any, str]] | None = None,
    ) -> None:
        self._prepare = prepare
        self._follow = follow
        self._finish = finish
        self._uninstall = uninstall
        self._forget_textures = forget_textures
        self._forget_imagery = forget_imagery
        self._launch = launch
        self._checks = checks
        self._settings = settings
        self._echo = echo
        self._starting_with: session.Options | None = None  # the start under way's
        self.log_file = log_file  # where the log is kept as a file too (orthostudio.live.logfile)
        self._lock = threading.Lock()
        self._state = IDLE
        self._error: str | None = None
        self._report: dict | None = None
        self._lines: deque[tuple[int, str]] = deque(maxlen=LOG_LINES)
        self._seq = 0
        self._stop = threading.Event()
        self._leave = threading.Event()
        # why this window goes: "build", the welcome page's answer, for OrthoStudio XP's window;
        # "quit", the app being updated or removed (:meth:`quit`)
        self.leaving_for: str | None = None
        # the window's Quit, answered yes: it goes once the stream is out (:meth:`close`)
        self.closing = False
        # shown in the app's frame (orthostudio.live.frame): its page stays when the welcome's
        # answer is building, the frame showing OrthoStudio XP's part beside it
        self.in_frame = False
        self._thread: threading.Thread | None = None
        self._checked: tuple[float, dict[str, Any] | None] = (0.0, None)
        # AutoOrtho's regions: GitHub's list, read in the background, and one install at a time
        self._catalog_of = catalog or aoregions.catalog
        self._installer_of = installer or aoregions.Installer
        self._catalog: dict | None = None
        self._catalog_error: str | None = None
        self._catalog_at = 0.0
        self._catalog_thread: threading.Thread | None = None
        self._installer: Any = None
        self._install_thread: threading.Thread | None = None
        # the window's "Prepare": a departure for the running stream, and SimBrief's suggestion
        self._resolve = resolve
        self._simbrief = simbrief
        self._departure: tuple[float, float, str] | None = None
        self._suggested: tuple[float, str, dict[str, str]] = (0.0, "", {})
        # the running streamer, for the map: what it has built, from its memory
        self.streamer: Any = None
        self._map_rows: tuple[int, list] = (-1, [])
        # the window's language, which the plugin's words in X-Plane follow while a stream runs
        try:
            self.language = self._settings().language
        except Exception:  # settings unreadable: the window says it elsewhere
            self.language = "en"

    # --- what the page reads -----------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    def log(self, text: str) -> None:
        """A line of the log: the page's, and the terminal's the window was opened from."""
        with self._lock:
            for line in text.splitlines() or [""]:
                self._seq += 1
                self._lines.append((self._seq, line))
        self._echo(text)

    def keep_region_lists_aside(self) -> threading.Thread:
        """Each region read into its list in the background, where that list is missing or old
        (``session.keep_region_lists``): the window does it as it opens, so that its Start or
        Prepare reads lists only, or waits for the one being made."""

        def run() -> None:
            try:
                xp = session.xplane_dir(None)
            except session.RefusedError:
                xp = None
            try:
                session.keep_region_lists(xp, self.log)
            except Exception as e:  # a start lists what is left
                self.log(f"the regions could not be read into their lists: {e}")

        thread = threading.Thread(target=run, name="live-region-lists", daemon=True)
        thread.start()
        return thread

    def checks(self, fresh: bool = False) -> dict[str, Any]:
        at, got = self._checked
        if fresh or got is None or time.monotonic() - at > CHECKS_S:
            got = self._checks()
            self._checked = (time.monotonic(), got)
        return got

    def status(self) -> dict[str, Any]:
        from orthostudio.fsutil import platform_name

        with self._lock:
            state, error, report, seq = self._state, self._error, self._report, self._seq
        return {
            "api": API,
            "state": state,
            "relayout": state == STARTING and self._relayout_due(),
            "error": error,
            "stream": report,
            "log_seq": seq,
            "log_file": None if self.log_file is None else str(self.log_file),
            "platform": platform_name(),  # mac, win or lin: what the file manager is called
            "checks": self.checks(),
            "settings": self._settings().model_dump(),
            "decals": list(DECALS),
            "regions": self.regions(),
        }

    def _relayout_due(self) -> bool:
        """Whether the start under way is laying the pack out for a new zoom, read from the pack
        each time asked: done, it says so at once."""
        options = self._starting_with
        if options is None:
            return False
        try:
            return session.relayout_due(options)
        except Exception:  # the pack unreadable this second: the start says the rest
            return False

    def log_after(self, after: int) -> dict[str, Any]:
        with self._lock:
            lines = [{"seq": s, "text": t} for s, t in self._lines if s > after]
            return {"lines": lines, "seq": self._seq}

    # --- what the page asks ------------------------------------------------------------------

    def start(self, *, fresh: bool = False, departure: str | None = None) -> bool:
        """Start streaming, building round ``departure`` straight away where given (an airport's
        ICAO code, as ``--from``); False when a stream is already on its way (or on its way
        out)."""
        with self._lock:
            if self._state != IDLE:
                return False
            self._state, self._error, self._report = STARTING, None, None
            self._stop.clear()
            self._leave.clear()
            self._departure = None
        options = options_for(self._settings(), fresh=fresh)
        if departure:
            options.start = departure
        self._starting_with = options
        self._thread = threading.Thread(
            target=self._run, args=(options,), name="live-stream", daemon=True
        )
        self._thread.start()
        return True

    def prepare_departure(self, text: str) -> tuple[str, str] | None:
        """The window's "Prepare": the ortho round ``text`` (an airport's ICAO code, or lat,lon)
        built before X-Plane loads that flight, which then passes its loading screen at once;
        Live started for it where it does not run. None, or (why: "empty", "unknown" or "busy",
        what to say)."""
        text = " ".join(text.split()).upper()
        if not text:
            return "empty", "no departure given"
        try:
            lat, lon = self._resolve(text)
        except ValueError as e:
            return "unknown", str(e)
        with self._lock:
            state = self._state
            if state == STREAMING:
                self._departure = (lat, lon, text)
                return None
        if state == IDLE and self.start(departure=text):
            return None
        return "busy", "Live is starting or stopping: prepare it in a moment"

    def _take_departure(self) -> tuple[float, float, str] | None:
        """The departure the window asked for since the stream last looked, once."""
        with self._lock:
            wanted, self._departure = self._departure, None
        return wanted

    def departure_suggestion(self, fresh: bool = False, now: bool = False) -> dict[str, str]:
        """The departure of the pilot's last SimBrief plan, which the window's "Prepare" box
        offers: {"icao": "LFMN", "plan": "LFMN-LFPG", "briefing": "123456"}, the briefing telling
        the window a new plan from the one it shows; {} where no SimBrief name is set in Live's
        settings (nothing is asked of simbrief.com then) or no plan came. Asked at most every
        SUGGEST_S, FRESH_S when the window looks for a new plan (``fresh``), NOW_S when the pilot
        asks with the plan line's button (``now``)."""
        user = self._settings().simbrief_user.strip()
        if not user:
            return {}
        at, asked_for, got = self._suggested
        wait = NOW_S if now else FRESH_S if fresh else SUGGEST_S
        if asked_for == user and time.monotonic() - at < wait:
            return got
        if self._simbrief is not None:
            plan, _why = self._simbrief(user)
        else:
            from orthostudio.live.flightplan import last_plan

            plan, _why = last_plan(user)
        got = (
            {}
            if plan is None
            else {
                "icao": plan.name.split("-")[0],
                "plan": plan.name,
                "briefing": plan.briefing or plan.name,
            }
        )
        self._suggested = (time.monotonic(), user, got)
        return got

    def stop(self) -> None:
        """Stop streaming. The pack comes out of X-Plane once X-Plane has quit."""
        with self._lock:
            if self._state in (STARTING, STREAMING):
                self._state = STOPPING
        self._stop.set()

    def leave(self) -> None:
        """Stop waiting for X-Plane to quit: the pack and the plugin stay installed."""
        self._leave.set()

    def busy(self) -> bool:
        """Whether a stream is on its way, in or out, or a region is being installed: the app,
        started from its icon, does not let Live's process go then (``orthostudio.live.web``)."""
        with self._lock:
            installing = self._install_thread is not None and self._install_thread.is_alive()
            return self._state != IDLE or installing

    def quit(self, *, force: bool = False) -> bool:
        """The app is being updated or removed (the Windows installer, ``stop_running.pas``): stop,
        and let the window and its process go (``leaving_for``). False, and nothing done, while
        a stream feeds a running X-Plane, unless ``force``: the stream then stops without waiting
        for X-Plane, which flies on over what is on the disk, and the pack stays installed; the
        plugin takes Live's links out of Custom Scenery at X-Plane's next start."""
        flying = self._state != IDLE and xplane_running()
        if flying and not force:
            return False
        self.leaving_for = "quit"
        self.stop()
        if flying:
            self.leave()
        return True

    def close(self) -> None:
        """The window's Quit, once the pilot said yes to its question, as OrthoStudio XP's: a
        stream stops as Stop stops it (Live waits for X-Plane to quit when X-Plane reads the
        pack, which comes out then), and the window goes once that is done (:meth:`gone`)."""
        self.closing = True
        if self._state != IDLE:
            self.stop()

    def gone(self) -> bool:
        """Whether the window and its process go: the welcome's answer was OrthoStudio XP's
        window, the app asked Live to go (:meth:`quit`), or the pilot quit and nothing streams."""
        return self.leaving_for in ("build", "quit") or (self.closing and self._state == IDLE)

    def uninstall(self) -> bool:
        """Take out a pack left installed; False while a stream runs, or while X-Plane does."""
        checks = self.checks(fresh=True)
        if self._state != IDLE or checks["xplane_running"] or not checks["xplane"]:
            return False
        self._uninstall(Path(checks["xplane"]), self.log)  # the X-Plane the window shows
        self.checks(fresh=True)
        return True

    def delete(self, *, textures: bool, imagery: bool) -> None:
        """Forget the built textures (back to the neutral one, built again when flown to), the
        downloaded imagery (downloaded again when needed), or both. Only while nothing streams,
        and the textures only while X-Plane does not run: it reads them."""
        with self._lock:
            if self._state != IDLE:
                raise session.RefusedError("Live is streaming: stop it first")
            self._state = DELETING
        try:
            if textures:
                self._forget_textures(default_pack_dir(), self.log)
            if imagery:
                self._forget_imagery(self.log)
        finally:
            with self._lock:
                self._state = IDLE
            self._checked = (0.0, None)

    def launch_xplane(self) -> None:
        """Start X-Plane, once Live is ready for it: X-Plane sees the pack and loads the plugin only
        if they are there when it starts."""
        if self._state != STREAMING:
            raise session.RefusedError("Live is not ready: start it first")
        checks = self.checks(fresh=True)
        if checks["xplane_running"]:
            raise session.RefusedError("X-Plane is running already")
        self._launch(Path(checks["xplane"]))
        self.log("X-Plane is starting")
        self._checked = (0.0, None)

    def _attach(self, streamer: Any) -> None:
        self.streamer = streamer
        self._map_rows = (-1, [])

    def built_in(
        self, west: float, south: float, east: float, north: float, known: int = -1
    ) -> dict:
        """The textures built in the map's view: from the running streamer's memory, at once, with
        its version (``{"version", "same": true}`` when the map has it already); else from the
        pack's record, which the streamer writes every 30 s."""
        from orthostudio.live.mapdata import built_in, rows_of

        # where the user's own tiles are, X-Plane shows them: Live's textures there are left out
        theirs = {(t["lat"], t["lon"]) for t in self.own_tiles()}
        streamer = self.streamer
        if streamer is None:
            return built_in(default_pack_dir(), west, south, east, north, covered=theirs)
        version, textures = streamer.built_now(known)
        if textures is None:
            return {"version": version, "same": True}
        if self._map_rows[0] != version:  # the rectangles of this version, once
            self._map_rows = (version, rows_of(textures, streamer.top_of))
        out = built_in(
            default_pack_dir(), west, south, east, north, rows=self._map_rows[1], covered=theirs
        )
        return {**out, "version": version}

    def own_tiles(self) -> list[dict]:
        """The squares X-Plane takes from the user's own tiles, for the map: each with its pack and
        who made it (:func:`made_by`). None without X-Plane."""
        xp = self.checks().get("xplane")
        if not xp:
            return []
        return [
            {"lat": lat, "lon": lon, "pack": pack, "kind": made_by(pack)}
            for (lat, lon), pack in sorted(own_tiles(Path(xp), self._settings().own_tiles).items())
        ]

    def textures_at(self, lat: float, lon: float) -> list[dict]:
        """The textures built under a point of the map, with their facts (its tooltip)."""
        from orthostudio.live.mapdata import record, textures_at, zoom_tops

        streamer = self.streamer
        if streamer is not None:
            built, patchy, seen = streamer.facts_now()
            top_of = shown_top = streamer.top_of
        else:
            meta = record(default_pack_dir())
            built, patchy = meta.get("textures", {}), set(meta.get("patchy", []))
            seen = meta.get("seen", {})
            top_of = file_tops(default_pack_dir())
            shown_top = zoom_tops(default_pack_dir())
        return textures_at(lat, lon, built, patchy, seen, time.time(), top_of, shown_top)

    def set_language(self, language: str, *, save: Any = None) -> None:
        """The window's language, as its EN/FR selector has it: the plugin's words in X-Plane take
        it at once, and it is kept for the next starts."""
        if language not in ("en", "fr"):
            raise ValueError(f"no language {language!r}")
        self.language = language
        settings = self._settings()
        if settings.language != language:
            from orthostudio.live.settings import save_settings

            (save or save_settings)(settings.model_copy(update={"language": language}))

    def choose_home(self, home: str, *, remember: bool, save: Any = None) -> None:
        """The welcome page's answer: Live's window, or OrthoStudio XP's (this window then closes
        and the app opens that one). Kept for the next starts when ``remember``."""
        if remember:
            from orthostudio.live.settings import save_settings

            settings = self._settings().model_copy(update={"app_home": home})
            (save or save_settings)(settings)
        if home == "build" and not self.in_frame:
            self.leaving_for = "build"

    # --- AutoOrtho's regions -----------------------------------------------------------------

    def _read_catalog(self) -> None:
        try:
            got = self._catalog_of(live_root())
            with self._lock:
                self._catalog, self._catalog_error = got, None
        except Exception as e:
            with self._lock:
                self._catalog_error = str(e)
        finally:
            with self._lock:
                self._catalog_at = time.monotonic()

    def regions(self, *, fetch: bool = False) -> dict[str, Any]:
        """Live's own mesh, region by region (:func:`our_regions`), then AutoOrtho's regions as
        the page lists them: each with its size and whether AutoOrtho or Live installed it; the
        install under way. With ``fetch`` (the page's own request, not its status), GitHub's list
        is read again in the background when it is old."""
        with self._lock:
            reading = self._catalog_thread is not None and self._catalog_thread.is_alive()
            stale = time.monotonic() - self._catalog_at > CATALOG_S
            if fetch and not reading and (self._catalog is None or stale):
                self._catalog_thread = threading.Thread(
                    target=self._read_catalog, name="live-regions", daemon=True
                )
                self._catalog_thread.start()
            catalog, error = self._catalog, self._catalog_error
            installer = self._installer
        mine = aoregions.installed(live_root())
        xp = self.checks().get("xplane")
        theirs = (
            {p.name.removeprefix("z_ao_") for p in autoortho_regions(Path(xp))} if xp else set()
        )
        listed = []
        for region_id in aoregions.REGION_IDS:
            region = (catalog or {}).get(region_id)
            if region is None and region_id not in mine and region_id not in theirs:
                continue
            installed_by = (
                "autoortho" if region_id in theirs else "live" if region_id in mine else None
            )
            listed.append(
                {
                    "id": region_id,
                    "release": region.release if region else None,
                    "size": region.size if region else None,
                    "installed_by": installed_by,
                    "installed_release": mine.get(region_id, {}).get("release"),
                }
            )
        progress = installer.snapshot() if installer is not None else None
        return {
            "ours": our_regions(live_root()),
            "list": listed,
            "error": error if catalog is None else None,
            "install": progress,
        }

    def install_region(self, region_id: str) -> None:
        """Download and install one of AutoOrtho's regions into Live's folder, in the background.
        One at a time; used from the next start on."""
        with self._lock:
            catalog = self._catalog
            busy = self._install_thread is not None and self._install_thread.is_alive()
        if busy:
            raise session.RefusedError("a region is being installed: one at a time")
        region = (catalog or {}).get(region_id)
        if region is None:
            raise session.RefusedError(
                f"AutoOrtho has no region {region_id!r} (or GitHub did not say)"
            )
        installer = self._installer_of(live_root(), region, log=self.log)
        thread = threading.Thread(target=installer.run, name="live-region-install", daemon=True)
        with self._lock:
            self._installer, self._install_thread = installer, thread
        thread.start()

    def cancel_install(self) -> None:
        with self._lock:
            installer = self._installer
        if installer is not None:
            installer.cancel()

    def remove_region(self, region_id: str) -> None:
        """Delete a region from the disk: the one the page shows, AutoOrtho's when AutoOrtho
        installed it (as AutoOrtho's own uninstall does: AutoOrtho no longer has it either), else
        Live's. Not while Live streams, nor while the region is installed, nor for AutoOrtho's while
        AutoOrtho or X-Plane runs."""
        with self._lock:
            installing = (
                self._install_thread is not None
                and self._install_thread.is_alive()
                and self._installer.region.id == region_id
            )
            streaming = self._state != IDLE
        if streaming:
            raise session.RefusedError("Live is streaming: stop it first")
        if installing:
            raise session.RefusedError("this region is being installed: stop that first")
        checks = self.checks(fresh=True)
        xp = Path(checks["xplane"]) if checks.get("xplane") else None
        theirs = {p.name.removeprefix("z_ao_") for p in autoortho_regions(xp)} if xp else set()
        if xp is not None and region_id in theirs:
            if checks.get("autoortho_mounted"):
                raise session.RefusedError("AutoOrtho is running and uses it: quit AutoOrtho first")
            if checks.get("xplane_running"):
                raise session.RefusedError("X-Plane is running: quit it first")
            aoregions.remove_autoortho(xp, region_id, log=self.log)
        elif region_id in aoregions.installed(live_root()):
            aoregions.remove(live_root(), region_id, log=self.log)
        else:
            raise session.RefusedError(f"the region {region_id!r} is not installed")
        self._checked = (0.0, None)

    def wait_idle(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self._state == IDLE

    # --- the stream's thread -----------------------------------------------------------------

    def _set(self, state: str) -> None:
        with self._lock:
            self._state = state

    def _reported(self, report: dict) -> None:
        with self._lock:
            self._report = report
            if self._state == STARTING:
                self._state = STREAMING

    def _run(self, options: session.Options) -> None:
        prepared = None
        try:
            prepared = self._prepare(options, self.log)
            if not self._stop.is_set():
                self._follow(
                    prepared,
                    options,
                    self.log,
                    should_stop=self._stop.is_set,
                    report=self._reported,
                    stop_hint=STOP_HINT,
                    language=lambda: self.language,
                    attach=self._attach,
                    departure=self._take_departure,
                )
        except session.RefusedError as e:
            self._error = str(e)
            self.log(str(e))
        except Exception as e:  # said in the page: the thread has nobody else to tell
            self._error = f"{type(e).__name__}: {e}"
            self.log("the streamer stopped:\n" + "".join(traceback.format_exception(e)))
        finally:
            if prepared is not None and prepared.gate is not None:
                # the plugin's port, which follow closes too: a Stop while the start was being
                # prepared skipped it, and the next Start found the port taken (2026-10-18)
                prepared.gate.close()
            try:
                if prepared is not None:
                    self._set(LEAVING if xplane_running() else STOPPING)
                    if not self._finish(
                        prepared.xplane, self.log, hint=LEAVE_HINT, give_up=self._leave.is_set
                    ):
                        self.log(
                            "the pack stays installed: take it out in the window once X-Plane "
                            "has quit"
                        )
            except Exception as e:
                self.log(
                    "the pack could not be taken out:\n" + "".join(traceback.format_exception(e))
                )
            finally:
                with self._lock:
                    self._state, self._report = IDLE, None
                self._checked = (0.0, None)
