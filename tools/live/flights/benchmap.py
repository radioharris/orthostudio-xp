# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's own map in the test flights' window (``bench.py``): ``livemap.js`` as Live's window draws
it, fed here from a test flight's Live rather than from a streamer of the window's own.

What Live built is its record, ``_live/ortho.json`` in its pack, which the streamer saves every
30 s (``orthostudio.live.mapdata`` draws from it): the cold home's pack
(``live-home.noindex/live/pack`` in the series' folder, ``live-home`` before 2026-10-01), the
user's own pack for a run with the user's folder, and once a cold run is cleaned, the copy the
runner kept (``live-built.json``). The aircraft is the test pilot's last
frame, its heading from the frames a second before; the route is the scenario's path while it
flies, the path flown afterwards.
"""

from __future__ import annotations

import contextlib
import csv
import json
import math
import time
from pathlib import Path
from typing import Any

import scenario as scenarios

RECORD = "live-built.json"
ROUTE_POINTS = 400


def pack_record(pack: Path) -> Path:
    return pack / "_live" / "ortho.json"


def record_file(series: Path, flying: bool = False) -> Path | None:
    """Where the record of the series' Live is; None when Live did not fly it. While it flies
    with the user's own folder, that folder's (the copy kept is a flight behind)."""
    for home in ("live-home.noindex", "live-home"):  # the cold home, named so from 2026-10-01
        cold = pack_record(series / home / "live" / "pack")
        if cold.is_file():
            return cold
    kept = series / RECORD
    if not (series / "live.txt").is_file():
        return kept if kept.is_file() else None
    if kept.is_file() and not flying:
        return kept
    from orthostudio.live.pack import default_pack_dir

    own = pack_record(default_pack_dir())  # Live with the user's own folder
    return own if own.is_file() else (kept if kept.is_file() else None)


class Records:
    """Records, each read again only once its file has changed: ``(version, record, rows)``."""

    def __init__(self) -> None:
        self._cache: dict[Path, tuple[float, dict[str, Any], list]] = {}

    def get(self, path: Path) -> tuple[int, dict[str, Any], list]:
        from orthostudio.live.mapdata import rows_of

        mtime = path.stat().st_mtime
        hit = self._cache.get(path)
        if hit is None or hit[0] != mtime:
            data = json.loads(path.read_text(encoding="utf-8"))
            hit = (mtime, data, rows_of(data.get("textures", {})))
            self._cache[path] = hit
        return int(mtime * 1000), hit[1], hit[2]


def bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Degrees from north, from ``a`` to ``b`` (lat, lon)."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _positions(lines: list[str]) -> list[tuple[float, float]]:
    out = []
    for line in lines:
        parts = line.split(",")
        if len(parts) > 4:
            with contextlib.suppress(ValueError):
                out.append((float(parts[3]), float(parts[4])))
    return out


def last_position(frames: Path) -> dict[str, float] | None:
    """The aircraft at the test pilot's last frame, and its heading from the frames a second
    before (60 a second)."""
    try:
        size = frames.stat().st_size
        with frames.open("rb") as f:
            f.seek(max(0, size - 8000))
            lines = f.read().decode(errors="replace").splitlines()[1:]
    except OSError:
        return None
    points = _positions(lines)
    if not points:
        return None
    here = points[-1]
    before = points[max(0, len(points) - 60)]
    hdg = bearing(before, here) if before != here else 0.0
    return {"lat": here[0], "lon": here[1], "hdg": round(hdg, 1)}


def thinned(points: list[tuple[float, float]], most: int = ROUTE_POINTS) -> list[list[float]]:
    """At most ``most`` points, the ends kept, as the map draws a route."""
    if len(points) > most:
        step = (len(points) - 1) / (most - 1)
        points = [points[round(i * step)] for i in range(most)]
    return [[round(a, 5), round(b, 5)] for a, b in points]


def flown(series: Path) -> list[list[float]]:
    """The path flown in a series: its flights' frames, one after the other."""
    points: list[tuple[float, float]] = []
    for folder in sorted(p for p in series.iterdir() if p.is_dir() and p.name[:1].isdigit()):
        frames = folder / "frames.csv"
        if not frames.is_file():
            continue
        with frames.open(newline="") as f:
            for i, row in enumerate(csv.DictReader(f)):
                if i % 30 == 0:  # every half second: plenty for a line
                    with contextlib.suppress(KeyError, TypeError, ValueError):
                        points.append((float(row["lat"]), float(row["lon"])))
    return thinned(points)


def planned(name: str) -> list[list[float]]:
    """A scenario's path, as it will be flown."""
    try:
        path = scenarios.load(name).path()
    except scenarios.ScenarioError:
        return []
    return thinned([(lat, lon) for lat, lon, _, _ in path])


def status(aircraft: dict[str, float] | None, streaming: bool) -> dict[str, Any]:
    """What ``livemap.js`` reads of Live's status: the sources, the one shown, the aircraft."""
    from orthostudio.live.providers import listing
    from orthostudio.live.settings import load_settings

    stream = {"data": "fresh", "aircraft": aircraft, "camera_km": 0} if aircraft else None
    return {
        "providers": listing(),
        "settings": {"provider": load_settings().provider},
        "state": "streaming" if streaming and stream else "idle",
        "stream": stream,
    }


def built(
    records: Records,
    path: Path,
    west: float,
    south: float,
    east: float,
    north: float,
    known: int,
    covered: set[tuple[int, int]],
) -> dict[str, Any]:
    """The textures built in the map's view, as Live's window answers (``map/built``)."""
    from orthostudio.live.mapdata import built_in

    version, _, rows = records.get(path)
    if known == version:
        return {"version": version, "same": True}
    out = built_in(path.parent, west, south, east, north, rows=rows, covered=covered)
    return {**out, "version": version}


def textures_at(records: Records, path: Path, lat: float, lon: float) -> list[dict[str, Any]]:
    """The textures under a point of the map, with their facts (its bar over the map)."""
    from orthostudio.live import mapdata

    _, data, _ = records.get(path)
    return mapdata.textures_at(
        lat,
        lon,
        data.get("textures", {}),
        set(data.get("patchy", [])),
        data.get("seen", {}),
        time.time(),
    )


def own_tiles(xp: Path | None) -> list[dict[str, Any]]:
    """The squares X-Plane takes from the user's own tiles, framed on the map as Live does."""
    if xp is None:
        return []
    from orthostudio.live.control import made_by
    from orthostudio.live.control import own_tiles as own

    return [
        {"lat": lat, "lon": lon, "pack": pack, "kind": made_by(pack)}
        for (lat, lon), pack in sorted(own(xp).items())
    ]


def regions(xp: Path | None) -> dict[str, Any]:
    """The squares of the regions installed, Live's and AutoOrtho's, as Live's map shades them."""
    from orthostudio.live import aoregions
    from orthostudio.live.mapdata import regions_installed
    from orthostudio.live.pack import live_root

    installed = set(aoregions.installed(live_root()))
    if xp is not None:
        from orthostudio.live.scenery import autoortho_regions

        installed |= {p.name.removeprefix("z_ao_") for p in autoortho_regions(xp)}
    return regions_installed(installed)
