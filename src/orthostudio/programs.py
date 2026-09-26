# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Where OrthoStudio XP finds the two programs it runs: Triangle4XP and DSFTool.

An installer puts both in ``orthostudio/bin``, next to this module (``docs/specs/packaging.md``).
A checkout of the repository builds Triangle4XP into ``native/triangle4xp/build`` and ships DSFTool
in ``native/dsftool/<os>``; the modules that run them fall back on those.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["PACKAGE_BIN", "executable_name", "installed_program"]

PACKAGE_BIN = Path(__file__).resolve().parent / "bin"
"""The programs an installer ships with the package; absent from a checkout."""


def executable_name(name: str) -> str:
    """``Triangle4XP``, or ``Triangle4XP.exe`` on Windows."""
    return f"{name}.exe" if sys.platform == "win32" else name


def installed_program(name: str, *, bin_dir: Path | None = None) -> Path | None:
    """The program ``name`` an installer put in ``orthostudio/bin`` (or ``bin_dir`` in tests), if
    it is there and executable."""
    path = (PACKAGE_BIN if bin_dir is None else Path(bin_dir)) / executable_name(name)
    return path if path.is_file() and os.access(path, os.X_OK) else None
