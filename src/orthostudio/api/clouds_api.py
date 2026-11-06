# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The cloud mode's routes (``docs/specs/api.md``, ``clouds.md`` 6).

``GET /api/clouds`` the Library's column; ``GET /api/clouds/{tile}`` the mode (the tile's state,
its picture, every cloud's row, what Apply would change, its tasks); ``POST .../find`` a search;
``POST .../picks`` a tick or a source (getting the cloud's images when it needs them);
``POST .../outlines`` and ``DELETE .../outlines/{id}`` a cloud outlined by hand;
``POST .../discard``; ``POST .../apply`` (``restore`` to bring back every cloud), a task;
``GET .../files/{name}`` the picture and the previews; ``POST /api/clouds/tasks/{id}/stop``.
A task is named by the action that starts it: ``find``, ``check``, ``outline``, ``apply``,
``restore``.
"""

from __future__ import annotations

import contextlib
import io
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, Body
from fastapi.responses import FileResponse
from PIL import Image

from orthostudio.clouds import cloudlist as cls
from orthostudio.clouds import state
from orthostudio.clouds.find import check, find, fine_level, preview, screen
from orthostudio.clouds.laying import folder_lock, show
from orthostudio.clouds.model import detector_bytes, detector_ready, ensure_detector
from orthostudio.clouds.opencv import ensure_opencv, opencv_bytes, opencv_ready
from orthostudio.clouds.picture import Picture, box_bounds, draw, search_level
from orthostudio.clouds.sources import SourcePieces, other_sources
from orthostudio.clouds.tasks import Task, Tasks
from orthostudio.clouds.tile import (
    TileClouds,
    is_own_folder,
    lists_dir,
    own_folder,
    previews_dir,
    read_tile,
)
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_bytes
from orthostudio.graph.digest import digest_bytes
from orthostudio.home import data_root_missing, default_chunks_root, disk_absent
from orthostudio.imagery.providers import load_registry
from orthostudio.install.library import Library, default_library_path
from orthostudio.model import TileRef

__all__ = ["CloudHooks", "clouds_router"]

_NAME = re.compile(r"^[0-9a-f]{16,128}\.(png|jpg)$")


@dataclass(frozen=True)
class CloudHooks:
    """What the routes ask of the engine: X-Plane's Custom Scenery (``None`` without one), and
    the tiles in a build or queued (``tile -> 'running' | 'queued'``)."""

    custom_scenery: Callable[[], Path | None]
    building: Callable[[], Mapping[str, str]]


class CloudEngine:
    """The tasks, and what is kept in memory of a tile: its picture at the map's level, the
    classes its last search read."""

    def __init__(self, hooks: CloudHooks) -> None:
        self.hooks = hooks
        self.tasks = Tasks()
        self._pictures: dict[tuple[str, str, int], Picture] = {}
        self._drawing: dict[tuple[str, str, int], threading.Lock] = {}
        self._classes: dict[tuple[str, str], np.ndarray] = {}
        self._lock = threading.Lock()

    def folder(self, tile: TileRef) -> Path:
        return own_folder(tile, custom_scenery=self.hooks.custom_scenery())

    def picture(self, tile: TileRef, shown: TileClouds, zl: int) -> Picture:
        """The tile at ``zl`` as X-Plane shows it once every cloud is unticked: its textures as
        the build made them. The map's level is kept (the last few, per build); a finer one,
        read once by a search, is not."""
        key = (tile.name, shown.built, zl)
        with self._lock:
            drawing = self._drawing.setdefault(key, threading.Lock())
        with drawing:  # two answers asking for it at once: one drawing
            with self._lock:
                hit = self._pictures.get(key)
            if hit is not None:
                return hit
            p = _draw(tile, shown, zl)
            with self._lock:
                self._drawing.pop(key, None)
                if zl == _level(tile, shown):
                    if len(self._pictures) > 4:
                        self._pictures.pop(next(iter(self._pictures)))
                    self._pictures[key] = p
            return p

    def remember(self, tile: TileRef, shown: TileClouds, classes: np.ndarray) -> None:
        """The classes the tile's last search read, for an outline's band to leave others'
        clouds out; one tile's at a time."""
        with self._lock:
            self._classes = {(tile.name, shown.built): classes}

    def classes(self, tile: TileRef, shown: TileClouds) -> np.ndarray | None:
        with self._lock:
            return self._classes.get((tile.name, shown.built))

    def let_go(self, tile: TileRef) -> None:
        """A tile about to be deleted: its tasks stopped, what is kept of it in memory dropped."""
        for t in self.tasks.of(tile.name):
            self.tasks.stop(t.id)
        self.tasks.settle(30.0, tile.name)  # an Apply ends what it began before its folder goes
        with self._lock:
            for drawn in [k for k in self._pictures if k[0] == tile.name]:
                del self._pictures[drawn]
            for read in [k for k in self._classes if k[0] == tile.name]:
                del self._classes[read]

    @contextlib.contextmanager
    def forgetting(
        self, tile: TileRef, folder: Path, *, custom_scenery: Path | None
    ) -> Iterator[bool]:
        """Around the deletion of ``folder``: when it is the tile's own, its tasks stopped and
        what is kept of it dropped first, its list and previews once it is deleted
        (``cloudlist.forgetting``)."""
        with cls.forgetting(tile, folder, custom_scenery=custom_scenery) as ours:
            if ours:
                self.let_go(tile)
            yield ours


def _level(tile: TileRef, shown: TileClouds) -> int:
    """The map's level: the search's, or the tile's textures' own when coarser."""
    return min(search_level(tile), min(t.zl for t in shown.textures))


def _picture_file(tile: TileRef, p: Picture, shown: TileClouds) -> dict[str, Any]:
    """The picture as the page lays it: a JPEG named by the build and the level, written whole."""
    name = digest_bytes(f"{shown.built}:{p.zl}:picture-1".encode())[:32] + ".jpg"
    path = previews_dir(tile) / name
    if not path.exists():
        buf = io.BytesIO()
        Image.fromarray(p.rgb).save(buf, "JPEG", quality=88)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, buf.getvalue())
    h, w = p.rgb.shape[:2]
    return {"name": name, "bounds": box_bounds(p, (0, 0, w, h)), "level": p.zl}


def _draw(tile: TileRef, shown: TileClouds, zl: int) -> Picture:
    """The tile at ``zl`` from its folder, its textures as the build made them: read while none
    of them is written, which ones show clouds read then."""
    with folder_lock(tile):
        now = read_tile(shown.folder) or shown
        return draw(tile, zl, dsf=shown.folder / tile.dsf_relpath, textures=now.plains())


def _tile_of(name: str) -> TileRef:
    try:
        return TileRef.parse(name)
    except ValueError:
        raise OsxpError(
            "CFG_LATLON_INVALID",
            context={"value": name},
            message=f"{name!r} is not a tile name.",
            remedy="Use a tile name such as +43+005.",
        ) from None


def _cloud_of(tile: TileRef, body: Mapping[str, Any] | None) -> str:
    cid = (body or {}).get("id")
    if not isinstance(cid, str) or not cid:
        raise OsxpError(
            "CLOUD_INVALID", context={"tile": tile.name, "cloud": "", "reason": "no cloud named"}
        )
    return cid


def _ring_of(tile: TileRef, body: Mapping[str, Any] | None) -> list[float]:
    """An outline's ring in the zones' format from ``polygon`` (``[[lon, lat], ...]``): three
    points or more around some ground."""
    ring: list[float] = []
    try:
        points = [(float(lon), float(lat)) for lon, lat in (body or {}).get("polygon") or ()]
    except (TypeError, ValueError):
        points = []
    if len(set(points)) >= 3:
        for lon, lat in [*points, points[0]]:
            ring += [lat, lon]
    if not ring or cls.rings_ha([ring]) <= 0:
        raise OsxpError(
            "CLOUD_INVALID",
            context={"tile": tile.name, "cloud": "", "reason": "an outline needs three points"},
        )
    return ring


def _detector_said(body: Mapping[str, Any] | None) -> None:
    """``CLOUD_DETECTOR_ASK`` while the detector is not on this computer and the user has not
    said to download it (``download``): the page asks, then sends again."""
    if (detector_ready() and opencv_ready()) or (body or {}).get("download"):
        return
    mb = max(1, round((detector_bytes() + opencv_bytes()) / 1e6))
    raise OsxpError("CLOUD_DETECTOR_ASK", context={"mb": mb})


def _tools(task: Task) -> None:
    """The detector and OpenCV, downloaded once, before a task runs them."""
    ensure_opencv(progress=lambda d, t: task.say("tool", d, t))
    ensure_detector(progress=lambda d, t: task.say("tool", d, t))


def clouds_router(hooks: CloudHooks) -> tuple[APIRouter, CloudEngine]:
    """The routes, and the engine (its tasks for Quit to stop, a deleted tile to forget). Every
    route reads what the tile and its clouds allow from one view (``state.view``)."""
    engine = CloudEngine(hooks)
    router = APIRouter()

    def look(
        tile: TileRef, cl: cls.CloudList | None = None
    ) -> tuple[Path, TileClouds | None, state.View]:
        """The tile's folder, what it shows (``None`` in a state its folder is not read in) and
        its view, of its list or of ``cl`` (a pick before it is kept)."""
        folder = engine.folder(tile)
        building = hooks.building().get(tile.name)
        shown = None if building else read_tile(folder)
        st = state.tile_state(
            data_away=data_root_missing() is not None,
            disk_away=disk_absent(folder),  # the build's own rule (``build._homes``)
            building=building,
            shown=shown,
        )
        if not state.TILE_STATES[st].reads:
            shown = None
        tasks = [t.view() for t in engine.tasks.of(tile.name)]
        return folder, shown, state.view(cl or cls.load(tile), shown, tile, st, tasks)

    def opened(
        name: str,
        action: str,
        body: Mapping[str, Any] | None = None,
        source: str | None = None,
    ) -> tuple[TileRef, Path, TileClouds, state.View]:
        """The tile ``name`` for ``action``, of the cloud ``body`` names when it names one (its
        ``id``; ``choose``: of ``source``): refused unless its view allows it."""
        tile = _tile_of(name)
        folder, shown, v = look(tile)
        if body is None:
            if not v.allows(action):
                raise OsxpError(
                    "CLOUD_TILE_NOT_READY", context={"tile": tile.name, "state": v.state}
                )
        else:
            cid = _cloud_of(tile, body)
            v.cloud(cid)  # CLOUD_UNKNOWN when it is not in the list
            if not v.allows(action, cid, source):
                raise OsxpError(
                    "CLOUD_INVALID",
                    context={
                        "tile": tile.name,
                        "cloud": cid,
                        "reason": f"{action} not possible now",
                    },
                )
        assert shown is not None  # a view allowing anything has its folder read
        return tile, folder, shown, v

    @router.get("/api/clouds/{name}")
    def tile_mode(name: str) -> dict[str, Any]:
        tile = _tile_of(name)
        _folder, shown, v = look(tile)
        picture, previews = None, {}
        if shown is not None:
            p = engine.picture(tile, shown, _level(tile, shown))
            picture = _picture_file(tile, p, shown)
            for entry in v.laid:  # what After lays: what Apply lays
                got = preview(p, tile, entry, previews_dir(tile), shown.built)
                if got is not None:
                    previews[entry["id"]] = {"name": got[0], "bounds": got[1]}
        failed = engine.tasks.failed(tile.name)
        return {
            "tile": tile.name,
            "state": v.state,
            "busy": v.busy,
            "line": v.line,
            "allowed": sorted(v.allowed),
            "views": v.views(picture is not None),
            "texts": v.texts(),
            "picture": picture,
            "clouds": [s.page(previews) for s in v.clouds],
            "tasks": v.tasks,
            "failed": failed.view() if failed else None,
        }

    @router.get("/api/clouds")
    def library_column() -> dict[str, Any]:
        """The Library's Clouds cell of each row it is offered on, by the row's path: a tile
        OrthoStudio XP built, the row its own folder. Its words are read of a tile with a list
        only: no other tile's folder is read (a disk asleep stays asleep)."""
        listed = {p.stem for p in lists_dir().glob("*.json")}
        try:
            with Library(default_library_path()) as lib:
                rows = lib.list(kind="ortho")
        except Exception:  # no library, no cell
            return {"rows": {}}
        cs = hooks.custom_scenery()
        out: dict[str, Any] = {}
        for r in rows:
            with contextlib.suppress(Exception):
                if r.built_by != "osxp" or not r.path.is_dir():
                    continue
                if not is_own_folder(r.tile, r.path, custom_scenery=cs):
                    continue
                cell = {"replaced": 0, "to_apply": 0}
                if r.tile.name in listed:
                    cell = look(r.tile)[2].library()
                out[str(r.path)] = cell
        return {"rows": out}

    def sources_for(tile: TileRef, shown: TileClouds) -> SourcePieces:
        return SourcePieces(
            default_chunks_root(), other_sources(tile, shown.provider, load_registry())
        )

    def get_images(
        tile: TileRef, cid: str, body: Mapping[str, Any] | None, *, again: bool = False
    ) -> None:
        """Getting the images of the cloud ``cid`` when its state offers it (``check``), the
        detector asked for first (``body``): the one way it starts, after a tick, a source
        chosen, an outline's sources tried, or the user's Get its images (``again``: its source
        asked again)."""
        _folder, shown, v = look(tile)
        if shown is None or not v.allows("check", cid):
            return
        _detector_said(body)
        s = v.cloud(cid)
        code, rings = s.source, s.cloud["rings"]
        assert code is not None  # a cloud is offered images of its source only
        since = cls.generation(tile)

        def run(task: Task) -> None:
            _tools(task)
            p = engine.picture(tile, shown, _level(tile, shown))
            registry = load_registry()
            if code not in registry:
                raise OsxpError(
                    "CFG_PROVIDER_UNKNOWN",
                    context={"provider": code, "known": ", ".join(sorted(registry))},
                )
            got = check(
                p,
                tile,
                cid,
                rings,
                code,
                registry[code],
                shown,
                SourcePieces(default_chunks_root(), {code: registry[code]}),
                classes=engine.classes(tile, shown),
                again=again,
                cancel=task.stop,
                phase=task.say,
            )
            with cls.update(tile, since=since) as cl:
                cls.record_check(cl, cid, code, rings, got)

        engine.tasks.start(tile.name, "check", run, cloud=cid)

    @router.post("/api/clouds/{name}/find")
    def find_clouds(
        name: str, body: Annotated[dict[str, Any] | None, Body()] = None
    ) -> dict[str, Any]:
        tile, _folder, shown, _v = opened(name, "find")
        _detector_said(body)  # the search runs it
        since = cls.generation(tile)

        def run(task: Task) -> None:
            _tools(task)
            zl = _level(tile, shown)
            p = engine.picture(tile, shown, zl)
            finer = fine_level(zl, shown.textures)
            fine = engine.picture(tile, shown, finer) if finer is not None else None
            areas, classes = find(p, fine, cancel=task.stop, phase=task.say)
            rings = [a.rings(p) for a in areas]
            verdicts = screen(
                p, rings, classes, sources_for(tile, shown), cancel=task.stop, phase=task.say
            )
            found = [
                {"rings": r, "screening": cls.screening_of(v)}
                for r, v in zip(rings, verdicts, strict=True)
            ]
            with cls.update(tile, since=since) as cl:
                cls.merge_search(cl, found, shown, {"level": zl, "found": len(found)})
            engine.remember(tile, shown, classes)

        task = engine.tasks.start(tile.name, "find", run)
        return {"task": task.view()}

    @router.post("/api/clouds/{name}/picks")
    def picks(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        action = cls.pick_action(body.get("tick"), body.get("source"))
        tile, _folder, _shown, _v = opened(name, action, body, body.get("source"))
        cid = body["id"]
        with cls.update(tile) as cl:
            cls.pick(cl, cid, action, body.get("source"))
            if look(tile, cl)[2].allows("check", cid):  # its images need the detector: asked
                _detector_said(body)  # before anything is kept, the same pick sent again on yes
        get_images(tile, cid, body)  # its images got when its state asks them
        return {"ok": True}

    @router.post("/api/clouds/{name}/check")
    def check_again(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        tile, _folder, _shown, _v = opened(name, "check", body)
        get_images(tile, body["id"], body, again=True)
        return {"ok": True}

    @router.post("/api/clouds/{name}/outlines")
    def outline(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        tile, _folder, shown, _v = opened(name, "outline")
        ring = _ring_of(tile, body)
        _detector_said(body)  # its sources are tried, then its images got
        p = engine.picture(tile, shown, _level(tile, shown))
        with cls.update(tile) as cl:
            cid = cls.add_outline(cl, [ring], shown)["id"]
        since = cls.generation(tile)

        def run(task: Task) -> None:
            _tools(task)
            classes = engine.classes(tile, shown)
            if classes is None:
                classes = np.zeros(p.rgb.shape[:2], np.uint8)
            (verdicts,) = screen(
                p, [[ring]], classes, sources_for(tile, shown), cancel=task.stop, phase=task.say
            )
            with cls.update(tile, since=since) as cl:
                cls.record_screening(cl, cid, verdicts)

        def then() -> None:
            """Drawing it is asking to replace it: ticked when its state allows it, its images
            got when its state asks them (the detector on this computer: the task got it)."""
            if look(tile)[2].allows("tick", cid):
                with cls.update(tile, since=since) as cl:
                    cls.pick(cl, cid, "tick")
            get_images(tile, cid, {"download": True})

        engine.tasks.start(tile.name, "outline", run, cloud=cid, then=then)
        return {"id": cid}

    @router.delete("/api/clouds/{name}/outlines/{cid}")
    def remove(name: str, cid: str) -> dict[str, Any]:
        tile, _folder, _shown, _v = opened(name, "remove", {"id": cid})
        with cls.update(tile) as cl:
            cls.remove_outline(cl, cid)
        return {"ok": True}

    @router.post("/api/clouds/{name}/discard")
    def discard(name: str) -> dict[str, Any]:
        tile, _folder, shown, _v = opened(name, "discard")
        with cls.update(tile) as cl:
            cls.discard(cl, shown)
        return {"ok": True}

    @router.post("/api/clouds/{name}/apply")
    def apply(name: str, body: Annotated[dict[str, Any] | None, Body()] = None) -> dict[str, Any]:
        action = "restore" if (body or {}).get("restore") else "apply"
        tile, folder, _shown, v = opened(name, action)
        laid = [] if action == "restore" else v.laid  # what its question said
        since = cls.generation(tile)

        def run(task: Task) -> None:
            def said(done: int, total: int) -> None:
                task.say("laying", done, total)

            show(
                tile,
                folder,
                laid,
                custom_scenery=hooks.custom_scenery(),
                since=since,
                untick=action == "restore",
                progress=said,
            )

        task = engine.tasks.start(tile.name, action, run, stoppable=False)
        return {"task": task.view()}

    @router.get("/api/clouds/{name}/files/{file}")
    def files(name: str, file: str) -> FileResponse:
        tile = _tile_of(name)
        if not _NAME.match(file):
            raise OsxpError(
                "CLOUD_INVALID",
                context={
                    "tile": tile.name,
                    "cloud": file,
                    "reason": "not a file of the cloud mode",
                },
            )
        path = previews_dir(tile) / file
        if not path.is_file():
            raise OsxpError(
                "CLOUD_INVALID",
                context={"tile": tile.name, "cloud": file, "reason": "no such file"},
            )
        return FileResponse(path, headers={"Cache-Control": "max-age=31536000, immutable"})

    @router.post("/api/clouds/tasks/{task_id}/stop")
    def stop(task_id: str) -> dict[str, Any]:
        return {"stopped": engine.tasks.stop(task_id)}

    return router, engine
