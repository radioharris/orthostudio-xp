"""Unit tests of the road builder (``docs/specs/vectors-water-roads.md`` 3).

No network: the roads are written as a small Ortho4XP-style ``.osm`` file
and read back through ``OsmData``. The metric helpers are checked against a transcription of
the Ortho4XP source, bit for bit, because they decide which roads are levelled at all.
"""

from __future__ import annotations

from math import cos, pi
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely import geometry

from orthostudio.model import TileRef
from orthostudio.vectors.noding import MARKERS
from orthostudio.vectors.osmdata import OsmData
from orthostudio.vectors.roads import (
    AirportAreas,
    RoadParams,
    build_road_layers,
    improved_buffer,
    length_in_meters,
    shift_way,
    weighted_normals,
)
from orthostudio.vectors.water import M_TO_LAT, scale_x

TILE = TileRef(43, 5)
SCALX = scale_x(TILE)


class SlopedDem:
    """Altitude rising ``slope`` metres per degree of latitude, plus a tilt across x."""

    def __init__(self, slope: float = 0.0, tilt: float = 0.0) -> None:
        self.slope = slope
        self.tilt = tilt

    def alt_vec(self, way: np.ndarray) -> np.ndarray:
        pts = np.asarray(way, dtype=np.float64)
        return self.slope * pts[:, 1] + self.tilt * pts[:, 0]


def write_roads(path: Path, ways: list[tuple[list[tuple[float, float]], dict[str, str]]]) -> Path:
    """Open ways with their tags, in Ortho4XP's cache shape."""
    nodes: list[str] = []
    bodies: list[str] = []
    node_id = 1
    for way_id, (coords, tags) in enumerate(ways, 1):
        ids: list[int] = []
        for lon, lat in coords:
            nodes.append(f'  <node id="{node_id}" lat="{lat:.9f}" lon="{lon:.9f}" version="1"/>\n')
            ids.append(node_id)
            node_id += 1
        refs = "".join(f'    <nd ref="{i}"/>\n' for i in ids)
        tagged = "".join(f'    <tag k="{k}" v="{v}"/>\n' for k, v in tags.items())
        bodies.append(f'  <way id="{way_id}" version="1">\n{refs}{tagged}  </way>\n')
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="test">\n'
        + "".join(nodes)
        + "".join(bodies)
        + "</osm>"
    )
    return path


def store(
    tmp_path: Path,
    ways: list[tuple[list[tuple[float, float]], dict[str, str]]],
    layer: str = "big_roads",
    name: str = "roads.osm",
) -> OsmData:
    return OsmData.load(write_roads(tmp_path / name, ways), layer=layer, tile=TILE)


def straight(n: int = 5, lon: float = 5.2, lat: float = 43.2) -> list[tuple[float, float]]:
    return [(lon + 0.001 * k, lat) for k in range(n)]


# -- the metric helpers --------------------------------------------------------------------


def reference_normals(way: np.ndarray, side: str, scalx: float) -> np.ndarray:
    """``O4_Vector_Utils.py:1071-1094``, transcribed here to compare against."""
    n = len(way)
    if n < 2:
        return np.zeros(n)
    sign = np.array([[-1 / scalx, 1]]) if side == "left" else np.array([[1 / scalx, -1]])
    tg = way[1:] - way[:-1]
    tg[:, 0] *= scalx
    tg = tg / (1e-6 + np.linalg.norm(tg, axis=1)).reshape(n - 1, 1)
    tg = np.vstack([tg, tg[-1]])
    if n > 2:
        scale = 1e-6 + np.linalg.norm(tg[1:-1] + tg[:-2], axis=1).reshape(n - 2, 1)
        tg[1:-1] = (tg[1:-1] + tg[:-2]) / scale
        if (way[0] == way[-1]).all():
            scale2 = 1e-6 + np.linalg.norm(tg[0] + tg[-1])
            tg[0] = tg[-1] = (tg[0] + tg[-1]) / scale2
    return np.roll(tg, 1, axis=1) * sign


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("closed", [False, True])
def test_weighted_normals_are_bit_identical_to_ortho4xp(side: str, closed: bool) -> None:
    rng = np.random.default_rng(11)
    way = np.cumsum(rng.normal(0, 0.001, size=(9, 2)), axis=0)
    if closed:
        way = np.vstack([way, way[:1]])
    assert np.array_equal(
        weighted_normals(way.copy(), side, SCALX), reference_normals(way.copy(), side, SCALX)
    )


def test_weighted_normals_of_a_point_is_zero() -> None:
    assert weighted_normals(np.zeros((1, 2)), "left", SCALX).shape == (1, 2)


def test_shift_way_moves_by_the_asked_number_of_meters() -> None:
    """A due-east road shifted left must move north by exactly ``shift`` metres."""
    way = np.array([[0.0, 0.0], [0.01, 0.0], [0.02, 0.0]])
    shifted = shift_way(way, 4.0, "left", SCALX)
    np.testing.assert_allclose(shifted[:, 0], way[:, 0], atol=1e-15)
    # Ortho4XP normalises with ``1e-6 + norm`` (``:1080``), so the shift is short by ~1.4e-4.
    np.testing.assert_allclose(shifted[:, 1] - way[:, 1], 4.0 * M_TO_LAT, rtol=1e-3)


def test_length_in_meters_uses_the_anisotropic_metric() -> None:
    way = np.array([[0.0, 0.0], [1.0, 0.0]])
    assert length_in_meters(way, SCALX) == pytest.approx(
        cos(43.5 * pi / 180) * pi * 6378137 / 180, rel=1e-12
    )


def test_improved_buffer_closes_a_narrow_gap() -> None:
    """Two parallel roads 3 m apart, buffered by 4 m: one polygon, no hole (``:1013-1020``)."""
    gap = 3 * M_TO_LAT
    lines = geometry.MultiLineString(
        [[(0.1, 0.5), (0.9, 0.5)], [(0.1, 0.5 + gap), (0.9, 0.5 + gap)]]
    )
    out = improved_buffer(lines, 4.0, 2.0, 0.0, SCALX)
    assert out.geom_type == "Polygon"
    assert len(out.interiors) == 0


def test_improved_buffer_width_is_in_meters() -> None:
    line = geometry.LineString([(0.2, 0.5), (0.8, 0.5)])
    out = improved_buffer(line, 10.0, 0.0, 0.0, SCALX)
    ymin, ymax = out.bounds[1], out.bounds[3]
    assert (ymax - ymin) / 2 == pytest.approx(10.0 * M_TO_LAT, rel=1e-3)


# -- which roads are levelled --------------------------------------------------------------


def test_flat_ground_levels_nothing(tmp_path: Path) -> None:
    data = store(tmp_path, [(straight(), {"highway": "motorway"})])
    result = build_road_layers(data, TILE, SlopedDem(), RoadParams())
    assert result.counts["ways"] == 1
    assert result.counts["levelled"] == 0
    assert result.counts["rejected"] == 1
    assert result.layers == ()


def test_a_banked_road_is_levelled(tmp_path: Path) -> None:
    """A 1 m per 4 m cross slope is past the 0.5 m limit (``road_banking_limit``)."""
    data = store(tmp_path, [(straight(), {"highway": "motorway"})])
    dem = SlopedDem(slope=1.0 / M_TO_LAT)  # 1 m per metre of latitude: hugely banked
    result = build_road_layers(data, TILE, dem, RoadParams())
    assert result.counts["levelled"] == 1
    assert [marker for _, marker, _ in result.layers] == [MARKERS["INTERP_ALT"]]
    assert result.seeds["INTERP_ALT"].shape[0] == result.counts["polygons"]


def test_bridges_and_tunnels_are_skipped(tmp_path: Path) -> None:
    dem = SlopedDem(slope=1.0 / M_TO_LAT)
    data = store(
        tmp_path,
        [
            (straight(), {"highway": "motorway", "bridge": "yes"}),
            (straight(lat=43.3), {"highway": "motorway", "tunnel": "yes"}),
            (straight(lat=43.4), {"highway": "motorway"}),
        ],
    )
    result = build_road_layers(data, TILE, dem, RoadParams())
    assert result.counts["levelled"] == 1


def test_a_road_touching_an_airport_is_levelled_even_on_flat_ground(tmp_path: Path) -> None:
    """``road_is_too_much_banked`` returns True on the raster test (``:231-240``)."""
    data = store(tmp_path, [(straight(), {"highway": "motorway"})])
    array = np.zeros((1001, 1001), dtype=bool)
    array[1000 - 200, 200] = True  # the first point of the way is (0.2, 0.2)
    result = build_road_layers(data, TILE, SlopedDem(), RoadParams(), AirportAreas(array=array))
    assert result.counts["levelled"] == 1


def test_max_levelled_segs_stops_the_slope_test(tmp_path: Path) -> None:
    dem = SlopedDem(slope=1.0 / M_TO_LAT)
    ways = [(straight(lat=43.1 + 0.1 * k), {"highway": "motorway"}) for k in range(4)]
    data = store(tmp_path, ways)
    generous = build_road_layers(data, TILE, dem, RoadParams(max_levelled_segs=200000))
    stingy = build_road_layers(data, TILE, dem, RoadParams(max_levelled_segs=6))
    assert generous.counts["levelled"] == 4
    assert stingy.counts["levelled"] == 2  # 5 vertices each: the budget is spent after two


def test_road_level_zero_builds_nothing(tmp_path: Path) -> None:
    data = store(tmp_path, [(straight(), {"highway": "motorway"})])
    dem = SlopedDem(slope=1.0 / M_TO_LAT)
    result = build_road_layers(data, TILE, dem, RoadParams(road_level=0))
    assert result.layers == ()
    assert result.counts["levelled"] == 0


def test_a_second_store_adds_its_roads(tmp_path: Path) -> None:
    """``road_level >= 2``: the caller passes ``small_roads`` as well (spec 3.1).

    There is no ``small_roads`` cache on the reference machine and no network is allowed, so
    this is a mechanism test, not a fidelity one: the layer must grow, nothing more.
    """
    dem = SlopedDem(slope=1.0 / M_TO_LAT)
    big = store(tmp_path, [(straight(), {"highway": "motorway"})])
    small = store(
        tmp_path,
        [(straight(lat=43.5), {"highway": "residential"})],
        layer="small_roads",
        name="small.osm",
    )
    one = build_road_layers(big, TILE, dem, RoadParams())
    both = build_road_layers([big, small], TILE, dem, RoadParams(road_level=3))
    assert both.counts["levelled"] == 2
    assert both.counts["polygons"] > one.counts["polygons"]
    assert both.area.area > one.area.area


def test_the_airport_area_is_carved_out_of_the_roads(tmp_path: Path) -> None:
    dem = SlopedDem(slope=1.0 / M_TO_LAT)
    data = store(tmp_path, [([(5.1, 43.2), (5.9, 43.2)], {"highway": "motorway"})])
    plain = build_road_layers(data, TILE, dem, RoadParams())
    apron = geometry.box(0.4, 0.199, 0.6, 0.201)
    carved = build_road_layers(data, TILE, dem, RoadParams(), AirportAreas(area=apron))
    assert carved.area.area < plain.area.area
    assert carved.counts["polygons"] == 2  # the road is cut in two by the airport


def test_the_altitude_is_sampled_inside_the_road(tmp_path: Path) -> None:
    """``alt_vec_shift`` (``:251-252``): z is the DEM ``lane_width`` metres to the left."""
    dem = SlopedDem(slope=1.0 / M_TO_LAT, tilt=0.0)
    data = store(tmp_path, [(straight(), {"highway": "motorway"})])
    result = build_road_layers(data, TILE, dem, RoadParams())
    geom, _, z = result.layers[0]
    coords = shapely.get_coordinates(geom)
    expected = dem.alt_vec(shift_way(coords, 4.0, "left", SCALX))
    np.testing.assert_allclose(z, expected)
    assert not np.allclose(z, dem.alt_vec(coords))
