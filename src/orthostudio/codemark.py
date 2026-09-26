# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What code an OrthoStudio XP runs: a mark of its package's Python files as they are on disk.

An engine loads its code when it starts, and a checkout's files change under it: a pull, another
branch, an edit. Opened again, the checkout showed the engine started before, the old code behind
the new page, and a pilot tested a fix that was not running (2026-09-25). The engine says the mark
it started with (``engine.code`` in ``GET /api/engine``); a launch compares it with the files and
has an engine whose code is no longer the one on disk make way (``api/serve.py`` ``take_over``,
``desktop.py`` ``engine_here``).

The mark reads no file: the name, the size and the time of change of each ``.py`` of the package,
a few milliseconds for its 160 files. Light on purpose: the app asks it before the engine's imports.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

__all__ = ["PACKAGE", "code_mark"]

PACKAGE = Path(__file__).resolve().parent
"""The folder of the ``orthostudio`` package this program runs."""


def code_mark(root: Path = PACKAGE) -> str:
    """The mark of the ``.py`` files under ``root``: it changes when one is added, removed or
    written. A file that goes while it is read (a checkout changing branch) is left out."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        try:
            stat = path.stat()
        except OSError:
            continue
        name = path.relative_to(root).as_posix()
        digest.update(f"{name}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()[:16]
