# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The roads of a DSF: 32-bit pools in their four encodings, the network commands among the others,
the classes of X-Plane's road subtypes, and, where X-Plane is installed, Nice's square."""

import struct
import unittest
from pathlib import Path

import numpy as np

from orthostudio.dsf.networks import read_pool32, road_class, walk_networks


def pool32(columns: list[np.ndarray], encodings: list[int]) -> bytes:
    """A ``PO32`` payload of ``columns``, each written in its encoding (bit 0 differenced, bit 1
    run-length)."""
    n = len(columns[0])
    out = bytearray(struct.pack("<IB", n, len(columns)))
    for col, enc in zip(columns, encodings, strict=True):
        out.append(enc)
        values = col.astype(np.uint32)
        if enc & 1:
            values = np.diff(values.astype(np.int64), prepend=0).astype(np.uint32)
        if enc & 2:
            i = 0
            while i < n:
                j = i
                while j < n and j - i < 127 and values[j] == values[i]:
                    j += 1
                if j - i >= 2:
                    out.append(0x80 | (j - i))
                    out += struct.pack("<I", int(values[i]))
                    i = j
                else:
                    out.append(1)
                    out += struct.pack("<I", int(values[i]))
                    i += 1
        else:
            out += values.astype("<u4").tobytes()
    return bytes(out)


class Pool32Test(unittest.TestCase):
    def test_every_encoding_reads_back(self):
        a = np.array([5, 5, 5, 9, 4000000000, 4000000000, 7], dtype=np.uint32)
        b = np.array([1, 2, 3, 4, 5, 6, 7], dtype=np.uint32)
        for enc in (0, 1, 2, 3):
            got = read_pool32(pool32([a, b], [enc, enc]))
            np.testing.assert_array_equal(got[:, 0], a, err_msg=f"encoding {enc}")
            np.testing.assert_array_equal(got[:, 1], b, err_msg=f"encoding {enc}")


class WalkTest(unittest.TestCase):
    def setUp(self):
        self.pools = [np.array([[7.0 + i / 100, 43.0 + i / 100] for i in range(10)])]

    def test_chains_and_the_commands_round_them(self):
        cmds = (
            bytes([4]) + struct.pack("<H", 0)  # definition 0
            + bytes([6, 50])  # subtype 50, a local road
            + bytes([7]) + struct.pack("<H", 3)  # an object, stepped over
            + bytes([12]) + struct.pack("<HB", 0, 2) + struct.pack("<HH", 1, 2)  # a polygon
            + bytes([9, 3]) + struct.pack("<HHH", 0, 1, 2)  # a chain of three points
            + bytes([2]) + struct.pack("<I", 5)  # junction offset 5
            + bytes([6, 220])  # a power line
            + bytes([10]) + struct.pack("<HH", 0, 3)  # points 5, 6, 7
            + bytes([32, 3]) + b"abc"  # a comment
            + bytes([11, 2]) + struct.pack("<II", 0, 4)  # points 5 and 9
        )  # fmt: skip
        chains = walk_networks(cmds, self.pools)
        self.assertEqual([c.subtype for c in chains], [50, 220, 220])
        np.testing.assert_allclose(chains[0].points[:, 0], [7.00, 7.01, 7.02])
        np.testing.assert_allclose(chains[1].points[:, 0], [7.05, 7.06, 7.07])
        np.testing.assert_allclose(chains[2].points[:, 0], [7.05, 7.09])

    def test_a_command_that_does_not_exist_is_refused(self):
        with self.assertRaises(ValueError):
            walk_networks(bytes([99]), self.pools)


class RoadClassTest(unittest.TestCase):
    def test_families_and_variants(self):
        self.assertEqual(road_class(50), ("residential", False))
        self.assertEqual(road_class(59), ("residential", True))
        self.assertEqual(road_class(14), ("primary", False))
        self.assertEqual(road_class(39), ("secondary", True))
        self.assertEqual(road_class(100), ("motorway", False))
        self.assertEqual(road_class(120), ("motorway_link", False))
        for none in (151, 152, 153, 220, 0, 5):
            self.assertIsNone(road_class(none))


DSF = (
    Path.home()
    / "X-Plane 12/Global Scenery/X-Plane 12 Global Scenery/Earth nav data/+40+000/+43+007.dsf"
)


@unittest.skipUnless(DSF.is_file(), "X-Plane 12's Global Scenery for +43+007 is not here")
class NiceTest(unittest.TestCase):
    def test_the_roads_of_nice(self):
        from orthostudio.dsf.networks import chains_of
        from orthostudio.dsf.xp12 import read_global_scenery_dsf

        names, chains = chains_of(
            read_global_scenery_dsf(DSF, type("T", (), {"name": "+43+007"})())
        )
        self.assertIn("lib/g10/roads_EU.net", names)
        self.assertGreater(len(chains), 30000)  # 41,920 on 2026-11-10
        pts = np.concatenate([c.points for c in chains])
        self.assertTrue((pts[:, 0] >= 6.99).all() and (pts[:, 0] <= 8.01).all())
        self.assertTrue((pts[:, 1] >= 42.99).all() and (pts[:, 1] <= 44.01).all())


if __name__ == "__main__":
    unittest.main()
