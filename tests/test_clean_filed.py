# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The cache of the tiles filed outside the atelier (the atelier, step 5): counted apart, freed on
request, all of it, and never a tile broken.

A tile filed elsewhere works without the cache; the cache only makes building it again quick. The
atelier's disk filled up with the cache of tiles that live on other disks, so "Free space" offers it
as a choice of its own. The store holds artefacts laid out as a build writes them (a DSF folder with
its terrain files and texture list, a textures folder linking the textures, its own terrain files
and listing), the packs are assembled by ``assemble_pack`` and filed by ``file_tile``, as the app
does: the first version of these tests made the artefacts simpler than a build does, and missed
that the textures it kept for a tile filed on the atelier's disk were orphaned (a review,
2026-10-06). The whole is checked on the owner's own tiles besides, with the real engine.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

import orthostudio.clean as clean_module
from orthostudio.clean import clean, disk_bytes
from orthostudio.cli import app
from orthostudio.graph import InputRef, ResolvedInput, Store, artifact_key
from orthostudio.graph import store as store_module
from orthostudio.imagery.grid import texture_at
from orthostudio.install import Library
from orthostudio.install import packs as install_packs
from orthostudio.model import OVERLAY_PACK, TileRef, pack_dir_name
from orthostudio.pipeline import filing
from orthostudio.pipeline.filing import file_tile
from orthostudio.pipeline.pack import (
    assemble_pack,
    library_pack,
    pack_is_intact,
    read_manifest,
)

A = TileRef(46, 6)  # built in the atelier
F = TileRef(46, 7)  # filed on another disk: copied there
S = TileRef(45, 6)  # filed on the atelier's disk: moved there, its files hard links to the store


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
                os.link(content, p)  # hard link, as tile.textures does
            else:
                p.write_bytes(content)
        b.commit(version=1, recipe=recipe, inputs=list(inputs))
    return key


def _ref(store: Store, name: str, key: str) -> InputRef:
    return InputRef(name, store.digest_of(key), key)


def _ter(name: str) -> bytes:
    """A terrain file as the DSF stage writes it."""
    return (
        f"A\n800\nTERRAIN\n\nLOAD_CENTER 46.55886 6.32812 107640 4096\n"
        f"BASE_TEX_NOWRAP ../textures/{name}.dds\nNO_ALPHA\n"
    ).encode("ascii")


class World:
    """A data folder (its store, its image pieces, the atelier's ``tiles``), a library, tiles."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.data = root / "data"
        self.store = self.data / "store"
        self.chunks = self.data / "chunks"
        self.tiles = self.data / "tiles"
        self.library = root / "home" / "library.sqlite"
        self.tiles.mkdir(parents=True)
        self.chunks.mkdir(parents=True)
        self.library.parent.mkdir(parents=True, exist_ok=True)
        self.keys: dict[TileRef, set[str]] = {}
        self.packs: dict[TileRef, Path] = {}
        with Store(self.store, fsync=False) as s:
            self.osm = _put(s, "orthostudio.osm", 1, files={
                "snapshot.json": b"{}", "osm/+40+000/+46+006.osm": b"o" * 300})  # fmt: skip

    def build(
        self, tile: TileRef, n: int, *, osm: str | None = None, remember: bool = True
    ) -> dict[str, str]:
        """The artefacts of a build of ``tile``, laid out as the build lays them out."""
        name = f"{1440 + n}_2112_BI16"
        other = f"{1456 + n}_2112_BI16"
        with Store(self.store, fsync=False) as s:
            source = (_ref(s, "osm", osm),) if osm else ()
            mesh = _put(s, "orthostudio.mesh", n, files={
                f"Data{tile.name}.mesh": b"m" * (3000 + n), "mesh.npz": b"z" * 700,
                "stats.json": b"{}"}, inputs=source)  # fmt: skip
            masks = _put(s, "orthostudio.masks", n, files={
                "index.json": b'{"masks": []}', "5760_8496.png": b"p" * 400})  # fmt: skip
            listing = {"format": "osxp-dsf-textures-1", "textures": []}
            dsf = _put(s, "tile.dsf", n, files={
                f"{tile.name}.dsf": b"XPLNEDSF" + b"d" * (1000 + n),
                f"terrain/{name}.ter": _ter(name), f"terrain/{other}.ter": _ter(other),
                "textures.json": json.dumps(listing).encode(), "stats.json": b"{}"},
                inputs=(_ref(s, "mesh", mesh), _ref(s, "masks", masks)))  # fmt: skip
            one = _put(s, "texture.dds", 10 * n + 1, data=b"K" * (5000 + n))
            two = _put(s, "texture.dds", 10 * n + 2, data=b"L" * (6000 + n))
            textures = [{"key": one, "name": f"{name}.dds"}, {"key": two, "name": f"{other}.dds"}]
            doc = json.dumps({"format": "osxp-textures-1", "textures": textures}).encode()
            folder = _put(s, "tile.textures", n, files={
                "manifest.json": doc, f"terrain/{name}.ter": _ter(name),
                f"textures/{name}.dds": s.path(one), f"textures/{other}.dds": s.path(two)},
                inputs=(_ref(s, "dsf", dsf), _ref(s, "masks", masks)))  # fmt: skip
            overlay = _put(s, "tile.overlay", n, data=b"XPLNEDSF overlay" + b"r" * (200 + n))
        keys = {"mesh": mesh, "masks": masks, "dsf": dsf, "one": one, "two": two,
                "textures": folder, "overlay": overlay}  # fmt: skip
        if remember:
            self.keys[tile] = set(keys.values())
        return keys

    def pack(self, tile: TileRef, keys: dict[str, str], folder: Path, *, link: bool = True) -> Path:
        """``tile``'s pack assembled into ``folder`` as the build assembles it, and its row."""
        with Store(self.store, fsync=False) as s:

            def given(name: str, label: str) -> ResolvedInput:
                key = keys[label]
                return ResolvedInput(name, s.digest_of(key), s.path(key), key)

            manifest, files = assemble_pack(
                s, folder, tile, provider="BI", zl=16, dsf=given("dsf", "dsf"),
                textures=given("textures", "textures"), overlay=given("overlay", "overlay"),
                link=link)  # fmt: skip
        with Library(self.library) as lib:
            lib.register(tile, "BI", 16, files.pack_dir, "osxp", manifest.keys)
        self.packs[tile] = files.pack_dir
        return files.pack_dir

    def file(
        self, tile: TileRef, dest: Path, monkeypatch: pytest.MonkeyPatch, *, away: bool
    ) -> Path:
        """``tile`` filed into ``dest`` by ``file_tile``: copied and read back on another disk
        (``away``), moved on this one."""
        dest.mkdir(parents=True, exist_ok=True)
        with monkeypatch.context() as m:
            if away:
                m.setattr(filing, "same_disk", lambda a, b: False)
            entry = library_pack(tile.name, path=str(self.packs[tile]), library_path=self.library)
            receipt = file_tile(entry, dest, custom_sceneries=[], library_path=self.library)
        self.packs[tile] = Path(receipt["to"])
        return self.packs[tile]

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
        """What the data folder and every pack take on the disk, each file once: a folder inside
        the data folder is not given again, or its files no longer linked would count twice."""
        outside = [p for p in self.packs.values() if not p.is_relative_to(self.data)]
        return disk_bytes([self.data, *outside, *(p.parent / OVERLAY_PACK for p in outside)])

    def whole(self, tile: TileRef) -> bool:
        """The tile's pack has every file its manifest names, as the app checks it."""
        pack = self.packs[tile]
        return pack_is_intact(pack, read_manifest(pack), with_overlay=False)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """A as the atelier's tile, F filed on another disk, S filed on the atelier's disk; A and F
    share their OSM data. The data folder is the settings' one, as in the app."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OSXP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(install_packs, "xplane_running", lambda: False)
    w = World(tmp_path)
    for tile, n, osm in ((A, 1, w.osm), (F, 2, w.osm), (S, 3, None)):
        w.pack(tile, w.build(tile, n, osm=osm), w.tiles)
    w.file(F, tmp_path / "Other disk" / "Alps", monkeypatch, away=True)
    w.file(S, tmp_path / "data" / "Filed", monkeypatch, away=False)
    return w


def _pieces(w: World) -> dict[str, Path]:
    return {
        "F": w.piece(46.5, 7.5, 4000),  # only F's square
        "S": w.piece(45.5, 6.5, 3000),  # only S's square
        "AF": w.piece(46.5, 6.99999, 2000),  # astride A's square and F's: A uses it too
        "none": w.piece(44.5, 5.5, 1000),  # no tile's
    }


def _rule_keys(w: World, rule: str) -> set[str]:
    with Store(w.store, fsync=False) as s:
        return {i.key for i in s.iter_artifacts(rule)}


def test_the_whole_cache_of_the_tiles_filed_outside_goes_on_request_and_they_stay_whole(
    world: World,
) -> None:
    """Their store data and their image pieces go, all of them; the figures are what the disk
    gets back; what the atelier's tile uses too (the OSM data, a texture astride two squares)
    stays; the filed tiles are whole after, the atelier's too."""
    w = world
    pieces = _pieces(w)
    assert w.whole(F) and w.whole(S)
    seen = w.run(dry_run=True)
    assert seen.filed_tiles == 2 and seen.filed_bytes > 0
    assert seen.filed_images_bytes == 4000 + 3000
    assert seen.images_bytes == 2000 + 1000  # the others: each choice counts its own line

    plain = w.run()  # without the choice, nothing of theirs goes
    assert not plain.filed_removed and w.stored() >= w.keys[F] | w.keys[S]
    assert all(p.is_file() for p in pieces.values())

    before = w.disk()
    done = w.run(filed=True)
    assert done.filed_removed
    assert done.filed_bytes == seen.filed_bytes and done.filed_images_bytes == 7000
    assert done.filed_bytes + done.filed_images_bytes == before - w.disk()
    left = w.stored()
    assert not left & (w.keys[F] | w.keys[S])  # all of their cache, whatever their disk
    assert w.keys[A] | {w.osm} <= left  # the atelier's, and what it shares
    assert not pieces["F"].exists() and not pieces["S"].exists()
    assert pieces["AF"].is_file() and pieces["none"].is_file()
    assert w.whole(A) and w.whole(F) and w.whole(S)


def test_a_tile_filed_on_the_atelier_s_disk_keeps_its_files_and_a_second_clean_finds_nothing(
    world: World,
) -> None:
    """Its textures and DSF are one file with the store's, hard-linked: they stay, the tile's own,
    and their room is not counted. Nothing of its cache is left behind half reachable: kept, its
    textures were collected by the next plain clean as data no tile needs (a review, 2026-10-06)."""
    w = world
    pack = w.packs[S]
    dds = sorted((pack / "textures").glob("*.dds"))
    assert dds and all(f.stat().st_nlink > 1 for f in dds)  # moved: the store's files too
    before = w.disk()
    done = w.run(filed=True)
    assert done.filed_bytes == before - w.disk()  # the shared files' room is not counted
    assert not w.stored() & w.keys[S]
    assert w.whole(S) and all(f.stat().st_nlink == 1 for f in dds)  # the tile's own files now
    again = w.run()
    assert again.removed == 0 and again.freed_bytes == 0
    assert w.run(dry_run=True).filed_tiles == 0  # nothing of theirs here any more


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
    assert w.keys[F] <= w.stored()
    w.run(filed=True)
    assert not w.stored() & w.keys[F]
    assert w.keys[A] | {w.osm} <= w.stored()


def test_a_tile_of_the_atelier_keeps_its_cache_even_while_it_is_not_found(world: World) -> None:
    """A tile listed in the atelier whose folder is not there (moved by hand, to be found again)
    is still the atelier's: its cache stays, as "Free space" always kept it."""
    w = world
    shutil.rmtree(w.packs[A])
    del w.packs[A]
    w.run(filed=True, images=True)
    assert w.keys[A] | {w.osm} <= w.stored()


def test_a_tile_of_the_atelier_listed_under_another_spelling_is_the_atelier_s(
    world: World, tmp_path: Path
) -> None:
    """The Mac's disk ignores the letter case: the data folder typed once ``Data``, chosen later
    ``data``, lists a tile of the atelier under a spelling of its own. It was taken for one filed
    outside, and a not found one lost its cache with theirs (a review, 2026-10-06)."""
    other = tmp_path / "DATA" / "TILES"
    if not other.is_dir():
        pytest.skip("this disk tells letter cases apart: another spelling is another folder")
    w = world
    keys = read_manifest(w.packs[A]).keys
    with Library(w.library) as lib:
        lib.forget(A, path=w.packs[A])
        lib.register(A, "BI", 16, other / pack_dir_name(A), "osxp", keys)
    shutil.rmtree(w.packs[A])  # not found, to be found again
    del w.packs[A]
    assert w.run(dry_run=True).filed_tiles == 2  # F and S only
    w.run(filed=True)
    assert w.keys[A] | {w.osm} <= w.stored()


def test_the_atelier_reached_through_a_link_is_the_atelier(world: World, tmp_path: Path) -> None:
    """A data folder reached through a link (a folder of the user's pointing at an external disk):
    a tile in its ``tiles`` is the atelier's, however the folder is named."""
    w = world
    link = tmp_path / "link-to-tiles"
    link.symlink_to(w.tiles, target_is_directory=True)
    report = clean(w.store, w.chunks, library_path=w.library, tiles_root=link, grace_s=0.0,
                   filed=True)  # fmt: skip
    assert report.filed_tiles == 2
    assert w.keys[A] | {w.osm} <= w.stored()


def test_what_is_pinned_or_was_just_used_stays(world: World) -> None:
    w = world
    (mesh,) = w.keys[F] & _rule_keys(w, "orthostudio.mesh")
    with Store(w.store, fsync=False) as s:
        s.pin("mine", mesh)
    w.run(filed=True, grace_s=3600.0)  # a build of another process may be about to use them
    assert w.keys[F] <= w.stored()
    w.run(filed=True)
    assert w.stored() & w.keys[F] == {mesh}


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
    assert w.keys[F] <= w.stored()  # the data of theirs too
    _pieces(w)
    both = w.run(images=True, filed=True)
    assert both.images_bytes == 2000 + 1000 and both.filed_images_bytes == 4000 + 3000
    assert list(w.chunks.iterdir()) == []


def test_two_builds_of_one_square_keep_its_image_pieces(world: World, tmp_path: Path) -> None:
    """Another build of the atelier's square, filed elsewhere: its own data goes, the image pieces
    of the square stay, since the atelier's build uses them."""
    w = world
    keys = w.build(A, 9, remember=False)
    elsewhere = tmp_path / "Other disk" / "Old"
    second = w.pack(A, keys, elsewhere, link=False)
    w.packs[A] = w.tiles / pack_dir_name(A)
    inside = w.piece(46.5, 6.5, 1500)  # in the square both builds cover
    assert w.run(filed=True).filed_tiles == 3
    assert inside.is_file()
    assert not w.stored() & set(keys.values())
    assert w.keys[A] <= w.stored()
    assert pack_is_intact(second, read_manifest(second), with_overlay=False)


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


def test_only_the_tiles_with_some_cache_here_are_counted(world: World, tmp_path: Path) -> None:
    """A tile of a data folder chosen before has its cache there, and one freed has none: "Cache
    of 8 tiles" for one tile's 9 kB said otherwise (a review, 2026-10-06)."""
    w = world
    with Library(w.library) as lib:  # a tile of another data folder: its keys are not here
        lib.register(TileRef(44, 6), "BI", 16, tmp_path / "Old" / "tiles" / "zOrthoStudio_+44+006",
                     "osxp", {"dsf": "f" * 64})  # fmt: skip
    assert w.run(dry_run=True).filed_tiles == 2
    w.run(filed=True)
    assert w.run(dry_run=True).filed_tiles == 0
    w.piece(46.5, 7.5, 4000)  # image pieces of F's square only: F has some cache here again
    assert w.run(dry_run=True).filed_tiles == 1


def test_an_inode_the_unused_data_shares_with_a_filed_tile_is_counted_once(
    world: World,
) -> None:
    """An older build's textures folder, which no tile needs any more, links the filed tile's
    texture: deleting it alone frees only its listing, and with the filed tile's cache the texture
    comes back. The two lines add up to what the disk gets back."""
    w = world
    one = sorted(w.keys[F] & _rule_keys(w, "texture.dds"))[0]
    with Store(w.store, fsync=False) as s:
        old = _put(s, "tile.textures", 99,
                   files={"manifest.json": b"{}", "textures/a.dds": s.path(one)})  # fmt: skip
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
    (mesh,) = w.keys[F] & _rule_keys(w, "orthostudio.mesh")
    real_remove, real_unlink = store_module._remove_path, os.unlink

    def remove(p: Path) -> None:
        if p.name.startswith(mesh):
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
    assert not (w.keys[F] - {mesh}) & w.stored()  # the others went
    assert done.filed_bytes + done.filed_images_bytes == before - w.disk()
    monkeypatch.undo()
    assert w.run(filed=True).filed_images_bytes == 4000  # once let go, it goes


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_an_artefact_that_cannot_be_moved_aside_is_left_whole(world: World) -> None:
    """Removed where it lay when the move aside was refused, what could not be removed stayed
    under the name of a finished artefact, its row gone, and the next build of that key adopted
    the remains: a tile built without its roads, said built (a review, 2026-10-06). It is left
    whole now, row and files, and the others go."""
    w = world
    (mesh,) = w.keys[F] & _rule_keys(w, "orthostudio.mesh")
    with Store(w.store, fsync=False) as s:
        path = s.path(mesh)
    names = sorted(p.name for p in path.iterdir())
    path.parent.chmod(0o555)  # its shard: nothing in it can be renamed
    try:
        done = w.run(filed=True)
    finally:
        path.parent.chmod(0o755)
    assert mesh in w.stored() and sorted(p.name for p in path.iterdir()) == names
    assert not (w.keys[F] - {mesh}) & w.stored()
    assert done.filed_removed
    w.run(filed=True)  # once it can move, it goes
    assert mesh not in w.stored()


class _Junction:
    """A listed folder as Windows lists a junction (a link to a folder elsewhere): a folder that
    is not a symbolic link, whose listing is the folder it leads to."""

    def __init__(self, entry: os.DirEntry[str]) -> None:
        self._entry, self.name, self.path = entry, entry.name, entry.path

    def is_junction(self) -> bool:
        return True

    def is_dir(self, follow_symlinks: bool = True) -> bool:
        return True

    def is_file(self, follow_symlinks: bool = True) -> bool:
        return False

    def is_symlink(self) -> bool:
        return False

    def stat(self, follow_symlinks: bool = True) -> os.stat_result:
        return self._entry.stat(follow_symlinks=True)


def test_a_junction_is_never_entered(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A junction in the relief or image folders leads to a folder of the user's: emptying entered
    it and emptied what it led to (``shutil.rmtree``, used before, refused it). It stays, and what
    it leads to too; the measure does not count it."""
    w = world
    mine = tmp_path / "My DEM"
    mine.mkdir()
    (mine / "N46E006.tif").write_bytes(b"t" * 900)
    elevation = w.data / "elevation"
    (elevation / "+40+000").mkdir(parents=True)
    (elevation / "+40+000" / "N45E006_COP30.tif").write_bytes(b"c" * 300)
    (elevation / "lidar").symlink_to(mine, target_is_directory=True)
    real_scandir = os.scandir

    @contextlib.contextmanager
    def windows_scandir(path: str):  # type: ignore[no-untyped-def]
        with real_scandir(path) as it:
            yield [_Junction(e) if e.name == "lidar" else e for e in it]

    monkeypatch.setattr(clean_module.os, "scandir", windows_scandir)
    seen = w.run(dry_run=True, elevation_root=elevation)
    assert seen.relief_bytes == 300
    done = w.run(relief=True, elevation_root=elevation)
    assert done.relief_removed and done.relief_bytes == 300
    assert (mine / "N46E006.tif").read_bytes() == b"t" * 900
    assert os.path.lexists(elevation / "lidar")


def test_the_command(world: World) -> None:
    w = world
    _pieces(w)
    runner = CliRunner()
    shown = runner.invoke(app, ["clean"])
    assert shown.exit_code == 0, shown.output
    assert "cache of the 2 tile(s) filed outside the workshop:" in shown.output
    assert "kept (--filed deletes it" in shown.output
    assert w.keys[F] <= w.stored()
    (mesh,) = w.keys[F] & _rule_keys(w, "orthostudio.mesh")
    with Store(w.store, fsync=False) as s:
        shard = s.path(mesh).parent
    building = shard / f"{mesh}.tmp-{os.getppid()}-abcd"  # another live process
    building.mkdir()
    refused = runner.invoke(app, ["clean", "--filed"])
    assert refused.exit_code == 1 and "osxp clean --filed again" in refused.output
    assert w.keys[F] <= w.stored()
    building.rmdir()
    done = runner.invoke(app, ["clean", "--all"])
    assert done.exit_code == 0, done.output
    assert "cache of the 2 tile(s) filed outside the workshop: freed" in done.output
    assert not w.stored() & (w.keys[F] | w.keys[S])
    assert w.whole(F) and w.whole(S)
