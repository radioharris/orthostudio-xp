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
import json
import threading
from collections.abc import Callable, Collection, Iterator, Mapping
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
from orthostudio.clouds.layer import RULE
from orthostudio.clouds.laying import folder_lock, show
from orthostudio.clouds.model import detector_bytes, detector_ready, ensure_detector
from orthostudio.clouds.opencv import ensure_opencv, opencv_bytes, opencv_ready
from orthostudio.clouds.picture import Picture, box_bounds, draw, map_level
from orthostudio.clouds.sources import SourcePieces, other_sources
from orthostudio.clouds.tasks import Task, Tasks
from orthostudio.clouds.tile import (
    TileClouds,
    clouds_dir,
    is_own_folder,
    listed,
    own_folder,
    picture_path,
    read_tile,
    served_path,
)
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_bytes
from orthostudio.home import data_root_missing, default_chunks_root, disk_absent
from orthostudio.imagery.providers import load_registry
from orthostudio.install.library import Library, default_library_path
from orthostudio.model import TileRef

__all__ = ["CloudHooks", "clouds_router"]


@dataclass(frozen=True)
class CloudHooks:
    """What the routes ask of the engine: X-Plane's Custom Scenery (``None`` without one), and
    the tiles whose folder something else holds, each with the state that says it
    (``state.TILE_STATES``: built, waiting in the queue, being filed, being deleted)."""

    custom_scenery: Callable[[], Path | None]
    taken: Callable[[], Mapping[str, str]]


class CloudEngine:
    """The tasks, and what is kept in memory of a tile: its pictures at the map's level, the
    classes its last search read."""

    def __init__(self, hooks: CloudHooks) -> None:
        self.hooks = hooks
        self.tasks = Tasks(taken=hooks.taken)
        self._pictures: dict[tuple[str, str], Picture] = {}
        self._drawing: dict[tuple[str, str], threading.Lock] = {}
        self._classes: dict[tuple[str, str], np.ndarray] = {}
        self._lock = threading.Lock()

    def folder(self, tile: TileRef) -> Path:
        return own_folder(tile, custom_scenery=self.hooks.custom_scenery())

    def picture(self, tile: TileRef, shown: TileClouds, zl: int) -> Picture:
        """Before: the tile at ``zl`` with no cloud replaced, its textures as the build made
        them (:meth:`drawn`), which a search and a cloud's images read."""
        return self.drawn(tile, shown, zl)[1]

    def drawn(
        self, tile: TileRef, shown: TileClouds, zl: int, remade: frozenset[str] | None = None
    ) -> tuple[str, Picture]:
        """The tile at ``zl`` as X-Plane draws it, every texture as X-Plane shows it but those
        Apply makes again (``remade``), as the build made them (After's ground); every one as
        built for Before (``None``). With what it is made of, said in a line, which names it:
        the same line, the same picture. The map's level is kept (the last few); a finer one,
        read once by a search, is not."""
        key = (tile.name, _what(shown, zl, remade))
        with self._lock:
            drawing = self._drawing.setdefault(key, threading.Lock())
        with drawing:  # two answers asking for it at once: one drawing
            with self._lock:
                hit = self._pictures.get(key)
            if hit is not None:
                return key[1], hit
            what, p = _draw(tile, shown.folder, zl, remade)
            with self._lock:
                self._drawing.pop(key, None)
                if zl == map_level(tile, shown.textures):
                    if len(self._pictures) > 4:
                        self._pictures.pop(next(iter(self._pictures)))
                    self._pictures[(tile.name, what)] = p
            return what, p

    def remember(self, tile: TileRef, shown: TileClouds, classes: np.ndarray) -> None:
        """The classes the tile's last search read, for an outline's band to leave others'
        clouds out; one tile's at a time."""
        with self._lock:
            self._classes = {(tile.name, shown.built): classes}

    def classes(self, tile: TileRef, shown: TileClouds) -> np.ndarray | None:
        with self._lock:
            return self._classes.get((tile.name, shown.built))

    def let_go(self, tile: TileRef) -> None:
        """A tile about to be deleted: its tasks ended (an Apply ends what it began before its
        folder goes), what is kept of it in memory dropped."""
        self.tasks.end({tile.name})
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


def _what(shown: TileClouds, zl: int, remade: frozenset[str] | None) -> str:
    """What a picture of the folder ``shown`` is made of, said in a line: the build, the level,
    the way clouds are laid, the clouds each texture drawn as X-Plane shows it shows
    (``state.as_shown``)."""
    laid = state.as_shown(shown, remade)
    return f"{shown.built}:{zl}:{RULE}:{json.dumps(sorted(laid.items()))}"


def _picture_file(tile: TileRef, what: str, p: Picture) -> dict[str, Any]:
    """The picture as the page lays it: a JPEG named by what it is made of, written whole."""
    path = picture_path(tile, what)
    if not path.exists():
        buf = io.BytesIO()
        Image.fromarray(p.rgb).save(buf, "JPEG", quality=88)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, buf.getvalue(), fsync=True)
    h, w = p.rgb.shape[:2]
    return {"name": path.name, "bounds": box_bounds(p, (0, 0, w, h)), "level": p.zl}


def _draw(
    tile: TileRef, folder: Path, zl: int, remade: frozenset[str] | None
) -> tuple[str, Picture]:
    """The tile at ``zl`` from its folder (:meth:`CloudEngine.drawn`), read while none of its
    textures is written: what it is made of is said of what was read then."""
    with folder_lock(tile):
        now = read_tile(folder)
        if now is None:
            raise OsxpError(
                "CLOUD_TILE_NOT_READY", context={"tile": tile.name, "state": "not_built"}
            )
        laid = state.as_shown(now, remade)
        textures = {n: now.texture_path(n) if n in laid else now.plain_path(n) for n in now.names}
        p = draw(tile, zl, dsf=folder / tile.dsf_relpath, textures=textures)
        return _what(now, zl, remade), p


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
    """An outline's ring in the zones' format from ``polygon`` (``[[lon, lat], ...]``): an
    outline on the tile (``cloudlist.outline_problem``)."""
    ring: list[float] = []
    try:
        points = [(float(lon), float(lat)) for lon, lat in (body or {}).get("polygon") or ()]
    except (TypeError, ValueError):
        points = []
    if len(set(points)) >= 3:
        for lon, lat in [*points, points[0]]:
            ring += [lat, lon]
    why = cls.outline_problem([ring], tile) if ring else "an outline needs three points"
    if why is not None:
        raise OsxpError("CLOUD_INVALID", context={"tile": tile.name, "cloud": "", "reason": why})
    return ring


def _detector_said(body: Mapping[str, Any] | None, then: str | None = None) -> None:
    """``CLOUD_DETECTOR_ASK`` while the detector is not on this computer and the user has not
    said to download it (``download``): the page asks, then sends with ``download`` the action
    it says (``then``: a cloud's images, ``check``, after a tick kept), else the same one."""
    if (detector_ready() and opencv_ready()) or (body or {}).get("download"):
        return
    mb = max(1, round((detector_bytes() + opencv_bytes()) / 1e6))
    raise OsxpError("CLOUD_DETECTOR_ASK", context={"mb": mb, "then": then})


def _body_of(tile: TileRef, body: Mapping[str, Any] | None, actions: Collection[str]) -> Any:
    """A request's action (one of ``actions``), its cloud (``id``, none for every cloud) and its
    source (``choose``), each of its kind; ``CLOUD_INVALID`` else."""
    b = body or {}
    action, cid, source = b.get("action"), b.get("id"), b.get("source")
    ok = action in actions and (cid is None or (isinstance(cid, str) and cid))
    ok = ok and (source is None or isinstance(source, str)) and (action != "choose" or source)
    if not ok:
        raise OsxpError(
            "CLOUD_INVALID",
            context={"tile": tile.name, "cloud": str(cid or ""), "reason": "not an action of it"},
        )
    return action, cid, source


def _tools(task: Task) -> None:
    """The detector and OpenCV, downloaded once, before a task runs them."""
    ensure_opencv(progress=lambda d, t: task.say("tool", d, t))
    ensure_detector(progress=lambda d, t: task.say("tool", d, t))


def clouds_router(hooks: CloudHooks) -> tuple[APIRouter, CloudEngine]:
    """The routes, and the engine (its tasks for Quit to stop, a deleted tile to forget). Every
    route reads what the tile and its clouds allow from one view (``state.view``)."""
    engine = CloudEngine(hooks)
    router = APIRouter()

    def look(tile: TileRef) -> tuple[Path, TileClouds | None, state.View]:
        """The tile's folder, what it shows (``None`` in a state its folder is not read in) and
        its view, of its list knowing what the folder shows (``cloudlist.knowing``)."""
        folder = engine.folder(tile)
        st, shown = state.tile_state(
            data_away=data_root_missing() is not None,
            disk_away=disk_absent(folder),  # the build's own rule (``build._homes``)
            taken=hooks.taken().get(tile.name),
            read=lambda: read_tile(folder),
        )
        tasks = [t.view() for t in engine.tasks.of(tile.name)]
        after = engine.tasks.following(tile.name)  # what follows a task: under way still
        view = state.view(cls.knowing(tile, shown), shown, tile, st, tasks, after)
        return folder, shown, view

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
        picture = after = None
        previews = {}
        fine = False
        if shown is not None:
            zl = map_level(tile, shown.textures)
            fine = fine_level(zl, shown.textures) is not None
            what, p = engine.drawn(tile, shown, zl)
            picture = _picture_file(tile, what, p)
            # After: what X-Plane shows, but the textures Apply makes again, which show the
            # build's own with the clouds Apply lays in them, each by its preview there
            after = _picture_file(tile, *engine.drawn(tile, shown, zl, v.remade))
            for entry, names in v.previews:
                got = preview(p, tile, entry, shown.built, names)
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
            "after": after,
            "fine": fine,
            "clouds": [s.page(previews) for s in v.clouds],
            "tasks": v.tasks,
            "failed": failed.view() if failed else None,
        }

    @router.get("/api/clouds")
    def library_column() -> dict[str, Any]:
        """The Library's Clouds cell of each row it is offered on, by the row's path: a tile
        OrthoStudio XP built, the row its own folder. Its words are read of a tile with a list or
        clouds laid in its folder only: no other tile's textures are read."""
        lists = listed()
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
                looked = r.tile.name in lists or clouds_dir(r.path).is_dir()
                out[str(r.path)] = look(r.tile)[2].library() if looked else state.library_cell()
        return {"rows": out}

    def sources_for(tile: TileRef, shown: TileClouds) -> SourcePieces:
        return SourcePieces(
            default_chunks_root(), other_sources(tile, shown.provider, load_registry())
        )

    def get_images(
        tile: TileRef,
        ids: Collection[str] | None,
        body: Mapping[str, Any] | None,
        *,
        again: bool = False,
        unasked: bool = False,
    ) -> None:
        """Getting the images of the clouds ``ids`` (of every cloud: ``None``) whose state offers
        it (``check``), the detector asked for first (``body``): the one way it starts, after a
        tick, a source chosen, an outline's sources tried, the user's Get its images (``again``:
        its source asked again), or a check that turned the cloud to a source never asked
        (``unasked``, ``CloudState.unasked``). Of several, one refused leaves the others go."""
        _folder, shown, v = look(tile)
        asked = [s.cloud["id"] for s in v.clouds] if ids is None else list(ids)
        offered = [i for i in asked if shown is not None and v.allows("check", i)]
        offered = [i for i in offered if not unasked or v.cloud(i).unasked]
        if offered:
            _detector_said(body, then="check")
        for cid in offered:
            try:
                _get_images(tile, shown, v.cloud(cid), again=again)
            except OsxpError:
                if len(offered) == 1:
                    raise

    def _get_images(tile: TileRef, shown: Any, s: state.CloudState, *, again: bool) -> None:
        """:func:`get_images` of the cloud ``s``: its task."""
        cid = s.cloud["id"]
        code, rings = s.source, s.cloud["rings"]
        assert code is not None  # a cloud is offered images of its source only
        since = cls.generation(tile)

        def run(task: Task) -> None:
            _tools(task)
            p = engine.picture(tile, shown, map_level(tile, shown.textures))
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

        def then() -> None:
            """Its source answered worse there: the next one's images, when never asked."""
            get_images(tile, [cid], {"download": True}, unasked=True)

        engine.tasks.start(tile.name, "check", run, cloud=cid, then=then)

    @router.post("/api/clouds/{name}/find")
    def find_clouds(
        name: str, body: Annotated[dict[str, Any] | None, Body()] = None
    ) -> dict[str, Any]:
        tile, _folder, shown, _v = opened(name, "find")
        _detector_said(body)  # the search runs it
        since = cls.generation(tile)
        asked_fine = bool((body or {}).get("fine"))

        def run(task: Task) -> None:
            _tools(task)
            zl = map_level(tile, shown.textures)
            p = engine.picture(tile, shown, zl)
            # the quick search reads the map's level; the fine one a level finer too, where the
            # textures are that fine: it finds the small clouds, in about twice the time (B1)
            finer = fine_level(zl, shown.textures) if asked_fine else None
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

    def pick(
        tile: TileRef,
        ids: Collection[str],
        action: str,
        source: str | None = None,
        body: Mapping[str, Any] | None = None,
        since: int | None = None,
    ) -> None:
        """A tick, an untick or a source kept, of the clouds ``ids``, then the images got of
        those whose state asks them: the one way a cloud is ticked, by the user, Tick all, or once
        an outline's sources are tried. Kept whatever is said of the detector: asked for
        (``CLOUD_DETECTOR_ASK``), the page asks, and on yes asks the images (``then``); on no,
        the cloud waits for them."""
        with cls.update(tile, since=since) as cl:
            for cid in ids:
                cls.pick(cl, cid, action, source)
        get_images(tile, ids, body)

    @router.post("/api/clouds/{name}/picks")
    def picks(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        """``{action, id?, source?}``: ``tick``, ``untick`` or ``choose`` of the cloud ``id``;
        without one, of every cloud whose state allows it (Tick all, Untick all)."""
        action, cid, source = _body_of(_tile_of(name), body, state.DRAFT)
        if cid is None:
            tile, _folder, _shown, v = opened(name, action)
            ids = [s.cloud["id"] for s in v.clouds if v.allows(action, s.cloud["id"])]
        else:
            tile, _folder, _shown, v = opened(name, action, {"id": cid}, source)
            ids = [cid]
        pick(tile, ids, action, source, body)
        return {"ok": True}

    @router.post("/api/clouds/{name}/check")
    def check_again(
        name: str, body: Annotated[dict[str, Any] | None, Body()] = None
    ) -> dict[str, Any]:
        """``{id?}``: the cloud's images got again; without one, those of every cloud whose state
        offers it."""
        _action, cid, _source = _body_of(_tile_of(name), {"action": "check", **(body or {})},
                                         {"check"})  # fmt: skip
        tile, _folder, _shown, _v = opened(name, "check", {"id": cid} if cid else None)
        get_images(tile, [cid] if cid else None, body, again=True)
        return {"ok": True}

    @router.post("/api/clouds/{name}/outlines")
    def outline(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        tile, _folder, shown, _v = opened(name, "outline")
        ring = _ring_of(tile, body)
        _detector_said(body)  # its sources are tried, then its images got
        p = engine.picture(tile, shown, map_level(tile, shown.textures))
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
            """Drawing it is asking to replace it: ticked when its state allows it (the detector
            on this computer: the task got it)."""
            if look(tile)[2].allows("tick", cid):
                pick(tile, [cid], "tick", body={"download": True}, since=since)

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
        """``{action}``: ``apply`` (what the view asks, each cloud's images in the order they are
        wanted, as its question said) or ``restore`` (none)."""
        action, _cid, _source = _body_of(_tile_of(name), body, state.WRITES)
        tile, folder, _shown, v = opened(name, action)
        laid = [] if action == "restore" else v.asked
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

        task = engine.tasks.start(tile.name, action, run)
        return {"task": task.view()}

    @router.get("/api/clouds/{name}/files/{file}")
    def files(name: str, file: str) -> FileResponse:
        tile = _tile_of(name)
        path = served_path(tile, file)
        if path is None:
            raise OsxpError(
                "CLOUD_INVALID",
                context={"tile": tile.name, "cloud": file, "reason": "no such file"},
            )
        return FileResponse(path, headers={"Cache-Control": "max-age=31536000, immutable"})

    @router.post("/api/clouds/tasks/{task_id}/stop")
    def stop(task_id: str) -> dict[str, Any]:
        return {"stopped": engine.tasks.stop(task_id)}

    return router, engine
