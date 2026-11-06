# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The cloud mode's routes (``docs/specs/api.md``, ``clouds.md`` 6).

``GET /api/clouds`` the Library's column; ``GET /api/clouds/{tile}`` the mode (the tile's state,
its picture, every cloud's row, what Apply would change, its tasks); ``POST .../find`` a search;
``POST .../picks`` a tick or a source (getting the cloud's images when it needs them);
``POST .../outlines`` and ``DELETE .../outlines/{id}`` a cloud outlined by hand;
``POST .../discard``; ``POST .../apply`` (``restore`` to bring back every cloud);
``GET .../files/{name}`` the picture and the previews; ``POST /api/clouds/tasks/{id}/stop``.
"""

from __future__ import annotations

import contextlib
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
from orthostudio.clouds.find import Area, check, find, preview_of, screen
from orthostudio.clouds.laying import parts_dir
from orthostudio.clouds.model import detector_bytes, detector_ready, ensure_detector
from orthostudio.clouds.opencv import ensure_opencv, opencv_bytes, opencv_ready
from orthostudio.clouds.picture import Picture, draw, search_level
from orthostudio.clouds.sources import SourcePieces, other_sources
from orthostudio.clouds.tasks import Task, Tasks
from orthostudio.clouds.tile import TileClouds, read_tile, tile_folder
from orthostudio.errors import OsxpError
from orthostudio.graph import Store
from orthostudio.graph.digest import digest_bytes
from orthostudio.home import (
    data_root_missing,
    default_chunks_root,
    default_store_root,
    default_tiles_root,
    osxp_home,
)
from orthostudio.imagery.grid import tile_to_wgs84
from orthostudio.imagery.providers import Provider, load_registry
from orthostudio.install.library import default_library_path
from orthostudio.model import TileRef
from orthostudio.pipeline.filing import tile_home
from orthostudio.pipeline.pack import read_build_settings, read_manifest

__all__ = ["CloudHooks", "clouds_router"]

_NAME = re.compile(r"^[0-9a-f]{16,128}\.(png|jpg)$")


@dataclass(frozen=True)
class CloudHooks:
    """What the routes ask of the engine: X-Plane's Custom Scenery (``None`` without one), the
    tiles in a build or queued (``tile -> 'running' | 'queued'``), and starting Apply's build of
    a tile with its clouds (the job's id)."""

    custom_scenery: Callable[[], Path | None]
    building: Callable[[], Mapping[str, str]]
    apply: Callable[[TileRef, list[dict[str, Any]]], str]


class _Engine:
    """The tasks and what is read again and again of a tile: its picture."""

    def __init__(self, hooks: CloudHooks) -> None:
        self.hooks = hooks
        self.tasks = Tasks()
        self._pictures: dict[tuple[str, str, int], Picture] = {}
        self._previews: dict[tuple[str, str], tuple[str, list[float]] | None] = {}
        self._lock = threading.Lock()

    def folder(self, tile: TileRef) -> Path:
        home, _gone = tile_home(
            tile,
            atelier=default_tiles_root(),
            custom_scenery=self.hooks.custom_scenery(),
            library_path=default_library_path(),
        )
        return tile_folder(tile, workshop=default_tiles_root(), home=home)

    def held(self, tile: TileRef) -> tuple[Path, TileClouds | None, Path | None]:
        """The tile's folder, what it holds, and its textures artefact's folder."""
        folder = self.folder(tile)
        with Store(default_store_root()) as store:
            held = read_tile(folder, store)
            art = store.path(held.textures_key) if held is not None else None
        return folder, held, art

    def picture(self, tile: TileRef, folder: Path, held: TileClouds, art: Path, zl: int) -> Picture:
        """The tile at ``zl`` as X-Plane shows it once every cloud is unticked: its replaced
        clouds read from their originals (kept the last few, per textures artefact)."""
        key = (tile.name, held.textures_key, zl)
        with self._lock:
            hit = self._pictures.get(key)
        if hit is not None:
            return hit
        p = draw(
            tile,
            zl,
            dsf=folder / tile.dsf_relpath,
            textures=art / "textures",
            originals=art / "clouds" / "originals" / "textures",
        )
        with self._lock:
            if len(self._pictures) > 4:
                self._pictures.pop(next(iter(self._pictures)))
            self._pictures[key] = p
        return p


def previews_dir(tile: TileRef) -> Path:
    return default_chunks_root().parent / "clouds" / tile.name


def _level(tile: TileRef, held: TileClouds) -> int:
    """The map's level: the search's, or the tile's textures' own when coarser."""
    from orthostudio.imagery.grid import parse_texture_name

    return min(search_level(tile), min(parse_texture_name(n).zl for n in held.chunks))


def _picture_file(tile: TileRef, p: Picture, held: TileClouds) -> dict[str, Any]:
    name = digest_bytes(f"{held.textures_key}:{p.zl}:picture-1".encode())[:32] + ".jpg"
    path = previews_dir(tile) / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(p.rgb).save(path, quality=88)
    h, w = p.rgb.shape[:2]
    north, west = tile_to_wgs84(p.x0 / 256, p.y0 / 256, p.zl)
    south, east = tile_to_wgs84((p.x0 + w) / 256, (p.y0 + h) / 256, p.zl)
    return {"name": name, "bounds": [south, west, north, east], "level": p.zl}


def _registry() -> dict[str, Provider]:
    return load_registry()


def clouds_router(hooks: CloudHooks) -> tuple[APIRouter, Tasks]:
    """The routes, and the engine's tasks (for Quit to name and stop them)."""
    engine = _Engine(hooks)
    router = APIRouter()

    def tile_of(name: str) -> TileRef:
        return TileRef.parse(name)

    def state_of(tile: TileRef, folder: Path, held: TileClouds | None) -> str:
        building = hooks.building().get(tile.name)
        try:
            own = read_manifest(folder).provider
        except Exception:
            own = None
        return mode.tile_state(
            mode.TileFacts(
                data_away=data_root_missing() is not None,
                disk_away=not folder.parent.exists(),
                in_build=building == "running",
                queued=building == "queued",
                folder=folder.is_dir(),
                held=held,
                settings=read_build_settings(folder) is not None,
                own_source=own,
                registry=frozenset(_registry()),
            )
        )

    def preview_for(
        tile: TileRef, p: Picture | None, held: TileClouds, art: Path, c: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """The preview After lays for ``c``: its check's for a cloud not held as it is, else made
        again from the held cloud's frozen parts."""
        entry = held.held(c["id"])
        if (entry is not None and entry["source"] == c["source"]) or (
            entry is not None and not c["tick"]
        ):
            key = (held.textures_key, c["id"])
            with engine._lock:
                known = engine._previews.get(key, False)
            if known is False and p is not None:
                path, bounds = preview_of(
                    p,
                    tile,
                    entry,
                    [art / "clouds" / "parts", parts_dir(default_chunks_root())],
                    previews_dir(tile),
                )
                known = (
                    (path.name, list(bounds)) if path is not None and bounds is not None else None
                )
                with engine._lock:
                    engine._previews[key] = known
            return {"name": known[0], "bounds": known[1]} if known else None
        got = (c.get("checks") or {}).get(c["source"]) or {}
        return {"name": got["preview"], "bounds": got["bounds"]} if got.get("preview") else None

    @router.get("/api/clouds/{name}")
    def tile_mode(name: str) -> dict[str, Any]:
        tile = tile_of(name)
        folder, held, art = engine.held(tile)
        state = state_of(tile, folder, held)
        cl = cls.load(tile, held)
        picture = None
        p = None
        if (
            held is not None
            and art is not None
            and held.described
            and state not in ("data_away", "disk_away")
        ):
            p = engine.picture(tile, folder, held, art, _level(tile, held))
            picture = _picture_file(tile, p, held)
        tasks = {t.cloud: t.view() for t in engine.tasks.of(tile.name) if t.cloud}
        previews = (
            {c["id"]: preview_for(tile, p, held, art, c) for c in cl.clouds}
            if held is not None and art is not None
            else {}
        )
        return {
            "tile": tile.name,
            "version": cl.version,
            "state": state,
            "line": mode.words(state),
            "picture": picture,
            "clouds": mode.rows(cl, held, tile, state, tasks, previews),
            "pending": mode.summary(cl, held, tile),
            "search": cl.search,
            "tasks": [t.view() for t in engine.tasks.of(tile.name)],
            "detector": {
                "ready": detector_ready() and opencv_ready(),
                "bytes": detector_bytes() + opencv_bytes(),
            },
        }

    @router.get("/api/clouds")
    def library_column() -> dict[str, Any]:
        out: dict[str, Any] = {}
        for path in sorted((osxp_home() / "clouds").glob("*.json")):
            with contextlib.suppress(Exception):
                tile = TileRef.parse(path.stem)
                folder, held, _art = engine.held(tile)
                cl = cls.load(tile, held)
                out[tile.name] = mode.library_row(
                    state_of(tile, folder, held), mode.summary(cl, held, tile)
                )
        return {"tiles": out}

    def ready_or_refuse(tile: TileRef) -> tuple[Path, TileClouds, Path]:
        folder, held, art = engine.held(tile)
        state = state_of(tile, folder, held)
        if state not in ("ready", "in_build", "queued") or held is None or art is None:
            raise OsxpError("CLOUD_TILE_NOT_READY", context={"tile": tile.name, "state": state})
        return folder, held, art

    def sources_for(tile: TileRef, folder: Path) -> SourcePieces:
        own = read_manifest(folder).provider
        return SourcePieces(default_chunks_root(), other_sources(tile, own, _registry()))

    @router.post("/api/clouds/{name}/find")
    def find_clouds(
        name: str, body: Annotated[dict[str, Any] | None, Body()] = None
    ) -> dict[str, Any]:
        tile = tile_of(name)
        folder, held, art = ready_or_refuse(tile)

        def run(task: Task) -> None:
            ensure_opencv(progress=lambda d, t: task.say("tool", d, t))
            ensure_detector(progress=lambda d, t: task.say("tool", d, t))
            zl = _level(tile, held)
            p = engine.picture(tile, folder, held, art, zl)
            # once more a level finer where the textures are: the small cumulus (B1)
            fine = (
                engine.picture(tile, folder, held, art, zl + 1)
                if max(_texture_zls(held)) > zl
                else None
            )
            areas, classes = find(p, fine, cancel=task.stop, phase=task.say)
            rings = [a.rings(p) for a in areas]
            verdicts = screen(
                p, rings, classes, sources_for(tile, folder), cancel=task.stop, phase=task.say
            )
            found = [_found(a, r, v, p) for a, r, v in zip(areas, rings, verdicts, strict=True)]
            with cls.update(tile, held) as cl:
                cls.merge_search(cl, found, held)
                cl.search = {"level": zl, "found": len(found)}
            _remember(tile, held.textures_key, classes)

        task = engine.tasks.start(tile.name, "search", run)
        return {"task": task.view()}

    @router.post("/api/clouds/{name}/picks")
    def picks(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        tile = tile_of(name)
        folder, held, art = ready_or_refuse(tile)
        with cls.update(tile, held) as cl:
            cid = str(body["id"])
            cls.pick(cl, cid, tick=body.get("tick"), source=body.get("source"))
            c = dict(cl.get(cid))
        # ticked with a source the tile does not hold it with and no images of it got yet
        was = held.held(cid)
        got = ((c.get("checks") or {}).get(c["source"]) or {}).get("entry") if c["source"] else None
        if (
            c["tick"]
            and c["source"]
            and (was is None or was["source"] != c["source"])
            and got is None
        ):
            start_getting(tile, folder, held, art, c)
        return {"version": cls.load(tile, held).version}

    def start_getting(
        tile: TileRef,
        folder: Path,
        held: TileClouds,
        art: Path,
        c: Mapping[str, Any],
        again: bool = False,
    ) -> Task:
        code, cid, rings = c["source"], c["id"], c["rings"]

        def run(task: Task) -> None:
            ensure_opencv(progress=lambda d, t: task.say("tool", d, t))
            ensure_detector(progress=lambda d, t: task.say("tool", d, t))
            p = engine.picture(tile, folder, held, art, _level(tile, held))
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
                classes=_classes(tile, held.textures_key),
                again=again,
                cancel=task.stop,
                phase=task.say,
            )
            with cls.update(tile, held) as cl, contextlib.suppress(OsxpError):
                c2 = cl.get(cid)
                if c2["rings"] == rings:
                    c2.setdefault("checks", {})[code] = {
                        "answer": got.judgement.answer,
                        "fit": got.judgement.fit,
                        "entry": got.entry,
                        "preview": got.preview.name if got.preview else None,
                        "bounds": list(got.bounds) if got.bounds else None,
                    }

        return engine.tasks.start(tile.name, "getting", run, cloud=cid)

    @router.post("/api/clouds/{name}/check")
    def check_again(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        tile = tile_of(name)
        folder, held, art = ready_or_refuse(tile)
        c = cls.load(tile, held).get(str(body["id"]))
        if c["source"]:
            start_getting(tile, folder, held, art, c, again=True)
        return {"ok": True}

    @router.post("/api/clouds/{name}/outlines")
    def outline(name: str, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        tile = tile_of(name)
        folder, held, art = ready_or_refuse(tile)
        ring: list[float] = []
        polygon = [(float(lon), float(lat)) for lon, lat in body["polygon"]]
        for lon, lat in [*polygon, polygon[0]]:
            ring += [lat, lon]
        p = engine.picture(tile, folder, held, art, _level(tile, held))
        with cls.update(tile, held) as cl:
            c = dict(cls.add_outline(cl, [ring], cls.rings_ha([ring]), held))

        def run(task: Task) -> None:
            ensure_opencv(progress=lambda d, t: task.say("tool", d, t))
            ensure_detector(progress=lambda d, t: task.say("tool", d, t))
            classes = _classes(tile, held.textures_key)
            if classes is None:
                classes = np.zeros(p.rgb.shape[:2], np.uint8)
            (v,) = screen(
                p, [[ring]], classes, sources_for(tile, folder), cancel=task.stop, phase=task.say
            )
            best = _best(v)
            with cls.update(tile, held) as cl, contextlib.suppress(OsxpError):
                c2 = cl.get(c["id"])
                c2["screening"] = {k: {"answer": j.answer, "fit": j.fit} for k, j in v.items()}
                c2["best"] = best
                if best is not None:  # drawing it is asking to replace it
                    cls.pick(cl, c["id"], source=best)
            if best is not None:
                start_getting(tile, folder, held, art, {**c, "source": best})

        engine.tasks.start(tile.name, "trying", run, cloud=c["id"])
        return {"id": c["id"]}

    @router.delete("/api/clouds/{name}/outlines/{cid}")
    def remove(name: str, cid: str) -> dict[str, Any]:
        tile = tile_of(name)
        _folder, held, _art = engine.held(tile)
        with cls.update(tile, held) as cl:
            cls.remove_outline(cl, cid)
        return {"ok": True}

    @router.post("/api/clouds/{name}/discard")
    def discard(name: str) -> dict[str, Any]:
        tile = tile_of(name)
        _folder, held, _art = engine.held(tile)
        with cls.update(tile, held) as cl:
            cls.discard(cl, held)
        return {"ok": True}

    @router.post("/api/clouds/{name}/apply")
    def apply(name: str, body: Annotated[dict[str, Any] | None, Body()] = None) -> dict[str, Any]:
        tile = tile_of(name)
        _folder, held, _art = ready_or_refuse(tile)
        with cls.update(tile, held) as cl:
            if (body or {}).get("restore"):
                cls.restore(cl)
            clouds = cls.laid_after(cl, held, tile)
        return {"job": hooks.apply(tile, clouds)}

    @router.get("/api/clouds/{name}/files/{file}")
    def files(name: str, file: str) -> FileResponse:
        tile = tile_of(name)
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

    return router, engine.tasks


_CLASSES: dict[tuple[str, str], np.ndarray] = {}


def _remember(tile: TileRef, key: str, classes: np.ndarray) -> None:
    """The classes the last search read, for a cloud's band to leave others' clouds out."""
    _CLASSES.clear()
    _CLASSES[(tile.name, key)] = classes


def _classes(tile: TileRef, key: str) -> np.ndarray | None:
    return _CLASSES.get((tile.name, key))


def _texture_zls(held: TileClouds) -> list[int]:
    from orthostudio.imagery.grid import parse_texture_name

    return [parse_texture_name(n).zl for n in held.chunks]


def _best(v: Mapping[str, Any]) -> str | None:
    clear = sorted(((j.fit, code) for code, j in v.items() if j.answer == "clear"), reverse=True)
    return clear[0][1] if clear else None


def _found(a: Area, rings: list[list[float]], v: Mapping[str, Any], p: Picture) -> dict[str, Any]:
    return {
        "rings": rings,
        "ha": a.ha(p),
        "shadow": a.shadow,
        "screening": {k: {"answer": j.answer, "fit": j.fit} for k, j in v.items()},
        "best": _best(v),
    }
