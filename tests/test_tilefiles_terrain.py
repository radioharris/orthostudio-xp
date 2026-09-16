"""Readers of Ortho4XP ``.ter`` files: name parsing, content checks, texture listing."""

from __future__ import annotations

from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.tilefiles import (
    TerKind,
    TextureId,
    list_textures,
    parse_ter_name,
    read_ter,
    ter_stem,
)
from orthostudio.tilefiles._grid import load_center

REPO = Path(__file__).resolve().parents[1]
T_6016 = TextureId(8448, 6016, 14, "BI")


def _ter_text(t: TextureId, kind: TerKind, *, base: str | None = None) -> str:
    lat, lon, size = load_center(t)
    base = base or f"../textures/{ter_stem(t)}.dds"
    tail = "WET\nNO_SHADOW\n" if kind.is_water else "NO_ALPHA\n"
    head = f"A\n800\nTERRAIN\n\nLOAD_CENTER {lat:.5f} {lon:.5f} {size} 4096\n"
    return f"{head}BASE_TEX_NOWRAP {base}\n{tail}"


# ---------------------------------------------------------------- names


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("6016_8448_BI14.ter", TerKind.LAND),
        ("6016_8448_BI14_water.ter", TerKind.WATER),
        ("6016_8448_BI14_water_overlay.ter", TerKind.WATER_OVERLAY),
        ("6016_8448_BI14_sea.ter", TerKind.SEA),
        ("6016_8448_BI14_sea_overlay.ter", TerKind.SEA_OVERLAY),
    ],
)
def test_parse_ter_name_suffixes(name: str, kind: TerKind) -> None:
    assert parse_ter_name(name) == (T_6016, kind)
    assert kind.suffix == name[len("6016_8448_BI14") : -4]


def test_parse_ter_name_provider_ending_with_digits() -> None:
    # PDOK18 at ZL16: "PDOK1816"; the 1-digit split (PDOK181 at ZL6) cannot hold til_x 33792
    assert parse_ter_name("24064_33792_PDOK1816.ter") == (
        TextureId(33792, 24064, 16, "PDOK18"),
        TerKind.LAND,
    )
    assert parse_ter_name("0_0_Arc@12.ter")[0] == TextureId(0, 0, 12, "Arc@")


def test_parse_ter_name_ambiguous_split_uses_hints() -> None:
    # "X114" at (0, 0): X11 at ZL4 and X1 at ZL14 are both geometrically possible
    assert parse_ter_name("0_0_X114.ter")[0] == TextureId(0, 0, 14, "X1")  # prefers 10..19
    assert parse_ter_name("0_0_X114.ter", zl=4)[0] == TextureId(0, 0, 4, "X11")
    lon_zl4 = load_center(TextureId(0, 0, 4, "X11"))[1]
    assert parse_ter_name("0_0_X114.ter", lon_med=lon_zl4)[0] == TextureId(0, 0, 4, "X11")
    lon_zl14 = load_center(TextureId(0, 0, 14, "X1"))[1]
    assert parse_ter_name("0_0_X114.ter", lon_med=lon_zl14)[0] == TextureId(0, 0, 14, "X1")


@pytest.mark.parametrize(
    "name",
    [
        "water_transition.ter",
        "6016_8448_BI.ter",
        "6017_8448_BI14.ter",
        "6016_8448_BI14_sea.txt",
        "6016_8448_BI14_overlay_sea.ter",
        "6016_8448_BI99.ter",
        "6016_8448_BI14_lava.ter",
    ],
)
def test_parse_ter_name_rejects(name: str) -> None:
    with pytest.raises(OsxpError) as exc:
        parse_ter_name(name)
    assert exc.value.code == "DSF_SOURCE_CORRUPTED"


def test_ter_kind_flags() -> None:
    assert [k.is_overlay for k in TerKind] == [False, False, True, False, True]
    assert [k.is_water for k in TerKind] == [False, True, True, True, True]


# ---------------------------------------------------------------- content


def test_read_ter_synthetic(tmp_path: Path) -> None:
    p = tmp_path / "6016_8448_BI14_sea_overlay.ter"
    p.write_text(_ter_text(T_6016, TerKind.SEA_OVERLAY), encoding="ascii")
    ter = read_ter(p)
    assert (ter.texture, ter.kind) == (T_6016, TerKind.SEA_OVERLAY)
    assert (round(ter.lat_med, 5), round(ter.lon_med, 5), ter.size_m) == (42.94034, 5.80078, 28649)
    assert ter.texture_stem == "6016_8448_BI14"
    assert ter.border_tex is None
    assert ter.directives[-2:] == ("WET", "NO_SHADOW")


@pytest.mark.parametrize(
    ("name", "text", "reason"),
    [
        ("6016_8448_BI14.ter", "A\n850\nTERRAIN\n", "header"),
        ("6016_8448_BI14.ter", "A\n800\nTERRAIN\n\nBASE_TEX_NOWRAP x.dds\n", "LOAD_CENTER"),
        ("6016_8448_BI14.ter", "A\n800\nTERRAIN\n\nLOAD_CENTER 1 2\nBASE_TEX_NOWRAP x\n", "lat"),
        ("6016_8448_BI14.ter", _ter_text(TextureId(8448, 6000, 14, "BI"), TerKind.LAND), "match"),
        ("6016_8448_BI14.ter", _ter_text(T_6016, TerKind.LAND, base="../textures/a.dds"), "match"),
        (
            "6016_8448_BI14.ter",
            _ter_text(T_6016, TerKind.LAND).replace(" 4096\n", " 2048\n"),
            "match",
        ),
    ],
)
def test_read_ter_rejects_inconsistent_content(
    tmp_path: Path, name: str, text: str, reason: str
) -> None:
    p = tmp_path / name
    p.write_text(text, encoding="ascii")
    with pytest.raises(OsxpError) as exc:
        read_ter(p)
    assert exc.value.code == "DSF_SOURCE_CORRUPTED"
    assert reason in exc.value.message


def test_read_ter_accepts_test_texture(tmp_path: Path) -> None:
    p = tmp_path / "6016_8448_BI14.ter"
    p.write_text(_ter_text(T_6016, TerKind.LAND, base="../textures/test_texture.dds"))
    assert read_ter(p).texture == T_6016


def test_list_textures_groups_kinds(tmp_path: Path) -> None:
    (tmp_path / "terrain").mkdir()
    t2 = TextureId(8432, 6000, 14, "BI")
    for t, kind in [(T_6016, TerKind.SEA_OVERLAY), (T_6016, TerKind.LAND), (t2, TerKind.LAND)]:
        (tmp_path / "terrain" / f"{ter_stem(t)}{kind.suffix}.ter").write_text(_ter_text(t, kind))
    assert list_textures(tmp_path) == [
        (t2, (TerKind.LAND,)),
        (T_6016, (TerKind.LAND, TerKind.SEA_OVERLAY)),
    ]


def test_list_textures_needs_terrain_dir(tmp_path: Path) -> None:
    with pytest.raises(OsxpError) as exc:
        list_textures(tmp_path)
    assert exc.value.code == "SYS_WORKING_DIR_INVALID"


# ---------------------------------------------------------------- frozen small fixture


# ---------------------------------------------------------------- writer


def test_ter_kind_values_match_the_p1_writer() -> None:
    ter = pytest.importorskip("orthostudio.textures.ter")
    theirs = {k.name: k.value for k in ter.TerKind}
    assert theirs == {k.name: k.value for k in TerKind}
    assert [ter.TerKind(k.value) for k in TerKind] == list(ter.TerKind)
