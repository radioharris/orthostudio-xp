# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The imagery sources (OrthoStudio XP's), decoding, levels and BC1."""

import io
import math
import struct
import unittest

from live_helpers import bc1_texel
from orthostudio.live.providers import (
    Provider,
    cache_key,
    covers,
    fallback_for,
    listing,
    no_imagery,
    quadkey,
    registry,
    tile_url,
)

try:
    import ispc_texcomp  # noqa: F401
    import numpy as np
    from PIL import Image
except ImportError:  # pragma: no cover - outside the project's environment
    np = None


class ProvidersTest(unittest.TestCase):
    def test_quadkey(self):
        # Microsoft's example: tile (3, 5) at level 3 is quadkey 213
        self.assertEqual(quadkey(3, 5, 3), "213")
        self.assertEqual(quadkey(16968, 11760, 15), "120221223221000")

    def test_urls(self):
        template = "https://t{switch:a,b}.example/{zoom}/{x}/{y}/{quadkey}"
        p = Provider(code="T", url_template=template, max_zl=19)
        self.assertEqual(tile_url(p, 3, 5, 3), "https://ta.example/3/3/5/213")  # (3 + 5) % 2 = 0
        self.assertEqual(tile_url(p, 3, 4, 3), "https://tb.example/3/3/4/211")
        bi = registry()["BI"]
        self.assertTrue(
            tile_url(bi, 16968, 11760, 15).startswith(
                "https://ecn.t0.tiles.virtualearth.net/tiles/a120221223221000.jpeg"
            )
        )
        # the same piece, another server
        self.assertTrue(tile_url(bi, 16968, 11760, 15, shift=1).startswith("https://ecn.t1."))

    def test_no_imagery(self):
        bi = registry()["BI"]
        self.assertTrue(no_imagery(bi, {"x-ve-tile-info": "no-tile"}))
        self.assertFalse(no_imagery(bi, {"Content-Type": "image/jpeg"}, b"a photo"))

    def test_osxp_sources_and_bing_beyond_a_country(self):
        sources = registry()
        self.assertEqual(next(iter(sources)), "BI")  # OrthoStudio XP's order: Bing Maps first
        self.assertIsNone(fallback_for(sources["BI"]))
        self.assertIsNone(fallback_for(sources["Arc"]))  # the whole world: nothing beyond
        spain = sources["SP"]
        self.assertEqual(fallback_for(spain).code, "BI")
        self.assertTrue(covers(spain, *_tile(40.42, -3.70, 16), 16))  # Madrid
        self.assertFalse(covers(spain, *_tile(45.76, 4.84, 16), 16))  # Lyon
        self.assertEqual(cache_key("BI"), "BI")  # where Live's imagery always was
        self.assertEqual(cache_key("SP"), "SP+BI")
        codes = [p["code"] for p in listing()]
        self.assertIn("PDOK", codes)
        self.assertNotIn("NL", codes)  # the very same imagery as PDOK: listed once


def _tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    n = 2**zoom
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return int((lon + 180) / 360 * n), int(y)


@unittest.skipIf(np is None, "needs numpy, Pillow and ispc_texcomp")
class ImagingTest(unittest.TestCase):
    def test_decode_piece(self):
        from orthostudio.live.imaging import decode_piece

        buf = io.BytesIO()
        Image.new("RGB", (256, 256), (10, 200, 30)).save(buf, "PNG")
        rgb = decode_piece(buf.getvalue())
        self.assertEqual(rgb.shape, (256, 256, 3))
        self.assertEqual(tuple(rgb[5, 5]), (10, 200, 30))
        small = io.BytesIO()
        Image.new("RGB", (64, 64)).save(small, "PNG")
        with self.assertRaises(ValueError):
            decode_piece(small.getvalue())

    def test_levels_in_linear_light(self):
        from orthostudio.live.imaging import mip_chain

        img = np.zeros((4, 4, 3), dtype=np.uint8)
        img[::2, ::2] = 255  # a quarter of the texels white: 25 % of the light
        chain = mip_chain(img)
        self.assertEqual([lv.shape[0] for lv in chain], [4, 2, 1])
        # 25 % light is 136 once gamma-encoded, not the 64 an average of the stored values gives
        self.assertAlmostEqual(int(chain[1][0, 0, 0]), round(255 * 0.25 ** (1 / 2.2)), delta=1)

    def test_four_colour(self):
        from orthostudio.live.imaging import four_colour

        red, blue = 0xF800, 0x001F
        flat = struct.pack(
            "<HHI", red, red, 0xFFFFFFFF
        )  # every index 3: black in three-colour mode
        three = struct.pack("<HHI", blue, red, 0b00_01_00_01)  # blue < red: three-colour mode
        before = flat + three
        out = four_colour(before)
        self.assertEqual(
            bc1_texel(out, 8, 0, 0), (255, 0, 0)
        )  # the flat block is all red, not black
        self.assertEqual(struct.unpack_from("<HH", out, 8), (red, blue))  # endpoints swapped
        self.assertEqual(bc1_texel(out, 8, 4, 0), bc1_texel(before, 8, 4, 0))  # the colours kept
        self.assertEqual(bc1_texel(out, 8, 5, 0), bc1_texel(before, 8, 5, 0))

    def test_encode_every_level(self):
        from orthostudio.live import dds
        from orthostudio.live.imaging import encode_levels

        img = np.zeros((256, 256, 3), dtype=np.uint8)
        img[:, :128] = (200, 30, 30)
        img[:, 128:] = (30, 30, 200)
        levels = encode_levels(img)
        self.assertEqual([len(b) for b in levels], [length for _, length in dds.mip_layout()[4:]])
        self.assertGreater(bc1_texel(levels[0], 256, 10, 10)[0], 150)
        self.assertGreater(bc1_texel(levels[0], 256, 250, 10)[2], 150)
        for blob in levels:  # nothing left in three-colour mode
            for i in range(0, len(blob), 8):
                c0, c1 = struct.unpack_from("<HH", blob, i)
                self.assertTrue(c0 > c1 or (c0 == c1 and blob[i + 4 : i + 8] == bytes(4)))


if __name__ == "__main__":
    unittest.main()
