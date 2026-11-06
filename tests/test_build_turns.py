# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A tile built by one program at a time (``pipeline/turns.py``): the second program asked to
build it while the first does is refused before anything is written; the system lets go of the
tile whatever ends the first."""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.home import data_root
from orthostudio.model import TileRef
from orthostudio.pipeline import turns
from orthostudio.pipeline.build import BuildSpec, build_tiles

TILE = TileRef(43, 5)
HOLD = """
import sys
from orthostudio.model import TileRef
from orthostudio.pipeline import turns
with turns.holding([TileRef(43, 5)]):
    print("held", flush=True)
    sys.stdin.read()
"""


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OSXP_DATA_DIR", raising=False)
    return tmp_path / "home"


@contextlib.contextmanager
def another_program() -> Iterator[subprocess.Popen[str]]:
    """Another program building +43+005 for the time of the block."""
    p = subprocess.Popen(
        [sys.executable, "-c", HOLD], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        env=os.environ.copy(),
    )  # fmt: skip
    try:
        assert p.stdout is not None and p.stdout.readline().strip() == "held"
        yield p
    finally:
        assert p.stdin is not None
        p.stdin.close()
        p.wait(timeout=30)


def test_a_tile_is_held_inside_the_block_and_let_go_after() -> None:
    with turns.holding([TILE, TILE]):
        assert (data_root() / "building" / "+43+005.lock").read_text() == str(os.getpid())
    with turns.holding([TILE]):  # let go: anyone takes it again
        pass


def test_a_tile_another_program_builds_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    spec = BuildSpec(tile=TILE, provider="BI", zl=12, out_dir=tmp_path / "out")
    with another_program() as other, pytest.raises(OsxpError) as got:
        build_tiles([spec])
    assert got.value.code == "SYS_TILE_BEING_BUILT"
    assert str(got.value.context["pid"]) == str(other.pid) and "+43+005" in got.value.message
    assert not (tmp_path / "out").exists()


def test_a_program_gone_holds_nothing() -> None:
    with another_program() as other:
        pass
    assert other.returncode == 0
    with turns.holding([TILE]):
        pass


def test_one_tile_refused_holds_none_of_the_others() -> None:
    with another_program():
        with pytest.raises(OsxpError), turns.holding([TileRef(42, 5), TILE, TileRef(44, 5)]):
            pass
        with turns.holding([TileRef(42, 5), TileRef(44, 5)]):  # let go at once
            pass


def test_nothing_is_made_while_the_data_folder_is_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    away = tmp_path / "unplugged" / "OrthoStudio"
    monkeypatch.setenv("OSXP_DATA_DIR", str(away))
    with pytest.raises(OsxpError) as got, turns.holding([TILE]):
        pass
    assert got.value.code == "CFG_DATA_DIR_MISSING" and not away.parent.exists()
