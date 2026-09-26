# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The real ``apt.dat`` of the local X-Plane 12 install (read only, skipped without it)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orthostudio.airports import AirportIndex, apt_dat_path
from orthostudio.model import TileRef

REAL_XP = Path.home() / "X-Plane 12"
APT = apt_dat_path(REAL_XP)

pytestmark = [
    pytest.mark.xplane,
    pytest.mark.skipif(not APT.is_file(), reason="reference X-Plane 12 apt.dat not present"),
]


@pytest.fixture(scope="module")
def index(tmp_path_factory: pytest.TempPathFactory) -> AirportIndex:
    idx = AirportIndex(tmp_path_factory.mktemp("airports") / "airports.sqlite")
    stats = idx.build(APT)
    assert stats.airports > 30_000
    assert stats.skipped_no_position == 0
    assert stats.seconds < 30.0
    return idx


def test_lfml_marseille(index: AirportIndex) -> None:
    a = index.get("LFML")
    assert a is not None
    assert a.lat == pytest.approx(43.44, abs=0.01)
    assert a.lon == pytest.approx(5.22, abs=0.01)
    assert a.has_datum and a.kind == "land" and a.name == "Marseille Provence"


def test_tiles_around_lfml(index: AirportIndex) -> None:
    tiles = index.tiles_around("LFML", 30)
    assert TileRef(43, 5) in tiles
    assert index.tiles_around("LFML", 0) == [TileRef(43, 5)]


def test_search_by_name_and_prefix(index: AirportIndex) -> None:
    assert "LFML" in {a.icao for a in index.search("marseille")}
    prefix = index.search("LFM")
    assert len(prefix) == 10 and all(a.icao.startswith("LFM") for a in prefix)


def test_kinds_and_meta(index: AirportIndex) -> None:
    meta = index.stats()
    assert int(meta["sea"]) > 0 and int(meta["heli"]) > 0
    assert index.get("ENQA") is not None  # heliport, no datum, first 102 row
    assert not index.is_stale(APT)
