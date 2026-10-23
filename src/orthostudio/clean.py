# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""``osxp clean``: give back the disk space the cache holds for no pack.

What is kept: every artefact a pack still on disk was built from -- the packs recorded in the
library and the packs of the default output folder -- through the inputs the store recorded,
plus the DDS the kept ``tile.textures`` artefacts list (a texture artefact is not an input of
anything, so the walk alone would miss them); anything pinned; and anything used in the last
hour, which a build running in another process may be about to record. Everything else goes:
superseded builds (the flat ``+46+006`` of the relief incident, a tile rebuilt with other
settings), builds whose pack was deleted, abandoned temporary directories.

Deleting a tile (``orthostudio.pipeline.pack.delete_receipt``) runs a narrower clean,
:func:`clean_after_delete`: only the cache of the deleted pack goes, with a grace period of ten
minutes instead of the hour, and superseded builds stay for ``osxp clean``.

The imagery caches are emptied only on request: ``chunks`` is what a texture rebuilt with other
zones or masks reads instead of downloading again, ``mapcache`` holds the tiles of the page's base
map (``docs/specs/map-zones.md`` section 6). The map cache is touched only when the caller names
it: no directory is ever guessed from another one.

The cache of the tiles filed outside the atelier (``filed``, the atelier's step 5) goes only on
request too, all of it but their own meshes, which their neighbours' masks read (:data:`MESH_RULE`):
what only those tiles need, and the downloaded image pieces only they use.
The tiles themselves are never touched, nor need it: a pack holds its files under its own names,
copies on another disk, hard links on the atelier's. A tile filed on the atelier's disk keeps its
textures and DSF that way, and their bytes do not come back: the figures count only what does.
Keeping the store's names of those files kept nothing whole: its DSF and textures folders hold
other files too, and a texture no longer listed by a kept folder was collected by the next clean
as data no tile needs (a review, 2026-10-06).

The images tiles hold as files of their own, the store keeping them a second time (a tile moved to
another disk, or copied apart), go whatever the choices, their textures folders with them
(``held_images``): the store remembers which file of the tile holds each, and a build that needs one
takes it back from there, checked to be the very same (``Store.take_back``). Nothing is lost: the
image stays in its tile, and comes back to the store only when a build asks for it.

Sizes are what the disk gets back, not what the index adds up. A DDS is hard-linked into its
``texture.dds`` artefact, into a ``tile.textures`` artefact and into the pack: its bytes are
freed only when the last of those links goes, and :func:`disk_bytes` counts them once (the page's
store size and the size of each pack in its library). What cannot be deleted (a file another
program holds, on Windows) stays, the rest goes, and only what went is counted as freed.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import stat
import time
from collections import Counter
from collections.abc import Collection, Iterable, Iterator
from dataclasses import asdict, dataclass, field, replace
from math import ceil, floor
from pathlib import Path

from orthostudio.graph import ArtifactInfo, Store
from orthostudio.home import disk_absent
from orthostudio.imagery.grid import TextureId, texture_bbox
from orthostudio.install import Library
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import MANIFEST_NAME, read_manifest

__all__ = [
    "DELETE_GRACE_S",
    "GRACE_S",
    "MAPCACHE_DIR",
    "MESH_RULE",
    "CleanReport",
    "clean",
    "clean_after_delete",
    "disk_bytes",
    "freed_bytes",
    "keep_keys",
    "needed_keys",
    "pack_dirs",
    "pack_disk_bytes",
]

log = logging.getLogger("orthostudio.clean")

GRACE_S = 3600.0
"""Artefacts used more recently than this are never collected (a concurrent build)."""

DELETE_GRACE_S = 600.0
"""The grace period of the clean that ends the delete of a tile (:func:`clean_after_delete`).

An artefact touched in the last 10 minutes may be shared with a build running in another process
(a CLI build of a neighbouring tile reusing the same elevation or OSM data) that has not recorded
its use of it yet, so it stays. Ten minutes rather than ``osxp clean``'s hour: a tile deleted
half an hour after its build gives its cache back, as the page's dialog said it would."""

MAPCACHE_DIR = "mapcache"
"""The map cache's directory under ``$OSXP_HOME`` (``orthostudio.api.map_api`` has the same name; it
is not imported from there, fastapi being an optional extra). ``clean`` empties it only when it is
given as ``mapcache_root``."""

MESH_RULE = "orthostudio.mesh"
"""The rule of a tile's mesh (``orthostudio.mesh.rule.OSXP_MESH``, not imported: it brings the
mesh builder). The mesh of a tile filed outside stays when its cache is freed: the masks of its
neighbours read it, and built again without it a neighbour drew its shore along their border as if
that tile did not exist (his choice, 2026-10-06). It goes once its tile is deleted or built again
otherwise, but for a neighbour whose masks were made from it, which keeps it as an input of theirs:
the water it draws is there all the same."""

_CONTAINER = re.compile(r"(\d+)_(\d+)\.chunks")
"""A texture container's name in the imagery cache, ``<til_y>_<til_x>.chunks``
(``docs/specs/imagery-chunks.md`` section 5)."""


@dataclass(slots=True)
class CleanReport:
    """What ``osxp clean`` kept, removed and freed (or would, with ``dry_run``)."""

    dry_run: bool
    packs: list[str] = field(default_factory=list)
    kept: int = 0
    removed: int = 0
    freed_bytes: int = 0
    tmp_removed: int = 0
    images_bytes: int = 0
    """The imagery caches: ``chunks``, and ``mapcache`` when it was given; the image pieces of the
    tiles filed outside the atelier are counted in ``filed_images_bytes`` instead."""
    images_removed: bool = False
    mapcache_bytes: int = 0
    """The map cache's share of ``images_bytes``."""
    relief_bytes: int = 0
    """The elevation cells downloaded and kept (``<data folder>/elevation``). Counted apart from
    the imagery: a square of relief is 40 MB from Copernicus against 400 MB from the USGS, and
    fetching it again costs far less than the imagery, so it is offered as its own choice. It was
    invisible until a user emptied everything and found 1.4 GB left (2026-09-18)."""
    relief_removed: bool = False
    filed_tiles: int = 0
    """The tiles OrthoStudio XP built that are filed outside the atelier with some of their data
    here (the atelier's step 5): their cache is ``filed_bytes`` and ``filed_images_bytes``."""
    filed_bytes: int = 0
    """What deleting the tile data only they need gives back, beyond ``freed_bytes``: all of it
    but their own meshes, which stay (:data:`MESH_RULE`)."""
    filed_images_bytes: int = 0
    """Their downloaded image pieces: the texture containers whose texture touches the square of
    one of them and of no tile of the atelier."""
    filed_removed: bool = False
    held_images: int = 0
    """The images tiles hold as files of their own, the store keeping them a second time: a tile
    moved to another disk, or copied apart. They go whatever the choices, the store remembering
    where each is (:meth:`Store.send_away`), and a build takes one back from its tile when it
    needs it (:meth:`Store.take_back`). A user's tiles moved to another partition left them all
    twice, and the store filled the disk they had left (2026-10-23)."""
    held_bytes: int = 0
    """What letting go of ``held_images`` gives back, beyond ``freed_bytes``: their textures
    folders with them."""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def pack_dirs(library_path: Path | None, tiles_root: Path | None) -> list[Path]:
    """The OrthoStudio XP packs on disk: those the library records, those under ``tiles_root``."""
    found: set[Path] = set()
    if tiles_root is not None and tiles_root.is_dir():
        found.update(d for d in tiles_root.iterdir() if (d / MANIFEST_NAME).is_file())
    if library_path is not None and library_path.is_file():
        with Library(library_path) as lib:
            for entry in lib.list():
                path = Path(entry.path)
                if (path / MANIFEST_NAME).is_file():
                    found.add(path)
    return sorted(found)


@dataclass(frozen=True, slots=True)
class _Built:
    """A pack OrthoStudio XP built, as a clean sees it: its square when known, the artefact keys it
    was made of, and whether it is a tile (a library row of the overlays only lends its keys)."""

    path: str
    tile: TileRef | None
    keys: frozenset[str]
    ortho: bool


def _built(
    packs: Iterable[Path], library_path: Path | None, tiles_root: Path | None
) -> tuple[list[_Built], list[_Built]]:
    """The packs whose cache is kept: the atelier's, right in ``tiles_root`` (links followed;
    every pack when there is none), and those filed outside it.

    A pack on disk gives its ``orthostudio.toml``. A pack the library knows but cannot see right
    now (a disk away, a folder gone) gives the keys the library recorded when it was built: one
    whose ``orthostudio.toml`` could not be read was taken for gone, so everything it was built from
    became collectable: unplug the external disk the tiles live on, press Free space, and the cache
    of every tile on it was given away under the words "data no tile needs any more" (found in
    review, 2026-09-23). A tile imported from Ortho4XP has neither, and needs none: it was not built
    here.
    """
    home = None if tiles_root is None else _folder_id(Path(tiles_root))
    atelier: list[_Built] = []
    away: list[_Built] = []

    def add(path: Path, tile: TileRef | None, keys: Iterable[object], ortho: bool) -> None:
        built = _Built(os.path.realpath(path), tile, frozenset(str(k) for k in keys if k), ortho)
        here = home is None or _folder_id(Path(path).parent) == home
        (atelier if here else away).append(built)

    for pack in packs:
        try:
            manifest = read_manifest(pack)
        except (OSError, ValueError, KeyError):
            continue
        tile: TileRef | None = None
        with contextlib.suppress(ValueError):
            tile = TileRef.parse(manifest.tile)
        add(pack, tile, manifest.keys.values(), True)
    if library_path is not None and library_path.is_file():
        with contextlib.suppress(Exception), Library(library_path) as lib:
            for entry in lib.list():
                if entry.keys and not (Path(entry.path) / MANIFEST_NAME).is_file():
                    add(Path(entry.path), entry.tile, entry.keys.values(), entry.kind == "ortho")
    return atelier, away


def _folder_id(folder: Path) -> tuple[int, int] | str:
    """What tells a folder from another: its disk and inode when it is there, however it is
    spelt (a letter case the Mac's disk ignores, a link); else its path with the links followed (a
    disk away). A tile of the atelier listed under another spelling was taken for one filed
    outside, and its cache freed with theirs (a review, 2026-10-06)."""
    try:
        st = os.stat(folder)
    except OSError:
        return os.path.realpath(folder)
    return (st.st_dev, st.st_ino)


def _roots(built: Iterable[_Built]) -> set[str]:
    return {key for b in built for key in b.keys}


def keep_keys(store: Store, packs: Iterable[Path], library_path: Path | None = None) -> set[str]:
    """Every stored artefact the ``packs`` need, and the packs the library knows but cannot see
    right now (:func:`_built`); see the module docstring."""
    mine, _away = _built(packs, library_path, None)
    return needed_keys(store, _roots(mine))


def needed_keys(store: Store, roots: Iterable[str]) -> set[str]:
    """The stored ``roots`` (the keys of a pack's ``orthostudio.toml``) and every artefact they
    need: what they were built from, through the inputs the store recorded, plus the DDS the
    ``tile.textures`` among them list (a texture artefact is the input of nothing, so the walk alone
    misses it)."""
    keep = store.reachable(roots)
    textures: set[str] = set()
    for key in keep:
        info = store.info(key)
        if info is None or info.rule != "tile.textures":
            continue
        try:
            doc = json.loads((info.path / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        textures.update(str(t["key"]) for t in doc.get("textures", []) if t.get("key"))
    return keep | store.reachable(textures)


def _files(root: Path, *, links: bool = True) -> Iterator[tuple[str, os.stat_result]]:
    """The path and ``lstat`` of every regular file under ``root``, or of ``root`` when it is one.

    Links below ``root`` are neither followed nor counted. What vanishes or cannot be read below
    ``root`` is skipped (``osxp serve`` writes the map cache meanwhile); a ``root`` that exists but
    cannot be listed raises ``OSError``, so that a caller can tell it from an empty folder. One
    ``scandir`` per folder and one ``lstat`` per file: 18 000 files in 70 ms on the reference Mac
    (warm cache). On Windows the listing gives the size but not the hard links, which cost opening
    each file; ``links=False`` does without them, for a folder whose files are never linked.
    """
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        return
    if stat.S_ISREG(st.st_mode):
        yield os.fspath(root), st
        return
    if not os.path.isdir(root):
        return
    folders = [os.fspath(root)]
    first = True
    while folders:
        folder = folders.pop()
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except OSError:
            if first:
                raise
            continue
        first = False
        for entry in entries:
            try:
                if entry.is_junction():  # a Windows link to a folder elsewhere: not entered
                    continue
                if entry.is_dir(follow_symlinks=False):
                    folders.append(entry.path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
                if links and not st.st_nlink:  # Windows' scandir leaves links at 0: ask the file
                    st = os.lstat(entry.path)
            except OSError:
                continue
            yield entry.path, st


def _file_stats(root: Path, *, links: bool = True) -> Iterator[os.stat_result]:
    """The ``lstat`` of every regular file under ``root`` (:func:`_files`)."""
    for _path, st in _files(root, links=links):
        yield st


def disk_bytes(paths: Iterable[Path], *, links: bool = True) -> int:
    """Bytes the files under ``paths`` take on the disk, a file hard-linked several times once.

    Like ``du``, in file sizes rather than blocks. The store's index adds a DDS up once per
    artefact that links it: it said 50 GB for a store ``du`` measured at 24 GB. Raises
    ``OSError`` when a path exists but cannot be listed; a missing one counts 0. ``links=False``
    reads the sizes from the folder listings alone (``_file_stats``), for files never linked.
    """
    seen: set[tuple[int, int]] = set()
    total = 0
    for root in paths:
        for st in _file_stats(Path(root), links=links):
            if st.st_nlink > 1:
                inode = (st.st_dev, st.st_ino)
                if inode in seen:
                    continue
                seen.add(inode)
            total += st.st_size
    return total


def pack_disk_bytes(pack: Path, store_root: Path | None) -> tuple[int, int]:
    """``(bytes, shared)`` of a tile's folder: its files on the disk, each inode once (as
    :func:`disk_bytes`), and those of them that are the store's own files too, one file under the
    tile's name and the store's (a hard link). Raises ``OSError`` when the folder cannot be listed.

    The store's size counted them again: a user's file manager showed the store folder about twice
    the size of the tiles, and the page's own figures added up the same files twice, 91 GB for a
    store that adds 40 GB to 62 GB of tiles (the owner's, 2026-10-23). A tile shares when one of its
    images is the very file the store keeps for it (``tile.textures``, named in its manifest): a
    tile copied to another disk, or one whose cache went, shares nothing, and the tiles of a
    former data folder on the same disk share with that folder's store, not this one. Then each of
    its files on the store's disk under more than one name is the store's too. Its DSF may be a
    copy where its images are not, as a few of the owner's were: each file is asked."""
    seen: set[tuple[int, int]] = set()
    linked: list[tuple[str, os.stat_result]] = []
    total = 0
    for path, st in _files(Path(pack)):
        if st.st_nlink > 1:
            inode = (st.st_dev, st.st_ino)
            if inode in seen:
                continue
            seen.add(inode)
            linked.append((path, st))
        total += st.st_size
    if store_root is None or not linked or not _shares_with(Path(pack), linked, Path(store_root)):
        return total, 0
    device = os.stat(store_root).st_dev
    return total, sum(st.st_size for _path, st in linked if st.st_dev == device)


def _shares_with(pack: Path, linked: list[tuple[str, os.stat_result]], store_root: Path) -> bool:
    """Whether one image of ``pack`` linked elsewhere is the store's own file for it (its
    ``tile.textures`` artefact, laid out as ``Store.artifact_path`` lays it)."""
    try:
        entry = read_manifest(pack).artefacts.get("textures")
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if entry is None:
        return False
    rule = entry.rule.partition("@")[0] or "tile.textures"
    kept = store_root / rule / entry.key[:2] / entry.key / "textures"
    textures = os.path.join(os.fspath(pack), "textures")
    for path, st in linked:
        if os.path.dirname(path) != textures:
            continue
        try:
            there = os.stat(kept / os.path.basename(path))
        except OSError:
            return False
        return (there.st_dev, there.st_ino) == (st.st_dev, st.st_ino)
    return False


def freed_bytes(paths: Iterable[Path]) -> int:
    """Bytes the disk gets back when every file under ``paths`` is deleted.

    A file counts when all its hard links are among them; one still linked from a pack (or
    from a kept artefact) frees nothing, nor does a folder that cannot be read.
    """
    inodes: dict[tuple[int, int], list[int]] = {}
    for root in paths:
        try:
            for st in _file_stats(Path(root)):
                entry = inodes.setdefault((st.st_dev, st.st_ino), [0, st.st_nlink, st.st_size])
                entry[0] += 1
        except OSError:
            continue
    return sum(size for links_here, nlink, size in inodes.values() if links_here >= nlink)


class _Names:
    """The files of some artefacts, by inode: their sizes, their links on the disk, and how many of
    those links each artefact holds. What deleting some of them gives back follows; an artefact that
    cannot be read counts as giving nothing back."""

    def __init__(self, infos: Iterable[ArtifactInfo]) -> None:
        self.size: dict[tuple[int, int], int] = {}
        self.nlink: dict[tuple[int, int], int] = {}
        self.of: dict[str, Counter[tuple[int, int]]] = {}
        for info in infos:
            here: Counter[tuple[int, int]] = Counter()
            try:
                for st in _file_stats(info.path):
                    inode = (st.st_dev, st.st_ino)
                    here[inode] += 1
                    self.size[inode] = st.st_size
                    self.nlink[inode] = st.st_nlink
            except OSError:
                here = Counter()
            self.of[info.key] = here

    def links(self, keys: Iterable[str]) -> Counter[tuple[int, int]]:
        """How many links to each file the artefacts ``keys`` hold, each artefact once."""
        held: Counter[tuple[int, int]] = Counter()
        for key in dict.fromkeys(keys):
            held.update(self.of.get(key, Counter()))
        return held

    def freed(self, keys: Iterable[str]) -> int:
        """Bytes the disk gets back once the artefacts ``keys`` are deleted: a file counts when
        all its links are theirs."""
        held = self.links(keys)
        return sum(self.size[i] for i, n in held.items() if n >= self.nlink[i])


def _own_meshes(store: Store, built: Iterable[_Built]) -> set[str]:
    """The meshes of these tiles themselves (:data:`MESH_RULE`), kept with them."""
    keys = {k for b in built if b.ortho for k in b.keys}
    return {k for k in keys if (info := store.info(k)) is not None and info.rule == MESH_RULE}


def _squares(built: Iterable[_Built]) -> set[tuple[int, int]]:
    return {(b.tile.lat, b.tile.lon) for b in built if b.tile is not None}


def _texture_squares(parts: list[str]) -> set[tuple[int, int]]:
    """The squares ``(lat, lon)`` the texture of a container touches, from its place in the imagery
    cache (``<provider>/<zl>/<til_y>_<til_x>.chunks``); none for any other file (the parent cache,
    a file being written)."""
    if len(parts) != 3 or not parts[1].isdigit():
        return set()
    m = _CONTAINER.fullmatch(parts[2])
    if m is None:
        return set()
    try:
        lat_max, lon_min, lat_min, lon_max = texture_bbox(
            TextureId(int(m[2]), int(m[1]), int(parts[1]), parts[0])
        )
    except ValueError:
        return set()
    return {
        (lat, lon)
        for lat in range(floor(lat_min), ceil(lat_max))
        for lon in range(floor(lon_min), ceil(lon_max))
    }


def _image_pieces(
    chunks: Path, atelier: set[tuple[int, int]], away: set[tuple[int, int]]
) -> tuple[list[tuple[str, int]], int]:
    """The downloaded image pieces only the tiles filed outside the atelier with some of their data
    here use, with their sizes: the texture containers whose texture touches the square of such a
    tile (``away``) and of no tile of the atelier, a texture of a neighbour in the atelier staying
    (at any level of detail: one only a filed neighbour uses, astride the border, counts with the
    images). Then the bytes of all the others. One walk of the cache, its sizes read from the
    listings: its files are never linked."""
    root = os.fspath(chunks)
    pieces: list[tuple[str, int]] = []
    rest = 0
    try:
        for path, st in _files(chunks, links=False):
            squares = _texture_squares(os.path.relpath(path, root).split(os.sep)) if away else set()
            if squares & away and not squares & atelier:
                pieces.append((path, st.st_size))
            else:
                rest += st.st_size
    except OSError:
        return [], 0
    return pieces, rest


def clean(
    store_root: Path,
    chunks_root: Path,
    *,
    library_path: Path | None,
    tiles_root: Path | None,
    images: bool = False,
    dry_run: bool = False,
    grace_s: float = GRACE_S,
    mapcache_root: Path | None = None,
    elevation_root: Path | None = None,
    relief: bool = False,
    filed: bool = False,
) -> CleanReport:
    """Collect what no pack needs; with ``images``, empty the imagery caches as well; with
    ``filed``, the cache of the tiles filed outside the atelier (``tiles_root``), but their own
    meshes, which their neighbours' masks read (:data:`MESH_RULE`).

    The imagery caches are ``chunks_root`` and the map cache ``mapcache_root``; both count in
    ``images_bytes``. Without ``mapcache_root`` (the default) no map cache is counted or
    emptied: a directory beside ``chunks_root`` is never assumed to be one (``osxp clean`` passes
    ``$OSXP_HOME/mapcache``). The image pieces only the tiles filed outside use are theirs, counted
    in ``filed_images_bytes`` and deleted with ``filed``: each choice frees what it counts.

    ``elevation_root`` is counted in ``relief_bytes`` and emptied with ``relief``, its own choice:
    the relief of a square is worth far less to download again than its imagery, and it used to be
    freed by nothing at all.

    Without ``dry_run``, the figures of what was deleted are what went: a file another program
    holds stays, and is not counted.
    """
    report = CleanReport(dry_run=dry_run)
    packs = pack_dirs(library_path, tiles_root)
    report.packs = [str(p) for p in packs]
    atelier, away = _built(packs, library_path, tiles_root)
    theirs: set[str] = set()
    if Path(store_root).is_dir():
        with Store(store_root) as store:
            mine = needed_keys(store, _roots(atelier))
            # their meshes stay; walked from, they kept what they were made of past the free, a
            # file held then staying on no line (a review, 2026-10-06)
            meshes = _own_meshes(store, away)
            needed = needed_keys(store, _roots(away) - meshes) - mine
            theirs = needed - meshes
            unused = _doomed(store, store.iter_artifacts(), mine | needed | meshes, grace_s)
            found = _held(store, packs, grace_s)
            infos = (store.info(key) for key in sorted(theirs))
            cache = _doomed(store, (i for i in infos if i is not None), set(), grace_s)
            held_infos = [i for h in found for i in (h.info, *_stored(h.images))]
            names = _Names([*unused, *held_infos, *cache])
            first = [i.key for i in unused]
            held = _let_go(names, first, found)
            letting = [k for h in held for k in h.keys]
            # the filed tiles' images held twice go with the others, whatever the choice
            cache = [i for i in cache if i.key not in set(letting)]
            report.removed = len(unused)
            report.freed_bytes = names.freed(first)
            report.held_images = sum(len(h.images) - len(h.stays) for h in held)
            report.held_bytes = names.freed([*first, *letting]) - report.freed_bytes
            report.filed_bytes = (
                names.freed([*first, *letting, *(i.key for i in cache)])
                - report.freed_bytes
                - report.held_bytes
            )
            present = sum(1 for k in letting if store.info(k) is not None)
            report.kept = len(store) - len(unused) - present - (len(cache) if filed else 0)
            if not dry_run:
                gone = _delete(store, unused)
                report.removed = len(gone)
                report.freed_bytes = names.freed(gone)
                sent = _send_away(store, held)
                report.held_bytes = names.freed([*gone, *sent]) - report.freed_bytes
                if filed:
                    taken = _delete(store, cache)
                    report.filed_bytes = (
                        names.freed([*gone, *sent, *taken]) - report.freed_bytes - report.held_bytes
                    )
                _tidy_away(store, packs)
                report.kept = len(store)
                report.tmp_removed = len(store.sweep_tmp(max_age_s=grace_s))
    # the tiles filed outside with some of their data here (measured before anything goes): a tile
    # of a data folder chosen before has its cache there, and one freed has none any more. Their
    # image pieces are theirs; a piece of a filed neighbour with nothing here counts with the
    # images: filing one tile counted its four filed neighbours too, whose squares the pieces
    # astride its borders touch (found on the owner's tiles, 2026-10-06)
    cached = [b for b in away if b.ortho and b.keys & theirs]
    report.filed_tiles = len({b.path for b in cached})
    chunks = Path(chunks_root)
    pieces: list[tuple[str, int]] = []
    if chunks.is_dir():
        pieces, report.images_bytes = _image_pieces(chunks, _squares(atelier), _squares(cached))
        report.filed_images_bytes = sum(size for _path, size in pieces)
    mapcache = Path(mapcache_root) if mapcache_root is not None else None
    if mapcache is not None and (not mapcache.is_dir() or _overlap(mapcache, chunks)):
        mapcache = None
    if mapcache is not None:
        report.mapcache_bytes = freed_bytes([mapcache])
        report.images_bytes += report.mapcache_bytes
    if filed and not dry_run:
        report.filed_images_bytes = _delete_files(pieces)
        report.filed_removed = True
    if images and not dry_run and (chunks.is_dir() or mapcache is not None):
        report.images_bytes = report.mapcache_bytes = 0
        if chunks.is_dir():
            # the pieces of the tiles filed outside are their own choice's, gone with it or kept
            report.images_bytes = _empty(chunks, keep=() if filed else {p for p, _ in pieces})
        if mapcache is not None:
            report.mapcache_bytes = _empty(mapcache)
            report.images_bytes += report.mapcache_bytes
        report.images_removed = True
    elevation = Path(elevation_root) if elevation_root is not None else None
    if elevation is not None and elevation.is_dir() and not _overlap(elevation, chunks):
        report.relief_bytes = freed_bytes([elevation])
        if relief and not dry_run:
            report.relief_bytes = _empty(elevation)
            report.relief_removed = True
    return report


def clean_after_delete(
    store_root: Path,
    roots: Iterable[str],
    *,
    library_path: Path | None,
    tiles_root: Path | None,
    grace_s: float = DELETE_GRACE_S,
) -> CleanReport:
    """The store clean that ends the delete of a pack: its own cache, nothing else.

    Candidates are the ``roots`` -- the artefact keys of the deleted pack's ``orthostudio.toml``,
    read before it went -- and what they need (:func:`needed_keys`). A candidate stays when a pack
    still on disk needs it (:func:`keep_keys` of :func:`pack_dirs`), when it is pinned, or when it
    was used in the last ``grace_s``. Everything else of the store stays as well: the superseded
    builds and abandoned temporary directories ``osxp clean`` collects, which a build running in
    another process may still be about to use, and which the deleted tile is not to be credited with
    in ``freed_bytes``.
    """
    report = CleanReport(dry_run=False)
    packs = pack_dirs(library_path, tiles_root)
    report.packs = [str(p) for p in packs]
    roots = set(roots)
    if roots and Path(store_root).is_dir():
        with Store(store_root) as store:
            infos = (store.info(key) for key in sorted(needed_keys(store, roots)))
            candidates = [info for info in infos if info is not None]
            doomed = _doomed(store, candidates, keep_keys(store, packs, library_path), grace_s)
            names = _Names(doomed)
            gone = _delete(store, doomed)
            report.removed = len(gone)
            report.freed_bytes = names.freed(gone)
            _tidy_away(store, packs)  # the deleted tile's own images held nowhere else now
            report.kept = len(store)
    return report


def _doomed(
    store: Store, candidates: Iterable[ArtifactInfo], keep: set[str], grace_s: float
) -> list[ArtifactInfo]:
    """The ``candidates`` no pack needs (``keep``), not pinned, unused for ``grace_s``."""
    cutoff = time.time() - grace_s
    return [
        info
        for info in candidates
        if info.key not in keep
        and max(info.last_used_at, info.created_at) <= cutoff
        and not store.pins(info.key)
    ]


def _delete(store: Store, infos: Iterable[ArtifactInfo]) -> list[str]:
    """Delete the artefacts ``infos`` and say which went. The store moves one aside before removing
    its files, so that none is ever left half there under its name; one whose files cannot all be
    removed (a file another program holds, on Windows) has left the store all the same, but its
    bytes have not come back, and it is not among those that went: the others go on."""
    gone: list[str] = []
    for info in infos:
        try:
            if store.delete(info.key, force=True):
                gone.append(info.key)
        except OSError as exc:
            log.warning("artefact %s not deleted: %s", info.key[:16], exc)
    return gone


@dataclass(frozen=True, slots=True)
class _Copy:
    """A tile's own copy of one of its images: its file, and what the store's is (its digest and
    size, its ``texture.dds`` artefact when the store still has it)."""

    path: Path
    digest: str
    size: int
    info: ArtifactInfo | None
    inode: tuple[int, int]
    """The store's file of it (in the textures folder)."""


@dataclass(frozen=True, slots=True)
class _Held:
    """A tile that holds every image of its textures folder (``info``, a ``tile.textures``
    artefact) as a file of its own: its folder, and its images by their ``texture.dds`` key."""

    info: ArtifactInfo
    pack: Path
    images: dict[str, _Copy]
    stays: frozenset[str] = frozenset()
    """The images that stay in the store, another artefact that stays linking them too
    (:func:`_let_go`): the folder goes without them."""

    @property
    def keys(self) -> list[str]:
        """The artefacts that go: the folder and its images but those that stay."""
        return [self.info.key, *(k for k in self.images if k not in self.stays)]


def _held(store: Store, packs: Iterable[Path], grace_s: float) -> list[_Held]:
    """The textures folders (``tile.textures``) of the tiles that hold every image of theirs as a
    file of their own, with those images by their ``texture.dds`` key: a tile moved to another
    disk, or copied apart. A tile whose images are the store's very files (hard links on the
    store's disk) holds none twice, nor does one missing an image or with one of another size:
    its folder stays, with its images. What was used in the last ``grace_s`` stays too, pinned or
    not, as the rest of the clean keeps it. One folder per tile, reached by any of its names."""
    found: list[_Held] = []
    seen: set[str] = set()
    cutoff = time.time() - grace_s

    def free(info: ArtifactInfo) -> bool:
        return max(info.last_used_at, info.created_at) <= cutoff and not store.pins(info.key)

    for pack in packs:
        real = os.path.realpath(pack)
        if real in seen:
            continue
        seen.add(real)
        try:
            entry = read_manifest(pack).artefacts.get("textures")
        except (OSError, ValueError, KeyError, TypeError):
            continue
        info = store.info(entry.key) if entry is not None else None
        if info is None or info.rule != "tile.textures" or not free(info):
            continue
        images = _own_copies(store, Path(real), info.path)
        if images and all(c.info is None or free(c.info) for c in images.values()):
            found.append(_Held(info, Path(real), images))
    return found


def _own_copies(store: Store, pack: Path, folder: Path) -> dict[str, _Copy] | None:
    """The images the textures folder ``folder`` lists, each as the tile ``pack`` holds it: a
    file of its own of the same size. None when one of them is the store's very file, or missing
    or of another size in the tile."""
    try:
        doc = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    images: dict[str, _Copy] = {}
    for t in doc.get("textures", []):
        name, key, digest = t.get("name"), t.get("key"), t.get("digest")
        if not (name and key and digest):
            return None
        file = f"{name}.dds"
        try:
            kept = os.lstat(folder / "textures" / file)
            there = os.lstat(pack / "textures" / file)
        except OSError:
            return None
        if (
            not stat.S_ISREG(there.st_mode)
            or (there.st_dev, there.st_ino) == (kept.st_dev, kept.st_ino)
            or there.st_size != kept.st_size
        ):
            return None
        info = store.info(str(key))
        if info is not None and info.rule != "texture.dds":
            return None
        images[str(key)] = _Copy(
            pack / "textures" / file, str(digest), kept.st_size, info, (kept.st_dev, kept.st_ino)
        )
    return images


def _stored(images: dict[str, _Copy]) -> list[ArtifactInfo]:
    return [c.info for c in images.values() if c.info is not None]


def _let_go(names: _Names, first: list[str], held: list[_Held]) -> list[_Held]:
    """The ``held`` folders, each with the images whose bytes do not come back once it goes, after
    the artefacts ``first``, as those that stay in the store: a texture astride the border with a
    neighbour, which the neighbour's folder lists too, or one a tile on the store's disk shares.
    The folder goes all the same, its tile holding every image, and the rest comes back: one image
    a neighbour shared kept a whole tile in the store, 18 of a user's 309 (2026-10-23). A folder
    none of whose images would come back stays, with them."""
    links = names.links([*first, *(k for h in held for k in h.keys)])
    out: list[_Held] = []
    for h in held:
        stays = frozenset(
            k for k, c in h.images.items() if links.get(c.inode, 0) < names.nlink.get(c.inode, 0)
        )
        if len(stays) < len(h.images):
            out.append(replace(h, stays=stays))
    return out


def _send_away(store: Store, held: list[_Held]) -> list[str]:
    """Let go of each held image, the store remembering the tile's file that holds it and the
    folder that listed it, then of that folder, remembered with its tile (what the Plan reads to
    count the images a build takes back); say which artefacts went. An image that cannot go
    (another program holds it, on Windows) stays, and so does its folder."""
    gone: list[str] = []
    for h in held:
        whole = True
        for key, copy in h.images.items():
            if key in h.stays:  # its folder's list names it, the artefact staying
                store.note_away(
                    key,
                    rule="texture.dds",
                    digest=copy.digest,
                    size=copy.size,
                    path=copy.path,
                    listed_in=h.info.key,
                )
                continue
            try:
                if store.send_away(
                    key,
                    rule="texture.dds",
                    digest=copy.digest,
                    size=copy.size,
                    path=copy.path,
                    listed_in=h.info.key,
                ):
                    gone.append(key)
            except OSError as exc:
                log.warning("image %s not let go of: %s", key[:16], exc)
                whole = False
        if not whole:
            continue
        info = h.info
        try:
            if store.send_away(
                info.key, rule=info.rule, digest=info.digest, size=info.size, path=h.pack
            ):
                gone.append(info.key)
        except OSError as exc:
            log.warning("textures folder %s not let go of: %s", info.key[:16], exc)
    return gone


def _tidy_away(store: Store, packs: Iterable[Path]) -> None:
    """The store's list of images held by tiles, kept true: an image back in the store leaves it;
    one whose tile's file is gone from a disk that is here is looked for in the tile's folders as
    the Library knows them now (moved with *Find again* or *File elsewhere*), and forgotten when
    none holds it; so is a textures folder, by its tile's folder. A disk unplugged keeps its rows
    for when it is back."""
    where: dict[str, list[Path]] = {}
    for pack in packs:
        where.setdefault(pack.name, []).append(Path(os.path.realpath(pack)))
    forget: list[str] = []
    for row in store.away_rows():
        if store.has(row.key):
            # back in the store; one its folder still away lists stays on that folder's list
            folder_away = row.listed_in is not None and not store.has(row.listed_in)
            if not folder_away or store.away(row.listed_in or "") is None:
                forget.append(row.key)
            continue
        if row.path.exists() or disk_absent(row.path):
            continue
        if row.rule == "tile.textures":  # its tile's folder
            moved = [p for p in where.get(row.path.name, []) if p.is_dir()]
        else:
            moved = [
                p / "textures" / row.path.name
                for p in where.get(row.path.parent.parent.name, [])
                if (p / "textures" / row.path.name).is_file()
            ]
        if moved:
            store.point_away(row.key, moved[0])
        else:
            forget.append(row.key)
    if forget:
        store.forget_away(forget)


def _delete_files(files: Iterable[tuple[str, int]]) -> int:
    """Delete the files ``(path, size)`` and say how many bytes went: one gone meanwhile, or held
    by another program, is not counted."""
    freed = 0
    for path, size in files:
        try:
            os.unlink(path)
        except OSError:
            continue
        freed += size
    return freed


def _overlap(a: Path, b: Path) -> bool:
    """``a`` and ``b`` are one directory, or one holds the other: count and empty it once."""
    ra, rb = a.resolve(), b.resolve()
    return ra == rb or ra.is_relative_to(rb) or rb.is_relative_to(ra)


def _empty(directory: Path, keep: Collection[str] = ()) -> int:
    """Delete what ``directory`` holds but the files ``keep`` names (as a walk of ``directory``
    spells them), the folders left empty with it, not ``directory``; say how many bytes went.

    A file gone meanwhile is not an error; one that cannot be deleted (another program holds it,
    on Windows) stays, its folder with it, and is not counted. Sizes come from the listings: what
    is emptied here is never linked."""
    kept = set(keep)
    freed = 0

    def empty(folder: str) -> bool:
        """Empty ``folder``; whether nothing is left in it."""
        nonlocal freed
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except OSError:
            return False
        left = False
        for entry in entries:
            try:
                if entry.is_junction():
                    # a Windows link to a folder elsewhere: what it leads to is not ours to empty,
                    # and it is left as it is (shutil.rmtree, used before, refused it too)
                    left = True
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if empty(entry.path):
                        os.rmdir(entry.path)
                    else:
                        left = True
                    continue
                if entry.path in kept:
                    left = True
                    continue
                st = entry.stat(follow_symlinks=False)
                os.unlink(entry.path)
            except FileNotFoundError:
                continue
            except OSError:
                left = True
                continue
            if stat.S_ISREG(st.st_mode):  # a link takes no room of its own
                freed += st.st_size
        return not left

    empty(os.fspath(directory))
    return freed
