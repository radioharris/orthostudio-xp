# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A test flight's scenario: the file the test pilot flies (``native/xplane_test_pilot``), read
here as the plugin reads it, for the runner (where X-Plane starts, how long the flight lasts) and
the report (the path flown). The statements are listed in ``test_pilot.c``'s header."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

FT = 0.3048
KT = 0.514444
M_DEG = 111195.0  # metres in a degree of latitude, the plugin's
STEP = 0.5  # the path's samples, seconds: the plugin's
DEFAULT_AIRCRAFT = "Aircraft/Laminar Research/Boeing 737-800/b738.acf"
SCENARIOS = Path(__file__).resolve().parent / "scenarios"


class ScenarioError(ValueError):
    """A scenario the test pilot would not fly; the message says why."""


@dataclass(frozen=True)
class Start:
    lat: float
    lon: float
    feet: float
    heading: float
    knots: float


@dataclass(frozen=True)
class Leg:
    seconds: float
    turn: float  # degrees a second, to the right
    climb: float  # feet a minute


@dataclass
class Scenario:
    name: str
    text: str  # the file, as written
    start: Start
    legs: list[Leg]
    aircraft: str = DEFAULT_AIRCRAFT
    screenshots: list[float] = field(default_factory=list)
    title: str = ""  # what it is for, in a few words, for the window (the plugin skips it)

    @property
    def duration(self) -> float:
        """The path's seconds, as the plugin samples it."""
        return sum(round(leg.seconds / STEP) for leg in self.legs) * STEP

    def for_the_pilot(self) -> str:
        """The file sent to the plugin: its own ``end`` lines out, X-Plane left up after it (the
        runner says when X-Plane quits, once the series is flown)."""
        lines = [ln for ln in self.text.splitlines() if _words(ln)[:1] != ["end"]]
        return "\n".join([*lines, "end stay", ""])

    def path(self) -> list[tuple[float, float, float, float]]:
        """(lat, lon, metres, heading) every STEP seconds from the start, as the plugin makes it."""
        s = self.start
        lat, lon, alt, hdg, v = s.lat, s.lon, s.feet * FT, s.heading, s.knots * KT
        points = [(lat, lon, alt, hdg)]
        for leg in self.legs:
            for _ in range(round(leg.seconds / STEP)):
                hdg = math.fmod(hdg + leg.turn * STEP + 360.0, 360.0)
                h = math.radians(hdg)
                lat += v * STEP * math.cos(h) / M_DEG
                lon += v * STEP * math.sin(h) / (M_DEG * math.cos(math.radians(lat)))
                alt += leg.climb * FT / 60.0 * STEP
                points.append((lat, lon, alt, hdg))
        return points

    def end(self) -> tuple[float, float, float, float]:
        return self.path()[-1]


def _words(line: str) -> list[str]:
    return line.split("#", 1)[0].split()


def parse(text: str, name: str = "scenario") -> Scenario:
    start = None
    legs: list[Leg] = []
    aircraft = DEFAULT_AIRCRAFT
    shots: list[float] = []
    title = ""
    for number, line in enumerate(text.splitlines(), 1):
        words = _words(line)
        if not words:
            continue
        try:
            if words[0] == "start":
                start = Start(*(float(w) for w in words[1:6]))
            elif words[0] == "leg":
                legs.append(Leg(*(float(w) for w in words[1:4])))
            elif words[0] == "aircraft":
                aircraft = line.split("#", 1)[0].strip()[len("aircraft") :].strip()
            elif words[0] == "screenshot":
                shots.append(float(words[1]))
            elif words[0] == "title":
                title = line.split("#", 1)[0].strip()[len("title") :].strip()
        except (TypeError, ValueError, IndexError) as e:
            raise ScenarioError(f"{name}, line {number}: {line.strip()!r}: {e}") from e
    if start is None:
        raise ScenarioError(f"{name}: no start line")
    if not legs:
        raise ScenarioError(f"{name}: no leg")
    return Scenario(name, text, start, legs, aircraft, shots, title)


def load(ref: str | Path) -> Scenario:
    """A scenario by its name in ``scenarios/`` (``scandinavia-columns``) or by its file."""
    path = Path(ref)
    if not path.suffix and not path.exists():
        path = SCENARIOS / f"{ref}.txt"
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in SCENARIOS.glob("*.txt")))
        raise ScenarioError(f"no scenario {ref!r} (the scenarios: {known})")
    return parse(path.read_text(encoding="utf-8"), path.stem)
