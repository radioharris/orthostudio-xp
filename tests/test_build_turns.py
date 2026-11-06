# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A tile built by one program at a time (``pipeline/turns.py``): the second program asked to
build it while the first does is refused before anything is written; a file left by a program
gone counts for nothing."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.home import data_root
from orthostudio.model import TileRef
from orthostudio.pipeline import turns
from orthostudio.pipeline.build import BuildSpec, build_tiles

TILE = TileRef(43, 5)


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OSXP_DATA_DIR", raising=False)
    return tmp_path / "home"


def _file() -> Path:
    return data_root() / "building" / f"{TILE.name}.pid"


def test_a_tile_is_held_inside_the_block_and_let_go_after() -> None:
    with turns.holding([TILE, TILE]):
        assert _file().read_text() == str(os.getpid())
    assert not _file().exists()


def test_a_tile_another_live_program_builds_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    _file().parent.mkdir(parents=True)
    _file().write_text(str(os.getppid()))  # the program that started this one: alive
    spec = BuildSpec(tile=TILE, provider="BI", zl=12, out_dir=tmp_path / "out")
    with pytest.raises(OsxpError) as got:
        build_tiles([spec])
    assert got.value.code == "SYS_TILE_BEING_BUILT"
    assert got.value.context["pid"] == os.getppid() and "+43+005" in got.value.message
    assert _file().read_text() == str(os.getppid())  # still the other program's
    assert not (tmp_path / "out").exists()


def test_a_file_left_by_a_program_gone_is_taken() -> None:
    gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)  # fmt: skip
    _file().parent.mkdir(parents=True)
    _file().write_text(gone.stdout.strip())
    with turns.holding([TILE]):
        assert _file().read_text() == str(os.getpid())
    assert not _file().exists()


def test_one_tile_refused_holds_none_of_the_others() -> None:
    other = TileRef(44, 5)
    _file().parent.mkdir(parents=True)
    _file().write_text(str(os.getppid()))
    with pytest.raises(OsxpError), turns.holding([TileRef(42, 5), TILE, other]):
        pass
    assert sorted(p.name for p in _file().parent.iterdir()) == [f"{TILE.name}.pid"]


def test_nothing_is_made_while_the_data_folder_is_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    away = tmp_path / "unplugged" / "OrthoStudio"
    monkeypatch.setenv("OSXP_DATA_DIR", str(away))
    with pytest.raises(OsxpError) as got, turns.holding([TILE]):
        pass
    assert got.value.code == "CFG_DATA_DIR_MISSING" and not away.parent.exists()
