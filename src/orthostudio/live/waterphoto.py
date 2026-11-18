# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The water of Live's own mesh: a water photo for the water that fades, one grey border for the
rivers and lakes that do not.

The water overlays of Live's mesh fade the photo into X-Plane's water near the shore, through a
mask of each texture. Given to X-Plane as their ``BORDER_TEX``, a PNG beside the photo, that mask
came after the photo: for 2 to 2.5 s at a cold start, 2 to 5 s in Live each time a new photo
appeared, the water showed large light triangles (the user's Rhône camera, 2026-11-17). Measured
there, X-Plane cold: the mask in the photo's alpha (Build's way), right from 0 s; a separate mask
PNG, 2 s; the same mask as a DDS, 2.5 s, so the format does not help; a water photo of its own,
the mask in its alpha, given as the overlay's ``BASE_TEX`` with no border, right from 0 s at 4096,
2048, 1024 and 512 px. Close at the bank, 512 and 1024 px left a straight line, 2048 was clean; at
ZL17/19 a water photo of 2048 px (3.3 m a pixel on the ZL16 grid's textures) looks the same as one
half the land's side: 2048 px at most, whatever the zoom.

So each texture of Live's mesh whose water fades has its water photo ``<texture>_water.dds``
beside it in the pack's ``textures``, its alpha the mask, and its water terrain names it instead
of the photo and the mask (``orthostudio.live.names``). Rivers and lakes fade so only when the
pilot asks (``LiveSettings.rivers_fade``); otherwise they are drawn as XPME and AutoOrtho draw
them, one uniform grey as their border, 150 (:data:`EVEN_BORDER`: in X-Plane the same look as
XPME's ``ratio_water`` 0.25), and no mask. The sea and lakes over 200 km² keep their masks either
way.

Measured with a prototype at Stockholm, every water body masked (6,111 water photos in a
ten-minute flight, the worst case): the same frame rate (38 against 37), Live's queue as often
empty (92 %), Live's processor +20 %, its writes +43 % (1.70 to 2.43 GB), X-Plane's memory with
its graphics +3 GB; a 2048 px water photo encoded in 43 ms against 291 ms for the 4096 px land
photo (2026-11-17).
"""

from __future__ import annotations

import contextlib
import functools
import io
import os
import struct
from pathlib import Path

SUFFIX = "_water.dds"
SHARPEST = 1
"""A water photo's sharpest level, counted as a texture's: 2048 px at most (the module's
docstring)."""
HEADER_SIZE = 128
BLOCK_BYTES = 16  # BC3: 8 bytes of alpha (as BC4), then 8 of colour (as BC1 in four-colour mode)
EVEN_BORDER = "osxp_rivers_even.png"
"""The border of rivers and lakes that do not fade: uniform grey :data:`EVEN_GREY`, Live's own name,
which no region uses."""
EVEN_GREY = 150
NO_BORDER = "osxp_rivers_none.png"
"""The border of rivers and lakes that fade but have no mask: all 0, X-Plane's water alone, as
before the water photos."""
BORDER_SIDE = 16


def water_name(name: str) -> str:
    """The water photo of a texture (``23264_33856_BI16.dds``: ``23264_33856_BI16_water.dds``)."""
    return name[: -len(".dds")] + SUFFIX


def is_water_name(name: str) -> bool:
    """Whether a file of the pack's ``textures`` is a water photo, not a texture: a texture's name
    with a suffix, which ``geo.parse_texture_name`` reads as the texture's."""
    return name.endswith(SUFFIX)


@functools.cache
def border_png(grey: int) -> bytes:
    """A small uniform grey PNG (8-bit grey), for a water terrain's ``BORDER_TEX``."""
    from PIL import Image

    out = io.BytesIO()
    Image.new("L", (BORDER_SIDE, BORDER_SIDE), grey).save(out, format="PNG")
    return out.getvalue()


def borders() -> dict[str, bytes]:
    """The two borders of rivers and lakes, by their file's name."""
    return {EVEN_BORDER: border_png(EVEN_GREY), NO_BORDER: border_png(0)}


def water_top(file_top: int) -> int:
    """The sharpest level of the water photo of a texture whose file's is ``file_top``, counted as
    the texture's: half the land's side, 2048 px at most (:data:`SHARPEST`): 2048 px for a 4096 or
    an 8192 px land file, 1024 for a 2048 px one."""
    return max(SHARPEST, file_top + 1)


def first_level(level: int, file_top: int) -> int:
    """The sharpest level a water photo holds for a land built from ``level`` in a file whose
    sharpest is ``file_top``: one coarser than the land's, where X-Plane reads it. X-Plane counts
    each file's levels from its top (``orthostudio.live.texture``), and the water photo's top is one
    level below the land's: so where it reads the land's level ``level``, it reads the water's
    ``level + 1``. Never sharper than the water photo's own top."""
    return max(level + 1, water_top(file_top))


def layout(top: int) -> list[tuple[int, int]]:
    """[(offset, length)] of every level of a water photo whose sharpest level is ``top``: a square
    BC3 DDS down to 1 x 1, entry k level ``top + k``."""
    from .dds import size_of

    out, offset, width = [], HEADER_SIZE, size_of(top)
    while width >= 1:
        length = max(1, width // 4) ** 2 * BLOCK_BYTES
        out.append((offset, length))
        offset += length
        width //= 2
    return out


def file_size(top: int) -> int:
    offset, length = layout(top)[-1]
    return offset + length


def header(top: int) -> bytes:
    """The 128-byte DDS header of a BC3 (DXT5) water photo with every level down to 1 x 1."""
    from .dds import size_of

    side = size_of(top)
    # CAPS HEIGHT WIDTH PIXELFORMAT MIPMAPCOUNT LINEARSIZE, as orthostudio.live.dds.header
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000
    linear_size = max(1, side * side >> 4) * BLOCK_BYTES
    caps = 0x1000 | 0x400000  # TEXTURE | MIPMAP
    out = struct.pack(
        "<4s7I44s2I4s5I5I",
        b"DDS ", 124, flags, side, side, linear_size, 0, len(layout(top)),
        b"\0" * 44, 32, 0x4, b"DXT5", 0, 0, 0, 0, 0, caps, 0, 0, 0, 0,
    )  # fmt: skip
    assert len(out) == HEADER_SIZE
    return out


def _write(path: Path, top: int, index: int, blobs: list[bytes]) -> None:
    """``path`` written whole under a name of its own, then renamed over it: X-Plane opens either
    the old file or the new one. Its levels from entry ``index`` of :func:`layout` are ``blobs``;
    the larger ones are a hole of a sparse file, which reads as zeros, alpha 0: X-Plane's water
    alone, as the placeholder (:func:`write_placeholder`)."""
    from .filesystem import BINARY, extend, make_sparse, punch_hole, pwrite, replace

    lay = layout(top)
    if len(blobs) != len(lay) - index or any(
        len(blob) != length for (_, length), blob in zip(lay[index:], blobs, strict=False)
    ):
        raise ValueError(f"{len(blobs)} levels given from entry {index} of a top {top}")
    end = file_size(top)
    start = lay[index][0] if blobs else end
    tmp = path.with_name(f".live-tmp-{os.getpid()}-{path.name}")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | BINARY, 0o644)
    try:
        make_sparse(fd)
        extend(fd, end)
        pwrite(fd, header(top), 0)
        if blobs:
            pwrite(fd, b"".join(blobs), start)
        punch_hole(fd, HEADER_SIZE, start)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    os.close(fd)
    replace(tmp, path)


def write_placeholder(path: Path, top: int) -> None:
    """A water photo with no photo in it: its header, then a hole, alpha 0 at every level, which
    shows X-Plane's water alone. What a water photo is until its land is built, as the neutral
    texture is for the land (``orthostudio.live.names.WaterPool`` links its names to one)."""
    _write(path, top, len(layout(top)), [])


def alpha_levels(mask, side: int) -> list:
    """The alpha of each level of a water photo from ``side`` px down to 1 x 1: the mask resized
    bilinear to ``side``, each smaller level the average of four texels of the one above."""
    import numpy as np
    from PIL import Image

    a = mask
    if a.shape != (side, side):
        a = np.asarray(Image.fromarray(mask).resize((side, side), Image.Resampling.BILINEAR))
    out = [a]
    while out[-1].shape[0] > 1:
        x = out[-1].astype(np.uint16)
        half = (x[0::2, 0::2] + x[1::2, 0::2] + x[0::2, 1::2] + x[1::2, 1::2] + 2) // 4
        out.append(half.astype(np.uint8))
    return out


def alpha_blocks(alpha):
    """The alpha halves of the BC3 blocks of an alpha level, (blocks, 8) bytes, by Intel's ISPC
    compressor; sides under 4 padded, as the format wants 4 x 4 blocks."""
    import ispc_texcomp
    import numpy as np

    h, w = alpha.shape
    ph, pw = max(4, h), max(4, w)
    rgba = np.zeros((ph, pw, 4), dtype=np.uint8)
    rgba[:h, :w, 3] = alpha
    if (ph, pw) != (h, w):  # edge-replicate into the padding
        rgba[h:, :w, 3] = alpha[h - 1 : h]
        rgba[:, w:, 3] = rgba[:, w - 1 : w, 3]
    n = (ph // 4) * (pw // 4)
    blocks = ispc_texcomp.compress_blocks_bc3(ispc_texcomp.RGBASurface(rgba, pw, ph))
    return np.frombuffer(blocks[: n * BLOCK_BYTES], dtype=np.uint8).reshape(n, BLOCK_BYTES)[:, :8]


def make(land: Path, level: int) -> int | None:
    """The water photo beside the texture ``land``, built from ``level``, made again from that
    file: its levels from :func:`first_level` down, a hole above; their colours the land's own
    blocks of those levels, their alpha the texture's water mask beside it. Returns ``level``;
    None, nothing written, where the texture has no water photo (no file beside it: the squares'
    files say which have one, ``orthostudio.live.names``). Raises ``OSError`` or ``ValueError``
    (the mask or the land unreadable): the caller says it, never failing the land for it.

    The colours are the land's BC1 blocks as the file holds them: every block of Live's leaves in
    BC1's four-colour mode (``imaging.four_colour``), which is how BC3 reads the colour half of each
    of its blocks. So the water shows the land's very colours, the season's included, and the bank
    never changes colour; nothing is encoded a second time; and a water photo can be made from its
    land's file whenever it is wanted, not only as its land is built: a texture built before its
    square's files, or before the water photos, or set to a new zoom. The prototype encoded the
    land's image again (43 ms for 2048 px), and its colours could differ from the land's at the
    bank. Made so, 36 to 47 ms for 2048 px from a 4096 px land and a mask of Europe's region, 16 ms
    of it the alpha's encoding; 3 to 7 ms from a land built from 256 px (the Mac, 2026-11-17)."""
    import numpy as np
    from PIL import Image

    from . import dds
    from .names import mask_name
    from .texture import levels_from

    water = land.with_name(water_name(land.name))
    if not os.path.lexists(water):
        return None
    top = dds.read_top(land)
    wt = water_top(top)
    first = first_level(level, top)
    colours = levels_from(land, first, top)
    with Image.open(land.with_name(mask_name(land.name))) as im:
        mask = np.asarray(im.convert("L"))
    blobs = []
    for alpha, colour in zip(alpha_levels(mask, dds.size_of(first)), colours, strict=True):
        blocks = np.empty((len(colour) // 8, BLOCK_BYTES), dtype=np.uint8)
        blocks[:, :8] = alpha_blocks(alpha)
        blocks[:, 8:] = np.frombuffer(colour, dtype=np.uint8).reshape(-1, 8)
        blobs.append(blocks.tobytes())
    _write(water, wt, first - wt, blobs)
    return level


def empty(land: Path) -> None:
    """The water photo beside ``land`` back to the placeholder of its land's size, a name of it
    (``orthostudio.live.names.WaterPool``, in the pack ``land`` is in)."""
    from . import dds
    from .names import WaterPool

    water = land.with_name(water_name(land.name))
    WaterPool(land.parent.parent).replace(water_top(dds.read_top(land)), water)


def follow(land: Path, level: int | None) -> int | None:
    """The water photo beside ``land`` following its land, whose file was just written again from
    its own levels: made again from them (``level``, the one it is built from), or back to the
    placeholder of its size where the land was made neutral (None) or where it cannot be made,
    never left with another size or photos its land no longer has. The land's own work never fails
    for it. Returns the level it follows, None where it holds no photo (or has no water photo)."""
    water = land.with_name(water_name(land.name))
    if not os.path.lexists(water):
        return None
    if level is not None:
        try:
            return make(land, level)
        except Exception:  # its placeholder then: X-Plane's water alone, never a stale photo
            pass
    with contextlib.suppress(OSError, ValueError):
        empty(land)
    return None


def drop(land: Path) -> None:
    """The water photo beside ``land`` and the mask it is made with deleted: the texture is no
    longer on any square made, and goes too."""
    from .names import mask_name

    for name in (water_name(land.name), mask_name(land.name)):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(land.with_name(name))


def ter_text(text: bytes, water: tuple) -> bytes:
    """A water terrain file of Live's mesh, as its name makes it, naming what its water takes:
    ``("photo", name, side)``, its water photo as its photo (``BASE_TEX_NOWRAP``), its side on
    the ``LOAD_CENTER`` line, and no border, its alpha the mask (the module's docstring);
    ``("border", name)``, the land's photo with that border, one of the two greys of rivers and
    lakes, the whole file. The other lines (``WET``, ``NO_SHADOW``...) as they are."""
    out = []
    for line in text.split(b"\n"):
        if line.startswith((b"LOAD_CENTER_BORDER ", b"BORDER_TEX ")):
            continue
        if water[0] == "photo" and line.startswith(b"BASE_TEX"):
            line = f"BASE_TEX_NOWRAP ../textures/{water[1]}".encode("ascii")
        elif water[0] == "photo" and line.startswith(b"LOAD_CENTER "):
            line = b" ".join([*line.split(b" ")[:-1], str(water[2]).encode("ascii")])
        out.append(line)
        if water[0] == "border" and line.startswith(b"BASE_TEX"):
            out.append(f"BORDER_TEX ../textures/{water[1]}".encode("ascii"))
    return b"\n".join(out)
