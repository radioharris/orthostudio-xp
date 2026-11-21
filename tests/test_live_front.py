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

from live_helpers import NAMES, make_source, other_meshes, save_settings, under_x_plane
from orthostudio.config.store import toml_dumps
from orthostudio.live import api as api_module
from orthostudio.live import control, filesystem, scenery, session, settings_file
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
from orthostudio.live.scenery import NOTHING_READ, Meshes, default_meshes
from orthostudio.live.settings import (
    AlreadyListedError,
    LiveSettings,
    add_other_mesh,
    flip_front,
    load_settings,
    move_other_mesh,
    put_in_front,
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
    """Each square once, as X-Plane names its DSF; a wrong name refused; kept in live.toml as a
    list."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    chosen = LiveSettings(other_mesh_front=["+45+007", " +45+006", "+45+006", "-01-001"])
    assert chosen.other_mesh_front == ["-01-001", "+45+006", "+45+007"]
    for wrong in ("45+006", "+45+6", "+90+000", "+45+180", "Courchevel"):
        with pytest.raises(ValidationError):
            LiveSettings(other_mesh_front=[wrong])
    save_settings(LiveSettings(disk_gb=40, other_mesh_front=["+45+006"]))
    assert tomllib.loads(settings_file().read_text())["stream"]["other_mesh_front"] == ["+45+006"]
    assert load_settings().other_mesh_front == ["+45+006"]


def test_a_wrong_line_of_a_list_costs_itself(tmp_path: Path, monkeypatch) -> None:
    """A list of the file read line by line, a wrong one left out, the others and the other
    settings kept; a list of folders that is not a list leaves the one folder of before in its
    place, so that the next save keeps it."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    settings_file().write_text(
        '[stream]\ndisk_gb = 40\nother_meshes = ["/Alps", 3, "/Pyrenees"]\n'
        'other_mesh_front = ["Alps", "+45+006"]\n'
    )
    saved = load_settings()
    assert saved.other_meshes == ["/Alps", "/Pyrenees"] and saved.other_mesh_front == ["+45+006"]
    assert saved.disk_gb == 40
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
    another order: set up again. The one place this rule is tested."""
    live, alps = live_and_alps(tmp_path)
    pack = tmp_path / "pack"
    setup_region([live, alps], pack, log=quiet)
    assert (pack / DSF).read_bytes() == (live / DSF).read_bytes()  # behind Live: Live's
    lines: list[str] = []
    front = {COURCHEVEL: alps}
    assert pack_to_redo([live, alps], pack, front)
    assert not session.region_ready(pack, Meshes([live, alps], front))
    setup_region([live, alps], pack, log=lines.append, front=front)
    assert any("1 squares in front of Live" in line for line in lines)
    assert (pack / DSF).read_bytes() == b"XPLNEDSF the Alps' own elevation"
    other = pack / "Earth nav data" / "+40+000" / "+45+006.dsf"
    assert other.read_bytes() == (live / "Earth nav data" / "+40+000" / "+45+006.dsf").read_bytes()
    recorded = json.loads((pack / REGION_MARKER).read_text())["front"]
    assert recorded["+46+002"]["source"] == str(alps)
    assert front_of_pack(pack) == front
    assert not pack_to_redo([live, alps], pack, front)
    assert session.region_ready(pack, Meshes([live, alps], front))
    lines.clear()
    setup_region([live, alps], pack, log=lines.append, front=front)
    assert lines == []  # nothing to do
    assert pack_to_redo([alps, live], pack, front)  # a square's DSF another's
    assert pack_to_redo([live, alps], pack, {})  # back behind Live
    setup_region([live, alps], pack, log=quiet)
    assert (pack / DSF).read_bytes() == (live / DSF).read_bytes()
    assert front_of_pack(pack) == {}


def test_a_start_refused_says_a_disk_only_for_a_mesh_gone(tmp_path: Path, monkeypatch) -> None:
    """A start refused while X-Plane runs says to connect a disk if and only if a mesh the pack
    was last set up from is no longer on the disk: a folder's first square put in front, its mesh
    coming in the middle of the regions, or its last put back, its mesh going, says the meshes
    changed, as a Mesh switched between New and Old does; that mesh gone from the disk, a disk to
    connect."""
    live, alps = live_and_alps(tmp_path)
    pyrenees = make_source(tmp_path / "pyrenees", tile=PYRENEES, textures=NAMES)
    pack = tmp_path / "pack"
    setup_region([live, pyrenees], pack, log=quiet)
    options = under_x_plane(tmp_path, monkeypatch, pack)

    def refused(meshes: Meshes) -> str:
        monkeypatch.setattr(session, "default_meshes", lambda xp, other=None: meshes)
        with pytest.raises(session.RefusedError) as e:
            session.prepare(options, log=quiet)
        return str(e.value)

    said = (
        "the meshes Live streams onto changed since its last start, and X-Plane may be reading "
        "those of before. Quit X-Plane, then start again"
    )
    assert refused(Meshes([live, alps, pyrenees], {COURCHEVEL: alps})) == said
    setup_region([live, alps, pyrenees], pack, log=quiet, front={COURCHEVEL: alps})
    assert refused(Meshes([live, pyrenees], {})) == said
    alps.rename(tmp_path / "unplugged")
    assert refused(Meshes([live, pyrenees], {})) == said.replace(
        "Quit X-Plane,", "Quit X-Plane, or connect the disk holding a mesh,"
    )


def test_a_dsf_in_front_edited_again_is_taken(tmp_path: Path) -> None:
    """The pilot's DSF of a square in front saved again, its relief fixed once more: the next
    check sets the pack up again, which then holds the new one; its region's folders' times alone
    did not change."""
    live, alps = live_and_alps(tmp_path)
    pack = tmp_path / "pack"
    front = {COURCHEVEL: alps}
    setup_region([live, alps], pack, log=quiet, front=front)
    assert not pack_to_redo([live, alps], pack, front)
    edited = alps / DSF.with_suffix(".new")  # as an editor saves it: a new file in its place
    edited.write_bytes(b"XPLNEDSF the Alps' relief, fixed again")
    later = (alps / DSF).stat().st_mtime_ns + 5_000_000_000
    os.utime(edited, ns=(later, later))
    os.replace(edited, alps / DSF)
    assert pack_to_redo([live, alps], pack, front)
    setup_region([live, alps], pack, log=quiet, front=front)
    assert (pack / DSF).read_bytes() == b"XPLNEDSF the Alps' relief, fixed again"
    assert not pack_to_redo([live, alps], pack, front)


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
    assert pack_to_redo([live, alps], pack, front)
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
    behind = other_meshes([folder], [live])
    found = behind.folders[0].found
    assert found.meshes == [] and found.refused == [("z_ao_alps", "already")]
    assert found.ground == {COURCHEVEL: alps} and behind.front == {}
    ahead = other_meshes([folder], [live], {COURCHEVEL, (10, 10)})
    found = ahead.folders[0].found
    assert found.meshes == [alps] and found.refused == []
    assert ahead.front == {COURCHEVEL: alps}
    copy = mesh(folder / "z_ao_alps_copy", squares=(COURCHEVEL,))
    with_copy = other_meshes([folder], [live], {COURCHEVEL})
    found = with_copy.folders[0].found
    assert found.refused == [("z_ao_alps_copy", "already")] and found.packs == [alps, copy]
    assert with_copy.front == {COURCHEVEL: alps}


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

    behind = other_meshes([alps_dir, pyrenees_dir], [live])
    assert behind.meshes == [alps, pyrenees] and behind.front == {}
    assert [owner(behind, sq) for sq in (COURCHEVEL, PYRENEES, BESIDE)] == [alps, pyrenees, live]
    assert behind.ground == {COURCHEVEL: alps_dir, BESIDE: alps_dir, PYRENEES: pyrenees_dir}
    ahead = other_meshes([alps_dir, pyrenees_dir], [live], {COURCHEVEL, BESIDE, PYRENEES})
    assert ahead.front == {COURCHEVEL: alps, BESIDE: alps, PYRENEES: pyrenees}
    turned = other_meshes([pyrenees_dir, alps_dir], [live])
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
    monkeypatch.setattr(settings_module, "folder_identity", no_disk)
    assert load_settings().other_meshes == [str(alps), str(pyrenees)]


def test_a_disk_that_numbers_no_file_compares_real_paths(tmp_path: Path, monkeypatch) -> None:
    """Some network shares number no file (``st_ino`` 0): every folder there was the same one, the
    second refused as listed already, every pack of a folder matched with every line of X-Plane's
    list. There their real paths are compared, as the system compares names: a link is still the
    folder it leads to."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    alps, pyrenees, link = tmp_path / "Alps", tmp_path / "Pyrenees", tmp_path / "Alps link"
    alps.mkdir()
    pyrenees.mkdir()
    folder_link(alps, link)
    stat = os.stat

    def unnumbered(path, *args, **kwargs):
        st = stat(path, *args, **kwargs)
        return os.stat_result((st.st_mode, 0, *tuple(st)[2:]))

    monkeypatch.setattr(filesystem.os, "stat", unnumbered)
    assert os.stat(alps).st_ino == 0
    one = filesystem.folder_identity
    assert one(alps) != one(pyrenees) and one(alps) == one(link) and one(tmp_path / "No") is None
    add_other_mesh(str(alps))
    assert add_other_mesh(str(pyrenees)).other_meshes == [str(alps), str(pyrenees)]
    with pytest.raises(AlreadyListedError):
        add_other_mesh(str(link))


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


@pytest.mark.anyio
async def test_remove_puts_the_folder_s_squares_behind_first(tmp_path: Path, monkeypatch) -> None:
    """Remove is « All behind » on its folder, then the folder forgotten, in one save: the squares
    it gives go back behind Live; one a folder above it gives, and one no folder gives, stay as
    they are; a folder not there gives nothing, nothing put behind. Its squares those of the
    folders as last read, no folder read under the settings' lock; its files left as they are."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    alps, pyrenees, gone = tmp_path / "Alps", tmp_path / "Pyrenees", tmp_path / "Gone"
    mesh(alps / "z_ao_alps", squares=(COURCHEVEL,))
    mesh(pyrenees / "z_ao_pyr", squares=(COURCHEVEL, PYRENEES))
    front = ["+42+000", "+45+006", "+46+002"]  # the Pyrenees', no folder's, the Alps'
    chosen = [str(alps), str(gone), str(pyrenees)]
    save_settings(LiveSettings(other_meshes=chosen, other_mesh_front=front))
    locked: list[bool] = []
    real = scenery.read_packs

    def watched(folder: Path):
        locked.append(settings_module.SAVING.locked())
        return real(folder)

    monkeypatch.setattr(scenery, "read_packs", watched)
    live = control.LiveControl(checks=lambda: {"xplane": None}, echo=quiet, catalog=lambda r: {})
    async with client_for(create_live_app(live)) as client:

        async def remove(folder: Path) -> dict:
            got = await client.post("/api/live/other/remove", json={"folder": str(folder)})
            assert got.status_code == 200
            return got.json()

        kept = ["+45+006", "+46+002"]
        assert await remove(pyrenees) == {"other_meshes": chosen[:2], "other_mesh_front": kept}
        assert await remove(gone) == {"other_meshes": chosen[:1], "other_mesh_front": kept}
    assert locked and not any(locked)
    assert (pyrenees / "z_ao_pyr").is_dir()


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
    identity, problem = settings_module.folder_identity, api_module.folder_problem

    def watched_identity(folder):
        asked.append(settings_module.SAVING.locked())
        return identity(folder)

    def watched_problem(folder, xplane):
        asked.append(settings_module.SAVING.locked())
        return problem(folder, xplane)

    monkeypatch.setattr(settings_module, "folder_identity", watched_identity)
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
    assert asked == [False, False, False]  # the two folders on the disk, Live's folder


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


async def _languages_answered() -> None:
    """English then French sent by the window: each answered, the window in that language."""
    live = control.LiveControl(checks=lambda: {"xplane": None}, echo=quiet, catalog=lambda r: {})
    async with client_for(create_live_app(live)) as client:
        for language in ("en", "fr"):
            got = await client.post("/api/live/language", json={"language": language})
            assert got.status_code == 200 and got.json() == {"language": language}
            assert live.language == language


@pytest.mark.anyio
async def test_the_language_with_live_toml_broken_leaves_it_as_it_is(
    tmp_path: Path, monkeypatch
) -> None:
    """The window's language, English or French, with live.toml not parsing: answered, the file
    left as it is; once the file parses, a new language is saved. Once, an English pilot got a
    200 and a French one a 500 (the language compared with the defaults)."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    settings_file().parent.mkdir(parents=True)
    settings_file().write_text("[stream\n")
    await _languages_answered()
    assert settings_file().read_text() == "[stream\n"
    settings_file().write_text("[stream]\ndisk_gb = 40\n")
    live = control.LiveControl(checks=lambda: {"xplane": None}, echo=quiet, catalog=lambda r: {})
    live.set_language("fr")
    assert load_settings().language == "fr" and load_settings().disk_gb == 40


@pytest.mark.anyio
@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions")
async def test_the_language_with_live_toml_unreadable_leaves_it_as_it_is(
    tmp_path: Path, monkeypatch
) -> None:
    """The window's language, English or French, with live.toml not readable: answered, the file
    left as it is. Once, its OSError answered 500, with a traceback in LogLive at each load of the
    page."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    settings_file().parent.mkdir(parents=True)
    text = '[stream]\nlanguage = "en"\ndisk_gb = 40\n'
    settings_file().write_text(text)
    settings_file().chmod(0)
    try:
        await _languages_answered()
    finally:
        settings_file().chmod(0o644)
    assert settings_file().read_text() == text


def test_two_quick_language_switches_end_on_the_last(tmp_path: Path, monkeypatch) -> None:
    """EN to FR then back to EN at once: the second compared with the settings as the first saved
    them, under the settings' lock, not as read before it; once compared with the file before
    the first was written, it saved nothing and the window stayed in French at the next start."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path))
    save_settings(LiveSettings(language="en", disk_gb=60))
    lock = WatchedLock()
    monkeypatch.setattr(settings_module, "SAVING", lock)
    writing, release = threading.Event(), threading.Event()
    write = settings_module._write

    def slow_first(data: dict, settings: LiveSettings) -> None:
        if not writing.is_set():
            writing.set()
            assert release.wait(5)
        write(data, settings)

    monkeypatch.setattr(settings_module, "_write", slow_first)
    live = control.LiveControl(checks=lambda: {"xplane": None}, echo=quiet, catalog=lambda r: {})
    french = threading.Thread(target=live.set_language, args=("fr",))
    french.start()
    assert writing.wait(5)  # French being written
    english = threading.Thread(target=live.set_language, args=("en",))
    english.start()
    assert lock.waiting.wait(5) and english.is_alive()  # English waits for it
    release.set()
    french.join(5)
    english.join(5)
    assert load_settings().language == "en" and live.language == "en"
    assert load_settings().disk_gb == 60


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
    packs = other_meshes([folder]).folders[0].found.packs
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
    apart, each with the folder that gives it; under the list, how many squares in front no
    folder there gives."""
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
    assert said is not None and said["front_unseen"] == 0  # both given by a folder there
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
    gone = str(tmp_path / "Unplugged")  # its square in front, +50+010, given by no folder there
    front = ["+45+006", "+50+010"]
    save_settings(LiveSettings(other_meshes=[*chosen, gone], other_mesh_front=front))
    said = control._other_meshes_said(xp)
    assert said is not None and said["front_unseen"] == 1


@pytest.mark.parametrize("why", ["new", "app of before"])
def test_no_start_on_a_new_front_while_x_plane_runs(tmp_path: Path, monkeypatch, why: str) -> None:
    """A square put in front while X-Plane runs, or the pack an app of before set up again without
    its DSF: the start refused, saying why, no DSF written; X-Plane closed, the pack set up with
    it."""
    live, alps = live_and_alps(tmp_path)
    pack = tmp_path / "pack"
    if why == "new":
        setup_region([live, alps], pack, log=quiet)
    else:
        setup_region([live, alps], pack, log=quiet, front={COURCHEVEL: alps})
        as_an_app_of_before(pack, [live, alps])
    options = under_x_plane(tmp_path, monkeypatch, pack)
    save_settings(LiveSettings(other_meshes=[str(alps.parent)], other_mesh_front=["+46+002"]))
    meshes = Meshes([live, alps], {COURCHEVEL: alps})
    monkeypatch.setattr(session, "default_meshes", lambda xp, other=None: meshes)
    with pytest.raises(session.RefusedError, match="the meshes Live streams onto changed"):
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
    Live's; none of its ground naming photos; not there. One square in front: its mesh taken. The
    start's line of each folder in Live's log says the same."""
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
    every = other_meshes(folders, [live])  # the start's log: the same cases, in words
    assert [session._other_said(one, every) for one in every.folders] == [
        f"another tool's mesh in {alps_dir}: 2 squares given, all behind Live's mesh; left aside: "
        "z_ao_alps (already)",
        f"another tool's mesh in {inner_dir}: 0 squares given, folders higher in the list have "
        "them all; left aside: z_ao_inner (already)",
        f"another tool's mesh in {empty}: 0 squares given; no mesh Live can use",
        f"another tool's mesh: {gone} is not there (its disk unplugged?): left out",
    ]


def _said_in_both_languages(function: str, cases: list) -> dict[str, list]:
    """``function`` of live.js on each of ``cases``, in English then in French, run by node."""
    script = (
        f"import {{ {function} }} from {json.dumps((UI_DIR / 'live.js').as_uri())};"
        f"import {{ setLanguage }} from {json.dumps((UI_DIR / 'strings.js').as_uri())};"
        f"const cases = {json.dumps(cases)}; const out = {{}};"
        "for (const lang of ['en', 'fr']) {"
        f" setLanguage(lang); out[lang] = cases.map({function}); }}"
        "console.log(JSON.stringify(out));"
    )
    run = subprocess.run(
        ["node", "--input-type=module", "-e", script], capture_output=True, encoding="utf-8"
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_box_of_each_folder_in_both_languages() -> None:
    """The box of a folder in the list, each fact the engine's: beside its name, the squares it
    gives and those of them in front of Live, nothing when it gives none; under its path, only
    what the pilot acts on: its squares all behind Live's mesh, or all given by a folder above it,
    said plainly, not as a warning; a folder not there, or giving no mesh, a warning; the packs
    left aside he may have meant (a tile with its photos), the others (a region Live has already)
    only in the name's hover, with the meshes and roads it gives."""
    there = {"next_start": False, "overlays": [], "refused": [], "in_xplane": [], "front": []}
    already = [{"name": "z_ao_pyr", "why": "already"}]
    photos = [{"name": "zOrtho4XP_+45+006", "why": "photos"}, *already]
    eur = {"meshes": ["z_ao_eur"], "overlays": ["yAutoOrtho_Overlays"], "next_start": True}
    folders = [
        {**there, "name": "Alps", "meshes": ["z_ao_alps"], "squares": 4, "front": ["+45+006"]},
        {**there, "name": "Pyr", "meshes": [], "squares": 1, "refused": already},
        {**there, "name": "Pyr", "meshes": [], "squares": 0, "refused": already},
        {**there, "name": "Empty", "meshes": [], "squares": 0},
        {**there, "name": "Gone", "meshes": [], "squares": 0},
        {**there, **eur, "name": "XP", "squares": 2, "refused": photos},
    ]
    for folder, unused in zip(
        folders, (None, "behind", "above", "no_mesh", "gone", None), strict=True
    ):
        folder["unused"] = unused
    said = _said_in_both_languages("otherMeshSaid", folders)
    aside = "Left aside: z_ao_pyr (its tiles already have a mesh)."
    assert said["en"] == [
        {
            "count": "4 squares · 1 in front",
            "note": "",
            "warn": False,
            "title": "Alps\nUsed: z_ao_alps.\nLive takes no roads, forests or buildings from "
            "this folder.",
        },
        {
            "count": "1 square · 0 in front",
            "note": "All its squares are behind Live's mesh.",
            "warn": False,
            "title": f"Pyr\n{aside}",
        },
        {
            "count": "",
            "note": "No square given: a folder higher in the list has them all.",
            "warn": False,
            "title": f"Pyr\n{aside}",
        },
        {"count": "", "note": "No mesh Live can use here.", "warn": True, "title": "Empty"},
        {"count": "", "note": "Not there: is its disk unplugged?", "warn": True, "title": "Gone"},
        {
            "count": "2 squares · 0 in front",
            "note": "Left aside: zOrtho4XP_+45+006 (a tile with its photos).",
            "warn": False,
            "title": "XP\nUsed from Live's next start: z_ao_eur.\nRoads, forests and buildings: "
            "yAutoOrtho_Overlays.\nLeft aside: zOrtho4XP_+45+006 (a tile with its photos), "
            "z_ao_pyr (its tiles already have a mesh).",
        },
    ]
    fr = [(f["count"], f["note"]) for f in said["fr"]]
    assert fr[0] == ("4 carrés · 1 devant", "")
    assert fr[1] == ("1 carré · 0 devant", "Tous ses carrés sont derrière le mesh de Live.")
    assert fr[2][1] == "Aucun carré donné : un dossier plus haut dans la liste les a tous."
    assert fr[4][1] == "Introuvable : son disque est-il débranché ?"
    assert said["fr"][0]["title"].startswith("Alps\nPris par Live : z_ao_alps.")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_under_the_list_a_folder_not_there_keeps_its_squares_in_front_when_removed() -> None:
    """Under the folders, a line each: with two folders or more, that the higher one wins; with
    one or more, that a change waits for Live's next start; while a folder is not there and a
    square in front comes from no folder there (``front_unseen``, the engine's count), that its
    Remove leaves its squares in front of Live, Live not knowing which it gave (the remove
    endpoint puts none behind for it): not with no such square in front, nor with no folder
    gone; nothing without a folder."""
    gives = {"squares": 3, "unused": None}
    gone = {"squares": 0, "unused": "gone"}
    cases = [
        {"folders": [gives], "front_unseen": 1},
        {"folders": [gives, gone], "front_unseen": 1},
        {"folders": [gone], "front_unseen": 2},
        {"folders": [gone], "front_unseen": 0},
        {"folders": [], "front_unseen": 0},
        None,
    ]
    said = _said_in_both_languages("otherFrontSaid", cases)
    wins = "The higher folder wins where two share a square."
    start = "Changes apply at Live's next start, X-Plane closed."
    kept = (
        "Removing a folder that is not there leaves its squares in front of Live: plug its disk "
        "in first."
    )
    assert said["en"] == [[start], [wins, start, kept], [start, kept], [start], [], []]
    assert said["fr"][1] == [
        "Là où deux dossiers ont le même carré, le plus haut l'emporte.",
        "Les changements s'appliquent au prochain démarrage de Live, X-Plane fermé.",
        "Retirer un dossier introuvable laisse ses carrés devant Live : branchez d'abord son "
        "disque.",
    ]
