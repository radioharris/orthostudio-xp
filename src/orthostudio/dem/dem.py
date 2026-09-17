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
    SOURCES,
    CellState,
    EnsureOptions,
    EnsureResult,
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
        overlays = tuple(cls._load_one(tile, name, opts, record) for name in sources[1:])
        dem.overlays = overlays
        return dem

    @classmethod
    def _load_one(
        cls,
        tile: TileRef,
        source: str,
        opts: EnsureOptions,
        record: Callable[[OsxpError], None],
    ) -> Dem:
        if source in GEOMETRY:
            combined: CombinedRaster = build_combined_raster(
                source, tile.lat, tile.lon, opts, on_event=record
            )
            require_own_cell(tile, source, combined.cells)
            return cls._from_read(tile, combined.read, source, combined.cells)
        if source in SOURCES:
            result = ensure_elevation(source, tile.lat, tile.lon, opts)
            if not result.ok or result.path is None:
                raise manual_download_error(source, tile.lat, tile.lon, opts.elevation_dir)
            read = _read_whole_file(result.path, tile, source, record, base_if_error=3601)
            return cls._from_read(tile, read, source, (result,))
        path = Path(source)
        read = _read_whole_file(path, tile, source, record)
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


def require_own_cell(tile: TileRef, source: str, cells: Sequence[EnsureResult]) -> None:
    """Raise ``DEM_TILE_UNAVAILABLE`` when the tile's own cell has no elevation data.

    A **fix** (decision 0007). Ortho4XP degrades that cell to 0 m like a neighbour and builds a
    flat tile without a word: that is how ``+46+006`` flew with a flat Jura when the ``dem1``
    archives of viewfinderpanoramas started answering 404. A neighbour still degrades (its
    only use is the 36-post margin); a sea cell (``OCEAN``) is not an error.
    """
    for cell in cells:
        if (cell.lat, cell.lon) == (tile.lat, tile.lon) and cell.state is CellState.MISSING:
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
