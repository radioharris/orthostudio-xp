"""The decals Settings offers (``orthostudio.decals``): setdecal's list less what X-Plane 12 no
longer exports."""

from __future__ import annotations

from pathlib import Path

import pytest

from orthostudio.decals import DECALS, DEFAULT_DECAL, decal_lib

REAL_DECALS = Path.home() / "X-Plane 12" / "Resources" / "default scenery" / "1000 decals"


def test_the_list_holds_ortho4xps_decal_once_in_setdecals_order() -> None:
    assert DEFAULT_DECAL in DECALS and len(set(DECALS)) == len(DECALS) == 67
    assert list(DECALS) == sorted(DECALS)  # setdecal's order: bytes, capitals first
    assert all(name.endswith(".dcl") and "/" not in name for name in DECALS)
    assert decal_lib(DEFAULT_DECAL) == "lib/g10/decals/maquify_2_green_key.dcl"


@pytest.mark.xplane
@pytest.mark.skipif(not REAL_DECALS.is_dir(), reason="reference X-Plane 12 install not present")
def test_x_plane_12_exports_every_decal_offered() -> None:
    """A tile naming a decal X-Plane does not export would ask it for one it does not have:
    setdecal still offers five such names (X-Plane 12.4.4, 2026-09-26)."""
    exported = {
        line.split()[1]
        for line in (REAL_DECALS / "library.txt").read_text(errors="replace").splitlines()
        if line.startswith("EXPORT")
    }
    assert {decal_lib(name) for name in DECALS} <= exported
