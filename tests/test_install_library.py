"""The tile library and the import of Ortho4XP tiles (spec section 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.install import Library, TileRef, default_library_path, pack_tile, tile_from_name

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


def test_import_ortho4xp_rejects_missing_dir(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite") as lib, pytest.raises(OsxpError) as exc:
        lib.import_ortho4xp(tmp_path / "nope")
    assert exc.value.code == "SYS_WORKING_DIR_INVALID"
