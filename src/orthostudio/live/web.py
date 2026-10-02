# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The Live window: its page and API served on the loopback, shown in a window of its own.

Until its launch, OrthoStudio XP Live has a window of its own rather than a screen in OrthoStudio
XP's, so that nothing of OrthoStudio XP changes: ``python -m orthostudio.live ui`` opens it. It is
built with OrthoStudio XP's own parts, as they are: the window (:func:`orthostudio.window.show`),
the guard of its API (the ``Host`` allow-list, no request from another website, JSON only), and
its page's style sheet, served under ``/static`` as OrthoStudio XP serves it. The page itself,
under ``/live``, is Live's (``orthostudio/live/ui``).

The window goes as OrthoStudio XP's does (the user asked, 2026-09-29): on macOS the close button
and Cmd+W put it away, the app staying in the Dock, a stream going on, and a click on its icon or
Cmd+1 brings it back; Quit (Cmd+Q, the app's menu, the Dock's) ends the app. On Windows and Linux
closing the window is quitting. Quitting while a stream runs asks first, in the window's language,
the window in front, then stops it, and the app stays until X-Plane has quit and the pack is out of
it (:class:`orthostudio.live.control.LiveControl`).
"""

from __future__ import annotations

import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from orthostudio.live.control import IDLE, LiveControl
from orthostudio.live.logfile import LogFile, archive_folder, log_path

UI_DIR = Path(__file__).resolve().parent / "ui"
TITLE = "OrthoStudio XP Live"
SIZE = (1180, 860)
STARTS_WITHIN_S = 10.0
QUIT_WHILE_STREAMING = {
    "en": (
        "OrthoStudio XP Live is streaming. Stop it and quit?\n\n"
        "If X-Plane is still running, Live stays until X-Plane quits: X-Plane reads its "
        "textures from Live's pack, which comes out of X-Plane then."
    ),
    "fr": (
        "OrthoStudio XP Live est en marche. L'arrêter et quitter ?\n\n"
        "Si X-Plane tourne encore, Live reste jusqu'à ce que X-Plane quitte : X-Plane lit ses "
        "textures dans le pack de Live, qui sort de X-Plane à ce moment-là."
    ),
}


def quit_question() -> str:
    """The question asked before quitting while a stream runs, in the window's language."""
    from orthostudio.live.settings import load_settings

    try:
        language = load_settings().language
    except Exception:  # a settings file that cannot be read: the question is asked all the same
        language = "en"
    return QUIT_WHILE_STREAMING.get(language, QUIT_WHILE_STREAMING["en"])


def _map_router(fetch: Any = None) -> Any:
    """OrthoStudio XP's base map, ``GET /api/map/{provider}/{z}/{x}/{y}``, as its own page reads
    it (the same cache), over Live's sources."""
    from orthostudio.api.map_api import map_router
    from orthostudio.live.providers import registry

    return map_router(registry=registry(), fetch=fetch)


def _airports_router() -> Any:
    """``GET /api/airports/in``, as OrthoStudio XP's engine answers it, for the map's airports:
    from the index OrthoStudio XP keeps, only if it is there (building it takes minutes)."""
    from fastapi import APIRouter, HTTPException

    from orthostudio.airports import AirportIndex, default_index_path

    router = APIRouter()
    index: dict[str, Any] = {}

    @router.get("/api/airports/in")
    def airports_in(
        west: float, south: float, east: float, north: float, limit: int = 400
    ) -> list[dict[str, Any]]:
        path = default_index_path()
        if "index" not in index:
            index["index"] = AirportIndex(path) if path.exists() else None
        if index["index"] is None:
            raise HTTPException(404, "no airport index: OrthoStudio XP builds it")
        rows = index["index"].in_bounds(west, south, east, north, limit=max(1, min(2000, limit)))
        return [
            {"icao": a.icao, "name": a.name, "lat": a.lat, "lon": a.lon, "kind": a.kind}
            for a in rows
        ]

    return router


def create_live_app(control: LiveControl, map_fetch: Any = None) -> Any:
    """The page, its API, OrthoStudio XP's base map and airports, and its style sheet under
    ``/static``. ``map_fetch`` replaces the base map's upstream fetch, for the tests."""
    from fastapi import FastAPI
    from fastapi.responses import FileResponse

    from orthostudio.api.app import (
        DEFAULT_ALLOWED_HOSTS,
        PAGE_CACHE_CONTROL,
        _GuardMiddleware,
        _RevalidatedStaticFiles,
    )
    from orthostudio.live.api import live_router
    from orthostudio.ui import ui_dir

    app = FastAPI(title=TITLE, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(_GuardMiddleware, allowed_hosts=DEFAULT_ALLOWED_HOSTS)
    app.include_router(live_router(control))
    app.include_router(_map_router(map_fetch))
    app.include_router(_airports_router())
    app.mount("/static", _RevalidatedStaticFiles(directory=ui_dir()), name="static")
    app.mount("/live", _RevalidatedStaticFiles(directory=UI_DIR), name="live")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html", headers={"Cache-Control": PAGE_CACHE_CONTROL})

    @app.get("/welcome", include_in_schema=False)
    def welcome() -> FileResponse:
        """The app's question at its first start (orthostudio.live.app_start)."""
        return FileResponse(UI_DIR / "welcome.html", headers={"Cache-Control": PAGE_CACHE_CONTROL})

    return app


def _serve(app: Any, port: int) -> tuple[Any, threading.Thread]:
    """Serve ``app`` on the loopback in a thread; raises OSError when the port is taken."""
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, name="live-web", daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTS_WITHIN_S
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise OSError(f"nothing could listen on 127.0.0.1:{port} (a Live window open already?)")
        time.sleep(0.05)
    return server, thread


def open_window(port: int, *, browser: bool = False, start: str | None = None) -> str | None:
    """Serve the page on ``port`` and show it; return once the window is closed and no stream is
    left behind. ``start="welcome"``: the app's question first. Returns ``"build"`` when its
    answer was OrthoStudio XP's window, for the app to open it."""
    from orthostudio import window
    from orthostudio.live import pools

    pools.start_server()  # before the engine's API loads PROJ (orthostudio.live.pools)
    log = LogFile(log_path(), archive=archive_folder())  # LogLive.txt, beside X-Plane's Log.txt
    control = LiveControl(echo=log.echo, log_file=log.path if log.open else None)
    server, thread = _serve(create_live_app(control), port)
    url = f"http://127.0.0.1:{port}/" + ("welcome" if start == "welcome" else "")
    try:
        if browser or not window.possible():
            _in_the_browser(url, control)
        else:
            _in_a_window(url, control)
    finally:
        server.should_exit = True
        thread.join(5)
        log.close()
    return control.leaving_for


def _in_the_browser(url: str, control: LiveControl) -> None:
    """The page in the browser, the terminal holding it: Ctrl-C stops the stream, and waits for
    X-Plane to quit as the command line does (Ctrl-C again leaves the pack installed)."""
    webbrowser.open(url)
    print(f"OrthoStudio XP Live: {url} (Ctrl-C here to close)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        control.stop()
    try:
        control.wait_idle()
    except KeyboardInterrupt:
        control.leave()
        control.wait_idle()


def _in_a_window(url: str, control: LiveControl) -> None:
    from orthostudio import window
    from orthostudio.live.pack import default_live_root

    closing = [False]  # asked, and the answer was yes: the app goes once the stream is out

    def may_quit() -> bool:
        """Quit, wherever it is asked (and the close button where closing is quitting): at once
        when nothing streams; else the question first, the window in front even when it was put
        away, and the app goes once the stream is out (``closes_when``)."""
        if control.state == IDLE or control.leaving_for:
            return True
        if closing[0]:
            return False  # already asked: it goes by itself, once the pack is out
        window.to_the_front()

        def aside() -> None:
            """The box cannot be drawn by the thread waiting for this answer."""
            if window.ask(TITLE, quit_question()):
                closing[0] = True
                control.stop()

        threading.Thread(target=aside, name="live-ask-before-quitting", daemon=True).start()
        return False

    def put_away() -> bool:
        """macOS: the close button and Cmd+W put the window away, as OrthoStudio XP's; the app and
        a stream go on, and the Dock's icon brings the window back."""
        window.away()
        return False

    window.show(
        url,
        title=TITLE,
        on_close=put_away if window.puts_away_on_close() else may_quit,
        may_quit=may_quit,
        closes_when=lambda: (
            (closing[0] and control.state == IDLE) or control.leaving_for == "build"
        ),
        size=SIZE,
        storage=default_live_root() / "window",  # where the page keeps its language
    )
    control.stop()  # the window went some other way: nothing may stream behind it
    control.wait_idle()
