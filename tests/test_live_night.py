# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Night textures as drawn: the same image for the same roads, the same light per square metre at
ZL16 and ZL18, rural roads dimmer, NASA's density, the day photo under the lights, and the brighter
smaller levels."""

import math
import unittest

import numpy as np

from orthostudio.live import night


def texture_at(lat: float, lon: float, zoom_level: int = 16) -> tuple[int, int]:
    """``x0``, ``y0`` of the texture (16 x 16 tiles) holding a point."""
    n = 2.0**zoom_level
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return int(x) // 16 * 16, int(y) // 16 * 16


def grid_of_streets(lat: float, lon: float, span: float = 0.02, step: float = 0.002, rural=False):
    """A town of straight streets round a point."""
    out = []
    for k in np.arange(-span, span + 1e-9, step):
        xs = np.linspace(lon - span, lon + span, 40)
        out.append(night.Road("residential", rural, xs, np.full_like(xs, lat + k)))
        ys = np.linspace(lat - span, lat + span, 40)
        out.append(night.Road("residential", rural, np.full_like(ys, lon + k), ys))
    return out


class DrawTest(unittest.TestCase):
    lat, lon = 43.70, 7.25

    def test_the_same_roads_give_the_same_image(self):
        x0, y0 = texture_at(self.lat, self.lon)
        roads = grid_of_streets(self.lat, self.lon)
        a = night.draw(roads, None, x0, y0, 16, 256, seed=night.seed_of(x0, y0))
        b = night.draw(roads, None, x0, y0, 16, 256, seed=night.seed_of(x0, y0))
        np.testing.assert_array_equal(a, b)
        self.assertGreater(a.mean(), 0.0)

    def test_no_road_no_light(self):
        x0, y0 = texture_at(self.lat, self.lon)
        far = grid_of_streets(self.lat + 1.0, self.lon + 1.0)
        self.assertEqual(night.draw(far, None, x0, y0, 16, 256).max(), 0.0)

    def test_zl18_has_the_light_of_zl16_where_they_overlap(self):
        roads = grid_of_streets(self.lat, self.lon, span=0.05)
        x16, y16 = texture_at(self.lat, self.lon, 16)
        x18, y18 = texture_at(self.lat, self.lon, 18)
        z16 = night.draw(roads, None, x16, y16, 16, 1024)
        z18 = night.draw(roads, None, x18, y18, 18, 1024)
        # the ZL18 texture's square inside the ZL16 one: 4 x 4 of its 16 x 16 tiles
        cx, cy = (x18 // 4 - x16) * 64, (y18 // 4 - y16) * 64
        inner = z16[cy : cy + 256, cx : cx + 256]
        self.assertAlmostEqual(z18.mean() / inner.mean(), 1.0, delta=0.2)

    def test_a_rural_road_is_dimmer(self):
        x0, y0 = texture_at(self.lat, self.lon)
        town = night.draw(grid_of_streets(self.lat, self.lon), None, x0, y0, 16, 256)
        fields = night.draw(grid_of_streets(self.lat, self.lon, rural=True), None, x0, y0, 16, 256)
        self.assertLess(fields.mean(), town.mean() * 0.6)

    def test_nasa_dims_where_it_saw_nothing(self):
        x0, y0 = texture_at(self.lat, self.lon)
        roads = grid_of_streets(self.lat, self.lon)
        bright = night.Density(np.full((10, 10), 255, np.uint8), 7.0, 44.0, 0.1)
        dark = night.Density(np.zeros((10, 10), np.uint8), 7.0, 44.0, 0.1)
        lit = night.draw(roads, bright, x0, y0, 16, 256)
        unlit = night.draw(roads, dark, x0, y0, 16, 256)
        self.assertLess(unlit.mean(), lit.mean() * 0.3)


class DensityTest(unittest.TestCase):
    def test_between_the_cells(self):
        d = night.Density(np.array([[0, 255], [0, 255]], np.uint8), 7.0, 44.0, 0.5)
        # cell centres at 7.25 and 7.75: halfway between them, half
        self.assertAlmostEqual(float(d.at(np.array([7.5]), np.array([43.5]))[0]), 0.5, places=3)
        self.assertAlmostEqual(float(d.at(np.array([7.25]), np.array([43.75]))[0]), 0.0, places=3)


class LevelsTest(unittest.TestCase):
    def test_ground_under_the_lights(self):
        lights = np.zeros((4, 4, 3))
        photo = np.full((4, 4, 3), 200, np.uint8)
        rgb = night.night_rgb(lights, photo, gain=1.0, ground=0.05)
        self.assertEqual(int(rgb[0, 0, 0]), round(200 * 0.05))

    def test_smaller_levels_keep_a_lamp_brighter_than_the_mean(self):
        rgb = np.zeros((64, 64, 3), np.uint8)
        rgb[10, 10] = 255
        levels = night.lit_levels(rgb)
        self.assertEqual([lv.shape[0] for lv in levels], [64, 32, 16, 8, 4, 2, 1])
        plain = 255 / 4
        self.assertGreater(int(levels[1][5, 5, 0]), plain)  # 255 / 4^(1/3), about 161


if __name__ == "__main__":
    unittest.main()


# --- in the pack: the night lines of the terrain files, the black names, taken away with a square

TER_LAND = (
    b"A\n800\nTERRAIN\n\nLOAD_CENTER 36.99378 -93.15308 1953 4096\n"
    b"BASE_TEX_NOWRAP ../textures/102032_63232_BI18.dds\n"
    b"DECAL_LIB lib/g10/decals/maquify_2_green_key.dcl\nNO_ALPHA\n"
)


class TerrainLineTest(unittest.TestCase):
    def test_the_night_texture_right_after_the_photo_and_gone_again(self):
        from orthostudio.live.names import with_night

        lit = with_night(TER_LAND, "102032_63232_BI18_LIT.dds")
        self.assertIn(
            b"BASE_TEX_NOWRAP ../textures/102032_63232_BI18.dds\n"
            b"LIT_TEX_NOWRAP ../textures_night/102032_63232_BI18_LIT.dds\n",
            lit,
        )
        self.assertEqual(with_night(lit, "102032_63232_BI18_LIT.dds"), lit)  # once only
        self.assertEqual(with_night(lit, None), TER_LAND)


class PackTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        from live_helpers import make_source
        from orthostudio.live.region import setup_region

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.row = tuple(f"23296_{33152 + 16 * k}_BI16" for k in range(12))
        self.pack = root / "pack"
        setup_region(
            make_source(root, tile=(46, 2), textures=self.row), self.pack, log=lambda _: None
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_square_made_with_the_night_lights_and_taken_away(self):
        import os

        from live_helpers import live_names
        from orthostudio.live.region import NIGHT_FOLDER, NIGHT_NEUTRAL

        names = live_names(self.pack, night=True)
        names.make((46, 2))
        night = self.pack / NIGHT_FOLDER
        for name in self.row[:11]:
            lit = night / f"{name}_LIT.dds"
            self.assertTrue(os.path.samefile(lit, self.pack / NIGHT_NEUTRAL))
            land = (self.pack / "terrain" / f"{name}.ter").read_bytes()
            self.assertIn(f"LIT_TEX_NOWRAP ../{NIGHT_FOLDER}/{name}_LIT.dds".encode(), land)
            water = (self.pack / "terrain" / f"{name}_water_overlay.ter").read_bytes()
            self.assertNotIn(b"LIT_TEX", water)
        built = night / f"{self.row[0]}_LIT.dds"
        built.unlink()
        built.write_bytes(b"a night texture built")
        names.done.add((46, 2))
        names.take_away((46, 2))
        self.assertTrue(built.exists())  # built: kept, as a day texture built is
        self.assertFalse((night / f"{self.row[1]}_LIT.dds").exists())

    def test_without_the_night_lights_no_line_and_no_name(self):
        from live_helpers import live_names
        from orthostudio.live.region import NIGHT_FOLDER

        live_names(self.pack).make((46, 2))
        land = (self.pack / "terrain" / f"{self.row[0]}.ter").read_bytes()
        self.assertNotIn(b"LIT_TEX", land)
        self.assertFalse((self.pack / NIGHT_FOLDER).exists())

    def test_turning_them_on_makes_the_terrain_files_again_but_not_under_x_plane(self):
        from live_helpers import live_names
        from orthostudio.live.region import set_night

        live_names(self.pack).make((46, 2))
        said: list[str] = []
        set_night(self.pack, True, log=said.append, xplane_running=lambda: True)
        self.assertTrue(any((self.pack / "terrain").iterdir()))  # X-Plane may read them
        set_night(self.pack, True, log=said.append)
        self.assertEqual(list((self.pack / "terrain").iterdir()), [])
        self.assertIn("night lights on", said[-1])
        set_night(self.pack, True, log=said.append)  # recorded: nothing more
        self.assertEqual(len(said), 2)


# --- the worker: a night texture made from the day texture on the disk


class Bc1Test(unittest.TestCase):
    def test_a_level_reads_back_as_it_was_encoded(self):
        from orthostudio.live.imaging import encode_bc1
        from orthostudio.live.night import decode_bc1

        y, x = np.mgrid[0:64, 0:64]
        rgb = np.stack([x * 4, y * 4, np.full_like(x, 128)], axis=-1).astype(np.uint8)
        back = decode_bc1(encode_bc1(rgb), 64)
        self.assertEqual(back.shape, (64, 64, 3))
        self.assertLess(np.abs(back.astype(int) - rgb.astype(int)).mean(), 6)


class NightJobTest(unittest.TestCase):
    def test_a_night_texture_from_a_day_texture_with_no_road(self):
        import tempfile
        from pathlib import Path

        from orthostudio.live import dds
        from orthostudio.live.night import decode_bc1
        from orthostudio.live.nightwork import night_job
        from orthostudio.live.region import NIGHT_FOLDER, NIGHT_TOP, neutral_levels
        from orthostudio.live.texture import write_sparse

        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp)
            name = "23904_34080_BI16.dds"
            (pack / "textures").mkdir()
            (pack / NIGHT_FOLDER).mkdir()
            write_sparse(pack / "textures" / name, 0, neutral_levels(0, rgb=(200, 200, 200)))
            got, level, _ = night_job((str(pack), name, 0, [], None))
            self.assertEqual((got, level), (name, 0))
            lit = pack / NIGHT_FOLDER / "23904_34080_BI16_LIT.dds"
            self.assertEqual(dds.read_top(lit), NIGHT_TOP)
            offset, length = dds.level_layout(NIGHT_TOP)[0]
            top = decode_bc1(lit.read_bytes()[offset : offset + length], 1024)
            self.assertAlmostEqual(float(top.mean()), 200 * 0.05, delta=2)  # the ground only

    def test_far_away_only_its_small_levels(self):
        import tempfile
        from pathlib import Path

        from orthostudio.live import dds
        from orthostudio.live.nightwork import night_job
        from orthostudio.live.region import NIGHT_FOLDER, NIGHT_TOP, neutral_levels
        from orthostudio.live.texture import write_sparse

        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp)
            name = "23904_34080_BI16.dds"
            (pack / "textures").mkdir()
            (pack / NIGHT_FOLDER).mkdir()
            write_sparse(pack / "textures" / name, 4, neutral_levels(4, rgb=(200, 200, 200)))
            night_job((str(pack), name, 4, [], None))  # a 256 px day texture: a 64 px night one
            lit = (pack / NIGHT_FOLDER / "23904_34080_BI16_LIT.dds").read_bytes()
            layout = dds.level_layout(NIGHT_TOP)
            o0, n0 = layout[0]
            o4, n4 = layout[4]
            self.assertEqual(lit[o0 : o0 + n0].count(0), n0)  # the 1024 px level black
            self.assertNotEqual(lit[o4 : o4 + n4].count(0), n4)  # the 64 px one written


# --- the streamer's night work: nothing by day, the textures built lit when night falls,
# nearest first, a start at night waiting for its own


class _Now:
    """An executor that runs each job at once, in this process."""

    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        from concurrent.futures import Future

        fut = Future()
        if fn.__name__ == "night_job":
            job = args[0]
            self.jobs.append(job)
            fut.set_result((job[1], job[2], 0.01))
        else:  # the roads of a square read (nightwork.read_roads), here at once
            self.jobs.append((fn.__name__, *args))
            fut.set_result(fn(*args))
        return fut

    def shutdown(self, **_):
        pass


class NightWorkTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        from orthostudio.live.nightwork import NightWork

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ex = _Now()
        self.work = NightWork(self.root, None, None, [self.root / "night"], log=lambda _: None,
                              executor=self.ex)  # fmt: skip
        x, y = texture_at(43.70, 7.25)
        self.near = f"{y}_{x}_BI16.dds"
        xf, yf = texture_at(44.50, 8.50)
        self.far = f"{yf}_{xf}_BI16.dds"

    def tearDown(self):
        self.tmp.cleanup()

    def test_nothing_by_day(self):
        self.work.tick(20.0, (43.7, 7.25), lambda: {self.near: 0})
        self.work.built(self.near, 0)
        self.assertEqual(self.ex.jobs, [])

    def test_night_falls_on_the_textures_built_nearest_first(self):
        self.work.tick(20.0, (43.7, 7.25), lambda: {})
        self.work.tick(2.0, (43.7, 7.25), lambda: {self.far: 4, self.near: 0})
        self.assertEqual([job[1] for job in self.ex.jobs], [self.near, self.far])
        self.assertEqual(self.work.made, {self.near: 0, self.far: 4})
        self.work.built(self.near, 0)  # built again at the same level: not made again
        self.work.tick(2.0, (43.7, 7.25), lambda: {})
        self.assertEqual(len(self.ex.jobs), 2)
        self.work.built(self.far, 2)  # sharper: made again from there
        self.work.tick(2.0, (43.7, 7.25), lambda: {})
        self.assertEqual(self.ex.jobs[-1][1:3], (self.far, 2))

    def test_a_start_at_night_counts_what_is_left_round_it(self):
        from pathlib import Path

        prefs = Path(self.tmp.name) / "Freeflight.prf"
        prefs.write_text(
            '_time {"local_time":{"day_of_year":314,"latitude":43.7,"longitude":7.25,'
            '"time_in_24_hours":21.0}}\n'
        )
        self.work.prefs = prefs
        self.work.start(43.70, 7.25)
        self.assertTrue(self.work.night)
        self.work.want(self.near, 0)
        self.work.want(self.far, 4)
        self.assertEqual(self.work.start_left(), 1)  # the far one does not hold X-Plane
        self.work.tick(None, (43.7, 7.25), lambda: {})
        self.assertEqual(self.work.start_left(), 0)

    def test_a_start_waits_for_every_near_night_however_far(self):
        self.work.start_at = (43.70, 7.25)
        self.work.sun = 10.0
        self.work.want(self.far, 2)  # 150 km away but built at 1024 px: X-Plane waits for it
        self.assertEqual(self.work.start_left(), 1)
        self.work.forget(self.far)
        self.work.want(self.far, 4)  # far and small: X-Plane does not wait
        self.assertEqual(self.work.start_left(), 0)

    def test_the_near_ones_first_then_the_nearest(self):
        self.work.sun = 10.0
        self.work.want(self.near, 4)  # under the aircraft, but small
        self.work.want(self.far, 1)  # 150 km away, but sharp
        self.work.tick(10.0, (43.7, 7.25), lambda: {})
        self.assertEqual([job[1] for job in self.ex.jobs], [self.far, self.near])

    def test_the_sun_at_noon_and_at_night(self):
        from pathlib import Path

        from orthostudio.live.nightwork import sun_at_start

        prefs = Path(self.tmp.name) / "Freeflight.prf"
        for hours, low, high in ((12.0, 20.0, 35.0), (21.0, -60.0, -15.0)):
            prefs.write_text(
                '_time {"local_time":{"day_of_year":314,"latitude":43.7,"longitude":7.25,'
                f'"time_in_24_hours":{hours}}}}}\n'
            )
            sun = sun_at_start(prefs)
            self.assertTrue(low < sun < high, (hours, sun))
        prefs.write_text("nothing\n")
        self.assertIsNone(sun_at_start(prefs))

    def test_back_to_black_and_the_bytes_counted(self):
        import os

        from orthostudio.live.region import NIGHT_FOLDER, NIGHT_NEUTRAL

        (self.root / NIGHT_FOLDER).mkdir()
        lit = self.root / NIGHT_FOLDER / self.near.replace(".dds", "_LIT.dds")
        lit.write_bytes(b"built")
        self.work.made[self.near] = 0
        self.assertGreater(self.work.bytes(), 600_000)  # a 1024 px BC1 file and its levels
        self.work.back_to_black(self.near, keep_name=True)
        self.assertTrue(os.path.samefile(lit, self.root / NIGHT_NEUTRAL))
        self.assertEqual(self.work.bytes(), 0)
        self.work.back_to_black(self.near, keep_name=False)
        self.assertFalse(lit.exists())


class RoadsOnceTest(unittest.TestCase):
    """The night's one process reads a square's roads once, in a Python of its own, and keeps the
    last few squares'."""

    def test_a_square_read_once(self):
        from pathlib import Path
        from unittest import mock

        from orthostudio.live import nightwork

        reads = []

        def read(path):
            reads.append(str(path))
            return night.Roads.of(grid_of_streets(43.70, 7.25))

        nightwork._ROADS.clear()
        self.addCleanup(nightwork._ROADS.clear)
        squares = [Path(f"Earth nav data/+40+000/+43+{lon:03d}.dsf") for lon in range(12)]
        with mock.patch.object(nightwork, "_read_apart", read):
            first = nightwork.roads_from([squares[0]])
            again = nightwork.roads_from([squares[0]])
            self.assertEqual(len(reads), 1)
            self.assertIs(first[0], again[0])
            nightwork.roads_from(squares)  # more squares than kept: the first ones dropped
            self.assertEqual(len(nightwork._ROADS), nightwork._ROADS_KEPT)
            nightwork.roads_from([squares[-1]])
            self.assertEqual(len(reads), 12)

    def test_a_dsf_missing_gives_no_road(self):
        from orthostudio.live.nightwork import read_roads

        self.assertEqual(len(read_roads("/nowhere/Earth nav data/+40+000/+43+007.dsf")), 0)

    def test_read_apart_as_here(self):
        """Nice's square, where X-Plane is installed: the same roads from the Python apart."""
        from pathlib import Path

        from orthostudio.live.nightwork import _read_apart, read_roads

        dsf = (
            Path.home() / "X-Plane 12" / "Global Scenery" / "X-Plane 12 Global Scenery"
            / "Earth nav data" / "+40+000" / "+43+007.dsf"
        )  # fmt: skip
        if not dsf.is_file():
            self.skipTest("no X-Plane 12 Global Scenery here")
        here, apart = read_roads(str(dsf)), _read_apart(dsf)
        self.assertGreater(len(here), 1000)
        for key in ("lon", "lat", "start", "kind", "rural", "box"):
            np.testing.assert_array_equal(getattr(here, key), getattr(apart, key), err_msg=key)


class LeanDrawingTest(unittest.TestCase):
    """The night's process draws without scipy (17 MB it no longer loads), its roads in float32
    from their square's corner, several squares never copied into one (2026-11-10)."""

    def test_the_filters_as_scipy_drew_them(self):
        try:
            from scipy.ndimage import gaussian_filter, maximum_filter, zoom
        except ImportError:
            self.skipTest("no scipy to compare with")
        rng = np.random.default_rng(3)
        a = (rng.random((300, 300), dtype=np.float32) ** 20).astype(np.float32)
        for sigma in (1.5, 1.74, 4.0):
            np.testing.assert_allclose(night._blur(a, sigma), gaussian_filter(a, sigma), atol=1e-6)
        np.testing.assert_array_equal(night._max2(a), maximum_filter(a, size=2))
        small = rng.random((64, 64), dtype=np.float32)
        np.testing.assert_allclose(
            night._zoom_linear(small, 1024), zoom(small, 16, order=1), atol=1e-6
        )
        rgb = rng.random((256, 256, 3), dtype=np.float32)
        np.testing.assert_allclose(
            night._zoom_linear(rgb, 1024), zoom(rgb, (4, 4, 1), order=1), atol=1e-6
        )

    def test_a_point_to_a_centimetre_anywhere(self):
        lon = np.array([179.123456789, 179.987654321])
        lat = np.array([-45.5555555, -45.0000001])
        roads = night.Roads.of([night.Road("residential", False, lon, lat)])
        got_lon, got_lat = roads.points(0)
        self.assertLess(np.abs(got_lon - lon).max() * 111_320, 0.01)
        self.assertLess(np.abs(got_lat - lat).max() * 111_320, 0.01)

    def test_two_squares_drawn_as_one(self):
        west = grid_of_streets(43.70, 6.99, span=0.008)  # a town astride two squares
        east = grid_of_streets(43.70, 7.01, span=0.008)
        x0, y0 = texture_at(43.70, 7.0)
        apart = night.draw([night.Roads.of(west), night.Roads.of(east)], None, x0, y0, 16, 256)
        whole = night.draw(night.Roads.of(west + east), None, x0, y0, 16, 256)
        self.assertGreater(float(whole.sum()), 0.0)
        self.assertLess(abs(float(apart.sum()) / float(whole.sum()) - 1.0), 0.01)
