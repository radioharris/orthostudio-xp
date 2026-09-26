# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The window of its own, ``orthostudio/window.py``: ``docs/specs/packaging.md``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from orthostudio import window
from orthostudio.home import osxp_home


def test_the_window_keeps_what_the_page_stores_in_orthostudios_own_folder() -> None:
    # pywebview forgets it all when the window closes unless it is given somewhere to keep it:
    # the page would lose its theme and its language at every launch
    assert window.storage_dir() == osxp_home() / "window"
    assert window.storage_dir().is_relative_to(osxp_home())


@pytest.mark.parametrize(
    ("platform", "says"),
    [
        ("linux", "gir1.2-webkit2-4.1"),
        ("win32", "WebView2"),
    ],
)
def test_a_system_without_a_window_is_told_what_to_install(
    platform: str, says: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(window.sys, "platform", platform)
    # on Linux the packages are only worth naming to the Python they were built for
    monkeypatch.setattr(window.sys, "base_prefix", "/usr")
    words = window.hint()
    assert words is not None and says in words
    # a link, so that a system we did not name is not left with a command that does not fit it
    assert "http" in words


def test_macos_is_told_nothing_because_it_carries_its_own_web_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(window.sys, "platform", "darwin")
    assert window.hint() is None


def test_asking_whether_a_window_is_possible_never_raises_and_starts_nothing() -> None:
    # the answer depends on the machine the tests run on; what matters is that asking is safe,
    # since it is asked before the engine starts (orthostudio.desktop.in_a_window)
    assert window.possible() in (True, False)
    assert window.possible() == window.possible()


def test_asking_imports_neither_the_web_view_nor_the_toolkit_under_it() -> None:
    """Importing the toolkit opens a connection to the window server, and the process grows an
    icon in the Dock. The engine asks this, for the doctor's window check, and must stay the plain
    program it is; asking the other way put an icon in the Dock for every test worker that asked
    (2026-09-20)."""
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "from orthostudio import window\n"
            "window.possible()\n"
            "print(sorted(m for m in sys.modules if m in "
            "{'webview', 'AppKit', 'WebKit', 'objc', 'gi', 'PyQt6', 'PySide6'}))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.strip() == "[]", done.stdout


def test_show_asks_for_a_web_view_and_says_so_when_there_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import builtins

    real = builtins.__import__

    def no_webview(name: str, *args: object, **kwargs: object) -> object:
        if name == "webview":
            raise ModuleNotFoundError("No module named 'webview'")
        return real(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", no_webview)
    with pytest.raises(ModuleNotFoundError):
        window.show("http://127.0.0.1:8641/", title="OrthoStudio XP", storage=tmp_path)


def test_the_window_asks_nothing_of_cocoa_on_a_system_that_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``import AppKit`` on Windows killed the thread that starts the engine, so the engine never
    started and the opening page waited for it for ever, with nothing in the log (2026-09-20)."""
    for platform in ("win32", "linux"):
        monkeypatch.setattr(window.sys, "platform", platform)
        assert window.on_quit(lambda: True) is None  # would raise if it reached for AppKit
        assert window._window_menu_shortcut() is None
        assert window.puts_away_on_close() is False


def test_the_engine_starts_even_when_the_window_trimmings_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """What the window puts around itself is comfort; the engine is the point. A comfort that
    fails must not take the engine with it, and must not do it silently."""
    started, said = [], []

    class FakeWindow:
        events = type("E", (), {"closed": type("S", (), {"__iadd__": lambda s, f: s})()})()

        def show(self) -> None:
            return None

    monkeypatch.setattr(
        window, "on_quit", lambda h: (_ for _ in ()).throw(RuntimeError("no AppKit"))
    )
    monkeypatch.setattr(window, "_window_menu_shortcut", lambda: None)

    def fake_start(func: object, **kw: object) -> None:
        assert callable(func)
        func()

    fake = type(
        "W",
        (),
        {"create_window": lambda *a, **k: FakeWindow(), "start": fake_start, "screens": []},
    )
    monkeypatch.setitem(__import__("sys").modules, "webview", fake)
    monkeypatch.setitem(
        __import__("sys").modules,
        "webview.menu",
        type("M", (), {"Menu": lambda *a, **k: None, "MenuAction": lambda *a, **k: None}),
    )

    window.show(
        "http://127.0.0.1:8641/",
        title="OrthoStudio XP",
        on_shown=lambda: started.append(True),
        may_quit=lambda: True,
        note=said.append,
        storage=tmp_path,
    )
    assert started == [True]  # the engine ran
    assert said and "no AppKit" in said[0]  # and the failure was said


def test_the_window_package_travels_everywhere() -> None:
    """Left out of Linux, the advice the doctor gives there led nowhere: it names the system
    packages to install, and they are useless without the package that uses them (2026-09-20).
    The wheel is pure Python and asks nothing of the system by itself."""
    lock = (Path(__file__).resolve().parents[1] / "uv.lock").read_text(encoding="utf-8")
    asked = [line for line in lock.splitlines() if '{ name = "pywebview"' in line]
    assert asked, "pywebview is not in the lock"
    assert not any("sys_platform" in line for line in asked), asked


def test_linux_is_told_the_truth_about_which_python_can_see_the_toolkit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A distribution builds its web view for the Python it ships. Installed beside an app that
    carries its own, the packages are there and invisible to it: measured in a container, apt put
    gi under /usr/lib/python3/dist-packages for 3.10 and the app's 3.14 never saw it
    (2026-09-20). Naming them to that app is advice that leads nowhere."""
    monkeypatch.setattr(window.sys, "platform", "linux")

    monkeypatch.setattr(window.sys, "base_prefix", "/opt/OrthoStudio-XP/python")
    assert window.the_distributions_python() is False
    assert window.install() is None  # nothing to do, so nothing is asked of the user
    words = window.hint() or ""
    assert "carries a Python of its own" in words and "apt install" not in words

    monkeypatch.setattr(window.sys, "base_prefix", "/usr")
    assert window.the_distributions_python() is True
    assert "apt install" in (window.install() or "")  # there, the packages do meet the app


def test_a_window_nobody_can_have_is_not_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    from orthostudio import doctor

    monkeypatch.setattr(window, "possible", lambda: False)
    monkeypatch.setattr(window, "install", lambda: None)
    assert doctor._window().status == "skip"  # an orange pill nobody can clear is noise
    monkeypatch.setattr(window, "install", lambda: "sudo apt install something")
    assert doctor._window().status == "warn"  # there is something to do: it is worth a colour


class FakeScreen:
    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height


def test_the_window_is_brought_down_to_what_the_screen_has_room_for() -> None:
    """Asked for its whole height, the window went under the menu bar and the Dock, and the map's
    legend was cut off on a 14-inch laptop (a user, 2026-09-20)."""
    small = window.fits_the_screen(window.SIZE, [FakeScreen(1512, 982)])
    assert small == (1440, 982 - window.ROOM_FOR_THE_SYSTEM[1])
    # a screen with room to spare gets the size as asked, never more: a bigger window is emptier
    assert window.fits_the_screen(window.SIZE, [FakeScreen(3840, 2160)]) == window.SIZE
    # and never below what the Plan's two columns need side by side
    assert window.fits_the_screen(window.SIZE, [FakeScreen(800, 600)]) == window.MIN_SIZE
    # a system that will not say keeps what was asked for
    assert window.fits_the_screen(window.SIZE, []) == window.SIZE


def test_the_window_lets_text_be_selected_like_a_browser_does() -> None:
    """pywebview keeps text from being selected by default, and injects user-select: none over
    the whole body. A browser never does: a path, a tile's name, an error, the very command this
    app tells a user to run, none of them could be copied out of the window (2026-09-20)."""
    source = (Path(__file__).resolve().parents[1] / "src/orthostudio/window.py").read_text(
        encoding="utf-8"
    )
    start = source.index("webview.create_window(")
    call = source[start : source.index("if window is None", start)]
    assert "text_select=True" in call
    # and the page keeps the pinch: it is how the map is zoomed
    assert "zoomable=False" in call


def test_the_window_zooms_its_page_because_a_browser_would() -> None:
    """A user found the text bigger in the window than in his browser, and had no way to make it
    smaller: a WKWebView will not zoom unless it is asked in Objective-C, and pywebview turns the
    browser's own shortcuts off in WebView2 (`AreBrowserAcceleratorKeysEnabled` follows `debug`),
    so Ctrl+plus did nothing on Windows either (2026-09-20).

    The page keeps the keys and the window does the scaling: a CSS zoom would take `100vh` with
    it and cut the map off at the foot, which is the bug 0.1.8 had just fixed."""
    from orthostudio.window import WINDOW_MENU_KEY, ZOOM_LIMITS, PageTools

    source = (Path(__file__).resolve().parents[1] / "src/orthostudio/window.py").read_text(
        encoding="utf-8"
    )
    start = source.index("webview.create_window(")
    call = source[start : source.index("if window is None", start)]
    assert "js_api=PageTools()" in call
    assert "setPageZoom_" in source and "ZoomFactor" in source  # macOS, then Windows

    # asked without a window, it says no rather than raising: the page then leaves the size alone
    tools = PageTools()
    assert tools.set_zoom(1.5) is False
    assert tools.set_zoom("not a number") is False  # type: ignore[arg-type]
    low, high = ZOOM_LIMITS
    assert low < 1 < high

    # Cmd+0 is what every browser uses to put the text back to its own size, and a menu's key is
    # taken by AppKit before the page sees it: the Window entry gave it up (it had it in 0.1.8)
    assert WINDOW_MENU_KEY != "0"


def test_the_window_carries_the_apps_icon_and_not_pythons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows the app is started by `python\\pythonw.exe`, and pywebview takes the icon of
    sys.executable when it is given none (`platforms/winforms.py`): the window and its taskbar
    button carried Python's icon (a user, 2026-09-20). The build already writes the icon beside
    the installed tree; the window just had to be told where."""
    from orthostudio import window as win

    app = tmp_path / "OrthoStudio XP"
    (app / "python").mkdir(parents=True)
    exe = app / "python" / "pythonw.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(win.sys, "executable", str(exe))
    monkeypatch.setattr(win.sys, "platform", "win32")
    assert win.icon_file() is None  # nothing written yet: the window opens as it always did
    (app / "orthostudio.ico").write_bytes(b"")
    assert win.icon_file() == str(app / "orthostudio.ico")

    # macOS asks for none: a .app carries its icon and the window takes it
    monkeypatch.setattr(win.sys, "platform", "darwin")
    assert win.icon_file() is None

    source = (Path(__file__).resolve().parents[1] / "src/orthostudio/window.py").read_text(
        encoding="utf-8"
    )
    # and an icon that is not there must not be passed: pywebview runs abspath on it
    assert '**({"icon": icon} if icon else {})' in source
