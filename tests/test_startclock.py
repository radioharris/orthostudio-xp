# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""How long a start took, and where: what ``serve.log`` says of it (``orthostudio.startclock``,
``docs/specs/packaging.md`` 4).

The app took more than 45 s to open on a cloud PC after an install, and the log could not say
where the time went (a Shadow PC, 2026-10-02): nothing in it had a time between the window's line
and uvicorn's.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import orthostudio.api.app as api_app
import test_api_fakes as fakes
from orthostudio import STARTED_AT
from orthostudio.api import serve as serve_mod
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.doctor import run_doctor
from orthostudio.logs import ROOT_LOGGER
from orthostudio.startclock import (
    LAUNCHED_ENV,
    LONGEST_S,
    from_filetime,
    launched_at,
    parts,
    process_started_at,
    span,
)
from test_api_fakes import FakeBuild, client_for

anyio_backend = fakes.anyio_backend
home = fakes.home
xplane = fakes.xplane


@pytest.fixture
def said() -> Iterator[list[str]]:
    """What the engine's loggers say, whatever an earlier test set up on them."""
    log = logging.getLogger(ROOT_LOGGER)
    before = (list(log.handlers), log.level, log.propagate)
    lines: list[str] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    log.addHandler(Keep(logging.INFO))
    log.setLevel(logging.INFO)
    yield lines
    log.handlers, log.level, log.propagate = list(before[0]), before[1], before[2]


@pytest.fixture
def app(home: Path):  # type: ignore[no-untyped-def]
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=lambda specs: None)
    application = create_app(jobs=mgr, settings_path=home / "config.toml")
    yield application
    mgr.close()


# -- the clock ---------------------------------------------------------------------------------


def test_a_span_is_left_out_when_a_clock_moved() -> None:
    assert span(10.0, 12.5) == 2.5
    assert span(None, 1.0) is None and span(1.0, None) is None
    assert span(12.0, 10.0) is None  # backwards: the clock was set back meanwhile
    assert span(0.0, LONGEST_S + 1.0) is None


def test_only_the_parts_whose_time_is_known_are_said() -> None:
    assert parts(("Python", 1.24), ("loading", None), ("setting up", 0.31)) == (
        "Python 1.2 s, setting up 0.3 s"
    )
    assert parts(("Python", None)) == ""


def test_a_windows_filetime_is_read_on_the_clock_of_time_time() -> None:
    assert from_filetime(116_444_736_000_000_000) == 0.0  # 1970-01-01, in 100 ns since 1601
    assert from_filetime(116_444_736_000_000_000 + 15_000_000) == 1.5


def test_the_process_start_is_known_on_windows_alone() -> None:
    when = process_started_at()
    if sys.platform == "win32":
        assert when is not None and span(when, STARTED_AT) is not None
    else:
        assert when is None


def test_the_engine_counts_from_the_time_its_window_handed_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LAUNCHED_ENV, repr(STARTED_AT - 1.5))
    assert launched_at() == STARTED_AT - 1.5
    assert LAUNCHED_ENV not in os.environ  # what the engine starts does not inherit it
    assert launched_at() is None


@pytest.mark.parametrize("value", ["soon", "inf", "nan", "60", "-60"])
def test_a_time_handed_that_cannot_be_the_engines_start_is_left_out(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    times = {"60": STARTED_AT + 60, "-60": STARTED_AT - 2 * LONGEST_S}
    monkeypatch.setenv(LAUNCHED_ENV, repr(times[value]) if value in times else value)
    assert launched_at() is None


# -- the window -------------------------------------------------------------------------------


def test_the_window_says_python_then_its_own_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    from orthostudio import desktop

    monkeypatch.setattr(desktop, "process_started_at", lambda: STARTED_AT - 0.8)
    assert desktop.window_opens(STARTED_AT + 2.5) == (
        "its window opens: Python 0.8 s, loading the window 2.5 s"
    )
    # Windows alone says when a process was created
    monkeypatch.setattr(desktop, "process_started_at", lambda: None)
    assert desktop.window_opens(STARTED_AT + 2.5) == "its window opens: loading the window 2.5 s"
    assert desktop.window_opens(STARTED_AT - 5.0) == "its window opens"  # a clock set back


def test_the_window_says_its_time_before_it_starts_its_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orthostudio import desktop

    log = tmp_path / "serve.log"
    monkeypatch.setattr(desktop, "engine_here", lambda *a, **k: False)
    monkeypatch.setattr(desktop, "open_while_starting", lambda port, *, log, browser: False)

    def start_engine(log: Path) -> None:
        with log.open("a", encoding="utf-8") as out:
            out.write("engine\n")

    monkeypatch.setattr(desktop, "start_engine", start_engine)

    def fake_show(url: str, *, title: str, on_shown: Any = None, **rest: object) -> None:
        on_shown()

    assert desktop.in_a_window(log, show=fake_show) is True
    lines = log.read_text(encoding="utf-8").splitlines()
    assert re.fullmatch(
        r"--- \S+ \S+ OrthoStudio XP: its window opens: (Python \d+\.\d s, )?"
        r"loading the window \d+\.\d s",
        lines[0],
    ), lines
    assert lines[1] == "engine"


def test_a_line_that_cannot_be_written_does_not_keep_the_engine_from_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orthostudio import desktop

    started: list[str] = []
    monkeypatch.setattr(desktop, "engine_here", lambda *a, **k: False)
    monkeypatch.setattr(desktop, "open_while_starting", lambda port, *, log, browser: False)
    monkeypatch.setattr(desktop, "start_engine", lambda log: started.append("engine"))

    def broken(now: float | None = None) -> str:
        raise OSError("the disk is full")

    monkeypatch.setattr(desktop, "window_opens", broken)

    def fake_show(url: str, *, title: str, on_shown: Any = None, **rest: object) -> None:
        on_shown()

    assert desktop.in_a_window(tmp_path / "serve.log", show=fake_show) is True
    assert started == ["engine"]


# -- the engine -------------------------------------------------------------------------------


def test_the_engine_says_its_parts_from_the_window_that_started_it() -> None:
    line = serve_mod.listening_line(
        STARTED_AT - 1.2, STARTED_AT + 24.3, STARTED_AT + 24.7, STARTED_AT + 24.9
    )
    assert line == (
        "listening, 26.1 s after the window started it: Python 1.2 s, loading 24.3 s, "
        "setting up 0.4 s, opening its port 0.2 s"
    )
    # started otherwise (a terminal, the browser's start): from its own code
    line = serve_mod.listening_line(None, STARTED_AT + 2.0, STARTED_AT + 2.4, STARTED_AT + 2.5)
    assert line == (
        "listening, 2.5 s after its code started: loading 2.0 s, setting up 0.4 s, "
        "opening its port 0.1 s"
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind((serve_mod.HOST, 0))
        return int(probe.getsockname()[1])


def _post(port: int, path: str, body: dict[str, Any]) -> int:
    request = urllib.request.Request(
        f"http://{serve_mod.HOST}:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as answer:
        return int(answer.status)


def test_the_engine_says_when_its_port_opened_and_when_its_page_came(
    home: Path, said: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real engine on a free port, started as the window starts it."""
    monkeypatch.setenv(LAUNCHED_ENV, repr(STARTED_AT - 0.5))
    port = _free_port()
    engine = threading.Thread(
        target=serve_mod.serve, kwargs={"port": port, "open_browser": False}, daemon=True
    )
    engine.start()
    try:
        deadline = time.monotonic() + 60
        while not any(line.startswith("listening") for line in said):
            assert engine.is_alive() and time.monotonic() < deadline, said
            time.sleep(0.05)
        (line,) = [line for line in said if line.startswith("listening")]
        assert re.fullmatch(
            r"listening, \d+\.\d s after the window started it: Python 0\.5 s, loading \d+\.\d s, "
            r"setting up \d+\.\d s, opening its port \d+\.\d s",
            line,
        ), line
        with socket.create_connection((serve_mod.HOST, port), timeout=5):
            pass  # open when it says so
        assert _post(port, "/api/presence", {}) == 200
        assert _post(port, "/api/presence", {}) == 200
        came = [line for line in said if line.startswith("its page is open")]
        assert len(came) == 1 and re.fullmatch(
            r"its page is open, \d+\.\d s after its port opened", came[0]
        ), said
    finally:
        with contextlib.suppress(OSError):
            _post(port, "/api/quit", {"force": False})
        engine.join(timeout=30)
    assert not engine.is_alive()


# -- what the page waits for ------------------------------------------------------------------


async def _answer(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


async def _sent(message: Any) -> None:
    return None


@pytest.mark.anyio
async def test_an_answer_slower_than_a_second_is_named_with_its_time(said: list[str]) -> None:
    ticks = iter([100.0, 102.5, 200.0, 200.4])
    timed = api_app._SlowAnswers(_answer, clock=lambda: next(ticks))
    await timed({"type": "http", "method": "GET", "path": "/api/status"}, None, _sent)
    await timed({"type": "http", "method": "GET", "path": "/api/jobs"}, None, _sent)
    assert said == ["GET /api/status answered in 2.5 s"]  # the jobs came in 0.4 s


@pytest.mark.anyio
async def test_the_base_maps_tiles_and_the_page_files_are_not_timed(said: list[str]) -> None:
    def clock() -> float:
        raise AssertionError("timed")

    timed = api_app._SlowAnswers(_answer, clock=clock)
    for path in ("/api/map/OSM/3/4/2", "/static/app.js", "/"):
        await timed({"type": "http", "method": "GET", "path": path}, None, _sent)
    await timed({"type": "lifespan"}, None, _sent)
    assert said == []


@pytest.mark.anyio
async def test_the_engines_answers_are_timed(  # type: ignore[no-untyped-def]
    app, said: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_app, "SLOW_S", -1.0)  # every answer is slow
    async with client_for(app) as c:
        assert (await c.get("/api/engine")).status_code == 200
    assert re.fullmatch(r"GET /api/engine answered in \d+\.\d s", said[-1]), said


@pytest.mark.anyio
async def test_the_status_names_its_slow_parts_and_the_doctors_slow_checks(  # type: ignore[no-untyped-def]
    app, xplane: Path, said: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_app, "SLOW_S", -1.0)  # every part is slow
    async with client_for(app) as c:
        doc = (await c.get("/api/status")).json()
    parts_said = {m.group(1) for m in map(re.compile(r"the status's (.+) took ").match, said) if m}
    assert parts_said == {
        "X-Plane folder",
        "checks",
        "X-Plane running",
        "other X-Planes",
        "packs of their own",
        "data folder",
        "library count",
    }
    checks = {
        m.group(1) for m in map(re.compile(r"the doctor's (.+) check took ").match, said) if m
    }
    assert checks == {c["name"] for c in doc["doctor"]}


@pytest.mark.anyio
async def test_a_fast_answer_says_nothing(app, said: list[str]) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app) as c:
        assert (await c.get("/api/engine")).status_code == 200  # well within the second
    assert said == []


def test_the_doctor_times_each_check_and_its_json_is_as_it_was(home: Path, tmp_path: Path) -> None:
    report = run_doctor(
        offline=True, xplane=tmp_path, store_root=tmp_path / "store", chunks_root=tmp_path / "c"
    )
    assert list(report.took_s) == [c.name for c in report.checks]
    assert all(took >= 0.0 for took in report.took_s.values())
    assert set(report.to_dict()) == {"schema", "osxp", "ok", "checks"}
    assert all("took_s" not in c for c in report.to_dict()["checks"])
