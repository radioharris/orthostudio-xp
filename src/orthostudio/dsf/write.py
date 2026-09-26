# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Writing the DSF: temporary file, rename, ``.bak`` of the previous file.

``O4_DSF_Utils.py:1053-1057`` and ``:1330-1346``; spec ``docs/specs/dsf-encoding.md`` 3.8.
"""

from __future__ import annotations

import os
from pathlib import Path

from orthostudio.dsf.encode import DsfBuild
from orthostudio.fsutil import atomic_write_bytes

__all__ = ["write_dsf"]


def write_dsf(path: Path, build: DsfBuild, *, backup: bool = True) -> None:
    """Write ``build.data`` at ``path`` atomically; an existing file becomes ``<name>.bak``."""
    path = Path(path)
    if backup and path.exists():
        os.replace(path, path.with_name(path.name + ".bak"))
    atomic_write_bytes(path, build.data)
