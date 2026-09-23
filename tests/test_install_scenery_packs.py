"""``scenery_packs.ini`` parsing, ordering rule and atomic save (spec section 3)."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.install.scenery_packs import (
    DEFAULT_HEADER,
    GLOBAL_AIRPORTS,
    SceneryPackEntry,
    SceneryPacks,
    pack_kind,
)

REAL_INI = Path.home() / "X-Plane 12" / "Custom Scenery" / "scenery_packs.ini"

SYNTHETIC_CRLF = (
    b"I\r\n1000 Version\r\nSCENERY\r\n\r\n"
    b"SCENERY_PACK Custom Scenery/Airport A/\r\n"
    b"SCENERY_PACK *GLOBAL_AIRPORTS*\r\n"
    b"# a comment the user left\r\n"
    b"SCENERY_PACK_DISABLED Custom Scenery/Some Lib/\r\n"
    b"SCENERY_PACK /Volumes/Ext/abs pack/\r\n"
    b"SCENERY_PACK C:\\X-Plane 12\\Custom Scenery\\winpack\\\r\n"
    b"SCENERY_PACK Custom Scenery/z_autoortho/\r\n"
    b"SCENERY_PACK Custom Scenery/zzz_last/"
)


def _names(p: SceneryPacks) -> list[str]:
    return p.names()


def _packs(*names: str) -> SceneryPacks:
    paths = [n if n == GLOBAL_AIRPORTS else f"Custom Scenery/{n}/" for n in names]
    return SceneryPacks(body=[SceneryPackEntry(x) for x in paths])


# ----------------------------------------------------------------- parsing


def test_round_trip_crlf_no_trailing_newline() -> None:
    p = SceneryPacks.from_bytes(SYNTHETIC_CRLF)
    assert p.newline == "\r\n" and p.trailing_newline is False
    assert p.header == ["I", "1000 Version", "SCENERY", ""]
    assert _names(p) == [
        "Airport A",
        GLOBAL_AIRPORTS,
        "Some Lib",
        "abs pack",
        "winpack",
        "z_autoortho",
        "zzz_last",
    ]
    assert p.body[2] == "# a comment the user left"
    assert p.find("Some Lib") is not None and p.find("Some Lib").enabled is False
    assert p.to_bytes() == SYNTHETIC_CRLF


def test_entry_flags() -> None:
    e = SceneryPackEntry("Custom Scenery/zOrthoStudio_+43+005/")
    assert e.is_ortho and not e.is_overlay and not e.is_autoortho and not e.is_global_airports
    assert SceneryPackEntry("Custom Scenery/yOrthoStudio_Overlays/").is_overlay
    assert SceneryPackEntry("Custom Scenery/z_ao_eur/").is_autoortho
    assert SceneryPackEntry("Custom Scenery/z_autoortho/").is_autoortho
    assert SceneryPackEntry(GLOBAL_AIRPORTS).is_global_airports
    assert SceneryPackEntry("Custom Scenery/x/", enabled=False).render() == (
        "SCENERY_PACK_DISABLED Custom Scenery/x/"
    )
    assert (
        pack_kind("yOrthoStudio_Overlays") == "overlay" and pack_kind("zOrthoStudio_x") == "ortho"
    )


def test_the_packs_of_imported_tiles_are_ordered_like_osxps() -> None:
    """A tile the Library imported from an Ortho4XP folder keeps Ortho4XP's names; it is still an
    ortho tile and its overlays still an overlays pack."""
    assert SceneryPackEntry("Custom Scenery/zOrtho4XP_+43+005/").is_ortho
    assert SceneryPackEntry("Custom Scenery/yOrtho4XP_Overlays/").is_overlay
    assert not SceneryPackEntry("Custom Scenery/zOrtho4XP_+43+005/").is_mesh
    assert pack_kind("yOrtho4XP_Overlays") == "overlay"
    p = _packs(GLOBAL_AIRPORTS, "yOrtho4XP_Overlays", "zOrtho4XP_+43+005", "z_autoortho")
    assert p.ensure("zOrthoStudio_+44+005", kind="ortho") is True
    assert _names(p)[2:5] == ["zOrtho4XP_+43+005", "zOrthoStudio_+44+005", "z_autoortho"]


def test_a_built_tile_goes_above_the_imported_tile_of_its_square() -> None:
    """X-Plane draws the first ortho pack of a square: the tile OrthoStudio XP built must win over
    an imported one of the same square, and leave the others where they are."""
    p = _packs(GLOBAL_AIRPORTS, "zOrtho4XP_+42+005", "zOrtho4XP_+43+005", "z_autoortho")
    assert p.ensure("zOrthoStudio_+43+005", kind="ortho", above="zOrtho4XP_+43+005") is True
    assert _names(p) == [
        GLOBAL_AIRPORTS, "zOrtho4XP_+42+005", "zOrthoStudio_+43+005", "zOrtho4XP_+43+005",
        "z_autoortho",
    ]  # fmt: skip
    q = _packs(GLOBAL_AIRPORTS, "zOrtho4XP_+42+005", "z_autoortho")  # no imported tile there
    assert q.ensure("zOrthoStudio_+43+005", kind="ortho", above="zOrtho4XP_+43+005") is True
    assert _names(q) == [
        GLOBAL_AIRPORTS,
        "zOrtho4XP_+42+005",
        "zOrthoStudio_+43+005",
        "z_autoortho",
    ]
    listed = _packs(GLOBAL_AIRPORTS, "zOrtho4XP_+43+005", "zOrthoStudio_+43+005")
    assert listed.ensure("zOrthoStudio_+43+005", kind="ortho", above="zOrtho4XP_+43+005") is False


def test_empty_headerless_and_missing_files(tmp_path: Path) -> None:
    assert SceneryPacks.from_bytes(b"").header == list(DEFAULT_HEADER)
    p = SceneryPacks.from_bytes(b"SCENERY_PACK Custom Scenery/a/\n")
    assert p.header == list(DEFAULT_HEADER) and _names(p) == ["a"]
    assert SceneryPacks.load(tmp_path / "none.ini").body == []
    # A header without the blank line stays as it is.
    p = SceneryPacks.from_bytes(b"I\n1000 Version\nSCENERY\nSCENERY_PACK Custom Scenery/a/\n")
    assert p.header == ["I", "1000 Version", "SCENERY"]
    assert p.to_bytes() == b"I\n1000 Version\nSCENERY\nSCENERY_PACK Custom Scenery/a/\n"


def test_undecodable_bytes_survive_round_trip() -> None:
    data = b"I\n1000 Version\nSCENERY\n\nSCENERY_PACK Custom Scenery/caf\xe9 \xff/\n"
    assert SceneryPacks.from_bytes(data).to_bytes() == data


# ----------------------------------------------------------------- ordering


def test_ensure_ortho_before_autoortho_then_after_last_ortho() -> None:
    p = _packs("Airport A", GLOBAL_AIRPORTS, "Lib", "z_autoortho", "rest")
    assert p.ensure("zOrthoStudio_+43+005", kind="ortho") is True
    assert _names(p) == [
        "Airport A",
        GLOBAL_AIRPORTS,
        "Lib",
        "zOrthoStudio_+43+005",
        "z_autoortho",
        "rest",
    ]
    assert p.ensure("zOrthoStudio_+44+005", kind="ortho") is True
    assert _names(p)[3:5] == ["zOrthoStudio_+43+005", "zOrthoStudio_+44+005"]
    assert p.find("zOrthoStudio_+44+005").path == "Custom Scenery/zOrthoStudio_+44+005/"


def test_ensure_overlay_before_first_ortho() -> None:
    p = _packs(
        GLOBAL_AIRPORTS, "Lib", "zOrthoStudio_+43+005", "zOrthoStudio_+44+005", "z_autoortho"
    )
    assert p.ensure("yOrthoStudio_Overlays", kind="overlay") is True
    assert _names(p)[1:4] == ["Lib", "yOrthoStudio_Overlays", "zOrthoStudio_+43+005"]


def test_ensure_overlay_without_ortho_goes_before_autoortho_or_end() -> None:
    p = _packs(GLOBAL_AIRPORTS, "Lib", "z_ao_eur", "z_autoortho")
    p.ensure("yOrthoStudio_Overlays", kind="overlay")
    assert _names(p) == [GLOBAL_AIRPORTS, "Lib", "yOrthoStudio_Overlays", "z_ao_eur", "z_autoortho"]
    p = _packs("Airport", GLOBAL_AIRPORTS, "Lib")
    p.ensure("zOrthoStudio_+43+005", kind="ortho")
    assert _names(p)[-1] == "zOrthoStudio_+43+005"


def test_ensure_existing_keeps_position_and_enables() -> None:
    p = _packs(GLOBAL_AIRPORTS, "zOrthoStudio_+43+005", "Lib", "z_autoortho")
    assert p.ensure("zOrthoStudio_+43+005", kind="ortho") is False
    p.find("zOrthoStudio_+43+005").enabled = False
    assert p.ensure("zOrthoStudio_+43+005", kind="ortho") is True
    assert _names(p) == [GLOBAL_AIRPORTS, "zOrthoStudio_+43+005", "Lib", "z_autoortho"]
    assert p.find("zOrthoStudio_+43+005").enabled is True


def test_ensure_can_leave_a_disabled_line_alone() -> None:
    # A simHeaven X-World user disables Ortho4XP's overlays: installing a tile keeps them off.
    p = _packs(GLOBAL_AIRPORTS, "yOrthoStudio_Overlays", "zOrthoStudio_+43+005", "z_autoortho")
    p.find("yOrthoStudio_Overlays").enabled = False
    assert p.ensure("yOrthoStudio_Overlays", kind="overlay", reenable=False) is False
    assert p.find("yOrthoStudio_Overlays").enabled is False
    assert p.ensure("zOrthoStudio_+44+005", kind="ortho", reenable=False) is True  # new lines on
    assert p.find("zOrthoStudio_+44+005").enabled is True


def test_nothing_above_global_airports_is_touched() -> None:
    # The user put an ortho pack above *GLOBAL_AIRPORTS*: it stays, and new packs go below.
    p = _packs("zOrthoStudio_+10+010", "Airport", GLOBAL_AIRPORTS, "Lib")
    p.ensure("zOrthoStudio_+43+005", kind="ortho")
    assert _names(p) == [
        "zOrthoStudio_+10+010",
        "Airport",
        GLOBAL_AIRPORTS,
        "zOrthoStudio_+43+005",
        "Lib",
    ]
    p.ensure("yOrthoStudio_Overlays", kind="overlay")
    assert _names(p)[:4] == [
        "zOrthoStudio_+10+010",
        "Airport",
        GLOBAL_AIRPORTS,
        "yOrthoStudio_Overlays",
    ]
    # Without *GLOBAL_AIRPORTS* there is no floor: before the first AutoOrtho line.
    p = _packs("Airport", "z_autoortho")
    p.ensure("zOrthoStudio_+43+005", kind="ortho")
    assert _names(p) == ["Airport", "zOrthoStudio_+43+005", "z_autoortho"]


def test_remove_and_disable() -> None:
    p = _packs(GLOBAL_AIRPORTS, "zOrthoStudio_+43+005", "Lib")
    assert p.disable("zOrthoStudio_+43+005") is True and p.disable("zOrthoStudio_+43+005") is False
    assert p.find("zOrthoStudio_+43+005").enabled is False
    assert p.remove("zOrthoStudio_+43+005") is True and p.remove("zOrthoStudio_+43+005") is False
    assert _names(p) == [GLOBAL_AIRPORTS, "Lib"]


# ----------------------------------------------------------------- saving


def test_save_is_atomic_with_backup(tmp_path: Path) -> None:
    ini = tmp_path / "scenery_packs.ini"
    ini.write_bytes(SYNTHETIC_CRLF)
    p = SceneryPacks.load(ini)
    p.ensure("zOrthoStudio_+43+005", kind="ortho")
    p.save(ini)
    assert (tmp_path / "scenery_packs.ini.bak").read_bytes() == SYNTHETIC_CRLF
    saved = ini.read_bytes()
    assert b"\r\nSCENERY_PACK Custom Scenery/zOrthoStudio_+43+005/\r\n" in saved
    # a second change: .bak keeps the original, .osxp-previous holds the list it replaced
    p.ensure("zOrthoStudio_+44+005", kind="ortho")
    p.save(ini)
    assert (tmp_path / "scenery_packs.ini.bak").read_bytes() == SYNTHETIC_CRLF
    assert (tmp_path / "scenery_packs.ini.osxp-previous").read_bytes() == saved
    assert b"\n" not in saved.replace(b"\r\n", b"")  # line ending preserved everywhere
    assert not saved.endswith(b"\r\n")  # no trailing newline, as in the original
    assert not list(tmp_path.glob("*.tmp-*"))
    # Fresh file: default header, one entry, trailing newline.
    fresh = tmp_path / "new" / "scenery_packs.ini"
    q = SceneryPacks.load(fresh)
    q.ensure("yOrthoStudio_Overlays", kind="overlay")
    q.save(fresh, backup=True)
    text = fresh.read_bytes().decode()
    assert text.startswith("I" + q.newline + "1000 Version" + q.newline + "SCENERY")
    assert text.endswith("SCENERY_PACK Custom Scenery/yOrthoStudio_Overlays/" + q.newline)
    assert not (tmp_path / "new" / "scenery_packs.ini.bak").exists()


def test_save_unwritable_is_coded(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("x")
    with pytest.raises(OsxpError) as exc:
        SceneryPacks().save(tmp_path / "file" / "scenery_packs.ini")
    assert exc.value.code == "XP_SCENERY_PACKS_UNWRITABLE"
    assert "reason" in exc.value.context


# ----------------------------------------------------------------- the real file

TILE_PACK = re.compile(
    r"^(y(OrthoStudio|Ortho4XP)_Overlays|z(OrthoStudio|Ortho4XP)_[+-]\d{2}[+-]\d{3})$"
)
"""The tile packs a real install may already hold: OrthoStudio XP's, or tiles Ortho4XP built."""


def without_osxp_packs(packs: SceneryPacks) -> SceneryPacks:
    """``packs`` without the tile packs a real install may already hold."""
    for name in [n for n in packs.names() if TILE_PACK.match(n)]:
        packs.remove(name)
    return packs


@pytest.mark.xplane
@pytest.mark.skipif(not REAL_INI.is_file(), reason="reference X-Plane 12 install not present")
def test_real_scenery_packs_ini_is_read_only_and_reordered_on_a_copy(tmp_path: Path) -> None:
    before = hashlib.sha256(REAL_INI.read_bytes()).hexdigest()
    mtime = REAL_INI.stat().st_mtime_ns
    copy = tmp_path / "Custom Scenery" / "scenery_packs.ini"
    copy.parent.mkdir()
    shutil.copyfile(REAL_INI, copy)
    assert SceneryPacks.load(copy).to_bytes() == copy.read_bytes()  # the real file round-trips
    # The user flies tiles already: start from their list without the tile packs, so that
    # installing is new.
    copy.write_bytes(without_osxp_packs(SceneryPacks.load(copy)).to_bytes())
    original = copy.read_bytes()

    p = SceneryPacks.load(copy)
    assert p.to_bytes() == original
    assert p.header == ["I", "1000 Version", "SCENERY", ""] and p.newline == "\n"
    names = p.names()
    ga = names.index(GLOBAL_AIRPORTS)
    # Nothing below is a fact about one particular installation: the user adds and removes
    # scenery, so the pack count, the first pack's name and the neighbours of the new packs
    # are read from the file itself. What is asserted is the ordering rule of install.md.

    assert p.ensure("zOrthoStudio_+43+005", kind="ortho") is True
    assert p.ensure("yOrthoStudio_Overlays", kind="overlay") is True
    p.save(copy)

    after = SceneryPacks.load(copy)
    new = after.names()
    # 1. Exactly two packs were added, and nothing was lost.
    assert sorted(new) == sorted([*names, "zOrthoStudio_+43+005", "yOrthoStudio_Overlays"])
    # 2. Everything up to and including *GLOBAL_AIRPORTS* keeps its place (airport packs are
    #    never moved).
    assert new[: ga + 1] == names[: ga + 1]
    # 3. The overlay sits right above the ortho pack, both below the airports.
    i_ovl, i_ortho = new.index("yOrthoStudio_Overlays"), new.index("zOrthoStudio_+43+005")
    assert i_ortho == i_ovl + 1 and i_ovl > ga
    # 4. Every pre-existing pack keeps its relative order.
    assert [n for n in new if n in names] == names
    # 5. If AutoOrtho or another mesh pack is installed, the ortho pack lands above it.
    below = [n for n in names[ga + 1 :] if n.startswith(("z_ao", "z_autoortho", "zzz_", "XPME_"))]
    if below:
        assert i_ortho < new.index(below[0])
    assert (copy.parent / "scenery_packs.ini.bak").read_bytes() == original
    # Everything above *GLOBAL_AIRPORTS* is byte-identical.
    head = original.split(b"SCENERY_PACK *GLOBAL_AIRPORTS*")[0]
    assert copy.read_bytes().startswith(head)
    # Idempotent.
    assert after.ensure("zOrthoStudio_+43+005", kind="ortho") is False

    assert hashlib.sha256(REAL_INI.read_bytes()).hexdigest() == before
    assert REAL_INI.stat().st_mtime_ns == mtime


def test_taking_a_tile_out_leaves_the_users_own_copy_alone() -> None:
    """Every line ending in the pack's name used to go, wherever it pointed. A simmer who keeps
    a copy of a tile on another disk has two lines, and taking the tile out of X-Plane took his
    archive out with it, silently, with nothing said (found in review, 2026-09-23)."""
    packs = SceneryPacks.from_bytes(
        b"I\n1000 Version\nSCENERY\n\n"
        b"SCENERY_PACK Custom Scenery/zOrthoStudio_+43+005/\n"
        b"SCENERY_PACK /Volumes/OrthoArchive/zOrthoStudio_+43+005/\n"
        b"SCENERY_PACK C:\\Scenery\\zOrthoStudio_+43+005\\\n"
        b"SCENERY_PACK Custom Scenery/Airport A/\n"
    )
    assert packs.remove("zOrthoStudio_+43+005") is True
    left = [line for line in packs.to_bytes().decode().splitlines() if "SCENERY_PACK" in line]
    assert left == [
        "SCENERY_PACK /Volumes/OrthoArchive/zOrthoStudio_+43+005/",
        "SCENERY_PACK C:\\Scenery\\zOrthoStudio_+43+005\\",
        "SCENERY_PACK Custom Scenery/Airport A/",
    ]
    # and a tile that is only his archive is not ours to take out at all
    assert packs.remove("zOrthoStudio_+43+005") is False


def test_a_tile_goes_above_the_mesh_as_well_as_above_autoortho() -> None:
    """The insertion looked for AutoOrtho and stopped there, so an AutoOrtho line sitting below
    a base mesh put the tile below the mesh too. X-Plane draws the higher one, so the square
    never appeared in the sim while the Library said it was installed (found in review,
    2026-09-23)."""
    for lines in (
        b"SCENERY_PACK Custom Scenery/XPME_Europe/\nSCENERY_PACK Custom Scenery/z_autoortho/\n",
        b"SCENERY_PACK Custom Scenery/z_autoortho/\nSCENERY_PACK Custom Scenery/XPME_Europe/\n",
    ):
        packs = SceneryPacks.from_bytes(
            b"I\n1000 Version\nSCENERY\n\nSCENERY_PACK *GLOBAL_AIRPORTS*\n" + lines
        )
        packs.ensure("zOrthoStudio_+46+006", kind="ortho")
        order = [
            line.split(" ", 1)[1]
            for line in packs.to_bytes().decode().splitlines()
            if line.startswith("SCENERY_PACK")
        ]
        mine = next(i for i, n in enumerate(order) if "zOrthoStudio" in n)
        assert mine < next(i for i, n in enumerate(order) if "XPME" in n), order
        assert mine < next(i for i, n in enumerate(order) if "autoortho" in n), order
        assert mine > next(i for i, n in enumerate(order) if "GLOBAL_AIRPORTS" in n), order
