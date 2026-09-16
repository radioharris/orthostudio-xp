"""Atomic file writes shared by every module that publishes a file.

One implementation of "write to a temporary name in the target directory, then ``os.replace``
over the final name": a reader sees the old file or the new one, never a partial one, and
two processes racing on the same path both leave a complete file (the last rename wins). The
temporary name carries the pid and a random token so that concurrent writers never collide.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path, PurePath

from orthostudio import winfront

__all__ = [
    "NO_CONSOLE_WINDOW",
    "REDIRECTION_GUARD_REMEDY",
    "atomic_link_or_copy",
    "atomic_write_bytes",
    "atomic_write_text",
    "fsync_dir",
    "raise_open_files_limit",
    "redirection_guard",
    "replace",
    "temp_name",
]

NO_CONSOLE_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)
"""``creationflags`` of every program OrthoStudio XP runs (DSFTool, Triangle4XP, tasklist, mklink,
nvcompress, the file manager and the folder dialog). The Windows app runs in ``pythonw``, which has
no console: each console program it started opened a console window of its own, which flashed on
the screen, and DSFTool ended with 0xC000013A (``STATUS_CONTROL_C_EXIT``) when that window closed
under it, failing the overlays of every tile (user report, 2026-09-15). 0 on other systems."""

PROCESS_REDIRECTION_TRUST_POLICY = 16
"""``ProcessRedirectionTrustPolicy`` (Windows' RedirectionGuard) for ``GetProcessMitigationPolicy``.
"""

REDIRECTION_GUARD_REMEDY = (
    "Quit OrthoStudio XP (Quit, at the top right) and open it again from the Start menu, not from "
    "the last page of its installer."
)
"""What to do when the app runs under :func:`redirection_guard`."""


def redirection_guard() -> bool:
    """Whether Windows' RedirectionGuard is enforced on this process. It then follows no junction
    made by an account without administrator rights, which includes the junctions an install makes
    in Custom Scenery (error 448, "untrusted mount point"), and neither do the programs it starts.

    Inno Setup enables it on its setup program since 6.7, and the app the installer's last page
    started had it: every install of a user's tiles failed (2026-09-15). ``False`` on other systems
    and when Windows cannot say (before Windows 11 and Windows 10 22H2)."""
    if os.name != "nt":
        return False
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    query = kernel32.GetProcessMitigationPolicy
    query.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
    flags = ctypes.c_uint32(0)
    process = kernel32.GetCurrentProcess()
    if not query(process, PROCESS_REDIRECTION_TRUST_POLICY, ctypes.byref(flags), 4):
        return False
    return bool(flags.value & 1)  # EnforceRedirectionTrust; bit 1 only audits


OPEN_FILES_WANTED = 8192
"""The soft limit of open files each command asks for (:func:`raise_open_files_limit`)."""


def raise_open_files_limit(wanted: int = OPEN_FILES_WANTED) -> tuple[int, int] | None:
    """Raise this process's soft limit of open files to ``wanted``, within the hard limit; the
    programs and worker processes it starts inherit it. ``(before, after)``; ``None`` on Windows,
    which has no such limit.

    macOS gives an app started from the Finder or the Dock 256 (launchd's ``maxfiles``), where a
    terminal has far more: an image download of 192 connections with the files it writes went past
    it, and textures of a user's tile failed with "Too many open files" (2026-09-15)."""
    try:
        import resource
    except ImportError:
        return None
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft == resource.RLIM_INFINITY or soft >= wanted:
        return soft, soft
    ceiling = wanted if hard == resource.RLIM_INFINITY else min(wanted, hard)
    for value in (ceiling, 4096, 2048, 1024):  # macOS refuses more than its per-process maximum
        if value <= soft:
            break
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (value, hard))
        except (ValueError, OSError):
            continue
        return soft, value
    return soft, soft


REPLACE_ATTEMPTS = 10
"""How many times :func:`replace` tries on Windows (0.55 s of waiting at most)."""


def temp_name(final: Path) -> Path:
    """A sibling temporary path of ``final`` unique to this process and call."""
    return final.with_name(f"{final.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")


def fsync_dir(d: Path) -> None:
    """Flush the directory entry to disk (no-op on Windows, silent on file systems refusing it)."""
    if os.name == "nt":
        return
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            os.fsync(fd)
    finally:
        os.close(fd)


def replace(src: Path, dst: Path) -> None:
    """``os.replace``, retried for an instant on Windows while another handle holds ``dst`` open.

    POSIX renames over an open file. Windows refuses with ``PermissionError`` as long as a reader
    has the target open (Python opens files without ``FILE_SHARE_DELETE``), which a concurrent
    read of a chunk container or a manifest does for a few milliseconds.
    """
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt" or attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.01 * (attempt + 1))


def _atomic(final: Path, produce: Callable[[Path], None], *, fsync: bool) -> Path:
    final = Path(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_name(final)
    try:
        produce(tmp)
        replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if fsync:
        fsync_dir(final.parent)
    return final


def atomic_write_bytes(path: Path, data: bytes, *, fsync: bool = False) -> Path:
    """Write ``data`` at ``path`` atomically; ``fsync`` also flushes the file and its directory."""

    def produce(tmp: Path) -> None:
        with open(tmp, "wb") as f:
            f.write(data)
            if fsync:
                f.flush()
                os.fsync(f.fileno())

    return _atomic(path, produce, fsync=fsync)


def atomic_write_text(
    path: Path, text: str, *, encoding: str = "utf-8", newline: str = "\n", fsync: bool = False
) -> Path:
    """Write ``text`` at ``path`` atomically with the given encoding and line ending."""

    def produce(tmp: Path) -> None:
        with open(tmp, "w", encoding=encoding, newline=newline) as f:
            f.write(text)
            if fsync:
                f.flush()
                os.fsync(f.fileno())

    return _atomic(path, produce, fsync=fsync)


def atomic_link_or_copy(src: Path, dest: Path, *, link: bool = True) -> Path:
    """Put ``src`` at ``dest`` atomically: a hard link when allowed and possible, else a copy."""

    def produce(tmp: Path) -> None:
        if link:
            try:
                os.link(src, tmp)
                return
            except OSError:
                pass
        shutil.copyfile(src, tmp)

    return _atomic(dest, produce, fsync=False)


def platform_name() -> str:
    """``mac``, ``win`` or ``lin``: what the page names the file manager after."""
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win"):
        return "win"
    return "lin"


def reveal_command(path: Path, platform: str | None = None) -> list[str]:
    """The command that shows ``path`` in the platform's file manager: a folder opened, a file
    selected in its folder (Finder, File Explorer; on Linux ``xdg-open`` opens the folder)."""
    name = platform or platform_name()
    folder = path.is_dir()
    if name == "mac":
        return ["open", str(path)] if folder else ["open", "-R", str(path)]
    if name == "win":
        return ["explorer", str(path)] if folder else ["explorer", f"/select,{path}"]
    return ["xdg-open", str(path if folder else path.parent)]


FOLDER_DIALOG_TIMEOUT_S = 900.0
"""How long a folder dialog may stay open before the engine stops waiting for it (15 minutes)."""


def _powershell_string(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def choose_folder_command(
    prompt: str, start: PurePath | None = None, platform: str | None = None
) -> list[str] | None:
    """The command that asks for a folder in the platform's own dialog and prints its path: the
    Finder's on macOS (``osascript``), the File Explorer's on Windows (PowerShell), zenity's or
    kdialog's on Linux; ``None`` on a Linux with neither (a user asked for a button instead of
    typing a path, 2026-09-15). ``start`` is the folder the dialog opens in."""
    name = platform or platform_name()
    if name == "mac":
        # JavaScript for Automation, not AppleScript: AppleScript's `activate` held the dialog back
        # 2 s (2.5 s against 0.5 s to show it, on a user's Mac, 2026-09-15). The panel comes in
        # front of the browser either way, at the level of modal panels.
        where = f", defaultLocation: Path({json.dumps(str(start))})" if start else ""
        script = (
            "var app = Application.currentApplication(); app.includeStandardAdditions = true; "
            "app.activate(); "
            f"app.chooseFolder({{withPrompt: {json.dumps(prompt)}{where}}}).toString()"
        )
        return ["osascript", "-l", "JavaScript", "-e", script]
    if name == "win":
        lines = [
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "Add-Type -AssemblyName System.Windows.Forms",
            "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}",
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
            f"$dialog.Description = {_powershell_string(prompt)}",
        ]
        if start:
            lines.append(f"$dialog.SelectedPath = {_powershell_string(str(start))}")
        lines.append(
            "if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK)"
            " { [Console]::Out.Write($dialog.SelectedPath) }"
        )
        return ["powershell", "-NoProfile", "-STA", "-Command", "; ".join(lines)]
    if shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", f"--title={prompt}"]
        return cmd + ([f"--filename={start}{os.sep}"] if start else [])
    if shutil.which("kdialog"):
        return ["kdialog", "--getexistingdirectory", str(start or Path.home()), "--title", prompt]
    return None


def choose_folder(prompt: str, start: Path | None = None) -> Path | None:
    """Ask the user for a folder in the platform's own dialog and wait for the answer: the folder,
    or ``None`` when they cancel. ``FileNotFoundError`` when the system has no such dialog."""
    cmd = choose_folder_command(prompt, start)
    if cmd is None:
        raise FileNotFoundError("no folder dialog on this system: install zenity or kdialog")
    with subprocess.Popen(  # a fixed program; the prompt and folder are quoted for it
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_CONSOLE_WINDOW,
    ) as dialog:
        answered = threading.Event()
        winfront.front_window_of(dialog.pid, answered)  # Windows: in front of the browser
        try:
            out, _err = dialog.communicate(timeout=FOLDER_DIALOG_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            dialog.kill()
            dialog.communicate()
            raise
        finally:
            answered.set()
    text = (out or "").strip()
    if dialog.returncode != 0 or not text:  # cancelled: osascript 1, zenity 1, nothing printed
        return None
    return Path(text.rstrip("/\\") or text)


def reveal_in_file_manager(path: Path) -> None:
    """Show ``path`` in Finder, File Explorer or the Linux file manager, without waiting.

    On Windows the File Explorer window is then brought in front of the browser: started by the
    engine, which runs in the background, it opened behind (a user, 2026-09-15)."""
    path = Path(path)
    before = winfront.explorer_windows()
    subprocess.Popen(  # a fixed program, and a path the caller checked
        reveal_command(path),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        creationflags=NO_CONSOLE_WINDOW,
    )
    winfront.front_explorer_window(before, path if path.is_dir() else path.parent)
