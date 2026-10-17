# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A flight plan file turned into the line the Plan's squares are chosen along (``flight-plan.md``).

Two formats, chosen by what the file holds rather than by its name:

* Little Navmap's own, ``.lnmpln``: XML, one ``Waypoint`` per point of the route, each with its
  ``Ident``, ``Name`` and ``Pos Lat Lon`` (a user saves his plans there, idea 11, 2026-10-09);
* X-Plane's ``.fms``, which Little Navmap and most flight planners export for X-Plane: text, one
  line per point, ``type ident [via] altitude lat lon`` (versions 3 and 1100).

The line comes back in the shape SimBrief's does (``api.simbrief.route_of``): ``{from, to,
points: [{ident, name, lat, lon}]}``, so the squares, the drawn path and the kept route are the
same for both. Nothing here touches the network; the file is read once and not kept.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from orthostudio.errors import OsxpError

__all__ = ["MAX_PLAN_BYTES", "MAX_PLAN_POINTS", "PLAN_SUFFIXES", "read_plan_file", "route_of_text"]

PLAN_SUFFIXES = (".lnmpln", ".fms")
"""What the file dialog offers; the content decides how a file is read."""

MAX_PLAN_BYTES = 5_000_000
"""A plan of a few hundred points is a few tens of kilobytes; anything bigger is not a plan."""

MAX_PLAN_POINTS = 400
"""As for SimBrief's navigation log (``api.simbrief.MAX_POINTS``)."""

FMS_POINT_TYPES = frozenset({"1", "2", "3", "11", "13", "28"})
"""X-Plane's FMS entry types: airport, NDB, VOR, fix, and a lat/lon point (13 in version 3, 28
in 1100)."""


def _invalid(path: Path | str, reason: str) -> OsxpError:
    return OsxpError("CFG_PLAN_FILE_INVALID", context={"file": Path(path).name, "reason": reason})


def _point(
    ident: str, name: str, lat: str | float | None, lon: str | float | None
) -> dict[str, Any] | None:
    try:
        y, x = float(lat), float(lon)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= y <= 90.0 and -180.0 <= x <= 180.0):
        return None
    return {
        "ident": (ident.strip().upper()[:8] or "?"),
        "name": name.strip()[:60],
        "lat": y,
        "lon": x,
    }


def _line(points: list[dict[str, Any]], first: str = "", last: str = "") -> dict[str, Any]:
    """The route with no point twice in a row, its two ends named."""
    line: list[dict[str, Any]] = []
    for point in points[:MAX_PLAN_POINTS]:
        if line and (point["lat"], point["lon"]) == (line[-1]["lat"], line[-1]["lon"]):
            continue
        line.append(point)
    return {
        "from": (first.strip().upper()[:8] or (line[0]["ident"] if line else "")),
        "to": (last.strip().upper()[:8] or (line[-1]["ident"] if line else "")),
        "points": line,
    }


def _lnmpln(text: str) -> dict[str, Any] | None:
    """Little Navmap's XML; ``None`` when the text is not that format."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    if root.tag != "LittleNavmap":
        return None
    points = []
    for waypoint in root.iter("Waypoint"):
        pos = waypoint.find("Pos")
        if pos is None:
            continue
        point = _point(
            waypoint.findtext("Ident") or "",
            waypoint.findtext("Name") or "",
            pos.get("Lat"),
            pos.get("Lon"),
        )
        if point is not None:
            points.append(point)
    return _line(points)


def _fms(text: str) -> dict[str, Any] | None:
    """X-Plane's FMS text; ``None`` when the text is not that format."""
    lines = [line.split() for line in text.splitlines()]
    words = [w for w in lines if w]
    if len(words) < 2 or words[0][0] not in ("I", "A") or not words[1][0].isdigit():
        return None
    ends = {"ADEP": "", "ADES": ""}
    points = []
    for w in words[2:]:
        if w[0] in ends and len(w) >= 2:
            ends[w[0]] = w[1]
        elif w[0] in FMS_POINT_TYPES and len(w) >= 5:
            point = _point(w[1], "", w[-2], w[-1])
            if point is not None:
                points.append(point)
    return _line(points, ends["ADEP"], ends["ADES"])


def route_of_text(text: str) -> dict[str, Any] | None:
    """The route of a plan's text, in whichever of the two formats it is; ``None`` for neither."""
    stripped = text.lstrip("﻿ \t\r\n")
    return _lnmpln(stripped) if stripped.startswith("<") else _fms(stripped)


def read_plan_file(path: Path | str) -> dict[str, Any]:
    """The route of a Little Navmap (``.lnmpln``) or X-Plane (``.fms``) flight plan file.

    ``CFG_PLAN_FILE_INVALID`` when it cannot be read, is in neither format, or holds fewer than two
    points with a position.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
        if size > MAX_PLAN_BYTES:
            raise _invalid(path, f"{size // 1_000_000} MB, more than a flight plan holds")
        data = path.read_bytes()
    except OSError as exc:
        raise _invalid(path, exc.strerror or type(exc).__name__) from exc
    text = data.decode("utf-8", errors="replace")
    route = route_of_text(text)
    if route is None:
        raise _invalid(path, "neither a Little Navmap plan nor an X-Plane FMS plan")
    if len(route["points"]) < 2:
        raise _invalid(path, "fewer than two points with a position")
    return route
