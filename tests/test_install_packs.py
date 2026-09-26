# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Linking a pack into a (temporary) Custom Scenery and undoing it (spec section 4)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from orthostudio.errors import OsxpError
from orthostudio.install import packs
from orthostudio.install.packs import install_pack, is_link, uninstall_pack
from orthostudio.install.scenery_packs import GLOBAL_AIRPORTS, SceneryPacks

INI = (
    b"I\n1000 Version\nSCENERY\n\n"
    b"SCENERY_PACK Custom Scenery/Airport A/\n"
    b"SCENERY_PACK *GLOBAL_AIRPORTS*\n"
    b"SCENERY_PACK Custom Scenery/Lib/\n"
    b"SCENERY_PACK_DISABLED Custom Scenery/z_autoortho/\n"
)


@pytest.fixture
def custom_scenery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(packs, "xplane_running", lambda: False)
    cs = tmp_path / "X-Plane 12" / "Custom Scenery"
    cs.mkdir(parents=True)
    (cs / "scenery_packs.ini").write_bytes(INI)
    return cs


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    p = tmp_path / "builds" / "zOrthoStudio_+43+005"
    (p / "Earth nav data" / "+40+000").mkdir(parents=True)
    (p / "Earth nav data" / "+40+000" / "+43+005.dsf").write_bytes(b"XPLNEDSF")
    (p / "terrain").mkdir()
    return p


def _names(cs: Path) -> list[str]:
    return SceneryPacks.load(cs / "scenery_packs.ini").names()


def test_install_links_and_activates(custom_scenery: Path, pack: Path, link_kind: str) -> None:
    target = install_pack(pack, custom_scenery)
    assert target == custom_scenery / "zOrthoStudio_+43+005"
    assert is_link(target) and os.path.samefile(os.path.realpath(target), pack)
    assert os.path.isjunction(target) == (link_kind == "junction")
    assert (target / "Earth nav data" / "+40+000" / "+43+005.dsf").is_file()
    assert _names(custom_scenery) == [
        "Airport A",
        GLOBAL_AIRPORTS,
        "Lib",
        "zOrthoStudio_+43+005",
        "z_autoortho",
    ]
    assert (custom_scenery / "scenery_packs.ini.bak").read_bytes() == INI
    # Idempotent: same link, ini untouched.
    ini_bytes = (custom_scenery / "scenery_packs.ini").read_bytes()
    assert install_pack(pack, custom_scenery) == target
    assert (custom_scenery / "scenery_packs.ini").read_bytes() == ini_bytes


def test_install_overlay_goes_before_ortho(
    custom_scenery: Path, pack: Path, tmp_path: Path
) -> None:
    install_pack(pack, custom_scenery)
    ovl = tmp_path / "builds" / "yOrthoStudio_Overlays"
    (ovl / "Earth nav data" / "+40+000").mkdir(parents=True)
    install_pack(ovl, custom_scenery)
    assert _names(custom_scenery)[2:5] == ["Lib", "yOrthoStudio_Overlays", "zOrthoStudio_+43+005"]


def test_install_refuses_while_xplane_runs(
    custom_scenery: Path, pack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(packs, "xplane_running", lambda: True)
    with pytest.raises(OsxpError) as exc:
        install_pack(pack, custom_scenery)
    assert exc.value.code == "XP_RUNNING"
    assert not (custom_scenery / "zOrthoStudio_+43+005").exists()
    with pytest.raises(OsxpError) as exc:
        uninstall_pack("zOrthoStudio_+43+005", custom_scenery)
    assert exc.value.code == "XP_RUNNING"


def test_install_conflicts_with_foreign_folder_or_link(
    custom_scenery: Path, pack: Path, tmp_path: Path
) -> None:
    (custom_scenery / "zOrthoStudio_+43+005").mkdir()
    with pytest.raises(OsxpError) as exc:
        install_pack(pack, custom_scenery)
    assert exc.value.code == "XP_PACK_CONFLICT" and exc.value.severity.value == "blocking"
    assert (custom_scenery / "zOrthoStudio_+43+005").is_dir()  # never deleted
    (custom_scenery / "zOrthoStudio_+43+005").rmdir()
    other = tmp_path / "other"
    other.mkdir()
    os.symlink(other, custom_scenery / "zOrthoStudio_+43+005", target_is_directory=True)
    with pytest.raises(OsxpError) as exc:
        install_pack(pack, custom_scenery)
    assert exc.value.code == "XP_PACK_CONFLICT" and "link elsewhere" in exc.value.message


def test_install_pack_built_in_place_is_a_noop(custom_scenery: Path) -> None:
    inplace = custom_scenery / "zOrthoStudio_+44+005"
    (inplace / "Earth nav data").mkdir(parents=True)
    assert install_pack(inplace, custom_scenery) == inplace
    assert not is_link(inplace) and "zOrthoStudio_+44+005" in _names(custom_scenery)


def test_install_copy_mode(custom_scenery: Path, pack: Path) -> None:
    target = install_pack(pack, custom_scenery, link=False)
    assert target.is_dir() and not is_link(target)
    assert (target / "Earth nav data" / "+40+000" / "+43+005.dsf").read_bytes() == b"XPLNEDSF"
    assert not list(custom_scenery.glob("*.tmp-*"))
    # Uninstall refuses a real folder unless asked, then removes it.
    with pytest.raises(OsxpError) as exc:
        uninstall_pack("zOrthoStudio_+43+005", custom_scenery)
    assert exc.value.code == "XP_PACK_CONFLICT" and target.is_dir()
    assert uninstall_pack("zOrthoStudio_+43+005", custom_scenery, remove_copy=True) is True
    assert not target.exists() and "zOrthoStudio_+43+005" not in _names(custom_scenery)


def test_uninstall_link_and_ini_line(custom_scenery: Path, pack: Path, link_kind: str) -> None:
    target = install_pack(pack, custom_scenery)
    assert is_link(target), link_kind
    assert uninstall_pack("zOrthoStudio_+43+005", custom_scenery) is True
    assert not target.exists() and not is_link(target)
    assert pack.is_dir() and (pack / "Earth nav data").is_dir()  # the build survives
    assert _names(custom_scenery) == ["Airport A", GLOBAL_AIRPORTS, "Lib", "z_autoortho"]
    assert uninstall_pack("zOrthoStudio_+43+005", custom_scenery) is False


def test_uninstall_never_removes_a_folder_without_earth_nav_data(custom_scenery: Path) -> None:
    (custom_scenery / "zOrthoStudio_+45+005" / "stuff").mkdir(parents=True)
    with pytest.raises(OsxpError) as exc:
        uninstall_pack("zOrthoStudio_+45+005", custom_scenery, remove_copy=True)
    assert exc.value.code == "XP_PACK_CONFLICT"
    assert (custom_scenery / "zOrthoStudio_+45+005" / "stuff").is_dir()


def test_install_without_ini_update(custom_scenery: Path, pack: Path) -> None:
    install_pack(pack, custom_scenery, update_ini=False)
    assert (custom_scenery / "scenery_packs.ini").read_bytes() == INI


def test_install_bad_inputs(custom_scenery: Path, pack: Path, tmp_path: Path) -> None:
    with pytest.raises(OsxpError) as exc:
        install_pack(tmp_path / "missing", custom_scenery)
    assert exc.value.code == "SYS_WORKING_DIR_INVALID"
    with pytest.raises(OsxpError) as exc:
        install_pack(pack, tmp_path / "no Custom Scenery")
    assert exc.value.code == "XP_DIR_NOT_FOUND"


def test_link_failure_is_coded(
    custom_scenery: Path, pack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*a: object, **k: object) -> None:
        raise PermissionError("no")

    monkeypatch.setattr(packs, "_symlink", refuse)
    if os.name == "nt":  # the junction fallback is refused too
        import _winapi

        monkeypatch.setattr(_winapi, "CreateJunction", refuse)
        monkeypatch.setattr(packs.subprocess, "run", refuse)
    with pytest.raises(OsxpError) as exc:
        install_pack(pack, custom_scenery)
    assert exc.value.code == "XP_LINK_FAILED" and "PermissionError" in exc.value.message
    assert not os.path.lexists(custom_scenery / pack.name)


windows_only = pytest.mark.skipif(os.name != "nt", reason="a junction is Windows'")


@windows_only
@pytest.mark.usefixtures("no_symlink_privilege")
def test_cmd_exit_code_does_not_decide_the_junction(
    custom_scenery: Path, pack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user's install failed although mklink said it had made the junction (2026-09-15): an
    AutoRun command of cmd that leaves an error level makes cmd exit with 1 after it."""
    import _winapi

    def unavailable(*a: object, **k: object) -> None:
        raise OSError(1, "CreateJunction refused")

    run = subprocess.run

    def autorun_left_an_error_level(*a: Any, **k: Any) -> subprocess.CompletedProcess[bytes]:
        done = run(*a, **k)
        return subprocess.CompletedProcess(done.args, 1, done.stdout, done.stderr)

    monkeypatch.setattr(_winapi, "CreateJunction", unavailable)
    monkeypatch.setattr(packs.subprocess, "run", autorun_left_an_error_level)
    target = install_pack(pack, custom_scenery)
    assert os.path.isjunction(target) and (target / "terrain").is_dir()


@windows_only
@pytest.mark.usefixtures("no_symlink_privilege")
def test_under_redirection_guard_the_error_says_to_open_the_app_again(
    custom_scenery: Path, pack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app the last page of its installer opened inherited Inno Setup's RedirectionGuard: it
    made each junction and could not follow it (2026-09-15)."""
    from orthostudio.fsutil import REDIRECTION_GUARD_REMEDY

    monkeypatch.setattr(packs, "_same_dir", lambda a, b: False)  # what the guard does to the app
    monkeypatch.setattr(packs, "redirection_guard", lambda: True)
    with pytest.raises(OsxpError) as exc:
        install_pack(pack, custom_scenery)
    assert exc.value.code == "XP_LINK_FAILED" and exc.value.remedy == REDIRECTION_GUARD_REMEDY
    assert "RedirectionGuard" in exc.value.message
    assert not os.path.lexists(custom_scenery / pack.name)


@windows_only
@pytest.mark.usefixtures("no_symlink_privilege")
def test_a_junction_that_fails_leaves_nothing_behind(
    custom_scenery: Path, pack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import _winapi

    def halfway(src: str, dst: str) -> None:
        os.mkdir(dst)  # CreateJunction makes the directory, then fails to make it a junction
        raise OSError(13, "Access is denied")

    def no_cmd(*a: object, **k: object) -> None:
        raise FileNotFoundError("cmd")

    monkeypatch.setattr(_winapi, "CreateJunction", halfway)
    monkeypatch.setattr(packs.subprocess, "run", no_cmd)
    with pytest.raises(OsxpError) as exc:
        install_pack(pack, custom_scenery)
    assert exc.value.code == "XP_LINK_FAILED" and "no junction was made" in exc.value.message
    assert not os.path.lexists(custom_scenery / pack.name)
