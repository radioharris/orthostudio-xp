# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Tiles OrthoStudio XP built, taken back where the Library lost them (``install.md`` 4.5):
``pack.find_tiles_receipt``, ``POST /api/library/find-in-workshop`` on the workshop's tiles folder,
and ``POST /api/library/import-ortho4xp`` on the folder the user shows. A user moved his tiles by
hand, found 200 of 1,300 again one by one, deleted the rest from the list, and his map stayed
empty (2026-10-22). Temporary folders and fake X-Planes only."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

import test_api_fakes as fakes
from orthostudio.api import app as api_app
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.install import Library, default_library_path, packs
from orthostudio.model import OVERLAY_PACK, TileRef
from orthostudio.pipeline.pack import (
    ArtefactEntry,
    PackManifest,
    find_tiles_receipt,
    install_receipt,
    links_to,
    overlay_link,
    write_pack,
)
from test_api_fakes import FakeBuild, FakeIndex, client_for

anyio_backend = fakes.anyio_backend
home = fakes.home
xplane = fakes.xplane

T = TileRef(43, 5)
U = TileRef(44, 6)


@pytest.fixture
def app(home: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(api_app, "other_xplane_dirs", lambda used: [])
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=None)
    application = create_app(
        env_factory=None, jobs=mgr, airports=FakeIndex(), settings_path=home / "config.toml"
    )
    yield application, mgr
    mgr.close()


def _pack(where: Path, tile: TileRef = T, *, key: str = "k1") -> Path:
    """A pack of ``tile`` as a build writes it into the tiles folder ``where``, its overlay DSF in
    the overlays pack beside it; ``key`` tells one build from another."""
    src = where.parent / f"src-{where.name}-{tile.name}-{key}"
    (src / "dsf" / "terrain").mkdir(parents=True)
    (src / "dsf" / f"{tile.name}.dsf").write_bytes(b"XPLNEDSF" + b"\0" * 100)
    (src / "dsf" / "terrain" / "a.ter").write_text("A\n800\nTERRAIN\n")
    (src / "tex" / "textures").mkdir(parents=True)
    (src / "tex" / "textures" / "a.dds").write_bytes(b"DDS " + b"\1" * 64)
    (src / "overlay.dsf").write_bytes(b"XPLNEDSF overlay")
    files = write_pack(
        where, tile, dsf_dir=src / "dsf", textures_dir=src / "tex", overlay_file=src / "overlay.dsf"
    )
    listed = {
        "dsf": tile.dsf_relpath.as_posix(),
        "dsf_size": 108,
        "textures": 1,
        "terrain": 1,
        "overlay": f"../{OVERLAY_PACK}/{tile.dsf_relpath.as_posix()}",
        "cfg": "",
    }
    artefacts = {"dsf": ArtefactEntry(key, "d" * 64, "tile.dsf@1")}
    manifest = PackManifest(tile.name, "BI", 16, artefacts, listed)
    (files.pack_dir / "orthostudio.toml").write_text(manifest.to_toml())
    return files.pack_dir


def _rows(tile: TileRef = T) -> dict[str, Any]:
    with Library(default_library_path()) as lib:
        return {r.kind: r for r in lib.list(tile=tile)}


@pytest.mark.anyio
async def test_tiles_of_the_workshop_the_list_lost_are_put_back(
    app: Any, home: Path, xplane: Path
) -> None:
    """The user's case: his tiles in the workshop's tiles folder, gone from the list. The page's
    look at the workshop puts them back as OrthoStudio XP tiles, the build's source, level and
    keys read from their manifest, their roads beside them; X-Plane does not show them yet, and
    Add puts them in it. Fails on the code before: nothing looked."""
    application, _mgr = app
    tiles = home / "tiles"
    _pack(tiles, T)
    _pack(tiles, U)
    async with client_for(application) as c:
        assert (await c.get("/api/library")).json() == []
        r = await c.post("/api/library/find-in-workshop")
        assert r.status_code == 200, r.text
        assert r.json()["added"] == [T.name, U.name] and r.json()["no_roads"] == []
        rows = {(e["tile"], e["kind"]): e for e in (await c.get("/api/library")).json()}
        ortho = rows[(T.name, "ortho")]
        assert ortho["built_by"] == "osxp" and ortho["provider"] == "BI" and ortho["zl"] == 16
        assert ortho["keys"] == {"dsf": "k1"} and ortho["present"] is True
        assert ortho["installed"] is False
        assert rows[(T.name, "overlay")]["path"] == str(tiles / OVERLAY_PACK)
        # a normal tile of the Library: Add puts it in X-Plane, roads included
        add = await c.post(f"/api/library/{T.name}/install", json={"path": ortho["path"]})
        assert add.status_code == 200, add.text
        again = await c.post("/api/library/find-in-workshop")
    cs = xplane / "Custom Scenery"
    assert links_to(cs / f"zOrthoStudio_{T.name}", tiles / f"zOrthoStudio_{T.name}")
    assert overlay_link(cs, tiles / OVERLAY_PACK) is not None
    assert again.json()["added"] == [] and again.json()["listed"] == 2  # once is enough


@pytest.mark.anyio
async def test_a_data_folder_moved_by_hand_is_found_again_x_plane_following(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """Every row leads to the old data folder, the tiles are in the workshop's: each is found
    again there at the Library's first look, as *Find again…* finds it, X-Plane's link following."""
    application, _mgr = app
    cs = xplane / "Custom Scenery"
    old = tmp_path / "Old disk" / "tiles"
    pack = _pack(old, T)
    install_receipt(pack, cs, tile=T, library_path=default_library_path())
    shutil.move(str(old), str(home / "tiles"))
    found = home / "tiles" / pack.name
    async with client_for(application) as c:
        r = await c.post("/api/library/find-in-workshop")
        assert r.status_code == 200, r.text
        rows = {e["kind"]: e for e in (await c.get("/api/library")).json()}
    assert r.json()["found"] == [T.name] and r.json()["added"] == []
    assert rows["ortho"]["path"] == str(found) and rows["ortho"]["installed"] is True
    assert links_to(cs / pack.name, found)
    assert overlay_link(cs, home / "tiles" / OVERLAY_PACK) is not None


def test_x_plane_running_leaves_the_moved_tiles_for_later(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X-Plane's links never change while it runs: a tile to find again waits, a tile to put back
    in the list does not, X-Plane being untouched."""
    cs = xplane / "Custom Scenery"
    old = tmp_path / "Old" / "tiles"
    pack = _pack(old, T)
    install_receipt(pack, cs, tile=T, library_path=default_library_path())
    shutil.move(str(old), str(home / "tiles"))
    _pack(home / "tiles", U)
    monkeypatch.setattr(packs, "xplane_running", lambda: True)
    receipt = find_tiles_receipt(home / "tiles", custom_sceneries=[cs])
    assert receipt["waiting"] == [T.name] and receipt["added"] == [U.name]
    assert _rows(T)["ortho"].path == pack and links_to(cs / pack.name, pack)
    monkeypatch.setattr(packs, "xplane_running", lambda: False)
    receipt = find_tiles_receipt(home / "tiles", custom_sceneries=[cs])
    assert receipt["found"] == [T.name] and receipt["listed"] == 1
    assert links_to(cs / pack.name, home / "tiles" / pack.name)


def test_a_tile_listed_elsewhere_keeps_its_row(home: Path, xplane: Path, tmp_path: Path) -> None:
    """The same tile in two places: the one listed stays, there or on a disk not connected; a
    copy is not taken for it, since X-Plane shows one folder of that name."""
    cs = xplane / "Custom Scenery"
    filed = _pack(tmp_path / "Filed", T)
    with Library(default_library_path()) as lib:
        lib.register(T, "BI", 16, filed, "osxp", {"dsf": "k1"})
    copy = _pack(home / "tiles", T, key="k2")
    receipt = find_tiles_receipt(home / "tiles", custom_sceneries=[cs])
    assert receipt["duplicates"] == [{"tile": T.name, "listed": str(filed)}]
    assert receipt["added"] == [] and _rows(T)["ortho"].path == filed
    # the listed one gone from a disk that is here, another build: the copy is listed beside it
    shutil.rmtree(filed)
    receipt = find_tiles_receipt(home / "tiles", custom_sceneries=[cs])
    assert receipt["added"] == [T.name]
    with Library(default_library_path()) as lib:
        assert {r.path for r in lib.list(tile=T, kind="ortho")} == {filed, copy}


def test_what_is_not_a_whole_tile_of_its_own_is_left(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    """A copy cut short, a folder renamed by hand, a link, and a tile a build works on are left,
    and so is a tile folder put inside X-Plane's Custom Scenery, as *Find again…* refuses it."""
    cs = xplane / "Custom Scenery"
    tiles = home / "tiles"
    cut = _pack(tiles, T)
    (cut / "textures" / "a.dds").unlink()
    renamed = _pack(tmp_path / "elsewhere", U)
    renamed.rename(tiles / "zOrthoStudio_+45+007")
    building = _pack(tiles, TileRef(46, 8))
    link = tiles / "zOrthoStudio_+47+009"
    link.symlink_to(_pack(tmp_path / "linked", TileRef(47, 9)), target_is_directory=True)
    receipt = find_tiles_receipt(tiles, custom_sceneries=[cs], leave={"+46+008"})
    assert receipt["incomplete"] == [T.name] and receipt["added"] == []
    assert building.is_dir() and _rows(TileRef(46, 8)) == {}
    inside = _pack(cs, TileRef(48, 10))
    for folder in (cs, inside):
        receipt = find_tiles_receipt(folder, custom_sceneries=[cs])
        assert receipt["in_xplane"] == ["+48+010"] and receipt["added"] == []
    with Library(default_library_path()) as lib:
        assert lib.list() == []


def test_a_tile_whose_roads_are_nowhere_comes_back_and_says_so(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    """Its roads and forests went with its old row (Delete…): it comes back, and the page says
    that building it again gives them back."""
    _pack(home / "tiles", T)
    shutil.rmtree(home / "tiles" / OVERLAY_PACK)
    receipt = find_tiles_receipt(home / "tiles", custom_sceneries=[xplane / "Custom Scenery"])
    assert receipt["added"] == [T.name] and receipt["no_roads"] == [T.name]
    assert set(_rows(T)) == {"ortho"}


@pytest.mark.anyio
async def test_the_folder_shown_finds_osxp_tiles_and_ortho4xp_ones(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """*Find tiles in a folder…*: a folder holding OrthoStudio XP tiles alone was refused, "a tile
    OrthoStudio XP built needs no import" (fails on the code before). Its tiles come back, a data
    folder shown finds those of its tiles folder, and Ortho4XP's are imported as before."""
    application, _mgr = app
    disk = tmp_path / "Big disk"
    _pack(disk / "Alps", T)
    _pack(disk / "Data" / "tiles", U)
    o4xp = disk / "Alps" / "zOrtho4XP_+45+007"
    (o4xp / "Earth nav data" / "+40+000").mkdir(parents=True)
    (o4xp / "Earth nav data" / "+40+000" / "+45+007.dsf").write_bytes(b"XPLNEDSF")
    (o4xp / "textures").mkdir()
    (o4xp / "textures" / "22224_10544_BI16.dds").write_bytes(b"DDS ")
    async with client_for(application) as c:
        alps = await c.post("/api/library/import-ortho4xp", json={"folder": str(disk / "Alps")})
        data = await c.post("/api/library/import-ortho4xp", json={"folder": str(disk / "Data")})
        rows = {(e["tile"], e["kind"]): e for e in (await c.get("/api/library")).json()}
    assert alps.status_code == 200, alps.text
    assert alps.json()["own"]["added"] == [T.name]
    assert [e["tile"] for e in alps.json()["entries"]] == ["+45+007"]
    assert data.status_code == 200, data.text
    assert data.json()["own"]["added"] == [U.name] and data.json()["entries"] == []
    # the data folder's tiles folder is said once, even where Ortho4XP's Tiles is the same folder
    said = [p for p in data.json()["searched"] if Path(p).is_dir()]
    assert sum(os.path.samefile(p, disk / "Data" / "tiles") for p in said) == 1
    assert rows[(T.name, "ortho")]["built_by"] == "osxp"
    assert rows[(U.name, "ortho")]["path"] == str(
        disk / "Data" / "tiles" / f"zOrthoStudio_{U.name}"
    )
    assert rows[("+45+007", "ortho")]["built_by"] == "ortho4xp"
