# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The tiles a build holds, told to the other programs: a file per tile in the data folder, the
building process's number in it. A second program asked to build a tile another one is building
(the command line beside the app) is refused: the two would write the same folder, and the later
would drop what the first laid, its replaced clouds among it. A build in the app waits in its
queue instead; a file left by a program gone counts for nothing.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from orthostudio.errors import OsxpError
from orthostudio.graph.store import pid_alive
from orthostudio.home import require_data_root
from orthostudio.model import TileRef

__all__ = ["holding"]


def _path(tile: TileRef) -> Path:
    # never a folder made on the computer's own disk while the data folder's is unplugged
    return require_data_root() / "building" / f"{tile.name}.pid"


def _owner(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(text) if text.isdigit() else None


def _take(tile: TileRef) -> Path:
    path = _path(tile)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            owner = _owner(path)
            if owner is not None and owner != os.getpid() and pid_alive(owner):
                raise OsxpError(
                    "SYS_TILE_BEING_BUILT", context={"tile": tile.name, "pid": owner}
                ) from None
            with contextlib.suppress(FileNotFoundError):
                path.unlink()  # left by a program gone, or by this one
            continue
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(str(os.getpid()))
        return path
    raise OsxpError("SYS_TILE_BEING_BUILT", context={"tile": tile.name, "pid": "?"})


@contextlib.contextmanager
def holding(tiles: Iterable[TileRef]) -> Iterator[None]:
    """The ``tiles`` held for this process inside the block; ``SYS_TILE_BEING_BUILT`` when another
    live program holds one of them, nothing held then."""
    held: list[Path] = []
    try:
        for t in sorted({t.name: t for t in tiles}.values(), key=lambda t: t.name):
            held.append(_take(t))
        yield
    finally:
        for path in held:
            if _owner(path) == os.getpid():
                with contextlib.suppress(OSError):
                    path.unlink()
