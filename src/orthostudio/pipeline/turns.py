# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The tiles a build holds, told to the other programs: a file per tile in the data folder, locked
by the system while the build holds the tile, the building process's number in it for the
message. A second program asked to build a tile another one is building (the command line beside
the app) is refused: the two would write the same folder, and the later would drop what the first
laid, its replaced clouds among it. A build in the app waits in its queue instead. The system
lets go of the lock whatever ends the program (a crash, a power cut): no file stands for a build
gone, nor for another process given its number since.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import IO

from orthostudio.errors import OsxpError
from orthostudio.home import require_data_root
from orthostudio.model import TileRef

__all__ = ["holding"]

_LOCKED_AT = 1 << 20
"""The byte locked, past the number written: under Windows a locked range cannot be read."""


def _path(tile: TileRef) -> Path:
    # never a folder made on the computer's own disk while the data folder's is unplugged
    return require_data_root() / "building" / f"{tile.name}.lock"


def _lock(f: IO[str]) -> bool:
    """Whether this process now holds ``f``'s lock (``False``: another one does)."""
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(_LOCKED_AT)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _take(tile: TileRef) -> IO[str]:
    path = _path(tile)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+", encoding="ascii")  # noqa: SIM115 (held open for the build)
    if not _lock(f):
        f.seek(0)
        owner = f.read(32).strip() or "?"
        f.close()
        raise OsxpError("SYS_TILE_BEING_BUILT", context={"tile": tile.name, "pid": owner})
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


@contextlib.contextmanager
def holding(tiles: Iterable[TileRef]) -> Iterator[None]:
    """The ``tiles`` held for this process inside the block; ``SYS_TILE_BEING_BUILT`` when another
    program holds one of them, nothing held then."""
    held: list[IO[str]] = []
    try:
        for t in sorted({t.name: t for t in tiles}.values(), key=lambda t: t.name):
            held.append(_take(t))
        yield
    finally:
        for f in held:
            f.close()  # the system lets go of the lock
