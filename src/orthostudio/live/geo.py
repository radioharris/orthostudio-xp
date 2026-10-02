# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Web Mercator tile maths and distances, as Ortho4XP and AutoOrtho name their textures.

A texture ``{y}_{x}_{provider}{zoom}.dds`` covers 16 x 16 slippy tiles at ``zoom``, the top-left one
being (x, y). Its centre is tile (x + 8, y + 8), the point the ``LOAD_CENTER`` line of its ``.ter``
file gives.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_008.8

# 100000_125184_BI18.dds, 100000_125184_BI18_water_overlay.ter, 23296_33152_GO216.dds.
# The provider is lazy so that GO216 reads as provider GO2, zoom 16, as AutoOrtho reads it.
_TEXTURE_RE = re.compile(r"^(\d+)_(\d+)_((?!ZL)[A-Za-z0-9]*?)(\d{2})(_[A-Za-z_]+)?\.(dds|ter)$")
_DSF_RE = re.compile(r"([+-]\d{2})([+-]\d{3})\.dsf$")


def tile_nw_corner(x: float, y: float, zoom: int) -> tuple[float, float]:
    """(lat, lon) of the north-west corner of slippy tile (x, y) at ``zoom``."""
    n = 2.0**zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lat, lon


@dataclass(frozen=True)
class Texture:
    """One 4096 x 4096 ortho texture: 16 x 16 slippy tiles at ``zoom`` from top-left tile (x, y)."""

    y: int
    x: int
    provider: str
    zoom: int

    @property
    def dds_name(self) -> str:
        return f"{self.y}_{self.x}_{self.provider}{self.zoom}.dds"

    @property
    def center(self) -> tuple[float, float]:
        return tile_nw_corner(self.x + 8, self.y + 8, self.zoom)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(south, west, north, east) in degrees."""
        north, west = tile_nw_corner(self.x, self.y, self.zoom)
        south, east = tile_nw_corner(self.x + 16, self.y + 16, self.zoom)
        return south, west, north, east


def parse_texture_name(name: str) -> Texture | None:
    """The texture a ``.dds`` or ``.ter`` file name stands for, or None."""
    m = _TEXTURE_RE.match(name)
    if not m:
        return None
    return Texture(int(m[1]), int(m[2]), m[3], int(m[4]))


def dsf_tile_of(path: str) -> tuple[int, int] | None:
    """(lat, lon) of the south-west corner of the tile a DSF path names, or None."""
    m = _DSF_RE.search(path)
    if not m:
        return None
    return int(m[1]), int(m[2])


def dsf_rel_path(lat: int, lon: int) -> str:
    """``Earth nav data/+40+000/+46+002.dsf`` for tile (46, 2)."""
    lat10 = math.floor(lat / 10) * 10
    lon10 = math.floor(lon / 10) * 10
    return f"Earth nav data/{lat10:+03d}{lon10:+04d}/{lat:+03d}{lon:+04d}.dsf"


def tile_name(lat: int, lon: int) -> str:
    return f"{lat:+03d}{lon:+04d}"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial true bearing from point 1 to point 2, 0-360."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angle_diff(a: float, b: float) -> float:
    """Absolute difference between two headings, 0-180."""
    d = abs(a - b) % 360.0
    return 360.0 - d if d > 180.0 else d
