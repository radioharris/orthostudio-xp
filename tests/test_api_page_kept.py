# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What a page keeps from one visit to the next (docs/specs/ui.md 1, *What the page keeps*): kept by
the program that serves it, written into the page as it is served, changed by
``PUT /api/page-kept``."""

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
from orthostudio.api.page_kept import KEPT_MARK, KEY, LIVE_PAGE_FILE, MAX_VALUE_CHARS
from orthostudio.ui import ui_dir, with_json
from test_api_fakes import client_for

home = fakes.home
anyio_backend = fakes.anyio_backend

WRITTEN = re.compile(r'<script type="application/json" id="page-kept">(.*?)</script>', re.S)


def _written(page: str) -> Any:
    found = WRITTEN.findall(page)
    assert len(found) == 1
    return json.loads(found[0])


@pytest.fixture
def app(home: Path, tmp_path: Path) -> Iterator[Any]:
    manager = JobManager(jobs_dir=tmp_path / "jobs", build=fakes.FakeBuild(), env_factory=None)
    yield create_app(
        env_factory=None, jobs=manager, settings_path=home / "config.toml", ui_dir=ui_dir()
    )
    manager.close()


def test_a_value_written_into_a_page_cannot_end_its_script() -> None:
    value = {"osxp.flightplan": '{"route": "</script><script>alert(1)</script><!-- -->"}'}
    page = with_json(f"<p>{KEPT_MARK}</p>", KEPT_MARK, value)
    assert "<script>alert(1)" not in page and "<!--" not in page
    assert _written(page) == value


@pytest.mark.anyio
async def test_the_page_comes_with_what_was_kept_and_a_change_is_kept(app: Any, home: Path) -> None:
    async with client_for(app) as c:
        assert _written((await c.get("/")).text) == {}  # nothing kept yet
        values = {"osxp.mapAirports": "1", "osxp.experts": "open"}
        r = await c.put("/api/page-kept", json={"values": values})
        assert r.status_code == 200, r.text
        r = await c.put("/api/page-kept", json={"values": {"osxp.experts": None}})  # forgotten
        assert r.status_code == 200, r.text
        assert _written((await c.get("/")).text) == {"osxp.mapAirports": "1"}
    assert json.loads((home / "page.json").read_text(encoding="utf-8")) == {"osxp.mapAirports": "1"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {"values": {"mapAirports": "1"}},  # not one of the pages' names
        {"values": {"osxp.": "1"}},
        {"values": {"osxp.map airports": "1"}},
        {"values": {"osxp.mapAirports": 1}},  # a page keeps text
        {"values": {"osxp.flightplan": "x" * (MAX_VALUE_CHARS + 1)}},
        {"values": ["osxp.mapAirports"]},
        {"values": {}, "more": 1},
    ],
)
async def test_what_a_page_never_writes_is_refused(app: Any, home: Path, body: Any) -> None:
    async with client_for(app) as c:
        r = await c.put("/api/page-kept", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_VALUE_INVALID"
    assert not (home / "page.json").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("text", ["", "{not json", '["osxp.a"]', '{"osxp.a": 3, "other": "2"}'])
async def test_a_file_that_is_not_a_page_s_keeps_nothing(app: Any, home: Path, text: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "page.json").write_text(text, encoding="utf-8")
    async with client_for(app) as c:
        assert _written((await c.get("/")).text) == {}
        r = await c.put("/api/page-kept", json={"values": {"osxp.mapStreet": "1"}})
        assert r.status_code == 200, r.text
    assert json.loads((home / "page.json").read_text(encoding="utf-8")) == {"osxp.mapStreet": "1"}


@pytest.mark.anyio
async def test_a_file_that_cannot_be_read_is_never_written_over(app: Any, home: Path) -> None:
    """Held by another program on Windows, say: a change written then would have kept that one
    choice alone (a review, 2026-11-15). The visit starts from the defaults, and nothing is lost."""
    (home / "page.json").mkdir(parents=True)  # a file that cannot be read, on every system
    async with client_for(app) as c:
        assert _written((await c.get("/")).text) == {}
        r = await c.put("/api/page-kept", json={"values": {"osxp.mapStreet": "1"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "SYS_WRITE_FAILED"
    assert (home / "page.json").is_dir()


def test_every_name_the_pages_keep_is_one_the_server_takes() -> None:
    """A name outside the rule would be refused, and the page does not say so: it would be kept
    for the visit alone."""
    from orthostudio.live.web import UI_DIR

    files = [ui_dir() / n for n in ("app.js", "map.js", "flightplan.js")] + [UI_DIR / "live.js"]
    names = {n for f in files for n in re.findall(r'"(osxp\.[^"]*)"', f.read_text("utf-8"))}
    assert "osxp.mapAirports" in names and "osxp.live.mapDetails" in names
    assert all(re.fullmatch(KEY, n) for n in names), names


@pytest.mark.anyio
async def test_live_s_page_keeps_its_choices_in_a_file_of_its_own(home: Path) -> None:
    """Live's page is served by the app's own program, which has none of the engine's error
    handlers: it keeps its choices in a file of its own and is refused in the same words."""
    from orthostudio.live.web import UI_DIR, create_live_app

    assert (UI_DIR / "index.html").read_text(encoding="utf-8").count(KEPT_MARK) == 1
    async with client_for(create_live_app(None)) as c:
        r = await c.put("/api/page-kept", json={"values": {"osxp.live.mapDetails": "0"}})
        assert r.status_code == 200, r.text
        assert _written((await c.get("/")).text) == {"osxp.live.mapDetails": "0"}
        r = await c.put(
            "/api/page-kept", content=b"{not json", headers={"Content-Type": "application/json"}
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_VALUE_INVALID"
    assert json.loads((home / LIVE_PAGE_FILE).read_text(encoding="utf-8")) == {
        "osxp.live.mapDetails": "0"
    }
    assert not (home / "page.json").exists()
