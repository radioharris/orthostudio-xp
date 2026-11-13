# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The roads, forests and buildings of Live's squares: X-Plane's own, as overlays.

X-Plane draws one base mesh per one-degree square. Where Live's pack gives the ground, X-Plane's own
scenery for that square is not read at all, and its roads, forests, buildings and beaches go with
it. Live's own overlays bring them back (``regions/y_osxp_overlays``: cut out of X-Plane 12's
scenery for every square of OrthoStudio XP's mesh), and AutoOrtho's
where a pilot has them and Live's lack a square (``yAutoOrtho_Overlays``, taken from X-Plane's own
scenery by Ortho4XP). Live puts the overlay
of each of its squares in a pack of its own, clones or hard links (a link, or a copy on Windows,
from another disk), brought up to date at each start: only the squares whose overlay changed are
placed or taken away, nothing on a start like the one before (all 16,621 made again took 2.2 s of
every start, the user's Mac, 2026-10-31). The pack is Live's folder's ``overlays``, linked
as ``y_osxp_live_overlays`` and listed right above Live's own pack, below every other overlay pack,
at each start: X-Plane reads no overlay listed under the pack that gives a square its ground.

A square gets no overlay from Live where something else already draws its roads and buildings:

- the pilot's own tiles give the ground: they have their own overlays;
- another overlay pack enabled has that square: AutoOrtho's own ``yAutoOrtho_Overlays``, Ortho4XP's,
  OrthoStudio XP's. They carry no exclusion zone, and the same roads would be drawn twice;
- simHeaven X-World or its kin has that square
  (:data:`orthostudio.install.xplane.SCENERY_OF_ITS_OWN`). Its squares carry exclusion zones over
  the whole square (``sim/exclude_net`` in its roads pack, ``sim/exclude_obj`` and
  ``sim/exclude_fac`` in its scenery pack, read in a user's packs on 27 September). They would hide
  AutoOrtho's roads and buildings, but X-Plane still loads a pack whose content is hidden: its
  manual asks to disable what is not needed. It is looked for square by square: simHeaven X-World
  Europe gives nothing over North Africa, where Live's overlays go.

Global Forests needs nothing of the kind: its ``sim/exclude_for`` over the whole square hides the
forests of every pack below it, Live's included, and leaves their roads and buildings.

The setting ``overlays`` set to "off" leaves them out altogether.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import stat
from collections.abc import Callable, Collection, Sequence
from pathlib import Path

from orthostudio.install.scenery_packs import SceneryPacks
from orthostudio.install.xplane import SCENERY_OF_ITS_OWN, custom_scenery_dir

from . import aoregions
from .filesystem import place_file
from .geo import dsf_rel_path, dsf_tile_of
from .pack import LINK_NAME, OVERLAY_LINK_NAME
from .scenery import _ini

OSXP_OVERLAYS = "y_osxp_overlays"  # Live's own, in its regions folder
AUTOORTHO_OVERLAYS = "yAutoOrtho_Overlays"  # where AutoOrtho's installer puts every region's
OVERLAY_PACK = re.compile(r"^y.*overlays", re.IGNORECASE)  # Ortho4XP's, AutoOrtho's, OSXP's...
Square = tuple[int, int]
Log = Callable[[str], None]


def squares_of(folder: Path) -> dict[Square, Path]:
    """The DSFs of a pack's ``Earth nav data``, by square."""
    out: dict[Square, Path] = {}
    with contextlib.suppress(OSError):
        for tens in os.scandir(folder / "Earth nav data"):
            if not tens.is_dir():
                continue
            for dsf in os.scandir(tens.path):
                square = dsf_tile_of(dsf.path) if dsf.name.endswith(".dsf") else None
                if square is not None:
                    out[square] = Path(dsf.path)
    return out


def overlay_folders(xplane: Path, sources: Sequence[Path], root: Path) -> list[Path]:
    """Where the overlays of Live's squares are, the first that has a square giving it: Live's own
    first (``regions/y_osxp_overlays``, X-Plane's roads, forests and buildings of every square of
    OrthoStudio XP's mesh), then AutoOrtho's where a pilot has
    them: Live's install of a region keeps them beside its mesh (``regions/y_ao_<id>``), in
    whichever Live folder (its marker: a cold test flight's Live streams onto the region of the
    user's, tools/live/flights/fly.py); AutoOrtho's installer puts every region's in one pack,
    ``yAutoOrtho_Overlays``, used here whether it is enabled or not."""
    out = [aoregions.regions_dir(root) / OSXP_OVERLAYS]
    for source in sources:
        region = source.name.removeprefix("z_ao_")
        if source == aoregions.mesh_dir(root, region) or (source / aoregions.MARKER).is_file():
            out.append(source.with_name(f"y_ao_{region}"))
    out.append(custom_scenery_dir(xplane) / AUTOORTHO_OVERLAYS)
    return [f for f in out if (f / "Earth nav data").is_dir()]


def other_overlays(xplane: Path) -> dict[Square, str]:
    """The squares whose roads and buildings another enabled pack draws, {square: what it is}: an
    overlay pack (``yAutoOrtho_Overlays``, ``yOrtho4XP_Overlays``, ``yOrthoStudio_Overlays``...), or
    simHeaven X-World and its kin, listed above Live's line: X-Plane reads no overlay listed under
    the ground it shows (2026-10-26), and a pack listed there first (Build's first overlays with
    "Live everywhere") left Live's squares bare (the review of 2026-11-13)."""
    own = dict(SCENERY_OF_ITS_OWN)
    out: dict[Square, str] = {}
    with contextlib.suppress(OSError, ValueError):
        for entry in SceneryPacks.load(_ini(xplane)).entries:
            if entry.name == LINK_NAME:
                break
            if not entry.enabled or entry.name == OVERLAY_LINK_NAME:
                continue
            kin = next((own[p] for p in own if entry.name.lower().startswith(p)), None)
            if kin is None and not OVERLAY_PACK.match(entry.name):
                continue
            folder = Path(entry.path.replace("\\", "/"))
            folder = folder if folder.is_absolute() else xplane / folder
            for square in squares_of(folder):
                out.setdefault(square, kin or entry.name)
    return out


def _place(src: Path, dst: Path) -> None:
    """A clone or a hard link on the same disk, free; AutoOrtho's on another disk read from there
    through a link, copied on Windows, where a file's link needs Developer Mode: the copy dated as
    its source, as a clone is, for :func:`_same`."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if place_file(src, dst, symlink=True) == "copied":
        s = os.stat(src)
        os.utime(dst, ns=(s.st_atime_ns, s.st_mtime_ns))


def _same(src: Path, dst: Path) -> bool:
    """Whether ``dst`` is already what :func:`_place` makes of ``src``: a link to it, or a clone,
    hard link or copy of its size and date (a region's overlays made again come newer)."""
    try:
        d = os.lstat(dst)
        if stat.S_ISLNK(d.st_mode):
            return os.readlink(dst) == str(src)
        s = os.stat(src)
    except OSError:
        return False
    return (d.st_size, d.st_mtime_ns) == (s.st_size, s.st_mtime_ns)


def _prune(enav: Path, wanted: dict[Path, Path]) -> tuple[set[Path], int]:
    """Every file of the pack's ``enav`` that is not already the overlay ``wanted`` there
    ({place: its source}) taken away, a folder left empty too. Returns the places kept, and how
    many went."""
    kept: set[Path] = set()
    gone = 0
    with contextlib.suppress(FileNotFoundError):
        for tens in os.scandir(enav):
            if not tens.is_dir(follow_symlinks=False):
                os.unlink(tens.path)
                gone += 1
                continue
            left = False
            for entry in os.scandir(tens.path):
                place = Path(entry.path)
                src = wanted.get(place)
                if src is not None and _same(src, place):
                    kept.add(place)
                    left = True
                elif entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path)
                    gone += 1
                else:
                    os.unlink(entry.path)
                    gone += 1
            if not left:
                os.rmdir(tens.path)
    return kept, gone


def build(
    xplane: Path,
    pack_dir: Path,
    overlay_dir: Path,
    sources: Sequence[Path],
    root: Path,
    covered: Collection[Square],
    setting: str = "auto",
    log: Log = print,
) -> int:
    """Live's overlay pack brought up to date for the squares of ``pack_dir``, only the squares
    that changed placed or taken away: returns how many squares have their roads, forests and
    buildings from it."""
    enav = overlay_dir / "Earth nav data"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    if setting == "off":
        _prune(enav, {})
        log("roads, forests and buildings: none from Live (switched off in its settings)")
        return 0
    ground = set(squares_of(pack_dir)) - set(covered)
    drawn = other_overlays(xplane)
    available: dict[Square, tuple[Path, bool]] = {}  # square: its overlay, whether Live's own
    for folder in overlay_folders(xplane, sources, root):
        own = folder.name == OSXP_OVERLAYS
        for square, dsf in squares_of(folder).items():
            available.setdefault(square, (dsf, own))
    wanted: dict[Path, Path] = {}  # place in the pack: its overlay
    ours = 0
    for square in sorted(ground - set(drawn)):
        found = available.get(square)
        if found is not None:
            wanted[overlay_dir / dsf_rel_path(*square)] = found[0]
            ours += found[1]
    kept, gone = _prune(enav, wanted)
    for dst, src in wanted.items():
        if dst not in kept:
            _place(src, dst)
    placed = len(wanted)
    elsewhere = len(ground & set(drawn))
    whose = (
        "X-Plane's"
        if ours == placed
        else f"X-Plane's on {ours}, AutoOrtho's on {placed - ours}"
        if ours
        else "AutoOrtho's"
    )
    log(
        f"roads, forests and buildings: {whose}, on {placed} of Live's {len(ground)} squares"
        + (
            f", {elsewhere} drawn by {sorted({drawn[s] for s in ground & set(drawn)})[0]}"
            if elsewhere
            else ""
        )
        + f" ({placed - len(kept)} placed and {gone} taken away since the last start)"
    )
    return placed
