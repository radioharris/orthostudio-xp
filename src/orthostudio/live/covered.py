# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The squares X-Plane takes from another pack than Live's: the user's own ortho tiles.

For each 1-degree square, X-Plane draws the ground of the first pack of its list
(``scenery_packs.ini``, top first) holding a DSF of that square that is not an overlay (a DSF with
the property ``sim/overlay 1`` only adds roads, forests, buildings or an airport over the ground).
Live lists its line under the tiles OrthoStudio XP built or imported from Ortho4XP, unless the
pilot chose "Live everywhere" or moved it (:func:`orthostudio.live.scenery.place_line`). Where a
pack listed above it gives the ground, X-Plane never reads Live's textures: Live downloads nothing
there, and a start there waits for none of them.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from orthostudio.install.dsf_head import dsf_properties
from orthostudio.install.scenery_packs import SceneryPacks

from .geo import dsf_tile_of
from .scenery import _ini, place_line


def is_overlay(path: Path) -> bool:
    return dsf_properties(path).get("sim/overlay") == "1"


def is_ground(path: Path) -> bool:
    """A DSF read, and not an overlay: the ground of its square. One that cannot be read is not
    taken for ground: Live then builds there, where the other way X-Plane could show green."""
    properties = dsf_properties(path)
    return bool(properties) and properties.get("sim/overlay") != "1"


def _folder(xplane: Path, written: str) -> Path:
    path = Path(written.replace("\\", "/"))
    return path if path.is_absolute() else xplane / path


def covered_squares(
    xplane: Path,
    below: str,
    region: Collection[tuple[int, int]] | None = None,
    packs: SceneryPacks | None = None,
) -> dict[tuple[int, int], str]:
    """``{(lat, lon): pack}``: the squares of ``region`` (Live's pack's; all when None) whose
    ground X-Plane takes from an enabled pack listed above ``below`` (Live's link), in ``packs``
    (X-Plane's list as it is on the disk when None).

    A pack is ground or overlays as one of its DSFs there says, read up to its properties: packs
    are one or the other, and simHeaven X-World or Global Forests hold thousands of zipped DSFs,
    which a first version read every one of, for minutes. OrthoStudio XP's tiles and those
    imported from Ortho4XP are ground without reading."""
    wanted = None if region is None else set(region)
    out: dict[tuple[int, int], str] = {}
    listed = packs if packs is not None else SceneryPacks.load(_ini(xplane))
    for entry in listed.entries:  # the top first
        if entry.name == below:
            break
        if not entry.enabled or entry.is_global_airports:
            continue
        nav = _folder(xplane, entry.path) / "Earth nav data"
        if not nav.is_dir():
            continue
        squares: dict[tuple[int, int], Path] = {}
        for dsf in nav.glob("*/*.dsf"):
            square = dsf_tile_of(str(dsf))
            if square is not None and (wanted is None or square in wanted):
                squares.setdefault(square, dsf)
        if not squares:
            continue
        if not entry.is_ortho and not is_ground(squares[min(squares)]):
            continue  # roads, forests, buildings, an airport: over the ground only
        for square in squares:
            out.setdefault(square, entry.name)
    return out


def own_squares(xplane: Path, below: str, own_tiles: str = "first") -> dict[tuple[int, int], str]:
    """The squares X-Plane takes from the user's own scenery, as :func:`covered_squares` finds
    them, with Live's line where it is listed, else where Live's next start lists it
    (:func:`orthostudio.live.scenery.place_line`, in memory only: the list on the disk stays as it
    is). The Live window shows them while Live is stopped too: what its next flight shows."""
    packs = SceneryPacks.load(_ini(xplane))
    place_line(packs, own_tiles)
    return covered_squares(xplane, below, packs=packs)
