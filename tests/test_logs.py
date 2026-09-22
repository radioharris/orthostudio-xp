"""Where OrthoStudio XP's own log lines go (``orthostudio.logs``).

The engine's output is appended to ``serve.log``, but nothing configured Python's logging, so the
file held uvicorn's connection lines and nothing of a build: a user on Linux whose build stopped on
the Data stage found nothing to send (2026-09-22).
"""

from __future__ import annotations

import io
import logging

import pytest

from orthostudio.logs import HANDLER_MARK, LOG_LEVEL_ENV, ROOT_LOGGER, setup_logging


@pytest.fixture(autouse=True)
def _own_logger() -> object:
    """The package's logger as it was: these tests add and remove a handler of their own."""
    log = logging.getLogger(ROOT_LOGGER)
    before = (list(log.handlers), log.level, log.propagate)
    log.handlers = []  # a serve() elsewhere in the suite leaves its own handler behind
    yield
    log.handlers, log.level, log.propagate = list(before[0]), before[1], before[2]


def test_the_engines_own_lines_are_written_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    out = io.StringIO()
    setup_logging("info", stream=out)
    setup_logging("info", stream=io.StringIO())  # called again: one handler, nothing twice
    log = logging.getLogger("orthostudio.demo")
    log.info("the data stage is downloading")
    log.debug("not at INFO")
    written = out.getvalue()
    assert written.count("the data stage is downloading") == 1
    assert "orthostudio.demo" in written and "INFO" in written and "not at INFO" not in written
    ours = [h for h in logging.getLogger(ROOT_LOGGER).handlers if getattr(h, HANDLER_MARK, False)]
    assert len(ours) == 1  # pytest attaches handlers of its own to this logger
    assert logging.getLogger(ROOT_LOGGER).propagate is False  # uvicorn writes its own lines


def test_the_level_of_the_environment_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LOG_LEVEL_ENV, "warning")
    out = io.StringIO()
    setup_logging("info", stream=out)
    logging.getLogger("orthostudio.demo").info("quiet")
    logging.getLogger("orthostudio.demo").warning("loud")
    assert "quiet" not in out.getvalue() and "loud" in out.getvalue()
    monkeypatch.setenv(LOG_LEVEL_ENV, "nonsense")  # an unknown name is INFO
    setup_logging(stream=out)
    logging.getLogger("orthostudio.demo").info("back")
    assert "back" in out.getvalue()


def test_the_engine_sets_it_up_before_serving() -> None:
    """``serve`` calls it, so that everything the engine does reaches ``serve.log``."""
    from pathlib import Path

    source = Path("src/orthostudio/api/serve.py").read_text(encoding="utf-8")
    assert "from orthostudio.logs import setup_logging" in source
    assert "setup_logging(log_level)" in source
