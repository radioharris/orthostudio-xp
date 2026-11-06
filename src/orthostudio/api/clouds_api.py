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
from orthostudio.clouds import mode
from orthostudio.clouds.find import check, find, preview, screen
from orthostudio.clouds.laying import folder_lock, show
from orthostudio.clouds.model import detector_bytes, detector_ready, ensure_detector
from orthostudio.clouds.opencv import ensure_opencv, opencv_bytes, opencv_ready
from orthostudio.clouds.picture import Picture, box_bounds, draw, search_level
from orthostudio.clouds.sources import SourcePieces, other_sources
from orthostudio.clouds.tasks import Task, Tasks
from orthostudio.clouds.tile import TileClouds, lists_dir, own_folder, previews_dir, read_tile
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_bytes
from orthostudio.graph.digest import digest_bytes
from orthostudio.home import data_root_missing, default_chunks_root, disk_absent
from orthostudio.imagery.providers import load_registry
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

    def kinds(self, tile: TileRef) -> frozenset[str]:
        """The kinds of the tile's tasks running or waiting."""
        return frozenset(t.kind for t in self.tasks.of(tile.name))

    def read(self, tile: TileRef, folder: Path) -> TileClouds | None:
        """What the tile's folder shows of the clouds last laid."""
        return read_tile(folder, cls.load(tile).laid)

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
        with self._lock:
            for key in [k for k in self._pictures if k[0] == tile.name]:
                del self._pictures[key]
            for key in [k for k in self._classes if k[0] == tile.name]:
                del self._classes[key]

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
    """The routes, and the engine (its tasks for Quit to stop, a deleted tile to forget)."""
    engine = CloudEngine(hooks)
    router = APIRouter()

    def look(tile: TileRef) -> tuple[Path, TileClouds | None, str]:
        """The tile's folder, what it shows and its state; its folder not read while a build
        writes it or waits to (``in_build``, ``queued``)."""
        folder = engine.folder(tile)
        building = hooks.building().get(tile.name)
        shown = None if building else engine.read(tile, folder)
        return folder, shown, state_of(tile, folder, shown, building)

    def state_of(
        tile: TileRef, folder: Path, shown: TileClouds | None, building: str | None
    ) -> str:
        return mode.tile_state(
            mode.TileFacts(
                data_away=data_root_missing() is not None,
                disk_away=disk_absent(folder),  # the build's own rule (``build._homes``)
                in_build=building == "running",
                queued=building == "queued",
                whole=shown is None or shown.whole,
                shown=shown,
                own_source=None if shown is None else shown.provider,
                registry=frozenset(load_registry()),
            )
        )

    def opened(name: str, action: str) -> tuple[TileRef, Path, TileClouds | None, frozenset[str]]:
        """The tile ``name`` for ``action``, its folder, what it shows and what it allows now:
        refused unless it allows ``action`` (``mode.require``)."""
        tile = _tile_of(name)
        folder, shown, state = look(tile)
        mode.require(tile.name, state, action, engine.kinds(tile))
        return tile, folder, shown, mode.possible(state, engine.kinds(tile))

    def ready(shown: TileClouds | None) -> TileClouds:
        assert shown is not None  # a state allowing a task or Apply has a folder of ours
        return shown

    @router.get("/api/clouds/{name}")
    def tile_mode(name: str) -> dict[str, Any]:
        tile = _tile_of(name)
        _folder, shown, state = look(tile)
        cl = cls.load(tile)
        plan = cls.plan(cl, shown, tile)
        counts = mode.summary(cl, shown, plan)
        running = engine.tasks.of(tile.name)
        searching = any(t.kind == "find" for t in running)
        can = mode.possible(state, engine.kinds(tile))
        picture, previews = None, {}
        if shown is not None and mode.readable(state):  # never while a build writes the folder
            p = engine.picture(tile, shown, _level(tile, shown))
            picture = _picture_file(tile, p, shown)
            for entry in plan.laid:  # what After lays: what Apply lays
                got = preview(p, tile, entry, previews_dir(tile), shown.built)
                if got is not None:
                    previews[entry["id"]] = {"name": got[0], "bounds": got[1]}
        tasks = {t.cloud: t.view() for t in running if t.cloud}
        return {
            "tile": tile.name,
            "state": state,
            # something under way: a task of the tile, or a build of it (the page reads again)
            "busy": bool(running) or state in ("in_build", "queued"),
            "line": mode.words(state, cl, searching),
            "allowed": sorted(mode.useful(can, counts, searching)),
            "views": mode.views(counts, picture is not None),
            "texts": mode.texts(tile.name, counts),
            "picture": picture,
            "clouds": mode.rows(cl, shown, plan, can, tasks, previews),
            "tasks": [t.view() for t in running],
            "failed": failed.view() if (failed := engine.tasks.failed(tile.name)) else None,
        }

    @router.get("/api/clouds")
    def library_column() -> dict[str, Any]:
        out: dict[str, Any] = {}
        for path in sorted(lists_dir().glob("*.json")):
            with contextlib.suppress(Exception):
                tile = TileRef.parse(path.stem)
                _folder, shown, state = look(tile)
                cl = cls.load(tile)
                counts = mode.summary(cl, shown, cls.plan(cl, shown, tile))
                out[tile.name] = mode.library_row(state, counts)
        return {"tiles": out}

    def sources_for(tile: TileRef, shown: TileClouds) -> SourcePieces:
        return SourcePieces(
            default_chunks_root(), other_sources(tile, shown.provider, load_registry())
        )

    def images_task(tile: TileRef, cid: str, *, again: bool = False) -> Callable[[], Task] | None:
        """Getting the images of the cloud ``cid``, ready to start, when the tile allows it now
        and its cloud has a source: asked for again (``again``), or its change waits for images
        asking may bring (``mode.asks_images``); ``None`` otherwise. The one way it starts."""
        _folder, maybe, state = look(tile)
        can = mode.possible(state, engine.kinds(tile))
        cl = cls.load(tile)
        c = cl.get(cid)
        if maybe is None or not c["source"] or "check" not in can:
            return None
        if not again and not mode.asks_images(cls.plan(cl, maybe, tile).change_of(cid), can):
            return None
        shown, code, rings = maybe, c["source"], c["rings"]
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

        return lambda: engine.tasks.start(tile.name, "check", run, cloud=cid)

    @router.post("/api/clouds/{name}/find")
    def find_clouds(
        name: str, body: Annotated[dict[str, Any] | None, Body()] = None
    ) -> dict[str, Any]:
        tile, _folder, maybe, _can = opened(name, "find")
        shown = ready(maybe)
        _detector_said(body)  # the search runs it
        since = cls.generation(tile)

        def run(task: Task) -> None:
            _tools(task)
            zl = _level(tile, shown)
            p = engine.picture(tile, shown, zl)
            # once more a level finer where the textures are: the small cumulus (B1)
            finer = max(t.zl for t in shown.textures) > zl
            fine = engine.picture(tile, shown, zl + 1) if finer else None
            areas, classes = find(p, fine, cancel=task.stop, phase=task.say)
            rings = [a.rings(p) for a in areas]
            verdicts = screen(
                p, rings, classes, sources_for(tile, shown), cancel=task.stop, phase=task.say
            )
            found = [_found(r, v) for r, v in zip(rings, verdicts, strict=True)]
            with cls.update(tile, since=since) as cl:
                cls.merge_search(cl, found, shown, {"level": zl, "found": len(found)})
            engine.remember(tile, shown, classes)

        task = engine.tasks.start(tile.name, "find", run)
        return {"task": task.view()}

    @router.post("/api/clouds/{name}/picks")
    def picks(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        action = cls.pick_action(body.get("tick"), body.get("source"))
        tile, _folder, _shown, _can = opened(name, action)
        cid = _cloud_of(tile, body)
        with cls.update(tile) as cl:
            cls.pick(cl, cid, action, body.get("source"))
        start = images_task(tile, cid)
        if start is not None:
            _detector_said(body)  # getting its images runs it: the pick itself is kept
            start()
        return {"ok": True}

    @router.post("/api/clouds/{name}/check")
    def check_again(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        tile, _folder, _shown, _can = opened(name, "check")
        _detector_said(body)
        start = images_task(tile, _cloud_of(tile, body), again=True)
        if start is not None:
            start()
        return {"ok": True}

    @router.post("/api/clouds/{name}/outlines")
    def outline(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        tile, _folder, maybe, _can = opened(name, "outline")
        shown = ready(maybe)
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
            (v,) = screen(
                p, [[ring]], classes, sources_for(tile, shown), cancel=task.stop, phase=task.say
            )
            with cls.update(tile, since=since) as cl:
                cls.record_screening(cl, cid, v)
            start = images_task(tile, cid)
            if start is not None:
                start()

        engine.tasks.start(tile.name, "outline", run, cloud=cid)
        return {"id": cid}

    @router.delete("/api/clouds/{name}/outlines/{cid}")
    def remove(name: str, cid: str) -> dict[str, Any]:
        tile, _folder, shown, _can = opened(name, "remove")
        with cls.update(tile) as cl:
            cls.remove_outline(cl, cid, shown)
        return {"ok": True}

    @router.post("/api/clouds/{name}/discard")
    def discard(name: str) -> dict[str, Any]:
        tile, _folder, shown, _can = opened(name, "discard")
        with cls.update(tile) as cl:
            cls.discard(cl, shown)
        return {"ok": True}

    @router.post("/api/clouds/{name}/apply")
    def apply(name: str, body: Annotated[dict[str, Any] | None, Body()] = None) -> dict[str, Any]:
        action = "restore" if (body or {}).get("restore") else "apply"
        tile, folder, maybe, _can = opened(name, action)
        shown = ready(maybe)
        cl = cls.load(tile)
        # what Apply lays, as its question said it
        laid = [] if action == "restore" else cls.plan(cl, shown, tile).laid
        since = cls.generation(tile)

        def run(task: Task) -> None:
            def said(done: int, total: int) -> None:
                task.say("laying", done, total)

            show(tile, folder, laid, since=since, untick=action == "restore", progress=said)

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


def _found(rings: list[list[float]], v: Mapping[str, Any]) -> dict[str, Any]:
    """A cloud the search found, as the list takes it (``cloudlist.merge_search``)."""
    screening = cls.screening_of(v)
    return {"rings": rings, "screening": screening, "best": cls.best_of({"screening": screening})}
