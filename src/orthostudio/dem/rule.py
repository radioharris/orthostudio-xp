"""Rule ``orthostudio.dem@1``: the tile elevation raster as a keyed ``kind=dir`` artefact.

Spec: ``docs/specs/dem.md`` section 9.

The rule is a graph root: its real input is external state (the elevation files on disk), so it is
keyed by its params alone. Everything the computation needs beyond those params -- where the
elevation files live, how to reach the network, the negative memo -- comes from a :class:`DemJob`
bound with :func:`dem_job`, the same pattern as the masks and OSM rules.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from orthostudio.dem.dem import Dem
from orthostudio.dem.raster import FillNodata
from orthostudio.dem.sources import (
    DownloadFn,
    EnsureOptions,
    NegativeMemo,
    default_elevation_dir,
    default_memo_path,
    http_download,
    no_download,
)
from orthostudio.dem.xplane import XP12_INPUTS
from orthostudio.errors import OsxpError
from orthostudio.graph import Rule, RuleParams, RunContext, rule
from orthostudio.model import TileRef

__all__ = ["DEM_RULE", "DemJob", "DemParams", "build_dem", "dem_job"]


class DemParams(RuleParams):
    """Exactly what the elevation computation consumes -- nothing else enters the key."""

    tile: str = ""
    """``"+43+005"``; the raster depends on it. ``dem_job`` fills it when left empty."""

    custom_dem: str = ""
    """Ortho4XP's ``custom_dem`` (``O4_Config_Utils.py``): empty, a source name, a file path, or
    ``"base;overlay1;overlay2"``. ``{latlon}`` expands to ``N43E005``. OrthoStudio XP adds the
    source ``"XP12"``, X-Plane 12's own relief, which the pipeline writes here by default (decision
    0007): an empty value keeps Ortho4XP's meaning (``<cell>.tif`` if present, else ``View``)."""

    fill_nodata: bool = True
    """Ortho4XP's boolean: True = nearest neighbour, False = every void to 0 m (spec section 7)."""

    dem1_local_fallback: bool = False
    """osxp-only, TODO (spec 9.1, blocker B2): reuse a local 3" file when the 1" archive is
    unavailable. Off by default: turning it on changes ``Data<tile>.alt`` and the mesh."""

    @property
    def fill(self) -> FillNodata:
        return "nearest" if self.fill_nodata else "zero"


@dataclass(slots=True)
class DemJob:
    """Where the elevation files live and how (or whether) to download the missing ones."""

    tile: TileRef
    elevation_dir: Path = field(default_factory=default_elevation_dir)
    download: DownloadFn | None = None
    """``None`` means :func:`orthostudio.dem.sources.http_download`; pass :func:`no_download` to
    forbid the network, or a stub in tests."""
    memo_path: Path | None = field(default_factory=default_memo_path)
    memo_ttl_s: float | None = None
    on_event: Callable[[OsxpError], None] | None = None
    cancel: threading.Event | None = None
    global_scenery_dir: Path | None = None
    """X-Plane 12 Global Scenery, for ``custom_dem = "XP12"`` when :func:`build_dem` is called
    directly. The rule ignores it and reads only the DSFs of its inputs."""

    def options(self, *, dem1_local_fallback: bool = False) -> EnsureOptions:
        """The :class:`EnsureOptions` this job implies."""
        return EnsureOptions(
            elevation_dir=self.elevation_dir,
            download=self.download if self.download is not None else http_download,
            memo=NegativeMemo(self.memo_path, ttl_s=self.memo_ttl_s),
            dem1_local_fallback=dem1_local_fallback,
            cancel=self.cancel,
        )


_JOB: ContextVar[DemJob | None] = ContextVar("osxp_dem_job", default=None)


@contextlib.contextmanager
def dem_job(job: DemJob) -> Iterator[DemJob]:
    """Bind ``job`` for the ``orthostudio.dem`` rules run inside the block."""
    token = _JOB.set(job)
    try:
        yield job
    finally:
        _JOB.reset(token)


def _job() -> DemJob:
    job = _JOB.get()
    if job is None:
        raise RuntimeError("rule orthostudio.dem needs a job: use dem_job(DemJob(...))")
    return job


def build_dem(
    params: DemParams, job: DemJob, *, xp12_dsfs: Mapping[tuple[int, int], Path] | None = None
) -> Dem:
    """The pure(ish) function behind the rule: build the raster of one tile.

    Exposed separately so a caller that wants the object rather than the artefact (the CLI,
    a notebook, the airport smoothing of P4) does not have to go through the store.
    ``xp12_dsfs`` (``(lat, lon) -> DSF``) is what the rule passes; without it the X-Plane
    relief is looked up under ``job.global_scenery_dir``.
    """
    tile = TileRef.parse(params.tile) if params.tile else job.tile
    if params.tile and job.tile is not None and tile != job.tile:
        raise ValueError(f"params.tile is {params.tile!r} but the job builds {job.tile.name!r}")
    opts = job.options(dem1_local_fallback=params.dem1_local_fallback)
    if xp12_dsfs is not None:
        opts.xp12_dsfs = dict(xp12_dsfs)
    else:
        opts.global_scenery_dir = job.global_scenery_dir
    try:
        return Dem.build(
            tile,
            opts,
            custom_dem=params.custom_dem,
            fill_nodata=params.fill,
            on_event=job.on_event,
        )
    finally:
        opts.memo.save()


def xp12_dsfs_of(ctx: RunContext, tile: TileRef) -> dict[tuple[int, int], Path]:
    """``(lat, lon) -> DSF`` of the present ``xp12*`` inputs of a run of the rule."""
    out: dict[tuple[int, int], Path] = {}
    for name, (dlat, dlon) in XP12_INPUTS.items():
        inp = ctx.inputs.get(name)
        if inp is not None and inp.present and inp.path is not None:
            cell = tile.neighbour(dlat, dlon)
            out[(cell.lat, cell.lon)] = inp.path
    return out


@rule(
    name="orthostudio.dem",
    version=1,
    params=DemParams,
    inputs=tuple(XP12_INPUTS),
    kind="dir",
    ram_mb=600,
)
def _dem_rule(ctx: RunContext) -> None:
    """Elevation raster of one tile: ``Data<tile>.alt``, ``dem.npy``, ``meta.json``.

    The nine ``xp12*`` inputs are the Global Scenery DSFs of the 3x3 block when the relief is
    X-Plane's (``custom_dem = "XP12"``), all absent otherwise: the root of the graph is keyed
    by its params alone for the files under ``Elevation_data/``, by content for X-Plane's.
    """
    params = ctx.params
    assert isinstance(params, DemParams)
    job = _job()
    tile = TileRef.parse(params.tile) if params.tile else job.tile
    dem = build_dem(params, job, xp12_dsfs=xp12_dsfs_of(ctx, tile))
    dem.save(ctx.out)


DEM_RULE: Rule = _dem_rule
"""``orthostudio.dem@1``."""


def dem_params(
    tile: TileRef,
    *,
    custom_dem: str = "",
    fill_nodata: bool = True,
    dem1_local_fallback: bool = False,
) -> DemParams:
    """Params for one tile, with the tile name filled in."""
    return DemParams(
        tile=tile.name,
        custom_dem=custom_dem,
        fill_nodata=fill_nodata,
        dem1_local_fallback=dem1_local_fallback,
    )


OFFLINE_DOWNLOAD: DownloadFn = no_download
"""Convenience alias for a job that must not touch the network."""
