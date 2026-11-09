# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""X-Plane's side for OrthoStudio XP Live: the mesh it streams onto, and its pack in Custom Scenery.

The mesh is AutoOrtho's (``z_ao_eur``: DSFs, ``.ter`` files, water masks), read only: the regions
AutoOrtho installed, and those Live installed itself the same way
(:mod:`orthostudio.live.aoregions`).
The pack, a link in Custom Scenery, is listed in ``scenery_packs.ini`` by OrthoStudio XP's own rules
(:class:`orthostudio.install.scenery_packs.SceneryPacks`): below OrthoStudio XP's built tiles, which
draw over it, and above AutoOrtho and the base meshes. Its overlays' link goes above the ortho.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from collections.abc import Collection
from pathlib import Path

from orthostudio.config.store import load_settings
from orthostudio.errors import OsxpError
from orthostudio.fsutil import NO_CONSOLE_WINDOW
from orthostudio.install import chosen_xplane, is_xplane_dir
from orthostudio.install.scenery_packs import PackKind, SceneryPacks
from orthostudio.install.xplane import XPLANE_EXECUTABLES, custom_scenery_dir
from orthostudio.install.xplane import xplane_running as _xplane_running
from orthostudio.live.filesystem import folder_link, is_folder_link, remove_folder_link


def find_xplane(explicit: Path | None = None) -> Path:
    """The X-Plane 12 folder: ``explicit``, else the one Build uses (``chosen_xplane``)."""
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not is_xplane_dir(path) and not (path / "Custom Scenery").is_dir():
            raise OsxpError("XP_DIR_NOT_FOUND", context={"path": str(path)})
        return path
    named = load_settings().essential.xplane_dir
    found = chosen_xplane(named)
    if found is None:
        raise OsxpError("XP_DIR_NOT_FOUND", context={"path": named or "(detected)"})
    return found


def autoortho_scenery(xplane: Path) -> Path:
    """Where AutoOrtho installs its regions, a ``z_ao_<region>`` folder each."""
    return xplane / "Custom Scenery" / "z_autoortho" / "scenery"


def autoortho_regions(xplane: Path) -> list[Path]:
    """AutoOrtho's regions installed by AutoOrtho (``z_ao_eur``, ``z_ao_na``...): those with a
    ``terrain`` folder."""
    try:
        folders = list(autoortho_scenery(xplane).iterdir())
    except OSError:
        return []
    return sorted(p for p in folders if p.name.startswith("z_ao_") and (p / "terrain").is_dir())


def osxp_regions(root: Path) -> list[Path]:
    """Live's own mesh, region by region (``regions/z_osxp_eur``), installed in Live's folder: those
    with a ``terrain`` folder (or the lists naming its files, ``names.TERRAIN_LISTS``) and the
    marker written once whole."""
    from orthostudio.live import aoregions

    try:
        folders = list(aoregions.regions_dir(root).iterdir())
    except OSError:
        return []
    return sorted(
        p
        for p in folders
        if p.name.startswith("z_osxp_")
        and ((p / "terrain").is_dir() or (p / "terrain-lists").is_dir())
        and (p / aoregions.MARKER).is_file()
    )


def default_sources(xplane: Path, root: Path | None = None) -> list[Path]:
    """The regions whose mesh Live streams onto: Live's own mesh first, so that a square it has is
    its (``region.setup_region``: a DSF a region has already stays that region's), then AutoOrtho's
    own, then those Live installed (in ``root``, Live's folder) that AutoOrtho has not."""
    from orthostudio.live import aoregions
    from orthostudio.live.pack import live_root

    root = live_root() if root is None else root
    ours = osxp_regions(root)
    theirs = autoortho_regions(xplane)
    have = {p.name for p in theirs}
    mine = [
        aoregions.mesh_dir(root, region)
        for region in sorted(aoregions.installed(root))
        if f"z_ao_{region}" not in have
    ]
    return ours + theirs + mine


def xplane_pref(xplane: Path, key: str) -> str | None:
    """A value of X-Plane's own preferences (``Output/preferences/X-Plane.prf``), or None."""
    prf = xplane / "Output" / "preferences" / "X-Plane.prf"
    if prf.exists():
        for line in prf.read_text(errors="replace").splitlines():
            name, _, value = line.partition(" ")
            if name == key:
                return value.strip()
    return None


def _ini(xplane: Path) -> Path:
    return custom_scenery_dir(xplane) / "scenery_packs.ini"


def install_link(
    xplane: Path,
    pack_dir: Path,
    link_name: str,
    log=print,
    kind: PackKind = "ortho",
    above: str | None = None,
    again: bool = False,
) -> None:
    """Link ``pack_dir`` into Custom Scenery as ``link_name`` and list it where OrthoStudio XP lists
    a pack of that ``kind`` (its rules and backups: ``scenery_packs.ini.bak`` and
    ``.osxp-previous``), or right above ``above``'s line ("Live everywhere": above the pilot's own
    tiles, :func:`live_above`). A line already there stays where it is, unless ``again``: then it
    goes where it would go new."""
    link = custom_scenery_dir(xplane) / link_name
    if is_folder_link(link):
        remove_folder_link(link)
    elif link.exists():
        raise OsxpError(
            "XP_LINK_FAILED",
            context={
                "link": str(link),
                "reason": "a folder of that name is there, not OrthoStudio XP Live's link",
            },
        )
    folder_link(pack_dir.resolve(), link)  # a junction on Windows without Developer Mode
    packs = SceneryPacks.load(_ini(xplane))
    before = packs.to_bytes()
    if again:
        packs.remove(link_name)
    packs.ensure(link_name, kind=kind, above=above)
    if packs.to_bytes() != before:
        packs.save(_ini(xplane))
    log(f"installed: {link} -> {pack_dir.resolve()}, listed in scenery_packs.ini")


def live_above(xplane: Path, link_name: str, packs_named: Collection[str]) -> str | None:
    """The first of ``packs_named`` X-Plane's list has above ``link_name``: where "Live everywhere"
    lists Live's pack, so that X-Plane takes Live's ground over the pilot's own tiles while Live
    runs (it takes the first pack listed that has a square). None when none is above."""
    wanted = set(packs_named)
    for entry in SceneryPacks.load(_ini(xplane)).entries:
        if entry.name == link_name:
            return None
        if entry.name in wanted:
            return entry.name
    return None


def uninstall_link(xplane: Path, link_name: str, log=print) -> None:
    """Take ``link_name`` out of scenery_packs.ini and remove the link. The pack itself stays."""
    packs = SceneryPacks.load(_ini(xplane))
    if packs.remove(link_name):
        packs.save(_ini(xplane))
    link = custom_scenery_dir(xplane) / link_name
    if is_folder_link(link):
        remove_folder_link(link)
    log(f"uninstalled: {link_name} is out of X-Plane")


def is_installed(xplane: Path, link_name: str) -> bool:
    entry = SceneryPacks.load(_ini(xplane)).find(link_name)
    return (
        is_folder_link(custom_scenery_dir(xplane) / link_name)
        and entry is not None
        and entry.enabled
    )


def autoortho_mounted(xplane: Path) -> bool:
    scenery = custom_scenery_dir(xplane)
    return any(p.name.startswith("z_ao_") and os.path.ismount(p) for p in scenery.iterdir())


ORTHO_STREAMERS = (("XPME", "xplanemapenh"), ("AutoOrtho", "autoortho"))
"""The other programs that stream ortho to X-Plane, by how their processes' names start, in lower
case and without spaces: XPME's window is « XPlane Map Enhancement » and its server
« XplaneMapEnhancement » (its version 4.7.22 on a Mac, 2026-10-08), AutoOrtho's program
« autoortho »; Linux keeps 15 characters of a name, Windows adds « .exe »."""


def ortho_streamers(xplane: Path) -> list[str]:
    """The other programs streaming ortho to X-Plane that run now, by name: from the system's list
    of processes (:func:`_processes`), and from the folders they mount, which say it where that
    list cannot be read. Only one may serve X-Plane its ortho: Live refuses to start beside one."""
    names = {_plain(name) for name, _ in (_processes() or {}).values()}
    running = {label for label, start in ORTHO_STREAMERS if any(n.startswith(start) for n in names)}
    if autoortho_mounted(xplane):
        running.add("AutoOrtho")
    if xpme_mounted(xplane):
        running.add("XPME")
    return [label for label, _ in ORTHO_STREAMERS if label in running]


def _plain(name: str) -> str:
    return name.lower().replace(" ", "").removesuffix(".exe")


def xpme_mounted(xplane: Path) -> bool:
    """Whether XPME serves its regions now: its packs in Custom Scenery (``XPME_Europe``...) are
    links into the folder it mounts while it runs (``~/XPME-fuse`` on a Mac), empty otherwise."""
    try:
        packs = [p for p in custom_scenery_dir(xplane).iterdir() if p.name.startswith("XPME_")]
    except OSError:
        return False
    for pack in packs:
        if pack.is_symlink() and os.path.ismount(os.path.dirname(os.path.realpath(pack))):
            return True
    return False


def xplane_running() -> bool:
    """Whether X-Plane runs, from the system's own list of processes (:func:`_processes`), never
    from a command (``ps``) run from Live's process: that command may die before it runs on a Mac
    (orthostudio.live.pools), and an X-Plane taken for gone had the pack taken out from under it
    (2026-09-28). OrthoStudio XP's answer where that list cannot be read."""
    procs = _processes()
    if procs is None:
        return _xplane_running()
    return any(_is_xplane(name) for name, _ in procs.values())


def _is_xplane(name: str) -> bool:
    """Whether a process's name is X-Plane's: ``X-Plane`` (a Mac), ``X-Plane.exe`` (Windows, in
    any case), ``X-Plane-x86_64`` (Linux)."""
    return name in XPLANE_EXECUTABLES or (
        sys.platform == "win32" and name.lower() in {n.lower() for n in XPLANE_EXECUTABLES}
    )


def xplane_pid() -> int | None:
    """X-Plane's process, from the system's own list (:func:`_processes`); None where X-Plane does
    not run or that list cannot be read."""
    found = [pid for pid, (name, _) in (_processes() or {}).items() if _is_xplane(name)]
    return min(found) if found else None


class _BsdInfo(ctypes.Structure):
    """``struct proc_bsdinfo`` (``<sys/proc_info.h>``): a process's name and start."""

    _fields_ = [
        *((f, ctypes.c_uint32) for f in ("flags", "status", "xstatus", "pid", "ppid", "uid")),
        *((f, ctypes.c_uint32) for f in ("gid", "ruid", "rgid", "svuid", "svgid", "rfu_1")),
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        *((f, ctypes.c_uint32) for f in ("nfiles", "pgid", "pjobc", "e_tdev", "e_tpgid")),
        ("nice", ctypes.c_int32),
        ("start_tvsec", ctypes.c_uint64),
        ("start_tvusec", ctypes.c_uint64),
    ]


PROC_PIDTBSDINFO = 3


def _processes() -> dict[int, tuple[str, float]] | None:
    """Each process running: its name and when it started, Unix time, from the system's own list:
    macOS's libproc, Linux's ``/proc``, Windows' NtQuerySystemInformation. None where it cannot
    say. Nothing is started to know it."""
    if sys.platform == "darwin":
        return _darwin_processes()
    if sys.platform == "win32":
        from .machine import windows_process_starts

        try:
            return windows_process_starts()
        except (OSError, AttributeError, ValueError):
            return None
    if sys.platform.startswith("linux"):
        return _linux_processes()
    return None


def _darwin_processes() -> dict[int, tuple[str, float]] | None:
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        count = libproc.proc_listallpids(None, 0)
        if count <= 0:
            return None
        pids = (ctypes.c_int * (count + 64))()  # room for those started meanwhile
        count = libproc.proc_listallpids(pids, ctypes.sizeof(pids))
        if count <= 0:
            return None
        info = _BsdInfo()
        size = ctypes.sizeof(info)
        out = {}
        for pid in pids[:count]:
            if (
                pid > 0
                and libproc.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, ctypes.byref(info), size) == size
            ):
                name = (info.name or info.comm).decode(errors="replace")
                out[pid] = (name, info.start_tvsec + info.start_tvusec / 1e6)
    except (OSError, AttributeError):
        return None
    return out


def _linux_processes(proc: Path = Path("/proc")) -> dict[int, tuple[str, float]] | None:
    """From ``/proc``: each process's name (15 characters at most, X-Plane-x86_64 whole) and its
    start, in clock ticks after the boot. A process that has quit but that its parent has not yet
    looked at (a zombie, ``Z``) is not running: X-Plane started by Live is looked at by a thread of
    Live's (:func:`_spawn`), one started by another program may wait."""
    try:
        boot = next(
            float(line.split()[1])
            for line in (proc / "stat").read_text().splitlines()
            if line.startswith("btime ")
        )
        tick = os.sysconf("SC_CLK_TCK")
        entries = [e for e in proc.iterdir() if e.name.isdigit()]
    except (OSError, StopIteration, ValueError, IndexError):
        return None
    out = {}
    for entry in entries:
        try:
            stat = (entry / "stat").read_text()
            name = stat[stat.index("(") + 1 : stat.rindex(")")]
            fields = stat[stat.rindex(")") + 2 :].split()  # from its state, the 3rd field, on
            if fields[0] in ("Z", "X"):
                continue
            out[int(entry.name)] = (name, boot + int(fields[19]) / tick)  # the 22nd: its start
        except (OSError, ValueError, IndexError):
            continue  # gone meanwhile
    return out


def _spawn(cmd: list[str], cwd: Path | None = None) -> subprocess.Popen:
    """Start ``cmd`` (a full path) apart from Live, which may stop while it runs:
    - on a Mac, with posix_spawn, which Python uses when no descriptor is to be closed (Live's are
      not inherited anyway): Live's process forks nothing (orthostudio.live.pools);
    - on Linux, in a session of its own: Ctrl-C in Live's terminal never reaches it;
    - on Windows, in a process group of its own, out of Live's job where the job allows it.
    A thread of Live's waits for it to end: a process that has quit stays listed until its parent
    has looked (:func:`_linux_processes`)."""
    windows = sys.platform == "win32"
    flags = NO_CONSOLE_WINDOW
    if windows:
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB  # type: ignore[attr-defined]

    def start(creationflags: int) -> subprocess.Popen:
        return subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=sys.platform != "darwin",
            start_new_session=sys.platform.startswith("linux"),
            creationflags=creationflags,
        )

    try:
        child = start(flags)
    except PermissionError:
        if not windows:
            raise
        # a job that keeps its processes (some terminals, some launchers): X-Plane in it then
        child = start(flags & ~subprocess.CREATE_BREAKAWAY_FROM_JOB)  # type: ignore[attr-defined]
    threading.Thread(target=child.wait, name="live-started", daemon=True).start()
    return child


XPLANE_PROGRAMS = {"darwin": "X-Plane.app", "win32": "X-Plane.exe", "linux": "X-Plane-x86_64"}


def launch_xplane(xplane: Path) -> None:
    """Start X-Plane as a double click on it does (in the Finder, the Explorer, a file manager);
    returns at once."""
    platform = "linux" if sys.platform.startswith("linux") else sys.platform
    program = xplane / XPLANE_PROGRAMS.get(platform, "X-Plane-x86_64")
    if platform == "darwin":
        if not program.is_dir():
            raise FileNotFoundError(f"no {program.name} in {xplane}")
        _spawn(["/usr/bin/open", str(program)])
        return
    if not program.is_file():
        raise FileNotFoundError(f"no {program.name} in {xplane}")
    _spawn([str(program)], cwd=xplane)  # from its folder, as a double click starts it


def xplane_started_at() -> float | None:
    """When the running X-Plane started (Unix time), or None; from the system's list of
    processes, as :func:`xplane_running`."""
    procs = _processes() or {}
    started = [at for name, at in procs.values() if _is_xplane(name)]
    return min(started) if started else None
