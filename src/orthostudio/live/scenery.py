# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""X-Plane's side for OrthoStudio XP Live: the mesh it streams onto, and its pack in Custom Scenery.

The mesh is AutoOrtho's (``z_ao_eur``: DSFs, ``.ter`` files, water masks), read only: the regions
AutoOrtho installed, and those Live installed itself the same way
(:mod:`orthostudio.live.aoregions`).
The pack, a link in Custom Scenery, is listed in ``scenery_packs.ini`` where the pilot puts it;
where it is missing, right above the first mesh, below OrthoStudio XP's tiles, which draw over it
(above them with "Live everywhere"): :func:`place_line`. Its overlays' line goes right above it.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from orthostudio.config.store import load_settings, save_settings
from orthostudio.errors import OsxpError
from orthostudio.fsutil import NO_CONSOLE_WINDOW
from orthostudio.install import chosen_xplane, is_xplane_dir
from orthostudio.install.scenery_packs import PackKind, SceneryPackEntry, SceneryPacks
from orthostudio.install.xplane import XPLANE_EXECUTABLES, custom_scenery_dir
from orthostudio.install.xplane import xplane_running as _xplane_running
from orthostudio.live.filesystem import folder_link, is_folder_link, remove_folder_link
from orthostudio.live.pack import LINK_NAME, OVERLAY_LINK_NAME


def named_xplane() -> str | None:
    """The X-Plane folder named in OrthoStudio XP's Settings, one setting for Build and Live;
    ``None``: the one detected."""
    return load_settings().essential.xplane_dir


def save_named_xplane(folder: str | None) -> None:
    settings = load_settings()
    essential = settings.essential.model_copy(update={"xplane_dir": folder})
    save_settings(settings.model_copy(update={"essential": essential}))


def find_xplane(explicit: Path | None = None) -> Path:
    """The X-Plane 12 folder: ``explicit``, else the one Build uses (``chosen_xplane``)."""
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not is_xplane_dir(path) and not (path / "Custom Scenery").is_dir():
            raise OsxpError("XP_DIR_NOT_FOUND", context={"path": str(path)})
        return path
    named = named_xplane()
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


@dataclass(frozen=True)
class OtherMesh:
    """What the folder of another tool's mesh holds (:func:`other_mesh`)."""

    meshes: list[Path]
    """Its regions Live streams onto: ground whose terrain files name photos to make."""
    overlays: list[Path]
    """Their roads, forests and buildings: the one overlay pack that goes with them."""
    refused: list[tuple[str, str]]
    """Its packs of ground Live leaves aside, and why."""


NO_OTHER_MESH = OtherMesh([], [], [])

PACK_DEPTH = 3  # AutoOrtho's own, moved: <folder>/z_autoortho/scenery/z_ao_<region>


def other_mesh(folder: Path | None, given: Sequence[Path] = ()) -> OtherMesh:
    """The meshes and the roads of the folder the pilot chose (``LiveSettings.other_mesh``), by
    what its packs (``Earth nav data``, in it or down to :data:`PACK_DEPTH` folders below) hold,
    whatever their names, as X-Plane tells ground from overlays (``sim/overlay 1``):
    - a pack of ground is a mesh when its terrain files name photos Live makes, without them
      (:func:`_why_not_streamed`), and it gives a square none of ``given`` (the regions before it)
      nor a mesh kept before it has (``already``: a copy of one);
    - of the overlay packs X-Plane's own (:data:`XPLANE_OVERLAYS`), the one with most squares of
      the folder's ground naming photos, copies included, is the roads, forests and buildings of
      its meshes (:func:`_roads_of`): an airport, a Landmark, simHeaven or Global Forests lying
      there are not taken (the reviews of 2026-11-14).
    X-Plane's folder or Live's chosen give nothing: their ground has no terrain files, names no
    photos, has its photos (a tile, Live's own pack) or is already there. Nothing when the folder
    is gone (its disk away)."""
    from orthostudio.install.dsf_head import dsf_properties
    from orthostudio.live.overlays import squares_of

    if folder is None:
        return NO_OTHER_MESH
    try:
        packs = _packs_in(folder, PACK_DEPTH)
    except OSError:
        return NO_OTHER_MESH
    meshes: list[Path] = []
    refused: list[tuple[str, str]] = []
    overlays: list[tuple[Path, set]] = []
    named: set = set()  # the squares of its ground naming photos, copies included
    have: set | None = None  # the squares before it, listed once a mesh is found
    for pack in packs:
        dsfs = squares_of(pack)
        if not dsfs:  # no DSF: no scenery (X-Plane's ATC data, an emptied pack)
            continue
        squares = set(dsfs)
        head = dsf_properties(dsfs[min(dsfs)])
        if head.get("sim/overlay") == "1":
            if head.get("sim/creation_agent", "").startswith(XPLANE_OVERLAYS):
                overlays.append((pack, squares))
        elif (why := _why_not_streamed(pack)) is not None:
            refused.append((pack.name, why))
        else:
            if have is None:
                have = set().union(*(squares_of(source) for source in given))
            named |= squares
            if squares <= have:  # the same region as one before it: it would add nothing
                refused.append((pack.name, "already"))
            else:
                meshes.append(pack)
                have |= squares
    return OtherMesh(meshes, _roads_of(named, overlays) if meshes else [], refused)


def _packs_in(folder: Path, depth: int) -> list[Path]:
    """The packs (``Earth nav data``) in ``folder``, it or below it ``depth`` levels at most, by
    name; a pack is not looked into, a folder that cannot be read below the first is left out."""
    if (folder / "Earth nav data").is_dir():
        return [folder]
    if depth == 0:
        return []
    out: list[Path] = []
    for sub in sorted(folder.iterdir()):
        if sub.is_dir() and not sub.name.startswith("."):
            with contextlib.suppress(OSError):
                out += _packs_in(sub, depth - 1)
    return out


XPLANE_OVERLAYS = "X-Plane Scenery Creator"
"""The tool X-Plane's own scenery is made with (``sim/creation_agent``): the overlays of its
Global Scenery, AutoOrtho's (« X-Plane Scenery Creator 0.9a ») and Live's alike, the roads,
forests and buildings a mesh replacing X-Plane's goes without. An airport or a Landmark
(WorldEditor), simHeaven (Scenery Designer Pro) and Global Forests (none) are made otherwise:
taken by their squares alone, Global Forests passed for the roads of a mesh moved without them
(the review of 3ac4a84b, 2026-11-14)."""


def _roads_of(ground: set, overlays: Sequence[tuple[Path, set]]) -> list[Path]:
    """Of X-Plane's own overlay packs, the one with most squares of ``ground`` (the folder's ground
    naming photos, copies left aside included: scored against the meshes kept alone, the one pack
    of every region lost to a copy and a region had no roads, the fourth review of 2026-11-14);
    none with no square of it."""
    best: list[Path] = []
    most = 0
    for pack, squares in overlays:
        common = len(squares & ground)
        if common > most:
            best, most = [pack], common
    return best


TER_SAMPLE = 20  # a mesh's terrain files looked at: one naming a photo Live makes is enough


def _why_not_streamed(pack: Path) -> str | None:
    """Why Live cannot stream onto a pack of ground, or None: ``photos``, a tile with its photos
    (the photo its first terrain file names is in its ``textures``: a tile of the pilot's, Live's
    own pack); ``no_photos``, terrain files naming no photo in Ortho4XP's names, which Live makes
    (HD Mesh's land classes); ``no_terrain``, no terrain file at all (X-Plane's own scenery,
    Live's regions, a virtual disk's empty mount point); ``unreadable``."""
    from orthostudio.live.geo import _TEXTURE_RE

    if not (pack / "terrain").is_dir():
        return "no_terrain"  # X-Plane's own scenery, Live's regions (their terrain-lists)
    seen = 0
    try:
        with os.scandir(pack / "terrain") as entries:
            for entry in entries:
                if not entry.name.endswith(".ter"):
                    continue
                seen += 1
                with open(entry.path, "rb") as f:
                    for line in f.read(8192).splitlines():
                        if line.startswith((b"BASE_TEX ", b"BASE_TEX_NOWRAP ")):
                            name = line.split()[-1].decode("utf-8", "replace")
                            name = name.replace("\\", "/").rsplit("/", 1)[-1]
                            if name.endswith(".dds") and _TEXTURE_RE.match(name):
                                return "photos" if (pack / "textures" / name).is_file() else None
                if seen >= TER_SAMPLE:
                    break
    except OSError:
        return "unreadable"
    # none at all: a virtual disk's mount point, empty while its streamer is not running
    return "no_photos" if seen else "no_terrain"


def other_mesh_folder() -> Path | None:
    """The folder of another tool's mesh the pilot chose in Live's Settings, if any."""
    from orthostudio.live.settings import load_settings

    folder = load_settings().other_mesh.strip()
    if not folder:
        return None
    try:
        return Path(folder).expanduser()
    except RuntimeError:  # « ~someone », no such user: as typed, not there (the fifth review)
        return Path(folder)


def other_mesh_there(folder: Path) -> bool:
    """Whether the folder chosen is there: a full path to a folder (a relative one would depend on
    where Live was started)."""
    return folder.is_absolute() and folder.is_dir()


OTHER_MESH_S = 30.0
"""The folder of another tool's mesh looked at again this often by the window's checks, which
come every 3 s (it was read twice each time, the review of 2026-11-14); at once when the setting
changes or the folder comes or goes, and at Live's start."""

_found: tuple[float, tuple, OtherMesh] = (0.0, (), NO_OTHER_MESH)


def other_mesh_found(xplane: Path | None, root: Path, *, fresh: bool = False) -> OtherMesh:
    """The folder chosen in the Settings and what Live takes there after the regions before it
    (:func:`base_sources`), as last looked at, or now when ``fresh`` (a start)."""
    global _found
    folder = other_mesh_folder()
    # its disk plugged in again: looked at at once, where the window said « no mesh » 30 s more
    key = (folder, folder is not None and other_mesh_there(folder), xplane, root)
    at, was, got = _found
    if not fresh and was == key and time.monotonic() - at < OTHER_MESH_S:
        return got
    there = key[1]  # the regions before it listed only for a folder to look into
    given = base_sources(xplane, root) if there and xplane is not None else []
    got = other_mesh(folder, given) if there else NO_OTHER_MESH
    _found = (time.monotonic(), key, got)
    return got


def base_sources(xplane: Path, root: Path) -> list[Path]:
    """Live's own mesh first, so that a square it has is its (``region.setup_region``: a DSF a
    region has already stays that region's), then AutoOrtho's own, then those Live installed (in
    ``root``, Live's folder) that AutoOrtho has not."""
    from orthostudio.live import aoregions

    theirs = autoortho_regions(xplane)
    have = {p.name for p in theirs}
    mine = [
        aoregions.mesh_dir(root, region)
        for region in sorted(aoregions.installed(root))
        if f"z_ao_{region}" not in have
    ]
    return osxp_regions(root) + theirs + mine


def default_sources(
    xplane: Path, root: Path | None = None, other: OtherMesh | None = None
) -> list[Path]:
    """The regions whose mesh Live streams onto: :func:`base_sources`, then those of the folder of
    another tool's mesh chosen in the Settings (``other``, as found at a start; else as last
    looked at, :func:`other_mesh_found`)."""
    from orthostudio.live.pack import live_root

    root = live_root() if root is None else root
    other = other_mesh_found(xplane, root) if other is None else other
    return base_sources(xplane, root) + other.meshes


def xplane_pref(xplane: Path, key: str) -> str | None:
    """A value of X-Plane's own preferences (``Output/preferences/X-Plane.prf``), or None: also when
    the file cannot be read (its rights, or Windows' lock while X-Plane writes it), so that the
    window's checks, which ask it, go on."""
    try:
        text = (xplane / "Output" / "preferences" / "X-Plane.prf").read_text(errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        if name == key:
            return value.strip()
    return None


def xplane_texture_quality(xplane: Path) -> int | None:
    """X-Plane's texture quality as it applies it, 0 to 4 (4: Maximum, 3: High), from its
    preferences: ``renopt_tex_res_04``, which X-Plane 12 reads ("Setting |Texture Resolution read
    from prefs"), else the older ``_tex_res`` (1 to 5); None when neither is there. For the window
    only: Live builds the zoom chosen whatever it is (the user, 2026-11-13)."""
    for key, shift in (("renopt_tex_res_04", 0), ("_tex_res", -1)):
        value = xplane_pref(xplane, key)
        if value and value.isdigit():
            return max(0, min(4, int(value) + shift))
    return None


def quality_said(quality: int | None) -> str:
    """X-Plane's texture quality as LogLive.txt says it, for a pilot who sends it: its name in
    X-Plane's menu where known, and its value 0 to 4."""
    if quality is None:
        return "not found in X-Plane's preferences"
    return f"{ {4: 'Maximum', 3: 'High'}.get(quality, 'below High') } ({quality} of 4)"


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
    ``.osxp-previous``), or right above ``above``'s line (Live's overlays: right above Live's). A
    line already there stays where it is, unless ``again``: then it goes where it would go new."""
    link = _link(xplane, pack_dir, link_name)
    packs = SceneryPacks.load(_ini(xplane))
    before = packs.to_bytes()
    if again:
        packs.remove(link_name)
    packs.ensure(link_name, kind=kind, above=above)
    if packs.to_bytes() != before:
        packs.save(_ini(xplane))
    log(f"installed: {link} -> {pack_dir.resolve()}, listed in scenery_packs.ini")


def _link(xplane: Path, pack_dir: Path, link_name: str) -> Path:
    """``pack_dir`` linked into Custom Scenery as ``link_name``, made again."""
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
    return link


# Live's line in X-Plane's list. One rule (the user, 2026-11-13): Live lists its line itself only
# where it is missing, or once when the setting own_tiles changes; wherever it is listed, by the
# pilot or a sorting tool such as xOrganizer, it stays; Live checks it as it starts and unchecks it
# as it stops, so that the list itself keeps that place. Until 2026-11-13 Live took its line out at
# every stop and listed it again by its rule at every start: moved by hand, it came back.


def live_anchor(packs: SceneryPacks, own_tiles: str) -> SceneryPackEntry | None:
    """The line Live's goes right above when Live lists it itself: the first mesh, below the
    tiles, the other photo packs and everything that draws over the ground; with "Live
    everywhere", the first of the pilot's tiles (OrthoStudio XP's, Ortho4XP's) if it comes first
    (one listed under the mesh is hidden by it anyway, and Live with it); None: the end."""
    lines = [e for e in packs.entries if e.name not in (LINK_NAME, OVERLAY_LINK_NAME)]
    start = next((i + 1 for i, e in enumerate(lines) if e.is_global_airports), 0)
    enabled = [e for e in lines[start:] if e.enabled]
    return next(
        (e for e in enabled if (own_tiles == "under" and e.is_ortho) or packs.is_mesh(e)), None
    )


def place_line(packs: SceneryPacks, own_tiles: str, move: bool = False) -> SceneryPackEntry:
    """Live's line in ``packs``: where it is listed, never moved; else, or with ``move`` (the
    setting own_tiles changed), right above :func:`live_anchor`. A new line is checked."""
    entry = packs.find(LINK_NAME)
    if entry is not None and not move:
        return entry
    if entry is None:
        entry = SceneryPackEntry(f"Custom Scenery/{LINK_NAME}/", enabled=True)
    else:
        packs.body = [x for x in packs.body if x is not entry]
    anchor = live_anchor(packs, own_tiles)
    packs.body.insert(
        next((i for i, x in enumerate(packs.body) if x is anchor), len(packs.body)), entry
    )
    return entry


def list_live(xplane: Path, pack_dir: Path, own_tiles: str, log=print) -> None:
    """At a start: Live's pack linked into Custom Scenery, its line checked where it is listed, or
    listed by :func:`place_line`."""
    link = _link(xplane, pack_dir, LINK_NAME)
    packs = SceneryPacks.load(_ini(xplane))
    before = packs.to_bytes()
    place_line(packs, own_tiles).enabled = True
    if packs.to_bytes() != before:
        packs.save(_ini(xplane))
    log(f"installed: {link} -> {pack_dir.resolve()}, listed in scenery_packs.ini")


def move_live(xplane: Path, own_tiles: str) -> bool:
    """The setting own_tiles changed: Live's line, when listed, moved once right above its new
    anchor (:func:`live_anchor`). True when it moved."""
    packs = SceneryPacks.load(_ini(xplane))
    before = packs.to_bytes()
    if packs.find(LINK_NAME) is not None:
        place_line(packs, own_tiles, move=True)
    if packs.to_bytes() == before:
        return False
    packs.save(_ini(xplane))
    return True


def unlist_live(xplane: Path, log=print) -> None:
    """Live out of X-Plane (a stop): its lines unchecked where they are, their links kept, X-Plane
    dropping the line of a folder it cannot reach. The pack itself stays."""
    packs = SceneryPacks.load(_ini(xplane))
    if [name for name in (LINK_NAME, OVERLAY_LINK_NAME) if packs.disable(name)]:
        packs.save(_ini(xplane))
    log("uninstalled: Live's scenery is unchecked in X-Plane's list, where it is")


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
    not run or that list cannot be read. The newest of two, as :func:`xplane_started_at`."""
    found = [(at, pid) for pid, (name, at) in (_processes() or {}).items() if _is_xplane(name)]
    return max(found)[1] if found else None


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
    processes, as :func:`xplane_running`. The newest of two: one quitting may still be listed as
    the next one starts (the review of 2026-11-11)."""
    procs = _processes() or {}
    started = [at for name, at in procs.values() if _is_xplane(name)]
    return max(started) if started else None
