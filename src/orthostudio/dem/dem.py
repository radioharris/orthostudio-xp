"""The :class:`Dem` object: the raster of one tile and the altitude queries built on it.

Spec: ``docs/specs/dem.md`` section 8.
Origin: Ortho4XP ``src/O4_DEM_Utils.py:37-350`` (class ``DEM``).

The hot method is :meth:`Dem.alt_vec`: the vector stage calls it once per way and the mesh
stage once per vertex block. Ortho4XP evaluates its four raster corners with four Python list
comprehensions over ``zip``; here they are four fancy-indexing gathers, bit-identical and
about sixteen times faster (``docs/specs/dem.md`` section 11).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from orthostudio.dem.raster import (
    GEOMETRY,
    NODATA,
    UNREADABLE_CODES,
    CombinedRaster,
    FillNodata,
    RasterRead,
    build_combined_raster,
    fill_nodata_nearest,
    nodata_to_zero,
    read_elevation_from_file,
)
from orthostudio.dem.sources import (
    MANUAL_SOURCES,
    OWN_SUFFIXES,
    SOURCES,
    CellState,
    EnsureOptions,
    EnsureResult,
    cell_file_in_folder,
    elevation_path,
    ensure_elevation,
    generic_tif,
    hem_latlon,
    manual_download_error,
)
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef

__all__ = [
    "ALT_FILE_FORMAT",
    "META_FORMAT",
    "Dem",
    "alt_file_name",
    "elevation_file",
    "expected_alt_size",
    "own_cell_missing",
    "require_own_cell",
    "resolve_source",
]

F32 = NDArray[np.float32]
F64 = NDArray[np.float64]

META_FORMAT = "osxp-dem-1"
ALT_FILE_FORMAT = "float32 row-major, north to south, no header"

LONG_NAMES: dict[str, str] = {
    "Viewfinderpanoramas (J. de Ferranti) - mostly worldwide": "View",
    "SRTMv3 (from OpenTopography) - NOW REQUIRES MANUAL DOWNLOAD": "SRTM",
    'NED 1" (from USGS) - USA, Canada, Mexico': "NED1",
    'NED 1/3" (from USGS) - USA': "NED1/3",
    "ALOS 3W30 (from OpenTopography) - NOW REQUIRES MANUAL DOWNLOAD": "ALOS",
}
"""Ortho4XP's ``available_sources`` pairs (``O4_DEM_Utils.py:20-31``): long name -> short name."""


def alt_file_name(tile: TileRef) -> str:
    """``Data+43+005.alt`` (``O4_File_Names.py:164-177``)."""
    return f"Data{tile.name}.alt"


def resolve_source(custom_dem: str, tile: TileRef, elevation_dir: Path) -> list[str]:
    """Ortho4XP's ``DEM.load_data`` source resolution (``:71-95``), as a list.

    The first element is the base source (a short source name or a file path), the rest are
    the overlays of a ``"base;overlay1;overlay2"`` composite. An empty ``custom_dem`` means
    ``<base>.tif`` when that file exists, else ``View``.
    """
    text = custom_dem.replace("{latlon}", hem_latlon(tile.lat, tile.lon))
    parts = [p for p in text.split(";")] if text else [""]
    resolved: list[str] = []
    for i, part in enumerate(parts):
        if part:
            resolved.append(LONG_NAMES.get(part, part))
        elif i == 0:
            tif = generic_tif(elevation_dir, tile.lat, tile.lon)
            resolved.append(str(tif) if tif.is_file() else "View")
        else:
            raise ValueError(f"empty overlay in custom_dem {custom_dem!r}")
    return resolved


def _relief_remedy(source: str, cell: str) -> str | None:
    """What to do about a missing elevation, when the relief is a folder or a file of one's own.

    The registry's remedy speaks of the X-Plane relief and ends by telling the user to give his
    own elevation file, which is what he just did: a Linux user pointed at his own folder, the
    build stopped at 0 %, and the advice sent him to the X-Plane installer (found in review,
    2026-09-23). The relief is the first thing a tile needs, so every stage after it is skipped
    and the bar never moves.
    """
    path = Path(source)
    if not (path.is_dir() or path.is_file() or source.startswith(("/", "~", "."))):
        return None  # a named source: the registry's words are right
    kinds = ", ".join(OWN_SUFFIXES)
    if path.is_dir():
        return (
            f"The folder is read for a file named after the square, {cell}, with one of these "
            f"endings: {kinds}. Rename or add that file, or choose another relief above, which "
            "answers for every square the folder has nothing for."
        )
    return (
        f"Check the file is readable and is one of {kinds}, and that it covers {cell}. "
        "Otherwise choose another relief above."
    )


@dataclass(slots=True)
class Dem:
    """One tile's elevation raster and the queries on it.

    ``x``/``y`` are tile-local (``x = lon - tile.lon``); the raster runs north to south. The
    window is ``[x0, x1] x [y0, y1]``, slightly larger than the unit square for an assembled
    source (section 4.1).
    """

    tile: TileRef
    alt_dem: F32
    x0: float
    y0: float
    x1: float
    y1: float
    nxdem: int
    nydem: int
    epsg: int = 4326
    nodata: float = NODATA
    source: str = "View"
    cells: tuple[EnsureResult, ...] = ()
    overlays: tuple[Dem, ...] = ()
    laid_over: tuple[str, ...] = ()
    """The overlays of a composite written into :attr:`alt_dem`, in the order they were laid."""
    events: list[OsxpError] = field(default_factory=list)

    # -- construction ------------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        tile: TileRef,
        opts: EnsureOptions,
        *,
        custom_dem: str = "",
        fill_nodata: FillNodata = "nearest",
        on_event: Callable[[OsxpError], None] | None = None,
    ) -> Dem:
        """Load, assemble and fill the raster of ``tile`` (``DEM.__init__``, ``:38-70``)."""
        events: list[OsxpError] = []

        def record(err: OsxpError) -> None:
            # Copernicus GLO-30 files declare no nodata value, and have none: their voids are
            # filled at the source. Saying so on every cell would be noise (2026-09-17).
            if err.code == "DEM_NODATA_UNDECLARED" and "COP30" in sources:
                return
            events.append(err)
            if on_event is not None:
                on_event(err)

        sources = resolve_source(custom_dem, tile, opts.elevation_dir)
        dem = cls._load_one(tile, sources[0], opts, record)
        assert dem is not None  # the base is never optional: it raises rather than answer None
        dem.events = events
        if fill_nodata == "zero":
            count = nodata_to_zero(dem.alt_dem, dem.nodata)
            dem.nodata = NODATA
            if count:
                record(
                    OsxpError(
                        "DEM_VOIDS_FILLED_WITH_ZERO",
                        context={"count": count, "cell": hem_latlon(tile.lat, tile.lon)},
                    )
                )
        elif fill_nodata == "nearest" and not fill_nodata_nearest(dem.alt_dem, dem.nodata):
            count = nodata_to_zero(dem.alt_dem, dem.nodata)
            dem.nodata = NODATA
            record(
                OsxpError(
                    "DEM_VOIDS_FILLED_WITH_ZERO",
                    context={"count": count, "cell": hem_latlon(tile.lat, tile.lon)},
                )
            )
        overlays = []
        for name in sources[1:]:
            # An overlay is what it can be: Canada's lidar covers the part of the country that
            # has been flown, so a cell without it lays the base alone rather than refusing the
            # tile (a user asked for a Canadian source, 2026-09-18).
            laid = cls._load_one(tile, name, opts, record, optional=True)
            if laid is None:
                record(
                    OsxpError(
                        "DEM_OVERLAY_UNAVAILABLE",
                        context={"cell": hem_latlon(tile.lat, tile.lon), "source": name},
                    )
                )
            else:
                overlays.append((name, laid))
        if overlays:
            # Into the raster, not beside it. Ortho4XP builds a tile in one go and can ask its
            # overlays at every point (``alt_vec_composite``); here the raster is written to the
            # store and read again by the vector and mesh stages, which know nothing of an
            # overlay. Kept beside it, a user's own file was found, keyed and then quietly
            # dropped: the scenery came out of the base alone (found 2026-09-19 on a build of
            # +46+006 whose lidar file changed nothing).
            _lay_into(dem, overlays, record)
        return dem

    @classmethod
    def _load_one(
        cls,
        tile: TileRef,
        source: str,
        opts: EnsureOptions,
        record: Callable[[OsxpError], None],
        *,
        optional: bool = False,
    ) -> Dem | None:
        """One source of the composite. ``optional`` (an overlay) answers ``None`` where the
        source has no data for the tile, instead of refusing the build."""
        if source in GEOMETRY:
            combined: CombinedRaster = build_combined_raster(
                source, tile.lat, tile.lon, opts, on_event=record
            )
            if optional and own_cell_missing(tile, combined.cells):
                return None
            require_own_cell(tile, source, combined.cells, opts.elevation_dir)
            return cls._from_read(tile, combined.read, source, combined.cells)
        if source in SOURCES:
            result = ensure_elevation(source, tile.lat, tile.lon, opts)
            if not result.ok or result.path is None:
                if optional:
                    return None
                # Only the USGS products are read cell by cell (the rest are assembled from a 3x3
                # block), and OrthoStudio XP downloads them: no file means the source does not
                # cover this cell. Telling a pilot over Europe to fetch a USGS file by hand would
                # send him nowhere (a user asked what such a build does, 2026-09-18); the page
                # names the sources that do cover the region.
                raise OsxpError(
                    "DEM_TILE_UNAVAILABLE",
                    context={
                        "cell": hem_latlon(tile.lat, tile.lon),
                        "source": source,
                        "reason": result.detail or "no file for this cell",
                    },
                )
            read = _read_whole_file(result.path, tile, source, record, base_if_error=3601)
            return cls._from_read(tile, read, source, (result,))
        path = Path(source)
        # A folder of one's own files: the one of this square is taken from it, wherever it sits
        # (``cell_file_in_folder``). An overlay finds nothing where the folder has nothing, and the
        # relief under it answers there; a base must have it, as any other base must.
        if path.is_dir():
            own = cell_file_in_folder(path, tile.lat, tile.lon)
            if own is None:
                if optional:
                    return None
                cell = hem_latlon(tile.lat, tile.lon)
                raise OsxpError(
                    "DEM_TILE_UNAVAILABLE",
                    context={
                        "cell": cell,
                        "source": source,
                        "reason": f"no file for this square in {path}",
                    },
                    remedy=_relief_remedy(source, cell),
                )
            path = own
        if optional and not path.is_file():
            return None
        read = _read_whole_file(path, tile, str(path), record)
        return cls._from_read(
            tile,
            read,
            source,
            (EnsureResult(tile.lat, tile.lon, CellState.LOCAL, path),),
        )

    @classmethod
    def _from_read(
        cls, tile: TileRef, read: RasterRead, source: str, cells: tuple[EnsureResult, ...]
    ) -> Dem:
        alt = read.alt_dem
        if alt is None:
            raise ValueError("Dem needs a raster, not an info-only read")
        return cls(
            tile=tile,
            alt_dem=alt,
            x0=read.x0,
            y0=read.y0,
            x1=read.x1,
            y1=read.y1,
            nxdem=read.nxdem,
            nydem=read.nydem,
            epsg=read.epsg,
            nodata=read.nodata,
            source=source,
            cells=cells,
        )

    # -- queries -----------------------------------------------------------------------------

    def alt_vec(self, way: NDArray[np.floating]) -> F64:
        """Altitude at each ``(x, y)`` row of ``way``, in metres.

        Barycentric on the triangulated raster, clamped to the window; overlays of a
        composite source win where they have data (``alt_vec_composite``, ``:329-334``).
        """
        out = self.alt_vec_nostrict(way)
        for overlay in self.overlays:
            other = overlay.alt_vec_strict(way)
            inside = other != overlay.nodata
            out[inside] = other[inside]
        return out

    def alt_vec_nostrict(self, way: NDArray[np.floating]) -> F64:
        """``O4_DEM_Utils.py:283-311``, with fancy indexing instead of list comprehensions."""
        points = np.asarray(way, dtype=np.float64)
        Nx = self.nxdem - 1  # noqa: N806 -- Ortho4XP's names, kept so the formulas read the same
        Ny = self.nydem - 1  # noqa: N806
        x = np.clip(points[:, 0], self.x0, self.x1)
        y = np.clip(points[:, 1], self.y0, self.y1)
        px = (x - self.x0) / (self.x1 - self.x0) * Nx
        py = (y - self.y0) / (self.y1 - self.y0) * Ny
        nx = px.astype(np.uint16)
        Nminusny = Ny - py.astype(np.uint16)  # noqa: N806
        rx = px - nx
        ry = py + Nminusny - Ny
        rows_up = (Nminusny - 1) * (Nminusny >= 1)
        cols_east = (nx + 1) * (nx < Nx) + Nx * (nx == Nx)
        alt = self.alt_dem
        t1 = alt[Nminusny, nx]
        t2 = alt[rows_up, cols_east]
        t3 = alt[Nminusny, cols_east]
        t4 = alt[rows_up, nx]
        lower = (1 - rx) * t1 + ry * t2 + (rx - ry) * t3
        upper = (1 - ry) * t1 + rx * t2 + (ry - rx) * t4
        return lower * (rx >= ry) + upper * (rx < ry)

    def alt_vec_strict(self, way: NDArray[np.floating]) -> F64:
        """Nearest-pixel value, ``nodata`` outside the window (``:313-327``)."""
        points = np.asarray(way, dtype=np.float64)
        x = points[:, 0]
        y = points[:, 1]
        inside = (x >= self.x0) & (x <= self.x1) & (y >= self.y0) & (y <= self.y1)
        nx = np.round((x - self.x0) / (self.x1 - self.x0) * (self.nxdem - 1)).astype(np.uint16)
        ny = np.round((self.y1 - y) / (self.y1 - self.y0) * (self.nydem - 1)).astype(np.uint16)
        nx = np.minimum(nx, self.nxdem - 1)
        ny = np.minimum(ny, self.nydem - 1)
        gathered = self.alt_dem[ny, nx].astype(np.float64)
        return np.where(inside, gathered, np.float64(self.nodata))

    def alt(self, node: Sequence[float]) -> float:
        """Scalar convenience over :meth:`alt_vec` (``alt_nostrict``/``alt_composite``)."""
        return float(self.alt_vec(np.array([[node[0], node[1]]], dtype=np.float64))[0])

    # -- artefact ----------------------------------------------------------------------------

    def write_alt(self, path: Path) -> None:
        """``Data<tile>.alt``: float32, row-major, no header (``DEM.write_to_file``, ``:174``)."""
        self.alt_dem.astype(np.float32).tofile(path)

    def meta(self) -> dict[str, object]:
        """The ``meta.json`` document of the artefact (spec section 9)."""
        return {
            "format": META_FORMAT,
            "tile": self.tile.name,
            "source": self.source,
            "epsg": self.epsg,
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
            "nodata": self.nodata,
            "nxdem": self.nxdem,
            "nydem": self.nydem,
            "min": float(self.alt_dem.min()),
            "max": float(self.alt_dem.max()),
            "mean": float(self.alt_dem.mean()),
            "nodata_pixels": int((self.alt_dem == self.nodata).sum()),
            "alt_layout": ALT_FILE_FORMAT,
            "laid_over": list(self.laid_over),
            "cells": [
                {
                    "cell": c.cell,
                    "state": str(c.state),
                    "path": str(c.path) if c.path is not None else None,
                    "detail": c.detail,
                }
                for c in self.cells
            ],
        }

    def save(self, directory: Path) -> None:
        """Write ``Data<tile>.alt``, ``dem.npy`` and ``meta.json`` into ``directory``."""
        directory.mkdir(parents=True, exist_ok=True)
        self.write_alt(directory / alt_file_name(self.tile))
        np.save(directory / "dem.npy", self.alt_dem, allow_pickle=False)
        (directory / "meta.json").write_text(
            json.dumps(self.meta(), indent=1, sort_keys=True), "utf-8"
        )

    @classmethod
    def load(cls, directory: Path, *, mmap: bool = True) -> Dem:
        """Read back a saved artefact; ``dem.npy`` is memory-mapped by default."""
        meta = json.loads((directory / "meta.json").read_text("utf-8"))
        if meta.get("format") != META_FORMAT:
            raise ValueError(f"{directory / 'meta.json'} is not a {META_FORMAT} document")
        alt = np.load(directory / "dem.npy", mmap_mode="r" if mmap else None, allow_pickle=False)
        return cls(
            tile=TileRef.parse(str(meta["tile"])),
            alt_dem=alt,
            x0=float(meta["x0"]),
            y0=float(meta["y0"]),
            x1=float(meta["x1"]),
            y1=float(meta["y1"]),
            nxdem=int(meta["nxdem"]),
            nydem=int(meta["nydem"]),
            epsg=int(meta["epsg"]),
            nodata=float(meta["nodata"]),
            source=str(meta["source"]),
        )


MAX_COMPOSITE_SIDE = 12_000
"""How fine a composite raster may become, in points a side: a 1/3" overlay over an assembled
window lands just under it (11 013), and 12 000 points is 576 MB of float32, the size the USGS
source already produces for one tile of the United States."""

ROWS_AT_A_TIME = 512
"""Rows of one block of the two grid walks below: a block of a 7 200-point raster is 15 MB."""


def _step_of(dem: Dem) -> float:
    """Degrees between two points of the raster (its window is square)."""
    return (dem.x1 - dem.x0) / (dem.nxdem - 1)


def _file_name(dem: Dem) -> str:
    """The name of the file a one-cell source was read from, for a message that names it."""
    cell = dem.cells[0] if len(dem.cells) == 1 else None
    return cell.path.name if cell is not None and cell.path is not None else ""


def _file_step(dem: Dem) -> float:
    """Degrees between two points of the *file* a one-cell source was read from.

    ``read_elevation_from_file`` refines a 1201 point ``.hgt`` to 3601 as Ortho4XP does, so the
    raster in hand is not the resolution the file was published at: a 3 second file of one's own
    would look as fine as a 1 second source. A ``.hgt`` or a ``.raw`` covers exactly one square,
    so its side is its size; anything else is read at its own resolution and the raster tells the
    truth.
    """
    cell = dem.cells[0] if len(dem.cells) == 1 else None
    path = cell.path if cell is not None else None
    if path is not None and path.suffix.lower() in (".hgt", ".raw"):
        try:
            side = round((path.stat().st_size // 2) ** 0.5)
        except OSError:
            return _step_of(dem)
        if side > 1:
            return 1.0 / (side - 1)
    return _step_of(dem)


def _lay_into(
    base: Dem, overlays: Sequence[tuple[str, Dem]], record: Callable[[OsxpError], None]
) -> None:
    """Write the overlays into the base raster, the last one first in line, in place.

    The finest step in the room wins, both ways. An overlay sharper than the base raises the whole
    window to its own grid, so that a half-second file of one's own is not read at the second of
    the source under it. An overlay *coarser* than the base is left where it is: a file of one's
    own at one second laid over the USGS relief at a third of a second would throw away two
    points out of three, and the American sets of one's own are made from that very source
    (a user asked what happens when he has both, 2026-09-20). The base has been filled by then,
    so nothing interpolates a void.
    """
    under = _file_step(base)
    coarse = [(name, over) for name, over in overlays if _file_step(over) > under * 1.000001]
    for name, over in coarse:
        record(
            OsxpError(
                "DEM_OVERLAY_COARSER",
                context={
                    "cell": hem_latlon(base.tile.lat, base.tile.lon),
                    "own": _file_name(over) or Path(name).name,
                    "own_m": f"{_file_step(over) * 111_320:.0f}",
                    "source": base.source,
                    "base_m": f"{under * 111_320:.0f}",
                },
            )
        )
    overlays = [pair for pair in overlays if pair not in coarse]
    if not overlays:
        base.laid_over = ()
        return
    finest = min(_file_step(dem) for _name, dem in overlays)
    if finest < _step_of(base) and round((base.x1 - base.x0) / finest) + 1 <= MAX_COMPOSITE_SIDE:
        _refine(base, finest)
    laid: list[str] = []
    for name, over in overlays:
        points = _lay_one(base, over)
        if not points:
            record(
                OsxpError(
                    "DEM_OVERLAY_UNAVAILABLE",
                    context={"cell": hem_latlon(base.tile.lat, base.tile.lon), "source": name},
                )
            )
            continue
        laid.append(name)
        base.cells = (*base.cells, *over.cells)
    base.laid_over = tuple(laid)


def _refine(dem: Dem, step: float) -> None:
    """Put the raster on a finer grid of the same window, bilinear, block by block."""
    nx = round((dem.x1 - dem.x0) / step) + 1
    ny = round((dem.y1 - dem.y0) / step) + 1
    src = dem.alt_dem
    out = np.empty((ny, nx), dtype=np.float32)
    fx = np.linspace(0.0, dem.nxdem - 1, nx)
    cols = np.clip(np.floor(fx).astype(np.int64), 0, dem.nxdem - 2)
    dx = (fx - cols).astype(np.float32)
    fy = np.linspace(0.0, dem.nydem - 1, ny)
    rows = np.clip(np.floor(fy).astype(np.int64), 0, dem.nydem - 2)
    dy = (fy - rows).astype(np.float32)
    for start in range(0, ny, ROWS_AT_A_TIME):
        stop = min(start + ROWS_AT_A_TIME, ny)
        rr, down = rows[start:stop], dy[start:stop, None]
        top = src[np.ix_(rr, cols)] * (1 - dx) + src[np.ix_(rr, cols + 1)] * dx
        bottom = src[np.ix_(rr + 1, cols)] * (1 - dx) + src[np.ix_(rr + 1, cols + 1)] * dx
        out[start:stop] = top * (1 - down) + bottom * down
    dem.alt_dem = out
    dem.nxdem, dem.nydem = nx, ny


def _lay_one(base: Dem, over: Dem) -> int:
    """One overlay into the base raster, nearest point, where it has data (``alt_vec_strict``)."""
    ys = np.linspace(base.y1, base.y0, base.nydem)
    xs = np.linspace(base.x0, base.x1, base.nxdem)
    rows = np.nonzero((ys >= over.y0) & (ys <= over.y1))[0]
    cols = np.nonzero((xs >= over.x0) & (xs <= over.x1))[0]
    if rows.size == 0 or cols.size == 0:
        return 0
    from_row = np.clip(
        np.rint((over.y1 - ys[rows]) / (over.y1 - over.y0) * (over.nydem - 1)).astype(np.int64),
        0,
        over.nydem - 1,
    )
    from_col = np.clip(
        np.rint((xs[cols] - over.x0) / (over.x1 - over.x0) * (over.nxdem - 1)).astype(np.int64),
        0,
        over.nxdem - 1,
    )
    written = 0
    for start in range(0, rows.size, ROWS_AT_A_TIME):
        stop = min(start + ROWS_AT_A_TIME, rows.size)
        patch = over.alt_dem[np.ix_(from_row[start:stop], from_col)]
        has = patch != over.nodata
        if not has.any():
            continue
        here = rows[start:stop]
        region = base.alt_dem[np.ix_(here, cols)]
        region[has] = patch[has]
        base.alt_dem[np.ix_(here, cols)] = region
        written += int(has.sum())
    return written


def _read_whole_file(
    path: Path,
    tile: TileRef,
    source: str,
    record: Callable[[OsxpError], None],
    *,
    base_if_error: int = 3601,
) -> RasterRead:
    """``read_elevation_from_file`` for a source that is one file covering the tile.

    The reader degrades an unreadable file to zeros, as Ortho4XP does; for such a source that is
    the whole tile flat (or, for an overlay, the tile flattened under it), so OrthoStudio XP raises
    ``DEM_TILE_UNAVAILABLE`` instead (decision 0007).
    """
    failures: list[str] = []

    def note(err: OsxpError) -> None:
        if err.code in UNREADABLE_CODES:
            failures.append(err.code)
        record(err)

    read = read_elevation_from_file(
        path, tile.lat, tile.lon, base_if_error=base_if_error, on_event=note
    )
    if failures:
        raise OsxpError(
            "DEM_TILE_UNAVAILABLE",
            context={
                "cell": hem_latlon(tile.lat, tile.lon),
                "source": source,
                "reason": f"the file is unreadable ({failures[0]}): {path}",
            },
        )
    return read


def own_cell_missing(tile: TileRef, cells: Sequence[EnsureResult]) -> bool:
    """Whether the tile's own cell has no elevation data (the one a build cannot do without)."""
    return any(
        (cell.lat, cell.lon) == (tile.lat, tile.lon) and cell.state is CellState.MISSING
        for cell in cells
    )


def require_own_cell(
    tile: TileRef, source: str, cells: Sequence[EnsureResult], elevation_dir: Path | None = None
) -> None:
    """Raise ``DEM_TILE_UNAVAILABLE`` when the tile's own cell has no elevation data.

    A **fix** (decision 0007). Ortho4XP degrades that cell to 0 m like a neighbour and builds a
    flat tile without a word: that is how ``+46+006`` flew with a flat Jura when the ``dem1``
    archives of viewfinderpanoramas started answering 404. A neighbour still degrades (its
    only use is the 36-post margin); a sea cell (``OCEAN``) is not an error.

    A source whose cells a user places by hand (``MANUAL_SOURCES``) says so instead, naming the
    file to put there: telling him to choose another source would hide the one thing that works.
    """
    for cell in cells:
        if (cell.lat, cell.lon) == (tile.lat, tile.lon) and cell.state is CellState.MISSING:
            if source in MANUAL_SOURCES and elevation_dir is not None:
                raise manual_download_error(source, tile.lat, tile.lon, elevation_dir)
            raise OsxpError(
                "DEM_TILE_UNAVAILABLE",
                context={
                    "cell": cell.cell,
                    "source": source,
                    "reason": cell.detail or "no file",
                },
            )


def expected_alt_size(source: str, custom_dem: str = "") -> int | None:
    """Byte size ``Data<tile>.alt`` must have for an assembled source (``O4_Mesh_Utils.py:582``).

    ``None`` when the source is a file whose size is only known after reading it.
    """
    geom = GEOMETRY.get(source)
    return None if geom is None else 4 * geom.n * geom.n


def elevation_file(source: str, elevation_dir: Path, tile: TileRef) -> Path:
    """Where the cell file of ``tile`` lives for ``source`` (re-exported for the CLI)."""
    return elevation_path(source, elevation_dir, tile.lat, tile.lon)
