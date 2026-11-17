# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A newer OrthoStudio XP installed from the app's own banner: *Install and relaunch*.

The banner of a new version (:mod:`orthostudio.update`) had a link to the release page alone: the
user downloaded the installer, quit the app, replaced it and, on macOS, opened it past Gatekeeper
once more (the user, 2026-11-17: « on peut pas faire une installation automatique ? »). Where the
app can replace itself, the banner now installs the new version. The installer of this system is
downloaded from the release and checked against the size and the SHA-256 GitHub gives for it; the
app makes ready what it can while it is still open, one bar for everyone (« Le même affichage pour
tout le monde »); then it hands over to what puts the new version in place, and quits; the new
version opens by itself.

- macOS: the app of the disk image is copied beside the old one, in a hidden folder of the same
  folder (:func:`stage_app`), while the app is still open: the bar counts the copy after the
  download. A shell script run by the system's ``/bin/bash`` then waits until nothing runs from the
  old app, swaps the two by two renames, and opens the new one (:func:`mac_script`); a failure puts
  the old one back and opens it. The copy was first made by the script once the app had quit: 30 s
  with no window and half an app in Applications (measured on 2026-11-17, a 2.2.0rc1 updating
  itself to 2.2.0). Tried the same day: macOS neither stops nor asks about an app without a
  signature that replaces itself, and the copy, made from a file no browser downloaded, opens
  without Gatekeeper's *Open Anyway*.
- Windows: the installer itself, run without its pages into the folder the app runs from
  (:func:`windows_command`): Windows replaces no file a running app holds, so it does its work
  once the app has quit, its own progress showing. It stops what still runs as on any install
  (``tools/package/stop_running.pas``) and starts the app at its end.
- Linux, the Windows zip, and an app that cannot be written where it is (a Mac account that is not
  an administrator, an app opened from its disk image or from where the browser left it): the
  banner keeps its link, as before (:func:`installed_here` is ``None``).

The last try is kept in ``update/pending.json`` until the version it installs starts: a try that
did not end there is said in the banner with why (:func:`last_try`), and the user is not left
wondering why the app is still the old one.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import logging
import os
import platform
import plistlib
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orthostudio import update
from orthostudio.fsutil import NO_CONSOLE_WINDOW
from orthostudio.net import USER_AGENT

__all__ = [
    "BUNDLE_ID",
    "QUIT_WAIT_S",
    "REASONS",
    "Here",
    "InstallCancelledError",
    "Installer",
    "UpdateError",
    "asset_facts",
    "download",
    "download_url",
    "installed_here",
    "installer_name",
    "last_try",
    "mac_script",
    "stage_app",
    "windows_command",
]

APP_NAME = "OrthoStudio XP"
BUNDLE_ID = "org.orthostudio-xp.app"
"""The macOS app's identifier (``tools/package/build.py`` ``BUNDLE_ID``; a test keeps the two
equal): a disk image whose app has another is not put in place."""
FILE_STEM = "OrthoStudio-XP"
WINDOWS_UNINSTALLER = "unins000.exe"
"""What Inno Setup leaves in the folder it installed: the zip's folder has none, and is not
replaced (its user put it where he wanted, by hand)."""
UPDATE_DIR = "update"
PENDING_NAME = "pending.json"
SCRIPT_NAME = "install.sh"
SETUP_LOG_NAME = "setup.log"
STAGING_PREFIX = ".osxp-update."
"""The hidden folder beside the app where the new one is made ready, then the old one put aside."""
QUIT_WAIT_S = 120
"""How long the macOS script waits for the app to quit before giving up, the old app untouched.
The app stops a build or a stream on its way out, which takes seconds."""
CHUNK = 1024 * 1024
TIMEOUT_S = 30.0
HDIUTIL_TIMEOUT_S = 120.0
RELEASE_API = f"https://api.github.com/repos/{update.REPOSITORY}/releases/tags/v{{version}}"

log = logging.getLogger(__name__)

REASONS = ("release", "download", "damaged", "room", "start", "not_quit", "replace", "unfinished")
"""Why a try did not install its version, each said in the page's words (``app.update_why.*``):
GitHub named no installer for this system (``release``); the download stopped (``download``); the
file was not the release's, or its disk image not the app's (``damaged``); the disk was full
(``room``); what installs it could not be started (``start``); the app did not quit in time
(``not_quit``); the old app could not be replaced, and stayed (``replace``); the new version never
started, with nothing else known (``unfinished``: the Windows installer stopped, or the app was
ended in the middle)."""


class UpdateError(Exception):
    """A try that stops, with its reason (:data:`REASONS`)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


class InstallCancelledError(Exception):
    """The page's *Cancel*: not a failure, nothing kept."""


# -- where the app is -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Here:
    """An installed OrthoStudio XP that can replace itself."""

    system: str
    """``"macos"`` or ``"windows"``."""
    machine: str
    """``"arm64"`` or ``"x86_64"`` on macOS, ``"x64"`` on Windows: the installer of the same app."""
    place: Path
    """The app (``…/OrthoStudio XP.app``) on macOS, its installation folder on Windows."""


def installed_here(
    executable: str | None = None, *, system: str | None = None, machine: str | None = None
) -> Here | None:
    """This OrthoStudio XP when it was installed by its installer and can be replaced where it is;
    ``None`` for a checkout, Linux, the Windows zip, or an app that cannot be written.

    macOS: the app around the Python that runs (``<app>/Contents/Resources/python/bin``), whose
    identifier is :data:`BUNDLE_ID`, not under ``AppTranslocation`` (macOS runs a downloaded app
    from a read-only copy until it is moved), and whose folder and itself can be written: the
    Applications folder of a Mac account that is not an administrator cannot. Windows: the folder
    around the Python (``<folder>\\python``), holding :data:`WINDOWS_UNINSTALLER` and writable.
    """
    executable = sys.executable if executable is None else executable
    system = sys.platform if system is None else system
    if not executable:
        return None
    python = Path(os.path.abspath(executable))
    if system == "darwin":
        app = python.parents[4] if len(python.parents) > 4 else None
        if (
            app is None
            or app.suffix != ".app"
            or python.parent != app / "Contents" / "Resources" / "python" / "bin"
            or "/AppTranslocation/" in str(app)
        ):
            return None
        if _bundle_facts(app)[0] != BUNDLE_ID:
            return None
        if not (os.access(app, os.W_OK) and os.access(app.parent, os.W_OK)):
            return None
        mac = machine or platform.machine()
        if mac not in ("arm64", "x86_64"):
            return None
        return Here("macos", mac, app)
    if system == "win32":
        folder = python.parent.parent
        if python.parent.name.lower() != "python" or not (folder / WINDOWS_UNINSTALLER).is_file():
            return None
        if not os.access(folder, os.W_OK):
            return None
        return Here("windows", "x64", folder)
    return None


def _bundle_facts(app: Path) -> tuple[str | None, str | None]:
    """The identifier and the version of a macOS app, ``None`` for what cannot be read."""
    try:
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None, None
    if not isinstance(info, dict):
        return None, None
    ident, version = info.get("CFBundleIdentifier"), info.get("CFBundleShortVersionString")
    return (
        ident if isinstance(ident, str) else None,
        version if isinstance(version, str) else None,
    )


def installer_name(version: str, here: Here) -> str:
    """The release's file for this app (``tools/package/build.py`` ``artefact_name``)."""
    suffix = ".dmg" if here.system == "macos" else "-setup.exe"
    return f"{FILE_STEM}-{version}-{here.system}-{here.machine}{suffix}"


def download_url(version: str, name: str) -> str:
    """Built here, as the release page is (:func:`orthostudio.update.release_page`): the app only
    ever downloads from this repository's own releases, whatever an answer says."""
    return f"https://github.com/{update.REPOSITORY}/releases/download/v{version}/{name}"


def asset_facts(version: str, name: str, *, timeout: float = TIMEOUT_S) -> tuple[int, str] | None:
    """The size and the SHA-256 GitHub gives for the release's file ``name``; ``None`` without an
    answer, or when the release has no such file, or GitHub gives no SHA-256 for it.

    Without the two nothing is installed: they are what tells the release's file from a download
    cut short or changed on its way."""
    request = urllib.request.Request(
        RELEASE_API.format(version=version),
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            doc = json.loads(answer.read(1024 * 1024))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    assets = doc.get("assets") if isinstance(doc, dict) else None
    for asset in assets if isinstance(assets, list) else []:
        if not isinstance(asset, dict) or asset.get("name") != name:
            continue
        size, digest = asset.get("size"), asset.get("digest")
        if not isinstance(size, int) or size <= 0 or not isinstance(digest, str):
            return None
        kind, _, value = digest.partition(":")
        if kind != "sha256" or len(value) != 64:
            return None
        return size, value.lower()
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    url: str,
    dest: Path,
    *,
    size: int,
    sha256: str,
    progress: Callable[[int], None] = lambda done: None,
    cancelled: Callable[[], bool] = lambda: False,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout: float = TIMEOUT_S,
) -> None:
    """``url`` into ``dest``, kept only when it is ``size`` bytes of SHA-256 ``sha256``.

    A file already there and right is kept as it is: a second try after a question, or after a
    failure further on, does not download it again. The download goes to ``dest.part`` and takes
    its name once checked, so that a file named ``dest`` is always a whole and right one.
    """
    if dest.is_file() and dest.stat().st_size == size and _sha256(dest) == sha256:
        progress(size)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with opener(request, timeout=timeout) as answer, part.open("wb") as out:
            while chunk := answer.read(CHUNK):
                if cancelled():
                    raise InstallCancelledError
                done += len(chunk)
                if done > size:
                    raise UpdateError("damaged", f"more than the {size} bytes GitHub gives")
                out.write(chunk)
                digest.update(chunk)
                progress(done)
    except (InstallCancelledError, UpdateError):
        part.unlink(missing_ok=True)
        raise
    except OSError as exc:  # urllib's errors are OSErrors, a full disk too
        part.unlink(missing_ok=True)
        raise UpdateError("room" if exc.errno == errno.ENOSPC else "download", str(exc)) from exc
    if done != size or digest.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise UpdateError("damaged", f"{done} bytes of SHA-256 {digest.hexdigest()}")
    os.replace(part, dest)


# -- the new app made ready, on macOS -------------------------------------------------------------


def tree_bytes(top: Path) -> int:
    """The bytes of the files under ``top``, links not followed: what a copy of it writes."""
    total = 0
    for root, _dirs, files in os.walk(top):
        for name in files:
            with contextlib.suppress(OSError):
                st = os.lstat(os.path.join(root, name))
                if stat.S_ISREG(st.st_mode):
                    total += st.st_size
    return total


def _copy_app(
    source: Path,
    dest: Path,
    *,
    progress: Callable[[float], None],
    cancelled: Callable[[], bool],
) -> None:
    """``source`` copied to ``dest`` by ``ditto``, macOS's own copy (links, modes, times and
    attributes kept, as the Finder copies an app), ``progress`` told the share of its bytes
    already written there every half second: the bar counts what is copied, not a guess of the
    time. 30 s for the 307 MB of 2.2.0 out of its compressed image (2026-11-17)."""
    total = max(1, tree_bytes(source))
    copying = subprocess.Popen(
        ["ditto", str(source), str(dest)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=NO_CONSOLE_WINDOW,
    )
    try:
        while copying.poll() is None:
            if cancelled():
                raise InstallCancelledError
            progress(min(0.99, tree_bytes(dest) / total))
            time.sleep(0.5)
    finally:
        if copying.poll() is None:
            copying.kill()
            copying.wait()
    said = copying.stderr.read().decode("utf-8", "replace") if copying.stderr else ""
    if copying.returncode != 0:
        full = "No space left" in said
        raise UpdateError("room" if full else "replace", said.strip() or "ditto failed")
    progress(1.0)


def stage_app(
    image: Path,
    app: Path,
    version: str,
    *,
    progress: Callable[[float], None] = lambda share: None,
    cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    """The app of the disk image ``image``, copied into a hidden folder beside ``app`` (the same
    disk: the swap is then two renames) while the old app still runs; the new app's path.

    The image is mounted on a folder of the system's temporary folder (macOS refuses a mount point
    on another disk), its app checked (:data:`BUNDLE_ID` at ``version``) before and after the copy,
    and the image let go whatever happens. Nothing is left beside ``app`` when it fails or is
    cancelled."""
    mount = Path(tempfile.mkdtemp(prefix="osxp-update."))
    staging: Path | None = None
    try:
        attach = [
            "hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen",
            "-mountpoint", str(mount), str(image),
        ]  # fmt: skip
        try:
            subprocess.run(
                attach,
                check=True,
                capture_output=True,
                timeout=HDIUTIL_TIMEOUT_S,
                creationflags=NO_CONSOLE_WINDOW,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise UpdateError("damaged", f"the disk image does not mount: {exc}") from exc
        try:
            new = mount / f"{APP_NAME}.app"
            if _bundle_facts(new) != (BUNDLE_ID, version):
                raise UpdateError("damaged", f"the disk image holds no {APP_NAME} {version}")
            try:
                staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=app.parent))
            except OSError as exc:
                raise UpdateError("replace", str(exc)) from exc
            staged = staging / app.name
            _copy_app(new, staged, progress=progress, cancelled=cancelled)
            if _bundle_facts(staged) != (BUNDLE_ID, version):
                raise UpdateError("replace", "the copy does not read as the new version")
        finally:
            detach = ["hdiutil", "detach", str(mount), "-quiet"]
            for command in (detach, [*detach, "-force"]):
                try:
                    subprocess.run(
                        command,
                        check=True,
                        capture_output=True,
                        timeout=HDIUTIL_TIMEOUT_S,
                        creationflags=NO_CONSOLE_WINDOW,
                    )
                    break
                except (OSError, subprocess.SubprocessError):
                    continue
        return staged
    except BaseException:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        with contextlib.suppress(OSError):
            mount.rmdir()


def _tidy_staging(app: Path) -> None:
    """The hidden folders beside the app that an earlier try left (the app ended in the middle)."""
    with contextlib.suppress(OSError):
        for entry in app.parent.iterdir():
            if entry.name.startswith(STAGING_PREFIX) and entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)


# -- the last try ---------------------------------------------------------------------------------


def _tidy(folder: Path, keep: str) -> None:
    """The files of earlier tries out of ``folder``, but the record of the last try and ``keep``
    (and its ``.part``): an installer is kept after a failure, for the next try, until another
    version's takes its place."""
    try:
        entries = list(folder.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name in (PENDING_NAME, keep, keep + ".part") or not entry.is_file():
            continue
        with contextlib.suppress(OSError):
            entry.unlink()


def _pending(home: Path) -> Path:
    return home / UPDATE_DIR / PENDING_NAME


def _write_pending(home: Path, version: str, reason: str | None = None) -> None:
    doc: dict[str, Any] = {"version": version, "at": int(time.time())}
    if reason is not None:
        doc["reason"] = reason
    path = _pending(home)
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")


def last_try(home: Path, current: str) -> dict[str, str] | None:
    """The last try that did not install its version, ``{version, reason}``; ``None`` when there
    was none or it did (the version that runs is that one or a later one: its record goes)."""
    path = _pending(home)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = doc.get("version") if isinstance(doc, dict) else None
    if update.version_tuple(version) is None or not update.is_newer(version, current):
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    reason = doc.get("reason")
    return {"version": str(version), "reason": reason if reason in REASONS else "unfinished"}


# -- handing over ---------------------------------------------------------------------------------


def _ere(text: str) -> str:
    """``text`` as itself in a POSIX extended regular expression, which ``pgrep -f`` reads."""
    return "".join("\\" + c if c in ".[]()*+?{}|^$\\" else c for c in text)


def mac_script(
    *,
    app: Path,
    staged: Path,
    version: str,
    pending: Path,
    log: Path,
    wait_s: int = QUIT_WAIT_S,
    reopen: Sequence[str] | None = None,
) -> str:
    """The shell script that puts ``staged``, the new app made ready beside it, in the place of
    ``app`` once the app has quit.

    It waits until no process runs from ``app`` (the app's window, its engine, Live, Triangle4XP),
    ``wait_s`` at most, else gives up with the old app untouched and the copy taken away. It moves
    the old app into a hidden folder beside it, then the new one into its place: two renames on
    the same disk, an instant without an app. A failure puts the old app back. It opens the app
    either way (``reopen``, ``open <app>`` by default; tests pass another command), writes what
    went wrong in ``pending`` for the next start's banner, and its steps in ``log``,
    ``serve.log``."""
    q = shlex.quote
    opens = " ".join(q(part) for part in (reopen or ("open", str(app))))
    return f"""#!/bin/bash
# {APP_NAME} {version}, put in place of the app that made it ready (orthostudio/self_update.py).
app={q(str(app))}
staged={q(str(staged))}
version={q(version)}
pending={q(str(pending))}
log={q(str(log))}
mine={q("^" + _ere(str(app)) + "/Contents/")}

say() {{
  printf -- '--- %s {APP_NAME} update to %s: %s\\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$version" \\
    "$*" >> "$log"
}}
failed() {{
  say "not installed ($1)"
  printf '{{"version": "%s", "at": %s, "reason": "%s"}}\\n' "$version" "$(date +%s)" "$1" \\
    > "$pending"
}}
reopen() {{ {opens} >> "$log" 2>&1 || say "could not open $app"; }}

say "waiting for {APP_NAME} to quit"
tries=0
while pgrep -f "$mine" > /dev/null; do
  if [ "$tries" -ge {wait_s * 5} ]; then rm -rf "$(dirname "$staged")"; failed not_quit; exit 1; fi
  sleep 0.2
  tries=$((tries + 1))
done
aside="$(dirname "$staged")/old"
if ! mkdir "$aside" || ! mv "$app" "$aside/"; then
  rm -rf "$(dirname "$staged")"; failed replace; reopen; exit 1
fi
if mv "$staged" "$app"; then
  rm -rf "$(dirname "$staged")"
  say "installed"
else
  mv "$aside/$(basename "$app")" "$app" || say "the old app is in $aside"
  failed replace
fi
reopen
"""


def windows_command(setup: Path, folder: Path, log: Path) -> list[str]:
    """The installer run by the app's update: its progress alone (``/SILENT``: no page, the page
    asked the questions), its boxes answered as by default (``/SUPPRESSMSGBOXES``: stop a build or a
    stream still running, which the page asked about), into the folder the app runs from, its log
    in OrthoStudio XP's ``update`` folder, and the app started at its end (``/RELAUNCH=1``,
    ``tools/package/stop_running.pas`` ``RelaunchAsked``)."""
    return [
        str(setup),
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        f"/DIR={folder}",
        f"/LOG={log}",
        "/RELAUNCH=1",
    ]


def _clean_env() -> dict[str, str]:
    """The environment of what is handed over, without what points Python at the app's files."""
    return {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}


def hand_over(
    here: Here,
    installer: Path,
    version: str,
    *,
    home: Path,
    log: Path,
    staged: Path | None = None,
) -> None:
    """Start what puts the new version in place, in a process of its own that outlives the app:
    on macOS the script that swaps ``staged`` in (the disk image no longer needed), on Windows the
    installer. Raises :class:`UpdateError` ``start`` when it cannot be started."""
    try:
        if here.system == "macos":
            if staged is None:
                raise UpdateError("start", "no new app made ready")
            script = installer.parent / SCRIPT_NAME
            script.write_text(
                mac_script(
                    app=here.place,
                    staged=staged,
                    version=version,
                    pending=_pending(home),
                    log=log,
                ),
                encoding="utf-8",
            )
            subprocess.Popen(
                ["/bin/bash", str(script)],
                creationflags=NO_CONSOLE_WINDOW,  # 0 here; every launch says it (a test checks)
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd="/",
                env=_clean_env(),
                start_new_session=True,
                close_fds=True,
            )
            installer.unlink(missing_ok=True)
            return
        command = windows_command(installer, here.place, installer.parent / SETUP_LOG_NAME)
        detached = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        breakaway = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB: a job that ends with the app
        try:
            subprocess.Popen(
                command,
                creationflags=detached | breakaway,
                close_fds=True,
                env=_clean_env(),
                cwd=str(installer.parent),
            )
        except OSError:  # a job that refuses breaking away: the installer outlives the app anyway
            subprocess.Popen(
                command,
                creationflags=detached,
                close_fds=True,
                env=_clean_env(),
                cwd=str(installer.parent),
            )
    except OSError as exc:
        raise UpdateError("start", str(exc)) from exc


# -- one try, for the page ------------------------------------------------------------------------


@dataclass(frozen=True)
class Running:
    """What the app's quitting stops: the page asks before it does."""

    build: bool
    live: bool
    flying: bool

    def any(self) -> bool:
        return self.build or self.live


class Installer:
    """The page's *Install and relaunch*: one try at a time, in a thread of its own, its state
    asked by the page while it works (:meth:`status`).

    States: ``idle``; ``preparing``, ``done`` of ``total`` bytes, what the app does while it is
    still open: the download, then on macOS the copy of the new app (its share of ``total`` is the
    size of the app as installed, the new one's near enough, so that the bar never goes back);
    ``ask``, ready while a build or Live runs that the user did not agree to stop (``running`` says
    which: the page asks, and starts again with ``force``, the download kept); ``starting``, what
    puts the new version in place being started: on Windows that is the installer, which Windows
    checks before it lets a program it never saw start, 12 to 18 s on a Shadow PC (measured on
    2026-11-17), with nothing to count; ``installing``, handed over, the app quitting; ``failed``
    (``reason``, :data:`REASONS`), the try also kept for the next start.
    """

    def __init__(
        self,
        *,
        home: Path,
        here: Here | None,
        log: Path,
        running: Callable[[], Running],
        quit_app: Callable[[], None],
        facts: Callable[[str, str], tuple[int, str] | None] | None = None,
        fetch: Callable[..., None] | None = None,
        stage: Callable[..., Path] | None = None,
        hand: Callable[..., None] | None = None,
    ) -> None:
        """``facts``, ``fetch``, ``stage`` and ``hand`` replace :func:`asset_facts`,
        :func:`download`, :func:`stage_app` and :func:`hand_over` (tests pass doubles); left out,
        the module's own are read at each use."""
        self.home = home
        self.here = here
        self.log = log
        self._running = running
        self._quit_app = quit_app
        self._facts = facts
        self._fetch = fetch
        self._stage = stage
        self._hand = hand
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {"state": "idle"}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _set(self, **state: Any) -> None:
        with self._lock:
            self._state = state

    def busy(self) -> bool:
        with self._lock:
            return self._state["state"] in ("preparing", "starting", "installing")

    def start(self, version: str, *, force: bool) -> bool:
        """Start a try at ``version``; ``False`` while one is under way already."""
        with self._lock:
            if self._state["state"] in ("preparing", "starting", "installing"):
                return False
            self._cancel.clear()
            self._state = {"state": "preparing", "version": version, "done": 0, "total": 0}
            self._thread = threading.Thread(
                target=self._run, args=(version, force), name="self-update", daemon=True
            )
            self._thread.start()
        return True

    def cancel(self) -> bool:
        """The page's *Cancel*, while it prepares: nothing kept, nothing said next time."""
        with self._lock:
            if self._state["state"] != "preparing":
                return False
        self._cancel.set()
        return True

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self, version: str, force: bool) -> None:
        here = self.here
        staged: Path | None = None
        try:
            if here is None:
                raise UpdateError("start", "this app cannot replace itself")
            _write_pending(self.home, version)
            name = installer_name(version, here)
            _tidy(self.home / UPDATE_DIR, keep=name)
            if here.system == "macos":
                _tidy_staging(here.place)
            facts = (self._facts or asset_facts)(version, name)
            if facts is None:
                raise UpdateError("release", f"no {name} with its size and SHA-256 in the release")
            size, sha256 = facts
            copy = tree_bytes(here.place) if here.system == "macos" else 0
            total = size + copy

            def progress(done: int) -> None:
                self._set(state="preparing", version=version, done=done, total=total)

            progress(0)
            dest = self.home / UPDATE_DIR / name
            (self._fetch or download)(
                download_url(version, name),
                dest,
                size=size,
                sha256=sha256,
                progress=progress,
                cancelled=self._cancel.is_set,
            )
            if here.system == "macos":
                staged = (self._stage or stage_app)(
                    dest,
                    here.place,
                    version,
                    progress=lambda share: progress(size + int(copy * share)),
                    cancelled=self._cancel.is_set,
                )
            running = self._running()
            if running.any() and not force:
                _discard(staged)
                asked = {"build": running.build, "live": running.live, "flying": running.flying}
                self._set(state="ask", version=version, running=asked)
                return
            self._set(state="starting", version=version)
            (self._hand or hand_over)(
                here, dest, version, home=self.home, log=self.log, staged=staged
            )
            log.info("update to %s handed over (%s): the app quits", version, dest.name)
            self._set(state="installing", version=version)
            self._quit_app()
        except InstallCancelledError:
            _discard(staged)
            with contextlib.suppress(OSError):
                _pending(self.home).unlink()
            self._set(state="idle")
        except UpdateError as exc:
            _discard(staged)
            log.warning("update to %s not installed: %s (%s)", version, exc.reason, exc)
            _write_pending(self.home, version, exc.reason)
            self._set(state="failed", version=version, reason=exc.reason, detail=str(exc))
        except Exception as exc:  # never a try that says nothing
            _discard(staged)
            log.exception("update to %s not installed", version)
            _write_pending(self.home, version, "start")
            self._set(state="failed", version=version, reason="start", detail=repr(exc))


def _discard(staged: Path | None) -> None:
    """The new app made ready, and its hidden folder, taken away: a try that stops leaves nothing
    beside the app."""
    if staged is not None:
        shutil.rmtree(staged.parent, ignore_errors=True)
