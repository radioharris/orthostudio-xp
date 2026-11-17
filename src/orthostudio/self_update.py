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
  old app, swaps the two by two renames, and opens the new one (:func:`swap_script`); a failure puts
  the old one back and opens it. The copy was first made by the script once the app had quit: 30 s
  with no window and half an app in Applications (measured on 2026-11-17, a 2.2.0rc1 updating
  itself to 2.2.0). Tried the same day: macOS neither stops nor asks about an app without a
  signature that replaces itself, and the copy, made from a file no browser downloaded, opens
  without Gatekeeper's *Open Anyway*.
- Linux: the same as macOS, for the folder the user extracted (``OrthoStudio-XP``): the new one is
  extracted from the release's archive into a hidden folder beside it while the app is open
  (:func:`stage_folder`), then the script swaps the two folders and starts the new one, which
  opens its page in the browser as ever. Asked for on 2026-11-17, and checked by the tests alone:
  there is no Linux desktop here to try it on.
- Windows: the installer itself, run without its pages into the folder the app runs from
  (:func:`windows_command`): Windows replaces no file a running app holds, so it does its work
  once the app has quit, its own progress showing. It stops what still runs as on any install
  (``tools/package/stop_running.pas``) and starts the app at its end.
- The Windows zip, and an app that cannot be written where it is (a Mac account that is not an
  administrator, an app opened from its disk image or from where the browser left it, a Linux
  folder of another user's): the banner keeps its link, as before (:func:`installed_here` is
  ``None``).

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
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
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
from orthostudio.fsutil import NO_CONSOLE_WINDOW, atomic_write_text
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
    "installer_names",
    "last_try",
    "stage_app",
    "stage_folder",
    "swap_script",
    "tidy",
    "windows_command",
]

APP_NAME = "OrthoStudio XP"
BUNDLE_ID = "org.orthostudio-xp.app"
"""The macOS app's identifier (``tools/package/build.py`` ``BUNDLE_ID``; a test keeps the two
equal): a disk image whose app has another is not put in place."""
FILE_STEM = "OrthoStudio-XP"
LINUX_LAUNCHER = "orthostudio-xp"
"""What starts the Linux app, at the top of its folder (``tools/package/build.py``), beside
``install.sh``: a folder without them is not the release's, and is not replaced."""
WINDOWS_UNINSTALLER = "unins000.exe"
"""What Inno Setup leaves in the folder it installed: the zip's folder has none, and is not
replaced (its user put it where he wanted, by hand)."""
UPDATE_DIR = "update"
PENDING_NAME = "pending.json"
SCRIPT_NAME = "swap.sh"
SETUP_LOG_NAME = "setup.log"
STAGING_PREFIX = ".osxp-update."
"""The hidden folder beside the app where the new one is made ready, then the old one put aside."""
QUIT_WAIT_S = 120
"""How long the swap script waits for the app to quit before giving up, the old app untouched.
The app stops a build or a stream on its way out, which takes seconds."""
UNDER_WAY = ("preparing", "starting", "installing")
"""The states of a try that is going on: one at a time, and the page follows it."""
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


def _can_write(folder: Path) -> bool:
    """Whether files can be made in ``folder``. On Windows a file is tried: ``os.access`` reads
    the read-only flag alone, not the folder's permissions, and would offer the update to an app a
    user installed in Program Files as an administrator, whose installer then fails without its
    rights, the app gone. Elsewhere the modes say it (and nothing is written inside a macOS app
    at each start)."""
    if sys.platform != "win32":
        return os.access(folder, os.W_OK)
    try:
        with tempfile.TemporaryFile(dir=folder):
            return True
    except OSError:
        return False


@dataclass(frozen=True)
class Here:
    """An installed OrthoStudio XP that can replace itself."""

    system: str
    """``"macos"``, ``"windows"`` or ``"linux"``."""
    machine: str
    """``"arm64"`` or ``"x86_64"`` on macOS, ``"x64"`` on Windows, ``"x86_64"`` on Linux: the
    installer of the same app."""
    place: Path
    """The app (``…/OrthoStudio XP.app``) on macOS, its folder on Windows and Linux."""


def installed_here(
    executable: str | None = None, *, system: str | None = None, machine: str | None = None
) -> Here | None:
    """This OrthoStudio XP when it was installed by its installer and can be replaced where it is;
    ``None`` for a checkout, the Windows zip, or an app that cannot be written.

    macOS: the app around the Python that runs (``<app>/Contents/Resources/python/bin``), whose
    identifier is :data:`BUNDLE_ID`, not under ``AppTranslocation`` (macOS runs a downloaded app
    from a read-only copy until it is moved), and whose folder and itself can be written: the
    Applications folder of a Mac account that is not an administrator cannot. Windows: the folder
    around the Python (``<folder>\\python``), holding :data:`WINDOWS_UNINSTALLER` and writable.
    Linux: the extracted folder around the Python (``<folder>/python/bin``), holding
    :data:`LINUX_LAUNCHER` and ``install.sh``, it and the folder above it writable, on a machine
    the release has an archive for.
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
        if not (_can_write(app) and _can_write(app.parent)):
            return None
        mac = machine or platform.machine()
        if mac not in ("arm64", "x86_64"):
            return None
        return Here("macos", mac, app)
    if system == "win32":
        folder = python.parent.parent
        if python.parent.name.lower() != "python" or not (folder / WINDOWS_UNINSTALLER).is_file():
            return None
        if not _can_write(folder):
            return None
        return Here("windows", "x64", folder)
    if system.startswith("linux"):
        if len(python.parents) < 3:
            return None
        top = python.parents[2]
        if (
            python.parent != top / "python" / "bin"
            or not (top / LINUX_LAUNCHER).is_file()
            or not (top / "install.sh").is_file()
        ):
            return None
        if not (_can_write(top) and _can_write(top.parent)):
            return None
        if (machine or platform.machine()) != "x86_64":
            return None
        return Here("linux", "x86_64", top)
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


SUFFIXES = {"macos": (".dmg",), "windows": ("-setup.exe",), "linux": (".tar.gz", ".tar.xz")}
"""The installers this app can install, by system, in the order they are looked for: Linux's
archive may become a ``.tar.xz`` (a smaller installer, planned), which an app of today must still
take, the name being chosen by the version that downloads it."""


def installer_names(version: str, here: Here) -> tuple[str, ...]:
    """The release's files this app can install (``tools/package/build.py`` ``artefact_name``)."""
    stem = f"{FILE_STEM}-{version}-{here.system}-{here.machine}"
    return tuple(stem + suffix for suffix in SUFFIXES[here.system])


def download_url(version: str, name: str) -> str:
    """Built here, as the release page is (:func:`orthostudio.update.release_page`): the app only
    ever downloads from this repository's own releases, whatever an answer says."""
    return f"https://github.com/{update.REPOSITORY}/releases/download/v{version}/{name}"


def asset_facts(
    version: str, names: Sequence[str], *, timeout: float = TIMEOUT_S
) -> tuple[str, int, str] | None:
    """The first of ``names`` the release has, with the size and the SHA-256 GitHub gives for it;
    ``None`` without an answer, or when the release has none of them, or GitHub gives no SHA-256.

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
    listed = assets if isinstance(assets, list) else []
    by_name = {a.get("name"): a for a in listed if isinstance(a, dict)}
    for name in names:
        asset = by_name.get(name)
        if asset is None:
            continue
        size, digest = asset.get("size"), asset.get("digest")
        if not isinstance(size, int) or size <= 0 or not isinstance(digest, str):
            return None
        kind, _, value = digest.partition(":")
        if kind != "sha256" or len(value) != 64:
            return None
        return name, size, value.lower()
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
    if done < size:  # the connection ended early, with no error: a cut, not a damaged file
        part.unlink(missing_ok=True)
        raise UpdateError("download", f"{done} of {size} bytes")
    if digest.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise UpdateError("damaged", f"{done} bytes of SHA-256 {digest.hexdigest()}")
    os.replace(part, dest)


# -- the new app made ready beside the old one, on macOS and Linux --------------------------------


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


_VERSION_LINE = re.compile(r'^__version__ = "([^"]+)"', re.MULTILINE)


def _folder_version(folder: Path) -> str | None:
    """The version of the Linux app in ``folder``, read from its package as it is written there."""
    for init in folder.glob("python/lib/python3*/site-packages/orthostudio/__init__.py"):
        try:
            found = _VERSION_LINE.search(init.read_text(encoding="utf-8"))
        except OSError:
            return None
        return found.group(1) if found else None
    return None


def stage_folder(
    archive: Path,
    folder: Path,
    version: str,
    *,
    progress: Callable[[float], None] = lambda share: None,
    cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    """The Linux app of the release's archive, extracted into a hidden folder beside ``folder``
    (the same disk: the swap is then two renames) while the old app still runs, under the old
    folder's name; the new folder's path.

    Every entry must lie in the archive's one folder (:data:`FILE_STEM`), and tarfile's ``data``
    filter refuses what would land outside it; the bar counts the archive's bytes read. The new
    folder must hold the launcher and the version asked. Nothing is left beside ``folder`` when it
    fails or is cancelled."""
    size = max(1, archive.stat().st_size)
    staging: Path | None = None
    try:
        try:
            staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=folder.parent))
        except OSError as exc:
            raise UpdateError("replace", str(exc)) from exc
        try:
            with archive.open("rb") as raw, tarfile.open(fileobj=raw, mode="r:*") as tar:
                for member in tar:
                    if cancelled():
                        raise InstallCancelledError
                    if member.name.split("/", 1)[0] != FILE_STEM:
                        raise UpdateError("damaged", f"{member.name} is outside {FILE_STEM}")
                    tar.extract(member, staging, filter="data")
                    progress(min(0.99, raw.tell() / size))
        except (tarfile.TarError, EOFError) as exc:
            raise UpdateError("damaged", f"the archive cannot be read: {exc}") from exc
        except OSError as exc:
            full = exc.errno == errno.ENOSPC
            raise UpdateError("room" if full else "replace", str(exc)) from exc
        staged = staging / folder.name
        if staged.name != FILE_STEM:
            (staging / FILE_STEM).rename(staged)
        if not (staged / LINUX_LAUNCHER).is_file() or _folder_version(staged) != version:
            raise UpdateError("damaged", f"the archive holds no {APP_NAME} {version}")
        progress(1.0)
        return staged
    except BaseException:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise


def tidy(home: Path, here: Here | None, *, keep: str | None = None) -> None:
    """What earlier tries left: the hidden folders beside the app (a try the app's quit cut
    short, a copy ``ditto`` finished alone), and the files of the ``update`` folder but the record
    of the last try and ``keep`` (with its ``.part``), the installer of the try under way: the
    installer Windows ran and its log, another version's installer, a download kept for a try that
    failed. Done when the engine first answers the page about updates, and as a try starts."""
    if here is not None:
        with contextlib.suppress(OSError):
            for entry in here.place.parent.iterdir():
                if entry.name.startswith(STAGING_PREFIX) and entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
    kept = {PENDING_NAME} | ({keep, keep + ".part"} if keep else set())
    try:
        entries = list((home / UPDATE_DIR).iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name not in kept and entry.is_file():
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
        atomic_write_text(path, json.dumps(doc))


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


def swap_script(
    *,
    app: Path,
    staged: Path,
    version: str,
    pending: Path,
    log: Path,
    wait_s: int = QUIT_WAIT_S,
    reopen: Sequence[str] | None = None,
) -> str:
    """The shell script that puts ``staged``, the new app made ready beside it (the macOS app, the
    Linux folder), in the place of ``app`` once the app has quit.

    It waits until no process runs from ``app`` (the app's window, its engine, Live, Triangle4XP),
    ``wait_s`` at most, else gives up with the old app untouched and the copy taken away. macOS's
    ``pgrep`` leaves out its own ancestors unless asked (``-a``), and the engine that starts the
    script and the window that started the engine are just those: without ``-a`` the script swapped
    the apps at once, the old one still running (found in review, 2026-11-17). On Linux ``-a``
    lists the command lines, which go nowhere. Only ``pgrep``'s « none » lets the script go on: an
    error of it gives up as an app that does not quit does, the old app untouched. It moves
    the old app into a hidden folder beside it, then the new one into its place: two renames on
    the same disk, an instant without an app. A failure puts the old app back. It opens the app
    either way (``reopen``, :func:`_reopen` by default; tests pass another command), writes what
    went wrong in ``pending`` for the next start's banner, and its steps in ``log``,
    ``serve.log``."""
    q = shlex.quote
    opens = " ".join(q(part) for part in (reopen or _reopen(app)))
    return f"""#!/bin/bash
# {APP_NAME} {version}, put in place of the app that made it ready (orthostudio/self_update.py).
app={q(str(app))}
staged={q(str(staged))}
version={q(version)}
pending={q(str(pending))}
log={q(str(log))}
mine={q("^" + _ere(str(app)) + "/")}

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
while true; do
  # -a: macOS's pgrep leaves out its ancestors, the engine and the window that started this
  pgrep -a -f "$mine" > /dev/null 2>&1
  found=$?
  [ "$found" -eq 1 ] && break  # nothing runs from the app any more
  if [ "$found" -ne 0 ] || [ "$tries" -ge {wait_s * 5} ]; then
    rm -rf "$(dirname "$staged")"; failed not_quit; exit 1
  fi
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


def _reopen(app: Path) -> tuple[str, ...]:
    """What opens the app the script put in place: the Finder's ``open`` for a macOS app; for a
    Linux folder its launcher, started apart and left to run (it serves the page until the app
    quits), the script ending at once."""
    if app.suffix == ".app":
        return ("open", str(app))
    launcher = shlex.quote(str(app / LINUX_LAUNCHER))
    return ("/bin/sh", "-c", f"{launcher} > /dev/null 2>&1 < /dev/null &")


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
    on macOS and Linux the script that swaps ``staged`` in (the downloaded file no longer needed),
    on Windows the installer. Raises :class:`UpdateError` ``start`` when it cannot be started."""
    try:
        if here.system in ("macos", "linux"):
            if staged is None:
                raise UpdateError("start", "no new app made ready")
            script = installer.parent / SCRIPT_NAME
            script.write_text(
                swap_script(
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

    def said(self) -> dict[str, bool]:
        return {"build": self.build, "live": self.live, "flying": self.flying}


class Installer:
    """The page's *Install and relaunch*: one try at a time, in a thread of its own, its state
    asked by the page while it works (:meth:`status`).

    States: ``idle``; ``ask``, a build or Live running that the app's quitting would stop
    (``running`` says which), asked when the try is started and again just before the hand-over,
    the page putting the question and starting again with ``force`` (a download already made is
    kept; nothing else of the try is); ``preparing``, ``done``
    of ``total`` bytes, what the app does while it is still open: the download, then on macOS
    and Linux the new app made ready beside the old one (its share of ``total`` is the size of
    the app as installed, the new one's near enough, so that the bar never goes back);
    ``starting``, what puts the new version in place being started: on Windows that is the
    installer, which Windows checks before it lets a program it never saw start, 12 to 18 s on a
    Shadow PC (measured on 2026-11-17), with nothing to count; ``installing``, handed over, the
    app quitting; ``failed`` (``reason``, :data:`REASONS`), the try also kept for the next
    start.
    """

    def __init__(
        self,
        *,
        home: Path,
        here: Here | None,
        log: Path,
        running: Callable[[], Running],
        quit_app: Callable[[], None],
        facts: Callable[[str, Sequence[str]], tuple[str, int, str] | None] | None = None,
        fetch: Callable[..., None] | None = None,
        stage: Callable[..., Path] | None = None,
        hand: Callable[..., None] | None = None,
    ) -> None:
        """``facts``, ``fetch``, ``stage`` and ``hand`` replace :func:`asset_facts`,
        :func:`download`, :func:`stage_app` or :func:`stage_folder`, and :func:`hand_over` (tests
        pass doubles); left out, the module's own are read at each use."""
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
            return self._state["state"] in UNDER_WAY

    def _asked(self, force: bool) -> Running | None:
        """What the app's quitting would stop, which the user did not agree to stop (``force``
        says they did): asked outside the lock, Live taking up to 1.5 s to answer."""
        if force:
            return None
        running = self._running()
        return running if running.any() else None

    def start(self, version: str, *, force: bool) -> None:
        """Start a try at ``version``, or say what it would stop (``ask``); nothing while one is
        under way already."""
        running = self._asked(force)
        with self._lock:
            if self._state["state"] in UNDER_WAY:
                return
            if running is not None:
                self._state = {"state": "ask", "version": version, "running": running.said()}
                return
            self._cancel.clear()
            self._state = {"state": "preparing", "version": version, "done": 0, "total": 0}
            self._thread = threading.Thread(
                target=self._run, args=(version, force), name="self-update", daemon=True
            )
            self._thread.start()

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
            names = installer_names(version, here)
            facts = (self._facts or asset_facts)(version, names)
            if facts is None:
                raise UpdateError("release", f"none of {names} with its size and SHA-256")
            name, size, sha256 = facts
            tidy(self.home, here, keep=name)
            beside = here.system in ("macos", "linux")
            copy = tree_bytes(here.place) if beside else 0
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
            if beside:
                made_ready = stage_app if here.system == "macos" else stage_folder
                staged = (self._stage or made_ready)(
                    dest,
                    here.place,
                    version,
                    progress=lambda share: progress(size + int(copy * share)),
                    cancelled=self._cancel.is_set,
                )
            # asked again: a build or Live started while it prepared; a Cancel answered yes is
            # honoured, and none is answered after
            running = None if self._cancel.is_set() else self._asked(force)
            with self._lock:
                cancelled = self._cancel.is_set()
                if not cancelled and running is not None:
                    self._state = {"state": "ask", "version": version, "running": running.said()}
                elif not cancelled:
                    self._state = {"state": "starting", "version": version}
            if cancelled:
                raise InstallCancelledError
            if running is not None:  # the question is the page's: nothing of this try is kept
                _discard(staged)
                with contextlib.suppress(OSError):
                    _pending(self.home).unlink()
                return
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
