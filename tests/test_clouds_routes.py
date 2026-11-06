# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The cloud mode's routes through the app (``clouds.md`` 1.5, 6): what a tile allows is the
engine's to say, in one table its routes refuse by; what runs the detector is asked first; a bad
tile name, cloud or outline is refused, never a bare error."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import test_api_fakes as fakes
from orthostudio.api import clouds_api
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.clouds import cloudlist, mode
from orthostudio.clouds.picture import Picture
from orthostudio.home import default_tiles_root
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import (
    CLOUDS_DIR,
    ArtefactEntry,
    PackManifest,
    write_build_settings,
    write_manifest,
)
from test_api_fakes import FakeBuild, FakeIndex, client_for

home = fakes.home
TILE = TileRef(43, 5)
NAME = "1488_2096_BI12"


@pytest.fixture
def app(home: Path):  # type: ignore[no-untyped-def]
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=lambda specs: None)
    application = create_app(
        env_factory=None, jobs=mgr, airports=FakeIndex(), settings_path=home / "config.toml"
    )
    yield application
    mgr.close()


@pytest.fixture
def ready(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tile built by this version, as its folder says it: its textures described, the settings
    of its build kept; its picture a small one."""
    folder = default_tiles_root() / "zOrthoStudio_+43+005"
    folder.mkdir(parents=True)
    dsf = folder / TILE.dsf_relpath
    dsf.parent.mkdir(parents=True)
    dsf.write_bytes(b"XPLNEDSF" + b"\0" * 100)
    files = {"dsf": TILE.dsf_relpath.as_posix(), "dsf_size": 108, "textures": 0, "terrain": 0}
    write_manifest(
        folder,
        TILE,
        PackManifest(TILE.name, "BI", 12, {"textures": ArtefactEntry("k1", "d", "r@2")}, files),
    )
    (folder / CLOUDS_DIR).mkdir()
    doc = {"textures": [{"name": NAME, "chunks": "c"}], "colours": {"tile": [0, 0, 0], "zones": []},
           "clouds": {"applied": [], "undone": {}}}  # fmt: skip
    (folder / CLOUDS_DIR / "k1.json").write_text(json.dumps(doc))
    write_build_settings(folder, provider="BI", zl=12, config={}, overlay=False, xp12_rasters=False)
    blank = np.zeros((8, 8), np.uint8)
    picture = Picture(np.zeros((8, 8, 3), np.uint8), blank, blank, blank - 1, (), 12, 0, 0)
    monkeypatch.setattr(clouds_api, "_draw", lambda tile, held, zl: picture)
    monkeypatch.setattr(clouds_api, "detector_ready", lambda: False)
    return folder


def _ring() -> list[list[float]]:
    return [[43.1, 5.1, 43.1, 5.2, 43.2, 5.2, 43.1, 5.1]]


@pytest.mark.anyio
async def test_a_bad_name_cloud_or_outline_is_refused_in_words(app: Any, ready: Path) -> None:
    async with client_for(app) as c:
        r = await c.get("/api/clouds/what")
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_LATLON_INVALID"
        r = await c.post("/api/clouds/+43+005/picks", json={"tick": False})
        assert r.json()["error"]["code"] == "CLOUD_INVALID"
        two = {"polygon": [[5.1, 43.1], [5.2, 43.2]], "download": True}
        r = await c.post("/api/clouds/+43+005/outlines", json=two)
        assert r.json()["error"]["code"] == "CLOUD_INVALID"


@pytest.mark.anyio
async def test_what_a_ready_tile_allows_is_the_engines_to_say(app: Any, ready: Path) -> None:
    async with client_for(app) as c:
        a = (await c.get("/api/clouds/+43+005")).json()
    assert a["state"] == "ready" and a["busy"] is False
    # nothing to apply, restore or discard yet: those wait for a change
    assert a["allowed"] == ["check", "choose", "find", "outline", "remove", "tick", "untick"]
    assert a["line"] == {"key": "clouds.not_looked", "params": {}}  # no search made yet
    assert a["texts"]["count"] == [{"key": "clouds.pending_none", "params": {}}]
    assert a["views"] == {"after": "clouds.after", "before": "clouds.before"}
    assert a["picture"]["level"] == 12


@pytest.mark.anyio
async def test_a_tile_not_built_allows_nothing(app: Any, home: Path) -> None:
    async with client_for(app) as c:
        a = (await c.get("/api/clouds/+43+005")).json()
        r = await c.post("/api/clouds/+43+005/find", json={"download": True})
    assert a["state"] == "not_built" and a["allowed"] == [] and a["views"]["after"] is None
    assert r.json()["error"]["code"] == "CLOUD_TILE_NOT_READY"


@pytest.mark.anyio
async def test_what_runs_the_detector_is_asked_first_and_an_untick_is_not(
    app: Any, ready: Path
) -> None:
    with cloudlist.update(TILE) as cl:
        cid = cl.add("drawn", _ring(), 1.0, tick=True, source="EOX")["id"]
    async with client_for(app) as c:
        r = await c.post("/api/clouds/+43+005/find")
        err = r.json()["error"]
        assert err["code"] == "CLOUD_DETECTOR_ASK" and err["context"]["mb"] >= 1
        r = await c.post("/api/clouds/+43+005/picks", json={"id": cid, "tick": True})
        assert r.json()["error"]["code"] == "CLOUD_DETECTOR_ASK"
        r = await c.post("/api/clouds/+43+005/picks", json={"id": cid, "tick": False})
        assert r.status_code == 200, r.text
    assert cloudlist.load(TILE).get(cid)["tick"] is False


def test_a_build_lets_the_list_be_changed_and_nothing_that_reads_the_folder() -> None:
    assert mode.possible("in_build") == mode.possible("queued") == mode.LIST_EDITS
    assert "apply" not in mode.possible("own_source_gone")
    assert mode.possible("earlier_version") == frozenset()
    with pytest.raises(Exception, match="cannot have its clouds looked at"):
        mode.require("+43+005", "in_build", "find")
    counts = {"changes": 0, "held": 1, "waiting": 0}
    assert mode.useful(mode.possible("ready"), counts, False) >= {"restore", "find"}
    assert "apply" not in mode.useful(mode.possible("ready"), counts, False)
    assert "find" not in mode.useful(mode.possible("ready"), counts, True)  # a search runs
    assert "apply" not in mode.possible("ready", tasks=True)  # a task reads the folder


@pytest.mark.anyio
async def test_a_ready_tiles_rows_offer_what_its_list_allows(app: Any, ready: Path) -> None:
    with cloudlist.update(TILE) as cl:
        found = cl.add("found", _ring(), 1.0, best="EOX", source="EOX")
        drawn = cl.add("drawn", _ring(), 1.0)
    async with client_for(app) as c:
        rows = {r["id"]: r for r in (await c.get("/api/clouds/+43+005")).json()["clouds"]}
    assert rows[found["id"]]["actions"] == ["tick", "choose"]
    assert rows[drawn["id"]]["actions"] == ["choose", "remove"]  # no source yet: no tick
