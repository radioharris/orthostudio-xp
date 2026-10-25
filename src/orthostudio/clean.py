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

A tile on another disk or partition than the store holds its images as files of its own, which
X-Plane reads, and the store keeps them a second time. They go only on request (``apart``): the
store's copy of their images, and their textures folders. Nothing is kept of where the tile's
copies are, nor taken back from them: a tile belongs to its user, who may move it, retouch it,
unplug its disk or delete it. A build of such a tile prepares its images again from the downloaded
image pieces, the same bytes; with the pieces deleted too (the ``images`` choice, then or later), it
downloads them again, which the provider may have changed. A user's tiles moved to another partition
left 358 GB of them twice, on the disk the move was to free (2026-10-23).

A tile of the atelier on the store's disk holding a texture or its DSF as a copy of the store's own
(brought back from another disk, by hand or filed back) has it made one file with the store's,
whatever the choices, once the two are read the same (:func:`_doubles`): the one change a clean
makes to a tile, which keeps the same bytes under the same name.

The relief and the mesh of each tile are kept in two forms, OrthoStudio XP's and Ortho4XP's, which
versions before this one read: 90 GB and 272 GB of the same user's 1.1 TB cache. With ``compact``
the cache keeps the first alone, from then on (``Store.compact``): a version before this one turns
it away at its opening, and going back to one is emptying the cache folder.

Sizes are what the disk gets back, not what the index adds up. A DDS is hard-linked into its
``texture.dds`` artefact, into a ``tile.textures`` artefact and into the pack: its bytes are
freed only when the last of those links goes, and :func:`disk_bytes` counts them once (the page's
store size and the size of each pack in its library). What cannot be deleted (a file another
program holds, on Windows) stays, the rest goes, and only what went is counted as freed.
"""

from __future__ import annotations

import contextlib
import filecmp
import json
import logging
import os
import re
import stat
import time
import zipfile
from collections import Counter
from collections.abc import Collection, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from math import ceil, floor
from pathlib import Path
from typing import IO, Any

import numpy as np

from orthostudio.graph import ArtifactInfo, Store
from orthostudio.imagery.grid import TextureId, texture_bbox
from orthostudio.install import Library
from orthostudio.model import TileRef
from orthostudio.pipeline.filing import same_disk
from orthostudio.pipeline.pack import MANIFEST_NAME, join_same_file, read_manifest

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
    atelier_tiles: int = 0
    """The tiles of the atelier (its ``tiles`` folder, links followed), whose data the page's first
    line keeps: those filed outside have a box of their own (``filed_tiles``), and counted in that
    line too they read as counted twice (a review, 2026-10-24)."""
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
    apart_tiles: int = 0
    """The tiles on another disk or partition than the store whose images the store keeps a
    second time (:func:`_apart`): the tile holds its own copies, which X-Plane reads."""
    apart_bytes: int = 0
    """What deleting the store's copy of their images gives back, beyond ``freed_bytes``, with
    ``apart``: their ``texture.dds`` artefacts and textures folders, but an image a tile on the
    store's disk needs too. A build of one of them prepares its images again from the downloaded
    image pieces, or downloads them again when those were deleted."""
    apart_removed: bool = False
    filed_apart_bytes: int = 0
    """``filed_bytes`` once the store's copy of the images of the tiles on another disk went
    (``apart``): a tile filed on another disk has its images in both."""
    twice_files: int = 0
    """The files of the relief and the mesh the store keeps a second time, in another form:
    Ortho4XP's ``Data<tile>.alt`` beside ``dem.npy``, its ``Data<tile>.mesh`` beside ``mesh.npz``,
    which versions before this one read. They are trimmed in a compacted cache, their artefacts
    keeping their identity (``Store.trim``): with ``compact``, or whatever the choices once it is
    (left by a compaction that was interrupted)."""
    twice_bytes: int = 0
    """What trimming ``twice_files`` gives back."""
    twice_filed_bytes: int = 0
    """The share of the second forms that the cache of the tiles filed outside holds: in
    ``twice_bytes`` without ``filed``, left out of it with ``filed``, since their data goes whole
    then. Measured with ``filed`` (the page's measure), the figure of compacting alone is
    ``twice_bytes + twice_filed_bytes``: it left them out, though compacting alone trims them too
    (a review, 2026-10-24)."""
    compacted: bool = False
    """Whether the cache keeps the relief and the mesh in one form (``Store.compacted``), once this
    clean is done; with ``dry_run``, as it is."""
    compacted_now: bool = False
    """Whether this clean compacted it: false for a cache compacted already, which the command line
    said it compacted (a campaign, 2026-10-24)."""
    doubles_files: int = 0
    """The textures and DSF of the atelier's tiles that are copies of the store's own on the same
    disk, made one file with them whatever the choices (:func:`_doubles`); with ``dry_run``, those
    of the same size, their bytes compared only when they are joined."""
    doubles_bytes: int = 0
    """What making ``doubles_files`` one file gives back."""
    doubles_packs: list[str] = field(default_factory=list)
    """The tiles holding those copies (with ``dry_run``), or whose files were made one file with
    the store's: what the Library remembers of their sizes is out of date (a tile moved away by
    hand and back keeps its files' dates, and was still counted as sharing them, 2026-10-25)."""
    doubles_since: dict[str, float] = field(default_factory=dict)
    """For each of ``doubles_packs`` (with ``dry_run``), when the newest of its copies came: what
    the Library measured of the tile before that is out of date, and a tile measured since is not
    measured again at every Disk space (19.6 s with 300 tiles on a Windows PC, 2026-10-25)."""

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
    return _Needs(store)(roots)


class _Needs:
    """:func:`needed_keys` for several sets of roots over one read of the store's index, each
    textures list read once: a Disk space measure asks for three sets (the atelier's tiles, those
    filed outside, those on the store's disk), and each read the whole index and the lists again
    (2026-10-25)."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._reach = store.reach()
        self._listed: dict[str, set[str]] = {}

    def __call__(self, roots: Iterable[str]) -> set[str]:
        keep = self._reach(roots)
        textures: set[str] = set()
        for key in keep:
            if key not in self._listed:
                self._listed[key] = _textures_listed(self._store, key)
            textures |= self._listed[key]
        return keep | self._reach(textures)


def _textures_listed(store: Store, key: str) -> set[str]:
    """The DDS a ``tile.textures`` artefact lists; nothing for any other artefact."""
    info = store.info(key)
    if info is None or info.rule != "tile.textures":
        return set()
    try:
        doc = json.loads((info.path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(t["key"]) for t in doc.get("textures", []) if t.get("key")}


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
    apart: bool = False,
    compact: bool = False,
) -> CleanReport:
    """Collect what no pack needs; with ``images``, empty the imagery caches as well; with
    ``filed``, the cache of the tiles filed outside the atelier (``tiles_root``), but their own
    meshes, which their neighbours' masks read (:data:`MESH_RULE`); with ``apart``, the store's
    copy of the images of the tiles on another disk or partition (:func:`_apart`); with
    ``compact``, Ortho4XP's form of the relief and the mesh, the cache keeping OrthoStudio XP's
    alone from then on (``Store.compact``).

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
    report.atelier_tiles = len({b.path for b in atelier if b.ortho})
    theirs: set[str] = set()
    if Path(store_root).is_dir():
        # a measure leaves the abandoned build folders to the next build or Free space: looking
        # for them lists every folder of the store, 1 s of Disk space's 4.8 with 301 tiles on
        # Windows (2026-10-25); Free space sweeps them at its end
        with Store(store_root, sweep=not dry_run) as store:
            needs = _Needs(store)
            mine = needs(_roots(atelier))
            # their meshes stay; walked from, they kept what they were made of past the free, a
            # file held then staying on no line (a review, 2026-10-06)
            meshes = _own_meshes(store, away)
            needed = needs(_roots(away) - meshes) - mine
            theirs = needed - meshes
            keep = mine | needed | meshes
            unused = _doomed(store, store.iter_artifacts(skip=keep), keep, grace_s)
            infos = (store.info(key) for key in sorted(theirs))
            cache = _doomed(store, (i for i in infos if i is not None), set(), grace_s)
            tiles = _apart(store, packs, store_root)
            built = {b.path: b for b in (*atelier, *away)}
            # an image a tile on the store's disk needs stays: one astride its border, say
            others = needs(_roots(b for path, b in built.items() if path not in tiles))
            candidates = (store.info(k) for k in sorted({k for ks in tiles.values() for k in ks}))
            images_apart = _doomed(store, (i for i in candidates if i is not None), others, grace_s)
            leaving = {i.key for i in images_apart}
            report.apart_tiles = sum(1 for keys in tiles.values() if set(keys) & leaving)
            names = _Names([*unused, *images_apart, *cache])
            first = [i.key for i in unused]
            sent = [i.key for i in images_apart]
            taken = [i.key for i in cache]
            report.removed = len(unused)
            report.freed_bytes = names.freed(first)
            report.apart_bytes = names.freed([*first, *sent]) - report.freed_bytes
            report.filed_bytes = names.freed([*first, *taken]) - report.freed_bytes
            report.filed_apart_bytes = (
                names.freed([*first, *sent, *taken]) - report.freed_bytes - report.apart_bytes
            )
            going_keys = {*first, *(sent if apart else ()), *(taken if filed else ())}
            report.kept = len(store) - len(going_keys)
            report.compacted = store.compacted()
            twice = _twice(store, {*first, *(sent if apart else ())}, grace_s)
            filed_keys = set(taken)
            report.twice_filed_bytes = sum(
                size for info, files in twice if info.key in filed_keys for size in files.values()
            )
            if filed:  # they go whole with the cache of the tiles filed outside
                twice = [(info, files) for info, files in twice if info.key not in filed_keys]
            report.twice_files = sum(len(files) for _info, files in twice)
            report.twice_bytes = sum(size for _info, files in twice for size in files.values())
            doubles = _doubles(store, atelier)
            report.doubles_files = len(doubles)
            report.doubles_bytes = sum(size for _pack, _mine, _theirs, size in doubles)
            report.doubles_packs = sorted({str(pack) for pack, _mine, _theirs, _size in doubles})
            report.doubles_since = _came(doubles)
            if not dry_run:
                went = _delete(store, unused)
                report.removed = len(went)
                report.freed_bytes = names.freed(went)
                if apart:
                    let = _delete(store, images_apart)
                    report.apart_bytes = names.freed([*went, *let]) - report.freed_bytes
                    report.apart_removed = True
                    went = [*went, *let]
                if filed:
                    off = _delete(store, [i for i in cache if i.key not in set(went)])
                    report.filed_bytes = names.freed([*went, *off]) - names.freed(went)
                    report.filed_apart_bytes = report.filed_bytes
                if compact and not report.compacted:
                    store.compact()  # on the disk before anything is trimmed
                    report.compacted = report.compacted_now = True
                if report.compacted:
                    # each artefact read whole first, and trimmed only as it was built (``trim``)
                    report.twice_files = report.twice_bytes = 0
                    for info, files in twice:
                        before = len(store.trimmed(info.key))
                        report.twice_bytes += store.trim(info.key, files)
                        report.twice_files += len(store.trimmed(info.key)) - before
                report.doubles_files, report.doubles_bytes, report.doubles_packs = _join(
                    store, doubles
                )
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
            report.kept = len(store)
    return report


def _apart(store: Store, packs: Iterable[Path], store_root: Path) -> dict[str, list[str]]:
    """The packs on another disk or partition than the store, by their folder with the links
    followed: what their textures folder's file system says (``st_dev``, the volume on Windows),
    as filing tells a disk from another. Each with the keys of the store's copy of its images:
    its textures folder (``tile.textures``) and the ``texture.dds`` that folder lists; a pack
    whose folder the store no longer has is not among them. Such a tile holds its images as files
    of its own: no file can have a name on two file systems. A pack whose folder cannot be read
    now (a disk away, a letter another disk took) is none of them, and its cache stays."""
    if not Path(store_root).is_dir():
        return {}
    found: dict[str, list[str]] = {}
    for pack in packs:
        real = os.path.realpath(pack)
        folder = Path(real) / "textures"
        if real in found or not folder.is_dir() or same_disk(store_root, folder):
            continue
        try:
            entry = read_manifest(pack).artefacts.get("textures")
        except (OSError, ValueError, KeyError, TypeError):
            continue
        info = store.info(entry.key) if entry is not None else None
        if info is None or info.rule != "tile.textures" or not store.has(info.key):
            continue
        try:
            doc = json.loads((info.path / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        found[real] = [info.key, *(str(t["key"]) for t in doc.get("textures", []) if t.get("key"))]
    return found


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


def _doubles(store: Store, built: Iterable[_Built]) -> list[tuple[Path, Path, Path, int]]:
    """The textures and the DSF of the atelier's tiles that are copies of the store's own, with
    their tile, the store's file and their size: on the store's disk, the same size, not one file
    with it, and no other name of their own, so that making them one file gives their bytes back.
    Read from their sizes alone, which is what Disk space measures; :func:`_join` compares the
    bytes first. A tile of the atelier holds them as links to the store's; a tile brought back
    from another disk, by hand or filed back, holds copies, and so did a pack kept as it was when
    an image made again after an update came out the same: 11 GB of a user's 16 tiles, which no
    choice freed, and a user who moved 552 GB of tiles back to his workshop's partition would
    have kept 358 GB of them twice (2026-10-25). The tiles filed elsewhere and those on another
    disk have their own choices, which delete the store's copy."""
    try:
        dev = os.stat(store.root).st_dev
    except OSError:
        return []
    different = _different(store)
    found: list[tuple[Path, Path, Path, int]] = []
    for b in built:
        if not b.ortho or b.tile is None:
            continue
        pack = Path(b.path)
        try:
            keys = read_manifest(pack).keys
        except (OSError, ValueError, KeyError):
            continue
        pairs: list[tuple[Path, Path]] = []
        textures = store.info(keys["textures"]) if keys.get("textures") else None
        if textures is not None:
            pairs += _unlinked(pack / "textures", textures.path / "textures")
        dsf = store.info(keys["dsf"]) if keys.get("dsf") else None
        if dsf is not None:
            pairs.append((pack / b.tile.dsf_relpath, dsf.path / f"{b.tile.name}.dsf"))
        for mine, theirs in pairs:
            try:
                a, s = os.stat(mine), os.stat(theirs)
            except OSError:
                continue
            if (
                a.st_dev == s.st_dev == dev
                and a.st_ino != s.st_ino
                and a.st_size == s.st_size
                and a.st_nlink == 1
                and _stamp(mine, a) not in different
            ):
                found.append((pack, mine, theirs, a.st_size))
    return found


def _came(doubles: Iterable[tuple[Path, Path, Path, int]]) -> dict[str, float]:
    """For each tile of ``doubles``, when the newest of its copies was made: its creation time where
    the system keeps one (macOS, Windows), else its change time (Linux); a copy is a new file."""
    came: dict[str, float] = {}
    for pack, mine, _theirs, _size in doubles:
        try:
            st = os.stat(mine)
        except OSError:
            continue
        at = getattr(st, "st_birthtime", None) or st.st_ctime
        came[str(pack)] = max(came.get(str(pack), 0.0), float(at))
    return came


_DIFFERENT = "clean.different"
"""The index's note of the tiles' files Free space read different from the store's own, the same
size: an image its user retouched in the tile, which a DDS of the same picture size always is. As
a copy, it was counted among the room the photos take twice at every measure, and Free space gave
back nothing for it, each time (a campaign, 2026-10-25)."""


def _stamp(path: Path, st: os.stat_result) -> tuple[str, int, int, int]:
    """What tells a file read before from one changed since: its path, size, date and inode."""
    return (str(path), st.st_size, st.st_mtime_ns, st.st_ino)


def _different(store: Store) -> set[tuple[str, int, int, int]]:
    """The files of :data:`_DIFFERENT`, as :func:`_stamp` gives them."""
    try:
        return {tuple(x) for x in json.loads(store.note(_DIFFERENT) or "[]")}  # type: ignore[misc]
    except (ValueError, TypeError):
        return set()


_LISTED_NUMBERS = os.name != "nt"
"""Whether a folder's listing carries each file's number (macOS, Linux), or only its size and
time (Windows), for :func:`_unlinked`."""


def _unlinked(mine: Path, theirs: Path) -> list[tuple[Path, Path]]:
    """The images of the tile's folder ``mine`` and of the store's ``theirs`` of the same name that
    may not be one file, from the two folders' listings, so that the images a tile shares with the
    store, nearly all of them, cost no call of their own. On macOS and Linux the listing carries
    each file's number: those that differ. On Windows it carries their size and time, and a file
    number costs a call per file (Disk space took 31 s with 300 tiles on a Windows PC, 2026-10-25):
    those whose size or time differs, and those that agree too when one of them is not one file
    (a folder copied with its times kept). Measuring every pair took Disk space from 0.8 to 3.4 s
    with 300 tiles on a Mac (2026-10-25)."""
    try:
        with os.scandir(theirs) as it:
            store_side = {e.name: e for e in it if e.name.lower().endswith(".dds")}
        with os.scandir(mine) as it:
            tile_side = [e for e in it if e.name in store_side]
    except OSError:
        return []
    found: list[os.DirEntry[str]] = []
    if _LISTED_NUMBERS:
        for e in tile_side:
            with contextlib.suppress(OSError):
                if e.inode() and e.inode() == store_side[e.name].inode():
                    continue
            found.append(e)
    else:
        agree: list[os.DirEntry[str]] = []
        for e in tile_side:
            try:
                a, s = e.stat(), store_side[e.name].stat()
            except OSError:
                continue
            same = (a.st_size, a.st_mtime_ns) == (s.st_size, s.st_mtime_ns)
            (agree if same else found).append(e)
        if agree:
            first = agree[0]
            try:
                one = os.path.samefile(first.path, store_side[first.name].path)
            except OSError:
                one = False
            if not one:
                found += agree
    return [(Path(e.path), Path(store_side[e.name].path)) for e in found]


def _join(
    store: Store, doubles: Iterable[tuple[Path, Path, Path, int]]
) -> tuple[int, int, list[str]]:
    """Make each of :func:`_doubles` one file with the store's, after reading both whole: a file of
    the tile that is not the same bytes (an image retouched by its user) stays as it is, and is
    noted (:data:`_DIFFERENT`), so that a measure no longer counts it until it changes again; the
    note keeps those of this pass alone, with those read different before and left out of it.
    Returns the files joined, the bytes given back and the tiles they belong to."""
    files = freed = 0
    packs: set[str] = set()
    different = {d for d in _different(store) if _still(d)}
    for pack, mine, theirs, size in doubles:
        try:
            same = filecmp.cmp(mine, theirs, shallow=False)
            st = os.stat(mine)
        except OSError:
            continue
        if not same:
            different.add(_stamp(mine, st))
        elif join_same_file(theirs, mine):
            files += 1
            freed += size
            packs.add(str(pack))
    store.set_note(_DIFFERENT, json.dumps(sorted(different)))
    return files, freed, sorted(packs)


def _still(stamp: tuple[str, int, int, int]) -> bool:
    """Whether the file a :func:`_stamp` names is still as it was then."""
    try:
        return _stamp(Path(stamp[0]), os.stat(stamp[0])) == tuple(stamp)
    except (OSError, IndexError):
        return False


_TWICE = {
    "orthostudio.dem": ("dem.npy", "Data*.alt"),
    "orthostudio.mesh": ("mesh.npz", "Data*.mesh"),
}
"""For each rule whose artefacts hold two forms outside a compacted cache: the form kept, and
Ortho4XP's, the same raster or mesh again, which goes (``CleanReport.twice_files``)."""


def _twice(
    store: Store, going: set[str], grace_s: float
) -> list[tuple[ArtifactInfo, dict[str, int]]]:
    """The relief and mesh artefacts that hold Ortho4XP's form beside the one OrthoStudio XP reads,
    with those files and their sizes (``_TWICE``); not those going anyway (``going``), nor what
    was used in the last ``grace_s`` or is pinned, which a build may be reading. A file is offered
    only when the kept form says the same size of the same thing: as many altitudes in ``dem.npy``
    as the raw ``.alt`` holds, as many points in ``mesh.npz`` as the ``.mesh`` names in its header,
    read from the headers alone, which is what Disk space measures; the trim itself reads every
    file of the artefact whole first and goes only if they are those it was built with
    (``Store.trim``). Its bytes count when it has no other name (never linked)."""
    cutoff = time.time() - grace_s
    found: list[tuple[ArtifactInfo, dict[str, int]]] = []
    for rule, (kept, pattern) in _TWICE.items():
        for info in store.iter_artifacts(rule):
            if info.key in going or max(info.last_used_at, info.created_at) > cutoff:
                continue
            if not (info.path / kept).is_file() or store.pins(info.key):
                continue
            files: dict[str, int] = {}
            for path in sorted(info.path.glob(pattern)):
                try:
                    st = path.lstat()
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and _same_thing(info.path / kept, path, st.st_size):
                    files[path.name] = st.st_size if st.st_nlink <= 1 else 0
            if files:
                found.append((info, files))
    return found


def _same_thing(kept: Path, other: Path, size: int) -> bool:
    """Whether ``other`` holds as many values as ``kept``, read from their headers alone."""
    try:
        if kept.name == "dem.npy":
            with open(kept, "rb") as f:
                shape, _fortran, dtype = _npy_header(f)
            return int(np.prod(shape)) * 4 == size and dtype.kind == "f"
        with zipfile.ZipFile(kept) as z, z.open("vertices.npy") as f:
            shape, _fortran, _dtype = _npy_header(f)
        with open(other, "rb") as f:
            head = f.read(4096)
        at = head.find(b"Vertices\n")
        if at < 0:
            return False
        line = head[at + len(b"Vertices\n") :].split(b"\n", 1)[0]
        return int(line) == int(shape[0])
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return False


def _npy_header(f: IO[bytes]) -> tuple[tuple[int, ...], bool, np.dtype[Any]]:
    """The shape, order and type of the ``.npy`` array ``f`` starts with, its data unread."""
    major, _minor = np.lib.format.read_magic(f)
    if major == 1:
        return np.lib.format.read_array_header_1_0(f)
    return np.lib.format.read_array_header_2_0(f)


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
