# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Round trips of the Triangle text formats written by Ortho4XP."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from orthostudio.vectors.triangle_files import (
    read_node_file,
    read_poly_file,
    write_node_file,
    write_poly_file,
)

pytestmark = pytest.mark.usefixtures("number_parsing")

ORTHO4XP_POLY = """0 2 1 0

3 1
1 1 2 16
2 2 3 0
3 3 1 3

0

2
1 0.213983750683422 0.444560921876941 128
2 0.500000000000000 0.500000000000000 1
"""

ORTHO4XP_NODE = """3 2 1 0
1 0.117254700 0.605783100 59.649825207
2 0.117222000 0.605866200 59.680110609
3 1.000000000 0.999023438 732.312510490
"""


def test_read_the_layout_ortho4xp_writes(tmp_path: Path) -> None:
    (tmp_path / "t.node").write_text(ORTHO4XP_NODE)
    (tmp_path / "t.poly").write_text(ORTHO4XP_POLY)
    nodes = read_node_file(tmp_path / "t.node")
    poly = read_poly_file(tmp_path / "t.poly", nodes.first_index)
    assert nodes.first_index == 1
    assert nodes.xy.shape == (3, 2) and nodes.attributes.shape == (3, 1)
    assert nodes.attributes[2, 0] == 732.312510490
    assert poly.segments.tolist() == [[0, 1], [1, 2], [2, 0]]
    assert poly.markers.tolist() == [16, 0, 3]
    assert poly.holes.shape == (0, 2)
    assert poly.seeds.tolist()[1] == [0.5, 0.5, 1.0]


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    nodes = np.array([[0.0, 0.0, 1.5], [1.0, 0.0, 2.123456789], [1.0, 1.0, -3.0]])
    edges = np.array([[0, 1], [1, 2], [2, 0]], dtype=np.int32)
    markers = np.array([1, 2, 128], dtype=np.uint8)
    seeds = {"WATER": np.array([[0.5, 0.25]]), 16: np.array([[0.1, 0.1], [0.2, 0.2]])}
    write_node_file(tmp_path / "x.node", nodes)
    write_poly_file(tmp_path / "x.poly", edges, markers, seeds)
    text = (tmp_path / "x.poly").read_text()
    assert text.startswith("0 2 1 0\n\n3 1\n1 1 2 1\n")
    assert "\n0\n\n3\n" in text  # no hole, three seeds
    back_nodes = read_node_file(tmp_path / "x.node")
    back = read_poly_file(tmp_path / "x.poly", back_nodes.first_index)
    assert np.allclose(back_nodes.xy, nodes[:, :2]) and np.allclose(
        back_nodes.attributes[:, 0], nodes[:, 2]
    )
    assert back.segments.tolist() == edges.tolist()
    assert back.markers.tolist() == markers.tolist()
    # seeds sorted by marker value: WATER (1) first, then the two RUNWAY (16)
    assert back.seeds[:, 2].tolist() == [1.0, 16.0, 16.0]
