"""Unit tests of ``flatten_helipads`` (``O4_Airport_Utils.py:1370-1464``).

Spec: ``docs/specs/airports-encoding.md`` section 9. The reference tile exercised the path too
(52 flattened helipads), but only its nominal branch: the exclusions and the hexagon are pinned
here.
"""

from __future__ import annotations

import numpy as np
import pytest
from shapely import geometry

from orthostudio.airports_vec.helipads import (
    HELIPAD_RADIUS_M,
    encode_helipads,
    helipad_areas,
)
from orthostudio.model import TileRef
from orthostudio.vectors.osmdata import OsmData
from orthostudio.vectors.water import LAT_TO_M

TILE = TileRef(43, 5)
EMPTY = geometry.Polygon()


class Ramp:
    def alt_vec(self, way):
        points = np.asarray(way, dtype=np.float64).reshape(-1, 2)
        return 100.0 + 1000.0 * points[:, 0] + 2000.0 * points[:, 1]


def _store(nodes=None, ways=None, node_tags=None, way_tags=None) -> OsmData:
    return OsmData(
        tile=TILE,
        nodes=dict(nodes or {}),
        ways=dict(ways or {}),
        tags={"n": dict(node_tags or {}), "w": dict(way_tags or {}), "r": {}},
    )


def _square(lon: float, lat: float, side: float) -> list[tuple[float, float]]:
    return [(lon, lat), (lon + side, lat), (lon + side, lat + side), (lon, lat + side), (lon, lat)]


def test_a_closed_way_tagged_helipad_is_flattened() -> None:
    ring = _square(5.3, 43.4, 0.0005)
    nodes = {-(i + 1): point for i, point in enumerate(ring[:-1])}
    store = _store(nodes, {-1: [-1, -2, -3, -4, -1]}, way_tags={-1: {"aeroway": "helipad"}})
    result = encode_helipads(store, TILE, Ramp(), EMPTY)
    assert result.found == 1
    assert len(result.ways) == 1
    way, z = result.ways[0]
    assert np.ptp(z) == 0.0  # flat, the mean of the raster over the outline
    assert np.isclose(z[0], Ramp().alt_vec(way).mean())
    assert len(result.seeds) == 1


def test_a_helipad_inside_the_treated_area_is_skipped() -> None:
    ring = _square(5.3, 43.4, 0.0005)
    nodes = {-(i + 1): point for i, point in enumerate(ring[:-1])}
    store = _store(nodes, {-1: [-1, -2, -3, -4, -1]}, way_tags={-1: {"aeroway": "helipad"}})
    treated = geometry.box(0.29, 0.39, 0.31, 0.41)
    result = encode_helipads(store, TILE, Ramp(), treated)
    assert result.found == 0
    assert result.ways == ()


def test_an_open_way_is_not_a_helipad() -> None:
    ring = _square(5.3, 43.4, 0.0005)
    nodes = {-(i + 1): point for i, point in enumerate(ring[:-1])}
    store = _store(nodes, {-1: [-1, -2, -3, -4]}, way_tags={-1: {"aeroway": "helipad"}})
    assert encode_helipads(store, TILE, Ramp(), EMPTY).found == 0


def test_a_node_helipad_becomes_a_hexagon_of_nine_metres() -> None:
    store = _store({-1: (5.25, 43.35)}, node_tags={-1: {"aeroway": "helipad"}})
    area, found = helipad_areas(store, TILE, EMPTY)
    assert found == 1
    (polygon,) = area.geoms
    ring = np.array(polygon.exterior.coords)
    assert len(ring) == 7
    centre = np.array([0.25, 0.35])
    north = ring[np.argmax(ring[:, 1])]
    # 7-decimal rounding (``:1434``) moves a vertex by up to 6 mm, hence the loose tolerance
    assert np.isclose(
        (north[1] - centre[1]) * LAT_TO_M, HELIPAD_RADIUS_M * np.sin(np.pi / 3), atol=0.02
    )


def test_a_node_inside_a_way_helipad_is_not_grown() -> None:
    ring = _square(5.3, 43.4, 0.0005)
    nodes = {-(i + 1): point for i, point in enumerate(ring[:-1])}
    nodes[-9] = (5.30025, 43.40025)  # inside the square above
    store = _store(
        nodes,
        {-1: [-1, -2, -3, -4, -1]},
        node_tags={-9: {"aeroway": "helipad"}},
        way_tags={-1: {"aeroway": "helipad"}},
    )
    assert helipad_areas(store, TILE, EMPTY)[1] == 1


def test_a_helipad_straddling_the_tile_side_is_clipped() -> None:
    store = _store({-1: (5.0, 43.5)}, node_tags={-1: {"aeroway": "helipad"}})
    area, found = helipad_areas(store, TILE, EMPTY)
    assert found == 1
    assert area.bounds[0] == pytest.approx(0.0, abs=1e-12)
    assert area.area > 0


def test_nothing_tagged_gives_nothing() -> None:
    result = encode_helipads(_store(), TILE, Ramp(), EMPTY)
    assert (result.found, result.ways, len(result.seeds)) == (0, (), 0)
    assert result.area.is_empty
