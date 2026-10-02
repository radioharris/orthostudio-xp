# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP Live's X-Plane plugin (``native/xplane_plugin``): it makes X-Plane wait, while a
flight loads, until the streamer has built the ortho around the start (see
``orthostudio.live.gate``).

An installer will ship it prebuilt in ``orthostudio/bin``, as it does Triangle4XP and DSFTool. A
checkout of the repository on a Mac builds it here, with clang, against the XPLM framework of the
X-Plane it goes into, into ``native/xplane_plugin/build`` (one file for Apple Silicon and Intel
Macs). Windows and Linux computers seldom have a compiler: their plugins are built ahead, on any
computer with Docker (``tools/live/plugin_builds.py``), and kept with the sources in
``native/xplane_plugin/prebuilt``, with the digest of the sources they were built from
(:func:`sources_digest`, which the tests check). The streamer installs it in ``Resources/plugins``
together with the pack, in X-Plane's folder for the system (``mac_x64``, ``win_x64``,
``lin_x64``), and takes it out again once X-Plane has quit: X-Plane loads plugins at start-up
only.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from orthostudio.fsutil import NO_CONSOLE_WINDOW
from orthostudio.programs import PACKAGE_BIN

NATIVE = Path(__file__).resolve().parents[3] / "native" / "xplane_plugin"  # in a checkout
SOURCE = NATIVE / "live_gate.c"
PANEL = NATIVE / "live_panel.m"  # its loading window on the Mac, in Objective-C
WINDOWS_PANEL = NATIVE / "live_panel_win.c"
LINUX_PANEL = NATIVE / "live_panel_x11.c"
XPLM_DEF = NATIVE / "XPLM_64.def"  # the XPLM functions it calls, for the Windows import library
BUILD_DIR = NATIVE / "build"
PREBUILT_DIR = NATIVE / "prebuilt"  # the Windows and Linux plugins, built ahead
SOURCES = PREBUILT_DIR / "SOURCES"  # the digest of the sources they were built from
NAME = "OrthoStudio XP Live"
PREBUILT = PACKAGE_BIN / f"{NAME}.xpl"  # an installer's, for the system it installs on

# What the Windows and Linux plugins are built from, and how (tools/live/plugin_builds.py): a
# change to either makes them stale.
LINUX_BUILD = (
    "gcc -O2 -Wall -Wextra -Werror -fPIC -shared -fvisibility=hidden "
    "live_gate.c live_panel_x11.c -ldl"
)
# MinGW's own C99 printf (always ending a cut string), and no symbols: a tenth of the size.
WINDOWS_BUILD = (
    "x86_64-w64-mingw32-dlltool -d XPLM_64.def -l libXPLM_64.a && "
    "x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -Werror -D__USE_MINGW_ANSI_STDIO=1 -shared "
    "-static-libgcc -s live_gate.c live_panel_win.c libXPLM_64.a -lws2_32 -luser32 -lgdi32"
)


def platform_folder(platform: str | None = None) -> str:
    """X-Plane's folder for a plugin's file on this system (or ``platform``, a ``sys.platform``):
    ``mac_x64`` on every Mac, Apple Silicon too (the file holds both), ``win_x64``, ``lin_x64``."""
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return "mac_x64"
    return "win_x64" if platform == "win32" else "lin_x64"


def plugin_dir(xplane: Path) -> Path:
    return xplane / "Resources" / "plugins" / NAME


def _xpl(root: Path, platform: str | None = None) -> Path:
    return root / platform_folder(platform) / f"{NAME}.xpl"


def sources_digest() -> str:
    """The digest of what the Windows and Linux plugins are built from: their sources, the import
    list and the build commands."""
    h = hashlib.sha256()
    for path in (SOURCE, WINDOWS_PANEL, LINUX_PANEL, XPLM_DEF):
        h.update(path.name.encode() + b"\0" + path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    h.update(LINUX_BUILD.encode() + b"\0" + WINDOWS_BUILD.encode())
    return h.hexdigest()


def compile_plugin(out: Path, frameworks: Path, port: int | None = None, extra: tuple = ()) -> None:
    """Compile the Mac plugin to ``out``: arm64 and x86_64 (Apple Silicon and Intel Macs), linked
    to ``frameworks``/XPLM.framework."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    cmd = [
        "clang",
        "-O2",
        "-Wall",
        "-fobjc-arc",
        "-arch",
        "arm64",
        "-arch",
        "x86_64",
        "-mmacosx-version-min=11.0",
        "-dynamiclib",
        "-fvisibility=hidden",
        f"-Wl,-install_name,@loader_path/{NAME}.xpl",
        "-F",
        str(frameworks),
        "-framework",
        "XPLM",
        "-framework",
        "Cocoa",
        "-framework",
        "QuartzCore",
    ]
    if port is not None:
        cmd.append(f"-DLIVE_PORT={port}")
    cmd += [*extra, "-o", str(tmp), str(SOURCE), str(PANEL)]
    done = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_CONSOLE_WINDOW)
    if done.returncode != 0:
        raise RuntimeError(f"clang failed:\n{done.stderr.strip()}")
    os.replace(tmp, out)


def built(xplane: Path) -> Path:
    """The plugin an installer shipped; else, on a Mac, the one built from the checkout's sources,
    compiled again when they are newer; on Windows and Linux, the one built ahead."""
    if PREBUILT.is_file():
        return PREBUILT
    if sys.platform != "darwin":
        ahead = _xpl(PREBUILT_DIR)
        if not ahead.is_file():
            raise RuntimeError(f"no plugin built for this system ({ahead} is missing)")
        return ahead
    out = _xpl(BUILD_DIR / NAME)
    if not out.exists() or out.stat().st_mtime < max(SOURCE.stat().st_mtime, PANEL.stat().st_mtime):
        compile_plugin(out, xplane / "Resources" / "plugins")
    return out


def availability() -> str:
    """``ready`` (shipped, built ahead, or built already), ``to_build`` (on a Mac, at the start,
    with Xcode's command-line tools), or ``missing``: X-Plane will not wait for the ortho at a
    start."""
    if PREBUILT.is_file() or _xpl(BUILD_DIR / NAME).is_file():
        return "ready"
    if sys.platform != "darwin":
        return "ready" if _xpl(PREBUILT_DIR).is_file() else "missing"
    return "to_build" if SOURCE.is_file() else "missing"


def install(xplane: Path, log: Callable[[str], None] = print) -> bool:
    """Put the plugin in X-Plane. False (and why) if it cannot be built: X-Plane then does not wait
    for the ortho."""
    try:
        src = built(xplane)
    except (OSError, RuntimeError) as e:
        log(f"the X-Plane plugin could not be built, X-Plane will not wait for the ortho: {e}")
        return False
    dest = _xpl(plugin_dir(xplane))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)
    return True


def installed(xplane: Path) -> bool:
    return _xpl(plugin_dir(xplane)).exists()


def uninstall(xplane: Path) -> bool:
    """The plugin out of X-Plane. False where it stays: Windows keeps the file of a plugin that a
    program has loaded until that program has quit, so in the seconds X-Plane takes to go."""
    folder = plugin_dir(xplane)
    xpl = _xpl(folder)
    if xpl.exists():
        try:
            xpl.unlink()
        except PermissionError:
            return False
    for d in (xpl.parent, folder):  # only if nothing else is in them
        with contextlib.suppress(OSError):
            d.rmdir()
    return True
