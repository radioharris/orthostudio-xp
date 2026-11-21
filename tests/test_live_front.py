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
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.test_live_other_mesh import mesh
from tests.test_live_overlays import xplane
from tests.test_live_regions import _another_disk

from live_helpers import NAMES, make_source, make_xplane, save_settings
from orthostudio.config.store import toml_dumps
from orthostudio.live import api as api_module
from orthostudio.live import control, scenery, session, settings_file
from orthostudio.live import settings as settings_module
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
    NOTHING_READ,
    Meshes,
    default_meshes,
    other_mesh,
    other_meshes,
)
from orthostudio.live.settings import (
    AlreadyListedError,
    LiveSettings,
    add_other_mesh,
    flip_front,
    load_settings,
    move_other_mesh,
    put_in_front,
    remove_other_mesh,
    saved_with,
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
    monkeypatch.setattr(scenery, "_found", (0.0, (), NOTHING_READ))


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
    that name alone left out; kept in live.toml as a list."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    chosen = LiveSettings(other_mesh_front=["+45+007", " +45+006", "+45+006", "-01-001"])
    assert chosen.other_mesh_front == ["-01-001", "+45+006", "+45+007"]
    for wrong in ("45+006", "+45+6", "+90+000", "+45+180", "Courchevel"):
        with pytest.raises(ValidationError):
            LiveSettings(other_mesh_front=[wrong])
    save_settings(LiveSettings(disk_gb=40, other_mesh_front=["+45+006"]))
    assert tomllib.loads(settings_file().read_text())["stream"]["other_mesh_front"] == ["+45+006"]
    assert load_settings().other_mesh_front == ["+45+006"]
    settings_file().write_text('[stream]\ndisk_gb = 40\nother_mesh_front = ["Alps", "+45+006"]\n')
    assert load_settings().other_mesh_front == ["+45+006"] and load_settings().disk_gb == 40


def test_a_wrong_line_of_a_list_costs_itself(tmp_path: Path, monkeypatch) -> None:
    """A list of the file read line by line, a wrong one left out, the others kept; a list of
    folders that is not a list leaves the one folder of before in its place, so that the next
    save keeps it."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    settings_file().write_text('[stream]\nother_meshes = ["/Alps", 3, "/Pyrenees"]\n')
    assert load_settings().other_meshes == ["/Alps", "/Pyrenees"]
    settings_file().write_text('[stream]\nother_meshes = "/Alps"\nother_mesh = "/Alps"\n')
    assert load_settings().other_meshes == ["/Alps"]
    add_other_mesh("/Pyrenees")
    assert tomllib.loads(settings_file().read_text())["stream"]["other_meshes"] == [
        "/Alps",
        "/Pyrenees",
    ]


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


def test_a_mesh_coming_with_the_front_is_said_as_the_front(tmp_path: Path) -> None:
    """A folder's first square put in front brings its mesh in the middle of the regions, its last
    put back takes it out: the start refused says the squares in front, not a disk to connect;
    that mesh gone from the disk, the meshes changed, a disk to connect."""
    live, alps = live_and_alps(tmp_path)
    pyrenees = make_source(tmp_path / "pyrenees", tile=PYRENEES, textures=NAMES)
    pack = tmp_path / "pack"
    setup_region([live, pyrenees], pack, log=quiet)
    assert pack_to_redo([live, alps, pyrenees], pack, {COURCHEVEL: alps}) == "front"
    setup_region([live, alps, pyrenees], pack, log=quiet, front={COURCHEVEL: alps})
    assert pack_to_redo([live, pyrenees], pack, {}) == "front"
    alps.rename(tmp_path / "unplugged")
    assert pack_to_redo([live, pyrenees], pack, {}) == "regions"


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


def as_an_app_of_before(pack: Path, regions: list[Path]) -> None:
    """The pack set up again by an app of before, which knows nothing of the squares in front:
    its regions' DSFs everywhere, the record of the front kept as it was."""
    marker = json.loads((pack / REGION_MARKER).read_text())
    setup_region(regions, pack, log=quiet, force=True)
    meta = json.loads((pack / REGION_MARKER).read_text())
    (pack / REGION_MARKER).write_text(json.dumps({**meta, "front": marker["front"]}))


@pytest.mark.parametrize("theirs", ["added", "same"])
def test_a_pack_an_app_of_before_set_up_again(tmp_path: Path, theirs: str) -> None:
    """After every set-up the pack holds the DSFs of the squares in front: one an app of before
    set up again, the record of the front kept and not the DSFs, is set up again, as for a front
    changed, whether the other mesh comes as a region added after those of before or as one of
    them, so that the DSF and the files Live makes for it agree."""
    live, alps = live_and_alps(tmp_path)
    pack = tmp_path / "pack"
    front = {COURCHEVEL: alps}
    setup_region([live, alps], pack, log=quiet, front=front)
    as_an_app_of_before(pack, [live] if theirs == "added" else [live, alps])
    assert (pack / DSF).read_bytes() == (live / DSF).read_bytes()
    assert pack_to_redo([live, alps], pack, front) == "front"
    assert not session.region_ready(pack, Meshes([live, alps], front))
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
    assert behind.ground == {COURCHEVEL: alps}
    assert other_meshes([folder], [live], ()).front == {}
    ahead = other_mesh(folder, [live], front={COURCHEVEL, (10, 10)})
    assert ahead.meshes == [alps] and ahead.refused == []
    assert other_meshes([folder], [live], {COURCHEVEL, (10, 10)}).front == {COURCHEVEL: alps}
    copy = mesh(folder / "z_ao_alps_copy", squares=(COURCHEVEL,))
    with_copy = other_mesh(folder, [live], front={COURCHEVEL})
    assert with_copy.refused == [("z_ao_alps_copy", "already")] and with_copy.packs == [alps, copy]
    assert other_meshes([folder], [live], {COURCHEVEL}).front == {COURCHEVEL: alps}


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
    assert table["other_meshes"] == ["/Volumes/Alps"]
    settings_file().write_text('[stream]\nother_mesh = " "\n')
    assert load_settings().other_meshes == []
    settings_file().write_text('[stream]\nother_mesh = "/Old"\nother_meshes = ["/New"]\n')
    assert load_settings().other_meshes == ["/New"]


def test_an_app_of_before_keeps_the_first_folder(tmp_path: Path, monkeypatch) -> None:
    """Each save writes the list's first folder as the one folder of before too, none: empty. An
    app of before reads it alone and writes its own table back without the list: the first folder
    is kept, read again as a list of one."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    save_settings(LiveSettings(disk_gb=40, other_meshes=["/Alps", "/Pyrenees"]))
    table = tomllib.loads(settings_file().read_text())["stream"]
    assert table["other_mesh"] == "/Alps"
    old_app = {k: v for k, v in table.items() if k not in ("other_meshes", "other_mesh_front")}
    settings_file().write_text(toml_dumps({"stream": old_app}))  # its first save
    assert load_settings().other_meshes == ["/Alps"] and load_settings().disk_gb == 40
    save_settings(LiveSettings())
    assert tomllib.loads(settings_file().read_text())["stream"]["other_mesh"] == ""


def test_each_folder_once(tmp_path: Path, monkeypatch) -> None:
    """A folder added at the bottom; the same one again refused, typed with « ~ », with a slash
    more, through a link, or in other capitals where the disk ignores them (a Mac's); nothing
    given refused. The list each folder once as written whatever the file holds, nothing asked of
    the disk at each reading of the settings."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    alps, pyrenees = tmp_path / "Alps", tmp_path / "Pyrenees"
    alps.mkdir()
    folder_link(alps, tmp_path / "Alps link")
    assert add_other_mesh(f" {alps} ").other_meshes == [str(alps)]
    added = add_other_mesh(str(pyrenees)).other_meshes  # not there: listed
    assert added == [str(alps), str(pyrenees)]
    again = ["~/Alps", f"{alps}/", str(tmp_path / "Alps link")]
    if (tmp_path / "ALPS").is_dir():  # a disk that ignores capitals, as a Mac's by default
        again.append(str(tmp_path / "ALPS"))
    for one in again:
        with pytest.raises(AlreadyListedError):
            add_other_mesh(one)
    with pytest.raises(ValueError):
        add_other_mesh("  ")
    assert load_settings().other_meshes == [str(alps), str(pyrenees)]
    twice = LiveSettings(other_meshes=[str(alps), "", "~/Alps", f"{alps}/", str(pyrenees)])
    assert twice.other_meshes == [str(alps), str(pyrenees)]

    def no_disk(path):
        raise AssertionError(f"the disk asked for {path}")

    monkeypatch.setattr(os.path, "realpath", no_disk)
    monkeypatch.setattr(os.path, "samefile", no_disk)
    assert load_settings().other_meshes == [str(alps), str(pyrenees)]


def test_a_folder_moved_up_or_down(tmp_path: Path, monkeypatch) -> None:
    """↑ and ↓ move a folder one line, nothing at an end; a folder not in the list refused."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    save_settings(LiveSettings(other_meshes=["/A", "/B", "/C"]))
    assert move_other_mesh("/C", up=True).other_meshes == ["/A", "/C", "/B"]
    assert move_other_mesh("/A", up=True).other_meshes == ["/A", "/C", "/B"]
    assert move_other_mesh("/B", up=False).other_meshes == ["/A", "/C", "/B"]
    assert move_other_mesh("/A", up=False).other_meshes == ["/C", "/A", "/B"]
    with pytest.raises(ValueError):
        move_other_mesh("/D", up=True)


def test_a_folder_removed_is_forgotten_alone(tmp_path: Path, monkeypatch) -> None:
    """Remove forgets a folder, its files left as they are; the squares in front stay as they are,
    those no folder gives now included: they are not taken."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    alps = tmp_path / "Alps"
    mesh(alps / "z_ao_alps", squares=(COURCHEVEL,))
    front = ["+42+000", "+45+006", "+46+002"]
    save_settings(LiveSettings(other_meshes=[str(alps), "/Pyrenees"], other_mesh_front=front))
    saved = remove_other_mesh(str(alps))
    assert saved.other_meshes == ["/Pyrenees"] and saved.other_mesh_front == front
    assert (alps / "z_ao_alps").is_dir()
    assert other_meshes([Path("/Pyrenees")], [], scenery.other_mesh_front(saved)).front == {}


class WatchedLock:
    """The settings' lock, saying when a save waits for it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.waiting = threading.Event()

    def locked(self) -> bool:
        return self.lock.locked()

    def __enter__(self) -> WatchedLock:
        if not self.lock.acquire(blocking=False):
            self.waiting.set()
            self.lock.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.lock.release()


def test_the_page_s_saves_wait_for_each_other(tmp_path: Path, monkeypatch) -> None:
    """A folder added while another save holds the settings waits for it, then reads what it saved:
    both kept."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    save_settings(LiveSettings(disk_gb=60))
    lock = WatchedLock()
    monkeypatch.setattr(settings_module, "SAVING", lock)
    holding, release = threading.Event(), threading.Event()

    def slow(now: LiveSettings) -> dict:
        holding.set()
        assert release.wait(5)
        return {"other_mesh_front": ["+46+002"]}

    first = threading.Thread(target=saved_with, args=(slow,))
    first.start()
    assert holding.wait(5)
    adding = threading.Thread(target=add_other_mesh, args=("/Alps",))
    adding.start()
    assert lock.waiting.wait(5) and adding.is_alive()  # waits for the save under way
    release.set()
    first.join(5)
    adding.join(5)
    saved = load_settings()
    assert saved.other_meshes == ["/Alps"] and saved.other_mesh_front == ["+46+002"]
    assert saved.disk_gb == 60


@pytest.mark.anyio
async def test_no_disk_is_asked_under_the_settings_lock(tmp_path: Path, monkeypatch) -> None:
    """A folder added is compared with those listed on the disk, and Live's folder of a Save
    checked on it, before the settings' lock, which every save waits for: a slow disk held them
    all. Under the lock a folder added meanwhile is told apart as written."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    asked: list[bool] = []
    samefile, problem = os.path.samefile, api_module.folder_problem

    def watched_samefile(a, b):
        asked.append(settings_module.SAVING.locked())
        return samefile(a, b)

    def watched_problem(folder, xplane):
        asked.append(settings_module.SAVING.locked())
        return problem(folder, xplane)

    monkeypatch.setattr(os.path, "samefile", watched_samefile)
    monkeypatch.setattr(api_module, "folder_problem", watched_problem)
    alps, pyrenees = tmp_path / "Alps", tmp_path / "Pyrenees"
    alps.mkdir()
    pyrenees.mkdir()
    add_other_mesh(str(alps))
    add_other_mesh(str(pyrenees))  # compared with the Alps on the disk
    live = control.LiveControl(checks=lambda: {"xplane": None}, echo=quiet, catalog=lambda r: {})
    async with client_for(create_live_app(live)) as client:
        got = await client.put("/api/live/settings", json={"folder": str(tmp_path / "Live")})
        assert got.status_code == 200
    assert asked == [False, False]


def test_a_click_reads_no_folder_again(tmp_path: Path, monkeypatch) -> None:
    """A square put in front or back, or a folder's squares all at once, takes the folders as last
    read: only the squares in front are applied anew, at once."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    folder = tmp_path / "Alps"
    mesh(folder / "z_ao_alps", squares=(COURCHEVEL,))
    save_settings(LiveSettings(other_meshes=[str(folder)]))
    read: list[Path] = []
    real = scenery.read_packs

    def counted(one: Path):
        read.append(one)
        return real(one)

    monkeypatch.setattr(scenery, "read_packs", counted)
    root = tmp_path / "live"
    assert scenery.other_mesh_found(None, root).front == {}
    flip_front("+46+002")
    assert scenery.other_mesh_found(None, root).front == {COURCHEVEL: folder / "z_ao_alps"}
    put_in_front(["+46+002"], False)
    assert scenery.other_mesh_found(None, root).front == {}
    assert read == [folder]


@pytest.mark.anyio
async def test_the_language_unchanged_leaves_the_file_as_it_is(tmp_path: Path, monkeypatch) -> None:
    """The window's language sent again with live.toml not parsing answers as before, the file
    left as it is; a new language is saved."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    settings_file().parent.mkdir(parents=True)
    settings_file().write_text("[stream\n")
    live = control.LiveControl(checks=lambda: {"xplane": None}, echo=quiet, catalog=lambda r: {})
    async with client_for(create_live_app(live)) as client:
        got = await client.post("/api/live/language", json={"language": "en"})
        assert got.status_code == 200 and got.json() == {"language": "en"}
        assert settings_file().read_text() == "[stream\n"
        settings_file().write_text("[stream]\ndisk_gb = 40\n")
        got = await client.post("/api/live/language", json={"language": "fr"})
        assert got.status_code == 200
    assert load_settings().language == "fr" and load_settings().disk_gb == 40


@pytest.mark.anyio
async def test_a_save_of_the_settings_keeps_the_folders_and_the_squares(
    tmp_path: Path, monkeypatch
) -> None:
    """The page's Save sends neither list: the folders and the squares in front stay as saved,
    whatever a folder holds now (one that read empty, a disk's empty mount point, once wiped the
    squares)."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    _, alps = live_and_alps(tmp_path)
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


def test_a_click_changes_its_square_alone(tmp_path: Path, monkeypatch) -> None:
    """Each click flips its square in the setting as saved now, in front where it was behind,
    back where it was in front: two clicks on two squares keep both, two on one put it back, the
    other settings stay; a folder's squares all at once, in front or back; a name not a square's
    refused, nothing saved."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    save_settings(LiveSettings(disk_gb=60, other_meshes=["/Alps"]))
    assert flip_front("+46+002").other_mesh_front == ["+46+002"]
    assert flip_front("+45+006").other_mesh_front == ["+45+006", "+46+002"]
    assert flip_front("+45+006").other_mesh_front == ["+46+002"]
    assert put_in_front(["+45+006", "+46+002"], True).other_mesh_front == ["+45+006", "+46+002"]
    assert put_in_front(["+46+002"], False).other_mesh_front == ["+45+006"]
    for wrong in (lambda: flip_front("Courchevel"), lambda: put_in_front(["Alps"], True)):
        with pytest.raises(ValueError):
            wrong()
    saved = load_settings()
    assert saved.other_mesh_front == ["+45+006"] and saved.disk_gb == 60


def test_the_folder_s_packs_checked_above_live_in_x_plane(tmp_path: Path) -> None:
    """Any pack of the folder's ground checked in X-Plane's list above Live's line, a copy as the
    first, whatever its line's name (a link to it, another case on a Mac's disk): X-Plane draws it
    there itself, without photos. Below Live's line, unchecked, or another pack of that name
    elsewhere: nothing to say."""
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
        # in other capitals, where the disk ignores them (a Mac's by default)
        ("f", [f"SCENERY_PACK {folder / 'Z_AO_ALPS'}/", live], ["Z_AO_ALPS"]),
    ]:
        if name == "f" and not (folder / "Z_AO_ALPS").is_dir():
            continue
        xp = xplane(tmp_path / name, lines)
        folder_link(alps, xp / "Custom Scenery" / "z_ao_alps")
        folder_link(alps, xp / "Custom Scenery" / "My Alps")
        assert listed_above_live(xp, [packs, []], LINK_NAME) == [said, []], name
    namesake = mesh(tmp_path / "elsewhere" / "z_ao_alps", squares=(COURCHEVEL,))
    assert listed_above_live(tmp_path / "a" / "X-Plane 12", [[namesake]], LINK_NAME) == [[]]


def test_what_the_window_says_and_draws(tmp_path: Path, monkeypatch) -> None:
    """A line under the setting for each folder: how many squares it gives, not those a folder
    above it has, those of them in front, whether they wait for Live's next start, its pack
    checked above Live in X-Plane; the map: every square of the folders, those in front told
    apart, each with the folder that gives it."""
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
    assert second["name"] == "Pyrenees" and second["squares"] == 1 and second["front"] == []
    live = control.LiveControl(checks=lambda: {"xplane": str(xp)}, echo=quiet)
    assert live.other_squares() == [
        {"lat": 42, "lon": 0, "front": False, "folder": "Pyrenees"},
        {"lat": 45, "lon": 6, "front": True, "folder": "Custom Scenery"},
        {"lat": 46, "lon": 2, "front": True, "folder": "Custom Scenery"},
    ]


@pytest.mark.parametrize("why", ["new", "app of before"])
def test_no_start_on_a_new_front_while_x_plane_runs(tmp_path: Path, monkeypatch, why: str) -> None:
    """A square put in front while X-Plane runs, or the pack an app of before set up again without
    its DSF: the start refused, saying why, no DSF written; X-Plane closed, the pack set up with
    it."""
    from orthostudio.live import osxpregions, winsearch

    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    xp = make_xplane(tmp_path)
    live, alps = live_and_alps(tmp_path)
    save_settings(LiveSettings(other_meshes=[str(alps.parent)], other_mesh_front=["+46+002"]))
    pack = tmp_path / "pack"
    if why == "new":
        setup_region([live, alps], pack, log=quiet)
    else:
        setup_region([live, alps], pack, log=quiet, front={COURCHEVEL: alps})
        as_an_app_of_before(pack, [live, alps])
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
    assert (pack / DSF).read_bytes() == (live / DSF).read_bytes()  # nothing written
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    session.prepare(options, log=quiet)
    assert front_of_pack(pack) == {COURCHEVEL: alps}
    assert (pack / DSF).read_bytes() == b"XPLNEDSF the Alps' own elevation"


@pytest.mark.anyio
async def test_the_map_s_click_saves_the_square(tmp_path: Path, monkeypatch) -> None:
    """The map's click sends its square alone, and the engine flips it as saved now: the map then
    draws it in front; two quick clicks, both sent before the page drew the first, put it back."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    folder = tmp_path / "Alps"
    mesh(folder / "z_ao_alps", squares=(COURCHEVEL,))
    save_settings(LiveSettings(other_meshes=[str(folder)]))
    live = control.LiveControl(checks=lambda: {"xplane": None}, echo=quiet, catalog=lambda r: {})
    async with client_for(create_live_app(live)) as client:
        got = await client.post("/api/live/map/other", json={"square": "+46+002"})
        assert got.status_code == 200
        assert got.json() == {"other_meshes": [str(folder)], "other_mesh_front": ["+46+002"]}
        drawn = (await client.get("/api/live/map/other")).json()
        assert drawn == [{"lat": 46, "lon": 2, "front": True, "folder": "Alps"}]
        for _ in range(2):
            got = await client.post("/api/live/map/other", json={"square": "+45+006"})
        assert got.json()["other_mesh_front"] == ["+46+002"]
        wrong = await client.post("/api/live/map/other", json={"square": "Alps"})
        assert wrong.status_code == 422


@pytest.mark.anyio
async def test_the_buttons_of_each_folder(tmp_path: Path, monkeypatch) -> None:
    """Each button goes through the engine, one place each, answering the list and the squares in
    front as saved, or why it refused."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    alps, gone = tmp_path / "Alps", tmp_path / "Gone"
    mesh(alps / "z_ao_alps", squares=(COURCHEVEL,))
    live = control.LiveControl(checks=lambda: {"xplane": None}, echo=quiet, catalog=lambda r: {})
    async with client_for(create_live_app(live)) as client:

        async def press(path: str, **body) -> int:
            return (await client.post(f"/api/live/other/{path}", json=body)).status_code

        assert await press("add", folder=str(alps)) == 200
        assert await press("add", folder=f"{alps}/") == 409  # listed already
        assert await press("add", folder=str(gone)) == 200
        assert await press("all", folder=str(alps), front=True) == 200
        assert await press("all", folder=str(gone), front=True) == 409  # not there
        assert await press("move", folder=str(gone), up=True) == 200
        assert await press("move", folder="/Nowhere", up=True) == 422  # not listed
        assert await press("remove", folder=str(gone)) == 200
    saved = load_settings()
    assert saved.other_meshes == [str(alps)] and saved.other_mesh_front == ["+46+002"]


@pytest.mark.anyio
async def test_all_in_front_and_behind_change_the_squares_the_folder_gives(
    tmp_path: Path, monkeypatch
) -> None:
    """« All in front » and « All behind » change the squares their folder gives alone: one a
    folder higher in the list gives stays as it is, in front of Live here."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    alps, pyrenees = tmp_path / "Alps", tmp_path / "Pyrenees"
    mesh(alps / "z_ao_alps", squares=(COURCHEVEL,))
    mesh(pyrenees / "z_ao_pyr", squares=(COURCHEVEL, PYRENEES))
    chosen = [str(alps), str(pyrenees)]
    save_settings(LiveSettings(other_meshes=chosen, other_mesh_front=["+46+002"]))
    live = control.LiveControl(checks=lambda: {"xplane": None}, echo=quiet, catalog=lambda r: {})
    async with client_for(create_live_app(live)) as client:
        for front, after in ((True, ["+42+000", "+46+002"]), (False, ["+46+002"])):
            body = {"folder": str(pyrenees), "front": front}
            got = await client.post("/api/live/other/all", json=body)
            assert got.status_code == 200 and got.json()["other_mesh_front"] == after


def test_why_a_folder_gives_no_mesh(tmp_path: Path) -> None:
    """Each folder's line says, from the engine alone, how many squares it gives and why Live
    takes none of its meshes: a folder whose squares a folder above has gives none, even with one
    of them in front of Live; one whose squares Live's mesh has, none in front, has them behind
    Live's; none of its ground naming photos; not there. One square in front: its mesh taken."""
    live = mesh(tmp_path / "live" / "z_osxp_eur", squares=(COURCHEVEL, BESIDE))
    alps_dir, inner_dir = tmp_path / "Alps", tmp_path / "Inner Alps"
    mesh(alps_dir / "z_ao_alps", squares=(COURCHEVEL, BESIDE))
    mesh(inner_dir / "z_ao_inner", squares=(COURCHEVEL,))
    empty, gone = tmp_path / "Empty", tmp_path / "Gone"
    empty.mkdir()
    folders = [alps_dir, inner_dir, empty, gone]

    def said(front) -> list[tuple]:
        every = other_meshes(folders, [live], front)
        out = [scenery.other_mesh_said(one, every) for one in every.folders]
        return [(f["squares"], len(f["front"]), f["unused"]) for f in out]

    assert said(()) == [(2, 0, "behind"), (0, 0, "above"), (0, 0, "no_mesh"), (0, 0, "gone")]
    assert said({COURCHEVEL})[:2] == [(2, 1, None), (0, 0, "above")]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_line_of_each_folder_in_both_languages() -> None:
    """The line of a folder in the list, each fact the engine's: the squares it gives and those of
    them in front of Live, then what it takes there; its squares all behind Live's mesh, or all
    given by a folder above it, said plainly, not as a warning; a folder not there, or giving no
    mesh, a warning."""
    there = {"there": True, "next_start": False, "overlays": [], "refused": [], "in_xplane": []}
    already = [{"name": "z_ao_pyr", "why": "already"}]
    folders = [
        {**there, "meshes": ["z_ao_alps"], "squares": 4, "front": ["+45+006"], "unused": None},
        {**there, "meshes": [], "squares": 1, "front": [], "refused": already, "unused": "behind"},
        {**there, "meshes": [], "squares": 0, "front": [], "refused": already, "unused": "above"},
        {**there, "meshes": [], "squares": 0, "front": [], "unused": "no_mesh"},
        {**there, "there": False, "meshes": [], "squares": 0, "front": [], "unused": "gone"},
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
        ["This folder gives no square: a folder higher in the list has them all.", False],
        ["No mesh Live can use in this folder.", True],
        ["This folder is not there: is its disk unplugged?", True],
    ]
    assert said["fr"][0][0].startswith("4 carrés · 1 devant Live. Pris par Live : z_ao_alps.")
    assert said["fr"][1][0] == "1 carré · 0 devant Live. Ses carrés sont derrière le mesh de Live."
    assert said["fr"][2][0] == (
        "Ce dossier ne donne aucun carré : un dossier plus haut dans la liste les a tous."
    )
