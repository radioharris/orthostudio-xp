# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The path ahead along the pilot's last SimBrief plan, while the aircraft flies it: turns and
approaches included, where five minutes straight on miss them."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from live_helpers import FakeDownloader, make_source
from orthostudio.live import flightplan
from orthostudio.live.cache import ChunkCache
from orthostudio.live.flightplan import ROUTE_KM, FlightPlan, last_plan, plan_of
from orthostudio.live.geo import haversine_m, parse_texture_name
from orthostudio.live.region import Zooms, setup_region
from orthostudio.live.route import KM_PER_DEG
from orthostudio.live.settings import LiveSettings
from orthostudio.live.streamer import Fix, Streamer

ROW = tuple(f"23296_{33152 + 16 * k}_BI16" for k in range(12))  # eastwards, 6.9 km apart


def center(k: int) -> tuple[float, float]:
    return parse_texture_name(f"{ROW[k]}.dds").center


def north_then_east() -> FlightPlan:
    """5 km north of the row's first texture, then east along the row, 5 km north of it."""
    lat0, lon0 = center(0)
    north = lat0 + 5.0 / KM_PER_DEG
    return FlightPlan("LFXX-LFYY", [(lat0, lon0), (north, lon0), (north, center(11)[1])])


def test_the_route_ahead_of_the_aircraft() -> None:
    plan = north_then_east()
    lat0, lon0 = center(0)
    along = plan.ahead(lat0, lon0, 30.0, track_deg=0.0)  # on the first leg, going its way
    assert along is not None
    assert along[1] == plan.points[1]  # the turn...
    end = along[-1]
    assert haversine_m(*plan.points[1], *end) / 1000 == pytest.approx(25.0, abs=0.5)  # ... and on
    assert plan.ahead(lat0 - 2 * ROUTE_KM / KM_PER_DEG, lon0, 30.0) is None  # 30 km south
    assert plan.ahead(lat0, lon0, 30.0, track_deg=180.0) is None  # flying the other way
    assert plan.ahead(lat0, lon0, 30.0, track_deg=None) is not None  # taxiing: any way
    assert plan.ahead(lat0, lon0, 500.0)[-1] == plan.points[-1]  # never past the arrival


def test_a_simbrief_briefing() -> None:
    doc = {
        "origin": {"icao_code": "LFLL", "pos_lat": "45.7256", "pos_long": "5.0811"},
        "destination": {"icao_code": "LSGG", "pos_lat": "46.2381", "pos_long": "6.1089"},
        "navlog": {"fix": [{"ident": "LSE", "pos_lat": "45.9", "pos_long": "5.5"}]},
    }
    plan = plan_of(doc)
    assert plan is not None and plan.name == "LFLL-LSGG"
    assert plan.points == [(45.7256, 5.0811), (45.9, 5.5), (46.2381, 6.1089)]
    assert plan_of({}) is None


def test_asking_simbrief(monkeypatch: pytest.MonkeyPatch) -> None:
    from orthostudio.api import simbrief

    asked = []

    def fetch(url: str):
        asked.append(url)
        if "nobody" in url:
            return None, "unknown user"
        doc = {
            "origin": {"icao_code": "LFLL", "pos_lat": 45.7, "pos_long": 5.1},
            "destination": {"icao_code": "LSGG", "pos_lat": 46.2, "pos_long": 6.1},
        }
        return json.dumps(doc).encode(), ""

    monkeypatch.setattr(simbrief, "_fetch", fetch)
    plan, why = last_plan(" radio harris ")
    assert plan is not None and plan.name == "LFLL-LSGG" and why == ""
    assert asked[0].endswith("username=radio%20harris&json=1")  # the name alone, as OSXP sends it
    assert last_plan("nobody") == (None, "unknown user")


def test_the_route_after_a_turn_is_prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heading north, a straight line misses the row of textures east of the turn: only those
    around the aircraft get their 8192 px. The plan reaches the row, and every texture along it
    gets its 8192 px before X-Plane reads it."""
    pack = tmp_path / "pack"
    setup_region(make_source(tmp_path, textures=ROW), pack, log=lambda _: None, zooms=Zooms(17, 18))
    s = Streamer(
        pack,
        ChunkCache(tmp_path / "c.sqlite"),
        FakeDownloader,
        finest_quality=0,
        max_zoom=17,
        reach_km=0.0,
        log=lambda _: None,
        simbrief_user="radioharris",
    )
    fix = Fix(*center(0), gs_ms=200.0, trk_deg=0.0)

    def sharp(jobs: dict) -> list[int]:
        return [k for k, name in enumerate(ROW) if jobs.get(f"{name}.dds", (0, 0))[1] == -1]

    assert sharp(s.plan(fix, x_plane_there=False)) == [0, 1, 2]  # north: within 15 km only
    monkeypatch.setattr(flightplan, "last_plan", lambda user: (north_then_east(), ""))
    monkeypatch.setattr("orthostudio.live.streamer.last_plan", lambda user: (north_then_east(), ""))
    s.refresh_flight_plan()
    end = time.time() + 5
    while s.flight_plan is None and time.time() < end:
        time.sleep(0.02)
    jobs = s.plan(fix, x_plane_there=False)
    assert s.on_route
    assert sharp(jobs) == list(range(12))  # along the route east, 5 km from it
    plan = s.snapshot()["flight_plan"]
    assert (plan["name"], plan["following"]) == ("LFXX-LFYY", True)
    first = north_then_east().points[0]
    assert plan["points"][0] == [
        round(first[0], 5),
        round(first[1], 5),
    ]  # the window's map draws it
    s.plan(Fix(center(0)[0] - 1.0, center(0)[1], gs_ms=200.0, trk_deg=0.0), x_plane_there=False)
    assert not s.on_route  # 111 km south: off the route, straight on again
    s.cache.close()


def test_the_name_is_the_pilots_to_give() -> None:
    assert LiveSettings().simbrief_user == ""  # nothing is asked of SimBrief unless given
    assert LiveSettings(simbrief_user="  radioharris ").simbrief_user == "radioharris"
    with pytest.raises(ValueError):
        LiveSettings(simbrief_user="x" * 65)
