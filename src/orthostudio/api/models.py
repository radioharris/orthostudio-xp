"""Request schemas of the API (pydantic, ``extra='forbid'``): spec ``api.md`` section 2.2."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orthostudio.errors import OsxpError
from orthostudio.zones import MAX_ZONES, check_zone_ids, too_many_zones
from orthostudio.zones import TileChoice as TileChoiceModel
from orthostudio.zones import Zone as ZoneModel

__all__ = [
    "MAX_OVERRIDES",
    "MAX_TILES",
    "AirportArea",
    "CleanRequest",
    "DeleteRequest",
    "ImportRequest",
    "InstallRequest",
    "JobRequest",
    "PlanRequest",
    "UninstallRequest",
    "ZoneModel",
    "tile_error",
]

MAX_TILES = 64
MAX_OVERRIDES = 64
_TILE_RE = re.compile(r"^([+-]\d{2})([+-]\d{3})$")
_ICAO_RE = re.compile(r"^[A-Z0-9]{2,7}$")


def tile_error(value: str) -> OsxpError:
    return OsxpError(
        "CFG_LATLON_INVALID",
        context={"value": value},
        message=f"Tile {value!r} is not of the form +43+005.",
        remedy="Give the south-west corner as sLLsLLL, e.g. +43+005 or -34-059.",
    )


def _check_tile(value: str) -> str:
    name = value.strip()
    m = _TILE_RE.match(name)
    if m is None:
        raise tile_error(value)
    lat, lon = int(m.group(1)), int(m.group(2))
    if not (-90 <= lat <= 89 and -180 <= lon <= 179):
        raise tile_error(value)
    return name


class AirportArea(BaseModel):
    model_config = ConfigDict(extra="forbid")
    icao: str = Field(min_length=2, max_length=7)
    radius_km: float = Field(default=0.0, ge=0.0, le=300.0)

    @field_validator("icao")
    @classmethod
    def _icao(cls, v: str) -> str:
        v = v.strip().upper()
        if not _ICAO_RE.match(v):
            raise ValueError("ICAO code: 2 to 7 letters or digits")
        return v


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tiles: list[str] | None = Field(default=None, max_length=MAX_TILES)
    airport: AirportArea | None = None
    provider: str | None = Field(default=None, max_length=32)
    zoom_level: int | None = Field(default=None, ge=10, le=19)
    overrides: dict[str, Any] = Field(default_factory=dict)
    xplane_dir: str | None = Field(default=None, max_length=1024)
    overlay: bool = True
    xp12_rasters: bool = True
    online: bool = False
    zones: list[ZoneModel] | None = None
    """``None``: the saved zones document; a list (possibly empty): exactly those zones
    (``map-zones.md`` 5). Each is validated like a zone of the document (``ZONE_INVALID``)."""
    tiles_settings: dict[str, TileChoiceModel] | None = None
    """What each square carries of its own (its colours), by name. ``None`` takes the saved
    document's, as ``zones`` does -- but a request that carries its own zones and not this built
    the tiles without their colours (a user built a black and white square and X-Plane showed it
    as usual, 2026-09-18)."""

    @field_validator("tiles")
    @classmethod
    def _tiles(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        out: list[str] = []
        for t in v:
            name = _check_tile(t)
            if name not in out:
                out.append(name)
        return out

    @field_validator("overrides")
    @classmethod
    def _overrides(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > MAX_OVERRIDES:
            raise ValueError(f"at most {MAX_OVERRIDES} overrides")
        return v

    @field_validator("zones", mode="before")
    @classmethod
    def _zones_count(cls, v: Any) -> Any:
        if isinstance(v, list) and len(v) > MAX_ZONES:
            raise too_many_zones(len(v))
        return v

    @field_validator("zones")
    @classmethod
    def _zones_ids(cls, v: list[ZoneModel] | None) -> list[ZoneModel] | None:
        if v is not None:
            check_zone_ids(v)
        return v

    def area(self) -> tuple[list[str] | None, AirportArea | None]:
        if (self.tiles is None or not self.tiles) == (self.airport is None):
            raise OsxpError(
                "CFG_LATLON_INVALID",
                context={"value": ""},
                message="Give either tiles or an airport, not both nor neither.",
                remedy='Send {"tiles": ["+43+005"]} or {"airport": {"icao": "LFML"}}.',
            )
        return self.tiles, self.airport


class JobRequest(PlanRequest):
    install: bool = False
    queue: bool = False
    """Wait behind the build running or queued, instead of a 409 ``SYS_BUSY`` (the page queues)."""


class SourceRequest(BaseModel):
    """An imagery source the user adds (``POST /api/sources``): its terms apply to that user."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    url_template: str = Field(min_length=10, max_length=2000)
    """A tile's address, ``{x}`` ``{y}`` ``{zoom}`` or ``{quadkey}`` in place of its numbers."""
    max_zl: int = Field(default=19, ge=1, le=22)


class SourceTestRequest(BaseModel):
    """One tile asked for through an address before it is added (``POST /api/sources/test``)."""

    model_config = ConfigDict(extra="forbid")
    url_template: str = Field(min_length=10, max_length=2000)
    max_zl: int = Field(default=19, ge=1, le=22)
    lat: float = Field(default=46.2, ge=-85, le=85)
    lon: float = Field(default=6.1, ge=-180, le=180)


class OverlaysRequest(BaseModel):
    """Leave the roads, forests and buildings of squares to the other packs' overlays (``others``),
    or draw the tiles' own again (``own``): ``POST /api/library/overlays``."""

    model_config = ConfigDict(extra="forbid")
    use: Literal["others", "own"]
    tiles: list[str] = Field(min_length=1, max_length=500)
    xplane_dir: str | None = Field(default=None, max_length=1024)

    @field_validator("tiles")
    @classmethod
    def _tile_names(cls, value: list[str]) -> list[str]:
        for name in value:
            if not re.fullmatch(r"[+-]\d{2}[+-]\d{3}", name):
                raise ValueError(f"{name!r} is not a tile name like +46+006")
        return value


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queue: bool = False
    """As ``JobRequest.queue``."""


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder: str = Field(min_length=1, max_length=1024)
    """The Ortho4XP folder whose tiles the Library imports (it holds ``Ortho4XP.py``)."""


class QuitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False
    """Stop a running build too (the page asks first)."""


class ChooseFolderRequest(BaseModel):
    """A folder asked for in the platform's own dialog (``POST /api/choose-folder``)."""

    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=200)
    """The dialog's line, in the page's language."""
    start: str | None = Field(default=None, max_length=4096)
    """The folder the dialog opens in, when it exists."""


class RevealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)


class InstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    xplane_dir: str | None = Field(default=None, max_length=1024)
    link: bool = True
    path: str | None = Field(default=None, max_length=4096)
    """The ``path`` of the library row, as ``GET /api/library`` gives it (see ``DeleteRequest``);
    ``None``: the newest row of the name."""


class UninstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    xplane_dir: str | None = Field(default=None, max_length=1024)
    path: str | None = Field(default=None, max_length=4096)
    """The ``path`` of the library row, as ``GET /api/library`` gives it (see ``DeleteRequest``);
    ``None``: the newest row of the name."""


class CleanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    images: bool = False
    """Also empty the downloaded image pieces and the map background (the Library's checkbox)."""
    relief: bool = False
    """Also empty the elevation cells downloaded and kept: its own checkbox, since the relief of a
    square costs far less to fetch again than its imagery (a user found 1.4 GB left after emptying
    everything, 2026-09-18)."""


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    xplane_dir: str | None = Field(default=None, max_length=1024)
    path: str | None = Field(default=None, max_length=4096)
    """The ``path`` of the library row to delete, as ``GET /api/library`` gives it: a tile built
    into two output folders is in the library twice under the same name, so the name alone does not
    say which row the user clicked."""
