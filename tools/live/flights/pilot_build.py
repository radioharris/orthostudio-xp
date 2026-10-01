# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The test pilot plugin's files and builds (``native/xplane_test_pilot``): on a Mac it is built
where the runner runs (``fly.py``), with clang against X-Plane's XPLM framework; for Windows and
Linux ahead, with Docker (``tools/live/plugin_builds.py``), into ``prebuilt`` with the digest of
what it was built from (:func:`sources_digest`, which the tests check)."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

NAME = "OrthoStudio XP Test Pilot"
NATIVE = Path(__file__).resolve().parents[3] / "native" / "xplane_test_pilot"
SOURCE = NATIVE / "test_pilot.c"
XPLM_DEF = NATIVE / "XPLM_64.def"  # the XPLM functions it calls, for the Windows import library
BUILD_DIR = NATIVE / "build" / NAME  # the Mac's, built here
PREBUILT_DIR = NATIVE / "prebuilt"  # Windows' and Linux's, built ahead
SOURCES = PREBUILT_DIR / "SOURCES"
LINUX_BUILD = "gcc -O2 -Wall -Wextra -Werror -fPIC -shared -fvisibility=hidden test_pilot.c -lm"
WINDOWS_BUILD = (
    "x86_64-w64-mingw32-dlltool -d XPLM_64.def -l libXPLM_64.a && "
    "x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -Werror -D__USE_MINGW_ANSI_STDIO=1 -shared "
    "-static-libgcc -s test_pilot.c libXPLM_64.a"
)


def platform_folder(platform: str | None = None) -> str:
    """X-Plane's folder for a plugin's file on this system: mac_x64, win_x64, lin_x64."""
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return "mac_x64"
    return "win_x64" if platform == "win32" else "lin_x64"


def xpl(root: Path, platform: str | None = None) -> Path:
    return root / platform_folder(platform) / f"{NAME}.xpl"


def sources_digest() -> str:
    """The digest of what the Windows and Linux test pilots are built from."""
    h = hashlib.sha256()
    for path in (SOURCE, XPLM_DEF):
        h.update(path.name.encode() + b"\0" + path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    h.update(LINUX_BUILD.encode() + b"\0" + WINDOWS_BUILD.encode())
    return h.hexdigest()
