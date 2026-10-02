# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Make ``dist/Vols de test Live.app``: the test flights' window (``bench.py``) of this checkout,
for the Dock, as ``tools/package/live_preview_app.py`` makes the preview's app.

    uv run python tools/live/flights/bench_app.py
    # then drag "dist/Vols de test Live.app" to Applications or the Dock
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "package"))

from checkout_app import EXECUTABLE, _shell_quote  # noqa: E402
from icon import ICON_SIZES, draw_icon  # noqa: E402

APP_NAME = "Vols de test Live"

LAUNCHER = """#!/bin/bash
# The test flights' window of OrthoStudio XP Live (tools/live/flights/bench_app.py): this
# checkout's tools/live/flights/bench.py.
REPO={repo}
cd "$REPO" || exit 1
exec "$REPO/.venv/bin/python" "$REPO/tools/live/flights/bench.py" "$@"
"""


def draw_bench_icon(size: int) -> Image.Image:
    """OrthoStudio XP's icon with an orange badge and an aircraft in its corner: told apart from
    the preview's own in the Dock."""
    img = draw_icon(size).convert("RGBA")
    scale = 4
    big = size * scale
    badge = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(badge)
    r = big * 0.22
    cx, cy = big * 0.74, big * 0.74
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(232, 120, 32, 255))

    def pt(x: float, y: float) -> tuple[float, float]:  # an aircraft on a 24 x 24 grid in it
        return (cx - r * 0.72 + r * 1.44 * x / 24, cy - r * 0.72 + r * 1.44 * y / 24)

    d.polygon(
        [pt(12, 2), pt(14, 9), pt(22, 13), pt(22, 15), pt(14, 13), pt(13.5, 19), pt(16, 21),
         pt(16, 22.5), pt(12, 21.5), pt(8, 22.5), pt(8, 21), pt(10.5, 19), pt(10, 13), pt(2, 15),
         pt(2, 13), pt(10, 9)],
        fill=(255, 255, 255, 255),
    )  # fmt: skip
    img.alpha_composite(badge.resize((size, size), Image.Resampling.LANCZOS))
    return img


def make_icns(dest: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / f"{EXECUTABLE}.iconset"
        iconset.mkdir()
        for s in ICON_SIZES[:-1]:
            draw_bench_icon(s).save(iconset / f"icon_{s}x{s}.png")
            draw_bench_icon(s * 2).save(iconset / f"icon_{s}x{s}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest)], check=True)


def make_app(out_dir: Path = REPO / "dist") -> Path:
    if sys.platform != "darwin":
        raise SystemExit("bench_app.py makes a macOS app: run it on macOS.")
    if not (REPO / ".venv" / "bin" / "python").is_file():
        raise SystemExit("No .venv in this checkout: run uv sync --all-extras first.")
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
    make_icns(resources / f"{EXECUTABLE}.icns")
    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "org.orthostudio-xp.launcher.live-test-flights",
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
