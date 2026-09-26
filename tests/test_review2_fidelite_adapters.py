# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Adversarial fidelity review (P2a): tile naming on every hemisphere, the parameters the
imagery stage of Ortho4XP consumes versus the textures/DSF keys, ``--set`` typing without
``eval``, and the neighbour wrap at the antimeridian.
"""

from __future__ import annotations

import ast
from math import floor
from pathlib import Path

import pytest

from orthostudio.model import TileRef
from orthostudio.pipeline.build import TileDsfParams, TileTexturesParams
from orthostudio.tilefiles.paths import round_latlon, short_latlon

REPO = Path(__file__).resolve().parents[1]
# ----------------------------------------------------------------- O4_File_Names.py:24-41


def reference_short_latlon(lat: int, lon: int) -> str:
    strlat = f"{lat:+.0f}".zfill(3)
    strlon = f"{lon:+.0f}".zfill(4)
    return strlat + strlon


def reference_round_latlon(lat: int, lon: int) -> str:
    strlatround = f"{floor(lat / 10) * 10:+.0f}".zfill(3)
    strlonround = f"{floor(lon / 10) * 10:+.0f}".zfill(4)
    return strlatround + strlonround


@pytest.mark.parametrize(
    "lat, lon",
    [(43, 5), (0, 0), (-1, -1), (-33, 151), (-34, -58), (-90, -180), (89, 179), (-10, -10),
     (10, -100), (-51, -73), (1, -1), (-1, 1)],
)  # fmt: skip
def test_tile_name_and_folder_match_ortho4xp_on_every_hemisphere(lat: int, lon: int) -> None:
    t = TileRef(lat, lon)
    assert t.name == reference_short_latlon(lat, lon) == short_latlon(lat, lon)
    assert t.folder == reference_round_latlon(lat, lon) == round_latlon(lat, lon)
    assert TileRef.parse(t.name) == t
    assert t.dsf_relpath.as_posix() == f"Earth nav data/{t.folder}/{t.name}.dsf"


def test_neighbour_wraps_at_the_antimeridian_unlike_ortho4xp() -> None:
    """``O4_Mask_Utils.py:223-236`` looks for ``zOrtho4XP_<lat><lon+1>`` without wrapping (``+180``
    does not exist); ``TileRef.neighbour`` wraps to ``-180``. Only tiles at ``lon=179`` / ``-180``
    differ (a real mesh of the wrapped tile would be used by OrthoStudio XP as a mask neighbour and
    ignored by Ortho4XP). Documented, not a defect."""
    assert TileRef(43, 179).neighbour(0, 1) == TileRef(43, -180)
    assert TileRef(43, -180).neighbour(0, -1) == TileRef(43, 179)
    assert reference_short_latlon(43, 180) == "+43+180"  # what Ortho4XP would look for


# ------------------------------------------------------- imagery parameters versus the keys


def _tile_attrs(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "tile"
    }


def test_dsf_key_ignores_what_the_dsf_does_not_read() -> None:
    """K2: ``sea_texture_blur`` (imagery) and ``masks_width`` (masks stage) must not be in the
    DSF key; ``distance_masks_too`` is carried by the masks artefact digest."""
    fields = set(TileDsfParams.model_fields)
    assert "sea_texture_blur" not in fields and "masks_width" not in fields
    assert "sea_texture_blur" in TileTexturesParams.model_fields


# --------------------------------------------------------------------------- --set typing


def test_set_overrides_are_typed_like_an_ortho4xp_cfg_line_without_eval() -> None:
    from orthostudio.cli import _parse_sets

    out = _parse_sets(
        [
            "zone_list=[([43.2, 5.3, 43.2, 5.5, 43.4, 5.5, 43.4, 5.3, 43.2, 5.3], 15, 'BI')]",
            "masks_width=[20, 40, 80]",
            "fill_nodata=False",
            "cover_airports_with_highres=ICAO",
            "water_tech=XP12",
            "ratio_water=0.5",
        ]
    )
    assert out["zone_list"] == [([43.2, 5.3, 43.2, 5.5, 43.4, 5.5, 43.4, 5.3, 43.2, 5.3], 15, "BI")]
    assert out["masks_width"] == [20, 40, 80]
    assert out["fill_nodata"] is False
    assert out["cover_airports_with_highres"] == "ICAO"
    assert out["water_tech"] == "XP12" and out["ratio_water"] == 0.5
    params = TileDsfParams.subset_of({**out, "tile": "+43+005"})
    assert params.zone_list[0][1] == 15 and params.water_tech == "XP12"
