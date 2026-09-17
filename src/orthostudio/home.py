"""Where OrthoStudio XP keeps its own data.

``$OSXP_HOME`` (default ``~/.orthostudio``) holds what is small and personal: the settings, the
library, the job history, the zones. The heavy data (the tiles, the artefact store, the downloaded
image pieces, the map background, the relief and map data downloaded, the build's work folder)
live in the **data folder** (:func:`data_root`): ``$OSXP_HOME`` too, unless the setting
``essential.data_dir`` names another folder, on an external disk for instance (a user asked,
2026-09-15; ``docs/specs/pipeline-textures.md`` section 2). The roots can be overridden per command.

This module sits at the bottom of the package on purpose: ``orthostudio.sources`` and
``orthostudio.dem`` need the roots and must not import ``orthostudio.pipeline`` to get them (review
4, layering). ``orthostudio.pipeline.home`` re-exports it, so every existing import keeps working.
It reads the one key of ``config.toml`` it needs itself, for the same reason.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import tomllib
from pathlib import Path

from orthostudio.errors import OsxpError

__all__ = [
    "DATA_DIR_ENV",
    "OSXP_HOME_ENV",
    "check_data_dir",
    "data_root",
    "data_root_missing",
    "default_chunks_root",
    "default_mapcache_root",
    "default_patches_dir",
    "default_store_root",
    "default_tiles_root",
    "default_work_root",
    "make_patches_dir",
    "osxp_home",
    "require_data_root",
]

OSXP_HOME_ENV = "OSXP_HOME"
DATA_DIR_ENV = "OSXP_DATA_DIR"
"""Overrides the data folder of the settings (tests, a command run by hand)."""


def osxp_home() -> Path:
    """``$OSXP_HOME`` or ``~/.orthostudio`` (not created)."""
    env = os.environ.get(OSXP_HOME_ENV)
    return Path(env).expanduser() if env else Path.home() / ".orthostudio"


_config_cache: dict[Path, tuple[tuple[int, int, int], Path | None]] = {}


def _configured_data_dir() -> Path | None:
    """``essential.data_dir`` of ``$OSXP_HOME/config.toml``, read again when the file changes."""
    path = osxp_home() / "config.toml"
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (stat.st_mtime_ns, stat.st_size, stat.st_ino)  # a save replaces the file: new inode
    hit = _config_cache.get(path)
    if hit is not None and hit[0] == key:
        return hit[1]
    try:
        essential = tomllib.loads(path.read_text("utf-8")).get("essential")
        value = essential.get("data_dir") if isinstance(essential, dict) else None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        value = None
    folder = Path(value).expanduser() if isinstance(value, str) and value.strip() else None
    _config_cache[path] = (key, folder)
    return folder


def data_root() -> Path:
    """The data folder: ``$OSXP_DATA_DIR``, else the folder of ``essential.data_dir``, else
    ``$OSXP_HOME``. Never created here: the folder of a disk that is not plugged in must not be made
    on the computer's own disk (:func:`data_root_missing`)."""
    env = os.environ.get(DATA_DIR_ENV)
    if env:
        return Path(env).expanduser()
    configured = _configured_data_dir()
    return configured if configured is not None else osxp_home()


def data_root_missing() -> Path | None:
    """The data folder when one was chosen and is not there (its disk unplugged), else ``None``.
    ``$OSXP_HOME`` itself is created as it always was."""
    root = data_root()
    if root == osxp_home() or root.is_dir():
        return None
    return root


def require_data_root() -> Path:
    """The data folder, or ``CFG_DATA_DIR_MISSING`` while it is not there: a build does not fill a
    folder of the computer's own disk when the external disk is unplugged."""
    missing = data_root_missing()
    if missing is not None:
        raise OsxpError("CFG_DATA_DIR_MISSING", context={"path": str(missing)})
    return data_root()


_DATA_DIR_REFUSALS: dict[str, tuple[str, str | None]] = {
    "relative": (
        "the path is not absolute",
        "Choose the folder with the button, or type its full path.",
    ),
    "file": ("it is a file, not a folder", "Choose a folder."),
    "xplane": (
        "it is inside X-Plane's Custom Scenery, whose folders X-Plane reads as scenery",
        "Choose a folder outside X-Plane's Custom Scenery.",
    ),
    "unwritable": (
        "it cannot be written",
        "Choose a folder you can write to: a disk may be read-only.",
    ),
    "links": (
        "its disk cannot hard-link files (exFAT or FAT32): each texture would be written three "
        "times",
        None,  # the registry's remedy: a disk formatted APFS, Mac OS Extended, NTFS or ext4
    ),
}


def _refuse_data_dir(path: Path, why: str, detail: str = "") -> OsxpError:
    reason, remedy = _DATA_DIR_REFUSALS[why]
    context = {"path": str(path), "reason": f"{reason}{detail}", "why": why}
    return OsxpError("CFG_DATA_DIR_INVALID", context=context, remedy=remedy)


def check_data_dir(value: str | Path, *, custom_scenery: Path | None = None) -> Path:
    """The folder ``value`` names, resolved, once it can hold the data. Else
    ``CFG_DATA_DIR_MISSING`` (not found: its disk unplugged, the folder not created yet) or
    ``CFG_DATA_DIR_INVALID``, whose ``why`` is ``relative``, ``file``, ``xplane`` (inside
    ``custom_scenery``), ``unwritable`` or ``links``.

    The disk must hard-link files: a texture is one file linked into the store and into its pack
    (``orthostudio.clean``). exFAT and FAT32, the format many external disks come in, cannot, and
    each texture would take three times its size: such a folder is refused (a user agreed,
    2026-09-15). Two empty probe files are written, linked and removed to find out.
    """
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        raise _refuse_data_dir(path, "relative")
    with contextlib.suppress(OSError):
        path = path.resolve()
    if not path.exists():
        raise OsxpError("CFG_DATA_DIR_MISSING", context={"path": str(path)})
    if not path.is_dir():
        raise _refuse_data_dir(path, "file")
    if custom_scenery is not None:
        with contextlib.suppress(OSError):
            scenery = Path(custom_scenery).resolve()
            if path == scenery or scenery in path.parents:
                raise _refuse_data_dir(path, "xplane")
    probe = path / f".osxp-probe-{secrets.token_hex(4)}"
    twin = probe.with_name(probe.name + ".link")
    try:
        try:
            probe.write_bytes(b"")
        except OSError as exc:
            raise _refuse_data_dir(path, "unwritable", f" ({exc.strerror or exc})") from None
        try:
            os.link(probe, twin)
        except OSError:
            raise _refuse_data_dir(path, "links") from None
    finally:
        for leftover in (twin, probe):
            with contextlib.suppress(OSError):
                leftover.unlink()
    return path


def default_store_root() -> Path:
    """Root of the artefact store (``docs/specs/graph-keys.md``)."""
    return data_root() / "store"


def default_chunks_root() -> Path:
    """Root of the raw tile containers and the parent cache (``imagery-chunks.md``)."""
    return data_root() / "chunks"


def default_tiles_root() -> Path:
    """Where the tile packs are written (``osxp build``, the page): ``<data>/tiles``."""
    return data_root() / "tiles"


def default_patches_dir() -> Path | None:
    """``~/.orthostudio/patches`` when it is there: the hand-made mesh patches, one folder
    per tile, used when Settings names no other (``expert.patches_dir``, 2026-09-17)."""
    folder = osxp_home() / "patches"
    return folder if folder.is_dir() else None


def make_patches_dir() -> Path:
    """Make ``$OSXP_HOME/patches`` if it is missing, and answer it.

    The page names that folder as the one a build reads when Settings names none, and a user
    went looking for it and found nothing: nothing had ever made it (2026-09-17). The engine
    makes it empty when it starts, so there is a folder to drop ``+46+006/...`` into; empty, it
    holds no tile, so it changes no build.
    """
    folder = osxp_home() / "patches"
    with contextlib.suppress(OSError):
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def default_work_root() -> Path:
    """The builds' work folder (logs, temporary files): ``<data>/work``."""
    return data_root() / "work"


def default_mapcache_root() -> Path:
    """The page's map background: ``<data>/mapcache``."""
    return data_root() / "mapcache"
