# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The test pilot of the replayable test flights (native/xplane_test_pilot/test_pilot.c), on every
system: the files built ahead for Windows and Linux, and a flight it flies in the stand-in for
X-Plane (tests/data/live/fake_xplm.c), whose clock runs as X-Plane's: the scenario read, the
aircraft placed, the path flown, the screenshot asked, the scenario set aside, X-Plane told to
quit. On a Mac and on Linux built here, with the plugin's own source; on Windows (and Linux
x86-64), the very file X-Plane loads, in the program that loads it as X-Plane does
(tests/data/live/xpl_host.c, built here or ahead: $OSXP_LIVE_XPL_HOST)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_live_gate import (
    ENTRY_POINTS,
    HERE,
    built_ahead_host,
    c_compiler,
    elf_symbols,
    pe_exports_and_imports,
)

sys.path.insert(0, str(HERE.parent / "tools" / "live" / "flights"))
import pilot_build

SCENARIO = "start 43.63 1.37 3000 320 230\nleg 2 0 0\nweather clear\nscreenshot 1\nend quit\n"


class PrebuiltPilotTest(unittest.TestCase):
    """The Windows and Linux test pilots, built ahead (tools/live/plugin_builds.py)."""

    def xpl(self, platform: str) -> bytes:
        return pilot_build.xpl(pilot_build.PREBUILT_DIR, platform).read_bytes()

    def test_built_from_these_sources(self):
        self.assertEqual(
            pilot_build.SOURCES.read_text().split()[0],
            pilot_build.sources_digest(),
            "the test pilot's sources changed since it was built for Windows and Linux: "
            "uv run python tools/live/plugin_builds.py",
        )

    def test_windows(self):
        machine, exports, imports = pe_exports_and_imports(self.xpl("win32"))
        self.assertEqual(machine, 0x8664)
        self.assertEqual(exports, ENTRY_POINTS)
        self.assertLessEqual(imports, {"xplm_64.dll", "kernel32.dll", "msvcrt.dll"})

    def test_linux(self):
        machine, defined, needed = elf_symbols(self.xpl("linux"))
        self.assertEqual(machine, 62)
        self.assertEqual(defined, ENTRY_POINTS)
        listed = {
            line.strip()
            for line in pilot_build.XPLM_DEF.read_text().splitlines()
            if line.strip().startswith("XPLM")
        }
        self.assertEqual({n for n in needed if n.startswith("XPLM")}, listed)


def pilot_program(tmp: Path) -> list[str] | None:
    """The command that runs the test pilot in the stand-in for X-Plane, here: the source built
    with this system's compiler, else the file built ahead in the program that loads it."""
    cc = c_compiler() if sys.platform != "win32" else None
    if cc is not None:
        exe = tmp / "pilot_harness"
        subprocess.run(
            [cc, "-O1", "-Wall", "-Werror", "-DNO_CLICK", "-o", str(exe),
             str(pilot_build.SOURCE), str(HERE / "data" / "live" / "fake_xplm.c"), "-lm"],
            check=True,
        )  # fmt: skip
        return [str(exe)]
    host = built_ahead_host(tmp)
    if host is None:
        return None
    return [str(host), str(pilot_build.xpl(pilot_build.PREBUILT_DIR))]


class PilotFlightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.program = pilot_program(Path(cls.tmp.name))
        if cls.program is None:
            cls.tmp.cleanup()
            raise unittest.SkipTest("no compiler, nor the stand-in built ahead")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def xplane(self, root: Path, *commands: str) -> str:
        out = subprocess.run(
            [*self.program, "0", "0", *commands],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            env={**os.environ, "FAKE_XPLANE": str(root)},
        )
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout

    def box_of(self, name: str) -> Path:
        box = Path(self.tmp.name) / name / "Output" / "osxp_test_pilot"
        box.mkdir(parents=True)
        return box

    def test_a_flight(self):
        """In a folder of X-Plane's whose name a Windows code page would lose: X-Plane gives it in
        UTF-8, and Windows' own calls take UTF-16."""
        box = self.box_of("X-Plane 12 é ø")
        (box / "scenario.txt").write_text(SCENARIO)
        (box / "scenario.done").write_text("the flight before")  # replaced, on Windows too
        said = self.xplane(box.parents[1], "frames:9000")
        events = (box / "events.txt").read_text()
        for line in (
            "scenario read: start 43.6300 1.3700, 1 legs, 0 cameras, 1 screenshots",
            "placed at 43.6300 1.3700, 3000 ft, heading 320, 230 kt",
            "flying the path: 1 legs, 2 s",
            "screenshot at 1.0 s",
            "the path flown (2 s)",
            "done",
            "quitting X-Plane",
        ):
            self.assertIn(line, events)
        self.assertIn("COMMAND sim/operation/screenshot", said)
        self.assertIn("COMMAND sim/operation/quit", said)
        self.assertEqual((box / "scenario.done").read_text(), SCENARIO)
        self.assertFalse((box / "scenario.txt").exists())
        frames = (box / "frames.csv").read_bytes()
        self.assertNotIn(b"\r\n", frames)  # the same file on every system
        rows = frames.decode().splitlines()
        self.assertEqual(rows[0].split(",")[:3], ["run_s", "path_s", "frame_ms"])
        self.assertGreater(len(rows), 50)

    def test_asked_to_stop(self):
        """A path ended where it is, as at its end: the runner asks once X-Plane has loaded what it
        waited for (fly.py --stop-after-loads)."""
        box = self.box_of("X-Plane stopping")
        (box / "scenario.txt").write_text(SCENARIO.replace("leg 2 ", "leg 600 "))
        (box / "stop").write_text("stop\n")
        self.xplane(box.parents[1], "frames:9000")
        events = (box / "events.txt").read_text()
        self.assertIn("stopping the path, as asked", events)
        self.assertIn("done", events)
        self.assertFalse((box / "stop").exists())

    def test_asked_to_quit(self):
        box = self.box_of("X-Plane quitting")
        (box / "quit").write_text("")
        said = self.xplane(box.parents[1], "frames:1500")
        self.assertIn("quitting X-Plane, as asked", said)
        self.assertIn("COMMAND sim/operation/quit", said)
        self.assertFalse((box / "quit").exists())


if __name__ == "__main__":
    unittest.main()
