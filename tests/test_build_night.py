# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The night textures of a tile built: drawn once at full strength, named by its terrain files at
Live's strength, laid by the pack, taken out when built again without them."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from unittest import mock

import httpx
import pytest
from tests.test_live_night import grid_of_streets, texture_at

from orthostudio.errors import OsxpError
from orthostudio.graph import ResolvedInput, RunContext
from orthostudio.imagery.grid import TextureId
from orthostudio.live import night as live_night
from orthostudio.live import nightwork, osxpregions
from orthostudio.model import TileRef
from orthostudio.pipeline import night
from orthostudio.pipeline.pack import PackManifest, pack_dir_name, pack_is_intact, write_pack
from orthostudio.textures.ter import (
    NIGHT_FOLDER,
    TerKind,
    TerParams,
    night_name,
    photo_of,
    ter_center,
    ter_filename,
    ter_text,
    with_night,
)

T = TileRef(43, 7)
TOWN = (43.70, 7.27)


def _texture(lat: float, lon: float) -> TextureId:
    x0, y0 = texture_at(lat, lon)
    return TextureId(til_x=x0, til_y=y0, zl=16, provider="BI")


TOWN_TEX = _texture(*TOWN)
SEA_TEX = _texture(43.20, 7.50)


def _ter(tex: TextureId, kind: TerKind = TerKind.LAND) -> str:
    lat, lon = ter_center(tex)
    return ter_text(tex, kind, lat_med=lat, lon_med=lon, params=TerParams())


def _dsf_dir(root: Path) -> Path:
    """A DSF artefact: the town's and the sea's land, and the town's sea overlay."""
    dsf_dir = root / "dsf"
    (dsf_dir / "terrain").mkdir(parents=True)
    (dsf_dir / f"{T.name}.dsf").write_bytes(b"XPLNEDSF" + b"\0" * 100)
    for tex, kind in ((TOWN_TEX, TerKind.LAND), (SEA_TEX, TerKind.LAND)):
        (dsf_dir / "terrain" / ter_filename(tex, kind)).write_text(_ter(tex, kind), newline="\n")
    sea = ter_filename(TOWN_TEX, TerKind.SEA_OVERLAY)
    (dsf_dir / "terrain" / sea).write_text(_ter(TOWN_TEX, TerKind.SEA_OVERLAY), newline="\n")
    return dsf_dir


def _photo(tex: TextureId) -> str:
    return photo_of(_ter(tex))


def _town():
    return [live_night.Roads.of(grid_of_streets(*TOWN, span=0.03))]


def _run_night(tmp_path: Path, dsf_dir: Path, grid: bytes = b"") -> Path:
    """The night rule run in this process, the town's roads given for every square."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    grid_file = tmp_path / "5d0c41e2b7a9"  # the store's name for it
    grid_file.write_bytes(grid)
    inputs = {name: ResolvedInput(name, None, None) for name in night.ROADS_INPUTS}
    inputs["dsf"] = ResolvedInput("dsf", "d", dsf_dir)
    inputs["grid"] = ResolvedInput("grid", "g", grid_file)
    out, scratch = tmp_path / "out", tmp_path / "scratch"
    out.mkdir()
    scratch.mkdir()
    ctx = RunContext(night.TILE_NIGHT, "k", night.NightParams(tile=T.name), inputs, out, scratch)
    with mock.patch.object(nightwork, "roads_from", lambda *_: _town()), night.night_job(1):
        night.TILE_NIGHT.fn(ctx)
    return out


# -- the terrain files --------------------------------------------------------------------------


def test_the_night_lines_come_right_after_the_photo_and_go_again() -> None:
    text = _ter(TOWN_TEX)
    photo = _photo(TOWN_TEX)
    lit = with_night(text, night_name(photo), 20)
    lines = lit.splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith("BASE_TEX"))
    assert lines[at + 1 : at + 3] == [
        f"LIT_TEX_NOWRAP ../{NIGHT_FOLDER}/{night_name(photo)}",
        "LUMINANCE 20",
    ]
    assert with_night(lit, night_name(photo), 12).count("LUMINANCE") == 1
    assert with_night(lit, None, 20) == text  # no night texture: the file as it was
    assert with_night(lit, night_name(photo), 0) == text  # Live's lights off
    assert with_night(text, None, 20) is not None and with_night(text, None, 20) == text


def test_the_strengths_are_live_s_choices() -> None:
    assert set(live_night.LUMINANCE) == set(live_night.LIGHTS)
    soft, normal, strong = (live_night.LUMINANCE[k] for k in ("soft", "normal", "strong"))
    assert 0 < soft < normal < strong


# -- the night textures ---------------------------------------------------------------------------


def test_the_town_s_texture_gets_its_night_and_the_dark_ones_none(tmp_path: Path) -> None:
    out = _run_night(tmp_path, _dsf_dir(tmp_path))
    made = sorted(p.name for p in (out / NIGHT_FOLDER).iterdir())
    assert made == [night_name(_photo(TOWN_TEX))]  # the sea's land has no light, the overlay none


def test_a_tile_s_night_is_live_s_at_full_strength(tmp_path: Path) -> None:
    """The same drawing as Live's: a tile beside Live's squares shows the same lights."""
    out = _run_night(tmp_path, _dsf_dir(tmp_path))
    live_pack = tmp_path / "live"
    photo = _photo(TOWN_TEX)
    with mock.patch.object(nightwork, "roads_from", lambda *_: _town()):
        assert nightwork.make_night(live_pack, photo, 0, [], None, 1.0, apart=False)
    ours = (out / NIGHT_FOLDER / night_name(photo)).read_bytes()
    assert ours == (live_pack / NIGHT_FOLDER / night_name(photo)).read_bytes()


def test_the_square_s_grid_dims_the_country(tmp_path: Path) -> None:
    """NASA's grid, whatever the name the store gives its file, is read for the tile's square."""
    import numpy as np
    from PIL import Image

    lit = _run_night(tmp_path / "plain", _dsf_dir(tmp_path / "plain"))
    buf = io.BytesIO()
    Image.fromarray(np.zeros((288, 288), np.uint8)).save(buf, "PNG")  # NASA saw nothing here
    dark = _run_night(tmp_path / "dark", _dsf_dir(tmp_path / "dark"), grid=buf.getvalue())
    name = night_name(_photo(TOWN_TEX))
    plain = (lit / NIGHT_FOLDER / name).stat().st_size
    assert (dark / NIGHT_FOLDER / name).read_bytes() != (lit / NIGHT_FOLDER / name).read_bytes()
    assert plain > 0


def test_the_land_s_photos_are_read_from_the_dsf_s_terrain_files(tmp_path: Path) -> None:
    dsf_dir = _dsf_dir(tmp_path)
    assert night.land_textures(dsf_dir) == sorted([_photo(TOWN_TEX), _photo(SEA_TEX)])


# -- the grid -----------------------------------------------------------------------------------


def _grid_ctx(tmp_path: Path) -> RunContext:
    out = tmp_path / "grid.out"
    params = night.NightGridParams(tile="+46+006")
    return RunContext(night.TILE_NIGHT_GRID, "k", params, {}, out, tmp_path)


def test_the_grid_is_taken_from_live_s_folders(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "region" / "night"
    folder.mkdir(parents=True)
    (folder / "+46+006.png").write_bytes(b"\x89PNG grid")
    monkeypatch.setattr(night, "grid_folders", lambda: [tmp_path / "none", folder])
    ctx = _grid_ctx(tmp_path)
    night.TILE_NIGHT_GRID.fn(ctx)
    assert ctx.out.read_bytes() == b"\x89PNG grid"


def _night_zip(squares: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in squares.items():
            zf.writestr(f"night/{name}", data)
    return buf.getvalue()


def _host(monkeypatch, tmp_path: Path, payload: bytes, sha: str | None = None) -> list[str]:
    asked: list[str] = []
    region = osxpregions.OsxpRegion(
        "eur",
        "2026-11-01",
        (),
        night=(
            osxpregions.Zip(
                "eur/night-zips/night_eur.zip",
                len(payload),
                sha or hashlib.sha256(payload).hexdigest(),
                len(payload),
            ),
        ),
    )

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(200, content=payload)

    monkeypatch.setattr(osxpregions, "shipped_host", lambda: ("https://host.test", "token"))
    monkeypatch.setattr(osxpregions, "catalog", lambda *a, **k: {"eur": region})
    monkeypatch.setattr(
        osxpregions, "_client", lambda token: httpx.Client(transport=httpx.MockTransport(answer))
    )
    monkeypatch.setattr("orthostudio.live.pack.live_root", lambda: tmp_path / "live")
    monkeypatch.setattr("orthostudio.live.aoregions.region_of", lambda lat, lon: "eur")
    return asked


def test_a_grid_live_has_not_is_downloaded_once_with_its_region_s(
    tmp_path: Path, monkeypatch
) -> None:
    payload = _night_zip({"+46+006.png": b"\x89PNG six", "+46+007.png": b"\x89PNG seven"})
    asked = _host(monkeypatch, tmp_path, payload)
    ctx = _grid_ctx(tmp_path)
    night.TILE_NIGHT_GRID.fn(ctx)
    assert ctx.out.read_bytes() == b"\x89PNG six"
    assert asked == ["/eur/night-zips/night_eur.zip"]
    assert (tmp_path / "live" / "night" / "+46+007.png").read_bytes() == b"\x89PNG seven"
    again = tmp_path / "again"
    again.mkdir()
    ctx = _grid_ctx(again)
    night.TILE_NIGHT_GRID.fn(ctx)  # Live's own folder has it now
    assert asked == ["/eur/night-zips/night_eur.zip"]


def test_a_damaged_download_stops_the_tile(tmp_path: Path, monkeypatch) -> None:
    _host(monkeypatch, tmp_path, _night_zip({"+46+006.png": b"x"}), sha="0" * 64)
    with pytest.raises(OsxpError) as err:
        night.TILE_NIGHT_GRID.fn(_grid_ctx(tmp_path))
    assert err.value.code == "NET_OFFLINE"


def test_a_build_without_live_s_address_stops_rather_than_drawing_without_the_grid(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(night, "grid_folders", lambda: [])
    monkeypatch.setattr("orthostudio.live.aoregions.region_of", lambda lat, lon: "eur")
    monkeypatch.setattr(osxpregions, "shipped_host", lambda: ("", ""))
    with pytest.raises(OsxpError) as err:
        night.TILE_NIGHT_GRID.fn(_grid_ctx(tmp_path))
    assert err.value.code == "SYS_RESOURCE_MISSING"


def test_a_square_of_no_region_has_an_empty_grid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(night, "grid_folders", lambda: [])
    monkeypatch.setattr("orthostudio.live.aoregions.region_of", lambda lat, lon: None)
    ctx = _grid_ctx(tmp_path)
    night.TILE_NIGHT_GRID.fn(ctx)
    assert ctx.out.read_bytes() == b""


# -- the pack and the strength ------------------------------------------------------------------


def _textures(root: Path) -> Path:
    tex_dir = root / "tex"
    (tex_dir / "textures").mkdir(parents=True)
    for tex in (TOWN_TEX, SEA_TEX):
        (tex_dir / "textures" / _photo(tex)).write_bytes(b"DDS " + b"\1" * 64)
    return tex_dir


def _lines(pack: Path, tex: TextureId) -> list[str]:
    text = (pack / "terrain" / ter_filename(tex, TerKind.LAND)).read_text()
    return [ln for ln in text.splitlines() if ln.startswith(("LIT_TEX", "LUMINANCE"))]


def test_the_pack_lays_the_night_textures_and_built_again_without_takes_them_out(
    tmp_path: Path,
) -> None:
    dsf_dir, tex_dir = _dsf_dir(tmp_path), _textures(tmp_path)
    night_dir = _run_night(tmp_path, dsf_dir)
    out = tmp_path / "tiles"
    pack = out / pack_dir_name(T)
    files = write_pack(
        out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=None,
        night_dir=night_dir, night=20,
    )  # fmt: skip
    lit = night_name(_photo(TOWN_TEX))
    assert files.night == 1
    assert sorted(p.name for p in (pack / NIGHT_FOLDER).iterdir()) == [lit]
    assert _lines(pack, TOWN_TEX) == [f"LIT_TEX_NOWRAP ../{NIGHT_FOLDER}/{lit}", "LUMINANCE 20"]
    assert _lines(pack, SEA_TEX) == []  # no light there: no night texture named
    sea = (pack / "terrain" / ter_filename(TOWN_TEX, TerKind.SEA_OVERLAY)).read_text()
    assert "LIT_TEX" not in sea
    off = write_pack(
        out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=None,
        night_dir=night_dir, night=0,
    )  # fmt: skip
    assert off.night == 1 and _lines(pack, TOWN_TEX) == []  # Live's lights off: files kept
    gone = write_pack(out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=None)
    assert gone.night == 0 and not (pack / NIGHT_FOLDER).exists()
    assert _lines(pack, TOWN_TEX) == []


def test_a_pack_missing_a_night_texture_is_not_intact(tmp_path: Path) -> None:
    dsf_dir, tex_dir = _dsf_dir(tmp_path), _textures(tmp_path)
    night_dir = _run_night(tmp_path, dsf_dir)
    out = tmp_path / "tiles"
    files = write_pack(
        out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=None,
        night_dir=night_dir, night=20,
    )  # fmt: skip
    pack = out / pack_dir_name(T)
    manifest = PackManifest(
        tile=T.name,
        provider="BI",
        zl=16,
        files={
            "dsf": T.dsf_relpath.as_posix(),
            "dsf_size": files.dsf_size,
            "textures": files.textures,
            "terrain": files.terrain,
            "overlay": "",
            "cfg": "",
            "night": files.night,
        },
    )
    from orthostudio.pipeline.pack import write_manifest

    write_manifest(pack, T, manifest)
    assert pack_is_intact(pack, manifest)
    (pack / NIGHT_FOLDER / night_name(_photo(TOWN_TEX))).unlink()
    assert not pack_is_intact(pack, manifest)


def test_live_s_strength_rewrites_the_tiles_with_night_alone(tmp_path: Path) -> None:
    dsf_dir, tex_dir = _dsf_dir(tmp_path), _textures(tmp_path)
    night_dir = _run_night(tmp_path, dsf_dir)
    with_night_out, plain_out = tmp_path / "a", tmp_path / "b"
    write_pack(
        with_night_out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=None,
        night_dir=night_dir, night=20,
    )  # fmt: skip
    write_pack(plain_out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=None)
    lit_pack, plain = with_night_out / pack_dir_name(T), plain_out / pack_dir_name(T)
    before = {p.name: p.read_bytes() for p in (plain / "terrain").iterdir()}
    assert night.set_strength([lit_pack, plain], 12) == 1
    assert _lines(lit_pack, TOWN_TEX)[1] == "LUMINANCE 12"
    assert {p.name: p.read_bytes() for p in (plain / "terrain").iterdir()} == before
    assert night.set_strength([lit_pack, plain], 12) == 0  # already so
    assert night.set_strength([lit_pack], 0) == 1 and _lines(lit_pack, TOWN_TEX) == []
    assert night.set_strength([lit_pack], 34) == 1
    assert _lines(lit_pack, TOWN_TEX)[1] == "LUMINANCE 34"


def test_the_strength_is_live_s_setting(monkeypatch) -> None:
    from orthostudio.live import settings as live_settings

    for on, light, lum in ((True, "soft", 12), (True, "strong", 34), (False, "normal", 0)):
        chosen = live_settings.LiveSettings(night=on, night_light=light)
        monkeypatch.setattr(live_settings, "load_settings", lambda chosen=chosen: chosen)
        assert night.luminance() == lum
