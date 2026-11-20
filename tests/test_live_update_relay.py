# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The new version's banner on Live's page: the same paths as Build's, answered by Build's engine,
which alone installs; Live says the offer itself while the engine does not run, and starts it for
*Install and relaunch*."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from orthostudio import __version__, self_update, update
from orthostudio.live import update_relay
from test_api_fakes import client_for


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from orthostudio.home import OSXP_HOME_ENV

    monkeypatch.setenv(OSXP_HOME_ENV, str(tmp_path))
    # a newer version known, as GitHub said it today: nothing is asked of GitHub
    update._write(tmp_path / update.CACHE_NAME, {"checked_at": time.time(), "latest": "999.0.0"})
    return tmp_path


class Engine:
    """Build's engine as Live's server sees it: whether it answers, and what it was asked."""

    def __init__(self, up: bool) -> None:
        self.up = up
        self.asked: list[tuple[str, str, Any]] = []
        self.started = threading.Event()

    def answer(self, port: int, method: str, path: str, body: Any = None) -> Any:
        self.asked.append((method, path, body))
        if path == "/api/update/install" and method == "POST":
            return 202, {"state": "preparing", "version": "999.0.0", "done": 0, "total": 10}
        return 200, {"from": "engine", "path": path}

    def start(self) -> None:
        self.started.set()
        self.up = True


def live_app(engine: Engine, monkeypatch: pytest.MonkeyPatch, **kw: Any) -> Any:
    from fastapi import FastAPI

    monkeypatch.setattr(update_relay, "_ask", engine.answer)
    app = FastAPI()
    app.include_router(
        update_relay.update_router(
            port=1,
            engine_here=lambda: engine.up,
            port_held=lambda: engine.up,
            start_engine=engine.start,
            **kw,
        )
    )
    return app


@pytest.mark.anyio
async def test_live_says_the_offer_as_build_does_while_the_engine_does_not_run(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = Engine(up=False)
    async with client_for(live_app(engine, monkeypatch)) as c:
        got = (await c.get("/api/update")).json()
    here = self_update.installed_here() is not None
    assert got == self_update.offer(home, __version__, ask=True, installable=here)
    assert got["available"] is True and got["latest"] == "999.0.0" and engine.asked == []


@pytest.mark.anyio
async def test_live_relays_to_the_engine_that_runs(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = Engine(up=True)
    async with client_for(live_app(engine, monkeypatch)) as c:
        assert (await c.get("/api/update")).json() == {"from": "engine", "path": "/api/update"}
        assert (await c.get("/api/update/install")).json()["from"] == "engine"
        assert (await c.delete("/api/update/install")).json()["from"] == "engine"
        posted = await c.post("/api/update/install", json={"force": True})
    assert posted.status_code == 202 and posted.json()["state"] == "preparing"
    assert engine.asked[-1] == ("POST", "/api/update/install", {"force": True})


@pytest.mark.anyio
async def test_not_now_is_kept_for_both_pages(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = Engine(up=False)
    async with client_for(live_app(engine, monkeypatch)) as c:
        assert (await c.post("/api/update/later", json={"version": "999.0.0"})).status_code == 200
        assert (await c.get("/api/update")).json()["later"] is True
    # the file Build's engine reads too
    assert update.later(home / update.CACHE_NAME) == "999.0.0"
    assert self_update.offer(home, __version__, ask=True, installable=False)["later"] is True


@pytest.mark.anyio
async def test_install_starts_the_engine_then_hands_it_the_try(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(self_update, "installed_here", lambda: Path("/Applications/x.app"))
    monkeypatch.setattr(update_relay, "STEP_S", 0.01)
    engine = Engine(up=False)
    async with client_for(live_app(engine, monkeypatch)) as c:
        posted = await c.post("/api/update/install", json={"force": False})
        assert posted.status_code == 202
        assert posted.json() == {"state": "starting", "version": "999.0.0"}
        assert engine.started.wait(5)
        for _ in range(500):
            if ("POST", "/api/update/install", {"force": False}) in engine.asked:
                break
            time.sleep(0.01)
        assert ("POST", "/api/update/install", {"force": False}) in engine.asked
        # the engine says the try's state from now on
        for _ in range(500):
            state = (await c.get("/api/update/install")).json()
            if state.get("from") == "engine":
                break
            time.sleep(0.01)
        assert state == {"from": "engine", "path": "/api/update/install"}


@pytest.mark.anyio
async def test_a_try_cancelled_while_the_engine_starts_is_never_handed(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(self_update, "installed_here", lambda: Path("/Applications/x.app"))
    monkeypatch.setattr(update_relay, "STEP_S", 0.01)
    engine = Engine(up=False)
    engine.start = engine.started.set  # type: ignore[method-assign]  # started, never answering
    async with client_for(live_app(engine, monkeypatch, wait_s=5.0)) as c:
        assert (await c.post("/api/update/install", json={})).json()["state"] == "starting"
        assert (await c.get("/api/update/install")).json()["state"] == "starting"
        assert (await c.delete("/api/update/install")).json() == {"cancelled": True}
        for _ in range(500):
            if (await c.get("/api/update/install")).json() == {"state": "idle"}:
                break
            time.sleep(0.01)
        assert (await c.get("/api/update/install")).json() == {"state": "idle"}
    assert engine.asked == []


@pytest.mark.anyio
async def test_a_checkout_offers_the_link_and_installs_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(self_update, "installed_here", lambda: None)
    engine = Engine(up=False)
    async with client_for(live_app(engine, monkeypatch)) as c:
        assert (await c.get("/api/update")).json()["installable"] is False
        refused = await c.post("/api/update/install", json={})
    assert refused.status_code == 409 and not engine.started.is_set()
