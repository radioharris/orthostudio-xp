"""The whole airport chain of one tile, in Ortho4XP's order, as three calls.

Origin: Ortho4XP ``src/O4_Vector_Map.include_airports`` (``:181-222``). This module owns no
geometry: it calls :mod:`orthostudio.airports_vec.discover`,
:mod:`~orthostudio.airports_vec.runways`, :mod:`~orthostudio.airports_vec.areas`,
:mod:`~orthostudio.airports_vec.smoothing` and :mod:`~orthostudio.airports_vec.encode` in the order
``include_airports`` calls their Ortho4XP counterparts, and it carries the one value between them
the record does not hold -- the elevation raster, which Ortho4XP mutates in place and
OrthoStudio XP replaces.

Three calls, because the elevation sits between them:

#. :func:`build_airports` -- steps 1 to 7 (OSM only, no raster);
#. :func:`smooth_elevation` -- step 9, the raster the rest of the stage samples;
#. :func:`encode` -- steps 11 to 15, the PSLG passes, the seeds and the footprints.

:func:`views_of` is the seam between the record (:class:`orthostudio.airports_vec.model.Airport`)
and the encoder (:class:`orthostudio.airports_vec.encode.AirportLike`): a nine-field projection with
no computation, so that neither module imports the other.

Spec: ``docs/specs/airports-integration.md``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Collection, Hashable
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from shapely import geometry
from shapely.geometry.base import BaseGeometry

from orthostudio.airports_vec import areas as apt_areas
from orthostudio.airports_vec import discover as apt_discover
from orthostudio.airports_vec import runways as apt_runways
from orthostudio.airports_vec import smoothing as apt_smoothing
from orthostudio.airports_vec.encode import (
    DEFAULT_ENCODE_PARAMS,
    AirportEncodeParams,
    AirportLayers,
    AirportView,
    encode_airports,
)
from orthostudio.airports_vec.model import Airport, AirportSet
from orthostudio.dem.dem import Dem
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.osmdata import OsmData
from orthostudio.vectors.water import Altitudes, EventHandler

__all__ = [
    "DEFAULT_AIRPORT_PARAMS",
    "AirportParams",
    "AirportStage",
    "build_airports",
    "encode",
    "smooth_elevation",
    "views_of",
]

log = logging.getLogger("orthostudio.airports_vec.stage")


@dataclass(frozen=True, slots=True)
class AirportParams:
    """Every constant the airport chain reads, gathered in one place.

    Only :attr:`smoothing_pix` is configuration in Ortho4XP (``O4_Config_Utils.py:117``); the
    others are the hard-coded numbers of ``O4_Airport_Utils``, named here so a profile could
    move them and so the rule knows what it consumes (``airports-integration.md`` 5).
    """

    smoothing_pix: int = 8
    """``apt_smoothing_pix``: the only airport setting a tile configuration carries."""
    discover: apt_discover.DiscoverParams = apt_discover.DEFAULTS
    runways: apt_runways.RunwayParams = apt_runways.DEFAULT_RUNWAY_PARAMS
    areas: apt_areas.AreaParams = apt_areas.DEFAULT_AREA_PARAMS
    encode: AirportEncodeParams = DEFAULT_ENCODE_PARAMS

    def smoothing(self) -> apt_smoothing.SmoothingParams:
        """The subset
        :func:`orthostudio.airports_vec.smoothing.smooth_dem_over_airports` consumes."""
        return apt_smoothing.SmoothingParams(apt_smoothing_pix=int(self.smoothing_pix))


DEFAULT_AIRPORT_PARAMS = AirportParams()
"""Ortho4XP's numbers, as a module-level singleton (ruff B008)."""


@dataclass(frozen=True, slots=True)
class AirportStage:
    """What the chain produced: the record, the encoded layers and the counters."""

    airports: AirportSet
    layers: AirportLayers
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def array(self) -> NDArray[np.bool_] | None:
        """The 1001x1001 neighbourhood raster the roads read (``:222``)."""
        return self.layers.array


# -- steps 1 to 7: the record and its geometry, from OSM alone --------------------------------


def build_airports(
    store: OsmData,
    tile: TileRef,
    params: AirportParams = DEFAULT_AIRPORT_PARAMS,
    *,
    on_event: EventHandler | None = None,
) -> tuple[AirportSet, dict[str, int]]:
    """``include_airports`` steps 1 to 7 (``O4_Vector_Map.py:196-204``).

    Discovery, attachment, runway reconstruction, the size filter, the three surface builders
    and the final boundaries -- in that order, which decides which aerodrome owns which
    surface and therefore what is flattened (``airports-discovery.md`` section 1).
    """
    airports = apt_discover.discover(store, tile, params.discover, on_event=_coded(on_event))
    found = len(airports)
    runways = apt_runways.build_runways(airports, store, tile, params.runways, on_event=on_event)
    apt_discover.discard_unwanted(airports, tile, params.discover, on_event=_coded(on_event))
    built = apt_areas.build_areas(airports, store, tile, params.areas, on_event=on_event)
    apt_discover.update_boundaries(airports, tile)
    counts = {
        "found": found,
        "kept": len(airports),
        "runways": runways.runways,
        "runways_merged": runways.merged,
        "runways_rejected": runways.rejected,
        "hangar_ways": built.hangars,
        "apron_ways": built.aprons,
        "taxiway_ways": built.taxiways,
        "surfaces_skipped": built.skipped,
    }
    log.info(
        "%s: %d airports (%d discovered), %d runways",
        tile.name,
        counts["kept"],
        counts["found"],
        counts["runways"],
    )
    return airports, counts


def _coded(on_event: EventHandler | None) -> apt_discover.EventHandler | None:
    """``discover``'s ``(code, context)`` callback over the repository's ``OsxpError`` one.

    The two conventions coexist in the package (blocker 3 of the ``aptgeom`` chantier); this
    is the one-line conversion that lets a caller pass a single handler. The registry's own
    message and remedy are used, so nothing is invented here.
    """
    if on_event is None:
        return None

    def handler(code: str, context: dict[str, object]) -> None:
        on_event(OsxpError(code, context=dict(context)))

    return handler


# -- step 9: the raster every other family samples --------------------------------------------


def smooth_elevation(
    dem: Dem,
    airports: AirportSet,
    params: AirportParams = DEFAULT_AIRPORT_PARAMS,
) -> Dem:
    """``smooth_raster_over_airports`` (``O4_Vector_Map.py:213``), on a copy.

    Returns a **new** :class:`orthostudio.dem.Dem`; the artefact of ``orthostudio.dem@1`` is
    memory-mapped and shared, so mutating it as Ortho4XP mutates ``tile.dem.alt_dem`` would corrupt
    the store.
    """
    return apt_smoothing.smooth_dem_over_airports(dem, list(airports), params.smoothing())


def require_dem(dem: Altitudes | Dem, tile: TileRef) -> Dem:
    """The elevation as a :class:`orthostudio.dem.Dem`, or a coded error naming the remedy.

    The smoothing reads the raster *and its window* (``x0``, ``y1``, ``nxdem``) and rebuilds the
    object, which the sampling protocol :class:`orthostudio.vectors.water.Altitudes` does not
    describe. ``orthostudio.vectors.rule`` only ever passes an ``orthostudio.dem@1`` artefact, so
    this is a guard, not a branch.
    """
    if isinstance(dem, Dem):
        return dem
    raise OsxpError(
        "DEM_FILE_UNREADABLE",
        context={"path": "-", "tile": tile.name, "reason": f"{type(dem).__name__} is not a Dem"},
        message="The airport smoothing needs the elevation raster and its window, not only a "
        "sampler.",
        remedy="Build the vectors with --dem native, which gives the stage the artefact of "
        "orthostudio.dem@1.",
    )


# -- the seam: the record as the encoder reads it ---------------------------------------------


def view_of(airport: Airport) -> AirportView:
    """One :class:`orthostudio.airports_vec.model.Airport` as an :class:`AirportView`.

    Nine fields, no computation, no copy of a geometry: ``runways`` is the concatenation
    Ortho4XP's ``runway[1] + runway[2]`` makes at every call site (``O4_Airport_Utils.py:1046``),
    and the way id lists are the ones the record kept when the area builders overwrote Ortho4XP's
    fields (``airports-discovery.md`` R16).
    """
    built = airport.areas
    empty = geometry.Polygon()
    return AirportView(
        key=airport.key,
        runways=built.runway_as_area + built.runway_as_line,
        runway_area=built.runway if built.runway is not None else empty,
        taxiway_area=built.taxiway if built.taxiway is not None else empty,
        taxiway_ways=tuple(airport.ways["taxiway"]),
        apron_area=built.apron if built.apron is not None else empty,
        apron_ways=tuple(airport.ways["apron"]),
        hangars=built.hangar if built.hangar is not None else empty,
        boundary=airport.boundary if airport.boundary is not None else empty,
    )


def views_of(airports: AirportSet) -> list[AirportView]:
    """Every airport of the set, in insertion order, as the encoder reads them."""
    return [view_of(airport) for airport in airports]


# -- steps 11 to 15: the PSLG passes ----------------------------------------------------------


def encode(
    airports: AirportSet,
    tile: TileRef,
    dem: Altitudes,
    params: AirportParams = DEFAULT_AIRPORT_PARAMS,
    *,
    store: OsmData,
    patch_names: Collection[Hashable] = (),
    patches_area: BaseGeometry | None = None,
    on_event: EventHandler | None = None,
    cancel: threading.Event | None = None,
) -> AirportStage:
    """``include_airports`` steps 11 to 15 (``O4_Vector_Map.py:215-222``).

    ``dem`` must be the raster :func:`smooth_elevation` returned (rule R-I1): Ortho4XP reads the
    smoothed one here and everywhere after it.
    """
    encoded = encode_airports(
        views_of(airports),
        tile,
        dem,
        params.encode,
        osm=store,
        patch_names=patch_names,
        patches_area=patches_area,
        array=apt_discover.airport_array(
            airports, tile, params.discover, on_event=_coded(on_event)
        ),
        on_event=on_event,
        cancel=cancel,
    )
    return AirportStage(airports=airports, layers=encoded, counts=dict(encoded.counts))
