# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""``python -m orthostudio.desktop``, what the installers start (``docs/specs/packaging.md`` 4)."""

from __future__ import annotations

import json
import socket
import sys
import time
import urllib.request
from pathlib import Path, PurePosixPath

import pytest

import orthostudio.cli
from orthostudio import desktop
from orthostudio.desktop import (
    APP_NAME,
    DEFAULT_ARGS,
    ENGINE_PORT,
    LOG_NAME,
    in_a_window,
    log_path,
    main,
    open_running,
    open_while_starting,
    opening_page,
)
from orthostudio.home import osxp_home


def test_the_log_is_in_the_platform_log_folder() -> None:
    """On Linux the log sits beside everything else of ours, under ``$OSXP_HOME``, because the
    platform's own answer is one nobody finds (a user, 2026-09-23); macOS and Windows each have
    a place people know, named after the app. The test asked for the app's name everywhere and
    so failed on Linux from that day, unseen until the format check stopped running first
    (CI, 2026-09-24)."""
    path = log_path()
    assert path.name == LOG_NAME
    if sys.platform.startswith("linux"):
        assert path.parent == osxp_home() / "log"
    else:
        assert APP_NAME in path.parts


def test_the_output_goes_to_the_log_and_the_streams_come_back(tmp_path: Path) -> None:
    before = sys.stdout, sys.stderr
    log = tmp_path / "logs" / "serve.log"

    code = main(["--help"], log=log)

    assert code == 0
    assert (sys.stdout, sys.stderr) == before
    text = log.read_text(encoding="utf-8")
    assert f"{APP_NAME}: orthostudio --help" in text and "serve" in text


def test_the_exit_code_of_the_command_is_returned(tmp_path: Path) -> None:
    assert main(["uninstall", "Genève"], log=tmp_path / "serve.log") == 1
    assert "CFG_LATLON_INVALID" in (tmp_path / "serve.log").read_text(encoding="utf-8")


def test_without_arguments_it_serves_and_opens_the_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def fake_app(*, args: list[str], prog_name: str) -> None:
        seen.append(args)

    monkeypatch.setattr(orthostudio.cli, "app", fake_app)
    shown: list[tuple[int, Path]] = []

    def running_already(port: int, *, log: Path) -> bool:
        shown.append((port, log))
        return False  # an engine listens: the usual start opens it

    def not_this_app(port: int) -> bool:
        return False  # nothing of this installation runs: the start goes on

    log = tmp_path / "serve.log"
    assert main([], log=log, opening=running_already, running=not_this_app, window=False) == 0
    assert seen == [list(DEFAULT_ARGS)] == [["serve", "--open", "--quit-when-closed"]]
    assert shown == [(ENGINE_PORT, log)]
    # the opening page shown, the engine does not open the page a second time
    assert (
        main([], log=log, opening=lambda port, *, log: True, running=not_this_app, window=False)
        == 0
    )
    assert seen[-1] == ["serve", "--no-open", "--quit-when-closed"]
    # a command run by hand shows no opening page
    assert main(["--help"], log=log, opening=running_already, running=not_this_app) == 0
    assert len(shown) == 1
    # this OrthoStudio XP runs already: its page opened, nothing starts, the log says so
    runs = len(seen)
    assert main([], log=log, opening=running_already, running=lambda port: True, window=False) == 0
    assert len(seen) == runs and len(shown) == 1
    assert log.read_text(encoding="utf-8").endswith(
        f"{APP_NAME}: already running, its page opened\n"
    )


class _Engine:
    """``GET /api/engine`` of a running OrthoStudio XP, as ``doc`` says, on a free port."""

    def __init__(self, doc: dict) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = json.dumps(doc).encode()
                self.send_response(200 if self.path == "/api/engine" else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.mark.parametrize(
    ("version", "root", "code", "opens"),
    [
        ("same", "same", "same", True),
        ("0.0.1", "same", "same", False),
        ("same", "/elsewhere/orthostudio", "same", False),
        ("same", "same", "started-before", False),
    ],
)
def test_a_second_launch_opens_this_apps_page_at_once(
    version: str, root: str, code: str, opens: bool
) -> None:
    """A user on Windows closed the browser, opened the app again and saw nothing come
    (2026-09-17): the page of this installation, at this version, opens before the engine's long
    imports. Another version or installation, or this one started before its files changed (a
    checkout's app opened again after a change, 2026-09-25), is left to the usual start, which asks
    it to stop."""
    import orthostudio
    import orthostudio.desktop
    from orthostudio.codemark import code_mark

    here = str(Path(orthostudio.desktop.__file__).resolve().parent)
    engine = _Engine(
        {
            "version": orthostudio.__version__ if version == "same" else version,
            "api_level": 14,
            "engine": {
                "root": here if root == "same" else root,
                "pid": 1,
                "code": code_mark() if code == "same" else code,
            },
        }
    )
    opened: list[str] = []
    try:
        assert open_running(engine.port, browser=opened.append) is opens
    finally:
        engine.stop()
    assert opened == ([f"http://127.0.0.1:{engine.port}/"] if opens else [])
    assert open_running(_free_port(), browser=opened.append) is False  # nothing listens


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_the_opening_page_shows_at_once_and_waits_for_the_engine(tmp_path: Path) -> None:
    """A user saw nothing for a long while after opening the app on Windows (2026-09-15)."""
    from orthostudio.api.serve import DEFAULT_PORT

    assert ENGINE_PORT == DEFAULT_PORT
    port = _free_port()  # nothing listens there: the engine is not started yet
    opened: list[str] = []
    log = tmp_path / "Logs" / "serve.log"
    assert open_while_starting(port, log=log, browser=opened.append) is True
    (url,) = opened
    assert url.startswith("http://127.0.0.1:") and not url.endswith(f":{port}/")
    with urllib.request.urlopen(url, timeout=5) as answer:
        page = answer.read().decode("utf-8")
        assert answer.headers["Cache-Control"] == "no-store"
    assert "Opening OrthoStudio XP" in page and "Ouverture d'OrthoStudio XP" in page
    assert f'const ENGINE = "http://127.0.0.1:{port}/";' in page
    assert json.dumps(str(log)) in page and "location.replace(ENGINE)" in page
    assert 'mode: "no-cors"' in page


def test_no_opening_page_when_an_engine_already_listens() -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = int(busy.getsockname()[1])
        opened: list[str] = []
        assert open_while_starting(port, log=Path("serve.log"), browser=opened.append) is False
        assert opened == []


def test_the_opening_page_escapes_the_log_path() -> None:
    # a POSIX path on every system: Windows would turn its slashes into backslashes
    log = PurePosixPath("/Users/a</script>b/serve.log")
    page = opening_page(8641, log).decode("utf-8")  # type: ignore[arg-type]
    assert "</script>b" not in page and "<\\/script>b" in page


# -- the window of its own ---------------------------------------------------------------------


def test_the_engine_starts_only_once_the_window_is_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[str] = []
    monkeypatch.setattr(desktop, "engine_here", lambda *a, **k: False)
    monkeypatch.setattr(desktop, "start_engine", lambda log: started.append("engine"))
    monkeypatch.setattr(desktop, "open_while_starting", lambda port, *, log, browser: False)
    seen: dict[str, object] = {}

    def fake_show(url: str, *, title: str, on_shown=None, **rest: object) -> None:
        seen["url"], seen["title"] = url, title
        assert started == []  # nothing runs before the window is on screen
        if on_shown:
            on_shown()
        seen["while_open"] = list(started)

    log = tmp_path / "serve.log"
    assert in_a_window(log, show=fake_show) is True
    assert seen["while_open"] == ["engine"] and seen["title"] == APP_NAME
    assert str(ENGINE_PORT) in str(seen["url"])
    assert "its window closed" in log.read_text(encoding="utf-8")


def test_a_window_is_not_asked_for_twice_when_the_engine_already_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop, "engine_here", lambda *a, **k: True)
    monkeypatch.setattr(
        desktop, "start_engine", lambda log: pytest.fail("the engine was started twice")
    )
    asked: list[object] = []

    def fake_show(url: str, *, title: str, on_shown=None, **rest: object) -> None:
        asked.append(on_shown)

    assert in_a_window(tmp_path / "serve.log", show=fake_show) is True
    assert asked == [None]  # nothing to start behind the window


def test_a_system_without_a_window_is_left_as_it_was_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop, "engine_here", lambda *a, **k: False)
    monkeypatch.setattr(
        desktop, "start_engine", lambda log: pytest.fail("the engine was started with no window")
    )
    monkeypatch.setattr(desktop, "open_while_starting", lambda port, *, log, browser: False)

    def refuses(url: str, *, title: str, on_shown=None, **rest: object) -> None:
        raise RuntimeError("no web view here")

    log = tmp_path / "serve.log"
    assert in_a_window(log, show=refuses) is False
    assert "could not be shown" in log.read_text(encoding="utf-8")


def test_the_app_started_in_a_window_runs_no_command_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orthostudio.cli, "app", lambda **kw: pytest.fail("the engine ran in the window's process")
    )
    monkeypatch.setattr(desktop, "engine_here", lambda *a, **k: True)

    def fake_show(url: str, *, title: str, on_shown=None, **rest: object) -> None:
        return None

    assert main([], log=tmp_path / "serve.log", window=fake_show) == 0


def test_the_window_closes_only_once_its_engine_has_answered_and_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter([False, False, True, True, False, False])
    monkeypatch.setattr(desktop, "_listening", lambda port: next(answers))
    gone = desktop.time_to_close(ENGINE_PORT)
    # coming up: the window stays, or a slow start would take it away under the opening page
    assert gone() is False and gone() is False
    assert gone() is False and gone() is False  # it answers: the window stays
    assert gone() is True  # Quit: nothing behind the page, the window closes
    assert gone() is True


def test_quitting_the_app_asks_the_page_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cmd+Q, the app's own Quit and the Dock's Quit must not take the app away from under a
    build without a word. The page already has the question, with what a build and a queue are
    worth in it, so the window comes back in front and its Quit button is pressed."""
    from orthostudio import window

    monkeypatch.setattr(desktop, "_listening", lambda port: True)
    front, asked = [], []
    monkeypatch.setattr(window, "to_the_front", lambda: front.append(True))
    monkeypatch.setattr(window, "ask_the_page_to_quit", lambda: asked.append(True))

    assert desktop.may_quit() is False  # the page answers, not this
    assert front == [True]
    for _ in range(50):
        if asked:
            break
        time.sleep(0.02)
    assert asked == [True]


def test_quitting_with_no_engine_left_goes_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from orthostudio import window

    monkeypatch.setattr(desktop, "_listening", lambda port: False)
    monkeypatch.setattr(window, "to_the_front", lambda: pytest.fail("nothing to ask about"))
    assert desktop.may_quit() is True


def test_the_close_button_puts_the_window_away_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    from orthostudio import window

    monkeypatch.setattr(window, "puts_away_on_close", lambda: True)
    put_away: list[bool] = []
    monkeypatch.setattr(window, "away", lambda: put_away.append(True))
    answer = desktop.on_close()
    assert answer is not None
    assert answer() is False and put_away == [True]  # the window stays, out of sight


def test_the_close_button_quits_without_a_word_when_nothing_is_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing a window on Windows means quitting the app, so the engine goes with it: one left
    working behind a window that is gone is what this window was made to end (a user, 2026-09-20).
    """
    from orthostudio import window

    monkeypatch.setattr(window, "puts_away_on_close", lambda: False)
    monkeypatch.setattr(desktop, "a_build_runs", lambda *a, **k: False)
    monkeypatch.setattr(window, "ask", lambda *a: pytest.fail("asked a question nobody needed"))
    stopped: list[bool] = []
    monkeypatch.setattr(
        desktop, "stop_the_engine", lambda *a, **k: stopped.append(k.get("force", False))
    )
    answer = desktop.on_close()
    assert answer is not None and answer() is True
    assert stopped == [False]  # nothing is building: it is asked to stop, not forced


def test_the_close_button_asks_when_a_build_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    from orthostudio import window

    monkeypatch.setattr(window, "puts_away_on_close", lambda: False)
    monkeypatch.setattr(desktop, "a_build_runs", lambda *a, **k: True)
    asked: list[tuple[str, str]] = []
    closed: list[bool] = []
    monkeypatch.setattr(window, "ask", lambda t, m: (asked.append((t, m)), True)[1])
    monkeypatch.setattr(window, "close_now", lambda: closed.append(True))
    forced: list[bool] = []
    monkeypatch.setattr(
        desktop, "stop_the_engine", lambda *a, **k: forced.append(k.get("force", False))
    )
    answer = desktop.on_close()
    assert answer is not None
    # no meanwhile: the box cannot be drawn by the thread waiting for this answer
    assert answer() is False
    for _ in range(50):
        if closed:
            break
        time.sleep(0.02)
    assert asked and asked[0][0] == APP_NAME and "build is running" in asked[0][1]
    assert "carries on from there" in asked[0][1]  # what it costs, and what it does not
    assert closed == [True]
    assert forced == [True]  # a build is stopped only when the user has said so

    # closing comes back through here, and the question must not be asked a second time: it was,
    # for ever, and OK did nothing but show the box again (a user, 2026-09-20)
    assert answer() is True
    assert len(asked) == 1


def test_the_close_button_asks_again_on_a_later_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer is kept for the closing it was given for, not for the life of the window: a
    window brought back and closed again asks again."""
    from orthostudio import window

    monkeypatch.setattr(window, "puts_away_on_close", lambda: False)
    monkeypatch.setattr(desktop, "a_build_runs", lambda *a, **k: True)
    monkeypatch.setattr(window, "ask", lambda t, m: False)  # the user says no
    monkeypatch.setattr(window, "close_now", lambda: pytest.fail("closed on a no"))
    monkeypatch.setattr(
        desktop, "stop_the_engine", lambda *a, **k: pytest.fail("stopped a build on a no")
    )
    answer = desktop.on_close()
    assert answer is not None
    assert answer() is False and answer() is False  # a no never lets it through


def test_a_log_that_has_grown_too_big_is_set_aside_and_one_is_kept(tmp_path: Path) -> None:
    """It is appended to for ever, and it holds every stage of every build since 0.1.14: a user
    asked to send it was being asked for a file without an end (found in review, 2026-09-23)."""
    from orthostudio.desktop import LOG_MAX_BYTES, roll_log

    log = tmp_path / "serve.log"
    log.write_text("one line\n", encoding="utf-8")
    roll_log(log)
    assert log.read_text(encoding="utf-8") == "one line\n", "a small log is left alone"
    assert not log.with_suffix(".log.1").exists()

    log.write_text("x" * (LOG_MAX_BYTES + 1), encoding="utf-8")
    roll_log(log)
    assert not log.exists(), "the run that follows starts a fresh one"
    assert log.with_name("serve.log.1").stat().st_size == LOG_MAX_BYTES + 1

    # a second roll keeps one previous, not two
    log.write_text("y" * (LOG_MAX_BYTES + 1), encoding="utf-8")
    roll_log(log)
    assert log.with_name("serve.log.1").read_text(encoding="utf-8")[0] == "y"
    assert not log.with_name("serve.log.1.1").exists()

    # and a log it cannot move must never stop the app from starting
    roll_log(tmp_path / "not-there.log")


def test_the_app_rolls_its_log_when_it_starts(tmp_path: Path) -> None:
    from orthostudio.desktop import LOG_MAX_BYTES, main

    log = tmp_path / "serve.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("z" * (LOG_MAX_BYTES + 1), encoding="utf-8")
    main(["uninstall", "Genève"], log=log)  # any command: it is the start that rolls
    assert log.with_name("serve.log.1").stat().st_size == LOG_MAX_BYTES + 1
    assert "CFG_LATLON_INVALID" in log.read_text(encoding="utf-8")
