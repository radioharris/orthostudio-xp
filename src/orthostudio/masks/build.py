# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Building the masks of one tile: one cell per task, a process pool, an indexed directory.

Spec: ``docs/specs/masks-build.md`` sections 1, 3 and 4. Origin: ``O4_Mask_Utils.py:64-221``
(``build_masks``), whose four GIL-bound threads are replaced by a pool of processes.

The artefact this writes is the directory ``orthostudio.textures.imprint.masks_dir_lookup`` and
``orthostudio.pipeline.build._distance_lookup`` already read: ``<til_y>_<til_x>.png``, optionally
``<til_y>_<til_x>_dist.png``, plus an ``index.json``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import Executor, ProcessPoolExecutor
from dataclasses import dataclass, field
from multiprocessing import get_all_start_methods, get_context
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from orthostudio.errors import OsxpError
from orthostudio.masks.distance import distance_mask
from orthostudio.masks.profiles import MasksWidth, blur_mask, halo_px, sea_level_for
from orthostudio.masks.raster import (
    CELL_PX,
    FULL_MARGIN_PX,
    custom_pre_mask,
    extent_polygons,
    pre_mask,
)
from orthostudio.masks.water import (
    MaskRange,
    MeshWaterTris,
    WaterTriangles,
    mask_cells,
    water_triangles,
)
from orthostudio.mesh.mesh_file import MeshData
from orthostudio.model import TileRef

__all__ = [
    "DEFAULT_START_METHOD",
    "MASKS_INDEX_FORMAT",
    "MAX_WORKERS",
    "START_METHOD_ENV",
    "WORKERS_ENV",
    "CellJob",
    "CellResult",
    "build_cell",
    "build_masks",
    "env_workers",
    "mask_file_name",
    "start_method",
    "worker_count",
]

MASKS_INDEX_FORMAT = "osxp-masks-1"
WORKERS_ENV = "OSXP_MASKS_WORKERS"
MAX_WORKERS = 8
"""Default ceiling on the pool: the measured gain stops there (``masks-build.md`` 6)."""
START_METHOD_ENV = "OSXP_MASKS_START_METHOD"
DEFAULT_START_METHOD = "spawn"
"""``spawn`` is the safe default on macOS (Ortho4XP itself uses processes here).

Measured on the reference tile, 8 workers: ``fork`` 0.36 s, ``forkserver`` 3.45 s the first
time then 0.36 s, ``spawn`` about 0.8 s every time, a thread pool 0.78 s (``numpy.convolve``
holds the GIL). A caller that builds several tiles should pass its own ``executor`` and pay
the start-up once.
"""


def mask_file_name(til_x: int, til_y: int, *, distance: bool = False) -> str:
    """``<til_y>_<til_x>.png`` / ``<til_y>_<til_x>_dist.png`` (``O4_File_Names.py:334-344``)."""
    return f"{til_y}_{til_x}_dist.png" if distance else f"{til_y}_{til_x}.png"


def env_workers() -> int | None:
    """``$OSXP_MASKS_WORKERS`` as an explicit process count, or ``None``.

    The **only** parser of that variable (review 4, finding M2: three copies disagreed on
    ``0`` and on negative values). A value that is not a positive integer -- ``0``, ``-4``,
    ``x`` -- is treated as *unset*, so the pipeline's RAM budget and the pool the rule starts
    can never be computed from two different numbers (``masks-build.md`` 6).
    """
    raw = os.environ.get(WORKERS_ENV)
    if raw is None or not raw.strip().lstrip("+").isdigit():
        return None
    value = int(raw)
    return value if value > 0 else None


def worker_count(cells: int, workers: int | None = None) -> int:
    """How many processes to use: the argument, then ``$OSXP_MASKS_WORKERS``, then the default.

    The default is ``min(MAX_WORKERS, os.cpu_count())`` and never the bare core count: the
    caller that budgets the RAM of the node (``pipeline.build._masks_ram_mb``) pays
    ``0.8 GB`` per process, and an unbounded pool made that budget meaningless (review 4,
    finding M1). An explicit argument is honoured as given -- it is the pipeline's own count.
    """
    if workers is None:
        workers = env_workers()
    if workers is None or workers <= 0:
        workers = min(MAX_WORKERS, os.cpu_count() or 4)
    return max(1, min(workers, max(1, cells)))


def _check_cancelled(cancel: threading.Event | None, tile: TileRef) -> None:
    """Raise instead of returning a half-built directory (review 4, findings C1/C2).

    ``build_masks`` used to ``break`` out of its loops and still write ``index.json``: the
    caller (``run_p0_rule``) then committed a truncated mask directory under the key of the
    complete one, and every later build took it as a hit. Raising makes the store abort its
    staging, so a cancelled run leaves nothing behind.
    """
    if cancel is not None and cancel.is_set():
        raise OsxpError("SYS_CANCELLED", context={"tile": tile.name, "stage": "masks"})


def start_method() -> str:
    """The multiprocessing start method of the pool (``$OSXP_MASKS_START_METHOD``).

    Validated against what the platform offers: ``fork`` on Windows used to reach
    ``get_context`` and raise a bare ``ValueError`` with no error code (review 4, minor).
    """
    name = os.environ.get(START_METHOD_ENV) or DEFAULT_START_METHOD
    available = get_all_start_methods()
    if name not in available:
        raise OsxpError(
            "CFG_VALUE_INVALID",
            context={
                "name": START_METHOD_ENV,
                "value": name,
                "type": "str",
                "range": ", ".join(available),
            },
            message=f"{START_METHOD_ENV}={name!r} is not available on this platform.",
            remedy=f"Use one of {', '.join(available)}, or clear the variable.",
        )
    return name


@dataclass(frozen=True, slots=True)
class CellJob:
    """Everything one cell needs; picklable, so it crosses to a worker process as is."""

    til_x: int
    til_y: int
    tris: WaterTriangles
    extents: Sequence[Sequence[tuple[int, int]]]
    mask_zl: int
    masking_mode: str
    masks_width: MasksWidth
    sea_level: int
    margin: int
    distance_masks_too: bool = False
    dem: NDArray[np.uint8] | None = None
    custom_extent: NDArray[np.uint8] | None = None
    lat: int = 0


@dataclass(frozen=True, slots=True)
class CellResult:
    """What one cell produced: the two images (or ``None``) and the time it took."""

    til_x: int
    til_y: int
    mask: NDArray[np.uint8] | None
    dist: NDArray[np.uint8] | None
    seconds: float
    raster_seconds: float = 0.0
    blur_seconds: float = 0.0

    @property
    def written(self) -> bool:
        return self.mask is not None


def build_cell(job: CellJob) -> CellResult:
    """One cell of the ``mask_zl`` grid (``O4_Mask_Utils.py:132-199``, the inner ``build_mask``).

    Returns ``mask=None`` when Ortho4XP would not write the file: an empty pre-mask, or a blurred
    mask that is uniformly black or uniformly white.
    """
    t0 = time.perf_counter()
    pre = pre_mask(
        job.til_x,
        job.til_y,
        job.tris,
        job.extents,
        mask_zl=job.mask_zl,
        sea_level=job.sea_level,
        margin=job.margin,
    )
    if job.dem is not None:
        pre = np.maximum(pre, job.dem)
    t_raster = time.perf_counter() - t0
    custom = (
        custom_pre_mask(job.custom_extent, job.sea_level) if job.custom_extent is not None else None
    )
    if pre.max() == 0 and (custom is None or custom.max() == 0):
        return CellResult(job.til_x, job.til_y, None, None, time.perf_counter() - t0, t_raster)
    t1 = time.perf_counter()
    blurred = blur_mask(
        pre,
        masking_mode=job.masking_mode,
        masks_width=job.masks_width,
        lat=job.lat,
        mask_zl=job.mask_zl,
        sea_level=job.sea_level,
    )
    t_blur = time.perf_counter() - t1
    margin = job.margin
    blurred = np.maximum((pre > 0).astype(np.uint8) * 255, blurred)[
        margin : margin + CELL_PX, margin : margin + CELL_PX
    ]
    if custom is not None:
        blurred = np.maximum(blurred, custom)
    if blurred.max() == 0 or blurred.min() == 255:
        return CellResult(
            job.til_x, job.til_y, None, None, time.perf_counter() - t0, t_raster, t_blur
        )
    dist = (
        distance_mask(pre, mask_zl=job.mask_zl, margin=margin) if job.distance_masks_too else None
    )
    return CellResult(
        job.til_x, job.til_y, blurred, dist, time.perf_counter() - t0, t_raster, t_blur
    )


def _write(out_dir: Path, result: CellResult) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "til_x": result.til_x,
        "til_y": result.til_y,
        "file": None,
        "dist": None,
    }
    if result.mask is not None:
        name = mask_file_name(result.til_x, result.til_y)
        Image.fromarray(result.mask).save(out_dir / name)
        entry["file"] = name
    if result.dist is not None:
        name = mask_file_name(result.til_x, result.til_y, distance=True)
        Image.fromarray(result.dist).save(out_dir / name)
        entry["dist"] = name
    return entry


CellReport = tuple[dict[str, Any], float, float, float]


def _run_and_write(out_dir_and_job: tuple[str, CellJob]) -> CellReport:
    """Worker entry point: build one cell and write its PNGs (never ship 16 MB back)."""
    out_dir, job = out_dir_and_job
    result = build_cell(job)
    entry = _write(Path(out_dir), result)
    return entry, result.seconds, result.raster_seconds, result.blur_seconds


@dataclass
class MasksIndex:
    """The ``index.json`` of a masks artefact, and the timings of the run."""

    tile: TileRef
    mask_zl: int
    range: MaskRange
    masks: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    cell_seconds: list[float] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "format": MASKS_INDEX_FORMAT,
            "tile": self.tile.name,
            "mask_zl": self.mask_zl,
            "range": {
                "til_x_min": self.range.til_x_min,
                "til_y_min": self.range.til_y_min,
                "til_x_max": self.range.til_x_max,
                "til_y_max": self.range.til_y_max,
            },
            "masks": self.masks,
        }


def build_masks(
    out_dir: Path,
    meshes: Mapping[TileRef, MeshData],
    tile: TileRef,
    *,
    mask_zl: int = 14,
    masks_width: MasksWidth = 100,
    masking_mode: str = "sand",
    use_masks_for_inland: bool = False,
    ratio_water: float = 0.25,
    distance_masks_too: bool = False,
    water_tris: Mapping[TileRef, MeshWaterTris] | None = None,
    dem_cells: Mapping[tuple[int, int], NDArray[np.uint8]] | None = None,
    custom_cells: Mapping[tuple[int, int], NDArray[np.uint8]] | None = None,
    workers: int | None = None,
    cancel: threading.Event | None = None,
    executor: Executor | None = None,
) -> MasksIndex:
    """Build every mask of a tile into ``out_dir`` and return its index.

    ``meshes`` maps the tile (and any of its 8 neighbours that exist) to its mesh; a missing
    neighbour is simply absent from the mapping, which is Ortho4XP's "no sea beyond that border".
    ``water_tris`` is the optional ``water_tris.npz`` of the same artefacts
    (``docs/specs/mesh-build.md`` section 7), which saves searching the mesh again.
    """
    t0 = time.perf_counter()
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = mask_cells(tile, mask_zl)
    sea_level = sea_level_for(ratio_water)
    margin = halo_px(
        masking_mode,
        masks_width,
        tile.lat,
        mask_zl,
        distance_masks_too=distance_masks_too,
        full_margin=FULL_MARGIN_PX,
    )
    by_cell = water_triangles(
        meshes,
        tile,
        mask_zl,
        use_masks_for_inland=use_masks_for_inland,
        water_tris=water_tris,
    )
    extents = extent_polygons(sorted(meshes, key=lambda t: (t.lat, t.lon)), mask_zl)
    jobs = [
        CellJob(
            til_x=til_x,
            til_y=til_y,
            tris=tris,
            extents=extents,
            mask_zl=mask_zl,
            masking_mode=masking_mode,
            masks_width=masks_width,
            sea_level=sea_level,
            margin=margin,
            distance_masks_too=distance_masks_too,
            dem=None if dem_cells is None else dem_cells.get((til_x, til_y)),
            custom_extent=None if custom_cells is None else custom_cells.get((til_x, til_y)),
            lat=tile.lat,
        )
        for (til_x, til_y), tris in sorted(by_cell.items(), key=lambda kv: (kv[0][1], kv[0][0]))
        if rng.contains(til_x, til_y)
    ]
    index = MasksIndex(tile=tile, mask_zl=mask_zl, range=rng)
    entries: list[dict[str, Any]] = []
    n_workers = worker_count(len(jobs), workers)
    payloads = [(str(out_dir), job) for job in jobs]
    if executor is not None:
        for entry, seconds, _, _ in executor.map(_run_and_write, payloads):
            _check_cancelled(cancel, tile)
            entries.append(entry)
            index.cell_seconds.append(seconds)
    elif n_workers == 1 or len(jobs) <= 1:
        for payload in payloads:
            _check_cancelled(cancel, tile)
            entry, seconds, _, _ = _run_and_write(payload)
            entries.append(entry)
            index.cell_seconds.append(seconds)
    else:
        context = get_context(start_method())
        with ProcessPoolExecutor(max_workers=n_workers, mp_context=context) as pool:
            futures = [pool.submit(_run_and_write, payload) for payload in payloads]
            try:
                for future in futures:
                    _check_cancelled(cancel, tile)
                    entry, seconds, _, _ = future.result()
                    entries.append(entry)
                    index.cell_seconds.append(seconds)
            except BaseException:
                # Stop the cells that have not started yet; `map` could not (review 4, C2).
                pool.shutdown(wait=False, cancel_futures=True)
                raise
    index.masks = [e for e in entries if e["file"] is not None]
    index.seconds = time.perf_counter() - t0
    # Last: a cancelled or failed run must leave no index.json behind, so that the artefact
    # the store would commit can never look complete (review 4, finding C2).
    (out_dir / "index.json").write_text(json.dumps(index.to_json(), indent=1) + "\n", "utf-8")
    return index
