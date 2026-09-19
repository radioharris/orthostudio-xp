"""Reading elevation files, assembling the 3x3 raster, filling voids, upsampling, smoothing.

Spec: ``docs/specs/dem.md`` sections 4 to 7 and 8.3.
Origin: Ortho4XP ``src/O4_DEM_Utils.py`` -- ``build_combined_raster`` (350-441),
``read_elevation_from_file`` (441-593), ``fill_nodata_values_with_nearest_neighbor`` (866-910),
``upsample`` (910-956), ``smoothen`` (956-1002) -- and ``O4_Airport_Utils.py:924-1034`` for
:func:`smooth_over_regions`.

Every function here is pure (no network, no configuration): downloading is
:mod:`orthostudio.dem.sources`, the artefact is :mod:`orthostudio.dem.rule`.
"""

from __future__ import annotations

import array
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, NamedTuple

import numpy as np
from numpy.typing import NDArray

from orthostudio.dem.sources import (
    CellState,
    EnsureOptions,
    EnsureResult,
    cells_of_block,
    ensure_elevation,
    hem_latlon,
)
from orthostudio.dem.xplane import XP12_SOURCE, read_elevation_posts
from orthostudio.errors import OsxpError

__all__ = [
    "GEOMETRY",
    "NODATA",
    "PLACEMENTS",
    "UNREADABLE_CODES",
    "CombinedRaster",
    "FillNodata",
    "Geometry",
    "RasterRead",
    "Region",
    "build_combined_raster",
    "fill_nodata_nearest",
    "nodata_to_zero",
    "read_elevation_from_file",
    "resample_posts",
    "shared_border",
    "smooth_over_regions",
    "smoothen",
    "upsample_1201_to_3601",
    "world_tiles",
]

F32 = NDArray[np.float32]

NODATA = -32768.0
"""The one nodata value OrthoStudio XP carries; every reader normalises to it (``:517-524``)."""

FillNodata = Literal["nearest", "zero", "none"]
"""Spec section 7. Ortho4XP's tri-state, named."""

MAX_FILL_STEPS = 20
MAX_FILL_PIXELS = 10000

DATA_DIR = Path(__file__).resolve().parent / "data"


class Geometry(NamedTuple):
    """Shape and window of a combined raster (``O4_DEM_Utils.py:355-374``)."""

    base: int
    overlap: int
    beyond: int
    x0: float
    x1: float
    n: int


def _view_geometry() -> Geometry:
    return Geometry(base=3601, overlap=1, beyond=36, x0=-0.01, x1=1.01, n=3601 + 72)


def _alos_geometry() -> Geometry:
    eps = 1 / 7200
    return Geometry(base=3600, overlap=0, beyond=36, x0=-0.01 + eps, x1=1.01 - eps, n=3600 + 72)


GEOMETRY: dict[str, Geometry] = {
    "View": _view_geometry(),
    "SRTM": _view_geometry(),
    "ALOS": _alos_geometry(),
    "COP30": _alos_geometry(),
    XP12_SOURCE: _view_geometry(),
}
"""Per-source geometry of the 3x3 assembly. ``x0 == y0`` and ``x1 == y1`` in every case.
``XP12`` (OrthoStudio XP only) shares ``View``'s: its posts are those of a 3" ``.hgt`` cell;
``COP30`` shares ``ALOS``'s, 3600 posts at the centre of each arc-second cell."""

UNREADABLE_CODES = frozenset(
    {"DEM_FILE_UNREADABLE", "DEM_RASTER_LIBRARY_MISSING", "DEM_EPSG_UNSUPPORTED"}
)
"""Codes with which :func:`read_elevation_from_file` gives up on a file and returns zeros."""


class RasterRead(NamedTuple):
    """What Ortho4XP's ``read_elevation_from_file`` returns, named.

    ``alt_dem`` is ``None`` when the call was ``info_only``. The window is tile-local:
    ``x = lon - tile.lon`` in ``[x0, x1]``, ``y = lat - tile.lat`` in ``[y0, y1]``, and the
    array runs north to south, west to east.
    """

    epsg: int
    x0: float
    y0: float
    x1: float
    y1: float
    nodata: float
    nxdem: int
    nydem: int
    alt_dem: F32 | None


# -- the world bitmap ------------------------------------------------------------------------


@lru_cache(maxsize=1)
def world_tiles() -> NDArray[np.uint8]:
    """``world_tiles.png``: 180x360, non-zero where a 1-degree cell has land.

    Copied verbatim from Ortho4XP's ``Utils/`` directory into this package. Indexing is
    Ortho4XP's:
    ``world_tiles()[89 - lat, (180 + lon) % 360]``.
    """
    from PIL import Image

    with Image.open(DATA_DIR / "world_tiles.png") as im:
        return np.array(im.convert("L"), dtype=np.uint8)


def cell_has_land(lat: int, lon: int) -> bool:
    """``O4_DEM_Utils.py:381-383``."""
    tiles = world_tiles()
    y = 89 - lat
    x = (180 + lon) % 360
    if not (0 <= y < tiles.shape[0]):
        return False
    return bool(tiles[y, x])


# -- reading ---------------------------------------------------------------------------------


def read_elevation_from_file(
    path: Path,
    lat: int,
    lon: int,
    *,
    info_only: bool = False,
    base_if_error: int = 3601,
    on_event: Callable[[OsxpError], None] | None = None,
) -> RasterRead:
    """Read one elevation file into a float32 array (``O4_DEM_Utils.py:441-593``).

    An unreadable file degrades to zeros on a ``base_if_error`` square with the unit window,
    exactly as Ortho4XP does; the reason is reported through ``on_event`` instead of being
    printed.
    """
    suffix = path.suffix.lower()
    try:
        if suffix == ".hgt":
            return _read_hgt(path, info_only=info_only)
        if suffix == ".raw":
            return _read_raw(path, info_only=info_only)
        return _read_gdal_like(path, lat, lon, info_only=info_only, on_event=on_event)
    except OsxpError as err:
        if on_event is not None:
            on_event(err)
        return _zero_read(base_if_error, info_only=info_only)
    except Exception as err:  # Ortho4XP degrades on any failure whatsoever (:463-475)
        if on_event is not None:
            on_event(OsxpError("DEM_FILE_UNREADABLE", context={"path": path, "reason": repr(err)}))
        return _zero_read(base_if_error, info_only=info_only)


def _zero_read(n: int, *, info_only: bool) -> RasterRead:
    alt = None if info_only else np.zeros((n, n), dtype=np.float32)
    return RasterRead(4326, 0.0, 0.0, 1.0, 1.0, NODATA, n, n, alt)


def _square_side(path: Path) -> int:
    return round(math.sqrt(path.stat().st_size / 2))


def _read_hgt(path: Path, *, info_only: bool) -> RasterRead:
    """Big-endian int16, square, north-up (``:443-475``)."""
    n = _square_side(path)
    alt: F32 | None = None
    if not info_only:
        alt = np.fromfile(path, dtype=">i2").astype(np.float32).reshape((n, n))
    if n == 1201:
        n = 3601
        if alt is not None:
            fill_nodata_nearest(alt, NODATA)
            alt = upsample_1201_to_3601(alt)
    return RasterRead(4326, 0.0, 0.0, 1.0, 1.0, NODATA, n, n, alt)


def _read_raw(path: Path, *, info_only: bool) -> RasterRead:
    """Host-order int16, square, **south-up** (``:476-500``: the ``[::-1]``)."""
    n = _square_side(path)
    alt: F32 | None = None
    if not info_only:
        buf = array.array("h")
        with path.open("rb") as f:
            buf.fromfile(f, n * n)
        alt = np.asarray(buf, dtype=np.float32).reshape((n, n))[::-1]
    return RasterRead(4326, 0.0, 0.0, 1.0, 1.0, NODATA, n, n, alt)


MAX_POINTS = 400_000_000
"""What a raster may hold and still be read whole: 1.6 GB of floats. Beyond it the file is refused
with a plain reason rather than filling the memory (a national model at 10 m holds twice that)."""


def _read_gdal_like(
    path: Path,
    lat: int,
    lon: int,
    *,
    info_only: bool,
    on_event: Callable[[OsxpError], None] | None,
) -> RasterRead:
    """GeoTIFF through Pillow and the raw TIFF tags (spec section 5.1, a **fix**)."""
    from PIL import Image, TiffImagePlugin

    # Pillow refuses a very large image outright, to guard against a bomb. A national elevation
    # model is legitimately that large (Switzerland at 10 m: 851 million points), and its header is
    # what tells us whether it can be used at all: the guard is lifted to read the header, and
    # MAX_POINTS below decides what is read whole.
    guard = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = None
        im = Image.open(path)
    except Exception as err:
        raise OsxpError("DEM_FILE_UNREADABLE", context={"path": path, "reason": repr(err)}) from err
    finally:
        Image.MAX_IMAGE_PIXELS = guard
    with im:
        if not isinstance(im, TiffImagePlugin.TiffImageFile):
            raise OsxpError("DEM_RASTER_LIBRARY_MISSING", context={"path": path})
        tags = im.tag_v2
        nxdem, nydem = im.size
        geo = _geotransform(tags, path)
        epsg = _epsg(tags, path, on_event)
        nodata = _nodata(tags, path, on_event)
        # Before reading a single pixel: a raster in another projection is refused, and some of
        # them are enormous. A national model at 10 m (Switzerland, 914 MB compressed) would have
        # been decompressed to 3.4 GB of floats first, only to be turned down (2026-09-19).
        if epsg not in (4326, 4269):
            raise OsxpError("DEM_EPSG_UNSUPPORTED", context={"path": path, "epsg": epsg})
        if not info_only and nxdem * nydem > MAX_POINTS:
            raise OsxpError(
                "DEM_FILE_UNREADABLE",
                context={
                    "path": path,
                    "reason": f"{nxdem * nydem / 1e6:.0f} million points is more than this "
                    f"version reads whole ({MAX_POINTS / 1e6:.0f} million)",
                },
            )
        alt: F32 | None = None
        if not info_only:
            try:
                alt = np.array(im, dtype=np.float32)
            except Exception as err:
                raise OsxpError("DEM_RASTER_LIBRARY_MISSING", context={"path": path}) from err
            if alt.ndim == 3:
                alt = alt[:, :, 0]
            if nodata != NODATA:
                alt[alt == np.float32(nodata)] = np.float32(NODATA)
    x0 = geo[0] + 0.5 * geo[1] - lon
    y1 = geo[3] + 0.5 * geo[5] - lat
    x1 = x0 + (nxdem - 1) * geo[1]
    y0 = y1 + (nydem - 1) * geo[5]
    return RasterRead(epsg, x0, y0, x1, y1, NODATA, nxdem, nydem, alt)


def _geotransform(tags: object, path: Path) -> tuple[float, float, float, float, float, float]:
    """GDAL's six-value transform from ``ModelPixelScaleTag`` and ``ModelTiepointTag``."""
    scale = _tag(tags, 33550)
    tie = _tag(tags, 33922)
    if scale is None or tie is None or len(scale) < 2 or len(tie) < 6:
        raise OsxpError(
            "DEM_FILE_UNREADABLE",
            context={"path": path, "reason": "no ModelPixelScale/ModelTiepoint tag"},
        )
    return (
        float(tie[3]) - float(tie[0]) * float(scale[0]),
        float(scale[0]),
        0.0,
        float(tie[4]) + float(tie[1]) * float(scale[1]),
        0.0,
        -float(scale[1]),
    )


def _epsg(tags: object, path: Path, on_event: Callable[[OsxpError], None] | None) -> int:
    keys = _tag(tags, 34735)
    if keys is not None and len(keys) >= 4:
        values = [int(v) for v in keys]  # type: ignore[call-overload]
        for i in range(4, len(values) - 3, 4):
            key_id, location, count, value = values[i : i + 4]
            if key_id in (2048, 3072) and location == 0 and count == 1:
                return value
    if on_event is not None:
        on_event(OsxpError("DEM_EPSG_UNDECLARED", context={"path": path}))
    return 4326


def _nodata(tags: object, path: Path, on_event: Callable[[OsxpError], None] | None) -> float:
    raw = _tag(tags, 42113)
    if raw is not None:
        text = raw if isinstance(raw, str) else (raw[0] if len(raw) else "")
        try:
            return float(text)
        except (TypeError, ValueError):
            pass
    if on_event is not None:
        on_event(OsxpError("DEM_NODATA_UNDECLARED", context={"path": path}))
    return NODATA


def _tag(tags: object, number: int) -> Sequence[float] | str | None:
    value = tags.get(number) if hasattr(tags, "get") else None  # type: ignore[union-attr]
    if value is None:
        return None
    if isinstance(value, str | bytes):
        return value.decode("ascii", "replace") if isinstance(value, bytes) else value
    if isinstance(value, int | float):
        return [float(value)]
    return [float(v) for v in value]


# -- voids ------------------------------------------------------------------------------------


def fill_nodata_nearest(alt_dem: F32, nodata: float = NODATA) -> bool:
    """Fill voids by iterated 4-neighbour dilation, in place (``:866-910``).

    Returns False without touching the array when it holds at least
    :data:`MAX_FILL_PIXELS` voids (the caller then zeroes everything, as Ortho4XP does). After
    :data:`MAX_FILL_STEPS` dilations the remaining voids are set to 0.
    """
    step = 0
    while bool((alt_dem == nodata).any()):
        if not step and int(np.sum(alt_dem == nodata)) >= MAX_FILL_PIXELS:
            return False
        alt10 = np.roll(alt_dem, 1, axis=0)
        alt10[0] = alt_dem[0]
        alt20 = np.roll(alt_dem, -1, axis=0)
        alt20[-1] = alt_dem[-1]
        alt01 = np.roll(alt_dem, 1, axis=1)
        alt01[:, 0] = alt_dem[:, 0]
        alt02 = np.roll(alt_dem, -1, axis=1)
        alt02[:, -1] = alt_dem[:, -1]
        merge = np.maximum if nodata < 0 else np.minimum
        atemp = merge(alt10, alt20)
        atemp = merge(atemp, alt01)
        atemp = merge(atemp, alt02)
        holes = alt_dem == nodata
        alt_dem[holes] = atemp[holes]
        step += 1
        if step > MAX_FILL_STEPS:
            alt_dem[alt_dem == nodata] = 0
            break
    return True


def nodata_to_zero(alt_dem: F32, nodata: float = NODATA) -> int:
    """Set every void to 0 m and return how many there were (``DEM.nodata_to_zero``, :166``)."""
    holes = alt_dem == nodata
    count = int(holes.sum())
    if count:
        alt_dem[holes] = 0
    return count


# -- upsampling ---------------------------------------------------------------------------------


def upsample_1201_to_3601(alt_dem: F32) -> F32:
    """3x piecewise-bilinear refinement of a 1201 raster (``:910-956``), vectorised.

    Bit-identical to Ortho4XP: the coefficients are Python floats, so every product is computed in
    float64 and rounded once on the store into the float32 result, in both versions.
    """
    if alt_dem.shape != (1201, 1201):
        raise ValueError(f"upsample expects a 1201x1201 raster, got {alt_dem.shape}")
    out = np.zeros((3601, 3601), dtype=np.float32)
    a = alt_dem
    left, right = a[:, :-1], a[:, 1:]
    top, bottom = a[:-1, :], a[1:, :]
    tl, tr = a[:-1, :-1], a[:-1, 1:]
    bl, br = a[1:, :-1], a[1:, 1:]
    # rows carrying a source sample
    out[::3, ::3] = a
    out[::3, 1::3] = 2 / 3 * left + 1 / 3 * right
    out[::3, 2::3] = 1 / 3 * left + 2 / 3 * right
    # the two interpolated rows between each pair of source rows
    out[1::3, ::3] = 2 / 3 * top + 1 / 3 * bottom
    out[2::3, ::3] = 1 / 3 * top + 2 / 3 * bottom
    out[1::3, 1::3] = 4 / 9 * tl + 2 / 9 * tr + 2 / 9 * bl + 1 / 9 * br
    out[2::3, 1::3] = 2 / 9 * tl + 1 / 9 * tr + 4 / 9 * bl + 2 / 9 * br
    out[1::3, 2::3] = 2 / 9 * tl + 4 / 9 * tr + 1 / 9 * bl + 2 / 9 * br
    out[2::3, 2::3] = 1 / 9 * tl + 2 / 9 * tr + 2 / 9 * bl + 4 / 9 * br
    return out


# -- the 3x3 assembly ---------------------------------------------------------------------------


def _dst_slices(dlat: int, dlon: int, by: int) -> tuple[slice, slice]:
    rows = {1: slice(0, by), 0: slice(by, -by), -1: slice(-by, None)}[dlat]
    cols = {-1: slice(0, by), 0: slice(by, -by), 1: slice(-by, None)}[dlon]
    return rows, cols


def _src_slices(dlat: int, dlon: int, by: int, ov: int) -> tuple[slice, slice]:
    far = slice(-ov - by, -ov) if ov else slice(-by, None)
    near = slice(ov, ov + by) if ov else slice(0, by)
    rows = {1: far, 0: slice(None), -1: near}[dlat]
    cols = {-1: far, 0: slice(None), 1: near}[dlon]
    return rows, cols


PLACEMENTS: tuple[tuple[int, int], ...] = tuple(
    (dlat, dlon) for dlat in (0, -1, 1) for dlon in (0, -1, 1)
)
"""``(dlat, dlon)`` of the nine cells, in Ortho4XP's ``itertools.product`` order (``:379``)."""


@dataclass(frozen=True, slots=True)
class CombinedRaster:
    """The assembled raster plus what each of the nine cells contributed."""

    read: RasterRead
    cells: tuple[EnsureResult, ...]

    @property
    def alt_dem(self) -> F32:
        assert self.read.alt_dem is not None
        return self.read.alt_dem


def build_combined_raster(
    source: str,
    lat: int,
    lon: int,
    opts: EnsureOptions,
    *,
    info_only: bool = False,
    on_event: Callable[[OsxpError], None] | None = None,
) -> CombinedRaster:
    """Assemble the 3x3 block into one raster overflowing the tile by 36 pixels (``:350-441``).

    A cell that ``world_tiles`` marks as open sea, or that could not be made available,
    contributes zeros -- Ortho4XP's behaviour, kept, because the alternative (extrapolating the
    tile's own border) would change every coastal mesh. Such a cell is reported ``MISSING``
    (an unreadable file too, a **fix**: Ortho4XP counted it as available), and
    :meth:`orthostudio.dem.dem.Dem.build` refuses a tile whose *own* cell is missing.
    """
    try:
        geom = GEOMETRY[source]
    except KeyError:
        raise ValueError(f"{source!r} is not assembled from neighbours") from None
    base, ov, by, n = geom.base, geom.overlap, geom.beyond, geom.n
    read_info = RasterRead(4326, geom.x0, geom.x0, geom.x1, geom.x1, NODATA, n, n, None)
    if info_only:
        return CombinedRaster(read_info, ())
    alt_dem = np.zeros((n, n), dtype=np.float32)

    def place(where: tuple[int, int], cell_array: F32) -> None:
        dst_rows, dst_cols = _dst_slices(where[0], where[1], by)
        src_rows, src_cols = _src_slices(where[0], where[1], by, ov)
        alt_dem[dst_rows, dst_cols] = cell_array[src_rows, src_cols]

    cells: list[EnsureResult] = []
    if source == XP12_SOURCE:
        cells, posts = _xp12_block(lat, lon, opts, on_event)
        for where in PLACEMENTS:  # one refined cell in memory at a time
            if where in posts:
                place(where, resample_posts(posts[where], base))
    else:
        for where, (lat0, lon0) in zip(PLACEMENTS, cells_of_block(lat, lon), strict=True):
            opts.check_cancelled()  # between two cells of the 3x3 block (review 4, finding C3)
            result, cell_array = _cell_array(source, lat0, lon0, base, opts, on_event)
            cells.append(result)
            place(where, cell_array)
    return CombinedRaster(read_info._replace(alt_dem=alt_dem), tuple(cells))


def _cell_array(
    source: str,
    lat0: int,
    lon0: int,
    base: int,
    opts: EnsureOptions,
    on_event: Callable[[OsxpError], None] | None,
) -> tuple[EnsureResult, F32]:
    zeros = np.zeros((base, base), dtype=np.float32)
    if not cell_has_land(lat0, lon0):
        if on_event is not None:
            on_event(OsxpError("DEM_CELL_ASSUMED_OCEAN", context={"cell": hem_latlon(lat0, lon0)}))
        return EnsureResult(lat0, lon0, CellState.OCEAN), zeros
    result = ensure_elevation(source, lat0, lon0, opts)
    if not result.ok or result.path is None:
        if on_event is not None:
            on_event(
                OsxpError(
                    "DEM_NEIGHBOUR_UNAVAILABLE",
                    context={"cell": result.cell, "reason": result.detail},
                )
            )
        return result, zeros
    failures: list[str] = []

    def note(err: OsxpError) -> None:
        if err.code in UNREADABLE_CODES:
            failures.append(err.code)
        if on_event is not None:
            on_event(err)

    read = read_elevation_from_file(result.path, lat0, lon0, base_if_error=base, on_event=note)
    alt = _to_base_columns(read.alt_dem, base)
    if alt is None or alt.shape != (base, base):
        note(
            OsxpError(
                "DEM_FILE_UNREADABLE",
                context={
                    "path": result.path,
                    "reason": f"expected a {base}x{base} raster, got "
                    f"{None if alt is None else alt.shape}",
                },
            )
        )
    if failures:
        unreadable = f"the file is unreadable ({failures[0]})"
        return EnsureResult(
            lat0, lon0, CellState.MISSING, result.path, result.url, unreadable
        ), zeros
    assert alt is not None
    return result, alt


def _to_base_columns(alt: F32 | None, base: int) -> F32 | None:
    """A cell sampled more coarsely in longitude, brought back to ``base`` columns.

    Copernicus GLO-30 keeps about 30 m on the ground rather than one arc-second: from 50° of
    latitude its cells carry 2400 columns instead of 3600, then 1800, 1200, 720 and 360 further
    north (and the same to the south). The rows stay 3600. Read as they come, such a cell was
    called unreadable and **the tile was refused**: nobody above 50° could build with Copernicus,
    nor with Canada's lidar, which is laid over it (a user in Alberta, twice, 2026-09-18 and
    2026-09-19).

    The columns are interpolated onto the grid the 3x3 block assembles. It invents no detail: at
    53° a 1.5" step in longitude is 28 m on the ground against 31 m for one second of latitude, so
    the cell is square in metres and the finer grid only restores the shape the assembly expects.
    """
    if alt is None or alt.ndim != 2:
        return alt
    rows, cols = alt.shape
    if cols == base or rows != base or cols < 2 or cols > base:
        return alt
    # Posts at the centre of each cell of the product (``GEOMETRY``): (i + 0.5) / cols. Outside
    # the first and the last post, the edge value holds rather than a line drawn beyond the data.
    pos = np.clip((np.arange(base, dtype=np.float64) + 0.5) * cols / base - 0.5, 0.0, cols - 1)
    left = np.floor(pos).astype(np.int64)
    right = np.minimum(left + 1, cols - 1)
    weight = (pos - left).astype(np.float32)
    return (alt[:, left] * (1.0 - weight) + alt[:, right] * weight).astype(np.float32)


def _xp12_block(
    lat: int,
    lon: int,
    opts: EnsureOptions,
    on_event: Callable[[OsxpError], None] | None,
) -> tuple[list[EnsureResult], dict[tuple[int, int], F32]]:
    """The cells of X-Plane 12's relief (:data:`PLACEMENTS` order) and the posts of those read.

    The tile's border posts are already :func:`shared_border` posts, so that two adjacent tiles
    built by OrthoStudio XP meet on the same line. Posts are 1201 x 1201, about 6 MB a cell.
    """
    cells: list[EnsureResult] = []
    posts: dict[tuple[int, int], F32] = {}
    for (dlat, dlon), (lat0, lon0) in zip(PLACEMENTS, cells_of_block(lat, lon), strict=True):
        opts.check_cancelled()  # between two cells of the 3x3 block (review 4, finding C3)
        result, cell_posts = _xp12_posts(lat0, lon0, opts, on_event)
        cells.append(result)
        if cell_posts is not None:
            posts[(dlat, dlon)] = cell_posts
    if (0, 0) in posts:
        posts[(0, 0)] = shared_border(posts)
    return cells, posts


def _xp12_posts(
    lat0: int,
    lon0: int,
    opts: EnsureOptions,
    on_event: Callable[[OsxpError], None] | None,
) -> tuple[EnsureResult, F32 | None]:
    """The void-filled posts of one cell of X-Plane 12's relief (``orthostudio.dem.xplane``).

    A DSF that exists wins over ``world_tiles`` (X-Plane knows islands the bitmap does not);
    without one, a sea cell is ``OCEAN`` and a land cell ``MISSING``; both contribute zeros.
    """
    cell = hem_latlon(lat0, lon0)
    path = opts.xp12_dsf(lat0, lon0)
    if path is None:
        if not cell_has_land(lat0, lon0):
            if on_event is not None:
                on_event(OsxpError("DEM_CELL_ASSUMED_OCEAN", context={"cell": cell}))
            return EnsureResult(lat0, lon0, CellState.OCEAN), None
        detail = "X-Plane 12 Global Scenery has no DSF for this cell"
        if on_event is not None:
            on_event(
                OsxpError("DEM_NEIGHBOUR_UNAVAILABLE", context={"cell": cell, "reason": detail})
            )
        return EnsureResult(lat0, lon0, CellState.MISSING, None, None, detail), None
    try:
        posts = read_elevation_posts(path, lat0, lon0)
    except OsxpError as err:
        if on_event is not None:
            on_event(err)
        detail = f"the Global Scenery DSF is unusable ({err.code})"
        return EnsureResult(lat0, lon0, CellState.MISSING, path, None, detail), None
    if not fill_nodata_nearest(posts, NODATA):
        nodata_to_zero(posts, NODATA)
    return EnsureResult(lat0, lon0, CellState.LOCAL, path), posts


_EDGES: tuple[tuple[tuple[int, int], tuple[Any, Any], tuple[Any, Any]], ...] = (
    ((1, 0), np.s_[0, :], np.s_[-1, :]),  # north neighbour: its south row is our north row
    ((-1, 0), np.s_[-1, :], np.s_[0, :]),
    ((0, -1), np.s_[:, 0], np.s_[:, -1]),
    ((0, 1), np.s_[:, -1], np.s_[:, 0]),
)
_CORNERS: tuple[tuple[tuple[int, int], dict[tuple[int, int], tuple[int, int]]], ...] = (
    ((0, 0), {(0, 0): (0, 0), (1, 0): (-1, 0), (0, -1): (0, -1), (1, -1): (-1, -1)}),
    ((0, -1), {(0, 0): (0, -1), (1, 0): (-1, -1), (0, 1): (0, 0), (1, 1): (-1, 0)}),
    ((-1, 0), {(0, 0): (-1, 0), (-1, 0): (0, 0), (0, -1): (-1, -1), (-1, -1): (0, -1)}),
    ((-1, -1), {(0, 0): (-1, -1), (-1, 0): (0, -1), (0, 1): (-1, 0), (-1, 1): (0, 0)}),
)
"""Each corner post of the tile (north-up ``(row, col)``) and where the tiles sharing it
hold it: ``(dlat, dlon)`` of the tile -> ``(row, col)`` in that tile's posts."""


def shared_border(posts: Mapping[tuple[int, int], F32]) -> F32:
    """The tile's posts (``posts[(0, 0)]``) with each border line replaced by its shared value.

    X-Plane 12's rasters do not always agree where two tiles meet: on the east border of
    ``+46+006``, 186 of the 1201 posts differ from ``+46+007``'s, by up to 31 m, while most borders
    (``+43+005``/``+43+006``, ``+46+005``/``+46+006``) are identical. Two tiles built by
    OrthoStudio XP must meet on one line, so a border post is the mean of the tiles that carry it --
    two along a side, up to four at a corner -- whichever tile is being built. A neighbour without a
    DSF (the sea) does not take part; nor does one whose raster has another shape.
    """
    own = posts[(0, 0)]
    out = own.astype(np.float64)
    for where, mine, theirs in _EDGES:
        other = posts.get(where)
        if other is not None and other.shape == own.shape:
            out[mine] = (own[mine].astype(np.float64) + other[theirs]) / 2
    for corner, holders in _CORNERS:
        values = [
            float(posts[t][rc])
            for t, rc in holders.items()
            if t in posts and posts[t].shape == own.shape
        ]
        out[corner] = math.fsum(values) / len(values)
    return out.astype(np.float32)


def resample_posts(posts: F32, n: int) -> F32:
    """A post-centred raster refined (or reduced) to ``n x n`` posts over the same square.

    1201 -> 3601 goes through :func:`upsample_1201_to_3601`, bit-identical to the ``.hgt``
    path; any other shape is bilinear on the same grid.
    """
    if posts.shape == (n, n):
        return posts
    if posts.shape == (1201, 1201) and n == 3601:
        return upsample_1201_to_3601(posts)
    h, w = posts.shape
    ys = np.linspace(0.0, h - 1.0, n)
    xs = np.linspace(0.0, w - 1.0, n)
    r0 = np.minimum(ys.astype(np.intp), h - 2)
    c0 = np.minimum(xs.astype(np.intp), w - 2)
    fy = (ys - r0)[:, None]
    fx = (xs - c0)[None, :]
    a = posts.astype(np.float64)
    top = a[r0][:, c0] * (1 - fx) + a[r0][:, c0 + 1] * fx
    bottom = a[r0 + 1][:, c0] * (1 - fx) + a[r0 + 1][:, c0 + 1] * fx
    return (top * (1 - fy) + bottom * fy).astype(np.float32)


# -- smoothing -----------------------------------------------------------------------------------


def _triangular_kernel(pix_width: int) -> NDArray[np.float64]:
    """``[1, 2, ..., pix+1, ..., 2, 1] / (pix + 1)^2`` (``:959-962``); sums to 1."""
    kernel = np.array(range(1, 2 * (pix_width + 1)))
    kernel[pix_width + 1 :] = range(pix_width, 0, -1)
    return kernel / (pix_width + 1) ** 2


def _convolve_rows(a: NDArray[np.floating], kernel: NDArray[np.float64]) -> None:
    """In-place separable pass over the rows, exactly as ``:966-968`` does it.

    Kept as a per-row :func:`numpy.convolve` on purpose: a vectorised accumulation over the
    kernel taps sums in a different order and is *not* bit-identical beyond a width of about
    four (measured: 1.5e-05 m on a float32 raster with ``pix_width = 8``).
    """
    pix = (len(kernel) - 1) // 2
    for i in range(len(a)):
        a[i] = np.convolve(a[i], kernel)[pix:-pix]


def smoothen(
    raster: NDArray[np.floating],
    pix_width: int,
    mask: NDArray[np.uint8] | None,
    *,
    preserve_boundary: bool = True,
) -> NDArray[np.floating]:
    """Mask-weighted separable smoothing of a raster (``O4_DEM_Utils.py:956-1002``).

    ``mask`` is a 0-255 image the size of ``raster``: 255 smooths fully, 0 leaves the input
    untouched, in between blends. Returns a new array; ``raster`` is not modified.
    """
    if not pix_width or mask is None:
        return raster
    tmp = np.array(raster)
    mask_array = np.array(mask, dtype=np.float32) / 255
    kernel = _triangular_kernel(pix_width)
    tmp = tmp * mask_array
    tmpw = np.array(mask_array)
    _convolve_rows(tmp, kernel)
    _convolve_rows(tmpw, kernel)
    tmp = tmp.transpose()
    tmpw = tmpw.transpose()
    _convolve_rows(tmp, kernel)
    _convolve_rows(tmpw, kernel)
    tmp = tmp.transpose()
    tmpw = tmpw.transpose()
    inside = mask_array != 0
    tmp[inside] = (
        mask_array[inside] * tmp[inside] / tmpw[inside] + (1 - mask_array[inside]) * raster[inside]
    )
    if preserve_boundary:
        for i in range(pix_width):
            tmp[i] = i / pix_width * tmp[i] + (pix_width - i) / pix_width * raster[i]
            tmp[-i - 1] = i / pix_width * tmp[-i - 1] + (pix_width - i) / pix_width * raster[-i - 1]
        for i in range(pix_width):
            tmp[:, i] = i / pix_width * tmp[:, i] + (pix_width - i) / pix_width * raster[:, i]
            tmp[:, -i - 1] = (
                i / pix_width * tmp[:, -i - 1] + (pix_width - i) / pix_width * raster[:, -i - 1]
            )
    return raster * (mask_array == 0) + tmp * (mask_array != 0)


@dataclass(frozen=True, slots=True)
class Region:
    """One already-rasterised smoothing window (an airport, typically).

    ``mask`` is a 0-255 array of shape ``(rowmax - rowmin + 1, colmax - colmin + 1)``.
    Producing it from OSM geometry is the vector stage's job (spec section 9.2).
    """

    rowmin: int
    rowmax: int
    colmin: int
    colmax: int
    mask: NDArray[np.uint8]
    pix: int


def smooth_over_regions(
    alt_dem: F32,
    regions: Iterable[Region],
    *,
    max_pix: int = 0,
    preserve_boundary: bool = True,
) -> F32:
    """Apply :func:`smoothen` to each region, then re-blend the raster border.

    The pure half of ``O4_Airport_Utils.smooth_raster_over_airports`` (924-1034): same order,
    same in-place semantics on a copy, same final boundary blend over ``max_pix`` rows and
    columns. ``max_pix`` defaults to the largest region width.
    """
    out = np.array(alt_dem, dtype=np.float32)
    regions = list(regions)
    if max_pix <= 0:
        max_pix = max((r.pix for r in regions), default=0)
    if not max_pix:
        return out
    up = np.array(out[:max_pix]) if preserve_boundary else None
    down = np.array(out[-max_pix:]) if preserve_boundary else None
    left = np.array(out[:, :max_pix]) if preserve_boundary else None
    right = np.array(out[:, -max_pix:]) if preserve_boundary else None
    for region in regions:
        if not region.pix or region.colmin >= region.colmax or region.rowmin >= region.rowmax:
            continue
        window = out[region.rowmin : region.rowmax + 1, region.colmin : region.colmax + 1]
        out[region.rowmin : region.rowmax + 1, region.colmin : region.colmax + 1] = smoothen(
            window, region.pix, region.mask, preserve_boundary=False
        )
    if preserve_boundary and up is not None and down is not None:
        assert left is not None and right is not None
        pix = max_pix
        for i in range(pix):
            out[i] = i / pix * out[i] + (pix - i) / pix * up[i]
            out[-i - 1] = i / pix * out[-i - 1] + (pix - i) / pix * down[-i - 1]
        for i in range(pix):
            out[:, i] = i / pix * out[:, i] + (pix - i) / pix * left[:, i]
            out[:, -i - 1] = i / pix * out[:, -i - 1] + (pix - i) / pix * right[:, -i - 1]
    return out
