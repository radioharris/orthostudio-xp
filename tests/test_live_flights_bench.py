# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The test flights' window (tools/live/flights/bench.py): what it offers, the runner's arguments
it makes of a choice, the flight under way, and the results it shows. X-Plane is never started:
a small script stands in for the runner."""

from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

FLIGHTS = Path(__file__).resolve().parents[1] / "tools" / "live" / "flights"
sys.path.insert(0, str(FLIGHTS))

import bench  # noqa: E402


def test_the_window_offers_every_scenario_and_campaign_with_its_title() -> None:
    opts = bench.options()
    names = {s["name"]: s for s in opts["scenarios"]}
    assert {"camera-check", "scandinavia-columns", "scandinavia-rows", "toulouse-low"} <= set(names)
    assert names["toulouse-low"]["title"].startswith("AutoOrtho contre Live")
    assert names["toulouse-low"]["minutes"] == 20.0
    assert "Toulouse" in names["toulouse-low"]["about"]
    camp = next(c for c in opts["campaigns"] if c["name"] == "ao-vs-live")
    labels = {r["id"]: r["label"] for r in camp["runs"]}
    assert labels["ao-default-1719"] == "AutoOrtho, ses réglages par défaut, ZL17/19, à froid"
    assert labels["live-1618"] == "Live, ZL16/18, à froid"
    assert labels["ao-mine-1618"] == "AutoOrtho, tes réglages, ZL16/18, à froid"
    assert camp["comparisons"][0] == {
        "index": 1,
        "runs": ["ao-default-1618", "live-1618"],
        "title": "AutoOrtho, ses réglages par défaut, ZL16/18 contre Live, ZL16/18",
    }
    assert len(camp["comparisons"]) == 4
    assert camp["run_minutes"] == 28.0  # its 20 min flight and a cold start
    assert camp["scenarios"] == ["toulouse-low"]
    assert isinstance(opts["autoortho"], bool)


def test_a_choice_becomes_the_runners_arguments() -> None:
    assert bench.fly_args({"scenarios": ["camera-check"]}) == [
        "--streamer",
        "none",
        "camera-check",
    ]
    assert bench.fly_args(
        {
            "scenarios": ["toulouse-low", "camera-check"],
            "streamer": "autoortho",
            "ao_settings": "default",
            "zooms": "17/19",
            "cold": True,
            "keep_open": True,
        }
    ) == [
        "--streamer",
        "autoortho",
        "--ao-settings",
        "default",
        "--zooms",
        "17/19",
        "--cold",
        "--keep-open",
        "toulouse-low",
        "camera-check",
    ]
    live = bench.fly_args({"scenarios": ["toulouse-low"], "streamer": "live", "ao_settings": "x"})
    assert live == ["--streamer", "live", "toulouse-low"]  # AutoOrtho's settings: not Live's
    assert bench.fly_args({"campaign": "ao-vs-live", "keep_open": True}) == [
        "--campaign",
        "ao-vs-live",
    ]
    every = bench.fly_args({"campaign": "ao-vs-live", "pick": [1, 2, 3, 4]})
    assert every == ["--campaign", "ao-vs-live"]
    some = bench.fly_args({"campaign": "ao-vs-live", "pick": [3, 1]})
    assert some == ["--campaign", "ao-vs-live", "--pick", "1,3"]


@pytest.mark.parametrize(
    ("choice", "why"),
    [
        ({"scenarios": []}, "au moins un scénario"),
        ({"scenarios": ["nowhere"]}, "pas de scénario"),
        ({"campaign": "nowhere"}, "pas de campagne"),
        ({"campaign": "ao-vs-live", "pick": []}, "au moins une comparaison"),
        ({"campaign": "ao-vs-live", "pick": [5]}, "de 1 à 4"),
        ({"scenarios": ["camera-check"], "streamer": "ao"}, "streamer inconnu"),
        ({"scenarios": ["camera-check"], "zooms": "17/19"}, "Live ou AutoOrtho"),
        ({"scenarios": ["camera-check"], "cold": True}, "Live ou AutoOrtho"),
        ({"scenarios": ["camera-check"], "streamer": "live", "zooms": "19/17"}, "zooms"),
    ],
)
def test_a_choice_the_runner_would_refuse(choice: dict, why: str) -> None:
    with pytest.raises(bench.BenchError, match=why):
        bench.fly_args(choice)


def test_where_the_runner_is() -> None:
    lines = [
        "15:00:00 campaign ao-vs-live, run 2 of 5: Live, ZL16/18, cold",
        "15:00:02 series of 1 in build/flights/x",
        "15:00:02 1/1 toulouse-low: 20.0 min of path",
        "15:00:40   pilot: flying the path: 6 legs, 1200 s",
        "15:01:40   flying: 61 of 1200 s",
    ]
    p = bench.progress(lines)
    assert p == {
        "campaign": "ao-vs-live",
        "run": 2,
        "runs": 5,
        "setup": "Live, ZL16/18, cold",
        "index": 1,
        "count": 1,
        "scenario": "toulouse-low",
        "path_s": 61.0,
        "duration_s": 1200.0,
    }
    assert bench.in_french(p["setup"]) == "Live, ZL16/18, à froid"
    assert bench.progress(lines[:3])["path_s"] == 0.0  # X-Plane starting


FAKE_RUNNER = textwrap.dedent(
    """
    import sys, time
    print("15:00:00 1/1 camera-check: 3.0 min of path", flush=True)
    print("15:00:30   flying: 12 of 180 s", flush=True)
    try:
        time.sleep(30)
    except KeyboardInterrupt:
        print("15:00:31 stopped", flush=True)
    """
)


def wait_for(check, timeout: float = 10.0) -> None:
    end = time.time() + timeout
    while not check():
        assert time.time() < end, "not in time"
        time.sleep(0.05)


def test_one_flight_at_a_time_found_again_and_stopped(tmp_path: Path) -> None:
    script = tmp_path / "fake_fly.py"
    script.write_text(FAKE_RUNNER)
    runner = bench.Runner(results=tmp_path / "results", command=[sys.executable, str(script)])
    cur = runner.start(["--streamer", "none", "camera-check"])
    assert cur["args"] == ["--streamer", "none", "camera-check"]
    wait_for(lambda: "flying" in "\n".join(runner.status()["lines"]))
    status = runner.status()
    assert status["running"]
    assert status["progress"]["path_s"] == 12.0 and status["progress"]["scenario"] == "camera-check"
    assert runner.status(since=status["next"])["lines"] == []
    with pytest.raises(bench.BenchError, match="déjà en cours"):
        runner.start(["camera-check"])
    again = bench.Runner(results=tmp_path / "results")  # a window opened again
    assert again.running()
    assert again.stop()
    wait_for(lambda: not runner.running())
    assert "15:00:31 stopped" in runner.status()["lines"]
    assert not again.stop()  # nothing to stop any more


def make_results(root: Path) -> None:
    series = root / "2026-09-29 142654"
    flight = series / "1-scandinavia-columns"
    (flight / "screenshots").mkdir(parents=True)
    (flight / "screenshots" / "b738 - 2026-09-29 14.29.32.png").write_bytes(b"png")
    (flight / "run.json").write_text(
        json.dumps({"scenario": "scandinavia-columns", "streamer": "none", "outcome": "done"})
    )
    (flight / "report.md").write_text("# scandinavia-columns\n\n- streamer: none\n  - sub <b>\n")
    (series / "summary.md").write_text(
        "# Series of 2026-09-29 15:10\n\n## scandinavia-columns: done\n"
    )
    (root / "2026-09-29 160000 ao-vs-live.md").write_text(
        "# ao-vs-live\n\n## toulouse-low\n\n| | AutoOrtho | Live |\n|---|---|---|\n"
        "| start | 5.9 min | 1.7 min |\n"
    )
    (root / "bench-logs").mkdir()
    (root / "notes.txt").write_text("not a result")


def test_the_results_newest_first(tmp_path: Path) -> None:
    make_results(tmp_path)
    items = bench.results_list(tmp_path)
    assert [i["kind"] for i in items] == ["campaign", "series"]
    assert items[0]["what"] == "campagne ao-vs-live"
    assert items[1] == {
        "path": "2026-09-29 142654",
        "kind": "series",
        "when": "2026-09-29 142654",
        "what": "scandinavia-columns",
        "with": "none",
        "outcome": "done",
    }
    view = bench.result_view("2026-09-29 142654", tmp_path)
    assert [p["title"] for p in view["parts"]] == ["Bilan", "1-scandinavia-columns"]
    assert view["parts"][1]["screenshots"] == [
        "results/2026-09-29 142654/1-scandinavia-columns/screenshots/b738 - 2026-09-29 14.29.32.png"
    ]
    assert "&lt;b&gt;" in view["parts"][1]["html"]  # the report's text, escaped
    table = bench.result_view("2026-09-29 160000 ao-vs-live.md", tmp_path)["parts"][0]["html"]
    assert "<th>AutoOrtho</th>" in table and "<td>5.9 min</td>" in table
    with pytest.raises(bench.BenchError):
        bench.result_view("../outside", tmp_path)


def test_the_reports_markdown() -> None:
    text = (
        "# Title\n\n## Frames\n- one **bold**\n  - a sub `code`\n\n```\n0.0 a < b\n```\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\nplain & text"
    )
    out = bench.markdown_html(text)
    assert "<h2>Title</h2>" in out and "<h3>Frames</h3>" in out
    assert "<li>one <strong>bold</strong></li>" in out
    assert '<li class="sub">a sub <code>code</code></li>' in out
    assert "<pre>0.0 a &lt; b</pre>" in out
    assert "<thead><tr><th>a</th><th>b</th></tr></thead>" in out and "<td>2</td>" in out
    assert "<p>plain &amp; text</p>" in out


def test_the_windows_api(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    make_results(tmp_path)
    runner = bench.Runner(results=tmp_path, command=[sys.executable, "-c", "pass"])
    client = TestClient(bench.create_app(runner, tmp_path), base_url="http://127.0.0.1:8661")
    assert client.get("/").status_code == 200
    assert client.get("/bench/bench.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/api/bench/status").json()["running"] is False
    refused = client.post("/api/bench/start", json={"scenarios": []})
    assert refused.status_code == 409 and "scénario" in refused.json()["detail"]
    assert [r["kind"] for r in client.get("/api/bench/results").json()] == ["campaign", "series"]
    shot = (
        "results/2026-09-29 142654/1-scandinavia-columns/screenshots/b738 - 2026-09-29 14.29.32.png"
    )
    assert client.get("/" + shot).content == b"png"
    assert client.get("/results/notes.txt").status_code == 200
    assert client.get("/results/../fly.py").status_code == 404
    assert client.get("/api/bench/result", params={"path": "../x"}).status_code == 404
    assert client.post("/api/bench/stop", json={}).json() == {"stopped": False}


def test_the_dock_apps_icon() -> None:
    pytest.importorskip("PIL")
    import bench_app

    img = bench_app.draw_bench_icon(64)
    assert img.size == (64, 64)
    assert img.getpixel((47, 47))[:3] != img.getpixel((16, 16))[:3]  # the badge in its corner


# ---- Live's map ---------------------------------------------------------------------------------


def write_track(path: Path, points: list[tuple[float, float]]) -> None:
    lines = ["run_s,path_s,frame_ms,lat,lon,ele_m,state,view_x,view_y,view_z,loading"]
    lines += [
        f"{i / 60:.4f},{i / 60:.2f},16.0,{la:.6f},{lo:.6f},914.4,2,0,0,0,0"
        for i, (la, lo) in enumerate(points)
    ]
    path.write_text("\n".join(lines) + "\n")


def test_the_aircraft_and_its_heading_from_the_last_frames(tmp_path: Path) -> None:
    import benchmap

    frames = tmp_path / "frames.csv"
    write_track(frames, [(43.6 + i * 1e-4, 1.4) for i in range(200)])  # due north
    at = benchmap.last_position(frames)
    assert at["lat"] == pytest.approx(43.6199) and at["lon"] == 1.4
    assert at["hdg"] == pytest.approx(0.0, abs=0.01)
    write_track(frames, [(43.6, 1.4 + i * 1e-4) for i in range(200)])  # due east
    assert benchmap.last_position(frames)["hdg"] == pytest.approx(90.0, abs=0.01)
    assert benchmap.last_position(tmp_path / "none.csv") is None
    assert len(benchmap.planned("toulouse-low")) <= benchmap.ROUTE_POINTS
    assert benchmap.planned("toulouse-low")[0] == [43.63, 1.37]


def make_live_series(root: Path, cold: bool) -> Path:
    series = root / "2026-09-29 173000"
    flight = series / "1-camera-check"
    flight.mkdir(parents=True)
    (series / "live.txt").write_text("Live started")
    (flight / "run.json").write_text(json.dumps({"scenario": "camera-check", "streamer": "live"}))
    write_track(flight / "frames.csv", [(43.63 + i * 1e-4, 1.37) for i in range(600)])
    record = {"textures": {"23904_32992_BI16.dds": -1, "23920_32992_BI16.dds": 3}, "seen": {}}
    if cold:
        pack = series / "live-home" / "live" / "pack" / "_live"
        pack.mkdir(parents=True)
        (pack / "ortho.json").write_text(json.dumps(record))
    else:
        (series / "live-built.json").write_text(json.dumps(record))
    return series


@pytest.mark.parametrize("cold", [True, False])
def test_live_s_map_of_a_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cold: bool
) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setattr(bench, "_xplane", lambda: None)
    series = make_live_series(tmp_path, cold)
    runner = bench.Runner(results=tmp_path, command=[sys.executable, "-c", "pass"])
    client = TestClient(bench.create_app(runner, tmp_path), base_url="http://127.0.0.1:8661")
    assert client.get("/live/livemap.js").status_code == 200  # Live's own map module
    src = {"src": series.name}
    state = client.get("/api/bench/map/state", params=src).json()
    assert state["live"] and state["record"] and state["status"]["state"] == "idle"
    assert state["route"][0] == [43.63, 1.37] and len(state["route"]) >= 2
    view = {"west": 1.0, "south": 43.0, "east": 2.0, "north": 44.5, **src}
    built = client.get("/api/bench/map/built", params=view).json()
    assert [t[4] for t in built["textures"]] == [17, 13]  # photos of ZL17 (8192 px) and ZL13
    again = client.get("/api/bench/map/built", params={**view, "known": built["version"]}).json()
    assert again["same"] is True
    under = client.get("/api/bench/map/texture", params={"lat": 43.7, "lon": 1.3, **src}).json()
    assert under["textures"][0]["px"] == 8192
    assert client.get("/api/bench/map/own", params=src).json() == []
    nothing = client.get("/api/bench/map/state", params={"src": "current"}).json()
    assert nothing == {"live": False}  # no flight under way
    no_live = client.get("/api/bench/map/built", params={**view, "src": "elsewhere"})
    assert no_live.status_code == 404


def test_live_s_map_while_live_flies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fly
    from fastapi.testclient import TestClient

    xp = tmp_path / "X-Plane 12"
    fly.box(xp).mkdir(parents=True)
    write_track(fly.box(xp) / "frames.csv", [(43.63, 1.37 + i * 1e-4) for i in range(120)])
    monkeypatch.setattr(bench, "_xplane", lambda: xp)
    results = tmp_path / "results"
    series = make_live_series(results, cold=True)
    script = tmp_path / "fake_fly.py"
    script.write_text(
        "import time\n"
        f"print('17:30:00 series of 1 in {series}', flush=True)\n"
        "print('17:30:00 1/1 camera-check: 3.0 min of path', flush=True)\n"
        "time.sleep(30)\n"
    )
    runner = bench.Runner(results=results, command=[sys.executable, str(script)])
    runner.start(["camera-check"])
    try:
        wait_for(lambda: runner.series() == series)
        client = TestClient(bench.create_app(runner, results), base_url="http://127.0.0.1:8661")
        state = client.get("/api/bench/map/state", params={"src": "current"}).json()
        assert state["live"] and state["status"]["state"] == "streaming"
        aircraft = state["status"]["stream"]["aircraft"]
        assert aircraft["lat"] == 43.63 and aircraft["hdg"] == pytest.approx(90.0, abs=0.01)
        assert state["route"][0] == [43.63, 1.37]  # the scenario's path, planned
        view = {"west": 1.0, "south": 43.0, "east": 2.0, "north": 44.5, "src": "current"}
        assert len(client.get("/api/bench/map/built", params=view).json()["textures"]) == 2
    finally:
        runner.stop()
