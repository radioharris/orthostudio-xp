# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP and OrthoStudio XP Live as one app: which window it opens, and the way from one
to the other.

Once Live is switched on (``enabled`` in ``live.toml``), the app asks at its first start what the
pilot wants to do: fly now, with Live, or build their tiles, with OrthoStudio XP (Live's
``/welcome`` page). The choice is kept (``app_home`` in Live's settings, which change it) and opens
that window at each start after (:func:`app_start`, which OrthoStudio XP's ``desktop.main`` asks
first). Each window has a button to the other: "Build tiles" in Live's, "Live" in OrthoStudio XP's
top bar, which the one line OrthoStudio XP's page gains brings (:func:`osxp_router`: the button,
and what it asks of the engine). The two windows are one program, with one icon in the Dock or the
taskbar: a button brings the other window to the front when it is open, else opens it beside, in
that program (``desktop.open_osxp_here``, ``web.live_window_here``): Live's window through its API,
OrthoStudio XP's through what its process listens for (:func:`osxp_window_listens`). A program
showing no window opens the other in a program of its own.

Where the system gives a window, the two are one window instead, the app's frame (a trial,
:mod:`orthostudio.live.frame`): Build | Live in its top bar shows one or the other.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import threading
import urllib.request
from collections.abc import Callable
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
    """Live's window: brought to the front when it is open (``front``), else opened beside
    OrthoStudio XP's window, in its program (``beside``: one program, one icon), else started in a
    program of its own. Says which."""
    from orthostudio.desktop import ENGINE_PORT

    if live_listening() and _ask_live("front", post=True) is not None:
        return "front"
    if _ask_osxp_window(ENGINE_PORT, b"live"):
        return "beside"
    _other_window("live", ["-m", "orthostudio.live", "ui", "--quit-when-closed"])
    return "started"


def osxp_window_file(port: int) -> Path:
    """Where OrthoStudio XP's open window says which process it is and where it listens: one file
    for each engine's port, so that the preview's Live never brings the usual app's window."""
    from orthostudio.home import osxp_home

    return osxp_home() / "run" / f"osxp-window-{port}.json"


def osxp_window_listens(
    port: int, front: Callable[[], object], *, live: Callable[[], object] | None = None
) -> Callable[[], None]:
    """In OrthoStudio XP's window process: run ``front`` when Live's Build tiles asks for this
    window, and ``live`` when this window's Live button asks for Live's window beside it, in this
    program; and say where to ask (:func:`osxp_window_file`). Returns what stops it.

    Live's window has its API for that; OrthoStudio XP's window has none, its engine being another
    process, so it listens on a port of the system's choosing, on this computer only."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    mine = {"pid": os.getpid(), "port": server.getsockname()[1]}
    path = osxp_window_file(port)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mine), encoding="utf-8")

    def answer() -> None:
        while True:
            try:
                asked, _ = server.accept()
            except OSError:
                return  # stopped
            with asked:
                try:
                    asked.settimeout(2.0)
                    what = asked.recv(16).strip()
                    if what == b"front":
                        front()
                        asked.sendall(b"ok\n")
                    elif what == b"live" and live is not None:
                        live()
                        asked.sendall(b"ok\n")
                except Exception:  # an answer that fails leaves the window as it is
                    continue

    threading.Thread(target=answer, name="osxp-window-front", daemon=True).start()

    def stop() -> None:
        with contextlib.suppress(OSError):
            server.close()
        with contextlib.suppress(OSError, ValueError):
            if json.loads(path.read_text(encoding="utf-8")) == mine:
                path.unlink()

    return stop


def _ask_osxp_window(port: int, what: bytes) -> bool:
    """Whether an open OrthoStudio XP window did ``what`` when asked: ``front`` (came to the
    front), ``live`` (opened Live's window beside it, in its program)."""
    try:
        listening = int(json.loads(osxp_window_file(port).read_text(encoding="utf-8"))["port"])
        with socket.create_connection(("127.0.0.1", listening), timeout=1.0) as window:
            window.settimeout(10.0)  # opening a window takes longer than bringing one
            window.sendall(what + b"\n")
            return window.recv(8).strip() == b"ok"
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _osxp_to_front(port: int) -> bool:
    """Whether an open OrthoStudio XP window came to the front when asked."""
    return _ask_osxp_window(port, b"front")


_opened_here = threading.Event()  # OrthoStudio XP's window opened beside Live's, in this program


def open_osxp() -> str:
    """OrthoStudio XP's window: in this program, beside Live's, brought to the front or opened
    (``here``: one program, one icon), else an open one of another program brought to the front
    (``front``), else started, and says which. Started, it is the app's own entry, which shows the
    running engine's page, or starts one (on this app's port, ``OSXP_PORT``). Each click on Build
    tiles used to start another, one more app in the Dock each time (a user saw it, 2026-10-02)."""
    from orthostudio.desktop import ENGINE_PORT, open_osxp_here

    if open_osxp_here():
        _opened_here.set()
        return "here"
    if _osxp_to_front(ENGINE_PORT):
        return "front"
    _other_window("build", ["-m", "orthostudio.desktop"])
    return "started"


def _in_the_frame(log: Path, wanted: str | None, home: str) -> bool:
    """Both parts in one window, the app's frame (:mod:`orthostudio.live.frame`), where the system
    gives a window and the frame is not turned off: shown first, the part asked for (``wanted``),
    else the one chosen before (``home``), else Live's with the app's question. ``True`` once it is
    closed, or when one open in another program came to the front; ``False`` when it cannot be
    shown here, nothing started then."""
    from orthostudio import window
    from orthostudio.live import frame

    if not frame.wanted() or not window.possible():
        return False
    from orthostudio.desktop import ENGINE_PORT

    if _osxp_to_front(ENGINE_PORT):
        return True  # up already, in a program of its own: in front
    first = wanted if wanted in ("build", "live") else ("build" if home == "build" else "live")
    return frame.show_frame(log, first=first, welcome=home == "ask" and wanted is None)


def app_start(log: Path) -> bool:
    """At the app's start, before OrthoStudio XP's window: ``True`` when the app's frame was shown
    instead (:func:`_in_the_frame`), or Live's window (chosen, or the welcome page the first time:
    its answer "build" opens OrthoStudio XP's window then, apart), ``False`` for OrthoStudio XP's in
    this process: Live switched off, building chosen before, or asked for by Live's own button."""
    from orthostudio.live.settings import load_settings

    wanted = os.environ.get(START_ENV)  # a window's button, through macOS (_other_window)
    if not enabled():
        return False
    home = load_settings().app_home
    if _in_the_frame(log, wanted, home):
        return True
    if wanted == "build" or (home == "build" and wanted != "live"):
        return False
    if live_listening():  # its window is open already, in a process of its own
        _ask_live("front", post=True)
        return True
    from orthostudio.live.web import LiveStartError, open_window

    start = "welcome" if home == "ask" and wanted != "live" else None
    try:
        answer = open_window(LIVE_PORT, start=start, quit_when_closed=True, log=log)
    except LiveStartError:
        answer = "build"  # Live could not be set up behind its window: OrthoStudio XP's opens
    if answer == "build" and not _opened_here.is_set():
        # OrthoStudio XP's window in a process of its own, as Live's button opens it, where it did
        # not open beside Live's in this program (the welcome's answer opens it there at once)
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
