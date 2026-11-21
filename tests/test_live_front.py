# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Squares of another tool's mesh put in front of Live on its map (discussion 33): a pilot's own
mesh of the Alps, its elevation fixed by hand, flown with Live's photos on the squares he chose,
Live's own mesh everywhere else. One setting, a list of squares; their DSF wins in the pack, the
rest in today's order; the pack set up again when the list changes, never under a running
X-Plane."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.test_live_other_mesh import mesh
from tests.test_live_overlays import xplane
from tests.test_live_regions import _another_disk

from live_helpers import NAMES, make_source, make_xplane
from orthostudio.live import control, scenery, session, settings_file
from orthostudio.live.covered import listed_above_live
from orthostudio.live.filesystem import folder_link
from orthostudio.live.pack import LINK_NAME
from orthostudio.live.region import (
    REGION_MARKER,
    front_of_pack,
    pack_to_redo,
    setup_region,
)
from orthostudio.live.scenery import NO_OTHER_MESH, Meshes, default_meshes, front_saved, other_mesh
from orthostudio.live.settings import LiveSettings, load_settings, save_settings
from orthostudio.live.web import create_live_app
from test_api_fakes import anyio_backend as anyio_backend
from test_api_fakes import client_for

COURCHEVEL, BESIDE = (46, 2), (45, 6)  # the squares of the test sources' DSFs
DSF = Path("Earth nav data") / "+40+000" / "+46+002.dsf"


@pytest.fixture(autouse=True)
def looked_at_anew(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test's folder looked at, not one kept from another test."""
    monkeypatch.setattr(scenery, "_found", (0.0, (), NO_OTHER_MESH))


def quiet(_: str) -> None:
    pass


def live_and_alps(root: Path) -> tuple[Path, Path]:
    """Live's region and the pilot's mesh, both with a DSF on Courchevel's square, told apart."""
    live = make_source(root / "live", tile=BESIDE, textures=NAMES)
    alps = make_source(root / "alps", tile=BESIDE, textures=NAMES)
    (alps / DSF).write_bytes(b"XPLNEDSF the Alps' own elevation")
    return live, alps


def test_the_setting_is_a_list_of_squares(tmp_path: Path, monkeypatch) -> None:
    """Each square once, as X-Plane names its DSF; a wrong name refused, and on reading the file
    the setting alone back to none; kept in live.toml as a list."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    chosen = LiveSettings(other_mesh_front=["+45+007", " +45+006", "+45+006", "-01-001"])
    assert chosen.other_mesh_front == ["-01-001", "+45+006", "+45+007"]
    for wrong in ("45+006", "+45+6", "+90+000", "+45+180", "Courchevel"):
        with pytest.raises(ValidationError):
            LiveSettings(other_mesh_front=[wrong])
    save_settings(LiveSettings(disk_gb=40, other_mesh_front=["+45+006"]))
    assert tomllib.loads(settings_file().read_text())["stream"]["other_mesh_front"] == ["+45+006"]
    assert load_settings().other_mesh_front == ["+45+006"]
    settings_file().write_text('[stream]\ndisk_gb = 40\nother_mesh_front = ["Alps"]\n')
    assert load_settings().other_mesh_front == [] and load_settings().disk_gb == 40


def test_a_square_in_front_takes_the_other_mesh_s_dsf(tmp_path: Path) -> None:
    """The pack takes the other mesh's DSF on the square in front, Live's elsewhere, and records
    the front with the regions; the same front again: nothing to do; another front, the regions in
    another order: set up again."""
    live, alps = live_and_alps(tmp_path)
    pack = tmp_path / "pack"
    setup_region([live, alps], pack, log=quiet)
    assert (pack / DSF).read_bytes() == (live / DSF).read_bytes()  # behind Live: Live's
    lines: list[str] = []
    front = {COURCHEVEL: alps}
    assert pack_to_redo([live, alps], pack, front)
    setup_region([live, alps], pack, log=lines.append, front=front)
    assert any("1 squares in front of Live" in line for line in lines)
    assert (pack / DSF).read_bytes() == b"XPLNEDSF the Alps' own elevation"
    other = pack / "Earth nav data" / "+40+000" / "+45+006.dsf"
    assert other.read_bytes() == (live / "Earth nav data" / "+40+000" / "+45+006.dsf").read_bytes()
    assert json.loads((pack / REGION_MARKER).read_text())["front"] == {"+46+002": str(alps)}
    assert front_of_pack(pack) == front
    assert not pack_to_redo([live, alps], pack, front)
    lines.clear()
    setup_region([live, alps], pack, log=lines.append, front=front)
    assert lines == []  # nothing to do
    assert pack_to_redo([alps, live], pack, front)  # a reorder: a square's DSF another's
    assert pack_to_redo([live, alps], pack, {})  # back behind Live
    setup_region([live, alps], pack, log=quiet)
    assert (pack / DSF).read_bytes() == (live / DSF).read_bytes()
    assert front_of_pack(pack) == {}


def test_a_square_in_front_writes_nothing_into_a_region_on_another_disk(
    tmp_path: Path, monkeypatch
) -> None:
    """Live's region on another disk, its DSFs linked: the link made a real folder first, so that
    the other mesh's DSF never goes into the region's own folder."""
    live, alps = live_and_alps(tmp_path)
    _another_disk(monkeypatch, (live, alps))
    pack = tmp_path / "pack"
    before = (live / DSF).read_bytes()
    setup_region([live, alps], pack, log=quiet, front={COURCHEVEL: alps})
    assert (live / DSF).read_bytes() == before
    assert (pack / DSF).read_bytes() == b"XPLNEDSF the Alps' own elevation"
    assert not (pack / "Earth nav data").is_symlink()


def test_the_folder_s_squares_and_those_in_front(tmp_path: Path) -> None:
    """Every square of the folder's ground naming photos is offered, copies of a region already
    there included; a mesh whose squares all have Live's is left aside, unless it has one put in
    front of Live, whose DSF it then gives; a square in front it does not have is not taken."""
    live = mesh(tmp_path / "live" / "z_osxp_eur", squares=(COURCHEVEL, BESIDE))
    folder = tmp_path / "Alps"
    alps = mesh(folder / "z_ao_alps", squares=(COURCHEVEL,))
    behind = other_mesh(folder, [live])
    assert behind.meshes == [] and behind.refused == [("z_ao_alps", "already")]
    assert behind.ground == {COURCHEVEL: alps} and behind.front == {}
    ahead = other_mesh(folder, [live], front={COURCHEVEL, (10, 10)})
    assert ahead.meshes == [alps] and ahead.refused == []
    assert ahead.front == {COURCHEVEL: alps}
    copy = mesh(folder / "z_ao_alps_copy", squares=(COURCHEVEL,))
    assert other_mesh(folder, [live], front={COURCHEVEL}).refused == [("z_ao_alps_copy", "already")]
    assert copy not in other_mesh(folder, [live], front={COURCHEVEL}).front.values()


def test_one_rule_for_every_caller(tmp_path: Path, monkeypatch) -> None:
    """The regions in today's order, the squares in front beside them: the start, the window's
    checks and the command line read the same."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    theirs = mesh(xp / "Custom Scenery" / "z_autoortho" / "scenery" / "z_ao_eur", squares=(BESIDE,))
    folder = tmp_path / "Alps"
    alps = mesh(folder / "z_ao_alps", squares=(COURCHEVEL, BESIDE))
    save_settings(LiveSettings(other_mesh=str(folder), other_mesh_front=["+45+006"]))
    root = tmp_path / "live"
    assert default_meshes(xp, root) == Meshes([theirs, alps], {BESIDE: alps})
    assert control.stream_meshes(xp) == default_meshes(xp)


def test_a_save_keeps_the_squares_the_folder_has(tmp_path: Path) -> None:
    """Another folder, or none: no square in front. A square the folder no longer has: dropped at
    the next save; all kept while the folder is not there (its disk unplugged)."""
    folder = tmp_path / "Alps"
    mesh(folder / "z_ao_alps", squares=(COURCHEVEL,))
    before = LiveSettings(other_mesh=str(folder), other_mesh_front=["+46+002", "+45+006"])
    assert front_saved(before, before).other_mesh_front == ["+46+002"]
    elsewhere = before.model_copy(update={"other_mesh": str(tmp_path / "Pyrenees")})
    assert front_saved(elsewhere, before).other_mesh_front == []
    assert front_saved(before.model_copy(update={"other_mesh": ""}), before).other_mesh_front == []
    unplugged = LiveSettings(other_mesh=str(tmp_path / "gone"), other_mesh_front=["+46+002"])
    assert front_saved(unplugged, unplugged).other_mesh_front == ["+46+002"]


def test_the_folder_s_pack_checked_above_live_in_x_plane(tmp_path: Path) -> None:
    """The folder's mesh also checked in X-Plane's list above Live's line: X-Plane draws it there
    itself, without photos. Below Live's line, unchecked, or another pack of that name elsewhere:
    nothing to say."""
    above = "SCENERY_PACK Custom Scenery/z_ao_alps/"
    live = f"SCENERY_PACK Custom Scenery/{LINK_NAME}/"
    namesake = mesh(tmp_path / "elsewhere" / "z_ao_alps", squares=(COURCHEVEL,))
    for name, lines, said in [
        ("a", [above, live], ["z_ao_alps"]),
        ("b", [live, above], []),
        ("c", ["SCENERY_DISABLED Custom Scenery/z_ao_alps/", live], []),
    ]:
        xp = xplane(tmp_path / name, lines)
        alps = mesh(xp / "Custom Scenery" / "z_ao_alps", squares=(COURCHEVEL,))
        assert listed_above_live(xp, [alps], LINK_NAME) == said, name
        assert listed_above_live(xp, [namesake], LINK_NAME) == [], name


def test_what_the_window_says_and_draws(tmp_path: Path, monkeypatch) -> None:
    """The line under the setting: the squares in front, whether they wait for Live's next start,
    the folder's pack checked above Live in X-Plane; the map: every square of the folder, those in
    front told apart."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    folder = tmp_path / "X-Plane 12" / "Custom Scenery"
    xp = xplane(
        tmp_path,
        ["SCENERY_PACK Custom Scenery/z_ao_alps/", f"SCENERY_PACK Custom Scenery/{LINK_NAME}/"],
    )
    mesh(folder / "z_ao_alps", squares=(COURCHEVEL, BESIDE))
    save_settings(LiveSettings(other_mesh=str(folder), other_mesh_front=["+45+006"]))
    said = control._other_mesh_said(xp)
    assert said is not None
    assert said["front"] == ["+45+006"] and said["front_next"]  # no start has taken it yet
    assert said["in_xplane"] == ["z_ao_alps"]
    live = control.LiveControl(checks=lambda: {"xplane": str(xp)}, echo=quiet)
    assert live.other_squares() == [
        {"lat": 45, "lon": 6, "front": True},
        {"lat": 46, "lon": 2, "front": False},
    ]


def test_no_start_on_a_new_front_while_x_plane_runs(tmp_path: Path, monkeypatch) -> None:
    """A square put in front while X-Plane runs: the start refused, saying why; X-Plane closed,
    the pack set up with it."""
    from orthostudio.live import osxpregions, winsearch

    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    xp = make_xplane(tmp_path)
    live, alps = live_and_alps(tmp_path)
    save_settings(LiveSettings(other_mesh=str(alps.parent), other_mesh_front=["+46+002"]))
    pack = tmp_path / "pack"
    setup_region([live, alps], pack, log=quiet)
    monkeypatch.setattr(winsearch, "keep_out", lambda folder, log=None: None)
    monkeypatch.setattr(osxpregions, "settle", lambda root, log, swap, only=None: None)
    monkeypatch.setattr(session, "_overlays", lambda *args: None)
    meshes = Meshes([live, alps], {COURCHEVEL: alps})
    monkeypatch.setattr(session, "default_meshes", lambda xp, other=None: meshes)
    monkeypatch.setattr(session, "is_installed", lambda *a: True)
    folder_link(pack, xp / "Custom Scenery" / LINK_NAME)  # as Live links it there
    monkeypatch.setattr(session, "xplane_running", lambda: True)
    options = session.Options(xplane=xp, pack=pack, no_plugin=True)
    with pytest.raises(session.RefusedError, match="squares in front of Live changed"):
        session.prepare(options, log=quiet)
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    session.prepare(options, log=quiet)
    assert front_of_pack(pack) == {COURCHEVEL: alps}
    assert (pack / DSF).read_bytes() == b"XPLNEDSF the Alps' own elevation"


@pytest.mark.anyio
async def test_the_map_s_click_saves_the_square(tmp_path: Path, monkeypatch) -> None:
    """The map puts a square in front with the settings' PUT, as the source is saved: a square the
    folder lacks dropped; the map then draws it in front; another folder chosen, none in front."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    folder = tmp_path / "Alps"
    mesh(folder / "z_ao_alps", squares=(COURCHEVEL,))
    save_settings(LiveSettings(other_mesh=str(folder)))
    live = control.LiveControl(checks=lambda: {"xplane": None}, echo=quiet, catalog=lambda r: {})
    async with client_for(create_live_app(live)) as client:
        put = {"other_mesh_front": ["+46+002", "+10+010"]}
        got = await client.put("/api/live/settings", json=put)
        assert got.status_code == 200 and got.json()["other_mesh_front"] == ["+46+002"]
        drawn = (await client.get("/api/live/map/other")).json()
        assert drawn == [{"lat": 46, "lon": 2, "front": True}]
        wrong = await client.put("/api/live/settings", json={"other_mesh_front": ["Alps"]})
        assert wrong.status_code == 422
        other = await client.put("/api/live/settings", json={"other_mesh": str(tmp_path / "B")})
        assert other.json()["other_mesh_front"] == []
