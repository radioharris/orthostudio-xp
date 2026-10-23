# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP Live's settings: the ``[stream]`` table of ``live.toml``.

The Live window saves them, and ``python -m orthostudio.live stream`` takes them as its defaults:
a disk budget set in the window must hold on the command line too, or the streamer would make the
textures built beyond it coarser. The file's other keys, ``enabled`` first, are written back as
they were read.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orthostudio.config.store import toml_dumps
from orthostudio.decals import DECALS
from orthostudio.fsutil import atomic_write_text
from orthostudio.live import settings_file

TABLE = "stream"
TEXTURES_SHARE = 0.5  # of Live's disk space: the textures built; the rest, the downloaded imagery
OLD_BUDGETS = {"disk_budget_gb": 20.0, "cache_budget_gb": 10.0}  # the two spaces set before, apart
# The least disk space Live is given, by its zooms: a 20-minute turning flight at Toulouse took 3.9
# GB of textures at ZL16/18 and 11.7 GB at ZL17/19 given 4 GB (2026-10-12): Live never makes coarser
# a texture X-Plane may read, so a smaller space was not kept, only exceeded
DISK_MIN_GB = 10.0
DISK_MIN_SHARP_GB = 20.0  # ZL17 or ZL19: 8192 px textures


SEASON_CHOICES = ("auto", "spring", "summer", "autumn", "winter")


class LookValues(BaseModel):
    """A season's five colour values (``orthostudio.live.look.Look``), each a deviation: 0
    changes nothing. The window's sliders, with their ends."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    brightness: float = Field(0.0, ge=-0.5, le=0.5)
    contrast: float = Field(0.0, ge=-0.5, le=0.5)
    saturation: float = Field(0.0, ge=-1.0, le=1.0)
    temperature: float = Field(0.0, ge=-1.0, le=1.0)
    vegetation: float = Field(0.0, ge=-1.0, le=1.0)

    def look(self):
        from orthostudio.live.look import Look

        return Look(**self.model_dump())


def _default(season: str) -> LookValues:
    from orthostudio.live.look import DEFAULT_LOOKS

    return LookValues(**DEFAULT_LOOKS[season].__dict__)


class SeasonLooks(BaseModel):
    """Each season's colours: Live's own (``look.DEFAULT_LOOKS``) until the pilot moves them."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    spring: LookValues = Field(default_factory=lambda: _default("spring"))
    summer: LookValues = Field(default_factory=lambda: _default("summer"))
    autumn: LookValues = Field(default_factory=lambda: _default("autumn"))
    winter: LookValues = Field(default_factory=lambda: _default("winter"))

    def looks(self) -> dict:
        return {
            name: getattr(self, name).look() for name in ("spring", "summer", "autumn", "winter")
        }


def disk_floor(max_zoom: int, max_zoom_airports: int) -> float:
    """The least disk space Live is given at these zooms."""
    return DISK_MIN_SHARP_GB if max_zoom > 16 or max_zoom_airports > 18 else DISK_MIN_GB


LIVE_DECAL = "maquify_2_green_key.dcl"
"""The decal of Live's squares until the pilot chooses another: Ortho4XP's, as OrthoStudio XP's
tiles and XPME have it (the user, 2026-10-23: its larger, stronger grain looks better on the land
than ``grass_and_stony_dirt_1.dcl``, Live's default from 2026-10-18)."""


class LiveSettings(BaseModel):
    """What a stream is built with, beyond where X-Plane is."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    disk_gb: float = Field(20.0, ge=DISK_MIN_GB, le=20_000.0)
    """Disk Live may take, one space as AutoOrtho's and XPME's cache: half for the textures built
    (beyond it, the farthest are made coarser), half for the downloaded imagery (beyond it, the
    oldest goes). Two spaces, set apart, confused a user (2026-09-27): which one to raise? Never
    less than :func:`disk_floor` for the zooms: a smaller one is raised to it (``_disk_floor``).
    20 GB by default, both zooms' floor, 40 GB until 2026-10-12."""
    max_zoom: int = Field(16, ge=14, le=17)
    """AutoOrtho's max_zoom: the zoom of the textures away from airports. Below the mesh's (16), a
    smaller texture; 17, an 8192 px one."""
    max_zoom_airports: int = Field(18, ge=16, le=19)
    """AutoOrtho's max_zoom_near_airports: the zoom of the mesh's ZL18 textures, around airports;
    19, an 8192 px one."""
    provider: str = "BI"
    """The imagery source: a code of OrthoStudio XP's sources
    (:func:`orthostudio.live.providers.registry`)."""
    folder: str = ""
    """Live's folder, for its pack and its imagery: empty for ``$OSXP_HOME/live``
    (:func:`orthostudio.live.pack.live_root`)."""
    app_home: Literal["ask", "live", "build"] = "ask"
    """What the app opens at its start (orthostudio.live.app_start): the question the first time,
    then Live's window or OrthoStudio XP's, as chosen."""
    simbrief_user: str = Field("", max_length=64)
    """The SimBrief name or pilot ID whose last plan the path ahead follows
    (:mod:`orthostudio.live.flightplan`); empty: nothing is asked of simbrief.com."""
    language: Literal["en", "fr"] = "en"
    """The Live window's language, set by its EN/FR selector: the plugin's texts in X-Plane follow
    it (its Log.txt lines stay English)."""
    overlays: Literal["auto", "off"] = "auto"
    """AutoOrtho's roads, forests and buildings on Live's squares where no other scenery draws
    them (:mod:`orthostudio.live.overlays`); "off": nowhere."""
    tint_8192: bool = False
    """A test: the 8192 px level of every texture built pink, the smaller ones as they are, so that
    a pilot sees where X-Plane shows the 8192 px, which the eye hardly tells from 4096 px."""
    season: Literal["auto", "spring", "summer", "autumn", "winter"] = "auto"
    """The photos' season (``orthostudio.live.look``): "auto" follows the date of the flight
    X-Plane starts, astronomical seasons at its latitude; or always the one named. Chosen for a
    start, never changed in flight (the user, 2026-10-13)."""
    seasons: SeasonLooks = Field(default_factory=SeasonLooks)
    """Each season's colours, as the window's sliders set them."""
    own_tiles: Literal["first", "under"] = "first"
    """Where the pilot built tiles with OrthoStudio XP (or has another ortho scenery): "first",
    X-Plane shows them and Live fills the rest, a step in the relief possible at their edges (the
    app's tiles stand on X-Plane's relief, Live's mesh on viewfinderpanoramas'); "under", Live
    everywhere: its pack listed above them while it runs, they are back once it stops."""
    decal: str = LIVE_DECAL
    """X-Plane 12's decal on the land of Live's squares, one of OrthoStudio XP's
    (:data:`orthostudio.decals.DECALS`): the fine ground detail drawn over the photo near the
    ground, written on the ``DECAL_LIB`` line of every land ``.ter`` file Live places
    (:mod:`orthostudio.live.names`); empty: none. The sea keeps none."""

    @model_validator(mode="before")
    @classmethod
    def _disk_floor(cls, data: Any) -> Any:
        """The disk space raised to its floor for the zooms, rather than refused: a pilot set at
        12 GB who chooses ZL17 gets 20, and the window says so."""
        if not isinstance(data, dict):
            return data
        try:
            floor = disk_floor(
                int(data.get("max_zoom", 16)), int(data.get("max_zoom_airports", 18))
            )
            disk = float(data["disk_gb"]) if "disk_gb" in data else None
        except (TypeError, ValueError):
            return data  # the fields' own checks say what is wrong
        if disk is not None and 0 < disk < floor:  # nought or less: wrong, refused as it was
            data = {**data, "disk_gb": floor}
        return data

    @property
    def textures_gb(self) -> float:
        """The built textures' part of Live's disk space."""
        return self.disk_gb * TEXTURES_SHARE

    @property
    def imagery_gb(self) -> float:
        """The downloaded imagery's part of Live's disk space."""
        return self.disk_gb - self.textures_gb

    @field_validator("folder")
    @classmethod
    def _full_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("a full path is needed, such as /Volumes/Disk/Live")
        return str(path)

    @field_validator("simbrief_user")
    @classmethod
    def _stripped(cls, value: str) -> str:
        return value.strip()

    @field_validator("decal")
    @classmethod
    def _known_decal(cls, name: str) -> str:
        if name and name not in DECALS:
            raise ValueError(f"unknown decal {name!r}")
        return name

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, code: str) -> str:
        from orthostudio.live.providers import registry

        if code not in registry():
            raise ValueError(f"unknown imagery source {code!r}")
        return code


class SettingsFileError(Exception):
    """``live.toml`` does not parse: saving would lose what it holds, the switch first."""


def _read() -> dict:
    """``live.toml`` as a table, empty when there is none. Raises when it does not parse."""
    try:
        text = settings_file().read_text("utf-8")
    except FileNotFoundError:
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise SettingsFileError(f"{settings_file()} does not parse: {e}") from e


def _one_space(table: dict) -> dict:
    """A file from before the one disk space: the two spaces it set, added (each one's default
    where it is missing or wrong), so that Live keeps as much as it was given."""
    if "disk_gb" in table or not any(key in table for key in OLD_BUDGETS):
        return table
    total = 0.0
    for key, default in OLD_BUDGETS.items():
        value = table.get(key, default)
        valid = isinstance(value, int | float) and not isinstance(value, bool) and value > 0
        total += float(value) if valid else default
    return {**table, "disk_gb": total}


def load_settings() -> LiveSettings:
    """The saved settings, the defaults for what is missing or cannot be read."""
    try:
        table = _read().get(TABLE, {})
    except (OSError, UnicodeDecodeError, SettingsFileError):
        return LiveSettings()
    if not isinstance(table, dict):
        return LiveSettings()
    table = _one_space(table)  # a choice gone since, as the start mode, is left out below
    defaults = LiveSettings().model_dump()
    kept = {}
    for key, value in table.items():  # one wrong value costs itself, not its neighbours
        try:
            LiveSettings.model_validate({**defaults, key: value})
        except ValueError:
            continue
        kept[key] = value
    return LiveSettings.model_validate({**defaults, **kept})


def save_settings(settings: LiveSettings) -> None:
    """Write ``settings`` into ``live.toml``, the rest of the file as it was."""
    data = _read()
    data[TABLE] = settings.model_dump()
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, toml_dumps(data))
