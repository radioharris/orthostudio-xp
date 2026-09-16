"""The scenery pack and its installation (spec ``pipeline-build.md`` 2.3, 3): layout, manifest
round trip, ``.bak``, stale files, intact checks, install receipt on a temporary Custom Scenery."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from orthostudio.install import packs
from orthostudio.install.scenery_packs import SceneryPacks
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import (
    ArtefactEntry,
    PackManifest,
    install_is_intact,
    install_receipt,
    pack_dir_name,
    pack_is_intact,
    read_manifest,
    write_pack,
)

T = TileRef(43, 5)
INI = (
    b"I\n1000 Version\nSCENERY\n\n"
    b"SCENERY_PACK Custom Scenery/Airport A/\n"
    b"SCENERY_PACK *GLOBAL_AIRPORTS*\n"
    b"SCENERY_PACK Custom Scenery/yAutoOrtho_Overlays/\n"
    b"SCENERY_PACK Custom Scenery/z_autoortho/\n"
)


@pytest.fixture
def artefacts(tmp_path: Path) -> dict[str, Path]:
    dsf_dir = tmp_path / "dsf"
    (dsf_dir / "terrain").mkdir(parents=True)
    (dsf_dir / f"{T.name}.dsf").write_bytes(b"XPLNEDSF" + b"\0" * 100)
    (dsf_dir / "terrain" / "6000_8448_BI14.ter").write_text("A\n800\nTERRAIN\n")
    (dsf_dir / "terrain" / "6000_8448_BI14_sea_overlay.ter").write_text("A\n800\nTERRAIN\nWET\n")
    tex_dir = tmp_path / "tex"
    (tex_dir / "textures").mkdir(parents=True)
    (tex_dir / "textures" / "6000_8448_BI14.dds").write_bytes(b"DDS " + b"\1" * 64)
    (tex_dir / "textures" / "water_transition.png").write_bytes(b"\x89PNG")
    overlay = tmp_path / "overlay.dsf"
    overlay.write_bytes(b"XPLNEDSF" + b"\2" * 50)
    return {"dsf": dsf_dir, "tex": tex_dir, "overlay": overlay}


def test_write_pack_writes_the_tile_settings(tmp_path: Path, artefacts: dict[str, Path]) -> None:
    """``tile_settings.cfg`` next to ``orthostudio.toml``: the 44 tile variables the build
    consumed, listed in the manifest and checked by ``pack_is_intact``."""
    from orthostudio.tilefiles import parse_tile_cfg, tile_cfg_text, tile_cfg_values

    cfg = tile_cfg_text(tile_cfg_values(provider="BI", zl=14))
    out = tmp_path / "out"
    files = write_pack(
        out, T, dsf_dir=artefacts["dsf"], textures_dir=artefacts["tex"],
        overlay_file=artefacts["overlay"], tile_cfg=cfg,
    )  # fmt: skip
    assert files.cfg == files.pack_dir / "tile_settings.cfg"
    assert files.cfg.read_text() == cfg and "default_zl=14\n" in cfg
    again = write_pack(
        out, T, dsf_dir=artefacts["dsf"], textures_dir=artefacts["tex"],
        overlay_file=artefacts["overlay"], tile_cfg=cfg,
    )  # fmt: skip
    assert again.changed == 0
    settings = parse_tile_cfg(files.cfg.read_text(), strict=True)
    assert (settings["default_website"], settings["default_zl"]) == ("BI", 14)
    manifest = PackManifest(T.name, "BI", 14, {}, {
        "dsf": T.dsf_relpath.as_posix(), "dsf_size": files.dsf_size, "textures": 1,
        "terrain": 2, "overlay": "", "cfg": "tile_settings.cfg",
    })  # fmt: skip
    (files.pack_dir / "orthostudio.toml").write_text(manifest.to_toml())
    assert pack_is_intact(files.pack_dir, manifest)
    files.cfg.unlink()
    assert not pack_is_intact(files.pack_dir, manifest)


def test_write_pack_layout_links_and_bak(tmp_path: Path, artefacts: dict[str, Path]) -> None:
    out = tmp_path / "out"
    files = write_pack(
        out, T, dsf_dir=artefacts["dsf"], textures_dir=artefacts["tex"],
        overlay_file=artefacts["overlay"],
    )  # fmt: skip
    pack = out / pack_dir_name(T)
    assert files.pack_dir == pack and files.textures == 1 and files.terrain == 2
    assert files.changed == 6 and files.removed == 0  # dsf, 2 ter, dds, png, overlay
    dsf = pack / T.dsf_relpath
    assert dsf.read_bytes() == (artefacts["dsf"] / f"{T.name}.dsf").read_bytes()
    assert os.path.samefile(dsf, artefacts["dsf"] / f"{T.name}.dsf")  # hard link
    assert (pack / "textures" / "6000_8448_BI14.dds").is_file()
    assert (pack / "textures" / "water_transition.png").is_file()
    assert files.overlay == out / "yOrthoStudio_Overlays" / T.dsf_relpath
    assert files.overlay.is_file()
    # unchanged rerun: nothing rewritten
    again = write_pack(
        out, T, dsf_dir=artefacts["dsf"], textures_dir=artefacts["tex"],
        overlay_file=artefacts["overlay"],
    )  # fmt: skip
    assert again.changed == 0 and again.removed == 0
    # a new DSF with different bytes: .bak of the old one, stale files removed
    (artefacts["dsf"] / f"{T.name}.dsf").unlink()
    (artefacts["dsf"] / f"{T.name}.dsf").write_bytes(b"XPLNEDSF" + b"\3" * 100)
    (artefacts["dsf"] / "terrain" / "6000_8448_BI14_sea_overlay.ter").unlink()
    (artefacts["tex"] / "textures" / "6000_8448_BI14.dds").unlink()
    (artefacts["tex"] / "textures" / "6016_8448_BI14.dds").write_bytes(b"DDS " + b"\4" * 64)
    third = write_pack(
        out, T, dsf_dir=artefacts["dsf"], textures_dir=artefacts["tex"],
        overlay_file=artefacts["overlay"],
    )  # fmt: skip
    assert (pack / T.dsf_relpath.parent / f"{T.name}.dsf.bak").is_file()
    assert third.removed == 2 and third.terrain == 1 and third.textures == 1
    assert sorted(p.name for p in (pack / "textures").glob("*.dds")) == ["6016_8448_BI14.dds"]
    # the DSF keeps its .bak; a replaced DDS does not (P2a review), and stray .bak are swept
    (pack / "textures" / "old.dds.bak").write_bytes(b"x")
    (artefacts["tex"] / "textures" / "6016_8448_BI14.dds").unlink()
    (artefacts["tex"] / "textures" / "6016_8448_BI14.dds").write_bytes(b"DDS " + b"\5" * 64)
    fourth = write_pack(
        out, T, dsf_dir=artefacts["dsf"], textures_dir=artefacts["tex"],
        overlay_file=artefacts["overlay"],
    )  # fmt: skip
    assert fourth.changed == 1 and fourth.removed == 1
    assert sorted(p.name for p in (pack / "textures").iterdir()) == [
        "6016_8448_BI14.dds",
        "water_transition.png",
    ]
    # copy mode when links are refused
    out2 = tmp_path / "out2"
    write_pack(out2, T, dsf_dir=artefacts["dsf"], textures_dir=None, overlay_file=None, link=False)
    p = out2 / pack_dir_name(T) / T.dsf_relpath
    assert p.is_file() and not os.path.samefile(p, artefacts["dsf"] / f"{T.name}.dsf")


def test_manifest_round_trip_and_intact(tmp_path: Path) -> None:
    m = PackManifest(
        tile="+43+005",
        provider="BI",
        zl=14,
        artefacts={
            "dsf": ArtefactEntry("a" * 64, "b" * 64, "tile.dsf@1"),
            "mesh": ArtefactEntry("c" * 64, "d" * 64, "orthostudio.mesh@1"),
        },
        files={
            "dsf": "Earth nav data/+40+000/+43+005.dsf",
            "dsf_size": 108,
            "textures": 1,
            "terrain": 2,
            "overlay": "../yOrthoStudio_Overlays/x.dsf",
        },
    )
    text = m.to_toml()
    assert text.startswith('format = "osxp-pack-1"') and "\n[artefacts]\n" in text
    back = PackManifest.from_toml(text)
    assert back == m and back.keys == {"dsf": "a" * 64, "mesh": "c" * 64}
    with pytest.raises(ValueError):
        PackManifest.from_toml('format = "nope"\n')
    pack = tmp_path / "zOrthoStudio_+43+005"
    (pack / "Earth nav data" / "+40+000").mkdir(parents=True)
    (pack / "Earth nav data" / "+40+000" / "+43+005.dsf").write_bytes(b"x" * 108)
    (pack / "textures").mkdir()
    (pack / "textures" / "a.dds").write_bytes(b"d")
    (pack / "terrain").mkdir()
    for n in ("a.ter", "b.ter"):
        (pack / "terrain" / n).write_text("t")
    (pack / "orthostudio.toml").write_text(text)
    assert not pack_is_intact(pack, m)  # the overlay is missing
    (tmp_path / "yOrthoStudio_Overlays").mkdir()
    (tmp_path / "yOrthoStudio_Overlays" / "x.dsf").write_bytes(b"o")
    assert pack_is_intact(pack, m)
    assert read_manifest(pack) == m
    (pack / "terrain" / "b.ter").unlink()
    assert not pack_is_intact(pack, m)


def test_install_receipt_orders_ini_and_registers(
    tmp_path: Path, artefacts: dict[str, Path], monkeypatch: pytest.MonkeyPatch, link_kind: str
) -> None:
    monkeypatch.setattr(packs, "xplane_running", lambda: False)
    out = tmp_path / "out"
    write_pack(
        out, T, dsf_dir=artefacts["dsf"], textures_dir=artefacts["tex"],
        overlay_file=artefacts["overlay"],
    )  # fmt: skip
    pack = out / pack_dir_name(T)
    m = PackManifest(
        "+43+005",
        "BI",
        14,
        {"dsf": ArtefactEntry("a" * 64, "b" * 64, "tile.dsf@1")},
        {
            "dsf": T.dsf_relpath.as_posix(),
            "dsf_size": 108,
            "textures": 1,
            "terrain": 2,
            "overlay": "../yOrthoStudio_Overlays/" + T.dsf_relpath.as_posix(),
        },
    )
    (pack / "orthostudio.toml").write_text(m.to_toml())
    cs = tmp_path / "X-Plane 12" / "Custom Scenery"
    cs.mkdir(parents=True)
    (cs / "scenery_packs.ini").write_bytes(INI)
    lib = tmp_path / "library.sqlite"
    receipt = install_receipt(pack, cs, tile=T, library_path=lib)
    assert receipt["target"] == str(cs / "zOrthoStudio_+43+005")
    assert receipt["overlay_target"] == str(cs / "yOrthoStudio_Overlays")
    for name in ("zOrthoStudio_+43+005", "yOrthoStudio_Overlays"):
        assert packs.is_link(cs / name) and os.path.isjunction(cs / name) == (
            link_kind == "junction"
        )
    names = SceneryPacks.load(cs / "scenery_packs.ini").names()
    assert names == [
        "Airport A",
        "*GLOBAL_AIRPORTS*",
        "yAutoOrtho_Overlays",
        "yOrthoStudio_Overlays",
        "zOrthoStudio_+43+005",
        "z_autoortho",
    ]
    # one save for both packs: the .bak is the state before OrthoStudio XP touched the file
    assert (cs / "scenery_packs.ini.bak").read_bytes() == INI
    assert install_is_intact(receipt)
    from orthostudio.install import Library

    with Library(lib) as library:
        rows = library.list()
    assert [(r.tile.name, r.kind, r.provider, r.zl, r.built_by) for r in rows] == [
        ("+43+005", "ortho", "BI", 14, "osxp"),
        ("+43+005", "overlay", "", 0, "osxp"),
    ]
    assert rows[0].keys == {"dsf": "a" * 64}
    # idempotent, then a removed link is detected
    receipt2 = install_receipt(pack, cs, tile=T, library_path=lib)
    assert receipt2 == receipt
    os.unlink(cs / "zOrthoStudio_+43+005")
    assert not install_is_intact(receipt)
    assert json.dumps(receipt)  # JSON-serialisable receipt


def test_the_tile_cfg_is_complete_ordered_and_readable_by_its_parser() -> None:
    """``Ortho4XP_<tile>.cfg``: every tile variable, in Ortho4XP's order, read back the same."""
    from orthostudio.tilefiles import (
        TILE_PARAMETERS,
        parse_tile_cfg,
        tile_cfg_text,
        tile_cfg_values,
    )

    values = tile_cfg_values(
        provider="BI",
        zl=16,
        overrides={"masks_width": [50, 100, 150], "masking_mode": "3steps", "curvature_tol": 3},
    )
    text = tile_cfg_text({**values, "keep_objects": False})
    assert [line.split("=", 1)[0] for line in text.splitlines()] == list(TILE_PARAMETERS)
    assert "masks_width=[50, 100, 150]\n" in text and "masking_mode=3steps\n" in text
    assert "default_website=BI\n" in text and "default_zl=16\n" in text
    assert "curvature_tol=3\n" in text and "zone_list=[]\n" in text
    back = parse_tile_cfg(text, strict=True)
    assert back["masks_width"] == [50.0, 100.0, 150.0] and back["clean_bad_geometries"] is True
    assert "keep_objects" not in text  # an overlay setting, not a tile variable
    with pytest.raises(KeyError):
        tile_cfg_values(provider="BI", zl=14, overrides={"mask_widht": 1})
