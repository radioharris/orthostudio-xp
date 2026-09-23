"""A zone that raises nothing says so.

A zone's level is read at the centre of each mesh cell, which is Ortho4XP's rule
(``zone_list_to_ortho_dico``) and about 850 m at ``mesh_zl`` 19. A user drew a 300 m band over
the darker edge of a tile, set it sharper, built, and saw no change and read no word: his zone
held no cell centre (2026-09-23). The rule stays; the silence does not.
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


def test_a_zone_finer_than_a_mesh_cell_says_it_raises_nothing(caplog) -> None:  # type: ignore[no-untyped-def]
    thin = _between_two_cell_centres()
    with caplog.at_level(logging.WARNING, logger="orthostudio.dsf.zones"):
        texture_map(TILE, _params([(thin, 18, "BI")]))
    said = [r.getMessage() for r in caplog.records]
    assert any("takes no mesh cell" in m for m in said), said
    assert any("level 18" in m for m in said)
    assert any(" m here" in m for m in said), "it says how big a cell is here"


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
