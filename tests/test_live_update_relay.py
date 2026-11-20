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

import anyio
import pytest

from orthostudio import __version__, desktop, self_update, update
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

    def answer(self, method: str, path: str, body: Any = None) -> Any:
        self.asked.append((method, path, body))
        if path == "/api/update/install" and method == "POST":
            return 202, {"state": "preparing", "version": "999.0.0", "done": 0, "total": 10}
        return 200, {"from": "engine", "path": path}

    def start(self) -> None:
        self.started.set()
        self.up = True


def live_app(engine: Engine, monkeypatch: pytest.MonkeyPatch, log: Any = lambda _text: None) -> Any:
    from fastapi import FastAPI

    monkeypatch.setattr(update_relay, "_ask", engine.answer)
    monkeypatch.setattr(desktop, "engine_here", lambda *_a, **_k: engine.up)
    monkeypatch.setattr(desktop, "_listening", lambda _port: engine.up)
    monkeypatch.setattr(desktop, "start_engine", lambda _log: engine.start())
    monkeypatch.setattr(update_relay, "STEP_S", 0.01)
    app = FastAPI()
    app.include_router(update_relay.update_router(log=log))
    return app


async def until(pred: Any) -> Any:
    """``pred()``'s first true answer, waited for 5 s at most; its last answer after."""
    for _ in range(500):
        got = await pred()
        if got:
            return got
        await anyio.sleep(0.01)
    return got


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
async def test_install_starts_the_engine_then_hands_it_the_try(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(self_update, "installed_here", lambda: Path("/Applications/x.app"))
    engine = Engine(up=False)
    handed = ("POST", "/api/update/install", {"force": False})
    async with client_for(live_app(engine, monkeypatch)) as c:
        posted = await c.post("/api/update/install", json={"force": False})
        assert posted.status_code == 202
        assert posted.json() == {"state": "starting", "version": "999.0.0"}
        assert engine.started.wait(5)

        async def asked() -> bool:
            return handed in engine.asked

        assert await until(asked)

        async def from_engine() -> Any:  # the engine says the try's state from now on
            state = (await c.get("/api/update/install")).json()
            return state if state.get("from") == "engine" else None

        assert await until(from_engine) == {"from": "engine", "path": "/api/update/install"}


@pytest.mark.anyio
async def test_an_engine_that_cannot_start_leaves_the_banner_and_a_line_in_lives_log(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(self_update, "installed_here", lambda: Path("/Applications/x.app"))
    engine = Engine(up=False)

    def full_disk() -> None:
        raise OSError(28, "No space left on device")

    engine.start = full_disk  # type: ignore[method-assign]
    said: list[str] = []
    async with client_for(live_app(engine, monkeypatch, log=said.append)) as c:
        assert (await c.post("/api/update/install", json={})).json()["state"] == "starting"

        async def idle() -> bool:
            return (await c.get("/api/update/install")).json() == {"state": "idle"}

        assert await until(idle)
    assert len(said) == 1 and "No space left on device" in said[0] and engine.asked == []


@pytest.mark.anyio
async def test_an_engine_that_refuses_or_does_not_answer_leaves_a_line_in_lives_log(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(self_update, "installed_here", lambda: Path("/Applications/x.app"))
    refused = (409, {"error": {"code": "SYS_UPDATE_NONE", "message": "No newer version."}})
    for answer, why in ((refused, "SYS_UPDATE_NONE"), (None, "did not answer")):
        engine = Engine(up=False)

        def asked(method: str, path: str, body: Any = None, got: Any = answer) -> Any:
            return got if method == "POST" else (200, {"state": "idle"})

        engine.answer = asked  # type: ignore[method-assign]
        said: list[str] = []
        async with client_for(live_app(engine, monkeypatch, log=said.append)) as c:
            await c.post("/api/update/install", json={})

            async def idle() -> bool:
                return (await c.get("/api/update/install")).json() == {"state": "idle"}

            assert await until(idle)
        assert len(said) == 1 and said[0].startswith(update_relay.HANDING) and why in said[0]


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
    assert refused.json()["error"]["code"] == "SYS_UPDATE_NOT_HERE"  # as Build's engine says it
