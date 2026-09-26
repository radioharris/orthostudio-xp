# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The airport record: :class:`Airport`, :class:`AirportSet` and the geodetic helpers.

Specification: ``docs/specs/airports-discovery.md`` section 2. Successor of the
``dico_airports`` dictionary of Ortho4XP (``O4_Airport_Utils.py:19-177`` builds it,
``:177-329`` fills it, ``:682-847`` reshapes it), with the same keys, the same contents and —
this is the load-bearing part — the same **insertion order**: ``attach_surfaces_to_airports``
stops at the *first* airport a surface meets (``:194-201``) and the nearest-airport fallback
keeps the *first* minimum (``:219-223``), so the order decides which aerodrome owns which
runway, hence what gets flattened.

The module holds no behaviour beyond the record itself and the two geodetic formulas the
airport rules need (``O4_Geo_Utils.py:4-28``). Discovery and attachment live in
:mod:`orthostudio.airports_vec.discover`; the runway reconstruction, the hangar / apron / taxiway
area builders and the raster smoothing fill :class:`SurfaceAreas` from their own modules.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from math import atan2, cos, pi, sin, sqrt
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

import numpy as np
from numpy.typing import NDArray
from shapely import geometry
from shapely.geometry.base import BaseGeometry

from orthostudio.errors import OsxpError
from orthostudio.vectors.water import LAT_TO_M, M_TO_LAT

if TYPE_CHECKING:  # pragma: no cover - the store is data the record only carries
    from orthostudio.vectors.osmdata import OsmData

__all__ = [
    "CATEGORIES",
    "EARTH_RADIUS",
    "LAT_TO_M",
    "MAX_SMOOTHING_PIX",
    "M_TO_LAT",
    "Airport",
    "AirportKey",
    "AirportSet",
    "Category",
    "KeyType",
    "RunwayPart",
    "SurfaceAreas",
    "great_circle_m",
    "has_boundary",
    "m_to_lon",
]

# -- geodesy (O4_Geo_Utils.py:4-28) ----------------------------------------------------------

EARTH_RADIUS = 6378137
"""``O4_Geo_Utils.py:4``: the sphere Ortho4XP measures distances on (WGS84 semi-major axis)."""

# ``LAT_TO_M`` (metres per degree of latitude, ``:5``) and ``M_TO_LAT`` (``:6``) are the ones
# ``orthostudio.vectors.water`` declares: one definition for the whole vector stage (review 6 found
# three copies that agreed bit for bit, i.e. three chances to stop agreeing).

MAX_SMOOTHING_PIX = 1000
"""The widest airport smoothing OrthoStudio XP accepts, in DEM pixels, from the tile setting
``apt_smoothing_pix`` as from an OSM ``smoothing_pix`` tag: beyond it the blur covers a whole
tile and the triangular kernel alone costs gigabytes (review 6). Ortho4XP bounds neither."""


def m_to_lon(lat: float) -> float:
    """Degrees of longitude per metre at this latitude (``O4_Geo_Utils.py:14-15``)."""
    return M_TO_LAT / cos(pi * lat / 180)


def great_circle_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in metres between two ``(lon, lat)`` points.

    ``O4_Geo_Utils.dist`` (``:19-28``), formula for formula and in scalar form: the value
    reaches a ``< 3500`` comparison in the attachment fallback
    (``docs/specs/airports-discovery.md`` R11), where an ulp decides which aerodrome owns a
    runway.
    """
    h = (
        sin((a[1] - b[1]) * pi / 360) ** 2
        + cos(a[1] * pi / 180) * cos(b[1] * pi / 180) * sin((a[0] - b[0]) * pi / 360) ** 2
    )
    return 2 * EARTH_RADIUS * atan2(sqrt(h), sqrt(1 - h))


# -- the record ------------------------------------------------------------------------------

Category = Literal["runway", "taxiway", "apron", "hangar"]
"""The four ``aeroway`` values Ortho4XP attaches to aerodromes (``O4_Airport_Utils.py:179``)."""

CATEGORIES: tuple[Category, ...] = ("runway", "taxiway", "apron", "hangar")
"""In Ortho4XP's own order: it decides which orphan airport is created first (spec R8)."""

KeyType = Literal["icao", "iata", "local_ref", "name", "repr_node"]
"""How an airport got its key, in the precedence order of ``:28-42`` and ``:89-99``."""

AirportKey = str | tuple[float, float]
"""An ICAO / IATA / ``local_ref`` code, a name, or the representative point itself."""


@dataclass(frozen=True, slots=True)
class RunwayPart:
    """One reconstructed runway: the 4-tuple of ``runways_as_area`` / ``runways_as_line``.

    ``O4_Airport_Utils.py:403-411`` (area form) and ``:659`` (linear form) both append
    ``(polygon, start, end, width)``. ``polygon`` is tile-local, ``start`` and ``end`` are
    tile-local ``(x, y)`` mid-points of the two short sides, ``width`` is in metres. Filled by
    the runway module of this wave, read by the encoder and by the listing.
    """

    polygon: geometry.Polygon
    start: NDArray[np.float64]
    end: NDArray[np.float64]
    width: float


@dataclass(slots=True)
class SurfaceAreas:
    """The built geometry of one airport, **tile-local**, in the slots Ortho4XP overwrites.

    Ortho4XP packs these into the very fields that held the way ids: ``runway`` becomes
    ``(area, as_area, as_line)`` at ``:676-680``, ``apron`` and ``taxiway`` become
    ``(area, way_ids)`` at ``:767`` and ``:800``, ``hangar`` becomes a bare ``MultiPolygon`` at
    ``:731``. OrthoStudio XP keeps the ids in :attr:`Airport.ways` and the geometry here; the second
    element of Ortho4XP's ``apron`` and ``taxiway`` tuples is exactly ``Airport.ways[category]``.

    Every field is ``None`` until the module that owns it has run, which is how
    :func:`orthostudio.airports_vec.discover.discard_unwanted` tells "no runway" from "the runways
    were never reconstructed" (spec R13).
    """

    runway: BaseGeometry | None = None
    runway_as_area: tuple[RunwayPart, ...] = ()
    runway_as_line: tuple[RunwayPart, ...] = ()
    taxiway: BaseGeometry | None = None
    apron: BaseGeometry | None = None
    hangar: BaseGeometry | None = None

    def runway_count(self) -> int:
        """``len(runway[1]) + len(runway[2])``, the count the listing shows (``:857-858``)."""
        return len(self.runway_as_area) + len(self.runway_as_line)


def _empty_ways() -> dict[Category, list[int]]:
    return {name: [] for name in CATEGORIES}


@dataclass(slots=True)
class Airport:
    """One entry of ``dico_airports``.

    ``repr_node`` and, until :func:`orthostudio.airports_vec.discover.update_boundaries` runs,
    ``boundary`` are in **absolute** degrees — that is what the attachment and the size filter
    consume (spec R7, R11, R13). After ``update_boundaries`` the boundary is a tile-local
    ``MultiPolygon``.
    """

    key: AirportKey
    key_type: KeyType
    name: str
    repr_node: tuple[float, float]
    boundary: BaseGeometry | None = None
    ways: dict[Category, list[int]] = field(default_factory=_empty_ways)
    """Internal way ids per category, in attachment order (``O4_Airport_Utils.py:201``)."""
    runway_rels: list[int] = field(default_factory=list)
    """``runway_as_rel``: relation ids of the runways encoded as multipolygons (``:271``)."""
    areas: SurfaceAreas = field(default_factory=SurfaceAreas)
    smoothing_pix: int | None = None
    """The ``smoothing_pix`` tag, overriding ``apt_smoothing_pix`` for this airport (``:105``)."""
    elevation: float | None = None
    """The OSM ``ele`` tag; **additive**, consumed by nothing in the port (spec difference 5)."""
    orphan: bool = False
    """Made by a surface far from every aerodrome (``:226-252``, ``:300-322``) rather than by
    an aerodrome element. Ortho4XP builds the two kinds of entries with their keys in a different
    order."""

    @property
    def code(self) -> str | None:
        """The key when it is an ICAO / IATA / ``local_ref`` code, else ``None``.

        ``list_airports_and_runways`` (``:866-909``) shows the key for those three key types
        and ``****`` for the other two; this is that distinction, as data.
        """
        if self.key_type in ("icao", "iata", "local_ref") and isinstance(self.key, str):
            return self.key
        return None

    def surface_count(self) -> int:
        """How many OSM surfaces are attached to this airport, relations included."""
        return sum(len(ids) for ids in self.ways.values()) + len(self.runway_rels)

    # -- Ortho4XP's flat field names, for the modules that consume the record ------------------
    #
    # ``runways.AirportRecord`` and ``areas.SurfaceRecord`` are structural protocols spelled
    # with ``dico_airports``' own keys. Exposing them as read-only views of :attr:`ways` and
    # :attr:`runway_rels` lets this record satisfy both without copying anything and without
    # either side importing the other.

    @property
    def runway(self) -> list[int]:
        """``dico_airports[key]["runway"]`` before the runways are reconstructed."""
        return self.ways["runway"]

    @property
    def taxiway(self) -> list[int]:
        """``dico_airports[key]["taxiway"]`` before the taxiway areas are built."""
        return self.ways["taxiway"]

    @property
    def apron(self) -> list[int]:
        """``dico_airports[key]["apron"]`` before the apron areas are built."""
        return self.ways["apron"]

    @property
    def hangar(self) -> list[int]:
        """``dico_airports[key]["hangar"]`` before the hangar areas are built."""
        return self.ways["hangar"]

    @property
    def runway_as_rel(self) -> list[int]:
        """``dico_airports[key]["runway_as_rel"]``, Ortho4XP's spelling of :attr:`runway_rels`."""
        return self.runway_rels


def has_boundary(airport: Airport) -> bool:
    """Ortho4XP's ``if dico_airports[x]["boundary"]`` (``:195``, ``:685``), shapely truthiness.

    ``None`` is false and so is an *empty* geometry, because shapely's ``__bool__`` is
    ``not is_empty``. Spelling it out keeps the two call sites honest under ``mypy``.
    """
    return airport.boundary is not None and not airport.boundary.is_empty


class AirportSet:
    """An ordered mapping from :data:`AirportKey` to :class:`Airport`.

    A thin wrapper on a ``dict`` because the insertion order *is* part of the specification
    (spec section 1) and because Ortho4XP keys by a string **or** by a ``(lon, lat)`` tuple
    (``:110``, ``:243``), which no list can index.
    """

    __slots__ = ("_by_key", "_store")

    def __init__(
        self,
        airports: Mapping[AirportKey, Airport] | None = None,
        *,
        store: OsmData | None = None,
    ) -> None:
        self._by_key: dict[AirportKey, Airport] = dict(airports or {})
        self._store: OsmData | None = store

    @property
    def store(self) -> OsmData:
        """The ``airports`` OSM layer the record was read from.

        The modules that build the geometry need it to resolve the way ids this record holds
        (``runways.AirportSet`` and ``areas.SurfaceSet`` both ask for it). A record built by
        hand -- in a test, or from a serialised artefact -- carries none, and asking for it
        then is a coded error rather than an ``AttributeError`` three frames down.
        """
        if self._store is None:
            raise OsxpError(
                "OSM_AIRPORT_INFO_UNAVAILABLE",
                context={"tile": "?", "reason": "this AirportSet carries no OSM store"},
                remedy="Build the record with orthostudio.airports_vec.discover.discover, which "
                "keeps the store it read.",
            )
        return self._store

    @property
    def airports(self) -> Mapping[AirportKey, Airport]:
        """The record as a read-only mapping, in insertion order (the protocols' view)."""
        return MappingProxyType(self._by_key)

    # -- mapping surface -------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self) -> Iterator[Airport]:
        """The airports in insertion order (Ortho4XP's ``for airport in dico_airports``)."""
        return iter(self._by_key.values())

    def __contains__(self, key: object) -> bool:
        return key in self._by_key

    def __getitem__(self, key: AirportKey) -> Airport:
        return self._by_key[key]

    def __repr__(self) -> str:
        return f"AirportSet({len(self._by_key)} airports)"

    def keys(self) -> list[AirportKey]:
        """The keys in insertion order."""
        return list(self._by_key)

    def get(self, key: AirportKey) -> Airport | None:
        return self._by_key.get(key)

    def add(self, airport: Airport) -> Airport:
        """Insert (or replace, as ``:230`` does) and return the airport."""
        self._by_key[airport.key] = airport
        return airport

    def pop(self, key: AirportKey) -> Airport | None:
        """``dico_airports.pop(key, None)``."""
        return self._by_key.pop(key, None)

    def with_boundary(self) -> list[Airport]:
        """The airports Ortho4XP's ``(x for x in dico if dico[x]["boundary"])`` yields, in order."""
        return [airport for airport in self if has_boundary(airport)]
