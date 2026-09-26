# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP Live's X-Plane plugin (``native/xplane_plugin``): it makes X-Plane wait, while a
flight loads, until the streamer has built the ortho around the start (see
``orthostudio.live.gate``).

An installer will ship it prebuilt in ``orthostudio/bin``, as it does Triangle4XP and DSFTool. A
checkout of the repository builds it here, with clang, against the XPLM framework of the X-Plane it
goes into, into ``native/xplane_plugin/build``. The streamer installs it in ``Resources/plugins``
together with the pack, and takes it out again once X-Plane has quit: X-Plane loads plugins at
start-up only.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from orthostudio.fsutil import NO_CONSOLE_WINDOW
from orthostudio.programs import PACKAGE_BIN

NATIVE = Path(__file__).resolve().parents[3] / "native" / "xplane_plugin"  # in a checkout
SOURCE = NATIVE / "live_gate.c"
PANEL = NATIVE / "live_panel.m"  # its loading window, in Objective-C
BUILD_DIR = NATIVE / "build"
NAME = "OrthoStudio XP Live"
PREBUILT = PACKAGE_BIN / f"{NAME}.xpl"


def plugin_dir(xplane: Path) -> Path:
    return xplane / "Resources" / "plugins" / NAME


def _xpl(root: Path) -> Path:
    return root / "mac_x64" / f"{NAME}.xpl"  # X-Plane's folder for Mac plugins, universal ones too


def compile_plugin(out: Path, frameworks: Path, port: int | None = None, extra: tuple = ()) -> None:
    """Compile the plugin to ``out``: arm64 and x86_64, linked to ``frameworks``/XPLM.framework."""
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
    """The plugin an installer shipped, else the one built from the checkout's sources, compiled
    again when they are newer."""
    if PREBUILT.is_file():
        return PREBUILT
    out = _xpl(BUILD_DIR / NAME)
    if not out.exists() or out.stat().st_mtime < max(SOURCE.stat().st_mtime, PANEL.stat().st_mtime):
        compile_plugin(out, xplane / "Resources" / "plugins")
    return out


def install(xplane: Path, log: Callable[[str], None] = print) -> bool:
    """Put the plugin in X-Plane. False (and why) if it cannot be built: X-Plane then does not wait
    for the ortho, and the streamer reloads the scenery after a start instead."""
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


def uninstall(xplane: Path) -> None:
    folder = plugin_dir(xplane)
    xpl = _xpl(folder)
    if xpl.exists():
        xpl.unlink()
    for d in (xpl.parent, folder):  # only if nothing else is in them
        with contextlib.suppress(OSError):
            d.rmdir()
