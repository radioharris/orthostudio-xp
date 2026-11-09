# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Which X-Plane Live works with on a computer that has two: the one Build uses."""

from __future__ import annotations

from pathlib import Path

import pytest

from orthostudio.api.specs import resolve_xplane
from orthostudio.config.models import Settings
from orthostudio.config.store import load_settings, save_settings
from orthostudio.live import control, logfile, session, settings_file
from orthostudio.live.control import IDLE, STREAMING, LiveControl
from orthostudio.live.web import UI_DIR, create_live_app
from orthostudio.ui import ui_dir
from test_api_fakes import anyio_backend as anyio_backend
from test_api_fakes import client_for


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
    monkeypatch.setattr(control, "xplane_running", lambda: False)
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


# --- Live's Settings ask the same question ------------------------------------------------------


def _control(installed: bool = False) -> tuple[LiveControl, list[Path]]:
    """A window on this computer's folders, and the X-Planes its pack was taken out of."""
    out_of: list[Path] = []
    window = LiveControl(
        checks=lambda: {**control.gather_checks(), "installed": installed},
        uninstall=lambda xplane, log: out_of.append(xplane),
        echo=lambda _: None,
        catalog=lambda root: {},
    )
    return window, out_of


@pytest.mark.anyio
@pytest.mark.usefixtures("found_first")
async def test_live_s_settings_name_the_x_plane_of_build(
    flown: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, _ = _control()
    first = control.gather_checks()["xplane"]
    monkeypatch.setattr(
        "orthostudio.install.xplane.xplane_candidates", lambda **_: [Path(first), flown]
    )
    settings = load_settings()
    essential = settings.essential.model_copy(update={"simbrief_user": "someone"})
    save_settings(settings.model_copy(update={"essential": essential}))
    async with client_for(create_live_app(window)) as client:
        before = (await client.get("/api/live/xplane-folder")).json()
        assert before == {"named": None, "others": [str(flown)]}
        answer = await client.put("/api/live/xplane-folder", json={"path": str(flown)})
        assert answer.status_code == 200
        assert answer.json() == {"named": str(flown.resolve()), "others": [first]}
    saved = load_settings().essential
    assert saved.xplane_dir == str(flown.resolve())
    assert saved.simbrief_user == "someone"  # the rest of the settings, as they were
    assert resolve_xplane(None, saved.xplane_dir) == flown.resolve()  # what Build reads
    assert not settings_file().exists()  # no second setting in Live's own file
    assert window.checks()["xplane"] == str(flown.resolve())  # the window's line, at once


@pytest.mark.anyio
async def test_empty_goes_back_to_the_one_detected(found_first: Path, flown: Path) -> None:
    _name_in_the_settings(flown)
    window, _ = _control()
    async with client_for(create_live_app(window)) as client:
        answer = await client.put("/api/live/xplane-folder", json={"path": "  "})
    assert answer.status_code == 200 and answer.json()["named"] is None
    assert session.xplane_dir(None) == found_first


@pytest.mark.anyio
@pytest.mark.usefixtures("found_first")
async def test_a_folder_that_is_no_x_plane_is_refused_and_nothing_changes(tmp_path: Path) -> None:
    window, out_of = _control(installed=True)
    (tmp_path / "Photos").mkdir()
    async with client_for(create_live_app(window)) as client:
        answer = await client.put(
            "/api/live/xplane-folder", json={"path": str(tmp_path / "Photos")}
        )
    assert answer.status_code == 409 and "Resources" in answer.json()["detail"]
    assert load_settings().essential.xplane_dir is None and out_of == []


@pytest.mark.usefixtures("found_first")
@pytest.mark.parametrize("what", ["streaming", "x-plane running"])
def test_not_while_the_pack_is_in_use(
    what: str, flown: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, out_of = _control(installed=True)
    if what == "streaming":
        window._state = STREAMING
    else:
        monkeypatch.setattr(control, "xplane_running", lambda: True)
    with pytest.raises(session.RefusedError, match="first"):
        window.name_xplane(str(flown))
    assert load_settings().essential.xplane_dir is None and out_of == []


def test_the_x_plane_left_keeps_neither_pack_nor_plugin(found_first: Path, flown: Path) -> None:
    window, out_of = _control(installed=True)
    window.name_xplane(str(flown))
    assert out_of == [found_first]


@pytest.mark.usefixtures("found_first")
def test_nothing_to_take_out_when_no_pack_was_left(flown: Path) -> None:
    window, out_of = _control(installed=False)
    window.name_xplane(str(flown))
    assert out_of == []


def test_naming_the_x_plane_already_used_takes_nothing_out(found_first: Path) -> None:
    """The one detected, named: the same X-Plane, the pack stays."""
    window, out_of = _control(installed=True)
    window.name_xplane(str(found_first))
    assert load_settings().essential.xplane_dir == str(found_first.resolve())
    assert out_of == []


@pytest.mark.usefixtures("found_first")
def test_the_same_answer_changes_nothing_even_while_streaming(flown: Path) -> None:
    """A Save with this folder as it was: its disk may be unplugged, Live may be running."""
    _name_in_the_settings(flown)
    window, out_of = _control(installed=True)
    window._state = STREAMING
    window.name_xplane(str(flown))
    assert window.state == STREAMING and window._state != IDLE and out_of == []


def test_a_save_in_build_never_writes_back_the_folder_it_read_earlier() -> None:
    script = (ui_dir() / "app.js").read_text(encoding="utf-8")
    save = script[script.index("async function saveSettings") :]
    assert save.index("await followXplaneFolder()") < save.index('api("PUT", `/api/settings')


def test_the_question_stands_before_live_s_folder() -> None:
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    assert html.index('id="q-xplane"') < html.index('id="q-folder"')
    assert 'id="set-xplane"' in html and 'id="xplane-choose"' in html
