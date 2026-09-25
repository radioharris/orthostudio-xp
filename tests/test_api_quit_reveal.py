"""Quit OrthoStudio XP and show a folder in the file manager, from the page (``POST /api/quit``,
``POST /api/reveal``).

A user asked for a Quit button, and for a button that opens the Finder, the Windows File
Explorer or the Linux file manager on a tile or on OrthoStudio XP's folder.
"""

from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

import test_api_fakes as fakes
from orthostudio.api import serve as serve_mod
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.codemark import code_mark
from orthostudio.fsutil import reveal_command
from orthostudio.install.library import Library
from orthostudio.model import TileRef
from test_api_fakes import FakeBuild, client_for, make_spec

anyio_backend = fakes.anyio_backend
home = fakes.home


def _app(home: Path, **kw: object) -> tuple[object, JobManager]:
    mgr = JobManager(jobs_dir=home / "jobs", build=kw.pop("build", FakeBuild()), env_factory=None)  # type: ignore[arg-type]
    app = create_app(env_factory=None, jobs=mgr, settings_path=home / "config.toml", **kw)  # type: ignore[arg-type]
    return app, mgr


@pytest.mark.anyio
async def test_quit_stops_osxp_and_asks_before_stopping_a_build(home: Path) -> None:
    calls: list[str] = []
    app, mgr = _app(home, shutdown=lambda: calls.append("stop"), build=FakeBuild(delay_s=0.2))
    try:
        async with client_for(app) as c:
            status = (await c.get("/api/status")).json()
            assert status["can_quit"] is True and status["platform"] in {"mac", "win", "lin"}
            job = mgr.start([make_spec("+46+006", home=home)])
            waiting = mgr.start([make_spec("+46+007", home=home)], queue=True)
            r = await c.post("/api/quit", json={})
            assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_BUSY"
            await asyncio.sleep(0.5)
            assert calls == [] and not job.finished
            r = await c.post("/api/quit", json={"force": True})
            assert r.status_code == 200
            assert r.json() == {
                "stopping": True,
                "cancelled": job.id,
                "queued_cancelled": [waiting.id],
            }
            assert job.wait(30) and job.status == "cancelled"
            # the build that waited never started: cancelled before the running one stopped
            assert waiting.status == "cancelled" and waiting.started_at is None
            await asyncio.sleep(0.5)
            assert calls == ["stop"]
            r = await c.post("/api/quit", json={"bogus": 1})
            assert r.status_code == 422
    finally:
        mgr.close()

    app, mgr = _app(home)  # a server that osxp serve did not start cannot be stopped from the page
    try:
        async with client_for(app) as c:
            assert (await c.get("/api/status")).json()["can_quit"] is False
            r = await c.post("/api/quit", json={})
            assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_NOT_STOPPABLE"
    finally:
        mgr.close()


@pytest.mark.anyio
async def test_reveal_shows_only_the_folders_osxp_works_with(home: Path, tmp_path: Path) -> None:
    shown: list[Path] = []
    app, mgr = _app(home, reveal=shown.append)
    tiles = home / "tiles" / "zOrthoStudio_+46+006"
    tiles.mkdir(parents=True)
    imported = tmp_path / "Ortho4XP" / "Tiles" / "zOrtho4XP_+45+005"
    imported.mkdir(parents=True)
    with Library(home / "library.sqlite") as lib:
        lib.register(TileRef(45, 5), "BI", 16, imported, "ortho4xp", None)
    elsewhere = tmp_path / "private"
    elsewhere.mkdir()
    try:
        async with client_for(app) as c:
            for path in (home, tiles, imported):
                r = await c.post("/api/reveal", json={"path": str(path)})
                assert r.status_code == 200, (path, r.text)
            assert shown == [home, tiles, imported]
            for bad in (elsewhere, home / ".." / "private", Path("tiles")):
                r = await c.post("/api/reveal", json={"path": str(bad)})
                assert r.status_code == 403 and r.json()["error"]["code"] == "SYS_FORBIDDEN_PATH"
            r = await c.post("/api/reveal", json={"path": str(home / "gone")})
            assert r.status_code == 404
            assert len(shown) == 3
    finally:
        mgr.close()


def test_the_file_manager_of_each_platform(tmp_path: Path) -> None:
    folder = tmp_path / "zOrthoStudio_+46+006"
    folder.mkdir()
    dsf = folder / "+46+006.dsf"
    dsf.write_bytes(b"XPLNEDSF")
    assert reveal_command(folder, "mac") == ["open", str(folder)]
    assert reveal_command(dsf, "mac") == ["open", "-R", str(dsf)]
    assert reveal_command(folder, "win") == ["explorer", str(folder)]
    assert reveal_command(dsf, "win") == ["explorer", f"/select,{dsf}"]
    assert reveal_command(dsf, "lin") == ["xdg-open", str(folder)]


def test_a_second_launch_finds_no_osxp_on_a_free_port() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert serve_mod.running_osxp(port, timeout_s=0.5) is None


class _RunningOsxp:
    """A stand-in for an OrthoStudio XP already serving a port: its ``/api/status`` gives
    ``api_level`` and, when ``root`` is given, the package folder it runs and the mark of the code
    it started with (``code``, the files as they are by default); its ``/api/quit`` answers
    ``quit_status`` and, on 200, stops serving."""

    def __init__(
        self,
        api_level: int,
        quit_status: int = 200,
        root: str | None = None,
        code: str | None = None,
    ) -> None:
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        owner = self
        self.quits = 0

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, status: int, doc: dict) -> None:
                body = json.dumps(doc).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                doc: dict = {"api_level": api_level}
                if root is not None:
                    doc["engine"] = {"root": root, "pid": 1, "code": code or code_mark()}
                self._answer(200, doc)

            def do_POST(self) -> None:
                owner.quits += 1
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if quit_status != 200:
                    self._answer(quit_status, {"code": "SYS_BUSY"})
                    return
                self._answer(200, {"stopping": True, "cancelled": None})
                threading.Thread(target=owner.stop, daemon=True).start()

            def log_message(self, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.stopped = False

    def stop(self) -> None:
        if not self.stopped:
            self.stopped = True
            self.server.shutdown()
            self.server.server_close()


def test_a_second_launch_opens_the_osxp_already_running() -> None:
    """Double-clicking the app while OrthoStudio XP runs must show it, not fail on a busy port."""
    from orthostudio.api.app import API_LEVEL

    root = str(serve_mod.package_root())
    running = _RunningOsxp(API_LEVEL, root=root)
    try:
        status = serve_mod.running_osxp(running.port)
        engine = {"root": root, "pid": 1, "code": code_mark()}
        assert status == {"api_level": API_LEVEL, "engine": engine}
        serve_mod.serve(port=running.port, open_browser=False)  # returns at once: nothing starts
        assert running.quits == 0 and not running.stopped
    finally:
        running.stop()


class _Answering:
    """A server on a free loopback port answering each GET path as ``routes`` says,
    ``(delay_s, status, document)``, and 404 to any other."""

    def __init__(self, routes: dict[str, tuple[float, int, dict]]) -> None:
        import json
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                delay_s, status, doc = routes.get(self.path, (0.0, 404, {"detail": "Not Found"}))
                time.sleep(delay_s)
                body = json.dumps(doc).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def test_a_second_launch_recognises_an_osxp_slow_to_give_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user on Windows opened the app again after closing its page: the running engine took
    longer than 1.5 s to give ``/api/status``, the launch took it for another program and ended,
    and nothing showed (2026-09-17). ``/api/engine`` answers at once."""
    import time

    from orthostudio.api.app import API_LEVEL

    engine = {"root": str(serve_mod.package_root()), "pid": 1, "code": code_mark()}
    doc = {"api_level": API_LEVEL, "engine": engine}
    running = _Answering({"/api/engine": (0.0, 200, doc), "/api/status": (3.0, 200, doc)})
    opened: list[str] = []
    monkeypatch.setattr(serve_mod.webbrowser, "open", opened.append)
    try:
        started = time.monotonic()
        assert serve_mod.running_osxp(running.port) == doc
        assert time.monotonic() - started < 1.5
        serve_mod.serve(port=running.port, open_browser=True)  # returns: nothing starts
        assert opened == [f"http://127.0.0.1:{running.port}/"]
    finally:
        running.stop()


def test_an_osxp_older_than_api_engine_is_recognised_by_its_status() -> None:
    """Its ``/api/engine`` is a 404; its status, slower than the old 1.5 s, is waited for."""
    doc = {"api_level": 13, "engine": {"root": "/elsewhere", "pid": 1}}
    running = _Answering({"/api/status": (2.0, 200, doc)})
    try:
        assert serve_mod.running_osxp(running.port) == doc
    finally:
        running.stop()


def test_a_port_held_by_another_program_still_opens_its_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What does not say it is OrthoStudio XP is not stopped: the error goes to the log, and its
    address opens, rather than nothing at all."""
    from orthostudio.errors import OsxpError

    other = _Answering({})
    opened: list[str] = []
    monkeypatch.setattr(serve_mod.webbrowser, "open", opened.append)
    try:
        assert serve_mod.running_osxp(other.port) is None
        with pytest.raises(OsxpError) as exc:
            serve_mod.serve(port=other.port, open_browser=True)
        assert "in use" in exc.value.message
        assert opened == [f"http://127.0.0.1:{other.port}/"]
    finally:
        other.stop()
    with socket.socket() as silent:  # listens, never answers: not waited for past the limit
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        assert serve_mod.running_osxp(silent.getsockname()[1], timeout_s=0.3) is None


@pytest.mark.parametrize("root", ["/Applications/OrthoStudio XP.app/Contents/Resources", None])
def test_another_installation_takes_the_place_of_the_running_one(root: str | None) -> None:
    """A user ran a checkout's ``osxp serve --open`` while the installed app ran, and got the
    app's page, told only that OrthoStudio XP was already running (2026-09-14). Another
    installation, or an engine that does not say which it is, is asked to quit, as recent as it
    may be; a build running in it keeps it (409)."""
    from orthostudio.api.app import API_LEVEL

    running = _RunningOsxp(API_LEVEL, root=root)
    try:
        status = serve_mod.running_osxp(running.port)
        assert status is not None
        assert serve_mod.take_over(running.port, status, timeout_s=10.0) is True
        assert running.quits == 1 and running.stopped
    finally:
        running.stop()
    busy = _RunningOsxp(API_LEVEL, quit_status=409, root=root)
    try:
        status = serve_mod.running_osxp(busy.port)
        assert status is not None
        assert serve_mod.take_over(busy.port, status, timeout_s=1.0) is False
        assert busy.quits == 1 and not busy.stopped
    finally:
        busy.stop()


def test_a_checkout_opened_again_after_a_change_runs_the_new_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A checkout's engine loads its code when it starts, and ``osxp serve --open`` run again after
    a change showed that engine: the new page on the old code, and a pilot tested a fix that was
    not running (2026-09-25). The same installation started before its files changed is asked to
    quit, as recent as it is; with the same files, it stays."""
    from orthostudio.api.app import API_LEVEL

    root = str(serve_mod.package_root())
    running = _RunningOsxp(API_LEVEL, root=root, code="started-before")
    try:
        status = serve_mod.running_osxp(running.port)
        assert status is not None
        assert serve_mod.take_over(running.port, status, timeout_s=10.0) is True
        assert running.quits == 1 and running.stopped
    finally:
        running.stop()
    assert "started before its code changed was running: it stopped" in capsys.readouterr().out
    same = _RunningOsxp(API_LEVEL, root=root)
    try:
        status = serve_mod.running_osxp(same.port)
        assert status is not None
        assert serve_mod.take_over(same.port, status, timeout_s=1.0) is False
        assert same.quits == 0 and not same.stopped
    finally:
        same.stop()


def test_a_launch_stops_an_older_osxp_and_takes_its_place() -> None:
    """Found by the user after an update: the new app opened its page on the engine started before
    the update, which could only say "restart it". An older engine is asked to quit."""
    from orthostudio.api.app import API_LEVEL

    running = _RunningOsxp(API_LEVEL - 1)
    try:
        status = serve_mod.running_osxp(running.port)
        assert status is not None
        assert serve_mod.take_over(running.port, status, timeout_s=10.0) is True
        assert running.quits == 1 and running.stopped
    finally:
        running.stop()


@pytest.mark.parametrize(("api_level", "quit_status", "asked"), [(8, 409, 1), (7, 200, 0)])
def test_an_older_osxp_that_cannot_stop_is_left_running(
    api_level: int, quit_status: int, asked: int
) -> None:
    """A build running in it (409), or an engine older than ``POST /api/quit`` (never asked): it
    stays, and its page is what opens."""
    running = _RunningOsxp(api_level, quit_status)
    try:
        status = serve_mod.running_osxp(running.port)
        assert status is not None
        assert serve_mod.take_over(running.port, status, timeout_s=1.0) is False
        serve_mod.serve(port=running.port, open_browser=False)  # returns at once
        assert running.quits == 2 * asked and not running.stopped
    finally:
        running.stop()


# -- the folder dialog (a user asked for a button instead of typing a path, 2026-09-15) ---------


def test_the_folder_dialog_command_of_each_system(monkeypatch: pytest.MonkeyPatch) -> None:
    from orthostudio import fsutil

    # the paths of another system are spelt as that system spells them, wherever the test runs
    mac = fsutil.choose_folder_command(
        'Where is "X-Plane" 12?', PurePosixPath("/Users/me/X-Plane 12"), "mac"
    )
    # JavaScript for Automation: AppleScript's `activate` held the dialog back 2 s (2026-09-15)
    assert mac is not None and mac[:4] == ["osascript", "-l", "JavaScript", "-e"]
    assert "app.activate();" in mac[4] and "activate\n" not in mac[4]
    assert (
        'app.chooseFolder({withPrompt: "Where is \\"X-Plane\\" 12?", '
        'defaultLocation: Path("/Users/me/X-Plane 12")}).toString()'
    ) in mac[4]
    assert "defaultLocation" not in fsutil.choose_folder_command("Folder", None, "mac")[4]  # type: ignore[index]
    win = fsutil.choose_folder_command(
        "Where's X-Plane", PureWindowsPath("C:/Games/X-Plane 12"), "win"
    )
    assert win is not None and win[:4] == ["powershell", "-NoProfile", "-STA", "-Command"]
    script = win[4]
    assert "$dialog.Description = 'Where''s X-Plane'" in script
    assert "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8" in script
    assert "TopMost = $true" in script and "$dialog.ShowDialog($owner)" in script
    tools = {"zenity"}
    monkeypatch.setattr(
        fsutil.shutil, "which", lambda name: f"/usr/bin/{name}" if name in tools else None
    )
    assert fsutil.choose_folder_command("Folder", None, "lin") == [
        "zenity", "--file-selection", "--directory", "--title=Folder"]  # fmt: skip
    tools = {"kdialog"}
    assert fsutil.choose_folder_command("Folder", PurePosixPath("/data"), "lin") == [
        "kdialog", "--getexistingdirectory", "/data", "--title", "Folder"]  # fmt: skip
    tools = set()
    assert fsutil.choose_folder_command("Folder", None, "lin") is None


def test_the_folder_dialog_answer_is_the_path_or_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from orthostudio import fsutil, winfront

    answers = [(0, "/Users/me/X-Plane 12/\n"), (1, ""), (0, "  ")]
    watched: list[tuple[int, threading.Event]] = []

    class Dialog:
        def __init__(self, cmd, **kw):  # type: ignore[no-untyped-def]
            self.pid = 4242
            self.returncode, self.out = answers.pop(0)

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *exc):  # type: ignore[no-untyped-def]
            return False

        def communicate(self, timeout=None):  # type: ignore[no-untyped-def]
            return self.out, ""

    monkeypatch.setattr(fsutil, "choose_folder_command", lambda prompt, start: ["dialog"])
    monkeypatch.setattr(fsutil.subprocess, "Popen", Dialog)
    # Windows: the dialog of that process is looked for, until it is answered
    monkeypatch.setattr(winfront, "front_window_of", lambda pid, done: watched.append((pid, done)))
    assert fsutil.choose_folder("X-Plane") == Path("/Users/me/X-Plane 12")
    assert fsutil.choose_folder("X-Plane") is None  # cancelled
    assert fsutil.choose_folder("X-Plane") is None  # nothing chosen
    assert [pid for pid, _ in watched] == [4242] * 3 and all(done.is_set() for _, done in watched)
    monkeypatch.setattr(fsutil, "choose_folder_command", lambda prompt, start: None)
    with pytest.raises(FileNotFoundError):
        fsutil.choose_folder("X-Plane")


def test_the_file_explorer_window_a_reveal_opened_is_the_one_brought_in_front() -> None:
    """Started by the engine, which runs in the background, File Explorer opened behind the
    browser (a user, 2026-09-15): its window is found, then brought in front."""
    from orthostudio.winfront import EXPLORER_CLASS, TopWindow, pick_explorer_window

    folder = PureWindowsPath(r"C:\Users\me\X-Plane 12")
    old = TopWindow(1, EXPLORER_CLASS, "Downloads", 10)
    same = TopWindow(2, EXPLORER_CLASS, "X-Plane 12", 10)
    chrome = TopWindow(3, "Chrome_WidgetWin_1", "OrthoStudio XP", 20)
    new = TopWindow(4, EXPLORER_CLASS, "X-Plane 12", 10)
    before = {1, 2}
    assert pick_explorer_window([chrome, new, old, same], before, folder, waited=False) == 4
    # no new window yet: wait, then take the one already open on that folder
    assert pick_explorer_window([chrome, old, same], before, folder, waited=False) is None
    assert pick_explorer_window([chrome, old, same], before, folder, waited=True) == 2
    full = TopWindow(5, EXPLORER_CLASS, r"c:\users\me\x-plane 12", 10)  # full path in the title
    assert pick_explorer_window([full], {5}, folder, waited=True) == 5
    assert pick_explorer_window([chrome, old], before, folder, waited=True) is None


def test_the_window_helpers_are_harmless_everywhere() -> None:
    import threading

    from orthostudio import winfront

    windows = winfront.top_windows()
    assert isinstance(windows, list)
    if os.name != "nt":
        assert windows == [] and winfront.explorer_windows() == set()
        assert winfront.force_foreground(1) is False
    done = threading.Event()
    done.set()
    winfront.front_window_of(0, done, wait_s=0.2)  # returns at once; its thread ends by itself
    # the File Explorer windows open now are left alone: none of a developer's comes in front
    winfront.front_explorer_window(winfront.explorer_windows(), None, wait_s=0.2)


@pytest.mark.anyio
async def test_the_page_asks_for_a_folder_in_the_systems_own_dialog(
    home: Path, tmp_path: Path
) -> None:
    asked: list[tuple[str, Path | None]] = []

    def dialog(prompt: str, start: Path | None) -> Path | None:
        asked.append((prompt, start))
        return None if prompt == "cancel" else tmp_path / "X-Plane 12"

    app, mgr = _app(home, folder_dialog=dialog)
    try:
        async with client_for(app) as c:
            r = await c.post(
                "/api/choose-folder", json={"prompt": "Choose X-Plane", "start": str(tmp_path)}
            )
            assert r.status_code == 200 and r.json() == {"path": str(tmp_path / "X-Plane 12")}
            r = await c.post(
                "/api/choose-folder", json={"prompt": "cancel", "start": "/no/such/folder"}
            )
            assert r.status_code == 200 and r.json() == {"path": None}
            assert asked == [
                ("Choose X-Plane", tmp_path),
                ("cancel", None),
            ]  # no folder to start in
            r = await c.post("/api/choose-folder", json={"prompt": ""})
            assert r.status_code == 422
    finally:
        mgr.close()

    def none(prompt: str, start: Path | None) -> Path | None:
        raise FileNotFoundError("no folder dialog on this system: install zenity or kdialog")

    app, mgr = _app(home, folder_dialog=none)
    try:
        async with client_for(app) as c:
            r = await c.post("/api/choose-folder", json={"prompt": "Choose X-Plane"})
            assert r.status_code == 501 and r.json()["error"]["code"] == "SYS_NO_FOLDER_DIALOG"
            assert "zenity" in r.json()["error"]["remedy"]
    finally:
        mgr.close()
