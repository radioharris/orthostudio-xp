# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Live's record of the textures it built (``pack/_live/ortho.json``): one that does not read is
read as none, the window answering and the streamer starting, and it is written whole, its bytes
on the disk before it takes the record's name (discussion 32, 2026-11-17: a record found empty
answered every request of the window with an error)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

import test_api_fakes as fakes
from live_helpers import make_source
from orthostudio import fsutil
from orthostudio.live import session
from orthostudio.live.cache import ChunkCache
from orthostudio.live.control import LiveControl
from orthostudio.live.names import built_files
from orthostudio.live.pack import MARKER, load_built, save_built
from orthostudio.live.region import setup_region
from orthostudio.live.streamer import Streamer
from orthostudio.live.web import create_live_app
from test_api_fakes import client_for

anyio_backend = fakes.anyio_backend

DAMAGED = [
    (b"", "empty"),
    (b"  \n", "empty"),
    (b'{"textures": {"23296_33152_BI16.dds": 0', "Expecting"),  # cut short
    (b"[1, 2]", "[1, 2]"),  # not a table
    (b"\xff\xfe{}", "utf-8"),  # not text
]


@pytest.mark.parametrize(("content", "why"), DAMAGED)
def test_a_record_that_does_not_read_is_read_as_none(
    tmp_path: Path, content: bytes, why: str
) -> None:
    (tmp_path / MARKER).parent.mkdir()
    (tmp_path / MARKER).write_bytes(content)
    heard: list[str] = []
    assert load_built(tmp_path, damaged=heard.append) == {}
    assert len(heard) == 1 and why in heard[0], heard
    assert load_built(tmp_path) == {}  # nobody to tell: none all the same


def test_no_record_is_none_said_to_nobody(tmp_path: Path) -> None:
    heard: list[str] = []
    assert load_built(tmp_path, damaged=heard.append) == {}
    assert heard == []


def test_the_record_is_on_the_disk_before_it_takes_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / MARKER).parent.mkdir()
    (tmp_path / MARKER).write_text("")  # the empty one of discussion 32
    order: list[str] = []
    fsync = os.fsync

    def synced(fd: int) -> None:
        order.append("fsync")
        fsync(fd)

    real_replace = fsutil.replace

    def renamed(src: Path, dst: Path) -> None:
        order.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", synced)
    monkeypatch.setattr(fsutil, "replace", renamed)
    meta = {"textures": {"23296_33152_BI16.dds": 0}, "seen": {}}
    size = save_built(tmp_path, meta)
    assert order[:2] == ["fsync", "replace"]  # then its folder's, on a Mac or Linux
    assert size == len((tmp_path / MARKER).read_text(encoding="utf-8"))
    assert load_built(tmp_path) == meta
    assert sorted(p.name for p in (tmp_path / MARKER).parent.iterdir()) == ["ortho.json"]


def test_the_streamer_starts_on_a_record_found_empty_and_frees_what_it_named(
    tmp_path: Path,
) -> None:
    """The textures the lost record named stay on the disk: counted stale, as after a season's
    change, they are made neutral again (or built again where X-Plane needs them); otherwise
    nothing counted them nor took them away (the review of 2026-11-17)."""
    names = ("23296_33152_BI16", "23296_33168_BI16")
    pack = tmp_path / "pack"
    setup_region(make_source(tmp_path, tile=(45, 6), textures=names), pack, log=lambda _: None)
    built = pack / "textures" / f"{names[0]}.dds"
    neutral = next((pack / "_live").glob("neutral*.dds"))
    built.unlink(missing_ok=True)
    shutil.copyfile(neutral, built)  # a file of its own: something built in it
    (pack / MARKER).write_text("")
    lines: list[str] = []
    cache = ChunkCache(tmp_path / "c.sqlite")
    try:
        s = Streamer(pack, cache, lambda on_piece: None, log=lines.append)
        assert s.built == {} and s.stale == {built.name: 0}
        said = "Live's record of its textures did not read (empty): its 1 textures built again"
        assert any(line.startswith(said) for line in lines), lines
        assert s.drop_stale(budget_s=10) == 1
        assert built_files(pack) == [] and s.stale == {}
        s.built = {f"{names[1]}.dds": 2}
        s.save()
    finally:
        cache.close()
    saved = json.loads((pack / MARKER).read_text(encoding="utf-8"))
    assert saved["textures"] == {f"{names[1]}.dds": 2} and saved["stale"] == {}


@pytest.mark.anyio
async def test_the_window_answers_with_a_record_found_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discussion 32: « Live does not answer: HTTP 500 » and a blank Settings page, every request
    of the window failing on the record's first character."""
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    record = tmp_path / "home" / "live" / "pack" / MARKER
    record.parent.mkdir(parents=True)
    record.write_text("")

    def no_xplane(explicit: Path | None) -> Path:
        raise session.RefusedError("no X-Plane here")

    monkeypatch.setattr(session, "xplane_dir", no_xplane)
    app = create_live_app(LiveControl(echo=lambda _: None))
    async with client_for(app) as client:
        r = await client.get("/api/live/status")
        assert r.status_code == 200, r.text
        assert r.json()["checks"]["textures_bytes"] == 0
