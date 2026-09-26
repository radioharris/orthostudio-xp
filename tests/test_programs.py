# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Where OrthoStudio XP finds Triangle4XP and DSFTool (``docs/specs/packaging.md`` section 3): an
installer's copy in ``orthostudio/bin`` before the PATH and the checkout."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

import orthostudio.programs as programs
from orthostudio import doctor
from orthostudio.mesh.rule import triangle_binary
from orthostudio.overlays import find_dsftool
from orthostudio.programs import executable_name, installed_program


def _program(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / executable_name(name)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def package_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An ``orthostudio/bin`` as an installer leaves it, with both programs."""
    folder = tmp_path / "orthostudio" / "bin"
    _program(folder, "Triangle4XP")
    _program(folder, "DSFTool")
    monkeypatch.setattr(programs, "PACKAGE_BIN", folder)
    monkeypatch.delenv("OSXP_TRIANGLE4XP", raising=False)
    return folder


def test_a_checkout_has_no_installed_program() -> None:
    assert not programs.PACKAGE_BIN.exists()
    assert installed_program("Triangle4XP") is None


def test_an_installed_program_is_found_only_when_it_is_there(tmp_path: Path) -> None:
    assert installed_program("DSFTool", bin_dir=tmp_path) is None
    tool = _program(tmp_path, "DSFTool")
    assert installed_program("DSFTool", bin_dir=tmp_path) == tool


def test_the_installed_triangle4xp_comes_after_the_environment_only(
    package_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_path = _program(tmp_path / "elsewhere", "Triangle4XP")
    monkeypatch.setenv("PATH", str(on_path.parent))
    installed = package_bin / executable_name("Triangle4XP")
    assert triangle_binary() == installed
    candidates = doctor._triangle_candidates()
    assert candidates[0] == installed and on_path in candidates
    monkeypatch.setenv("OSXP_TRIANGLE4XP", str(on_path))
    assert triangle_binary() == on_path


def test_the_installed_dsftool_comes_first(package_bin: Path, tmp_path: Path) -> None:
    assert find_dsftool() == package_bin / executable_name("DSFTool")
    # a folder given explicitly (tests) is still the one read
    assert find_dsftool(own_dir=tmp_path / "empty") is None
