# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A newer OrthoStudio XP installed from the app's own banner: *Install and relaunch*.

The banner of a new version (:mod:`orthostudio.update`) had a link to the release page alone: the
user downloaded the installer, quit the app, replaced it and, on macOS, opened it past Gatekeeper
once more (the user, 2026-11-17: « on peut pas faire une installation automatique ? »). Where the
app can replace itself, the banner now installs the new version: the installer of this system is
downloaded from the release, checked against the size and the SHA-256 GitHub gives for it, handed
over to what puts it in place, and the app quits; the new version opens by itself.

- macOS: a shell script written beside the download and run by the system's ``/bin/bash``, nothing
  of the app it replaces (:func:`mac_script`). It waits until nothing runs from the app any more,
  mounts the disk image, checks the app in it, puts the old app aside in its own folder, copies the
  new one in its place and opens it; any failure puts the old app back and opens that. Tried on a
  Mac on 2026-11-17: an app without a signature that replaces itself is neither stopped nor asked
  about by macOS, and the copy, made from a file no browser downloaded, opens without Gatekeeper's
  *Open Anyway*.
- Windows: the installer itself, run without its pages (its progress shows; the page asked the
  questions) into the folder the app runs from (:func:`windows_command`). It stops what still runs
  as on any install (``tools/package/stop_running.pas``) and starts the app at its end.
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
import subprocess
import sys
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
    "Installer",
    "asset_facts",
    "download",
    "download_url",
    "installed_here",
    "installer_name",
    "last_try",
    "mac_script",
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
QUIT_WAIT_S = 120
"""How long the macOS script waits for the app to quit before giving up, the old app untouched.
The app stops a build or a stream on its way out, which takes seconds."""
CHUNK = 256 * 1024
TIMEOUT_S = 30.0
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


class DownloadCancelledError(Exception):
    """The download stopped by the page's *Cancel*: not a failure, nothing kept."""


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
        try:
            info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException):
            return None
        if not isinstance(info, dict) or info.get("CFBundleIdentifier") != BUNDLE_ID:
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
                    raise DownloadCancelledError
                done += len(chunk)
                if done > size:
                    raise UpdateError("damaged", f"more than the {size} bytes GitHub gives")
                out.write(chunk)
                digest.update(chunk)
                progress(done)
    except DownloadCancelledError:
        part.unlink(missing_ok=True)
        raise
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    except OSError as exc:  # urllib's errors are OSErrors, a full disk too
        part.unlink(missing_ok=True)
        raise UpdateError("room" if exc.errno == errno.ENOSPC else "download", str(exc)) from exc
    if done != size or digest.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise UpdateError("damaged", f"{done} bytes of SHA-256 {digest.hexdigest()}")
    os.replace(part, dest)


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


# -- the last try ---------------------------------------------------------------------------------


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
    image: Path,
    version: str,
    pending: Path,
    log: Path,
    wait_s: int = QUIT_WAIT_S,
    reopen: Sequence[str] | None = None,
) -> str:
    """The shell script that puts the app of the disk image ``image`` in the place of ``app``.

    It waits until no process runs from ``app`` (the app's window, its engine, Live, Triangle4XP),
    ``wait_s`` at most, else gives up with the old app untouched. It mounts the image, checks that
    its app is OrthoStudio XP at ``version``, moves the old app into a folder of its own beside it
    (the same disk: a move is a rename), copies the new one into its place with ``ditto`` and reads
    its version back. Any failure puts the old app back. It opens the app either way (``reopen``,
    ``open <app>`` by default; tests pass another command), writes what went wrong in ``pending``
    for the next start's banner, and its steps in ``log``, ``serve.log``.
    """
    q = shlex.quote
    opens = " ".join(q(part) for part in (reopen or ("open", str(app))))
    plist = "/usr/libexec/PlistBuddy"
    return f"""#!/bin/bash
# {APP_NAME} {version}, put in place of the app that downloaded it (orthostudio/self_update.py).
app={q(str(app))}
image={q(str(image))}
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
read_key() {{ {plist} -c "Print :$1" "$2/Contents/Info.plist" 2>/dev/null; }}
detach() {{
  hdiutil detach "$mount" -quiet || hdiutil detach -force "$mount" -quiet
  rmdir "$mount" 2>/dev/null
}}

say "waiting for {APP_NAME} to quit"
tries=0
while pgrep -f "$mine" > /dev/null; do
  if [ "$tries" -ge {wait_s * 5} ]; then failed not_quit; exit 1; fi
  sleep 0.2
  tries=$((tries + 1))
done
mount=$(mktemp -d "${{TMPDIR:-/tmp}}/osxp-update.XXXXXX") || {{ failed replace; reopen; exit 1; }}
if ! hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$mount" "$image" > /dev/null; then
  rmdir "$mount"; failed damaged; reopen; exit 1
fi
new="$mount/{APP_NAME}.app"
ident=$(read_key CFBundleIdentifier "$new")
found=$(read_key CFBundleShortVersionString "$new")
if [ "$ident" != {q(BUNDLE_ID)} ] || [ "$found" != "$version" ]; then
  detach; failed damaged; reopen; exit 1
fi
aside=$(mktemp -d "$(dirname "$app")/.osxp-update.XXXXXX") || {{
  detach; failed replace; reopen; exit 1
}}
if ! mv "$app" "$aside/"; then
  rmdir "$aside"; detach; failed replace; reopen; exit 1
fi
old="$aside/$(basename "$app")"
if ditto "$new" "$app" && [ "$(read_key CFBundleShortVersionString "$app")" = "$version" ]; then
  detach
  rm -rf "$aside"
  rm -f "$image"
  say "installed"
else
  rm -rf "$app"
  if mv "$old" "$app"; then rmdir "$aside"; else say "the old app is in $aside"; fi
  detach
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


def hand_over(here: Here, installer: Path, version: str, *, home: Path, log: Path) -> None:
    """Start what puts ``installer`` in place, in a process of its own that outlives the app.
    Raises :class:`UpdateError` ``start`` when it cannot be started."""
    try:
        if here.system == "macos":
            script = installer.parent / SCRIPT_NAME
            script.write_text(
                mac_script(
                    app=here.place,
                    image=installer,
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
    asked by the page while it downloads (:meth:`status`).

    States: ``idle``; ``downloading`` (``done`` of ``total`` bytes); ``ask``, the file ready while
    a build or Live runs that the user did not agree to stop (``running`` says which: the page
    asks, and starts again with ``force``, the file kept); ``installing``, handed over, the app
    quitting; ``failed`` (``reason``, :data:`REASONS`), the try also kept for the next start.
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
        hand: Callable[..., None] | None = None,
    ) -> None:
        """``facts``, ``fetch`` and ``hand`` replace :func:`asset_facts`, :func:`download` and
        :func:`hand_over` (tests pass doubles); left out, the module's own are read at each use."""
        self.home = home
        self.here = here
        self.log = log
        self._running = running
        self._quit_app = quit_app
        self._facts = facts
        self._fetch = fetch
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
            return self._state["state"] in ("downloading", "installing")

    def start(self, version: str, *, force: bool) -> bool:
        """Start a try at ``version``; ``False`` while one downloads or installs already."""
        with self._lock:
            if self._state["state"] in ("downloading", "installing"):
                return False
            self._cancel.clear()
            self._state = {"state": "downloading", "version": version, "done": 0, "total": 0}
            self._thread = threading.Thread(
                target=self._run, args=(version, force), name="self-update", daemon=True
            )
            self._thread.start()
        return True

    def cancel(self) -> bool:
        """The page's *Cancel*, while the file downloads: nothing kept, nothing said next time."""
        with self._lock:
            if self._state["state"] != "downloading":
                return False
        self._cancel.set()
        return True

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self, version: str, force: bool) -> None:
        here = self.here
        try:
            if here is None:
                raise UpdateError("start", "this app cannot replace itself")
            _write_pending(self.home, version)
            name = installer_name(version, here)
            _tidy(self.home / UPDATE_DIR, keep=name)
            facts = (self._facts or asset_facts)(version, name)
            if facts is None:
                raise UpdateError("release", f"no {name} with its size and SHA-256 in the release")
            size, sha256 = facts
            self._set(state="downloading", version=version, done=0, total=size)

            def progress(done: int) -> None:
                self._set(state="downloading", version=version, done=done, total=size)

            dest = self.home / UPDATE_DIR / name
            (self._fetch or download)(
                download_url(version, name),
                dest,
                size=size,
                sha256=sha256,
                progress=progress,
                cancelled=self._cancel.is_set,
            )
            running = self._running()
            if running.any() and not force:
                asked = {"build": running.build, "live": running.live, "flying": running.flying}
                self._set(state="ask", version=version, running=asked)
                return
            (self._hand or hand_over)(here, dest, version, home=self.home, log=self.log)
            log.info("update to %s handed over (%s): the app quits", version, dest.name)
            self._set(state="installing", version=version)
            self._quit_app()
        except DownloadCancelledError:
            with contextlib.suppress(OSError):
                _pending(self.home).unlink()
            self._set(state="idle")
        except UpdateError as exc:
            log.warning("update to %s not installed: %s (%s)", version, exc.reason, exc)
            _write_pending(self.home, version, exc.reason)
            self._set(state="failed", version=version, reason=exc.reason, detail=str(exc))
        except Exception as exc:  # never a try that says nothing
            log.exception("update to %s not installed", version)
            _write_pending(self.home, version, "start")
            self._set(state="failed", version=version, reason="start", detail=repr(exc))
