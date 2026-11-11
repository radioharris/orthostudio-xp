# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Night textures as drawn: the same image for the same roads, the same light per square metre at
ZL16 and ZL18, rural roads dimmer, NASA's density, black between the lights, and the brighter
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
    def test_black_between_the_lights(self):
        """No photo under them, and no faint glow BC1 would turn purple or green (LSGG,
        2026-11-10)."""
        lights = np.zeros((4, 4, 3), np.float32)
        lights[0, 0] = 0.8  # a lamp
        lights[1, 1] = 3 / 255  # the faint edge of a glow
        rgb = night.night_rgb(lights, gain=1.0)
        self.assertEqual(int(rgb[0, 0, 0]), 204)
        self.assertEqual(int(rgb[1, 1].max()), 0)
        self.assertEqual(int(rgb[2:, 2:].max()), 0)

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


def decode_bc1(blob: bytes, side: int) -> np.ndarray:
    """The RGB uint8 pixels of one BC1 level ``side`` px wide (at least 4)."""
    blocks = max(1, side // 4)
    b = np.frombuffer(blob, dtype="<u2", count=blocks * blocks * 4).reshape(blocks, blocks, 4)
    c0, c1 = b[..., 0].astype(np.int32), b[..., 1].astype(np.int32)
    idx = b[..., 2].astype(np.uint32) | (b[..., 3].astype(np.uint32) << 16)

    def rgb565(c):
        return np.stack(
            [((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31],
            axis=-1,
        )

    p0, p1 = rgb565(c0), rgb565(c1)
    four = (c0 > c1)[..., None]
    p2 = np.where(four, (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four, (p0 + 2 * p1) // 3, 0)
    palette = np.stack([p0, p1, p2, p3], axis=2)
    which = ((idx[..., None] >> (np.arange(16, dtype=np.uint32) * 2)) & 3).astype(np.intp)
    pixels = np.take_along_axis(palette, which[..., None], axis=2)
    out = pixels.reshape(blocks, blocks, 4, 4, 3).transpose(0, 2, 1, 3, 4)
    return out.reshape(blocks * 4, blocks * 4, 3)[:side, :side].astype(np.uint8)


class ResetNightTest(unittest.TestCase):
    def test_a_reset_puts_the_nights_back_to_black(self):
        """Every built texture back to neutral: its night black again, never left over it."""
        import json
        import os
        import tempfile
        from pathlib import Path

        from orthostudio.live.region import NIGHT_FOLDER, ensure_night_neutral, reset_built

        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp)
            (pack / "textures").mkdir()
            (pack / NIGHT_FOLDER).mkdir()
            black = ensure_night_neutral(pack)
            name = "23904_34080_BI16.dds"
            lit = pack / NIGHT_FOLDER / "23904_34080_BI16_LIT.dds"
            lit.write_bytes(b"a night made before")
            (pack / "_live" / "ortho.json").write_text(json.dumps({"textures": {name: 0}}))
            reset_built(pack, log=lambda _: None)
            self.assertEqual(os.stat(lit).st_ino, os.stat(black).st_ino)


class Bc1Test(unittest.TestCase):
    def test_a_level_reads_back_as_it_was_encoded(self):
        from orthostudio.live.imaging import encode_bc1

        y, x = np.mgrid[0:64, 0:64]
        rgb = np.stack([x * 4, y * 4, np.full_like(x, 128)], axis=-1).astype(np.uint8)
        back = decode_bc1(encode_bc1(rgb), 64)
        self.assertEqual(back.shape, (64, 64, 3))
        self.assertLess(np.abs(back.astype(int) - rgb.astype(int)).mean(), 6)


class NightJobTest(unittest.TestCase):
    def test_no_road_no_light(self):
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
            write_sparse(pack / "textures" / name, 0, neutral_levels(0, rgb=(200, 200, 200)))
            got, level, _, _ = night_job((str(pack), name, 0, [], None, None))
            self.assertEqual((got, level), (name, 0))
            lit = pack / NIGHT_FOLDER / "23904_34080_BI16_LIT.dds"
            self.assertEqual(dds.read_top(lit), NIGHT_TOP)
            offset, length = dds.level_layout(NIGHT_TOP)[0]
            top = decode_bc1(lit.read_bytes()[offset : offset + length], 1024)
            self.assertEqual(int(top.max()), 0)  # black: nothing of the day photo under it

    def test_far_away_only_its_small_levels(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from orthostudio.live import dds, nightwork
        from orthostudio.live.nightwork import night_job
        from orthostudio.live.region import NIGHT_FOLDER, NIGHT_TOP, neutral_levels
        from orthostudio.live.texture import write_sparse

        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp)
            name = "23904_34080_BI16.dds"
            (pack / "textures").mkdir()
            (pack / NIGHT_FOLDER).mkdir()
            write_sparse(pack / "textures" / name, 4, neutral_levels(4, rgb=(200, 200, 200)))
            town = [night.Roads.of(grid_of_streets(43.70, 7.27, span=0.03))]
            with mock.patch.object(nightwork, "roads_from", lambda *_: town):
                night_job(
                    (str(pack), name, 4, [], None, None)
                )  # a 256 px day texture: a 64 px night one
            lit = (pack / NIGHT_FOLDER / "23904_34080_BI16_LIT.dds").read_bytes()
            layout = dds.level_layout(NIGHT_TOP)
            o0, n0 = layout[0]
            o4, n4 = layout[4]
            self.assertEqual(lit[o0 : o0 + n0].count(0), n0)  # the 1024 px level black
            self.assertNotEqual(lit[o4 : o4 + n4].count(0), n4)  # the 64 px one written

    def test_the_lights_as_bright_as_chosen(self):
        """Live's "Night lights": soft, normal or strong, the same lamps fainter or brighter."""
        import tempfile
        from pathlib import Path
        from unittest import mock

        from orthostudio.live import dds, nightwork
        from orthostudio.live.night import GAIN, LIGHTS
        from orthostudio.live.nightwork import night_job
        from orthostudio.live.region import NIGHT_FOLDER, NIGHT_TOP, neutral_levels
        from orthostudio.live.texture import write_sparse

        self.assertEqual(GAIN, LIGHTS["normal"])
        self.assertLess(LIGHTS["soft"], LIGHTS["normal"])
        self.assertLess(LIGHTS["normal"], LIGHTS["strong"])
        town = [night.Roads.of(grid_of_streets(43.70, 7.27, span=0.03))]
        means = {}
        for light, gain in LIGHTS.items():
            with tempfile.TemporaryDirectory() as tmp:
                pack = Path(tmp)
                name = "23904_34080_BI16.dds"
                (pack / "textures").mkdir()
                (pack / NIGHT_FOLDER).mkdir()
                write_sparse(pack / "textures" / name, 0, neutral_levels(0, rgb=(200, 200, 200)))
                with mock.patch.object(nightwork, "roads_from", lambda *_: town):
                    night_job((str(pack), name, 0, [], None, gain))
                lit = (pack / NIGHT_FOLDER / "23904_34080_BI16_LIT.dds").read_bytes()
                offset, length = dds.level_layout(NIGHT_TOP)[0]
                means[light] = float(decode_bc1(lit[offset : offset + length], 1024).mean())
        self.assertGreater(means["soft"], 0)
        self.assertLess(means["soft"], means["normal"])
        self.assertLess(means["normal"], means["strong"])


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
            fut.set_result((job[1], job[2], 0.01, None))
        else:  # the roads of a square read (nightwork.read_roads), here at once
            self.jobs.append((fn.__name__, *args))
            fut.set_result(fn(*args))
        return fut

    def shutdown(self, **_):
        pass


def nightwork_team_max():
    from orthostudio.live.nightwork import TEAM_MAX

    return TEAM_MAX


class _TeamNow:
    """The start's team, its square jobs ended when the test says."""

    def __init__(self):
        self.size, self.batches, self.futures, self.shut = None, [], [], False

    def make(self, size):
        self.size = size
        return self

    def submit(self, fn, batch):
        from concurrent.futures import Future

        fut = Future()
        self.batches.append(batch)
        self.futures.append((fut, batch))
        return fut

    def finish(self):
        for fut, batch in self.futures:
            fut.set_result([(job[1], job[2], 0.01, None) for job in batch])

    def shutdown(self, **_):
        self.shut = True


class _Later:
    """An executor whose jobs end when the test says: in the order given."""

    def __init__(self):
        self.given, self.pending = [], []

    def submit(self, fn, job):
        from concurrent.futures import Future

        fut = Future()
        self.given.append(job[1])
        self.pending.append((fut, job))
        return fut

    def finish_one(self):
        fut, job = self.pending.pop(0)
        fut.set_result((job[1], job[2], 0.01, None))

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

    def test_the_lights_chosen_go_with_each_job(self):
        from orthostudio.live.nightwork import NightWork

        work = NightWork(self.root, None, None, [], log=lambda _: None, executor=self.ex, gain=1.0)
        self.assertEqual(work._job(self.near, 0)[5], 1.0)

    def test_nothing_by_day(self):
        self.work.tick(20.0, (43.7, 7.25), lambda: {self.near: 0})
        self.work.built(self.near, 0)
        self.assertEqual(self.ex.jobs, [])

    def test_night_falls_on_the_textures_built_nearest_first(self):
        self.work.tick(20.0, (43.7, 7.25), lambda: {})
        self.work.tick(2.0, (43.7, 7.25), lambda: {self.far: 4, self.near: 0})
        self.assertEqual([job[1] for job in self.ex.jobs], [self.near, self.far])
        self.assertEqual(self.work.made, {self.near: 0, self.far: 4})
        self.assertEqual([job[5] for job in self.ex.jobs], [None, None])  # the gain: GAIN
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
        self.assertEqual(self.work.start_left(), 2)  # the far one too: X-Plane reads it there
        self.work.tick(None, (43.7, 7.25), lambda: {})
        self.assertEqual(self.work.start_left(), 0)

    def test_a_start_known_at_night_after_its_textures_were_built(self):
        """Live learnt of LSGG before X-Plane saved its time, built round it by the day of the
        flight before, then X-Plane gave 23:30 (2026-11-10): the textures built get their night."""
        from pathlib import Path

        prefs = Path(self.tmp.name) / "Freeflight.prf"

        def saved(hours: float) -> None:
            prefs.write_text(
                '_time {"local_time":{"day_of_year":314,"latitude":43.7,"longitude":7.25,'
                f'"time_in_24_hours":{hours}}}}}\n'
            )

        self.work.prefs = prefs
        saved(12.0)
        self.work.start(43.70, 7.25, lambda: {})
        self.assertFalse(self.work.night)
        self.work.built(self.near, 0)  # built by day: no night wanted yet
        self.work.built(self.far, 4)
        saved(23.5)
        self.work.start(43.70, 7.25, lambda: {self.near: 0, self.far: 4})
        self.assertTrue(self.work.night)
        self.assertEqual(self.work.start_left(), 2)  # both, which the start waits for
        self.work.tick(None, (43.7, 7.25), lambda: {})
        self.assertEqual({job[1] for job in self.ex.jobs}, {self.near, self.far})
        self.assertEqual(self.work.start_left(), 0)

    def test_a_team_makes_the_far_ones_a_square_each_and_ends(self):
        """At a start at night, the far ones go to a team of processes, a square to a job, from
        the first built, while its photos are still built; the team ends with the start's
        nights (the user, 2026-11-11)."""
        team = _TeamNow()
        self.work._make_team = team.make
        self.work.team = 12
        self.work.start_at = (43.70, 7.25)
        self.work._starting = True
        self.work.sun = 10.0
        far = {}
        for lat, lon in ((43.10, 7.10), (43.15, 7.30), (43.12, 7.60), (44.50, 8.50)):
            x, y = texture_at(lat, lon)
            far[(lat, lon)] = f"{y}_{x}_BI16.dds"
            self.work.want(far[(lat, lon)], 4)
        self.work.want(self.near, 0)
        self.assertEqual(self.work.start_left(day_left=3), 5)  # its photos still built: all
        self.work.pump()  # the streamer's loop, X-Plane running or not
        self.assertEqual(team.size, nightwork_team_max())  # the team at work already
        self.assertEqual(
            [sorted(job[1] for job in batch) for batch in team.batches],
            [[self.near],  # the near ones first, a few to a job
             sorted(far[k] for k in ((43.10, 7.10), (43.15, 7.30), (43.12, 7.60))),
             [far[(44.50, 8.50)]]],
        )  # fmt: skip
        team.finish()
        self.assertFalse(team.shut)  # the start goes on: more may come
        self.assertEqual(self.work.start_left(day_left=0), 0)
        self.assertTrue(team.shut)
        self.assertIsNone(self.work._team_pool)
        self.assertEqual(len(self.work.made), 5)

    def test_a_start_waits_only_for_the_nights_of_what_was_built_with_its_photos(self):
        """Those built after, farther, are made in flight: waiting for them took 27 s more at
        LSGG (2026-11-11)."""
        self.work.start_at = (43.70, 7.25)
        self.work.sun = 10.0
        self.work.want(self.near, 0)
        self.assertEqual(self.work.start_left(day_left=0), 1)  # the start's own: the near one
        self.work.want(self.far, 4)  # built after, as X-Plane waits
        self.assertEqual(self.work.start_left(day_left=0), 1)
        self.work.tick(None, (43.7, 7.25), lambda: {})
        self.assertEqual(self.work.start_left(day_left=0), 0)

    def test_a_start_waits_a_minute_at_most_for_its_nights(self):
        from unittest import mock

        from orthostudio.live import nightwork

        self.work.start_at = (43.70, 7.25)
        self.work.sun = 10.0
        self.work.want(self.near, 0)
        clock = [1000.0]
        with mock.patch.object(nightwork.time, "monotonic", lambda: clock[0]):
            self.assertEqual(self.work.start_left(day_left=5), 1)  # its photos still coming
            clock[0] += 500  # however long they take, the minute starts once they are built
            self.assertEqual(self.work.start_left(day_left=0), 1)
            clock[0] += nightwork.NIGHT_WAIT_S - 1
            self.assertEqual(self.work.start_left(), 1)
            clock[0] += 2
            self.assertEqual(self.work.start_left(), 0)  # X-Plane goes on, the night made in flight
            self.assertTrue(self.work.gave_up)
            self.assertEqual(self.work.start_left(), 0)
        self.work.start(43.70, 7.25)  # the next start waits again
        self.assertFalse(self.work.gave_up)

    def test_the_near_ones_first_then_the_nearest(self):
        self.work.sun = 10.0
        self.work.want(self.near, 4)  # under the aircraft, but small
        self.work.want(self.far, 1)  # 150 km away, but sharp
        self.work.tick(10.0, (43.7, 7.25), lambda: {})
        self.assertEqual([job[1] for job in self.ex.jobs], [self.far, self.near])

    def test_the_far_ones_a_square_at_a_time(self):
        """Nearest first, the far ones went round the aircraft from square to square, each
        needing its squares' roads read again (LSGG, 2026-11-10)."""
        names = {}
        for lat, lon in ((44.05, 7.5), (43.9, 7.9), (43.1, 7.1)):
            x, y = texture_at(lat, lon)
            names[(lat, lon)] = f"{y}_{x}_BI16.dds"
        later = _Later()
        self.work._executor = later
        self.work.sun = 10.0
        for name in names.values():
            self.work.want(name, 4)
        self.work.tick(10.0, (43.98, 7.5), lambda: {})
        later.finish_one()  # the next given as soon as one is made, without a tick
        later.finish_one()
        # the square +43+007 (its centre nearer) whole first, its nearer texture first
        self.assertEqual(later.given, [names[(43.9, 7.9)], names[(43.1, 7.1)], names[(44.05, 7.5)]])
        self.assertEqual(len(self.work.made), 2)

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


class StartBarTest(unittest.TestCase):
    def test_the_bar_of_a_start_at_night_counts_its_night_textures(self):
        """It stood at 100 % while LSGG's 8,700 night textures were made (2026-11-11)."""
        from orthostudio.live.streamer import DAY_SHARE, Arrival

        a = Arrival(46.24, 6.11, "LSGG", "plugin", 0.0)
        self.assertEqual(a.with_night(0.5, 10, 0, night=False), 0.5)  # by day: the photos alone
        self.assertAlmostEqual(a.with_night(0.5, 10, 30, night=True), DAY_SHARE * 0.5)
        self.assertAlmostEqual(a.with_night(1.0, 0, 8700, night=True), DAY_SHARE)
        self.assertAlmostEqual(
            a.with_night(1.0, 0, 4350, night=True), DAY_SHARE + (1 - DAY_SHARE) / 2
        )
        self.assertAlmostEqual(a.with_night(1.0, 0, 0, night=True), 1.0)


class NightWordsTest(unittest.TestCase):
    """Once the photos are built, the loading window says night textures (the user,
    2026-11-11: « possible de mettre "Night textures to build" ? »)."""

    def test_the_plugin_is_told_how_many_are_night_ones(self):
        from orthostudio.live.gate import _wait_line

        line = _wait_line(12, "LSGG", 0.5, 30.0, 0.0, "en", building=12, night=12)
        self.assertIn(" night=12 ", line)
        self.assertTrue(line.split(" at=")[0].endswith("night=12"))  # at= stays last
        # 0 said too: the plugin keeps the last value it read (the review of 2026-11-11)
        self.assertIn(" night=0 ", _wait_line(4, "LSGG", 0.5, 30.0, 0.0, "en", building=4))

    def test_the_pause_window_says_night_textures(self):
        from orthostudio.live.pause_window import lines

        self.assertEqual(
            lines("LSGG", 7, 7, 0.0, 40.0, True, "en", night=7)[1],
            "7 night textures to build  -  40 s",
        )
        self.assertEqual(
            lines("LSGG", 7, 7, 0.0, 40.0, True, "fr", night=7)[1],
            "7 textures de nuit à construire  -  40 s",
        )
        self.assertNotIn("night", lines("LSGG", 9, 2, 3.0, 40.0, True, "en", night=7)[1])


# --- the review of 2026-11-11: what it found, mended


class _Failing:
    """An executor whose every job fails at once."""

    def __init__(self):
        self.given = []

    def submit(self, fn, job):
        from concurrent.futures import Future

        fut = Future()
        self.given.append(job[1])
        fut.set_exception(OSError("the disk is full"))
        return fut

    def shutdown(self, **_):
        pass


class _BrokenTeam(_TeamNow):
    """A team that lost a process before its first job."""

    def submit(self, fn, batch):
        from concurrent.futures.process import BrokenProcessPool

        raise BrokenProcessPool("a process of the team died")


class ReviewTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        x, y = texture_at(43.70, 7.25)
        self.near = f"{y}_{x}_BI16.dds"
        xf, yf = texture_at(43.30, 7.60)
        self.far = f"{yf}_{xf}_BI16.dds"
        self.said: list[str] = []

    def tearDown(self):
        self.tmp.cleanup()

    def work(self, executor, **kw):
        from orthostudio.live.nightwork import NightWork

        return NightWork(self.root, None, None, [], log=self.said.append, executor=executor, **kw)

    def test_a_start_by_day_leaves_no_team_for_the_night_in_flight(self):
        """Dusk in flight after a start by day: the night's own process, no team of eight
        processes left on for the whole flight."""
        team, ex = _TeamNow(), _Now()
        work = self.work(ex)
        work._make_team, work.team = team.make, 12
        work.start(43.70, 7.25)
        work.arrived()  # X-Plane flies there (the streamer's arrived)
        work.tick(2.0, (43.70, 7.25), lambda: {self.near: 0, self.far: 4})
        work.pump()
        self.assertIsNone(team.size)
        self.assertEqual(sorted(j[1] for j in ex.jobs), sorted([self.near, self.far]))

    def test_a_team_that_loses_a_process_hands_its_textures_back(self):
        """Its pool broken, the next job raised out of the streamer's loop and stopped Live."""
        from concurrent.futures.process import BrokenProcessPool

        for broken_at in ("submit", "result"):
            team, ex = (_BrokenTeam() if broken_at == "submit" else _TeamNow()), _Now()
            work = self.work(ex)
            work._make_team, work.team = team.make, 12
            work.sun = 10.0
            work.start(43.70, 7.25)
            work.want(self.near, 0)
            work.want(self.far, 4)
            work.pump()  # never raises
            if broken_at == "result":
                for fut, _ in team.futures:
                    fut.set_exception(BrokenProcessPool("a process of the team died"))
                work._pumped = 0.0
                work.pump()
            self.assertTrue(team.shut, broken_at)
            self.assertTrue(work._team_off, broken_at)
            self.assertEqual(set(work.made), {self.near, self.far}, broken_at)  # the night's own
            self.assertEqual(work.in_flight, {}, broken_at)
            self.assertTrue(any("a process of the start's team stopped" in s for s in self.said))

    def test_a_night_process_that_keeps_dying_ends_the_night_not_live(self):
        from concurrent.futures.process import BrokenProcessPool

        class Dead:
            def submit(self, fn, job):
                raise BrokenProcessPool("6 encoding pools lost within 60 s")

        work = self.work(Dead())
        work.tick(2.0, (43.70, 7.25), lambda: {self.near: 0})  # never raises
        self.assertTrue(work.broken)
        self.assertEqual(work.in_flight, {})
        self.assertIn(self.near, work.waiting)

    def test_a_start_waits_only_for_x_plane_s_squares_round_it(self):
        """Not for the whole pack's: 11,142 night textures at a Paris start, Nice's and the Alps'
        among them (2026-11-11)."""
        work = self.work(_Later())
        work.sun = 10.0
        work.start(43.70, 7.25)
        x, y = texture_at(48.85, 2.35)
        paris = f"{y}_{x}_BI16.dds"
        work.want(self.near, 0)
        work.want(paris, 4)
        self.assertEqual(work.start_left(day_left=3), 1)
        self.assertEqual(work.start_left(day_left=0), 1)
        self.assertEqual(work._start_set, {self.near})

    def test_one_job_a_texture_the_sharper_after_the_coarser(self):
        """Two at once wrote the file in either order: a coarse one last left it black where
        X-Plane looks, and the start counted it made while the other still ran."""
        ex = _Later()
        work = self.work(ex)
        work.sun = 10.0
        work.want(self.near, 4)
        work._give(None)
        work.want(self.near, 0)  # built sharper meanwhile
        work._give(None)
        self.assertEqual(ex.given, [self.near])  # not twice at once
        self.assertEqual(work.start_left(), 0)  # no start: nothing waited for
        ex.finish_one()  # the coarse one: the sharp one given right after
        self.assertEqual(ex.given, [self.near, self.near])
        self.assertEqual(work.made[self.near], 4)
        ex.finish_one()
        self.assertEqual(work.made[self.near], 0)

    def test_a_failing_night_texture_is_said_tried_once_more_then_left(self):
        ex = _Failing()
        work = self.work(ex)
        work.tick(2.0, (43.70, 7.25), lambda: {self.near: 0})
        work._pumped = 0.0
        work.pump()
        self.assertEqual(ex.given, [self.near, self.near])  # twice, not every half second
        self.assertEqual(work.waiting, {})
        failed = [s for s in self.said if "failed" in s]
        self.assertEqual(len(failed), 2)
        self.assertIn("the disk is full", failed[0])
        self.assertIn("left for this flight", failed[1])

    def test_a_night_texture_made_neutral_while_made_is_black_again(self):
        from orthostudio.live.names import night_name
        from orthostudio.live.region import NIGHT_FOLDER

        ex = _Later()
        work = self.work(ex)
        work.sun = 10.0
        lit = self.root / NIGHT_FOLDER / night_name(self.near)
        lit.parent.mkdir(parents=True)
        work.want(self.near, 0)
        work._give(None)
        work.back_to_black(self.near, keep_name=False)  # far from every square made
        lit.write_bytes(b"lit")  # the job writes it after
        ex.finish_one()
        self.assertFalse(lit.exists())
        self.assertNotIn(self.near, work.made)

    def test_the_night_textures_made_are_kept_from_one_start_to_the_next(self):
        import os

        from orthostudio.live.names import night_name
        from orthostudio.live.nightwork import night_disk_bytes
        from orthostudio.live.region import NIGHT_FOLDER

        lit = self.root / NIGHT_FOLDER / night_name(self.near)
        lit.parent.mkdir(parents=True)
        lit.write_bytes(b"lit")
        work = self.work(_Later())
        work.made[self.near] = 0
        work.stamps[self.near] = os.stat(lit).st_mtime_ns
        work._changed = True
        work.close()  # saves
        self.assertGreater(night_disk_bytes(self.root), 600_000)
        again = self.work(_Later())
        self.assertEqual(again.made, {self.near: 0})
        again.want(self.near, 0)
        self.assertEqual(again.waiting, {})  # neither made again nor waited for
        self.assertEqual(self.work(_Later(), gain=1.0).made, {})  # another strength: made again
        stamp = os.stat(lit).st_mtime_ns + 10**9
        os.utime(lit, ns=(stamp, stamp))  # made black again, or rewritten, since
        self.assertEqual(self.work(_Later()).made, {})

    def test_a_lock_left_by_a_process_that_died_is_taken_over(self):
        import os
        import time

        from orthostudio.live.nightwork import ROADS_LOCK_S, _lock_square

        file = self.root / "square.npz"
        lock = file.with_suffix(".lock")
        lock.write_text("")
        old = time.time() - ROADS_LOCK_S - 10
        os.utime(lock, (old, old))
        t0 = time.monotonic()
        self.assertEqual(_lock_square(file), lock)
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_a_roads_read_that_could_not_be_made_is_not_kept_roadless(self):
        from unittest import mock

        from orthostudio.dsf import xp12
        from orthostudio.live import nightwork

        dsf = self.root / "+43+007.dsf"
        dsf.write_bytes(b"dsf")
        pack = self.root / "pack"
        apart = mock.patch.object(nightwork, "_READ_APART", False)
        short = mock.patch.object(xp12, "read_global_scenery_dsf", side_effect=MemoryError)
        with apart, short, self.assertRaises(MemoryError):
            nightwork._roads_of(dsf, pack)
        self.assertEqual(list((pack / nightwork.ROADS_FOLDER).glob("*.npz")), [])
        damaged = mock.patch.object(xp12, "read_global_scenery_dsf", side_effect=ValueError)
        with apart, damaged:
            self.assertEqual(len(nightwork._roads_of(dsf, pack)), 0)  # damaged: no lights
        self.assertEqual(len(list((pack / nightwork.ROADS_FOLDER).glob("*.npz"))), 1)

    def test_no_light_greener_or_bluer_than_red_out_of_bc1(self):
        from orthostudio.live.imaging import encode_bc1

        x, y = texture_at(43.70, 7.27)
        streets = [night.Roads.of(grid_of_streets(43.70, 7.27, span=0.03))]
        lights = night.draw(streets, None, x, y, 16, 1024, seed=1)
        for level in night.lit_levels(night.night_rgb(lights, 0.75))[:3]:
            side = level.shape[0]
            plain = decode_bc1(encode_bc1(level), side).astype(int)
            warm = decode_bc1(night.warm_bc1(encode_bc1(level)), side).astype(int)
            self.assertGreater(int((plain[..., 1] > plain[..., 0] + 1).sum()), 0, side)
            self.assertEqual(int((warm[..., 1] > warm[..., 0] + 1).sum()), 0, side)
            self.assertEqual(int((warm[..., 2] > warm[..., 0] + 1).sum()), 0, side)
            self.assertLess(np.abs(warm - plain).mean(), 0.2, side)
            low = level.max(axis=-1)
            self.assertEqual(int(((low > 0) & (low < night.FLOOR)).sum()), 0, side)

    def test_night_lights_off_take_the_night_files_away(self):
        from orthostudio.live.nightwork import MADE_RECORD, ROADS_FOLDER
        from orthostudio.live.region import NIGHT_FOLDER, set_night

        pack = self.root / "pack"
        set_night(pack, True, log=self.said.append)
        (pack / NIGHT_FOLDER).mkdir(parents=True)
        (pack / NIGHT_FOLDER / "23904_34080_BI16_LIT.dds").write_bytes(b"lit")
        (pack / ROADS_FOLDER).mkdir(parents=True)
        (pack / ROADS_FOLDER / "a-1.npz").write_bytes(b"roads")
        (pack / MADE_RECORD).write_text("{}")
        self.assertTrue(any((pack / NIGHT_FOLDER).iterdir()))
        set_night(pack, False, log=self.said.append)
        self.assertFalse(
            any((pack / NIGHT_FOLDER).iterdir()) if (pack / NIGHT_FOLDER).exists() else False
        )
        self.assertFalse((pack / ROADS_FOLDER / "a-1.npz").exists())
        self.assertFalse((pack / MADE_RECORD).exists())

    def test_a_start_removes_the_night_s_half_written_files(self):
        from orthostudio.live.nightwork import ROADS_FOLDER
        from orthostudio.live.region import NIGHT_FOLDER, TEMP_PREFIX, remove_temps

        pack = self.root / "pack"
        (pack / NIGHT_FOLDER).mkdir(parents=True)
        (pack / ROADS_FOLDER).mkdir(parents=True)
        half = [
            pack / NIGHT_FOLDER / f"{TEMP_PREFIX}a",
            pack / ROADS_FOLDER / ".+43+007-1.123.tmp",
            pack / ROADS_FOLDER / "+43+007-1.lock",
        ]
        kept = [pack / NIGHT_FOLDER / "a_LIT.dds", pack / ROADS_FOLDER / "+43+007-1.npz"]
        for f in half + kept:
            f.write_bytes(b"x")
        self.assertEqual(remove_temps(pack), 3)
        self.assertEqual([f.exists() for f in half + kept], [False] * 3 + [True] * 2)
