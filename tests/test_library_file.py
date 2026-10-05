# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Tiles filed elsewhere (the atelier, step 3, 2026-10-05): ``pipeline/filing.py`` and the routes
``POST /api/library/file-plan``, ``POST /api/library/{name}/file``, ``GET /api/library/filing``,
``POST /api/library/filing/stop``. Temporary folders and fake X-Planes only; nothing of the
machine's is read or written."""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest

import test_api_fakes as fakes
from orthostudio.api import app as api_app
from orthostudio.home import data_root
from orthostudio.install import Library, default_library_path, packs
from orthostudio.model import OVERLAY_PACK, TileRef
from orthostudio.pipeline import filing
from orthostudio.pipeline.filing import PART_SUFFIX, FilingStoppedError, file_tile
from orthostudio.pipeline.pack import library_pack, links_to, overlay_link
from test_api_fakes import client_for
from test_library_find import NAME, T, _built, _installed, _pack, _rows
from test_library_find import app as app  # the fixture, as test_library_find has it

anyio_backend = fakes.anyio_backend
home = fakes.home
xplane = fakes.xplane


def _entry(pack: Path) -> Any:
    return library_pack(T.name, path=str(pack), library_path=default_library_path())


def _copied(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every folder on another disk than the tile: the copy, not the move."""
    monkeypatch.setattr(filing, "same_disk", lambda a, b: False)


def _bytes(pack: Path) -> dict[str, bytes]:
    return {
        str(f.relative_to(pack)): f.read_bytes() for f in sorted(pack.rglob("*")) if f.is_file()
    }


@pytest.mark.anyio
async def test_a_tile_filed_on_the_same_disk_is_moved_in_one_step(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """On one disk, the folder moves: at once, nothing more on the disk, and X-Plane, the Library
    and its roads and forests follow it."""
    application, _mgr = app
    pack, cs = _installed(home, xplane)
    dds = pack / "textures" / "a.dds"
    inode = dds.stat().st_ino
    alps = tmp_path / "Alps"
    alps.mkdir()
    async with client_for(application) as c:
        r = await c.post(
            f"/api/library/{T.name}/file", json={"path": str(pack), "folder": str(alps)}
        )
        rows = await _rows(c)
    assert r.status_code == 200, r.text
    target = alps / NAME
    assert r.json()["moved"] is True and r.json()["to"] == str(target)
    assert not pack.exists() and (target / "textures" / "a.dds").stat().st_ino == inode
    assert rows["ortho"]["path"] == str(target) and rows["ortho"]["installed"] is True
    assert links_to(cs / NAME, target) and overlay_link(cs, alps / OVERLAY_PACK) is not None
    assert (alps / OVERLAY_PACK / T.dsf_relpath).is_file()


@pytest.mark.anyio
async def test_a_tile_filed_on_another_disk_is_copied_read_back_then_taken_out(
    app: Any, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On another disk, the copy is made under a temporary name, read back, put in place; then
    X-Plane and the Library follow it, and the tile leaves the folder it came from."""
    application, _mgr = app
    _copied(monkeypatch)
    pack, cs = _installed(home, xplane)
    before = _bytes(pack)
    alps = tmp_path / "Other disk" / "Alps"
    alps.mkdir(parents=True)
    async with client_for(application) as c:
        r = await c.post(
            f"/api/library/{T.name}/file", json={"path": str(pack), "folder": str(alps)}
        )
        rows = await _rows(c)
    assert r.status_code == 200, r.text
    target = alps / NAME
    assert r.json()["moved"] is False and r.json()["left"] is None
    assert _bytes(target) == before and not pack.exists()
    assert not (alps / (NAME + PART_SUFFIX)).exists()
    assert rows["ortho"]["path"] == str(target) and links_to(cs / NAME, target)
    assert (alps / OVERLAY_PACK / T.dsf_relpath).is_file()
    assert not (home / "tiles" / OVERLAY_PACK / T.dsf_relpath).exists()


def test_the_copy_says_how_far_it_is_and_a_stop_leaves_the_tile_where_it_was(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copied(monkeypatch)
    pack, cs = _installed(home, xplane)
    alps = tmp_path / "Alps"
    alps.mkdir()
    heard: list[tuple[str, int, int]] = []
    files: list[int] = []

    def stop() -> bool:
        files.append(1)
        return len(files) > 2  # after two files

    with pytest.raises(FilingStoppedError):
        file_tile(
            _entry(pack),
            alps,
            custom_sceneries=[cs],
            library_path=default_library_path(),
            progress=lambda *step: heard.append(step),
            stop=stop,
        )
    assert heard and all(phase == "copy" for phase, _done, _total in heard)
    assert heard[-1][1] < heard[-1][2]  # stopped half way
    assert not (alps / (NAME + PART_SUFFIX)).exists() and not (alps / NAME).exists()
    assert (pack / "orthostudio.toml").is_file() and links_to(cs / NAME, pack)
    with Library(default_library_path()) as lib:
        assert [r.path for r in lib.list(tile=T, kind="ortho")] == [pack]
    # asked again without a stop, it is copied, then read back
    heard.clear()
    file_tile(
        _entry(pack),
        alps,
        custom_sceneries=[cs],
        library_path=default_library_path(),
        progress=lambda *step: heard.append(step),
    )
    assert [p for p, _d, _t in heard][-1] == "check" and heard[-1][1] == heard[-1][2]


def test_a_copy_that_reads_back_wrong_is_taken_away(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disk that does not give back what was written: the copy goes, the tile stays."""
    _copied(monkeypatch)
    pack, cs = _installed(home, xplane)
    alps = tmp_path / "Alps"
    alps.mkdir()
    real = filing.send_to_disk

    def worn(p: Path) -> None:
        if p.suffix == ".dds":
            p.write_bytes(b"DDS worn")
        real(p)

    monkeypatch.setattr(filing, "send_to_disk", worn)
    with pytest.raises(filing.OsxpError) as err:
        file_tile(_entry(pack), alps, custom_sceneries=[cs], library_path=default_library_path())
    assert err.value.code == "SYS_TILE_COPY_DIFFERS"
    assert not (alps / (NAME + PART_SUFFIX)).exists() and not (alps / NAME).exists()
    assert links_to(cs / NAME, pack) and (pack / "textures" / "a.dds").is_file()


@pytest.mark.anyio
async def test_a_whole_copy_a_stop_left_is_taken_and_another_folder_of_its_name_refused(
    app: Any, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application, _mgr = app
    _copied(monkeypatch)
    pack, cs = _installed(home, xplane)
    whole = tmp_path / "Whole"
    shutil.copytree(pack, whole / NAME)
    other = tmp_path / "Other"
    _pack(other, key="k2")  # another build of the tile, under its name
    cut = tmp_path / "Cut"
    shutil.copytree(pack, cut / NAME)
    (cut / NAME / "textures" / "a.dds").write_bytes(b"DDS")  # a copy cut short
    async with client_for(application) as c:
        for folder in (other, cut):
            r = await c.post(
                f"/api/library/{T.name}/file", json={"path": str(pack), "folder": str(folder)}
            )
            assert r.status_code == 422 and r.json()["error"]["code"] == "SYS_TILE_NAME_TAKEN"
        assert links_to(cs / NAME, pack)
        plan = await c.post(
            "/api/library/file-plan",
            json={"tiles": [{"name": NAME, "path": str(pack)}], "folder": str(whole)},
        )
        assert plan.json()["tiles"][0]["how"] == "reuse"
        r = await c.post(
            f"/api/library/{T.name}/file", json={"path": str(pack), "folder": str(whole)}
        )
    assert r.status_code == 200, r.text
    assert links_to(cs / NAME, whole / NAME) and not pack.exists()


@pytest.mark.anyio
async def test_a_folder_no_tile_goes_into_is_refused_and_nothing_changes(
    app: Any, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside X-Plane's Custom Scenery, the workshop itself, a folder inside a tile's folder,
    X-Plane running, a disk without room: refused, and the tile is where it was."""
    application, _mgr = app
    pack, cs = _installed(home, xplane)
    other = _pack(tmp_path / "Filed", key="k2")
    alps = tmp_path / "Alps"
    alps.mkdir()
    async with client_for(application) as c:
        for folder, code in (
            (cs, "SYS_FOLDER_IN_CUSTOM_SCENERY"),
            (home / "tiles", "SYS_FOLDER_IS_ATELIER"),
            (other / "textures", "SYS_FOLDER_IN_TILE"),
        ):
            r = await c.post(
                f"/api/library/{T.name}/file", json={"path": str(pack), "folder": str(folder)}
            )
            assert r.status_code == 422 and r.json()["error"]["code"] == code, (folder, r.text)
        _copied(monkeypatch)
        monkeypatch.setattr(
            filing.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**12, 10**12, 10)
        )
        r = await c.post(
            f"/api/library/{T.name}/file", json={"path": str(pack), "folder": str(alps)}
        )
        assert r.json()["error"]["code"] == "SYS_DISK_FULL"
        monkeypatch.setattr(packs, "xplane_running", lambda: True)
        r = await c.post(
            f"/api/library/{T.name}/file", json={"path": str(pack), "folder": str(alps)}
        )
        assert r.status_code == 409 and r.json()["error"]["code"] == "XP_RUNNING"
        rows = await _rows(c)
    assert rows["ortho"]["path"] == str(pack) and links_to(cs / NAME, pack)
    assert list(alps.iterdir()) == []


@pytest.mark.anyio
async def test_the_plan_says_what_each_tile_does_before_anything_is_done(
    app: Any, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application, _mgr = app
    _copied(monkeypatch)
    pack = _built(home)
    imported = tmp_path / "Ortho4XP" / "Tiles" / "zOrtho4XP_+44+005"
    gone = tmp_path / "Gone" / "zOrthoStudio_+44+006"
    with Library(default_library_path()) as lib:
        lib.register(TileRef(44, 5), "BI", 16, imported, "ortho4xp", None)
        lib.register(TileRef(44, 6), "BI", 16, gone, "osxp", {"dsf": "k3"})
    alps = tmp_path / "Alps"
    alps.mkdir()
    tiles = [
        {"name": NAME, "path": str(pack)},
        {"name": "zOrtho4XP_+44+005", "path": str(imported)},
        {"name": "zOrthoStudio_+44+006", "path": str(gone)},
    ]
    async with client_for(application) as c:
        plan = (
            await c.post("/api/library/file-plan", json={"tiles": tiles, "folder": str(alps)})
        ).json()
        there = await c.post(
            "/api/library/file-plan", json={"tiles": tiles[:1], "folder": str(home / "Elsewhere")}
        )
    hows = {r["tile"]: r["how"] for r in plan["tiles"]}
    assert hows == {T.name: "copy", "+44+005": "imported", "+44+006": "missing"}
    assert plan["copy_bytes"] == filing.pack_bytes(pack) > 0 and plan["room"] is True
    assert there.status_code == 422  # a folder that is not there
    assert list(alps.iterdir()) == []  # it reads only


@pytest.mark.anyio
async def test_one_filing_at_a_time_and_nothing_else_changes_a_tile_meanwhile(
    app: Any, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While a tile is filed: how far it is can be asked, it can be stopped, and a second filing,
    a change of a tile, a build or "Free space" (the atelier, step 5: the filing moves a tile out of
    the atelier) waits its turn."""
    application, _mgr = app
    pack, _cs = _installed(home, xplane)
    alps = tmp_path / "Alps"
    alps.mkdir()
    started, release = threading.Event(), threading.Event()

    def slow(entry: Any, dest: Path, **kwargs: Any) -> dict[str, Any]:
        kwargs["progress"]("copy", 5, 10)
        started.set()
        release.wait(10)
        if kwargs["stop"]():
            raise FilingStoppedError(entry.tile.name)
        return {"tile": entry.tile.name}

    monkeypatch.setattr(api_app, "file_tile", slow)
    async with client_for(application) as c:
        filed = asyncio.create_task(
            c.post(f"/api/library/{T.name}/file", json={"path": str(pack), "folder": str(alps)})
        )
        assert await asyncio.to_thread(started.wait, 10)
        progress = (await c.get("/api/library/filing")).json()["progress"]
        second = await c.post(
            f"/api/library/{T.name}/file", json={"path": str(pack), "folder": str(alps)}
        )
        install = await c.post(f"/api/library/{T.name}/install", json={"path": str(pack)})
        build = await c.post("/api/jobs", json={"tiles": ["+43+005"]})
        free = await c.post("/api/clean", json={"filed": True})
        stop = await c.post("/api/library/filing/stop", json={})
        release.set()
        r = await filed
        after = (await c.get("/api/library/filing")).json()["progress"]
    assert progress == {"tile": T.name, "phase": "copy", "done": 5, "total": 10}
    for busy in (second, install, build, free):
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "SYS_BUSY", busy.text
    assert "filed elsewhere" in free.json()["error"]["message"]
    assert stop.json() == {"stopping": True}
    assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_FILING_STOPPED"
    assert after is None
    # and a tile is filed only between builds
    monkeypatch.setattr(_mgr, "active", lambda: object())
    async with client_for(application) as c:
        during = await c.post(
            f"/api/library/{T.name}/file", json={"path": str(pack), "folder": str(alps)}
        )
    assert during.status_code == 409 and during.json()["error"]["code"] == "SYS_BUSY", during.text
    assert "between builds" in during.json()["error"]["message"]


# -- the review of 2026-10-05 -------------------------------------------------------------------


def _symlink(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("no symbolic links here")


def _plan(c: Any, pack: Path, folder: Path) -> Any:
    return c.post(
        "/api/library/file-plan",
        json={"tiles": [{"name": NAME, "path": str(pack)}], "folder": str(folder)},
    )


def _file(c: Any, pack: Path, folder: Path) -> Any:
    return c.post(f"/api/library/{T.name}/file", json={"path": str(pack), "folder": str(folder)})


def _lines(cs: Path) -> list[str]:
    """The pack lines of X-Plane's scenery_packs.ini, in their order."""
    text = (cs / "scenery_packs.ini").read_text()
    return [line for line in text.splitlines() if line.startswith("SCENERY_PACK")]


@pytest.mark.anyio
async def test_a_link_of_the_tile_s_name_is_never_taken_for_a_copy_of_it(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """A folder holding a link of the tile's name that leads to the tile itself: taken for a whole
    copy already there, the original was deleted, and the only copy of the tile with it. A link is
    never a copy: refused, and the tile is where it was."""
    application, _mgr = app
    pack = _built(home)
    alps = tmp_path / "Alps"
    alps.mkdir()
    _symlink(pack, alps / NAME)
    async with client_for(application) as c:
        plan = (await _plan(c, pack, alps)).json()
        r = await _file(c, pack, alps)
        rows = await _rows(c)
    assert plan["tiles"][0]["how"] == "taken"
    assert r.status_code == 422 and r.json()["error"]["code"] == "SYS_TILE_NAME_TAKEN", r.text
    assert (pack / "textures" / "a.dds").is_file() and rows["ortho"]["path"] == str(pack)


@pytest.mark.anyio
async def test_x_plane_s_custom_scenery_reached_another_way_is_refused(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """X-Plane's Custom Scenery moved to a big disk and linked back, and its real folder chosen,
    or a folder in it, or its name in another case on a disk that ignores case: still X-Plane's.
    Taken for a folder like any other, the tile's own link there passed for a copy of it, and the
    tile went. Refused, and nothing changes."""
    application, _mgr = app
    real = tmp_path / "Big disk" / "XP scenery"
    real.parent.mkdir(parents=True)
    shutil.move(str(xplane / "Custom Scenery"), str(real))
    _symlink(real, xplane / "Custom Scenery")
    pack, cs = _installed(home, xplane)
    (real / "Mine").mkdir()
    folders = [real, real / "Mine"]
    if (xplane / "custom scenery").is_dir():  # a disk that ignores case (APFS, NTFS)
        folders.append(xplane / "custom scenery")
    async with client_for(application) as c:
        for folder in folders:
            for got in (await _plan(c, pack, folder), await _file(c, pack, folder)):
                assert got.status_code == 422, (folder, got.text)
                assert got.json()["error"]["code"] == "SYS_FOLDER_IN_CUSTOM_SCENERY", folder
    assert links_to(cs / NAME, pack) and (pack / "textures" / "a.dds").is_file()


@pytest.mark.anyio
async def test_a_copy_found_there_is_taken_only_when_it_reads_back_the_same_bytes(
    app: Any, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand copy cut short keeps its files at their full size, written later: its sizes alone
    passed for a whole copy, and the original was deleted. It is read back first, as a copy made
    here is: it differs, so it is refused, and the tile is where it was."""
    application, _mgr = app
    _copied(monkeypatch)
    pack, cs = _installed(home, xplane)
    cut = tmp_path / "Cut"
    shutil.copytree(pack, cut / NAME)
    dds = cut / NAME / "textures" / "a.dds"
    dds.write_bytes(b"\0" * dds.stat().st_size)  # its size, not yet its bytes
    async with client_for(application) as c:
        assert (await _plan(c, pack, cut)).json()["tiles"][0]["how"] == "reuse"
        r = await _file(c, pack, cut)
    assert r.status_code == 422 and r.json()["error"]["code"] == "SYS_TILE_NAME_TAKEN", r.text
    assert links_to(cs / NAME, pack) and (pack / "textures" / "a.dds").read_bytes()[:4] == b"DDS "


@pytest.mark.anyio
async def test_the_workshop_s_own_folders_are_refused(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """A folder inside the workshop's tiles, or one of the data folder's caches, which Free space
    empties whole (the tile filed there would go with them): refused, and nothing changes."""
    application, _mgr = app
    pack, cs = _installed(home, xplane)
    folders = [data_root() / "tiles" / "Alps", data_root() / "chunks", data_root() / "store" / "x"]
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
    async with client_for(application) as c:
        for folder in folders:
            r = await _file(c, pack, folder)
            assert r.status_code == 422, (folder, r.text)
            assert r.json()["error"]["code"] == "SYS_FOLDER_IS_ATELIER", folder
    assert links_to(cs / NAME, pack) and all(not (f / NAME).exists() for f in folders)


@pytest.mark.anyio
async def test_a_tile_not_whole_is_refused_before_anything_of_it_moves(
    app: Any, home: Path, xplane: Path, tmp_path: Path
) -> None:
    """A tile missing a file, or holding another build than the Library's, was moved, then
    refused by the switch: X-Plane no longer found it, and Find again refused it too. It is
    refused first, the plan says so, and nothing moves."""
    application, _mgr = app
    pack, cs = _installed(home, xplane)
    alps = tmp_path / "Alps"
    alps.mkdir()
    (pack / "textures" / "a.dds").unlink()
    async with client_for(application) as c:
        plan = (await _plan(c, pack, alps)).json()
        r = await _file(c, pack, alps)
        rows = await _rows(c)
    assert plan["tiles"][0]["how"] == "not_whole"
    assert r.status_code == 422 and r.json()["error"]["code"] == "SYS_TILE_NOT_WHOLE", r.text
    assert links_to(cs / NAME, pack) and rows["ortho"]["path"] == str(pack)
    assert list(alps.iterdir()) == []
    with Library(default_library_path()) as lib:  # another build than the Library's
        lib.register(T, "BI", 16, pack, "osxp", {"dsf": "k9"})
    assert not filing._is_whole(_entry(pack), pack)


@pytest.mark.anyio
async def test_an_old_folder_that_cannot_be_taken_away_is_said_with_its_path(
    app: Any, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file held by another program keeps the tile's old folder, whole and listed nowhere: the
    receipt names it, for the page to say where it is."""
    application, _mgr = app
    _copied(monkeypatch)
    pack, cs = _installed(home, xplane)
    alps = tmp_path / "Alps"
    alps.mkdir()

    def held(folder: Path) -> None:
        raise PermissionError(13, "held by another program", str(folder / "textures" / "a.dds"))

    monkeypatch.setattr(filing, "_delete_pack_dir", held)
    async with client_for(application) as c:
        r = await _file(c, pack, alps)
    assert r.status_code == 200, r.text
    assert r.json()["left"] == str(pack) and links_to(cs / NAME, alps / NAME)


@pytest.mark.anyio
async def test_a_stop_asked_as_the_filing_starts_is_kept(
    app: Any, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Stop asked while the engine still reads the tile's row was cleared by the filing it
    asked to stop, which went on. Asked once the filing holds its turn, it is kept."""
    application, _mgr = app
    pack, _cs = _installed(home, xplane)
    alps = tmp_path / "Alps"
    alps.mkdir()
    reading, release = threading.Event(), threading.Event()
    read_row = api_app.library_pack

    def slow_row(*args: Any, **kwargs: Any) -> Any:
        reading.set()
        release.wait(10)
        return read_row(*args, **kwargs)

    def filed(entry: Any, dest: Path, **kwargs: Any) -> dict[str, Any]:
        if kwargs["stop"]():
            raise FilingStoppedError(entry.tile.name)
        return {"tile": entry.tile.name}

    monkeypatch.setattr(api_app, "library_pack", slow_row)
    monkeypatch.setattr(api_app, "file_tile", filed)
    async with client_for(application) as c:
        task = asyncio.create_task(_file(c, pack, alps))
        assert await asyncio.to_thread(reading.wait, 10)
        stop = await c.post("/api/library/filing/stop", json={})
        release.set()
        r = await task
    assert stop.json() == {"stopping": True}
    assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_FILING_STOPPED", r.text


@pytest.mark.anyio
@pytest.mark.parametrize("disk", ["same", "other"])
async def test_the_tile_s_line_stays_as_the_user_left_it(
    disk: str, app: Any, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user who disabled the tile in X-Plane and put its line below his other scenery: filed on
    another disk, it was taken out and installed again, enabled, at its default place. Led to the
    copy first, X-Plane keeps the line as it was, on one disk or two."""
    application, _mgr = app
    if disk == "other":
        _copied(monkeypatch)
    pack, cs = _installed(home, xplane)
    ini = cs / "scenery_packs.ini"
    line = f"Custom Scenery/{NAME}/"
    text = ini.read_text().replace(f"SCENERY_PACK {line}\n", "")
    ini.write_text(
        text + f"SCENERY_PACK Custom Scenery/my_airport/\nSCENERY_PACK_DISABLED {line}\n"
    )
    before = _lines(cs)
    alps = tmp_path / "Alps"
    alps.mkdir()
    async with client_for(application) as c:
        r = await _file(c, pack, alps)
    assert r.status_code == 200, r.text
    assert links_to(cs / NAME, alps / NAME)
    after = [x for x in _lines(cs) if "Overlays" not in x]
    assert after == [x for x in before if "Overlays" not in x]


@pytest.mark.anyio
@pytest.mark.parametrize("disk", ["same", "other"])
async def test_a_new_roads_line_takes_the_state_of_the_one_the_tile_came_from(
    disk: str, app: Any, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user of simHeaven X-World turns OrthoStudio XP's roads off: filed in a new folder, the
    tile's roads came back on, in a new overlays line added enabled. That line takes the state of
    the one the tile came from."""
    application, _mgr = app
    if disk == "other":
        _copied(monkeypatch)
    pack, cs = _installed(home, xplane)
    ini = cs / "scenery_packs.ini"
    roads = f"Custom Scenery/{OVERLAY_PACK}/"
    ini.write_text(
        ini.read_text().replace(f"SCENERY_PACK {roads}", f"SCENERY_PACK_DISABLED {roads}")
    )
    alps = tmp_path / "Alps"
    alps.mkdir()
    async with client_for(application) as c:
        r = await _file(c, pack, alps)
    assert r.status_code == 200, r.text
    link = overlay_link(cs, alps / OVERLAY_PACK)
    assert link is not None and link.name != OVERLAY_PACK
    assert f"SCENERY_PACK_DISABLED Custom Scenery/{link.name}/" in _lines(cs)
