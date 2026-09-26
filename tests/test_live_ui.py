# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The Live window: its settings beside the switch, its stream's states, its API and its page."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

import pytest

import test_api_fakes as fakes
from live_helpers import make_source
from orthostudio.live import control as control_module
from orthostudio.live import enabled, session, settings_file
from orthostudio.live.control import IDLE, LEAVING, STREAMING, LiveControl
from orthostudio.live.region import setup_region
from orthostudio.live.settings import (
    LiveSettings,
    SettingsFileError,
    load_settings,
    save_settings,
)
from orthostudio.live.streamer import Arrival, Streamer, progress_report
from orthostudio.live.web import UI_DIR, create_live_app
from test_api_fakes import client_for

anyio_backend = fakes.anyio_backend


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


# --- settings -----------------------------------------------------------------------------------


def test_the_settings_go_beside_the_switch(home: Path) -> None:
    settings_file().write_text("enabled = true\n")
    assert load_settings() == LiveSettings()
    save_settings(LiveSettings(disk_budget_gb=50, provider="Arc"))
    saved = tomllib.loads(settings_file().read_text())
    assert saved["enabled"] is True  # the switch, as it was
    assert saved["stream"]["disk_budget_gb"] == 50
    assert enabled()
    assert load_settings().disk_budget_gb == 50
    assert load_settings().provider == "Arc"


def test_a_wrong_value_costs_itself_only(home: Path) -> None:
    settings_file().write_text("[stream]\ndisk_budget_gb = -3\ncache_budget_gb = 40\n")
    got = load_settings()
    assert got.disk_budget_gb == LiveSettings().disk_budget_gb
    assert got.cache_budget_gb == 40


def test_an_unknown_source_is_bing(home: Path) -> None:
    """A source of the user's removed from OrthoStudio XP since: Live goes back to Bing Maps."""
    settings_file().write_text('[stream]\nprovider = "Gone"\n')
    assert load_settings().provider == "BI"


def test_a_file_that_does_not_parse_is_left_alone(home: Path) -> None:
    settings_file().write_text("enabled = [broken\n")
    assert load_settings() == LiveSettings()
    with pytest.raises(SettingsFileError):
        save_settings(LiveSettings())
    assert settings_file().read_text() == "enabled = [broken\n"


# --- Live's folder ------------------------------------------------------------------------------


def test_live_keeps_its_folder_when_osxp_data_moves(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OrthoStudio XP's data folder moved to a disk for tests: Live's pack stays where it is
    (a user, 2026-09-27)."""
    from orthostudio.live.pack import default_cache_path, default_pack_dir, live_root

    monkeypatch.setenv("OSXP_DATA_DIR", str(tmp_path / "external"))
    assert live_root() == home / "live"
    assert default_pack_dir() == home / "live" / "pack"
    save_settings(LiveSettings(folder=str(tmp_path / "chosen")))
    assert live_root() == tmp_path / "chosen"
    assert default_cache_path() == tmp_path / "chosen" / "chunks.sqlite"


def test_a_folder_that_cannot_hold_the_pack(tmp_path: Path) -> None:
    from orthostudio.live.pack import FolderError, check_folder

    with pytest.raises(FolderError, match="not a full path"):
        check_folder(Path("live"))
    with pytest.raises(FolderError, match="plugged in"):
        check_folder(tmp_path / "unplugged" / "live")  # never made on the computer's own disk
    (tmp_path / "a-file").write_text("")
    with pytest.raises(FolderError, match="a file"):
        check_folder(tmp_path / "a-file")
    scenery = tmp_path / "X-Plane 12" / "Custom Scenery"
    scenery.mkdir(parents=True)
    with pytest.raises(FolderError, match="Custom Scenery"):
        check_folder(scenery / "live", custom_scenery=scenery)
    assert not (tmp_path / "live").exists()
    assert check_folder(tmp_path / "live", create=True) == tmp_path / "live"  # APFS here
    assert (tmp_path / "live").is_dir()
    assert list((tmp_path / "live").iterdir()) == []  # the probe cleaned up


def test_a_start_refused_while_the_folder_is_not_there(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from live_helpers import make_xplane

    monkeypatch.setattr(session, "xplane_running", lambda: False)
    save_settings(LiveSettings(folder=str(tmp_path / "unplugged" / "live")))
    xp = make_xplane(tmp_path)
    with pytest.raises(session.RefusedError, match=r"Live's folder: .*plugged in"):
        session.prepare(session.Options(xplane=xp), log=lambda _: None)
    assert not (xp / "Custom Scenery" / "z_osxp_live").exists()  # nothing installed


# --- the stream's states ------------------------------------------------------------------------


def checks(**changed: Any):
    base = {
        "supported": True,
        "xplane": "/X-Plane 12",
        "xplane_problem": None,
        "xplane_running": False,
        "mesh": "/mesh",
        "mesh_found": True,
        "region_ready": True,
        "autoortho_mounted": False,
        "installed": False,
        "plugin": "ready",
        "folder": "/Live",
        "folder_default": "/Live",
        "folder_problem": None,
        "textures_bytes": 2_500_000_000,
        "imagery_bytes": 900_000_000,
    }
    base.update(changed)
    return lambda: dict(base)


REPORT = {
    "data": "none",
    "aircraft": None,
    "camera_km": None,
    "start": None,
    "mbs": 0.0,
    "streamer": {},
}


class FakeStream:
    """The session's steps, without X-Plane: prepare, follow until stopped, finish."""

    def __init__(self, *, refuse: str | None = None, crash: str | None = None) -> None:
        self.refuse, self.crash = refuse, crash
        self.options: session.Options | None = None
        self.finished: list[Path] = []
        self.gave_up: bool | None = None
        self.done: list[str] = []  # deleted, launched

    def prepare(self, options: session.Options, log) -> session.Prepared:
        self.options = options
        if self.refuse:
            raise session.RefusedError(self.refuse)
        log("prepared")
        return session.Prepared(Path("/X-Plane 12"), Path("/pack"), None, None, 0)

    def follow(self, prepared, options, log, *, should_stop, report, stop_hint) -> None:
        report(REPORT)
        if self.crash:
            raise RuntimeError(self.crash)
        while not should_stop():
            time.sleep(0.01)

    def finish(self, xplane: Path, log, *, hint: str, give_up) -> bool:
        self.finished.append(xplane)
        if control_module.xplane_running():
            while not give_up():
                time.sleep(0.01)
            self.gave_up = True
            return False
        return True


def control_for(stream: FakeStream, **changed: Any) -> LiveControl:
    return LiveControl(
        prepare=stream.prepare,
        follow=stream.follow,
        finish=stream.finish,
        uninstall=lambda xplane, log: log("uninstalled"),
        forget_textures=lambda pack, log: stream.done.append("textures"),
        forget_imagery=lambda log: stream.done.append("imagery"),
        launch=lambda xplane: stream.done.append(f"launched {xplane}"),
        checks=checks(**changed),
        settings=lambda: LiveSettings(disk_budget_gb=33),
        echo=lambda _: None,
    )


def wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_start_stream_stop(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    stream = FakeStream()
    c = control_for(stream)
    assert c.start(fresh=True)
    assert not c.start()  # one stream at a time
    wait_for(lambda: c.state == STREAMING)
    status = c.status()
    assert status["stream"] == REPORT
    assert status["api"] == control_module.API
    assert stream.options is not None
    assert stream.options.fresh and stream.options.disk_budget_gb == 33  # the window's settings
    c.stop()
    assert c.wait_idle(5)
    assert stream.finished == [Path("/X-Plane 12")]  # the pack comes out
    assert c.status()["stream"] is None
    assert "prepared" in [line["text"] for line in c.log_after(0)["lines"]]
    assert c.start()  # and again
    c.stop()
    assert c.wait_idle(5)


def test_refused_before_anything_is_installed(home: Path) -> None:
    stream = FakeStream(refuse="AutoOrtho is mounted: quit it first")
    c = control_for(stream)
    assert c.start()
    assert c.wait_idle(5)
    assert c.status()["error"] == "AutoOrtho is mounted: quit it first"
    assert stream.finished == []  # nothing was installed, nothing to take out


def test_a_streamer_that_stops_on_an_error(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    stream = FakeStream(crash="dictionary changed size during iteration")
    c = control_for(stream)
    assert c.start()
    assert c.wait_idle(5)
    assert c.status()["error"] == "RuntimeError: dictionary changed size during iteration"
    text = "\n".join(line["text"] for line in c.log_after(0)["lines"])
    assert "the streamer stopped:" in text and "Traceback" in text
    assert stream.finished  # installed: taken out all the same


def test_stopped_while_xplane_runs(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """X-Plane reads the pack: the stream waits for it to quit, unless left installed."""
    monkeypatch.setattr(control_module, "xplane_running", lambda: True)
    stream = FakeStream()
    c = control_for(stream, xplane_running=True, installed=True)
    assert c.start()
    wait_for(lambda: c.state == STREAMING)
    c.stop()
    wait_for(lambda: c.state == LEAVING)
    c.leave()
    assert c.wait_idle(5)
    assert stream.gave_up
    assert any("stays installed" in line["text"] for line in c.log_after(0)["lines"])


def test_a_pack_left_installed_comes_out_once_xplane_has_quit(home: Path) -> None:
    c = control_for(FakeStream(), xplane_running=True, installed=True)
    assert not c.uninstall()  # X-Plane reads it
    c = control_for(FakeStream(), xplane_running=False, installed=True)
    assert c.uninstall()
    assert c.log_after(0)["lines"][-1]["text"] == "uninstalled"


def test_delete_only_while_nothing_streams(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    stream = FakeStream()
    c = control_for(stream)
    c.delete(textures=True, imagery=False)
    c.delete(textures=False, imagery=True)
    assert stream.done == ["textures", "imagery"]
    assert c.state == IDLE
    assert c.start()
    wait_for(lambda: c.state == STREAMING)
    with pytest.raises(session.RefusedError, match="stop it first"):
        c.delete(textures=True, imagery=True)
    c.stop()
    assert c.wait_idle(5)
    assert stream.done == ["textures", "imagery"]


def test_xplane_starts_only_once_live_is_ready(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    stream = FakeStream()
    c = control_for(stream)
    with pytest.raises(session.RefusedError, match="not ready"):
        c.launch_xplane()  # the pack and the plugin are not in X-Plane yet
    assert c.start()
    wait_for(lambda: c.state == STREAMING)
    c.launch_xplane()
    assert stream.done == ["launched /X-Plane 12"]
    c.stop()
    assert c.wait_idle(5)
    running = control_for(FakeStream(), xplane_running=True)
    running._state = STREAMING
    with pytest.raises(session.RefusedError, match="running already"):
        running.launch_xplane()


def test_the_log_after_a_line(home: Path) -> None:
    c = control_for(FakeStream())
    c.log("one\ntwo")
    c.log("three")
    got = c.log_after(1)
    assert [line["text"] for line in got["lines"]] == ["two", "three"]
    assert got["seq"] == 3


# --- what the streamer reports ------------------------------------------------------------------


def test_the_status_line_and_the_window_read_the_same_figures(tmp_path: Path) -> None:
    from orthostudio.live.cache import ChunkCache
    from orthostudio.live.evict import texture_bytes

    names = ("23296_33152_BI16",)
    pack = tmp_path / "pack"
    setup_region(make_source(tmp_path, tile=(45, 6), textures=names), pack, log=lambda _: None)
    cache = ChunkCache(tmp_path / "c.sqlite")
    try:
        s = Streamer(pack, cache, lambda on_piece: None, finest_quality=0, log=lambda _: None)
        s.built = {f"{names[0]}.dds": 0}
        s.done[0] = 1
        snap = s.snapshot()
        assert snap["built"] == [1, 0, 0, 0, 0]
        assert snap["disk_bytes"] == texture_bytes(0)
        assert "queue empty (urgent 0)" in s.status()
        assert "built L0:1 |" in s.status()
        arrival = Arrival(45.5, 6.5, "LFLL runway 17R", "click", time.time() - 12, held=True)
        values = {"lat": 45.7, "lon": 5.1, "alt_m": 250.0, "gs_ms": 0.0}
        now = time.time()
        report = progress_report(s, values, arrival, now, last_packet=now - 60, mbs=15.5)
        json.dumps(report)  # what the page gets
        assert report["data"] == "stale"  # X-Plane stopped sending: it loads the flight
        assert report["start"]["held"] and report["start"]["name"] == "LFLL runway 17R"
        assert report["start"]["seconds"] == pytest.approx(12, abs=1)
        assert report["aircraft"]["alt_ft"] == pytest.approx(820, abs=1)
        assert report["streamer"] == s.snapshot()
    finally:
        cache.close()


# --- the API ------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_page_and_its_api(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    settings_file().write_text("enabled = true\n")
    c = control_for(FakeStream())
    c._settings = load_settings  # the file, through the API
    async with client_for(create_live_app(c)) as client:
        page = await client.get("/")
        assert page.status_code == 200 and "live/live.js" in page.text
        for path in ("/live/live.js", "/live/strings.js", "/live/live.css", "/static/styles.css"):
            assert (await client.get(path)).status_code == 200, path
        status = (await client.get("/api/live/status")).json()
        assert status["state"] == IDLE and status["checks"]["plugin"] == "ready"
        assert (await client.post("/api/live/start", json={"fresh": False})).status_code == 202
        assert (await client.post("/api/live/start", json={})).status_code == 409
        wait_for(lambda: c.state == STREAMING)
        assert (await client.post("/api/live/stop")).status_code == 202
        assert c.wait_idle(5)

        saved = await client.put("/api/live/settings", json={"disk_budget_gb": 40})
        assert saved.status_code == 200 and saved.json()["disk_budget_gb"] == 40
        assert tomllib.loads(settings_file().read_text())["enabled"] is True
        wrong = await client.put("/api/live/settings", json={"disk_budget_gb": 0})
        assert wrong.status_code == 422 and "disk_budget_gb" in wrong.json()["detail"]
        assert load_settings().disk_budget_gb == 40
        relative = await client.put("/api/live/settings", json={"folder": "Live"})
        assert relative.status_code == 422 and "full path" in relative.json()["detail"]
        away = await client.put("/api/live/settings", json={"folder": str(home / "no" / "disk")})
        assert away.status_code == 422 and "plugged in" in away.json()["detail"]
        chosen = await client.put("/api/live/settings", json={"folder": str(home / "Live")})
        assert chosen.status_code == 200 and chosen.json()["folder"] == str(home / "Live")
        assert load_settings().disk_budget_gb == 40  # the rest as it was


@pytest.mark.anyio
async def test_the_buttons_of_the_page(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delete, Start X-Plane, the sources' list, the folder's dialog and the Finder."""
    import orthostudio.fsutil as fsutil

    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    asked, shown = [], []

    def dialog(prompt: str, start: Path | None) -> Path:
        asked.append(prompt)
        return tmp_path

    monkeypatch.setattr(fsutil, "choose_folder", dialog)
    monkeypatch.setattr(fsutil, "reveal_in_file_manager", shown.append)
    stream = FakeStream()
    c = control_for(stream)
    async with client_for(create_live_app(c)) as client:
        deleted = await client.post("/api/live/delete", json={"textures": True, "imagery": True})
        assert deleted.status_code == 200 and stream.done == ["textures", "imagery"]
        early = await client.post("/api/live/launch-xplane")
        assert early.status_code == 409 and "not ready" in early.json()["detail"]
        sources = (await client.get("/api/live/providers")).json()
        assert sources[0]["code"] == "BI" and any(p["extent"] == "Spain" for p in sources)
        chosen = await client.post("/api/live/choose-folder", json={"prompt": "Live's folder"})
        assert chosen.json() == {"path": str(tmp_path)} and asked == ["Live's folder"]
        revealed = await client.post("/api/live/reveal", json={"path": str(tmp_path)})
        assert revealed.status_code == 200 and shown == [tmp_path]
        gone = await client.post("/api/live/reveal", json={"path": str(tmp_path / "gone")})
        assert gone.status_code == 404


@pytest.mark.anyio
async def test_other_websites_cannot_start_a_stream(home: Path) -> None:
    """OrthoStudio XP's own guard: no other Host, no other website, JSON only."""
    c = control_for(FakeStream())
    async with client_for(create_live_app(c)) as client:
        cross = await client.post("/api/live/start", headers={"Sec-Fetch-Site": "cross-site"})
        assert cross.status_code == 403
        form = await client.post(
            "/api/live/start",
            content="fresh=true",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert form.status_code == 415
        rebound = await client.get("/api/live/status", headers={"Host": "evil.example"})
        assert rebound.status_code == 400
    assert c.state == IDLE


# --- the page -----------------------------------------------------------------------------------

NODE = shutil.which("node")


def _strings() -> dict[str, list[str]]:
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
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_page_says_everything_in_both_languages() -> None:
    keys = _strings()
    assert set(keys["en"]) == set(keys["fr"])
    js = (UI_DIR / "live.js").read_text()
    html = (UI_DIR / "index.html").read_text()
    used = set(re.findall(r'\bt\("([\w.]+)"', js))
    used |= set(re.findall(r'data-i18n(?:-title)?="([\w.]+)"', html))
    used |= {f"flight.data_{d}" for d in ("none", "fresh", "stale")}  # t(`flight.data_${...}`)
    assert used, "no key found"
    assert used <= set(keys["en"]), sorted(used - set(keys["en"]))


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_a_click_on_a_field_title_is_left_to_its_text() -> None:
    """As OrthoStudio XP does it: a title can be selected; a label around its own box keeps it."""
    script = (
        f"import {{ isTitleClick }} from {json.dumps((UI_DIR / 'live.js').as_uri())};"
        "const field = {};"
        "const title = { control: field, contains: () => false };"
        "const around = { control: field, contains: () => true };"
        "const on = (label) => ({ closest: () => label });"
        "console.log(JSON.stringify([isTitleClick(on(title)), isTitleClick(on(around)),"
        " isTitleClick(on(null)), isTitleClick(null)]));"
    )
    out = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.stdout) == [True, False, False, False]
    js = (UI_DIR / "live.js").read_text()
    handler = r"addEventListener\(\"click\",[^}]*isTitleClick\(ev\.target\)[^}]*\}, true\)"
    assert re.search(handler, js)  # in the capture phase, before any other listener


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_page_parses() -> None:
    for name in ("live.js", "strings.js"):
        subprocess.run([NODE or "node", "--check", str(UI_DIR / name)], check=True)


def test_the_page_uses_osxp_parts_and_no_outside_script() -> None:
    html = (UI_DIR / "index.html").read_text()
    assert re.findall(r"<script[^>]*src=\"([^\"]+)\"", html) == ["live/live.js"]
    assert 'href="static/styles.css"' in html  # OrthoStudio XP's own style sheet
    assert "http" not in re.sub(r"<!--.*?-->", "", html, flags=re.S).replace("http-equiv", "")


def test_threads_are_not_left_behind(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    before = {t.name for t in threading.enumerate()}
    c = control_for(FakeStream())
    assert c.start()
    wait_for(lambda: c.state == STREAMING)
    c.stop()
    assert c.wait_idle(5)
    assert "live-stream" not in {t.name for t in threading.enumerate()} - before
