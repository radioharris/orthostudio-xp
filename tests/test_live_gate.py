# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The start gate: X-Plane waits on its loading screen until the ortho around the start is built.

The plugin is compiled for real, with a stand-in for X-Plane (tests/fake_xplm.c), and talks to the
gate over UDP as it does in X-Plane.
"""

import contextlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
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
from orthostudio.live.filesystem import folder_link, is_folder_link
from orthostudio.live.gate import Gate, read_last_start, start_position
from orthostudio.live.geo import parse_texture_name
from orthostudio.live.pause import last_start
from orthostudio.live.region import Zooms, setup_region
from orthostudio.live.streamer import Fix, Streamer, run

HERE = Path(__file__).resolve().parent
CLANG = shutil.which("clang")
PLANE_LOADED, LIVERY_LOADED, FMOD_BANK_LOADED = 102, 108, 112


def c_compiler() -> str | None:
    """The compiler of this system for the stand-in X-Plane: clang on a Mac, gcc (MinGW) or clang
    on Windows, cc on Linux."""
    if sys.platform == "darwin":
        return CLANG
    if sys.platform == "win32":
        return shutil.which("gcc") or shutil.which("clang")
    return shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


def has_x11_headers(cc: str) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "x11.c"
        probe.write_text("#include <X11/Xlib.h>\nint main(void) { return 0; }\n")
        return (
            subprocess.run([cc, "-fsyntax-only", str(probe)], capture_output=True).returncode == 0
        )


def harness_command(cc: str, exe: Path, defines: list[str]) -> list[str]:
    """The plugin and its loading window for this system, around the stand-in X-Plane
    (tests/data/live/fake_xplm.c), as one program."""
    fake = str(HERE / "data" / "live" / "fake_xplm.c")
    common = [cc, "-O1", "-Wall", "-Werror", *defines, "-o", str(exe), str(plugin.SOURCE)]
    if sys.platform == "darwin":
        return [*common[:3], "-fobjc-arc", *common[3:], str(plugin.PANEL), fake]
    if sys.platform == "win32":
        return [*common, str(plugin.WINDOWS_PANEL), fake, "-lws2_32", "-luser32", "-lgdi32"]
    no_x11 = [] if has_x11_headers(cc) else ["-DLIVE_NO_X11"]
    return [*common, *no_x11, str(plugin.LINUX_PANEL), fake, "-ldl", "-lm"]


def free_port() -> int:
    """A UDP port free now, outside every system's ephemeral ports (Linux 32768-60999, Windows and
    macOS 49152-65535): the harnesses have it built in and bind it later, and meanwhile the system
    never gives it to another test's socket (a clash seen once on Linux, 2026-10-01)."""
    import random

    for _ in range(500):
        port = random.randrange(20000, 32768)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:  # taken, or reserved (Windows)
                continue
            return port
    raise OSError("no free UDP port between 20000 and 32767")


def let_go(sock: socket.socket) -> None:
    """Close a stand-in streamer's socket, waking first a thread that reads it: Linux keeps a
    socket bound while a thread waits on it, closed or not, and the next test could not bind the
    port (its read then gives no data)."""
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)
    sock.close()


@contextlib.contextmanager
def live_port(port: int):
    """Live's port, bound as the streamer binds it, let go of on the way out (:func:`let_go`)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    try:
        yield sock
    finally:
        let_go(sock)


def read(sock: socket.socket) -> tuple[bytes, object]:
    """A datagram for the stand-in streamer; OSError once its socket is let go of."""
    data, addr = sock.recvfrom(100)
    if not data:
        raise OSError("let go of")
    return data, addr


def first_hold(sock: socket.socket) -> object:
    """The plugin's address at its first HOLD, the datagrams before it read (its start's HELLO,
    LOAD): answering one of those, the stand-in's answer could come before the plugin asks, and
    the plugin drops what came before it asks (a slow machine, 2026-10-01)."""
    while True:
        data, addr = read(sock)
        if data.startswith(b"LIVE1 HOLD"):
            return addr


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
                p.send(b"LIVE1 HOLD 43.438210 5.212888 flight day=287")  # the flight's date
                time.sleep(0.1)
                got = gate.requests()
                self.assertEqual(
                    [(k, lat, lon, why) for k, lat, lon, _, why in got],
                    [
                        ("LOAD", 43.43821, 5.212888, "aircraft"),
                        ("HOLD", 43.43821, 5.212888, "flight"),
                        ("HOLD", 43.43821, 5.212888, "flight"),
                    ],
                )
                self.assertEqual(gate.held_day, 288)  # 15 October: X-Plane counts from 0
                self.assertIsNone(gate.held_utc)  # an older plugin: the date alone
                # a newer one gives the flight's UTC time too: whether it is night at the start
                p.send(b"LIVE1 HOLD 43.438210 5.212888 aircraft day=171 utc=55800")
                time.sleep(0.1)
                self.assertEqual([k for k, *_ in gate.requests()], ["HOLD"])
                self.assertEqual((gate.held_day, gate.held_utc), (172, 55800.0))
                p.send(b"LIVE1 HOLD 43.438210 5.212888 aircraft day=171 utc=-1")  # no dataref
                time.sleep(0.1)
                gate.requests()
                self.assertEqual((gate.held_day, gate.held_utc), (172, None))
                gate.answer(got[1][3], False, 12, "LFML runway 13L", 0.4567, 23.4, 15.18)
                self.assertEqual(
                    p.recv(100),
                    b"LIVE1 WAIT n=12 b=0 p=457 s=23 mbs=15.2 lang=en night=0 at=LFML runway 13L",
                )
                gate.answer(got[1][3], False, 3, "LFPG Aérogare 2 機", lang="fr")
                self.assertTrue(
                    p.recv(100).endswith("lang=fr night=0 at=LFPG Aérogare 2 機".encode())
                )
                gate.answer(got[1][3], False, 3, "LFBZ runway 09", lang="fr")
                self.assertTrue(
                    p.recv(100).endswith(b"lang=fr night=0 at=LFBZ piste 09")
                )  # in French
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
                self.assertEqual(p.recv(100), b"LIVE1 WAIT n=0 b=0 p=0 s=0 mbs=0.0")
                gate.hold_at("LGAV parking B17", "fr")  # the start the loop plans next
                p.send(b"LIVE1 HOLD 37.936700 23.944500 flight")
                self.assertTrue(p.recv(100).endswith(b"lang=fr night=0 at=LGAV parking B17"))
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


@unittest.skipIf(c_compiler() is None, "needs a C compiler")
class PluginTest(unittest.TestCase):
    """native/xplane_plugin/live_gate.c itself, with this system's loading window, driven by a
    fake X-Plane (tests/data/live/fake_xplm.c)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = free_port()
        cls.exe = Path(cls.tmp.name) / (
            "plugin_harness.exe" if sys.platform == "win32" else "plugin_harness"
        )
        cmd = harness_command(
            c_compiler(), cls.exe, [f"-DLIVE_PORT={cls.port}", "-DSILENT_S=1.0", "-DALONE_S=0.05"]
        )
        if sys.platform == "darwin":
            cmd += ["-framework", "Cocoa", "-framework", "QuartzCore"]
        subprocess.run(cmd, check=True)

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
        folder_link(root / "pack", scenery / "z_osxp_live")  # as Live makes them on this system
        folder_link(root / "pack", scenery / "y_osxp_live_overlays")
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
        with live_port(self.port):  # Live listens, answering nothing yet
            _, logged = self.xplane(43.4382, 5.2129, root=root)
        self.assertTrue(is_folder_link(root / "Custom Scenery" / "z_osxp_live"))
        self.assertFalse(any("Live is not running" in line for line in logged))

    def test_message_115_holds_nothing(self):
        """Message 115 is XPLM_MSG_WEATHER_DELIVERED (SDK 4.4.0): X-Plane 12.4.4 sends it as a
        flight's weather is delivered, at every change of weather too, 12.4.3 not at all. The
        plugin holds X-Plane at none of it, a first flight or later: a new location chosen in
        flight, which loads no aircraft, is held by Live's pause (orthostudio.live.pause)."""
        holds = []

        def streamer(sock):
            with contextlib.suppress(OSError):  # its socket let go of
                while True:
                    data, addr = read(sock)
                    if data.startswith(b"LIVE1 HOLD"):
                        holds.append(time.time())
                        sock.sendto(b"LIVE1 WAIT 3" if len(holds) < 3 else b"LIVE1 GO 0", addr)

        with live_port(self.port) as sock:
            threading.Thread(target=streamer, args=(sock,), daemon=True).start()
            took, _ = self.xplane(46.2381, 6.1089, "115:1")
            self.assertLess(took["115:1"], 0.3)
            took, _ = self.xplane(46.2381, 6.1089, "102:0", "115:0", "115:1")
            let_go(sock)
        self.assertLess(took["115:1"], 0.3)
        self.assertLess(took["115:0"], 0.3)
        self.assertEqual(holds, [])

    def test_waits_until_the_streamer_says_go(self):
        heard = []

        def streamer(sock):
            waits = 3
            with contextlib.suppress(OSError):  # its socket let go of
                while True:
                    data, addr = read(sock)
                    heard.append(data.decode())
                    if data.startswith(b"LIVE1 HOLD"):
                        sock.sendto(
                            b"LIVE1 WAIT n=7 p=350 s=12 mbs=5.2 at=LFMN runway 04R"
                            if waits
                            else b"LIVE1 GO 0",
                            addr,
                        )
                        waits = max(0, waits - 1)

        with live_port(self.port) as sock:
            threading.Thread(target=streamer, args=(sock,), daemon=True).start()
            took, logged = self.xplane(43.4382, 5.2129, "102:0", "112:0", "112:1", "108:1", "108:0")
            let_go(sock)
        heard = [h for h in heard if h != "LIVE1 HELLO"]  # the plugin's start: is Live there?
        self.assertEqual(heard[0], "LIVE1 LOAD 43.438200 5.212900")  # PLANE_LOADED tells where
        # the stand-in X-Plane's date and UTC time: day 0, at 0 s
        self.assertTrue(
            all(h == "LIVE1 HOLD 43.438200 5.212900 aircraft day=0 utc=0" for h in heard[1:])
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
            with contextlib.suppress(OSError):
                sock.sendto(b"LIVE1 WAIT 50", first_hold(sock))  # then silence

        with live_port(self.port) as sock:
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
            with contextlib.suppress(OSError):
                sock.sendto(b"LIVE1 WAIT 50", first_hold(sock))
                sock.close()  # then gone

        threading.Thread(target=streamer, daemon=True).start()
        try:
            took, logged = self.xplane(45.0, 6.0, "102:0", "108:0")
        finally:
            let_go(sock)
        self.assertLess(took["108:0"], 0.9)  # not SILENT_S later
        self.assertIn("the streamer stopped: X-Plane goes on", logged[-1])

    def test_a_notice_where_there_is_no_mesh(self):
        """The streamer's notice shows in a small panel, said once in Log.txt; its cross closes it
        for good, and the plugin keeps asking."""
        heard = []
        note = b"LIVE1 NOTE 9 Live: no mesh here: install North America in the Live window"

        def streamer(sock):
            with contextlib.suppress(OSError):  # its socket let go of
                while True:
                    data, addr = read(sock)
                    heard.append(data.decode())
                    if data.startswith(b"LIVE1 ASK"):
                        sock.sendto(note, addr)

        with live_port(self.port) as sock:
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
            let_go(sock)
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
        with live_port(self.port) as sock:
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
        self.pauser = FakePauser()
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
                # the season of the flight's date: autumn from day 266 (23 September)
                season_for=lambda lat, day=None: ("autumn" if (day or 0) >= 266 else "summer", "t"),
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

    def test_a_flight_in_another_season_waits_for_its_colours(self):
        """The same start again, its date in autumn: X-Plane waits until the textures are built
        again in autumn's colours (it went on after 1 s with summer's, 2026-10-13)."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as p:
            p.connect(("127.0.0.1", self.port))
            p.settimeout(2)
            end = time.time() + 60
            summer = f"LIVE1 HOLD {self.lat:.6f} {self.lon:.6f} flight day=200".encode()
            while not self.ask(p, summer).startswith("LIVE1 GO"):
                self.assertLess(time.time(), end)
                time.sleep(0.1)
            self.assertEqual(self.streamer.season, "summer")
            built = dict(self.streamer.built)
            autumn = f"LIVE1 HOLD {self.lat:.6f} {self.lon:.6f} flight day=287".encode()
            self.assertTrue(self.ask(p, autumn).startswith("LIVE1 WAIT"))
            while not self.ask(p, autumn).startswith("LIVE1 GO"):
                self.assertLess(time.time(), end)
                time.sleep(0.1)
            self.assertEqual(self.streamer.season, "autumn")
            self.assertEqual(self.streamer.built, built)  # every one built again, in autumn


@unittest.skipUnless(sys.platform == "darwin" and CLANG, "the Mac's build, with clang")
class BuildTest(unittest.TestCase):
    def test_build_install_uninstall(self):
        """The real build, which needs no X-Plane: linked to X-Plane's XPLM framework by the name
        that one answers to, as Laminar's own plugins are, then put into a stand-in X-Plane."""
        with tempfile.TemporaryDirectory() as tmp:
            xplane = Path(tmp) / "X-Plane 12"
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
                linked = subprocess.run(["otool", "-L", str(xpl)], capture_output=True, text=True)
                xplm = f"{plugin.XPLM_INSTALL_NAME} (compatibility version 1.0.0, current version"
                self.assertEqual(linked.stdout.count(f"{xplm} 1.0.0)"), 2)  # each half, as xlua's
                for machine in arch:
                    bound = subprocess.run(
                        ["nm", "-m", "-arch", machine, str(xpl)], capture_output=True, text=True
                    ).stdout.splitlines()
                    self.assertEqual(
                        {line.split()[-3] for line in bound if line.endswith("(from XPLM)")},
                        {f"_{name}" for name in plugin.xplm_functions()},
                    )
                plugin.uninstall(xplane)
                self.assertFalse(
                    (xplane / "Resources" / "plugins" / "OrthoStudio XP Live").exists()
                )

    def test_shipped_by_the_mac_installer(self):
        """The Mac installer's copy (:func:`plugin.ship`, tools/package/build.py): built for Apple
        Silicon and Intel Macs at once, whichever app it goes in (X-Plane runs natively, the Intel
        app perhaps under Rosetta), and the one Live then installs."""
        with tempfile.TemporaryDirectory() as tmp:
            shipped = plugin.ship(Path(tmp), "darwin")
            self.assertEqual(shipped, Path(tmp) / f"{plugin.NAME}.xpl")
            arch = subprocess.run(["lipo", "-archs", str(shipped)], capture_output=True, text=True)
            self.assertEqual(sorted(arch.stdout.split()), ["arm64", "x86_64"])
            with (
                mock.patch.object(plugin, "PREBUILT", shipped),
                mock.patch.object(plugin, "BUILD_DIR", Path(tmp) / "build"),
                mock.patch.object(plugin.subprocess, "run", side_effect=AssertionError("built")),
            ):
                self.assertEqual(plugin.availability(), "ready")
                self.assertEqual(plugin.built(), shipped)

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


ENTRY_POINTS = {
    "XPluginStart",
    "XPluginStop",
    "XPluginEnable",
    "XPluginDisable",
    "XPluginReceiveMessage",
}


def pe_exports_and_imports(data: bytes) -> tuple[int, set[str], set[str]]:
    """A Windows DLL's machine, the names it exports, and the DLLs it imports from."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe : pe + 4] == b"PE\0\0"
    machine, count = struct.unpack_from("<HH", data, pe + 4)
    optional = pe + 24
    directories = optional + (112 if struct.unpack_from("<H", data, optional)[0] == 0x20B else 96)
    sections = optional + struct.unpack_from("<H", data, pe + 20)[0]

    def at(rva: int) -> int:
        for i in range(count):
            size, address, raw_size, raw = struct.unpack_from("<IIII", data, sections + 40 * i + 8)
            if address <= rva < address + max(size, raw_size):
                return rva - address + raw
        raise ValueError(f"no section holds {rva:#x}")

    def text(rva: int) -> str:
        start = at(rva)
        return data[start : data.index(b"\0", start)].decode()

    exports_rva = struct.unpack_from("<I", data, directories)[0]
    names, names_rva = (
        struct.unpack_from("<I", data, at(exports_rva) + 24)[0],
        struct.unpack_from("<I", data, at(exports_rva) + 32)[0],
    )
    exports = {text(struct.unpack_from("<I", data, at(names_rva) + 4 * i)[0]) for i in range(names)}
    imports, entry = set(), at(struct.unpack_from("<I", data, directories + 8)[0])
    while struct.unpack_from("<I", data, entry + 12)[0]:
        imports.add(text(struct.unpack_from("<I", data, entry + 12)[0]).lower())
        entry += 20
    return machine, exports, imports


def elf_symbols(data: bytes) -> tuple[int, set[str], set[str]]:
    """A Linux shared object's machine, and the dynamic symbols it defines and those it needs."""
    assert data[:4] == b"\x7fELF" and data[4] == 2 and data[5] == 1  # 64 bits, little-endian
    machine = struct.unpack_from("<H", data, 18)[0]
    offset = struct.unpack_from("<Q", data, 0x28)[0]
    size, count = struct.unpack_from("<HH", data, 0x3A)
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, offset + i * size) for i in range(count)]
    defined: set[str] = set()
    needed: set[str] = set()
    for section in sections:
        if section[1] != 11:  # SHT_DYNSYM
            continue
        strings = sections[section[6]][4]
        for k in range(section[5] // 24):
            name, info, _, index = struct.unpack_from("<IBBH", data, section[4] + 24 * k)
            start = strings + name
            symbol = data[start : data.index(b"\0", start)].decode()
            if symbol and info >> 4 in (1, 2):  # global or weak
                (needed if index == 0 else defined).add(symbol)
    return machine, defined, needed


class PrebuiltTest(unittest.TestCase):
    """The Windows and Linux plugins, built ahead (tools/live/plugin_builds.py): from these very
    sources, with what X-Plane calls, needing nothing a Windows or a Linux lacks."""

    def xpl(self, platform: str) -> bytes:
        return plugin._xpl(plugin.PREBUILT_DIR, platform).read_bytes()

    def test_built_from_these_sources(self):
        said = plugin.SOURCES.read_text().split()[0]
        self.assertEqual(
            said,
            plugin.sources_digest(),
            "the plugin's sources changed since the Windows and Linux plugins were built: "
            "uv run python tools/live/plugin_builds.py",
        )

    def test_windows(self):
        machine, exports, imports = pe_exports_and_imports(self.xpl("win32"))
        self.assertEqual(machine, 0x8664)  # x86-64, as X-Plane 12
        self.assertEqual(exports, ENTRY_POINTS)  # nothing else
        self.assertLessEqual(
            imports,
            {"xplm_64.dll", "ws2_32.dll", "user32.dll", "gdi32.dll", "kernel32.dll", "msvcrt.dll"},
        )
        self.assertIn("xplm_64.dll", imports)

    def test_linux(self):
        machine, defined, needed = elf_symbols(self.xpl("linux"))
        self.assertEqual(machine, 62)  # x86-64
        self.assertEqual(defined, ENTRY_POINTS)
        xplm = {n for n in needed if n.startswith("XPLM")}
        self.assertEqual(
            xplm,
            {
                "XPLMDebugString",
                "XPLMEnableFeature",
                "XPLMFindDataRef",
                "XPLMGetDatad",
                "XPLMGetDataf",
                "XPLMGetDatai",
                "XPLMGetSystemPath",
                "XPLMRegisterFlightLoopCallback",
                "XPLMUnregisterFlightLoopCallback",
            },
        )
        self.assertEqual(xplm, set(plugin.xplm_functions()))  # the Mac's stub lists the same
        self.assertFalse(
            {n for n in needed if n.startswith("X") and not n.startswith("XPLM")}
        )  # X11: opened

    def test_shipped_by_the_installers(self):
        """An installer's copy (:func:`plugin.ship`, tools/package/build.py): the very file built
        ahead, where Live looks for an installer's; none once the sources changed since."""
        for platform in ("win32", "linux"):
            with self.subTest(platform), tempfile.TemporaryDirectory() as tmp:
                shipped = plugin.ship(Path(tmp), platform)
                self.assertEqual(shipped, Path(tmp) / f"{plugin.NAME}.xpl")
                self.assertEqual(shipped.read_bytes(), self.xpl(platform))
                shipped.unlink()
                with (
                    mock.patch.object(plugin, "sources_digest", return_value="0" * 64),
                    self.assertRaisesRegex(RuntimeError, "plugin_builds.py"),
                ):
                    plugin.ship(Path(tmp), platform)
                self.assertFalse(shipped.exists())

    def test_installed_where_xplane_looks(self):
        for platform, folder in (("win32", "win_x64"), ("linux", "lin_x64")):
            with (
                tempfile.TemporaryDirectory() as tmp,
                mock.patch.object(plugin.sys, "platform", platform),
                mock.patch.object(plugin, "PREBUILT", Path(tmp) / "none.xpl"),
            ):
                xplane = Path(tmp) / "X-Plane 12"
                self.assertEqual(plugin.availability(), "ready")
                self.assertTrue(plugin.install(xplane, log=self.fail))
                xpl = xplane / "Resources" / "plugins" / plugin.NAME / folder / f"{plugin.NAME}.xpl"
                self.assertEqual(xpl.read_bytes(), self.xpl(platform))
                self.assertTrue(plugin.uninstall(xplane))
                self.assertFalse((xplane / "Resources" / "plugins" / plugin.NAME).exists())

    def test_none_for_this_system(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(plugin.sys, "platform", "linux"),
            mock.patch.object(plugin, "PREBUILT", Path(tmp) / "none.xpl"),
            mock.patch.object(plugin, "PREBUILT_DIR", Path(tmp) / "prebuilt"),
        ):
            said = []
            self.assertEqual(plugin.availability(), "missing")
            self.assertFalse(plugin.install(Path(tmp) / "X-Plane 12", log=said.append))
            self.assertIn("will not wait", said[0])

    def test_windows_holds_a_loaded_plugin(self):
        """Windows keeps the file of a plugin X-Plane has loaded until X-Plane has quit: it stays,
        and says so."""
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(plugin.sys, "platform", "win32"),
        ):
            xplane = Path(tmp)
            xpl = plugin._xpl(plugin.plugin_dir(xplane))
            xpl.parent.mkdir(parents=True)
            xpl.write_bytes(b"MZ")
            with mock.patch.object(Path, "unlink", side_effect=PermissionError("in use")):
                self.assertFalse(plugin.uninstall(xplane))
            self.assertTrue(xpl.exists())


def runs_intel_code() -> bool:
    """Whether this Mac runs Intel code: an Intel Mac, or Apple Silicon with Rosetta."""
    if sys.platform != "darwin" or CLANG is None:
        return False
    return subprocess.run(["/usr/bin/arch", "-x86_64", "/usr/bin/true"]).returncode == 0


@unittest.skipUnless(runs_intel_code(), "a Mac that runs Intel code")
class IntelMacTest(unittest.TestCase):
    """The Mac plugin's Intel half (one file holds both), run as on an Intel Mac: under Rosetta on
    Apple Silicon. The other tests run the half of the Mac they run on."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = free_port()
        cls.exe = Path(cls.tmp.name) / "plugin_harness_x86_64"
        cmd = harness_command(
            CLANG, cls.exe, [f"-DLIVE_PORT={cls.port}", "-DSILENT_S=1.0", "-DALONE_S=0.05"]
        )
        subprocess.run(
            [*cmd, "-arch", "x86_64", "-framework", "Cocoa", "-framework", "QuartzCore"], check=True
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_intel_code(self):
        kind = subprocess.run(["/usr/bin/file", str(self.exe)], capture_output=True, text=True)
        self.assertIn("x86_64", kind.stdout)
        self.assertNotIn("arm64", kind.stdout)

    def test_x_plane_waits_until_live_says_go(self):
        answers = []

        def streamer(sock):
            with contextlib.suppress(OSError):  # its socket let go of
                while True:
                    data, addr = read(sock)
                    if data.startswith(b"LIVE1 HOLD"):
                        answers.append(data)
                        sock.sendto(b"LIVE1 WAIT 3" if len(answers) < 3 else b"LIVE1 GO 0", addr)

        with live_port(self.port) as sock:
            threading.Thread(target=streamer, args=(sock,), daemon=True).start()
            out = subprocess.run(
                [str(self.exe), "43.4382", "5.2129", "102:0", "112:1"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        took = {
            f"{msg}:{param}": float(secs)
            for _, msg, param, secs in (
                line.split() for line in out.stdout.splitlines() if line.startswith("MSG ")
            )
        }
        self.assertGreater(took["112:1"], 0.4)  # held until the third answer
        self.assertLess(took["112:1"], 3.0)
        self.assertIn("X-Plane waits at the radio sounds", out.stdout)


HOST_DIR = (
    "OSXP_LIVE_XPL_HOST"  # a folder with xpl_host(.exe) built already (a Windows without gcc)
)


def built_ahead_host(tmp: Path) -> Path | None:
    """A program that loads this system's plugin built ahead as X-Plane loads it
    (tests/data/live/xpl_host.c), where this computer can run it: Linux or Windows on x86-64,
    with a compiler (MinGW's gcc on Windows), or one built already in $OSXP_LIVE_XPL_HOST (with
    its fake XPLM_64.dll on Windows); else None."""
    import sysconfig

    windows = sys.platform == "win32"
    # this Python's own machine, x86-64 also where a Windows on ARM runs it as X-Plane, emulated
    if sysconfig.get_platform() not in ("win-amd64", "linux-x86_64"):
        return None
    given = Path(os.environ.get(HOST_DIR, ".")) / ("xpl_host.exe" if windows else "xpl_host")
    if os.environ.get(HOST_DIR) and given.is_file():
        return given
    cc = shutil.which("gcc") if windows else c_compiler()
    if cc is None:
        return None
    data = HERE / "data" / "live"
    if windows:  # the XPLM functions in a fake XPLM_64.dll, which the plugin imports from
        implib = tmp / "libXPLM_64.a"
        subprocess.run(
            [
                cc,
                "-shared",
                "-DNO_MAIN",
                "-o",
                str(tmp / "XPLM_64.dll"),
                str(data / "fake_xplm.c"),
                f"-Wl,--out-implib,{implib}",
            ],
            check=True,
        )
        host = tmp / "xpl_host.exe"
        subprocess.run([cc, "-o", str(host), str(data / "xpl_host.c"), str(implib)], check=True)
        return host
    host = tmp / "xpl_host"
    subprocess.run(
        [
            cc,
            "-rdynamic",
            "-DNO_MAIN",
            "-o",
            str(host),
            str(data / "xpl_host.c"),
            str(data / "fake_xplm.c"),
            "-ldl",
            "-lm",
        ],
        check=True,
    )
    return host


class BuiltAheadTest(unittest.TestCase):
    """The very file X-Plane loads on Windows or Linux (native/xplane_plugin/prebuilt), loaded as
    X-Plane loads it, on such a computer only: its own port (49250) and waits, as built. One test,
    its scenarios in turn: on that one port, two at once would hear each other's streamer."""

    PORT = 49250
    run_as: tuple[str, ...] = ()  # the host's machine, where this computer runs another's (Rosetta)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.xpl = plugin._xpl(plugin.PREBUILT_DIR)
        cls.host = built_ahead_host(Path(cls.tmp.name))
        if cls.host is None:
            cls.tmp.cleanup()
            raise unittest.SkipTest("the plugin built ahead runs on Linux and Windows, x86-64")
        try:
            with live_port(cls.PORT):
                pass
        except OSError:
            cls.tmp.cleanup()
            raise unittest.SkipTest("Live's port is taken: Live runs on this computer") from None

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def xplane(self, *commands, root=None):
        env = {**os.environ, "FAKE_XPLANE": str(root) if root else ""}
        out = subprocess.run(
            [*self.run_as, str(self.host), str(self.xpl), "43.4382", "5.2129", *commands],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("NAME OrthoStudio XP Live", out.stdout)
        took, logged = {}, []
        for line in out.stdout.splitlines():
            if line.startswith("MSG "):
                _, msg, param, secs = line.split()
                took[f"{msg}:{param}"] = float(secs)
            elif line.startswith("LOG "):
                logged.append(line[4:])
        return took, logged

    def scenario_without_live_x_plane_goes_on(self):
        took, logged = self.xplane("102:0", "112:1", "108:0")
        self.assertLess(max(took.values()), 1.5)  # no streamer: no wait
        self.assertIn("OrthoStudio XP Live: message 112 (1)", logged)

    def scenario_x_plane_waits_until_live_says_go(self):
        holds = []

        def streamer(sock):
            with contextlib.suppress(OSError):  # its socket let go of
                while True:
                    data, addr = read(sock)
                    if data.startswith(b"LIVE1 HOLD"):
                        holds.append(data)
                        answer = b"LIVE1 WAIT n=7 p=350 s=12 mbs=5.2 at=LFML runway 13L"
                        sock.sendto(answer if len(holds) < 4 else b"LIVE1 GO 0", addr)

        with live_port(self.PORT) as sock:
            threading.Thread(target=streamer, args=(sock,), daemon=True).start()
            took, logged = self.xplane("102:0", "112:1")
        self.assertGreater(took["112:1"], 0.6)  # three waits of 0.25 s before the go
        self.assertLess(took["112:1"], 5.0)
        self.assertTrue(any("waits at the radio sounds" in line for line in logged), logged)

    def scenario_live_s_links_left_behind_are_taken_out(self):
        root = Path(self.tmp.name) / f"X-Plane {time.monotonic_ns()}"
        scenery = root / "Custom Scenery"
        (root / "pack").mkdir(parents=True)
        (scenery / "someone's own").mkdir(parents=True)
        folder_link(root / "pack", scenery / "z_osxp_live")
        self.xplane(root=root)
        self.assertFalse(os.path.lexists(scenery / "z_osxp_live"))
        self.assertTrue((scenery / "someone's own").is_dir())
        self.assertTrue((root / "pack").is_dir())

    def scenario_x_plane_s_window_keeps_responding(self):
        """X-Plane held 8 s, its window open: Windows never takes it for a program that does not
        respond (IsHungAppWindow, after 5 s without a look at its messages), which would grey it
        as "Not Responding". The loading window shows over it meanwhile."""
        holds = []

        def streamer(sock):
            with contextlib.suppress(OSError):  # its socket let go of
                while True:
                    data, addr = read(sock)
                    if data.startswith(b"LIVE1 HOLD"):
                        holds.append(time.monotonic())
                        late = time.monotonic() - holds[0] > 8.0
                        sock.sendto(b"LIVE1 GO 0" if late else b"LIVE1 WAIT n=5 at=LFMN", addr)

        with live_port(self.PORT) as sock:
            threading.Thread(target=streamer, args=(sock,), daemon=True).start()
            out = subprocess.run(
                [
                    str(self.host),
                    str(self.xpl),
                    "43.6",
                    "7.2",
                    "window",
                    "102:0",
                    "112:1",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertIn("WINDOW open", out.stdout)
        self.assertIn("RESPONDED always", out.stdout)
        self.assertNotIn("HUNG", out.stdout)
        took = [
            float(line.split()[3]) for line in out.stdout.splitlines() if line.startswith("MSG 112")
        ]
        self.assertGreater(took[0], 7.5)

    def scenario_a_notice_in_flight(self):
        heard = []

        def streamer(sock):
            with contextlib.suppress(OSError):  # its socket let go of
                while True:
                    data, addr = read(sock)
                    heard.append(data)
                    if data.startswith(b"LIVE1 ASK"):
                        sock.sendto(b"LIVE1 NOTE 4 lang=fr Live : pas de maillage ici", addr)

        with live_port(self.PORT) as sock:
            threading.Thread(target=streamer, args=(sock,), daemon=True).start()
            _, logged = self.xplane("102:0", "loop", "sleep:200", "loop", "sleep:200", "loop")
        self.assertGreaterEqual(len([h for h in heard if h.startswith(b"LIVE1 ASK")]), 2)
        said = [line for line in logged if "pas de maillage" in line]
        self.assertEqual(said, ["OrthoStudio XP Live: Live : pas de maillage ici"])

    def test_the_plugin_as_x_plane_loads_it(self):
        scenarios = [
            self.scenario_without_live_x_plane_goes_on,
            self.scenario_x_plane_waits_until_live_says_go,
            self.scenario_live_s_links_left_behind_are_taken_out,
            self.scenario_a_notice_in_flight,
        ]
        if sys.platform == "win32":  # Windows' Not Responding
            scenarios.append(self.scenario_x_plane_s_window_keeps_responding)
        for scenario in scenarios:
            with self.subTest(scenario.__name__):
                scenario()


def mac_xplane(root: Path) -> Path:
    """A stand-in X-Plane laid out as X-Plane is on a Mac (tests/data/live/xpl_host.c): its program
    in ``X-Plane.app/Contents/MacOS``, linked to a stand-in XPLM framework (fake_xplm.c) in
    ``Resources/plugins`` that answers to the name of X-Plane's own. A plugin it loads finds its
    XPLM functions there, as in X-Plane. Both halves, Apple Silicon and Intel."""
    data = HERE / "data" / "live"
    both = ["-arch", "arm64", "-arch", "x86_64"]
    xplm = root / "Resources" / "plugins" / "XPLM.framework" / "XPLM"
    xplm.parent.mkdir(parents=True)
    subprocess.run(
        [CLANG, "-dynamiclib", "-DNO_MAIN", *both, "-install_name", plugin.XPLM_INSTALL_NAME,
         "-o", str(xplm), str(data / "fake_xplm.c")],
        check=True,
    )  # fmt: skip
    exe = root / "X-Plane.app" / "Contents" / "MacOS" / "X-Plane"
    exe.parent.mkdir(parents=True)
    subprocess.run([CLANG, *both, "-o", str(exe), str(data / "xpl_host.c"), str(xplm)], check=True)
    return exe


def apple_silicon() -> bool:
    """Whether this is an Apple Silicon Mac, this Python perhaps an Intel one under Rosetta."""
    if sys.platform != "darwin":
        return False
    said = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True)
    return said.stdout.strip() == "1"


@unittest.skipUnless(sys.platform == "darwin" and CLANG, "the Mac's build, with clang")
class MacBuildTest(BuiltAheadTest):
    """The Mac plugin as Live builds it, and as the Mac installer ships it (:func:`plugin.ship`),
    loaded as X-Plane loads it, by a stand-in X-Plane laid out as X-Plane is on a Mac
    (:func:`mac_xplane`): the half of this Mac's own kind, the Apple Silicon one on Apple Silicon
    even when these tests run under Rosetta (whose programs start the Intel half of a file that
    holds both). Its own port, a free one: Live may run on this Mac."""

    run_as = ("/usr/bin/arch", "-arm64") if apple_silicon() else ()

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.PORT = free_port()
        cls.host = mac_xplane(root / "X-Plane 12")
        cls.xpl = root / "built" / f"{plugin.NAME}.xpl"
        plugin.compile_plugin(cls.xpl, port=cls.PORT)


@unittest.skipUnless(runs_intel_code(), "a Mac that runs Intel code")
class IntelMacBuildTest(MacBuildTest):
    """The same file's Intel half, as on an Intel Mac: under Rosetta on Apple Silicon."""

    run_as = ("/usr/bin/arch", "-x86_64")


class EncodersTest(unittest.TestCase):
    def test_in_flight_the_encoders_leave_xplane_its_cores(self):
        """All the workers while X-Plane waits on its loading screen; four while it flies."""
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
                workers=12,
                log=lambda _: None,
                memory_pressure=lambda: 0,  # not this computer's: it failed with X-Plane flying
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
                memory_pressure=lambda: 0,  # failed with X-Plane flying beside it (2026-10-19)
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

    def test_a_camera_anywhere_changes_nothing(self):
        """X-Plane takes each texture's levels for the aircraft alone: a camera over a texture,
        low or high, near or 111 km from the aircraft, leaves the plan as the aircraft alone makes
        it (the drone and circle views of 2026-10-20, docs/specs/live/levels-test.md)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            cache = ChunkCache(root / "c.sqlite")
            lat, lon = parse_texture_name(f"{NAMES[0]}.dds").center
            s = Streamer(pack, cache, lambda on_piece: None, finest_quality=0, log=lambda _: None)
            for aircraft in ((lat - 1.0, lon, 3000.0), (lat, lon, 0.0)):
                alone = s.plan(Fix(*aircraft[:2], agl_m=aircraft[2]), x_plane_there=False)
                for cam_up in (300.0, 30_000.0):
                    fix = Fix(
                        *aircraft[:2], agl_m=aircraft[2], cam_lat=lat, cam_lon=lon, cam_agl_m=cam_up
                    )
                    self.assertEqual(s.plan(fix, x_plane_there=False), alone)
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
class FakePauser:
    """What run() reads of orthostudio.live.pause.Pauser, and its resume() noted."""

    def __init__(self):
        self.flying = False
        self.paused_for = None
        self.paused_since = 0.0
        self.resumed = False

    def resume(self):
        self.paused_for = None
        self.resumed = True


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
        self.pauser = FakePauser()
        self.lat, self.lon = parse_texture_name(f"{NAMES[0]}.dds").center
        patch = mock.patch.object(streamer_module, "PLAN_EVERY_S", 0.2)
        patch.start()
        self.addCleanup(patch.stop)
        self.run_live()

    def run_live(self, **more):
        """run() in its thread, with a streamer of its own (run() closes it at the end)."""

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
        self.thread = threading.Thread(
            target=run,
            daemon=True,
            kwargs=dict(
                streamer=self.streamer,
                xp=self.xp,
                gate=self.gate,
                should_stop=self.stop.is_set,
                log=log,
                pauser=self.pauser,
                **more,
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

    def test_a_start_prepared_by_its_name(self):
        """The Live window's Prepare (--from) with the start's name: built at once, and the plugin
        told that name while X-Plane waits (Live stopped at Prepare with the pause's own
        start_name in place of the name, 2026-11-02)."""
        self.stop.set()
        self.thread.join(30)
        self.run_live(start=Fix(self.lat, self.lon), start_name="EHAM")
        self.assertTrue(self.wait_for(lambda: any("building around EHAM" in m for m in self.log)))
        answers = self.hold(self.lat, self.lon)
        self.assertEqual(answers[-1], "LIVE1 GO 0")
        self.assertTrue(all(a.endswith(" at=EHAM") for a in answers[:-1]), answers)

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

    def test_a_new_location_in_flight_paused_until_built(self):
        """A new location chosen in flight loads no aircraft, so the plugin holds nothing: Live's
        pause keeps X-Plane (orthostudio.live.pause), for the start X-Plane wrote at the click,
        until everything X-Plane will read there is built, then lets it go."""
        home = parse_texture_name(f"{NAMES[1]}.dds").center
        self.xp.position = (home[0] - 2.0, home[1])  # flying 220 km south
        self.assertTrue(self.wait_for(lambda: self.pauser.flying))
        self.click()  # X-Plane writes the new start...
        self.pauser.paused_since = time.time()  # ... and Live pauses it at once
        self.pauser.paused_for = last_start(self.prefs)
        self.assertTrue(self.wait_for(lambda: self.pauser.resumed))
        self.assertEqual(self.streamer.built, {f"{n}.dds": 1 for n in NAMES})
        said = [m for m in self.log if m.startswith("  X-Plane goes on after ")]
        self.assertTrue(said and said[0].endswith("is built"), self.log)

    def test_a_paused_start_never_read_lets_xplane_go(self):
        """X-Plane paused for a start Live cannot read: never kept for nothing."""
        self.pauser.paused_since = time.time() - streamer_module.PAUSE_UNREAD_S - 1
        self.pauser.paused_for = '_last_start {"runway_start":{"airport_id":"ZZZZ","runway":"01"}}'
        self.assertTrue(self.wait_for(lambda: self.pauser.resumed))
        self.assertTrue(any("was not read" in m for m in self.log), self.log)

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
            p.send(f"LIVE1 HOLD {self.lat:.6f} {self.lon:.6f} aircraft".encode())
            self.assertTrue(p.recv(100).startswith(b"LIVE1 WAIT"))
        answers = self.hold(self.lat, self.lon)
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


class RefusedDownloader(SilentDownloader):
    """Asked for pieces, answers none: every request refused, as curl said it."""

    last_error = "SSLError: Failed to perform, curl: (77) error adding trust anchors"


class DeniedDownloader(SilentDownloader):
    """Asked for pieces, answers none: the source's server refuses this computer (403)."""

    def refusing(self):
        return ["Esri World Imagery"]


class NoTimeLimitTest(unittest.TestCase):
    """The plugin holds X-Plane as long as pieces arrive, with no time limit; once none has arrived
    for STALL_S, the streamer lets X-Plane go, and reloads the scenery once all is built."""

    def test_nothing_downloads_any_more(self):
        answers, took, logged = self.fly(SilentDownloader)
        self.assertTrue(answers[0].startswith("LIVE1 WAIT"), answers[:3])
        self.assertTrue(answers[-1].startswith("LIVE1 GO"), answers[-3:])
        self.assertNotEqual(answers[-1], "LIVE1 GO 0")  # textures were left to build
        self.assertGreater(took, 1.5)  # held while pieces could still come
        stalled = [line for line in logged if "nothing downloaded for" in line]
        self.assertTrue(stalled, logged[-5:])
        self.assertIn("(is the internet there?)", stalled[0])

    def test_the_reason_nothing_downloads(self):
        """The internet there, but every request refused (on the user's Windows, 2026-10-04,
        curl could not open its certificates' file): the message says how they fail."""
        answers, _took, logged = self.fly(RefusedDownloader)
        self.assertTrue(answers[-1].startswith("LIVE1 GO"), answers[-3:])
        stalled = [line for line in logged if "nothing downloaded for" in line]
        self.assertTrue(stalled, logged[-5:])
        self.assertIn(f"(the requests fail: {RefusedDownloader.last_error})", stalled[0])

    def test_a_source_refusing_this_computer(self):
        """Its server answers every request 403, as Esri's edge did to a user's address
        (2026-10-07): the message names it, where it asked whether the internet was there."""
        answers, _took, logged = self.fly(DeniedDownloader)
        self.assertTrue(answers[-1].startswith("LIVE1 GO"), answers[-3:])
        stalled = [line for line in logged if "nothing downloaded for" in line]
        self.assertTrue(stalled, logged[-5:])
        self.assertIn("(Esri World Imagery refuses this computer: access denied)", stalled[0])

    def fly(self, downloader):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            cache = ChunkCache(root / "c.sqlite")
            port = free_port()
            gate = Gate(root / "X-Plane 12", lambda icao: None, port=port)
            logged = []
            s = Streamer(pack, cache, downloader, finest_quality=1, workers=1, log=logged.append)
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
        return answers, took, logged


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
