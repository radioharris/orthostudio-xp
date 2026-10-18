# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The user's own tiles first: where a pack above Live's draws the ground (a tile OrthoStudio XP
built, one imported from Ortho4XP, any other ortho), X-Plane never reads Live's textures, so Live
downloads nothing there and a start there waits for none of them."""

from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from live_helpers import NAMES, FakeDownloader, atom, make_source, make_xplane
from orthostudio.install.scenery_packs import IMPORTED_PACK_PREFIX, ORTHO_PREFIX
from orthostudio.live import session
from orthostudio.live.cache import ChunkCache
from orthostudio.live.covered import covered_squares, dsf_properties, is_overlay, own_squares
from orthostudio.live.geo import parse_texture_name
from orthostudio.live.pack import LINK_NAME
from orthostudio.live.region import setup_region
from orthostudio.live.streamer import Fix, Streamer

STRADDLING = "23296_33312_BI16"  # from 2.99 to 3.08 degrees east: in two squares


def dsf(props: dict[str, str]) -> bytes:
    prop = b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in props.items())
    head = atom(b"HEAD", atom(b"PROP", prop))
    return b"XPLNEDSF" + struct.pack("<i", 1) + head + atom(b"DEFN", b"") + b"\0" * 16


def packed(path: Path, data: bytes) -> None:
    """``data`` as X-Plane's own scenery keeps a DSF: in a 7z archive of that name."""
    import py7zr

    plain = path.with_suffix(".plain")
    plain.write_bytes(data)
    with py7zr.SevenZipFile(path, "w") as archive:
        archive.write(plain, arcname=path.name)
    plain.unlink()


def put_dsf(pack: Path, square: tuple[int, int], data: bytes, zipped: bool = False) -> Path:
    lat, lon = square
    folder = (
        pack
        / "Earth nav data"
        / f"{math.floor(lat / 10) * 10:+03d}{math.floor(lon / 10) * 10:+04d}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{lat:+03d}{lon:+04d}.dsf"
    if zipped:
        packed(path, data)
    else:
        path.write_bytes(data)
    return path


def test_the_properties_of_a_dsf(tmp_path: Path) -> None:
    ground = put_dsf(tmp_path / "a", (45, 5), dsf({"sim/west": "5", "sim/south": "45"}))
    over = put_dsf(tmp_path / "b", (45, 5), dsf({"sim/overlay": "1", "sim/west": "5"}))
    zipped = put_dsf(tmp_path / "c", (45, 5), dsf({"sim/overlay": "1"}), zipped=True)
    junk = tmp_path / "junk.dsf"
    junk.write_bytes(b"not a DSF")
    assert dsf_properties(ground) == {"sim/west": "5", "sim/south": "45"}
    assert not is_overlay(ground)
    assert is_overlay(over)
    assert is_overlay(zipped)
    assert dsf_properties(junk) == {}


def test_the_squares_the_users_own_tiles_cover(tmp_path: Path) -> None:
    xp = tmp_path / "X-Plane 12"
    scenery = xp / "Custom Scenery"
    lines = [
        "SCENERY_PACK Custom Scenery/LFLL Lyon/",
        "SCENERY_PACK *GLOBAL_AIRPORTS*",
        "SCENERY_PACK Custom Scenery/yOrthoStudio_Overlays/",
        f"SCENERY_PACK Custom Scenery/{ORTHO_PREFIX}+45+005/",
        f"SCENERY_PACK_DISABLED Custom Scenery/{IMPORTED_PACK_PREFIX}+46+005/",
        "SCENERY_PACK Custom Scenery/Photo Lyon/",
        f"SCENERY_PACK Custom Scenery/{LINK_NAME}/",
        f"SCENERY_PACK Custom Scenery/{IMPORTED_PACK_PREFIX}+47+005/",
        "SCENERY_PACK Custom Scenery/z_ao_eur/",
    ]
    scenery.mkdir(parents=True)
    (scenery / "scenery_packs.ini").write_text(
        "I\n1000 Version\nSCENERY\n\n" + "\n".join(lines) + "\n"
    )
    # an airport, zipped as its DSF may be, and OrthoStudio XP's overlays: over the ground only
    put_dsf(scenery / "LFLL Lyon", (45, 5), dsf({"sim/overlay": "1"}), zipped=True)
    put_dsf(scenery / "yOrthoStudio_Overlays", (44, 4), dsf({"sim/overlay": "1"}))
    # a tile OrthoStudio XP built: ground, whatever its DSF holds
    put_dsf(scenery / f"{ORTHO_PREFIX}+45+005", (45, 5), b"not read")
    put_dsf(scenery / f"{IMPORTED_PACK_PREFIX}+46+005", (46, 5), b"disabled")
    put_dsf(scenery / "Photo Lyon", (44, 5), dsf({"sim/west": "5"}))  # another ortho, read
    put_dsf(scenery / LINK_NAME, (45, 6), dsf({"sim/west": "6"}))  # Live's own
    put_dsf(scenery / f"{IMPORTED_PACK_PREFIX}+47+005", (47, 5), b"below Live's")
    assert covered_squares(xp, LINK_NAME) == {
        (45, 5): f"{ORTHO_PREFIX}+45+005",
        (44, 5): "Photo Lyon",
    }
    assert covered_squares(xp, LINK_NAME, region={(44, 5)}) == {(44, 5): "Photo Lyon"}


def test_the_users_tiles_while_live_is_stopped(tmp_path: Path) -> None:
    """Live's pack out of X-Plane's list (Live stopped): its tiles are those above where
    OrthoStudio XP would list it, below the tiles it built; the list on the disk is left alone."""
    xp = tmp_path / "X-Plane 12"
    scenery = xp / "Custom Scenery"
    scenery.mkdir(parents=True)
    ini = scenery / "scenery_packs.ini"
    ini.write_text(
        "I\n1000 Version\nSCENERY\n\n"
        "SCENERY_PACK *GLOBAL_AIRPORTS*\n"
        f"SCENERY_PACK Custom Scenery/{ORTHO_PREFIX}+46+006/\n"
        "SCENERY_PACK Custom Scenery/Photo Lyon/\n"
        "SCENERY_PACK Custom Scenery/z_ao_eur/\n"
    )
    before = ini.read_text()
    put_dsf(scenery / f"{ORTHO_PREFIX}+46+006", (46, 6), b"not read")
    put_dsf(scenery / "Photo Lyon", (44, 5), dsf({"sim/west": "5"}))  # below Live's place
    put_dsf(scenery / "z_ao_eur", (46, 6), dsf({"sim/west": "6"}))
    assert own_squares(xp, LINK_NAME) == {(46, 6): f"{ORTHO_PREFIX}+46+006"}
    assert ini.read_text() == before


def test_one_dsf_read_a_pack_and_only_its_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """simHeaven X-World and Global Forests: thousands of zipped DSFs, overlays all; the first
    version unpacked every one of them, for minutes, before a start."""
    import os

    from orthostudio.live import covered

    xp = tmp_path / "X-Plane 12"
    scenery = xp / "Custom Scenery"
    scenery.mkdir(parents=True)
    (scenery / "scenery_packs.ini").write_text(
        "I\n1000 Version\nSCENERY\n\nSCENERY_PACK Custom Scenery/Global_Forests_v2/\n"
        "SCENERY_PACK Custom Scenery/Broken/\n"
        f"SCENERY_PACK Custom Scenery/{LINK_NAME}/\n"
    )
    forests = scenery / "Global_Forests_v2"
    big = dsf({"sim/overlay": "1"}) + os.urandom(3_000_000)  # a DSF of some weight, zipped
    for lat in range(40, 46):
        put_dsf(forests, (lat, 5), big, zipped=True)
    put_dsf(scenery / "Broken", (46, 5), b"7z\xbc\xaf\x27\x1c broken")  # unreadable
    read = []
    real = covered.dsf_properties
    monkeypatch.setattr(covered, "dsf_properties", lambda p: read.append(p) or real(p))
    assert covered_squares(xp, LINK_NAME) == {}  # overlays; unreadable is not taken for ground
    assert len(read) == 2  # one DSF a pack
    head = covered._head(read[0])
    assert head.startswith(b"XPLNEDSF") and len(head) < 1_100_000  # its start, not 3 MB


def test_live_leaves_the_users_squares_alone(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    setup_region(
        make_source(tmp_path, tile=(46, 2), textures=(*NAMES, STRADDLING)), pack, log=lambda _: None
    )
    south, west, *_ = parse_texture_name(f"{NAMES[0]}.dds").bounds
    square = (math.floor(south), math.floor(west))
    s = Streamer(
        pack,
        ChunkCache(tmp_path / "c.sqlite"),
        FakeDownloader,
        finest_quality=0,
        log=lambda _: None,
        covered={square},
    )
    assert s.theirs == {f"{n}.dds" for n in NAMES}  # the straddling one reaches another square
    jobs = s.plan(Fix(*parse_texture_name(f"{NAMES[0]}.dds").center), x_plane_there=False)
    assert not any(f"{n}.dds" in jobs for n in NAMES)  # nothing to download, nothing waited for
    assert f"{STRADDLING}.dds" in jobs
    s.cache.close()


def test_a_start_says_which_squares_it_leaves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    xp = make_xplane(tmp_path)
    scenery = xp / "Custom Scenery"
    ini = scenery / "scenery_packs.ini"
    ini.write_text(
        ini.read_text().replace(
            "SCENERY_PACK Custom Scenery/z_ao_eur/",
            f"SCENERY_PACK Custom Scenery/{ORTHO_PREFIX}+46+002/\n"
            "SCENERY_PACK Custom Scenery/z_ao_eur/",
        )
    )
    put_dsf(scenery / f"{ORTHO_PREFIX}+46+002", (46, 2), b"a tile")
    source = make_source(tmp_path, tile=(46, 2), textures=NAMES)
    lines: list[str] = []
    prepared = session.prepare(
        session.Options(xplane=xp, pack=tmp_path / "pack", source=source, no_plugin=True),
        log=lines.append,
    )
    assert prepared.covered == {(46, 2): f"{ORTHO_PREFIX}+46+002"}
    assert any("your own scenery covers 1 squares" in line for line in lines)
    order = ini.read_text().splitlines()
    assert order.index(f"SCENERY_PACK Custom Scenery/{ORTHO_PREFIX}+46+002/") < order.index(
        f"SCENERY_PACK Custom Scenery/{LINK_NAME}/"
    )  # OrthoStudio XP's tile above Live's pack: X-Plane draws it


def test_live_everywhere_lists_its_pack_above_the_users_tiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    xp = make_xplane(tmp_path)
    scenery = xp / "Custom Scenery"
    ini = scenery / "scenery_packs.ini"
    tile = f"SCENERY_PACK Custom Scenery/{ORTHO_PREFIX}+46+002/"
    ini.write_text(
        ini.read_text().replace(
            "SCENERY_PACK Custom Scenery/z_ao_eur/",
            tile + "\nSCENERY_PACK Custom Scenery/z_ao_eur/",
        )
    )
    put_dsf(scenery / f"{ORTHO_PREFIX}+46+002", (46, 2), b"a tile")
    source = make_source(tmp_path, tile=(46, 2), textures=NAMES)
    live = f"SCENERY_PACK Custom Scenery/{LINK_NAME}/"

    def start(own_tiles: str) -> session.Prepared:
        options = session.Options(
            xplane=xp, pack=tmp_path / "pack", source=source, no_plugin=True, own_tiles=own_tiles
        )
        return session.prepare(options, log=lambda _: None)

    assert start("under").covered == {}  # Live draws the square
    order = ini.read_text().splitlines()
    assert order.index(live) < order.index(tile)
    # a stop that left it there: the pilot's tiles first again at the next start
    assert start("first").covered == {(46, 2): f"{ORTHO_PREFIX}+46+002"}
    order = ini.read_text().splitlines()
    assert order.index(tile) < order.index(live)
