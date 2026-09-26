# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Elevation file names, viewfinderpanoramas URLs, zip extraction and the negative memo.

Spec: ``docs/specs/dem.md`` sections 2, 3 (acceptance A8, A9).
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from orthostudio.dem.sources import (
    DEM1_BLOCKS,
    DEM1_CELLS,
    FULL_HGT_SIZE,
    CellState,
    Download,
    EnsureOptions,
    NegativeMemo,
    base_file_name,
    cell_file_in_folder,
    cells_of_block,
    cop30_name,
    cop30_url,
    elevation_path,
    ensure_elevation,
    extract_view_zip,
    hem_latlon,
    ned_url,
    no_download,
    round_latlon,
    view_url,
)

# -- naming (spec 2) ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [
        (43, 5, "N43E005"),
        (0, 0, "N00E000"),
        (-1, -1, "S01W001"),
        (-34, -59, "S34W059"),
        (60, 179, "N60E179"),
    ],
)
def test_hem_latlon(lat: int, lon: int, expected: str) -> None:
    assert hem_latlon(lat, lon) == expected


@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [(43, 5, "+40+000"), (-1, -1, "-10-010"), (0, 0, "+00+000"), (-34, -59, "-40-060")],
)
def test_round_latlon_floors_towards_minus_infinity(lat: int, lon: int, expected: str) -> None:
    assert round_latlon(lat, lon) == expected


def test_base_file_name_and_suffixes(tmp_path: Path) -> None:
    assert base_file_name(tmp_path, 43, 5) == tmp_path / "+40+000" / "N43E005"
    assert elevation_path("View", tmp_path, 43, 5).name == "N43E005.hgt"
    assert elevation_path("SRTM", tmp_path, 43, 5).name == "N43E005_SRTMv3.hgt"
    assert elevation_path("ALOS", tmp_path, 43, 5).name == "N43E005_ALOS3W30.tif"
    assert elevation_path("NED1", tmp_path, 43, 5).name == "N43E005_NED1.tif"
    assert elevation_path("NED1/3", tmp_path, 43, 5).name == "N43E005_NED13.tif"
    with pytest.raises(ValueError, match="unknown elevation source"):
        elevation_path("nope", tmp_path, 43, 5)


# -- URLs (spec 3.1, 3.3) -----------------------------------------------------------------


def test_view_url_dem1_cells() -> None:
    assert len(DEM1_CELLS) == 34
    assert view_url(44, 5) == ("http://viewfinderpanoramas.org/dem1/n44e005.zip", 1)
    assert view_url(47, 15) == ("http://viewfinderpanoramas.org/dem1/n47e015.zip", 1)


def test_view_url_dem3_blocks_and_resolution() -> None:
    assert len(DEM1_BLOCKS) == 22
    # +43+005 is not a dem1 cell: block K31, 3 arc seconds.
    assert view_url(43, 5) == ("http://viewfinderpanoramas.org/dem3/K31.zip", 3)
    # A block listed in DEM1_BLOCKS keeps the dem3 URL but claims 1" resolution.
    assert view_url(60, 3) == ("http://viewfinderpanoramas.org/dem3/P31.zip", 1)
    # Southern hemisphere: the letter is prefixed with S and counted from -1.
    url, resol = view_url(-34, -59)
    assert url.endswith("/dem3/SI21.zip")
    assert resol == 3


def test_ned_url_carries_the_latitude_for_eastern_tiles() -> None:
    """Ortho4XP drops the latitude when ``lon >= 0`` (``O4_DEM_Utils.py:816-818``); OrthoStudio XP
    does not."""
    assert ned_url("NED1", 43, -72).endswith("/n44w072/USGS_1_n44w072.tif")
    assert ned_url("NED1/3", 43, 5).endswith("/n44e005/USGS_13_n44e005.tif")


def test_cells_of_block_order_matches_ortho4xp() -> None:
    assert cells_of_block(43, 5) == [
        (43, 5), (43, 4), (43, 6),
        (42, 5), (42, 4), (42, 6),
        (44, 5), (44, 4), (44, 6),
    ]  # fmt: skip


def test_cells_of_block_wraps_the_antimeridian() -> None:
    assert cells_of_block(0, 179)[2] == (0, -180)
    assert cells_of_block(0, -180)[1] == (0, 179)


# -- zip extraction (spec 3.1) ------------------------------------------------------------


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return buf.getvalue()


def test_extract_view_zip_writes_every_parsable_member(tmp_path: Path) -> None:
    blob = _zip(
        {
            "K31/N43E005.hgt": b"\x00\x01" * 8,
            "K31/s01w002.hgt": b"\x00\x02" * 8,
            "K31/readme.txt": b"hello",
            "K31/": b"",
        }
    )
    written = extract_view_zip(blob, tmp_path)
    assert sorted(p.name for p in written) == ["N43E005.hgt", "S01W002.hgt"]
    assert (tmp_path / "+40+000" / "N43E005.hgt").read_bytes() == b"\x00\x01" * 8
    assert (tmp_path / "-10-010" / "S01W002.hgt").exists()
    assert not (tmp_path / "+40+000" / "readme.txt").exists()


def test_extract_view_zip_does_not_shrink_an_existing_file(tmp_path: Path) -> None:
    out = elevation_path("View", tmp_path, 43, 5)
    out.parent.mkdir(parents=True)
    out.write_bytes(b"x" * 100)
    extract_view_zip(_zip({"N43E005.hgt": b"y" * 10}), tmp_path)
    assert out.read_bytes() == b"x" * 100
    extract_view_zip(_zip({"N43E005.hgt": b"y" * 500}), tmp_path)
    assert out.read_bytes() == b"y" * 500


# -- negative memo (spec 3.4) -------------------------------------------------------------


def test_negative_memo_records_and_expires(tmp_path: Path) -> None:
    memo = NegativeMemo(tmp_path / "misses.json", ttl_s=100)
    url = "http://viewfinderpanoramas.org/dem1/n44e005.zip"
    assert not memo.is_missing(url, now=0)
    memo.record(url, now=0)
    assert memo.is_missing(url, now=99)
    assert not memo.is_missing(url, now=101)
    memo.forget(url)
    assert not memo.is_missing(url, now=0)


def test_negative_memo_round_trips_through_disk(tmp_path: Path) -> None:
    path = tmp_path / "dem" / "misses.json"
    memo = NegativeMemo(path, ttl_s=100)
    memo.record("http://a/x.zip", now=10)
    memo.save()
    doc = json.loads(path.read_text())
    assert doc["format"] == NegativeMemo.FORMAT
    again = NegativeMemo(path, ttl_s=100)
    assert again.is_missing("http://a/x.zip", now=20)


def test_negative_memo_ignores_a_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "misses.json"
    path.write_text("{not json")
    assert NegativeMemo(path).misses == {}
    path.write_text('{"format": "other", "misses": {"u": 1}}')
    assert NegativeMemo(path).misses == {}


def test_memo_without_a_path_is_in_memory_only(tmp_path: Path) -> None:
    memo = NegativeMemo()
    memo.record("http://a", now=0)
    memo.save()
    assert list(tmp_path.iterdir()) == []


# -- ensure_elevation (spec 3) ------------------------------------------------------------


def _opts(tmp_path: Path, download, **kw) -> EnsureOptions:  # type: ignore[no-untyped-def]
    return EnsureOptions(elevation_dir=tmp_path, download=download, **kw)


def test_copernicus_cell_name_url_and_download(tmp_path: Path) -> None:
    """GLO-30, the source a user asked for (2026-09-17): one GeoTIFF per cell on the public
    store, kept in the elevation folder, and a cell all at sea has none."""
    assert cop30_name(46, 6) == "Copernicus_DSM_COG_10_N46_00_E006_00_DEM"
    assert cop30_name(-1, -78) == "Copernicus_DSM_COG_10_S01_00_W078_00_DEM"
    url = cop30_url(46, 6)
    assert url == (
        "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N46_00_E006_00_DEM"
        "/Copernicus_DSM_COG_10_N46_00_E006_00_DEM.tif"
    )
    calls: list[str] = []

    def download(asked: str) -> Download:
        calls.append(asked)
        return Download(asked, status=200, body=b"II*\x00 not really a tiff")

    opts = _opts(tmp_path, download, memo=NegativeMemo())
    got = ensure_elevation("COP30", 46, 6, opts)
    assert got.state is CellState.DOWNLOADED and got.path is not None
    assert got.path == elevation_path("COP30", tmp_path, 46, 6)
    assert got.path.name == "N46E006_COP30.tif" and got.path.read_bytes().startswith(b"II*")
    assert calls == [url]
    # the file is there: the next build reads it without asking again
    again = ensure_elevation("COP30", 46, 6, opts)
    assert again.state is CellState.LOCAL and len(calls) == 1
    assert not list(tmp_path.rglob("*.part"))


def test_a_copernicus_cell_all_at_sea_is_remembered_as_missing(tmp_path: Path) -> None:
    calls: list[str] = []

    def download(asked: str) -> Download:
        calls.append(asked)
        return Download(asked, status=404)

    opts = _opts(tmp_path, download, memo=NegativeMemo())
    assert ensure_elevation("COP30", 30, -40, opts).state is CellState.MISSING
    assert ensure_elevation("COP30", 30, -40, opts).state is CellState.MISSING
    assert len(calls) == 1  # the 404 is remembered, as for the other sources


def test_recycles_a_3sec_file_without_asking_the_network(tmp_path: Path) -> None:
    path = elevation_path("View", tmp_path, 43, 5)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x00" * 2884802)
    calls: list[str] = []

    def download(url: str) -> Download:
        calls.append(url)
        raise AssertionError("must not download")

    result = ensure_elevation("View", 43, 5, _opts(tmp_path, download))
    assert result.state is CellState.LOCAL
    assert calls == []


def test_a_1sec_cell_with_only_a_3sec_file_is_downloaded_again(tmp_path: Path) -> None:
    """``O4_DEM_Utils.py:669-676``: the size test is what makes Ortho4XP re-ask for dem1 zips."""
    path = elevation_path("View", tmp_path, 44, 5)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x00" * 2884802)
    calls: list[str] = []

    def download(url: str) -> Download:
        calls.append(url)
        return Download(url, status=404)

    opts = _opts(tmp_path, download, memo=NegativeMemo())
    first = ensure_elevation("View", 44, 5, opts)
    assert first.state is CellState.MISSING
    assert calls == ["http://viewfinderpanoramas.org/dem1/n44e005.zip"]
    # the 404 is remembered: the second run does not ask again (the OrthoStudio XP fix)
    second = ensure_elevation("View", 44, 5, opts)
    assert second.state is CellState.MISSING
    assert len(calls) == 1


def test_a_full_1sec_file_is_recycled(tmp_path: Path) -> None:
    path = elevation_path("View", tmp_path, 44, 5)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x00" * FULL_HGT_SIZE)
    assert ensure_elevation("View", 44, 5, _opts(tmp_path, no_download)).state is CellState.LOCAL


def test_a_server_error_is_not_memoised(tmp_path: Path) -> None:
    calls: list[str] = []

    def download(url: str) -> Download:
        calls.append(url)
        return Download(url, status=503, error="NET_SERVER_ERROR")

    opts = _opts(tmp_path, download, memo=NegativeMemo())
    ensure_elevation("View", 43, 5, opts)
    ensure_elevation("View", 43, 5, opts)
    assert len(calls) == 2


def test_a_successful_download_is_extracted(tmp_path: Path) -> None:
    blob = _zip({"K31/N43E005.hgt": b"\x00\x01" * 8})

    def download(url: str) -> Download:
        return Download(url, body=blob, status=200)

    result = ensure_elevation("View", 43, 5, _opts(tmp_path, download))
    assert result.state is CellState.DOWNLOADED
    assert result.path is not None and result.path.is_file()


def test_dem1_local_fallback_is_off_by_default(tmp_path: Path) -> None:
    path = elevation_path("View", tmp_path, 44, 5)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x00" * 2884802)
    strict = ensure_elevation("View", 44, 5, _opts(tmp_path, no_download))
    assert strict.state is CellState.MISSING
    lenient = ensure_elevation(
        "View", 44, 5, _opts(tmp_path, no_download, dem1_local_fallback=True)
    )
    assert lenient.state is CellState.LOCAL


def test_srtm_and_alos_never_download(tmp_path: Path) -> None:
    def download(url: str) -> Download:
        raise AssertionError("SRTM/ALOS must not be downloaded")

    for source in ("SRTM", "ALOS"):
        result = ensure_elevation(source, 43, 5, _opts(tmp_path, download))
        assert result.state is CellState.MISSING
        assert "manual" in result.detail
    path = elevation_path("SRTM", tmp_path, 43, 5)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 8)
    assert ensure_elevation("SRTM", 43, 5, _opts(tmp_path, download)).state is CellState.LOCAL


def test_ned_downloads_the_tif_as_is(tmp_path: Path) -> None:
    def download(url: str) -> Download:
        return Download(url, body=b"II*\x00fake", status=200)

    result = ensure_elevation("NED1", 43, -72, _opts(tmp_path, download))
    assert result.state is CellState.DOWNLOADED
    assert result.path is not None
    assert result.path.read_bytes() == b"II*\x00fake"


def test_unknown_source_is_a_programming_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown elevation source"):
        ensure_elevation("GLO30", 43, 5, _opts(tmp_path, no_download))


def test_the_finest_file_of_a_folder_wins_and_the_answer_never_depends_on_the_disk(
    tmp_path: Path,
) -> None:
    """The same sets come at 3", 1" and 0.5", under the same names, one folder each, and a user who
    has all three keeps all three (X-Plane.Org, 2026-09-19).

    The candidate with the most points is taken, so each square is built with the finest file the
    folder holds for it. Sizes here stand for the real ones: 1201, 3601 and 7201 points a side.
    """
    root = tmp_path / "Sonny"
    for name, side in (("Austria_3", 4), ("Austria_1", 8), ("Austria_05", 16)):
        (root / name).mkdir(parents=True)
        np.zeros((side, side), dtype=">i2").tofile(root / name / "N47E011.hgt")
    # one square he only has coarsely
    np.zeros((4, 4), dtype=">i2").tofile(root / "Austria_3" / "N48E011.hgt")

    assert cell_file_in_folder(root, 47, 11) == root / "Austria_05" / "N47E011.hgt"
    assert cell_file_in_folder(root, 48, 11) == root / "Austria_3" / "N48E011.hgt"
    assert cell_file_in_folder(root, 46, 11) is None
    # a GeoTIFF is weighed by its header, not by the bytes its compression happens to take
    tif = root / "Geotiffs"
    tif.mkdir()
    _write_small_geotiff(tif / "N48E011.tif", 6)
    assert cell_file_in_folder(root, 48, 11) == tif / "N48E011.tif"


def _write_small_geotiff(path: Path, side: int) -> None:
    from PIL import Image, TiffImagePlugin

    info = TiffImagePlugin.ImageFileDirectory_v2()
    info[33550] = (1 / side, 1 / side, 0.0)
    info[33922] = (0.0, 0.0, 0.0, 11.0, 49.0, 0.0)
    info[34735] = (1, 1, 0, 1, 2048, 0, 1, 4326)
    Image.fromarray(np.zeros((side, side), dtype=np.float32)).save(path, tiffinfo=info)


# -- ANADEM, South America (spec 3.0b) ---------------------------------------------------------


def test_anadem_zones_and_urls() -> None:
    """A one-degree square never straddles an MGRS zone, so each has exactly one file; a square
    outside what they published has none, and Copernicus answers there."""
    from orthostudio.dem.sources import ANADEM_ZONES, anadem_url, anadem_zone

    assert len(ANADEM_ZONES) == 52
    assert anadem_zone(-10, -60) == "21L"  # the Amazon
    assert anadem_zone(-23, -47) == "23K"  # Sao Paulo
    assert anadem_zone(-35, -59) == "21H"  # Buenos Aires
    assert anadem_zone(10, -70) == "19P"  # Venezuela
    assert anadem_zone(46, 6) is None and anadem_zone(-10, 20) is None
    assert anadem_url("21L").endswith("/anadem_v1_21L.tif")


def test_anadem_reads_only_its_square_and_writes_it(tmp_path: Path) -> None:
    """The zone is 2 GB: the header is read once, then the tiles the square falls in, and what
    comes back is written as a small GeoTIFF of that square alone."""
    import numpy as np
    from tests.test_dem_cog import tiled_tiff

    from orthostudio.dem.sources import CellState, EnsureOptions, ensure_elevation

    # a zone of ten squares, of which we want one
    rows = cols = 64
    values = np.arange(rows * cols, dtype=np.float32).reshape(rows, cols) % 300
    blob = tiled_tiff(values)
    # the fixture's grid: 0.001 degree from (-60, -9); the square -60..-59.936 fits in it
    asked: list[tuple[int, int]] = []

    def ranges(url: str, parts: list[tuple[int, int]]) -> list[bytes]:
        assert url.endswith("anadem_v1_21L.tif")
        asked.extend(parts)
        return [blob[at : at + size] for at, size in parts]

    opts = EnsureOptions(elevation_dir=tmp_path, ranges=ranges)
    got = ensure_elevation("ANADEM", -10, -60, opts)
    assert got.state is CellState.DOWNLOADED and got.path is not None
    assert got.path.name == "S10W060_ANADEM.tif"
    from orthostudio.dem.cog import HEADER_BYTES

    assert asked[0] == (0, HEADER_BYTES)  # the header first, in one read
    tiles = asked[1:]
    assert tiles and sum(size for _at, size in tiles) < len(blob)  # then a part of the zone

    again = ensure_elevation("ANADEM", -10, -60, opts)
    assert again.state is CellState.LOCAL  # kept, and never asked for twice


def test_a_square_outside_anadem_asks_nothing(tmp_path: Path) -> None:
    from orthostudio.dem.sources import CellState, EnsureOptions, ensure_elevation

    def ranges(url: str, parts: list[tuple[int, int]]) -> list[bytes]:
        raise AssertionError("nothing to ask for outside the zones published")

    got = ensure_elevation("ANADEM", 46, 6, EnsureOptions(elevation_dir=tmp_path, ranges=ranges))
    assert got.state is CellState.MISSING and got.path is None
