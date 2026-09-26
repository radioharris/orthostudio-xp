# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""One texture as X-Plane will read it: the levels it will want, from imagery, in a sparse DDS.

The file keeps the full 4096 layout of ``orthostudio.live.dds`` (so X-Plane sees what AutoOrtho
would serve) but only levels ``top`` to 12 are written; the larger ones are a hole of a sparse file,
which costs no disk and which X-Plane, reading by distance, never reaches. Level ``k`` of a texture
of zoom ``z`` is imagery at zoom ``z - k``: ``(16 >> k)²`` pieces of 256 px.

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
    return tex.zoom - level, tex.x >> level, tex.y >> level, 16 >> level


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
    path: Path, top_level: int, levels: Sequence[bytes], base: Path | None = None
) -> None:
    """Write levels ``top_level``..12 of ``path`` and replace it atomically.

    With ``base`` (the region's neutral texture, complete at every level), the file starts as a
    clone of it and only the levels given are written: the larger ones stay the base's, shared on
    the disk at no cost, so a level read sharper than it was built shows the neutral colour, never
    black. Without, they are a hole of a sparse file, which reads as zeros.
    """
    layout = dds.mip_layout()
    if len(levels) != len(layout) - top_level:
        raise ValueError(f"{len(levels)} levels given for top level {top_level}")
    for (offset, length), blob in zip(layout[top_level:], levels, strict=False):
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
            os.pwrite(fd, b"".join(levels), layout[top_level][0])
        finally:
            os.close(fd)
    else:
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o644)
        try:
            os.ftruncate(fd, dds.file_size())
            os.pwrite(fd, dds.header(), 0)
            os.pwrite(fd, b"".join(levels), layout[top_level][0])
            punch_hole(fd, dds.HEADER_SIZE, layout[top_level][0])
        finally:
            os.close(fd)
    os.rename(tmp, path)  # atomic: X-Plane opens either the old file or the new one


def downgrade(path: Path, level: int, neutral: Path) -> None:
    """Make a built texture coarser in place of downloading: keep its levels ``level``..12 (they are
    in the file already) over a clone of the neutral texture; past level 4, the neutral clone."""
    layout = dds.mip_layout()
    if level > 4:
        from .clone import replace_with_clone

        replace_with_clone(str(neutral), str(path))
        return
    start = layout[level][0]
    with open(path, "rb") as f:
        f.seek(start)
        tail = f.read()
    levels, pos = [], 0
    for _, length in layout[level:]:
        levels.append(tail[pos : pos + length])
        pos += length
    write_sparse(path, level, levels, base=neutral if neutral.exists() else None)


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
    """Worker: (name, top level, pieces, parents, path[, base]) -> (name, top level, from parent,
    blank). ``base``: the neutral texture the file starts from (see ``write_sparse``)."""
    name, level, pieces, parents, path = job[:5]
    base = Path(job[5]) if len(job) > 5 and job[5] else None
    tex = parse_texture_name(name)
    img, from_parent, blank = assemble(tex, level, pieces, parents)
    write_sparse(Path(path), level, encode_levels(img), base=base)
    return name, level, from_parent, blank
