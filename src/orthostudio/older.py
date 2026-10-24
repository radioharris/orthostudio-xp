# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The cache made ready for an older OrthoStudio XP, before going back to it.

Since 2026-10-23 the relief and the mesh are kept once in the cache (``dem.npy``, ``mesh.npz``),
and Free space trims the second form earlier versions wrote beside them (``Data<tile>.alt``,
``Data<tile>.mesh``, :meth:`Store.trim`). An earlier version reads that second form, and refuses an
index whose format is 2 (``graph.store.SCHEMA_VERSION``). Before one goes back to it, the Library's
*Prepare for an older version…* puts the cache in the form it reads:

* every second form Free space trimmed is written again from the form kept, by the writers that
  made it (``Dem.write_alt``, ``mesh_file.write_mesh_text``), and taken back only when its size and
  digest are those recorded when it went (:meth:`Store.untrim`): the artefact is as it was built;
* a relief or mesh this version built in its one form, or one whose second form cannot be proved
  the same, is removed: the older version builds it again when it needs it, from the relief on the
  disk, nothing downloaded;
* the index gets the format number 1, and this process opens it no more
  (:func:`~orthostudio.graph.store.close_for_older`): the engine stops after.

Nothing is written unless the disk of the cache has room for all of it first. The tiles are not
touched. Opened again by this version, the cache becomes 2 again and Free space trims again.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from orthostudio.graph import Store
from orthostudio.graph.store import close_for_older
from orthostudio.mesh.mesh_file import read_mesh_npz, write_mesh_text

__all__ = ["OLDER_FORMAT", "OlderPlan", "OlderReport", "plan_older", "prepare_older"]

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


@dataclass
class OlderPlan:
    """What :func:`prepare_older` would do: the second forms to write again (artefact key, file
    name, size), the artefacts to remove, the bytes to write and the room on the disk."""

    restore: list[tuple[str, str, int]] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    write_bytes: int = 0
    free_bytes: int = 0

    @property
    def room(self) -> bool:
        return self.free_bytes >= self.write_bytes + ROOM_MARGIN


@dataclass
class OlderReport:
    """What :func:`prepare_older` did."""

    restored: int = 0
    written_bytes: int = 0
    removed: int = 0
    format: int = OLDER_FORMAT


def _second(name: str, suffix: str) -> bool:
    return name.startswith("Data") and name.endswith(suffix) and "/" not in name


def plan_older(store: Store) -> OlderPlan:
    """What the cache needs before an older version reads it, measured without writing."""
    plan = OlderPlan()
    for rule, (kept, suffix) in SECOND_FORMS.items():
        for info in store.iter_artifacts(rule):
            if info.kind != "dir" or not (info.path / kept).is_file():
                continue
            gone = store.trimmed(info.key)
            trimmed = {name: size for name, size in gone.items() if _second(name, suffix)}
            present = any(_second(p.name, suffix) for p in info.path.iterdir() if p.is_file())
            if trimmed:
                if not store.trimmed_proven(info.key):
                    plan.remove.append(info.key)  # trimmed before digests were kept: unprovable
                    continue
                for name, size in sorted(trimmed.items()):
                    plan.restore.append((info.key, name, size))
                    if not (info.path / name).is_file():
                        plan.write_bytes += size
            elif not present:
                plan.remove.append(info.key)  # built in this version's one form
    plan.free_bytes = shutil.disk_usage(store.root).free
    return plan


def _write_second(store: Store, key: str, tmp: Path) -> None:
    """Write at ``tmp`` the second form of the artefact ``key``, from the form kept, by the writer
    that made it; the caller puts it in place."""
    info = store.info(key)
    assert info is not None
    if info.rule == "orthostudio.dem":
        # Dem.write_alt: float32, row-major, no header, from the raster dem.npy holds
        raster = np.load(info.path / "dem.npy", allow_pickle=False)
        np.asarray(raster, dtype=np.float32).tofile(tmp)
    else:
        write_mesh_text(tmp, read_mesh_npz(info.path / "mesh.npz"))


def prepare_older(
    store_root: str | Path,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> OlderReport:
    """Put the cache at ``store_root`` in the form an older version reads (module docstring), and
    close it for this process. ``progress(written, to_write)`` is called as the files are written.
    Raises ``OSError`` (``ENOSPC``) before writing anything when the disk has not the room."""
    report = OlderReport()
    root = Path(store_root)
    with Store(root, sweep=False) as store:
        plan = plan_older(store)
        if not plan.room:
            raise OSError(
                28,
                f"{plan.write_bytes + ROOM_MARGIN} bytes are needed on the disk of the cache, "
                f"{plan.free_bytes} are free",
                str(root),
            )
        doomed = set(plan.remove)
        done = 0
        for key, name, size in plan.restore:
            if key in doomed:
                continue
            info = store.info(key)
            if info is None:
                continue
            final = info.path / name
            if not final.is_file():  # an earlier run that stopped may have put it back already
                tmp = info.path / f"{name}.older-tmp"
                try:
                    _write_second(store, key, tmp)
                    tmp.replace(final)
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
                done += size
                report.written_bytes += size
                if progress is not None:
                    progress(done, plan.write_bytes)
            if store.untrim(key, name):
                report.restored += 1
            else:
                # not the file it was built with: the older version builds the artefact again
                log.warning("%s of %s is not as it was built: the artefact goes", name, key[:16])
                final.unlink(missing_ok=True)
                doomed.add(key)
        for key in sorted(doomed):
            if store.delete(key, force=True):
                report.removed += 1
    close_for_older(root, OLDER_FORMAT)
    return report
