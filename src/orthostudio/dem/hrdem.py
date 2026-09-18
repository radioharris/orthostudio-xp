"""Canada's lidar relief: the HRDEM mosaic of Natural Resources Canada, read cell by cell.

A user of the X-Plane.Org page asked for other elevation sources, "especially for the US and
Canada" (2026-09-18). The United States have the USGS 3DEP (``dem/sources.py``); Canada has no
national 10 m set, but NRCan serves the **HRDEM mosaic** -- airborne lidar at 1 to 2 m, bare
earth (DTM) -- through a WCS, over the part of the country that has been flown.

Two things make it usable here:

* **Partial coverage.** Outside the flown areas the service answers a raster of nodata, quickly.
  A cheap coarse request over the whole cell says where the lidar is before anything heavy is
  downloaded, and the blocks that have none are never asked for again. A cell with no lidar at
  all is simply absent, and the relief falls back to what it is laid over (Copernicus).
* **Bare earth.** Copernicus GLO-30 is a *surface* model: it carries the tree canopy, so a
  forest reads as a 10-20 m plateau. HRDEM's DTM is the ground itself, which is what a mesh
  under photo scenery wants.

The answer is a big-endian float32 GeoTIFF, uncompressed and tiled, which Pillow reads the tags
of but not the pixels (it decodes the samples in the wrong byte order). The pixels are therefore
read here, with numpy, from the tile offsets the tags give; the cell is written as a plain
``.hgt`` (big-endian int16, ``-32768`` for the voids), the format the raster reader already
knows.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:  # pragma: no cover - import cycle, types only
    from orthostudio.dem.sources import Download

__all__ = [
    "HRDEM_BLOCK",
    "HRDEM_POSTS",
    "HRDEM_PROBE",
    "HRDEM_WCS_URL",
    "HgtVoid",
    "cell_blocks",
    "hrdem_cell",
    "hrdem_url",
    "read_wcs_tiff",
    "write_hgt",
]

HRDEM_WCS_URL = "https://datacube.services.geo.ca/wrapper/ogc/elevation-hrdem-mosaic"
"""The mosaic's WCS (``datacube.services.geo.ca/ows/elevation`` redirects here). Coverages:
``dtm`` (bare earth, what a mesh wants) and ``dsm``."""

HRDEM_POSTS = 3601
"""Posts a side of a written cell: one arc-second, the grid of a ``.hgt`` and of Copernicus.

The lidar is far finer, but a cell at 1/3" would be 466 MB from the service and 233 MB on the
disk. At 1" the gain is already the one that counts: bare earth, measured, instead of a radar
surface with the trees on it.
"""

HRDEM_BLOCK = 901
"""Posts a side of one request: 4x4 blocks a cell, ~3 MB and a few seconds each (measured
2026-09-18: 1000x1000 posts in 2.7 s). Blocks share their edge posts, which carry the same
heights."""

HRDEM_PROBE = 128
"""Posts a side of the coverage request: 64 KB, one round trip, and it says which blocks are
worth asking for."""

HgtVoid = -32768
"""The void of a ``.hgt``, which is also ``raster.NODATA``."""


@dataclass(frozen=True, slots=True)
class Block:
    """One request: the posts ``[x0, x0 + n)`` east and ``[y0, y0 + n)`` south of the cell's
    north-west corner, and the degrees they cover."""

    x0: int
    y0: int
    n: int
    lat0: float
    lon0: float
    lat1: float
    lon1: float


def cell_blocks(
    lat: int, lon: int, posts: int = HRDEM_POSTS, side: int = HRDEM_BLOCK
) -> list[Block]:
    """The blocks a cell is asked for, left to right and north to south."""
    starts = list(range(0, posts - 1, side - 1))
    out: list[Block] = []
    for y0 in starts:
        for x0 in starts:
            n = min(side, posts - max(x0, y0))
            out.append(
                Block(
                    x0=x0,
                    y0=y0,
                    n=n,
                    lat0=lat + 1 - (y0 + n - 1) / (posts - 1),
                    lon0=lon + x0 / (posts - 1),
                    lat1=lat + 1 - y0 / (posts - 1),
                    lon1=lon + (x0 + n - 1) / (posts - 1),
                )
            )
    return out


def hrdem_url(lat0: float, lon0: float, lat1: float, lon1: float, nx: int, ny: int) -> str:
    """A GetCoverage of the DTM over that window, on a grid of ``nx`` x ``ny`` posts.

    WCS 1.1.1 with the URN form of EPSG:4326, whose axes are **latitude first**: written the
    other way the service builds an inverted extent and answers 500 (measured 2026-09-18).
    """
    dx = (lon1 - lon0) / (nx - 1) if nx > 1 else 0.0
    dy = (lat1 - lat0) / (ny - 1) if ny > 1 else 0.0
    return (
        f"{HRDEM_WCS_URL}?service=WCS&version=1.1.1&request=GetCoverage&identifier=dtm"
        f"&boundingbox={lat0:.9f},{lon0:.9f},{lat1:.9f},{lon1:.9f},urn:ogc:def:crs:EPSG::4326"
        "&format=image/geotiff&gridbasecrs=urn:ogc:def:crs:EPSG::4326"
        f"&gridorigin={lat1:.9f},{lon0:.9f}&gridoffsets={-dy:.12f},{dx:.12f}"
    )


def read_wcs_tiff(body: bytes) -> tuple[NDArray[np.float32], tuple[float, float, float, float]]:
    """The raster of a WCS answer, with ``(lon, lat, dlon, dlat)`` of its first post's centre.

    Pillow reads the tags of these files but decodes their samples in the wrong byte order, so
    the tiles are read here: uncompressed big-endian float32, no predictor (checked on the
    service's own answers). Heights are returned with the voids as :data:`HgtVoid`.
    """
    import io

    from PIL import Image, TiffImagePlugin

    with Image.open(io.BytesIO(body)) as im:
        if not isinstance(im, TiffImagePlugin.TiffImageFile):
            raise ValueError("the elevation service did not answer a TIFF")
        tags: Any = im.tag_v2
        width, height = im.size
        scale = tags.get(33550)
        tie = tags.get(33922)
        if not scale or not tie or len(tie) < 6:
            raise ValueError("the raster carries no ModelPixelScale/ModelTiepoint")
        if int(tags.get(259, 1)) != 1 or int(tags.get(317, 1)) != 1:
            raise ValueError("the raster is compressed or predicted, which this reader does not do")
        if int(tags.get(258, (32,))[0]) != 32 or int(tags.get(339, (3,))[0]) != 3:
            raise ValueError("the raster is not 32-bit floating point")
        nodata = float(tags.get(42113, HgtVoid))
        offsets = tags.get(324)
        tile_w, tile_h = tags.get(322), tags.get(323)
        if offsets is None or tile_w is None or tile_h is None:
            raise ValueError("the raster is not tiled, which this reader does not do")
        tile_w, tile_h = int(tile_w), int(tile_h)
    across = math.ceil(width / tile_w)
    out = np.full((height, width), np.float32(HgtVoid), dtype=np.float32)
    for i, offset in enumerate(offsets):
        tile = np.frombuffer(body, dtype=">f4", count=tile_w * tile_h, offset=int(offset))
        tile = tile.reshape((tile_h, tile_w))
        y0, x0 = (i // across) * tile_h, (i % across) * tile_w
        part = tile[: min(tile_h, height - y0), : min(tile_w, width - x0)]
        out[y0 : y0 + part.shape[0], x0 : x0 + part.shape[1]] = part
    out[~np.isfinite(out)] = np.float32(HgtVoid)
    out[out == np.float32(nodata)] = np.float32(HgtVoid)
    # a lidar mosaic holds no height below the Dead Sea or above Everest: anything else is a void
    out[(out < -500.0) | (out > 9000.0)] = np.float32(HgtVoid)
    # the *centre* of the first post, as the raster reader computes it (``raster._read_gdal_like``):
    # the tie point names a pixel corner, and placing on corners shifts a block half a post
    return out, (
        float(tie[3]) - float(tie[0]) * float(scale[0]) + 0.5 * float(scale[0]),
        float(tie[4]) + float(tie[1]) * float(scale[1]) - 0.5 * float(scale[1]),
        float(scale[0]),
        float(scale[1]),
    )


def hrdem_cell(
    lat: int,
    lon: int,
    download: Callable[[str], Download],
    *,
    posts: int = HRDEM_POSTS,
    side: int = HRDEM_BLOCK,
    probe: int = HRDEM_PROBE,
    check_cancelled: Callable[[], None] | None = None,
    on_block: Callable[[int, int], None] | None = None,
) -> NDArray[np.float32] | None:
    """The cell's heights, or ``None`` when the lidar does not reach it.

    The coverage request comes first: a cell with no lidar costs one small round trip, and a
    cell with a little costs only the blocks that hold it.
    """
    got = download(hrdem_url(lat, lon, lat + 1, lon + 1, probe, probe))
    if not got.ok:
        return None
    coarse, _ = read_wcs_tiff(got.body)
    if not (coarse != HgtVoid).any():
        return None
    blocks = [b for b in cell_blocks(lat, lon, posts, side) if _has_lidar(coarse, b, posts)]
    if not blocks:
        return None
    cell = np.full((posts, posts), np.float32(HgtVoid), dtype=np.float32)
    for done, block in enumerate(blocks, start=1):
        if check_cancelled is not None:
            check_cancelled()
        # One post of margin east and south: the service rounds a window down by a post, which
        # left the seam between blocks empty (measured 2026-09-18).
        margin = 1.0 / (posts - 1)
        url = hrdem_url(
            block.lat0 - margin,
            block.lon0,
            block.lat1,
            block.lon1 + margin,
            block.n + 1,
            block.n + 1,
        )
        answer = download(url)
        if answer.ok:
            part, geo = read_wcs_tiff(answer.body)
            _place(cell, part, geo, lat, lon, posts)
        if on_block is not None:
            on_block(done, len(blocks))
    return cell if (cell != HgtVoid).any() else None


def _has_lidar(coarse: NDArray[np.float32], block: Block, posts: int) -> bool:
    """Whether the coverage request saw any height in that block's part of the cell."""
    n = coarse.shape[0]
    y0 = int(block.y0 / (posts - 1) * (n - 1))
    y1 = max(y0 + 1, int((block.y0 + block.n - 1) / (posts - 1) * (n - 1)) + 1)
    x0 = int(block.x0 / (posts - 1) * (n - 1))
    x1 = max(x0 + 1, int((block.x0 + block.n - 1) / (posts - 1) * (n - 1)) + 1)
    return bool((coarse[y0:y1, x0:x1] != HgtVoid).any())


def _place(
    cell: NDArray[np.float32],
    part: NDArray[np.float32],
    geo: tuple[float, float, float, float],
    lat: int,
    lon: int,
    posts: int,
) -> None:
    """Write an answer into the cell where *it* says it is.

    The window asked for and the window served differ by a post or so, so the answer is placed
    by its own first post rather than by the request: a block that came back shifted would
    otherwise write its heights a post away from the truth.
    """
    lon_west, lat_north, _dlon, _dlat = geo
    x0 = round((lon_west - lon) * (posts - 1))
    y0 = round((lat + 1 - lat_north) * (posts - 1))
    sy, sx = max(0, -y0), max(0, -x0)
    y0, x0 = max(0, y0), max(0, x0)
    h = min(part.shape[0] - sy, posts - y0)
    w = min(part.shape[1] - sx, posts - x0)
    if h <= 0 or w <= 0:
        return
    got = part[sy : sy + h, sx : sx + w]
    np.copyto(cell[y0 : y0 + h, x0 : x0 + w], got, where=got != np.float32(HgtVoid))


def write_hgt(path: Path, cell: NDArray[np.float32]) -> None:
    """Write the cell as a ``.hgt``: big-endian int16, north-up, ``-32768`` for the voids."""
    heights = np.where(cell == np.float32(HgtVoid), np.float32(HgtVoid), np.rint(cell))
    np.clip(heights, HgtVoid, 32767, out=heights)
    tmp = path.with_name(path.name + ".part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(heights.astype(">i2").tobytes())
    tmp.replace(path)
