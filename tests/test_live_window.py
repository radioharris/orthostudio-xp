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

    def stop(self) -> None:
        self.stopped += 1
        self.state = IDLE

    def wait_idle(self) -> None:
        pass


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
