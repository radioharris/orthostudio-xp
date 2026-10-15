# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A water node a hair's breadth off the shore is shallow (``dsf/bathy.near_shore``)."""

from __future__ import annotations

import numpy as np

from orthostudio.dsf.bathy import SHORE_SHALLOW_M, depth_ratio_u16, near_shore
from orthostudio.dsf.recut import INLAND, LAND, recut_water_tris

LAT = 37.27
M_LAT = 1 / 111320.0


def _mesh(gap_m: float):
    """A straight shore A-B along ``LAT``, land L to the north, and to the south a water node W1
    ``gap_m`` off the shore and W2 50 m off it, as a thin sliver along a cut leaves them."""
    nodes = [
        (-115.8, LAT),  # A, shore
        (-115.799, LAT),  # B, shore
        (-115.7995, LAT + 50 * M_LAT),  # L, land
        (-115.7995, LAT - gap_m * M_LAT),  # W1
        (-115.7995, LAT - 50 * M_LAT),  # W2
    ]
    coords = np.zeros((len(nodes), 5))
    coords[:, :2] = nodes
    coords[:, 2] = 1351.5
    tris = np.array([[0, 1, 2], [0, 3, 1], [0, 4, 3], [3, 4, 1]])
    types = np.array([LAND, INLAND, INLAND, INLAND], dtype=np.uint8)
    return recut_water_tris(coords, tris, types)


def test_a_water_node_off_the_shore_by_centimetres_is_shallow():
    rm = _mesh(0.3)
    near = near_shore(rm.coords, rm.tris, rm.tri_types, rm.node_is_coast, LAT)
    assert near.tolist() == [False, False, False, True, False]
    ratio = depth_ratio_u16(rm.node_is_coast, np.full(rm.n_nodes, 255, np.uint8), 1.0)
    ratio[near] = 0
    assert ratio[3] == 0 and ratio[4] == 65535  # W1 shallow, W2 keeps its depth


def test_a_node_farther_than_the_distance_keeps_its_depth():
    rm = _mesh(2.0)
    assert not near_shore(rm.coords, rm.tris, rm.tri_types, rm.node_is_coast, LAT).any()
    assert SHORE_SHALLOW_M < 2.0


def test_the_distance_is_a_parameter():
    rm = _mesh(0.3)
    near = near_shore(rm.coords, rm.tris, rm.tri_types, rm.node_is_coast, LAT, metres=0.1)
    assert not near.any()


def test_water_without_land_has_no_shore():
    coords = np.zeros((3, 5))
    coords[:, :2] = [(-115.8, LAT), (-115.799, LAT), (-115.7995, LAT - 0.3 * M_LAT)]
    rm = recut_water_tris(coords, np.array([[0, 1, 2]]), np.array([INLAND], dtype=np.uint8))
    assert not near_shore(rm.coords, rm.tris, rm.tri_types, rm.node_is_coast, LAT).any()
