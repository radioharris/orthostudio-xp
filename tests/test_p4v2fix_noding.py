"""Review-6 fixes: the incremental noding pass is the full one, to the bit.

Spec: ``docs/specs/vectors-pslg.md`` 2.13. ``noding._insert`` re-processes only the new segments and
the existing edges the layer's box meets; ``noding._insert_full`` re-derives the whole graph and is
the reference. Every comparison here is on the bytes of the three arrays of
:class:`orthostudio.vectors.noding.NodedGraph`, order included: the numbering is what
Triangle4XP sees.
"""

from __future__ import annotations

import threading

import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, MultiLineString

from orthostudio.errors import OsxpError
from orthostudio.vectors import noding
from orthostudio.vectors.noding import Layer, NodedGraph, node_layers


def _full(layers: list[Layer]) -> NodedGraph:
    """``node_layers`` with every pass re-derived from scratch (the pre-review-6 noder)."""
    graph = None
    for geometry, marker, z in layers:
        graph = noding._insert_full(graph, noding._layer_segments(geometry, int(marker), z))
    assert graph is not None
    nodes = graph.nodes.copy()
    nodes[:, :2] = np.round(nodes[:, :2], noding.SNAP_DIGITS)
    return NodedGraph(nodes, graph.edges, graph.markers)


def _same(a: NodedGraph, b: NodedGraph) -> bool:
    return (
        a.nodes.tobytes() == b.nodes.tobytes()
        and a.edges.tobytes() == b.edges.tobytes()
        and a.markers.tobytes() == b.markers.tobytes()
        and a.nodes.shape == b.nodes.shape
        and a.edges.shape == b.edges.shape
    )


def _random_layers(rng: np.random.Generator) -> list[Layer]:
    """Shared vertices, grid-snapped coordinates, 1e-10 jitter, sub-key segments, overlaps."""
    pool = rng.random((30, 2))
    snap = rng.choice([0.0, 1e-3, 1e-9])
    layers: list[Layer] = []
    for _ in range(int(rng.integers(1, 12))):
        lines = []
        for _ in range(int(rng.integers(1, 6))):
            n = int(rng.integers(2, 6))
            points = pool[rng.integers(0, 30, n)] if rng.random() < 0.5 else rng.random((n, 2))
            if snap:
                points = np.round(points / snap) * snap
            if rng.random() < 0.2:
                points = points + rng.normal(0.0, 3e-10, points.shape)
            if rng.random() < 0.1 and n > 2:
                points[1] = points[0] + 2e-10  # shorter than the 9-decimal key: an isolated node
            lines.append(LineString(np.clip(points, 0.0, 1.0)))
        geometry = MultiLineString(lines)
        z = rng.random(len(shapely.get_coordinates(geometry)))
        layers.append((geometry, int(rng.choice([0, 1, 2, 16, 32, 64, 128])), z))
    return layers


def test_the_incremental_pass_is_the_full_pass_on_random_layers() -> None:
    isolated = 0
    original = noding._drop_isolated

    def count(graph: noding._Graph) -> noding._Graph:
        nonlocal isolated
        isolated += int(len(graph.isolated) > 0)
        return original(graph)

    noding._drop_isolated = count  # type: ignore[assignment]
    try:
        for seed in range(800):
            layers = _random_layers(np.random.default_rng(seed))
            assert _same(node_layers(layers), _full(layers)), seed
    finally:
        noding._drop_isolated = original  # type: ignore[assignment]
    assert isolated > 0, "the sub-key segments never produced an isolated node"


def test_the_fallback_gives_the_same_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the one configuration the local pass does not handle: the result is unchanged."""
    monkeypatch.setattr(noding, "_meets_an_edge_outside", lambda *args: True)
    for seed in range(100):
        layers = _random_layers(np.random.default_rng(10_000 + seed))
        assert _same(node_layers(layers), _full(layers)), seed


def test_a_pass_touches_only_the_edges_its_box_meets(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [LineString([(0.0, y), (1.0, y)]) for y in np.linspace(0.05, 0.95, 50)]
    far = (MultiLineString([[(0.98, 0.0), (0.99, 1.0)]]), 16, None)
    near = (MultiLineString([[(0.5, 0.5), (0.52, 0.51)]]), 32, None)
    seen: list[int] = []
    original = noding._edge_prefilter

    def spy(nodes, edges, a_new, b_new):  # type: ignore[no-untyped-def]
        cand = original(nodes, edges, a_new, b_new)
        seen.append(len(cand))
        return cand

    monkeypatch.setattr(noding, "_edge_prefilter", spy)
    layers: list[Layer] = [(MultiLineString(rows), 1, None), far, near]
    assert _same(node_layers(layers), _full(layers))
    assert seen[0] == 50  # the vertical line crosses every row
    assert seen[1] <= 1  # the short segment meets at most one row's pieces


def test_the_token_is_read_before_every_pass() -> None:
    cancel = threading.Event()
    layers: list[Layer] = [
        (MultiLineString([[(0.1, 0.1), (0.9, 0.9)]]), 1, None),
        (MultiLineString([[(0.1, 0.9), (0.9, 0.1)]]), 2, None),
    ]
    assert len(node_layers(layers, cancel=cancel).nodes) == 5
    cancel.set()
    with pytest.raises(OsxpError) as err:
        node_layers(layers, cancel=cancel)
    assert err.value.code == "SYS_CANCELLED"
