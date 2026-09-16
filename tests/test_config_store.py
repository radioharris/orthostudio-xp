"""TOML emitter, load/save round trip, .bak, error codes (spec settings.md section 4.2)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from orthostudio.config import (
    Advanced,
    CoastTransition,
    Essential,
    Expert,
    Settings,
    default_config_path,
    load_settings,
    save_settings,
    toml_dumps,
)
from orthostudio.errors import OsxpError


def test_toml_dumps_scalars_lists_tables_and_escapes() -> None:
    data = {
        "s": 'a "quoted" \\ back\ttab\nnl \x01',
        "b": True,
        "i": -3,
        "f": 0.5,
        "big": 1e21,
        "inf": float("inf"),
        "ninf": float("-inf"),
        "nan": float("nan"),
        "l": [1, "x", 2.5, False],
        "none": None,
        "t": {"k": 1, "sub": {"deep": "v", "weird key!": 2}},
        "empty": {},
    }
    text = toml_dumps(data)
    back = tomllib.loads(text)
    assert back["s"] == data["s"]
    assert back["b"] is True and back["i"] == -3 and back["f"] == 0.5 and back["big"] == 1e21
    assert back["inf"] == float("inf") and back["ninf"] == float("-inf")
    assert back["nan"] != back["nan"]  # NaN
    assert back["l"] == [1, "x", 2.5, False]
    assert "none" not in back
    assert back["t"] == {"k": 1, "sub": {"deep": "v", "weird key!": 2}}
    assert back["empty"] == {}
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "[t]\n" in text and "[t.sub]\n" in text and '"weird key!" = 2' in text


def test_toml_dumps_rejects_unsupported_values() -> None:
    with pytest.raises(TypeError):
        toml_dumps({"x": object()})


def _sample() -> Settings:
    return Settings(
        essential=Essential(
            provider="Arc",
            zoom_level=17,
            coast_transition=CoastTransition(profile="3steps", width_m=[20, 50.5, 30]),
            xplane_dir="/Users/x/X-Plane 12",
        ),
        advanced=Advanced(road_level=3, ratio_water_pct=40, overlay_lod_km=12.5),
        expert=Expert(ovl_exclude_pol=[0, "!.for"], ovl_exclude_net=[22001], mesh_zl=18),
    )


def test_round_trip(tmp_path: Path) -> None:
    for s in (Settings(), _sample()):
        p = tmp_path / "config.toml"
        save_settings(s, p)
        assert load_settings(p) == s
        text = p.read_text(encoding="utf-8")
        assert text.startswith("[essential]\n")
        assert "[advanced]\n" in text and "[expert]\n" in text
        assert "xplane_dir" in text or s.essential.xplane_dir is None


def test_save_keeps_a_bak_and_is_atomic(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "config.toml"
    save_settings(Settings(), p)
    assert p.is_file() and not (tmp_path / "sub" / "config.toml.bak").exists()
    first = p.read_text(encoding="utf-8")
    save_settings(_sample(), p)
    assert (tmp_path / "sub" / "config.toml.bak").read_text(encoding="utf-8") == first
    assert load_settings(p) == _sample()
    assert not list((tmp_path / "sub").glob("*.tmp-*"))


def test_absent_file_gives_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "missing.toml") == Settings()
    assert not (tmp_path / "missing.toml").exists()


def test_default_path_follows_osxp_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    assert default_config_path() == tmp_path / "home" / "config.toml"
    assert load_settings() == Settings()
    save_settings(_sample())
    assert (tmp_path / "home" / "config.toml").is_file()
    assert load_settings() == _sample()


def test_invalid_toml_is_cfg_line_invalid(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("[essential]\nzoom_level = \n", encoding="utf-8")
    with pytest.raises(OsxpError) as info:
        load_settings(p)
    assert info.value.code == "CFG_LINE_INVALID"
    assert info.value.context["line"] == 2


def test_invalid_value_is_cfg_value_invalid(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("[advanced]\nroad_level = 9\n", encoding="utf-8")
    with pytest.raises(OsxpError) as info:
        load_settings(p)
    assert info.value.code == "CFG_VALUE_INVALID"
    assert info.value.context["name"] == "advanced.road_level"
    assert info.value.context["value"] == 9


def test_unknown_keys_are_ignored_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    p = tmp_path / "config.toml"
    p.write_text("[essential]\nzoom_level = 15\nfuture = true\n", encoding="utf-8")
    with caplog.at_level("WARNING", logger="orthostudio.config"):
        s = load_settings(p)
    assert s.essential.zoom_level == 15
    assert any("essential.future" in r.message for r in caplog.records)


def test_write_failure_is_reported(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(OsxpError) as info:
        save_settings(Settings(), blocker / "config.toml")
    assert info.value.code == "CFG_TILE_WRITE_FAILED"
