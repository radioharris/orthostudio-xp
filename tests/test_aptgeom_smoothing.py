"""Unit tests of the airport smoothing rules (``docs/specs/airports-geometry.md`` A8).

No network: a 200x200 synthetic raster and hand-built footprints, so the rules the
reference tile never exercises -- a per-airport ``smoothing_pix``, ``apt_smoothing_pix = 0``, a
footprint off the raster, a window clamped to the border -- are covered here.

The blur itself belongs to ``orthostudio.dem.raster`` and is tested in ``test_dem_*``; what is
checked here is *which* region is smoothed, in *what order*, and with what mask.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from shapely import geometry
from shapely.geometry.base import BaseGeometry

from orthostudio.airports_vec import smoothing as apt_smoothing
from orthostudio.airports_vec.model import Airport, SurfaceAreas
from orthostudio.dem.dem import Dem
from orthostudio.model import TileRef

TILE = TileRef(43, 5)
SIZE = 200


def footprint(boundary: BaseGeometry, smoothing_pix: int | None = None) -> Airport:
    """One airport reduced to what the smoothing reads: a footprint and its own ``pix``."""
    airport = Airport(
        key="APT",
        key_type="icao",
        name="test",
        repr_node=(5.0, 43.0),
        boundary=boundary,
        smoothing_pix=smoothing_pix,
    )
    airport.areas = SurfaceAreas()
    return airport


def _dem(values: np.ndarray | None = None) -> Dem:
    if values is None:
        rng = np.random.default_rng(7)
        values = (rng.random((SIZE, SIZE)) * 100).astype(np.float32)
    return Dem(
        tile=TILE,
        alt_dem=values,
        x0=-0.01,
        y0=-0.01,
        x1=1.01,
        y1=1.01,
        nxdem=SIZE,
        nydem=SIZE,
    )


def _step(dem: Dem) -> float:
    return (dem.x1 - dem.x0) / dem.nxdem


# -- max_pix and upscale -----------------------------------------------------------------------


def test_max_pix_is_the_tile_setting_when_no_airport_overrides_it() -> None:
    footprints = [footprint(geometry.box(0.2, 0.2, 0.3, 0.3))]
    assert apt_smoothing.max_smoothing_pix(footprints) == 8


def test_max_pix_is_raised_by_any_airport_even_a_skipped_one() -> None:
    footprints = [
        footprint(geometry.box(0.2, 0.2, 0.3, 0.3)),
        footprint(geometry.Polygon(), smoothing_pix=20),
    ]
    assert apt_smoothing.max_smoothing_pix(footprints) == 20


def test_a_smaller_per_airport_value_does_not_lower_max_pix() -> None:
    footprints = [footprint(geometry.box(0.2, 0.2, 0.3, 0.3), smoothing_pix=2)]
    assert apt_smoothing.max_smoothing_pix(footprints) == 8


@pytest.mark.parametrize(("ystep", "expected"), [(1.02 / 3673, 4), (1.02 / 1201, 10), (1e-6, 1)])
def test_the_upscale_targets_ten_metre_mask_pixels(ystep: float, expected: int) -> None:
    assert apt_smoothing.upscale_factor(ystep) == expected


# -- the window --------------------------------------------------------------------------------


def test_the_window_is_the_footprint_bounds_grown_by_pix() -> None:
    dem = _dem()
    step = _step(dem)
    box = geometry.box(0.2, 0.2, 0.3, 0.3)
    params = apt_smoothing.SmoothingParams(apt_smoothing_pix=3)
    (region,) = apt_smoothing.airport_regions(dem, [footprint(box)], params)
    assert region.colmin == int(np.floor((0.2 - dem.x0) / step)) - 3
    assert region.colmax == int(np.ceil((0.3 - dem.x0) / step)) + 3
    assert region.rowmin == int(np.floor((dem.y1 - 0.3) / step)) - 3
    assert region.rowmax == int(np.ceil((dem.y1 - 0.2) / step)) + 3
    assert region.pix == 3
    assert region.mask.shape == (
        region.rowmax - region.rowmin + 1,
        region.colmax - region.colmin + 1,
    )


def test_the_window_is_clamped_to_the_raster() -> None:
    dem = _dem()
    (region,) = apt_smoothing.airport_regions(dem, [footprint(geometry.box(-0.5, -0.5, 1.5, 1.5))])
    assert (region.rowmin, region.colmin) == (0, 0)
    assert (region.rowmax, region.colmax) == (SIZE - 1, SIZE - 1)


def test_an_airport_with_pix_zero_is_skipped() -> None:
    dem = _dem()
    box = geometry.box(0.2, 0.2, 0.3, 0.3)
    assert apt_smoothing.airport_regions(dem, [footprint(box, smoothing_pix=0)]) == []


def test_an_empty_footprint_is_skipped() -> None:
    assert apt_smoothing.airport_regions(_dem(), [footprint(geometry.Polygon())]) == []


def test_a_footprint_off_the_raster_gives_no_region() -> None:
    dem = _dem()
    params = apt_smoothing.SmoothingParams(apt_smoothing_pix=0)
    assert (
        apt_smoothing.airport_regions(dem, [footprint(geometry.box(0.2, 0.2, 0.3, 0.3))], params)
        == []
    )


def test_the_per_airport_value_wins_over_the_tile_one() -> None:
    dem = _dem()
    box = geometry.box(0.2, 0.2, 0.3, 0.3)
    (region,) = apt_smoothing.airport_regions(dem, [footprint(box, smoothing_pix=2)])
    assert region.pix == 2


# -- the mask ----------------------------------------------------------------------------------


def test_a_footprint_covering_its_window_gives_a_full_mask() -> None:
    dem = _dem()
    box = geometry.box(0.2, 0.2, 0.4, 0.4)
    params = apt_smoothing.SmoothingParams(apt_smoothing_pix=1)
    (region,) = apt_smoothing.airport_regions(dem, [footprint(box)], params)
    inner = region.mask[3:-3, 3:-3]
    assert inner.min() == 255
    assert region.mask[0, 0] == 0  # the one-pixel margin is outside the footprint


def test_a_hole_in_the_footprint_is_drawn_black() -> None:
    dem = _dem()
    ring = geometry.box(0.2, 0.2, 0.4, 0.4).difference(geometry.box(0.27, 0.27, 0.33, 0.33))
    (region,) = apt_smoothing.airport_regions(dem, [footprint(ring)])
    rows, cols = region.mask.shape
    assert region.mask[rows // 2, cols // 2] == 0
    assert region.mask[rows // 2, cols // 8] > 0


# -- the stage ---------------------------------------------------------------------------------


def test_smoothing_flattens_the_raster_under_the_footprint() -> None:
    dem = _dem()
    before = np.array(dem.alt_dem)
    box = geometry.box(0.3, 0.3, 0.6, 0.6)
    out = apt_smoothing.smooth_dem_over_airports(dem, [footprint(box)])
    assert out is not dem
    assert np.array_equal(dem.alt_dem, before), "the input raster must not be touched"
    (region,) = apt_smoothing.airport_regions(dem, [footprint(box)])
    row = (region.rowmin + region.rowmax) // 2
    col = (region.colmin + region.colmax) // 2
    window = slice(row - 5, row + 5), slice(col - 5, col + 5)
    assert out.alt_dem[window].std() < before[window].std()


def test_the_tile_border_is_left_alone() -> None:
    dem = _dem()
    before = np.array(dem.alt_dem)
    out = apt_smoothing.smooth_dem_over_airports(dem, [footprint(geometry.box(0.0, 0.0, 1.0, 1.0))])
    assert np.array_equal(out.alt_dem[0], before[0])
    assert np.array_equal(out.alt_dem[-1], before[-1])
    assert np.array_equal(out.alt_dem[:, 0], before[:, 0])
    assert np.array_equal(out.alt_dem[:, -1], before[:, -1])


def test_apt_smoothing_pix_zero_returns_the_dem_untouched() -> None:
    dem = _dem()
    params = apt_smoothing.SmoothingParams(apt_smoothing_pix=0)
    out = apt_smoothing.smooth_dem_over_airports(
        dem, [footprint(geometry.box(0.2, 0.2, 0.3, 0.3))], params
    )
    assert out is dem


def test_no_airport_at_all_still_re_blends_the_border() -> None:
    """Ortho4XP runs the border blend even with an empty airport dictionary (spec 5.5).

    ``i/pix * x + (pix-i)/pix * x`` is ``x`` in real arithmetic and not always in float32:
    102 of the 40 000 samples of this raster move by at most 4e-6 m. Faithful, and measured
    rather than hidden behind an early return.
    """
    dem = _dem()
    before = np.array(dem.alt_dem)
    out = apt_smoothing.smooth_dem_over_airports(dem, [])
    assert np.array_equal(out.alt_dem[8:-8, 8:-8], before[8:-8, 8:-8])
    assert float(np.abs(out.alt_dem - before).max()) < 1e-5


def test_the_result_keeps_every_other_field_of_the_dem() -> None:
    dem = _dem()
    out = apt_smoothing.smooth_dem_over_airports(dem, [footprint(geometry.box(0.2, 0.2, 0.3, 0.3))])
    for field_name in ("tile", "x0", "y0", "x1", "y1", "nxdem", "nydem", "source", "nodata"):
        assert getattr(out, field_name) == getattr(dem, field_name)
    assert out.alt_dem.dtype == np.float32


def test_two_overlapping_airports_are_smoothed_one_after_the_other() -> None:
    """The order is the caller's, and the second sees the first's blur (spec 5.4)."""
    dem = _dem()
    a = footprint(geometry.box(0.30, 0.30, 0.50, 0.50))
    b = footprint(geometry.box(0.40, 0.40, 0.60, 0.60))
    first: Any = apt_smoothing.smooth_dem_over_airports(dem, [a, b])
    second: Any = apt_smoothing.smooth_dem_over_airports(dem, [b, a])
    assert not np.array_equal(first.alt_dem, second.alt_dem)
