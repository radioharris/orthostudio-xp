# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Night textures: the lights of a town painted on a texture X-Plane shows at night.

X-Plane 12's own ground glows at night where it is a town (its terrain files' ``TEXTURE_LIT``); a
photo texture replaces that ground, and without one of its own a town on Live's ground is black but
for X-Plane's roads and buildings. A ``LIT_TEX_NOWRAP`` line in a texture's ``.ter`` gives it one:
X-Plane adds it to the photo at night, fades it in at dusk by itself, and reads it as it comes
closer, as the photo's own levels, so a night texture written after the area loaded is shown
(the late test, 2026-11-10).

What this module paints, for one texture, as the prototype on the user's Nice tile settled it
(2026-11-10, flown at 2704 x 1756, cost not measurable):

- **street lamps** along X-Plane's own roads (:mod:`orthostudio.dsf.networks`), one every
  20 to 45 m by the road's class, a third of them white (LEDs) and the rest sodium orange, at
  levels drawn at random and one in eight out; far fewer and dimmer along a rural road;
- **a soft glow** round the lamps and the larger streets;
- **windows** in a band along the town's streets, a few bright, most dim;
- all of it times **how bright NASA's Black Marble saw the place** (:class:`Density`): a dense
  centre glows, the hills round it less;
- and nothing else: between the lights the night texture is black, X-Plane lighting the photo
  itself at night. A twentieth of the day photo under the lights, as XPME does (its « night
  ground visibility », 5 % by default), came out of BC1 as a checkerboard of purple, brown and
  green blocks: values of 4 to 10 out of 255, which the format cannot hold in their colour (the
  user's LSGG, 2026-11-10). What is fainter than :data:`FLOOR` is black for the same reason.

Everything is drawn at the same metres a pixel at every zoom (a ZL18 texture's lights drawn at a
quarter of its side and enlarged): drawn at the texture's own pixels, the airport's sharper zone
came out twice darker than the town round it. The smaller levels are a power mean of the level
above (:data:`POWER`), not the plain mean, so a few lamps in the dark keep showing from far: at
80 km the coast was 20 % brighter for it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

__all__ = [
    "LIT_SIDE",
    "Density",
    "Road",
    "draw",
    "lit_levels",
    "night_rgb",
    "roads_of",
]

LIT_SIDE = 1024
"""A night texture's side: X-Plane's own are 1024 px for 2 to 5 km; a quarter of the photo."""

ROADS: dict[str, tuple[float, float, float]] = {
    "motorway": (16.0, 1.00, 45.0),
    "motorway_link": (8.0, 0.90, 35.0),
    "primary": (12.0, 0.95, 35.0),
    "secondary": (10.0, 0.85, 32.0),
    "residential": (6.0, 0.60, 28.0),
}
"""Per class: the street's width in metres, its brightness and the spacing of its lamps."""

RURAL = (0.45, 1.8)
"""A rural road (X-Plane's ``rural_edge`` variant): its brightness and its lamps' spacing scaled."""

HOUSES = {"residential": 0.10, "secondary": 0.10, "primary": 0.10}
"""How bright the windows along a town street are, in a band :data:`HOUSES_M` wide."""

HOUSES_M = 50.0

WEIGHTS = (0.12, 1.0, 0.35, 1.0)
"""The street itself, its lamps, their glow and the windows, in the sum."""

SODIUM = np.array([1.00, 0.70, 0.36])
WHITE = np.array([0.95, 0.92, 0.85])
WHITE_SHARE = 0.35
LAMPS_OUT = 0.12

DENSITY_FLOOR, DENSITY_SPAN = 0.2, 1.3
"""The lights' factor where NASA saw nothing, and how much more where it saw its brightest."""

DENSITY_SAMPLES = 64
"""NASA's density is worked out at this many points a side of a texture, drawn up between them:
every 16 px of a 1024 px texture, three or four points a cell of the grid."""

GAIN = 0.75
POWER = 3.0

FLOOR = 6
"""Light fainter than this (out of 255, its brightest channel) is written black: BC1 holds red
and blue in steps of 8, so the faint edge of a glow came out purple or green."""

EARTH_M = 40075016.7


@dataclass(frozen=True)
class Road:
    """A road as the night textures need it: its class, whether rural, its points in degrees."""

    kind: str
    rural: bool
    lon: np.ndarray
    lat: np.ndarray
    box: tuple[float, float, float, float] | None = None  # south, west, north, east, once

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """South, west, north, east."""
        if self.box is not None:
            return self.box
        return (
            float(self.lat.min()),
            float(self.lon.min()),
            float(self.lat.max()),
            float(self.lon.max()),
        )


KINDS = tuple(ROADS)


@dataclass(frozen=True)
class Roads:
    """A DSF's roads in a few arrays rather than one object each: every point's longitude and
    latitude end to end, where each road starts in them, its class (an index into
    :data:`KINDS`), whether rural, and its bounds. The night's process keeps a few squares'
    (nightwork): 40,000 roads a square as objects took gigabytes across Live's encoders
    (2026-11-10). The points are float32 degrees from the corner ``west``, ``south``: a centimetre
    anywhere on Earth, half the memory of float64."""

    lon: np.ndarray  # float32, degrees east of west
    lat: np.ndarray  # float32, degrees north of south
    start: np.ndarray  # n + 1 offsets into lon and lat
    kind: np.ndarray  # uint8
    rural: np.ndarray  # bool
    box: np.ndarray  # (n, 4) float32: south, west, north, east, in degrees
    west: float = 0.0
    south: float = 0.0

    def __len__(self) -> int:
        return len(self.kind)

    @staticmethod
    def of(roads: Iterable[Road]) -> Roads:
        roads = list(roads)
        if not roads:
            return Roads.empty()
        start = np.zeros(len(roads) + 1, dtype=np.int64)
        start[1:] = np.cumsum([len(r.lon) for r in roads])
        lon = np.concatenate([np.asarray(r.lon, dtype=np.float64) for r in roads])
        lat = np.concatenate([np.asarray(r.lat, dtype=np.float64) for r in roads])
        west, south = math.floor(lon.min()), math.floor(lat.min())
        return Roads(
            (lon - west).astype(np.float32),
            (lat - south).astype(np.float32),
            start,
            np.array([KINDS.index(r.kind) for r in roads], dtype=np.uint8),
            np.array([r.rural for r in roads], dtype=bool),
            np.array([r.bounds for r in roads], dtype=np.float32),
            float(west),
            float(south),
        )

    @staticmethod
    def empty() -> Roads:
        z = np.zeros(0, dtype=np.float32)
        return Roads(z, z, np.zeros(1, dtype=np.int64), z.astype(np.uint8), z.astype(bool),
                     np.zeros((0, 4), dtype=np.float32))  # fmt: skip

    def points(self, i: int) -> tuple[np.ndarray, np.ndarray]:
        """Road ``i``'s points, longitudes and latitudes in degrees (float64)."""
        lo, hi = int(self.start[i]), int(self.start[i + 1])
        return (
            self.lon[lo:hi].astype(np.float64) + self.west,
            self.lat[lo:hi].astype(np.float64) + self.south,
        )

    def near(self, s: float, w: float, n: float, e: float) -> np.ndarray:
        """The indices of the roads whose bounds meet the box."""
        b = self.box
        return np.flatnonzero((b[:, 2] >= s) & (b[:, 0] <= n) & (b[:, 3] >= w) & (b[:, 1] <= e))


def roads_of(data: bytes) -> Roads:
    """The roads of a DSF's bytes (X-Plane's Global Scenery, or the overlays Live installs, cut
    from it), railways and power lines left out. Straight into :class:`Roads`' arrays: a
    :class:`Road` each took 138 MB at most on the square of Marseille (156,258 chains), these
    64 MB, the same arrays (2026-11-10)."""
    from orthostudio.dsf.networks import chains_of, road_class  # the roads' own process only

    _names, chains = chains_of(data)
    kind_index = {kind: i for i, kind in enumerate(KINDS)}
    points, kinds, rural = [], [], []
    for chain in chains:
        known = road_class(chain.subtype)
        if known is not None:
            points.append(chain.points)
            kinds.append(kind_index[known[0]])
            rural.append(known[1])
    del chains
    if not points:
        return Roads.empty()
    start = np.zeros(len(points) + 1, dtype=np.int64)
    np.cumsum([len(p) for p in points], out=start[1:])
    both = np.concatenate(points)
    del points
    lon, lat = both[:, 0], both[:, 1]
    first = start[:-1]  # every road has two points at least (networks.walk_networks)
    box = np.stack(
        [
            np.minimum.reduceat(lat, first),
            np.minimum.reduceat(lon, first),
            np.maximum.reduceat(lat, first),
            np.maximum.reduceat(lon, first),
        ],
        axis=1,
    ).astype(np.float32)
    west, south = math.floor(lon.min()), math.floor(lat.min())
    return Roads(
        (lon - west).astype(np.float32),
        (lat - south).astype(np.float32),
        start,
        np.array(kinds, np.uint8),
        np.array(rural, bool),
        box,
        float(west),
        float(south),
    )


@dataclass(frozen=True)
class Density:
    """How bright NASA's Black Marble saw the ground, 0 to 255 on a grid of ``step`` degrees whose
    first cell's north-west corner is at ``west``, ``north``."""

    grid: np.ndarray
    west: float
    north: float
    step: float

    def at(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        """0 to 1 at each point, read between the cells' centres."""
        g = self.grid.astype(np.float64) / 255.0
        c = (lon - self.west) / self.step - 0.5
        r = (self.north - lat) / self.step - 0.5
        c0 = np.clip(np.floor(c).astype(int), 0, g.shape[1] - 2)
        r0 = np.clip(np.floor(r).astype(int), 0, g.shape[0] - 2)
        fc, fr = np.clip(c - c0, 0.0, 1.0), np.clip(r - r0, 0.0, 1.0)
        return (
            g[r0, c0] * (1 - fc) * (1 - fr)
            + g[r0, c0 + 1] * fc * (1 - fr)
            + g[r0 + 1, c0] * (1 - fc) * fr
            + g[r0 + 1, c0 + 1] * fc * fr
        )


def lights_at(lights: np.ndarray, level: int, power: float = POWER) -> np.ndarray:
    """``lights`` (float, drawn at the night texture's full side) reduced ``level`` times by the
    power mean, as :func:`lit_levels` reduces a texture: a night texture far away is the sharp one
    made smaller, never one drawn small, whose lamps kept their pixels where a pixel covers 16 or
    256 times the ground, and came out up to four times too bright (the user's Live flight,
    2026-11-10: blown-out blocks far off, put out coming closer)."""
    out = lights
    for _ in range(level):
        h = out.shape[0] // 2
        if h < 1:
            break
        out = (out[: 2 * h, : 2 * h].reshape(h, 2, h, 2, -1) ** power).mean(
            axis=(1, 3), dtype=np.float32
        ) ** (1.0 / power)
    return out


def _pixels(
    x0: int, y0: int, zoom_level: int, side: int, lon, lat
) -> tuple[np.ndarray, np.ndarray]:
    """A texture's pixels of points: the texture is the 16 x 16 tiles from ``x0``, ``y0``."""
    n = 2.0**zoom_level
    x = ((np.asarray(lon) + 180.0) / 360.0 * n - x0) / 16.0 * side
    lat_r = np.radians(np.asarray(lat))
    y = ((1.0 - np.arcsinh(np.tan(lat_r)) / math.pi) / 2.0 * n - y0) / 16.0 * side
    return x, y


def _bounds(x0: int, y0: int, zoom_level: int) -> tuple[float, float, float, float]:
    """South, west, north, east of a texture, in degrees."""
    n = 2.0**zoom_level

    def lat(y: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))

    return lat(y0 + 16), x0 / n * 360.0 - 180.0, lat(y0), (x0 + 16) / n * 360.0 - 180.0


def draw(
    roads: Roads | Sequence[Roads] | Iterable[Road],
    density: Density | None,
    x0: int,
    y0: int,
    zoom_level: int,
    side: int = LIT_SIDE,
    seed: int = 0,
) -> np.ndarray:
    """The lights of one texture, ``(side, side, 3)`` RGB from 0 to 1 (float32), before
    :data:`GAIN`. The same roads, place and ``seed`` always give the same image. ``roads``: one
    square's, several squares' (drawn in turn, never copied into one: a texture on a corner took
    four squares' worth, up to 40 MB, 2026-11-10), or :class:`Road` objects."""
    parts: list[Roads]
    if isinstance(roads, Roads):
        parts = [roads]
    else:
        items = list(roads)
        if items and all(isinstance(r, Roads) for r in items):
            parts = [r for r in items if isinstance(r, Roads)]
        else:
            parts = [Roads.of(r for r in items if isinstance(r, Road))]
    shrink = 2 ** max(0, zoom_level - 16)
    inner = max(1, side // shrink)
    rgb = _draw(parts, density, x0, y0, zoom_level, inner, seed)
    if shrink > 1:
        rgb = _zoom_linear(rgb, side)
    return np.clip(rgb, 0.0, 1.0, out=rgb)


def _draw(parts: list[Roads], density, x0, y0, zoom_level, side, seed) -> np.ndarray:
    s, w, n, e = _bounds(x0, y0, zoom_level)
    m_per_px = 16.0 * EARTH_M / 2.0**zoom_level * math.cos(math.radians((s + n) / 2)) / side
    # k: 1 at the reference scale (a ZL16 texture at 1024 px, a ZL18 one at 256), k times coarser
    # for a far texture (FAR_DRAW): its light kept, its lamps a pixel each
    ref = m_per_px * side / LIT_SIDE * 2.0 ** (zoom_level - 16)
    k = max(1, round(m_per_px / ref))
    lamp_k = (4.0 / k**2) ** (1 / POWER) if k > 1 else 1.0  # a lamp's light, its power mean kept
    glow_k = (4.0 / k**2) ** (1 - 1 / POWER) if k > 1 else 1.0  # and what it adds to the glow
    margin = 0.01
    rng = np.random.default_rng(seed)
    core = Image.new("F", (side, side), 0.0)
    fill = Image.new("F", (side, side), 0.0)
    draw_core, draw_fill = ImageDraw.Draw(core), ImageDraw.Draw(fill)
    sodium = np.zeros((side, side), dtype=np.float32)
    white = np.zeros((side, side), dtype=np.float32)
    houses_px = max(1, round(HOUSES_M / m_per_px))
    houses_k = _width_k(HOUSES_M, m_per_px, ref)
    box = (s - margin, w - margin, n + margin, e + margin)
    for part, i in [(p, i) for p in parts for i in p.near(*box)]:
        kind = KINDS[int(part.kind[i])]
        rural = bool(part.rural[i])
        width_m, bright, spacing_m = ROADS[kind]
        if rural:
            bright, spacing_m = bright * RURAL[0], spacing_m * RURAL[1]
        px, py = _pixels(x0, y0, zoom_level, side, *part.points(i))
        points = list(zip(px.tolist(), py.tolist(), strict=True))
        if kind in HOUSES and not rural:
            draw_fill.line(points, fill=HOUSES[kind] * houses_k, width=houses_px)
        draw_core.line(
            points,
            fill=bright * _width_k(width_m, m_per_px, ref),
            width=max(1, round(width_m / m_per_px)),
        )
        # lamps along the road, one every spacing_m from a random start, a few out
        steps = np.hypot(np.diff(px), np.diff(py))
        along = np.concatenate(([0.0], np.cumsum(steps)))
        step = spacing_m / m_per_px
        at = np.arange(rng.uniform(0.0, step), along[-1], step)
        if len(at) == 0:
            continue
        lit = rng.random(len(at)) > LAMPS_OUT
        level = (bright * lamp_k * rng.uniform(0.35, 0.8, len(at)))[lit]
        ix = np.interp(at, along, px)[lit].astype(int)
        iy = np.interp(at, along, py)[lit].astype(int)
        inside = (ix >= 0) & (ix < side) & (iy >= 0) & (iy < side)
        target = white if rng.random() < WHITE_SHARE else sodium
        np.maximum.at(target, (iy[inside], ix[inside]), level[inside].astype(np.float32))
    # a lamp is a dot of about two pixels across, as the prototype drew them (a disc of 0.6 px);
    # drawn coarser, a pixel
    if k == 1:
        sodium, white = _max2(sodium), _max2(white)
    sigma = max(1.5, 12.0 / m_per_px)
    a, b, c, d = (np.float32(v) for v in WEIGHTS)
    # in place, a few arrays at a time: each is 4 MB at 1024 px, and what the process once held
    # it keeps (2026-11-10)
    core_a = np.asarray(core, dtype=np.float32)
    del draw_core, core
    tmp = core_a * np.float32(0.5)
    tmp += sodium * np.float32(glow_k)
    warm = _blur(tmp, sigma)  # the glow round the sodium lamps and the streets
    warm *= c
    np.multiply(core_a, a, out=tmp)
    warm += tmp
    del core_a
    np.multiply(sodium, b, out=tmp)
    warm += tmp
    del sodium
    cool = _blur(white * np.float32(glow_k), sigma)
    cool *= c
    np.multiply(white, b, out=tmp)
    cool += tmp
    del white, tmp
    windows = np.array(fill, dtype=np.float32)  # a copy: Pillow's array is read only
    del draw_fill, fill
    spark = rng.random((side, side), dtype=np.float32)
    spark **= 6
    spark *= np.float32(2.5)
    spark += np.float32(0.25)
    windows *= spark
    del spark
    cold = rng.random((side, side), dtype=np.float32) < 0.3
    windows *= d
    np.add(warm, windows, out=warm, where=~cold)
    np.add(cool, windows, out=cool, where=cold)
    del windows, cold
    if density is not None:
        factor = _density_factor(density, x0, y0, zoom_level, side)
        warm *= factor
        cool *= factor
        del factor
    rgb = np.empty((side, side, 3), dtype=np.float32)
    for ch in range(3):
        np.multiply(warm, np.float32(SODIUM[ch]), out=rgb[..., ch])
        rgb[..., ch] += cool * np.float32(WHITE[ch])
    return rgb


def _width_k(width_m: float, m_per_px: float, ref: float) -> float:
    """A line ``width_m`` wide drawn at ``m_per_px`` lit as at the reference scale ``ref``: its
    light times the width it had there over the width it has here (a pixel at the least)."""
    if m_per_px <= ref * 1.01:
        return 1.0
    there = max(1, round(width_m / ref)) * ref
    here = max(1, round(width_m / m_per_px)) * m_per_px
    return there / here


def _blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """``a`` (2-D float32) blurred as scipy.ndimage.gaussian_filter blurs it (its border mirrored,
    its kernel 4 sigmas each side), within 1e-7: one axis then the other, each tap added in place.
    Twice as fast as scipy, and the night's process loads no scipy (17 MB, 2026-11-10)."""
    radius = int(4.0 * sigma + 0.5)
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    k = (k / k.sum()).astype(np.float32)
    rows, cols = a.shape
    p = np.pad(a, ((radius, radius), (0, 0)), mode="symmetric")
    out = p[radius : radius + rows] * k[radius]
    tmp = np.empty_like(out)
    for i in range(1, radius + 1):
        np.add(p[radius - i : radius - i + rows], p[radius + i : radius + i + rows], out=tmp)
        tmp *= k[radius + i]
        out += tmp
    p = np.pad(out, ((0, 0), (radius, radius)), mode="symmetric")
    out = p[:, radius : radius + cols] * k[radius]
    for i in range(1, radius + 1):
        np.add(p[:, radius - i : radius - i + cols], p[:, radius + i : radius + i + cols], out=tmp)
        tmp *= k[radius + i]
        out += tmp
    return out


def _max2(a: np.ndarray) -> np.ndarray:
    """scipy.ndimage.maximum_filter(a, size=2), the same to the bit: each pixel the brightest of
    itself and the ones above, left and above left."""
    out = a.copy()
    np.maximum(out[1:], a[:-1], out=out[1:])
    above = out.copy()
    np.maximum(out[:, 1:], above[:, :-1], out=out[:, 1:])
    return out


def _zoom_linear(a: np.ndarray, side: int) -> np.ndarray:
    """``a`` (n x n, or n x n x channels) enlarged to ``side`` as scipy.ndimage.zoom(order=1) does,
    within 1e-6: its corners on the corners, linear between."""
    n = a.shape[0]
    pos = np.arange(side, dtype=np.float64) * ((n - 1) / (side - 1))
    i0 = np.minimum(pos.astype(np.int64), n - 2)
    f = (pos - i0).astype(np.float32)
    g = (1 - f).astype(np.float32)
    shape = (side, 1) + (1,) * (a.ndim - 2)
    rows = a[i0] * g.reshape(shape) + a[i0 + 1] * f.reshape(shape)
    shape = (1, side) + (1,) * (a.ndim - 2)
    return rows[:, i0] * g.reshape(shape) + rows[:, i0 + 1] * f.reshape(shape)


def _density_factor(density: Density, x0: int, y0: int, zoom_level: int, side: int) -> np.ndarray:
    """:data:`DENSITY_FLOOR` plus :data:`DENSITY_SPAN` times NASA's density, at each pixel: worked
    out at :data:`DENSITY_SAMPLES` points a side and drawn up between them (a cell of the grid,
    15 arc-seconds, is about 60 px of a ZL16 texture at 1024 px). Worked out at every pixel, it
    took a worker 120 MB a texture (2026-11-10)."""
    n = min(side, DENSITY_SAMPLES)
    # the pixels sampled are those zoom keeps: the first, the last and evenly between
    u = np.linspace(0.0, side - 1.0, n) + 0.5
    nn = 2.0**zoom_level
    lon = (x0 + u / side * 16.0) / nn * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * (y0 + u / side * 16.0) / nn))))
    lons, lats = np.meshgrid(lon, lat)
    small = (DENSITY_FLOOR + DENSITY_SPAN * density.at(lons, lats)).astype(np.float32)
    return small if n == side else _zoom_linear(small, side)


def night_rgb(lights: np.ndarray, gain: float = GAIN) -> np.ndarray:
    """The night texture's top level, RGB uint8: the lights times ``gain``, what is fainter than
    :data:`FLOOR` black. In place, in float32: a 1024 px texture's copies are 12 MB each."""
    rgb = lights * np.float32(gain)
    np.clip(rgb, 0.0, 1.0, out=rgb)
    rgb *= np.float32(255.0)
    rgb += np.float32(0.5)
    out = rgb.astype(np.uint8)
    out[out.max(axis=-1) < FLOOR] = 0
    return out


def lit_levels(rgb: np.ndarray, power: float = POWER) -> list[np.ndarray]:
    """``rgb`` (square, power-of-two side, uint8) and its smaller levels down to 1 x 1, each the
    power mean of four pixels of the one above: brighter than their plain mean where a few lamps
    stand in the dark, which is what keeps a town visible from far."""
    side = rgb.shape[0]
    if rgb.ndim != 3 or rgb.shape[1] != side or side & (side - 1):
        raise ValueError(f"lit_levels needs a square power-of-two RGB image, got {rgb.shape}")
    out = [rgb]
    if side == 1:
        return out
    p = np.float32(power)
    # the first, the largest, a channel at a time: the whole level in float32 is 12 MB at 1024 px
    h = side // 2
    level = np.empty((h, h, rgb.shape[2]), dtype=np.float32)
    for ch in range(rgb.shape[2]):
        c = rgb[..., ch].astype(np.float32)
        c *= np.float32(1.0 / 255.0)
        c **= p
        level[..., ch] = c.reshape(h, 2, h, 2).mean(axis=(1, 3), dtype=np.float32)
    level **= 1 / p
    out.append((np.clip(level, 0.0, 1.0) * np.float32(255.0) + np.float32(0.5)).astype(np.uint8))
    while level.shape[0] > 1:
        h = level.shape[0] // 2
        level = (level.reshape(h, 2, h, 2, -1) ** p).mean(axis=(1, 3), dtype=np.float32) ** (1 / p)
        out.append(
            (np.clip(level, 0.0, 1.0) * np.float32(255.0) + np.float32(0.5)).astype(np.uint8)
        )
    return out


def seed_of(x0: int, y0: int) -> int:
    """The same lamps for a texture at every build."""
    return (x0 * 73856093) ^ (y0 * 19349663)


def textures_lit(names: Sequence[str]) -> list[str]:
    """The night texture's name of each texture name: ``<stem>_LIT.dds``."""
    return [name[: -len(".dds")] + "_LIT.dds" for name in names]
