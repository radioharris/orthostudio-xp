# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The new version's banner on Live's page: the same paths as Build's page, answered by
OrthoStudio XP's engine, which alone installs (:class:`orthostudio.self_update.Installer`: it asks
what quitting stops, then quits the whole app). Where the engine does not run, Live says the offer
itself with the same function (:func:`orthostudio.self_update.offer`), and *Install and relaunch*
starts the engine first, the banner saying the installation starts meanwhile."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

# at the module's top: the routes' bodies are read from their annotations
from orthostudio.api.update_banner import UpdateInstallRequest

ENGINE_WAIT_S = 120.0
"""How long an install waits for the engine it started to answer."""
STEP_S = 0.5
HANDING = "Install and relaunch stopped:"
"""How Live's log says an install it could not hand to the engine."""


def _ask(port: int, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """The engine's answer, ``(status, document)``; None when it does not answer."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10.0) as answer:
            return answer.status, json.load(answer)
    except urllib.error.HTTPError as refused:
        try:
            return refused.code, json.load(refused)
        except ValueError:
            return refused.code, {}
    except (OSError, ValueError):
        return None


def update_router(
    *,
    port: int | None = None,
    engine_here: Callable[[], bool] | None = None,
    port_held: Callable[[], bool] | None = None,
    start_engine: Callable[[], None] | None = None,
    wait_s: float = ENGINE_WAIT_S,
    log: Callable[[str], None] | None = None,
) -> Any:
    """``/api/update``, ``/api/update/later`` and ``/api/update/install`` for Live's page. The
    engine's port, whether it answers, whether something holds its port and how it starts are the
    app's (:mod:`orthostudio.desktop`) unless a test says otherwise; ``log`` writes in Live's log
    why an install was not handed over."""
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    from orthostudio import __version__, desktop, self_update
    from orthostudio.api.update_banner import install_refused, later_router
    from orthostudio.config.store import load_settings
    from orthostudio.home import osxp_home

    port = port or desktop.ENGINE_PORT
    here = engine_here or (lambda: desktop.engine_here(port, timeout_s=1.0))
    held = port_held or (lambda: desktop._listening(port))
    start = start_engine or (lambda: desktop.start_engine(desktop.log_path()))
    say = log or (lambda _text: None)
    lock = threading.Lock()
    handing: dict[str, Any] = {}  # the try waiting for the engine: its version

    def relayed(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        got = _ask(port, method, path, body) if here() else None
        return None if got is None else JSONResponse(got[1], status_code=got[0])

    def offered() -> dict[str, Any]:
        return self_update.offer(
            osxp_home(),
            __version__,
            ask=load_settings().expert.check_updates,
            installable=self_update.installed_here() is not None,
        )

    def hand_over(force: bool, first: bool) -> None:
        """The engine started (unless something already starts it), then asked to install."""
        try:
            if first:
                start()
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline:
                if here():
                    _ask(port, "POST", "/api/update/install", {"force": force})
                    break
                time.sleep(STEP_S)
            else:
                say(f"{HANDING} OrthoStudio XP's engine did not answer in {wait_s:.0f} s")
        except Exception as exc:
            say(f"{HANDING} OrthoStudio XP's engine could not be started: {exc!r}")
        finally:
            with lock:
                handing.clear()  # the engine says the try's state from now on, or nothing runs

    router = APIRouter()
    router.include_router(later_router())

    @router.get("/api/update")
    def offer() -> Any:
        return relayed("GET", "/api/update") or offered()

    @router.post("/api/update/install", status_code=202)
    def install(req: UpdateInstallRequest) -> Any:
        answer = relayed("POST", "/api/update/install", {"force": req.force})
        if answer is not None:
            return answer
        now = offered()
        refused = install_refused(now["installable"], now["latest"] if now["available"] else None)
        if refused is not None:
            return refused
        with lock:
            if not handing:
                handing["version"] = now["latest"]
                threading.Thread(
                    target=hand_over,
                    args=(req.force, not held()),
                    name="update-hand-over",
                    daemon=True,
                ).start()
            return {"state": "starting", "version": handing["version"]}

    @router.get("/api/update/install")
    def state() -> Any:
        with lock:
            if handing:
                return {"state": "starting", "version": handing["version"]}
        return relayed("GET", "/api/update/install") or {"state": "idle"}

    @router.delete("/api/update/install")
    def cancel() -> Any:
        return relayed("DELETE", "/api/update/install") or {"cancelled": False}

    return router
