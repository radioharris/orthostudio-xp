# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Every AutoOrtho region installed in one pack: Europe, North America... set up once, a region
added later merged in without losing what was built. The DSFs from the start, the files they name
per square round the aircraft (orthostudio.live.names)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from live_helpers import NAMES, make_source, make_squares, make_xplane
from orthostudio.live import aoregions, session
from orthostudio.live.filesystem import is_folder_link
from orthostudio.live.names import RegionTextures, region_list_file
from orthostudio.live.pack import live_root
from orthostudio.live.region import (
    REGION_MARKER,
    neutral_file,
    setup_region,
    sources_of,
    sources_of_pack,
)
from orthostudio.live.scenery import default_sources

OTHERS = ("23312_33152_BI16", "23312_33168_BI16")  # another region's textures


def quiet(_: str) -> None:
    pass


def two_regions(root: Path) -> tuple[Path, Path]:
    # both DSFs in the same ten-degree folder: merged file by file there
    eur = make_source(root / "eur", tile=(45, 6), textures=NAMES)
    na = make_source(root / "na", tile=(46, 2), textures=OTHERS)
    return eur, na


def test_two_regions_in_one_pack(tmp_path: Path) -> None:
    eur, na = two_regions(tmp_path)
    pack = tmp_path / "pack"
    meta = setup_region([eur, na], pack, log=quiet)
    assert sources_of(meta) == [str(eur), str(na)]
    assert len(RegionTextures(sources_of_pack(pack))) == 4
    nav = pack / "Earth nav data" / "+40+000"
    assert (nav / "+45+006.dsf").exists() and (nav / "+46+002.dsf").exists()
    # the files the DSFs name: none until their squares are made, round the aircraft
    assert list((pack / "terrain").iterdir()) == []
    assert [p.name for p in (pack / "textures").iterdir()] == ["water_transition.png"]
    make_squares(pack)
    for name in (*NAMES, *OTHERS):
        assert (pack / "terrain" / f"{name}.ter").exists()
        assert (pack / "terrain" / f"{name}_water_overlay.ter").exists()
        assert (pack / "textures" / f"{name}.dds").exists()
    assert session.region_ready(pack, [na, eur])  # in any order
    assert not session.region_ready(pack, [eur])


def test_two_regions_each_with_its_list(tmp_path: Path) -> None:
    """Europe and North America in one pack, each region's textures kept in a list of its own:
    North America added later is the only one listed then, each texture with its region. Both
    folders have one name here (z_ao_test), as AutoOrtho's and Live's copies of a region may."""
    eur, na = two_regions(tmp_path)
    pack, lists = tmp_path / "pack", tmp_path / "lists"
    setup_region(eur, pack, log=quiet)
    assert len(RegionTextures(sources_of_pack(pack), lists=lists)) == 2
    setup_region([eur, na], pack, log=quiet)
    both = RegionTextures(sources_of_pack(pack), lists=lists)
    assert both.listed == [na] and len(both) == 4  # Europe read from its list
    again = RegionTextures(sources_of_pack(pack), lists=lists)
    assert again.listed == [] and list(again.entries()) == list(both.entries())
    regions = {entry[0].removesuffix(".dds"): entry[5] for entry in again.entries()}
    assert regions == {**dict.fromkeys(NAMES, 0), **dict.fromkeys(OTHERS, 1)}
    assert region_list_file(lists, eur) != region_list_file(lists, na)


def live_region(root: Path, region: str, textures: tuple[str, ...]) -> Path:
    """A region Live installed in its folder, as its install leaves it, its marker last."""
    mesh = aoregions.mesh_dir(root, region)
    (mesh / "terrain").mkdir(parents=True)
    for name in textures:
        (mesh / "terrain" / f"{name}.ter").write_text("A\n800\nTERRAIN\n")
    (mesh / aoregions.MARKER).write_text(json.dumps({"id": region, "release": "0.0.51"}))
    return mesh


def test_the_regions_are_read_into_their_lists_before_any_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows setup and Live's window read each region a start would use into its list:
    Live's own, and AutoOrtho's where X-Plane is known; each once."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    root = live_root()
    mine = live_region(root, "eur", NAMES)
    xp = tmp_path / "X-Plane 12"
    theirs = xp / "Custom Scenery" / "z_autoortho" / "scenery" / "z_ao_na"
    (theirs / "terrain").mkdir(parents=True)
    for name in OTHERS:
        (theirs / "terrain" / f"{name}.ter").write_text("A\n800\nTERRAIN\n")
    lines: list[str] = []
    session.keep_region_lists(None, lines.append)  # no X-Plane: Live's own regions alone
    assert len(lines) == 1 and lines[0].startswith("z_ao_eur: 2 textures listed in ")
    lines.clear()
    session.keep_region_lists(xp, lines.append)
    assert len(lines) == 1 and lines[0].startswith("z_ao_na: 2 textures listed in ")
    lines.clear()
    session.keep_region_lists(xp, lines.append)
    assert lines == []  # every region read: a start reads lists only
    lists = aoregions.lists_dir(root)
    assert region_list_file(lists, mine).exists() and region_list_file(lists, theirs).exists()


def test_a_region_added_later_keeps_what_is_built(tmp_path: Path) -> None:
    eur, na = two_regions(tmp_path)
    pack = tmp_path / "pack"
    setup_region(eur, pack, log=quiet)
    built = pack / "textures" / f"{NAMES[0]}.dds"
    built.write_bytes(b"built for real")
    lines: list[str] = []
    meta = setup_region([eur, na], pack, log=lines.append)
    assert any(line.startswith("adding ") for line in lines)  # merged in, not set up again
    assert built.read_bytes() == b"built for real"
    make_squares(pack)
    assert built.read_bytes() == b"built for real"  # a texture built is never made a name
    other = pack / "textures" / f"{OTHERS[0]}.dds"
    assert os.path.samefile(other, pack / neutral_file(0))  # a name of the neutral texture
    assert sources_of(meta) == [str(eur), str(na)]
    assert (pack / REGION_MARKER).exists()
    lines.clear()
    setup_region([na, eur], pack, log=lines.append)
    assert lines == []  # nothing to do


def test_the_regions_autoortho_installed(tmp_path: Path) -> None:
    xp = tmp_path / "X-Plane 12"
    scenery = xp / "Custom Scenery" / "z_autoortho" / "scenery"
    for region in ("z_ao_na", "z_ao_eur"):
        (scenery / region / "terrain").mkdir(parents=True)
    (scenery / "z_ao_half").mkdir()  # still downloading: no terrain yet
    (scenery / "other").mkdir()
    # a Live folder of the test's own, empty: not the computer's, where Live may have installed
    # regions (the user's Shadow PC had Europe there, 2026-10-02)
    live = tmp_path / "live"
    assert [p.name for p in default_sources(xp, live)] == ["z_ao_eur", "z_ao_na"]
    assert default_sources(tmp_path / "nowhere", live) == []


def test_lives_own_mesh_comes_before_autoortho_s(tmp_path: Path) -> None:
    """``regions/z_osxp_eur`` first: a square both have is Live's own;
    one half unpacked (no marker yet) is left out."""
    from orthostudio.live.aoregions import MARKER, regions_dir

    xp = tmp_path / "X-Plane 12"
    (xp / "Custom Scenery" / "z_autoortho" / "scenery" / "z_ao_eur" / "terrain").mkdir(parents=True)
    live = tmp_path / "live"
    ours = regions_dir(live) / "z_osxp_eur"
    (ours / "terrain").mkdir(parents=True)
    assert [p.name for p in default_sources(xp, live)] == ["z_ao_eur"]
    (ours / MARKER).write_text("{}")
    assert [p.name for p in default_sources(xp, live)] == ["z_osxp_eur", "z_ao_eur"]


def test_the_page_lists_lives_own_regions_with_their_size(tmp_path: Path) -> None:
    """The window's list of OrthoStudio XP's regions: one here, one on another disk linked from
    Live's folder; the size its marker holds; one half unpacked (no marker) left out."""
    from orthostudio.live.aoregions import MARKER, regions_dir
    from orthostudio.live.control import our_regions

    live = tmp_path / "live"
    here = regions_dir(live) / "z_osxp_eur"
    (here / "terrain").mkdir(parents=True)
    (here / MARKER).write_text(json.dumps({"release": "osxp-local", "size": 38_000_000_000}))
    other = tmp_path / "other disk" / "z_osxp_na"
    (other / "terrain").mkdir(parents=True)
    (other / MARKER).write_text(json.dumps({"release": "osxp-local"}))
    os.symlink(other, regions_dir(live) / "z_osxp_na")
    (regions_dir(live) / "z_osxp_asi" / "terrain").mkdir(parents=True)
    assert our_regions(live) == [
        {
            "id": "eur",
            "size": 38_000_000_000,
            "elsewhere": False,
            "folder": str(regions_dir(live).resolve()),
        },
        {"id": "na", "size": None, "elsewhere": True, "folder": str(other.parent.resolve())},
    ]


def test_the_mesh_is_placed_file_by_file(tmp_path: Path) -> None:
    """Many short clones, not one call for the whole tree: 684,000 terrain files in one call held
    the disk 53 s and froze other programs (2026-09-28). The DSFs at the setup; the terrain files
    with their squares."""
    eur = make_source(tmp_path / "eur", tile=(45, 6), textures=NAMES)
    pack = tmp_path / "pack"
    lines: list[str] = []
    setup_region(eur, pack, log=lines.append)
    for folder in ("terrain", "Earth nav data"):
        assert not (pack / folder).is_symlink()
    assert any("Earth nav data:" in line and "files placed" in line for line in lines)
    make_squares(pack)
    ter = pack / "terrain" / f"{NAMES[0]}.ter"
    assert not ter.is_symlink()
    assert ter.read_text() == (eur / "terrain" / f"{NAMES[0]}.ter").read_text()


def _another_disk(monkeypatch, sources: tuple[Path, ...]) -> None:
    """The pack on another disk than ``sources``: no clone nor hard link from them crosses."""
    import errno

    from orthostudio.live import filesystem, region

    real_clone, real_link = filesystem.clone, os.link

    def across(src) -> bool:
        return any(str(src).startswith(str(s)) for s in sources)

    def no_clone_across(src: str, dst: str) -> None:
        if across(src):
            raise OSError(errno.EXDEV, "cross-device", dst)
        real_clone(src, dst)

    def no_link_across(src, dst, *args, **kwargs) -> None:
        if across(src):
            raise OSError(errno.EXDEV, "cross-device", str(dst))
        real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(region, "same_disk", lambda a, b: not across(a))
    monkeypatch.setattr(filesystem, "clone", no_clone_across)
    monkeypatch.setattr(os, "link", no_link_across)


def test_a_pack_on_another_disk_links_the_mesh(tmp_path: Path, monkeypatch) -> None:
    """Live's folder on another disk than AutoOrtho's region (an external disk): the DSFs are
    linked, X-Plane reads them from there; the files they name are real files on Live's disk,
    copies of the small ones, the textures Live's own."""
    eur = make_source(tmp_path / "eur", tile=(45, 6), textures=NAMES)
    _another_disk(monkeypatch, (eur,))
    pack = tmp_path / "pack"
    lines: list[str] = []
    setup_region(eur, pack, log=lines.append)
    nav = pack / "Earth nav data"
    assert is_folder_link(nav) and nav.resolve() == (eur / "Earth nav data").resolve()
    assert not (pack / "terrain").is_symlink()
    shared = pack / "textures" / "water_transition.png"
    assert shared.is_file() and not shared.is_symlink()  # copied: small
    assert any("linked to" in line for line in lines)
    assert any("copied from another disk" in line for line in lines)
    make_squares(pack)
    ter = pack / "terrain" / f"{NAMES[0]}.ter"
    assert ter.is_file() and not ter.is_symlink()  # X-Plane finds ../textures beside it
    texture = pack / "textures" / f"{NAMES[0]}.dds"
    assert os.path.samefile(texture, pack / neutral_file(0))  # Live's own, on Live's disk


def test_a_region_added_on_another_disk_writes_nothing_into_the_first(
    tmp_path: Path, monkeypatch
) -> None:
    """A second region merged into a linked mesh: the links made real folders of links first, so
    that nothing is written into the first region's own folders."""
    eur, na = two_regions(tmp_path)
    _another_disk(monkeypatch, (eur, na))
    pack = tmp_path / "pack"
    setup_region(eur, pack, log=quiet)
    before = sorted(p.relative_to(eur) for p in eur.rglob("*"))
    setup_region([eur, na], pack, log=quiet)
    make_squares(pack)
    assert sorted(p.relative_to(eur) for p in eur.rglob("*")) == before  # AO's folder untouched
    assert not (pack / "Earth nav data").is_symlink()  # a real folder of links now
    nav = pack / "Earth nav data" / "+40+000"
    assert (nav / "+45+006.dsf").exists() and (nav / "+46+002.dsf").exists()
    for name in (*NAMES, *OTHERS):
        ter = pack / "terrain" / f"{name}.ter"
        assert ter.is_file() and not ter.is_symlink()


def test_a_region_changed_under_the_pack_sets_it_up_again(tmp_path: Path) -> None:
    """The pack takes the files its DSFs name from the region while Live streams: a region
    installed again or updated must not leave DSFs of before beside .ter files of now. A start
    with X-Plane running on it is refused before (test_no_start_on_a_region_updated_while_xplane_
    runs)."""
    import os
    import time

    eur = make_source(tmp_path / "eur", tile=(45, 6), textures=NAMES)
    pack = tmp_path / "pack"
    setup_region(eur, pack, log=quiet)
    lines: list[str] = []
    setup_region(eur, pack, log=lines.append)
    assert lines == []  # nothing changed: nothing to do
    time.sleep(0.01)
    (eur / "terrain" / "extra.ter").write_text("A\n800\nTERRAIN\n")  # an update
    os.utime(eur / "terrain")
    setup_region(eur, pack, log=lines.append)
    assert any("changed since the pack was set up" in line for line in lines)
    assert any("Earth nav data:" in line for line in lines)  # its DSFs placed again
    lines.clear()
    setup_region(eur, pack, log=lines.append)
    assert lines == []


def test_no_start_without_the_region_the_pack_is_made_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A region taken away: the pack once held every file its DSFs name; now they are made from
    the region while Live streams, so Live says so rather than fly without them."""
    import shutil

    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    xp = make_xplane(tmp_path)
    src = make_source(tmp_path, tile=(45, 6), textures=NAMES)
    options = session.Options(xplane=xp, pack=tmp_path / "pack", source=src, no_plugin=True)
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    from orthostudio.live import winsearch
    from orthostudio.live.pack import live_root

    kept_out: list[Path] = []
    monkeypatch.setattr(winsearch, "keep_out", lambda folder, log=None: kept_out.append(folder))
    session.prepare(options, log=quiet)
    assert kept_out == [live_root()]  # Live's folder out of Windows Search, at every start
    shutil.rmtree(src / "terrain")
    with pytest.raises(session.RefusedError, match="gone or incomplete"):
        session.prepare(options, log=quiet)


def test_no_start_while_the_plugin_s_port_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another Live, or Windows keeping the port (Hyper-V, WSL): Live does not stream without its
    gate, since the plugin, finding nobody on the port as X-Plane starts, would take Live's
    scenery out of X-Plane."""
    from orthostudio.live import gate, plugin

    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    xp = make_xplane(tmp_path)
    src = make_source(tmp_path, tile=(45, 6), textures=NAMES)
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    monkeypatch.setattr(plugin, "install", lambda xplane, log: True)

    def taken(*args, **kwargs):
        raise PermissionError(10013, "access forbidden by its access permissions")

    monkeypatch.setattr(gate, "Gate", taken)
    with pytest.raises(session.RefusedError, match=r"UDP 49250\) is taken"):
        session.prepare(session.Options(xplane=xp, pack=tmp_path / "pack", source=src), log=quiet)


def test_a_region_put_before_the_others_takes_their_squares(tmp_path: Path) -> None:
    """Live's own mesh comes first (scenery.osxp_regions): added in front of a pack set up from
    AutoOrtho's region, the pack is set up again and its DSFs are the new region's; merged in,
    the DSFs of before stayed, naming terrain files the new region lacks (X-Plane refused the
    pack, 2026-10-18)."""
    theirs = make_source(tmp_path / "theirs", tile=(46, 2))
    ours = make_source(tmp_path / "ours", tile=(46, 2))
    dsf = Path("Earth nav data") / "+40+000" / "+46+002.dsf"
    (ours / dsf).write_bytes((ours / dsf).read_bytes() + b"ours")
    pack = tmp_path / "pack"
    setup_region(theirs, pack, log=quiet)
    lines: list[str] = []
    meta = setup_region([ours, theirs], pack, log=lines.append)
    assert not any(line.startswith("adding ") for line in lines)
    assert sources_of(meta) == [str(ours), str(theirs)]
    assert (pack / dsf).read_bytes() == (ours / dsf).read_bytes()


def test_a_new_decal_makes_the_pack_s_terrain_files_again(tmp_path: Path) -> None:
    """The .ter files made with another decal go at a start, to be made again as X-Plane comes;
    the same decal keeps them; X-Plane running keeps them until the next start."""
    from orthostudio.live.region import DECAL_RECORD, set_decal

    pack = tmp_path / "pack"
    terrain = pack / "terrain"
    terrain.mkdir(parents=True)
    (terrain / "a.ter").write_text("DECAL_LIB lib/g10/decals/maquify_2_green_key.dcl\n")
    said: list[str] = []
    set_decal(pack, "grass_and_stony_dirt_1.dcl", log=said.append, xplane_running=lambda: True)
    assert (terrain / "a.ter").exists() and not (pack / DECAL_RECORD).exists()
    set_decal(pack, "grass_and_stony_dirt_1.dcl", log=said.append)
    assert not (terrain / "a.ter").exists() and terrain.is_dir()
    assert json.loads((pack / DECAL_RECORD).read_text()) == {
        "decal": "grass_and_stony_dirt_1.dcl",
        "on_water": True,
    }
    (terrain / "b.ter").write_text("made since\n")
    set_decal(pack, "grass_and_stony_dirt_1.dcl", log=said.append)
    assert (terrain / "b.ter").exists()
    set_decal(pack, None, log=said.append)  # the regions' own decals: nothing done
    assert (terrain / "b.ter").exists()
    set_decal(pack, "", log=said.append)
    assert not (terrain / "b.ter").exists()
    assert json.loads((pack / DECAL_RECORD).read_text()) == {"decal": "", "on_water": True}


def test_a_pack_made_before_the_water_took_the_decal_makes_its_terrain_files_again(
    tmp_path: Path,
) -> None:
    """A record of before the photo on the water took the decal makes the .ter files again once, at
    a start with X-Plane not running; with no decal chosen there is nothing to add, nothing done."""
    from orthostudio.live.region import DECAL_RECORD, set_decal

    pack = tmp_path / "pack"
    terrain = pack / "terrain"
    terrain.mkdir(parents=True)
    (terrain / "a.ter").write_text("made before\n")
    (pack / DECAL_RECORD).parent.mkdir(parents=True)
    (pack / DECAL_RECORD).write_text(json.dumps({"decal": "maquify_2_green_key.dcl"}))
    set_decal(pack, "maquify_2_green_key.dcl", log=lambda _: None, xplane_running=lambda: True)
    assert (terrain / "a.ter").exists()
    set_decal(pack, "maquify_2_green_key.dcl", log=lambda _: None)
    assert not (terrain / "a.ter").exists()
    (terrain / "b.ter").write_text("made since\n")
    set_decal(pack, "maquify_2_green_key.dcl", log=lambda _: None)
    assert (terrain / "b.ter").exists()
    (pack / DECAL_RECORD).write_text(json.dumps({"decal": ""}))
    set_decal(pack, "", log=lambda _: None)
    assert (terrain / "b.ter").exists()


def test_live_s_decal_is_one_of_osxp_s() -> None:
    from orthostudio.live.settings import LIVE_DECAL, LiveSettings

    assert LiveSettings().decal == LIVE_DECAL == "maquify_2_green_key.dcl"
    assert LiveSettings(decal="").decal == ""
    with pytest.raises(ValueError):
        LiveSettings(decal="rail_dry_grd.dcl")


def test_a_set_up_stopped_midway_does_not_refuse_the_next(tmp_path: Path) -> None:
    """``Earth nav data.old`` left by a set-up stopped while taking it away (a Ctrl-C) goes
    first: renamed onto, it refused the start with "Directory not empty"."""
    from orthostudio.live.region import _place_dir

    src = tmp_path / "region" / "Earth nav data" / "+40+000"
    src.mkdir(parents=True)
    (src / "+45+005.dsf").write_bytes(b"XPLNEDSF")
    pack = tmp_path / "pack"
    dst = pack / "Earth nav data"
    (dst / "+40+000").mkdir(parents=True)
    (dst / "+40+000" / "+45+005.dsf").write_bytes(b"before")
    left = pack / "Earth nav data.old" / "+30+000"
    left.mkdir(parents=True)
    (left / "+35+005.dsf").write_bytes(b"left")
    _place_dir(src.parent, dst, log=lambda _: None)
    assert not (pack / "Earth nav data.old").exists()
    assert (dst / "+40+000" / "+45+005.dsf").read_bytes() == b"XPLNEDSF"


def test_a_region_updated_drops_the_textures_no_square_names_any_more(tmp_path: Path) -> None:
    """An update of a region names some textures no more: set up again, the pack deletes those it
    built, with their night textures and water photos, and forgets them in its record, so that
    they no longer take Live's space; night textures of no texture go too, and every water photo,
    made again with its square, is forgotten with it."""
    import time

    from orthostudio.live.names import night_name
    from orthostudio.live.pack import load_built, save_built
    from orthostudio.live.region import NIGHT_FOLDER
    from orthostudio.live.waterphoto import water_name

    eur = make_source(tmp_path / "eur", tile=(46, 2), textures=NAMES)  # the textures' square
    pack = tmp_path / "pack"
    setup_region(eur, pack, log=quiet)
    textures, night = pack / "textures", pack / NIGHT_FOLDER
    night.mkdir(exist_ok=True)
    kept, gone, orphan = (f"{n}.dds" for n in (*NAMES, "23296_33184_BI16"))
    for name in (kept, gone):
        (textures / name).write_bytes(b"built")
        (textures / water_name(name)).write_bytes(b"water")
        (night / night_name(name)).write_bytes(b"lit")
    (night / night_name(orphan)).write_bytes(b"lit, its texture long gone")
    save_built(
        pack,
        {
            "textures": {kept: 0, gone: 0},
            "seen": {kept: 1, gone: 1},
            "lit": {kept: 2, gone: 2, orphan: 2},
            "water": {kept: 1, gone: 1},
            "patchy": [gone],
            "copied": [gone],
        },
    )
    time.sleep(0.01)
    for ter in (f"{NAMES[1]}.ter", f"{NAMES[1]}_water_overlay.ter"):  # the update: one gone
        (eur / "terrain" / ter).unlink()
    os.utime(eur / "terrain")
    lines: list[str] = []
    setup_region(eur, pack, log=lines.append, lists=tmp_path / "lists")
    assert (textures / kept).exists() and (night / night_name(kept)).exists()
    assert not (textures / gone).exists() and not (night / night_name(gone)).exists()
    assert not (night / night_name(orphan)).exists()
    assert not (textures / water_name(kept)).exists()  # made again with its square
    meta = load_built(pack)
    assert meta["textures"] == {kept: 0} and meta["seen"] == {kept: 1}
    assert meta["lit"] == {kept: 2} and meta["water"] == {}
    assert meta["patchy"] == [] and meta["copied"] == []
    assert any("no square names any more" in line for line in lines)


def test_a_region_away_for_a_while_loses_nothing(tmp_path: Path) -> None:
    """A region only away (another tool's mesh on a disk unplugged): the pack is set up again
    without it, and the textures built on its squares stay, recorded, for its return."""
    from orthostudio.live.pack import load_built, save_built

    eur = make_source(tmp_path / "eur", tile=(46, 2), textures=NAMES)
    other = make_source(tmp_path / "other", tile=(45, 6), textures=("23968_33424_BI16",))
    pack = tmp_path / "pack"
    setup_region([eur, other], pack, log=quiet)
    kept = pack / "textures" / "23968_33424_BI16.dds"
    kept.write_bytes(b"built")
    save_built(pack, {"textures": {kept.name: 0}, "seen": {kept.name: 1}})
    setup_region([eur], pack, log=quiet)  # the other one's disk unplugged
    assert kept.read_bytes() == b"built"
    assert load_built(pack)["textures"] == {kept.name: 0}


def test_the_shared_files_follow_their_region(tmp_path: Path) -> None:
    """``water_transition.png`` and the other files every square may name: placed again where an
    update of the region brought another, the first region's where two have one."""
    import time

    eur = make_source(tmp_path / "eur", tile=(45, 6), textures=NAMES)
    na = make_source(tmp_path / "na", tile=(46, 2), textures=OTHERS)
    (na / "textures" / "water_transition.png").write_bytes(b"\x89PNG of the second region")
    pack = tmp_path / "pack"
    setup_region([eur, na], pack, log=quiet)
    shared = pack / "textures" / "water_transition.png"
    assert shared.read_bytes() == b"\x89PNG fake"
    time.sleep(0.01)
    new = eur / "textures" / "water_transition.png.new"
    new.write_bytes(b"\x89PNG of the update")
    os.replace(new, eur / "textures" / "water_transition.png")  # unpacked, a file of its own
    os.utime(eur / "terrain")
    setup_region([eur, na], pack, log=quiet)
    assert shared.read_bytes() == b"\x89PNG of the update"


def test_no_start_on_a_region_updated_while_xplane_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A start first puts each update waiting whole in place, X-Plane not running; with X-Plane
    running it leaves them waiting, and a region that changed since the pack was set up refuses
    the start, as a new zoom does: X-Plane may be reading the region of before."""
    import time

    from orthostudio.live import osxpregions, winsearch
    from orthostudio.live.filesystem import folder_link

    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    xp = make_xplane(tmp_path)
    src = make_source(tmp_path, tile=(45, 6), textures=NAMES)
    pack = tmp_path / "pack"
    options = session.Options(xplane=xp, pack=pack, source=src, no_plugin=True)
    monkeypatch.setattr(winsearch, "keep_out", lambda folder, log=None: None)
    settled: list[bool] = []
    monkeypatch.setattr(
        osxpregions, "settle", lambda root, log, swap, only=None: settled.append(swap)
    )
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    session.prepare(options, log=quiet)
    assert settled == [True]  # X-Plane not running: the updates put in place first
    link = xp / "Custom Scenery" / "z_osxp_live"
    if not is_folder_link(link):
        folder_link(pack, link)  # as Live links it here
    monkeypatch.setattr(session, "is_installed", lambda *a: True)
    monkeypatch.setattr(session, "xplane_running", lambda: True)
    session.prepare(options, log=quiet)  # nothing changed: it may run
    assert settled == [True, False]
    time.sleep(0.01)
    (src / "terrain" / "extra.ter").write_text("A\n800\nTERRAIN\n")  # an update
    os.utime(src / "terrain")
    with pytest.raises(session.RefusedError, match="changed since Live last started"):
        session.prepare(options, log=quiet)
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    session.prepare(options, log=quiet)  # X-Plane quit: the pack set up again
    monkeypatch.setattr(session, "xplane_running", lambda: True)
    session.prepare(options, log=quiet)


def test_a_region_updated_and_one_added_before_a_start(tmp_path: Path) -> None:
    """Europe's update waited while X-Plane ran, North America installed meanwhile: the next start
    puts the update in place and adds the new region. The pack is set up again whole, never the
    new region merged alone beside Europe's DSFs of before."""
    import shutil

    regions = tmp_path / "regions"
    regions.mkdir()
    eur = make_source(tmp_path / "a", tile=(45, 6)).rename(regions / "z_osxp_eur")
    pack = tmp_path / "pack"
    setup_region([eur], pack, log=quiet)
    dsf = "Earth nav data/+40+000/+45+006.dsf"
    update = make_source(tmp_path / "b", tile=(45, 6))
    (update / dsf).write_bytes(b"the update's DSF")
    eur.rename(regions / ".z_osxp_eur.old")  # the update put in place
    update.rename(eur)
    shutil.rmtree(regions / ".z_osxp_eur.old")
    os.utime(eur / "Earth nav data", (2_000_000_000, 2_000_000_000))
    na = make_source(tmp_path / "c", tile=(46, 7)).rename(regions / "z_osxp_na")
    lines: list[str] = []
    setup_region([eur, na], pack, log=lines.append)
    assert (pack / dsf).read_bytes() == b"the update's DSF"
    assert (pack / "Earth nav data/+40+000/+46+007.dsf").exists()
    assert any("changed since the pack was set up" in line for line in lines)


def test_the_mesh_setting_streams_each_region_s_kept_old_mesh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """« Mesh: old », for every region at once: each region's kept old mesh in its place where it
    has one, the new mesh elsewhere (North America keeps none); « new », the regions installed.
    The kept mesh is Live's own mesh alike, with a list of textures of its own."""
    from orthostudio.live import osxpregions
    from orthostudio.live.aoregions import MARKER, regions_dir
    from orthostudio.live.names import own_mesh
    from orthostudio.live.settings import LiveSettings, save_settings

    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    live = tmp_path / "live"
    xp = tmp_path / "X-Plane 12"
    folders = {}
    for name in ("z_osxp_eur", "z_osxp_na", ".z_osxp_eur.kept"):
        folder = regions_dir(live) / name
        (folder / "terrain-lists").mkdir(parents=True)
        (folder / MARKER).write_text("{}")
        folders[name] = folder
    eur, na, kept = folders["z_osxp_eur"], folders["z_osxp_na"], folders[".z_osxp_eur.kept"]
    assert osxpregions.kept_mesh(live, "eur") == kept
    assert default_sources(xp, live) == [eur, na]  # new, the default
    save_settings(LiveSettings(mesh="old"))
    assert default_sources(xp, live) == [kept, na]
    assert own_mesh(kept) and region_list_file(tmp_path, kept) != region_list_file(tmp_path, eur)
    (kept / MARKER).unlink()  # not whole: the new one
    assert default_sources(xp, live) == [eur, na]
    save_settings(LiveSettings(mesh="new"))
    (kept / MARKER).write_text("{}")
    assert default_sources(xp, live) == [eur, na]


def test_no_start_on_a_mesh_changed_while_xplane_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pack made from one folder, the start reading the region from another (the Settings'
    Mesh switched to a kept old mesh, or back, or the disk of a mesh not connected): refused with
    X-Plane running, as a zoom changed is, saying the case in plain words, the pack never set up
    again under it; with X-Plane closed, set up again. A region only added after the others is
    merged in, as before; one put first sets the pack up again."""
    from orthostudio.live import osxpregions, winsearch
    from orthostudio.live.filesystem import folder_link
    from orthostudio.live.region import pack_to_redo

    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    xp = make_xplane(tmp_path)
    new = make_source(tmp_path / "new", tile=(45, 6), textures=NAMES)
    old = make_source(tmp_path / "old", tile=(45, 6), textures=NAMES)
    pack = tmp_path / "pack"
    monkeypatch.setattr(winsearch, "keep_out", lambda folder, log=None: None)
    monkeypatch.setattr(osxpregions, "settle", lambda root, log, swap, only=None: None)
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    session.prepare(session.Options(xplane=xp, pack=pack, source=new, no_plugin=True), log=quiet)
    assert not pack_to_redo([new], pack) and not pack_to_redo([new, old], pack)  # added: merged
    assert pack_to_redo([old], pack) and pack_to_redo([old, new], pack)  # another comes first
    link = xp / "Custom Scenery" / "z_osxp_live"
    if not is_folder_link(link):
        folder_link(pack, link)
    monkeypatch.setattr(session, "is_installed", lambda *a: True)
    monkeypatch.setattr(session, "xplane_running", lambda: True)
    switched = session.Options(xplane=xp, pack=pack, source=old, no_plugin=True)
    with pytest.raises(session.RefusedError, match="the mesh changed since Live last started") as e:
        session.prepare(switched, log=quiet)
    said = str(e.value)
    assert "a region was installed, updated or deleted" in said.lower()
    assert "Mesh was switched between New and Old" in said
    assert "a disk holding a mesh is not connected" in said and "z_ao_test" not in said
    assert sources_of_pack(pack) == [new]  # the pack as X-Plane found it
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    session.prepare(switched, log=quiet)  # X-Plane closed: set up again from the old mesh
    assert sources_of_pack(pack) == [old]
