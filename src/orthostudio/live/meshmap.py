# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Which of OrthoStudio XP's mesh regions has each one-degree square (``meshmap.json``): the map
shades the regions installed with it, and Live says which region to install where none installed
has the square (:meth:`orthostudio.live.streamer.Streamer.notice_at`). Six regions, the continents:
every square of the world but Antarctica's that has a relief to be made from, 19,295."""

from __future__ import annotations

import base64
import json
import math
import zlib
from functools import cache
from pathlib import Path

MAP_FILE = Path(__file__).with_name("meshmap.json")
REGION_NAMES = {  # as the plugin says them in X-Plane (the window says them in its own words)
    "eur": "Europe",
    "na": "North America",
    "sa": "South America",
    "afr": "Africa",
    "asi": "Asia",
    "aus_pac": "Australia and the Pacific",
}
REGION_NAMES_FR = {
    "eur": "Europe",
    "na": "Amérique du Nord",
    "sa": "Amérique du Sud",
    "afr": "Afrique",
    "asi": "Asie",
    "aus_pac": "Australie et Pacifique",
}


@cache
def _map() -> tuple[tuple[str, ...], bytes]:
    doc = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    return tuple(doc["regions"]), zlib.decompress(base64.b64decode(doc["map"]))


def region_of(lat: float, lon: float) -> str | None:
    """The region whose mesh has the square of (lat, lon); None where none has (the open sea,
    Antarctica)."""
    regions, cells = _map()
    south, west = math.floor(lat), math.floor(lon)
    if not (-90 <= south < 90 and -180 <= west < 180):
        return None
    index = cells[(south + 90) * 360 + (west + 180)]
    return regions[index - 1] if index else None


def squares_of(installed: set[str]) -> dict[str, list[list[int]]]:
    """The one-degree squares of each region in ``installed``, for the map to shade:
    ``{"eur": [[lat, lon], ...], ...}``."""
    names, cells = _map()
    wanted = {i + 1: name for i, name in enumerate(names) if name in installed}
    out: dict[str, list[list[int]]] = {name: [] for name in wanted.values()}
    for cell, index in enumerate(cells):
        name = wanted.get(index)
        if name is not None:
            out[name].append([cell // 360 - 90, cell % 360 - 180])
    return out
