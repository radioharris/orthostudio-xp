# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Where OrthoStudio XP's own log lines go.

The app's launcher appends everything the engine writes to ``serve.log`` (``desktop.log_path``),
but nothing ever configured Python's logging: uvicorn set up its own loggers, so the file held its
connection lines and nothing else. A user on Linux whose build stopped on the Data stage had no
trace of it at all (2026-09-22). One handler on the ``orthostudio`` logger writes what the engine
does, at INFO, to the output the launcher sends to that file.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import IO, Any

__all__ = ["HANDLER_MARK", "LOG_LEVEL_ENV", "ROOT_LOGGER", "setup_logging"]

ROOT_LOGGER = "orthostudio"
"""The logger every module of the package logs under (``orthostudio.<module>``)."""

LOG_LEVEL_ENV = "OSXP_LOG_LEVEL"
"""Its level, over the caller's: ``debug`` while looking for something, ``warning`` for quiet."""

HANDLER_MARK = "_osxp_handler"
"""What tells our handler from another's (pytest attaches its own to this logger)."""
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _level(wanted: str | int | None) -> int:
    if isinstance(wanted, int):
        return wanted
    name = str(wanted or "").strip().upper()
    return logging.getLevelNamesMapping().get(name, logging.INFO)


def setup_logging(level: str | int | None = None, *, stream: IO[str] | None = None) -> Any:
    """Send the ``orthostudio.*`` lines to ``stream`` (the output in use, by default) at ``level``.

    ``$OSXP_LOG_LEVEL`` wins over ``level``, and an unknown name is INFO. Called again (the tests,
    a second engine in one process) it keeps the one handler and only changes its level, so that
    no line is ever written twice.
    """
    wanted = _level(os.environ.get(LOG_LEVEL_ENV) or level)
    log = logging.getLogger(ROOT_LOGGER)
    log.setLevel(wanted)
    # uvicorn's handlers write its own lines; ours are written here alone
    log.propagate = False
    for handler in log.handlers:
        if getattr(handler, HANDLER_MARK, False):
            handler.setLevel(wanted)
            return log
    handler = logging.StreamHandler(sys.stdout if stream is None else stream)
    handler.setLevel(wanted)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    setattr(handler, HANDLER_MARK, True)
    log.addHandler(handler)
    return log
