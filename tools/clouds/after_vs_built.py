# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The base rule measured on one replaced cloud: After as the cloud mode shows it (the tile's
Before picture with the cloud's preview over it) against the tile as built (its textures), on the
pixels of the preview, inside the cloud, on its soft edge and outside (``docs/benchmarks/
clouds.md`` B5). The engine runs (``osxp serve``) on the same home; the cloud is held.

    OSXP_HOME=<home> python tools/clouds/after_vs_built.py +56-131 z9lo5c8m strip.png --port 8696

The strip written is Before, After, the texture as built, and their difference times four.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from orthostudio.api.clouds_api import previews_dir
from orthostudio.clouds.picture import draw
from orthostudio.clouds.tile import read_tile, tile_folder
from orthostudio.graph import Store
from orthostudio.home import default_store_root, default_tiles_root
from orthostudio.imagery.grid import wgs84_to_tile
from orthostudio.install.library import default_library_path
from orthostudio.model import TileRef
from orthostudio.pipeline.filing import tile_home


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tile")
    ap.add_argument("cloud")
    ap.add_argument("strip", type=Path)
    ap.add_argument("--port", type=int, default=8642)
    a = ap.parse_args()
    tile = TileRef.parse(a.tile)
    url = f"http://127.0.0.1:{a.port}/api/clouds/{tile.name.replace('+', '%2B')}"
    with urllib.request.urlopen(url) as r:
        doc = json.load(r)
    row = next(c for c in doc["clouds"] if c["id"] == a.cloud)
    zl = int(doc["picture"]["level"])
    home, _gone = tile_home(tile, atelier=default_tiles_root(), custom_scenery=None,
                            library_path=default_library_path())  # fmt: skip
    with Store(default_store_root()) as store:
        held = read_tile(tile_folder(tile, workshop=default_tiles_root(), home=home), store)
        if held is None or held.held(a.cloud) is None:
            raise SystemExit(f"{a.cloud} is not held by {tile.name}")
        art = store.path(held.textures_key)
        dsf = held.folder / tile.dsf_relpath
        before = draw(tile, zl, dsf=dsf, textures=art / "textures",
                      originals=art / "clouds" / "originals" / "textures")  # fmt: skip
        built = draw(tile, zl, dsf=dsf, textures=art / "textures")
    png = Image.open(previews_dir(tile) / row["preview"]["name"]).convert("RGBA")
    pv = np.asarray(png).astype(np.float32)
    _s, w, n, _e = row["preview"]["bounds"]
    gx, gy = wgs84_to_tile(n, w, zl)
    x0, y0 = round(gx * 256) - before.x0, round(gy * 256) - before.y0
    h, wd = pv.shape[:2]
    b = before.rgb[y0 : y0 + h, x0 : x0 + wd].astype(np.float32)
    t = built.rgb[y0 : y0 + h, x0 : x0 + wd].astype(np.float32)
    alpha = pv[..., 3:4] / 255
    shown = alpha * pv[..., :3] + (1 - alpha) * b
    inside, outside = pv[..., 3] == 255, pv[..., 3] == 0
    edge = ~inside & ~outside
    d = np.abs(t - shown)
    parts = {"inside": inside, "edge": edge, "outside": outside}
    print(json.dumps({
        "level": zl,
        "after_vs_built": {name: d[m].mean(0).round(2).tolist() for name, m in parts.items()},
        "replacement_inside": np.abs(t - b)[inside].mean(0).round(1).tolist(),
        "pixels": {"inside": int(inside.sum()), "edge": int(edge.sum())},
    }))  # fmt: skip
    strip = np.concatenate([b, shown, t, np.clip(d * 4, 0, 255)], axis=1)
    Image.fromarray(strip.astype(np.uint8)).save(a.strip)


if __name__ == "__main__":
    main()
