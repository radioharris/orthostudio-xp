# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP and OrthoStudio XP Live as one app: which window it opens, and the way from one
to the other.

Once Live is switched on (``enabled`` in ``live.toml``), the app asks at its first start what the
pilot wants to do: fly now, with Live, or build their tiles, with OrthoStudio XP (Live's
``/welcome`` page). The choice is kept (``app_home`` in Live's settings, which change it) and opens
that window at each start after (:func:`app_start`, which OrthoStudio XP's ``desktop.main`` asks
first). Each window has a button to the other, each window a process of its own: "Build tiles" in
Live's, "Live" in OrthoStudio XP's top bar, which the one line OrthoStudio XP's page gains brings
(:func:`osxp_router`: the button, and what it asks of the engine).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from orthostudio.live import enabled

LIVE_PORT = 8642  # the Live window's (``python -m orthostudio.live ui``)
# "build": OrthoStudio XP's window, whatever was chosen (Live's button); "live": Live's window,
# whatever was chosen (OrthoStudio XP's button, on a Mac: :func:`_other_window`)
START_ENV = "OSXP_START"
APP_ENV = "OSXP_APP"  # the macOS app a launcher started this from (tools/package)
PASSED_ENV = ("OSXP_HOME",)  # what the other window must share, macOS starting it afresh
UI_DIR = Path(__file__).resolve().parent / "ui"


def live_listening(port: int = LIVE_PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _ask_live(path: str, *, post: bool = False, port: int = LIVE_PORT) -> Any:
    """The Live window's API, from another process of the app; None when it does not answer."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/live/{path}",
        data=b"{}" if post else None,
        headers={"Content-Type": "application/json"} if post else {},
        method="POST" if post else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as answer:
            return json.load(answer)
    except (OSError, ValueError):
        return None


def live_state(port: int = LIVE_PORT) -> str:
    """``off`` without a Live window, else its stream's state (``idle``, ``streaming``...)."""
    if not live_listening(port):
        return "off"
    status = _ask_live("status", port=port)
    return status.get("state", "off") if isinstance(status, dict) else "off"


def _detached(args: list[str], env: dict[str, str] | None = None) -> None:
    """A process of the app's own, apart from this one: the other window."""
    from orthostudio.fsutil import NO_CONSOLE_WINDOW

    subprocess.Popen(
        [sys.executable, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, **(env or {})},
        creationflags=NO_CONSOLE_WINDOW,
    )


def this_app() -> Path | None:
    """The macOS app this process was started from, which its launcher names (:data:`APP_ENV`:
    the installed app's, the checkout's and the preview's, ``tools/package``); None elsewhere, and
    from a terminal."""
    if sys.platform != "darwin":
        return None
    named = os.environ.get(APP_ENV)
    return Path(named) if named and Path(named).is_dir() else None


def _other_window(start: str, aside: list[str]) -> None:
    """The other window, ``start`` ("live" or "build"), in a process of its own. On a Mac, macOS
    opens it from this app itself, a new instance (``open -n``), so that the Dock shows it as this
    app, with its name and icon: a window opened by a Python started aside is called Python and
    carries Python's icon, as OrthoStudio XP's own window once did (measured, 2026-09-20).
    Elsewhere, or when that fails, this Python started aside with ``aside``."""
    import subprocess

    from orthostudio.fsutil import NO_CONSOLE_WINDOW

    app = this_app()
    if app is not None:
        passed = [f"{k}={os.environ[k]}" for k in PASSED_ENV if os.environ.get(k)]
        options = [x for kv in [f"{START_ENV}={start}", *passed] for x in ("--env", kv)]
        try:
            done = subprocess.run(
                ["open", "-n", "-a", str(app), *options],
                capture_output=True,
                timeout=30,
                creationflags=NO_CONSOLE_WINDOW,
            )
            if done.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
    _detached(aside, {START_ENV: start})


def open_live() -> str:
    """Live's window: brought to the front when it is open, else started. Says which."""
    if live_listening() and _ask_live("front", post=True) is not None:
        return "front"
    _other_window("live", ["-m", "orthostudio.live", "ui", "--quit-when-closed"])
    return "started"


def open_osxp() -> None:
    """OrthoStudio XP's window, whatever the app opens at its start: its own entry, which shows
    the running engine's page, or starts one (on this app's port, ``OSXP_PORT``)."""
    _other_window("build", ["-m", "orthostudio.desktop"])


def app_start(log: Path) -> bool:
    """At the app's start, before OrthoStudio XP's window: ``True`` when Live's window was shown
    instead (chosen, or the welcome page the first time: its answer "build" opens OrthoStudio
    XP's window then, apart), ``False`` for OrthoStudio XP's in this process: Live switched off,
    building chosen before, or asked for by Live's own button."""
    from orthostudio.live.settings import load_settings

    wanted = os.environ.get(START_ENV)  # a window's button, through macOS (_other_window)
    if wanted == "build" or not enabled():
        return False
    home = load_settings().app_home
    if home == "build" and wanted != "live":
        return False
    if live_listening():  # its window is open already, in a process of its own
        _ask_live("front", post=True)
        return True
    from orthostudio.live.web import open_window

    start = "welcome" if home == "ask" and wanted != "live" else None
    if open_window(LIVE_PORT, start=start, quit_when_closed=True) == "build":
        # OrthoStudio XP's window in a process of its own, as Live's button opens it: the window
        # library's loop starts once a process, and Live's has just ended
        open_osxp()
    return True


def osxp_router() -> Any:
    """What OrthoStudio XP's engine serves for Live's button in its top bar: whether Live is
    switched on and streaming, a click on the button, and the button's own script (the one line
    OrthoStudio XP's page gains loads it)."""
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import FileResponse

    router = APIRouter()

    @router.get("/api/live-app")
    def status() -> dict[str, Any]:
        if not enabled():
            return {"enabled": False}
        return {"enabled": True, "state": live_state()}

    @router.post("/api/live-app/open")
    def open_it() -> dict[str, Any]:
        if not enabled():
            raise HTTPException(404, "OrthoStudio XP Live is not switched on")
        return {"opened": open_live()}

    @router.get("/api/live-app/entry.js", include_in_schema=False)
    def entry() -> FileResponse:
        return FileResponse(
            UI_DIR / "osxp-entry.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    return router
