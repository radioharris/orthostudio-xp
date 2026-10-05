# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The cache of the tiles filed outside the atelier (the atelier, step 5): counted apart, freed on
request, and never a tile broken.

A tile filed elsewhere works without the cache; the cache only makes building it again quick. The
atelier's disk filled up with the cache of tiles that live on other disks, so "Free space" offers it
as a choice of its own. Every test here runs the real clean on a real store, real packs and real
hard links: a tile filed on another disk holds copies, one filed on the atelier's disk holds links.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

import orthostudio.clean as clean_module
from orthostudio.clean import clean, disk_bytes
from orthostudio.cli import app
from orthostudio.graph import InputRef, Store, artifact_key
from orthostudio.graph import store as store_module
from orthostudio.imagery.grid import texture_at
from orthostudio.install import Library
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import ArtefactEntry, PackManifest, pack_dir_name

A = TileRef(46, 6)  # built in the atelier
F = TileRef(46, 7)  # filed on another disk: its pack holds copies
S = TileRef(45, 6)  # filed on the atelier's disk: its pack holds hard links to the store


def _put(
    store: Store,
    rule: str,
    n: int,
    *,
    data: bytes = b"",
    files: dict[str, bytes | Path] | None = None,
    inputs: tuple[InputRef, ...] = (),
) -> str:
    key, recipe = artifact_key(rule, 1, {"n": n}, {i.name: i.digest for i in inputs})
    kind = "dir" if files is not None else "file"
    with store.begin(rule, key, kind) as b:
        if files is None:
            b.out.write_bytes(data)
        for name, content in (files or {}).items():
            p = b.out / name
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, Path):
                os.link(content, p)  # hard link, as tile.textures and the pack do
            else:
                p.write_bytes(content)
        b.commit(version=1, recipe=recipe, inputs=list(inputs))
    return key


def _ref(store: Store, name: str, key: str) -> InputRef:
    return InputRef(name, store.digest_of(key), key)


class World:
    """A data folder (store, image pieces, the atelier's ``tiles``), a library, and tiles."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.store = root / "data" / "store"
        self.chunks = root / "data" / "chunks"
        self.tiles = root / "data" / "tiles"
        self.library = root / "home" / "library.sqlite"
        self.tiles.mkdir(parents=True)
        self.chunks.mkdir(parents=True)
        self.keys: dict[TileRef, dict[str, str]] = {}
        self.packs: dict[TileRef, Path] = {}
        with Store(self.store, fsync=False) as s:
            self.osm = _put(s, "orthostudio.osm", 1, data=b"o" * 300)  # shared by A and F

    def build(
        self, tile: TileRef, n: int, *, osm: str | None = None, remember: bool = True
    ) -> dict[str, str]:
        """The artefacts of a build of ``tile``: a mesh, a DSF, a texture and its folder."""
        with Store(self.store, fsync=False) as s:
            inputs = (_ref(s, "osm", osm),) if osm else ()
            mesh = _put(s, "orthostudio.mesh", n, data=b"m" * (3000 + n), inputs=inputs)
            dsf = _put(s, "tile.dsf", n, data=b"d" * (1000 + n), inputs=(_ref(s, "mesh", mesh),))
            dds = _put(s, "texture.dds", n, data=b"K" * (5000 + n))
            listing = json.dumps({"textures": [{"key": dds}]}).encode()
            textures = _put(
                s, "tile.textures", n,
                files={"manifest.json": listing, "textures/a.dds": s.path(dds)},
                inputs=(_ref(s, "dsf", dsf),),
            )  # fmt: skip
        keys = {"mesh": mesh, "dsf": dsf, "dds": dds, "textures": textures}
        if remember:
            self.keys[tile] = keys
        return keys

    def pack(self, tile: TileRef, folder: Path, *, link: bool, register: bool = True) -> Path:
        """``tile``'s pack in ``folder``: hard links to the store (the atelier's disk) or copies
        (another disk), its ``orthostudio.toml``, and its library row."""
        keys = self.keys[tile]
        pack = folder / pack_dir_name(tile)
        (pack / "textures").mkdir(parents=True)
        (pack / tile.dsf_relpath).parent.mkdir(parents=True)
        with Store(self.store, fsync=False) as s:
            place = os.link if link else shutil.copyfile
            place(s.path(keys["dds"]), pack / "textures" / "a.dds")
            place(s.path(keys["dsf"]), pack / tile.dsf_relpath)
        entries = {
            "dsf": ArtefactEntry(keys["dsf"], "0" * 64, "tile.dsf@1"),
            "textures": ArtefactEntry(keys["textures"], "0" * 64, "tile.textures@1"),
        }
        (pack / "orthostudio.toml").write_text(
            PackManifest(tile.name, "BI", 16, entries, {}).to_toml()
        )
        if register:
            with Library(self.library) as lib:
                lib.register(tile, "BI", 16, pack, "osxp", {"dsf": keys["dsf"],
                             "textures": keys["textures"]})  # fmt: skip
        self.packs[tile] = pack
        return pack

    def piece(self, lat: float, lon: float, size: int, *, folder: str = "BI") -> Path:
        """A texture container of the image pieces, at the texture holding ``(lat, lon)``."""
        t = texture_at(lat, lon, 16, folder)
        path = self.chunks / folder / "16" / f"{t.til_y}_{t.til_x}.chunks"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"j" * size)
        return path

    def run(self, **kw: object) -> clean_module.CleanReport:
        return clean(
            self.store,
            self.chunks,
            library_path=self.library,
            tiles_root=self.tiles,
            **{"grace_s": 0.0, **kw},  # type: ignore[arg-type]
        )

    def stored(self) -> set[str]:
        with Store(self.store, fsync=False) as s:
            return {i.key for i in s.iter_artifacts()}

    def disk(self) -> int:
        """What the data, the packs and the image pieces take on the disk, each file once."""
        return disk_bytes([self.store, self.chunks, *self.packs.values()])

    def whole(self, tile: TileRef) -> bool:
        pack = self.packs[tile]
        k = 5000 + {A: 1, F: 2, S: 3}.get(tile, 4)
        return (pack / "textures" / "a.dds").read_bytes() == b"K" * k and (
            pack / tile.dsf_relpath
        ).stat().st_size > 1000


@pytest.fixture
def world(tmp_path: Path) -> World:
    w = World(tmp_path)
    w.build(A, 1, osm=w.osm)
    w.build(F, 2, osm=w.osm)
    w.build(S, 3)
    w.pack(A, w.tiles, link=True)
    w.pack(F, tmp_path / "Other disk" / "Alps", link=False)
    w.pack(S, tmp_path / "data" / "Filed", link=True)
    return w


def _pieces(w: World) -> dict[str, Path]:
    return {
        "F": w.piece(46.5, 7.5, 4000),  # only F's square
        "S": w.piece(45.5, 6.5, 3000),  # only S's square
        "AF": w.piece(46.5, 6.99999, 2000),  # astride A's square and F's: A uses it too
        "none": w.piece(44.5, 5.5, 1000),  # no tile's
    }


def test_the_cache_of_a_tile_filed_outside_is_counted_apart_and_freed_on_request(
    world: World,
) -> None:
    """The tile filed on another disk loses its whole cache, data and image pieces; the tile itself,
    a copy, is untouched; what the atelier's tile uses too (the OSM data, a texture astride the
    two squares) stays. The figures are what the disk gets back."""
    w = world
    pieces = _pieces(w)
    seen = w.run(dry_run=True)
    assert seen.filed_tiles == 2
    f, s = w.keys[F], w.keys[S]
    data_f = (3000 + 2) + (1000 + 2) + (5000 + 2)
    listing = len(json.dumps({"textures": [{"key": f["dds"]}]}).encode())
    data_s = (3000 + 3) + listing  # S keeps the texture and the DSF its pack holds
    assert seen.filed_bytes == data_f + listing + data_s
    assert seen.filed_images_bytes == 4000 + 3000
    assert seen.images_bytes == 2000 + 1000  # the others: each choice counts its own line
    assert seen.freed_bytes == 0 and seen.removed == 0

    plain = w.run()  # "Free space" without the choice: nothing of theirs goes
    assert not plain.filed_removed and w.stored() >= set(f.values()) | set(s.values())
    assert all(p.is_file() for p in pieces.values())

    before = w.disk()
    done = w.run(filed=True)
    assert done.filed_removed
    assert done.filed_bytes + done.filed_images_bytes == before - w.disk()
    assert done.filed_bytes == seen.filed_bytes and done.filed_images_bytes == 7000
    left = w.stored()
    assert not left & set(f.values())  # all of F's cache
    assert set(w.keys[A].values()) | {w.osm} <= left  # the atelier's, and what it shares
    assert not pieces["F"].exists() and not pieces["S"].exists()
    assert pieces["AF"].is_file() and pieces["none"].is_file()
    assert w.whole(A) and w.whole(F) and w.whole(S)
    again = w.run(dry_run=True)
    assert again.filed_tiles == 2 and again.filed_bytes == again.filed_images_bytes == 0


def test_a_tile_filed_on_the_atelier_s_disk_keeps_the_textures_it_holds(world: World) -> None:
    """Its texture and DSF are one file with the store's, hard-linked: deleting the store's names
    of them would give nothing back and lose what building it again reuses. They stay; what only
    the store holds (its mesh, the textures folder's listing) goes."""
    w = world
    s = w.keys[S]
    w.run(filed=True)
    left = w.stored()
    assert {s["dds"], s["dsf"]} <= left
    assert s["mesh"] not in left and s["textures"] not in left
    assert w.whole(S)
    with Store(w.store, fsync=False) as st:  # the texture is still the pack's own file
        assert os.path.samefile(st.path(s["dds"]), w.packs[S] / "textures" / "a.dds")


def test_a_tile_on_a_disk_away_or_gone_has_its_cache_freed_from_the_library_s_keys(
    world: World,
) -> None:
    """The library recorded what each tile was built from: a tile filed on a disk that is not
    plugged in, or whose folder went, is still a tile filed outside, and gives its cache up from
    those keys, nothing being read where it was."""
    w = world
    shutil.rmtree(w.packs[F].parent.parent)  # its disk: away
    del w.packs[F]
    seen = w.run(dry_run=True)
    assert seen.filed_tiles == 2 and seen.filed_bytes > 0 and seen.freed_bytes == 0
    w.run()  # not data no tile needs: without the choice, it stays
    assert set(w.keys[F].values()) <= w.stored()
    w.run(filed=True)
    assert not w.stored() & set(w.keys[F].values())
    assert set(w.keys[A].values()) | {w.osm} <= w.stored()


def test_a_tile_of_the_atelier_keeps_its_cache_even_while_it_is_not_found(world: World) -> None:
    """A tile listed in the atelier whose folder is not there (moved by hand, to be found again)
    is still the atelier's: its cache stays, as "Free space" always kept it."""
    w = world
    shutil.rmtree(w.packs[A])
    del w.packs[A]
    w.run(filed=True, images=True)
    assert set(w.keys[A].values()) | {w.osm} <= w.stored()


def test_the_atelier_reached_through_a_link_is_the_atelier(world: World, tmp_path: Path) -> None:
    """A data folder reached through a link (a folder of the user's pointing at an external disk):
    a tile in its ``tiles`` is the atelier's, however the folder is named."""
    w = world
    link = tmp_path / "link-to-tiles"
    link.symlink_to(w.tiles, target_is_directory=True)
    report = clean(w.store, w.chunks, library_path=w.library, tiles_root=link, grace_s=0.0,
                   filed=True)  # fmt: skip
    assert report.filed_tiles == 2
    assert set(w.keys[A].values()) | {w.osm} <= w.stored()


def test_what_is_pinned_or_was_just_used_stays(world: World) -> None:
    w = world
    with Store(w.store, fsync=False) as s:
        s.pin("mine", w.keys[F]["mesh"])
    w.run(filed=True, grace_s=3600.0)  # a build of another process may be about to use them
    assert set(w.keys[F].values()) <= w.stored()
    w.run(filed=True)
    assert w.stored() & set(w.keys[F].values()) == {w.keys[F]["mesh"]}


def test_each_choice_frees_its_own_line(world: World) -> None:
    """The images choice empties the image pieces but those of the tiles filed outside, which are
    their own choice's; with both, every piece goes."""
    w = world
    pieces = _pieces(w)
    parent = w.chunks / "BI" / "_parents" / "14" / "1_2.chunks"
    parent.parent.mkdir(parents=True)
    parent.write_bytes(b"p" * 500)
    images = w.run(images=True)
    assert images.images_removed and images.images_bytes == 2000 + 1000 + 500
    assert pieces["F"].is_file() and pieces["S"].is_file()
    assert not pieces["AF"].exists() and not pieces["none"].exists() and not parent.exists()
    assert set(w.keys[F].values()) <= w.stored()  # the data of theirs too
    _pieces(w)
    both = w.run(images=True, filed=True)
    assert both.images_bytes == 2000 + 1000 and both.filed_images_bytes == 4000 + 3000
    assert list(w.chunks.iterdir()) == []


def test_two_builds_of_one_square_keep_its_image_pieces(world: World, tmp_path: Path) -> None:
    """Another build of the atelier's square, filed elsewhere: its own data goes, the image pieces
    of the square stay, since the atelier's build uses them."""
    w = world
    other = TileRef(46, 6)
    keys = w.build(other, 9, remember=False)
    second = tmp_path / "Other disk" / "Old" / pack_dir_name(other)
    (second / "textures").mkdir(parents=True)
    (second / other.dsf_relpath).parent.mkdir(parents=True)
    with Store(w.store, fsync=False) as s:
        shutil.copyfile(s.path(keys["dds"]), second / "textures" / "a.dds")
        shutil.copyfile(s.path(keys["dsf"]), second / other.dsf_relpath)
    entries = {
        "dsf": ArtefactEntry(keys["dsf"], "0" * 64, "tile.dsf@1"),
        "textures": ArtefactEntry(keys["textures"], "0" * 64, "tile.textures@1"),
    }
    (second / "orthostudio.toml").write_text(
        PackManifest(other.name, "BI", 16, entries, {}).to_toml()
    )
    with Library(w.library) as lib:
        lib.register(other, "BI", 16, second, "osxp", {"dsf": keys["dsf"]})
    inside = w.piece(46.5, 6.5, 1500)  # in the square both builds cover
    assert w.run(filed=True).filed_tiles == 3
    assert inside.is_file()
    assert not w.stored() & set(keys.values())
    assert set(w.keys[A].values()) <= w.stored()


def test_an_ortho4xp_tile_is_neither_counted_nor_touched(world: World, tmp_path: Path) -> None:
    w = world
    folder = tmp_path / "Ortho4XP" / "Tiles" / "zOrtho4XP_+44+005"
    (folder / "textures").mkdir(parents=True)
    (folder / "textures" / "x.dds").write_bytes(b"x" * 100)
    with Library(w.library) as lib:
        lib.register(TileRef(44, 5), "BI", 16, folder, "ortho4xp", None)
    none = w.piece(44.5, 5.5, 1000)
    report = w.run(filed=True)
    assert report.filed_tiles == 2 and none.is_file()
    assert (folder / "textures" / "x.dds").read_bytes() == b"x" * 100


def test_an_inode_the_unused_data_shares_with_a_filed_tile_is_counted_once(
    world: World,
) -> None:
    """An older build's textures folder, which no tile needs any more, links the filed tile's
    texture: deleting it alone frees only its listing, and with the filed tile's cache the texture
    comes back. The two lines add up to what the disk gets back."""
    w = world
    f = w.keys[F]
    with Store(w.store, fsync=False) as s:
        old = _put(s, "tile.textures", 99,
                   files={"manifest.json": b"{}", "textures/a.dds": s.path(f["dds"])})  # fmt: skip
    seen = w.run(dry_run=True)
    assert seen.freed_bytes == 2  # the old listing alone: the texture is still linked
    before = w.disk()
    done = w.run(filed=True)
    assert old not in w.stored()
    assert done.freed_bytes + done.filed_bytes + done.filed_images_bytes == before - w.disk()
    assert done.freed_bytes + done.filed_bytes == seen.freed_bytes + seen.filed_bytes


def test_a_file_another_program_holds_stays_and_is_not_counted(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows a file another program holds cannot be deleted: it stays, the rest goes, and
    "Free space" says only what went."""
    w = world
    pieces = _pieces(w)
    f = w.keys[F]
    with Store(w.store, fsync=False) as s:
        held_artefact = s.path(f["mesh"]).name
    real_remove, real_unlink = store_module._remove_path, os.unlink

    def remove(p: Path) -> None:
        if p.name.startswith(held_artefact):
            raise PermissionError(13, "held by another program", str(p))
        real_remove(p)

    def unlink(path: object, *args: object, **kw: object) -> None:
        if os.fspath(path) == os.fspath(pieces["F"]):  # type: ignore[arg-type]
            raise PermissionError(13, "held by another program", os.fspath(path))  # type: ignore[arg-type]
        real_unlink(path, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(store_module, "_remove_path", remove)
    monkeypatch.setattr(clean_module.os, "unlink", unlink)
    before = w.disk()
    done = w.run(filed=True)
    assert pieces["F"].is_file() and not pieces["S"].exists()
    assert done.filed_images_bytes == 3000
    assert f["dsf"] not in w.stored() and f["dds"] not in w.stored()  # the others went
    assert done.filed_bytes + done.filed_images_bytes == before - w.disk()
    monkeypatch.undo()
    assert w.run(filed=True).filed_images_bytes == 4000  # once let go, it goes


def test_the_command(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    w = world
    monkeypatch.setenv("OSXP_HOME", str(w.library.parent))
    monkeypatch.setenv("OSXP_DATA_DIR", str(w.store.parent))
    _pieces(w)
    runner = CliRunner()
    shown = runner.invoke(app, ["clean"])
    assert shown.exit_code == 0, shown.output
    assert "cache of the 2 tile(s) filed outside the atelier:" in shown.output
    assert "kept (--filed deletes it" in shown.output
    assert set(w.keys[F].values()) <= w.stored()
    with Store(w.store, fsync=False) as s:
        shard = s.path(w.keys[F]["mesh"]).parent
    building = shard / f"{w.keys[F]['mesh']}.tmp-{os.getppid()}-abcd"  # another live process
    building.mkdir()
    refused = runner.invoke(app, ["clean", "--filed"])
    assert refused.exit_code == 1 and "osxp clean --filed again" in refused.output
    assert set(w.keys[F].values()) <= w.stored()
    building.rmdir()
    done = runner.invoke(app, ["clean", "--all"])
    assert done.exit_code == 0, done.output
    assert "cache of the 2 tile(s) filed outside the atelier: freed" in done.output
    assert not w.stored() & set(w.keys[F].values())
    assert w.whole(F) and w.whole(S)
