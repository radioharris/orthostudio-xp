# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The app icon of OrthoStudio XP: the page's logo (a frame and a relief line) on a blue rounded
square, drawn with Pillow for the macOS ``.icns``, the Windows ``.ico`` and the Linux ``.png``."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ICON_SIZES = (16, 32, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw_icon(size: int) -> Image.Image:
    """The logo on a rounded square, at ``size`` pixels."""
    scale = 4  # drawn large, then reduced: smooth edges without anti-aliased primitives
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    margin = big * 0.06
    d.rounded_rectangle(
        (margin, margin, big - margin, big - margin), radius=big * 0.2, fill=(33, 88, 140, 255)
    )

    def pt(x: float, y: float) -> tuple[float, float]:  # the logo's 24 x 24 grid, centred
        return (big * (0.14 + 0.72 * x / 24), big * (0.14 + 0.72 * y / 24))

    width = max(1, round(big * 0.05))
    d.rectangle((*pt(3, 5), *pt(21, 19)), outline=(255, 255, 255, 255), width=width)
    d.line(
        [pt(3, 12), pt(9, 12), pt(12, 8), pt(15, 14), pt(17, 12), pt(21, 12)],
        fill=(255, 255, 255, 255),
        width=width,
        joint="curve",
    )
    return img.resize((size, size), Image.Resampling.LANCZOS)


def make_icns(dest: Path, name: str = "orthostudio") -> None:
    """``dest``, a macOS icon (``iconutil``, so on macOS only)."""
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / f"{name}.iconset"
        iconset.mkdir()
        for s in ICON_SIZES[:-1]:
            draw_icon(s).save(iconset / f"icon_{s}x{s}.png")
            draw_icon(s * 2).save(iconset / f"icon_{s}x{s}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest)], check=True)


def make_ico(dest: Path) -> None:
    """``dest``, a Windows icon holding the usual sizes."""
    draw_icon(256).save(dest, format="ICO", sizes=[(s, s) for s in ICO_SIZES])


def make_png(dest: Path, size: int = 256) -> None:
    """``dest``, the icon as a PNG (Linux desktop entries)."""
    draw_icon(size).save(dest)
