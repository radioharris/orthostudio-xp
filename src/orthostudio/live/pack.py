# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Where OrthoStudio XP Live keeps its pack and its imagery, and what it has built.

Both are heavy, and live in Live's own folder (:func:`live_root`): ``$OSXP_HOME/live``, on the
computer's own disk, unless the Live window's settings chose another. Not OrthoStudio XP's data
folder: that one may move to another disk for a while, and a pack following it would be set up
again there, the textures built and the imagery downloaded left behind (a user, 2026-09-27).
- ``pack``, the pack X-Plane reads through the link ``z_osxp_live`` in Custom Scenery;
- ``overlays``, the roads, forests and buildings of its squares, through ``y_osxp_live_overlays``
  (:mod:`orthostudio.live.overlays`);
- ``regions``, the AutoOrtho regions Live installed itself (:mod:`orthostudio.live.aoregions`);
- ``chunks.sqlite``, the downloaded image pieces.

The pack's textures round the aircraft are names of one neutral file until they are built, and a
built one keeps on the disk only the levels built, so the folder must be on a disk that can do both
(:func:`check_folder`, :mod:`orthostudio.live.filesystem`): APFS on a Mac, NTFS on Windows, ext4 on
Linux. ``_live/ortho.json`` in the pack records the level each texture is built to.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orthostudio.fsutil import atomic_write_text, open_read
from orthostudio.home import osxp_home
from orthostudio.live.filesystem import abilities

LINK_NAME = "z_osxp_live"
OVERLAY_LINK_NAME = "y_osxp_live_overlays"
MARKER = Path("_live") / "ortho.json"


def default_live_root() -> Path:
    return osxp_home() / "live"


def live_root() -> Path:
    """Live's folder: the settings' ``folder``, else :func:`default_live_root`."""
    from orthostudio.live.settings import load_settings

    folder = load_settings().folder
    return Path(folder) if folder else default_live_root()


class FolderError(ValueError):
    """A folder that cannot hold Live's pack; the message says why."""


def check_folder(path: Path, *, custom_scenery: Path | None = None, create: bool = False) -> Path:
    """``path``, once it can hold Live's pack. Raises :class:`FolderError` when it is not a full
    path, when its disk is not there (never made on the computer's own disk instead), when it is a
    file, inside X-Plane's Custom Scenery (which X-Plane reads as scenery), or on a disk that cannot
    hold the pack: one with neither clones nor sparse files, or no hard links (Mac OS Extended,
    exFAT, FAT32), or read only. ``create`` makes it when its parent is there."""
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise FolderError(f"{path} is not a full path")
    if not path.exists() and not path.parent.is_dir():
        raise FolderError(f"{path} is not there: is its disk plugged in?")
    if path.exists() and not path.is_dir():
        raise FolderError(f"{path} is a file, not a folder")
    if custom_scenery is not None:
        inside, scenery = path.resolve(), Path(custom_scenery).resolve()
        if inside == scenery or scenery in inside.parents:
            raise FolderError(f"{path} is inside X-Plane's Custom Scenery, read as scenery")
    if not abilities(path if path.exists() else path.parent).holds_pack:
        raise FolderError(
            f"{path} cannot hold the pack: its disk must be APFS (a Mac), NTFS (Windows) or ext4 "
            f"(Linux), and writable"
        )
    if create:
        path.mkdir(exist_ok=True)
    return path


def pack_dir(root: Path) -> Path:
    """The pack in a Live folder ``root``."""
    return root / "pack"


def default_pack_dir() -> Path:
    return pack_dir(live_root())


def default_cache_path() -> Path:
    return live_root() / "chunks.sqlite"


def load_built(pack_dir: Path, damaged: Callable[[str], object] | None = None) -> dict[str, Any]:
    """Live's record of the textures it built (:data:`MARKER`), {} when there is none.

    A record that does not read (empty, cut short, not a table, not text) is read as none, and
    ``damaged`` hears why: every request of the window reads it, and one found empty answered
    them all with an error (discussion 32, docs/specs/live.md)."""
    try:
        with open_read(pack_dir / MARKER) as f:  # Windows: never in the instant of a save
            text = f.read().decode("utf-8")
        meta: Any = json.loads(text) if text.strip() else "empty"
    except FileNotFoundError:
        return {}
    except ValueError as e:  # not JSON, or not text (UnicodeDecodeError is a ValueError)
        meta = e
    if isinstance(meta, dict):
        return meta
    if damaged is not None:
        damaged(f"Live's record of its textures did not read ({str(meta)[:120]})")
    return {}


def save_built(pack_dir: Path, meta: dict[str, Any]) -> int:
    """Live's record of its textures written whole, its bytes flushed to the disk before it takes
    the record's name (docs/specs/live.md). Returns its size."""
    text = json.dumps(meta)
    atomic_write_text(pack_dir / MARKER, text, fsync=True)
    return len(text)


def copies_in(meta: dict[str, Any]) -> list[str]:
    """The 8192 px files the record says hold the copy of their 4096 px level
    (orthostudio.live.texture.copy_up), while the record is the one saved with them: an older
    Live rewrote it (its own ``updated``) without knowing them, and may have written those files
    again (the review of 2026-11-11)."""
    same = meta.get("copied_with") == meta.get("updated")
    return list(meta.get("copied", [])) if same else []
