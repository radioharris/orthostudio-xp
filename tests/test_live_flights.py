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
    (xp / "Log.txt").write_text("before the scenario\nits lines\n")
    shots = xp / "Output" / "screenshots"
    shots.mkdir(parents=True)
    (shots / "mine.png").write_bytes(b"the user's")
    before = fly.screenshots_now(xp)
    (shots / "b738_1.png").write_bytes(b"the flight's")
    (out / "live.txt").write_text("earlier flight\nthis flight\n")
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


def test_autoortho_with_the_users_settings_and_cache(user_home: Path, tmp_path: Path) -> None:
    home = fly.autoortho_home(tmp_path / "ao-home", "mine", cold=False, zooms=None)
    text = (home / ".autoortho").read_text()
    assert "max_zoom = 15\n" in text
    assert "cache_dir = /Users/someone/.autoortho-data/cache" in text  # warm: theirs
    assert fly.autoortho_folder(home) == Path("/Users/someone/.autoortho-data/cache")


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
    monkeypatch.setattr(sampler, "_run", lambda cmd: ioreg)
    assert sampler.disk_writes() == {"disk0": 179092750336, "disk4": 75561712640}
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


def test_a_cold_runs_heavy_files_go_its_record_stays(tmp_path: Path) -> None:
    """A cold run's own homes, under its series' folder: the heavy files deleted once its streamer
    stopped (a 2 min cold AutoOrtho run left 3.1 GB), Live's record kept for the map."""
    out = tmp_path / "build" / "flights" / "2026-09-29 170000"
    pack = out / "live-home" / "live" / "pack"
    (pack / "_live").mkdir(parents=True)
    (pack / "_live" / "ortho.json").write_text('{"textures": {"a": 0}}')
    (pack / "textures").mkdir()
    (pack / "textures" / "big.dds").write_bytes(b"x" * 1000)
    (out / "live-home" / "live.toml").write_text("enabled = true\n")
    cache = out / "ao-home" / ".autoortho-data" / "cache"
    cache.mkdir(parents=True)
    (cache / "chunk.jpg").write_bytes(b"x")
    logs = out / "ao-home" / ".autoortho-data" / "logs"
    logs.mkdir()
    (logs / "autoortho.log").write_text("log")
    xp = tmp_path / "X-Plane 12"
    scenery = xp / "Custom Scenery"
    scenery.mkdir(parents=True)
    (scenery / "z_osxp_live").symlink_to(pack)  # the test's Live left its link
    users = tmp_path / "users-pack"
    users.mkdir()
    (scenery / "y_osxp_live_overlays").symlink_to(users)  # not the test's: untouched
    fly.clean_cold(out, xp)
    assert (out / "live-built.json").read_text() == '{"textures": {"a": 0}}'
    assert not (out / "live-home" / "live").exists()
    assert not cache.exists() and (logs / "autoortho.log").exists()
    assert (out / "live-home" / "live.toml").exists()
    assert not (scenery / "z_osxp_live").is_symlink()
    assert (scenery / "y_osxp_live_overlays").is_symlink() and users.is_dir()


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
