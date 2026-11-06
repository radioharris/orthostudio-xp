# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A cloud of a texture laid over with the same ground from another source (``clouds.md`` 4.4).

One function says what a cloud lays on a texture (:func:`layer`), one lays the layers in order
(:func:`lay`). The build goes through both, and a cloud's preview is drawn from the same layer:
what the map shows is what the texture gets. A cloud is laid from its *part*, the source's pieces
it reads in one texture, frozen when its images were got (:func:`encode_part`): a build never asks
the source again.

Inside the outline the source's pixels, centre on centre, in the colours its preview showed
(``match``: a gain and a shift per channel); a source four times or more coarser than the texture
is sharpened a little. The outline has a soft edge, drawn past the window and past the tile's edge
before it is blurred and only then cut, so a seam between two textures or the tile's edge is no
edge of the cloud. Where the source has no picture, nothing is laid, its soft edge on the
source's side: a hole is worse than a cloud.
"""

from __future__ import annotations

import io
import math
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from PIL import Image

from orthostudio.clouds.opencv import cv2
from orthostudio.imagery.grid import TextureId, wgs84_to_tile
from orthostudio.textures.assemble import decode_tile

__all__ = [
    "CloudPatch",
    "Layer",
    "Match",
    "Piece",
    "clouds_input",
    "decode_part",
    "encode_part",
    "lay",
    "layer",
    "pieces",
    "read_clouds_input",
    "read_piece",
    "tile_box",
    "window",
]

TEXTURE = 4096
CHUNK = 256
FEATHER_PX = 24
"""Half width of the soft edge, as the zones' colours have it: about 40 m at ZL16."""
REACH_PX = 2 * FEATHER_PX
"""How far the soft edge's blur sees: the outline is drawn this far past the window."""
SHARPEN_FROM = 4

Match = tuple[tuple[float, float, float], tuple[float, float, float]]
"""``((gain r, g, b), (shift r, g, b))``: the source's colours as its preview showed them."""
PieceKey = tuple[int, int, int]
"""``(x, y, zl)`` of a source's piece."""


@dataclass(frozen=True, slots=True)
class CloudPatch:
    """One cloud in one texture: its outline's rings in the texture's pixels (``x0, y0, x1, y1,
    ...``), its source read at ``source_zl`` (the texture's level, or the source's finest when
    coarser), its colours, and its tile's box in the texture's pixels (``west, north, east,
    south``)."""

    cloud: str
    source: str
    source_zl: int
    rings: tuple[tuple[float, ...], ...]
    match: Match
    tile: tuple[float, float, float, float]


def tile_box(tile_lat: int, tile_lon: int, t: TextureId) -> tuple[float, float, float, float]:
    """The 1-degree tile ``(tile_lat, tile_lon)`` in the pixels of the texture ``t``."""
    xw, yn = wgs84_to_tile(tile_lat + 1, tile_lon, t.zl)
    xe, ys = wgs84_to_tile(tile_lat, tile_lon + 1, t.zl)
    return (
        (xw - t.til_x) * CHUNK,
        (yn - t.til_y) * CHUNK,
        (xe - t.til_x) * CHUNK,
        (ys - t.til_y) * CHUNK,
    )


def _bounds(p: CloudPatch, grow: int) -> tuple[int, int, int, int]:
    pts = np.concatenate([np.asarray(r, np.float64).reshape(-1, 2) for r in p.rings])
    return (
        math.floor(pts[:, 0].min()) - grow,
        math.floor(pts[:, 1].min()) - grow,
        math.ceil(pts[:, 0].max()) + grow,
        math.ceil(pts[:, 1].max()) + grow,
    )


def window(p: CloudPatch) -> tuple[int, int, int, int] | None:
    """What ``p`` writes of its texture, ``(x0, y0, x1, y1)``: its outline and soft edge, inside
    the texture; ``None`` when it does not reach it."""
    x0, y0, x1, y1 = _bounds(p, REACH_PX)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(TEXTURE, x1), min(TEXTURE, y1)
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _source_box(p: CloudPatch, t: TextureId, box: tuple[int, int, int, int]) -> tuple[float, ...]:
    """``box`` (texture pixels) in the source's global pixels: ``gx0, gy0, gx1, gy1, scale``."""
    scale = 2 ** (t.zl - p.source_zl)
    x0, y0, x1, y1 = box
    return (
        (t.til_x * CHUNK + x0) / scale,
        (t.til_y * CHUNK + y0) / scale,
        (t.til_x * CHUNK + x1) / scale,
        (t.til_y * CHUNK + y1) / scale,
        scale,
    )


def _read_box(p: CloudPatch) -> tuple[int, int, int, int] | None:
    """The window grown by the soft edge's half width, past the texture's edges too: where the
    source is had is known there, so a source stopping on a seam fades inside the texture that
    has it instead of stopping on a straight line along the seam."""
    w = window(p)
    return (
        None
        if w is None
        else (w[0] - FEATHER_PX, w[1] - FEATHER_PX, w[2] + FEATHER_PX, w[3] + FEATHER_PX)
    )


def pieces(p: CloudPatch, t: TextureId) -> list[PieceKey]:
    """Every piece of ``p``'s source its layer reads in ``t``, as the world's grid has them:
    round the antimeridian, none past the poles' rows."""
    box = _read_box(p)
    if box is None:
        return []
    gx0, gy0, gx1, gy1, _scale = _source_box(p, t, box)
    n = 1 << p.source_zl
    keys = (
        (x % n, y, p.source_zl)
        for y in range(int(gy0 // CHUNK), int((gy1 - 1e-9) // CHUNK) + 1)
        for x in range(int(gx0 // CHUNK), int((gx1 - 1e-9) // CHUNK) + 1)
        if 0 <= y < n
    )
    return list(dict.fromkeys(keys))


# -- a source's piece ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Piece:
    """A piece as every reader takes it: ``picture`` (its ``rgb`` and where it ``shows`` its
    source: not where it is transparent), ``no_picture`` or ``not_readable``."""

    kind: str
    rgb: np.ndarray | None = None
    shows: np.ndarray | None = None


def read_piece(body: bytes | None) -> Piece:
    """The one reading of a source's piece, for the layer, a search and a check alike."""
    if not body:
        return Piece("no_picture")
    try:
        rgb = decode_tile(body)
    except Exception:  # whatever the decoder raises, another size included
        return Piece("not_readable")
    shows = np.ones((CHUNK, CHUNK), bool)
    if body[:3] != b"\xff\xd8\xff":  # a JPEG has no transparency
        with Image.open(io.BytesIO(body)) as im:
            if "A" in im.getbands() or "transparency" in im.info:
                shows = np.asarray(im.convert("RGBA"))[:, :, 3] > 0
    return Piece("picture", rgb, shows) if shows.any() else Piece("no_picture")


# -- a part: one cloud's pieces in one texture --------------------------------------------------

_PART = struct.Struct("<8sI")
_PIECE = struct.Struct("<IIBI")
PART_MAGIC = b"OSXPPRT2"


def encode_part(got: Mapping[PieceKey, bytes | None]) -> bytes:
    """The pieces a cloud reads in one texture, a picture's bytes or none, in one order: one
    content, one digest."""
    out = [_PART.pack(PART_MAGIC, len(got))]
    for (x, y, zl), body in sorted(got.items()):
        out += [_PIECE.pack(x, y, zl, len(body or b"")), body or b""]
    return b"".join(out)


def decode_part(data: bytes) -> dict[PieceKey, bytes | None]:
    """:func:`encode_part`'s pieces back; ``ValueError`` for anything else."""
    try:
        magic, count = _PART.unpack_from(data, 0)
        if magic != PART_MAGIC:
            raise ValueError("not a cloud's part")
        at, got = _PART.size, {}
        for _ in range(count):
            x, y, zl, n = _PIECE.unpack_from(data, at)
            at += _PIECE.size
            got[(x, y, zl)] = data[at : at + n] or None
            at += n
    except struct.error as exc:
        raise ValueError(f"a cloud's part cut short: {exc}") from None
    if at != len(data):
        raise ValueError("bytes past a cloud's part")
    return got


_CLOUDS = struct.Struct("<8sI")
_ENTRY = struct.Struct("<HI")
CLOUDS_MAGIC = b"OSXPCLD3"


def clouds_input(parts: Sequence[tuple[str, bytes]]) -> bytes:
    """A texture's ``clouds`` input: each cloud's id and part, in laying order."""
    out = [_CLOUDS.pack(CLOUDS_MAGIC, len(parts))]
    for cloud, part in parts:
        name = cloud.encode()
        out += [_ENTRY.pack(len(name), len(part)), name, part]
    return b"".join(out)


def read_clouds_input(data: bytes) -> dict[str, dict[PieceKey, bytes | None]]:
    """:func:`clouds_input`'s parts back, by cloud; ``ValueError`` for anything else."""
    try:
        magic, count = _CLOUDS.unpack_from(data, 0)
        if magic != CLOUDS_MAGIC:
            raise ValueError("not a texture's clouds")
        at, out = _CLOUDS.size, {}
        for _ in range(count):
            n, m = _ENTRY.unpack_from(data, at)
            at += _ENTRY.size
            out[data[at : at + n].decode()] = decode_part(data[at + n : at + n + m])
            at += n + m
    except struct.error as exc:
        raise ValueError(f"a texture's clouds cut short: {exc}") from None
    return out


# -- the layer ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class Layer:
    """What one cloud lays on one texture over ``window``: the source at the texture's scale
    (``uint8``), how much of it shows (``alpha``, ``uint8``) and its colours (``match``)."""

    window: tuple[int, int, int, int]
    source: np.ndarray
    alpha: np.ndarray
    match: Match

    def rgb(self, r0: int = 0, r1: int | None = None) -> np.ndarray:
        """Rows ``r0:r1`` of its colours, in the photo's (``match``) and clipped to what a pixel
        holds (``float32``), a few at a time: a cloud over a whole texture in ``float32`` is
        200 MB, half of what a texture may hold. The build lays them, the preview reduces them."""
        gain, shift = (np.asarray(v, np.float32) for v in self.match)
        return np.clip(self.source[r0:r1].astype(np.float32) * gain + shift, 0, 255)


def _source(
    p: CloudPatch,
    t: TextureId,
    box: tuple[int, int, int, int],
    got: Mapping[PieceKey, bytes | None],
) -> tuple[np.ndarray, np.ndarray]:
    """The source over ``box`` at the texture's scale, and where it has a picture."""
    gx0, gy0, gx1, gy1, scale = _source_box(p, t, box)
    tx0, ty0 = int(gx0 // CHUNK), int(gy0 // CHUNK)
    tx1, ty1 = int((gx1 - 1e-9) // CHUNK), int((gy1 - 1e-9) // CHUNK)
    mosaic = np.zeros(((ty1 - ty0 + 1) * CHUNK, (tx1 - tx0 + 1) * CHUNK, 3), np.uint8)
    have = np.zeros(mosaic.shape[:2], np.uint8)
    n = 1 << p.source_zl
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            piece = read_piece(got.get((tx % n, ty, p.source_zl)) if 0 <= ty < n else None)
            if piece.kind == "picture":
                r, c = (ty - ty0) * CHUNK, (tx - tx0) * CHUNK
                mosaic[r : r + CHUNK, c : c + CHUNK] = piece.rgb
                have[r : r + CHUNK, c : c + CHUNK] = piece.shows
    # pixel centre on pixel centre: corner on corner shifted EOX under ZL16 by 3/8 of a pixel
    centre = 0.5 / scale - 0.5
    m = np.float32(
        [[1 / scale, 0, gx0 - tx0 * CHUNK + centre], [0, 1 / scale, gy0 - ty0 * CHUNK + centre]]
    )
    size = (box[2] - box[0], box[3] - box[1])
    src = cv2.warpAffine(
        mosaic,
        m,
        size,
        flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_REPLICATE,
    )
    ok = cv2.warpAffine(have, m, size, flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP) > 0
    if scale >= SHARPEN_FROM:  # soft at 10 m beside 2.7 m it looked smeared (2026-11-03)
        src = cv2.addWeighted(src, 2.2, cv2.GaussianBlur(src, (0, 0), sigmaX=scale), -1.2, 0)
    return src, ok


def layer(p: CloudPatch, t: TextureId, got: Mapping[PieceKey, bytes | None]) -> Layer | None:
    """What ``p`` lays on the texture ``t`` from its part's pieces ``got``; ``None`` when it lays
    nothing (outside ``t``, or no picture of its source under it)."""
    w = window(p)
    box = _read_box(p)
    if w is None or box is None:
        return None
    wide, ok_wide = _source(p, t, box, got)
    f = FEATHER_PX
    src, ok = np.ascontiguousarray(wide[f:-f, f:-f]), ok_wide[f:-f, f:-f]
    if not ok.any():
        return None
    x0, y0, x1, y1 = w
    r = REACH_PX
    sx0, sy0 = x0 - r, y0 - r
    inside = np.zeros((y1 - y0 + 2 * r, x1 - x0 + 2 * r), np.uint8)
    polys = [
        np.round(np.asarray(r, np.float64).reshape(-1, 2) - (sx0, sy0)).astype(np.int32)
        for r in p.rings
    ]
    cv2.fillPoly(inside, polys, 255)
    if not ok_wide.all():  # where the source stops, the outline steps back by the soft edge
        side = 2 * f + 1
        on = cv2.erode(
            ok_wide.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_RECT, (side, side))
        )
        part = inside[r - f : r - f + on.shape[0], r - f : r - f + on.shape[1]]
        part[on == 0] = 0
    inside = _past_the_tile(inside, p.tile, (sx0, sy0))
    alpha = np.ascontiguousarray(cv2.GaussianBlur(inside, (0, 0), sigmaX=f / 2)[r:-r, r:-r])
    alpha[~ok] = 0
    return Layer(w, src, alpha, p.match)


def _past_the_tile(
    stencil: np.ndarray, tile: Sequence[float], origin: tuple[int, int]
) -> np.ndarray:
    """``stencil`` with what lies past the tile's edge made what it is at that edge: X-Plane
    shows nothing of this tile there, so the tile's edge must not fade the cloud."""
    west, north, east, south = tile
    h, w = stencil.shape
    c0 = min(w - 1, max(0, math.ceil(west - origin[0] - 0.5)))
    c1 = min(w - 1, max(0, math.ceil(east - origin[0] - 0.5) - 1))
    r0 = min(h - 1, max(0, math.ceil(north - origin[1] - 0.5)))
    r1 = min(h - 1, max(0, math.ceil(south - origin[1] - 0.5) - 1))
    if (c0, r0, c1, r1) == (0, 0, w - 1, h - 1):
        return stencil
    return np.ascontiguousarray(
        stencil[np.clip(np.arange(h), r0, r1)][:, np.clip(np.arange(w), c0, c1)]
    )


def lay(rgb: np.ndarray, layers: Iterable[Layer]) -> np.ndarray:
    """``rgb`` (``uint8``) with ``layers`` laid in order, the later over the earlier; ``rgb``
    itself when there is none, else a new array. 512 rows at a time, for the memory."""
    out: np.ndarray | None = None
    for la in layers:
        out = rgb.copy() if out is None else out
        x0, y0, x1, y1 = la.window
        here = out[y0:y1, x0:x1]
        for r0 in range(0, y1 - y0, 512):
            r1 = min(y1 - y0, r0 + 512)
            a = la.alpha[r0:r1, :, None].astype(np.float32) / 255
            here[r0:r1] = np.clip(a * la.rgb(r0, r1) + (1 - a) * here[r0:r1], 0, 255).astype(
                np.uint8
            )
    return rgb if out is None else out
