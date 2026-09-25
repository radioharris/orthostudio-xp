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

Sizes are what the disk gets back, not what the index adds up. A DDS is hard-linked into its
``texture.dds`` artefact, into a ``tile.textures`` artefact and into the pack: its bytes are
freed only when the last of those links goes, and :func:`disk_bytes` counts them once (the page's
store size and the size of each pack in its library).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from orthostudio.graph import ArtifactInfo, Store
from orthostudio.install import Library
from orthostudio.pipeline.pack import MANIFEST_NAME, read_manifest

__all__ = [
    "DELETE_GRACE_S",
    "GRACE_S",
    "MAPCACHE_DIR",
    "CleanReport",
    "clean",
    "clean_after_delete",
    "disk_bytes",
    "freed_bytes",
    "keep_keys",
    "needed_keys",
    "pack_dirs",
]

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
    """The imagery caches: ``chunks``, and ``mapcache`` when it was given."""
    images_removed: bool = False
    mapcache_bytes: int = 0
    """The map cache's share of ``images_bytes``."""
    relief_bytes: int = 0
    """The elevation cells downloaded and kept (``<data folder>/elevation``). Counted apart from
    the imagery: a square of relief is 40 MB from Copernicus against 400 MB from the USGS, and
    fetching it again costs far less than the imagery, so it is offered as its own choice. It was
    invisible until a user emptied everything and found 1.4 GB left (2026-09-18)."""
    relief_removed: bool = False

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


def keys_of_packs_out_of_reach(library_path: Path | None) -> set[str]:
    """The artefact keys of tiles the library knows but cannot see on disk right now.

    A pack whose ``orthostudio.toml`` cannot be read was taken for gone, so everything it was
    built from became collectable: unplug the external disk the tiles live on, press Free space,
    and the cache of every tile on it is given away under the words "data no tile needs any
    more". No scenery is lost, but the next build of those tiles downloads and encodes
    everything again (found in review, 2026-09-23).

    The library recorded those keys when the tile was built, so they are kept without reading
    anything from the missing disk. A tile imported from Ortho4XP has none, and needs none: it
    was not built here.
    """
    kept: set[str] = set()
    if library_path is None or not library_path.is_file():
        return kept
    with contextlib.suppress(Exception), Library(library_path) as lib:
        for entry in lib.list():
            if entry.keys and not (Path(entry.path) / MANIFEST_NAME).is_file():
                kept.update(str(k) for k in entry.keys.values() if k)
    return kept


def keep_keys(store: Store, packs: Iterable[Path], library_path: Path | None = None) -> set[str]:
    """Every stored artefact the ``packs`` need, see the module docstring."""
    roots: set[str] = set()
    for pack in packs:
        try:
            roots.update(read_manifest(pack).keys.values())
        except (OSError, ValueError, KeyError):
            continue
    roots |= keys_of_packs_out_of_reach(library_path)
    return needed_keys(store, roots)


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


def _file_stats(root: Path, *, links: bool = True) -> Iterator[os.stat_result]:
    """The ``lstat`` of every regular file under ``root``, or of ``root`` when it is one.

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
        yield st
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
) -> CleanReport:
    """Collect what no pack needs; with ``images``, empty the imagery caches as well.

    The imagery caches are ``chunks_root`` and the map cache ``mapcache_root``; both count in
    ``images_bytes``. Without ``mapcache_root`` (the default) no map cache is counted or
    emptied: a directory beside ``chunks_root`` is never assumed to be one (``osxp clean`` passes
    ``$OSXP_HOME/mapcache``).

    ``elevation_root`` is counted in ``relief_bytes`` and emptied with ``relief``, its own choice:
    the relief of a square is worth far less to download again than its imagery, and it used to be
    freed by nothing at all.
    """
    report = CleanReport(dry_run=dry_run)
    packs = pack_dirs(library_path, tiles_root)
    report.packs = [str(p) for p in packs]
    if Path(store_root).is_dir():
        with Store(store_root) as store:
            keep = keep_keys(store, packs, library_path)
            _collect(store, store.iter_artifacts(), keep, grace_s, report)
            if not dry_run:
                report.tmp_removed = len(store.sweep_tmp(max_age_s=grace_s))
    chunks = Path(chunks_root)
    mapcache = Path(mapcache_root) if mapcache_root is not None else None
    for cache in (chunks, mapcache):
        if cache is None or not cache.is_dir() or (cache is mapcache and _overlap(cache, chunks)):
            continue
        size = freed_bytes([cache])
        report.images_bytes += size
        if cache is mapcache:
            report.mapcache_bytes = size
        if images and not dry_run:
            _empty(cache)
            report.images_removed = True
    elevation = Path(elevation_root) if elevation_root is not None else None
    if elevation is not None and elevation.is_dir() and not _overlap(elevation, chunks):
        report.relief_bytes = freed_bytes([elevation])
        if relief and not dry_run:
            _empty(elevation)
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
            keep = keep_keys(store, packs, library_path)
            _collect(store, candidates, keep, grace_s, report)
    return report


def _collect(
    store: Store,
    candidates: Iterable[ArtifactInfo],
    keep: set[str],
    grace_s: float,
    report: CleanReport,
) -> None:
    """Delete the ``candidates`` no pack needs (``keep``), not pinned, unused for ``grace_s``;
    count them in ``report`` (nothing is deleted when it is a dry run)."""
    cutoff = time.time() - grace_s
    doomed = [
        info
        for info in candidates
        if info.key not in keep
        and max(info.last_used_at, info.created_at) <= cutoff
        and not store.pins(info.key)
    ]
    report.kept = len(store) - len(doomed)
    report.removed = len(doomed)
    report.freed_bytes = freed_bytes(i.path for i in doomed if i.path.exists())
    if not report.dry_run:
        for info in doomed:
            store.delete(info.key, force=True)


def _overlap(a: Path, b: Path) -> bool:
    """``a`` and ``b`` are one directory, or one holds the other: count and empty it once."""
    ra, rb = a.resolve(), b.resolve()
    return ra == rb or ra.is_relative_to(rb) or rb.is_relative_to(ra)


def _empty(directory: Path) -> None:
    """Delete what ``directory`` holds, not the directory; a file gone meanwhile is not an error."""

    def vanished(_function: object, _path: object, exc: BaseException) -> None:
        if not isinstance(exc, FileNotFoundError):
            raise exc

    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, onexc=vanished)
        else:
            child.unlink(missing_ok=True)
