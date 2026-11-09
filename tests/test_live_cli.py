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


def test_on_in_the_beta_unless_live_toml_says_off(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live is on by default (the suite keeps it off,
    tests/conftest.py): ``enabled = false`` switches it off."""
    import subprocess
    import sys

    import orthostudio.live as live

    shipped = subprocess.run(
        [sys.executable, "-c", "import orthostudio.live as l; print(l.ENABLED_BY_DEFAULT)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert shipped.stdout.strip() == "True"
    monkeypatch.setattr(live, "ENABLED_BY_DEFAULT", True)
    assert settings_file() == home / "live.toml"
    assert enabled()  # no file
    settings_file().write_text('[stream]\nlanguage = "fr"\n')
    assert enabled()  # no switch in it
    settings_file().write_text("enabled = false\n")
    assert not enabled()
    settings_file().write_text("enabled = true\n")
    assert enabled()
    settings_file().write_text("enabled = [broken\n")
    assert enabled()  # a file that does not parse: as shipped


def test_switched_off_until_live_toml_says_so_when_off_by_default(home: Path) -> None:
    """As the suite runs: off unless ``enabled = true``."""
    assert not enabled()  # no file
    settings_file().write_text("enabled = false\n")
    assert not enabled()
    settings_file().write_text("enabled = true\n")
    assert enabled()
    settings_file().write_text("enabled = [broken\n")
    assert not enabled()  # a file that does not parse keeps it as by default


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


def test_the_setup_reads_the_regions_into_their_lists(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``lists``, which the Windows setup runs: each region read into its list, its time limit
    taken back at its end (a test, or a setup that finished in time, goes on unharmed)."""
    import json
    import threading

    from orthostudio.live import aoregions, session
    from orthostudio.live.names import region_list_file
    from orthostudio.live.pack import live_root

    settings_file().write_text("enabled = true\n")

    def no_xplane(explicit: Path | None) -> Path:
        raise session.RefusedError("no X-Plane here")

    monkeypatch.setattr(session, "xplane_dir", no_xplane)
    mesh = aoregions.mesh_dir(live_root(), "eur")
    (mesh / "terrain").mkdir(parents=True)
    (mesh / "terrain" / "23296_33152_BI16.ter").write_text("A\n800\nTERRAIN\n")
    (mesh / aoregions.MARKER).write_text(json.dumps({"id": "eur", "release": "0.0.51"}))
    timers: list[list[str]] = []

    class Timer:
        def __init__(self, seconds: float, function: object, args: tuple) -> None:
            self.daemon = False
            self.said = [f"{seconds:.0f} s"]
            timers.append(self.said)

        def start(self) -> None:
            self.said.append("started")

        def cancel(self) -> None:
            self.said.append("cancelled")

    monkeypatch.setattr(threading, "Timer", Timer)
    result = CliRunner().invoke(app, ["lists", "--limit", "300"])
    assert result.exit_code == 0, result.output
    assert "z_ao_eur: 1 textures listed in " in result.output
    assert timers == [["300 s", "started", "cancelled"]]
    assert region_list_file(aoregions.lists_dir(live_root()), mesh).exists()
