# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP Live's switch, its command line, and its pack's place in scenery_packs.ini."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orthostudio.install.scenery_packs import SceneryPacks
from orthostudio.live import enabled, settings_file
from orthostudio.live.__main__ import app
from orthostudio.live.pack import LINK_NAME
from orthostudio.live.scenery import install_link, is_installed, uninstall_link


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_switched_off_until_live_toml_says_so(home: Path) -> None:
    assert settings_file() == home / "live.toml"
    assert not enabled()  # no file
    settings_file().write_text("enabled = false\n")
    assert not enabled()
    settings_file().write_text("enabled = true\n")
    assert enabled()
    settings_file().write_text("enabled = [broken\n")
    assert not enabled()  # a file that does not parse keeps it off


def test_every_command_refuses_while_switched_off(home: Path) -> None:
    result = CliRunner().invoke(app, ["status"])
    assert result.exit_code == 1
    assert "switched off" in result.output
    assert "live.toml" in result.output


def test_the_config_of_orthostudio_xp_is_left_alone(home: Path) -> None:
    """The switch lives in its own file: saving the settings of OrthoStudio XP never touches it."""
    from orthostudio.config import Settings, save_settings

    settings_file().write_text("enabled = true\n")
    save_settings(Settings())
    assert enabled()
    saved = tomllib.loads((home / "config.toml").read_text())
    assert "live" not in saved
    assert all("live" not in section for section in saved.values() if isinstance(section, dict))


def _xplane(root: Path) -> Path:
    xp = root / "X-Plane 12"
    (xp / "Custom Scenery" / "z_ao_eur").mkdir(parents=True)
    (xp / "Custom Scenery" / "scenery_packs.ini").write_text(
        "I\n1000 Version\nSCENERY\n\n"
        "SCENERY_PACK Custom Scenery/Aerosoft - LFMN Nice Cote d Azur X/\n"
        "SCENERY_PACK *GLOBAL_AIRPORTS*\n"
        "SCENERY_PACK Custom Scenery/yOrthoStudio_Overlays/\n"
        "SCENERY_PACK Custom Scenery/zOrthoStudio_+43+007/\n"
        "SCENERY_PACK Custom Scenery/z_ao_eur/\n"
        "SCENERY_PACK Custom Scenery/z_autoortho/\n"
    )
    return xp


def test_the_pack_goes_below_the_built_tiles_and_above_autoortho(tmp_path: Path) -> None:
    xp = _xplane(tmp_path)
    pack = tmp_path / "pack"
    pack.mkdir()
    install_link(xp, pack, LINK_NAME, log=lambda _: None)
    ini = xp / "Custom Scenery" / "scenery_packs.ini"
    names = SceneryPacks.load(ini).names()
    # OrthoStudio XP's own tiles draw over the stream, the stream over AutoOrtho's mesh
    assert names.index("zOrthoStudio_+43+007") < names.index(LINK_NAME) < names.index("z_ao_eur")
    assert (xp / "Custom Scenery" / LINK_NAME).resolve() == pack.resolve()
    assert is_installed(xp, LINK_NAME)
    assert (ini.parent / "scenery_packs.ini.bak").exists()  # OrthoStudio XP's own backup
    uninstall_link(xp, LINK_NAME, log=lambda _: None)
    assert LINK_NAME not in SceneryPacks.load(ini).names()
    assert not (xp / "Custom Scenery" / LINK_NAME).exists()
    assert pack.is_dir()  # the pack itself stays
