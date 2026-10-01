# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A series of the replayable test flights flown by their runner (tools/live/flights/fly.py) from
its start to its end, X-Plane a stand-in that does what the runner and the test pilot ask of it
(tests/data/live/fake_xplane.c): the test pilot installed into X-Plane's plugins and loaded, X-Plane
started straight at the scenario's start, the path flown, the computer sampled, the files taken
into the flight's folder, the report written, X-Plane told to quit and gone. On Windows and on
Linux, on x86-64, the test pilot being the one built ahead for them
(native/xplane_test_pilot/prebuilt); a Mac's runner starts X-Plane.app, which no stand-in
replaces."""

import csv
import json
import os
import shutil
import statistics
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import pytest

from test_live_gate import HERE, HOST_DIR, c_compiler

sys.path.insert(0, str(HERE.parent / "tools" / "live" / "flights"))
import fly
import pilot_build

SCENARIO = """title a short one
start 43.63 1.37 3000 320 230
weather clear
camera 0 3 cockpit
camera 3 99 chase 150 30 180
leg 4 0 0
leg 2 3 0
screenshot 2
"""


def stand_in_xplane(root: Path, tmp: Path) -> bool:
    """The stand-in for X-Plane in ``root`` as X-Plane's own program there: X-Plane-x86_64, or
    X-Plane.exe with its fake XPLM_64.dll; built here, or on a Windows without a compiler the one
    built ahead ($OSXP_LIVE_XPL_HOST: fake_xplane.exe, tools/live/windows_kit.py). False where
    neither can be, or where the test pilot built ahead cannot run (not x86-64)."""
    data = HERE / "data" / "live"
    platform = sysconfig.get_platform()  # this Python's machine: x86-64 also when emulated
    if platform == "linux-x86_64":
        cc = c_compiler()
        if cc is None:
            return False
        subprocess.run(
            [cc, "-O1", "-Wall", "-Werror", "-rdynamic", "-DNO_MAIN",
             "-o", str(root / "X-Plane-x86_64"), str(data / "fake_xplane.c"),
             str(data / "fake_xplm.c"), "-ldl", "-lm"],
            check=True,
        )  # fmt: skip
        return True
    if platform != "win-amd64":
        return False
    given = Path(os.environ.get(HOST_DIR, "."))
    if os.environ.get(HOST_DIR) and (given / "fake_xplane.exe").is_file():
        shutil.copy2(given / "fake_xplane.exe", root / "X-Plane.exe")
        shutil.copy2(given / "XPLM_64.dll", root / "XPLM_64.dll")
        return True
    gcc = shutil.which("gcc")
    if gcc is None:
        return False
    implib = tmp / "libXPLM_64.a"
    subprocess.run(
        [gcc, "-shared", "-DNO_MAIN", "-o", str(root / "XPLM_64.dll"), str(data / "fake_xplm.c"),
         f"-Wl,--out-implib,{implib}"],
        check=True,
    )  # fmt: skip
    subprocess.run(
        [gcc, "-o", str(root / "X-Plane.exe"), str(data / "fake_xplane.c"), str(implib),
         "-lws2_32"],
        check=True,
    )  # fmt: skip
    return True


def gone_within(seconds: float) -> bool:
    from orthostudio.live.scenery import xplane_running

    began = time.time()
    while time.time() - began < seconds:
        if not xplane_running():
            return True
        time.sleep(0.5)
    return False


@pytest.mark.skipif(sys.platform == "darwin", reason="a Mac's runner starts X-Plane.app")
def test_a_series_flown_from_its_start_to_its_end(tmp_path: Path) -> None:
    from orthostudio.live.scenery import xplane_running

    xp = tmp_path / "X-Plane 12 é"  # a name a Windows code page would lose
    (xp / "Custom Scenery").mkdir(parents=True)
    if not stand_in_xplane(xp, tmp_path):
        pytest.skip("no compiler, nor the stand-in built ahead; or not x86-64")
    if xplane_running():
        pytest.skip("an X-Plane runs on this computer")
    scenario = tmp_path / "short.txt"
    scenario.write_text(SCENARIO, encoding="utf-8")
    results = tmp_path / "results"
    try:
        code = fly.main(["--xplane", str(xp), "--results", str(results), str(scenario)])
        assert gone_within(60), "X-Plane asked to quit, and still there"
    finally:
        if xplane_running():  # never left behind
            subprocess.run(
                ["taskkill", "/F", "/IM", "X-Plane.exe"]
                if sys.platform == "win32"
                else ["pkill", "-f", "X-Plane-x86_64"],
                capture_output=True,
            )
    assert code == 0
    assert pilot_build.xpl(xp / "Resources" / "plugins" / pilot_build.NAME).is_file()
    [series] = [p for p in results.iterdir() if p.is_dir()]
    folder = series / "1-short"
    run = json.loads((folder / "run.json").read_text())
    assert run["outcome"] == "done"
    assert run["xplane_start"].startswith("started for the series")
    events = (folder / "events.txt").read_text()
    for line in (
        "scenario read: start 43.6300 1.3700, 2 legs, 2 cameras, 1 screenshots",
        "already at the start: not placed again",  # X-Plane started there
        "flying the path: 2 legs, 6 s",
        "screenshot at 2.0 s",
        "the path flown (6 s)",
    ):
        assert line in events
    assert events.splitlines()[-1].endswith(" done")
    log = (folder / "Log.txt").read_text(encoding="utf-8")  # X-Plane's, from the scenario on
    assert "OrthoStudio XP Test Pilot: done" in log
    frames = list(csv.DictReader((folder / "frames.csv").open(newline="")))
    assert len(frames) > 100  # a frame every 20 ms
    flown = [f for f in frames if float(f["path_s"]) >= 0]
    assert float(flown[-1]["path_s"]) >= 5.5
    # the aircraft moved where the test pilot flew it: north-west, about 700 m in 6 s at 230 kt
    north = float(flown[-1]["lat"]) - float(flown[0]["lat"])
    west = float(flown[0]["lon"]) - float(flown[-1]["lon"])
    assert north > 0.003 and west > 0.002
    assert 10 < statistics.median(float(f["frame_ms"]) for f in frames) < 200  # 20 ms, loaded
    system = list(csv.DictReader((folder / "system.csv").open(newline="")))
    assert len(system) >= 2  # before the scenario, and at its end
    assert max(float(r["xplane_rss_mb"]) for r in system) > 0  # X-Plane found among the processes
    if sys.platform == "win32":  # the memory, as Windows tells it
        assert all(r["mem_free_pct"] for r in system)
    report = (folder / "report.md").read_text()
    assert "## Frames" in report and "OUTCOME" not in report  # said only of a flight not flown
    summary = (series / "summary.md").read_text()
    assert "## short: done" in summary
    # nothing of the flight left in the test pilot's folder, for the next
    left = {p.name for p in fly.box(xp).iterdir()}
    assert not left & {"scenario.txt", "scenario.done", "events.txt", "frames.csv", "quit"}
