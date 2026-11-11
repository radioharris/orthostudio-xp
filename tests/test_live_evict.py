# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The disk budget, and the camera away from the aircraft."""

import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path

from live_helpers import disk_clones, file_extents, make_source
from orthostudio.live import dds
from orthostudio.live.cache import IMAGE, ChunkCache
from orthostudio.live.evict import (
    XPlaneWindow,
    pick,
    texture_bytes,
    tiles_of,
    usage,
    xplane_tiles,
)
from orthostudio.live.filesystem import allocated
from orthostudio.live.geo import Texture, parse_texture_name
from orthostudio.live.region import NEUTRAL_FILE, NEUTRAL_LEVEL, setup_region
from orthostudio.live.streamer import Fix, Streamer, camera_position
from orthostudio.live.texture import downgrade, write_sparse

NAMES = ("23296_33152_BI16", "23296_33168_BI16")


def levels_from(top):
    return [bytes([lvl]) * length for lvl, (_, length) in enumerate(dds.mip_layout())][top:]


class PickTest(unittest.TestCase):
    def test_least_recently_seen_far_ones_first(self):
        built = {"a": 0, "b": 0, "c": 1, "near": 0}
        seen = {"a": 10.0, "b": 5.0, "c": 1.0, "near": 0.0}
        far = lambda n: n != "near"  # noqa: E731
        budget = usage(built) - texture_bytes(0) // 2  # a little over: one sharp texture must go
        self.assertEqual(pick(built, seen, far, budget)[0], ("c", 4))  # oldest first, near never
        self.assertEqual(pick(built, seen, far, 10**12), [])
        everything = pick(built, seen, far, 1)
        self.assertEqual([n for n, _ in everything], ["c", "b", "a"])  # then nothing small is left
        self.assertNotIn("near", [n for n, _ in everything])

    def test_behind_the_aircraft_first(self):
        """On a way back the oldest built are ahead of the aircraft: those behind go first."""
        built = {"ahead_old": 0, "behind_new": 0}
        seen = {"ahead_old": 1.0, "behind_new": 9.0}
        budget = usage(built) - texture_bytes(0) // 2  # one must go
        picked = pick(built, seen, lambda n: True, budget, behind=lambda n: n.startswith("behind"))
        self.assertEqual(picked[0], ("behind_new", 4))

    def test_small_ones_go_to_neutral_last(self):
        built = {"x": 4, "y": 4}
        self.assertEqual(
            pick(built, {"x": 2.0, "y": 1.0}, lambda n: True, 1),
            [("y", NEUTRAL_LEVEL), ("x", NEUTRAL_LEVEL)],
        )


class XPlaneWindowTest(unittest.TestCase):
    """The DSF tiles X-Plane holds, as the measurement flight and three starts showed them
    (2026-09-29)."""

    def window(self, rows, columns):
        return {(r, c) for r in rows for c in columns}

    def test_the_starts(self):
        for lat, lon, rows, columns in (
            (43.637, 1.358, range(42, 45), range(-1, 3)),  # LFBO: -1..2, 42..44
            (40.472, -3.561, range(39, 42), range(-6, -2)),  # LEMD
            (41.297, 2.078, range(40, 43), range(0, 4)),  # LEBL
        ):
            self.assertLessEqual(self.window(rows, columns), xplane_tiles(lat, lon), (lat, lon))

    def test_the_flight_east_and_back(self):
        # east: column 3 loaded at 1.65, column 5 at 3.65; columns 0 and 1 let go on the way
        self.assertIn((43, 3), xplane_tiles(43.637, 1.651))
        at_3_65 = xplane_tiles(43.63, 3.65)
        self.assertLessEqual(self.window(range(42, 45), range(2, 6)), at_3_65)
        self.assertNotIn((43, 0), at_3_65)  # let go of at 2.65: loaded again at 2.35
        # back west: column 1 loaded again at 3.35, column 0 at 2.35
        self.assertIn((43, 1), xplane_tiles(43.52, 3.349))
        at_2_2 = xplane_tiles(43.52, 2.2)
        self.assertIn((43, 0), at_2_2)
        self.assertNotIn((43, 5), at_2_2)
        self.assertNotIn((43, -1), xplane_tiles(43.52, 2.9))  # far behind: let go of
        # behind, at 43.6 N, 213 km at most: 2.65 columns
        self.assertNotIn((43, 0), xplane_tiles(43.6, 3.2))

    def test_the_rows(self):
        self.assertLessEqual(self.window(range(42, 45), [1]), xplane_tiles(43.5, 1.2))
        self.assertNotIn((45, 1), xplane_tiles(43.5, 1.2))
        self.assertIn((45, 1), xplane_tiles(43.9, 1.2))  # its lag, near a row's edge

    def test_where_x_plane_moves_its_window(self):
        """Norway, 2026-09-30: columns moved at 10.65, 11.65, 12.65 east, 12.35, 11.35, 10.35
        back west; rows at 61.1 north, 60.9 back south."""
        w = XPlaneWindow()
        self.assertFalse(w.follow(60.5, 10.4))  # the start: taken, not moved
        self.assertEqual((w.column, w.row), (10, 60))
        for lon, moved, column in ((10.64, False, 10), (10.66, True, 11), (11.66, True, 12)):
            self.assertEqual(w.follow(60.5, lon), moved, lon)
            self.assertEqual(w.column, column, lon)
        for lon, moved, column in ((12.66, True, 13), (13.03, False, 13), (12.36, False, 13)):
            self.assertEqual(w.follow(60.415, lon), moved, lon)
            self.assertEqual(w.column, column, lon)
        for lon, column in ((12.34, 12), (11.34, 11), (10.34, 10)):  # back west
            self.assertTrue(w.follow(60.415, lon), lon)
            self.assertEqual(w.column, column, lon)
        self.assertFalse(w.follow(61.09, 10.34))
        self.assertTrue(w.follow(61.11, 10.34))  # north
        self.assertEqual(w.row, 61)
        self.assertFalse(w.follow(60.91, 10.34))
        self.assertTrue(w.follow(60.89, 10.34))  # back south
        self.assertEqual(w.row, 60)
        self.assertFalse(w.follow(45.0, 5.0))  # a jump: taken again from there
        self.assertEqual((w.column, w.row), (5, 45))

    def test_a_texture_across_a_tile_edge(self):
        across = Texture(23904, 33120, "BI", 16)  # 1.934 to 2.021 E
        self.assertEqual(tiles_of(across), {(43, 1), (43, 2)})
        inside = parse_texture_name("23904_33232_BI16.dds")
        self.assertEqual(len(tiles_of(inside)), 1)


class TwoStepPickTest(unittest.TestCase):
    def test_coarser_where_x_plane_holds_nothing_neutral_only_far(self):
        built = {"behind": 0, "far_small": 4, "near_small": 4}
        seen = {"behind": 1.0, "far_small": 2.0, "near_small": 0.5}
        got = pick(
            built,
            seen,
            lambda n: n != "near_small",
            1,
            neutral=lambda n: n == "far_small",
        )
        self.assertEqual(got, [("behind", 4), ("far_small", NEUTRAL_LEVEL)])


class DowngradeTest(unittest.TestCase):
    def test_sharp_to_small_to_neutral(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.dds"
            neutral = Path(tmp) / "neutral.dds"
            write_sparse(neutral, 4, levels_from(4))
            write_sparse(path, 1, levels_from(1))
            before = allocated(path)
            downgrade(path, 4, neutral)
            data = path.read_bytes()
            after = allocated(path)
            layout = dds.mip_layout()
            self.assertEqual(
                data[layout[4][0] :], b"".join(levels_from(4))
            )  # the small levels kept
            self.assertEqual(set(data[layout[1][0] : layout[4][0]]), {0})  # the large ones a hole
            self.assertGreater(before, 2_000_000)
            self.assertLess(after, 200_000)  # NTFS gives a sparse file 64 KB at a time: 110 KB
            downgrade(path, NEUTRAL_LEVEL, neutral)
            self.assertEqual(path.read_bytes(), neutral.read_bytes())


class CacheEvictTest(unittest.TestCase):
    def test_oldest_go_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = ChunkCache(Path(tmp) / "c.sqlite")
            for i in range(10):
                c.db.execute(
                    "INSERT INTO chunks VALUES ('BI', 12, ?, 0, ?, ?, ?)",
                    (i, IMAGE, b"x" * 1000, i),
                )
            c.db.commit()
            self.assertEqual(c.size_bytes(), 10_000)
            freed = c.evict_oldest(2500)
            left = [x for (x,) in c.db.execute("SELECT x FROM chunks ORDER BY x")]
            c.close()
        self.assertEqual(freed, 3000)
        self.assertEqual(left, list(range(3, 10)))


class PreparedStartTest(unittest.TestCase):
    def test_reload_only_if_xplane_came_first(self):
        from orthostudio.live.streamer import read_before_ready

        self.assertFalse(read_before_ready(ready_at=100.0, xplane_started=150.0))
        self.assertTrue(read_before_ready(ready_at=100.0, xplane_started=90.0))
        self.assertTrue(read_before_ready(ready_at=None, xplane_started=150.0))  # never ready
        self.assertTrue(read_before_ready(ready_at=100.0, xplane_started=None))  # cannot tell


class ResetTest(unittest.TestCase):
    def test_back_to_the_first_time(self):
        import json

        from orthostudio.live.region import reset_built

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "stream_pack"
            setup_region(make_source(root, tile=(45, 6), textures=NAMES), pack, log=lambda _: None)
            built = pack / "textures" / f"{NAMES[0]}.dds"
            write_sparse(built, 0, levels_from(0), base=pack / NEUTRAL_FILE)
            marker = pack / "_live" / "ortho.json"
            marker.write_text(
                json.dumps(
                    {"textures": {built.name: 0}, "seen": {built.name: 1}, "tiles": ["+45+006"]}
                )
            )
            self.assertEqual(reset_built(pack, log=lambda _: None), 1)
            self.assertEqual(built.read_bytes(), (pack / NEUTRAL_FILE).read_bytes())
            meta = json.loads(marker.read_text())
            self.assertEqual((meta["textures"], meta["seen"], meta["tiles"]), ({}, {}, ["+45+006"]))
            self.assertEqual(reset_built(pack, log=lambda _: None), 0)  # nothing left


class CameraTest(unittest.TestCase):
    def test_opengl_offsets_to_latlon(self):
        # 1 km east and 2 km north of the aircraft (OpenGL: x east, z south)
        lat, lon = camera_position(45.0, 6.0, (100.0, 0.0, 500.0), (1100.0, 30.0, -1500.0))
        self.assertAlmostEqual((lat - 45.0) * 111_195, 2000, delta=1)
        self.assertAlmostEqual((lon - 6.0) * 111_195 * 0.70711, 1000, delta=1)


class StreamerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pack = root / "stream_pack"
        setup_region(make_source(root, tile=(45, 6), textures=NAMES), self.pack, log=lambda _: None)
        self.cache = ChunkCache(root / "c.sqlite")

    def tearDown(self):
        self.cache.close()
        self.tmp.cleanup()

    def streamer(self, **kw):
        return Streamer(
            self.pack, self.cache, lambda on_piece: None, finest_quality=0, log=lambda _: None, **kw
        )

    def test_textures_being_built_are_as_urgent_as_the_last_plan_says(self):
        """A texture taken for the camera stops counting as urgent once a plan elsewhere no longer
        wants it: a start's "everything is built" must not wait for it."""
        s = self.streamer()
        lat, lon = parse_texture_name(f"{NAMES[0]}.dds").center
        s.plan(Fix(lat, lon), x_plane_there=False)
        s.take_batch(10_000)  # both textures being built
        self.assertEqual(s.urgent_left(), 2)
        s.plan(Fix(lat - 3.0, lon), x_plane_there=False)  # 330 km away: beyond every ring
        self.assertEqual(s.urgent_left(), 0)
        s.plan(Fix(lat, lon), x_plane_there=False)  # back: urgent again
        self.assertEqual(s.urgent_left(), 2)

    def test_the_camera_counts_for_nothing(self):
        """X-Plane takes each texture's levels for the aircraft, never for the camera: a camera
        taken over a texture leaves it at the level the aircraft's distance gives
        (docs/specs/live/levels-test.md, 2026-10-20)."""
        tex = parse_texture_name(f"{NAMES[1]}.dds")
        lat, lon = tex.center
        away = (lat - 0.5, lon)  # the aircraft 55 km south, flying south: the texture behind it
        s = self.streamer()
        alone = s.plan(Fix(*away, gs_ms=200.0, trk_deg=180.0), x_plane_there=False)
        self.assertEqual(alone[f"{NAMES[1]}.dds"][1], 3)  # 55 km behind: the 72 km ring
        with_cam = s.plan(
            Fix(*away, gs_ms=200.0, trk_deg=180.0, cam_lat=lat, cam_lon=lon), x_plane_there=False
        )
        self.assertEqual(with_cam[f"{NAMES[1]}.dds"][1], 3)

    def test_the_status_line_while_textures_are_finished(self):
        """The status line and the save read every texture built while the builder adds more: a
        loop over the live map fails when it grows ("dictionary changed size during iteration"),
        which stopped the streamer at LFLL, 17 s into a start, and X-Plane went on half-built."""
        s = self.streamer()
        s.built = {f"old{i}.dds": 4 for i in range(5000)}
        done: Future = Future()
        done.set_result(None)
        stop = threading.Event()

        def builder():
            i = 0
            while not stop.is_set():
                s._finished(done, f"new{i}.dds", 4)
                i += 1
                time.sleep(0.001)

        thread = threading.Thread(target=builder)
        thread.start()
        try:
            for _ in range(20):
                s.status()
                s.save()
        finally:
            stop.set()
            thread.join()

    def test_needed_only_while_read_as_sharp_as_built(self):
        """A texture counts as seen while X-Plane will read it as sharp as it is built, not merely
        within 300 km: else the oldest built go first, ahead of the aircraft on a way back."""
        s = self.streamer()
        name = f"{NAMES[0]}.dds"
        lat, lon = parse_texture_name(name).center
        s.built = {name: 0}
        s.plan(Fix(lat, lon), x_plane_there=False)  # right there: its 4096 px level is read
        self.assertIn(name, s.seen)
        s.seen.clear()
        s.plan(Fix(lat, lon + 1.5), x_plane_there=False)  # 116 km off: only its far levels are
        self.assertNotIn(name, s.seen)

    def test_the_textures_take_the_room_the_imagery_leaves(self):
        """One disk space: the textures may use what the imagery cache does not."""
        s = self.streamer(disk_budget=20 * 10**9, cache_budget=20 * 10**9)
        s._cache_size = 2 * 10**9
        self.assertEqual(s.texture_budget(), 38 * 10**9)
        s._cache_size = 25 * 10**9  # the cache over its own share: nothing to borrow
        self.assertEqual(s.texture_budget(), 20 * 10**9)

    def test_behind_the_aircraft(self):
        s = self.streamer()
        name = f"{NAMES[0]}.dds"
        lat, lon = parse_texture_name(name).center
        s.last_fix = Fix(lat, lon - 1.0, gs_ms=200.0, trk_deg=90.0)  # flying east, towards it
        self.assertFalse(s.behind(name))
        s.last_fix = Fix(lat, lon + 1.0, gs_ms=200.0, trk_deg=90.0)  # past it
        self.assertTrue(s.behind(name))
        s.last_fix = Fix(lat, lon + 1.0)  # not moving: nothing is behind
        self.assertFalse(s.behind(name))

    def test_what_may_go_back_to_256_px(self):
        """Nothing on a tile X-Plane may hold, round the aircraft or the camera; nothing X-Plane
        reads sharper than 256 px from there."""
        s = self.streamer()
        name = f"{NAMES[0]}.dds"
        lat, lon = parse_texture_name(name).center
        s.last_fix = Fix(lat, lon - 1.5)  # the texture two columns away: X-Plane holds its tile
        self.assertFalse(s.may_coarsen(name))
        s.last_fix = Fix(lat, lon - 3.6)  # four columns away, 280 km: let go of
        self.assertTrue(s.may_coarsen(name))
        self.assertTrue(s.far(name) is False)  # yet within 300 km: 256 px, not the neutral clone
        s.last_fix = Fix(lat, lon - 3.6, cam_lat=lat, cam_lon=lon - 1.0)  # the camera there
        self.assertFalse(s.may_coarsen(name))
        s.reach_km = 400.0  # a window other than measured: the 256 px guard still holds
        s.last_fix = Fix(lat, lon - 3.6)
        self.assertFalse(s.may_coarsen(name))

    def test_a_start_s_end_ends_the_night_s_start_too(self):
        """The streamer's arrived tells the night (orthostudio.live.nightwork.NightWork.arrived):
        a start by day left the night's team on for the whole flight (the review of 2026-11-11)."""
        s = self.streamer()
        told: list[str] = []
        s.night = type("Night", (), {"arrived": lambda self: told.append("arrived")})()
        s.arrived()
        self.assertEqual(told, ["arrived"])

    def test_a_night_texture_that_stays_lit_leaves_its_day_texture_recorded_neutral(self):
        """Windows refusing to replace a night texture X-Plane holds open: the day texture,
        neutral already, is recorded so all the same, and built again when flown to (the review of
        2026-11-11)."""
        from unittest import mock

        from orthostudio.live import streamer as streamer_module

        name = f"{NAMES[0]}.dds"
        lines: list[str] = []
        s = Streamer(self.pack, self.cache, lambda on_piece: None, finest_quality=0,
                     log=lines.append)  # fmt: skip
        s.built = {name: 4}
        s.seen = {name: 1.0}

        class Pool:
            def replace(self, top, path):
                path.write_bytes(b"neutral")

        class Names:
            pool = Pool()
            crashed = None

            def holds(self, name):
                return True

        class Night:
            def bytes(self):
                return 0

            def back_to_black(self, name, held):
                raise PermissionError("X-Plane holds it open")

        s.names, s.night = Names(), Night()
        with mock.patch.object(streamer_module, "pick", return_value=[(name, NEUTRAL_LEVEL)]):
            s.evict()
        self.assertNotIn(name, s.built)
        self.assertTrue(any("stays as it was" in ln for ln in lines))

    def test_evicts_far_textures_down_to_the_budget(self):
        for n in NAMES:
            write_sparse(self.pack / "textures" / f"{n}.dds", 0, levels_from(0))
        # over budget by one sharp texture, 90 % of the budget reached once it is made small;
        # no room of the imagery cache's to borrow
        s = self.streamer(
            disk_budget=int((texture_bytes(0) + texture_bytes(4)) / 0.9) + 1, cache_budget=0
        )
        s.built = {f"{NAMES[0]}.dds": 0, f"{NAMES[1]}.dds": 0}
        s.seen = {f"{NAMES[0]}.dds": 1.0, f"{NAMES[1]}.dds": 2.0}
        lat, lon = parse_texture_name(f"{NAMES[0]}.dds").center
        s.last_fix = Fix(lat, lon)  # both textures in reach: nothing may go
        self.assertEqual(s.evict(), 0)
        s.last_fix = Fix(lat + 5, lon)  # 550 km away: the older one goes to 256 px
        self.assertEqual(s.evict(), 1)
        self.assertEqual(s.built, {f"{NAMES[0]}.dds": 4, f"{NAMES[1]}.dds": 0})
        path = self.pack / "textures" / f"{NAMES[0]}.dds"
        if not disk_clones(self.pack):  # NTFS, ext4: its larger levels a hole, which takes no disk
            self.assertLess(allocated(path), texture_bytes(4) + 256 * 1024)
            return
        data = path.read_bytes()
        self.assertNotEqual(set(data[dds.mip_layout()[0][0] : dds.mip_layout()[0][0] + 4096]), {0})
        if sys.platform != "darwin":  # btrfs, xfs: their extents not read here
            return
        # its larger levels are the neutral texture's own blocks, shared: no disk, and never black
        small = file_extents(path)
        neutral = file_extents(self.pack / NEUTRAL_FILE)

        def device_offset(extents, offset):
            for start, dev, length in extents:
                if start <= offset < start + length:
                    return dev + offset - start
            return None

        tail = dds.mip_layout()[4][0] // 4096 * 4096
        for offset in range(
            0, tail, 256 * 1024
        ):  # every larger level: the neutral file's own blocks
            self.assertEqual(device_offset(small, offset), device_offset(neutral, offset), offset)


if __name__ == "__main__":
    unittest.main()
