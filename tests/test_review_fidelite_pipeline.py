# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Adversarial fidelity review of the P1 texture pipeline against Ortho4XP behaviour.

Uses the local tile server of ``test_pipeline_textures.py``. The tests that were ``xfail`` at
review time (border PNG size, corrupted body, sea kind without mask) now pass: the markers
were removed by the P1 fixes and each test names the spec section that records the rule.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from orthostudio.imagery.chunks import ChunkStatus, ChunkStore
from orthostudio.imagery.grid import TextureId
from orthostudio.net import FetchResult
from orthostudio.pipeline.textures import CORRUPT_RETRIES, build_textures, chunk_entry_for
from orthostudio.textures.imprint import (
    load_mask,
    mask_cell,
    mask_crop,
    mask_crop_raw,
    mask_for_texture,
    needs_mask,
    needs_mask_for_texture,
)
from orthostudio.textures.ter import WATER_TRANSITION_PNG, TerKind, TerParams
from test_pipeline_textures import (
    JOBS,
    T_SEA,
    ZL,
    TileServer,
    chunk_means,
    make_spec,
    tile_colour,
    tile_png,
)


@pytest.fixture
def server() -> TileServer:
    srv = TileServer()
    yield srv  # type: ignore[misc]
    srv.close()


def _mask_dir_at_zl5(tmp_path: Path, window: np.ndarray) -> Path:
    """Mask cell ``0_0.png`` of the ZL5 grid; ``window`` (2048²) is the sub-square of T_SEA.

    T_SEA is (16, 16) at ZL6: factor 2, mask cell (0, 0), rx = ry = 1, so the texture reads
    the window (2048, 2048, 2048) of the mask (``O4_Mask_Utils.py:41-56``).
    """
    d = tmp_path / "masks5"
    d.mkdir()
    full = np.zeros((4096, 4096), dtype=np.uint8)
    full[2048:4096, 2048:4096] = window
    Image.fromarray(full, mode="L").save(d / "0_0.png")
    return d


# --- external mask PNG when masks are not imprinted -----------------------------------------------


def test_border_png_keeps_the_crop_size_like_ortho4xp(server: TileServer, tmp_path: Path) -> None:
    """Spec pipeline section 3: with ``imprint_masks_to_dds=False`` the border PNG is the raw
    crop (``4096 // factor`` px, ``O4_DSF_Utils.py:737-742`` saves ``needs_mask``'s
    ``small_img``), the resolution the ``.ter`` declares in ``LOAD_CENTER_BORDER``."""
    window = np.zeros((2048, 2048), dtype=np.uint8)
    window[:, :1024] = 200
    window[:, 1024:1100] = np.linspace(200, 0, 76, dtype=np.uint8)[None, :]
    masks = _mask_dir_at_zl5(tmp_path, window)
    params = TerParams(imprint_masks_to_dds=False, mask_zl=ZL - 1)
    spec = make_spec(server, tmp_path, masks, jobs=[JOBS[0]], mask_zl=ZL - 1, ter_params=params)
    report = build_textures(spec)
    assert report.ok, report.errors
    assert report.outcomes[0].fmt == "bc1"
    ter = (tmp_path / "out" / "terrain" / "16_16_T6_sea_overlay.ter").read_text()
    assert "LOAD_CENTER_BORDER" in ter and ter.split("LOAD_CENTER_BORDER")[1].split()[3] == "2048"
    assert "BORDER_TEX ../textures/16_16_ZL6.png" in ter
    with Image.open(tmp_path / "out" / "textures" / "16_16_ZL6.png") as im:
        assert im.mode == "L"
        # Ortho4XP: the crop itself, 4096 // factor = 2048 px, as the .ter says
        assert im.size == (2048, 2048)
        assert np.array_equal(np.asarray(im), window)


# --- DXT5 decision on the raw crop (pipeline) vs the resampled mask (module API) ------------------


def test_dxt5_decision_uses_the_raw_crop_as_ortho4xp_even_when_bicubic_overshoots(
    server: TileServer, tmp_path: Path
) -> None:
    """A crop whose maximum is exactly 30 next to zeros: bicubic upsampling overshoots above 30.
    One rule for the module API and the pipeline (spec imprint section 4, amended by the P1
    review): the threshold is evaluated on the **raw** crop, so ``mask_for_texture`` returns
    ``None`` and ``needs_mask_for_texture`` is False, as Ortho4XP. The pipeline, whose job carries a
    sea kind that the mask does not support any more, fails the texture (pipeline section 3)."""
    window = np.zeros((2048, 2048), dtype=np.uint8)
    window[:, :1024] = 30
    masks = _mask_dir_at_zl5(tmp_path, window)

    def lookup(x: int, y: int) -> Path | None:
        p = masks / f"{y}_{x}.png"
        return p if p.is_file() else None

    m_til_x, m_til_y, x0, y0, side = mask_cell(T_SEA, ZL - 1)
    raw = mask_crop_raw(load_mask(masks / f"{m_til_y}_{m_til_x}.png"), x0, y0, side)
    assert needs_mask(raw) is False and int(raw.max()) == 30
    assert int(mask_crop(raw, 0, 0, side).max()) > 30, "bicubic overshoot on the 30 -> 0 step"
    assert mask_for_texture(T_SEA, ZL - 1, lookup) is None
    assert needs_mask_for_texture(T_SEA, ZL - 1, lookup) is False
    spec = make_spec(server, tmp_path, masks, jobs=[JOBS[0]], mask_zl=ZL - 1)
    report = build_textures(spec)
    o = report.outcomes[0]
    assert o.status == "failed" and o.error is not None and o.error["code"] == "MASK_STALE"
    assert "mask maximum 30 <= 30" in o.error["message"]
    assert not (tmp_path / "out" / "textures" / "16_16_T6.dds").exists()


# --- sea kind without a usable mask ---------------------------------------------------------------


def test_sea_kind_without_mask_is_not_a_success(server: TileServer, tmp_path: Path) -> None:
    """Spec pipeline section 3: a ``_sea_overlay`` terrain over a DXT1 texture (no alpha) is an
    opaque overlay that hides X-Plane's water, a situation Ortho4XP never produces (``needs_mask``
    False -> ``terrain_Water``, no ``.ter``); the texture is failed, not published."""
    empty = tmp_path / "nomasks"
    empty.mkdir()
    report = build_textures(make_spec(server, tmp_path, empty, jobs=[JOBS[0]]))
    assert any(e["code"] == "MASK_STALE" for e in report.errors)
    sea_ter = tmp_path / "out" / "terrain" / "16_16_T6_sea_overlay.ter"
    dds = tmp_path / "out" / "textures" / "16_16_T6.dds"
    # a WET overlay .ter pointing at a DXT1 texture must not be published as a valid result
    assert not (sea_ter.is_file() and dds.is_file() and report.ok)
    assert not report.ok and not dds.exists() and report.outcomes[0].status == "failed"


# --- corrupted body accepted as OK ----------------------------------------------------------------


def test_chunk_entry_rejects_a_truncated_image_body(server: TileServer) -> None:
    """Spec pipeline section 4: a body is ``OK`` only when its structure is complete
    (``image_body_complete``: JPEG ``FFD9``, PNG ``IEND``...); Ortho4XP decoded the body and retried
    a corrupted one (``http_request_to_image``, ``max_baddata_retries``)."""
    p = server.provider()
    truncated = tile_png(ZL, 33, 17)[:300]
    r = FetchResult("k", 200, truncated, {"content-type": "image/png"}, 0.01, 1, False, None)
    entry = chunk_entry_for(p, r)
    assert entry.status is ChunkStatus.ERROR and entry.content_type == "IMG_TILE_CORRUPTED"
    three_bytes = FetchResult("k", 200, b"\xff\xd8\xff", {}, 0.01, 1, False, None)
    assert chunk_entry_for(p, three_bytes).status is ChunkStatus.ERROR
    whole = FetchResult("k", 200, tile_png(ZL, 33, 17), {}, 0.01, 1, False, None)
    assert chunk_entry_for(p, whole).status is ChunkStatus.OK


def test_corrupted_body_is_retried_not_cached_forever(server: TileServer, tmp_path: Path) -> None:
    """Spec pipeline section 4: a corrupted body is asked again ``CORRUPT_RETRIES`` times in the
    run (Ortho4XP ``max_baddata_retries``), then left ``ERROR`` so the next run fetches it."""
    st = server.state
    key = (ZL, 33, 17)
    st.cache[key] = tile_png(*key)[:300]  # PNG signature intact, body truncated
    masks = tmp_path / "nomasks"
    masks.mkdir()
    report = build_textures(make_spec(server, tmp_path, masks, jobs=[JOBS[1]]))
    container = ChunkStore(tmp_path / "chunks").read(TextureId(32, 16, ZL, "T"))
    assert container is not None
    i = 16 * (17 - 16) + (33 - 32)
    # Ortho4XP fidelity: the chunk is not a final answer; it must be retryable (ERROR) and the
    # texture must not be reported complete with a mean-filled chunk
    assert container.get(i).status is ChunkStatus.ERROR
    assert container.get(i).content_type == "IMG_TILE_CORRUPTED"
    assert report.outcomes[0].unfilled == 0 and report.outcomes[0].status == "incomplete"
    assert st.hits[key] == 1 + CORRUPT_RETRIES and report.counts["tiles_retried"] == 2
    assert report.counts["tiles_total"] == 256 + CORRUPT_RETRIES
    # a second run must ask the server again for that tile, and only for it
    del st.cache[key]
    server.reset_hits()
    report2 = build_textures(make_spec(server, tmp_path, masks, jobs=[JOBS[1]]))
    assert st.hits[key] == 1 and st.total_hits() == 1 and report2.ok


# --- parent chain of 404s across rounds -----------------------------------------------------------


def test_parent_chain_of_404_continues_to_the_fifth_level_and_stops_there(
    server: TileServer, tmp_path: Path
) -> None:
    """Chunk (33, 17) and its parents d = 1..4 are 404: the d = 5 parent (1, 0, ZL1) is used
    (``get_wmts_image`` walks down_sample 1..5); ZL0 is never asked."""
    st = server.state
    st.not_found.add((ZL, 33, 17))
    for d in range(1, 5):
        st.not_found.add((ZL - d, 33 >> d, 17 >> d))
    masks = tmp_path / "nomasks"
    masks.mkdir()
    report = build_textures(make_spec(server, tmp_path, masks, jobs=[JOBS[1]]))
    assert report.ok, report.errors
    o = report.outcomes[0]
    assert o.status == "built" and o.from_fallback == 1 and o.unfilled == 0
    assert report.counts["parents_total"] == 5
    assert st.hits[(1, 1, 0)] == 1 and st.hits[(0, 0, 0)] == 0
    means = chunk_means(tmp_path / "out" / "textures" / "16_32_T6.dds")
    assert np.abs(means[1, 1] - np.array(tile_colour(1, 1, 0))).max() < 5


# --- water_transition.png -------------------------------------------------------------------------


def test_water_transition_copied_only_for_inland_water_overlays(
    server: TileServer, tmp_path: Path
) -> None:
    """Ortho4XP copies it when writing a ``_water_overlay`` .ter (``O4_DSF_Utils.py:308-317``)."""
    masks = tmp_path / "nomasks"
    masks.mkdir()
    land_only = [type(JOBS[1])(JOBS[1].texture, (TerKind.LAND,))]
    build_textures(make_spec(server, tmp_path, masks, jobs=land_only))
    assert not (tmp_path / "out" / "textures" / WATER_TRANSITION_PNG).exists()
    build_textures(make_spec(server, tmp_path, masks, jobs=[JOBS[1]], out_dir=tmp_path / "out2"))
    assert (tmp_path / "out2" / "textures" / WATER_TRANSITION_PNG).is_file()
