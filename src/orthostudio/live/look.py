# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The season of the photos: their colours changed before a texture is encoded.

The photos are taken in summer, mostly. Live gives the ground the colours of a season (the user,
2026-10-13: spring, summer, autumn, winter, with sliders to change them): five values, applied
in this order to every pixel, each a deviation where 0 changes nothing:

- **brightness**, **contrast**, **saturation**: OrthoStudio XP's own arithmetic
  (``orthostudio.textures.colour``), here chained with the two below before one rounding;
- **temperature**: red up and blue down (warmer) or the reverse (colder), 12 % at the end of its
  scale;
- **vegetation**: what is green in the photo, and only that, turned towards yellow and russet
  (the hue turned by up to 75 degrees), or made a fresher green below 0 (its own saturation up
  to 60 % more). How green a pixel is comes from the photo as delivered (green above both red and
  blue, fully green 12 steps above), so brown fields, roofs, roads and water stay as they are.

No snow: white cannot be made from a summer photo without looking wrong (the user agreed,
2026-10-13). Summer is the photo as delivered unless the pilot moves its sliders.

**Where it is applied: to the encoded blocks** (:func:`look_blocks`). A BC1 block holds two
colours and, for each of its 16 pixels, a mix of them; the look changes the two colours, through
a table of every 5:6:5 colour (65,536, made in 4 ms). Applied to every pixel before the encoding
it cost as much as the encoding itself (0.30 s against 0.29 s for a 4096 px texture, 1.2 s
against 1.14 s for an 8192 px one, on the M4 Pro, 2026-10-13); on the blocks, 20 ms for a 4096
px texture, and the result is the same to the eye: 3.0 steps of 255 from the exact colours on
average where the per-pixel way and its encoding are 2.5 away (four places, three seasons).

The same arithmetic runs in the Live window's preview (``ui/look.js``), held equal by a test.
"""

from __future__ import annotations

import datetime
import functools
import math
from dataclasses import astuple, dataclass

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "DEFAULT_LOOKS",
    "SEASONS",
    "Look",
    "apply_look",
    "look_blocks",
    "season_on",
]

SEASONS = ("spring", "summer", "autumn", "winter")
LUMA = (0.299, 0.587, 0.114)  # ITU-R BT.601, as orthostudio.textures.colour
WARMTH = 0.12  # red and blue scaled by this much at temperature +1 or -1
GREEN_STEPS = 12.0  # green this far above red and blue: the vegetation slider's full weight
TURN_DEG = 75.0  # vegetation +1: the hue of what is green turned this far, to russet
FRESHER = 0.6  # vegetation -1: what is green this much more saturated
TROPICS = 23.44  # latitude: nearer the equator, no season, the photo as summer has it
_ROWS = 512  # rows converted to float at a time, as orthostudio.textures.colour


@dataclass(frozen=True)
class Look:
    """The five values of a season, each a deviation: 0 changes nothing."""

    brightness: float = 0.0
    contrast: float = 0.0
    saturation: float = 0.0
    temperature: float = 0.0
    vegetation: float = 0.0

    @property
    def unchanged(self) -> bool:
        return not any(astuple(self))

    def values(self) -> tuple[float, float, float, float, float]:
        return astuple(self)  # type: ignore[return-value]


# Measured on Sentinel-2 (2026-10-13): the same places in July and in January, April and October,
# cloud- and snow-free, light taken out (X-Plane lights the ground by the sun of the flight's date).
# Most of a season's change is field by field (a crop grown, harvested, ploughed), which no colour
# can guess: the best filter reproduced a third of July to January (the light, mostly: 1 % once
# it is out), less than 6 % of July to April and nothing of July to October. What is common:
# green turns olive in winter (much), a little yellower and paler in April and October. Summer is
# the driest season in the south of France: the photos, mostly summer's, stay as delivered
# (docs/specs/live.md, "The season").
DEFAULT_LOOKS: dict[str, Look] = {
    "spring": Look(vegetation=0.15),
    "summer": Look(),
    "autumn": Look(saturation=-0.1, vegetation=0.25),
    "winter": Look(saturation=-0.15, vegetation=0.6),
}


def _hue_turn(deg: float) -> NDArray[np.float32]:
    """The matrix turning a colour's hue by ``deg`` round the grey axis, its BT.601 grey kept
    (the one of CSS's hue-rotate filter): green goes towards yellow for a positive angle."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array(
        [
            [
                0.299 + 0.701 * c + 0.168 * s,
                0.587 - 0.587 * c + 0.330 * s,
                0.114 - 0.114 * c - 0.497 * s,
            ],
            [
                0.299 - 0.299 * c - 0.328 * s,
                0.587 + 0.413 * c + 0.035 * s,
                0.114 - 0.114 * c + 0.292 * s,
            ],
            [
                0.299 - 0.300 * c + 1.250 * s,
                0.587 - 0.588 * c - 1.050 * s,
                0.114 + 0.886 * c - 0.203 * s,
            ],
        ],
        dtype=np.float32,
    )


def apply_look(rgb: NDArray[np.uint8], look: Look | tuple | None) -> NDArray[np.uint8]:
    """``rgb`` (H, W, 3 uint8) in ``look``'s colours, or itself when the look changes nothing."""
    if look is not None and not isinstance(look, Look):
        look = Look(*look)
    if look is None or look.unchanged:
        return rgb
    b, c, s, t, g = look.values()
    luma = np.asarray(LUMA, dtype=np.float32)
    warm = np.asarray([1.0 + WARMTH * t, 1.0, 1.0 - WARMTH * t], dtype=np.float32)
    turn = _hue_turn(TURN_DEG * g).T if g > 0 else None
    out = np.empty_like(rgb)
    for start in range(0, rgb.shape[0], _ROWS):
        block = rgb[start : start + _ROWS].astype(np.float32)
        if g:
            green = block[..., 1] - np.maximum(block[..., 0], block[..., 2])
            green *= 1.0 / GREEN_STEPS
            np.clip(green, 0.0, 1.0, out=green)
            green = green[..., None]
        if b:
            block *= 1.0 + b
        if c:
            block -= 127.5
            block *= 1.0 + c
            block += 127.5
        if s:
            grey = (block @ luma)[..., None]
            block = grey + (block - grey) * (1.0 + s)
        if t:
            block *= warm
        if turn is not None:
            block += green * (block @ turn - block)
        elif g < 0:
            grey = (block @ luma)[..., None]
            block += green * (-g * FRESHER) * (block - grey)
        # rounded, not truncated: truncation alone would darken every pixel by half a step
        np.rint(block, out=block)
        np.clip(block, 0.0, 255.0, out=block)
        out[start : start + _ROWS] = block.astype(np.uint8)
    return out


@functools.lru_cache(maxsize=4)
def _table_565(values: tuple[float, ...]) -> NDArray[np.uint16]:
    """Every 5:6:5 colour in the look ``values``, rounded back to 5:6:5: an encoder makes it once
    per look."""
    from .imaging import expand_565

    colours = expand_565(np.arange(65536, dtype=np.uint16)).reshape(-1, 1, 3)
    out = apply_look(colours, Look(*values)).reshape(-1, 3).astype(np.uint32)
    r = (out[:, 0] * 31 + 127) // 255
    g = (out[:, 1] * 63 + 127) // 255
    b = (out[:, 2] * 31 + 127) // 255
    return ((r << 11) | (g << 5) | b).astype(np.uint16)


def look_blocks(blocks: bytes, look: Look | tuple | None) -> bytes:
    """BC1 ``blocks`` in ``look``'s colours: each block's two colours changed, its pixels' mixes
    kept, and the block put back in four-colour mode where the change swapped its colours' order
    (:func:`orthostudio.live.imaging.four_colour`)."""
    if look is not None and not isinstance(look, Look):
        look = Look(*look)
    if look is None or look.unchanged:
        return blocks
    from .imaging import four_colour

    table = _table_565(look.values())
    a = np.frombuffer(blocks, dtype=np.uint8).reshape(-1, 8).copy()
    ends = a[:, 0:4].copy().view("<u2")
    a[:, 0:4] = table[ends].astype("<u2").view(np.uint8).reshape(-1, 4)
    return four_colour(a.tobytes())


def _astronomical(day: int, year: int) -> str:
    """The season in the northern hemisphere on ``day`` (1 = 1 January) of ``year``: spring from
    the March equinox (20 March), summer from the June solstice (21 June), autumn from the
    September equinox (23 September), winter from the December solstice (21 December)."""
    date = datetime.date(year, 1, 1) + datetime.timedelta(days=day - 1)
    md = (date.month, date.day)
    if (3, 20) <= md < (6, 21):
        return "spring"
    if (6, 21) <= md < (9, 23):
        return "summer"
    if (9, 23) <= md < (12, 21):
        return "autumn"
    return "winter"


_OPPOSITE = {"spring": "autumn", "summer": "winter", "autumn": "spring", "winter": "summer"}


def season_on(day: int, lat: float, year: int | None = None) -> str:
    """The season on ``day`` of the year (1 = 1 January) at latitude ``lat``: the astronomical
    seasons, the other way round south of the equator, and none between the tropics (summer: the
    photo as delivered there, unless the pilot changed summer's sliders)."""
    if abs(lat) < TROPICS:
        return "summer"
    year = year or datetime.date.today().year
    day = min(max(int(day), 1), 366 if _leap(year) else 365)
    north = _astronomical(day, year)
    return north if lat >= 0 else _OPPOSITE[north]


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def today() -> int:
    """Today's day of the year on this computer (1 = 1 January)."""
    return datetime.date.today().timetuple().tm_yday


# --- the window's preview ------------------------------------------------------------------------

SAMPLE_PIECES = 2  # a square of 2 x 2 pieces: 512 px, about 1.2 km at ZL16
SAMPLE_ZOOMS = (16, 15, 17, 14, 18, 13)  # the zoom of the pieces shown, the first Live keeps
GOLDEN = 0.6180339887  # each next place this far along the pieces kept: never twice the same


def sample_photo(cache_path, provider: str, index: int = 0) -> bytes | None:
    """A JPEG of the ground for the window's preview, from the imagery Live keeps: a square of
    whole pieces at ZL16 (else the zoom nearest kept), ``index`` going through places spread over
    what is kept. None when Live keeps none yet. Nothing is downloaded for it: what is shown is
    ground this pilot flew over. Only the cache's own key is read (provider, zoom, x, y): asking
    for the newest pieces scanned a 2.3 GB cache for 12 s (2026-10-13)."""
    import io
    import sqlite3

    from PIL import Image

    try:
        db = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    n = SAMPLE_PIECES

    def body(z: int, x: int, y: int) -> bytes | None:
        row = db.execute(
            "SELECT data FROM chunks WHERE provider=? AND z=? AND x=? AND y=? AND status=1",
            (provider, z, x, y),
        ).fetchone()
        return row[0] if row else None

    def square(zoom: int) -> bytes | None:
        low, high = db.execute(
            "SELECT min(x), max(x) FROM chunks WHERE provider=? AND z=?", (provider, zoom)
        ).fetchone()
        if low is None:
            return None
        start = low + int(((index * GOLDEN) % 1.0) * (high - low + 1))
        for x0 in (start, low):  # from that place on, else from the first
            rows = db.execute(
                "SELECT x, y FROM chunks WHERE provider=? AND z=? AND x>=? AND status=1 LIMIT 256",
                (provider, zoom, x0),
            ).fetchall()
            for x, y in rows:
                cx, cy = x - x % n, y - y % n
                pieces = [body(zoom, cx + i, cy + j) for i in range(n) for j in range(n)]
                if all(pieces):
                    image = Image.new("RGB", (256 * n, 256 * n))
                    for k, data in enumerate(pieces):
                        with Image.open(io.BytesIO(data)) as piece:
                            image.paste(piece.convert("RGB"), (256 * (k // n), 256 * (k % n)))
                    out = io.BytesIO()
                    image.save(out, "JPEG", quality=90)
                    return out.getvalue()
        return None

    try:
        for zoom in SAMPLE_ZOOMS:
            found = square(zoom)
            if found is not None:
                return found
        return None
    except (sqlite3.Error, OSError, ValueError):
        return None
    finally:
        db.close()
