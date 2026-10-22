# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""How far external views take the camera, at cruise and below: what the camera's reach should
be set on, flight after flight."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orthostudio.live.cameralog import CameraLog, reach_needed


def reading(agl: float, east: float = 0.0, up: float = 0.0, south: float = 0.0) -> dict:
    """The aircraft at the origin of X-Plane's frame, ``agl`` metres up; the camera offset."""
    return {"px": 0.0, "py": 0.0, "pz": 0.0, "vx": east, "vy": up, "vz": south, "agl_m": agl}


def test_the_camera_at_cruise_and_below(tmp_path: Path) -> None:
    log = CameraLog(tmp_path / "_live" / "camera.json")
    t = 0.0
    for _ in range(30):  # a minute in the cockpit at FL350
        log.note(reading(10_700.0, east=0.3, up=0.5), t)
        t += 2.0
    for east in range(0, 6000, 200):  # then an external view going out 6 km, 2 km below
        log.note(reading(10_700.0, east=float(east), up=-2000.0), t)
        t += 2.0
    for _ in range(30):  # on the ground, a view 150 m away: the cockpit's for this
        log.note(reading(0.0, south=150.0), t)
        t += 2.0
    lines = log.summary()
    assert lines[0].startswith("camera at cruise (above 10,000 ft): external views 1 of 2 min")
    assert "5.8 km from the aircraft at most, 8.7 km above the ground at the lowest" in lines[0]
    assert lines[1] == "camera below 10,000 ft: no external view in 1 min"
    log.save()
    log.save()  # the same run: once in the file
    flights = json.loads((tmp_path / "_live" / "camera.json").read_text())["flights"]
    assert len(flights) == 1
    assert flights[0]["high"]["farthest_km"] == 5.8


def test_the_last_flights_are_kept(tmp_path: Path) -> None:
    path = tmp_path / "camera.json"
    for n in range(25):
        log = CameraLog(path)
        log.started = f"flight {n}"
        log.note(reading(500.0, east=1000.0), 0.0)
        log.save()
    flights = json.loads(path.read_text())["flights"]
    assert [f["started"] for f in flights] == [f"flight {n}" for n in range(5, 25)]


def test_the_reach_a_camera_needs() -> None:
    """Far and low at once is what the reach is for: near the ground, the 4096 px ring (13.6 km at
    ZL16) reaches 13.6 km past the camera, 1.5 km past the aircraft's 15.1; high up, nothing."""
    assert reach_needed(15.0, 0.0) == pytest.approx(13.504)  # 15 + 13.6 - 15.096
    # 8192 px files: X-Plane reads their 4096 px at the 2048 px ring, 15 + 23.7 - 26.307
    assert reach_needed(15.0, 0.0, first_ring=1) == pytest.approx(12.393)
    assert reach_needed(15.0, 45.0) == 0.0  # far up: only the small levels, built already
    assert reach_needed(1.0, 0.1) == 0.0  # within the margin of its rings (1.5 km): its own rings
    assert reach_needed(15.0, 0.0, scale=2.0) == pytest.approx(12.008)  # 15 + 27.2 - 30.192


def test_each_run_keeps_the_reach_it_needed(tmp_path: Path) -> None:
    log = CameraLog(tmp_path / "camera.json", first_ring=1)
    t = 0.0
    for south in range(0, 13_001, 500):  # a minute zooming out 13 km south, 100 m up
        log.note(reading(300.0, south=float(south), up=-200.0), t)
        t += 2.0
    assert "a reach of 10.4 km needed" in log.summary()[0]  # 13 + 23.7 - 26.307, from 100 m up
    log.save()
    flights = json.loads((tmp_path / "camera.json").read_text())["flights"]
    assert flights[0]["low"]["reach_km"] == pytest.approx(10.393, abs=0.01)
