"""Global Scenery rasters: synthetic DSFs (plain and 7z) and the user's X-Plane 12 install."""

from __future__ import annotations

import hashlib
import io
import struct
from pathlib import Path

import numpy as np
import py7zr
import pytest

from orthostudio.dsf import Xp12Rasters, extract_xp12_rasters, rasters_from_dsf
from orthostudio.dsf.container import Atom, DsfFile, parse_dsf
from orthostudio.dsf.xp12 import DEMO_AREAS, clamp_bathymetry, global_scenery_dsf
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.overlays.source import overlay_source_path

TILE = TileRef(43, 5)


def _find(dsf: DsfFile, path: str) -> Atom:
    atom = dsf.find(path)
    assert atom is not None, path
    return atom


def _atom(name: str, payload: bytes) -> bytes:
    return name.encode()[::-1] + struct.pack("<I", 8 + len(payload)) + payload


def _demi(width: int, height: int, bpp: int = 2, flags: int = 5) -> bytes:
    return _atom("DEMI", struct.pack("<BBHIIff", 1, bpp, flags, width, height, 1.0, 0.0))


def synthetic_dsf(
    elev: np.ndarray, bathy: np.ndarray, *, names: bytes = b"elevation\0sea_level\0"
) -> bytes:
    """A minimal DSF with a DEMN and a DEMS holding two 10x10 int16 rasters."""
    defn = _atom("TERT", b"terrain_Water\0") + _atom("DEMN", names)
    dems = (
        _demi(10, 10)
        + _atom("DEMD", elev.astype("<i2").tobytes())
        + _demi(10, 10)
        + _atom("DEMD", bathy.astype("<i2").tobytes())
        + _demi(2, 2, 1, 6)
        + _atom("DEMD", b"\x01\x02\x03\x04")
    )
    body = b"XPLNEDSF" + struct.pack("<I", 1) + _atom("HEAD", _atom("PROP", b"sim/west\x005\x00"))
    body += _atom("DEFN", defn) + _atom("GEOD", b"") + _atom("CMDS", b"") + _atom("DEMS", dems)
    return body + hashlib.md5(body).digest()


@pytest.fixture
def rasters_pair() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(1)
    elev = rng.integers(-5, 50, 100).astype(np.int16)
    bathy = rng.integers(-40, 60, 100).astype(np.int16)
    return elev, bathy


def test_clamp_bathymetry_only_touches_the_second_large_raster(rasters_pair) -> None:
    elev, bathy = rasters_pair
    data = synthetic_dsf(elev, bathy)
    r = rasters_from_dsf(data, tile=TILE)
    assert r.demn == b"elevation\0sea_level\0"
    dsf = parse_dsf(data)
    original = _find(dsf, "DEMS").payload(data)
    assert len(r.dems) == len(original)
    demd = [a for a in _find(dsf, "DEMS").children or [] if a.name == "DEMD"]
    base = _find(dsf, "DEMS").payload_offset
    e0 = demd[0].payload_offset - base
    b0 = demd[1].payload_offset - base
    got_elev = np.frombuffer(r.dems[e0 : e0 + 200], dtype="<i2")
    got_bathy = np.frombuffer(r.dems[b0 : b0 + 200], dtype="<i2")
    assert np.array_equal(got_elev, elev)
    assert np.array_equal(got_bathy, np.minimum(bathy, elev - 2))
    assert (got_bathy < bathy).any() and (got_bathy == bathy).any()
    # everything but the bathymetry bytes is untouched
    mask = np.ones(len(original), dtype=bool)
    mask[b0 : b0 + 200] = False
    assert (
        np.frombuffer(r.dems, dtype=np.uint8)[mask].tobytes()
        == np.frombuffer(original, dtype=np.uint8)[mask].tobytes()
    )
    assert clamp_bathymetry(original) == r.dems


def test_extract_from_7z_and_plain_files(tmp_path: Path, rasters_pair) -> None:
    elev, bathy = rasters_pair
    data = synthetic_dsf(elev, bathy)
    scenery = tmp_path / "Global Scenery"
    plain = global_scenery_dsf(scenery, TILE)
    plain.parent.mkdir(parents=True)
    plain.write_bytes(data)
    from_plain = extract_xp12_rasters(scenery, TILE)
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as z:
        z.writestr(data, "+43+005.dsf")
    plain.write_bytes(buf.getvalue())
    assert plain.read_bytes()[:6] == b"7z\xbc\xaf\x27\x1c"
    from_7z = extract_xp12_rasters(scenery, TILE)
    assert from_7z == from_plain == rasters_from_dsf(data)
    assert isinstance(from_7z, Xp12Rasters)


def test_a_tile_only_in_the_demo_areas_is_read_there(tmp_path: Path, rasters_pair) -> None:
    """X-Plane 12 keeps Maui to Kauai in ``X-Plane 12 Demo Areas`` only, and ``+20-160`` of its
    Global Scenery is an empty folder: a user who had installed every part of the world read
    that Hawaii's scenery was not (2026-09-22)."""
    elev, bathy = rasters_pair
    data = synthetic_dsf(elev, bathy)
    gs = tmp_path / "Global Scenery" / "X-Plane 12 Global Scenery"
    demo = tmp_path / "Global Scenery" / DEMO_AREAS
    oahu, innsbruck, sea = TileRef(21, -158), TileRef(47, 11), TileRef(20, -160)
    (gs / oahu.dsf_relpath).parent.mkdir(parents=True)
    for root, tile in ((demo, oahu), (demo, innsbruck), (gs, innsbruck)):
        path = root / tile.dsf_relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    assert global_scenery_dsf(gs, oahu) == demo / oahu.dsf_relpath
    assert extract_xp12_rasters(gs, oahu) == rasters_from_dsf(data)
    assert overlay_source_path(tmp_path, 21, -158) == demo / oahu.dsf_relpath  # from X-Plane's root
    assert global_scenery_dsf(gs, innsbruck) == gs / innsbruck.dsf_relpath  # in both: the first
    with pytest.raises(OsxpError) as exc:
        extract_xp12_rasters(gs, sea)
    assert exc.value.context["path"] == str(gs / sea.dsf_relpath)  # in neither: the first named


def test_errors_are_coded(tmp_path: Path, rasters_pair) -> None:
    elev, bathy = rasters_pair
    scenery = tmp_path / "gs"
    with pytest.raises(OsxpError) as exc:
        extract_xp12_rasters(scenery, TILE)
    assert exc.value.code == "DSF_GLOBAL_SCENERY_MISSING" and exc.value.context["tile"] == "+43+005"
    path = global_scenery_dsf(scenery, TILE)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a dsf at all")
    with pytest.raises(OsxpError) as exc:
        extract_xp12_rasters(scenery, TILE)
    assert exc.value.code == "DSF_SOURCE_CORRUPTED"
    path.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\0" * 40)
    with pytest.raises(OsxpError) as exc:
        extract_xp12_rasters(scenery, TILE)
    assert exc.value.code == "DSF_SOURCE_DECOMPRESS_FAILED"
    body = b"XPLNEDSF" + struct.pack("<I", 1) + _atom("DEFN", _atom("TERT", b"terrain_Water\0"))
    path.write_bytes(body + hashlib.md5(body).digest())
    with pytest.raises(OsxpError) as exc:
        extract_xp12_rasters(scenery, TILE)
    assert exc.value.code == "DSF_SOURCE_CORRUPTED" and "DEMN" in exc.value.context["reason"]
    with pytest.raises(OsxpError) as exc:
        rasters_from_dsf(synthetic_dsf(elev, bathy, names=b"sea_level\0elevation\0"), tile=TILE)
    assert exc.value.code == "DSF_SOURCE_CORRUPTED"
