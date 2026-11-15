# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the page keeps from one visit to the next (docs/specs/ui.md 1, *What the page keeps*):
kept by the engine in ``$OSXP_HOME/page.json``, written into the page as it is served, changed by
``PUT /api/page-kept``.
In the app's frame the page is an iframe, whose storage the web view of macOS forgot at every
launch (2026-11-15)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import test_api_fakes as fakes
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.api.page_kept import KEPT_MARK, MAX_KEYS, MAX_VALUE_CHARS, load_kept
from orthostudio.ui import ui_dir
from test_api_fakes import client_for

home = fakes.home
anyio_backend = fakes.anyio_backend

GIVEN = re.compile(r'<script type="application/json" id="page-kept">(.*?)</script>', re.S)


def _given(page: str) -> Any:
    found = GIVEN.findall(page)
    assert len(found) == 1
    return json.loads(found[0])


@pytest.fixture
def app(home: Path, tmp_path: Path) -> Iterator[Any]:
    manager = JobManager(jobs_dir=tmp_path / "jobs", build=fakes.FakeBuild(), env_factory=None)
    yield create_app(
        env_factory=None, jobs=manager, settings_path=home / "config.toml", ui_dir=ui_dir()
    )
    manager.close()


def test_the_page_has_one_place_for_what_is_kept() -> None:
    """Served as plain files, the mark stays ``null``: no engine keeps anything (kept.js)."""
    page = (ui_dir() / "index.html").read_text(encoding="utf-8")
    assert page.count(KEPT_MARK) == 1
    assert page.index(KEPT_MARK) < page.index('<script type="module" src="static/app.js">')


@pytest.mark.anyio
async def test_the_page_comes_with_what_was_kept_and_a_change_is_kept(app: Any, home: Path) -> None:
    async with client_for(app) as c:
        assert _given((await c.get("/")).text) == {"kept": None}  # nothing kept yet
        values = {"osxp.mapAirports": "1", "osxp.experts": "open"}
        r = await c.put("/api/page-kept", json={"values": values})
        assert r.status_code == 200, r.text
        r = await c.put("/api/page-kept", json={"values": {"osxp.experts": None}})  # forgotten
        assert r.status_code == 200, r.text
        assert _given((await c.get("/")).text) == {"kept": {"osxp.mapAirports": "1"}}
    assert json.loads((home / "page.json").read_text(encoding="utf-8")) == {"osxp.mapAirports": "1"}


@pytest.mark.anyio
async def test_a_kept_value_cannot_close_the_script_it_is_written_in(app: Any) -> None:
    plan = '{"route": "</script><script>alert(1)</script><!-- -->"}'
    async with client_for(app) as c:
        r = await c.put("/api/page-kept", json={"values": {"osxp.flightplan": plan}})
        assert r.status_code == 200, r.text
        page = (await c.get("/")).text
    assert _given(page) == {"kept": {"osxp.flightplan": plan}}
    assert "<script>alert(1)" not in page


@pytest.mark.anyio
@pytest.mark.parametrize(
    "values",
    [
        {"mapAirports": "1"},  # not one of the page's names
        {"osxp.": "1"},
        {"osxp.map airports": "1"},
        {"osxp.mapAirports": 1},  # the page keeps text
        {"osxp.flightplan": "x" * (MAX_VALUE_CHARS + 1)},
    ],
)
async def test_what_the_page_never_writes_is_refused(
    app: Any, home: Path, values: dict[str, Any]
) -> None:
    async with client_for(app) as c:
        r = await c.put("/api/page-kept", json={"values": values})
    assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_VALUE_INVALID"
    assert not (home / "page.json").exists()


@pytest.mark.anyio
async def test_the_values_kept_have_a_ceiling(app: Any, home: Path) -> None:
    many = {f"osxp.k{i}": "1" for i in range(MAX_KEYS)}
    async with client_for(app) as c:
        assert (await c.put("/api/page-kept", json={"values": many})).status_code == 200
        r = await c.put("/api/page-kept", json={"values": {"osxp.more": "1"}})
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_VALUE_INVALID"
        # forgetting one makes room again
        r = await c.put("/api/page-kept", json={"values": {"osxp.k0": None, "osxp.more": "1"}})
        assert r.status_code == 200, r.text
    kept = json.loads((home / "page.json").read_text(encoding="utf-8"))
    assert len(kept) == MAX_KEYS and "osxp.more" in kept and "osxp.k0" not in kept


def test_a_file_that_cannot_be_read_is_a_first_visit(tmp_path: Path) -> None:
    path = tmp_path / "page.json"
    assert load_kept(path) is None
    path.write_text("{not json", encoding="utf-8")
    assert load_kept(path) is None
    path.write_text('["osxp.a"]', encoding="utf-8")
    assert load_kept(path) is None
    # only what a page could have written is read
    path.write_text('{"osxp.a": "1", "other": "2", "osxp.b": 3}', encoding="utf-8")
    assert load_kept(path) == {"osxp.a": "1"}


@pytest.mark.anyio
async def test_live_s_page_keeps_its_choices_in_a_file_of_its_own(home: Path) -> None:
    """Live's page is an iframe of the frame too, served by the app's own program: it keeps its
    choices the same way, in ``live-page.json``, never in OrthoStudio XP's engine's file."""
    from orthostudio.live.web import UI_DIR, create_live_app

    assert (UI_DIR / "index.html").read_text(encoding="utf-8").count(KEPT_MARK) == 1
    async with client_for(create_live_app(None)) as c:
        assert _given((await c.get("/")).text) == {"kept": None}
        r = await c.put("/api/page-kept", json={"values": {"osxp.live.mapDetails": "0"}})
        assert r.status_code == 200, r.text
        assert _given((await c.get("/")).text) == {"kept": {"osxp.live.mapDetails": "0"}}
    assert json.loads((home / "live-page.json").read_text(encoding="utf-8")) == {
        "osxp.live.mapDetails": "0"
    }
    assert not (home / "page.json").exists()
