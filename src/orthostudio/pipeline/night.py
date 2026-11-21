# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The night textures of a tile: its towns' lights, which X-Plane adds to the photo at night.

Live's drawing (:mod:`orthostudio.live.night`), from X-Plane's own roads and NASA's density grid
of the square, at full strength. How bright X-Plane shows them is the ``LUMINANCE`` line of the
terrain files (:data:`orthostudio.live.night.LUMINANCE`), Live's strength: a change of it rewrites
those lines alone (:func:`set_strength`), never the textures.

Two rules: ``tile.night_grid`` finds the square's grid, in Live's folders or downloaded once with
its region's; ``tile.night`` draws a night texture for each texture of the land holding a light.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
from collections.abc import Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from contextvars import ContextVar
from multiprocessing import get_context
from pathlib import Path

from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_bytes
from orthostudio.graph import Rule, RuleParams, RunContext, rule
from orthostudio.model import TileRef
from orthostudio.parentwatch import leave_with_parent
from orthostudio.textures.ter import (
    NIGHT_FOLDER,
    TerKind,
    night_name,
    photo_of,
    ter_kind,
    with_night,
)

log = logging.getLogger(__name__)

ROADS_INPUTS = ("roads", "roads_n", "roads_ne", "roads_e", "roads_se")
ROADS_INPUTS += ("roads_s", "roads_sw", "roads_w", "roads_nw")
"""X-Plane's Global Scenery DSF of the square and of its eight neighbours: a texture on the
square's edge takes the roads across it too."""

ROADS_OFFSETS = {
    "roads": (0, 0),
    "roads_n": (1, 0),
    "roads_ne": (1, 1),
    "roads_e": (0, 1),
    "roads_se": (-1, 1),
    "roads_s": (-1, 0),
    "roads_sw": (-1, -1),
    "roads_w": (0, -1),
    "roads_nw": (1, -1),
}

MAX_WORKERS = 8
BASE_MB = 300
WORKER_MB = 300
"""A drawing process: a 1024 px texture's float copies and a few squares' roads."""

_WORKERS: ContextVar[int] = ContextVar("night_workers", default=1)
_FETCHING = threading.Lock()


@contextlib.contextmanager
def night_job(workers: int) -> Iterator[None]:
    """The processes :func:`_tile_night` may start, for the node that runs it."""
    token = _WORKERS.set(max(1, workers))
    try:
        yield
    finally:
        _WORKERS.reset(token)


class NightGridParams(RuleParams):
    tile: str


class NightParams(RuleParams):
    tile: str


def square_name(lat: int, lon: int) -> str:
    return f"{lat:+03d}{lon:+04d}"


def land_textures(dsf_dir: Path) -> list[str]:
    """The photo of each land terrain file of a DSF artefact, by file name, sorted."""
    names = {
        photo_of(ter.read_text(encoding="ascii", errors="replace"))
        for ter in (Path(dsf_dir) / "terrain").glob("*.ter")
        if ter_kind(ter.name) is TerKind.LAND
    }
    return sorted(n for n in names if n)


# -- the grid ----------------------------------------------------------------------------------


def grid_folders() -> list[Path]:
    """Where Live keeps NASA's grids: its regions' ``night`` folders, then its own."""
    from orthostudio.live import osxpregions
    from orthostudio.live.pack import live_root

    root = live_root()
    regions = sorted(osxpregions.installed(root))
    return [osxpregions.mesh_dir(root, r) / "night" for r in regions] + [root / "night"]


def find_grid(lat: int, lon: int) -> Path | None:
    file = f"{square_name(lat, lon)}.png"
    return next((d / file for d in grid_folders() if (d / file).is_file()), None)


def fetch_grids(region: str, dest: Path) -> int:
    """The night grids of Live's ``region`` downloaded into ``dest``, checked against the host's
    list; returns the grids written."""
    import httpx

    from orthostudio.live import osxpregions
    from orthostudio.live.pack import live_root

    url, token = osxpregions.shipped_host()
    if not url:  # a build from the sources without Live's address: never a tile without its grid
        raise OsxpError("SYS_RESOURCE_MISSING", context={"path": osxpregions.HOST_FILE})
    try:
        regions = osxpregions.catalog(live_root())
    except osxpregions.CatalogError as exc:
        raise _offline(region, str(exc)) from exc
    found = regions.get(region)
    if found is None:
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    written = 0
    with (
        tempfile.TemporaryDirectory(dir=dest, prefix=".fetch-") as tmp,
        osxpregions._client(token) as client,
    ):
        for z in found.night:
            part = Path(tmp) / z.name
            h = hashlib.sha256()
            try:
                with client.stream("GET", f"{url}/{z.path}") as r, open(part, "wb") as out:
                    r.raise_for_status()
                    for chunk in r.iter_bytes(1 << 20):
                        h.update(chunk)
                        out.write(chunk)
            except (OSError, httpx.HTTPError) as exc:
                raise _offline(region, str(exc)) from exc
            if h.hexdigest() != z.sha256:
                raise _offline(region, f"{z.name} came damaged")
            unpacked = Path(tmp) / "unpacked"
            osxpregions.unpack(part, unpacked)
            for png in unpacked.rglob("*.png"):
                os.replace(png, dest / png.name)
                written += 1
    return written


def _offline(region: str, reason: str) -> OsxpError:
    log.warning("the night lights' grids of %s did not come: %s", region, reason)
    return OsxpError("NET_OFFLINE")


def _tile_night_grid(ctx: RunContext) -> None:
    """The square's grid, or an empty file where it has none (the open sea, no region)."""
    from orthostudio.live.aoregions import region_of
    from orthostudio.live.pack import live_root

    params = ctx.params
    assert isinstance(params, NightGridParams)
    tile = TileRef.parse(params.tile)
    with _FETCHING:  # the squares of a batch: their region's grids downloaded once
        found = find_grid(tile.lat, tile.lon)
        if found is None:
            region = region_of(tile.lat + 0.5, tile.lon + 0.5)
            if region is not None:
                fetch_grids(region, live_root() / "night")
                found = find_grid(tile.lat, tile.lon)
    if found is None:
        ctx.out.write_bytes(b"")
    else:
        shutil.copyfile(found, ctx.out)


# -- the night textures ------------------------------------------------------------------------


def _draw(job: tuple[str, str, list[str], str | None]) -> tuple[str, bool]:
    """In a drawing process: the night texture of one texture, at full strength."""
    from orthostudio.live.nightwork import make_night

    pack, name, dsfs, grid = job
    return name, make_night(Path(pack), name, 0, dsfs, grid, 1.0, apart=False)


def _jobs(
    names: Iterable[str], work: Path, roads: dict[tuple[int, int], str], grid: str | None
) -> list[tuple[str, str, list[str], str | None]]:
    from orthostudio.live.evict import tiles_of
    from orthostudio.live.geo import parse_texture_name

    out = []
    for name in names:
        tex = parse_texture_name(name)
        if tex is None:
            continue
        dsfs = [roads[sq] for sq in sorted(tiles_of(tex)) if sq in roads]
        out.append((str(work), name, dsfs, grid))
    return out


def _tile_night(ctx: RunContext) -> None:
    """``textures_night/<texture>_LIT.dds`` of each texture of the land with a light."""
    params = ctx.params
    assert isinstance(params, NightParams)
    tile = TileRef.parse(params.tile)
    roads: dict[tuple[int, int], str] = {}
    for name, (dlat, dlon) in ROADS_OFFSETS.items():
        if ctx.inputs[name].path is not None:
            roads[(tile.lat + dlat, tile.lon + dlon)] = str(ctx.input_path(name))
    grid_file = ctx.input_path("grid")
    grid = None
    if grid_file.stat().st_size:  # named after its square, which the drawing reads
        named = ctx.scratch / f"{square_name(tile.lat, tile.lon)}.png"
        shutil.copyfile(grid_file, named)
        grid = str(named)
    work = ctx.scratch / "night"
    jobs = _jobs(land_textures(ctx.input_path("dsf")), work, roads, grid)
    workers = min(_WORKERS.get(), max(1, len(jobs)))
    if workers == 1:
        results = [_draw(job) for job in jobs]
    else:
        context = get_context("spawn")
        with ProcessPoolExecutor(
            workers, mp_context=context, initializer=leave_with_parent
        ) as pool:
            results = list(pool.map(_draw, jobs, chunksize=4))
    folder = ctx.out / NIGHT_FOLDER
    folder.mkdir()
    for name, lit in results:
        if lit:
            os.replace(work / NIGHT_FOLDER / night_name(name), folder / night_name(name))


TILE_NIGHT_GRID: Rule = rule(
    name="tile.night_grid",
    version=1,
    params=NightGridParams,
    inputs=(),
    ram_mb=100,
    kind="file",
)(_tile_night_grid)
TILE_NIGHT: Rule = rule(
    name="tile.night",
    version=1,
    params=NightParams,
    inputs=("dsf", "grid", *ROADS_INPUTS),
    ram_mb=BASE_MB,
    kind="dir",
)(_tile_night)


# -- the strength --------------------------------------------------------------------------------


def luminance() -> int:
    """The ``LUMINANCE`` Live's night lights ask for now; 0 when they are off."""
    from orthostudio.live.night import LUMINANCE
    from orthostudio.live.settings import SettingsFileError, load_settings

    try:
        settings = load_settings()
    except (OSError, SettingsFileError):
        return LUMINANCE["normal"]
    return LUMINANCE[settings.night_light] if settings.night else 0


def lit_of(text: str, lit: set[str]) -> str | None:
    """The night texture a land terrain file of ``text`` names, when it is among ``lit``."""
    photo = photo_of(text)
    name = night_name(photo) if photo else ""
    return name if name in lit else None


def night_lines(pack_dir: Path, lum: int) -> int:
    """The land terrain files of ``pack_dir`` naming their night texture at ``lum``, those without
    one naming none; returns the files rewritten."""
    terrain, folder = Path(pack_dir) / "terrain", Path(pack_dir) / NIGHT_FOLDER
    lit = {p.name for p in folder.glob("*_LIT.dds")} if folder.is_dir() else set()
    changed = 0
    for ter in sorted(terrain.glob("*.ter")):
        if ter_kind(ter.name) is not TerKind.LAND:
            continue
        text = ter.read_bytes().decode("ascii")
        new = with_night(text, lit_of(text, lit), lum)
        if new != text:
            atomic_write_bytes(ter, new.encode("ascii"))
            changed += 1
    return changed


def set_strength(pack_dirs: Iterable[Path], lum: int) -> int:
    """Live's strength given to every tile of ``pack_dirs`` holding night textures; returns the
    tiles changed. One that cannot be written keeps what it had and the others go on."""
    tiles = 0
    for pack_dir in pack_dirs:
        if not (Path(pack_dir) / NIGHT_FOLDER).is_dir():
            continue
        try:
            tiles += night_lines(pack_dir, lum) > 0
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("the night lights of %s keep their strength: %s", pack_dir, exc)
    return tiles


def library_strength() -> int:
    """Live's strength given to the tiles of the Library; returns the tiles changed."""
    from orthostudio.install import Library

    try:
        with Library() as lib:
            dirs = [e.path for e in lib.list(kind="ortho")]
    except sqlite3.Error as exc:
        log.warning("the Library could not be read: %s", exc)
        return 0
    return set_strength(dirs, luminance())
