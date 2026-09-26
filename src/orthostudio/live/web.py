# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The Live window: its page and API served on the loopback, shown in a window of its own.

Until its launch, OrthoStudio XP Live has a window of its own rather than a screen in OrthoStudio
XP's, so that nothing of OrthoStudio XP changes: ``python -m orthostudio.live ui`` opens it. It is
built with OrthoStudio XP's own parts, as they are: the window (:func:`orthostudio.window.show`),
the guard of its API (the ``Host`` allow-list, no request from another website, JSON only), and
its page's style sheet, served under ``/static`` as OrthoStudio XP serves it. The page itself,
under ``/live``, is Live's (``orthostudio/live/ui``).

Closing the window while a stream runs asks first, then stops it, and the window stays until X-Plane
has quit and the pack is out of it (:class:`orthostudio.live.control.LiveControl`).
"""

from __future__ import annotations

import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from orthostudio.live.control import IDLE, LiveControl

UI_DIR = Path(__file__).resolve().parent / "ui"
TITLE = "OrthoStudio XP Live"
SIZE = (1180, 860)
STARTS_WITHIN_S = 10.0
CLOSE_WHILE_STREAMING = (
    "OrthoStudio XP Live is streaming. Stop it and close the window?\n\n"
    "If X-Plane is still running, the window stays until X-Plane quits: X-Plane reads its "
    "textures from Live's pack, which comes out of X-Plane then."
)


def create_live_app(control: LiveControl) -> Any:
    """The page, its API, and OrthoStudio XP's style sheet under ``/static``."""
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
    app.mount("/static", _RevalidatedStaticFiles(directory=ui_dir()), name="static")
    app.mount("/live", _RevalidatedStaticFiles(directory=UI_DIR), name="live")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html", headers={"Cache-Control": PAGE_CACHE_CONTROL})

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


def open_window(port: int, *, browser: bool = False) -> None:
    """Serve the page on ``port`` and show it; return once the window is closed and no stream is
    left behind."""
    from orthostudio import window

    control = LiveControl()
    server, thread = _serve(create_live_app(control), port)
    url = f"http://127.0.0.1:{port}/"
    try:
        if browser or not window.possible():
            _in_the_browser(url, control)
        else:
            _in_a_window(url, control)
    finally:
        server.should_exit = True
        thread.join(5)


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

    closing = [False]  # asked, and the answer was yes: the window goes once the stream is out

    def may_close() -> bool:
        if control.state == IDLE:
            return True
        if closing[0]:
            return False  # already asked: it closes by itself, once the pack is out

        def aside() -> None:
            """The box cannot be drawn by the thread waiting for this answer."""
            if window.ask(TITLE, CLOSE_WHILE_STREAMING):
                closing[0] = True
                control.stop()

        threading.Thread(target=aside, name="live-ask-before-closing", daemon=True).start()
        return False

    window.show(
        url,
        title=TITLE,
        on_close=may_close,
        may_quit=may_close,
        closes_when=lambda: closing[0] and control.state == IDLE,
        size=SIZE,
        storage=default_live_root() / "window",  # where the page keeps its language
    )
    control.stop()  # the window went some other way: nothing may stream behind it
    control.wait_idle()
