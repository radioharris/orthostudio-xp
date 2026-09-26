# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A flight's route: airports and waypoints, and how close it passes to a point.

Airports come from X-Plane's own airport data (``Global Airports/Earth nav data/apt.dat``): the
``1302 datum_lat/datum_lon`` of the airport, else the middle of its first runway.
"""

from __future__ import annotations

import contextlib
import itertools
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .geo import haversine_m

KM_PER_DEG = 111.195
_AIRPORT_ROW = (b"1 ", b"16 ", b"17 ")  # land airport, seaplane base, heliport


def apt_dat(xplane: Path) -> Path:
    return xplane / "Global Scenery" / "Global Airports" / "Earth nav data" / "apt.dat"


def airport_positions(apt: Path, icaos: Iterable[str]) -> dict:
    """{ICAO: (lat, lon)} of the airports found in ``apt`` (one pass over the file)."""
    wanted = {i.upper() for i in icaos}
    found: dict = {}
    current: str | None = None
    datum: dict = {}
    runway: tuple[float, float] | None = None

    def close() -> None:
        if current is None:
            return
        if "lat" in datum and "lon" in datum:
            found[current] = (datum["lat"], datum["lon"])
        elif runway is not None:
            found[current] = runway

    with open(apt, "rb") as f:
        for line in f:
            if line.startswith(_AIRPORT_ROW):
                close()
                parts = line.split()
                current = parts[4].decode("ascii", "replace").upper() if len(parts) > 4 else None
                if current not in wanted:
                    current = None
                datum, runway = {}, None
                if len(found) == len(wanted):
                    break
            elif current is None:
                continue
            elif line.startswith(b"1302 datum_lat ") or line.startswith(b"1302 datum_lon "):
                parts = line.split()
                with contextlib.suppress(IndexError, ValueError):
                    datum["lat" if parts[1] == b"datum_lat" else "lon"] = float(parts[2])
            elif line.startswith(b"100 ") and runway is None:
                parts = line.split()
                with contextlib.suppress(IndexError, ValueError):
                    runway = (
                        (float(parts[9]) + float(parts[18])) / 2,
                        (float(parts[10]) + float(parts[19])) / 2,
                    )
        else:
            close()
    return found


_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


@dataclass
class Route:
    names: list[str]
    points: list[tuple[float, float]]

    @classmethod
    def resolve(cls, xplane: Path, items: Sequence[str]) -> Route:
        """A route from airport codes and ``lat,lon`` waypoints, in order."""
        icaos = [i for i in items if not _LATLON.match(i)]
        positions = airport_positions(apt_dat(xplane), icaos) if icaos else {}
        missing = [i for i in icaos if i.upper() not in positions]
        if missing:
            raise SystemExit(f"not found in X-Plane's airports: {', '.join(missing)}")
        points = []
        for i in items:
            m = _LATLON.match(i)
            points.append((float(m[1]), float(m[2])) if m else positions[i.upper()])
        return cls(list(items), points)

    def length_km(self) -> float:
        return (
            sum(haversine_m(*a, *b) for a, b in zip(self.points, self.points[1:], strict=False))
            / 1000.0
        )

    def distance_km(self, lat: float, lon: float) -> float:
        """Distance from a point to the route: to the nearest point of its legs.

        Each leg is flattened around the point (an equirectangular view): close to the route, where
        the distance matters, the error is well under the rings' margin.
        """
        k = math.cos(math.radians(lat))
        best = math.inf
        pts = [
            ((plon - lon) * k * KM_PER_DEG, (plat - lat) * KM_PER_DEG) for plat, plon in self.points
        ]
        if len(pts) == 1:
            return math.hypot(*pts[0])
        for (ax, ay), (bx, by) in itertools.pairwise(pts):
            dx, dy = bx - ax, by - ay
            seg2 = dx * dx + dy * dy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
            best = min(best, math.hypot(ax + t * dx, ay + t * dy))
        return best

    def tiles_within(self, km: float) -> list[tuple[int, int]]:
        """The 1-degree tiles some part of which lies within ``km`` of the route."""
        lats = [p[0] for p in self.points]
        lons = [p[1] for p in self.points]
        pad_lat = km / KM_PER_DEG + 1
        pad_lon = (
            km
            / (KM_PER_DEG * math.cos(math.radians(max(abs(min(lats)), abs(max(lats))) + pad_lat)))
            + 1
        )
        out = []
        for lat in range(math.floor(min(lats) - pad_lat), math.floor(max(lats) + pad_lat) + 1):
            for lon in range(math.floor(min(lons) - pad_lon), math.floor(max(lons) + pad_lon) + 1):
                # nearest point of the tile to the route, sampled on a 5 x 5 grid over the tile
                d = min(
                    self.distance_km(lat + i / 4, lon + j / 4) for i in range(5) for j in range(5)
                )
                if d <= km:
                    out.append((lat, lon))
        return out
