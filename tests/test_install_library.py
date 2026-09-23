"""The tile library and the import of Ortho4XP tiles (spec section 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.install import Library, TileRef, default_library_path, pack_tile, tile_from_name
from orthostudio.install.library import ortho4xp_searched

REPO = Path(__file__).resolve().parents[1]
T43 = TileRef(43, 5)


def test_tile_ref_and_names() -> None:
    assert T43.name == "+43+005" and T43.folder == "+40+000"
    assert TileRef(-34, -59).name == "-34-059" and TileRef(-34, -59).folder == "-40-060"
    assert tile_from_name("+43+005") == T43 and tile_from_name("zOrthoStudio_+43+005") is None
    assert pack_tile("zOrthoStudio_+43+005") == T43 and pack_tile("zOrtho4XP_+43+005") == T43
    assert pack_tile("+43+005") == T43 and pack_tile("zOrthoStudio_Group") is None
    assert tile_from_name("-34-059") == TileRef(-34, -59)


def test_default_path_follows_osxp_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    assert default_library_path() == tmp_path / "home" / "library.sqlite"
    with Library() as lib:
        assert lib.path == tmp_path / "home" / "library.sqlite"
        assert lib.list() == []


def test_register_list_forget(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite") as lib:
        e = lib.register(
            T43, "BI", 16, tmp_path / "zOrthoStudio_+43+005", "osxp", {"dsf": "ab" * 32}
        )
        assert e.tile == T43 and e.zl == 16 and e.built_by == "osxp" and e.kind == "ortho"
        assert e.keys == {"dsf": "ab" * 32} and e.pack_name == "zOrthoStudio_+43+005"
        first = e.registered_at
        # Upsert on (tile, kind, path): one row, registered_at kept, provider updated.
        e2 = lib.register(T43, "Arc", 17, tmp_path / "zOrthoStudio_+43+005", "osxp", None)
        assert len(lib.list()) == 1 and e2.provider == "Arc" and e2.keys is None
        assert e2.registered_at == first and e2.updated_at >= first
        # Another path for the same tile is another row; overlays are their own kind.
        lib.register(T43, "BI", 14, tmp_path / "other" / "zOrtho4XP_+43+005", "ortho4xp", None)
        lib.register(T43, "", 0, tmp_path / "yOrtho4XP_Overlays", "ortho4xp", None, kind="overlay")
        lib.register(TileRef(44, 5), "BI", 16, tmp_path / "zOrthoStudio_+44+005", "osxp", {})
        assert len(lib.list()) == 4 and len(lib.list(tile=T43)) == 3
        assert [x.kind for x in lib.list(kind="overlay")] == ["overlay"]
        assert lib.forget(T43, kind="overlay") == 1
        assert lib.forget(T43, path=tmp_path / "other" / "zOrtho4XP_+43+005") == 1
        assert lib.forget(T43) == 1 and lib.forget(T43) == 0
        assert [x.tile for x in lib.list()] == [TileRef(44, 5)]
    # Reopening sees the rows.
    with Library(tmp_path / "lib.sqlite") as lib:
        assert len(lib.list()) == 1


def _write_cfg(build_dir: Path, tile: TileRef | None, website: str, zl: int) -> None:
    name = f"Ortho4XP_{tile.name}.cfg" if tile else "Ortho4XP.cfg"
    (build_dir / name).write_text(f"default_website={website}\ndefault_zl={zl}\n")


def _dsf(pack: Path, tile: TileRef) -> None:
    d = pack / "Earth nav data" / tile.folder
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tile.name}.dsf").write_bytes(b"XPLNEDSF")


def test_import_ortho4xp_synthetic_tree(tmp_path: Path) -> None:
    ortho4xp_dir = tmp_path / "Ortho4XP"
    tiles = ortho4xp_dir / "Tiles"
    single = tiles / "zOrtho4XP_+43+005"
    _dsf(single, T43)
    _write_cfg(single, T43, "BI", 14)
    group = tiles / "zOrtho4XP_Group"  # grouped build: two DSFs, generic cfg only
    _dsf(group, TileRef(44, 5))
    _dsf(group, TileRef(45, 6))
    _write_cfg(group, None, "Arc", 17)
    (tiles / "not_a_pack").mkdir()
    custom = tmp_path / "elsewhere"
    _dsf(custom / "zOrtho4XP_+46+006", TileRef(46, 6))  # no cfg, no terrain: unknown provider
    (ortho4xp_dir / ".last_gui_params.txt").write_text(f"43 5 BI 16\n{custom}/\n")
    ovl = ortho4xp_dir / "yOrtho4XP_Overlays"
    _dsf(ovl, T43)
    _dsf(ovl, TileRef(44, 5))

    before = {q: q.stat().st_mtime_ns for q in ortho4xp_dir.rglob("*")}
    with Library(tmp_path / "lib.sqlite") as lib:
        entries = lib.import_ortho4xp(ortho4xp_dir)
        assert {
            q: q.stat().st_mtime_ns for q in ortho4xp_dir.rglob("*")
        } == before  # nothing written
        assert len(entries) == 6
        assert all(e.built_by == "ortho4xp" and e.keys is None for e in entries)
        by = {(e.tile, e.kind): e for e in entries}
        assert by[(T43, "ortho")].provider == "BI" and by[(T43, "ortho")].zl == 14
        assert by[(T43, "ortho")].path == single
        assert (
            by[(TileRef(44, 5), "ortho")].provider == "Arc"
            and by[(TileRef(44, 5), "ortho")].zl == 17
        )
        assert by[(TileRef(45, 6), "ortho")].path == group
        assert (
            by[(TileRef(46, 6), "ortho")].provider == "" and by[(TileRef(46, 6), "ortho")].zl == 0
        )
        assert by[(T43, "overlay")].path == ovl and by[(TileRef(44, 5), "overlay")].zl == 0
        # Idempotent.
        assert len(lib.import_ortho4xp(ortho4xp_dir)) == 6 and len(lib.list()) == 6


def test_import_ortho4xp_custom_build_dir_without_slash_is_one_pack(tmp_path: Path) -> None:
    ortho4xp_dir = tmp_path / "Ortho4XP"
    ortho4xp_dir.mkdir()
    pack = tmp_path / "mybuild"
    _dsf(pack, TileRef(47, 7))
    _write_cfg(pack, TileRef(47, 7), "SP", 18)
    (ortho4xp_dir / "Ortho4XP.cfg").write_text(f"verbosity=1\ncustom_build_dir={pack}\n")
    with Library(tmp_path / "lib.sqlite") as lib:
        entries = lib.import_ortho4xp(ortho4xp_dir)
    assert [(e.tile, e.provider, e.zl, e.path) for e in entries] == [
        (TileRef(47, 7), "SP", 18, pack)
    ]


def test_import_a_folder_of_tiles_kept_away_from_ortho4xp(tmp_path: Path) -> None:
    """A user kept his tiles on another disk, ``M:\\XPTilesZL14``, away from Ortho4XP's folder,
    and was refused whatever layout he copied around them (2026-09-22): a folder that is not
    Ortho4XP's own is read as one holding its tiles, or as one of them."""
    tiles = tmp_path / "XPTilesZL14"
    _dsf(tiles / "zOrtho4XP_+46+006", TileRef(46, 6))
    _write_cfg(tiles / "zOrtho4XP_+46+006", TileRef(46, 6), "BI", 14)
    _dsf(tiles / "zOrtho4XP_+47+008", TileRef(47, 8))
    _dsf(tiles / "yOrtho4XP_Overlays", TileRef(46, 6))
    with Library(tmp_path / "lib.sqlite") as lib:
        entries = lib.import_ortho4xp(tiles)
        got = sorted((e.tile.name, e.kind, e.path.name) for e in entries)
        assert got == [
            ("+46+006", "ortho", "zOrtho4XP_+46+006"),
            ("+46+006", "overlay", "yOrtho4XP_Overlays"),
            ("+47+008", "ortho", "zOrtho4XP_+47+008"),
        ]
        geneva = next(e for e in entries if e.tile == TileRef(46, 6) and e.kind == "ortho")
        assert geneva.provider == "BI" and geneva.zl == 14
        # one tile chosen by itself
        (alone,) = lib.import_ortho4xp(tiles / "zOrtho4XP_+47+008")
        assert alone.tile == TileRef(47, 8) and alone.path == tiles / "zOrtho4XP_+47+008"
    # Ortho4XP's own folder still looks in Tiles/ only, not in itself
    own = tmp_path / "Ortho4XP"
    own.mkdir()
    (own / "Ortho4XP.py").write_text("# Ortho4XP\n")
    assert ortho4xp_searched(own) == [own / "Tiles"]
    assert ortho4xp_searched(tiles) == [tiles / "Tiles", tiles]


def test_import_ortho4xp_rejects_missing_dir(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite") as lib, pytest.raises(OsxpError) as exc:
        lib.import_ortho4xp(tmp_path / "nope")
    assert exc.value.code == "SYS_WORKING_DIR_INVALID"


def test_an_ortho_pack_is_known_by_what_it_holds_not_by_its_name(tmp_path: Path) -> None:
    """A user's ``/media/Data/X-Plane_Orthos/EUR_EOX_ZL14`` was refused as "not an Ortho4XP
    installation" because the import knew a pack only by the name Ortho4XP gives it. He was right
    about the remedy: an ``Earth nav data`` with DSFs in it and the orthophotos beside it is what
    a pack is (2026-09-23)."""
    from orthostudio.install.library import holds_ortho4xp_tiles, looks_like_an_ortho_pack

    def pack(path: Path, *, ours: bool = False, dsf: bool = True) -> Path:
        (path / "Earth nav data" / "+40+000").mkdir(parents=True)
        if dsf:
            (path / "Earth nav data" / "+40+000" / "+46+006.dsf").write_bytes(b"x")
        (path / "textures").mkdir()
        (path / "textures" / "23440_33760_BI16.dds").write_bytes(b"DDS ")
        (path / "terrain").mkdir()
        if ours:
            (path / "orthostudio.toml").write_text("", encoding="utf-8")
        return path

    his = pack(tmp_path / "X-Plane_Orthos" / "EUR_EOX_ZL14")
    assert looks_like_an_ortho_pack(his)
    assert holds_ortho4xp_tiles(his), "the folder he named"
    assert holds_ortho4xp_tiles(his.parent), "and the one holding it"

    assert not looks_like_an_ortho_pack(pack(tmp_path / "zOrthoStudio_x", ours=True)), "ours"
    assert not looks_like_an_ortho_pack(pack(tmp_path / "empty", dsf=False)), "no tile in it"
    plain = tmp_path / "Documents"
    (plain / "textures").mkdir(parents=True)
    assert not looks_like_an_ortho_pack(plain)


def _scenery_pack(root: Path, name: str, textures: list[str]) -> Path:
    """A folder shaped like an X-Plane scenery pack: a DSF, a terrain folder and textures."""
    pack = root / name
    (pack / "Earth nav data" / "+40+000").mkdir(parents=True)
    (pack / "Earth nav data" / "+40+000" / "+46+006.dsf").write_bytes(b"XPLNEDSF")
    (pack / "terrain").mkdir()
    (pack / "textures").mkdir()
    for texture in textures:
        (pack / "textures" / texture).write_bytes(b"DDS ")
    return pack


def test_a_pack_of_photo_tiles_is_known_by_the_names_of_its_textures(tmp_path: Path) -> None:
    """A DSF and a textures folder say only "scenery pack": a mesh, an airport and a forest
    library all have both. Asking for no more than that, over a real Custom Scenery, took a
    commercial forest pack for 37 632 photo tiles, each a lat/lon its owner never built, with no
    way to undo them but deleting the library by hand (found in review, 2026-09-23).

    An orthophoto is named ``<til_y>_<til_x>_<provider><zl>.dds``, as Ortho4XP writes it and as
    OrthoStudio XP does.
    """
    from orthostudio.install.library import looks_like_an_ortho_pack

    takes = {
        "an Ortho4XP pack": ["23440_33760_BI16.dds"],
        "a folder named the user's own way": ["11720_16880_EOX14.dds"],
        "a source whose code carries digits": ["23440_33760_PDOK2018.dds"],
    }
    for what, textures in takes.items():
        pack = _scenery_pack(tmp_path, what, textures)
        assert looks_like_an_ortho_pack(pack), what

    leaves = {
        "a forest library": ["Global_Forests_NM_fall.dds", "Global_Forests_NM_n.png"],
        "a base mesh": ["mesh_grass.dds", "mesh_rock.dds"],
        "an airport": ["apron_01.dds", "terminal.dds"],
        "a pack whose textures folder is empty": [],
    }
    for what, textures in leaves.items():
        pack = _scenery_pack(tmp_path, what, textures)
        assert not looks_like_an_ortho_pack(pack), what

    # and our own packs are still left to the library that already knows them
    ours = _scenery_pack(tmp_path, "zOrthoStudio_+46+006", ["23440_33760_BI16.dds"])
    (ours / "orthostudio.toml").write_text("format = 'osxp-pack-1'\n", encoding="utf-8")
    assert not looks_like_an_ortho_pack(ours)
