# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Tests of the patch reader (``docs/specs/vectors-water-roads.md`` 4).

The patch files are written here, tag by tag, and the expected altitudes are computed by hand.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from shapely import geometry

from orthostudio.model import TileRef
from orthostudio.vectors.noding import MARKERS
from orthostudio.vectors.patches import (
    build_patch_layers,
    plane_profile,
    read_obj8,
    spline_profile,
    tanh_profile,
)

TILE = TileRef(43, 5)


class RampDem:
    """Altitude = 1000 * (latitude - 43), so every value is easy to predict."""

    def alt_vec(self, way: np.ndarray) -> np.ndarray:
        return 1000 * (np.asarray(way, dtype=np.float64)[:, 1])


def patch(tmp_path: Path, body: str, name: str = "test.patch.osm") -> Path:
    directory = tmp_path / "Patches" / TILE.name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n<osm version='0.6' generator='JOSM'>\n"
        + body
        + "</osm>"
    )
    return directory


def way(
    ids: list[int],
    coords: list[tuple[float, float]],
    tags: dict[str, str],
    way_id: int = -1,
    node_tags: dict[int, dict[str, str]] | None = None,
) -> str:
    out = []
    for node_id, (lon, lat) in zip(ids, coords, strict=True):
        extra = (node_tags or {}).get(node_id)
        if extra:
            inner = "".join(f"    <tag k='{k}' v='{v}'/>\n" for k, v in extra.items())
            out.append(f"  <node id='{node_id}' lat='{lat!r}' lon='{lon!r}'>\n{inner}  </node>\n")
        else:
            out.append(f"  <node id='{node_id}' lat='{lat!r}' lon='{lon!r}' />\n")
    refs = "".join(f"    <nd ref='{i}'/>\n" for i in ids)
    tagged = "".join(f"    <tag k='{k}' v='{v}'/>\n" for k, v in tags.items())
    out.append(f"  <way id='{way_id}'>\n{refs}{tagged}  </way>\n")
    return "".join(out)


SQUARE = [(5.2, 43.2), (5.3, 43.2), (5.3, 43.3), (5.2, 43.3), (5.2, 43.2)]
SQUARE_IDS = [-1, -2, -3, -4, -1]


def test_a_closed_way_is_an_interp_alt_polygon_with_a_seed(tmp_path: Path) -> None:
    directory = patch(tmp_path, way(SQUARE_IDS, SQUARE, {"cst_alt_abs": "42"}))
    result = build_patch_layers(directory, TILE, RampDem())
    geom, marker, z = result.layers[0]
    assert marker == MARKERS["INTERP_ALT"]
    assert geom.geom_type == "MultiPolygon"
    np.testing.assert_allclose(z, 42.0)
    assert result.seeds["INTERP_ALT"].shape == (1, 2)
    assert result.area.contains(geometry.Point(result.seeds["INTERP_ALT"][0]))
    assert result.names == ("test",)
    assert result.counts["polygons"] == 1


def test_an_open_way_is_a_dummy_line(tmp_path: Path) -> None:
    directory = patch(tmp_path, way([-1, -2], [(5.2, 43.2), (5.3, 43.25)], {}))
    result = build_patch_layers(directory, TILE, RampDem())
    geom, marker, z = result.layers[0]
    assert marker == MARKERS["DUMMY"]
    assert geom.geom_type == "MultiLineString"
    np.testing.assert_allclose(z, [200.0, 250.0])
    assert result.counts["lines"] == 1
    assert not result.seeds


def test_cst_alt_rel_is_the_mean_ground_plus_the_value(tmp_path: Path) -> None:
    directory = patch(tmp_path, way(SQUARE_IDS, SQUARE, {"cst_alt_rel": "10"}))
    result = build_patch_layers(directory, TILE, RampDem())
    mean = np.mean(RampDem().alt_vec(np.array(SQUARE) - [5, 43]))
    np.testing.assert_allclose(result.layers[0][2], mean + 10)


def test_var_alt_rel_follows_the_ground(tmp_path: Path) -> None:
    directory = patch(tmp_path, way(SQUARE_IDS, SQUARE, {"var_alt_rel": "-5"}))
    result = build_patch_layers(directory, TILE, RampDem())
    ground = RampDem().alt_vec(np.array(SQUARE) - [5, 43])
    np.testing.assert_allclose(result.layers[0][2], ground - 5)


def test_the_deprecated_altitude_tag_falls_back_on_the_mean(tmp_path: Path) -> None:
    good = patch(tmp_path, way(SQUARE_IDS, SQUARE, {"altitude": "17"}), "a.patch.osm")
    result = build_patch_layers(good, TILE, RampDem())
    np.testing.assert_allclose(result.layers[0][2], 17.0)
    (good / "a.patch.osm").unlink()
    bad = patch(tmp_path, way(SQUARE_IDS, SQUARE, {"altitude": "not a number"}), "b.patch.osm")
    result = build_patch_layers(bad, TILE, RampDem())
    mean = np.mean(RampDem().alt_vec(np.array(SQUARE) - [5, 43]))
    np.testing.assert_allclose(result.layers[0][2], mean)


def test_node_tags_override_single_vertices(tmp_path: Path) -> None:
    directory = patch(
        tmp_path,
        way(
            SQUARE_IDS,
            SQUARE,
            {"cst_alt_abs": "42"},
            node_tags={-2: {"alt_abs": "7"}, -3: {"alt_rel": "3"}},
        ),
    )
    result = build_patch_layers(directory, TILE, RampDem())
    z = result.layers[0][2]
    assert z[0] == 42.0
    assert z[1] == 7.0
    assert z[2] == pytest.approx(1000 * 0.3 + 3)
    assert z[4] == 42.0  # the closing point repeats node -1, which carries no tag


def test_a_ramp_builds_its_profile_and_its_cross_bars(tmp_path: Path) -> None:
    """``altitude_high`` / ``altitude_low`` (``O4_Vector_Map.py:711-813``)."""
    ramp = [(5.2, 43.2), (5.2, 43.2009), (5.2001, 43.2009), (5.2001, 43.2), (5.2, 43.2)]
    directory = patch(
        tmp_path,
        way(
            [-1, -2, -3, -4, -1],
            ramp,
            {
                "altitude_high": "100",
                "altitude_low": "50",
                "cell_size": "25",
                "profile": "plane",
            },
        ),
    )
    result = build_patch_layers(directory, TILE, RampDem())
    _polygons, marker, z = result.layers[0]
    assert marker == MARKERS["INTERP_ALT"]
    # 0.0009 degree of latitude is 100.008 m, so cuts_long = int(100.008/25) = 4, then 5.
    cuts_long = 5
    assert len(z) == 2 * cuts_long + 3
    expected = 100 - np.arange(cuts_long + 1) / cuts_long * 50
    np.testing.assert_allclose(z[: cuts_long + 1], expected)
    np.testing.assert_allclose(z[cuts_long + 1 : 2 * cuts_long + 2], expected[::-1])
    bars, bar_marker, _ = result.layers[1]
    assert bar_marker == MARKERS["DUMMY"]
    assert len(bars.geoms) == cuts_long - 1


@pytest.mark.parametrize(
    ("name", "profile"),
    [("spline", spline_profile), ("plane", plane_profile)],
)
def test_the_profiles_are_the_ortho4xp_formulas(name: str, profile) -> None:
    x = np.linspace(0, 1, 11)
    if name == "spline":
        np.testing.assert_allclose(profile(x), 3 * x**2 - 2 * x**3)
    else:
        np.testing.assert_allclose(profile(x), x)
    np.testing.assert_allclose(tanh_profile(2.0, np.array([0.5])), [0.5])
    assert tanh_profile(2.0, np.array([0.0]))[0] == pytest.approx(0.0, abs=1e-12)


def test_an_invalid_closed_way_is_skipped(tmp_path: Path) -> None:
    bowtie = [(5.2, 43.2), (5.3, 43.3), (5.3, 43.2), (5.2, 43.3), (5.2, 43.2)]
    directory = patch(tmp_path, way([-1, -2, -3, -4, -1], bowtie, {"cst_alt_abs": "1"}))
    events: list[str] = []
    result = build_patch_layers(
        directory, TILE, RampDem(), on_event=lambda e: events.append(e.code)
    )
    assert result.layers == ()
    assert result.counts["skipped"] == 1
    assert events == ["OSM_PATCH_INVALID"]


def test_a_missing_directory_gives_an_empty_result(tmp_path: Path) -> None:
    result = build_patch_layers(tmp_path / "nope", TILE, RampDem())
    assert result.layers == ()
    assert result.names == ()


def test_tagged_ways_come_before_untagged_ones(tmp_path: Path) -> None:
    """``:673-676``: the altitude of the tagged ways wins at a shared node."""
    body = way([-1, -2], [(5.2, 43.2), (5.25, 43.2)], {}, way_id=-1)
    body += way([-3, -4], [(5.3, 43.2), (5.35, 43.2)], {"var_alt_rel": "5"}, way_id=-2)
    directory = patch(tmp_path, body)
    result = build_patch_layers(directory, TILE, RampDem())
    first_geom, _, first_z = result.layers[0]
    assert first_geom.geoms[0].coords[0][0] == pytest.approx(0.3)  # the tagged way
    assert first_z[0] == pytest.approx(205.0)


# -- OBJ8 ----------------------------------------------------------------------------------


OBJ8 = """ANCHOR 5.5 43.5 120 0
A
800
OBJ

VT 0 0 0 0 1 0 0 0
VT 10 1 0 0 1 0 0 0
VT 0 2 10 0 1 0 0 0
IDX10 0 1 2 0 1 2 0 1 2 0
TRIS 0 3
"""


def test_read_obj8_places_the_triangles_around_the_anchor(tmp_path: Path) -> None:
    path = tmp_path / "object.obj"
    path.write_text(OBJ8)
    obj = read_obj8(path, TILE, RampDem())
    assert obj is not None
    assert len(obj.triangles) == 1
    coords = np.array(obj.triangles[0].coords)
    assert coords[0] == pytest.approx([0.5, 0.5])  # the anchor itself
    # heading 0: +x of the object goes east, +z goes south
    assert coords[1][0] > coords[0][0]
    assert coords[2][1] < coords[0][1]
    np.testing.assert_allclose(obj.altitudes[0], [120, 121, 122, 120])
    assert obj.seeds.shape == (1, 2)
    assert obj.area.contains(geometry.Point(obj.seeds[0]))


def test_an_anchor_without_altitude_takes_it_from_the_dem(tmp_path: Path) -> None:
    path = tmp_path / "object.obj"
    path.write_text(OBJ8.replace("ANCHOR 5.5 43.5 120 0", "ANCHOR 5.5 43.5 0"))
    obj = read_obj8(path, TILE, RampDem())
    assert obj is not None
    assert obj.altitudes[0][0] == pytest.approx(500.0)  # RampDem at y = 0.5


def test_a_file_without_anchor_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "object.obj"
    path.write_text("I am 800\nOBJ\n")
    assert read_obj8(path, TILE, RampDem()) is None


def test_objects_of_a_subdirectory_join_the_layers(tmp_path: Path) -> None:
    directory = patch(tmp_path, way(SQUARE_IDS, SQUARE, {"cst_alt_abs": "42"}))
    (directory / "myobjects").mkdir()
    (directory / "myobjects" / "o.obj").write_text(OBJ8)
    result = build_patch_layers(directory, TILE, RampDem())
    assert result.names == ("test", "myobjects")
    assert result.counts["objects"] == 1
    assert result.counts["triangles"] == 1
    assert result.seeds["INTERP_ALT"].shape == (2, 2)
    assert len(result.layers) == 2  # the polygon run, then the triangle run (lines)
    assert result.area.area > 0


# -- the one real patch of the reference machine --------------------------------------------


def test_a_file_whose_ways_are_all_unusable_says_so(tmp_path: Path) -> None:
    """A patch that changes nothing was silent: the tile came out as if none had been given.

    Found on a file written by hand whose ``<nd>`` elements were all on one line, which this
    reader (like Ortho4XP's) does not see: 2026-09-17.
    """
    events: list = []
    directory = patch(
        tmp_path,
        "  <node id='-1' lat='43.2' lon='5.2' />\n"
        "  <way id='-1'><nd ref='-1'/><tag k='altitude' v='500'/></way>\n",
    )
    result = build_patch_layers(directory, TILE, None, on_event=events.append)
    assert result.counts["files"] == 1 and not result.counts["polygons"]
    (event,) = events
    assert event.code == "OSM_PATCH_INVALID"
    assert event.context["reason"] == "no way of this file could be used"
    assert event.context["path"].endswith("test.patch.osm")
