# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Clones: a copy that shares the source's blocks, so a texture built only to a small level keeps
the neutral texture's larger ones at no cost. APFS on a Mac (``clonefile``), btrfs and xfs on Linux
(the ``FICLONE`` ioctl). NTFS, ext4 and Mac OS Extended have none: a texture there keeps its unbuilt
levels as a hole instead (:mod:`orthostudio.live.filesystem`)."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import os
import shutil
import sys
import threading
from pathlib import Path

_clonefile = None
if sys.platform == "darwin":
    _libc = ctypes.CDLL(None, use_errno=True)
    with contextlib.suppress(AttributeError):
        _clonefile = _libc.clonefile
        _clonefile.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32)
        _clonefile.restype = ctypes.c_int

FICLONE = 0x40049409  # <linux/fs.h>: _IOW(0x94, 9, int)
# what a disk that cannot clone answers: the caller writes the file another way
NO_CLONE = frozenset(
    {errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS, errno.EINVAL, errno.ENOTTY}
)


def clone(src: str, dst: str) -> None:
    """Clone ``src`` to ``dst`` (which must not exist). Raises OSError when the disk cannot, its
    errno in :data:`NO_CLONE`."""
    if _clonefile is not None:
        if _clonefile(os.fsencode(src), os.fsencode(dst), 0) != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), dst)
        return
    if sys.platform.startswith("linux"):
        import fcntl

        with open(src, "rb") as source:
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                fcntl.ioctl(fd, FICLONE, source.fileno())
            except OSError:
                os.close(fd)
                os.unlink(dst)
                raise
            os.close(fd)
        return
    raise OSError(errno.ENOTSUP, "this system clones no files", dst)


def clones_work(folder: Path) -> bool:
    """Whether files in ``folder`` can be cloned: APFS, btrfs or xfs, writable."""
    probe = folder / f".live-probe-{os.getpid()}-{threading.get_ident()}"
    twin = Path(f"{probe}.clone")
    try:
        probe.write_bytes(b"x")
        clone(str(probe), str(twin))
        return True
    except OSError:
        return False
    finally:
        for f in (twin, probe):
            with contextlib.suppress(OSError):
                f.unlink()


def clone_or_copy(src: str, dst: str) -> None:
    """Clone when the disk can, copy otherwise. For small files only."""
    try:
        clone(src, dst)
    except OSError as e:
        if e.errno not in NO_CLONE:
            raise
        shutil.copyfile(src, dst)


def same_disk(a: str | Path, b: str | Path) -> bool:
    """Whether ``a`` and ``b`` are on the same volume, where one can be cloned into the other."""
    return os.stat(a).st_dev == os.stat(b).st_dev


def replace_with_clone(src: str, dst: str) -> None:
    """Atomically replace ``dst`` by a clone of ``src``: clone beside it, then replace it."""
    from .filesystem import replace

    tmp = os.path.join(os.path.dirname(dst), f".live-tmp-{os.getpid()}-{os.path.basename(dst)}")
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp)
    clone(src, tmp)
    replace(tmp, dst)
