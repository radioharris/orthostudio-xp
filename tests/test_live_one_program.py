# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP's window and OrthoStudio XP Live's, one program with one icon in the Dock or the
taskbar (the user's choice, 2026-10-03): each opens beside the other, Live set up in that program
the first time; Quit in either page quits the app, the other window going too, at once when
nothing runs there, else after its own page's question; Cmd+Q asks where something runs."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orthostudio import desktop
from orthostudio import window as window_module
from orthostudio.live import app_start, web
from orthostudio.live.control import IDLE

STREAMING = "streaming"


class Control:
    """Live's control as the windows see it."""

    def __init__(self, state: str = IDLE) -> None:
        self.state = state
        self.leaving_for: str | None = None
        self.closing = False
        self.stopped = 0

    def busy(self) -> bool:
        return self.state != IDLE

    def stop(self) -> None:
        self.stopped += 1

    def close(self) -> None:
        self.closing = True

    def gone(self) -> bool:
        return self.leaving_for in ("build", "quit") or (self.closing and self.state == IDLE)

    def wait_idle(self) -> None:
        pass


@pytest.fixture
def program(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The window module of a program showing windows, as far as the app's code asks it: which
    are up, and what was done to which."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    up: set[str] = set()
    done: list[tuple[str, Any]] = []
    monkeypatch.setattr(window_module, "running", lambda: bool(up))
    monkeypatch.setattr(window_module, "shown", lambda name=None: name in up)
    monkeypatch.setattr(
        window_module, "to_the_front", lambda name=None: done.append(("front", name))
    )
    monkeypatch.setattr(window_module, "close_now", lambda name=None: done.append(("close", name)))
    monkeypatch.setattr(
        window_module,
        "ask_the_page_to_quit",
        lambda name=None: done.append(("page", name)),
    )

    def open_another(url: str, **kw: Any) -> None:
        done.append(("open", kw["place"]))
        up.add(kw["place"])
        kw["given_url"] = url
        opened.append(kw)

    opened: list[dict[str, Any]] = []
    monkeypatch.setattr(window_module, "open_another", open_another)
    monkeypatch.setattr(web, "_here", None)
    return SimpleNamespace(up=up, done=done, opened=opened, log=tmp_path / "serve.log")


def wait_for(predicate: Any, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return bool(predicate())


# -- each window beside the other ----------------------------------------------------------------


def test_osxp_s_window_opens_beside_live_s_in_its_program(
    program: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live's Build tiles: OrthoStudio XP's window in Live's program, its engine started behind
    the opening page as at the app's start; brought to the front once it is up."""
    assert desktop.open_osxp_here(program.log) is False  # no window shown: another program
    program.up.add(web.TITLE)
    started: list[Path] = []
    monkeypatch.setattr(desktop, "engine_here", lambda port: False)
    monkeypatch.setattr(desktop, "open_while_starting", lambda port, **kw: False)
    monkeypatch.setattr(desktop, "start_engine", started.append)
    assert desktop.open_osxp_here(program.log) is True
    assert program.done == [("open", window_module.PLACE_NAME)]
    given = program.opened[0]
    assert given["title"] == desktop.APP_NAME and given["given_url"].endswith(":8641/")
    given["on_shown"]()
    assert started == [program.log]  # its engine, once the window is up
    assert desktop.open_osxp_here(program.log) is True  # up: in front, not a second one
    assert program.done[-1] == ("front", window_module.PLACE_NAME) and len(program.opened) == 1


def test_build_tiles_opens_osxp_here_before_anywhere_else(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(app_start, "_other_window", lambda start, aside: started.append(start))
    monkeypatch.setattr(app_start, "_osxp_to_front", lambda port: False)
    monkeypatch.setattr(app_start._opened_here, "is_set", lambda: False)
    monkeypatch.setattr(desktop, "open_osxp_here", lambda log=None: True)
    assert app_start.open_osxp() == "here" and started == []
    monkeypatch.setattr(desktop, "open_osxp_here", lambda log=None: False)
    assert app_start.open_osxp() == "started" and started == ["build"]  # no window in this one


def test_live_s_button_opens_live_beside_osxp_s_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OrthoStudio XP's Live button is in its engine, a program of its own: it asks OrthoStudio
    XP's window for Live's, which opens beside it in that program."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    from orthostudio.desktop import ENGINE_PORT

    asked: list[str] = []
    stop = app_start.osxp_window_listens(
        ENGINE_PORT, lambda: asked.append("front"), live=lambda: asked.append("live")
    )
    started: list[str] = []
    monkeypatch.setattr(app_start, "live_listening", lambda port=8642: False)
    monkeypatch.setattr(app_start, "_other_window", lambda start, aside: started.append(start))
    try:
        assert app_start.open_live() == "beside" and asked == ["live"] and started == []
    finally:
        stop()
    assert app_start.open_live() == "started" and started == ["live"]  # no window to ask


def test_live_s_window_opens_beside_osxp_s_live_set_up_the_first_time(
    program: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert web.live_window_here(program.log) == "none"  # no window shown: nothing to open beside
    program.up.add(window_module.PLACE_NAME)
    set_up = threading.Event()
    control = Control()

    def fake_set_up(port: int, *, front: Any) -> tuple:
        set_up.set()
        took = (("its code", 0.1), ("setting up", 0.1), ("its page", 0.1))
        return control, SimpleNamespace(should_exit=False), None, None, None, took

    monkeypatch.setattr(web, "_set_up", fake_set_up)
    monkeypatch.setattr(desktop, "open_while_starting", lambda port, **kw: False)
    assert web.live_window_here(program.log) == "opened"
    assert set_up.wait(5) and wait_for(lambda: web._here is not None and web._here.control)
    assert program.done == [("open", web.TITLE)] and program.opened[0]["title"] == web.TITLE
    assert wait_for(
        lambda: (
            program.log.exists()
            and "Live is ready, beside OrthoStudio XP's window" in program.log.read_text()
        )
    )
    assert web.live_window_here(program.log) == "front"  # up: in front
    # closed after its Quit, then opened again: Live set up once, the new window not closed at once
    program.up.discard(web.TITLE)
    control.closing = True
    set_up.clear()
    assert web.live_window_here(program.log) == "opened"
    assert not set_up.is_set() and control.closing is False and control.leaving_for is None
    assert program.opened[-1]["closes_when"]() is False


# -- quitting the app ------------------------------------------------------------------------------


def _live_here(state: str = IDLE) -> Control:
    live = web._Starting()
    live.control = Control(state)
    web._be_here(live)
    return live.control  # type: ignore[return-value]


def test_live_s_quit_takes_osxp_s_window_with_it(
    program: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quit in Live's page quits the app: OrthoStudio XP's window goes too, its engine asked to
    stop (the window closes once it is gone), or its page asking first while a build runs."""
    control = _live_here()
    program.up.update({web.TITLE, window_module.PLACE_NAME})
    closes_when = web._live_window(web._here)["closes_when"]
    stopped: list[int] = []
    monkeypatch.setattr(desktop, "_listening", lambda port: True)
    monkeypatch.setattr(desktop, "a_build_runs", lambda port=0: False)
    monkeypatch.setattr(desktop, "stop_the_engine", lambda port, **kw: stopped.append(port))
    assert closes_when() is False
    control.close()  # the page's yes
    assert closes_when() is True
    assert wait_for(lambda: stopped == [desktop.ENGINE_PORT])
    # a build running: OrthoStudio XP's page asks, in front
    monkeypatch.setattr(desktop, "a_build_runs", lambda port=0: True)
    asked: list[bool] = []
    monkeypatch.setattr(desktop, "may_quit", lambda: asked.append(True) or False)
    assert closes_when() is True and wait_for(lambda: asked == [True])
    # its engine gone already: its window closes at once
    monkeypatch.setattr(desktop, "_listening", lambda port: False)
    assert closes_when() is True
    assert wait_for(lambda: ("close", window_module.PLACE_NAME) in program.done)


def test_the_welcome_s_answer_does_not_quit_osxp(
    program: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = _live_here()
    program.up.update({web.TITLE, window_module.PLACE_NAME})
    monkeypatch.setattr(desktop, "stop_the_engine", lambda *a, **k: pytest.fail("quit OSXP"))
    monkeypatch.setattr(desktop, "may_quit", lambda: pytest.fail("asked OSXP to quit"))
    control.leaving_for = "build"  # Live's window goes, OrthoStudio XP's just opened stays
    assert web._live_window(web._here)["closes_when"]() is True
    time.sleep(0.1)
    assert program.done == []


def test_osxp_s_quit_takes_live_s_window_with_it(
    program: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quit in OrthoStudio XP's page stops its engine and closes its window: Live's goes too, at
    once when nothing streams, else after its page's own question, in front."""
    gone = [False]
    closes_when = desktop._then_live_too(lambda: gone[0])
    control = _live_here()
    program.up.update({web.TITLE, window_module.PLACE_NAME})
    assert closes_when() is False and not control.closing
    gone[0] = True
    assert closes_when() is True and wait_for(lambda: control.closing)  # it goes at once
    control.closing, control.state = False, STREAMING
    assert closes_when() is True
    assert wait_for(lambda: ("page", web.TITLE) in program.done) and not control.closing
    assert ("front", web.TITLE) in program.done


def test_cmd_q_asks_where_something_runs(program: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The app's Quit, its windows one program: OrthoStudio XP's page asks while a build runs,
    Live's while it streams, else the window up's; at once with no window up."""
    assert web.app_may_quit() is True  # nothing up
    osxp_asked: list[bool] = []
    monkeypatch.setattr(desktop, "may_quit", lambda: osxp_asked.append(True) or False)
    monkeypatch.setattr(desktop, "a_build_runs", lambda port=0: False)
    control = _live_here()
    program.up.update({web.TITLE, window_module.PLACE_NAME})
    assert web.app_may_quit() is False and osxp_asked == [True]  # nothing runs: OSXP's page asks
    control.state = STREAMING
    assert web.app_may_quit() is False and osxp_asked == [True]  # a stream: Live's page asks
    assert wait_for(lambda: ("page", web.TITLE) in program.done)
    monkeypatch.setattr(desktop, "a_build_runs", lambda port=0: True)
    assert web.app_may_quit() is False and osxp_asked == [True, True]  # a build: OSXP's first
    # Live's window alone, nothing streaming: its page asks
    program.up.discard(window_module.PLACE_NAME)
    control.state = IDLE
    program.done.clear()
    assert web.app_may_quit() is False
    assert wait_for(lambda: ("page", web.TITLE) in program.done)
    control.close()  # its yes: Live gone
    assert web.app_may_quit() is True


def test_live_set_up_beside_stops_with_the_app(program: Any) -> None:
    control = _live_here(STREAMING)
    ended: list[bool] = []
    web._here.end = lambda: ended.append(True)  # type: ignore[union-attr,method-assign]
    web.end_here()
    assert control.stopped == 1 and ended == [True] and web._here is None
    web.end_here()  # nothing here any more: nothing done
