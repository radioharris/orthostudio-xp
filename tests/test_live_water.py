# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The water of Live's own mesh (orthostudio.live.waterphoto): the setting for rivers and lakes,
the pack's record of what its terrain files name, the two borders."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from live_helpers import fake_dsf, live_names
from orthostudio.live import control as control_module
from orthostudio.live import settings_file
from orthostudio.live.names import MASK_RE, TER_KINDS, TERRAIN_LISTS, mask_name
from orthostudio.live.region import (
    WaterRecord,
    set_water,
    setup_region,
    water_record,
)
from orthostudio.live.settings import LiveSettings, load_settings, save_settings
from orthostudio.live.waterphoto import EVEN_BORDER, NO_BORDER

TILE = (46, 2)
# ZL16 textures in a row eastwards, their centres on TILE: the sea alone, a river alone (its mask
# in rivers/textures), both, a river without a mask, the land alone
SEA, RIVER, BOTH, BARE, LAND = (f"23296_{33152 + 16 * k}_BI16" for k in range(5))
KINDS = {SEA: 1 | 4, RIVER: 1 | 2, BOTH: 1 | 2 | 4, BARE: 1 | 2, LAND: 1}


def quiet(_: str) -> None:
    pass


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def mask_png(side: int = 64) -> bytes:
    """A water mask as Live's mesh has them: the photo whole on the land's side (255), faded to
    nothing (0) across the water."""
    ramp = np.clip(np.linspace(300, -45, side), 0, 255).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(np.tile(ramp, (side, 1))).save(out, format="PNG")
    return out.getvalue()


def live_region(root: Path, kinds: dict[str, int] = KINDS, rivers=(RIVER,)) -> Path:
    """A region of Live's own mesh: its DSF, its terrain files named in a list (kinds: 1 the land,
    2 a river's or a lake's overlay, 4 the sea's), the masks of the textures with sea in
    ``textures``, those of ``rivers`` (textures with rivers alone) in ``rivers/textures``."""
    lat, lon = TILE
    src = root / "z_osxp_test"
    dsf = src / "Earth nav data" / "+40+000" / f"{lat:+03d}{lon:+04d}.dsf"
    dsf.parent.mkdir(parents=True)
    (src / TERRAIN_LISTS).mkdir()
    (src / "textures").mkdir()
    (src / "rivers" / "textures").mkdir(parents=True)
    files = []
    for name, bits in kinds.items():
        files += [f"{name}{suffix}.ter" for suffix, bit in TER_KINDS if bits & bit]
        if bits & 4:
            (src / "textures" / mask_name(f"{name}.dds")).write_bytes(mask_png())
    for name in rivers:
        (src / "rivers" / "textures" / mask_name(f"{name}.dds")).write_bytes(mask_png())
    lst = src / TERRAIN_LISTS / f"z_osxp_test_{lat:+03d}{lon:+04d}.txt"
    lst.write_text("\n".join(files) + "\n", encoding="ascii")
    dsf.write_bytes(fake_dsf(["terrain_Water"] + [f"terrain/{f}" for f in files]))
    return src


def live_pack(root: Path, **kwargs) -> Path:
    pack = root / "pack"
    setup_region(live_region(root, **kwargs), pack, log=quiet)
    return pack


# --- the setting --------------------------------------------------------------------------------


def test_rivers_and_lakes_are_even_until_chosen(home: Path) -> None:
    """Off by default, carried to a start, and kept: a file of before has none, a wrong value costs
    itself only."""
    assert LiveSettings().rivers_fade is False
    assert control_module.options_for(LiveSettings()).rivers_fade is False
    assert control_module.options_for(LiveSettings(rivers_fade=True)).rivers_fade is True
    settings_file().write_text("[stream]\nnight = false\n")  # a file of before the setting
    assert load_settings().rivers_fade is False
    save_settings(LiveSettings(rivers_fade=True))
    assert load_settings().rivers_fade is True
    settings_file().write_text('[stream]\nrivers_fade = "blinding"\nprovider = "Arc"\n')
    got = load_settings()
    assert got.rivers_fade is False and got.provider == "Arc"


# --- the pack's record --------------------------------------------------------------------------


def test_the_record_makes_the_terrain_files_again_but_not_under_x_plane(tmp_path: Path) -> None:
    """A pack made before the water photos: its terrain files name the masks until a start with
    X-Plane not running makes them again, the masks copied with them gone; then once for each
    change of the rivers' setting, never under X-Plane."""
    pack = live_pack(tmp_path)
    assert water_record(pack) is None
    live_names(pack).make(TILE)  # as before: the sea's terrain names its mask
    sea = pack / "terrain" / f"{SEA}_sea_overlay.ter"
    assert f"BORDER_TEX ../textures/{mask_name(SEA + '.dds')}".encode() in sea.read_bytes()
    assert (pack / "textures" / mask_name(f"{SEA}.dds")).exists()
    said: list[str] = []
    set_water(pack, False, log=said.append, xplane_running=lambda: True)
    assert sea.exists() and water_record(pack) is None  # X-Plane may read them
    assert "Live's next start" in said[-1]
    set_water(pack, False, log=said.append)
    assert list((pack / "terrain").iterdir()) == []
    assert not any(MASK_RE.match(p.name) for p in (pack / "textures").iterdir())
    assert water_record(pack) == WaterRecord(rivers_fade=False)
    count = len(said)
    set_water(pack, False, log=said.append)  # recorded: nothing more
    assert len(said) == count
    live_names(pack).make(TILE)
    set_water(pack, True, log=said.append)
    assert water_record(pack) == WaterRecord(rivers_fade=True)
    assert list((pack / "terrain").iterdir()) == []
    assert "faded at their banks" in " ".join(said[count:])


def test_the_two_borders_are_written_at_each_start(tmp_path: Path) -> None:
    """Uniform grey 150 for rivers and lakes that do not fade, 0 for those without a mask: names
    of Live's own, never taken for a water mask or a texture, and written again when changed."""
    from orthostudio.live.geo import parse_texture_name
    from orthostudio.live.region import _to_squares

    pack = live_pack(tmp_path)
    set_water(pack, False, log=quiet, xplane_running=lambda: True)  # written whatever X-Plane does
    for name, grey in ((EVEN_BORDER, 150), (NO_BORDER, 0)):
        with Image.open(pack / "textures" / name) as im:
            assert im.mode == "L" and im.size == (16, 16)
            assert set(np.asarray(im).ravel()) == {grey}
        assert not MASK_RE.match(name) and parse_texture_name(name) is None
    (pack / "textures" / EVEN_BORDER).write_bytes(b"spoilt")
    set_water(pack, False, log=quiet)
    with Image.open(pack / "textures" / EVEN_BORDER) as im:
        assert set(np.asarray(im).ravel()) == {150}
    _to_squares(pack, quiet)  # what a pack of before has taken away: not them
    assert (pack / "textures" / EVEN_BORDER).exists() and (pack / "textures" / NO_BORDER).exists()
