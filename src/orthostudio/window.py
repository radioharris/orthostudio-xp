"""The app in a window of its own, when the system has one to give.

The interface is a page, and a page in a browser is not a window. Its icon leaves the Dock while
the app runs, a click on the icon has nothing to bring in front, every click opens one more tab,
and two tabs of the same app do not follow each other (a user, 2026-09-20). Shown through the web
view the system already carries (WKWebView on macOS, WebView2 on Windows, WebKitGTK on Linux), the
app is an app: one window, an icon that stays, a click that brings it back. The page inside is the
very one the browser shows, served by the same engine on the same port: nothing of the interface
is written twice.

Nothing here is required. :func:`show` raises when the system has no web view to give, and the
caller opens the browser instead, which is what every version until 0.1.9 did. A system that could
have a window and lacks a library is told which one, with a link (:func:`hint`); OrthoStudio XP
installs nothing of its own on a system it does not own.
"""

from __future__ import annotations

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
    "SIZE",
    "WEBVIEW2_HELP",
    "ask",
    "ask_the_page_to_quit",
    "away",
    "close_now",
    "hint",
    "install",
    "on_quit",
    "possible",
    "puts_away_on_close",
    "show",
    "storage_dir",
    "to_the_front",
]

POLL_S = 2.0
"""How often ``closes_when`` is asked, while the window is open."""

SIZE = (1440, 920)
"""The window a first run opens. Narrower than 1280, the Plan's two columns crowd each other."""

MIN_SIZE = (1024, 700)

WINDOW_MENU = "Window"
"""The menu that brings the window back once it has been put away. macOS builds one of its own for
apps that list their windows in it; pywebview builds none, and without it the Dock's icon was the
only way back (a user asked, 2026-09-20)."""

WINDOW_MENU_KEY = "0"
"""Cmd+0 for that entry, which a pywebview menu cannot carry and :func:`_window_menu_shortcut`
puts there by hand."""

LINUX_PACKAGES = "python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1"
"""What Debian and Ubuntu call the GTK web view. Other distributions name them otherwise, which is
why the message carries the link rather than a command for a system we did not recognise."""

_window: Any = None
"""The window that is up, for what has to speak to its page from elsewhere."""

LINUX_HELP = "https://pywebview.flowrl.com/guide/installation.html"
WEBVIEW2_HELP = "https://developer.microsoft.com/microsoft-edge/webview2/"


def storage_dir() -> Path:
    """Where the web view keeps what the page stores (the theme, the language, the Experts panel).

    pywebview starts in a private mode that forgets all of it when the window closes; the page
    would lose its theme and its language at every launch. The folder is OrthoStudio XP's own, so
    that removing the app takes it away with the rest.
    """
    from orthostudio.home import osxp_home

    return osxp_home() / "window"


def install() -> str | None:
    """What to do about it, in one line: the command on Linux, Microsoft's page on Windows.

    Kept apart from :func:`hint`, whose sentence is English, so that the page can put its own
    words around this and leave the command itself where it is written once."""
    if sys.platform.startswith("linux"):
        return f"sudo apt install {LINUX_PACKAGES}"
    if sys.platform == "win32":
        return WEBVIEW2_HELP
    return None


def hint() -> str | None:
    """What to install for a window on this system, once :func:`show` has refused; ``None`` when
    the system is one we cannot advise (macOS carries WKWebView, and has nothing to install)."""
    if sys.platform.startswith("linux"):
        return (
            "OrthoStudio XP opened in your browser: this system has no web view of its own to "
            f"show it in a window. On Debian and Ubuntu: sudo apt install {LINUX_PACKAGES}. "
            f"For other systems: {LINUX_HELP}"
        )
    if sys.platform == "win32":
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


def _webview2_runtime() -> bool:
    """Whether Windows carries the WebView2 Runtime, read the way Microsoft says to read it: the
    ``pv`` value of the runtime's key, per machine or per user, present and above 0.0.0.0. The
    installer offers it from the same two keys (``tools/package/webview2.pas``)."""
    if sys.platform != "win32":
        return False
    import winreg

    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
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
            return True
    return False


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
        return _here("clr") and _webview2_runtime()  # pythonnet, and Microsoft's own component
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
    window = webview.create_window(
        title,
        url,
        width=size[0],
        height=size[1],
        min_size=MIN_SIZE,
        # the page draws its own background for the theme it was given; white flashes on a dark one
        background_color="#14171d",
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
    webview.start(start, menu=menu, private_mode=False, storage_path=str(store))
