# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Another tool's mesh, from a folder chosen in Live's Settings: a region copied by another
streamer, AutoOrtho's moved elsewhere. Taken by what they hold, whatever their names: after Live's
own mesh and AutoOrtho's in X-Plane, overlays last too."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_live_overlays import put, xplane

from live_helpers import make_source, make_xplane
from orthostudio.errors import OsxpError
from orthostudio.live import control, overlays, region, scenery, session
from orthostudio.live.scenery import NO_OTHER_MESH, OtherMesh, default_sources, other_mesh
from orthostudio.live.settings import LiveSettings, load_settings, save_settings

A = (45, 5)
B = (46, 7)
PHOTO_TER = b"A\n800\nTERRAIN\n\nBASE_TEX_NOWRAP ../textures/24528_38224_BI16.dds\n"


def mesh(pack: Path, ter: bytes = PHOTO_TER, photos: bool = False, square=A) -> Path:
    """A pack of AutoOrtho's format: a DSF, a terrain file naming a photo, a water mask."""
    put(pack, square)
    (pack / "terrain").mkdir(parents=True, exist_ok=True)
    (pack / "terrain" / "24528_38224_BI16.ter").write_bytes(ter)
    (pack / "textures").mkdir(exist_ok=True)
    (pack / "textures" / "24528_38224_ZL16.png").write_bytes(b"mask")
    if photos:
        (pack / "textures" / "24528_38224_BI16.dds").write_bytes(b"DDS ")
    return pack


@pytest.fixture(autouse=True)
def looked_at_anew(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test's folder looked at, not one kept from another test."""
    monkeypatch.setattr(scenery, "_found", (0.0, (), NO_OTHER_MESH))


def test_the_folder_taken_by_what_it_holds(tmp_path: Path) -> None:
    """Another streamer's folder: its region, its overlays; a tile with its photos, a mesh of
    X-Plane's land classes and a region of Live's kept there left aside, said why; a folder with
    no pack gives nothing."""
    root = tmp_path / "live"
    folder = tmp_path / "Base Packages"
    eur = mesh(folder / "Mesh_Eur")
    put(folder / "Mesh_Overlays", A)
    mesh(folder / "zOrtho4XP_+45+005", photos=True)
    mesh(folder / "HD_Mesh", ter=b"A\n800\nTERRAIN\n\nBASE_TEX ../textures/grass_dry.dds\n")
    put(folder / "z_osxp_eur", A)  # one of Live's regions, kept on another disk
    (folder / "z_osxp_eur" / "terrain-lists").mkdir()
    (folder / "notes").mkdir()
    found = other_mesh(folder, None, root)
    assert found.meshes == [eur]
    assert found.overlays == [folder / "Mesh_Overlays"]
    assert found.refused == [
        ("HD_Mesh", "no_photos"),
        ("zOrtho4XP_+45+005", "photos"),
        ("z_osxp_eur", "live"),
    ]
    assert other_mesh(eur, None, root).meshes == [eur]  # the region's own folder chosen: that one
    assert other_mesh(tmp_path / "unplugged", None, root) == NO_OTHER_MESH
    assert other_mesh(None, None, root) == NO_OTHER_MESH
    assert other_mesh(folder / "notes", None, root) == NO_OTHER_MESH


def test_autoortho_s_own_layout_moved(tmp_path: Path) -> None:
    """AutoOrtho's pack taken out of X-Plane as it is: its region two folders down, its overlays
    beside it."""
    folder = tmp_path / "moved"
    aus = mesh(folder / "z_autoortho" / "scenery" / "z_ao_aus_pac")
    (folder / "z_autoortho" / "aus_pac_info.json").write_text("{}")
    put(folder / "yAutoOrtho_Overlays", A)
    found = other_mesh(folder, None, tmp_path / "live")
    assert found.meshes == [aus]
    assert found.overlays == [folder / "yAutoOrtho_Overlays"]
    assert other_mesh(folder / "z_autoortho", None, tmp_path / "live").meshes == [aus]


def test_x_plane_s_folder_and_live_s_left_aside(tmp_path: Path) -> None:
    """Chosen by mistake, X-Plane's folder gave every airport and Landmark as roads, and Live's
    its own regions (the review of 2026-11-14): both refused whole, and never looked into from a
    folder above them."""
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    put(xp / "Custom Scenery" / "Aerosoft - LFMN Nice Cote d Azur X", A)  # no terrain: an airport
    root = tmp_path / "live"
    put(root / "overlays", A)
    eur = mesh(tmp_path / "Mesh_Eur")
    assert other_mesh(xp, xp, root) == OtherMesh([], [], [], "in_xplane")
    assert other_mesh(xp / "Custom Scenery", xp, root).whole == "in_xplane"
    assert other_mesh(root, xp, root).whole == "in_live"
    found = other_mesh(tmp_path, xp, root)  # the folder holding them all
    assert found.meshes == [eur] and found.overlays == [] and found.whole is None


def test_after_lives_and_autoortho_s_a_copy_left_aside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live's mesh first, AutoOrtho's in X-Plane next, the chosen folder's last; a copy of a
    region already there adds no square and is left aside, its listing spared (the review of
    2026-11-14)."""
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    root = tmp_path / "live"
    theirs = mesh(xp / "Custom Scenery" / "z_autoortho" / "scenery" / "z_ao_eur")
    folder = tmp_path / "Base Packages"
    mesh(folder / "Mesh_Eur")  # the same squares as AutoOrtho's
    na = mesh(folder / "Mesh_Na", square=B)
    monkeypatch.setattr(scenery, "other_mesh_folder", lambda: folder)
    found = scenery.other_mesh_found(xp, root, fresh=True)
    assert found.meshes == [na]
    assert found.refused == [("Mesh_Eur", "already")]
    assert default_sources(xp, root) == [theirs, na]


def test_the_folder_looked_at_again_after_a_while(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window's checks come every 3 s and read it twice: once in a while now, at once for a
    new setting or a start (the review of 2026-11-14)."""
    root = tmp_path / "live"
    folder = tmp_path / "Base Packages"
    mesh(folder / "Mesh_Eur")
    monkeypatch.setattr(scenery, "other_mesh_folder", lambda: folder)
    first = scenery.other_mesh_found(None, root)
    mesh(folder / "Mesh_Na", square=B)
    assert scenery.other_mesh_found(None, root) is first
    assert len(scenery.other_mesh_found(None, root, fresh=True).meshes) == 2


def test_the_roads_go_with_the_mesh_of_the_start(tmp_path: Path) -> None:
    """The overlays of the folder are those its start found, passed along: a folder changed
    between two flights gave other roads while the mesh waited for the next start (the review of
    2026-11-14)."""
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    root = tmp_path / "live"
    roads = put(tmp_path / "Mesh_Overlays", A).parents[2]
    assert overlays.overlay_folders(xp, [], root, [roads]) == [roads]
    assert overlays.overlay_folders(xp, [], root) == []


def test_the_setting_and_what_the_window_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kept in Live's settings; the window's line says what Live takes there and leaves aside,
    that it waits for Live's next start, or that the folder is gone."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    folder = tmp_path / "Base Packages"
    mesh(folder / "Mesh_Eur")
    put(folder / "Mesh_Overlays", A)
    mesh(folder / "zOrtho4XP_+45+005", photos=True)
    assert control._other_mesh_said(None) is None
    save_settings(LiveSettings(other_mesh=str(folder)))
    assert load_settings().other_mesh == str(folder)
    assert control._other_mesh_said(None) == {
        "folder": str(folder),
        "there": True,
        "whole": None,
        "next_start": True,  # no start has taken it yet
        "meshes": ["Mesh_Eur"],
        "overlays": ["Mesh_Overlays"],
        "refused": [{"name": "zOrtho4XP_+45+005", "why": "photos"}],
    }
    save_settings(LiveSettings(other_mesh=str(tmp_path / "unplugged")))
    said = control._other_mesh_said(None)  # a new setting, looked at at once
    assert said is not None and said["there"] is False and said["meshes"] == []


def a_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder: Path) -> session.Options:
    """A start with no region of Live's or AutoOrtho's, another tool's mesh chosen in ``folder``."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    save_settings(LiveSettings(other_mesh=str(folder)))
    xp = make_xplane(tmp_path)
    return session.Options(xplane=xp, pack=tmp_path / "pack", no_plugin=True)


def test_a_start_says_the_folder_unplugged(tmp_path: Path, monkeypatch) -> None:
    """Its only mesh on a disk unplugged: the start says so, not « install the region »."""
    options = a_start(tmp_path, monkeypatch, tmp_path / "unplugged")
    with pytest.raises(session.RefusedError, match="is not there"):
        session.prepare(options, log=lambda _: None)


def test_a_start_names_the_folder_it_could_not_link(tmp_path: Path, monkeypatch) -> None:
    """Windows links a network folder only with Developer Mode: the start's error named Live's
    pack, not the folder chosen (the review of 2026-11-14)."""
    folder = tmp_path / "share"
    make_source(folder, tile=(46, 2))
    options = a_start(tmp_path, monkeypatch, folder)

    def refused(*args, **kwargs) -> None:
        raise OsxpError("XP_LINK_FAILED", context={"link": "pack", "reason": "a junction"})

    monkeypatch.setattr(region, "setup_region", refused)
    with pytest.raises(session.RefusedError, match="could not link the mesh of"):
        session.prepare(options, log=lambda _: None)


def test_a_start_keeps_the_roads_it_found(tmp_path: Path, monkeypatch) -> None:
    """Its overlays found once, with its mesh, and kept for the rescans between flights."""
    folder = tmp_path / "Base Packages"
    make_source(folder, tile=(46, 2))
    roads = put(folder / "Mesh_Overlays", (46, 2)).parents[2]
    options = a_start(tmp_path, monkeypatch, folder)
    given: list = []

    def overlays_of(*args) -> None:
        given.append(args[-1])

    monkeypatch.setattr(session, "_overlays", overlays_of)
    prepared = session.prepare(options, log=lambda _: None)
    assert prepared.sources == [folder / "z_ao_test"]
    assert prepared.other_overlays == [roads] and given == [[roads]]
