# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The Live page's API, under ``/api/live``: a router of its own, which OrthoStudio XP's engine
can include as it is the day Live comes into its window (``app.include_router``).

- ``GET  /api/live/status``: the state, the last second's figures, the checks, the settings;
  the page asks every second, which also says it is open; ``POST /api/live/presence`` says only
  that (the welcome page);
- ``GET  /api/live/log?after=N``: the log's lines after the N-th;
- ``POST /api/live/start`` ``{"fresh": bool}``: start streaming (409 while a stream is on);
- ``POST /api/live/stop``: stop it; the pack comes out once X-Plane has quit;
- ``POST /api/live/leave``: stop waiting for X-Plane: the pack stays installed;
- ``POST /api/live/quit`` ``{"force": bool}``: the window goes, for the app's update or removal
  (409 while a stream feeds a running X-Plane, unless ``force``);
- ``POST /api/live/uninstall``: take out a pack left installed (409 while X-Plane runs);
- ``POST /api/live/delete`` ``{"textures": bool, "imagery": bool}``: forget them (409 while a
  stream runs, or X-Plane for the textures);
- ``POST /api/live/launch-xplane``: start X-Plane (409 until Live is ready for it);
- ``POST /api/live/home`` ``{"home": "live" | "build", "remember": bool}``: the welcome page's
  answer; ``POST /api/live/osxp``: OrthoStudio XP's window; ``POST /api/live/front``: this window
  to the front, or the page opened again in the browser (``orthostudio.live.app_start``);
- ``GET``/``PUT /api/live/settings``: the settings of ``live.toml`` (:mod:`.settings`);
- ``GET /api/live/providers``: the imagery sources, as OrthoStudio XP's ``GET /api/providers``;
- ``POST /api/live/choose-folder`` ``{"prompt", "start"}``: the system's folder dialog, as
  OrthoStudio XP's ``POST /api/choose-folder``; ``POST /api/live/reveal`` ``{"path"}``: the
  folder in the Finder.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field, ValidationError

from orthostudio.live import session
from orthostudio.live.control import LiveControl
from orthostudio.live.pack import FolderError, check_folder
from orthostudio.live.settings import (
    LiveSettings,
    SettingsFileError,
    load_settings,
    save_settings,
)


class LanguageBody(BaseModel):
    language: Literal["en", "fr"]


class HomeBody(BaseModel):
    home: Literal["live", "build"]
    remember: bool = True


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


class QuitBody(BaseModel):
    force: bool = False


class PrepareBody(BaseModel):
    departure: str = Field("", max_length=40)  # an airport's ICAO code, or lat,lon


def live_router(
    control: LiveControl,
    *,
    front: Callable[[], object] | None = None,
    seen: Callable[[], object] | None = None,
) -> APIRouter:
    """The API of ``control``. ``front`` brings the page to the front, wherever it is shown (the
    window by default); ``seen`` hears the page's every status, its sign of life
    (``orthostudio.live.web``)."""
    router = APIRouter(prefix="/api/live")

    @router.get("/status")
    def status() -> dict[str, Any]:
        if seen is not None:
            seen()
        return control.status()

    @router.post("/presence")
    def presence() -> dict[str, Any]:
        """A page that asks for no status says it is open (the welcome page, every 30 s)."""
        if seen is not None:
            seen()
        return {"ok": True}

    @router.get("/log")
    def log(after: int = 0) -> dict[str, Any]:
        return control.log_after(after)

    @router.post("/start", status_code=202)
    def start(body: StartBody | None = None) -> dict[str, Any]:
        if not control.start(fresh=body.fresh if body else False):
            raise HTTPException(409, "a stream is already on its way, or on its way out")
        return {"state": control.state}

    @router.get("/departure")
    def departure(fresh: bool = False, now: bool = False) -> dict[str, str]:
        """What the "Prepare" box offers: the departure of the pilot's last SimBrief plan;
        ``fresh``: asked again sooner, the window looking for a new plan; ``now``: at once, the
        pilot asking with the plan line's button."""
        return control.departure_suggestion(fresh=fresh, now=now)

    @router.post("/prepare", status_code=202)
    def prepare(body: PrepareBody) -> dict[str, Any]:
        """The ortho round a departure built before X-Plane loads it (404: X-Plane knows no
        such airport; 409: Live is starting or stopping)."""
        refused = control.prepare_departure(body.departure)
        if refused is not None:
            raise HTTPException(409 if refused[0] == "busy" else 404, refused[1])
        return {"state": control.state}

    @router.post("/stop", status_code=202)
    def stop() -> dict[str, Any]:
        control.stop()
        return {"state": control.state}

    @router.post("/leave", status_code=202)
    def leave() -> dict[str, Any]:
        control.leave()
        return {"state": control.state}

    @router.post("/quit", status_code=202)
    def quit_live(body: QuitBody | None = None) -> dict[str, Any]:
        """The app being updated or removed: the Windows installer asks it before it replaces or
        removes the files (``tools/package/stop_running.pas``), as it asks OrthoStudio XP's
        engine."""
        if not control.quit(force=body is not None and body.force):
            raise HTTPException(
                409, "X-Plane is flying with Live: stopping it now leaves X-Plane on what is built"
            )
        return {"state": control.state}

    @router.post("/close")
    def close() -> dict[str, Any]:
        """The window's Quit, answered yes in its question (live.js quitLive): Live goes, at once
        when nothing streams, else once the stream is out."""
        control.close()
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

    @router.post("/language")
    def language(body: LanguageBody) -> dict[str, Any]:
        control.set_language(body.language)
        return {"language": control.language}

    @router.post("/home")
    def home(body: HomeBody) -> dict[str, Any]:
        control.choose_home(body.home, remember=body.remember)
        return {"home": body.home}

    @router.post("/osxp")
    def osxp() -> dict[str, Any]:
        from orthostudio.live.app_start import open_osxp

        return {"opened": open_osxp()}

    @router.post("/front")
    def to_the_front() -> dict[str, Any]:
        if front is not None:
            front()
        else:
            from orthostudio import window

            window.to_the_front()
        return {"front": True}

    @router.get("/regions")
    def regions() -> dict[str, Any]:
        return control.regions(fetch=True)

    @router.post("/regions/cancel")
    def cancel_install() -> dict[str, Any]:
        control.cancel_install()
        return control.regions()

    @router.post("/regions/{region_id}/install", status_code=202)
    def install_region(region_id: str) -> dict[str, Any]:
        try:
            control.install_region(region_id)
        except session.RefusedError as e:
            raise HTTPException(409, str(e)) from e
        return control.regions()

    @router.delete("/regions/{region_id}")
    def remove_region(region_id: str) -> dict[str, Any]:
        try:
            control.remove_region(region_id)
        except session.RefusedError as e:
            raise HTTPException(409, str(e)) from e
        return control.regions()

    @router.get("/map/built")
    def map_built(
        west: float, south: float, east: float, north: float, known: int = -1
    ) -> dict[str, Any]:
        """The textures built in the map's view (orthostudio.live.mapdata); ``known``: the version
        the map has, answered with ``same`` while nothing changed."""
        return control.built_in(west, south, east, north, known)

    @router.get("/map/texture")
    def map_texture(lat: float, lon: float) -> dict[str, Any]:
        """The textures built under a point of the map, with their facts: its tooltip."""
        return {"textures": control.textures_at(lat, lon)}

    @router.get("/map/own")
    def map_own() -> list[dict[str, Any]]:
        """The squares X-Plane takes from the user's own tiles: the map frames them."""
        return control.own_tiles()

    @router.get("/map/regions")
    def map_regions() -> dict[str, Any]:
        """The one-degree squares of the regions installed."""
        from orthostudio.live.mapdata import regions_installed

        installed = {r["id"] for r in control.regions()["list"] if r["installed_by"]}
        return regions_installed(installed)

    @router.post("/map/route")
    def map_route() -> dict[str, Any]:
        """The pilot's last SimBrief plan, asked of simbrief.com on the map's button only."""
        from orthostudio.live.flightplan import last_plan
        from orthostudio.live.mapdata import route_points

        user = load_settings().simbrief_user
        if not user:
            raise HTTPException(409, "no SimBrief name in Live's settings (Flight plan)")
        plan, why = last_plan(user)
        if plan is None:
            raise HTTPException(502, f"SimBrief: {why}")
        return {"name": plan.name, "points": route_points(plan.points)}

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
