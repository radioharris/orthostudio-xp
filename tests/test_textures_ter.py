""".ter terrain definitions: every directive of Ortho4XP and the 39 files of the reference tile."""

from __future__ import annotations

import re

import pytest

from orthostudio.imagery.grid import TextureId
from orthostudio.textures.ter import (
    TerKind,
    TerParams,
    border_mask_filename,
    load_center_size,
    sea_kind,
    takes_decal,
    ter_center,
    ter_filename,
    ter_kind,
    ter_text,
    texture_dds_name,
    with_decal,
)

T = TextureId(til_x=8416, til_y=5984, zl=14, provider="BI")
CENTER = ter_center(T)
DEFAULTS = TerParams()


def _lines(text: str) -> list[str]:
    return text.split("\n")


def test_kinds_tri_type_overlay_and_suffix() -> None:
    assert [k.value for k in TerKind] == ["land", "water", "water_overlay", "sea", "sea_overlay"]
    assert TerKind.LAND.tri_type == 0 and not TerKind.LAND.overlay and TerKind.LAND.suffix == ""
    assert TerKind.WATER_OVERLAY.tri_type == 1 and TerKind.WATER_OVERLAY.suffix == "_water_overlay"
    assert TerKind.SEA.suffix == "_sea" and TerKind.SEA_OVERLAY.suffix == "_sea_overlay"
    for kind in TerKind:
        assert TerKind.of(kind.tri_type, kind.overlay) is kind
    with pytest.raises(ValueError):
        TerKind.of(0, True)


def test_sea_kind_follows_water_tech_and_imprint() -> None:
    assert sea_kind(TerParams()) is TerKind.SEA_OVERLAY
    assert sea_kind(TerParams(water_tech="XP12")) is TerKind.SEA
    assert sea_kind(TerParams(water_tech="XP12", imprint_masks_to_dds=False)) is TerKind.SEA_OVERLAY


def test_names() -> None:
    assert texture_dds_name(T) == "5984_8416_BI14.dds"
    assert border_mask_filename(T) == "5984_8416_ZL14.png"
    assert ter_filename(T, TerKind.LAND) == "5984_8416_BI14.ter"
    assert ter_filename(T, TerKind.WATER_OVERLAY) == "5984_8416_BI14_water_overlay.ter"
    assert ter_filename(T, TerKind.SEA) == "5984_8416_BI14_sea.ter"
    assert ter_filename(T, TerKind.SEA_OVERLAY) == "5984_8416_BI14_sea_overlay.ter"


def test_center_and_size_match_the_reference_values() -> None:
    lat, lon = CENTER
    assert f"{lat:.5f} {lon:.5f}" == "43.45292 5.09766"
    assert load_center_size(lat, 14) == 28410
    assert load_center_size(ter_center(TextureId(8416, 5952, 14, "BI"))[0], 14) == 28170


def test_land_text_is_the_reference_one() -> None:
    text = ter_text(T, TerKind.LAND, lat_med=CENTER[0], lon_med=CENTER[1], params=DEFAULTS)
    assert text == (
        "A\n800\nTERRAIN\n\n"
        "LOAD_CENTER 43.45292 5.09766 28410 4096\n"
        "BASE_TEX_NOWRAP ../textures/5984_8416_BI14.dds\n"
        "NO_ALPHA\n"
    )


def _text(kind: TerKind, **params: object) -> list[str]:
    p = TerParams(**params)  # type: ignore[arg-type]
    return _lines(ter_text(T, kind, lat_med=CENTER[0], lon_med=CENTER[1], params=p))


def test_sea_overlay_and_water_overlay_directives() -> None:
    assert _text(TerKind.SEA_OVERLAY)[6:] == ["WET", "NO_SHADOW", ""]
    assert _text(TerKind.WATER_OVERLAY)[6:] == [
        "BORDER_TEX ../textures/water_transition.png",
        "WET",
        "NO_SHADOW",
        "",
    ]


def test_xp12_water_color_mask() -> None:
    assert _text(TerKind.SEA, water_tech="XP12")[6:] == ["WATER_COLOR_MASK", "WET", "NO_SHADOW", ""]
    assert _text(TerKind.WATER, water_tech="XP12")[6:] == [
        "WATER_COLOR_MASK",
        "WET",
        "NO_SHADOW",
        "",
    ]


def test_external_mask_when_not_imprinted() -> None:
    lines = _text(TerKind.SEA_OVERLAY, imprint_masks_to_dds=False, mask_zl=14)
    assert lines[6:] == [
        "LOAD_CENTER_BORDER 43.45292 5.09766 28410 4096",
        "BORDER_TEX ../textures/5984_8416_ZL14.png",
        "WET",
        "NO_SHADOW",
        "",
    ]
    t16 = TextureId(til_x=33664, til_y=23936, zl=16, provider="BI")
    lat, lon = ter_center(t16)
    text = ter_text(
        t16,
        TerKind.SEA_OVERLAY,
        lat_med=lat,
        lon_med=lon,
        params=TerParams(imprint_masks_to_dds=False),
    )
    assert re.search(r"^LOAD_CENTER_BORDER [-\d.]+ [-\d.]+ \d+ 1024$", text, re.M)
    assert "BORDER_TEX ../textures/23936_33664_ZL16.png" in text


def test_decal_on_land_only_unless_the_sea_is_asked_for() -> None:
    """Ortho4XP writes the decal on land and sea alike; a user asked for the land alone
    (2026-09-17), so the sea has it only with ``decal_on_sea``. Inland water never has it."""
    decal = "DECAL_LIB lib/g10/decals/maquify_2_green_key.dcl"
    assert _text(TerKind.LAND, use_decal_on_terrain=True)[6:] == [decal, "NO_ALPHA", ""]
    assert decal not in _text(TerKind.SEA_OVERLAY, use_decal_on_terrain=True)
    assert decal not in _text(TerKind.SEA, use_decal_on_terrain=True, water_tech="XP12")
    assert decal not in _text(TerKind.WATER_OVERLAY, use_decal_on_terrain=True, decal_on_sea=True)
    assert decal in _text(TerKind.SEA_OVERLAY, use_decal_on_terrain=True, decal_on_sea=True)
    assert decal in _text(TerKind.LAND, use_decal_on_terrain=True, decal_on_sea=True)
    # the sea asked for, order: after BORDER_TEX / WATER_COLOR_MASK, before WET
    lines = _text(TerKind.SEA, use_decal_on_terrain=True, decal_on_sea=True, water_tech="XP12")
    assert lines[6:9] == ["WATER_COLOR_MASK", decal, "WET"]


def test_shadows_and_test_texture() -> None:
    assert _text(TerKind.LAND, terrain_casts_shadows=False)[6:] == ["NO_ALPHA", "NO_SHADOW", ""]
    assert (
        _text(TerKind.LAND, use_test_texture=True)[5]
        == "BASE_TEX_NOWRAP ../textures/test_texture.dds"
    )


def test_five_decimals_and_int_truncation() -> None:
    t = TextureId(til_x=0, til_y=0, zl=14, provider="X")
    lat, lon = ter_center(t)
    line = _lines(ter_text(t, TerKind.LAND, lat_med=lat, lon_med=lon, params=DEFAULTS))[4]
    assert line == f"LOAD_CENTER {lat:.5f} {lon:.5f} {load_center_size(lat, 14)} 4096"
    assert lon == -180.0 + 8 * 360 / 2**14


def test_the_pack_writes_the_decal_where_ter_text_would() -> None:
    """The pack adds the decal line to terrain files written without it, as setdecal rewrites a
    built tile (its author asked for the choice, 2026-09-25): for every kind, with and without the
    sea, the result is the very file ``ter_text`` writes with decals on."""
    lat, lon = CENTER
    for kind in TerKind:
        assert ter_kind(ter_filename(T, kind)) is kind
        plain = ter_text(T, kind, lat_med=lat, lon_med=lon, params=DEFAULTS)
        assert "DECAL_LIB" not in plain and with_decal(plain, "") == plain
        for on_sea in (False, True):
            on = TerParams(use_decal_on_terrain=True, decal_on_sea=on_sea)
            decal = "maquify_2_green_key.dcl" if takes_decal(kind, on_sea=on_sea) else ""
            written = ter_text(T, kind, lat_med=lat, lon_med=lon, params=on)
            assert with_decal(plain, decal) == written, (kind, on_sea)
            assert with_decal(written, "") == plain  # off again: the line goes
    assert not takes_decal(TerKind.WATER_OVERLAY, on_sea=True)  # inland water never has one


def test_with_decal_replaces_the_line_and_keeps_every_byte_else() -> None:
    lat, lon = CENTER
    on = TerParams(use_decal_on_terrain=True)
    land = ter_text(T, TerKind.LAND, lat_med=lat, lon_med=lon, params=on)
    grass = with_decal(land, "grass_and_stony_dirt_1.dcl")
    assert grass == land.replace("maquify_2_green_key.dcl", "grass_and_stony_dirt_1.dcl")
    assert with_decal(grass, "grass_and_stony_dirt_1.dcl") == grass
    crlf = land.replace("\n", "\r\n")  # a file edited on Windows keeps its line ends
    assert with_decal(crlf, "") == ter_text(
        T, TerKind.LAND, lat_med=lat, lon_med=lon, params=DEFAULTS
    ).replace("\n", "\r\n")
