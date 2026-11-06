# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The cloud mode's routes (``docs/specs/api.md``, ``clouds.md`` 6).

``GET /api/clouds`` the Library's column; ``GET /api/clouds/{tile}`` the mode (the tile's state,
its picture, every cloud's row, what Apply would change, its tasks); ``POST .../find`` a search;
``POST .../picks`` a tick or a source (getting the cloud's images when it needs them);
``POST .../outlines`` and ``DELETE .../outlines/{id}`` a cloud outlined by hand;
``POST .../discard``; ``POST .../apply`` (``restore`` to bring back every cloud), a task;
``GET .../files/{name}`` the picture and the previews; ``POST /api/clouds/tasks/{id}/stop``.
"""

from __future__ import annotations

import contextlib
import io
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, Body
from fastapi.responses import FileResponse
from PIL import Image

from orthostudio.clouds import cloudlist as cls
from orthostudio.clouds import mode
from orthostudio.clouds.find import Area, check, find, preview, screen
from orthostudio.clouds.laying import folder_lock, show
from orthostudio.clouds.model import detector_bytes, detector_ready, ensure_detector
from orthostudio.clouds.opencv import ensure_opencv, opencv_bytes, opencv_ready
from orthostudio.clouds.picture import Picture, draw, search_level
from orthostudio.clouds.sources import SourcePieces, other_sources
from orthostudio.clouds.tasks import Task, Tasks
from orthostudio.clouds.tile import TileClouds, got_parts_dir, own_folder, previews_dir, read_tile
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_bytes
from orthostudio.graph.digest import digest_bytes
from orthostudio.home import (
    data_root_missing,
    default_chunks_root,
    disk_absent,
    osxp_home,
)
from orthostudio.imagery.grid import tile_to_wgs84
from orthostudio.imagery.providers import Provider, load_registry
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import pack_is_intact, read_manifest

__all__ = ["CloudHooks", "clouds_router"]

_NAME = re.compile(r"^[0-9a-f]{16,128}\.(png|jpg)$")


@dataclass(frozen=True)
class CloudHooks:
    """What the routes ask of the engine: X-Plane's Custom Scenery (``None`` without one), and
    the tiles in a build or queued (``tile -> 'running' | 'queued'``)."""

    custom_scenery: Callable[[], Path | None]
    building: Callable[[], Mapping[str, str]]


class CloudEngine:
    """The tasks and what is read again and again of a tile: its picture."""

    def __init__(self, hooks: CloudHooks) -> None:
        self.hooks = hooks
        self.tasks = Tasks()
        self._pictures: dict[tuple[str, str, int], Picture] = {}
        self._drawing: dict[tuple[str, str, int], threading.Lock] = {}
        self._lock = threading.Lock()

    def folder(self, tile: TileRef) -> Path:
        return own_folder(tile, custom_scenery=self.hooks.custom_scenery())

    def let_go(self, tile: TileRef) -> None:
        """A tile about to be deleted: its tasks stopped, what is kept of it in memory dropped."""
        for t in self.tasks.of(tile.name):
            self.tasks.stop(t.id)
        with self._lock:
            for key in [k for k in self._pictures if k[0] == tile.name]:
                del self._pictures[key]

    def kinds(self, tile: TileRef) -> frozenset[str]:
        """The kinds of the tile's tasks running or waiting."""
        return frozenset(t.kind for t in self.tasks.of(tile.name))

    def held(self, tile: TileRef) -> tuple[Path, TileClouds | None]:
        """The tile's folder and what it shows of the clouds last applied."""
        folder = self.folder(tile)
        return folder, read_tile(folder, cls.load(tile).laid)

    def picture(self, tile: TileRef, held: TileClouds, zl: int) -> Picture:
        """The tile at ``zl`` as X-Plane shows it once every cloud is unticked: its textures as
        the build made them (kept the last few, per build)."""
        key = (tile.name, held.shown, zl)
        with self._lock:
            drawing = self._drawing.setdefault(key, threading.Lock())
        with drawing:  # two answers asking for it at once: one drawing
            with self._lock:
                hit = self._pictures.get(key)
            if hit is not None:
                return hit
            p = _draw(tile, held, zl)
            with self._lock:
                if len(self._pictures) > 4:
                    self._pictures.pop(next(iter(self._pictures)))
                self._pictures[key] = p
                self._drawing.pop(key, None)
            return p


def _level(tile: TileRef, held: TileClouds) -> int:
    """The map's level: the search's, or the tile's textures' own when coarser."""

    return min(search_level(tile), min(t.zl for t in held.textures))


def _picture_file(tile: TileRef, p: Picture, held: TileClouds) -> dict[str, Any]:
    """The picture as the page lays it: a JPEG named by what the folder shows, written whole."""
    name = digest_bytes(f"{held.shown}:{p.zl}:picture-1".encode())[:32] + ".jpg"
    path = previews_dir(tile) / name
    if not path.exists():
        buf = io.BytesIO()
        Image.fromarray(p.rgb).save(buf, "JPEG", quality=88)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, buf.getvalue())
    h, w = p.rgb.shape[:2]
    north, west = tile_to_wgs84(p.x0 / 256, p.y0 / 256, p.zl)
    south, east = tile_to_wgs84((p.x0 + w) / 256, (p.y0 + h) / 256, p.zl)
    return {"name": name, "bounds": [south, west, north, east], "level": p.zl}


def _registry() -> dict[str, Provider]:
    return load_registry()


def _draw(tile: TileRef, held: TileClouds, zl: int) -> Picture:
    """The tile at ``zl`` from its folder, its textures as the build made them: read while none
    of them is written, which ones show clouds read then."""
    with folder_lock(tile):
        now = read_tile(held.folder) or held
        return draw(tile, zl, dsf=held.folder / tile.dsf_relpath, textures=now.plains())


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


def clouds_router(hooks: CloudHooks) -> tuple[APIRouter, CloudEngine]:
    """The routes, and the engine (its tasks for Quit to stop, a deleted tile to forget)."""
    engine = CloudEngine(hooks)
    router = APIRouter()

    def state_of(tile: TileRef, folder: Path, held: TileClouds | None) -> str:
        building = hooks.building().get(tile.name)
        try:
            manifest = read_manifest(folder)
            own, whole = manifest.provider, pack_is_intact(folder, manifest, with_overlay=False)
        except Exception:
            own, whole = None, True
        return mode.tile_state(
            mode.TileFacts(
                data_away=data_root_missing() is not None,
                disk_away=disk_absent(folder),  # the build's own rule (``build._homes``)
                in_build=building == "running",
                queued=building == "queued",
                whole=whole,
                held=held,
                own_source=own,
                registry=frozenset(_registry()),
            )
        )

    def opened(
        name: str, action: str, body: Mapping[str, Any] | None = None
    ) -> tuple[TileRef, Path, TileClouds | None, str]:
        """The tile ``name`` for ``action``, its folder, what it holds and its state: refused
        unless the tile allows it now (``mode.require``)."""
        tile = _tile_of(name)
        folder, held = engine.held(tile)
        state = state_of(tile, folder, held)
        mode.require(tile.name, state, action, engine.kinds(tile))
        return tile, folder, held, state

    def ready(held: TileClouds | None) -> TileClouds:
        assert held is not None  # a state allowing a task or Apply has a folder of ours
        return held

    @router.get("/api/clouds/{name}")
    def tile_mode(name: str) -> dict[str, Any]:
        tile = _tile_of(name)
        folder, held = engine.held(tile)
        state = state_of(tile, folder, held)
        cl = cls.load(tile)
        pending = cls.changes(cl, held, tile)
        counts = mode.summary(cl, held, pending)
        running = engine.tasks.of(tile.name)
        searching = any(t.kind == "search" for t in running)
        can = mode.possible(state, engine.kinds(tile))
        picture, previews = None, {}
        if held is not None and mode.readable(state):  # never while a build writes the folder
            p = engine.picture(tile, held, _level(tile, held))
            picture = _picture_file(tile, p, held)
            folders = [got_parts_dir(default_chunks_root())]
            for entry in cls.laid_after(cl, held, pending):  # what After lays: what Apply lays
                got = preview(p, tile, entry, folders, previews_dir(tile), held.shown)
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
            "clouds": mode.rows(cl, held, pending, can, tasks, previews),
            "pending": counts,
            "search": cl.search,
            "tasks": [t.view() for t in running],
            "failed": failed.view() if (failed := engine.tasks.failed(tile.name)) else None,
        }

    @router.get("/api/clouds")
    def library_column() -> dict[str, Any]:
        out: dict[str, Any] = {}
        for path in sorted((osxp_home() / "clouds").glob("*.json")):
            with contextlib.suppress(Exception):
                tile = TileRef.parse(path.stem)
                folder, held = engine.held(tile)
                cl = cls.load(tile)
                pending = cls.changes(cl, held, tile)
                out[tile.name] = mode.library_row(
                    state_of(tile, folder, held), mode.summary(cl, held, pending)
                )
        return {"tiles": out}

    def sources_for(tile: TileRef, folder: Path) -> SourcePieces:
        own = read_manifest(folder).provider
        return SourcePieces(default_chunks_root(), other_sources(tile, own, _registry()))

    @router.post("/api/clouds/{name}/find")
    def find_clouds(
        name: str, body: Annotated[dict[str, Any] | None, Body()] = None
    ) -> dict[str, Any]:
        tile, folder, maybe, _state = opened(name, "find", body)
        held = ready(maybe)
        _detector_said(body)  # the search runs it
        since = cls.generation(tile)

        def run(task: Task) -> None:
            ensure_opencv(progress=lambda d, t: task.say("tool", d, t))
            ensure_detector(progress=lambda d, t: task.say("tool", d, t))
            zl = _level(tile, held)
            p = engine.picture(tile, held, zl)
            # once more a level finer where the textures are: the small cumulus (B1)
            fine = _draw(tile, held, zl + 1) if max(_texture_zls(held)) > zl else None
            areas, classes = find(p, fine, cancel=task.stop, phase=task.say)
            rings = [a.rings(p) for a in areas]
            verdicts = screen(
                p, rings, classes, sources_for(tile, folder), cancel=task.stop, phase=task.say
            )
            found = [_found(a, r, v, p) for a, r, v in zip(areas, rings, verdicts, strict=True)]
            with cls.update(tile, since=since) as cl:
                cls.merge_search(cl, found, held, {"level": zl, "found": len(found)})
            _remember(tile, held.shown, classes)

        task = engine.tasks.start(tile.name, "search", run)
        return {"task": task.view()}

    @router.post("/api/clouds/{name}/picks")
    def picks(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        action = "untick" if body.get("tick") is False else "choose" if "source" in body else "tick"
        tile, folder, held, state = opened(name, action, body)
        cid = _cloud_of(tile, body)
        with cls.update(tile) as cl:
            cls.pick(cl, cid, tick=body.get("tick"), source=body.get("source"))
            c = dict(cl.get(cid))
            change = next((ch for ch in cls.changes(cl, held, tile) if ch.cloud == cid), None)
        can = mode.possible(state, engine.kinds(tile))
        if held is not None and cls.needs_images(change) and "check" in can:
            _detector_said(body)  # getting its images runs it: the pick itself is kept
            start_getting(tile, folder, held, c)
        return {"ok": True}

    def start_getting(
        tile: TileRef,
        folder: Path,
        held: TileClouds,
        c: Mapping[str, Any],
        again: bool = False,
    ) -> Task:
        code, cid, rings = c["source"], c["id"], c["rings"]
        since = cls.generation(tile)

        def run(task: Task) -> None:
            ensure_opencv(progress=lambda d, t: task.say("tool", d, t))
            ensure_detector(progress=lambda d, t: task.say("tool", d, t))
            p = engine.picture(tile, held, _level(tile, held))
            registry = _registry()
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
                held,
                SourcePieces(default_chunks_root(), {code: registry[code]}),
                chunks_root=default_chunks_root(),
                previews=previews_dir(tile),
                classes=_classes(tile, held.shown),
                again=again,
                cancel=task.stop,
                phase=task.say,
            )
            with cls.update(tile, since=since) as cl:
                cls.record_check(cl, cid, code, rings, got)

        return engine.tasks.start(tile.name, "getting", run, cloud=cid)

    @router.post("/api/clouds/{name}/check")
    def check_again(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        tile, folder, maybe, _state = opened(name, "check", body)
        held = ready(maybe)
        _detector_said(body)
        c = cls.load(tile).get(_cloud_of(tile, body))
        if c["source"]:
            start_getting(tile, folder, held, c, again=True)
        return {"ok": True}

    @router.post("/api/clouds/{name}/outlines")
    def outline(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        tile, folder, maybe, _state = opened(name, "outline", body)
        held = ready(maybe)
        ring = _ring_of(tile, body)
        _detector_said(body)  # its sources are tried, then its images got
        p = engine.picture(tile, held, _level(tile, held))
        with cls.update(tile) as cl:
            c = dict(cls.add_outline(cl, [ring], cls.rings_ha([ring]), held))
        since = cls.generation(tile)

        def run(task: Task) -> None:
            ensure_opencv(progress=lambda d, t: task.say("tool", d, t))
            ensure_detector(progress=lambda d, t: task.say("tool", d, t))
            classes = _classes(tile, held.shown)
            if classes is None:
                classes = np.zeros(p.rgb.shape[:2], np.uint8)
            (v,) = screen(
                p, [[ring]], classes, sources_for(tile, folder), cancel=task.stop, phase=task.say
            )
            with cls.update(tile, since=since) as cl:
                cls.record_screening(cl, c["id"], v)
                best = next((x["best"] for x in cl.clouds if x["id"] == c["id"]), None)
            if best is not None:
                start_getting(tile, folder, held, {**c, "source": best})

        engine.tasks.start(tile.name, "trying", run, cloud=c["id"])
        return {"id": c["id"]}

    @router.delete("/api/clouds/{name}/outlines/{cid}")
    def remove(name: str, cid: str) -> dict[str, Any]:
        tile, _folder, held, _state = opened(name, "remove")
        with cls.update(tile) as cl:
            cls.remove_outline(cl, cid, held)
        return {"ok": True}

    @router.post("/api/clouds/{name}/discard")
    def discard(name: str) -> dict[str, Any]:
        tile, _folder, held, _state = opened(name, "discard")
        with cls.update(tile) as cl:
            cls.discard(cl, held)
        return {"ok": True}

    @router.post("/api/clouds/{name}/apply")
    def apply(name: str, body: Annotated[dict[str, Any] | None, Body()] = None) -> dict[str, Any]:
        restore = bool((body or {}).get("restore"))
        tile, folder, maybe, _state = opened(name, "restore" if restore else "apply")
        held = ready(maybe)
        cl = cls.load(tile)
        # what Apply lays, as its question said it
        laid = [] if restore else cls.laid_after(cl, held, cls.changes(cl, held, tile))
        since = cls.generation(tile)

        def run(task: Task) -> None:
            def said(done: int, total: int) -> None:
                task.say("laying", done, total)

            show(tile, folder, laid, since=since, untick=restore, progress=said)

        task = engine.tasks.start(tile.name, "applying", run, stoppable=False)
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


_CLASSES: dict[tuple[str, str], np.ndarray] = {}


def _remember(tile: TileRef, key: str, classes: np.ndarray) -> None:
    """The classes the last search read, for a cloud's band to leave others' clouds out."""
    _CLASSES.clear()
    _CLASSES[(tile.name, key)] = classes


def _classes(tile: TileRef, key: str) -> np.ndarray | None:
    return _CLASSES.get((tile.name, key))


def _texture_zls(held: TileClouds) -> list[int]:

    return [t.zl for t in held.textures]


def _found(a: Area, rings: list[list[float]], v: Mapping[str, Any], p: Picture) -> dict[str, Any]:
    return {
        "rings": rings,
        "ha": a.ha(p),
        "shadow": a.shadow,
        "screening": (screening := cls.screening_of(v)),
        "best": cls.best_of({"screening": screening}),
    }
