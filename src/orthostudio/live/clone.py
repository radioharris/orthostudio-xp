# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""APFS clones: a copy that shares the source's blocks, so thousands of 11 MB textures cost no
space."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import os
import shutil

_libc = ctypes.CDLL(None, use_errno=True)
try:
    _clonefile = _libc.clonefile
    _clonefile.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32)
    _clonefile.restype = ctypes.c_int
except AttributeError:  # pragma: no cover - not macOS
    _clonefile = None


def clone(src: str, dst: str) -> None:
    """Clone ``src`` to ``dst`` (which must not exist). Raises OSError when the volume cannot."""
    if _clonefile is None:
        raise OSError(errno.ENOTSUP, "clonefile() needs macOS", dst)
    if _clonefile(os.fsencode(src), os.fsencode(dst), 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), dst)


def clone_or_copy(src: str, dst: str) -> None:
    """Clone when the volume can, copy otherwise. For small files only."""
    try:
        clone(src, dst)
    except OSError as e:
        if e.errno not in (errno.ENOTSUP, errno.EXDEV):
            raise
        shutil.copyfile(src, dst)


def replace_with_clone(src: str, dst: str) -> None:
    """Atomically replace ``dst`` by a clone of ``src``: clone beside it, then rename() over it."""
    tmp = os.path.join(os.path.dirname(dst), f".live-tmp-{os.getpid()}-{os.path.basename(dst)}")
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp)
    clone(src, tmp)
    os.rename(tmp, dst)
