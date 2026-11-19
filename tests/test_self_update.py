# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A newer OrthoStudio XP installed from the app's own banner (``orthostudio.self_update``).

The banner had a link alone: the user downloaded the installer, quit the app, replaced it, and on
macOS opened it past Gatekeeper again (2026-11-17). *Install and relaunch* does it: these tests go
through the real download (a server on the loopback), the real macOS script (a real disk image,
mounted and copied by the system's own tools) and the real engine routes.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.server
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import test_api_fakes as fakes
from orthostudio import self_update
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.self_update import Here, InstallCancelledError, Installer, Running, UpdateError
from test_api_fakes import FakeBuild, client_for, make_spec

anyio_backend = fakes.anyio_backend
home = fakes.home

PACKAGE = Path(__file__).resolve().parents[1] / "tools" / "package"
on_a_mac = pytest.mark.skipif(sys.platform != "darwin", reason="macOS's own tools")


# -- where the app is -----------------------------------------------------------------------------


def _mac_app(folder: Path, *, version: str = "2.2.0", ident: str = self_update.BUNDLE_ID) -> Path:
    """An app laid out as the macOS installer's: its Python under Contents/Resources."""
    app = folder / "OrthoStudio XP.app"
    (app / "Contents" / "Resources" / "python" / "bin").mkdir(parents=True)
    (app / "Contents" / "MacOS").mkdir()
    (app / "Contents" / "Resources" / "python" / "bin" / "python3").write_text("")
    (app / "Contents" / "Resources" / "version.txt").write_text(version)
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": ident, "CFBundleShortVersionString": version})
    )
    return app


def _python_of(app: Path) -> str:
    return str(app / "Contents" / "Resources" / "python" / "bin" / "python3")


def test_an_app_installed_by_its_installer_can_replace_itself(tmp_path: Path) -> None:
    app = _mac_app(tmp_path / "Applications")
    here = self_update.installed_here(_python_of(app), system="darwin", machine="arm64")
    assert here == Here("macos", "arm64", app)
    intel = self_update.installed_here(_python_of(app), system="darwin", machine="x86_64")
    assert intel is not None and intel.machine == "x86_64"

    folder = tmp_path / "Programs" / "OrthoStudio XP"
    (folder / "python").mkdir(parents=True)
    (folder / "python" / "pythonw.exe").write_text("")
    (folder / self_update.WINDOWS_UNINSTALLER).write_text("")
    here = self_update.installed_here(str(folder / "python" / "pythonw.exe"), system="win32")
    assert here == Here("windows", "x64", folder)


def _linux_folder(parent: Path, *, version: str = "2.2.0", name: str = "OrthoStudio-XP") -> Path:
    """A folder laid out as the Linux archive's: its Python, its launcher, its install.sh."""
    folder = parent / name
    package = folder / "python" / "lib" / "python3.14" / "site-packages" / "orthostudio"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f'"""x"""\n\n__version__ = "{version}"\n')
    (folder / "python" / "bin").mkdir()
    (folder / "python" / "bin" / "python3.14").write_text("")
    os.symlink("python3.14", folder / "python" / "bin" / "python3")
    for name_ in ("orthostudio-xp", "install.sh"):
        (folder / name_).write_text("#!/bin/sh\n")
        os.chmod(folder / name_, 0o755)
    return folder


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX links")
def test_a_linux_folder_extracted_from_the_release_can_replace_itself(tmp_path: Path) -> None:
    folder = _linux_folder(tmp_path / "apps")
    python = str(folder / "python" / "bin" / "python3")
    here = self_update.installed_here(python, system="linux", machine="x86_64")
    assert here == Here("linux", "x86_64", folder)
    assert self_update.installed_here(python, system="linux", machine="aarch64") is None
    (folder / "install.sh").unlink()  # not the release's folder
    assert self_update.installed_here(python, system="linux", machine="x86_64") is None


def test_what_cannot_replace_itself_keeps_the_link(tmp_path: Path) -> None:
    """A checkout, the Windows zip (no uninstaller: its user put it where he wanted), an app
    of another identifier, an app macOS runs from a read-only copy until it is moved, an app on a
    machine the release has no installer for: the banner keeps its link."""
    app = _mac_app(tmp_path / "Applications")
    python = _python_of(app)
    assert self_update.installed_here(python, system="linux") is None  # a Mac app's layout
    assert self_update.installed_here(python, system="darwin", machine="ppc") is None
    checkout = str(tmp_path / ".venv" / "bin" / "python")
    assert self_update.installed_here(checkout, system="darwin") is None
    other = _mac_app(tmp_path / "Other", ident="org.example.app")
    assert self_update.installed_here(_python_of(other), system="darwin", machine="arm64") is None
    moved = _mac_app(tmp_path / "AppTranslocation" / "X" / "d")
    assert self_update.installed_here(_python_of(moved), system="darwin", machine="arm64") is None

    zipped = tmp_path / "zip" / "OrthoStudio XP"
    (zipped / "python").mkdir(parents=True)
    (zipped / "python" / "pythonw.exe").write_text("")
    unzipped = str(zipped / "python" / "pythonw.exe")
    assert self_update.installed_here(unzipped, system="win32") is None


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX modes, not root")
def test_an_app_in_a_folder_that_cannot_be_written_keeps_the_link(tmp_path: Path) -> None:
    """The Applications folder of a Mac account that is not an administrator."""
    app = _mac_app(tmp_path / "Applications")
    os.chmod(app.parent, 0o555)
    try:
        assert self_update.installed_here(_python_of(app), system="darwin", machine="arm64") is None
    finally:
        os.chmod(app.parent, 0o755)


def test_the_installer_downloaded_is_the_release_s_own() -> None:
    """The name the packaging gives each installer, and the identifier of the macOS app."""
    sys.path.insert(0, str(PACKAGE))
    try:
        import build
    finally:
        sys.path.remove(str(PACKAGE))
    assert self_update.BUNDLE_ID == build.BUNDLE_ID
    for system, machine in (
        ("macos", "arm64"),
        ("macos", "x86_64"),
        ("windows", "x64"),
        ("linux", "x86_64"),
    ):
        target = build.Target(system=system, machine=machine)  # type: ignore[call-arg]
        here = Here(system, machine, Path("/x"))
        names = self_update.installer_names("2.3.0", here)
        assert names[0] == build.artefact_name("2.3.0", target)
    # Linux's archive may become a .tar.xz: an app of today takes it too
    linux = self_update.installer_names("2.3.0", Here("linux", "x86_64", Path("/x")))
    assert linux == (
        "OrthoStudio-XP-2.3.0-linux-x86_64.tar.gz",
        "OrthoStudio-XP-2.3.0-linux-x86_64.tar.xz",
    )
    assert self_update.download_url("2.3.0", "f.dmg") == (
        "https://github.com/radioharris/orthostudio-xp/releases/download/v2.3.0/f.dmg"
    )


# -- what GitHub says of the file -----------------------------------------------------------------


class _Answer:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self, n: int = -1) -> bytes:
        return self.body

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _app(home: Path, **kw: Any) -> tuple[Any, JobManager]:
    mgr = JobManager(jobs_dir=home / "jobs", build=kw.pop("build", FakeBuild()), env_factory=None)  # type: ignore[arg-type]
    app = create_app(env_factory=None, jobs=mgr, settings_path=home / "config.toml", **kw)
    return app, mgr


def test_the_size_and_sha256_come_from_the_release(monkeypatch: pytest.MonkeyPatch) -> None:
    digest = "ab" * 32
    release = {
        "assets": [
            {"name": "other.dmg", "size": 5, "digest": "sha256:" + "cd" * 32},
            {"name": "f.dmg", "size": 1234, "digest": "sha256:" + digest.upper()},
        ]
    }
    asked: list[str] = []

    def answer(request: Any, timeout: float) -> _Answer:
        asked.append(request.full_url)
        return _Answer(json.dumps(release).encode())

    monkeypatch.setattr(self_update.urllib.request, "urlopen", answer)
    assert self_update.asset_facts("2.3.0", ["f.dmg"]) == ("f.dmg", 1234, digest)
    assert asked == ["https://api.github.com/repos/radioharris/orthostudio-xp/releases/tags/v2.3.0"]
    # the first of the names the release has
    assert self_update.asset_facts("2.3.0", ["f.tar.gz", "f.dmg"]) == ("f.dmg", 1234, digest)
    # no such file, no SHA-256, another kind of digest: nothing to check against, nothing installed
    assert self_update.asset_facts("2.3.0", ["g.dmg"]) is None
    release["assets"] = [{"name": "f.dmg", "size": 1234}]
    assert self_update.asset_facts("2.3.0", ["f.dmg"]) is None
    release["assets"] = [{"name": "f.dmg", "size": 1234, "digest": "md5:abc"}]
    assert self_update.asset_facts("2.3.0", ["f.dmg"]) is None

    def offline(request: Any, timeout: float) -> _Answer:
        raise OSError("no network")

    monkeypatch.setattr(self_update.urllib.request, "urlopen", offline)
    assert self_update.asset_facts("2.3.0", ["f.dmg"]) is None


# -- the download ---------------------------------------------------------------------------------


@pytest.fixture
def served() -> Iterator[dict[str, Any]]:
    """A server on the loopback answering ``/f`` with ``body`` (``cut``: the connection closed half
    way), counting its requests."""
    state: dict[str, Any] = {"body": b"", "hits": 0, "cut": False}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state["hits"] += 1
            if self.path != "/f":
                self.send_error(404)
                return
            body = state["body"]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body[: len(body) // 2] if state["cut"] else body)

        def log_message(self, *args: Any) -> None:
            return None

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.handle_error = lambda request, address: None  # type: ignore[method-assign]  # a cut
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    state["url"] = f"http://127.0.0.1:{server.server_address[1]}/f"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def test_the_download_is_kept_only_whole_and_right(tmp_path: Path, served: dict[str, Any]) -> None:
    body = os.urandom(3 * self_update.CHUNK + 1000)  # several reads: the bar moves
    served["body"] = body
    dest = tmp_path / "update" / "f.dmg"
    seen: list[int] = []
    self_update.download(
        served["url"], dest, size=len(body), sha256=_sha(body), progress=seen.append
    )
    assert dest.read_bytes() == body and seen[-1] == len(body) and len(seen) > 1
    assert not dest.with_name("f.dmg.part").exists()

    # there already and right: not downloaded again (a second try after a question)
    hits = served["hits"]
    self_update.download(served["url"], dest, size=len(body), sha256=_sha(body))
    assert served["hits"] == hits

    # another file than the release's: refused, nothing left under either name
    other = tmp_path / "update" / "g.dmg"
    with pytest.raises(UpdateError) as caught:
        self_update.download(served["url"], other, size=len(body), sha256="0" * 64)
    assert caught.value.reason == "damaged"
    assert list(other.parent.iterdir()) == [dest]
    # longer than GitHub says: stopped at once
    with pytest.raises(UpdateError) as caught:
        self_update.download(served["url"], other, size=10, sha256=_sha(body))
    assert caught.value.reason == "damaged" and list(other.parent.iterdir()) == [dest]


def test_a_download_cut_or_refused_says_so(tmp_path: Path, served: dict[str, Any]) -> None:
    body = os.urandom(300_000)
    served.update(body=body, cut=True)
    dest = tmp_path / "f.dmg"
    with pytest.raises(UpdateError) as caught:
        self_update.download(served["url"], dest, size=len(body), sha256=_sha(body))
    assert caught.value.reason == "download"  # a cut, not a damaged file
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(UpdateError) as caught:
        self_update.download(served["url"] + "x", dest, size=len(body), sha256=_sha(body))
    assert caught.value.reason == "download" and list(tmp_path.iterdir()) == []


def test_cancel_leaves_nothing(tmp_path: Path, served: dict[str, Any]) -> None:
    body = os.urandom(2_000_000)
    served["body"] = body
    dest = tmp_path / "f.dmg"
    with pytest.raises(InstallCancelledError):
        self_update.download(
            served["url"], dest, size=len(body), sha256=_sha(body), cancelled=lambda: True
        )
    assert list(tmp_path.iterdir()) == []


# -- the last try ---------------------------------------------------------------------------------


def test_the_last_try_is_said_until_its_version_starts(tmp_path: Path) -> None:
    pending = tmp_path / self_update.UPDATE_DIR / self_update.PENDING_NAME
    assert self_update.last_try(tmp_path, "2.2.0") is None
    pending.parent.mkdir()
    # handed over, and the app never came back in the new version: no reason known
    pending.write_text(json.dumps({"version": "2.3.0", "at": 1}))
    assert self_update.last_try(tmp_path, "2.2.0") == {"version": "2.3.0", "reason": "unfinished"}
    pending.write_text(json.dumps({"version": "2.3.0", "at": 1, "reason": "replace"}))
    assert self_update.last_try(tmp_path, "2.2.0") == {"version": "2.3.0", "reason": "replace"}
    pending.write_text(json.dumps({"version": "2.3.0", "reason": "bogus"}))
    assert self_update.last_try(tmp_path, "2.2.0") == {"version": "2.3.0", "reason": "unfinished"}
    # the version tried runs now: installed, and the record goes
    assert self_update.last_try(tmp_path, "2.3.0") is None and not pending.exists()
    pending.write_text("not json")
    assert self_update.last_try(tmp_path, "2.2.0") is None


# -- the new app made ready, then swapped in, on macOS, for real ----------------------------------


def _image(tmp_path: Path, version: str, *, ident: str = self_update.BUNDLE_ID) -> Path:
    """A disk image holding an app at ``version``, made by macOS's own hdiutil: an HFS+ image as
    ``makehybrid`` writes it, mounted as the release's is (``create`` takes 4 s an image)."""
    # not a name ending in .app: hdiutil would take the folder for the app itself
    source = tmp_path / f"image-{version}-{ident.replace('.', '-')}"
    _mac_app(source, version=version, ident=ident)
    (source / "OrthoStudio XP.app" / "Contents" / "Resources" / "big").write_bytes(
        os.urandom(3_000_000)
    )
    image = tmp_path / f"OrthoStudio-XP-{version}-{ident.replace('.', '-')}.dmg"
    subprocess.run(["hdiutil", "makehybrid", "-quiet", "-hfs", "-o", image, source], check=True)
    return image


@pytest.fixture
def mounts(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The system's temporary folder, where the image is mounted, a folder of its own: macOS
    refuses a mount point on another disk (the tests' folder may be on one), and each test sees
    that its mount point went."""
    folder = Path(tempfile.mkdtemp(prefix="osxp-test-mounts-"))
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _version_of(app: Path) -> str:
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    return str(info["CFBundleShortVersionString"])


def _beside(app: Path) -> list[str]:
    return sorted(p.name for p in app.parent.iterdir())


@on_a_mac
def test_the_new_app_is_made_ready_beside_the_old_one(tmp_path: Path, mounts: Path) -> None:
    """While the app is still open: the old one untouched, the new one in a hidden folder of the
    same folder, the bar told what share is copied, the image let go."""
    app = _mac_app(tmp_path / "Applications")
    shares: list[float] = []
    staged = self_update.stage_app(_image(tmp_path, "2.3.0"), app, "2.3.0", progress=shares.append)
    assert _version_of(app) == "2.2.0" and _version_of(staged) == "2.3.0"
    assert staged.name == app.name and staged.parent.parent == app.parent
    assert staged.parent.name.startswith(self_update.STAGING_PREFIX)
    assert (staged / "Contents" / "Resources" / "big").stat().st_size == 3_000_000
    assert shares[-1] == 1.0 and shares == sorted(shares)
    assert list(mounts.iterdir()) == []


@on_a_mac
def test_an_image_of_another_app_or_version_leaves_nothing(tmp_path: Path, mounts: Path) -> None:
    app = _mac_app(tmp_path / "Applications")
    for image in (_image(tmp_path, "2.4.0"), _image(tmp_path, "2.3.0", ident="org.example")):
        with pytest.raises(UpdateError) as caught:
            self_update.stage_app(image, app, "2.3.0")
        assert caught.value.reason == "damaged"
        assert _beside(app) == [app.name] and list(mounts.iterdir()) == []
    (tmp_path / "not.dmg").write_text("not an image")
    with pytest.raises(UpdateError) as caught:
        self_update.stage_app(tmp_path / "not.dmg", app, "2.3.0")
    assert caught.value.reason == "damaged" and list(mounts.iterdir()) == []


@on_a_mac
def test_cancel_while_the_app_is_copied_leaves_nothing(tmp_path: Path, mounts: Path) -> None:
    app = _mac_app(tmp_path / "Applications")
    with pytest.raises(InstallCancelledError):
        self_update.stage_app(_image(tmp_path, "2.3.0"), app, "2.3.0", cancelled=lambda: True)
    assert _beside(app) == [app.name] and list(mounts.iterdir()) == []


def _staged(app: Path, version: str = "2.3.0") -> Path:
    """A new app made ready as :func:`stage_app` leaves it."""
    staging = Path(tempfile.mkdtemp(prefix=self_update.STAGING_PREFIX, dir=app.parent))
    _mac_app(staging, version=version)
    return staging / app.name


def _run_script(tmp_path: Path, app: Path, staged: Path, wait_s: int = 5) -> Path:
    """The script, run as the app runs it, reopening by a mark rather than by ``open``."""
    reopened = tmp_path / "reopened"
    script = tmp_path / "swap.sh"
    script.write_text(
        self_update.swap_script(
            app=app,
            staged=staged,
            version="2.3.0",
            pending=tmp_path / "pending.json",
            log=tmp_path / "serve.log",
            wait_s=wait_s,
            reopen=("touch", str(reopened)),
        )
    )
    subprocess.run(["/bin/bash", str(script)], check=False, timeout=60)
    return reopened


@pytest.mark.skipif(sys.platform == "win32", reason="bash")
def test_the_script_swaps_the_new_app_in_and_opens_it(tmp_path: Path) -> None:
    app = _mac_app(tmp_path / "Applications")
    reopened = _run_script(tmp_path, app, _staged(app))
    assert _version_of(app) == "2.3.0" and reopened.exists()
    assert _beside(app) == [app.name]  # the old app gone, nothing hidden left
    assert not (tmp_path / "pending.json").exists()
    assert "installed" in (tmp_path / "serve.log").read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="bash and pgrep")
def test_an_app_that_does_not_quit_is_left_as_it_is(tmp_path: Path) -> None:
    """Something still runs from the app after the wait: nothing is touched, nothing opened, the
    new app made ready taken away."""
    app = _mac_app(tmp_path / "Applications")
    staged = _staged(app)
    # a process whose command line starts with the app's path, as the app's Python's does (a copy
    # of a system program does not run outside its folder)
    sleeper = app / "python" / "bin" / "sleeper"  # anywhere in it: a Linux folder has no Contents
    running = subprocess.Popen([str(sleeper), "60"], executable="/bin/sleep")
    try:
        time.sleep(0.2)
        reopened = _run_script(tmp_path, app, staged, wait_s=1)
    finally:
        running.kill()
        running.wait()
    assert _version_of(app) == "2.2.0" and not reopened.exists() and _beside(app) == [app.name]
    assert json.loads((tmp_path / "pending.json").read_text())["reason"] == "not_quit"


@pytest.mark.skipif(sys.platform == "win32", reason="bash and pgrep")
def test_the_script_waits_for_the_app_that_started_it(tmp_path: Path) -> None:
    """The engine starts the script: it and the window are the script's ancestors, which macOS's
    pgrep leaves out unless asked. The script swapped the apps at once, the old one running
    (found in review, 2026-11-17)."""
    app = _mac_app(tmp_path / "Applications")
    staged = _staged(app)
    script = tmp_path / "swap.sh"
    reopened = tmp_path / "reopened"
    script.write_text(
        self_update.swap_script(
            app=app,
            staged=staged,
            version="2.3.0",
            pending=tmp_path / "pending.json",
            log=tmp_path / "serve.log",
            reopen=("touch", str(reopened)),
        )
    )
    seen = tmp_path / "seen"
    version = app / "Contents" / "Resources" / "version.txt"
    # the "engine": a process run from the app, which starts the script and lives on 1.5 s
    engine = subprocess.Popen(
        [
            str(app / "Contents" / "Resources" / "python" / "bin" / "python3"),
            "-c",
            f"/bin/bash '{script}' & sleep 1.5; cat '{version}' > '{seen}'",
        ],
        executable="/bin/bash",
    )
    engine.wait(timeout=20)
    for _ in range(200):
        if reopened.exists():
            break
        time.sleep(0.05)
    assert seen.read_text() == "2.2.0"  # the old app in place until the engine was gone
    assert reopened.exists() and _version_of(app) == "2.3.0"


@pytest.mark.skipif(sys.platform == "win32", reason="bash")
def test_a_pgrep_that_fails_counts_as_an_app_that_does_not_quit(tmp_path: Path) -> None:
    """An error of pgrep (an option it does not know, none on the system) must not read as « no
    app running »: the apps would be swapped under a running one."""
    app = _mac_app(tmp_path / "Applications")
    staged = _staged(app)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "pgrep").write_text("#!/bin/sh\nexit 2\n")
    os.chmod(tools / "pgrep", 0o755)
    script = tmp_path / "swap.sh"
    reopened = tmp_path / "reopened"
    script.write_text(
        self_update.swap_script(
            app=app,
            staged=staged,
            version="2.3.0",
            pending=tmp_path / "pending.json",
            log=tmp_path / "serve.log",
            reopen=("touch", str(reopened)),
        )
    )
    env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}
    subprocess.run(["/bin/bash", str(script)], check=False, env=env, timeout=30)
    assert _version_of(app) == "2.2.0" and not reopened.exists()
    assert json.loads((tmp_path / "pending.json").read_text())["reason"] == "not_quit"


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX modes, not root")
def test_an_app_that_cannot_be_replaced_stays_and_opens(tmp_path: Path) -> None:
    """Its folder could be written when the page asked, and no longer can."""
    app = _mac_app(tmp_path / "Applications")
    staged = _staged(app)
    os.chmod(app.parent, 0o555)
    try:
        reopened = _run_script(tmp_path, app, staged)
    finally:
        os.chmod(app.parent, 0o755)
    assert _version_of(app) == "2.2.0" and reopened.exists()
    assert json.loads((tmp_path / "pending.json").read_text())["reason"] == "replace"


@pytest.mark.skipif(sys.platform == "win32", reason="bash")
def test_the_script_names_the_app_by_its_path_alone() -> None:
    """``pgrep -f`` reads a regular expression: the app's path is matched as itself, from the
    start of a command line, so that the script's own does not count."""
    assert self_update._ere("/A/OrthoStudio XP (2).app") == "/A/OrthoStudio XP \\(2\\)\\.app"
    script = self_update.swap_script(
        app=Path("/Applications/OrthoStudio XP.app"),
        staged=Path("/Applications/.osxp-update.x/OrthoStudio XP.app"),
        version="2.3.0",
        pending=Path("/h/u/pending.json"),
        log=Path("/l/serve.log"),
    )
    assert "mine='^/Applications/OrthoStudio XP\\.app/'" in script
    assert "open '/Applications/OrthoStudio XP.app'" in script


# -- the new folder made ready, then swapped in, on Linux, for real --------------------------------


def _archive(tmp_path: Path, version: str, *, extra: str | None = None) -> Path:
    """The release's archive as tools/package/build.py writes it: one folder, OrthoStudio-XP."""
    source = _linux_folder(tmp_path / f"src-{version}", version=version)
    (source / "big").write_bytes(os.urandom(2_000_000))
    archive = tmp_path / f"OrthoStudio-XP-{version}-linux-x86_64.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="OrthoStudio-XP")
        if extra is not None:
            stray = tmp_path / "stray"
            stray.write_text("x")
            tar.add(stray, arcname=extra)
    return archive


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX links and modes")
def test_the_new_folder_is_made_ready_beside_the_old_one(tmp_path: Path) -> None:
    """Under the old folder's own name, links and modes kept, the bar told the share read."""
    folder = _linux_folder(tmp_path / "apps", name="OrthoStudio XP mine")
    shares: list[float] = []
    staged = self_update.stage_folder(
        _archive(tmp_path, "2.3.0"), folder, "2.3.0", progress=shares.append
    )
    assert staged.name == folder.name and staged.parent.parent == folder.parent
    assert staged.parent.name.startswith(self_update.STAGING_PREFIX)
    assert self_update._folder_version(staged) == "2.3.0"
    assert self_update._folder_version(folder) == "2.2.0"
    assert os.readlink(staged / "python" / "bin" / "python3") == "python3.14"
    assert os.access(staged / "orthostudio-xp", os.X_OK)
    assert (staged / "big").stat().st_size == 2_000_000
    assert shares[-1] == 1.0 and shares == sorted(shares)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX links and modes")
@pytest.mark.parametrize("trouble", ["version", "outside", "not an archive", "cancel"])
def test_a_wrong_archive_or_a_cancel_leaves_nothing(tmp_path: Path, trouble: str) -> None:
    folder = _linux_folder(tmp_path / "apps")
    archive = _archive(
        tmp_path,
        "2.4.0" if trouble == "version" else "2.3.0",
        extra="elsewhere/x" if trouble == "outside" else None,
    )
    if trouble == "not an archive":
        archive.write_bytes(b"not an archive")
    if trouble == "cancel":
        with pytest.raises(InstallCancelledError):
            self_update.stage_folder(archive, folder, "2.3.0", cancelled=lambda: True)
    else:
        with pytest.raises(UpdateError) as caught:
            self_update.stage_folder(archive, folder, "2.3.0")
        assert caught.value.reason == "damaged"
    assert sorted(p.name for p in folder.parent.iterdir()) == [folder.name]


@pytest.mark.skipif(sys.platform == "win32", reason="bash")
def test_the_script_swaps_the_new_folder_in_and_starts_it(tmp_path: Path) -> None:
    """Its launcher started apart, the script ending at once: it serves the page until the app
    quits."""
    folder = _linux_folder(tmp_path / "apps")
    staging = Path(tempfile.mkdtemp(prefix=self_update.STAGING_PREFIX, dir=folder.parent))
    staged = _linux_folder(staging, version="2.3.0")
    started = tmp_path / "started"
    (staged / "orthostudio-xp").write_text(f"#!/bin/sh\ntouch '{started}'\nsleep 30\n")
    script = tmp_path / "swap.sh"
    script.write_text(
        self_update.swap_script(
            app=folder,
            staged=staged,
            version="2.3.0",
            pending=tmp_path / "pending.json",
            log=tmp_path / "serve.log",
        )
    )
    began = time.monotonic()
    subprocess.run(["/bin/bash", str(script)], check=True, timeout=20)
    assert time.monotonic() - began < 10  # not held by the app it started
    for _ in range(100):
        if started.exists():
            break
        time.sleep(0.05)
    assert started.exists()
    assert self_update._folder_version(folder) == "2.3.0"
    assert sorted(p.name for p in folder.parent.iterdir()) == [folder.name]
    subprocess.run(["pkill", "-f", f"^/bin/sh {folder}/orthostudio-xp"], check=False)


# -- one try, as the page follows it --------------------------------------------------------------


class _Doubles:
    """What the installer reaches outside: GitHub, the download, the copy, the hand-over, what the
    app's quitting would stop, the app's quit."""

    def __init__(self, *, found: bool = True) -> None:
        self.found = found
        self.running = [Running(build=False, live=False, flying=False)]  # one per question
        self.calls: list[str] = []
        self.fetch_error: BaseException | None = None
        self.stage_error: BaseException | None = None
        self.hand_error: BaseException | None = None
        self.on_stage: Any = None
        self.told: Any = lambda: None  # called after each progress the doubles report

    def installer(self, home: Path) -> Installer:
        app = home / "OrthoStudio XP.app"
        (app / "Contents").mkdir(parents=True, exist_ok=True)
        (app / "Contents" / "a").write_bytes(b"x" * 300)  # the copy's share: 300 bytes
        return Installer(
            home=home,
            here=Here("macos", "arm64", app),
            log=home / "serve.log",
            running=self.asked,
            quit_app=lambda: self.calls.append("quit"),
            facts=lambda version, names: (names[0], 100, "ab" * 32) if self.found else None,
            fetch=self.fetch,
            stage=self.stage,
            hand=self.hand,
        )

    def asked(self) -> Running:
        return self.running.pop(0) if len(self.running) > 1 else self.running[0]

    def fetch(self, url: str, dest: Path, **kw: Any) -> None:
        self.calls.append(f"fetch {url.rsplit('/', 1)[-1]}")
        if self.fetch_error is not None:
            if isinstance(self.fetch_error, InstallCancelledError):
                while not kw["cancelled"]():
                    time.sleep(0.01)
            raise self.fetch_error
        kw["progress"](kw["size"])
        self.told()

    def stage(self, image: Path, app: Path, version: str, **kw: Any) -> Path:
        self.calls.append(f"stage {image.name}")
        if self.stage_error is not None:
            raise self.stage_error
        kw["progress"](0.5)
        self.told()
        kw["progress"](1.0)
        self.told()
        if self.on_stage is not None:
            self.on_stage()
        staging = app.parent / (self_update.STAGING_PREFIX + "t")
        staging.mkdir()
        return staging / app.name

    def hand(self, here: Here, installer: Path, version: str, **kw: Any) -> None:
        self.calls.append(f"hand {installer.name} {kw['staged'].parent.name}")
        if self.hand_error is not None:
            raise self.hand_error


def _pending(home: Path) -> dict[str, Any]:
    return json.loads((home / self_update.UPDATE_DIR / self_update.PENDING_NAME).read_text())


BUSY = Running(build=True, live=True, flying=True)
FREE = Running(build=False, live=False, flying=False)


def test_a_try_downloads_makes_ready_hands_over_then_quits(tmp_path: Path) -> None:
    d = _Doubles()
    installer = d.installer(tmp_path)
    seen: list[tuple[int, int]] = []

    def told() -> None:
        now = installer.status()
        seen.append((now["done"], now["total"]))

    d.told = told
    installer.start("2.3.0", force=False)
    installer.join(10)
    assert installer.status() == {"state": "installing", "version": "2.3.0"}
    name = "OrthoStudio-XP-2.3.0-macos-arm64.dmg"
    assert d.calls == [f"fetch {name}", f"stage {name}", f"hand {name} .osxp-update.t", "quit"]
    # one bar: the download's 100 bytes then the copy's 300 (the app as installed), never back
    assert seen == [(100, 400), (250, 400), (400, 400)]
    assert _pending(tmp_path)["version"] == "2.3.0" and "reason" not in _pending(tmp_path)


def test_what_installs_it_starting_is_said_apart(tmp_path: Path) -> None:
    """Between the bar and the app's quit: on Windows, Windows checks the installer first."""
    d = _Doubles()
    installer = d.installer(tmp_path)
    during: list[str] = []
    real_hand = d.hand

    def hand(*args: Any, **kw: Any) -> None:
        during.append(installer.status()["state"])
        real_hand(*args, **kw)

    installer._hand = hand
    installer.start("2.3.0", force=False)
    installer.join(10)
    assert during == ["starting"] and installer.status()["state"] == "installing"


def test_what_runs_is_asked_about_first_and_a_not_now_leaves_nothing(tmp_path: Path) -> None:
    """A build or Live at work when the try starts: nothing is downloaded, the page asks; « Not
    now » leaves no trace, not even a try said at the next start."""
    d = _Doubles()
    d.running = [BUSY]
    installer = d.installer(tmp_path)
    installer.start("2.3.0", force=False)
    assert installer.status() == {
        "state": "ask",
        "version": "2.3.0",
        "running": {"build": True, "live": True, "flying": True},
    }
    assert d.calls == [] and self_update.last_try(tmp_path, "2.2.0") is None
    installer.start("2.3.0", force=True)  # the user said yes
    installer.join(10)
    assert installer.status()["state"] == "installing" and d.calls[-1] == "quit"


def test_what_starts_while_it_prepares_is_asked_about_before_the_hand_over(tmp_path: Path) -> None:
    """Free when the try started, busy when it is ready: asked again, nothing handed over, the copy
    taken away and the try not said at the next start; the download kept for the yes."""
    d = _Doubles()
    d.running = [FREE, BUSY]
    installer = d.installer(tmp_path)
    installer.start("2.3.0", force=False)
    installer.join(10)
    assert installer.status()["state"] == "ask"
    assert not any(c.startswith("hand") for c in d.calls) and "quit" not in d.calls
    assert not (tmp_path / (self_update.STAGING_PREFIX + "t")).exists()
    assert self_update.last_try(tmp_path, "2.2.0") is None


def test_a_cancel_answered_yes_is_honoured_at_the_end_of_the_copy(tmp_path: Path) -> None:
    """*Cancel* clicked while the copy ends: the app does not quit and install all the same."""
    d = _Doubles()
    installer = d.installer(tmp_path)
    d.on_stage = installer.cancel  # answered while it is still preparing
    installer.start("2.3.0", force=False)
    installer.join(10)
    assert installer.status() == {"state": "idle"}
    assert not any(c.startswith("hand") for c in d.calls) and "quit" not in d.calls
    assert not (tmp_path / (self_update.STAGING_PREFIX + "t")).exists()


@pytest.mark.parametrize(
    ("trouble", "reason"),
    [
        ("no facts", "release"),
        ("download", "download"),
        ("damaged", "damaged"),
        ("copy", "room"),
        ("hand", "start"),
        ("bug", "start"),
    ],
)
def test_a_try_that_fails_says_why_now_and_at_the_next_start(
    tmp_path: Path, trouble: str, reason: str
) -> None:
    d = _Doubles(found=trouble != "no facts")
    if trouble in ("download", "damaged"):
        d.fetch_error = UpdateError(trouble)
    if trouble == "copy":
        d.stage_error = UpdateError("room")
    if trouble == "hand":
        d.hand_error = UpdateError("start")
    if trouble == "bug":
        d.hand_error = RuntimeError("a bug")
    installer = d.installer(tmp_path)
    installer.start("2.3.0", force=True)
    installer.join(10)
    status = installer.status()
    assert status["state"] == "failed" and status["reason"] == reason
    assert "quit" not in d.calls  # the app stays: nothing was handed over
    assert not (tmp_path / (self_update.STAGING_PREFIX + "t")).exists()  # nor made ready
    assert self_update.last_try(tmp_path, "2.2.0") == {"version": "2.3.0", "reason": reason}


def test_cancel_stops_the_try_and_forgets_it(tmp_path: Path) -> None:
    d = _Doubles()
    d.fetch_error = InstallCancelledError()
    installer = d.installer(tmp_path)
    installer.start("2.3.0", force=False)
    time.sleep(0.05)
    assert installer.busy()
    installer.start("2.3.0", force=False)  # one at a time: the try goes on
    assert installer.status()["state"] == "preparing"
    assert installer.cancel()
    installer.join(10)
    assert installer.status() == {"state": "idle"}
    assert self_update.last_try(tmp_path, "2.2.0") is None
    assert not installer.cancel()  # nothing is under way now


def test_what_earlier_tries_left_goes_when_the_engine_starts(tmp_path: Path) -> None:
    """The installer Windows ran and its log, a download kept for a failed try, an old script,
    and a new app made ready that a quit cut short: gone; the record of the last try kept."""
    folder = tmp_path / self_update.UPDATE_DIR
    folder.mkdir()
    for name in ("OrthoStudio-XP-2.2.5-windows-x64-setup.exe", "setup.log", "swap.sh"):
        (folder / name).write_text("old")
    (folder / self_update.PENDING_NAME).write_text("{}")
    left = tmp_path / "apps" / (self_update.STAGING_PREFIX + "old") / "OrthoStudio XP.app"
    left.mkdir(parents=True)
    self_update.tidy(tmp_path, Here("macos", "arm64", tmp_path / "apps" / "OrthoStudio XP.app"))
    assert sorted(p.name for p in folder.iterdir()) == [self_update.PENDING_NAME]
    assert not left.parent.exists()


def test_a_try_takes_away_another_version_s_installer(tmp_path: Path) -> None:
    folder = tmp_path / self_update.UPDATE_DIR
    folder.mkdir()
    (folder / "OrthoStudio-XP-2.2.5-macos-arm64.dmg").write_text("old")
    d = _Doubles()
    d.fetch_error = UpdateError("download")
    installer = d.installer(tmp_path)
    installer.start("2.3.0", force=True)
    installer.join(10)
    assert sorted(p.name for p in folder.iterdir()) == [self_update.PENDING_NAME]


# -- the engine's routes --------------------------------------------------------------------------


class _Recorder:
    """An installer as the routes see it: what it was asked, and nothing done."""

    def __init__(self, here: Here | None) -> None:
        self.here = here
        self.started: list[tuple[str, bool]] = []
        self.cancelled = 0

    def busy(self) -> bool:
        return False

    def start(self, version: str, *, force: bool) -> None:
        self.started.append((version, force))

    def status(self) -> dict[str, Any]:
        return {"state": "preparing", "version": "999.0.0", "done": 0, "total": 0}

    def cancel(self) -> bool:
        self.cancelled += 1
        return True


@pytest.fixture
def newer(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub names 999.0.0, and no Live answers: nothing leaves the machine."""
    from orthostudio import update
    from orthostudio.api import app as app_module

    real_check = update.check
    monkeypatch.setattr(
        update,
        "check",
        lambda current, *, path: real_check(current, path=path, fetch=lambda: "999.0.0"),
    )
    monkeypatch.setattr(app_module, "_live_status", lambda: None)


@pytest.mark.anyio
async def test_the_page_is_asked_first_then_the_engine_quits_as_for_quit(
    home: Path, newer: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine's own installer: asked first while a build runs, and while Live flies (what
    Live's status says); after the yes, handed over, Live asked to quit, the builds cancelled and
    the engine stopped, as the Windows installer and the page's Quit make it do."""
    from orthostudio.api import app as app_module

    asked: list[str] = []
    handed: list[str] = []
    monkeypatch.setattr(app_module, "_ask_live_to_quit", lambda: asked.append("live"))
    here = Here("macos", "arm64", home / "A.app")
    monkeypatch.setattr(self_update, "installed_here", lambda: here)
    monkeypatch.setattr(
        self_update, "asset_facts", lambda version, names: (names[0], 3, _sha(b"abc"))
    )

    def fetch(url: str, dest: Path, **kw: Any) -> None:
        handed.append("fetched")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"abc")

    monkeypatch.setattr(self_update, "download", fetch)
    monkeypatch.setattr(self_update, "stage_app", lambda image, app, version, **kw: home / "n.app")
    monkeypatch.setattr(
        self_update, "hand_over", lambda here, installer, version, **kw: handed.append(version)
    )
    stopped = threading.Event()
    app, mgr = _app(home, shutdown=stopped.set, build=FakeBuild(delay_s=0.5))
    try:
        async with client_for(app) as c:
            answer = (await c.get("/api/update")).json()
            assert answer["available"] and answer["installable"] and answer["last_try"] is None
            # a build runs: asked first, with what runs, and nothing done
            job = mgr.start([make_spec("+46+006", home=home)])
            r = await c.post("/api/update/install", json={})
            assert r.status_code == 202, r.text
            assert r.json()["state"] == "ask"
            assert r.json()["running"] == {"build": True, "live": False, "flying": False}
            assert handed == []
            # Live at work, X-Plane running: said too, from Live's own status
            live = {"state": "streaming", "busy": True, "checks": {"xplane_running": True}}
            monkeypatch.setattr(app_module, "_live_status", lambda: live)
            r = await c.post("/api/update/install", json={})
            assert r.json()["running"] == {"build": True, "live": True, "flying": True}
            # the user said yes
            r = await c.post("/api/update/install", json={"force": True})
            assert r.status_code == 202, r.text
            for _ in range(100):
                if stopped.is_set():
                    break
                await asyncio.sleep(0.05)
            assert stopped.is_set() and handed == ["fetched", "999.0.0"] and asked == ["live"]
            assert (await c.get("/api/update/install")).json()["state"] == "installing"
            assert job.wait(30) and job.status == "cancelled"
    finally:
        mgr.close()


def test_a_live_that_listens_and_does_not_answer_counts_as_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking costs a question; not asking could stop a flight."""
    from orthostudio.live import app_start

    monkeypatch.setattr(app_start, "live_listening", lambda port=0: True)
    monkeypatch.setattr(app_start, "_ask_live", lambda *a, **kw: None)
    assert app_start.live_status() == {"busy": True}
    assert app_start.live_state() == "off"  # what the Build page's Live button says, as before
    monkeypatch.setattr(app_start, "live_listening", lambda port=0: False)
    assert app_start.live_status() is None


@pytest.mark.anyio
async def test_the_engine_takes_away_what_earlier_tries_left(home: Path, newer: None) -> None:
    here = Here("macos", "arm64", home / "apps" / "OrthoStudio XP.app")
    left = home / "apps" / (self_update.STAGING_PREFIX + "old") / "OrthoStudio XP.app"
    left.mkdir(parents=True)
    (home / self_update.UPDATE_DIR).mkdir()
    (home / self_update.UPDATE_DIR / "setup.log").write_text("old")
    import orthostudio.self_update as module

    original = module.installed_here
    module.installed_here = lambda: here  # type: ignore[assignment]
    app, mgr = _app(home, shutdown=lambda: None)
    try:
        async with client_for(app) as c:
            assert (await c.get("/api/update")).json()["installable"]
            for _ in range(100):  # the hidden folder first, then the update folder's files
                if not left.parent.exists() and not any((home / self_update.UPDATE_DIR).iterdir()):
                    break
                await asyncio.sleep(0.05)
            assert not left.parent.exists()
            assert list((home / self_update.UPDATE_DIR).iterdir()) == []
    finally:
        module.installed_here = original
        mgr.close()


@pytest.mark.anyio
async def test_cancel_reaches_the_try(home: Path, newer: None) -> None:
    recorder = _Recorder(Here("macos", "arm64", home / "OrthoStudio XP.app"))
    app, mgr = _app(home, shutdown=lambda: None, updater=recorder)
    try:
        async with client_for(app) as c:
            r = await c.post("/api/update/install", json={"force": True})
            assert r.status_code == 202 and recorder.started == [("999.0.0", True)]
            assert (await c.get("/api/update/install")).json()["state"] == "preparing"
            assert (await c.delete("/api/update/install")).json() == {"cancelled": True}
            assert recorder.cancelled == 1
    finally:
        mgr.close()


@pytest.mark.anyio
async def test_an_app_that_cannot_replace_itself_keeps_its_link(home: Path, newer: None) -> None:
    """A checkout, the zip, an app that cannot be written; or an engine the page cannot stop
    (not started by osxp serve): no button, and the route refuses."""
    for kwargs in ({"updater": _Recorder(None), "shutdown": lambda: None}, {}):
        app, mgr = _app(home, **kwargs)
        try:
            async with client_for(app) as c:
                answer = (await c.get("/api/update")).json()
                assert answer["available"] and answer["installable"] is False
                r = await c.post("/api/update/install", json={"force": True})
                assert r.status_code == 409
                assert r.json()["error"]["code"] == "SYS_UPDATE_NOT_HERE"
        finally:
            mgr.close()


@pytest.mark.anyio
async def test_nothing_newer_nothing_installed(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from orthostudio import __version__, update
    from orthostudio.api import app as app_module

    real_check = update.check
    monkeypatch.setattr(
        update,
        "check",
        lambda current, *, path: real_check(current, path=path, fetch=lambda: __version__),
    )
    monkeypatch.setattr(app_module, "_live_status", lambda: None)
    recorder = _Recorder(Here("macos", "arm64", home / "OrthoStudio XP.app"))
    app, mgr = _app(home, shutdown=lambda: None, updater=recorder)
    try:
        async with client_for(app) as c:
            r = await c.post("/api/update/install", json={"force": True})
            assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_UPDATE_NONE"
            assert recorder.started == []
    finally:
        mgr.close()


@pytest.mark.anyio
async def test_the_banner_says_why_the_last_try_failed(home: Path, newer: None) -> None:
    pending = home / self_update.UPDATE_DIR / self_update.PENDING_NAME
    pending.parent.mkdir(parents=True)
    pending.write_text(json.dumps({"version": "999.0.0", "at": 1, "reason": "replace"}))
    recorder = _Recorder(Here("macos", "arm64", home / "OrthoStudio XP.app"))
    app, mgr = _app(home, shutdown=lambda: None, updater=recorder)
    try:
        async with client_for(app) as c:
            answer = (await c.get("/api/update")).json()
            assert answer["last_try"] == {"version": "999.0.0", "reason": "replace"}
    finally:
        mgr.close()
