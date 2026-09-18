"""``Dem``: source resolution, ``alt_vec``, composites, and the artefact round trip.

Spec: ``docs/specs/dem.md`` sections 8 and 9 (acceptance A6, A7).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from orthostudio.dem.dem import Dem, alt_file_name, expected_alt_size, resolve_source
from orthostudio.dem.raster import NODATA
from orthostudio.dem.sources import EnsureOptions, NegativeMemo, elevation_path, no_download
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef

TILE = TileRef(43, 5)


def _reference_alt_vec_nostrict(dem: Dem, way: np.ndarray) -> np.ndarray:
    """``O4_DEM_Utils.py:283-311``, verbatim (the four list comprehensions)."""
    Nx = dem.nxdem - 1  # noqa: N806
    Ny = dem.nydem - 1  # noqa: N806
    x, y = way[:, 0], way[:, 1]
    x = np.maximum.reduce([x, dem.x0 * np.ones(x.shape)])
    x = np.minimum.reduce([x, dem.x1 * np.ones(x.shape)])
    y = np.maximum.reduce([y, dem.y0 * np.ones(y.shape)])
    y = np.minimum.reduce([y, dem.y1 * np.ones(y.shape)])
    px = (x - dem.x0) / (dem.x1 - dem.x0) * Nx
    py = (y - dem.y0) / (dem.y1 - dem.y0) * Ny
    nx = px.astype(np.uint16)
    Nminusny = Ny - py.astype(np.uint16)  # noqa: N806
    rx = px - nx
    ry = py + Nminusny - Ny
    alt_dem = dem.alt_dem
    t1 = [alt_dem[i][j] for i, j in zip(Nminusny, nx, strict=True)]
    t2 = [
        alt_dem[i][j]
        for i, j in zip(
            (Nminusny - 1) * (Nminusny >= 1),
            (nx + 1) * (nx < Nx) + Nx * (nx == Nx),
            strict=True,
        )
    ]
    t3 = [
        alt_dem[i][j] for i, j in zip(Nminusny, (nx + 1) * (nx < Nx) + Nx * (nx == Nx), strict=True)
    ]
    t4 = [alt_dem[i][j] for i, j in zip((Nminusny - 1) * (Nminusny >= 1), nx, strict=True)]
    return ((1 - rx) * t1 + ry * t2 + (rx - ry) * t3) * (rx >= ry) + (
        (1 - ry) * t1 + rx * t2 + (ry - rx) * t4
    ) * (rx < ry)


def _dem(alt: np.ndarray, *, x0: float = -0.01, x1: float = 1.01) -> Dem:
    n = alt.shape[0]
    return Dem(tile=TILE, alt_dem=alt, x0=x0, y0=x0, x1=x1, y1=x1, nxdem=n, nydem=n)


def _synthetic_raster(n: int = 121, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(300, 200, (n, n)).astype(np.float32)


# -- source resolution (spec 8, ``DEM.load_data``) -----------------------------------------


def test_resolve_source_defaults_to_view(tmp_path: Path) -> None:
    assert resolve_source("", TILE, tmp_path) == ["View"]


def test_resolve_source_prefers_a_generic_tif(tmp_path: Path) -> None:
    tif = tmp_path / "+40+000" / "N43E005.tif"
    tif.parent.mkdir(parents=True)
    tif.write_bytes(b"II*\x00")
    assert resolve_source("", TILE, tmp_path) == [str(tif)]


def test_resolve_source_expands_latlon_and_long_names(tmp_path: Path) -> None:
    assert resolve_source("/dem/{latlon}.tif", TILE, tmp_path) == ["/dem/N43E005.tif"]
    long_name = "Viewfinderpanoramas (J. de Ferranti) - mostly worldwide"
    assert resolve_source(long_name, TILE, tmp_path) == ["View"]


def test_resolve_source_splits_a_composite(tmp_path: Path) -> None:
    assert resolve_source("View;/a.tif;/b.tif", TILE, tmp_path) == ["View", "/a.tif", "/b.tif"]
    with pytest.raises(ValueError, match="empty overlay"):
        resolve_source("View;;/b.tif", TILE, tmp_path)


# -- alt_vec (spec 8.1, acceptance A6) -----------------------------------------------------


@pytest.mark.parametrize("seed", range(5))
def test_alt_vec_matches_the_reference_formula_bitwise(seed: int) -> None:
    dem = _dem(_synthetic_raster(seed=seed))
    rng = np.random.default_rng(1000 + seed)
    way = np.column_stack([rng.uniform(-0.05, 1.05, 5000), rng.uniform(-0.05, 1.05, 5000)])
    mine = dem.alt_vec(way)
    theirs = _reference_alt_vec_nostrict(dem, way)
    assert mine.tobytes() == theirs.tobytes()


def test_alt_vec_on_the_window_corners_is_bitwise_identical() -> None:
    dem = _dem(_synthetic_raster(seed=42))
    corners = np.array(
        [
            [dem.x0, dem.y0],
            [dem.x1, dem.y1],
            [dem.x0, dem.y1],
            [dem.x1, dem.y0],
            [-10.0, -10.0],
            [10.0, 10.0],
        ]
    )
    assert dem.alt_vec(corners).tobytes() == _reference_alt_vec_nostrict(dem, corners).tobytes()


def test_alt_vec_reproduces_the_raster_on_the_pixel_centres() -> None:
    alt = _synthetic_raster(61, seed=5)
    dem = _dem(alt, x0=0.0, x1=1.0)
    n = alt.shape[0] - 1
    rows, cols = np.meshgrid(np.arange(0, n + 1, 7), np.arange(0, n + 1, 7), indexing="ij")
    way = np.column_stack([cols.ravel() / n, 1.0 - rows.ravel() / n])
    got = dem.alt_vec(way)
    assert np.allclose(got, alt[rows.ravel(), cols.ravel()], atol=1e-4)


def test_alt_vec_clamps_instead_of_failing() -> None:
    dem = _dem(_synthetic_raster(31))
    far = np.array([[-180.0, -90.0], [180.0, 90.0]])
    inside = np.array([[dem.x0, dem.y0], [dem.x1, dem.y1]])
    assert np.array_equal(dem.alt_vec(far), dem.alt_vec(inside))


def test_alt_is_the_scalar_form() -> None:
    dem = _dem(_synthetic_raster(31))
    assert dem.alt((0.3, 0.4)) == pytest.approx(float(dem.alt_vec(np.array([[0.3, 0.4]]))[0]))


def test_alt_vec_strict_returns_nodata_outside_the_window() -> None:
    alt = _synthetic_raster(31, seed=8)
    dem = _dem(alt, x0=0.0, x1=1.0)
    way = np.array([[0.5, 0.5], [1.5, 0.5], [0.5, -0.2]])
    got = dem.alt_vec_strict(way)
    assert got[0] == pytest.approx(float(alt[15, 15]))
    assert got[1] == NODATA
    assert got[2] == NODATA


def test_a_composite_lets_the_overlay_win_where_it_has_data() -> None:
    base = np.full((21, 21), 100.0, dtype=np.float32)
    overlay = np.full((21, 21), NODATA, dtype=np.float32)
    overlay[5:15, 5:15] = 500.0
    dem = _dem(base, x0=0.0, x1=1.0)
    sub = _dem(overlay, x0=0.0, x1=1.0)
    dem.overlays = (sub,)
    way = np.array([[0.5, 0.5], [0.01, 0.01]])
    got = dem.alt_vec(way)
    assert got[0] == 500.0
    assert got[1] == 100.0


# -- build (spec 4, 7) ---------------------------------------------------------------------


def _block(tmp_path: Path, value: int = 250, holes: int = 0) -> EnsureOptions:
    for lat in (45, 46, 47):
        for lon in (4, 5, 6):
            data = np.full((3601, 3601), value, dtype=np.int16)
            if holes and (lat, lon) == (46, 5):
                data[:holes, :holes] = -32768
            path = tmp_path / "+40+000" / f"N{lat:02d}E{lon:03d}.hgt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data.astype(">i2").tobytes())
    return EnsureOptions(elevation_dir=tmp_path, download=no_download, memo=NegativeMemo())


def test_build_assembles_and_fills(tmp_path: Path) -> None:
    opts = _block(tmp_path, holes=20)
    dem = Dem.build(TileRef(46, 5), opts, fill_nodata="nearest")
    assert (dem.nxdem, dem.nydem) == (3673, 3673)
    assert not (dem.alt_dem == NODATA).any()
    assert dem.alt_dem.max() == 250


def test_build_with_zero_fill_reports_the_count(tmp_path: Path) -> None:
    opts = _block(tmp_path, holes=20)
    events: list[OsxpError] = []
    dem = Dem.build(TileRef(46, 5), opts, fill_nodata="zero", on_event=events.append)
    assert not (dem.alt_dem == NODATA).any()
    filled = [e for e in events if e.code == "DEM_VOIDS_FILLED_WITH_ZERO"]
    assert filled and filled[0].context["count"] == 400


def test_too_many_voids_fall_back_to_zero(tmp_path: Path) -> None:
    opts = _block(tmp_path, holes=200)  # 40 000 >= 10 000
    events: list[OsxpError] = []
    dem = Dem.build(TileRef(46, 5), opts, fill_nodata="nearest", on_event=events.append)
    assert not (dem.alt_dem == NODATA).any()
    assert (dem.alt_dem == 0).sum() >= 200 * 200
    assert any(e.code == "DEM_VOIDS_FILLED_WITH_ZERO" for e in events)


def test_build_of_a_non_assembled_source_without_a_file_is_an_error(tmp_path: Path) -> None:
    """NED is read cell by cell (``O4_DEM_Utils.py:96-120``), so a missing file is fatal.

    It is downloaded, not placed by hand, so the tile is refused as unavailable; a source whose
    cells a user does place by hand keeps the remedy that names the file.
    """
    opts = EnsureOptions(elevation_dir=tmp_path, download=no_download)
    with pytest.raises(OsxpError) as excinfo:
        Dem.build(TileRef(43, 5), opts, custom_dem="NED1")
    assert excinfo.value.code == "DEM_TILE_UNAVAILABLE"
    with pytest.raises(OsxpError) as manual:
        Dem.build(TileRef(43, 5), opts, custom_dem="ALOS")
    assert manual.value.code == "DEM_SOURCE_MANUAL_DOWNLOAD"
    assert manual.value.context["expected_name"].endswith("N43E005_ALOS3W30.tif")


def test_srtm_without_the_tile_cell_refuses_to_build_a_flat_tile(tmp_path: Path) -> None:
    """SRTM is a global source: Ortho4XP degrades *each* missing cell, the tile's own included,
    and builds a tile flat at 0 m. OrthoStudio XP refuses the tile's own cell (decision 0007); the
    neighbours still degrade (``test_dem_raster``).

    SRTM is not downloaded (OpenTopography stopped serving it), so the refusal is the one that
    names the file to place by hand -- the remedy the catalogue always described for it, which
    only the USGS products used to receive.
    """
    opts = EnsureOptions(elevation_dir=tmp_path, download=no_download)
    events: list[OsxpError] = []
    with pytest.raises(OsxpError) as excinfo:
        Dem.build(TileRef(46, 5), opts, custom_dem="SRTM", on_event=events.append)
    assert excinfo.value.code == "DEM_SOURCE_MANUAL_DOWNLOAD"
    assert excinfo.value.context["cell"] == "N46E005"
    assert excinfo.value.context["expected_name"].endswith("N46E005_SRTMv3.hgt")
    assert any(e.code == "DEM_NEIGHBOUR_UNAVAILABLE" for e in events)


def test_the_usgs_relief_outside_the_united_states_refuses_the_tile(tmp_path: Path) -> None:
    """Settings offers the USGS 3DEP (``relief.source = "usgs"`` -> ``NED1/3``), which covers the
    United States only. A European tile asks for a cell the USGS does not serve (404): the tile's
    own cell is missing, so the build stops with ``DEM_TILE_UNAVAILABLE`` and the page names the
    two sources that cover the region -- never a tile flat at 0 m (decision 0007)."""
    from orthostudio.dem.sources import Download

    asked: list[str] = []

    def refused(url: str) -> Download:
        asked.append(url)
        return Download(url, status=404)  # what the USGS bucket answers outside its coverage

    opts = EnsureOptions(elevation_dir=tmp_path, download=refused, memo=NegativeMemo())
    with pytest.raises(OsxpError) as excinfo:
        Dem.build(TileRef(46, 6), opts, custom_dem="NED1/3")
    assert excinfo.value.code == "DEM_TILE_UNAVAILABLE"
    assert excinfo.value.context["cell"] == "N46E006"
    assert excinfo.value.context["source"] == "NED1/3"
    assert asked == [
        "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/"
        "n47e006/USGS_13_n47e006.tif"
    ]


def test_an_unreadable_tile_cell_is_refused_too(tmp_path: Path) -> None:
    """A truncated ``.hgt`` used to count as available and give the same flat tile.

    ``N50E010`` is a 3" cell (block ``M32``), so the local file is used as it is.
    """
    path = elevation_path("View", tmp_path, 50, 10)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x00" * 7)
    opts = EnsureOptions(elevation_dir=tmp_path, download=no_download, memo=NegativeMemo())
    with pytest.raises(OsxpError) as excinfo:
        Dem.build(TileRef(50, 10), opts)
    assert excinfo.value.code == "DEM_TILE_UNAVAILABLE"
    assert "unreadable" in excinfo.value.context["reason"]


def test_build_of_a_single_file_source(tmp_path: Path) -> None:
    data = np.full((1201, 1201), 42, dtype=np.int16)
    path = tmp_path / "custom.hgt"
    path.write_bytes(data.astype(">i2").tobytes())
    dem = Dem.build(
        TileRef(43, 5),
        EnsureOptions(elevation_dir=tmp_path, download=no_download),
        custom_dem=str(path),
    )
    assert (dem.nxdem, dem.x0, dem.x1) == (3601, 0.0, 1.0)
    assert dem.alt_dem.min() == pytest.approx(42, abs=1e-4)


def test_an_unreadable_custom_file_is_refused(tmp_path: Path) -> None:
    """Ortho4XP turns an unreadable ``custom_dem`` into a tile flat at 0 m (decision 0007)."""
    path = tmp_path / "custom.hgt"
    path.write_bytes(b"\x00" * 7)
    events: list[OsxpError] = []
    with pytest.raises(OsxpError) as excinfo:
        Dem.build(
            TileRef(43, 5),
            EnsureOptions(elevation_dir=tmp_path, download=no_download),
            custom_dem=str(path),
            on_event=events.append,
        )
    assert excinfo.value.code == "DEM_TILE_UNAVAILABLE"
    assert str(path) in excinfo.value.context["reason"]
    assert [e.code for e in events] == ["DEM_FILE_UNREADABLE"]


def test_an_unreadable_overlay_is_refused_too(tmp_path: Path) -> None:
    """A zero overlay would win everywhere inside its window: the tile flat under it."""
    base = tmp_path / "base.hgt"
    base.write_bytes(np.full((1201, 1201), 10, np.int16).astype(">i2").tobytes())
    over = tmp_path / "over.hgt"
    over.write_bytes(b"\x00" * 7)
    with pytest.raises(OsxpError, match="DEM_TILE_UNAVAILABLE"):
        Dem.build(
            TileRef(43, 5),
            EnsureOptions(elevation_dir=tmp_path, download=no_download),
            custom_dem=f"{base};{over}",
        )


def test_build_of_a_composite_keeps_the_overlays(tmp_path: Path) -> None:
    base = tmp_path / "base.hgt"
    base.write_bytes(np.full((1201, 1201), 10, np.int16).astype(">i2").tobytes())
    over = tmp_path / "over.hgt"
    over.write_bytes(np.full((1201, 1201), 20, np.int16).astype(">i2").tobytes())
    dem = Dem.build(
        TileRef(43, 5),
        EnsureOptions(elevation_dir=tmp_path, download=no_download),
        custom_dem=f"{base};{over}",
    )
    assert len(dem.overlays) == 1
    assert dem.alt_vec(np.array([[0.5, 0.5]]))[0] == pytest.approx(20.0, abs=1e-4)


# -- artefact (spec 9, acceptance A7) ------------------------------------------------------


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    alt = _synthetic_raster(51, seed=9)
    dem = _dem(alt)
    dem.source = "View"
    out = tmp_path / "artifact"
    dem.save(out)
    assert (out / alt_file_name(TILE)).stat().st_size == 4 * 51 * 51
    again = Dem.load(out)
    assert again.tile == TILE
    assert (again.nxdem, again.nydem) == (51, 51)
    assert np.array_equal(np.asarray(again.alt_dem), alt)
    way = np.array([[0.2, 0.7], [0.9, 0.1]])
    assert again.alt_vec(way).tobytes() == dem.alt_vec(way).tobytes()


def test_write_alt_is_raw_float32_row_major(tmp_path: Path) -> None:
    alt = _synthetic_raster(17, seed=3)
    path = tmp_path / "Data+43+005.alt"
    _dem(alt).write_alt(path)
    assert path.read_bytes() == alt.astype(np.float32).tobytes()


def test_meta_json_schema(tmp_path: Path) -> None:
    dem = _dem(_synthetic_raster(21, seed=4))
    dem.save(tmp_path)
    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["format"] == "osxp-dem-1"
    assert meta["tile"] == "+43+005"
    assert set(meta) >= {
        "epsg",
        "x0",
        "y0",
        "x1",
        "y1",
        "nodata",
        "nxdem",
        "nydem",
        "min",
        "max",
        "mean",
        "nodata_pixels",
        "cells",
    }


def test_load_refuses_a_foreign_document(tmp_path: Path) -> None:
    (tmp_path / "meta.json").write_text('{"format": "something-else"}')
    with pytest.raises(ValueError, match="osxp-dem-1"):
        Dem.load(tmp_path)


def test_expected_alt_size_matches_the_ortho4xp_check() -> None:
    assert expected_alt_size("View") == 4 * 3673 * 3673 == 53963716
    assert expected_alt_size("ALOS") == 4 * 3672 * 3672
    assert expected_alt_size("/some/file.tif") is None


def test_elevation_path_is_shared_with_ortho4xp(tmp_path: Path) -> None:
    assert elevation_path("View", tmp_path, 43, 5).relative_to(tmp_path) == Path(
        "+40+000/N43E005.hgt"
    )
