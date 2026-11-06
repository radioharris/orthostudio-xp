# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The clouds laid in a tile's folder, put back, and laid again after a build (``clouds.md`` 2.2,
2.3). What X-Plane shows of a tile's clouds changes one way (:func:`show`): the folder made to show
the clouds given, of them those whose images stand (:func:`lay_folder`), then the list told which
it shows (``CloudList.laid``), under the tile's lock. Apply gives it the clouds to show, Restore
none, and a build of the tile, once it has placed its textures, the clouds last laid
(:func:`lay_again`).

A texture is laid from the build's own: its first level read, the clouds laid over it by the
layer the previews are drawn with (``textures.clouds``), its levels made again as the build makes
them, and of each level only the blocks a cloud reaches encoded again, as the build encodes them;
every other block is the build's, byte for byte. The build's texture is kept beside, the same file
(``clouds/<name>.dds``), the laid one stamped with what it is made of (``tile.Stamp``), written
whole over the build's: a write cut short leaves either.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from orthostudio.clouds import cloudlist
from orthostudio.clouds.tile import (
    DIGEST_HEX,
    Stamp,
    TileClouds,
    clouds_digest,
    find_part,
    got_parts_dir,
    own_folder,
    patches_of,
    reaching,
    read_stamp,
    read_tile,
    stamped,
    stands_not,
    texture_digest,
)
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_link_or_copy, atomic_write_bytes
from orthostudio.graph.digest import digest_bytes
from orthostudio.imagery.grid import parse_texture_name
from orthostudio.model import TileRef
from orthostudio.pipeline.home import default_chunks_root
from orthostudio.textures import dds
from orthostudio.textures.bcdecode import decode_blocks
from orthostudio.textures.clouds import decode_part, lay, layer
from orthostudio.textures.encode import encode_blocks
from orthostudio.textures.mips import mip_chain

__all__ = ["folder_lock", "lay_again", "lay_folder", "lay_texture", "show"]

log = logging.getLogger(__name__)

_LOCKS: dict[str, threading.RLock] = {}
_GUARD = threading.Lock()


def folder_lock(tile: TileRef) -> threading.RLock:
    """The tile's textures written by one at a time in this program (Apply, Restore, a build's
    laying again), and read by none meanwhile (the map's picture)."""
    with _GUARD:
        return _LOCKS.setdefault(tile.name, threading.RLock())


def show(
    tile: TileRef,
    folder: Path,
    entries: Sequence[Mapping[str, Any]],
    *,
    since: int | None = None,
    untick: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> list[Mapping[str, Any]]:
    """The tile's folder made to show the clouds ``entries`` that stand, then the list told which
    it shows (``laid``); ``untick`` every cloud of the list unticked (Restore). A write refused
    (X-Plane holding a texture, on Windows) leaves the list as it was. ``since``: the list's
    generation the action began on (``cloudlist.update``). Returns the clouds laid."""
    with folder_lock(tile):
        parts = [got_parts_dir(default_chunks_root())]
        laid = lay_folder(tile, folder, entries, parts=parts, progress=progress)
        with cloudlist.update(tile, since=since) as cl:
            cl.laid = [dict(e) for e in laid]
            if untick:
                cloudlist.restore(cl)
        return laid


def lay_texture(
    data: bytes,
    name: str,
    tile: TileRef,
    entries: Sequence[Mapping[str, Any]],
    parts: Sequence[Path],
) -> bytes:
    """The texture ``name`` of ``tile`` as built (its DDS ``data``) with the clouds ``entries``
    laid in it, in their order, from their frozen parts found in ``parts``; stamped."""
    header, levels = dds.split_levels(data)
    t = parse_texture_name(name)
    first = decode_blocks(bytes(levels[0]), header.width, header.height, header.fmt)
    layers = []
    for e in entries:
        zl, digest = e["parts"][name]
        patch = patches_of(e, tile, [t], lambda _t, zl=zl: int(zl)).get(name)
        path = find_part(str(digest), parts)
        if path is None:
            raise OsxpError(
                "CLOUD_TEXTURE_LOST",
                context={"tile": tile.name, "path": f"{digest}.part"},
            )
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
    size = dds.BLOCK_BYTES[fmt]
    bh, bw = blocks.shape
    out = np.frombuffer(level, np.uint8).reshape(bh, bw, size).copy()
    if not blocks.any():
        return level
    rows, cols = np.nonzero(blocks)
    r0, r1, c0, c1 = int(rows.min()), int(rows.max()) + 1, int(cols.min()), int(cols.max()) + 1
    part = np.ascontiguousarray(image[r0 * 4 : r1 * 4, c0 * 4 : c1 * 4])
    made = np.frombuffer(encode_blocks(part, fmt), np.uint8).reshape(r1 - r0, c1 - c0, size)
    here = blocks[r0:r1, c0:c1]
    out[r0:r1, c0:c1][here] = made[here]
    return out.tobytes()


def lay_folder(
    tile: TileRef,
    folder: Path,
    entries: Sequence[Mapping[str, Any]],
    *,
    parts: Sequence[Path],
    progress: Callable[[int, int], None] | None = None,
) -> list[Mapping[str, Any]]:
    """Make every texture of ``folder`` show those of the clouds ``entries`` (in laying order)
    whose images stand on it (``tile.stands_not``), and no other: each texture they reach laid
    from the build's own, each other one the build's again. A texture showing these already is
    left as it is; the build's textures no longer needed aside go. Returns the clouds laid."""
    with folder_lock(tile):
        held = read_tile(folder)
        if held is None:
            raise OsxpError(
                "CLOUD_TILE_NOT_READY", context={"tile": tile.name, "state": "not_built"}
            )
        laid = [e for e in entries if stands_not(e, tile, held) is None]
        work = []
        for name in held.names:
            mine = reaching(laid, name)
            want = clouds_digest(mine) if mine else None
            stamp = held.stamps.get(name)
            if (stamp.clouds if stamp is not None else None) != want:
                work.append((name, mine))
        for n, (name, mine) in enumerate(work):
            if progress is not None:
                progress(n, len(work))
            _lay_one(tile, held, name, mine, parts)
        _sweep(held)
        if progress is not None:
            progress(len(work), len(work))
        return laid


def _lay_one(
    tile: TileRef,
    held: TileClouds,
    name: str,
    mine: Sequence[Mapping[str, Any]],
    parts: Sequence[Path],
) -> None:
    texture = held.textures_dir / f"{name}.dds"
    kept = held.plains_dir / f"{name}.dds"
    stamp = held.stamps.get(name)
    if stamp is not None and (not kept.is_file() or texture_digest(kept) != stamp.plain):
        raise OsxpError(
            "CLOUD_TEXTURE_LOST",
            context={"tile": tile.name, "path": str(kept)},
        )
    try:
        if not mine:  # the build's own again
            atomic_link_or_copy(kept, texture)
            return
        plain = (kept if stamp is not None else texture).read_bytes()
        laid = lay_texture(plain, name, tile, mine, parts)
        if stamp is None:  # the build's own kept aside, the same file, before it is replaced
            held.plains_dir.mkdir(exist_ok=True)
            atomic_link_or_copy(texture, kept)
        atomic_write_bytes(texture, laid)
    except PermissionError as exc:  # Windows: X-Plane holds the texture
        raise OsxpError(
            "XP_PACK_CONFLICT",
            context={
                "tile": tile.name,
                "pack": str(texture),
                "reason": f"{type(exc).__name__}: {exc}",
            },
            message=f"{texture} is in use and cannot be replaced ({exc}).",
            remedy="Quit X-Plane (or the tool holding the file), then apply again.",
        ) from exc


def _sweep(held: TileClouds) -> None:
    """The build's textures kept aside for textures that show no cloud now go: the texture is
    that same file."""
    if not held.plains_dir.is_dir():
        return
    for kept in held.plains_dir.glob("*.dds"):
        texture = held.textures_dir / kept.name
        try:
            if not texture.is_file() or read_stamp(texture) is None:
                kept.unlink()
        except OSError:
            log.warning("%s stays", kept, exc_info=True)
    with contextlib.suppress(OSError):
        held.plains_dir.rmdir()  # when empty


def lay_again(tile: TileRef, folder: Path, *, custom_scenery: Path | None) -> None:
    """After a build of ``tile`` in ``folder``, its own: the clouds last laid laid again, those
    whose images still stand on the textures it placed. Nothing when none was."""
    if not cloudlist.load(tile).laid:
        return
    if Path(folder).resolve() != own_folder(tile, custom_scenery=custom_scenery).resolve():
        return  # a build written elsewhere (a command line's own folder): not the tile's clouds
    with folder_lock(tile):
        laid = cloudlist.load(tile).laid  # read again under the lock: an Apply may have ended
        again = show(tile, folder, laid)
    log.info("%s: %d of its %d replaced clouds laid again", tile.name, len(again), len(laid))
