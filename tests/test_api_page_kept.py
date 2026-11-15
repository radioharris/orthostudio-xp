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
from orthostudio.api.page_kept import KEPT_MARK, KEY, MAX_KEYS, MAX_VALUE_CHARS
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


@pytest.mark.anyio
async def test_the_page_comes_with_what_was_kept_and_a_change_is_kept(app: Any, home: Path) -> None:
    async with client_for(app) as c:
        assert _given((await c.get("/")).text) == {"kept": {}}  # nothing kept yet
        values = {"osxp.mapAirports": "1", "osxp.experts": "open"}
        r = await c.put("/api/page-kept", json={"values": values})
        assert r.status_code == 200, r.text
        r = await c.put("/api/page-kept", json={"values": {"osxp.experts": None}})  # forgotten
        assert r.status_code == 200, r.text
        assert _given((await c.get("/")).text) == {"kept": {"osxp.mapAirports": "1"}}
    assert json.loads((home / "page.json").read_text(encoding="utf-8")) == {"osxp.mapAirports": "1"}


@pytest.mark.anyio
async def test_a_kept_value_cannot_end_the_script_it_is_written_in(app: Any) -> None:
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
        {"mapAirports": "1"},  # not one of the pages' names
        {"osxp.": "1"},
        {"osxp.map airports": "1"},
        {"osxp.mapAirports": 1},  # a page keeps text
        {"osxp.flightplan": "x" * (MAX_VALUE_CHARS + 1)},
    ],
)
async def test_what_a_page_never_writes_is_refused(
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


@pytest.mark.anyio
@pytest.mark.parametrize("text", ["", "{not json", '["osxp.a"]'])
async def test_a_file_that_cannot_be_read_keeps_nothing_until_the_next_change(
    app: Any, home: Path, text: str
) -> None:
    """Nothing comes back from elsewhere: the page starts from its defaults, and the next change
    writes the file whole again."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "page.json").write_text(text, encoding="utf-8")
    async with client_for(app) as c:
        assert _given((await c.get("/")).text) == {"kept": {}}
        r = await c.put("/api/page-kept", json={"values": {"osxp.mapStreet": "1"}})
        assert r.status_code == 200, r.text
    assert json.loads((home / "page.json").read_text(encoding="utf-8")) == {"osxp.mapStreet": "1"}


@pytest.mark.anyio
async def test_only_what_a_page_could_have_written_is_read(app: Any, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "page.json").write_text('{"osxp.a": "1", "other": "2", "osxp.b": 3}', encoding="utf-8")
    async with client_for(app) as c:
        assert _given((await c.get("/")).text) == {"kept": {"osxp.a": "1"}}


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
    """Live's page is served by the app's own program: it keeps its choices the same way, in
    ``live-page.json``, never in the file of OrthoStudio XP's engine, and is refused in the same
    words."""
    from orthostudio.live.web import UI_DIR, create_live_app

    assert (UI_DIR / "index.html").read_text(encoding="utf-8").count(KEPT_MARK) == 1
    async with client_for(create_live_app(None)) as c:
        assert _given((await c.get("/")).text) == {"kept": {}}
        r = await c.put("/api/page-kept", json={"values": {"osxp.live.mapDetails": "0"}})
        assert r.status_code == 200, r.text
        assert _given((await c.get("/")).text) == {"kept": {"osxp.live.mapDetails": "0"}}
        r = await c.put("/api/page-kept", json={"values": {"live.mapDetails": "0"}})
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_VALUE_INVALID"
        many = {f"osxp.k{i}": "1" for i in range(MAX_KEYS)}
        r = await c.put("/api/page-kept", json={"values": many})
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_VALUE_INVALID"
    assert json.loads((home / "live-page.json").read_text(encoding="utf-8")) == {
        "osxp.live.mapDetails": "0"
    }
    assert not (home / "page.json").exists()
