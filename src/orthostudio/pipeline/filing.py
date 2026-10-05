# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Tiles filed elsewhere (the atelier, step 3, 2026-10-05), one at a time.

A tile whose new folder is on the same disk is moved, in one step. On another disk it is copied
whole under a temporary name beside where it goes, its files sent to the disk as a group with one
flush of the disk's own cache (``graph.store.send_to_disk``, as a build's textures are), read back
against what was read, then put in place. Then it is switched as a tile found again
(``pack.find_again_receipt``: the Library and every X-Plane that showed it follow, its roads and
forests go with it), and taken out of the folder it came from, whole elsewhere by then.

Wherever a stop comes, a whole tile is left: the original until the copy is in place, the copy
after. A temporary folder is a copy a stop left half made, made again. A tile is checked whole
before anything of it moves, and a folder of its name found where it goes is taken for a copy only
when it is not a link and reads back the same bytes. Spec ``install.md`` 4.6.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import blake3

from orthostudio.errors import OsxpError
from orthostudio.fsutil import fsync_dir, replace
from orthostudio.graph.store import flush_disk, send_to_disk
from orthostudio.home import data_root
from orthostudio.install import LibraryEntry, is_xplane_dir
from orthostudio.install import packs as install_packs
from orthostudio.pipeline.pack import (
    _INSTALL_LOCK,
    MANIFEST_NAME,
    _delete_pack_dir,
    find_again_receipt,
    links_to,
    pack_is_intact,
    read_manifest,
)

__all__ = [
    "FILE_FORMAT",
    "PART_SUFFIX",
    "ROOM_MARGIN",
    "WORKSHOP_FOLDERS",
    "FilingStoppedError",
    "check_destination",
    "file_tile",
    "filing_plan",
    "pack_bytes",
    "same_disk",
]

FILE_FORMAT = "osxp-file-1"
PART_SUFFIX = ".osxp-part"
"""The name a copy is made under, beside where it goes, until it is read back and put in place."""
CHUNK = 8 * 2**20
"""Bytes read and written at a time: a file costs its bytes, not its calls."""
ROOM_MARGIN = 256 * 2**20
"""Free space kept on the disk a copy goes to, beyond the copy itself."""
WORKSHOP_FOLDERS = ("tiles", "store", "chunks", "work", "mapcache", "elevation", "dem")
"""The data folder's own folders: the workshop's tiles, which tiles are filed out of, and what the
builds write and Free space empties whole (a tile filed there would go with it)."""


class FilingStoppedError(Exception):
    """Asked to stop: the tile in hand is where it was, whole."""


def _files(pack: Path) -> list[Path]:
    """The regular files of ``pack``, in a fixed order."""
    out: list[Path] = []
    for dirpath, dirs, names in os.walk(pack):
        dirs.sort()
        out.extend(Path(dirpath) / n for n in sorted(names) if (Path(dirpath) / n).is_file())
    return out


def pack_bytes(pack: Path) -> int:
    """What a copy of ``pack`` writes: every file in full, those it shares with the cache too."""
    return sum(f.stat().st_size for f in _files(Path(pack)))


def same_disk(a: Path, b: Path) -> bool:
    """Whether ``a`` and ``b`` are on one disk, where a folder moves in one step."""
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _same_folder(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _around(folder: Path) -> list[Path]:
    """``folder`` and the folders holding it, as named and with its links followed: a folder
    reached through a link, or named in another case, is the folder it leads to."""
    real = Path(os.path.realpath(folder))
    return [folder, *folder.parents, real, *real.parents]


def check_destination(dest: Path, custom_sceneries: Iterable[Path] = ()) -> None:
    """Refuse a folder no tile is filed into: inside an X-Plane's Custom Scenery, where X-Plane
    reads OrthoStudio XP's tiles through a link, named so or one of ``custom_sceneries`` however
    reached (``SYS_FOLDER_IN_CUSTOM_SCENERY``); the workshop or another of the data folder's own
    folders (:data:`WORKSHOP_FOLDERS`, ``SYS_FOLDER_IS_ATELIER``); a folder inside a tile, which
    deleting that tile would take away with it (``SYS_FOLDER_IN_TILE``); a folder that is not
    there, gone since it was chosen (``SYS_FOLDER_GONE``)."""
    if not dest.is_dir():
        raise OsxpError("SYS_FOLDER_GONE", context={"folder": str(dest)})
    around = _around(dest)
    sceneries = [Path(cs) for cs in custom_sceneries]
    if any(p.name == "Custom Scenery" and is_xplane_dir(p.parent) for p in around) or any(
        _same_folder(p, cs) for p in around for cs in sceneries
    ):
        raise OsxpError("SYS_FOLDER_IN_CUSTOM_SCENERY", context={"folder": str(dest)})
    data = data_root()
    if any(_same_folder(p, data / name) for p in around for name in WORKSHOP_FOLDERS):
        raise OsxpError("SYS_FOLDER_IS_ATELIER", context={"folder": str(dest)})
    pack = next((p for p in around if (p / MANIFEST_NAME).is_file()), None)
    if pack is not None:
        raise OsxpError("SYS_FOLDER_IN_TILE", context={"folder": str(dest), "tile": pack.name})


def _is_whole(entry: LibraryEntry, folder: Path) -> bool:
    """Whether ``folder`` holds the build of ``entry``, whole: its manifest's tile and keys, and
    what that manifest lists (``pack_is_intact``, the overlay DSF aside: it may sit beside it)."""
    try:
        manifest = read_manifest(folder)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    same = manifest.tile == entry.tile.name and (entry.keys is None or manifest.keys == entry.keys)
    return same and pack_is_intact(folder, manifest, with_overlay=False)


def _whole_build(target: Path, entry: LibraryEntry) -> bool:
    """Whether ``target``, a real folder that is not the tile's own, holds this very build of it,
    whole, every file of the original at its size: what the plan sees of a copy a stop left in
    place, before :func:`_same_bytes` reads it back."""
    old = Path(entry.path)
    if install_packs.is_link(target) or _same_folder(target, old) or not _is_whole(entry, target):
        return False
    sizes = {f.relative_to(old): f.stat().st_size for f in _files(old)}
    return all(
        (target / rel).is_file() and (target / rel).stat().st_size == n for rel, n in sizes.items()
    )


def filing_plan(entry: LibraryEntry, dest: Path) -> dict[str, Any]:
    """What filing ``entry`` into ``dest`` does: ``how`` is ``move`` (same disk, one step),
    ``copy`` (another disk, ``bytes`` to write), ``reuse`` (a whole copy is there already),
    ``there`` (already in that folder), ``taken`` (another folder or a link of that name is there)
    or ``not_whole`` (the tile's own folder lacks files, or holds another build: to build again)."""
    old = Path(entry.path)
    target = Path(dest) / old.name
    if _same_folder(old.parent, dest):
        return {"how": "there", "bytes": 0}
    if not _is_whole(entry, old):
        return {"how": "not_whole", "bytes": 0}
    if os.path.lexists(target):  # a broken link too
        return {"how": "reuse" if _whole_build(target, entry) else "taken", "bytes": 0}
    if same_disk(old, dest):
        return {"how": "move", "bytes": 0}
    return {"how": "copy", "bytes": pack_bytes(old)}


def file_tile(
    entry: LibraryEntry,
    dest: Path,
    *,
    custom_sceneries: Iterable[Path],
    library_path: Path | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """File the tile of ``entry``, a pack OrthoStudio XP built that is where the Library says,
    into the folder ``dest`` (the module's doc). ``progress(phase, done, total)`` hears the bytes
    copied (``copy``) and read back (``check``); ``stop()`` true stops before the next file, the
    tile left where it was (:class:`FilingStoppedError`). Returns the receipt of the tile found
    again, with ``moved`` (one step on one disk) and ``left``: the folder it came from when it
    could not be taken out (a file held by another program), else ``None``."""
    old = Path(entry.path)
    dest = Path(dest)
    target = dest / old.name
    tile = entry.tile.name
    sceneries = [Path(cs) for cs in custom_sceneries]
    check_destination(dest, sceneries)
    if install_packs.xplane_running():
        raise OsxpError(
            "XP_RUNNING",
            message="X-Plane is running, and its links never change while it runs: nothing was "
            "changed.",
            remedy="Quit X-Plane, then file the tile again.",
        )
    plan = filing_plan(entry, dest)
    if plan["how"] == "there":
        return {"format": FILE_FORMAT, "tile": tile, "to": str(old), "moved": False, "left": None}
    if plan["how"] == "not_whole":
        # refused before anything of it moves: the switch would refuse it after, where X-Plane no
        # longer finds it (a review, 2026-10-05)
        raise OsxpError("SYS_TILE_NOT_WHOLE", context={"tile": tile, "folder": str(old)})
    taken = plan["how"] == "taken"
    if taken or (plan["how"] == "reuse" and not _same_bytes(old, target, tile, progress, stop)):
        # a copy found there is trusted only read back whole: taking the original away after one
        # cut short, or after a link to the original itself, lost the tile (a review, 2026-10-05)
        raise OsxpError("SYS_TILE_NAME_TAKEN", context={"tile": tile, "folder": str(target)})
    moved = False
    if plan["how"] == "move":
        try:
            replace(old, target)
            moved = True
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            plan = {"how": "copy", "bytes": pack_bytes(old)}  # two disks after all
    if plan["how"] == "copy":
        free = shutil.disk_usage(dest).free
        if plan["bytes"] + ROOM_MARGIN > free:
            raise OsxpError(
                "SYS_DISK_FULL",
                context={"volume": str(dest), "needed": _gb(plan["bytes"]), "free": _gb(free)},
            )
        _copy_checked(old, target, tile, progress, stop)
    if not moved:
        _lead_to(sceneries, old, target)
    receipt = find_again_receipt(
        entry, target, custom_sceneries=sceneries, library_path=library_path
    )
    left = None
    if not moved and old.exists() and not _same_folder(old, target):
        # whole elsewhere now, and in the Library and X-Plane from there
        try:
            _delete_pack_dir(old)
        except OSError:
            left = str(old)  # a file another program holds: the page says where it is
    return {**receipt, "format": FILE_FORMAT, "moved": moved, "left": left}


def _lead_to(custom_sceneries: list[Path], old: Path, target: Path) -> None:
    """Every X-Plane whose link leads to ``old``, the tile still there, given a link to ``target``
    instead, its line in ``scenery_packs.ini`` left as the user left it, enabled or not and where
    he put it: the switch then finds what a move leaves. Taken out and installed again, the line
    came back enabled, at its default place (a review, 2026-10-05). A link that cannot be made
    gives the old one back."""
    with _INSTALL_LOCK:
        for cs in custom_sceneries:
            if not links_to(cs / old.name, old):
                continue
            install_packs.uninstall_pack(old.name, cs, update_ini=False)
            try:
                install_packs.install_pack(target, cs, update_ini=False)
            except BaseException:
                install_packs.install_pack(old, cs, update_ini=False)
                raise


def _same_bytes(
    src: Path,
    target: Path,
    tile: str,
    progress: Callable[[str, int, int], None] | None,
    stop: Callable[[], bool] | None,
) -> bool:
    """Whether every file of ``src`` is in ``target`` with the same bytes: a copy found there is
    read back as a copy made here is. Its sizes alone vouched for a copy cut short, whose files
    a system writes at their full size first."""
    files = _files(src)
    total = sum(f.stat().st_size for f in files)
    done = 0
    for f in files:
        if stop is not None and stop():
            raise FilingStoppedError(tile)
        try:
            with open(f, "rb") as a, open(target / f.relative_to(src), "rb") as b:
                while True:
                    chunk = a.read(CHUNK)
                    if b.read(CHUNK) != chunk:
                        return False
                    if not chunk:
                        break
                    done += len(chunk)
        except OSError:
            return False
        if progress is not None:
            progress("check", done, total)
    return True


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def _copy_checked(
    src: Path,
    target: Path,
    tile: str,
    progress: Callable[[str, int, int], None] | None,
    stop: Callable[[], bool] | None,
) -> None:
    """``src`` copied whole to ``target``: under the temporary name, files sent to the disk as a
    group, read back against what was read, then put in place. Any failure or stop takes the
    temporary folder away; ``src`` is never touched."""
    part = target.with_name(target.name + PART_SUFFIX)
    if part.exists():
        shutil.rmtree(part)  # a copy a stop left half made: made again
    files = _files(src)
    total = sum(f.stat().st_size for f in files)
    digests: dict[Path, str] = {}
    try:
        part.mkdir()
        done = 0
        for f in files:
            if stop is not None and stop():
                raise FilingStoppedError(tile)
            rel = f.relative_to(src)
            out = part / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            h = blake3.blake3()
            with open(f, "rb") as r, open(out, "wb") as w:
                while chunk := r.read(CHUNK):
                    h.update(chunk)
                    w.write(chunk)
                    done += len(chunk)
            with contextlib.suppress(OSError):  # a disk that keeps no dates or rights: no harm
                shutil.copystat(f, out)
            digests[rel] = h.hexdigest()
            if progress is not None:
                progress("copy", done, total)
        # sent to the disk as a group, the disk's own cache flushed once: each file forced alone
        # held a hard disk under Windows at every one (2026-09-28)
        for rel in digests:
            # a file a scanner holds a moment is written all the same, and read back below
            with contextlib.suppress(PermissionError):
                send_to_disk(part / rel)
        if digests:
            flush_disk(part / next(iter(digests)))
        checked = 0
        for rel, digest in digests.items():
            if stop is not None and stop():
                raise FilingStoppedError(tile)
            h = blake3.blake3()
            with open(part / rel, "rb") as r:
                while chunk := r.read(CHUNK):
                    h.update(chunk)
                    checked += len(chunk)
            if h.hexdigest() != digest:
                raise OsxpError(
                    "SYS_TILE_COPY_DIFFERS", context={"tile": tile, "file": str(target / rel)}
                )
            if progress is not None:
                progress("check", checked, total)
        replace(part, target)
        with contextlib.suppress(OSError):
            fsync_dir(target.parent)
    except BaseException:
        shutil.rmtree(part, ignore_errors=True)
        raise
