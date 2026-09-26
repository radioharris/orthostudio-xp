# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Turn a capture of the page into the pictures the README and the store listing show.

A capture is the whole screen: the menu bar, the browser's tabs and address bar, the page, and the
dock. Published is the page alone, with two things put right:

* the status bar at the foot is cut off. It names the X-Plane folder and the data folder, which
  carry the home folder of whoever took the capture, and nobody reading a store listing needs them;
* the version beside the brand is the one that took the capture, and the pictures are made from the
  build about to become the release. The digits that change are painted over and written again, at
  the size and on the baseline measured from the ones that were there, so the rest of the line is
  the browser's own rendering, untouched.

The pictures of the README (``--profile docs``) are PNG, whatever they hold and whatever they
weigh. Those of the store listing (``--profile store``) are written as PNG or as JPEG, whichever
suits what each holds, under ``MAX_BYTES``, and the set stays under ``MAX_TOTAL``, which is what
the listing accepts for its pictures together.

Nothing else is retouched, and a new release costs one command, which is why none of this is done
by hand.

Usage (from the repository root)::

    python tools/screenshots/prepare.py ~/Desktop/captures --out ~/Desktop/store --version 0.1.5
    python tools/screenshots/prepare.py ~/Desktop/captures --out docs/images --profile docs \\
        --names plan,cost,works,library,settings

The captures are expected in the dark theme, in Chrome, taken with the whole screen (the colours
below were measured there); each may be at its own browser zoom, which is why nothing here is a
fixed position. When something is not where the page keeps it, the script says so instead of
painting over the wrong thing.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

PAGE_COLOURS = ((32, 34, 37), (24, 25, 27))
"""The page's bars and its body, dark theme: what the crop looks for."""

BROWSER_CHROME = (60, 60, 60)
"""Chrome's own tabs and address bar, dark theme: the page starts under the last row of them."""

TOLERANCE = 6
"""How far a pixel may be from one of those colours and still count as it."""

UI_FONT = "/System/Library/Fonts/SFNS.ttf"
"""The page asks for ``system-ui``; on macOS that is this one."""

DIM_TEXT = 150
"""The version is written in the page's faintest ink (``--fg-3``). The brand beside it reaches 228
and the tabs 172, so the first run of ink that never gets brighter than this is the version."""

TITLE_BAR = 200
"""How far below the first band of page colour the page's own bar may still be, in pixels: a
window's title bar stands between them and is about 54 px tall at twice the scale."""

STATUS_BAR = (30, 100)
"""How tall the status bar may be, in pixels, for the crop to trust what it found."""

JPEG_ABOVE = 60_000
"""More distinct colours than this and the picture is aerial imagery, which JPEG keeps far smaller;
below it the picture is flat user interface, which PNG keeps sharper and smaller."""

MAX_BYTES = 1_900_000
"""What one picture must stay under. A picture of the page weighs 300 to 600 KB, so nothing is
usually done; one that goes over is written again as JPEG, at a lower quality and then smaller,
until it fits."""

MAX_TOTAL = 1_800_000
"""What the whole set must stay under: the store listing takes 1.9 MB for its pictures together
(2026-09-19), and this leaves a margin under it. The photographic ones (the map) are written again
at a lower quality until the set fits; the flat ones are left alone, since a screen of text is what
a lower quality ruins first."""

QUALITIES = (88, 82, 74, 66, 58)


@dataclass(frozen=True)
class Found:
    """Where a piece of text sits: its ink box, and the box of each of its glyphs."""

    box: tuple[int, int, int, int]
    glyphs: tuple[tuple[int, int], ...]
    background: tuple[int, int, int]
    colour: tuple[int, int, int]


def _matches(pixels: np.ndarray, colour: Sequence[int], tolerance: int = TOLERANCE) -> np.ndarray:
    return np.abs(pixels - np.array(colour)).max(axis=2) <= tolerance


def page_box(pixels: np.ndarray) -> tuple[int, int, int, int]:
    """Where the page is in a capture, as ``(left, top, right, bottom)``.

    Two kinds of capture reach here. In one, the page is a tab of the whole screen: the browser's
    own grey is the landmark, and under its last row the page opens with its bar. In the other,
    since 0.1.8, the page is the app's own window, and there is no browser at all: the search then
    starts at the top of the capture. The window's title bar is no obstacle, being a grey of macOS
    and not one of the page (44,43,44 against 32,34,37, measured), so it falls outside on its own,
    and so does the desktop around the window.

    Either way the page opens with its bar, which is one of the page colours across the width. The
    bottom is the last such row, the status bar; the sides come from the bar itself, which is why a
    window narrower than the screen needs nothing said about it.
    """
    page = np.zeros(pixels.shape[:2], bool)
    for colour in PAGE_COLOURS:
        page |= _matches(pixels, colour)
    share = page.mean(axis=1)
    chrome = np.where(_matches(pixels, BROWSER_CHROME).mean(axis=1) >= 0.5)[0]
    under = int(chrome.max()) + 1 if chrome.size else 0
    tops = [row for row in range(under, len(share) - 12) if share[row : row + 12].min() >= 0.5]
    if not tops:
        raise LookupError("no page under the browser bars: is the page in its dark theme?")
    top = tops[0]
    # The page draws its bars in its own colours, and a capture keeps them exactly. The grey macOS
    # paints a window's title bar with only comes close: 35,34,35 against the page's 32,34,37 on
    # one capture and 44,43,44 on the next, measured, the first of which the tolerance that finds
    # a bar would swallow whole. So the top is taken from the exact colour, which the title bar
    # never has; a capture whose colours have been through JPEG has none either, and keeps what
    # the tolerance found.
    exact = _matches(pixels, PAGE_COLOURS[0], tolerance=0).mean(axis=1)
    page_bar = [row for row in range(top, min(top + TITLE_BAR, len(exact))) if exact[row] >= 0.5]
    if page_bar:
        top = page_bar[0]
    bottom = max(row for row in np.where(share >= 0.5)[0] if share[row - 12 : row + 1].min() >= 0.5)
    columns = np.where(page[top : top + 40].mean(axis=0) >= 0.95)[0]
    return int(columns.min()), top, int(columns.max()) + 1, int(bottom) + 1


def status_bar_top(pixels: np.ndarray) -> int:
    """The first row of the status bar, the band of bar colour the page ends with.

    It is read from the foot upwards: the bar is its own colour from edge to edge, the line above it
    is not. What sits above is the body, or the map, and neither is that colour across the width.
    """
    bar = _runs(_matches(pixels, PAGE_COLOURS[0]).mean(axis=1) >= 0.7, gap=3, least=10)
    if not bar or len(pixels) - bar[-1][1] > 8:
        raise LookupError("the page does not end with its status bar")
    top = bar[-1][0]
    height = len(pixels) - top
    if not STATUS_BAR[0] <= height <= STATUS_BAR[1]:
        raise LookupError(f"the status bar measures {height} px, which is not a status bar")
    return top


def _runs(flags: np.ndarray, gap: int, least: int) -> list[tuple[int, int]]:
    """Runs of true columns, joined across ``gap`` false ones, keeping those ``least`` wide."""
    columns = np.where(flags)[0]
    if not columns.size:
        return []
    out, start = [], columns[0]
    for previous, column in pairwise(columns):
        if column - previous > gap:
            out.append((int(start), int(previous)))
            start = column
    out.append((int(start), int(columns[-1])))
    return [(first, last) for first, last in out if last - first + 1 >= least]


def find_version(page: Image.Image) -> Found:
    """The version beside the brand, found by being the palest ink in the page's top bar."""
    strip = np.array(page.crop((0, 0, min(page.width, 900), 70)).convert("RGB")).astype(int)
    light = strip.mean(axis=2)
    background = float(np.median(light))
    brightest = light.max(axis=0)
    dim = _runs((brightest > background + 25) & (brightest <= DIM_TEXT), gap=8, least=15)
    if not dim:
        raise LookupError("no version beside the brand: is this the page's top bar?")
    first, last = dim[0]
    # Only the rows the version itself covers: the line that closes the bar runs under it, and it
    # is ink as faint as the version's own.
    inked = (light[:, first : last + 1] > background + 18).any(axis=1)
    bands = _runs(inked, gap=1, least=4)
    if not bands:
        raise LookupError("the version beside the brand has no rows of its own")
    top, bottom = max(bands, key=lambda band: band[1] - band[0])
    shape = (last - first + 1) / (bottom - top + 1)
    if not 2.5 <= shape <= 5.5:
        raise LookupError(f"what stands beside the brand is {shape:.1f} times as wide as tall")
    ink = light[top : bottom + 1, first : last + 1] > background + 18
    glyphs = tuple((first + a, first + b) for a, b in _runs(ink.any(axis=0), gap=1, least=1))
    body = strip[top : bottom + 1, first : last + 1].reshape(-1, 3)
    return Found(
        box=(first, top, last + 1, bottom + 1),
        glyphs=glyphs,
        background=tuple(int(v) for v in np.median(strip.reshape(-1, 3), axis=0).round()),
        colour=tuple(int(v) for v in body[body.sum(axis=1).argsort()[-20:]].mean(axis=0).round()),
    )


def _fitted_to_height(text: str, height: int) -> ImageFont.FreeTypeFont:
    """The font at the size where ``text`` inks exactly ``height`` rows."""
    low, high = 4.0, 80.0
    for _ in range(20):
        size = (low + high) / 2
        box = ImageFont.truetype(UI_FONT, size).getbbox(text)
        if box[3] - box[1] < height:
            low = size
        else:
            high = size
    return ImageFont.truetype(UI_FONT, (low + high) / 2)


def write_version(page: Image.Image, was: str, version: str) -> str:
    """Write ``version`` where the page shows ``was``, one glyph at a time."""
    old, new = f"v{was}", f"v{version}"
    if old == new:
        return "version unchanged"
    found = find_version(page)
    if len(found.glyphs) != len(old) or len(old) != len(new):
        raise LookupError(f"{old!r} has {len(found.glyphs)} marks in the picture, not {len(old)}")
    draw = ImageDraw.Draw(page)
    changed = []
    for glyph, before, after in zip(found.glyphs, old, new, strict=True):
        if before == after:
            continue
        left, right = glyph
        # The digits are tabular (``.num`` in the page's stylesheet), so the one written keeps the
        # place of the one painted out: same height, same baseline, centred on the same ink.
        font = _fitted_to_height(before, found.box[3] - found.box[1])
        box = font.getbbox(after)
        space = (left - 2, found.box[1] - 3, right + 2, found.box[3] + 3)
        draw.rectangle(space, fill=found.background)
        width = box[2] - box[0]
        x = (left + right + 1) / 2 - width / 2 - box[0]
        draw.text((x, found.box[1] - box[1]), after, font=font, fill=found.colour)
        changed.append(after)
    return f"{old} -> {new} at {found.box}, {len(changed)} mark(s) written again"


def prepare(source: Path, was: str, version: str, max_width: int | None) -> Image.Image:
    """One capture: cropped to the page, without its status bar, showing the version released."""
    image = Image.open(source).convert("RGB")
    page = image.crop(page_box(np.array(image)))
    print(f"  {write_version(page, was, version)}")
    pixels = np.array(page)
    page = page.crop((0, 0, page.width, status_bar_top(pixels)))
    if max_width is not None and page.width > max_width:
        page = page.resize((max_width, round(page.height * max_width / page.width)), Image.LANCZOS)
    return page


def _jpeg(image: Image.Image, target: Path, quality: int) -> Path:
    image.save(target, quality=quality, subsampling=0, optimize=True, progressive=True)
    return target


def save(image: Image.Image, folder: Path, name: str, png_only: bool = False) -> Path:
    """PNG or JPEG, whichever suits what the picture holds, and always under ``MAX_BYTES``.

    ``png_only`` for the pictures of the README, which are PNG whatever they hold and whatever
    they weigh: JPEG and the weights it is there to reach belong to the store listing, which sets
    them (the author, 2026-09-20).
    """
    folder.mkdir(parents=True, exist_ok=True)
    if png_only:
        target = folder / f"{name}.png"
        image.save(target, optimize=True)
        return target
    flat = image.getcolors(maxcolors=JPEG_ABOVE) is not None
    if flat:
        target = folder / f"{name}.png"
        image.save(target, optimize=True)
        if target.stat().st_size <= MAX_BYTES:
            return target
        target.unlink()  # too heavy for a flat picture: JPEG from here on
    target = folder / f"{name}.jpg"
    for quality in QUALITIES:
        if _jpeg(image, target, quality).stat().st_size <= MAX_BYTES:
            return target
    smaller = image
    while smaller.width > 800:
        smaller = smaller.resize((smaller.width * 4 // 5, smaller.height * 4 // 5), Image.LANCZOS)
        if _jpeg(smaller, target, QUALITIES[0]).stat().st_size <= MAX_BYTES:
            print(f"  narrowed to {smaller.width} px to stay under {MAX_BYTES // 1000} KB")
            return target
    raise RuntimeError(f"{name} does not fit in {MAX_BYTES} bytes")


def fit_total(written: list[tuple[Path, Image.Image]]) -> None:
    """Bring the whole set under ``MAX_TOTAL``: the quality of its photographs first, then, if
    that is not enough, the size of every picture, so the listing shows them all at one size."""
    photos = [(path, image) for path, image in written if path.suffix == ".jpg"]
    for quality in QUALITIES[1:]:
        total = sum(path.stat().st_size for path, _ in written)
        if total <= MAX_TOTAL or not photos:
            break
        for path, image in photos:
            _jpeg(image, path, quality)
        total = sum(path.stat().st_size for path, _ in written)
        print(f"the {len(photos)} photograph(s) written again at quality {quality}: "
              f"{total / 1e6:.2f} MB for the set")  # fmt: skip
    total = sum(path.stat().st_size for path, _ in written)
    if total <= MAX_TOTAL:
        return
    # Quality alone was not enough, which two pictures of the map instead of one made true
    # (0.1.8). Narrower at a quality that holds beats wider and muddy, since a listing shows them
    # far smaller than they are written and a low quality ruins the text over the map first.
    #
    # The photographs alone are narrowed. A flat picture narrowed comes out heavier, not lighter:
    # resampling turns its crisp flat colours into gradients that PNG cannot pack. Measured on
    # this set, Settings went from 453 KB at 3020 px to 508 KB at 1545 px, and narrowing the five
    # together weighed more than leaving three of them alone.
    photos = [(path, image.copy()) for path, image in photos]
    while photos and photos[0][1].width > 1600:
        photos = [
            (path, image.resize((image.width * 4 // 5, image.height * 4 // 5), Image.LANCZOS))
            for path, image in photos
        ]
        for path, image in photos:
            _jpeg(image, path, QUALITIES[1])
        total = sum(path.stat().st_size for path, _ in written)
        print(f"the {len(photos)} photograph(s) narrowed to {photos[0][1].width} px at quality "
              f"{QUALITIES[1]}: {total / 1e6:.2f} MB for the set")  # fmt: skip
        if total <= MAX_TOTAL:
            return
    raise RuntimeError(f"the set weighs {total / 1e6:.2f} MB, above {MAX_TOTAL / 1e6:.2f} MB")


def main(argv: Sequence[str] | None = None) -> int:
    here = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(here / "src"))
    from orthostudio import __version__

    parser = argparse.ArgumentParser(description="See the file's own documentation.")
    parser.add_argument("captures", type=Path, help="a folder of captures, or one capture")
    parser.add_argument("--out", type=Path, required=True, help="where the pictures are written")
    parser.add_argument("--version", default=__version__, help="the version they should show")
    parser.add_argument("--was", default=__version__, help="the version they were taken with")
    parser.add_argument("--profile", choices=("store", "docs"), default="store")
    parser.add_argument("--names", default="", help="docs: the names to write, in order")
    args = parser.parse_args(argv)

    sources = sorted(args.captures.glob("*.png")) if args.captures.is_dir() else [args.captures]
    if not sources:
        parser.error(f"no capture in {args.captures}")
    names = [name.strip() for name in args.names.split(",") if name.strip()]
    if names and len(names) != len(sources):
        parser.error(f"{len(names)} names for {len(sources)} captures")

    written: list[tuple[Path, Image.Image]] = []
    for index, source in enumerate(sources):
        print(source.name)
        page = prepare(source, args.was, args.version, None if args.profile == "store" else 1600)
        if names:
            name = names[index]
        elif args.profile == "store":
            name = f"orthostudio-xp-{args.version}-{source.stem}"
        else:
            name = source.stem
        target = save(page, args.out, name, png_only=args.profile == "docs")
        written.append((target, page))
        print(f"  {target} ({page.width}x{page.height}, {target.stat().st_size // 1024} KB)")
    if args.profile == "store":
        fit_total(written)
    for target, _ in written:
        print(f"{target.name}: {target.stat().st_size // 1024} KB")
    print(f"the set: {sum(t.stat().st_size for t, _ in written) / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
