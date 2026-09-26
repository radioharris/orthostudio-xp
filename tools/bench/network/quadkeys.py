# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Web-mercator tile arithmetic and Bing quadkeys for the network benchmarks.

Pure functions, no dependency on the OrthoStudio XP package. The formulas follow
the standard slippy-map convention (origin top-left, 256 px tiles); ``wgs84_to_gtile`` uses the
same rounding as Ortho4XP (``O4_Geo_Utils.wgs84_to_gtile``) so that the two agree on tile
indices at cell borders.

Command line::

    uv run python tools/bench/network/quadkeys.py --lat 43 --lon 5 --zl 16 --out tiles.txt
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Iterator
from pathlib import Path

TILE_PX = 256
TEXTURE_TILES = 16  # an Ortho4XP texture is 16 x 16 tiles = 4096 px


def wgs84_to_gtile(lat: float, lon: float, zl: int) -> tuple[int, int]:
    """Return the (x, y) index of the tile containing (lat, lon) at zoom ``zl``."""
    rat_x = lon / 180.0
    rat_y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / math.pi
    pix_x = round((rat_x + 1.0) * (2 ** (zl + 7)))
    pix_y = round((1.0 - rat_y) * (2 ** (zl + 7)))
    return pix_x // TILE_PX, pix_y // TILE_PX


def gtile_to_wgs84(x: int, y: int, zl: int) -> tuple[float, float]:
    """Return the (lat, lon) of the top-left corner of tile (x, y)."""
    rat_x = x / (2 ** (zl - 1)) - 1.0
    rat_y = 1.0 - y / (2 ** (zl - 1))
    lon = rat_x * 180.0
    lat = 360.0 / math.pi * math.atan(math.exp(math.pi * rat_y)) - 90.0
    return lat, lon


def gtile_to_quadkey(x: int, y: int, zl: int) -> str:
    """Encode a slippy-map tile index as a Bing quadkey (one base-4 digit per level)."""
    if not (0 <= x < 2**zl and 0 <= y < 2**zl):
        raise ValueError(f"tile ({x}, {y}) out of range for zoom {zl}")
    digits = []
    for level in range(zl - 1, -1, -1):
        mask = 1 << level
        digits.append(str(((x & mask) and 1) + ((y & mask) and 2)))
    return "".join(digits)


def quadkey_to_gtile(quadkey: str) -> tuple[int, int, int]:
    """Decode a quadkey into (x, y, zl)."""
    x = y = 0
    for digit in quadkey:
        d = int(digit)
        if d not in (0, 1, 2, 3):
            raise ValueError(f"bad quadkey digit {digit!r}")
        x = (x << 1) | (d & 1)
        y = (y << 1) | (d >> 1)
    return x, y, len(quadkey)


def cell_tile_range(lat: int, lon: int, zl: int) -> tuple[range, range]:
    """Tile index ranges (xs, ys) covering the 1 degree cell whose SW corner is (lat, lon)."""
    x0, y_south = wgs84_to_gtile(lat, lon, zl)
    x1, y_north = wgs84_to_gtile(lat + 1, lon + 1, zl)
    # The tile containing the exact east/north border belongs to the neighbour cell.
    return range(x0, x1), range(y_north, y_south)


def texture_aligned_range(xs: range, ys: range) -> tuple[range, range]:
    """Expand tile ranges to whole 16 x 16 textures, as Ortho4XP downloads them."""
    ax = range(xs.start // TEXTURE_TILES * TEXTURE_TILES, -(-xs.stop // TEXTURE_TILES) * 16)
    ay = range(ys.start // TEXTURE_TILES * TEXTURE_TILES, -(-ys.stop // TEXTURE_TILES) * 16)
    return ax, ay


def iter_cell_tiles(
    lat: int,
    lon: int,
    zl: int,
    *,
    texture_order: bool = True,
    texture_aligned: bool = False,
) -> Iterator[tuple[int, int]]:
    """Yield the (x, y) tiles of a 1 degree cell.

    With ``texture_order`` the tiles are grouped texture by texture (16 x 16 blocks, row-major
    inside each block), which is the access pattern of a real build. Otherwise plain row-major.
    With ``texture_aligned`` the ranges are widened to whole textures, which is what Ortho4XP
    downloads (its textures are anchored on multiples of 16 tiles and overlap the neighbours).
    """
    xs, ys = cell_tile_range(lat, lon, zl)
    if texture_aligned:
        xs, ys = texture_aligned_range(xs, ys)
    if not texture_order:
        for y in ys:
            for x in xs:
                yield x, y
        return
    for ty in range(ys.start // TEXTURE_TILES, -(-ys.stop // TEXTURE_TILES)):
        for tx in range(xs.start // TEXTURE_TILES, -(-xs.stop // TEXTURE_TILES)):
            for y in range(ty * TEXTURE_TILES, (ty + 1) * TEXTURE_TILES):
                if y not in ys:
                    continue
                for x in range(tx * TEXTURE_TILES, (tx + 1) * TEXTURE_TILES):
                    if x in xs:
                        yield x, y


def cell_quadkeys(lat: int, lon: int, zl: int, *, texture_aligned: bool = False) -> list[str]:
    """Quadkeys of all tiles of a 1 degree cell, in texture order."""
    tiles = iter_cell_tiles(lat, lon, zl, texture_aligned=texture_aligned)
    return [gtile_to_quadkey(x, y, zl) for x, y in tiles]


def main() -> None:
    """Write the quadkeys of one cell to a text file, one per line."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lat", type=int, required=True, help="south latitude of the cell")
    ap.add_argument("--lon", type=int, required=True, help="west longitude of the cell")
    ap.add_argument("--zl", type=int, default=16)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--texture-aligned",
        action="store_true",
        help="widen to whole 16 x 16 textures like Ortho4XP (56 576 tiles for +43+005)",
    )
    args = ap.parse_args()
    keys = cell_quadkeys(args.lat, args.lon, args.zl, texture_aligned=args.texture_aligned)
    args.out.write_text("\n".join(keys) + "\n")
    xs, ys = cell_tile_range(args.lat, args.lon, args.zl)
    if args.texture_aligned:
        xs, ys = texture_aligned_range(xs, ys)
    print(
        f"cell {args.lat:+d}{args.lon:+d} ZL{args.zl}: x {xs.start}..{xs.stop - 1} "
        f"({len(xs)} cols), y {ys.start}..{ys.stop - 1} ({len(ys)} rows), "
        f"{len(keys)} tiles -> {args.out}"
    )


if __name__ == "__main__":
    main()
