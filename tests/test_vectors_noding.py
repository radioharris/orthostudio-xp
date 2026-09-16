"""Synthetic cases for orthostudio.vectors.noding (rules from docs/specs/vectors-pslg.md)."""

from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import LineString, MultiLineString, Polygon

from orthostudio.vectors import MARKERS, NodedGraph, check_planar, node_layers

WATER, SEA, INTERP = MARKERS["WATER"], MARKERS["SEA"], MARKERS["INTERP_ALT"]


def line(*pts: tuple[float, float], z: float | list[float] | None = None):
    """A layer tuple helper: LineString plus per-vertex z."""
    zs = None if z is None else np.full(len(pts), z, dtype=float) if np.isscalar(z) else z
    return LineString(pts), zs


def node_at(g: NodedGraph, x: float, y: float) -> int:
    hit = np.flatnonzero((g.nodes[:, 0] == x) & (g.nodes[:, 1] == y))
    assert len(hit) == 1, f"node ({x}, {y}) found {len(hit)} times"
    return int(hit[0])


def edge_set(g: NodedGraph) -> dict[tuple[tuple[float, float], tuple[float, float]], int]:
    out = {}
    for (i, j), m in zip(g.edges.tolist(), g.markers.tolist(), strict=True):
        a, b = tuple(g.nodes[i, :2].tolist()), tuple(g.nodes[j, :2].tolist())
        out[(min(a, b), max(a, b))] = m
    return out


def test_two_crossing_segments_z_from_priority_layer() -> None:
    l1, z1 = line((0, 0), (1, 1), z=[0.0, 10.0])
    l2, z2 = line((0, 1), (1, 0), z=100.0)
    g = node_layers([(l1, WATER, z1), (l2, SEA, z2)])
    assert len(g.nodes) == 5 and len(g.edges) == 4
    c = node_at(g, 0.5, 0.5)
    assert g.nodes[c, 2] == pytest.approx(5.0)  # interpolated on the WATER edge, not 100
    assert sorted(g.markers.tolist()) == [1, 1, 2, 2]
    assert np.bincount(g.edges.ravel())[c] == 4
    assert check_planar(g.nodes, g.edges) == 0


def test_crossing_z_interpolated_at_parameter() -> None:
    l1, z1 = line((0, 0), (1, 0), z=[0.0, 100.0])
    l2, z2 = line((0.25, -1), (0.25, 1), z=5.0)
    g = node_layers([(l1, WATER, z1), (l2, SEA, z2)])
    assert g.nodes[node_at(g, 0.25, 0.0), 2] == pytest.approx(25.0)


def test_t_junction_new_vertex_on_old_edge_keeps_vertex_z() -> None:
    l1, z1 = line((0, 0), (1, 0), z=[0.0, 10.0])
    l2, z2 = line((0.5, 0), (0.5, 1), z=7.0)
    g = node_layers([(l1, WATER, z1), (l2, SEA, z2)])
    assert len(g.nodes) == 4 and len(g.edges) == 3
    # the vertex is inserted before its edge is checked (insert_way), so its z wins
    assert g.nodes[node_at(g, 0.5, 0.0), 2] == 7.0
    assert edge_set(g) == {
        ((0.0, 0.0), (0.5, 0.0)): WATER,
        ((0.5, 0.0), (1.0, 0.0)): WATER,
        ((0.5, 0.0), (0.5, 1.0)): SEA,
    }


def test_t_junction_old_vertex_on_new_edge_keeps_old_z() -> None:
    l1, z1 = line((0.5, 0), (0.5, 1), z=7.0)
    l2, z2 = line((0, 0), (1, 0), z=[0.0, 10.0])
    g = node_layers([(l1, WATER, z1), (l2, SEA, z2)])
    assert len(g.nodes) == 4 and len(g.edges) == 3
    assert g.nodes[node_at(g, 0.5, 0.0), 2] == 7.0


def test_exact_duplicate_edges_or_markers() -> None:
    l1, z1 = line((0, 0), (1, 1), z=1.0)
    l2, z2 = line((1, 1), (0, 0), z=2.0)  # reversed copy
    g = node_layers([(l1, WATER, z1), (l2, SEA, z2), (l1, INTERP, z1)])
    assert len(g.nodes) == 2 and len(g.edges) == 1
    assert g.markers[0] == WATER | SEA | INTERP
    assert g.nodes[:, 2].tolist() == [1.0, 1.0]  # first insertion keeps its z


def test_collinear_overlap_inside() -> None:
    l1, z1 = line((0, 0), (1, 0), z=0.0)
    l2, z2 = line((0.25, 0), (0.75, 0), z=0.0)
    g = node_layers([(l1, WATER, z1), (l2, SEA, z2)])
    assert edge_set(g) == {
        ((0.0, 0.0), (0.25, 0.0)): WATER,
        ((0.25, 0.0), (0.75, 0.0)): WATER | SEA,
        ((0.75, 0.0), (1.0, 0.0)): WATER,
    }


def test_collinear_overlap_partial_and_reversed() -> None:
    l1, z1 = line((0, 0), (1, 0), z=0.0)
    l2, z2 = line((1.5, 0), (0.5, 0), z=0.0)
    g = node_layers([(l1, WATER, z1), (l2, SEA, z2)])
    assert edge_set(g) == {
        ((0.0, 0.0), (0.5, 0.0)): WATER,
        ((0.5, 0.0), (1.0, 0.0)): WATER | SEA,
        ((1.0, 0.0), (1.5, 0.0)): SEA,
    }
    assert check_planar(g.nodes, g.edges) == 0


def test_vertex_falling_on_edge_splits_it_and_keeps_vertex_z() -> None:
    l1, z1 = line((0, 0), (1, 1), z=[0.0, 100.0])
    l2, z2 = line((0.2, 0.8), (0.5, 0.5), (0.9, 0.1), z=[1.0, 2.0, 3.0])
    g = node_layers([(l1, WATER, z1), (l2, SEA, z2)])
    assert len(g.nodes) == 5 and len(g.edges) == 4
    assert g.nodes[node_at(g, 0.5, 0.5), 2] == 2.0
    assert check_planar(g.nodes, g.edges) == 0


def test_crossing_node_created_before_later_vertex_keeps_crossing_z() -> None:
    l1, z1 = line((0, 0), (1, 0), z=[0.0, 100.0])
    l2, z2 = line((0.5, -1), (0.5, 1), z=5.0)
    l3, z3 = line((0.5, 0), (2, 2), z=999.0)  # starts exactly at the crossing
    g = node_layers([(l1, WATER, z1), (l2, SEA, z2), (l3, INTERP, z3)])
    assert g.nodes[node_at(g, 0.5, 0.0), 2] == pytest.approx(50.0)
    assert np.bincount(g.edges.ravel())[node_at(g, 0.5, 0.0)] == 5


def test_crossing_interpolates_on_the_split_chain_not_the_original_segment() -> None:
    l1, z1 = line((0, 0), (1, 0), z=[0.0, 100.0])
    l2, z2 = line((0.5, 0), (0.5, 1), z=1000.0)  # T-junction vertex with its own z
    l3, z3 = line((0.75, -1), (0.75, 1), z=0.0)  # later crossing of the (0.5, 1) piece
    g = node_layers([(l1, WATER, z1), (l2, SEA, z2), (l3, SEA, z3)])
    assert g.nodes[node_at(g, 0.75, 0.0), 2] == pytest.approx(550.0)  # Ortho4XP semantics, not 75


def test_three_segments_through_one_point() -> None:
    l1, z1 = line((0, 0), (1, 1), z=0.0)
    l2, z2 = line((0, 1), (1, 0), z=1.0)
    l3, z3 = line((0.5, 0), (0.5, 1), z=2.0)
    g = node_layers([(l1, WATER, z1), (l2, SEA, z2), (l3, INTERP, z3)])
    c = node_at(g, 0.5, 0.5)
    assert len(g.nodes) == 7 and len(g.edges) == 6
    assert np.bincount(g.edges.ravel())[c] == 6
    assert g.nodes[c, 2] == 0.0
    assert check_planar(g.nodes, g.edges) == 0


def test_polygon_with_hole_and_z_from_3d_coordinates() -> None:
    outer = [(0, 0, 1), (1, 0, 2), (1, 1, 3), (0, 1, 4), (0, 0, 1)]
    inner = [(0.4, 0.4, 9), (0.6, 0.4, 9), (0.6, 0.6, 9), (0.4, 0.6, 9), (0.4, 0.4, 9)]
    g = node_layers([(Polygon(outer, [inner]), WATER, None)])
    assert len(g.nodes) == 8 and len(g.edges) == 8
    assert g.nodes[node_at(g, 1.0, 1.0), 2] == 3.0
    assert g.nodes[node_at(g, 0.4, 0.4), 2] == 9.0
    assert set(g.markers.tolist()) == {WATER}


def test_zero_length_segments_and_empty_inputs() -> None:
    l1, z1 = line((0, 0), (0, 0), (1, 0), (1, 0), z=0.0)
    g = node_layers([(l1, WATER, z1), (MultiLineString(), SEA, None)])
    assert len(g.nodes) == 2 and len(g.edges) == 1
    empty = node_layers([])
    assert empty.nodes.shape == (0, 3) and empty.edges.shape == (0, 2)


def test_nodes_are_rounded_to_9_decimals_and_unique() -> None:
    l1, z1 = line((0.1234567891234, 0), (1, 0), z=0.0)
    l2, z2 = line((0.1234567894, 1e-10), (1, 1), z=5.0)  # same node after rounding
    g = node_layers([(l1, WATER, z1), (l2, SEA, z2)])
    assert len(g.nodes) == 3
    assert g.nodes[node_at(g, 0.123456789, 0.0), 2] == 0.0
    keys = {tuple(r) for r in g.nodes[:, :2].tolist()}
    assert len(keys) == len(g.nodes)


def test_z_none_defaults_to_zero_and_bad_lengths_raise() -> None:
    g = node_layers([(LineString([(0, 0), (1, 0)]), WATER, None)])
    assert g.nodes[:, 2].tolist() == [0.0, 0.0]
    with pytest.raises(ValueError, match="coordinates"):
        node_layers([(LineString([(0, 0), (1, 0)]), WATER, np.zeros(3))])
    with pytest.raises(ValueError, match="uint8"):
        node_layers([(LineString([(0, 0), (1, 0)]), 256, None)])


def _random_layers(seed: int, n_walks: int = 40, n_pts: int = 50) -> list:
    rng = np.random.default_rng(seed)
    walks = [
        np.clip(np.cumsum(rng.normal(size=(n_pts, 2)) * 0.02, axis=0) + 0.5, 0, 1)
        for _ in range(n_walks)
    ]
    layers = [
        (MultiLineString([LineString(w) for w in walks[::2]]), WATER, None),
        (MultiLineString([LineString(w) for w in walks[1::2]]), SEA, None),
        (
            MultiLineString([LineString([(x, -0.1), (x, 1.1)]) for x in np.linspace(0, 1, 9)]),
            0,
            None,
        ),
        (
            MultiLineString([LineString([(-0.1, y), (1.1, y)]) for y in np.linspace(0, 1, 9)]),
            0,
            None,
        ),
    ]
    return layers


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_random_walks_give_a_planar_graph_and_noding_is_idempotent(seed: int) -> None:
    g = node_layers(_random_layers(seed))
    assert check_planar(g.nodes, g.edges) == 0
    assert len(g.edges) > 2000
    # every edge has two distinct nodes, each unordered pair once
    assert np.all(g.edges[:, 0] != g.edges[:, 1])
    pairs = np.sort(g.edges, axis=1)
    assert len(np.unique(pairs, axis=0)) == len(pairs)
    # feeding the result back (one layer per marker) changes nothing
    again = node_layers(
        [
            (
                MultiLineString([LineString(g.nodes[e, :2]) for e in g.edges[g.markers == m]]),
                int(m),
                g.nodes[g.edges[g.markers == m]][:, :, 2].reshape(-1),
            )
            for m in np.unique(g.markers)
        ]
    )
    assert edge_set(again) == edge_set(g)
    assert {tuple(r) for r in again.nodes.tolist()} == {tuple(r) for r in g.nodes.tolist()}
