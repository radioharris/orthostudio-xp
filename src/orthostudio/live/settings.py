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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orthostudio.config.store import toml_dumps
from orthostudio.fsutil import atomic_write_text
from orthostudio.live import settings_file

TABLE = "stream"


class LiveSettings(BaseModel):
    """What a stream is built with, beyond where X-Plane is."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    disk_budget_gb: float = Field(20.0, ge=2.0, le=10_000.0)
    """Disk the built textures may take; beyond it, the farthest are made coarser."""
    cache_budget_gb: float = Field(10.0, ge=1.0, le=10_000.0)
    """Disk the downloaded imagery may take; beyond it, the oldest goes."""
    max_zoom: int = Field(16, ge=14, le=17)
    """AutoOrtho's max_zoom: the zoom of the textures away from airports. Below the mesh's (16), a
    smaller texture; 17, an 8192 px one."""
    max_zoom_airports: int = Field(18, ge=16, le=19)
    """AutoOrtho's max_zoom_near_airports: the zoom of the mesh's ZL18 textures, around airports;
    19, an 8192 px one."""
    start_mode: Literal["full", "around", "normal", "reload"] = "around"
    """What X-Plane waits for at a flight's start where the zoom makes 8192 px textures
    (:data:`orthostudio.live.streamer.START_MODES`)."""
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


def load_settings() -> LiveSettings:
    """The saved settings, the defaults for what is missing or cannot be read."""
    try:
        table = _read().get(TABLE, {})
    except (OSError, UnicodeDecodeError, SettingsFileError):
        return LiveSettings()
    if not isinstance(table, dict):
        return LiveSettings()
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
