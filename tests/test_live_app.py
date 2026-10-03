# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP and OrthoStudio XP Live as one app: the question at its first start, the window
it opens after, the button of each window to the other, and the preview's app beside the usual
checkout's."""

from __future__ import annotations

import importlib
import json
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
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

    def open_window(
        port: int, *, browser: bool = False, start: str | None = None, **kw: object
    ) -> str | None:
        assert kw.get("quit_when_closed") is True and "log" in kw  # the app's own start
        shown.append((port, start))
        return answer

    monkeypatch.setattr(web, "open_window", open_window)
    monkeypatch.setattr(app_start, "live_listening", lambda port=app_start.LIVE_PORT: False)
    return shown


def test_live_that_cannot_start_leaves_osxp_s_window(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live's window opened at once, and Live could not be set up behind it: the window closed,
    and OrthoStudio XP's opens in a process of its own (the window library's loop ran here)."""
    import orthostudio.live.web as web

    switch_on("live")
    opened = []

    def fails(port: int, **kw: object) -> str | None:
        raise web.LiveStartError("its port was taken")

    monkeypatch.setattr(web, "open_window", fails)
    monkeypatch.setattr(app_start, "live_listening", lambda port=app_start.LIVE_PORT: False)
    monkeypatch.setattr(app_start, "open_osxp", lambda: opened.append(1) or "started")
    assert app_start.app_start(home / "log") is True and opened == [1]


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


def test_osxp_s_button_opens_live_s_window_whatever_was_chosen(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a Mac, OrthoStudio XP's Live button opens a new instance of the app (``open -n``) asked
    for Live's window: straight to its page, building chosen or the question not answered yet."""
    monkeypatch.setenv(app_start.START_ENV, "live")
    for chosen in ("build", None):
        switch_on(chosen)
        shown = windows(monkeypatch)
        assert app_start.app_start(home / "log")
        assert shown == [(app_start.LIVE_PORT, None)]


def test_on_a_mac_the_other_window_is_this_app_s(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each window's button opens the other in a process of its own. On a Mac macOS opens it from
    the app itself, so that the Dock shows it as this app and not as Python; elsewhere, from a
    terminal, or when that fails, this Python is started aside."""
    app = tmp_path / "OrthoStudio XP.app"
    app.mkdir()
    ran, aside = [], []
    monkeypatch.setattr(app_start.sys, "platform", "darwin")
    monkeypatch.setattr(app_start, "_detached", lambda args, env=None: aside.append((args, env)))
    monkeypatch.setattr(app_start, "live_listening", lambda port=app_start.LIVE_PORT: False)

    def run(cmd, **kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv(app_start.APP_ENV, str(app))
    assert app_start.this_app() == app
    assert app_start.open_live() == "started"
    app_start.open_osxp()
    passed = ["--env", f"OSXP_HOME={home}"]
    assert ran == [
        ["open", "-n", "-a", str(app), "--env", "OSXP_START=live", *passed],
        ["open", "-n", "-a", str(app), "--env", "OSXP_START=build", *passed],
    ]
    assert aside == []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1))
    app_start.open_osxp()  # macOS could not: started aside, as elsewhere
    monkeypatch.delenv(app_start.APP_ENV)  # from a terminal
    assert app_start.this_app() is None
    app_start.open_live()
    assert aside == [
        (["-m", "orthostudio.desktop"], {"OSXP_START": "build"}),
        (["-m", "orthostudio.live", "ui", "--quit-when-closed"], {"OSXP_START": "live"}),
    ]
    monkeypatch.setattr(app_start.sys, "platform", "linux")
    monkeypatch.setenv(app_start.APP_ENV, str(app))
    assert app_start.this_app() is None  # a Mac's only


def test_the_mac_launchers_name_their_app() -> None:
    """What ``this_app`` reads: the installed app's launcher, the checkout's and the preview's."""
    tools = Path(__file__).resolve().parents[1] / "tools" / "package"
    sys.path.insert(0, str(tools))
    import build
    import checkout_app
    import live_preview_app

    assert 'export OSXP_APP="$(dirname "$CONTENTS")"' in build.macos_launcher()
    for launcher in (checkout_app.LAUNCHER, live_preview_app.LAUNCHER):
        assert 'export OSXP_APP="$(cd "$(dirname "$0")/../.." && pwd)"' in launcher


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


def test_an_osxp_window_open_already_comes_to_the_front(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live's Build tiles brings OrthoStudio XP's window when it is open, put away or not, and
    opens one only when none is: each click used to open another (a user saw it, 2026-10-02)."""
    from orthostudio.desktop import ENGINE_PORT

    started, fronted = [], []
    monkeypatch.setattr(app_start, "_other_window", lambda start, aside: started.append(start))
    stop = app_start.osxp_window_listens(ENGINE_PORT, lambda: fronted.append(1))
    try:
        assert app_start.open_osxp() == "front"
        assert app_start.open_osxp() == "front"
        assert fronted == [1, 1] and started == []
        other = app_start.osxp_window_listens(ENGINE_PORT + 10, lambda: fronted.append(2))
        try:  # the preview's window, on a port of its own, is not this app's
            assert app_start.open_osxp() == "front" and fronted == [1, 1, 1]
        finally:
            other()
    finally:
        stop()
    assert not app_start.osxp_window_file(ENGINE_PORT).exists()
    assert app_start.open_osxp() == "started" and started == ["build"]
    # a window gone without a word: its file names a port nobody listens on
    with socket.socket() as gone:
        gone.bind(("127.0.0.1", 0))
        port = gone.getsockname()[1]
    app_start.osxp_window_file(ENGINE_PORT).write_text(json.dumps({"pid": 1, "port": port}))
    assert app_start.open_osxp() == "started" and started == ["build", "build"]


def test_osxp_s_window_listens_for_live_while_it_is_open(home: Path, tmp_path: Path) -> None:
    import orthostudio.desktop as desktop

    stop = desktop._brought_by_live(tmp_path / "log")
    assert stop is not None
    try:
        said = json.loads(app_start.osxp_window_file(desktop.ENGINE_PORT).read_text())
        assert said["pid"] == os.getpid()
    finally:
        stop()
    assert not app_start.osxp_window_file(desktop.ENGINE_PORT).exists()


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
    monkeypatch.setattr(app_start, "open_osxp", lambda: opened.append(1) or "started")
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
        # at once, before this window goes: beside it in its program, the app still there
        assert opened == [1]
        assert (await client.post("/api/live/home", json={"home": "elsewhere"})).status_code == 422
        assert (await client.post("/api/live/osxp")).json() == {"opened": "started"}
    assert opened == [1, 1]


@pytest.mark.anyio
async def test_the_page_s_quit(home: Path) -> None:
    """Quit, as OrthoStudio XP's: its button and its question in the page, then ``POST
    /api/live/close``: Live goes at once when nothing streams (the user asked, 2026-10-03)."""
    control = LiveControl(echo=lambda _: None)
    async with client_for(create_live_app(control)) as client:
        page = (await client.get("/")).text
        assert 'id="quit-btn"' in page and 'id="quit-confirm"' in page
        assert 'id="departure-check"' in page  # SimBrief asked at once for a newer plan
        assert not control.gone()
        assert (await client.post("/api/live/close")).json() == {"state": "idle"}
    assert control.gone() and control.leaving_for is None


def test_quitting_a_stream_waits_for_it_to_be_out() -> None:
    from orthostudio.live import control as control_module

    control = LiveControl(echo=lambda _: None)
    control._state = control_module.STREAMING
    control.close()
    assert control.state == control_module.STOPPING and not control.gone()
    control._state = control_module.IDLE  # X-Plane has quit, the pack is out
    assert control.gone()


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


def test_live_s_process_goes_when_the_installer_asks(tmp_path: Path) -> None:
    """Live's window served without its page opened (``ui --no-open``, as the Windows installer's
    check starts it, tools/package/build.py) ends once asked to quit, as the installer asks it
    before it replaces the app's files (stop_running.pas)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (tmp_path / "live.toml").write_text("enabled = true\n")
    env = {**os.environ, "OSXP_HOME": str(tmp_path)}
    args = ["-m", "orthostudio.live", "ui", "--no-open", "--port", str(port)]
    live = subprocess.Popen([sys.executable, *args], env=env, stdout=subprocess.PIPE, text=True)
    url = f"http://127.0.0.1:{port}/api/live"
    try:
        deadline = time.monotonic() + 60
        while True:
            assert live.poll() is None, live.stdout.read() if live.stdout else ""
            try:
                with urllib.request.urlopen(f"{url}/status", timeout=2) as answer:
                    if answer.status == 200:
                        break
            except OSError:
                assert time.monotonic() < deadline, "Live's window did not answer"
                time.sleep(0.2)
        asked = urllib.request.Request(
            f"{url}/quit", data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(asked, timeout=5) as answer:
            assert answer.status == 202
        assert live.wait(timeout=30) == 0
    finally:
        if live.poll() is None:
            live.kill()
            live.wait()


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
