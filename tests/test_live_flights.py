# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The replayable test flights (tools/live/flights, docs/specs/live/flights.md): the scenarios as
the test pilot reads them, the report made from what a flight leaves, and the runner's handling of
the test pilot's folder. X-Plane itself is never started here."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

# the test flights fly X-Plane on a Mac and on Windows; a few of their parts are a Mac's only:
# AutoOrtho's runs, the watcher (sudo, spindump), process groups stopped with a Ctrl-C (Windows:
# tests/test_live_flights_windows.py)
mac_only = pytest.mark.skipif(sys.platform == "win32", reason="a Mac's part of the test flights")
FLIGHTS = Path(__file__).resolve().parents[1] / "tools" / "live" / "flights"
sys.path.insert(0, str(FLIGHTS))

import fly  # noqa: E402
import report  # noqa: E402
import scenario  # noqa: E402

DSF = (
    "0:09:05.591 I/SCN: DSF load time: 4007 for file Global Scenery/X-Plane 12 Global Scenery/"
    "Earth nav data/{block}/{name}.dsf (0 tris, 0 skipped for 0.0 m^2)"
)


def dsf(lat: int, lon: int, pack: str = "Global Scenery/X-Plane 12 Global Scenery") -> str:
    name = f"{lat:+03d}{lon:+04d}"
    block = f"{lat // 10 * 10:+03d}{lon // 10 * 10:+04d}"
    return DSF.format(block=block, name=name).replace(
        "Global Scenery/X-Plane 12 Global Scenery", pack
    )


def mark(lat: float, lon: float, path_s: float) -> str:
    return f"OrthoStudio XP Test Pilot: scenery load at {lat:.4f} {lon:.4f}, path {path_s:.1f} s"


# ---- scenarios ----------------------------------------------------------------------------------


def test_a_scenario_reads_as_the_test_pilot_reads_it() -> None:
    sc = scenario.parse(
        "# a comment\n"
        "start 60.5 10.4 30000 90 480   # where\n"
        "aircraft Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf\n"
        "weather clear\n"
        "leg 60 0 0\n"
        "leg 30 3 500\n"
        "screenshot 10\n"
        "end quit\n",
        "test",
    )
    assert sc.start == scenario.Start(60.5, 10.4, 30000.0, 90.0, 480.0)
    assert sc.legs == [scenario.Leg(60, 0, 0), scenario.Leg(30, 3, 500)]
    assert sc.aircraft == "Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf"
    assert sc.screenshots == [10.0]
    assert sc.duration == 90.0
    sent = sc.for_the_pilot()
    assert "end quit" not in sent and sent.rstrip().endswith("end stay")  # the runner quits X-Plane
    assert "weather clear" in sent


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("leg 60 0 0\n", "no start"),
        ("start 60 10 30000 90 480\n", "no leg"),
        ("start 60 10 30000 90\nleg 60 0 0\n", "line 1"),
        ("start 60 10 30000 90 480\nleg sixty 0 0\n", "line 2"),
    ],
)
def test_a_scenario_the_test_pilot_would_not_fly_is_refused(text: str, why: str) -> None:
    with pytest.raises(scenario.ScenarioError, match=why):
        scenario.parse(text)


def test_the_path_is_the_test_pilots() -> None:
    """Straight east keeps the latitude to within the plugin's own steps; a U-turn at 3 degrees a
    second for 60 s reverses the heading; the distance flown is the speed's."""
    sc = scenario.parse("start 60.5 10.4 30000 90 480\nleg 600 0 0\nleg 60 3 0\n")
    path = sc.path()
    assert len(path) == 1 + round(660 / scenario.STEP)
    east = path[round(600 / scenario.STEP)]
    assert east[0] == pytest.approx(60.5, abs=1e-9)
    km = (east[1] - 10.4) * scenario.M_DEG * math.cos(math.radians(60.5)) / 1000
    assert km == pytest.approx(480 * scenario.KT * 600 / 1000, rel=1e-6)
    assert path[-1][3] == pytest.approx(270.0)
    assert path[-1][2] == pytest.approx(30000 * scenario.FT)


def test_the_loads_x_plane_finished_during_the_path() -> None:
    """Counted as the test pilot marks them: a load before the path (its start's) is not one, a
    load begun but not finished is not one yet."""
    lines = [
        "12:00:01 scenery load at 43.6 1.3, path -1.0 s",
        "12:00:20 scenery loaded in 19.0 s",
        "12:05:01 scenery load at 43.6 2.3, path 300.0 s",
        "12:05:09 scenery loaded in 8.0 s",
        "12:09:01 scenery load at 43.6 3.3, path 540.0 s",
    ]
    assert fly.loads_done(lines) == 1
    assert fly.loads_done([*lines, "12:09:07 scenery loaded in 6.0 s"]) == 2


def test_a_leg_changes_the_ground_speed_evenly() -> None:
    """A leg's fourth number is the ground speed at its end, reached evenly: 500 kt down to 250 kt
    over 600 s flies their mean; a leg without one keeps it."""
    sc = scenario.parse("start 45 6 30000 0 500\nleg 600 0 0 250\nleg 100 0 0\n")
    path = sc.path()
    mid = path[round(600 / scenario.STEP)]
    km = (mid[0] - 45) * scenario.M_DEG / 1000
    assert km == pytest.approx(375 * scenario.KT * 600 / 1000, rel=2e-3)
    last = (path[-1][0] - mid[0]) * scenario.M_DEG / 1000
    assert last == pytest.approx(250 * scenario.KT * 100 / 1000, rel=1e-6)


def test_the_scenarios_of_the_repository_read_and_follow_each_other() -> None:
    names = sorted(p.stem for p in scenario.SCENARIOS.glob("*.txt"))
    assert {"scandinavia-columns", "scandinavia-rows"} <= set(names)
    for name in names:
        assert scenario.load(name).duration > 0
    lat, lon, _, hdg = scenario.load("scandinavia-columns").end()
    rows = scenario.load("scandinavia-rows").start
    # one series flies both without a second start: the test pilot finds the aircraft there
    assert math.hypot(lat - rows.lat, (lon - rows.lon) * math.cos(math.radians(lat))) < 0.01
    assert hdg == pytest.approx(rows.heading)
    with pytest.raises(scenario.ScenarioError, match="no scenario"):
        scenario.load("nowhere")


# ---- the report ---------------------------------------------------------------------------------


def test_scenery_loads_are_cut_at_the_test_pilots_marks() -> None:
    lines = [
        "0:00:01.000 I/SCN: something else",
        dsf(60, 10),
        dsf(60, 10, "Custom Scenery/simHeaven_X-WORLD-Pro_Europe-09-scenery"),
        dsf(61, 11),
        "OrthoStudio XP Test Pilot: scenario read: start 60.5000 10.4000, 3 legs",
        mark(60.5, 10.65, -1),
        dsf(59, 9),
        mark(60.5, 10.652, 95.5),
        dsf(59, 12),
        dsf(60, 12),
        dsf(61, 12),
        "OrthoStudio XP Test Pilot: scenery loaded in 3.5 s",
    ]
    loads = report.scenery_loads(lines)
    assert loads[2].seconds == 3.5 and loads[1].seconds is None
    assert [ld.path_s for ld in loads] == [None, None, 95.5]
    assert loads[0].tiles == {(60, 10), (61, 11)}
    assert loads[0].packs["simHeaven_X-WORLD-Pro_Europe-09-scenery"] == {(60, 10)}
    assert (loads[1].lat, loads[1].lon, loads[1].tiles) == (60.5, 10.65, {(59, 9)})
    assert loads[2].tiles == {(59, 12), (60, 12), (61, 12)}


def test_the_flight_befores_late_lines_are_not_the_starts() -> None:
    """X-Plane writes Log.txt late: a scenario's part may begin with the DSFs of the one before."""
    lines = [
        dsf(60, 9),  # the flight before's last load, written late
        "OrthoStudio XP Test Pilot: done",
        "OrthoStudio XP Test Pilot: scenario read: start 60.4150 9.9640, 4 legs",
        "OrthoStudio XP Test Pilot: already at the start: not placed again",
        mark(61.1, 10.0, 170.0),
        dsf(62, 9),
    ]
    first, load = report.scenery_loads(lines, launched=False)
    assert first.tiles == set()
    assert load.tiles == {(62, 9)}
    assert report.scenery_loads(lines, launched=True)[0].tiles == {(60, 9)}


def test_the_window_lines_say_what_came_again_and_what_the_model_misses() -> None:
    loads = report.scenery_loads(
        [
            *(dsf(la, lo) for la in (59, 60, 61) for lo in (8, 9, 10, 11)),
            mark(60.5, 10.652, 95.0),
            *(dsf(la, 12) for la in (59, 60, 61)),
            mark(60.45, 12.348, 700.0),
            *(dsf(la, 10) for la in (59, 60, 61)),
        ]
    )

    def model(lat: float, lon: float) -> set[tuple[int, int]]:
        return {(la, lo) for la in (59, 60, 61) for lo in range(9, 13)}

    text = "\n".join(report.window_lines(loads, model))
    assert "**start**: 12 tiles, columns 8..11 (4), rows 59..61 (3)" in text
    assert "columns 12..12 (1), its near edge 1.35 deg" in text  # 1.35 degrees ahead, east
    assert "LOADED AGAIN (X-Plane had let go of them): +59+010 +60+010 +61+010" in text
    assert text.count("inside Live's model") == 2
    loads[1].packs["x"] = {(62, 14)}
    assert "OUTSIDE LIVE'S MODEL of X-Plane's window: +62+014" in "\n".join(
        report.window_lines(loads, model)
    )


def at(stamp: str, line: str) -> str:
    """A line of Log.txt at another moment of X-Plane's clock."""
    return line.replace("0:09:05.591", stamp, 1)


def test_the_tiles_of_a_load_as_they_came() -> None:
    """Norway, 2026-09-30: two tiles of a column within 10 s, the third 7.5 minutes later, and
    X-Plane's log says nothing of why: the report says when each came, and the gap."""
    loads = report.scenery_loads(
        [
            mark(60.5, 12.65, 498.9),
            at("0:10:29.620", dsf(61, 14, "Custom Scenery/Global_Forests_v2")),
            at("0:10:40.697", dsf(61, 14, "Custom Scenery/z_osxp_live")),
            at("0:10:50.590", dsf(59, 14, "Custom Scenery/z_osxp_live")),
            at("0:18:19.013", dsf(60, 14, "Custom Scenery/z_osxp_live")),
        ]
    )
    assert report.arrival_lines(loads[1]) == [
        "  - tiles as they came, from the first: +61+014 0 s, +59+014 10 s, +60+014 458 s",
        "  - NO TILE FOR 448 s, then +60+014",
    ]
    assert report.arrival_lines(loads[0]) == []  # none, or a single tile: nothing to say


def test_the_live_model_holds_the_window_measured_at_43_6_north() -> None:
    """The start of the 60 s test flight over Toulouse (2026-09-29): 4 columns by 3 rows."""
    model = report.xplane_window_model()
    assert model is not None
    measured = {(la, lo) for la in (42, 43, 44) for lo in (-1, 0, 1, 2)}
    assert measured <= model(43.63, 1.40)


def write_frames(path: Path, rows: list[tuple[float, float, int, int]]) -> None:
    lines = ["run_s,path_s,frame_ms,lat,lon,ele_m,state,view_x,view_y,view_z,loading"]
    lines += [
        f"{100 + t:.4f},{t:.2f},{ms:.4f},60.5,10.4,9144.0,{state},0,0,0,{loading}"
        for t, ms, state, loading in rows
    ]
    path.write_text("\n".join(lines) + "\n60.1,12")  # the last line cut short by X-Plane quitting


def test_the_frames_of_the_path_only(tmp_path: Path) -> None:
    rows = [(t / 60, 16.0, 2, 0) for t in range(600)]
    rows[300] = (5.0, 120.0, 2, 1)
    rows.insert(0, (0.0, 900.0, 1, 1))  # before the path: not counted
    write_frames(tmp_path / "frames.csv", rows)
    f = report.frames_of(tmp_path / "frames.csv")
    assert f is not None
    assert f.count == 600
    assert f.max_ms == 120.0 and f.over_100 == 1 and f.over_50 == 1
    assert f.p50_ms == 16.0
    assert f.worst[0] == (5.0, 120.0, 1)
    assert report.frames_of(tmp_path / "none.csv") is None


def test_a_report_from_a_flights_folder(tmp_path: Path) -> None:
    (tmp_path / "run.json").write_text(
        json.dumps(
            {
                "scenario": "scandinavia-columns",
                "streamer": "none",
                "cold": False,
                "xplane_start": "started for the series",
                "outcome": "done",
                "to_flying_s": 41.0,
                "flight_s": 1290.0,
            }
        )
    )
    write_frames(tmp_path / "frames.csv", [(t / 60, 16.0, 2, 0) for t in range(120)])
    (tmp_path / "Log.txt").write_text("\n".join([dsf(60, 10), mark(60.5, 10.65, 95), dsf(60, 12)]))
    (tmp_path / "events.txt").write_text("0.000 scenario read\n1300.000 done\n")
    (tmp_path / "live.txt").write_text("started\nthe camera ahead of Live at 60.5 10.9\nok\n")
    (tmp_path / "screenshots").mkdir()
    (tmp_path / "screenshots" / "b738_1.png").write_bytes(b"png")
    text = report.report(tmp_path)
    assert text.startswith("# scandinavia-columns")
    assert "from the scenario sent to the path's start: 41 s" in text
    assert "OUTCOME" not in text
    assert "**load 1** at path 95 s" in text
    assert "1300.000 done" in text
    assert "## Live\n\n```\n3 lines of output\nthe camera ahead of Live" in text
    assert "- b738_1.png" in text


# ---- the runner ---------------------------------------------------------------------------------


def test_the_runner_sends_a_scenario_whole_and_sets_old_files_aside(tmp_path: Path) -> None:
    box = fly.box(tmp_path)
    box.mkdir(parents=True)
    (box / "events.txt").write_text("old")
    (box / "quit").write_text("")
    fly.clear_box(tmp_path)
    assert sorted(p.name for p in (box / "earlier").iterdir()) == ["events.txt", "quit"]
    sc = scenario.parse("start 60.5 10.4 30000 90 480\nleg 60 0 0\nend quit\n", "x")
    fly.send(tmp_path, sc)
    assert (box / "scenario.txt").read_text() == sc.for_the_pilot()
    assert not (box / "scenario.tmp").exists()


def test_the_runner_collects_a_flights_files(tmp_path: Path) -> None:
    xp, folder, out = tmp_path / "X-Plane 12", tmp_path / "out" / "1-x", tmp_path / "out"
    box = fly.box(xp)
    box.mkdir(parents=True)
    folder.mkdir(parents=True)
    for name in ("events.txt", "frames.csv", "scenario.done"):
        (box / name).write_text(name)
    (xp / "Log.txt").write_bytes(b"before the scenario\nits lines\n")  # as X-Plane writes it
    shots = xp / "Output" / "screenshots"
    shots.mkdir(parents=True)
    (shots / "mine.png").write_bytes(b"the user's")
    before = fly.screenshots_now(xp)
    (shots / "b738_1.png").write_bytes(b"the flight's")
    (out / "live.txt").write_bytes(b"earlier flight\nthis flight\n")
    fly.collect(xp, folder, len("before the scenario\n"), before, len("earlier flight\n"), out)
    assert (folder / "Log.txt").read_text() == "its lines\n"
    assert (folder / "scenario.txt").read_text() == "scenario.done"
    assert (folder / "events.txt").exists() and not (box / "events.txt").exists()
    assert [p.name for p in (folder / "screenshots").iterdir()] == ["b738_1.png"]
    assert (shots / "mine.png").exists()  # the user's own, untouched
    assert (folder / "live.txt").read_text() == "this flight\n"


def test_the_runner_waits_for_xplanes_late_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fly, "POLL_S", 0.0)
    monkeypatch.setattr(fly, "LOG_LATE_S", 0.05)
    (tmp_path / "Log.txt").write_text("OrthoStudio XP Test Pilot: done\nlater\n")
    assert not fly.wait_log(tmp_path, len("OrthoStudio XP Test Pilot: done\n"), "Pilot: done")
    with (tmp_path / "Log.txt").open("a") as f:
        f.write("OrthoStudio XP Test Pilot: done\n")
    assert fly.wait_log(tmp_path, len("OrthoStudio XP Test Pilot: done\n"), "Pilot: done")


def test_the_runner_waits_for_the_test_pilots_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = fly.box(tmp_path)
    box.mkdir(parents=True)
    (box / "events.txt").write_text(
        "0.0 scenario read\n3.0 flying the path: 1 legs, 60 s\n63.0 done\n"
    )
    monkeypatch.setattr(fly, "POLL_S", 0.0)
    monkeypatch.setattr(fly, "xplane_up", lambda: True)
    monkeypatch.setattr(fly, "xplane_gone", lambda: False)
    sc = scenario.parse("start 60.5 10.4 30000 90 480\nleg 60 0 0\n")
    outcome, times = fly.wait_flight(tmp_path, sc, 0.0)
    assert outcome == "done"
    assert "flight_s" in times


def test_the_runner_says_when_xplane_went(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fly.box(tmp_path).mkdir(parents=True)
    monkeypatch.setattr(fly, "POLL_S", 0.0)
    monkeypatch.setattr(fly, "xplane_up", lambda: True)
    monkeypatch.setattr(fly, "xplane_gone", lambda: True)
    sc = scenario.parse("start 60.5 10.4 30000 90 480\nleg 60 0 0\n")
    assert fly.wait_flight(tmp_path, sc, 1e18)[0] == "X-Plane quit"


def test_the_series_summary_counts_each_load_once(tmp_path: Path) -> None:
    """The report names a load twice: where it was, then what X-Plane and the computer did."""
    (tmp_path / "report.md").write_text(
        "## X-Plane's scenery loads\n"
        "- **start**: 12 tiles\n"
        "- **load 1** at path 618 s, aircraft 44.100 +1.510 (lat x.100, lon x.510): 4 tiles\n"
        "- **load 2** at path 741 s, aircraft 44.184 +1.650 (lat x.184, lon x.650): 3 tiles\n"
        "## X-Plane and the computer during the loads\n"
        "- **load 1** at path 618 s: 35 s, X-Plane 3.02 cores\n"
        "- **load 2** at path 741 s: 28 s, X-Plane 2.92 cores\n"
    )
    assert fly.summary_lines(tmp_path)[0] == "- 2 scenery loads during the path"


def test_a_cold_live_keeps_the_users_settings_but_not_their_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orthostudio.live as live

    mine = tmp_path / "live.toml"
    mine.write_text(
        'enabled = true\n\n[stream]\nmax_zoom = 17\nfolder = "/Volumes/Disk/Live"\n'
        'simbrief = "someone"\n'
    )
    monkeypatch.setattr(live, "settings_file", lambda: mine)
    home = fly.cold_home(tmp_path / "home")
    text = (home / "live.toml").read_text()
    assert "enabled = true" in text and "max_zoom = 17" in text
    assert 'folder = ""' in text and 'simbrief = ""' in text
    assert "someone" in mine.read_text()  # the user's own file, untouched


def test_a_cold_live_without_settings_has_live_s_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orthostudio.live as live

    monkeypatch.setattr(live, "settings_file", lambda: tmp_path / "none" / "live.toml")
    assert (fly.cold_home(tmp_path / "home") / "live.toml").read_text() == "enabled = true\n"


def test_a_cold_live_streams_onto_the_region_live_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A computer without AutoOrtho (a Shadow PC): the region is the one Live installed in the
    user's folder, which a cold Live's own folder does not hold; where X-Plane holds AutoOrtho's
    (the developer's Mac), Live finds it itself, as always."""
    from orthostudio.live import aoregions, pack

    xp = tmp_path / "X-Plane 12"
    (xp / "Custom Scenery").mkdir(parents=True)
    users = tmp_path / "live"
    monkeypatch.setattr(pack, "live_root", lambda: users)
    toulouse = scenario.parse("start 43.63 1.37 3000 320 230\nleg 60 0 0\n", "toulouse")
    oslo = scenario.parse("start 60.5 10.4 30000 90 480\nleg 60 0 0\n", "oslo")
    new_york = scenario.parse("start 40.6 -73.8 3000 0 230\nleg 60 0 0\n", "new-york")
    with pytest.raises(fly.RefusedError, match="the region eur is not installed"):
        fly.cold_source(xp, [toulouse])
    mesh = aoregions.mesh_dir(users, "eur")
    (mesh / "terrain").mkdir(parents=True)
    assert fly.cold_source(xp, [toulouse, oslo]) == mesh
    with pytest.raises(fly.RefusedError, match="several regions"):
        fly.cold_source(xp, [toulouse, new_york])
    theirs = xp / "Custom Scenery" / "z_autoortho" / "scenery" / "z_ao_eur"
    (theirs / "terrain").mkdir(parents=True)
    assert fly.cold_source(xp, [toulouse]) is None


AO_SETTINGS = """[general]
gui = True
showconfig = True

[paths]
xplane_path = /Users/someone/X-Plane 12
scenery_path = /Users/someone/X-Plane 12/Custom Scenery
cache_dir = /Users/someone/.autoortho-data/cache
download_dir = /Users/someone/.autoortho-data/downloads
log_file = /Users/someone/.autoortho-data/logs/autoortho.log
dsf_dir = /Users/someone/.autoortho-data/dsf

[autoortho]
maptype_override = BI
max_zoom = 15
max_zoom_near_airports = 16
prefetch_enabled = True
"""


@pytest.fixture
def user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "someone"
    home.mkdir()
    (home / ".autoortho").write_text(AO_SETTINGS)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@mac_only
def test_autoortho_with_the_users_settings_at_other_zooms(user_home: Path, tmp_path: Path) -> None:
    home = fly.autoortho_home(tmp_path / "ao-home", "mine", cold=True, zooms=(17, 19))
    text = (home / ".autoortho").read_text()
    assert "max_zoom = 17\n" in text and "max_zoom_near_airports = 19" in text
    assert "prefetch_enabled = True" in text and "maptype_override = BI" in text  # theirs kept
    assert f"cache_dir = {home / '.autoortho-data' / 'cache'}" in text
    assert f"log_file = {home / '.autoortho-data' / 'logs' / 'autoortho.log'}" in text
    assert "xplane_path = /Users/someone/X-Plane 12\n" in text
    assert "showconfig = False" in text
    assert fly.autoortho_folder(home) == home / ".autoortho-data" / "cache"
    assert (user_home / ".autoortho").read_text() == AO_SETTINGS  # the user's own, untouched


@mac_only
def test_autoortho_with_the_users_settings_and_cache(user_home: Path, tmp_path: Path) -> None:
    home = fly.autoortho_home(tmp_path / "ao-home", "mine", cold=False, zooms=None)
    text = (home / ".autoortho").read_text()
    assert "max_zoom = 15\n" in text
    assert "cache_dir = /Users/someone/.autoortho-data/cache" in text  # warm: theirs
    assert fly.autoortho_folder(home) == Path("/Users/someone/.autoortho-data/cache")


@mac_only
def test_autoortho_with_its_own_defaults(user_home: Path, tmp_path: Path) -> None:
    home = fly.autoortho_home(tmp_path / "ao-home", "default", cold=True, zooms=None)
    text = (home / ".autoortho").read_text()
    # AutoOrtho fills in its defaults for every setting the file leaves out: only where X-Plane is
    assert "max_zoom" not in text and "maptype_override" not in text and "prefetch" not in text
    assert "xplane_path = /Users/someone/X-Plane 12\n" in text
    assert "scenery_path = /Users/someone/X-Plane 12/Custom Scenery" in text
    assert text.index("[paths]") < text.index("cache_dir") and "[general]" in text
    with_zooms = fly.autoortho_home(tmp_path / "ao-2", "default", cold=True, zooms=(17, 19))
    text = (with_zooms / ".autoortho").read_text()
    assert "[autoortho]\nmax_zoom = 17\nmax_zoom_near_airports = 19" in text


@pytest.mark.parametrize(("text", "zooms"), [("17/19", (17, 19)), ("16/18", (16, 18))])
def test_zooms_as_autoortho_says_them(text: str, zooms: tuple[int, int]) -> None:
    assert fly.zooms_of(text) == zooms


@pytest.mark.parametrize("text", ["17", "17/16", "20/20", "a/b"])
def test_zooms_that_make_no_sense(text: str) -> None:
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        fly.zooms_of(text)


# ---- the computer -------------------------------------------------------------------------------


def test_the_samplers_readings(monkeypatch: pytest.MonkeyPatch) -> None:
    import sampler

    assert sampler.cpu_seconds("21:38.20") == pytest.approx(1298.2)
    assert sampler.cpu_seconds("1:02:03.50") == pytest.approx(3723.5)
    assert sampler.cpu_seconds("2-00:00:01") == pytest.approx(172801)
    rows = [
        (10, 1, 100.0, 5.0, "python"),
        (11, 10, 50.0, 2.0, "python worker"),
        (12, 11, 25.0, 1.0, "python worker's child"),
        (13, 1, 999.0, 99.0, "X-Plane"),
    ]
    assert sampler.tree(rows, 10) == (175.0, 8.0)
    ioreg = (
        "+-o IOBlockStorageDriver  <class IOBlockStorageDriver>\n"
        '    "Statistics" = {"Bytes (Write)"=0,"Bytes (Read)"=1}\n'
        "+-o IOBlockStorageDriver  <class IOBlockStorageDriver>\n"
        '  |   "Statistics" = {"Operations (Write)"=5,"Bytes (Write)"=179092750336}\n'
        '  |   "BSD Name" = "disk0"\n'
        '  |   "Statistics" = {"Bytes (Write)"=4177920}\n'
        '  |   "BSD Name" = "disk1"\n'
        "+-o IOBlockStorageDriver  <class IOBlockStorageDriver>\n"
        '    "Statistics" = {"Bytes (Write)"=75561712640}\n'
        '    "BSD Name" = "disk4"\n'
    )
    monkeypatch.setattr(sampler, "WINDOWS", False)  # a Mac's commands, read on any system
    monkeypatch.setattr(sampler, "_run", lambda cmd: ioreg)
    assert sampler.disk_writes() == {"disk0": 179092750336, "disk4": 75561712640}
    reads = ioreg.replace('"Bytes (Write)"=179092750336', '"Bytes (Write)"=7,"Bytes (Read)"=9')
    assert sampler.disk_bytes(reads) == {"disk0": (7, 9), "disk4": (75561712640, 0)}
    netstat = (
        "Name  Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts\n"
        "lo0   16384 <Link#1>                       1195148     0  509149045  1195148\n"
        "en0   1500  <Link#14>   aa:d9:19:ce:bb:4d 24519595     0 34788779108  6038397\n"
        "en0   1500  fe80::14a2: fe80:e::14a2:a606 24519595     - 34788779108  6038397\n"
        "en1   1500  <Link#15>   aa:d9:19:ce:bb:4e       10     0       1000       10\n"
    )
    monkeypatch.setattr(sampler, "_run", lambda cmd: netstat)
    assert sampler.net_in() == 34788779108 + 1000


def test_what_the_start_and_the_path_cost() -> None:
    def row(wall: float, net: float, disk: float, s_cpu: float, s_mb: float) -> dict:
        return {
            "wall": wall,
            "xp_mb": 12288.0,
            "xp_cpu": wall,  # one core
            "s_mb": s_mb,
            "s_cpu": s_cpu,
            "net": int(net * 1e9),
            "disks": {"disk0": int(disk * 1e9)},
        }

    rows = [row(0, 0, 0, 0, 0), row(60, 1.0, 2.0, 30, 4096), row(660, 3.0, 5.0, 330, 6144)]
    run = {"sent_wall": 0.0, "flying_wall": 60.0, "done_wall": 660.0, "disks": {"X-Plane": "disk0"}}
    start, path = report.computer_lines(rows, run)
    assert start.startswith("- **start** (1.0 min): downloaded 1.00 GB, disk0 written 2.00 GB")
    assert "X-Plane 1.00 core" in start and "streamer 0.50 core" in start
    assert path.startswith("- **path** (10.0 min): downloaded 2.00 GB, disk0 written 3.00 GB")
    assert "streamer 0.50 core, 6.0 GB (most 6.0)" in path
    assert report.computer_lines(rows, {"sent_wall": 0.0}) == []


# ---- campaigns ----------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="libproc, sysctl and notify: macOS")
def test_a_process_and_the_mac_read_from_outside() -> None:
    """What the sampler reads of X-Plane without attaching to it, tried on this test's own
    process: its threads, its whole memory and disk reads; the Mac's memory and heat."""
    import os

    import sampler

    threads = sampler.threads(os.getpid())
    assert threads and all(
        cpu >= 0 and state in sampler.THREAD_STATES for _, _, cpu, state, _ in threads
    )
    use = sampler.usage(os.getpid())
    assert use["footprint"] > 1_000_000 and use["read"] >= 0 and use["pageins"] >= 0
    mem = sampler.memory()
    assert 0 <= mem["free_pct"] <= 100 and mem["pressure"] in sampler.PRESSURE
    assert mem["compressed_mb"] >= 0 and mem["swapped_mb"] >= 0
    assert sampler.heat() in sampler.HEAT
    assert sampler.usage(999_999_999) == {} and sampler.threads(999_999_999) == []
    assert 1 <= sampler.cores_allowed() <= (os.cpu_count() or 1)


def test_the_busy_processes_between_two_samples() -> None:
    """processes.csv (2026-10-01): each process over OTHERS_MIN_CORES since the line before, the
    others' share of a hot Mac's cores."""
    import sampler

    before: dict[int, tuple[float, float]] = {}
    first = [(1, 0, 10.0, 100.0, "/usr/libexec/mds_stores"), (2, 0, 5.0, 50.0, "/bin/zsh")]
    assert sampler.busy_processes(first, before, 1000.0) == []  # nothing to compare with yet
    second = [
        (1, 0, 10.0, 104.0, "/usr/libexec/mds_stores"),  # 4 s over 5 s
        (2, 0, 5.0, 50.1, "/bin/zsh"),  # 0.02 core: not busy
        (3, 0, 1.0, 9.0, "Google Chrome Helper"),  # new since: no rate yet
    ]
    assert sampler.busy_processes(second, before, 1005.0) == [["1005.0", 1, "mds_stores", "0.80"]]


def write_system(path: Path, rows: list[dict]) -> None:
    """``system.csv`` with 2026-09-30's readings, as sampler.Sampler writes it."""
    import csv

    import sampler

    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sampler.FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in sampler.FIELDS})


def test_x_plane_and_the_mac_during_a_load(tmp_path: Path) -> None:
    """A load of 100 s in a path of 400: X-Plane's texture thread works harder, its scenery thread
    stops, it reads much more; the Mac's memory runs short. The report says each."""
    flying, done, t0, t1 = 1000.0, 1400.0, 1100.0, 1200.0

    def cpu(outside: float, during: float, t: float) -> float:  # cumulative, at moment t
        before = max(0.0, min(t, t0) - 995.0) * outside
        inside = max(0.0, min(t, t1) - t0) * during
        after = max(0.0, t - t1) * outside
        return before + inside + after

    threads = {
        "a1": ("SceneryLoader", 1.0, 0.0),
        "b2": ("TexturePager", 0.5, 3.0),
        "1f00": ("", 2, 2),
    }
    moments = [995.0 + 5 * k for k in range(83)]  # to 1405
    lines = ["wall,thread,name,cpu_s,state,priority"]
    for t in moments:
        for handle, (name, out_rate, in_rate) in threads.items():
            lines.append(f"{t:.1f},{handle},{name},{cpu(out_rate, in_rate, t):.3f},3,31")
    (tmp_path / "threads.csv").write_text("\n".join(lines) + "\n")
    loading = lambda t: t0 < t < t1  # noqa: E731
    write_system(
        tmp_path / "system.csv",
        [
            {
                "wall": t,
                "xplane_rss_mb": 9000,
                "xplane_cpu_s": sum(cpu(o, i, t) for _, o, i in threads.values()),
                "streamer_rss_mb": 4000,
                "streamer_cpu_s": 0.5 * t,
                "net_in": int(t * 1e6),
                "disks": f"disk0={int(t * 1e7)}",
                "xplane_footprint_mb": 20000 if loading(t) else 15000,
                "xplane_read_mb": cpu(1.0, 50.0, t),  # 1 MB/s, 50 during the load
                "xplane_pageins": cpu(10, 400, t),
                "mem_free_pct": 40 if loading(t) else 60,
                "pressure": 2 if loading(t) else 1,
                "compressed_mb": 3072,
                "swapped_mb": 0,
                "heat": 2,
                "disk_reads": f"disk0={int(t * 1e8)}",
            }
            for t in moments
        ],
    )
    run = {
        "flying_wall": flying,
        "done_wall": done,
        "sent_wall": 990.0,
        "disks": {"X-Plane": "disk0"},
    }
    (tmp_path / "run.json").write_text(json.dumps({**run, "scenario": "s", "outcome": "done"}))
    (tmp_path / "Log.txt").write_text(
        "\n".join(
            [mark(60.5, 12.65, 100.0), dsf(60, 14), f"{report.PILOT}scenery loaded in 100.0 s"]
        )
    )
    text = report.report(tmp_path)
    during = text.split("## X-Plane and the computer during the loads")[1]
    assert (
        "- **load 1** at path 100 s: 100 s, X-Plane 5.00 cores, read 50.0 MB/s, 400 page-ins/s"
        in during
    )
    assert (
        "memory free at least 40 %, pressure WARNING" in during and "heat heavy at worst" in during
    )
    assert (
        "busiest threads: `TexturePager` 3.00 cores (0.50 outside the loads); thread …1f00 2.00"
        in during
    )
    assert "slowed the most: `SceneryLoader` 0.00 cores (1.00 outside the loads)\n" in during
    path = next(ln for ln in text.splitlines() if ln.startswith("- **path**"))
    assert "X-Plane 3.88 core, 8.8 GB (most 8.8), 14.6 GB with its GPU's (most 19.5)" in path
    assert "the computer: memory free at least 40 %, pressure WARNING" in path
    assert "nothing swapped" in path


def test_the_campaigns_of_the_repository_read(tmp_path: Path) -> None:
    camp = fly.read_campaign("ao-vs-live")
    assert camp.scenarios == ["toulouse-low"]
    assert camp.title.startswith("AutoOrtho contre Live")
    runs = dict(camp.runs)
    assert runs["ao-default-1618"] == [
        "--streamer", "autoortho", "--ao-settings", "default", "--zooms", "16/18", "--cold",
    ]  # fmt: skip
    assert all(options[0] == "--streamer" for options in runs.values())
    # the user's four comparisons (2026-09-29), like against like, six flights: Live's flown once
    assert camp.comparisons == [
        ["ao-default-1618", "live-1618"],
        ["ao-default-1719", "live-1719"],
        ["ao-mine-1618", "live-1618"],
        ["ao-mine-1719", "live-1719"],
    ]
    comps, runs = camp.picked(None)
    assert len(comps) == 4 and len(runs) == 6
    assert all(flights == ["toulouse-low"] for _, _, flights in runs)
    comps, runs = camp.picked([(1, None), (3, None)])
    assert [i for i, _, _ in runs] == ["ao-default-1618", "live-1618", "ao-mine-1618"]
    assert [c.number for c in comps] == [1, 3]  # their own numbers, for the tables
    assert [i for i, _, _ in camp.picked([(4, None)])[1]] == ["ao-mine-1719", "live-1719"]
    # each comparison on its own flights: a run two share flies both's, in one X-Plane
    comps, runs = camp.picked([(1, ["toulouse-low"]), (3, ["scandinavia-columns"])])
    flights = {i: f for i, _, f in runs}
    assert flights["live-1618"] == ["toulouse-low", "scandinavia-columns"]
    assert flights["ao-default-1618"] == ["toulouse-low"]
    assert flights["ao-mine-1618"] == ["scandinavia-columns"]
    assert comps[1].scenarios == ["scandinavia-columns"]
    bad = tmp_path / "bad.txt"
    bad.write_text("scenarios toulouse-low\nfly --streamer live\n")
    with pytest.raises(fly.RefusedError, match="line 2"):
        fly.read_campaign(str(bad))
    bad.write_text("scenarios toulouse-low\nrun a --streamer live\ncompare a b\n")
    with pytest.raises(fly.RefusedError, match="line 3: compare two runs named before"):
        fly.read_campaign(str(bad))
    with pytest.raises(fly.RefusedError, match="no campaign"):
        fly.read_campaign("nowhere")


def test_a_campaign_without_comparisons_picks_its_runs(tmp_path: Path) -> None:
    path = tmp_path / "plain.txt"
    path.write_text("scenarios camera-check\nrun --streamer live\nrun --streamer autoortho\n")
    camp = fly.read_campaign(str(path))
    assert [i for i, _ in camp.runs] == ["run1", "run2"]
    _, runs = camp.picked([(2, None)])
    assert runs == [("run2", ["--streamer", "autoortho"], ["camera-check"])]
    assert fly.picks_of("1, 3") == [(1, None), (3, None)] and fly.picks_of("") is None
    assert fly.picks_of("1:toulouse-low+camera-check,3") == [
        (1, ["toulouse-low", "camera-check"]),
        (3, None),
    ]
    with pytest.raises(fly.RefusedError):
        fly.picks_of("one")


def test_what_ran_in_a_few_words() -> None:
    import argparse

    def ns(**kw: object) -> argparse.Namespace:
        base = {"streamer": "none", "ao_settings": "mine", "zooms": None, "cold": False}
        return argparse.Namespace(**{**base, **kw})

    assert fly.label(ns()) == "X-Plane alone"
    assert fly.label(ns(streamer="autoortho", ao_settings="default", cold=True)) == (
        "AutoOrtho, its defaults, cold"
    )
    assert (
        fly.label(ns(streamer="autoortho", zooms=(17, 19))) == "AutoOrtho, your settings, ZL17/19"
    )
    assert fly.label(ns(streamer="live", zooms=(16, 18), cold=True)) == "Live, ZL16/18, cold"


def test_a_campaigns_runs_side_by_side(tmp_path: Path) -> None:
    runs = []
    for label, fps_ms in (("AutoOrtho, its defaults, cold", 20.0), ("Live, ZL16/18, cold", 16.0)):
        series = tmp_path / label.replace(",", "").replace(" ", "_")
        folder = series / "1-toulouse-low"
        folder.mkdir(parents=True)
        (folder / "run.json").write_text(
            json.dumps(
                {"outcome": "done", "sent_wall": 0.0, "flying_wall": 60.0, "done_wall": 660.0}
            )
        )
        write_frames(folder / "frames.csv", [(t / 50, fps_ms, 2, 0) for t in range(500)])
        (folder / "scenario.txt").write_text("start 43.63 1.37 3000 320 230\nleg 600 0 0\n")
        runs.append((label, series))
    text = report.campaign_table(runs)
    assert text.startswith("## toulouse-low")
    assert "| | AutoOrtho, its defaults, cold | Live, ZL16/18, cold |" in text
    assert "| frame time: mean, 99th, worst (ms) | 20.0, 20, 20 | 16.0, 16, 16 |" in text
    assert "| start | ? | ? |" in text  # no system.csv: the costs unknown, said so


@mac_only
def test_a_streamer_stops_with_every_process_it_started(tmp_path: Path) -> None:
    """AutoOrtho's mount worker has a process group of its own: a Ctrl-C to AutoOrtho's group left
    it running, its region mounted (2026-09-29). The whole tree gets it."""
    import subprocess
    import time

    child = tmp_path / "child.pid"
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
            " start_new_session=True)\n"
            f"open({str(child)!r}, 'w').write(str(c.pid))\n"
            "time.sleep(60)\n",
        ],
        start_new_session=True,
    )
    end = time.time() + 10
    while not child.exists() or not child.read_text():
        assert time.time() < end
        time.sleep(0.05)
    worker = int(child.read_text())
    tree = [p for p, _ in fly.process_tree(parent.pid)]
    assert tree[0] == parent.pid and worker in tree
    fly.stop_tree(parent.pid, timeout_s=10)
    parent.wait(5)
    end = time.time() + 5
    while fly._still(worker, dict(fly.process_tree(worker)).get(worker, "?")):
        assert time.time() < end, "the worker in its own group is still there"
        time.sleep(0.1)


def test_x_planes_telemetry_kept_when_written_during_the_series(tmp_path: Path) -> None:
    """X-Plane writes telemetry_0.tlm as it quits (its pager's counts, its GPU's memory): kept in
    the series' folder, not an older flight's."""
    import os

    xp = tmp_path / "X-Plane 12"
    tlm = xp / fly.TELEMETRY / "telemetry_0.tlm"
    tlm.parent.mkdir(parents=True)
    tlm.write_bytes(b"tlm")
    series = tmp_path / "flights" / "2026-10-01 090000"
    series.mkdir(parents=True)
    from datetime import datetime

    began = datetime(2026, 10, 1, 9, 0, 0).timestamp()
    os.utime(tlm, (began - 60, began - 60))  # the flight before's
    assert fly.keep_telemetry(xp, series) is None
    os.utime(tlm, (began + 1500, began + 1500))
    assert fly.keep_telemetry(xp, series) == series / "telemetry.tlm"
    assert (series / "telemetry.tlm").read_bytes() == b"tlm"
    assert fly.keep_telemetry(xp, tmp_path / "not a series") is None


def test_live_frozen_in_flight_is_a_live_test(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit):
        fly.main(["--freeze-in-flight", "scandinavia-columns"])
    assert "--freeze-in-flight is for Live" in capsys.readouterr().err


def test_a_cold_runs_heavy_files_go_its_record_stays(tmp_path: Path) -> None:
    """A cold run's own homes, under its series' folder: the heavy files deleted once its streamer
    stopped (a 2 min cold AutoOrtho run left 3.1 GB), Live's record kept for the map."""
    from orthostudio.live.filesystem import folder_link, is_folder_link

    out = tmp_path / "build" / "flights" / "2026-09-29 170000"
    pack = out / fly.LIVE_HOME / "live" / "pack"
    (pack / "_live").mkdir(parents=True)
    (pack / "_live" / "ortho.json").write_text('{"textures": {"a": 0}}')
    (pack / "textures").mkdir()
    (pack / "textures" / "big.dds").write_bytes(b"x" * 1000)
    (out / fly.LIVE_HOME / "live.toml").write_text("enabled = true\n")
    cache = out / "ao-home" / ".autoortho-data" / "cache"
    cache.mkdir(parents=True)
    (cache / "chunk.jpg").write_bytes(b"x")
    logs = out / "ao-home" / ".autoortho-data" / "logs"
    logs.mkdir()
    (logs / "autoortho.log").write_text("log")
    xp = tmp_path / "X-Plane 12"
    scenery = xp / "Custom Scenery"
    scenery.mkdir(parents=True)
    folder_link(pack, scenery / "z_osxp_live")  # the test's Live left its link (Windows: junction)
    users = tmp_path / "users-pack"
    (users / "Earth nav data").mkdir(parents=True)
    folder_link(users, scenery / "y_osxp_live_overlays")  # not the test's: untouched
    fly.clean_cold(out, xp)
    assert (out / "live-built.json").read_text() == '{"textures": {"a": 0}}'
    assert not (out / fly.LIVE_HOME / "live").exists()
    assert not cache.exists() and (logs / "autoortho.log").exists()
    assert (out / fly.LIVE_HOME / "live.toml").exists()
    assert not is_folder_link(scenery / "z_osxp_live")
    assert is_folder_link(scenery / "y_osxp_live_overlays")
    assert (users / "Earth nav data").is_dir()


def test_a_scenario_sent_to_record_or_replay_the_users_camera(tmp_path: Path) -> None:
    sc = scenario.parse(
        "start 43.63 1.37 3000 320 230\ncamera 0 30 cockpit\ncamera 30 60 chase 150 30 180\n"
        "leg 60 0 0\nscreenshot 20\n",
        "cams",
    )
    plain = sc.for_the_pilot()
    assert "camera 30 60 chase" in plain and "camera-" not in plain
    record = sc.for_the_pilot(record_camera=True)
    assert "camera 0 30" not in record and "camera-record" in record
    assert "screenshot 20" in record  # the same moments, for the photos compared
    track = tmp_path / "my cams.csv"
    replay = sc.for_the_pilot(track=track)
    assert "camera 0 30" not in replay  # the user's, looped when shorter than the flight
    assert f"camera-track {track.resolve()}" in replay  # a path with a space, whole


def test_a_camera_recorded_is_kept_the_one_before_set_aside(tmp_path: Path) -> None:
    results = tmp_path / "flights"
    recorded = tmp_path / "camera.csv"
    recorded.write_text(
        "path_s,external,a,b,c,h,p,r,fov\n0.00,0,0,0,0,0,0,0,60\n0.05,1,-150,0,30,0,-10,0,60\n"
    )
    kept = fly.keep_camera(recorded, results, "toulouse-low")
    assert (
        kept == fly.camera_file(results, "toulouse-low")
        and kept.read_text() == recorded.read_text()
    )
    recorded.write_text(recorded.read_text() + "0.10,1,-151,0,30,0,-10,0,60\n")
    fly.keep_camera(recorded, results, "toulouse-low")
    names = sorted(p.name for p in (results / "cameras").iterdir())
    assert names[-1] == "toulouse-low.csv" and len(names) == 2  # the one before, dated
    assert fly.camera_file(results, "toulouse-low").read_text().count("\n") == 4
    empty = tmp_path / "empty.csv"
    empty.write_text("path_s,external,a,b,c,h,p,r,fov\n")
    assert fly.keep_camera(empty, results, "camera-check") is None


def test_the_photos_sharpness_where_their_ground_differs(tmp_path: Path) -> None:
    """The same camera, the same moment: the sky and the aircraft alike, the ground's detail not."""
    import numpy as np
    from PIL import Image, ImageFilter

    rng = np.random.default_rng(7)
    sky = np.full((256, 512), 200, dtype=np.uint8)
    ground = rng.integers(0, 255, size=(256, 512), dtype=np.uint8)
    sharp = Image.fromarray(np.vstack([sky, ground]))
    blurred_ground = Image.fromarray(ground).filter(ImageFilter.GaussianBlur(2))
    blurry = Image.fromarray(np.vstack([sky, np.asarray(blurred_ground)]))
    sharp.save(tmp_path / "live.png")
    blurry.save(tmp_path / "ao.png")
    got = report.sharpness([tmp_path / "ao.png", tmp_path / "live.png"])
    assert got is not None
    assert got["vs_first"][0] == 0.0 and got["vs_first"][1] > 50  # the sharp ground, much more
    assert 0.4 < got["ground"] <= 0.5  # the sky alike: left out
    assert report.sharpness([tmp_path / "ao.png", tmp_path / "ao.png"]) is None  # nothing differs
    assert report.sharpness([tmp_path / "ao.png"]) is None
    assert report.sharpness([tmp_path / "ao.png", None]) is None


def test_a_campaign_writes_one_table_for_one_comparison_and_more_for_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each run flown by a stand-in for fly.run; the tables written after each (the user saw the
    same table twice for one comparison, 2026-09-29)."""
    import argparse

    camp = tmp_path / "duo.txt"
    camp.write_text(
        "title Duo\nscenarios camera-check\n"
        "run ao --streamer autoortho --cold\nrun live --streamer live --cold\n"
        "run live2 --streamer live --zooms 17/19 --cold\n"
        "compare ao live\ncompare ao live2\n"
    )
    flown: list[list[str]] = []
    made = [0]

    def fake_run(a: argparse.Namespace) -> int:
        made[0] += 1
        a.out = tmp_path / "results" / f"2026-09-29 1700{made[0]:02d}"
        folder = a.out / "1-camera-check"
        folder.mkdir(parents=True)
        (folder / "run.json").write_text(json.dumps({"outcome": "done"}))
        flown.append([a.streamer, *a.scenarios])
        return 0

    for name in ("xplane_up", "live_runs", "autoortho_runs"):
        monkeypatch.setattr(fly, name, lambda: False)
    monkeypatch.setattr(fly, "wait_quiet", lambda: None)
    monkeypatch.setattr(fly, "run", fake_run)

    def campaign(pick: str | None) -> str:
        flown.clear()
        results = tmp_path / "results"
        for old in results.glob("*.md") if results.exists() else []:
            old.unlink()
        args = argparse.Namespace(results=results, xplane=None, camera="scenario", pick=pick)
        parser = argparse.ArgumentParser()
        parser.add_argument("scenarios", nargs="*")
        parser.add_argument("--streamer", default="none")
        parser.add_argument("--cold", action="store_true")
        parser.add_argument("--ao-settings", default="mine")
        parser.add_argument("--zooms", type=fly.zooms_of)
        parser.add_argument("--results", type=Path)
        parser.add_argument("--xplane", type=Path)
        parser.add_argument("--camera", default="scenario")
        assert fly.campaign(str(camp), parser, args) == 0
        return next(results.glob("*.md")).read_text()

    one = campaign("1")
    assert flown == [["autoortho", "camera-check"], ["live", "camera-check"]]
    assert one.count("| outcome |") == 1 and "# Comparison 1:" in one
    both = campaign(None)
    assert len(flown) == 3  # "ao" flown once for both comparisons
    assert both.count("| outcome |") == 3  # every flight, then each comparison
    assert "# Comparison 2:" in both


@mac_only
def test_the_watcher_follows_x_planes_log_and_samples_the_loads_that_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stacks.py (2026-10-01), run with sudo during a series: X-Plane's stacks once in cruise and
    during each load from 40 s after it began, every 75 s while it lasts; never during the start,
    nor after the path."""
    import stacks

    clock = [1000.0]
    monkeypatch.setattr(stacks.time, "time", lambda: clock[0])
    taken: list[tuple[str, list[str]]] = []

    def fake_spindump(flight: stacks.Flight, label: str, session: stacks.Session) -> None:
        taken.append((label, list(flight.load["meshes"]) if flight.load else []))
        flight.samples += 1

    monkeypatch.setattr(stacks, "spindump", fake_spindump)
    session = stacks.Session(tmp_path / "stacks" / "2026-10-01 090000")
    log = tmp_path / "Log.txt"
    flight = stacks.Flight(1, 4242)
    pilot = stacks.PILOT
    mesh = (
        "0:12:11.871 I/SCN: DSF load time: 29368088 for file /Users/u/X-Plane 12/Custom Scenery/"
        "z_osxp_live/Earth nav data/+50+010/+59+014.dsf (995788 tris, 96 skipped for -25.3 m^2)\n"
    )
    overlay = mesh.replace("995788 tris", "0 tris").replace("+59+014", "+61+014")

    def at(t: float, text: str = "") -> None:
        clock[0] = t
        with log.open("a") as f:
            f.write(text)
        stacks.read(flight, stacks.new_lines(log, flight), t, session)
        stacks.maybe_sample(flight, session)

    at(1000, f"{pilot}ready\n{pilot}scenery load at 60.5000 10.4000, path 0.0 s\n")
    at(1050)  # no path yet: the start's loads are not sampled
    assert taken == [] and flight.path_wall is None
    flight.load = None
    at(1100, f"{pilot}flying the path: 3 legs, 1288 s\n")
    at(1155, f"{pilot}scenery load at 60.5000 10.6500, path 55.4 s\n")
    at(1161, f"{pilot}scenery loaded in 6.2 s\n")  # a load over in 6 s: no sample
    at(1350)  # path 250 s: not yet cruise
    at(1400)  # path 300 s, no load for 239 s: the cruise sample
    at(1599, f"{pilot}scenery load at 60.5000 12.6500, path 498.9 s\n{overlay}{mesh[:40]}")
    at(1630, mesh[40:])  # the line ended: the outer tile's mesh
    assert taken == [("cruise-300s", [])]
    at(1640)
    at(1700)
    at(1715)
    at(1716, f"{pilot}scenery loaded in 117.0 s\n")
    at(1800)
    assert taken == [("cruise-300s", []), ("load3-41s", ["+59+014"]), ("load3-116s", ["+59+014"])]
    at(2400, f"{pilot}the path flown (1288 s)\n{pilot}scenery load at 60.4 10.3, path 1300 s\n")
    at(2500)  # the path flown: no more samples
    assert len(taken) == 3
    log.write_text(f"{pilot}ready\n")  # X-Plane's next start writes a new log
    assert stacks.new_lines(log, flight) == [f"{pilot}ready"]


# ---- the app itself, in its one window (2026-10-04) ---------------------------------------------


def test_the_app_in_a_few_words_and_its_options() -> None:
    import argparse

    def ns(**kw: object) -> argparse.Namespace:
        base = {
            "streamer": "app",
            "ao_settings": "mine",
            "zooms": (16, 18),
            "cold": True,
            "with_osxp": False,
            "freeze_in_flight": False,
            "record_camera": False,
            "camera": "scenario",
        }
        return argparse.Namespace(**{**base, **kw})

    assert fly.label(ns()) == "Live in the app, alone in it, ZL16/18, cold"
    assert (
        fly.label(ns(with_osxp=True)) == "Live in the app, with OrthoStudio XP open, ZL16/18, cold"
    )
    assert fly.refused_options(ns()) is None
    assert fly.refused_options(ns(freeze_in_flight=True)) is None  # Live's test, in the app too
    assert "--with-osxp is for the app" in fly.refused_options(ns(streamer="live", with_osxp=True))
    # the app takes its zooms from its settings: the run's own, a cold home's
    assert "needs --cold" in fly.refused_options(ns(cold=False))
    assert fly.refused_options(ns(cold=False, zooms=None)) is None


def test_a_cold_app_has_live_on_and_the_series_zooms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tomllib

    import orthostudio.live as live

    mine = tmp_path / "live.toml"
    mine.write_text(
        'enabled = false\n\n[stream]\nmax_zoom = 17\nsimbrief_user = "radioharris"\n'
        'folder = "/Volumes/Disk/Live"\napp_home = "build"\n'
    )
    monkeypatch.setattr(live, "settings_file", lambda: mine)
    home = fly.cold_home(tmp_path / "home")
    fly.app_settings(home, (16, 18))
    data = tomllib.loads((home / "live.toml").read_text())
    assert data["enabled"] is True  # the app asks nothing and shows Live: switched on
    stream = data["stream"]
    assert (stream["max_zoom"], stream["max_zoom_airports"]) == (16, 18)
    assert stream["simbrief_user"] == "" and stream["folder"] == ""  # neither the user's
    assert "radioharris" in mine.read_text()  # the user's own file, untouched


def test_the_one_window_campaign_reads() -> None:
    camp = fly.read_campaign("fenetre-unique")
    assert camp.title.startswith("La fenêtre unique")
    runs = dict(camp.runs)
    assert runs["live-seul"] == ["--streamer", "app", "--zooms", "16/18", "--cold"]
    assert runs["live-et-osxp"] == [
        "--streamer",
        "app",
        "--with-osxp",
        "--zooms",
        "16/18",
        "--cold",
    ]
    assert runs["xplane-seul"] == []
    assert [i for i, _, _ in camp.picked([(1, None)])[1]] == ["live-seul", "live-et-osxp"]
    assert [i for i, _, _ in camp.picked(None)[1]] == ["live-seul", "live-et-osxp", "xplane-seul"]


def test_a_series_with_another_imagery_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live with Esri World Imagery or EOX instead of Bing Maps (2026-10-08): the source in the
    cold app's settings, in Live's command, in the run's words, and refused where no Live runs or
    where the app would take the user's own settings."""
    import argparse
    import tomllib

    import orthostudio.live as live

    mine = tmp_path / "live.toml"
    mine.write_text('enabled = true\n\n[stream]\nprovider = "BI"\n')
    monkeypatch.setattr(live, "settings_file", lambda: mine)
    home = fly.cold_home(tmp_path / "home")
    fly.app_settings(home, (16, 18), "EOX")
    assert tomllib.loads((home / "live.toml").read_text())["stream"]["provider"] == "EOX"
    assert 'provider = "BI"' in mine.read_text()  # the user's own, untouched

    assert fly.imagery_of("Arc") == "Arc"
    with pytest.raises(argparse.ArgumentTypeError, match="BI"):
        fly.imagery_of("bing")

    def ns(**kw: object) -> argparse.Namespace:
        base = {"streamer": "app", "zooms": (16, 18), "cold": True, "imagery": "Arc"}
        return argparse.Namespace(**{**base, **kw})

    assert fly.label(ns()) == "Live in the app, alone in it, ZL16/18, Esri World Imagery, cold"
    assert fly.refused_options(ns()) is None
    assert fly.refused_options(ns(streamer="live")) is None
    assert "--imagery is for Live" in fly.refused_options(ns(streamer="autoortho"))
    assert "needs --cold" in fly.refused_options(ns(cold=False, zooms=None))
    runs = dict(fly.read_campaign("sources").runs)
    assert runs["esri"][-2:] == ["--imagery", "Arc"] and runs["eox"][-2:] == ["--imagery", "EOX"]


def test_the_sampler_counts_the_app_s_web_view(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Mac's web view is WebKit's processes working for the app, started by the system and not
    under it; Windows' is WebView2's, under the app."""
    import sampler

    rows = [
        (10, 1, 300.0, 5.0, "/opt/venv/bin/python"),  # the app's program
        (11, 10, 140.0, 2.0, "/opt/venv/bin/python"),  # OrthoStudio XP's engine, under it
        (20, 1, 120.0, 1.0, "/System/.../com.apple.WebKit.WebContent"),
        (21, 1, 50.0, 0.5, "/System/.../com.apple.WebKit.GPU"),
        (22, 1, 90.0, 9.0, "/System/.../com.apple.WebKit.WebContent"),  # another app's
        (30, 10, 80.0, 3.0, "msedgewebview2.exe"),  # Windows' names: the image's alone
    ]
    monkeypatch.setattr(sampler.sys, "platform", "darwin")
    monkeypatch.setattr(
        sampler, "responsible", lambda pid, command="": 10 if pid in (20, 21) else 99
    )
    assert [r[0] for r in sampler.web_view(rows, 10)] == [20, 21]
    monkeypatch.setattr(sampler.sys, "platform", "win32")
    assert [r[0] for r in sampler.web_view(rows, 10)] == [30]
    assert sampler.tree(rows, 11) == (140.0, 2.0)  # the engine alone
    assert sampler.FIELDS[-6:-2] == ["engine_rss_mb", "engine_cpu_s", "web_rss_mb", "web_cpu_s"]
    assert sampler.FIELDS[-2:] == ["xplane_written_mb", "streamer_written_mb"]


def test_what_the_app_s_engine_and_web_view_cost() -> None:
    def row(wall: float, s_cpu: float, e_cpu: float, w_cpu: float) -> dict:
        return {
            "wall": wall,
            "xp_mb": 12288.0,
            "xp_cpu": wall,
            "s_mb": 2048.0,
            "s_cpu": s_cpu,
            "net": 0,
            "disks": {},
            "e_mb": 150.0,
            "e_cpu": e_cpu,
            "w_mb": 300.0,
            "w_cpu": w_cpu,
        }

    rows = [row(0, 0, 0, 0), row(60, 30, 0.6, 3), row(660, 330, 6.6, 33)]
    run = {"sent_wall": 0.0, "flying_wall": 60.0, "done_wall": 660.0, "disks": {}}
    _, path = report.computer_lines(rows, run)
    assert "streamer 0.50 core, 2.0 GB (most 2.0)" in path
    assert "of it, OrthoStudio XP's engine 0.01 core, 0.15 GB" in path
    assert "of it, its web view 0.05 core, 0.29 GB" in path
    # without them (Live's streamer, AutoOrtho): nothing said of them
    plain = [
        {k: v for k, v in r.items() if k not in ("e_mb", "e_cpu", "w_mb", "w_cpu")} for r in rows
    ]
    assert "of it" not in report.computer_lines(plain, run)[1]


def test_the_app_opens_on_its_sides_and_starts_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--streamer app --with-osxp --cold``: macOS opens the runner's app with its environment,
    OrthoStudio XP's side first, then Live's, asked through the window; Live started as its Start
    starts it, the runner back once Live is ready for X-Plane."""
    import socket
    import threading

    import orthostudio.live as live

    monkeypatch.setattr(live, "settings_file", lambda: tmp_path / "none" / "live.toml")
    monkeypatch.setattr(fly, "POLL_S", 0.01)
    monkeypatch.setattr(fly, "_port_open", lambda port: False)  # no Live, no app of the user's
    monkeypatch.setattr(fly.sys, "platform", "darwin")
    monkeypatch.setattr(fly, "app_bundle", lambda: tmp_path / "OrthoStudio XP (vols de test).app")
    out = tmp_path / "2026-10-04 010203"
    out.mkdir()
    home = out / fly.LIVE_HOME
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    asked: list[bytes] = []

    def window_answers() -> None:
        conn, _ = listener.accept()
        with conn:
            asked.append(conn.recv(16))
            conn.sendall(b"ok\n")

    threading.Thread(target=window_answers, daemon=True).start()
    opened: list[list[str]] = []

    def run(cmd: list[str], **kw: object) -> object:
        opened.append(cmd)
        (home / "run").mkdir(parents=True, exist_ok=True)
        shown = {"pid": 4242, "port": listener.getsockname()[1]}
        (home / "run" / f"osxp-window-{fly.APP_PORT}.json").write_text(json.dumps(shown))
        return None

    monkeypatch.setattr(fly.subprocess, "run", run)
    states = iter(["idle", "starting", "streaming"])
    posted: list[tuple[str, dict]] = []

    def http(url: str, body: dict | None = None, timeout_s: float = 5.0) -> dict | None:
        if body is not None:
            posted.append((url, body))
            return {}
        if url.endswith("/api/status"):
            return {"active_job": None}
        if url.endswith("/api/engine"):
            return {"engine": {"pid": 4343}}
        if url.endswith("/api/live/status"):
            return {"state": next(states, "streaming"), "error": None}
        return None

    monkeypatch.setattr(fly, "_http", http)
    app = fly.start_app(
        tmp_path / "X-Plane 12", out, True, (16, 18), False, tmp_path / "eur", with_osxp=True
    )
    cmd = opened[0]
    assert cmd[:3] == ["open", "-n", "-a"]
    env = dict(cmd[i + 1].split("=", 1) for i, w in enumerate(cmd) if w == "--env")
    assert env["OSXP_START"] == "build" and env["OSXP_PORT"] == str(fly.APP_PORT)
    assert env["OSXP_HOME"] == str(home) and env["OSXP_LIVE_SOURCE"] == str(tmp_path / "eur")
    assert asked == [b"live\n"]  # Live's side, once OrthoStudio XP's engine answered
    assert posted == [(fly.LIVE_API + "start", {})]
    assert (app.window, app.engine_pid, app.home) == (4242, 4343, home)
    assert app.live_log == home / "live" / "LogLive.txt"
    assert app.log.name == f"serve-test-flight-{out.name.replace(' ', '-')}.log"


def test_the_app_quits_as_an_update_s_installer_quits_it(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(
        fly, "_http", lambda url, body=None, timeout_s=5.0: posted.append((url, body))
    )
    monkeypatch.setattr(fly, "_alive", lambda pid: len(posted) < 2)
    monkeypatch.setattr(fly, "stop_tree", lambda pid, timeout_s=0: pytest.fail("ended by force"))
    fly.stop_app(4242, timeout_s=5)
    assert posted == [
        (f"http://127.0.0.1:{fly.APP_PORT}/api/quit", {"force": True}),
        (fly.LIVE_API + "quit", {"force": True}),
    ]


def test_an_app_whose_live_cannot_start_is_closed_before_the_run_gives_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Left open, it would hold Live's port and its engine's, and no run after it could start."""
    import orthostudio.live as live

    monkeypatch.setattr(live, "settings_file", lambda: tmp_path / "none" / "live.toml")
    monkeypatch.setattr(fly, "POLL_S", 0.01)
    monkeypatch.setattr(fly, "_port_open", lambda port: False)  # no Live, no app of the user's
    monkeypatch.setattr(fly.sys, "platform", "darwin")
    monkeypatch.setattr(fly, "app_bundle", lambda: tmp_path / "OrthoStudio XP (vols de test).app")
    out = tmp_path / "2026-10-04 020304"
    out.mkdir()
    home = out / fly.LIVE_HOME

    def run(cmd: list[str], **kw: object) -> None:
        (home / "run").mkdir(parents=True, exist_ok=True)
        (home / "run" / f"osxp-window-{fly.APP_PORT}.json").write_text('{"pid": 4242, "port": 1}')

    monkeypatch.setattr(fly.subprocess, "run", run)
    calls = iter([{"state": "idle", "error": None}, {"state": "idle", "error": "no X-Plane"}])
    monkeypatch.setattr(
        fly,
        "_http",
        lambda url, body=None, timeout_s=5.0: (
            {}
            if body is not None
            else (
                next(calls, {"state": "idle", "error": "no X-Plane"})
                if "live/status" in url
                else None
            )
        ),
    )
    stopped: list[tuple[int, float]] = []
    monkeypatch.setattr(fly, "stop_app", lambda pid, timeout_s=0: stopped.append((pid, timeout_s)))
    with pytest.raises(fly.RefusedError, match="no X-Plane"):
        fly.start_app(tmp_path / "X-Plane 12", out, True)
    assert stopped == [(4242, 60.0)]


def test_the_app_is_not_opened_beside_a_live_of_the_user_s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user's own app or the preview with Live up: their Live would answer for the run's."""
    monkeypatch.setattr(fly, "_port_open", lambda port: port == fly.LIVE_PORT)
    monkeypatch.setattr(fly.subprocess, "run", lambda *a, **k: pytest.fail("the app opened"))
    with pytest.raises(fly.RefusedError, match="Live or the app runs already"):
        fly.start_app(tmp_path / "X-Plane 12", tmp_path, True)


def test_a_campaign_s_next_run_waits_for_the_mac_to_cool(monkeypatch: pytest.MonkeyPatch) -> None:
    import sampler

    monkeypatch.setattr(fly, "xplane_gone", lambda: True)
    monkeypatch.setattr(fly, "live_runs", lambda: False)
    monkeypatch.setattr(fly, "autoortho_runs", lambda: False)
    monkeypatch.setattr(fly, "_port_open", lambda port: False)
    monkeypatch.setattr(fly.time, "sleep", lambda s: None)
    readings = iter([2, 1, 0])  # heavy, moderate, nominal
    monkeypatch.setattr(sampler, "heat", lambda: next(readings, 0))
    fly.wait_quiet()
    assert next(readings, "done") == "done"  # read until nominal
    monkeypatch.setattr(sampler, "heat", lambda: None)  # Windows: no heat told, no wait
    fly.wait_quiet()


def test_a_cold_live_takes_the_regions_lists_and_leaves_its_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A region's list of textures, made as it installs, read in a second at a user's start: the
    cold home takes the user's, and the cold run starts as quickly (the Shadow read 400,000 files
    again, 2026-10-04). Its LogLive.txt stays with the series once its folder is deleted."""
    import orthostudio.live as live
    from orthostudio.live import aoregions
    from orthostudio.live import pack as pack_module

    mine = tmp_path / "mine"
    lists = aoregions.lists_dir(mine)
    lists.mkdir(parents=True)
    (lists / "z_ao_eur-0123456789ab.json").write_text('{"names": []}')
    monkeypatch.setattr(live, "settings_file", lambda: tmp_path / "none" / "live.toml")
    monkeypatch.setattr(pack_module, "live_root", lambda: mine)
    out = tmp_path / "series"
    home = fly.cold_home(out / fly.LIVE_HOME)
    kept = aoregions.lists_dir(home / "live") / "z_ao_eur-0123456789ab.json"
    assert kept.read_text() == '{"names": []}'
    (home / "live" / "LogLive.txt").write_text("LogLive.txt for OrthoStudio XP Live\nstarting\n")
    fly.clean_cold(out)
    assert not (home / "live").exists()  # the heavy folder gone
    assert (out / "LogLive.txt").read_text().endswith("starting\n")  # its log kept


@mac_only
def test_xpme_is_flown_as_the_user_starts_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """XPME (XPlane Map Enhancement) is started by hand, its service in its window: the runner
    refuses to fly without it, refuses a cold run whose cache holds pieces, says its imagery and
    resolution, and gives the sampler its app's main process, under which its service runs."""
    import sqlite3

    cache = tmp_path / ".xpme-cache"
    cache.mkdir()
    with sqlite3.connect(cache / ".xpme-cache-index.sqlite3") as db:  # as XPME makes it
        db.execute(
            "CREATE TABLE cache_entries (path TEXT PRIMARY KEY, kind INTEGER NOT NULL, "
            "size_bytes INTEGER NOT NULL, last_access_utc_ticks INTEGER NOT NULL)"
        )
    settings = {"CacheFolder": str(cache), "SelectedMapServer": "BingMaps", "ImageResolution": 0}
    monkeypatch.setattr(fly, "xpme_settings", lambda: settings)
    main = f"{fly.XPME_APP}/Contents/MacOS/XPlane Map Enhancement"
    rows = [(10, 1, 300.0, 1.0, main), (11, 10, 900.0, 5.0, "XplaneMapEnhancement")]
    monkeypatch.setattr(fly.sampler, "processes", lambda: rows)
    monkeypatch.setattr(fly, "xpme_mounted", lambda: False)
    with pytest.raises(fly.RefusedError, match="XPME's service is not started"):
        fly.check_xpme(cold=True)
    monkeypatch.setattr(fly, "xpme_mounted", lambda: True)
    said: list[str] = []
    monkeypatch.setattr(fly, "say", said.append)
    xpme = fly.check_xpme(cold=True)
    assert (xpme.pid, xpme.imagery, xpme.resolution) == (10, "BingMaps", "Normal (ZL16/18)")
    assert said == [f"XPME runs: BingMaps, Normal (ZL16/18), cache {cache}"]
    with sqlite3.connect(cache / ".xpme-cache-index.sqlite3") as db:  # a flight's pieces kept
        db.execute("INSERT INTO cache_entries VALUES ('16/1/1.jpg', 0, 20000, 0)")
    with pytest.raises(fly.RefusedError, match="XPME's cache holds 1 pieces"):
        fly.check_xpme(cold=True)
    assert fly.check_xpme(cold=False).pid == 10  # warm: its cache as it is


def test_xpme_s_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its resolution is chosen in its window: no --zooms for it."""
    import argparse

    def ns(**kw: object) -> argparse.Namespace:
        return argparse.Namespace(**{"streamer": "xpme", "cold": True, **kw})

    assert fly.refused_options(ns()) is None
    assert "--zooms is not for XPME" in fly.refused_options(ns(zooms=(16, 18)))
    assert "--imagery is for Live" in fly.refused_options(ns(imagery="Arc"))
    assert fly.label(ns(zooms=None, ao_settings="mine")) == "XPME, cold"
    assert "xpme" in fly.parser().parse_args(["--streamer", "xpme", "x"]).streamer


def test_the_flight_s_load_from_x_plane_s_own_log(tmp_path: Path) -> None:
    """The start as a pilot lives it: from X-Plane's loading of the flight (a click on Start
    Flight's moment) to its loading screen gone, on its own clock, which stands still while it
    loads; then that first image to X-Plane running, the runner's. The lines of the Live flight
    of 2026-10-09 over Toulouse: 55.6 s, then 4 s (the user's « les vrais temps »)."""
    lines = [
        "0:00:03.479 D/STM: Transitioning from state state_QuickFlightWizard to "
        "state_NormalLoading",
        "OrthoStudio XP Live: message 106 (0)",
        "0:00:03.479 D/STM: Transitioning from state state_NormalLoading to state_Normal",
        "0:00:03.479 D/DRM: Pro license status changed to 1",
        "OrthoStudio XP Test Pilot: scenario read: start 43.6300 1.3700, 6 legs, 6 cameras, "
        "5 screenshots",
        "0:00:59.128 D/DRM: Got 1 valid products.",
    ]
    assert report.flight_load_s(lines) == pytest.approx(55.649)
    assert report.flight_load_s(lines[:2]) is None  # still loading where the log ends
    folder = tmp_path / "1-toulouse-low"
    folder.mkdir()
    (folder / "Log.txt").write_text("\n".join(lines) + "\n")
    run = {"xplane_start": "started for the series", "image_wall": 100.0, "running_wall": 104.0}
    assert report.start_split(folder, run) == {"load_s": pytest.approx(55.649), "frozen_s": 4.0}
    assert report.start_split(folder, {**run, "xplane_start": "running already"}) == {}
