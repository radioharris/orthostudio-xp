"""A zone that raises nothing says so.

A zone's level is read at the centre of each mesh cell, which is Ortho4XP's rule
(``zone_list_to_ortho_dico``) and about 850 m at ``mesh_zl`` 19. A user drew a 300 m band over
the darker edge of a tile, set it sharper, built, and saw no change and read no word: his zone
held no cell centre (2026-09-23). The rule stays; the silence does not.
"""

from __future__ import annotations

import logging
from pathlib import Path

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
    assert any("raises nothing" in m for m in said), said
    assert any("level 18" in m for m in said)
    assert any(" m here" in m for m in said), "it says how big a cell is here"


def test_a_zone_that_does_raise_cells_says_nothing(caplog) -> None:  # type: ignore[no-untyped-def]
    wide = _band(46.4, 6.4, 0.2, 0.2)  # about 22 km: many cells
    with caplog.at_level(logging.WARNING, logger="orthostudio.dsf.zones"):
        tmap = texture_map(TILE, _params([(wide, 18, "BI")]))
    assert not [r for r in caplog.records if "raises nothing" in r.getMessage()]
    assert 18 in set(tmap.zl.flatten().tolist())


def test_the_one_that_is_too_small_is_named_among_several(caplog) -> None:  # type: ignore[no-untyped-def]
    wide = _band(46.4, 6.4, 0.2, 0.2)
    thin = _between_two_cell_centres()
    with caplog.at_level(logging.WARNING, logger="orthostudio.dsf.zones"):
        texture_map(TILE, _params([(wide, 18, "BI"), (thin, 17, "BI")]))
    said = [r.getMessage() for r in caplog.records if "raises nothing" in r.getMessage()]
    assert len(said) == 1 and "level 17" in said[0]
    assert "zone 2 of the list" in said[0], said[0]


def test_the_plan_says_it_before_the_build(tmp_path: Path) -> None:
    """Told before the build it costs nothing; told after, it costs the build. The warning rides
    with the estimate, beside the one about a full disk."""
    from orthostudio.api.specs import _a_zone_raises_nothing
    from orthostudio.pipeline.build import BuildSpec

    def spec(zone_list: list[tuple[list[float], int, str]]) -> BuildSpec:
        return BuildSpec(
            tile=TILE,
            provider="BI",
            zl=16,
            out_dir=tmp_path,
            config={"zone_list": zone_list, "mesh_zl": 19},
        )

    thin = _between_two_cell_centres()
    wide = _band(46.4, 6.4, 0.2, 0.2)
    assert _a_zone_raises_nothing([spec([(thin, 18, "BI")])])
    assert not _a_zone_raises_nothing([spec([(wide, 18, "BI")])])
    assert not _a_zone_raises_nothing([spec([])])
    assert _a_zone_raises_nothing([spec([(wide, 18, "BI")]), spec([(thin, 17, "BI")])])


def test_the_warning_has_words_and_a_remedy_in_both_languages() -> None:
    from pathlib import Path as _Path

    from orthostudio.errors import OsxpError

    said = OsxpError("ZONE_TOO_SMALL")
    assert "changes nothing" in said.message and "colours still apply" in said.remedy
    i18n = (_Path(__file__).resolve().parents[1] / "src/orthostudio/ui/i18n.js").read_text("utf-8")
    assert "ZONE_TOO_SMALL:" in i18n and "niveau ne change rien" in i18n


def _idle_by_the_build(zone_list: list[tuple[list[float], int, str]], caplog) -> set[int]:  # type: ignore[no-untyped-def]
    """Which zones the build itself took no cell for, read from what it says, 0-based."""
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="orthostudio.dsf.zones"):
        texture_map(TILE, _params(zone_list))
    found = set()
    for record in caplog.records:
        message = record.getMessage()
        if "raises nothing" in message:
            found.add(int(message.split("the zone ")[1].split(" of the list")[0]) - 1)
    return found


def test_the_plan_answers_what_the_build_will_do(caplog) -> None:  # type: ignore[no-untyped-def]
    """The page used to read the rings a second time, in exact geometry, where the build reads
    them through a 4096² image at the centre of each cell. The two disagreed both ways: a zone
    another zone covers takes no cell and the page called it fine, and a zone the image rounds
    onto a centre it geometrically misses was called idle (found in review, 2026-09-23)."""
    from orthostudio.zones import zone_list_raising_nothing

    wide = _band(46.4, 6.4, 0.2, 0.2)
    thin = _between_two_cell_centres()
    covered = _band(46.45, 6.45, 0.05, 0.05)  # wholly inside ``wide``, which is drawn over it
    edge = _band(46.0, 6.0, 0.004, 0.004)  # a corner of the tile, where the centre is clamped
    cases: list[list[tuple[list[float], int, str]]] = [
        [(thin, 18, "BI")],
        [(wide, 18, "BI")],
        [(wide, 18, "BI"), (covered, 17, "BI")],
        [(covered, 17, "BI"), (wide, 18, "BI")],
        [(edge, 19, "BI")],
        [(wide, 18, "BI"), (thin, 17, "BI"), (covered, 16, "BI")],
    ]
    for zone_list in cases:
        build = _idle_by_the_build(zone_list, caplog)
        plan = set(zone_list_raising_nothing([list(z) for z in zone_list], TILE, 19))
        assert plan == build, f"{zone_list!r}: the page says {plan}, the build says {build}"


def test_a_zone_another_zone_covers_is_named_too(caplog) -> None:  # type: ignore[no-untyped-def]
    """Its level changes nothing, because the zone above it wins every cell they share."""
    wide = _band(46.4, 6.4, 0.2, 0.2)
    covered = _band(46.45, 6.45, 0.05, 0.05)
    assert _idle_by_the_build([(wide, 18, "BI"), (covered, 17, "BI")], caplog) == {1}
