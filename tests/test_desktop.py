"""``python -m orthostudio.desktop``, what the installers start (``docs/specs/packaging.md`` 4)."""

from __future__ import annotations

import json
import socket
import sys
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


def test_the_log_is_in_the_platform_log_folder() -> None:
    path = log_path()
    assert path.name == LOG_NAME and APP_NAME in path.parts


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
    ("version", "root", "opens"),
    [("same", "same", True), ("0.0.1", "same", False), ("same", "/elsewhere/orthostudio", False)],
)
def test_a_second_launch_opens_this_apps_page_at_once(version: str, root: str, opens: bool) -> None:
    """A user on Windows closed the browser, opened the app again and saw nothing come
    (2026-09-17): the page of this installation, at this version, opens before the engine's long
    imports. Another version or installation is left to the usual start, which asks it to stop."""
    import orthostudio
    import orthostudio.desktop

    here = str(Path(orthostudio.desktop.__file__).resolve().parent)
    engine = _Engine(
        {
            "version": orthostudio.__version__ if version == "same" else version,
            "api_level": 14,
            "engine": {"root": here if root == "same" else root, "pid": 1},
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
    gone = desktop.engine_stopped(ENGINE_PORT)
    # coming up: the window stays, or a slow start would take it away under the opening page
    assert gone() is False and gone() is False
    assert gone() is False and gone() is False  # it answers: the window stays
    assert gone() is True  # Quit: nothing behind the page, the window closes
    assert gone() is True
