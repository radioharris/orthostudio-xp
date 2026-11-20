# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The new version's banner on Live's page: the same paths as Build's page, answered by
OrthoStudio XP's engine, which alone installs (:class:`orthostudio.self_update.Installer`: it asks
what quitting stops, then quits the whole app). Where the engine does not run, Live says the offer
itself with the same function (:func:`orthostudio.self_update.offer`), and *Install and relaunch*
starts the engine first, the banner saying the installation starts meanwhile."""

from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from orthostudio import __version__, desktop, self_update
from orthostudio.api.update_banner import UpdateInstallRequest, install_refused, later_router
from orthostudio.config.store import load_settings
from orthostudio.home import osxp_home

ENGINE_WAIT_S = 120.0
"""How long an install waits for the engine it started to answer."""
STEP_S = 0.5
HANDING = "Install and relaunch stopped:"
"""How Live's log says an install it could not hand to the engine."""


def _ask(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """The engine's answer, ``(status, document)``; None when it does not answer."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{desktop.ENGINE_PORT}{path}",
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
        except (ValueError, OSError, http.client.HTTPException):
            return refused.code, {}
    except (OSError, ValueError, http.client.HTTPException):
        return None


def _refusal(document: Any) -> str:
    """What an engine's refusal says, in one line."""
    error = document.get("error") if isinstance(document, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return f"{error['message']} ({error.get('code')})"
    return "the engine refused it"


def update_router(*, log: Callable[[str], None]) -> Any:
    """``/api/update``, ``/api/update/later`` and ``/api/update/install`` for Live's page,
    OrthoStudio XP's engine (:mod:`orthostudio.desktop`) asked when it answers; ``log`` writes in
    Live's log why an install was not handed over."""
    lock = threading.Lock()
    handing: dict[str, Any] = {}  # the try waiting for the engine: its version

    def here() -> bool:
        return desktop.engine_here(desktop.ENGINE_PORT, timeout_s=1.0)

    def relayed(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        got = _ask(method, path, body) if here() else None
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
                desktop.start_engine(desktop.log_path())
            deadline = time.monotonic() + ENGINE_WAIT_S
            while time.monotonic() < deadline:
                if here():
                    got = _ask("POST", "/api/update/install", {"force": force})
                    if got is None:
                        log(f"{HANDING} OrthoStudio XP's engine did not answer")
                    elif got[0] >= 400:
                        log(f"{HANDING} {_refusal(got[1])}")
                    break
                time.sleep(STEP_S)
            else:
                log(f"{HANDING} OrthoStudio XP's engine did not answer in {ENGINE_WAIT_S:.0f} s")
        except Exception as exc:
            log(f"{HANDING} OrthoStudio XP's engine could not be started: {exc!r}")
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
        refused = install_refused(now)
        if refused is not None:
            return refused
        with lock:
            if not handing:
                handing["version"] = now["latest"]
                threading.Thread(
                    target=hand_over,
                    args=(req.force, not desktop._listening(desktop.ENGINE_PORT)),
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
