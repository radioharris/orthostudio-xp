"""``python -m orthostudio.desktop``, what the installers start (``docs/specs/packaging.md`` 4)."""

from __future__ import annotations

import json
import socket
import sys
import urllib.request
from pathlib import Path, PurePosixPath

import pytest

import orthostudio.cli
from orthostudio.desktop import (
    APP_NAME,
    DEFAULT_ARGS,
    ENGINE_PORT,
    LOG_NAME,
    log_path,
    main,
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

    assert main([], log=tmp_path / "serve.log", opening=running_already) == 0
    assert seen == [list(DEFAULT_ARGS)] == [["serve", "--open"]]
    assert shown == [(ENGINE_PORT, tmp_path / "serve.log")]
    # the opening page shown, the engine does not open the page a second time
    assert main([], log=tmp_path / "serve.log", opening=lambda port, *, log: True) == 0
    assert seen[-1] == ["serve", "--no-open"]
    # a command run by hand shows no opening page
    assert main(["--help"], log=tmp_path / "serve.log", opening=running_already) == 0
    assert len(shown) == 1


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
