# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A texture built from imagery pieces with OrthoStudio XP's decoder, mips and encoder.

Runs in the project's environment (``uv run python -m unittest``); skipped without numpy, Pillow and
ispc_texcomp.
"""

import io
import tempfile
import unittest
from pathlib import Path

try:
    import ispc_texcomp  # noqa: F401
    import numpy as np
    from PIL import Image
except ImportError:  # pragma: no cover - outside the project's environment
    np = None

from live_helpers import bc1_texel
from orthostudio.live import dds
from orthostudio.live.geo import parse_texture_name
from orthostudio.live.texture import build_job, parent_keys, piece_keys


def jpeg(rgb):
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), rgb).save(buf, "JPEG", quality=95)
    return buf.getvalue()


@unittest.skipIf(np is None, "needs numpy, Pillow and ispc_texcomp")
class BuildTest(unittest.TestCase):
    def test_level_1_texture(self):
        name = "23520_33936_BI16.dds"
        keys = piece_keys(parse_texture_name(name), 1)
        pieces = {k: jpeg((200, 40, 40)) for k in keys}
        pieces[keys[0]] = None  # no imagery there: taken from its parent
        parents = {parent_keys(keys[0])[0]: jpeg((40, 40, 200))}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            _, level, from_parent, blank = build_job((name, 1, pieces, parents, str(path)))
            data = path.read_bytes()
        self.assertEqual((level, from_parent, blank), (1, 1, 0))
        self.assertEqual(len(data), dds.file_size())
        layout = dds.mip_layout()
        self.assertEqual(set(data[128 : layout[1][0]]), {0})  # level 0 is a hole
        # decode level 1 (2048 px) and look at a piece from the imagery and the one from the parent
        start, length = layout[1]
        level1 = data[start : start + length]
        self.assertGreater(bc1_texel(level1, 2048, 1000, 1000)[0], 150)  # red imagery
        self.assertGreater(bc1_texel(level1, 2048, 100, 100)[2], 150)  # blue, from the parent


if __name__ == "__main__":
    unittest.main()
