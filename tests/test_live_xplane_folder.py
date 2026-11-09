# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Which X-Plane Live works with on a computer that has two: the one Build uses."""

from __future__ import annotations

from pathlib import Path

import pytest

from orthostudio.api.specs import resolve_xplane
from orthostudio.config.models import Settings
from orthostudio.config.store import load_settings, save_settings
from orthostudio.live import control, logfile, session


def _xplane(folder: Path) -> Path:
    (folder / "Resources").mkdir(parents=True)
    (folder / "Custom Scenery").mkdir()
    return folder


def _name_in_the_settings(folder: Path) -> None:
    settings = Settings()
    essential = settings.essential.model_copy(update={"xplane_dir": str(folder)})
    save_settings(settings.model_copy(update={"essential": essential}))


@pytest.fixture
def found_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The X-Plane the detection finds first, which is not the one flown."""
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    folder = _xplane(tmp_path / "C" / "X-Plane 12")
    monkeypatch.setattr("orthostudio.install.xplane.detect_xplane", lambda: folder)
    return folder


@pytest.fixture
def flown(tmp_path: Path) -> Path:
    return _xplane(tmp_path / "E" / "X-Plane 12")


@pytest.mark.usefixtures("found_first")
def test_live_works_with_the_x_plane_build_uses(flown: Path) -> None:
    _name_in_the_settings(flown)
    assert session.xplane_dir(None) == flown
    assert resolve_xplane(None, load_settings().essential.xplane_dir) == flown


@pytest.mark.usefixtures("found_first")
def test_the_window_shows_the_x_plane_named_in_the_settings(flown: Path) -> None:
    """The line of the window, and the folder its Start X-Plane launches."""
    _name_in_the_settings(flown)
    assert control.gather_checks()["xplane"] == str(flown)


def test_with_nothing_named_live_takes_the_one_detected(found_first: Path) -> None:
    assert session.xplane_dir(None) == found_first


@pytest.mark.usefixtures("found_first")
def test_a_folder_named_and_gone_is_said_never_replaced_by_another(tmp_path: Path) -> None:
    """Its disk unplugged: Live must not install itself into the other X-Plane."""
    _name_in_the_settings(tmp_path / "unplugged" / "X-Plane 12")
    with pytest.raises(session.RefusedError, match="unplugged"):
        session.xplane_dir(None)
    checks = control.gather_checks()
    assert checks["xplane"] is None
    assert "unplugged" in checks["xplane_problem"]


@pytest.mark.usefixtures("found_first")
def test_a_folder_given_on_the_command_line_goes_first(tmp_path: Path, flown: Path) -> None:
    given = _xplane(tmp_path / "D" / "X-Plane 12")
    _name_in_the_settings(flown)
    assert session.xplane_dir(given) == given


@pytest.mark.usefixtures("found_first")
def test_the_log_goes_beside_the_x_plane_named(
    tmp_path: Path, flown: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _name_in_the_settings(flown)
    saved = load_settings()
    monkeypatch.setattr("orthostudio.live.pack.live_root", lambda: tmp_path / "live")
    monkeypatch.setattr("orthostudio.live.scenery.load_settings", lambda: saved)
    monkeypatch.delenv("OSXP_HOME")  # a test window keeps its log in its own folder
    assert logfile.log_path() == flown / logfile.NAME
