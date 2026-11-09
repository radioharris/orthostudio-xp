# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The tile's picture the cloud mode draws (``clouds/picture.py``): where each texture shows is
filled from the DSF's triangles once at the map's level and kept with them, and a picture drawn
from what was kept is the one drawn without it. On a Windows server, filling them again was 9 of
the 10 s each picture of a tile of 3 M triangles took (+52+004, 2026-11-07)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from orthostudio.clouds import picture
from orthostudio.imagery.grid import TextureId, texture_name
from orthostudio.model import TileRef
from orthostudio.textures.encode import encode_dds

TILE = TileRef(43, 5)
WEST = TextureId(til_x=2096, til_y=1488, zl=12, provider="T")
EAST = TextureId(til_x=2112, til_y=1488, zl=12, provider="T")


@pytest.fixture(scope="module")
def textures(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Two textures of the tile as the build encodes them, each its own colours."""
    folder = tmp_path_factory.mktemp("textures")
    out = {}
    y, x = np.mgrid[0:4096, 0:4096]
    for seed, t in enumerate((WEST, EAST)):
        rgb = np.dstack([(x * 7 + seed) % 200 + 20, (y * 5) % 180 + 30, (x + y) % 150 + 40])
        path = folder / f"{texture_name(t)}.dds"
        path.write_bytes(encode_dds(rgb.astype(np.uint8), "bc1", encoder="ispc"))
        out[texture_name(t)] = path
    return out


def _dsf() -> Any:
    """A DSF as :func:`picture._read_dsf` gives it: each texture's square in two triangles of
    land, the west one's cut by a triangle of sea."""
    gx0, gy0, _w, _h = picture.frame(TILE, picture._CORNERS_ZL)
    k = 2 ** (picture._CORNERS_ZL - 12)
    xs, ys, terrains = [], [], []
    for t in (WEST, EAST):
        x0, y0 = t.til_x * 256 * k - gx0, t.til_y * 256 * k - gy0
        x1, y1 = x0 + 4096 * k, y0 + 4096 * k
        xs += [[x0, x1, x1], [x0, x1, x0]]
        ys += [[y0, y0, y1], [y0, y1, y1]]
        terrains.append((texture_name(t), picture.LAND, 1.0))
    x0, y0 = WEST.til_x * 256 * k - gx0, WEST.til_y * 256 * k - gy0
    xs.append([x0, x0 + 2048 * k, x0])
    ys.append([y0, y0, y0 + 2048 * k])
    terrains.append((texture_name(WEST), picture.SEA, 1.0))
    order = [0, 1, 4, 2, 3]  # sorted by terrain: west's land, west's sea, east's land
    return picture._Drawn(
        np.asarray(xs, np.float32)[order],
        np.asarray(ys, np.float32)[order],
        np.array([0, 2, 3, 5]),
        (terrains[0], terrains[2], terrains[1]),
        (gx0, gy0),
    )


def _drawing(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[int]]:
    d = _dsf()
    monkeypatch.setattr(picture, "_drawn", lambda dsf, tile: d)
    fills: list[int] = []
    fill = picture._fill
    monkeypatch.setattr(picture, "_fill", lambda *a: (fills.append(1), fill(*a))[1])
    return d, fills


def _same(a: picture.Picture, b: picture.Picture) -> bool:
    return a.names == b.names and all(
        np.array_equal(getattr(a, k), getattr(b, k)) for k in ("rgb", "photo", "share", "shows")
    )


def test_where_each_texture_shows_is_filled_once_at_the_maps_level(
    textures: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    d, fills = _drawing(monkeypatch)
    zl = picture.map_level(TILE, [WEST, EAST])
    first = picture.draw(TILE, zl, dsf=Path("dsf"), textures=textures)
    filled = len(fills)
    assert filled == 3 and list(d.laid) == [zl]  # its three terrains, kept
    again = picture.draw(TILE, zl, dsf=Path("dsf"), textures=textures)
    assert len(fills) == filled  # drawn again from what was kept: nothing filled
    assert _same(first, again) and first.names == tuple(textures)


def test_a_picture_from_what_was_kept_is_the_one_drawn_afresh(
    textures: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    zl = picture.map_level(TILE, [WEST, EAST])
    _drawing(monkeypatch)
    picture.draw(TILE, zl, dsf=Path("dsf"), textures=textures)
    kept = picture.draw(TILE, zl, dsf=Path("dsf"), textures=textures)
    _drawing(monkeypatch)  # a DSF read again: nothing kept
    fresh = picture.draw(TILE, zl, dsf=Path("dsf"), textures=textures)
    assert _same(kept, fresh)
    # a texture missing from the folder is left out of the picture, as before
    west = {texture_name(WEST): textures[texture_name(WEST)]}
    alone = picture.draw(TILE, zl, dsf=Path("dsf"), textures=west)
    assert alone.names == (texture_name(WEST),)


def test_another_level_is_filled_each_time_and_not_kept(
    textures: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # another level (a search's finer reading) is drawn once and big: kept, it would only
    # weigh on the memory
    d, fills = _drawing(monkeypatch)
    zl = picture.map_level(TILE, [WEST, EAST]) - 1
    picture.draw(TILE, zl, dsf=Path("dsf"), textures=textures)
    picture.draw(TILE, zl, dsf=Path("dsf"), textures=textures)
    assert len(fills) == 6 and d.laid == {}


# -- a cloud's layer reduced to the picture (``find._lay_reduced``) ------------------------------


def _layer(seed: int, window: tuple[int, int, int, int]) -> Any:
    from orthostudio.clouds.layer import Layer

    rng = np.random.default_rng(seed)
    w, h = window[2] - window[0], window[3] - window[1]
    source = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    alpha = rng.integers(0, 256, (h, w), dtype=np.uint8)
    return Layer(window, source, alpha, ((1.3, 0.8, 1.1), (25.0, -30.0, 0.5)))


def test_a_layers_table_is_its_colours_in_linear_light() -> None:
    la = _layer(1, (0, 0, 64, 64))
    table = la.linear()
    read = np.stack([table[c][la.source[..., c]] for c in range(3)], axis=-1)
    assert np.array_equal(read, np.power(la.rgb() / 255.0, 2.2))  # the same numbers, all 256


def test_a_layer_reduced_through_its_table_is_the_one_reduced_pixel_by_pixel() -> None:
    from orthostudio.clouds import find

    # what the reduction computed for each pixel before its table (2026-11-07)
    def before(la: Any, k: int, ay0: int, ax0: int, h: int, w: int) -> tuple[Any, Any]:
        wx0, wy0, wx1, wy1 = la.window
        src = np.zeros((h, w, 3), np.float32)
        a = np.zeros((h, w), np.float32)
        a[wy0 - ay0 : wy1 - ay0, wx0 - ax0 : wx1 - ax0] = la.alpha.astype(np.float32) / 255
        src[wy0 - ay0 : wy1 - ay0, wx0 - ax0 : wx1 - ax0] = la.rgb()
        ra = find._reduce(a, k)
        lin = find._reduce(np.power(src / 255.0, 2.2) * a[..., None], k)
        return ra, np.power(lin / np.maximum(ra[..., None], 1e-6), 1 / 2.2) * 255.0

    t = TextureId(til_x=0, til_y=0, zl=15, provider="T")
    k, side = 8, 4096 // 8
    blank = np.zeros((side, side), np.uint8)
    p = picture.Picture(
        np.zeros((side, side, 3), np.uint8), blank, blank, blank.astype(np.int16), ("x",), 12, 0, 0
    )
    for window in ((5, 3, 1203, 701), (0, 0, 4096, 1100), (1001, 2050, 1009, 2057)):
        la = _layer(2, window)
        rgb = np.zeros((side, side, 3), np.float32)
        alpha = np.zeros((side, side), np.float32)
        find._lay_reduced(la, t, p, (0, 0, side, side), "x", rgb, alpha)
        ax0, ay0 = (window[0] // k) * k, (window[1] // k) * k
        h, w = -(-window[3] // k) * k - ay0, -(-window[2] // k) * k - ax0
        ra, colour = before(la, k, ay0, ax0, h, w)
        y, x = ay0 // k, ax0 // k
        assert np.array_equal(alpha[y : y + ra.shape[0], x : x + ra.shape[1]], ra)
        on = ra > 0
        assert np.array_equal(rgb[y : y + ra.shape[0], x : x + ra.shape[1]][on], colour[on])


def test_a_clouds_textures_lay_the_same_picture_in_any_order_and_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a picture's pixel shows one texture: one of a zone inside another's square, laid in any
    # order and four at once, gives the same picture
    from orthostudio.clouds import find

    big = TextureId(til_x=0, til_y=0, zl=15, provider="T")  # 512 px of the picture
    small = TextureId(til_x=0, til_y=0, zl=16, provider="T")  # its first 256, a zone
    shows = np.zeros((512, 512), np.int16)
    shows[:200, :200] = 1  # where X-Plane shows the zone's texture
    blank = np.zeros((512, 512), np.uint8)
    p = picture.Picture(
        np.zeros((512, 512, 3), np.uint8), blank, blank, shows, ("b", "s"), 12, 0, 0
    )
    made = [
        ("b", big, lambda: _layer(3, (0, 0, 2400, 2400))),
        ("s", small, lambda: _layer(4, (0, 0, 4096, 4096))),
    ]

    def laid(order: list[Any], at_once: int) -> tuple[Any, Any]:
        monkeypatch.setattr(find, "LAYING_AT_ONCE", at_once)
        rgb = np.zeros((512, 512, 3), np.float32)
        alpha = np.zeros((512, 512), np.float32)
        find._all_reduced(p, (0, 0, 512, 512), rgb, alpha, order)
        return rgb, alpha

    one = laid(made, 1)
    other = laid(made[::-1], 4)
    assert np.array_equal(one[0], other[0]) and np.array_equal(one[1], other[1])
    assert (one[1][:200, :200] > 0).any() and (one[1][220:300, 220:300] > 0).any()  # both laid
