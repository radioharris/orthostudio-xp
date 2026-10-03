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
Cmd+1 brings it back. Quit (its button at the top, Cmd+Q, the app's menu, the Dock's) asks first in
the page, as OrthoStudio XP's own Quit (the user asked, 2026-10-03): the window in front, the same
question with what a stream is worth, Stay or Quit. On Windows and Linux closing the window is
quitting, and asks first, in a box, only while a stream runs, as OrthoStudio XP asks only while a
build runs. Quit stops the stream, and the app stays until X-Plane has quit and the pack is out of
it (:class:`orthostudio.live.control.LiveControl`).
"""

from __future__ import annotations

import contextlib
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orthostudio.live.control import LiveControl

UI_DIR = Path(__file__).resolve().parent / "ui"
IDLE = "idle"  # orthostudio.live.control.IDLE, read here without loading Live before its window
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


def create_live_app(
    control: LiveControl,
    map_fetch: Any = None,
    *,
    front: Callable[[], object] | None = None,
    seen: Callable[[], object] | None = None,
) -> Any:
    """The page, its API, OrthoStudio XP's base map and airports, and its style sheet under
    ``/static``. ``map_fetch`` replaces the base map's upstream fetch, for the tests; ``front``
    and ``seen``: :func:`orthostudio.live.api.live_router`'s."""
    from fastapi import FastAPI
    from fastapi.responses import FileResponse

    from orthostudio.api.guard import (
        DEFAULT_ALLOWED_HOSTS,
        PAGE_CACHE_CONTROL,
        _GuardMiddleware,
        _RevalidatedStaticFiles,
    )
    from orthostudio.live.api import live_router
    from orthostudio.ui import ui_dir

    app = FastAPI(title=TITLE, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(_GuardMiddleware, allowed_hosts=DEFAULT_ALLOWED_HOSTS)
    app.include_router(live_router(control, front=front, seen=seen))
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

    # no colours: uvicorn would ask the console whether it shows them, and the Windows app runs in
    # pythonw, which has no console at all: Live stopped there at the app's start ("Unable to
    # configure formatter 'default'", a cloud PC, 2026-10-03); its button, whose process has an
    # output, opened it
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="off",
            use_colors=False,
        )
    )
    thread = threading.Thread(target=server.run, name="live-web", daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTS_WITHIN_S
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise OSError(f"nothing could listen on 127.0.0.1:{port} (a Live window open already?)")
        time.sleep(0.05)
    return server, thread


class LiveStartError(RuntimeError):
    """Live could not be set up behind its window, which then closed: the app opens OrthoStudio
    XP's window instead, in a process of its own (:func:`orthostudio.live.app_start.app_start`)."""


class _Starting:
    """Live's control while Live is set up behind its window, which shows OrthoStudio XP's opening
    page at once (:func:`open_window`): nothing streams yet and the window may go; every call
    reaches the control once it is there. A failure (``error``) closes the window."""

    def __init__(self) -> None:
        self.control: Any = None
        self.server: Any = None
        self.thread: threading.Thread | None = None
        self.log_file: Any = None
        self.error: BaseException | None = None
        self.cancelled = False  # the window went first: what is set up after is stopped

    @property
    def state(self) -> str:
        return IDLE if self.control is None else self.control.state

    @property
    def closing(self) -> bool:
        return self.control is not None and self.control.closing

    @property
    def leaving_for(self) -> str | None:
        return None if self.control is None else self.control.leaving_for

    def gone(self) -> bool:
        return self.error is not None or (self.control is not None and self.control.gone())

    def close(self) -> None:
        if self.control is not None:
            self.control.close()

    def stop(self) -> None:
        if self.control is not None:
            self.control.stop()

    def wait_idle(self) -> None:
        if self.control is not None:
            self.control.wait_idle()

    def end(self) -> None:
        """The window closed: its page served no more, its log closed."""
        self.cancelled = True
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(5)
        if self.log_file is not None:
            self.log_file.close()


def _set_up(port: int, *, front: Callable[[], object] | None) -> tuple[Any, ...]:
    """Live itself: its encoders' fork server, its log beside X-Plane's, its control, and its page
    and API served on ``port``. Returns them with how long each part took, which the app's log
    says (:func:`open_window`)."""
    began = time.time()
    from orthostudio.live import pools

    pools.start_server()  # before the engine's API loads PROJ (orthostudio.live.pools)
    from orthostudio.api.presence import Presence
    from orthostudio.live.control import LiveControl
    from orthostudio.live.logfile import LogFile, archive_folder, log_path

    loaded = time.time()
    log = LogFile(log_path(), archive=archive_folder())  # LogLive.txt, beside X-Plane's Log.txt
    control = LiveControl(echo=log.echo, log_file=log.path if log.open else None)
    control.keep_region_lists_aside()  # a region not read yet, read before any start asks for it
    seen = Presence()
    made = time.time()
    try:
        server, thread = _serve(create_live_app(control, front=front, seen=seen.seen), port)
    except BaseException:
        log.close()
        raise
    took = (
        ("its code", loaded - began),
        ("setting up", made - loaded),
        ("its page", time.time() - made),
    )
    return control, server, thread, log, seen, took


def _say(log: Path | None, text: str) -> None:
    """A line in the app's own log (``serve.log``), as its window writes them."""
    if log is None:
        return
    from orthostudio.desktop import _note

    with contextlib.suppress(OSError):
        _note(log, text)


def open_window(
    port: int,
    *,
    browser: bool = False,
    start: str | None = None,
    show: bool = True,
    quit_when_closed: bool = False,
    log: Path | None = None,
) -> str | None:
    """Serve the page on ``port`` and show it; return once the window is closed and no stream is
    left behind. ``start="welcome"``: the app's question first. ``show=False``: served only, as in
    the browser but without opening it (the installers' checks, ``tools/package/build.py``).
    ``quit_when_closed``, the app's own start: in the browser, where no window closes, the process
    goes a while after its page last spoke (:func:`_quit_when_closed`). Returns ``"build"`` when
    the welcome's answer was OrthoStudio XP's window, for the app to open it.

    The window opens at once on OrthoStudio XP's opening page, and Live is set up behind it, as
    OrthoStudio XP's window does with its engine: the window came only once Live was ready, after
    seconds of nothing on a cloud PC (a user, 2026-10-03). ``log``, the app's own (``serve.log``):
    where the window says how long its opening and Live's setting up took, as OrthoStudio XP's own
    start says it. Raises :class:`LiveStartError` when Live could not be set up behind it."""
    from orthostudio import window

    home = f"http://127.0.0.1:{port}/"
    path = "welcome" if start == "welcome" else ""
    url = home + path
    in_the_browser = browser or not show or not window.possible()
    if in_the_browser:
        # the page again, where it is: opened in the browser again, which a closed tab left
        # nothing of (the app's icon, OrthoStudio XP's Live button)
        control, server, thread, live_log, seen, _took = _set_up(
            port, front=lambda: webbrowser.open(home)
        )
        ended = threading.Event()
        try:
            if quit_when_closed:
                _quit_when_closed(seen, control, ended)
            _in_the_browser(url, control, show=show)
        finally:
            ended.set()
            server.should_exit = True
            thread.join(5)
            live_log.close()
        return control.leaving_for

    from orthostudio import desktop
    from orthostudio.startclock import parts, process_started_at, span

    shown: list[str] = []
    name = desktop.APP_NAME if path == "welcome" else TITLE
    desktop.open_while_starting(
        port, log=log or desktop.log_path(), browser=shown.append, path=path, name=name
    )
    live = _Starting()

    def set_up() -> None:
        try:
            control, server, thread, live_log, _seen, took = _set_up(port, front=None)
        except BaseException as exc:
            _say(log, f"OrthoStudio XP Live could not start ({type(exc).__name__}: {exc})")
            live.error = exc
            return
        live.server, live.thread, live.log_file = server, thread, live_log
        live.control = control
        if live.cancelled:  # its window went meanwhile
            live.end()
        _say(log, f"Live is ready: {parts(*took)}")

    def opens() -> None:
        from orthostudio import STARTED_AT

        said = parts(
            ("Python", span(process_started_at(), STARTED_AT)),
            ("loading the window", span(STARTED_AT, time.time())),
        )
        _say(log, f"Live's window opens: {said}" if said else "Live's window opens")

    threading.Thread(target=set_up, name="live-set-up", daemon=True).start()
    try:
        _in_a_window(shown[0] if shown else url, live, on_shown=opens)
    finally:
        live.end()
    if live.error is not None:
        raise LiveStartError(str(live.error)) from live.error
    return live.leaving_for


def _quit_when_closed(seen: Any, control: Any, ended: threading.Event) -> None:
    """The app's Live in the browser goes once no page of it has spoken for a while, as
    OrthoStudio XP's engine does (:mod:`orthostudio.api.presence`): closing a tab ends nothing, and
    nothing would show the process then. The page asks for its status every second, its sign of
    life (``seen``); a stream on its way, in or out, or a region's install keeps it
    (``control.busy``)."""
    from orthostudio.api import presence

    def say(_: str) -> None:
        control.log(
            f"no page of Live has been open for {presence.QUIT_AFTER_S / 60:g} min and nothing "
            "streams: Live goes"
        )

    threading.Thread(
        target=presence.quit_when_closed,
        args=(seen, control.busy, control.quit),
        kwargs={
            "wait": ended.wait,
            "after_s": presence.QUIT_AFTER_S,
            "tick_s": presence.TICK_S,
            "say": say,
        },
        name="live-quit-when-closed",
        daemon=True,
    ).start()


def _in_the_browser(url: str, control: LiveControl, *, show: bool = True) -> None:
    """The page in the browser (opened unless not ``show``), the terminal holding it: Ctrl-C
    stops the stream, and waits for X-Plane to quit as the command line does (Ctrl-C again leaves
    the pack installed)."""
    if show:
        webbrowser.open(url)
    print(f"OrthoStudio XP Live: {url} (Ctrl-C here to close)")
    try:
        # until the welcome's answer is OrthoStudio XP's window, or the app goes (control.gone)
        while not control.gone():
            time.sleep(1)
    except KeyboardInterrupt:
        control.stop()
    try:
        control.wait_idle()
    except KeyboardInterrupt:
        control.leave()
        control.wait_idle()


def _in_a_window(url: str, control: Any, *, on_shown: Callable[[], None] | None = None) -> None:
    from orthostudio import window
    from orthostudio.live.pack import default_live_root

    def may_quit() -> bool:
        """Quit (Cmd+Q, the app's menu, the Dock's), as OrthoStudio XP's (``desktop.may_quit``):
        the window in front, put away or not, and its page's Quit pressed: the page asks, in its
        language, and says yes to Live (``POST /api/live/close``). This answers no meanwhile; the
        app goes once the stream is out (``closes_when``)."""
        if control.gone():
            return True
        window.to_the_front()
        threading.Thread(
            target=window.ask_the_page_to_quit, name="live-ask-the-page-to-quit", daemon=True
        ).start()
        return False

    def close_asks() -> bool:
        """Windows and Linux: closing is quitting, as OrthoStudio XP's (``desktop.on_close``): at
        once when nothing streams; else the question first, in a box, the window in front, and the
        app goes once the stream is out (``closes_when``)."""
        if control.state == IDLE or control.gone():
            return True
        if control.closing:
            return False  # already asked: it goes by itself, once the pack is out
        window.to_the_front()

        def aside() -> None:
            """The box cannot be drawn by the thread waiting for this answer."""
            if window.ask(TITLE, quit_question()):
                control.close()

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
        on_shown=on_shown,
        on_close=put_away if window.puts_away_on_close() else close_asks,
        may_quit=may_quit,
        closes_when=control.gone,
        size=SIZE,
        storage=default_live_root() / "window",  # where the page keeps its language and its sizes
        place=TITLE,  # the name macOS keeps its place under: OrthoStudio XP's window has its own
    )
    control.stop()  # the window went some other way: nothing may stream behind it
    control.wait_idle()
