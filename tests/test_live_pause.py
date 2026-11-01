# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's pause of X-Plane at a new location chosen in flight (orthostudio.live.pause), with a
stand-in for X-Plane's process (``sleep``) and Freeflight.prf written as X-Plane writes it at the
click on Fly: in place, line by line, three times with the flight running, then with the new
start."""

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from orthostudio.live.pause import (
    Pauser,
    last_start,
    start_guard,
    start_key,
    start_name,
    stop_guard,
)
from orthostudio.live.pause_window import lines

OLD = {"runway_start": {"airport_id": "LSGG", "runway": "22"}}
NEW = {"ramp_start": {"airport_id": "LGAV", "ramp": "B17"}}


def prefs_lines(start: dict) -> list[str]:
    head = ["I", "1100 Version", "_default_to_ramps 0", "_airport X"]
    tail = [f"P _rwy_or_ramp K{i:03d} 0" for i in range(200)]
    return [*head, "_last_start " + json.dumps(start, separators=(",", ":")), *tail]


def write_like_xplane(path: Path, start: dict) -> None:
    """In place, truncated first, then a line at a time (what kqueue sees of X-Plane's writes)."""
    with open(path, "w", buffering=1) as f:
        for line in prefs_lines(start):
            f.write(line + "\n")
            f.flush()


def stopped(pid: int) -> bool:
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return out.stdout.strip().startswith("T")


@unittest.skipIf(sys.platform == "win32", "a stand-in process paused by SIGSTOP")
class PauserTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prefs = Path(self.tmp.name) / "Freeflight.prf"
        write_like_xplane(self.prefs, OLD)
        self.xplane = subprocess.Popen(["sleep", "60"])
        self.log = []
        self.pauser = Pauser(self.prefs, lambda: self.xplane.pid, self.log.append)

    def tearDown(self):
        self.pauser.stop()
        self.xplane.kill()
        self.xplane.wait()
        self.tmp.cleanup()

    def wait_for(self, test, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            if test():
                return True
            time.sleep(0.02)
        return False

    def test_a_new_location_in_flight_pauses_until_resumed(self):
        self.pauser.flying = True
        self.pauser.start()
        time.sleep(0.3)  # watching
        for _ in range(3):  # the flight running, three times
            write_like_xplane(self.prefs, OLD)
        self.assertIsNone(self.pauser.paused_for)
        self.assertFalse(stopped(self.xplane.pid))
        threading.Thread(target=write_like_xplane, args=(self.prefs, NEW), daemon=True).start()
        self.assertTrue(self.wait_for(lambda: self.pauser.paused_for is not None))
        self.assertEqual(start_key(self.pauser.paused_for), "LGAV")
        self.assertTrue(self.wait_for(lambda: stopped(self.xplane.pid)))
        said = next(m for m in self.log if m.startswith("X-Plane paused"))
        self.assertNotIn("(0 brief", said)  # every earlier line let go at once
        self.pauser.resume()
        self.assertTrue(self.wait_for(lambda: not stopped(self.xplane.pid)))
        self.assertIsNone(self.pauser.paused_for)

    def test_no_pause_on_the_ground_or_for_the_same_start(self):
        self.pauser.start()
        time.sleep(0.3)
        write_like_xplane(self.prefs, NEW)  # a load from the main menu: the plugin's
        time.sleep(0.3)
        self.assertIsNone(self.pauser.paused_for)
        self.pauser.flying = True
        write_like_xplane(self.prefs, NEW)  # a change of weather, X-Plane quitting
        time.sleep(0.3)
        self.assertIsNone(self.pauser.paused_for)
        self.assertFalse(stopped(self.xplane.pid))

    def test_stop_lets_xplane_go(self):
        self.pauser.flying = True
        self.pauser.start()
        time.sleep(0.3)
        write_like_xplane(self.prefs, NEW)
        self.assertTrue(self.wait_for(lambda: stopped(self.xplane.pid)))
        self.pauser.stop()
        self.assertTrue(self.wait_for(lambda: not stopped(self.xplane.pid)))


@unittest.skipIf(sys.platform == "win32", "a stand-in process paused by SIGSTOP")
class GuardTest(unittest.TestCase):
    def test_the_guard_lets_xplane_go_when_live_goes(self):
        """Live's end of the pipe closed (Live gone however it went): X-Plane goes on at once."""
        xplane = subprocess.Popen(["sleep", "60"])
        try:
            guard = start_guard(xplane.pid)
            time.sleep(0.5)  # the guard reading its pipe
            xplane.send_signal(subprocess.signal.SIGSTOP)
            self.assertTrue(stopped(xplane.pid))
            t0 = time.time()
            stop_guard(guard)
            while stopped(xplane.pid) and time.time() - t0 < 5:
                time.sleep(0.02)
            self.assertFalse(stopped(xplane.pid))
            self.assertLess(time.time() - t0, 2.0)
        finally:
            xplane.kill()
            xplane.wait()


class NamesTest(unittest.TestCase):
    def test_a_start_named_as_the_streamer_names_it(self):
        line = "_last_start " + json.dumps(NEW)
        self.assertEqual(start_key(line), "LGAV")
        self.assertEqual(start_name(line), "LGAV parking B17")
        air = "_last_start " + json.dumps({"lle_air_start": {"latitude": 46.1, "longitude": 6.25}})
        self.assertEqual(start_name(air), "46.100 6.250")
        self.assertIsNone(start_key("_last_start {broken"))

    def test_last_start_read_in_the_middle_of_a_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "Freeflight.prf"
            prefs.write_text("I\n1100 Version\n_airp")  # cut before the line
            self.assertIsNone(last_start(prefs))
            prefs.write_text("\n".join(prefs_lines(NEW)[:6]))
            self.assertEqual(start_key(last_start(prefs)), "LGAV")

    def test_the_window_says_the_plugins_lines(self):
        self.assertEqual(
            lines("LSGG piste 22", 1234, 3, 14.25, 7.4, True, "fr"),
            (
                "Chargement de l'ortho autour de LSGG piste 22",
                "1234 textures à construire  -  14,2 Mo/s  -  7 s",
            ),
        )
        self.assertEqual(
            lines("LSGG runway 22", 5, 5, 0.0, 9, True, "en")[1],
            "5 textures to build  -  every photo in, building  -  9 s",
        )
        self.assertEqual(
            lines("LSGG runway 22", 0, 0, 0.0, 2, False, "en")[1], "preparing the files  -  2 s"
        )


if __name__ == "__main__":
    unittest.main()
