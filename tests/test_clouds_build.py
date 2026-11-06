# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A build lays the replaced clouds it is given, whole or not at all, and its textures artefact
records them (``clouds.md`` R1, R5, R6): the real ``tile.textures`` rule through the graph's
executor, over a local tile server (``test_pipeline_textures.TileServer``), +43+005 at ZL12."""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from PIL import Image

from orthostudio.clouds.laying import patches_of
from orthostudio.clouds.tile import artefact_originals, colours_near, got_parts_dir
from orthostudio.graph import Executor, Node, Source, Store, artifact_key, digest_bytes
from orthostudio.imagery.chunks import ChunkStore
from orthostudio.imagery.grid import TextureId, texture_name
from orthostudio.model import TileRef
from orthostudio.pipeline import build as build_mod
from orthostudio.pipeline.build import TILE_TEXTURES, BuildEnv, TileTexturesParams
from orthostudio.pipeline.textures import resolve_encoder
from orthostudio.textures.bcdecode import decode_dds
from orthostudio.textures.clouds import encode_part, pieces
from test_pipeline_textures import TileServer

TILE = TileRef(43, 5)
ZL = 12
WEST = TextureId(til_x=2096, til_y=1488, zl=ZL, provider="T")
EAST = TextureId(til_x=2112, til_y=1488, zl=ZL, provider="T")
NEUTRAL = [[1.0, 1.0, 1.0], [0.0, 0.0, 0.0]]
RED, BLUE = (230, 20, 20), (20, 20, 230)


def _ring(lat0: float, lon0: float, lat1: float, lon1: float) -> list[float]:
    return [lat0, lon0, lat0, lon1, lat1, lon1, lat1, lon0, lat0, lon0]


def _png(rgb: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), rgb).save(buf, format="PNG")
    return buf.getvalue()


class Tile:
    """A data folder (store, pieces, parts) and the tile's textures made by the real step."""

    def __init__(self, root: Path, server: TileServer) -> None:
        self.root, self.server = root, server
        self.store = Store(root / "store", fsync=False)
        self.chunks = root / "chunks"
        self.encoder, self.encoder_version = resolve_encoder("auto")

    def dsf(self, textures: Sequence[TextureId]) -> Source:
        listing = {
            "format": "osxp-dsf-textures-1",
            "textures": [
                {
                    "til_x": t.til_x,
                    "til_y": t.til_y,
                    "zl": t.zl,
                    "provider": t.provider,
                    "kinds": ["land"],
                }
                for t in textures
            ],
        }
        key, recipe = artifact_key("tile.dsf", 4, {"tile": TILE.name, "t": listing["textures"]}, {})
        if not self.store.has(key):
            with self.store.begin("tile.dsf", key, "dir") as b:
                (b.out / "terrain").mkdir()
                for t in textures:
                    (b.out / "terrain" / f"{texture_name(t)}.ter").write_text("A\n800\nTERRAIN\n")
                (b.out / "textures.json").write_text(json.dumps(listing))
                b.commit(version=4, recipe=recipe, inputs=[])
        info = self.store.info(key)
        assert info is not None
        return Source(info.digest, info.path, "dsf")

    def textures(self, clouds: Sequence[dict[str, Any]] = (), **params: Any) -> Path:
        """The ``tile.textures`` artefact laying ``clouds``; its folder."""
        env = BuildEnv(
            store=self.store,
            store_root=self.store.root,
            chunks_root=self.chunks,
            workdir=self.root / "work",
            global_scenery=None,
            dsftool=None,
            registry={"T": self.server.provider()},
            workers=0,
            library_path=self.root / "library.sqlite",
        )
        node = SimpleNamespace(
            node_id=f"{TILE.name}/textures",
            cancel_event=threading.Event(),
            progress=lambda _f, _m: None,
            set_idle=lambda _b: None,
            wrote=lambda _n: None,
            fetched=lambda _n: None,
        )
        p = TileTexturesParams(
            tile=TILE.name,
            encoder=self.encoder,
            encoder_version=self.encoder_version,
            clouds=list(clouds),
            **params,
        )
        inputs = {"dsf": self.dsf([WEST, EAST]), "masks": None}
        with build_mod._active_env(env, node):  # type: ignore[arg-type]
            done = Executor(self.store).run(Node(TILE_TEXTURES, p, inputs))
        return Path(done.target.path)

    def manifest(self, folder: Path) -> dict[str, Any]:
        return json.loads((folder / "manifest.json").read_text())

    def cloud(
        self,
        cid: str,
        rings: list[list[float]],
        rgb: tuple[int, int, int],
        *,
        colours: Sequence[float] = (0.0, 0.0, 0.0),
        zones: Sequence[Any] = (),
    ) -> dict[str, Any]:
        """A cloud as a check would have made it: its parts of solid ``rgb`` pieces frozen in
        the data folder, laid on the pieces the tile's textures have now."""
        c: dict[str, Any] = {"id": cid, "source": "C", "match": NEUTRAL, "rings": rings}
        reached = patches_of(c, TILE, [WEST, EAST], lambda t: t.zl)
        store = ChunkStore(self.chunks, fsync=False)
        parts: dict[str, list[Any]] = {}
        images: dict[str, str] = {}
        folder = got_parts_dir(self.chunks)
        folder.mkdir(parents=True, exist_ok=True)
        for name, patch in reached.items():
            t = WEST if name == texture_name(WEST) else EAST
            data = encode_part({k: _png(rgb) for k in pieces(patch, t)})
            digest = digest_bytes(data)
            (folder / f"{digest}.part").write_bytes(data)
            parts[name] = [patch.source_zl, digest]
            container = store.read(t)
            assert container is not None
            images[name] = container.digest()
        near = colours_near(rings, TILE, {"tile": colours, "zones": zones})
        c.update(parts=parts, images=images, colours=near)
        return c

    def mean(self, folder: Path, t: TextureId, ring: list[float]) -> np.ndarray:
        """The mean colour of ``t``'s level 0 around the centre of ``ring``."""
        from orthostudio.imagery.grid import wgs84_to_tile

        lat, lon = (ring[0] + ring[4]) / 2, (ring[1] + ring[3]) / 2
        x, y = wgs84_to_tile(lat, lon, ZL)
        px, py = int((x - t.til_x) * 256), int((y - t.til_y) * 256)
        rgb = decode_dds((folder / "textures" / f"{texture_name(t)}.dds").read_bytes())[0][:, :, :3]
        return rgb[py - 4 : py + 4, px - 4 : px + 4].reshape(-1, 3).mean(0)


@pytest.fixture
def tile(tmp_path: Path) -> Iterator[Tile]:
    server = TileServer()
    t = Tile(tmp_path, server)
    yield t
    t.store.close()
    server.close()


INSIDE = [_ring(43.40, 5.10, 43.50, 5.30)]
"""Inside the western texture, far from the eastern one."""
ACROSS = [_ring(43.60, 5.50, 43.70, 5.75)]
"""Across the seam of the two textures."""


def test_a_tile_without_clouds_keeps_the_keys_it_always_had(tile: Tile) -> None:
    # 4.5: neither the textures nor the tile's artefact change for a tile with no cloud
    plain = tile.textures()
    again = tile.textures(clouds=[])
    assert plain == again
    listed = {t["name"]: t["key"] for t in tile.manifest(plain)["textures"]}
    assert "clouds" not in tile.manifest(plain)
    assert all(listed.values())


def test_the_way_clouds_are_laid_is_in_the_key_of_the_textures_holding_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 4.5: a new way of laying makes the textures with clouds again, and only them
    plain = TileTexturesParams(tile=TILE.name, encoder="e", encoder_version="1")
    cloudy = TileTexturesParams(tile=TILE.name, encoder="e", encoder_version="1", clouds=[{}])
    before = (plain.canonical(), cloudy.canonical())
    monkeypatch.setattr(build_mod, "CLOUDS_RULE", build_mod.CLOUDS_RULE + 1)
    assert plain.canonical() == before[0] and "clouds_rule" not in before[0]
    assert cloudy.canonical() != before[1]


def test_a_cloud_is_laid_from_its_frozen_parts_and_recorded_with_the_textures(tile: Tile) -> None:
    # R1, R5: laid as its part says, the record in the very artefact of its textures
    plain = tile.textures()
    before = tile.mean(plain, WEST, INSIDE[0])
    c = tile.cloud("k1", INSIDE, RED)
    laid = tile.textures([c])
    assert np.abs(tile.mean(laid, WEST, INSIDE[0]) - RED).max() < 6
    assert np.abs(before - RED).max() > 40  # it was not red before
    clouds = tile.manifest(laid)["clouds"]
    assert [x["id"] for x in clouds["applied"]] == ["k1"] and clouds["undone"] == {}
    kept = {p.name for p in (laid / "clouds" / "parts").iterdir()}
    assert kept == {f"{d}.part" for _zl, d in c["parts"].values()}


def test_a_cloud_across_two_textures_is_laid_in_both(tile: Tile) -> None:
    tile.textures()
    c = tile.cloud("k1", ACROSS, RED)
    assert set(c["parts"]) == {texture_name(WEST), texture_name(EAST)}
    laid = tile.textures([c])
    west = _ring(43.60, 5.50, 43.70, 5.60)
    east = _ring(43.60, 5.65, 43.70, 5.75)
    assert np.abs(tile.mean(laid, WEST, west) - RED).max() < 6
    assert np.abs(tile.mean(laid, EAST, east) - RED).max() < 6


def test_clouds_are_laid_in_their_order(tile: Tile) -> None:
    tile.textures()
    under = tile.cloud("k1", INSIDE, RED)
    over = tile.cloud("k2", [_ring(43.42, 5.15, 43.48, 5.25)], BLUE)
    laid = tile.textures([under, over])
    assert np.abs(tile.mean(laid, WEST, over["rings"][0]) - BLUE).max() < 6
    laid = tile.textures([over, under])
    assert np.abs(tile.mean(laid, WEST, over["rings"][0]) - RED).max() < 6


def test_other_colours_around_a_cloud_undo_it(tile: Tile) -> None:
    # R6: its colours were matched on the photo with the tile's colours of then
    tile.textures()
    c = tile.cloud("k1", INSIDE, RED)
    done = tile.textures([c], photo_brightness=0.2)
    assert tile.manifest(done)["clouds"] == {"applied": [], "undone": {"k1": "colours"}}
    assert np.abs(tile.mean(done, WEST, INSIDE[0]) - RED).max() > 40


def test_a_cloud_whose_part_is_found_nowhere_is_undone(tile: Tile) -> None:
    tile.textures()
    c = tile.cloud("k1", INSIDE, RED)
    for f in got_parts_dir(tile.chunks).iterdir():
        f.unlink()
    done = tile.textures([c])
    assert tile.manifest(done)["clouds"]["undone"] == {"k1": "parts"}


def test_a_cloud_laid_on_other_pieces_than_its_own_is_undone_whole(tile: Tile) -> None:
    # R6 (the owner's rule): another photo under it, it is reset, in every texture it reaches
    tile.textures()
    c = tile.cloud("k1", ACROSS, RED)
    store = ChunkStore(tile.chunks, fsync=False)
    container = store.read(EAST)
    assert container is not None
    (tile.chunks / "T" / str(ZL)).exists()
    # the eastern texture's pieces come again with other images
    tile.server.state.cache[(ZL, EAST.til_x, EAST.til_y)] = _png((10, 200, 10))
    store.path(EAST).unlink()
    done = tile.textures([c])
    assert tile.manifest(done)["clouds"] == {"applied": [], "undone": {"k1": "images"}}
    west = _ring(43.60, 5.50, 43.70, 5.60)
    assert np.abs(tile.mean(done, WEST, west) - RED).max() > 40  # not left in the other texture


def test_a_cloud_laid_in_other_textures_than_its_own_is_undone(tile: Tile) -> None:
    tile.textures()
    c = tile.cloud("k1", INSIDE, RED)
    c["parts"] = {"1488_2096_T13": c["parts"][texture_name(WEST)]}
    c["images"] = {"1488_2096_T13": c["images"][texture_name(WEST)]}
    done = tile.textures([c])
    assert tile.manifest(done)["clouds"]["undone"] == {"k1": "level"}


def test_a_texture_holding_a_cloud_keeps_its_original_beside_it(tile: Tile) -> None:
    # R2: the map shows the photo under a replaced cloud from the texture as built without it
    plain = tile.textures()
    c = tile.cloud("k1", INSIDE, RED)
    laid = tile.textures([c])
    name = f"{texture_name(WEST)}.dds"
    original = artefact_originals(laid) / "textures" / name
    assert original.read_bytes() == (plain / "textures" / name).read_bytes()
    assert (laid / "textures" / name).read_bytes() != original.read_bytes()
    assert not (artefact_originals(laid) / "textures" / f"{texture_name(EAST)}.dds").exists()
