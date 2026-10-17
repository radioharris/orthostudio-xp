# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A test flight's scenario made from a route, as a pilot flies it: waypoints, and the height and
ground speed along the way (``docs/specs/live/flights.md``). The test pilot flies legs (a
duration, a turn rate, a climb rate, the speed at their end); writing those by hand for a real
route, with its turns, its climb and its descent, was out of reach, so they are generated here:
the aircraft turns towards each waypoint (2 degrees a second above 300 kt, 3 below), its height
and speed follow the profile by the distance flown, and a leg is written every 15 s of path.

    uv run python tools/live/flights/route.py tools/live/flights/routes/alps.toml

A route file (TOML):

    title = "..."                      # the scenario's title
    zulu = [11, 271]                   # hour UTC, day of the year (0: 1 January)
    waypoints = [[46.24, 6.11], ...]   # lat, lon, flown in order from the first
    heading = 110                      # at the start (default: towards the second waypoint)
    profile = [[0, 6000, 250], [80, 13000, 280], [-40, 3000, 200]]
                                       # km flown, feet, knots, interpolated; a negative km counts
                                       # from the route's end (a descent ends at the airport)
    cameras = ["0 99999 cockpit"]      # the scenario's camera lines, as written; by default
                                       # views(): half in the cockpit, half outside
    screenshots = [120, 600]           # seconds of path (default: every 5 minutes)
    note = "..."                       # the comment at the top
"""

from __future__ import annotations

import itertools
import math
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scenario as sc  # noqa: E402

LEG_S = 15.0
# The cameras of a flight by default, as a pilot looks round (the user, 2026-10-17: « alterner
# cockpit, extérieur plus souvent, des zoom out rapides, on tourne rapidement autour de l'avion,
# 50/50 »): every 2 minutes, 60 s in the cockpit, then 60 s outside, by turns a quick zoom out to
# 8 km behind and back, a turn round the aircraft in 20 s, and the view 20 km to the right.
VIEW_CYCLE_S = 120


def views(total: float) -> list[str]:
    out, t, k = [], 0.0, 0
    while t < total:
        c = t
        out.append(f"{c:g} {c + 60:g} cockpit")
        if k % 3 == 0:  # just behind, back to 8 km in 6 s, there 12 s, back in 6 s, a turn round
            out += [
                f"{c + 60:g} {c + 66:g} chase 150 30 180 8000 2000 180",
                f"{c + 66:g} {c + 78:g} chase 8000 2000 180",
                f"{c + 78:g} {c + 84:g} chase 8000 2000 180 150 30 180",
                f"{c + 84:g} {c + 104:g} chase 150 30 180 150 30 540",
                f"{c + 104:g} {c + 120:g} chase 150 30 180",
            ]
        elif k % 3 == 1:  # round the aircraft twice, close then farther out
            out += [
                f"{c + 60:g} {c + 80:g} chase 120 25 0 120 25 360",
                f"{c + 80:g} {c + 105:g} chase 600 150 0 600 150 360",
                f"{c + 105:g} {c + 120:g} chase 150 30 180",
            ]
        else:  # far out to the right, then back behind
            out += [
                f"{c + 60:g} {c + 66:g} chase 150 30 180 20000 3000 90",
                f"{c + 66:g} {c + 96:g} chase 20000 3000 90",
                f"{c + 96:g} {c + 102:g} chase 20000 3000 90 150 30 180",
                f"{c + 102:g} {c + 120:g} chase 150 30 180",
            ]
        t += VIEW_CYCLE_S
        k += 1
    out.append(f"{t:g} 99999 cockpit")  # after the last cycle, to the end
    return out


FAST_KT = 300.0  # above, turns at 2 degrees a second: an airliner's bank at that speed


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * sc.M_DEG
    dx = (lon2 - lon1) * sc.M_DEG * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy) / 1000.0


def _profile(profile: list[list[float]], km: float) -> tuple[float, float]:
    """(feet, knots) at ``km`` flown, linearly between the profile's points."""
    if km <= profile[0][0]:
        return profile[0][1], profile[0][2]
    for (k0, f0, v0), (k1, f1, v1) in itertools.pairwise(profile):
        if km <= k1:
            t = (km - k0) / (k1 - k0) if k1 > k0 else 1.0
            return f0 + (f1 - f0) * t, v0 + (v1 - v0) * t
    return profile[-1][1], profile[-1][2]


def route_km(pts: list[list[float]]) -> float:
    return sum(_km(*a, *b) for a, b in itertools.pairwise(pts))


def legs_of(route: dict) -> tuple[sc.Start, list[sc.Leg]]:
    pts = route["waypoints"]
    total = route_km(pts)
    prof = sorted(
        ([k if k >= 0 else total + k, f, v] for k, f, v in route["profile"]), key=lambda p: p[0]
    )
    lat, lon = pts[0]
    feet, knots = _profile(prof, 0.0)
    hdg = float(route.get("heading", _bearing(lat, lon, *pts[1])))
    start = sc.Start(lat, lon, feet, hdg, knots)
    flown, target, t = 0.0, 1, 0.0
    legs: list[sc.Leg] = []
    seg_hdg0, seg_feet0 = hdg, feet
    while target < len(pts):
        tl, tn = pts[target]
        dist = _km(lat, lon, tl, tn)
        rate = 2.0 if knots > FAST_KT else 3.0
        # the next waypoint once within a turn's anticipation of it, or at the last: within 1 km
        lead = (knots * sc.KT) / math.radians(rate) / 1000.0 * 0.6
        if dist < (1.0 if target == len(pts) - 1 else max(1.0, lead)):
            target += 1
            continue
        want = _bearing(lat, lon, tl, tn)
        d = (want - hdg + 540.0) % 360.0 - 180.0
        hdg = (hdg + max(-rate, min(rate, d))) % 360.0
        v = knots * sc.KT
        lat += v * math.cos(math.radians(hdg)) / sc.M_DEG
        lon += v * math.sin(math.radians(hdg)) / (sc.M_DEG * math.cos(math.radians(lat)))
        flown += v / 1000.0
        feet, knots = _profile(prof, flown)
        t += 1.0
        if t % LEG_S == 0:
            turn = ((hdg - seg_hdg0 + 540.0) % 360.0 - 180.0) / LEG_S
            climb = (feet - seg_feet0) / LEG_S * 60.0
            legs.append(sc.Leg(LEG_S, round(turn, 4), round(climb, 1), round(knots, 1)))
            seg_hdg0, seg_feet0 = hdg, feet
    return start, _merged(legs)


def _merged(legs: list[sc.Leg]) -> list[sc.Leg]:
    """Legs alike one after the other as one: a straight, level leg at one speed for minutes."""
    out: list[sc.Leg] = []
    for leg in legs:
        prev = out[-1] if out else None
        if prev and prev.turn == leg.turn and prev.climb == leg.climb and prev.knots == leg.knots:
            out[-1] = sc.Leg(prev.seconds + leg.seconds, leg.turn, leg.climb, leg.knots)
        else:
            out.append(leg)
    return out


def scenario_text(route: dict) -> str:
    start, legs = legs_of(route)
    lines = [f"# {line}" for line in route.get("note", "").strip().splitlines()]
    lines.append(f"# generated by tools/live/flights/route.py from routes/{route['_name']}.toml")
    lines.append(f"title {route['title']}")
    where = f"{start.lat:.5f} {start.lon:.5f} {start.feet:.0f}"
    lines.append(f"start {where} {start.heading:.1f} {start.knots:.0f}")
    lines.append("weather clear")
    hour, day = route.get("zulu", [11, 271])
    lines.append(f"zulu {hour} {day}")
    total = sum(g.seconds for g in legs)
    lines += [f"camera {c}" for c in route.get("cameras") or views(total)]
    shots = route.get("screenshots") or list(range(120, int(total), 300))
    lines += [f"leg {g.seconds:g} {g.turn:g} {g.climb:g} {g.knots:g}" for g in legs]
    lines += [f"screenshot {s:g}" for s in shots]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> None:
    for name in argv:
        path = Path(name)
        route = tomllib.loads(path.read_text())
        route["_name"] = path.stem
        out = sc.SCENARIOS / f"{path.stem}.txt"
        out.write_text(scenario_text(route))
        s = sc.parse(out.read_text(), path.stem)
        print(f"{out.name}: {len(s.legs)} legs, {s.duration / 60:.1f} min")


if __name__ == "__main__":
    main(sys.argv[1:])
