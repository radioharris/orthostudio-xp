# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP Live's settings: the ``[stream]`` table of ``live.toml``.

The Live window saves them, and ``python -m orthostudio.live stream`` takes them as its defaults:
a disk budget set in the window must hold on the command line too, or the streamer would make the
textures built beyond it coarser. The file's other keys, ``enabled`` first, are written back as
they were read.
"""

from __future__ import annotations

import os
import threading
import tomllib
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orthostudio.config.store import toml_dumps
from orthostudio.decals import DECALS
from orthostudio.fsutil import atomic_write_text
from orthostudio.install.library import tile_from_name
from orthostudio.live import settings_file
from orthostudio.live.geo import tile_name

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
    other_meshes: list[str] = Field(default_factory=list)
    """Folders holding other tools' meshes in AutoOrtho's format, chosen in the Settings, top
    first: a region copied by another streamer, AutoOrtho's moved elsewhere, a pilot's own. Live
    streams onto them after its own mesh and AutoOrtho's in X-Plane, but on the squares of
    :attr:`other_mesh_front`, and takes their roads, forests and buildings too; between them the
    first in the list that has a square gives it, in front of Live or behind
    (:func:`orthostudio.live.scenery.other_meshes`). Each folder once, as typed; added at the
    bottom, moved up or down, or removed by the page's buttons (:func:`add_other_mesh`,
    :func:`move_other_mesh`, :func:`remove_other_mesh`). A setting, not a look into the other
    tools' own files, which may change under Live (the user, 2026-11-14: « je préfère le réglage
    sans surprise »); the one folder of before, ``other_mesh``, read as a list of one
    (:func:`_one_list`), and written with the list's first folder (:func:`_write`)."""
    other_mesh_front: list[str] = Field(default_factory=list)
    """The squares of those folders' meshes put in front of Live on its map ("+45+006"): there the
    DSF of the first folder that has it wins over Live's own and AutoOrtho's, with Live's photos
    (:func:`orthostudio.live.scenery.default_meshes`); elsewhere they come after them. Put in
    front or back by a click (:func:`flip_front`), or a folder's squares all at once
    (:func:`put_in_front`); never dropped otherwise, a folder removed included: a square no folder
    gives is not taken. Taken at the next start with X-Plane not running, as every change of the
    mesh (``session.prepare``)."""
    mesh: Literal["new", "old"] = "new"
    """Which mesh of Live's own regions a start streams onto, for all of them at once: "new", each
    region installed; "old", each region's kept old mesh where it has one, the new one elsewhere
    (:func:`orthostudio.live.scenery.osxp_sources`). Shown in the window only once a region keeps
    its old mesh (``osxpregions.kept_dir``); taken at the next start with X-Plane not running, as
    every change of the mesh (``session.prepare``)."""
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
    """Where the pilot built tiles with OrthoStudio XP or imported them from Ortho4XP: "first",
    X-Plane shows them and Live fills the rest, a step in the relief possible at their edges (the
    app's tiles stand on X-Plane's relief, Live's mesh on viewfinderpanoramas'); "under", Live
    everywhere: Live's line listed above them, moved once when the setting changes
    (:func:`orthostudio.live.scenery.move_live`), unchecked at Live's stop, when they are back.
    Another photo pack listed above Live's line shows with either."""
    night: bool = True
    """The towns' lights at night on Live's squares (:mod:`orthostudio.live.night`): a night
    texture beside each land texture, made from two hours before sunset in X-Plane to sunrise,
    counted in Live's disk space with its day one; off: none, the land's ``.ter`` files naming
    none. On by default: by day it costs nothing (the user, 2026-11-10)."""
    night_light: Literal["soft", "normal", "strong"] = "normal"
    """How bright those lights are (:data:`orthostudio.live.night.LIGHTS`), the window's one
    question with ``night``: at the next start, each night texture made again as night comes."""
    rivers_fade: bool = False
    """How the photo meets the water of rivers and lakes on Live's squares
    (:mod:`orthostudio.live.waterphoto`): off, evenly see-through, one grey border for all of
    them, as XPME and AutoOrtho draw them; on, faded into the water near their banks as on the
    sea, through each one's mask, with a water photo of its own. The sea and lakes over 200 km²
    keep their masks either way. Off by default (the user, 2026-11-17): soft banks take more
    graphics memory where there is much water, X-Plane +0.7 GB against off at Stockholm on the
    new mesh. Only regions of the new mesh, whose rivers and lakes have
    terrain files of their own, tell the two apart; a change takes effect at the next start with
    X-Plane not running, as the decal's."""
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

    @field_validator("other_meshes")
    @classmethod
    def _folders(cls, folders: list[str]) -> list[str]:
        """Each folder once, the first kept, as typed without the spaces around it; no empty line.
        Told apart as written (:func:`folder_key`), nothing asked of the disk, as at every reading
        of the settings: the same folder through a link, or in another case on macOS, is refused
        when it is added (:func:`add_other_mesh`)."""
        out: list[str] = []
        seen: set[str] = set()
        for folder in (f.strip() for f in folders):
            if folder and (key := folder_key(folder)) not in seen:
                seen.add(key)
                out.append(folder)
        return out

    @field_validator("other_mesh_front")
    @classmethod
    def _square_names(cls, names: list[str]) -> list[str]:
        """Each square once, written as X-Plane names its DSF, south to north then west to east."""
        squares = set()
        for name in names:
            square = tile_from_name(name.strip())
            if square is None:
                raise ValueError(f"not a square's name: {name!r}, such as +45+006")
            squares.add(square)
        return [tile_name(*square) for square in sorted(squares)]

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


def folder_path(text: str) -> Path:
    """A folder of the list of other tools' meshes as a path: ``~`` the home folder; a
    ``~someone`` with no such user as typed (not there)."""
    try:
        return Path(text).expanduser()
    except RuntimeError:
        return Path(text)


def folder_key(text: str) -> str:
    """A folder of that list as written, to compare: ``~`` the home folder, a slash more or a
    ``..`` taken out, the case where the system ignores it in names (Windows). Nothing asked of
    the disk; :func:`_same_folder` asks it, when a folder is added."""
    return os.path.normcase(os.path.normpath(folder_path(text)))


def _same_folder(a: str, b: str) -> bool:
    """Whether two folders of that list are one: written the same (:func:`folder_key`), or, both
    there, the same folder on the disk (one a link to the other, or written in another case on
    macOS, whose disks ignore it)."""
    if folder_key(a) == folder_key(b):
        return True
    try:
        return os.path.samefile(folder_path(a), folder_path(b))
    except (OSError, ValueError):  # one not there (its disk unplugged), a name no disk takes
        return False


class SettingsFileError(Exception):
    """``live.toml`` does not parse: saving would lose what it holds, the switch first."""


def _read() -> dict:
    """``live.toml`` as a table, empty when there is none. Raises :class:`SettingsFileError` when
    it does not parse, the :class:`OSError` when it cannot be read."""
    try:
        text = settings_file().read_text("utf-8")
    except FileNotFoundError:
        return {}
    except UnicodeDecodeError as e:
        raise SettingsFileError(f"{settings_file()} is not UTF-8 text: {e}") from e
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


def _one_list(table: dict) -> dict:
    """A file from before several folders of other tools' meshes, or whose list is not one: its one
    folder (``other_mesh``) the list's only line, so that nobody loses his."""
    old = table.get("other_mesh")
    if isinstance(table.get("other_meshes"), list) or not isinstance(old, str) or not old.strip():
        return table
    return {**table, "other_meshes": [old]}


def _settings_of(table: dict) -> LiveSettings:
    """The settings ``[stream]`` holds, the defaults for what is missing: one wrong value costs
    itself, not its neighbours, and one wrong line of a list itself, not the list; a choice gone
    since, as the start mode, is left out."""
    table = _one_list(_one_space(table))
    defaults = LiveSettings().model_dump()

    def valid(key: str, value: Any) -> bool:
        try:
            LiveSettings.model_validate({**defaults, key: value})
        except ValueError:
            return False
        return True

    kept = {}
    for key, value in table.items():
        if valid(key, value):
            kept[key] = value
        elif isinstance(value, list) and isinstance(defaults.get(key), list):
            kept[key] = [line for line in value if valid(key, [line])]
    return LiveSettings.model_validate({**defaults, **kept})


def load_settings() -> LiveSettings:
    """The saved settings, the defaults for what is missing or cannot be read."""
    try:
        table = _read().get(TABLE, {})
    except (OSError, SettingsFileError):
        return LiveSettings()
    return _settings_of(table) if isinstance(table, dict) else LiveSettings()


def _write(data: dict, settings: LiveSettings) -> None:
    """``settings`` written into ``live.toml`` as ``data`` holds it, the rest of the file as it was.
    With them ``other_mesh``, the one folder of other tools' meshes of 2.4 and before, the list's
    first (none: empty), so that an older Live keeps that folder: it reads ``other_mesh`` alone and
    writes its own settings back without the list at its first save, which wiped both lists; the
    first folder then comes back here as a list of one (:func:`_one_list`)."""
    first = settings.other_meshes[0] if settings.other_meshes else ""
    data[TABLE] = {**settings.model_dump(), "other_mesh": first}
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, toml_dumps(data))


SAVING = threading.Lock()
"""Held by every write of Live's settings (:func:`saved_with`): one after the other, each keeps
what the others saved."""


def saved_with(change: Callable[[LiveSettings], dict[str, Any] | None]) -> LiveSettings:
    """The settings as saved now, ``change`` applied, saved, under :data:`SAVING`: the one way Live
    writes them (its page's Settings, its map's click, the buttons of the folders of other tools'
    meshes, the language, the welcome page's answer, the Mesh back to New), so that two at once
    keep both. ``change`` gives the values that change, or None for none, nothing written. The file
    read as it is, never the defaults in its place: one that cannot be read or does not parse is
    left as it is, the :class:`OSError` or :class:`SettingsFileError` raised. Raises what
    ``change`` raises, or :class:`ValueError` for a value refused, nothing saved."""
    with SAVING:
        data = _read()
        table = data.get(TABLE, {})
        if not isinstance(table, dict):
            raise SettingsFileError(f"{settings_file()}: [{TABLE}] is not a table")
        settings = _settings_of(table)
        changed = change(settings)
        if changed is None:
            return settings
        saved = LiveSettings.model_validate({**settings.model_dump(), **changed})
        _write(data, saved)
    return saved


class AlreadyListedError(ValueError):
    """A folder added to the list of other tools' meshes that the list has already."""


def flip_front(square: str) -> LiveSettings:
    """A square of the folders of other tools' meshes clicked on Live's map: put in front of Live
    where it was behind, back behind where it was in front, as the setting is saved now, so that
    two quick clicks flip it twice whatever the page drew. The settings, as saved;
    :class:`ValueError` for a name not a square's."""
    name = square.strip()

    def change(settings: LiveSettings) -> dict[str, Any]:
        front = settings.other_mesh_front
        return {
            "other_mesh_front": [n for n in front if n != name] if name in front else [*front, name]
        }

    return saved_with(change)


def put_in_front(squares: Collection[str], front: bool) -> LiveSettings:
    """Squares of the folders of other tools' meshes put in front of Live, or back behind it: those
    a folder gives, its « All in front » or « All behind ». The setting read, those squares alone
    changed, saved. The settings, as saved; :class:`ValueError` for a name not a square's."""
    names = {name.strip() for name in squares}

    def change(settings: LiveSettings) -> dict[str, Any]:
        kept = [name for name in settings.other_mesh_front if name not in names]
        return {"other_mesh_front": [*kept, *names] if front else kept}

    return saved_with(change)


def _index(settings: LiveSettings, folder: str) -> int:
    """Where ``folder``, as the page names it, is in the list (:func:`folder_key`);
    :class:`ValueError` where it is not."""
    key = folder_key(folder)
    for i, listed in enumerate(settings.other_meshes):
        if folder_key(listed) == key:
            return i
    raise ValueError(f"{folder} is not in the list of other tools' meshes")


def add_other_mesh(folder: str) -> LiveSettings:
    """A folder of another tool's mesh added at the bottom of the list, as typed or chosen. The
    settings, as saved; :class:`AlreadyListedError` for one the list has already, however written
    (:func:`_same_folder`, the disks asked before the settings' lock, which every save waits for),
    :class:`ValueError` for none."""
    folder = folder.strip()
    if not folder:
        raise ValueError("no folder given")

    already = AlreadyListedError(f"{folder} is in the list already")
    if any(_same_folder(folder, listed) for listed in load_settings().other_meshes):
        raise already
    key = folder_key(folder)

    def change(settings: LiveSettings) -> dict[str, Any]:
        if any(folder_key(listed) == key for listed in settings.other_meshes):  # added meanwhile
            raise already
        return {"other_meshes": [*settings.other_meshes, folder]}

    return saved_with(change)


def move_other_mesh(folder: str, up: bool) -> LiveSettings:
    """A folder of the list moved one line up or down, which changes which folder gives a square
    both have; at an end, left there. The settings, as saved."""

    def change(settings: LiveSettings) -> dict[str, Any]:
        folders = list(settings.other_meshes)
        i = _index(settings, folder)
        j = i - 1 if up else i + 1
        if 0 <= j < len(folders):
            folders[i], folders[j] = folders[j], folders[i]
        return {"other_meshes": folders}

    return saved_with(change)


def remove_other_mesh(folder: str) -> LiveSettings:
    """A folder forgotten, its files left as they are; the squares in front of Live stay as they
    are (one no folder gives is not taken). The settings, as saved."""

    def change(settings: LiveSettings) -> dict[str, Any]:
        folders = list(settings.other_meshes)
        del folders[_index(settings, folder)]
        return {"other_meshes": folders}

    return saved_with(change)
