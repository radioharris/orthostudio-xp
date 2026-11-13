# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""AutoOrtho's roads, forests and buildings on Live's squares, where nothing else draws them: not on
the pilot's own tiles, not under another overlay pack, not where simHeaven X-World is; listed
below every other overlay pack, where Global Forests' exclusion hides their forests."""

from __future__ import annotations

import math
from pathlib import Path

from orthostudio.install.scenery_packs import SceneryPacks
from orthostudio.live import aoregions, overlays, session
from orthostudio.live.filesystem import is_folder_link
from orthostudio.live.pack import LINK_NAME, OVERLAY_LINK_NAME
from orthostudio.live.scenery import default_sources, install_link

A, B, C, D, E = (45, 5), (45, 6), (46, 5), (46, 6), (47, 5)


def put(pack: Path, square: tuple[int, int], data: bytes = b"XPLNEDSF") -> Path:
    lat, lon = square
    folder = (
        pack
        / "Earth nav data"
        / f"{math.floor(lat / 10) * 10:+03d}{math.floor(lon / 10) * 10:+04d}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{lat:+03d}{lon:+04d}.dsf"
    path.write_bytes(data)
    return path


def xplane(tmp_path: Path, lines: list[str]) -> Path:
    xp = tmp_path / "X-Plane 12"
    scenery = xp / "Custom Scenery"
    scenery.mkdir(parents=True)
    (scenery / "scenery_packs.ini").write_text(
        "I\n1000 Version\nSCENERY\n\n" + "".join(f"{line}\n" for line in lines)
    )
    return xp


def test_only_where_nothing_else_draws_them(tmp_path: Path) -> None:
    xp = xplane(
        tmp_path,
        [
            "SCENERY_PACK *GLOBAL_AIRPORTS*",
            "SCENERY_PACK Custom Scenery/simHeaven_X-WORLD-Pro_Europe-13-net3-roads/",
            "SCENERY_PACK Custom Scenery/Global_Forests_v2/",
            "SCENERY_PACK Custom Scenery/yOrtho4XP_Overlays/",
            "SCENERY_PACK_DISABLED Custom Scenery/yAutoOrtho_Overlays/",
            "SCENERY_PACK Custom Scenery/z_ao_eur/",
        ],
    )
    scenery = xp / "Custom Scenery"
    pack = tmp_path / "live" / "pack"
    for square in (A, B, C, D, E):
        put(pack, square)
        put(scenery / "yAutoOrtho_Overlays", square, b"overlay")  # AutoOrtho's, switched off
    put(scenery / "simHeaven_X-WORLD-Pro_Europe-13-net3-roads", B)
    put(scenery / "yOrtho4XP_Overlays", C)
    put(scenery / "Global_Forests_v2", E)  # its exclusion hides the forests only: E keeps them
    sources = [scenery / "z_autoortho" / "scenery" / "z_ao_eur"]
    said: list[str] = []
    out = tmp_path / "live" / "overlays"
    placed = overlays.build(
        xp, pack, out, sources, tmp_path / "live", {D: "zOrthoStudio"}, log=said.append
    )
    assert placed == 2
    assert sorted(overlays.squares_of(out)) == [A, E]  # B simHeaven's, C Ortho4XP's, D the pilot's
    assert (overlays.squares_of(out)[A]).read_bytes() == b"overlay"
    assert "simHeaven X-World" in said[0] or "yOrtho4XP_Overlays" in said[0]
    # switched off: none, and what was there goes
    assert overlays.build(xp, pack, out, sources, tmp_path / "live", {}, "off", said.append) == 0
    assert overlays.squares_of(out) == {}


def test_overlays_listed_under_live_s_line_do_not_count(tmp_path: Path) -> None:
    """X-Plane reads no overlay listed under the ground it shows: an overlays pack Build listed
    first between Live's line and the pilot's first tile ("Live everywhere") left Live's squares
    without roads or forests (the review of 2026-11-13). Live brings its own there."""
    xp = xplane(
        tmp_path,
        [
            "SCENERY_PACK *GLOBAL_AIRPORTS*",
            "SCENERY_PACK Custom Scenery/yOrtho4XP_Overlays/",
            f"SCENERY_PACK Custom Scenery/{LINK_NAME}/",
            "SCENERY_PACK Custom Scenery/yOrthoStudio_Overlays/",
            "SCENERY_PACK Custom Scenery/zOrthoStudio_+46+005/",
            "SCENERY_PACK Custom Scenery/z_ao_eur/",
        ],
    )
    scenery = xp / "Custom Scenery"
    put(scenery / "yOrtho4XP_Overlays", A)
    put(scenery / "yOrthoStudio_Overlays", B)
    assert overlays.other_overlays(xp) == {A: "yOrtho4XP_Overlays"}


def test_a_region_live_installed_brings_its_own(tmp_path: Path) -> None:
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    root = tmp_path / "live"
    mesh = aoregions.mesh_dir(root, "na")
    put(mesh, (40, -75))
    (mesh / "terrain").mkdir()
    (mesh / aoregions.MARKER).write_text('{"release": "0.0.54"}')
    put(aoregions.overlays_dir(root, "na"), (40, -75), b"na overlay")
    sources = default_sources(xp, root)
    assert sources == [mesh]  # Live's own install, AutoOrtho having none
    pack = root / "pack"
    put(pack, (40, -75))
    assert overlays.build(xp, pack, root / "overlays", sources, root, {}, log=lambda _: None) == 1
    # AutoOrtho's own install of the region is used first, Live's left aside
    theirs = xp / "Custom Scenery" / "z_autoortho" / "scenery" / "z_ao_na"
    (theirs / "terrain").mkdir(parents=True)
    assert default_sources(xp, root) == [theirs]


def test_a_region_of_another_live_folder_brings_its_own(tmp_path: Path) -> None:
    """A cold test flight's Live has a folder of its own, and streams onto the region Live
    installed in the user's (tools/live/flights/fly.py): its overlays come with it."""
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    users = tmp_path / "live"
    mesh = aoregions.mesh_dir(users, "eur")
    put(mesh, A)
    (mesh / "terrain").mkdir()
    (mesh / aoregions.MARKER).write_text('{"release": "0.0.54"}')
    put(aoregions.overlays_dir(users, "eur"), A, b"eur overlay")
    cold = tmp_path / "flight" / "live"
    assert overlays.overlay_folders(xp, [mesh], cold) == [aoregions.overlays_dir(users, "eur")]
    pack = cold / "pack"
    put(pack, A)
    assert overlays.build(xp, pack, cold / "overlays", [mesh], cold, {}, log=lambda _: None) == 1
    assert overlays.squares_of(cold / "overlays")[A].read_bytes() == b"eur overlay"
    # AutoOrtho's own install, without Live's marker: its overlays are in yAutoOrtho_Overlays
    theirs = xp / "Custom Scenery" / "z_autoortho" / "scenery" / "z_ao_eur"
    put(theirs.with_name("y_ao_eur"), A)
    assert overlays.overlay_folders(xp, [theirs], cold) == []


def test_listed_above_the_ortho_below_every_other_overlay(tmp_path: Path) -> None:
    xp = xplane(
        tmp_path,
        [
            "SCENERY_PACK Custom Scenery/LFLL Lyon/",
            "SCENERY_PACK *GLOBAL_AIRPORTS*",
            "SCENERY_PACK Custom Scenery/simHeaven_X-WORLD-Pro_Europe-13-net3-roads/",
            "SCENERY_PACK Custom Scenery/Global_Forests_v2/",
            "SCENERY_PACK Custom Scenery/z_ao_eur/",
        ],
    )
    for name, folder, kind in (
        (LINK_NAME, tmp_path / "pack", "ortho"),
        (OVERLAY_LINK_NAME, tmp_path / "overlays", "overlay"),
    ):
        folder.mkdir()
        install_link(xp, folder, name, log=lambda _: None, kind=kind)
    names = [e.name for e in SceneryPacks.load(xp / "Custom Scenery" / "scenery_packs.ini").entries]
    assert (
        names.index("Global_Forests_v2") < names.index(OVERLAY_LINK_NAME) < names.index(LINK_NAME)
    )
    assert names.index(LINK_NAME) < names.index("z_ao_eur")
    # out of X-Plane once it has quit: both lines unchecked where they are, their links kept
    session.uninstall(xp, lambda _: None)
    packs = SceneryPacks.load(xp / "Custom Scenery" / "scenery_packs.ini")
    assert [e.name for e in packs.entries] == names
    assert not packs.find(OVERLAY_LINK_NAME).enabled and not packs.find(LINK_NAME).enabled
    assert is_folder_link(xp / "Custom Scenery" / OVERLAY_LINK_NAME)


def test_live_everywhere_keeps_its_overlays_above_its_pack(tmp_path: Path, monkeypatch) -> None:
    """ "Live everywhere" lists Live's pack above the pilot's own tiles; its overlays, listed new
    after it, went under it, where X-Plane reads no overlay of a square whose ground a pack above
    gives: no roads, forests or buildings on Live's squares (KSME, 2026-10-26). They go right above
    Live's pack, and back there at the next start from wherever they were."""
    xp = xplane(
        tmp_path,
        [
            "SCENERY_PACK *GLOBAL_AIRPORTS*",
            "SCENERY_PACK Custom Scenery/Global_Forests_v2/",
            "SCENERY_PACK_DISABLED Custom Scenery/zOSXP_TEST_KSME_XPME_copie_locale/",
            f"SCENERY_PACK Custom Scenery/{LINK_NAME}/",
            "SCENERY_PACK Custom Scenery/zOrthoStudio_+12+080/",
            "SCENERY_PACK Custom Scenery/zOrthoStudio_+37-085/",
        ],
    )
    folder = tmp_path / "live" / "overlays"
    folder.mkdir(parents=True)
    monkeypatch.setattr(session, "overlay_dir", lambda: folder)
    monkeypatch.setattr(overlays, "build", lambda *a, **k: 1)
    ini = xp / "Custom Scenery" / "scenery_packs.ini"
    for _ in range(2):
        session._overlays(xp, tmp_path / "pack", [], {}, "auto", False, lambda _: None)
        names = [e.name for e in SceneryPacks.load(ini).entries]
        assert names.index("Global_Forests_v2") < names.index(OVERLAY_LINK_NAME)
        assert names.index(OVERLAY_LINK_NAME) + 1 == names.index(LINK_NAME)
        # a line left under Live's pack, as the start of 17:12 left it
        packs = SceneryPacks.load(ini)
        packs.remove(OVERLAY_LINK_NAME)
        packs.ensure(OVERLAY_LINK_NAME, kind="overlay", above="zOrthoStudio_+12+080")
        packs.save(ini)


def test_live_s_own_overlays_come_before_autoortho_s(tmp_path: Path) -> None:
    """Live's own (regions/y_osxp_overlays, X-Plane's roads cut for its squares)
    give a square first; AutoOrtho's only the squares Live's lack."""
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    scenery = xp / "Custom Scenery"
    root = tmp_path / "live"
    pack = root / "pack"
    for square in (A, B):
        put(pack, square)
        put(scenery / "yAutoOrtho_Overlays", square, b"autoortho")
    put(aoregions.regions_dir(root) / overlays.OSXP_OVERLAYS, A, b"ours")
    said: list[str] = []
    out = root / "overlays"
    assert overlays.build(xp, pack, out, [], root, {}, log=said.append) == 2
    assert overlays.squares_of(out)[A].read_bytes() == b"ours"
    assert overlays.squares_of(out)[B].read_bytes() == b"autoortho"
    assert "X-Plane's on 1, AutoOrtho's on 1" in said[0]


def test_a_start_like_the_one_before_touches_no_file(tmp_path: Path) -> None:
    """Only the squares whose overlay changed are placed or taken away (all 16,621 made again took
    2.2 s of every start, the user's Mac, 2026-10-31): a start like the one before keeps every
    file; a tile of the pilot's takes its square's away, a region's overlay made again replaces it,
    and anything else in the pack goes."""
    xp = xplane(tmp_path, ["SCENERY_PACK *GLOBAL_AIRPORTS*"])
    root = tmp_path / "live"
    pack = root / "pack"
    ours = aoregions.regions_dir(root) / overlays.OSXP_OVERLAYS
    for square in (A, B, C):
        put(pack, square)
        put(ours, square, b"ours")
    out = root / "overlays"
    said: list[str] = []
    assert overlays.build(xp, pack, out, [], root, {}, log=said.append) == 3
    assert "(3 placed and 0 taken away since the last start)" in said[-1]
    files = {square: path.stat().st_ino for square, path in overlays.squares_of(out).items()}
    assert overlays.build(xp, pack, out, [], root, {}, log=said.append) == 3
    assert "(0 placed and 0 taken away since the last start)" in said[-1]
    assert {s: p.stat().st_ino for s, p in overlays.squares_of(out).items()} == files  # the same
    made_again = overlays.squares_of(ours)[A]
    made_again.unlink()  # a new file, as an update writes it: in place, a hard link changes too
    made_again.write_bytes(b"ours, made again")
    stray = out / "Earth nav data" / "+40+000" / ".live-tmp-1-2-+45+005.dsf"
    stray.write_bytes(b"")
    assert overlays.build(xp, pack, out, [], root, {B: "zOrthoStudio"}, log=said.append) == 2
    assert sorted(overlays.squares_of(out)) == [A, C]
    assert overlays.squares_of(out)[A].read_bytes() == b"ours, made again"
    assert not stray.exists()
    assert "(1 placed and 3 taken away since the last start)" in said[-1]  # A; B, A's old, stray


def test_the_map_of_osxp_s_regions() -> None:
    """Every square of OrthoStudio XP's mesh is in its map; the open
    sea and Antarctica are in none."""
    from orthostudio.live.meshmap import REGION_NAMES, REGION_NAMES_FR, region_of, squares_of

    assert region_of(35.5, 139.8) == "asi"  # Haneda
    assert region_of(46.2, 6.1) == "eur"  # Geneva
    assert region_of(45.5, -122.6) == "na"  # Portland: no AutoOrtho region had it
    assert region_of(30.5, -40.5) is None  # the open Atlantic
    assert region_of(-80.5, 0.5) is None  # Antarctica
    assert set(REGION_NAMES) == set(REGION_NAMES_FR) == {
        "eur", "na", "sa", "afr", "asi", "aus_pac"
    }  # fmt: skip
    assert region_of(47.5, -122.3) == "na"  # Seattle: AutoOrtho's map had it in Europe
    assert region_of(64.1, -21.9) == "eur"  # Reykjavik
    assert region_of(72.5, -40.5) == "na"  # Greenland's ice cap
    assert region_of(19.5, -155.5) == "aus_pac"  # Hawaii
    total = sum(len(v) for v in squares_of(set(REGION_NAMES)).values())
    assert total == 19_295  # the six regions
