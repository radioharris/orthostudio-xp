# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The builder never keeps a texture X-Plane may be showing behind others: urgent textures are taken
even when the builder holds all it may of textures that cannot be encoded while X-Plane loads
scenery, and a texture taken far away is encoded as urgent once the planner says it is (the Norway
flights of 2026-09-29, where urgent textures waited behind the others)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from live_helpers import FakeDownloader, Image, make_source
from orthostudio.live import streamer as streamer_module
from orthostudio.live.cache import ChunkCache
from orthostudio.live.region import setup_region
from orthostudio.live.streamer import Streamer

pytestmark = pytest.mark.skipif(Image is None, reason="needs the project's imaging libraries")

ROW = tuple(f"23296_{33152 + 16 * k}_BI16" for k in range(8))
LEVEL = 1  # 2048 px: 64 pieces a texture


def flying_and_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Streamer:
    """A streamer in flight while X-Plane loads scenery: textures X-Plane does not read yet may not
    be encoded, and the builder holds at most two textures' pieces."""
    monkeypatch.setattr(streamer_module, "HELD_PIECES", 100)
    pack = tmp_path / "pack"
    setup_region(make_source(tmp_path, tile=(46, 2), textures=ROW), pack, log=lambda _: None)
    s = Streamer(
        pack,
        ChunkCache(tmp_path / "c.sqlite"),
        FakeDownloader,
        finest_quality=0,
        workers=2,
        flight_encodes=1,
        log=lambda _: None,
    )
    s.flying = True
    s.yield_until = time.monotonic() + 3600  # X-Plane loads scenery all along
    return s


def wait_for(test, timeout: float = 20.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.02)
    return False


def test_an_urgent_texture_passes_a_full_builder(tmp_path: Path, monkeypatch) -> None:
    s = flying_and_loading(tmp_path, monkeypatch)
    with s.lock:
        s.queue = {f"{n}.dds": (50.0 + k, LEVEL) for k, n in enumerate(ROW[:6])}
    s.start()
    try:
        assert wait_for(lambda: s.ready_now >= 1)  # their photos in, none may be encoded
        time.sleep(0.3)
        assert s.built == {} and s.ready_now <= 2  # the builder full: no more of them taken
        urgent = f"{ROW[7]}.dds"
        with s.lock:
            s.queue[urgent] = (-1.0, LEVEL)  # X-Plane is about to show it
        assert wait_for(lambda: urgent in s.built)
        assert list(s.built) == [urgent]  # the others still wait for X-Plane's load to end
    finally:
        s.close()
        s.cache.close()


def test_a_texture_become_urgent_is_encoded_as_urgent(tmp_path: Path, monkeypatch) -> None:
    s = flying_and_loading(tmp_path, monkeypatch)
    name = f"{ROW[0]}.dds"
    with s.lock:
        s.queue = {name: (40.0, LEVEL)}  # taken while far away
    s.start()
    try:
        assert wait_for(lambda: s.ready_now == 1)
        time.sleep(0.2)
        assert name not in s.built
        with s.lock:  # the planner's next pass: X-Plane reads it now
            s.in_flight[name] = (-2.0, LEVEL)
        assert wait_for(lambda: name in s.built)
    finally:
        s.close()
        s.cache.close()
