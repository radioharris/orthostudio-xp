# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Tiles filed elsewhere (the atelier, step 3, 2026-10-05): ``pipeline/filing.py`` and the routes
``POST /api/library/file-plan``, ``POST /api/library/{name}/file``, ``GET /api/library/filing``,
``POST /api/library/filing/stop``. Temporary folders and fake X-Planes only; nothing of the
machine's is read or written."""

from __future__ import annotations

import asyncio
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest

import test_api_fakes as fakes
from orthostudio.api import app as api_app
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
    a change of a tile or a build waits its turn."""
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
        stop = await c.post("/api/library/filing/stop", json={})
        release.set()
        r = await filed
        after = (await c.get("/api/library/filing")).json()["progress"]
    assert progress == {"tile": T.name, "phase": "copy", "done": 5, "total": 10}
    for busy in (second, install, build):
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "SYS_BUSY", busy.text
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
