# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's window closes and quits as OrthoStudio XP's (orthostudio.live.web, the user asked,
2026-09-29): on macOS the close button and Cmd+W put it away, a stream going on; Quit ends the app,
and quitting while a stream runs asks first, in the window's language, the window in front."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orthostudio import window as window_module
from orthostudio.live import settings as settings_module
from orthostudio.live import web
from orthostudio.live.control import IDLE

STREAMING = "streaming"


class FakeControl:
    def __init__(self) -> None:
        self.state = IDLE
        self.leaving_for: str | None = None
        self.stopped = 0
        self.said: list[str] = []

    def stop(self) -> None:
        self.stopped += 1
        self.state = IDLE

    def wait_idle(self) -> None:
        pass

    def busy(self) -> bool:
        return self.state != IDLE

    def quit(self, *, force: bool = False) -> bool:
        self.leaving_for = "quit"
        return True

    def log(self, text: str) -> None:
        self.said.append(text)


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Live's window "shown" on a system that puts it away on close (macOS) or not: what it was
    given, what it did, and the answer its question gets."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))

    def open_on(mac: bool, answer: bool = True, language: str = "en"):
        done: list = []
        given: dict = {}
        monkeypatch.setattr(window_module, "puts_away_on_close", lambda: mac)
        monkeypatch.setattr(window_module, "away", lambda: done.append("away"))
        monkeypatch.setattr(window_module, "to_the_front", lambda: done.append("front"))

        def ask(title: str, message: str) -> bool:
            done.append(("ask", message))
            return answer

        monkeypatch.setattr(window_module, "ask", ask)
        monkeypatch.setattr(window_module, "show", lambda url, **kw: given.update(kw))
        monkeypatch.setattr(
            settings_module, "load_settings", lambda: SimpleNamespace(language=language)
        )
        control = FakeControl()
        web._in_a_window("http://127.0.0.1:1/", control)
        control.stopped = 0  # the window's own stop as it goes: the fake one went at once
        return given, done, control

    return open_on


def questions(done: list) -> list[str]:
    deadline = time.monotonic() + 5.0
    while not any(isinstance(d, tuple) for d in done) and time.monotonic() < deadline:
        time.sleep(0.01)
    return [d[1] for d in done if isinstance(d, tuple)]


def test_on_macos_closing_puts_the_window_away(opened) -> None:
    given, done, control = opened(mac=True)
    assert given["on_close"]() is False and done == ["away"]  # the app stays in the Dock
    control.state = STREAMING
    assert given["on_close"]() is False and done == ["away", "away"]  # the stream goes on
    assert control.stopped == 0


def test_quit_when_nothing_streams_is_at_once(opened) -> None:
    given, done, _ = opened(mac=True)
    assert given["may_quit"]() is True and done == []


def test_quit_while_streaming_asks_first_the_window_in_front(opened) -> None:
    given, done, control = opened(mac=True)
    control.state = STREAMING
    assert given["may_quit"]() is False
    assert done[0] == "front"
    assert questions(done) == [web.QUIT_WHILE_STREAMING["en"]]
    deadline = time.monotonic() + 5.0
    while control.stopped == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert control.stopped == 1
    assert given["closes_when"]() is True  # the stream out: the app goes
    assert given["may_quit"]() is True


def test_the_question_in_the_window_s_language(opened) -> None:
    given, done, control = opened(mac=True, answer=False, language="fr")
    control.state = STREAMING
    given["may_quit"]()
    assert questions(done) == [web.QUIT_WHILE_STREAMING["fr"]]
    time.sleep(0.05)
    assert control.stopped == 0 and not given["closes_when"]()  # "no": nothing stops


def test_on_windows_and_linux_closing_is_quitting(opened) -> None:
    given, done, control = opened(mac=False)
    assert given["on_close"]() is True  # nothing streams: the window closes, the app ends
    control.state = STREAMING
    assert given["on_close"]() is False and "away" not in done
    assert questions(done) == [web.QUIT_WHILE_STREAMING["en"]]


def test_asked_once(opened) -> None:
    given, done, control = opened(mac=True)
    control.state = STREAMING
    stop = threading.Event()
    control.stop = lambda: stop.set()  # type: ignore[method-assign]
    given["may_quit"]()
    questions(done)
    assert stop.wait(5.0)
    assert given["may_quit"]() is False  # asked already: it goes by itself once the pack is out
    assert len(questions(done)) == 1


def test_the_app_s_update_closes_the_window_without_a_question(opened) -> None:
    """The Windows installer asked Live to go (``control.quit``, ``stop_running.pas``): the window
    closes, streaming or not, and nothing is asked: the installer asked the user already."""
    for mac in (True, False):
        given, done, control = opened(mac=mac)
        control.state = STREAMING
        assert given["closes_when"]() is False
        control.leaving_for = "quit"
        assert given["closes_when"]() is True
        assert given["may_quit"]() is True and questions(done) == []


@pytest.mark.parametrize("leaving_for", ["quit", "build"])
def test_in_the_browser_it_ends_as_in_a_window(
    monkeypatch: pytest.MonkeyPatch, leaving_for: str
) -> None:
    """Without a window (a Linux without its libraries, ``--browser``), the page in the browser:
    the process ends once asked to quit, or once the welcome's answer is OrthoStudio XP's window
    (the app opens it then), as with a window."""
    monkeypatch.setattr(web.webbrowser, "open", lambda url: True)
    control = FakeControl()
    ended = threading.Event()

    def page() -> None:
        web._in_the_browser("http://127.0.0.1:1/", control)
        ended.set()

    threading.Thread(target=page, daemon=True).start()
    assert not ended.wait(1.5)
    control.leaving_for = leaving_for
    assert ended.wait(5.0)


def test_in_the_browser_the_app_s_live_goes_a_while_after_its_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Started by the app and shown in the browser (a Linux without the window's libraries), Live
    goes once no page of it has spoken for a while, as OrthoStudio XP's engine does: closing a tab
    ends nothing, and nothing would show the process then. Not while a stream is on its way."""
    from orthostudio.api import presence

    monkeypatch.setattr(presence, "QUIT_AFTER_S", 0.6)
    monkeypatch.setattr(presence, "TICK_S", 0.05)
    monkeypatch.setattr(web.webbrowser, "open", lambda url: True)
    control = FakeControl()
    control.state = STREAMING
    seen = presence.Presence()
    ended, gone = threading.Event(), threading.Event()

    def page() -> None:
        web._quit_when_closed(seen, control, ended)
        web._in_the_browser("http://127.0.0.1:1/", control)
        gone.set()

    threading.Thread(target=page, daemon=True).start()
    assert not gone.wait(1.5)  # a stream: it stays, its page closed or not
    control.state = IDLE
    for _ in range(10):  # the page asks for its status: it stays
        seen.seen()
        assert not gone.wait(0.1)
    assert gone.wait(5.0)  # the page gone, nothing streaming: it goes
    assert control.leaving_for == "quit" and "Live goes" in control.said[0]
    ended.set()
