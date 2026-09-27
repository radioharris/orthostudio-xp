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

from orthostudio.live import session, supported
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
from orthostudio.live.scenery import (
    autoortho_mounted,
    autoortho_scenery,
    default_sources,
    is_installed,
    launch_xplane,
    xplane_running,
)
from orthostudio.live.settings import LiveSettings, load_settings

API = 1  # the page's /api/live answers, as the page reads them
CHECKS_S = 3.0  # the checks are looked at again this often: pgrep and disks, not every second
LOG_LINES = 2000
IDLE, STARTING, STREAMING = "idle", "starting", "streaming"
STOPPING, LEAVING, DELETING = "stopping", "leaving", "deleting"
LEAVE_HINT = "Leave it installed, in the window, to stop waiting"
STOP_HINT = "Stop in the window to end"


def options_for(settings: LiveSettings, *, fresh: bool) -> session.Options:
    return session.Options(
        provider=settings.provider,
        disk_budget_gb=settings.disk_budget_gb,
        cache_budget_gb=settings.cache_budget_gb,
        max_zoom=settings.max_zoom,
        max_zoom_airports=settings.max_zoom_airports,
        start_mode=settings.start_mode,
        simbrief_user=settings.simbrief_user,
        fresh=fresh,
    )


def _osxp_simbrief() -> str | None:
    try:
        from orthostudio.config.store import load_settings as osxp_settings

        return (osxp_settings().essential.simbrief_user or "").strip() or None
    except Exception:  # OrthoStudio XP's settings unreadable: nothing to offer
        return None


def gather_checks() -> dict[str, Any]:
    """What streaming needs, as the page lists it."""
    from orthostudio.live import plugin

    out: dict[str, Any] = {
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
        "textures_bytes": usage(load_built(default_pack_dir()).get("textures", {})),
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
        # AutoOrtho's regions, by name, or where they would be
        mesh=", ".join(s.name for s in sources) if sources else str(autoortho_scenery(xp)),
        mesh_found=bool(sources),
        region_ready=session.region_ready(default_pack_dir(), sources),
        autoortho_mounted=autoortho_mounted(xp),
        installed=is_installed(xp, LINK_NAME),
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
        self._lock = threading.Lock()
        self._state = IDLE
        self._error: str | None = None
        self._report: dict | None = None
        self._lines: deque[tuple[int, str]] = deque(maxlen=LOG_LINES)
        self._seq = 0
        self._stop = threading.Event()
        self._leave = threading.Event()
        self._reload = threading.Event()  # the reload offered, asked for from the page
        self._thread: threading.Thread | None = None
        self._checked: tuple[float, dict[str, Any] | None] = (0.0, None)

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
        with self._lock:
            state, error, report, seq = self._state, self._error, self._report, self._seq
        return {
            "api": API,
            "state": state,
            "error": error,
            "stream": report,
            "log_seq": seq,
            "checks": self.checks(),
            "settings": self._settings().model_dump(),
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
            self._reload.clear()
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

    def reload(self) -> None:
        """Reload the scenery in X-Plane, once the 8192 px textures offered after a start are built
        (``start_mode`` "reload"): the streamer asks X-Plane over its UDP."""
        with self._lock:
            offer = ((self._report or {}).get("streamer") or {}).get("offer")
            streaming = self._state == STREAMING
        if not streaming or not offer or offer.get("state") != "ready":
            raise session.RefusedError("no reload is offered now")
        self._reload.set()

    def _reload_asked(self) -> bool:
        """Once per click: what the streamer's loop asks each turn."""
        if not self._reload.is_set():
            return False
        self._reload.clear()
        return True

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
                    reload_asked=self._reload_asked,
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
