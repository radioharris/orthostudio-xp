"""Zones drawn on the map, and their clipping into each tile's Ortho4XP ``zone_list``.

Spec: ``docs/specs/map-zones.md`` sections 3 to 5. A zone is a polygon asking for another
zoom level, and optionally another provider, inside the tiles it covers; a zone covering
several tiles is a region, not a separate object. The zones live in one document,
``$OSXP_HOME/zones.json`` (format ``osxp-zones-1``). At build time every zone is clipped to
every tile it touches into ``zone_list`` entries, the format ``orthostudio.dsf.zones.texture_map``
paints: the list is drawn reversed, so index 0 wins where zones overlap (decision M4), and
the parts of one zone stay adjacent so that the clipping keeps that priority.

Every refusal is an ``OsxpError`` naming the zone: ``ZONE_INVALID`` (context ``zone``, its id,
and ``reason``) or ``ZONE_TOO_MANY``. Pydantic's own errors never leave this module. A document
being saved is refused whole (:func:`parse_zones_document`); the saved one is never refused
whole (:func:`read_saved_zones`): each zone is checked on its own, the page lists the problems
next to the zones and a build is refused only for the tiles a zone with a problem touches.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

import numpy as np
import shapely
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.functional_validators import ModelWrapValidatorHandler
from shapely.errors import GEOSException
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.validation import explain_validity, make_valid

from orthostudio.dsf.params import Zone as ZoneEntry
from orthostudio.dsf.zones import _MAX_ZONES
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_link_or_copy, atomic_write_text
from orthostudio.home import osxp_home
from orthostudio.imagery.grid import TEXTURE_TILES, TextureId, texture_at, tile_to_wgs84
from orthostudio.imagery.providers import Provider, load_registry
from orthostudio.model import TileRef

__all__ = [
    "DECIMALS",
    "MAX_TILE_ENTRIES",
    "MAX_VERTICES",
    "MAX_ZL",
    "MAX_ZONES",
    "MIN_PART_AREA",
    "MIN_VERTICES",
    "MIN_ZL",
    "REGISTRY_CONTEXT",
    "ZONES_FILE",
    "ZONES_FORMAT",
    "LoadedZones",
    "PhotoChoice",
    "SavedZones",
    "TileChoice",
    "Zone",
    "ZoneEntry",
    "ZoneProblem",
    "ZonesDocument",
    "check_zone_ids",
    "default_zones_path",
    "dumps_zones",
    "load_zones",
    "parse_zones_document",
    "photo_zone_entries",
    "read_saved_zones",
    "read_zones_file",
    "save_zones",
    "tiles_touched",
    "too_many_zones",
    "with_photo_zones",
    "with_tile_photo",
    "with_zone_list",
    "zone_conflict",
    "zone_invalid",
    "zone_list_textures",
    "zone_textures",
    "zones_file_revision",
    "zones_for_tile",
    "zones_from_geojson",
    "zones_revision",
]

log = logging.getLogger("orthostudio.zones")

ZONES_FORMAT = "osxp-zones-1"
ZONES_FILE = "zones.json"
MAX_ZONES = 500
"""Zones in one document."""
MIN_VERTICES, MAX_VERTICES = 3, 2000
MIN_ZL, MAX_ZL = 12, 20
MAX_LAT = 85.0
"""Latitude limit of a vertex (web-mercator)."""
MAX_ID_LENGTH = 64
MAX_NAME_LENGTH = 80
DECIMALS = 9
"""Coordinates are rounded to 9 decimals (about 0.1 mm), in the document and in ``zone_list``."""
MIN_PART_AREA = 1e-10
"""Square degrees: a clipped part (or a texture overlap) at or below it is ignored."""
MAX_TILE_ENTRIES = _MAX_ZONES
"""``zone_list`` entries of one tile: the priority image is 8-bit (base 1, zones 2 to 255)."""
REGISTRY_CONTEXT = "registry"
"""Validation-context key of a provider registry other than the embedded one."""

_ID_PATTERN = r"^[A-Za-z0-9_-]+$"

Bounds = tuple[float, float, float, float]
"""``(lon_min, lat_min, lon_max, lat_max)`` in degrees."""


# -- errors -------------------------------------------------------------------------------------


def zone_invalid(zone: str, reason: str, **context: Any) -> OsxpError:
    """``ZONE_INVALID`` for zone ``zone`` (its id, ``""`` when the document itself is wrong)."""
    return OsxpError(
        "ZONE_INVALID",
        context={"zone": zone, "reason": reason, **context},
        message=None if zone else f"The zones document is invalid: {reason}.",
    )


def too_many_zones(count: int, *, tile: TileRef | None = None) -> OsxpError:
    """``ZONE_TOO_MANY``: ``count`` zones in the document, or entries for ``tile``."""
    if tile is None:
        context: dict[str, Any] = {"scope": "the zones document", "limit": MAX_ZONES}
    else:
        context = {"scope": f"tile {tile.name}", "limit": MAX_TILE_ENTRIES, "tile": tile.name}
    return OsxpError("ZONE_TOO_MANY", context={"count": count, **context})


def zone_conflict(path: Path) -> OsxpError:
    """``ZONE_CONFLICT``: the zones file at ``path`` is no longer the one a save was based on."""
    return OsxpError("ZONE_CONFLICT", context={"path": str(path)})


def _reason(exc: ValidationError) -> str:
    """The first pydantic error as ``field: message``."""
    errors = exc.errors()
    if not errors:
        return str(exc)
    first = errors[0]
    loc = ".".join(str(p) for p in first.get("loc", ()))
    msg = str(first.get("msg", "invalid value")).removeprefix("Value error, ")
    return f"{loc}: {msg}" if loc else msg


def _embedded_registry() -> dict[str, Provider]:
    # read each time: the sources a user added may change while OrthoStudio XP runs
    # (load_registry keeps what did not change)
    return load_registry()


def _registry(registry: Mapping[str, Provider] | None) -> Mapping[str, Provider]:
    return registry if registry is not None else _embedded_registry()


def _context_registry(info: ValidationInfo) -> Mapping[str, Provider]:
    context = info.context if isinstance(info.context, Mapping) else {}
    return _registry(context.get(REGISTRY_CONTEXT))


# -- the document -------------------------------------------------------------------------------


def _coordinate(value: float) -> float:
    return round(value, DECIMALS) + 0.0  # + 0.0 turns -0.0 into 0.0


class PhotoChoice(BaseModel):
    """Colours of the photos of a tile or a zone, the three levels being Settings, then the
    tile, then the zone: each inherits the one above until it names its own (a user, 2026-09-18).

    ``look`` ``None`` inherits; ``custom`` uses the three numbers, which are deviations as in
    Settings (``-0.3`` takes 30 % away).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    look: Literal["as_delivered", "softer", "much_softer", "custom"] | None = None
    brightness: float = Field(default=0.0, ge=-0.5, le=0.5)
    contrast: float = Field(default=0.0, ge=-0.5, le=0.5)
    saturation: float = Field(default=0.0, ge=-1.0, le=0.5)

    @field_validator("look", mode="before")
    @classmethod
    def _look(cls, value: Any) -> Any:
        return None if value == "" else value

    def values(self) -> tuple[float, float, float] | None:
        """The three numbers this choice asks for, or ``None`` when it inherits."""
        from orthostudio.config.overrides import PHOTO_LOOKS

        if self.look is None:
            return None
        if self.look == "custom":
            return (float(self.brightness), float(self.contrast), float(self.saturation))
        return PHOTO_LOOKS[self.look]


class TileChoice(BaseModel):
    """What the map holds for one tile of its own, beside the zones: its colours for now."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    photo: PhotoChoice = Field(default_factory=PhotoChoice)


class Zone(BaseModel):
    """One zone of an ``osxp-zones-1`` document (spec section 3), normalised and validated.

    ``polygon`` holds ``[lon, lat]`` vertices, ring not closed: a last vertex equal to the
    first is dropped and every coordinate is rounded to 9 decimals. ``provider`` ``None`` is
    the tile's provider (an empty string is read as ``None``). Validation raises
    ``ZONE_INVALID`` naming the zone; the provider is looked up in the embedded registry, or
    in the one given as the validation context ``{"registry": {...}}``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=MAX_ID_LENGTH, pattern=_ID_PATTERN)
    name: str = Field(default="", max_length=MAX_NAME_LENGTH)
    zl: int = Field(ge=MIN_ZL, le=MAX_ZL)
    provider: str | None = None
    photo: PhotoChoice = Field(default_factory=PhotoChoice)
    """Colours of the photos inside this zone; it inherits the tile's while its ``look`` is
    ``None`` (a user asked for colours per zone, 2026-09-18)."""
    polygon: list[tuple[float, float]]

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: Any) -> Any:
        return "" if value is None else value

    @field_validator("provider", mode="before")
    @classmethod
    def _provider(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("polygon", mode="before")
    @classmethod
    def _polygon_size(cls, value: Any) -> Any:
        if isinstance(value, list | tuple) and len(value) > MAX_VERTICES + 1:
            raise ValueError(f"{len(value)} vertices, {MIN_VERTICES} to {MAX_VERTICES} needed")
        return value

    @field_validator("polygon")
    @classmethod
    def _polygon(cls, value: list[tuple[float, float]]) -> list[tuple[float, float]]:
        for i, (lon, lat) in enumerate(value):
            if not (math.isfinite(lon) and math.isfinite(lat)):
                raise ValueError(f"vertex {i} is not a finite number")
        ring = [(_coordinate(lon), _coordinate(lat)) for lon, lat in value]
        if len(ring) > 1 and ring[-1] == ring[0]:
            ring.pop()
        if not MIN_VERTICES <= len(ring) <= MAX_VERTICES:
            raise ValueError(
                f"{len(ring)} vertices (ring not closed), {MIN_VERTICES} to {MAX_VERTICES} needed"
            )
        for i, (lon, lat) in enumerate(ring):
            if not -180 <= lon <= 180:
                raise ValueError(f"vertex {i}: longitude {lon} outside [-180, 180]")
            if not -MAX_LAT <= lat <= MAX_LAT:
                raise ValueError(f"vertex {i}: latitude {lat} outside [-85, 85]")
        return ring

    @model_validator(mode="wrap")
    @classmethod
    def _validate(
        cls, data: Any, handler: ModelWrapValidatorHandler[Zone], info: ValidationInfo
    ) -> Zone:
        if isinstance(data, Zone):
            return data
        raw_id = data.get("id") if isinstance(data, Mapping) else None
        try:
            zone = handler(data)
        except ValidationError as exc:
            raise zone_invalid(raw_id if isinstance(raw_id, str) else "", _reason(exc)) from None
        if not zone.shape.is_valid:
            raise zone_invalid(zone.id, f"not a simple polygon: {explain_validity(zone.shape)}")
        if zone.provider is not None:
            provider = _context_registry(info).get(zone.provider)
            if provider is None:
                raise zone_invalid(zone.id, f"provider {zone.provider} is not in the registry")
            if zone.zl > provider.max_zl:
                raise zone_invalid(
                    zone.id,
                    f"zoom level {zone.zl} is above {provider.max_zl}, the maximum of "
                    f"provider {provider.code}",
                )
            if provider.extent_bounds is not None:
                west, south, east, north = zone.shape.bounds
                lon0, lat0, lon1, lat1 = provider.extent_bounds
                if not (west < lon1 and east > lon0 and south < lat1 and north > lat0):
                    raise zone_invalid(
                        zone.id,
                        f"provider {provider.code} covers {provider.extent} only, not this zone",
                    )
        return zone

    @cached_property
    def shape(self) -> Polygon:
        """The polygon in ``(lon, lat)`` degrees."""
        return Polygon(self.polygon)

    @cached_property
    def bounds(self) -> tuple[float, float, float, float]:
        """``(lon_min, lat_min, lon_max, lat_max)``."""
        minx, miny, maxx, maxy = self.shape.bounds
        return (minx, miny, maxx, maxy)


def check_zone_ids(zones: Sequence[Zone]) -> None:
    """``ZONE_INVALID`` when an id is used twice."""
    seen: set[str] = set()
    for zone in zones:
        if zone.id in seen:
            raise zone_invalid(zone.id, "the id is used by another zone")
        seen.add(zone.id)


class ZonesDocument(BaseModel):
    """The ``osxp-zones-1`` document: at most 500 zones, ids unique, order = priority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["osxp-zones-1"] = "osxp-zones-1"
    zones: list[Zone] = Field(default_factory=list)
    tiles: dict[str, TileChoice] = Field(default_factory=dict)
    """What a tile holds of its own, by name (``+46+006``): the map is where a pilot sets it, so
    it is kept beside the zones, in the same file and the same revision."""

    @model_validator(mode="wrap")
    @classmethod
    def _validate(
        cls, data: Any, handler: ModelWrapValidatorHandler[ZonesDocument], info: ValidationInfo
    ) -> ZonesDocument:
        if isinstance(data, ZonesDocument):
            return data
        if isinstance(data, Mapping):
            raw = data.get("zones")
            if isinstance(raw, list) and len(raw) > MAX_ZONES:
                raise too_many_zones(len(raw))
        try:
            doc = handler(data)
        except ValidationError as exc:
            raise zone_invalid("", f"not an {ZONES_FORMAT} document: {_reason(exc)}") from None
        check_zone_ids(doc.zones)
        return doc


def parse_zones_document(
    data: Any, *, registry: Mapping[str, Provider] | None = None
) -> ZonesDocument:
    """Validate and normalise a document (a JSON object); ``ZONE_INVALID`` / ``ZONE_TOO_MANY``."""
    if not isinstance(data, Mapping):
        raise zone_invalid("", f"an {ZONES_FORMAT} document is a JSON object")
    return ZonesDocument.model_validate(data, context={REGISTRY_CONTEXT: registry})


def default_zones_path() -> Path:
    """``$OSXP_HOME/zones.json``."""
    return osxp_home() / ZONES_FILE


_FILE_REMEDY = (
    "Correct the file: UTF-8 JSON holding an osxp-zones-1 document (osxp build --zones also reads "
    "a GeoJSON FeatureCollection)."
)
_SAVED_FILE_REMEDY = (
    "Correct the zones file by hand, or save the zones from the map again: saving replaces it and "
    "keeps the previous file as zones.json.bak."
)


def _file_invalid(path: Path, reason: str, *, remedy: str = _FILE_REMEDY) -> OsxpError:
    """``ZONE_INVALID`` about a zones file as a whole; its ``reason`` names the file."""
    return OsxpError(
        "ZONE_INVALID",
        context={"zone": "", "reason": f"zones file {path}: {reason}", "path": str(path)},
        message=f"Zones file {path} is invalid: {reason}.",
        remedy=remedy,
    )


def _in_file(exc: OsxpError, path: Path, index: int | None = None) -> OsxpError:
    """``exc`` with ``path``, and the zone's ``index`` when given, in its context and message."""
    context = {**exc.context, "path": str(path)}
    if index is not None:
        context["index"] = index
    zone, reason = context.get("zone"), context.get("reason")
    if exc.code != "ZONE_INVALID":
        message = f"{exc.message} (zones file {path})"
    elif zone:
        message = f"Zone {zone} of {path} is invalid: {reason}."
    elif index is not None:
        message = f"Zone number {index + 1} of {path} is invalid: {reason}."
    else:
        message = f"Zones file {path} is invalid: {reason}."
    return OsxpError(exc.code, context=context, message=message, remedy=exc.remedy)


def _read_bytes(path: Path) -> bytes:
    """The bytes of ``path``; ``FileNotFoundError`` passes, another failure is coded."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _file_invalid(
            path,
            f"it cannot be read ({exc})",
            remedy=f"Check that OrthoStudio XP can read {path} (its permissions, a folder in the "
            "way).",
        ) from exc


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a JSON number")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"the number {text} is out of range")
    return value


def _json_value(data: bytes) -> Any:
    """Strict JSON in UTF-8: ``NaN``, ``Infinity`` and numbers beyond a double are refused, so that
    every value read can be answered as JSON again. ``ValueError`` or ``RecursionError``."""
    return json.loads(data.decode("utf-8"), parse_constant=_no_constant, parse_float=_finite_float)


def _read_json(path: Path) -> Any:
    """The JSON value of ``path``; ``FileNotFoundError`` passes, other failures are coded."""
    data = _read_bytes(path)
    try:
        return _json_value(data)
    except (ValueError, RecursionError) as exc:
        raise _file_invalid(path, f"it is not JSON ({exc})") from None


def load_zones(path: Path, *, registry: Mapping[str, Provider] | None = None) -> ZonesDocument:
    """The document at ``path``; an empty one when the file does not exist.

    :func:`read_saved_zones` without its tolerance: an unreadable file, bad JSON or an invalid
    zone raise ``ZONE_INVALID`` (or ``ZONE_TOO_MANY``) with ``path`` in the context, the first
    problem found: saved zones are never ignored silently.
    """
    saved = read_saved_zones(path, registry=registry)
    if saved.problems:
        raise saved.problems[0].error
    return ZonesDocument(zones=list(saved.zones))


def dumps_zones(doc: ZonesDocument) -> str:
    """The document as JSON text, one zone per line (``zones.json`` stays readable)."""
    lines = ["{", f' "format": {json.dumps(doc.format)},']
    if not doc.zones:
        lines.append(' "zones": []')
    else:
        lines.append(' "zones": [')
        last = len(doc.zones) - 1
        for i, zone in enumerate(doc.zones):
            text = json.dumps(zone.model_dump(mode="json"), ensure_ascii=False)
            lines.append(f"  {text}{',' if i < last else ''}")
        lines.append(" ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def save_zones(path: Path, doc: ZonesDocument) -> None:
    """Write ``doc`` at ``path`` atomically; the previous file is kept as ``<name>.bak``."""
    path = Path(path)
    text = dumps_zones(doc)
    try:
        if path.is_file():
            atomic_link_or_copy(path, path.with_name(path.name + ".bak"), link=False)
        atomic_write_text(path, text)
    except OSError as exc:
        raise OsxpError(
            "SYS_WRITE_FAILED", context={"path": str(path), "reason": str(exc)}
        ) from exc


# -- the saved document, read zone by zone ------------------------------------------------------


def zones_revision(data: bytes) -> str:
    """The revision of a zones file: the SHA-256 of its bytes, in hex."""
    return hashlib.sha256(data).hexdigest()


def zones_file_revision(path: Path) -> str:
    """The revision of the file at ``path``, ``""`` when there is none (``ZONE_INVALID`` when it
    cannot be read)."""
    path = Path(path)
    try:
        return zones_revision(_read_bytes(path))
    except FileNotFoundError:
        return ""


def _is_number(value: Any) -> bool:
    """A finite JSON number; a boolean is not one."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an integer beyond a double
        return False


def _is_position(value: Any) -> bool:
    return (
        isinstance(value, list | tuple)
        and len(value) == 2
        and _is_number(value[0])
        and _is_number(value[1])
    )


def _listable(entry: Any) -> bool:
    """The page can show the zone as it is stored: a string ``id``, an integer ``zl``, a
    ``polygon`` list of ``[lon, lat]`` number pairs, a ``provider`` and a ``name`` that are
    strings, null or absent (other keys do not matter)."""
    if not isinstance(entry, Mapping):
        return False
    zl, polygon = entry.get("zl"), entry.get("polygon")
    return (
        isinstance(entry.get("id"), str)
        and _is_number(zl)
        and (isinstance(zl, int) or (isinstance(zl, float) and zl.is_integer()))
        and isinstance(polygon, list)
        and all(_is_position(vertex) for vertex in polygon)
        and all(
            entry.get(key) is None or isinstance(entry.get(key), str)
            for key in ("provider", "name")
        )
    )


def _outline(entry: Any) -> tuple[Bounds | None, Polygon | None]:
    """Where a zone that failed validation lies: the bounds of its usable vertices, and its
    polygon when they make a valid one (``None`` otherwise)."""
    polygon = entry.get("polygon") if isinstance(entry, Mapping) else None
    if not isinstance(polygon, list):
        return None, None
    vertices = [(float(v[0]), float(v[1])) for v in polygon if _is_position(v)]
    if not vertices:
        return None, None
    lons, lats = [lon for lon, _ in vertices], [lat for _, lat in vertices]
    bounds = (min(lons), min(lats), max(lons), max(lats))
    try:
        shape = Polygon(vertices)
        usable = shape.is_valid and shape.area > MIN_PART_AREA
    except (ValueError, GEOSException):  # fewer than 3 distinct vertices
        return bounds, None
    return bounds, shape if usable else None


def _reaches(bounds: Bounds, shape: Polygon | None, tile: TileRef) -> bool:
    """A part of ``shape`` above :data:`MIN_PART_AREA` lies in ``tile``; without a shape, the
    ``bounds`` overlap the tile (more than along an edge)."""
    lon_min, lat_min, lon_max, lat_max = bounds
    west, south = tile.lon, tile.lat
    if lon_max <= west or lon_min >= west + 1 or lat_max <= south or lat_min >= south + 1:
        return False
    return shape is None or bool(_clip(shape, _cell(tile)))


@dataclass(frozen=True, slots=True)
class ZoneProblem:
    """A problem of the saved zones document, found by :func:`read_saved_zones`.

    ``index`` is the position of the zone in the document's ``zones`` list, ``zone`` its id when
    the zone is listed (``None`` when it cannot be shown); both are ``None`` for the file itself.
    ``error`` is what a build refused because of it raises.
    """

    zone: str | None
    index: int | None
    error: OsxpError
    bounds: Bounds | None = None
    """``(lon_min, lat_min, lon_max, lat_max)`` of the zone's usable vertices; ``None`` when no
    tile can be told (a problem of the file, a zone without one usable vertex)."""
    shape: Polygon | None = None
    """The zone's polygon when its vertices make a valid one; else the bounds alone decide."""

    def touches(self, tile: TileRef) -> bool:
        """The zone would change ``tile``: a part of its polygon lies in the tile, as for a valid
        zone, or, without a valid polygon, its bounds overlap the tile."""
        return self.bounds is not None and _reaches(self.bounds, self.shape, tile)

    def to_dict(self) -> dict[str, Any]:
        """One entry of ``problems`` in ``GET /api/zones``."""
        reason = self.error.context.get("reason")
        return {
            "zone": self.zone,
            "index": self.index,
            "code": self.error.code,
            "reason": str(reason) if reason else self.error.message,
            "message": self.error.message,
        }


@dataclass(frozen=True, slots=True)
class SavedZones:
    """The saved zones document as :func:`read_saved_zones` found it."""

    path: Path
    revision: str
    """SHA-256 of the file's bytes in hex; ``""`` when there is no file."""
    readable: bool = True
    """``False`` when no zone can be read from the file (not JSON, not an ``osxp-zones-1``
    object): ``zones`` is empty and ``problems`` holds the file's problem."""
    listed: tuple[dict[str, Any], ...] = ()
    """The zones the page shows, in document order: a valid zone normalised, a zone with a
    problem as stored when it can be shown."""
    zones: tuple[Zone, ...] = ()
    """The valid zones, in document order (the ones a build may use)."""
    tiles: Mapping[str, TileChoice] = field(default_factory=dict)
    """What each tile holds of its own (its colours), by name."""
    problems: tuple[ZoneProblem, ...] = ()

    def to_json(self) -> dict[str, Any]:
        """The answer of ``GET /api/zones``: ``{format, revision, zones, problems}``."""
        return {
            "format": ZONES_FORMAT,
            "revision": self.revision,
            "zones": list(self.listed),
            "tiles": {name: choice.model_dump(mode="json") for name, choice in self.tiles.items()},
            "problems": [problem.to_dict() for problem in self.problems],
        }

    def zones_for(self, tiles: Iterable[TileRef]) -> list[Zone]:
        """The valid zones touching at least one of ``tiles``, for a build of those tiles.

        Raises the file's error when no zone can be read, else the error of the first zone with
        a problem that touches one of ``tiles``; a problem touching none of them is ignored.
        """
        refs = list(tiles)
        if not self.readable:
            raise self.problems[0].error
        for problem in self.problems:
            if any(problem.touches(tile) for tile in refs):
                raise problem.error
        return [
            zone for zone in self.zones if any(_reaches(zone.bounds, zone.shape, t) for t in refs)
        ]


def _document_fault(value: Any) -> str | None:
    """Why no zone can be read from ``value``, or ``None``. An object with keys but neither
    ``format`` nor ``zones`` (a GeoJSON file saved in its place) is not a zones document."""
    if not isinstance(value, Mapping) or (value and "format" not in value and "zones" not in value):
        return f"it is not an {ZONES_FORMAT} object"
    if value.get("format", ZONES_FORMAT) != ZONES_FORMAT:
        return f"its format is not {ZONES_FORMAT}"
    if not isinstance(value.get("zones", []), list):
        return "its zones are not a list"
    return None


def _past_the_limit(zone: str, index: int, count: int, path: Path) -> OsxpError:
    """``ZONE_TOO_MANY`` for a zone after the 500th of a saved document."""
    exc = too_many_zones(count)
    reason = f"zone {index + 1} of {count}, a document holds at most {MAX_ZONES}"
    return OsxpError(
        exc.code,
        context={**exc.context, "zone": zone, "index": index, "path": str(path), "reason": reason},
        message=f"Zone {zone} of {path} is past the limit: the document holds {count} zones, "
        f"at most {MAX_ZONES}.",
        remedy=exc.remedy,
    )


def _zone_by_zone(
    path: Path, value: Mapping[str, Any], registry: Mapping[str, Provider] | None
) -> tuple[list[dict[str, Any]], list[Zone], list[ZoneProblem]]:
    """The listed zones, the valid zones and the problems of a document that was refused."""
    listed: list[dict[str, Any]] = []
    zones: list[Zone] = []
    problems: list[ZoneProblem] = []
    extra = sorted(str(key) for key in value if key not in ("format", "zones"))
    if extra:
        detail = "unexpected key(s) " + ", ".join(extra)
        problems.append(
            ZoneProblem(None, None, _file_invalid(path, detail, remedy=_SAVED_FILE_REMEDY))
        )
    entries: list[Any] = value.get("zones", [])
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        zone: Zone | None = None
        error: OsxpError | None = None
        try:
            if not isinstance(entry, Mapping):
                raise zone_invalid("", "a zone is a JSON object")
            zone = Zone.model_validate(entry, context={REGISTRY_CONTEXT: registry})
        except OsxpError as exc:
            if not exc.code.startswith("ZONE_"):
                raise
            error = _in_file(exc, path, index)
        else:
            if zone.id in seen:
                error = _in_file(
                    zone_invalid(zone.id, "the id is used by another zone"), path, index
                )
            elif index >= MAX_ZONES:
                error = _past_the_limit(zone.id, index, len(entries), path)
        zone_id: str | None = None
        if zone is not None and error is None:
            zone_id = zone.id
            listed.append(zone.model_dump(mode="json"))
            zones.append(zone)
        elif error is not None:
            if _listable(entry):
                zone_id = entry["id"]
                listed.append(dict(entry))
            elif zone is not None:  # valid on its own, stored with types the page cannot read
                zone_id = zone.id
                listed.append(zone.model_dump(mode="json"))
            bounds, shape = (zone.bounds, zone.shape) if zone is not None else _outline(entry)
            problems.append(ZoneProblem(zone_id, index, error, bounds, shape))
        if zone_id is not None:
            seen.add(zone_id)
    return listed, zones, problems


def read_saved_zones(path: Path, *, registry: Mapping[str, Provider] | None = None) -> SavedZones:
    """The saved document at ``path``, never refused whole (spec ``map-zones.md`` 3 and 5).

    Each zone is checked on its own, in document order, and has at most one problem. A valid
    zone is listed normalised and kept for the builds. A zone that fails validation, repeats the
    id of a zone listed before it or comes after the 500th is reported in ``problems`` with its
    index and listed as stored when the page can show it (see :func:`_listable`); otherwise it
    is left out, unless it passed validation itself: then it is listed normalised. Keys other
    than ``format`` and ``zones`` are one problem of the file, and the zones are still read.

    A file that is not JSON, or not an ``osxp-zones-1`` object, lists no zone and has one
    problem naming the file (``readable`` is false). No file: nothing, revision ``""``. Only a
    file that cannot be read raises (``ZONE_INVALID``). The file is never written.
    """
    path = Path(path)
    try:
        data = _read_bytes(path)
    except FileNotFoundError:
        return SavedZones(path, "")
    revision = zones_revision(data)
    value: Any = None
    fault: str | None
    try:
        value = _json_value(data)
    except (ValueError, RecursionError) as exc:
        fault = f"it is not JSON ({exc})"
    else:
        fault = _document_fault(value)
    if fault is not None:
        error = _file_invalid(path, fault, remedy=_SAVED_FILE_REMEDY)
        return SavedZones(
            path, revision, readable=False, problems=(ZoneProblem(None, None, error),)
        )
    try:
        document = parse_zones_document(value, registry=registry)
    except OsxpError as exc:
        if not exc.code.startswith("ZONE_"):
            raise
        listed, zones, problems = _zone_by_zone(path, value, registry)
        if not problems:  # never expected: what refuses a document is a problem found there
            problem = ZoneProblem(None, None, _in_file(exc, path))
            return SavedZones(path, revision, readable=False, problems=(problem,))
        return SavedZones(
            path, revision, listed=tuple(listed), zones=tuple(zones), problems=tuple(problems)
        )
    listed = [zone.model_dump(mode="json") for zone in document.zones]
    return SavedZones(
        path,
        revision,
        listed=tuple(listed),
        zones=tuple(document.zones),
        tiles=dict(document.tiles),
    )


# -- zones files of the command line ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoadedZones:
    """A zones file read by :func:`read_zones_file`."""

    document: ZonesDocument
    source: Literal["osxp-zones-1", "geojson"]
    holes_dropped: int = 0
    """Interior rings of GeoJSON polygons dropped (zones fill their exterior ring, M6)."""


def _feature_id(properties: Mapping[str, Any], index: int) -> str:
    value = properties.get("id")
    if value is None or isinstance(value, bool):
        return f"feature-{index + 1}"
    return str(value)


def _ring(value: Any, where: str, zone: str) -> list[list[Any]]:
    if not isinstance(value, list):
        raise zone_invalid(zone, f"{where}: a ring is a list of positions")
    out: list[list[Any]] = []
    for position in value:
        if not isinstance(position, list | tuple) or len(position) < 2:
            raise zone_invalid(zone, f"{where}: a position is [lon, lat]")
        out.append([position[0], position[1]])  # an altitude is ignored
    return out


def zones_from_geojson(
    data: Mapping[str, Any], *, registry: Mapping[str, Provider] | None = None
) -> LoadedZones:
    """A GeoJSON ``FeatureCollection`` of ``Polygon`` / ``MultiPolygon`` features as zones.

    ``properties.zl`` is required; ``provider``, ``name`` and ``id`` are optional (id
    ``feature-<n>`` by default). Features keep their order (the priority); the parts of a
    ``MultiPolygon`` become adjacent zones ``<id>-1``, ``<id>-2``... Interior rings are dropped
    and counted.
    """
    features = data.get("features")
    if data.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise zone_invalid("", "a GeoJSON zones file is a FeatureCollection with a features list")
    raw_zones: list[dict[str, Any]] = []
    holes = 0
    for index, feature in enumerate(features):
        where = f"feature {index + 1}"
        if not isinstance(feature, Mapping) or feature.get("type") != "Feature":
            raise zone_invalid("", f"{where} is not a GeoJSON Feature")
        properties = feature.get("properties") or {}
        if not isinstance(properties, Mapping):
            raise zone_invalid("", f"{where}: properties is not an object")
        zone_id = _feature_id(properties, index)
        if "zl" not in properties:
            raise zone_invalid(zone_id, f"{where}: properties.zl (the zoom level) is required")
        geometry = feature.get("geometry")
        kind = geometry.get("type") if isinstance(geometry, Mapping) else None
        coordinates = geometry.get("coordinates") if isinstance(geometry, Mapping) else None
        if kind == "Polygon":
            polygons = [coordinates]
        elif kind == "MultiPolygon" and isinstance(coordinates, list):
            polygons = coordinates
        else:
            raise zone_invalid(
                zone_id, f"{where}: geometry {kind} is not a Polygon or MultiPolygon"
            )
        for part, rings in enumerate(polygons):
            if not isinstance(rings, list) or not rings:
                raise zone_invalid(zone_id, f"{where}: a polygon without rings")
            holes += len(rings) - 1
            part_id = zone_id if len(polygons) == 1 else f"{zone_id}-{part + 1}"
            raw_zones.append(
                {
                    "id": part_id,
                    "name": properties.get("name"),
                    "zl": properties["zl"],
                    "provider": properties.get("provider"),
                    "polygon": _ring(rings[0], where, part_id),
                }
            )
    document = parse_zones_document({"format": ZONES_FORMAT, "zones": raw_zones}, registry=registry)
    return LoadedZones(document, "geojson", holes)


def read_zones_file(path: Path, *, registry: Mapping[str, Provider] | None = None) -> LoadedZones:
    """An ``osxp-zones-1`` document or a GeoJSON ``FeatureCollection`` (``osxp build --zones``).

    Unlike :func:`load_zones`, a missing file is an error: the user named it.
    """
    path = Path(path).expanduser()
    try:
        data = _read_json(path)
    except FileNotFoundError:
        raise _file_invalid(path, "the file does not exist") from None
    try:
        if isinstance(data, Mapping) and data.get("type") == "FeatureCollection":
            return zones_from_geojson(data, registry=registry)
        if isinstance(data, Mapping) and data.get("format") == ZONES_FORMAT:
            return LoadedZones(parse_zones_document(data, registry=registry), "osxp-zones-1")
    except OsxpError as exc:
        raise _in_file(exc, path) from None
    raise _file_invalid(path, f"neither an {ZONES_FORMAT} document nor a GeoJSON FeatureCollection")


# -- clipping -----------------------------------------------------------------------------------


def _cell(tile: TileRef) -> Polygon:
    return box(tile.lon, tile.lat, tile.lon + 1, tile.lat + 1)


def _polygons(geometry: BaseGeometry) -> list[Polygon]:
    """The polygons of an overlay result (lines and points of a touching contact dropped)."""
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon | GeometryCollection):
        out: list[Polygon] = []
        for part in geometry.geoms:
            out.extend(_polygons(part))
        return out
    return []


def _clip(shape: Polygon, cell: Polygon) -> list[Polygon]:
    """The parts of ``shape`` inside ``cell`` above :data:`MIN_PART_AREA`, holes included."""
    parts = [shape] if cell.contains(shape) else _polygons(shape.intersection(cell))
    return [p for p in parts if p.area > MIN_PART_AREA]


def _entry_ring(part: Polygon) -> list[float] | None:
    """``[lat0, lon0, lat1, lon1, ..., lat0, lon0]`` of the exterior ring, 9 decimals.

    Canonical whatever the overlay library returns: counter-clockwise, starting at the
    smallest ``(lon, lat)`` vertex, consecutive duplicates removed. ``None`` when fewer than 3
    distinct vertices remain after rounding.
    """
    coords = orient(Polygon(part.exterior), 1.0).exterior.coords
    ring: list[tuple[float, float]] = []
    for lon, lat in list(coords)[:-1]:
        vertex = (_coordinate(lon), _coordinate(lat))
        if not ring or ring[-1] != vertex:
            ring.append(vertex)
    while len(ring) > 1 and ring[-1] == ring[0]:
        ring.pop()
    if len(ring) < MIN_VERTICES:
        return None
    start = min(range(len(ring)), key=ring.__getitem__)
    ring = ring[start:] + ring[:start]
    out: list[float] = []
    for lon, lat in [*ring, ring[0]]:
        out += [lat, lon]
    return out


def _entries(parts: Sequence[Polygon], zl: int, provider: str) -> tuple[list[ZoneEntry], int]:
    """One entry per part, parts in a stable order; the number of interior rings dropped."""
    rings = [ring for part in parts if (ring := _entry_ring(part)) is not None]
    rings.sort(key=lambda r: (r[1], r[0]))
    holes = sum(len(part.interiors) for part in parts)
    return [(ring, zl, provider) for ring in rings], holes


def _check_level(
    zone: Zone,
    code: str,
    tile: TileRef,
    registry: Mapping[str, Provider],
    mesh_zl: int | None,
) -> None:
    if mesh_zl is not None and zone.zl > mesh_zl:
        raise OsxpError(
            "ZONE_INVALID",
            context={
                "zone": zone.id,
                "reason": f"zoom level {zone.zl} is above mesh_zl {mesh_zl} of tile {tile.name}",
                "tile": tile.name,
            },
            remedy=f"Lower the zone to ZL{mesh_zl}, or raise the tile's mesh_zl to {zone.zl} "
            "(an expert setting: the mesh caps the zoom level of its textures).",
        )
    provider = registry.get(code)
    if provider is None:
        if zone.provider is None:
            known = ", ".join(sorted(registry))
            raise OsxpError(
                "CFG_PROVIDER_UNKNOWN",
                context={"provider": code, "known": known},
                message=f"Provider {code!r} of tile {tile.name} is not in the registry ({known}).",
            )
        raise zone_invalid(zone.id, f"provider {code} is not in the registry", tile=tile.name)
    if zone.zl > provider.max_zl:
        raise zone_invalid(
            zone.id,
            f"zoom level {zone.zl} is above {provider.max_zl}, the maximum of provider {code} "
            f"(tile {tile.name})",
            tile=tile.name,
        )


def zones_for_tile(
    zones: Sequence[Zone],
    tile: TileRef,
    provider: str,
    *,
    registry: Mapping[str, Provider] | None = None,
    mesh_zl: int | None = None,
) -> list[ZoneEntry]:
    """The ``zone_list`` of ``tile``: every zone clipped to the tile, in document order.

    Each part above :data:`MIN_PART_AREA` becomes ``([lat0, lon0, ..., lat0, lon0], zl,
    provider)`` with the zone's provider or else ``provider`` (the tile's); the parts of one
    zone are adjacent, so index 0 still wins (M4). Holes are dropped (Ortho4XP fills exterior
    rings, M6) and logged. A zoom level above the provider's ``max_zl``, or above ``mesh_zl``
    when it is given (the DSF gives every mesh cell one texture: a finer zone would be
    stretched over the cell), is ``ZONE_INVALID``; more than 254 entries is
    ``ZONE_TOO_MANY``. An empty list when no zone touches the tile.
    """
    reg = _registry(registry)
    cell = _cell(tile)
    west, south = tile.lon, tile.lat
    entries: list[ZoneEntry] = []
    holes = 0
    for zone in zones:
        lon_min, lat_min, lon_max, lat_max = zone.bounds
        if lon_max <= west or lon_min >= west + 1 or lat_max <= south or lat_min >= south + 1:
            continue
        parts = _clip(zone.shape, cell)
        if not parts:
            continue
        code = zone.provider if zone.provider is not None else provider
        _check_level(zone, code, tile, reg, mesh_zl)
        part_entries, part_holes = _entries(parts, zone.zl, code)
        entries.extend(part_entries)
        holes += part_holes
    if len(entries) > MAX_TILE_ENTRIES:
        raise too_many_zones(len(entries), tile=tile)
    if holes:
        log.warning(
            "%s: %d hole(s) of clipped zones dropped (zones fill their exterior ring only)",
            tile.name,
            holes,
        )
    return entries


def photo_zone_entries(zones: Sequence[Zone], tile: TileRef) -> list[list[Any]]:
    """The parts of the zones that name their own colours, clipped to ``tile``.

    ``[[lat0, lon0, ..., lat0, lon0], brightness, contrast, saturation]`` per part, in document
    order like ``zone_list``: the textures stage reads them and gives each texture the colours
    of the first part its centre falls in (``pipeline-textures.md`` 6, a user asked for colours
    per zone, 2026-09-18). A zone whose ``photo.look`` is ``None`` is absent: it inherits the
    tile's colours.
    """
    cell = _cell(tile)
    out: list[list[Any]] = []
    for zone in zones:
        values = zone.photo.values()
        if values is None:
            continue
        lon_min, lat_min, lon_max, lat_max = zone.bounds
        if lon_max <= tile.lon or lon_min >= tile.lon + 1:
            continue
        if lat_max <= tile.lat or lat_min >= tile.lat + 1:
            continue
        brightness, contrast, saturation = values
        for part in _clip(zone.shape, cell):
            ring = _entry_ring(part)
            if ring is not None:
                out.append([ring, brightness, contrast, saturation])
    return out


def with_zone_list(
    config: Mapping[str, Any], entries: Sequence[ZoneEntry], tile: TileRef
) -> dict[str, Any]:
    """``config`` plus ``zone_list`` = ``entries``, only when there are entries.

    The entries are stored as JSON lists (``[[lat0, lon0, ...], zl, provider]``, what Ortho4XP's
    GUI writes), so a job reloaded from its JSON journal keeps the same keys. A ``zone_list``
    already in ``config`` (an override) and zones on the same tile are refused.
    """
    out = dict(config)
    if not entries:
        return out
    if out.get("zone_list"):
        raise OsxpError(
            "CFG_VALUE_INVALID",
            context={"name": "zone_list", "value": "override", "type": "list", "range": "-"},
            message=f"Tile {tile.name} has both a zone_list override and zones drawn on the map.",
            remedy="Remove the zone_list override (--set zone_list or overrides), or build "
            "without zones.",
        )
    out["zone_list"] = [[list(coords), int(zl), str(code)] for coords, zl, code in entries]
    return out


def with_tile_photo(config: Mapping[str, Any], choice: TileChoice | None) -> dict[str, Any]:
    """``config`` with the tile's own colours, when it names any (Settings' otherwise).

    The three levels are Settings, the tile, then the zones: this is the middle one, and the
    zones of that tile override it inside their polygons (a user, 2026-09-18).
    """
    values = choice.photo.values() if choice is not None else None
    if values is None:
        return dict(config)
    names = ("photo_brightness", "photo_contrast", "photo_saturation")
    return {**config, **dict(zip(names, values, strict=True))}


def with_photo_zones(
    config: Mapping[str, Any], zones: Sequence[Zone], tile: TileRef
) -> dict[str, Any]:
    """``config`` plus ``photo_zones`` when a zone of ``tile`` names its own colours."""
    entries = photo_zone_entries(zones, tile)
    return {**config, "photo_zones": entries} if entries else dict(config)


def tiles_touched(zone: Zone) -> list[TileRef]:
    """The tiles where the zone has a part above :data:`MIN_PART_AREA`, south-west first."""
    lon_min, lat_min, lon_max, lat_max = zone.bounds
    lons = range(max(-180, math.floor(lon_min)), min(179, math.ceil(lon_max) - 1) + 1)
    lats = range(max(-90, math.floor(lat_min)), min(89, math.ceil(lat_max) - 1) + 1)
    return [
        TileRef(lat, lon)
        for lat in lats
        for lon in lons
        if _clip(zone.shape, _cell(TileRef(lat, lon)))
    ]


# -- textures (estimate) ------------------------------------------------------------------------


def _textures_of(part: Polygon, zl: int, provider: str) -> set[TextureId]:
    """Textures at ``zl`` whose square overlaps ``part`` by more than :data:`MIN_PART_AREA`.

    Row by row of the texture grid, so that a large part at a high zoom level never holds
    more than one row of squares in memory.
    """
    lon_min, lat_min, lon_max, lat_max = part.bounds
    first = texture_at(lat_max, lon_min, zl, provider)
    last = texture_at(lat_min, lon_max, zl, provider)
    xs = np.arange(first.til_x, last.til_x + 1, TEXTURE_TILES, dtype=np.int64)
    west = (xs / 2 ** (zl - 1) - 1) * 180  # tile_to_wgs84, vectorised
    east = ((xs + TEXTURE_TILES) / 2 ** (zl - 1) - 1) * 180
    shapely.prepare(part)
    found: set[TextureId] = set()
    for til_y in range(first.til_y, last.til_y + 1, TEXTURE_TILES):
        north = tile_to_wgs84(0, til_y, zl)[0]
        south = tile_to_wgs84(0, til_y + TEXTURE_TILES, zl)[0]
        squares = shapely.box(west, south, east, north)
        hit = shapely.intersects(squares, part)
        if not hit.any():
            continue
        areas = shapely.area(shapely.intersection(squares[hit], part))
        found.update(
            TextureId(int(til_x), til_y, zl, provider) for til_x in xs[hit][areas > MIN_PART_AREA]
        )
    return found


def zone_textures(zone: Zone, tile: TileRef) -> int:
    """Textures at the zone's zoom level overlapping the zone clipped to ``tile`` (estimate)."""
    code = zone.provider or ""
    found: set[TextureId] = set()
    for part in _clip(zone.shape, _cell(tile)):
        found |= _textures_of(part, zone.zl, code)
    return len(found)


def zone_list_textures(zone_list: Iterable[Sequence[Any]], tile: TileRef) -> set[TextureId]:
    """The textures a ``zone_list`` adds to ``tile``: for each entry, the textures at its zoom
    level whose square overlaps the entry clipped to the tile (an upper bound of what the DSF
    uses). Accepts any Ortho4XP ``zone_list``: an invalid ring is repaired first."""
    cell = _cell(tile)
    found: set[TextureId] = set()
    for index, entry in enumerate(zone_list):
        try:
            coords, zl, provider = entry
            level = int(zl)
            ring = [(float(x), float(y)) for x, y in zip(coords[1::2], coords[::2], strict=True)]
            if not 0 <= level <= 24:
                raise ValueError(f"zoom level {level} outside [0, 24]")
        except (TypeError, ValueError) as exc:
            raise OsxpError(
                "CFG_ZONE_LIST_INVALID",
                context={"index": index, "tile": tile.name, "reason": str(exc)},
            ) from None
        if len(ring) < MIN_VERTICES:
            continue
        shape: BaseGeometry = Polygon(ring)
        if not shape.is_valid:
            shape = make_valid(shape)
        for part in _polygons(shape.intersection(cell)):
            if part.area > MIN_PART_AREA:
                found |= _textures_of(part, level, str(provider))
    return found
