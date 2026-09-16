"""Airports as vector data: the record, its discovery and the attachment of its surfaces.

``orthostudio.airports_vec`` is the wave-2 port of Ortho4XP's ``O4_Airport_Utils`` chain, which
turns the ``aeroway`` OSM layer into the ``dico_airports`` record every later airport step
reads. This package holds the beginning of that chain:

* :mod:`orthostudio.airports_vec.model` -- :class:`~orthostudio.airports_vec.model.Airport`,
  :class:`~orthostudio.airports_vec.model.AirportSet`,
  :class:`~orthostudio.airports_vec.model.SurfaceAreas` and the two geodetic formulas the airport
  rules use;
* :mod:`orthostudio.airports_vec.discover` -- discovery, surface attachment, size filter, final
  boundaries, neighbourhood raster and listing.

The runway reconstruction (:mod:`~orthostudio.airports_vec.runways`), the hangar / apron / taxiway
area builders (:mod:`~orthostudio.airports_vec.areas`), the elevation smoothing
(:mod:`~orthostudio.airports_vec.smoothing`) and the PSLG encoding
(:mod:`~orthostudio.airports_vec.encode`, :mod:`~orthostudio.airports_vec.helipads`) are the other
modules of the same wave; they fill :class:`~orthostudio.airports_vec.model.SurfaceAreas` and read
the record back. :mod:`~orthostudio.airports_vec.stage` calls all of them in Ortho4XP's order and is
what the vector stage uses; :mod:`~orthostudio.airports_vec.artefact` serialises the
result (``docs/specs/airports-integration.md``).

Specification: ``docs/specs/airports-discovery.md``. The name of the package is
``airports_vec`` and not ``airports`` because :mod:`orthostudio.airports` already exists and is a
different thing: the ``apt.dat`` index behind the ICAO search of the page (arbitration B5).

Only the *types* are re-exported here. The functions stay in
:mod:`orthostudio.airports_vec.discover` (``from orthostudio.airports_vec.discover import
discover``) so that the package attribute ``discover`` keeps naming the module and never
the function.
"""

from __future__ import annotations

from orthostudio.airports_vec.discover import AirportRow, DiscoverParams
from orthostudio.airports_vec.model import (
    CATEGORIES,
    Airport,
    AirportKey,
    AirportSet,
    Category,
    KeyType,
    RunwayPart,
    SurfaceAreas,
)

__all__ = [
    "CATEGORIES",
    "Airport",
    "AirportKey",
    "AirportRow",
    "AirportSet",
    "Category",
    "DiscoverParams",
    "KeyType",
    "RunwayPart",
    "SurfaceAreas",
]
