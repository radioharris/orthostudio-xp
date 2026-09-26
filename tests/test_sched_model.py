# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Shared value types of P2 (orthostudio.model): tile naming as in Ortho4XP, artefact references."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from orthostudio.model import ArtifactRef, TileRef, ZoomSpec


@pytest.mark.parametrize(
    ("lat", "lon", "name", "folder"),
    [
        (43, 5, "+43+005", "+40+000"),
        (-33, 151, "-33+151", "-40+150"),
        (0, -1, "+00-001", "+00-010"),
        (-1, -180, "-01-180", "-10-180"),
        (51, -3, "+51-003", "+50-010"),
        (89, 179, "+89+179", "+80+170"),
        (-90, -180, "-90-180", "-90-180"),
    ],
)
def test_tile_name_and_folder_match_ortho4xp(lat: int, lon: int, name: str, folder: str) -> None:
    # O4_File_Names.py:24-41: short_latlon / round_latlon (floor to 10 degrees)
    t = TileRef(lat, lon)
    assert t.name == name and str(t) == name
    assert t.folder == folder
    assert t.dsf_relpath == Path("Earth nav data") / folder / f"{name}.dsf"
    assert TileRef.parse(name) == t


@given(st.integers(-90, 89), st.integers(-180, 179))
def test_parse_round_trips(lat: int, lon: int) -> None:
    t = TileRef(lat, lon)
    assert TileRef.parse(t.name) == t
    assert len(t.name) == 7 and len(t.folder) == 7


@pytest.mark.parametrize("bad", ["43+005", "+43+5", "+43+0050", "", "+ab+cde"])
def test_parse_rejects_malformed_names(bad: str) -> None:
    with pytest.raises(ValueError, match="not a tile name"):
        TileRef.parse(bad)


def test_neighbour_wraps_longitude() -> None:
    assert TileRef(43, 5).neighbour(1, 1) == TileRef(44, 6)
    assert TileRef(43, 179).neighbour(0, 1) == TileRef(43, -180)
    assert TileRef(43, -180).neighbour(0, -1) == TileRef(43, 179)


def test_tile_is_a_plain_tuple() -> None:
    lat, lon = TileRef(43, 5)
    assert (lat, lon) == (43, 5)
    assert TileRef(43, 5) == (43, 5)
    assert ZoomSpec("BI", 16) == ("BI", 16)


def test_artifact_ref_validates_hex() -> None:
    ref = ArtifactRef("a" * 64, "b" * 64, Path("/x"), "rule", "file", 3)
    assert ref.size == 3 and ref.kind == "file"
    with pytest.raises(ValueError, match=r"ArtifactRef\.key"):
        ArtifactRef("A" * 64, "b" * 64, Path("/x"), "rule", "file")
    with pytest.raises(ValueError, match=r"ArtifactRef\.digest"):
        ArtifactRef("a" * 64, "b" * 63, Path("/x"), "rule", "dir")
