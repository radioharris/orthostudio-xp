# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Canada's lidar relief (``dem/hrdem.py``): the WCS of NRCan, read cell by cell.

A user of the X-Plane.Org page asked for other elevation sources, "especially for the US and
Canada" (2026-09-18). The service answers float32 GeoTIFFs, big-endian tiles over the mosaic and
little-endian strips far outside it, read by the GeoTIFF reader of the tiled sources; the cells
are assembled from blocks and written as ``.hgt``.
Every test but the one marked ``network`` builds its own answers.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

from orthostudio.dem.hrdem import (
    HgtVoid,
    cell_blocks,
    hrdem_cell,
    hrdem_url,
    read_wcs_tiff,
    write_hgt,
)
from orthostudio.dem.raster import read_elevation_from_file
from orthostudio.dem.sources import Download


def wcs_tiff(
    heights: np.ndarray,
    *,
    lon: float,
    lat: float,
    step: float,
    tile: int = 256,
    rows: int | None = None,
    order: str = ">",
) -> bytes:
    """One answer as the service writes them over the mosaic: big-endian, uncompressed, tiled,
    float32. ``rows`` writes strips of that many rows instead (the last one cut short) and
    ``order="<"`` writes it little-endian, the form of its answers far outside the mosaic.

    ``lon``/``lat`` are the *centre* of the first post, the convention the reader gives back.
    """
    height, width = heights.shape
    sample = order + "f4"
    pieces: list[bytes] = []
    if rows is None:
        for ty in range(-(-height // tile)):
            for tx in range(-(-width // tile)):
                block = np.full((tile, tile), np.float32(-32767.0), dtype=sample)
                part = heights[ty * tile : ty * tile + tile, tx * tile : tx * tile + tile]
                block[: part.shape[0], : part.shape[1]] = part
                pieces.append(block.tobytes())
    else:
        pieces = [heights[y : y + rows].astype(sample).tobytes() for y in range(0, height, rows)]
    pixels = b"".join(pieces)
    scale = struct.pack(order + "3d", step, step, 0.0)
    # the tie point names the pixel's corner: half a post north-west of the first post's centre
    tie = struct.pack(order + "6d", 0.0, 0.0, 0.0, lon - step / 2, lat + step / 2, 0.0)
    nodata = b"-32767\0"
    many = len(pieces)
    data_at = 8
    scale_at = data_at + len(pixels)
    tie_at = scale_at + len(scale)
    nodata_at = tie_at + len(tie)
    offsets_at = nodata_at + len(nodata)
    counts_at = offsets_at + 4 * many
    ifd_at = counts_at + 4 * many
    starts = [data_at + sum(len(p) for p in pieces[:i]) for i in range(many)]
    offsets = struct.pack(f"{order}{many}I", *starts)
    counts = struct.pack(f"{order}{many}I", *(len(p) for p in pieces))

    def short(value: int) -> bytes:
        return struct.pack(order + "HH", value, 0)

    def entry(tag: int, kind: int, count: int, value: int | bytes) -> tuple[int, bytes]:
        raw = value if isinstance(value, bytes) else struct.pack(order + "I", value)
        return tag, struct.pack(order + "HHI", tag, kind, count) + raw.ljust(4, b"\0")[:4]

    where = (offsets if many == 1 else offsets_at, counts if many == 1 else counts_at)
    layout = (
        [
            entry(322, 3, 1, short(tile)),
            entry(323, 3, 1, short(tile)),
            entry(324, 4, many, where[0]),
            entry(325, 4, many, where[1]),
        ]
        if rows is None
        else [
            entry(273, 4, many, where[0]),
            entry(278, 3, 1, short(rows)),
            entry(279, 4, many, where[1]),
        ]
    )
    entries = sorted(
        [
            entry(256, 3, 1, short(width)),
            entry(257, 3, 1, short(height)),
            entry(258, 3, 1, short(32)),
            entry(259, 3, 1, short(1)),
            entry(262, 3, 1, short(1)),
            entry(277, 3, 1, short(1)),
            entry(284, 3, 1, short(2)),
            entry(339, 3, 1, short(3)),
            entry(33550, 12, 3, scale_at),
            entry(33922, 12, 6, tie_at),
            entry(42113, 2, len(nodata), nodata_at),
            *layout,
        ]
    )
    ifd = (
        struct.pack(order + "H", len(entries))
        + b"".join(raw for _tag, raw in entries)
        + struct.pack(order + "I", 0)
    )
    head = struct.pack(order + "2sHI", b"MM" if order == ">" else b"II", 42, ifd_at)
    return head + pixels + scale + tie + nodata + offsets + counts + ifd


def test_the_wcs_url_puts_the_latitude_first() -> None:
    """EPSG:4326 in its URN form has latitude first: written the other way round the service
    builds an inverted extent and answers 500 (measured on it, 2026-09-18)."""
    url = hrdem_url(45.0, -76.0, 45.25, -75.75, 901, 901)
    assert "identifier=dtm" in url and "format=image/geotiff" in url
    assert "boundingbox=45.000000000,-76.000000000,45.250000000,-75.750000000," in url
    assert "urn:ogc:def:crs:EPSG::4326" in url
    assert "gridorigin=45.250000000,-76.000000000" in url  # the north-west corner
    lat_step, lon_step = url.split("gridoffsets=")[1].split(",")
    assert float(lat_step) == pytest.approx(-0.25 / 900)  # southwards
    assert float(lon_step) == pytest.approx(0.25 / 900)


def test_a_wcs_answer_is_read_where_pillow_reads_it_wrong() -> None:
    """Pillow reads these files' tags but decodes their samples in the wrong byte order; the
    pixels are read from the tile offsets instead. The nodata of the service becomes the void of
    a ``.hgt``, and the geometry given back is the *centre* of the first post."""
    heights = np.arange(12 * 10, dtype=np.float32).reshape(10, 12) + 100.0
    heights[3, 4] = -32767.0  # the service's nodata
    heights[5, 5] = 1e30  # and a value no ground has
    body = wcs_tiff(heights, lon=-76.0, lat=46.0, step=0.001, tile=8)

    read, (lon, lat, dlon, dlat) = read_wcs_tiff(body)

    assert read.shape == (10, 12)
    assert read[0, 0] == 100.0 and read[9, 11] == 219.0
    assert read[3, 4] == HgtVoid and read[5, 5] == HgtVoid
    assert (lon, lat) == pytest.approx((-76.0, 46.0))
    assert (dlon, dlat) == pytest.approx((0.001, 0.001))


def test_tiles_and_strips_are_read_in_the_byte_order_the_file_declares() -> None:
    """Over the mosaic the service answers big-endian tiles; far outside it, little-endian
    strips. The reader takes the layout and the byte order each file declares: read the other
    way, the void of a little-endian answer is a height of 0 m (2.3e-38), a flat cell of lidar."""
    heights = np.arange(7 * 5, dtype=np.float32).reshape(7, 5) + 100.0
    heights[2, 3] = -32767.0
    expected = np.where(heights == -32767.0, np.float32(HgtVoid), heights)
    for rows in (None, 3):  # the last strip of three is cut short to one row
        for order in "<>":
            body = wcs_tiff(
                heights, lon=-76.0, lat=46.0, step=0.001, tile=4, rows=rows, order=order
            )

            read, (lon, lat, _dlon, _dlat) = read_wcs_tiff(body)

            assert (read == expected).all(), (rows, order)
            assert (lon, lat) == pytest.approx((-76.0, 46.0))


def test_the_answer_far_outside_the_mosaic_is_an_empty_cell(tmp_path: Path) -> None:
    """A build of +21-083 (Cuba) over Canada's lidar stopped at the relief with "the raster is
    not tiled" (2026-11-15). The coverage request there is answered by one void pixel,
    little-endian, in a strip and in Canada's Lambert projection: its real 401 bytes are kept
    here. It is an empty cell: one request, remembered, and the relief under it answers alone."""
    from orthostudio.dem.sources import CellState, EnsureOptions, NegativeMemo, ensure_elevation

    body = (Path(__file__).parent / "data" / "hrdem" / "outside-the-mosaic-21-083.tif").read_bytes()
    heights, _geo = read_wcs_tiff(body)
    assert heights.shape == (1, 1) and heights[0, 0] == HgtVoid

    asked: list[str] = []

    def download(url: str) -> Download:
        asked.append(url)
        return Download(url, body=body, status=200)

    memo = NegativeMemo(tmp_path / "misses.json")
    opts = EnsureOptions(elevation_dir=tmp_path, download=download, memo=memo)
    assert ensure_elevation("HRDEM", 21, -83, opts).state is CellState.MISSING
    assert len(asked) == 1 and len(memo.misses) == 1


def test_an_answer_that_is_no_raster_is_a_service_that_did_not_answer(tmp_path: Path) -> None:
    """An exception report served with a 200 stopped the build; it is the service not answering:
    the cell is missing this time, nothing is remembered, and the relief under it answers."""
    from orthostudio.dem.hrdem import ProbeError
    from orthostudio.dem.sources import CellState, EnsureOptions, NegativeMemo, ensure_elevation

    report = b'<?xml version="1.0"?><ows:ExceptionReport><ows:Exception/></ows:ExceptionReport>'
    with pytest.raises(ProbeError):
        read_wcs_tiff(report)

    def download(url: str) -> Download:
        return Download(url, body=report, status=200)

    memo = NegativeMemo(tmp_path / "misses.json")
    opts = EnsureOptions(elevation_dir=tmp_path, download=download, memo=memo)
    assert ensure_elevation("HRDEM", 21, -83, opts).state is CellState.MISSING
    assert not memo.misses


def _server(cover: np.ndarray, posts: int, lat: int, lon: int):
    """A service that answers any window from one array of the cell, nodata outside it."""
    asked: list[str] = []

    def download(url: str) -> Download:
        asked.append(url)
        box = url.split("boundingbox=")[1].split(",urn")[0].split(",")
        lat0, lon0, lat1, lon1 = (float(v) for v in box)
        nx = round((lon1 - lon0) * (posts - 1)) + 1
        ny = round((lat1 - lat0) * (posts - 1)) + 1
        x0 = round((lon0 - lon) * (posts - 1))
        y0 = round((lat + 1 - lat1) * (posts - 1))
        out = np.full((ny, nx), np.float32(-32767.0), dtype=np.float32)
        ys, xs = slice(max(0, y0), y0 + ny), slice(max(0, x0), x0 + nx)
        part = cover[ys, xs]
        out[: part.shape[0], : part.shape[1]] = part
        # the service rounds a window down by a post, which is why a margin is asked for
        body = wcs_tiff(
            out[:-1, :-1] if ny > 2 else out,
            lon=lon + x0 / (posts - 1),
            lat=lat + 1 - y0 / (posts - 1),
            step=1 / (posts - 1),
            tile=64,
        )
        return Download(url, body=body, status=200)

    return download, asked


def test_a_cell_is_stitched_from_the_blocks_that_hold_lidar() -> None:
    """The coverage request comes first, and only the blocks it saw lidar in are asked for: a
    Canadian cell is mostly outside the flown areas (Calgary: 44 % of the square, measured)."""
    posts, side = 41, 21
    cover = np.full((posts, posts), np.float32(-32767.0), dtype=np.float32)
    cover[0:10, 0:10] = np.arange(100, dtype=np.float32).reshape(10, 10)  # a patch, north-west
    download, asked = _server(cover, posts, 45, -76)

    cell = hrdem_cell(45, -76, download, posts=posts, side=side, probe=9)

    assert cell is not None
    assert len(asked) == 2  # the coverage request, then the one block the patch falls in
    assert (cell[0:10, 0:10] == cover[0:10, 0:10]).all()
    assert (cell[25:, 25:] == HgtVoid).all()  # the rest of the cell keeps its voids


def test_a_cell_the_lidar_does_not_reach_costs_one_request() -> None:
    posts = 41
    nothing = np.full((posts, posts), np.float32(-32767.0), dtype=np.float32)
    download, asked = _server(nothing, posts, 54, -72)

    assert hrdem_cell(54, -72, download, posts=posts, side=21, probe=9) is None
    assert len(asked) == 1


def test_a_service_that_does_not_answer_is_not_remembered_as_empty(tmp_path: Path) -> None:
    """A user in Alberta reported the lidar "will not load" over a square the mosaic covers at
    85 % (2026-09-19). A coverage request that fails is not an empty cell: remembering it as one
    would hide the lidar there for the month the negative memo keeps a miss."""
    from orthostudio.dem.hrdem import ProbeError, hrdem_cell
    from orthostudio.dem.sources import CellState, EnsureOptions, NegativeMemo, ensure_elevation

    def broken(url: str) -> Download:
        return Download(url, error="NET_TIMEOUT")

    with pytest.raises(ProbeError):
        hrdem_cell(53, -114, broken, posts=41, side=21, probe=9)

    memo = NegativeMemo(tmp_path / "misses.json")
    opts = EnsureOptions(elevation_dir=tmp_path, download=broken, memo=memo)
    result = ensure_elevation("HRDEM", 53, -114, opts)
    assert result.state is CellState.MISSING
    assert not memo.misses  # nothing remembered: the next build asks again

    # a cell the service says is empty *is* remembered, so it costs one request and no more
    posts = 41
    nothing = np.full((posts, posts), np.float32(-32767.0), dtype=np.float32)
    quiet, _ = _server(nothing, posts, 54, -72)
    opts = EnsureOptions(elevation_dir=tmp_path, download=quiet, memo=memo)
    assert ensure_elevation("HRDEM", 54, -72, opts).state is CellState.MISSING
    assert len(memo.misses) == 1


def test_the_written_cell_is_read_back_by_the_engine(tmp_path: Path) -> None:
    """``.hgt``: big-endian int16, north-up, ``-32768`` for the voids -- the format the raster
    reader already knows, so nothing else in the pipeline learns a new one."""
    posts = 61
    cell = np.full((posts, posts), np.float32(HgtVoid), dtype=np.float32)
    cell[10:20, 10:20] = 123.4
    path = tmp_path / "N45W076_HRDEM.hgt"

    write_hgt(path, cell)
    read = read_elevation_from_file(path, 45, -76)

    assert path.stat().st_size == posts * posts * 2
    assert (read.nxdem, read.nydem, read.epsg) == (posts, posts, 4326)
    assert read.alt_dem is not None
    assert read.alt_dem[15, 15] == 123.0  # rounded to the metre
    assert read.alt_dem[0, 0] == HgtVoid


def test_the_blocks_cover_the_cell_edge_to_edge() -> None:
    blocks = cell_blocks(45, -76)
    assert len(blocks) == 16
    assert (blocks[0].lat1, blocks[0].lon0) == (46.0, -76.0)
    assert blocks[-1].lat0 == pytest.approx(45.0) and blocks[-1].lon1 == pytest.approx(-75.0)
    assert {b.x0 for b in blocks} == {0, 900, 1800, 2700}


def test_the_canadian_relief_is_laid_over_copernicus() -> None:
    """Coverage is partial, so the lidar is an *overlay*: Copernicus answers everywhere else,
    which is what the composite of ``custom_dem`` is for."""
    from orthostudio.api.jobs import _relief_of
    from orthostudio.config.models import Essential, Relief, Settings
    from orthostudio.config.overrides import to_build_overrides

    settings = Settings(essential=Essential(relief=Relief(source="canada")))
    assert to_build_overrides(settings)["custom_dem"] == "COP30;HRDEM"

    class _Spec:
        config: ClassVar[dict[str, str]] = {"custom_dem": "COP30;HRDEM"}
        relief = "xplane"

    assert _relief_of(_Spec()) == "canada"


def test_a_missing_overlay_lays_the_base_alone_instead_of_refusing(
    tmp_path: Path, view_site_at_3sec: None
) -> None:
    """Over a cell the lidar never flew, the build must go on with the relief underneath (the
    tile is not refused, and it is not flat either)."""
    from orthostudio.dem.dem import Dem
    from orthostudio.dem.sources import EnsureOptions, NegativeMemo, elevation_path
    from orthostudio.errors import OsxpError
    from orthostudio.model import TileRef

    path = elevation_path("View", tmp_path, 45, -76)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(np.full((1201, 1201), 700, dtype=">i2").tobytes())
    events: list[OsxpError] = []
    opts = EnsureOptions(elevation_dir=tmp_path, memo=NegativeMemo())

    dem = Dem.build(TileRef(45, -76), opts, custom_dem="View;HRDEM", on_event=events.append)

    assert dem.overlays == ()  # no lidar here: the base answers alone
    assert dem.alt_vec(np.array([[0.5, 0.5]]))[0] == 700.0  # and the tile is not flat at 0 m
    assert [e.code for e in events if e.code == "DEM_OVERLAY_UNAVAILABLE"]


@pytest.mark.network
def test_the_service_answers_the_url_this_module_builds() -> None:
    """One coverage request to NRCan (64 KB): the shape of the URL and of the answer are the
    contract this module depends on, and an endpoint that moves must be seen."""
    import urllib.request

    url = hrdem_url(45, -76, 46, -75, 64, 64)
    with urllib.request.urlopen(url, timeout=60) as answer:
        body = answer.read()
    heights, (lon, lat, _dlon, _dlat) = read_wcs_tiff(body)

    # the service rounds a window down by a post or two (62x62 for 64x64, measured): the module
    # asks for a margin and places an answer where its own geometry says, never where it asked
    assert 58 <= heights.shape[0] <= 64 and heights.shape[0] == heights.shape[1]
    assert (lon, lat) == pytest.approx((-76.0, 46.0), abs=0.05)
    real = heights[heights != HgtVoid]
    assert real.size > 3000  # Ottawa and its surroundings are flown
    assert 20.0 < float(real.min()) < float(real.max()) < 600.0
