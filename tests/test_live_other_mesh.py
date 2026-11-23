# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Other tools' meshes, from folders chosen in Live's Settings: a region copied by another
streamer, AutoOrtho's moved elsewhere. Taken by what their packs hold, whatever their names, as
X-Plane tells ground from overlays: after Live's own mesh and AutoOrtho's in X-Plane, each
folder's roads the one overlay pack that goes with its meshes."""

from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest
from tests.test_live_overlays import xplane

from live_helpers import atom, make_source, make_xplane, other_meshes, put, save_settings
from orthostudio.errors import OsxpError
from orthostudio.live import control, overlays, region, scenery, session
from orthostudio.live.scenery import (
    NO_OTHER_MESH,
    NOTHING_READ,
    Meshes,
    base_sources,
    default_meshes,
)
from orthostudio.live.settings import LiveSettings, load_settings

A, B, C, D = (45, 5), (46, 7), (47, 8), (48, 9)
PHOTO_TER = b"A\n800\nTERRAIN\n\nBASE_TEX_NOWRAP ../textures/24528_38224_BI16.dds\n"


def overlay_dsf(agent: str | None) -> bytes:
    """An overlay DSF's head, made by ``agent`` (``sim/creation_agent``), or by none said."""
    props = b"sim/overlay\x001\x00" + (
        f"sim/creation_agent\x00{agent}\x00".encode() if agent else b""
    )
    return b"XPLNEDSF" + struct.pack("<i", 1) + atom(b"HEAD", atom(b"PROP", props))


XPLANE_MADE = "X-Plane Scenery Creator 0.9a"  # X-Plane's own overlays, AutoOrtho's and Live's


def mesh(pack: Path, ter: bytes = PHOTO_TER, photos: bool = False, squares=(A,)) -> Path:
    """A pack of AutoOrtho's format: ground DSFs, a terrain file naming a photo, a water mask."""
    for square in squares:
        put(pack, square)
    (pack / "terrain").mkdir(parents=True, exist_ok=True)
    (pack / "terrain" / "24528_38224_BI16.ter").write_bytes(ter)
    (pack / "textures").mkdir(exist_ok=True)
    (pack / "textures" / "24528_38224_ZL16.png").write_bytes(b"mask")
    if photos:
        (pack / "textures" / "24528_38224_BI16.dds").write_bytes(b"DDS ")
    return pack


def roads(pack: Path, squares=(A,), agent: str | None = XPLANE_MADE) -> Path:
    """An overlay pack: DSFs X-Plane reads as overlays (``sim/overlay 1``), X-Plane's own roads,
    forests and buildings unless another ``agent`` made them."""
    for square in squares:
        put(pack, square, overlay_dsf(agent))
    return pack


@pytest.fixture(autouse=True)
def looked_at_anew(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test's folder looked at, not one kept from another test."""
    monkeypatch.setattr(scenery, "_found", (0.0, (), NOTHING_READ))


def test_the_folder_taken_by_what_it_holds(tmp_path: Path) -> None:
    """Another streamer's folder: its region, its roads; a tile with its photos, a mesh of
    X-Plane's land classes and a region of Live's kept there left aside, said why; a folder with
    no pack gives nothing."""
    folder = tmp_path / "Other mesh"
    eur = mesh(folder / "Mesh_Eur")
    eur_roads = roads(folder / "Mesh_Overlays")
    mesh(folder / "zOrtho4XP_+45+005", photos=True)
    mesh(folder / "HD_Mesh", ter=b"A\n800\nTERRAIN\n\nBASE_TEX ../textures/grass_dry.dds\n")
    put(folder / "z_osxp_eur", A)  # one of Live's regions, kept on another disk: no terrain
    (folder / "z_osxp_eur" / "terrain-lists").mkdir()
    put(folder / "Virtual_mount", B)  # a virtual disk's mount point, its streamer not running
    (folder / "Virtual_mount" / "terrain").mkdir()
    (folder / "notes").mkdir()
    (folder / "1200 atc data" / "Earth nav data").mkdir(parents=True)  # no DSF: no scenery
    found = other_meshes([folder]).folders[0].found
    assert found.meshes == [eur]
    assert found.overlays == [eur_roads]
    assert sorted(found.refused) == [  # Windows orders the folders without their case
        ("HD_Mesh", "no_photos"),
        ("Virtual_mount", "no_terrain"),
        ("zOrtho4XP_+45+005", "photos"),
        ("z_osxp_eur", "no_terrain"),
    ]
    # the region's own folder chosen: that one
    assert other_meshes([eur]).folders[0].found.meshes == [eur]
    assert other_meshes([tmp_path / "unplugged"]).folders[0].found == NO_OTHER_MESH
    assert other_meshes([folder / "notes"]).folders[0].found == NO_OTHER_MESH


def test_autoortho_s_own_layout_moved(tmp_path: Path) -> None:
    """AutoOrtho's pack taken out of X-Plane as it is: its region two folders down, its overlays
    beside it."""
    folder = tmp_path / "moved"
    aus = mesh(folder / "z_autoortho" / "scenery" / "z_ao_aus_pac")
    (folder / "z_autoortho" / "aus_pac_info.json").write_text("{}")
    aus_roads = roads(folder / "yAutoOrtho_Overlays")
    found = other_meshes([folder]).folders[0].found
    assert found.meshes == [aus] and found.overlays == [aus_roads]
    assert other_meshes([folder / "z_autoortho"]).folders[0].found.meshes == [aus]


def test_the_roads_that_go_with_the_mesh(tmp_path: Path) -> None:
    """Every pack without terrain was taken for roads, the first by name winning its square: an
    airport, simHeaven or another X-Plane's own scenery lying beside AutoOrtho's pack took Sydney's
    (the review of 2026-11-14). The overlay pack whose squares best match the mesh's is its roads;
    another X-Plane's ground is no mesh, having no photos named."""
    folder = tmp_path / "Scenery put aside"
    aus = mesh(folder / "z_autoortho" / "scenery" / "z_ao_aus_pac", squares=(A, B))
    aus_roads = roads(folder / "yAutoOrtho_Overlays", (A, B))
    roads(folder / "Aerosoft - YSSY Sydney", (A,), "WorldEditor2.7.0-r1")
    roads(
        folder / "simHeaven_X-World", (A, B, C, D), "Scenery Designer Pro (SDP) 1.2b by simHeaven"
    )
    roads(folder / "Global_Forests_v2", (A, B, C, D), None)
    world = folder / "X-Plane 11" / "Global Scenery" / "X-Plane 11 Global Scenery"
    for square in (A, B, C, D):
        put(world, square)  # ground, with no terrain folder
    found = other_meshes([folder]).folders[0].found
    assert found.meshes == [aus] and found.overlays == [aus_roads]
    assert found.refused == [("X-Plane 11 Global Scenery", "no_terrain")]
    only_roads = tmp_path / "only roads"
    roads(only_roads / "yAutoOrtho_Overlays", (A, B))
    # no mesh, no roads to go with it
    assert other_meshes([only_roads]).folders[0].found.overlays == []


def test_the_roads_of_every_region_kept_beside_a_copy(tmp_path: Path) -> None:
    """The other streamer's Europe, a copy of AutoOrtho's in X-Plane, left aside, and its
    Australia kept, with one roads pack for both: scored against Australia alone, the pack lost
    to Europe's squares and Australia had no roads (the fourth review of 2026-11-14)."""
    theirs = mesh(tmp_path / "X-Plane 12" / "z_ao_eur", squares=(A, C))
    folder = tmp_path / "Other mesh"
    mesh(folder / "Mesh_Eur", squares=(A, C))
    aus = mesh(folder / "Mesh_Aus", squares=(B,))
    both = roads(folder / "Mesh_Overlays", (A, B, C))
    found = other_meshes([folder], [theirs]).folders[0].found
    assert found.meshes == [aus] and found.refused == [("Mesh_Eur", "already")]
    assert found.overlays == [both]


def test_the_roads_of_one_region_moved_with_the_others(tmp_path: Path) -> None:
    """AutoOrtho's one roads pack holds every region of its: moved with one region's mesh only, it
    has more squares elsewhere than on it, and was taken for none (the fifth review of
    2026-11-14). Most of the mesh is enough."""
    folder = tmp_path / "Other mesh"
    aus = mesh(folder / "z_autoortho" / "scenery" / "z_ao_aus_pac", squares=(B,))
    every = roads(folder / "yAutoOrtho_Overlays", (A, B, C, D))
    found = other_meshes([folder]).folders[0].found
    assert found.meshes == [aus] and found.overlays == [every]


def test_a_mesh_without_its_roads_said(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chosen one folder too deep, AutoOrtho's mesh came without its roads beside it: said as a
    fact of the folder, not a guess of what Live places (AutoOrtho's may be left in X-Plane)."""
    folder = tmp_path / "moved"
    mesh(folder / "z_autoortho" / "scenery" / "z_ao_aus_pac")
    roads(folder / "yAutoOrtho_Overlays")
    deep = folder / "z_autoortho"
    found = other_meshes([deep])
    assert session._other_said(found.folders[0], found) == (
        f"another tool's mesh in {deep}: 1 square given; uses z_ao_aus_pac; no roads, forests or "
        "buildings taken from it"
    )


def test_no_roads_rather_than_another_s_overlays(tmp_path: Path) -> None:
    """A mesh moved without its roads: an airport beside it was taken for them (the fourth review
    of 2026-11-14), then Global Forests, which covers it all (the review of 3ac4a84b). Its roads
    are X-Plane's own, or none."""
    folder = tmp_path / "Other mesh"
    mesh(folder / "z_ao_aus_pac", squares=(A, B, C))
    roads(folder / "Aerosoft - YSSY Sydney", (A,), "WorldEditor2.7.0-r1")
    roads(folder / "Global_Forests_v2", (A, B, C, D), None)
    assert other_meshes([folder]).folders[0].found.overlays == []


def test_x_plane_s_folder_or_live_s_give_nothing(tmp_path: Path) -> None:
    """Chosen by mistake: X-Plane's scenery holds tiles with their photos, airports, AutoOrtho's
    region already used; Live's folder its own regions, without photos named."""
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    scenery_dir = xp / "Custom Scenery"
    mesh(scenery_dir / "z_autoortho" / "scenery" / "z_ao_eur")
    roads(scenery_dir / "Aerosoft - LFMN Nice Cote d Azur X")
    mesh(scenery_dir / "zOrtho4XP_+45+005", photos=True)
    root = tmp_path / "live"
    put(root / "regions" / "z_osxp_eur", A)
    roads(root / "regions" / "y_osxp_overlays")
    given = base_sources(xp, root)
    for chosen in (xp, scenery_dir, root):
        found = other_meshes([chosen], given).folders[0].found
        assert found.meshes == [] and found.overlays == [], chosen
    assert ("z_ao_eur", "already") in other_meshes([scenery_dir], given).folders[0].found.refused


def test_after_lives_and_autoortho_s_a_copy_left_aside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live's mesh first, AutoOrtho's in X-Plane next, the chosen folder's last; a copy of a
    region already there adds no square and is left aside, its listing spared (the review of
    2026-11-14)."""
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    root = tmp_path / "live"
    theirs = mesh(xp / "Custom Scenery" / "z_autoortho" / "scenery" / "z_ao_eur")
    folder = tmp_path / "Other mesh"
    mesh(folder / "Mesh_Eur")  # the same squares as AutoOrtho's
    na = mesh(folder / "Mesh_Na", squares=(B,))
    monkeypatch.setattr(scenery, "other_mesh_folders", lambda settings=None: [folder])
    found = scenery.other_mesh_found(xp, root, fresh=True)
    assert found.meshes == [na]
    assert found.folders[0].found.refused == [("Mesh_Eur", "already")]
    assert default_meshes(xp, root) == Meshes([theirs, na], {})


def test_the_folder_looked_at_again_after_a_while(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once in a while, at once for a start, or as the folder comes back: its disk plugged in
    again, the window said « no mesh » 30 s more (the third review of 2026-11-14)."""
    root = tmp_path / "live"
    folder = tmp_path / "Other mesh"
    monkeypatch.setattr(scenery, "other_mesh_folders", lambda settings=None: [folder])
    assert scenery.other_mesh_found(None, root).meshes == []  # its disk away
    mesh(folder / "Mesh_Eur")
    first = scenery.other_mesh_found(None, root)  # back: looked at at once
    assert len(first.meshes) == 1
    mesh(folder / "Mesh_Na", squares=(B,))
    assert scenery.other_mesh_found(None, root) == first  # the folder not read again
    assert len(scenery.other_mesh_found(None, root, fresh=True).meshes) == 2


def test_the_window_reads_the_folder_once_in_a_while(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its checks come every 3 s and asked for the folder twice, without X-Plane's folder then with
    it: each forgot what the other found, and the folder was read twice each time (the second
    review of 2026-11-14)."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    monkeypatch.setattr(control.session, "xplane_dir", lambda explicit: xp)
    folder = tmp_path / "Other mesh"
    mesh(folder / "Mesh_Eur")
    save_settings(LiveSettings(other_meshes=[str(folder)]))
    read: list[Path] = []
    real = scenery.read_packs

    def counted(folder):
        read.append(folder)
        return real(folder)

    monkeypatch.setattr(scenery, "read_packs", counted)
    for _ in range(3):
        checks = control.gather_checks()
    assert checks["other_meshes"]["folders"][0]["meshes"] == ["Mesh_Eur"]
    assert checks["mesh_regions"] == ["Mesh_Eur"]
    assert read == [folder]
    # Live's Europe and AutoOrtho's said « Europe, Europe » (the review of 0eb7e561): once
    mesh(xp / "Custom Scenery" / "z_autoortho" / "scenery" / "z_ao_eur", squares=(B,))
    monkeypatch.setattr(
        control, "stream_meshes", lambda xp: Meshes([Path("z_osxp_eur"), Path("z_ao_eur")], {})
    )
    assert control.gather_checks()["mesh_regions"] == ["eur"]


def test_the_roads_go_with_the_mesh_of_the_start(tmp_path: Path) -> None:
    """The overlays of the folder are those its start found, passed along: a folder changed
    between two flights gave other roads while the mesh waited for the next start (the review of
    2026-11-14)."""
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    root = tmp_path / "live"
    found = roads(tmp_path / "Mesh_Overlays")
    assert overlays.overlay_folders(xp, [], root, [found]) == [found]
    assert overlays.overlay_folders(xp, [], root) == []


def test_the_setting_and_what_the_window_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kept in Live's settings; the window's line of each folder says what Live takes there and
    leaves aside, that it waits for Live's next start, or that the folder is gone."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    folder = tmp_path / "Other mesh"
    mesh(folder / "Mesh_Eur")
    roads(folder / "Mesh_Overlays")
    mesh(folder / "zOrtho4XP_+45+005", photos=True)
    assert control._other_meshes_said(None) is None
    save_settings(LiveSettings(other_meshes=[str(folder), str(tmp_path / "unplugged")]))
    assert load_settings().other_meshes == [str(folder), str(tmp_path / "unplugged")]
    said = control._other_meshes_said(None)
    assert said is not None and set(said) == {"folders", "front_unseen"}
    assert said["folders"][0] == {
        "folder": str(folder),
        "name": "Other mesh",
        "next_start": True,  # no start has taken it yet
        "meshes": ["Mesh_Eur"],
        "overlays": ["Mesh_Overlays"],
        "refused": [{"name": "zOrtho4XP_+45+005", "why": "photos"}],
        "squares": 1,
        "front": [],
        "unused": None,
        "in_xplane": [],
    }
    gone = said["folders"][1]
    assert gone["unused"] == "gone" and gone["meshes"] == [] and gone["name"] == "unplugged"
    assert gone["squares"] == 0 and "there" not in gone  # one fact of each case: unused
    # typed by hand: « ~ » is the home folder; a relative path, which would depend on where Live
    # was started, is not there (the fourth review of 2026-11-14)
    save_settings(LiveSettings(other_meshes=["~/Other mesh"]))
    assert scenery.other_mesh_folders() == [Path.home() / "Other mesh"]
    monkeypatch.chdir(tmp_path)
    save_settings(LiveSettings(other_meshes=["Other mesh"]))
    said = control._other_meshes_said(None)
    assert said is not None and said["folders"][0]["unused"] == "gone"
    assert said["folders"][0]["meshes"] == []
    # « ~someone » with no such user stopped the window's checks and every start (the fifth
    # review of 2026-11-14): as typed, not there
    save_settings(LiveSettings(other_meshes=["~nosuchpilot1234/mesh"]))
    said = control._other_meshes_said(None)
    assert said is not None and said["folders"][0]["unused"] == "gone"


def a_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *folders: Path) -> session.Options:
    """A start with no region of Live's or AutoOrtho's, other tools' meshes chosen in
    ``folders``."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    save_settings(LiveSettings(other_meshes=[str(folder) for folder in folders]))
    xp = make_xplane(tmp_path)
    return session.Options(xplane=xp, pack=tmp_path / "pack", no_plugin=True)


def test_a_start_says_the_folder_unplugged(tmp_path: Path, monkeypatch) -> None:
    """Its only mesh on a disk unplugged: the start says so, not « install the region »."""
    options = a_start(tmp_path, monkeypatch, tmp_path / "unplugged")
    with pytest.raises(session.RefusedError, match="is not there"):
        session.prepare(options, log=lambda _: None)


def test_a_start_says_the_folder(tmp_path: Path, monkeypatch) -> None:
    """Its disk unplugged while another region is installed: the start says what it takes in each
    folder, or that one is not there, and goes on with the next folder."""
    folder = tmp_path / "Other mesh"
    make_source(folder, tile=(46, 2))
    gone = tmp_path / "unplugged"
    found = other_meshes([folder, gone])
    assert session._other_said(found.folders[0], found) == (
        f"another tool's mesh in {folder}: 1 square given; uses z_ao_test; no roads, forests or "
        "buildings taken from it"
    )
    said_gone = f"another tool's mesh: {gone} is not there (its disk unplugged?): left out"
    assert session._other_said(found.folders[1], found) == said_gone
    options = a_start(tmp_path, monkeypatch, gone, folder)
    said: list[str] = []
    monkeypatch.setattr(session, "_overlays", lambda *args: None)
    prepared = session.prepare(options, log=said.append)
    assert said_gone in said and prepared.sources == [folder / "z_ao_test"]


def test_a_start_names_the_folder_it_could_not_link(tmp_path: Path, monkeypatch) -> None:
    """Windows links a network folder only with Developer Mode: the start's error named Live's
    pack, not the folder chosen (the review of 2026-11-14); elsewhere the error is left as it is
    (the second review)."""
    folder = tmp_path / "share"
    make_source(folder, tile=(46, 2))
    options = a_start(tmp_path, monkeypatch, folder)

    def refused(*args, **kwargs) -> None:
        raise OsxpError("XP_LINK_FAILED", context={"link": "pack", "reason": "a junction"})

    monkeypatch.setattr(region, "setup_region", refused)
    monkeypatch.setattr(session.sys, "platform", "win32")
    # escaped: a Windows path's backslashes
    said = re.escape(f"another tool's mesh, in {folder}, on a network")
    with pytest.raises(session.RefusedError, match=said):
        session.prepare(options, log=lambda _: None)
    monkeypatch.setattr(session.sys, "platform", "darwin")
    with pytest.raises(OsxpError):
        session.prepare(options, log=lambda _: None)


def test_a_start_keeps_the_roads_it_found(tmp_path: Path, monkeypatch) -> None:
    """Its overlays found once, with its mesh, and kept for the rescans between flights."""
    folder = tmp_path / "Other mesh"
    make_source(folder, tile=(46, 2))
    found = roads(folder / "Mesh_Overlays", ((46, 2),))
    options = a_start(tmp_path, monkeypatch, folder)
    given: list = []

    def overlays_of(*args) -> None:
        given.append(args[-1])

    monkeypatch.setattr(session, "_overlays", overlays_of)
    prepared = session.prepare(options, log=lambda _: None)
    assert prepared.sources == [folder / "z_ao_test"]
    assert prepared.other_overlays == [found] and given == [[found]]
