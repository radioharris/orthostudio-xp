# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The clouds laid in a tile's folder, put back, and laid again after a build (``clouds.md`` 2.2,
2.3). What Apply does is computed one way (:func:`plan_folder`): the cloud mode shows it
(``state.view``) and Apply runs it (:func:`lay_folder`). What X-Plane shows changes one way
(:func:`lay_folder`), under the tile's lock: Apply and Restore (:func:`show`) give it the clouds
of the view, or none, then tell the list which it shows (``laid``); a build of the tile gives it
the clouds Apply last laid (:func:`lay_again`), and leaves the list as it is: those that do not
stand on its textures are laid again by the next build they stand on.

A texture is laid from the build's own: its first level read, the clouds laid over it by the
layer the previews are drawn with (``clouds.layer``), its levels made again as the build makes
them, and of each level only the blocks a cloud reaches encoded again, as the build encodes them;
every other block is the build's, byte for byte. The build's own is kept beside, the same file
(``clouds/<name>.dds``), and the clouds laid in it beside too (``clouds/<digest>.json``), the
laid texture stamped with both (``tile.Stamp``). A laying is all or nothing, each file written
whole: a program cut short leaves every texture whole, saying what it shows.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np

from orthostudio.clouds import cloudlist
from orthostudio.clouds.layer import decode_part, lay, layer
from orthostudio.clouds.tile import (
    DIGEST_HEX,
    Stamp,
    TileClouds,
    beside,
    clouds_digest,
    clouds_dir,
    entry_patches,
    find_part,
    held_by_id,
    identity,
    is_own_folder,
    kept_files,
    laid_fields,
    lays_not,
    leftovers,
    part_path,
    reaching,
    read_tile,
    same_laid,
    sidecar_path,
    sidecars,
    stamped,
    texture_digest,
    textures_dir,
    unready,
)
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_text, fsync_dir, replace
from orthostudio.graph.digest import digest_bytes
from orthostudio.graph.store import _pid_alive, flush_disk
from orthostudio.imagery.grid import parse_texture_name
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import PackEnv, _refuse_rewrite_under_running_xplane
from orthostudio.textures import dds
from orthostudio.textures.bcdecode import decode_blocks
from orthostudio.textures.encode import encode_blocks
from orthostudio.textures.mips import mip_chain

__all__ = [
    "FolderPlan",
    "folder_lock",
    "lay_again",
    "lay_folder",
    "lay_texture",
    "plan_folder",
    "show",
]

log = logging.getLogger(__name__)

_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


def folder_lock(tile: TileRef) -> threading.Lock:
    """The tile's textures written by one at a time in this program (Apply, Restore, a build's
    laying again), and read by none meanwhile (the map's picture)."""
    with _GUARD:
        return _LOCKS.setdefault(tile.name, threading.Lock())


# -- what Apply does ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FolderPlan:
    """What laying in a folder does: the clouds it shows after (``laid``, in order), the textures
    made again with the clouds each shows (``work``), and why a cloud's first images are not laid
    (``dropped``: ``tile.lays_not``'s, or ``parts`` for frozen parts not on this computer where a
    texture of theirs is made again)."""

    laid: list[Mapping[str, Any]]
    work: list[tuple[str, list[Mapping[str, Any]]]]
    dropped: dict[str, str]

    @property
    def remade(self) -> frozenset[str]:
        """The textures made again: every other one stays as X-Plane shows it."""
        return frozenset(name for name, _mine in self.work)


def plan_folder(
    tile: TileRef, shown: TileClouds, clouds: Sequence[Sequence[Mapping[str, Any]]]
) -> FolderPlan:
    """The one computing of what laying in the folder ``shown`` does, each cloud of ``clouds``
    (in laying order) given as the images it may show, the wanted first: of each, the first that
    can be laid is. Images the folder shows as they are need nothing to stay but their frozen
    parts where their texture is made again; others need to stand on the tile and their frozen
    parts (``tile.lays_not``). A texture is made again when the clouds it shows are not those it
    is to show, or no file beside it says which it shows (``TileClouds.known``)."""
    held = held_by_id(shown)
    dropped: dict[str, str] = {}
    keep: list[Mapping[str, Any]] = []
    for options in clouds:
        for e in options:
            why = None if same_laid(held.get(e["id"]), e) else lays_not(e, tile, shown)
            if why is None:
                keep.append(e)
                break
            dropped.setdefault(e["id"], why)
    while True:
        work = []
        for name in shown.names:
            mine = reaching(keep, name)
            want = clouds_digest(mine) if mine else None
            stamp = shown.stamps.get(name)
            if (stamp.clouds if stamp is not None else None) != want or (
                stamp is not None and name not in shown.known
            ):
                work.append((name, list(mine)))
        made = {name for name, _mine in work}
        lost = [
            e
            for e in keep
            if any(n in made and find_part(str(p[1])) is None for n, p in e["parts"].items())
        ]
        if not lost:
            return FolderPlan(keep, work, dropped)
        for e in lost:
            dropped.setdefault(e["id"], "parts")
            keep.remove(e)


# -- one texture -------------------------------------------------------------------------------


def lay_texture(
    data: bytes, name: str, tile: TileRef, entries: Sequence[Mapping[str, Any]]
) -> bytes:
    """The texture ``name`` of ``tile`` as built (its DDS ``data``) with the clouds ``entries``
    laid in it, in their order, from their frozen parts (:func:`plan_folder` saw them here);
    stamped."""
    header, levels = dds.split_levels(data)
    t = parse_texture_name(name)
    first = decode_blocks(bytes(levels[0]), header.width, header.height, header.fmt)
    layers = []
    for e in entries:
        digest = str(e["parts"][name][1])
        path = find_part(digest)
        if path is None:
            raise OsxpError(
                "CLOUD_TEXTURE_LOST", context={"tile": tile.name, "path": str(part_path(digest))}
            )
        patch = entry_patches(e, tile, [t]).get(name)
        la = None if patch is None else layer(patch, t, decode_part(path.read_bytes()))
        if la is not None:
            layers.append(la)
    rgb = lay(np.ascontiguousarray(first[:, :, :3]), layers)
    image = rgb if header.fmt == "bc1" else np.dstack([rgb, first[:, :, 3]])
    reached = np.zeros(rgb.shape[:2], bool)
    for la in layers:
        x0, y0, x1, y1 = la.window
        reached[y0:y1, x0:x1] |= la.alpha > 0
    chain = mip_chain(image, levels=len(levels))  # the build's own, from the first level
    blocks = _blocks(reached)
    out = []
    for k, level in enumerate(levels):
        if k:
            blocks = _halved(blocks)
        out.append(_spliced(bytes(level), chain[k], blocks, header.fmt))
    sizes = sum(len(b) for b in out)
    stamp = Stamp(digest_bytes(data)[:DIGEST_HEX], clouds_digest(entries))
    head = stamped(data[: dds.HEADER_SIZE], stamp)
    return head + b"".join(out) + data[dds.HEADER_SIZE + sizes :]


def _blocks(reached: np.ndarray) -> np.ndarray:
    """The first level's blocks a cloud reaches."""
    h, w = reached.shape
    pad = np.zeros((-(-h // 4) * 4, -(-w // 4) * 4), bool)
    pad[:h, :w] = reached
    return pad.reshape(pad.shape[0] // 4, 4, pad.shape[1] // 4, 4).any(axis=(1, 3))


def _halved(blocks: np.ndarray) -> np.ndarray:
    """The next level's blocks a cloud reaches: a block there is two by two blocks here, down
    to the one block of the smallest levels."""
    h, w = blocks.shape
    rows, cols = (-(-h // 2), 2) if h > 1 else (1, 1), (-(-w // 2), 2) if w > 1 else (1, 1)
    pad = np.zeros((rows[0] * rows[1], cols[0] * cols[1]), bool)
    pad[:h, :w] = blocks
    return pad.reshape(rows[0], rows[1], cols[0], cols[1]).any(axis=(1, 3))


def _spliced(level: bytes, image: np.ndarray, blocks: np.ndarray, fmt: str) -> bytes:
    """``level`` with the ``blocks`` a cloud reaches encoded again from ``image``."""
    if not blocks.any():
        return level
    size = dds.BLOCK_BYTES[fmt]
    bh, bw = blocks.shape
    out = np.frombuffer(level, np.uint8).reshape(bh, bw, size).copy()
    rows, cols = np.nonzero(blocks)
    r0, r1, c0, c1 = int(rows.min()), int(rows.max()) + 1, int(cols.min()), int(cols.max()) + 1
    part = np.ascontiguousarray(image[r0 * 4 : r1 * 4, c0 * 4 : c1 * 4])
    made = np.frombuffer(encode_blocks(part, fmt), np.uint8).reshape(r1 - r0, c1 - c0, size)
    here = blocks[r0:r1, c0:c1]
    out[r0:r1, c0:c1][here] = made[here]
    return out.tobytes()


# -- the folder --------------------------------------------------------------------------------


def show(
    tile: TileRef,
    folder: Path,
    clouds: Sequence[Sequence[Mapping[str, Any]]],
    *,
    custom_scenery: Path | None,
    progress: Callable[[int, int], None] | None = None,
) -> list[Mapping[str, Any]]:
    """Apply and Restore: the tile's folder made to show ``clouds``, each given as the images it
    may show, the wanted first (:func:`lay_folder`), then the list told which it shows
    (``laid``, what a build lays again), when that changed. A write refused leaves the list as it
    was. Returns
    the clouds laid."""
    with folder_lock(tile):
        laid = lay_folder(tile, folder, clouds, custom_scenery=custom_scenery, progress=progress)
        now = [dict(e) for e in laid]
        if cloudlist.load(tile).laid != now:
            with cloudlist.update(tile) as cl:
                cl.laid = now
        return laid


def lay_folder(
    tile: TileRef,
    folder: Path,
    clouds: Sequence[Sequence[Mapping[str, Any]]],
    *,
    custom_scenery: Path | None,
    progress: Callable[[int, int], None] | None = None,
) -> list[Mapping[str, Any]]:
    """Make the whole folder show what :func:`plan_folder` says of ``clouds``, each given as the
    images it may show, the wanted first. Refused while X-Plane runs and shows the tile, as a
    build is (``CLOUD_XPLANE_RUNNING``). All or nothing: every new texture is made first beside its
    own and written to the disk, then each is put in place, only over the very file read, the one
    before kept until all are; when one cannot be (X-Plane holding it, a build replacing it
    meanwhile), those put in place are put back, a build's own left as it is, and nothing has
    changed. The files no longer needed beside go after, whatever stops that. A folder not whole (a
    build cut short, files taken from it) is never laid in. Called under the tile's lock
    (:func:`folder_lock`). Returns the clouds laid."""
    _not_under_xplane(tile, folder, custom_scenery)
    shown = read_tile(folder)
    why = unready(shown)
    if why is not None:
        raise OsxpError("CLOUD_TILE_NOT_READY", context={"tile": tile.name, "state": why})
    assert shown is not None
    _sweep_leftovers(shown)
    plan = plan_folder(tile, shown, clouds)
    made: list[tuple[str, Path]] = []
    try:
        for n, (name, mine) in enumerate(plan.work):
            if progress is not None:
                progress(n, len(plan.work))
            made.append((name, _make(tile, shown, name, mine)))
        if made:  # every new texture on the disk itself, its cache too, before one is in place
            with contextlib.suppress(OSError):  # a disk that has no such flush: fsync was it
                flush_disk(made[-1][1])
        _put_in_place(tile, shown, made)
    finally:
        for _name, path in made:
            with contextlib.suppress(OSError):  # left, it goes at a later laying
                path.unlink(missing_ok=True)
    with contextlib.suppress(OsxpError, OSError):  # what is laid stays laid
        _sweep(read_tile(folder) or shown)
    if progress is not None:
        progress(len(plan.work), len(plan.work))
    return plan.laid


def _not_under_xplane(tile: TileRef, folder: Path, custom_scenery: Path | None) -> None:
    """The build's own rule (``pack._refuse_rewrite_under_running_xplane``), said in Apply's
    words (``CLOUD_XPLANE_RUNNING``)."""
    env = cast(PackEnv, SimpleNamespace(custom_scenery=custom_scenery))
    try:
        _refuse_rewrite_under_running_xplane(env, tile, Path(folder))
    except OsxpError as exc:
        raise OsxpError("CLOUD_XPLANE_RUNNING", context={"tile": tile.name}) from exc


def _place(src: Path, dest: Path) -> None:
    """``dest`` made the same file as ``src`` (a hard link, else a copy written to the disk), in a
    folder that is there: a tile deleted meanwhile is never made again."""
    dest.unlink(missing_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)
        with open(dest, "rb+") as f:
            os.fsync(f.fileno())


def _write(path: Path, data: bytes) -> None:
    """``data`` at ``path``, a new file of its own (never one another name shares: a link left
    there is let go first, not written through) and on the disk before it is put in place."""
    path.unlink(missing_ok=True)
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _make(tile: TileRef, shown: TileClouds, name: str, mine: Sequence[Mapping[str, Any]]) -> Path:
    """The new texture ``name``, made beside it: laid with ``mine``, else the build's own; the
    build's own kept aside first, and the clouds it shows written beside."""
    texture, kept = shown.texture_path(name), shown.kept_path(name)
    plain = shown.plain_path(name)
    new = beside(texture, "new")
    if not mine:  # the build's own again
        _place(plain, new)
        return new
    data = lay_texture(plain.read_bytes(), name, tile, mine)
    kept.parent.mkdir(exist_ok=True)  # the tile's folder itself is never made again
    if not _same_file(plain, kept):  # a build links its textures from its store: often it is
        aside = beside(kept, "kept")  # (a rename onto the same file does nothing)
        _place(plain, aside)
        replace(aside, kept)
    side = json.dumps([laid_fields(e) for e in mine], sort_keys=True)
    atomic_write_text(sidecar_path(shown.folder, clouds_digest(mine)), side, fsync=True)
    _write(new, data)
    return new


def _put_in_place(tile: TileRef, shown: TileClouds, made: Sequence[tuple[str, Path]]) -> None:
    """Each texture of ``made`` replaced by its new one, only over the very file read, the ones
    before kept until all are in place; when one cannot be, those put in place are put back
    unless a build placed its own over them since."""
    done: list[tuple[Path, Path, Any]] = []
    texture = shown.folder
    try:
        for name, new in made:
            texture = shown.texture_path(name)
            before = beside(texture, "before")
            _place(texture, before)
            if identity(texture) != shown.files[name]:  # a build placed its own meanwhile
                with contextlib.suppress(OSError):
                    before.unlink(missing_ok=True)
                raise OsxpError(
                    "CLOUD_TILE_NOT_READY", context={"tile": tile.name, "state": "in_build"}
                )
            ours = identity(new)  # taken first: the rename keeps it, whatever comes after
            try:
                replace(new, texture)
            except BaseException:
                with contextlib.suppress(OSError):
                    before.unlink(missing_ok=True)
                raise
            done.append((texture, before, ours))
        fsync_dir(textures_dir(shown.folder))
    except BaseException as exc:
        for placed, before, ours in reversed(done):
            with contextlib.suppress(OSError):
                if identity(placed) == ours:  # still Apply's: the one before back
                    replace(before, placed)
        if isinstance(exc, PermissionError):  # Windows: X-Plane holds the texture
            raise OsxpError(
                "XP_PACK_CONFLICT",
                context={"tile": tile.name, "pack": str(texture), "reason": str(exc)},
                message=f"{texture} is in use and cannot be replaced ({exc}).",
                remedy="Quit X-Plane (or the tool holding the file), then apply again.",
            ) from exc
        raise
    finally:
        for _placed, before, _ours in done:
            with contextlib.suppress(OSError):  # left, it goes at a later laying
                before.unlink(missing_ok=True)


def _sweep(shown: TileClouds) -> None:
    """What lies beside the textures and no texture needs goes: the build's own kept for a
    texture that is the build's own again (:func:`_builds_own_again`), the clouds of no stamp."""
    named = {s.clouds for s in shown.stamps.values()}
    for name, kept in kept_files(shown.folder).items():
        with contextlib.suppress(OSError):
            if name not in shown.stamps and _builds_own_again(shown, name, kept):
                kept.unlink()
    for digest, side in sidecars(shown.folder).items():
        if digest not in named:
            with contextlib.suppress(OSError):
                side.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        clouds_dir(shown.folder).rmdir()  # when empty


def _builds_own_again(shown: TileClouds, name: str, kept: Path) -> bool:
    """Whether the texture ``name``, showing no cloud, is the build's own again, so the one kept
    beside it is needed no more: the kept one itself, or the store's (a build placed it since),
    or the same bytes as one of them; its texture gone with a build of other textures. A texture
    that is none of them (one a power cut left torn) keeps it: it may be the only one left."""
    texture = shown.texture_path(name)
    if name not in shown.names:
        return not texture.exists()
    stored = shown.stored_path(name)
    owns = [p for p in (kept, stored) if p is not None and p.is_file()]
    if any(_same_file(texture, p) for p in owns):
        return True
    return texture_digest(texture) in {texture_digest(p) for p in owns}


def _sweep_leftovers(shown: TileClouds) -> None:
    """The files a laying cut short left beside the textures (``tile.leftovers``), whose program
    is gone: one of a program under way stays."""
    for left, pid in leftovers(shown.folder):
        if pid != os.getpid() and not _pid_alive(pid):
            with contextlib.suppress(OSError):
                left.unlink(missing_ok=True)


def lay_again(
    tile: TileRef,
    folder: Path,
    *,
    custom_scenery: Path | None,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """After a build of ``tile`` in ``folder``, its own: the clouds Apply last laid laid again,
    those whose images stand on the textures it placed; the list left as it is, so the others come
    back with a build they stand on again (another build's colours tried, then the first ones
    back). Nothing when Apply laid none. ``progress``: as :func:`lay_folder` says it."""
    if not cloudlist.load(tile).laid:
        return
    if not is_own_folder(tile, folder, custom_scenery=custom_scenery):
        # a build written elsewhere (a command line's own folder): not the tile's clouds
        log.info("%s: built in %s, not its own folder: its clouds left out", tile.name, folder)
        return
    with folder_lock(tile):
        laid = cloudlist.load(tile).laid  # read again under the lock: an Apply may have ended
        again = lay_folder(
            tile, folder, [[e] for e in laid], custom_scenery=custom_scenery, progress=progress
        )
    log.info("%s: %d of its %d replaced clouds laid again", tile.name, len(again), len(laid))
