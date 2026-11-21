# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Squares of other tools' meshes put in front of Live on its map (discussion 33): a pilot's own
mesh of the Alps, its elevation fixed by hand, flown with Live's photos on the squares he chose,
Live's own mesh everywhere else. One setting, a list of squares; their DSF wins in the pack, the
rest in today's order; the pack set up again when the list or one of their DSFs changes, never
under a running X-Plane. Several folders, in an order: the first that has a square gives it, in
front of Live or behind; each folder added, moved, removed or put in front at once by its own
button."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
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
from orthostudio.live.names import RegionTextures
from orthostudio.live.pack import LINK_NAME
from orthostudio.live.region import (
    REGION_MARKER,
    front_of_pack,
    pack_to_redo,
    setup_region,
    square_owner,
)
from orthostudio.live.scenery import (
    NO_OTHER_MESHES,
    Meshes,
    default_meshes,
    other_mesh,
    other_meshes,
)
from orthostudio.live.settings import (
    SAVING,
    AlreadyListedError,
    LiveSettings,
    add_other_mesh,
    load_settings,
    move_other_mesh,
    put_in_front,
    remove_other_mesh,
    save_settings,
)
from orthostudio.live.web import UI_DIR, create_live_app
from test_api_fakes import anyio_backend as anyio_backend
from test_api_fakes import client_for

COURCHEVEL, BESIDE = (46, 2), (45, 6)  # the squares of the test sources' DSFs
PYRENEES = (42, 0)  # a square of one folder alone
DSF = Path("Earth nav data") / "+40+000" / "+46+002.dsf"


@pytest.fixture(autouse=True)
def looked_at_anew(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test's folder looked at, not one kept from another test."""
    monkeypatch.setattr(scenery, "_found", (0.0, (), NO_OTHER_MESHES))


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
    another order: set up again, saying why. The one place this rule is tested."""
    live, alps = live_and_alps(tmp_path)
    pack = tmp_path / "pack"
    setup_region([live, alps], pack, log=quiet)
    assert (pack / DSF).read_bytes() == (live / DSF).read_bytes()  # behind Live: Live's
    lines: list[str] = []
    front = {COURCHEVEL: alps}
    assert pack_to_redo([live, alps], pack, front) == "front"
    assert not session.region_ready(pack, Meshes([live, alps], front))
    setup_region([live, alps], pack, log=lines.append, front=front)
    assert any("1 squares in front of Live" in line for line in lines)
    assert (pack / DSF).read_bytes() == b"XPLNEDSF the Alps' own elevation"
    other = pack / "Earth nav data" / "+40+000" / "+45+006.dsf"
    assert other.read_bytes() == (live / "Earth nav data" / "+40+000" / "+45+006.dsf").read_bytes()
    recorded = json.loads((pack / REGION_MARKER).read_text())["front"]
    assert recorded["+46+002"]["source"] == str(alps)
    assert front_of_pack(pack) == front
    assert pack_to_redo([live, alps], pack, front) is None
    assert session.region_ready(pack, Meshes([live, alps], front))
    lines.clear()
    setup_region([live, alps], pack, log=lines.append, front=front)
    assert lines == []  # nothing to do
    assert pack_to_redo([alps, live], pack, front) == "regions"  # a square's DSF another's
    assert pack_to_redo([live, alps], pack, {}) == "front"  # back behind Live
    setup_region([live, alps], pack, log=quiet)
    assert (pack / DSF).read_bytes() == (live / DSF).read_bytes()
    assert front_of_pack(pack) == {}


def test_a_dsf_in_front_edited_again_is_taken(tmp_path: Path) -> None:
    """The pilot's DSF of a square in front saved again, its relief fixed once more: the next
    check sets the pack up again, which then holds the new one; its region's folders' times alone
    did not change."""
    live, alps = live_and_alps(tmp_path)
    pack = tmp_path / "pack"
    front = {COURCHEVEL: alps}
    setup_region([live, alps], pack, log=quiet, front=front)
    assert pack_to_redo([live, alps], pack, front) is None
    edited = alps / DSF.with_suffix(".new")  # as an editor saves it: a new file in its place
    edited.write_bytes(b"XPLNEDSF the Alps' relief, fixed again")
    later = (alps / DSF).stat().st_mtime_ns + 5_000_000_000
    os.utime(edited, ns=(later, later))
    os.replace(edited, alps / DSF)
    assert pack_to_redo([live, alps], pack, front) == "front"
    setup_region([live, alps], pack, log=quiet, front=front)
    assert (pack / DSF).read_bytes() == b"XPLNEDSF the Alps' relief, fixed again"
    assert pack_to_redo([live, alps], pack, front) is None


@pytest.mark.parametrize("theirs", ["added", "same"])
def test_a_pack_an_app_of_before_set_up_again(tmp_path: Path, theirs: str) -> None:
    """An app of before, which knows nothing of the squares in front, set the pack up again, its
    regions' DSFs everywhere, the record of the front kept as it was: the next start puts the
    other mesh's DSF back in place, whether the other mesh comes as a region added after those of
    before or as one of them, so that the DSF and the files Live makes for it agree."""
    live, alps = live_and_alps(tmp_path)
    pack = tmp_path / "pack"
    front = {COURCHEVEL: alps}
    setup_region([live, alps], pack, log=quiet, front=front)
    marker = json.loads((pack / REGION_MARKER).read_text())
    before = [live] if theirs == "added" else [live, alps]
    setup_region(before, pack, log=quiet, force=True)  # the app of before
    meta = json.loads((pack / REGION_MARKER).read_text())
    (pack / REGION_MARKER).write_text(json.dumps({**meta, "front": marker["front"]}))
    assert (pack / DSF).read_bytes() == (live / DSF).read_bytes()
    setup_region([live, alps], pack, log=quiet, front=front)
    assert (pack / DSF).read_bytes() == b"XPLNEDSF the Alps' own elevation"
    owner = RegionTextures([live, alps], front=front_of_pack(pack)).owner(COURCHEVEL)
    assert owner == 1  # the files Live makes there: the other mesh's, as its DSF names them


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
    with_copy = other_mesh(folder, [live], front={COURCHEVEL})
    assert with_copy.refused == [("z_ao_alps_copy", "already")]
    assert copy not in with_copy.front.values() and with_copy.packs == [alps, copy]


def test_one_rule_for_every_caller(tmp_path: Path, monkeypatch) -> None:
    """The regions in today's order, the squares in front beside them, as the start, the window's
    checks and the command line read them."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    theirs = mesh(xp / "Custom Scenery" / "z_autoortho" / "scenery" / "z_ao_eur", squares=(BESIDE,))
    folder = tmp_path / "Alps"
    alps = mesh(folder / "z_ao_alps", squares=(COURCHEVEL, BESIDE))
    save_settings(LiveSettings(other_meshes=[str(folder)], other_mesh_front=["+45+006"]))
    root = tmp_path / "live"
    assert default_meshes(xp, root) == Meshes([theirs, alps], {BESIDE: alps})


def test_between_folders_the_first_that_has_a_square_gives_it(tmp_path: Path) -> None:
    """Two folders: behind Live, a square Live lacks takes the DSF of the first folder in the list
    that has it; in front of Live too; a square one folder alone has is that folder's. Their order
    changed, the other folder gives the squares both have, in front and behind; Live's own square
    stays Live's behind it. The map names the folder that gives each square."""
    live = mesh(tmp_path / "live" / "z_osxp_eur", squares=(BESIDE,))
    alps_dir, pyrenees_dir = tmp_path / "Alps", tmp_path / "Pyrenees"
    alps = mesh(alps_dir / "z_ao_alps", squares=(COURCHEVEL, BESIDE))
    pyrenees = mesh(pyrenees_dir / "z_ao_pyr", squares=(COURCHEVEL, PYRENEES))

    def owner(found, square):
        meshes = Meshes([live, *found.meshes], found.front)
        at = square_owner(meshes.sources, meshes.front, square)
        return None if at is None else meshes.sources[at]

    behind = other_meshes([alps_dir, pyrenees_dir], [live], ())
    assert behind.meshes == [alps, pyrenees] and behind.front == {}
    assert [owner(behind, sq) for sq in (COURCHEVEL, PYRENEES, BESIDE)] == [alps, pyrenees, live]
    assert behind.ground == {COURCHEVEL: alps_dir, BESIDE: alps_dir, PYRENEES: pyrenees_dir}
    ahead = other_meshes([alps_dir, pyrenees_dir], [live], {COURCHEVEL, BESIDE, PYRENEES})
    assert ahead.front == {COURCHEVEL: alps, BESIDE: alps, PYRENEES: pyrenees}
    turned = other_meshes([pyrenees_dir, alps_dir], [live], ())
    assert [owner(turned, sq) for sq in (COURCHEVEL, PYRENEES, BESIDE)] == [
        pyrenees,
        pyrenees,
        live,
    ]
    assert turned.ground[COURCHEVEL] == pyrenees_dir
    turned_ahead = other_meshes([pyrenees_dir, alps_dir], [live], {COURCHEVEL, BESIDE})
    assert turned_ahead.front == {COURCHEVEL: pyrenees, BESIDE: alps}


def test_a_folder_not_there_gives_its_squares_to_the_next(tmp_path: Path) -> None:
    """The first folder's disk unplugged: it gives nothing and says so; a square in front comes
    from the next folder that has it, else from Live's mesh."""
    live = mesh(tmp_path / "live" / "z_osxp_eur", squares=(BESIDE,))
    unplugged, pyrenees_dir = tmp_path / "Unplugged", tmp_path / "Pyrenees"
    pyrenees = mesh(pyrenees_dir / "z_ao_pyr", squares=(COURCHEVEL,))
    found = other_meshes([unplugged, pyrenees_dir], [live], {COURCHEVEL, BESIDE})
    assert [(one.folder, one.there) for one in found.folders] == [
        (unplugged, False),
        (pyrenees_dir, True),
    ]
    assert found.front == {COURCHEVEL: pyrenees}  # BESIDE: no folder there has it, Live's
    assert found.meshes == [pyrenees]


def test_the_one_folder_of_before_becomes_a_list_of_one(tmp_path: Path, monkeypatch) -> None:
    """A file from before several folders: its one folder is the list's only line, written as a
    list at the next save; an empty one gives none; a list already there wins."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    settings_file().parent.mkdir(parents=True, exist_ok=True)
    settings_file().write_text('[stream]\ndisk_gb = 40\nother_mesh = "/Volumes/Alps"\n')
    assert load_settings().other_meshes == ["/Volumes/Alps"] and load_settings().disk_gb == 40
    save_settings(load_settings())
    table = tomllib.loads(settings_file().read_text())["stream"]
    assert table["other_meshes"] == ["/Volumes/Alps"] and "other_mesh" not in table
    settings_file().write_text('[stream]\nother_mesh = " "\n')
    assert load_settings().other_meshes == []
    settings_file().write_text('[stream]\nother_mesh = "/Old"\nother_meshes = ["/New"]\n')
    assert load_settings().other_meshes == ["/New"]


def test_each_folder_once(tmp_path: Path, monkeypatch) -> None:
    """A folder added at the bottom; the same one again refused, typed with « ~ », with a slash
    more, or through a link; nothing given refused; the list each folder once whatever is
    written in the file."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    alps, pyrenees = tmp_path / "Alps", tmp_path / "Pyrenees"
    alps.mkdir()
    folder_link(alps, tmp_path / "Alps link")
    assert add_other_mesh(f" {alps} ") == [str(alps)]
    assert add_other_mesh(str(pyrenees)) == [str(alps), str(pyrenees)]  # not there: listed
    for again in ("~/Alps", f"{alps}/", str(tmp_path / "Alps link")):
        with pytest.raises(AlreadyListedError):
            add_other_mesh(again)
    with pytest.raises(ValueError):
        add_other_mesh("  ")
    assert load_settings().other_meshes == [str(alps), str(pyrenees)]
    twice = LiveSettings(other_meshes=[str(alps), "", "~/Alps", str(pyrenees)])
    assert twice.other_meshes == [str(alps), str(pyrenees)]


def test_a_folder_moved_up_or_down(tmp_path: Path, monkeypatch) -> None:
    """↑ and ↓ move a folder one line, nothing at an end; a folder not in the list refused."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    save_settings(LiveSettings(other_meshes=["/A", "/B", "/C"]))
    assert move_other_mesh("/C", up=True) == ["/A", "/C", "/B"]
    assert move_other_mesh("/A", up=True) == ["/A", "/C", "/B"]
    assert move_other_mesh("/B", up=False) == ["/A", "/C", "/B"]
    assert move_other_mesh("/A", up=False) == ["/C", "/A", "/B"]
    with pytest.raises(ValueError):
        move_other_mesh("/D", up=True)


def test_a_folder_removed_takes_out_the_squares_no_folder_left_has(
    tmp_path: Path, monkeypatch
) -> None:
    """Remove forgets a folder, its files left, and drops from the squares in front those no folder
    left has; one left whose disk is unplugged keeps them all, Live cannot tell which it has."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    alps_dir, pyrenees_dir = tmp_path / "Alps", tmp_path / "Pyrenees"
    mesh(alps_dir / "z_ao_alps", squares=(COURCHEVEL, BESIDE))
    mesh(pyrenees_dir / "z_ao_pyr", squares=(COURCHEVEL,))
    front = ["+42+000", "+45+006", "+46+002"]
    both = LiveSettings(other_meshes=[str(alps_dir), str(pyrenees_dir)], other_mesh_front=front)
    save_settings(both)
    saved = remove_other_mesh(str(alps_dir), scenery.folder_squares)
    assert saved.other_meshes == [str(pyrenees_dir)] and saved.other_mesh_front == ["+46+002"]
    assert (alps_dir / "z_ao_alps").is_dir()
    save_settings(both)
    unplugged = both.model_copy(update={"other_meshes": [str(alps_dir), str(tmp_path / "Gone")]})
    save_settings(unplugged)
    saved = remove_other_mesh(str(alps_dir), scenery.folder_squares)
    assert saved.other_mesh_front == front


def test_the_page_s_saves_wait_for_each_other(tmp_path: Path, monkeypatch) -> None:
    """A folder added while another save holds the settings waits for it, then reads what it saved:
    both kept."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    save_settings(LiveSettings(disk_gb=60))
    with SAVING:
        adding = threading.Thread(target=add_other_mesh, args=("/Alps",))
        adding.start()
        time.sleep(0.2)
        assert adding.is_alive()  # waits for the save under way
        save_settings(load_settings().model_copy(update={"other_mesh_front": ["+46+002"]}))
    adding.join(5)
    saved = load_settings()
    assert saved.other_meshes == ["/Alps"] and saved.other_mesh_front == ["+46+002"]
    assert saved.disk_gb == 60


@pytest.mark.anyio
async def test_a_save_of_the_settings_keeps_the_folders_and_the_squares(
    tmp_path: Path, monkeypatch
) -> None:
    """The page's Save sends neither list: the folders and the squares in front stay as saved,
    whatever a folder holds now (one that read empty, a disk's empty mount point, once wiped the
    squares). A folder whose every square Live has, one square in front: the next set-up takes the
    folder's DSF there."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    live, alps = live_and_alps(tmp_path)
    empty = tmp_path / "mount"
    empty.mkdir()
    chosen = [str(empty), str(alps.parent)]
    save_settings(LiveSettings(other_meshes=chosen, other_mesh_front=["+46+002"]))
    control_ = control.LiveControl(
        checks=lambda: {"xplane": None}, echo=quiet, catalog=lambda r: {}
    )
    async with client_for(create_live_app(control_)) as client:
        got = await client.put("/api/live/settings", json={"disk_gb": 60})
        assert got.status_code == 200
    saved = load_settings()
    assert saved.other_meshes == chosen and saved.other_mesh_front == ["+46+002"]
    found = other_meshes([alps.parent], [live], scenery.other_mesh_front(saved))
    assert other_mesh(alps.parent, [live]).refused == [("z_ao_test", "already")]
    assert found.meshes == [alps] and found.front == {COURCHEVEL: alps}
    pack = tmp_path / "pack"
    setup_region([live, *found.meshes], pack, log=quiet, front=found.front)
    assert (pack / DSF).read_bytes() == b"XPLNEDSF the Alps' own elevation"


def test_a_click_changes_its_square_alone(tmp_path: Path, monkeypatch) -> None:
    """Each click puts its square in front or back in the setting as saved now: two clicks one
    after the other keep both, the same one twice changes nothing more, the other settings stay;
    a name not a square's refused, nothing saved."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    save_settings(LiveSettings(disk_gb=60, other_meshes=["/Alps"]))
    assert put_in_front(["+46+002"], True) == ["+46+002"]
    assert put_in_front(["+45+006"], True) == ["+45+006", "+46+002"]
    assert put_in_front(["+45+006"], True) == ["+45+006", "+46+002"]
    assert put_in_front(["+46+002"], False) == ["+45+006"]
    with pytest.raises(ValueError):
        put_in_front(["Courchevel"], True)
    saved = load_settings()
    assert saved.other_mesh_front == ["+45+006"] and saved.disk_gb == 60


def test_the_folder_s_packs_checked_above_live_in_x_plane(tmp_path: Path) -> None:
    """Any pack of the folder's ground checked in X-Plane's list above Live's line, a copy as the
    first, whatever its line's name (a link to it): X-Plane draws it there itself, without photos.
    Below Live's line, unchecked, or another pack of that name elsewhere: nothing to say."""
    live = f"SCENERY_PACK Custom Scenery/{LINK_NAME}/"
    folder = tmp_path / "Alps"
    alps = mesh(folder / "z_ao_alps", squares=(COURCHEVEL,))
    mesh(folder / "z_ao_alps_copy", squares=(COURCHEVEL,))
    packs = other_mesh(folder).packs
    for name, lines, said in [
        ("a", ["SCENERY_PACK Custom Scenery/z_ao_alps/", live], ["z_ao_alps"]),
        ("b", [live, "SCENERY_PACK Custom Scenery/z_ao_alps/"], []),
        ("c", ["SCENERY_DISABLED Custom Scenery/z_ao_alps/", live], []),
        ("d", ["SCENERY_PACK Custom Scenery/My Alps/", live], ["My Alps"]),
        ("e", [f"SCENERY_PACK {folder / 'z_ao_alps_copy'}/", live], ["z_ao_alps_copy"]),
    ]:
        xp = xplane(tmp_path / name, lines)
        folder_link(alps, xp / "Custom Scenery" / "z_ao_alps")
        folder_link(alps, xp / "Custom Scenery" / "My Alps")
        assert listed_above_live(xp, packs, LINK_NAME) == said, name
    namesake = mesh(tmp_path / "elsewhere" / "z_ao_alps", squares=(COURCHEVEL,))
    assert listed_above_live(tmp_path / "a" / "X-Plane 12", [namesake], LINK_NAME) == []


def test_what_the_window_says_and_draws(tmp_path: Path, monkeypatch) -> None:
    """A line under the setting for each folder: how many squares it gives the map, those it gives
    in front, whether they wait for Live's next start, its pack checked above Live in X-Plane; the
    map: every square of the folders, those in front told apart, each with the folder that gives
    it."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    folder = tmp_path / "X-Plane 12" / "Custom Scenery"
    xp = xplane(
        tmp_path,
        ["SCENERY_PACK Custom Scenery/z_ao_alps/", f"SCENERY_PACK Custom Scenery/{LINK_NAME}/"],
    )
    mesh(folder / "z_ao_alps", squares=(COURCHEVEL, BESIDE))
    pyrenees = tmp_path / "Pyrenees"
    mesh(pyrenees / "z_ao_pyr", squares=(COURCHEVEL, PYRENEES))
    chosen = [str(folder), str(pyrenees)]
    save_settings(LiveSettings(other_meshes=chosen, other_mesh_front=["+45+006", "+46+002"]))
    said = control._other_meshes_said(xp)
    assert said is not None and said["front_next"]  # no start has taken them yet
    first, second = said["folders"]
    assert first["name"] == "Custom Scenery" and first["squares"] == 2
    assert first["front"] == ["+45+006", "+46+002"] and first["in_xplane"] == ["z_ao_alps"]
    assert second["name"] == "Pyrenees" and second["squares"] == 2 and second["front"] == []
    live = control.LiveControl(checks=lambda: {"xplane": str(xp)}, echo=quiet)
    assert live.other_squares() == [
        {"lat": 42, "lon": 0, "front": False, "folder": "Pyrenees"},
        {"lat": 45, "lon": 6, "front": True, "folder": "Custom Scenery"},
        {"lat": 46, "lon": 2, "front": True, "folder": "Custom Scenery"},
    ]


def test_no_start_on_a_new_front_while_x_plane_runs(tmp_path: Path, monkeypatch) -> None:
    """A square put in front while X-Plane runs: the start refused, saying why; X-Plane closed,
    the pack set up with it."""
    from orthostudio.live import osxpregions, winsearch

    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    xp = make_xplane(tmp_path)
    live, alps = live_and_alps(tmp_path)
    save_settings(LiveSettings(other_meshes=[str(alps.parent)], other_mesh_front=["+46+002"]))
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
    with pytest.raises(session.RefusedError, match="squares in front of Live or their mesh"):
        session.prepare(options, log=quiet)
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    session.prepare(options, log=quiet)
    assert front_of_pack(pack) == {COURCHEVEL: alps}
    assert (pack / DSF).read_bytes() == b"XPLNEDSF the Alps' own elevation"


@pytest.mark.anyio
async def test_the_map_s_click_saves_the_square(tmp_path: Path, monkeypatch) -> None:
    """The map's click sends its square alone; the map then draws it in front."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    folder = tmp_path / "Alps"
    mesh(folder / "z_ao_alps", squares=(COURCHEVEL,))
    save_settings(LiveSettings(other_meshes=[str(folder)]))
    live = control.LiveControl(checks=lambda: {"xplane": None}, echo=quiet, catalog=lambda r: {})
    async with client_for(create_live_app(live)) as client:
        click = {"square": "+46+002", "front": True}
        got = await client.post("/api/live/map/other", json=click)
        assert got.status_code == 200
        assert got.json() == {"other_meshes": [str(folder)], "other_mesh_front": ["+46+002"]}
        drawn = (await client.get("/api/live/map/other")).json()
        assert drawn == [{"lat": 46, "lon": 2, "front": True, "folder": "Alps"}]
        wrong = await client.post("/api/live/map/other", json={"square": "Alps", "front": True})
        assert wrong.status_code == 422


@pytest.mark.anyio
async def test_the_buttons_of_each_folder(tmp_path: Path, monkeypatch) -> None:
    """Each button goes through the engine, one place each: a folder added at the bottom (the same
    again refused), every square of it put in front or back, moved, removed; a folder not there
    has no square to put in front."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    alps, pyrenees = tmp_path / "Alps", tmp_path / "Pyrenees"
    mesh(alps / "z_ao_alps", squares=(COURCHEVEL, BESIDE))
    mesh(pyrenees / "z_ao_pyr", squares=(PYRENEES,))
    live = control.LiveControl(checks=lambda: {"xplane": None}, echo=quiet, catalog=lambda r: {})
    async with client_for(create_live_app(live)) as client:

        async def press(path: str, **body) -> dict:
            got = await client.post(f"/api/live/other/{path}", json=body)
            assert got.status_code == 200, got.text
            return got.json()

        assert (await press("add", folder=str(alps)))["other_meshes"] == [str(alps)]
        again = await client.post("/api/live/other/add", json={"folder": f"{alps}/"})
        assert again.status_code == 409
        await press("add", folder=str(pyrenees))
        got = await press("all", folder=str(alps), front=True)
        assert got["other_mesh_front"] == ["+45+006", "+46+002"]
        got = await press("all", folder=str(pyrenees), front=True)
        assert got["other_mesh_front"] == ["+42+000", "+45+006", "+46+002"]
        got = await press("all", folder=str(alps), front=False)
        assert got["other_mesh_front"] == ["+42+000"]
        got = await press("move", folder=str(pyrenees), up=True)
        assert got["other_meshes"] == [str(pyrenees), str(alps)]
        got = await press("remove", folder=str(pyrenees))
        assert got == {"other_meshes": [str(alps)], "other_mesh_front": []}
        await press("add", folder=str(tmp_path / "Gone"))
        gone = await client.post(
            "/api/live/other/all", json={"folder": str(tmp_path / "Gone"), "front": True}
        )
        assert gone.status_code == 409
        missing = await client.post("/api/live/other/move", json={"folder": "/Nowhere", "up": True})
        assert missing.status_code == 422


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_line_of_each_folder_in_both_languages() -> None:
    """The line of a folder in the list: its squares and those it gives in front of Live, then
    what it takes there; its squares all behind Live's mesh said plainly, not as a warning; a
    folder not there, or giving no mesh, a warning."""
    there = {"there": True, "next_start": False, "overlays": [], "refused": [], "in_xplane": []}
    folders = [
        {**there, "meshes": ["z_ao_alps"], "squares": 4, "front": ["+45+006"]},
        {
            **there,
            "meshes": [],
            "squares": 1,
            "front": [],
            "refused": [{"name": "z_ao_pyr", "why": "already"}],
        },
        {**there, "there": False, "meshes": [], "squares": 0, "front": []},
    ]
    script = (
        f"import {{ otherMeshSaid }} from {json.dumps((UI_DIR / 'live.js').as_uri())};"
        f"import {{ setLanguage }} from {json.dumps((UI_DIR / 'strings.js').as_uri())};"
        f"const folders = {json.dumps(folders)}; const out = {{}};"
        "for (const lang of ['en', 'fr']) {"
        " setLanguage(lang); out[lang] = folders.map(otherMeshSaid); }"
        "console.log(JSON.stringify(out));"
    )
    run = subprocess.run(
        ["node", "--input-type=module", "-e", script], capture_output=True, encoding="utf-8"
    )
    assert run.returncode == 0, run.stderr
    said = json.loads(run.stdout)
    assert said["en"] == [
        [
            "4 squares · 1 in front of Live. Used: z_ao_alps. Live takes no roads, forests or "
            "buildings from this folder.",
            False,
        ],
        ["1 square · 0 in front of Live. Its squares are behind Live's mesh.", False],
        ["This folder is not there: is its disk unplugged?", True],
    ]
    assert said["fr"][0][0].startswith("4 carrés · 1 devant Live. Pris par Live : z_ao_alps.")
    assert said["fr"][1][0] == "1 carré · 0 devant Live. Ses carrés sont derrière le mesh de Live."
