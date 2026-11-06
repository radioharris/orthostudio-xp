# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The tile as X-Plane draws it, at a reduced level: what the cloud mode's map shows, what the
search looks for clouds in, what a cloud's colours are matched on (``clouds.md`` R2).

X-Plane draws a tile from its DSF: every triangle takes the texture its terrain names. On ground
the photo shows whole; on the sea the photo shows at its texture's alpha (the sea mask) over
X-Plane's own water; on inland water at that alpha times the lakes' share over its water; where
only X-Plane's water is drawn, no photo at all. So the picture is drawn triangle by triangle from
the DSF the tile folder holds, each texture read at the level of its file that matches, as the
build made it (``TileClouds.plains``): the photo under the clouds, as X-Plane shows it once a cloud
is unticked. X-Plane's water is one colour here
(:data:`WATER_RGB`); the share of the photo over it is X-Plane's.
"""

from __future__ import annotations

import math
import struct
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from orthostudio.clouds.layer import tile_pixels
from orthostudio.clouds.opencv import cv2
from orthostudio.dsf.decode import decode_dsf
from orthostudio.errors import OsxpError
from orthostudio.imagery.grid import TextureId, parse_texture_name, tile_to_wgs84
from orthostudio.masks.profiles import WATER_TRANSITION
from orthostudio.model import TileRef
from orthostudio.textures.assemble import TEXTURE
from orthostudio.textures.bcdecode import decode_blocks
from orthostudio.textures.dds import BLOCK_BYTES, HEADER_SIZE, level_size, mip_dims, parse_header
from orthostudio.textures.ter import ter_kind

__all__ = [
    "WATER_RGB",
    "Picture",
    "box_bounds",
    "draw",
    "frame",
    "over_water",
    "pixel_metres",
    "search_level",
]

WATER_RGB = (36, 64, 84)
"""X-Plane's water on the map, one colour: its waves and reflections no picture can hold."""
SEARCH_METRES = 15.0
"""The search reads a tile at the sharpest level whose pixel is at least this (B1)."""
_CORNERS_ZL = 20
LAND, SEA, INLAND = 1, 2, 3


def pixel_metres(lat: float, zl: int) -> float:
    return 40075016.686 * math.cos(math.radians(lat)) / (256 * 2**zl)


def search_level(tile: TileRef) -> int:
    """The sharpest level whose pixel is at least :data:`SEARCH_METRES` on ``tile``."""
    return max(z for z in range(8, 20) if pixel_metres(tile.lat + 0.5, z) >= SEARCH_METRES)


def frame(tile: TileRef, zl: int) -> tuple[int, int, int, int]:
    """The tile's box at ``zl`` in the world's whole pixels: ``x0, y0, width, height``."""
    west, north, east, south = tile_pixels(tile.lat, tile.lon, zl)
    x0, y0 = math.floor(west), math.floor(north)
    return x0, y0, math.ceil(east) - x0, math.ceil(south) - y0


def lakes_share(t: float) -> float:
    """The photo's share over inland water: ``water_transition.png`` at the border coordinate a
    lake's corners carry (``ratio_water``), read from the image's bottom: 58 % at the default."""
    ramp = np.asarray(WATER_TRANSITION, np.float64) / 255
    return float(
        np.interp((1.0 - min(1.0, max(0.0, t))) * (len(ramp) - 1), np.arange(len(ramp)), ramp)
    )


@dataclass(frozen=True, slots=True, eq=False)
class Picture:
    """The tile at ``zl`` from the global pixel ``(x0, y0)``: what X-Plane shows (``rgb``), the
    share of the photo in it (``share``, 0 to 255: 0 where X-Plane shows its water alone), the
    photo itself (``photo``) and which texture each pixel shows (``shows``, an index of
    ``names``, -1 for none)."""

    rgb: np.ndarray
    photo: np.ndarray
    share: np.ndarray
    shows: np.ndarray
    names: tuple[str, ...]
    zl: int
    x0: int
    y0: int


@dataclass(frozen=True, slots=True, eq=False)
class _Drawn:
    """A DSF's triangles: corners in pixels of :data:`_CORNERS_ZL` from ``origin`` (``x``, ``y``,
    ``(n, 3)``), sorted by terrain, those of terrain ``i`` at ``starts[i]:starts[i + 1]``; each
    terrain's texture (``None``: X-Plane's water), kind and share."""

    x: np.ndarray
    y: np.ndarray
    starts: np.ndarray
    terrains: tuple[tuple[str | None, int, float], ...]
    origin: tuple[int, int]


def _terrain(name: str, border: float) -> tuple[str | None, int, float]:
    if not name.startswith("terrain/"):
        return (None, 0, 0.0)
    file = name.rsplit("/", 1)[-1]
    kind = ter_kind(file)
    texture = (
        file.removesuffix(".ter").removesuffix(kind.suffix)
        if kind.suffix
        else file.removesuffix(".ter")
    )
    if kind.tri_type == 0:
        return (texture, LAND, 1.0)
    if kind.tri_type == 1 and kind.overlay:
        return (texture, INLAND, lakes_share(border))
    return (texture, SEA, 1.0)


def _read_dsf(path: Path, tile: TileRef) -> _Drawn:
    """The DSF's triangles as the app's decoder reads them (``dsf.decode``), sorted by terrain."""
    dsf = decode_dsf(path.read_bytes())
    names = dsf.terrains
    if dsf.patches:
        corners = np.concatenate([q.corners for q in dsf.patches])
        terrain = np.concatenate(
            [np.full(len(q.corners), q.terrain, np.int64) for q in dsf.patches]
        )
    else:
        corners, terrain = np.zeros((0, 3, 2), np.int64), np.zeros(0, np.int64)
    pool, index = corners[..., 0].reshape(-1), corners[..., 1].reshape(-1)
    first = np.concatenate([[0], np.cumsum([len(q) for q in dsf.pools])[:-1]]).astype(np.int64)
    at = first[pool] + index

    def plane(j: int) -> np.ndarray:
        raw = np.concatenate(
            [q[:, j] if q.shape[1] > j else np.zeros(len(q), np.uint16) for q in dsf.pools]
        )
        scale = np.array([s[j, 0] if len(s) > j else 0.0 for s in dsf.scales])
        offset = np.array([s[j, 1] if len(s) > j else 0.0 for s in dsf.scales])
        return offset[pool] + raw[at].astype(np.float64) * scale[pool] / 65535

    lon, lat = plane(0), plane(1)
    gx0, gy0, _w, _h = frame(tile, _CORNERS_ZL)
    half = 2.0 ** (_CORNERS_ZL - 1) * 256
    x = ((lon / 180 + 1) * half - gx0).astype(np.float32).reshape(-1, 3)
    y = (
        ((1 - np.log(np.tan((90 + lat) * np.pi / 360)) / np.pi) * half - gy0)
        .astype(np.float32)
        .reshape(-1, 3)
    )
    border = plane(8).reshape(-1, 3).mean(axis=1) if len(terrain) else np.zeros(0)
    order = np.argsort(terrain, kind="stable")
    starts = np.searchsorted(terrain[order], np.arange(len(names) + 1)).astype(np.int64)
    terrains = tuple(
        _terrain(
            name,
            float(border[order[starts[i] : starts[i + 1]]].mean())
            if starts[i + 1] > starts[i]
            else 0.0,
        )
        for i, name in enumerate(names)
    )
    return _Drawn(x[order], y[order], starts, terrains, (gx0, gy0))


_DRAWN: dict[tuple[str, int, int, int], _Drawn] = {}
_LOCK = threading.Lock()


def _drawn(dsf: Path, tile: TileRef) -> _Drawn:
    """The DSF X-Plane reads, read once while that file stays the same."""
    try:
        st = dsf.stat()
        key = (str(dsf), st.st_ino, st.st_size, st.st_mtime_ns)
        with _LOCK:
            if key not in _DRAWN:
                _DRAWN.clear()
                _DRAWN[key] = _read_dsf(dsf, tile)
            return _DRAWN[key]
    except (OSError, ValueError, IndexError, NotImplementedError, struct.error) as exc:
        raise OsxpError(
            "CLOUD_NOT_BUILT", context={"tile": tile.name, "reason": f"its DSF: {exc}"}
        ) from exc


def _filled(
    d: _Drawn, i: int, zl: int, origin: tuple[int, int], shape: tuple[int, int]
) -> np.ndarray:
    """The triangles of terrain ``i`` for ``fillPoly`` on an image of ``shape`` whose first pixel
    is the global ``origin`` at ``zl``: sixteenths of a pixel, centre on centre."""
    sel = slice(int(d.starts[i]), int(d.starts[i + 1]))
    k = float(2 ** (_CORNERS_ZL - zl))
    x = (d.x[sel].astype(np.float64) + (d.origin[0] - origin[0] * k)) / k - 0.5
    y = (d.y[sel].astype(np.float64) + (d.origin[1] - origin[1] * k)) / k - 0.5
    if not len(x):
        return np.zeros((0, 3, 2), np.int32)
    h, w = shape
    on = (x.max(axis=1) >= -1) & (x.min(axis=1) <= w) & (y.max(axis=1) >= -1) & (y.min(axis=1) <= h)
    return np.rint(np.stack([x[on], y[on]], axis=2) * 16).astype(np.int32)


def _fill(image: np.ndarray, pts: np.ndarray, value: int) -> None:
    if len(pts):
        cv2.fillPoly(image, pts, int(value), lineType=cv2.LINE_8, shift=4)


def _level_rows(path: Path, index: int, rows: tuple[int, int]) -> tuple[np.ndarray, int] | None:
    """Rows ``rows`` of mip level ``index`` of the DDS at ``path`` (``RGBA``), read alone from
    the disk, and the row they start at; ``None`` when the file stops above that level."""
    with open(path, "rb") as f:
        header = parse_header(f.read(HEADER_SIZE))
        dims = mip_dims(header.width, header.height, header.mip_count)
        if index >= len(dims):
            return None
        offset = HEADER_SIZE + sum(level_size(w, h, header.fmt) for w, h in dims[:index])
        lw, lh = dims[index]
        stride = -(-lw // 4) * BLOCK_BYTES[header.fmt]
        first, last = max(0, rows[0]) // 4, -(-min(lh, max(rows[1], rows[0] + 1)) // 4)
        f.seek(offset + first * stride)
        strip = f.read((last - first) * stride)
    return decode_blocks(strip, lw, (last - first) * 4, header.fmt), first * 4


def draw(
    tile: TileRef,
    zl: int,
    *,
    dsf: Path,
    textures: Mapping[str, Path],
) -> Picture:
    """The tile at ``zl`` as X-Plane draws it from the DSF ``dsf``, each texture read from its
    file in ``textures`` (by name)."""
    x0, y0, w, h = frame(tile, zl)
    d = _drawn(dsf, tile)
    photo = np.zeros((h, w, 3), np.uint8)
    share = np.zeros((h, w), np.uint8)
    shows = np.full((h, w), -1, np.int16)
    names: list[str] = []
    bx0, by0, bx1, by1 = 0, 0, w, h
    by_texture: dict[str, list[int]] = {}
    for i, (texture, _kind, _share) in enumerate(d.terrains):
        if texture is not None:
            by_texture.setdefault(texture, []).append(i)
    for name in sorted(by_texture):
        t: TextureId = parse_texture_name(name)
        path = textures.get(name)
        if t.zl < zl or path is None or not path.is_file():
            continue
        level = t.zl - zl
        gx, gy = ((t.til_x * 256) >> level) - x0, ((t.til_y * 256) >> level) - y0
        side = TEXTURE >> level
        cx0, cy0, cx1, cy1 = max(gx, bx0), max(gy, by0), min(gx + side, bx1), min(gy + side, by1)
        if cx1 <= cx0 or cy1 <= cy0:
            continue
        code = np.zeros((cy1 - cy0, cx1 - cx0), np.uint8)
        for j, i in enumerate(by_texture[name], start=1):
            _fill(code, _filled(d, i, zl, (x0 + cx0, y0 + cy0), code.shape), j)
        if not code.any():
            continue
        read = _level_rows(path, level, (cy0 - gy, cy1 - gy))
        if read is None:
            continue
        px, top = read
        px = px[cy0 - gy - top : cy1 - gy - top, cx0 - gx : cx1 - gx]
        code = code[: px.shape[0], : px.shape[1]]
        here = code > 0
        cover = np.full(code.shape, 255, np.uint8)
        for j, i in enumerate(by_texture[name], start=1):
            _texture, kind, lake = d.terrains[i]
            if kind != LAND:  # the photo over water at its alpha, a lake's at its share too
                on = code == j
                cover[on] = np.rint(px[:, :, 3][on] * np.float32(lake)).astype(np.uint8)
        at = (slice(cy0, cy0 + px.shape[0]), slice(cx0, cx0 + px.shape[1]))
        np.copyto(photo[at], px[:, :, :3], where=here[:, :, None])
        np.copyto(share[at], cover, where=here)
        np.copyto(shows[at], np.int16(len(names)), where=here)
        names.append(name)
    return Picture(over_water(photo, share), photo, share, shows, tuple(names), zl, x0, y0)


def box_bounds(p: Picture, box: tuple[int, int, int, int]) -> list[float]:
    """The box ``(x0, y0, x1, y1)`` of the picture's pixels in degrees: ``[south, west, north,
    east]``, as the page lays an image."""
    north, west = tile_to_wgs84((p.x0 + box[0]) / 256, (p.y0 + box[1]) / 256, p.zl)
    south, east = tile_to_wgs84((p.x0 + box[2]) / 256, (p.y0 + box[3]) / 256, p.zl)
    return [south, west, north, east]


def over_water(rgb: np.ndarray, share: np.ndarray) -> np.ndarray:
    """``rgb`` as X-Plane shows it at ``share`` (0 to 255) over its water (``uint8``)."""
    s = share.astype(np.float32)[..., None] / 255
    out = rgb.astype(np.float32) * s + np.asarray(WATER_RGB, np.float32) * (1 - s)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)
