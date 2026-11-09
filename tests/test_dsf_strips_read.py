# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A DSF's pools and strips as the decoder reads them (orthostudio.dsf.strips)."""

from __future__ import annotations

import struct

import numpy as np

from orthostudio.dsf.strips import FAN, STRIP, TRI, _triangles, read_pool


def test_a_raw_pool() -> None:
    table, raw = read_pool(struct.pack("<IB", 3, 1) + bytes([0]) + struct.pack("<3H", 5, 9, 2))
    assert raw
    assert table.tolist() == [[5], [9], [2]]


def test_a_pool_differenced_and_run_length_encoded() -> None:
    plane = bytes([3, 0x82]) + struct.pack("<H", 10) + bytes([2]) + struct.pack("<2H", 1, 2)
    table, raw = read_pool(struct.pack("<IB", 4, 1) + plane)
    assert not raw
    assert table.tolist() == [[10], [20], [21], [23]]


def test_triangles_of_each_kind_keep_their_winding() -> None:
    points = [np.array([0, 1, 2, 3])]
    assert _triangles(STRIP, points).tolist() == [[0, 1, 2], [2, 1, 3]]
    assert _triangles(FAN, points).tolist() == [[0, 1, 2], [0, 2, 3]]
    assert _triangles(TRI, [np.array([0, 1, 2, 3, 4, 5])]).tolist() == [[0, 1, 2], [3, 4, 5]]
