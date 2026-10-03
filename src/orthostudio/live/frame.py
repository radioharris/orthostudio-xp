# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP and OrthoStudio XP Live in one window: the app's frame (the user's choice,
2026-10-04, after a trial: « Bien mieux que deux applications »).

Two windows that do not do the same thing and are one app were confusing, each with its own
language, theme, text size and zoom (the user: « Le fait d'avoir deux fenetres d'applications qui
font pas la même chose, mais tout en étant la même application. C'est super confus »). The frame
is one window with one top bar and one foot for both: the app's name, Build | Live (the part shown,
the other's state in a word), the screens of the part shown as tabs, the language, the theme, Quit;
the text size and the zoom at the foot (``ui/frame``). Each part's page is the very one its own
window shows, in a frame of its own (an iframe): loaded the first time the part is shown,
OrthoStudio XP's engine started then, Live set up in this program then; kept alive after, a build
or a stream going on while the other part is shown. The page leaves the frame its own bars
(``ui/embed.js`` on its side).

What the pilot chooses in the frame is the same for both parts and kept here (:data:`PREFS_FILE`),
given to the frame's page as it is served (:func:`frame_page`): kept by the page's own storage
alone, it would last only while the frame is served from the same address. The first start of the
frame takes what the pages had kept of their own, once.

The frame's page is served on the loopback by this program (:func:`serve`), on a port of its own
(:func:`frame_port`), with OrthoStudio XP's style sheet and scripts under ``/static``. Closing the
window goes as OrthoStudio XP's: put away on macOS, the app staying in the Dock; quitting, Cmd+Q
or the frame's Quit, asks first while a build or a stream runs, once, in a box
(:meth:`Frame.may_quit`).
"""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from orthostudio import window

FRAME_DIR = Path(__file__).resolve().parent / "ui" / "frame"
PARTS = ("build", "live")
OFF_ENV = "OSXP_FRAME"
"""``OSXP_FRAME=0``: the two windows of before rather than the frame, to compare them."""

PREFS_FILE = "frame.json"
LANGUAGES = ("en", "fr")
THEMES = ("", "dark", "light")  # "" is the system's
TEXT_SIZES = (100, 115, 130)  # ui/textsize.js TEXT_SIZES, which a test keeps equal
GIVEN = '<script type="application/json" id="frame-given">{}</script>'
"""Where :func:`frame_page` puts what the frame starts from, in ``frame.html``."""
OSXP_MARK = re.compile(r"<!-- osxp: ([.#][\w-]+) -->")
"""Where :func:`frame_page` puts one of OrthoStudio XP's own elements (:func:`osxp_element`), in
``frame.html``: ``.class`` or ``#id``, as that page names it."""
VOID = frozenset(
    ("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "wbr")
)

TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
}
"""What the frame's page may load from this program: its scripts, its styles and their images."""

QUIT_AFTER_THE_ANSWER_S = 0.3
"""How long the frame's Quit waits for its call's answer to reach the page (:meth:`Frame.quit`)."""

QUIT_WORDS = {
    "en": {
        "build": "A build is running",
        "flying": "Live is flying with X-Plane",
        "live": "Live is at work",
        "and": ", and ",
        "stops_it": "Quitting stops it.",
        "stops_both": "Quitting stops both.",
        "kept": "What is already built is kept: starting the build again carries on from there.",
        "flies_on": "X-Plane flies on with the photos already made, without new ones.",
        "anyway": "Quit anyway?",
    },
    "fr": {
        "build": "Un build est en cours",
        "flying": "Live est en vol avec X-Plane",
        "live": "Live est en marche",
        "and": ", et ",
        "stops_it": "Quitter l'arrête.",
        "stops_both": "Quitter arrête les deux.",
        "kept": "Ce qui est déjà construit est gardé : relancer le build reprend à partir de là.",
        "flies_on": "X-Plane continue de voler avec les photos déjà faites, sans nouvelles.",
        "anyway": "Quitter quand même ?",
    },
}
"""The words of the question before quitting (:func:`quit_question`), in the frame's language."""


def quit_question(language: str, *, build: bool, live: bool, flying: bool) -> str:
    """The one question asked before quitting while something runs: what runs, that quitting stops
    it, what is left after. A build and a stream at once asked their two questions one after the
    other in the box, and the user wanted one, simpler and saying more (2026-10-04)."""
    words = QUIT_WORDS.get(language, QUIT_WORDS["en"])
    running = ([words["build"]] if build else []) + (
        [words["flying" if flying else "live"]] if live else []
    )
    said = words["and"].join(running) + ". " + words["stops_both" if build and live else "stops_it"]
    after = ([words["kept"]] if build else []) + ([words["flies_on"]] if live and flying else [])
    return "\n\n".join([said, *([" ".join(after)] if after else []), words["anyway"]])


def xplane_flies() -> bool:
    """Whether X-Plane runs, which the question says: Live's photos are what it flies over."""
    from orthostudio.live.scenery import xplane_running

    return xplane_running()


def wanted() -> bool:
    """Whether the app opens in the frame rather than in two windows (:data:`OFF_ENV`)."""
    return os.environ.get(OFF_ENV, "").strip() != "0"


def frame_port() -> int:
    """The frame's own port, beside its engine's: 8640 for the app's 8641, 8650 for the preview's
    8651 (``OSXP_PORT``)."""
    from orthostudio.desktop import ENGINE_PORT

    return ENGINE_PORT - 1


# -- what the pilot chose in the frame ------------------------------------------------------------


def prefs_path() -> Path:
    from orthostudio.home import osxp_home

    return osxp_home() / PREFS_FILE


def clean_prefs(given: Any) -> dict[str, Any]:
    """What can be kept of ``given``: the language, the theme, the text size and the zoom, each one
    only when the pages know it."""
    if not isinstance(given, dict):
        return {}
    kept: dict[str, Any] = {}
    if given.get("language") in LANGUAGES:
        kept["language"] = given["language"]
    if given.get("theme") in THEMES:
        kept["theme"] = given["theme"]
    size = given.get("textSize")
    if isinstance(size, int | float) and not isinstance(size, bool) and size in TEXT_SIZES:
        kept["textSize"] = int(size)
    zoom = given.get("zoom")
    if isinstance(zoom, int | float) and not isinstance(zoom, bool):
        low, high = window.ZOOM_LIMITS
        if low <= zoom <= high:
            kept["zoom"] = float(zoom)
    return kept


def load_prefs() -> dict[str, Any] | None:
    """What the pilot chose in the frame, or None when nothing was ever kept (the frame's first
    start, which takes what the pages had of their own)."""
    try:
        return clean_prefs(json.loads(prefs_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def save_prefs(prefs: Any) -> dict[str, Any]:
    """Keep ``prefs`` (what :func:`clean_prefs` keeps of them), and return what was kept."""
    from orthostudio.fsutil import atomic_write_text

    kept = clean_prefs(prefs)
    path = prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(kept, sort_keys=True) + "\n")
    return kept


# -- the frame's page -----------------------------------------------------------------------------


class _Element(HTMLParser):
    """Where the first element ``selector`` names (``.class`` or ``#id``) starts and ends in
    ``page``: ``span``, or None when it is not there."""

    def __init__(self, page: str, selector: str) -> None:
        super().__init__(convert_charrefs=False)
        self.page, self.by_id, self.name = page, selector[0] == "#", selector[1:]
        self.starts = [0]  # where each line of the page starts, for the parser's (line, column)
        for line in page.splitlines(keepends=True):
            self.starts.append(self.starts[-1] + len(line))
        self.tag: str | None = None
        self.depth = 0
        self.start = 0
        self.span: tuple[int, int] | None = None
        self.feed(page)
        self.close()

    def _here(self) -> int:
        line, column = self.getpos()
        return self.starts[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.span is not None:
            return
        if self.tag is None:
            named = dict(attrs)
            found = (
                named.get("id") == self.name
                if self.by_id
                else self.name in (named.get("class") or "").split()
            )
            if found:
                self.tag, self.start = tag, self._here()
                if tag in VOID:
                    self.span = (self.start, self.start + len(self.get_starttag_text() or ""))
                else:
                    self.depth = 1
        elif tag == self.tag and tag not in VOID:
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self.tag is None or self.span is not None or tag != self.tag:
            return
        self.depth -= 1
        if self.depth == 0:
            self.span = (self.start, self.page.index(">", self._here()) + 1)


def osxp_element(selector: str, page: str | None = None) -> str:
    """OrthoStudio XP's own element ``selector`` (``.class``, ``#id``: the first so named), as its
    page writes it (``ui/index.html``), or ``page``; "" when it is not there."""
    if page is None:
        from orthostudio.ui import ui_dir

        try:
            page = (ui_dir() / "index.html").read_text(encoding="utf-8")
        except OSError:
            return ""
    found = _Element(page, selector).span
    return page[found[0] : found[1]] if found else ""


def frame_page(given: dict[str, Any]) -> bytes:
    """``frame.html`` with what the frame starts from (``given``: the part shown first, whether the
    welcome is asked, the app's version, what the pilot chose and whether it was ever kept), and
    OrthoStudio XP's own elements where it marks them (:data:`OSXP_MARK`): the name, the language,
    the theme, Quit, the text size and the zoom are written once, in OrthoStudio XP's page. The
    frame had copies of them, and a change to OrthoStudio XP's Quit left its own behind (the user:
    « pourquoi c'est pas le même bouton ??? », 2026-10-04)."""
    html = (FRAME_DIR / "frame.html").read_text(encoding="utf-8")
    literal = json.dumps(given).replace("</", "<\\/").replace("<!--", "<\\!--")
    html = html.replace(GIVEN, GIVEN.replace("{}", literal))
    from orthostudio.ui import ui_dir

    try:
        osxp = (ui_dir() / "index.html").read_text(encoding="utf-8")
    except OSError:
        osxp = ""
    return OSXP_MARK.sub(lambda mark: osxp_element(mark.group(1), osxp), html).encode("utf-8")


def _inside(base: Path, rel: str) -> Path | None:
    """The file ``rel`` names under ``base``, when it is one the frame may load; never outside."""
    try:
        root = base.resolve()
        candidate = (root / urllib.parse.unquote(rel)).resolve()
    except (OSError, ValueError):
        return None
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate if candidate.suffix in TYPES else None


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # Windows lets a second socket take a port another listens on when this is set, which would
    # leave the frame's address to chance: there a taken port is refused, and another is chosen
    allow_reuse_address = sys.platform != "win32"


def serve(page: Callable[[], bytes], port: int) -> tuple[http.server.ThreadingHTTPServer, int]:
    """Serve the frame's page (``page()``, made at each load) and what it loads, on ``port`` of the
    loopback, else on a free one; returns the server, serving in a thread, and its port."""
    from orthostudio.ui import ui_dir

    static = ui_dir()

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            here = self.server.server_address[1]
            if self.headers.get("Host", "") not in (f"127.0.0.1:{here}", f"localhost:{here}"):
                self.send_error(403)  # another website's name for this computer: nothing for it
                return
            path = urllib.parse.urlsplit(self.path).path
            if path == "/":
                body, kind = page(), "text/html; charset=utf-8"
            else:
                file = None
                if path.startswith("/frame/"):
                    file = _inside(FRAME_DIR, path[len("/frame/") :])
                elif path.startswith("/static/"):
                    file = _inside(static, path[len("/static/") :])
                if file is None:
                    self.send_error(404)
                    return
                body, kind = file.read_bytes(), TYPES[file.suffix]
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return  # the app's log is for the app

    server: _Server | None = None
    for asked in (port, 0):
        try:
            server = _Server(("127.0.0.1", asked), Page)
            break
        except OSError:
            continue
    if server is None:
        raise OSError("no port of the loopback could serve the app's frame")
    threading.Thread(target=server.serve_forever, name="app-frame", daemon=True).start()
    return server, server.server_address[1]


# -- the frame ------------------------------------------------------------------------------------


class Frame:
    """The app's one window and what is behind it: the parts' pages, what they take, its Quit."""

    def __init__(self, log: Path, *, first: str, welcome: bool = False) -> None:
        self.log = log
        self.first = first if first in PARTS else "build"
        self.welcome = welcome  # the app's question, asked once in Live's part (its /welcome)
        self.said_yes = False  # asked before quitting and answered: closing goes through at once

    def given(self) -> dict[str, Any]:
        """What the frame's page starts from (:func:`frame_page`)."""
        from orthostudio import __version__

        prefs = load_prefs()
        return {
            "first": self.first,
            "welcome": self.welcome,
            "version": __version__,
            "prefs": prefs or {},
            "kept": prefs is not None,
        }

    # -- the parts --------------------------------------------------------------------------------

    def ensure(self, part: str, *, welcome: bool = False) -> str:
        """The address of ``part``'s page, the first time it is shown, and what it takes started:
        OrthoStudio XP's engine behind its opening page, Live set up in this program behind its
        own. ``welcome``: the app's question first, in Live's part, once. "" for no such part."""
        if part == "build":
            return self._build()
        if part == "live":
            return self._live(welcome=welcome and self.welcome)
        return ""

    def _build(self) -> str:
        from orthostudio import desktop

        url = f"http://127.0.0.1:{desktop.ENGINE_PORT}/"
        if desktop.engine_here(desktop.ENGINE_PORT):
            return url
        shown: list[str] = []
        if desktop.open_while_starting(desktop.ENGINE_PORT, log=self.log, browser=shown.append):
            url = shown[0] if shown else url  # the opening page, which goes to the engine's
        with contextlib.suppress(Exception):
            desktop._note(self.log, "its engine starts, for the app's frame")
        desktop.start_engine(self.log)
        return url

    def _live(self, *, welcome: bool) -> str:
        from orthostudio import desktop
        from orthostudio.live import web
        from orthostudio.live.app_start import LIVE_PORT

        path = "welcome" if welcome else ""
        self.welcome = self.welcome and not welcome
        url = f"http://127.0.0.1:{LIVE_PORT}/{path}"
        with web._here_lock:
            live = web._here
        if live is not None and live.error is None:
            return url  # set up already: its page again, after the welcome's answer
        shown: list[str] = []
        desktop.open_while_starting(
            LIVE_PORT, log=self.log, browser=shown.append, path=path, name=web.TITLE
        )
        url = shown[0] if shown else url
        live = web._Starting()
        web._be_here(live)
        threading.Thread(
            target=self._set_up_live, args=(live,), name="live-set-up", daemon=True
        ).start()
        return url

    def _set_up_live(self, live: Any) -> None:
        """Live itself, behind its part's opening page, as its own window sets it up."""
        from orthostudio.live import web
        from orthostudio.live.app_start import LIVE_PORT
        from orthostudio.startclock import parts

        try:
            control, server, thread, live_log, _seen, took = web._set_up(
                LIVE_PORT, front=lambda: self.front("live")
            )
        except BaseException as exc:
            web._say(self.log, f"OrthoStudio XP Live could not start ({type(exc).__name__}: {exc})")
            live.error = exc
            return
        control.in_frame = True  # its welcome and its Build tiles show the frame's other part
        live.server, live.thread, live.log_file = server, thread, live_log
        live.control = control
        web._say(self.log, f"Live is ready, in the app's frame: {parts(*took)}")

    def front(self, part: str | None = None) -> None:
        """The window in front, put away or not, ``part`` shown: the app started again, Live's way
        back to its page (its API's ``front``)."""
        window.to_the_front(window.PLACE_NAME)
        if part is not None:
            self.show_part(part)

    def show_part(self, part: str) -> None:
        """Show ``part`` in the frame, as its button does. Never from the main thread: the page's
        answer is waited for there."""
        shown = window._named(window.PLACE_NAME)
        if shown is None or part not in PARTS:
            return
        with contextlib.suppress(Exception):
            shown.evaluate_js(f"window.showPart && window.showPart({json.dumps(part)})")

    # -- what the pilot chose ---------------------------------------------------------------------

    def keep(self, prefs: Any) -> dict[str, Any]:
        """Keep what the pilot chose in the frame; Live's plugin speaks its language in X-Plane
        (Live's page says it too, once it is told)."""
        try:
            kept = save_prefs(prefs)
        except OSError:
            return {}
        if "language" in kept:
            with contextlib.suppress(Exception):
                self._live_language(kept["language"])
        return kept

    def _live_language(self, language: str) -> None:
        from orthostudio.live import web

        with web._here_lock:
            live = web._here
        if live is not None and live.control is not None:
            live.control.set_language(language)
            return
        # not set up in this program: its settings, which its plugin reads at its next start
        from orthostudio.live.settings import load_settings, save_settings

        settings = load_settings()
        if settings.language != language:
            save_settings(settings.model_copy(update={"language": language}))

    def language(self) -> str:
        kept = load_prefs() or {}
        return kept.get("language", "en")

    # -- closing ----------------------------------------------------------------------------------

    def _running(self) -> tuple[bool, Any]:
        """Whether a build runs or waits, and Live's control when it streams (else None)."""
        from orthostudio import desktop
        from orthostudio.live import web

        port = desktop.ENGINE_PORT
        build = desktop._listening(port) and desktop.a_build_runs(port)
        with web._here_lock:
            live = web._here
        control = None if live is None else live.control
        streams = control is not None and not live.gone() and control.busy()
        return bool(build), control if streams else None

    def question(self, build: bool, live: bool) -> str:
        """What is asked before quitting while a build or Live runs, in the frame's language."""
        flying = live and xplane_flies()
        return quit_question(self.language(), build=build, live=live, flying=flying)

    def may_quit(self) -> bool:
        """The app's Quit (Cmd+Q, its menu's, the Dock's, the frame's own), and the close button
        where closing quits (Windows, Linux): at once when nothing runs, OrthoStudio XP's engine
        asked to stop; else one question first, in a box, the window in front, and on yes the
        build or the stream stops and the window closes. The box cannot be drawn by the thread
        waiting for this answer: it is asked aside, and this answers no meanwhile."""
        if self.said_yes:
            return True
        build, control = self._running()
        if not build and control is None:
            self._stop(build=False, control=None)
            return True
        window.to_the_front(window.PLACE_NAME)

        def aside() -> None:
            from orthostudio import desktop

            asked = self.question(build, control is not None)
            if not window.ask(desktop.APP_NAME, asked, name=window.PLACE_NAME):
                return
            self.said_yes = True  # closing comes back through here, and must go through
            self._stop(build=build, control=control)
            window.close_now(window.PLACE_NAME)

        threading.Thread(target=aside, name="frame-ask-before-quitting", daemon=True).start()
        return False

    def quit(self) -> bool:
        """The frame's own Quit: as the app's (:meth:`may_quit`), the window closing on yes.

        Asked by the page, whose call must have its answer before the window goes: closed under
        its own call, the call's thread waited for ever to answer a page that was gone, and the
        app could not end (2026-10-04). The answer goes at once, the rest a moment later, aside."""

        def aside() -> None:
            time.sleep(QUIT_AFTER_THE_ANSWER_S)
            if not self.may_quit():
                return  # asked in a box, which closes the window itself on yes
            self.said_yes = True  # nothing runs, and the engine was asked to stop: no second asking
            window.close_now(window.PLACE_NAME)

        threading.Thread(target=aside, name="frame-quit", daemon=True).start()
        return True

    def put_away(self) -> bool:
        """macOS: the close button and Cmd+W put the window away, the app and what runs going on;
        the Dock's icon brings it back."""
        window.away(window.PLACE_NAME)
        return False

    def gone(self) -> bool:
        """Whether the app is asked to go from elsewhere: Live asked to quit by the installer of
        an update (its API's ``quit``), OrthoStudio XP's engine with it."""
        from orthostudio.live import web

        with web._here_lock:
            live = web._here
        return live is not None and live.leaving_for == "quit"

    def _stop(self, *, build: bool, control: Any) -> None:
        from orthostudio import desktop

        if desktop._listening(desktop.ENGINE_PORT):
            desktop.stop_the_engine(desktop.ENGINE_PORT, force=build)
        if control is not None:
            # X-Plane flies on over what is built; its plugin takes Live's links out next start
            control.quit(force=True)


_shown: Frame | None = None
"""The frame this program shows, while it shows one."""


def current() -> Frame | None:
    return _shown


def show_frame(log: Path, *, first: str, welcome: bool = False) -> bool:
    """The app in its frame, ``first`` shown (``welcome``: the app's question first, in Live's
    part); returns True once the window is closed, False when it could not be shown, nothing
    started then: the app opens its windows as before."""
    from orthostudio import desktop
    from orthostudio.live import app_start, web

    global _shown
    frame = Frame(log, first=first, welcome=welcome)
    try:
        server, port = serve(lambda: frame_page(frame.given()), frame_port())
    except OSError as exc:
        desktop._note(log, f"the app's frame could not be served ({exc}): its windows open instead")
        return False
    stop_listening: Callable[[], None] | None = None
    with contextlib.suppress(Exception):
        # the app started again, from its icon or its menu, brings this window to the front
        stop_listening = app_start.osxp_window_listens(
            desktop.ENGINE_PORT, frame.front, live=lambda: frame.front("live")
        )
    _shown = frame

    def opens() -> None:
        with contextlib.suppress(Exception):
            desktop._note(log, desktop.window_opens().replace("its window", "its frame", 1))

    try:
        window.show(
            f"http://127.0.0.1:{port}/",
            title=desktop.APP_NAME,
            tools=FrameTools(frame),
            on_shown=opens,
            closes_when=frame.gone,
            on_close=frame.put_away if window.puts_away_on_close() else frame.may_quit,
            may_quit=frame.may_quit,
            note=lambda text: desktop._note(log, text),
            place=window.PLACE_NAME,
        )
    except Exception as exc:
        desktop._note(log, f"the app's frame could not be shown ({type(exc).__name__}: {exc})")
        return False
    finally:
        _shown = None
        if stop_listening is not None:
            stop_listening()
        server.shutdown()
        web.end_here()
    desktop._note(log, "its window closed")
    return True


class FrameTools(window.PageTools):
    """What the frame's page may ask of the app (pywebview's ``js_api``, which the frame's page
    alone has, not the parts' pages in it): the zoom, the whole window's
    (:class:`orthostudio.window.PageTools`); a part's page and what it takes; Quit; and what the
    pilot chose, to keep. What each part is doing its own page says (``ui/embed.js``): asking the
    engine every 2 s wrote a line in ``serve.log`` each time."""

    def __init__(self, frame: Frame) -> None:
        super().__init__(window.PLACE_NAME)
        self._frame = frame  # not given to the page (pywebview skips names beginning with _)

    def ensure(self, part: str, welcome: bool = False) -> str:
        return self._frame.ensure(str(part), welcome=bool(welcome))

    def quit(self) -> bool:
        return self._frame.quit()

    def keep(self, prefs: Any) -> dict[str, Any]:
        return self._frame.keep(prefs)
