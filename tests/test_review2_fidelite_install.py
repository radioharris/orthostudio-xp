"""Adversarial fidelity review (P2a): ``scenery_packs.ini`` ordering on realistic lists
(airport packs above and below ``*GLOBAL_AIRPORTS*``, absolute paths with spaces, CRLF,
HD-mesh packs at the bottom, X-Plane 12 putting a new pack at the top of the list).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from orthostudio.install.scenery_packs import SceneryPacks

REAL_INI = Path.home() / "X-Plane 12" / "Custom Scenery" / "scenery_packs.ini"
TILE_PACK = re.compile(
    r"^(y(OrthoStudio|Ortho4XP)_Overlays|z(OrthoStudio|Ortho4XP)_[+-]\d{2}[+-]\d{3})$"
)
"""The tile packs a real install may already hold: OrthoStudio XP's, or tiles Ortho4XP built."""


def _ini(*lines: str, nl: str = "\n") -> bytes:
    return nl.join(["I", "1000 Version", "SCENERY", "", *lines, ""]).encode()


def _order(packs: SceneryPacks) -> list[str]:
    return packs.names()


def test_airport_packs_below_global_airports_stay_above_the_ortho_packs() -> None:
    """A custom airport listed *below* ``*GLOBAL_AIRPORTS*`` (X-Plane 12 appends packs it
    discovers at the top, but users move them) must not end up below the ortho or overlay."""
    packs = SceneryPacks.from_bytes(
        _ini(
            "SCENERY_PACK Custom Scenery/Aerosoft - LFMN Nice Cote d Azur X/",
            "SCENERY_PACK *GLOBAL_AIRPORTS*",
            "SCENERY_PACK Custom Scenery/LFML - Marseille Provence/",
            "SCENERY_PACK Custom Scenery/openSAM_Library/",
            "SCENERY_PACK_DISABLED Custom Scenery/z_ao_eur/",
            "SCENERY_PACK_DISABLED Custom Scenery/z_autoortho/",
            "SCENERY_PACK Custom Scenery/XPME_Europe/",
        )
    )
    assert packs.ensure("zOrthoStudio_+43+005", kind="ortho")
    assert packs.ensure("yOrthoStudio_Overlays", kind="overlay")
    assert packs.ensure("zOrthoStudio_+43+004", kind="ortho")
    names = _order(packs)
    assert names.index("LFML - Marseille Provence") < names.index("yOrthoStudio_Overlays")
    assert names.index("openSAM_Library") < names.index("yOrthoStudio_Overlays")
    assert names[names.index("yOrthoStudio_Overlays") + 1 :][:2] == [
        "zOrthoStudio_+43+005",
        "zOrthoStudio_+43+004",
    ]
    assert names.index("zOrthoStudio_+43+004") < names.index("z_ao_eur")
    # XPME (a mesh) stays where the user put it, below AutoOrtho
    assert names[-1] == "XPME_Europe"


def test_absolute_paths_with_spaces_are_recognised_and_not_duplicated(tmp_path: Path) -> None:
    packs = SceneryPacks.from_bytes(
        _ini(
            "SCENERY_PACK *GLOBAL_AIRPORTS*",
            "SCENERY_PACK /Users/pilot/X-Plane 12/Custom Scenery/yOrthoStudio_Overlays/",
            "SCENERY_PACK_DISABLED /Users/pilot/X-Plane 12/Custom Scenery/zOrthoStudio_+43+005/",
            "SCENERY_PACK Custom Scenery/z_autoortho/",
        )
    )
    assert packs.ensure("zOrthoStudio_+43+005", kind="ortho")  # re-enabled in place
    assert not packs.ensure("yOrthoStudio_Overlays", kind="overlay")
    assert packs.ensure("zOrthoStudio_+43+006", kind="ortho")
    rendered = packs.to_bytes().decode()
    assert rendered.count("zOrthoStudio_+43+005") == 1
    assert "SCENERY_PACK /Users/pilot/X-Plane 12/Custom Scenery/zOrthoStudio_+43+005/" in rendered
    assert "SCENERY_PACK Custom Scenery/zOrthoStudio_+43+006/" in rendered
    names = _order(packs)
    assert names.index("zOrthoStudio_+43+006") == names.index("zOrthoStudio_+43+005") + 1


def test_crlf_file_stays_crlf_and_only_gains_two_lines() -> None:
    data = _ini(
        "SCENERY_PACK *GLOBAL_AIRPORTS*",
        "SCENERY_PACK Custom Scenery/z_autoortho/",
        nl="\r\n",
    )
    packs = SceneryPacks.from_bytes(data)
    packs.ensure("yOrthoStudio_Overlays", kind="overlay")
    packs.ensure("zOrthoStudio_+43+005", kind="ortho")
    out = packs.to_bytes()
    assert b"\n" not in out.replace(b"\r\n", b"")
    assert out.count(b"\r\n") == data.count(b"\r\n") + 2
    assert out.split(b"\r\n")[4:8] == [
        b"SCENERY_PACK *GLOBAL_AIRPORTS*",
        b"SCENERY_PACK Custom Scenery/yOrthoStudio_Overlays/",
        b"SCENERY_PACK Custom Scenery/zOrthoStudio_+43+005/",
        b"SCENERY_PACK Custom Scenery/z_autoortho/",
    ]


def test_without_autoortho_a_new_ortho_pack_lands_above_a_mesh_pack() -> None:
    """Rule 3 of ``install.md`` 3.2, amended after the P2a review: with no ``zOrthoStudio_*``
    and no AutoOrtho line, the ortho pack (and the overlay) is inserted *before* the first
    base-mesh pack (``zzz_hd_global_scenery4``, ``XPME_*``, any ``z*`` name sorting after
    ``zOrthoStudio_``) rather than appended below it, so X-Plane draws the ortho tile, not the
    mesh. Library packs above stay where the user put them."""
    packs = SceneryPacks.from_bytes(
        _ini(
            "SCENERY_PACK *GLOBAL_AIRPORTS*",
            "SCENERY_PACK Custom Scenery/simHeaven_X-WORLD-Pro_Library/",
            "SCENERY_PACK Custom Scenery/zzz_hd_global_scenery4/",
        )
    )
    packs.ensure("yOrthoStudio_Overlays", kind="overlay")
    packs.ensure("zOrthoStudio_+43+005", kind="ortho")
    names = _order(packs)
    assert names == [
        "*GLOBAL_AIRPORTS*",
        "simHeaven_X-WORLD-Pro_Library",
        "yOrthoStudio_Overlays",
        "zOrthoStudio_+43+005",
        "zzz_hd_global_scenery4",
    ]
    packs = SceneryPacks.from_bytes(
        _ini("SCENERY_PACK *GLOBAL_AIRPORTS*", "SCENERY_PACK Custom Scenery/XPME_Europe/")
    )
    packs.ensure("zOrthoStudio_+43+005", kind="ortho")
    assert _order(packs) == ["*GLOBAL_AIRPORTS*", "zOrthoStudio_+43+005", "XPME_Europe"]


def test_pack_placed_at_the_top_by_xplane_12_is_not_moved_and_siblings_go_below_airports() -> None:
    """X-Plane 12 inserts a folder it discovers at the *top* of ``scenery_packs.ini``. Rule 1
    keeps that line; the next tile goes to the floor (right after ``*GLOBAL_AIRPORTS*``), so
    the two ortho packs are split around the airports. Observed behaviour, no move."""
    packs = SceneryPacks.from_bytes(
        _ini(
            "SCENERY_PACK Custom Scenery/zOrthoStudio_+43+005/",
            "SCENERY_PACK Custom Scenery/Aerosoft - LFMN Nice Cote d Azur X/",
            "SCENERY_PACK *GLOBAL_AIRPORTS*",
            "SCENERY_PACK Custom Scenery/z_autoortho/",
        )
    )
    packs.ensure("zOrthoStudio_+43+004", kind="ortho")
    packs.ensure("yOrthoStudio_Overlays", kind="overlay")
    names = _order(packs)
    assert names[0] == "zOrthoStudio_+43+005"
    assert names.index("*GLOBAL_AIRPORTS*") < names.index("yOrthoStudio_Overlays")
    assert names.index("yOrthoStudio_Overlays") < names.index("zOrthoStudio_+43+004")
    assert names.index("zOrthoStudio_+43+004") < names.index("z_autoortho")


@pytest.mark.xplane
def test_real_ini_copy_two_tiles_and_overlay_keep_every_airport_above(tmp_path: Path) -> None:
    """Read-only use of the user's file: a copy in ``tmp_path`` receives two tiles and the
    overlay; every line above ``*GLOBAL_AIRPORTS*`` (the airports and landmarks) is unchanged,
    the three new lines sit between ``yAutoOrtho_Overlays`` and ``z_ao_eur``."""
    if not REAL_INI.is_file():
        pytest.skip("no X-Plane 12 scenery_packs.ini on this machine")
    real = REAL_INI.read_bytes()
    copy = tmp_path / "scenery_packs.ini"
    copy.write_bytes(real)
    assert SceneryPacks.load(copy).to_bytes() == real
    # The user flies tiles already: start from their list without the tile packs, so that
    # installing is new.
    packs = SceneryPacks.load(copy)
    for name in [n for n in packs.names() if TILE_PACK.match(n)]:
        packs.remove(name)
    original = packs.to_bytes()
    copy.write_bytes(original)
    packs = SceneryPacks.load(copy)
    assert packs.to_bytes() == original
    packs.ensure("zOrthoStudio_+43+005", kind="ortho")
    packs.ensure("yOrthoStudio_Overlays", kind="overlay")
    packs.ensure("zOrthoStudio_+43+004", kind="ortho")
    packs.save(copy)
    assert REAL_INI.read_bytes() == real
    before = original.split(b"\n")
    after = copy.read_bytes().split(b"\n")
    k = before.index(b"SCENERY_PACK *GLOBAL_AIRPORTS*")
    assert after[: k + 1] == before[: k + 1]
    names = SceneryPacks.load(copy).names()
    i = names.index("yAutoOrtho_Overlays")
    assert names[i + 1 : i + 5] == [
        "yOrthoStudio_Overlays",
        "zOrthoStudio_+43+005",
        "zOrthoStudio_+43+004",
        "z_ao_eur",
    ]
    assert (tmp_path / "scenery_packs.ini.bak").read_bytes() == original
