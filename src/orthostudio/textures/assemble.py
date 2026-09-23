"""Assemble a 4096² RGB texture from its 256 web-mercator chunks.

Ported from Ortho4XP ``build_texture_from_tilbox`` / ``get_wmts_image``
(``src/O4_Imagery_Utils.py:1244-1368``); behaviour spec in ``docs/specs/textures-assemble.md``.

The chunk container is ``orthostudio.imagery.chunks.ChunkContainer``: 256 entries in row-major
order, each with a ``status`` (``ChunkStatus``) and a ``data`` body. Only those two attributes are
read here, so any object with the same shape works.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from PIL import Image, UnidentifiedImageError

from orthostudio.errors import OsxpError
from orthostudio.imagery.chunks import ChunkStatus

__all__ = [
    "CHUNK",
    "GRID",
    "AssembledTexture",
    "assemble_texture",
    "assemble_texture_detailed",
    "chunk_origin",
    "decode_tile",
    "image_body_complete",
    "parent_fallback",
]

GRID = 16
"""Chunks per texture side."""

CHUNK = 256
"""Pixels per chunk side."""

TEXTURE = GRID * CHUNK

MAX_PARENT_LEVELS = 5
"""Ortho4XP gives up once ``down_sample`` reaches 6, so parents at ``zl-1`` .. ``zl-5`` are
tried."""

Fallback = Callable[[int], "np.ndarray | None"]
ParentAccessor = Callable[[int, int, int], "bytes | None"]


class _Entry(Protocol):
    @property
    def status(self) -> Any: ...

    @property
    def data(self) -> bytes: ...


class _Container(Protocol):
    @property
    def entries(self) -> Sequence[_Entry]: ...


class _TextureLike(Protocol):
    @property
    def til_x(self) -> int: ...

    @property
    def til_y(self) -> int: ...

    @property
    def zl(self) -> int: ...


def chunk_origin(i: int) -> tuple[int, int]:
    """Pixel ``(x0, y0)`` of chunk ``i`` (row-major, ``i = 16*row + col``) inside the texture."""
    if not 0 <= i < GRID * GRID:
        raise ValueError(f"chunk index {i} out of range 0..{GRID * GRID - 1}")
    row, col = divmod(i, GRID)
    return col * CHUNK, row * CHUNK


def _open_image(data: bytes) -> Image.Image:
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        # the decoder is given bytes and nothing else: the source and the chunk belong to the
        # caller, so it says what it knows rather than naming them and leaving them empty
        raise OsxpError(
            "IMG_TILE_CORRUPTED",
            context={"reason": str(exc)},
            message=f"A piece of imagery did not decode ({exc}).",
            remedy="It is fetched again; if it keeps happening the source is sending bad data.",
        ) from exc
    return im


def decode_tile(data: bytes) -> np.ndarray:
    """Decode a JPEG or PNG chunk body to an RGB uint8 ``(256, 256, 3)`` array.

    Any Pillow mode is converted to RGB (Ortho4XP pasted the decoded image as is, Pillow doing the
    same conversion inside ``paste``). A body that does not decode, or whose size is not
    256x256, raises ``OsxpError("IMG_TILE_CORRUPTED")``.
    """
    im = _open_image(data)
    if im.size != (CHUNK, CHUNK):
        raise OsxpError(
            "IMG_TILE_CORRUPTED",
            context={"reason": f"chunk is {im.size[0]}x{im.size[1]}, expected {CHUNK}x{CHUNK}"},
            message=(
                f"A piece of imagery is {im.size[0]}x{im.size[1]} where {CHUNK}x{CHUNK} was "
                "expected."
            ),
            remedy="It is fetched again; if it keeps happening the source is sending bad data.",
        )
    if im.mode != "RGB":
        im = im.convert("RGB")
    return np.asarray(im)


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_IEND = b"IEND\xaeB`\x82"  # the IEND chunk type and its constant CRC end every PNG


def image_body_complete(body: bytes) -> bool:
    """Cheap structural check of a tile body before it is stored as ``OK``.

    Ortho4XP decoded every body on receipt and retried a corrupted one up to
    ``max_baddata_retries`` (``O4_Imagery_Utils.py:1035-1046``). Decoding 1 400 bodies per
    second on the event loop is too costly, so this checks the signature **and** the trailer
    the format requires: JPEG ``FFD9`` (a few trailing bytes tolerated), PNG ``IEND`` chunk,
    WebP/BMP declared size, GIF trailer ``0x3B``. A body failing it is a truncated or foreign
    answer, not a tile; :func:`decode_tile` remains the authority at assembly time.
    """
    if body.startswith(b"\xff\xd8\xff"):
        return len(body) > 4 and body.rfind(b"\xff\xd9") >= len(body) - 18
    if body.startswith(_PNG_SIGNATURE):
        return len(body) > len(_PNG_SIGNATURE) + 12 and body.endswith(_PNG_IEND)
    if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return len(body) >= int.from_bytes(body[4:8], "little") + 8
    if body.startswith(b"GIF8"):
        return len(body) > 13 and body.endswith(b"\x3b")
    if body.startswith(b"BM"):
        return len(body) > 26 and len(body) >= int.from_bytes(body[2:6], "little")
    return False


def _decode_or_none(data: bytes) -> np.ndarray | None:
    try:
        return decode_tile(data)
    except OsxpError:
        return None


def parent_fallback(
    t: _TextureLike, get_parent: ParentAccessor, *, max_levels: int = MAX_PARENT_LEVELS
) -> Fallback:
    """Build the Ortho4XP parent-chunk fallback for texture ``t``.

    ``get_parent(x, y, zl)`` returns the body of chunk ``(x, y)`` at zoom ``zl`` or ``None``.
    For chunk ``i`` the parents ``(x >> d, y >> d, zl - d)`` are tried for ``d = 1..max_levels``;
    the first one that decodes is cropped to the quadrant covering the chunk with the Ortho4XP
    formula (``O4_Imagery_Utils.py:1256-1272``) and upsampled to 256² with Pillow ``BICUBIC``.
    """
    if max_levels < 1:
        raise ValueError("max_levels must be at least 1")

    def fallback(i: int) -> np.ndarray | None:
        col, row = (v // CHUNK for v in chunk_origin(i))
        x_orig, y_orig = t.til_x + col, t.til_y + row
        x, y, zl = x_orig, y_orig, t.zl
        for d in range(1, max_levels + 1):
            x, y, zl = x // 2, y // 2, zl - 1
            if zl < 0:
                return None
            body = get_parent(x, y, zl)
            if body is None:
                continue
            rgb = _decode_or_none(body)
            if rgb is None:
                continue
            scale = 2**d
            x0 = (x_orig - scale * x) * CHUNK // scale
            y0 = (y_orig - scale * y) * CHUNK // scale
            side = CHUNK // scale
            part = Image.fromarray(rgb).crop((x0, y0, x0 + side, y0 + side))
            return np.asarray(part.resize((CHUNK, CHUNK), Image.Resampling.BICUBIC))
        return None

    return fallback


@dataclass(slots=True)
class AssembledTexture:
    """Result of :func:`assemble_texture_detailed`."""

    rgb: np.ndarray
    from_fallback: list[int] = field(default_factory=list)
    """Chunks the container lacked and the fallback provided (``IMG_TILE_PARENT_FALLBACK``)."""
    unfilled: list[int] = field(default_factory=list)
    """Chunks nobody provided, filled with their neighbours' mean (``IMG_TILE_MISSING``)."""
    corrupted: list[int] = field(default_factory=list)
    """``OK`` entries whose body did not decode (``IMG_TILE_CORRUPTED``); they are also in
    ``from_fallback`` or ``unfilled``, and the caller should make the store fetch them again."""

    @property
    def missing(self) -> list[int]:
        """Every chunk not taken from the container, ascending."""
        return sorted(self.from_fallback + self.unfilled)

    @property
    def complete(self) -> bool:
        return not self.unfilled


def _fill_unfilled(rgb: np.ndarray, filled: np.ndarray, unfilled: list[int]) -> None:
    """Paint each unfilled chunk with the mean colour of its filled 8-neighbours."""
    means = np.zeros((GRID, GRID, 3), dtype=np.float64)
    for row in range(GRID):
        for col in range(GRID):
            if filled[row, col]:
                block = rgb[row * CHUNK : (row + 1) * CHUNK, col * CHUNK : (col + 1) * CHUNK]
                means[row, col] = block.reshape(-1, 3).mean(axis=0)
    overall = means[filled].mean(axis=0) if filled.any() else np.full(3, 128.0)
    for i in unfilled:
        row, col = divmod(i, GRID)
        r0, r1 = max(0, row - 1), min(GRID, row + 2)
        c0, c1 = max(0, col - 1), min(GRID, col + 2)
        window = filled[r0:r1, c0:c1]
        colour = means[r0:r1, c0:c1][window].mean(axis=0) if window.any() else overall
        x0, y0 = chunk_origin(i)
        rgb[y0 : y0 + CHUNK, x0 : x0 + CHUNK] = np.rint(colour).astype(np.uint8)


def assemble_texture_detailed(
    container: _Container, fallback: Fallback | None = None
) -> AssembledTexture:
    """Assemble the texture and report separately what the fallback and the mean filled."""
    entries = container.entries
    if len(entries) != GRID * GRID:
        raise ValueError(f"container has {len(entries)} entries, expected {GRID * GRID}")
    rgb = np.empty((TEXTURE, TEXTURE, 3), dtype=np.uint8)
    filled = np.zeros((GRID, GRID), dtype=bool)
    result = AssembledTexture(rgb)
    for i, entry in enumerate(entries):
        tile: np.ndarray | None = None
        if entry.status == ChunkStatus.OK:
            tile = _decode_or_none(entry.data)
            if tile is None:
                result.corrupted.append(i)
        if tile is None and fallback is not None:
            tile = fallback(i)
            if tile is not None:
                tile = np.asarray(tile, dtype=np.uint8)
                if tile.shape != (CHUNK, CHUNK, 3):
                    raise ValueError(f"fallback for chunk {i} returned shape {tile.shape}")
                result.from_fallback.append(i)
        if tile is None:
            result.unfilled.append(i)
            continue
        x0, y0 = chunk_origin(i)
        rgb[y0 : y0 + CHUNK, x0 : x0 + CHUNK] = tile
        filled[i // GRID, i % GRID] = True
    if result.unfilled:
        _fill_unfilled(rgb, filled, result.unfilled)
    return result


def assemble_texture(
    container: _Container, fallback: Fallback | None = None
) -> tuple[np.ndarray, list[int]]:
    """Assemble the 4096² RGB texture of ``container``.

    Returns the image and the ascending indices of every chunk that did not come from the
    container (replaced by ``fallback(i)`` when it returned an image, otherwise by the mean
    colour of the neighbouring chunks). Use :func:`assemble_texture_detailed` to tell the two
    cases apart.
    """
    result = assemble_texture_detailed(container, fallback)
    return result.rgb, result.missing
