# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The two grid formulas of Ortho4XP a ``.ter`` file needs, to check it against its name.

The full texture grid, ``TextureId`` included, is ``orthostudio.imagery.grid``.
"""

from __future__ import annotations

from math import atan, cos, exp, pi

from orthostudio.imagery.grid import TextureId

EARTH_RADIUS_M = 6378137  # O4_Geo_Utils.py:4


def gtile_to_wgs84(til_x: float, til_y: float, zl: int) -> tuple[float, float]:
    """(lat, lon) of the top-left corner of a web-mercator tile (O4_Geo_Utils.py:66-78)."""
    rat_x = til_x / (2 ** (zl - 1)) - 1
    rat_y = 1 - til_y / (2 ** (zl - 1))
    return (360 / pi * atan(exp(pi * rat_y)) - 90, rat_x * 180)


def webmercator_pixel_size(lat: float, zl: int) -> float:
    """Ground size in metres of one web-mercator pixel (O4_Geo_Utils.py:32-33)."""
    return 2 * pi * EARTH_RADIUS_M * cos(pi * lat / 180) / (2 ** (zl + 8))


def load_center(t: TextureId) -> tuple[float, float, int]:
    """``(lat_med, lon_med, size_m)`` as written on the ``LOAD_CENTER`` line of a ``.ter``.

    ``O4_DSF_Utils.py:284-300``: centre of the texture (tile ``+8``), approximate ground
    size ``int(pixel_size * 4096)``.
    """
    lat_med, lon_med = gtile_to_wgs84(t.til_x + 8, t.til_y + 8, t.zl)
    return lat_med, lon_med, int(webmercator_pixel_size(lat_med, t.zl) * 4096)
