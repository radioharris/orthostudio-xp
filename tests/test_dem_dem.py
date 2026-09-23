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
    """A zero overlay would win everywhere inside its window: the tile flat under it.

    A single file named as an overlay is still refused, and a *folder* of one's own is not:
    there the file simply does not count for that square and the relief chosen is kept whole
    (``test_a_file_of_ones_own_that_cannot_be_read_gives_way_to_the_relief_chosen``).
    """
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


def _hgt(path: Path, side: int, value: int) -> Path:
    path.write_bytes(np.full((side, side), value, np.int16).astype(">i2").tobytes())
    return path


def test_build_of_a_composite_writes_the_overlay_into_the_raster(tmp_path: Path) -> None:
    """The overlay must be *in* the raster, not beside it.

    Kept beside it, it lived only in the object: the artefact held the base alone, and the
    vector and mesh stages, which read the artefact, built the tile out of the base. A user's
    own lidar file was found, keyed, and changed nothing (+46+006, 2026-09-19).
    """
    base = _hgt(tmp_path / "base.hgt", 1201, 10)
    over = _hgt(tmp_path / "over.hgt", 1201, 20)
    opts = EnsureOptions(elevation_dir=tmp_path, download=no_download)
    dem = Dem.build(TileRef(43, 5), opts, custom_dem=f"{base};{over}")
    assert dem.laid_over == (str(over),)
    assert dem.alt_dem.min() == 20 and dem.alt_dem.max() == 20
    out = tmp_path / "artifact"
    dem.save(out)
    again = Dem.load(out)  # what the next stage reads
    assert again.alt_vec(np.array([[0.5, 0.5]]))[0] == pytest.approx(20.0, abs=1e-4)
    assert json.loads((out / "meta.json").read_text())["laid_over"] == [str(over)]


def test_an_overlay_finer_than_the_base_raises_the_whole_grid(tmp_path: Path) -> None:
    """A half-second file of one's own read at the second of the source under it would throw
    away half of what the user downloaded: the window takes the finest step in the room."""
    base = _hgt(tmp_path / "base.hgt", 1201, 10)
    fine = np.full((3601, 3601), 20, np.int16)
    fine[::2, ::2] = 40  # detail no 3" grid could hold
    over = tmp_path / "over.hgt"
    over.write_bytes(fine.astype(">i2").tobytes())
    opts = EnsureOptions(elevation_dir=tmp_path, download=no_download)
    dem = Dem.build(TileRef(43, 5), opts, custom_dem=f"{base};{over}")
    assert (dem.nxdem, dem.nydem) == (3601, 3601)
    assert np.array_equal(np.asarray(dem.alt_dem), fine.astype(np.float32))


def test_a_file_of_ones_own_coarser_than_the_relief_is_left_aside(tmp_path: Path) -> None:
    """The finest wins both ways. A 1" file laid over the USGS 1/3" would throw away two points
    out of three, and the American sets of one's own are made from that very source (a user with
    both asked what happens, 2026-09-20). It is said, not done silently."""
    base = _hgt(tmp_path / "base.hgt", 3601, 10)  # the finer relief
    mine = tmp_path / "mine"
    mine.mkdir()
    _hgt(mine / "N43E005.hgt", 1201, 20)  # three times coarser
    opts = EnsureOptions(elevation_dir=tmp_path, download=no_download)
    events: list[OsxpError] = []
    dem = Dem.build(TileRef(43, 5), opts, custom_dem=f"{base};{mine}", on_event=events.append)
    assert dem.laid_over == ()
    assert dem.alt_dem.min() == 10 and dem.alt_dem.max() == 10
    (told,) = [e for e in events if e.code == "DEM_OVERLAY_COARSER"]
    assert told.context["own"] == "N43E005.hgt" and told.message
    # the same file over a coarser relief is laid, and raises the grid
    coarse = _hgt(tmp_path / "coarse.hgt", 601, 10)
    over = Dem.build(TileRef(43, 5), opts, custom_dem=f"{coarse};{mine}")
    assert over.laid_over == (str(mine),) and (over.nxdem, over.nydem) == (1201, 1201)


def test_a_hole_in_the_overlay_lets_the_relief_under_it_through(tmp_path: Path) -> None:
    base = _hgt(tmp_path / "base.hgt", 1201, 10)
    holed = np.full((1201, 1201), 20, np.int16)
    holed[:100, :100] = -32768
    over = tmp_path / "over.hgt"
    over.write_bytes(holed.astype(">i2").tobytes())
    opts = EnsureOptions(elevation_dir=tmp_path, download=no_download)
    dem = Dem.build(TileRef(43, 5), opts, custom_dem=f"{base};{over}")
    assert float(dem.alt_dem[0, 0]) == 10.0  # the void of the overlay
    assert float(dem.alt_dem[-1, -1]) == 20.0
    assert not (dem.alt_dem == NODATA).any()


def test_an_overlay_covering_the_tile_leaves_the_margin_to_the_base(tmp_path: Path) -> None:
    """An assembled source is read a little beyond the tile (section 4.1); a file of one's own
    covers the square and no more, so the skirt around it keeps the relief underneath."""
    opts = _block(tmp_path, value=250)
    over = _hgt(tmp_path / "over.hgt", 3601, 700)
    dem = Dem.build(TileRef(46, 5), opts, custom_dem=f"View;{over}")
    assert (dem.nxdem, dem.nydem) == (3673, 3673)
    assert float(dem.alt_dem[0, 0]) == 250.0  # the margin, north-west of the square
    assert float(dem.alt_dem[dem.nydem // 2, dem.nxdem // 2]) == 700.0
    assert float(dem.alt_dem[36, 36]) == 700.0 and float(dem.alt_dem[35, 35]) == 250.0


def test_a_folder_without_this_square_leaves_the_relief_alone(tmp_path: Path) -> None:
    """A partial collection is no trouble: the tile is built from the relief chosen."""
    mine = tmp_path / "mine"
    mine.mkdir()
    _hgt(mine / "N46E006.hgt", 1201, 900)  # another square
    opts = _block(tmp_path, value=250)
    events: list[OsxpError] = []
    dem = Dem.build(TileRef(46, 5), opts, custom_dem=f"View;{mine}", on_event=events.append)
    assert dem.laid_over == ()
    assert dem.alt_dem.min() == 250 and dem.alt_dem.max() == 250
    assert any(e.code == "DEM_OVERLAY_UNAVAILABLE" for e in events)


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


def test_a_folder_of_ones_own_files_is_laid_over_the_relief_chosen(tmp_path: Path) -> None:
    """A user has the lidar models of Europe by the hundred, one file per square, and wants to
    name the folder once rather than a file per tile (X-Plane.Org, 2026-09-19).

    The folder rides in ``custom_dem`` as an overlay: the square it holds takes its file, and where
    it has nothing the relief under it answers, so a partial set builds every tile.
    """
    own = tmp_path / "Sonny" / "Austria"
    own.mkdir(parents=True)
    ramp = np.linspace(300, 2000, 1201, dtype=np.float32)
    (np.zeros((1201, 1), np.float32) + ramp[None, :]).astype(">i2").tofile(own / "N47E011.hgt")
    base = tmp_path / "base"  # stands here for Copernicus or X-Plane's relief: one file a square
    base.mkdir()
    for cell in ("N47E011", "N47E010"):
        np.full((1201, 1201), 100, dtype=">i2").tofile(base / f"{cell}.hgt")
    opts = EnsureOptions(elevation_dir=tmp_path, download=no_download, memo=NegativeMemo())
    folder = str(tmp_path / "Sonny")

    middle = np.array([[0.25, 0.5], [0.5, 0.5], [0.75, 0.5]])
    held = Dem.build(TileRef(47, 11), opts, custom_dem=f"{base};{folder}")
    assert held.alt_vec(middle).round().tolist() == [725.0, 1150.0, 1575.0]  # his own file

    # a square his folder does not hold: the relief under it, and no failure
    events: list[OsxpError] = []
    elsewhere = Dem.build(
        TileRef(47, 10), opts, custom_dem=f"{base};{folder}", on_event=events.append
    )
    assert elsewhere.alt_vec(middle).round().tolist() == [100.0, 100.0, 100.0]
    assert [e.code for e in events if e.code == "DEM_OVERLAY_UNAVAILABLE"]


def test_a_folder_of_ones_own_alone_refuses_the_squares_it_does_not_hold(tmp_path: Path) -> None:
    """Named as the relief itself, rather than over one, the folder must hold the square: a tile
    is refused rather than built flat (decision 0007), and the message names the folder."""
    own = tmp_path / "own"
    own.mkdir()
    np.full((1201, 1201), 500, dtype=">i2").tofile(own / "N47E011.hgt")
    opts = EnsureOptions(elevation_dir=tmp_path / "base", download=no_download, memo=NegativeMemo())
    held = Dem.build(TileRef(47, 11), opts, custom_dem=str(own))
    assert held.alt_vec(np.array([[0.5, 0.5]])).round().tolist() == [500.0]
    with pytest.raises(OsxpError) as raised:
        Dem.build(TileRef(47, 10), opts, custom_dem=str(own))
    assert raised.value.code == "DEM_TILE_UNAVAILABLE"
    assert str(own) in str(raised.value.context.get("reason", ""))


def test_a_relief_file_of_ones_own_is_weighed_so_a_better_one_is_read(tmp_path: Path) -> None:
    """*My own elevation file* puts the file where the relief itself goes, not among the
    overlays, and nothing weighed it: only its path reached the key. A user who corrected his
    file, built again and installed flew the relief he had replaced, with nothing to tell him
    (found in review, 2026-09-23). Nobody on a named source is rebuilt for this."""
    from orthostudio.model import TileRef
    from orthostudio.pipeline.build import BuildSpec, _stamp_own_file

    tile = TileRef(49, -122)
    spec = BuildSpec(tile=tile, provider="BI", zl=16, out_dir=tmp_path / "out", config={})

    def stamp(custom_dem: str) -> str:
        return str(_stamp_own_file({"custom_dem": custom_dem}, spec).get("own_stamp", ""))

    own = tmp_path / "N49W122.hgt"
    own.write_bytes(b"\x00" * 2000)
    before = stamp(str(own))
    assert before, "his own file is weighed"
    own.write_bytes(b"\x01" * 4000)  # the same path, a better file
    assert stamp(str(own)) != before, "so the tile is built again"

    folder = tmp_path / "lidar"
    folder.mkdir()
    (folder / "N49W122.hgt").write_bytes(b"\x00" * 100)
    was = stamp(f"COP30;{folder}")
    assert stamp("COP30") == "", "a named source alone keeps the key it has always had"
    assert stamp("COP30;HRDEM") == "2:HRDEM", "and so does a source named as an overlay"
    assert was.startswith("2:N49W122.hgt:100:"), "a folder of one's own is weighed as before"


def test_the_relief_of_ones_own_says_what_to_do_about_it(tmp_path: Path) -> None:
    """The remedy spoke of the X-Plane installer and ended by telling the user to give his own
    elevation file, which is what he had just done. The relief is the first thing a tile needs,
    so the build stopped at 0 % with that advice (a Linux user, found in review 2026-09-23)."""
    from orthostudio.dem.dem import _relief_remedy

    folder = tmp_path / "my relief"
    folder.mkdir()
    said = _relief_remedy(str(folder), "N49W122")
    assert said is not None
    assert "N49W122" in said and ".hgt" in said and "choose another relief" in said

    lone = tmp_path / "mine.hgt"
    lone.write_bytes(b"\x00")
    said = _relief_remedy(str(lone), "N49W122")
    assert said is not None and "N49W122" in said

    assert _relief_remedy("COP30", "N49W122") is None, "a named source keeps the general words"
    assert _relief_remedy("XP12", "N49W122") is None


def test_a_file_of_ones_own_that_cannot_be_read_gives_way_to_the_relief_chosen(
    tmp_path: Path,
) -> None:
    """Settings says of a folder of one's own: "Where your folder has nothing, the relief chosen
    above is used, so a partial set is no trouble at all". A file that cannot be read -- half
    downloaded, or a GeoTIFF in a projection we do not read, which is how most national lidar
    ships -- is nothing for that square, and it killed the tile at nought per cent instead. A
    partial set is exactly what a user collects (a Linux user; found in review, 2026-09-23)."""
    from orthostudio.dem.dem import Dem
    from orthostudio.dem.sources import EnsureOptions
    from orthostudio.errors import OsxpError
    from orthostudio.model import TileRef

    tile = TileRef(43, 5)
    opts = EnsureOptions(elevation_dir=tmp_path / "elevation")
    base = tmp_path / "N43E005.hgt"
    base.write_bytes(np.full((1201, 1201), 100, dtype=">i2").tobytes())
    folder = tmp_path / "his lidar"
    folder.mkdir()
    (folder / "N43E005.hgt").write_bytes(b"\x00" * 37)  # half downloaded

    said: list[OsxpError] = []
    dem = Dem.build(tile, opts, custom_dem=f"{base};{folder}", on_event=said.append)
    # a single file named as an overlay is a different matter and is still refused:
    # ``test_an_unreadable_overlay_is_refused_too``
    assert dem is not None, "the tile is built on the relief he chose"
    assert int(dem.alt_dem.min()) == 100 and int(dem.alt_dem.max()) == 100
    assert "DEM_FILE_UNREADABLE" in {e.code for e in said}, "and the file is named"

    # but when his folder *is* the relief, giving way would mean a flat tile: still refused,
    # with words about his folder rather than about the X-Plane installer
    with pytest.raises(OsxpError) as exc:
        Dem.build(tile, opts, custom_dem=str(folder), on_event=said.append)
    assert exc.value.code == "DEM_TILE_UNAVAILABLE"
    assert ".hgt" in exc.value.remedy and "N43E005" in exc.value.remedy
