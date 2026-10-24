# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The cache made ready for an older OrthoStudio XP, before going back to it.

Since 2026-10-23 the relief and the mesh are kept once in the cache (``dem.npy``, ``mesh.npz``),
and Free space trims the second form earlier versions wrote beside them (``Data<tile>.alt``,
``Data<tile>.mesh``, :meth:`Store.trim`). An earlier version reads that second form, also to find
a neighbouring tile's mesh, which draws the shores along the border between them; and it refuses
an index whose format is 2 (``graph.store.SCHEMA_VERSION``). Before one goes back to it, the
Library's *Prepare for an older version…* puts the cache in the form it reads:

* every second form Free space trimmed is written again from the form kept, by the writers that
  made it (``Dem.write_alt``, ``mesh_file.write_mesh_text``), on the disk itself before the index
  says so, and taken back only when its size and digest are those recorded when it went
  (:meth:`Store.untrim`): the artefact is as it was built;
* a relief or mesh this version built in its one form gets its second form the same way, and is
  taken as if it had been built with it (:meth:`Store.complete`): an older version reuses it, and
  sees a neighbour this version built;
* one whose form kept cannot be read, or whose trimmed form cannot be proved the same, is removed:
  the older version builds it again when it needs it, nothing downloaded;
* the index gets the format number 1, and this process opens it no more
  (:func:`~orthostudio.graph.store.close_for_older`): the engine stops after.

Nothing is written unless the disk of the cache has room for all of it first; without that room,
``remove_instead`` removes those artefacts rather than writing them, the older version building
them again (until a tile is built again, its neighbours built by the older version do not see its
mesh). ``stop`` ends the run between two files, the cache left this version's (format 2) and sound:
a run started again goes on from there. The tiles are not touched. Opened again by this version,
the cache becomes 2 again and Free space trims again.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import numpy as np

from orthostudio.fsutil import fsync_dir
from orthostudio.fsutil import replace as fs_replace
from orthostudio.graph import ArtifactInfo, Store
from orthostudio.graph.store import close_for_older
from orthostudio.mesh.mesh_file import read_mesh_npz, write_mesh_text

__all__ = [
    "OLDER_FORMAT",
    "OlderPlan",
    "OlderReport",
    "OlderStoppedError",
    "plan_older",
    "prepare_older",
]

log = logging.getLogger("orthostudio.older")

OLDER_FORMAT = 1
"""The index format of every version before the relief and the mesh were kept once."""
SECOND_FORMS = {
    "orthostudio.dem": ("dem.npy", ".alt"),
    "orthostudio.mesh": ("mesh.npz", ".mesh"),
}
"""For each rule: the form this version keeps, and the suffix of the ``Data<tile>`` file an older
version reads beside it."""
ROOM_MARGIN = 512 * 1024 * 1024
"""Room left on the disk once everything is written: a disk filled to its last byte fails the
index's own writes."""
TMP_SUFFIX = ".older-tmp"
"""A second form being written; ``write_mesh_text`` adds its own ``.tmp`` to it."""


class OlderStoppedError(Exception):
    """The run was asked to stop (:func:`prepare_older` ``stop``): the cache is this version's."""


@dataclass
class OlderPlan:
    """What :func:`prepare_older` would do: the trimmed second forms to write again (artefact
    key, file name, size), the second forms to add to artefacts built in one form (key, name,
    bytes estimated), the artefacts to remove, the bytes to write and the room on the disk."""

    restore: list[tuple[str, str, int]] = field(default_factory=list)
    complete: list[tuple[str, str, int]] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    write_bytes: int = 0
    free_bytes: int = 0

    @property
    def need_bytes(self) -> int:
        """What the disk must have free: the bytes to write and the margin."""
        return self.write_bytes + ROOM_MARGIN

    @property
    def room(self) -> bool:
        return self.free_bytes >= self.need_bytes


@dataclass
class OlderReport:
    """What :func:`prepare_older` did."""

    restored: int = 0
    completed: int = 0
    written_bytes: int = 0
    removed: int = 0
    format: int = OLDER_FORMAT


def _second(name: str, suffix: str) -> bool:
    return name.startswith("Data") and name.endswith(suffix) and "/" not in name


def _second_name(store: Store, info: ArtifactInfo, suffix: str) -> str | None:
    """``Data<tile>`` with ``suffix`` for an artefact of one tile, from its recipe."""
    try:
        tile = store.why(info.key).params.get("tile")
    except (OSError, ValueError, KeyError):
        return None
    return f"Data{tile}{suffix}" if isinstance(tile, str) and tile else None


def _npy_header(f: IO[bytes]) -> tuple[Any, ...]:
    """The shape, order and type of the ``.npy`` array ``f`` starts with, its data unread."""
    major, _minor = np.lib.format.read_magic(f)
    if major == 1:
        return tuple(np.lib.format.read_array_header_1_0(f))
    return tuple(np.lib.format.read_array_header_2_0(f))


def _estimate(info: ArtifactInfo, kept: str) -> int | None:
    """The bytes of the second form, from the headers of the form kept: four a value of the
    relief; about 75 a vertex and 28 a triangle of the mesh's text. ``None`` when unreadable."""
    try:
        if kept == "dem.npy":
            with open(info.path / kept, "rb") as f:
                shape = _npy_header(f)[0]
            return int(np.prod(shape)) * 4
        with zipfile.ZipFile(info.path / kept) as z:
            with z.open("vertices.npy") as f:
                vertices = int(_npy_header(f)[0][0])
            with z.open("tris.npy") as f:
                triangles = int(_npy_header(f)[0][0])
        return vertices * 75 + triangles * 28 + 4096
    except (OSError, ValueError, KeyError, IndexError, zipfile.BadZipFile):
        return None


def _others_bytes(info: ArtifactInfo, suffix: str) -> int:
    """The bytes of the artefact's files on the disk, but its second forms."""
    return sum(
        p.stat().st_size for p in info.path.iterdir() if p.is_file() and not _second(p.name, suffix)
    )


def plan_older(store: Store) -> OlderPlan:
    """What the cache needs before an older version reads it, measured without writing."""
    plan = OlderPlan()
    for rule, (kept, suffix) in SECOND_FORMS.items():
        for info in store.iter_artifacts(rule):
            if info.kind != "dir" or not (info.path / kept).is_file():
                continue
            gone = store.trimmed(info.key)
            trimmed = {name: size for name, size in gone.items() if _second(name, suffix)}
            on_disk = {
                p.name: p.stat().st_size
                for p in info.path.iterdir()
                if p.is_file() and _second(p.name, suffix)
            }
            if trimmed:
                if not store.trimmed_proven(info.key):
                    plan.remove.append(info.key)  # trimmed before digests were kept: unprovable
                    continue
                for name, size in sorted(trimmed.items()):
                    plan.restore.append((info.key, name, size))
                    if name not in on_disk:
                        plan.write_bytes += size
            elif not on_disk:
                name = _second_name(store, info, suffix)
                size = _estimate(info, kept)
                if name is None or size is None:
                    plan.remove.append(info.key)  # nothing to write it from: built again
                    continue
                plan.complete.append((info.key, name, size))
                plan.write_bytes += size
            elif _others_bytes(info, suffix) == info.size:
                # written by a run that stopped before the index took it: taken now
                plan.complete.extend((info.key, name, 0) for name in sorted(on_disk))
    plan.free_bytes = shutil.disk_usage(store.root).free
    return plan


def _sweep_tmp(store: Store) -> None:
    """The second forms an interrupted run left half written: gone, a new run writes them again."""
    for rule in SECOND_FORMS:
        for info in store.iter_artifacts(rule):
            if info.kind == "dir" and info.path.is_dir():
                for path in info.path.glob(f"*{TMP_SUFFIX}*"):
                    path.unlink(missing_ok=True)


def _read_kept(info: ArtifactInfo) -> Any:
    """The form kept, whole: the relief's raster or the mesh. Raises on a file that cannot be read
    (``OSError``, ``ValueError``, ``zipfile.BadZipFile``...)."""
    if info.rule == "orthostudio.dem":
        return np.load(info.path / "dem.npy", allow_pickle=False)
    return read_mesh_npz(info.path / "mesh.npz")


def _write_second(info: ArtifactInfo, kept: Any, final: Path) -> None:
    """Write the second form at ``final`` from the form kept, by the writer that made it, through
    a temporary name, on the disk itself (file and folder) before it is renamed and before the
    index hears of it: a power cut must not leave the index saying whole a file the disk lost."""
    tmp = final.with_name(final.name + TMP_SUFFIX)
    try:
        if info.rule == "orthostudio.dem":
            # Dem.write_alt: float32, row-major, no header, from the raster dem.npy holds
            np.asarray(kept, dtype=np.float32).tofile(tmp)
        else:
            write_mesh_text(tmp, kept)
        with open(tmp, "r+b") as f:
            os.fsync(f.fileno())
        fs_replace(tmp, final)
        fsync_dir(final.parent)
    except BaseException:
        tmp.unlink(missing_ok=True)
        tmp.with_name(tmp.name + ".tmp").unlink(missing_ok=True)
        raise


def prepare_older(
    store_root: str | Path,
    *,
    progress: Callable[[int, int], None] | None = None,
    remove_instead: bool = False,
    stop: threading.Event | None = None,
) -> OlderReport:
    """Put the cache at ``store_root`` in the form an older version reads (module docstring), and
    close it for this process. ``progress(written, to_write)`` is called as files are written.
    Raises ``OSError`` (``ENOSPC``) before writing anything when the disk has not the room and not
    ``remove_instead``; :class:`OlderStoppedError` when ``stop`` is set, the cache left at 2."""

    def check() -> None:
        if stop is not None and stop.is_set():
            raise OlderStoppedError("the preparation was stopped; the cache is this version's")

    report = OlderReport()
    root = Path(store_root)
    with Store(root, sweep=False) as store:
        _sweep_tmp(store)
        plan = plan_older(store)
        doomed = set(plan.remove)
        if remove_instead:
            doomed |= {key for key, _name, _size in plan.restore + plan.complete}
        elif not plan.room:
            raise OSError(
                errno.ENOSPC,
                f"{plan.need_bytes} bytes are needed on the disk of the cache, "
                f"{plan.free_bytes} are free",
                str(root),
            )
        to_write = plan.write_bytes
        steps = [(key, name, size, True) for key, name, size in plan.restore]
        steps += [(key, name, size, False) for key, name, size in plan.complete]
        for key, name, size, trimmed in steps:
            check()
            info = store.info(key)
            if key in doomed or info is None:
                continue
            final = info.path / name
            if not final.is_file():  # an earlier run that stopped may have written it already
                try:
                    kept = _read_kept(info)
                except Exception as exc:  # the form kept is damaged: built again
                    log.warning("%s cannot be read (%s): the artefact goes", key[:16], exc)
                    doomed.add(key)
                    continue
                _write_second(info, kept, final)
                report.written_bytes += size
                if progress is not None:
                    progress(min(report.written_bytes, to_write), to_write)
            if trimmed and store.untrim(key, name):
                report.restored += 1
            elif not trimmed and store.complete(key, name):
                report.completed += 1
            else:
                # not the file it was built with: the older version builds the artefact again
                log.warning("%s of %s is not as built: the artefact goes", name, key[:16])
                final.unlink(missing_ok=True)
                doomed.add(key)
        for key in sorted(doomed):
            check()
            if store.delete(key, force=True):
                report.removed += 1
        check()
    close_for_older(root, OLDER_FORMAT)
    return report
