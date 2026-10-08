# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A tile moved by hand, found again (the atelier, step 2, 2026-10-05): ``POST
/api/library/{name}/find`` and ``pack.find_again_receipt``. Temporary folders and fake X-Planes
only; nothing of the machine's is read or written."""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path
from typing import Any

import pytest

import test_api_fakes as fakes
from orthostudio.api import app as api_app
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.home import disk_absent
from orthostudio.install import Library, default_library_path, packs
from orthostudio.install.scenery_packs import SceneryPacks
from orthostudio.model import OVERLAY_PACK, TileRef
from orthostudio.pipeline import pack as pack_mod
from orthostudio.pipeline.pack import (
    PARKED_OVERLAY,
    ArtefactEntry,
    PackManifest,
    find_again_receipt,
    install_receipt,
    library_pack,
    links_to,
    overlay_link,
    write_pack,
)
from test_api_fakes import FakeBuild, FakeIndex, client_for

anyio_backend = fakes.anyio_backend
home = fakes.home
xplane = fakes.xplane

T = TileRef(43, 5)
NAME = "zOrthoStudio_+43+005"


@pytest.fixture
def app(home: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The application and its job manager; the machine's other X-Planes are those a test names."""
    monkeypatch.setattr(api_app, "other_xplane_dirs", lambda used: [])
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=None)
    application = create_app(
        env_factory=None, jobs=mgr, airports=FakeIndex(), settings_path=home / "config.toml"
    )
    yield application, mgr
    mgr.close()


def _pack(where: Path, *, key: str = "k1") -> Path:
    """A pack of +43+005 as a build writes it into the tiles folder ``where``, its overlay DSF in
    the overlays pack beside it; ``key`` tells one build from another."""
    src = where.parent / f"src-{where.name}-{key}"
    (src / "dsf" / "terrain").mkdir(parents=True)
    (src / "dsf" / f"{T.name}.dsf").write_bytes(b"XPLNEDSF" + b"\0" * 100)
    (src / "dsf" / "terrain" / "a.ter").write_text("A\n800\nTERRAIN\n")
    (src / "tex" / "textures").mkdir(parents=True)
    (src / "tex" / "textures" / "a.dds").write_bytes(b"DDS " + b"\1" * 64)
    (src / "overlay.dsf").write_bytes(b"XPLNEDSF overlay")
    files = write_pack(
        where, T, dsf_dir=src / "dsf", textures_dir=src / "tex", overlay_file=src / "overlay.dsf"
    )
    listed = {
        "dsf": T.dsf_relpath.as_posix(),
        "dsf_size": 108,
        "textures": 1,
        "terrain": 1,
        "overlay": f"../{OVERLAY_PACK}/{T.dsf_relpath.as_posix()}",
        "cfg": "",
    }
    artefacts = {"dsf": ArtefactEntry(key, "d" * 64, "tile.dsf@1")}
    manifest = PackManifest(T.name, "BI", 16, artefacts, listed)
    (files.pack_dir / "orthostudio.toml").write_text(manifest.to_toml())
    return files.pack_dir


def _built(home: Path) -> Path:
    """+43+005 built in the atelier, in the Library, in no X-Plane."""
    pack = _pack(home / "tiles")
    with Library(default_library_path()) as lib:
        lib.register(T, "BI", 16, pack, "osxp", {"dsf": "k1"})
    return pack


def _installed(home: Path, xplane: Path) -> tuple[Path, Path]:
    """+43+005 built in the atelier and in X-Plane: its pack, and X-Plane's Custom Scenery."""
    cs = xplane / "Custom Scenery"
    pack = _pack(home / "tiles")
    install_receipt(pack, cs, tile=T, library_path=default_library_path())
    return pack, cs


def _moved(pack: Path, to: Path) -> Path:
    """The tile's folder moved by hand, as the Finder moves it: the folder alone."""
    to.mkdir(parents=True, exist_ok=True)
    return Path(shutil.move(str(pack), str(to / pack.name)))


def _xplane(where: Path) -> Path:
    """Another X-Plane 12 of the machine: its Custom Scenery."""
    (where / "Resources").mkdir(parents=True)
    cs = where / "Custom Scenery"
    cs.mkdir()
    (cs / "scenery_packs.ini").write_bytes(b"I\n1000 Version\nSCENERY\n\n")
    return cs


async def _rows(c: Any) -> dict[str, dict[str, Any]]:
    """The Library's rows of +43+005, by kind."""
    return {r["kind"]: r for r in (await c.get("/api/library")).json() if r["tile"] == T.name}


@pytest.mark.anyio
async def test_a_tile_moved_by_hand_is_found_again_and_x_plane_follows(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """A user files his tiles by area with the Finder: the Library said "files missing" and X-Plane
    showed nothing of the tile. Shown where it went, the tile is found again: the Library and
    X-Plane follow, its roads and forests come beside it, and nothing of the tile moves."""
    application, _mgr = app
    pack, cs = _installed(home, xplane)
    alps = tmp_path / "Big disk" / "Alps"
    moved = _moved(pack, alps)
    dds = moved / "textures" / "a.dds"
    before = (dds.stat().st_ino, dds.stat().st_mtime_ns)
    async with client_for(application) as c:
        rows = await _rows(c)
        assert rows["ortho"]["present"] is False and rows["ortho"]["disk_absent"] is False
        r = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(alps)}
        )
        assert r.status_code == 200, r.text
        rows = await _rows(c)
    receipt = r.json()
    assert receipt["from"] == str(pack) and receipt["to"] == str(moved)
    assert receipt["xplanes"] == [str(cs)] and receipt["overlay_lost"] is False
    assert rows["ortho"]["path"] == str(moved)
    assert rows["ortho"]["present"] is True and rows["ortho"]["installed"] is True
    assert links_to(cs / NAME, moved)  # X-Plane reads it there, under the same name
    # its roads and forests came with it, beside it, in an overlays pack X-Plane reads
    beside = alps / OVERLAY_PACK
    assert (beside / T.dsf_relpath).is_file()
    assert not (home / "tiles" / OVERLAY_PACK / T.dsf_relpath).exists()
    assert overlay_link(cs, beside) is not None and rows["overlay"]["path"] == str(beside)
    assert (cs / "scenery_packs.ini").read_text().count(f"Custom Scenery/{NAME}/") == 1
    assert (dds.stat().st_ino, dds.stat().st_mtime_ns) == before  # its images stayed as they were


@pytest.mark.anyio
async def test_the_folder_shown_may_be_the_tile_s_own(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    application, _mgr = app
    pack, cs = _installed(home, xplane)
    moved = _moved(pack, tmp_path / "Tiles by hand")
    async with client_for(application) as c:
        r = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(moved)}
        )
    assert r.status_code == 200, r.text
    assert r.json()["to"] == str(moved) and links_to(cs / NAME, moved)


@pytest.mark.anyio
async def test_a_tile_in_no_x_plane_is_found_by_the_library_alone(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """No X-Plane showed it: the row follows, and its roads and forests wait in it, parked, as
    they would after "Remove from X-Plane", for the day it is added."""
    application, _mgr = app
    pack = _built(home)
    moved = _moved(pack, tmp_path / "Alps")
    async with client_for(application) as c:
        r = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(moved)}
        )
        rows = await _rows(c)
    assert r.status_code == 200, r.text
    assert r.json()["xplanes"] == [] and rows["ortho"]["path"] == str(moved)
    assert (moved / PARKED_OVERLAY).is_file()
    assert not (xplane / "Custom Scenery" / NAME).exists()


@pytest.mark.anyio
async def test_every_x_plane_that_showed_the_tile_follows_and_no_other(
    app: Any, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application, _mgr = app
    beta = tmp_path / "X-Plane 12 beta"
    old = tmp_path / "X-Plane 12 old"
    cs_beta, cs_old = _xplane(beta), _xplane(old)
    monkeypatch.setattr(api_app, "other_xplane_dirs", lambda used: [beta, old])
    pack, cs = _installed(home, xplane)
    install_receipt(pack, cs_beta, tile=T, library_path=default_library_path())
    alps = tmp_path / "Alps"
    moved = _moved(pack, alps)
    async with client_for(application) as c:
        r = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(alps)}
        )
    assert r.status_code == 200, r.text
    assert r.json()["xplanes"] == [str(cs), str(cs_beta)]
    for each in (cs, cs_beta):
        assert links_to(each / NAME, moved)
        assert overlay_link(each, alps / OVERLAY_PACK) is not None
    assert not (cs_old / NAME).exists()  # it never showed the tile: nothing is added to it


@pytest.mark.anyio
async def test_a_folder_that_is_not_that_tile_changes_nothing(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """Shown the wrong folder, a renamed copy, another build of the tile or a copy cut short, the
    page says so and nothing changes: the row, X-Plane's link, its roads and forests."""
    application, _mgr = app
    pack, cs = _installed(home, xplane)
    moved = _moved(pack, tmp_path / "Alps")
    empty = tmp_path / "Empty"
    empty.mkdir()
    renamed = tmp_path / "Renamed" / "My +43+005"
    shutil.copytree(moved, renamed)
    other = _pack(tmp_path / "Other", key="k2")
    cut = tmp_path / "Cut short" / NAME
    shutil.copytree(moved, cut)
    (cut / "textures" / "a.dds").unlink()
    async with client_for(application) as c:
        for folder, code in (
            (empty, "SYS_TILE_NOT_IN_FOLDER"),
            (renamed, "SYS_TILE_NOT_IN_FOLDER"),
            (other.parent, "SYS_TILE_OTHER_BUILD"),
            (cut, "SYS_TILE_INCOMPLETE"),
        ):
            r = await c.post(
                f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(folder)}
            )
            assert r.status_code == 422 and r.json()["error"]["code"] == code, (folder, r.text)
        rows = await _rows(c)
    assert rows["ortho"]["path"] == str(pack) and rows["ortho"]["present"] is False
    assert links_to(cs / NAME, pack)
    assert (home / "tiles" / OVERLAY_PACK / T.dsf_relpath).is_file()
    assert not (moved / PARKED_OVERLAY).exists()


@pytest.mark.anyio
async def test_x_plane_running_changes_nothing(
    app: Any, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X-Plane's links never change while it runs."""
    application, _mgr = app
    pack, cs = _installed(home, xplane)
    moved = _moved(pack, tmp_path / "Alps")
    monkeypatch.setattr(packs, "xplane_running", lambda: True)
    async with client_for(application) as c:
        r = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(moved)}
        )
        rows = await _rows(c)
    assert r.status_code == 409 and r.json()["error"]["code"] == "XP_RUNNING"
    assert rows["ortho"]["path"] == str(pack) and links_to(cs / NAME, pack)
    assert (home / "tiles" / OVERLAY_PACK / T.dsf_relpath).is_file()


@pytest.mark.anyio
async def test_a_tile_in_its_place_an_imported_one_and_one_in_a_build_are_not_looked_for(
    app: Any, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application, mgr = app
    pack, _cs = _installed(home, xplane)
    imported = tmp_path / "Ortho4XP" / "Tiles" / "zOrtho4XP_+44+005"
    with Library(default_library_path()) as lib:
        lib.register(TileRef(44, 5), "BI", 16, imported, "ortho4xp", None)
    async with client_for(application) as c:
        here = await c.post(f"/api/library/{T.name}/find", json={"path": str(pack), "folder": "/x"})
        o4xp = await c.post(
            "/api/library/+44+005/find", json={"path": str(imported), "folder": str(tmp_path)}
        )
        moved = _moved(pack, tmp_path / "Alps")
        monkeypatch.setattr(mgr, "building_tiles", lambda: {T.name: "job-1"})
        building = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(moved)}
        )
    assert here.status_code == 409 and here.json()["error"]["code"] == "SYS_TILE_NOT_MISSING"
    assert o4xp.status_code == 409 and o4xp.json()["error"]["code"] == "SYS_PACK_IMPORTED"
    assert building.status_code == 409
    assert building.json()["error"]["code"] == "SYS_TILE_IN_BUILD"


@pytest.mark.anyio
async def test_a_tile_whose_roads_are_nowhere_is_found_and_says_so(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """The atelier's overlays pack went too: the tile is found, and the page says that building it
    again gives its roads and forests back."""
    application, _mgr = app
    pack, cs = _installed(home, xplane)
    moved = _moved(pack, tmp_path / "Alps")
    shutil.rmtree(home / "tiles" / OVERLAY_PACK)
    async with client_for(application) as c:
        r = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(moved)}
        )
    assert r.status_code == 200, r.text
    assert r.json()["overlay_lost"] is True and links_to(cs / NAME, moved)


def test_asked_again_after_a_stop_half_way_it_finishes(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app stopped while X-Plane's links were being changed: asked again, it finishes what was
    left, and the tile's roads and forests are not lost on the way."""
    pack, cs = _installed(home, xplane)
    moved = _moved(pack, tmp_path / "Alps")
    entry = library_pack(T.name, path=str(pack), library_path=default_library_path())
    real = pack_mod.install_receipt
    stops = [OSError("the power went")]

    def install(*args: Any, **kwargs: Any) -> Any:
        if stops:
            raise stops.pop()
        return real(*args, **kwargs)

    monkeypatch.setattr(pack_mod, "install_receipt", install)
    with pytest.raises(OSError):
        find_again_receipt(entry, moved, custom_sceneries=[cs], library_path=default_library_path())
    assert (moved / PARKED_OVERLAY).is_file()  # its roads and forests came, then the stop
    receipt = find_again_receipt(
        entry, moved, custom_sceneries=[cs], library_path=default_library_path()
    )
    assert receipt["xplanes"] == [str(cs)] and links_to(cs / NAME, moved)
    assert (moved.parent / OVERLAY_PACK / T.dsf_relpath).is_file()
    with Library(default_library_path()) as lib:
        assert [r.path for r in lib.list(tile=T, kind="ortho")] == [moved]


def test_a_copy_of_its_roads_made_before_a_stop_is_not_left_twice(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    """The app stopped between the copy of the tile's roads and forests into it and the removal
    of the ones behind: asked again, the ones behind go, or X-Plane would draw them twice."""
    pack, cs = _installed(home, xplane)
    moved = _moved(pack, tmp_path / "Alps")
    behind = home / "tiles" / OVERLAY_PACK / T.dsf_relpath
    shutil.copy2(behind, moved / PARKED_OVERLAY)  # copied, and the stop came
    entry = library_pack(T.name, path=str(pack), library_path=default_library_path())
    find_again_receipt(entry, moved, custom_sceneries=[cs], library_path=default_library_path())
    assert not behind.exists()
    assert (moved.parent / OVERLAY_PACK / T.dsf_relpath).is_file() and links_to(cs / NAME, moved)


@pytest.mark.anyio
async def test_a_tile_put_in_x_plane_s_own_custom_scenery_is_not_found_there(
    app: Any, home: Path, xplane: Path
) -> None:
    """Ortho4XP's habit: the tile's folder dragged into Custom Scenery, in place of its link. Found
    there, it lost its roads and forests, and could no longer be taken out: the page says to put
    it elsewhere, and nothing changes, its roads and forests least of all (a review, 2026-10-05)."""
    application, _mgr = app
    pack, cs = _installed(home, xplane)
    (cs / NAME).unlink()  # the Finder replaces the link with the folder
    moved = _moved(pack, cs)
    async with client_for(application) as c:
        r = await c.post(f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(cs)})
        rows = await _rows(c)
    assert r.status_code == 422 and r.json()["error"]["code"] == "SYS_TILE_IN_CUSTOM_SCENERY"
    assert rows["ortho"]["path"] == str(pack)
    assert (home / "tiles" / OVERLAY_PACK / T.dsf_relpath).is_file()
    assert not (moved / PARKED_OVERLAY).exists()


def test_the_one_overlay_there_is_is_never_removed_for_a_copy_of_itself(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    """A folder whose overlays pack is a link to the one the tile came from shows the same file
    twice: it is the only one there is, and it stays (a review, 2026-10-05)."""
    pack, cs = _installed(home, xplane)
    shelf = tmp_path / "Shelf"
    shelf.mkdir()
    (shelf / OVERLAY_PACK).symlink_to(home / "tiles" / OVERLAY_PACK, target_is_directory=True)
    moved = _moved(pack, shelf)
    entry = library_pack(T.name, path=str(pack), library_path=default_library_path())
    find_again_receipt(entry, moved, custom_sceneries=[cs], library_path=default_library_path())
    assert (home / "tiles" / OVERLAY_PACK / T.dsf_relpath).is_file()


def test_asked_again_after_a_stop_past_x_plane_s_link_it_finishes(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stop came after X-Plane's link led to the found folder, before its roads and forests
    were put beside it: asked again, X-Plane's install is finished, and the tile has its roads
    (a review, 2026-10-05)."""
    pack, cs = _installed(home, xplane)
    moved = _moved(pack, tmp_path / "Alps")
    entry = library_pack(T.name, path=str(pack), library_path=default_library_path())
    real = pack_mod._unpark_overlay
    stops = [OSError("the power went")]

    def unpark(*args: Any, **kwargs: Any) -> Any:
        if stops:
            raise stops.pop()
        return real(*args, **kwargs)

    monkeypatch.setattr(pack_mod, "_unpark_overlay", unpark)
    with pytest.raises(OSError):
        find_again_receipt(entry, moved, custom_sceneries=[cs], library_path=default_library_path())
    assert links_to(cs / NAME, moved) and (moved / PARKED_OVERLAY).is_file()
    receipt = find_again_receipt(
        entry, moved, custom_sceneries=[cs], library_path=default_library_path()
    )
    assert receipt["xplanes"] == [str(cs)] and receipt["overlay_lost"] is False
    assert (moved.parent / OVERLAY_PACK / T.dsf_relpath).is_file()
    assert overlay_link(cs, moved.parent / OVERLAY_PACK) is not None


@pytest.mark.anyio
async def test_a_line_the_user_disabled_stays_disabled(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    application, _mgr = app
    pack, cs = _installed(home, xplane)
    ini = cs / "scenery_packs.ini"
    line = f"Custom Scenery/{NAME}/"
    ini.write_text(ini.read_text().replace(f"SCENERY_PACK {line}", f"SCENERY_PACK_DISABLED {line}"))
    alps = tmp_path / "Alps"
    _moved(pack, alps)
    async with client_for(application) as c:
        r = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(alps)}
        )
    assert r.status_code == 200, r.text
    assert f"SCENERY_PACK_DISABLED {line}" in ini.read_text()
    assert f"SCENERY_PACK {line}" not in ini.read_text()


@pytest.mark.anyio
async def test_its_new_roads_line_takes_the_state_of_the_one_it_came_from(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """A user of simHeaven X-World turns OrthoStudio XP's roads off: a tile found again in a new
    folder brought them back on, in a new overlays line added enabled (a review, 2026-10-05). That
    line takes the state of the one the tile came from."""
    application, _mgr = app
    pack, cs = _installed(home, xplane)
    ini = cs / "scenery_packs.ini"
    roads = f"Custom Scenery/{OVERLAY_PACK}/"
    ini.write_text(
        ini.read_text().replace(f"SCENERY_PACK {roads}", f"SCENERY_PACK_DISABLED {roads}")
    )
    alps = tmp_path / "Alps"
    _moved(pack, alps)
    async with client_for(application) as c:
        r = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(alps)}
        )
    assert r.status_code == 200, r.text
    link = overlay_link(cs, alps / OVERLAY_PACK)
    assert link is not None and link.name != OVERLAY_PACK
    assert f"SCENERY_PACK_DISABLED Custom Scenery/{link.name}/" in ini.read_text()


@pytest.mark.anyio
async def test_a_tile_whose_disk_is_away_says_so_rather_than_not_found(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """A disk unplugged comes back by itself: the Library says *Disk absent* and offers nothing
    to do. A folder gone from a disk that is here says *Not found*, with *Find again…*."""
    application, _mgr = app
    away = Path("/Volumes") / f"OSXP test disk {secrets.token_hex(4)}" / "Tiles" / NAME
    (tmp_path / "Tiles").mkdir()
    gone = tmp_path / "Tiles" / "zOrthoStudio_+43+006"
    with Library(default_library_path()) as lib:
        lib.register(T, "BI", 16, away, "osxp", {"dsf": "k1"})
        lib.register(TileRef(43, 6), "BI", 16, gone, "osxp", {"dsf": "k2"})
    async with client_for(application) as c:
        rows = {r["tile"]: r for r in (await c.get("/api/library")).json()}
    assert rows[T.name]["present"] is False and rows[T.name]["disk_absent"] is True
    assert rows["+43+006"]["present"] is False and rows["+43+006"]["disk_absent"] is False
    assert disk_absent(tmp_path) is False  # there
    # a folder gone two levels down on a disk that is here is not a disk away
    assert disk_absent(tmp_path / "Gone" / "Alps" / NAME) is False
    assert disk_absent(Path("/Volumes") / f"nope {secrets.token_hex(4)}" / "x") is True
    assert disk_absent(Path("/media") / f"someone {secrets.token_hex(4)}" / "USB" / "x") is True


def _unplugged(disk: Path) -> Path:
    """The disk holding a folder unplugged: its folders are out of reach, the disk renamed aside."""
    return disk.rename(disk.with_name(disk.name + " (away)"))


def _found_on_the_mac(home: Path, xplane: Path, tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """+43+005 filed on disk D, in X-Plane with its roads beside it, copied to the Mac's disk by
    hand: its pack, the copy, X-Plane's Custom Scenery and disk D."""
    cs = xplane / "Custom Scenery"
    disk = tmp_path / "Disk D"
    pack = _pack(disk / "Alps")
    install_receipt(pack, cs, tile=T, library_path=default_library_path())
    copy = tmp_path / "Mac" / "Alps" / NAME
    shutil.copytree(pack, copy)
    return pack, copy, cs, disk


@pytest.mark.anyio
async def test_roads_left_on_a_disk_away_are_not_drawn_once_it_is_back(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """Found again elsewhere while the disk of its folder was away, a tile left its roads in the
    overlays pack there, and the day the disk came back X-Plane drew them twice (found on the
    owner's tiles, 2026-10-06). That pack, which no tile of X-Plane is in any more, leaves X-Plane:
    its link and its line."""
    application, _mgr = app
    pack, copy, cs, disk = _found_on_the_mac(home, xplane, tmp_path)
    roads = overlay_link(cs, pack.parent / OVERLAY_PACK)
    assert roads is not None
    aside = _unplugged(disk)
    async with client_for(application) as c:
        r = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(copy)}
        )
    assert r.status_code == 200, r.text
    assert links_to(cs / NAME, copy) and r.json()["overlay_lost"] is True
    aside.rename(disk)  # plugged in again: the old roads are there, drawn by nothing
    assert (pack.parent / OVERLAY_PACK / T.dsf_relpath).is_file()
    assert overlay_link(cs, pack.parent / OVERLAY_PACK) is None
    assert SceneryPacks.load(cs / "scenery_packs.ini").find(roads.name) is None


@pytest.mark.anyio
async def test_a_tile_of_that_folder_still_in_x_plane_keeps_its_roads_pack(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """Another tile of the folder left is still in X-Plane: the overlays pack there holds its
    roads, and stays for the day its disk comes back."""
    from test_put_back import W, _pack_of  # it imports this module: imported once both are

    application, _mgr = app
    pack, copy, cs, disk = _found_on_the_mac(home, xplane, tmp_path)
    neighbour = _pack_of(W, pack.parent, "w1")
    install_receipt(neighbour, cs, tile=W, library_path=default_library_path())
    roads = overlay_link(cs, pack.parent / OVERLAY_PACK)
    assert roads is not None
    _unplugged(disk)
    async with client_for(application) as c:
        r = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(copy)}
        )
    assert r.status_code == 200, r.text
    assert links_to(cs / NAME, copy) and os.path.lexists(roads)
    assert SceneryPacks.load(cs / "scenery_packs.ini").find(roads.name) is not None


@pytest.mark.anyio
async def test_a_roads_line_turned_off_goes_with_its_link_and_the_next_build_draws_the_roads(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """A user of simHeaven X-World turns OrthoStudio XP's roads off, and the tile is found again
    while the disk of its folder is away. That folder's roads line was kept alone, disabled, for
    the next overlays pack taking that name to keep the roads off (a review, 2026-10-06): it did
    only when that name was the first one free, and on the owner's Mac the roads came back on
    under another name, the lone line left behind (2026-10-06). The line goes with its link; built
    again, the tile's roads are drawn under a line of their own, which the user turns off again if
    they want."""
    application, _mgr = app
    pack, copy, cs, disk = _found_on_the_mac(home, xplane, tmp_path)
    ini = cs / "scenery_packs.ini"
    roads = overlay_link(cs, pack.parent / OVERLAY_PACK)
    assert roads is not None
    listed = SceneryPacks.load(ini)
    assert listed.disable(roads.name)
    listed.save(ini)
    _unplugged(disk)
    async with client_for(application) as c:
        r = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(copy)}
        )
    assert r.status_code == 200, r.text
    # its link and its line go
    assert not os.path.lexists(roads)
    assert SceneryPacks.load(ini).find(roads.name) is None
    # built again: its roads parked in it, installed as the put back installs it
    (copy / PARKED_OVERLAY).write_bytes(b"XPLNEDSF overlay")
    install_receipt(copy, cs, tile=T, library_path=default_library_path(), reenable=False)
    new = overlay_link(cs, copy.parent / OVERLAY_PACK)
    assert new is not None
    line = SceneryPacks.load(ini).find(new.name)
    assert line is not None and line.enabled


@pytest.mark.anyio
async def test_a_roads_link_name_taken_over_starts_its_line_again(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """The overlays link of a folder deleted by hand is broken, its name free: the roads of a new
    folder took it over with the line the user had turned off for the folder before, and X-Plane
    drew none of them, the Library saying nothing (found on the owner's Mac, 2026-10-06). A name
    taken anew starts its line as a new one."""
    from test_api_app import _osxp_pack
    from test_put_back import W  # it imports this module: imported once both are

    application, _mgr = app
    pack, cs = _installed(home, xplane)
    ini = cs / "scenery_packs.ini"
    listed = SceneryPacks.load(ini)
    assert listed.disable(OVERLAY_PACK)  # the user turned that folder's roads off
    listed.save(ini)
    alps = tmp_path / "Alps"
    _moved(pack, alps)
    async with client_for(application) as c:  # its roads go with it, still off
        r = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(alps)}
        )
    assert r.status_code == 200, r.text
    shutil.rmtree(home / "tiles")  # the folder it left deleted by hand
    assert packs.is_link(cs / OVERLAY_PACK) and not (cs / OVERLAY_PACK).exists()
    new = _osxp_pack(home, W.name, out=tmp_path / "SSD" / "tiles")  # a new folder, with roads

    install_receipt(new, cs, tile=W, library_path=default_library_path())

    assert links_to(cs / OVERLAY_PACK, new.parent / OVERLAY_PACK)  # the name taken over
    line = SceneryPacks.load(ini).find(OVERLAY_PACK)
    assert line is not None and line.enabled
    assert pack_mod.overlay_states(cs)[W.name].state == "own"


@pytest.mark.anyio
async def test_roads_turned_off_stay_off_under_a_name_taken_over_whose_line_was_on(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """The other way round: a tile whose roads the user turned off, found again in a new folder
    whose overlays take over the name of a folder deleted by hand, whose line was on, had its
    roads drawn again."""
    from test_api_app import _osxp_pack
    from test_put_back import W  # it imports this module: imported once both are

    application, _mgr = app
    cs = xplane / "Custom Scenery"
    ini = cs / "scenery_packs.ini"
    library = default_library_path()
    other = _osxp_pack(home, W.name)  # W in the atelier, its roads on
    install_receipt(other, cs, tile=W, library_path=library)
    pack = _pack(tmp_path / "Disk B" / "Alps")  # T in another folder, its roads turned off
    install_receipt(pack, cs, tile=T, library_path=library)
    roads = overlay_link(cs, pack.parent / OVERLAY_PACK)
    assert roads is not None and roads.name != OVERLAY_PACK
    listed = SceneryPacks.load(ini)
    assert listed.disable(roads.name)
    listed.save(ini)
    async with client_for(application) as c:
        # W moved by hand and found again: the atelier's overlays pack is left empty, its line on
        _moved(other, tmp_path / "Valais")
        r = await c.post(
            f"/api/library/{W.name}/find",
            json={"path": str(other), "folder": str(tmp_path / "Valais")},
        )
        assert r.status_code == 200, r.text
        shutil.rmtree(home / "tiles")  # the atelier's folder deleted by hand
        # T moved by hand and found again: its roads come from a line turned off
        _moved(pack, tmp_path / "Jura")
        r = await c.post(
            f"/api/library/{T.name}/find",
            json={"path": str(pack), "folder": str(tmp_path / "Jura")},
        )
        assert r.status_code == 200, r.text
    assert links_to(cs / OVERLAY_PACK, tmp_path / "Jura" / OVERLAY_PACK)  # the name taken over
    line = SceneryPacks.load(ini).find(OVERLAY_PACK)
    assert line is not None and not line.enabled
    assert T.name not in pack_mod.overlay_states(cs)


def _x_plane_starts(cs: Path) -> None:
    """X-Plane 12's start as measured on the owner's Mac (2026-10-06): the lines of the packs it
    cannot reach go, the packs of Custom Scenery it does not list come at the end, enabled."""
    ini = cs / "scenery_packs.ini"
    listed = SceneryPacks.load(ini)
    for name in list(listed.names()):
        if not name.startswith("*") and not (cs / name).exists():
            listed.remove(name)
    listed.save(ini)
    known = set(SceneryPacks.load(ini).names())
    with ini.open("ab") as f:
        for name in sorted(os.listdir(cs)):
            if name not in known and (cs / name).is_dir():
                f.write(f"SCENERY_PACK Custom Scenery/{name}/\n".encode())


@pytest.mark.anyio
async def test_x_plane_started_without_the_disk_brings_no_old_roads_back(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """The roads line of the folder left is disabled, its link kept: X-Plane started without the
    disk dropped the line, and gave it back enabled once the disk was back, the old roads over
    X-World's (a review, 2026-10-06). The link and its line going, nothing of that folder comes
    back."""
    application, _mgr = app
    pack, copy, cs, disk = _found_on_the_mac(home, xplane, tmp_path)
    ini = cs / "scenery_packs.ini"
    roads = overlay_link(cs, pack.parent / OVERLAY_PACK)
    assert roads is not None
    listed = SceneryPacks.load(ini)
    assert listed.disable(roads.name)
    listed.save(ini)
    aside = _unplugged(disk)
    async with client_for(application) as c:
        r = await c.post(
            f"/api/library/{T.name}/find", json={"path": str(pack), "folder": str(copy)}
        )
    assert r.status_code == 200, r.text
    _x_plane_starts(cs)  # the disk away
    aside.rename(disk)
    _x_plane_starts(cs)  # the disk back
    assert overlay_link(cs, pack.parent / OVERLAY_PACK) is None
    assert all(
        os.path.realpath(cs / name) != os.path.realpath(pack.parent / OVERLAY_PACK)
        for name in SceneryPacks.load(ini).names()
    )


def test_the_tidying_up_and_an_install_do_not_lose_each_other_s_line(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every install edits scenery_packs.ini under one lock: the tidying up of Find again did not
    take it, and an install saving meanwhile lost its line (a review, 2026-10-06)."""
    import threading

    from test_put_back import W, _pack_of  # it imports this module: imported once both are

    pack, copy, cs, disk = _found_on_the_mac(home, xplane, tmp_path)
    other = _pack_of(W, tmp_path / "Atelier" / "tiles", "w1")
    _unplugged(disk)
    read = threading.Event()
    saved = threading.Event()
    real_update = packs._update_ini

    def slow_update(custom_scenery: Path, name: str, *, add: bool) -> bool:
        """The tidying up has read the file when the other install would save it."""
        if add:
            return real_update(custom_scenery, name, add=add)
        listed = SceneryPacks.load(custom_scenery / "scenery_packs.ini")
        read.set()
        saved.wait(0.5)
        changed = listed.remove(name)
        if changed:
            listed.save(custom_scenery / "scenery_packs.ini", backup=True)
        return changed

    def install_meanwhile() -> None:
        assert read.wait(10)
        install_receipt(other, cs, tile=W, library_path=default_library_path())
        saved.set()

    monkeypatch.setattr(packs, "_update_ini", slow_update)
    meanwhile = threading.Thread(target=install_meanwhile)
    meanwhile.start()
    entry = library_pack(T.name, path=str(pack), library_path=default_library_path())
    find_again_receipt(entry, copy, custom_sceneries=[cs], library_path=default_library_path())
    meanwhile.join(10)
    assert other.name in SceneryPacks.load(cs / "scenery_packs.ini").names()
