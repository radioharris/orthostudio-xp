"""Find the X-Plane 12 folder and tell whether X-Plane is running.

Spec: ``docs/specs/install.md`` section 2. X-Plane's installer records every installation,
one absolute directory per line (trailing ``/`` included), in a per-user text file whose
location depends on the OS. Detection reads that file first, then the usual folders.

``xplane_running`` reads the process list without ``psutil``: ``/proc/<pid>/comm`` on Linux,
``ps -axo comm=`` on other POSIX systems, ``tasklist /FO CSV /NH`` on Windows. Only the
executable base name is compared, never the command line, so that ``orthostudio`` itself (whose
arguments may contain ``X-Plane 12``) is never mistaken for the simulator.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from orthostudio.fsutil import NO_CONSOLE_WINDOW

__all__ = [
    "SCENERY_OF_ITS_OWN",
    "XPLANE_DIR_ENV",
    "XPLANE_EXECUTABLES",
    "custom_scenery_dir",
    "default_candidates",
    "default_install_file",
    "detect_xplane",
    "global_scenery_dir",
    "is_xplane_dir",
    "other_xplane_dirs",
    "packs_of_their_own",
    "process_names",
    "xplane_candidates",
    "xplane_running",
]

XPLANE_DIR_ENV = "OSXP_XPLANE_DIR"
INSTALL_FILE_NAME = "x-plane_install_12.txt"
GLOBAL_SCENERY_NAME = "X-Plane 12 Global Scenery"

#: Executable base names of the simulator on the three OSes.
XPLANE_EXECUTABLES: frozenset[str] = frozenset({"X-Plane", "X-Plane-x86_64", "X-Plane.exe"})


def _platform() -> str:
    return sys.platform


def default_install_file(env: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    """Where X-Plane 12's installer lists the installations on this OS."""
    env = os.environ if env is None else env
    platform = platform or _platform()
    if platform == "darwin":
        return Path.home() / "Library" / "Preferences" / INSTALL_FILE_NAME
    if platform.startswith("win"):
        local = env.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / INSTALL_FILE_NAME
    return Path.home() / ".x-plane" / INSTALL_FILE_NAME


def default_candidates(
    env: Mapping[str, str] | None = None, platform: str | None = None
) -> list[Path]:
    """Usual X-Plane 12 folders tried when the install file names none that exists."""
    env = os.environ if env is None else env
    platform = platform or _platform()
    home = Path.home()
    if platform == "darwin":
        return [home / "X-Plane 12", Path("/Applications/X-Plane 12")]
    if platform.startswith("win"):
        profile = Path(env.get("USERPROFILE") or home)
        return [
            Path("C:/X-Plane 12"),
            profile / "Desktop" / "X-Plane 12",
            profile / "X-Plane 12",
        ]
    return [home / "X-Plane 12"]


def is_xplane_dir(path: Path) -> bool:
    """``True`` when ``path`` holds both ``Resources/`` and ``Custom Scenery/``."""
    path = Path(path)
    return (path / "Resources").is_dir() and (path / "Custom Scenery").is_dir()


def _install_file_entries(install_file: Path) -> list[Path]:
    try:
        text = Path(install_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[Path] = []
    for raw in text.splitlines():
        line = raw.strip().rstrip("/\\")
        if line:
            out.append(Path(line))
    return out


def xplane_candidates(
    *,
    install_file: Path | None = None,
    candidates: Iterable[Path] | None = None,
    env: Mapping[str, str] | None = None,
) -> list[Path]:
    """The folders ``detect_xplane`` tries, in its order (the doctor lists them when none is one).

    Order: ``$OSXP_XPLANE_DIR``, the installer's list, the usual folders. The keyword arguments
    exist for tests; the plain call reads this machine.
    """
    env = os.environ if env is None else env
    tried: list[Path] = []
    override = env.get(XPLANE_DIR_ENV)
    if override:
        tried.append(Path(override).expanduser())
    install_file = default_install_file(env) if install_file is None else install_file
    tried.extend(_install_file_entries(install_file))
    tried.extend(default_candidates(env) if candidates is None else candidates)
    return tried


def other_xplane_dirs(
    used: Path | None,
    *,
    install_file: Path | None = None,
    candidates: Iterable[Path] | None = None,
    env: Mapping[str, str] | None = None,
) -> list[Path]:
    """The X-Plane 12 folders of this machine, other than ``used``, in the order tried.

    A user installed a tile, saw "Yes" in the Library and found nothing in the Custom Scenery of
    the X-Plane he flies: OrthoStudio XP had taken another X-Plane 12 of his Mac, one he had
    forgotten (2026-09-17). The page says which ones it found, so the choice is his.
    """
    seen: set[Path] = set()
    if used is not None:
        with contextlib.suppress(OSError):
            seen.add(Path(used).resolve())
    out: list[Path] = []
    for candidate in xplane_candidates(install_file=install_file, candidates=candidates, env=env):
        if not is_xplane_dir(candidate):
            continue
        real = candidate
        with contextlib.suppress(OSError):
            real = candidate.resolve()
        if real in seen:
            continue
        seen.add(real)
        out.append(candidate)
    return out


def detect_xplane(
    *,
    install_file: Path | None = None,
    candidates: Iterable[Path] | None = None,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    """The X-Plane 12 folder, or ``None``: the first of ``xplane_candidates`` that is one."""
    tried = xplane_candidates(install_file=install_file, candidates=candidates, env=env)
    for candidate in tried:
        if is_xplane_dir(candidate):
            return candidate
    return None


def global_scenery_dir(xplane: Path) -> Path:
    """``<xp>/Global Scenery/X-Plane 12 Global Scenery`` (path only, not checked)."""
    return Path(xplane) / "Global Scenery" / GLOBAL_SCENERY_NAME


def custom_scenery_dir(xplane: Path) -> Path:
    """``<xp>/Custom Scenery`` (path only, not checked)."""
    return Path(xplane) / "Custom Scenery"


SCENERY_OF_ITS_OWN: tuple[tuple[str, str], ...] = (
    ("simheaven_x-world", "simHeaven X-World"),
    ("simheaven_x-europe", "simHeaven X-Europe"),
    ("simheaven_x-america", "simHeaven X-America"),
    ("simheaven_x-asia", "simHeaven X-Asia"),
    ("simheaven_x-africa", "simHeaven X-Africa"),
    ("simheaven_x-oceania", "simHeaven X-Oceania"),
)
"""Packs that bring their own roads, forests and buildings over the whole world: folder prefix,
lowercased, and the name to show. A user of the X-Plane.Org page had X-World installed and kept
OrthoStudio XP's overlays as well, so everything was drawn twice (2026-09-20); the Settings
question said to choose one, but nothing checked."""


def packs_of_their_own(xplane: Path) -> list[str]:
    """The names of :data:`SCENERY_OF_ITS_OWN` installed in ``<xp>/Custom Scenery``, in order.

    A pack disabled in ``scenery_packs.ini`` does not count: it draws nothing. A folder is read
    once, and an unreadable one answers nothing rather than raising: this is a hint on a settings
    page, never a reason to fail.
    """
    from orthostudio.install.scenery_packs import SceneryPacks

    custom = custom_scenery_dir(xplane)
    try:
        folders = [p.name for p in custom.iterdir() if p.is_dir()]
    except OSError:
        return []
    disabled: set[str] = set()
    ini = custom / "scenery_packs.ini"
    if ini.is_file():
        with contextlib.suppress(OSError, ValueError):
            disabled = {e.name for e in SceneryPacks.load(ini).entries if not e.enabled}
    found: list[str] = []
    for prefix, shown in SCENERY_OF_ITS_OWN:
        if any(f.lower().startswith(prefix) and f not in disabled for f in folders):
            found.append(shown)
    return found


# ----------------------------------------------------------------- running?


def _proc_names() -> set[str] | None:
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    names: set[str] = set()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        with contextlib.suppress(OSError):
            names.add((entry / "comm").read_text(encoding="utf-8", errors="replace").strip())
    return names


def _run(cmd: list[str]) -> str | None:
    try:
        done = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=NO_CONSOLE_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _ps_names() -> set[str] | None:
    out = _run(["ps", "-axo", "comm="])
    if out is None:
        return None
    return {Path(line.strip()).name for line in out.splitlines() if line.strip()}


def _tasklist_names() -> set[str] | None:
    out = _run(["tasklist", "/FO", "CSV", "/NH"])
    if out is None:
        return None
    names: set[str] = set()
    for line in out.splitlines():
        if line.startswith('"'):
            names.add(line.split('","', 1)[0].strip('"'))
    return names


def process_names(platform: str | None = None) -> set[str]:
    """Base names of the executables currently running (empty when they cannot be listed)."""
    platform = platform or _platform()
    if platform.startswith("win"):
        return _tasklist_names() or set()
    if platform.startswith("linux"):
        names = _proc_names()
        if names is not None:
            return names
    return _ps_names() or set()


def xplane_running() -> bool:
    """``True`` when an X-Plane executable is running (``False`` if processes cannot be listed)."""
    return not XPLANE_EXECUTABLES.isdisjoint(process_names())
