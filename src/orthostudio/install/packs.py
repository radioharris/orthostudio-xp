# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Link (or copy) a built scenery pack into ``Custom Scenery`` and undo it.

Spec: ``docs/specs/install.md`` section 4. Origin: Ortho4XP ``O4_GUI_Utils.py:1713-1780``
(``toggle_to_custom``): a symbolic link named after the pack, identity checked with
``os.path.samefile`` on the real paths, removal with ``os.remove``. OrthoStudio XP adds the refusal
while X-Plane runs, the junction fallback on Windows, the copy mode and the
``scenery_packs.ini`` update.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

from orthostudio.errors import Action, OsxpError, Severity
from orthostudio.fsutil import (
    NO_CONSOLE_WINDOW,
    REDIRECTION_GUARD_REMEDY,
    redirection_guard,
    temp_name,
)
from orthostudio.install.scenery_packs import SceneryPacks, pack_kind
from orthostudio.install.xplane import xplane_running

__all__ = ["SCENERY_PACKS_INI", "install_pack", "is_link", "uninstall_pack"]

SCENERY_PACKS_INI = "scenery_packs.ini"


def is_link(path: Path) -> bool:
    """``True`` for a symbolic link or, on Windows, a directory junction."""
    path = Path(path)
    if path.is_symlink():
        return True
    if os.name != "nt":
        return False
    try:
        st = path.lstat()
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(os.path.realpath(a), os.path.realpath(b))
    except OSError:
        return False


def _refuse_if_running() -> None:
    if xplane_running():
        raise OsxpError("XP_RUNNING")


def _conflict(target: Path, pack_dir: Path, reason: str) -> OsxpError:
    """Something is in the way in Custom Scenery, and it is not ours to move.

    Called with ``target`` for both when nothing else is known, which read "X already exists and
    is not a link to X" and told the user to install, in the middle of taking a tile out (found
    in review, 2026-09-23).
    """
    itself = target == pack_dir
    message = (
        f"{target} is a real folder, not a link OrthoStudio XP made ({reason})."
        if itself
        else f"{target} already exists and is not a link to {pack_dir} ({reason})."
    )
    remedy = (
        "It was left where it is. Move or delete it yourself if you meant to."
        if itself
        else "Remove or rename the existing folder in Custom Scenery, then install again."
    )
    return OsxpError(
        "XP_PACK_CONFLICT",
        context={"tile": target.name, "pack": str(target), "reason": reason},
        message=message,
        remedy=remedy,
        severity=Severity.BLOCKING,
        action=Action.STOP,
    )


def _symlink(pack_dir: Path, target: Path) -> None:
    """The symbolic link. Tests refuse it on Windows, as Windows refuses it to an account without
    Developer Mode: they take the junction path of most Windows users."""
    os.symlink(pack_dir, target, target_is_directory=True)


def _make_link(pack_dir: Path, target: Path) -> None:
    try:
        _symlink(pack_dir, target)
        return
    except OSError as exc:
        if os.name != "nt":
            # the registry's remedy names Developer Mode, which is a Windows setting: on a Mac
            # or on Linux a refused link is the file system's doing, an exFAT or SMB Custom
            # Scenery for instance (found in review, 2026-09-23)
            raise OsxpError(
                "XP_LINK_FAILED",
                context={"link": str(target), "reason": f"{type(exc).__name__}: {exc}"},
                remedy=(
                    "X-Plane's Custom Scenery is on a disk that does not take links (an exFAT or "
                    "a network disk, say). Put X-Plane's Custom Scenery on a disk that does, or "
                    "copy the tile's folder into it by hand."
                ),
            ) from exc
        first = exc
    # Windows without Developer Mode: a directory junction needs no privilege.
    tried = _make_junction(pack_dir, target)
    if is_link(target) and _same_dir(target, pack_dir):
        return
    found = _found_at(target)
    _take_back(target)
    reason = f"symlink: {type(first).__name__}: {first}; {tried}; {found}"
    if redirection_guard():
        # the junction is made, and this process may not follow it: X-Plane could, the app cannot
        raise OsxpError(
            "XP_LINK_FAILED",
            context={"link": str(target), "reason": reason},
            message=f"The tile could not be installed: OrthoStudio XP runs with Windows' junction "
            f"protection (RedirectionGuard), and cannot follow the junction {target} ({reason}).",
            remedy=REDIRECTION_GUARD_REMEDY,
        )
    raise OsxpError("XP_LINK_FAILED", context={"link": str(target), "reason": reason})


def _make_junction(pack_dir: Path, target: Path) -> str:
    """Make ``target`` a directory junction leading to ``pack_dir``; returns what was tried, for the
    error when no such junction is there afterwards.

    CPython's own ``_winapi.CreateJunction`` first (its tests make their junctions with it): no
    program to start. ``cmd /d /c mklink /J`` when it fails. What decides is the disk afterwards
    (:func:`_make_link`), never cmd's exit code: an AutoRun command of cmd that leaves an error
    level makes cmd exit with 1 after mklink made the junction (reproduced on Windows; ``/d`` skips
    AutoRun).
    """
    try:
        import _winapi

        _winapi.CreateJunction(str(pack_dir), str(target))  # type: ignore[attr-defined]
        return "CreateJunction: done"
    except (ImportError, AttributeError, OSError) as exc:
        tried = f"CreateJunction: {type(exc).__name__}: {exc}"
    # CreateJunction makes the directory before it turns it into a junction
    _remove_empty_dir(target)
    try:
        done = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(target), str(pack_dir)],
            capture_output=True,
            timeout=30,
            check=False,
            creationflags=NO_CONSOLE_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{tried}; mklink /J: {type(exc).__name__}: {exc}"
    said = _console_text(done.stderr.strip() or done.stdout.strip())
    return f"{tried}; mklink /J (exit {done.returncode}): {said}"


def _console_text(data: bytes) -> str:
    """What a console program of Windows wrote, in its OEM code page: read as the ANSI one, the
    accents of a French mklink came out as commas."""
    try:
        return data.decode("oem", errors="replace")
    except LookupError:  # a system other than Windows: the tests
        return data.decode("utf-8", errors="replace")


def _found_at(target: Path) -> str:
    """What is at ``target`` when no link to the pack is, in words for ``XP_LINK_FAILED``."""
    if not os.path.lexists(target):
        return "no junction was made"
    if not is_link(target):
        return "a folder is there instead of a junction"
    try:
        return f"the junction made leads to {os.readlink(target)}"
    except OSError as exc:
        return f"the junction made cannot be read ({exc})"


def _take_back(target: Path) -> None:
    """Remove what a failed link attempt left at ``target``, where nothing was before: a link, or
    an empty folder. Anything else stays."""
    if is_link(target):
        _remove_link(target)
    else:
        _remove_empty_dir(target)


def _remove_empty_dir(path: Path) -> None:
    if is_link(path) or not path.is_dir():
        return
    with contextlib.suppress(OSError):
        path.rmdir()  # empty only: a folder with anything in it stays


def _copy_tree(pack_dir: Path, target: Path) -> None:
    tmp = temp_name(target)
    try:
        shutil.copytree(pack_dir, tmp, symlinks=False)
        os.replace(tmp, target)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise OsxpError(
            "XP_LINK_FAILED",
            context={"link": str(target), "reason": f"copy: {type(exc).__name__}: {exc}"},
            message=f"The pack could not be copied to {target} ({exc}).",
            remedy="Check free space and permissions on Custom Scenery.",
        ) from exc


def _update_ini(custom_scenery: Path, name: str, *, add: bool) -> bool:
    ini = custom_scenery / SCENERY_PACKS_INI
    packs = SceneryPacks.load(ini)
    kind = pack_kind(name)
    changed = (
        packs.ensure(name, kind=kind, reenable=kind != "overlay") if add else packs.remove(name)
    )
    if changed:
        packs.save(ini, backup=True)
    return changed


def install_pack(
    pack_dir: Path,
    custom_scenery: Path,
    *,
    link: bool = True,
    update_ini: bool = True,
    name: str | None = None,
) -> Path:
    """Make ``pack_dir`` visible as ``<custom_scenery>/<name>`` (its own name by default) and
    activate it.

    ``link`` creates a symbolic link (a junction on Windows when symlinks are refused);
    ``link=False`` copies the pack. Idempotent when the target already is the pack. Raises
    ``XP_RUNNING``, ``XP_PACK_CONFLICT`` (a foreign folder at the target), ``XP_LINK_FAILED``
    or ``XP_SCENERY_PACKS_UNWRITABLE``.
    """
    pack_dir = Path(pack_dir).resolve()
    custom_scenery = Path(custom_scenery)
    if not pack_dir.is_dir():
        raise OsxpError(
            "SYS_WORKING_DIR_INVALID",
            context={"path": str(pack_dir)},
            message=f"Scenery pack {pack_dir} is not a directory.",
            remedy="Build the tile first, then install it.",
        )
    if not custom_scenery.is_dir():
        raise OsxpError("XP_DIR_NOT_FOUND", context={"path": str(custom_scenery)})
    _refuse_if_running()
    target = custom_scenery / (name or pack_dir.name)
    if is_link(target) and not target.exists():
        # A dangling link (the pack was moved): replacing it destroys nothing of the user's.
        _remove_link(target)
    exists = target.exists() or is_link(target)
    if exists:
        if not _same_dir(target, pack_dir):
            what = "link elsewhere" if is_link(target) else "real folder"
            raise _conflict(target, pack_dir, what)
    elif link:
        _make_link(pack_dir, target)
    else:
        _copy_tree(pack_dir, target)
    if update_ini:
        _update_ini(custom_scenery, target.name, add=True)
    return target


def _remove_link(target: Path) -> None:
    """Remove a symbolic link, or a junction on Windows (never the directory it points to)."""
    if os.name == "nt" and not target.is_symlink():
        os.rmdir(target)  # junction
    else:
        os.unlink(target)


def _looks_like_pack(path: Path) -> bool:
    return (path / "Earth nav data").is_dir()


def uninstall_pack(
    name: str,
    custom_scenery: Path,
    *,
    update_ini: bool = True,
    remove_copy: bool = False,
) -> bool:
    """Remove ``<custom_scenery>/<name>`` (a link, or a copied pack with ``remove_copy``).

    A real folder is never deleted unless ``remove_copy`` is set *and* the folder holds
    ``Earth nav data/``; otherwise ``XP_PACK_CONFLICT``. The ``scenery_packs.ini`` line is
    dropped. Returns whether anything was removed.
    """
    custom_scenery = Path(custom_scenery)
    target = custom_scenery / name
    _refuse_if_running()
    removed = False
    if is_link(target):
        _remove_link(target)
        removed = True
    elif target.is_dir():
        if not remove_copy or not _looks_like_pack(target):
            raise _conflict(target, target, "real folder, not removed")
        # guarded: one file held open by X-Plane or by an antivirus left the copy half
        # destroyed and the page a bare 500, with no code and no remedy (found in review,
        # 2026-09-23)
        try:
            shutil.rmtree(target)
        except OSError as exc:
            raise OsxpError(
                "SYS_WRITE_FAILED",
                context={"path": str(exc.filename or target), "reason": str(exc.strerror or exc)},
                remedy=(
                    "Quit X-Plane and any program that may be using those files, then take the "
                    "tile out again: what is already removed stays removed."
                ),
            ) from exc
        removed = True
    if update_ini:
        removed = _update_ini(custom_scenery, name, add=False) or removed
    return removed
