# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The app in a window of its own, when the system has one to give.

The interface is a page, and a page in a browser is not a window. Its icon leaves the Dock while
the app runs, a click on the icon has nothing to bring in front, every click opens one more tab,
and two tabs of the same app do not follow each other (a user, 2026-09-20). Shown through the web
view the system already carries (WKWebView on macOS, WebView2 on Windows, WebKitGTK on Linux), the
app is an app: one window, an icon that stays, a click that brings it back. The page inside is the
very one the browser shows, served by the same engine on the same port: nothing of the interface
is written twice.

Nothing here is required. :func:`show` raises when the system has no web view to give, and the
caller opens the browser instead, which is what every version before 0.1.8 did. A system that could
have a window and lacks a library is told which one, with a link (:func:`hint`); OrthoStudio XP
installs nothing of its own on a system it does not own.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = [
    "LINUX_HELP",
    "LINUX_PACKAGES",
    "MIN_SIZE",
    "POLL_S",
    "ROOM_FOR_THE_SYSTEM",
    "SIZE",
    "WEBVIEW2_HELP",
    "WEBVIEW2_MINIMUM",
    "ask",
    "ask_the_page_to_quit",
    "away",
    "close_now",
    "fits_the_screen",
    "hint",
    "install",
    "on_quit",
    "possible",
    "puts_away_on_close",
    "show",
    "storage_dir",
    "the_distributions_python",
    "to_the_front",
    "webview2_too_old",
]

POLL_S = 2.0
"""How often ``closes_when`` is asked, while the window is open."""

SIZE = (1440, 920)
"""The window a first run opens, when the screen has room for it. Narrower than 1280, the Plan's
two columns crowd each other."""

ROOM_FOR_THE_SYSTEM = (60, 140)
"""What a screen keeps for itself, which its reported size does not take out: a menu bar and a
Dock, a taskbar. Asked for the whole height, the window went under them and the map's legend was
cut off on a 14-inch laptop (a user, 2026-09-20)."""

MIN_SIZE = (1024, 700)

WINDOW_MENU = "Window"
"""The menu that brings the window back once it has been put away. macOS builds one of its own for
apps that list their windows in it; pywebview builds none, and without it the Dock's icon was the
only way back (a user asked, 2026-09-20)."""

WINDOW_MENU_KEY = "1"
"""Cmd+1 for that entry, which a pywebview menu cannot carry and :func:`_window_menu_shortcut`
puts there by hand.

It was Cmd+0 until 0.1.9. A menu's key is taken by AppKit before the page ever sees it, and
Cmd+0 is what every browser uses to put the text back to its own size, which the page now needs
(``ui/zoom.js``). Cmd+1 is the macOS way of asking for the first window."""

ICON_NAMES = {"win32": "orthostudio.ico", "linux": "orthostudio.png"}
"""What the window's icon is called where the system takes one from a file. macOS is not here:
a ``.app`` carries its icon and the window takes it."""


def icon_file() -> str | None:
    """The app's own icon for the window, or None where there is none to give.

    pywebview falls back to the icon of ``sys.executable`` (``platforms/winforms.py``), and on
    Windows this app is started by ``pythonw.exe``: the window and its taskbar button carried
    Python's icon (a user, 2026-09-20). The build writes the icon beside the installed tree
    (``tools/package/build.py``), two folders up from ``python\\pythonw.exe``.
    """
    name = ICON_NAMES.get(sys.platform)
    if name is None:
        return None
    here = Path(sys.executable).resolve()
    for folder in (here.parent, *here.parents[1:3]):
        candidate = folder / name
        if candidate.is_file():
            return str(candidate)
    return None


ZOOM_LIMITS = (0.5, 3.0)
"""What the page may ask for. A browser stops around there too, and a window scaled past it has
no room left for the map."""


def on_its_own_thread(control: Any, do: Callable[[], object]) -> None:
    """Run ``do`` on the thread that owns the WinForms ``control``, and wait for it.

    WebView2 is touched from that thread alone (``CoreWebView2 can only be accessed from the UI
    thread``), and the page's calls come in on threads of their own: pywebview runs each one apart
    so as not to hold the window, and hands its own work to that thread the same way
    (``platforms/edgechromium.py``, ``evaluate_js``). Set from there, the zoom raised, the page
    heard no, and Ctrl+plus never did anything on Windows (a user, 2026-10-03).

    Reading it from there is no better: the read waits for the window's thread while it holds
    Python's lock, and that thread runs Python for each of the page's requests (pywebview's
    ``on_web_resource_request``). When one came at that moment, each waited for the other: the
    window froze at most starts, the page asking for the zoom as it opened (0.1.22rc3 on a
    Shadow, 2026-10-03). ``Invoke`` lets the lock go while it waits.
    """
    if not getattr(control, "InvokeRequired", False):
        do()
        return
    from System import Func, Object  # pythonnet, which the window runs on there

    # the delegate pywebview hands its own scripts over with (``evaluate_js``)
    control.Invoke(Func[Object](lambda: do()))


class PageTools:
    """What the page may ask of the window it runs in (pywebview's ``js_api``).

    Only the zoom, for now. The window has none of its own: a WKWebView is asked through
    ``pageZoom``, a WebView2 through ``ZoomFactor``, and pywebview turns the browser's own
    shortcuts off in WebView2 (``platforms/edgechromium.py``: ``AreBrowserAcceleratorKeysEnabled``
    follows ``debug``), so Ctrl+plus did nothing on Windows either. A user who found the text
    bigger in the window than in his browser had no way to make it smaller (2026-09-20).
    """

    def set_zoom(self, factor: float) -> bool:
        """Draw the page ``factor`` times its size; whether it could be done.

        False when this system's view will not say: the page then leaves the zoom where it is
        rather than scaling itself with CSS, which would take `100vh` with it and cut the map.
        """
        try:
            low, high = ZOOM_LIMITS
            wanted = max(low, min(high, float(factor)))
        except (TypeError, ValueError):
            return False
        view = _web_view()
        if view is None:
            return False
        try:
            if hasattr(view, "setPageZoom_"):  # WKWebView, macOS 11 and later
                view.setPageZoom_(wanted)
                return True
            # WebView2, through its WinForms control: its zoom is not even read from here, only
            # on the window's own thread (on_its_own_thread)
            if hasattr(view, "InvokeRequired"):
                on_its_own_thread(view, lambda: setattr(view, "ZoomFactor", wanted))
                return True
        except Exception:
            return False
        return False


def _web_view() -> Any:
    """The platform's own view inside the window, or None where it cannot be reached.

    pywebview does not offer it, so this reaches for the one it keeps. Guarded: a version that
    keeps it elsewhere costs the zoom, not the window.
    """
    if _window is None:
        return None
    uid = getattr(_window, "uid", None)
    for module in ("webview.platforms.cocoa", "webview.platforms.winforms"):
        try:
            platform = __import__(module, fromlist=["BrowserView"])
            instance = platform.BrowserView.instances.get(uid)
        except Exception:
            continue
        if instance is None:
            continue
        view = getattr(instance, "webview", None)  # macOS keeps the WKWebView here
        if view is not None:
            return view
        browser = getattr(instance, "browser", None)  # Windows keeps an EdgeChrome
        if browser is not None:
            return getattr(browser, "webview", None)
    return None


LINUX_PACKAGES = "python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1"
"""What Debian and Ubuntu call the GTK web view. Other distributions name them otherwise, which is
why the message carries the link rather than a command for a system we did not recognise."""

_window: Any = None
"""The window that is up, for what has to speak to its page from elsewhere."""

LINUX_HELP = "https://pywebview.flowrl.com/guide/installation.html"
WEBVIEW2_HELP = "https://developer.microsoft.com/microsoft-edge/webview2/"

WEBVIEW2_MINIMUM = "101.0.1210.39"
"""The oldest WebView2 Runtime the window starts on. pywebview's WebView2 control asks the runtime
for ``ICoreWebView2Environment10`` as it starts, which came with 101.0.1210.39 (Microsoft's
reference): on an older one the window opened empty, in its background colour, and said why
nowhere (a Shadow PC that carried 100.0.1185.36 of 2022, 2026-10-01). On an older one the app opens
the browser instead, and the installer offers the update (``tools/package/webview2.pas``, which is
given this same number by ``tools/package/build.py``)."""


def storage_dir() -> Path:
    """Where the web view keeps what the page stores (the theme, the language, the Experts panel).

    pywebview starts in a private mode that forgets all of it when the window closes; the page
    would lose its theme and its language at every launch. The folder is OrthoStudio XP's own, so
    that removing the app takes it away with the rest.
    """
    from orthostudio.home import osxp_home

    return osxp_home() / "window"


def the_distributions_python() -> bool:
    """Whether this Python is the one the distribution ships, which is the only one its own
    packages can be imported into.

    A distribution builds its web view bindings for its own Python and no other: installed beside
    the app they are there, and invisible to it, because the app carries a Python of its own.
    Measured in a container: ``apt install python3-gi gir1.2-webkit2-4.1`` put ``gi`` under
    ``/usr/lib/python3/dist-packages`` for Python 3.10, and the app's 3.14 never saw it
    (2026-09-20). Run from a checkout made with the distribution's Python, they do meet.
    """
    base = Path(sys.base_prefix)
    return base == Path("/usr") or str(base).startswith("/usr/")


def install() -> str | None:
    """What to do about it, in one line: the command on Linux, Microsoft's page on Windows.

    Kept apart from :func:`hint`, whose sentence is English, so that the page can put its own
    words around this and leave the command itself where it is written once."""
    if sys.platform.startswith("linux"):
        # naming them to a Python that could never import them is advice that leads nowhere
        return f"sudo apt install {LINUX_PACKAGES}" if the_distributions_python() else None
    if sys.platform == "win32":
        return WEBVIEW2_HELP
    return None


def hint() -> str | None:
    """What to install for a window on this system, once :func:`show` has refused; ``None`` when
    the system is one we cannot advise (macOS carries WKWebView, and has nothing to install)."""
    if sys.platform.startswith("linux"):
        if not the_distributions_python():
            return (
                "OrthoStudio XP opens in your browser on Linux, and there is nothing to install "
                "for it: the app carries a Python of its own, and a distribution builds its web "
                "view for the Python it ships. Run from a checkout made with that Python, the two "
                f"meet ({LINUX_HELP})."
            )
        return (
            "OrthoStudio XP opened in your browser: this system has no web view of its own to "
            f"show it in a window. On Debian and Ubuntu: sudo apt install {LINUX_PACKAGES}. "
            f"For other systems: {LINUX_HELP}"
        )
    if sys.platform == "win32":
        old = webview2_too_old()
        if old is not None:
            # it is there, so Microsoft's installer answers that it is installed already, unless
            # it is run as administrator, which is how it updated the one of a Shadow PC
            return (
                "OrthoStudio XP opened in your browser: the WebView2 Runtime, which draws its "
                f"window, is too old on this PC (version {old}; {WEBVIEW2_MINIMUM} or later is "
                "needed). Microsoft's Evergreen Bootstrapper updates it when it is run as "
                f"administrator: {WEBVIEW2_HELP}"
            )
        return (
            "OrthoStudio XP opened in your browser: the WebView2 Runtime, which draws its window, "
            f"is not installed. Microsoft gives it here: {WEBVIEW2_HELP}"
        )
    return None


def _here(name: str) -> bool:
    """Whether ``name`` could be imported, without importing it."""
    from importlib.util import find_spec

    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _version(text: str) -> tuple[int, ...] | None:
    """``101.0.1210.39`` as numbers to compare, or ``None`` when it is not a dotted version."""
    parts = text.strip().split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _webview2_version() -> str | None:
    """The version of the WebView2 Runtime Windows carries, read the way Microsoft says to read it:
    the ``pv`` value of the runtime's key, per machine or per user, present and above 0.0.0.0, and
    the newer of the two when both have one. ``None`` when neither has one, and off Windows. The
    installer reads the same two keys (``tools/package/webview2.pas``)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:  # a test that only says it is Windows
        return None

    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    found: list[str] = []
    for root, key in (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{guid}"),
    ):
        try:
            with winreg.OpenKey(root, key) as handle:
                version, _kind = winreg.QueryValueEx(handle, "pv")
        except OSError:
            continue
        if isinstance(version, str) and version not in ("", "0.0.0.0"):
            found.append(version)
    return max(found, key=lambda v: _version(v) or ()) if found else None


def _older_than_the_minimum(version: str) -> bool:
    """Whether ``version`` is older than :data:`WEBVIEW2_MINIMUM`. A version written in a way this
    cannot read is given the benefit of the doubt, as every version before 0.1.20 gave all."""
    have = _version(version)
    return have is not None and have < (_version(WEBVIEW2_MINIMUM) or ())


def webview2_too_old() -> str | None:
    """The version of the WebView2 Runtime this Windows carries when the window cannot start on it
    (:data:`WEBVIEW2_MINIMUM`); ``None`` when it is recent enough, missing, or off Windows."""
    found = _webview2_version()
    return found if found is not None and _older_than_the_minimum(found) else None


def _webview2_runtime() -> bool:
    """Whether Windows carries a WebView2 Runtime the window starts on: one is there
    (:func:`_webview2_version`), and it is not older than :data:`WEBVIEW2_MINIMUM`."""
    found = _webview2_version()
    return found is not None and not _older_than_the_minimum(found)


def possible() -> bool:
    """Whether this system has what a window of its own takes.

    It asks **without importing any of it**: importing the toolkit is what opens a connection to
    the window server, and a process that does so grows an icon in the Dock. The engine asks this
    (the doctor's ``window`` check) and must stay the plain program it is; asking the other way
    put an icon in the Dock for every test worker that asked (2026-09-20).

    It answers for what it can see from here, which is not the whole story on Linux: PyGObject may
    be installed and its WebKit2 typelib missing. :func:`show` then raises, before anything has
    been started, and the browser opens instead.
    """
    if not _here("webview"):
        return False
    if sys.platform == "darwin":
        return _here("AppKit") and _here("WebKit")  # pyobjc, which travels inside the app
    if sys.platform == "win32":
        # pythonnet, and Microsoft's own component, recent enough to start the window on
        return _here("clr") and _webview2_runtime()
    return _here("gi") or _here("PyQt6") or _here("PyQt5") or _here("PySide6")


def puts_away_on_close() -> bool:
    """Whether the close button should put the app away rather than quit it.

    macOS only: there, closing a window does not quit its app, which stays in the Dock for the
    click that brings it back. Windows and Linux have no such place to stay in, and quit."""
    return sys.platform == "darwin"


def away() -> None:
    """Put the window away without closing it. The app stays where it was, active, its name in the
    menu bar, and :func:`on_quit` brings the window back when its icon is clicked.

    Not the whole app: hiding that would hand the menu bar to whichever app comes next, where
    closing a window leaves a Mac app as it was (a user saw it, 2026-09-20)."""
    if _window is not None:
        _window.hide()


def to_the_front() -> None:
    """Bring the window back and the app in front, put away or not: what it is about to ask must
    be seen."""
    if _window is not None:
        _window.show()
    if not puts_away_on_close():
        return
    from AppKit import NSApplication

    app = NSApplication.sharedApplication()
    app.unhide_(None)
    app.activateIgnoringOtherApps_(True)


CLICK_QUIT = """
(() => {
  const b = document.getElementById("quit-btn");
  if (!b || b.hidden) return false;
  b.click();
  return true;
})()
"""
"""The page's own Quit: it asks about a build that runs or waits, in the user's own language, and
stops the engine (``ui/app.js`` ``quitOsxp``). Asking it beats asking again in a second voice."""


def ask_the_page_to_quit() -> None:
    """Press the page's Quit button, and end the app outright when there is no page to ask.

    Never from the main thread: reading a page's answer waits for the main thread, which would be
    waiting for this.
    """
    window = _window
    if window is None:
        return
    try:
        asked = window.evaluate_js(CLICK_QUIT)
    except Exception:
        asked = False
    if not asked:
        window.destroy()  # no page, or a page that cannot stop the engine: the app goes


_quitter: Any = None
"""Kept here because an NSApplication holds its delegate without keeping it alive."""


def on_quit(handler: Callable[[], bool]) -> None:
    """Have ``handler`` decide what the app's Quit does, and bring the window back at a click on
    the app's icon.

    Cmd+Q, the Quit of the app's own menu and the Quit of its Dock menu all end in
    ``applicationShouldTerminate:``, which pywebview answers by asking each window whether it may
    close. With a close button that puts the window away instead of closing it (:func:`away`),
    that answer is always no, and the app could not be quit at all. This takes the decision back:
    ``handler`` answers True to let the app go, False to keep it.

    The same delegate answers ``applicationShouldHandleReopen:``, which macOS sends when the app's
    icon is clicked and no window is showing: pywebview has no answer of its own for it, and a
    window put away would have stayed away.
    """
    if not puts_away_on_close():
        return  # elsewhere the close button closes, and the app's Quit needs nothing of ours
    import AppKit

    global _quitter
    if _quitter is None:
        now = getattr(AppKit, "NSTerminateNow", 1)
        cancel = getattr(AppKit, "NSTerminateCancel", 0)
        held: list[Callable[[], bool]] = []

        class OrthoStudioQuit(AppKit.NSObject):  # type: ignore[misc]
            def applicationShouldTerminate_(self, app: object) -> int:  # noqa: N802
                return now if held[0]() else cancel

            def applicationShouldHandleReopen_hasVisibleWindows_(  # noqa: N802
                self, app: object, visible: bool
            ) -> bool:
                if not visible and _window is not None:
                    _window.show()
                return True

            def applicationSupportsSecureRestorableState_(  # noqa: N802
                self, app: object
            ) -> bool:
                return True

        _quitter = (OrthoStudioQuit.alloc().init(), held)
    delegate, held = _quitter  # type: ignore[misc]
    held[:] = [handler]

    def install() -> None:
        AppKit.NSApplication.sharedApplication().setDelegate_(delegate)

    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(install)


def _window_menu_shortcut() -> None:
    """Give the first entry of the :data:`WINDOW_MENU` its key, once the menus are built.

    ``webview.menu.MenuAction`` takes a title and something to run, and no key: this walks the
    menu macOS was given and puts one on. Nothing raises here; without it the entry is still in
    the menu, only without its key.
    """
    if not puts_away_on_close():
        return
    import AppKit

    command = getattr(AppKit, "NSEventModifierFlagCommand", 1 << 20)

    def put_it_there() -> None:
        main = AppKit.NSApplication.sharedApplication().mainMenu()
        if main is None:
            return
        for index in range(main.numberOfItems()):
            item = main.itemAtIndex_(index)
            submenu = item.submenu()
            if item.title() == WINDOW_MENU and submenu is not None and submenu.numberOfItems():
                first = submenu.itemAtIndex_(0)
                first.setKeyEquivalent_(WINDOW_MENU_KEY)
                first.setKeyEquivalentModifierMask_(command)
                return

    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(put_it_there)


def ask(title: str, message: str) -> bool:
    """Ask in a window of the system's own; whether the answer was yes.

    Never from the thread that draws: the close button's answer is given on that thread, and
    waiting there for a box that same thread must draw would wait for ever. Asked and not
    answerable, the answer is yes: the button was pressed, after all.
    """
    window = _window
    if window is None:
        return True
    try:
        return bool(window.create_confirmation_dialog(title, message))
    except Exception:
        return True


def close_now() -> None:
    """Close the window. It goes through the close button's own answer again, which must let it
    through this time (``orthostudio.desktop.on_close``) or the question would be asked for ever
    (a user, 2026-09-20)."""
    if _window is not None:
        _window.destroy()


def fits_the_screen(
    size: tuple[int, int], screens: object, room: tuple[int, int] = ROOM_FOR_THE_SYSTEM
) -> tuple[int, int]:
    """``size``, brought down to what the first screen has room for (:data:`ROOM_FOR_THE_SYSTEM`).

    Never up: a window larger than the page needs is only emptier. Never below :data:`MIN_SIZE`
    either, where the Plan's two columns stop fitting side by side; a screen that small is one
    where the window is scrolled rather than crowded.
    """
    try:
        first = next(iter(screens))  # type: ignore[call-overload]
        room_for = (int(first.width) - room[0], int(first.height) - room[1])
    except Exception:  # a system that will not say: what was asked for stands
        return size
    return (
        max(MIN_SIZE[0], min(size[0], room_for[0])),
        max(MIN_SIZE[1], min(size[1], room_for[1])),
    )


# -- where the window was left ------------------------------------------------------------------

PLACE_NAME = "OrthoStudio XP"
"""The name macOS keeps the window's frame under, in the app's own defaults
(``NSWindow.setFrameAutosaveName_``)."""

PLACE_FILE = "place.json"
"""Where Windows' window is kept, in the window's own folder (:func:`storage_dir`)."""

TITLE_BAR = 30
"""How tall a band at the top of a window is taken as its title bar, by which it is moved."""


def grabbable(
    band: tuple[float, float, float, float],
    screens: list[tuple[float, float, float, float]],
    need: tuple[float, float] = (100, 20),
) -> bool:
    """Whether a window's title bar ``band`` (x, y, width, height) lies on one of ``screens``
    (their working areas, in the same units) by at least ``need``, or by the whole band when it is
    smaller: enough of it to take it by the mouse. Either way up: a band and screens counted down
    from the top, as on Windows, or up from the bottom, as on macOS."""
    x, y, w, h = band
    need_w, need_h = min(need[0], w), min(need[1], h)
    for sx, sy, sw, sh in screens:
        across = min(x + w, sx + sw) - max(x, sx)
        down = min(y + h, sy + sh) - max(y, sy)
        if across >= need_w and down >= need_h:
            return True
    return False


def saved_place(store: Path) -> dict[str, Any] | None:
    """Where Windows' window was left (:data:`PLACE_FILE`): ``{x, y, width, height, maximized}``,
    or ``None`` when nothing was kept or what was kept cannot be read."""
    import json

    try:
        place = json.loads((store / PLACE_FILE).read_text(encoding="utf-8"))
        x, y, width, height = (int(place[k]) for k in ("x", "y", "width", "height"))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "maximized": place.get("maximized") is True,
    }


def back_where_it_was(native: Any, store: Path, *, system: str = sys.platform) -> None:
    """Put the window being made (``native``: an ``NSWindow``, a WinForms ``Form``) where it was
    left, the size it had, before it shows: in pywebview's ``before_show``, on the window's thread.

    The window opened where the system chose, on the screen of the shortcut rather than the one
    the user works on, and was moved and resized at every start (TinkerNZ, 2026-10-03; a user:
    "surtout sous Windows"). macOS keeps a window's frame by name in the app's defaults; Windows'
    is read from :data:`PLACE_FILE`. A place whose title bar lies on no screen any more, the
    screen it was on unplugged, is left to the system, as on a first start.
    """
    if system == "darwin":
        import AppKit

        if native.setFrameUsingName_(PLACE_NAME):
            frame = native.frame()
            top = frame.origin.y + frame.size.height  # macOS counts up from the bottom
            band = (frame.origin.x, top - TITLE_BAR, frame.size.width, TITLE_BAR)
            screens = []
            for screen in AppKit.NSScreen.screens():
                area = screen.visibleFrame()
                screens.append((area.origin.x, area.origin.y, area.size.width, area.size.height))
            if not grabbable(band, screens):
                native.center()
        # from now on AppKit keeps the frame under that name as the window moves or resizes
        native.setFrameAutosaveName_(PLACE_NAME)
    elif system == "win32":
        place = saved_place(store)
        if place is None:
            return
        from System.Drawing import Rectangle
        from System.Windows.Forms import FormStartPosition, FormWindowState, Screen

        screens = []
        for screen in Screen.AllScreens:
            area = screen.WorkingArea
            screens.append((area.X, area.Y, area.Width, area.Height))
        x, y, width, height = place["x"], place["y"], place["width"], place["height"]
        if not grabbable((x, y, width, TITLE_BAR), screens):
            return
        native.StartPosition = FormStartPosition.Manual
        native.Bounds = Rectangle(x, y, width, height)
        if place["maximized"]:
            native.WindowState = FormWindowState.Maximized


def keep_where_it_is(native: Any, store: Path, *, system: str = sys.platform) -> None:
    """Keep where Windows' window is, as it closes (pywebview's ``closing``, on the window's own
    thread): its place and size as a normal window, and whether it was maximized. macOS keeps its
    own as the window moves (:func:`back_where_it_was`).

    A normal window's place is its ``Bounds``. WinForms' ``RestoreBounds`` follows what code sets
    and the moment the window leaves its normal state, not a move or a resize by hand: taken
    always, it kept the place of each start for ever, and the window was moved and resized in
    vain (0.1.22rc5 on a Shadow, 2026-10-03). Maximized or minimized, it is the place the window
    goes back to."""
    if system != "win32":
        return
    import json

    from System.Windows.Forms import FormWindowState

    from orthostudio.fsutil import atomic_write_text

    normal = native.WindowState == FormWindowState.Normal
    bounds = native.Bounds if normal else native.RestoreBounds
    place = {
        "x": int(bounds.X),
        "y": int(bounds.Y),
        "width": int(bounds.Width),
        "height": int(bounds.Height),
        "maximized": native.WindowState == FormWindowState.Maximized,
    }
    store.mkdir(parents=True, exist_ok=True)
    atomic_write_text(store / PLACE_FILE, json.dumps(place) + "\n")


class _ToNote(logging.Handler):
    """pywebview's own warnings and errors, given to the ``note`` of :func:`show`.

    pywebview writes them to standard error, which the app opened from the Start menu does not
    have (``pythonw``): a WebView2 that could not start left an empty window, and its reason went
    nowhere, not even into ``serve.log`` (a Shadow PC, 2026-10-01).
    """

    def __init__(self, note: Callable[[str], None]) -> None:
        super().__init__(logging.WARNING)
        self._note = note

    def emit(self, record: logging.LogRecord) -> None:
        # a line that cannot be written must not take the window with it
        with contextlib.suppress(Exception):
            self._note(f"the window's web view: {record.getMessage()}")


def show(
    url: str,
    *,
    title: str,
    on_shown: Callable[[], None] | None = None,
    closes_when: Callable[[], bool] | None = None,
    on_close: Callable[[], bool] | None = None,
    may_quit: Callable[[], bool] | None = None,
    note: Callable[[str], None] | None = None,
    size: tuple[int, int] = SIZE,
    storage: Path | None = None,
) -> None:
    """Show ``url`` in a window of its own, and return once the window is closed.

    ``on_shown`` runs in a thread of its own the moment the window is up, which is where the
    engine is started: the window is on screen while it loads, rather than after. ``closes_when``
    is asked every :data:`POLL_S` while the window is open, and the window closes the moment it
    says yes: *Quit* stops the engine, and a window left on a page with nothing behind it would
    keep the app in the Dock with nothing to show. ``on_close`` is asked when the close button is
    clicked, and the window stays when it answers no, which is how the app is put away rather than
    quit (:func:`puts_away_on_close`); ``may_quit`` answers for the app's own Quit, wherever it is
    asked from (:func:`on_quit`). ``note`` is given a line when something that is not the engine
    goes wrong behind the window, which would otherwise be said to nobody.

    Raises when this system has no web view (the package missing, or no toolkit under it). The
    engine must not have been started before this returns, so that a system without a window
    leaves nothing behind when the caller falls back to the browser.
    """
    import webview  # not at import time: a system without it must still run the engine
    from webview.menu import Menu, MenuAction

    global _window
    store = storage_dir() if storage is None else storage
    store.mkdir(parents=True, exist_ok=True)
    width, height = fits_the_screen(size, webview.screens)
    window = webview.create_window(
        title,
        url,
        width=width,
        height=height,
        min_size=MIN_SIZE,
        # the page draws its own background for the theme it was given; white flashes on a dark one
        background_color="#14171d",
        # pywebview keeps text from being selected by default, which a browser never does: a path,
        # a tile's name, an error, the very command this app tells a user to run, none of them
        # could be copied out of the window (2026-09-20)
        text_select=True,
        # and the page keeps its own zoom: on a trackpad, pinching is how the map is zoomed, and a
        # window that zoomed itself instead would take that away. Cmd+plus and Ctrl+plus are the
        # page's business too, through PageTools.set_zoom.
        zoomable=False,
        js_api=PageTools(),
    )
    if window is None:  # pywebview answers nothing when it could not make one
        raise RuntimeError("the web view gave no window")
    _window = window

    if on_close is not None:

        def closing() -> bool | None:
            # pywebview takes the close away when a handler answers False, and lets it through
            # otherwise (webview.event.Event.set)
            return None if on_close() else False

        window.events.closing += closing

    # The window opens where it was left, the size it had (back_where_it_was); a place that
    # cannot be had leaves it where the system puts it, as before, and says so in the log.
    def placed() -> None:
        try:
            back_where_it_was(window.native, store)
        except Exception as exc:
            if note is not None:
                note(f"the window opened where the system put it, not where it was left: {exc!r}")

    def kept() -> None:  # answers nothing: closing is not taken away
        try:
            keep_where_it_is(window.native, store)
        except Exception as exc:
            if note is not None:
                note(f"where the window was left could not be kept: {exc!r}")

    window.events.before_show += placed
    window.events.closing += kept

    def behind() -> None:
        """What runs while the window is up. It must end when the window does: pywebview gives it
        a thread of its own, and that thread is not a daemon.

        The engine starts first and on its own: what follows is the window's own comfort, and a
        comfort that fails must not take the engine with it. It did: ``on_quit`` reached for AppKit
        on a system that has none, the thread died on the import, the engine was never started, and
        the opening page waited for it for ever with nothing in the log (Windows, 2026-09-20).
        """
        if on_shown is not None:
            on_shown()
        try:
            if may_quit is not None:
                on_quit(may_quit)
            _window_menu_shortcut()
        except Exception as exc:  # the window is up and the engine runs: this is not worth dying
            if note is not None:
                note(f"the window opened without its menu and its Quit: {exc!r}")
        if closes_when is None:
            return
        closed = threading.Event()
        window.events.closed += closed.set
        while not closed.wait(POLL_S):
            if closes_when():
                window.destroy()
                return

    start = behind if any(x is not None for x in (on_shown, closes_when, may_quit)) else None
    # the way back to a window that was put away, next to the Dock's icon; where the close button
    # closes, there is nothing to come back to
    menu = (
        [Menu(WINDOW_MENU, [MenuAction(title, lambda: window.show())])]
        if puts_away_on_close()
        else []
    )
    icon = icon_file()
    # what pywebview itself has to say about the web view goes to the app's log, for as long as the
    # window is up (:class:`_ToNote`)
    said = logging.getLogger("pywebview")
    relay = _ToNote(note) if note is not None else None
    if relay is not None:
        said.addHandler(relay)
    try:
        webview.start(
            start,
            menu=menu,
            private_mode=False,
            storage_path=str(store),
            **({"icon": icon} if icon else {}),
        )
    finally:
        if relay is not None:
            said.removeHandler(relay)
