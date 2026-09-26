# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Web-mercator tile grid and texture naming, ported bit for bit from Ortho4XP.

Origin: ``src/O4_Geo_Utils.py:66-150`` (``gtile_to_wgs84``, ``wgs84_to_gtile``,
``gtile_to_quadkey``, ``wgs84_to_orthogrid``, ``st_coord``, ``webmercator_pixel_size``) and
``src/O4_File_Names.py:350-410`` (texture names). Specification and acceptance tests:
``docs/specs/imagery-grid.md``.

A texture is 16 x 16 tiles of 256 px at zoom level ``zl``; it is addressed by the Google/XYZ
indices of its top-left tile, both multiples of 16.
"""

from __future__ import annotations

import re
from math import atan, cos, exp, log, pi, tan
from typing import NamedTuple

__all__ = [
    "EARTH_RADIUS",
    "TEXTURE_TILES",
    "TextureId",
    "parent_tile",
    "parse_texture_name",
    "quadkey",
    "st_coord",
    "texture_at",
    "texture_bbox",
    "texture_name",
    "texture_tiles",
    "textures_covering",
    "tile_to_wgs84",
    "webmercator_pixel_size",
    "wgs84_to_gtile",
    "wgs84_to_tile",
]

EARTH_RADIUS = 6378137
"""Web-mercator sphere radius in metres (``O4_Geo_Utils.py:4``)."""

TEXTURE_TILES = 16
"""Tiles per texture side (a texture is 4096 px = 16 x 256 px)."""

_MIN_ZL, _MAX_ZL = 0, 24
_NAME_RE = re.compile(r"^(\d+)_(\d+)_([A-Za-z0-9_@-]+?)(\d{1,2})$")


class TextureId(NamedTuple):
    """One 4096² texture: top-left tile indices (multiples of 16), zoom level, provider."""

    til_x: int
    til_y: int
    zl: int
    provider: str


def _check_zl(zl: int) -> None:
    if not isinstance(zl, int) or not _MIN_ZL <= zl <= _MAX_ZL:
        raise ValueError(f"zoom level {zl!r} outside [{_MIN_ZL}, {_MAX_ZL}]")


def wgs84_to_tile(lat: float, lon: float, zl: int) -> tuple[float, float]:
    """Continuous web-mercator tile coordinates ``(x, y)`` of a point at zoom ``zl``.

    The integer parts are the Google/XYZ tile indices; the fractional parts locate the
    point inside the tile (x eastwards, y southwards). ``lat`` must be inside the
    web-mercator range (about +/- 85.05°).
    """
    _check_zl(zl)
    scale = 2 ** (zl - 1)
    x = (lon / 180 + 1) * scale
    y = (1 - log(tan((90 + lat) * pi / 360)) / pi) * scale
    return (x, y)


def wgs84_to_gtile(lat: float, lon: float, zl: int) -> tuple[int, int]:
    """Google tile containing a point, as Ortho4XP computes it (``wgs84_to_gtile``, ``:79-87``).

    Ortho4XP rounds to the nearest pixel before dividing by 256, so a point within half a pixel
    west (or north) of a tile edge is attributed to the next tile. Kept for callers that
    reproduce Ortho4XP requests; :func:`texture_at` truncates instead.
    """
    _check_zl(zl)
    rat_x = lon / 180
    rat_y = log(tan((90 + lat) * pi / 360)) / pi
    pix_x = round((rat_x + 1) * (2 ** (zl + 7)))
    pix_y = round((1 - rat_y) * (2 ** (zl + 7)))
    return (pix_x // 256, pix_y // 256)


def tile_to_wgs84(x: float, y: float, zl: int) -> tuple[float, float]:
    """``(lat, lon)`` of the top-left corner of tile ``(x, y)`` (``gtile_to_wgs84``, ``:66-77``).

    Accepts fractional tile coordinates, so it is the exact inverse of :func:`wgs84_to_tile`.
    """
    _check_zl(zl)
    rat_x = x / (2 ** (zl - 1)) - 1
    rat_y = 1 - y / (2 ** (zl - 1))
    lon = rat_x * 180
    lat = 360 / pi * atan(exp(pi * rat_y)) - 90
    return (lat, lon)


def texture_at(lat: float, lon: float, zl: int, provider: str) -> TextureId:
    """Texture containing a point (``wgs84_to_orthogrid``, ``O4_Geo_Utils.py:127-134``).

    Truncation (``int``) of the texture-unit coordinates, then times 16: bit for bit the
    Ortho4XP formula, including its ``int()`` towards zero at the western edge.
    """
    _check_zl(zl)
    ratio_x = lon / 180
    ratio_y = log(tan((90 + lat) * pi / 360)) / pi
    mult = 2 ** (zl - 5)
    til_x = int((ratio_x + 1) * mult) * 16
    til_y = int((1 - ratio_y) * mult) * 16
    return TextureId(til_x, til_y, zl, provider)


def texture_bbox(t: TextureId) -> tuple[float, float, float, float]:
    """``(lat_max, lon_min, lat_min, lon_max)`` of a texture in WGS84 degrees."""
    lat_max, lon_min = tile_to_wgs84(t.til_x, t.til_y, t.zl)
    lat_min, lon_max = tile_to_wgs84(t.til_x + TEXTURE_TILES, t.til_y + TEXTURE_TILES, t.zl)
    return (lat_max, lon_min, lat_min, lon_max)


def texture_tiles(t: TextureId) -> list[tuple[int, int]]:
    """The 256 ``(x, y)`` tiles of a texture, row-major: ``y`` outer, ``x`` inner.

    Index ``i`` of the list is ``16 * (y - til_y) + (x - til_x)``; the chunk container and
    the assembler use this order.
    """
    return [
        (t.til_x + dx, t.til_y + dy) for dy in range(TEXTURE_TILES) for dx in range(TEXTURE_TILES)
    ]


def textures_covering(
    lat0: float, lon0: float, lat1: float, lon1: float, zl: int, provider: str
) -> list[TextureId]:
    """Textures of the closed rectangle spanned by the two corners, y outer then x.

    Same enumeration as Ortho4XP's ``build_texture_region`` (``O4_Imagery_Utils.py:1933-1940``)
    and the mesh-time loop of ``O4_DSF_Utils.py:176-180``: the textures containing the
    north-west and south-east corners are both included, whatever their position inside
    those textures. The corners may be given in any order.
    """
    lat_max, lat_min = max(lat0, lat1), min(lat0, lat1)
    lon_min, lon_max = min(lon0, lon1), max(lon0, lon1)
    first = texture_at(lat_max, lon_min, zl, provider)
    last = texture_at(lat_min, lon_max, zl, provider)
    return [
        TextureId(x, y, zl, provider)
        for y in range(first.til_y, last.til_y + 1, TEXTURE_TILES)
        for x in range(first.til_x, last.til_x + 1, TEXTURE_TILES)
    ]


def texture_name(t: TextureId) -> str:
    """``{til_y}_{til_x}_{provider}{zl}`` (``O4_File_Names.py:365-372``), e.g. ``6016_8448_BI14``.

    The zoom level follows the provider code with no separator.
    """
    return f"{t.til_y}_{t.til_x}_{t.provider}{t.zl}"


def parse_texture_name(name: str) -> TextureId:
    """Inverse of :func:`texture_name`; accepts a trailing extension (``.dds``, ``.jpg``).

    The zoom level is glued to the provider code, which may itself end with digits
    (``PDOK18``): the last two digits are the ZL when they form a number in [10, 22], else
    the last digit alone (see ``docs/specs/imagery-grid.md`` section 3).
    """
    stem = name.rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.split(".", 1)[0]
    m = _NAME_RE.match(stem)
    if m is None:
        raise ValueError(f"not a texture name: {name!r}")
    til_y, til_x, code, digits = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
    if len(digits) == 2 and not 10 <= int(digits) <= 22:
        code, digits = code + digits[0], digits[1]
    if til_x % TEXTURE_TILES or til_y % TEXTURE_TILES:
        raise ValueError(f"texture indices of {name!r} are not multiples of 16")
    return TextureId(til_x, til_y, int(digits), code)


def quadkey(x: int, y: int, zl: int) -> str:
    """Bing quadkey of a Google tile (``gtile_to_quadkey``, ``O4_Geo_Utils.py:109-124``)."""
    key: list[str] = []
    temp_x, temp_y = x, y
    for step in range(1, zl + 1):
        size = 2 ** (zl - step)
        a = temp_x // size
        b = temp_y // size
        temp_x -= a * size
        temp_y -= b * size
        key.append(str(a + 2 * b))
    return "".join(key)


def parent_tile(x: int, y: int, levels: int = 1) -> tuple[int, int]:
    """Tile ``levels`` zoom levels above ``(x, y)`` (the parent fallback of Ortho4XP)."""
    if levels < 0:
        raise ValueError("levels must be >= 0")
    return (x >> levels, y >> levels)


def webmercator_pixel_size(lat: float, zl: int) -> float:
    """Ground size in metres of one pixel of a 256 px tile at ``lat`` (``O4_Geo_Utils.py:32``)."""
    return 2 * pi * EARTH_RADIUS * cos(pi * lat / 180) / (2 ** (zl + 8))


def st_coord(lat: float, lon: float, til_x: int, til_y: int, zl: int) -> tuple[float, float]:
    """Texture ``(s, t)`` coordinates of a point, clamped to [0, 1] (``O4_Geo_Utils.py:137-150``).

    ``s`` grows eastwards, ``t`` northwards (OpenGL convention, origin bottom-left).
    """
    ratio_x = lon / 180
    ratio_y = log(tan((90 + lat) * pi / 360)) / pi
    mult = 2 ** (zl - 5)
    s = (ratio_x + 1) * mult - (til_x // 16)
    t = 1 - ((1 - ratio_y) * mult - til_y // 16)
    s = s if s >= 0 else 0
    s = s if s <= 1 else 1
    t = t if t >= 0 else 0
    t = t if t <= 1 else 1
    return (s, t)
