# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""One texture as X-Plane will read it: the levels it will want, from imagery, in a sparse DDS.

The file keeps the whole layout of its size (``orthostudio.live.dds``: 4096 px at the mesh's zoom,
smaller or 8192 px for another zoom set, so X-Plane sees what AutoOrtho would serve) but only the
levels from the one built down are written; the larger ones are the neutral texture's, shared on
the disk, which X-Plane, reading by distance, never reaches. Level ``k`` of a texture of zoom ``z``
is imagery at zoom ``z - k``: ``(16 >> k)²`` pieces of 256 px, 1,024 at level -1 (8192 px).

X-Plane counts a file's levels from its top (an fs_usage trace in flight, 2026-09-28), so it reads
each level of an 8192 px file one ring before the photos of that level are built at a 4096 px
file's distances. Nearer than 38 km the streamer builds its 4096 px and 2048 px levels that ring
earlier (``Streamer._level``), round the camera and, widened by the camera's reach, round the
aircraft and its path. The far levels of an 8192 px file, though, are read at 72 km and at load
before their photos come: so a file built from the 512 px or the 256 px level has the level above
it written too, as its copy, twice as wide (:func:`double_bc1`, :func:`needs_copy`), the same
image, never the neutral colour.
And the 8192 px level of a texture about to get its photos first gets the copy of its 4096 px
level (:func:`copy_up`): photos coming late show the ortho as at ZL16, never the neutral colour.
A copy takes four times the disk of the level it copies, which is why there are no others (every
level copied took 27.7 GB for a five-minute flight near Lyon, 2026-09-28).

The pieces are assembled, mip-mapped in linear light and encoded to BC1 by
``orthostudio.live.imaging``, imported where it is used so that the layout functions here need
nothing beyond the standard library.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from . import dds
from .cache import Key
from .clone import NO_CLONE
from .filesystem import BINARY, extend, make_sparse, punch_hole, pwrite, replace
from .geo import Texture, parse_texture_name

__all__ = ["punch_hole", "write_sparse"]

CHUNK = 256
MAX_PARENT_DEPTH = 4
MISSING_RGB = (72, 80, 64)  # a dull green-grey where nobody has imagery
# The test of the 8192 px (settings tint_8192): that level only, washed with pink, so that a pilot
# sees where X-Plane shows it; the 4096 px and smaller levels are the photos as they are.
TINT_RGB = (255, 64, 200)
TINT_ALPHA = 0.35
COPY_FROM = 3  # the levels whose copy goes above them in an 8192 px file: 512 px and smaller


def worker_ignores_ctrl_c() -> None:
    """Initializer of the encoding pools: Ctrl-C is for the main process, which winds the pool
    down. And a lower priority: X-Plane's threads go first, the encoders take the cores left (a
    user saw stutters in flight while Live built, 2026-09-28). Windows has no nice(): its class
    below normal there, as Windows' Task Manager names it."""
    with contextlib.suppress(OSError, AttributeError):
        if sys.platform == "win32":
            kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL
        else:
            os.nice(10)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if hasattr(signal, "SIGBREAK"):  # Windows: Live's Ctrl-Break reaches its whole group
        signal.signal(signal.SIGBREAK, signal.SIG_IGN)


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


def needs_copy(level: int, file_top: int) -> bool:
    """Whether a file whose sharpest level is ``file_top``, built from ``level``, holds the copy of
    that level above it: an 8192 px file built from a far level (the module's docstring)."""
    return file_top < 0 and level >= COPY_FROM


def write_sparse(
    path: Path,
    top_level: int,
    levels: Sequence[bytes],
    base: Path | None = None,
    file_top: int = 0,
    copy_above: bool = False,
    keep_from: int | None = None,
) -> None:
    """Write the levels of ``path`` from ``top_level`` down to 1 x 1, and replace it atomically.

    ``file_top`` is the file's own sharpest level (0: a 4096 px file, 1: 2048, -1: 8192). With
    ``base`` (the region's neutral texture of that size, complete at every level), the file starts
    as a clone of it and only the levels given are written: the larger ones stay the base's, shared
    on the disk at no cost, so a level read sharper than it was built shows the neutral colour,
    never black. Without, they are a hole of a sparse file, which reads as zeros. ``copy_above``:
    the level above ``top_level`` written too, as its copy (the module's docstring).

    ``keep_from``: the level ``path`` is built to already. A sharper build starts from a clone of
    ``path`` itself and writes only the levels above that one: those from ``keep_from`` down stay
    the file's, built from their own photos, not written again (a sharper build rewrote them all,
    the most of Live's disk writes after the 8192 px levels themselves, 2026-09-28).
    """
    layout = dds.level_layout(file_top)
    if copy_above and top_level > file_top and levels:
        levels = [double_bc1(levels[0], dds.size_of(top_level)), *levels]
        top_level -= 1
    first = top_level - file_top
    if first < 0 or len(levels) != len(layout) - first:
        raise ValueError(f"{len(levels)} levels given for level {top_level} of a top {file_top}")
    for (offset, length), blob in zip(layout[first:], levels, strict=False):
        if len(blob) != length:
            raise ValueError(f"level at {offset}: {len(blob)} bytes, expected {length}")
    tmp = path.with_name(f".live-tmp-{os.getpid()}-{path.name}")
    kept = keep_from is not None and top_level < keep_from and _built_file(path, file_top)
    source = path if kept else base
    if source is not None and _clone_into(source, tmp):
        written = levels[: keep_from - top_level] if kept else levels
        fd = os.open(tmp, os.O_WRONLY | BINARY)
        try:
            pwrite(fd, b"".join(written), layout[first][0])
        finally:
            os.close(fd)
    else:  # nothing to start from, or a disk that clones nothing: the levels given, then a hole
        size = dds.size_of(file_top)
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | BINARY, 0o644)
        try:
            make_sparse(fd)
            extend(fd, dds.file_size(size))
            pwrite(fd, dds.header(size), 0)
            pwrite(fd, b"".join(levels), layout[first][0])
            punch_hole(fd, dds.HEADER_SIZE, layout[first][0])
        finally:
            os.close(fd)
    replace(tmp, path)  # atomic: X-Plane opens either the old file or the new one


_UNCLONED: set[int] = set()  # disks found to clone nothing, by device: not tried again


def _clone_into(source: Path, tmp: Path) -> bool:
    """``tmp`` made a clone of ``source``; False, nothing made, where the disk clones nothing
    (NTFS, ext4: the caller writes a sparse file instead, :mod:`orthostudio.live.filesystem`)."""
    from .clone import clone

    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp)
    device = os.stat(source).st_dev
    if device in _UNCLONED:
        return False
    try:
        clone(str(source), str(tmp))
        return True
    except OSError as e:
        if e.errno not in NO_CLONE:
            raise
        _UNCLONED.add(device)
        return False


def _built_file(path: Path, file_top: int) -> bool:
    """Whether ``path`` is a texture file of the size ``file_top`` gives, to build on."""
    try:
        return dds.read_top(path) == file_top
    except (OSError, ValueError):
        return False


def _file_top(path: Path, expected: int) -> int:
    """The sharpest level ``path`` has, read from its header: a texture not yet set to a new zoom
    has another than the pack's layout (region.set_zooms); ``expected`` where it cannot be read."""
    try:
        return dds.read_top(path)
    except (OSError, ValueError):
        return expected


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


def to_neutral(path: Path, neutral: Path) -> None:
    """``path`` made a name of the neutral texture again, atomically: a hard link, else (a file
    with as many names as its disk allows, a disk without links) a clone or a copy."""
    from .clone import clone_or_copy

    tmp = path.with_name(f".live-tmp-{os.getpid()}-{path.name}")
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp)
    try:
        os.link(neutral, tmp)
    except OSError as e:
        if e.errno == errno.ENOENT:
            raise
        clone_or_copy(str(neutral), str(tmp))
    replace(tmp, path)


def downgrade(path: Path, level: int, neutral: Path, file_top: int = 0) -> None:
    """Make a built texture coarser in place of downloading: keep its levels from ``level`` down
    (they are in the file already) over a clone of the neutral texture of its size, the file's
    size unchanged; past level 4, the neutral texture itself (:func:`to_neutral`)."""
    if level > 4:
        to_neutral(path, neutral)
        return
    actual = _file_top(path, file_top)  # set to the zoom here if it was not yet
    level = max(level, file_top, actual)  # never sharper than the new file, nor than the old one
    levels = _levels_from(path, level, actual)
    write_sparse(
        path,
        level,
        levels,
        base=neutral if neutral.exists() else None,
        file_top=file_top,
        copy_above=needs_copy(level, file_top),
    )


def copy_up(path: Path, neutral: Path, file_top: int = -1) -> None:
    """The 8192 px level of a texture built from its 4096 px one made the copy of that one, the
    levels kept, nothing downloaded: until its photos come (the module's docstring)."""
    levels = _levels_from(path, 0, _file_top(path, file_top))  # a file not yet set to the zoom too
    write_sparse(path, 0, levels, base=neutral, file_top=file_top, copy_above=True, keep_from=0)


def resize(path: Path, built: int, old_top: int, new_top: int, neutral: Path) -> int:
    """A built texture in a file of another size, for another zoom: its levels from ``built`` down
    kept, those sharper than the new file's own dropped, nothing downloaded. Returns the level it
    is built to now."""
    keep = max(built, new_top)
    levels = _levels_from(path, keep, old_top)
    write_sparse(
        path, keep, levels, base=neutral, file_top=new_top, copy_above=needs_copy(keep, new_top)
    )
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


def encode_levels(img, tint_top: bool = False, look=None) -> list[bytes]:
    """BC1 blocks of ``img`` and of every smaller level down to 1 x 1; ``tint_top``: ``img``'s own
    level pink, the smaller ones made from it untinted before; ``look``: the season's colours
    (``orthostudio.live.look``, five values), given to the blocks of every level."""
    from .imaging import encode_bc1, mip_chain
    from .look import look_blocks

    levels = mip_chain(img)
    if tint_top:
        levels[0] = tinted(levels[0])
    return [look_blocks(encode_bc1(level), look) for level in levels]


def build_job(job) -> tuple[str, int, int, int]:
    """Worker: (name, top level, pieces, parents, path[, base[, file top[, tint[, built[, look]]]]])
    -> (name, top level, from parent, blank). ``base``: the neutral texture the file starts from,
    of the file's size; ``file top``: the file's sharpest level (see ``write_sparse``); ``tint``:
    the test of the 8192 px, their level pink (:func:`encode_levels`); ``built``: the level the
    file holds already, kept below a sharper build (``write_sparse``'s ``keep_from``); ``look``:
    the season's five values, or None for the photos as delivered."""
    name, level, pieces, parents, path = job[:5]
    base = Path(job[5]) if len(job) > 5 and job[5] else None
    file_top = job[6] if len(job) > 6 else 0
    tint = bool(job[7]) if len(job) > 7 else False
    built = job[8] if len(job) > 8 else None
    look = job[9] if len(job) > 9 else None
    tex = parse_texture_name(name)
    img, from_parent, blank = assemble(tex, level, pieces, parents)
    levels = encode_levels(img, tint_top=tint and level < 0, look=look)
    write_sparse(
        Path(path),
        level,
        levels,
        base=base,
        file_top=file_top,
        copy_above=needs_copy(level, file_top),
        keep_from=built,
    )
    return name, level, from_parent, blank
