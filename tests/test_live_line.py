# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's line in X-Plane's list of sceneries (orthostudio.live.scenery): listed by Live only
where it is missing, right above the first mesh; never moved after, whoever moved it; checked at a
start, unchecked at a stop; moved once when the setting own_tiles changes (issue 24, 2026-11-13)."""

from __future__ import annotations

from pathlib import Path

from orthostudio.install.scenery_packs import ORTHO_PREFIX, SceneryPacks
from orthostudio.live import session
from orthostudio.live.filesystem import is_folder_link
from orthostudio.live.pack import LINK_NAME
from orthostudio.live.scenery import list_live, move_live
from test_live_covered import dsf, put_dsf

TILE = f"{ORTHO_PREFIX}+46+006"


def xplane(tmp_path: Path, names: list[str], live: str | None = None) -> Path:
    """An X-Plane whose list holds ``names`` (``*``: X-Plane's airports), and Live's line,
    unchecked, right above ``live``."""
    xp = tmp_path / "X-Plane 12"
    scenery = xp / "Custom Scenery"
    scenery.mkdir(parents=True)
    lines = []
    for name in names:
        if name == live:
            lines.append(f"SCENERY_PACK_DISABLED Custom Scenery/{LINK_NAME}/")
        lines.append(
            "SCENERY_PACK *GLOBAL_AIRPORTS*"
            if name == "*"
            else f"SCENERY_PACK Custom Scenery/{name}/"
        )
    (scenery / "scenery_packs.ini").write_text(
        "I\n1000 Version\nSCENERY\n\n" + "\n".join(lines) + "\n"
    )
    put_dsf(scenery / "simHeaven_X-World_Europe-1-vfr", (46, 6), dsf({"sim/overlay": "1"}))
    (scenery / "z_library").mkdir()  # a library: no DSF
    put_dsf(scenery / "z_roads", (46, 6), dsf({"sim/overlay": "1"}))  # overlays so named
    put_dsf(scenery / TILE, (46, 6), b"a tile")
    put_dsf(scenery / "Photo Lyon", (45, 4), dsf({"sim/west": "4"}))
    put_dsf(scenery / "zzz_mesh", (46, 6), dsf({"sim/west": "6"}))
    return xp


def names(xp: Path) -> list[str]:
    packs = SceneryPacks.load(xp / "Custom Scenery" / "scenery_packs.ini")
    return [
        "*" if e.is_global_airports else e.name if e.enabled else "-" + e.name
        for e in packs.entries
    ]


LIST = [
    "*",
    "z_library",
    "simHeaven_X-World_Europe-1-vfr",
    "z_roads",
    TILE,
    "Photo Lyon",
    "zzz_mesh",
]


def test_a_missing_line_goes_right_above_the_first_mesh(tmp_path: Path) -> None:
    """Issue 24: a library named z... above simHeaven X-World took Live's line above it, whose
    buildings Live's photos then hid. A pack is a mesh by its name and its ground."""
    xp = xplane(tmp_path, LIST)
    pack = tmp_path / "pack"
    pack.mkdir()
    list_live(xp, pack, "first", log=lambda _: None)
    assert names(xp) == [*LIST[:-1], LINK_NAME, "zzz_mesh"]
    # with "Live everywhere": above the pilot's tiles
    xp = xplane(tmp_path / "b", LIST)
    list_live(xp, pack, "under", log=lambda _: None)
    assert names(xp) == [*LIST[:4], LINK_NAME, *LIST[4:]]
    # no mesh: the end; a mesh's folder out of reach (a disk unplugged): its name decides
    xp = xplane(tmp_path / "c", LIST[:-1])
    list_live(xp, pack, "first", log=lambda _: None)
    assert names(xp) == [*LIST[:-1], LINK_NAME]
    xp = xplane(tmp_path / "d", [*LIST[:-1], "zzz_unplugged", "zzz_mesh"])
    list_live(xp, pack, "first", log=lambda _: None)
    assert names(xp) == [*LIST[:-1], LINK_NAME, "zzz_unplugged", "zzz_mesh"]


def test_the_line_stays_where_it_is_listed(tmp_path: Path) -> None:
    """Moved by the pilot or xOrganizer, even above simHeaven: checked there at a start, unchecked
    there at a stop, its link kept, so that X-Plane keeps the line where it is."""
    xp = xplane(tmp_path, LIST, live="z_library")
    pack = tmp_path / "pack"
    pack.mkdir()
    for own_tiles in ("first", "under"):
        list_live(xp, pack, own_tiles, log=lambda _: None)
        assert names(xp) == ["*", LINK_NAME, *LIST[1:]]
        session.uninstall(xp, lambda _: None)
        assert names(xp) == ["*", "-" + LINK_NAME, *LIST[1:]]
        assert is_folder_link(xp / "Custom Scenery" / LINK_NAME)


def test_the_setting_moves_the_line_once(tmp_path: Path) -> None:
    xp = xplane(tmp_path, LIST, live="zzz_mesh")
    assert move_live(xp, "under")
    assert names(xp) == [*LIST[:4], "-" + LINK_NAME, *LIST[4:]]  # unchecked, as it was
    assert not move_live(xp, "under")
    assert move_live(xp, "first")
    assert names(xp) == [*LIST[:-1], "-" + LINK_NAME, "zzz_mesh"]
    # not listed: listed by Live's next start
    xp = xplane(tmp_path / "b", LIST)
    before = names(xp)
    assert not move_live(xp, "under") and names(xp) == before


def test_a_tile_built_joins_the_others(tmp_path: Path) -> None:
    """Build lists a new tile right after the last of its tiles: under Live's line with "Live
    everywhere", above it else, with no rule of its own about Live."""
    new = f"{ORTHO_PREFIX}+47+006"
    for own_tiles, expected in (
        ("first", [*LIST[:5], new, "Photo Lyon", LINK_NAME, "zzz_mesh"]),
        ("under", [*LIST[:4], LINK_NAME, TILE, new, "Photo Lyon", "zzz_mesh"]),
    ):
        xp = xplane(tmp_path / own_tiles, LIST)
        pack = tmp_path / own_tiles / "pack"
        pack.mkdir()
        list_live(xp, pack, own_tiles, log=lambda _: None)
        ini = xp / "Custom Scenery" / "scenery_packs.ini"
        packs = SceneryPacks.load(ini)
        packs.ensure(new, kind="ortho")
        packs.save(ini)
        assert names(xp) == expected
