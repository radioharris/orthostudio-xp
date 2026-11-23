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

from orthostudio.dem.xplane import XP12_INPUTS
from orthostudio.errors import OsxpError
from orthostudio.graph import ResolvedInput, RunContext
from orthostudio.imagery.grid import TextureId
from orthostudio.live import night as live_night
from orthostudio.live import nightwork, osxpregions
from orthostudio.model import TileRef
from orthostudio.pipeline import night
from orthostudio.pipeline.pack import (
    PackManifest,
    pack_dir_name,
    pack_is_intact,
    read_manifest,
    write_manifest,
    write_pack,
)
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


def _run_night(tmp_path: Path, dsf_dir: Path, grid: Path | None = None) -> Path:
    """The night rule run in this process, the town's roads given for every square."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    inputs = {name: ResolvedInput(name, None, None) for name in XP12_INPUTS}
    inputs["dsf"] = ResolvedInput("dsf", "d", dsf_dir)
    out, scratch = tmp_path / "out", tmp_path / "scratch"
    out.mkdir()
    scratch.mkdir()
    ctx = RunContext(night.TILE_NIGHT, "k", night.NightParams(tile=T.name), inputs, out, scratch)
    with (
        mock.patch.object(nightwork, "roads_from", lambda *_: _town()),
        mock.patch.object(night, "grid_of", lambda tile: grid),
        night.night_job(1),
    ):
        night.TILE_NIGHT.fn(ctx)
    return out


# -- the terrain files --------------------------------------------------------------------------


def test_the_night_lines_come_right_after_the_photo_and_go_again() -> None:
    text = _ter(TOWN_TEX)
    lit_name = night_name(_photo(TOWN_TEX))
    lit = with_night(text, lit_name, 20)
    lines = lit.splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith("BASE_TEX"))
    assert lines[at + 1 : at + 3] == [
        f"LIT_TEX_NOWRAP ../{NIGHT_FOLDER}/{lit_name}",
        "LUMINANCE 20",
    ]
    assert with_night(lit, lit_name, 12).count("LUMINANCE") == 1
    assert with_night(lit, None) == text  # no night texture: the file as it was


# -- the night textures ---------------------------------------------------------------------------


def test_the_town_s_texture_gets_live_s_night_at_full_strength_and_the_dark_ones_none(
    tmp_path: Path,
) -> None:
    """The same drawing as Live's: a tile beside Live's squares shows the same lights."""
    out = _run_night(tmp_path, _dsf_dir(tmp_path))
    photo = _photo(TOWN_TEX)
    made = sorted(p.name for p in (out / NIGHT_FOLDER).iterdir())
    assert made == [night_name(photo)]  # the sea's land has no light, the overlay none
    live_pack = tmp_path / "live"
    with mock.patch.object(nightwork, "roads_from", lambda *_: _town()):
        assert nightwork.make_night(live_pack, photo, 0, [], None, 1.0, apart=False)
    ours = (out / NIGHT_FOLDER / night_name(photo)).read_bytes()
    assert ours == (live_pack / NIGHT_FOLDER / night_name(photo)).read_bytes()


def test_the_square_s_grid_dims_the_country(tmp_path: Path) -> None:
    import numpy as np
    from PIL import Image

    grid = tmp_path / f"{T.name}.png"
    Image.fromarray(np.zeros((288, 288), np.uint8)).save(grid)  # a grid without light
    plain = _run_night(tmp_path / "plain", _dsf_dir(tmp_path / "plain"))
    dark = _run_night(tmp_path / "dark", _dsf_dir(tmp_path / "dark"), grid)
    name = night_name(_photo(TOWN_TEX))
    assert (dark / NIGHT_FOLDER / name).read_bytes() != (plain / NIGHT_FOLDER / name).read_bytes()


# -- the grid -----------------------------------------------------------------------------------


def _night_zip(squares: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in squares.items():
            zf.writestr(f"night/{name}", data)
    return buf.getvalue()


def _region(region: str, data: bytes, sha: str | None = None) -> osxpregions.OsxpRegion:
    z = osxpregions.Zip(
        f"{region}/night-zips/night_{region}.zip",
        len(data),
        sha or hashlib.sha256(data).hexdigest(),
        len(data),
    )
    return osxpregions.OsxpRegion(region, "release", (), night=(z,))


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Live's host with one night zip per region, nothing of Live's on the disk; returns what was
    asked of it and the regions' zips to fill."""
    asked: list[str] = []
    zips: dict[str, bytes] = {}

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(200, content=zips[request.url.path.split("/")[1]])

    monkeypatch.setattr(osxpregions, "shipped_host", lambda: ("https://host.test", "token"))
    monkeypatch.setattr(
        osxpregions, "catalog", lambda *a, **k: {r: _region(r, d) for r, d in zips.items()}
    )
    monkeypatch.setattr(
        osxpregions, "_client", lambda token: httpx.Client(transport=httpx.MockTransport(answer))
    )
    monkeypatch.setattr("orthostudio.live.pack.live_root", lambda: tmp_path / "live")
    monkeypatch.setattr(night, "grids_folder", lambda: tmp_path / "grids")
    return asked, zips


def test_the_grid_is_taken_from_live_s_folders(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "region" / "night"
    folder.mkdir(parents=True)
    (folder / "+46+006.png").write_bytes(b"\x89PNG grid")
    monkeypatch.setattr(nightwork, "grid_dirs", lambda regions: [tmp_path / "none", folder])
    monkeypatch.setattr(night, "grids_folder", lambda: tmp_path / "grids")
    assert night.grid_of(TileRef(46, 6)) == folder / "+46+006.png"


def test_a_grid_live_has_not_is_downloaded_once_with_its_region_s(host) -> None:
    asked, zips = host
    zips["eur"] = _night_zip({"+46+006.png": b"\x89PNG six", "+46+007.png": b"\x89PNG seven"})
    assert night.grid_of(TileRef(46, 6)).read_bytes() == b"\x89PNG six"
    assert night.grid_of(TileRef(46, 7)).read_bytes() == b"\x89PNG seven"  # already here
    assert night.grid_of(TileRef(47, 6)) is None  # its region's had none: not asked again
    assert asked == ["/eur/night-zips/night_eur.zip"]


def test_another_data_folder_asks_for_its_region_s_grids_again(host, tmp_path, monkeypatch) -> None:
    asked, zips = host
    zips["eur"] = _night_zip({"+46+006.png": b"\x89PNG six"})
    assert night.grid_of(TileRef(46, 6)) is not None
    monkeypatch.setattr(night, "grids_folder", lambda: tmp_path / "other")
    assert night.grid_of(TileRef(46, 6)) == tmp_path / "other" / "+46+006.png"
    assert len(asked) == 2


def test_the_grid_comes_from_osxp_s_own_region_of_the_square(host) -> None:
    """Cairo's grid comes from OrthoStudio XP's own region of the square, Africa."""
    asked, zips = host
    zips["afr"] = _night_zip({"+30+031.png": b"\x89PNG cairo"})
    assert night.grid_of(TileRef(30, 31)).read_bytes() == b"\x89PNG cairo"
    assert asked == ["/afr/night-zips/night_afr.zip"]


def test_a_square_of_open_sea_has_no_grid_and_asks_nothing(host) -> None:
    asked, _zips = host
    assert night.grid_of(TileRef(40, -40)) is None
    assert asked == []


def test_a_damaged_download_stops_the_tile_and_is_tried_again(host, monkeypatch) -> None:
    asked, zips = host
    zips["eur"] = _night_zip({"+46+006.png": b"x"})
    damaged = {"eur": _region("eur", zips["eur"], sha="0" * 64)}
    with (
        mock.patch.object(osxpregions, "catalog", lambda *a, **k: damaged),
        pytest.raises(OsxpError) as err,
    ):
        night.grid_of(TileRef(46, 6))
    assert err.value.code == "TEX_NIGHT_GRIDS_FAILED"
    assert night.grid_of(TileRef(46, 6)) is not None  # not taken for done: asked again
    assert len(asked) == 2


def test_a_build_without_live_s_address_stops_rather_than_drawing_without_the_grid(
    host, monkeypatch
) -> None:
    monkeypatch.setattr(osxpregions, "shipped_host", lambda: ("", ""))
    with pytest.raises(OsxpError) as err:
        night.grid_of(TileRef(46, 6))
    assert err.value.code == "TEX_NIGHT_GRIDS_FAILED"


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


def _manifest(pack: Path, files) -> PackManifest:
    files_doc = {"dsf": T.dsf_relpath.as_posix(), "dsf_size": files.dsf_size, "overlay": ""}
    files_doc |= {"textures": files.textures, "terrain": files.terrain, "cfg": ""}
    manifest = PackManifest(
        tile=T.name, provider="BI", zl=16, files={**files_doc, "night": 1, "night_lum": 20}
    )
    write_manifest(pack, T, manifest)
    return manifest


def test_the_pack_lays_the_night_textures_and_built_again_without_takes_them_out(
    tmp_path: Path,
) -> None:
    dsf_dir, tex_dir = _dsf_dir(tmp_path), _textures(tmp_path)
    night_dir = _run_night(tmp_path, dsf_dir)
    out = tmp_path / "tiles"
    pack = out / pack_dir_name(T)
    files = write_pack(
        out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=None,
        night_dir=night_dir, night_lum=20,
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
        night_dir=night_dir, night_lum=0,
    )  # fmt: skip
    assert off.night == 1 and _lines(pack, TOWN_TEX) == []  # Live's lights off: files kept
    gone = write_pack(out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=None)
    assert gone.night == 0 and not (pack / NIGHT_FOLDER).exists()
    assert _lines(pack, TOWN_TEX) == []


def test_a_pack_missing_a_night_texture_is_not_intact(tmp_path: Path) -> None:
    dsf_dir, tex_dir = _dsf_dir(tmp_path), _textures(tmp_path)
    out = tmp_path / "tiles"
    files = write_pack(
        out, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=None,
        night_dir=_run_night(tmp_path, dsf_dir), night_lum=20,
    )  # fmt: skip
    pack = out / pack_dir_name(T)
    manifest = _manifest(pack, files)
    assert pack_is_intact(pack, manifest)
    (pack / NIGHT_FOLDER / night_name(_photo(TOWN_TEX))).unlink()
    assert not pack_is_intact(pack, manifest)


def test_live_s_strength_rewrites_the_tiles_with_night_alone(tmp_path: Path) -> None:
    dsf_dir, tex_dir = _dsf_dir(tmp_path), _textures(tmp_path)
    night_dir = _run_night(tmp_path, dsf_dir)
    files = write_pack(
        tmp_path / "a", T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=None,
        night_dir=night_dir, night_lum=20,
    )  # fmt: skip
    write_pack(tmp_path / "b", T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=None)
    lit_pack, plain = tmp_path / "a" / pack_dir_name(T), tmp_path / "b" / pack_dir_name(T)
    _manifest(lit_pack, files)
    built_on = (lit_pack / "orthostudio.toml").stat().st_mtime_ns
    before = {p.name: p.read_bytes() for p in (plain / "terrain").iterdir()}
    assert night.set_strength([lit_pack, plain], 12) == 1
    assert _lines(lit_pack, TOWN_TEX)[1] == "LUMINANCE 12"
    assert read_manifest(lit_pack).files["night_lum"] == 12  # a later build tells them apart
    assert (lit_pack / "orthostudio.toml").stat().st_mtime_ns == built_on  # the Library's date
    assert {p.name: p.read_bytes() for p in (plain / "terrain").iterdir()} == before
    assert night.set_strength([lit_pack, plain], 12) == 0  # already so
    assert night.set_strength([lit_pack], 0) == 1 and _lines(lit_pack, TOWN_TEX) == []
    assert night.set_strength([lit_pack], 34) == 1
    assert _lines(lit_pack, TOWN_TEX)[1] == "LUMINANCE 34"


def test_the_strength_is_live_s_setting() -> None:
    from orthostudio.live.settings import LiveSettings

    for on, light, lum in ((True, "soft", 12), (True, "strong", 34), (False, "normal", 0)):
        assert night.luminance(LiveSettings(night=on, night_light=light)) == lum
