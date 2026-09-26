# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Deleting a tile for good: ``delete_receipt`` (``docs/specs/install.md`` section 4.2).

Found on the user's Library screen: a tile could be taken out of X-Plane, not deleted.
``OrthoStudio XP uninstall --delete`` deleted a pack only while it was installed, left its rows in
the library (the Library kept listing a tile whose files were gone) and its space in the store until
``OrthoStudio XP clean``. The review of the delete (``v1`` to ``v7``) added what must never go with
it: the overlays of Ortho4XP, another row's pack, a link to another pack, anything while X-Plane
runs, other tiles' cache. Everything here happens in a temporary OrthoStudio XP home and a fake
``Custom Scenery``.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from orthostudio.clean import DELETE_GRACE_S, clean, disk_bytes, freed_bytes
from orthostudio.cli import app
from orthostudio.errors import OsxpError
from orthostudio.graph import InputRef, Store, artifact_key
from orthostudio.install import Library, install_pack, packs
from orthostudio.install.scenery_packs import (
    IMPORTED_OVERLAY_PACK,
    IMPORTED_PACK_PREFIX,
    SceneryPacks,
    pack_kind,
)
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import (
    OVERLAY_PACK,
    ArtefactEntry,
    PackManifest,
    delete_receipt,
    install_receipt,
    library_pack,
    library_packs,
    pack_dir_name,
    pack_to_delete,
    uninstall_receipt,
    write_pack,
)

T1, T2, T3 = TileRef(43, 5), TileRef(44, 5), TileRef(45, 5)
INI = (
    b"I\n1000 Version\nSCENERY\n\n"
    b"SCENERY_PACK *GLOBAL_AIRPORTS*\n"
    b"SCENERY_PACK Custom Scenery/z_autoortho/\n"
)
RULES = ("tile.dsf", "texture.dds", "tile.textures", "tile.overlay")


@dataclass
class World:
    """A temporary OrthoStudio XP home (store, library, output folder) and X-Plane
    (Custom Scenery)."""

    home: Path
    xplane: Path

    @property
    def store(self) -> Path:
        return self.home / "store"

    @property
    def tiles(self) -> Path:
        return self.home / "tiles"

    @property
    def library(self) -> Path:
        return self.home / "library.sqlite"

    @property
    def cs(self) -> Path:
        return self.xplane / "Custom Scenery"

    def delete(self, pack: Path, tile: TileRef, **kw: Any) -> dict[str, Any]:
        """``delete_receipt`` on this world, without a grace period unless one is given: the
        artefacts were all created a moment ago."""
        args: dict[str, Any] = {"custom_scenery": self.cs, "library_path": self.library,
                                "store_root": self.store, "tiles_root": self.tiles,
                                "grace_s": 0.0}  # fmt: skip
        return delete_receipt(pack, tile=tile, **{**args, **kw})

    def rows(self) -> set[tuple[str, str, str]]:
        with Library(self.library) as lib:
            return {(r.tile.name, r.kind, r.built_by) for r in lib.list()}

    def keys(self) -> set[str]:
        with Store(self.store, fsync=False) as s:
            return {i.key for i in s.iter_artifacts()}

    def names(self) -> list[str]:
        return SceneryPacks.load(self.cs / "scenery_packs.ini").names()

    def list_in_xplane(self, name: str) -> None:
        packs_ini = SceneryPacks.load(self.cs / "scenery_packs.ini")
        packs_ini.ensure(name, kind=pack_kind(name))
        packs_ini.save(self.cs / "scenery_packs.ini")


@pytest.fixture(autouse=True)
def xplane_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packs, "xplane_running", lambda: False)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("OSXP_HOME", str(home))  # nothing may fall back on the real ~/.orthostudio
    xp = tmp_path / "X-Plane 12"
    (xp / "Resources").mkdir(parents=True)
    (xp / "Custom Scenery").mkdir()
    (xp / "Custom Scenery" / "scenery_packs.ini").write_bytes(INI)
    return World(home, xp)


def _put(
    store: Store,
    rule: str,
    name: str,
    *,
    data: bytes = b"",
    files: Any = None,
    inputs: tuple[InputRef, ...] = (),
) -> str:
    key, recipe = artifact_key(rule, 1, {"name": name}, {i.name: i.digest for i in inputs})
    with store.begin(rule, key, "file" if files is None else "dir") as b:
        if files is None:
            b.out.write_bytes(data)
        for file_name, content in (files or {}).items():
            p = b.out / file_name
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, Path):
                os.link(content, p)  # hard link, as tile.textures does
            else:
                p.write_bytes(content)
        b.commit(version=1, recipe=recipe, inputs=list(inputs))
    return key


def _build(
    world: World,
    tile: TileRef,
    *,
    out: Path | None = None,
    overlay: bool = True,
    osm: str | None = None,
) -> tuple[Path, set[str]]:
    """The pack of ``tile`` in ``out`` (the output folder by default), hard-linked from its
    artefacts in the store as ``osxp build`` leaves it, and the keys of those artefacts. ``osm``
    is an artefact the tile's DSF was built from (shared with another tile, say)."""
    with Store(world.store, fsync=False) as s:
        inputs = () if osm is None else (InputRef("osm", s.digest_of(osm), osm),)
        dsf = _put(s, "tile.dsf", tile.name, inputs=inputs,
                   files={f"{tile.name}.dsf": b"XPLNEDSF" + b"d" * 1000,
                          "terrain/a.ter": b"A\n800\nTERRAIN\n"})  # fmt: skip
        dds = _put(s, "texture.dds", tile.name, data=b"DDS " + b"t" * 5000)
        listing = {"manifest.json": json.dumps({"textures": [{"key": dds}]}).encode(),
                   "textures/a.dds": s.path(dds)}  # fmt: skip
        textures = _put(s, "tile.textures", tile.name, files=listing)
        keys = {dsf, dds, textures}
        entries = {"dsf": ArtefactEntry(dsf, "0" * 64, "tile.dsf@1"),
                   "textures": ArtefactEntry(textures, "0" * 64, "tile.textures@1")}  # fmt: skip
        overlay_file = None
        if overlay:
            key = _put(
                s, "tile.overlay", tile.name, data=b"XPLNEDSF overlay of " + tile.name.encode()
            )
            keys.add(key)
            entries["overlay"] = ArtefactEntry(key, "0" * 64, "tile.overlay@1")
            overlay_file = s.path(key)
        dsf_dir, tex_dir = s.path(dsf), s.path(textures)
    files = write_pack(out or world.tiles, tile, dsf_dir=dsf_dir, textures_dir=tex_dir,
                       overlay_file=overlay_file)  # fmt: skip
    listed = {
        "dsf": tile.dsf_relpath.as_posix(),
        "dsf_size": files.dsf_size,
        "textures": 1,
        "terrain": 1,
        "overlay": f"../{OVERLAY_PACK}/{tile.dsf_relpath.as_posix()}" if overlay else "",
        "cfg": "",
    }
    manifest = PackManifest(tile.name, "BI", 16, entries, listed)
    (files.pack_dir / "orthostudio.toml").write_text(manifest.to_toml())
    return files.pack_dir, keys


def _overlay_dsf(world: World, tile: TileRef) -> Path:
    return world.tiles / OVERLAY_PACK / tile.dsf_relpath


def _age(world: World, seconds: float) -> None:
    """Every artefact of the store created and last used ``seconds`` ago."""
    then = time.time() - seconds
    db = sqlite3.connect(world.store / "index.sqlite")
    with db:
        db.execute("UPDATE artifacts SET created_at = ?, last_used_at = ?", (then, then))
    db.close()


def _snapshot(root: Path) -> list[tuple[str, bool, int]]:
    """Every path under ``root`` (links not followed), without the sqlite files that opening a
    library or a store touches."""
    out = []
    for p in sorted(root.rglob("*")):
        if ".sqlite" in p.name:
            continue
        size = p.lstat().st_size if p.is_file() else 0
        out.append((str(p.relative_to(root)), p.is_symlink(), size))
    return out


# -- what goes ---------------------------------------------------------------------------------


def test_an_installed_tile_leaves_xplane_the_disk_the_library_and_the_store(world: World) -> None:
    pack, _keys = _build(world, T1)
    install_receipt(pack, world.cs, tile=T1, library_path=world.library)
    assert world.rows() == {("+43+005", "ortho", "osxp"), ("+43+005", "overlay", "osxp")}
    # everything this tile holds on the disk, each file once: the pack, its overlay DSF, and its
    # artefacts in the store (DDS and DSF are hard links between them)
    everything = [pack, world.tiles / OVERLAY_PACK, *(world.store / rule for rule in RULES)]
    size = disk_bytes(everything)

    receipt = world.delete(pack, T1)

    assert receipt == {
        "format": "osxp-delete-1",
        "name": "zOrthoStudio_+43+005",
        "tile": "+43+005",
        "removed_from_xplane": True,
        "pack_deleted": True,
        "freed_bytes": size,
        "custom_scenery": str(world.cs),
        "warning": None,
    }
    assert size > 6000  # the DDS, the DSF and the overlay: the bytes are the disk's
    assert not os.path.lexists(world.cs / pack.name)
    assert not os.path.lexists(world.cs / OVERLAY_PACK)
    assert world.names() == ["*GLOBAL_AIRPORTS*", "z_autoortho"]
    assert not os.path.lexists(pack) and not _overlay_dsf(world, T1).exists()
    assert world.rows() == set() and world.keys() == set()


def test_the_other_tiles_keep_the_overlay_pack_and_their_cache(world: World) -> None:
    p1, _ = _build(world, T1)
    p2, keys2 = _build(world, T2)
    for pack, tile in ((p1, T1), (p2, T2)):
        install_receipt(pack, world.cs, tile=tile, library_path=world.library)

    receipt = world.delete(p1, T1)

    assert receipt["removed_from_xplane"] and receipt["pack_deleted"] and receipt["freed_bytes"]
    assert world.names() == ["*GLOBAL_AIRPORTS*", OVERLAY_PACK, p2.name, "z_autoortho"]
    assert os.path.islink(world.cs / OVERLAY_PACK) and os.path.islink(world.cs / p2.name)
    assert not _overlay_dsf(world, T1).exists() and _overlay_dsf(world, T2).is_file()
    assert world.rows() == {("+44+005", "ortho", "osxp"), ("+44+005", "overlay", "osxp")}
    assert world.keys() == keys2


def test_a_tile_that_is_not_installed_is_deleted_all_the_same(world: World) -> None:
    pack, _ = _build(world, T1)
    install_receipt(pack, world.cs, tile=T1, library_path=world.library)
    uninstall_receipt(pack.name, world.cs)  # its overlay DSF is parked in the pack
    ini = (world.cs / "scenery_packs.ini").read_bytes()

    receipt = world.delete(pack, T1)

    assert receipt["removed_from_xplane"] is False and receipt["pack_deleted"] is True
    assert receipt["freed_bytes"] > 6000
    assert (world.cs / "scenery_packs.ini").read_bytes() == ini
    assert not os.path.lexists(pack) and world.rows() == set() and world.keys() == set()


def test_a_tile_never_installed_takes_its_overlay_along_without_xplane(world: World) -> None:
    """Built without --install: no library row, its overlay DSF waits in the shared overlay pack,
    which X-Plane reads as soon as another tile is installed."""
    p1, _ = _build(world, T1)
    _p2, keys2 = _build(world, T2)

    receipt = world.delete(p1, T1, custom_scenery=None)

    assert receipt["custom_scenery"] is None and receipt["removed_from_xplane"] is False
    assert receipt["pack_deleted"] and not os.path.lexists(p1)
    assert not _overlay_dsf(world, T1).exists() and _overlay_dsf(world, T2).is_file()
    assert world.keys() == keys2  # the pack of the output folder keeps its cache, row or not


def test_a_pack_folder_deleted_by_hand_still_leaves_xplane_and_the_library(world: World) -> None:
    pack, _ = _build(world, T1)
    install_receipt(pack, world.cs, tile=T1, library_path=world.library)
    shutil.rmtree(pack)  # the link in Custom Scenery now leads to where the pack was

    receipt = world.delete(pack, T1)

    assert receipt["pack_deleted"] is False and receipt["removed_from_xplane"] is True
    assert not os.path.lexists(world.cs / pack.name)
    assert not _overlay_dsf(world, T1).exists()  # OrthoStudio XP's, the library says so
    # without the pack's orthostudio.toml, the overlay pack's link is left as it is (install.md 4.1)
    assert os.path.islink(world.cs / OVERLAY_PACK)
    assert world.names() == ["*GLOBAL_AIRPORTS*", OVERLAY_PACK, "z_autoortho"]
    assert world.rows() == set() and world.keys() == set()  # its keys were in its library row


# -- the store: the deleted tile's cache, nothing else ------------------------------------------


def test_the_store_gives_back_the_cache_of_the_deleted_tile_only(world: World) -> None:
    """Review finding 5: the delete ran ``osxp clean`` on the whole store, took other processes'
    finished artefacts and credited the deleted tile with other tiles' superseded builds."""
    with Store(world.store, fsync=False) as s:
        osm = _put(s, "orthostudio.osm", "the region", data=b"o" * 3000)
    p1, keys1 = _build(world, T1, osm=osm)
    _p2, keys2 = _build(world, T2, osm=osm)  # shares the OSM artefact
    with Store(world.store, fsync=False) as s:
        superseded = _put(s, "tile.dsf", "+43+005 with other settings", data=b"f" * 2000)
        overlay1 = next(k for k in keys1 if s.info(k).rule == "tile.overlay")  # type: ignore[union-attr]
        collected = [s.path(k) for k in keys1 - {overlay1}]
    _age(world, 20 * 60)  # built twenty minutes ago: past the grace period of a delete
    with Store(world.store, fsync=False) as s:
        s.touch(overlay1)  # used a moment ago, by a build in another process perhaps
    size = disk_bytes([p1, *collected])

    receipt = world.delete(p1, T1, grace_s=None)

    assert receipt["freed_bytes"] == size and receipt["warning"] is None
    assert world.keys() == keys2 | {osm, superseded, overlay1}
    report = clean(world.store, world.home / "chunks", library_path=world.library,
                   tiles_root=world.tiles, grace_s=0.0)  # fmt: skip
    assert report.removed == 2  # osxp clean takes the rest: the superseded build, the overlay


def test_a_cache_used_in_the_last_ten_minutes_stays(world: World) -> None:
    pack, keys = _build(world, T1)
    own = freed_bytes(
        [pack]
    )  # its files that nothing else links to: orthostudio.toml, the .ter copy
    _age(world, DELETE_GRACE_S - 60)

    receipt = world.delete(pack, T1, grace_s=None)

    assert receipt["pack_deleted"] and receipt["freed_bytes"] == own and world.keys() == keys


def test_a_store_that_cannot_be_cleaned_is_a_warning_once_the_tile_is_gone(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orthostudio.clean

    def locked(*_args: object, **_kw: object) -> None:
        raise OSError("database is locked")

    pack, keys = _build(world, T1)
    install_receipt(pack, world.cs, tile=T1, library_path=world.library)
    own = freed_bytes([pack])
    monkeypatch.setattr(orthostudio.clean, "clean_after_delete", locked)

    receipt = world.delete(pack, T1)

    assert receipt["warning"].startswith("The tile is deleted, but")
    assert "database is locked" in receipt["warning"] and "osxp clean" in receipt["warning"]
    assert receipt["freed_bytes"] == own and receipt["pack_deleted"]
    assert not os.path.lexists(pack) and world.rows() == set() and world.keys() == keys


# -- what stays --------------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["ortho4xp", "no orthostudio.toml", "a link", "another tile"])
def test_what_osxp_did_not_build_is_refused_and_nothing_changes(
    world: World, tmp_path: Path, case: str
) -> None:
    folder = world.tiles / pack_dir_name(T1)
    if case == "ortho4xp":
        folder = tmp_path / "Ortho4XP" / "Tiles" / f"{IMPORTED_PACK_PREFIX}{T1.name}"
    (folder.parent).mkdir(parents=True, exist_ok=True)
    if case == "a link":
        real, _ = _build(world, T1)  # a real OrthoStudio XP pack elsewhere, reached through a link
        folder = tmp_path / "linked" / real.name
        folder.parent.mkdir()
        folder.symlink_to(real, target_is_directory=True)
    elif case == "another tile":
        other, _ = _build(world, T2)
        folder = other.rename(world.tiles / pack_dir_name(T1))
    else:
        dsf = folder / "Earth nav data" / "+40+000" / "+43+005.dsf"
        dsf.parent.mkdir(parents=True)
        dsf.write_bytes(b"XPLNEDSF of the user")
    with Library(world.library) as lib:
        lib.register(T1, "BI", 16, folder, "ortho4xp" if case == "ortho4xp" else "osxp", None)
    install_pack(folder, world.cs)  # installed in X-Plane: it must stay installed too
    before = _snapshot(tmp_path)
    rows = world.rows()

    with pytest.raises(OsxpError) as refused:
        world.delete(folder, T1)

    err = refused.value
    assert err.code == "SYS_PACK_NOT_OSXP" and err.context["path"] == str(folder)
    assert "Nothing was deleted" in err.remedy and "Uninstall" in err.remedy
    if case == "ortho4xp":
        assert "Ortho4XP" in err.message and str(folder) in err.remedy
    assert _snapshot(tmp_path) == before and world.rows() == rows


def test_the_overlays_of_ortho4xp_are_never_touched(world: World, tmp_path: Path) -> None:
    """Review finding 1 (v1): deleting an OrthoStudio XP tile parked Ortho4XP's overlay DSF of that
    tile in the OrthoStudio XP pack, then deleted it. The overlays of imported tiles keep their own
    pack and link, ``yOrtho4XP_Overlays``, which a delete never touches."""
    ortho4xp_overlays = tmp_path / "Ortho4XP" / IMPORTED_OVERLAY_PACK
    their_dsf = ortho4xp_overlays / T1.dsf_relpath
    their_dsf.parent.mkdir(parents=True)
    their_dsf.write_bytes(b"XPLNEDSF overlay built by Ortho4XP")
    (world.cs / IMPORTED_OVERLAY_PACK).symlink_to(ortho4xp_overlays, target_is_directory=True)
    world.list_in_xplane(IMPORTED_OVERLAY_PACK)
    pack, _ = _build(world, T1, overlay=False)
    install_receipt(pack, world.cs, tile=T1, library_path=world.library)

    receipt = world.delete(pack, T1)

    assert receipt["removed_from_xplane"] and receipt["pack_deleted"]
    assert their_dsf.read_bytes() == b"XPLNEDSF overlay built by Ortho4XP"
    assert os.path.islink(world.cs / IMPORTED_OVERLAY_PACK)
    assert world.names() == ["*GLOBAL_AIRPORTS*", IMPORTED_OVERLAY_PACK, "z_autoortho"]


def test_another_row_built_straight_into_custom_scenery_is_not_a_copy(world: World) -> None:
    """Review finding 2 (v3): the same build made twice, once into the output folder (B) and
    once straight into Custom Scenery (A), has the same orthostudio.toml; deleting B took A for its
    copy in X-Plane and deleted it."""
    b, _ = _build(world, T1)
    with Library(world.library) as lib:
        lib.register(T1, "BI", 16, b, "osxp", {})
    a, _ = _build(world, T1, out=world.cs)
    install_receipt(a, world.cs, tile=T1, library_path=world.library)
    assert (a / "orthostudio.toml").read_bytes() == (b / "orthostudio.toml").read_bytes()

    receipt = world.delete(b, T1)

    assert receipt["removed_from_xplane"] is False and receipt["pack_deleted"] is True
    assert (a / "orthostudio.toml").is_file() and (a / T1.dsf_relpath).is_file()
    assert world.rows() == {("+43+005", "ortho", "osxp"), ("+43+005", "overlay", "osxp")}
    assert pack_dir_name(T1) in world.names()


def test_a_link_of_that_name_to_another_pack_stays_in_xplane(world: World, tmp_path: Path) -> None:
    """Review finding 3 (v4): with the OrthoStudio XP pack gone, any broken link of its name counted
    as this tile, here the link to another build of the tile, on a disk that is not mounted."""
    gone = world.tiles / pack_dir_name(T1)
    unplugged = tmp_path / "Unplugged" / "Tiles" / pack_dir_name(T1)
    with Library(world.library) as lib:
        lib.register(T1, "BI", 16, gone, "osxp", {})
        lib.register(T1, "BI", 16, unplugged, "osxp", {})
    (world.cs / gone.name).symlink_to(unplugged, target_is_directory=True)
    world.list_in_xplane(gone.name)

    receipt = world.delete(gone, T1)

    assert receipt["removed_from_xplane"] is False and receipt["pack_deleted"] is False
    assert os.path.islink(world.cs / gone.name) and gone.name in world.names()
    assert world.rows() == {("+43+005", "ortho", "osxp")}


def test_a_copy_in_custom_scenery_leaves_with_the_tile_but_not_its_copied_overlay(
    world: World,
) -> None:
    """Review finding 6 (v7): a tile installed with --copy has its overlays in a copied overlay
    pack, a real folder, which OrthoStudio XP never modifies: the deleted tile's DSF stays in it."""
    pack, _ = _build(world, T1)
    install_receipt(pack, world.cs, tile=T1, link=False, library_path=world.library)
    copy = world.cs / pack.name
    assert copy.is_dir() and not copy.is_symlink()
    copy_size = disk_bytes([copy])

    receipt = world.delete(pack, T1)

    assert receipt["removed_from_xplane"] and not copy.exists() and not pack.exists()
    assert receipt["freed_bytes"] > copy_size  # the copy's own files left the disk too
    assert (world.cs / OVERLAY_PACK / T1.dsf_relpath).is_file()  # the documented limitation
    assert world.names() == ["*GLOBAL_AIRPORTS*", OVERLAY_PACK, "z_autoortho"]


def test_a_folder_of_that_name_from_another_build_stays(world: World) -> None:
    pack, _ = _build(world, T1)
    stranger = world.cs / pack.name
    shutil.copytree(pack, stranger)
    with (stranger / "orthostudio.toml").open("a") as f:
        f.write("# another build\n")  # not a copy of this pack

    receipt = world.delete(pack, T1)

    assert receipt["removed_from_xplane"] is False and stranger.is_dir() and not pack.exists()


def test_nothing_changes_while_xplane_runs(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding 4 (v2): "XP_RUNNING refuses first" was not true. With the pack folder gone
    the overlay DSF was deleted before the check; a tile not installed had no check at all, yet
    its overlay DSF sat in the overlay pack X-Plane reads for another tile."""
    installed, _ = _build(world, T1)
    gone, _ = _build(world, T2)
    for pack, tile in ((installed, T1), (gone, T2)):
        install_receipt(pack, world.cs, tile=tile, library_path=world.library)
    shutil.rmtree(gone)
    not_installed, _ = _build(world, T3)
    with Library(world.library) as lib:
        lib.register(T3, "BI", 16, not_installed, "osxp", {})
    monkeypatch.setattr(packs, "xplane_running", lambda: True)
    before, rows, keys = _snapshot(tmp_path), world.rows(), world.keys()

    for pack, tile in ((installed, T1), (gone, T2), (not_installed, T3)):
        with pytest.raises(OsxpError) as refused:
            world.delete(pack, tile)
        assert refused.value.code == "XP_RUNNING", tile
        assert "nothing was deleted" in refused.value.message

    assert _snapshot(tmp_path) == before and world.rows() == rows and world.keys() == keys
    # It used to read: "without an X-Plane folder there is nothing of X-Plane's to protect", and
    # the delete went through. But not knowing where X-Plane is does not mean it is not reading
    # the tile: it means we cannot see that it is. Those users -- the ones whose X-Plane was not
    # found -- were the only ones whose tiles could be deleted from under a running sim (found in
    # review, 2026-09-23). X-Plane running is what decides now, folder or no folder.
    with pytest.raises(OsxpError) as refused:
        world.delete(not_installed, T3, custom_scenery=None)
    assert refused.value.code == "XP_RUNNING"
    assert _snapshot(tmp_path) == before and world.rows() == rows and world.keys() == keys


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_a_deletion_stopped_halfway_can_be_asked_again(world: World) -> None:
    """A file that cannot be deleted (Windows: X-Plane holds it open) stops the deletion; the pack
    keeps its orthostudio.toml, so it is still OrthoStudio XP's and the delete can be
    asked again."""
    pack, _ = _build(world, T1)
    install_receipt(pack, world.cs, tile=T1, library_path=world.library)
    textures = pack / "textures"
    textures.chmod(0o555)
    try:
        with pytest.raises(OsxpError) as stopped:
            world.delete(pack, T1)
    finally:
        textures.chmod(0o755)
    assert stopped.value.code == "SYS_WRITE_FAILED"
    assert "delete the tile again" in stopped.value.remedy
    assert (pack / "orthostudio.toml").is_file() and len(world.rows()) == 2

    receipt = world.delete(pack, T1)

    assert receipt["pack_deleted"] and not os.path.lexists(pack) and world.rows() == set()


# -- which row ---------------------------------------------------------------------------------


def test_the_row_a_request_means(world: World, tmp_path: Path) -> None:
    osxp_pack, _ = _build(world, T1)
    imported = tmp_path / "Ortho4XP" / "Tiles" / f"{IMPORTED_PACK_PREFIX}{T1.name}"
    imported.mkdir(parents=True)
    with Library(world.library) as lib:
        lib.register(T1, "BI", 16, osxp_pack, "osxp", {})
        time.sleep(0.01)
        lib.register(T1, "BI", 17, imported, "ortho4xp", None)  # newer
    lib_path = world.library
    assert library_packs("+43+005", library_path=lib_path)[1][0].built_by == "ortho4xp"

    # install and uninstall: the newest row, or the one at path
    assert library_pack("+43+005", library_path=lib_path).path == imported
    assert library_pack("+43+005", path=str(osxp_pack), library_path=lib_path).path == osxp_pack
    # delete: the newest osxp build, or the one at path
    assert pack_to_delete("+43+005", library_path=lib_path).path == osxp_pack
    assert pack_to_delete("zOrthoStudio_+43+005", library_path=lib_path).path == osxp_pack
    assert pack_to_delete("zOrtho4XP_+43+005", library_path=lib_path).path == imported
    chosen = pack_to_delete("+43+005", path=str(imported), library_path=lib_path)
    assert chosen.path == imported and chosen.built_by == "ortho4xp"
    for name, path, code in (
        ("+43+005", str(tmp_path / "elsewhere" / imported.name), "SYS_WORKING_DIR_INVALID"),
        ("+44+005", None, "SYS_WORKING_DIR_INVALID"),
        ("Genève", None, "CFG_LATLON_INVALID"),
    ):
        for choose in (library_pack, pack_to_delete):
            with pytest.raises(OsxpError) as exc:
                choose(name, path=path, library_path=lib_path)
            assert exc.value.code == code, (name, choose)


def test_the_command_deletes_a_tile_installed_or_not(world: World) -> None:
    p1, _ = _build(world, T1)
    _build(world, T2)  # built without --install: the library does not know it
    install_receipt(p1, world.cs, tile=T1, library_path=world.library)
    runner = CliRunner()
    xp = ["--xplane", str(world.xplane)]

    done = runner.invoke(app, ["uninstall", "+43+005", "--delete", *xp])

    assert done.exit_code == 0, done.output
    assert "took zOrthoStudio_+43+005 out of X-Plane" in done.output
    assert f"deleted the tile's folder {p1}" in done.output
    assert "+43+005 is no longer in the library" in done.output and "freed " in done.output
    assert "warning" not in done.output
    assert not os.path.lexists(p1) and not os.path.lexists(world.cs / p1.name)
    assert world.rows() == set()

    done = runner.invoke(app, ["uninstall", "zOrthoStudio_+44+005", "--delete", "--json", *xp])

    assert done.exit_code == 0, done.output
    doc = json.loads(done.output)
    assert doc["pack_deleted"] is True and doc["removed_from_xplane"] is False
    assert doc["warning"] is None and not (world.tiles / "zOrthoStudio_+44+005").exists()

    unknown = runner.invoke(app, ["uninstall", "+45+005", "--delete", *xp])

    assert unknown.exit_code == 1 and "OrthoStudio XP knows no tile +45+005" in unknown.output


def test_the_command_refuses_a_tile_imported_from_ortho4xp(world: World, tmp_path: Path) -> None:
    """``osxp uninstall <tile> --delete`` takes the tile's newest OrthoStudio XP row, else its
    newest row: a tile imported from Ortho4XP is refused, named by its tile or by its folder."""
    imported = tmp_path / "Ortho4XP" / "Tiles" / f"{IMPORTED_PACK_PREFIX}{T1.name}"
    dsf = imported / T1.dsf_relpath
    dsf.parent.mkdir(parents=True)
    dsf.write_bytes(b"XPLNEDSF of the user")
    with Library(world.library) as lib:
        lib.register(T1, "BI", 16, imported, "ortho4xp", None)
    runner = CliRunner()

    for name in ("+43+005", imported.name):
        refused = runner.invoke(app, ["uninstall", name, "--delete", "--xplane", str(world.xplane)])

        assert refused.exit_code == 1 and "error SYS_PACK_NOT_OSXP" in refused.output, name
    assert dsf.read_bytes() == b"XPLNEDSF of the user"
    assert world.rows() == {("+43+005", "ortho", "ortho4xp")}
