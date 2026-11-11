# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Where external views take the camera, flight after flight: what the camera's reach should be.

Every ring is widened by the camera's reach, 20 km (25 km until 2026-09-28), so that an external
view zoomed far out never finds a texture unbuilt. At cruise, with 25 km, that was three quarters
of what Live downloaded (about 14 GB an hour at FL350 and 450 kt, 2026-09-27), and a pilot who
flies external views at cruise could not say how far or how low the camera goes. So Live notes
it, as the probe flights did: in the views where the camera leaves the aircraft, how far from it
and how low above the ground it goes, at cruise (the aircraft above HIGH_M above the ground) and
below. It says so in its log when it stops, and keeps the last flights in ``_live/camera.json``,
for the reach to be set on them.
"""

from __future__ import annotations

import contextlib
import json
import math
import time
from pathlib import Path

from .filesystem import replace as replace_file
from .rings import DEFAULT_MARGIN, LOAD_LEVEL, RINGS_ZL16_KM

HIGH_M = 3000.0  # the aircraft this high above the ground: cruise, or near it
AWAY_M = 200.0  # the camera this far from the aircraft: an external view, not the cockpit
KEEP = 20  # flights kept in the file


def reach_needed(
    along_km: float, height_km: float, scale: float = 1.0, first_ring: int = 0
) -> float:
    """The camera's reach a camera ``along_km`` from the aircraft and ``height_km`` above the
    ground needs: how much farther from the aircraft than its own rings X-Plane reads each level
    there, on ZL16 terrain, in three dimensions, ``scale`` its image's (``rings.screen_scale``).
    ``first_ring`` 1: the terrain's files are 8192 px, whose 4096 px level X-Plane reads at the
    2048 px ring; their 8192 px level has no reach (``orthostudio.live.streamer``)."""
    need = 0.0
    for index in range(first_ring, LOAD_LEVEL):
        ring = RINGS_ZL16_KM[index] * scale
        if height_km < ring:
            reads = along_km + math.sqrt(ring * ring - height_km * height_km)
            need = max(need, reads - ring * DEFAULT_MARGIN)
    return need


class CameraLog:
    """What one run of Live saw of the camera."""

    def __init__(self, path: Path, first_ring: int = 0) -> None:
        self.path = path
        self.first_ring = first_ring  # reach_needed's
        self.started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.phases: dict[str, dict] = {}
        self.last: float | None = None

    def note(self, values: dict, now: float, scale: float = 1.0) -> None:
        """One reading of X-Plane's datarefs (streamer.DATAREFS): the aircraft's and the camera's
        positions in its OpenGL frame (metres), the aircraft's height above the ground."""
        if not all(k in values for k in ("px", "py", "pz", "vx", "vy", "vz")):
            return
        dt = 0.0 if self.last is None else min(now - self.last, 10.0)
        self.last = now
        agl = values.get("agl_m", 0.0)
        phase = self.phases.setdefault(
            "high" if agl >= HIGH_M else "low",
            {"minutes": 0.0, "away_minutes": 0.0, "farthest_km": 0.0, "lowest_km": None},
        )
        phase["minutes"] += dt / 60.0
        east, up, south = (
            (values["vx"] - values["px"]),
            (values["vy"] - values["py"]),
            (values["vz"] - values["pz"]),
        )
        if math.sqrt(east * east + up * up + south * south) < AWAY_M:
            return  # the cockpit, or a view hugging the aircraft
        phase["away_minutes"] += dt / 60.0
        along = math.hypot(east, south) / 1000.0
        phase["farthest_km"] = max(phase["farthest_km"], along)
        height = max(0.0, agl + up) / 1000.0
        if phase["lowest_km"] is None or height < phase["lowest_km"]:
            phase["lowest_km"] = height
        need = reach_needed(along, height, scale, self.first_ring)  # far and low at once
        phase["reach_km"] = max(phase.get("reach_km", 0.0), need)

    def summary(self) -> list[str]:
        """What the log says of it."""
        out = []
        for key, label in (("high", "at cruise (above 10,000 ft)"), ("low", "below 10,000 ft")):
            phase = self.phases.get(key)
            if phase is None or phase["minutes"] < 0.5:
                continue
            if not phase["away_minutes"]:
                out.append(f"camera {label}: no external view in {phase['minutes']:.0f} min")
                continue
            out.append(
                f"camera {label}: external views {phase['away_minutes']:.0f} of "
                f"{phase['minutes']:.0f} min, {phase['farthest_km']:.1f} km from the aircraft "
                f"at most, {phase['lowest_km']:.1f} km above the ground at the lowest, "
                f"a reach of {phase.get('reach_km', 0.0):.1f} km needed"
            )
        return out

    def save(self) -> None:
        """This run's figures with the last flights', in the file."""
        if not self.phases:
            return
        try:
            flights = json.loads(self.path.read_text()).get("flights", [])
        except (OSError, ValueError, AttributeError):
            flights = []
        flights = [f for f in flights if f.get("started") != self.started]
        flights.append({"started": self.started, **self.phases})
        tmp = self.path.with_suffix(".tmp")
        with contextlib.suppress(OSError):  # held by another program: saved whole next time
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"flights": flights[-KEEP:]}, indent=1))
            replace_file(tmp, self.path)
