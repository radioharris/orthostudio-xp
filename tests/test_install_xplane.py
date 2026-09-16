"""X-Plane detection and running check (``docs/specs/install.md`` section 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orthostudio.install import xplane
from orthostudio.install.xplane import (
    XPLANE_EXECUTABLES,
    custom_scenery_dir,
    default_candidates,
    default_install_file,
    detect_xplane,
    global_scenery_dir,
    is_xplane_dir,
    xplane_running,
)

REAL_XP = Path.home() / "X-Plane 12"


def _make_xp(root: Path) -> Path:
    (root / "Resources").mkdir(parents=True)
    (root / "Custom Scenery").mkdir()
    return root


def test_default_install_file_per_os(tmp_path: Path) -> None:
    home = Path.home()
    assert default_install_file({}, "darwin") == (
        home / "Library" / "Preferences" / "x-plane_install_12.txt"
    )
    assert default_install_file({"LOCALAPPDATA": str(tmp_path)}, "win32") == (
        tmp_path / "x-plane_install_12.txt"
    )
    assert default_install_file({}, "win32") == (
        home / "AppData" / "Local" / "x-plane_install_12.txt"
    )
    assert default_install_file({}, "linux") == home / ".x-plane" / "x-plane_install_12.txt"


def test_default_candidates_per_os(tmp_path: Path) -> None:
    assert Path("/Applications/X-Plane 12") in default_candidates({}, "darwin")
    win = default_candidates({"USERPROFILE": str(tmp_path)}, "win32")
    assert win[0] == Path("C:/X-Plane 12") and tmp_path / "Desktop" / "X-Plane 12" in win
    assert default_candidates({}, "linux") == [Path.home() / "X-Plane 12"]


def test_is_xplane_dir(tmp_path: Path) -> None:
    assert not is_xplane_dir(tmp_path)
    (tmp_path / "Resources").mkdir()
    assert not is_xplane_dir(tmp_path)
    (tmp_path / "Custom Scenery").mkdir()
    assert is_xplane_dir(tmp_path)
    assert global_scenery_dir(tmp_path) == (
        tmp_path / "Global Scenery" / "X-Plane 12 Global Scenery"
    )
    assert custom_scenery_dir(tmp_path) == tmp_path / "Custom Scenery"


def test_detect_prefers_first_valid_line_of_install_file(tmp_path: Path) -> None:
    gone = tmp_path / "Old X-Plane 12"  # listed but deleted
    good = _make_xp(tmp_path / "X-Plane 12")
    install_file = tmp_path / "x-plane_install_12.txt"
    install_file.write_text(f"{gone}/\n{good}/\n", encoding="utf-8")
    assert detect_xplane(install_file=install_file, candidates=[], env={}) == good


def test_detect_falls_back_to_candidates_then_none(tmp_path: Path) -> None:
    cand = _make_xp(tmp_path / "cand")
    missing = tmp_path / "no-such-file.txt"
    assert detect_xplane(install_file=missing, candidates=[tmp_path / "x", cand], env={}) == cand
    assert detect_xplane(install_file=missing, candidates=[], env={}) is None


def test_detect_env_override_wins(tmp_path: Path) -> None:
    a = _make_xp(tmp_path / "a")
    b = _make_xp(tmp_path / "b")
    install_file = tmp_path / "f.txt"
    install_file.write_text(f"{b}/\n")
    env = {"OSXP_XPLANE_DIR": str(a)}
    assert detect_xplane(install_file=install_file, candidates=[], env=env) == a
    # An override that is not an X-Plane folder is skipped, not trusted.
    env = {"OSXP_XPLANE_DIR": str(tmp_path / "nope")}
    assert detect_xplane(install_file=install_file, candidates=[], env=env) == b


def test_running_compares_base_names_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xplane, "process_names", lambda platform=None: {"python3", "X-Plane"})
    assert xplane_running() is True
    monkeypatch.setattr(xplane, "process_names", lambda platform=None: {"osxp", "X-Plane 12"})
    assert xplane_running() is False
    monkeypatch.setattr(xplane, "process_names", lambda platform=None: set())
    assert xplane_running() is False
    assert {"X-Plane", "X-Plane-x86_64", "X-Plane.exe"} == set(XPLANE_EXECUTABLES)


def test_ps_and_tasklist_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    ps_out = "/Users/x/X-Plane 12/X-Plane.app/Contents/MacOS/X-Plane\n/bin/zsh\n\n"
    monkeypatch.setattr(xplane, "_run", lambda cmd: ps_out)
    assert xplane._ps_names() == {"X-Plane", "zsh"}
    csv = '"X-Plane.exe","1234","Console","1","1,024 K"\r\n"cmd.exe","5","Console","1","1 K"\r\n'
    monkeypatch.setattr(xplane, "_run", lambda cmd: csv)
    assert xplane._tasklist_names() == {"X-Plane.exe", "cmd.exe"}
    monkeypatch.setattr(xplane, "_run", lambda cmd: None)
    assert xplane._ps_names() is None and xplane._tasklist_names() is None
    assert xplane.process_names("win32") == set()


def test_process_listing_failure_means_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cmd: list[str], **kw: object) -> None:
        raise OSError("no ps")

    monkeypatch.setattr(xplane.subprocess, "run", boom)
    assert xplane._run(["ps"]) is None
    assert xplane.process_names("darwin") == set()


# ----------------------------------------------------------------- this machine


@pytest.mark.xplane
@pytest.mark.skipif(not REAL_XP.is_dir(), reason="reference X-Plane 12 install not present")
def test_detect_real_install() -> None:
    assert detect_xplane() == REAL_XP
    assert is_xplane_dir(REAL_XP)
    assert (global_scenery_dir(REAL_XP) / "Earth nav data").is_dir()


def test_real_running_is_bool() -> None:
    assert isinstance(xplane_running(), bool)
