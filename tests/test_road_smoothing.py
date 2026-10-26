# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The smoothing of the levelled roads (``docs/specs/vectors-water-roads.md`` 3.6).

The altitude of each levelled road polygon is averaged along its outline over 100 or 250 m, the
points staying where they are; off, nothing changes, the keys of the tiles built before included.
"""

from __future__ import annotations

import typing
from pathlib import Path

import numpy as np
import pytest
import shapely

from orthostudio.config import Settings
from orthostudio.config.models import Expert
from orthostudio.config.overrides import to_build_overrides
from orthostudio.errors import OsxpError
from orthostudio.graph import key_for
from orthostudio.model import TileRef
from orthostudio.tilefiles.config import OSXP_PARAMETERS
from orthostudio.vectors.roads import (
    ROAD_SMOOTHING_CHOICES,
    RoadParams,
    build_road_layers,
    shift_way,
    smooth_along,
)
from orthostudio.vectors.rule import VECTORS, VectorsParams
from orthostudio.vectors.water import LAT_TO_M, M_TO_LAT, scale_x
from test_vectors_roads import SlopedDem, store, straight

TILE = TileRef(43, 5)
SCALX = scale_x(TILE)


def ring_of(side_m: float, every_m: float, lon: float = 5.2, lat: float = 43.2) -> np.ndarray:
    """A closed square of ``side_m`` metres, a point every ``every_m`` metres, first == last."""
    n = round(side_m / every_m)
    t = np.arange(n) * every_m
    dx, dy = M_TO_LAT / SCALX, M_TO_LAT
    edges = [
        np.column_stack((lon + t * dx, np.full(n, lat))),
        np.column_stack((np.full(n, lon + side_m * dx), lat + t * dy)),
        np.column_stack((lon + (side_m - t) * dx, np.full(n, lat + side_m * dy))),
        np.column_stack((np.full(n, lon), lat + (side_m - t) * dy)),
    ]
    pts = np.vstack(edges)
    return np.vstack((pts, pts[:1]))


def along(way: np.ndarray) -> np.ndarray:
    """Metres along ``way`` at each of its points."""
    step = np.hypot(np.diff(way[:, 0]) * SCALX, np.diff(way[:, 1])) * LAT_TO_M
    return np.concatenate(([0.0], np.cumsum(step)))


def test_off_returns_the_altitudes_as_they_are() -> None:
    way = ring_of(500, 10)
    z = np.sin(along(way))
    out = smooth_along(way, z, 0, SCALX)
    np.testing.assert_array_equal(out, z)
    assert out is not z


def test_a_flat_road_stays_flat() -> None:
    way = ring_of(500, 10)
    out = smooth_along(way, np.full(len(way), 312.5), 100, SCALX)
    np.testing.assert_allclose(out, 312.5, atol=1e-9)


@pytest.mark.parametrize("window", [100, 250])
def test_the_small_waves_go_and_the_ring_stays_closed(window: int) -> None:
    """Waves of 25 m and 5 m high, as a 30 m relief makes them, come out under half a metre."""
    way = ring_of(1000, 5)
    s = along(way)
    z = 400.0 + 5.0 * np.sin(2 * np.pi * s / 25.0)
    out = smooth_along(way, z, window, SCALX)
    assert np.abs(out - 400.0).max() < 0.5
    assert out[0] == out[-1]
    assert len(out) == len(way)


def test_a_steady_slope_is_kept_on_an_open_way() -> None:
    """The mean of a straight slope is the slope: away from the ends nothing moves."""
    way = np.array([(5.2 + k * 10 * M_TO_LAT / SCALX, 43.2) for k in range(101)])  # 1 km east
    s = along(way)
    z = 200.0 + 0.05 * s
    out = smooth_along(way, z, 100, SCALX)
    inside = (s >= 50) & (s <= s[-1] - 50)
    np.testing.assert_allclose(out[inside], z[inside], atol=1e-6)


def test_a_ring_shorter_than_the_window_takes_its_mean() -> None:
    way = ring_of(20, 2)  # 80 m round
    s = along(way)
    z = 100.0 + s / 10.0
    z[-1] = z[0]
    out = smooth_along(way, z, 250, SCALX)
    assert np.ptp(out) < 1e-9
    assert out[0] == pytest.approx(np.trapezoid(z, s) / s[-1], abs=0.2)


def test_points_given_twice_do_no_harm() -> None:
    way = ring_of(300, 10)
    way = np.insert(way, [5, 5, 40], way[[5, 5, 40]], axis=0)
    z = 50.0 + np.cos(along(way) / 7.0)
    out = smooth_along(way, z, 100, SCALX)
    assert np.isfinite(out).all() and len(out) == len(way)
    assert out[5] == out[6] == out[7]


def test_the_choices_are_those_of_the_settings_page() -> None:
    literal = Expert.model_fields["road_smoothing"].annotation
    assert typing.get_args(literal) == ROAD_SMOOTHING_CHOICES == (0, 100, 250)
    assert Expert().road_smoothing == 0
    assert OSXP_PARAMETERS["road_smoothing"].default == 0


# -- the road builder ----------------------------------------------------------------------


def banked(tmp_path: Path, params: RoadParams):  # type: ignore[no-untyped-def]
    dem = SlopedDem(slope=1.0 / M_TO_LAT, tilt=3.0 / M_TO_LAT)
    data = store(tmp_path, [(straight(n=8), {"highway": "motorway"})])
    return dem, build_road_layers(data, TILE, dem, params)


def test_off_builds_the_roads_of_ortho4xp(tmp_path: Path) -> None:
    _dem, default = banked(tmp_path, RoadParams())
    _dem, off = banked(tmp_path, RoadParams(road_smoothing=0))
    (g0, m0, z0), (g1, m1, z1) = default.layers[0], off.layers[0]
    assert shapely.equals_exact(g0, g1, tolerance=0) and m0 == m1
    np.testing.assert_array_equal(z0, z1)


def test_on_moves_the_heights_and_not_the_points(tmp_path: Path) -> None:
    _dem, off = banked(tmp_path, RoadParams())
    dem, on = banked(tmp_path, RoadParams(road_smoothing=100))
    (g0, _m0, z0), (g1, _m1, z1) = off.layers[0], on.layers[0]
    assert shapely.equals_exact(g0, g1, tolerance=0)
    assert on.counts == off.counts
    assert not np.allclose(z0, z1)
    rings = [
        np.asarray(r.coords) for p in shapely.get_parts(g1) for r in (p.exterior, *p.interiors)
    ]
    expected = np.concatenate(
        [smooth_along(r, dem.alt_vec(shift_way(r, 4.0, "left", SCALX)), 100, SCALX) for r in rings]
    )
    np.testing.assert_allclose(z1, expected)


# -- settings, parameters and keys ---------------------------------------------------------


def test_the_setting_reaches_the_build() -> None:
    assert to_build_overrides(Settings())["road_smoothing"] == 0
    on = Settings(expert=Expert(road_smoothing=250))
    assert to_build_overrides(on)["road_smoothing"] == 250
    assert VECTORS.bind({"tile": "+43+005", "road_smoothing": 250}).road_smoothing == 250


def test_off_leaves_the_key_of_every_tile_built_before() -> None:
    """The field is out of the key at 0: the store finds the tiles of 1.2.0 as they are."""
    inputs = {"osm": "a" * 64, "dem": "b" * 64, "patches": None, "airports": None}
    inputs["global_scenery"] = None
    params = VectorsParams(tile=TILE.name)
    assert "road_smoothing" not in params.canonical()
    before = params.model_dump(mode="json")
    before.pop("road_smoothing")
    assert params.canonical() == before
    on = VectorsParams(tile=TILE.name, road_smoothing=100)
    assert on.canonical()["road_smoothing"] == 100
    assert key_for(VECTORS, on, inputs) != key_for(VECTORS, params, inputs)


@pytest.mark.parametrize("value", [50, -100, 500])
def test_a_length_the_page_does_not_offer_is_refused(value: int) -> None:
    with pytest.raises(OsxpError) as caught:
        VectorsParams(tile=TILE.name, road_smoothing=value)
    assert caught.value.code == "CFG_VALUE_INVALID"
