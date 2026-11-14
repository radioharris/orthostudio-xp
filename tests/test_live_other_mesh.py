# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Another tool's mesh, from a folder chosen in Live's Settings: a region copied by another
streamer, AutoOrtho's moved elsewhere. Taken by what they hold, whatever their names: after Live's
own mesh and AutoOrtho's in X-Plane, overlays last too."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_live_overlays import put, xplane

from orthostudio.live import control, overlays, scenery
from orthostudio.live.scenery import NO_OTHER_MESH, default_sources, other_mesh
from orthostudio.live.settings import LiveSettings, load_settings, save_settings

A = (45, 5)
PHOTO_TER = b"A\n800\nTERRAIN\n\nBASE_TEX_NOWRAP ../textures/24528_38224_BI16.dds\n"


def mesh(pack: Path, ter: bytes = PHOTO_TER, photos: bool = False) -> Path:
    """A pack of AutoOrtho's format: a DSF, a terrain file naming a photo, a water mask."""
    put(pack, A)
    (pack / "terrain").mkdir(parents=True, exist_ok=True)
    (pack / "terrain" / "24528_38224_BI16.ter").write_bytes(ter)
    (pack / "textures").mkdir(exist_ok=True)
    (pack / "textures" / "24528_38224_ZL16.png").write_bytes(b"mask")
    if photos:
        (pack / "textures" / "24528_38224_BI16.dds").write_bytes(b"DDS ")
    return pack


def test_the_folder_taken_by_what_it_holds(tmp_path: Path) -> None:
    """Another streamer's folder: its region, its overlays; a tile with its photos and a mesh of
    X-Plane's land classes left aside, said why; a folder with no pack gives nothing."""
    folder = tmp_path / "Base Packages"
    eur = mesh(folder / "Mesh_Eur")
    put(folder / "Mesh_Overlays", A)
    mesh(folder / "zOrtho4XP_+45+005", photos=True)
    mesh(folder / "HD_Mesh", ter=b"A\n800\nTERRAIN\n\nBASE_TEX ../textures/grass_dry.dds\n")
    (folder / "notes").mkdir()
    found = other_mesh(folder)
    assert found.meshes == [eur]
    assert found.overlays == [folder / "Mesh_Overlays"]
    assert found.refused == [("HD_Mesh", "no_photos"), ("zOrtho4XP_+45+005", "photos")]
    assert other_mesh(eur).meshes == [eur]  # the region's own folder chosen: that one
    assert other_mesh(tmp_path / "unplugged") == NO_OTHER_MESH
    assert other_mesh(None) == NO_OTHER_MESH
    assert other_mesh(folder / "notes") == NO_OTHER_MESH


def test_autoortho_s_own_layout_moved(tmp_path: Path) -> None:
    """AutoOrtho's pack taken out of X-Plane as it is: its region two folders down, its overlays
    beside it."""
    folder = tmp_path / "moved"
    region = mesh(folder / "z_autoortho" / "scenery" / "z_ao_aus_pac")
    (folder / "z_autoortho" / "aus_pac_info.json").write_text("{}")
    put(folder / "yAutoOrtho_Overlays", A)
    found = other_mesh(folder)
    assert found.meshes == [region]
    assert found.overlays == [folder / "yAutoOrtho_Overlays"]
    assert other_mesh(folder / "z_autoortho").meshes == [region]


def chosen(monkeypatch: pytest.MonkeyPatch, folder: Path | None) -> None:
    """The folder of another tool's mesh the Settings would say."""
    for module in (scenery, overlays):
        monkeypatch.setattr(module, "other_mesh_folder", lambda: folder)


def test_after_lives_and_autoortho_s_each_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live's mesh first, AutoOrtho's in X-Plane next, the chosen folder's last; AutoOrtho's own
    folder chosen as the other tool's counts once. Their overlays in the same order."""
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    root = tmp_path / "live"
    theirs = mesh(xp / "Custom Scenery" / "z_autoortho" / "scenery" / "z_ao_na")
    folder = tmp_path / "Base Packages"
    eur = mesh(folder / "Mesh_Eur")
    put(folder / "Mesh_Overlays", A)
    chosen(monkeypatch, folder)
    assert default_sources(xp, root) == [theirs, eur]
    assert overlays.overlay_folders(xp, [theirs, eur], root)[-1] == folder / "Mesh_Overlays"
    chosen(monkeypatch, theirs.parent)
    assert default_sources(xp, root) == [theirs]  # AutoOrtho's, once
    chosen(monkeypatch, None)
    assert default_sources(xp, root) == [theirs]


def test_the_setting_and_what_the_window_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kept in Live's settings; the window's line says what Live takes there and leaves aside,
    or that the folder is gone."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    folder = tmp_path / "Base Packages"
    mesh(folder / "Mesh_Eur")
    put(folder / "Mesh_Overlays", A)
    mesh(folder / "zOrtho4XP_+45+005", photos=True)
    assert control._other_mesh_said() is None
    save_settings(LiveSettings(other_mesh=str(folder)))
    assert load_settings().other_mesh == str(folder)
    assert control._other_mesh_said() == {
        "folder": str(folder),
        "there": True,
        "meshes": ["Mesh_Eur"],
        "overlays": ["Mesh_Overlays"],
        "refused": [{"name": "zOrtho4XP_+45+005", "why": "photos"}],
    }
    save_settings(LiveSettings(other_mesh=str(tmp_path / "unplugged")))
    said = control._other_mesh_said()
    assert said is not None and said["there"] is False and said["meshes"] == []
