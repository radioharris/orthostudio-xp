"""The app stops a while after its last page closed, unless a build runs or waits
(``orthostudio.api.presence``, ``POST /api/presence``, ``GET /api/engine``, ``osxp serve
--quit-when-closed``).

A user on Windows closed the page, found the engine still running where nothing showed it, and had
to end it in the Task Manager before the app would open again (2026-09-17).
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

import test_api_fakes as fakes
from orthostudio import __version__
from orthostudio.api import presence as presence_mod
from orthostudio.api import serve as serve_mod
from orthostudio.api.app import API_LEVEL, create_app
from orthostudio.api.jobs import JobManager
from orthostudio.api.presence import QUIT_AFTER_S, TICK_S, Presence, quit_when_closed
from test_api_fakes import FakeBuild, client_for

anyio_backend = fakes.anyio_backend
home = fakes.home


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _watch(
    clock: _Clock,
    presence: Presence,
    *,
    busy: Callable[[int], bool] = lambda tick: False,
    on_tick: Callable[[int], None] = lambda tick: None,
    step: Callable[[int], float] = lambda tick: TICK_S,
    ticks: int = 200,
) -> tuple[bool, list[float], list[str]]:
    """The watch on the fake ``clock``: each wait moves it ``step(tick)`` on, then ``on_tick``
    runs; the engine stops anyway after ``ticks`` waits. Returns what the watch returned, when
    ``stop`` was called, and what it said."""
    stops: list[float] = []
    said: list[str] = []
    count = [0]

    def wait(seconds: float) -> bool:
        assert seconds == TICK_S
        count[0] += 1
        if count[0] > ticks:
            return True
        clock.now += step(count[0])
        on_tick(count[0])
        return False

    ended = quit_when_closed(
        presence,
        lambda: busy(count[0]),
        lambda: stops.append(clock.now),
        wait=wait,
        clock=clock,
        say=said.append,
    )
    return ended, stops, said


def test_the_engine_stops_five_minutes_after_the_last_word_from_a_page() -> None:
    clock = _Clock()
    ended, stops, said = _watch(clock, Presence(clock))
    assert QUIT_AFTER_S == 300.0
    assert ended is True and stops == [1000.0 + QUIT_AFTER_S]
    assert said == [
        "No page of OrthoStudio XP has been open for 5 min and no build runs: OrthoStudio XP stops."
    ]


def test_an_open_page_keeps_it_running() -> None:
    """The page says so every 30 s (two ticks) until tick 40, then is closed."""
    clock = _Clock()
    presence = Presence(clock)

    def page(tick: int) -> None:
        if tick <= 40 and tick % 2 == 0:
            presence.seen()

    ended, stops, _ = _watch(clock, presence, on_tick=page)
    assert ended is True and stops == [1000.0 + 40 * TICK_S + QUIT_AFTER_S]


def test_a_build_keeps_it_running_and_the_wait_starts_when_it_ends() -> None:
    """No page for an hour, but a build runs or waits until tick 30."""
    clock = _Clock()
    ended, stops, _ = _watch(clock, Presence(clock), busy=lambda tick: tick <= 30)
    assert ended is True and stops == [1000.0 + 30 * TICK_S + QUIT_AFTER_S]


def test_a_computer_asleep_does_not_count_as_a_closed_page() -> None:
    """The page cannot speak while the computer sleeps: the wait starts again at the wake-up (on a
    system whose clock goes on during sleep)."""
    clock = _Clock()
    ended, stops, _ = _watch(
        clock, Presence(clock), step=lambda tick: 3600.0 if tick == 3 else TICK_S
    )
    woke = 1000.0 + 2 * TICK_S + 3600.0
    assert ended is True and stops == [woke + QUIT_AFTER_S]


def test_the_watch_ends_with_the_engine() -> None:
    clock = _Clock()
    ended, stops, said = _watch(clock, Presence(clock), ticks=5)
    assert ended is False and stops == [] and said == []


@pytest.fixture
def app(home: Path):  # type: ignore[no-untyped-def]
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=lambda specs: None)
    application = create_app(jobs=mgr, settings_path=home / "config.toml")
    yield application
    mgr.close()


@pytest.mark.anyio
async def test_engine_says_at_once_which_osxp_serves_the_port(app) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app) as c:
        r = await c.get("/api/engine")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["version"] == __version__ and doc["api_level"] == API_LEVEL
    assert doc["engine"]["root"] == str(serve_mod.package_root())
    assert isinstance(doc["engine"]["pid"], int)
    assert doc["active_job"] is None and doc["can_quit"] is False


@pytest.mark.anyio
async def test_a_page_saying_it_is_open_starts_the_wait_again(app) -> None:  # type: ignore[no-untyped-def]
    clock = _Clock()
    app.state.orthostudio["presence"] = Presence(clock)
    clock.now += 250.0
    assert app.state.orthostudio["presence"].idle_s() == 250.0
    async with client_for(app) as c:
        r = await c.post("/api/presence", json={})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert app.state.orthostudio["presence"].idle_s() == 0.0


def test_serve_watches_the_pages_only_when_asked(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--quit-when-closed`` starts the watch on the engine's presence and jobs; a plain
    ``osxp serve`` in a terminal does not stop by itself."""
    import uvicorn

    monkeypatch.setattr(uvicorn.Server, "run", lambda self: None)
    watched: list[tuple[object, bool, bool]] = []
    called = threading.Event()

    def fake_watch(presence, busy, stop, *, wait):  # type: ignore[no-untyped-def]
        watched.append((presence, busy(), wait(5.0)))
        called.set()
        return False

    monkeypatch.setattr(presence_mod, "quit_when_closed", fake_watch)
    with socket.socket() as probe:
        probe.bind((serve_mod.HOST, 0))
        port = probe.getsockname()[1]
    serve_mod.serve(port=port, open_browser=False)
    assert not called.wait(0.3) and watched == []
    serve_mod.serve(port=port, open_browser=False, quit_when_closed=True)
    assert called.wait(5.0)
    presence, busy, stopped = watched[0]
    assert isinstance(presence, Presence) and busy is False
    assert stopped is True  # the watch's wait ends once the server has stopped


def test_the_command_and_the_app_pass_the_option(monkeypatch: pytest.MonkeyPatch) -> None:
    from orthostudio import desktop
    from orthostudio.cli import app as cli_app

    seen: list[dict] = []

    def fake_main(**kwargs: object) -> int:
        seen.append(kwargs)
        return 0

    monkeypatch.setattr(serve_mod, "main", fake_main)
    runner = CliRunner()
    assert runner.invoke(cli_app, ["serve", "--no-open", "--quit-when-closed"]).exit_code == 0
    assert runner.invoke(cli_app, ["serve", "--no-open"]).exit_code == 0
    assert [kw["quit_when_closed"] for kw in seen] == [True, False]
    assert "--quit-when-closed" in desktop.DEFAULT_ARGS
