"""Tests of the P1 review fixes (spec sections named in each test).

Covers what the reviewers' own tests (``test_review_*.py``) do not: the atomic-write helper,
the structural image check, the raw-crop mask rule of the module API, provider codes with an
underscore, byte identity in ``validate``, job de-duplication, the single ``mask_zl`` source,
coded errors for leaked exceptions, corrupted bodies that pass the structural check, and
``doctor --online``.
"""

from __future__ import annotations

import io
import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from typer.testing import CliRunner

from orthostudio import fsutil
from orthostudio.cli import app
from orthostudio.errors import OsxpError
from orthostudio.imagery.chunks import ChunkStatus, ChunkStore
from orthostudio.imagery.grid import TextureId
from orthostudio.pipeline import textures as tex_mod
from orthostudio.pipeline.textures import (
    TextureJob,
    build_textures,
    merge_jobs,
)
from orthostudio.textures.assemble import decode_tile, image_body_complete
from orthostudio.textures.imprint import (
    mask_crop_raw,
    mask_for_texture,
    masks_dir_lookup,
    needs_mask_for_texture,
)
from orthostudio.textures.ter import TerKind, TerParams
from orthostudio.tilefiles import TerKind as ReaderTerKind
from orthostudio.tilefiles import parse_ter_name
from test_pipeline_textures import JOBS, T_LAND, ZL, TileServer, make_spec, tile_png

runner = CliRunner()


@pytest.fixture
def server() -> Iterator[TileServer]:
    srv = TileServer()
    yield srv
    srv.close()


@pytest.fixture
def mask_dir(tmp_path: Path) -> Path:
    d = tmp_path / "masks"
    d.mkdir()
    mask = np.full((4096, 4096), 255, dtype=np.uint8)
    mask[:, :2048] = 0
    Image.fromarray(mask, mode="L").save(d / "16_16.png")
    return d


# --- fsutil ---------------------------------------------------------------------------------------


def test_atomic_writes_leave_no_temporary_and_replace_the_target(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "a.bin"
    fsutil.atomic_write_bytes(p, b"one", fsync=True)
    fsutil.atomic_write_bytes(p, b"two")
    assert p.read_bytes() == b"two"
    t = tmp_path / "t.txt"
    fsutil.atomic_write_text(t, "x\ny\n", encoding="ascii")
    assert t.read_bytes() == b"x\ny\n"
    assert sorted(q.name for q in tmp_path.rglob("*.tmp-*")) == []
    fsutil.fsync_dir(tmp_path / "does-not-exist")  # silent


def test_atomic_write_failure_removes_the_temporary(tmp_path: Path) -> None:
    class BoomError(Exception):
        pass

    def produce(_tmp: Path) -> None:
        raise BoomError

    with pytest.raises(BoomError):
        fsutil._atomic(tmp_path / "x", produce, fsync=False)
    assert list(tmp_path.iterdir()) == []


def test_atomic_link_or_copy(tmp_path: Path) -> None:
    src = tmp_path / "src.dds"
    src.write_bytes(b"dds")
    linked = tmp_path / "out" / "linked.dds"
    copied = tmp_path / "out" / "copied.dds"
    fsutil.atomic_link_or_copy(src, linked)
    fsutil.atomic_link_or_copy(src, copied, link=False)
    assert linked.read_bytes() == copied.read_bytes() == b"dds"
    assert linked.stat().st_nlink == 2 and copied.stat().st_nlink == 1


def test_concurrent_atomic_writers_leave_a_complete_file(tmp_path: Path) -> None:
    p = tmp_path / "shared.bin"
    payloads = [bytes([i]) * 200_000 for i in range(8)]

    def write(data: bytes) -> None:
        for _ in range(5):
            fsutil.atomic_write_bytes(p, data)

    threads = [threading.Thread(target=write, args=(d,)) for d in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = p.read_bytes()
    assert got in payloads and not list(tmp_path.glob("*.tmp-*"))


# --- structural image check (pipeline spec section 4) ---------------------------------------------


def _encode(fmt: str, **kw: object) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (10, 20, 30)).save(buf, format=fmt, **kw)
    return buf.getvalue()


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "GIF", "BMP"])
def test_image_body_complete_accepts_whole_images_and_rejects_truncation(fmt: str) -> None:
    body = _encode(fmt)
    assert image_body_complete(body), fmt
    assert not image_body_complete(body[: len(body) // 2]), fmt
    assert not image_body_complete(body[:-1]), fmt
    assert np.asarray(decode_tile(body)).shape == (256, 256, 3)


def test_image_body_complete_rejects_signatures_alone_and_garbage() -> None:
    assert not image_body_complete(b"\xff\xd8\xff")
    assert not image_body_complete(b"\x89PNG\r\n\x1a\n")
    assert not image_body_complete(b"<html>not a tile</html>")
    assert not image_body_complete(b"")
    # a JPEG whose server appended a few bytes after EOI is still whole
    assert image_body_complete(_encode("JPEG") + b"\r\n")


# --- raw-crop mask rule in the module API (imprint spec section 4) --------------------------------


def test_mask_for_texture_applies_the_raw_threshold(tmp_path: Path) -> None:
    masks = tmp_path / "m"
    masks.mkdir()
    full = np.zeros((4096, 4096), dtype=np.uint8)
    full[:, :100] = 30  # top-left quarter (ZL 15 texture rx=ry=0) is at most 30 everywhere
    full[2048:, 2048:] = 200  # bottom-right quarter carries sea
    Image.fromarray(full, mode="L").save(masks / "6016_8448.png")
    lookup = masks_dir_lookup(masks)
    low = TextureId(16896, 12032, 15, "BI")  # rx=0, ry=0 in cell (8448, 6016)
    high = TextureId(16912, 12048, 15, "BI")  # rx=1, ry=1
    assert not needs_mask_for_texture(low, 14, lookup)
    assert mask_for_texture(low, 14, lookup) is None
    assert needs_mask_for_texture(high, 14, lookup)
    m = mask_for_texture(high, 14, lookup)
    assert m is not None and m.shape == (4096, 4096) and int(m.max()) == 200
    raw = mask_crop_raw(full, 2048, 2048, 2048)
    assert raw.shape == (2048, 2048) and raw.flags["C_CONTIGUOUS"]
    # below mask_zl: never masked, no file read
    assert not needs_mask_for_texture(TextureId(4224, 3008, 13, "BI"), 14, lookup)


# --- provider codes with an underscore (tile-files spec) ------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("6016_8448_My_Prov14.ter", ReaderTerKind.LAND),
        ("6016_8448_My_Prov14_water.ter", ReaderTerKind.WATER),
        ("6016_8448_My_Prov14_sea_overlay.ter", ReaderTerKind.SEA_OVERLAY),
        ("6016_8448_g2xpl_1614.ter", ReaderTerKind.LAND),
    ],
)
def test_parse_ter_name_accepts_underscores_in_the_provider_code(
    name: str, kind: ReaderTerKind
) -> None:
    t, k = parse_ter_name(name)
    assert k is kind and (t.til_x, t.til_y, t.zl) == (8448, 6016, 14)
    assert t.provider == name.split("_", 2)[2].split("14")[0]


def test_parse_ter_name_still_rejects_unknown_suffixes() -> None:
    with pytest.raises(OsxpError):
        parse_ter_name("6016_8448_My_Prov14_lava.ter")
    with pytest.raises(OsxpError):
        parse_ter_name("6016_8448_My_Prov.ter")


# --- validate: byte identity reported separately (pipeline spec section 11) -----------------------


# --- jobs, mask_zl, leaked exceptions, corrupted bodies (pipeline spec sections 2-4, 8) -----------


def test_merge_jobs_unions_kinds_per_texture() -> None:
    a = TextureJob(T_LAND, (TerKind.LAND,))
    b = TextureJob(T_LAND, (TerKind.WATER_OVERLAY, TerKind.LAND))
    merged = merge_jobs([a, b, JOBS[0]])
    assert merged == [
        TextureJob(T_LAND, (TerKind.LAND, TerKind.WATER_OVERLAY)),
        JOBS[0],
    ]


def test_spec_mask_zl_is_the_single_source_for_the_ter(server: TileServer, tmp_path: Path) -> None:
    """``ter_params.mask_zl`` (14, the default) disagrees with ``spec.mask_zl`` (ZL - 1): the
    ``.ter`` declares the border at ``4096 // 2**(zl - spec.mask_zl)`` = 2048 and the PNG is the
    raw 2048² crop, as the mask was actually read at ``spec.mask_zl``."""
    masks = tmp_path / "masks5"
    masks.mkdir()
    full = np.zeros((4096, 4096), dtype=np.uint8)
    full[2048:, 2048:] = 255  # window of T_SEA in the ZL5 cell 0_0
    Image.fromarray(full, mode="L").save(masks / "0_0.png")
    params = TerParams(imprint_masks_to_dds=False, mask_zl=14)
    spec = make_spec(server, tmp_path, masks, jobs=[JOBS[0]], mask_zl=ZL - 1, ter_params=params)
    report = build_textures(spec)
    assert report.ok, report.errors
    ter = (tmp_path / "out" / "terrain" / "16_16_T6_sea_overlay.ter").read_text()
    assert ter.split("LOAD_CENTER_BORDER")[1].split()[3] == "2048"
    with Image.open(tmp_path / "out" / "textures" / "16_16_ZL6.png") as im:
        assert im.size == (2048, 2048) and np.asarray(im).min() == 255
    assert report.outcomes[0].fmt == "bc1"


def test_leaked_exception_in_a_task_fails_the_texture_with_a_code(
    server: TileServer, tmp_path: Path, mask_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> list[Path]:
        raise RuntimeError("simulated")

    monkeypatch.setattr(tex_mod, "write_ter_files", boom)
    report = build_textures(make_spec(server, tmp_path, mask_dir, jobs=[JOBS[1]]))
    assert not report.ok
    o = report.outcomes[0]
    assert o.status == "failed" and o.error is not None
    assert o.error["code"] == "SYS_INTERNAL_ERROR"
    assert "simulated" in o.error["context"]["detail"]
    assert {e["code"] for e in report.errors} >= {"SYS_INTERNAL_ERROR", "TEX_MISSING"}


def _png_with_valid_trailer_but_broken_data(z: int, x: int, y: int) -> bytes:
    body = bytearray(tile_png(z, x, y))
    idat = body.find(b"IDAT")
    assert idat > 0
    body[idat + 4 : idat + 12] = b"\x00" * 8  # inside the IDAT payload: CRC no longer matches
    return bytes(body)


def test_body_passing_the_structural_check_but_undecodable_is_flagged_on_disk(
    server: TileServer, tmp_path: Path, mask_dir: Path
) -> None:
    """Second layer of spec section 4: the worker reports ``corrupted`` chunks, the pipeline
    flips them to ``ERROR`` in the container (next run fetches them again) and reports
    ``IMG_TILE_CORRUPTED``; the texture is published, degraded."""
    st = server.state
    key = (ZL, 33, 17)
    broken = _png_with_valid_trailer_but_broken_data(*key)
    assert image_body_complete(broken)
    with pytest.raises(OsxpError):
        decode_tile(broken)
    st.cache[key] = broken
    report = build_textures(make_spec(server, tmp_path, mask_dir, jobs=[JOBS[1]]))
    o = report.outcomes[0]
    assert o.status == "built" and o.corrupted == 1 and o.from_fallback + o.unfilled == 1
    assert report.counts["chunks_corrupted"] == 1
    assert any(e["code"] == "IMG_TILE_CORRUPTED" for e in report.errors)
    c = ChunkStore(tmp_path / "chunks").read(T_LAND)
    assert c is not None
    i = 16 * (17 - 16) + (33 - 32)
    assert c.get(i).status is ChunkStatus.ERROR and c.get(i).content_type == "IMG_TILE_CORRUPTED"
    # the provider recovers: only that tile is fetched, the texture is re-encoded
    del st.cache[key]
    server.reset_hits()
    report2 = build_textures(make_spec(server, tmp_path, mask_dir, jobs=[JOBS[1]]))
    assert report2.ok and st.total_hits() == 1 and st.hits[key] == 1
    assert report2.outcomes[0].status == "built" and report2.outcomes[0].corrupted == 0
    assert report2.outcomes[0].key != o.key


def test_report_carries_sys_cancelled_when_cancelled_before_start(
    server: TileServer, tmp_path: Path, mask_dir: Path
) -> None:
    cancel = threading.Event()
    cancel.set()
    report = build_textures(make_spec(server, tmp_path, mask_dir, cancel=cancel))
    assert report.cancelled and any(e["code"] == "SYS_CANCELLED" for e in report.errors)
    assert server.state.total_hits() == 0


# --- doctor --------------------------------------------------------------------------------------


def test_doctor_is_offline_by_default(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["doctor", "--json", "--store", str(tmp_path / "s"), "--chunks", str(tmp_path / "c")]
    )
    assert result.exit_code == 0, result.output
    by_name = {c["name"]: c for c in json.loads(result.output)["checks"]}
    assert by_name["bing"]["status"] == "skip" and "--online" in by_name["bing"]["summary"]


@pytest.mark.network
def test_doctor_online_probes_one_bing_tile(tmp_path: Path) -> None:
    if not os.environ.get("OSXP_NETWORK_TESTS"):
        pytest.skip("set OSXP_NETWORK_TESTS=1 to probe Bing")
    result = runner.invoke(
        app,
        [
            "doctor",
            "--json",
            "--online",
            "--store",
            str(tmp_path / "s"),
            "--chunks",
            str(tmp_path / "c"),
        ],
    )
    by_name = {c["name"]: c for c in json.loads(result.output)["checks"]}
    assert by_name["bing"]["status"] in ("ok", "warn"), by_name["bing"]
