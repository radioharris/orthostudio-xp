# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The region pack and the streamer's loop, with stand-ins for the imagery and X-Plane."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from live_helpers import NAMES, FakeDownloader, Image, jpeg_body, make_source
from orthostudio.live import dds
from orthostudio.live.cache import ChunkCache
from orthostudio.live.geo import parse_texture_name
from orthostudio.live.region import NEUTRAL_FILE, REGION_MARKER, setup_region
from orthostudio.live.streamer import Fix, Streamer, TextureIndex, ahead
from orthostudio.live.texture import piece_keys


class RegionTest(unittest.TestCase):
    def test_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = make_source(root, tile=(45, 6), textures=NAMES)
            pack = root / "stream_pack"
            (pack / "textures").mkdir(parents=True)
            (pack / "textures" / f"{NAMES[0]}.dds").write_bytes(b"built already")
            meta = setup_region(src, pack, log=lambda _: None)
            self.assertEqual(meta["textures"], 2)
            self.assertTrue((pack / "Earth nav data" / "+40+000" / "+45+006.dsf").exists())
            self.assertTrue((pack / "terrain" / f"{NAMES[1]}_water_overlay.ter").exists())
            self.assertTrue((pack / "textures" / "water_transition.png").exists())
            self.assertEqual((pack / "textures" / f"{NAMES[0]}.dds").read_bytes(), b"built already")
            neutral = (pack / "textures" / f"{NAMES[1]}.dds").read_bytes()
            self.assertEqual(neutral, (pack / NEUTRAL_FILE).read_bytes())
            self.assertEqual(len(neutral), dds.file_size())
            before = (pack / REGION_MARKER).stat().st_mtime_ns
            setup_region(src, pack, log=lambda _: None)  # already done: nothing redone
            self.assertEqual((pack / REGION_MARKER).stat().st_mtime_ns, before)
            # the neutral texture is complete: every level readable, none a hole (black)
            layout = dds.mip_layout()
            for start, length in layout[:4]:
                self.assertNotEqual(set(neutral[start : start + min(length, 4096)]), {0})

    def test_upgrade_from_version_1(self):
        """A pack set up with the first, holed neutral texture is brought up to date once."""
        import json

        from orthostudio.live.region import neutral_levels
        from orthostudio.live.texture import write_sparse

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = make_source(root, tile=(45, 6), textures=NAMES)
            pack = root / "stream_pack"
            setup_region(src, pack, log=lambda _: None)
            # make it look like version 1: a holed neutral clone, a built texture with a hole above
            # it
            write_sparse(pack / "textures" / f"{NAMES[1]}.dds", 4, neutral_levels(4))
            built = pack / "textures" / f"{NAMES[0]}.dds"
            write_sparse(built, 2, [bytes([9]) * length for _, length in dds.mip_layout()[2:]])
            (pack / "_live" / "ortho.json").write_text(
                json.dumps({"textures": {f"{NAMES[0]}.dds": 2}})
            )
            meta = json.loads((pack / REGION_MARKER).read_text())
            meta.pop("version")
            (pack / REGION_MARKER).write_text(json.dumps(meta))
            logged = []
            setup_region(src, pack, log=logged.append)
            self.assertTrue(any("never black" in line for line in logged))
            self.assertEqual(json.loads((pack / REGION_MARKER).read_text())["version"], 2)
            layout = dds.mip_layout()
            clone_ = (pack / "textures" / f"{NAMES[1]}.dds").read_bytes()
            self.assertEqual(clone_, (pack / NEUTRAL_FILE).read_bytes())
            upgraded = built.read_bytes()
            self.assertNotEqual(
                set(upgraded[layout[0][0] : layout[0][0] + 4096]), {0}
            )  # neutral, not a hole
            self.assertEqual(
                set(upgraded[layout[2][0] : layout[2][0] + 4096]), {9}
            )  # its own level kept


class PlanningTest(unittest.TestCase):
    def test_ahead_and_index(self):
        lat, lon = ahead(Fix(45.0, 6.0, gs_ms=100.0, trk_deg=0.0), 10)  # 60 km north
        self.assertAlmostEqual(lat, 45.0 + 60 / 111.195, places=4)
        self.assertAlmostEqual(lon, 6.0, places=6)
        idx = TextureIndex([f"{n}.dds" for n in NAMES] + ["not_a_texture.dds"])
        self.assertEqual(len(idx), 2)
        c = parse_texture_name(f"{NAMES[0]}.dds").center
        self.assertEqual(len(list(idx.around(*c, 50))), 2)
        self.assertEqual(len(list(idx.around(c[0] + 5, c[1], 50))), 0)


class FakeXPlane:
    def __init__(self):
        self.commands = []

    def command(self, name):
        self.commands.append(name)


class BrokenDownloader:
    def __init__(self, on_piece):
        pass

    def submit(self, key, priority):
        raise RuntimeError("no network")

    def close(self):
        pass


class CrashTest(unittest.TestCase):
    def test_a_dead_builder_stops_the_streamer(self):
        from orthostudio.live.streamer import run

        class QuietXPlane:
            def subscribe(self):
                pass

            def receive(self, timeout):
                time.sleep(timeout)
                return None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            logged = []
            s = Streamer(
                pack,
                ChunkCache(root / "c.sqlite"),
                BrokenDownloader,
                finest_quality=1,
                workers=1,
                log=logged.append,
            )
            start = Fix(*parse_texture_name(f"{NAMES[0]}.dds").center)
            deadline = time.time() + 30
            with self.assertRaises(SystemExit) as cm:
                try:
                    run(
                        s,
                        QuietXPlane(),
                        start,
                        should_stop=lambda: time.time() > deadline,
                        log=lambda _: None,
                    )
                finally:
                    s.cache.close()
        self.assertIn("no network", str(cm.exception))
        self.assertTrue(any("the builder stopped" in line for line in logged))


@unittest.skipIf(Image is None, "needs numpy, Pillow and ispc_texcomp")
class StreamerTest(unittest.TestCase):
    def test_builds_what_xplane_will_read_then_asks_for_a_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = make_source(root, tile=(45, 6), textures=NAMES)
            pack = root / "stream_pack"
            setup_region(src, pack, log=lambda _: None)
            xp = FakeXPlane()
            s = Streamer(
                pack,
                ChunkCache(root / "c.sqlite"),
                FakeDownloader,
                finest_quality=1,
                workers=2,
                xplane=xp,
                log=lambda _: None,
            )
            lat, lon = parse_texture_name(f"{NAMES[0]}.dds").center
            jobs = s.plan(Fix(lat, lon))
            self.assertEqual(
                {n: lv for n, (_, lv) in jobs.items()}, {f"{NAMES[0]}.dds": 1, f"{NAMES[1]}.dds": 1}
            )
            self.assertTrue(s.reload_pending)
            s.start()
            deadline = time.time() + 60
            while (s.urgent_left() or s.reload_pending) and time.time() < deadline:
                time.sleep(0.1)
            s.close()
            s.cache.close()
            self.assertEqual(s.built, {f"{NAMES[0]}.dds": 1, f"{NAMES[1]}.dds": 1})
            self.assertEqual(xp.commands, ["sim/operation/reload_scenery"])
            path = pack / "textures" / f"{NAMES[0]}.dds"
            self.assertGreater(os.stat(path).st_blocks * 512, 2_000_000)  # 2048 px written
            self.assertEqual(
                json.loads((pack / "_live" / "ortho.json").read_text())["textures"], s.built
            )
            self.assertEqual(s.plan(Fix(lat, lon)), {})  # nothing left to do there


@unittest.skipIf(Image is None, "needs numpy, Pillow and ispc_texcomp")
class NoHeadOfLineTest(unittest.TestCase):
    def test_a_slow_piece_delays_only_its_texture(self):
        """The first streamer waited for a whole batch: one slow piece stalled everything for 40
        s."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            slow_tex, _fast_tex = (parse_texture_name(f"{n}.dds") for n in NAMES)
            slow_piece = piece_keys(slow_tex, 1)[0]
            made = []

            def factory(on_piece):
                d = FakeDownloader(on_piece, slow=[slow_piece], delay=4.0)
                made.append(d)
                return d

            s = Streamer(
                pack,
                ChunkCache(root / "c.sqlite"),
                factory,
                finest_quality=1,
                workers=2,
                log=lambda _: None,
            )
            s.plan(Fix(*slow_tex.center), x_plane_there=False)
            t0 = time.time()
            s.start()
            while f"{NAMES[1]}.dds" not in s.built and time.time() - t0 < 30:
                time.sleep(0.05)
            fast_done = time.time() - t0
            while f"{NAMES[0]}.dds" not in s.built and time.time() - t0 < 30:
                time.sleep(0.05)
            slow_done = time.time() - t0
            s.close()
            s.cache.close()
        self.assertLess(fast_done, 3.5, "the fast texture waited for the slow piece")
        self.assertGreaterEqual(slow_done, 4.0)
        self.assertEqual(made[0].asked.count(slow_piece), 1)


class _Server:
    """A local tile server: an image, no imagery (404), a 429 then an image, a slow image."""

    def __init__(self, body):
        import http.server
        import threading

        self.hits = {}
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                _, z, x, y = self.path.split("/")
                key = (int(z), int(x), int(y))
                server.hits[key] = server.hits.get(key, 0) + 1
                if key == (15, 1, 1):
                    self.send_response(404)
                    self.end_headers()
                    return
                if key == (15, 2, 2) and server.hits[key] == 1:
                    self.send_response(429)
                    self.send_header("Retry-After", "1")
                    self.end_headers()
                    return
                if key == (15, 3, 3):
                    time.sleep(2.0)
                if key == (15, 4, 4) and server.hits[key] == 1:
                    time.sleep(6.0)  # a stuck answer: a second server answers at once
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@unittest.skipIf(Image is None, "needs numpy, Pillow and ispc_texcomp")
class DownloaderTest(unittest.TestCase):
    def test_pieces_arrive_as_they_come(self):
        import queue

        from orthostudio.live.cache import IMAGE, NO_IMAGERY
        from orthostudio.live.downloader import Downloader
        from orthostudio.live.providers import Provider

        body = jpeg_body()
        server = _Server(body)
        p = Provider(
            code="LOCAL",
            url_template=f"http://127.0.0.1:{server.port}/{{zoom}}/{{x}}/{{y}}",
            max_zl=19,
        )
        got = queue.Queue()
        d = Downloader(p, lambda k, st, b: got.put((time.time(), k, st, b)), max_in_flight=8)
        try:
            t0 = time.time()
            keys = [(15, 3, 3), (15, 1, 1), (15, 2, 2)] + [(15, 10 + i, 10) for i in range(20)]
            for i, k in enumerate(keys):
                d.submit(k, float(i))
            answers = {}
            order = []
            while len(answers) < len(keys) and time.time() - t0 < 20:
                _t, k, st, b = got.get(timeout=20)
                answers[k] = (st, b)
                order.append(k)
        finally:
            d.close()
            server.close()
        self.assertEqual(answers[(15, 1, 1)], (NO_IMAGERY, None))
        self.assertEqual(answers[(15, 2, 2)], (IMAGE, body))  # 429, paused, asked again
        self.assertEqual(server.hits[(15, 2, 2)], 2)
        self.assertEqual(answers[(15, 3, 3)], (IMAGE, body))
        self.assertEqual(order[-1], (15, 3, 3))  # the slow one did not hold the others back
        self.assertEqual(d.downloaded, 22)

    def test_a_stuck_piece_is_asked_again_elsewhere(self):
        import queue
        from unittest import mock

        from orthostudio.live import downloader
        from orthostudio.live.cache import IMAGE
        from orthostudio.live.providers import Provider

        body = jpeg_body()
        server = _Server(body)
        p = Provider(
            code="LOCAL",
            url_template=f"http://127.0.0.1:{server.port}/{{zoom}}/{{x}}/{{y}}",
            max_zl=19,
        )
        got = queue.Queue()
        with mock.patch.object(downloader, "HEDGE_S", 0.5):
            d = downloader.Downloader(
                p, lambda k, st, b: got.put((time.time(), st)), max_in_flight=4
            )
            try:
                t0 = time.time()
                d.submit((15, 4, 4), 0.0)
                t, st = got.get(timeout=20)
            finally:
                d.close()
                server.close()
        self.assertEqual(st, IMAGE)
        self.assertLess(t - t0, 3.0)  # not the 6 s of the stuck answer
        self.assertEqual(server.hits[(15, 4, 4)], 2)
        self.assertEqual((d.hedged, d.downloaded), (1, 1))


if __name__ == "__main__":
    unittest.main()
