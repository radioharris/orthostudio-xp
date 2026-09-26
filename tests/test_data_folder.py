# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The data folder (``essential.data_dir``, ``orthostudio.home``): the tiles, the downloaded imagery
and the caches on another disk than OrthoStudio XP's own folder (a user asked, 2026-09-15).
Temporary folders and a fake X-Plane only; nothing of the machine's is read or written."""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import test_api_fakes as fakes
from orthostudio.api.app import _library_rows, create_app
from orthostudio.api.jobs import JobManager
from orthostudio.api.map_api import MapProxy
from orthostudio.cli import app as cli_app
from orthostudio.config.store import default_config_path
from orthostudio.dem.sources import default_elevation_dir, default_memo_path
from orthostudio.doctor import run_doctor
from orthostudio.errors import OsxpError
from orthostudio.home import (
    DATA_DIR_ENV,
    check_data_dir,
    data_root,
    data_root_missing,
    default_chunks_root,
    default_mapcache_root,
    default_store_root,
    default_tiles_root,
    default_work_root,
    require_data_root,
)
from orthostudio.install import default_library_path
from orthostudio.install.scenery_packs import SceneryPacks, pack_kind
from orthostudio.model import OVERLAY_PACK, TileRef
from orthostudio.pipeline.pack import (
    PARKED_OVERLAY,
    install_receipt,
    links_to,
    overlay_states,
    uninstall_receipt,
)
from orthostudio.sources.osm import SnapshotStore
from test_api_app import _osxp_pack
from test_api_fakes import FakeBuild, FakeIndex, client_for, make_spec
from test_api_map import JPEG, Upstream, _client, _files

anyio_backend = fakes.anyio_backend
home = fakes.home
xplane = fakes.xplane


def _choose(home: Path, folder: Path) -> None:
    """``essential.data_dir`` in the home's ``config.toml``, as Settings saves it."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(f'[essential]\ndata_dir = "{folder.as_posix()}"\n')


def _no_links(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
    """``os.link`` on exFAT or FAT32."""
    raise OSError(errno.EPERM, "Operation not permitted")


@pytest.fixture
def app(home: Path) -> Any:
    """The application, its job manager and the paths it showed in the file manager (none opens)."""
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(delay_s=0.2), env_factory=None)
    shown: list[Path] = []
    application = create_app(
        env_factory=None,
        jobs=mgr,
        airports=FakeIndex(),
        settings_path=home / "config.toml",
        reveal=shown.append,
    )
    yield application, mgr, shown
    mgr.close()


# -- where the data goes -------------------------------------------------------------------------


def test_the_heavy_data_goes_to_the_data_folder_and_the_personal_data_stays_home(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OSXP_ELEVATION_DIR", raising=False)
    assert data_root() == home and data_root_missing() is None  # created when needed, as before
    disk = tmp_path / "SSD" / "OrthoStudio"
    disk.mkdir(parents=True)
    _choose(home, disk)
    assert data_root() == disk and data_root_missing() is None and require_data_root() == disk
    roots = (default_store_root(), default_chunks_root(), default_tiles_root(), default_work_root())
    assert roots == (disk / "store", disk / "chunks", disk / "tiles", disk / "work")
    assert default_mapcache_root() == disk / "mapcache"
    assert default_elevation_dir() == disk / "elevation"
    assert default_memo_path() == disk / "dem" / "misses.json"
    assert SnapshotStore().root == disk
    # the settings, the library, the jobs and the zones stay in OrthoStudio XP's own folder
    assert default_config_path().parent == home and default_library_path().parent == home

    # a second save is read at once, in the same clock tick and at the same size
    other = tmp_path / "SSD" / "Orthostudio"
    _choose(home, other)
    assert data_root() == other
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "by-hand"))
    assert data_root() == tmp_path / "by-hand"


def test_a_settings_file_that_cannot_be_read_for_a_moment_changes_nothing(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read that failed once (the file in use for a moment, an antivirus scanning it) was
    remembered as "no folder chosen": the data went to OrthoStudio XP's own folder until the next
    save, which matches what a user saw after an update (2026-09-25). It is not remembered, and the
    folder read before stays."""
    disk = tmp_path / "D" / "OrthoStudio"
    disk.mkdir(parents=True)
    _choose(home, disk)
    config = home / "config.toml"
    real = Path.read_text
    busy = {"now": True}

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self == config and busy["now"]:
            raise PermissionError(errno.EACCES, "in use by another process")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    assert data_root() == home  # the very first read fails: no folder is known yet
    busy["now"] = False
    assert data_root() == disk  # read again, the file unchanged
    other = tmp_path / "D" / "Other"
    other.mkdir()
    _choose(home, other)
    busy["now"] = True
    assert data_root() == disk  # a save that cannot be read yet keeps the folder read before
    busy["now"] = False
    assert data_root() == other


def test_an_unplugged_disk_is_missing_and_nothing_is_made_on_the_computer(
    home: Path, tmp_path: Path
) -> None:
    gone = tmp_path / "Volumes" / "SSD" / "OrthoStudio"
    _choose(home, gone)
    assert data_root_missing() == gone
    with pytest.raises(OsxpError) as info:
        require_data_root()
    assert info.value.code == "CFG_DATA_DIR_MISSING" and info.value.context == {"path": str(gone)}
    disk = {c.name: c for c in run_doctor(offline=True).checks}["disk"]
    assert disk.status == "fail" and str(gone) in disk.summary
    assert not (tmp_path / "Volumes").exists()


@pytest.mark.anyio
async def test_the_map_is_served_without_a_cache_while_the_disk_is_unplugged(
    home: Path, tmp_path: Path
) -> None:
    gone = tmp_path / "Volumes" / "SSD"
    _choose(home, gone)
    up = Upstream()
    async with _client(MapProxy(fetch=up)) as c:
        for _ in range(2):
            r = await c.get("/api/map/BI/3/4/2")
            assert r.status_code == 200 and r.content == JPEG
        assert len(up.requests) == 2  # nothing remembered, nothing written
        assert not (tmp_path / "Volumes").exists() and _files(home) == [home / "config.toml"]
        gone.mkdir(parents=True)  # plugged in again
        assert (await c.get("/api/map/BI/3/4/2")).content == JPEG
        assert _files(gone) == [gone / "mapcache" / "BI" / "3" / "4" / "2"]


# -- choosing it -----------------------------------------------------------------------------------


def test_a_folder_is_accepted_only_where_the_data_can_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disk = tmp_path / "SSD"
    disk.mkdir()
    assert check_data_dir(disk) == disk.resolve() and list(disk.iterdir()) == []
    assert check_data_dir(f"{disk}{os.sep}") == disk.resolve()

    def refused(value: Any, **kwargs: Any) -> tuple[str, str | None]:
        with pytest.raises(OsxpError) as info:
            check_data_dir(value, **kwargs)
        assert info.value.message and info.value.remedy
        return info.value.code, info.value.context.get("why")

    assert refused("OrthoStudio") == ("CFG_DATA_DIR_INVALID", "relative")
    assert refused(tmp_path / "nowhere") == ("CFG_DATA_DIR_MISSING", None)
    (tmp_path / "notes.txt").write_text("mine")
    assert refused(tmp_path / "notes.txt") == ("CFG_DATA_DIR_INVALID", "file")
    cs = tmp_path / "X-Plane 12" / "Custom Scenery"
    (cs / "data").mkdir(parents=True)
    assert refused(cs, custom_scenery=cs) == ("CFG_DATA_DIR_INVALID", "xplane")
    assert refused(cs / "data", custom_scenery=cs) == ("CFG_DATA_DIR_INVALID", "xplane")
    assert check_data_dir(disk, custom_scenery=cs) == disk.resolve()

    # exFAT, FAT32: a texture linked into the store and into its pack would be written three times
    with monkeypatch.context() as m:
        m.setattr(os, "link", _no_links)
        assert refused(disk) == ("CFG_DATA_DIR_INVALID", "links")
    assert list(disk.iterdir()) == []  # the probe went with the refusal

    if os.name != "nt" and os.geteuid() != 0:  # root writes anywhere, Windows ignores the mode
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            assert refused(locked) == ("CFG_DATA_DIR_INVALID", "unwritable")
        finally:
            locked.chmod(0o700)


@pytest.mark.anyio
async def test_settings_save_a_data_folder_between_builds_and_builds_wait_for_its_disk(
    app: Any, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application, mgr, shown = app
    disk = tmp_path / "SSD"
    disk.mkdir()
    async with client_for(application) as c:
        status = (await c.get("/api/status")).json()
        assert status["data_dir"] == {"path": str(home), "chosen": False, "present": True}
        settings = (await c.get("/api/settings")).json()
        assert settings["essential"]["data_dir"] is None

        settings["essential"]["data_dir"] = str(tmp_path / "nowhere")
        r = await c.put("/api/settings", json=settings)
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_DATA_DIR_MISSING"
        settings["essential"]["data_dir"] = str(disk)
        with monkeypatch.context() as m:
            m.setattr(os, "link", _no_links)
            r = await c.put("/api/settings", json=settings)
        err = r.json()["error"]
        assert r.status_code == 422 and err["code"] == "CFG_DATA_DIR_INVALID"
        assert err["context"]["why"] == "links" and "exFAT" in err["message"]
        assert data_root() == home

        job = mgr.start([make_spec("+46+006", home=home)])
        r = await c.put("/api/settings", json=settings)
        assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_BUSY"
        mgr.cancel(job.id)
        assert job.wait(30)

        r = await c.put("/api/settings", json=settings)
        assert r.status_code == 200, r.text
        saved = r.json()
        assert (
            saved["essential"]["data_dir"] == str(disk.resolve()) and data_root() == disk.resolve()
        )
        status = (await c.get("/api/status")).json()
        assert status["data_dir"] == {"path": str(disk.resolve()), "chosen": True, "present": True}
        folder = disk / "tiles" / "zOrthoStudio_+43+005"
        folder.mkdir(parents=True)
        r = await c.post("/api/reveal", json={"path": str(folder)})
        assert r.status_code == 200 and shown == [folder], r.text  # the data folder's may be shown
        saved["essential"]["data_dir"] = f"  {disk.resolve()}  "  # typed with spaces around
        r = await c.put("/api/settings", json=saved)
        assert r.status_code == 200 and r.json()["essential"]["data_dir"] == str(disk.resolve())

        # unplugged: the other settings still save, and nothing is estimated or built
        disk.rename(tmp_path / "SSD away")
        saved["essential"]["zoom_level"] = 17
        r = await c.put("/api/settings", json=saved)
        assert r.status_code == 200, r.text
        assert (await c.get("/api/status")).json()["data_dir"]["present"] is False
        for path in ("/api/plan", "/api/jobs"):
            r = await c.post(path, json={"tiles": ["+43+005"], "zoom_level": 14})
            assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_DATA_DIR_MISSING"
        assert not disk.exists()

        # OrthoStudio XP's own folder again: an empty field, or that folder chosen
        for value in ("", str(home)):
            saved["essential"]["data_dir"] = value
            r = await c.put("/api/settings", json=saved)
            assert r.status_code == 200 and r.json()["essential"]["data_dir"] is None, r.text
            assert data_root() == home


def test_osxp_plan_says_the_data_folder_is_missing(home: Path, tmp_path: Path) -> None:
    _choose(home, tmp_path / "gone")
    result = CliRunner().invoke(
        cli_app,
        ["plan", "--tile", "+43+005", "--provider", "BI", "--zl", "14", "--offline", "--json",
         "--no-overlay", "--no-xp12-rasters", "--out", str(tmp_path / "out")],
    )  # fmt: skip
    assert result.exit_code != 0 and "CFG_DATA_DIR_MISSING" in result.output
    assert not (tmp_path / "gone").exists()


# -- the tiles built before, in another folder -------------------------------------------------

T1, T2 = TileRef(43, 5), TileRef(44, 5)


def _names(cs: Path) -> list[str]:
    return SceneryPacks.load(cs / "scenery_packs.ini").names()


def test_the_tiles_of_two_folders_are_shown_together_each_with_its_roads(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    """The data folder changed while a tile of the one before stays installed: a tile of the new
    folder installs beside it, each folder's overlays pack under a link of its own, and nothing
    has to be deleted first (a user found that asking for it made no sense, 2026-09-15)."""
    cs = xplane / "Custom Scenery"
    library = default_library_path()
    old = _osxp_pack(home, T1.name)
    install_receipt(old, cs, tile=T1, library_path=library)
    new = _osxp_pack(home, T2.name, out=tmp_path / "SSD" / "tiles")

    receipt = install_receipt(new, cs, tile=T2, library_path=library)

    assert receipt["overlay_target"] == str(cs / "yOrthoStudio_Overlays_2")
    assert links_to(cs / OVERLAY_PACK, old.parent / OVERLAY_PACK)
    assert links_to(cs / "yOrthoStudio_Overlays_2", new.parent / OVERLAY_PACK)
    assert links_to(cs / old.name, old) and links_to(cs / new.name, new)
    names = _names(cs)
    overlays = [names.index(OVERLAY_PACK), names.index("yOrthoStudio_Overlays_2")]
    assert max(overlays) < min(names.index(old.name), names.index(new.name))
    assert {t: s.state for t, s in overlay_states(cs).items()} == {T1.name: "own", T2.name: "own"}
    rows = {(r["name"], r["path"]): r["installed"] for r in _library_rows(cs)}
    assert rows == {
        (old.name, str(old)): True,
        (new.name, str(new)): True,
        (OVERLAY_PACK, str(old.parent / OVERLAY_PACK)): True,
        (OVERLAY_PACK, str(new.parent / OVERLAY_PACK)): True,
    }
    assert pack_kind("yOrthoStudio_Overlays_2") == "overlay"
    assert pack_kind("yOrthoStudio_Overlays_1") == pack_kind("yOrthoStudio_Overlays_x") == "ortho"

    # out of X-Plane, the new folder's tile takes its overlays link with it, and only that one
    assert uninstall_receipt(new.name, cs)["overlay_pack_removed"] is True
    assert not os.path.lexists(cs / "yOrthoStudio_Overlays_2")
    assert "yOrthoStudio_Overlays_2" not in _names(cs) and OVERLAY_PACK in _names(cs)
    install_receipt(new, cs, tile=T2, library_path=library)
    assert links_to(cs / "yOrthoStudio_Overlays_2", new.parent / OVERLAY_PACK)


def test_a_tile_built_again_into_the_new_folder_takes_the_place_of_the_old_build(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    cs = xplane / "Custom Scenery"
    library = default_library_path()
    old = _osxp_pack(home, T1.name)
    install_receipt(old, cs, tile=T1, library_path=library)
    new = _osxp_pack(home, T1.name, out=tmp_path / "SSD-tiles")

    install_receipt(new, cs, tile=T1, library_path=library)

    assert links_to(cs / new.name, new) and old.is_dir()  # the old build stays on the disk
    assert (old / PARKED_OVERLAY).is_file()  # its roads out of X-Plane, kept in its folder
    # no tile of the old folder is left in X-Plane: the new folder's overlays take the first name
    assert links_to(cs / OVERLAY_PACK, new.parent / OVERLAY_PACK)
    assert _names(cs).count(new.name) == 1 and _names(cs).count(OVERLAY_PACK) == 1
    rows = {r["path"]: r["installed"] for r in _library_rows(cs) if r["kind"] == "ortho"}
    assert rows == {str(old): False, str(new): True}  # the old one can be deleted, or put back

    install_receipt(old, cs, tile=T1, library_path=library)
    assert links_to(cs / old.name, old) and (new / PARKED_OVERLAY).is_file()
    assert links_to(cs / OVERLAY_PACK, old.parent / OVERLAY_PACK)


def test_the_overlays_name_of_an_unplugged_disk_waits_for_it_and_a_deleted_folder_frees_it(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    cs = xplane / "Custom Scenery"
    library = default_library_path()
    old = _osxp_pack(home, T1.name)
    install_receipt(old, cs, tile=T1, library_path=library)
    away = home / "tiles away"
    (home / "tiles").rename(away)  # the disk unplugged: every link of that folder is broken
    new = _osxp_pack(home, T2.name, out=tmp_path / "SSD" / "tiles")

    receipt = install_receipt(new, cs, tile=T2, library_path=library)

    assert receipt["overlay_target"] == str(cs / "yOrthoStudio_Overlays_2")
    away.rename(home / "tiles")  # plugged in again: both folders' tiles have their roads
    assert {t: s.state for t, s in overlay_states(cs).items()} == {T1.name: "own", T2.name: "own"}

    # that folder deleted by hand, then its tile taken out of X-Plane (the Library's Delete)
    shutil.rmtree(home / "tiles")
    uninstall_receipt(old.name, cs)
    uninstall_receipt(new.name, cs)
    install_receipt(new, cs, tile=T2, library_path=library)
    assert links_to(cs / OVERLAY_PACK, new.parent / OVERLAY_PACK)  # the broken link replaced
    assert not os.path.lexists(cs / "yOrthoStudio_Overlays_2")
