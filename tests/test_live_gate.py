# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The start gate: X-Plane waits on its loading screen until the ortho around the start is built.

The plugin is compiled for real, with a stand-in for X-Plane (tests/fake_xplm.c), and talks to the
gate over UDP as it does in X-Plane.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from live_helpers import NAMES, FakeDownloader, Image, make_source
from orthostudio.live import gate as gate_module
from orthostudio.live import plugin
from orthostudio.live import streamer as streamer_module
from orthostudio.live.cache import ChunkCache
from orthostudio.live.gate import Gate, read_last_start, start_position
from orthostudio.live.geo import parse_texture_name
from orthostudio.live.region import Zooms, setup_region
from orthostudio.live.streamer import Fix, Streamer, run

HERE = Path(__file__).resolve().parent
CLANG = shutil.which("clang")
PLANE_LOADED, LIVERY_LOADED, FMOD_BANK_LOADED = 102, 108, 112


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_start(prefs: Path, start: dict) -> None:
    prefs.parent.mkdir(parents=True, exist_ok=True)
    prefs.write_text(
        f"I\n1100 Version\n_airport LFMN\n_last_start {json.dumps(start)}\n_time {{}}\n"
    )


class StartTest(unittest.TestCase):
    def test_what_xplane_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "Freeflight.prf"
            write_start(prefs, {"runway_start": {"airport_id": "LFML", "runway": "13L"}})
            start = read_last_start(prefs)
            self.assertEqual(start, {"runway_start": {"airport_id": "LFML", "runway": "13L"}})
            airports = {"LFML": (43.4367, 5.215)}.get
            self.assertEqual(start_position(start, airports), (43.4367, 5.215, "LFML runway 13L"))
            self.assertIsNone(read_last_start(Path(tmp) / "nothing.prf"))

    def test_kinds_of_start(self):
        airports = {"LFMN": (43.6654, 7.215)}.get
        for ramp, name in (
            ("A12", "LFMN parking A12"),
            ("44", "LFMN parking 44"),
            ("Gate  12", "LFMN Gate 12"),  # the airport's name says it already
            ("GA Parking", "LFMN GA Parking"),
            (" ", "LFMN"),
        ):
            self.assertEqual(
                start_position({"ramp_start": {"airport_id": "lfmn", "ramp": ramp}}, airports),
                (43.6654, 7.215, name),
            )
        self.assertEqual(
            start_position(
                {
                    "lle_air_start": {
                        "latitude": 44.5,
                        "longitude": 6.25,
                        "elevation_in_meters": 3000,
                    }
                },
                airports,
            ),
            (44.5, 6.25, "44.500 6.250"),
        )
        self.assertIsNone(start_position({"runway_start": {"airport_id": "ZZZZ"}}, airports))
        self.assertIsNone(start_position({"boat_start": {"boat_name": "carrier"}}, airports))


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.xplane = Path(self.tmp.name)
        self.prefs = self.xplane / "Output" / "preferences" / "Freeflight.prf"
        self.port = free_port()

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_click_is_a_new_start(self):
        write_start(self.prefs, {"runway_start": {"airport_id": "LFML", "runway": "13L"}})
        old = time.time() - 60
        os.utime(self.prefs, (old, old))
        gate = Gate(
            self.xplane, {"LFML": (43.4367, 5.215), "LFMN": (43.6654, 7.215)}.get, port=self.port
        )
        try:
            self.assertIsNone(gate.new_start())  # nothing new since the gate opened
            write_start(self.prefs, {"runway_start": {"airport_id": "LFMN", "runway": "04R"}})
            self.assertEqual(gate.new_start(), (43.6654, 7.215, "LFMN runway 04R"))
            self.assertIsNone(gate.new_start())  # seen once
        finally:
            gate.close()

    def test_requests_and_answers(self):
        gate = Gate(self.xplane, lambda icao: None, port=self.port)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as p:
                p.connect(("127.0.0.1", self.port))
                p.settimeout(2)
                p.send(b"LIVE1 LOAD 43.438210 5.212888")
                p.send(b"nonsense")
                p.send(b"LIVE1 HOLD 43.438210 5.212888 flight")
                p.send(b"LIVE1 HOLD 43.438210 5.212888 flight msl=9144 agl=9144")  # its heights
                p.send(b"LIVE1 LOAD 43.438210 5.212888 msl=40 agl=2")
                time.sleep(0.1)
                got = gate.requests()
                self.assertEqual(
                    [(k, lat, lon, why) for k, lat, lon, _, why in got],
                    [
                        ("LOAD", 43.43821, 5.212888, "aircraft"),
                        ("HOLD", 43.43821, 5.212888, "flight"),
                        ("HOLD", 43.43821, 5.212888, "flight msl=9144 agl=9144"),
                        ("LOAD", 43.43821, 5.212888, "aircraft msl=40 agl=2"),
                    ],
                )
                gate.answer(got[1][3], False, 12, "LFML runway 13L", 0.4567, 23.4, 15.18)
                self.assertEqual(
                    p.recv(100), b"LIVE1 WAIT n=12 p=457 s=23 mbs=15.2 lang=en at=LFML runway 13L"
                )
                gate.answer(got[1][3], False, 3, "LFPG Aérogare 2 機", lang="fr")
                self.assertTrue(p.recv(100).endswith("lang=fr at=LFPG Aérogare 2 機".encode()))
                gate.answer(got[1][3], False, 3, "LFBZ runway 09", lang="fr")
                self.assertTrue(p.recv(100).endswith(b"lang=fr at=LFBZ piste 09"))  # in French
                gate.answer(got[1][3], True, 0)
                self.assertEqual(p.recv(100), b"LIVE1 GO 0")
        finally:
            gate.close()

    def test_the_gate_answers_for_a_busy_loop(self):
        """The plugin lets X-Plane go when a HOLD gets no answer within a second. While the loop
        is late reading the plugin's lines, the gate answers for it with the loop's last WAIT,
        and keeps the lines for the loop (LGAV and LCLK, 2026-09-29)."""
        gate = Gate(self.xplane, lambda icao: None, port=self.port)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as p:
                p.connect(("127.0.0.1", self.port))
                p.settimeout(1.0)  # the plugin's second
                p.send(b"LIVE1 HOLD 37.936700 23.944500 flight")
                self.assertEqual(p.recv(100), b"LIVE1 WAIT n=0 p=0 s=0 mbs=0.0")
                gate.hold_at("LGAV parking B17", "fr")  # the start the loop plans next
                p.send(b"LIVE1 HOLD 37.936700 23.944500 flight")
                self.assertTrue(p.recv(100).endswith(b"lang=fr at=LGAV parking B17"))
                got = gate.requests()  # the loop back: both kept for it
                self.assertEqual([k for k, *_ in got], ["HOLD", "HOLD"])
                self.assertEqual(len(gate.stood_in()), 2)
                self.assertEqual(gate.stood_in(), [])
                gate.answer(got[0][3], False, 1565, "LGAV parking B17", 0.1, 5.0, 12.0)
                self.assertTrue(p.recv(100).startswith(b"LIVE1 WAIT n=1565 "))
                p.send(b"LIVE1 HOLD 37.936700 23.944500 flight")
                p.settimeout(0.3)
                with self.assertRaises(TimeoutError):  # the loop answers, it has just read
                    p.recv(100)
                p.settimeout(1.0)
                self.assertTrue(p.recv(100).startswith(b"LIVE1 WAIT n=1565 "))  # its last WAIT
                self.assertEqual(len(gate.requests()), 1)
                with mock.patch.object(gate_module, "ALIVE_S", 0.8):  # a loop gone: no answer
                    time.sleep(0.9)
                    p.send(b"LIVE1 HOLD 37.936700 23.944500 flight")
                    with self.assertRaises(TimeoutError):
                        p.recv(100)
        finally:
            gate.close()

    def test_a_notice_asked_in_flight(self):
        """In flight, the plugin asks every second whether the streamer has a notice for it: none,
        or no mesh under the aircraft, in the window's language. A reload is no longer offered."""
        gate = Gate(self.xplane, lambda icao: None, port=self.port)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as p:
                p.connect(("127.0.0.1", self.port))
                p.settimeout(2)
                p.send(b"LIVE1 ASK max=150 n=40")  # the longest frame of the second, 150 ms
                p.send(b"LIVE1 RELOAD")  # an old plugin's button: nothing now
                time.sleep(0.1)
                got = gate.requests()
                self.assertEqual([k for k, *_ in got], ["ASK"])
                self.assertEqual(got[0][4], "max=150 n=40")
                self.assertEqual(streamer_module.longest_frame(got[0][4]), 150)
                self.assertIsNone(streamer_module.longest_frame(""))  # an older plugin
                addr = got[0][3]
                gate.note(addr)
                self.assertEqual(p.recv(100), b"LIVE1 NONE lang=en")
                notice = {"id": 12, "region": "na", "installed": False}
                note = "Live : pas de mesh ici, installez Amérique du Nord dans la fenêtre de Live"
                gate.note(addr, note, notice, lang="fr")
                self.assertEqual(p.recv(200), f"LIVE1 NOTE 12 lang=fr {note}".encode())
        finally:
            gate.close()


@unittest.skipIf(CLANG is None, "needs clang")
class PluginTest(unittest.TestCase):
    """native/xplane_plugin/live_gate.c itself, driven by a fake X-Plane
    (tests/data/live/fake_xplm.c)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = free_port()
        cls.exe = Path(cls.tmp.name) / "plugin_harness"
        subprocess.run(
            [
                CLANG,
                "-O1",
                "-Wall",
                "-Werror",
                "-fobjc-arc",
                f"-DLIVE_PORT={cls.port}",
                "-DSILENT_S=1.0",
                "-DALONE_S=0.05",
                "-o",
                str(cls.exe),
                str(plugin.SOURCE),
                str(plugin.PANEL),
                str(HERE / "data" / "live" / "fake_xplm.c"),
                "-framework",
                "Cocoa",
                "-framework",
                "QuartzCore",
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def xplane(self, lat, lon, *messages, root=None):
        """Run the fake X-Plane: {message: seconds it held X-Plane}, and what the plugin logged;
        ``root``: its folder, which the plugin may look into as it starts."""
        env = {**os.environ, "FAKE_XPLANE": str(root) if root else ""}
        out = subprocess.run(
            [str(self.exe), str(lat), str(lon), *messages],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        took, logged, traced = {}, [], []
        for line in out.stdout.splitlines():
            if line.startswith("MSG "):
                _, msg, param, secs = line.split()
                took[f"{msg}:{param}"] = float(secs)
            elif line.startswith("LOG OrthoStudio XP Live: message "):
                traced.append(line[len("LOG OrthoStudio XP Live: message ") :])
            elif line.startswith("LOG "):
                logged.append(line[4:])
        self.traced = traced
        return took, logged

    def test_no_streamer_no_wait(self):
        took, logged = self.xplane(43.4382, 5.2129, "102:0", "112:1", "108:0", "115:1")
        self.assertEqual(set(took), {"102:0", "112:1", "108:0", "115:1"})
        self.assertLess(max(took.values()), 0.5)
        self.assertEqual(logged, [])
        self.assertEqual(
            self.traced, ["102 (0)", "112 (1)", "108 (0)", "115 (1)"]
        )  # every message, in Log.txt

    def fake_xplane_with_live_s_links(self) -> Path:
        root = Path(self.tmp.name) / f"X-Plane {time.monotonic_ns()}"
        scenery = root / "Custom Scenery"
        (root / "pack").mkdir(parents=True)
        (scenery / "someone's own").mkdir(parents=True)
        (scenery / "z_osxp_live").symlink_to(root / "pack")
        (scenery / "y_osxp_live_overlays").symlink_to(root / "pack")
        return root

    def test_a_pack_left_behind_is_taken_out(self):
        """Live stopped without taking its scenery out (a crash, a restart): X-Plane starting
        without it must not read a pack whose files Live makes only while it runs. X-Plane starts
        its plugins before it lists its scenery."""
        root = self.fake_xplane_with_live_s_links()
        _, logged = self.xplane(43.4382, 5.2129, root=root)
        scenery = root / "Custom Scenery"
        self.assertFalse(os.path.lexists(scenery / "z_osxp_live"))
        self.assertFalse(os.path.lexists(scenery / "y_osxp_live_overlays"))
        self.assertTrue((scenery / "someone's own").is_dir())  # never a folder of the user's
        self.assertTrue((root / "pack").is_dir())  # the link went, not what it led to
        self.assertTrue(any("Live is not running" in line for line in logged), logged)

    def test_live_s_scenery_stays_while_live_runs(self):
        root = self.fake_xplane_with_live_s_links()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", self.port))  # Live listens, answering nothing yet
            _, logged = self.xplane(43.4382, 5.2129, root=root)
        self.assertTrue((root / "Custom Scenery" / "z_osxp_live").is_symlink())
        self.assertFalse(any("Live is not running" in line for line in logged))

    def test_a_new_flight_with_the_same_aircraft(self):
        """No aircraft loads, so no PLANE_LOADED, FMOD_BANK_LOADED or LIVERY_LOADED: the plugin
        waits at message 115 (1) instead, but only once a first flight has loaded."""
        holds = []

        def streamer(sock):
            while True:
                try:
                    data, addr = sock.recvfrom(100)
                except OSError:
                    return
                if data.startswith(b"LIVE1 HOLD"):
                    holds.append(time.time())
                    sock.sendto(b"LIVE1 WAIT 3" if len(holds) < 3 else b"LIVE1 GO 0", addr)

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", self.port))
            threading.Thread(target=streamer, args=(sock,), daemon=True).start()
            took, _ = self.xplane(
                46.2381, 6.1089, "115:1"
            )  # at the first flight: the aircraft messages come next
            self.assertLess(took["115:1"], 0.3)
            self.assertEqual(holds, [])
            took, logged = self.xplane(46.2381, 6.1089, "102:0", "115:0", "115:1")
            sock.close()
        self.assertGreater(took["115:1"], 0.4)  # held until the third answer
        self.assertLess(took["115:0"], 0.3)  # leaving a flight for the menus: nothing to wait for
        self.assertIn("waits at the start of a new flight", logged[0])

    def test_waits_until_the_streamer_says_go(self):
        heard = []

        def streamer(sock):
            waits = 3
            while True:
                try:
                    data, addr = sock.recvfrom(100)
                except OSError:
                    return
                heard.append(data.decode())
                if data.startswith(b"LIVE1 HOLD"):
                    sock.sendto(
                        b"LIVE1 WAIT n=7 p=350 s=12 mbs=5.2 at=LFMN runway 04R"
                        if waits
                        else b"LIVE1 GO 0",
                        addr,
                    )
                    waits = max(0, waits - 1)

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", self.port))
            threading.Thread(target=streamer, args=(sock,), daemon=True).start()
            took, logged = self.xplane(43.4382, 5.2129, "102:0", "112:0", "112:1", "108:1", "108:0")
            sock.close()
        heard = [h for h in heard if h != "LIVE1 HELLO"]  # the plugin's start: is Live there?
        # PLANE_LOADED tells where, and how high: the stand-in's aircraft at FL300, X-Plane with
        # no ground under it yet (its height above the ground its altitude)
        self.assertEqual(heard[0], "LIVE1 LOAD 43.438200 5.212900 msl=9144 agl=9144")
        self.assertTrue(
            all(h == "LIVE1 HOLD 43.438200 5.212900 aircraft msl=9144 agl=9144" for h in heard[1:])
        )
        self.assertGreater(took["112:1"], 0.6)  # three waits of 0.25 s before the go
        self.assertLess(took["112:1"], 3.0)
        for other in (
            "102:0",
            "112:0",
            "108:1",
            "108:0",
        ):  # not held: the aircraft's own sounds, an AI livery, already go
            self.assertLess(took[other], 0.3, other)
        self.assertIn(
            "waits at the radio sounds until the ortho around LFMN runway 04R is built", logged[0]
        )
        self.assertIn("7 textures to go", logged[0])
        self.assertIn("X-Plane goes on", logged[1])

    def test_a_streamer_that_stops_answering(self):
        def streamer(sock):
            sock.recvfrom(100)  # LOAD
            _, addr = sock.recvfrom(100)
            sock.sendto(b"LIVE1 WAIT 50", addr)  # then silence

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", self.port))
            threading.Thread(target=streamer, args=(sock,), daemon=True).start()
            took, logged = self.xplane(45.0, 6.0, "102:0", "108:0")
        self.assertGreater(took["108:0"], 0.9)  # SILENT_S, 1 s in this build
        self.assertLess(took["108:0"], 3.0)
        self.assertIn("stopped answering", logged[-1])

    def test_a_streamer_that_stops_while_xplane_waits(self):
        """Its port closes (it stopped on an error, or Ctrl-C): X-Plane goes on at once, and
        Log.txt says why."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", self.port))

        def streamer():
            sock.recvfrom(100)  # LOAD
            _, addr = sock.recvfrom(100)
            sock.sendto(b"LIVE1 WAIT 50", addr)
            sock.close()  # then gone

        threading.Thread(target=streamer, daemon=True).start()
        try:
            took, logged = self.xplane(45.0, 6.0, "102:0", "108:0")
        finally:
            sock.close()
        self.assertLess(took["108:0"], 0.9)  # not SILENT_S later
        self.assertIn("the streamer stopped: X-Plane goes on", logged[-1])

    def test_a_notice_where_there_is_no_mesh(self):
        """The streamer's notice shows in a small panel, said once in Log.txt; its cross closes it
        for good, and the plugin keeps asking."""
        heard = []
        note = b"LIVE1 NOTE 9 Live: no mesh here: install North America in the Live window"

        def streamer(sock):
            while True:
                try:
                    data, addr = sock.recvfrom(100)
                except OSError:
                    return
                heard.append(data.decode())
                if data.startswith(b"LIVE1 ASK"):
                    sock.sendto(note, addr)

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", self.port))
            threading.Thread(target=streamer, args=(sock,), daemon=True).start()
            _, logged = self.xplane(
                40.64,
                -73.78,
                "102:0",
                "loop",
                "sleep:150",
                "loop",
                "sleep:150",
                "loop",
                "click",
                "loop",
                "sleep:150",
                "loop",
            )
            sock.close()
        asks = [line for line in heard if line.startswith("LIVE1 ASK")]
        self.assertGreaterEqual(len(asks), 3)
        # each ask says the second's frames, the longest in ms: what Live logs as a stutter
        self.assertTrue(all(re.fullmatch(r"LIVE1 ASK max=\d+ n=\d+", a) for a in asks), asks)
        said = [line for line in logged if "no mesh here" in line]
        self.assertEqual(
            said,
            ["OrthoStudio XP Live: Live: no mesh here: install North America in the Live window"],
        )

    def test_no_position_while_xplane_starts(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", self.port))
            sock.settimeout(0.3)
            took, _ = self.xplane(0.0, 0.0, "102:0", "112:1")
            got = []
            while True:
                try:
                    got.append(sock.recvfrom(100)[0])
                except TimeoutError:
                    break
        # nothing but the start's question whether Live is there
        self.assertEqual([g for g in got if g != b"LIVE1 HELLO"], [])
        self.assertLess(max(took.values()), 0.3)


@unittest.skipIf(Image is None, "needs numpy, Pillow and ispc_texcomp")
class FlightStartTest(unittest.TestCase):
    """run() from a start into the flight, at the package's zoom: X-Plane goes on at 4096 px, and
    in flight the plugin's ask gets nothing to show; no reload of the scenery, which X-Plane would
    not read the textures again for (the "reload" start mode went, 2026-09-27)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        pack = root / "stream_pack"
        source = make_source(root, tile=(45, 6), textures=NAMES)
        setup_region(source, pack, log=lambda _: None, zooms=Zooms(16, 18))
        self.cache = ChunkCache(root / "c.sqlite")
        self.port = free_port()
        self.gate = Gate(root / "X-Plane 12", lambda icao: None, port=self.port)
        self.xp = ScriptedXPlane()
        self.log = []
        self.streamer = Streamer(
            pack,
            self.cache,
            FakeDownloader,
            finest_quality=0,
            workers=2,
            xplane=self.xp,
            log=self.log.append,
        )
        self.stop = threading.Event()
        self.lat, self.lon = parse_texture_name(f"{NAMES[0]}.dds").center
        patch = mock.patch.object(streamer_module, "PLAN_EVERY_S", 0.2)
        patch.start()
        self.addCleanup(patch.stop)
        self.thread = threading.Thread(
            target=run,
            daemon=True,
            kwargs=dict(
                streamer=self.streamer,
                xp=self.xp,
                gate=self.gate,
                should_stop=self.stop.is_set,
                log=self.log.append,
            ),
        )
        self.thread.start()

    def tearDown(self):
        self.stop.set()
        self.thread.join(30)
        self.gate.close()
        self.cache.close()
        self.tmp.cleanup()

    def ask(self, p, line: bytes) -> str:
        """The streamer's answer, asked again as the plugin would if it comes late (a machine
        busy with the other tests)."""
        end = time.time() + 30
        while True:
            p.send(line)
            try:
                return p.recv(100).decode()
            except TimeoutError:
                self.assertLess(time.time(), end, f"no answer to {line!r}")

    def test_from_the_start_into_the_flight(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as p:
            p.connect(("127.0.0.1", self.port))
            p.settimeout(2)
            end = time.time() + 60
            hold = f"LIVE1 HOLD {self.lat:.6f} {self.lon:.6f} flight".encode()
            while not self.ask(p, hold).startswith("LIVE1 GO"):
                self.assertLess(time.time(), end)
                time.sleep(0.1)
            self.assertEqual(set(self.streamer.built.values()), {0})  # 4096 px around
            self.assertEqual(self.ask(p, b"LIVE1 ASK"), "LIVE1 NONE lang=en")
            self.xp.position = (self.lat, self.lon)  # the flight runs
            time.sleep(0.6)  # a few plans
            self.assertEqual(self.ask(p, b"LIVE1 ASK"), "LIVE1 NONE lang=en")
            self.assertEqual(set(self.streamer.built.values()), {0})  # read at 4096 px: kept
            self.assertEqual(self.xp.commands, [])  # never a reload


@unittest.skipIf(CLANG is None, "needs clang")
class BuildTest(unittest.TestCase):
    def test_build_install_uninstall(self):
        """The real build, against a stand-in XPLM framework in a stand-in X-Plane."""
        with tempfile.TemporaryDirectory() as tmp:
            xplane = Path(tmp) / "X-Plane 12"
            fw = xplane / "Resources" / "plugins" / "XPLM.framework"
            fw.mkdir(parents=True)
            subprocess.run(
                [
                    CLANG,
                    "-dynamiclib",
                    "-DNO_MAIN",
                    "-arch",
                    "arm64",
                    "-arch",
                    "x86_64",
                    "-install_name",
                    "@executable_path/../../../Resources/plugins/XPLM.framework/XPLM",
                    "-o",
                    str(fw / "XPLM"),
                    str(HERE / "data" / "live" / "fake_xplm.c"),
                ],
                check=True,
            )
            with mock.patch.object(plugin, "BUILD_DIR", Path(tmp) / "build"):
                self.assertTrue(plugin.install(xplane, log=self.fail))
                xpl = (
                    xplane
                    / "Resources"
                    / "plugins"
                    / "OrthoStudio XP Live"
                    / "mac_x64"
                    / "OrthoStudio XP Live.xpl"
                )
                self.assertTrue(plugin.installed(xplane))
                arch = subprocess.run(
                    ["lipo", "-archs", str(xpl)], capture_output=True, text=True
                ).stdout.split()
                self.assertEqual(sorted(arch), ["arm64", "x86_64"])
                symbols = subprocess.run(
                    ["nm", "-gU", str(xpl)], capture_output=True, text=True
                ).stdout
                for name in (
                    "XPluginStart",
                    "XPluginEnable",
                    "XPluginReceiveMessage",
                    "XPluginDisable",
                    "XPluginStop",
                ):
                    self.assertIn(f"_{name}", symbols)
                plugin.uninstall(xplane)
                self.assertFalse(
                    (xplane / "Resources" / "plugins" / "OrthoStudio XP Live").exists()
                )
                self.assertTrue(fw.exists())

    def test_no_compiler(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(plugin, "BUILD_DIR", Path(tmp) / "build"),
        ):
            said = []
            with mock.patch.object(
                plugin.subprocess, "run", side_effect=FileNotFoundError("clang")
            ):
                self.assertFalse(plugin.install(Path(tmp), log=said.append))
            self.assertIn("will not wait", said[0])
            self.assertFalse(plugin.installed(Path(tmp)))


class EncodersTest(unittest.TestCase):
    def test_in_flight_the_encoders_leave_xplane_its_cores(self):
        """All the workers while X-Plane waits on its loading screen; four while it flies."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            cache = ChunkCache(root / "c.sqlite")
            s = Streamer(
                pack, cache, lambda on_piece: None, finest_quality=0, workers=12, log=lambda _: None
            )
            self.assertEqual(s.encode_cap(), 12)  # waiting: all of them
            s.flying = True
            self.assertEqual(s.encode_cap(), streamer_module.FLIGHT_ENCODES)
            cache.close()

    def test_a_smaller_computer_s_share(self):
        """A computer of 8 cores and 16 GB (orthostudio.live.machine): its six workers all at work
        while X-Plane waits, two while it flies, one 8192 px texture at a time."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            cache = ChunkCache(root / "c.sqlite")
            s = Streamer(
                pack,
                cache,
                lambda on_piece: None,
                finest_quality=0,
                workers=6,
                log=lambda _: None,
                flight_encodes=2,
                sharp_builds=1,
            )
            self.assertEqual(s.encode_cap(), 6)
            s.flying = True
            self.assertEqual(s.encode_cap(), 2)
            self.assertEqual(s.sharp_builds, 1)
            cache.close()

    def test_short_of_memory_in_flight_live_holds_back(self):
        """The system's word on its memory (machine.memory_pressure), in flight only: short of it,
        one texture at a time and no 8192 px one ahead of time; very short, only what X-Plane
        reads now. On a simulated 16 GB computer Live's bursts stalled X-Plane (2026-09-28)."""
        from orthostudio.live import machine

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            cache = ChunkCache(root / "c.sqlite")
            level = [machine.SHORT]
            said: list[str] = []
            s = Streamer(
                pack,
                cache,
                lambda on_piece: None,
                finest_quality=0,
                workers=6,
                log=said.append,
                flight_encodes=2,
                memory_pressure=lambda: level[0],
            )
            # waiting on a start: nothing drawn to stutter, all the workers
            self.assertEqual(s.encode_cap(), 6)
            self.assertEqual(s.memory_state(), machine.NORMAL)
            s.flying = True
            self.assertEqual(s.encode_cap(), 1)  # short of memory: one at a time
            self.assertIn("short of memory", said[-1])
            self.assertTrue(s.may_encode(5.0, 0))  # ahead of time, 4096 px: yes
            self.assertFalse(s.may_encode(5.0, -1))  # ahead of time, 8192 px: not now
            self.assertTrue(s.may_encode(-1.0, -1))  # X-Plane reads it: always
            level[0] = machine.VERY_SHORT
            s.memory_state(now=time.monotonic() + 10)
            self.assertFalse(s.may_encode(5.0, 0))  # only what X-Plane reads now
            self.assertTrue(s.may_encode(0.0, 0))
            level[0] = machine.NORMAL
            s.memory_state(now=time.monotonic() + 20)
            self.assertEqual(s.encode_cap(), 2)
            self.assertTrue(s.may_encode(5.0, -1))
            self.assertIn("memory back", said[-1])
            cache.close()


class ReachTest(unittest.TestCase):
    def test_external_views_widen_the_sharp_rings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            cache = ChunkCache(root / "c.sqlite")
            lat, lon = parse_texture_name(f"{NAMES[0]}.dds").center
            away = Fix(lat - 0.3, lon)  # 33 km south of the texture
            name = f"{NAMES[0]}.dds"
            cockpit = Streamer(
                pack, cache, lambda on_piece: None, finest_quality=0, log=lambda _: None
            )
            self.assertEqual(cockpit.plan(away, x_plane_there=False)[name][1], 2)  # the 38 km ring
            views = Streamer(
                pack,
                cache,
                lambda on_piece: None,
                finest_quality=0,
                reach_km=25.0,
                log=lambda _: None,
            )
            urgency, level = views.plan(away, x_plane_there=False)[name]
            self.assertEqual(
                level, 0
            )  # 33 - 25 = 8 km from where the camera may be: the 12 km ring
            self.assertLess(urgency, 0)  # the camera may be there now
            far = Fix(
                lat - 2.3, lon
            )  # 255 km: only the 256 px level, which the camera does not change
            self.assertEqual(views.plan(far, x_plane_there=False)[name][1], 4)
            cache.close()

    def test_the_reach_even_in_the_cockpit(self):
        """The camera's reach widens the rings in the cockpit too: an external view comes without
        warning, a zoom takes the camera out faster than any build, and X-Plane never reads a level
        again. With the reach only in external views (2026-09-29), a quick zoom out would have
        found the 4096 px levels 15 to 35 km out still the neutral colour."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            cache = ChunkCache(root / "c.sqlite")
            s = Streamer(
                pack,
                cache,
                lambda on_piece: None,
                finest_quality=0,
                reach_km=20.0,
                log=lambda _: None,
            )
            lat, lon = parse_texture_name(f"{NAMES[0]}.dds").center
            name = f"{NAMES[0]}.dds"
            cockpit = Fix(lat - 0.25, lon, cam_lat=lat - 0.2499, cam_lon=lon)  # 28 km, the head
            self.assertEqual(s.plan(cockpit, x_plane_there=False)[name][1], 0)  # 4096 px already
            zoomed = Fix(lat - 0.25, lon, cam_lat=lat - 0.1, cam_lon=lon)  # 17 km out, low
            self.assertEqual(s.plan(zoomed, x_plane_there=False)[name][1], 0)  # what it reads
            cache.close()

    def test_a_camera_ahead_of_live_is_logged(self):
        """An external view's camera that reaches textures before Live built the level X-Plane
        reads there: the neutral colour on the screen, so the log says it (the reach's test,
        2026-09-29). In the cockpit, or while X-Plane waits on a start, nothing is said."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            cache = ChunkCache(root / "c.sqlite")
            said: list[str] = []
            s = Streamer(
                pack,
                cache,
                lambda on_piece: None,
                finest_quality=0,
                reach_km=10.0,
                log=said.append,
            )
            lat, lon = parse_texture_name(f"{NAMES[0]}.dds").center
            name = f"{NAMES[0]}.dds"
            cockpit = Fix(lat - 0.25, lon, cam_lat=lat - 0.2499, cam_lon=lon)
            s.plan(cockpit)  # the first fix: a jump, X-Plane there before anything was built
            self.assertTrue(s.read_early)
            s.plan(Fix(lat - 0.25, lon, cam_lat=lat - 0.02, cam_lon=lon))
            s.read_early = False  # everything built since
            s.plan(Fix(lat - 0.25, lon, cam_lat=lat - 0.02, cam_lon=lon), starting=True)
            s.plan(cockpit)
            self.assertEqual((s.outran, said), ({}, []))
            zoomed = Fix(lat - 0.25, lon, cam_lat=lat - 0.02, cam_lon=lon)  # 26 km out, low
            s.plan(zoomed)
            self.assertEqual(s.outran[name], 0)  # X-Plane reads its 4096 px, not built
            self.assertTrue(said[-1].startswith("  camera ahead of Live: "))
            self.assertIn("the camera 25.6 km from the aircraft", said[-1])
            s.plan(zoomed)
            self.assertEqual(len([line for line in said if "ahead of Live" in line]), 1)  # once
            cache.close()

    def test_a_camera_far_up_needs_only_the_small_levels(self):
        """X-Plane reads from the camera in three dimensions: a camera high above a texture needs
        only its small levels, however near the point below it; one low down, its sharpest. No
        reach around the camera itself, which is where it is: a camera taken 57 km out at cruise
        had Live build 4096 px levels around the point below it (2026-09-28)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            cache = ChunkCache(root / "c.sqlite")
            lat, lon = parse_texture_name(f"{NAMES[0]}.dds").center
            name = f"{NAMES[0]}.dds"
            views = Streamer(
                pack,
                cache,
                lambda on_piece: None,
                finest_quality=0,
                reach_km=20.0,
                log=lambda _: None,
            )

            def level(cam_up_km: float, cam_south_deg: float = 0.0) -> int:
                """The level the texture gets with the aircraft 111 km south of it (beyond its
                reach) and the camera over it, or cam_south_deg south of it."""
                fix = Fix(
                    lat - 1.0,
                    lon,
                    agl_m=3000.0,
                    cam_lat=lat - cam_south_deg,
                    cam_lon=lon,
                    cam_agl_m=cam_up_km * 1000.0,
                )
                return views.plan(fix, x_plane_there=False)[name][1]

            self.assertEqual(level(0.5), 0)  # low, right above it: its sharpest
            self.assertEqual(level(30.0), 2)  # 30 km up: the 1024 px level, as X-Plane reads it
            self.assertEqual(level(0.5, 0.27), 2)  # 30 km out, low: no reach around the camera
            # the aircraft on the ground by the texture, the camera 10 km up above it: the ground
            # round the aircraft stays at its sharpest, whatever the camera's height (the reach)
            on_ground = Fix(lat, lon, agl_m=0.0, cam_lat=lat, cam_lon=lon, cam_agl_m=10_000.0)
            self.assertEqual(views.plan(on_ground, x_plane_there=False)[name][1], 0)
            cache.close()


class RateTest(unittest.TestCase):
    def test_megabytes_over_the_last_seconds(self):
        from orthostudio.live.streamer import Rate

        r = Rate(window_s=3.0)
        self.assertEqual(r.mbs(100.0, 0), 0.0)
        self.assertAlmostEqual(r.mbs(101.0, 5_000_000), 5.0)  # 5 MB in 1 s
        self.assertAlmostEqual(r.mbs(102.0, 10_000_000), 5.0)
        for t in range(103, 110):
            r.mbs(float(t), 10_000_000)  # nothing more comes
        self.assertEqual(r.mbs(110.0, 10_000_000), 0.0)


class ScriptedXPlane:
    """X-Plane's UDP side for run(): silent while it loads a flight, then the aircraft at
    ``position``."""

    def __init__(self):
        self.commands = []
        self.position = None

    def subscribe(self):
        pass

    def command(self, name):
        self.commands.append(name)

    def receive(self, timeout):
        time.sleep(min(timeout, 0.02))
        if self.position is None:
            return None
        return {
            0: self.position[0],
            1: self.position[1],
        }  # latitude and longitude, DATAREFS' first two


@unittest.skipIf(Image is None, "needs numpy, Pillow and ispc_texcomp")
class RunTest(unittest.TestCase):
    """run() with the gate: a click in X-Plane, the plugin holding it, the aircraft arriving."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pack = root / "stream_pack"
        setup_region(make_source(root, tile=(45, 6), textures=NAMES), self.pack, log=lambda _: None)
        self.cache = ChunkCache(root / "c.sqlite")
        self.xplane = root / "X-Plane 12"
        self.prefs = self.xplane / "Output" / "preferences" / "Freeflight.prf"
        self.port = free_port()
        self.gate = Gate(self.xplane, lambda icao: None, port=self.port)
        self.xp = ScriptedXPlane()
        self.log = []
        self.built_when_said = {}  # message -> how many textures were built when it was logged

        def log(message):
            self.log.append(message)
            self.built_when_said.setdefault(message, len(self.streamer.built))

        self.streamer = Streamer(
            self.pack,
            self.cache,
            FakeDownloader,
            finest_quality=1,
            workers=2,
            xplane=self.xp,
            log=log,
        )
        self.stop = threading.Event()
        self.lat, self.lon = parse_texture_name(f"{NAMES[0]}.dds").center
        patch = mock.patch.object(streamer_module, "PLAN_EVERY_S", 0.2)
        patch.start()
        self.addCleanup(patch.stop)
        self.thread = threading.Thread(
            target=run,
            daemon=True,
            kwargs=dict(
                streamer=self.streamer,
                xp=self.xp,
                gate=self.gate,
                should_stop=self.stop.is_set,
                log=log,
            ),
        )
        self.thread.start()

    def tearDown(self):
        self.stop.set()
        self.thread.join(30)
        self.gate.close()
        self.cache.close()
        self.tmp.cleanup()

    def click(self):
        write_start(
            self.prefs,
            {"lle_ground_start": {"latitude": self.lat, "longitude": self.lon, "heading_true": 90}},
        )

    def hold(self, lat, lon, timeout=60.0, why="aircraft"):
        """What the plugin gets when it asks to hold X-Plane: every answer until GO."""
        answers = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as p:
            p.connect(("127.0.0.1", self.port))
            p.settimeout(0.5)
            end = time.time() + timeout
            while time.time() < end:
                p.send(f"LIVE1 HOLD {lat:.6f} {lon:.6f} {why}".encode())
                try:
                    answers.append(p.recv(100).decode())
                except TimeoutError:
                    continue
                if answers[-1].startswith("LIVE1 GO"):
                    break
                time.sleep(0.1)
        return answers

    def wait_for(self, test, timeout=30.0):
        end = time.time() + timeout
        while time.time() < end:
            if test():
                return True
            time.sleep(0.05)
        return False

    def test_held_until_built_then_no_reload(self):
        self.click()
        self.assertTrue(self.wait_for(lambda: any("starting a flight" in m for m in self.log)))
        answers = self.hold(self.lat, self.lon)
        self.assertTrue(answers[0].startswith("LIVE1 WAIT"), answers)  # nothing was built yet
        self.assertEqual(answers[-1], "LIVE1 GO 0")
        self.assertEqual(
            self.streamer.built, {f"{n}.dds": 1 for n in NAMES}
        )  # built before X-Plane goes on
        self.assertTrue(self.streamer.squares_ready(self.lat, self.lon))  # and its squares' files
        self.assertTrue((self.pack / "terrain" / f"{NAMES[0]}_water_overlay.ter").exists())
        waits = [
            dict(kv.split("=", 1) for kv in a.split(" at=")[0].split()[2:]) for a in answers[:-1]
        ]
        self.assertTrue(
            all(a.endswith(f" at={self.lat:.3f} {self.lon:.3f}") for a in answers[:-1])
        )  # the start's name
        permille = [int(w["p"]) for w in waits]
        self.assertEqual(permille, sorted(permille))  # the bar only moves forward
        self.assertTrue(all(float(w["mbs"]) >= 0 for w in waits))
        self.xp.position = (self.lat, self.lon)  # the flight runs
        time.sleep(1.0)
        self.assertEqual(self.xp.commands, [])  # X-Plane read everything once built: no reload
        self.assertFalse(self.streamer.read_early)
        self.assertTrue(any("X-Plane goes on" in m for m in self.log))

    def test_held_until_its_squares_files_are_made(self):
        """Everything built, X-Plane still waits for the files of its squares: the .ter files and
        texture names its DSFs name (orthostudio.live.names)."""
        names = self.streamer.names
        go_on = threading.Event()
        made = names.make

        def slow_make(square):
            go_on.wait(30)
            return made(square)

        names.make = slow_make
        self.click()
        self.assertTrue(
            self.wait_for(lambda: self.streamer.built == {f"{n}.dds": 1 for n in NAMES})
        )
        answers = self.hold(self.lat, self.lon, timeout=1.5)
        self.assertTrue(answers and all(a.startswith("LIVE1 WAIT n=0 ") for a in answers), answers)
        self.assertTrue(self.wait_for(lambda: any("are being made" in m for m in self.log)))
        go_on.set()
        answers = self.hold(self.lat, self.lon)
        self.assertEqual(answers[-1], "LIVE1 GO 0")
        self.assertTrue((self.pack / "terrain" / f"{NAMES[0]}.ter").exists())

    def test_without_the_plugin_no_reload(self):
        """X-Plane did not wait: once everything is built the log says so, and no reload of the
        scenery is asked, which keeps the textures X-Plane holds (2026-09-27, 2026-09-29)."""
        self.click()
        self.xp.position = (self.lat, self.lon)  # X-Plane did not wait
        self.assertTrue(
            self.wait_for(lambda: any("what it read before stays" in m for m in self.log))
        )
        self.assertEqual(self.xp.commands, [])

    def test_a_new_flight_while_flying(self):
        """Start Flight clicked while X-Plane runs elsewhere: its position is still the old one,
        yet the new start is what is built before X-Plane goes on."""
        home = parse_texture_name(f"{NAMES[1]}.dds").center
        self.xp.position = (home[0] - 2.0, home[1])  # flying 220 km south
        self.assertTrue(
            self.wait_for(
                lambda: self.streamer.last_fix is not None and not self.streamer.read_early
            )
        )
        before = len(
            self.xp.commands
        )  # the streamer came after X-Plane: X-Plane may have read early there
        self.click()  # X-Plane runs: a flight change, or X-Plane quitting, as far as anyone knows
        self.assertTrue(self.wait_for(lambda: any("next start is" in m for m in self.log)))
        old = self.xp.position
        answers = self.hold(*old)  # the plugin still reads the old position
        self.assertTrue(answers[0].startswith("LIVE1 WAIT"), answers)
        self.assertEqual(answers[-1], "LIVE1 GO 0")
        self.assertEqual(self.streamer.built, {f"{n}.dds": 1 for n in NAMES})
        said = [m for m in self.log if m.startswith("  everything X-Plane will read around")]
        self.assertEqual(
            [self.built_when_said[m] for m in said], [2]
        )  # said once, and only once true
        self.xp.position = (self.lat, self.lon)
        time.sleep(1.0)
        self.assertEqual(self.xp.commands[before:], [])  # no reload for the new flight

    def test_a_new_flight_before_the_click_is_seen(self):
        """X-Plane's message 115 can come before the streamer has seen Freeflight.prf change: a
        flight's start is held all the same, where X-Plane already says it starts."""
        home = parse_texture_name(f"{NAMES[1]}.dds").center
        self.xp.position = (home[0] - 2.0, home[1])  # flying 220 km south
        self.assertTrue(
            self.wait_for(
                lambda: self.streamer.last_fix is not None and not self.streamer.read_early
            )
        )
        before = len(self.xp.commands)
        answers = self.hold(self.lat, self.lon, why="flight")  # no click written yet
        self.assertTrue(answers[0].startswith("LIVE1 WAIT"), answers)
        self.assertEqual(answers[-1], "LIVE1 GO 0")
        self.xp.position = (self.lat, self.lon)
        time.sleep(1.0)
        self.assertEqual(self.xp.commands[before:], [])

    def test_a_busy_loop_still_holds_xplane(self):
        """The loop planning a new start for 2 s (3 s at LGAV under a start's load, 2026-09-29):
        the plugin, which lets X-Plane go when a HOLD gets no answer within a second, gets the
        gate's WAIT meanwhile, then GO once everything is built."""
        plan = self.streamer.plan

        def slow(fix, *args, **kw):
            if kw.get("starting"):
                time.sleep(2.0)
            return plan(fix, *args, **kw)

        self.streamer.plan = slow  # type: ignore[method-assign]
        self.click()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as p:
            p.connect(("127.0.0.1", self.port))
            p.settimeout(1.0)  # the plugin's second
            p.send(f"LIVE1 HOLD {self.lat:.6f} {self.lon:.6f} flight".encode())
            self.assertTrue(p.recv(100).startswith(b"LIVE1 WAIT"))
        answers = self.hold(self.lat, self.lon, why="flight")
        self.assertEqual(answers[-1], "LIVE1 GO 0")
        self.assertEqual(self.streamer.built, {f"{n}.dds": 1 for n in NAMES})
        self.assertTrue(any(m.startswith("  Live was busy ") for m in self.log))

    def test_quitting_is_not_a_start(self):
        """X-Plane writes Freeflight.prf as it quits: while it runs there, that is no new flight."""
        self.xp.position = (self.lat, self.lon)
        self.assertTrue(self.wait_for(lambda: self.streamer.last_fix is not None))
        self.assertTrue(
            self.wait_for(lambda: not self.streamer.read_early and not self.streamer.urgent_left())
        )
        before = len(self.xp.commands)
        self.click()  # the same start, written again, X-Plane never stopping
        self.assertTrue(self.wait_for(lambda: any("next start is" in m for m in self.log)))
        self.assertFalse(any("starting a flight" in m for m in self.log))  # not said, as it was
        time.sleep(1.0)
        self.assertEqual(self.xp.commands[before:], [])  # no reload

    def test_a_livery_change_in_flight_does_not_hold(self):
        self.xp.position = (self.lat, self.lon)
        self.assertTrue(self.wait_for(lambda: self.streamer.last_fix is not None))
        start = time.time()
        self.assertEqual(self.hold(self.lat, self.lon), ["LIVE1 GO 0"])
        self.assertLess(time.time() - start, 1.0)


if __name__ == "__main__":
    unittest.main()


class SilentDownloader:
    """Asked for pieces, answers none: the internet gone."""

    def __init__(self, on_piece):
        self.asked = []
        self.downloaded = self.bytes = self.failed = 0

    def submit(self, key, priority):
        self.asked.append(key)

    def close(self):
        pass


class NoTimeLimitTest(unittest.TestCase):
    """The plugin holds X-Plane as long as pieces arrive, with no time limit; once none has arrived
    for STALL_S, the streamer lets X-Plane go, and reloads the scenery once all is built."""

    def test_nothing_downloads_any_more(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            cache = ChunkCache(root / "c.sqlite")
            port = free_port()
            gate = Gate(root / "X-Plane 12", lambda icao: None, port=port)
            logged = []
            s = Streamer(
                pack, cache, SilentDownloader, finest_quality=1, workers=1, log=logged.append
            )
            stop = threading.Event()
            lat, lon = parse_texture_name(f"{NAMES[0]}.dds").center
            with (
                mock.patch.object(streamer_module, "STALL_S", 1.5),
                mock.patch.object(streamer_module, "PLAN_EVERY_S", 0.2),
            ):
                thread = threading.Thread(
                    target=run,
                    daemon=True,
                    kwargs=dict(
                        streamer=s,
                        xp=ScriptedXPlane(),
                        gate=gate,
                        should_stop=stop.is_set,
                        log=logged.append,
                    ),
                )
                thread.start()
                answers = []
                t0 = time.time()
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as p:
                    p.connect(("127.0.0.1", port))
                    p.settimeout(0.5)
                    while time.time() - t0 < 20:
                        p.send(f"LIVE1 HOLD {lat:.6f} {lon:.6f} aircraft".encode())
                        try:
                            answers.append(p.recv(100).decode())
                        except TimeoutError:
                            continue
                        if answers[-1].startswith("LIVE1 GO"):
                            break
                        time.sleep(0.1)
                took = time.time() - t0
                stop.set()
                thread.join(30)
            gate.close()
            cache.close()
        self.assertTrue(answers[0].startswith("LIVE1 WAIT"), answers[:3])
        self.assertTrue(answers[-1].startswith("LIVE1 GO"), answers[-3:])
        self.assertNotEqual(answers[-1], "LIVE1 GO 0")  # textures were left to build
        self.assertGreater(took, 1.5)  # held while pieces could still come
        self.assertTrue(any("nothing downloaded for" in line for line in logged), logged[-5:])


class InFlightTest(unittest.TestCase):
    def test_as_many_requests_at_once_as_the_source_takes(self):
        """OrthoStudio XP measured Bing's servers: 128 requests at once, beyond which they gave no
        more. Live asked 96."""
        from orthostudio.live.downloader import Downloader
        from orthostudio.live.providers import registry

        d = Downloader("BI", lambda *a: None)
        try:
            self.assertEqual(d.max_in_flight, registry()["BI"].max_in_flight)
            self.assertEqual(d.max_in_flight, 128)
        finally:
            d.close()
