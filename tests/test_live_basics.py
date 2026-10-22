# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP Live's building blocks: rings, route, cache, sparse files, texture names, DDS
layout."""

import os
import tempfile
import time
import unittest
from pathlib import Path

from orthostudio.live import dds
from orthostudio.live.cache import IMAGE, NO_IMAGERY, ChunkCache
from orthostudio.live.filesystem import allocated
from orthostudio.live.geo import parse_texture_name
from orthostudio.live.rings import (
    file_top,
    finest_for_quality,
    finest_readable,
    ring_km,
    top_level_for,
    zoom_for,
)
from orthostudio.live.route import AirportIndex, Route, airport_positions
from orthostudio.live.texture import chunk_grid, parent_keys, piece_keys, pieces_of, write_sparse

APT = b"""I
1200 Version
1     12 0 0 LFMN Nice/Cote d'Azur
1302 icao_code LFMN
1302 datum_lat 43.665405556
1302 datum_lon 7.214983333
100 45.11 1 0 0.00 1 3 0 04R 43.6467373 7.2024975 0 0 3 0 0 1 22L 43.6656148 7.2284692 0 0 3 0 0 1
1     1411 0 0 LSGG Geneve
100 50.00 1 0 0.00 1 3 0 04 46.2200000 6.0900000 0 0 3 0 0 1 22 46.2500000 6.1300000 0 0 3 0 0 1
99
"""


class RingsTest(unittest.TestCase):
    def test_law(self):
        self.assertEqual(ring_km(1, 16), 23.7)  # 21 km measured in flight, x 1.13 (2026-10-22)
        self.assertEqual(ring_km(1, 18), 23.7 / 4)
        self.assertEqual(top_level_for(5, 16, finest=1), 1)  # High: never level 0
        self.assertEqual(top_level_for(5, 16, finest=0), 0)
        self.assertEqual(top_level_for(40, 16, finest=0), 2)  # 42.9 km ring, widened by the margin
        self.assertEqual(top_level_for(200, 16, finest=0), 4)
        self.assertEqual(top_level_for(10, 18, finest=0), 2)
        self.assertEqual(top_level_for(5, 16, finest=-1), 0)  # 8192 px: the streamer's own rule
        self.assertEqual(top_level_for(10, 16, finest=-1), 0)

    def test_settings(self):
        self.assertEqual(finest_for_quality(5), 0)
        self.assertEqual(finest_for_quality(4), 1)
        self.assertEqual(finest_for_quality(None), 0)

    def test_zooms_as_autoortho_sizes_them(self):
        """getortho.py: below the mesh's zoom a smaller texture, one zoom above an 8192 px one."""
        self.assertEqual(file_top(16, 16), 0)  # 4096 px
        self.assertEqual(file_top(16, 14), 2)  # 1024 px
        self.assertEqual(file_top(16, 17), -1)  # 8192 px
        self.assertEqual(file_top(16, 19), -1)  # one zoom above at most
        self.assertEqual(file_top(18, 19), -1)
        self.assertEqual(file_top(18, 16), 2)
        self.assertEqual(zoom_for(18, 14, 19), 19)  # the mesh's ZL18 textures: its airports
        self.assertEqual(zoom_for(16, 14, 19), 14)
        # X-Plane at Maximum reads a file's sharpest level; at High, the sharper of two readings
        self.assertEqual(finest_readable(0, 0), 0)
        self.assertEqual(finest_readable(-1, 0), -1)
        self.assertEqual(finest_readable(2, 0), 2)
        self.assertEqual(finest_readable(0, 1), 1)
        self.assertEqual(finest_readable(-1, 1), 0)  # 4096 of 8192: one level down from the file
        self.assertEqual(finest_readable(2, 1), 2)  # 1024 px: under High's 2048 already


class RouteTest(unittest.TestCase):
    def test_airports_and_distance(self):
        with tempfile.TemporaryDirectory() as tmp:
            apt = Path(tmp) / "apt.dat"
            apt.write_bytes(APT)
            found = airport_positions(apt, ["lfmn", "LSGG"])
        self.assertAlmostEqual(found["LFMN"][0], 43.665405556)  # the datum
        self.assertAlmostEqual(found["LSGG"][0], 46.235)  # the middle of its runway
        r = Route(["LFMN", "LSGG"], [found["LFMN"], found["LSGG"]])
        self.assertAlmostEqual(r.distance_km(*found["LFMN"]), 0.0, places=6)
        mid = ((found["LFMN"][0] + found["LSGG"][0]) / 2, (found["LFMN"][1] + found["LSGG"][1]) / 2)
        self.assertLess(r.distance_km(*mid), 1.0)
        self.assertAlmostEqual(
            r.distance_km(mid[0], mid[1] + 1.0), 77.0, delta=2.0
        )  # 1 degree of longitude at 45 N
        tiles = r.tiles_within(50)
        self.assertIn((43, 7), tiles)
        self.assertIn((46, 6), tiles)
        self.assertNotIn((43, 0), tiles)

    def test_every_airport_read_once(self):
        """The airports' positions read once in a thread of their own: a click on Start Flight is
        placed at once (one pass for one airport kept the streamer's loop 3 s, 2026-09-29)."""
        with tempfile.TemporaryDirectory() as tmp:
            apt = Path(tmp) / "apt.dat"
            apt.write_bytes(APT)
            every = airport_positions(apt, None)
            self.assertEqual(set(every) >= {"LFMN", "LSGG"}, True)
            index = AirportIndex(apt)
            end = time.time() + 5
            while index._all is None and time.time() < end:
                time.sleep(0.01)
            self.assertEqual(index._all, every)
            self.assertEqual(index("lfmn"), every["LFMN"])
            self.assertIsNone(index("ZZZZ"))
            self.assertIsNone(AirportIndex(Path(tmp) / "none.dat")("LFMN"))  # no file: none


class CacheTest(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = ChunkCache(Path(tmp) / "c.sqlite")
            c.put_many("BI", [((12, 1, 2), IMAGE, b"jpeg"), ((12, 1, 3), NO_IMAGERY, None)])
            got = c.get_many("BI", [(12, 1, 2), (12, 1, 3), (12, 9, 9)])
            c.close()
        self.assertEqual(got, {(12, 1, 2): (IMAGE, b"jpeg"), (12, 1, 3): (NO_IMAGERY, None)})

    def test_a_rollback_journal_not_a_write_ahead_log(self):
        # In the WAL every piece was written twice: 2.5 times its bytes, 1.4 with the journal.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.sqlite"
            old = __import__("sqlite3").connect(str(path))
            old.execute("PRAGMA journal_mode=WAL")  # a cache from before
            old.close()
            c = ChunkCache(path)
            mode = c.db.execute("PRAGMA journal_mode").fetchone()[0]
            c.close()
        self.assertEqual(mode, "truncate")

    def test_used_from_another_thread(self):
        # The streamer opens it in the main thread and its builder thread uses it.
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            c = ChunkCache(Path(tmp) / "c.sqlite")
            out = {}
            t = threading.Thread(
                target=lambda: out.update(c.get_many("BI", [(1, 0, 0)]) or {"ok": 1})
            )
            t.start()
            t.join()
            c.close()
        self.assertEqual(out, {"ok": 1})


class SparseTest(unittest.TestCase):
    def test_grid(self):
        tex = parse_texture_name("23520_33936_BI16.dds")
        self.assertEqual(chunk_grid(tex, 0), (16, 33936, 23520, 16))
        self.assertEqual(chunk_grid(tex, 1), (15, 16968, 11760, 8))
        # 256 px: from ZL13's four pieces, shrunk, never Bing's ZL12 of other colours
        self.assertEqual(chunk_grid(tex, 3), (13, 4242, 2940, 2))
        self.assertEqual(chunk_grid(tex, 4), (13, 4242, 2940, 2))
        self.assertEqual(len(piece_keys(tex, 1)), 64)
        self.assertEqual(chunk_grid(tex, -1), (17, 67872, 47040, 32))  # 8192 px: ZL17 pieces
        self.assertEqual(len(piece_keys(tex, -1)), pieces_of(-1))
        self.assertEqual((pieces_of(-1), pieces_of(0), pieces_of(4)), (1024, 256, 4))
        # a source whose ZL13 is other imagery too: Spain's, Japan's, Esri Clarity's from ZL14
        spain = parse_texture_name("24608_32496_SP16.dds")
        self.assertEqual(chunk_grid(spain, 4), (14, 8124, 6152, 4))
        self.assertEqual(pieces_of(4, 16, 14), 16)
        from orthostudio.live.texture import photo_floor

        self.assertEqual(
            [photo_floor(c) for c in ("BI", "Arc", "SP", "JP", "Arc@")], [13] * 2 + [14] * 3
        )
        airport = parse_texture_name("94080_135744_BI18.dds")  # ZL18: its 256 px from ZL14
        self.assertEqual(chunk_grid(airport, 4)[0], 14)
        self.assertEqual(pieces_of(4, 18), 1)

    def test_no_photo_coarser_than_zl13(self):
        """A 256 px level made of ZL13's pieces is 256 px, and a missing piece is never made from
        a parent coarser than ZL13 (2026-10-19)."""
        import io

        from PIL import Image

        from orthostudio.live.texture import MIN_PHOTO_ZL, assemble

        tex = parse_texture_name("23520_33936_BI16.dds")
        z, x0, y0, n = chunk_grid(tex, 4)
        pieces = {}
        for j in range(n):
            for i in range(n):
                buf = io.BytesIO()
                Image.new("RGB", (256, 256), (40 * (i + 2 * j), 90, 30)).save(buf, "JPEG")
                pieces[(z, x0 + i, y0 + j)] = buf.getvalue()
        img, from_parent, blank = assemble(tex, 4, pieces, {})
        self.assertEqual(img.shape, (256, 256, 3))
        self.assertEqual((from_parent, blank), (0, 0))
        self.assertTrue(abs(int(img[64, 64, 0]) - 0) < 8 and abs(int(img[192, 192, 0]) - 120) < 8)
        self.assertEqual(MIN_PHOTO_ZL, 13)
        self.assertEqual([k[0] for k in parent_keys((15, 100, 100))], [14, 13])
        self.assertEqual(parent_keys((13, 100, 100)), [])
        self.assertEqual(
            parent_keys((16, 33936, 23520), depth=2), [(15, 16968, 11760), (14, 8484, 5880)]
        )

    def test_write_sparse(self):
        layout = dds.mip_layout()
        levels = [bytes([lvl]) * length for lvl, (_, length) in enumerate(layout)][4:]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "23520_33936_BI16.dds"
            path.write_bytes(b"old")
            write_sparse(path, 4, levels)
            data = path.read_bytes()
            taken = allocated(path)
            self.assertEqual(os.listdir(tmp), ["23520_33936_BI16.dds"])  # no temporary file left
        self.assertEqual(len(data), dds.file_size())
        self.assertEqual(data[:128], dds.header())
        self.assertEqual(data[layout[4][0]], 4)
        self.assertEqual(data[layout[12][0]], 12)
        self.assertEqual(set(data[128 : layout[4][0]]), {0})  # the hole reads as zeros
        self.assertLess(taken, 256 * 1024)  # and takes no disk
        with self.assertRaises(ValueError):
            write_sparse(Path(tmp) / "x.dds", 3, levels)

    def test_files_of_every_size(self):
        """1024 px for a zoom two below the mesh's, 8192 px one above: the header says so."""
        with tempfile.TemporaryDirectory() as tmp:
            for top, side in ((2, 1024), (-1, 8192)):
                layout = dds.level_layout(top)
                levels = [bytes([k]) * length for k, (_, length) in enumerate(layout)]
                path = Path(tmp) / f"{side}.dds"
                write_sparse(path, top, levels, file_top=top)
                data = path.read_bytes()
                self.assertEqual(len(data), dds.file_size(side))
                self.assertEqual(data[:128], dds.header(side))
                self.assertEqual(data[layout[1][0]], 1)  # the level under the sharpest one


if __name__ == "__main__":
    unittest.main()
