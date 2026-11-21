# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Half of the processor for the clouds (``expert.clouds_half_processor``): OpenCV's threads and
the textures Apply makes at once, read once; never on a Mac, whose OpenCV takes no limit."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from orthostudio.clouds import laying, opencv
from orthostudio.config import load_settings, save_settings


def _choose(home: Path, half: bool) -> None:
    s = load_settings()
    expert = s.expert.model_copy(update={"clouds_half_processor": half})
    save_settings(s.model_copy(update={"expert": expert}))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("OSXP_HOME", str(h))
    return h


def test_the_choice_is_read_once_and_halves_what_apply_makes_at_once(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(opencv, "HALVES", True)
    assert opencv.half_processor() is False and laying.at_once() == laying.LAYING_AT_ONCE
    opencv.half_processor.cache_clear()  # the next start
    _choose(home, True)
    assert opencv.half_processor() is True and laying.at_once() == laying.LAYING_AT_ONCE // 2
    # unticked while the app runs: OpenCV's threads were set, so it waits for the next start
    _choose(home, False)
    assert opencv.half_processor() is True
    opencv.half_processor.cache_clear()
    assert opencv.half_processor() is False


def test_a_mac_never_halves_even_when_its_settings_say_so(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settings file brought from another computer: the Mac's OpenCV would ignore the limit."""
    monkeypatch.setattr(opencv, "HALVES", False)
    _choose(home, True)
    assert opencv.half_processor() is False and laying.at_once() == laying.LAYING_AT_ONCE


def test_an_unreadable_settings_file_leaves_the_whole_processor(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(opencv, "HALVES", True)
    (home / "config.toml").write_text("[expert\nclouds_half_processor = true\n", encoding="utf-8")
    assert opencv.half_processor() is False


@pytest.mark.parametrize(
    ("half", "cpus", "threads"), [(True, 8, [4]), (True, 1, [1]), (False, 8, [])]
)
def test_opencv_takes_half_of_the_logical_processors_as_it_is_loaded(
    home: Path, monkeypatch: pytest.MonkeyPatch, half: bool, cpus: int, threads: list[int]
) -> None:
    set_to: list[int] = []
    fake = types.ModuleType("cv2")
    fake.setNumThreads = set_to.append  # type: ignore[attr-defined]
    fake.getNumberOfCPUs = lambda: cpus  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cv2", fake)
    monkeypatch.setattr(opencv, "_MODULE", None)
    monkeypatch.setattr(opencv, "_installed_here", lambda: True)
    monkeypatch.setattr(opencv, "HALVES", True)
    _choose(home, half)
    assert opencv.opencv() is fake and set_to == threads
    assert opencv.opencv() is fake and set_to == threads  # once, as it is loaded
