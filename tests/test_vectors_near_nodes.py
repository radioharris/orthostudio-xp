# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A node lying next to an edge it does not end is put on that edge (``vectors-pslg.md`` 2.14).

``+35+139`` (Tokyo, 2026-10-13): a canal node 7 micrometres off a river's edge at Odaiba, runway
outlines laid twice 1 to 4 cm apart at Haneda. Triangle4XP filled the slivers they leave with
hundreds of thousands of points, and X-Plane never finished draping RJTT's painted lines over
them. Every graph here is in tile-local degrees, ``lat`` 35 as for that tile.
"""

from __future__ import annotations

import numpy as np

from orthostudio.vectors.noding import (
    MARKERS,
    NEAR_NODE_M,
    NodedGraph,
    check_planar,
    join_near_nodes,
)

LAT = 35
WATER, SEA = MARKERS["WATER"], MARKERS["SEA"]
#: Metres of a degree of latitude, as ``join_near_nodes`` counts them.
LAT_TO_M = np.pi * 6378137.0 / 180


def graph(nodes: list[tuple[float, float]], edges: list[tuple[int, int, int]]) -> NodedGraph:
    xyz = np.column_stack([np.array(nodes, dtype=np.float64), np.zeros(len(nodes))])
    pairs = np.array([(a, b) for a, b, _ in edges], dtype=np.int32)
    return NodedGraph(xyz, pairs, np.array([m for _, _, m in edges], dtype=np.uint8))


def edge_list(g: NodedGraph) -> list[tuple[int, int, int]]:
    return [(int(a), int(b), int(m)) for (a, b), m in zip(g.edges, g.markers, strict=True)]


def test_the_canal_node_of_odaiba_goes_on_the_river_edge() -> None:
    """OSM's own coordinates at Odaiba: the river goes 0 -> 1, the canal 0 -> 2 -> 1, node 2
    lies 7 micrometres off the river's edge. Afterwards the river follows the canal: two edges,
    no sliver left between them."""
    g = graph(
        [(0.8055468, 0.6575799), (0.8052853, 0.6574032), (0.8054179, 0.6574928)],
        [(0, 1, WATER), (0, 2, WATER), (1, 2, WATER)],
    )
    joined, contacts = join_near_nodes(g, LAT)
    assert contacts == 1
    assert edge_list(joined) == [(0, 2, WATER), (1, 2, WATER)]
    assert np.array_equal(joined.nodes, g.nodes)  # no node moves


def test_the_near_nodes_of_one_edge_are_chained_in_order() -> None:
    """A 100 m sea edge with two shore nodes 2 mm off it, each tied to one of its ends by a water
    edge: the sea edge goes through both, in order along it, at its own place among the edges,
    and the edges that became one carry both markers."""
    off = 0.002 / LAT_TO_M
    g = graph(
        [(0.1, 0.1), (0.1011, 0.1), (0.1008, 0.1 + off), (0.1003, 0.1 - off)],
        [(0, 3, WATER), (0, 1, SEA), (1, 2, WATER)],
    )
    joined, contacts = join_near_nodes(g, LAT)
    assert contacts == 2
    assert edge_list(joined) == [(0, 3, WATER | SEA), (2, 3, SEA), (1, 2, SEA | WATER)]


def test_a_node_of_another_shape_stays_apart() -> None:
    """+46+006, the Dranse: an islet's shore passes 10 cm from the river bank, a node of it 2 cm
    from a bank edge. Joining it would make the two shores touch and could shut a pocket of the
    river away from its seed; the node shares no edge with that bank edge, so it stays apart."""
    off = 0.02 / LAT_TO_M
    g = graph(
        [(0.2, 0.2), (0.2008, 0.2), (0.2004, 0.2 + off), (0.2004, 0.2005)],
        [(0, 1, WATER), (2, 3, WATER)],
    )
    joined, contacts = join_near_nodes(g, LAT)
    assert contacts == 0 and joined is g


def test_a_join_that_would_cross_an_edge_is_not_made() -> None:
    """+22+113: in a knot of nodes millimetres apart a bent edge could cross another one. The
    shore node 2 mm above the edge 0-1 is tied to its end 0, but the bent piece 2-1 would cut the
    little edge 3-4 standing between them: the edge 0-1 is left as it was. (3 and 4 are shore
    nodes of the same water, tied to neither end, so they stay apart too.)"""
    mm = 0.001 / LAT_TO_M
    g = graph(
        [
            (0.3, 0.3),
            (0.3004, 0.3),
            (0.3001, 0.3 + 2 * mm),
            (0.3003, 0.3 + 0.5 * mm),
            (0.3003, 0.3 + 1.5 * mm),
        ],
        [(0, 1, WATER), (0, 2, WATER), (3, 4, WATER)],
    )
    joined, contacts = join_near_nodes(g, LAT)
    assert contacts == 0 and joined is g
    assert check_planar(joined.nodes, joined.edges) == 0


def test_edges_that_become_one_carry_both_markers() -> None:
    """A sea edge and a water path over the same two ends, 3 mm apart: one edge, both bits."""
    off = 0.003 / LAT_TO_M
    g = graph(
        [(0.3, 0.3), (0.3005, 0.3), (0.30025, 0.3 + off)],
        [(0, 1, SEA), (0, 2, WATER), (1, 2, WATER)],
    )
    joined, _ = join_near_nodes(g, LAT)
    assert edge_list(joined) == [(0, 2, SEA | WATER), (1, 2, SEA | WATER)]


def test_a_node_beyond_the_threshold_stays_apart() -> None:
    """6 cm is beyond ``NEAR_NODE_M``: the graph is left as it is."""
    assert NEAR_NODE_M == 0.05
    off = 0.06 / LAT_TO_M
    g = graph(
        [(0.4, 0.4), (0.4005, 0.4), (0.40025, 0.4 + off)],
        [(0, 1, WATER), (0, 2, WATER), (1, 2, WATER)],
    )
    joined, contacts = join_near_nodes(g, LAT)
    assert contacts == 0 and joined is g


def test_the_node_two_edges_share_is_not_a_contact() -> None:
    """Two edges leaving one node at a narrow angle, their far ends 6 cm apart: the shared
    node lies on both but ends both, so there is nothing to join."""
    g = graph(
        [(0.5, 0.5), (0.5005, 0.5), (0.5005, 0.5 + 0.06 / LAT_TO_M)],
        [(0, 1, WATER), (0, 2, WATER)],
    )
    joined, contacts = join_near_nodes(g, LAT)
    assert contacts == 0 and joined is g


def test_a_graph_without_contacts_is_returned_untouched() -> None:
    """The isolation of 2.14: no contact, the very same object, so the same bytes are written
    and every artefact built on them stays in the store."""
    g = graph(
        [(0.6, 0.6), (0.61, 0.6), (0.61, 0.61)], [(0, 1, WATER), (1, 2, WATER), (0, 2, WATER)]
    )
    joined, contacts = join_near_nodes(g, LAT)
    assert contacts == 0 and joined is g
    empty = NodedGraph(np.zeros((0, 3)), np.zeros((0, 2), dtype=np.int32), np.zeros(0, np.uint8))
    assert join_near_nodes(empty, LAT) == (empty, 0)
