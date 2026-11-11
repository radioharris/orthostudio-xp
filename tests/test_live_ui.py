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
from pydantic import ValidationError

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
    save_settings(LiveSettings(disk_gb=50, provider="Arc"))
    saved = tomllib.loads(settings_file().read_text())
    assert saved["enabled"] is True  # the switch, as it was
    assert saved["stream"]["disk_gb"] == 50
    assert enabled()
    assert load_settings().disk_gb == 50
    assert load_settings().provider == "Arc"


def test_a_wrong_value_costs_itself_only(home: Path) -> None:
    settings_file().write_text('[stream]\ndisk_gb = -3\nprovider = "Arc"\n')
    got = load_settings()
    assert got.disk_gb == LiveSettings().disk_gb
    assert got.provider == "Arc"


def test_the_test_of_the_8192_px_is_off_until_ticked(home: Path) -> None:
    assert LiveSettings().tint_8192 is False
    assert not control_module.options_for(LiveSettings()).tint_8192
    assert control_module.options_for(LiveSettings(tint_8192=True)).tint_8192


def test_the_night_lights_normal_until_chosen(home: Path) -> None:
    assert LiveSettings().night and LiveSettings().night_light == "normal"
    assert control_module.options_for(LiveSettings()).night_light == "normal"
    assert control_module.options_for(LiveSettings(night_light="soft")).night_light == "soft"
    settings_file().write_text('[stream]\nnight_light = "blinding"\nprovider = "Arc"\n')
    assert load_settings().night_light == "normal"  # a wrong value costs itself only


def test_one_disk_space_shared_and_the_two_of_before(home: Path) -> None:
    """One space, as AutoOrtho's cache: half for the textures built, half for the downloaded
    imagery. A file from before, with two spaces, keeps as much as it gave: their sum."""
    space = LiveSettings(disk_gb=40)
    assert (space.textures_gb, space.imagery_gb) == (20, 20)
    settings_file().write_text("[stream]\ndisk_budget_gb = 20\ncache_budget_gb = 20\n")
    assert load_settings().disk_gb == 40
    settings_file().write_text("[stream]\ncache_budget_gb = 30\n")  # the other one's default
    assert load_settings().disk_gb == 50
    settings_file().write_text("[stream]\ndisk_budget_gb = -3\ncache_budget_gb = 40\n")
    assert load_settings().disk_gb == 60  # a wrong one counts as its default, 20
    save_settings(load_settings())  # saved once: one space only in the file
    written = tomllib.loads(settings_file().read_text())["stream"]
    assert written["disk_gb"] == 60 and "disk_budget_gb" not in written


def test_the_start_choice_gone(home: Path) -> None:
    """The start modes went once every file had its size from the start: X-Plane waits for the
    8192 px around the aircraft at every start. A file's start mode is left out, the rest kept,
    and it is gone from the file once saved."""
    settings_file().write_text('[stream]\nstart_mode = "normal"\nmax_zoom = 17\n')
    settings = load_settings()
    assert settings.max_zoom == 17 and "start_mode" not in settings.model_dump()
    save_settings(settings)
    assert "start_mode" not in tomllib.loads(settings_file().read_text())["stream"]


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


def test_a_start_refused_beside_another_program_streaming_ortho(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """XPME or AutoOrtho running: one program only may stream ortho to X-Plane."""
    from live_helpers import make_xplane

    monkeypatch.setattr(session, "xplane_running", lambda: False)
    xp = make_xplane(tmp_path)
    for others, said in (
        (["XPME"], "XPME is running: quit it first"),
        (["XPME", "AutoOrtho"], "XPME and AutoOrtho are running: quit them first"),
    ):
        monkeypatch.setattr(session, "ortho_streamers", lambda _xp, others=others: others)
        with pytest.raises(session.RefusedError, match=said):
            session.prepare(session.Options(xplane=xp), log=lambda _: None)
    assert not (xp / "Custom Scenery" / "z_osxp_live").exists()  # nothing installed


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
        "ortho_streamers": [],
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

    def __init__(
        self, *, refuse: str | None = None, crash: str | None = None, report: dict = REPORT
    ) -> None:
        self.refuse, self.crash, self.report = refuse, crash, report
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

    def follow(
        self,
        prepared,
        options,
        log,
        *,
        should_stop,
        report,
        stop_hint,
        language,
        attach,
        departure=lambda: None,
    ) -> None:
        self.language = language
        self.departure = departure  # what the window asks to prepare, taken by the loop
        report(self.report)
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
        resolve=lambda text: {"LFMN": (43.6584, 7.2159)}.get(text) or _unknown(text),
        simbrief=lambda user: (fakes_plan("LFMN-LFPG"), ""),
        prepare=stream.prepare,
        follow=stream.follow,
        finish=stream.finish,
        uninstall=lambda xplane, log: log("uninstalled"),
        forget_textures=lambda pack, log: stream.done.append("textures"),
        forget_imagery=lambda log: stream.done.append("imagery"),
        launch=lambda xplane: stream.done.append(f"launched {xplane}"),
        checks=checks(**changed),
        settings=lambda: LiveSettings(disk_gb=66),
        echo=lambda _: None,
        catalog=lambda root: {},  # never GitHub from a test
    )


def _unknown(text: str):
    raise ValueError(f"X-Plane knows no airport {text}")


def fakes_plan(name: str):
    from orthostudio.live.flightplan import FlightPlan

    return FlightPlan(name, [(43.6584, 7.2159), (49.0097, 2.5479)])


def wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_start_stream_stop(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    stream = FakeStream()
    c = control_for(stream)
    assert c.start()
    assert not c.start()  # one stream at a time
    wait_for(lambda: c.state == STREAMING)
    status = c.status()
    assert status["stream"] == REPORT
    assert status["api"] == control_module.API
    assert stream.options is not None
    assert not stream.options.fresh  # the window never starts from scratch: its trash deletes first
    assert stream.options.disk_budget_gb == 33  # the window's, halved
    assert stream.options.cache_budget_gb == 33
    assert stream.options.camera_reach_km == 0.0  # sharp that much farther for external views
    assert status["log_file"] is None  # no LogLive.txt for this window
    assert status["platform"] in ("mac", "win", "lin")
    c.log_file = Path("/X-Plane 12/LogLive.txt")
    assert c.status()["log_file"] == str(Path("/X-Plane 12/LogLive.txt"))
    c.stop()
    assert c.wait_idle(5)
    assert stream.finished == [Path("/X-Plane 12")]  # the pack comes out
    assert c.status()["stream"] is None
    assert "prepared" in [line["text"] for line in c.log_after(0)["lines"]]
    assert c.start()  # and again
    c.stop()
    assert c.wait_idle(5)


def test_a_stop_while_preparing_gives_the_plugins_port_back(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orthostudio.live.gate import Gate

    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    stream = FakeStream()
    c = control_for(stream)
    gates: list[Gate] = []

    def prepare(options: session.Options, log) -> session.Prepared:
        gate = Gate(home, None, port=0)  # Live's real gate, on a free port
        gates.append(gate)
        c.stop()  # Stop pressed while the start is being prepared
        return session.Prepared(Path("/X-Plane 12"), Path("/pack"), gate, None, 0)

    c._prepare = prepare
    assert c.start()
    assert c.wait_idle(5)
    assert gates[0].sock.fileno() == -1  # closed: the next Start can take the port


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


def test_busy_while_a_stream_is_on_its_way(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """What keeps the app's Live from going once its page is closed (orthostudio.live.web)."""
    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    c = control_for(FakeStream())
    assert not c.busy()
    assert c.start()
    assert c.busy()
    c.stop()
    assert c.wait_idle(5) and not c.busy()


def test_the_app_s_update_asks_live_to_quit(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Windows installer asks Live to go before it replaces or removes the files
    (tools/package/stop_running.pas): at once while no stream feeds a running X-Plane, the pack
    coming out; while one does, not unless forced, and then without waiting for X-Plane, which
    flies on over what is built, the pack staying installed."""
    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    c = control_for(FakeStream())
    assert c.quit() and c.leaving_for == "quit"  # nothing streams
    stream = FakeStream()
    c = control_for(stream)
    assert c.start()
    wait_for(lambda: c.state == STREAMING)
    assert c.quit()  # X-Plane does not run
    assert c.wait_idle(5) and stream.finished == [Path("/X-Plane 12")] and not stream.gave_up

    monkeypatch.setattr(control_module, "xplane_running", lambda: True)
    stream = FakeStream()
    c = control_for(stream, xplane_running=True, installed=True)
    assert c.start()
    wait_for(lambda: c.state == STREAMING)
    assert not c.quit()  # X-Plane flies with it
    assert c.state == STREAMING and c.leaving_for is None
    assert c.quit(force=True) and c.leaving_for == "quit"
    assert c.wait_idle(5) and stream.gave_up  # the pack stays installed


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
    assert stream.done == [f"launched {Path('/X-Plane 12')}"]
    c.stop()
    assert c.wait_idle(5)
    running = control_for(FakeStream(), xplane_running=True)
    running._state = STREAMING
    with pytest.raises(session.RefusedError, match="running already"):
        running.launch_xplane()

    c.stop()
    assert c.wait_idle(5)


def test_the_window_reads_the_regions_into_their_lists_as_it_opens(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the background, X-Plane or not; what goes wrong there is said, and a start lists what
    is left."""
    asked: list[Any] = []

    def no_xplane(explicit: Path | None) -> Path:
        raise session.RefusedError("no X-Plane here")

    monkeypatch.setattr(session, "xplane_dir", no_xplane)
    monkeypatch.setattr(session, "keep_region_lists", lambda xp, log: asked.append(xp))
    LiveControl(checks=checks(), echo=lambda _: None).keep_region_lists_aside().join(5)
    assert asked == [None]

    def broken(xp: Path | None, log: Any) -> None:
        raise OSError("the disk went away")

    monkeypatch.setattr(session, "keep_region_lists", broken)
    lines: list[str] = []
    LiveControl(checks=checks(), echo=lines.append).keep_region_lists_aside().join(5)
    assert lines == ["the regions could not be read into their lists: the disk went away"]


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
        assert snap["first_level"] == -1  # 8192 px, one zoom above the mesh's
        assert snap["built"] == [0, 1, 0, 0, 0, 0]
        assert snap["disk_bytes"] == texture_bytes(0)
        assert "queue empty (urgent 0)" in s.status()
        assert "built 4096 px: 1 |" in s.status()
        arrival = Arrival(45.5, 6.5, "LFLL runway 17R", "click", time.time() - 12, held=True)
        values = {"lat": 45.7, "lon": 5.1, "alt_m": 250.0, "gs_ms": 0.0, "trk_deg": 275.5}
        now = time.time()
        report = progress_report(s, values, arrival, now, last_packet=now - 60, mbs=15.5)
        assert report["aircraft"]["hdg"] == 275.5  # no heading said: the track
        values["hdg_deg"] = 90.0  # a drone's aircraft: no speed, its track left behind
        report = progress_report(s, values, arrival, now, last_packet=now - 60, mbs=15.5)
        assert report["aircraft"]["hdg"] == 90.0  # the map points it as X-Plane does
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
async def test_the_page_s_status_is_its_sign_of_life_and_front_shows_it(home: Path) -> None:
    """Each status the page asks for (every second) says it is open (``seen``: in the browser,
    the app's Live goes a while after the last); ``front`` shows the page wherever it is (in the
    browser: opened again)."""
    seen, fronts = [], []
    c = control_for(FakeStream())
    app = create_live_app(c, seen=lambda: seen.append(1), front=lambda: fronts.append(1))
    async with client_for(app) as client:
        assert (await client.get("/api/live/status")).status_code == 200
        assert (await client.get("/api/live/status")).status_code == 200
        assert (await client.post("/api/live/front", json={})).status_code == 200
        assert (await client.post("/api/live/presence")).status_code == 200  # the welcome page
    assert len(seen) == 3 and fronts == [1]
    welcome = (UI_DIR / "welcome.js").read_text(encoding="utf-8")
    assert 'fetch("api/live/presence", { method: "POST" })' in welcome


@pytest.mark.anyio
async def test_the_installer_s_quit(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """What the Windows installer sends (stop_running.pas): ``{}``, then ``{"force": true}`` once
    the user said to go on while X-Plane flies with Live."""
    monkeypatch.setattr(control_module, "xplane_running", lambda: True)
    stream = FakeStream()
    c = control_for(stream, xplane_running=True, installed=True)
    async with client_for(create_live_app(c)) as client:
        assert c.start()
        wait_for(lambda: c.state == STREAMING)
        assert (await client.post("/api/live/quit", json={})).status_code == 409
        assert c.state == STREAMING
        assert (await client.post("/api/live/quit", json={"force": True})).status_code == 202
        assert c.wait_idle(5) and c.leaving_for == "quit" and stream.gave_up


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
        # OrthoStudio XP's find bar, text size and settings search, loaded by the page as they are
        for name in ("find.js", "zoom.js", "i18n.js", "settings.js"):
            assert (await client.get(f"/static/{name}")).status_code == 200, name
        assert 'id="settings-search"' in page.text and 'id="find-bar"' in page.text
        # two screens, as OrthoStudio XP's; the imagery source on Live's, before Start
        html = page.text
        assert 'id="screen-live"' in html and 'id="screen-settings"' in html
        assert html.index('id="set-provider"') < html.index('id="start-btn"')
        assert html.index('id="set-provider"') < html.index('id="screen-settings"')
        status = (await client.get("/api/live/status")).json()
        assert status["state"] == IDLE and status["checks"]["plugin"] == "ready"
        assert (await client.post("/api/live/start")).status_code == 202
        assert (await client.post("/api/live/start", json={})).status_code == 409
        wait_for(lambda: c.state == STREAMING)
        assert (await client.post("/api/live/stop")).status_code == 202
        assert c.wait_idle(5)

        saved = await client.put("/api/live/settings", json={"disk_gb": 60})
        assert saved.status_code == 200 and saved.json()["disk_gb"] == 60
        assert tomllib.loads(settings_file().read_text())["enabled"] is True
        wrong = await client.put("/api/live/settings", json={"disk_gb": 0})
        assert wrong.status_code == 422 and "disk_gb" in wrong.json()["detail"]
        assert load_settings().disk_gb == 60
        relative = await client.put("/api/live/settings", json={"folder": "Live"})
        assert relative.status_code == 422 and "full path" in relative.json()["detail"]
        away = await client.put("/api/live/settings", json={"folder": str(home / "no" / "disk")})
        assert away.status_code == 422 and "plugged in" in away.json()["detail"]
        chosen = await client.put("/api/live/settings", json={"folder": str(home / "Live")})
        assert chosen.status_code == 200 and chosen.json()["folder"] == str(home / "Live")
        assert load_settings().disk_gb == 60  # the rest as it was


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


def test_a_departure_prepared_before_x_plane_loads_it(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window's "Prepare" (the user's choice, 2026-09-30): Live started for it where idle, as
    --from; told of it while it streams, once; an airport X-Plane does not know said so."""
    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    stream = FakeStream()
    c = control_for(stream)
    assert c.prepare_departure("  ") == ("empty", "no departure given")
    assert c.prepare_departure("lfxx")[0] == "unknown"
    assert c.prepare_departure(" lfmn ") == (43.6584, 7.2159)  # idle: Live starts, building there
    wait_for(lambda: c.state == STREAMING)
    assert stream.options.start == "LFMN"
    assert c.prepare_departure("LFMN") == (43.6584, 7.2159)  # streaming: the loop takes it, once
    assert stream.departure() == (43.6584, 7.2159, "LFMN")
    assert stream.departure() is None
    c.stop()
    assert c.wait_idle(5.0)


def test_the_departure_offered_is_the_last_simbrief_plan_s(home: Path) -> None:
    """Asked of simbrief.com only with a SimBrief name in Live's settings, at most every
    SUGGEST_S."""
    asked: list[str] = []

    def simbrief(user: str):
        asked.append(user)
        return fakes_plan("LFMN-LFPG"), ""

    c = control_for(FakeStream())
    c._simbrief = simbrief
    assert c.departure_suggestion() == {}  # no SimBrief name: nothing asked
    c._settings = lambda: LiveSettings(disk_gb=66, simbrief_user="pilot")
    plan = {"icao": "LFMN", "plan": "LFMN-LFPG", "briefing": "LFMN-LFPG"}
    assert c.departure_suggestion() == plan
    assert c.departure_suggestion() == plan
    assert c.departure_suggestion(fresh=True) == plan  # the window looking for a new plan...
    assert asked == ["pilot"]
    c._suggested = (c._suggested[0] - control_module.FRESH_S, *c._suggested[1:])
    assert c.departure_suggestion() == plan  # ... asked again after FRESH_S, not SUGGEST_S
    assert asked == ["pilot"]
    assert c.departure_suggestion(fresh=True) == plan
    assert asked == ["pilot", "pilot"]
    # the plan line's button: at once, though asked a moment ago, but not more than every NOW_S
    assert c.departure_suggestion(now=True) == plan
    assert asked == ["pilot", "pilot"]
    c._suggested = (c._suggested[0] - control_module.NOW_S, *c._suggested[1:])
    assert c.departure_suggestion(now=True) == plan
    assert asked == ["pilot", "pilot", "pilot"]


def test_a_new_simbrief_plan_is_another_briefing(home: Path) -> None:
    """The same route planned again is another plan: the window offers it (its briefing)."""
    from orthostudio.live.flightplan import FlightPlan

    briefings = iter(["101", "102"])

    def simbrief(user: str):
        return FlightPlan("LFBO-LFMN", [(43.6, 1.4), (43.7, 7.2)], next(briefings)), ""

    c = control_for(FakeStream())
    c._simbrief = simbrief
    c._settings = lambda: LiveSettings(disk_gb=66, simbrief_user="pilot")
    assert c.departure_suggestion()["briefing"] == "101"
    c._suggested = (c._suggested[0] - control_module.FRESH_S, *c._suggested[1:])
    assert c.departure_suggestion(fresh=True) == {
        "icao": "LFBO", "plan": "LFBO-LFMN", "briefing": "102",
    }  # fmt: skip


@pytest.mark.anyio
async def test_the_window_prepares_a_departure(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    stream = FakeStream()
    c = control_for(stream)
    async with client_for(create_live_app(c)) as client:
        assert (await client.get("/api/live/departure")).json() == {}
        unknown = await client.post("/api/live/prepare", json={"departure": "LFXX"})
        assert unknown.status_code == 404 and "LFXX" in unknown.json()["detail"]
        ok = await client.post("/api/live/prepare", json={"departure": "LFMN"})
        assert ok.status_code == 202
        assert ok.json()["at"] == [43.6584, 7.2159]  # the window's map brought there
    wait_for(lambda: c.state == STREAMING)
    assert stream.options.start == "LFMN"
    c.stop()
    assert c.wait_idle(5.0)


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
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_foot_of_the_page_is_osxp_s_text_size_and_zoom() -> None:
    """The user asked for the foot of OrthoStudio XP's page in Live's (2026-10-03): its text size
    (static/textsize.js), in a browser too, and its zoom (static/zoom.js) in Live's own window,
    bound as OrthoStudio XP binds them, written again when the language changes, in its words."""
    from orthostudio.ui import ui_dir

    def foot(html: str) -> str:
        return html[html.index('<footer class="statusbar') : html.index("</footer>")]

    bar = foot((UI_DIR / "index.html").read_text())
    osxp_bar = foot((ui_dir() / "index.html").read_text())
    for part in (
        'id="text-size"',
        'class="status-text-mark"',
        'id="status-zoom"',
        'data-zoom="out"',
        'data-zoom="level"',
        'data-zoom="in"',
    ):
        assert part in bar and part in osxp_bar, part
    assert bar.index('id="text-size"') < bar.index('id="status-zoom"')  # as in OrthoStudio XP's
    js = (UI_DIR / "live.js").read_text()
    assert 'import("../static/textsize.js")' in js
    assert "textSize.applyTextSize(textSize.savedTextSize());" in js
    assert 'textSizeRefresh = textSize.bindTextSize($("text-size"));' in js
    assert 'zoomRefresh = zoom.bindZoom(toast, $("status-zoom"));' in js
    change = js[js.index('$("lang-select").addEventListener("change"') :]
    change = change[: change.index("\n  });")]
    assert "languageChanged();" in change  # what the page says again, the frame's choice too
    again = js[js.index("function languageChanged() {") :]
    again = again[: again.index("\n}")]
    assert "zoomRefresh?.();" in again and "textSizeRefresh?.();" in again
    # its words are OrthoStudio XP's, in both languages
    keys = [
        "app.text_size",
        "app.zoom_out",
        "app.zoom_out_mac",
        "app.zoom_level",
        "app.zoom_level_mac",
        "app.zoom_in",
        "app.zoom_in_mac",
    ]
    script = (
        "globalThis.document = { documentElement: {} };"
        f"const live = await import({json.dumps((UI_DIR / 'strings.js').as_uri())});"
        f"const osxp = await import({json.dumps((ui_dir() / 'i18n.js').as_uri())});"
        f"const keys = {json.dumps(keys)}; const out = {{}};"
        "for (const lang of ['en', 'fr']) { live.setLanguage(lang); osxp.setLanguage(lang);"
        " out[lang] = keys.map((k) => [live.t(k), osxp.t(k)]); }"
        "console.log(JSON.stringify(out));"
    )
    out = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", script],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    for lang, pairs in json.loads(out.stdout).items():
        for key, (live_text, osxp_text) in zip(keys, pairs, strict=True):
            assert live_text == osxp_text, (lang, key)


def test_the_decal_list_follows_a_change_of_language() -> None:
    # it was filled only with the settings: its none and Live's choice stayed in the language before
    js = (UI_DIR / "live.js").read_text()
    again = js[js.index("function languageChanged() {") :]
    again = again[: again.index("\n}")]
    assert 'fillDecal(decalList, $("set-decal").value)' in again  # the choice on screen kept
    fill = js[js.index("function fillDecal(") :]
    fill = fill[: fill.index("\n}")]
    assert "JSON.stringify([language(), decals])" in fill  # drawn again in the new language


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_page_says_everything_in_both_languages() -> None:
    keys = _strings()
    assert set(keys["en"]) == set(keys["fr"])
    js = (UI_DIR / "live.js").read_text()
    html = (UI_DIR / "index.html").read_text()
    used = set(re.findall(r'\bt\("([\w.]+)"', js))
    used |= set(re.findall(r'data-i18n(?:-title|-placeholder|-aria-label)?="([\w.]+)"', html))
    assert used, "no key found"
    assert used <= set(keys["en"]), sorted(used - set(keys["en"]))


def _views(statuses: list[dict]) -> dict:
    """live.js flightView and buildView of each status, in English and in French, their spaces
    plain (French numbers are grouped by a narrow no-break space)."""
    script = (
        f"import {{ flightView, buildView }} from {json.dumps((UI_DIR / 'live.js').as_uri())};"
        f"import {{ setLanguage }} from {json.dumps((UI_DIR / 'strings.js').as_uri())};"
        f"const statuses = {json.dumps(statuses)}; const out = {{}};"
        "for (const lang of ['en', 'fr']) { setLanguage(lang);"
        " out[lang] = statuses.map((s) => ({ flight: flightView(s), build: buildView(s) })); }"
        "console.log(JSON.stringify(out));"
    )
    out = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", script],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(re.sub(r"\\u00a0|\\u202f|\u00a0|\u202f", " ", out.stdout))


def _streaming(**streamer: object) -> dict:
    """A status while Live runs: an aircraft flying, everything the two cards show."""
    return {
        "state": "streaming",
        "checks": {"xplane_running": True, "imagery_bytes": 2_000_000_000},
        "settings": {"disk_gb": 40},
        "stream": {
            "data": "fresh",
            "aircraft": {"lat": 44.2181, "lon": 1.7052, "alt_ft": 8820.4, "gs_kt": 231.2},
            "camera_km": 2.34,
            "start": {"name": "LFBO", "ready": True, "held": False},
            "mbs": 0.14,
            "notice": None,
            "streamer": {
                "first_level": -1,  # 8192 px, then 4096 to 256 px
                "queued": [0, 1, 0, 0, 0, 6],
                "urgent": 0,
                "building": 24,
                "pieces_awaited": 12,
                "built": [2, 260, 291, 524, 1182, 7332],
                "downloaded": 104530,
                "downloaded_bytes": 1_553_000_000,
                "from_cache": 947,
                "failed": 3,
                "disk_bytes": 2_600_000_000,
                "at_8192": 5,
                "flight_plan": {"name": "LFBO-LFPG", "following": True},
                "screen": {"width": 3024, "fov": 65, "km_4096": 13.2},
                **streamer,
            },
        },
    }


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_flight_and_the_textures_show_every_figure() -> None:
    """The two cards show all the line of each showed before (2026-10-08): X-Plane's state, the
    aircraft's three figures, how far X-Plane's image is sharp, the plan followed, the camera, the
    start; the textures to build by size and the urgent ones, those being built and the pieces
    they wait for, those built by size, what was downloaded with the cache and the failures, the
    rate, the disk and its 8192 px textures."""
    views = _views([_streaming()])
    assert views["fr"][0] == {
        "flight": {
            "state": ["X-Plane envoie sa position", "ok"],
            "figures": [
                {"value": "8 820 ft", "label": "Altitude"},
                {"value": "231 kt", "label": "Vitesse sol"},
                {"value": "44,218 · 1,705", "label": "Position"},
            ],
            "rows": [
                ["Net jusqu'à", "13 km (image de 3 024 px, champ de 65°)"],
                ["Plan de vol", "LFBO-LFPG (SimBrief), suivi"],
                ["Caméra", "à 2,3 km de l'avion"],
                ["Départ", "LFBO"],
            ],
        },
        "build": {
            "state": ["En construction", "running"],
            "rate": ["Débit", "0,1 Mo/s"],
            "figures": [
                {
                    "value": "7",
                    "label": "à construire, aucune urgente",
                    "more": "1 \u00d7 4096 px · 6 \u00d7 256 px",  # the multiplication sign
                },
                {"value": "24", "label": "en construction", "more": "12 morceaux attendus"},
                {"value": "9 591", "label": "construites depuis le début", "more": None},
                {
                    "value": "1,55 Go",
                    "label": "téléchargés",
                    "beside": True,  # on the value's line, in italics
                    "more": "104 530 morceaux",
                    "hover": "104 530 morceaux, 947 du cache",  # too long for the box's line
                    "problem": "3 en échec",
                },
            ],
            "sizes": [
                "8192 px · 2",
                "4096 px · 260",
                "2048 px · 291",
                "1024 px · 524",
                "512 px · 1 182",
                "256 px · 7 332",
            ],
            "disk": {
                "text": "4,6 Go sur 40 Go · 5 textures en 8192 px",
                "share": 0.115,
                "warn": False,
            },
        },
    }
    en = views["en"][0]
    assert en["flight"]["state"] == ["X-Plane sends its position", "ok"]
    assert [f["value"] for f in en["flight"]["figures"]] == ["8,820 ft", "231 kt", "44.218 · 1.705"]
    assert en["flight"]["rows"][0] == ["Sharp up to", "13 km (3,024 px image, 65° field of view)"]
    assert en["build"]["rate"] == ["Rate", "0.1 MB/s"]
    assert en["build"]["figures"][0]["label"] == "to build, none urgent"
    assert en["build"]["figures"][3]["value"] == "1.55 GB"
    assert en["build"]["disk"]["text"] == "4.6 GB of 40 GB · 5 textures at 8192 px"


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_flight_and_the_textures_say_their_state() -> None:
    """X-Plane's state when Live is stopped and before it sends its position; the textures' state:
    downloads all failing, late (but not while the start is built: the state above says so), being
    built, all built, nothing to build; the disk's bar warns past 90 %."""
    stopped = {"state": "idle", "checks": {"xplane_running": False}}
    running = {"state": "idle", "checks": {"xplane_running": True}}
    waiting = _streaming(queued=[0] * 6, building=0, pieces_awaited=0, built=[0] * 6, failed=0)
    waiting["stream"].update(data="none", aircraft=None, camera_km=None, start=None)
    waiting["stream"]["streamer"].update(flight_plan=None, screen=None, at_8192=0)
    refused = _streaming(downloaded=0, downloaded_bytes=0, from_cache=0, failed=40)
    late = _streaming(urgent=3)
    holding = _streaming(urgent=1200)
    holding["stream"]["start"] = {"name": "LFBO", "ready": False, "held": True}
    done = _streaming(queued=[0] * 6, building=0, pieces_awaited=0)
    full = _streaming(disk_bytes=34_500_000_000)
    views = _views([stopped, running, waiting, refused, late, holding, done, full])["fr"]
    assert [v["flight"]["state"] for v in views[:3]] == [
        ["X-Plane n'est pas lancé", ""],
        ["X-Plane est lancé", ""],
        ["Pas encore de position d'X-Plane", ""],
    ]
    assert views[0]["flight"]["figures"] == views[0]["flight"]["rows"] == []
    assert views[2]["flight"]["figures"] == views[2]["flight"]["rows"] == []
    assert views[0]["build"] is None and views[1]["build"] is None
    assert [v["build"]["state"] for v in views[2:7]] == [
        ["Rien à construire", ""],
        ["Téléchargements en échec", "fail"],
        ["En retard : 3 urgentes", "warn"],
        ["En construction", "running"],
        ["Tout est construit", "ok"],
    ]
    assert views[2]["build"]["sizes"] == []
    assert views[2]["build"]["figures"][1]["more"] is None  # nothing built, nothing awaited
    assert views[3]["build"]["figures"][3]["value"] == "0 Mo"
    assert views[4]["build"]["figures"][0]["label"] == "à construire, 3 urgentes"
    assert views[7]["build"]["disk"] == {
        "text": "36,5 Go sur 40 Go · 5 textures en 8192 px",
        "share": 0.9125,
        "warn": True,
    }


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_a_start_says_what_it_does_the_textures_set_to_a_new_zoom_included() -> None:
    """While Live starts: the region set up the first time, the textures set to the size of a
    new zoom (the status's relayout), else the pack put in X-Plane. It said the last while it set
    the textures to a new zoom, 16 s on 2026-10-09 (the user: fix it)."""
    starting = {"state": "starting", "checks": {"region_ready": True}, "relayout": False}
    statuses = [
        {**starting, "checks": {"region_ready": False}},
        {**starting, "relayout": True},
        starting,
    ]
    script = (
        f"import {{ headline }} from {json.dumps((UI_DIR / 'live.js').as_uri())};"
        f"import {{ setLanguage }} from {json.dumps((UI_DIR / 'strings.js').as_uri())};"
        f"const all = {json.dumps(statuses)}; setLanguage('fr');"
        "console.log(JSON.stringify(all.map((s) => headline(s)[1])));"
    )
    out = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", script],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(out.stdout) == [
        "Préparation de la région, une seule fois : quelques secondes.",
        "Mise des textures au nouveau zoom, une seule fois : quelques secondes, "
        "rien à télécharger.",
        "Installation du pack et du plugin dans X-Plane.",
    ]


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_window_says_a_source_refuses_this_computer() -> None:
    """While Live runs, a source whose server refuses this computer (403, access denied) is said
    under the source, in OrthoStudio XP's words for its map, with what to do; the textures' label
    says their downloads are refused. Nothing is said otherwise, or once Live stops."""
    refused = _streaming(refused_by=["Esri World Imagery"])
    stopped = {"state": "idle", "checks": {"xplane_running": False}}
    script = (
        f"import {{ refusedText, buildView }} from {json.dumps((UI_DIR / 'live.js').as_uri())};"
        f"import {{ setLanguage }} from {json.dumps((UI_DIR / 'strings.js').as_uri())};"
        f"const all = {json.dumps([refused, _streaming(), stopped])}; const out = {{}};"
        "for (const lang of ['en', 'fr']) { setLanguage(lang);"
        " out[lang] = [all.map(refusedText), buildView(all[0]).state]; }"
        "console.log(JSON.stringify(out));"
    )
    out = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", script],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    said = json.loads(out.stdout)
    assert said["fr"] == [
        [
            "Esri World Imagery refuse votre connexion (accès refusé) : arrêtez Live et "
            "choisissez une autre source en attendant.",
            "",
            "",
        ],
        ["Téléchargements refusés", "fail"],
    ]
    assert said["en"][0][0] == (
        "Esri World Imagery refuses your connection (access denied): stop Live and choose "
        "another source meanwhile."
    )
    assert said["en"][1] == ["Downloads refused", "fail"]
    html = (UI_DIR / "index.html").read_text()
    source = html[html.index('id="set-provider"') : html.index('id="source-error"')]
    assert 'class="error-inline" id="source-refused" role="alert" hidden' in source


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_departure_box_and_a_new_simbrief_plan() -> None:
    """The first plan fills an empty box; a newer one (another briefing) is offered, never put in
    the box unasked; a plan comes into a box the pilot typed in only when asked."""
    script = (
        f"import {{ departureView }} from {json.dumps((UI_DIR / 'live.js').as_uri())};"
        "const a = { icao: 'LFMN', plan: 'LFMN-LFPG', briefing: '101' };"
        "const b = { icao: 'LFBO', plan: 'LFBO-LFMN', briefing: '102' };"
        "console.log(JSON.stringify(["
        " departureView(null, a, ''),"  # the window opens: the box filled
        " departureView(a, a, 'LFMN'),"  # the same plan: nothing to say
        " departureView(a, b, 'LFMN'),"  # a new plan: offered
        " departureView(a, b, 'LSGG'),"  # the pilot typed another: offered all the same
        " departureView(null, a, 'LSGG'),"  # typed before the plan came: offered, not put in
        " departureView(a, {}, 'LFMN'),"  # no plan (no name, simbrief.com silent): as it is
        "]));"
    )
    out = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", script],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    a = {"icao": "LFMN", "plan": "LFMN-LFPG", "briefing": "101"}
    b = {"icao": "LFBO", "plan": "LFBO-LFMN", "briefing": "102"}
    assert json.loads(out.stdout) == [
        {"fill": "LFMN", "shown": a, "offer": None},
        {"fill": None, "shown": a, "offer": None},
        {"fill": None, "shown": a, "offer": b},
        {"fill": None, "shown": a, "offer": b},
        {"fill": None, "shown": None, "offer": a},
        {"fill": None, "shown": a, "offer": None},
    ]


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
        encoding="utf-8",
        check=True,
    )
    assert json.loads(out.stdout) == [True, False, False, False]
    js = (UI_DIR / "live.js").read_text()
    handler = r"addEventListener\(\"click\",[^}]*isTitleClick\(ev\.target\)[^}]*\}, true\)"
    assert re.search(handler, js)  # in the capture phase, before any other listener


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_settings_search_reads_what_a_question_shows() -> None:
    """OrthoStudio XP's search (its settings.js), on the text a question shows: what is hidden
    (the start's four choices with the package's zooms) is left out, a list's choices are in."""
    osxp = UI_DIR.parent.parent / "ui" / "settings.js"
    script = (
        f"import {{ shownText }} from {json.dumps((UI_DIR / 'live.js').as_uri())};"
        f"import {{ searchWords, matchesSearch }} from {json.dumps(osxp.as_uri())};"
        "const text = (v) => ({ nodeType: 3, nodeValue: v });"
        "const el = (kids, hidden = false) => ({ nodeType: 1, hidden, childNodes: kids });"
        "const box = el([text('Zoom'), el([el([text('ZL17: sharper up close')])]),"
        " el([text('Full resolution')], true), text('Espace disque')]);"
        "const seen = shownText(box);"
        "console.log(JSON.stringify(['sharper', 'resolution', 'disques', 'ESPACE zoom']"
        ".map((q) => matchesSearch(seen, searchWords(q)))));"
    )
    out = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", script],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(out.stdout) == [True, False, True, True]


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_address_names_the_screen() -> None:
    """#settings opens the settings, #settings/regions with the regions in view; anything else,
    Live's screen."""
    script = (
        f"import {{ screenFromHash }} from {json.dumps((UI_DIR / 'live.js').as_uri())};"
        "console.log(JSON.stringify(['', '#live', '#settings', '#settings/regions', '#live/x',"
        " '#nope', '#settings/a b'].map(screenFromHash)));"
    )
    out = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", script],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    live = {"name": "live", "question": None}
    assert json.loads(out.stdout) == [
        live,
        live,
        {"name": "settings", "question": None},
        {"name": "settings", "question": "regions"},
        live,
        live,
        live,
    ]


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_a_square_is_named_as_osxp_names_it() -> None:
    script = (
        f"import {{ tileName }} from {json.dumps((UI_DIR / 'live.js').as_uri())};"
        "console.log(JSON.stringify([tileName(46, 6), tileName(-33, -70), tileName(5, 150)]));"
    )
    out = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", script],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(out.stdout) == ["+46+006", "-33-070", "+05+150"]


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_page_warns_of_zl17_and_zl19_on_a_small_computer() -> None:
    """Below the 32 GB they want (orthostudio.live.machine), whichever of the two is chosen."""
    script = (
        f"import {{ shortOfMemory }} from {json.dumps((UI_DIR / 'live.js').as_uri())};"
        "const small = { cores: 8, ram_gb: 16, sharp_ram_gb: 32 };"
        "const large = { cores: 14, ram_gb: 48, sharp_ram_gb: 32 };"
        "console.log(JSON.stringify([shortOfMemory(small, 16, 18), shortOfMemory(small, 17, 18),"
        " shortOfMemory(small, 16, 19), shortOfMemory(large, 17, 19), shortOfMemory(null, 17, 19),"
        " shortOfMemory({ ...small, ram_gb: 32 }, 17, 19)]));"
    )
    out = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", script],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(out.stdout) == [False, True, True, False, False, False]


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_file_manager_s_name() -> None:
    """Show buttons (Live's folder, LogLive.txt) name the file manager of this computer."""
    script = (
        f"import {{ revealLabel }} from {json.dumps((UI_DIR / 'live.js').as_uri())};"
        "console.log(JSON.stringify(['mac', 'win', 'lin'].map(revealLabel)));"
    )
    out = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", script],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(out.stdout) == ["Show in Finder", "Show in File Explorer", "Open the folder"]


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_page_parses() -> None:
    for name in ("live.js", "strings.js"):
        subprocess.run([NODE or "node", "--check", str(UI_DIR / name)], check=True)


def test_the_page_uses_osxp_parts_and_no_outside_script() -> None:
    html = (UI_DIR / "index.html").read_text()
    # the map's Leaflet is OrthoStudio XP's vendored copy, served from the engine like the rest
    assert re.findall(r"<script[^>]*src=\"([^\"]+)\"", html) == [
        "static/vendor/leaflet/leaflet.js",
        "live/live.js",
    ]
    assert 'href="static/styles.css"' in html  # OrthoStudio XP's own style sheet
    assert "http" not in re.sub(r"<!--.*?-->", "", html, flags=re.S).replace("http-equiv", "")


def test_the_flight_and_the_textures_are_osxp_parts() -> None:
    """Their heads are OrthoStudio XP's job head (a title and what stands beside it on one
    baseline, its title-gap under them), their boxes its .cost, their sizes a row of its .chips,
    and their own rules place them with its tokens only (a user, 2026-10-08: a title and its text
    beside it not aligned)."""
    html = (UI_DIR / "index.html").read_text()
    assert 'class="job-head" id="flight-head"' in html
    assert 'class="job-head" id="build-head"' in html
    assert 'class="chips" id="build-sizes"' in html
    assert 'class="help" id="build-idle"' in html  # OrthoStudio XP's text beside a title
    js = (UI_DIR / "live.js").read_text()
    assert 'figure.className = "cost live-figure"' in js
    css = (UI_DIR / "live.css").read_text()
    rules = css[css.index(".live .job-head.is-alone") : css.index(".live-checks")]
    spacing = re.findall(r"(?:margin[\w-]*|gap|padding[\w-]*):\s*([^;]+);", rules)
    assert spacing
    for value in spacing:
        assert all(part == "0" or part.startswith("var(--") for part in value.split()), value


def test_threads_are_not_left_behind(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    before = {t.name for t in threading.enumerate()}
    c = control_for(FakeStream())
    assert c.start()
    wait_for(lambda: c.state == STREAMING)
    c.stop()
    assert c.wait_idle(5)
    assert "live-stream" not in {t.name for t in threading.enumerate()} - before


# --- OrthoStudio XP's regions -------------------------------------------------------------------


class FakeInstaller:
    """An install that downloads until it is stopped."""

    def __init__(self, root: Path, region, overlays: bool = True, log=print) -> None:
        self.region = region
        self.overlays = overlays
        self.stopped = threading.Event()
        self.phase = "downloading"

    def run(self) -> None:
        while not self.stopped.wait(0.01):
            pass
        self.phase = "cancelled"

    def cancel(self) -> None:
        self.stopped.set()

    def snapshot(self) -> dict:
        return {"region": self.region.id, "phase": self.phase, "done_bytes": 5, "total_bytes": 10}


def regions_control(xp: Path, **settings: Any) -> LiveControl:
    from orthostudio.live.osxpregions import OsxpRegion, Zip

    catalog = {
        "eur": OsxpRegion(
            "eur",
            "2026-10-18",
            (Zip("eur/zips/z_osxp_eur_+45+005.zip", 10, "a" * 64, 17),),
            (Zip("eur/overlay-zips/y_osxp_eur_+45+005.zip", 3, "b" * 64, 3),),
        ),
        "na": OsxpRegion(
            "na", "2026-10-18", (Zip("na/zips/z_osxp_na_+40-100.zip", 20, "c" * 64, 34),)
        ),
    }
    return LiveControl(
        checks=checks(xplane=str(xp)),
        settings=lambda: LiveSettings(**settings),
        echo=lambda _: None,
        catalog=lambda root: catalog,
        installer=FakeInstaller,
    )


def test_the_regions_the_page_lists(home: Path, tmp_path: Path) -> None:
    from orthostudio.live import aoregions, osxpregions
    from orthostudio.live.pack import live_root

    c = regions_control(tmp_path / "X-Plane 12")
    # the status never asks the host itself: the regions installed only
    assert c.status()["regions"]["list"] == []
    c.regions(fetch=True)
    wait_for(lambda: len(c.regions()["list"]) == 2)
    listed = {r["id"]: r for r in c.regions()["list"]}
    assert listed["eur"] == {
        "id": "eur",
        "installed": False,
        "download": 13,  # its mesh and its overlays
        "unpacked": 20,
        "changed": 0,
        "changed_bytes": 13,
    }
    c.install_region("eur")
    assert c._installer.overlays  # the pilot takes X-Plane's roads: they come with it
    with pytest.raises(session.RefusedError):
        c.install_region("na")  # one at a time
    assert c.regions()["install"]["phase"] == "downloading"
    with pytest.raises(session.RefusedError):
        c.remove_region("eur")  # not while it is installed
    c.cancel_install()
    wait_for(lambda: c.regions()["install"]["phase"] == "cancelled")
    # installed, then one of its zips mended in the bucket: the page offers the update
    mine = osxpregions.mesh_dir(live_root(), "eur")
    (mine / "terrain").mkdir(parents=True)
    zips = {"eur/zips/z_osxp_eur_+45+005.zip": "0" * 64}
    (mine / aoregions.MARKER).write_text(json.dumps({"size": 17, "zips": zips}))
    listed = {r["id"]: r for r in c.regions()["list"]}
    assert listed["eur"]["installed"] and listed["eur"]["changed"] == 2  # and its overlays
    assert [r["id"] for r in c.regions()["ours"]] == ["eur"]
    c.remove_region("eur")
    assert not mine.exists()
    with pytest.raises(session.RefusedError):
        c.remove_region("eur")  # not installed (any more)
    with pytest.raises(session.RefusedError):
        c.install_region("atlantis")


def test_without_overlays_a_region_downloads_its_mesh_alone(home: Path, tmp_path: Path) -> None:
    c = regions_control(tmp_path / "X-Plane 12", overlays="off")
    c.regions(fetch=True)
    wait_for(lambda: len(c.regions()["list"]) == 2)
    assert {r["id"]: r["download"] for r in c.regions()["list"]}["eur"] == 10
    c.install_region("eur")
    assert not c._installer.overlays
    c.cancel_install()


@pytest.mark.anyio
async def test_the_regions_api(home: Path, tmp_path: Path) -> None:
    c = regions_control(tmp_path / "X-Plane 12")
    async with client_for(create_live_app(c)) as client:
        await client.get("/api/live/regions")
        wait_for(lambda: c.regions()["list"])
        got = (await client.get("/api/live/regions")).json()
        assert [r["id"] for r in got["list"]] == ["eur", "na"]
        started = await client.post("/api/live/regions/eur/install")
        assert started.status_code == 202 and started.json()["install"]["region"] == "eur"
        assert (await client.post("/api/live/regions/na/install")).status_code == 409
        await client.post("/api/live/regions/cancel")
        wait_for(lambda: c.regions()["install"]["phase"] == "cancelled")
        assert (await client.delete("/api/live/regions/eur")).status_code == 409  # not installed


def test_the_overlays_setting(home: Path) -> None:
    assert LiveSettings().overlays == "auto"
    save_settings(LiveSettings(overlays="off"))
    assert load_settings().overlays == "off"
    assert control_module.options_for(load_settings()).overlays == "off"
    settings_file().write_text('[stream]\noverlays = "everywhere"\n')
    assert load_settings().overlays == "auto"


def test_the_plugin_speaks_the_window_s_language(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(control_module, "xplane_running", lambda: False)
    stream = FakeStream()
    c = LiveControl(
        prepare=stream.prepare,
        follow=stream.follow,
        finish=stream.finish,
        checks=checks(),
        echo=lambda _: None,
        catalog=lambda root: {},
    )
    assert c.language == "en"
    assert c.start()
    wait_for(lambda: c.state == STREAMING)
    c.set_language("fr")  # the window's selector, while it streams
    assert stream.language() == "fr"  # the plugin's next answer says it
    assert load_settings().language == "fr"  # kept for the next starts
    assert control_module.options_for(load_settings()).language == "fr"
    with pytest.raises(ValueError):
        c.set_language("de")
    c.stop()
    assert c.wait_idle(5)


def test_the_disk_space_is_raised_to_the_floor_of_its_zooms(home: Path) -> None:
    """20 GB by default; never under 10 GB at 16/18 nor 20 GB with 8192 px textures (16/19, 17/19):
    a smaller space is raised rather than refused, as a 20-minute flight at 17/19 took 11.7 GB of
    textures given 4 (2026-10-12); nought or less stays wrong."""
    assert LiveSettings().disk_gb == 20
    assert LiveSettings(disk_gb=4).disk_gb == 10
    assert LiveSettings(disk_gb=12, max_zoom=17, max_zoom_airports=19).disk_gb == 20
    assert LiveSettings(disk_gb=12, max_zoom=16, max_zoom_airports=19).disk_gb == 20
    assert LiveSettings(disk_gb=12).disk_gb == 12
    with pytest.raises(ValidationError):
        LiveSettings(disk_gb=0)


def test_the_buttons_draw_osxp_s_own_icons() -> None:
    """OrthoStudio XP's sheet sizes its icons (.icon) and no longer a button's svg: in the Mac
    app's WebKit the bin and Quit's sign, left as svg, came out 0 px wide (2026-11-09)."""
    html = (UI_DIR / "index.html").read_text()
    assert 'class="icon icon-bin"' in html
    assert 'class="icon icon-quit"' in html
