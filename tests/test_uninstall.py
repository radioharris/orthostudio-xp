"""``osxp uninstall`` / ``uninstall_receipt``: a tile leaves X-Plane with its overlay.

Found reading the page's uninstall (``/api/library/{name}/uninstall``): it removed the tile's
link and line, and left its DSF in the shared ``yOrthoStudio_Overlays`` pack, so X-Plane kept
drawing the tile's roads and objects over the default scenery, which has them already.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orthostudio.cli import app
from orthostudio.errors import OsxpError
from orthostudio.install import install_pack, packs
from orthostudio.install.scenery_packs import (
    IMPORTED_OVERLAY_PACK,
    IMPORTED_PACK_PREFIX,
    SceneryPacks,
)
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import (
    OVERLAY_PACK,
    PARKED_OVERLAY,
    ArtefactEntry,
    PackManifest,
    install_receipt,
    pack_dir_name,
    uninstall_receipt,
    write_pack,
)

T1, T2 = TileRef(43, 5), TileRef(44, 5)
INI = (
    b"I\n1000 Version\nSCENERY\n\n"
    b"SCENERY_PACK *GLOBAL_AIRPORTS*\n"
    b"SCENERY_PACK Custom Scenery/z_autoortho/\n"
)


@pytest.fixture(autouse=True)
def xplane_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packs, "xplane_running", lambda: False)


def _pack(tmp_path: Path, tile: TileRef) -> Path:
    src = tmp_path / "src" / tile.name
    (src / "dsf" / "terrain").mkdir(parents=True)
    (src / "dsf" / f"{tile.name}.dsf").write_bytes(b"XPLNEDSF" + b"\0" * 100)
    (src / "dsf" / "terrain" / "a.ter").write_text("A\n800\nTERRAIN\n")
    (src / "tex" / "textures").mkdir(parents=True)
    (src / "tex" / "textures" / "a.dds").write_bytes(b"DDS " + b"\1" * 64)
    overlay = src / "overlay.dsf"
    overlay.write_bytes(b"XPLNEDSF overlay of " + tile.name.encode())
    out = tmp_path / "tiles"
    write_pack(
        out, tile, dsf_dir=src / "dsf", textures_dir=src / "tex", overlay_file=overlay
    )  # fmt: skip
    pack = out / pack_dir_name(tile)
    manifest = PackManifest(
        tile.name,
        "BI",
        14,
        {"dsf": ArtefactEntry("a" * 64, "b" * 64, "tile.dsf@1")},
        {"dsf": tile.dsf_relpath.as_posix(), "dsf_size": 108, "textures": 1, "terrain": 1,
         "overlay": f"../{OVERLAY_PACK}/{tile.dsf_relpath.as_posix()}"},
    )  # fmt: skip
    (pack / "orthostudio.toml").write_text(manifest.to_toml())
    return pack


@pytest.fixture
def xplane(tmp_path: Path) -> Path:
    xp = tmp_path / "X-Plane 12"
    (xp / "Custom Scenery").mkdir(parents=True)
    (xp / "Custom Scenery" / "scenery_packs.ini").write_bytes(INI)
    return xp


def _names(cs: Path) -> list[str]:
    return SceneryPacks.load(cs / "scenery_packs.ini").names()


def test_the_overlay_leaves_with_the_last_tile_and_comes_back(
    tmp_path: Path, xplane: Path, link_kind: str
) -> None:
    cs = xplane / "Custom Scenery"
    pack = _pack(tmp_path, T1)
    install_receipt(pack, cs, tile=T1, library_path=tmp_path / "lib.sqlite")
    overlay_dsf = pack.parent / OVERLAY_PACK / T1.dsf_relpath
    installed = (cs / "scenery_packs.ini").read_bytes()
    assert overlay_dsf.is_file() and OVERLAY_PACK in _names(cs)

    receipt = uninstall_receipt(pack.name, cs)
    assert receipt["removed"] and receipt["overlay_pack_removed"]
    assert not os.path.lexists(cs / pack.name) and not os.path.lexists(cs / OVERLAY_PACK)
    assert _names(cs) == ["*GLOBAL_AIRPORTS*", "z_autoortho"]
    assert not overlay_dsf.exists(), "X-Plane would still draw the tile's overlay"
    assert (pack / PARKED_OVERLAY).read_bytes() == b"XPLNEDSF overlay of +43+005"
    assert (cs / "scenery_packs.ini.bak").read_bytes() == INI  # the original, kept
    assert (cs / "scenery_packs.ini.osxp-previous").read_bytes() == installed

    again = install_receipt(pack, cs, tile=T1, library_path=tmp_path / "lib.sqlite")
    assert again["overlay_target"] == str(cs / OVERLAY_PACK)
    assert overlay_dsf.read_bytes() == b"XPLNEDSF overlay of +43+005"
    assert not (pack / PARKED_OVERLAY).exists()
    assert (cs / "scenery_packs.ini").read_bytes() == installed


def test_the_shared_overlay_pack_stays_for_the_other_tiles(
    tmp_path: Path, xplane: Path, link_kind: str
) -> None:
    cs = xplane / "Custom Scenery"
    p1, p2 = _pack(tmp_path, T1), _pack(tmp_path, T2)
    for pack, tile in ((p1, T1), (p2, T2)):
        install_receipt(pack, cs, tile=tile, library_path=tmp_path / "lib.sqlite")
    receipt = uninstall_receipt(p1.name, cs)
    assert receipt["overlay_parked"] and not receipt["overlay_pack_removed"]
    assert packs.is_link(cs / OVERLAY_PACK) and packs.is_link(cs / p2.name)
    assert _names(cs) == ["*GLOBAL_AIRPORTS*", OVERLAY_PACK, p2.name, "z_autoortho"]
    assert not (p1.parent / OVERLAY_PACK / T1.dsf_relpath).exists()
    assert (p2.parent / OVERLAY_PACK / T2.dsf_relpath).is_file()


def test_delete_removes_the_built_pack(tmp_path: Path, xplane: Path) -> None:
    cs = xplane / "Custom Scenery"
    pack = _pack(tmp_path, T1)
    install_receipt(pack, cs, tile=T1, library_path=tmp_path / "lib.sqlite")
    receipt = uninstall_receipt(pack.name, cs, delete_pack=True)
    assert receipt["pack_deleted"] and receipt["overlay_parked"] is None
    assert not pack.exists()


def test_a_tile_whose_pack_is_gone_leaves_without_a_crash(tmp_path: Path, xplane: Path) -> None:
    """Found while writing the delete: the overlay DSF was parked into a pack folder deleted by
    hand, and ``os.replace`` raised. There is nowhere to park it, and without the pack's
    orthostudio.toml nothing says the overlay pack is OrthoStudio XP's: the overlay stays as it is,
    and ``delete_receipt`` deletes the DSF when the library says it is OrthoStudio XP's
    (``test_delete.py``)."""
    cs = xplane / "Custom Scenery"
    pack = _pack(tmp_path, T1)
    install_receipt(pack, cs, tile=T1, library_path=tmp_path / "lib.sqlite")
    shutil.rmtree(pack)
    receipt = uninstall_receipt(pack.name, cs)
    assert receipt["removed"] and receipt["overlay_parked"] is None
    assert not os.path.lexists(cs / pack.name) and pack.name not in _names(cs)
    assert (pack.parent / OVERLAY_PACK / T1.dsf_relpath).is_file()
    assert os.path.islink(cs / OVERLAY_PACK) and not receipt["overlay_pack_removed"]


def _imported_overlays(tmp_path: Path, cs: Path) -> Path:
    """Ortho4XP's overlay folder with the DSF of +43+005, linked in Custom Scenery as its users
    do."""
    dsf = tmp_path / "Ortho4XP" / IMPORTED_OVERLAY_PACK / T1.dsf_relpath
    dsf.parent.mkdir(parents=True)
    dsf.write_bytes(b"XPLNEDSF overlay built by Ortho4XP")
    install_pack(tmp_path / "Ortho4XP" / IMPORTED_OVERLAY_PACK, cs)
    return dsf


def test_an_ortho4xp_tile_leaves_x_plane_without_its_overlays(tmp_path: Path, xplane: Path) -> None:
    """Review finding 1 (v5): "Remove from X-Plane" on an Ortho4XP row parked Ortho4XP's overlay
    DSF into Ortho4XP's tile folder and took the overlay link out, and "Add to X-Plane" never put
    them back. Only the tile's link goes: its overlay stays in Ortho4XP's folder, still in X-Plane.
    """
    cs = xplane / "Custom Scenery"
    dsf = _imported_overlays(tmp_path, cs)
    tile = tmp_path / "Ortho4XP" / "Tiles" / f"{IMPORTED_PACK_PREFIX}{T1.name}"
    (tile / "Earth nav data" / "+40+000").mkdir(parents=True)
    (tile / "Earth nav data" / "+40+000" / "+43+005.dsf").write_bytes(b"XPLNEDSF Ortho4XP ortho")
    install_pack(tile, cs)

    receipt = uninstall_receipt(tile.name, cs)

    assert receipt["removed"] and receipt["overlay_parked"] is None
    assert not receipt["overlay_pack_removed"] and os.path.islink(cs / IMPORTED_OVERLAY_PACK)
    assert dsf.read_bytes() == b"XPLNEDSF overlay built by Ortho4XP"
    assert sorted(p.name for p in tile.iterdir()) == ["Earth nav data"]  # nothing parked in it
    assert _names(cs) == ["*GLOBAL_AIRPORTS*", IMPORTED_OVERLAY_PACK, "z_autoortho"]


def test_an_osxp_tile_leaves_the_overlays_another_folder_holds(
    tmp_path: Path, xplane: Path
) -> None:
    """Review finding 1 (v1): the overlay link leads to another folder than the overlay pack beside
    the OrthoStudio XP pack (the overlay pack of another output folder); the tile's uninstall parks
    nothing from it and keeps the link."""
    cs = xplane / "Custom Scenery"
    dsf = tmp_path / "elsewhere" / OVERLAY_PACK / T1.dsf_relpath
    dsf.parent.mkdir(parents=True)
    dsf.write_bytes(b"XPLNEDSF overlay of another build")
    install_pack(tmp_path / "elsewhere" / OVERLAY_PACK, cs)
    pack = _pack(tmp_path, T1)
    shutil.rmtree(pack.parent / OVERLAY_PACK)  # this build has no overlay of its own
    install_pack(pack, cs)

    receipt = uninstall_receipt(pack.name, cs)

    assert receipt["removed"] and receipt["overlay_parked"] is None
    assert not receipt["overlay_pack_removed"] and os.path.islink(cs / OVERLAY_PACK)
    assert dsf.is_file() and not (pack / PARKED_OVERLAY).exists()


def test_a_tile_that_is_not_installed_changes_nothing(tmp_path: Path, xplane: Path) -> None:
    cs = xplane / "Custom Scenery"
    receipt = uninstall_receipt(pack_dir_name(T1), cs)
    assert receipt["removed"] is False
    assert (cs / "scenery_packs.ini").read_bytes() == INI
    assert not (cs / "scenery_packs.ini.bak").exists()


def test_nothing_moves_while_xplane_runs(
    tmp_path: Path, xplane: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = xplane / "Custom Scenery"
    pack = _pack(tmp_path, T1)
    install_receipt(pack, cs, tile=T1, library_path=tmp_path / "lib.sqlite")
    monkeypatch.setattr(packs, "xplane_running", lambda: True)
    with pytest.raises(OsxpError) as excinfo:
        uninstall_receipt(pack.name, cs)
    assert excinfo.value.code == "XP_RUNNING"
    assert os.path.islink(cs / pack.name)
    assert (pack.parent / OVERLAY_PACK / T1.dsf_relpath).is_file()


def test_the_command(tmp_path: Path, xplane: Path) -> None:
    cs = xplane / "Custom Scenery"
    pack = _pack(tmp_path, T1)
    install_receipt(pack, cs, tile=T1, library_path=tmp_path / "lib.sqlite")
    runner = CliRunner()
    bad = runner.invoke(app, ["uninstall", "Genève", "--xplane", str(xplane)])
    assert bad.exit_code != 0
    done = runner.invoke(app, ["uninstall", "+43+005", "--xplane", str(xplane)])
    assert done.exit_code == 0, done.output
    assert "uninstalled zOrthoStudio_+43+005" in done.output
    assert not os.path.lexists(cs / "zOrthoStudio_+43+005")
    twice = runner.invoke(app, ["uninstall", "zOrthoStudio_+43+005", "--xplane", str(xplane)])
    assert twice.exit_code == 0 and "is not installed" in twice.output


# -- overlays from another pack (simHeaven X-World) ------------------------------------------


def test_a_disabled_overlays_line_stays_disabled(tmp_path: Path, xplane: Path) -> None:
    cs = xplane / "Custom Scenery"
    p1, p2 = _pack(tmp_path, T1), _pack(tmp_path, T2)
    install_receipt(p1, cs, tile=T1, library_path=tmp_path / "lib.sqlite")
    ini = SceneryPacks.load(cs / "scenery_packs.ini")
    ini.disable(OVERLAY_PACK)  # the user, who flies with simHeaven X-World
    ini.save(cs / "scenery_packs.ini")
    install_receipt(p2, cs, tile=T2, library_path=tmp_path / "lib.sqlite")
    entry = SceneryPacks.load(cs / "scenery_packs.ini").find(OVERLAY_PACK)
    assert entry is not None and entry.enabled is False
    assert SceneryPacks.load(cs / "scenery_packs.ini").find(p2.name).enabled is True


def test_a_tile_built_without_overlay_takes_its_old_one_out(tmp_path: Path, xplane: Path) -> None:
    """Built again with overlays "none": its DSF leaves the shared pack (and a parked copy goes),
    the pack leaves X-Plane with the last overlay in it, and the library forgets the row."""
    from orthostudio.install.library import Library

    cs = xplane / "Custom Scenery"
    lib = tmp_path / "lib.sqlite"
    p1, p2 = _pack(tmp_path, T1), _pack(tmp_path, T2)
    for pack, tile in ((p1, T1), (p2, T2)):
        install_receipt(pack, cs, tile=tile, library_path=lib)
    shared = p1.parent / OVERLAY_PACK

    def rebuild_without_overlay(pack: Path, tile: TileRef) -> None:
        src = tmp_path / "src" / tile.name
        write_pack(pack.parent, tile, dsf_dir=src / "dsf", textures_dir=src / "tex",
                   overlay_file=None)  # fmt: skip
        text = (
            (pack / "orthostudio.toml")
            .read_text()
            .replace(f'"../{OVERLAY_PACK}/{tile.dsf_relpath.as_posix()}"', '""')
        )
        (pack / "orthostudio.toml").write_text(text)

    (p1 / PARKED_OVERLAY).write_bytes(b"an old parked overlay")
    rebuild_without_overlay(p1, T1)
    assert not (shared / T1.dsf_relpath).exists() and not (p1 / PARKED_OVERLAY).exists()
    receipt = install_receipt(p1, cs, tile=T1, library_path=lib)
    assert receipt["overlay_target"] is None
    assert os.path.islink(cs / OVERLAY_PACK), "T2 still needs the shared pack"
    with Library(lib) as rows:
        assert [e.tile for e in rows.list(kind="overlay")] == [T2]

    rebuild_without_overlay(p2, T2)
    install_receipt(p2, cs, tile=T2, library_path=lib)
    assert not os.path.lexists(cs / OVERLAY_PACK)
    assert OVERLAY_PACK not in _names(cs)
    assert _names(cs) == ["*GLOBAL_AIRPORTS*", p1.name, p2.name, "z_autoortho"]
    with Library(lib) as rows:
        assert rows.list(kind="overlay") == []
