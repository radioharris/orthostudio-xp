# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Make ``dist/OrthoStudio XP (aperçu Live).app``: OrthoStudio XP with OrthoStudio XP Live, as one
app, from this checkout of the ``live`` branch, beside the usual checkout's app.

It is ``checkout_app.py``'s app with three differences, so that the two never stand in each
other's way: its own name, its engine on its own port (``OSXP_PORT``, 8651, where the usual app's
takes 8641 and stops another OrthoStudio XP it finds there), and its own log
(``OSXP_LOG_NAME``). Both share the user's settings, tiles and Live's folder
(``~/.orthostudio``): one at a time.

    uv run python tools/package/live_preview_app.py
    # then drag "dist/OrthoStudio XP (aperçu Live).app" to Applications or the Dock
"""

from __future__ import annotations

import plistlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from checkout_app import EXECUTABLE, _shell_quote
from icon import make_icns

REPO = Path(__file__).resolve().parents[2]
APP_NAME = "OrthoStudio XP (aperçu Live)"
PORT = 8651
LOG_NAME = "serve-live-preview.log"

LAUNCHER = """#!/bin/bash
# OrthoStudio XP with OrthoStudio XP Live, the preview (tools/package/live_preview_app.py): this
# checkout's engine on a port and a log of its own, beside the usual checkout's app.
REPO={repo}
# the app itself, which opens the other window as this app (orthostudio.live.app_start)
export OSXP_APP="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 1
export OSXP_PORT={port}
export OSXP_LOG_NAME={log}
exec "$REPO/.venv/bin/python" -m orthostudio.desktop "$@"
"""


def make_app(out_dir: Path = REPO / "dist") -> Path:
    if sys.platform != "darwin":
        raise SystemExit("live_preview_app.py makes a macOS app: run it on macOS.")
    if not (REPO / ".venv" / "bin" / "orthostudio").is_file():
        raise SystemExit(
            "No .venv/bin/orthostudio in this checkout: run uv sync --all-extras first."
        )
    app = out_dir / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    launcher = macos / EXECUTABLE
    launcher.write_text(
        LAUNCHER.format(repo=_shell_quote(str(REPO)), port=PORT, log=LOG_NAME), encoding="utf-8"
    )
    launcher.chmod(0o755)
    make_icns(resources / f"{EXECUTABLE}.icns", EXECUTABLE)
    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "org.orthostudio-xp.launcher.live-preview",
        "CFBundleExecutable": EXECUTABLE,
        "CFBundleIconFile": EXECUTABLE,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1",
        "LSMinimumSystemVersion": "12.0",
        "LSArchitecturePriority": ["arm64"],  # a script launcher: not under Rosetta
        "LSRequiresNativeExecution": True,
        "NSHighResolutionCapable": True,
    }
    with (app / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump(info, f)
    return app


if __name__ == "__main__":
    print(make_app())
