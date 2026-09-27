# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP and OrthoStudio XP Live as one app: the question at its first start, the window
it opens after, the button of each window to the other, and the preview's app beside the usual
checkout's."""

from __future__ import annotations

import importlib
import json
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import test_api_fakes as fakes
from orthostudio.live import app_start, settings_file
from orthostudio.live.control import LiveControl
from orthostudio.live.settings import LiveSettings, load_settings, save_settings
from orthostudio.live.web import UI_DIR, create_live_app
from test_api_fakes import client_for

anyio_backend = fakes.anyio_backend
NODE = shutil.which("node")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(app_start.START_ENV, raising=False)
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def switch_on(choice: str | None = None) -> None:
    settings_file().write_text("enabled = true\n")
    if choice is not None:
        save_settings(LiveSettings(app_home=choice))


def windows(monkeypatch: pytest.MonkeyPatch, answer: str | None = None) -> list:
    """Live's window, as the app would show it: what it was asked, and the welcome's answer."""
    import orthostudio.live.web as web

    shown: list = []

    def open_window(port: int, *, browser: bool = False, start: str | None = None):
        shown.append((port, start))
        return answer

    monkeypatch.setattr(web, "open_window", open_window)
    monkeypatch.setattr(app_start, "live_listening", lambda port=app_start.LIVE_PORT: False)
    return shown


def test_osxp_as_ever_without_live(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shown = windows(monkeypatch)
    assert not app_start.app_start(home / "log")  # not switched on: OrthoStudio XP's window
    switch_on("build")
    assert not app_start.app_start(home / "log")  # building chosen
    assert shown == []


def test_the_question_the_first_time(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    switch_on()
    shown = windows(monkeypatch, answer=None)
    assert app_start.app_start(home / "log")  # Live's window, the question first
    assert shown == [(app_start.LIVE_PORT, "welcome")]
    shown = windows(monkeypatch, answer="build")
    opened = []
    monkeypatch.setattr(app_start, "open_osxp", lambda: opened.append(1))
    assert app_start.app_start(home / "log")  # answered "build": OrthoStudio XP's opens, apart
    assert opened == [1]
    save_settings(LiveSettings(app_home="live"))
    shown = windows(monkeypatch)
    assert app_start.app_start(home / "log")
    assert shown == [(app_start.LIVE_PORT, None)]  # chosen: straight to Live's page


def test_live_s_button_opens_osxp_s_window(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    switch_on("live")
    shown = windows(monkeypatch)
    monkeypatch.setenv(app_start.START_ENV, "build")
    assert not app_start.app_start(home / "log")
    assert shown == []


def test_a_live_window_open_already_comes_to_the_front(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    switch_on("live")
    shown = windows(monkeypatch)
    asked = []
    monkeypatch.setattr(app_start, "live_listening", lambda port=app_start.LIVE_PORT: True)
    monkeypatch.setattr(app_start, "_ask_live", lambda path, **kw: asked.append(path) or {})
    assert app_start.app_start(home / "log")
    assert asked == ["front"] and shown == []


@pytest.mark.anyio
async def test_osxp_s_button(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI

    opened = []
    monkeypatch.setattr(app_start, "live_state", lambda port=app_start.LIVE_PORT: "off")
    monkeypatch.setattr(app_start, "open_live", lambda: opened.append(1) or "started")
    app = FastAPI()
    app.include_router(app_start.osxp_router())
    async with client_for(app) as client:
        assert (await client.get("/api/live-app")).json() == {"enabled": False}
        assert (await client.post("/api/live-app/open")).status_code == 404
        switch_on()
        assert (await client.get("/api/live-app")).json() == {"enabled": True, "state": "off"}
        assert (await client.post("/api/live-app/open")).json() == {"opened": "started"}
        script = await client.get("/api/live-app/entry.js")
        assert script.headers["content-type"].startswith("text/javascript")
        assert "api/live-app/open" in script.text
    assert opened == [1]


@pytest.mark.anyio
async def test_osxp_s_engine_serves_it_and_its_page_loads_it(home: Path) -> None:
    from orthostudio.api.app import create_app
    from orthostudio.ui import ui_dir

    async with client_for(create_app()) as client:
        assert (await client.get("/api/live-app")).json() == {"enabled": False}
        script = await client.get("/api/live-app/entry.js")
        assert script.status_code == 200 and "live-entry" in script.text
    page = (ui_dir() / "index.html").read_text()
    assert '<script type="module" src="api/live-app/entry.js"></script>' in page


@pytest.mark.anyio
async def test_the_welcome_page_s_answer(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened = []
    monkeypatch.setattr(app_start, "open_osxp", lambda: opened.append(1))
    control = LiveControl(echo=lambda _: None)
    async with client_for(create_live_app(control)) as client:
        page = await client.get("/welcome")
        assert page.status_code == 200 and "welcome.js" in page.text
        answer = await client.post("/api/live/home", json={"home": "live", "remember": False})
        assert answer.json() == {"home": "live"}
        assert load_settings().app_home == "ask" and control.leaving_for is None
        await client.post("/api/live/home", json={"home": "build", "remember": True})
        assert load_settings().app_home == "build"
        assert control.leaving_for == "build"  # the window closes, OrthoStudio XP's opens
        assert (await client.post("/api/live/home", json={"home": "elsewhere"})).status_code == 422
        assert (await client.post("/api/live/osxp")).json() == {"opened": True}
    assert opened == [1]


def test_the_preview_takes_a_port_and_a_log_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """Beside the usual app, whose engine takes 8641 and stops another OrthoStudio XP there."""
    import orthostudio.desktop as desktop

    try:
        monkeypatch.setenv("OSXP_PORT", "8651")
        monkeypatch.setenv("OSXP_LOG_NAME", "serve-live-preview.log")
        importlib.reload(desktop)
        assert desktop.ENGINE_PORT == 8651
        assert desktop.ENGINE_ARGS[-2:] == ("--port", "8651")
        assert desktop.LOG_NAME == "serve-live-preview.log"
    finally:
        monkeypatch.delenv("OSXP_PORT")
        monkeypatch.delenv("OSXP_LOG_NAME")
        importlib.reload(desktop)
    assert desktop.ENGINE_PORT == 8641 and desktop.ENGINE_ARGS == (
        "serve",
        "--no-open",
        "--quit-when-closed",
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="a macOS app")
def test_the_preview_s_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = Path(__file__).resolve().parents[1] / "tools" / "package"
    monkeypatch.syspath_prepend(str(tools))
    import live_preview_app

    fake_repo = tmp_path / "repo"
    (fake_repo / ".venv" / "bin").mkdir(parents=True)
    (fake_repo / ".venv" / "bin" / "orthostudio").write_text("")
    monkeypatch.setattr(live_preview_app, "REPO", fake_repo)
    app = live_preview_app.make_app(tmp_path / "dist")
    assert app.name == "OrthoStudio XP (aperçu Live).app"
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "org.orthostudio-xp.launcher.live-preview"
    launcher = (app / "Contents" / "MacOS" / "orthostudio").read_text()
    assert "export OSXP_PORT=8651" in launcher and "serve-live-preview.log" in launcher
    assert str(fake_repo) in launcher


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_new_pages_parse_and_say_everything_in_both_languages() -> None:
    import re

    for name in ("welcome.js", "osxp-entry.js"):
        subprocess.run([NODE or "node", "--check", str(UI_DIR / name)], check=True)
    script = (
        f"import {{ STRINGS }} from {json.dumps((UI_DIR / 'strings.js').as_uri())};"
        "console.log(JSON.stringify({en: Object.keys(STRINGS.en), fr: Object.keys(STRINGS.fr)}));"
    )
    out = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    keys = json.loads(out.stdout)
    used = set(re.findall(r'data-i18n="([\w.]+)"', (UI_DIR / "welcome.html").read_text()))
    used |= set(re.findall(r'data-i18n="([\w.]+)"', (UI_DIR / "index.html").read_text()))
    assert {"welcome.title", "home.title", "action.osxp"} <= used
    assert used <= set(keys["en"]) and used <= set(keys["fr"]), sorted(used - set(keys["fr"]))
