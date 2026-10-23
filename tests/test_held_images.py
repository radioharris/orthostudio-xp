# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The images a tile holds as files of its own, which the store kept a second time: let go of by
"Free space" whatever the choices, and taken back from the tile when a build needs them.

A tile laid on the store's disk shares its images with the store, one file under two names. A tile
moved to another disk, or copied apart, holds its own copy, and the store's stayed: a user's tiles
moved to another partition left every image twice, and the store filled the disk they had left
(2026-10-23). The world is the filed tiles' one (``test_clean_filed``): ``A`` in the workshop,
``F`` copied to another disk, ``S`` moved on the workshop's own disk. Temporary folders only.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

import orthostudio.clean as clean_module
import test_api_fakes as fakes
import test_clean_filed as filed
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.graph import Store, artifact_key
from orthostudio.install import Library
from test_api_fakes import FakeBuild, FakeIndex, client_for
from test_clean_filed import A, F, S, World

anyio_backend = fakes.anyio_backend
world = filed.world


def _images(w: World, tile: object) -> dict[str, Path]:
    """The tile's ``texture.dds`` keys, each with the file of its pack that holds it."""
    with Store(w.store, fsync=False) as s:
        keys = [k for k in w.keys[tile] if (i := s.info(k)) is not None and i.rule == "texture.dds"]
        folder = next(
            s.path(k) for k in w.keys[tile] if (i := s.info(k)) and i.rule == "tile.textures"
        )
        names = {os.stat(f).st_ino: f.name for f in (folder / "textures").glob("*.dds")}
        return {k: w.packs[tile] / "textures" / names[os.stat(s.path(k)).st_ino] for k in keys}


def _recipe(key: str) -> tuple[int, str]:
    """The recipe the world's builds made an image with (``test_clean_filed._put``)."""
    for n in range(1, 40):
        made, recipe = artifact_key("texture.dds", 1, {"n": n}, {})
        if made == key:
            return 1, recipe
    raise AssertionError(key)


def _take_back(w: World, key: str, *, also: tuple[Path, ...] = ()) -> Any:
    version, recipe = _recipe(key)
    with Store(w.store, fsync=False) as s:
        return s.take_back(key, version=version, recipe=recipe, inputs=[], also=also)


def test_the_images_a_tile_holds_on_another_disk_go_and_come_back_from_it(world: World) -> None:
    """F's images, copied to its disk, go with their folder: the figure is what the disk gets
    back; the store remembers F's file for each; F stays whole. Built again, an image comes back
    from F's file, the very same bytes. The workshop's tile and the one moved on its disk share
    their images with the store: theirs stay."""
    w = world
    images = _images(w, F)
    with Store(w.store, fsync=False) as s:
        digests = {k: s.digest_of(k) for k in images}
    seen = w.run(dry_run=True)
    assert seen.held_images == 2 and seen.held_bytes > 0
    before = w.disk()
    done = w.run()
    assert done.held_images == 2
    assert done.freed_bytes + done.held_bytes == before - w.disk()
    assert not set(images) & w.stored()
    assert w.keys[A] <= w.stored() and w.keys[S] <= w.stored()
    assert w.whole(F)
    with Store(w.store, fsync=False) as s:
        for key, file in images.items():
            row = s.away(key)
            assert row is not None and row.path == Path(os.path.realpath(file))
            assert row.digest == digests[key] and row.size == file.stat().st_size
    assert w.run(dry_run=True).held_images == 0  # a second time: nothing held twice any more

    key, file = next(iter(images.items()))
    info = _take_back(w, key)
    assert info is not None and info.digest == digests[key]
    assert info.path.read_bytes() == file.read_bytes()
    with Store(w.store, fsync=False) as s:
        assert s.has(key) and _take_back(w, key) == s.info(key)  # the store's now


def test_an_image_changed_in_its_tile_is_not_taken_back(world: World) -> None:
    """Retouched in the tile since, the image is no longer the one the store let go of: nothing
    is taken back, and the build prepares it as any other."""
    w = world
    images = _images(w, F)
    w.run()
    key, file = next(iter(images.items()))
    data = bytearray(file.read_bytes())
    data[-1] ^= 0xFF  # same size, another image
    file.write_bytes(bytes(data))
    assert _take_back(w, key) is None
    with Store(w.store, fsync=False) as s:
        assert not s.has(key) and s.away(key) is not None
        assert not list(s.root.glob(f"texture.dds/{key[:2]}/{key}.tmp-*"))  # nothing left behind


def test_a_tile_whose_images_are_not_all_its_own_keeps_them_in_the_store(world: World) -> None:
    """A tile missing an image, or with one of another size, does not hold them all: its folder
    stays, and its images with it."""
    w = world
    images = _images(w, F)
    first = next(iter(images.values()))
    first.unlink()
    assert w.run(dry_run=True).held_images == 0
    first.write_bytes(b"x")  # there, but not the store's
    w.run()
    assert set(images) <= w.stored()


def test_a_tile_moved_since_is_followed(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moved after its images were let go of (File elsewhere), the tile is still where they
    come back from: a build looks in its folders as the Library knows them, and the next Free
    space points the store to its new place."""
    w = world
    images = _images(w, F)
    w.run()
    moved = w.file(F, tmp_path / "Third disk", monkeypatch, away=True)
    key = next(iter(images))
    with Store(w.store, fsync=False) as s:
        assert not s.away(key).path.exists()  # type: ignore[union-attr]
    assert _take_back(w, key, also=(moved / "textures",)) is not None
    w.run()
    with Store(w.store, fsync=False) as s:
        for other in set(images) - {key}:
            assert s.away(other).path.parent == Path(os.path.realpath(moved / "textures"))  # type: ignore[union-attr]


def test_a_tile_gone_takes_its_images_with_it_but_not_a_disk_unplugged(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a disk unplugged, the tile comes back with it: the store keeps where its images are.
    Deleted, nothing holds them any more: the store forgets them."""
    w = world
    images = _images(w, F)
    w.run()
    pack = w.packs[F]
    shutil.move(pack, pack.with_name("elsewhere"))
    with monkeypatch.context() as m:
        m.setattr(clean_module, "disk_absent", lambda path: True)
        w.run()
    with Store(w.store, fsync=False) as s:
        assert all(s.away(k) is not None for k in images)
    shutil.rmtree(pack.with_name("elsewhere"))
    with Library(w.library) as lib:
        lib.forget(F, kind="ortho", path=pack)
    w.run()
    with Store(w.store, fsync=False) as s:
        assert all(s.away(k) is None for k in images)


@pytest.fixture
def app(world: World, tmp_path: Path) -> Any:
    mgr = JobManager(jobs_dir=tmp_path / "home" / "jobs", build=FakeBuild(), env_factory=None)
    application = create_app(
        env_factory=None,
        jobs=mgr,
        airports=FakeIndex(),
        settings_path=tmp_path / "home" / "config.toml",
    )
    yield application
    mgr.close()


@pytest.mark.anyio
async def test_the_page_is_told_and_free_space_lets_them_go(app: Any, world: World) -> None:
    """Disk space counts them on a line of their own; Free space lets go of them without being
    asked, and says what came back."""
    w = world
    images = _images(w, F)
    async with client_for(app) as c:
        disk = (await c.get("/api/disk")).json()
        assert disk["held_bytes"] > 0
        done = (await c.post("/api/clean", json={})).json()
        assert done["held_freed_bytes"] == disk["held_bytes"]
        assert (await c.get("/api/disk")).json()["held_bytes"] == 0
    assert not set(images) & w.stored()
