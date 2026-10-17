# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A coastline that would make a whole tile sea where X-Plane draws land is ignored
(``docs/specs/vectors-coastline.md`` section 9).

Tehran, 2026-10-01: a market tagged ``natural=coastline``, a ring alone in the tile, and the whole
tile became sea at 0 m but for the market. Manaus the next day with a hotel; eleven such tiles in
the library of 2026-10-07.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from shapely import geometry

from orthostudio.model import TileRef
from orthostudio.vectors import layers
from orthostudio.vectors.coast import LAND_SHARE_MAX, build_sea_layers

TILE = TileRef(35, 51)


def ring(x: float, y: float, r: float, ccw: bool = True) -> geometry.MultiLineString:
    """A closed square line in tile-local coordinates, counter-clockwise (an island) by default."""
    pts = [(x - r, y - r), (x + r, y - r), (x + r, y + r), (x - r, y + r), (x - r, y - r)]
    return geometry.MultiLineString([pts if ccw else pts[::-1]])


def never(_area: object) -> float:
    raise AssertionError("X-Plane's land is asked only when the coastline makes the tile all sea")


def test_a_ring_alone_on_land_is_ignored_and_the_tile_is_land() -> None:
    market = ring(0.03, 0.71, 0.001)
    flooded = build_sea_layers(market, TILE)
    assert flooded.stats.tile_assumed_sea and flooded.sea_polygons.area > 0.99
    out = build_sea_layers(market, TILE, land=lambda area: 1.0)
    assert out.sea_polygons.is_empty and out.sea_lines.is_empty and len(out.seeds) == 0
    assert out.on_land == 1.0
    ((lat, lon),) = out.on_land_at
    assert abs(lat - 35.71) < 0.002 and abs(lon - 51.03) < 0.002
    assert out.stats.ways_read == flooded.stats.ways_read and not out.stats.tile_assumed_sea


def test_an_island_in_a_true_sea_keeps_its_sea() -> None:
    """The Maldives: X-Plane's sea where ours is; the very result of before."""
    island = ring(0.5, 0.5, 0.01)
    before = build_sea_layers(island, TILE)
    for land in (lambda area: 0.003, lambda area: LAND_SHARE_MAX, lambda area: None):
        after = build_sea_layers(island, TILE, land=land)
        assert after.on_land is None
        assert after.sea_polygons.equals(before.sea_polygons)
        assert np.array_equal(after.seeds, before.seeds)
        assert after.sea_lines.equals(before.sea_lines)


def test_a_coast_across_the_tile_never_asks() -> None:
    coast = geometry.MultiLineString([[(1.0, 0.5), (0.0, 0.5)]])
    before = build_sea_layers(coast, TILE)
    after = build_sea_layers(coast, TILE, land=never)
    assert after.sea_polygons.equals(before.sea_polygons) and after.on_land is None


def test_x_planes_land_is_what_stands_above_its_sea(monkeypatch: pytest.MonkeyPatch) -> None:
    """Land above 0.5 m, X-Plane's sea at 0 m; a sea below it (the Caspian at -28 m) is no land,
    and a post without a value counts for nothing. The posts come north to south
    (``elevation_posts``): read the other way, Jeju's island fell in our sea and its sea on the
    island (+33+126, 2026-10-13)."""
    z = np.zeros((1201, 1201), dtype=np.float32)
    z[:, :600] = 1100.0  # the west half is Tehran's plateau
    z[:, 900:] = -28.0  # the east quarter a sea below sea level
    z[:100, :] = -32768  # no value along the north edge
    z[1100:, :600] = 0.0  # the south-west corner is sea
    monkeypatch.setattr(layers, "read_elevation_posts", lambda path, lat, lon: z)
    share = layers.xp_land_share(Path("x.dsf"), TILE)
    assert share is not None
    west = geometry.box(0.0, 0.2, 0.45, 0.9)
    east = geometry.box(0.8, 0.2, 1.0, 0.9)
    assert share(west) == 1.0 and share(east) == 0.0
    assert share(geometry.box(0.0, 0.0, 0.45, 0.07)) == 0.0  # the south-west corner, not the north
    assert abs(share(geometry.box(0.0, 0.2, 1.0, 0.9)) - 0.5) < 0.01
    assert share(geometry.box(0.0, 0.95, 1.0, 1.0)) is None  # only posts without a value
    assert layers.xp_land_share(None, TILE) is None


def test_an_unreadable_dsf_leaves_the_coastline_as_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    from orthostudio.errors import OsxpError

    def broken(path: Path, lat: int, lon: int) -> np.ndarray:
        raise OsxpError("DSF_SOURCE_CORRUPTED", context={"tile": TILE.name, "reason": "test"})

    monkeypatch.setattr(layers, "read_elevation_posts", broken)
    share = layers.xp_land_share(Path("x.dsf"), TILE)
    assert share is not None and share(geometry.box(0, 0, 1, 1)) is None
    out = build_sea_layers(ring(0.03, 0.71, 0.001), TILE, land=share)
    assert out.on_land is None and out.stats.tile_assumed_sea
