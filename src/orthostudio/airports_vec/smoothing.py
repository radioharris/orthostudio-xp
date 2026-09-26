# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Smoothing the elevation raster over airport footprints: what makes a runway flat.

Specification: ``docs/specs/airports-geometry.md`` section 5. Origin: Ortho4XP
``src/O4_Airport_Utils.py`` ``smooth_raster_over_airports`` (924-1038), the only writer of
``Data<tile>.alt`` on Ortho4XP's normal path (``docs/specs/dem.md`` 9.2).

The blur itself is **not** written here: ``orthostudio.dem.raster.smoothen`` and
``orthostudio.dem.raster.smooth_over_regions`` are the pure half of the Ortho4XP function, already
ported and proven bit for bit. This module is the other half -- which regions, in what order, with
what margins and what mask -- and it calls them.

Nothing here touches the network.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from math import ceil, floor
from typing import Protocol

import numpy as np
from PIL import Image, ImageDraw
from shapely import ops
from shapely.geometry.base import BaseGeometry

from orthostudio.airports_vec.model import MAX_SMOOTHING_PIX
from orthostudio.dem.dem import Dem
from orthostudio.dem.raster import Region, smooth_over_regions
from orthostudio.vectors.geom import ensure_multipolygon
from orthostudio.vectors.water import LAT_TO_M

__all__ = [
    "DEFAULT_SMOOTHING_PARAMS",
    "Footprint",
    "SmoothingParams",
    "SurfaceLike",
    "airport_regions",
    "max_smoothing_pix",
    "smooth_dem_over_airports",
    "surfaces_of",
    "upscale_factor",
]


class SurfaceLike(Protocol):
    """The four built areas of one
    airport (:class:`orthostudio.airports_vec.model.SurfaceAreas`)."""

    @property
    def runway(self) -> BaseGeometry | None:
        """The union of the runway outlines, or ``None`` when they were never built."""

    @property
    def hangar(self) -> BaseGeometry | None:
        """The buffered hangars."""

    @property
    def taxiway(self) -> BaseGeometry | None:
        """The buffered taxiways, before the encoder cleans them."""

    @property
    def apron(self) -> BaseGeometry | None:
        """The aprons."""


class Footprint(Protocol):
    """What the smoothing needs of one airport: :class:`orthostudio.airports_vec.model.Airport`."""

    @property
    def boundary(self) -> BaseGeometry | None:
        """The **tile-local** footprint, after ``update_boundaries``; its bounds give the window."""

    @property
    def areas(self) -> SurfaceLike:
        """The four built areas, drawn into the mask with the boundary (``:967-975``)."""

    @property
    def smoothing_pix(self) -> int | None:
        """This airport's own ``smoothing_pix`` tag, or ``None`` for the tile default."""


def surfaces_of(footprint: Footprint) -> list[BaseGeometry]:
    """The geometries the mask is drawn from, in Ortho4XP's union order (``:967-975``).

    The boundary first, then runways, hangars, taxiways, aprons -- redundant after
    ``update_boundaries`` unioned the same set, and kept because ``unary_union`` of the same
    geometries in another order can differ in the last bits and this is a byte-identity
    target. An area no module has built yet is left out rather than treated as empty.
    """
    areas = footprint.areas
    candidates = (
        footprint.boundary,
        areas.runway,
        areas.hangar,
        areas.taxiway,
        areas.apron,
    )
    return [geom for geom in candidates if geom is not None]


@dataclass(frozen=True, slots=True)
class SmoothingParams:
    """The one user setting of the stage, and the two constants beside it."""

    apt_smoothing_pix: int = 8
    """Ortho4XP's ``apt_smoothing_pix`` (``O4_Config_Utils.py:117``); ``0`` disables the stage."""
    target_pixel_m: float = 10.0
    """Mask resolution the upscale aims at, "to avoid aliasing" (``:941``)."""
    preserve_boundary: bool = True
    """Fade the outer ``max_pix`` rows and columns back, so neighbouring tiles glue (``:1002``)."""


DEFAULT_SMOOTHING_PARAMS = SmoothingParams()
"""The Ortho4XP defaults, as a module-level singleton (ruff B008)."""


def _tag_pix(footprint: Footprint) -> int | None:
    """The airport's own width, or ``None`` when it has none OrthoStudio XP accepts.

    ``discover`` already drops an out-of-range tag with a code; this is the same bound for a
    footprint built by other means, so that no path hands ``smooth_over_regions`` a negative
    or unbounded width (review 6, ``airports-geometry.md`` difference 7).
    """
    if footprint.smoothing_pix is None:
        return None
    try:
        value = int(footprint.smoothing_pix)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= MAX_SMOOTHING_PIX else None


def _pix_of(footprint: Footprint, params: SmoothingParams) -> int:
    """This airport's smoothing width in raster pixels (``:948-959``)."""
    value = _tag_pix(footprint)
    return value if value is not None else params.apt_smoothing_pix


def max_smoothing_pix(
    footprints: Iterable[Footprint], params: SmoothingParams = DEFAULT_SMOOTHING_PARAMS
) -> int:
    """Width of the final border re-blend (``:925-934``).

    The tile setting, raised to the largest ``smoothing_pix`` **any** airport carries -- even
    one whose own window is later skipped.
    """
    widest = params.apt_smoothing_pix
    for footprint in footprints:
        value = _tag_pix(footprint)
        if value is not None:
            widest = max(value, widest)
    return widest


def upscale_factor(ystep: float, params: SmoothingParams = DEFAULT_SMOOTHING_PARAMS) -> int:
    """How much finer than the DEM the mask is drawn (``:940-942``).

    The mask is rasterised ``upscale`` times finer and resized down bicubically, so the edge
    of a runway is anti-aliased instead of stair-stepped. On a 3" raster of a 1.02 degree
    window this is 4.
    """
    return max(ceil(ystep * LAT_TO_M / params.target_pixel_m), 1)


def airport_regions(
    dem: Dem,
    footprints: Iterable[Footprint],
    params: SmoothingParams = DEFAULT_SMOOTHING_PARAMS,
) -> list[Region]:
    """One :class:`orthostudio.dem.raster.Region` per airport worth smoothing (``:944-1000``).

    The window is the bounds of the **footprint**, grown by ``pix`` pixels so the blur can
    reach past it and clamped to the raster; a window with no interior is skipped, as is an
    airport whose ``pix`` is zero.
    """
    xstep = (dem.x1 - dem.x0) / dem.nxdem
    ystep = (dem.y1 - dem.y0) / dem.nydem
    upscale = upscale_factor(ystep, params)
    regions: list[Region] = []
    for footprint in footprints:
        pix = _pix_of(footprint, params)
        if not pix:
            continue
        boundary = footprint.boundary
        if boundary is None or boundary.is_empty:
            continue
        (xmin, ymin, xmax, ymax) = boundary.bounds
        colmin = max(floor((xmin - dem.x0) / xstep) - pix, 0)
        colmax = min(ceil((xmax - dem.x0) / xstep) + pix, dem.nxdem - 1)
        rowmin = max(floor((dem.y1 - ymax) / ystep) - pix, 0)
        rowmax = min(ceil((dem.y1 - ymin) / ystep) + pix, dem.nydem - 1)
        if colmin >= colmax or rowmin >= rowmax:
            continue
        mask = _mask_of(
            footprint,
            x0=dem.x0 + colmin * xstep,
            y1=dem.y1 - rowmin * ystep,
            xstep=xstep,
            ystep=ystep,
            width=colmax - colmin + 1,
            height=rowmax - rowmin + 1,
            upscale=upscale,
        )
        regions.append(
            Region(
                rowmin=rowmin,
                rowmax=rowmax,
                colmin=colmin,
                colmax=colmax,
                mask=mask,
                pix=pix,
            )
        )
    return regions


def _mask_of(
    footprint: Footprint,
    *,
    x0: float,
    y1: float,
    xstep: float,
    ystep: float,
    width: int,
    height: int,
    upscale: int,
) -> np.ndarray:
    """Rasterise one airport's surfaces into a 0-255 mask the size of its window (``:955-1000``).

    Exteriors are filled white and interiors black, in ring order, at ``upscale`` times the
    DEM resolution; the image is then resized bicubically to the window. A hole inside a hole
    would be lost, as in Ortho4XP; no airport has one.
    """
    image = Image.new("L", (upscale * width, upscale * height))
    draw = ImageDraw.Draw(image)
    full_area = ensure_multipolygon(ops.unary_union(surfaces_of(footprint)))
    for polygon in full_area.geoms:
        draw.polygon(
            [
                (round(upscale * (x - x0) / xstep), round(upscale * (y1 - y) / ystep))
                for (x, y) in polygon.exterior.coords
            ],
            fill="white",
        )
        for inner_ring in polygon.interiors:
            draw.polygon(
                [
                    (round(upscale * (x - x0) / xstep), round(upscale * (y1 - y) / ystep))
                    for (x, y) in inner_ring.coords
                ],
                fill="black",
            )
    return np.asarray(image.resize((width, height), Image.Resampling.BICUBIC), dtype=np.uint8)


def smooth_dem_over_airports(
    dem: Dem,
    airports: Iterable[Footprint],
    params: SmoothingParams = DEFAULT_SMOOTHING_PARAMS,
) -> Dem:
    """The raster of ``dem``, blurred over every airport (``:924-1038``).

    Returns a **new** :class:`orthostudio.dem.Dem` sharing every other field: Ortho4XP mutates
    ``tile.dem.alt_dem`` in place, which a cached artefact keyed by its digest must not do.
    Writing the result with :meth:`orthostudio.dem.Dem.write_alt` is what produces
    ``Data<tile>.alt``.
    """
    footprints = list(airports)
    max_pix = max_smoothing_pix(footprints, params)
    if not max_pix:
        return dem
    # A raster narrower than the border re-blend would index past its own edge
    # (``smooth_over_regions`` walks ``max_pix`` rows from each side). Ortho4XP has the same
    # arithmetic and never meets the case: its window is 3 673 samples wide and
    # ``apt_smoothing_pix`` is 8. The clamp changes nothing there and turns a crash into a
    # narrower blend on a test-sized raster.
    max_pix = min(max_pix, dem.nydem, dem.nxdem)
    regions = airport_regions(dem, footprints, params)
    smoothed = smooth_over_regions(
        dem.alt_dem,
        regions,
        max_pix=max_pix,
        preserve_boundary=params.preserve_boundary,
    )
    return replace(dem, alt_dem=smoothed)
