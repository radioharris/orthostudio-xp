# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The region pack and the streamer's loop, with stand-ins for the imagery and X-Plane."""

import json
import math
import os
import tempfile
import time
import unittest
from pathlib import Path

from live_helpers import NAMES, FakeDownloader, Image, jpeg_body, make_source, make_squares
from orthostudio.live import dds
from orthostudio.live.cache import ChunkCache
from orthostudio.live.geo import parse_texture_name
from orthostudio.live.region import NEUTRAL_FILE, REGION_MARKER, REGION_VERSION, setup_region
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
            self.assertEqual(meta["version"], REGION_VERSION)
            self.assertTrue((pack / "Earth nav data" / "+40+000" / "+45+006.dsf").exists())
            self.assertTrue((pack / "textures" / "water_transition.png").exists())
            water = pack / "terrain" / f"{NAMES[1]}_water_overlay.ter"
            self.assertFalse(water.exists())  # made with its square, round the aircraft
            make_squares(pack)
            self.assertTrue(water.exists())
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

    def _pack_of_before(self, root: Path, version: int | None) -> tuple[Path, Path, Path]:
        """A pack as an older Live left it: every square's files, a texture built to 2 (holed
        above it with version 1), the other a neutral name (holed with version 1)."""
        from orthostudio.live.region import neutral_levels
        from orthostudio.live.texture import write_sparse

        src = make_source(root, tile=(45, 6), textures=NAMES)
        pack = root / "stream_pack"
        setup_region(src, pack, log=lambda _: None)
        make_squares(pack)
        if version == 1:
            write_sparse(pack / "textures" / f"{NAMES[1]}.dds", 4, neutral_levels(4))
        built = pack / "textures" / f"{NAMES[0]}.dds"
        write_sparse(built, 2, [bytes([9]) * length for _, length in dds.mip_layout()[2:]])
        (pack / "_live" / "ortho.json").write_text(json.dumps({"textures": {built.name: 2}}))
        meta = json.loads((pack / REGION_MARKER).read_text())
        if version is None or version == 1:
            meta.pop("version")
        else:
            meta["version"] = version
        (pack / REGION_MARKER).write_text(json.dumps(meta))
        return src, pack, built

    def test_upgrade_from_version_1(self):
        """A pack set up with the first, holed neutral texture and every square's files is
        brought up to date once: its built textures never black, the rest made per square."""
        with tempfile.TemporaryDirectory() as tmp:
            src, pack, built = self._pack_of_before(Path(tmp), 1)
            logged = []
            setup_region(src, pack, log=logged.append)
            self.assertTrue(any("never black" in line for line in logged))
            self.assertTrue(any("made per square" in line for line in logged))
            self.assertEqual(json.loads((pack / REGION_MARKER).read_text())["version"], 3)
            self.assertFalse((pack / "textures" / f"{NAMES[1]}.dds").exists())
            self.assertEqual(list((pack / "terrain").iterdir()), [])
            self.assertTrue((pack / "textures" / "water_transition.png").exists())
            layout = dds.mip_layout()
            upgraded = built.read_bytes()
            self.assertNotEqual(
                set(upgraded[layout[0][0] : layout[0][0] + 4096]), {0}
            )  # neutral, not a hole
            self.assertEqual(
                set(upgraded[layout[2][0] : layout[2][0] + 4096]), {9}
            )  # its own level kept

    def test_upgrade_waits_for_xplane(self):
        """A pack of version 2, every square's files: taken away only once X-Plane is not running,
        which may be reading them; meanwhile the streamer finds them all there."""
        with tempfile.TemporaryDirectory() as tmp:
            src, pack, built = self._pack_of_before(Path(tmp), 2)
            other = pack / "textures" / f"{NAMES[1]}.dds"
            logged = []
            setup_region(src, pack, log=logged.append, xplane_running=lambda: True)
            self.assertTrue(any("while X-Plane runs" in line for line in logged))
            self.assertEqual(json.loads((pack / REGION_MARKER).read_text())["version"], 2)
            self.assertTrue(other.exists() and (pack / "terrain" / f"{NAMES[1]}.ter").exists())
            cache = ChunkCache(Path(tmp) / "c.sqlite")
            s = Streamer(pack, cache, FakeDownloader, finest_quality=0, log=lambda _: None)
            self.assertIsNone(s.names)  # nothing made nor taken away: every file is there
            self.assertEqual(len(s.index), 2)
            cache.close()
            setup_region(src, pack, log=lambda _: None)  # X-Plane has quit
            self.assertEqual(json.loads((pack / REGION_MARKER).read_text())["version"], 3)
            self.assertFalse(other.exists())
            self.assertTrue(built.exists())


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


class NoticeTest(unittest.TestCase):
    """Where the pack has no mesh but one of the regions has: the region to install, for the
    window and X-Plane; nothing over the pack's mesh, nor where no region has any."""

    def test_the_region_to_install(self):
        from orthostudio.live.streamer import notice_text, progress_report

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            installed = set()
            s = Streamer(
                pack,
                ChunkCache(root / "c.sqlite"),
                FakeDownloader,
                finest_quality=1,
                workers=1,
                log=lambda _: None,
                installed_regions=lambda: installed,
            )
            try:
                here = parse_texture_name(f"{NAMES[0]}.dds").center
                self.assertIsNone(s.notice_at(*here))  # the pack's mesh
                self.assertIsNone(s.notice_at(40.0, -40.0))  # the open Atlantic: nothing to install
                notice = s.notice_at(40.64, -73.78)  # New York
                self.assertEqual((notice["region"], notice["installed"]), ("na", False))
                self.assertEqual(
                    notice_text(notice), "no mesh here: install North America in the Live window"
                )
                self.assertIs(s.notice_at(40.7, -73.9), notice)  # the same square: the same notice
                installed.add("asi")
                tokyo = s.notice_at(35.55, 139.78)  # another region: another notice
                self.assertNotEqual(tokyo["id"], None)
                self.assertEqual((tokyo["region"], tokyo["installed"]), ("asi", True))
                self.assertEqual(
                    notice_text(tokyo), "Asia is installed: used from Live's next start"
                )
                report = progress_report(
                    s, {"lat": 35.55, "lon": 139.78}, None, time.time(), time.time(), 0.0
                )
                self.assertEqual(report["notice"]["region"], "asi")  # the aircraft's, to the window
                self.assertIsNone(s.notice_at(None, None))
            finally:
                s.cache.close()


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
    def test_builds_what_xplane_will_read_then_says_so(self):
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
            self.assertTrue(s.read_early)
            s.start()
            deadline = time.time() + 60
            while (s.urgent_left() or s.read_early) and time.time() < deadline:
                time.sleep(0.1)
            s.close()
            s.cache.close()
            self.assertEqual(s.built, {f"{NAMES[0]}.dds": 1, f"{NAMES[1]}.dds": 1})
            self.assertEqual(xp.commands, [])  # no reload: X-Plane's keeps what it holds
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


class FailingDownloader(FakeDownloader):
    """Pieces in ``fail`` fail every try, once each (FAILED), then come as any."""

    def __init__(self, on_piece, fail=()):
        super().__init__(on_piece)
        self.fail = set(fail)

    def submit(self, key, priority):
        if key in self.fail:
            self.fail.discard(key)
            self.asked.append(key)
            self.on_piece(key, 3, None)  # orthostudio.live.downloader.FAILED
            return
        super().submit(key, priority)


@unittest.skipIf(Image is None, "needs numpy, Pillow and ispc_texcomp")
class MissingPieceTest(unittest.TestCase):
    """A piece that does not come: the texture is built without it, from the zoom below, and
    built again later with it. X-Plane waits for no piece more than STUCK_S (a start at LSGG waited
    20 s for one)."""

    def streamer(self, root: Path, factory) -> Streamer:
        pack = root / "stream_pack"
        setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
        return Streamer(
            pack,
            ChunkCache(root / "c.sqlite"),
            factory,
            finest_quality=1,
            workers=2,
            log=lambda _: None,
        )

    def wait(self, test, timeout=30.0):
        end = time.time() + timeout
        while not test() and time.time() < end:
            time.sleep(0.05)
        return test()

    def test_a_stuck_piece_while_xplane_waits(self):
        from unittest import mock

        from orthostudio.live import streamer as streamer_module

        tex = parse_texture_name(f"{NAMES[0]}.dds")
        stuck = piece_keys(tex, 1)[0]
        name = f"{NAMES[0]}.dds"
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(streamer_module, "STUCK_S", 0.5),
            mock.patch.object(streamer_module, "PATCH_RETRY_S", 1.0),
        ):
            s = self.streamer(Path(tmp), lambda on: FakeDownloader(on, slow=[stuck], delay=3.0))
            jobs = s.plan(Fix(*tex.center), x_plane_there=False, starting=True)
            self.assertLessEqual(jobs[name][0], 0)  # what X-Plane waits for
            t0 = time.time()
            s.start()
            self.assertTrue(self.wait(lambda: name in s.built))
            self.assertLess(time.time() - t0, 2.5)  # not the 3 s the piece takes
            self.assertIn(name, s.patchy)
            self.assertEqual(s.urgent_left(), 0)  # X-Plane goes on
            time.sleep(3.0 - (time.time() - t0) + 0.2)  # the piece comes, into the cache
            self.assertTrue(self.wait(lambda: s.patchy[name][0] <= time.time()))
            again = s.plan(Fix(*tex.center), x_plane_there=False)
            self.assertEqual(again[name], (1.0, 1))  # the same level, never waited for
            self.assertTrue(self.wait(lambda: name not in s.patchy))
            s.close()
            s.cache.close()
            meta = json.loads((s.pack_dir / "_live" / "ortho.json").read_text())
            self.assertEqual(meta["patchy"], [])

    def test_a_piece_that_failed_is_asked_again_later(self):
        from unittest import mock

        from orthostudio.live import streamer as streamer_module

        tex = parse_texture_name(f"{NAMES[0]}.dds")
        failing = piece_keys(tex, 1)[0]
        name = f"{NAMES[0]}.dds"
        made = []

        def factory(on_piece):
            made.append(FailingDownloader(on_piece, fail=[failing]))
            return made[-1]

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(streamer_module, "PATCH_RETRY_S", 0.5),
        ):
            s = self.streamer(Path(tmp), factory)
            s.plan(Fix(*tex.center), x_plane_there=False, starting=True)
            s.start()
            self.assertTrue(self.wait(lambda: name in s.patchy))
            s.save()
            meta = json.loads((s.pack_dir / "_live" / "ortho.json").read_text())
            self.assertEqual(meta["patchy"], [name])  # remembered, should Live stop now
            self.assertTrue(self.wait(lambda: s.patchy[name][0] <= time.time()))
            s.plan(Fix(*tex.center), x_plane_there=False)
            self.assertTrue(self.wait(lambda: name not in s.patchy))
            self.assertEqual(made[0].asked.count(failing), 2)  # failed, then asked again
            s.close()
            s.cache.close()


class _Server:
    """A local tile server: an image, no imagery (404), a 429 then an image, a slow image."""

    def __init__(self, body, missing=((15, 1, 1),)):
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
                if key in missing:
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

    def test_while_x_plane_is_busy_only_what_it_shows_is_asked_for(self):
        """Live stopped for X-Plane's background workers (Streamer.holds, 2026-10-01): a piece
        ahead waits, put back in its place, and one X-Plane shows passes it, with a single
        request at once."""
        import queue

        from orthostudio.live.downloader import Downloader
        from orthostudio.live.providers import Provider

        server = _Server(jpeg_body())
        p = Provider(
            code="LOCAL",
            url_template=f"http://127.0.0.1:{server.port}/{{zoom}}/{{x}}/{{y}}",
            max_zl=19,
        )
        got: queue.Queue = queue.Queue()
        busy = [True]
        d = Downloader(p, lambda k, st, b: got.put(k), max_in_flight=1, fallback=None)
        d.hold = lambda priority: busy[0] and priority > 0
        try:
            d.submit((15, 20, 20), 5.0)  # ahead of the aircraft
            time.sleep(0.5)
            d.submit((15, 21, 21), -1.0)  # X-Plane shows it
            self.assertEqual(got.get(timeout=10), (15, 21, 21))
            with self.assertRaises(queue.Empty):
                got.get(timeout=1.0)
            busy[0] = False  # X-Plane's workers free again
            self.assertEqual(got.get(timeout=10), (15, 20, 20))
        finally:
            d.close()
            server.close()

    def test_an_unexpected_error_fails_its_piece_only(self):
        """An error that is not the network's (a source's address that cannot be made) fails that
        piece; the request it ran in goes on with the next pieces, where it died with it until
        2026-09-30, one of the 128 fewer each time."""
        import queue
        from unittest import mock

        from orthostudio.live import downloader as downloader_module
        from orthostudio.live.cache import IMAGE
        from orthostudio.live.downloader import FAILED, Downloader
        from orthostudio.live.providers import Provider

        body = jpeg_body()
        server = _Server(body)
        p = Provider(
            code="LOCAL",
            url_template=f"http://127.0.0.1:{server.port}/{{zoom}}/{{x}}/{{y}}",
            max_zl=19,
        )
        real_url = downloader_module.tile_url

        def tile_url(provider, x, y, z, server_index):
            if (z, x, y) == (15, 7, 7):
                raise KeyError("a bug")
            return real_url(provider, x, y, z, server_index)

        got, logged = queue.Queue(), []
        with mock.patch.object(downloader_module, "tile_url", tile_url):
            d = Downloader(p, lambda k, st, b: got.put((k, st)), max_in_flight=1, log=logged.append)
            try:
                for i, k in enumerate([(15, 7, 7), (15, 8, 8), (15, 9, 9)]):
                    d.submit(k, float(i))
                answers = dict(got.get(timeout=20) for _ in range(3))
            finally:
                d.close()
                server.close()
        self.assertEqual(answers[(15, 7, 7)], FAILED)
        self.assertEqual(answers[(15, 8, 8)], IMAGE)  # the one request left went on
        self.assertEqual(answers[(15, 9, 9)], IMAGE)
        self.assertEqual(len(logged), 1)

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

    def test_bing_beyond_a_country_source(self):
        """Outside a country's bounds, a piece is asked of the fallback only; one the country's
        source has no imagery for (404) is asked of the fallback next."""
        import queue

        from orthostudio.live.cache import IMAGE
        from orthostudio.live.downloader import Downloader
        from orthostudio.live.providers import Provider

        body = jpeg_body()
        country, world = _Server(body), _Server(body, missing=())
        corner = -180 + 8 / 2**15 * 360  # ZL15 tiles 0-7 from the grid's top left corner
        lat0 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * 8 / 2**15))))
        inside = Provider(
            code="C",
            url_template=f"http://127.0.0.1:{country.port}/{{zoom}}/{{x}}/{{y}}",
            max_zl=19,
            extent="Testland",
            extent_bounds=(-180.0, lat0, corner, 85.05),
        )
        elsewhere = Provider(
            code="W", url_template=f"http://127.0.0.1:{world.port}/{{zoom}}/{{x}}/{{y}}", max_zl=19
        )
        got = queue.Queue()
        d = Downloader(
            inside, lambda k, st, b: got.put((k, st)), max_in_flight=4, fallback=elsewhere
        )
        try:
            keys = [(15, 1, 1), (15, 5, 5), (15, 20, 20)]  # no imagery, inside, outside
            for i, k in enumerate(keys):
                d.submit(k, float(i))
            answers = dict(got.get(timeout=20) for _ in keys)
        finally:
            d.close()
            country.close()
            world.close()
        self.assertEqual(answers, dict.fromkeys(keys, IMAGE))
        self.assertEqual(set(country.hits), {(15, 1, 1), (15, 5, 5)})
        self.assertEqual(set(world.hits), {(15, 1, 1), (15, 20, 20)})

    def test_a_server_with_a_ceiling_is_asked_no_faster(self):
        import queue

        from orthostudio.live.downloader import Downloader
        from orthostudio.live.providers import Provider

        server = _Server(jpeg_body())
        p = Provider(
            code="SLOW",
            url_template=f"http://127.0.0.1:{server.port}/{{zoom}}/{{x}}/{{y}}",
            max_zl=19,
            server_req_per_s=20,
        )
        got = queue.Queue()
        d = Downloader(p, lambda k, st, b: got.put(k), max_in_flight=8, fallback=None)
        try:
            t0 = time.time()
            for i in range(10):
                d.submit((15, 10 + i, 10), float(i))
            for _ in range(10):
                got.get(timeout=20)
            took = time.time() - t0
        finally:
            d.close()
            server.close()
        self.assertGreater(took, 0.4)  # 10 requests at 20 a second: 9 gaps of 50 ms at least


if __name__ == "__main__":
    unittest.main()
