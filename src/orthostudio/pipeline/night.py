# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The night textures of a tile: its towns' lights, which X-Plane adds to the photo at night.

Drawn as Live draws them (:mod:`orthostudio.live.night`), at full strength, a night texture for
each photo of the land holding a light. The terrain files name them at Live's strength, their
``LUMINANCE`` line (:data:`orthostudio.live.night.LUMINANCE`): a change of it rewrites those
lines alone (:func:`set_strength`), never the textures.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from contextvars import ContextVar
from multiprocessing import get_context
from pathlib import Path
from typing import Any

from orthostudio.dem.rule import xp12_dsfs_of
from orthostudio.dem.xplane import XP12_INPUTS
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_bytes
from orthostudio.graph import Rule, RuleParams, RunContext, rule
from orthostudio.home import data_root
from orthostudio.model import TileRef
from orthostudio.parentwatch import leave_with_parent
from orthostudio.pipeline.pack import MANIFEST_NAME, read_manifest, write_manifest
from orthostudio.textures.ter import (
    NIGHT_FOLDER,
    TerKind,
    night_name,
    photo_of,
    ter_kind,
    with_night_of,
)

log = logging.getLogger(__name__)

MAX_WORKERS = 8
BASE_MB = 300
WORKER_MB = 300
"""A drawing process: a 1024 px texture's float copies; the roads are read apart."""
BUDGET_SHARE = 2
"""The night of a tile takes at most half of the RAM budget, as the masks do."""

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


class NightParams(RuleParams):
    tile: str


def land_textures(dsf_dir: Path) -> list[str]:
    """The photo of each land terrain file of a DSF artefact, by file name, sorted."""
    names = {
        photo_of(ter.read_text(encoding="ascii", errors="replace"))
        for ter in (Path(dsf_dir) / "terrain").glob("*.ter")
        if ter_kind(ter.name) is TerKind.LAND
    }
    return sorted(n for n in names if n)


# -- the grid ----------------------------------------------------------------------------------


def grids_folder() -> Path:
    """Where the grids Live has not got are downloaded, in the data folder."""
    return data_root() / "night"


def find_grid(tile: TileRef) -> Path | None:
    """The square's grid among Live's (its regions', its own) and those downloaded here."""
    from orthostudio.live import osxpregions
    from orthostudio.live.nightwork import grid_dirs
    from orthostudio.live.pack import live_root

    root = live_root()
    regions = [osxpregions.mesh_dir(root, r) for r in sorted(osxpregions.installed(root))]
    file = f"{tile.name}.png"
    dirs = [*grid_dirs(regions), grids_folder()]
    return next((d / file for d in dirs if (d / file).is_file()), None)


def grid_of(tile: TileRef) -> Path | None:
    """The square's grid; where none is here, its region's downloaded once into the data folder;
    None for a square without one (the open sea)."""
    from orthostudio.live import osxpregions
    from orthostudio.live.aoregions import InstallError
    from orthostudio.live.meshmap import region_of

    with _FETCHING:  # the squares of a batch: their region's grids downloaded once
        found = find_grid(tile)
        region = region_of(tile.lat + 0.5, tile.lon + 0.5)
        folder = grids_folder()
        done = folder / f".{region}.done"  # its region's grids are all there
        if found is None and region is not None and not done.exists():
            try:
                osxpregions.night_grids(region, folder)
                done.touch()
            except (InstallError, osxpregions.CatalogError, OSError) as exc:
                raise OsxpError(
                    "TEX_NIGHT_GRIDS_FAILED", context={"region": region, "reason": str(exc)}
                ) from exc
            found = find_grid(tile)
    return found


# -- the night textures ---------------------------------------------------------------------------


def _draw(job: tuple[str, str, list[str], str | None]) -> tuple[str, bool]:
    """In a drawing process: the night texture of one texture, at full strength, the roads read in
    a process of their own."""
    from orthostudio.live.nightwork import make_night

    pack, name, dsfs, grid = job
    return name, make_night(Path(pack), name, 0, dsfs, grid, 1.0, apart=True)


def _tile_night(ctx: RunContext) -> None:
    """``textures_night/<texture>_LIT.dds`` of each texture of the land with a light."""
    from orthostudio.live.evict import tiles_of
    from orthostudio.live.geo import parse_texture_name

    params = ctx.params
    assert isinstance(params, NightParams)
    tile = TileRef.parse(params.tile)
    roads = xp12_dsfs_of(ctx, tile)
    grid = grid_of(tile)
    work = ctx.scratch / "night"
    jobs = []
    for name in land_textures(ctx.input_path("dsf")):
        tex = parse_texture_name(name)
        if tex is not None:
            dsfs = [str(roads[sq]) for sq in sorted(tiles_of(tex)) if sq in roads]
            jobs.append((str(work), name, dsfs, str(grid) if grid else None))
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


TILE_NIGHT: Rule = rule(
    name="tile.night",
    version=1,
    params=NightParams,
    inputs=("dsf", *XP12_INPUTS),
    ram_mb=BASE_MB,
    kind="dir",
)(_tile_night)


# -- the strength --------------------------------------------------------------------------------


def luminance(settings: Any = None) -> int:
    """The ``LUMINANCE`` Live's night lights ask for (its settings, read when not given); 0 when
    they are off."""
    from orthostudio.live.night import LUMINANCE
    from orthostudio.live.settings import load_settings

    settings = load_settings() if settings is None else settings
    return LUMINANCE[settings.night_light] if settings.night else 0


def _night_lines(pack_dir: Path, lum: int) -> bool:
    """The land terrain files of ``pack_dir`` naming their night texture at ``lum``, and its
    manifest saying so (its date kept: the Library's « built on »); True when one changed."""
    folder = Path(pack_dir) / NIGHT_FOLDER
    lit = {p.name for p in folder.glob("*_LIT.dds")}
    changed = False
    for ter in sorted((Path(pack_dir) / "terrain").glob("*.ter")):
        text = ter.read_bytes().decode("ascii")
        new = with_night_of(text, ter_kind(ter.name), lit, lum)
        if new != text:
            atomic_write_bytes(ter, new.encode("ascii"))
            changed = True
    with contextlib.suppress(OSError, ValueError, KeyError):
        manifest = read_manifest(pack_dir)
        if manifest.files.get("night") and manifest.files.get("night_lum") != lum:
            when = (Path(pack_dir) / MANIFEST_NAME).stat()
            manifest.files["night_lum"] = lum
            write_manifest(pack_dir, TileRef.parse(manifest.tile), manifest)
            os.utime(Path(pack_dir) / MANIFEST_NAME, ns=(when.st_atime_ns, when.st_mtime_ns))
    return changed


def set_strength(pack_dirs: Iterable[Path], lum: int) -> int:
    """Live's strength given to every tile of ``pack_dirs`` holding night textures; returns the
    tiles changed. One that cannot be written keeps what it had and the others go on."""
    tiles = 0
    for pack_dir in pack_dirs:
        if not (Path(pack_dir) / NIGHT_FOLDER).is_dir():
            continue
        try:
            tiles += _night_lines(pack_dir, lum)
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("the night lights of %s keep their strength: %s", pack_dir, exc)
    return tiles


def library_strength(lum: int) -> int:
    """``lum`` given to the tiles of the Library; returns the tiles changed."""
    from orthostudio.install import Library

    try:
        with Library() as lib:
            dirs = [e.path for e in lib.list(kind="ortho")]
    except sqlite3.Error as exc:
        log.warning("the Library could not be read: %s", exc)
        return 0
    return set_strength(dirs, lum)
