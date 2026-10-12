# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The pilot's flight plan, from SimBrief, for the path ahead.

Live looks ahead along the aircraft's track, two minutes straight on, which a turn, a departure
procedure or an approach leaves: after a tight turn, a few textures near the aircraft could not
grow to 8192 px in time. With the pilot's last SimBrief plan, the path ahead follows the route
instead, ten minutes of it, while the aircraft is on it (within ROUTE_KM, going its way).

SimBrief serves a pilot's last plan without a key or a password, from their name or pilot ID
(``orthostudio.api.simbrief``, OrthoStudio XP's own reading of it). The name is Live's setting,
empty unless the pilot gives it, and only that name goes to simbrief.com: when Live starts, when a
flight starts, at most once a minute, and while the Live window is open, to offer the departure of
the last plan and say when a new one came (``LiveControl.departure_suggestion``).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .route import KM_PER_DEG

ROUTE_KM = 15.0  # the aircraft this near the route follows it
ROUTE_LOOKAHEAD_MIN = 10.0  # the route ahead the planner looks along
WRONG_WAY_DEG = 100.0  # an aircraft flying this far from the route's way is not following it
MOVING_MS = 25.0  # slower, taxiing or turning on the ground: its track says nothing of the route

LatLon = tuple[float, float]


def _xy(p: LatLon, lat0: float, lon0: float) -> tuple[float, float]:
    """``p`` in km east and north of (lat0, lon0), flattened there (equirectangular)."""
    k = math.cos(math.radians(lat0))
    return (p[1] - lon0) * k * KM_PER_DEG, (p[0] - lat0) * KM_PER_DEG


@dataclass
class FlightPlan:
    """A route: its name (``LFLL-LSGG``) and its points, departure and arrival included; and the
    briefing it comes from (SimBrief's number for it, or when it was made): another plan of the
    same route is another briefing."""

    name: str
    points: list[LatLon]
    briefing: str = ""

    def ahead(
        self, lat: float, lon: float, km: float, track_deg: float | None = None
    ) -> list[LatLon] | None:
        """The route from its point nearest (lat, lon) on, over ``km``; None when (lat, lon) is
        more than ROUTE_KM from it, or, given the aircraft's ``track_deg``, flies against it."""
        if len(self.points) < 2:
            return None
        pts = [_xy(p, lat, lon) for p in self.points]
        best = (math.inf, 0, 0.0)  # distance, leg, how far along it (0 to 1)
        for i in range(len(pts) - 1):
            (ax, ay), (bx, by) = pts[i], pts[i + 1]
            dx, dy = bx - ax, by - ay
            seg2 = dx * dx + dy * dy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
            d = math.hypot(ax + t * dx, ay + t * dy)
            if d < best[0]:
                best = (d, i, t)
        d, leg, t = best
        if d > ROUTE_KM:
            return None
        (ax, ay), (bx, by) = pts[leg], pts[leg + 1]
        if track_deg is not None and (bx, by) != (ax, ay):
            way = math.degrees(math.atan2(bx - ax, by - ay))
            if abs((track_deg - way + 180.0) % 360.0 - 180.0) > WRONG_WAY_DEG:
                return None
        a, b = self.points[leg], self.points[leg + 1]
        here = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        out = [here]
        left = km
        prev, prev_xy = here, (ax + t * (bx - ax), ay + t * (by - ay))
        for p, p_xy in zip(self.points[leg + 1 :], pts[leg + 1 :], strict=True):
            step = math.hypot(p_xy[0] - prev_xy[0], p_xy[1] - prev_xy[1])
            if step >= left:  # the last point, part of the way along this leg
                f = left / step if step else 0.0
                out.append((prev[0] + f * (p[0] - prev[0]), prev[1] + f * (p[1] - prev[1])))
                return out
            out.append(p)
            left -= step
            prev, prev_xy = p, p_xy
        return out


def plan_of(doc: dict) -> FlightPlan | None:
    """A SimBrief briefing (its JSON) as a route, or None when it holds none."""
    from orthostudio.api.simbrief import route_of

    route = route_of(doc)
    points = [(p["lat"], p["lon"]) for p in route["points"]]
    if len(points) < 2:
        return None
    params = doc.get("params") if isinstance(doc.get("params"), dict) else {}
    briefing = str(params.get("request_id") or params.get("time_generated") or "")
    return FlightPlan(f"{route['from']}-{route['to']}", points, briefing)


def last_plan(user: str) -> tuple[FlightPlan | None, str]:
    """The pilot's last SimBrief plan, or None and why (the network, an unknown name...)."""
    try:
        from orthostudio.api.simbrief import _fetch, simbrief_url
    except ImportError as e:  # the engine's server part is not installed
        return None, str(e)
    body, error = _fetch(simbrief_url(user.strip()))
    if body is None:
        return None, error
    try:
        doc = json.loads(body)
    except ValueError:
        return None, "not a SimBrief briefing"
    plan = plan_of(doc) if isinstance(doc, dict) else None
    return plan, "" if plan is not None else "no flight plan in it"
