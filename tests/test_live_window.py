# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's window closes and quits as OrthoStudio XP's (orthostudio.live.web, the user asked,
2026-09-29 and 2026-10-03): on macOS the close button and Cmd+W put it away, a stream going on;
Quit asks in the page, as OrthoStudio XP's, the window in front; on Windows and Linux closing is
quitting, and asks first, in a box in the window's language, only while a stream runs."""

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
        self.closing = False
        self.stopped = 0
        self.said: list[str] = []

    def stop(self) -> None:
        self.stopped += 1
        self.state = IDLE

    def close(self) -> None:
        self.closing = True
        if self.state != IDLE:
            self.stop()

    def gone(self) -> bool:
        return self.leaving_for in ("build", "quit") or (self.closing and self.state == IDLE)

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

        def mine(what: str):  # every call names Live's window: OrthoStudio XP's may be beside it
            def did(name: str | None = None) -> None:
                assert name == web.TITLE, (what, name)
                done.append(what)

            return did

        monkeypatch.setattr(window_module, "away", mine("away"))
        monkeypatch.setattr(window_module, "to_the_front", mine("front"))
        monkeypatch.setattr(window_module, "ask_the_page_to_quit", mine("page"))
        # Live's window up, OrthoStudio XP's not
        monkeypatch.setattr(window_module, "shown", lambda name=None: name == web.TITLE)

        def ask(title: str, message: str, *, name: str | None = None) -> bool:
            assert name == web.TITLE
            done.append(("ask", message))
            return answer

        monkeypatch.setattr(window_module, "ask", ask)
        monkeypatch.setattr(window_module, "show", lambda url, **kw: given.update(kw))
        monkeypatch.setattr(
            settings_module, "load_settings", lambda: SimpleNamespace(language=language)
        )
        control = FakeControl()
        live = web._Starting()
        live.control = control
        monkeypatch.setattr(web, "_here", live)  # Live in this program, as the app's start sets up
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


def asked_the_page(done: list) -> bool:
    deadline = time.monotonic() + 5.0
    while "page" not in done and time.monotonic() < deadline:
        time.sleep(0.01)
    return "page" in done


@pytest.mark.parametrize("state", [IDLE, STREAMING])
def test_the_app_s_quit_asks_only_while_a_stream_runs(opened, state: str) -> None:
    """Cmd+Q, the app's menu, the Dock's: the app goes at once when nothing streams; while a
    stream runs, one box over Live's window first, and on yes the stream stops (X-Plane flying on
    over what is built) and the windows close. A page's own Quit closes its window alone."""
    given, done, control = opened(mac=True)
    control.state = state
    if state == IDLE:
        assert given["may_quit"]() is True and questions_none(done)
        return
    assert given["may_quit"]() is False and done[0] == "front"
    assert questions(done) == [web.QUIT_WHILE_STREAMING["en"]]
    deadline = time.monotonic() + 5.0
    while control.leaving_for != "quit" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert control.leaving_for == "quit"  # the stream stopped, the app going
    assert given["closes_when"]() is True


def questions_none(done: list) -> bool:
    return not any(isinstance(d, tuple) for d in done)


def test_the_question_in_the_window_s_language(opened) -> None:
    given, done, control = opened(mac=False, answer=False, language="fr")
    control.state = STREAMING
    given["on_close"]()
    assert questions(done) == [web.QUIT_WHILE_STREAMING["fr"]]
    time.sleep(0.05)
    assert control.stopped == 0 and not given["closes_when"]()  # "no": nothing stops


def test_the_window_keeps_a_place_of_its_own_in_the_app_s_one_program(opened) -> None:
    """Its place is Live's own (macOS keeps it by name in the app's defaults, Windows in a file of
    its own): under OrthoStudio XP's, each window opened where the other was left (2026-10-03).
    The web view's folder is the program's, OrthoStudio XP's beside it sharing it; the zoom and
    the text size each page keeps stay its own, by its address. The Window menu has both."""
    given, _done, _control = opened(True)
    assert given["place"] == web.TITLE != window_module.PLACE_NAME
    assert "storage" not in given  # the program's, window.storage_dir()
    assert [title for title, _ in given["menu"]] == ["OrthoStudio XP", web.TITLE]


def test_on_windows_and_linux_closing_is_quitting(opened) -> None:
    given, done, control = opened(mac=False)
    assert given["on_close"]() is True  # nothing streams: the window closes, the app ends
    control.state = STREAMING
    assert given["on_close"]() is False and "away" not in done
    assert questions(done) == [web.QUIT_WHILE_STREAMING["en"]]


def test_asked_once(opened) -> None:
    given, done, control = opened(mac=False)
    control.state = STREAMING
    stop = threading.Event()
    control.stop = lambda: stop.set()  # type: ignore[method-assign]
    given["on_close"]()
    questions(done)
    assert stop.wait(5.0)
    assert given["on_close"]() is False  # asked already: it goes by itself once the pack is out
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


def test_the_window_opens_at_once_and_live_is_set_up_behind_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window first, on OrthoStudio XP's opening page, Live set up behind it, as OrthoStudio
    XP's window with its engine: it came only once Live was ready, after seconds of nothing on a
    cloud PC (a user, 2026-10-03). The app's log says how long each part took."""
    import urllib.request

    from orthostudio.live import control as control_module

    assert web.IDLE == control_module.IDLE
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    log = tmp_path / "serve.log"
    control = FakeControl()
    seen: dict = {}

    def set_up(port: int, *, front: object) -> tuple:
        time.sleep(0.2)  # Live's imports and setting up, behind the window
        served = threading.Thread(target=lambda: None)
        served.start()
        took = (("its code", 0.1), ("setting up", 0.05), ("its page", 0.05))
        live_log = SimpleNamespace(close=lambda: None)
        return control, SimpleNamespace(should_exit=False), served, live_log, None, took

    def show(url: str, **kw: object) -> None:
        seen["url"] = url
        with urllib.request.urlopen(url, timeout=5) as answer:
            seen["page"] = answer.read().decode("utf-8")
        assert control.state == IDLE and seen.get("ready") is None  # nothing set up yet
        kw["on_shown"]()  # type: ignore[operator]
        deadline = time.monotonic() + 5.0
        while not kw["closes_when"]() and time.monotonic() < deadline:  # type: ignore[operator]
            if seen.setdefault("ready", None) is None and "Live is ready" in log.read_text():
                seen["ready"] = True
                control.leaving_for = "build"  # the welcome's answer
            time.sleep(0.02)

    monkeypatch.setattr(window_module, "possible", lambda: True)
    monkeypatch.setattr(window_module, "show", show)
    monkeypatch.setattr(web, "_set_up", set_up)
    assert web.open_window(1, start="welcome", log=log) == "build"
    assert not seen["url"].endswith(":1/welcome")  # the opening page, on a port of its own
    assert 'location.replace(ENGINE + "welcome")' in seen["page"]
    assert "Opening OrthoStudio XP" in seen["page"]
    said = log.read_text()
    assert "Live's window opens" in said and "Live is ready: its code 0.1 s" in said


def test_live_that_cannot_be_set_up_closes_its_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    log = tmp_path / "serve.log"
    closed = []

    def set_up(port: int, *, front: object) -> tuple:
        raise OSError("nothing could listen on 127.0.0.1:1")

    def show(url: str, **kw: object) -> None:
        deadline = time.monotonic() + 5.0
        while not kw["closes_when"]() and time.monotonic() < deadline:  # type: ignore[operator]
            time.sleep(0.02)
        closed.append(kw["closes_when"]())  # type: ignore[operator]

    monkeypatch.setattr(window_module, "possible", lambda: True)
    monkeypatch.setattr(window_module, "show", show)
    monkeypatch.setattr(web, "_set_up", set_up)
    with pytest.raises(web.LiveStartError):
        web.open_window(1, log=log)
    assert closed == [True]
    assert "OrthoStudio XP Live could not start (OSError" in log.read_text()


def test_live_serves_in_a_process_without_a_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Windows app runs in pythonw, which has no console: Live's server, set up in the app's
    own process at its start, stopped there ("Unable to configure formatter 'default'", a cloud
    PC, 2026-10-03)."""
    import socket
    import sys

    from fastapi import FastAPI

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    server, thread = web._serve(FastAPI(), port)
    try:
        assert server.started
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_live_s_start_leaves_the_tile_engine_out(tmp_path: Path) -> None:
    """Live's window serves its page without OrthoStudio XP's tile engine. It took the guard of its
    page from the engine's app and loaded the whole engine with it: half of what its start read,
    which took 44 s on a cloud PC the first time after an install (2026-10-03)."""
    import os
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from orthostudio.api.presence import Presence\n"
        "from orthostudio.live import control, logfile, web\n"
        "from orthostudio.live.providers import listing\n"
        "web.create_live_app(None)\n"
        "listing()\n"
        "engine = ('orthostudio.api.jobs', 'orthostudio.api.app', 'orthostudio.pipeline', 'pyproj',"
        " 'scipy', 'shapely')\n"
        "print([m for m in engine if m in sys.modules])\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
        env={**os.environ, "OSXP_HOME": str(tmp_path)},
    )
    assert done.stdout.strip() == "[]", done.stdout + done.stderr
