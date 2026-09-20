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

    fake = type("W", (), {"create_window": lambda *a, **k: FakeWindow(), "start": fake_start})
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
