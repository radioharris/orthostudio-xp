# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The Live page's API, under ``/api/live``: a router of its own, which OrthoStudio XP's engine
can include as it is the day Live comes into its window (``app.include_router``).

- ``GET  /api/live/status``: the state, the last second's figures, the checks, the settings;
- ``GET  /api/live/log?after=N``: the log's lines after the N-th;
- ``POST /api/live/start`` ``{"fresh": bool}``: start streaming (409 while a stream is on);
- ``POST /api/live/stop``: stop it; the pack comes out once X-Plane has quit;
- ``POST /api/live/leave``: stop waiting for X-Plane: the pack stays installed;
- ``POST /api/live/uninstall``: take out a pack left installed (409 while X-Plane runs);
- ``POST /api/live/delete`` ``{"textures": bool, "imagery": bool}``: forget them (409 while a
  stream runs, or X-Plane for the textures);
- ``POST /api/live/launch-xplane``: start X-Plane (409 until Live is ready for it);
- ``GET``/``PUT /api/live/settings``: the settings of ``live.toml`` (:mod:`.settings`);
- ``GET /api/live/providers``: the imagery sources, as OrthoStudio XP's ``GET /api/providers``;
- ``POST /api/live/choose-folder`` ``{"prompt", "start"}``: the system's folder dialog, as
  OrthoStudio XP's ``POST /api/choose-folder``; ``POST /api/live/reveal`` ``{"path"}``: the
  folder in the Finder.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ValidationError

from orthostudio.live import session
from orthostudio.live.control import LiveControl
from orthostudio.live.pack import FolderError, check_folder
from orthostudio.live.settings import (
    LiveSettings,
    SettingsFileError,
    load_settings,
    save_settings,
)


class StartBody(BaseModel):
    fresh: bool = False


class DeleteBody(BaseModel):
    textures: bool = False
    imagery: bool = False


class FolderBody(BaseModel):
    prompt: str = "Choose a folder"
    start: str | None = None


class RevealBody(BaseModel):
    path: str


def live_router(control: LiveControl) -> APIRouter:
    router = APIRouter(prefix="/api/live")

    @router.get("/status")
    def status() -> dict[str, Any]:
        return control.status()

    @router.get("/log")
    def log(after: int = 0) -> dict[str, Any]:
        return control.log_after(after)

    @router.post("/start", status_code=202)
    def start(body: StartBody | None = None) -> dict[str, Any]:
        if not control.start(fresh=body.fresh if body else False):
            raise HTTPException(409, "a stream is already on its way, or on its way out")
        return {"state": control.state}

    @router.post("/stop", status_code=202)
    def stop() -> dict[str, Any]:
        control.stop()
        return {"state": control.state}

    @router.post("/leave", status_code=202)
    def leave() -> dict[str, Any]:
        control.leave()
        return {"state": control.state}

    @router.post("/uninstall")
    def uninstall() -> dict[str, Any]:
        if not control.uninstall():
            raise HTTPException(
                409, "not while a stream runs, or while X-Plane does: it reads the pack"
            )
        return {"state": control.state}

    @router.post("/delete")
    def delete(body: DeleteBody) -> dict[str, Any]:
        try:
            control.delete(textures=body.textures, imagery=body.imagery)
        except session.RefusedError as e:
            raise HTTPException(409, str(e)) from e
        return {"state": control.state}

    @router.post("/launch-xplane")
    def launch_xplane() -> dict[str, Any]:
        try:
            control.launch_xplane()
        except session.RefusedError as e:
            raise HTTPException(409, str(e)) from e
        except OSError as e:
            raise HTTPException(500, f"X-Plane could not be started: {e}") from e
        return {"state": control.state}

    @router.get("/providers")
    def providers() -> list[dict[str, Any]]:
        from orthostudio.live.providers import listing

        return listing()

    dialog_open = threading.Lock()

    @router.post("/choose-folder")
    def choose_folder(body: FolderBody) -> dict[str, Any]:
        from orthostudio.fsutil import choose_folder as ask

        if not dialog_open.acquire(blocking=False):
            raise HTTPException(409, "a folder dialog is already open")
        try:
            start = Path(body.start).expanduser() if body.start else None
            chosen = ask(body.prompt, start if start is not None and start.is_dir() else None)
        except (OSError, subprocess.SubprocessError) as e:
            raise HTTPException(500, f"no folder dialog could be opened: {e}") from e
        finally:
            dialog_open.release()
        return {"path": None if chosen is None else str(chosen)}

    @router.post("/reveal")
    def reveal(body: RevealBody) -> dict[str, Any]:
        from orthostudio.fsutil import reveal_in_file_manager

        target = Path(body.path).expanduser()
        if not target.exists():
            raise HTTPException(404, f"{target} is not there")
        reveal_in_file_manager(target)
        return {"revealed": str(target)}

    @router.get("/settings")
    def get_settings() -> dict[str, Any]:
        return load_settings().model_dump()

    @router.put("/settings")
    def put_settings(body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
        try:
            settings = LiveSettings.model_validate({**load_settings().model_dump(), **body})
        except ValidationError as e:
            first = e.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            raise HTTPException(422, f"{where}: {first.get('msg', 'invalid')}") from e
        if settings.folder:
            try:
                scenery = session.xplane_dir(None) / "Custom Scenery"
            except session.RefusedError:
                scenery = None
            try:
                check_folder(Path(settings.folder), custom_scenery=scenery)
            except FolderError as e:
                raise HTTPException(422, f"folder: {e}") from e
        try:
            save_settings(settings)
        except (OSError, SettingsFileError) as e:
            raise HTTPException(500, f"the settings could not be saved: {e}") from e
        return settings.model_dump()

    return router
