# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Orthophoto grid and gluing border (``docs/specs/vectors-assembly.md`` section 3).

The target is bit-identity with the ways Ortho4XP inserts, because these coordinates become
9-decimal node keys; it was measured on +43+005 before decision 0010 removed the comparison.
"""

from __future__ import annotations

from itertools import pairwise
from math import atan, exp, pi

import numpy as np
import shapely

from orthostudio.imagery.grid import TEXTURE_TILES, texture_at
from orthostudio.model import TileRef
from orthostudio.vectors.grid import (
    BORDER_SEGMENTS,
    GRID_MARGIN,
    gluing_border,
    grid_and_border,
    ortho_grid_abscissae,
    ortho_grid_lines,
)

TILE = TileRef(43, 5)
MESH_ZL = 19


def flat_alt(way: np.ndarray) -> np.ndarray:
    """A stand-in elevation: altitude = 100 x + y, so every sample is identifiable."""
    points = np.asarray(way, dtype=np.float64)
    return 100.0 * points[:, 0] + points[:, 1]


# ----------------------------------------------------------------------------- abscissae
def test_tile_edges_are_in_the_families_exactly_once() -> None:
    xgrid, ygrid = ortho_grid_abscissae(TILE, MESH_ZL)
    for family in (xgrid, ygrid):
        assert family == sorted(family)
        assert len(family) == len(set(family))
        assert family[0] == 0.0 and family[-1] == 1.0


def test_abscissae_are_the_texture_boundaries_at_mesh_zl() -> None:
    xgrid, _ = ortho_grid_abscissae(TILE, MESH_ZL)
    inner = [x for x in xgrid if 0.0 < x < 1.0]
    # every interior line is the western edge of a texture: the texture there starts on it
    for x in inner:
        left = texture_at(TILE.lat + 0.5, TILE.lon + x + 1e-9, MESH_ZL, "")
        assert abs(_texture_west(left) - (TILE.lon + x)) < 1e-9
    # two consecutive lines bound exactly one texture: nothing between them, 16 tiles across
    for a, b in pairwise(inner):
        after_a = texture_at(TILE.lat + 0.5, TILE.lon + a + 1e-9, MESH_ZL, "")
        before_b = texture_at(TILE.lat + 0.5, TILE.lon + b - 1e-9, MESH_ZL, "")
        after_b = texture_at(TILE.lat + 0.5, TILE.lon + b + 1e-9, MESH_ZL, "")
        assert after_a.til_x == before_b.til_x
        assert after_b.til_x - after_a.til_x == TEXTURE_TILES


def _texture_west(texture: object) -> float:
    til_x = getattr(texture, "til_x")  # noqa: B009 -- explicit, the NamedTuple is opaque here
    return (til_x / 2 ** (MESH_ZL - 1) - 1) * 180


def test_horizontal_abscissae_follow_the_mercator_formula() -> None:
    _, ygrid = ortho_grid_abscissae(TILE, MESH_ZL)
    north_west = texture_at(TILE.lat + 1, TILE.lon, MESH_ZL, "")
    expected = []
    for til_y in range(
        north_west.til_y + TEXTURE_TILES,
        texture_at(TILE.lat, TILE.lon + 1, MESH_ZL, "").til_y + 1,
        TEXTURE_TILES,
    ):
        pos_y = 1 - til_y / (2 ** (MESH_ZL - 1))
        expected.append(360 / pi * atan(exp(pi * pos_y)) - 90 - TILE.lat)
    assert sorted({*expected, 0.0, 1.0}) == ygrid


def test_a_coarser_zoom_gives_fewer_lines() -> None:
    fine_x, fine_y = ortho_grid_abscissae(TILE, 19)
    coarse_x, coarse_y = ortho_grid_abscissae(TILE, 17)
    assert len(coarse_x) < len(fine_x) and len(coarse_y) < len(fine_y)
    assert set(coarse_x) <= set(fine_x)  # a ZL-17 boundary is also a ZL-19 boundary


# ----------------------------------------------------------------------------- geometry
def test_grid_lines_overshoot_the_tile() -> None:
    vertical, horizontal = ortho_grid_lines(TILE, MESH_ZL)
    for line in vertical.geoms:
        (x0, y0), (x1, y1) = line.coords
        assert x0 == x1
        assert (y0, y1) == (-GRID_MARGIN, 1 + GRID_MARGIN)
    for line in horizontal.geoms:
        (x0, y0), (x1, y1) = line.coords
        assert y0 == y1
        assert (x0, x1) == (-GRID_MARGIN, 1 + GRID_MARGIN)


def test_border_is_four_polylines_of_2048_segments() -> None:
    border = gluing_border()
    assert len(border.geoms) == 4
    for line in border.geoms:
        coords = np.asarray(line.coords)
        assert len(coords) == BORDER_SEGMENTS + 1
        assert coords[0].min() == 0.0 and coords[-1].max() == 1.0
    south, north, west, east = (np.asarray(g.coords) for g in border.geoms)
    assert np.array_equal(south[:, 0], west[:, 1])  # same abscissae on every side
    assert (south[:, 1] == 0).all() and (north[:, 1] == 1).all()
    assert (west[:, 0] == 0).all() and (east[:, 0] == 1).all()


def test_border_vertices_are_exact_binary_fractions() -> None:
    coords = np.asarray(gluing_border().geoms[0].coords)[:, 0]
    assert np.array_equal(coords * BORDER_SEGMENTS, np.arange(BORDER_SEGMENTS + 1))


def test_three_passes_in_insertion_order_with_dem_altitudes() -> None:
    passes = grid_and_border(TILE, MESH_ZL, flat_alt)
    assert [p.name for p in passes] == ["grid_vertical", "grid_horizontal", "border"]
    for lines in passes:
        coords = shapely.get_coordinates(lines.geometry)
        assert len(lines.z) == len(coords)
        assert np.array_equal(lines.z, flat_alt(coords))


def test_border_segment_count_is_adjustable_for_tests() -> None:
    passes = grid_and_border(TILE, MESH_ZL, flat_alt, segments=8)
    assert len(shapely.get_coordinates(passes[2].geometry)) == 4 * 9
