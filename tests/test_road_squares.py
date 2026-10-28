# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A large road network buffered in squares (``docs/specs/vectors-water-roads.md`` 3.8).

Past ``ROAD_SQUARES_FROM`` segments, and only once ``max_levelled_segs`` is raised past them too,
the network is cut into squares, each buffered on its own and kept within its square; the outline
is the one buffer's within half a metre. At the default limit nothing changes, keys included.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import shapely
from shapely import geometry

from orthostudio.graph import key_for
from orthostudio.graph.keys import artifact_key
from orthostudio.vectors import roads
from orthostudio.vectors.roads import (
    BUFFER_SEPARATION_M,
    BUFFER_SIMPLIFY_M,
    ROAD_SQUARES,
    ROAD_SQUARES_FROM,
    RoadParams,
    improved_buffer,
    improved_buffer_in_squares,
    road_bands,
)
from orthostudio.vectors.rule import VECTORS, VectorsParams
from orthostudio.vectors.water import LAT_TO_M, M_TO_LAT

SCALX = 0.73
SIDE = 0.02
"""Side of the town, in degrees: 2.2 km, some 9 000 points."""


def network(seed: int = 7) -> geometry.MultiLineString:
    """A town of :data:`SIDE`: winding streets every 60 to 120 m both ways, a point every
    10 m, and dead ends off them, close enough for their bands to meet and leave holes."""
    rng = np.random.default_rng(seed)
    lines = []
    for axis in (0, 1):
        place = 0.0
        while place < SIDE:
            place += rng.uniform(60, 120) * M_TO_LAT
            t = np.arange(0.0, SIDE, 10 * M_TO_LAT)
            wave = place + 15 * M_TO_LAT * np.sin(t * 900 + rng.uniform(0, 6))
            pts = np.column_stack((t, wave) if axis == 0 else (wave, t))
            lines.append(pts)
            for start in rng.choice(len(pts) - 1, size=3, replace=False):
                end = pts[start] + rng.uniform(-40, 40, size=2) * M_TO_LAT
                lines.append(np.vstack((pts[start], end)))
    scaled = [np.column_stack((pts[:, 0] / SCALX, pts[:, 1])) for pts in lines]
    return geometry.MultiLineString(scaled)


def metres(geom: Any) -> Any:
    """``geom`` in metres both ways."""
    return shapely.affinity.affine_transform(geom, [SCALX * LAT_TO_M, 0, 0, LAT_TO_M, 0, 0])


def buffer_args(net: geometry.MultiLineString) -> tuple[Any, float, float, float, float]:
    return (net, 4.0, BUFFER_SEPARATION_M, BUFFER_SIMPLIFY_M, SCALX)


def holes(geom: Any) -> int:
    return sum(len(part.interiors) for part in shapely.get_parts(geom))


@pytest.mark.parametrize("rounded", [True, False])
@pytest.mark.parametrize("seed", [7, 11])
def test_unsimplified_the_squares_draw_the_bands_of_the_one_buffer(
    rounded: bool, seed: int
) -> None:
    """Each square sees every line its bands depend on: glued, they are the one buffer's, and
    the squares' edges leave no seam."""
    net = network(seed)
    whole = improved_buffer(net, 4.0, BUFFER_SEPARATION_M, 0, SCALX, rounded=rounded)
    cut = improved_buffer_in_squares(
        net, 4.0, BUFFER_SEPARATION_M, 0, SCALX, rounded=rounded, squares=4, workers=1
    )
    assert metres(whole).symmetric_difference(metres(cut)).area < 1e-3  # m2
    assert len(shapely.get_parts(cut)) == len(shapely.get_parts(whole))
    assert holes(whole) > 20 and holes(cut) == holes(whole)


@pytest.mark.parametrize("rounded", [True, False])
@pytest.mark.parametrize("seed", [7, 11])
def test_simplified_they_keep_as_close_to_the_bands(rounded: bool, seed: int) -> None:
    """Each square simplifies its own outline: it differs from the one buffer's, but stays as
    close to the exact bands (measured: 0.82 m at most here, the one buffer 0.73 m)."""
    net = network(seed)
    exact = metres(improved_buffer(net, 4.0, BUFFER_SEPARATION_M, 0, SCALX, rounded=rounded))
    whole = metres(improved_buffer(*buffer_args(net), rounded=rounded))
    cut = metres(
        improved_buffer_in_squares(*buffer_args(net), rounded=rounded, squares=4, workers=1)
    )
    assert shapely.hausdorff_distance(cut.boundary, exact.boundary) < 2 * BUFFER_SIMPLIFY_M
    assert abs(cut.area - whole.area) <= abs(whole.area - exact.area)
    assert holes(cut) == holes(whole)


def test_the_outline_is_the_same_on_any_number_of_processes() -> None:
    net = network(3)
    one = improved_buffer_in_squares(*buffer_args(net), squares=4, workers=1)
    two = improved_buffer_in_squares(*buffer_args(net), squares=4, workers=2)
    assert shapely.to_wkb(one) == shapely.to_wkb(two)


def test_the_default_limit_never_cuts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default limit is the threshold, and a limit at the threshold keeps a network past it
    in one piece, point for point (the threshold brought down to the test's town)."""
    assert RoadParams().max_levelled_segs == ROAD_SQUARES_FROM
    net = network()
    monkeypatch.setattr(roads, "ROAD_SQUARES_FROM", 100)
    params = RoadParams(max_levelled_segs=100)
    assert shapely.to_wkb(road_bands(net, params, SCALX)) == shapely.to_wkb(
        improved_buffer(*buffer_args(net), rounded=params.rounded_ends)
    )


def test_a_raised_limit_cuts_only_a_network_past_the_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    net = network()
    segments = shapely.get_num_coordinates(net) - shapely.get_num_geometries(net)
    cut: list[int] = []

    def in_squares(*args: Any, **kwargs: Any) -> Any:
        cut.append(1)
        return improved_buffer(*args, rounded=kwargs["rounded"])

    monkeypatch.setattr(roads, "improved_buffer_in_squares", in_squares)
    raised = RoadParams(max_levelled_segs=2_000_000)
    monkeypatch.setattr(roads, "ROAD_SQUARES_FROM", segments)
    road_bands(net, raised, SCALX)
    assert not cut
    monkeypatch.setattr(roads, "ROAD_SQUARES_FROM", segments - 1)
    road_bands(net, raised, SCALX)
    assert cut


def test_the_key_changes_only_with_a_raised_limit() -> None:
    inputs = {"osm": "a" * 64, "dem": "b" * 64, "patches": None, "airports": None}
    inputs["global_scenery"] = None
    default = VectorsParams(tile="+33-085")
    assert "road_squares" not in default.canonical()
    at_limit = VectorsParams(tile="+33-085", max_levelled_segs=ROAD_SQUARES_FROM)
    assert at_limit.canonical() == default.canonical()
    raised = VectorsParams(tile="+33-085", max_levelled_segs=2_000_000)
    plain = raised.model_dump(mode="json")
    plain.pop("road_smoothing")
    doc = raised.canonical()
    assert doc.pop("road_squares") == ROAD_SQUARES
    assert doc == plain
    # a tile traced in one piece under a raised limit, before the squares, is traced again
    before = artifact_key(VECTORS.name, VECTORS.version, plain, inputs)
    assert key_for(VECTORS, raised, inputs) != before
