"""Make ``dist/OrthoStudio XP.app`` for a checkout: a double-click on it starts this checkout's
OrthoStudio XP and opens its page, no terminal.

For a checkout of this repository on macOS (``uv sync`` done): the app runs the checkout's
``.venv/bin/orthostudio serve --open``, writing its log to
``~/Library/Logs/OrthoStudio XP/serve.log``, so it follows every change of the code. Opening it
while OrthoStudio XP already runs opens the running one's page; *Quit* in the page, or *Quit* on
the app's Dock icon, stops it. The installers, with Python inside, are ``build.py``'s.

    uv run python tools/package/checkout_app.py
    # then drag "dist/OrthoStudio XP.app" to Applications or the Dock
"""

from __future__ import annotations

import plistlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from icon import make_icns

REPO = Path(__file__).resolve().parents[2]
APP_NAME = "OrthoStudio XP"
EXECUTABLE = "orthostudio"
"""The launcher inside the app and its icon file: a name without a space."""

LAUNCHER = """#!/bin/bash
# OrthoStudio XP launcher (tools/package/checkout_app.py): starts this checkout's engine and
# opens its page.
REPO={repo}
LOGS="$HOME/Library/Logs/OrthoStudio XP"
mkdir -p "$LOGS"
cd "$REPO" || exit 1
echo "--- $(date '+%Y-%m-%d %H:%M:%S') OrthoStudio XP.app" >>"$LOGS/serve.log"
exec "$REPO/.venv/bin/orthostudio" serve --open >>"$LOGS/serve.log" 2>&1
"""


def make_app(out_dir: Path = REPO / "dist") -> Path:
    if sys.platform != "darwin":
        raise SystemExit("make_macos_app.py makes a macOS app: run it on macOS.")
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
    launcher.write_text(LAUNCHER.format(repo=_shell_quote(str(REPO))), encoding="utf-8")
    launcher.chmod(0o755)
    make_icns(resources / f"{EXECUTABLE}.icns", EXECUTABLE)
    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "org.orthostudio-xp.launcher",
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


def _shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    print(make_app())
