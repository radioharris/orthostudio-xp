# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Free space killed at every step: the tiles never change, the store always opens with every
artefact left as it was built, nothing let go of is out of reach, and Free space run again ends
where it would have ended.

The process is really killed (``os._exit`` in a process of its own, no ``finally`` run, as a
power cut or a SIGKILL leaves it) before and after each file removed, renamed or linked and each
transaction of the store's index (begun, about to commit, committed), one kill point at a time, all
of them.
The world: a tile in the workshop sharing its files with the store, a tile filed on another disk
holding its own copies (its images held twice), the relief and the mesh of both in two forms, data
no tile needs, downloaded image pieces; Free space without its boxes, and with all of them. The
owner asked for no data loss and no cache loss, "RIEN" (2026-10-23).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from orthostudio.clean import clean
from orthostudio.graph import InputRef, Store, artifact_key
from orthostudio.graph import store as store_module
from orthostudio.graph.digest import digest_file
from orthostudio.install import Library
from orthostudio.mesh.mesh_file import MeshData, write_mesh_npz, write_mesh_text
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import ArtefactEntry, PackManifest, write_pack

A = TileRef(46, 6)  # in the workshop, its files the store's
F = TileRef(46, 7)  # filed on another disk, its own copies
KILLED = 137
PARTS = 6
HERE = Path(__file__).parent


def _put(store: Store, rule: str, n: str, files: dict[str, Any], inputs: list[InputRef]) -> Any:
    key, recipe = artifact_key(rule, 1, {"n": n}, {i.name: i.digest for i in inputs})
    with store.begin(rule, key, "dir") as b:
        for name, data in files.items():
            p = b.out / name
            p.parent.mkdir(parents=True, exist_ok=True)
            if callable(data):
                data(p)
            else:
                p.write_bytes(data)
        info, _ = b.commit(version=1, recipe=recipe, inputs=inputs)
    return info


def _image(store: Store, n: str, fill: int) -> Any:
    key, recipe = artifact_key("texture.dds", 1, {"n": n}, {})
    with store.begin("texture.dds", key, "file") as b:
        b.out.write_bytes(bytes([fill]) * 3000 + n.encode())
        info, _ = b.commit(version=1, recipe=recipe, inputs=[])
    return info


def _mesh(seed: int) -> MeshData:
    rng = np.random.default_rng(seed)
    v = np.column_stack([6 + rng.random(30), 46 + rng.random(30), rng.uniform(300, 900, 30)])
    tris = np.array([[i, i + 1, i + 2] for i in range(28)], np.int32)
    return MeshData(v, rng.uniform(-1, 1, (30, 2)).astype(np.float32), tris, np.zeros(28, np.uint8))


def _ref(info: Any, name: str) -> InputRef:
    return InputRef(name, info.digest, info.key)


def _linker(src: Path) -> Callable[[Path], None]:
    return lambda p: os.link(src, p)


def _tile(s: Store, tile: TileRef, folder: Path, link: bool, seed: int) -> PackManifest:
    """The artefacts of one tile as a build leaves them, and its pack in ``folder``."""
    name = tile.name
    raster = np.arange(49, dtype=np.float32).reshape(7, 7) * seed
    mesh = _mesh(seed)
    dem = _put(
        s,
        "orthostudio.dem",
        name,
        {
            "dem.npy": lambda p: np.save(p, raster),
            f"Data{name}.alt": raster.tobytes(),
            "meta.json": b"{}",
        },
        [],
    )
    vec = _put(
        s, "orthostudio.vectors", name, {f"Data{name}.alt": raster.tobytes()}, [_ref(dem, "dem")]
    )
    msh = _put(
        s,
        "orthostudio.mesh",
        name,
        {
            "mesh.npz": lambda p: write_mesh_npz(p, mesh),
            f"Data{name}.mesh": lambda p: write_mesh_text(p, mesh),
            "stats.json": b"{}",
        },
        [_ref(vec, "vectors")],
    )
    masks = _put(s, "orthostudio.masks", name, {"index.json": b"{}"}, [])
    dsf_files = {f"{name}.dsf": b"XPLNEDSF" + bytes([seed]) * 200, "terrain/a.ter": b"A\n"}
    dsf = _put(s, "tile.dsf", name, dsf_files, [_ref(msh, "mesh"), _ref(masks, "masks")])
    images = [_image(s, f"{name}-{i}", 40 * seed + i) for i in range(3)]
    listing = [
        {"name": f"{i}_{seed}_BI16", "key": im.key, "digest": im.digest, "status": "built"}
        for i, im in enumerate(images)
    ]
    files: dict[str, Any] = {"manifest.json": json.dumps({"textures": listing}).encode()}
    for t, im in zip(listing, images, strict=True):
        files[f"textures/{t['name']}.dds"] = _linker(s.path(im.key))
    tex = _put(s, "tile.textures", name, files, [_ref(dsf, "dsf"), _ref(masks, "masks")])
    pack = write_pack(
        folder,
        tile,
        dsf_dir=s.path(dsf.key),
        textures_dir=s.path(tex.key),
        overlay_file=None,
        link=link,
    ).pack_dir
    roles = {"dsf": dsf, "textures": tex, "mesh": msh, "masks": masks, "vectors": vec}
    artefacts = {r: ArtefactEntry(i.key, i.digest, f"{i.rule}@1") for r, i in roles.items()}
    listed = {"dsf": tile.dsf_relpath.as_posix(), "dsf_size": 208, "textures": 3, "terrain": 1}
    manifest = PackManifest(name, "BI", 16, artefacts, listed)
    (pack / "orthostudio.toml").write_text(manifest.to_toml())
    return manifest


def _world(root: Path) -> dict[str, Path]:
    """The data folder, its store, the two tiles and the Library, as builds leave them."""
    paths = {
        "store": root / "data" / "store",
        "chunks": root / "data" / "chunks",
        "elevation": root / "data" / "elevation",
        "tiles": root / "data" / "tiles",
        "away": root / "other disk" / "Alps",
        "library": root / "home" / "library.sqlite",
    }
    for key in ("chunks", "elevation", "tiles", "away"):
        paths[key].mkdir(parents=True)
    paths["library"].parent.mkdir(parents=True)
    with Store(paths["store"], fsync=False) as s, Library(paths["library"]) as lib:
        _put(s, "orthostudio.dem", "old", {"dem.npy": b"x" * 500}, [])  # no tile needs it
        for tile, folder, link, seed in ((A, "tiles", True, 1), (F, "away", False, 2)):
            manifest = _tile(s, tile, paths[folder], link, seed)
            pack = paths[folder] / f"zOrthoStudio_{tile.name}"
            lib.register(tile, "BI", 16, pack, "osxp", manifest.keys)
    for i in range(3):  # downloaded image pieces and relief
        (paths["chunks"] / "BI" / "16").mkdir(parents=True, exist_ok=True)
        (paths["chunks"] / "BI" / "16" / f"{i}_1.chunks").write_bytes(b"j" * 100)
    (paths["elevation"] / "N46E006.tif").write_bytes(b"e" * 100)
    return paths


def _free(paths: dict[str, Path], boxes: bool) -> Any:
    return clean(
        paths["store"], paths["chunks"], library_path=paths["library"], tiles_root=paths["tiles"],
        grace_s=0.0, images=boxes, relief=boxes, filed=boxes, elevation_root=paths["elevation"],
    )  # fmt: skip


def _tiles(paths: dict[str, Path]) -> dict[str, str]:
    """Every file of both tiles, by path, with its bytes' hash."""
    return {
        os.fspath(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in (paths["tiles"], paths["away"])
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def _state(paths: dict[str, Path]) -> dict[str, Any]:
    """What a Free space left: the artefacts, what was trimmed of them, what was let go of and
    where it is, the downloads left."""
    with Store(paths["store"], fsync=False) as s:
        keys = sorted(i.key for i in s.iter_artifacts() if s.has(i.key))
        return {
            "keys": keys,
            "trimmed": {k: s.trimmed(k) for k in keys if s.trimmed(k)},
            "away": sorted((r.key, os.fspath(r.path)) for r in s.away_rows() if not s.has(r.key)),
            "downloads": sorted(
                os.fspath(p.relative_to(paths["chunks"].parent))
                for d in (paths["chunks"], paths["elevation"])
                for p in d.rglob("*")
                if p.is_file()
            ),
        }


def _healthy(paths: dict[str, Path], tiles: dict[str, str]) -> None:
    """The tiles as they were; the store opens; every artefact left is as built; whatever was let
    go of is held, intact, where the store says."""
    assert _tiles(paths) == tiles
    with Store(paths["store"], fsync=False) as s:
        for info in list(s.iter_artifacts()):
            if s.has(info.key):
                assert s.content_digest(info.key) == info.digest, info.rule
        for row in s.away_rows():
            if s.has(row.key):
                continue
            if row.rule == "tile.textures":  # a textures folder: the tile that holds its images
                assert (row.path / "orthostudio.toml").is_file()
            else:
                assert row.path.is_file() and digest_file(row.path) == row.digest


class _Kill:
    """Counts the steps that change the disk or the index, and kills the process at one."""

    def __init__(self, at: int | None) -> None:
        self.at, self.count = at, 0

    def tick(self) -> None:
        self.count += 1
        if self.count == self.at:
            os._exit(KILLED)

    @contextlib.contextmanager
    def armed(self) -> Iterator[None]:
        mp = pytest.MonkeyPatch()
        try:
            for module, name in (
                (os, "unlink"), (os, "remove"), (os, "rmdir"), (os, "rename"), (os, "replace"),
                (os, "link"), (shutil, "rmtree"),
            ):  # fmt: skip
                real = getattr(module, name)
                mp.setattr(module, name, self._wrap(real))
            real_tx = store_module.Store._tx
            kill = self

            @contextlib.contextmanager
            def tx(store: Store) -> Iterator[Any]:
                kill.tick()  # before the transaction begins
                with real_tx(store) as db:
                    yield db
                    kill.tick()  # written, not committed
                kill.tick()  # committed

            mp.setattr(store_module.Store, "_tx", tx)
            yield
        finally:
            mp.undo()

    def _wrap(self, real: Callable[..., Any]) -> Callable[..., Any]:
        def wrapped(*args: Any, **kw: Any) -> Any:
            self.tick()
            result = real(*args, **kw)
            self.tick()
            return result

        return wrapped


def _child(paths: dict[str, str], boxes: bool, at: int | None) -> None:
    """In the child process: Free space, killed at step ``at``; with no kill, the number of steps
    in a status file."""
    data = {k: Path(v) for k, v in paths.items()}
    kill = _Kill(at)
    with kill.armed():
        _free(data, boxes)
    (data["store"].parent / "steps.txt").write_text(str(kill.count))


def _run(paths: dict[str, Path], boxes: bool, at: int | None) -> int:
    """Free space in a process of its own (a fresh interpreter: forking pytest's, which runs
    threads, may deadlock the child), killed at step ``at``; its exit code."""
    args = json.dumps([{k: os.fspath(v) for k, v in paths.items()}, boxes, at])
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(HERE), os.environ.get("PYTHONPATH", "")]),
    }
    code = f"import json, test_free_space_kill as t; t._child(*json.loads({args!r}))"
    return subprocess.run([sys.executable, "-c", code], env=env, check=False).returncode


@pytest.mark.parametrize("part", range(PARTS), ids=[f"part {i + 1}" for i in range(PARTS)])
@pytest.mark.parametrize("boxes", [False, True], ids=["no boxes", "every box"])
def test_free_space_killed_at_every_step(tmp_path: Path, boxes: bool, part: int) -> None:
    """Each run builds its world anew: copying a folder breaks the hard links between the tiles
    and the store, and the Library would still name the first folder's tiles. The kill points are
    shared among ``PARTS`` tests, which run side by side: every point is one part's."""
    reference = _world(tmp_path / "reference")
    tiles = _tiles(reference)
    assert _run(reference, boxes, None) == 0
    steps = int((reference["store"].parent / "steps.txt").read_text())
    expected = _state(reference)
    assert steps > 40  # the world gives Free space things to do
    assert expected["trimmed"] and (expected["away"] or boxes)
    _healthy(reference, tiles)

    def moved(state: dict[str, Any], to: Path) -> dict[str, Any]:
        here = os.fspath(tmp_path / "reference")
        away = [(k, p.replace(here, os.fspath(to))) for k, p in state["away"]]
        return {**state, "away": away}

    for at in range(1 + part, steps + 1, PARTS):
        run_root = tmp_path / f"kill-{at}"
        run = _world(run_root)
        run_tiles = _tiles(run)
        assert _run(run, boxes, at) == KILLED, at
        _healthy(run, run_tiles)
        assert _run(run, boxes, None) == 0, at  # Free space again, to the end
        _healthy(run, run_tiles)
        assert _state(run) == moved(expected, run_root), at
        shutil.rmtree(run_root)
