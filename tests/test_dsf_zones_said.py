# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A zone takes every mesh cell it covers a part of, and one that raises nothing says so.

Ortho4XP read a zone's level at the centre of each mesh cell (``zone_list_to_ortho_dico``), about
1 km at ``mesh_zl`` 19: a user drew a 300 m band over the darker edge of a tile, set it sharper,
built, and saw no change and read no word (2026-09-23); another drew eleven ZL18 zones, seven
raised nothing and the rest ended in straight seams across what he drew (2026-09-29). A zone now
takes every cell it covers a pixel of (``dsf/zones.cells_touched``).
"""

from __future__ import annotations

import logging

from orthostudio.dsf.params import DsfParams
from orthostudio.dsf.zones import texture_map
from orthostudio.model import TileRef

TILE = TileRef(46, 6)


def _params(zone_list: list[tuple[list[float], int, str]]) -> DsfParams:
    return DsfParams(default_zl=16, default_website="BI", mesh_zl=19, zone_list=zone_list)


def _band(lat: float, lon: float, height: float, width: float) -> list[float]:
    """A ring, latitude first and closed, as ``zone_list`` holds them."""
    return [
        lat, lon,
        lat, lon + width,
        lat + height, lon + width,
        lat + height, lon,
        lat, lon,
    ]  # fmt: skip


def _between_two_cell_centres() -> list[float]:
    """A small zone laid deliberately between two mesh cell centres, where nothing reads it."""
    from orthostudio.imagery.grid import texture_at, tile_to_wgs84

    first = texture_at(TILE.lat + 1, TILE.lon, 19, "")
    centres = [tile_to_wgs84(first.til_x + 8 + 16 * i, first.til_y + 8, 19) for i in range(2)]
    (lat0, lon0), (_lat1, lon1) = centres
    mid = (lon0 + lon1) / 2
    gap = abs(lon1 - lon0) / 8
    return _band(lat0 - gap / 2, mid - gap / 2, gap, gap)


def test_a_zone_finer_than_a_mesh_cell_takes_the_cells_it_covers(caplog) -> None:  # type: ignore[no-untyped-def]
    thin = _between_two_cell_centres()  # astride the edge of two cells, far from both centres
    with caplog.at_level(logging.WARNING, logger="orthostudio.dsf.zones"):
        tmap = texture_map(TILE, _params([(thin, 18, "BI")]))
    assert not [r for r in caplog.records if "takes no mesh cell" in r.getMessage()]
    assert int((tmap.zl == 18).sum()) == 2, "the two cells it covers a part of"


def _corner(k: int, m: int) -> tuple[float, float]:
    """``(lat, lon)`` of the corner shared by four mesh cells, ``k`` columns and ``m`` rows in."""
    from orthostudio.imagery.grid import texture_at, tile_to_wgs84

    first = texture_at(TILE.lat + 1, TILE.lon, 19, "")
    return tile_to_wgs84(first.til_x + 16 * k, first.til_y + 16 * m, 19)


def _box(north: float, west: float, south: float, east: float) -> list[float]:
    return _band(south, west, north - south, east - west)


def test_a_zone_on_the_corner_of_four_cells_takes_all_four() -> None:
    """A helipad of some 150 m where four cells meet held none of their centres."""
    lat, lon = _corner(30, 30)
    pad = _box(lat + 0.0007, lon - 0.0007, lat - 0.0007, lon + 0.0007)
    tmap = texture_map(TILE, _params([(pad, 18, "BI")]))
    assert int((tmap.zl == 18).sum()) == 4


def test_a_zone_drawn_on_the_cell_edges_takes_those_cells_and_no_more() -> None:
    """A zone laid on the squares, as one draws them, does not spill into the next ones."""
    north, west = _corner(20, 20)
    south, east = _corner(23, 23)
    tmap = texture_map(TILE, _params([(_box(north, west, south, east), 18, "BI")]))
    assert int((tmap.zl == 18).sum()) == 9


def test_where_two_zones_share_a_cell_the_first_of_the_list_wins() -> None:
    lat, lon = _corner(30, 30)
    pad = _box(lat + 0.0007, lon - 0.0007, lat - 0.0007, lon + 0.0007)  # four cells
    north, west = _corner(29, 29)
    south, east = _corner(32, 32)
    block = _box(north, west, south, east)  # the nine cells around them
    first = texture_map(TILE, _params([(pad, 18, "BI"), (block, 17, "BI")]))
    assert (int((first.zl == 18).sum()), int((first.zl == 17).sum())) == (4, 5)
    second = texture_map(TILE, _params([(block, 17, "BI"), (pad, 18, "BI")]))
    assert (int((second.zl == 18).sum()), int((second.zl == 17).sum())) == (0, 9)


def test_a_zone_that_does_raise_cells_says_nothing(caplog) -> None:  # type: ignore[no-untyped-def]
    wide = _band(46.4, 6.4, 0.2, 0.2)  # about 22 km: many cells
    with caplog.at_level(logging.WARNING, logger="orthostudio.dsf.zones"):
        tmap = texture_map(TILE, _params([(wide, 18, "BI")]))
    assert not [r for r in caplog.records if "takes no mesh cell" in r.getMessage()]
    assert 18 in set(tmap.zl.flatten().tolist())


def _idle_by_the_build(zone_list: list[tuple[list[float], int, str]], caplog) -> set[int]:  # type: ignore[no-untyped-def]
    """Which zones the build itself took no cell for, read from what it says, 0-based."""
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="orthostudio.dsf.zones"):
        texture_map(TILE, _params(zone_list))
    found = set()
    for record in caplog.records:
        message = record.getMessage()
        if "takes no mesh cell" in message:
            found.add(int(message.split("the zone ")[1].split(" of the list")[0]) - 1)
    return found


def test_a_zone_another_zone_covers_is_named_too(caplog) -> None:  # type: ignore[no-untyped-def]
    """Its level changes nothing, because the zone above it wins every cell they share."""
    wide = _band(46.4, 6.4, 0.2, 0.2)
    covered = _band(46.45, 6.45, 0.05, 0.05)
    assert _idle_by_the_build([(wide, 18, "BI"), (covered, 17, "BI")], caplog) == {1}
    said = [r.getMessage() for r in caplog.records if "takes no mesh cell" in r.getMessage()]
    assert "level 17" in said[0] and " m here" in said[0], said


def test_a_dsf_assigned_at_the_cell_centres_is_built_again() -> None:
    """The rule's version enters the DSF's key. Left at 1, a tile built with zones before kept
    its DSF from the store at the next build, and the new rule never reached it (found checking
    the change, 2026-09-30); the DSF of a tile without zones comes out the same, so its textures
    and pack are taken again from the store."""
    from orthostudio.pipeline.build import TILE_DSF

    assert TILE_DSF.version >= 2
