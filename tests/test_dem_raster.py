"""Reading, assembling, filling, upsampling and smoothing rasters.

Spec: ``docs/specs/dem.md`` sections 4 to 7 and 8.3 (acceptance A3, A4, A5).

The ``_reference_*`` helpers are literal transcriptions of Ortho4XP (line numbers in their
docstrings); the OrthoStudio XP functions are compared to them **bitwise**.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from orthostudio.dem.raster import (
    GEOMETRY,
    MAX_FILL_PIXELS,
    NODATA,
    PLACEMENTS,
    _cell_array,
    _to_base_columns,
    build_combined_raster,
    cell_has_land,
    fill_nodata_nearest,
    nodata_to_zero,
    read_elevation_from_file,
    smooth_over_regions,
    smoothen,
    upsample_1201_to_3601,
    world_tiles,
)
from orthostudio.dem.sources import CellState, Download, EnsureOptions, NegativeMemo, no_download

# -- literal transcriptions of Ortho4XP --------------------------------------------------------


def _reference_upsample(alt_dem: np.ndarray) -> np.ndarray:
    """``O4_DEM_Utils.py:910-956``, verbatim."""
    out = np.zeros((3601, 3601), dtype=np.float32)
    for i in range(1201):
        out[3 * i, ::3] = alt_dem[i]
        out[3 * i, 1::3] = 2 / 3 * alt_dem[i, :-1] + 1 / 3 * alt_dem[i, 1:]
        out[3 * i, 2::3] = 1 / 3 * alt_dem[i, :-1] + 2 / 3 * alt_dem[i, 1:]
        if i == 1200:
            break
        out[3 * i + 1, ::3] = 2 / 3 * alt_dem[i] + 1 / 3 * alt_dem[i + 1]
        out[3 * i + 2, ::3] = 1 / 3 * alt_dem[i] + 2 / 3 * alt_dem[i + 1]
        out[3 * i + 1, 1::3] = (
            4 / 9 * alt_dem[i][:-1]
            + 2 / 9 * alt_dem[i, 1:]
            + 2 / 9 * alt_dem[i + 1, :-1]
            + 1 / 9 * alt_dem[i + 1, 1:]
        )
        out[3 * i + 2, 1::3] = (
            2 / 9 * alt_dem[i][:-1]
            + 1 / 9 * alt_dem[i, 1:]
            + 4 / 9 * alt_dem[i + 1, :-1]
            + 2 / 9 * alt_dem[i + 1, 1:]
        )
        out[3 * i + 1, 2::3] = (
            2 / 9 * alt_dem[i][:-1]
            + 4 / 9 * alt_dem[i, 1:]
            + 1 / 9 * alt_dem[i + 1, :-1]
            + 2 / 9 * alt_dem[i + 1, 1:]
        )
        out[3 * i + 2, 2::3] = (
            1 / 9 * alt_dem[i][:-1]
            + 2 / 9 * alt_dem[i, 1:]
            + 2 / 9 * alt_dem[i + 1, :-1]
            + 4 / 9 * alt_dem[i + 1, 1:]
        )
    return out


def _reference_fill(alt_dem: np.ndarray, nodata: float) -> int:
    """``O4_DEM_Utils.py:866-910``, verbatim."""
    step = 0
    while (alt_dem == nodata).any():
        if not step and np.sum(alt_dem == nodata) >= 10000:
            return 0
        alt10 = np.roll(alt_dem, 1, axis=0)
        alt10[0] = alt_dem[0]
        alt20 = np.roll(alt_dem, -1, axis=0)
        alt20[-1] = alt_dem[-1]
        alt01 = np.roll(alt_dem, 1, axis=1)
        alt01[:, 0] = alt_dem[:, 0]
        alt02 = np.roll(alt_dem, -1, axis=1)
        alt02[:, -1] = alt_dem[:, -1]
        if nodata < 0:
            atemp = np.maximum(alt10, alt20)
            atemp = np.maximum(atemp, alt01)
            atemp = np.maximum(atemp, alt02)
        else:
            atemp = np.minimum(alt10, alt20)
            atemp = np.minimum(atemp, alt01)
            atemp = np.minimum(atemp, alt02)
        alt_dem[alt_dem == nodata] = atemp[alt_dem == nodata]
        step += 1
        if step > 20:
            alt_dem[alt_dem == nodata] = 0
            break
    return 1


def _reference_smoothen(raster, pix_width, mask_im, preserve_boundary=True):  # type: ignore[no-untyped-def]
    """``O4_DEM_Utils.py:956-1002``, verbatim (``mask_im`` is an array here)."""
    if not pix_width:
        return raster
    if mask_im is None:
        return raster
    tmp = np.array(raster)
    mask_array = np.array(mask_im, dtype=np.float32) / 255
    kernel = np.array(range(1, 2 * (pix_width + 1)))
    kernel[pix_width + 1 :] = range(pix_width, 0, -1)
    kernel = kernel / (pix_width + 1) ** 2
    tmp = tmp * mask_array
    tmpw = np.array(mask_array)
    for i in range(0, len(tmp)):
        tmp[i] = np.convolve(tmp[i], kernel)[pix_width:-pix_width]
        tmpw[i] = np.convolve(tmpw[i], kernel)[pix_width:-pix_width]
    tmp = tmp.transpose()
    tmpw = tmpw.transpose()
    for i in range(0, len(tmp)):
        tmp[i] = np.convolve(tmp[i], kernel)[pix_width:-pix_width]
        tmpw[i] = np.convolve(tmpw[i], kernel)[pix_width:-pix_width]
    tmp = tmp.transpose()
    tmpw = tmpw.transpose()
    tmp[mask_array != 0] = (
        mask_array[mask_array != 0] * tmp[mask_array != 0] / tmpw[mask_array != 0]
        + (1 - mask_array[mask_array != 0]) * raster[mask_array != 0]
    )
    if preserve_boundary:
        for i in range(pix_width):
            tmp[i] = i / pix_width * tmp[i] + (pix_width - i) / pix_width * raster[i]
            tmp[-i - 1] = i / pix_width * tmp[-i - 1] + (pix_width - i) / pix_width * raster[-i - 1]
        for i in range(pix_width):
            tmp[:, i] = i / pix_width * tmp[:, i] + (pix_width - i) / pix_width * raster[:, i]
            tmp[:, -i - 1] = (
                i / pix_width * tmp[:, -i - 1] + (pix_width - i) / pix_width * raster[:, -i - 1]
            )
    return raster * (mask_array == 0) + tmp * (mask_array != 0)


# -- upsample (spec 6, acceptance A3) ------------------------------------------------------


@pytest.mark.parametrize("seed", range(6))
def test_upsample_matches_the_reference_bitwise(seed: int) -> None:
    rng = np.random.default_rng(seed)
    a = (rng.normal(400, 300, (1201, 1201))).astype(np.float32)
    mine = upsample_1201_to_3601(a)
    theirs = _reference_upsample(a)
    assert mine.dtype == np.float32
    assert mine.tobytes() == theirs.tobytes()


def test_upsample_keeps_the_source_samples_on_the_multiples_of_three() -> None:
    rng = np.random.default_rng(7)
    a = rng.integers(-100, 3000, (1201, 1201)).astype(np.float32)
    out = upsample_1201_to_3601(a)
    assert np.array_equal(out[::3, ::3], a)


def test_upsample_refuses_another_size() -> None:
    with pytest.raises(ValueError, match="1201x1201"):
        upsample_1201_to_3601(np.zeros((3601, 3601), dtype=np.float32))


# -- voids (spec 7, acceptance A4) ---------------------------------------------------------


@pytest.mark.parametrize("seed", range(8))
def test_fill_nodata_matches_the_reference_bitwise(seed: int) -> None:
    rng = np.random.default_rng(100 + seed)
    a = rng.normal(200, 80, (97, 131)).astype(np.float32)
    holes = rng.random(a.shape) < 0.02
    a[holes] = NODATA
    mine = a.copy()
    theirs = a.copy()
    assert fill_nodata_nearest(mine, NODATA) == bool(_reference_fill(theirs, NODATA))
    assert mine.tobytes() == theirs.tobytes()


def test_fill_nodata_refuses_a_raster_with_too_many_voids() -> None:
    a = np.zeros((200, 200), dtype=np.float32)
    a[:60, :] = NODATA  # 12 000 >= 10 000
    assert int((a == NODATA).sum()) >= MAX_FILL_PIXELS
    before = a.copy()
    assert fill_nodata_nearest(a, NODATA) is False
    assert np.array_equal(a, before)


def test_fill_nodata_gives_up_after_twenty_steps() -> None:
    a = np.full((99, 99), NODATA, dtype=np.float32)
    a[0, 0] = 5.0
    assert int((a == NODATA).sum()) < MAX_FILL_PIXELS
    assert fill_nodata_nearest(a, NODATA) is True
    assert not (a == NODATA).any()
    assert (a == 0).any(), "the unreachable pixels are zeroed, not extrapolated"


def test_fill_nodata_takes_the_minimum_for_a_positive_nodata() -> None:
    a = np.array([[1.0, 9999.0, 3.0]], dtype=np.float32)
    fill_nodata_nearest(a, 9999.0)
    assert a[0, 1] == 1.0


def test_nodata_to_zero_counts_what_it_changed() -> None:
    a = np.array([[1.0, NODATA, NODATA]], dtype=np.float32)
    assert nodata_to_zero(a, NODATA) == 2
    assert np.array_equal(a, np.array([[1.0, 0.0, 0.0]], dtype=np.float32))
    assert nodata_to_zero(a, NODATA) == 0


# -- smoothing (spec 8.3, acceptance A5) ---------------------------------------------------


@pytest.mark.parametrize("pix", [1, 2, 5, 11])
@pytest.mark.parametrize("preserve", [True, False])
def test_smoothen_matches_the_reference_bitwise(pix: int, preserve: bool) -> None:
    rng = np.random.default_rng(pix * 10 + preserve)
    raster = rng.normal(300, 120, (61, 83)).astype(np.float32)
    mask = np.zeros(raster.shape, dtype=np.uint8)
    mask[10:40, 12:60] = 255
    mask[40:48, 20:30] = 128
    mine = smoothen(raster, pix, mask, preserve_boundary=preserve)
    theirs = _reference_smoothen(raster, pix, mask, preserve_boundary=preserve)
    assert mine.tobytes() == theirs.tobytes()


def test_smoothen_is_a_no_op_without_width_or_mask() -> None:
    raster = np.arange(25, dtype=np.float32).reshape(5, 5)
    assert smoothen(raster, 0, np.full((5, 5), 255, np.uint8)) is raster
    assert smoothen(raster, 3, None) is raster


def test_smoothen_leaves_the_raster_untouched_where_the_mask_is_zero() -> None:
    rng = np.random.default_rng(3)
    raster = rng.normal(100, 50, (40, 40)).astype(np.float32)
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[15:25, 15:25] = 255
    out = smoothen(raster, 3, mask, preserve_boundary=False)
    assert np.array_equal(out[:10, :10], raster[:10, :10])
    assert not np.array_equal(out[18:22, 18:22], raster[18:22, 18:22])


def test_smooth_over_regions_matches_the_ortho4xp_airport_loop() -> None:
    """``O4_Airport_Utils.py:937-1033``, transcribed in the test."""
    from orthostudio.dem.raster import Region

    rng = np.random.default_rng(11)
    alt = rng.normal(200, 60, (120, 140)).astype(np.float32)
    mask_a = np.zeros((31, 41), dtype=np.uint8)
    mask_a[5:25, 5:35] = 255
    mask_b = np.zeros((21, 21), dtype=np.uint8)
    mask_b[3:18, 3:18] = 255
    regions = [
        Region(10, 40, 20, 60, mask_a, 4),
        Region(70, 90, 90, 110, mask_b, 8),
    ]
    max_pix = 8
    mine = smooth_over_regions(alt, regions, max_pix=max_pix, preserve_boundary=True)

    theirs = np.array(alt)
    up = np.array(theirs[:max_pix])
    down = np.array(theirs[-max_pix:])
    left = np.array(theirs[:, :max_pix])
    right = np.array(theirs[:, -max_pix:])
    for r in regions:
        theirs[r.rowmin : r.rowmax + 1, r.colmin : r.colmax + 1] = _reference_smoothen(
            theirs[r.rowmin : r.rowmax + 1, r.colmin : r.colmax + 1],
            r.pix,
            r.mask,
            preserve_boundary=False,
        )
    pix = max_pix
    for i in range(pix):
        theirs[i] = i / pix * theirs[i] + (pix - i) / pix * up[i]
        theirs[-i - 1] = i / pix * theirs[-i - 1] + (pix - i) / pix * down[-i - 1]
    for i in range(pix):
        theirs[:, i] = i / pix * theirs[:, i] + (pix - i) / pix * left[:, i]
        theirs[:, -i - 1] = i / pix * theirs[:, -i - 1] + (pix - i) / pix * right[:, -i - 1]
    assert mine.tobytes() == theirs.tobytes()


def test_smooth_over_regions_without_regions_copies() -> None:
    alt = np.arange(100, dtype=np.float32).reshape(10, 10)
    out = smooth_over_regions(alt, [])
    assert out is not alt
    assert np.array_equal(out, alt)


# -- reading (spec 5) ----------------------------------------------------------------------


def _write_hgt(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(values.astype(">i2").tobytes())


def test_read_hgt_3601(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    data = rng.integers(0, 2000, (3601, 3601)).astype(np.int16)
    path = tmp_path / "N43E005.hgt"
    _write_hgt(path, data)
    read = read_elevation_from_file(path, 43, 5)
    assert (read.nxdem, read.nydem) == (3601, 3601)
    assert (read.x0, read.y0, read.x1, read.y1) == (0.0, 0.0, 1.0, 1.0)
    assert read.nodata == NODATA
    assert read.alt_dem is not None
    assert np.array_equal(read.alt_dem, data.astype(np.float32))


def test_read_hgt_1201_is_filled_then_upsampled(tmp_path: Path) -> None:
    rng = np.random.default_rng(2)
    data = rng.integers(0, 2000, (1201, 1201)).astype(np.int16)
    data[100, 100] = -32768
    path = tmp_path / "N43E005.hgt"
    _write_hgt(path, data)
    read = read_elevation_from_file(path, 43, 5)
    assert (read.nxdem, read.nydem) == (3601, 3601)
    expected = data.astype(np.float32)
    fill_nodata_nearest(expected, NODATA)
    assert read.alt_dem is not None
    assert read.alt_dem.tobytes() == upsample_1201_to_3601(expected).tobytes()


def test_read_hgt_info_only_gives_the_shape_without_the_data(tmp_path: Path) -> None:
    path = tmp_path / "N43E005.hgt"
    _write_hgt(path, np.zeros((1201, 1201), dtype=np.int16))
    read = read_elevation_from_file(path, 43, 5, info_only=True)
    assert read.alt_dem is None
    assert (read.nxdem, read.nydem) == (3601, 3601)


def test_read_raw_is_south_up(tmp_path: Path) -> None:
    data = np.arange(16, dtype=np.int16).reshape(4, 4)
    path = tmp_path / "custom.raw"
    path.write_bytes(struct.pack("<16h", *data.ravel().tolist()))
    read = read_elevation_from_file(path, 43, 5)
    assert read.alt_dem is not None
    assert np.array_equal(read.alt_dem, data.astype(np.float32)[::-1])


def test_an_unreadable_file_degrades_to_zeros(tmp_path: Path) -> None:
    path = tmp_path / "N43E005.hgt"
    path.write_bytes(b"\x00" * 7)  # not a square number of samples
    events: list[object] = []
    read = read_elevation_from_file(path, 43, 5, base_if_error=64, on_event=events.append)
    assert read.alt_dem is not None
    assert read.alt_dem.shape == (64, 64)
    assert not read.alt_dem.any()
    assert [getattr(e, "code", "") for e in events] == ["DEM_FILE_UNREADABLE"]


def test_read_geotiff_window_and_nodata(tmp_path: Path) -> None:
    from PIL import Image, TiffImagePlugin

    data = np.arange(9, dtype=np.float32).reshape(3, 3)
    data[1, 1] = -9999.0
    path = tmp_path / "N43E005.tif"
    info = TiffImagePlugin.ImageFileDirectory_v2()
    info[33550] = (1 / 3600, 1 / 3600, 0.0)
    info[33922] = (0.0, 0.0, 0.0, 5.0, 44.0, 0.0)
    info[42113] = "-9999"
    info[34735] = (1, 1, 0, 1, 2048, 0, 1, 4326)
    Image.fromarray(data).save(path, tiffinfo=info)
    read = read_elevation_from_file(path, 43, 5)
    assert read.epsg == 4326
    assert read.nodata == NODATA
    assert read.alt_dem is not None
    assert read.alt_dem[1, 1] == np.float32(NODATA)
    assert read.x0 == pytest.approx(0.5 / 3600)
    assert read.y1 == pytest.approx(1.0 - 0.5 / 3600)
    assert read.x1 == pytest.approx(read.x0 + 2 / 3600)


def test_a_geotiff_in_another_crs_is_refused(tmp_path: Path) -> None:
    from PIL import Image, TiffImagePlugin

    path = tmp_path / "N43E005.tif"
    info = TiffImagePlugin.ImageFileDirectory_v2()
    info[33550] = (1.0, 1.0, 0.0)
    info[33922] = (0.0, 0.0, 0.0, 5.0, 44.0, 0.0)
    info[34735] = (1, 1, 0, 1, 3072, 0, 1, 32631)
    Image.fromarray(np.zeros((3, 3), dtype=np.float32)).save(path, tiffinfo=info)
    events: list[object] = []
    read = read_elevation_from_file(path, 43, 5, base_if_error=8, on_event=events.append)
    assert "DEM_EPSG_UNSUPPORTED" in [getattr(e, "code", "") for e in events]
    assert read.alt_dem is not None and read.alt_dem.shape == (8, 8)


# -- the world bitmap and the 3x3 assembly (spec 4) ----------------------------------------


def test_world_tiles_shape_and_known_cells() -> None:
    tiles = world_tiles()
    assert tiles.shape == (180, 360)
    assert cell_has_land(43, 5) and cell_has_land(43, 6)
    assert not cell_has_land(42, 5), "the sea south of Marseille"
    assert not cell_has_land(0, -30), "mid-Atlantic"


def test_geometry_table() -> None:
    assert GEOMETRY["View"] == GEOMETRY["SRTM"]
    assert GEOMETRY["View"].n == 3673
    assert GEOMETRY["ALOS"].n == 3672
    assert GEOMETRY["ALOS"].overlap == 0
    assert GEOMETRY["ALOS"].x0 == pytest.approx(-0.01 + 1 / 7200)
    assert PLACEMENTS == (
        (0, 0), (0, -1), (0, 1),
        (-1, 0), (-1, -1), (-1, 1),
        (1, 0), (1, -1), (1, 1),
    )  # fmt: skip


LAND_BLOCK = (46, 5)
"""A 3x3 block whose nine cells are all land in ``world_tiles.png``."""


def _synthetic_block(tmp_path: Path, value_of) -> EnsureOptions:  # type: ignore[no-untyped-def]
    for lat in (45, 46, 47):
        for lon in (4, 5, 6):
            data = np.full((3601, 3601), value_of(lat, lon), dtype=np.int16)
            data[0, :] = 900  # north row of the cell: the shared line, never used
            data[-1, :] = 901  # south row: the shared line as well
            data[:, 0] = 902
            data[:, -1] = 903
            _write_hgt(tmp_path / "+40+000" / f"N{lat:02d}E{lon:03d}.hgt", data)
    return EnsureOptions(elevation_dir=tmp_path, download=no_download, memo=NegativeMemo())


def test_combined_raster_places_every_neighbour_and_skips_the_shared_line(
    tmp_path: Path,
) -> None:
    value = lambda lat, lon: (lat - 45) * 3 + (lon - 4) + 1  # noqa: E731
    opts = _synthetic_block(tmp_path, value)
    lat, lon = LAND_BLOCK
    combined = build_combined_raster("View", lat, lon, opts)
    alt = combined.alt_dem
    assert alt.shape == (3673, 3673)
    inner = np.s_[1:-1]
    assert np.all(alt[36:-36, 36:-36][inner, inner] == value(lat, lon))
    # every neighbour contributes 36 lines that skip the shared line (900..903)
    assert np.all(alt[:36, 36:-36][:, inner] == value(lat + 1, lon))
    assert np.all(alt[-36:, 36:-36][:, inner] == value(lat - 1, lon))
    assert np.all(alt[36:-36, :36][inner] == value(lat, lon - 1))
    assert np.all(alt[36:-36, -36:][inner] == value(lat, lon + 1))
    assert alt[0, 0] == value(lat + 1, lon - 1)
    assert alt[-1, -1] == value(lat - 1, lon + 1)
    assert len(combined.cells) == 9


def test_a_cell_the_world_bitmap_calls_ocean_is_zero(tmp_path: Path) -> None:
    """+42+005 (the sea south of Marseille) is skipped even when a file is there."""
    for lat in (42, 43, 44):
        for lon in (4, 5, 6):
            _write_hgt(
                tmp_path / "+40+000" / f"N{lat:02d}E{lon:03d}.hgt",
                np.full((3601, 3601), 7, dtype=np.int16),
            )
    opts = EnsureOptions(elevation_dir=tmp_path, download=no_download, memo=NegativeMemo())
    events: list[object] = []
    combined = build_combined_raster("View", 43, 5, opts, on_event=events.append)
    assert np.all(combined.alt_dem[-36:, 36:-36] == 0)
    states = {c.cell: c.state for c in combined.cells}
    assert states["N42E005"] is CellState.OCEAN
    assert any(getattr(e, "code", "") == "DEM_CELL_ASSUMED_OCEAN" for e in events)


def test_a_missing_neighbour_degrades_to_zeros_and_is_reported(tmp_path: Path) -> None:
    opts = _synthetic_block(tmp_path, lambda lat, lon: 5)
    lat, lon = LAND_BLOCK
    (tmp_path / "+40+000" / f"N{lat:02d}E{lon + 1:03d}.hgt").unlink()
    events: list[object] = []
    combined = build_combined_raster("View", lat, lon, opts, on_event=events.append)
    assert np.all(combined.alt_dem[36:-36, -36:] == 0)
    codes = [getattr(e, "code", "") for e in events]
    assert "DEM_NEIGHBOUR_UNAVAILABLE" in codes
    states = {c.cell: c.state for c in combined.cells}
    assert states[f"N{lat:02d}E{lon + 1:03d}"] is CellState.MISSING


def test_info_only_gives_the_geometry_without_touching_the_disk(tmp_path: Path) -> None:
    opts = EnsureOptions(elevation_dir=tmp_path / "nope", download=no_download)
    combined = build_combined_raster("View", 43, 5, opts, info_only=True)
    assert combined.read.alt_dem is None
    assert combined.read.nxdem == 3673
    assert combined.cells == ()


def test_a_source_that_is_not_assembled_is_a_programming_error(tmp_path: Path) -> None:
    opts = EnsureOptions(elevation_dir=tmp_path, download=no_download)
    with pytest.raises(ValueError, match="not assembled"):
        build_combined_raster("NED1", 43, 5, opts)


def test_the_download_hook_is_used_for_a_missing_cell(tmp_path: Path) -> None:
    asked: list[str] = []

    def download(url: str) -> Download:
        asked.append(url)
        return Download(url, status=404)

    opts = EnsureOptions(elevation_dir=tmp_path, download=download, memo=NegativeMemo())
    build_combined_raster("View", 43, 5, opts)
    assert asked, "every land cell of the block was asked for once"
    assert all(u.startswith("http://viewfinderpanoramas.org/dem") for u in asked)


def _write_geotiff(path: Path, alt: np.ndarray, lat: int, lon: int) -> None:
    """One cell as Copernicus serves it: posts at the centre of each cell, EPSG 4326."""
    from PIL import Image, TiffImagePlugin

    rows, cols = alt.shape
    info = TiffImagePlugin.ImageFileDirectory_v2()
    info[33550] = (1 / cols, 1 / rows, 0.0)
    info[33922] = (0.0, 0.0, 0.0, float(lon), float(lat + 1), 0.0)
    info[34735] = (1, 1, 0, 1, 2048, 0, 1, 4326)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(alt).save(path, tiffinfo=info)


def test_a_copernicus_cell_coarser_in_longitude_is_stretched_not_refused() -> None:
    """Copernicus GLO-30 keeps about 30 m on the ground rather than one arc-second: from 50° of
    latitude its cells carry 2400 columns instead of 3600 (then 1800, 1200, 720, 360).

    Read as they came, those cells were called unreadable and the tile was refused: nobody above
    50° could build with Copernicus, nor with Canada's lidar, which is laid over it (a user in
    Alberta, 2026-09-18 and again on the 19th with 0.1.5).
    """
    ramp = np.arange(4, dtype=np.float32).reshape(1, 4).repeat(8, axis=0)
    out = _to_base_columns(ramp, 8)
    assert out is not None and out.shape == (8, 8)
    # posts at the centre of each cell: the ends hold the edge value, the middle interpolates
    assert out[0].tolist() == [0.0, 0.25, 0.75, 1.25, 1.75, 2.25, 2.75, 3.0]
    square = np.zeros((8, 8), dtype=np.float32)
    assert _to_base_columns(square, 8) is square  # already on the grid: not copied
    odd = np.zeros((7, 4), dtype=np.float32)  # not the rows expected: left to be refused
    assert _to_base_columns(odd, 8) is odd
    assert _to_base_columns(None, 8) is None


def test_the_block_reads_a_cell_coarser_in_longitude(tmp_path: Path) -> None:
    """The same, through the code the build runs: the cell is read, placed and never called
    unreadable. 360 posts stand here for the 3600 of the real product, 240 for its 2400."""
    lat, lon, base = 53, -114, 360
    alt = np.tile(np.arange(240, dtype=np.float32), (base, 1))
    _write_geotiff(tmp_path / "+50-120" / "N53W114_COP30.tif", alt, lat, lon)
    opts = EnsureOptions(elevation_dir=tmp_path, download=no_download, memo=NegativeMemo())
    events: list[object] = []
    result, cell = _cell_array("COP30", lat, lon, base, opts, events.append)
    assert result.state is CellState.LOCAL
    assert cell.shape == (base, base)
    assert cell[0, 0] == 0.0 and cell[0, -1] == 239.0  # the cell's own extent, end to end
    assert not [e for e in events if getattr(e, "code", "") == "DEM_FILE_UNREADABLE"]
