# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""One texture as X-Plane will read it: the levels it will want, from imagery, in a sparse DDS.

The file keeps the whole layout of its size (``orthostudio.live.dds``: 4096 px at the mesh's zoom,
smaller or 8192 px for another zoom set, so X-Plane sees what AutoOrtho would serve) but only the
levels from the one built down are written; the larger ones are the neutral texture's, shared on
the disk, which X-Plane, reading by distance, never reaches. Level ``k`` of a texture of zoom ``z``
is imagery at zoom ``z - k``: ``(16 >> k)²`` pieces of 256 px, 1,024 at level -1 (8192 px).

An 8192 px file has one level more written: the one above the level built holds a copy of it, twice
as wide (:func:`double_bc1`). X-Plane counts a file's levels from its top (an fs_usage trace in
flight, 2026-09-28), so it reads each level of an 8192 px file one ring before the photos of that
level are built: it finds that copy there, the same image, never the neutral colour.

The pieces are assembled, mip-mapped in linear light and encoded to BC1 by
``orthostudio.live.imaging``, imported where it is used so that the layout functions here need
nothing beyond the standard library.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import signal
import struct
from collections.abc import Sequence
from pathlib import Path

from . import dds
from .cache import Key
from .geo import Texture, parse_texture_name

CHUNK = 256
BLOCK = 4096
# <sys/fcntl.h>; argument: struct fpunchhole {uint flags; uint reserved; off_t offset; off_t length}
F_PUNCHHOLE = 99
MAX_PARENT_DEPTH = 4
MISSING_RGB = (72, 80, 64)  # a dull green-grey where nobody has imagery
# The test of the 8192 px (settings tint_8192): that level only, washed with pink, so that a pilot
# sees where X-Plane shows it; the 4096 px and smaller levels are the photos as they are.
TINT_RGB = (255, 64, 200)
TINT_ALPHA = 0.35


def worker_ignores_ctrl_c() -> None:
    """Initializer of the encoding pools: Ctrl-C is for the main process, which winds the pool
    down. And a lower priority: X-Plane's threads go first, the encoders take the cores left (a
    user saw stutters in flight while Live built, 2026-09-28)."""
    with contextlib.suppress(OSError):
        os.nice(10)
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def chunk_grid(tex: Texture, level: int) -> tuple[int, int, int, int]:
    """(zoom, x0, y0, n): the n x n imagery pieces at ``zoom`` whose pixels are level ``level``."""
    if level >= 0:
        return tex.zoom - level, tex.x >> level, tex.y >> level, 16 >> level
    up = -level  # above the mesh's zoom: 8192 px
    return tex.zoom + up, tex.x << up, tex.y << up, 16 << up


def pieces_of(level: int) -> int:
    """The imagery pieces level ``level`` of a texture takes: 256 at 4096 px, 1,024 at 8192."""
    n = 16 >> level if level >= 0 else 16 << -level
    return n * n


def piece_keys(tex: Texture, level: int) -> list[Key]:
    z, x0, y0, n = chunk_grid(tex, level)
    return [(z, x0 + i, y0 + j) for j in range(n) for i in range(n)]


def parent_keys(key: Key, depth: int = MAX_PARENT_DEPTH) -> list[Key]:
    z, x, y = key
    return [(z - d, x >> d, y >> d) for d in range(1, depth + 1) if z - d >= 0]


def punch_hole(fd: int, start: int, end: int) -> bool:
    """Give back the disk under [start, end), whole 4 KB blocks only. False where unsupported.

    APFS allocates the gap when a file is extended (by ``ftruncate`` or by writing past its end), at
    no I/O cost; only ``F_PUNCHHOLE`` makes it a hole again (measured: 0.3 ms and 47 KB a
    placeholder, against 11 MB).
    """
    start = (start + BLOCK - 1) // BLOCK * BLOCK
    end = end // BLOCK * BLOCK
    if end <= start:
        return True
    try:
        fcntl.fcntl(fd, F_PUNCHHOLE, struct.pack("=IIqq", 0, 0, start, end - start))
        return True
    except OSError:
        return False


def double_bc1(blob: bytes, side: int) -> bytes:
    """The BC1 blocks of a level ``side`` px wide, as a level twice as wide: each pixel four times,
    the nearest, straight from the blocks (each block's two colours kept, its sixteen indices spread
    over four blocks), nothing decoded or encoded. The same image, no sharper: what X-Plane would
    show of the level itself."""
    import numpy as np

    n = side // 4
    words = np.frombuffer(blob, dtype="<u4").reshape(n, n, 2)  # per block: colours, indices
    shifts = (2 * np.arange(16, dtype=np.uint32)).reshape(4, 4)  # pixel (x, y): bits 2(4y + x)
    grid = (words[..., 1, None, None] >> shifts) & 3  # each block's indices, 4 x 4
    out = np.empty((n, 2, n, 2, 2), dtype="<u4")  # block row, its half, block column, its half
    out[..., 0] = words[:, None, :, None, 0]
    for qy in (0, 1):
        for qx in (0, 1):
            quarter = grid[:, :, 2 * qy : 2 * qy + 2, 2 * qx : 2 * qx + 2]
            spread = quarter.repeat(2, axis=2).repeat(2, axis=3)
            out[:, qy, :, qx, 1] = (spread << shifts).sum(axis=(2, 3), dtype=np.uint32)
    return out.reshape(2 * n, 2 * n, 2).tobytes()


def write_sparse(
    path: Path,
    top_level: int,
    levels: Sequence[bytes],
    base: Path | None = None,
    file_top: int = 0,
) -> None:
    """Write the levels of ``path`` from ``top_level`` down to 1 x 1, and replace it atomically.

    ``file_top`` is the file's own sharpest level (0: a 4096 px file, 1: 2048, -1: 8192). With
    ``base`` (the region's neutral texture of that size, complete at every level), the file starts
    as a clone of it and only the levels given are written: the larger ones stay the base's, shared
    on the disk at no cost, so a level read sharper than it was built shows the neutral colour,
    never black. Without, they are a hole of a sparse file, which reads as zeros. In an 8192 px
    file, the level above ``top_level`` is written too, as its copy (the module's docstring).
    """
    layout = dds.level_layout(file_top)
    if file_top < 0 and top_level > file_top and levels:
        levels = [double_bc1(levels[0], dds.size_of(top_level)), *levels]
        top_level -= 1
    first = top_level - file_top
    if first < 0 or len(levels) != len(layout) - first:
        raise ValueError(f"{len(levels)} levels given for level {top_level} of a top {file_top}")
    for (offset, length), blob in zip(layout[first:], levels, strict=False):
        if len(blob) != length:
            raise ValueError(f"level at {offset}: {len(blob)} bytes, expected {length}")
    tmp = path.with_name(f".live-tmp-{os.getpid()}-{path.name}")
    if base is not None:
        from .clone import clone

        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        clone(str(base), str(tmp))
        fd = os.open(tmp, os.O_WRONLY)
        try:
            os.pwrite(fd, b"".join(levels), layout[first][0])
        finally:
            os.close(fd)
    else:
        size = dds.size_of(file_top)
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o644)
        try:
            os.ftruncate(fd, dds.file_size(size))
            os.pwrite(fd, dds.header(size), 0)
            os.pwrite(fd, b"".join(levels), layout[first][0])
            punch_hole(fd, dds.HEADER_SIZE, layout[first][0])
        finally:
            os.close(fd)
    os.rename(tmp, path)  # atomic: X-Plane opens either the old file or the new one


def _levels_from(path: Path, level: int, file_top: int) -> list[bytes]:
    """Levels ``level`` down to 1 x 1 of a file whose sharpest level is ``file_top``."""
    layout = dds.level_layout(file_top)
    with open(path, "rb") as f:
        f.seek(layout[level - file_top][0])
        tail = f.read()
    levels, pos = [], 0
    for _, length in layout[level - file_top :]:
        levels.append(tail[pos : pos + length])
        pos += length
    return levels


def downgrade(path: Path, level: int, neutral: Path, file_top: int = 0) -> None:
    """Make a built texture coarser in place of downloading: keep its levels from ``level`` down
    (they are in the file already) over a clone of the neutral texture of its size, the file's
    size unchanged; past level 4, the neutral clone."""
    if level > 4:
        from .clone import replace_with_clone

        replace_with_clone(str(neutral), str(path))
        return
    levels = _levels_from(path, level, file_top)
    write_sparse(path, level, levels, base=neutral if neutral.exists() else None, file_top=file_top)


def resize(path: Path, built: int, old_top: int, new_top: int, neutral: Path) -> int:
    """A built texture in a file of another size, for another zoom: its levels from ``built`` down
    kept, those sharper than the new file's own dropped, nothing downloaded. Returns the level it
    is built to now."""
    keep = max(built, new_top)
    levels = _levels_from(path, keep, old_top)
    write_sparse(path, keep, levels, base=neutral, file_top=new_top)
    return keep


def _piece_from_parent(parent_body: bytes, depth: int, key: Key):
    """The part of a parent piece ``depth`` levels up that covers ``key``, scaled back to 256 px."""
    import numpy as np
    from PIL import Image

    from .imaging import decode_piece

    _, x, y = key
    rgb = decode_piece(parent_body)
    scale = 2**depth
    side = CHUNK // scale
    x0 = (x - (x >> depth) * scale) * side
    y0 = (y - (y >> depth) * scale) * side
    part = Image.fromarray(rgb[y0 : y0 + side, x0 : x0 + side])
    return np.asarray(part.resize((CHUNK, CHUNK), Image.Resampling.BICUBIC))


def assemble(
    tex: Texture,
    level: int,
    pieces: dict[Key, bytes | None],
    parents: dict[Key, bytes | None],
):
    """(RGB image of level ``level``, pieces taken from a parent, pieces left blank)."""
    import numpy as np

    from .imaging import decode_piece

    z, x0, y0, n = chunk_grid(tex, level)
    img = np.empty((n * CHUNK, n * CHUNK, 3), dtype=np.uint8)
    from_parent = blank = 0
    for j in range(n):
        for i in range(n):
            key = (z, x0 + i, y0 + j)
            rgb = None
            body = pieces.get(key)
            if body is not None:
                try:
                    rgb = decode_piece(body)
                except Exception:  # a corrupt piece: as if missing
                    rgb = None
            if rgb is None:
                for depth, pk in enumerate(parent_keys(key), start=1):
                    pb = parents.get(pk)
                    if pb is None:
                        continue
                    try:
                        rgb = _piece_from_parent(pb, depth, key)
                        from_parent += 1
                        break
                    except Exception:
                        continue
            if rgb is None:
                rgb = MISSING_RGB
                blank += 1
            img[j * CHUNK : (j + 1) * CHUNK, i * CHUNK : (i + 1) * CHUNK] = rgb
    return img, from_parent, blank


def tinted(img):
    """``img`` washed with the test's pink (:data:`TINT_RGB`), its photo still seen under it."""
    import numpy as np

    pink = np.array(TINT_RGB, dtype=np.float32)
    return (img.astype(np.float32) * (1.0 - TINT_ALPHA) + pink * TINT_ALPHA).astype(np.uint8)


def encode_levels(img, tint_top: bool = False) -> list[bytes]:
    """BC1 blocks of ``img`` and of every smaller level down to 1 x 1; ``tint_top``: ``img``'s own
    level pink, the smaller ones made from it untinted before."""
    from .imaging import encode_bc1, mip_chain

    levels = mip_chain(img)
    if tint_top:
        levels[0] = tinted(levels[0])
    return [encode_bc1(level) for level in levels]


def build_job(job) -> tuple[str, int, int, int]:
    """Worker: (name, top level, pieces, parents, path[, base[, file top[, tint]]]) -> (name, top
    level, from parent, blank). ``base``: the neutral texture the file starts from, of the file's
    size; ``file top``: the file's sharpest level (see ``write_sparse``); ``tint``: the test of the
    8192 px, their level pink (:func:`encode_levels`)."""
    name, level, pieces, parents, path = job[:5]
    base = Path(job[5]) if len(job) > 5 and job[5] else None
    file_top = job[6] if len(job) > 6 else 0
    tint = bool(job[7]) if len(job) > 7 else False
    tex = parse_texture_name(name)
    img, from_parent, blank = assemble(tex, level, pieces, parents)
    levels = encode_levels(img, tint_top=tint and level < 0)
    write_sparse(Path(path), level, levels, base=base, file_top=file_top)
    return name, level, from_parent, blank
