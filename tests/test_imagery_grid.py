# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Unit tests of the web-mercator grid (``docs/specs/imagery-grid.md``, test G3)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from orthostudio.imagery import grid
from orthostudio.imagery.grid import (
    TextureId,
    parent_tile,
    parse_texture_name,
    quadkey,
    st_coord,
    texture_at,
    texture_bbox,
    texture_name,
    texture_tiles,
    textures_covering,
    tile_to_wgs84,
    webmercator_pixel_size,
    wgs84_to_gtile,
    wgs84_to_tile,
)

MARSEILLE = (43.4, 5.2)


def test_marseille_texture_and_name() -> None:
    t = texture_at(*MARSEILLE, 14, "BI")
    assert t == TextureId(8416, 5984, 14, "BI")
    assert texture_name(t) == "5984_8416_BI14"
    assert parse_texture_name("5984_8416_BI14") == t
    assert parse_texture_name("5984_8416_BI14.dds") == t
    assert parse_texture_name("/x/y/5984_8416_BI14.jpg") == t


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("5952_8416_PDOK1814", TextureId(8416, 5952, 14, "PDOK18")),
        ("5952_8416_PDOK189", TextureId(8416, 5952, 9, "PDOK18")),
        ("5952_8416_BI9", TextureId(8416, 5952, 9, "BI")),
        ("0_0_Arc@16", TextureId(0, 0, 16, "Arc@")),
        ("16_32_USGS22", TextureId(32, 16, 22, "USGS")),
    ],
)
def test_parse_texture_name_cases(name: str, expected: TextureId) -> None:
    assert parse_texture_name(name) == expected
    assert texture_name(expected) == name


@pytest.mark.parametrize(
    "bad", ["", "BI14", "5984_8416", "5984_8417_BI14", "5985_8416_BI14", "a_b_BI14"]
)
def test_parse_texture_name_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_texture_name(bad)


def test_tile_roundtrip_and_hemispheres() -> None:
    for lat, lon in [(43.4, 5.2), (-33.9, 151.2), (40.7, -74.0), (-54.8, -68.3), (0.0, 0.0)]:
        for zl in (10, 14, 16, 19):
            x, y = wgs84_to_tile(lat, lon, zl)
            assert 0 <= x < 2**zl and 0 <= y < 2**zl
            back = tile_to_wgs84(x, y, zl)
            assert back == pytest.approx((lat, lon), abs=1e-9)
            assert wgs84_to_gtile(lat, lon, zl) in {
                (math.floor(x), math.floor(y)),
                (math.floor(x) + 1, math.floor(y)),
                (math.floor(x), math.floor(y) + 1),
                (math.floor(x) + 1, math.floor(y) + 1),
            }
    # southern / western hemisphere: y beyond the equator row, x below the meridian column
    x, y = wgs84_to_tile(-33.9, 151.2, 10)
    assert y > 512 and x > 512
    x, y = wgs84_to_tile(40.7, -74.0, 10)
    assert x < 512 and y < 512


def test_wgs84_to_gtile_rounds_to_the_nearest_pixel_like_ortho4xp() -> None:
    # a point 0.4 px west of the edge of tile 513 at ZL10 is attributed to tile 513 by Ortho4XP
    lat, lon = tile_to_wgs84(513 - 0.4 / 256, 400, 10)
    assert wgs84_to_gtile(lat, lon, 10)[0] == 513
    assert math.floor(wgs84_to_tile(lat, lon, 10)[0]) == 512


def test_texture_at_truncates_and_bbox_is_consistent() -> None:
    t = texture_at(*MARSEILLE, 14, "BI")
    lat_max, lon_min, lat_min, lon_max = texture_bbox(t)
    assert lat_max > MARSEILLE[0] > lat_min
    assert lon_min < MARSEILLE[1] < lon_max
    # every interior point maps back to the same texture
    for f in (0.001, 0.5, 0.999):
        lat = lat_min + f * (lat_max - lat_min)
        lon = lon_min + f * (lon_max - lon_min)
        assert texture_at(lat, lon, 14, "BI") == t
    # the south-east corner belongs to the next texture
    assert texture_at(lat_min - 1e-9, lon_max + 1e-9, 14, "BI") == TextureId(8432, 6000, 14, "BI")


def test_texture_tiles_order() -> None:
    t = TextureId(8416, 5984, 14, "BI")
    tiles = texture_tiles(t)
    assert len(tiles) == 256
    assert tiles[0] == (8416, 5984)
    assert tiles[1] == (8417, 5984)
    assert tiles[16] == (8416, 5985)
    assert tiles[255] == (8431, 5999)
    assert len(set(tiles)) == 256


def test_textures_covering_reference_cell() -> None:
    ts = textures_covering(44, 5, 43, 6, 14, "BI")
    assert len(ts) == 20
    assert ts[0] == TextureId(8416, 5952, 14, "BI")
    assert ts[-1] == TextureId(8464, 6016, 14, "BI")
    assert ts[1] == TextureId(8432, 5952, 14, "BI")  # x inner
    assert ts == textures_covering(43, 6, 44, 5, 14, "BI")  # corners in any order
    assert all(x % 16 == 0 and y % 16 == 0 for x, y, _, _ in ts)


def test_textures_covering_single_texture() -> None:
    lat_max, lon_min, lat_min, lon_max = texture_bbox(TextureId(8416, 5984, 14, "BI"))
    eps = 1e-7
    ts = textures_covering(lat_max - eps, lon_min + eps, lat_min + eps, lon_max - eps, 14, "BI")
    assert ts == [TextureId(8416, 5984, 14, "BI")]


@pytest.mark.parametrize(
    ("x", "y", "zl", "key"),
    [
        (0, 0, 0, ""),
        (0, 0, 1, "0"),
        (1, 0, 1, "1"),
        (0, 1, 1, "2"),
        (1, 1, 1, "3"),
        (3, 5, 3, "213"),  # Bing Maps tile system documentation
        (16857, 11990, 15, "120222133031221"),  # Marseille probe of the providers audit
    ],
)
def test_quadkey_known_values(x: int, y: int, zl: int, key: str) -> None:
    assert quadkey(x, y, zl) == key


def test_parent_tile() -> None:
    assert parent_tile(16857, 11990) == (8428, 5995)
    assert parent_tile(16857, 11990, 0) == (16857, 11990)
    assert parent_tile(16857, 11990, 6) == (263, 187)
    with pytest.raises(ValueError):
        parent_tile(1, 1, -1)


def test_webmercator_pixel_size() -> None:
    assert webmercator_pixel_size(0, 0) == pytest.approx(156543.03392804097)
    assert webmercator_pixel_size(43.4, 16) == pytest.approx(2.3886 * 0.7266, rel=1e-3)


def test_st_coord_clamped() -> None:
    t = TextureId(8416, 5984, 14, "BI")
    lat_max, lon_min, lat_min, lon_max = texture_bbox(t)
    assert st_coord(lat_max, lon_min, t.til_x, t.til_y, 14) == pytest.approx((0.0, 1.0), abs=1e-9)
    assert st_coord(lat_min, lon_max, t.til_x, t.til_y, 14) == pytest.approx((1.0, 0.0), abs=1e-9)
    assert st_coord(lat_max + 1, lon_min - 1, t.til_x, t.til_y, 14) == (0.0, 1.0)


def test_zoom_level_bounds() -> None:
    with pytest.raises(ValueError):
        wgs84_to_tile(0, 0, 25)
    with pytest.raises(ValueError):
        tile_to_wgs84(0, 0, -1)


@settings(max_examples=300, deadline=None)
@given(
    lat=st.floats(-85.0, 85.0),
    lon=st.floats(-180.0, 180.0, exclude_max=True),
    zl=st.integers(10, 19),
)
def test_texture_contains_its_point(lat: float, lon: float, zl: int) -> None:
    t = texture_at(lat, lon, zl, "BI")
    assert t.til_x % 16 == 0 and t.til_y % 16 == 0
    lat_max, lon_min, lat_min, lon_max = texture_bbox(t)
    assert lat_min - 1e-9 <= lat <= lat_max + 1e-9
    assert lon_min - 1e-9 <= lon <= lon_max + 1e-9
    x, y = wgs84_to_tile(lat, lon, zl)
    assert t.til_x <= x <= t.til_x + 16 and t.til_y <= y <= t.til_y + 16
    assert grid.TEXTURE_TILES == 16
