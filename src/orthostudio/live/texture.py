# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""One texture as X-Plane will read it: the levels it will want, from imagery, in a sparse DDS.

The file keeps the whole layout of its size (``orthostudio.live.dds``: 4096 px at the mesh's zoom,
smaller or 8192 px for another zoom set, so X-Plane sees what AutoOrtho would serve) but only the
levels from the one built down are written; the larger ones are the neutral texture's, shared on
the disk, which X-Plane, reading by distance, never reaches. Level ``k`` of a texture of zoom ``z``
is imagery at zoom ``z - k``: ``(16 >> k)²`` pieces of 256 px, 1,024 at level -1 (8192 px).

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


def worker_ignores_ctrl_c() -> None:
    """Initializer of the encoding pools: Ctrl-C is for the main process, which winds the pool
    down."""
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
    never black. Without, they are a hole of a sparse file, which reads as zeros.
    """
    layout = dds.level_layout(file_top)
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
    (they are in the file already) over a clone of the neutral texture of its size; past level 4,
    the neutral clone."""
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


def encode_levels(img) -> list[bytes]:
    """BC1 blocks of ``img`` and of every smaller level down to 1 x 1."""
    from .imaging import encode_levels as encode

    return encode(img)


def build_job(job) -> tuple[str, int, int, int]:
    """Worker: (name, top level, pieces, parents, path[, base[, file top]]) -> (name, top level,
    from parent, blank). ``base``: the neutral texture the file starts from, of the file's size;
    ``file top``: the file's sharpest level (see ``write_sparse``)."""
    name, level, pieces, parents, path = job[:5]
    base = Path(job[5]) if len(job) > 5 and job[5] else None
    file_top = job[6] if len(job) > 6 else 0
    tex = parse_texture_name(name)
    img, from_parent, blank = assemble(tex, level, pieces, parents)
    write_sparse(Path(path), level, encode_levels(img), base=base, file_top=file_top)
    return name, level, from_parent, blank
