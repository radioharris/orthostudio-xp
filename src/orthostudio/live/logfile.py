# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's log in a file, as X-Plane's own: ``LogLive.txt``, beside X-Plane's ``Log.txt``.

The Live window kept its log in memory only: closed, it was gone, twice before it could be read
(2026-09-29). The user asked for a file "simple à trouver aussi pour l'utilisateur, à l'image de
Log.txt de X-Plane", found OrthoStudio XP's own (``~/Library/Logs``) hard to find, and asked for
"un log simple à la racine... du genre LogLive.txt".

- Where: at the top of the X-Plane folder Live streams into, beside ``Log.txt``. Without one Live
  can write in, in Live's own folder (:func:`orthostudio.live.pack.live_root`). With ``OSXP_HOME``
  set (a test window), always there: never in the user's X-Plane.
- As X-Plane's: written again each time the window opens, with a header (which Live, since when,
  on what computer), then every line of the window's log with its time. Each line is flushed as it
  comes, so that a crash leaves the file whole.
- The runs before go to ``log`` in Live's folder, named ``LogLive <start>.txt``: the last KEEP.
- The window's log names the file and shows it in the Finder (File Explorer, the file manager).
- Nothing about the log may stop Live: a file that cannot be written is left out.
"""

from __future__ import annotations

import contextlib
import os
import platform
import shutil
import threading
import time
from pathlib import Path
from typing import TextIO

NAME = "LogLive.txt"
ARCHIVE = "log"  # the runs before, in Live's folder
KEEP = 10
STARTED = "Live started on "  # the header's line: time.ctime's words, as X-Plane's Log.txt
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def log_path() -> Path:
    """Where the window's log goes (the module's docstring)."""
    from orthostudio.live.pack import live_root

    root = live_root()
    if os.environ.get("OSXP_HOME"):
        return root / NAME
    from orthostudio.live.scenery import find_xplane

    with contextlib.suppress(Exception):  # no X-Plane found, or settings unreadable
        xplane = find_xplane()
        if os.access(xplane, os.W_OK):
            return xplane / NAME
    return root / NAME


def archive_folder() -> Path:
    from orthostudio.live.pack import live_root

    return live_root() / ARCHIVE


def header(now: float | None = None) -> str:
    """What Live, since when and on what computer, as X-Plane's Log.txt begins."""
    from orthostudio import __version__
    from orthostudio.codemark import code_mark

    return (
        f"{NAME} for OrthoStudio XP Live {__version__} (code {code_mark()})\n"
        f"{STARTED}{time.ctime(now)}\n"
        f"This computer: {platform.platform()}, Python {platform.python_version()}\n"
        "\n"
    )


def started(path: Path) -> float:
    """When the run whose log ``path`` is started: its header's line, else the file's last
    change. The header's words are parsed here, whatever language the system speaks."""
    with (
        contextlib.suppress(OSError, ValueError, IndexError),
        open(path, encoding="utf-8", errors="replace") as f,
    ):
        for _ in range(5):
            line = f.readline()
            if line.startswith(STARTED):
                _, month, day, clock, year = line[len(STARTED) :].split()
                hour, minute, second = (int(v) for v in clock.split(":"))
                month_number = _MONTHS.index(month) + 1
                return time.mktime(
                    (int(year), month_number, int(day), hour, minute, second, 0, 0, -1)
                )
    return path.stat().st_mtime


def set_aside(path: Path, archive: Path, keep: int = KEEP) -> None:
    """The last run's log into ``archive``, named for when it started; there, only the last
    ``keep``."""
    if not path.exists():
        return
    stamp = time.strftime("%Y-%m-%d %H-%M-%S", time.localtime(started(path)))
    archive.mkdir(parents=True, exist_ok=True)
    target, n = archive / f"LogLive {stamp}.txt", 2
    while target.exists():
        target, n = archive / f"LogLive {stamp} ({n}).txt", n + 1
    shutil.move(str(path), str(target))  # X-Plane may be on another disk than Live's folder
    kept = sorted(archive.glob("LogLive *.txt"), key=lambda p: p.name)
    for old in kept[: max(0, len(kept) - keep)]:
        with contextlib.suppress(OSError):
            old.unlink()


class LogFile:
    """The window's log in a file (the module's docstring)."""

    def __init__(self, path: Path, archive: Path | None = None, keep: int = KEEP) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._out: TextIO | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if archive is not None:
                with contextlib.suppress(OSError):  # the last run's log stays, overwritten
                    set_aside(path, archive, keep)
            self._out = open(path, "w", encoding="utf-8", newline="\n")  # noqa: SIM115
            self._out.write(header())
            self._out.flush()
        except OSError:
            self._out = None

    @property
    def open(self) -> bool:
        return self._out is not None

    def write(self, text: str) -> None:
        """``text``'s lines, each with the time, flushed."""
        stamp = time.strftime("%H:%M:%S")
        with self._lock:
            if self._out is None:
                return
            try:
                for line in text.splitlines() or [""]:
                    self._out.write(f"{stamp} {line}\n")
                self._out.flush()
            except (OSError, ValueError):  # the disk full, or the file gone
                pass

    def echo(self, text: str) -> None:
        """The window's echo: the terminal it was opened from, and the file."""
        print(text)
        self.write(text)

    def close(self) -> None:
        with self._lock:
            if self._out is not None:
                with contextlib.suppress(OSError):
                    self._out.close()
                self._out = None
