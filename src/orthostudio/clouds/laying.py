# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The clouds laid in a tile's folder, put back, and laid again after a build (``clouds.md`` 2.2,
2.3). What Apply does is computed one way (:func:`plan_folder`): the cloud mode shows it
(``state.view``) and Apply runs it (:func:`lay_folder`). What X-Plane shows changes one way
(:func:`show`), under the tile's lock: the folder made to show the clouds given, then the list
told which it shows. Apply gives it the clouds of the view, Restore none, a build of the tile the
clouds last laid (:func:`lay_again`).

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
import re
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
    CLOUDS_DIR,
    DIGEST_HEX,
    Stamp,
    TileClouds,
    clouds_digest,
    entry_patches,
    find_part,
    held_by_id,
    identity,
    is_own_folder,
    laid_fields,
    lays_not,
    part_path,
    reaching,
    read_stamp,
    read_tile,
    sidecar_path,
    stamped,
    texture_digest,
)
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_text, replace
from orthostudio.graph.digest import digest_bytes
from orthostudio.graph.store import Store, _pid_alive
from orthostudio.home import default_store_root
from orthostudio.imagery.grid import parse_texture_name
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import (
    PackEnv,
    _refuse_rewrite_under_running_xplane,
    read_manifest,
)
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
    """What laying ``entries`` in a folder does: the clouds it shows after (``laid``, in order),
    the textures made again with the clouds each shows (``work``), the clouds it cannot lay with
    why (``dropped``: ``tile.lays_not``'s, or ``parts`` for frozen parts not on this computer
    where a texture of theirs must be made again)."""

    laid: list[Mapping[str, Any]]
    work: list[tuple[str, list[Mapping[str, Any]]]]
    dropped: dict[str, str]


def plan_folder(
    tile: TileRef, shown: TileClouds, entries: Sequence[Mapping[str, Any]]
) -> FolderPlan:
    """The one computing of what laying ``entries`` (in order) in the folder ``shown`` does. A
    cloud to lay needs its images to stand and their frozen parts (``tile.lays_not``); one the
    folder shows as it is needs nothing to stay but its parts where its texture is made again."""
    held = held_by_id(shown)
    dropped: dict[str, str] = {}
    keep: list[Mapping[str, Any]] = []
    for e in entries:
        shows = e["id"] in held and laid_fields(held[e["id"]]) == laid_fields(e)
        why = None if shows else lays_not(e, tile, shown)
        if why is None:
            keep.append(e)
        else:
            dropped[e["id"]] = why
    while True:
        work = []
        for name in shown.names:
            mine = reaching(keep, name)
            want = clouds_digest(mine) if mine else None
            stamp = shown.stamps.get(name)
            if (stamp.clouds if stamp is not None else None) != want:
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
            dropped[e["id"]] = "parts"
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
    entries: Sequence[Mapping[str, Any]],
    *,
    custom_scenery: Path | None,
    since: int | None = None,
    untick: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> list[Mapping[str, Any]]:
    """The tile's folder made to show the clouds ``entries`` (:func:`lay_folder`), then the list
    told which it shows (``laid``), when that changed; ``untick`` every cloud of the list unticked
    (Restore). Refused while X-Plane runs and shows the tile, as a build is; a write refused
    leaves the list as it was. ``since``: the list's generation the action began on
    (``cloudlist.update``). Returns the clouds laid."""
    with folder_lock(tile):
        return _show(tile, folder, entries, custom_scenery, since, untick, progress)


def _show(
    tile: TileRef,
    folder: Path,
    entries: Sequence[Mapping[str, Any]],
    custom_scenery: Path | None,
    since: int | None = None,
    untick: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> list[Mapping[str, Any]]:
    """:func:`show`, under the tile's lock."""
    _not_under_xplane(tile, folder, custom_scenery)
    laid = lay_folder(tile, folder, entries, progress=progress)
    now = [dict(e) for e in laid]
    if untick or cloudlist.load(tile).laid != now:
        with cloudlist.update(tile, since=since) as cl:
            cl.laid = now
            if untick:
                cloudlist.untick_all(cl)
    return laid


def _not_under_xplane(tile: TileRef, folder: Path, custom_scenery: Path | None) -> None:
    """The build's own rule (``pack._refuse_rewrite_under_running_xplane``), in Apply's words."""
    env = cast(PackEnv, SimpleNamespace(custom_scenery=custom_scenery))
    try:
        _refuse_rewrite_under_running_xplane(env, tile, Path(folder))
    except OsxpError as exc:
        raise OsxpError(
            exc.code,
            context=exc.context,
            message=f"X-Plane is running and shows tile {tile.name}: its textures are not "
            "replaced while it runs. Nothing was changed.",
            remedy="Quit X-Plane, then apply again.",
        ) from exc


def lay_folder(
    tile: TileRef,
    folder: Path,
    entries: Sequence[Mapping[str, Any]],
    *,
    progress: Callable[[int, int], None] | None = None,
) -> list[Mapping[str, Any]]:
    """Make the folder show what :func:`plan_folder` says of ``entries``. All or nothing: every
    new texture is made first beside its own, then each is put in place, only over the very file
    read, the one before kept until all are; when one cannot be (X-Plane holding it, a build
    replacing it meanwhile), those put in place are put back, a build's own left as it is, and
    nothing has changed. The files no longer needed beside go. Called under the tile's lock
    (:func:`folder_lock`). Returns the clouds laid."""
    shown = read_tile(folder)
    if shown is None:
        raise OsxpError("CLOUD_TILE_NOT_READY", context={"tile": tile.name, "state": "not_built"})
    _sweep_leftovers(shown)
    plan = plan_folder(tile, shown, entries)
    made: list[tuple[str, Path]] = []
    try:
        for n, (name, mine) in enumerate(plan.work):
            if progress is not None:
                progress(n, len(plan.work))
            made.append((name, _make(tile, shown, name, mine)))
        _put_in_place(tile, shown, made)
    finally:
        for _name, path in made:
            path.unlink(missing_ok=True)
    _sweep(read_tile(folder) or shown)
    if progress is not None:
        progress(len(plan.work), len(plan.work))
    return plan.laid


_SIDECAR = re.compile(f"[0-9a-f]{{{DIGEST_HEX}}}")
"""A sidecar's name (``tile.sidecar_path``): the digest of the clouds it lists."""
_OURS = ".osxp-clouds-"
"""What the files laying makes beside a texture are named with (``<name>.dds.osxp-clouds-new-
<pid>``, ``...-before-<pid>``); one whose program is gone goes at a later laying."""


def _beside(path: Path, what: str) -> Path:
    return path.with_name(f"{path.name}{_OURS}{what}-{os.getpid()}")


def _place(src: Path, dest: Path) -> None:
    """``dest`` made the same file as ``src`` (a hard link, else a copy), in a folder that is
    there: a tile deleted meanwhile is never made again."""
    dest.unlink(missing_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _plain_path(tile: TileRef, shown: TileClouds, name: str) -> Path:
    """The texture ``name`` as the build made it: in place, kept beside, else the store's own
    (the kept one taken away); ``CLOUD_TEXTURE_LOST`` when none is."""
    stamp = shown.stamps.get(name)
    if stamp is None:
        return shown.texture_path(name)
    kept = shown.kept_path(name)
    for path in (kept, _stored(shown, name)):
        if path is not None and path.is_file() and texture_digest(path) == stamp.plain:
            return path
    raise OsxpError("CLOUD_TEXTURE_LOST", context={"tile": tile.name, "path": str(kept)})


def _stored(shown: TileClouds, name: str) -> Path | None:
    """The texture ``name`` in the store's artefact the tile's manifest names, when there."""
    with contextlib.suppress(Exception):
        entry = read_manifest(shown.folder).artefacts.get("textures")
        if entry is not None:
            with Store(default_store_root(), sweep=False) as store:
                info = store.info(entry.key)
            if info is not None:
                return Path(info.path) / "textures" / f"{name}.dds"
    return None


def _make(tile: TileRef, shown: TileClouds, name: str, mine: Sequence[Mapping[str, Any]]) -> Path:
    """The new texture ``name``, made beside it: laid with ``mine``, else the build's own; the
    build's own kept aside first, and the clouds it shows written beside."""
    texture, kept = shown.texture_path(name), shown.kept_path(name)
    plain = _plain_path(tile, shown, name)
    new = _beside(texture, "new")
    if not mine:  # the build's own again
        _place(plain, new)
        return new
    data = lay_texture(plain.read_bytes(), name, tile, mine)
    kept.parent.mkdir(exist_ok=True)  # the tile's folder itself is never made again
    if not _same_file(plain, kept):  # a build links its textures from its store: often it is
        aside = _beside(kept, "kept")  # (a rename onto the same file does nothing)
        _place(plain, aside)
        replace(aside, kept)
    side = sidecar_path(shown.folder, clouds_digest(mine))
    if not side.is_file():
        atomic_write_text(side, json.dumps([laid_fields(e) for e in mine], sort_keys=True))
    with open(new, "wb") as f:
        f.write(data)
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
            before = _beside(texture, "before")
            _place(texture, before)
            if identity(texture) != shown.files[name]:  # a build placed its own meanwhile
                before.unlink(missing_ok=True)
                raise OsxpError(
                    "CLOUD_TILE_NOT_READY", context={"tile": tile.name, "state": "in_build"}
                )
            try:
                replace(new, texture)
            except BaseException:
                before.unlink(missing_ok=True)
                raise
            done.append((texture, before, identity(texture)))
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
            before.unlink(missing_ok=True)


def _sweep(shown: TileClouds) -> None:
    """What lies beside the textures and no texture names goes: the build's own kept for a
    texture that shows no cloud (the texture is that same file), the clouds of no stamp."""
    aside = shown.folder / CLOUDS_DIR
    if not aside.is_dir():
        return
    named = {s.clouds for s in shown.stamps.values()}
    for kept in aside.glob("*.dds"):
        with contextlib.suppress(OSError):
            texture = shown.texture_path(kept.stem)
            if not texture.is_file() or read_stamp(texture) is None:
                kept.unlink()
    for side in aside.glob("*.json"):  # the sidecars only: another file there is not ours
        if _SIDECAR.fullmatch(side.stem) and side.stem not in named:
            side.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        aside.rmdir()  # when empty


def _sweep_leftovers(shown: TileClouds) -> None:
    """The files a laying cut short left beside the textures (:data:`_OURS`), whose program is
    gone: one of a program under way stays."""
    for folder in (shown.folder / "textures", shown.folder / CLOUDS_DIR):
        for left in folder.glob(f"*{_OURS}*") if folder.is_dir() else ():
            pid = left.name.rsplit("-", 1)[-1].split(".", 1)[0]
            if pid.isdigit() and int(pid) != os.getpid() and not _pid_alive(int(pid)):
                left.unlink(missing_ok=True)


def lay_again(tile: TileRef, folder: Path, *, custom_scenery: Path | None) -> None:
    """After a build of ``tile`` in ``folder``, its own: the clouds last laid laid again, those
    whose images still stand on the textures it placed. Nothing when none was."""
    if not cloudlist.load(tile).laid:
        return
    if not is_own_folder(tile, folder, custom_scenery=custom_scenery):
        return  # a build written elsewhere (a command line's own folder): not the tile's clouds
    with folder_lock(tile):
        laid = cloudlist.load(tile).laid  # read again under the lock: an Apply may have ended
        again = _show(tile, folder, laid, custom_scenery)
    log.info("%s: %d of its %d replaced clouds laid again", tile.name, len(again), len(laid))
