# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the disk of Live's folder can do, and the file operations Live needs, alike on macOS,
Windows and Linux.

The pack keeps texture files only round the aircraft (:mod:`orthostudio.live.names`). Unbuilt, a
texture is a name of one neutral texture: a hard link, which every disk Live accepts has, up to a
number of names a file (:attr:`Abilities.link_max`: 32,767 on a Mac, 1,023 on NTFS, 65,000 on
ext4). Built, a texture holds on the disk only the levels built: its larger ones share the neutral
texture's blocks where the disk clones files (APFS, btrfs, xfs), else they are a hole of a sparse
file (NTFS, ext4), which reads as zeros. A disk that can do neither, Mac OS Extended, exFAT or
FAT32, would take a texture's full 11 or 45 MB for every texture round the aircraft, tens to
hundreds of gigabytes: it cannot hold Live's folder (:func:`abilities`, 2026-09-29).

Folders are linked as OrthoStudio XP links its tiles into Custom Scenery
(:mod:`orthostudio.install.packs`): a symbolic link, or on Windows, where one needs Developer Mode,
a directory junction, which needs nothing.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import os
import shutil
import struct
import sys
import threading
import time
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .clone import NO_CLONE, clone

WINDOWS = os.name == "nt"  # a flag of its own: tests take the Windows path on any system
BINARY = getattr(os, "O_BINARY", 0)  # Windows: no newline translation in a texture
BLOCK = 4096
F_PUNCHHOLE = 99  # <sys/fcntl.h>: struct fpunchhole {uint flags, reserved; off_t offset, length}
FALLOC_FL_KEEP_SIZE, FALLOC_FL_PUNCH_HOLE = 1, 2  # <linux/falloc.h>
FSCTL_SET_SPARSE = 0x000900C4  # <winioctl.h>
FSCTL_SET_ZERO_DATA = 0x000980C8
ERROR_TOO_MANY_LINKS = 1142  # what NTFS answers past 1,023 names a file
NTFS_LINK_MAX = 1023
REPLACE_TRIES_S = 2.0  # Windows: a file X-Plane holds open cannot be replaced; tried this long
PROBE_BYTES = 1 << 20


def too_many_links(e: OSError) -> bool:
    """Whether ``e`` says the file has as many names as its disk allows."""
    return e.errno == errno.EMLINK or getattr(e, "winerror", None) == ERROR_TOO_MANY_LINKS


def pwrite(fd: int, data: bytes, offset: int) -> None:
    """``data`` written at ``offset``: ``os.pwrite``, which Windows lacks."""
    if hasattr(os, "pwrite"):
        os.pwrite(fd, data, offset)
        return
    os.lseek(fd, offset, os.SEEK_SET)
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def replace(src: str | Path, dst: str | Path) -> None:
    """``src`` renamed over ``dst``, atomically: X-Plane opens either the old file or the new one.
    On Windows a file open in another program cannot be replaced: tried again for a moment."""
    if not WINDOWS:
        os.replace(src, dst)
        return
    deadline = time.monotonic() + REPLACE_TRIES_S
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.02)


def _windows_ioctl(fd: int, code: int, payload: bytes = b"") -> bool:
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    returned = ctypes.c_ulong(0)
    buf = ctypes.create_string_buffer(payload) if payload else None
    return bool(
        kernel32.DeviceIoControl(
            ctypes.c_void_p(msvcrt.get_osfhandle(fd)),  # type: ignore[attr-defined]
            ctypes.c_ulong(code),
            buf,
            ctypes.c_ulong(len(payload)),
            None,
            ctypes.c_ulong(0),
            ctypes.byref(returned),
            None,
        )
    )


def make_sparse(fd: int) -> bool:
    """Mark the file sparse, so that what is never written takes no disk: NTFS needs it asked;
    APFS, ext4, btrfs and xfs need nothing. False where it failed."""
    if not WINDOWS:
        return True
    try:
        return _windows_ioctl(fd, FSCTL_SET_SPARSE)
    except (OSError, AttributeError, ImportError):
        return False


@cache
def _linux_fallocate():
    libc = ctypes.CDLL(None, use_errno=True)
    fallocate = libc.fallocate
    fallocate.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int64, ctypes.c_int64)
    fallocate.restype = ctypes.c_int
    return fallocate


def punch_hole(fd: int, start: int, end: int) -> bool:
    """Give back the disk under [start, end), whole 4 KB blocks only: a hole, which reads as zeros.
    False where the disk cannot.

    APFS allocates the gap when a file is extended (by ``ftruncate`` or by writing past its end), at
    no I/O cost; only ``F_PUNCHHOLE`` makes it a hole again (measured: 0.3 ms and 47 KB a
    placeholder, against 11 MB). ext4 leaves the gap a hole already; NTFS once the file is sparse
    (:func:`make_sparse`)."""
    start = (start + BLOCK - 1) // BLOCK * BLOCK
    end = end // BLOCK * BLOCK
    if end <= start:
        return True
    try:
        if WINDOWS:
            return _windows_ioctl(fd, FSCTL_SET_ZERO_DATA, struct.pack("<qq", start, end))
        if sys.platform == "darwin":
            import fcntl

            fcntl.fcntl(fd, F_PUNCHHOLE, struct.pack("=IIqq", 0, 0, start, end - start))
            return True
        if sys.platform.startswith("linux"):
            mode = FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE
            return _linux_fallocate()(fd, mode, start, end - start) == 0
    except (OSError, AttributeError, ImportError):
        return False
    return False


def allocated(path: str | Path) -> int:
    """The bytes a file takes on the disk (its holes not counted)."""
    if WINDOWS:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        high = ctypes.c_ulong(0)
        size_fn = kernel32.GetCompressedFileSizeW
        size_fn.restype = ctypes.c_ulong
        low = size_fn(ctypes.c_wchar_p(str(path)), ctypes.byref(high))
        return (high.value << 32) | low
    return os.stat(path).st_blocks * 512


@dataclass(frozen=True)
class Abilities:
    """What a disk can do for Live's pack (:func:`abilities`)."""

    clones: bool  # a copy sharing its source's blocks: APFS, btrfs, xfs
    links: bool  # hard links: every disk but exFAT and FAT
    holes: bool  # a file's unwritten part takes no disk: APFS, NTFS, ext4, btrfs, xfs
    link_max: int  # names a file may have

    @property
    def holds_pack(self) -> bool:
        """Whether Live's folder can be there: names for the textures round the aircraft, and their
        unbuilt levels shared or left out (the module's docstring)."""
        return self.links and (self.clones or self.holes)


def _link_max(folder: Path) -> int:
    if WINDOWS:
        return NTFS_LINK_MAX
    try:
        return int(os.pathconf(folder, "PC_LINK_MAX"))
    except (OSError, ValueError, AttributeError):
        return NTFS_LINK_MAX  # the least any disk with links allows


def _holes_work(path: Path) -> bool:
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | BINARY, 0o644)
    try:
        make_sparse(fd)
        os.ftruncate(fd, PROBE_BYTES)
        pwrite(fd, b"x", PROBE_BYTES - 1)
        punch_hole(fd, 0, PROBE_BYTES - BLOCK)
        os.fsync(fd)
    finally:
        os.close(fd)
    return allocated(path) < PROBE_BYTES // 2


def abilities(folder: Path) -> Abilities:
    """What the disk of ``folder`` (which must exist) can do, tried there with small files deleted
    after. Nothing where it cannot write."""
    stem = folder / f".live-probe-{os.getpid()}-{threading.get_ident()}"
    probe, twin, name, hole = (Path(f"{stem}{end}") for end in ("", ".clone", ".link", ".hole"))
    clones = links = holes = False
    try:
        probe.write_bytes(b"x")
        with contextlib.suppress(OSError):
            clone(str(probe), str(twin))
            clones = True
        with contextlib.suppress(OSError):
            os.link(probe, name)
            links = True
        with contextlib.suppress(OSError):
            holes = _holes_work(hole)
    except OSError:
        pass
    finally:
        for f in (twin, name, hole, probe):
            with contextlib.suppress(OSError):
                f.unlink()
    return Abilities(clones, links, holes, _link_max(folder) if links else 0)


def place_file(src: Path, dst: Path, *, symlink: bool = False) -> str:
    """``dst`` made the same file as ``src``, as cheaply as the disks allow: a clone, else a hard
    link (both on the same disk only), else a symbolic link where ``symlink`` (never on Windows,
    where it needs Developer Mode), else a copy. Returns which: "cloned", "linked", "symlinked",
    "copied". ``dst`` must not exist."""
    try:
        clone(str(src), str(dst))
        return "cloned"
    except OSError as e:
        if e.errno not in NO_CLONE:
            raise
    try:
        os.link(src, dst)
        return "linked"
    except FileExistsError:
        raise
    except OSError:
        pass
    if symlink and not WINDOWS:
        os.symlink(src, dst)
        return "symlinked"
    # a copy beside it, put in whole or not at all, never over another file
    tmp = dst.with_name(f".live-tmp-{os.getpid()}-{threading.get_ident()}-{dst.name}")
    shutil.copyfile(src, tmp)
    try:
        if os.path.lexists(dst):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(dst))
        os.replace(tmp, dst)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
    return "copied"


def folder_link(target: Path, link: Path) -> None:
    """``link`` made a link to the folder ``target``: a symbolic link, or a junction on Windows
    without Developer Mode (OrthoStudio XP's own, :mod:`orthostudio.install.packs`)."""
    from orthostudio.install import packs

    packs._make_link(Path(target), Path(link))


def is_folder_link(path: Path) -> bool:
    """A symbolic link, or on Windows a junction."""
    from orthostudio.install import packs

    return packs.is_link(Path(path))


def remove_folder_link(path: Path) -> None:
    """The link removed, never the folder it leads to."""
    from orthostudio.install import packs

    packs._remove_link(Path(path))


def link_target(path: Path) -> Path:
    """Where a folder link leads: a symbolic link's target, or a junction's (Windows's ``\\\\?\\``
    prefix dropped)."""
    target = os.readlink(path)
    if target.startswith("\\\\?\\"):
        target = target[4:]
    return Path(path).parent / target
