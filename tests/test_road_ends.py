# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The ends of the road bands, angular or round (``docs/specs/vectors-water-roads.md`` 3.7).

Angular by default, as Ortho4XP 1.40 and OrthoStudio XP 1.1.0 draw them, with the key of the
tiles 1.1.0 traced; round on request, as Ortho4XP 1.30 and OrthoStudio XP 1.2.0 draw them, with
the key of the tiles 1.2.0 traced: at the service roads of a dense city round ends doubled the
tracing's time and memory (Atlanta, 2026-10-27).
"""

from __future__ import annotations

from pathlib import Path

import shapely
from shapely import geometry

from orthostudio.config import Settings
from orthostudio.config.models import Expert
from orthostudio.config.overrides import to_build_overrides
from orthostudio.graph import key_for
from orthostudio.model import TileRef
from orthostudio.tilefiles.config import OSXP_PARAMETERS
from orthostudio.vectors.layers import _airport_params
from orthostudio.vectors.roads import RoadParams, build_road_layers, improved_buffer
from orthostudio.vectors.rule import VECTORS, VECTORS_ANGULAR, VectorsParams, vectors_rule
from orthostudio.vectors.water import M_TO_LAT, scale_x
from test_vectors_roads import SlopedDem, store, straight

TILE = TileRef(43, 5)
SCALX = scale_x(TILE)
KEY_1_1_0 = "bbf35de24fdadfe766226b8a9e7e237e84da30e65b7a39bd9ea97ab472f2ceef"
"""The key OrthoStudio XP 1.1.0's own code gives the tracing of ``test_a_tile_1_1_0_traced...``."""


def points(geom: shapely.Geometry) -> int:
    return int(shapely.get_num_coordinates(geom))


def test_an_angular_band_has_square_ends() -> None:
    """An L-shaped way: 9 points angular, 39 round (Ortho4XP 1.30's, KSME, 2026-10-24)."""
    way = geometry.LineString([(5.2, 43.2), (5.201, 43.2), (5.201, 43.201)])
    angular = improved_buffer(way, 4.0, 2.0, 0.5, SCALX, rounded=False)
    round_ = improved_buffer(way, 4.0, 2.0, 0.5, SCALX)
    assert points(angular) < points(round_)
    assert abs(angular.area - round_.area) < 0.05 * round_.area


def bands(tmp_path: Path, params: RoadParams, name: str):  # type: ignore[no-untyped-def]
    dem = SlopedDem(slope=1.0 / M_TO_LAT, tilt=3.0 / M_TO_LAT)
    data = store(tmp_path, [(straight(n=8), {"highway": "motorway"})], name=name)
    return build_road_layers(data, TILE, dem, params)


def test_the_road_bands_follow_the_road_ends(tmp_path: Path) -> None:
    angular = bands(tmp_path, RoadParams(rounded_ends=False), "a.osm")
    round_ = bands(tmp_path, RoadParams(rounded_ends=True), "b.osm")
    (g1, _, _), (g2, _, _) = angular.layers[0], round_.layers[0]
    assert points(g1) < points(g2)
    assert angular.counts["levelled"] == round_.counts["levelled"] == 1


def test_the_setting_reaches_the_build() -> None:
    assert to_build_overrides(Settings())["rounded_road_ends"] is False
    on = Settings(expert=Expert(rounded_road_ends=True))
    assert to_build_overrides(on)["rounded_road_ends"] is True
    assert OSXP_PARAMETERS["rounded_road_ends"].default is False


def test_the_shape_is_the_rule_s_version_never_a_field_of_the_key() -> None:
    """Angular ends are the rule at version 12, what 1.1.0 ran; round ends at 13, what 1.2.0
    ran. The parameters of the key are those of both, so each finds the tiles it traced."""
    assert vectors_rule(True) is VECTORS and VECTORS.version == 13
    angular = vectors_rule(False)
    assert angular is VECTORS_ANGULAR and angular.version == 12
    assert angular.name == VECTORS.name and angular.fn is VECTORS.fn
    assert "rounded_road_ends" not in VectorsParams.model_fields
    inputs = {"osm": "a" * 64, "dem": "b" * 64, "patches": None, "airports": None}
    inputs["global_scenery"] = None
    params = VectorsParams(tile=TILE.name)
    assert key_for(angular, params, inputs) != key_for(VECTORS, params, inputs)


def test_a_tile_1_1_0_traced_keeps_its_key() -> None:
    """The key 1.1.0 gave a tile's tracing at its defaults, computed by 1.1.0's own code
    (``orthostudio.vectors@12``, the parameters it had, the same inputs)."""
    inputs = {"osm": "a" * 64, "dem": "b" * 64, "patches": None, "airports": None}
    inputs["global_scenery"] = None
    params = VectorsParams(tile="+33-085", road_level=4, apt_smoothing_pix=32)
    key, _recipe = key_for(VECTORS_ANGULAR, params, inputs)
    assert key == KEY_1_1_0


def test_the_taxiway_bands_follow_the_road_ends() -> None:
    assert _airport_params(8, rounded_ends=False).areas.rounded_ends is False
    assert _airport_params(8, rounded_ends=True).areas.rounded_ends is True
