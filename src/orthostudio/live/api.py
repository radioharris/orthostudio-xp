# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The Live page's API, under ``/api/live``: a router of its own, which OrthoStudio XP's engine
can include as it is the day Live comes into its window (``app.include_router``).

- ``GET  /api/live/status``: the state, the last second's figures, the checks, the settings;
  the page asks every second, which also says it is open; ``POST /api/live/presence`` says only
  that (the welcome page);
- ``GET  /api/live/log?after=N``: the log's lines after the N-th;
- ``POST /api/live/start``: start streaming (409 while a stream is on, or while a region is
  being downloaded or installed);
- ``POST /api/live/stop``: stop it; the pack comes out once X-Plane has quit;
- ``POST /api/live/leave``: stop waiting for X-Plane: the pack stays installed;
- ``POST /api/live/quit`` ``{"force": bool}``: the window goes, for the app's update or removal
  (409 while a stream feeds a running X-Plane, unless ``force``);
- ``POST /api/live/uninstall``: take out a pack left installed (409 while X-Plane runs);
- ``POST /api/live/delete`` ``{"textures": bool, "imagery": bool}``: forget them (409 while a
  stream runs, or X-Plane for the textures);
- ``POST /api/live/launch-xplane``: start X-Plane (409 until Live is ready for it);
- ``POST /api/live/home`` ``{"home": "live" | "build", "remember": bool}``: the welcome page's
  answer; ``POST /api/live/osxp``: OrthoStudio XP's window (its part, in the app's frame);
  ``GET /api/live/osxp-build``: whether OrthoStudio XP builds now; ``POST /api/live/front``: this
  window to the front, or the page opened again in the browser (``orthostudio.live.app_start``);
- ``GET``/``PUT /api/live/settings``: the settings of ``live.toml`` (:mod:`.settings`); soft
  banks saved start the download of the installed regions' river masks
  (:meth:`LiveControl.rivers_chosen`);
- ``GET /api/live/map/other``: the squares of the folders of other tools' meshes, each in front of
  Live or not, with the folder that gives it, which the map draws; ``POST`` ``{"square":
  "+45+006"}``: the one its click puts in front where it is behind, back where it is in front, as
  saved now (:func:`~orthostudio.live.settings.flip_front`);
- ``POST /api/live/other/add`` ``{"folder"}``: a folder of another tool's mesh added at the
  bottom of the list (409 when the list has it already); ``POST /api/live/other/move``
  ``{"folder", "up": bool}``: moved one line; ``POST /api/live/other/remove`` ``{"folder"}``:
  forgotten, its files left; ``POST /api/live/other/all`` ``{"folder", "front": bool}``: every
  square it gives put in front of Live or back (409 when it is not there). Each answers the list
  and the squares in front, as saved (:mod:`.settings`, one function each);
- ``GET``/``PUT /api/live/xplane-folder`` ``{"path"}``: the X-Plane folder named in OrthoStudio
  XP's settings, the same for Build and Live, with the other X-Planes of the computer;
- ``GET /api/live/regions``: OrthoStudio XP's regions (:meth:`LiveControl.regions`), each with its
  place in the queue (``queued``) and why its last install failed (``failed``); ``POST
  /api/live/regions/{id}/install`` ``{"whole": bool, "keep_old": bool, "make_room": bool}``: install
  or update one, the region of before kept or not, its kept old mesh deleted first for room or not,
  as asked on an update that replaced the mesh or not (409 where it no longer does so), queued with
  these answers and started at once or once the install under way ends (refused at its turn, it says
  why in its ``failed``); ``POST /api/live/regions/cancel``: stop the install under way and empty
  the queue; ``DELETE /api/live/regions/{id}/queued``: take one out of the queue (nothing done where
  it is not in it); ``GET /api/live/regions/{id}/room``: the room its update lacks beside it, asked
  at the click; ``DELETE /api/live/regions/{id}``: delete its installed mesh, its kept old mesh
  taking its place where it keeps one, else the region; ``DELETE /api/live/regions/{id}/kept``:
  delete its kept old mesh alone (409 for each where it may not);
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

from fastapi import APIRouter, Body, HTTPException, Response
from pydantic import BaseModel, Field, ValidationError

from orthostudio.errors import OsxpError
from orthostudio.live import session
from orthostudio.live.control import LiveControl, Refusal
from orthostudio.live.geo import tile_name
from orthostudio.live.pack import folder_problem
from orthostudio.live.scenery import OtherFolder, move_live
from orthostudio.live.settings import (
    AlreadyListedError,
    LiveSettings,
    SettingsFileError,
    add_other_mesh,
    flip_front,
    load_settings,
    move_other_mesh,
    put_in_front,
    remove_other_mesh,
    saved_with,
)


class LanguageBody(BaseModel):
    language: Literal["en", "fr"]


class HomeBody(BaseModel):
    home: Literal["live", "build"]
    remember: bool = True


class DeleteBody(BaseModel):
    textures: bool = False
    imagery: bool = False


class FolderBody(BaseModel):
    prompt: str = "Choose a folder"
    start: str | None = None


class XplaneFolderBody(BaseModel):
    path: str | None = Field(None, max_length=1024)  # None: the one detected


class RevealBody(BaseModel):
    path: str


class QuitBody(BaseModel):
    force: bool = False


class PrepareBody(BaseModel):
    departure: str = Field("", max_length=40)  # an airport's ICAO code, or lat,lon


class FrontBody(BaseModel):
    square: str = Field(max_length=16)  # "+45+006": in front of Live, or back behind it


class OtherFolderBody(BaseModel):
    folder: str = Field(max_length=1024)  # as the list names it, or a folder to add


class OtherMoveBody(OtherFolderBody):
    up: bool  # one line up, or down


class OtherAllBody(OtherFolderBody):
    front: bool  # every square of it in front of Live, or back behind it


class InstallBody(BaseModel):
    whole: bool  # its update replaced the mesh as the window asked (LiveControl.install_region)
    keep_old: bool = False  # the region of before kept at the swap (osxpregions.kept_dir)
    make_room: bool = False  # its kept old mesh deleted first (osxpregions.Installer.make_room)


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
    def start() -> dict[str, Any]:
        try:
            started = control.start()
        except session.RefusedError as e:  # a region being installed
            raise HTTPException(409, str(e)) from e
        if not started:
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
        """The ortho round a departure built before X-Plane loads it, and where it is (``at``:
        [lat, lon]), the window's map brought there (404: X-Plane knows no such airport; 409:
        Live is starting or stopping)."""
        got = control.prepare_departure(body.departure)
        if isinstance(got, Refusal):
            raise HTTPException(409 if got.why == "busy" else 404, got.what)
        return {"state": control.state, "at": list(got)}

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
        if body.home == "build" and not control.in_frame:
            # OrthoStudio XP's window first, beside this one in its program where it can be, so
            # that the app is still there when this one goes (control.gone); in the app's frame
            # the page asks the frame for OrthoStudio XP's part itself (embed.js askFrame)
            from orthostudio.live.app_start import open_osxp

            open_osxp()
        return {"home": body.home}

    @router.post("/osxp")
    def osxp() -> dict[str, Any]:
        if control.in_frame:
            from orthostudio.live import frame

            shown = frame.current()
            if shown is not None:
                shown.show_part("build")  # OrthoStudio XP's part of the app's frame
                return {"opened": "here"}
        from orthostudio.live.app_start import open_osxp

        return {"opened": open_osxp()}

    @router.get("/osxp-build")
    def osxp_build() -> dict[str, Any]:
        """Whether OrthoStudio XP builds now, which a start or a departure prepared asks first: a
        build uses a lot of processor time and may slow X-Plane and Live down (a question, never a
        refusal). A second at most: an engine that does not answer builds nothing worth asking
        about."""
        from orthostudio.desktop import ENGINE_PORT, _listening, a_build_runs

        return {"building": _listening(ENGINE_PORT) and a_build_runs(ENGINE_PORT, timeout_s=1.0)}

    @router.post("/front")
    def to_the_front() -> dict[str, Any]:
        if front is not None:
            front()
        else:
            from orthostudio import window
            from orthostudio.live.web import TITLE

            window.to_the_front(TITLE)
        return {"front": True}

    @router.get("/regions")
    def regions() -> dict[str, Any]:
        return control.regions(fetch=True)

    @router.post("/regions/cancel")
    def cancel_install() -> dict[str, Any]:
        control.cancel_install()
        return control.regions()

    @router.post("/regions/{region_id}/install", status_code=202)
    def install_region(region_id: str, body: InstallBody) -> dict[str, Any]:
        try:
            control.install_region(
                region_id, whole=body.whole, keep_old=body.keep_old, make_room=body.make_room
            )
        except session.RefusedError as e:
            raise HTTPException(409, str(e)) from e
        return control.regions()

    @router.delete("/regions/{region_id}/queued")
    def unqueue_region(region_id: str) -> dict[str, Any]:
        control.unqueue_region(region_id)
        return control.regions()

    @router.get("/regions/{region_id}/room")
    def update_room(region_id: str) -> dict[str, Any]:
        return {"room": control.update_room(region_id)}

    @router.delete("/regions/{region_id}/kept")
    def remove_kept(region_id: str) -> dict[str, Any]:
        try:
            control.remove_kept(region_id)
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

    @router.get("/map/other")
    def map_other() -> list[dict[str, Any]]:
        """The squares of the folders of other tools' meshes, in front of Live or not, each with
        the folder that gives it."""
        return control.other_squares()

    def other_changed(change: Callable[[], LiveSettings]) -> dict[str, Any]:
        """A change of the folders of other tools' meshes, or of the squares in front of Live,
        saved by its one function in :mod:`.settings`: the list and the squares as it saved them,
        the checks said anew at the next status. 409 for a folder the list has already, 422 for a
        value refused, 500 when the settings cannot be saved."""
        try:
            saved = change()
        except AlreadyListedError as e:
            raise HTTPException(409, str(e)) from e
        except ValidationError as e:  # a square's name refused: its own words
            raise HTTPException(422, e.errors()[0].get("msg", "invalid")) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except (OSError, SettingsFileError) as e:
            raise HTTPException(500, f"the settings could not be saved: {e}") from e
        control.checks_changed()
        return {"other_meshes": saved.other_meshes, "other_mesh_front": saved.other_mesh_front}

    @router.post("/map/other")
    def map_other_front(body: FrontBody) -> dict[str, Any]:
        """A square of those folders clicked on the map: put in front of Live where it is behind,
        back where it is in front, decided here, as saved now: two quick clicks flip it twice."""
        return other_changed(lambda: flip_front(body.square))

    @router.post("/other/add")
    def other_add(body: OtherFolderBody) -> dict[str, Any]:
        """A folder typed or chosen: added at the bottom of the list."""
        return other_changed(lambda: add_other_mesh(body.folder))

    @router.post("/other/move")
    def other_move(body: OtherMoveBody) -> dict[str, Any]:
        """A folder's ↑ or ↓: moved one line."""
        return other_changed(lambda: move_other_mesh(body.folder, body.up))

    def folder_gives(folder: str) -> tuple[OtherFolder, list[str]]:
        """The folder of the list the page names, and the squares it gives
        (``OtherMeshes.gives``: not those a folder above it gives, whose own buttons and clicks
        decide them), from the folders as last read, outside the settings' lock: the squares its
        « All in front », « All behind » and Remove change, those its line counts
        (``scenery.other_mesh_said``). 422 for a folder not listed."""
        found = control.other_found()
        one = found.named(folder)
        if one is None:
            raise HTTPException(422, f"{folder} is not in the list of other tools' meshes")
        return one, [tile_name(*square) for square in found.gives(one.folder)]

    @router.post("/other/remove")
    def other_remove(body: OtherFolderBody) -> dict[str, Any]:
        """A folder's Remove: « All behind » on it, then forgotten, its files left as they are; a
        folder not there gives nothing, nothing put behind."""
        _, squares = folder_gives(body.folder)
        return other_changed(lambda: remove_other_mesh(body.folder, squares))

    @router.post("/other/all")
    def other_all(body: OtherAllBody) -> dict[str, Any]:
        """A folder's « All in front » or « All behind »: every square it gives in front of Live,
        or back behind it."""
        one, squares = folder_gives(body.folder)
        if not one.there:
            raise HTTPException(409, f"{body.folder} is not there: is its disk unplugged?")
        return other_changed(lambda: put_in_front(squares, body.front))

    @router.get("/map/regions")
    def map_regions() -> dict[str, Any]:
        """The one-degree squares of OrthoStudio XP's regions installed."""
        from orthostudio.live.mapdata import regions_installed

        installed = {r["id"] for r in control.regions()["ours"]}
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

    @router.get("/xplane-folder")
    def get_xplane_folder() -> dict[str, Any]:
        return control.xplane_folders()

    @router.put("/xplane-folder")
    def put_xplane_folder(body: XplaneFolderBody) -> dict[str, Any]:
        try:
            control.name_xplane(body.path)
        except session.RefusedError as e:
            raise HTTPException(409, str(e)) from e
        return control.xplane_folders()

    @router.post("/reveal")
    def reveal(body: RevealBody) -> dict[str, Any]:
        from orthostudio.fsutil import reveal_in_file_manager

        target = Path(body.path).expanduser()
        if not target.exists():
            raise HTTPException(404, f"{target} is not there")
        reveal_in_file_manager(target)
        return {"revealed": str(target)}

    @router.get("/look/sample")
    def look_sample(index: int = 0, provider: str | None = None) -> Response:
        """A photo of the ground Live keeps, for the season preview: the places downloaded last,
        ``index`` going through them (404: none kept yet)."""
        from orthostudio.live.look import sample_photo
        from orthostudio.live.pack import default_cache_path
        from orthostudio.live.providers import cache_key

        try:
            key = cache_key(provider or load_settings().provider)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        body = sample_photo(default_cache_path(), key, index)
        if body is None:
            raise HTTPException(404, "Live keeps no photo yet: fly once with Live")
        return Response(body, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @router.get("/settings")
    def get_settings() -> dict[str, Any]:
        return load_settings().model_dump()

    @router.put("/settings")
    def put_settings(body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
        def validated(now: LiveSettings) -> LiveSettings:
            try:
                return LiveSettings.model_validate({**now.model_dump(), **body})
            except ValidationError as e:
                first = e.errors()[0]
                where = ".".join(str(p) for p in first.get("loc", ()))
                raise HTTPException(422, f"{where}: {first.get('msg', 'invalid')}") from e

        # Live's folder checked on the disk before the settings' lock, which every save waits for
        folder = validated(load_settings()).folder
        if folder:
            problem = folder_problem(Path(folder), session.xplane_or_none())
            if problem is not None:
                raise HTTPException(422, f"folder: {problem}")
        before = LiveSettings()

        def change(now: LiveSettings) -> dict[str, Any]:
            nonlocal before
            before = now
            return validated(now).model_dump()

        try:  # a click or a folder's button meanwhile kept: one write after the other
            settings = saved_with(change)
        except (OSError, SettingsFileError) as e:
            raise HTTPException(500, f"the settings could not be saved: {e}") from e
        from orthostudio.pipeline.night import library_strength, luminance

        lum = luminance(settings)
        if lum != luminance(before):
            tiles = library_strength(lum)
            if tiles:
                control.log(f"the night lights of {tiles} of your own tiles follow Live's")
        if settings.own_tiles != before.own_tiles:  # Live's line once above or below the tiles
            try:
                move_live(session.xplane_dir(None), settings.own_tiles)
            except (session.RefusedError, OSError, OsxpError) as e:
                control.log(f"Live's line in X-Plane's list stays where it is: {e}")
        if settings.rivers_fade and not before.rivers_fade:  # soft banks: their river masks
            control.rivers_chosen()
        return settings.model_dump()

    return router
