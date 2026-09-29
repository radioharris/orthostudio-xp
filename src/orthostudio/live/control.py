# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The Live window's stream: started and stopped from its page, run in a thread of its own.

It runs what ``python -m orthostudio.live stream`` runs (:mod:`orthostudio.live.session`), and
keeps for the page what the terminal would have shown: the state, the figures of the last second
(:func:`orthostudio.live.streamer.progress_report`), and the log.

The states:

- ``idle``: nothing streams;
- ``starting``: the checks, the region set up (a couple of minutes, the first time), the pack and
  the plugin put in X-Plane;
- ``streaming``: following X-Plane;
- ``stopping``: asked to stop, the builder finishing its textures;
- ``leaving``: stopped while X-Plane still runs; it reads its textures from the pack, which is
  taken out once X-Plane quits, unless the user leaves it installed;
- ``deleting``: the built textures, the downloaded imagery or both being forgotten, nothing
  streaming.
"""

from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
LEAVE_HINT = "Leave it installed, in the window, to stop waiting"
STOP_HINT = "Stop in the window to end"


def options_for(settings: LiveSettings, *, fresh: bool) -> session.Options:
    return session.Options(
        provider=settings.provider,
        disk_budget_gb=settings.textures_gb,  # Live's one disk space, shared
        cache_budget_gb=settings.imagery_gb,
        max_zoom=settings.max_zoom,
        max_zoom_airports=settings.max_zoom_airports,
        simbrief_user=settings.simbrief_user,
        overlays=settings.overlays,
        language=settings.language,
        tint_8192=settings.tint_8192,
        fresh=fresh,
    )


def _osxp_simbrief() -> str | None:
    try:
        from orthostudio.config.store import load_settings as osxp_settings

        return (osxp_settings().essential.simbrief_user or "").strip() or None
    except Exception:  # OrthoStudio XP's settings unreadable: nothing to offer
        return None


_own: tuple[float, tuple[str, float], dict[tuple[int, int], str]] = (0.0, ("", -1.0), {})


def own_tiles(xp: Path) -> dict[tuple[int, int], str]:
    """``{(lat, lon): pack}``: the squares X-Plane takes from the user's own scenery (a tile
    OrthoStudio XP built, one imported from Ortho4XP, another ortho), Live stopped or not
    (:func:`orthostudio.live.covered.own_squares`). Looked for again when X-Plane's list of packs
    changes, else every OWN_S: about 0.1 s each time, where the checks are every 3 s."""
    global _own
    from orthostudio.live.covered import own_squares
    from orthostudio.live.scenery import _ini

    try:
        seen = (str(xp), _ini(xp).stat().st_mtime)
    except OSError:
        return {}
    at, was, got = _own
    if was == seen and time.monotonic() - at < OWN_S:
        return got
    try:
        got = own_squares(xp, LINK_NAME)
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
    sources = default_sources(xp)
    out.update(
        xplane=str(xp),
        # the regions installed, by name, or where they would be
        mesh=", ".join(s.name for s in sources) if sources else str(autoortho_scenery(xp)),
        mesh_regions=[s.name.removeprefix("z_ao_") for s in sources],
        mesh_found=bool(sources),
        region_ready=session.region_ready(default_pack_dir(), sources),
        autoortho_mounted=autoortho_mounted(xp),
        installed=is_installed(xp, LINK_NAME),
        # the squares X-Plane takes from the user's own tiles: Live downloads nothing there
        own_tiles=[[lat, lon, pack] for (lat, lon), pack in sorted(own_tiles(xp).items())],
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
        self.log_file = log_file  # where the log is kept as a file too (orthostudio.live.logfile)
        self._lock = threading.Lock()
        self._state = IDLE
        self._error: str | None = None
        self._report: dict | None = None
        self._lines: deque[tuple[int, str]] = deque(maxlen=LOG_LINES)
        self._seq = 0
        self._stop = threading.Event()
        self._leave = threading.Event()
        # the welcome page's answer: "build" closes this window for OrthoStudio XP's
        self.leaving_for: str | None = None
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
            "error": error,
            "stream": report,
            "log_seq": seq,
            "log_file": None if self.log_file is None else str(self.log_file),
            "platform": platform_name(),  # mac, win or lin: what the file manager is called
            "checks": self.checks(),
            "settings": self._settings().model_dump(),
            "regions": self.regions(),
        }

    def log_after(self, after: int) -> dict[str, Any]:
        with self._lock:
            lines = [{"seq": s, "text": t} for s, t in self._lines if s > after]
            return {"lines": lines, "seq": self._seq}

    # --- what the page asks ------------------------------------------------------------------

    def start(self, *, fresh: bool = False) -> bool:
        """Start streaming; False when a stream is already on its way (or on its way out)."""
        with self._lock:
            if self._state != IDLE:
                return False
            self._state, self._error, self._report = STARTING, None, None
            self._stop.clear()
            self._leave.clear()
        options = options_for(self._settings(), fresh=fresh)
        self._thread = threading.Thread(
            target=self._run, args=(options,), name="live-stream", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop streaming. The pack comes out of X-Plane once X-Plane has quit."""
        with self._lock:
            if self._state in (STARTING, STREAMING):
                self._state = STOPPING
        self._stop.set()

    def leave(self) -> None:
        """Stop waiting for X-Plane to quit: the pack and the plugin stay installed."""
        self._leave.set()

    def uninstall(self) -> bool:
        """Take out a pack left installed; False while a stream runs, or while X-Plane does."""
        checks = self.checks(fresh=True)
        if self._state != IDLE or checks["xplane_running"] or not checks["xplane"]:
            return False
        self._uninstall(session.xplane_dir(None), self.log)
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
            self._map_rows = (version, rows_of(textures))
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
            for (lat, lon), pack in sorted(own_tiles(Path(xp)).items())
        ]

    def textures_at(self, lat: float, lon: float) -> list[dict]:
        """The textures built under a point of the map, with their facts (its tooltip)."""
        from orthostudio.live.mapdata import record, textures_at

        streamer = self.streamer
        if streamer is not None:
            built, patchy, seen = streamer.facts_now()
            top_of = streamer.top_of
        else:
            meta = record(default_pack_dir())
            built, patchy = meta.get("textures", {}), set(meta.get("patchy", []))
            seen = meta.get("seen", {})
            top_of = file_tops(default_pack_dir())
        return textures_at(lat, lon, built, patchy, seen, time.time(), top_of)

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
        if home == "build":
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
        """AutoOrtho's regions as the page lists them: each with its size and whether AutoOrtho or
        Live installed it; the install under way. With ``fetch`` (the page's own request, not its
        status), GitHub's list is read again in the background when it is old."""
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
        return {"list": listed, "error": error if catalog is None else None, "install": progress}

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
                )
        except session.RefusedError as e:
            self._error = str(e)
            self.log(str(e))
        except Exception as e:  # said in the page: the thread has nobody else to tell
            self._error = f"{type(e).__name__}: {e}"
            self.log("the streamer stopped:\n" + "".join(traceback.format_exception(e)))
        finally:
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
